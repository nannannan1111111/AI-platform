"""API 来源凭据的只写存储 seam。"""

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Protocol
from uuid import uuid4

from app.model_routing.models import InvalidProviderConfiguration


class ProviderSecretConfigurationError(RuntimeError):
    """生产 Provider 密钥目录缺失或不能安全使用。"""


@dataclass(frozen=True, slots=True)
class StoredProviderSecret:
    """可安全持久化的密钥引用与识别指纹。"""

    secret_ref: str
    key_fingerprint: str


class ProviderSecrets(Protocol):
    """把明文凭据隔离在数据库和公开响应之外。"""

    def store(self, provider_id: str, api_key: str) -> StoredProviderSecret:
        """保存或轮换凭据并返回不含明文的引用。"""

    def read(self, secret_ref: str) -> str:
        """仅供上游调用 Adapter 在内存中取得凭据。"""

    def delete(self, secret_ref: str) -> None:
        """幂等删除一个不再使用的来源凭据。"""


class InMemoryProviderSecrets:
    """仅用于自动化和本地开发的进程内密钥 Adapter。"""

    def __init__(self) -> None:
        """创建空的进程内密钥存储。"""
        self._values: dict[str, str] = {}
        self._lock = RLock()

    def store(self, provider_id: str, api_key: str) -> StoredProviderSecret:
        """保存测试凭据，公开对象只返回随机引用和短指纹。"""
        value = api_key.strip()
        if not value:
            raise InvalidProviderConfiguration("API Key 不能为空")
        secret_ref = f"memory://provider/{provider_id}/{uuid4()}"
        with self._lock:
            self._values[secret_ref] = value
        return StoredProviderSecret(
            secret_ref=secret_ref,
            key_fingerprint=sha256(value.encode()).hexdigest()[:8],
        )

    def read(self, secret_ref: str) -> str:
        """读取只在当前进程存在的测试凭据。"""
        with self._lock:
            return self._values[secret_ref]

    def delete(self, secret_ref: str) -> None:
        """幂等删除测试凭据。"""
        with self._lock:
            self._values.pop(secret_ref, None)


class FileSystemProviderSecrets:
    """在受控服务器目录中原子保存 API 来源凭据。"""

    _REFERENCE_PREFIX = "provider-file://"

    def __init__(self, root: str | os.PathLike[str]) -> None:
        """使用不进入公开引用的服务器绝对目录。"""
        self._root = Path(root).resolve()

    def store(self, provider_id: str, api_key: str) -> StoredProviderSecret:
        """写入不以 Provider 身份命名的新密钥文件并返回不透明引用。"""
        value = api_key.strip()
        if not provider_id.strip() or not value:
            raise InvalidProviderConfiguration("Provider ID 和 API Key 不能为空")
        secret_id = sha256(provider_id.strip().encode()).hexdigest()
        destination = self._root / f"{secret_id}.secret"
        descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-provider-", dir=self._root)
        try:
            os.chmod(temporary_name, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(value.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
            os.chmod(destination, 0o600)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise
        return StoredProviderSecret(
            secret_ref=f"{self._REFERENCE_PREFIX}{secret_id}",
            key_fingerprint=sha256(value.encode()).hexdigest()[:8],
        )

    def read(self, secret_ref: str) -> str:
        """按不透明引用读取凭据，拒绝路径或其他 Adapter 的引用。"""
        if not secret_ref.startswith(self._REFERENCE_PREFIX):
            raise KeyError("provider secret is unavailable")
        secret_id = secret_ref.removeprefix(self._REFERENCE_PREFIX)
        if len(secret_id) != 64 or any(character not in "0123456789abcdef" for character in secret_id):
            raise KeyError("provider secret is unavailable")
        try:
            return (self._root / f"{secret_id}.secret").read_text(encoding="utf-8")
        except OSError as exc:
            raise KeyError("provider secret is unavailable") from exc

    def delete(self, secret_ref: str) -> None:
        """按不透明引用幂等删除密钥文件。"""
        if not secret_ref.startswith(self._REFERENCE_PREFIX):
            raise KeyError("provider secret is unavailable")
        secret_id = secret_ref.removeprefix(self._REFERENCE_PREFIX)
        if len(secret_id) != 64 or any(character not in "0123456789abcdef" for character in secret_id):
            raise KeyError("provider secret is unavailable")
        (self._root / f"{secret_id}.secret").unlink(missing_ok=True)


def configured_file_system_provider_secrets(
    environ: Mapping[str, str] | None = None,
) -> FileSystemProviderSecrets:
    """从生产进程环境装配文件系统 Provider 密钥 Adapter。"""
    environment = os.environ if environ is None else environ
    configured_root = environment.get("PROVIDER_SECRETS_ROOT", "").strip()
    if not configured_root:
        raise ProviderSecretConfigurationError("PROVIDER_SECRETS_ROOT 必须配置")
    root = Path(configured_root)
    if not root.is_absolute():
        raise ProviderSecretConfigurationError("PROVIDER_SECRETS_ROOT 必须是绝对路径")
    if not root.is_dir():
        raise ProviderSecretConfigurationError("PROVIDER_SECRETS_ROOT 必须指向已存在的目录")
    try:
        os.chmod(root, 0o700)
        with tempfile.NamedTemporaryFile(prefix=".provider-secret-access-probe-", dir=root) as probe:
            os.chmod(probe.name, 0o600)
            probe.write(b"provider-secret-access-probe")
            probe.flush()
            probe.seek(0)
            if probe.read() != b"provider-secret-access-probe":
                raise OSError("provider secret access probe could not be read")
    except OSError as exc:
        raise ProviderSecretConfigurationError("PROVIDER_SECRETS_ROOT 必须允许应用进程安全读写和删除密钥文件") from exc
    return FileSystemProviderSecrets(root)

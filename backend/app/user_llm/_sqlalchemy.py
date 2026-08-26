"""Persistent account-owned LLM providers and OpenAI-compatible completion calls."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.model_routing import ProviderSecrets
from app.user_llm.models import (
    InvalidUserLLMProvider,
    UserLLMCompletion,
    UserLLMProvider,
    UserLLMProviderNotFound,
    UserLLMProviderSave,
    UserLLMUpstreamError,
)
from app.user_llm.tables import user_llm_providers


def _clean(command: UserLLMProviderSave) -> tuple[str, str, str, tuple[str, ...]]:
    code = re.sub(r"[^a-z0-9_-]+", "-", command.code.strip().lower()).strip("-")
    name = command.display_name.strip()
    base_url = command.base_url.strip().rstrip("/")
    parsed = urlparse(base_url)
    models = tuple(dict.fromkeys(value.strip() for value in command.models if value.strip()))
    if not code or len(code) > 64 or not name or len(name) > 120:
        raise InvalidUserLLMProvider("LLM 配置名称或代码无效")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or len(base_url) > 500:
        raise InvalidUserLLMProvider("LLM API 基础地址无效")
    if not models or len(models) > 100 or any(len(value) > 200 for value in models):
        raise InvalidUserLLMProvider("请至少配置一个有效的文本模型")
    return code, name, base_url, models


def _completion_text(payload: object) -> str | None:
    """Extract text from the common OpenAI-compatible completion shapes."""
    if not isinstance(payload, dict):
        return None
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            if parts:
                return "".join(parts)
        for key in ("output_text", "reasoning_content"):
            value = message.get(key)
            if isinstance(value, str):
                return value
    text = first.get("text")
    return text if isinstance(text, str) else None


class SqlAlchemyUserLLMProviders:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        secrets: ProviderSecrets,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._sessions = session_factory
        self._secrets = secrets
        self._transport = transport
        self._timeout = timeout

    @staticmethod
    def _public(row: RowMapping | dict[str, object]) -> UserLLMProvider:
        item = dict(row)
        return UserLLMProvider(
            id=str(item["id"]), account_space_id=str(item["account_space_id"]), code=str(item["code"]),
            display_name=str(item["display_name"]), base_url=str(item["base_url"]),
            models=tuple(json.loads(str(item["models_json"]))), enabled=bool(item["enabled"]),
            has_key=bool(item["secret_ref"]), key_fingerprint=str(item["key_fingerprint"]),
            created_at=item["created_at"], updated_at=item["updated_at"],
        )

    def list(self, account_space_id: str) -> tuple[UserLLMProvider, ...]:
        with self._sessions() as db:
            rows = db.execute(select(user_llm_providers).where(
                user_llm_providers.c.account_space_id == account_space_id
            ).order_by(user_llm_providers.c.created_at, user_llm_providers.c.display_name)).mappings()
            return tuple(self._public(row) for row in rows)

    def create(self, command: UserLLMProviderSave) -> UserLLMProvider:
        code, name, base_url, models = _clean(command)
        if not command.api_key.strip():
            raise InvalidUserLLMProvider("API Key 不能为空")
        provider_id, now = str(uuid4()), datetime.now(UTC)
        stored = self._secrets.store(f"user-llm:{command.account_space_id}:{provider_id}", command.api_key)
        values = dict(id=provider_id, account_space_id=command.account_space_id, code=code,
                      display_name=name, base_url=base_url, models_json=json.dumps(models, ensure_ascii=False),
                      enabled=command.enabled, secret_ref=stored.secret_ref,
                      key_fingerprint=stored.key_fingerprint, created_at=now, updated_at=now)
        try:
            with self._sessions.begin() as db:
                db.execute(insert(user_llm_providers).values(**values))
        except IntegrityError as exc:
            self._secrets.delete(stored.secret_ref)
            raise InvalidUserLLMProvider("该 LLM Provider 代码已存在") from exc
        return self._public(values)

    def update(self, provider_id: str, command: UserLLMProviderSave) -> UserLLMProvider:
        code, name, base_url, models = _clean(command)
        with self._sessions() as db:
            old = db.execute(select(user_llm_providers).where(
                user_llm_providers.c.id == provider_id,
                user_llm_providers.c.account_space_id == command.account_space_id,
            )).mappings().first()
        if old is None:
            raise UserLLMProviderNotFound(provider_id)
        stored = None
        if command.api_key.strip():
            stored = self._secrets.store(f"user-llm:{command.account_space_id}:{provider_id}", command.api_key)
        values = dict(code=code, display_name=name, base_url=base_url,
                      models_json=json.dumps(models, ensure_ascii=False), enabled=command.enabled,
                      updated_at=datetime.now(UTC))
        if stored:
            values.update(secret_ref=stored.secret_ref, key_fingerprint=stored.key_fingerprint)
        try:
            with self._sessions.begin() as db:
                db.execute(update(user_llm_providers).where(
                    user_llm_providers.c.id == provider_id,
                    user_llm_providers.c.account_space_id == command.account_space_id,
                ).values(**values))
        except IntegrityError as exc:
            raise InvalidUserLLMProvider("该 LLM Provider 代码已存在") from exc
        return next(item for item in self.list(command.account_space_id) if item.id == provider_id)

    def delete(self, account_space_id: str, provider_id: str) -> None:
        with self._sessions.begin() as db:
            row = db.execute(select(user_llm_providers.c.secret_ref).where(
                user_llm_providers.c.id == provider_id,
                user_llm_providers.c.account_space_id == account_space_id,
            )).first()
            if row is None:
                raise UserLLMProviderNotFound(provider_id)
            db.execute(delete(user_llm_providers).where(
                user_llm_providers.c.id == provider_id,
                user_llm_providers.c.account_space_id == account_space_id,
            ))
        self._secrets.delete(str(row.secret_ref))

    def complete(self, command: UserLLMCompletion) -> str:
        with self._sessions() as db:
            row = db.execute(select(user_llm_providers).where(
                user_llm_providers.c.account_space_id == command.account_space_id,
                user_llm_providers.c.code == command.provider_code,
                user_llm_providers.c.enabled.is_(True),
            )).mappings().first()
        if row is None:
            raise UserLLMProviderNotFound(command.provider_code)
        models = tuple(json.loads(str(row["models_json"])))
        model = command.model.strip() or models[0]
        if model not in models:
            raise InvalidUserLLMProvider("所选模型不在当前用户的 LLM 配置中")
        messages = [dict(item) for item in command.messages if isinstance(item, dict)]
        if command.system_prompt.strip():
            messages.insert(0, {"role": "system", "content": command.system_prompt.strip()})
        content: object = command.message
        if command.images:
            content = [{"type": "text", "text": command.message}, *(
                {"type": "image_url", "image_url": {"url": url}} for url in command.images
            )]
        if command.videos:
            suffix = "\n\n视频参考：" + "\n".join(command.videos)
            if isinstance(content, str):
                content += suffix
            else:
                content[0]["text"] += suffix  # type: ignore[index]
        messages.append({"role": "user", "content": content})
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.post(
                    f'{str(row["base_url"]).rstrip("/")}/chat/completions',
                    headers={"Authorization": f'Bearer {self._secrets.read(str(row["secret_ref"]))}'},
                    json={"model": model, "messages": messages},
                )
                response.raise_for_status()
                text = _completion_text(response.json())
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise UserLLMUpstreamError("LLM 请求失败，请检查 API 地址、密钥和模型配置") from exc
        if not isinstance(text, str):
            raise UserLLMUpstreamError("LLM 返回内容格式无效")
        return text

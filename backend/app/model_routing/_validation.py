"""API 来源与模型路由共享校验。"""

from urllib.parse import urlsplit, urlunsplit

from app.model_routing.models import InvalidModelRoute, InvalidProviderConfiguration


def required(value: str, label: str) -> str:
    """规范化必填文本。"""
    normalized = value.strip()
    if not normalized:
        raise InvalidProviderConfiguration(f"{label}不能为空")
    return normalized


def valid_reference_image_limit(value: int) -> int:
    """Validate the model-level reference image upload limit."""
    if isinstance(value, bool) or not 0 <= value <= 16:
        raise InvalidModelRoute("可上传参考图张数必须是 0–16 的整数")
    return value


def normalized_base_url(value: str) -> str:
    """规范化不包含凭据、查询参数和片段的 HTTPS API 基础地址。"""
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise InvalidProviderConfiguration("API 地址必须是无内嵌凭据的 HTTPS 地址")
    if parsed.query or parsed.fragment:
        raise InvalidProviderConfiguration("API 地址不能包含查询参数或片段")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

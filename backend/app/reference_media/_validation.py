"""临时参考媒体的共享内容校验。"""

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256

from app.reference_media.models import (
    InvalidReferenceMedia,
    ReferenceMediaRecord,
    ReferenceMediaState,
    ReferenceMediaUpload,
)

_MAX_REFERENCE_IMAGE_BYTES = 50 * 1024 * 1024


def validated_reference_media(
    upload: ReferenceMediaUpload,
    *,
    media_id: str,
    object_key: str,
) -> ReferenceMediaRecord:
    """从真实文件签名建立不信任浏览器 MIME 的参考图片记录。"""
    content = bytes(upload.content)
    if not upload.user_id.strip() or not upload.account_space_id.strip() or not media_id.strip():
        raise InvalidReferenceMedia("参考图片归属无效")
    if not content:
        raise InvalidReferenceMedia("参考图片不能为空")
    if len(content) > _MAX_REFERENCE_IMAGE_BYTES:
        raise InvalidReferenceMedia("参考图片不能超过 50MB")
    mime_type = _image_mime_type(content)
    original_name = upload.original_name.replace("\\", "/").rsplit("/", 1)[-1].strip()[:255]
    return ReferenceMediaRecord(
        media_id=media_id,
        user_id=upload.user_id,
        account_space_id=upload.account_space_id,
        original_name=original_name or "reference-image",
        object_key=object_key,
        mime_type=mime_type,
        size_bytes=len(content),
        content_hash=sha256(content).hexdigest(),
        state=ReferenceMediaState.TEMPORARY,
        origin=upload.origin,
        created_at=upload.created_at,
        expires_at=upload.created_at + timedelta(hours=24),
    )


def expired(media: ReferenceMediaRecord) -> ReferenceMediaRecord:
    """保留归属和审计元数据，仅推进到失效状态。"""
    return replace(media, state=ReferenceMediaState.EXPIRED)


def _image_mime_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise InvalidReferenceMedia("参考图片格式无效，仅支持 PNG、JPEG 或 WEBP")

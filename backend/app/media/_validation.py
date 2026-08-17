"""GeneratedMedia Adapter 共享的领域校验与记录构造。"""

from datetime import timedelta

from app.generation.models import GenerationTask, GenerationTaskStatus
from app.media.models import (
    CanvasMediaUpload,
    GeneratedMediaKind,
    GeneratedMediaRecord,
    GeneratedMediaRegistration,
    GeneratedMediaState,
    InvalidGeneratedMedia,
)

_TEMPORARY_TTL = timedelta(hours=24)
_MAX_CANVAS_IMAGE_BYTES = 50 * 1024 * 1024


def validated_media(registration: GeneratedMediaRegistration, media_id: str) -> GeneratedMediaRecord:
    """校验 Worker 元数据并构造固定 24 小时的临时媒体记录。"""
    try:
        kind = GeneratedMediaKind(registration.kind)
    except ValueError as exc:
        raise InvalidGeneratedMedia("生成媒体类型无效") from exc
    if not registration.result_reference.strip() or not registration.object_key.strip():
        raise InvalidGeneratedMedia("生成媒体引用不能为空")
    if not registration.mime_type.strip() or registration.size_bytes <= 0:
        raise InvalidGeneratedMedia("生成媒体描述无效")
    content_hash = registration.content_hash.lower()
    if len(content_hash) != 64 or any(character not in "0123456789abcdef" for character in content_hash):
        raise InvalidGeneratedMedia("生成媒体内容哈希无效")
    return GeneratedMediaRecord(
        media_id=media_id,
        user_id=registration.user_id,
        account_space_id=registration.account_space_id,
        canvas_id=registration.canvas_id,
        task_id=registration.task_id,
        result_reference=registration.result_reference,
        object_key=registration.object_key,
        kind=kind,
        mime_type=registration.mime_type,
        size_bytes=registration.size_bytes,
        content_hash=content_hash,
        state=GeneratedMediaState.TEMPORARY,
        created_at=registration.created_at,
        expires_at=registration.created_at + _TEMPORARY_TTL,
    )


def validated_canvas_image_mime(upload: CanvasMediaUpload) -> str:
    """Return the canonical MIME only for bounded PNG, JPEG, or WebP bytes."""
    content = bytes(upload.content)
    if not upload.user_id or not upload.account_space_id or not upload.canvas_id:
        raise InvalidGeneratedMedia("画布媒体归属无效")
    if not content or len(content) > _MAX_CANVAS_IMAGE_BYTES:
        raise InvalidGeneratedMedia("画布图片大小无效")
    declared = upload.declared_mime_type.lower().split(";", 1)[0].strip()
    if declared == "image/jpg":
        declared = "image/jpeg"
    detected = ""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        detected = "image/webp"
    if declared not in {"image/png", "image/jpeg", "image/webp"} or detected != declared:
        raise InvalidGeneratedMedia("画布只支持内容与声明一致的 PNG、JPEG、WebP 图片")
    return detected


def validate_running_task(task: GenerationTask, registration: GeneratedMediaRegistration) -> None:
    """确认媒体声明与运行中的归属任务一致。"""
    if (
        task.status is not GenerationTaskStatus.RUNNING
        or task.user_id != registration.user_id
        or task.canvas_id != registration.canvas_id
    ):
        raise InvalidGeneratedMedia("生成媒体必须属于运行中的任务")


def matches_registration(media: GeneratedMediaRecord, registration: GeneratedMediaRegistration) -> bool:
    """判断已有不可变记录是否与幂等重放完全一致。"""
    object_key_matches = media.object_key == registration.object_key or (
        media.state
        in {
            GeneratedMediaState.PERSISTENT,
            GeneratedMediaState.RELEASED,
            GeneratedMediaState.DELETED,
        }
        and media.object_key == f"persistent/{registration.account_space_id}/{registration.content_hash.lower()}"
    )
    return (
        media.user_id == registration.user_id
        and media.canvas_id == registration.canvas_id
        and object_key_matches
        and media.kind.value == registration.kind
        and media.mime_type == registration.mime_type
        and media.size_bytes == registration.size_bytes
        and media.content_hash == registration.content_hash.lower()
        and media.created_at == registration.created_at
    )

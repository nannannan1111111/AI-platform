"""账户空间归属的临时生成媒体 Module。"""

from app.media._memory import InMemoryGeneratedMedia
from app.media._sqlalchemy import SqlAlchemyGeneratedMedia
from app.media._sqlalchemy_allowances import SqlAlchemyStorageAllowances
from app.media.allowances import (
    MAX_STORAGE_ALLOWANCE_BYTES,
    AccountStorageAllowancePolicy,
    InMemoryStorageAllowances,
    StorageAllowancePolicy,
    StorageAllowances,
)
from app.media.configuration import (
    MediaStorageConfigurationError,
    configured_file_system_media_objects,
)
from app.media.interface import GeneratedMedia
from app.media.models import (
    CanvasMediaUpload,
    GeneratedMediaConflict,
    GeneratedMediaKind,
    GeneratedMediaNotDeletable,
    GeneratedMediaNotFound,
    GeneratedMediaNotRetainable,
    GeneratedMediaRecord,
    GeneratedMediaRegistration,
    GeneratedMediaState,
    InvalidGeneratedMedia,
    MediaExpirationReport,
    MediaReferenceReconciliation,
    StorageAllowance,
    StorageAllowanceExceeded,
)
from app.media.objects import (
    FileSystemMediaObjects,
    InMemoryMediaObjects,
    MediaContentStore,
    MediaObjectConflict,
    MediaObjectDeletionFailed,
    MediaObjectPromotionFailed,
    MediaObjects,
    S3CompatibleMediaObjects,
    StoredMediaObject,
)

__all__ = [
    "GeneratedMedia",
    "CanvasMediaUpload",
    "GeneratedMediaConflict",
    "GeneratedMediaKind",
    "GeneratedMediaNotRetainable",
    "GeneratedMediaNotDeletable",
    "GeneratedMediaNotFound",
    "GeneratedMediaRecord",
    "GeneratedMediaRegistration",
    "GeneratedMediaState",
    "FileSystemMediaObjects",
    "InMemoryGeneratedMedia",
    "InMemoryMediaObjects",
    "InMemoryStorageAllowances",
    "InvalidGeneratedMedia",
    "MediaExpirationReport",
    "MediaReferenceReconciliation",
    "MediaStorageConfigurationError",
    "MediaObjectConflict",
    "MediaContentStore",
    "MediaObjectDeletionFailed",
    "MediaObjectPromotionFailed",
    "MediaObjects",
    "MAX_STORAGE_ALLOWANCE_BYTES",
    "AccountStorageAllowancePolicy",
    "S3CompatibleMediaObjects",
    "SqlAlchemyGeneratedMedia",
    "SqlAlchemyStorageAllowances",
    "StorageAllowance",
    "StorageAllowanceExceeded",
    "StorageAllowancePolicy",
    "StorageAllowances",
    "StoredMediaObject",
    "configured_file_system_media_objects",
]

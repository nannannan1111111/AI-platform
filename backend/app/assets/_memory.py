"""PersonalAssets Interface 的内存 Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from threading import RLock
from uuid import uuid4

from app.assets._validation import matches_replay, validated_rename, validated_save
from app.assets.models import (
    PersonalAsset,
    PersonalAssetConflict,
    PersonalAssetNotFound,
    PersonalAssetRename,
    PersonalAssetSave,
)
from app.media import GeneratedMedia


class InMemoryPersonalAssets:
    """在单进程内保存账户空间拥有的个人资产。"""

    def __init__(
        self,
        generated_media: GeneratedMedia,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._generated_media = generated_media
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._assets_by_id: dict[str, PersonalAsset] = {}
        self._state_by_id: dict[str, str] = {}
        self._command_by_key: dict[tuple[str, str], PersonalAssetSave] = {}
        self._asset_id_by_key: dict[tuple[str, str], str] = {}
        self._lock = RLock()

    def save_generated_media(self, command: PersonalAssetSave) -> PersonalAsset:
        """把生成媒体幂等保存为个人资产。"""
        normalized = validated_save(command)
        key = (normalized.account_space_id, normalized.idempotency_key)
        with self._lock:
            existing_id = self._asset_id_by_key.get(key)
            if existing_id is not None:
                if self._state_by_id[existing_id] != "active":
                    raise PersonalAssetConflict(normalized.idempotency_key)
                if matches_replay(self._command_by_key[key], normalized, include_display_name=False):
                    return self._assets_by_id[existing_id]
                raise PersonalAssetConflict(normalized.idempotency_key)
            asset_id = self._id_factory()
            media = self._generated_media.retain_to_personal_asset(
                normalized.account_space_id,
                normalized.media_id,
                asset_id,
                normalized.saved_at,
            )
            asset = PersonalAsset(
                asset_id=asset_id,
                user_id=normalized.user_id,
                account_space_id=normalized.account_space_id,
                media_id=media.media_id,
                display_name=normalized.display_name,
                kind=media.kind,
                mime_type=media.mime_type,
                size_bytes=media.size_bytes,
                created_at=normalized.saved_at,
            )
            self._assets_by_id[asset.asset_id] = asset
            self._state_by_id[asset.asset_id] = "active"
            self._command_by_key[key] = normalized
            self._asset_id_by_key[key] = asset.asset_id
            return asset

    def list(self, account_space_id: str) -> tuple[PersonalAsset, ...]:
        """读取账户空间拥有的个人资产。"""
        with self._lock:
            return tuple(
                sorted(
                    (
                        asset
                        for asset in self._assets_by_id.values()
                        if asset.account_space_id == account_space_id and self._state_by_id[asset.asset_id] == "active"
                    ),
                    key=lambda asset: (asset.created_at, asset.asset_id),
                )
            )

    def rename(self, command: PersonalAssetRename) -> PersonalAsset:
        """修改账户空间中个人资产的显示名称。"""
        normalized = validated_rename(command)
        with self._lock:
            existing = self._assets_by_id.get(normalized.asset_id)
            if (
                existing is None
                or existing.account_space_id != normalized.account_space_id
                or self._state_by_id[existing.asset_id] != "active"
            ):
                raise PersonalAssetNotFound(normalized.asset_id)
            renamed = replace(existing, display_name=normalized.display_name)
            self._assets_by_id[renamed.asset_id] = renamed
            return renamed

    def remove(self, account_space_id: str, asset_id: str, removed_at: datetime) -> None:
        """不可恢复地移除个人资产及其媒体引用。"""
        normalized_account_space_id = account_space_id.strip()
        normalized_asset_id = asset_id.strip()
        with self._lock:
            existing = self._assets_by_id.get(normalized_asset_id)
            if existing is None or existing.account_space_id != normalized_account_space_id:
                raise PersonalAssetNotFound(normalized_asset_id)
            state = self._state_by_id[normalized_asset_id]
            if state == "removed":
                return
            if state == "active":
                self._state_by_id[normalized_asset_id] = "removing"
            self._generated_media.release_from_personal_asset(
                normalized_account_space_id,
                existing.media_id,
                normalized_asset_id,
                removed_at,
            )
            self._state_by_id[normalized_asset_id] = "removed"

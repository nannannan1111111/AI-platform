"""PersonalAssets Adapter 共享的命令校验。"""

from app.assets.models import InvalidPersonalAsset, PersonalAssetRename, PersonalAssetSave


def validated_save(command: PersonalAssetSave) -> PersonalAssetSave:
    """规范化并校验个人资产保存命令。"""
    display_name = command.display_name.strip()
    idempotency_key = command.idempotency_key.strip()
    if (
        not command.user_id.strip()
        or not command.account_space_id.strip()
        or not command.media_id.strip()
        or not display_name
        or len(display_name) > 120
        or not idempotency_key
        or len(idempotency_key) > 255
    ):
        raise InvalidPersonalAsset("个人资产参数无效")
    return PersonalAssetSave(
        user_id=command.user_id.strip(),
        account_space_id=command.account_space_id.strip(),
        media_id=command.media_id.strip(),
        display_name=display_name,
        idempotency_key=idempotency_key,
        saved_at=command.saved_at,
    )


def matches_replay(
    existing: PersonalAssetSave,
    replay: PersonalAssetSave,
    *,
    include_display_name: bool,
) -> bool:
    """按资产状态判断忽略服务器接收时间后的幂等命令是否一致。"""
    return (
        existing.user_id == replay.user_id
        and existing.account_space_id == replay.account_space_id
        and existing.media_id == replay.media_id
        and (not include_display_name or existing.display_name == replay.display_name)
        and existing.idempotency_key == replay.idempotency_key
    )


def validated_rename(command: PersonalAssetRename) -> PersonalAssetRename:
    """规范化并校验个人资产重命名命令。"""
    account_space_id = command.account_space_id.strip()
    asset_id = command.asset_id.strip()
    display_name = command.display_name.strip()
    if not account_space_id or not asset_id or not display_name or len(display_name) > 120:
        raise InvalidPersonalAsset("个人资产参数无效")
    return PersonalAssetRename(
        account_space_id=account_space_id,
        asset_id=asset_id,
        display_name=display_name,
    )

"""Commands and errors for account-owned prompt assets."""

from __future__ import annotations

from dataclasses import dataclass


class PromptAssetNotFound(LookupError):
    """The requested prompt asset does not belong to the account."""


class InvalidPromptAsset(ValueError):
    """The submitted prompt-asset data is invalid."""


@dataclass(frozen=True, slots=True)
class PromptLibraryCreate:
    """Request to create an account-owned prompt library."""

    account_space_id: str
    name: str


@dataclass(frozen=True, slots=True)
class PromptCategoryCreate:
    """Request to create a category inside a prompt library."""

    account_space_id: str
    library_id: str
    name: str


@dataclass(frozen=True, slots=True)
class PromptItemSave:
    """Validated input used to create or replace a prompt item."""

    account_space_id: str
    library_id: str
    name: str
    positive: str
    negative: str = ""
    category: str = "custom"
    scene: str = ""
    params: dict[str, object] | None = None

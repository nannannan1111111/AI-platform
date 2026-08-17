"""Port for account-owned prompt libraries, categories, and items."""

from __future__ import annotations

from typing import Protocol

from app.prompt_assets.models import PromptCategoryCreate, PromptItemSave, PromptLibraryCreate


class PromptAssets(Protocol):
    """Manage the complete prompt-asset projection for one account."""

    def projection(self, account_space_id: str) -> dict[str, object]:
        """Return the account's prompt libraries."""

    def create_library(self, command: PromptLibraryCreate) -> dict[str, object]:
        """Create a prompt library."""

    def rename_library(self, account_space_id: str, library_id: str, name: str) -> dict[str, object]:
        """Rename an existing library."""

    def delete_library(self, account_space_id: str, library_id: str) -> None:
        """Delete a non-system library."""

    def create_category(self, command: PromptCategoryCreate) -> dict[str, object]:
        """Create a category inside a library."""

    def rename_category(self, account_space_id: str, category_id: str, name: str) -> dict[str, object]:
        """Rename an existing category."""

    def delete_category(self, account_space_id: str, category_id: str) -> None:
        """Delete a category without deleting its prompt items."""

    def create_item(self, command: PromptItemSave) -> dict[str, object]:
        """Create a prompt item."""

    def update_item(self, account_space_id: str, item_id: str, command: PromptItemSave) -> dict[str, object]:
        """Replace an existing prompt item."""

    def delete_items(self, account_space_id: str, item_ids: tuple[str, ...]) -> None:
        """Delete the selected prompt items."""

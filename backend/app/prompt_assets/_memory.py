"""In-memory prompt-asset adapter used by tests and local composition."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast
from uuid import uuid4

from app.prompt_assets._shared import DEFAULT_CATEGORIES, DEFAULT_PROMPTS, clean_name, clean_prompt
from app.prompt_assets.models import PromptAssetNotFound, PromptCategoryCreate, PromptItemSave, PromptLibraryCreate


class InMemoryPromptAssets:
    """Keep account-owned prompt libraries in process memory."""

    def __init__(self) -> None:
        """Create an empty account projection store."""
        self._accounts: dict[str, dict[str, Any]] = {}

    def _data(self, account_space_id: str) -> dict[str, Any]:
        if account_space_id not in self._accounts:
            self._accounts[account_space_id] = {
                "active_library_id": "system",
                "libraries": [
                    {
                        "id": "system",
                        "name": "提示词资产库",
                        "readonly": False,
                        "categories": deepcopy(DEFAULT_CATEGORIES),
                        "items": deepcopy(DEFAULT_PROMPTS),
                    }
                ],
            }
        return self._accounts[account_space_id]

    def projection(self, account_space_id: str) -> dict[str, object]:
        """Return a detached account projection."""
        return deepcopy(self._data(account_space_id))

    def create_library(self, command: PromptLibraryCreate) -> dict[str, object]:
        """Create an empty prompt library."""
        item: dict[str, Any] = {
            "id": str(uuid4()),
            "name": clean_name(command.name),
            "readonly": False,
            "categories": [],
            "items": [],
        }
        self._data(command.account_space_id)["libraries"].append(item)
        return deepcopy(item)

    def _library(self, account_space_id: str, library_id: str) -> dict[str, Any]:
        item = next(
            (library for library in self._data(account_space_id)["libraries"] if library["id"] == library_id),
            None,
        )
        if item is None:
            raise PromptAssetNotFound(library_id)
        return cast(dict[str, Any], item)

    def rename_library(self, account_space_id: str, library_id: str, name: str) -> dict[str, object]:
        """Rename one account-owned library."""
        item = self._library(account_space_id, library_id)
        item["name"] = clean_name(name)
        return deepcopy(item)

    def delete_library(self, account_space_id: str, library_id: str) -> None:
        """Delete a non-system library."""
        if library_id == "system":
            raise PromptAssetNotFound(library_id)
        data = self._data(account_space_id)
        before = len(data["libraries"])
        data["libraries"] = [library for library in data["libraries"] if library["id"] != library_id]
        if len(data["libraries"]) == before:
            raise PromptAssetNotFound(library_id)

    def create_category(self, command: PromptCategoryCreate) -> dict[str, object]:
        """Create a category inside an existing library."""
        item: dict[str, Any] = {"id": str(uuid4()), "name": clean_name(command.name)}
        self._library(command.account_space_id, command.library_id)["categories"].append(item)
        return deepcopy(item)

    def _category(self, account_space_id: str, category_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for library in self._data(account_space_id)["libraries"]:
            for item in library["categories"]:
                if item["id"] == category_id:
                    return library, item
        raise PromptAssetNotFound(category_id)

    def rename_category(self, account_space_id: str, category_id: str, name: str) -> dict[str, object]:
        """Rename an existing category."""
        _, item = self._category(account_space_id, category_id)
        item["name"] = clean_name(name)
        return deepcopy(item)

    def delete_category(self, account_space_id: str, category_id: str) -> None:
        """Delete a category and move its prompt items to the custom category."""
        library, _ = self._category(account_space_id, category_id)
        library["categories"] = [item for item in library["categories"] if item["id"] != category_id]
        for item in library["items"]:
            if item.get("category") == category_id:
                item["category"] = "custom"

    def create_item(self, command: PromptItemSave) -> dict[str, object]:
        """Create a prompt item inside an existing library."""
        item: dict[str, Any] = {"id": str(uuid4()), **clean_prompt(command)}
        self._library(command.account_space_id, command.library_id)["items"].append(item)
        return deepcopy(item)

    def _item(self, account_space_id: str, item_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for library in self._data(account_space_id)["libraries"]:
            for item in library["items"]:
                if item["id"] == item_id:
                    return library, item
        raise PromptAssetNotFound(item_id)

    def update_item(self, account_space_id: str, item_id: str, command: PromptItemSave) -> dict[str, object]:
        """Replace a prompt item and optionally move it between libraries."""
        old_library, item = self._item(account_space_id, item_id)
        new_library = self._library(account_space_id, command.library_id)
        item.update(clean_prompt(command))
        if old_library is not new_library:
            old_library["items"].remove(item)
            new_library["items"].append(item)
        return deepcopy(item)

    def delete_items(self, account_space_id: str, item_ids: tuple[str, ...]) -> None:
        """Delete the selected prompt items wherever they are stored."""
        wanted = set(item_ids)
        for library in self._data(account_space_id)["libraries"]:
            library["items"] = [item for item in library["items"] if item["id"] not in wanted]

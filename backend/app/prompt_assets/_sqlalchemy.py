"""SQLAlchemy adapter for account-owned prompt assets."""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.prompt_assets._shared import DEFAULT_CATEGORIES, DEFAULT_PROMPTS, clean_name, clean_prompt
from app.prompt_assets.models import PromptAssetNotFound, PromptCategoryCreate, PromptItemSave, PromptLibraryCreate
from app.prompt_assets.tables import (
    prompt_categories,
    prompt_items,
    prompt_libraries,
    prompt_library_accounts,
)


class SqlAlchemyPromptAssets:
    """Persist each account's prompt libraries in the shared database."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Retain the shared session factory."""
        self._sessions = session_factory

    def _seed(self, database: Session, account_space_id: str) -> None:
        exists = database.execute(
            select(prompt_library_accounts.c.account_space_id).where(
                prompt_library_accounts.c.account_space_id == account_space_id
            )
        ).first()
        if exists:
            return
        database.execute(
            insert(prompt_library_accounts).values(account_space_id=account_space_id, seeded=True)
        )
        database.execute(
            insert(prompt_libraries).values(
                id="system",
                account_space_id=account_space_id,
                name="提示词资产库",
                position=0,
            )
        )
        database.execute(
            insert(prompt_categories),
            [
                {
                    **item,
                    "account_space_id": account_space_id,
                    "library_id": "system",
                    "position": index,
                }
                for index, item in enumerate(DEFAULT_CATEGORIES)
            ],
        )
        database.execute(
            insert(prompt_items),
            [
                {
                    "id": item["id"],
                    "account_space_id": account_space_id,
                    "library_id": "system",
                    "name": item["name"],
                    "positive": item["positive"],
                    "negative": item["negative"],
                    "category_id": item["category"],
                    "scene": item["scene"],
                    "params_json": json.dumps(item["params"], ensure_ascii=False),
                    "position": index,
                }
                for index, item in enumerate(DEFAULT_PROMPTS)
            ],
        )

    def projection(self, account_space_id: str) -> dict[str, object]:
        """Return the complete account prompt projection."""
        with self._sessions.begin() as database:
            self._seed(database, account_space_id)
            libraries = list(
                database.execute(
                    select(prompt_libraries)
                    .where(prompt_libraries.c.account_space_id == account_space_id)
                    .order_by(prompt_libraries.c.position, prompt_libraries.c.name)
                ).mappings()
            )
            categories = list(
                database.execute(
                    select(prompt_categories)
                    .where(prompt_categories.c.account_space_id == account_space_id)
                    .order_by(prompt_categories.c.position, prompt_categories.c.name)
                ).mappings()
            )
            items = list(
                database.execute(
                    select(prompt_items)
                    .where(prompt_items.c.account_space_id == account_space_id)
                    .order_by(prompt_items.c.position, prompt_items.c.name)
                ).mappings()
            )
        result: list[dict[str, object]] = []
        for library in libraries:
            library_id = str(library["id"])
            result.append(
                {
                    "id": library_id,
                    "name": str(library["name"]),
                    "readonly": False,
                    "categories": [
                        {"id": str(item["id"]), "name": str(item["name"])}
                        for item in categories
                        if item["library_id"] == library_id
                    ],
                    "items": [
                        {
                            "id": str(item["id"]),
                            "name": str(item["name"]),
                            "positive": str(item["positive"]),
                            "negative": str(item["negative"]),
                            "category": str(item["category_id"]),
                            "scene": str(item["scene"]),
                            "params": json.loads(str(item["params_json"])),
                        }
                        for item in items
                        if item["library_id"] == library_id
                    ],
                }
            )
        return {"active_library_id": "system", "libraries": result}

    def create_library(self, command: PromptLibraryCreate) -> dict[str, object]:
        """Create an empty library."""
        library_id = str(uuid4())
        name = clean_name(command.name)
        with self._sessions.begin() as database:
            self._seed(database, command.account_space_id)
            database.execute(
                insert(prompt_libraries).values(
                    id=library_id,
                    account_space_id=command.account_space_id,
                    name=name,
                    position=100,
                )
            )
        return {"id": library_id, "name": name, "readonly": False, "categories": [], "items": []}

    def rename_library(self, account_space_id: str, library_id: str, name: str) -> dict[str, object]:
        """Rename an existing account-owned library."""
        cleaned_name = clean_name(name)
        with self._sessions.begin() as database:
            renamed_id = database.execute(
                update(prompt_libraries)
                .where(
                    prompt_libraries.c.account_space_id == account_space_id,
                    prompt_libraries.c.id == library_id,
                )
                .values(name=cleaned_name)
                .returning(prompt_libraries.c.id)
            ).scalar_one_or_none()
            if renamed_id is None:
                raise PromptAssetNotFound(library_id)
        return {"id": library_id, "name": cleaned_name}

    def delete_library(self, account_space_id: str, library_id: str) -> None:
        """Delete a non-system library."""
        if library_id == "system":
            raise PromptAssetNotFound(library_id)
        with self._sessions.begin() as database:
            deleted_id = database.execute(
                delete(prompt_libraries)
                .where(
                    prompt_libraries.c.account_space_id == account_space_id,
                    prompt_libraries.c.id == library_id,
                )
                .returning(prompt_libraries.c.id)
            ).scalar_one_or_none()
            if deleted_id is None:
                raise PromptAssetNotFound(library_id)

    def create_category(self, command: PromptCategoryCreate) -> dict[str, object]:
        """Create a category in an existing library."""
        category_id = str(uuid4())
        name = clean_name(command.name)
        with self._sessions.begin() as database:
            self._require_library(database, command.account_space_id, command.library_id)
            database.execute(
                insert(prompt_categories).values(
                    id=category_id,
                    account_space_id=command.account_space_id,
                    library_id=command.library_id,
                    name=name,
                    position=100,
                )
            )
        return {"id": category_id, "name": name}

    def rename_category(self, account_space_id: str, category_id: str, name: str) -> dict[str, object]:
        """Rename an existing category."""
        cleaned_name = clean_name(name)
        with self._sessions.begin() as database:
            renamed_id = database.execute(
                update(prompt_categories)
                .where(
                    prompt_categories.c.account_space_id == account_space_id,
                    prompt_categories.c.id == category_id,
                )
                .values(name=cleaned_name)
                .returning(prompt_categories.c.id)
            ).scalar_one_or_none()
            if renamed_id is None:
                raise PromptAssetNotFound(category_id)
        return {"id": category_id, "name": cleaned_name}

    def delete_category(self, account_space_id: str, category_id: str) -> None:
        """Delete a category and move its items to the custom category."""
        with self._sessions.begin() as database:
            deleted_id = database.execute(
                delete(prompt_categories)
                .where(
                    prompt_categories.c.account_space_id == account_space_id,
                    prompt_categories.c.id == category_id,
                )
                .returning(prompt_categories.c.id)
            ).scalar_one_or_none()
            if deleted_id is None:
                raise PromptAssetNotFound(category_id)
            database.execute(
                update(prompt_items)
                .where(
                    prompt_items.c.account_space_id == account_space_id,
                    prompt_items.c.category_id == category_id,
                )
                .values(category_id="custom")
            )

    def create_item(self, command: PromptItemSave) -> dict[str, object]:
        """Create a prompt item in an existing library."""
        cleaned = clean_prompt(command)
        item_id = str(uuid4())
        with self._sessions.begin() as database:
            self._require_library(database, command.account_space_id, command.library_id)
            database.execute(
                insert(prompt_items).values(
                    id=item_id,
                    account_space_id=command.account_space_id,
                    library_id=command.library_id,
                    name=cleaned["name"],
                    positive=cleaned["positive"],
                    negative=cleaned["negative"],
                    category_id=cleaned["category"],
                    scene=cleaned["scene"],
                    params_json=json.dumps(cleaned["params"], ensure_ascii=False),
                    position=100,
                )
            )
        return {"id": item_id, **cleaned}

    def update_item(self, account_space_id: str, item_id: str, command: PromptItemSave) -> dict[str, object]:
        """Replace an existing prompt item."""
        cleaned = clean_prompt(command)
        with self._sessions.begin() as database:
            self._require_library(database, account_space_id, command.library_id)
            updated_id = database.execute(
                update(prompt_items)
                .where(
                    prompt_items.c.account_space_id == account_space_id,
                    prompt_items.c.id == item_id,
                )
                .values(
                    library_id=command.library_id,
                    name=cleaned["name"],
                    positive=cleaned["positive"],
                    negative=cleaned["negative"],
                    category_id=cleaned["category"],
                    scene=cleaned["scene"],
                    params_json=json.dumps(cleaned["params"], ensure_ascii=False),
                )
                .returning(prompt_items.c.id)
            ).scalar_one_or_none()
            if updated_id is None:
                raise PromptAssetNotFound(item_id)
        return {"id": item_id, **cleaned}

    def delete_items(self, account_space_id: str, item_ids: tuple[str, ...]) -> None:
        """Delete selected prompt items owned by an account."""
        with self._sessions.begin() as database:
            database.execute(
                delete(prompt_items).where(
                    prompt_items.c.account_space_id == account_space_id,
                    prompt_items.c.id.in_(item_ids),
                )
            )

    @staticmethod
    def _require_library(database: Session, account_space_id: str, library_id: str) -> None:
        exists = database.execute(
            select(prompt_libraries.c.id).where(
                prompt_libraries.c.account_space_id == account_space_id,
                prompt_libraries.c.id == library_id,
            )
        ).first()
        if exists is None:
            raise PromptAssetNotFound(library_id)

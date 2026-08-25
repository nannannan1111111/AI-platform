"""管理员维护的提示词安全规则。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Boolean, Column, MetaData, String, Table, Text, insert, select, update
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True, slots=True)
class PromptSafetySettings:
    enabled: bool = True
    prompt_check_enabled: bool = True
    keywords: tuple[str, ...] = ()


class PromptSafety(Protocol):
    def current(self) -> PromptSafetySettings: ...
    def update(self, *, enabled: bool, prompt_check_enabled: bool, keywords: list[str]) -> PromptSafetySettings: ...
    def matches(self, prompt: str) -> tuple[str, ...]: ...


def normalize_keywords(values: list[str] | tuple[str, ...] | str) -> tuple[str, ...]:
    source = values.splitlines() if isinstance(values, str) else values
    result: list[str] = []
    seen: set[str] = set()
    for value in source:
        keyword = str(value).strip()
        if keyword and keyword.casefold() not in seen:
            seen.add(keyword.casefold())
            result.append(keyword)
    return tuple(result[:10_000])


class InMemoryPromptSafety:
    def __init__(self, settings: PromptSafetySettings | None = None) -> None:
        self._settings = settings or PromptSafetySettings()

    def current(self) -> PromptSafetySettings:
        return self._settings

    def update(self, *, enabled: bool, prompt_check_enabled: bool, keywords: list[str]) -> PromptSafetySettings:
        self._settings = PromptSafetySettings(enabled, prompt_check_enabled, normalize_keywords(keywords))
        return self._settings

    def matches(self, prompt: str) -> tuple[str, ...]:
        settings = self._settings
        if not settings.enabled or not settings.prompt_check_enabled:
            return ()
        folded = prompt.casefold()
        return tuple(keyword for keyword in settings.keywords if keyword.casefold() in folded)


_metadata = MetaData()
_settings = Table(
    "prompt_safety_settings",
    _metadata,
    Column("settings_key", String(32), primary_key=True),
    Column("enabled", Boolean, nullable=False),
    Column("prompt_check_enabled", Boolean, nullable=False),
    Column("keywords", Text, nullable=False),
)


class SqlAlchemyPromptSafety:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def current(self) -> PromptSafetySettings:
        with self._session_factory() as database:
            row = database.execute(select(_settings).where(_settings.c.settings_key == "global")).mappings().one_or_none()
        if row is None:
            return PromptSafetySettings()
        return PromptSafetySettings(bool(row["enabled"]), bool(row["prompt_check_enabled"]), normalize_keywords(str(row["keywords"])))

    def update(self, *, enabled: bool, prompt_check_enabled: bool, keywords: list[str]) -> PromptSafetySettings:
        normalized = normalize_keywords(keywords)
        payload = {"enabled": bool(enabled), "prompt_check_enabled": bool(prompt_check_enabled), "keywords": "\n".join(normalized)}
        with self._session_factory.begin() as database:
            existing = database.execute(select(_settings.c.settings_key).where(_settings.c.settings_key == "global")).scalar_one_or_none()
            if existing is None:
                database.execute(insert(_settings).values(settings_key="global", **payload))
            else:
                database.execute(update(_settings).where(_settings.c.settings_key == "global").values(**payload))
        return PromptSafetySettings(payload["enabled"], payload["prompt_check_enabled"], normalized)

    def matches(self, prompt: str) -> tuple[str, ...]:
        settings = self.current()
        if not settings.enabled or not settings.prompt_check_enabled:
            return ()
        folded = prompt.casefold()
        return tuple(keyword for keyword in settings.keywords if keyword.casefold() in folded)


__all__ = ["InMemoryPromptSafety", "PromptSafety", "PromptSafetySettings", "SqlAlchemyPromptSafety", "normalize_keywords"]

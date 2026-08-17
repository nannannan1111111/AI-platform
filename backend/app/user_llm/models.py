"""Account-owned LLM provider models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class InvalidUserLLMProvider(ValueError):
    """The submitted provider configuration is invalid."""


class UserLLMProviderNotFound(LookupError):
    """No provider owned by the account matches the requested identifier."""


class UserLLMUpstreamError(RuntimeError):
    """The configured LLM endpoint could not complete a request."""


@dataclass(frozen=True, slots=True)
class UserLLMProvider:
    """Safe account-owned provider projection."""

    id: str
    account_space_id: str
    code: str
    display_name: str
    base_url: str
    models: tuple[str, ...]
    enabled: bool
    has_key: bool
    key_fingerprint: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class UserLLMProviderSave:
    """Input used to create or replace an account-owned provider."""

    account_space_id: str
    code: str
    display_name: str
    base_url: str
    models: tuple[str, ...]
    enabled: bool = True
    api_key: str = ""


@dataclass(frozen=True, slots=True)
class UserLLMCompletion:
    """Completion request routed through an account-owned provider."""

    account_space_id: str
    provider_code: str
    model: str
    message: str
    system_prompt: str = ""
    messages: tuple[dict[str, object], ...] = ()
    images: tuple[str, ...] = ()
    videos: tuple[str, ...] = ()

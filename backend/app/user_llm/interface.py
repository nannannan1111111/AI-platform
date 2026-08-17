"""Account-owned LLM provider interface."""

from typing import Protocol

from app.user_llm.models import UserLLMCompletion, UserLLMProvider, UserLLMProviderSave


class UserLLMProviders(Protocol):
    """Manage and invoke LLM providers owned by one account."""

    def list(self, account_space_id: str) -> tuple[UserLLMProvider, ...]:
        """List safe provider projections owned by an account."""

    def create(self, command: UserLLMProviderSave) -> UserLLMProvider:
        """Create an account-owned provider."""

    def update(self, provider_id: str, command: UserLLMProviderSave) -> UserLLMProvider:
        """Replace an account-owned provider configuration."""

    def delete(self, account_space_id: str, provider_id: str) -> None:
        """Delete an account-owned provider and its secret."""

    def complete(self, command: UserLLMCompletion) -> str:
        """Send one completion through the selected account provider."""

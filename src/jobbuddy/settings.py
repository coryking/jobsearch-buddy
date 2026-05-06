"""jobbuddy settings — pydantic-settings with platformdirs for config/data paths.

Priority (highest to lowest):
  explicit kwargs > env vars (JOBBUDDY_*) > defaults

Settings are inert data — no network calls, no side effects. Token fetching
for Azure Entra auth happens at connection time via get_azure_token().
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, TypeAlias

from platformdirs import user_data_dir
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_NAME = "jobsearch-buddy"

ReasoningEffort: TypeAlias = Literal["minimal", "low", "medium", "high"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOBBUDDY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path(user_data_dir(_APP_NAME)) / "data"
    listings_dir: Path = Path(user_data_dir(_APP_NAME)) / "listings"

    # PostgreSQL connection via pg_service.conf (local) or Entra token (Azure)
    pg_service: str = "job-search-buddy-azure"
    postgres_host: Optional[str] = None  # Set to enable Azure Entra token auth
    postgres_database: Optional[str] = None
    postgres_user: Optional[str] = None  # Managed identity name (not client ID)

    # OpenAI API (for the distill phase)
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_azure_api_version: Optional[str] = None  # If set, uses AzureOpenAI client
    distill_model: str = "gpt-5.4-nano"
    distill_reasoning_effort: ReasoningEffort = "high"
    distill_prompt_version: str = "distill-v3.1"

    research_model: str = "gpt-5.4"
    research_endpoint: Optional[str] = None
    research_max_workers: int = 4  # Bing web_search 429s observed >4 concurrent

    embedding_model: str = "text-embedding-3-small"

    @property
    def has_openai(self) -> bool:
        """Whether OpenAI credentials are configured (enables the distill phase)."""
        return bool(self.openai_api_key or self.openai_azure_api_version)

    @property
    def has_research(self) -> bool:
        """Whether company-research is configured.

        Research uses Azure's OpenAI-compatible /openai/v1/ Responses surface
        with managed-identity bearer auth. Recognizes the three documented
        Azure-resource hostname suffixes: openai.azure.com,
        cognitiveservices.azure.com, and api.cognitive.microsoft.com.
        """
        endpoint = self.research_endpoint or self.openai_base_url or ""
        host = endpoint.lower()
        return any(
            suffix in host
            for suffix in (
                ".openai.azure.com",
                ".cognitiveservices.azure.com",
                ".api.cognitive.microsoft.com",
            )
        )

    @property
    def needs_azure_token(self) -> bool:
        """Whether this config requires an Azure Entra token for DB auth."""
        return bool(self.postgres_host) or "azure" in self.pg_service

    @property
    def pg_conninfo(self) -> str:
        """Base connection info string (no credentials).

        Returns the pg_service reference or host-based URI template.
        For Azure auth, use pg_conninfo_with_token() which adds the Entra token.
        """
        if self.postgres_host:
            return (
                f"postgresql://{self.postgres_user}@{self.postgres_host}"
                f":5432/{self.postgres_database}?sslmode=require"
            )
        return f"service={self.pg_service}"

    @field_validator("data_dir", "listings_dir", mode="after")
    @classmethod
    def _expand_path(cls, v):
        if v is None:
            return v
        return Path(str(v)).expanduser()


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# ---------------------------------------------------------------------------
# Azure Entra credential + token helpers
# ---------------------------------------------------------------------------

_azure_credential = None


def get_azure_credential():
    """Return a singleton DefaultAzureCredential.

    One instance shared across all Azure auth (DB, OpenAI, Redis, etc.)
    so MSAL's in-memory token cache persists for the process lifetime.
    Tokens are valid ~60 min; MSAL auto-refreshes within 5 min of expiry.
    """
    global _azure_credential
    if _azure_credential is None:
        from azure.identity import DefaultAzureCredential
        _azure_credential = DefaultAzureCredential()
    return _azure_credential


def get_azure_token(scope: str) -> str:
    """Fetch an Azure Entra token for the given resource scope.

    Uses the singleton credential so repeated calls for the same scope
    return a cached token until near expiry.
    """
    return get_azure_credential().get_token(scope).token


def pg_conninfo_with_token(settings: Settings | None = None) -> str:
    """Build a connection string, adding an Azure Entra token if needed.

    Called at connection time by store._connect().
    """
    s = settings or get_settings()
    if not s.needs_azure_token:
        return s.pg_conninfo

    token = get_azure_token("https://ossrdbms-aad.database.windows.net/.default")
    if s.postgres_host:
        return (
            f"postgresql://{s.postgres_user}:{token}"
            f"@{s.postgres_host}:5432/{s.postgres_database}"
            f"?sslmode=require"
        )
    return f"service={s.pg_service} password={token}"

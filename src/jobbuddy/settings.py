"""jobbuddy settings — pydantic-settings with platformdirs for config/data paths.

Priority (highest to lowest):
  explicit kwargs > env vars (JOBBUDDY_*) > defaults

Settings are inert data — no network calls, no side effects. Token fetching
for Azure Entra auth happens at connection time in pg_connect().
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from platformdirs import user_data_dir
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_NAME = "jobsearch-buddy"


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

    # OpenAI API (for strip, embed, and semantic search)
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_azure_api_version: Optional[str] = None  # If set, uses AzureOpenAI client
    strip_model: str = "gpt-5-nano"
    embedding_model: str = "text-embedding-3-small"
    strip_batch_size: int = 50

    @property
    def has_openai(self) -> bool:
        """Whether OpenAI credentials are configured (enables strip/embed/search)."""
        return bool(self.openai_api_key or self.openai_azure_api_version)

    @property
    def needs_azure_token(self) -> bool:
        """Whether this config requires an Azure Entra token for DB auth."""
        return bool(self.postgres_host) or "azure" in self.pg_service

    @property
    def pg_conninfo(self) -> str:
        """Base connection info string (no credentials).

        Returns the pg_service reference or host-based URI template.
        For Azure auth, use pg_connect() which adds the Entra token.
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


def _get_azure_token() -> str:
    """Fetch an Azure Entra token for PostgreSQL. Cached internally by DefaultAzureCredential."""
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential().get_token(
        "https://ossrdbms-aad.database.windows.net/.default"
    ).token


def pg_conninfo_with_token(settings: Settings | None = None) -> str:
    """Build a connection string, adding an Azure Entra token if needed.

    This is the only place token fetching happens. Called at connection time
    by pg_connect() and store._connect().
    """
    s = settings or get_settings()
    if not s.needs_azure_token:
        return s.pg_conninfo

    token = _get_azure_token()
    if s.postgres_host:
        return (
            f"postgresql://{s.postgres_user}:{token}"
            f"@{s.postgres_host}:5432/{s.postgres_database}"
            f"?sslmode=require"
        )
    return f"service={s.pg_service} password={token}"

"""jobbuddy settings — pydantic-settings with platformdirs for config/data paths.

Priority (highest to lowest):
  explicit kwargs > env vars (JOBBUDDY_*) > defaults
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
    pg_service: str = "job-search-buddy-remote"
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
    def pg_conninfo(self) -> str:
        """Connection info string for psycopg.

        Azure mode (postgres_host set): builds a connection URI with a fresh
        Entra token on every call. DefaultAzureCredential caches tokens internally
        and auto-refreshes before expiry, so calling get_token() is cheap.

        Local mode: returns pg_service reference.
        """
        if self.postgres_host:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
            token = credential.get_token(
                "https://ossrdbms-aad.database.windows.net/.default"
            )
            return (
                f"postgresql://{self.postgres_user}:{token.token}"
                f"@{self.postgres_host}:5432/{self.postgres_database}"
                f"?sslmode=require"
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

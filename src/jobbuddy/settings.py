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

    data_dir: Path = Path.home() / "projects" / "resume" / "data"
    db_path: Path = None  # type: ignore[reportAssignmentType]  — validator always fills this
    listings_dir: Path = Path.home() / "projects" / "resume" / "job-listings"

    # Azure OpenAI (for description stripping)
    azure_openai_api_key: Optional[str] = None
    azure_openai_endpoint: Optional[str] = None
    azure_openai_api_version: str = "2024-12-01-preview"
    azure_openai_model: str = "gpt-5-nano"
    strip_batch_size: int = 50

    @field_validator("db_path", mode="before")
    @classmethod
    def _default_db_path(cls, v):
        if v is None:
            return Path(user_data_dir(_APP_NAME)) / "jobs_cache.db"
        return v

    @field_validator("data_dir", "listings_dir", "db_path", mode="after")
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

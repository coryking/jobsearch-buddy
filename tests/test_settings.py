"""Tests for jobbuddy.settings — pydantic-settings + platformdirs config."""

import os
from pathlib import Path

import pytest


class TestDefaultPaths:
    def test_data_dir_default(self, monkeypatch):
        """Default data_dir points to ~/projects/resume/data."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_DB_PATH", raising=False)
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        # Clear cached singleton
        import importlib
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.data_dir == Path.home() / "projects" / "resume" / "data"

    def test_db_path_default_uses_platformdirs(self, monkeypatch):
        """Default db_path uses platformdirs user_data_dir."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_DB_PATH", raising=False)
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.db_path.name == "jobs_cache.db"
        assert "jobsearch-buddy" in str(settings.db_path)

    def test_listings_dir_default(self, monkeypatch):
        """Default listings_dir points to ~/projects/resume/job-listings."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_DB_PATH", raising=False)
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.listings_dir == Path.home() / "projects" / "resume" / "job-listings"


class TestEnvVarOverrides:
    def test_data_dir_env_override(self, monkeypatch, tmp_path):
        """JOBBUDDY_DATA_DIR env var overrides default."""
        monkeypatch.setenv("JOBBUDDY_DATA_DIR", str(tmp_path / "mydata"))
        monkeypatch.delenv("JOBBUDDY_DB_PATH", raising=False)
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.data_dir == tmp_path / "mydata"

    def test_db_path_env_override(self, monkeypatch, tmp_path):
        """JOBBUDDY_DB_PATH env var overrides default."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.setenv("JOBBUDDY_DB_PATH", str(tmp_path / "custom.db"))
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.db_path == tmp_path / "custom.db"

    def test_listings_dir_env_override(self, monkeypatch, tmp_path):
        """JOBBUDDY_LISTINGS_DIR env var overrides default."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_DB_PATH", raising=False)
        monkeypatch.setenv("JOBBUDDY_LISTINGS_DIR", str(tmp_path / "listings"))
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.listings_dir == tmp_path / "listings"


class TestSingleton:
    def test_get_settings_returns_same_instance(self, monkeypatch):
        """get_settings() returns a cached singleton."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_DB_PATH", raising=False)
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        a = get_settings()
        b = get_settings()
        assert a is b

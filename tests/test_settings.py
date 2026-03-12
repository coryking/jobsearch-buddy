"""Tests for jobbuddy.settings -- pydantic-settings + platformdirs config."""

import os
from pathlib import Path

import pytest


class TestDefaultPaths:
    def test_data_dir_default(self, monkeypatch):
        """Default data_dir uses platformdirs user_data_dir."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_PG_SERVICE", raising=False)
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.data_dir.name == "data"
        assert "jobsearch-buddy" in str(settings.data_dir)

    def test_pg_service_default(self, monkeypatch):
        """Default pg_service is job-search-buddy-remote."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_PG_SERVICE", raising=False)
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.pg_service == "job-search-buddy-remote"
        assert settings.pg_conninfo == "service=job-search-buddy-remote"

    def test_listings_dir_default(self, monkeypatch):
        """Default listings_dir uses platformdirs user_data_dir."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_PG_SERVICE", raising=False)
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.listings_dir.name == "listings"
        assert "jobsearch-buddy" in str(settings.listings_dir)


class TestEnvVarOverrides:
    def test_data_dir_env_override(self, monkeypatch, tmp_path):
        """JOBBUDDY_DATA_DIR env var overrides default."""
        monkeypatch.setenv("JOBBUDDY_DATA_DIR", str(tmp_path / "mydata"))
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_PG_SERVICE", raising=False)
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.data_dir == tmp_path / "mydata"

    def test_pg_service_env_override(self, monkeypatch):
        """JOBBUDDY_PG_SERVICE env var overrides default."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        monkeypatch.setenv("JOBBUDDY_PG_SERVICE", "custom-service")
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.pg_service == "custom-service"
        assert settings.pg_conninfo == "service=custom-service"

    def test_listings_dir_env_override(self, monkeypatch, tmp_path):
        """JOBBUDDY_LISTINGS_DIR env var overrides default."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.setenv("JOBBUDDY_LISTINGS_DIR", str(tmp_path / "listings"))
        monkeypatch.delenv("JOBBUDDY_PG_SERVICE", raising=False)
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        settings = get_settings()
        assert settings.listings_dir == tmp_path / "listings"


class TestSingleton:
    def test_get_settings_returns_same_instance(self, monkeypatch):
        """get_settings() returns a cached singleton."""
        monkeypatch.delenv("JOBBUDDY_DATA_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_LISTINGS_DIR", raising=False)
        monkeypatch.delenv("JOBBUDDY_PG_SERVICE", raising=False)
        import jobbuddy.settings as s
        s._settings = None

        from jobbuddy.settings import get_settings
        a = get_settings()
        b = get_settings()
        assert a is b

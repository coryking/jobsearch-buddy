"""Tests for jobbuddy.openai_client — client factory for OpenAI-compatible APIs."""

from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.openai_client import create_openai_client


@pytest.fixture(autouse=True)
def clear_settings_singleton():
    """Reset the settings singleton so each test gets fresh settings."""
    import jobbuddy.settings as s
    s._settings = None
    yield
    s._settings = None


class TestCreateOpenaiClient:
    def test_raises_without_api_key(self):
        settings = MagicMock()
        settings.openai_api_key = None
        with patch("jobbuddy.openai_client.get_settings", return_value=settings):
            with pytest.raises(ValueError, match="OpenAI API not configured"):
                create_openai_client()

    def test_returns_azure_client_when_api_version_set(self):
        settings = MagicMock()
        settings.openai_api_key = "test-key"
        settings.openai_base_url = "https://westus3.api.cognitive.microsoft.com/"
        settings.openai_azure_api_version = "2024-12-01-preview"
        with (
            patch("jobbuddy.openai_client.get_settings", return_value=settings),
            patch("jobbuddy.openai_client.AzureOpenAI") as mock_azure,
        ):
            client = create_openai_client()
            mock_azure.assert_called_once_with(
                api_key="test-key",
                azure_endpoint="https://westus3.api.cognitive.microsoft.com/",
                api_version="2024-12-01-preview",
            )
            assert client is mock_azure.return_value

    def test_returns_standard_client_without_api_version(self):
        settings = MagicMock()
        settings.openai_api_key = "sk-test"
        settings.openai_base_url = None
        settings.openai_azure_api_version = None
        with (
            patch("jobbuddy.openai_client.get_settings", return_value=settings),
            patch("jobbuddy.openai_client.OpenAI") as mock_openai,
        ):
            client = create_openai_client()
            mock_openai.assert_called_once_with(
                api_key="sk-test",
                base_url=None,
            )
            assert client is mock_openai.return_value

    def test_passes_custom_base_url_to_standard_client(self):
        settings = MagicMock()
        settings.openai_api_key = "sk-test"
        settings.openai_base_url = "http://localhost:11434/v1"
        settings.openai_azure_api_version = None
        with (
            patch("jobbuddy.openai_client.get_settings", return_value=settings),
            patch("jobbuddy.openai_client.OpenAI") as mock_openai,
        ):
            create_openai_client()
            mock_openai.assert_called_once_with(
                api_key="sk-test",
                base_url="http://localhost:11434/v1",
            )

    def test_passes_extra_kwargs_through(self):
        settings = MagicMock()
        settings.openai_api_key = "sk-test"
        settings.openai_base_url = None
        settings.openai_azure_api_version = None
        with (
            patch("jobbuddy.openai_client.get_settings", return_value=settings),
            patch("jobbuddy.openai_client.OpenAI") as mock_openai,
        ):
            create_openai_client(timeout=60.0, max_retries=0)
            mock_openai.assert_called_once_with(
                api_key="sk-test",
                base_url=None,
                timeout=60.0,
                max_retries=0,
            )

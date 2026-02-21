"""OpenAI client factory — supports standard OpenAI, Azure, and compatible APIs.

Detects Azure mode when openai_azure_api_version is set. Otherwise returns a
standard OpenAI client (works with api.openai.com, Groq, Together, Ollama, etc.).
"""

from __future__ import annotations

from openai import AzureOpenAI, OpenAI

from jobbuddy.settings import get_settings


def create_openai_client(**kwargs) -> OpenAI:
    """Create an OpenAI client based on settings.

    If openai_azure_api_version is set, returns AzureOpenAI (base_url used as
    azure_endpoint). Otherwise returns standard OpenAI client.

    Raises ValueError if openai_api_key is not configured.
    """
    s = get_settings()
    if not s.openai_api_key:
        raise ValueError(
            "OpenAI API not configured. Set JOBBUDDY_OPENAI_API_KEY. "
            "Optionally set JOBBUDDY_OPENAI_BASE_URL for non-OpenAI providers, "
            "or JOBBUDDY_OPENAI_AZURE_API_VERSION for Azure."
        )
    if s.openai_azure_api_version:
        return AzureOpenAI(
            api_key=s.openai_api_key,
            azure_endpoint=s.openai_base_url,  # type: ignore[arg-type]  # validated above
            api_version=s.openai_azure_api_version,
            **kwargs,
        )
    return OpenAI(
        api_key=s.openai_api_key,
        base_url=s.openai_base_url or None,
        **kwargs,
    )

"""OpenAI client factory — supports standard OpenAI, Azure, and compatible APIs.

Detects Azure mode when openai_azure_api_version is set. Otherwise returns a
standard OpenAI client (works with api.openai.com, Groq, Together, Ollama, etc.).
"""

from __future__ import annotations

from openai import AzureOpenAI, OpenAI

from jobbuddy.settings import get_settings


def create_openai_client(**kwargs) -> OpenAI:
    """Create an OpenAI client based on settings.

    Three modes:
    1. Azure managed identity: api_version set, api_key absent → DefaultAzureCredential
    2. Azure API key: api_version set, api_key set → AzureOpenAI with key
    3. Standard OpenAI: no api_version → OpenAI client (works with Groq, Together, etc.)

    Raises ValueError if no credentials are configured.
    """
    s = get_settings()

    if s.openai_azure_api_version and not s.openai_api_key:
        # Managed identity mode (Azure Functions)
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )
        return AzureOpenAI(
            azure_endpoint=s.openai_base_url,  # type: ignore[arg-type]
            azure_ad_token_provider=token_provider,
            api_version=s.openai_azure_api_version,
            **kwargs,
        )

    if not s.openai_api_key:
        raise ValueError(
            "OpenAI API not configured. Set JOBBUDDY_OPENAI_API_KEY. "
            "Optionally set JOBBUDDY_OPENAI_BASE_URL for non-OpenAI providers, "
            "or JOBBUDDY_OPENAI_AZURE_API_VERSION for Azure."
        )

    if s.openai_azure_api_version:
        # Azure API key mode (local dev)
        return AzureOpenAI(
            api_key=s.openai_api_key,
            azure_endpoint=s.openai_base_url,  # type: ignore[arg-type]
            api_version=s.openai_azure_api_version,
            **kwargs,
        )

    # Catch misconfiguration: Azure-looking URL without the api_version set
    if s.openai_base_url and (
        ".cognitive.microsoft.com" in s.openai_base_url
        or ".cognitiveservices.azure.com" in s.openai_base_url
    ):
        raise ValueError(
            "JOBBUDDY_OPENAI_BASE_URL looks like an Azure endpoint but "
            "JOBBUDDY_OPENAI_AZURE_API_VERSION is not set. "
            "Set it (e.g., '2024-12-01-preview') to use Azure OpenAI."
        )
    return OpenAI(
        api_key=s.openai_api_key,
        base_url=s.openai_base_url or None,
        **kwargs,
    )

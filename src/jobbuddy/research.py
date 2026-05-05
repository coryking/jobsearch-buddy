"""Company bio research via Azure Responses API + web_search.

Uses Azure's OpenAI-compatible /openai/v1/ surface (separate from the
chat-completions /openai/{deployment}/ path used by strip/embed) because
the Responses API is required for the `web_search` tool.

Auth: AAD bearer token via DefaultAzureCredential — same managed-identity
path the rest of the project uses for Azure services.

Returns CompanyBio. Callers handle persistence and retry orchestration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from jobbuddy.settings import Settings, get_azure_credential, get_settings

log = logging.getLogger(__name__)


PROMPT_FILE = Path(__file__).resolve().parents[2] / "prompts" / "company-research-v2.txt"


OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["short_bio", "long_bio"],
    "properties": {
        "short_bio": {
            "type": "string",
            "description": "60-100 word factual capsule, NPOV. Pure prose, no markdown.",
        },
        "long_bio": {
            "type": "string",
            "description": "Full profile, ~150-800 words depending on evidence depth. Prose with optional sections.",
        },
    },
}


@dataclass
class CompanyBio:
    short_bio: str
    long_bio: str
    model: str
    web_search_count: int


class ResearchError(RuntimeError):
    """Raised when the research call fails or is filtered."""


def _research_client(settings: Settings) -> OpenAI:
    """Build an OpenAI client pointed at Azure's /openai/v1/ Responses surface.

    Uses bearer-token auth instead of a key. The endpoint is the Azure
    resource root (e.g. https://my-resource.openai.azure.com); /openai/v1/
    is appended.
    """
    endpoint = settings.research_endpoint or settings.openai_base_url
    if not endpoint:
        raise ResearchError(
            "Research endpoint not configured. Set JOBBUDDY_RESEARCH_ENDPOINT "
            "or JOBBUDDY_OPENAI_BASE_URL to an Azure OpenAI resource URL."
        )
    base_url = f"{endpoint.rstrip('/')}/openai/v1/"

    from azure.identity import get_bearer_token_provider
    token_provider = get_bearer_token_provider(
        get_azure_credential(), "https://cognitiveservices.azure.com/.default"
    )

    return OpenAI(base_url=base_url, api_key=token_provider)


def _load_prompt() -> str:
    return PROMPT_FILE.read_text()


def research_company(
    name: str,
    *,
    client: OpenAI | None = None,
    settings: Settings | None = None,
) -> CompanyBio:
    """Run the researcher against Azure Responses API. Returns parsed bio.

    Raises ResearchError on content_filter rejection, malformed output, or
    transport failure.

    `client` is injectable for testing. `settings` similarly."""
    s = settings or get_settings()
    c = client or _research_client(s)

    user_msg = f"<name>{name}</name>"
    system_prompt = _load_prompt()

    response = c.responses.create(
        model=s.research_model,
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "company_bio",
                "schema": OUTPUT_SCHEMA,
                "strict": True,
            }
        },
    )

    incomplete = getattr(response, "incomplete_details", None)
    if incomplete and getattr(incomplete, "reason", None) == "content_filter":
        raise ResearchError(f"content_filter on {name}")

    output_text = response.output_text or ""
    if not output_text:
        raise ResearchError(f"empty response for {name}")

    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as e:
        raise ResearchError(f"non-JSON response for {name}: {e}") from e

    short_bio = (parsed.get("short_bio") or "").strip()
    long_bio = (parsed.get("long_bio") or "").strip()
    if not short_bio or not long_bio:
        raise ResearchError(f"missing bio fields for {name}")

    web_search_count = sum(
        1 for item in response.output
        if getattr(item, "type", None) == "web_search_call"
    )

    return CompanyBio(
        short_bio=short_bio,
        long_bio=long_bio,
        model=s.research_model,
        web_search_count=web_search_count,
    )

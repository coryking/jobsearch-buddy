"""Company bio research via Azure Responses API + web_search.

Uses Azure's OpenAI-compatible /openai/v1/ surface (separate from the
chat-completions /openai/{deployment}/ path used by strip/embed) because
the Responses API is required for the `web_search` tool.

Auth: AAD bearer token via DefaultAzureCredential — same managed-identity
path the rest of the project uses for Azure services.

Returns CompanyBio. Callers handle persistence and retry orchestration.
ResearchError subclasses signal whether retrying is worthwhile:
TransientResearchError (raise → WorkerPhase retries) vs
PermanentResearchError (caller should not retry — content_filter, malformed
schema, etc., where the same input → same failure)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from azure.identity import get_bearer_token_provider
from openai import OpenAI

from jobbuddy.settings import Settings, get_azure_credential, get_settings

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
You are researching a company to produce a profile that will be stored in a
job search database. The profile feeds two downstream consumers:

1. An LLM that combines it with individual job descriptions to produce
   richer short_jds. Job seekers search with queries like "AI-native PM jobs
   near Seattle," "startups building developer tools," "chill company that
   ships fast," or "defense contractors working on autonomy." The company
   profile provides context that no individual job description contains —
   what the company is, what domain they're in, what it's like to work
   there.

2. Humans who read it to understand what the system knows about a company.
   If the profile is wrong or thin, they can see that and fix it.

## How to research

Use web search extensively. You are building a picture from external
sources, not from your training data. Your training data can help you
understand what you find, but the profile should be grounded in what you
can verify through search.

Search broadly. Let the question route the search — for well-documented
companies, you'll find a rich evidence base; for smaller companies, work
with what you find. Thoroughness means exhausting available sources, not
forcing depth where evidence is thin.

The company's own job descriptions contain culture signal through
recurring language — but specific open positions are transient, skip those.

## What to capture

Two kinds of signal matter for job search:

**Factual signals.** What the company builds, who their customers are, what
industry they're in, their business model, what technologies they bet on,
how big they are, where they're located, who founded them and why, who they
compete with. Report these as accurately as you can — exact numbers, named
products, specific technologies. "$2.1B revenue" and "Series C, $85M
raised" and "430 employees" are all good. A downstream LLM will handle any
translation needed for the embedding model; your job is accuracy.

**Behavioral and cultural signals.** What it's actually like to work at
this company, as reported by people who do. This is not about whether the
company is "good" or "bad" — it's about describing the organizational
reality in specific, behavioral terms so that a job seeker's query can find
companies whose reality matches what they're looking for.

The kinds of behavioral signals worth capturing when evidence supports
them:

- How decisions get made — top-down, consensus, unclear?
- Pace and intensity — always-on, steady, bureaucratic?
- Process vs. autonomy — heavyweight sign-offs or "just ship it"?
- Communication culture — meetings, async, hallway conversations?
- Stability — how often do priorities, org charts, strategies change?
- Mission authenticity — do employees believe it or is it marketing?
- Technical culture — build vs. buy, over-engineering vs. pragmatism?
- Performance and accountability — how do people get promoted, fired?
- Growth dynamics — hypergrowth, stable, contracting?

These are signals to look for, not a checklist to fill in. If reviews are
full of people talking about decision-making chaos, that's worth capturing.
If nobody mentions it, don't fabricate a finding. Report what the evidence
supports.

**The critical rule: describe, don't judge.** "Decisions get revisited
constantly" is a finding. "Toxic decision-making culture" is a verdict. The
same reality that one person calls "chaotic and disorganized" another calls
"scrappy and fast-moving." Your job is to describe the reality specifically
enough that both of those seekers' queries can find it. The compatibility
judgment belongs to the seeker, not to you.

<examples>
<example title="specificity-and-tone">
Too vague: "The company has a fast-paced, innovative culture."

Better: "The engineering team ships weekly releases. Three reviews from
2024-2025 describe sprint cycles as 'relentless' with one describing it as
'exactly what I wanted after working at a bank.' Priorities shift
frequently — multiple reviews mention quarterly strategy pivots."
</example>

<example title="describe-dont-judge">
Verdict (avoid): "The company has a toxic management culture with poor
work-life balance."

Description (preferred): "Reviews (averaging 3.1/5 over 47 entries)
consistently mention long hours and weekend work. Several describe being
contacted on PTO. Management is described as 'responsive but always on'
and 'expects you to match their pace.' Two reviews from engineering
describe 60+ hour weeks as the norm during product launches."
</example>
</examples>

## Writing standards

Describe the company's reality in specific, behavioral terms. Let job
seekers decide compatibility for themselves.

Write in the register of an industry journalist briefing a colleague —
factual and dated, but unafraid to use the company's, employees', and
press's actual words. Quote primary sources directly when a paraphrase
would lose what the quote conveys, and don't narrate in the source's
voice — quoted-with-context is journalism; the same words as your own
narration is the bio adopting the line as its own.

When the company uses marketing language ("innovative," "industry-leading"),
report what's behind the language — the actual product, the actual
achievement.

If evidence is thin, keep the profile short and state what sources were
available. A two-paragraph profile with clear sourcing is better than a
long one that fills gaps with plausible guesses.

Organize by what you learned, not by source. Attribute claims inline when
the source matters for credibility ("reviews consistently mention..."
rather than a section titled "Reviews Say").

The profile covers durable company characteristics. Omit specific open
positions, job titles, and other transient details. Omit SWOT analyses,
recommendations, and fit assessments — you are a researcher, not an
advisor.

Profile length should reflect evidence depth — a well-documented public
company might warrant 500-800 words, a startup with minimal web presence
might be 150-300 words. Let the evidence drive length rather than
targeting a specific word count.

## Output

Emit a JSON object with two fields:

- `long_bio` — the full profile, natural-language prose. Use whatever
  structure serves the company — sections, flowing narrative, or a mix.
  Begin with a brief factual summary (what the company is, size, stage,
  location) so readers get oriented quickly.

- `short_bio` — a 60-100 word distillation of the long_bio. Pure prose, no
  markdown, no headers. Lead with identity (what they make, who for),
  followed by ownership / maturity / size, then the 1-2 most load-bearing
  facts a job seeker needs to know. Same NPOV stance as long_bio.

Both fields are required. No preamble, no commentary outside the JSON.
"""


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


# 5 minutes covers the slowest observed run (~45s + Bing latency variance + retries).
RESEARCH_TIMEOUT_SECONDS = 300.0


@dataclass
class CompanyBio:
    short_bio: str
    long_bio: str
    model: str
    web_search_count: int


class ResearchError(ValueError):
    """Base class for company-research failures."""


class TransientResearchError(ResearchError):
    """Retry-worthy failure: rate limit, transport timeout, server hiccup."""


class PermanentResearchError(ResearchError):
    """Same input → same failure: content_filter, schema mismatch, missing fields."""


def build_research_client(settings: Settings) -> OpenAI:
    """Build an OpenAI client pointed at Azure's /openai/v1/ Responses surface.

    Uses bearer-token auth via a callable api_key (the openai SDK invokes the
    callable on each request, so the token refreshes automatically). The
    endpoint is the Azure resource root; /openai/v1/ is appended."""
    endpoint = settings.research_endpoint or settings.openai_base_url
    if not endpoint:
        raise PermanentResearchError(
            "Research endpoint not configured. Set JOBBUDDY_RESEARCH_ENDPOINT "
            "or JOBBUDDY_OPENAI_BASE_URL to an Azure OpenAI resource URL."
        )
    base_url = f"{endpoint.rstrip('/')}/openai/v1/"

    token_provider = get_bearer_token_provider(
        get_azure_credential(), "https://cognitiveservices.azure.com/.default"
    )
    return OpenAI(base_url=base_url, api_key=token_provider, timeout=RESEARCH_TIMEOUT_SECONDS)


def research_company(
    name: str,
    *,
    client: OpenAI | None = None,
    settings: Settings | None = None,
) -> CompanyBio:
    """Run the researcher against Azure Responses API. Returns parsed bio.

    Raises TransientResearchError for retry-worthy failures and
    PermanentResearchError otherwise. `client` and `settings` are
    injectable for testing."""
    s = settings or get_settings()
    c = client or build_research_client(s)

    user_msg = f"<name>{name}</name>"

    response = c.responses.create(
        model=s.research_model,
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
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
    reason = getattr(incomplete, "reason", None) if incomplete else None
    if reason == "content_filter":
        raise PermanentResearchError(f"content_filter on {name}")
    if reason:
        raise PermanentResearchError(f"incomplete response for {name}: {reason}")

    output_text = response.output_text or ""
    if not output_text:
        raise PermanentResearchError(f"empty response for {name}")

    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as e:
        raise PermanentResearchError(f"non-JSON response for {name}: {e}") from e

    short_bio = (parsed.get("short_bio") or "").strip()
    long_bio = (parsed.get("long_bio") or "").strip()
    if not short_bio or not long_bio:
        raise PermanentResearchError(f"missing bio fields for {name}")

    output = response.output or []
    web_search_count = sum(
        1 for item in output
        if getattr(item, "type", None) == "web_search_call"
    )

    return CompanyBio(
        short_bio=short_bio,
        long_bio=long_bio,
        model=s.research_model,
        web_search_count=web_search_count,
    )

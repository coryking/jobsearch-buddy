"""find_companies — vibe / kind-of-company search."""

import json
from typing import Annotated

from pydantic import Field

from jobbuddy.mcp_auth import CurrentAccount
from jobbuddy.mcp_tools.app import mcp
from jobbuddy.models import Account


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
def find_companies(
    query: Annotated[str, Field(description=(
        "What kind of company the user is asking about — described in their"
        " own words. Vibe queries handled well: 'companies that ship AI as"
        " product', 'climate-tech with hardware', 'fintech for SMBs'."
        " Exact-name lookups (e.g. 'Stripe', 'Mirabel AI') work but the"
        " result may be weak if the entity isn't a registered company;"
        " check coverage_hint."
    ))],
    limit: Annotated[int, Field(default=20, ge=1, le=100, description=(
        "Max companies to return (default 20, hard cap 100). Each row is"
        " a slug + name + short_bio + active_jobs count, ~140 tokens of"
        " bio per row."
    ))] = 20,
    account: Account = CurrentAccount(),
) -> str:
    """Find registered companies by vibe or theme, returning slugs you can
    pass into `search_jobs(companies=[...])` to scope job search. Use
    BEFORE `search_jobs` when the user describes companies rather than
    naming them — e.g. 'companies that work on internet privacy',
    'AI-as-product startups', 'biotech in the Bay Area'.

    Recommended workflow: call this once, save the returned slugs in your
    conversation or project memory as the user's watch list, then on this
    and future turns scope job search with
    `search_jobs(companies=[<saved slugs>])`. Don't re-run `find_companies`
    every turn — re-run only when the user changes the theme or asks to
    expand the list.

    Each row carries `slug`, `name`, `short_bio`, and `active_jobs` (the
    number of currently-listed jobs at that company). Use `active_jobs` to
    prefer companies that actually have openings when the user is hunting
    for roles. A top-level `coverage_hint` may be set when ranking both
    misses; in that case the named entity is likely not a registered
    company and a web search is the right fallback.

    Trigger phrases: 'companies that do X', 'who builds Y', 'startups
    working on Z', 'AI-first companies', 'climate companies', 'find me
    companies like'. For 'tell me about [exact name]' or 'what does
    [company] do', prefer the ats://companies resource — it's a direct
    lookup, no embedding round-trip.

    Returns short_bio (~140 tokens), not long_bio. The LLM is filtering,
    not reading deeply; use the slug to fetch jobs via search_jobs.

    Each result also carries `applications`: the number of times this
    account has logged `Application` rows against that company in the
    activity log. Useful for prioritization — already-applied companies
    rank differently than fresh ones.
    """
    from jobbuddy.core import find_companies as core_find_companies
    from jobbuddy.store import JobStore

    try:
        result = core_find_companies(query, limit=limit)
    except ValueError as e:
        return f"Error: {e}"

    with JobStore() as store:
        counts = store.application_counts_by_company(account.id)
    for row in result.get("results", []):
        row["applications"] = counts.get((row.get("name") or "").lower(), 0)

    return json.dumps(result, indent=2)

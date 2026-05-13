"""Watchlist CRUD tools — saved-search definitions scoped to one account."""

import json
from typing import Annotated

from pydantic import Field

from jobbuddy.mcp_auth import CurrentAccount
from jobbuddy.mcp_tools.app import mcp
from jobbuddy.mcp_tools.helpers import WATCHLIST_FILTER_DESC, compact_json
from jobbuddy.models import Account


@mcp.tool(annotations={
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
})
def watchlist_create(
    name: Annotated[str, Field(description="Human-readable watchlist name, e.g. 'AI-as-product companies'.")],
    slug: Annotated[str, Field(default="", description="URL-style identifier. Auto-derived from `name` (lowercased, hyphenated) if omitted. Must be unique per account.")] = "",
    filter: Annotated[dict, Field(default={}, description=WATCHLIST_FILTER_DESC)] = {},
    company_slugs: Annotated[list[str], Field(default=[], description="Initial company roster — list of registered slugs (from find_companies or the ats://companies resource).")] = [],
    notes: Annotated[str, Field(default="", description="Free-text notes about why this watchlist exists.")] = "",
    account: Account = CurrentAccount(),
) -> str:
    """Create a watchlist — a saved search definition (companies + filter defaults).

    Watchlists are durable, account-scoped saved searches. Once created,
    pass `watchlist=<slug>` to `search_jobs` to get jobs matching its
    companies + filters. Use this after the user expresses a recurring
    interest ("save these AI companies as my watch list", "I want to
    track climate-tech jobs"). Returns the new watchlist row.
    """
    from jobbuddy.models import slugify
    from jobbuddy.store import JobStore

    final_slug = slug.strip() or slugify(name)
    if not final_slug:
        return "Error: could not derive a slug from name; pass `slug` explicitly."

    try:
        with JobStore() as store:
            row = store.create_watchlist(
                account.id,
                slug=final_slug,
                name=name,
                filter=filter or {},
                notes=notes or None,
                company_slugs=company_slugs or None,
            )
    except ValueError as e:
        return f"Error: {e}"
    return json.dumps(row, indent=2, default=str)


@mcp.tool(annotations={
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
})
def watchlist_update(
    slug: Annotated[str, Field(description="Slug of the watchlist to update.")],
    name: Annotated[str, Field(default="", description="New display name (leave empty to keep current).")] = "",
    notes: Annotated[str, Field(default="", description="New notes (leave empty to keep current).")] = "",
    filter: Annotated[dict | None, Field(default=None, description=WATCHLIST_FILTER_DESC + " Pass null to leave unchanged; pass {} to clear.")] = None,
    add: Annotated[list[str], Field(default=[], description="Company slugs to add to the watchlist.")] = [],
    remove: Annotated[list[str], Field(default=[], description="Company slugs to remove from the watchlist.")] = [],
    account: Account = CurrentAccount(),
) -> str:
    """Update a watchlist's metadata, filter, or company roster.

    Pass only the fields you want to change. `add` / `remove` mutate the
    member roster idempotently — adding a company that's already a member
    is a no-op, as is removing one that isn't.
    """
    from jobbuddy.store import JobStore

    with JobStore() as store:
        row = store.update_watchlist(
            account.id,
            slug,
            name=name or None,
            notes=notes or None,
            filter=filter,
            add=add or None,
            remove=remove or None,
        )
    if row is None:
        return f"Error: watchlist '{slug}' not found for this account."
    return json.dumps(row, indent=2, default=str)


@mcp.tool(annotations={
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": False,
})
def watchlist_delete(
    slug: Annotated[str, Field(description="Slug of the watchlist to delete.")],
    account: Account = CurrentAccount(),
) -> str:
    """Delete a watchlist. The companies and any jobs are untouched; only
    the saved search definition goes away."""
    from jobbuddy.store import JobStore

    with JobStore() as store:
        removed = store.delete_watchlist(account.id, slug)
    if not removed:
        return f"Error: watchlist '{slug}' not found for this account."
    return compact_json({"status": "ok", "deleted": slug})


@mcp.tool(annotations={
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": False,
})
def watchlist_list(
    account: Account = CurrentAccount(),
) -> str:
    """List this account's watchlists with precomputed activity summaries.

    Returns inventory only — no job listings. Each row carries `slug`,
    `name`, `notes`, `filter`, `companies`, and a `counts` block with
    `total_active`, `posted_1d`, `posted_7d`, `posted_30d`, and `applied`
    (rows the user has already logged Application activity against any
    company in the watchlist).

    Use as a navigation aid: pick a watchlist by which one has fresh
    activity (`posted_1d` / `posted_7d`), then call
    `search_jobs(watchlist=<slug>, posted_since=...)` for the actual
    listings.

    Takes no parameters — time windows belong on `search_jobs`, not on
    this metadata endpoint."""
    from jobbuddy.store import JobStore

    with JobStore() as store:
        rows = store.list_watchlists(account.id)
    return json.dumps(rows, indent=2, default=str)

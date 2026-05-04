"""Company registry — maps slugs to ATS config (PostgreSQL-backed)."""

import re

from jobbuddy.models import Company, slugify


def _store():
    from jobbuddy.store import JobStore
    return JobStore()


def load_registry(*, store=None) -> dict[str, Company]:
    """Load all companies. Returns {slug: Company}."""
    if store is not None:
        return store.load_companies()
    with _store() as s:
        return s.load_companies()


def save_registry(companies: dict[str, Company]) -> None:
    """No-op. Kept for API compatibility."""
    pass


def lookup_by_slug(slug: str) -> Company | None:
    """Look up a company by slug. Normalizes input slug before lookup."""
    registry = load_registry()
    return registry.get(slugify(slug))


def _normalize(s: str) -> str:
    """Strip to lowercase alphanumerics for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def lookup_by_board(board: str, ats: str | None = None) -> Company | None:
    """Look up a company by its board value (e.g., 'eeho' for Oracle HCM).

    When the slug and board differ (slug='oracle', board='eeho'), lookup_by_slug
    won't find it. This searches the board field directly. Optionally filter by ATS type."""
    registry = load_registry()
    for company in registry.values():
        if company.board == board and (ats is None or company.ats == ats):
            return company
    return None


def lookup_by_name(name: str) -> Company | None:
    """Look up a company by display name, slug, board, or fuzzy match. Returns Company or None.

    Tries exact match on name/slug first, then falls back to normalized matching
    so 'open ai', 'Open AI', 'openai' all resolve to the same company. Finally
    falls back to board match (case-insensitive) so an ATS board identifier
    extracted from a URL (e.g. 'thealleninstitute' for the Greenhouse board
    backing the 'ai2' slug) resolves to the registered company."""
    registry = load_registry()
    name_lower = name.lower()

    # Exact match on display name or slug
    for company in registry.values():
        if company.name.lower() == name_lower or company.slug == name_lower:
            return company

    # Fuzzy match: strip non-alphanumeric, compare
    name_norm = _normalize(name)
    if name_norm:
        for company in registry.values():
            if _normalize(company.name) == name_norm or _normalize(company.slug) == name_norm:
                return company

    # Board match: URL-derived board identifiers (board != slug)
    for company in registry.values():
        if company.board and company.board.lower() == name_lower:
            return company

    return None


def register_company(name: str, ats: str | None = None, board: str | None = None, **extra) -> Company:
    """Register a new company. Returns the Company.

    Slug is derived from board (if provided) or name, then normalized by Company's validator.
    When ats is None, the company is registered as a directory entry without
    job board scraping support (used for activity log tracking)."""
    slug_input = board or name
    company = Company(slug=slug_input, name=name, ats=ats, board=board, **extra)
    with _store() as s:
        s.save_company(company)
    return company


def ensure_company(name: str) -> Company:
    """Look up a company by name; register with ats=None if not found.

    Returns Company. Used by activity log tools to backfill the
    company registry from CSV data without requiring ATS board config."""
    result = lookup_by_name(name)
    if result:
        return result
    return register_company(name)


def list_companies() -> dict[str, Company]:
    """Return all registered companies."""
    return load_registry()

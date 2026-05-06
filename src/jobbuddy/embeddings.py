"""Minimal embedding helper for the company-bio pipeline.

One synchronous call, no batching, no caching, no rate-limit pacing — the
caller is ResearchPhase's ThreadPoolExecutor (parallelism handled there)
or a one-shot query at search time. ~700 companies and one query embedding
per `find_companies` call don't justify the old job-side scaffolding.

Returns the vector as the pgvector text literal `'[v1,v2,...]'` so callers
can write directly via `%s::vector` without a pgvector python adapter.
"""

from __future__ import annotations

from openai import OpenAI

from jobbuddy.openai_client import create_openai_client
from jobbuddy.settings import get_settings

DIMENSIONS = 1536

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = create_openai_client()
    return _client


def embed_text(text: str) -> tuple[str, int]:
    """Embed one string. Returns (pgvector_literal, total_tokens).

    The literal is `'[v1,v2,...]'`-formatted for direct use in SQL via
    `%s::vector`. Tokens are surfaced so the caller can update display
    counters; query-time callers can ignore them.

    Validates the returned vector matches `DIMENSIONS` (the schema's
    `vector(1536)` constraint). A model swap to a different-dim model
    surfaces here, before the row hits a runtime SQL dimension-mismatch.
    """
    client = _get_client()
    settings = get_settings()
    response = client.embeddings.create(input=text, model=settings.embedding_model)
    vector = response.data[0].embedding
    if len(vector) != DIMENSIONS:
        raise ValueError(
            f"embedding_model={settings.embedding_model!r} returned "
            f"{len(vector)} dims; schema expects {DIMENSIONS}. "
            f"Either switch back to a {DIMENSIONS}-dim model or migrate "
            f"the schema to match."
        )
    total_tokens = response.usage.total_tokens if response.usage else 0
    return vector_literal(vector), total_tokens


def vector_literal(vector: list[float]) -> str:
    """Format a float list as a pgvector text literal: `[v1,v2,v3]`."""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"

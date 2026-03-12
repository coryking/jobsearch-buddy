"""OpenAI embeddings via text-embedding-3-small (or configurable model).

Single model (1536 dims). No registry, no local inference.
Batch embedding for sync, single-call embedding for search queries.

Single worker with a post-call delay for rate limit pacing.
Pacing uses x-ratelimit-remaining-tokens response headers (tested on Azure OpenAI).
If your provider doesn't return these headers, no pacing occurs.
"""

from __future__ import annotations

import logging
import struct
import time

import numpy as np
from openai import OpenAI, RateLimitError

from jobbuddy.openai_client import create_openai_client
from jobbuddy.settings import get_settings

log = logging.getLogger(__name__)

# Model constants
MODEL_KEY = "text3small"
DIMENSIONS = 1536
MAX_BATCH_SIZE = 2048  # OpenAI API limit per request

# Singleton client — single worker, no per-thread storage needed.
_client: OpenAI | None = None

# Cumulative token counter for throughput calculation
_cumulative_tokens = 0
_first_call_time: float | None = None


def _get_client() -> OpenAI:
    """Return a cached OpenAI client. Created once per process.

    Raises ValueError if API credentials not configured.
    """
    global _client
    if _client is None:
        _client = create_openai_client(max_retries=0)
    return _client


def embed_texts(texts: list[str]) -> tuple[list[list[float]], int]:
    """Embed a batch of texts in a single API call.

    Caller is responsible for chunking into batches <= MAX_BATCH_SIZE.
    Raises openai.APIError on failure.

    Returns (vectors, total_tokens).
    """
    if not texts:
        return [], 0
    if len(texts) > MAX_BATCH_SIZE:
        raise ValueError(f"Batch too large: {len(texts)} > {MAX_BATCH_SIZE}")
    client = _get_client()
    t0 = time.monotonic()
    try:
        raw = client.embeddings.with_raw_response.create(input=texts, model=get_settings().embedding_model)
    except RateLimitError as e:
        retry_after = int(e.response.headers.get("retry-after", "5"))
        log.warning("Rate limited (%d texts). Retrying in %ds.", len(texts), retry_after)
        time.sleep(retry_after)
        t0 = time.monotonic()
        raw = client.embeddings.with_raw_response.create(input=texts, model=get_settings().embedding_model)
        log.info("Retry succeeded (%d texts)", len(texts))
    call_duration = time.monotonic() - t0

    response = raw.parse()
    total_tokens = response.usage.total_tokens if response.usage else 0

    global _cumulative_tokens, _first_call_time
    if _first_call_time is None:
        log.debug("Rate limit: %s TPM capacity",
                  raw.headers.get("x-ratelimit-limit-tokens", "?"))
    _cumulative_tokens += total_tokens
    now = time.monotonic()
    if _first_call_time is None:
        _first_call_time = now
    elapsed_min = (now - _first_call_time) / 60
    avg_tpm = _cumulative_tokens / elapsed_min if elapsed_min > 0.1 else 0

    # Pace based on remaining budget. Refill rate: 1M tokens / 60s = 16,667 tok/s.
    remaining = raw.headers.get("x-ratelimit-remaining-tokens")
    remaining_tokens = int(remaining) if remaining is not None else None
    if remaining_tokens is not None:
        refill_rate = 16_667
        base_wait = total_tokens / refill_rate
        target = 100_000
        extra = max(0, (target - remaining_tokens) / refill_rate) if remaining_tokens < target else 0
        wait = base_wait + extra
        log.debug("%d items, %dk tok | budget: %dk remaining, call %.1fs, sleep %.1fs | total: %.1fM tok, avg: %.0fk TPM",
                  len(texts), total_tokens // 1000,
                  remaining_tokens // 1000, call_duration, wait,
                  _cumulative_tokens / 1_000_000, avg_tpm / 1000)
        if wait > 0:
            time.sleep(wait)

    return [item.embedding for item in response.data], total_tokens


def embed_query(text: str) -> list[float]:
    """Embed a single search query. ~200ms API call."""
    client = _get_client()
    response = client.embeddings.create(input=[text], model=get_settings().embedding_model)
    return response.data[0].embedding


def compute_batch_size(avg_tokens: int = 1438, target_tokens: int = 62_500) -> int:
    """Batch size targeting ~6.25% of 1M TPM per request (~43 items).

    Returns min(target_tokens // avg_tokens, MAX_BATCH_SIZE).
    With defaults: min(43, 2048) = 43.
    """
    return min(target_tokens // avg_tokens, MAX_BATCH_SIZE)


def serialize_f32(vector: list[float]) -> list[float]:
    """Pass-through for pgvector (accepts lists directly)."""
    return vector


def deserialize_f32(blob: bytes) -> np.ndarray:
    """Convert BLOB bytes back to numpy array."""
    return np.frombuffer(blob, dtype=np.float32).copy()

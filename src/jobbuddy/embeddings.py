"""Azure OpenAI embeddings via text-embedding-3-small.

Single model (1536 dims). No registry, no local inference.
Batch embedding for sync, single-call embedding for search queries.

Rate limiting: a shared TokenBucket gates all embed calls to stay under
the Azure deployment's TPM limit. Workers block on consume() instead of
blasting the API and eating 429 retries.
"""

from __future__ import annotations

import logging
import random
import struct
import threading
import time

import numpy as np
from openai import AzureOpenAI

from jobbuddy.settings import get_settings

log = logging.getLogger(__name__)

# Model constants
MODEL_KEY = "text3small"
DEPLOYMENT_NAME = "text-embedding-3-small"
DIMENSIONS = 1536
MAX_BATCH_SIZE = 2048  # Azure API limit per request
AVG_TOKENS_PER_ITEM = 1438  # measured from production data

# Azure deployment limit: 1M tokens per 60s = ~16.7K tokens/s.
TOKEN_CAPACITY = 1_000_000
TOKEN_REFILL_PER_SEC = int(TOKEN_CAPACITY / 60)  # ~16.7K tok/s

# Per-thread client storage — each worker gets its own client with a
# randomized timeout to avoid thundering herd on 429 retries.
_thread_local = threading.local()


class TokenBucket:
    """Thread-safe token bucket for rate limiting API calls.

    Workers call consume(n) before each API call. If the bucket doesn't
    have enough tokens, the worker sleeps until refill catches up.
    """

    def __init__(self, capacity: int, refill_per_sec: int,
                 initial: int | None = None):
        self.capacity = capacity
        self.tokens = float(initial if initial is not None else capacity)
        self.refill_per_sec = refill_per_sec
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
        self.last_refill = now

    def consume(self, n: int) -> None:
        """Block until enough tokens are available, then deduct them.

        If n > capacity, waits until the bucket is full and drains it.
        The caller will overdraw, but that's fine — subsequent calls
        will wait for the refill to catch up.
        """
        target = min(n, self.capacity)
        while True:
            with self.lock:
                self._refill()
                if self.tokens >= target:
                    self.tokens -= n  # may go negative, that's intentional
                    return
                deficit = target - self.tokens
                wait = deficit / self.refill_per_sec
            time.sleep(min(wait + random.uniform(0, 0.5), 2.0))


# Shared rate limiter. Starts with enough for one batch so pacing kicks
# in immediately instead of letting all workers burst at once.
_rate_limiter = TokenBucket(TOKEN_CAPACITY, TOKEN_REFILL_PER_SEC,
                            initial=70_000)


def _get_client() -> AzureOpenAI:
    """Return a per-thread AzureOpenAI client (created on first call).

    Raises ValueError if Azure credentials not configured.
    """
    client = getattr(_thread_local, "client", None)
    if client is None:
        s = get_settings()
        if not s.azure_openai_api_key or not s.azure_openai_endpoint:
            raise ValueError(
                "Azure OpenAI not configured. Set JOBBUDDY_AZURE_OPENAI_API_KEY "
                "and JOBBUDDY_AZURE_OPENAI_ENDPOINT."
            )
        timeout = random.uniform(1.0, 5.0)
        client = AzureOpenAI(
            api_key=s.azure_openai_api_key,
            azure_endpoint=s.azure_openai_endpoint,
            api_version=s.azure_openai_api_version,
            timeout=timeout,
            max_retries=0,
        )
        _thread_local.client = client
    return client


def embed_texts(texts: list[str]) -> tuple[list[list[float]], int]:
    """Embed a batch of texts in a single API call.

    Blocks on the shared token bucket before calling the API to stay
    under the deployment's TPM limit. Caller is responsible for chunking
    into batches <= MAX_BATCH_SIZE.

    Returns (vectors, total_tokens).
    """
    if not texts:
        return [], 0
    if len(texts) > MAX_BATCH_SIZE:
        raise ValueError(f"Batch too large: {len(texts)} > {MAX_BATCH_SIZE}")

    estimated_tokens = len(texts) * AVG_TOKENS_PER_ITEM
    _rate_limiter.consume(estimated_tokens)

    client = _get_client()
    response = client.embeddings.create(input=texts, model=DEPLOYMENT_NAME)
    total_tokens = response.usage.total_tokens if response.usage else 0
    return [item.embedding for item in response.data], total_tokens


def embed_query(text: str) -> list[float]:
    """Embed a single search query. ~200ms API call."""
    client = _get_client()
    response = client.embeddings.create(input=[text], model=DEPLOYMENT_NAME)
    return response.data[0].embedding


def compute_batch_size(avg_tokens: int = 1438, target_tokens: int = 70_000) -> int:
    """Optimal batch size targeting ~50 items per API call.

    Bench testing showed batch=50 (~70K tokens) has the best items/s throughput.
    Larger batches (100+) hit diminishing returns on API latency.

    Returns min(target_tokens // avg_tokens, MAX_BATCH_SIZE).
    With defaults: min(48, 2048) = 48.
    """
    return min(target_tokens // avg_tokens, MAX_BATCH_SIZE)


def serialize_f32(vector: list[float]) -> bytes:
    """Convert float list to little-endian bytes for BLOB storage."""
    return struct.pack(f"<{len(vector)}f", *vector)


def deserialize_f32(blob: bytes) -> np.ndarray:
    """Convert BLOB bytes back to numpy array."""
    return np.frombuffer(blob, dtype=np.float32).copy()

"""Azure OpenAI embeddings via text-embedding-3-small.

Single model (1536 dims). No registry, no local inference.
Batch embedding for sync, single-call embedding for search queries.
"""

from __future__ import annotations

import logging
import struct

import numpy as np
from openai import AzureOpenAI

from jobbuddy.settings import get_settings

log = logging.getLogger(__name__)

# Model constants
MODEL_KEY = "text3small"
DEPLOYMENT_NAME = "text-embedding-3-small"
DIMENSIONS = 1536
MAX_BATCH_SIZE = 2048  # Azure API limit per request

# Singleton client
_client: AzureOpenAI | None = None


def _get_client() -> AzureOpenAI:
    """Return a cached AzureOpenAI client. Created once per process.

    Raises ValueError if Azure credentials not configured.
    """
    global _client
    if _client is None:
        s = get_settings()
        if not s.azure_openai_api_key or not s.azure_openai_endpoint:
            raise ValueError(
                "Azure OpenAI not configured. Set JOBBUDDY_AZURE_OPENAI_API_KEY "
                "and JOBBUDDY_AZURE_OPENAI_ENDPOINT."
            )
        _client = AzureOpenAI(
            api_key=s.azure_openai_api_key,
            azure_endpoint=s.azure_openai_endpoint,
            api_version=s.azure_openai_api_version,
        )
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
    response = client.embeddings.create(input=texts, model=DEPLOYMENT_NAME)
    total_tokens = response.usage.total_tokens if response.usage else 0
    return [item.embedding for item in response.data], total_tokens


def embed_query(text: str) -> list[float]:
    """Embed a single search query. ~200ms API call."""
    client = _get_client()
    response = client.embeddings.create(input=[text], model=DEPLOYMENT_NAME)
    return response.data[0].embedding


def compute_batch_size(avg_tokens: int = 1438, target_tokens: int = 250_000) -> int:
    """Optimal batch size targeting ~25% of 1M TPM per batch.

    Returns min(target_tokens // avg_tokens, MAX_BATCH_SIZE).
    With defaults: min(173, 2048) = 173.
    """
    return min(target_tokens // avg_tokens, MAX_BATCH_SIZE)


def serialize_f32(vector: list[float]) -> bytes:
    """Convert float list to little-endian bytes for BLOB storage."""
    return struct.pack(f"<{len(vector)}f", *vector)


def deserialize_f32(blob: bytes) -> np.ndarray:
    """Convert BLOB bytes back to numpy array."""
    return np.frombuffer(blob, dtype=np.float32).copy()

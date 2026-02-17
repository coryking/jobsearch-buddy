"""Local embedding generation via fastembed (BGE small).

Lazy-loaded model singleton — first call downloads ~130MB model, cached after that.
"""

import struct
from collections.abc import Generator
from functools import lru_cache

from fastembed import TextEmbedding

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384

# fastembed's internal batch size — we use this to yield control back to Python
# between batches so Ctrl+C works and progress can update.
BATCH_SIZE = 32


@lru_cache(maxsize=1)
def get_model() -> TextEmbedding:
    """Load the embedding model (singleton, lazy)."""
    return TextEmbedding(model_name=MODEL_NAME)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch embed documents for storage. Uses passage prefix for asymmetric retrieval."""
    model = get_model()
    return [vec.tolist() for vec in model.passage_embed(texts, batch_size=BATCH_SIZE)]


def embed_texts_iter(texts: list[str]) -> Generator[list[float], None, None]:
    """Yield embeddings one at a time. Allows progress tracking and Ctrl+C between batches."""
    model = get_model()
    for vec in model.passage_embed(texts, batch_size=BATCH_SIZE):
        yield vec.tolist()


def embed_query(text: str) -> list[float]:
    """Embed a single search query. Uses query prefix for asymmetric retrieval."""
    model = get_model()
    results = list(model.query_embed([text]))
    return results[0].tolist()


def serialize_f32(vector: list[float]) -> bytes:
    """Convert float list to little-endian bytes for sqlite-vec."""
    return struct.pack(f"<{len(vector)}f", *vector)

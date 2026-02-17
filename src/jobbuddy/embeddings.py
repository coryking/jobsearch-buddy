"""Multi-model embedding generation via sentence-transformers.

Model registry + lazy-loaded models. Adding a new model = one dataclass entry.
Models are cached in a dict so each is loaded at most once per process.
"""

import struct
from collections.abc import Generator
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class EmbeddingModelConfig:
    """Configuration for an embedding model."""

    model_key: str
    model_name: str  # HuggingFace model ID
    dimensions: int
    query_prefix: str = ""
    passage_prefix: str = ""
    trust_remote_code: bool = False


MODEL_REGISTRY: dict[str, EmbeddingModelConfig] = {
    "jobbge_m3": EmbeddingModelConfig(
        model_key="jobbge_m3",
        model_name="pj-mathematician/JobBGE-m3",
        dimensions=1024,
    ),
    "nomic_v15": EmbeddingModelConfig(
        model_key="nomic_v15",
        model_name="nomic-ai/nomic-embed-text-v1.5",
        dimensions=768,
        query_prefix="search_query: ",
        passage_prefix="search_document: ",
        trust_remote_code=True,
    ),
    "bge_small": EmbeddingModelConfig(
        model_key="bge_small",
        model_name="BAAI/bge-small-en-v1.5",
        dimensions=384,
    ),
}

DEFAULT_MODEL_KEY = "bge_small"

# Backward compat — referenced by old code during migration
DIMENSIONS = MODEL_REGISTRY[DEFAULT_MODEL_KEY].dimensions
MODEL_NAME = MODEL_REGISTRY[DEFAULT_MODEL_KEY].model_name

_models: dict[str, "SentenceTransformer"] = {}


def get_config(model_key: str) -> EmbeddingModelConfig:
    """Get model config by key. Raises KeyError for unknown keys."""
    return MODEL_REGISTRY[model_key]


def list_models() -> list[EmbeddingModelConfig]:
    """Return all registered model configs."""
    return list(MODEL_REGISTRY.values())


def get_model(model_key: str = DEFAULT_MODEL_KEY) -> "SentenceTransformer":
    """Load an embedding model (cached per model_key, lazy)."""
    if model_key not in _models:
        from sentence_transformers import SentenceTransformer

        config = get_config(model_key)
        _models[model_key] = SentenceTransformer(
            config.model_name,
            trust_remote_code=config.trust_remote_code,
        )
    return _models[model_key]


def embed_texts(texts: list[str], model_key: str = DEFAULT_MODEL_KEY) -> list[list[float]]:
    """Batch embed documents for storage. Prepends passage prefix if configured."""
    config = get_config(model_key)
    model = get_model(model_key)
    if config.passage_prefix:
        texts = [config.passage_prefix + t for t in texts]
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def embed_texts_iter(
    texts: list[str], model_key: str = DEFAULT_MODEL_KEY, batch_size: int = 32
) -> Generator[list[float], None, None]:
    """Yield embeddings one at a time. Allows progress tracking and Ctrl+C between batches."""
    config = get_config(model_key)
    model = get_model(model_key)
    if config.passage_prefix:
        texts = [config.passage_prefix + t for t in texts]
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        vecs = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
        for v in vecs:
            yield v.tolist()


def embed_query(text: str, model_key: str = DEFAULT_MODEL_KEY) -> list[float]:
    """Embed a single search query. Prepends query prefix if configured."""
    config = get_config(model_key)
    model = get_model(model_key)
    if config.query_prefix:
        text = config.query_prefix + text
    vec = model.encode([text], normalize_embeddings=True, show_progress_bar=False)
    return vec[0].tolist()


def serialize_f32(vector: list[float]) -> bytes:
    """Convert float list to little-endian bytes for BLOB storage."""
    return struct.pack(f"<{len(vector)}f", *vector)


def deserialize_f32(blob: bytes, dimensions: int) -> np.ndarray:
    """Convert BLOB bytes back to numpy array."""
    return np.frombuffer(blob, dtype=np.float32).copy()

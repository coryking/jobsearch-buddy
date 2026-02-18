"""Multi-model embedding generation via fastembed (ONNX Runtime).

Model registry + lazy-loaded models. Adding a new model = one dataclass entry.
Models are cached in a dict so each is loaded at most once per process.
"""

import gc
import logging
import struct
from collections.abc import Generator
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddingModelConfig:
    """Configuration for an embedding model."""

    model_key: str
    model_name: str  # HuggingFace model ID
    dimensions: int
    batch_size: int = 32


MODEL_REGISTRY: dict[str, EmbeddingModelConfig] = {
    "nomic_v15": EmbeddingModelConfig(
        model_key="nomic_v15",
        model_name="nomic-ai/nomic-embed-text-v1.5-Q",
        dimensions=768,
        batch_size=4,
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

_models: dict[str, "TextEmbedding"] = {}


def get_config(model_key: str) -> EmbeddingModelConfig:
    """Get model config by key. Raises KeyError for unknown keys."""
    return MODEL_REGISTRY[model_key]


def list_models() -> list[EmbeddingModelConfig]:
    """Return all registered model configs."""
    return list(MODEL_REGISTRY.values())


def unload_models() -> None:
    """Unload all cached models and free memory.

    Called between model runs in the embed phase so only one model
    occupies memory at a time.
    """
    if not _models:
        return
    keys = list(_models.keys())
    log.info("Unloading models: %s", ", ".join(keys))
    _models.clear()
    gc.collect()


def get_model(model_key: str = DEFAULT_MODEL_KEY) -> "TextEmbedding":
    """Load an embedding model (cached per model_key, lazy)."""
    if model_key not in _models:
        from fastembed import TextEmbedding

        config = get_config(model_key)
        log.info("Loading model %s (%s, %dd) via ONNX", model_key, config.model_name, config.dimensions)
        _models[model_key] = TextEmbedding(model_name=config.model_name)
        log.info("Model %s ready (onnx)", model_key)
    return _models[model_key]


def embed_texts(texts: list[str], model_key: str = DEFAULT_MODEL_KEY) -> list[list[float]]:
    """Batch embed documents for storage. fastembed handles prefixes internally."""
    model = get_model(model_key)
    vecs = list(model.passage_embed(texts))
    return [v.tolist() for v in vecs]


def embed_texts_iter(
    texts: list[str], model_key: str = DEFAULT_MODEL_KEY, batch_size: int | None = None
) -> Generator[list[float], None, None]:
    """Yield embeddings one at a time. Allows progress tracking and Ctrl+C between batches."""
    config = get_config(model_key)
    model = get_model(model_key)
    if batch_size is None:
        batch_size = config.batch_size
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        for v in model.passage_embed(batch):
            yield v.tolist()


def embed_query(text: str, model_key: str = DEFAULT_MODEL_KEY) -> list[float]:
    """Embed a single search query. fastembed handles query prefix internally."""
    model = get_model(model_key)
    vecs = list(model.query_embed(text))
    return vecs[0].tolist()


def serialize_f32(vector: list[float]) -> bytes:
    """Convert float list to little-endian bytes for BLOB storage."""
    return struct.pack(f"<{len(vector)}f", *vector)


def deserialize_f32(blob: bytes, dimensions: int) -> np.ndarray:
    """Convert BLOB bytes back to numpy array."""
    return np.frombuffer(blob, dtype=np.float32).copy()

"""Tests for jobbuddy.embeddings — model registry and embedding functions."""

import struct
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from jobbuddy.embeddings import (
    DEFAULT_MODEL_KEY,
    MODEL_REGISTRY,
    EmbeddingModelConfig,
    deserialize_f32,
    embed_query,
    embed_texts,
    embed_texts_iter,
    get_config,
    list_models,
    serialize_f32,
)
from jobbuddy.models import Job


def _make_job(**kw) -> Job:
    defaults = dict(
        id="123",
        title="Product Manager",
        location="Seattle, WA",
        url="https://example.com/jobs/123",
        apply_url="https://example.com/jobs/123/apply",
    )
    defaults.update(kw)
    return Job(**defaults)


# ---------------------------------------------------------------------------
# Model Registry
# ---------------------------------------------------------------------------


class TestModelRegistry:
    def test_registry_has_two_models(self):
        assert len(MODEL_REGISTRY) == 2

    def test_default_model_exists(self):
        assert DEFAULT_MODEL_KEY in MODEL_REGISTRY

    def test_get_config_returns_correct_model(self):
        config = get_config("bge_small")
        assert config.model_name == "BAAI/bge-small-en-v1.5"
        assert config.dimensions == 384

    def test_get_config_unknown_raises(self):
        with pytest.raises(KeyError):
            get_config("nonexistent_model")

    def test_list_models_returns_all(self):
        models = list_models()
        assert len(models) == 2
        keys = {m.model_key for m in models}
        assert keys == {"nomic_v15", "bge_small"}

    def test_nomic_has_prefixes(self):
        config = get_config("nomic_v15")
        assert config.query_prefix == "search_query: "
        assert config.passage_prefix == "search_document: "

    def test_bge_small_has_no_prefixes(self):
        config = get_config("bge_small")
        assert config.query_prefix == ""
        assert config.passage_prefix == ""


# ---------------------------------------------------------------------------
# Job.embed_text()
# ---------------------------------------------------------------------------


class TestEmbedText:
    def test_returns_none_without_description(self):
        job = _make_job(description=None)
        assert job.embed_text("Acme Corp") is None

    def test_returns_none_for_empty_description(self):
        job = _make_job(description="")
        assert job.embed_text("Acme Corp") is None

    def test_includes_company_and_title(self):
        job = _make_job(description="Build cool things.")
        text = job.embed_text("Acme Corp")
        assert text.startswith("Acme Corp — Product Manager")

    def test_includes_department_and_location(self):
        job = _make_job(description="Build cool things.", department="Engineering", location="Seattle, WA")
        text = job.embed_text("Acme Corp")
        assert "Engineering, Seattle, WA" in text

    def test_includes_description(self):
        job = _make_job(description="Build cool things.")
        text = job.embed_text("Acme Corp")
        assert text.endswith("Build cool things.")

    def test_omits_meta_when_no_department_and_empty_location(self):
        job = _make_job(description="Build cool things.", department=None, location="")
        text = job.embed_text("Acme Corp")
        lines = text.split("\n")
        assert lines[0] == "Acme Corp — Product Manager"
        assert lines[1] == ""
        assert lines[2] == "Build cool things."


# ---------------------------------------------------------------------------
# serialize_f32 / deserialize_f32
# ---------------------------------------------------------------------------


class TestSerializeF32:
    def test_round_trip(self):
        vec = [1.0, 2.0, 3.0]
        blob = serialize_f32(vec)
        assert len(blob) == 12  # 3 floats * 4 bytes
        unpacked = list(struct.unpack("<3f", blob))
        assert unpacked == vec

    def test_correct_dimensions(self):
        config = get_config("bge_small")
        vec = [0.0] * config.dimensions
        blob = serialize_f32(vec)
        assert len(blob) == config.dimensions * 4

    def test_deserialize_round_trip(self):
        vec = [1.0, 2.5, -3.0, 0.0]
        blob = serialize_f32(vec)
        arr = deserialize_f32(blob, len(vec))
        np.testing.assert_array_almost_equal(arr, vec)

    def test_deserialize_returns_writable_copy(self):
        """Deserialized array should be writable (not a read-only view)."""
        blob = serialize_f32([1.0, 2.0])
        arr = deserialize_f32(blob, 2)
        arr[0] = 99.0  # should not raise


# ---------------------------------------------------------------------------
# embed_texts / embed_query (mocked model)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_model():
    """Patch get_model to return a mock SentenceTransformer."""
    model = MagicMock()

    def fake_encode(texts, **kwargs):
        # Return deterministic vectors based on text index
        return np.array([
            np.random.RandomState(i).randn(384).astype(np.float32)
            for i, _ in enumerate(texts)
        ])

    model.encode = fake_encode

    with patch("jobbuddy.embeddings.get_model", return_value=model):
        yield model


class TestEmbedFunctions:
    def test_embed_texts_returns_list_of_vectors(self, mock_model):
        result = embed_texts(["hello world", "test doc"])
        assert len(result) == 2
        assert len(result[0]) == 384
        assert isinstance(result[0][0], float)

    def test_embed_query_returns_single_vector(self, mock_model):
        result = embed_query("search query")
        assert len(result) == 384
        assert isinstance(result[0], float)

    def test_embed_texts_iter_yields_all(self, mock_model):
        texts = ["a", "b", "c"]
        results = list(embed_texts_iter(texts))
        assert len(results) == 3
        assert all(len(v) == 384 for v in results)

    def test_embed_texts_with_prefix(self, mock_model):
        """nomic model should prepend passage prefix."""
        calls = []
        original_encode = mock_model.encode

        def capture_encode(texts, **kwargs):
            calls.append(list(texts))
            return original_encode(texts, **kwargs)

        mock_model.encode = capture_encode
        embed_texts(["test doc"], model_key="nomic_v15")
        assert calls[0][0] == "search_document: test doc"

    def test_embed_query_with_prefix(self, mock_model):
        """nomic model should prepend query prefix."""
        calls = []
        original_encode = mock_model.encode

        def capture_encode(texts, **kwargs):
            calls.append(list(texts))
            return original_encode(texts, **kwargs)

        mock_model.encode = capture_encode
        embed_query("test query", model_key="nomic_v15")
        assert calls[0][0] == "search_query: test query"

"""Tests for jobbuddy.embeddings — OpenAI embedding wrapper."""

import struct
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import jobbuddy.embeddings as emb
from jobbuddy.embeddings import (
    DIMENSIONS,
    MAX_BATCH_SIZE,
    compute_batch_size,
    deserialize_f32,
    embed_query,
    embed_texts,
    serialize_f32,
)

from conftest import make_job


def _mock_response(n: int = 1, dim: int = DIMENSIONS):
    """Build a fake OpenAI embeddings response with n items."""
    data = []
    for i in range(n):
        item = SimpleNamespace(
            embedding=list(np.random.RandomState(i).randn(dim).astype(float)),
            index=i,
        )
        data.append(item)
    return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=n * 100))


@pytest.fixture(autouse=True)
def _reset_client():
    """Reset the singleton client before each test."""
    emb._client = None
    yield
    emb._client = None


@pytest.fixture
def mock_client():
    """Provide a mock OpenAI client, patching _get_client."""
    client = MagicMock()
    with patch("jobbuddy.embeddings._get_client", return_value=client):
        yield client


@pytest.fixture
def mock_settings():
    """Provide mock settings with embedding_model configured."""
    settings = MagicMock()
    settings.embedding_model = "text-embedding-3-small"
    with patch("jobbuddy.embeddings.get_settings", return_value=settings):
        yield settings


# ---------------------------------------------------------------------------
# _get_client
# ---------------------------------------------------------------------------


class TestGetClient:
    def test_delegates_to_factory(self):
        mock_factory_client = MagicMock()
        with patch("jobbuddy.embeddings.create_openai_client", return_value=mock_factory_client) as mock_factory:
            client = emb._get_client()
            mock_factory.assert_called_once_with(max_retries=0)
            assert client is mock_factory_client

    def test_caches_client(self):
        mock_factory_client = MagicMock()
        with patch("jobbuddy.embeddings.create_openai_client", return_value=mock_factory_client) as mock_factory:
            c1 = emb._get_client()
            c2 = emb._get_client()
            assert c1 is c2
            mock_factory.assert_called_once()


# ---------------------------------------------------------------------------
# embed_texts
# ---------------------------------------------------------------------------


class TestEmbedTexts:
    def test_empty_list_returns_empty(self, mock_client):
        vectors, tokens = embed_texts([])
        assert vectors == []
        assert tokens == 0
        mock_client.embeddings.create.assert_not_called()

    def test_returns_embeddings(self, mock_client, mock_settings):
        mock_raw = MagicMock()
        mock_raw.parse.return_value = _mock_response(2)
        mock_raw.headers = {}
        mock_client.embeddings.with_raw_response.create.return_value = mock_raw
        vectors, tokens = embed_texts(["hello", "world"])
        assert len(vectors) == 2
        assert len(vectors[0]) == DIMENSIONS
        assert tokens == 200
        mock_client.embeddings.with_raw_response.create.assert_called_once_with(
            input=["hello", "world"], model="text-embedding-3-small"
        )

    def test_batch_too_large_raises(self, mock_client):
        texts = ["x"] * (MAX_BATCH_SIZE + 1)
        with pytest.raises(ValueError, match="Batch too large"):
            embed_texts(texts)

    def test_max_batch_size_allowed(self, mock_client, mock_settings):
        mock_raw = MagicMock()
        mock_raw.parse.return_value = _mock_response(MAX_BATCH_SIZE)
        mock_raw.headers = {}
        mock_client.embeddings.with_raw_response.create.return_value = mock_raw
        texts = ["x"] * MAX_BATCH_SIZE
        vectors, _ = embed_texts(texts)
        assert len(vectors) == MAX_BATCH_SIZE


# ---------------------------------------------------------------------------
# embed_query
# ---------------------------------------------------------------------------


class TestEmbedQuery:
    def test_returns_single_vector(self, mock_client, mock_settings):
        mock_client.embeddings.create.return_value = _mock_response(1)
        result = embed_query("search query")
        assert len(result) == DIMENSIONS
        assert isinstance(result[0], float)
        mock_client.embeddings.create.assert_called_once_with(
            input=["search query"], model="text-embedding-3-small"
        )


# ---------------------------------------------------------------------------
# compute_batch_size
# ---------------------------------------------------------------------------


class TestComputeBatchSize:
    def test_large_tokens_caps_at_max(self):
        result = compute_batch_size(avg_tokens=1, target_tokens=10_000)
        assert result == MAX_BATCH_SIZE

    def test_custom_values(self):
        result = compute_batch_size(avg_tokens=500, target_tokens=100_000)
        assert result == 200


# ---------------------------------------------------------------------------
# serialize_f32 / deserialize_f32
# ---------------------------------------------------------------------------


class TestSerializeF32:
    def test_passthrough(self):
        """serialize_f32 returns the list unchanged (pgvector takes lists)."""
        vec = [1.0, 2.0, 3.0]
        result = serialize_f32(vec)
        assert result is vec

    def test_correct_dimensions(self):
        vec = [0.0] * DIMENSIONS
        result = serialize_f32(vec)
        assert len(result) == DIMENSIONS

    def test_deserialize_round_trip(self):
        """deserialize_f32 still works for migration from SQLite blobs."""
        vec = [1.0, 2.5, -3.0, 0.0]
        blob = struct.pack(f"<{len(vec)}f", *vec)
        arr = deserialize_f32(blob)
        np.testing.assert_array_almost_equal(arr, vec)

    def test_deserialize_returns_writable_copy(self):
        """Deserialized array should be writable (not a read-only view)."""
        blob = struct.pack("<2f", 1.0, 2.0)
        arr = deserialize_f32(blob)
        arr[0] = 99.0  # should not raise


# ---------------------------------------------------------------------------
# Job.embed_text()
# ---------------------------------------------------------------------------


class TestEmbedText:
    def test_returns_none_without_description(self):
        job = make_job(description=None)
        assert job.embed_text("Acme Corp") is None

    def test_returns_none_for_empty_description(self):
        job = make_job(description="")
        assert job.embed_text("Acme Corp") is None

    def test_includes_company_and_title(self):
        job = make_job(title="Product Manager", description="Build cool things.")
        text = job.embed_text("Acme Corp")
        assert text.startswith("Acme Corp — Product Manager")

    def test_includes_department_and_location(self):
        job = make_job(title="Product Manager", location="Seattle, WA", description="Build cool things.", department="Engineering")
        text = job.embed_text("Acme Corp")
        assert "Engineering, Seattle, WA" in text

    def test_includes_description(self):
        job = make_job(title="Product Manager", description="Build cool things.")
        text = job.embed_text("Acme Corp")
        assert text.endswith("Build cool things.")

    def test_omits_meta_when_no_department_and_empty_location(self):
        job = make_job(title="Product Manager", location="", description="Build cool things.", department=None)
        text = job.embed_text("Acme Corp")
        lines = text.split("\n")
        assert lines[0] == "Acme Corp — Product Manager"
        assert lines[1] == ""
        assert lines[2] == "Build cool things."

"""Tests for jobbuddy.embeddings — Azure OpenAI embedding wrapper."""

import struct
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import jobbuddy.embeddings as emb
from jobbuddy.embeddings import (
    DEPLOYMENT_NAME,
    DIMENSIONS,
    MAX_BATCH_SIZE,
    MODEL_KEY,
    compute_batch_size,
    deserialize_f32,
    embed_query,
    embed_texts,
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


def _fake_embedding(dim: int = DIMENSIONS) -> list[float]:
    """Return a deterministic fake embedding vector."""
    return list(np.random.RandomState(42).randn(dim).astype(float))


def _mock_response(n: int = 1, dim: int = DIMENSIONS):
    """Build a fake OpenAI embeddings response with n items."""
    data = []
    for i in range(n):
        item = SimpleNamespace(
            embedding=list(np.random.RandomState(i).randn(dim).astype(float)),
            index=i,
        )
        data.append(item)
    return SimpleNamespace(data=data)


@pytest.fixture(autouse=True)
def _reset_client():
    """Reset the singleton client before each test."""
    emb._client = None
    yield
    emb._client = None


@pytest.fixture
def mock_client():
    """Provide a mock AzureOpenAI client, patching _get_client."""
    client = MagicMock()
    with patch("jobbuddy.embeddings._get_client", return_value=client):
        yield client


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_model_key(self):
        assert MODEL_KEY == "text3small"

    def test_dimensions(self):
        assert DIMENSIONS == 1536

    def test_max_batch_size(self):
        assert MAX_BATCH_SIZE == 2048

    def test_deployment_name(self):
        assert DEPLOYMENT_NAME == "text-embedding-3-small"


# ---------------------------------------------------------------------------
# _get_client
# ---------------------------------------------------------------------------


class TestGetClient:
    def test_raises_without_credentials(self):
        settings = MagicMock()
        settings.azure_openai_api_key = None
        settings.azure_openai_endpoint = None
        with patch("jobbuddy.embeddings.get_settings", return_value=settings):
            with pytest.raises(ValueError, match="Azure OpenAI not configured"):
                emb._get_client()

    def test_raises_without_endpoint(self):
        settings = MagicMock()
        settings.azure_openai_api_key = "key"
        settings.azure_openai_endpoint = None
        with patch("jobbuddy.embeddings.get_settings", return_value=settings):
            with pytest.raises(ValueError, match="Azure OpenAI not configured"):
                emb._get_client()

    def test_creates_client_with_credentials(self):
        settings = MagicMock()
        settings.azure_openai_api_key = "test-key"
        settings.azure_openai_endpoint = "https://test.openai.azure.com"
        settings.azure_openai_api_version = "2024-12-01-preview"
        with (
            patch("jobbuddy.embeddings.get_settings", return_value=settings),
            patch("jobbuddy.embeddings.AzureOpenAI") as mock_cls,
        ):
            client = emb._get_client()
            mock_cls.assert_called_once_with(
                api_key="test-key",
                azure_endpoint="https://test.openai.azure.com",
                api_version="2024-12-01-preview",
            )
            assert client is mock_cls.return_value

    def test_caches_client(self):
        settings = MagicMock()
        settings.azure_openai_api_key = "test-key"
        settings.azure_openai_endpoint = "https://test.openai.azure.com"
        settings.azure_openai_api_version = "2024-12-01-preview"
        with (
            patch("jobbuddy.embeddings.get_settings", return_value=settings),
            patch("jobbuddy.embeddings.AzureOpenAI") as mock_cls,
        ):
            c1 = emb._get_client()
            c2 = emb._get_client()
            assert c1 is c2
            mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# embed_texts
# ---------------------------------------------------------------------------


class TestEmbedTexts:
    def test_empty_list_returns_empty(self, mock_client):
        result = embed_texts([])
        assert result == []
        mock_client.embeddings.create.assert_not_called()

    def test_returns_embeddings(self, mock_client):
        mock_client.embeddings.create.return_value = _mock_response(2)
        result = embed_texts(["hello", "world"])
        assert len(result) == 2
        assert len(result[0]) == DIMENSIONS
        mock_client.embeddings.create.assert_called_once_with(
            input=["hello", "world"], model=DEPLOYMENT_NAME
        )

    def test_batch_too_large_raises(self, mock_client):
        texts = ["x"] * (MAX_BATCH_SIZE + 1)
        with pytest.raises(ValueError, match="Batch too large"):
            embed_texts(texts)

    def test_max_batch_size_allowed(self, mock_client):
        mock_client.embeddings.create.return_value = _mock_response(MAX_BATCH_SIZE)
        texts = ["x"] * MAX_BATCH_SIZE
        result = embed_texts(texts)
        assert len(result) == MAX_BATCH_SIZE


# ---------------------------------------------------------------------------
# embed_query
# ---------------------------------------------------------------------------


class TestEmbedQuery:
    def test_returns_single_vector(self, mock_client):
        mock_client.embeddings.create.return_value = _mock_response(1)
        result = embed_query("search query")
        assert len(result) == DIMENSIONS
        assert isinstance(result[0], float)
        mock_client.embeddings.create.assert_called_once_with(
            input=["search query"], model=DEPLOYMENT_NAME
        )


# ---------------------------------------------------------------------------
# compute_batch_size
# ---------------------------------------------------------------------------


class TestComputeBatchSize:
    def test_default_values(self):
        result = compute_batch_size()
        assert result == 250_000 // 1438  # 173

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
    def test_round_trip(self):
        vec = [1.0, 2.0, 3.0]
        blob = serialize_f32(vec)
        assert len(blob) == 12  # 3 floats * 4 bytes
        unpacked = list(struct.unpack("<3f", blob))
        assert unpacked == vec

    def test_correct_dimensions(self):
        vec = [0.0] * DIMENSIONS
        blob = serialize_f32(vec)
        assert len(blob) == DIMENSIONS * 4

    def test_deserialize_round_trip(self):
        vec = [1.0, 2.5, -3.0, 0.0]
        blob = serialize_f32(vec)
        arr = deserialize_f32(blob)
        np.testing.assert_array_almost_equal(arr, vec)

    def test_deserialize_returns_writable_copy(self):
        """Deserialized array should be writable (not a read-only view)."""
        blob = serialize_f32([1.0, 2.0])
        arr = deserialize_f32(blob)
        arr[0] = 99.0  # should not raise


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

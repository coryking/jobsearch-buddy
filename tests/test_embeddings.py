"""Tests for ats.embeddings — embedding model and Job.embed_text()."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from jobbuddy.embeddings import DIMENSIONS, embed_query, embed_texts, serialize_f32
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
        # Should be: title line, blank line, description — no meta line
        assert lines[0] == "Acme Corp — Product Manager"
        assert lines[1] == ""
        assert lines[2] == "Build cool things."


# ---------------------------------------------------------------------------
# serialize_f32
# ---------------------------------------------------------------------------


class TestSerializeF32:
    def test_round_trip(self):
        import struct

        vec = [1.0, 2.0, 3.0]
        blob = serialize_f32(vec)
        assert len(blob) == 12  # 3 floats * 4 bytes
        unpacked = list(struct.unpack(f"<3f", blob))
        assert unpacked == vec

    def test_correct_dimensions(self):
        vec = [0.0] * DIMENSIONS
        blob = serialize_f32(vec)
        assert len(blob) == DIMENSIONS * 4


# ---------------------------------------------------------------------------
# embed_texts / embed_query (mocked model)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_model():
    """Patch get_model to return a mock with deterministic vectors."""
    model = MagicMock()

    def fake_passage_embed(texts, **kwargs):
        for i, _ in enumerate(texts):
            yield np.random.RandomState(i).randn(DIMENSIONS).astype(np.float32)

    def fake_query_embed(texts, **kwargs):
        for i, _ in enumerate(texts):
            yield np.random.RandomState(i + 1000).randn(DIMENSIONS).astype(np.float32)

    model.passage_embed = fake_passage_embed
    model.query_embed = fake_query_embed

    with patch("jobbuddy.embeddings.get_model", return_value=model):
        yield model


class TestEmbedFunctions:
    def test_embed_texts_returns_list_of_vectors(self, mock_model):
        result = embed_texts(["hello world", "test doc"])
        assert len(result) == 2
        assert len(result[0]) == DIMENSIONS
        assert isinstance(result[0][0], float)

    def test_embed_query_returns_single_vector(self, mock_model):
        result = embed_query("search query")
        assert len(result) == DIMENSIONS
        assert isinstance(result[0], float)

    def test_passage_and_query_produce_different_vectors(self, mock_model):
        """Asymmetric embedding: same text through passage vs query should differ."""
        passage = embed_texts(["test"])[0]
        query = embed_query("test")
        # With different random seeds, these will differ
        assert passage != query

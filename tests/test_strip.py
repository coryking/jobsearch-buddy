"""Tests for boilerplate stripping -- store methods and StripPhase."""

from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.store import JobStore
from jobbuddy.sync.display import PhaseState
from jobbuddy.sync.strip import StripPhase

from conftest import make_job, seed_jobs


# ---------------------------------------------------------------------------
# Store: description_stripped column
# ---------------------------------------------------------------------------


class TestStoreStripping:
    def test_description_stripped_column_exists(self, store):
        row = store.conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'jobs' AND column_name = 'description_stripped'
        """).fetchone()
        assert row is not None

    def test_get_jobs_needing_stripping(self, store):
        store.upsert_jobs("acme", [
            make_job("1", description="Full description here"),
            make_job("2", description="Another description"),
            make_job("3"),  # no description -- should be excluded
        ])
        store.conn.execute(
            "UPDATE jobs SET description_stripped = 'cleaned' WHERE job_id = '1'"
        )

        needing = store.get_jobs_needing_stripping()
        assert len(needing) == 1
        assert needing[0]["job_id"] == "2"

    def test_get_jobs_needing_stripping_excludes_disappeared(self, store):
        store.upsert_jobs("acme", [
            make_job("1", description="desc"),
            make_job("2", description="desc"),
        ])
        store.upsert_jobs("acme", [make_job("1", description="desc")])

        needing = store.get_jobs_needing_stripping()
        assert len(needing) == 1
        assert needing[0]["job_id"] == "1"

    def test_get_jobs_needing_stripping_respects_limit(self, store):
        store.upsert_jobs("acme", [
            make_job("1", description="d1"),
            make_job("2", description="d2"),
            make_job("3", description="d3"),
        ])
        needing = store.get_jobs_needing_stripping(limit=2)
        assert len(needing) == 2

    def test_update_stripped_description(self, store):
        store.upsert_jobs("acme", [make_job("1", description="raw")])
        pk = store.conn.execute("SELECT id FROM jobs WHERE job_id = '1'").fetchone()["id"]
        store.update_stripped_description(pk, "cleaned text")
        row = store.conn.execute("SELECT description_stripped FROM jobs WHERE id = %s", (pk,)).fetchone()
        assert row["description_stripped"] == "cleaned text"

    def test_clear_stripped_descriptions(self, store):
        store.upsert_jobs("acme", [make_job("1", description="raw")])
        pk = store.conn.execute("SELECT id FROM jobs WHERE job_id = '1'").fetchone()["id"]
        store.update_stripped_description(pk, "cleaned")
        cleared = store.clear_stripped_descriptions()
        assert cleared == 1
        row = store.conn.execute("SELECT description_stripped FROM jobs WHERE id = %s", (pk,)).fetchone()
        assert row["description_stripped"] is None


# ---------------------------------------------------------------------------
# Store: embed_text uses stripped description
# ---------------------------------------------------------------------------


class TestEmbedTextStripped:
    def test_embed_text_prefers_stripped(self, store):
        store.upsert_jobs("acme", [make_job("1", description="raw description")])
        pk = store.conn.execute("SELECT id FROM jobs WHERE job_id = '1'").fetchone()["id"]

        store.conn.execute("UPDATE jobs SET description_stripped = 'raw description' WHERE id = %s", (pk,))

        vec = [0.1] * 1536
        store.store_embedding(pk, vec)
        assert store.count_jobs_needing_embeddings() == 0

        store.update_stripped_description(pk, "stripped description")
        assert store.count_jobs_needing_embeddings() == 1

    def test_embed_text_falls_back_to_raw(self):
        """embed_text uses raw description when no stripped version available."""
        job = make_job("1", description="raw desc")
        text = job.embed_text("acme")
        assert "raw desc" in text

    def test_embed_text_uses_stripped_when_available(self):
        """embed_text uses stripped description when provided."""
        job = make_job("1", description="raw desc")
        text = job.embed_text("acme", description_stripped="stripped desc")
        assert "stripped desc" in text
        assert "raw desc" not in text


# ---------------------------------------------------------------------------
# StripPhase
# ---------------------------------------------------------------------------


class TestStripPhase:
    """Tests for StripPhase directly -- no sync_jobs() pipeline."""

    def test_strip_phase_noop_when_no_work(self, pg_conninfo):
        """StripPhase returns early when no jobs need stripping."""
        seed_jobs(pg_conninfo, "acme", [make_job("1")])

        display = PhaseState("Strip")
        StripPhase(pg_conninfo, display=display, max_workers=1).run()

        assert display.status == "pending"

    @patch("jobbuddy.sync.strip.create_openai_client")
    def test_strip_phase_calls_llm(self, mock_factory, pg_conninfo):
        """StripPhase calls OpenAI API for unstripped jobs."""
        seed_jobs(pg_conninfo, "acme", [
            make_job("1", description="raw description with boilerplate"),
            make_job("2", description="another raw description"),
        ])

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "cleaned description"
        mock_response.usage.total_tokens = 100
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_factory.return_value = mock_client

        display = PhaseState("Strip")
        StripPhase(pg_conninfo, display=display, max_workers=1).run()

        assert mock_client.chat.completions.create.call_count == 2
        assert display.total == 2
        assert display.done == 2
        assert display.status == "idle"

        store = JobStore(pg_conninfo)
        row1 = store.conn.execute("SELECT description_stripped FROM jobs WHERE job_id = '1'").fetchone()
        row2 = store.conn.execute("SELECT description_stripped FROM jobs WHERE job_id = '2'").fetchone()
        assert row1["description_stripped"] == "cleaned description"
        assert row2["description_stripped"] == "cleaned description"
        store.close()

    @patch("jobbuddy.sync.strip.create_openai_client")
    def test_strip_phase_skips_already_stripped(self, mock_factory, pg_conninfo):
        """StripPhase skips jobs that already have stripped descriptions."""
        seed_jobs(pg_conninfo, "acme", [
            make_job("1", description="raw"),
            make_job("2", description="raw"),
        ])
        store = JobStore(pg_conninfo)
        store.conn.execute(
            "UPDATE jobs SET description_stripped = 'already clean' WHERE job_id = '1'"
        )
        store.close()

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "cleaned"
        mock_response.usage.total_tokens = 50
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_factory.return_value = mock_client

        display = PhaseState("Strip")
        StripPhase(pg_conninfo, display=display, max_workers=1).run()

        assert mock_client.chat.completions.create.call_count == 1

    @patch("jobbuddy.sync.strip.create_openai_client")
    def test_strip_phase_rejects_empty_llm_response(self, mock_factory, pg_conninfo):
        """Empty LLM response records an error, not a silent empty write."""
        seed_jobs(pg_conninfo, "acme", [make_job("1", description="real job description")])

        mock_response = MagicMock()
        mock_response.choices[0].message.content = ""
        mock_response.usage.total_tokens = 10
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_factory.return_value = mock_client

        display = PhaseState("Strip")
        StripPhase(pg_conninfo, display=display, max_workers=1).run()

        assert display.errors >= 1

        store = JobStore(pg_conninfo)
        row = store.conn.execute(
            "SELECT description_stripped FROM jobs WHERE job_id = '1'"
        ).fetchone()
        assert row["description_stripped"] is None
        store.close()

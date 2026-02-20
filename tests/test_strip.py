"""Tests for boilerplate stripping — store methods and StripPhase."""

from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.models import Job
from jobbuddy.store import JobStore
from jobbuddy.sync.display import PhaseState
from jobbuddy.sync.strip import StripPhase


def _make_job(id: str = "123", title: str = "PM", location: str = "Seattle", **kw) -> Job:
    return Job(
        id=id,
        title=title,
        location=location,
        url=f"https://example.com/jobs/{id}",
        apply_url=f"https://example.com/jobs/{id}/apply",
        **kw,
    )


@pytest.fixture
def store():
    s = JobStore(":memory:")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Store: description_stripped column
# ---------------------------------------------------------------------------


class TestStoreStripping:
    def test_description_stripped_column_exists(self, store):
        cols = {row[1] for row in store.conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "description_stripped" in cols

    def test_get_jobs_needing_stripping(self, store):
        store.upsert_jobs("acme", [
            _make_job("1", description="Full description here"),
            _make_job("2", description="Another description"),
            _make_job("3"),  # no description — should be excluded
        ])
        # Manually set one as already stripped
        store.conn.execute(
            "UPDATE jobs SET description_stripped = 'cleaned' WHERE job_id = '1'"
        )
        store.conn.commit()

        needing = store.get_jobs_needing_stripping()
        assert len(needing) == 1
        assert needing[0]["job_id"] == "2"

    def test_get_jobs_needing_stripping_excludes_disappeared(self, store):
        store.upsert_jobs("acme", [
            _make_job("1", description="desc"),
            _make_job("2", description="desc"),
        ])
        # Make job 2 disappear
        store.upsert_jobs("acme", [_make_job("1", description="desc")])

        needing = store.get_jobs_needing_stripping()
        assert len(needing) == 1
        assert needing[0]["job_id"] == "1"

    def test_get_jobs_needing_stripping_respects_limit(self, store):
        store.upsert_jobs("acme", [
            _make_job("1", description="d1"),
            _make_job("2", description="d2"),
            _make_job("3", description="d3"),
        ])
        needing = store.get_jobs_needing_stripping(limit=2)
        assert len(needing) == 2

    def test_update_stripped_description(self, store):
        store.upsert_jobs("acme", [_make_job("1", description="raw")])
        pk = store.conn.execute("SELECT id FROM jobs WHERE job_id = '1'").fetchone()["id"]
        store.update_stripped_description(pk, "cleaned text")
        row = store.conn.execute("SELECT description_stripped FROM jobs WHERE id = ?", (pk,)).fetchone()
        assert row["description_stripped"] == "cleaned text"

    def test_clear_stripped_descriptions(self, store):
        store.upsert_jobs("acme", [_make_job("1", description="raw")])
        pk = store.conn.execute("SELECT id FROM jobs WHERE job_id = '1'").fetchone()["id"]
        store.update_stripped_description(pk, "cleaned")
        cleared = store.clear_stripped_descriptions()
        assert cleared == 1
        row = store.conn.execute("SELECT description_stripped FROM jobs WHERE id = ?", (pk,)).fetchone()
        assert row["description_stripped"] is None

    def test_column_migration_adds_description_stripped(self, tmp_path):
        """Opening a DB without description_stripped column adds it via migration."""
        import sqlite3

        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE jobs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                company_slug   TEXT NOT NULL,
                job_id         TEXT NOT NULL,
                title          TEXT NOT NULL,
                location       TEXT,
                url            TEXT,
                published_at   DATE,
                department     TEXT,
                team           TEXT,
                salary         TEXT,
                description    TEXT,
                ats_metadata   TEXT,
                last_seen      TIMESTAMP NOT NULL,
                disappeared_at TIMESTAMP,
                UNIQUE (company_slug, job_id)
            );
            CREATE TABLE sync_status (
                company_slug TEXT PRIMARY KEY,
                last_sync    TIMESTAMP NOT NULL,
                job_count    INTEGER NOT NULL DEFAULT 0,
                error        TEXT
            );
        """)
        conn.commit()
        conn.close()

        store = JobStore(str(db_path))
        cols = {row[1] for row in store.conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "description_stripped" in cols
        store.close()


# ---------------------------------------------------------------------------
# Store: embed_text uses stripped description
# ---------------------------------------------------------------------------


class TestEmbedTextStripped:
    def test_embed_text_prefers_stripped(self, store):
        """jobs_needing_embeddings detects hash change when description_stripped is set."""
        import hashlib
        import struct

        store.upsert_jobs("acme", [_make_job("1", description="raw description")])
        pk = store.conn.execute("SELECT id FROM jobs WHERE job_id = '1'").fetchone()["id"]

        # Embed with raw description
        job = _make_job("1", description="raw description")
        raw_text = job.embed_text("acme")
        raw_hash = hashlib.sha256(raw_text.encode()).hexdigest()
        blob = struct.pack(f"<1536f", *([0.1] * 1536))
        store.store_embedding(pk, blob, raw_hash)

        # Should be up to date
        assert store.jobs_needing_embeddings(count_only=True) == 0

        # Set stripped description — hash changes, needs re-embedding
        store.update_stripped_description(pk, "stripped description")
        assert store.jobs_needing_embeddings(count_only=True) == 1

    def test_embed_text_falls_back_to_raw(self):
        """embed_text uses raw description when no stripped version available."""
        job = _make_job("1", description="raw desc")
        text = job.embed_text("acme")
        assert "raw desc" in text

    def test_embed_text_uses_stripped_when_available(self):
        """embed_text uses stripped description when provided."""
        job = _make_job("1", description="raw desc")
        text = job.embed_text("acme", description_stripped="stripped desc")
        assert "stripped desc" in text
        assert "raw desc" not in text


# ---------------------------------------------------------------------------
# StripPhase
# ---------------------------------------------------------------------------


class TestStripPhase:
    """Tests for StripPhase directly — no sync_jobs() pipeline."""

    def _make_mock_settings(self):
        settings = MagicMock()
        settings.azure_openai_api_key = "fake-key"
        settings.azure_openai_endpoint = "https://test.openai.azure.com/"
        settings.azure_openai_api_version = "2024-12-01-preview"
        settings.azure_openai_model = "gpt-5-nano"
        return settings

    def _seed_jobs(self, db_path, jobs):
        store = JobStore(db_path)
        store.upsert_jobs("acme", jobs)
        store.close()

    def test_strip_phase_noop_when_no_work(self, tmp_path):
        """StripPhase returns early when no jobs need stripping."""
        db = str(tmp_path / "test.db")
        self._seed_jobs(db, [_make_job("1")])  # no description → nothing to strip

        display = PhaseState("Strip")
        StripPhase(db, display=display, max_workers=1).run()

        assert display.status == "pending"  # never started

    @patch("jobbuddy.sync.strip.AzureOpenAI")
    @patch("jobbuddy.sync.strip.get_settings")
    def test_strip_phase_calls_llm(self, mock_settings, mock_client_cls, tmp_path):
        """StripPhase calls Azure OpenAI for unstripped jobs."""
        mock_settings.return_value = self._make_mock_settings()
        db = str(tmp_path / "test.db")
        self._seed_jobs(db, [
            _make_job("1", description="raw description with boilerplate"),
            _make_job("2", description="another raw description"),
        ])

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "cleaned description"
        mock_response.usage.total_tokens = 100
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_client_cls.return_value = mock_client

        display = PhaseState("Strip")
        StripPhase(db, display=display, max_workers=1).run()

        assert mock_client.chat.completions.create.call_count == 2
        assert display.total == 2
        assert display.done == 2
        assert display.status == "idle"

        # Verify descriptions were updated
        store = JobStore(db)
        row1 = store.conn.execute("SELECT description_stripped FROM jobs WHERE job_id = '1'").fetchone()
        row2 = store.conn.execute("SELECT description_stripped FROM jobs WHERE job_id = '2'").fetchone()
        assert row1["description_stripped"] == "cleaned description"
        assert row2["description_stripped"] == "cleaned description"
        store.close()

    @patch("jobbuddy.sync.strip.AzureOpenAI")
    @patch("jobbuddy.sync.strip.get_settings")
    def test_strip_phase_skips_already_stripped(self, mock_settings, mock_client_cls, tmp_path):
        """StripPhase skips jobs that already have stripped descriptions."""
        mock_settings.return_value = self._make_mock_settings()
        db = str(tmp_path / "test.db")
        self._seed_jobs(db, [
            _make_job("1", description="raw"),
            _make_job("2", description="raw"),
        ])
        # Manually mark job 1 as stripped
        store = JobStore(db)
        store.conn.execute(
            "UPDATE jobs SET description_stripped = 'already clean' WHERE job_id = '1'"
        )
        store.conn.commit()
        store.close()

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "cleaned"
        mock_response.usage.total_tokens = 50
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_client_cls.return_value = mock_client

        display = PhaseState("Strip")
        StripPhase(db, display=display, max_workers=1).run()

        assert mock_client.chat.completions.create.call_count == 1

    @patch("jobbuddy.sync.strip.AzureOpenAI")
    @patch("jobbuddy.sync.strip.get_settings")
    def test_strip_phase_error_isolation(self, mock_settings, mock_client_cls, tmp_path):
        """One job failing to strip doesn't block others."""
        mock_settings.return_value = self._make_mock_settings()
        db = str(tmp_path / "test.db")
        self._seed_jobs(db, [
            _make_job("1", description="first"),
            _make_job("2", description="second"),
        ])

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("API error")
            response = MagicMock()
            response.choices[0].message.content = "cleaned"
            response.usage.total_tokens = 50
            return response

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect
        mock_client_cls.return_value = mock_client

        display = PhaseState("Strip")
        StripPhase(db, display=display, max_workers=1).run()

        assert display.errors == 1
        # WorkerPhase re-polls: the failed job retries and succeeds on next pass
        assert display.done == 2

        # Both end up stripped (transient error retried via DB-as-queue)
        store = JobStore(db)
        rows = store.conn.execute(
            "SELECT job_id, description_stripped FROM jobs ORDER BY job_id"
        ).fetchall()
        stripped_count = sum(1 for r in rows if r["description_stripped"] is not None)
        assert stripped_count == 2
        store.close()

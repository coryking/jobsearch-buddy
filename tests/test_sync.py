"""Tests for sync orchestration in jobbuddy.sync (PostgreSQL)."""

import queue
from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.models import Company, Job
from jobbuddy.sync import SyncResult, sync_jobs
from jobbuddy.sync.types import (
    CompanySkipped,
    Done,
    FetchResult,
)


def _make_job(id: str, title: str = "PM", ats_metadata: dict | None = None) -> Job:
    return Job(
        id=id,
        title=title,
        location="Seattle",
        url=f"https://example.com/jobs/{id}",
        apply_url=f"https://example.com/jobs/{id}/apply",
        ats_metadata=ats_metadata,
    )


def _make_company(slug: str, ats: str = "greenhouse") -> Company:
    return Company(slug=slug, name=slug.title(), ats=ats, board=slug)


def _drain_events(eq):
    """Drain all events from a SimpleQueue into a list."""
    events = []
    while True:
        try:
            events.append(eq.get_nowait())
        except queue.Empty:
            break
    return events


class TestSync:
    @patch("jobbuddy.sync.list_companies")
    @patch("jobbuddy.sync.fetch.get_fetcher")
    def test_sync_single_company(self, mock_get_fetcher, mock_list_companies, pg_conninfo):
        """Sync one company populates cache."""
        company = _make_company("acme")
        mock_list_companies.return_value = {"acme": company}

        mock_fetcher = MagicMock()
        mock_fetcher.list_jobs.return_value = [_make_job("1"), _make_job("2")]
        mock_get_fetcher.return_value = mock_fetcher

        with patch("jobbuddy.sync.lookup_by_name", return_value=company):
            results = sync_jobs(company_slugs=["acme"], conninfo=pg_conninfo)

        assert len(results) == 1
        assert results[0].ok
        assert results[0].job_count == 2
        assert results[0].slug == "acme"

        from jobbuddy.store import JobStore
        store = JobStore(pg_conninfo)
        assert store.job_count() == 2
        rows = store.query_jobs(company="acme")
        assert len(rows) == 2
        store.close()

    @patch("jobbuddy.sync.list_companies")
    @patch("jobbuddy.sync.fetch.get_fetcher")
    def test_sync_error_isolation(self, mock_get_fetcher, mock_list_companies, pg_conninfo):
        """One company failing doesn't stop others."""
        good_co = _make_company("good")
        bad_co = _make_company("bad")
        mock_list_companies.return_value = {"good": good_co, "bad": bad_co}

        def side_effect(company):
            fetcher = MagicMock()
            if company.slug == "bad":
                fetcher.list_jobs.side_effect = Exception("Connection refused")
            else:
                fetcher.list_jobs.return_value = [_make_job("1")]
            return fetcher

        mock_get_fetcher.side_effect = side_effect

        results = sync_jobs(max_workers=1, conninfo=pg_conninfo)

        ok_results = [r for r in results if r.ok]
        err_results = [r for r in results if not r.ok]
        assert len(ok_results) == 1
        assert len(err_results) == 1
        assert err_results[0].error == "Connection refused"

    @patch("jobbuddy.sync.list_companies")
    @patch("jobbuddy.sync.fetch.get_fetcher")
    def test_sync_events_fire(self, mock_get_fetcher, mock_list_companies, pg_conninfo):
        """FetchResult events fire for each company."""
        company = _make_company("acme")
        mock_list_companies.return_value = {"acme": company}

        mock_fetcher = MagicMock()
        mock_fetcher.list_jobs.return_value = [_make_job("1")]
        mock_get_fetcher.return_value = mock_fetcher

        eq = queue.SimpleQueue()
        sync_jobs(events=eq, conninfo=pg_conninfo)

        events = _drain_events(eq)
        fetch_results = [e for e in events if isinstance(e, FetchResult)]
        assert len(fetch_results) == 1
        assert fetch_results[0].result.ok

        assert any(isinstance(e, Done) for e in events)

    def test_sync_unknown_company_raises(self):
        """Syncing a non-existent company raises ValueError."""
        with patch("jobbuddy.sync.lookup_by_name", return_value=None):
            with pytest.raises(ValueError, match="Unknown company"):
                sync_jobs(company_slugs=["nonexistent"])

    def test_sync_no_ats_raises(self):
        """Syncing a company without ATS config raises ValueError."""
        company = Company(slug="noats", name="No ATS", ats=None, board=None)
        with patch("jobbuddy.sync.lookup_by_name", return_value=company):
            with pytest.raises(ValueError, match="No ATS configured"):
                sync_jobs(company_slugs=["noats"])

    @patch("jobbuddy.sync.list_companies")
    @patch("jobbuddy.sync.fetch.get_fetcher")
    def test_sync_stale_skips_recent(self, mock_get_fetcher, mock_list_companies, pg_conninfo):
        """--stale flag skips recently synced companies."""
        company = _make_company("acme")
        mock_list_companies.return_value = {"acme": company}

        mock_fetcher = MagicMock()
        mock_fetcher.list_jobs.return_value = [_make_job("1")]
        mock_get_fetcher.return_value = mock_fetcher

        # First sync
        sync_jobs(conninfo=pg_conninfo)
        # Second sync with stale_hours=24 should skip
        eq = queue.SimpleQueue()
        results = sync_jobs(
            stale_hours=24,
            events=eq,
            conninfo=pg_conninfo,
        )
        assert len(results) == 0

        events = _drain_events(eq)
        skipped = [e for e in events if isinstance(e, CompanySkipped)]
        assert len(skipped) == 1
        assert skipped[0].slug == "acme"


class TestEnrichment:
    """Tests for EnrichPhase directly -- no sync_jobs() pipeline."""

    def _seed_jobs(self, conninfo, slug, jobs):
        """Insert jobs into the DB (simulates what FetchPhase does)."""
        from jobbuddy.store import JobStore
        store = JobStore(conninfo)
        store.upsert_jobs(slug, jobs)
        store.close()

    @patch("jobbuddy.sync.enrich.get_fetcher")
    def test_enrichment_fetches_descriptions_for_stub_fetchers(
        self, mock_get_fetcher, pg_conninfo
    ):
        """Jobs from fetchers with descriptions_in_listing=False get descriptions."""
        company = _make_company("workday-co", ats="workday")
        self._seed_jobs(pg_conninfo, "workday-co", [
            _make_job("1", "PM", ats_metadata={"ext_path": "/job/1"}),
            _make_job("2", "SWE", ats_metadata={"ext_path": "/job/2"}),
        ])

        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = False

        def fake_fetch(job_ids, *, metadata=None, on_fetched=None, on_retry=None):
            for jid, desc in [("1", "PM description"), ("2", "SWE description")]:
                if on_fetched:
                    on_fetched(jid, desc)

        mock_fetcher.fetch_descriptions.side_effect = fake_fetch
        mock_get_fetcher.return_value = mock_fetcher

        from jobbuddy.sync.display import PhaseState
        from jobbuddy.sync.enrich import EnrichPhase
        EnrichPhase(
            pg_conninfo, slugs=["workday-co"], targets=[company],
            display=PhaseState("Enrich"), max_workers=1,
        ).run()

        from jobbuddy.store import JobStore
        store = JobStore(pg_conninfo)
        row1 = store.conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        row2 = store.conn.execute("SELECT description FROM jobs WHERE job_id = '2'").fetchone()
        assert row1["description"] == "PM description"
        assert row2["description"] == "SWE description"
        store.close()

    @patch("jobbuddy.sync.enrich.get_fetcher")
    def test_no_enrichment_for_full_fetchers(self, mock_get_fetcher, pg_conninfo):
        """Fetchers with descriptions_in_listing=True don't trigger enrichment."""
        company = _make_company("acme")
        self._seed_jobs(pg_conninfo, "acme", [_make_job("1")])

        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = True
        mock_get_fetcher.return_value = mock_fetcher

        from jobbuddy.sync.display import PhaseState
        from jobbuddy.sync.enrich import EnrichPhase
        EnrichPhase(
            pg_conninfo, slugs=["acme"], targets=[company],
            display=PhaseState("Enrich"), max_workers=1,
        ).run()

        mock_fetcher.fetch_descriptions.assert_not_called()

    @patch("jobbuddy.sync.enrich.get_fetcher")
    def test_enrichment_skips_already_described_jobs(self, mock_get_fetcher, pg_conninfo):
        """EnrichPhase doesn't re-enrich jobs that already have descriptions."""
        company = _make_company("workday-co", ats="workday")
        self._seed_jobs(pg_conninfo, "workday-co", [_make_job("1")])

        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = False

        def fake_fetch(job_ids, *, metadata=None, on_fetched=None, on_retry=None):
            for jid in job_ids:
                if on_fetched:
                    on_fetched(jid, "description")

        mock_fetcher.fetch_descriptions.side_effect = fake_fetch
        mock_get_fetcher.return_value = mock_fetcher

        from jobbuddy.sync.display import PhaseState
        from jobbuddy.sync.enrich import EnrichPhase

        EnrichPhase(
            pg_conninfo, slugs=["workday-co"], targets=[company],
            display=PhaseState("Enrich"), max_workers=1,
        ).run()
        assert mock_fetcher.fetch_descriptions.call_count == 1

        mock_fetcher.fetch_descriptions.reset_mock()
        EnrichPhase(
            pg_conninfo, slugs=["workday-co"], targets=[company],
            display=PhaseState("Enrich"), max_workers=1,
        ).run()
        mock_fetcher.fetch_descriptions.assert_not_called()

    @patch("jobbuddy.sync.enrich.get_fetcher")
    def test_enrichment_failure_isolated(self, mock_get_fetcher, pg_conninfo):
        """Enrichment failure is caught by WorkerPhase -- run() completes."""
        company = _make_company("workday-co", ats="workday")
        self._seed_jobs(pg_conninfo, "workday-co", [_make_job("1")])

        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = False
        mock_fetcher.fetch_descriptions.side_effect = Exception("Network error")
        mock_get_fetcher.return_value = mock_fetcher

        from jobbuddy.sync.display import PhaseState
        from jobbuddy.sync.enrich import EnrichPhase
        display = PhaseState("Enrich")
        EnrichPhase(
            pg_conninfo, slugs=["workday-co"], targets=[company],
            display=display, max_workers=1,
        ).run()

        assert display.errors == 1

    @patch("jobbuddy.sync.enrich.get_fetcher")
    def test_enrichment_display_state(self, mock_get_fetcher, pg_conninfo):
        """EnrichPhase updates PhaseState: total, done, status."""
        company = _make_company("workday-co", ats="workday")
        self._seed_jobs(pg_conninfo, "workday-co", [_make_job("1"), _make_job("2")])

        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = False

        def fake_fetch(job_ids, *, metadata=None, on_fetched=None, on_retry=None):
            for jid in job_ids:
                if on_fetched:
                    on_fetched(jid, f"desc-{jid}")

        mock_fetcher.fetch_descriptions.side_effect = fake_fetch
        mock_get_fetcher.return_value = mock_fetcher

        from jobbuddy.sync.display import PhaseState
        from jobbuddy.sync.enrich import EnrichPhase
        display = PhaseState("Enrich")
        EnrichPhase(
            pg_conninfo, slugs=["workday-co"], targets=[company],
            display=display, max_workers=1,
        ).run()

        assert display.total == 2
        assert display.done == 2
        assert display.status == "idle"

    @patch("jobbuddy.sync.enrich.get_fetcher")
    def test_enrichment_passes_metadata(self, mock_get_fetcher, pg_conninfo):
        """Enrichment passes ats_metadata from DB to fetch_descriptions."""
        company = _make_company("workday-co", ats="workday")
        self._seed_jobs(pg_conninfo, "workday-co", [
            _make_job("1", ats_metadata={"ext_path": "/job/1"}),
        ])

        captured_metadata = {}
        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = False

        def fake_fetch(job_ids, *, metadata=None, on_fetched=None, on_retry=None):
            captured_metadata.update(metadata or {})
            for jid in job_ids:
                if on_fetched:
                    on_fetched(jid, "description")

        mock_fetcher.fetch_descriptions.side_effect = fake_fetch
        mock_get_fetcher.return_value = mock_fetcher

        from jobbuddy.sync.display import PhaseState
        from jobbuddy.sync.enrich import EnrichPhase
        EnrichPhase(
            pg_conninfo, slugs=["workday-co"], targets=[company],
            display=PhaseState("Enrich"), max_workers=1,
        ).run()

        assert "1" in captured_metadata
        assert captured_metadata["1"]["ext_path"] == "/job/1"

    @patch("jobbuddy.sync.enrich.get_fetcher")
    def test_incremental_commit_survives_partial_failure(self, mock_get_fetcher, pg_conninfo):
        """Descriptions committed via on_fetched survive even if later jobs fail."""
        company = _make_company("workday-co", ats="workday")
        self._seed_jobs(pg_conninfo, "workday-co", [_make_job("1"), _make_job("2")])

        mock_fetcher = MagicMock()
        mock_fetcher.descriptions_in_listing = False

        def fake_fetch(job_ids, *, metadata=None, on_fetched=None, on_retry=None):
            if on_fetched:
                on_fetched("1", "first description")
            raise Exception("Network died mid-batch")

        mock_fetcher.fetch_descriptions.side_effect = fake_fetch
        mock_get_fetcher.return_value = mock_fetcher

        from jobbuddy.sync.display import PhaseState
        from jobbuddy.sync.enrich import EnrichPhase
        EnrichPhase(
            pg_conninfo, slugs=["workday-co"], targets=[company],
            display=PhaseState("Enrich"), max_workers=1,
        ).run()

        from jobbuddy.store import JobStore
        store = JobStore(pg_conninfo)
        row1 = store.conn.execute("SELECT description FROM jobs WHERE job_id = '1'").fetchone()
        row2 = store.conn.execute("SELECT description FROM jobs WHERE job_id = '2'").fetchone()
        assert row1["description"] == "first description"
        assert row2["description"] is None
        store.close()


class TestSyncResult:
    def test_ok_result(self):
        sr = SyncResult("acme", job_count=10)
        assert sr.ok is True
        assert sr.error is None

    def test_error_result(self):
        sr = SyncResult("acme", error="timeout")
        assert sr.ok is False
        assert sr.error == "timeout"

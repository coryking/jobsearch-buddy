"""Tests for sync orchestration in jobbuddy.sync (PostgreSQL)."""

import queue
from unittest.mock import MagicMock, patch

import pytest

from jobbuddy.models import Company, Job
from jobbuddy.settings import Settings
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

    # NOTE: test_sync_unknown_company_raises and test_sync_no_ats_raises
    # moved to TestValidateSyncConfig — validation happens in
    # validate_sync_config(), not sync_jobs().

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


class TestFetchPhaseState:
    """Tests for FetchPhase updating PhaseState directly (no event queue)."""

    @patch("jobbuddy.sync.fetch.get_fetcher")
    def test_fetch_updates_done_count(self, mock_get_fetcher, pg_conninfo):
        """FetchPhase sets done count on PhaseState."""
        from jobbuddy.sync.display import PhaseState
        from jobbuddy.sync.fetch import FetchPhase
        from jobbuddy.store import JobStore

        company = _make_company("acme")
        mock_fetcher = MagicMock()
        mock_fetcher.list_jobs.return_value = [_make_job("1"), _make_job("2")]
        mock_get_fetcher.return_value = mock_fetcher

        display = PhaseState("Fetch")
        store = JobStore(pg_conninfo)
        FetchPhase(store, [company], max_workers=1, display=display).run()
        store.close()

        assert display.done == 1  # 1 company completed
        assert display.status == "idle"

    @patch("jobbuddy.sync.fetch.get_fetcher")
    def test_fetch_tracks_errors(self, mock_get_fetcher, pg_conninfo):
        """FetchPhase records errors on PhaseState."""
        from jobbuddy.sync.display import PhaseState
        from jobbuddy.sync.fetch import FetchPhase
        from jobbuddy.store import JobStore

        company = _make_company("bad")
        mock_fetcher = MagicMock()
        mock_fetcher.list_jobs.side_effect = Exception("Connection refused")
        mock_get_fetcher.return_value = mock_fetcher

        display = PhaseState("Fetch")
        store = JobStore(pg_conninfo)
        FetchPhase(store, [company], max_workers=1, display=display).run()
        store.close()

        assert display.errors == 1
        assert display.done == 1  # still counts as processed

    @patch("jobbuddy.sync.fetch.get_fetcher")
    def test_fetch_counts_jobs_in_info(self, mock_get_fetcher, pg_conninfo):
        """FetchPhase tracks total job count via info counter."""
        from jobbuddy.sync.display import PhaseState
        from jobbuddy.sync.fetch import FetchPhase
        from jobbuddy.store import JobStore

        company = _make_company("acme")
        mock_fetcher = MagicMock()
        mock_fetcher.list_jobs.return_value = [_make_job("1"), _make_job("2"), _make_job("3")]
        mock_get_fetcher.return_value = mock_fetcher

        display = PhaseState("Fetch")
        store = JobStore(pg_conninfo)
        FetchPhase(store, [company], max_workers=1, display=display).run()
        store.close()

        assert display.info  # should have job count string
        assert display._info_counter == 3


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


class TestValidateSyncConfig:
    """Tests for validate_sync_config() — all preconditions checked up front."""

    def _settings(self, has_openai: bool = True) -> Settings:
        """Build a Settings with controlled OpenAI state."""
        return Settings(
            pg_service="job-search-buddy-test",
            openai_api_key="test-key" if has_openai else None,
        )

    def test_rejects_invalid_phases(self):
        """Invalid phase names raise ValueError before any I/O."""
        from jobbuddy.sync import validate_sync_config

        with pytest.raises(ValueError, match="Invalid phase"):
            validate_sync_config(phases={"bogus"}, settings=self._settings())

    def test_requires_openai_for_strip(self):
        """Missing OpenAI key with strip phase raises ValueError."""
        from jobbuddy.sync import validate_sync_config

        with pytest.raises(ValueError, match="JOBBUDDY_OPENAI_API_KEY"):
            validate_sync_config(
                phases={"fetch", "strip"},
                settings=self._settings(has_openai=False),
            )

    def test_requires_openai_for_embed(self):
        """Missing OpenAI key with embed phase raises ValueError."""
        from jobbuddy.sync import validate_sync_config

        with pytest.raises(ValueError, match="JOBBUDDY_OPENAI_API_KEY"):
            validate_sync_config(
                phases={"fetch", "embed"},
                settings=self._settings(has_openai=False),
            )

    def test_fetch_enrich_ok_without_openai(self):
        """fetch + enrich works without OpenAI key."""
        from jobbuddy.sync import validate_sync_config

        config = validate_sync_config(
            phases={"fetch", "enrich"},
            settings=self._settings(has_openai=False),
        )
        assert config.phases == {"fetch", "enrich"}

    def test_all_phases_requires_openai(self):
        """Running all phases (default) requires OpenAI."""
        from jobbuddy.sync import validate_sync_config

        with pytest.raises(ValueError, match="JOBBUDDY_OPENAI_API_KEY"):
            validate_sync_config(
                phases=None,
                settings=self._settings(has_openai=False),
            )

    @patch("jobbuddy.sync.lookup_by_name")
    def test_resolves_company_slugs(self, mock_lookup):
        """Company names are resolved to Company objects."""
        from jobbuddy.sync import validate_sync_config

        company = _make_company("acme")
        mock_lookup.return_value = company
        config = validate_sync_config(
            phases={"fetch"},
            company_slugs=["acme"],
            settings=self._settings(),
        )
        assert len(config.targets) == 1
        assert config.targets[0].slug == "acme"

    @patch("jobbuddy.sync.lookup_by_name", return_value=None)
    def test_unknown_company_raises(self, mock_lookup):
        """Unknown company name raises ValueError."""
        from jobbuddy.sync import validate_sync_config

        with pytest.raises(ValueError, match="Unknown company"):
            validate_sync_config(
                phases={"fetch"},
                company_slugs=["nonexistent"],
                settings=self._settings(),
            )

    @patch("jobbuddy.sync.lookup_by_name")
    def test_company_without_ats_raises(self, mock_lookup):
        """Company without ATS config raises ValueError."""
        from jobbuddy.sync import validate_sync_config

        company = Company(slug="noats", name="No ATS", ats=None, board=None)
        mock_lookup.return_value = company
        with pytest.raises(ValueError, match="No ATS configured"):
            validate_sync_config(
                phases={"fetch"},
                company_slugs=["noats"],
                settings=self._settings(),
            )

    def test_returns_conninfo_from_settings(self):
        """Config.conninfo comes from settings.pg_conninfo."""
        from jobbuddy.sync import validate_sync_config

        settings = self._settings()
        config = validate_sync_config(
            phases={"fetch", "enrich"},
            settings=settings,
        )
        assert config.conninfo == settings.pg_conninfo

    def test_default_phases_is_all(self):
        """phases=None resolves to all four phases."""
        from jobbuddy.sync import validate_sync_config, VALID_PHASES

        config = validate_sync_config(phases=None, settings=self._settings())
        assert config.phases == VALID_PHASES


class TestSyncResult:
    def test_ok_result(self):
        sr = SyncResult("acme", job_count=10)
        assert sr.ok is True
        assert sr.error is None

    def test_error_result(self):
        sr = SyncResult("acme", error="timeout")
        assert sr.ok is False
        assert sr.error == "timeout"

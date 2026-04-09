"""Tests for EmbedPhase — single-threaded embed pipeline.

The embed phase runs one synchronous loop (poll → embed → write → repeat).
It is intentionally single-threaded: embed_texts() saturates the provider's
TPM quota from a single worker via header-based pacing, and going wider
was the source of a prefetch/dedupe bug that re-embedded jobs several
times per run.

These tests lock in:
- Each eligible job is embedded exactly once, even when upstream_done is
  still firing (the regression).
- Happy path, slug filter, empty-work, retry, and recovery behaviors.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from jobbuddy.store import JobStore
from jobbuddy.sync.display import PhaseState
from jobbuddy.sync.embed import EmbedPhase

from conftest import make_job, seed_jobs


def _seed_embed_ready(conninfo: str, slug: str, jobs: list[tuple[str, str]]) -> None:
    """Seed jobs with description_stripped set so EmbedPhase will pick them up."""
    seed_jobs(conninfo, slug, [
        make_job(jid, title=f"{slug}-{jid}", description=desc)
        for jid, desc in jobs
    ])
    store = JobStore(conninfo)
    for jid, _ in jobs:
        row = store.conn.execute(
            "SELECT id FROM jobs WHERE company_slug = %s AND job_id = %s",
            (slug, jid),
        ).fetchone()
        store.update_stripped_description(row["id"], f"stripped text for {slug}-{jid}")
    store.close()


def _fake_embed(texts: list[str]) -> tuple[list[list[float]], int]:
    """Deterministic fake for embed_texts: 1536-dim zero vectors."""
    return [[0.0] * 1536 for _ in texts], len(texts) * 100


class TestEmbedPhase:
    @patch("jobbuddy.sync.embed.embed_texts")
    def test_embeds_all_ready_jobs_exactly_once(self, mock_embed, pg_conninfo):
        """Happy path: each eligible job is embedded exactly once."""
        _seed_embed_ready(pg_conninfo, "acme", [
            ("1", "first job"),
            ("2", "second job"),
            ("3", "third job"),
        ])
        mock_embed.side_effect = _fake_embed

        display = PhaseState("Embed")
        EmbedPhase(pg_conninfo, display=display).run()

        # Collect every text ever passed to embed_texts across all calls
        all_texts: list[str] = []
        for call in mock_embed.call_args_list:
            all_texts.extend(call.args[0])

        assert len(all_texts) == 3, f"expected 3 embeddings, got {len(all_texts)}"
        assert len(set(all_texts)) == 3, "duplicate texts sent to embed_texts"

        store = JobStore(pg_conninfo)
        n = store.conn.execute(
            "SELECT COUNT(*) AS c FROM job_embeddings"
        ).fetchone()["c"]
        store.close()
        assert n == 3

        assert display.done == 3
        assert display.errors == 0
        assert display.status == "idle"

    @patch("jobbuddy.sync.embed.embed_texts")
    @patch("jobbuddy.sync.embed.compute_batch_size", return_value=3)
    def test_no_re_embed_while_upstream_still_running(
        self, mock_bs, mock_embed, pg_conninfo,
    ):
        """REGRESSION: embed phase must not re-embed jobs while upstream_done is unset.

        The old queue+prefetch design would re-dispatch in-flight jobs as a
        fresh batch tuple on each poll, causing duplicate API calls per job.
        Trigger conditions:
          - Small batch size (3) so multiple batches are needed for 10 jobs.
          - Slow embed (0.3s) so the producer re-polls mid-processing.
          - upstream_done unset so the producer loop keeps going instead of
            bailing on the first empty poll.

        Under the old code, the first batch (j1..j3) gets dispatched; before
        the worker commits, the producer re-polls with LIMIT = 3 + len(dispatched),
        gets a different-sized row set (j1..j4), builds a fresh tuple key
        (1,2,3,4) that the in-memory dedupe set doesn't recognize, and queues
        it — re-embedding j1..j3. The simplified single-threaded loop embeds
        each job exactly once regardless of poll frequency.
        """
        _seed_embed_ready(pg_conninfo, "acme", [
            (f"{i}", f"job text {i}") for i in range(10)
        ])

        def slow_embed(texts: list[str]):
            time.sleep(0.3)
            return _fake_embed(texts)

        mock_embed.side_effect = slow_embed

        upstream_done = threading.Event()
        display = PhaseState("Embed")
        phase = EmbedPhase(
            pg_conninfo, display=display, upstream_done=upstream_done,
        )

        def signal_later():
            time.sleep(2.5)
            upstream_done.set()

        threading.Thread(target=signal_later, daemon=True).start()
        phase.run()

        all_texts: list[str] = []
        for call in mock_embed.call_args_list:
            all_texts.extend(call.args[0])

        assert len(all_texts) == 10, (
            f"expected exactly 10 embed_texts entries (one per job), "
            f"got {len(all_texts)} — duplicates indicate the prefetch race is back"
        )
        assert len(set(all_texts)) == 10

        store = JobStore(pg_conninfo)
        n = store.conn.execute(
            "SELECT COUNT(*) AS c FROM job_embeddings"
        ).fetchone()["c"]
        store.close()
        assert n == 10

    @patch("jobbuddy.sync.embed.embed_texts")
    def test_respects_slug_filter(self, mock_embed, pg_conninfo):
        """Only jobs matching the slugs filter get embedded."""
        _seed_embed_ready(pg_conninfo, "acme", [("1", "acme job")])
        _seed_embed_ready(pg_conninfo, "beta", [("2", "beta job")])

        mock_embed.side_effect = _fake_embed

        display = PhaseState("Embed")
        EmbedPhase(pg_conninfo, display=display, slugs=["acme"]).run()

        all_texts: list[str] = []
        for call in mock_embed.call_args_list:
            all_texts.extend(call.args[0])

        assert any("acme" in t for t in all_texts)
        assert not any("beta" in t for t in all_texts)

        store = JobStore(pg_conninfo)
        acme_embeddings = store.conn.execute(
            """SELECT COUNT(*) AS c FROM job_embeddings e
               JOIN jobs j ON e.job_id = j.id
               WHERE j.company_slug = 'acme'"""
        ).fetchone()["c"]
        beta_embeddings = store.conn.execute(
            """SELECT COUNT(*) AS c FROM job_embeddings e
               JOIN jobs j ON e.job_id = j.id
               WHERE j.company_slug = 'beta'"""
        ).fetchone()["c"]
        store.close()
        assert acme_embeddings == 1
        assert beta_embeddings == 0

    @patch("jobbuddy.sync.embed.embed_texts")
    def test_empty_work_no_upstream_returns_immediately(self, mock_embed, pg_conninfo):
        """No work and no upstream producer → phase skips without calling embed."""
        display = PhaseState("Embed")
        EmbedPhase(pg_conninfo, display=display).run()
        mock_embed.assert_not_called()
        # Phase never transitions to active when there's nothing to do
        assert display.status == "pending"

    @patch("jobbuddy.sync.embed.embed_texts")
    def test_empty_work_upstream_done_returns_immediately(self, mock_embed, pg_conninfo):
        """Upstream already finished with zero work → phase exits cleanly."""
        upstream_done = threading.Event()
        upstream_done.set()
        display = PhaseState("Embed")
        EmbedPhase(
            pg_conninfo, display=display, upstream_done=upstream_done,
        ).run()
        mock_embed.assert_not_called()
        assert display.status == "pending"

    @patch("jobbuddy.sync.embed.embed_texts")
    def test_persistent_failure_gives_up_after_max_retries(
        self, mock_embed, pg_conninfo
    ):
        """A job that keeps failing is eventually given up on so the phase can finish."""
        _seed_embed_ready(pg_conninfo, "acme", [("1", "first")])
        mock_embed.side_effect = Exception("API down")

        display = PhaseState("Embed")
        EmbedPhase(pg_conninfo, display=display).run()

        # Phase must complete (not loop forever)
        assert display.status == "idle"
        assert display.errors >= 1

        store = JobStore(pg_conninfo)
        n = store.conn.execute(
            "SELECT COUNT(*) AS c FROM job_embeddings"
        ).fetchone()["c"]
        store.close()
        assert n == 0
        # Retries should cap somewhere reasonable
        assert mock_embed.call_count <= 10

    @patch("jobbuddy.sync.embed.embed_texts")
    def test_poison_pill_does_not_punish_batchmates(self, mock_embed, pg_conninfo):
        """One bad record in a batch must not prevent its healthy batchmates
        from being embedded. Reviewer-requested regression test.

        Old behavior: `_process_batch` failed with one bad job in the batch
        → every job in the batch got `failures[] += 1` → after MAX_RETRIES
        of recurring unordered batches, all batchmates got skipped alongside
        the poison. On a small dataset this silently lost healthy embeddings.

        New behavior: bisection isolates the poison pill; healthy jobs get
        embedded in the half that doesn't contain the bad record, and only
        the culprit accumulates retry count.
        """
        _seed_embed_ready(pg_conninfo, "acme", [
            (str(i), f"job text {i}") for i in range(5)
        ])

        def selective_fail(texts: list[str]):
            # "poison" appears only in job 0's stripped text
            if any("acme-0" in t for t in texts):
                raise Exception("poison pill")
            return _fake_embed(texts)

        mock_embed.side_effect = selective_fail

        display = PhaseState("Embed")
        EmbedPhase(pg_conninfo, display=display).run()

        # All four healthy jobs should end up embedded; only the poison pill
        # gets skipped after MAX_RETRIES.
        store = JobStore(pg_conninfo)
        embedded_ids = {
            r["job_id"] for r in store.conn.execute(
                """SELECT j.job_id FROM job_embeddings e
                   JOIN jobs j ON e.job_id = j.id"""
            ).fetchall()
        }
        store.close()
        assert embedded_ids == {"1", "2", "3", "4"}, (
            f"poison pill took batchmates down with it: embedded={embedded_ids}"
        )
        assert display.status == "idle"

    @patch("jobbuddy.sync.embed.embed_texts")
    def test_transient_failure_then_recovery(self, mock_embed, pg_conninfo):
        """A single transient failure doesn't prevent successful embedding on retry."""
        _seed_embed_ready(pg_conninfo, "acme", [
            ("1", "first"),
            ("2", "second"),
        ])

        call_count = {"n": 0}

        def flaky(texts: list[str]):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("transient network blip")
            return _fake_embed(texts)

        mock_embed.side_effect = flaky

        display = PhaseState("Embed")
        EmbedPhase(pg_conninfo, display=display).run()

        store = JobStore(pg_conninfo)
        n = store.conn.execute(
            "SELECT COUNT(*) AS c FROM job_embeddings"
        ).fetchone()["c"]
        store.close()
        assert n == 2
        assert display.errors == 1
        assert display.done == 2
        assert display.status == "idle"

    @patch("jobbuddy.sync.embed.embed_texts")
    def test_late_arrivals_picked_up_while_upstream_open(
        self, mock_embed, pg_conninfo
    ):
        """Jobs added after the phase starts are picked up before upstream_done."""
        mock_embed.side_effect = _fake_embed

        upstream_done = threading.Event()
        display = PhaseState("Embed")

        # Seed initial work so the phase enters its main loop
        _seed_embed_ready(pg_conninfo, "acme", [("1", "first job")])

        phase = EmbedPhase(
            pg_conninfo, display=display, upstream_done=upstream_done,
        )

        def add_more_then_finish():
            time.sleep(0.3)
            _seed_embed_ready(pg_conninfo, "acme", [("2", "second job")])
            time.sleep(0.3)
            upstream_done.set()

        threading.Thread(target=add_more_then_finish, daemon=True).start()
        phase.run()

        all_texts: list[str] = []
        for call in mock_embed.call_args_list:
            all_texts.extend(call.args[0])
        assert len(all_texts) == 2
        assert len(set(all_texts)) == 2

        store = JobStore(pg_conninfo)
        n = store.conn.execute(
            "SELECT COUNT(*) AS c FROM job_embeddings"
        ).fetchone()["c"]
        store.close()
        assert n == 2

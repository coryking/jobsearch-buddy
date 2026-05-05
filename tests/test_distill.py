"""Tests for the DistillPhase + load/parse helpers.

DB-touching tests use the test PG via the standard fixtures. The OpenAI
client is stubbed end-to-end -- no live LLM calls. The phase's threading
machinery is exercised through its public process_item() rather than a
full run() loop, so workers, queues, and backoff aren't re-tested here
(those belong to WorkerPhase tests)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jobbuddy.models import Job
from jobbuddy.sync.display import PhaseState
from jobbuddy.sync.distill import (
    DISTILL_SCHEMA,
    DistillPhase,
    build_user_message,
    load_distill_prompt,
)
from jobbuddy.types import DistillWorkItem
from tests.conftest import TEST_CONNINFO, make_job


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


def _mk_item(**overrides) -> DistillWorkItem:
    base: DistillWorkItem = {
        "id": 1,
        "company_slug": "acme",
        "company_name": "Acme Corp",
        "job_id": "job-1",
        "title": "Software Engineer",
        "location": "Seattle, WA",
        "salary": None,
        "description": "We are hiring an engineer to build widgets.",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def test_build_user_message_includes_all_xml_tags():
    msg = build_user_message(_mk_item(), company_bio="Acme makes widgets.")
    assert "<title>Software Engineer</title>" in msg
    assert "<company>Acme Corp</company>" in msg
    assert "<location>Seattle, WA</location>" in msg
    assert "<ats_provided_salary>false</ats_provided_salary>" in msg
    assert "<company_bio>Acme makes widgets.</company_bio>" in msg
    assert "<job_description>" in msg and "</job_description>" in msg
    assert "build widgets" in msg


def test_build_user_message_marks_ats_provided_salary_true_when_salary_present():
    msg = build_user_message(_mk_item(salary="$120k"), company_bio="")
    assert "<ats_provided_salary>true</ats_provided_salary>" in msg


def test_build_user_message_treats_whitespace_only_salary_as_absent():
    msg = build_user_message(_mk_item(salary="   "), company_bio="")
    assert "<ats_provided_salary>false</ats_provided_salary>" in msg


def test_build_user_message_handles_null_location():
    msg = build_user_message(_mk_item(location=None), company_bio="")
    assert "<location></location>" in msg


def test_build_user_message_handles_empty_company_bio():
    msg = build_user_message(_mk_item(), company_bio="")
    assert "<company_bio></company_bio>" in msg


def test_load_distill_prompt_reads_v1_from_repo_root():
    prompt = load_distill_prompt("distill-v1")
    # Anchor on stable strings from the prompt -- these define the contract.
    assert "<job_description>" in prompt
    assert "short_jd" in prompt
    assert "description_normalized" in prompt
    assert "salary" in prompt


def test_distill_schema_is_strict_friendly():
    """Strict mode requires every property in `required` and additionalProperties:false."""
    assert DISTILL_SCHEMA["additionalProperties"] is False
    assert set(DISTILL_SCHEMA["required"]) == set(DISTILL_SCHEMA["properties"].keys())


# ---------------------------------------------------------------------------
# DistillPhase.process_item with a stubbed OpenAI client
# ---------------------------------------------------------------------------


class _Recorder:
    """Captures store.update_job_distill calls submitted to the WriteQueue."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def update_job_distill(self, pk, *, short_jd, description_normalized, salary):
        self.calls.append({
            "pk": pk,
            "short_jd": short_jd,
            "description_normalized": description_normalized,
            "salary": salary,
        })


def _make_phase(*, response_content: str, usage_total: int = 100) -> tuple[DistillPhase, _Recorder, MagicMock]:
    phase = DistillPhase(
        TEST_CONNINFO,
        display=PhaseState("Distill"),
        max_workers=1,
    )
    # Bypass on_phase_start side effects.
    phase._system_prompt = "SYSTEM"
    phase._company_bios = {"acme": "Acme makes widgets."}

    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=response_content))],
        usage=SimpleNamespace(total_tokens=usage_total),
    )
    phase._client = client

    recorder = _Recorder()
    phase.submit_write = lambda fn: fn(recorder)  # type: ignore[assignment]
    return phase, recorder, client


def test_process_item_writes_three_fields_on_success():
    phase, recorder, client = _make_phase(
        response_content=json.dumps({
            "short_jd": "Builds widgets in Python.",
            "description_normalized": "Full JD minus boilerplate.",
            "salary": "$120k-$150k",
        }),
    )
    phase.process_item(_mk_item())

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["pk"] == 1
    assert call["short_jd"] == "Builds widgets in Python."
    assert call["description_normalized"] == "Full JD minus boilerplate."
    assert call["salary"] == "$120k-$150k"


def test_process_item_passes_strict_json_schema_and_xml_user_message():
    phase, _, client = _make_phase(
        response_content=json.dumps({
            "short_jd": "x", "description_normalized": "y", "salary": None,
        }),
    )
    phase.process_item(_mk_item())

    kwargs = client.chat.completions.create.call_args.kwargs
    fmt = kwargs["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == DISTILL_SCHEMA

    user_msg = kwargs["messages"][1]["content"]
    assert "<title>Software Engineer</title>" in user_msg
    assert "<company_bio>Acme makes widgets.</company_bio>" in user_msg


def test_process_item_persists_null_salary():
    phase, recorder, _ = _make_phase(
        response_content=json.dumps({
            "short_jd": "x", "description_normalized": "y", "salary": None,
        }),
    )
    phase.process_item(_mk_item(salary="$100k"))
    assert recorder.calls[0]["salary"] is None


def test_process_item_treats_blank_salary_string_as_null():
    phase, recorder, _ = _make_phase(
        response_content=json.dumps({
            "short_jd": "x", "description_normalized": "y", "salary": "  ",
        }),
    )
    phase.process_item(_mk_item())
    assert recorder.calls[0]["salary"] is None


def test_process_item_raises_on_non_json_response():
    phase, recorder, _ = _make_phase(response_content="not json {")
    with pytest.raises(ValueError, match="Non-JSON"):
        phase.process_item(_mk_item())
    assert recorder.calls == []


def test_process_item_raises_on_empty_short_jd():
    phase, recorder, _ = _make_phase(
        response_content=json.dumps({
            "short_jd": "", "description_normalized": "y", "salary": None,
        }),
    )
    with pytest.raises(ValueError, match="Empty distill fields"):
        phase.process_item(_mk_item())
    assert recorder.calls == []


def test_process_item_raises_on_empty_description_normalized():
    phase, recorder, _ = _make_phase(
        response_content=json.dumps({
            "short_jd": "x", "description_normalized": "   ", "salary": None,
        }),
    )
    with pytest.raises(ValueError, match="Empty distill fields"):
        phase.process_item(_mk_item())
    assert recorder.calls == []


def test_process_item_records_usage_tokens():
    phase, _, _ = _make_phase(
        response_content=json.dumps({
            "short_jd": "x", "description_normalized": "y", "salary": None,
        }),
        usage_total=4321,
    )
    phase.process_item(_mk_item())
    assert "tok" in phase.display.info or "k" in phase.display.info  # humanize.metric


# ---------------------------------------------------------------------------
# Store-layer integration (real PG)
# ---------------------------------------------------------------------------


def _seed_distill_jobs(store, slug: str, count: int) -> list[int]:
    """Seed `count` active jobs with descriptions and no short_jd."""
    jobs = [
        make_job(id=f"job-{i}", title=f"Role {i}", description=f"JD body {i}.")
        for i in range(count)
    ]
    store.upsert_jobs(slug, jobs)
    rows = store.conn.execute(
        "SELECT id FROM jobs WHERE company_slug = %s ORDER BY id", (slug,)
    ).fetchall()
    return [r["id"] for r in rows]


def test_count_jobs_needing_distill_counts_only_eligible_rows(store):
    _seed_distill_jobs(store, "acme", 3)
    # Add a job with no description -- ineligible.
    store.upsert_jobs("beta", [make_job(id="no-desc", description=None)])

    assert store.count_jobs_needing_distill() == 3
    assert store.count_jobs_needing_distill(slugs=["acme"]) == 3
    assert store.count_jobs_needing_distill(slugs=["beta"]) == 0


def test_count_jobs_needing_distill_excludes_already_distilled(store):
    pks = _seed_distill_jobs(store, "acme", 2)
    store.update_job_distill(
        pks[0], short_jd="x", description_normalized="y", salary=None,
    )
    assert store.count_jobs_needing_distill() == 1


def test_count_jobs_needing_distill_excludes_removed_listings(store):
    _seed_distill_jobs(store, "acme", 2)
    # Reupsert with empty list to soft-delete both
    store.upsert_jobs("acme", [])
    assert store.count_jobs_needing_distill() == 0


def test_get_jobs_needing_distill_is_deterministic(store):
    _seed_distill_jobs(store, "acme", 5)
    a = store.get_jobs_needing_distill(limit=10)
    b = store.get_jobs_needing_distill(limit=10)
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert [r["id"] for r in a] == sorted(r["id"] for r in a)


def test_get_jobs_needing_distill_joins_company_name(store):
    _seed_distill_jobs(store, "acme", 1)
    rows = store.get_jobs_needing_distill(limit=10)
    assert rows[0]["company_name"] == "Acme Corp"
    assert rows[0]["company_slug"] == "acme"


def test_get_jobs_needing_distill_carries_salary_for_ats_provided_flag(store):
    store.upsert_jobs("acme", [
        make_job(id="with-salary", description="Body.", salary="$100k"),
        make_job(id="no-salary", description="Body."),
    ])
    rows = store.get_jobs_needing_distill(limit=10)
    by_jid = {r["job_id"]: r for r in rows}
    assert by_jid["with-salary"]["salary"] == "$100k"
    assert by_jid["no-salary"]["salary"] is None


def test_update_job_distill_writes_all_three_fields(store):
    pks = _seed_distill_jobs(store, "acme", 1)
    store.update_job_distill(
        pks[0], short_jd="capsule", description_normalized="full",
        salary="$200k",
    )
    row = store.conn.execute(
        "SELECT short_jd, description_normalized, salary FROM jobs WHERE id = %s",
        (pks[0],),
    ).fetchone()
    assert row["short_jd"] == "capsule"
    assert row["description_normalized"] == "full"
    assert row["salary"] == "$200k"


def test_update_job_distill_preserves_existing_salary_when_distill_returns_null(store):
    store.upsert_jobs("acme", [
        make_job(id="with-salary", description="Body.", salary="$ats-provided"),
    ])
    pk = store.conn.execute(
        "SELECT id FROM jobs WHERE company_slug = 'acme' AND job_id = 'with-salary'"
    ).fetchone()["id"]
    store.update_job_distill(
        pk, short_jd="x", description_normalized="y", salary=None,
    )
    row = store.conn.execute(
        "SELECT salary FROM jobs WHERE id = %s", (pk,)
    ).fetchone()
    assert row["salary"] == "$ats-provided"


def test_distill_outputs_invalidated_when_description_body_changes(store):
    """Regression: upsert_jobs nulls short_jd / description_normalized when the
    description body changes, so the distill phase picks the row up again."""
    store.upsert_jobs("acme", [
        make_job(id="job-1", description="original body"),
    ])
    pk = store.conn.execute(
        "SELECT id FROM jobs WHERE job_id = 'job-1'"
    ).fetchone()["id"]
    store.update_job_distill(
        pk, short_jd="x", description_normalized="y", salary=None,
    )
    assert store.count_jobs_needing_distill() == 0

    store.upsert_jobs("acme", [
        make_job(id="job-1", description="updated body"),
    ])
    assert store.count_jobs_needing_distill() == 1

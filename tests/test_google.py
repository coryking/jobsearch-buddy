"""Tests for the Google Careers fetcher."""

import json

import httpx
import pytest

from jobbuddy.fetchers.google import (
    GoogleFetcher,
    build_description,
    build_location,
    extract_tokens,
    parse_batchexecute_response,
    parse_timestamp,
)
from jobbuddy.url import parse_url


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def test_parse_google_url():
    result = parse_url("https://www.google.com/about/careers/applications/jobs/results/101886789328741062")
    assert result is not None
    assert result.ats == "google"
    assert result.job_id == "101886789328741062"
    assert result.board == "google"


def test_parse_google_url_rejects_non_google():
    assert parse_url("https://example.com/about/careers/applications/jobs/results/123") is None


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------

MOCK_HTML = """
<html><head><script>
window.WIZ_global_data = {
  "FdrFJe":"-1234567890",
  "cfb2h":"boq_corp-hiring-boq-cportal-frontend_20260426.07_p0"
};
</script></head></html>
"""


def test_extract_tokens():
    f_sid, bl = extract_tokens(MOCK_HTML)
    assert f_sid == "-1234567890"
    assert bl == "boq_corp-hiring-boq-cportal-frontend_20260426.07_p0"


def test_extract_tokens_missing_sid():
    with pytest.raises(ValueError, match="f.sid"):
        extract_tokens("<html></html>")


def test_extract_tokens_missing_bl():
    html = '<script>"FdrFJe":"-123"</script>'
    with pytest.raises(ValueError, match="build label"):
        extract_tokens(html)


# ---------------------------------------------------------------------------
# Location building
# ---------------------------------------------------------------------------

def test_build_location_single():
    locs = [["Mountain View, CA, USA", ["Mountain View"], None, None, None, "US"]]
    assert build_location(locs) == "Mountain View, CA, USA"


def test_build_location_multiple():
    locs = [
        ["Texas, USA", ["Texas"], None, None, None, "US"],
        ["California, USA", ["California"], None, None, None, "US"],
    ]
    assert build_location(locs) == "Texas, USA | California, USA"


def test_build_location_none():
    assert build_location(None) == ""


def test_build_location_empty():
    assert build_location([]) == ""


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

def test_parse_timestamp_valid():
    assert parse_timestamp([1777024853, 467000000]) == "2026-04-24"


def test_parse_timestamp_none():
    assert parse_timestamp(None) is None
    assert parse_timestamp([None]) is None


def test_parse_timestamp_empty():
    assert parse_timestamp([]) is None


# ---------------------------------------------------------------------------
# Description building
# ---------------------------------------------------------------------------

def _make_entry(**overrides):
    """Build a minimal 21-element job entry array."""
    entry = [None] * 21
    entry[0] = overrides.get("id", "123")
    entry[1] = overrides.get("title", "Test Job")
    entry[2] = overrides.get("apply_url", "https://example.com/apply")
    entry[3] = overrides.get("description", [None, "<p>Job responsibilities</p>"])
    entry[4] = overrides.get("qualifications", [None, "<h3>Minimum qualifications:</h3><ul><li>BS degree</li></ul>"])
    entry[9] = overrides.get("locations", [["Mountain View, CA, USA"]])
    entry[10] = overrides.get("summary", [None, "<p>About the role</p>"])
    entry[12] = overrides.get("timestamp", [1777024853, 467000000])
    entry[18] = overrides.get("workplace", [None, "<b>Remote location: USA</b>"])
    return entry


def test_build_description_full():
    entry = _make_entry()
    desc = build_description(entry)
    assert desc is not None
    assert "About the role" in desc
    assert "Job responsibilities" in desc
    assert "BS degree" in desc
    assert "Remote location: USA" in desc


def test_build_description_missing_sections():
    entry = _make_entry(description=None, qualifications=None, workplace=None)
    desc = build_description(entry)
    assert desc is not None
    assert "About the role" in desc


def test_build_description_all_none():
    entry = _make_entry(summary=None, description=None, qualifications=None, workplace=None)
    desc = build_description(entry)
    assert desc is None


# ---------------------------------------------------------------------------
# Batchexecute response parsing
# ---------------------------------------------------------------------------

def _make_batchexecute_response(jobs_data, total=None, page_size=20):
    """Build a mock batchexecute response string."""
    inner = [jobs_data, None, total, page_size]
    inner_json = json.dumps(inner)
    envelope = json.dumps([["wrb.fr", "r06xKb", inner_json, None, None, None, "generic"]])
    return ")]" + "}'\n\n" + str(len(envelope)) + "\n" + envelope


def test_parse_batchexecute_response():
    entries = [[_make_entry()]]
    raw = _make_batchexecute_response(entries[0], total=1)
    data = parse_batchexecute_response(raw)
    assert len(data[0]) == 1
    assert data[2] == 1


def test_parse_batchexecute_response_empty():
    raw = _make_batchexecute_response([], total=0)
    data = parse_batchexecute_response(raw)
    assert data[0] == []


def test_parse_batchexecute_response_invalid():
    with pytest.raises(ValueError, match="Could not find"):
        parse_batchexecute_response(")]}'\n\n5\n[1,2]")


# ---------------------------------------------------------------------------
# Fetcher construction
# ---------------------------------------------------------------------------

def test_fetcher_defaults():
    f = GoogleFetcher("", name="Google")
    assert f.ats_type == "google"
    assert f.name == "Google"
    assert f.descriptions_in_listing is True


# ---------------------------------------------------------------------------
# Entry → Job mapping
# ---------------------------------------------------------------------------

def test_entry_to_job():
    f = GoogleFetcher("")
    entry = _make_entry(
        id="12345678901234567",
        title="Software Engineer, Cloud",
        apply_url="https://google.com/apply/xyz",
    )
    job = f._entry_to_job(entry)
    assert job.id == "12345678901234567"
    assert job.title == "Software Engineer, Cloud"
    assert job.location == "Mountain View, CA, USA"
    assert "12345678901234567" in job.url
    assert job.apply_url == "https://google.com/apply/xyz"
    assert job.published_at is not None
    assert job.description is not None


def test_entry_to_job_minimal():
    f = GoogleFetcher("")
    entry = [None] * 21
    entry[0] = "999"
    entry[1] = "Intern"
    entry[2] = None
    job = f._entry_to_job(entry)
    assert job.id == "999"
    assert job.title == "Intern"
    assert job.location == ""
    assert "999" in job.url


# ---------------------------------------------------------------------------
# list_jobs (mocked HTTP)
# ---------------------------------------------------------------------------

def _mock_careers_page() -> httpx.Response:
    return httpx.Response(
        200,
        text=MOCK_HTML,
        request=httpx.Request("GET", "https://www.google.com/about/careers/applications/jobs/results"),
    )


def _mock_batchexecute_response(entries, total=None, page_size=20) -> httpx.Response:
    inner = [entries, None, total, page_size]
    inner_json = json.dumps(inner)
    envelope = json.dumps([["wrb.fr", "r06xKb", inner_json, None, None, None, "generic"]])
    body = ")]" + "}'\n\n" + str(len(envelope)) + "\n" + envelope
    return httpx.Response(
        200,
        text=body,
        request=httpx.Request("POST", "https://www.google.com/batchexecute"),
    )


def test_list_jobs_single_page(monkeypatch):
    entries = [_make_entry(id=str(i), title=f"Job {i}") for i in range(5)]
    f = GoogleFetcher("")

    call_count = 0

    def mock_get(*args, **kwargs):
        return _mock_careers_page()

    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_batchexecute_response(entries, total=5)
        return _mock_batchexecute_response([], total=5)

    monkeypatch.setattr(f.client, "get", mock_get)
    monkeypatch.setattr(f.client, "post", mock_post)

    jobs = f.list_jobs()
    assert len(jobs) == 5


def test_list_jobs_pagination(monkeypatch):
    page1 = [_make_entry(id=str(i), title=f"Job {i}") for i in range(20)]
    page2 = [_make_entry(id=str(i), title=f"Job {i}") for i in range(20, 35)]

    f = GoogleFetcher("")
    post_calls = 0

    def mock_get(*args, **kwargs):
        return _mock_careers_page()

    def mock_post(*args, **kwargs):
        nonlocal post_calls
        post_calls += 1
        if post_calls == 1:
            return _mock_batchexecute_response(page1, total=35)
        if post_calls == 2:
            return _mock_batchexecute_response(page2, total=35)
        return _mock_batchexecute_response([], total=35)

    monkeypatch.setattr(f.client, "get", mock_get)
    monkeypatch.setattr(f.client, "post", mock_post)

    progress = []
    jobs = f.list_jobs(on_progress=lambda fetched, total: progress.append((fetched, total)))
    assert len(jobs) == 35
    assert progress[-1][0] == 35


def test_list_jobs_deduplicates(monkeypatch):
    """Pages can have overlapping job IDs; list_jobs should deduplicate."""
    page1 = [_make_entry(id="1"), _make_entry(id="2"), _make_entry(id="3")]
    page2 = [_make_entry(id="3"), _make_entry(id="4")]  # id=3 is a duplicate

    f = GoogleFetcher("")
    post_calls = 0

    def mock_get(*args, **kwargs):
        return _mock_careers_page()

    def mock_post(*args, **kwargs):
        nonlocal post_calls
        post_calls += 1
        if post_calls == 1:
            return _mock_batchexecute_response(page1, total=4)
        if post_calls == 2:
            return _mock_batchexecute_response(page2, total=4)
        return _mock_batchexecute_response([], total=4)

    monkeypatch.setattr(f.client, "get", mock_get)
    monkeypatch.setattr(f.client, "post", mock_post)

    jobs = f.list_jobs()
    assert len(jobs) == 4
    assert {j.id for j in jobs} == {"1", "2", "3", "4"}


def test_list_jobs_unreliable_total(monkeypatch):
    """Google reports a total that's lower than actual job count; should keep paginating."""
    page1 = [_make_entry(id=str(i)) for i in range(20)]
    page2 = [_make_entry(id=str(i)) for i in range(20, 40)]
    page3 = [_make_entry(id=str(i)) for i in range(40, 50)]

    f = GoogleFetcher("")
    post_calls = 0

    def mock_get(*args, **kwargs):
        return _mock_careers_page()

    def mock_post(*args, **kwargs):
        nonlocal post_calls
        post_calls += 1
        if post_calls == 1:
            return _mock_batchexecute_response(page1, total=25)
        if post_calls == 2:
            return _mock_batchexecute_response(page2, total=25)
        if post_calls == 3:
            return _mock_batchexecute_response(page3, total=25)
        return _mock_batchexecute_response([], total=25)

    monkeypatch.setattr(f.client, "get", mock_get)
    monkeypatch.setattr(f.client, "post", mock_post)

    jobs = f.list_jobs()
    assert len(jobs) == 50


# ---------------------------------------------------------------------------
# fetch_job (mocked HTTP)
# ---------------------------------------------------------------------------

def test_fetch_job_found(monkeypatch):
    target = _make_entry(id="42", title="Target Role")
    entries = [_make_entry(id="1"), target, _make_entry(id="99")]

    f = GoogleFetcher("")

    def mock_get(*args, **kwargs):
        return _mock_careers_page()

    def mock_post(*args, **kwargs):
        return _mock_batchexecute_response(entries, total=3)

    monkeypatch.setattr(f.client, "get", mock_get)
    monkeypatch.setattr(f.client, "post", mock_post)

    job = f.fetch_job("42")
    assert job.id == "42"
    assert job.title == "Target Role"


def test_fetch_job_not_found(monkeypatch):
    f = GoogleFetcher("")

    def mock_get(*args, **kwargs):
        return _mock_careers_page()

    def mock_post(*args, **kwargs):
        return _mock_batchexecute_response([], total=0)

    monkeypatch.setattr(f.client, "get", mock_get)
    monkeypatch.setattr(f.client, "post", mock_post)

    with pytest.raises(ValueError, match="not found"):
        f.fetch_job("nonexistent")

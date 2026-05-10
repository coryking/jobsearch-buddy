"""Tests for the Amazon.jobs fetcher."""

import json
from unittest.mock import MagicMock

import httpx
import pytest

from jobbuddy.fetchers.amazon import AmazonFetcher, _parse_epoch
from jobbuddy.url import parse_url


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected_id", [
    ("https://www.amazon.jobs/en/jobs/10406569/software-development-engineer-aws", "10406569"),
    ("https://amazon.jobs/en/jobs/10406569/software-development-engineer-aws", "10406569"),
    ("https://www.amazon.jobs/en-gb/jobs/2345678/some-role-title", "2345678"),
])
def test_parse_amazon_url(url, expected_id):
    result = parse_url(url)
    assert result is not None
    assert result.ats == "amazon"
    assert result.job_id == expected_id
    assert result.board == "amazon"


def test_parse_amazon_url_rejects_non_amazon():
    assert parse_url("https://example.com/en/jobs/12345/foo") is None


# ---------------------------------------------------------------------------
# Epoch date parsing
# ---------------------------------------------------------------------------

def test_parse_epoch_valid():
    assert _parse_epoch("1776450511") is not None

def test_parse_epoch_none():
    assert _parse_epoch(None) is None
    assert _parse_epoch("") is None

def test_parse_epoch_invalid():
    assert _parse_epoch("not-a-number") is None


# ---------------------------------------------------------------------------
# Fetcher construction
# ---------------------------------------------------------------------------

def test_fetcher_defaults():
    f = AmazonFetcher("", name="Amazon")
    assert f.ats_type == "amazon"
    assert f.name == "Amazon"
    assert f.categories == []
    assert f.country == ""
    assert f.descriptions_in_listing is True


def test_fetcher_with_filters():
    f = AmazonFetcher("", categories=["Software Development", "Data Science"], country="US")
    body = f._build_request_body(0, 10)
    filter_names = [fac["name"] for fac in body["filterFacets"]]
    assert "category" in filter_names
    assert "country" in filter_names

    cat_facet = next(f for f in body["filterFacets"] if f["name"] == "category")
    assert len(cat_facet["values"]) == 2


def test_fetcher_no_filters():
    f = AmazonFetcher("")
    body = f._build_request_body(0, 10)
    assert body["filterFacets"] == []


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

SAMPLE_HIT = {
    "fields": {
        "icimsJobId": ["10406569"],
        "title": ["Software Development Engineer, AWS Transform"],
        "city": ["Seattle"],
        "region": ["WA"],
        "country": ["US"],
        "description": ["<p>Some description</p>"],
        "basicQualifications": ["<p>3+ years experience</p>"],
        "preferredQualifications": ["<p>5+ years experience</p>"],
        "category": ["Software Development"],
        "jobFamily": ["Software Development"],
        "createdDate": ["1776450511"],
        "urlNextStep": ["https://account.amazon.jobs/jobs/10406569/apply"],
    }
}


def test_parse_hit():
    f = AmazonFetcher("")
    job = f._parse_hit(SAMPLE_HIT)
    assert job.id == "10406569"
    assert job.title == "Software Development Engineer, AWS Transform"
    assert "Seattle" in job.location
    assert "WA" in job.location
    assert job.department == "Software Development"
    assert job.team == "Software Development"
    assert job.description is not None
    assert "Some description" in job.description
    assert "Basic Qualifications:" in job.description
    assert "Preferred Qualifications:" in job.description
    assert job.url == "https://www.amazon.jobs/en/jobs/10406569/software-development-engineer-aws-transform"
    assert job.apply_url == "https://account.amazon.jobs/jobs/10406569/apply"
    assert job.published_at is not None


def test_parse_hit_extracts_last_listing_update():
    """`updatedDate` epoch field becomes last_listing_update."""
    hit = {"fields": {
        "icimsJobId": ["10406569"],
        "title": ["SDE"],
        "createdDate": ["1700000000"],   # 2023-11-14
        "updatedDate": ["1776450511"],   # 2026-04-17
    }}
    job = AmazonFetcher("")._parse_hit(hit)
    assert job.published_at is not None
    assert job.last_listing_update is not None
    assert job.last_listing_update > job.published_at


def test_parse_hit_no_updated_date_leaves_null():
    hit = {"fields": {
        "icimsJobId": ["1"],
        "title": ["SDE"],
        "createdDate": ["1776450511"],
    }}
    job = AmazonFetcher("")._parse_hit(hit)
    assert job.last_listing_update is None


def test_parse_hit_missing_fields():
    f = AmazonFetcher("")
    hit = {"fields": {"icimsJobId": ["99999"], "title": ["Test Role"]}}
    job = f._parse_hit(hit)
    assert job.id == "99999"
    assert job.title == "Test Role"
    assert job.location == ""
    assert job.description is None


# ---------------------------------------------------------------------------
# list_jobs pagination (mocked HTTP)
# ---------------------------------------------------------------------------

def _make_search_response(hits: list[dict], found: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={"found": found, "searchHits": hits, "start": 0, "facets": []},
        request=httpx.Request("POST", "https://amazon.jobs/api/jobs/search"),
    )


def test_list_jobs_single_page(monkeypatch):
    hits = [
        {"fields": {"icimsJobId": [str(i)], "title": [f"Job {i}"]}}
        for i in range(3)
    ]
    f = AmazonFetcher("", categories=["Software Development"])

    call_count = 0
    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _make_search_response(hits, found=3)

    monkeypatch.setattr(f.client, "post", mock_post)

    jobs = f.list_jobs()
    assert len(jobs) == 3
    assert call_count == 1


def test_list_jobs_pagination(monkeypatch):
    page1_hits = [
        {"fields": {"icimsJobId": [str(i)], "title": [f"Job {i}"]}}
        for i in range(500)
    ]
    page2_hits = [
        {"fields": {"icimsJobId": [str(i)], "title": [f"Job {i}"]}}
        for i in range(500, 700)
    ]

    f = AmazonFetcher("")
    calls = []

    def mock_post(*args, **kwargs):
        body = kwargs.get("json", {})
        calls.append(body.get("start", 0))
        if body.get("start", 0) == 0:
            return _make_search_response(page1_hits, found=700)
        return _make_search_response(page2_hits, found=700)

    monkeypatch.setattr(f.client, "post", mock_post)

    progress_calls = []
    jobs = f.list_jobs(on_progress=lambda fetched, total: progress_calls.append((fetched, total)))
    assert len(jobs) == 700
    assert len(calls) == 2
    assert progress_calls[-1] == (700, 700)


# ---------------------------------------------------------------------------
# fetch_job (mocked HTTP)
# ---------------------------------------------------------------------------

def test_fetch_job_found(monkeypatch):
    hit = {"fields": {"icimsJobId": ["12345"], "title": ["Test Engineer"]}}
    f = AmazonFetcher("")

    def mock_post(*args, **kwargs):
        body = kwargs.get("json", {})
        assert body["query"] == "12345"
        return _make_search_response([hit], found=1)

    monkeypatch.setattr(f.client, "post", mock_post)

    job = f.fetch_job("12345")
    assert job.id == "12345"
    assert job.title == "Test Engineer"


def test_fetch_job_not_found(monkeypatch):
    f = AmazonFetcher("")

    def mock_post(*args, **kwargs):
        return _make_search_response([], found=0)

    monkeypatch.setattr(f.client, "post", mock_post)

    with pytest.raises(ValueError, match="not found"):
        f.fetch_job("99999")


def test_fetch_job_filters_by_id(monkeypatch):
    hits = [
        {"fields": {"icimsJobId": ["111"], "title": ["Wrong Job"]}},
        {"fields": {"icimsJobId": ["222"], "title": ["Right Job"]}},
    ]
    f = AmazonFetcher("")

    def mock_post(*args, **kwargs):
        return _make_search_response(hits, found=2)

    monkeypatch.setattr(f.client, "post", mock_post)

    job = f.fetch_job("222")
    assert job.id == "222"
    assert job.title == "Right Job"

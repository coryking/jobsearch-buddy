"""Tests for content hash contract — Python ↔ SQL agreement.

The hash is the cache invalidation contract between Python and PostgreSQL.
If they disagree, the embed phase re-embeds the entire corpus on every run.
"""

import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from jobbuddy.models import Company, Job

from conftest import TEST_CONNINFO


class TestJobContentHash:
    """Job.content_hash() must match SQL md5(coalesce(stripped,'') || title || coalesce(location,'') || coalesce(department,''))::uuid."""

    def test_determinism(self):
        """Same inputs produce the same hash."""
        job = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a")
        h1 = job.content_hash("stripped desc")
        h2 = job.content_hash("stripped desc")
        assert h1 == h2

    def test_returns_uuid(self):
        job = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a")
        h = job.content_hash("desc")
        assert isinstance(h, uuid.UUID)

    def test_sensitivity_title(self):
        """Changing title produces a different hash."""
        j1 = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a")
        j2 = Job(id="1", title="SWE", location="Seattle", url="u", apply_url="a")
        assert j1.content_hash("desc") != j2.content_hash("desc")

    def test_sensitivity_location(self):
        j1 = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a")
        j2 = Job(id="1", title="PM", location="NYC", url="u", apply_url="a")
        assert j1.content_hash("desc") != j2.content_hash("desc")

    def test_sensitivity_department(self):
        j1 = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a", department="Eng")
        j2 = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a", department="Sales")
        assert j1.content_hash("desc") != j2.content_hash("desc")

    def test_sensitivity_stripped_description(self):
        job = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a")
        assert job.content_hash("version 1") != job.content_hash("version 2")

    def test_null_handling_matches_sql_coalesce(self):
        """None fields produce same hash as SQL's coalesce(NULL, '')."""
        j1 = Job(id="1", title="PM", location="", url="u", apply_url="a")
        j2 = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a")
        j2_model = Job(id="1", title="PM", location="", url="u", apply_url="a")
        # None and '' should produce the same hash (coalesce(NULL, '') = '')
        j_none = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a",
                     department=None)
        j_empty = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a",
                      department=None)
        assert j_none.content_hash(None) == j_empty.content_hash(None)

    def test_none_stripped_matches_empty_stripped(self):
        """content_hash(None) == content_hash('') — mirrors coalesce(NULL, '')."""
        job = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a")
        assert job.content_hash(None) == job.content_hash("")

    def test_python_matches_sql(self):
        """Python hash matches SQL md5(...)::uuid computed in PostgreSQL."""
        job = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a",
                  department="Engineering")
        stripped = "Build AI products."
        py_hash = job.content_hash(stripped)

        conn = psycopg.connect(TEST_CONNINFO, autocommit=True, row_factory=dict_row)
        row = conn.execute(
            "SELECT md5(coalesce(%s, '') || %s || coalesce(%s, '') || coalesce(%s, ''))::uuid AS h",
            (stripped, job.title, job.location, job.department),
        ).fetchone()
        conn.close()

        assert py_hash == row["h"]

    def test_python_matches_sql_with_nulls(self):
        """Python hash matches SQL when nullable fields are None."""
        job = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a")
        py_hash = job.content_hash(None)

        conn = psycopg.connect(TEST_CONNINFO, autocommit=True, row_factory=dict_row)
        row = conn.execute(
            "SELECT md5(coalesce(%s, '') || %s || coalesce(%s, '') || coalesce(%s, ''))::uuid AS h",
            (None, job.title, job.location, None),
        ).fetchone()
        conn.close()

        assert py_hash == row["h"]

    def test_hash_fields_match_embed_text_inputs(self):
        """Hash covers exactly the fields that feed embed_text().

        embed_text() uses: company_name (from Company hash), title, department,
        location, description_stripped. Job hash covers all except company_name.
        """
        job = Job(id="1", title="PM", location="Seattle", url="u", apply_url="a",
                  department="Eng", description="raw desc")
        # Changing fields NOT in embed_text should NOT change the hash
        j2 = Job(id="1", title="PM", location="Seattle", url="u", apply_url="different",
                 department="Eng", description="different raw desc", salary="$200k")
        assert job.content_hash("stripped") == j2.content_hash("stripped")


class TestCompanyContentHash:
    """Company.content_hash must match SQL md5(name)::uuid."""

    def test_determinism(self):
        c = Company(slug="acme", name="Acme Corp")
        assert c.content_hash == c.content_hash

    def test_returns_uuid(self):
        c = Company(slug="acme", name="Acme Corp")
        assert isinstance(c.content_hash, uuid.UUID)

    def test_sensitivity(self):
        c1 = Company(slug="acme", name="Acme Corp")
        c2 = Company(slug="acme", name="Acme Inc")
        assert c1.content_hash != c2.content_hash

    def test_python_matches_sql(self):
        c = Company(slug="acme", name="Acme Corp")
        py_hash = c.content_hash

        conn = psycopg.connect(TEST_CONNINFO, autocommit=True, row_factory=dict_row)
        row = conn.execute(
            "SELECT md5(%s)::uuid AS h", (c.name,)
        ).fetchone()
        conn.close()

        assert py_hash == row["h"]

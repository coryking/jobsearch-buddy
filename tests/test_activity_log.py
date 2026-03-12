"""Tests for activity log PostgreSQL operations."""

from datetime import date

from jobbuddy.store import JobStore


class TestAppendActivity:
    def test_basic_append(self, store: JobStore):
        row = store.append_activity("Acme Corp", "Engineer", "Application")
        assert row["company"] == "Acme Corp"
        assert row["role"] == "Engineer"
        assert row["action"] == "Application"
        assert row["date"] == date.today().isoformat()
        # Optional fields default to empty string (consumer contract)
        assert row["job_id"] == ""
        assert row["person"] == ""
        assert row["url"] == ""
        assert row["notes"] == ""

    def test_append_with_all_fields(self, store: JobStore):
        row = store.append_activity(
            "Acme Corp",
            "Engineer",
            "Screen",
            job_id="123",
            person="Jane",
            location="Seattle",
            status="Active",
            url="https://example.com/jobs/123",
            notes="Phone screen scheduled",
            row_date="2026-01-15",
        )
        assert row["date"] == "2026-01-15"
        assert row["job_id"] == "123"
        assert row["person"] == "Jane"
        assert row["location"] == "Seattle"
        assert row["status"] == "Active"
        assert row["url"] == "https://example.com/jobs/123"
        assert row["notes"] == "Phone screen scheduled"

    def test_append_returns_all_keys(self, store: JobStore):
        row = store.append_activity("Co", "Role", "Apply")
        expected_keys = {"date", "company", "role", "job_id", "action", "person", "location", "status", "url", "notes"}
        assert set(row.keys()) == expected_keys

    def test_duplicates_allowed(self, store: JobStore):
        store.append_activity("Acme", "Eng", "Application", url="https://example.com/1")
        store.append_activity("Acme", "Eng", "Follow-up", url="https://example.com/1")
        rows = store.read_activity_log()
        assert len(rows) == 2


class TestReadActivityLog:
    def test_empty_log(self, store: JobStore):
        rows = store.read_activity_log()
        assert rows == []

    def test_returns_all_rows_ordered_by_date(self, store: JobStore):
        store.append_activity("A", "R1", "Apply", row_date="2026-01-10")
        store.append_activity("B", "R2", "Apply", row_date="2026-01-15")
        store.append_activity("C", "R3", "Apply", row_date="2026-01-12")
        rows = store.read_activity_log()
        assert len(rows) == 3
        # Ordered by date descending (most recent first)
        assert rows[0]["company"] == "B"
        assert rows[1]["company"] == "C"
        assert rows[2]["company"] == "A"

    def test_null_fields_returned_as_empty_string(self, store: JobStore):
        store.append_activity("Co", "Role", "Apply")
        row = store.read_activity_log()[0]
        for key in ["job_id", "person", "location", "status", "url", "notes"]:
            assert row[key] == "", f"Expected empty string for {key}, got {row[key]!r}"


class TestFindActivityDuplicates:
    def test_find_by_url(self, store: JobStore):
        store.append_activity("A", "R1", "Apply", url="https://example.com/1")
        store.append_activity("B", "R2", "Apply", url="https://example.com/2")
        dupes = store.find_activity_duplicates(url="https://example.com/1")
        assert len(dupes) == 1
        assert dupes[0]["company"] == "A"

    def test_find_by_company_and_role(self, store: JobStore):
        store.append_activity("Acme", "Engineer", "Apply")
        store.append_activity("Acme", "Designer", "Apply")
        dupes = store.find_activity_duplicates(company="Acme", role="Engineer")
        assert len(dupes) == 1
        assert dupes[0]["role"] == "Engineer"

    def test_case_insensitive_company_role(self, store: JobStore):
        store.append_activity("Acme Corp", "Senior Engineer", "Apply")
        dupes = store.find_activity_duplicates(company="acme corp", role="senior engineer")
        assert len(dupes) == 1

    def test_no_matches(self, store: JobStore):
        store.append_activity("A", "R", "Apply")
        assert store.find_activity_duplicates(url="https://nope.com") == []
        assert store.find_activity_duplicates(company="Z", role="X") == []


class TestFindActivityByCompany:
    def test_finds_matching_company(self, store: JobStore):
        store.append_activity("Acme", "Eng", "Apply")
        store.append_activity("Acme", "PM", "Screen")
        store.append_activity("Other", "Eng", "Apply")
        rows = store.find_activity_by_company("Acme")
        assert len(rows) == 2

    def test_case_insensitive(self, store: JobStore):
        store.append_activity("Acme Corp", "Eng", "Apply")
        rows = store.find_activity_by_company("acme corp")
        assert len(rows) == 1


class TestUniqueActivityCompanies:
    def test_returns_unique_names(self, store: JobStore):
        store.append_activity("Acme", "Eng", "Apply")
        store.append_activity("Acme", "PM", "Apply")
        store.append_activity("BigCo", "Eng", "Apply")
        companies = store.unique_activity_companies()
        assert companies == {"Acme", "BigCo"}

    def test_empty_log(self, store: JobStore):
        assert store.unique_activity_companies() == set()

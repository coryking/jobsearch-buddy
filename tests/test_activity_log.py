"""Tests for activity log PostgreSQL operations.

Every read and write is account-scoped. Cross-account isolation is the
load-bearing invariant — a test that demonstrates one account's writes
not leaking into another account's reads is more important than any
single field-level assertion.
"""

from datetime import date

from jobbuddy.models import Account
from jobbuddy.store import JobStore


class TestAppendActivity:
    def test_basic_append(self, store: JobStore, test_account: Account):
        row = store.append_activity(test_account.id, "Acme Corp", "Engineer", "Application")
        assert row["company"] == "Acme Corp"
        assert row["role"] == "Engineer"
        assert row["action"] == "Application"
        assert row["date"] == date.today().isoformat()
        # Optional fields default to empty string (consumer contract)
        assert row["job_id"] == ""
        assert row["person"] == ""
        assert row["url"] == ""
        assert row["notes"] == ""

    def test_append_with_all_fields(self, store: JobStore, test_account: Account):
        row = store.append_activity(
            test_account.id,
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

    def test_append_returns_all_keys(self, store: JobStore, test_account: Account):
        row = store.append_activity(test_account.id, "Co", "Role", "Apply")
        expected_keys = {"date", "company", "role", "job_id", "action", "person", "location", "status", "url", "notes"}
        assert set(row.keys()) == expected_keys

    def test_duplicates_allowed(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme", "Eng", "Application", url="https://example.com/1")
        store.append_activity(test_account.id, "Acme", "Eng", "Follow-up", url="https://example.com/1")
        rows = store.read_activity_log(test_account.id)
        assert len(rows) == 2


class TestReadActivityLog:
    def test_empty_log(self, store: JobStore, test_account: Account):
        assert store.read_activity_log(test_account.id) == []

    def test_returns_all_rows_ordered_by_date(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "A", "R1", "Apply", row_date="2026-01-10")
        store.append_activity(test_account.id, "B", "R2", "Apply", row_date="2026-01-15")
        store.append_activity(test_account.id, "C", "R3", "Apply", row_date="2026-01-12")
        rows = store.read_activity_log(test_account.id)
        assert len(rows) == 3
        # Ordered by date descending (most recent first)
        assert rows[0]["company"] == "B"
        assert rows[1]["company"] == "C"
        assert rows[2]["company"] == "A"

    def test_null_fields_returned_as_empty_string(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Co", "Role", "Apply")
        row = store.read_activity_log(test_account.id)[0]
        for key in ["job_id", "person", "location", "status", "url", "notes"]:
            assert row[key] == "", f"Expected empty string for {key}, got {row[key]!r}"


class TestFindActivityDuplicates:
    def test_find_by_url(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "A", "R1", "Apply", url="https://example.com/1")
        store.append_activity(test_account.id, "B", "R2", "Apply", url="https://example.com/2")
        dupes = store.find_activity_duplicates(test_account.id, url="https://example.com/1")
        assert len(dupes) == 1
        assert dupes[0]["company"] == "A"

    def test_find_by_company_and_role(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme", "Engineer", "Apply")
        store.append_activity(test_account.id, "Acme", "Designer", "Apply")
        dupes = store.find_activity_duplicates(test_account.id, company="Acme", role="Engineer")
        assert len(dupes) == 1
        assert dupes[0]["role"] == "Engineer"

    def test_case_insensitive_company_role(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme Corp", "Senior Engineer", "Apply")
        dupes = store.find_activity_duplicates(test_account.id, company="acme corp", role="senior engineer")
        assert len(dupes) == 1

    def test_no_matches(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "A", "R", "Apply")
        assert store.find_activity_duplicates(test_account.id, url="https://nope.com") == []
        assert store.find_activity_duplicates(test_account.id, company="Z", role="X") == []


class TestFindActivityByCompany:
    def test_finds_matching_company(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme", "Eng", "Apply")
        store.append_activity(test_account.id, "Acme", "PM", "Screen")
        store.append_activity(test_account.id, "Other", "Eng", "Apply")
        rows = store.find_activity_by_company(test_account.id, "Acme")
        assert len(rows) == 2

    def test_case_insensitive(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme Corp", "Eng", "Apply")
        rows = store.find_activity_by_company(test_account.id, "acme corp")
        assert len(rows) == 1


class TestUniqueActivityCompanies:
    def test_returns_unique_names(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme", "Eng", "Apply")
        store.append_activity(test_account.id, "Acme", "PM", "Apply")
        store.append_activity(test_account.id, "BigCo", "Eng", "Apply")
        companies = store.unique_activity_companies(test_account.id)
        assert companies == {"Acme", "BigCo"}

    def test_empty_log(self, store: JobStore, test_account: Account):
        assert store.unique_activity_companies(test_account.id) == set()


class TestApplicationCountsByCompany:
    def test_only_application_action_counts(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme", "Eng", "Application")
        store.append_activity(test_account.id, "Acme", "Eng", "Screen")
        store.append_activity(test_account.id, "Acme", "PM", "Application")
        store.append_activity(test_account.id, "BigCo", "Eng", "Application")
        counts = store.application_counts_by_company(test_account.id)
        assert counts == {"acme": 2, "bigco": 1}

    def test_keys_lowercased(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "MixedCase Corp", "Eng", "Application")
        counts = store.application_counts_by_company(test_account.id)
        assert counts == {"mixedcase corp": 1}

    def test_empty_log(self, store: JobStore, test_account: Account):
        assert store.application_counts_by_company(test_account.id) == {}


class TestCrossAccountIsolation:
    """Two distinct accounts must never see each other's activity_log rows."""

    def _other_account(self, store: JobStore) -> Account:
        return store.upsert_account_from_claims(
            "github",
            {"sub": "999", "login": "otheruser", "name": "Other User", "email": None},
        )

    def test_read_does_not_leak(self, store: JobStore, test_account: Account):
        other = self._other_account(store)
        store.append_activity(test_account.id, "Mine Co", "Eng", "Application")
        store.append_activity(other.id, "Theirs Co", "Eng", "Application")

        mine = store.read_activity_log(test_account.id)
        theirs = store.read_activity_log(other.id)
        assert {r["company"] for r in mine} == {"Mine Co"}
        assert {r["company"] for r in theirs} == {"Theirs Co"}

    def test_duplicate_check_is_per_account(self, store: JobStore, test_account: Account):
        """Same URL logged by another account must NOT show up as a
        duplicate. Otherwise we'd leak account A's URLs to account B
        through the duplicate-warning path."""
        other = self._other_account(store)
        store.append_activity(other.id, "Theirs Co", "Eng", "Application", url="https://example.com/shared")

        dupes = store.find_activity_duplicates(test_account.id, url="https://example.com/shared")
        assert dupes == []

    def test_find_by_company_is_per_account(self, store: JobStore, test_account: Account):
        other = self._other_account(store)
        store.append_activity(other.id, "Acme", "Eng", "Application")

        assert store.find_activity_by_company(test_account.id, "Acme") == []
        assert len(store.find_activity_by_company(other.id, "Acme")) == 1

    def test_unique_companies_is_per_account(self, store: JobStore, test_account: Account):
        other = self._other_account(store)
        store.append_activity(test_account.id, "Mine", "Eng", "Application")
        store.append_activity(other.id, "Theirs", "Eng", "Application")

        assert store.unique_activity_companies(test_account.id) == {"Mine"}
        assert store.unique_activity_companies(other.id) == {"Theirs"}

    def test_application_counts_is_per_account(self, store: JobStore, test_account: Account):
        other = self._other_account(store)
        store.append_activity(test_account.id, "Acme", "Eng", "Application")
        store.append_activity(other.id, "Acme", "Eng", "Application")
        store.append_activity(other.id, "Acme", "PM", "Application")

        assert store.application_counts_by_company(test_account.id) == {"acme": 1}
        assert store.application_counts_by_company(other.id) == {"acme": 2}

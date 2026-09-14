"""Tests for activity log PostgreSQL operations.

Every read and write is account-scoped. Cross-account isolation is the
load-bearing invariant — a test that demonstrates one account's writes
not leaking into another account's reads is more important than any
single field-level assertion.
"""

from datetime import date

from jobbuddy.models import Account, ActivityAging, ActivityTimeline
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
        row = store.append_activity(test_account.id, "Co", "Role", "Application")
        expected_keys = {"date", "company", "role", "job_id", "action", "person", "location", "status", "url", "notes"}
        assert set(row.keys()) == expected_keys

    def test_duplicates_allowed(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme", "Eng", "Application", url="https://example.com/1")
        store.append_activity(test_account.id, "Acme", "Eng", "Contact", url="https://example.com/1")
        rows = store.read_activity_log(test_account.id)
        assert len(rows) == 2


class TestReadActivityLog:
    def test_empty_log(self, store: JobStore, test_account: Account):
        assert store.read_activity_log(test_account.id) == []

    def test_returns_all_rows_ordered_by_date(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "A", "R1", "Application", row_date="2026-01-10")
        store.append_activity(test_account.id, "B", "R2", "Application", row_date="2026-01-15")
        store.append_activity(test_account.id, "C", "R3", "Application", row_date="2026-01-12")
        rows = store.read_activity_log(test_account.id)
        assert len(rows) == 3
        # Ordered by date descending (most recent first)
        assert rows[0]["company"] == "B"
        assert rows[1]["company"] == "C"
        assert rows[2]["company"] == "A"

    def test_null_fields_returned_as_empty_string(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Co", "Role", "Application")
        row = store.read_activity_log(test_account.id)[0]
        for key in ["job_id", "person", "location", "status", "url", "notes"]:
            assert row[key] == "", f"Expected empty string for {key}, got {row[key]!r}"


class TestFindActivityDuplicates:
    def test_find_by_url(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "A", "R1", "Application", url="https://example.com/1")
        store.append_activity(test_account.id, "B", "R2", "Application", url="https://example.com/2")
        dupes = store.find_activity_duplicates(test_account.id, url="https://example.com/1")
        assert len(dupes) == 1
        assert dupes[0]["company"] == "A"

    def test_find_by_company_and_role(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme", "Engineer", "Application")
        store.append_activity(test_account.id, "Acme", "Designer", "Application")
        dupes = store.find_activity_duplicates(test_account.id, company="Acme", role="Engineer")
        assert len(dupes) == 1
        assert dupes[0]["role"] == "Engineer"

    def test_case_insensitive_company_role(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme Corp", "Senior Engineer", "Application")
        dupes = store.find_activity_duplicates(test_account.id, company="acme corp", role="senior engineer")
        assert len(dupes) == 1

    def test_no_matches(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "A", "R", "Application")
        assert store.find_activity_duplicates(test_account.id, url="https://nope.com") == []
        assert store.find_activity_duplicates(test_account.id, company="Z", role="X") == []


class TestFindActivityByCompany:
    def test_finds_matching_company(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme", "Eng", "Application")
        store.append_activity(test_account.id, "Acme", "PM", "Screen")
        store.append_activity(test_account.id, "Other", "Eng", "Application")
        rows = store.find_activity_by_company(test_account.id, "Acme")
        assert len(rows) == 2

    def test_case_insensitive(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme Corp", "Eng", "Application")
        rows = store.find_activity_by_company(test_account.id, "acme corp")
        assert len(rows) == 1


class TestUniqueActivityCompanies:
    def test_returns_unique_names(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Acme", "Eng", "Application")
        store.append_activity(test_account.id, "Acme", "PM", "Application")
        store.append_activity(test_account.id, "BigCo", "Eng", "Application")
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


class TestReadActivityLogFiltered:
    """Store-level since and action filters on read_activity_log."""

    def test_filter_by_since(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "Old", "Eng", "Application", row_date="2025-01-01")
        store.append_activity(test_account.id, "New", "Eng", "Application", row_date="2026-06-01")
        rows = store.read_activity_log(test_account.id, since=date(2026, 1, 1))
        assert len(rows) == 1
        assert rows[0]["company"] == "New"

    def test_filter_by_action(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "A", "Eng", "Application")
        store.append_activity(test_account.id, "B", "Eng", "Screen")
        store.append_activity(test_account.id, "C", "PM", "Application")
        rows = store.read_activity_log(test_account.id, action="Screen")
        assert len(rows) == 1
        assert rows[0]["company"] == "B"

    def test_filter_by_since_and_action(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "A", "Eng", "Application", row_date="2025-01-01")
        store.append_activity(test_account.id, "B", "Eng", "Screen", row_date="2026-06-01")
        store.append_activity(test_account.id, "C", "PM", "Application", row_date="2026-06-01")
        rows = store.read_activity_log(test_account.id, since=date(2026, 1, 1), action="Application")
        assert len(rows) == 1
        assert rows[0]["company"] == "C"

    def test_no_filters_returns_all(self, store: JobStore, test_account: Account):
        store.append_activity(test_account.id, "A", "Eng", "Application")
        store.append_activity(test_account.id, "B", "Eng", "Screen")
        rows = store.read_activity_log(test_account.id)
        assert len(rows) == 2

    def test_since_inclusive(self, store: JobStore, test_account: Account):
        """The since date itself is included (>=)."""
        store.append_activity(test_account.id, "Exact", "Eng", "Application", row_date="2026-03-15")
        store.append_activity(test_account.id, "Before", "Eng", "Application", row_date="2026-03-14")
        rows = store.read_activity_log(test_account.id, since=date(2026, 3, 15))
        assert len(rows) == 1
        assert rows[0]["company"] == "Exact"


class TestActivityAging:
    """Model bucketing: companies grouped by days since last touch."""

    def test_bucket_assignment(self):
        """Companies land in the correct time-range bucket."""
        today = date(2026, 9, 14)
        by_company = {
            "Recent": [
                {"date": "2026-09-10", "action": "Screen", "company": "Recent"},
            ],
            "Month": [
                {"date": "2026-08-10", "action": "Contact", "company": "Month"},
            ],
            "Quarter": [
                {"date": "2026-07-01", "action": "Application", "company": "Quarter"},
            ],
            "Old": [
                {"date": "2026-01-01", "action": "Interview", "company": "Old"},
            ],
        }
        aging = ActivityAging.from_log(by_company, today=today)
        result = aging.to_mcp_result()

        # Check all bucket headers present
        assert "## 0-30 days" in result
        assert "## 31-60 days" in result
        assert "## 61-90 days" in result
        assert "## 90+ days" in result

        # Recent (4 days ago) in 0-30
        assert "Recent" in result.split("## 0-30 days")[1].split("##")[0]
        # Month (~35 days ago) in 31-60
        assert "Month" in result.split("## 31-60 days")[1].split("##")[0]
        # Quarter (~75 days ago) in 61-90
        assert "Quarter" in result.split("## 61-90 days")[1].split("##")[0]
        # Old (~256 days ago) in 90+
        assert "Old" in result.split("## 90+ days")[1]

    def test_sort_within_bucket(self):
        """Within a bucket, companies sorted by last_activity desc."""
        today = date(2026, 9, 14)
        by_company = {
            "Earlier": [
                {"date": "2026-09-01", "action": "Screen", "company": "Earlier"},
            ],
            "Later": [
                {"date": "2026-09-10", "action": "Contact", "company": "Later"},
            ],
        }
        aging = ActivityAging.from_log(by_company, today=today)
        result = aging.to_mcp_result()
        bucket_0_30 = result.split("## 0-30 days")[1].split("##")[0]
        later_pos = bucket_0_30.index("Later")
        earlier_pos = bucket_0_30.index("Earlier")
        assert later_pos < earlier_pos

    def test_last_action_is_most_recent(self):
        """last_action reflects the action from the most recent date."""
        today = date(2026, 9, 14)
        by_company = {
            "Acme": [
                {"date": "2026-09-01", "action": "Application", "company": "Acme"},
                {"date": "2026-09-10", "action": "Screen", "company": "Acme"},
                {"date": "2026-09-05", "action": "Contact", "company": "Acme"},
            ],
        }
        aging = ActivityAging.from_log(by_company, today=today)
        result = aging.to_mcp_result()
        # The last_action for Acme should be Screen (from 09-10)
        assert "Acme,3,2026-09-10,Screen" in result

    def test_summary_line(self):
        """Header line counts total activities and companies."""
        today = date(2026, 9, 14)
        by_company = {
            "A": [
                {"date": "2026-09-10", "action": "Screen", "company": "A"},
                {"date": "2026-09-05", "action": "Contact", "company": "A"},
            ],
            "B": [
                {"date": "2026-09-01", "action": "Application", "company": "B"},
            ],
        }
        aging = ActivityAging.from_log(by_company, today=today)
        result = aging.to_mcp_result()
        assert "3 activities across 2 companies" in result

    def test_empty_buckets_omitted(self):
        """Buckets with no companies are not rendered."""
        today = date(2026, 9, 14)
        by_company = {
            "Recent": [
                {"date": "2026-09-10", "action": "Screen", "company": "Recent"},
            ],
        }
        aging = ActivityAging.from_log(by_company, today=today)
        result = aging.to_mcp_result()
        assert "## 0-30 days" in result
        assert "## 31-60 days" not in result
        assert "## 61-90 days" not in result
        assert "## 90+ days" not in result

    def test_empty_log(self):
        """Empty input yields a clean message."""
        aging = ActivityAging.from_log({}, today=date(2026, 9, 14))
        result = aging.to_mcp_result()
        assert "0 activities across 0 companies" in result

    def test_bucket_boundaries(self):
        """Exact boundary days land in the correct bucket (no overlap)."""
        today = date(2026, 9, 14)
        by_company = {
            "Day30": [{"date": "2026-08-15", "action": "Application", "company": "Day30"}],
            "Day31": [{"date": "2026-08-14", "action": "Application", "company": "Day31"}],
            "Day60": [{"date": "2026-07-16", "action": "Application", "company": "Day60"}],
            "Day61": [{"date": "2026-07-15", "action": "Application", "company": "Day61"}],
            "Day90": [{"date": "2026-06-16", "action": "Application", "company": "Day90"}],
            "Day91": [{"date": "2026-06-15", "action": "Application", "company": "Day91"}],
        }
        aging = ActivityAging.from_log(by_company, today=today)
        result = aging.to_mcp_result()
        bucket_0_30 = result.split("## 0-30 days")[1].split("##")[0]
        bucket_31_60 = result.split("## 31-60 days")[1].split("##")[0]
        bucket_61_90 = result.split("## 61-90 days")[1].split("##")[0]
        bucket_90_plus = result.split("## 90+ days")[1]
        assert "Day30" in bucket_0_30
        assert "Day31" in bucket_31_60
        assert "Day60" in bucket_31_60
        assert "Day61" in bucket_61_90
        assert "Day90" in bucket_61_90
        assert "Day91" in bucket_90_plus


class TestActivityTimeline:
    """Flat reverse-chron timeline with 5 columns."""

    def test_columns(self):
        rows = [
            {"date": "2026-09-10", "company": "Acme", "role": "Engineer", "action": "Screen", "person": "Jane"},
        ]
        timeline = ActivityTimeline.from_rows(rows)
        result = timeline.to_mcp_result()
        # Header row
        assert "date,company,role,action,person" in result
        assert "2026-09-10,Acme,Engineer,Screen,Jane" in result

    def test_empty_rows(self):
        timeline = ActivityTimeline.from_rows([])
        result = timeline.to_mcp_result()
        assert "0 activities" in result

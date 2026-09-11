"""Tests for jobbuddy.models utilities."""

from datetime import date, datetime, timezone

from jobbuddy.models import parse_published_at, strip_html


class TestParsePublishedAt:
    """parse_published_at is the shared coercion point for ATS-supplied
    date-ish values. Five fetchers depend on it; bad behavior here ripples."""

    def test_none_passthrough(self):
        assert parse_published_at(None) is None

    def test_date_passthrough(self):
        d = date(2026, 3, 4)
        assert parse_published_at(d) == d

    def test_datetime_truncates_to_date(self):
        dt = datetime(2026, 3, 4, 18, 30, tzinfo=timezone.utc)
        assert parse_published_at(dt) == date(2026, 3, 4)

    def test_iso_date_string(self):
        assert parse_published_at("2026-03-04") == date(2026, 3, 4)

    def test_iso_timestamp_string_truncates(self):
        assert parse_published_at("2026-03-04T18:30:00Z") == date(2026, 3, 4)

    def test_epoch_seconds(self):
        # 2026-04-24 ~ midday UTC
        assert parse_published_at(1777024853) == date(2026, 4, 24)

    def test_epoch_float(self):
        assert parse_published_at(1777024853.467) == date(2026, 4, 24)

    def test_garbage_string_returns_none(self):
        assert parse_published_at("not a date") is None

    def test_empty_string_returns_none(self):
        assert parse_published_at("") is None

    def test_short_year_only_returns_none(self):
        # Old paylocity _parse_date passed "2026" through; new contract:
        # anything not a full YYYY-MM-DD prefix is None. Junk-as-valid
        # is worse than missing.
        assert parse_published_at("2026") is None


class TestStripHtmlLists:
    """20 of 24 fetchers pipe ATS description HTML through strip_html, and
    `get_job` hands the result to the calling LLM as the evidence payload it
    ranks against (docs/NORTH_STAR.md). Requirement lists are the structure
    being ranked, so flattening them to prose is a data-quality bug, not a
    formatting nit. See issue #89."""

    def test_list_items_get_bullet_markers(self):
        html = "<ul><li>Own an account list</li><li>Close deals</li></ul>"
        assert strip_html(html) == "- Own an account list\n- Close deals"

    def test_ordered_list_items_also_get_bullets(self):
        html = "<ol><li>Apply</li><li>Interview</li></ol>"
        assert strip_html(html) == "- Apply\n- Interview"

    def test_nested_list_is_indented_not_flattened(self):
        # A sub-bullet qualifying its parent must not become a sibling
        # assertion at the same level -- that changes meaning.
        html = (
            "<ul><li>Requirements"
            "<ul><li>5 years Python</li><li>Distributed systems</li></ul>"
            "</li><li>Nice to have</li></ul>"
        )
        assert strip_html(html) == (
            "- Requirements\n"
            "  - 5 years Python\n"
            "  - Distributed systems\n"
            "- Nice to have"
        )

    def test_three_levels_of_nesting(self):
        html = "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul>"
        assert strip_html(html) == "- a\n  - b\n    - c"

    def test_source_whitespace_inside_li_is_not_kept_after_the_bullet(self):
        # Real ATS HTML is pretty-printed; the newline after <li> must not
        # push the item text off its own bullet line.
        html = "<ul>\n  <li>\n    Own a named account list\n  </li>\n</ul>"
        assert strip_html(html) == "- Own a named account list"

    def test_empty_list_item_leaves_no_dangling_bullet(self):
        html = "<ul><li>Real item</li><li></li></ul>"
        assert strip_html(html) == "- Real item"

    def test_heading_then_list_keeps_them_on_separate_lines(self):
        html = "<h3>Responsibilities</h3><ul><li>One</li><li>Two</li></ul>"
        assert strip_html(html) == "Responsibilities\n\n- One\n- Two"

    def test_paragraphs_still_separated_by_blank_line(self):
        # Non-list behavior must not regress.
        html = "<p>First para.</p><p>Second para.</p>"
        assert strip_html(html) == "First para.\n\nSecond para."

    def test_unbalanced_close_tag_does_not_break_depth(self):
        html = "</ul><ul><li>Still one level</li></ul>"
        assert strip_html(html) == "- Still one level"

    def test_li_outside_any_list_still_gets_a_bullet(self):
        assert strip_html("<li>Orphan</li>") == "- Orphan"


class TestStripHtmlLinks:
    """`<a>` was not handled at all: link text survived, the href was
    discarded. A JD pointing at a benefits doc or a team blog lost the
    target entirely. See issue #89."""

    def test_http_link_target_is_inlined(self):
        html = '<p>See <a href="https://example.com/benefits">our benefits</a>.</p>'
        assert strip_html(html) == "See [our benefits](https://example.com/benefits)."

    def test_link_with_no_text_falls_back_to_the_url(self):
        html = '<a href="https://example.com/apply"></a>'
        assert strip_html(html) == "https://example.com/apply"

    def test_link_whose_text_is_the_url_is_not_duplicated(self):
        html = '<a href="https://example.com">https://example.com</a>'
        assert strip_html(html) == "https://example.com"

    def test_anchor_without_href_passes_text_through(self):
        assert strip_html('<a name="top">Top</a>') == "Top"

    def test_relative_and_fragment_hrefs_are_dropped(self):
        # A relative target is meaningless without the base URL; emitting it
        # is noise, not evidence.
        assert strip_html('<a href="/careers">Careers</a>') == "Careers"
        assert strip_html('<a href="#apply">Apply</a>') == "Apply"

    def test_mailto_and_tel_are_dropped_not_inlined(self):
        # Recruiter contact details are PII the pipeline is meant to strip;
        # inlining them here would *introduce* it into every JD.
        html = '<a href="mailto:recruiter@example.com">Contact us</a>'
        assert strip_html(html) == "Contact us"
        assert strip_html('<a href="tel:+15555550100">Call</a>') == "Call"

    def test_link_inside_a_list_item_keeps_both_bullet_and_target(self):
        html = '<ul><li>Read the <a href="https://example.com/blog">blog</a></li></ul>'
        assert strip_html(html) == "- Read the [blog](https://example.com/blog)"

    def test_unclosed_anchor_does_not_swallow_the_rest(self):
        html = '<p><a href="https://example.com">dangling</p><p>after</p>'
        assert "dangling" in strip_html(html)
        assert "after" in strip_html(html)

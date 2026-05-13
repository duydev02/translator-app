"""Tests for the schema-aware autocomplete suggestion engine.

The Tk widget integration needs a display, so we exercise the pure
suggestion logic — `_suggestions_for(prefix)` — through a stub-Text
shim. That covers the matching rules (prefix, length floor, table-then-
column ordering, dedupe, cap) without needing a live window.
"""

import types
import pytest

from translator_app.ui import autocomplete as ac


class _StubText:
    """Just enough of a tk.Text to construct an Autocomplete instance —
    we don't actually invoke any of the bound methods in these tests.
    """
    def bind(self, *_a, **_kw):
        pass


def _make_autocomplete(table_index=None, column_index=None):
    app = types.SimpleNamespace(
        table_index=table_index or {},
        column_index=column_index or {},
        _theme="light",
        _mono=None,
    )
    text = _StubText()
    return ac.Autocomplete(app, text)


class TestSuggestionsFor:
    def test_too_short_prefix_returns_empty(self):
        a = _make_autocomplete(
            table_index={"R_FOO": [("S", "Foo")]},
        )
        assert a._suggestions_for("R") == []   # 1 char < min

    def test_simple_prefix_match(self):
        a = _make_autocomplete(
            table_index={
                "R_FOO":  [("S", "Foo")],
                "R_BAR":  [("S", "Bar")],
                "OTHER":  [("S", "Other")],
            },
        )
        out = a._suggestions_for("R_")
        assert "R_FOO" in out
        assert "R_BAR" in out
        assert "OTHER" not in out

    def test_case_insensitive_prefix(self):
        a = _make_autocomplete(
            table_index={"R_FOO": [("S", "Foo")]},
        )
        assert a._suggestions_for("r_") == ["R_FOO"]
        assert a._suggestions_for("R_F") == ["R_FOO"]

    def test_tables_listed_before_columns(self):
        a = _make_autocomplete(
            table_index={"BUNRUI_T":  [("S", "BunruiTable")]},
            column_index={"BUNRUI_C": [("S", "T", "TL", "Bunrui col")]},
        )
        out = a._suggestions_for("BUNRUI")
        # Tables come first.
        assert out[0] == "BUNRUI_T"
        assert "BUNRUI_C" in out

    def test_dedupes_when_name_in_both_indexes(self):
        # An identical key showing up in both indexes must not appear twice.
        a = _make_autocomplete(
            table_index={"BUNRUI": [("S", "x")]},
            column_index={"BUNRUI": [("S", "T", "TL", "y")]},
        )
        out = a._suggestions_for("BUN")
        assert out.count("BUNRUI") == 1

    def test_caps_results_at_max(self):
        a = _make_autocomplete(
            table_index={f"R_TBL_{i:02d}": [("S", f"l{i}")] for i in range(50)},
        )
        out = a._suggestions_for("R_")
        assert len(out) <= ac._MAX_SUGGESTIONS

    def test_no_match_returns_empty(self):
        a = _make_autocomplete(
            table_index={"R_FOO": [("S", "Foo")]},
        )
        assert a._suggestions_for("ZZZZZZ") == []

    def test_empty_indexes_returns_empty(self):
        a = _make_autocomplete()
        assert a._suggestions_for("anything") == []


class TestWordRegex:
    def test_matches_simple_identifier(self):
        m = ac._WORD_RE.search("foo bar BUNRUI")
        assert m and m.group(0) == "BUNRUI"

    def test_excludes_dot_qualified(self):
        # `tr.col` — only the trailing `col` should match.
        m = ac._WORD_RE.search("WHERE tr.col")
        assert m and m.group(0) == "col"

    def test_no_match_on_pure_punctuation(self):
        assert ac._WORD_RE.search("(((") is None

    def test_no_match_starting_with_digit(self):
        # Identifiers must start with a letter or underscore.
        m = ac._WORD_RE.search("123abc")
        # `abc` matches (the regex is anchored at end via `$`, so it
        # requires the match to extend to the end of string — `abc` does).
        assert m and m.group(0) == "abc"

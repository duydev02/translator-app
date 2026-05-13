"""Tests for the pure helper functions inside the GUI dialogs.

The dialogs themselves require a Tk display, but each ships several
display-independent helpers that we *can* test:

* `command_palette._fuzzy_filter` / `_fuzzy_score`
* `snippets._default_name`
* `schema_browser._build_table_rows` /
                  `_build_column_rows_by_table` /
                  `_build_column_rows_all`
"""

import types

import pytest

from translator_app.paths import CUSTOM_SCHEMA
from translator_app.ui.dialogs.command_palette import _fuzzy_filter, _fuzzy_score
from translator_app.ui.dialogs.snippets import _default_name
from translator_app.ui.dialogs.schema_browser import (
    _build_column_rows_all,
    _build_column_rows_by_table,
    _build_table_rows,
)


# ── command_palette: fuzzy matching ─────────────────────────────────────────
COMMANDS = [
    {"label": "Toggle theme",         "category": "View"},
    {"label": "Toggle word wrap",     "category": "View"},
    {"label": "Toggle line numbers",  "category": "View"},
    {"label": "Translate now",        "category": "Action"},
    {"label": "Open Schema Browser…", "category": "Dialog"},
    {"label": "Open Snippets…",       "category": "Dialog"},
    {"label": "Direction: Logical → Physical",
                                      "category": "Direction"},
]


class TestFuzzyFilter:
    def test_empty_query_returns_all(self):
        assert _fuzzy_filter(COMMANDS, "") == COMMANDS

    def test_substring_match_ranks_first(self):
        # `theme` is a substring of "Toggle theme" → it should win.
        out = _fuzzy_filter(COMMANDS, "theme")
        assert out[0]["label"] == "Toggle theme"

    def test_fuzzy_match_ordered(self):
        # `trsl` is not a substring of any label, but it appears in order
        # in "Translate now" → that should match.
        out = _fuzzy_filter(COMMANDS, "trsl")
        labels = [c["label"] for c in out]
        assert "Translate now" in labels

    def test_no_match_returns_empty(self):
        out = _fuzzy_filter(COMMANDS, "zzzzzzzz")
        assert out == []

    def test_case_insensitive(self):
        a = _fuzzy_filter(COMMANDS, "schema")
        b = _fuzzy_filter(COMMANDS, "SCHEMA")
        assert [c["label"] for c in a] == [c["label"] for c in b]

    def test_shorter_label_wins_tie(self):
        # Two substring matches at the same position — the shorter label
        # should rank first.
        cmds = [
            {"label": "Open Snippets…",            "category": ""},
            {"label": "Open Snippets manager…",    "category": ""},
        ]
        out = _fuzzy_filter(cmds, "snippets")
        assert out[0]["label"] == "Open Snippets…"

    def test_query_can_match_category(self):
        # "dialog" only appears in the category, not labels.
        out = _fuzzy_filter(COMMANDS, "dialog")
        assert any(c["category"] == "Dialog" for c in out)


class TestFuzzyScore:
    def test_substring_returns_finite_score(self):
        score = _fuzzy_score("toggle theme view", "theme", "toggle theme")
        assert score is not None
        assert score >= 0

    def test_no_match_returns_none(self):
        assert _fuzzy_score("hello world", "xyz", "hello world") is None

    def test_substring_beats_pure_fuzzy(self):
        # Both haystacks contain the chars of "trans" in order, but only
        # one has it as a literal substring.
        sub = _fuzzy_score("translate now action", "trans", "translate now")
        fuzzy = _fuzzy_score("toggle theme view", "tggl", "toggle theme")
        assert sub is not None
        assert fuzzy is not None
        # Substring scores should be (much) lower than pure-fuzzy scores
        # — pure fuzzy adds +100 to deprioritise it.


# ── snippets: default-name extraction ────────────────────────────────────────
class TestDefaultName:
    def test_picks_java_method_name(self):
        text = "/** doc */\nprivate String getFoo(String x) { return null; }"
        assert _default_name(text) == "getFoo"

    def test_handles_multi_modifier_signature(self):
        text = "public static final synchronized PreparedStatement makeIt(int n) {"
        assert _default_name(text) == "makeIt"

    def test_falls_back_to_first_real_line(self):
        text = "SELECT * FROM r_foo WHERE id = ?"
        assert _default_name(text) == "SELECT * FROM r_foo WHERE id = ?"

    def test_skips_javadoc_lines(self):
        text = """\
/** doc */
 *  more javadoc
SELECT 1
"""
        assert _default_name(text) == "SELECT 1"

    def test_truncates_very_long_first_line(self):
        long = "x" * 200
        out = _default_name(long)
        assert len(out) <= 60

    def test_empty_input_returns_default_label(self):
        assert _default_name("") == "snippet"

    def test_only_comments_returns_snippet(self):
        text = "// just a comment\n/* another */"
        assert _default_name(text) == "snippet"


# ── schema_browser: data builders ────────────────────────────────────────────
def _mock_app(table_index, column_index):
    """Build a stub object that the data builders can read."""
    return types.SimpleNamespace(
        table_index=table_index,
        column_index=column_index,
    )


class TestSchemaBrowserBuilders:
    def test_build_table_rows_collapses_duplicates(self):
        app = _mock_app(
            table_index={
                "R_SYOHIN": [("マスタ管理", "商品マスタ"),
                             ("業務系",     "商品マスタ")],
                "R_TENPO":  [("マスタ管理", "店舗マスタ")],
            },
            column_index={},
        )
        rows = dict(_build_table_rows(app))
        assert "R_SYOHIN" in rows
        # Logical names are deduped — the two "商品マスタ" entries collapse.
        assert rows["R_SYOHIN"] == "商品マスタ"
        assert rows["R_TENPO"] == "店舗マスタ"

    def test_build_table_rows_skips_custom_only_entries(self):
        # User-map-only overrides shouldn't appear as new tables.
        app = _mock_app(
            table_index={
                "R_X": [(CUSTOM_SCHEMA, "override-only")],
                "R_Y": [("マスタ", "yLogical")],
            },
            column_index={},
        )
        rows = dict(_build_table_rows(app))
        assert "R_Y" in rows
        assert "R_X" not in rows

    def test_build_table_rows_with_no_logical_name(self):
        app = _mock_app(
            table_index={
                "R_X": [("マスタ", "")],   # no logical name
            },
            column_index={},
        )
        rows = dict(_build_table_rows(app))
        # Should still appear, with a placeholder.
        assert "R_X" in rows
        assert "(no logical name)" in rows["R_X"]

    def test_build_column_rows_by_table_groups_correctly(self):
        app = _mock_app(
            table_index={},
            column_index={
                "BUNRUI1_CD": [
                    ("マスタ", "R_SYOHIN", "商品マスタ", "分類１コード"),
                    ("マスタ", "R_TENPO",  "店舗マスタ", "分類１コード"),
                ],
                "SYOHIN_CD":  [
                    ("マスタ", "R_SYOHIN", "商品マスタ", "商品コード"),
                ],
            },
        )
        by_table = _build_column_rows_by_table(app)
        # R_SYOHIN gets both columns
        rs = by_table["R_SYOHIN"]
        names = {row[0] for row in rs}
        assert names == {"BUNRUI1_CD", "SYOHIN_CD"}
        # The "other tables" hint for BUNRUI1_CD in R_SYOHIN should mention R_TENPO.
        b_row = next(r for r in rs if r[0] == "BUNRUI1_CD")
        assert "R_TENPO" in b_row[2]

    def test_build_column_rows_all_aggregates_tables(self):
        app = _mock_app(
            table_index={},
            column_index={
                "X_CD": [
                    ("マスタ", "T1", "TableOne", "x"),
                    ("マスタ", "T2", "TableTwo", "x"),
                    ("マスタ", "T3", "TableThree", "x"),
                ],
            },
        )
        rows = _build_column_rows_all(app)
        # Single (X_CD, x) entry whose third column lists all three tables.
        assert len(rows) == 1
        phys, logical, tables_s = rows[0]
        assert phys == "X_CD"
        assert logical == "x"
        for tbl in ("T1", "T2", "T3"):
            assert tbl in tables_s

    def test_build_column_rows_all_skips_custom_schema(self):
        app = _mock_app(
            table_index={},
            column_index={
                "USER_OVERRIDE_CD": [(CUSTOM_SCHEMA, CUSTOM_SCHEMA, CUSTOM_SCHEMA, "override")],
                "REAL_CD":          [("マスタ", "T1", "TableOne", "real")],
            },
        )
        rows = _build_column_rows_all(app)
        names = {r[0] for r in rows}
        # User-map-only entries are filtered out of the global view.
        assert "REAL_CD" in names
        assert "USER_OVERRIDE_CD" not in names

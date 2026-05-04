"""Tests for the more recent additions to designdoc.py.

The original `tests/test_designdoc.py` covers the basic shape of
`java_to_design_doc()`. This file targets the helpers and edge cases that
have grown out of real-world inputs:

* `_pretty_sql`            — the SQL pretty-printer used by Inspect's
                             "Reconstructed SQL" tab.
* `_is_paren_group`        — recognises grouped sub-conditions `( a OR b )`
                             so they expand to multi-line in the output.
* `_extract_appends_with_receiver` — receiver-aware `.append(...)` extractor
                             for multi-StringBuffer Java methods.
* `_build_sql_from_java`   — main-buffer detection (returned vs spliced
                             vs most-appended) and toString() inlining.
* `_parse_insert`          — paren-aware INSERT parsing (column list vs
                             wrapped SELECT, nested parens in NVL etc.).
* `_parse_update`          — table alias between target and SET.
* `_parse_sql`             — outer-paren stripping for whole SQL wrapped
                             in `( … )` (used as a sub-select later).
* `_build_stats_block` /
  `_stat_target`           — placeholder resolution (target shows
                             `${tableName}` not the raw private-use char).
* `compute_design_stats`   — pure-function stats output.
* `compute_design_details` — used by the Inspect dialog.
"""

import pytest

from translator_app.designdoc import (
    _build_sql_from_java,
    _build_stats_block,
    _extract_appends_with_receiver,
    _is_paren_group,
    _parse_insert,
    _parse_sql,
    _parse_update,
    _pretty_sql,
    _stat_target,
    compute_design_details,
    compute_design_stats,
    java_to_design_doc,
)


# ── _pretty_sql ─────────────────────────────────────────────────────────────
class TestPrettySql:
    def test_aligns_clause_keywords_to_same_column(self):
        out = _pretty_sql(
            "update foo set a = ? where b = ? and c = ? and d = ?"
        )
        # Each clause keyword line should start with the same right-aligned
        # column. Strip per-line indent and check the keyword is uppercase.
        lines = out.splitlines()
        assert lines[0].startswith("UPDATE")
        assert "   SET " in lines[1]
        assert " WHERE " in lines[2]
        assert "   AND " in lines[3]
        assert "   AND " in lines[4]
        # AND/OR rows align with WHERE (all sit inside a 6-char field).
        and_col = lines[3].index("AND")
        where_col = lines[2].index("WHERE")
        # AND should be 1 char further right than WHERE since AND is shorter
        assert and_col > where_col

    def test_does_not_break_keywords_inside_string_literals(self):
        sql = "select * from t where status = 'AND IS NULL UPDATE' and x = 1"
        out = _pretty_sql(sql)
        # Quoted text should remain on a single line.
        assert "'AND IS NULL UPDATE'" in out

    def test_does_not_break_keywords_inside_placeholders(self):
        sql = "update ${tableName} set syohin_cd = ? where x = ?"
        out = _pretty_sql(sql)
        assert "${tableName}" in out

    def test_does_not_recurse_into_subqueries(self):
        sql = "select a from t where exists (select 1 from s where s.x = t.x and s.y = ?) and t.z = ?"
        out = _pretty_sql(sql)
        # The inner `(select 1 ... s.y = ?)` stays on one line; AND inside
        # the parens must not become its own line.
        lines = out.splitlines()
        # There should only be one AND breakpoint at the outer level.
        and_lines = [l for l in lines if l.lstrip().startswith("AND")]
        assert len(and_lines) == 1
        # Subquery body unchanged.
        assert any("(select 1" in l for l in lines)

    def test_normalises_whitespace_in_input(self):
        sql = "select   *\tfrom\nfoo  where\n\nx = 1"
        out = _pretty_sql(sql)
        # Multiple whitespace flattened, then properly broken.
        assert out == "SELECT *\n  FROM foo\n WHERE x = 1"

    def test_empty_input_returns_input(self):
        assert _pretty_sql("") == ""

    def test_idempotent_on_already_formatted(self):
        once = _pretty_sql("select a from t where b = 1")
        twice = _pretty_sql(once)
        assert once == twice


# ── _is_paren_group ─────────────────────────────────────────────────────────
class TestIsParenGroup:
    def test_simple_outer_parens(self):
        assert _is_paren_group("(a OR b)")

    def test_unbalanced_parens(self):
        assert not _is_paren_group("(a OR b")

    def test_two_top_level_groups(self):
        # Two adjacent groups don't share a single matching pair.
        assert not _is_paren_group("(a) AND (b)")

    def test_outer_parens_with_nested(self):
        assert _is_paren_group("(a AND (b OR c))")

    def test_no_outer_parens(self):
        assert not _is_paren_group("a OR b")

    def test_whitespace_padding(self):
        assert _is_paren_group("   ( a OR b )   ")

    def test_single_paren_pair_with_padding(self):
        assert _is_paren_group("(  )")


# ── _extract_appends_with_receiver ──────────────────────────────────────────
class TestExtractAppendsWithReceiver:
    def test_simple_single_buffer(self):
        code = 'sb.append("hi"); sb.append("there");'
        out = _extract_appends_with_receiver(code)
        assert out == [("sb", '"hi"'), ("sb", '"there"')]

    def test_multiple_buffers_kept_separate(self):
        code = 'sql1.append("A,"); sql2.append("?,"); sql.append("end");'
        out = _extract_appends_with_receiver(code)
        receivers = [r for r, _ in out]
        assert receivers == ["sql1", "sql2", "sql"]

    def test_chained_append_attributes_to_same_receiver(self):
        code = 'sb.append("a").append(x).append("b");'
        out = _extract_appends_with_receiver(code)
        receivers = [r for r, _ in out]
        # All three appends should be attributed to `sb`.
        assert receivers == ["sb", "sb", "sb"]

    def test_balanced_parens_in_arg(self):
        code = 'sb.append(NVL(col, "0"));'
        out = _extract_appends_with_receiver(code)
        assert out == [("sb", 'NVL(col, "0")')]


# ── _build_sql_from_java: main-buffer detection ─────────────────────────────
class TestBuildSqlFromJava:
    def test_single_buffer_returned_directly(self):
        java = """
        void f(){
            StringBuffer sb=new StringBuffer();
            sb.append("SELECT 1");
            return sb.toString();
        }
        """
        sql, _expr_map, _doc, _func = _build_sql_from_java(java)
        assert sql.strip() == "SELECT 1"

    def test_multi_buffer_with_return_picks_main(self):
        java = """
        void f(){
            StringBuffer sql=new StringBuffer();
            StringBuffer sql1=new StringBuffer();
            sql1.append("ignore me");
            sql.append("INSERT INTO t");
            return sql.toString();
        }
        """
        sql, _e, _d, _func = _build_sql_from_java(java)
        # Only `sql`'s appends should appear because it's the returned buffer.
        # `sql1`'s "ignore me" must NOT leak in.
        assert "INSERT INTO t" in sql
        assert "ignore me" not in sql

    def test_multi_buffer_no_return_falls_back_to_consumer(self):
        # Method returns a PreparedStatement, not a String. `sql` is the
        # consumer — it splices `sql1` and `sql2` via `.toString()` — so it
        # should still be picked as main.
        java = """
        PreparedStatement g(MasterDataBase d){
            StringBuffer sql=new StringBuffer();
            StringBuffer sql1=new StringBuffer();
            StringBuffer sql2=new StringBuffer();
            sql1.append("A,");
            sql2.append("?,");
            sql.append("INSERT INTO R_FOO (");
            sql.append(sql1.toString());
            sql.append(") VALUES (");
            sql.append(sql2.toString());
            sql.append(")");
            return d.getPrepareStatement(sql.toString());
        }
        """
        sql, _e, _d, _func = _build_sql_from_java(java)
        # The expected resulting SQL has sql1 + sql2 spliced in.
        assert "INSERT INTO R_FOO" in sql
        assert "A," in sql        # from sql1
        assert "?," in sql        # from sql2

    def test_other_buffer_toString_inlined(self):
        # `mainSb.append(other.toString())` should splice `other`'s appends
        # in place of the toString() call.
        java = """
        String f(){
            StringBuffer main=new StringBuffer();
            StringBuffer other=new StringBuffer();
            other.append("X,");
            other.append("Y");
            main.append("[");
            main.append(other.toString());
            main.append("]");
            return main.toString();
        }
        """
        sql, _e, _d, _func = _build_sql_from_java(java)
        # `[X,Y]` (toString result spliced inline)
        assert "[X,Y]" in sql.replace(" ", "")


# ── _parse_insert ───────────────────────────────────────────────────────────
class TestParseInsert:
    def test_with_column_list_and_values(self):
        p = _parse_insert("INSERT INTO t (a, b, c) VALUES (1, 2, 3)")
        assert p["target"] == "t"
        assert p["columns"] == ["a", "b", "c"]
        assert p.get("values") == ["1", "2", "3"]

    def test_with_column_list_and_select(self):
        p = _parse_insert("INSERT INTO t (a, b) SELECT x, y FROM s")
        assert p["columns"] == ["a", "b"]
        assert p.get("select", {}).get("fields") == ["x", "y"]

    def test_wrapped_select(self):
        # `INSERT INTO t (SELECT … FROM …)` with no separate column list —
        # the parens enclose a sub-select.
        p = _parse_insert("INSERT INTO t (SELECT a, NVL(b,0), c FROM s)")
        assert p["target"] == "t"
        # Column list should be EMPTY — the parens were the SELECT wrapper.
        assert p["columns"] == []
        assert p.get("select", {}).get("fields") == ["a", "NVL(b,0)", "c"]

    def test_direct_select_no_parens(self):
        p = _parse_insert("INSERT INTO t SELECT x FROM s")
        assert p["columns"] == []
        assert p.get("select", {}).get("fields") == ["x"]

    def test_nested_parens_in_column_value_not_split(self):
        p = _parse_insert("INSERT INTO t (a) VALUES (NVL(b,0))")
        assert p["columns"] == ["a"]
        assert p["values"] == ["NVL(b,0)"]


# ── _parse_update ───────────────────────────────────────────────────────────
class TestParseUpdate:
    def test_no_alias(self):
        p = _parse_update("UPDATE foo SET a = 1 WHERE b = 2")
        assert p["target"] == "foo"
        assert "alias" not in p
        assert len(p.get("set", [])) == 1

    def test_implicit_alias(self):
        p = _parse_update("UPDATE foo TR SET a = 1 WHERE TR.b = 2")
        assert p["target"] == "foo"
        assert p["alias"] == "TR"
        assert len(p.get("set", [])) == 1
        assert p.get("where")[0]["left"] == "TR.b"

    def test_explicit_AS_alias(self):
        p = _parse_update("UPDATE foo AS tr SET a = 1")
        assert p["target"] == "foo"
        assert p["alias"] == "tr"

    def test_no_set_falls_through_to_raw(self):
        p = _parse_update("UPDATE foo")  # malformed
        assert p["type"] == "UPDATE"
        # Should NOT crash; either returns target only or marks raw.
        # We accept either shape.


# ── _parse_sql outer-paren stripping ────────────────────────────────────────
class TestParseSqlOuterParens:
    def test_select_wrapped_in_outer_parens(self):
        p = _parse_sql("(SELECT a FROM t)")
        assert p["type"] == "SELECT"

    def test_select_union_wrapped(self):
        p = _parse_sql("(SELECT a FROM t1 UNION SELECT b FROM t2)")
        # Should classify as SELECT_UNION (has parts).
        assert p["type"] in ("SELECT", "SELECT_UNION")

    def test_double_wrapped(self):
        p = _parse_sql("((SELECT a FROM t))")
        assert p["type"] == "SELECT"

    def test_two_groups_at_top_level_not_stripped(self):
        # `(SELECT…) UNION (SELECT…)` — outer parens are NOT a single
        # matching pair, so we shouldn't strip anything.
        p = _parse_sql("(SELECT 1) UNION (SELECT 2)")
        assert p["type"] in ("SELECT", "SELECT_UNION", "UNKNOWN")


# ── _stat_target placeholder resolution ─────────────────────────────────────
class TestStatTarget:
    def test_plain_target_passes_through(self):
        out = _stat_target({"target": "R_FOO"}, expr_map={})
        assert out == "R_FOO"

    def test_placeholder_marker_resolved(self):
        # Build a parsed dict with a marker matching index 0 in the expr_map.
        from translator_app.designdoc import _EXPR_OPEN, _EXPR_CLOSE
        marker = f"{_EXPR_OPEN}0{_EXPR_CLOSE}"
        out = _stat_target({"target": marker}, {0: "tableName"})
        assert out == "${tableName}"

    def test_missing_target_returns_unknown(self):
        assert _stat_target({}, expr_map={}) == "(unknown)"

    def test_long_expr_truncated(self):
        from translator_app.designdoc import _EXPR_OPEN, _EXPR_CLOSE
        marker = f"{_EXPR_OPEN}5{_EXPR_CLOSE}"
        long = "a" * 100
        out = _stat_target({"target": marker}, {5: long})
        assert "…" in out
        assert len(out) < 80


# ── _build_stats_block / compute_design_stats ───────────────────────────────
class TestStats:
    def test_update_stats(self):
        java = """
        void f(String tableName){
            StringBuffer sb=new StringBuffer();
            sb.append("UPDATE foo TR SET x = ?, y = ? WHERE z = ?");
            return sb.toString();
        }
        """
        stats = compute_design_stats(java)
        s = "\n".join(stats)
        assert "Target table:" in s
        assert "SET columns: 2" in s
        assert "WHERE conditions: 1" in s
        assert "alias TR" in s

    def test_insert_stats(self):
        java = """
        void f(){
            StringBuffer sb=new StringBuffer();
            sb.append("INSERT INTO t (a, b, c) VALUES (?, ?, ?)");
            return sb.toString();
        }
        """
        stats = compute_design_stats(java)
        s = "\n".join(stats)
        assert "Target table: t" in s
        assert "Columns: 3" in s
        assert "Source: VALUES" in s
        assert "? binds: 3" in s

    def test_select_stats_with_join(self):
        java = """
        void f(){
            StringBuffer sb=new StringBuffer();
            sb.append("SELECT a, b FROM r1 r INNER JOIN r2 s ON r.x = s.x WHERE r.y > 1 ORDER BY r.y");
            return sb.toString();
        }
        """
        stats = compute_design_stats(java)
        s = "\n".join(stats)
        assert "Selected columns: 2" in s
        assert "JOINs: 1" in s
        assert "ORDER BY: 1" in s

    def test_unparsable_returns_empty(self):
        assert compute_design_stats("not java at all") == []


# ── compute_design_details (used by Inspect) ────────────────────────────────
class TestComputeDesignDetails:
    def test_basic_update_details(self):
        java = """
        void f(){
            StringBuffer sb=new StringBuffer();
            sb.append("UPDATE foo SET x = ?, y = ? WHERE z = ?");
            return sb.toString();
        }
        """
        d = compute_design_details(java, table_index={}, column_index={})
        assert d["ok"]
        assert d["stype"] == "UPDATE"
        # 3 ? placeholders → 3 bind positions
        assert len(d["bind_positions"]) == 3
        # No warnings expected
        assert d["warnings"] == []
        # Reconstructed SQL is pretty-printed (multi-line)
        assert "\n" in d["reconstructed_sql"]

    def test_warns_on_insert_count_mismatch(self):
        java = """
        void f(){
            StringBuffer sb=new StringBuffer();
            sb.append("INSERT INTO t (a, b, c) VALUES (?, ?)");
            return sb.toString();
        }
        """
        d = compute_design_details(java, {}, {})
        assert any("column count" in w.lower() for w in d["warnings"])

    def test_buffers_listed_for_multi_buffer(self):
        java = """
        String f(){
            StringBuffer main=new StringBuffer();
            StringBuffer extra=new StringBuffer();
            extra.append("x");
            main.append("INSERT INTO t (");
            main.append(extra.toString());
            main.append(") VALUES (?)");
            return main.toString();
        }
        """
        d = compute_design_details(java, {}, {})
        names = {b["name"] for b in d["buffers"]}
        assert "main" in names
        assert "extra" in names
        # `main` is the returned buffer, so it should be marked main.
        main_buf = next(b for b in d["buffers"] if b["name"] == "main")
        assert main_buf["is_main"]

    def test_unknown_token_picked_up(self):
        # Schema doesn't know SOMETHING_WEIRD
        java = """
        void f(){
            StringBuffer sb=new StringBuffer();
            sb.append("UPDATE foo SET SOMETHING_WEIRD = ? WHERE ID = ?");
            return sb.toString();
        }
        """
        d = compute_design_details(java, table_index={"FOO": [("S", "Foo")]},
                                   column_index={})
        assert "SOMETHING_WEIRD" in d["unknown_tokens"]

    def test_reconstructed_sql_substitutes_placeholders(self):
        java = """
        void f(String tableName){
            StringBuffer sb=new StringBuffer();
            sb.append("UPDATE ");
            sb.append(tableName);
            sb.append(" SET x = ?");
            return sb.toString();
        }
        """
        d = compute_design_details(java, {}, {})
        # Placeholder resolved to ${tableName} for readability.
        assert "${tableName}" in d["reconstructed_sql"]


# ── Integration: end-to-end design doc ─────────────────────────────────────-
class TestJavaToDesignDocIntegration:
    def test_no_inline_count_in_section_headers(self):
        # Section header counts like `(N)` must not appear in the rendered
        # text, because they get copied along with the doc.
        java = """
        void f(){
            StringBuffer sb=new StringBuffer();
            sb.append("UPDATE foo SET a = ?, b = ?, c = ? WHERE d = ? AND e = ?");
            return sb.toString();
        }
        """
        out = java_to_design_doc(java, {}, {}, {}, {})
        # No `■... (N)` patterns
        import re
        assert not re.search(r"■.+\(\d+\)", out)

    def test_string_literals_not_uppercased_when_uppercase_columns_on(self):
        java = """
        void f(){
            StringBuffer sb=new StringBuffer();
            sb.append("SELECT a FROM t WHERE status = 'Active' AND name LIKE 'Hello'");
            return sb.toString();
        }
        """
        out = java_to_design_doc(java, {}, {}, {}, {}, uppercase=True)
        # Quoted strings stay verbatim
        assert "'Active'" in out
        assert "'Hello'" in out
        # Identifiers ARE uppercased
        assert "STATUS" in out

    def test_outer_paren_wrapped_select_classified(self):
        java = """
        public String f(String tableName){
            StringBuffer sb=new StringBuffer();
            sb.append("(SELECT a FROM t1 UNION SELECT b FROM t2)");
            return sb.toString();
        }
        """
        out = java_to_design_doc(java, {}, {}, {}, {})
        # Should NOT fall through to "Unknown SQL statement type"
        assert "Unknown SQL" not in out
        assert "■処理区分" in out

    def test_exists_subquery_expanded(self):
        java = """
        void f(){
            StringBuffer sb=new StringBuffer();
            sb.append("UPDATE t SET x=1 WHERE EXISTS (SELECT 1 FROM s WHERE s.x=t.x)");
            return sb.toString();
        }
        """
        out = java_to_design_doc(java, {}, {}, {}, {})
        # EXISTS expansion creates an inner ■抽出テーブル block.
        assert "EXISTS (" in out
        # The body of the subquery should be on its own lines, not a single blob.
        lines = [l for l in out.splitlines() if "EXISTS" in l]
        assert any(l.strip().endswith("(") for l in lines)

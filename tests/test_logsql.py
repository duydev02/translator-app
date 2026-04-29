"""Tests for logsql.py — log scanning + SQL/params combining.

Most fixtures are short hand-written log snippets so the assertions stay
easy to read. The end-to-end test reads the real `stclibApp.log` checked
into the project root, locating a known query id from it."""

from pathlib import Path

import pytest

from translator_app.logsql import (
    DEFAULT_NOISE_PACKAGES,
    DEFAULT_NOISE_TABLES,
    Statement,
    annotate_scores,
    combine_sql_params,
    count_placeholders,
    extract_statement_type,
    extract_target_tables,
    find_entry_by_id,
    find_last_entry,
    format_param,
    group_by_action,
    parse_log,
    parse_params,
    score_statement,
)


# ── parse_params ──────────────────────────────────────────────────────────────
def test_parse_params_returns_position_ordered_pairs():
    raw = "[STRING:1:foo][STRING:2:bar][STRING:3:baz]"
    assert parse_params(raw) == [
        ("STRING", "foo"),
        ("STRING", "bar"),
        ("STRING", "baz"),
    ]


def test_parse_params_preserves_trailing_spaces_in_values():
    raw = "[STRING:1:0018      ][STRING:2:4513915012649 ]"
    assert parse_params(raw) == [
        ("STRING", "0018      "),
        ("STRING", "4513915012649 "),
    ]


def test_parse_params_handles_empty_value():
    raw = "[STRING:1:][STRING:2:not-empty]"
    assert parse_params(raw) == [("STRING", ""), ("STRING", "not-empty")]


def test_parse_params_empty_input_returns_empty_list():
    assert parse_params("") == []
    assert parse_params(None or "") == []


def test_parse_params_fills_missing_positions_with_empty_string():
    # Position 2 omitted; result must have a slot for it so positional
    # substitution stays correct.
    raw = "[STRING:1:a][STRING:3:c]"
    out = parse_params(raw)
    assert out == [("STRING", "a"), ("STRING", ""), ("STRING", "c")]


def test_parse_params_supports_other_types():
    raw = "[INT:1:42][DECIMAL:2:3.14][NULL:3:][DATE:4:2024-08-21]"
    assert parse_params(raw) == [
        ("INT", "42"),
        ("DECIMAL", "3.14"),
        ("NULL", ""),
        ("DATE", "2024-08-21"),
    ]


# ── format_param ──────────────────────────────────────────────────────────────
def test_format_param_string_quotes_and_escapes():
    assert format_param("STRING", "abc") == "'abc'"
    assert format_param("STRING", "O'Brien") == "'O''Brien'"
    assert format_param("STRING", "") == "''"


def test_format_param_null_emits_keyword():
    assert format_param("NULL", "anything") == "NULL"
    assert format_param("STRING", None) == "NULL"


def test_format_param_int_bare():
    assert format_param("INT", "42") == "42"
    assert format_param("BIGINT", "-7") == "-7"
    assert format_param("DECIMAL", "3.14") == "3.14"


def test_format_param_int_falls_back_to_quoted_when_unparseable():
    # Defensive — bad data shouldn't crash; emit as a string literal.
    assert format_param("INT", "n/a") == "'n/a'"


def test_format_param_unknown_type_falls_back_to_string():
    assert format_param("WEIRD_TYPE", "hi") == "'hi'"


def test_format_param_date_quoted():
    assert format_param("DATE", "2024-08-21") == "'2024-08-21'"
    assert format_param("TIMESTAMP", "2024-08-21 11:07:42") == "'2024-08-21 11:07:42'"


# ── count_placeholders / combine_sql_params ───────────────────────────────────
def test_count_placeholders_ignores_question_marks_inside_strings():
    sql = "SELECT '?' FROM T WHERE A = ? AND B = ?"
    assert count_placeholders(sql) == 2


def test_count_placeholders_handles_doubled_quotes():
    # `''` is an escaped single quote inside a string literal.
    sql = "SELECT 'O''Brien?' WHERE A = ?"
    assert count_placeholders(sql) == 1


def test_combine_sql_params_substitutes_in_order():
    sql = "SELECT * FROM T WHERE A = ? AND B = ?"
    out = combine_sql_params(sql, [("STRING", "alpha"), ("INT", "42")])
    assert out == "SELECT * FROM T WHERE A = 'alpha' AND B = 42"


def test_combine_sql_params_does_not_substitute_inside_strings():
    sql = "SELECT '?' WHERE A = ?"
    out = combine_sql_params(sql, [("STRING", "x")])
    assert out == "SELECT '?' WHERE A = 'x'"


def test_combine_sql_params_too_few_params_keeps_extra_question_marks():
    sql = "VALUES (?, ?, ?)"
    out = combine_sql_params(sql, [("STRING", "a")])
    assert out == "VALUES ('a', ?, ?)"


def test_combine_sql_params_too_many_params_drops_extras():
    sql = "VALUES (?)"
    out = combine_sql_params(sql, [("STRING", "a"), ("STRING", "ignored")])
    assert out == "VALUES ('a')"


def test_combine_sql_params_empty_params_returns_sql_unchanged():
    sql = "SELECT 1 FROM T WHERE A = ?"
    assert combine_sql_params(sql, []) == sql


# ── find_entry_by_id (synthetic log) ──────────────────────────────────────────
_FAKE_LOG = """\
2026-01-01 00:00:00,INFO,commons.dao.AbstractDaoInvoker,InvokeDao           ,Dao open jp.co.example.dao.impl.FooDao threadName=t1
2026-01-01 00:00:00,DEBUG,commons.dao.PreparedStatementEx,<init>              ,CreatePreparedStatement id=abcd1234   sql=SELECT * FROM T WHERE A = ? AND B = ?
2026-01-01 00:00:00,INFO,commons.dao.PreparedStatementEx,executeQuery        ,PreparedStatement.executeQuery() id=abcd1234  params=[STRING:1:hello][STRING:2:world]
2026-01-01 00:00:01,INFO,commons.dao.AbstractDaoInvoker,InvokeDao           ,Dao open jp.co.example.dao.impl.BarDao threadName=t1
2026-01-01 00:00:01,DEBUG,commons.dao.PreparedStatementEx,<init>              ,CreatePreparedStatement id=ef015678   sql=UPDATE T SET X = ? WHERE Y = ?
2026-01-01 00:00:01,INFO,commons.dao.PreparedStatementEx,executeUpdate       ,PreparedStatement.execteUpdate() id=ef015678  params=[STRING:1:new][STRING:2:42]
"""


def test_find_entry_by_id_returns_full_record():
    e = find_entry_by_id(_FAKE_LOG, "abcd1234")
    assert e is not None
    assert e["id"] == "abcd1234"
    assert e["sql"] == "SELECT * FROM T WHERE A = ? AND B = ?"
    assert e["params"] == [("STRING", "hello"), ("STRING", "world")]
    assert e["fqcn"] == "jp.co.example.dao.impl.FooDao"
    assert e["result"] == "SELECT * FROM T WHERE A = 'hello' AND B = 'world'"


def test_find_entry_by_id_is_case_insensitive():
    e = find_entry_by_id(_FAKE_LOG, "ABCD1234")
    assert e is not None and e["id"] == "abcd1234"


def test_find_entry_by_id_returns_none_for_missing_id():
    assert find_entry_by_id(_FAKE_LOG, "deadbeef") is None


def test_find_entry_by_id_attaches_correct_fqcn_to_each_id():
    # When two ids are in the same log, each must pick up its own
    # surrounding InvokeDao FQCN — not the previous one.
    e2 = find_entry_by_id(_FAKE_LOG, "ef015678")
    assert e2 is not None
    assert e2["fqcn"] == "jp.co.example.dao.impl.BarDao"


# ── find_last_entry (synthetic log) ───────────────────────────────────────────
def test_find_last_entry_returns_most_recent_complete_pair():
    e = find_last_entry(_FAKE_LOG)
    assert e is not None
    assert e["id"] == "ef015678"
    assert "UPDATE T" in e["sql"]


def test_find_last_entry_ignores_dangling_init_without_exec():
    log = _FAKE_LOG + (
        "2026-01-01 00:00:02,DEBUG,commons.dao.PreparedStatementEx,<init>              ,"
        "CreatePreparedStatement id=99999999   sql=SELECT 1\n"
    )
    e = find_last_entry(log)
    # 99999999 has no execute line, so the latest *complete* pair is still
    # ef015678.
    assert e is not None and e["id"] == "ef015678"


def test_find_last_entry_returns_none_on_empty_log():
    assert find_last_entry("") is None


# ── End-to-end against the real log shipped with the repo ─────────────────────
SAMPLE_LOG = Path(__file__).parent.parent / "stclibApp.log"


@pytest.mark.skipif(not SAMPLE_LOG.exists(), reason="sample log not present")
def test_real_log_extracts_known_query_id():
    """Smoke test against the bundled stclibApp.log. The id 189369c1 is
    the WITH-TMP_TBL query the user pointed us at; we don't pin the full
    SQL (it's huge), just confirm the parser locates it and produces a
    plausible result."""
    from translator_app.logsql import read_log_file
    text = read_log_file(str(SAMPLE_LOG))
    e = find_entry_by_id(text, "189369c1")
    assert e is not None
    assert e["sql"].startswith("WITH TMP_TBL"), e["sql"][:80]
    assert len(e["params"]) == 9   # all STRING:1..9
    # The result should have all `?`s replaced with quoted literals.
    # The original SQL has many `?`s outside of any string; in `result`
    # the count of `?` outside literals should drop.
    assert count_placeholders(e["result"]) == 0
    # The DAO that issued this query is recorded in the log right above
    # the <init> line.
    assert e["fqcn"] is not None
    assert "PdaDataSelectDao" in e["fqcn"]


# ── extract_statement_type / extract_target_tables ────────────────────────────
def test_extract_statement_type_recognises_common_verbs():
    assert extract_statement_type("SELECT 1 FROM T") == "SELECT"
    assert extract_statement_type("  /* comment */ INSERT INTO T VALUES (1)") == "INSERT"
    assert extract_statement_type("UPDATE T SET A = 1") == "UPDATE"
    assert extract_statement_type("DELETE FROM T WHERE A = 1") == "DELETE"
    assert extract_statement_type("WITH X AS (SELECT 1) SELECT * FROM X") == "WITH"
    assert extract_statement_type("MERGE INTO T USING S ON …") == "MERGE"
    assert extract_statement_type("") == ""
    assert extract_statement_type("CALL my_proc()") == "SQL"


def test_extract_target_tables_skips_aliases_and_dedupes():
    sql = "SELECT * FROM R_TENPO T1 LEFT JOIN R_SYOHIN T2 ON T1.X = T2.Y"
    assert extract_target_tables(sql) == ["R_TENPO", "R_SYOHIN"]


def test_extract_target_tables_filters_generic_aliases():
    # TMP_TBL / MAIN are the generic aliases used in the sample SQL —
    # they shouldn't pollute the displayed table list.
    sql = "WITH TMP_TBL AS (SELECT 1) SELECT * FROM TMP_TBL JOIN R_HANBAI_SYOHIN HS"
    out = extract_target_tables(sql)
    assert "TMP_TBL" not in out
    assert "R_HANBAI_SYOHIN" in out


def test_extract_target_tables_caps_at_max_n():
    sql = "SELECT * FROM A1234 JOIN B1234 JOIN C1234 JOIN D1234 JOIN E1234"
    assert len(extract_target_tables(sql, max_n=3)) == 3


# ── score_statement ───────────────────────────────────────────────────────────
def _make_stmt(**overrides):
    s = Statement(id="x", sql=overrides.pop("sql", "SELECT 1 FROM T WHERE A = ?"))
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_score_statement_penalises_noise_package():
    s = _make_stmt(fqcn="jp.co.x.swc.commons.resorces.SystemPropertieDao",
                   params=[("STRING", "x")])
    assert score_statement(s, noise_packages=DEFAULT_NOISE_PACKAGES) < 0


def test_score_statement_rewards_explicit_primary_package():
    sql = "WITH TMP AS (SELECT 1) " + ("SELECT * FROM R_HANBAI_SYOHIN " * 30)
    s = _make_stmt(
        fqcn="jp.co.x.mdware.shiire.dao.impl.PdaDataSelectDao",
        sql=sql,
        params=[("STRING", str(i)) for i in range(9)],
    )
    score = score_statement(
        s,
        primary_packages=["mdware.shiire"],
        noise_packages=DEFAULT_NOISE_PACKAGES,
    )
    assert score >= 50, f"expected primary score, got {score}"


def test_score_statement_baseline_bonus_for_known_non_noise_dao():
    """Domain DAOs with no explicit primary list still cross the
    threshold for typical real queries (700+ chars). The +10 baseline
    bonus is the difference between a domain DAO and an unknown one;
    paired with the >500-char length bonus it lands TenpoSelectDao-style
    queries above DEFAULT_PRIMARY_THRESHOLD = 30."""
    # Realistic-length SQL — the actual TenpoSelectDao query in the
    # bundled stclibApp.log is ~933 chars; we mirror that to trigger the
    # >500 length bonus (which is what gets it across the threshold).
    sql = (
        "SELECT TENPO_CD, YUKO_DT, DELETE_FG, TENPOKAISO1_CD, TENPOKAISO2_CD, "
        "TENPOKAISO3_CD, KANJI_NA, KANA_NA, KANJI_RN, KANA_RN, TENPO_KB, "
        "KAITEN_DT, HEITEN_DT, ZAIMU_END_DT, HOJIN_CD, OPEN_DT, HOJIN_NA, "
        "TENPO_TYPE_KB, ENRYO_NB, AREA_NB, ZIPCODE_NA, JUSHO_KANJI_NA, "
        "JUSHO_KANA_NA, TEL_NB, FAX_NB, FAX_OUT_NB, EIGYO_KAISHI_DT, "
        "EIGYO_TERMINATE_DT, REGISTRATION_DT, UPDATE_DT, INSERT_DT "
        "FROM R_TENPO WHERE TENPO_CD = ? AND YUKO_DT BETWEEN ? AND ? "
        "AND DELETE_FG = '0' AND TENPO_KB IN ('1', '4') "
        "ORDER BY TENPO_CD, YUKO_DT DESC"
    )
    assert len(sql) > 500  # sanity: triggers the >500-char length bonus
    s = _make_stmt(
        fqcn="jp.co.x.mdware.shiire.dao.impl.TenpoSelectDao",
        sql=sql,
        params=[("STRING", "1"), ("STRING", "2"), ("STRING", "3")],
    )
    score = score_statement(s)
    assert score >= 30, f"expected primary, got {score}"


def test_score_statement_short_unknown_dao_below_threshold():
    """A short SELECT with no DAO context shouldn't accidentally rank
    primary — that would let random noise leak through."""
    s = _make_stmt(sql="SELECT 1 FROM T WHERE A = ?", params=[("STRING", "x")])
    assert score_statement(s) < 30


def test_score_statement_demotes_infra_table_targets():
    s = _make_stmt(
        fqcn="jp.co.x.mdware.shiire.dao.impl.WeirdDao",
        sql="INSERT INTO DT_TABLE_LOG (X, Y, Z) VALUES (?, ?, ?)",
        params=[("STRING", "a"), ("STRING", "b"), ("STRING", "c")],
    )
    s._tables = None
    s._stmt_type = None
    score = score_statement(s, noise_tables=DEFAULT_NOISE_TABLES)
    # Even though the DAO is not in noise_packages, the target table
    # alone (DT_TABLE_LOG) makes this infrastructure.
    assert score < 30, f"expected non-primary, got {score}"


# ── parse_log + group_by_action ───────────────────────────────────────────────
@pytest.mark.skipif(not SAMPLE_LOG.exists(), reason="sample log not present")
def test_parse_log_finds_all_statements_in_real_log():
    from translator_app.logsql import read_log_file
    text = read_log_file(str(SAMPLE_LOG))
    stmts = parse_log(text)
    # Sample log has 13 prepared statements (verified manually).
    assert len(stmts) == 13
    # Every statement got an FQCN — including the very first one whose
    # InvokeDao line appears AFTER the init line (the 2-pass FQCN fill
    # is what makes that work).
    assert all(s.fqcn for s in stmts), [s.id for s in stmts if not s.fqcn]
    # The big WITH TMP_TBL query should be marked primary after scoring.
    annotate_scores(stmts)
    assert any(
        s.is_primary and "PdaDataSelectDao" in (s.fqcn or "")
        for s in stmts
    )


@pytest.mark.skipif(not SAMPLE_LOG.exists(), reason="sample log not present")
def test_group_by_action_uses_call_method_labels():
    from translator_app.logsql import read_log_file
    text = read_log_file(str(SAMPLE_LOG))
    stmts = parse_log(text)
    actions = group_by_action(stmts)
    labels = [a.label for a in actions]
    # Both #search and #upd actions should be detected as labelled groups.
    assert any("#search" in l for l in labels)
    assert any("#upd" in l for l in labels)


def test_group_by_action_falls_back_to_time_gap_for_orphans():
    """Statements with no callMethod marker get bucketed by 1-second
    time gaps so an orphan statement at file start still has a home."""
    s1 = Statement(id="aaa", timestamp="2026-01-01 00:00:00",
                   sql="SELECT 1", fqcn="jp.co.x.foo.Dao", action=None)
    s2 = Statement(id="bbb", timestamp="2026-01-01 00:00:00",
                   sql="SELECT 2", fqcn="jp.co.x.foo.Dao", action=None)
    s3 = Statement(id="ccc", timestamp="2026-01-01 00:00:30",
                   sql="SELECT 3", fqcn="jp.co.x.foo.Dao", action=None)
    actions = group_by_action([s1, s2, s3])
    # First two are within 1s → same group; third is 30s later → new group.
    assert len(actions) == 2
    assert len(actions[0].statements) == 2
    assert len(actions[1].statements) == 1


def test_group_by_action_keeps_init_order_within_a_group():
    s1 = Statement(id="aaa", timestamp="2026-01-01 00:00:00",
                   sql="SELECT 1", action="Foo#bar")
    s2 = Statement(id="bbb", timestamp="2026-01-01 00:00:00",
                   sql="SELECT 2", action="Foo#bar")
    actions = group_by_action([s1, s2])
    assert len(actions) == 1
    assert [s.id for s in actions[0].statements] == ["aaa", "bbb"]


# ── back-compat: old find_entry_by_id shape still works ─────────────────────
def test_find_entry_by_id_back_compat_dict_shape():
    """The dict shape old callers expect: {id, sql, params_raw, params,
    fqcn, result}. Now produced by parse_log + Statement.as_dict, but the
    public contract must be unchanged."""
    e = find_entry_by_id(_FAKE_LOG, "abcd1234")
    assert e is not None
    for k in ("id", "sql", "params_raw", "params", "fqcn", "result"):
        assert k in e, f"missing key: {k}"

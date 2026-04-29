"""Tests for logsql.py — log scanning + SQL/params combining.

Most fixtures are short hand-written log snippets so the assertions stay
easy to read. The end-to-end test reads the real `stclibApp.log` checked
into the project root, locating a known query id from it."""

from pathlib import Path

import pytest

from translator_app.logsql import (
    combine_sql_params,
    count_placeholders,
    find_entry_by_id,
    find_last_entry,
    format_param,
    parse_params,
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

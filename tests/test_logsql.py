"""Tests for logsql.py — log scanning + SQL/params combining.

Most fixtures are short hand-written log snippets so the assertions stay
easy to read. The end-to-end test reads the real `stclibApp.log` checked
into the project root, locating a known query id from it."""

from pathlib import Path

import pytest

from translator_app.logsql import (
    DEFAULT_NOISE_PACKAGES,
    DEFAULT_NOISE_TABLES,
    SUBST_CLOSE,
    SUBST_OPEN,
    Statement,
    annotate_scores,
    archive_log_file,
    combine_sql_params,
    combine_sql_params_marked,
    count_placeholders,
    clear_log_file,
    extract_statement_type,
    extract_subst_ranges,
    extract_target_tables,
    extract_pasted_statement,
    find_entry_by_id,
    find_last_entry,
    format_param,
    group_by_action,
    keep_newest_repeated_sql,
    parse_log,
    parse_params,
    pretty_sql,
    score_statement,
    tokenize_sql_for_highlight,
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


# ── Nested-DAO attribution ────────────────────────────────────────────────────
# Real `stclibApp.log` files have nested DAO scopes — a business DAO opens,
# internally calls one or more SystemPropertieDao for config, then runs its
# real query. The previous flat `last_fqcn` tracking attributed the
# business query to whichever DAO was last *mentioned* (SystemPropertieDao,
# which had just closed). The fix is a stack: open = push, close = pop.
# Note: prepared-statement ids in the real logs are hex
# (`262f0e15`, `3b852eed`, …) — the parser's regex enforces
# `[0-9A-Fa-f]{4,}`. Stick to hex chars here too.
_NESTED_LOG = """\
2026-04-29 11:07:42,INFO,commons.dao.AbstractDaoInvoker,InvokeDao           ,Daoの開始jp.co.x.mdware.dailyorder.dao.web.impl.DailyOrderRetrieveDao threadName=t1
2026-04-29 11:07:42,INFO,commons.dao.AbstractDaoInvoker,InvokeDao           ,Daoの開始jp.co.x.swc.commons.resorces.SystemPropertieDao threadName=t1
2026-04-29 11:07:42,DEBUG,commons.dao.PreparedStatementEx,<init>              ,CreatePreparedStatement id=c0ff0001   sql=SELECT * FROM SYSTEM_CONTROL WHERE A = ?
2026-04-29 11:07:42,INFO,commons.dao.PreparedStatementEx,executeQuery        ,PreparedStatement.executeQuery() id=c0ff0001  params=[STRING:1:ONLINE_DT]
2026-04-29 11:07:42,INFO,commons.dao.AbstractDaoInvoker,InvokeDao           ,Daoの終了jp.co.x.swc.commons.resorces.SystemPropertieDao
2026-04-29 11:07:42,DEBUG,commons.dao.PreparedStatementEx,<init>              ,CreatePreparedStatement id=b1289999   sql=SELECT A, B, C FROM DT_DAILY_HACHU_HEADER WHERE TENPO_CD = ? AND BUNRUI1_CD = ?
2026-04-29 11:07:42,INFO,commons.dao.PreparedStatementEx,executeQuery        ,PreparedStatement.executeQuery() id=b1289999  params=[STRING:1:0018][STRING:2:001901]
2026-04-29 11:07:42,INFO,commons.dao.AbstractDaoInvoker,InvokeDao           ,Daoの終了jp.co.x.mdware.dailyorder.dao.web.impl.DailyOrderRetrieveDao
"""


def test_nested_dao_attributes_to_outer_scope():
    """The second SELECT runs AFTER SystemPropertieDao closed but
    BEFORE DailyOrderRetrieveDao closes — i.e. inside the outer
    scope. It must be attributed to DailyOrderRetrieveDao, NOT to
    SystemPropertieDao (which was the previous misattribution)."""
    stmts = parse_log(_NESTED_LOG)
    biz = next(s for s in stmts if s.id == "b1289999")
    assert biz.fqcn == "jp.co.x.mdware.dailyorder.dao.web.impl.DailyOrderRetrieveDao"


def test_nested_inner_query_keeps_inner_dao():
    """The inner config query runs while SystemPropertieDao is the
    open inner scope — it should get SystemPropertieDao."""
    stmts = parse_log(_NESTED_LOG)
    cfg = next(s for s in stmts if s.id == "c0ff0001")
    assert cfg.fqcn == "jp.co.x.swc.commons.resorces.SystemPropertieDao"


def test_nested_dao_scoring_lands_business_query_as_primary():
    """The business query (id=b1289999) is short here, so won't cross
    the threshold by length. But after the FQCN fix it gets the
    domain (mdware.dailyorder) baseline bonus instead of the
    swc.commons noise penalty — net positive score."""
    stmts = parse_log(_NESTED_LOG)
    annotate_scores(stmts)
    biz = next(s for s in stmts if s.id == "b1289999")
    # Pre-fix: this was attributed to SystemPropertieDao (noise) and
    # scored deeply negative. Post-fix: domain DAO, positive score.
    assert biz.score > 0, f"expected positive, got {biz.score}"


def test_fqcn_regex_rejects_mojibake_prefix():
    """When the encoding chain falls to latin-1, the Japanese tokens
    become high-Latin-1 chars that Unicode `\\w` would happily glue to
    the real FQCN. The ASCII-only regex must break at those bytes and
    extract only the real Java dotted identifier."""
    # `Daoの開始` decoded as latin-1 from cp932 bytes — represents the
    # exact mojibake pattern we observed in real logs.
    mojibake_line = (
        "InvokeDao Dao\xe3\x81\xae\xe9\x96\x8b\xe5\xa7\x8b"
        "jp.co.x.mdware.dailyorder.dao.web.impl.DailyOrderRetrieveDao threadName=t"
    )
    from translator_app.logsql import _extract_fqcn
    fqcn = _extract_fqcn(mojibake_line)
    # The cleanly-extracted FQCN must NOT include the mojibake prefix.
    assert fqcn == "jp.co.x.mdware.dailyorder.dao.web.impl.DailyOrderRetrieveDao"


# ── Scoring tiers for very-large SQL ──────────────────────────────────────────
def test_score_statement_large_sql_tiers():
    """A multi-thousand-char SQL gets cumulative length bonuses so it
    stays primary even under adverse conditions (wrong DAO, noisy
    target table). The user's real example (id=262f0e15) is 11K chars
    with 56 binds — those should land it well above the threshold."""
    # ~12K of repeated identifiers to clear the >10000-char tier.
    # Each "COLUMN_XXX, " is ~12 chars; need ~850 to exceed 10K.
    big_sql = ("SELECT " + ", ".join(f"COLUMN_{i:04d}" for i in range(900))
               + " FROM T WHERE A = ?")
    assert len(big_sql) > 10000
    s = Statement(
        id="big",
        sql=big_sql,
        fqcn="jp.co.x.mdware.dailyorder.dao.web.impl.DailyOrderRetrieveDao",
        params=[("STRING", "x")],
    )
    score = score_statement(s)
    # Length tiers alone: +20 (>500) +10 (>1500) +15 (>5000) +15 (>10000)
    # = +60. Plus +10 baseline for non-noise DAO. Well above 30.
    assert score >= 60


def test_score_statement_param_cap_raised_to_ten():
    """The param-count bonus was previously capped at 6 (+18). Real
    business queries with multiple UNION-ALL branches legitimately
    have 20+ binds — the new cap is 10 (+30)."""
    s_few = Statement(id="a", sql="SELECT 1 FROM T", fqcn="x.y.z.Dao",
                       params=[("STRING", str(i)) for i in range(6)])
    s_many = Statement(id="b", sql="SELECT 1 FROM T", fqcn="x.y.z.Dao",
                       params=[("STRING", str(i)) for i in range(20)])
    # 6 params → +18 from params; 20 params → +30 (capped at 10).
    # The delta is exactly +12.
    assert score_statement(s_many) - score_statement(s_few) == 12


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


def test_extract_pasted_statement_reads_copied_init_and_execute_lines():
    pasted = """\
2026-05-20 11:24:30,INFO,commons.dao.PreparedStatementEx,<init>              ,CreatePreparedStatement id=1f863b10   sql= SELECT A, B FROM DT_TEN_RECEIPT_SEISAN WHERE COMP_CD = ? AND KEIJO_DT = ? AND TENPO_CD = ?
2026-05-20 11:24:30,INFO,commons.dao.PreparedStatementEx,executeQuery        ,PreparedStatement.executeQuery() id=1f863b10  params=[STRING:1:0000][STRING:2:20250612][STRING:3:001001]
"""
    stmt = extract_pasted_statement(pasted)

    assert stmt is not None
    assert stmt.id == "1f863b10"
    assert stmt.sql.startswith("SELECT A, B")
    assert stmt.params == [
        ("STRING", "0000"),
        ("STRING", "20250612"),
        ("STRING", "001001"),
    ]
    assert stmt.combined_sql() == (
        "SELECT A, B FROM DT_TEN_RECEIPT_SEISAN "
        "WHERE COMP_CD = '0000' AND KEIJO_DT = '20250612' AND TENPO_CD = '001001'"
    )


def test_extract_pasted_statement_uses_newest_complete_pair():
    pasted = _FAKE_LOG + (
        "2026-01-01 00:00:02,DEBUG,commons.dao.PreparedStatementEx,<init>              ,"
        "CreatePreparedStatement id=99999999   sql=SELECT 1\n"
    )

    stmt = extract_pasted_statement(pasted)

    assert stmt is not None
    assert stmt.id == "ef015678"


def test_keep_newest_repeated_sql_keeps_latest_matching_shape():
    older = Statement(
        id="1111",
        sql="SELECT * FROM VW_SAKUTAIHI WHERE A = ?",
        fqcn="jp.co.example.HeaderCreateDao",
        params=[("STRING", "old")],
    )
    newer = Statement(
        id="2222",
        sql=" SELECT  *  FROM  VW_SAKUTAIHI  WHERE  A = ? ",
        fqcn="jp.co.example.HeaderCreateDao",
        params=[("STRING", "new")],
    )
    other = Statement(
        id="3333",
        sql="SELECT * FROM VW_SAKUTAIHI WHERE B = ?",
        fqcn="jp.co.example.HeaderCreateDao",
        params=[("STRING", "different")],
    )

    assert keep_newest_repeated_sql([older, newer, other]) == [newer, other]


def test_keep_newest_repeated_sql_does_not_merge_different_daos():
    sql = "SELECT * FROM VW_SAKUTAIHI WHERE A = ?"
    left = Statement(id="1111", sql=sql, fqcn="jp.co.example.HeaderCreateDao")
    right = Statement(id="2222", sql=sql, fqcn="jp.co.example.DetailCreateDao")

    assert keep_newest_repeated_sql([left, right]) == [left, right]


def test_clear_log_file_truncates_file(tmp_path):
    path = tmp_path / "stclibApp.log"
    original = "old log content\nSELECT 1\n".encode("cp932")
    path.write_bytes(original)

    archive_path = clear_log_file(str(path))

    assert path.exists()
    assert path.read_text(encoding="utf-8") == ""
    assert archive_path is not None
    assert Path(archive_path).exists()
    assert Path(archive_path).read_bytes() == original


def test_clear_log_file_can_skip_archive(tmp_path):
    path = tmp_path / "stclibApp.log"
    path.write_text("old log content\n", encoding="utf-8")

    archive_path = clear_log_file(str(path), archive=False)

    assert archive_path is None
    assert path.read_text(encoding="utf-8") == ""
    assert not (tmp_path / "archive").exists()


def test_archive_log_file_returns_none_for_empty_file(tmp_path):
    path = tmp_path / "stclibApp.log"
    path.write_text("", encoding="utf-8")

    assert archive_log_file(str(path)) is None
    assert not (tmp_path / "archive").exists()


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
# ── pretty_sql ────────────────────────────────────────────────────────────────
def test_pretty_sql_breaks_major_clauses():
    sql = "SELECT A, B FROM T WHERE X = 1 ORDER BY A"
    out = pretty_sql(sql)
    lines = out.splitlines()
    # Each top-level clause should land on its own line.
    assert any(l.startswith("SELECT") for l in lines)
    assert any(l.startswith("FROM")   for l in lines)
    assert any(l.startswith("WHERE")  for l in lines)
    assert any(l.startswith("ORDER BY") for l in lines)


def test_pretty_sql_indents_joins_and_connectives():
    sql = "SELECT * FROM A LEFT JOIN B ON A.X = B.X AND A.Y = B.Y WHERE A.Z = 1"
    out = pretty_sql(sql)
    # JOINs are indented two spaces, AND/OR four spaces.
    assert "\n  LEFT JOIN" in out
    assert "\n    AND" in out


def test_pretty_sql_preserves_string_literals():
    """A keyword like FROM that lives inside a string literal must not be
    broken across lines — only the real keywords are."""
    sql = "SELECT 'WHERE I LIVE' AS LBL FROM T WHERE A = 1"
    out = pretty_sql(sql)
    assert "'WHERE I LIVE'" in out
    assert any(l.startswith("WHERE A") for l in out.splitlines())


def test_pretty_sql_preserves_first_token():
    """The leading SELECT shouldn't get a leading newline (cosmetic)."""
    out = pretty_sql("SELECT 1 FROM T")
    assert not out.startswith("\n")


def test_pretty_sql_handles_empty_input():
    assert pretty_sql("") == ""


def test_pretty_sql_keeps_between_and_inline():
    """The `AND` inside `BETWEEN x AND y` is syntactic, not a logical
    connective — it must stay on the same line as the BETWEEN clause,
    not get pushed to its own indented line."""
    sql = "SELECT * FROM T WHERE YUKO_DT BETWEEN '20240101' AND '20241231'"
    out = pretty_sql(sql)
    # The BETWEEN ... AND ... stays on one line.
    assert "BETWEEN '20240101' AND '20241231'" in out
    # Specifically, no break inserted before this particular AND.
    assert "\n    AND '20241231'" not in out


def test_pretty_sql_still_breaks_real_connectives_after_between():
    """A later logical AND in the same WHERE — distinct from BETWEEN's
    syntactic AND — should still break to its own indented line."""
    sql = (
        "SELECT * FROM T WHERE YUKO_DT BETWEEN ? AND ? "
        "AND DELETE_FG = '0' AND TENPO_CD = ?"
    )
    out = pretty_sql(sql)
    # BETWEEN's AND stays inline.
    assert "BETWEEN ? AND ?" in out
    # The subsequent AND `AND DELETE_FG` and `AND TENPO_CD` ARE logical
    # connectives — they break.
    assert "\n    AND DELETE_FG" in out
    assert "\n    AND TENPO_CD" in out


def test_pretty_sql_does_not_break_into_substrings():
    """`INTO` keyword should not match inside `IDOSAKI_TENPO_CD` etc."""
    sql = "SELECT IDOSAKI_TENPO_CD FROM TBL WHERE IDOSAKI_TENPO_CD = 1"
    out = pretty_sql(sql)
    assert "IDOSAKI_TENPO_CD" in out
    assert "IDOSAKI\nTENPO_CD" not in out


def test_find_entry_by_id_back_compat_dict_shape():
    """The dict shape old callers expect: {id, sql, params_raw, params,
    fqcn, result}. Now produced by parse_log + Statement.as_dict, but the
    public contract must be unchanged."""
    e = find_entry_by_id(_FAKE_LOG, "abcd1234")
    assert e is not None
    for k in ("id", "sql", "params_raw", "params", "fqcn", "result"):
        assert k in e, f"missing key: {k}"


# ── tokenize_sql_for_highlight ────────────────────────────────────────────────
def test_tokenize_sql_for_highlight_classifies_basic_tokens():
    sql = "SELECT 'hello', 42 FROM T -- a comment"
    toks = tokenize_sql_for_highlight(sql)
    kinds = {kind for _, _, kind in toks}
    assert kinds == {"keyword", "string", "number", "comment"}
    # Ranges line up with the source.
    for s, e, kind in toks:
        if kind == "string":
            assert sql[s:e] == "'hello'"
        if kind == "number":
            assert sql[s:e] == "42"
        if kind == "comment":
            assert sql[s:e].startswith("--")


def test_tokenize_sql_for_highlight_detects_block_comment_across_newlines():
    sql = "SELECT /* multi\nline */ 1 FROM T"
    toks = tokenize_sql_for_highlight(sql)
    comments = [(s, e) for s, e, k in toks if k == "comment"]
    assert len(comments) == 1
    s, e = comments[0]
    assert sql[s:e].startswith("/*") and sql[s:e].endswith("*/")


def test_tokenize_sql_for_highlight_does_not_flag_identifiers_as_keywords():
    """`SYOHIN_CD` looks like a word but isn't a SQL keyword — it must
    not pick up the keyword tag (otherwise nearly every column name would
    light up)."""
    sql = "SELECT SYOHIN_CD FROM TBL"
    keyword_tokens = [
        sql[s:e] for s, e, kind in tokenize_sql_for_highlight(sql)
        if kind == "keyword"
    ]
    assert keyword_tokens == ["SELECT", "FROM"]


def test_tokenize_sql_for_highlight_handles_empty_input():
    assert tokenize_sql_for_highlight("") == []


# ── combine_sql_params_marked + extract_subst_ranges round-trip ──────────────
def test_combine_sql_params_marked_wraps_substitutions():
    marked, _ = combine_sql_params_marked(
        "SELECT * FROM T WHERE A = ?",
        [("STRING", "x")],
    )
    assert SUBST_OPEN in marked and SUBST_CLOSE in marked
    # The wrapped value is the formatted literal.
    assert SUBST_OPEN + "'x'" + SUBST_CLOSE in marked


def test_extract_subst_ranges_round_trip_recovers_clean_text_and_offsets():
    marked, _ = combine_sql_params_marked(
        "SELECT * FROM T WHERE A = ? AND B = ?",
        [("STRING", "foo"), ("INT", "42")],
    )
    clean, ranges = extract_subst_ranges(marked)
    # Clean text has no sentinels; offsets point at substituted spans.
    assert SUBST_OPEN not in clean and SUBST_CLOSE not in clean
    assert len(ranges) == 2
    assert clean[ranges[0][0]:ranges[0][1]] == "'foo'"
    assert clean[ranges[1][0]:ranges[1][1]] == "42"


def test_subst_ranges_survive_pretty_sql_round_trip():
    """The whole point of the sentinel dance: pretty_sql changes char
    offsets, but extract_subst_ranges in the cleaned post-pretty text
    still points at the right runs."""
    marked, _ = combine_sql_params_marked(
        "SELECT * FROM T WHERE A = ? AND B = ?",
        [("STRING", "foo"), ("INT", "42")],
    )
    pretty = pretty_sql(marked)
    clean, ranges = extract_subst_ranges(pretty)
    assert "\nWHERE" in clean       # pretty_sql ran
    assert clean[ranges[0][0]:ranges[0][1]] == "'foo'"
    assert clean[ranges[1][0]:ranges[1][1]] == "42"


def test_extract_subst_ranges_drops_unbalanced_sentinels_safely():
    # Defensive: a stray open sentinel shouldn't crash, just yield no
    # range for it (and the orphan char gets stripped).
    text = "abc" + SUBST_OPEN + "xyz"   # no close
    clean, ranges = extract_subst_ranges(text)
    assert clean == "abcxyz"
    assert ranges == []

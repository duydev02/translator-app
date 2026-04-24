from translator_app.designdoc import (
    _build_sql_from_java,
    _parse_sql,
    _strip_java_comments,
    java_to_design_doc,
)


def test_strip_java_comments_removes_line_and_block():
    code = '''
    // single line
    String s = "keep // this"; /* block
    comment */ int x = 1;
    /** javadoc */ int y = 2;
    '''
    out = _strip_java_comments(code)
    assert "single line" not in out
    assert "block" not in out
    assert "javadoc" not in out
    assert '"keep // this"' in out  # string contents preserved


def test_build_sql_from_java_concatenates_appends():
    java = '''
    void f() {
        sb.append("SELECT * ");
        sb.append("FROM ");
        sb.append("R_SYOHIN");
    }
    '''
    sql, expr_map, _, _ = _build_sql_from_java(java)
    assert "SELECT *" in sql
    assert "FROM" in sql
    assert "R_SYOHIN" in sql


def test_parse_sql_identifies_statement_types():
    assert _parse_sql("INSERT INTO T (A) VALUES (1)")["type"] == "INSERT"
    assert _parse_sql("UPDATE T SET A = 1")["type"] == "UPDATE"
    assert _parse_sql("DELETE FROM T WHERE A = 1")["type"] == "DELETE"
    assert _parse_sql("SELECT A FROM T")["type"] == "SELECT"


def test_java_to_design_doc_update_lowercase(sample_index):
    table_index, column_index, rev_table_index, rev_column_index, _ = sample_index
    java = '''
    void f() {
        sb.append("update r_syohin ");
        sb.append("set tanka = ? ");
        sb.append("where syohin_cd = ?");
    }
    '''
    out = java_to_design_doc(
        java, table_index, column_index, rev_table_index, rev_column_index
    )
    assert "■処理区分\n\tUPDATE" in out
    assert "商品マスタ" in out
    assert "単価" in out
    assert "商品コード" in out


def test_java_to_design_doc_case_insensitivity(sample_index):
    table_index, column_index, rev_table_index, rev_column_index, _ = sample_index
    java_lower = '''void f() { sb.append("update r_syohin set tanka = ? where syohin_cd = ?"); }'''
    java_mixed = '''void f() { sb.append("UPDATE R_Syohin SET Tanka = ? WHERE Syohin_Cd = ?"); }'''
    out_lower = java_to_design_doc(
        java_lower, table_index, column_index, rev_table_index, rev_column_index
    )
    out_mixed = java_to_design_doc(
        java_mixed, table_index, column_index, rev_table_index, rev_column_index
    )
    # Both should contain the translated Japanese names
    for out in (out_lower, out_mixed):
        assert "商品マスタ" in out
        assert "単価" in out
        assert "商品コード" in out


def test_java_to_design_doc_select_with_alias(sample_index):
    table_index, column_index, rev_table_index, rev_column_index, _ = sample_index
    java = '''void f() { sb.append("select rs.syohin_cd from r_syohin rs"); }'''
    out = java_to_design_doc(
        java, table_index, column_index, rev_table_index, rev_column_index
    )
    assert "商品コード" in out
    assert "商品マスタ" in out


def test_parse_sql_empty_is_unknown():
    assert _parse_sql("")["type"] == "UNKNOWN"

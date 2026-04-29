from translator_app.translate import (
    _exclusion_ranges,
    _tokens,
    find_column_inconsistencies,
    find_unknown_tokens,
    translate_inline_mode,
    translate_reverse_inline_mode,
)


def test_translate_inline_mode_preserves_surrounding_text(sample_index):
    table_index, column_index, _, _, _, _ = sample_index
    text = "SELECT SYOHIN_CD FROM R_SYOHIN WHERE X = 1"
    out, rmap, _ = translate_inline_mode(text, table_index, column_index)
    assert "SELECT" in out  # surrounding keyword preserved (not in index)
    assert "商品マスタ" in out
    assert "商品コード" in out
    assert rmap["R_SYOHIN"] == "商品マスタ"


def test_translate_inline_mode_exclusion_preserves_token(sample_index):
    table_index, column_index, _, _, _, _ = sample_index
    text = "R_SYOHIN SYOHIN_CD"
    out, _, _ = translate_inline_mode(
        text, table_index, column_index, exclusions=["R_SYOHIN"]
    )
    assert "R_SYOHIN" in out          # preserved as-is
    assert "商品コード" in out         # other still translated


def test_tokens_returns_identifier_like_tokens():
    toks = _tokens("SELECT SYOHIN_CD, FOO FROM R_SYOHIN")
    assert "SYOHIN_CD" in toks
    assert "FOO" in toks
    assert "R_SYOHIN" in toks
    # single-letter/lower shouldn't appear
    assert "a" not in toks


def test_exclusion_ranges_reports_character_ranges():
    text = "SYOHIN_CD and TENPO_CD"
    ranges = _exclusion_ranges(text, ["SYOHIN_CD"])
    assert ranges == [(0, 9)]


def test_find_unknown_tokens(sample_index):
    table_index, column_index, _, _, _, _ = sample_index
    text = "R_SYOHIN UNKNOWN_COL XYZ_T"
    unknown = find_unknown_tokens(text, table_index, column_index)
    assert "UNKNOWN_COL" in unknown
    assert "XYZ_T" in unknown
    assert "R_SYOHIN" not in unknown


def test_find_column_inconsistencies():
    # In-memory column index: same phys_col, two different logical names
    column_index = {
        "STATUS_CD": [
            ("S1", "T_A", "表A", "状態コード"),
            ("S1", "T_A", "表A", "状態コード"),
            ("S1", "T_B", "表B", "ステータス"),
        ],
        "OTHER_CD": [
            ("S1", "T_A", "表A", "その他コード"),
        ],
    }
    results = find_column_inconsistencies(column_index)
    assert len(results) == 1
    assert results[0]["phys_col"] == "STATUS_CD"
    variants = {v["logical"] for v in results[0]["variants"]}
    assert variants == {"状態コード", "ステータス"}


def test_translate_reverse_inline_mode(sample_index):
    _, _, rev_table_index, rev_column_index, _, _ = sample_index
    out, rmap, _ = translate_reverse_inline_mode(
        "商品マスタの商品コードを取得", rev_table_index, rev_column_index
    )
    assert "R_SYOHIN" in out
    assert "SYOHIN_CD" in out
    assert rmap["商品マスタ"] == "R_SYOHIN"

from translator_app.paths import CUSTOM_SCHEMA
from translator_app.schema import (
    _filter_entries,
    _is_ambiguous,
    _most_common,
    merge_user_map,
)


def test_load_index_has_expected_table_keys(sample_index):
    table_index, column_index, _, _, _ = sample_index
    assert "R_SYOHIN" in table_index
    assert "R_TENPO" in table_index
    assert "SYOHIN_CD" in column_index


def test_load_index_table_entry_has_logical_name(sample_index):
    table_index, _, _, _, _ = sample_index
    entries = table_index["R_SYOHIN"]
    # Each entry is (schema, logical_table)
    assert any(lg == "商品マスタ" for _, lg in entries)


def test_load_index_schemas_is_list(sample_index):
    _, _, _, _, schemas = sample_index
    assert isinstance(schemas, list)
    assert "マスタ管理" in schemas


def test_merge_user_map_injects_override(sample_index):
    table_index, column_index, rev_table_index, rev_column_index, _ = sample_index
    user_map = {"tables": {"FOO_T": "フー表"}, "columns": {"BAR_C": "バー列"}}
    merge_user_map(table_index, column_index, rev_table_index, rev_column_index, user_map)
    assert any(s == CUSTOM_SCHEMA and lg == "フー表" for s, lg in table_index["FOO_T"])
    assert any(e[0] == CUSTOM_SCHEMA and e[-1] == "バー列" for e in column_index["BAR_C"])
    assert "フー表" in rev_table_index
    assert "バー列" in rev_column_index


def test_most_common_picks_majority():
    # Column-entry shape: (schema, phys_table, logical_table, logical_col)
    entries = [
        ("S1", "T1", "TL1", "商品コード"),
        ("S2", "T2", "TL2", "商品コード"),
        ("S3", "T3", "TL3", "別名"),
    ]
    assert _most_common("SYOHIN_CD", entries) == "商品コード"


def test_most_common_user_override_wins():
    entries = [
        ("S1", "T1", "TL1", "他の名前"),
        ("S1", "T1", "TL1", "他の名前"),
        (CUSTOM_SCHEMA, CUSTOM_SCHEMA, CUSTOM_SCHEMA, "カスタム名"),
    ]
    assert _most_common("X", entries) == "カスタム名"


def test_is_ambiguous_when_entries_disagree():
    entries = [
        ("S1", "T1", "TL1", "名前A"),
        ("S2", "T2", "TL2", "名前B"),
    ]
    assert _is_ambiguous("X", entries) is True


def test_is_ambiguous_false_with_user_override():
    entries = [
        ("S1", "T1", "TL1", "名前A"),
        ("S2", "T2", "TL2", "名前B"),
        (CUSTOM_SCHEMA, CUSTOM_SCHEMA, CUSTOM_SCHEMA, "正解"),
    ]
    assert _is_ambiguous("X", entries) is False


def test_filter_entries_narrows_by_schema():
    entries = [
        ("S1", "商品マスタ"),
        ("S2", "商品マスタ"),
    ]
    filtered = _filter_entries(entries, schemas={"S1"}, has_phys_table=False)
    assert filtered == [("S1", "商品マスタ")]

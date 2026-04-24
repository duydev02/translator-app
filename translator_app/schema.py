import json
from collections import Counter

from .paths import CUSTOM_SCHEMA


# Index shapes (used as loose aliases — entries are plain tuples at runtime):
#   TableIndex    : phys_table  → [(schema, logical_table)]
#   ColumnIndex   : phys_column → [(schema, phys_table, logical_table, logical_column)]
#   RevTableIndex : logical_table  → [(schema, phys_table)]
#   RevColumnIndex: logical_column → [(schema, phys_table, logical_table, phys_column)]
TableIndex     = dict[str, list[tuple[str, str]]]
ColumnIndex    = dict[str, list[tuple[str, str, str, str]]]
RevTableIndex  = dict[str, list[tuple[str, str]]]
RevColumnIndex = dict[str, list[tuple[str, str, str, str]]]


# ── Index loading ─────────────────────────────────────────────────────────────
def load_index(
    json_file: str,
) -> tuple[TableIndex, ColumnIndex, RevTableIndex, RevColumnIndex, list[str]]:
    """Return (table_index, column_index, rev_table_index, rev_column_index, schemas)."""
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    table_index, column_index = {}, {}
    rev_table_index, rev_column_index = {}, {}
    # Skip top-level non-dict values (e.g. the "__comment__" key in the sample
    # schema). Only real schema entries map to a dict of tables.
    schemas = [k for k, v in data.items() if isinstance(v, dict)]

    for schema, tables in data.items():
        if not isinstance(tables, dict):
            continue
        for phys_table, tdata in tables.items():
            logical_table = tdata["logical_table"] or ""
            table_index.setdefault(phys_table, []).append((schema, logical_table))
            if logical_table and logical_table != phys_table:
                rev_table_index.setdefault(logical_table, []).append((schema, phys_table))

            for phys_col, logical_col in tdata["columns"].items():
                column_index.setdefault(phys_col, []).append(
                    (schema, phys_table, logical_table, logical_col)
                )
                if logical_col and logical_col != phys_col:
                    rev_column_index.setdefault(logical_col, []).append(
                        (schema, phys_table, logical_table, phys_col)
                    )
    return table_index, column_index, rev_table_index, rev_column_index, schemas


def merge_user_map(
    table_index: TableIndex,
    column_index: ColumnIndex,
    rev_table_index: RevTableIndex,
    rev_column_index: RevColumnIndex,
    user_map: dict,
) -> None:
    """Inject user-defined mappings into the indexes using CUSTOM_SCHEMA as the
    schema marker. They always win during voting and bypass filters.
    Mutates the dicts in place."""
    for phys, logical in (user_map.get("tables") or {}).items():
        if not phys or not logical:
            continue
        table_index.setdefault(phys, []).append((CUSTOM_SCHEMA, logical))
        rev_table_index.setdefault(logical, []).append((CUSTOM_SCHEMA, phys))

    for phys, logical in (user_map.get("columns") or {}).items():
        if not phys or not logical:
            continue
        # Column entries are (schema, phys_table, logical_table, logical_col).
        # User doesn't specify a table context so we use CUSTOM_SCHEMA in both
        # slots; the filter code recognises CUSTOM_SCHEMA and never drops it.
        column_index.setdefault(phys, []).append(
            (CUSTOM_SCHEMA, CUSTOM_SCHEMA, CUSTOM_SCHEMA, logical)
        )
        rev_column_index.setdefault(logical, []).append(
            (CUSTOM_SCHEMA, CUSTOM_SCHEMA, CUSTOM_SCHEMA, phys)
        )


def _most_common(key, entries):
    # User overrides (schema == CUSTOM_SCHEMA) always win outright
    for e in entries:
        if e[0] == CUSTOM_SCHEMA and e[-1] and e[-1] != key:
            return e[-1]
    meaningful = [e for e in entries if e[-1] and e[-1] != key]
    pool = meaningful if meaningful else entries
    return Counter(e[-1] for e in pool).most_common(1)[0][0]


def _is_ambiguous(key, entries):
    """True when there are at least 2 different meaningful logical values.
    User overrides make the result explicit → never ambiguous."""
    if any(e[0] == CUSTOM_SCHEMA for e in entries):
        return False
    distinct = {e[-1] for e in entries if e[-1] and e[-1] != key}
    return len(distinct) > 1


def _filter_entries(entries, schemas=None, tables=None, has_phys_table=True):
    """Strictly filter entries by schemas and physical-table set.

    schemas, tables: sets (empty / None means no restriction).
    has_phys_table=True when entry[1] is a physical table (column + reverse-table entries).
    User-override entries (schema == CUSTOM_SCHEMA) are always kept.
    """
    if not schemas and not tables:
        return entries
    out = []
    for e in entries:
        if e[0] == CUSTOM_SCHEMA:
            out.append(e)      # user overrides bypass filters
            continue
        if schemas and e[0] not in schemas:
            continue
        if tables and has_phys_table and len(e) >= 2 and e[1] not in tables:
            continue
        out.append(e)
    return out


def _filter_by_table_context(entries, table_context):
    """For column entries (schema, phys_table, logical_table, *), prefer those
    whose phys_table or logical_table is mentioned in the input text.
    User-override entries are always kept so custom mappings are never dropped.
    Returns the filtered subset if any match, otherwise the original list."""
    if not table_context or len(entries) <= 1:
        return entries
    if len(entries[0]) < 3:
        return entries
    filtered = [e for e in entries
                if e[0] == CUSTOM_SCHEMA
                or e[1] in table_context
                or e[2] in table_context]
    return filtered if filtered else entries

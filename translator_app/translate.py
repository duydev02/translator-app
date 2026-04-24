import re
from collections.abc import Iterable

from .paths import CUSTOM_SCHEMA
from .schema import (
    ColumnIndex,
    RevColumnIndex,
    RevTableIndex,
    TableIndex,
    _filter_by_table_context,
    _filter_entries,
    _is_ambiguous,
    _most_common,
)


# ── Exclusion range helpers ───────────────────────────────────────────────────
_IDENT_RE = re.compile(r"^\w+$")


def _exclusion_ranges(text, exclusions):
    """Collect (start, end) ranges where each exclusion matches in text.

    - Exclusions that look like identifiers (only \\w chars) are matched with
      word boundaries, so 'ON' won't match inside 'SYSTEM_CONTROL'.
    - Other exclusions (e.g. '■処理区分') use substring matching.
    """
    ranges = []
    for e in exclusions:
        if not e:
            continue
        if _IDENT_RE.match(e):
            # Identifier-like: match as whole word
            pattern = re.compile(r"\b" + re.escape(e) + r"\b")
            for m in pattern.finditer(text):
                ranges.append((m.start(), m.end()))
        else:
            # Substring match (supports Japanese labels, punctuation, etc.)
            start = 0
            while True:
                idx = text.find(e, start)
                if idx == -1:
                    break
                ranges.append((idx, idx + len(e)))
                start = idx + len(e)
    return ranges


def _overlaps_any(pos_start, pos_end, ranges):
    for rs, re_ in ranges:
        if pos_start < re_ and pos_end > rs:
            return True
    return False


# ── Translation ───────────────────────────────────────────────────────────────
_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9_]{1,}\b")


def _tokens(text):
    return list(dict.fromkeys(_TOKEN_RE.findall(text)))


# ── Forward (Physical → Logical) ──────────────────────────────────────────────
def translate_table_mode(
    text: str,
    table_index: TableIndex,
    column_index: ColumnIndex,
    schemas: Iterable[str] | None = None,
    tables: Iterable[str] | None = None,
    table_context: Iterable[str] | None = None,
) -> str:
    tokens = _tokens(text)
    # Forward table_index: KEY is phys_table → apply table filter at lookup level
    matched_tables = {}
    for t in tokens:
        if t not in table_index:
            continue
        if tables and t not in tables:
            continue
        filt = _filter_entries(table_index[t], schemas=schemas, has_phys_table=False)
        if filt:
            matched_tables[t] = filt
    matched_columns = {}
    for t in tokens:
        if t not in column_index:
            continue
        filt = _filter_entries(column_index[t], schemas=schemas, tables=tables, has_phys_table=True)
        if filt:
            matched_columns[t] = filt
    lines = []

    if matched_tables:
        lines.append("━━━  テーブル (Tables)  ━━━")
        for phys, entries in matched_tables.items():
            logical = _most_common(phys, entries)
            amb = " ⚠" if _is_ambiguous(phys, entries) else ""
            lines.append(f"  {phys}{amb}")
            lines.append(f"      → {logical}")
            if len(entries) > 1:
                for schema, lg in entries:
                    lines.append(f"         [{schema}]  {lg}")
            else:
                lines.append(f"         [{entries[0][0]}]")
        lines.append("")

    if matched_columns:
        lines.append("━━━  カラム (Columns)  ━━━")
        for phys, entries in matched_columns.items():
            # Prioritize column entries whose table is in the input
            prioritized = _filter_by_table_context(entries, table_context)
            logical = _most_common(phys, prioritized)
            amb = " ⚠" if _is_ambiguous(phys, prioritized) else ""
            lines.append(f"  {phys}{amb}")
            lines.append(f"      → {logical}")
            # Display: show prioritized first when context is effective
            display = prioritized if prioritized is not entries else entries
            if len(display) > 1:
                for schema, phys_table, logical_table, logical_col in display:
                    lines.append(f"         [{logical_table} ({phys_table}) / {schema}]  {logical_col}")
            else:
                schema, phys_table, logical_table, _ = display[0]
                lines.append(f"         [{logical_table} / {schema}]")
        lines.append("")

    if not matched_tables and not matched_columns:
        lines.append("(一致するテーブル・カラム名が見つかりませんでした)")
    return "\n".join(lines)


def translate_inline_mode(
    text: str,
    table_index: TableIndex,
    column_index: ColumnIndex,
    exclusions: Iterable[str] | None = None,
    schemas: Iterable[str] | None = None,
    tables: Iterable[str] | None = None,
    table_context: Iterable[str] | None = None,
) -> tuple[str, dict, list]:
    tokens = _tokens(text)
    rmap, kinds, ambig = {}, {}, {}
    for t in tokens:
        if t in table_index:
            if tables and t not in tables:
                continue
            entries = _filter_entries(table_index[t], schemas=schemas, has_phys_table=False)
            if not entries:
                continue
            logical = _most_common(t, entries)
            if logical and logical != t:
                rmap[t] = logical
                kinds[t] = "table"
                ambig[t] = _is_ambiguous(t, entries)
        elif t in column_index:
            entries = _filter_entries(column_index[t], schemas=schemas, tables=tables, has_phys_table=True)
            if not entries:
                continue
            entries = _filter_by_table_context(entries, table_context)
            logical = _most_common(t, entries)
            if logical and logical != t:
                rmap[t] = logical
                kinds[t] = "column"
                ambig[t] = _is_ambiguous(t, entries)

    if not rmap:
        return text, {}, []

    excl_ranges = _exclusion_ranges(text, exclusions or [])
    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in rmap) + r")\b")

    out_parts, spans = [], []
    running, pos = 0, 0
    for m in pattern.finditer(text):
        pre = text[pos:m.start()]
        out_parts.append(pre);  running += len(pre)
        tok = m.group(0)
        if _overlaps_any(m.start(), m.end(), excl_ranges):
            out_parts.append(tok);  running += len(tok)
        else:
            translated = rmap[tok]
            start_out = running
            out_parts.append(translated);  running += len(translated)
            spans.append((start_out, running, tok, kinds[tok], ambig[tok]))
        pos = m.end()
    out_parts.append(text[pos:])
    return "".join(out_parts), rmap, spans


def find_column_inconsistencies(column_index: ColumnIndex) -> list[dict]:
    """Scan column_index for columns whose logical name differs across tables.
    Returns list of {phys_col, variants: [{logical, count, tables: [(phys_table, logical_table, schema), ...]}]}
    sorted with most-variants first."""
    results = []
    for phys_col, entries in column_index.items():
        grouped = {}
        for schema, phys_table, logical_table, logical_col in entries:
            if not logical_col or logical_col == phys_col:
                continue  # skip echoes
            if schema == CUSTOM_SCHEMA:
                continue  # user overrides are the fix, not the problem
            grouped.setdefault(logical_col, []).append((schema, phys_table, logical_table))
        if len(grouped) >= 2:
            variants = sorted(
                ({"logical": lg, "count": len(rows), "tables": rows}
                 for lg, rows in grouped.items()),
                key=lambda v: -v["count"],
            )
            results.append({"phys_col": phys_col, "variants": variants})
    results.sort(key=lambda r: (-len(r["variants"]), -sum(v["count"] for v in r["variants"])))
    return results


def find_unknown_tokens(
    text: str,
    table_index: TableIndex,
    column_index: ColumnIndex,
    exclusions: Iterable[str] | None = None,
) -> list[str]:
    """Return uppercase tokens that aren't in either index AND aren't covered by any exclusion."""
    excl_ranges = _exclusion_ranges(text, exclusions or [])
    unknown, seen = [], set()
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok in seen:
            continue
        seen.add(tok)
        if tok in table_index or tok in column_index:
            continue
        if _overlaps_any(m.start(), m.end(), excl_ranges):
            continue
        unknown.append(tok)
    return unknown


# ── Reverse (Logical → Physical) ──────────────────────────────────────────────
def _find_logical_tokens(text, rev_table_index, rev_column_index):
    candidates = sorted(
        set(rev_table_index.keys()) | set(rev_column_index.keys()),
        key=len, reverse=True,
    )
    found, seen = [], set()
    if not candidates:
        return found
    pattern = re.compile("|".join(re.escape(c) for c in candidates))
    for m in pattern.finditer(text):
        name = m.group(0)
        if name in seen:
            continue
        seen.add(name)
        is_table = name in rev_table_index
        found.append((name, is_table))
    return found


def translate_reverse_table_mode(
    text: str,
    rev_table_index: RevTableIndex,
    rev_column_index: RevColumnIndex,
    schemas: Iterable[str] | None = None,
    tables: Iterable[str] | None = None,
    table_context: Iterable[str] | None = None,
) -> str:
    found = _find_logical_tokens(text, rev_table_index, rev_column_index)
    matched_tables = []
    for n, ist in found:
        if not ist:
            continue
        filt = _filter_entries(rev_table_index[n], schemas=schemas, tables=tables, has_phys_table=True)
        if filt:
            matched_tables.append((n, filt))
    matched_columns = []
    for n, ist in found:
        if ist:
            continue
        filt = _filter_entries(rev_column_index[n], schemas=schemas, tables=tables, has_phys_table=True)
        if filt:
            matched_columns.append((n, filt))
    lines = []

    if matched_tables:
        lines.append("━━━  テーブル (Tables)  ━━━")
        for logical, entries in matched_tables:
            phys = _most_common(logical, entries)
            amb = " ⚠" if _is_ambiguous(logical, entries) else ""
            lines.append(f"  {logical}{amb}")
            lines.append(f"      → {phys}")
            if len(entries) > 1:
                for schema, ph in entries:
                    lines.append(f"         [{schema}]  {ph}")
            else:
                lines.append(f"         [{entries[0][0]}]")
        lines.append("")

    if matched_columns:
        lines.append("━━━  カラム (Columns)  ━━━")
        for logical, entries in matched_columns:
            prioritized = _filter_by_table_context(entries, table_context)
            phys = _most_common(logical, prioritized)
            amb = " ⚠" if _is_ambiguous(logical, prioritized) else ""
            lines.append(f"  {logical}{amb}")
            lines.append(f"      → {phys}")
            display = prioritized if prioritized is not entries else entries
            if len(display) > 1:
                for schema, phys_table, logical_table, phys_col in display:
                    lines.append(f"         [{logical_table} ({phys_table}) / {schema}]  {phys_col}")
            else:
                schema, phys_table, logical_table, _ = display[0]
                lines.append(f"         [{logical_table} / {schema}]")
        lines.append("")

    if not matched_tables and not matched_columns:
        lines.append("(一致する論理名が見つかりませんでした)")
    return "\n".join(lines)


def translate_reverse_inline_mode(
    text: str,
    rev_table_index: RevTableIndex,
    rev_column_index: RevColumnIndex,
    exclusions: Iterable[str] | None = None,
    schemas: Iterable[str] | None = None,
    tables: Iterable[str] | None = None,
    table_context: Iterable[str] | None = None,
) -> tuple[str, dict, list]:
    found = _find_logical_tokens(text, rev_table_index, rev_column_index)
    rmap, kinds, ambig = {}, {}, {}
    for name, is_table in found:
        src = rev_table_index[name] if is_table else rev_column_index[name]
        entries = _filter_entries(src, schemas=schemas, tables=tables, has_phys_table=True)
        if not entries:
            continue
        if not is_table:
            entries = _filter_by_table_context(entries, table_context)
        phys = _most_common(name, entries)
        if phys and phys != name:
            rmap[name] = phys
            kinds[name] = "table" if is_table else "column"
            ambig[name] = _is_ambiguous(name, entries)

    if not rmap:
        return text, {}, []

    excl_ranges = _exclusion_ranges(text, exclusions or [])
    pattern = re.compile("|".join(re.escape(k) for k in sorted(rmap.keys(), key=len, reverse=True)))

    out_parts, spans = [], []
    running, pos = 0, 0
    for m in pattern.finditer(text):
        pre = text[pos:m.start()]
        out_parts.append(pre);  running += len(pre)
        tok = m.group(0)
        if _overlaps_any(m.start(), m.end(), excl_ranges):
            out_parts.append(tok);  running += len(tok)
        else:
            translated = rmap[tok]
            start_out = running
            out_parts.append(translated);  running += len(translated)
            spans.append((start_out, running, tok, kinds[tok], ambig[tok]))
        pos = m.end()
    out_parts.append(text[pos:])
    return "".join(out_parts), rmap, spans

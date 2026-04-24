import json
import re
import os
import sys
import tkinter as tk
from tkinter import scrolledtext, font, ttk, filedialog, messagebox
from collections import Counter

# ── Files ─────────────────────────────────────────────────────────────────────
def _app_dir():
    """Folder containing the exe (when frozen) or this script (in dev).

    Important for PyInstaller: sys.frozen is set and sys.executable points to
    the exe. In that case we want data files next to the exe, NOT inside the
    temporary _MEIxxxxxx extraction directory.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR        = _app_dir()
JSON_FILE       = os.path.join(BASE_DIR, "db_schema_output.json")
USER_MAP_FILE   = os.path.join(BASE_DIR, "translator_custom_map.json")
EXCLUSIONS_FILE = os.path.join(BASE_DIR, "translator_exclusions.txt")
SETTINGS_FILE   = os.path.join(BASE_DIR, "translator_settings.json")
HISTORY_FILE    = os.path.join(BASE_DIR, "translator_history.txt")

# Marker "schema" used when injecting user overrides into the indexes so the
# rest of the logic can detect them uniformly.
CUSTOM_SCHEMA   = "(custom)"

MAX_HISTORY = 10

# Optional drag-and-drop support (tkinterdnd2 if available)
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES   # type: ignore
    _DND_AVAILABLE = True
except Exception:
    _DND_AVAILABLE = False


# ── Themes ────────────────────────────────────────────────────────────────────
THEMES = {
    "dark": {
        "bg":          "#1e1e2e",
        "surface":     "#313244",
        "output_bg":   "#181825",
        "fg":          "#cdd6f4",
        "fg_muted":    "#6c7086",
        "accent":      "#89b4fa",
        "accent_fg":   "#1e1e2e",
        "muted_bg":    "#45475a",
        "muted_fg":    "#cdd6f4",
        "success":     "#a6e3a1",
        "warning":     "#f9e2af",
        "info":        "#89dceb",
        "danger":      "#f38ba8",
        "insert":      "#cdd6f4",
        "tag_header":  "#89dceb",
        "tag_phys":    "#f9e2af",
        "tag_logical": "#a6e3a1",
        "tag_meta":    "#585b70",
        "tag_table":   "#89b4fa",   # inline: table replacements
        "tag_column":  "#a6e3a1",   # inline: column replacements
        "tag_ambig":   "#f9e2af",   # inline: ambiguous replacements
        "tag_input_hi":"#89dceb",   # input highlight for known tokens
        "tag_search":  "#f38ba8",
        "placeholder": "#585b70",
    },
    "light": {
        "bg":          "#eff1f5",
        "surface":     "#dce0e8",
        "output_bg":   "#e6e9ef",
        "fg":          "#4c4f69",
        "fg_muted":    "#9ca0b0",
        "accent":      "#1e66f5",
        "accent_fg":   "#ffffff",
        "muted_bg":    "#bcc0cc",
        "muted_fg":    "#4c4f69",
        "success":     "#40a02b",
        "warning":     "#df8e1d",
        "info":        "#04a5e5",
        "danger":      "#d20f39",
        "insert":      "#4c4f69",
        "tag_header":  "#04a5e5",
        "tag_phys":    "#df8e1d",
        "tag_logical": "#40a02b",
        "tag_meta":    "#9ca0b0",
        "tag_table":   "#1e66f5",
        "tag_column":  "#40a02b",
        "tag_ambig":   "#df8e1d",
        "tag_input_hi":"#04a5e5",
        "tag_search":  "#d20f39",
        "placeholder": "#acb0be",
    },
}


# ── Simple persistence ────────────────────────────────────────────────────────
def load_exclusions():
    if not os.path.exists(EXCLUSIONS_FILE):
        return []
    with open(EXCLUSIONS_FILE, "r", encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f if ln.strip()]


def save_exclusions(exclusions):
    with open(EXCLUSIONS_FILE, "w", encoding="utf-8") as f:
        for e in exclusions:
            f.write(e + "\n")


def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        items = [x for x in content.split("\x1e") if x.strip()]
        return items[-MAX_HISTORY:]
    except Exception:
        return []


def save_history(items):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            f.write("\x1e".join(items[-MAX_HISTORY:]))
    except Exception:
        pass


def load_user_map():
    """Return {'tables': {phys: logical}, 'columns': {phys: logical}}."""
    if not os.path.exists(USER_MAP_FILE):
        return {"tables": {}, "columns": {}}
    try:
        with open(USER_MAP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "tables":  dict(data.get("tables")  or {}),
            "columns": dict(data.get("columns") or {}),
        }
    except Exception as e:
        print(f"Warning: could not parse {USER_MAP_FILE}: {e}")
        return {"tables": {}, "columns": {}}


def save_user_map(data):
    # Normalise shape before writing
    out = {
        "tables":  dict(data.get("tables")  or {}),
        "columns": dict(data.get("columns") or {}),
    }
    with open(USER_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


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


# ── Index loading ─────────────────────────────────────────────────────────────
def load_index(json_file):
    """Return (table_index, column_index, rev_table_index, rev_column_index, schemas)."""
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    table_index, column_index = {}, {}
    rev_table_index, rev_column_index = {}, {}
    schemas = list(data.keys())

    for schema, tables in data.items():
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


def merge_user_map(table_index, column_index, rev_table_index, rev_column_index, user_map):
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


# ── Translation ───────────────────────────────────────────────────────────────
_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9_]{1,}\b")


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


def _tokens(text):
    return list(dict.fromkeys(_TOKEN_RE.findall(text)))


# ── Forward (Physical → Logical) ──────────────────────────────────────────────
def translate_table_mode(text, table_index, column_index, schemas=None, tables=None, table_context=None):
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


def translate_inline_mode(text, table_index, column_index, exclusions=None, schemas=None, tables=None, table_context=None):
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


def find_column_inconsistencies(column_index):
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


def find_unknown_tokens(text, table_index, column_index, exclusions=None):
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


def translate_reverse_table_mode(text, rev_table_index, rev_column_index, schemas=None, tables=None, table_context=None):
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


def translate_reverse_inline_mode(text, rev_table_index, rev_column_index, exclusions=None, schemas=None, tables=None, table_context=None):
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


# ── Design Doc: Java SQL-builder → Japanese design document ──────────────────
# Markers for embedded Java expressions (Unicode Private Use Area;
# won't appear in normal SQL text).
_EXPR_OPEN  = "\uE001"
_EXPR_CLOSE = "\uE002"
_EXPR_RE    = re.compile(re.escape(_EXPR_OPEN) + r"(\d+)" + re.escape(_EXPR_CLOSE))

_TAB = "\t"
_COL_TABS  = _TAB * 10   # big gap between column and value/operator
_META_TABS = _TAB * 5    # smaller gap for 【SQL論理名】 / 【SQL定義名】 header lines
_OP_TABS   = _TAB * 2    # gap after operator (＝, etc.) before the value

# Module-level map used by _translate_in_text for alias-based disambiguation.
# Set at the start of each java_to_design_doc call; cleared afterwards.
_CURRENT_ALIAS_MAP = {}

JOIN_PATTERNS = [
    ("LEFT OUTER JOIN",  "左外部結合"),
    ("RIGHT OUTER JOIN", "右外部結合"),
    ("FULL OUTER JOIN",  "完全外部結合"),
    ("LEFT JOIN",        "左外部結合"),
    ("RIGHT JOIN",       "右外部結合"),
    ("FULL JOIN",        "完全外部結合"),
    ("CROSS JOIN",       "交差結合"),
    ("INNER JOIN",       "内部結合"),
    ("JOIN",             "内部結合"),
]


# ── Java parsing ──────────────────────────────────────────────────────────────
def _strip_java_comments(code):
    """Strip // and /* */ comments (including /** */) preserving string literals."""
    out, i, n = [], 0, len(code)
    while i < n:
        c = code[i]
        if c == '"':
            j = i + 1
            while j < n:
                if code[j] == '\\' and j + 1 < n:
                    j += 2; continue
                if code[j] == '"':
                    j += 1; break
                j += 1
            out.append(code[i:j]); i = j
        elif c == "'":
            j = i + 1
            while j < n and code[j] != "'":
                j += 2 if code[j] == '\\' and j + 1 < n else 1
            j += 1
            out.append(code[i:j]); i = j
        elif c == '/' and i + 1 < n and code[i+1] == '/':
            while i < n and code[i] != '\n':
                i += 1
        elif c == '/' and i + 1 < n and code[i+1] == '*':
            i += 2
            while i + 1 < n and not (code[i] == '*' and code[i+1] == '/'):
                i += 1
            i += 2
        else:
            out.append(c); i += 1
    return "".join(out)


def _parse_java_string(s):
    """Unescape a Java \"...\" string literal."""
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return s
    s = s[1:-1]
    out, i, n = [], 0, len(s)
    while i < n:
        if s[i] == '\\' and i + 1 < n:
            nxt = s[i+1]
            table = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b',
                     'f': '\f', '"': '"', "'": "'", '\\': '\\'}
            if nxt in table:
                out.append(table[nxt]); i += 2; continue
            if nxt == 'u' and i + 5 < n:
                try:
                    out.append(chr(int(s[i+2:i+6], 16))); i += 6; continue
                except ValueError:
                    pass
        out.append(s[i]); i += 1
    return "".join(out)


def _split_java_concat(expr):
    """Split a Java expression by top-level '+' (outside strings / parens)."""
    parts, cur = [], []
    i, n = 0, len(expr)
    in_str = in_char = False
    depth = 0
    while i < n:
        c = expr[i]
        if in_str:
            cur.append(c)
            if c == '\\' and i + 1 < n:
                cur.append(expr[i+1]); i += 2; continue
            if c == '"': in_str = False
            i += 1
        elif in_char:
            cur.append(c)
            if c == '\\' and i + 1 < n:
                cur.append(expr[i+1]); i += 2; continue
            if c == "'": in_char = False
            i += 1
        else:
            if c == '"':   in_str = True;  cur.append(c); i += 1
            elif c == "'": in_char = True; cur.append(c); i += 1
            elif c == '(': depth += 1; cur.append(c); i += 1
            elif c == ')': depth -= 1; cur.append(c); i += 1
            elif c == '+' and depth == 0:
                parts.append("".join(cur).strip()); cur = []; i += 1
            else:
                cur.append(c); i += 1
    if cur:
        parts.append("".join(cur).strip())
    return parts


def _extract_appends(code):
    """Return a list of arg-expressions for every `.append(...)` call."""
    results = []
    i, n = 0, len(code)
    while i < n:
        idx = code.find('.append', i)
        if idx == -1:
            break
        # next char after .append must be '(' (allowing whitespace)
        j = idx + 7
        while j < n and code[j].isspace():
            j += 1
        if j >= n or code[j] != '(':
            i = idx + 1; continue
        j += 1
        start = j; depth = 1
        in_str = in_char = False
        while j < n and depth > 0:
            c = code[j]
            if in_str:
                if c == '\\' and j + 1 < n: j += 2; continue
                if c == '"': in_str = False
                j += 1
            elif in_char:
                if c == '\\' and j + 1 < n: j += 2; continue
                if c == "'": in_char = False
                j += 1
            else:
                if c == '"': in_str = True; j += 1
                elif c == "'": in_char = True; j += 1
                elif c == '(': depth += 1; j += 1
                elif c == ')':
                    depth -= 1
                    if depth == 0: break
                    j += 1
                else: j += 1
        if depth == 0:
            results.append(code[start:j])
        i = j + 1
    return results


def _parse_javadoc(code):
    """Return {'description': str, 'params': {name: desc}}."""
    m = re.search(r'/\*\*(.*?)\*/', code, re.DOTALL)
    if not m:
        return {"description": "", "params": {}}
    lines = []
    for ln in m.group(1).split('\n'):
        ln = ln.strip().lstrip('*').strip()
        if ln:
            lines.append(ln)
    desc = ""
    params = {}
    for ln in lines:
        if ln.startswith('@param'):
            pm = re.match(r'@param\s+(\S+)\s*(.*)', ln)
            if pm:
                params[pm.group(1)] = pm.group(2).strip()
        elif ln.startswith('@'):
            continue
        elif not desc:
            desc = ln
    return {"description": desc, "params": params}


def _parse_function_sig(code):
    """Return {'name': str, 'params': [names]}."""
    clean = _strip_java_comments(code)
    # Find first method signature: [modifiers] return_type name(params) [throws] {
    m = re.search(
        r'(?:(?:public|private|protected|static|final|abstract|synchronized|native)\s+)+'
        r'[\w<>\[\],\s]+\s+(\w+)\s*\(([^)]*)\)',
        clean
    )
    if not m:
        m = re.search(r'(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w.,\s]+)?\s*\{', clean)
        if not m:
            return {"name": "", "params": []}
    name = m.group(1)
    params = []
    for p in (m.group(2) or "").split(','):
        p = p.strip()
        if not p:
            continue
        toks = p.split()
        if toks:
            params.append(toks[-1].lstrip('[').rstrip(']'))
    return {"name": name, "params": params}


def _build_sql_from_java(java_code):
    """Extract concatenated SQL with markers for embedded Java expressions.
    Returns (sql_text, expr_map, javadoc_info, func_info)."""
    javadoc = _parse_javadoc(java_code)
    func    = _parse_function_sig(java_code)
    clean   = _strip_java_comments(java_code)
    appends = _extract_appends(clean)

    parts, expr_map, counter = [], {}, [0]
    def add(e):
        idx = counter[0]; counter[0] += 1
        expr_map[idx] = e.strip()
        return f"{_EXPR_OPEN}{idx}{_EXPR_CLOSE}"

    for arg in appends:
        for tok in _split_java_concat(arg):
            tok = tok.strip()
            if not tok:
                continue
            if tok.startswith('"') and tok.endswith('"'):
                parts.append(_parse_java_string(tok))
            else:
                parts.append(add(tok))

    sql = "".join(parts)
    # Strip SQL single-quotes that wrap a pure placeholder: `'<mark>'` → `<mark>`.
    # Java code commonly writes `'" + var + "'` to build a quoted literal; after
    # substitution we no longer need the quotes around the placeholder itself.
    sql = re.sub(
        r"'\s*(" + re.escape(_EXPR_OPEN) + r"\d+" + re.escape(_EXPR_CLOSE) + r")\s*'",
        r"\1",
        sql,
    )
    return sql, expr_map, javadoc, func


# ── SQL parser ────────────────────────────────────────────────────────────────
def _is_top_level(text, pos):
    """True if pos is at paren-depth 0 (ignoring strings)."""
    depth = 0; in_str = in_char = False
    for i in range(min(pos, len(text))):
        c = text[i]
        if in_str:
            if c == '\\' and i + 1 < len(text): pass
            elif c == '"': in_str = False
        elif in_char:
            if c == "'": in_char = False
        else:
            if   c == '"': in_str = True
            elif c == "'": in_char = True
            elif c == '(': depth += 1
            elif c == ')': depth -= 1
    return depth == 0


def _kw_positions(sql, keywords):
    """[(start, end, kw_upper)] for each top-level occurrence.
    When two keywords match at the same start, the longer one wins
    (so `UNION ALL` beats `UNION`, etc.)."""
    positions = []
    for kw in keywords:
        pat = r'\b' + re.escape(kw).replace(r'\ ', r'\s+') + r'\b'
        for m in re.finditer(pat, sql, re.IGNORECASE):
            if _is_top_level(sql, m.start()):
                positions.append((m.start(), m.end(), kw.upper()))
    # Sort by start ASC, end DESC so longer matches at same position come first
    positions.sort(key=lambda p: (p[0], -p[1]))
    cleaned = []
    for s, e, k in positions:
        if cleaned and s < cleaned[-1][1]:
            continue
        cleaned.append((s, e, k))
    return cleaned


def _split_clauses(sql, keywords):
    """Split SQL into {kw: text} at top-level keyword boundaries.
    The part before the first keyword is '_PREFIX'."""
    positions = _kw_positions(sql, keywords)
    out = {}
    prev_end, prev_kw = 0, None
    for s, e, kw in positions:
        (out.setdefault('_PREFIX', sql[prev_end:s].strip())
         if prev_kw is None else out.__setitem__(prev_kw, sql[prev_end:s].strip()))
        prev_end, prev_kw = e, kw
    if prev_kw:
        out[prev_kw] = sql[prev_end:].strip()
    else:
        out['_PREFIX'] = sql[prev_end:].strip()
    return out


def _split_commas_top(text):
    """Split by top-level commas."""
    parts, cur = [], []
    depth = 0; in_str = False
    for c in text:
        if in_str:
            cur.append(c)
            if c == '"' or c == "'": in_str = False
            continue
        if   c == '"' or c == "'": in_str = True; cur.append(c)
        elif c == '(': depth += 1; cur.append(c)
        elif c == ')': depth -= 1; cur.append(c)
        elif c == ',' and depth == 0:
            parts.append("".join(cur).strip()); cur = []
        else:
            cur.append(c)
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _parse_table_ref(text):
    text = text.strip()
    if not text:
        return {"table": "", "alias": ""}

    # Derived table:   (subquery) [AS] alias
    if text.startswith('('):
        depth = 0
        close = -1
        for i, c in enumerate(text):
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close != -1:
            inner = text[1:close].strip()
            after = text[close + 1:].strip()
            if re.match(r'SELECT\s', inner, re.IGNORECASE):
                # Parse the subquery recursively (UNION-aware)
                sub = _parse_select(inner)
                alias = ""
                if after:
                    tks = re.split(r'\s+', after)
                    if tks and tks[0].upper() == 'AS' and len(tks) > 1:
                        alias = tks[1]
                    elif tks:
                        alias = tks[0]
                return {"subquery": sub, "alias": alias, "table": ""}
        # Couldn't balance → treat as opaque
        return {"table": text, "alias": ""}

    parts = re.split(r'\s+', text, maxsplit=2)
    if len(parts) >= 3 and parts[1].upper() == 'AS':
        return {"table": parts[0], "alias": parts[2]}
    if len(parts) >= 2:
        return {"table": parts[0], "alias": parts[1]}
    return {"table": parts[0], "alias": ""}


def _parse_conditions(text):
    """Split a WHERE/ON/HAVING clause into list of conditions."""
    text = text.strip()
    if not text:
        return []
    pat = re.compile(r'\b(AND|OR)\b', re.IGNORECASE)
    positions = [(m.start(), m.end(), m.group(0).upper())
                 for m in pat.finditer(text) if _is_top_level(text, m.start())]
    pieces = []
    prev_end, prev_conn = 0, ''
    for s, e, conn in positions:
        pieces.append((prev_conn, text[prev_end:s].strip()))
        prev_end, prev_conn = e, conn
    pieces.append((prev_conn, text[prev_end:].strip()))

    result = []
    for conn, body in pieces:
        if not body:
            continue
        # col OP value
        op_m = re.search(r'(=|<>|!=|<=|>=|<|>|\bLIKE\b|\bIS\s+NOT\s+NULL\b|\bIS\s+NULL\b|\bIN\b|\bBETWEEN\b)',
                          body, re.IGNORECASE)
        if op_m and _is_top_level(body, op_m.start()):
            result.append({
                "connector": conn,
                "left":  body[:op_m.start()].strip(),
                "op":    op_m.group(0).strip(),
                "right": body[op_m.end():].strip(),
                "raw":   body,
            })
        else:
            result.append({"connector": conn, "left": "", "op": "", "right": "", "raw": body})
    return result


def _parse_from_clause(text):
    text = text.strip()
    join_kws = [kw for kw, _ in JOIN_PATTERNS]
    positions = _kw_positions(text, join_kws)
    if not positions:
        return {"main": _parse_table_ref(text), "joins": []}
    first = positions[0]
    main = _parse_table_ref(text[:first[0]].strip())
    joins = []
    for i, (s, e, kw) in enumerate(positions):
        nxt = positions[i+1][0] if i+1 < len(positions) else len(text)
        body = text[e:nxt].strip()
        on_m = re.search(r'\bON\b', body, re.IGNORECASE)
        if on_m and _is_top_level(body, on_m.start()):
            table_part = body[:on_m.start()].strip()
            on_part    = body[on_m.end():].strip()
        else:
            table_part, on_part = body, ""
        joins.append({
            "kind":      kw,
            "table_ref": _parse_table_ref(table_part),
            "on":        _parse_conditions(on_part) if on_part else [],
        })
    return {"main": main, "joins": joins}


def _parse_sql(sql):
    sql = re.sub(r'/\*\+.*?\*/', ' ', sql, flags=re.DOTALL)
    sql = re.sub(r'\s+', ' ', sql).strip()
    if not sql:
        return {"type": "UNKNOWN"}
    u = sql.lstrip().upper()
    if u.startswith('INSERT'):   return _parse_insert(sql)
    if u.startswith('UPDATE'):   return _parse_update(sql)
    if u.startswith('DELETE'):   return _parse_delete(sql)
    if u.startswith('SELECT'):   return _parse_select(sql)
    if u.startswith('TRUNCATE'):
        m = re.match(r'TRUNCATE\s+(?:TABLE\s+)?(\S+)', sql, re.IGNORECASE)
        return {"type": "TRUNCATE", "target": m.group(1) if m else ""}
    return {"type": "UNKNOWN", "raw": sql}


def _parse_insert(sql):
    m = re.match(
        r'INSERT\s+(?:INTO\s+)?(\S+)(?:\s+NOLOGGING)?\s*(?:\(([^)]*)\))?\s*(.*)',
        sql, re.IGNORECASE | re.DOTALL
    )
    if not m:
        return {"type": "INSERT", "raw": sql}
    result = {
        "type":    "INSERT",
        "target":  m.group(1),
        "columns": [c.strip() for c in (m.group(2) or "").split(',') if c.strip()],
    }
    rest = (m.group(3) or "").strip()
    if rest:
        u = rest.lstrip().upper()
        if u.startswith('VALUES'):
            vm = re.match(r'VALUES\s*\((.*)\)\s*$', rest, re.IGNORECASE | re.DOTALL)
            if vm:
                result["values"] = _split_commas_top(vm.group(1))
        elif u.startswith('SELECT'):
            result["select"] = _parse_select(rest)
    return result


def _parse_update(sql):
    m = re.match(r'UPDATE\s+(\S+)\s+SET\s+(.*)', sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return {"type": "UPDATE", "raw": sql}
    target, rest = m.group(1), m.group(2).strip()
    where_text = ""
    wm = re.search(r'\bWHERE\b', rest, re.IGNORECASE)
    if wm and _is_top_level(rest, wm.start()):
        set_part, where_text = rest[:wm.start()].strip(), rest[wm.end():].strip()
    else:
        set_part = rest
    assignments = []
    for piece in _split_commas_top(set_part):
        em = re.match(r'(.+?)\s*=\s*(.+)', piece, re.DOTALL)
        if em:
            assignments.append({"col": em.group(1).strip(), "value": em.group(2).strip()})
    out = {"type": "UPDATE", "target": target, "set": assignments}
    if where_text:
        out["where"] = _parse_conditions(where_text)
    return out


def _parse_delete(sql):
    m = re.match(r'DELETE\s+(?:FROM\s+)?(\S+)\s*(.*)', sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return {"type": "DELETE", "raw": sql}
    out = {"type": "DELETE", "target": m.group(1)}
    rest = (m.group(2) or "").strip()
    if rest.upper().startswith('WHERE'):
        out["where"] = _parse_conditions(rest[5:].strip())
    return out


def _parse_select(sql):
    """Parse a SELECT statement; if UNION / UNION ALL is present at top level,
    return a SELECT_UNION compound with each branch parsed separately."""
    union_positions = _kw_positions(sql, ['UNION ALL', 'UNION'])
    if union_positions:
        parts, connectors = [], []
        prev_end = 0
        for s, e, kw in union_positions:
            sub = sql[prev_end:s].strip()
            if sub:
                parts.append(_parse_single_select(sub))
                connectors.append(kw)
            prev_end = e
        last = sql[prev_end:].strip()
        if last:
            parts.append(_parse_single_select(last))
        # If only one valid branch materialised, fall back to plain SELECT
        if len(parts) == 1:
            return parts[0]
        return {"type": "SELECT_UNION", "parts": parts, "connectors": connectors}
    return _parse_single_select(sql)


def _parse_single_select(sql):
    m = re.match(r'SELECT\s+(DISTINCT\s+)?', sql, re.IGNORECASE)
    if not m:
        return {"type": "SELECT", "raw": sql}
    distinct = bool(m.group(1))
    after = sql[m.end():]
    clauses = _split_clauses(after, ['FROM', 'WHERE', 'GROUP BY', 'HAVING', 'ORDER BY'])
    out = {
        "type":     "SELECT",
        "distinct": distinct,
        "fields":   _split_commas_top(clauses.get('_PREFIX', '')),
    }
    if 'FROM' in clauses:      out["from"]     = _parse_from_clause(clauses['FROM'])
    if 'WHERE' in clauses:     out["where"]    = _parse_conditions(clauses['WHERE'])
    if 'GROUP BY' in clauses:  out["group_by"] = _split_commas_top(clauses['GROUP BY'])
    if 'HAVING' in clauses:    out["having"]   = _parse_conditions(clauses['HAVING'])
    if 'ORDER BY' in clauses:  out["order_by"] = _split_commas_top(clauses['ORDER BY'])
    return out


# ── Design-doc emitter ────────────────────────────────────────────────────────
def _render_expr(expr, translate_fn=None, uppercase=False):
    """Convert a Java expression into design-doc notation.
    If translate_fn is supplied, rs.getString("COL") translates COL through it.
    """
    expr = expr.strip()
    if not expr:
        return ""
    # var[index]
    m = re.match(r'^(\w+)\[(\d+)\]$', expr)
    if m:
        return f"「引数：{m.group(1)}」[{m.group(2)}]"
    # rs.getString("X")  →  「引数：rs」.<translated X>
    m = re.match(r'^(\w+)\.getString\s*\(\s*"([^"]+)"\s*\)$', expr)
    if m:
        col = m.group(2)
        if translate_fn:
            translated = translate_fn(col)
            if translated != col:
                col = translated
            elif uppercase:
                col = col.upper()
        elif uppercase:
            col = col.upper()
        return f"「引数：{m.group(1)}」.{col}"
    # Simple variable
    if re.match(r'^\w+$', expr):
        return f"「引数：{expr}」"
    # Method call / constant / class reference — passthrough
    return expr


def _translate_in_text(text, translate_fn, uppercase=False):
    """Translate identifier tokens inside a SQL fragment. Preserves placeholders.
    - ALIAS.COL: if ALIAS is in _CURRENT_ALIAS_MAP, scopes COL translation to that table
    - Translated tokens keep their translation (Japanese as-is)
    - Untranslated identifiers: uppercased if requested"""
    result, i, n = [], 0, len(text)
    alias_map = _CURRENT_ALIAS_MAP

    def _cased(s):
        return s.upper() if uppercase else s

    def sub(m):
        left, right = m.group(1), m.group(2)
        if right is not None:
            # ALIAS.COL — scope column lookup to that table when known
            if alias_map and left in alias_map:
                hint = alias_map[left]
                col_translated = translate_fn(right, _hint_table=hint)
                if col_translated != right:
                    return f"{left}.{col_translated}"
                return f"{left}.{_cased(right)}"
            # Unknown alias: translate each half independently
            l_tr = translate_fn(left)
            r_tr = translate_fn(right)
            l_out = l_tr if l_tr != left  else _cased(left)
            r_out = r_tr if r_tr != right else _cased(right)
            return f"{l_out}.{r_out}"
        translated = translate_fn(left)
        if translated != left:
            return translated
        return _cased(left)

    pattern = re.compile(r'\b([A-Za-z_][A-Za-z_0-9]*)(?:\.([A-Za-z_][A-Za-z_0-9]*))?\b')
    while i < n:
        if text[i] == _EXPR_OPEN:
            end = text.find(_EXPR_CLOSE, i)
            if end == -1:
                result.append(text[i]); i += 1; continue
            idx = int(text[i+1:end])
            result.append(f"{_EXPR_OPEN}{idx}{_EXPR_CLOSE}")
            i = end + 1
        else:
            lit_end = i
            while lit_end < n and text[lit_end] != _EXPR_OPEN:
                lit_end += 1
            result.append(pattern.sub(sub, text[i:lit_end]))
            i = lit_end
    return "".join(result)


def _render_placeholders(text, expr_map, translate_fn=None, uppercase=False):
    """Replace \\uE001N\\uE002 markers with rendered Java expressions.
    When a marker is adjacent to literal text, emit ' + ' between them."""
    if _EXPR_OPEN not in text:
        return text
    out = []
    i, n = 0, len(text)
    last_kind = None  # 'lit' | 'expr'
    while i < n:
        if text[i] == _EXPR_OPEN:
            end = text.find(_EXPR_CLOSE, i)
            if end == -1:
                out.append(text[i]); i += 1; continue
            idx = int(text[i+1:end])
            rendered = _render_expr(expr_map.get(idx, ""), translate_fn, uppercase)
            if last_kind == 'lit' and out and out[-1].rstrip():
                out[-1] = out[-1].rstrip()
                out.append(" + ")
            out.append(rendered)
            last_kind = 'expr'
            i = end + 1
        else:
            lit_end = i
            while lit_end < n and text[lit_end] != _EXPR_OPEN:
                lit_end += 1
            lit = text[i:lit_end]
            if last_kind == 'expr' and lit.strip():
                out.append(" + ")
                out.append(lit.strip())
            else:
                out.append(lit)
            last_kind = 'lit'
            i = lit_end
    return "".join(out).strip()


def _render_value(text, expr_map, translate_fn, uppercase=False):
    """Render a value expression: translate names AND expand placeholders."""
    t = _translate_in_text(text, translate_fn, uppercase=False)  # don't uppercase expression contents
    return _render_placeholders(t, expr_map, translate_fn, uppercase)


def _render_name(name, translate_fn, uppercase=False, expr_map=None):
    """Render a single identifier (column or table).
    Handles embedded expression placeholders when expr_map is supplied."""
    if not name:
        return name
    # Placeholder markers (tableName style) → resolve via expr_map
    if expr_map is not None and _EXPR_OPEN in name:
        return _render_placeholders(
            _translate_in_text(name, translate_fn, uppercase=uppercase),
            expr_map, translate_fn, uppercase,
        )
    # ALIAS.NAME — translate only the NAME part
    if '.' in name:
        parts = name.split('.')
        parts = [translate_fn(p) if translate_fn(p) != p else (p.upper() if uppercase else p)
                 for p in parts]
        return ".".join(parts)
    translated = translate_fn(name)
    if translated != name:
        return translated
    return name.upper() if uppercase else name


def _render_target(target_raw, expr_map, translate_fn, uppercase):
    """Render a table target — may be 'LITERAL + placeholder' concatenation."""
    if _EXPR_OPEN not in target_raw:
        return _render_name(target_raw, translate_fn, uppercase)
    return _render_placeholders(
        _translate_in_text(target_raw, translate_fn, uppercase=uppercase),
        expr_map, translate_fn, uppercase,
    )


def _emit_condition_line(cond, expr_map, translate_fn, uppercase, first_prefix=""):
    left  = _translate_in_text(cond.get("left", ""), translate_fn, uppercase=uppercase)
    left  = _render_placeholders(left, expr_map)
    op    = cond.get("op", "").strip()
    disp  = "＝" if op == "=" else op
    right = _render_value(cond.get("right", ""), expr_map, translate_fn)

    conn = cond.get("connector", "") or ""
    if first_prefix:
        label = first_prefix + left
    elif conn:
        label = conn + " " + left
    else:
        label = left
    if left and op:
        return _TAB + label + _COL_TABS + disp + _OP_TABS + right
    # Fallback: raw condition body
    return _TAB + (conn + " " if conn else "") + cond.get("raw", "")


_LONG_THRESHOLD = 10  # > 10 items → move projection section to the end


def _emit_update(parsed, expr_map, translate_fn, uppercase, lines, flags=None):
    flags = flags or {}
    def _emit_target():
        if not flags.get("show_target", True):
            return
        lines.append("■更新テーブル")
        lines.append(_TAB + _render_target(parsed.get("target", ""), expr_map, translate_fn, uppercase))
        lines.append("")

    def _emit_set():
        if not flags.get("show_projection", True):
            return
        lines.append("■更新項目")
        lines.append(_TAB + "カラム名" + _COL_TABS + "セット内容")
        for a in parsed.get("set", []):
            col = _render_name(a["col"], translate_fn, uppercase)
            val = _render_value(a["value"], expr_map, translate_fn)
            lines.append(_TAB + col + _COL_TABS + val)
        lines.append("")

    def _emit_where():
        if not flags.get("show_where", True) or not parsed.get("where"):
            return
        lines.append("■抽出条件")
        for c in parsed["where"]:
            lines.append(_emit_condition_line(c, expr_map, translate_fn, uppercase))
        lines.append("")

    _emit_target()
    long_mode = len(parsed.get("set", [])) > _LONG_THRESHOLD
    if long_mode:
        _emit_where(); _emit_set()
    else:
        _emit_set(); _emit_where()

    if flags.get("show_footer", True):
        lines.append("■実行後処理")
        lines.append(_TAB + "「退避：更新件数」" + _COL_TABS + "＝" + _OP_TABS + "SQL実行した結果の件数")
        lines.append("")


def _emit_delete(parsed, expr_map, translate_fn, uppercase, lines, flags=None):
    flags = flags or {}
    if flags.get("show_target", True):
        lines.append("■対象テーブル")
        lines.append(_TAB + _render_target(parsed.get("target", ""), expr_map, translate_fn, uppercase))
        lines.append("")

    if flags.get("show_where", True) and parsed.get("where"):
        lines.append("■抽出条件")
        for c in parsed["where"]:
            lines.append(_emit_condition_line(c, expr_map, translate_fn, uppercase))
        lines.append("")

    if flags.get("show_footer", True):
        lines.append("■実行後処理")
        lines.append(_TAB + "「退避：削除件数」" + _COL_TABS + "＝" + _OP_TABS + "SQL実行した結果の件数")
        lines.append("")


def _emit_select_block(parsed, expr_map, translate_fn, uppercase, indent=0, flags=None):
    flags = flags or {}
    ind = _TAB * indent
    out = []

    if flags.get("show_stype", True):
        out.append(ind + "■処理区分")
        out.append(ind + _TAB + "SELECT")
        out.append("")

    distinct_str = "  (DISTINCT)" if parsed.get("distinct") else ""

    def _emit_table_ref_item(ref):
        """Render one table reference. Recursively handles (subquery) [AS] alias."""
        if ref.get("subquery"):
            alias = ref.get("alias", "")
            out.append(ind + _TAB + "(")
            out.extend(_emit_select_or_union(
                ref["subquery"], expr_map, translate_fn, uppercase,
                indent=indent + 2, flags=flags,
            ))
            out.append(ind + _TAB + ")" + (_COL_TABS + alias if alias else ""))
        else:
            tbl = _render_name(ref.get("table", ""), translate_fn, uppercase, expr_map)
            alias = ref.get("alias", "")
            out.append(ind + _TAB + tbl + (_COL_TABS + alias if alias else ""))

    def _emit_projection():
        if not flags.get("show_projection", True):
            return
        out.append(ind + "■抽出項目" + distinct_str)
        for f in parsed.get("fields", []):
            out.append(ind + _TAB + _render_value(f, expr_map, translate_fn))
        out.append("")

    def _emit_from_and_join():
        from_info = parsed.get("from")
        if not (from_info and from_info.get("main")):
            return
        if flags.get("show_from", True):
            out.append(ind + "■抽出テーブル")
            _emit_table_ref_item(from_info["main"])
            out.append("")
        if flags.get("show_join", True) and from_info.get("joins"):
            out.append(ind + "■結合条件")
            for join in from_info["joins"]:
                ja = dict(JOIN_PATTERNS).get(join["kind"], join["kind"])
                out.append(ind + _TAB + ja)
                _emit_table_ref_item(join["table_ref"])
                for i, c in enumerate(join.get("on", [])):
                    prefix = "ON " if i == 0 else ""
                    out.append(ind + _TAB + _emit_condition_line(
                        c, expr_map, translate_fn, uppercase, first_prefix=prefix).lstrip())
            out.append("")

    def _emit_where():
        if flags.get("show_where", True) and parsed.get("where"):
            out.append(ind + "■抽出条件")
            for c in parsed["where"]:
                out.append(ind + _TAB + _emit_condition_line(c, expr_map, translate_fn, uppercase).lstrip())
            out.append("")

    def _emit_group():
        if flags.get("show_group", True) and parsed.get("group_by"):
            out.append(ind + "■グループ化条件")
            for g in parsed["group_by"]:
                out.append(ind + _TAB + _render_value(g, expr_map, translate_fn))
            out.append("")

    def _emit_having():
        if flags.get("show_having", True) and parsed.get("having"):
            out.append(ind + "■集計後抽出条件")
            for c in parsed["having"]:
                out.append(ind + _TAB + _emit_condition_line(c, expr_map, translate_fn, uppercase).lstrip())
            out.append("")

    def _emit_order():
        if flags.get("show_order", True) and parsed.get("order_by"):
            out.append(ind + "■並び順")
            for o in parsed["order_by"]:
                out.append(ind + _TAB + _render_value(o, expr_map, translate_fn))
            out.append("")

    # Projection at end only when there are many fields (> _LONG_THRESHOLD)
    long_mode = len(parsed.get("fields", [])) > _LONG_THRESHOLD
    if long_mode:
        _emit_from_and_join(); _emit_where(); _emit_group(); _emit_having(); _emit_order()
        _emit_projection()
    else:
        _emit_projection()
        _emit_from_and_join(); _emit_where(); _emit_group(); _emit_having(); _emit_order()

    return out


def _emit_select_or_union(parsed, expr_map, translate_fn, uppercase, indent=0, flags=None):
    """Emit a SELECT or SELECT_UNION (branches separated by UNION [ALL])."""
    if parsed.get("type") == "SELECT_UNION":
        out = []
        for i, part in enumerate(parsed["parts"]):
            if i > 0:
                out.append("")
                out.append(_TAB * indent + parsed["connectors"][i-1])
                out.append("")
            out.extend(_emit_select_block(part, expr_map, translate_fn, uppercase, indent, flags))
        return out
    return _emit_select_block(parsed, expr_map, translate_fn, uppercase, indent, flags)


def _emit_insert(parsed, expr_map, translate_fn, uppercase, lines, flags=None):
    flags = flags or {}

    def _emit_target():
        if not flags.get("show_target", True):
            return
        lines.append("■登録テーブル")
        lines.append(_TAB + _render_target(parsed.get("target", ""), expr_map, translate_fn, uppercase))
        lines.append("")

    columns = parsed.get("columns", [])
    select_part = parsed.get("select")
    values = parsed.get("values")

    def _emit_nested_select():
        if not select_part:
            return
        if not flags.get("show_from", True):
            return
        lines.append("■抽出テーブル")
        lines.append(_TAB + "(")
        lines.extend(_emit_select_or_union(select_part, expr_map, translate_fn, uppercase,
                                           indent=2, flags=flags))
        lines.append(_TAB + ")")
        lines.append("")

    def col_label(i, field_text):
        if columns and i < len(columns):
            return _render_name(columns[i], translate_fn, uppercase)
        if field_text.strip() == 'T.*':
            return f"({i+1}列目以降)"
        return f"({i+1}列目)"

    # Pick fields from select (take first UNION branch's fields if applicable)
    fields = []
    if select_part:
        if select_part.get("type") == "SELECT_UNION" and select_part.get("parts"):
            fields = select_part["parts"][0].get("fields", [])
        else:
            fields = select_part.get("fields", [])

    def _emit_mapping():
        if not flags.get("show_projection", True):
            return
        lines.append("■項目移送")
        lines.append(_TAB + "カラム名" + _COL_TABS + "セット内容")
        if fields:
            for i, f in enumerate(fields):
                lines.append(_TAB + col_label(i, f) + _COL_TABS +
                             _render_value(f, expr_map, translate_fn))
        elif values:
            for i, v in enumerate(values):
                lines.append(_TAB + col_label(i, v) + _COL_TABS +
                             _render_value(v, expr_map, translate_fn))
        lines.append("")

    _emit_target()

    # Apply the ">10" rule for the mapping table order
    n_items = len(fields) if fields else len(values or [])
    long_mode = n_items > _LONG_THRESHOLD
    if long_mode:
        _emit_nested_select()
        _emit_mapping()
    else:
        _emit_mapping()
        _emit_nested_select()


def _build_alias_map(parsed):
    """Collect alias → physical-table mapping from a parsed SQL AST.
    Used by _translate_in_text so that `RS.SYSTEM_KB` resolves to the
    SYSTEM_KB that belongs specifically to RS's table, not any other."""
    aliases = {}

    def walk(sel):
        if not sel:
            return
        if sel.get("type") == "SELECT_UNION":
            for p in sel.get("parts", []):
                walk(p)
            return
        fi = sel.get("from") or {}
        main = fi.get("main") or {}
        if main.get("alias") and main.get("table"):
            aliases[main["alias"]] = main["table"]
        if main.get("subquery"):
            walk(main["subquery"])
        for j in fi.get("joins", []):
            ref = j.get("table_ref", {}) or {}
            if ref.get("alias") and ref.get("table"):
                aliases[ref["alias"]] = ref["table"]
            if ref.get("subquery"):
                walk(ref["subquery"])

    if parsed.get("type") in ("SELECT", "SELECT_UNION"):
        walk(parsed)
    elif parsed.get("type") == "INSERT" and parsed.get("select"):
        walk(parsed["select"])
    return aliases


def _build_table_context(parsed, table_index):
    """Collect physical table names used in this SQL for translator priority."""
    ctx = set()
    def collect(s):
        if not s:
            return
        s = _EXPR_RE.sub('', s)
        for tok in re.findall(r'\b[A-Za-z_][A-Za-z_0-9]*\b', s):
            if tok in table_index:
                ctx.add(tok)

    def walk_select(sel):
        if not sel:
            return
        if sel.get("type") == "SELECT_UNION":
            for p in sel.get("parts", []):
                walk_select(p)
            return
        fi = sel.get("from") or {}
        main = fi.get("main") or {}
        if main.get("subquery"):
            walk_select(main["subquery"])
        else:
            collect(main.get("table", ""))
        for j in fi.get("joins", []):
            ref = j.get("table_ref", {}) or {}
            if ref.get("subquery"):
                walk_select(ref["subquery"])
            else:
                collect(ref.get("table", ""))

    collect(parsed.get("target", ""))
    if parsed.get("type") in ("SELECT", "SELECT_UNION"):
        walk_select(parsed)
    elif parsed.get("type") == "INSERT" and parsed.get("select"):
        walk_select(parsed["select"])
    return ctx


def java_to_design_doc(java_code, table_index, column_index,
                       rev_table_index, rev_column_index,
                       schemas=None, tables=None,
                       uppercase=False, direction="forward",
                       show_overview=True, show_sql_logical=True,
                       show_sql_physical=True, show_stype=True,
                       show_target=True, show_projection=True,
                       show_from=True, show_join=True, show_where=True,
                       show_group=True, show_having=True, show_order=True,
                       show_footer=True):
    """Top-level entry point: Java method text → design-doc string."""
    try:
        sql, expr_map, javadoc, func = _build_sql_from_java(java_code)
    except Exception as e:
        return f"(Java parse error: {e})"

    if not sql.strip():
        return "(No SQL found — did the method use sb.append(...)?)"

    parsed = _parse_sql(sql)
    if parsed.get("type") == "UNKNOWN":
        return f"(Unknown SQL statement type)\n\n{sql}"

    ctx = _build_table_context(parsed, table_index)

    # Build alias → physical-table map, make it available to _translate_in_text
    global _CURRENT_ALIAS_MAP
    _CURRENT_ALIAS_MAP = _build_alias_map(parsed)

    def _scope_by_hint(entries, hint_table):
        """Narrow column entries to those whose phys_table / logical_table
        matches the alias-hinted table. Fall back to all entries if none match."""
        if not hint_table or len(entries) <= 1 or len(entries[0]) < 3:
            return entries
        filtered = [e for e in entries
                    if e[1] == hint_table or e[2] == hint_table]
        return filtered if filtered else entries

    def translate_fn(name, _hint_table=None):
        if not name or _EXPR_OPEN in name:
            return name
        if direction == "forward":
            if name in table_index:
                e = _filter_entries(table_index[name], schemas=schemas, has_phys_table=False)
                if e:
                    return _most_common(name, e)
            if name in column_index:
                e = _filter_entries(column_index[name], schemas=schemas, tables=tables)
                if _hint_table:
                    e = _scope_by_hint(e, _hint_table)
                else:
                    e = _filter_by_table_context(e, ctx)
                if e:
                    return _most_common(name, e)
        else:
            if name in rev_table_index:
                e = _filter_entries(rev_table_index[name], schemas=schemas, tables=tables)
                if e:
                    return _most_common(name, e)
            if name in rev_column_index:
                e = _filter_entries(rev_column_index[name], schemas=schemas, tables=tables)
                if _hint_table:
                    e = _scope_by_hint(e, _hint_table)
                else:
                    e = _filter_by_table_context(e, ctx)
                if e:
                    return _most_common(name, e)
        return name

    lines = []
    if show_overview:
        lines.append("■処理概要")
        lines.append(_TAB + "(説明を追加)")
        lines.append("")
    desc = javadoc.get("description", "")
    if show_sql_logical:
        lines.append("【SQL論理名】" + _META_TABS + "：" + _TAB + desc)
    if show_sql_physical:
        lines.append("【SQL定義名】" + _META_TABS + "：" + _TAB + func.get("name", ""))
    if show_sql_logical or show_sql_physical:
        lines.append("")

    stype = parsed.get("type", "")
    if show_stype:
        lines.append("■処理区分")
        lines.append(_TAB + stype)
        lines.append("")

    # Bundle section flags for the emit functions
    flags = {
        "show_target":     show_target,
        "show_projection": show_projection,
        "show_from":       show_from,
        "show_join":       show_join,
        "show_where":      show_where,
        "show_group":      show_group,
        "show_having":     show_having,
        "show_order":      show_order,
        "show_stype":      show_stype,
        "show_footer":     show_footer,
    }

    if   stype == "UPDATE":       _emit_update(parsed, expr_map, translate_fn, uppercase, lines, flags)
    elif stype == "INSERT":       _emit_insert(parsed, expr_map, translate_fn, uppercase, lines, flags)
    elif stype == "DELETE":       _emit_delete(parsed, expr_map, translate_fn, uppercase, lines, flags)
    elif stype in ("SELECT", "SELECT_UNION"):
        lines.extend(_emit_select_or_union(parsed, expr_map, translate_fn, uppercase, indent=0, flags=flags))
    elif stype == "TRUNCATE":
        if show_target:
            lines.append("■対象テーブル")
            lines.append(_TAB + _render_target(parsed.get("target", ""), expr_map, translate_fn, uppercase))
            lines.append("")

    _CURRENT_ALIAS_MAP = {}   # clear module-level state
    return "\n".join(lines)


# ── Floating UI helpers ───────────────────────────────────────────────────────
class Tooltip:
    def __init__(self, parent):
        self.parent = parent
        self.tw = None
        self._label = None
        self._theme_fn = lambda: THEMES["light"]

    def set_theme_fn(self, fn):
        self._theme_fn = fn

    def show(self, text, x_root, y_root):
        t = self._theme_fn()
        if self.tw is None:
            self.tw = tk.Toplevel(self.parent)
            self.tw.wm_overrideredirect(True)
            self.tw.attributes("-topmost", True)
            self._label = tk.Label(
                self.tw, text=text, justify="left",
                bg=t["surface"], fg=t["fg"],
                font=("Segoe UI", 9), padx=10, pady=6,
                bd=1, relief="solid",
            )
            self._label.pack()
        else:
            self._label.configure(text=text, bg=t["surface"], fg=t["fg"])
        self.tw.wm_geometry(f"+{x_root + 16}+{y_root + 18}")

    def hide(self):
        if self.tw:
            self.tw.destroy()
            self.tw = None


class Toast:
    def __init__(self, parent):
        self.parent = parent
        self.tw = None
        self._job = None
        self._theme_fn = lambda: THEMES["light"]

    def set_theme_fn(self, fn):
        self._theme_fn = fn

    def show(self, text, duration=1800, kind="info"):
        t = self._theme_fn()
        color_map = {"info": t["accent"], "success": t["success"], "error": t["danger"]}
        bg = color_map.get(kind, t["accent"])
        fg = t["accent_fg"] if kind == "info" else "#1e1e2e"
        if self.tw:
            self.tw.destroy()
        self.tw = tk.Toplevel(self.parent)
        self.tw.wm_overrideredirect(True)
        self.tw.attributes("-topmost", True)
        lbl = tk.Label(
            self.tw, text=text,
            bg=bg, fg=fg,
            font=("Segoe UI", 10, "bold"),
            padx=18, pady=8, bd=0,
        )
        lbl.pack()
        # Position in bottom-right of parent window
        self.parent.update_idletasks()
        px = self.parent.winfo_rootx() + self.parent.winfo_width() - 260
        py = self.parent.winfo_rooty() + self.parent.winfo_height() - 80
        self.tw.wm_geometry(f"+{max(px, 0)}+{max(py, 0)}")
        if self._job:
            try: self.parent.after_cancel(self._job)
            except Exception: pass
        self._job = self.parent.after(duration, self.hide)

    def hide(self):
        if self.tw:
            try: self.tw.destroy()
            except Exception: pass
            self.tw = None


# ── Main Application ──────────────────────────────────────────────────────────
_BaseTk = TkinterDnD.Tk if _DND_AVAILABLE else tk.Tk


class TranslatorApp(_BaseTk):
    def __init__(self, json_path):
        super().__init__()

        # Load persistent state
        self._settings   = load_settings()
        self._exclusions = load_exclusions()
        self._history    = load_history()

        # Load index
        self._json_path = json_path
        self._load_data()

        # Mutable state (persisted in settings)
        self._theme       = self._settings.get("theme", "light")
        self._mode        = tk.StringVar(value=self._settings.get("mode", "inline"))
        self._direction   = tk.StringVar(value=self._settings.get("direction", "forward"))
        self._filter_schemas = set(self._settings.get("filter_schemas", []))   # empty = all
        self._filter_tables  = set(self._settings.get("filter_tables",  []))   # empty = all
        self._font_size   = int(self._settings.get("font_size", 10))
        # "vertical" = input on top, output on bottom (default)
        # "horizontal" = input on left, output on right
        self._pane_orient = self._settings.get("pane_orient", "vertical")
        if self._pane_orient not in ("vertical", "horizontal"):
            self._pane_orient = "vertical"

        # Transient state
        self._copy_job     = None
        self._autotr_job   = None
        self._input_hi_job = None
        self._spans         = []
        self._table_context = set()
        self._tooltip       = None
        self._toast         = None

        self.title("Translator — Legacy Schema Helper")
        # Window-title-bar icon (separate from the exe's Explorer icon).
        # Search order:
        #   1. Bundled location (sys._MEIPASS/image.ico) when running as exe
        #   2. assets/image.ico  (source-checkout layout)
        #   3. image.ico         (legacy flat layout, backward compat)
        try:
            candidates = []
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                candidates.append(os.path.join(sys._MEIPASS, "image.ico"))
            candidates.extend([
                os.path.join(BASE_DIR, "assets", "image.ico"),
                os.path.join(BASE_DIR, "image.ico"),
            ])
            for p in candidates:
                if os.path.exists(p):
                    self.iconbitmap(p)
                    break
        except Exception:
            pass
        geom = self._settings.get("geometry", "1060x800")
        self.geometry(geom)
        self.minsize(780, 540)

        self._mono    = font.Font(family="Consolas", size=self._font_size)
        self._ui      = font.Font(family="Segoe UI", size=9)
        self._ui_b    = font.Font(family="Segoe UI", size=9,  weight="bold")
        self._btn     = font.Font(family="Segoe UI", size=10, weight="bold")
        self._small   = font.Font(family="Segoe UI", size=8)

        self._build()
        self._apply_theme()
        self._refresh_mode_tabs()
        self._refresh_excl_btn()
        self._refresh_filter_btn()
        self._refresh_umap_btn()
        self._refresh_layout_btn()
        self._refresh_history_menu()
        self._set_direction_label()
        self._show_placeholder_if_empty()

        # Bindings
        self.input_box.bind("<Control-Return>", self._on_ctrl_enter)
        self.bind_all("<Control-BackSpace>",  lambda e: self.on_clear())
        self.bind_all("<Control-Shift-C>",    lambda e: self.on_copy())
        self.bind_all("<Control-s>",          lambda e: self.on_export())
        self.bind_all("<Control-r>",          lambda e: self.on_reload_json())
        self.bind_all("<Control-f>",          lambda e: self.open_search_bar())
        self.bind_all("<Escape>",             lambda e: self.close_search_bar())
        self.bind_all("<F1>",                 lambda e: self.show_help_dialog())
        self.bind_all("<Control-m>",          lambda e: self.toggle_mode())
        self.bind_all("<Control-Shift-D>",    lambda e: self.toggle_direction())
        self.bind_all("<Control-plus>",       lambda e: self.zoom_in())
        self.bind_all("<Control-equal>",      lambda e: self.zoom_in())
        self.bind_all("<Control-minus>",      lambda e: self.zoom_out())
        self.bind_all("<Control-0>",          lambda e: self.zoom_reset())

        # Right-click context menu
        self.input_box.bind("<Button-3>",  lambda e: self._on_right_click(e, self.input_box))
        self.output_box.bind("<Button-3>", lambda e: self._on_right_click(e, self.output_box))

        # Auto-translate + input highlight
        self.input_box.bind("<KeyRelease>", self._on_input_change)
        self.input_box.bind("<<Paste>>",    self._on_paste)

        # Focus-in / focus-out for placeholder
        self.input_box.bind("<FocusIn>",  lambda e: self._clear_placeholder())
        self.input_box.bind("<FocusOut>", lambda e: self._show_placeholder_if_empty())

        # Hover tooltip + toast
        self._tooltip = Tooltip(self)
        self._tooltip.set_theme_fn(lambda: THEMES[self._theme])
        self._toast = Toast(self)
        self._toast.set_theme_fn(lambda: THEMES[self._theme])
        self.output_box.bind("<Motion>", self._on_output_motion)
        self.output_box.bind("<Leave>",  lambda e: self._tooltip.hide())

        # Drag & drop
        if _DND_AVAILABLE:
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self._on_file_drop)
            except Exception:
                pass

        # Save settings on exit
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ── Data loading ──────────────────────────────────────────────────────────
    def _load_data(self):
        ti, ci, rti, rci, schemas = load_index(self._json_path)
        self._user_map = load_user_map()
        merge_user_map(ti, ci, rti, rci, self._user_map)

        self.table_index      = ti
        self.column_index     = ci
        self.rev_table_index  = rti
        self.rev_column_index = rci
        # CUSTOM_SCHEMA is exposed in the filter dropdown if user has any overrides
        self.schemas = list(schemas)
        if (self._user_map.get("tables") or self._user_map.get("columns")):
            if CUSTOM_SCHEMA not in self.schemas:
                self.schemas.append(CUSTOM_SCHEMA)

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build(self):
        # ── Top bar ──────────────────────────────────────────────────────────
        self._topbar = tk.Frame(self, height=48)
        self._topbar.pack(fill="x")
        self._topbar.pack_propagate(False)

        self._tab_frame = tk.Frame(self._topbar)
        self._tab_frame.pack(side="left", padx=12, pady=8)
        tab_frame = self._tab_frame

        self._tab_table = tk.Button(tab_frame, text="Translation Table",
            font=self._ui_b, relief="flat", padx=14, pady=4, cursor="hand2", bd=0,
            command=lambda: self._set_mode("table"))
        self._tab_table.pack(side="left")

        self._tab_inline = tk.Button(tab_frame, text="Inline Replace",
            font=self._ui_b, relief="flat", padx=14, pady=4, cursor="hand2", bd=0,
            command=lambda: self._set_mode("inline"))
        self._tab_inline.pack(side="left", padx=(2, 0))

        self._tab_designdoc = tk.Button(tab_frame, text="Design Doc",
            font=self._ui_b, relief="flat", padx=14, pady=4, cursor="hand2", bd=0,
            command=lambda: self._set_mode("designdoc"))
        self._tab_designdoc.pack(side="left", padx=(2, 0))

        self._tab_sep = tk.Label(tab_frame, text="│", font=self._ui_b)
        self._tab_sep.pack(side="left", padx=10)

        self._tab_forward = tk.Button(tab_frame, text="Phys → Logic",
            font=self._ui_b, relief="flat", padx=14, pady=4, cursor="hand2", bd=0,
            command=lambda: self._set_direction("forward"))
        self._tab_forward.pack(side="left")

        self._tab_reverse = tk.Button(tab_frame, text="Logic → Phys",
            font=self._ui_b, relief="flat", padx=14, pady=4, cursor="hand2", bd=0,
            command=lambda: self._set_direction("reverse"))
        self._tab_reverse.pack(side="left", padx=(2, 0))

        # Right side: theme, help, exclusions, schema filter
        self._theme_btn = tk.Button(self._topbar, text="☀  Light",
            font=self._ui_b, relief="flat", padx=12, pady=4, cursor="hand2", bd=0,
            command=self.toggle_theme)
        self._theme_btn.pack(side="right", padx=(6, 12), pady=8)

        # Layout-orientation toggle (right, left of theme)
        self._layout_btn = tk.Button(self._topbar,
            font=self._ui_b, relief="flat", padx=10, pady=4, cursor="hand2", bd=0,
            command=self.toggle_pane_orient)
        self._layout_btn.pack(side="right", pady=8)

        self._help_btn = tk.Button(self._topbar, text="?",
            font=self._ui_b, relief="flat", padx=10, pady=4, cursor="hand2", bd=0,
            command=self.show_help_dialog)
        self._help_btn.pack(side="right", pady=8)

        self._excl_btn = tk.Button(self._topbar, text="⊘  Exclusions",
            font=self._ui_b, relief="flat", padx=12, pady=4, cursor="hand2", bd=0,
            command=self.open_exclusions_dialog)
        self._excl_btn.pack(side="right", padx=(0, 6), pady=8)

        # User-defined override map
        self._umap_btn = tk.Button(self._topbar, text="🖉  User Map",
            font=self._ui_b, relief="flat", padx=12, pady=4, cursor="hand2", bd=0,
            command=self.open_user_map_dialog)
        self._umap_btn.pack(side="right", padx=(0, 6), pady=8)

        # Filter button (replaces the old single-schema combobox)
        self._filter_btn = tk.Button(
            self._topbar, text="⚙  Filter",
            font=self._ui_b, relief="flat", padx=12, pady=4,
            cursor="hand2", bd=0, command=self.open_filter_dialog,
        )
        self._filter_btn.pack(side="right", padx=(0, 6), pady=8)

        # ── Paned window: input / output (orientation toggleable) ───────────
        # Using tk.PanedWindow (not ttk) because it supports `minsize`, which
        # prevents dragging the sash over the action bar.
        self._paned = tk.PanedWindow(
            self, orient=self._pane_orient,
            sashwidth=6, sashrelief="flat", bd=0, showhandle=False,
            opaqueresize=True,
        )
        self._paned.pack(fill="both", expand=True, padx=12, pady=(2, 0))

        # Top / left pane: input + header + action bar
        # Parent to self (not self._paned) so pane survives a paned.destroy()
        # during orientation toggling.
        top_pane = tk.Frame(self)
        self._top_pane = top_pane
        self._paned.add(top_pane, minsize=self._top_minsize(), stretch="always")

        # Pack order matters: anchor header top, action bar bottom FIRST
        # so those zones are reserved before the expanding input fills the middle.
        in_header = tk.Frame(top_pane)
        in_header.pack(side="top", fill="x", pady=(6, 2))

        self._lbl_in = tk.Label(in_header, text="Paste content here", font=self._ui_b, anchor="w")
        self._lbl_in.pack(side="left")

        self._hint_in = tk.Label(in_header,
            text="Ctrl+Enter translate · Ctrl+⌫ clear · F1 help",
            font=self._small, anchor="e")
        self._hint_in.pack(side="right")

        # Input box + history dropdown
        self._history_btn = tk.Menubutton(in_header, text="⌄ History",
            font=self._small, relief="flat", bd=0, padx=6, pady=0, cursor="hand2")
        self._history_menu = tk.Menu(self._history_btn, tearoff=0)
        self._history_btn["menu"] = self._history_menu
        self._history_btn.pack(side="right", padx=(6, 10))

        # Action bar — packed BEFORE the input so it reserves its vertical space
        self._actionbar = tk.Frame(top_pane, height=46)
        self._actionbar.pack(side="bottom", fill="x", pady=8)
        self._actionbar.pack_propagate(False)

        # Input fills whatever's left between header and action bar
        self.input_box = scrolledtext.ScrolledText(
            top_pane, wrap=tk.WORD, font=self._mono,
            relief="flat", borderwidth=0, padx=10, pady=8, undo=True,
        )
        self.input_box.pack(side="top", fill="both", expand=True)

        self._translate_btn = tk.Button(self._actionbar, text="▶  Translate",
            font=self._btn, relief="flat", padx=20, pady=6, cursor="hand2", bd=0,
            command=self.on_translate)
        self._translate_btn.pack(side="left")

        self._clear_btn = tk.Button(self._actionbar, text="✕  Clear",
            font=self._btn, relief="flat", padx=14, pady=6, cursor="hand2", bd=0,
            command=self.on_clear)
        self._clear_btn.pack(side="left", padx=(6, 0))

        self._open_btn = tk.Button(self._actionbar, text="📂  Open",
            font=self._btn, relief="flat", padx=12, pady=6, cursor="hand2", bd=0,
            command=self.on_open_file)
        self._open_btn.pack(side="left", padx=(6, 0))

        self._reload_btn = tk.Button(self._actionbar, text="⟳  Reload JSON",
            font=self._btn, relief="flat", padx=12, pady=6, cursor="hand2", bd=0,
            command=self.on_reload_json)
        self._reload_btn.pack(side="left", padx=(6, 0))

        # Uppercase toggle — only visible in Design Doc mode (see _refresh_mode_tabs)
        self._uppercase_var = tk.BooleanVar(value=bool(self._settings.get("design_uppercase", True)))
        self._upper_chk = tk.Checkbutton(
            self._actionbar, text="UPPERCASE columns",
            variable=self._uppercase_var, font=self._ui,
            bd=0, highlightthickness=0,
            command=self.on_translate,
        )

        # Design-doc section visibility toggles
        def _flag(key, default=True):
            return tk.BooleanVar(value=bool(self._settings.get(key, default)))

        self._show_overview     = _flag("design_show_overview")
        self._show_sql_logical  = _flag("design_show_sql_logical")
        self._show_sql_physical = _flag("design_show_sql_physical")
        self._show_stype        = _flag("design_show_stype")
        self._show_target       = _flag("design_show_target")
        self._show_projection   = _flag("design_show_projection")
        self._show_from         = _flag("design_show_from")
        self._show_join         = _flag("design_show_join")
        self._show_where        = _flag("design_show_where")
        self._show_group        = _flag("design_show_group")
        self._show_having       = _flag("design_show_having")
        self._show_order        = _flag("design_show_order")
        self._show_footer       = _flag("design_show_footer")

        self._sections_mb = tk.Button(
            self._actionbar, text="⚙ Sections ▾", font=self._ui_b,
            relief="flat", bd=0, padx=10, pady=6, cursor="hand2",
            command=self.toggle_sections_popup,
        )
        self._sections_popup = None    # held open until user clicks elsewhere / Escape
        # Not packed yet — handled by _refresh_mode_tabs

        self._status_var = tk.StringVar(value="")
        self._status_lbl = tk.Label(self._actionbar, textvariable=self._status_var,
            font=self._ui, anchor="w")
        self._status_lbl.pack(side="left", padx=14)

        # ── Bottom / right pane: output ──────────────────────────────────────
        # Parent to self (same reasoning as top_pane).
        bot_pane = tk.Frame(self)
        self._bot_pane = bot_pane
        self._paned.add(bot_pane, minsize=self._bot_minsize(), stretch="always")

        out_header = tk.Frame(bot_pane)
        out_header.pack(fill="x", pady=(2, 2))

        self._lbl_out = tk.Label(out_header, text="Translation result", font=self._ui_b, anchor="w")
        self._lbl_out.pack(side="left")

        self._copy_btn = tk.Button(out_header, text="⎘  Copy",
            font=self._ui_b, relief="flat", padx=10, pady=2, cursor="hand2", bd=0,
            command=self.on_copy)
        self._copy_btn.pack(side="right")

        self._save_btn = tk.Button(out_header, text="💾  Save…",
            font=self._ui_b, relief="flat", padx=10, pady=2, cursor="hand2", bd=0,
            command=self.on_export)
        self._save_btn.pack(side="right", padx=(0, 6))

        self._hint_out = tk.Label(out_header, text="Ctrl+C copy · Ctrl+S save · Ctrl+F find",
            font=self._small, anchor="e")
        self._hint_out.pack(side="right", padx=(0, 10))

        # Search bar (initially hidden)
        self._search_frame = tk.Frame(bot_pane)
        self._search_var = tk.StringVar()
        self._search_entry = tk.Entry(self._search_frame, textvariable=self._search_var,
            font=self._ui, relief="flat", bd=0)
        self._search_entry.pack(side="left", fill="x", expand=True, padx=(4, 6), pady=4, ipady=4)
        self._search_entry.bind("<Return>",  lambda e: self._search_next())
        self._search_entry.bind("<KeyRelease>", lambda e: self._search_highlight_all())
        self._search_prev_btn = tk.Button(self._search_frame, text="▲",
            font=self._small, relief="flat", bd=0, cursor="hand2",
            command=self._search_prev)
        self._search_prev_btn.pack(side="left", padx=2)
        self._search_next_btn = tk.Button(self._search_frame, text="▼",
            font=self._small, relief="flat", bd=0, cursor="hand2",
            command=self._search_next)
        self._search_next_btn.pack(side="left", padx=2)
        self._search_count_var = tk.StringVar(value="")
        self._search_count_lbl = tk.Label(self._search_frame,
            textvariable=self._search_count_var, font=self._small)
        self._search_count_lbl.pack(side="left", padx=6)
        self._search_close_btn = tk.Button(self._search_frame, text="✕",
            font=self._small, relief="flat", bd=0, cursor="hand2",
            command=self.close_search_bar)
        self._search_close_btn.pack(side="right", padx=4)
        # Don't pack _search_frame yet (hidden until Ctrl+F)

        self.output_box = scrolledtext.ScrolledText(
            bot_pane, wrap=tk.WORD, font=self._mono,
            relief="flat", borderwidth=0, padx=10, pady=8, state="disabled",
        )
        self.output_box.pack(fill="both", expand=True)

        # ── Status bar ──
        self._statusbar = tk.Frame(self, height=26)
        self._statusbar.pack(fill="x", side="bottom")
        self._statusbar.pack_propagate(False)

        self._sb_index = tk.Label(self._statusbar, text="", font=self._small, anchor="w")
        self._sb_index.pack(side="left", padx=6)

        self._sb_match = tk.Label(self._statusbar, text="", font=self._small, anchor="e")
        self._sb_match.pack(side="right", padx=8)

        self._refresh_index_stats()

        # Theme-tracked widget lists
        self._frames = [self._topbar, self._actionbar, self._statusbar, in_header,
                        out_header, top_pane, bot_pane, self._search_frame,
                        self._tab_frame, self]
        self._labels = [self._lbl_in, self._lbl_out, self._hint_in, self._hint_out,
                        self._sb_index, self._sb_match, self._status_lbl, self._tab_sep,
                        self._search_count_lbl]
        self._small_buttons = [self._search_prev_btn, self._search_next_btn,
                               self._search_close_btn]

        # ttk style handle (for Combobox + PanedWindow)
        self._ttk_style = ttk.Style()
        try:
            self._ttk_style.theme_use("clam")   # 'clam' respects our colors best
        except tk.TclError:
            pass

    # ── Theme ─────────────────────────────────────────────────────────────────
    def _apply_theme(self):
        t = THEMES[self._theme]
        for w in self._frames:
            w.configure(bg=t["bg"])
        for w in self._labels:
            w.configure(bg=t["bg"], fg=t["fg_muted"])
        self._lbl_in.configure(fg=t["fg"])
        self._lbl_out.configure(fg=t["fg"])
        self._status_lbl.configure(fg=t["success"])

        # Big text boxes (+ their scrollbars)
        self.input_box.configure(bg=t["surface"], fg=t["fg"], insertbackground=t["insert"])
        self.output_box.configure(bg=t["output_bg"], fg=t["fg"])
        self._theme_scrollbar(self.input_box.vbar, t)
        self._theme_scrollbar(self.output_box.vbar, t)

        self._translate_btn.configure(
            bg=t["accent"], fg=t["accent_fg"],
            activebackground=t["accent"], activeforeground=t["accent_fg"])
        for btn in (self._clear_btn, self._copy_btn, self._excl_btn, self._save_btn,
                    self._open_btn, self._reload_btn, self._help_btn, self._history_btn,
                    self._filter_btn, self._umap_btn, self._layout_btn,
                    *self._small_buttons):
            btn.configure(bg=t["muted_bg"], fg=t["muted_fg"],
                activebackground=t["muted_bg"], activeforeground=t["muted_fg"])
        self._theme_btn.configure(
            text="☀  Light" if self._theme == "dark" else "🌙  Dark",
            bg=t["muted_bg"], fg=t["muted_fg"],
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"])

        # Search entry
        self._search_entry.configure(bg=t["surface"], fg=t["fg"], insertbackground=t["insert"])

        # Uppercase checkbox (Design Doc mode)
        try:
            self._upper_chk.configure(
                bg=t["bg"], fg=t["fg"],
                activebackground=t["bg"], activeforeground=t["fg"],
                selectcolor=t["surface"],
            )
        except Exception:
            pass
        # Sections popup-opener button
        try:
            self._sections_mb.configure(
                bg=t["muted_bg"], fg=t["muted_fg"],
                activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            )
        except Exception:
            pass

        # ttk widgets (Combobox)
        self._apply_ttk_theme(t)

        # PanedWindow sash color
        try:
            self._paned.configure(bg=t["muted_bg"])
        except Exception:
            pass

        bold_mono = font.Font(family="Consolas", size=self._font_size, weight="bold")
        self.output_box.tag_configure("header",    foreground=t["tag_header"], font=bold_mono)
        self.output_box.tag_configure("physical",  foreground=t["tag_phys"])
        self.output_box.tag_configure("logical",   foreground=t["tag_logical"])
        self.output_box.tag_configure("meta",      foreground=t["tag_meta"])
        self.output_box.tag_configure("inline_table",  foreground=t["tag_table"],  underline=True)
        self.output_box.tag_configure("inline_column", foreground=t["tag_column"], underline=True)
        self.output_box.tag_configure("inline_ambig",  foreground=t["tag_ambig"],  underline=True)
        self.output_box.tag_configure("unknown",   foreground=t["warning"])
        self.output_box.tag_configure("search_match", background=t["tag_search"], foreground=t["accent_fg"])
        self.output_box.tag_configure("placeholder", foreground=t["placeholder"])

        self.input_box.tag_configure("input_known", foreground=t["tag_input_hi"])
        self.input_box.tag_configure("placeholder", foreground=t["placeholder"])

        self._refresh_mode_tabs()

    def _theme_scrollbar(self, sb, t):
        """Color a tk.Scrollbar to match the theme (best effort on Windows)."""
        try:
            sb.configure(
                bg=t["muted_bg"],
                troughcolor=t["bg"],
                activebackground=t["accent"],
                highlightthickness=0,
                borderwidth=0,
                elementborderwidth=0,
            )
        except tk.TclError:
            pass

    def _apply_ttk_theme(self, t):
        """Style the ttk widgets (Combobox, PanedWindow) to match the theme."""
        style = self._ttk_style
        # Combobox — needs field, text, arrow, dropdown-list
        style.configure("TCombobox",
            fieldbackground=t["surface"],
            background=t["muted_bg"],
            foreground=t["fg"],
            arrowcolor=t["fg"],
            bordercolor=t["muted_bg"],
            lightcolor=t["muted_bg"],
            darkcolor=t["muted_bg"],
            selectbackground=t["accent"],
            selectforeground=t["accent_fg"],
        )
        style.map("TCombobox",
            fieldbackground=[("readonly", t["surface"])],
            foreground=[("readonly", t["fg"])],
            selectbackground=[("readonly", t["surface"])],
            selectforeground=[("readonly", t["fg"])],
        )
        # Dropdown list (a separate top-level widget in Tk)
        self.option_add("*TCombobox*Listbox.background",       t["surface"])
        self.option_add("*TCombobox*Listbox.foreground",       t["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", t["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", t["accent_fg"])

        # PanedWindow sash
        style.configure("TPanedwindow", background=t["bg"])
        style.configure("Sash", background=t["muted_bg"], sashthickness=6)

        # Treeview (for user-map dialog)
        style.configure("Treeview",
            background=t["surface"], fieldbackground=t["surface"],
            foreground=t["fg"], bordercolor=t["muted_bg"],
            lightcolor=t["muted_bg"], darkcolor=t["muted_bg"],
            borderwidth=0, rowheight=24,
        )
        style.configure("Treeview.Heading",
            background=t["muted_bg"], foreground=t["fg"],
            relief="flat", borderwidth=0,
        )
        style.map("Treeview",
            background=[("selected", t["accent"])],
            foreground=[("selected", t["accent_fg"])],
        )
        style.map("Treeview.Heading",
            background=[("active", t["muted_bg"])],
        )

        # Notebook (for user-map dialog tabs)
        style.configure("TNotebook",
            background=t["bg"], borderwidth=0, tabmargins=(0, 4, 0, 0),
        )
        style.configure("TNotebook.Tab",
            background=t["muted_bg"], foreground=t["fg_muted"],
            padding=[14, 5], borderwidth=0,
        )
        style.map("TNotebook.Tab",
            background=[("selected", t["accent"]), ("active", t["muted_bg"])],
            foreground=[("selected", t["accent_fg"]), ("active", t["fg"])],
        )

    def toggle_theme(self):
        self._theme = "light" if self._theme == "dark" else "dark"
        self._apply_theme()
        self._toast.show(f"{self._theme.title()} theme", 1000, "info")

    # ── Pane orientation ──────────────────────────────────────────────────────
    def _top_minsize(self):
        return 220 if self._pane_orient == "vertical" else 380

    def _bot_minsize(self):
        return 180 if self._pane_orient == "vertical" else 320

    def _refresh_layout_btn(self):
        if self._pane_orient == "vertical":
            self._layout_btn.configure(text="⬍  Vertical")
        else:
            self._layout_btn.configure(text="⬌  Horizontal")

    def toggle_pane_orient(self):
        self._pane_orient = "horizontal" if self._pane_orient == "vertical" else "vertical"
        self._rebuild_panes()
        self._refresh_layout_btn()
        self._apply_theme()
        self._toast.show(f"Layout: {self._pane_orient}", 900, "info")

    def _rebuild_panes(self):
        """Recreate the PanedWindow with the new orientation.
        The child panes are parented to `self`, so destroying the old
        PanedWindow leaves them intact; we just re-add them to the new one."""
        # Detach from old paned first so they don't get destroyed with it
        try:
            self._paned.forget(self._top_pane)
            self._paned.forget(self._bot_pane)
        except Exception:
            pass
        self._paned.pack_forget()
        self._paned.destroy()

        self._paned = tk.PanedWindow(
            self, orient=self._pane_orient,
            sashwidth=6, sashrelief="flat", bd=0, showhandle=False,
            opaqueresize=True,
        )
        self._paned.pack(fill="both", expand=True, padx=12, pady=(2, 0))
        self._paned.add(self._top_pane, minsize=self._top_minsize(), stretch="always")
        self._paned.add(self._bot_pane, minsize=self._bot_minsize(), stretch="always")
        # Stacking fix: the new PanedWindow was created last so it sits on top
        # of the (older) child panes. Raise the panes so they render above
        # the PW's background.
        self._top_pane.lift()
        self._bot_pane.lift()

    # ── Mode / direction / schema ─────────────────────────────────────────────
    def _set_mode(self, mode):
        self._mode.set(mode)
        self._refresh_mode_tabs()
        self.on_translate()

    def _set_direction(self, direction):
        self._direction.set(direction)
        self._refresh_mode_tabs()
        self._set_direction_label()
        self.on_translate()
        self._schedule_input_highlight()

    def _set_direction_label(self):
        if self._direction.get() == "forward":
            self._lbl_in.configure(text="Paste content here  (Physical → Logical)")
        else:
            self._lbl_in.configure(text="Paste content here  (Logical → Physical)")

    def toggle_mode(self):
        self._set_mode("table" if self._mode.get() == "inline" else "inline")

    def toggle_direction(self):
        self._set_direction("reverse" if self._direction.get() == "forward" else "forward")

    def _refresh_mode_tabs(self):
        t = THEMES[self._theme]
        def style(btn, active):
            if active:
                btn.configure(bg=t["accent"], fg=t["accent_fg"],
                    activebackground=t["accent"], activeforeground=t["accent_fg"])
            else:
                btn.configure(bg=t["muted_bg"], fg=t["fg_muted"],
                    activebackground=t["muted_bg"], activeforeground=t["fg_muted"])
        mode = self._mode.get()
        style(self._tab_table,     mode == "table")
        style(self._tab_inline,    mode == "inline")
        style(self._tab_designdoc, mode == "designdoc")
        style(self._tab_forward, self._direction.get() == "forward")
        style(self._tab_reverse, self._direction.get() == "reverse")
        # Toggle Design-Doc-specific controls
        try:
            if mode == "designdoc":
                self._upper_chk.pack(side="left", padx=(10, 0))
                self._sections_mb.pack(side="left", padx=(6, 0))
            else:
                self._upper_chk.pack_forget()
                self._sections_mb.pack_forget()
        except AttributeError:
            pass


    # ── Font zoom ─────────────────────────────────────────────────────────────
    def zoom_in(self):   self._set_font_size(self._font_size + 1)
    def zoom_out(self):  self._set_font_size(max(7, self._font_size - 1))
    def zoom_reset(self): self._set_font_size(10)

    def _set_font_size(self, size):
        self._font_size = max(6, min(size, 28))
        self._mono.configure(size=self._font_size)
        self._apply_theme()  # rebuild bold_mono with new size
        self._toast.show(f"Font size: {self._font_size}", 900, "info")

    # ── Translate ─────────────────────────────────────────────────────────────
    def _on_ctrl_enter(self, event=None):
        self.on_translate()
        # Explicit translate → save current input to history
        self._add_history(self._current_input())
        return "break"

    def _on_paste(self, event=None):
        # Save current input (before paste) into history, then schedule retranslate
        # after paste has actually modified the buffer.
        self.after(20, lambda: self._add_history(self._current_input()))
        self._schedule_autotranslate(80)

    def _schedule_autotranslate(self, delay_ms=300):
        if self._autotr_job:
            try: self.after_cancel(self._autotr_job)
            except Exception: pass
        self._autotr_job = self.after(delay_ms, self.on_translate)

    def on_translate(self):
        text = self._current_input()
        mode = self._mode.get()
        direction = self._direction.get()
        schemas = self._filter_schemas or None
        tables  = self._filter_tables  or None

        self._spans = []
        unknown = []

        # Skip translation entirely when input is empty / only placeholder
        if not text.strip():
            self._write_output("")
            self._status_var.set("")
            self._sb_match.configure(text="")
            self._table_context = set()
            return

        # Compute which tables are mentioned in the input — used to
        # prioritize column entries whose table is in the pasted text.
        if direction == "forward":
            self._table_context = {t for t in _tokens(text) if t in self.table_index}
        else:
            self._table_context = {n for n in self.rev_table_index if n and n in text}

        # ── Design Doc mode has its own pipeline ──
        if mode == "designdoc":
            uppercase = bool(self._uppercase_var.get())
            result = java_to_design_doc(
                text,
                self.table_index, self.column_index,
                self.rev_table_index, self.rev_column_index,
                schemas=schemas, tables=tables,
                uppercase=uppercase, direction=direction,
                show_overview    = bool(self._show_overview.get()),
                show_sql_logical = bool(self._show_sql_logical.get()),
                show_sql_physical= bool(self._show_sql_physical.get()),
                show_stype       = bool(self._show_stype.get()),
                show_target      = bool(self._show_target.get()),
                show_projection  = bool(self._show_projection.get()),
                show_from        = bool(self._show_from.get()),
                show_join        = bool(self._show_join.get()),
                show_where       = bool(self._show_where.get()),
                show_group       = bool(self._show_group.get()),
                show_having      = bool(self._show_having.get()),
                show_order       = bool(self._show_order.get()),
                show_footer      = bool(self._show_footer.get()),
            )
            # Compute hoverable spans over the rendered text so tooltips work
            # in this mode exactly like Inline Replace mode does.
            self._spans = self._compute_design_spans(result, direction)
            self._render_inline(result, unknown=None)
            self._status_var.set(f"Design Doc generated · hover names for context ({len(self._spans)} spans)")
            self._sb_match.configure(text=f"Design Doc · {len(self._spans)} spans  ")
            return

        ctx = self._table_context
        if direction == "forward":
            if mode == "table":
                result = translate_table_mode(text, self.table_index, self.column_index,
                                              schemas=schemas, tables=tables, table_context=ctx)
                unknown = find_unknown_tokens(text, self.table_index, self.column_index, self._exclusions)
                self._render_table(result, unknown)
            else:
                translated, rmap, spans = translate_inline_mode(
                    text, self.table_index, self.column_index, self._exclusions,
                    schemas=schemas, tables=tables, table_context=ctx)
                unknown = find_unknown_tokens(text, self.table_index, self.column_index, self._exclusions)
                self._spans = spans
                self._render_inline(translated, unknown)
            tokens = _tokens(text)
            n_t = sum(1 for t in tokens if t in self.table_index)
            n_c = sum(1 for t in tokens if t in self.column_index)
        else:
            if mode == "table":
                result = translate_reverse_table_mode(text, self.rev_table_index, self.rev_column_index,
                                                      schemas=schemas, tables=tables, table_context=ctx)
                self._render_table(result, [])
            else:
                translated, rmap, spans = translate_reverse_inline_mode(
                    text, self.rev_table_index, self.rev_column_index, self._exclusions,
                    schemas=schemas, tables=tables, table_context=ctx)
                self._spans = spans
                self._render_inline(translated, [])
            found = _find_logical_tokens(text, self.rev_table_index, self.rev_column_index)
            n_t = sum(1 for _, is_t in found if is_t)
            n_c = sum(1 for _, is_t in found if not is_t)

        n_amb = sum(1 for s in self._spans if s[4])
        extra = []
        if n_amb:        extra.append(f"Ambiguous: {n_amb}")
        if unknown:      extra.append(f"Unknown: {len(unknown)}")
        extra_txt = "  ·  " + "  ·  ".join(extra) if extra else ""

        self._status_var.set(f"Tables: {n_t}  ·  Columns: {n_c}{extra_txt}")
        self._sb_match.configure(text=f"T:{n_t} · C:{n_c}{extra_txt}  ")

    # ── Clear / copy / history ────────────────────────────────────────────────
    def on_clear(self):
        self.input_box.delete("1.0", tk.END)
        self._write_output("")
        self._status_var.set("")
        self._sb_match.configure(text="")
        self._show_placeholder_if_empty()

    def on_copy(self):
        content = self._get_output_without_unknown()
        if not content.strip():
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self._toast.show("✔  Copied to clipboard", 1400, "success")
        if self._copy_job:
            try: self.after_cancel(self._copy_job)
            except Exception: pass
        self._copy_btn.configure(text="✔  Copied!")
        self._copy_job = self.after(1600, lambda: self._copy_btn.configure(text="⎘  Copy"))

    def _add_history(self, text):
        text = text.strip()
        if not text:
            return
        if self._history and self._history[-1] == text:
            return
        self._history = [h for h in self._history if h != text]
        self._history.append(text)
        self._history = self._history[-MAX_HISTORY:]
        save_history(self._history)
        self._refresh_history_menu()

    def _refresh_history_menu(self):
        self._history_menu.delete(0, tk.END)
        if not self._history:
            self._history_menu.add_command(label="(empty)", state="disabled")
            return
        for item in reversed(self._history):
            preview = item.replace("\n", " ⏎ ")
            if len(preview) > 60:
                preview = preview[:57] + "…"
            self._history_menu.add_command(
                label=preview, command=lambda v=item: self._load_from_history(v))
        self._history_menu.add_separator()
        self._history_menu.add_command(label="Clear history", command=self._clear_history)

    def _load_from_history(self, text):
        self._clear_placeholder()
        self.input_box.delete("1.0", tk.END)
        self.input_box.insert("1.0", text)
        self.on_translate()

    def _clear_history(self):
        self._history = []
        save_history(self._history)
        self._refresh_history_menu()

    # ── Open / reload / export ────────────────────────────────────────────────
    def on_open_file(self):
        path = filedialog.askopenfilename(
            title="Open file",
            filetypes=[("Text / SQL", "*.txt *.sql *.md"), ("All files", "*.*")],
        )
        if path:
            self._load_file_into_input(path)

    def _load_file_into_input(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="cp932") as f:
                content = f.read()
        except Exception as e:
            self._toast.show(f"Open failed: {e}", 2200, "error")
            return
        self._clear_placeholder()
        self.input_box.delete("1.0", tk.END)
        self.input_box.insert("1.0", content)
        self.on_translate()
        self._add_history(content)
        self._toast.show(f"Loaded {os.path.basename(path)}", 1500, "success")

    def on_reload_json(self):
        try:
            self._load_data()
            # Drop any filter entries that no longer exist
            self._filter_schemas &= set(self.schemas)
            self._filter_tables  &= set(self.table_index.keys())
            self._refresh_filter_btn()
            self._refresh_umap_btn()
            self._refresh_index_stats()
            self.on_translate()
            self._toast.show("✔  Reloaded JSON + user map", 1200, "success")
        except Exception as e:
            self._toast.show(f"Reload failed: {e}", 2500, "error")

    def on_export(self):
        content = self._get_output_without_unknown()
        if not content.strip():
            self._toast.show("Nothing to save", 1200, "error")
            return
        path = filedialog.asksaveasfilename(
            title="Save translation",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("Markdown", "*.md"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._toast.show(f"✔  Saved {os.path.basename(path)}", 1600, "success")
        except Exception as e:
            self._toast.show(f"Save failed: {e}", 2500, "error")

    def _on_file_drop(self, event):
        # event.data has space-separated paths wrapped in braces if they have spaces
        raw = event.data.strip()
        # tkinterdnd2 wraps paths with spaces in {braces}; parse them
        paths = re.findall(r"\{([^}]+)\}|(\S+)", raw)
        paths = [a or b for a, b in paths]
        if paths:
            self._load_file_into_input(paths[0])

    # ── Input change handler (autotranslate + highlight) ──────────────────────
    def _on_input_change(self, event=None):
        self._schedule_autotranslate(350)
        self._schedule_input_highlight()

    def _schedule_input_highlight(self, delay_ms=300):
        if self._input_hi_job:
            try: self.after_cancel(self._input_hi_job)
            except Exception: pass
        self._input_hi_job = self.after(delay_ms, self._highlight_input_tokens)

    def _highlight_input_tokens(self):
        self.input_box.tag_remove("input_known", "1.0", tk.END)
        text = self._current_input()
        if not text:
            return
        direction = self._direction.get()
        if direction == "forward":
            tokens = {t for t in _tokens(text)
                      if t in self.table_index or t in self.column_index}
            if not tokens:
                return
            pattern = re.compile(r"\b(" + "|".join(re.escape(t) for t in tokens) + r")\b")
        else:
            cands = set(self.rev_table_index.keys()) | set(self.rev_column_index.keys())
            found = {c for c in cands if c and c in text}
            if not found:
                return
            pattern = re.compile("|".join(re.escape(c) for c in sorted(found, key=len, reverse=True)))

        for m in pattern.finditer(text):
            start = f"1.0+{m.start()}c"
            end   = f"1.0+{m.end()}c"
            self.input_box.tag_add("input_known", start, end)

    # ── Placeholder ───────────────────────────────────────────────────────────
    _PLACEHOLDER_TEXT = (
        "   Paste SQL, design docs, or any text containing physical (or logical) names.\n"
        "   Ctrl+Enter translates immediately; right-click a word to manage exclusions.\n"
        "   Drag a .sql / .txt / .md file here to load it, or press F1 for shortcuts."
    )

    def _current_input(self):
        """Return input text minus placeholder."""
        text = self.input_box.get("1.0", tk.END)
        if self._is_placeholder_showing():
            return ""
        return text

    def _is_placeholder_showing(self):
        return "placeholder" in self.input_box.tag_names("1.0")

    def _show_placeholder_if_empty(self):
        content = self.input_box.get("1.0", tk.END).strip()
        if not content:
            self.input_box.delete("1.0", tk.END)
            self.input_box.insert("1.0", self._PLACEHOLDER_TEXT, "placeholder")

    def _clear_placeholder(self):
        if self._is_placeholder_showing():
            self.input_box.delete("1.0", tk.END)

    # ── Rendering ─────────────────────────────────────────────────────────────
    def _write_output(self, text=""):
        self.output_box.configure(state="normal")
        self.output_box.delete("1.0", tk.END)
        if text:
            self.output_box.insert(tk.END, text)
        self.output_box.configure(state="disabled")

    def _get_output_without_unknown(self):
        """Return the output contents minus the 未定義 section."""
        ranges = self.output_box.tag_ranges("unknown_section")
        if ranges:
            return self.output_box.get("1.0", ranges[0]).rstrip("\n")
        return self.output_box.get("1.0", tk.END).rstrip("\n")

    def _render_table(self, text, unknown=None):
        self.output_box.configure(state="normal")
        self.output_box.delete("1.0", tk.END)
        for line in text.splitlines(keepends=True):
            if line.startswith("━"):
                self.output_box.insert(tk.END, line, "header")
            elif line.startswith("  ") and "→" not in line and line.strip():
                self.output_box.insert(tk.END, line, "physical")
            elif "→" in line:
                idx = line.index("→")
                self.output_box.insert(tk.END, line[:idx + 1], "logical")
                rest = line[idx + 1:]
                b = rest.find("[")
                if b != -1:
                    self.output_box.insert(tk.END, rest[:b], "logical")
                    self.output_box.insert(tk.END, rest[b:], "meta")
                else:
                    self.output_box.insert(tk.END, rest, "logical")
            else:
                self.output_box.insert(tk.END, line)

        self._append_unknown_section(unknown)
        self.output_box.configure(state="disabled")

    def _render_inline(self, translated_text, unknown=None):
        self.output_box.configure(state="normal")
        self.output_box.delete("1.0", tk.END)

        # Clear previous span tags
        for tag in list(self.output_box.tag_names()):
            if tag.startswith("span_"):
                self.output_box.tag_delete(tag)

        spans = self._spans
        if spans:
            pos = 0
            for i, (s, e, _orig, kind, is_amb) in enumerate(spans):
                if s > pos:
                    self.output_box.insert(tk.END, translated_text[pos:s])
                base_tag = "inline_ambig" if is_amb else ("inline_table" if kind == "table" else "inline_column")
                span_tag = f"span_{i}"
                self.output_box.insert(tk.END, translated_text[s:e], (base_tag, span_tag))
                self.output_box.tag_bind(span_tag, "<Enter>",
                    lambda ev, idx=i: self._show_span_tooltip(idx, ev))
                pos = e
            if pos < len(translated_text):
                self.output_box.insert(tk.END, translated_text[pos:])
        else:
            self.output_box.insert(tk.END, translated_text)

        self._append_unknown_section(unknown)
        self.output_box.configure(state="disabled")

    def _append_unknown_section(self, unknown):
        if not unknown:
            return
        current = self.output_box.get("1.0", tk.END)
        if not current.endswith("\n\n") and current.strip():
            self.output_box.insert(tk.END, "\n\n", "unknown_section")
        self.output_box.insert(tk.END, "━━━  未定義 (Not in JSON)  ━━━\n",
                               ("header", "unknown_section"))
        for token in unknown:
            self.output_box.insert(tk.END, f"  • {token}\n",
                                   ("unknown", "unknown_section"))

    # ── Hover tooltip ─────────────────────────────────────────────────────────
    def _show_span_tooltip(self, span_idx, event):
        if span_idx >= len(self._spans):
            return
        _, _, original, kind, is_amb = self._spans[span_idx]
        direction = self._direction.get()
        ctx = self._table_context

        # Build the tooltip based on whichever index holds the name.
        # Direction-specific lookup is tried first; falls back to the other
        # side so Design-Doc spans (which may carry either physical or logical
        # names) always produce useful context.
        def _from_fwd_table(original):
            entries = self.table_index[original]
            return (f"Table: {original}",
                    [f"  → {lg}  [{sc}]" for sc, lg in entries])

        def _from_fwd_col(original):
            entries = _filter_by_table_context(self.column_index[original], ctx)
            return (f"Column: {original}",
                    [f"  → {lc}  [{lt} ({pt}) / {sc}]" for sc, pt, lt, lc in entries])

        def _from_rev_table(original):
            entries = self.rev_table_index[original]
            return (f"Table: {original}",
                    [f"  → {ph}  [{sc}]" for sc, ph in entries])

        def _from_rev_col(original):
            entries = _filter_by_table_context(self.rev_column_index[original], ctx)
            return (f"Column: {original}",
                    [f"  → {pc}  [{lt} ({pt}) / {sc}]" for sc, pt, lt, pc in entries])

        header, body = None, None
        preferred = (
            [_from_fwd_table, _from_fwd_col, _from_rev_table, _from_rev_col]
            if direction == "forward"
            else [_from_rev_table, _from_rev_col, _from_fwd_table, _from_fwd_col]
        )
        fwd_tests = [
            (_from_fwd_table, lambda o: o in self.table_index),
            (_from_fwd_col,   lambda o: o in self.column_index),
            (_from_rev_table, lambda o: o in self.rev_table_index),
            (_from_rev_col,   lambda o: o in self.rev_column_index),
        ]
        order = preferred
        tests = {fn: pred for fn, pred in fwd_tests}
        for fn in order:
            if tests[fn](original):
                header, body = fn(original)
                break
        if header is None:
            return

        prefix = "⚠ Ambiguous\n" if is_amb else ""
        self._tooltip.show(prefix + header + "\n" + "\n".join(body),
                           event.x_root, event.y_root)

    def _on_output_motion(self, event):
        idx = self.output_box.index(f"@{event.x},{event.y}")
        tags = self.output_box.tag_names(idx)
        if not any(tg.startswith("span_") for tg in tags):
            self._tooltip.hide()

    def _compute_design_spans(self, text, direction):
        """Scan the rendered design-doc text and return spans for every known
        physical or logical name occurrence so hover tooltips work in this mode.
        Skips any match that overlaps an entry in the exclusion list."""
        spans = []
        # Exclusion ranges computed over the OUTPUT text so e.g. '■処理区分'
        # stays silent on hover while '処理区分' elsewhere still works.
        excl_ranges = _exclusion_ranges(text, self._exclusions)
        # Collect candidate names present anywhere in the text
        # Forward mode emits logical (Japanese) names; reverse keeps physical.
        # Accept both just in case the user mixed modes.
        logical_cands  = [n for n in (set(self.rev_table_index) | set(self.rev_column_index))
                          if n and n in text]
        physical_cands = [n for n in (set(self.table_index) | set(self.column_index))
                          if n and n in text]

        # Sort longer-first so e.g. '商品マスタ' wins over '商品'
        logical_cands.sort(key=len, reverse=True)
        physical_cands.sort(key=len, reverse=True)

        patterns = []
        if logical_cands:
            patterns.append(("logical", re.compile(
                "(" + "|".join(re.escape(c) for c in logical_cands) + ")")))
        if physical_cands:
            patterns.append(("physical", re.compile(
                r"\b(" + "|".join(re.escape(c) for c in physical_cands) + r")\b")))

        if not patterns:
            return spans

        # Collect matches from each pattern, sort by position, de-overlap
        raw = []
        for kind_tag, pat in patterns:
            for m in pat.finditer(text):
                name = m.group(0)
                raw.append((m.start(), m.end(), name, kind_tag))
        # Sort by (start, -end): longer match at same position wins
        raw.sort(key=lambda x: (x[0], -x[1]))

        last_end = -1
        for s, e, name, kind_tag in raw:
            if s < last_end:
                continue
            if _overlaps_any(s, e, excl_ranges):
                continue
            if kind_tag == "logical":
                if name in self.rev_table_index:
                    kind = "table"
                    entries = self.rev_table_index[name]
                else:
                    kind = "column"
                    entries = self.rev_column_index[name]
            else:  # physical
                if name in self.table_index:
                    kind = "table"
                    entries = self.table_index[name]
                else:
                    kind = "column"
                    entries = self.column_index[name]
            is_amb = _is_ambiguous(name, entries)
            spans.append((s, e, name, kind, is_amb))
            last_end = e

        spans.sort()
        return spans

    # ── Exclusions: right-click ───────────────────────────────────────────────
    def _on_right_click(self, event, widget):
        try:
            selected = widget.selection_get()
        except tk.TclError:
            return
        selected = selected.strip("\r\n")
        if not selected.strip():
            return

        t = THEMES[self._theme]
        menu = tk.Menu(self, tearoff=0,
            bg=t["surface"], fg=t["fg"],
            activebackground=t["accent"], activeforeground=t["accent_fg"],
            bd=0, relief="flat")
        preview = selected if len(selected) <= 40 else selected[:37] + "…"
        preview = preview.replace("\n", "⏎")

        if selected in self._exclusions:
            menu.add_command(label=f"✕  Remove from exclusions:  «{preview}»",
                command=lambda: self._remove_exclusion(selected))
        else:
            menu.add_command(label=f"⊘  Add to exclusions:  «{preview}»",
                command=lambda: self._add_exclusion(selected))
        menu.add_separator()
        menu.add_command(label="Open exclusion list…", command=self.open_exclusions_dialog)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _add_exclusion(self, text):
        text = text.strip("\r\n")
        if text and text not in self._exclusions:
            self._exclusions.append(text)
            save_exclusions(self._exclusions)
            self._refresh_excl_btn()
            self.on_translate()
            self._toast.show("Added to exclusions", 1200, "success")

    def _remove_exclusion(self, text):
        text = text.strip("\r\n")
        if text in self._exclusions:
            self._exclusions.remove(text)
            save_exclusions(self._exclusions)
            self._refresh_excl_btn()
            self.on_translate()
            self._toast.show("Removed from exclusions", 1200, "success")

    def _refresh_excl_btn(self):
        n = len(self._exclusions)
        self._excl_btn.configure(text=f"⊘  Exclusions ({n})" if n else "⊘  Exclusions")

    # ── User map button + dialog ──────────────────────────────────────────────
    def _refresh_umap_btn(self):
        n = len((self._user_map.get("tables") or {})) + len((self._user_map.get("columns") or {}))
        self._umap_btn.configure(text=f"🖉  User Map ({n})" if n else "🖉  User Map")

    def open_user_map_dialog(self):
        """Table-based editor for the user override map."""
        t = THEMES[self._theme]
        dlg = tk.Toplevel(self)
        dlg.title("User-Defined Overrides")
        dlg.geometry("760x600")
        dlg.minsize(620, 460)
        dlg.configure(bg=t["bg"])
        dlg.transient(self); dlg.grab_set()

        tk.Label(
            dlg,
            text=(
                "Custom physical ↔ logical mappings. These always win over "
                "db_schema_output.json and bypass schema/table filters."
            ),
            font=self._ui, bg=t["bg"], fg=t["fg_muted"],
            anchor="w", justify="left", wraplength=720,
        ).pack(fill="x", padx=14, pady=(12, 6))

        # ── Footer buttons (packed first so they reserve space) ──
        btns = tk.Frame(dlg, bg=t["bg"])
        btns.pack(side="bottom", fill="x", padx=14, pady=(0, 12))

        # File info line (bottom, below buttons)
        file_lbl = tk.Label(dlg,
            text=f"File: {USER_MAP_FILE}",
            font=self._small, bg=t["bg"], fg=t["fg_muted"], anchor="w",
        )
        file_lbl.pack(side="bottom", fill="x", padx=14, pady=(0, 4))

        # ── Notebook with two tabs ──
        nb = ttk.Notebook(dlg)
        nb.pack(fill="both", expand=True, padx=14, pady=(4, 8))

        tbl_tab, tbl_tree = self._build_user_map_tab(
            nb, self._user_map.get("tables", {}), t)
        col_tab, col_tree = self._build_user_map_tab(
            nb, self._user_map.get("columns", {}), t)
        nb.add(tbl_tab, text="Tables")
        nb.add(col_tab, text="Columns")

        def _collect(tree):
            out = {}
            for iid in tree.get_children():
                p, l = tree.item(iid, "values")
                p, l = str(p).strip(), str(l).strip()
                if p and l:
                    out[p] = l
            return out

        def _save():
            new = {
                "tables":  _collect(tbl_tree),
                "columns": _collect(col_tree),
            }
            save_user_map(new)
            self._user_map = new
            self._load_data()
            self._refresh_umap_btn()
            self._refresh_index_stats()
            self.on_translate()
            dlg.destroy()
            total = len(new["tables"]) + len(new["columns"])
            self._toast.show(f"Saved {total} override(s)", 1500, "success")

        def _open_externally():
            if not os.path.exists(USER_MAP_FILE):
                save_user_map({"tables": {}, "columns": {}})
            try:
                os.startfile(USER_MAP_FILE)
            except Exception as e:
                self._toast.show(f"Open failed: {e}", 2500, "error")

        tk.Button(btns, text="Save", font=self._btn, relief="flat", bd=0,
            bg=t["accent"], fg=t["accent_fg"], padx=18, pady=6, cursor="hand2",
            activebackground=t["accent"], activeforeground=t["accent_fg"],
            command=_save).pack(side="right")
        tk.Button(btns, text="Cancel", font=self._btn, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"], padx=14, pady=6, cursor="hand2",
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            command=dlg.destroy).pack(side="right", padx=(0, 6))
        tk.Button(btns, text="📂  Open JSON file", font=self._btn, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"], padx=12, pady=6, cursor="hand2",
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            command=_open_externally).pack(side="left")
        tk.Button(btns, text="⚠  Inconsistencies…", font=self._btn, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"], padx=12, pady=6, cursor="hand2",
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            command=lambda: (dlg.destroy(), self.open_inconsistency_dialog())).pack(side="left", padx=(6, 0))

    def _build_user_map_tab(self, parent, data, t):
        """Build one tab (Tables or Columns). Returns (frame, treeview)."""
        frame = tk.Frame(parent, bg=t["bg"])

        # ── Treeview + scrollbar ──
        tree_wrap = tk.Frame(frame, bg=t["bg"])
        tree_wrap.pack(side="top", fill="both", expand=True, padx=4, pady=(8, 4))

        tree = ttk.Treeview(
            tree_wrap, columns=("phys", "logical"),
            show="headings", selectmode="browse",
        )
        tree.heading("phys",    text="Physical name",
            command=lambda: self._sort_tree(tree, "phys"))
        tree.heading("logical", text="Logical name",
            command=lambda: self._sort_tree(tree, "logical"))
        tree.column("phys",    width=220, anchor="w")
        tree.column("logical", width=400, anchor="w")

        vsb = tk.Scrollbar(tree_wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        self._theme_scrollbar(vsb, t)
        vsb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

        # Populate from existing data
        for phys, logical in sorted(data.items()):
            tree.insert("", "end", values=(phys, logical))

        # ── Entry row ──
        entry_bar = tk.Frame(frame, bg=t["bg"])
        entry_bar.pack(side="top", fill="x", padx=4, pady=(4, 4))

        phys_var = tk.StringVar()
        log_var  = tk.StringVar()

        def _mk_entry(var):
            e = tk.Entry(entry_bar, textvariable=var, font=self._ui,
                bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
                relief="flat", bd=0)
            return e

        tk.Label(entry_bar, text="Physical:", font=self._ui,
            bg=t["bg"], fg=t["fg"]).grid(row=0, column=0, sticky="w", padx=(0, 6))
        phys_ent = _mk_entry(phys_var)
        phys_ent.grid(row=0, column=1, sticky="ew", padx=(0, 10), ipady=4)

        tk.Label(entry_bar, text="Logical:", font=self._ui,
            bg=t["bg"], fg=t["fg"]).grid(row=0, column=2, sticky="w", padx=(0, 6))
        log_ent = _mk_entry(log_var)
        log_ent.grid(row=0, column=3, sticky="ew", ipady=4)

        entry_bar.columnconfigure(1, weight=1)
        entry_bar.columnconfigure(3, weight=2)

        # ── Action buttons ──
        btn_bar = tk.Frame(frame, bg=t["bg"])
        btn_bar.pack(side="top", fill="x", padx=4, pady=(0, 8))

        def _add_or_update():
            p = phys_var.get().strip()
            l = log_var.get().strip()
            if not p or not l:
                self._toast.show("Both fields are required", 1500, "error")
                return
            # Replace row with same physical name if present
            for iid in tree.get_children():
                if tree.item(iid, "values")[0] == p:
                    tree.item(iid, values=(p, l))
                    phys_var.set(""); log_var.set("")
                    phys_ent.focus_set()
                    return
            tree.insert("", "end", values=(p, l))
            phys_var.set(""); log_var.set("")
            phys_ent.focus_set()

        def _remove_selected():
            sel = tree.selection()
            if sel:
                tree.delete(sel[0])
            phys_var.set(""); log_var.set("")

        def _on_select(_event=None):
            sel = tree.selection()
            if sel:
                p, l = tree.item(sel[0], "values")
                phys_var.set(p)
                log_var.set(l)

        tree.bind("<<TreeviewSelect>>", _on_select)
        tree.bind("<Double-1>", lambda e: phys_ent.focus_set())
        tree.bind("<Delete>",   lambda e: _remove_selected())
        phys_ent.bind("<Return>", lambda e: log_ent.focus_set())
        log_ent.bind("<Return>",  lambda e: _add_or_update())

        tk.Button(btn_bar, text="➕  Add / Update",
            font=self._btn, relief="flat", bd=0,
            bg=t["accent"], fg=t["accent_fg"], padx=14, pady=6, cursor="hand2",
            activebackground=t["accent"], activeforeground=t["accent_fg"],
            command=_add_or_update).pack(side="left")
        tk.Button(btn_bar, text="➖  Remove",
            font=self._btn, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"], padx=14, pady=6, cursor="hand2",
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            command=_remove_selected).pack(side="left", padx=(6, 0))

        tk.Label(btn_bar,
            text="  Enter in Physical → jumps to Logical.  Enter in Logical → Add.  Del key → Remove.",
            font=self._small, bg=t["bg"], fg=t["fg_muted"]
        ).pack(side="left", padx=10)

        return frame, tree

    def _sort_tree(self, tree, col):
        """Toggle ascending/descending sort on a treeview column."""
        items = [(tree.set(k, col), k) for k in tree.get_children()]
        # Cache previous direction on the widget
        reverse = not getattr(tree, f"_sort_{col}", False)
        setattr(tree, f"_sort_{col}", reverse)
        items.sort(reverse=reverse)
        for idx, (_val, k) in enumerate(items):
            tree.move(k, "", idx)

    # ── Filter button + dialog ────────────────────────────────────────────────
    def _refresh_filter_btn(self):
        ns = len(self._filter_schemas)
        nt = len(self._filter_tables)
        parts = []
        if ns: parts.append(f"{ns}S")
        if nt: parts.append(f"{nt}T")
        label = "⚙  Filter"
        if parts:
            label += f" ({' / '.join(parts)})"
        self._filter_btn.configure(text=label)

    def open_filter_dialog(self):
        t = THEMES[self._theme]
        dlg = tk.Toplevel(self)
        dlg.title("Translation Filter")
        dlg.geometry("680x560")
        dlg.minsize(520, 420)
        dlg.configure(bg=t["bg"])
        dlg.transient(self); dlg.grab_set()

        # Header
        tk.Label(dlg,
            text="Choose which schemas and tables to include. Empty = all.",
            font=self._ui, bg=t["bg"], fg=t["fg_muted"], anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 6))

        # Main body: two columns (Schemas | Tables)
        body = tk.Frame(dlg, bg=t["bg"])
        body.pack(fill="both", expand=True, padx=14, pady=4)

        # ── Left: schemas ──
        left = tk.Frame(body, bg=t["bg"])
        left.pack(side="left", fill="both", expand=False, padx=(0, 8))
        tk.Label(left, text="Schemas", font=self._ui_b,
            bg=t["bg"], fg=t["fg"], anchor="w").pack(fill="x")

        schema_vars = {}
        schema_frame = tk.Frame(left, bg=t["surface"], padx=6, pady=6)
        schema_frame.pack(fill="both", expand=True, pady=(4, 4))
        for s in self.schemas:
            v = tk.BooleanVar(value=(s in self._filter_schemas) if self._filter_schemas else False)
            schema_vars[s] = v
            cb = tk.Checkbutton(schema_frame, text=s, variable=v,
                bg=t["surface"], fg=t["fg"], selectcolor=t["bg"],
                activebackground=t["surface"], activeforeground=t["fg"],
                font=self._ui, anchor="w", bd=0, highlightthickness=0)
            cb.pack(fill="x", anchor="w")

        sch_btns = tk.Frame(left, bg=t["bg"])
        sch_btns.pack(fill="x")
        tk.Button(sch_btns, text="All", font=self._small, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=2, cursor="hand2",
            command=lambda: [v.set(True) for v in schema_vars.values()]).pack(side="left")
        tk.Button(sch_btns, text="None", font=self._small, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=2, cursor="hand2",
            command=lambda: [v.set(False) for v in schema_vars.values()]).pack(side="left", padx=(6, 0))

        # ── Right: tables (search + scrollable checkbox list) ──
        right = tk.Frame(body, bg=t["bg"])
        right.pack(side="left", fill="both", expand=True)

        right_head = tk.Frame(right, bg=t["bg"])
        right_head.pack(fill="x")
        tk.Label(right_head, text="Tables", font=self._ui_b,
            bg=t["bg"], fg=t["fg"], anchor="w").pack(side="left")

        search_var = tk.StringVar()
        search_ent = tk.Entry(right_head, textvariable=search_var,
            font=self._ui, relief="flat", bd=0,
            bg=t["surface"], fg=t["fg"], insertbackground=t["insert"])
        search_ent.pack(side="right", fill="x", expand=True, ipady=3, padx=(8, 0))
        tk.Label(right_head, text="🔍", font=self._ui,
            bg=t["bg"], fg=t["fg_muted"]).pack(side="right", padx=(6, 2))

        # Scrollable list
        list_wrap = tk.Frame(right, bg=t["surface"])
        list_wrap.pack(fill="both", expand=True, pady=(4, 4))

        canvas = tk.Canvas(list_wrap, bg=t["surface"], highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True)
        vsb = tk.Scrollbar(list_wrap, orient="vertical", command=canvas.yview)
        vsb.pack(side="right", fill="y")
        self._theme_scrollbar(vsb, t)
        canvas.configure(yscrollcommand=vsb.set)

        inner = tk.Frame(canvas, bg=t["surface"])
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        def _on_inner_resize(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(win_id, width=canvas.winfo_width())
        inner.bind("<Configure>", _on_inner_resize)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))
        # Mouse-wheel scrolling — scoped to the canvas/inner frame so it
        # doesn't hijack the wheel globally and doesn't outlive the dialog.
        def _on_wheel(e):
            if not canvas.winfo_exists():
                return
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            return "break"
        def _bind_wheel(_=None):
            canvas.bind("<MouseWheel>", _on_wheel)
            inner.bind_all
        # Bind on canvas, inner frame, and each child — only active while pointer is over them
        for w in (canvas, inner):
            w.bind("<MouseWheel>", _on_wheel)
        # Rebind mouse-wheel on dynamically-added children
        def _bind_children_wheel():
            for child in inner.winfo_children():
                child.bind("<MouseWheel>", _on_wheel)
        self.after(50, _bind_children_wheel)

        # Build table checkboxes (with logical name hint)
        table_vars = {}
        table_labels = []   # list of (name, widget, search_text)
        for phys in sorted(self.table_index.keys()):
            entries = self.table_index[phys]
            logical = entries[0][1] if entries else ""
            schema  = entries[0][0] if entries else ""
            v = tk.BooleanVar(value=(phys in self._filter_tables) if self._filter_tables else False)
            table_vars[phys] = v
            display = f"{phys}    ({logical})  · {schema}" if logical else f"{phys}  · {schema}"
            cb = tk.Checkbutton(inner, text=display, variable=v,
                bg=t["surface"], fg=t["fg"], selectcolor=t["bg"],
                activebackground=t["surface"], activeforeground=t["fg"],
                font=self._ui, anchor="w", bd=0, highlightthickness=0)
            cb.pack(fill="x", anchor="w")
            table_labels.append((phys, cb, f"{phys} {logical} {schema}".lower()))

        def _filter_tables(*_):
            q = search_var.get().strip().lower()
            for _, cb, hay in table_labels:
                if not q or q in hay:
                    cb.pack(fill="x", anchor="w")
                else:
                    cb.pack_forget()
        search_var.trace_add("write", _filter_tables)

        tbl_btns = tk.Frame(right, bg=t["bg"])
        tbl_btns.pack(fill="x")
        def _set_all_visible(value):
            q = search_var.get().strip().lower()
            for phys, cb, hay in table_labels:
                if not q or q in hay:
                    table_vars[phys].set(value)
        tk.Button(tbl_btns, text="All (visible)", font=self._small, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=2, cursor="hand2",
            command=lambda: _set_all_visible(True)).pack(side="left")
        tk.Button(tbl_btns, text="None (visible)", font=self._small, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=2, cursor="hand2",
            command=lambda: _set_all_visible(False)).pack(side="left", padx=(6, 0))

        # ── Footer ──
        footer = tk.Frame(dlg, bg=t["bg"])
        footer.pack(fill="x", padx=14, pady=(8, 12))

        def _clear_all():
            for v in schema_vars.values(): v.set(False)
            for v in table_vars.values():  v.set(False)

        def _apply():
            self._filter_schemas = {s for s, v in schema_vars.items() if v.get()}
            self._filter_tables  = {t_ for t_, v in table_vars.items() if v.get()}
            self._refresh_filter_btn()
            dlg.destroy()
            self.on_translate()
            self._toast.show(
                f"Filter: {len(self._filter_schemas)} schemas · {len(self._filter_tables)} tables",
                1500, "success")

        tk.Button(footer, text="Apply", font=self._btn, relief="flat", bd=0,
            bg=t["accent"], fg=t["accent_fg"], padx=18, pady=6, cursor="hand2",
            activebackground=t["accent"], activeforeground=t["accent_fg"],
            command=_apply).pack(side="right")
        tk.Button(footer, text="Cancel", font=self._btn, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"], padx=14, pady=6, cursor="hand2",
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            command=dlg.destroy).pack(side="right", padx=(0, 6))
        tk.Button(footer, text="Clear all", font=self._btn, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"], padx=12, pady=6, cursor="hand2",
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            command=_clear_all).pack(side="left")

    # ── Inconsistency detector ────────────────────────────────────────────────
    def open_inconsistency_dialog(self):
        t = THEMES[self._theme]
        issues = find_column_inconsistencies(self.column_index)

        dlg = tk.Toplevel(self)
        dlg.title("Column Name Inconsistencies")
        dlg.geometry("820x580")
        dlg.minsize(640, 460)
        dlg.configure(bg=t["bg"])
        dlg.transient(self); dlg.grab_set()

        header = tk.Label(
            dlg,
            text=(
                f"Found {len(issues)} column(s) with conflicting logical names.\n"
                "Select a variant from each row and click “Apply” to add those "
                "picks to the User Map."
            ),
            font=self._ui, bg=t["bg"], fg=t["fg_muted"],
            anchor="w", justify="left", wraplength=780,
        )
        header.pack(fill="x", padx=14, pady=(12, 6))

        # Footer first so it stays visible
        footer = tk.Frame(dlg, bg=t["bg"])
        footer.pack(side="bottom", fill="x", padx=14, pady=(0, 12))

        # Tree
        tree_wrap = tk.Frame(dlg, bg=t["bg"])
        tree_wrap.pack(fill="both", expand=True, padx=14, pady=(4, 8))

        tree = ttk.Treeview(
            tree_wrap, columns=("logical", "count", "tables"),
            show="tree headings", selectmode="extended",
        )
        tree.heading("#0",      text="Physical column")
        tree.heading("logical", text="Logical name")
        tree.heading("count",   text="# tables")
        tree.heading("tables",  text="Tables")
        tree.column("#0",      width=200, stretch=False, anchor="w")
        tree.column("logical", width=230, stretch=False, anchor="w")
        tree.column("count",   width=70,  stretch=False, anchor="center")
        tree.column("tables",  width=280, stretch=True,  anchor="w")

        vsb = tk.Scrollbar(tree_wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        self._theme_scrollbar(vsb, t)
        vsb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

        # Populate — parent row = phys_col, child rows = variants
        child_meta = {}  # iid → (phys_col, logical)
        for issue in issues:
            phys = issue["phys_col"]
            total = sum(v["count"] for v in issue["variants"])
            parent_id = tree.insert(
                "", "end", text=phys,
                values=(f"{len(issue['variants'])} variants", total, ""),
                open=True,
            )
            for v in issue["variants"]:
                tbls = ", ".join(f"{lt}({pt})" for _, pt, lt in v["tables"][:4])
                if len(v["tables"]) > 4:
                    tbls += f" … +{len(v['tables']) - 4}"
                child_id = tree.insert(
                    parent_id, "end", text="",
                    values=(v["logical"], v["count"], tbls),
                )
                child_meta[child_id] = (phys, v["logical"])

        if not issues:
            tk.Label(
                tree_wrap,
                text="🎉  No inconsistencies found — every column has a single logical name.",
                bg=t["bg"], fg=t["success"], font=self._ui_b,
            ).place(relx=0.5, rely=0.5, anchor="center")

        # Apply: each selected child variant → written to user_map["columns"]
        def _apply_selected():
            picks = {}
            for iid in tree.selection():
                if iid in child_meta:
                    phys, logical = child_meta[iid]
                    # If user picked multiple variants for the same phys_col,
                    # take the last one
                    picks[phys] = logical
            if not picks:
                self._toast.show("Select at least one variant first", 1800, "error")
                return
            # Merge into user map
            cols = self._user_map.get("columns") or {}
            cols.update(picks)
            self._user_map["columns"] = cols
            save_user_map(self._user_map)
            self._load_data()
            self._refresh_umap_btn()
            self._refresh_index_stats()
            self.on_translate()
            dlg.destroy()
            self._toast.show(
                f"Added {len(picks)} override(s) to User Map",
                1600, "success",
            )

        def _export_csv():
            path = filedialog.asksaveasfilename(
                title="Save inconsistency report",
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
                parent=dlg,
            )
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8-sig") as f:
                    f.write("Physical column,Logical variant,# tables,Tables\n")
                    for issue in issues:
                        for v in issue["variants"]:
                            tbls = "; ".join(f"{lt} ({pt}) / {sc}"
                                             for sc, pt, lt in v["tables"])
                            line = f'"{issue["phys_col"]}","{v["logical"]}",{v["count"]},"{tbls}"\n'
                            f.write(line)
                self._toast.show(f"Saved {os.path.basename(path)}", 1400, "success")
            except Exception as e:
                self._toast.show(f"Save failed: {e}", 2500, "error")

        tk.Button(
            footer, text="Apply picks to User Map",
            font=self._btn, relief="flat", bd=0,
            bg=t["accent"], fg=t["accent_fg"],
            activebackground=t["accent"], activeforeground=t["accent_fg"],
            padx=16, pady=6, cursor="hand2", command=_apply_selected,
        ).pack(side="right")
        tk.Button(
            footer, text="Close",
            font=self._btn, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"],
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            padx=14, pady=6, cursor="hand2", command=dlg.destroy,
        ).pack(side="right", padx=(0, 6))
        tk.Button(
            footer, text="📄 Export CSV",
            font=self._btn, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"],
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            padx=14, pady=6, cursor="hand2", command=_export_csv,
        ).pack(side="left")

    # ── Sections popup (stays open across multiple toggles) ───────────────────
    def toggle_sections_popup(self):
        existing = self._sections_popup
        if existing and existing.winfo_exists():
            existing.destroy()
            self._sections_popup = None
            return

        t = THEMES[self._theme]
        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=t["muted_bg"])

        # Inner frame (acts as border)
        inner = tk.Frame(popup, bg=t["surface"], bd=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        def add_check(label, var):
            cb = tk.Checkbutton(
                inner, text=label, variable=var,
                bg=t["surface"], fg=t["fg"], selectcolor=t["bg"],
                activebackground=t["accent"], activeforeground=t["accent_fg"],
                anchor="w", font=self._ui,
                bd=0, highlightthickness=0, padx=10, pady=3,
                command=self.on_translate,
            )
            cb.pack(fill="x")

        def add_sep():
            tk.Frame(inner, height=1, bg=t["muted_bg"]).pack(fill="x", padx=2, pady=3)

        add_check("■処理概要",               self._show_overview)
        add_check("【SQL論理名】",            self._show_sql_logical)
        add_check("【SQL定義名】",            self._show_sql_physical)
        add_sep()
        add_check("■処理区分",               self._show_stype)
        add_check("■対象/登録/更新テーブル",   self._show_target)
        add_check("■項目移送/更新項目/抽出項目", self._show_projection)
        add_check("■抽出テーブル (FROM)",     self._show_from)
        add_check("■結合条件 (JOIN)",        self._show_join)
        add_check("■抽出条件 (WHERE)",       self._show_where)
        add_check("■グループ化条件",          self._show_group)
        add_check("■集計後抽出条件",          self._show_having)
        add_check("■並び順",                 self._show_order)
        add_sep()
        add_check("■実行後処理",             self._show_footer)

        # Position under the button
        self.update_idletasks()
        btn = self._sections_mb
        px = btn.winfo_rootx()
        py = btn.winfo_rooty() + btn.winfo_height() + 2
        popup.wm_geometry(f"+{px}+{py}")

        # Close on Escape
        popup.bind("<Escape>", lambda e: popup.destroy())

        # Close when user clicks anywhere outside the popup (or the opener button).
        def _is_descendant(widget):
            w = widget
            while w is not None:
                if w == popup:
                    return True
                try:
                    w = w.master
                except Exception:
                    break
            return False

        def _on_click(event):
            if event.widget is self._sections_mb:
                return  # the button itself handles toggle
            if not _is_descendant(event.widget):
                try:
                    popup.destroy()
                except Exception:
                    pass

        bind_id = self.bind("<Button-1>", _on_click, add="+")

        def _on_destroy(event):
            if event.widget is popup:
                try:
                    self.unbind("<Button-1>", bind_id)
                except Exception:
                    pass
                self._sections_popup = None

        popup.bind("<Destroy>", _on_destroy)
        self._sections_popup = popup

    # ── Exclusions dialog ─────────────────────────────────────────────────────
    def open_exclusions_dialog(self):
        t = THEMES[self._theme]
        dlg = tk.Toplevel(self)
        dlg.title("Exclusion List — do not translate")
        dlg.geometry("560x460")
        dlg.minsize(420, 320)
        dlg.configure(bg=t["bg"])
        dlg.transient(self); dlg.grab_set()

        header = tk.Frame(dlg, bg=t["bg"])
        header.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(header,
            text="One entry per line. Any match of a listed string is preserved as-is.",
            font=self._ui, bg=t["bg"], fg=t["fg_muted"], anchor="w", justify="left"
        ).pack(fill="x")

        btns = tk.Frame(dlg, bg=t["bg"])
        btns.pack(side="bottom", fill="x", padx=14, pady=(0, 12))

        editor = scrolledtext.ScrolledText(
            dlg, wrap=tk.NONE, font=self._mono,
            bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
            relief="flat", borderwidth=0, padx=8, pady=6,
            undo=True, autoseparators=True, maxundo=-1,
        )
        editor.pack(fill="both", expand=True, padx=14, pady=(4, 8))
        editor.insert("1.0", "\n".join(self._exclusions))
        editor.edit_reset()

        def _delete_lines():
            editor.edit_separator()
            try:
                sel_first = editor.index("sel.first")
                sel_last  = editor.index("sel.last")
                start = editor.index(f"{sel_first} linestart")
                if editor.index(f"{sel_last} linestart") == sel_last:
                    end = sel_last
                else:
                    end = editor.index(f"{sel_last} lineend +1c")
            except tk.TclError:
                cur = editor.index("insert")
                start = editor.index(f"{cur} linestart")
                end   = editor.index(f"{cur} lineend +1c")
            editor.delete(start, end)
            editor.edit_separator()
            editor.focus_set()

        def _undo():
            try: editor.edit_undo()
            except tk.TclError: pass
            editor.focus_set()

        def _redo():
            try: editor.edit_redo()
            except tk.TclError: pass
            editor.focus_set()

        editor.bind("<Control-d>", lambda e: (_delete_lines(), "break")[1])
        editor.bind("<Control-y>", lambda e: (_redo(), "break")[1])

        def _save():
            content = editor.get("1.0", tk.END)
            lines = [ln.rstrip("\r") for ln in content.split("\n")]
            self._exclusions = [ln for ln in lines if ln.strip()]
            save_exclusions(self._exclusions)
            self._refresh_excl_btn()
            dlg.destroy()
            self.on_translate()
            self._toast.show(f"Saved {len(self._exclusions)} exclusions", 1400, "success")

        tk.Button(btns, text="Save", font=self._btn, relief="flat",
            padx=18, pady=6, cursor="hand2", bd=0,
            bg=t["accent"], fg=t["accent_fg"],
            activebackground=t["accent"], activeforeground=t["accent_fg"],
            command=_save).pack(side="right")
        tk.Button(btns, text="Cancel", font=self._btn, relief="flat",
            padx=14, pady=6, cursor="hand2", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"],
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            command=dlg.destroy).pack(side="right", padx=(0, 6))

        for label, cmd in [("🗑  Delete line", _delete_lines), ("↶  Undo", _undo), ("↷  Redo", _redo)]:
            tk.Button(btns, text=label, font=self._btn, relief="flat",
                padx=12, pady=6, cursor="hand2", bd=0,
                bg=t["muted_bg"], fg=t["muted_fg"],
                activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
                command=cmd).pack(side="left", padx=(0, 6))

        tk.Label(dlg, text="Ctrl+D delete line · Ctrl+Z undo · Ctrl+Y redo",
            font=self._small, bg=t["bg"], fg=t["fg_muted"], anchor="w"
        ).pack(side="bottom", fill="x", padx=14, pady=(0, 4))

        editor.focus_set()

    # ── Search bar ────────────────────────────────────────────────────────────
    def open_search_bar(self):
        if not self._search_frame.winfo_ismapped():
            self._search_frame.pack(before=self.output_box, fill="x", pady=(2, 2))
        self._search_entry.focus_set()
        self._search_entry.select_range(0, tk.END)
        self._search_highlight_all()

    def close_search_bar(self):
        if self._search_frame.winfo_ismapped():
            self._search_frame.pack_forget()
        self.output_box.tag_remove("search_match", "1.0", tk.END)
        self._search_count_var.set("")

    def _search_highlight_all(self):
        self.output_box.tag_remove("search_match", "1.0", tk.END)
        query = self._search_var.get()
        if not query:
            self._search_count_var.set("")
            return
        count = 0
        pos = "1.0"
        while True:
            idx = self.output_box.search(query, pos, stopindex=tk.END, nocase=True)
            if not idx:
                break
            end = f"{idx}+{len(query)}c"
            self.output_box.tag_add("search_match", idx, end)
            count += 1
            pos = end
        self._search_count_var.set(f"{count} matches" if count else "no matches")

    def _search_next(self, reverse=False):
        query = self._search_var.get()
        if not query:
            return
        cur = self.output_box.index(tk.INSERT)
        if reverse:
            idx = self.output_box.search(query, cur, backwards=True, nocase=True, stopindex="1.0")
            if not idx:
                idx = self.output_box.search(query, tk.END, backwards=True, nocase=True, stopindex="1.0")
        else:
            idx = self.output_box.search(query, cur, nocase=True, stopindex=tk.END)
            if not idx:
                idx = self.output_box.search(query, "1.0", nocase=True, stopindex=tk.END)
        if idx:
            end = f"{idx}+{len(query)}c"
            self.output_box.mark_set(tk.INSERT, end)
            self.output_box.see(idx)

    def _search_prev(self): self._search_next(reverse=True)

    # ── Help dialog ───────────────────────────────────────────────────────────
    def show_help_dialog(self):
        t = THEMES[self._theme]
        dlg = tk.Toplevel(self)
        dlg.title("Keyboard Shortcuts & Features")
        dlg.geometry("640x520")
        dlg.configure(bg=t["bg"])
        dlg.transient(self); dlg.grab_set()

        content = (
            "──  TRANSLATION  ──\n"
            "  Ctrl+Enter           Translate now\n"
            "  Ctrl+M               Toggle Table / Inline mode\n"
            "  Ctrl+Shift+D         Toggle Forward / Reverse direction\n"
            "\n"
            "──  CLIPBOARD / FILES  ──\n"
            "  Ctrl+Shift+C         Copy output\n"
            "  Ctrl+S               Save output to file\n"
            "  Ctrl+R               Reload db_schema_output.json\n"
            "  Drag file onto window  Load file into input\n"
            "\n"
            "──  EDITING  ──\n"
            "  Ctrl+⌫ (BackSpace)  Clear input + output\n"
            "  Ctrl+F               Open search bar\n"
            "  Esc                  Close search bar\n"
            "\n"
            "──  VIEW  ──\n"
            "  Ctrl + / =           Zoom in\n"
            "  Ctrl + -             Zoom out\n"
            "  Ctrl + 0             Reset zoom\n"
            "\n"
            "──  EXCLUSIONS  ──\n"
            "  Right-click text     Add / remove exclusion\n"
            "  ⊘ Exclusions button  Open exclusion dialog (bulk edit)\n"
            "  Ctrl+D (in dialog)   Delete selected lines\n"
            "  Ctrl+Z / Ctrl+Y      Undo / redo\n"
            "\n"
            "──  INLINE MODE  ──\n"
            "  Blue underline        Table name replacement\n"
            "  Green underline       Column name replacement\n"
            "  Yellow underline + ⚠  Ambiguous (multiple logical names)\n"
            "  Hover                 Show tooltip with full context\n"
            "\n"
            "──  DESIGN DOC MODE  ──\n"
            "  Paste a Java method   (with sb.append(\"...\") SQL-builder)\n"
            "                        → generates ■処理区分 / ■登録テーブル /\n"
            "                          ■項目移送 … design-doc template.\n"
            "  UPPERCASE columns     Toggle to force column names to uppercase.\n"
            "  Forward direction     Physical names → logical (Japanese).\n"
            "  Reverse direction     Keep / restore physical names.\n"
            "\n"
            "──  MISC  ──\n"
            "  F1                   Show this help\n"
            "  History dropdown     Re-load last 10 inputs\n"
            "  Schema dropdown      Limit translation to one schema\n"
        )
        txt = scrolledtext.ScrolledText(dlg, wrap=tk.WORD, font=self._mono,
            bg=t["output_bg"], fg=t["fg"], relief="flat", borderwidth=0, padx=12, pady=10)
        txt.pack(fill="both", expand=True, padx=14, pady=(12, 8))
        txt.insert("1.0", content)
        txt.configure(state="disabled")

        tk.Button(dlg, text="Close", font=self._btn, relief="flat",
            padx=18, pady=6, cursor="hand2", bd=0,
            bg=t["accent"], fg=t["accent_fg"],
            activebackground=t["accent"], activeforeground=t["accent_fg"],
            command=dlg.destroy
        ).pack(side="bottom", pady=(0, 12))

    # ── Index stats in status bar ─────────────────────────────────────────────
    def _refresh_index_stats(self):
        self._sb_index.configure(
            text=f"  ● {len(self.table_index)} tables · {len(self.column_index)} columns · "
                 f"{len(self.schemas)} schema(s) loaded")

    # ── Close handler ─────────────────────────────────────────────────────────
    def on_close(self):
        # Persist settings
        try:
            self._settings.update({
                "theme":         self._theme,
                "mode":          self._mode.get(),
                "direction":     self._direction.get(),
                "filter_schemas":          sorted(self._filter_schemas),
                "filter_tables":           sorted(self._filter_tables),
                "design_uppercase":        bool(self._uppercase_var.get()),
                "design_show_overview":    bool(self._show_overview.get()),
                "design_show_sql_logical": bool(self._show_sql_logical.get()),
                "design_show_sql_physical":bool(self._show_sql_physical.get()),
                "design_show_stype":       bool(self._show_stype.get()),
                "design_show_target":      bool(self._show_target.get()),
                "design_show_projection":  bool(self._show_projection.get()),
                "design_show_from":        bool(self._show_from.get()),
                "design_show_join":        bool(self._show_join.get()),
                "design_show_where":       bool(self._show_where.get()),
                "design_show_group":       bool(self._show_group.get()),
                "design_show_having":      bool(self._show_having.get()),
                "design_show_order":       bool(self._show_order.get()),
                "design_show_footer":      bool(self._show_footer.get()),
                "pane_orient":             self._pane_orient,
                "font_size":     self._font_size,
                "geometry":      self.winfo_geometry(),
            })
            save_settings(self._settings)
            # Save current input to history if non-trivial
            text = self._current_input().strip()
            if text and len(text) > 10:
                self._add_history(text)
        except Exception:
            pass
        self.destroy()


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not os.path.exists(JSON_FILE):
        msg = (
            f"db_schema_output.json was not found next to the application.\n\n"
            f"Expected location:\n  {JSON_FILE}\n\n"
            f"Please place db_schema_output.json in this folder and try again."
        )
        try:
            # Use a tiny Tk root so packaged (--windowed) users see a dialog
            root = tk.Tk(); root.withdraw()
            messagebox.showerror("Missing data file", msg)
        except Exception:
            print("ERROR:", msg)
        sys.exit(1)

    print(f"Loading: {JSON_FILE}")
    app = TranslatorApp(JSON_FILE)
    if not _DND_AVAILABLE:
        print("  (Install 'tkinterdnd2' for drag-and-drop file support: pip install tkinterdnd2)")
    app.mainloop()

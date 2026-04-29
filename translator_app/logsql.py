"""Read a `stclibApp.log`, find a prepared-statement entry by `id=<HEX>`,
combine the SQL with its bound parameters, and return the executable SQL.

Log format observed in the wild (real `stclibApp.log` examples):

    2026-04-29 11:07:42,DEBUG,commons.dao.PreparedStatementEx,<init>              ,CreatePreparedStatement id=189369c1   sql= WITH TMP_TBL AS  …
    2026-04-29 11:07:42,INFO,commons.dao.PreparedStatementEx,executeQuery        ,PreparedStatement.executeQuery() id=189369c1  params=[STRING:1:0000][STRING:2:0018][STRING:3:…]

Each PreparedStatement is logged twice: once at construction (carries the
SQL text with `?` placeholders) and once on execute (carries the bound
parameters in `[TYPE:N:value]` format). The two are correlated by the
`id=<HEX>` token on both lines.

Nearby `InvokeDao` lines record the FQCN that owns the statement, e.g.
`Dao<garbled>jp.co.vinculumjapan.mdware.shiire.dao.impl.PdaDataSelectDao`.
We extract that as a hint for the user; failure to find it is non-fatal.

Public API
----------
* parse_params(raw)            → list[(type, value)]
* format_param(typ, value)     → SQL literal string
* count_placeholders(sql)      → int  (skips `?` inside string literals)
* combine_sql_params(sql, ps)  → str  (substitutes positional placeholders)
* find_entry_by_id(text, id)   → dict | None
* find_last_entry(text)        → dict | None
"""

from __future__ import annotations

import re
from typing import Iterable

# ── Regex constants ───────────────────────────────────────────────────────────
# `id=<HEX>` — case-insensitive hex of any reasonable length.
_ID_RE = re.compile(r"\bid=([0-9A-Fa-f]{4,})\b")

# Constructor line: carries the SQL text. Captures (id, sql).
# We tolerate any whitespace between `id=…` and `sql=…`.
_INIT_LINE_RE = re.compile(
    r"CreatePreparedStatement\s+id=([0-9A-Fa-f]{4,})\s+sql=(.*)$",
)

# Execute line: carries the params. Captures (id, raw_params_blob).
_EXEC_LINE_RE = re.compile(
    r"PreparedStatement\.(?:executeQuery|execteUpdate|executeUpdate)\(\)\s+"
    r"id=([0-9A-Fa-f]{4,})\s+params=(.*)$",
)

# A single `[TYPE:POS:VALUE]` segment. We greedy-match VALUE up to the next
# `][` boundary or the end of the blob — values may contain spaces and even
# `[` characters (e.g. `[STRING:3:…log message containing comma…]`), but in
# the observed logs they never embed a literal `][TYPE:` substring.
_PARAM_SEG_RE = re.compile(
    r"\[([A-Z][A-Z0-9_]*)\s*:\s*(\d+)\s*:(.*?)\](?=\[[A-Z]|$)",
    re.DOTALL,
)

# `InvokeDao` (Dao open) lines record the FQCN. The Japanese prefix in the
# message is mojibake-encoded depending on the locale; we find the first
# `jp.co.…` substring and treat that as the class. Defensive — many logs
# use other vendor prefixes; we just grab the longest dotted identifier.
_FQCN_RE = re.compile(r"\b((?:[a-zA-Z_]\w*\.){2,}[A-Za-z_]\w*)\b")

# Recognise a SQL string literal (single quotes, with `''` as escape) so we
# don't substitute placeholders found inside one.
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")


# ── Param parsing ─────────────────────────────────────────────────────────────
def parse_params(raw: str) -> list[tuple[str, str]]:
    """Parse a `[TYPE:N:value]…` blob into a position-ordered list of
    (type, value) pairs. Empty blob → empty list.

    The position number `N` (1-indexed) is honoured: segments are returned
    sorted by N. Holes (e.g. positions 1, 3 with 2 missing) are preserved by
    inserting empty STRING placeholders so the resulting list always has
    length max(N) — keeps positional substitution honest.
    """
    if not raw:
        return []
    by_pos: dict[int, tuple[str, str]] = {}
    for typ, pos, val in _PARAM_SEG_RE.findall(raw):
        by_pos[int(pos)] = (typ, val)
    if not by_pos:
        return []
    n = max(by_pos)
    return [by_pos.get(i, ("STRING", "")) for i in range(1, n + 1)]


def format_param(typ: str, value: str) -> str:
    """Render a (type, value) pair as a SQL literal.

    Conservative defaults — STRING-like types are single-quoted with `''`
    escaping; numeric types are emitted bare; NULL is the SQL keyword;
    DATE/TIMESTAMP get the standard quoted form. Unknown types fall back
    to STRING handling (safest)."""
    t = (typ or "STRING").upper()
    if t == "NULL" or value is None:
        return "NULL"
    # Numeric types — emit bare. Trim whitespace; if it doesn't parse as a
    # number we fall back to quoted (defensive — bad data shouldn't crash).
    if t in ("INT", "INTEGER", "LONG", "BIGINT", "SHORT", "SMALLINT", "TINYINT",
             "DECIMAL", "NUMERIC", "NUMBER", "DOUBLE", "FLOAT", "REAL"):
        v = (value or "").strip()
        if v == "":
            return "NULL"
        # Must look like a number; otherwise quote it.
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", v):
            return v
        return _quote_string(v)
    if t == "BOOLEAN" or t == "BOOL":
        v = (value or "").strip().lower()
        if v in ("true", "1", "t", "y", "yes"):
            return "1"
        if v in ("false", "0", "f", "n", "no", ""):
            return "0"
        return _quote_string(value)
    if t in ("DATE", "TIMESTAMP", "TIME", "DATETIME"):
        return _quote_string(value)
    if t == "BYTES" or t == "BLOB":
        # Best we can do without knowing the encoding — emit as a hex literal
        # if the value looks hex; else quote.
        v = (value or "").strip()
        if re.fullmatch(r"[0-9A-Fa-f]*", v) and v:
            return f"X'{v}'"
        return _quote_string(value)
    # STRING / CHAR / VARCHAR / CLOB / unknown → single-quoted.
    return _quote_string(value)


def _quote_string(value: str) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


# ── Placeholder counting + substitution ───────────────────────────────────────
def count_placeholders(sql: str) -> int:
    """Count `?` placeholders in `sql`, ignoring `?` inside SQL string
    literals. Line comments (`--`) and block comments (`/* */`) are NOT
    stripped — Excel's reference tool doesn't either, and the bound params
    follow the count produced by the JDBC driver, which sees the raw SQL."""
    return sum(1 for _ in _iter_placeholders(sql))


def _iter_placeholders(sql: str) -> Iterable[int]:
    """Yield offsets of each `?` in `sql` that's outside a string literal."""
    if not sql:
        return
    masked = _mask_string_literals(sql)
    for i, ch in enumerate(masked):
        if ch == "?":
            yield i


def _mask_string_literals(sql: str) -> str:
    """Replace every `'…'` literal with same-length runs of `\\x00` so a
    naive char-by-char scan can ignore characters inside literals while
    keeping all other indices identical."""
    def _repl(m: re.Match) -> str:
        return "\x00" * (m.end() - m.start())
    return _STRING_LITERAL_RE.sub(_repl, sql)


def combine_sql_params(sql: str, params: list[tuple[str, str]]) -> str:
    """Substitute positional `?` placeholders in `sql` with formatted
    values from `params`. `?` inside string literals is left alone.

    Mismatch handling:
      * fewer params than placeholders → trailing `?`s are kept verbatim
        (so the user sees what's missing).
      * more params than placeholders → extras silently dropped.
    Both cases are surfaced by `count_placeholders` vs `len(params)` —
    the dialog shows both numbers so users notice.
    """
    if not sql:
        return sql
    if not params:
        return sql
    out: list[str] = []
    last = 0
    pi = 0
    for off in _iter_placeholders(sql):
        out.append(sql[last:off])
        if pi < len(params):
            typ, val = params[pi]
            out.append(format_param(typ, val))
            pi += 1
        else:
            out.append("?")  # ran out of params — leave the placeholder
        last = off + 1
    out.append(sql[last:])
    return "".join(out)


# ── Log scanning ──────────────────────────────────────────────────────────────
def find_entry_by_id(log_text: str, query_id: str) -> dict | None:
    """Scan `log_text` for the SQL/params/class belonging to `query_id`.

    Returns a dict on success:
        {
          "id":        "189369c1",
          "sql":       "WITH TMP_TBL AS …",
          "params_raw":"[STRING:1:0000]…",
          "params":    [("STRING", "0000"), …],
          "fqcn":      "jp.co.vinculumjapan.mdware…PdaDataSelectDao" | None,
          "result":    "WITH TMP_TBL AS ( SELECT '0000' …)",
        }
    Returns None when the id isn't found.
    """
    if not query_id or not log_text:
        return None
    target = query_id.strip().lower()
    sql_text: str | None = None
    params_raw: str | None = None
    fqcn: str | None = None
    last_fqcn: str | None = None  # most recent InvokeDao FQCN we've seen

    # Single linear pass — log files run to many MB; load as string is OK
    # for typical sizes (the sample is 50 KB; even 50 MB fits in memory).
    for line in log_text.splitlines():
        # Track the running FQCN so we can pin down the dao for this id.
        if "InvokeDao" in line:
            last_fqcn = _extract_fqcn(line) or last_fqcn

        m = _INIT_LINE_RE.search(line)
        if m and m.group(1).lower() == target:
            sql_text = m.group(2).strip()
            fqcn = last_fqcn
            continue
        m = _EXEC_LINE_RE.search(line)
        if m and m.group(1).lower() == target:
            params_raw = m.group(2).strip()
            # SQL might already have been captured; we can stop scanning.
            if sql_text is not None:
                break
    if sql_text is None and params_raw is None:
        return None
    params = parse_params(params_raw or "")
    result = combine_sql_params(sql_text or "", params)
    return {
        "id":         target,
        "sql":        sql_text or "",
        "params_raw": params_raw or "",
        "params":     params,
        "fqcn":       fqcn,
        "result":     result,
    }


def find_last_entry(log_text: str) -> dict | None:
    """Find the most recent prepared-statement entry that has BOTH an
    init line (SQL) and an execute line (params). Used by the dialog's
    'Get last SQL' button.

    Walks the log forwards keeping a per-id pair as we accumulate; the
    last pair to be completed (i.e. its execute line is the latest such
    line in the file) is returned."""
    if not log_text:
        return None
    by_id: dict[str, dict] = {}
    last_complete_id: str | None = None
    last_fqcn: str | None = None
    for line in log_text.splitlines():
        if "InvokeDao" in line:
            last_fqcn = _extract_fqcn(line) or last_fqcn
        m = _INIT_LINE_RE.search(line)
        if m:
            qid = m.group(1).lower()
            entry = by_id.setdefault(qid, {})
            entry["sql"] = m.group(2).strip()
            entry["fqcn"] = entry.get("fqcn") or last_fqcn
            continue
        m = _EXEC_LINE_RE.search(line)
        if m:
            qid = m.group(1).lower()
            entry = by_id.setdefault(qid, {})
            entry["params_raw"] = m.group(2).strip()
            if "sql" in entry:
                last_complete_id = qid
    if not last_complete_id:
        return None
    e = by_id[last_complete_id]
    params = parse_params(e.get("params_raw", ""))
    return {
        "id":         last_complete_id,
        "sql":        e.get("sql", ""),
        "params_raw": e.get("params_raw", ""),
        "params":     params,
        "fqcn":       e.get("fqcn"),
        "result":     combine_sql_params(e.get("sql", ""), params),
    }


def _extract_fqcn(invoke_dao_line: str) -> str | None:
    """Pull the longest `a.b.c.d…` FQCN out of an InvokeDao message.

    The Japanese descriptive text in the message is often mojibake-encoded
    depending on the file's encoding, but the dotted Java FQCN survives
    intact because it's pure ASCII."""
    matches = _FQCN_RE.findall(invoke_dao_line)
    if not matches:
        return None
    # Prefer the longest match — usually the deepest package path.
    matches.sort(key=len, reverse=True)
    # Drop the leading log-category prefix `commons.dao.AbstractDaoInvoker`
    # which would otherwise win for a few InvokeDao lines.
    for cand in matches:
        if cand.startswith("commons.") or cand.startswith("util.") or cand.startswith("common."):
            continue
        return cand
    return matches[0]


# ── File-level convenience ────────────────────────────────────────────────────
def read_log_file(path: str, encoding: str = "utf-8") -> str:
    """Read a log file with a forgiving encoding chain. Returns "" on
    error so callers can show a soft 'no SQL found' instead of crashing.

    The order tries: utf-8 → utf-8-sig → cp932 (Windows-Japanese, common
    for these logs) → latin-1 (always succeeds, possibly mojibake)."""
    chain = [encoding, "utf-8-sig", "cp932", "latin-1"]
    for enc in chain:
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            continue
    # Last-ditch — read bytes and replace.
    try:
        with open(path, "rb") as f:
            return f.read().decode("latin-1", errors="replace")
    except OSError:
        return ""

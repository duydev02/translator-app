"""Read a `stclibApp.log`, find prepared-statement entries, score them so the
1–2 *primary* business queries surface above the dozens of infrastructure
calls, and combine SQL + bound params into runnable SQL.

Log format observed in real `stclibApp.log` files:

    2026-04-29 11:07:42,DEBUG,commons.dao.PreparedStatementEx,<init>              ,CreatePreparedStatement id=189369c1   sql= WITH TMP_TBL AS  …
    2026-04-29 11:07:42,INFO,commons.dao.PreparedStatementEx,executeQuery        ,PreparedStatement.executeQuery() id=189369c1  params=[STRING:1:0000][STRING:2:0018][STRING:3:…]

Each PreparedStatement is logged twice: once at construction (carries the
SQL text with `?` placeholders) and once on execute (carries the bound
parameters in `[TYPE:N:value]` format). The two are correlated by the
`id=<HEX>` token on both lines.

Three other line kinds are exploited to enrich the data:

* `commons.struts.RequestProcessor,callMethod` — marks the start of a
  user-facing request, e.g. `PdaHonbuIdoShijiTorikomiAction#search`. We
  group every prepared statement under the most-recent callMethod so the
  UI can show "this user click ran these 5 queries".
* `commons.dao.AbstractDaoInvoker,InvokeDao` — names the DAO class
  (`jp.co.…shiire.dao.impl.PdaDataSelectDao`). The package path is the
  primary signal/noise discriminator: domain packages
  (`mdware.<domain>.*`) are signal, framework ones (`swc.commons.*`,
  `mdware.common.*`) are noise.
* `initSession` / `endSession` — used as a fallback transaction marker
  when no `callMethod` line precedes a statement (background jobs).

Public API
----------
* parse_params(raw)            → list[(type, value)]
* format_param(typ, value)     → SQL literal string
* count_placeholders(sql)      → int  (skips `?` inside string literals)
* combine_sql_params(sql, ps)  → str  (substitutes positional placeholders)
* parse_log(text)              → list[Statement]      ← the new "scan everything" entry point
* group_by_action(stmts)       → list[Action]         ← list grouped under user requests
* score_statement(stmt, …)     → int                  ← higher = more likely a primary business query
* extract_statement_type(sql)  → "SELECT" / "INSERT" / …
* extract_target_tables(sql)   → list[str]            ← short list, max 4
* find_entry_by_id(text, id)   → dict | None          ← thin wrapper over parse_log (back-compat)
* find_last_entry(text)        → dict | None          ← thin wrapper over parse_log (back-compat)

Constants:
* DEFAULT_NOISE_PACKAGES, DEFAULT_NOISE_TABLES, DEFAULT_PRIMARY_THRESHOLD
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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

# Front of every log line: `YYYY-MM-DD HH:MM:SS,LEVEL,LOGGER,METHOD,…`. We
# only need the timestamp; the rest is matched generically below.
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# `commons.struts.RequestProcessor,callMethod` — start of a user request.
# Captures the action label, e.g. `PdaHonbuIdoShijiTorikomiAction#search`.
_CALL_METHOD_RE = re.compile(
    r"RequestProcessor\s*,\s*callMethod\s*,\s*"
    r"(?:[A-Za-z_][\w$]*\.)*([A-Za-z_][\w$]*#[A-Za-z_][\w$]*)"
)
# `commons.dao.ServletDaoInvoker,initSession` / `endSession` — fallback
# transaction markers when there's no callMethod (e.g. background jobs).
_INIT_SESSION_RE = re.compile(r"ServletDaoInvoker\s*,\s*initSession")
_END_SESSION_RE  = re.compile(r"ServletDaoInvoker\s*,\s*endSession")

# Pull SELECT/INSERT/UPDATE/DELETE/MERGE/WITH off the front of a SQL.
_STMT_TYPE_RE = re.compile(
    r"\s*(?:--[^\n]*\n|/\*.*?\*/|\s)*"
    r"\b(WITH|SELECT|INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|ALTER|DROP)\b",
    re.IGNORECASE | re.DOTALL,
)

# Crude but effective table extractor — first identifier after FROM / INTO /
# UPDATE / JOIN / TABLE keywords. Skips `(` (subqueries / derived tables) and
# common generic aliases like `TMP_TBL` / `MAIN`.
_TABLE_AFTER_RE = re.compile(
    r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+(?:\w+\.)?([A-Z_][A-Z0-9_]{1,})",
    re.IGNORECASE,
)
_GENERIC_ALIAS = {"TMP_TBL", "MAIN", "DUAL", "TBL_1", "TBL_2", "TBL_3",
                  "TBL_4", "TBL_5", "T", "A", "B", "C", "X", "Y"}


# ── Default scoring tables ────────────────────────────────────────────────────
# Substrings that, if present in a DAO's package path, mark the statement as
# infrastructure (config reads, audit logging, message lookup, etc.).
DEFAULT_NOISE_PACKAGES = ("swc.commons", "mdware.common")

# Tables whose statements are almost always cross-cutting concerns rather
# than a primary business query. Hits subtract from the score.
DEFAULT_NOISE_TABLES = (
    "SYSTEM_CONTROL", "DT_TABLE_LOG", "R_MESSAGE",
    "R_DICTIONARY_CONTROL", "R_NAMECTF",
)

# A statement scoring at or above this threshold gets the ★ primary tag and
# is the only kind shown when "Hide infrastructure" is on.
DEFAULT_PRIMARY_THRESHOLD = 30


# ── Statement / Action data classes ───────────────────────────────────────────
@dataclass
class Statement:
    """One prepared statement extracted from the log.

    `score` is filled by `score_statement(...)` and depends on the user's
    project-specific noise/primary lists; raw parsers leave it at 0."""
    id:         str = ""
    timestamp:  str = ""             # "YYYY-MM-DD HH:MM:SS"
    sql:        str = ""             # raw, with `?` placeholders
    params_raw: str = ""             # `[STRING:1:…][STRING:2:…]`
    params:     list[tuple[str, str]] = field(default_factory=list)
    fqcn:       str | None = None    # full DAO class name
    action:     str | None = None    # callMethod label, e.g. `…#search`
    op:         str = ""             # "executeQuery" or "executeUpdate"
    init_line:  int = 0              # line number of the <init> log line
    exec_line:  int = 0              # line number of the execute log line
    score:      int = 0              # primary-vs-noise score

    # Lazily-computed views; cheap, but nice to compute once and cache.
    _stmt_type: str | None = None
    _tables:    list[str] | None = None

    @property
    def statement_type(self) -> str:
        if self._stmt_type is None:
            self._stmt_type = extract_statement_type(self.sql)
        return self._stmt_type

    @property
    def target_tables(self) -> list[str]:
        if self._tables is None:
            self._tables = extract_target_tables(self.sql)
        return self._tables

    @property
    def dao_short(self) -> str:
        """Last segment of the FQCN — what's useful in a list view."""
        if not self.fqcn:
            return ""
        return self.fqcn.rsplit(".", 1)[-1]

    @property
    def is_primary(self) -> bool:
        return self.score >= DEFAULT_PRIMARY_THRESHOLD

    def combined_sql(self) -> str:
        """SQL with `?` placeholders substituted using `params`."""
        return combine_sql_params(self.sql, self.params)

    # Back-compat shim: the old find_entry_by_id() returned a dict; some
    # callers (and tests) expect dict-style access. Make it walk talk like one.
    def as_dict(self) -> dict:
        return {
            "id":         self.id,
            "timestamp":  self.timestamp,
            "sql":        self.sql,
            "params_raw": self.params_raw,
            "params":     self.params,
            "fqcn":       self.fqcn,
            "action":     self.action,
            "op":         self.op,
            "score":      self.score,
            "result":     self.combined_sql(),
        }


@dataclass
class Action:
    """A user-facing request grouping one or more Statements.

    `label` is the callMethod hint (e.g. `…#search`) when available, or
    a synthetic fallback like `Session @ 11:09:36` when only an
    init/endSession marker was seen, or `Detached statements` for
    statements with no preceding marker at all."""
    label:      str
    timestamp:  str = ""
    statements: list[Statement] = field(default_factory=list)


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


# Sentinel chars used by combine_sql_params_marked() to delimit substituted
# values inside the produced text. Chosen from the ASCII control range so
# pretty_sql's whitespace handling and the dialog's text rendering pass them
# through verbatim, and they're vanishingly unlikely to appear in real SQL
# or in bound param values.
SUBST_OPEN  = "\x01"   # start of a substituted run
SUBST_CLOSE = "\x02"   # end of a substituted run


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


def combine_sql_params_marked(
    sql: str, params: list[tuple[str, str]],
) -> tuple[str, list[tuple[int, int]]]:
    """Like `combine_sql_params`, but records where each substituted value
    lives in the output. Returns `(text_with_sentinels, [(start, end)])`.

    Sentinels (`SUBST_OPEN` / `SUBST_CLOSE`) wrap each substituted run in
    the returned text. Callers should pass that text through `pretty_sql`
    (sentinels survive — pretty_sql treats them as ordinary chars), then
    call `extract_subst_ranges()` to strip them and recover the final
    `(clean_text, ranges)` so the UI can highlight bound values.

    Why a sentinel dance: pretty_sql changes character offsets (newlines /
    indentation), so the offsets we'd compute up front are stale by the
    time the user sees the text. Sentinels follow the text through the
    formatter and let us recover ranges in the final coordinates.
    """
    if not sql or not params:
        return sql, []
    out: list[str] = []
    last = 0
    pi = 0
    for off in _iter_placeholders(sql):
        out.append(sql[last:off])
        if pi < len(params):
            typ, val = params[pi]
            out.append(SUBST_OPEN)
            out.append(format_param(typ, val))
            out.append(SUBST_CLOSE)
            pi += 1
        else:
            out.append("?")
        last = off + 1
    out.append(sql[last:])
    return "".join(out), []  # ranges intentionally empty here — call
                              # extract_subst_ranges after prettifying


def extract_subst_ranges(marked: str) -> tuple[str, list[tuple[int, int]]]:
    """Strip `SUBST_OPEN/CLOSE` sentinels from `marked` and return the
    clean text plus the (start, end) char ranges of each substituted run.

    Robust against unbalanced sentinels (defensive — should never happen
    with our own producer, but a stray one in input data shouldn't break
    rendering): orphans are simply stripped and not added to ranges."""
    if SUBST_OPEN not in marked:
        return marked, []
    out: list[str] = []
    ranges: list[tuple[int, int]] = []
    pos = 0           # offset in the cleaned output we're building
    open_at: int | None = None
    for ch in marked:
        if ch == SUBST_OPEN:
            open_at = pos
        elif ch == SUBST_CLOSE:
            if open_at is not None:
                ranges.append((open_at, pos))
                open_at = None
            # silently drop unbalanced close
        else:
            out.append(ch)
            pos += 1
    return "".join(out), ranges


# ── Pretty-printer (very small, deliberately minimal) ────────────────────────
# Major clause keywords get a fresh line. Joins get a fresh line *and* a
# small indent so they read nested under the FROM. AND/OR connectives in
# conditions go on their own indented line. The aim is "easy to scan",
# not RDBMS-grade reflow — leave a real formatter to the user's IDE.
_TOP_LEVEL_KEYWORDS = (
    "WITH", "SELECT", "INSERT INTO", "INTO", "VALUES", "UPDATE", "SET",
    "DELETE FROM", "MERGE INTO", "FROM", "WHERE", "GROUP BY", "HAVING",
    "ORDER BY", "LIMIT", "OFFSET", "FETCH FIRST",
    "UNION ALL", "UNION", "INTERSECT", "EXCEPT",
)
_JOIN_KEYWORDS = (
    "LEFT OUTER JOIN", "RIGHT OUTER JOIN", "FULL OUTER JOIN",
    "LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "INNER JOIN", "CROSS JOIN", "JOIN",
    "ON",
)
# Connectives inside ON/WHERE/HAVING — indented further so they sit under
# their parent clause visually.
_CONNECTIVE_KEYWORDS = ("AND", "OR")

# Build one big alternation, longest first so e.g. `LEFT OUTER JOIN` matches
# before `JOIN`. Word-boundary on both sides keeps `INTO` from triggering
# inside `INTOSAKI_TENPO_CD`.
_PRETTY_TOP_RE = re.compile(
    r"\b(" + "|".join(re.escape(kw) for kw in
                      sorted(_TOP_LEVEL_KEYWORDS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)
_PRETTY_JOIN_RE = re.compile(
    r"\b(" + "|".join(re.escape(kw) for kw in
                      sorted(_JOIN_KEYWORDS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)
_PRETTY_CONNECTIVE_RE = re.compile(
    r"\b(" + "|".join(_CONNECTIVE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def pretty_sql(sql: str) -> str:
    """Lightweight SQL prettifier. Inserts newlines before major clauses
    and joins; leaves the surrounding text untouched. Quote-aware: tokens
    inside `'…'` literals are left alone.

    Returns `sql` unchanged for empty input. The output isn't reflowed to
    a target column width — long SELECT lists stay on one line. The goal
    is "give my eyes a chance" on a 933-char query, not full RDBMS-grade
    formatting (your IDE does that better)."""
    if not sql or not sql.strip():
        return sql

    # Mask string literals so keywords inside them aren't broken across
    # lines. We compute newline insert positions on the masked string,
    # then apply them to the original (so the visible content survives).
    masked = _mask_string_literals(sql)

    # Collect (offset, replacement) tuples. We rebuild the string in one
    # pass at the end to avoid mutating positions mid-loop.
    edits: list[tuple[int, int, str]] = []  # (start, end, new_text)

    def _emit(m: re.Match, prefix: str):
        start, end = m.start(1), m.end(1)
        # Don't emit a newline for the very first non-whitespace token —
        # the SQL already starts there.
        leading = masked[:start]
        if not leading.strip():
            return
        # Don't double-up newlines: if the char immediately before the
        # match is already \n (possibly with spaces), skip.
        i = start - 1
        while i >= 0 and masked[i] in " \t":
            i -= 1
        if i >= 0 and masked[i] == "\n":
            return
        kw = sql[start:end]    # keep original casing
        edits.append((start, end, prefix + kw))

    for m in _PRETTY_TOP_RE.finditer(masked):
        _emit(m, "\n")
    for m in _PRETTY_JOIN_RE.finditer(masked):
        _emit(m, "\n  ")
    for m in _PRETTY_CONNECTIVE_RE.finditer(masked):
        _emit(m, "\n    ")

    if not edits:
        return sql.strip()

    # Apply edits in reverse so earlier positions don't shift.
    edits.sort(key=lambda e: e[0])
    out: list[str] = []
    cursor = 0
    for start, end, repl in edits:
        if start < cursor:
            continue   # shouldn't happen with sorted, non-overlapping edits
        out.append(sql[cursor:start])
        out.append(repl)
        cursor = end
    out.append(sql[cursor:])
    pretty = "".join(out).strip()

    # Collapse runs of whitespace *after* the first non-space char on each
    # line — keeps the leading indent we added for JOIN/AND/OR but folds
    # the tab/space padding the original Java string concatenation left
    # inside each clause. The lookbehind on `(?<=\S)` is what protects the
    # leading indent.
    pretty = "\n".join(re.sub(r"(?<=\S)[ \t]{2,}", " ", line).rstrip()
                       for line in pretty.splitlines())
    return pretty


# ── SQL tokenizer (lightweight, used for syntax highlighting) ────────────────
# Reserved word set — covers what's actually present in the in-tree logs.
# Case-insensitive on lookup, but the output preserves the original casing so
# the highlighter doesn't change what the user sees.
_SQL_KEYWORDS = frozenset(s.upper() for s in (
    "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "EXISTS", "BETWEEN",
    "LIKE", "IS", "NULL", "AS", "DISTINCT", "ALL", "GROUP", "BY", "HAVING",
    "ORDER", "ASC", "DESC", "LIMIT", "OFFSET", "FETCH", "FIRST", "ROWS",
    "ROW", "WITH", "RECURSIVE", "UNION", "INTERSECT", "EXCEPT", "VALUES",
    "INSERT", "INTO", "UPDATE", "SET", "DELETE", "MERGE", "TRUNCATE",
    "CREATE", "ALTER", "DROP", "TABLE", "INDEX", "VIEW", "PROCEDURE",
    "FUNCTION", "CASE", "WHEN", "THEN", "ELSE", "END", "ON", "USING",
    "JOIN", "INNER", "OUTER", "LEFT", "RIGHT", "FULL", "CROSS",
    "COALESCE", "NULLIF", "CAST", "CONVERT", "EXTRACT",
    "TRUE", "FALSE", "DEFAULT", "PRIMARY", "KEY", "FOREIGN", "REFERENCES",
    "CHECK", "UNIQUE", "CONSTRAINT", "GRANT", "REVOKE", "COMMIT", "ROLLBACK",
    "SAVEPOINT", "BEGIN", "DECLARE", "RETURN", "RETURNING",
))

# Pre-compiled scanner. Each named group is one token kind. We try them in
# this order: comments before strings before numbers before words, and the
# "anything else" catch-all keeps the scan moving over whitespace/punct.
# `re.DOTALL` so `/* … */` block comments can span newlines.
_SQL_TOKEN_RE = re.compile(
    r"(?P<lcomment>--[^\n]*)"
    r"|(?P<bcomment>/\*.*?\*/)"
    r"|(?P<string>'(?:''|[^'])*')"
    r"|(?P<number>\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)"
    r"|(?P<word>[A-Za-z_][A-Za-z_0-9]*)"
    r"|(?P<other>.)",
    re.DOTALL,
)


def tokenize_sql_for_highlight(sql: str) -> list[tuple[int, int, str]]:
    """Return `(start, end, tag)` triples for syntax-highlight tagging.

    Tags emitted: `"keyword"`, `"string"`, `"number"`, `"comment"`. Plain
    identifiers and punctuation/whitespace are NOT emitted (the caller
    leaves them in the default style), keeping the tag set minimal so the
    Tk Text widget stays fast on multi-thousand-char SQL."""
    out: list[tuple[int, int, str]] = []
    if not sql:
        return out
    for m in _SQL_TOKEN_RE.finditer(sql):
        kind = m.lastgroup
        if kind == "lcomment" or kind == "bcomment":
            out.append((m.start(), m.end(), "comment"))
        elif kind == "string":
            out.append((m.start(), m.end(), "string"))
        elif kind == "number":
            out.append((m.start(), m.end(), "number"))
        elif kind == "word":
            if m.group(0).upper() in _SQL_KEYWORDS:
                out.append((m.start(), m.end(), "keyword"))
        # 'other' and non-keyword 'word' fall through — no tag.
    return out


# ── SQL shape helpers (statement type / target tables) ───────────────────────
def extract_statement_type(sql: str) -> str:
    """Return the leading verb of a SQL — `SELECT`, `INSERT`, `UPDATE`,
    `DELETE`, `MERGE`, `WITH`, `TRUNCATE`, `CREATE`, `ALTER`, `DROP`.

    Falls back to `""` for empty SQL or `"SQL"` if the input doesn't start
    with one of the recognised verbs (e.g. some stored-procedure call)."""
    if not sql:
        return ""
    m = _STMT_TYPE_RE.match(sql)
    if not m:
        return "SQL"
    return m.group(1).upper()


def extract_target_tables(sql: str, max_n: int = 4) -> list[str]:
    """Return up to `max_n` distinct, real-looking target tables from the
    SQL — first ones following FROM/INTO/UPDATE/JOIN.

    Generic aliases and one-letter names (TMP_TBL, MAIN, T, X…) are
    filtered out; the order is preserved (so the FROM table appears
    first, then the joined ones). De-duplicated."""
    if not sql:
        return []
    out: list[str] = []
    seen: set[str] = set()
    masked = _mask_string_literals(sql)
    for m in _TABLE_AFTER_RE.finditer(masked):
        name = m.group(1).upper()
        if name in _GENERIC_ALIAS or len(name) <= 2:
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= max_n:
            break
    return out


# ── Scoring (primary vs infrastructure) ───────────────────────────────────────
def score_statement(
    stmt: Statement,
    *,
    primary_packages: Iterable[str] = (),
    noise_packages: Iterable[str] = DEFAULT_NOISE_PACKAGES,
    noise_tables: Iterable[str] = DEFAULT_NOISE_TABLES,
) -> int:
    """Heuristic score: higher = more likely a primary business query.

    The defaults work well for the sample stclibApp.log:
      * `swc.commons.*` and `mdware.common.*` packages → infrastructure
      * `SYSTEM_CONTROL / DT_TABLE_LOG / R_MESSAGE / R_DICTIONARY_CONTROL
        / R_NAMECTF` tables → infrastructure
      * `WITH` / `JOIN` and longer SQL → primary signal
      * Many bound params → primary signal (real user input)

    `primary_packages` is opt-in: when set, statements whose DAO is in one
    of those packages get a strong positive bonus. Empty list means
    "everything not in noise_packages is potentially primary"."""
    score = 0
    fqcn = stmt.fqcn or ""
    sql = stmt.sql or ""

    # Package signal — explicit primary list wins outright.
    is_noise_pkg = bool(noise_packages and any(p in fqcn for p in noise_packages))
    if primary_packages and any(p in fqcn for p in primary_packages):
        score += 50
    if is_noise_pkg:
        score -= 80
    elif fqcn:
        # Baseline bonus: DAO is known and not in any noise package, so
        # it's at least a domain class. Without this, short domain SELECTs
        # like `TenpoSelectDao` sit just below the primary threshold.
        score += 10

    # SQL complexity.
    n = len(sql)
    if n > 500:
        score += 20
    if n > 1500:
        score += 10
    if n < 200:
        score -= 10

    # Joins, CTEs, unions — hallmarks of business queries.
    upper = sql.upper()
    if " JOIN " in upper or "\nJOIN" in upper:
        score += 15
    if upper.lstrip().startswith("WITH"):
        score += 15
    if " UNION " in upper or " UNION ALL " in upper:
        score += 10

    # Param count — more params means more user input being threaded
    # through. Capped so a 50-bind logging insert doesn't dominate.
    n_params = len(stmt.params)
    score += min(n_params, 6) * 3   # capped at +18

    # Target-table noise.
    for tbl in stmt.target_tables:
        if tbl in noise_tables:
            score -= 40
            break  # one infrastructure target is enough to mark it

    # Single-param config lookups — `SELECT … WHERE PARAMETER_ID = ?`.
    if n_params == 1 and n < 300:
        score -= 15

    return score


# ── Full-log parser ──────────────────────────────────────────────────────────
def parse_log(log_text: str) -> list[Statement]:
    """Walk `log_text` once and return every prepared statement found.

    Pairs init/execute lines by `id`. A statement appears in the result
    even if only one of the two lines was logged (e.g. a query that errored
    before execute) — the missing side stays empty so the UI can flag it.

    Statements are returned in the order their *init* line appeared. Each
    statement also carries the most-recent `callMethod` action label and
    the most-recent InvokeDao FQCN observed before its init line, which
    lets the UI group by user request and label by DAO."""
    if not log_text:
        return []
    by_id: dict[str, Statement] = {}
    order: list[str] = []
    last_fqcn: str | None = None
    last_action: str | None = None
    last_session_ts: str | None = None
    # Recorded as we go so we can fill in missing FQCNs in a second pass —
    # some logs (e.g. files that start mid-session) have an init line BEFORE
    # the first InvokeDao, so the running last_fqcn is still None.
    invoke_records: list[tuple[int, str]] = []  # (lineno, fqcn)

    for lineno, line in enumerate(log_text.splitlines(), start=1):
        # Track DAO / action / session context — these prime the next
        # statement's metadata.
        if "InvokeDao" in line:
            f = _extract_fqcn(line)
            if f:
                last_fqcn = f
                invoke_records.append((lineno, f))
            continue
        cm = _CALL_METHOD_RE.search(line)
        if cm:
            last_action = cm.group(1)
            continue
        if _INIT_SESSION_RE.search(line):
            ts = _extract_timestamp(line)
            last_session_ts = ts or last_session_ts
            continue
        if _END_SESSION_RE.search(line):
            # Closing a session may signal the end of a callMethod block;
            # we don't reset last_action immediately (some logs interleave
            # session boundaries) — see group_by_action for the truth.
            continue

        # Init line: SQL with `?` placeholders.
        m = _INIT_LINE_RE.search(line)
        if m:
            qid = m.group(1).lower()
            sql = m.group(2).strip()
            stmt = by_id.get(qid)
            if stmt is None:
                stmt = Statement(id=qid)
                by_id[qid] = stmt
                order.append(qid)
            stmt.sql = sql
            stmt.fqcn = stmt.fqcn or last_fqcn
            stmt.action = stmt.action or last_action
            stmt.timestamp = stmt.timestamp or _extract_timestamp(line) or last_session_ts or ""
            stmt.init_line = lineno
            continue

        # Exec line: bound params. May arrive before init in pathological
        # logs but we still pair them.
        m = _EXEC_LINE_RE.search(line)
        if m:
            qid = m.group(1).lower()
            stmt = by_id.get(qid)
            if stmt is None:
                stmt = Statement(id=qid)
                by_id[qid] = stmt
                order.append(qid)
            stmt.params_raw = m.group(2).strip()
            stmt.params = parse_params(stmt.params_raw)
            stmt.exec_line = lineno
            stmt.op = "executeUpdate" if "execteUpdate" in line or "executeUpdate" in line else "executeQuery"
            stmt.fqcn = stmt.fqcn or last_fqcn
            stmt.action = stmt.action or last_action
            if not stmt.timestamp:
                stmt.timestamp = _extract_timestamp(line) or ""

    # ── Pass 2: fill in missing FQCNs from the closest InvokeDao record.
    # When a statement has no fqcn, look at the InvokeDao records on either
    # side of its init/exec lines and pick the nearest within a small window.
    if invoke_records:
        inv_lines = [r[0] for r in invoke_records]
        for stmt in by_id.values():
            if stmt.fqcn:
                continue
            anchor = stmt.init_line or stmt.exec_line
            if not anchor:
                continue
            # Binary-search-ish: find the nearest InvokeDao record by line.
            best = None
            best_dist = 1 << 30
            for ln, fq in invoke_records:
                d = abs(ln - anchor)
                if d < best_dist:
                    best_dist = d
                    best = fq
            # Cap at a reasonable window — unrelated InvokeDaos far away
            # shouldn't bleed in.
            if best and best_dist <= 30:
                stmt.fqcn = best

    return [by_id[qid] for qid in order]


def annotate_scores(
    statements: list[Statement],
    *,
    primary_packages: Iterable[str] = (),
    noise_packages: Iterable[str] = DEFAULT_NOISE_PACKAGES,
    noise_tables: Iterable[str] = DEFAULT_NOISE_TABLES,
) -> None:
    """Compute and store `.score` on each statement in place."""
    pp = tuple(primary_packages)
    np = tuple(noise_packages)
    nt = tuple(noise_tables)
    for s in statements:
        s.score = score_statement(
            s, primary_packages=pp, noise_packages=np, noise_tables=nt,
        )


def group_by_action(
    statements: list[Statement],
    *,
    fallback_gap_seconds: int = 1,
) -> list[Action]:
    """Group statements under the user request that triggered them.

    Strategy, in order of preference:
      1. Statements sharing a non-None `action` (callMethod label) and
         a contiguous timestamp window are placed under that action.
      2. Statements with no action label fall into a synthetic
         `Session @ HH:MM:SS` group keyed on a 1-second gap from the
         previous statement (`fallback_gap_seconds`).

    The result is in execution order; each Action's `statements` list
    preserves init-order from `parse_log`."""
    actions: list[Action] = []
    current: Action | None = None
    last_ts_secs: int | None = None
    last_action_label: str | None = None
    for s in statements:
        ts_secs = _ts_to_seconds(s.timestamp)
        # New explicit action label → start a new group.
        if s.action and s.action != last_action_label:
            current = Action(label=s.action, timestamp=s.timestamp)
            actions.append(current)
            last_action_label = s.action
        # Same action label → keep appending.
        elif s.action and s.action == last_action_label and current is not None:
            pass
        # No action label → fall back to time-gap grouping.
        else:
            need_new = (
                current is None
                or last_ts_secs is None
                or ts_secs is None
                or (ts_secs - last_ts_secs) > fallback_gap_seconds
            )
            if need_new:
                label = (
                    f"Session @ {s.timestamp[-8:]}"
                    if s.timestamp else "Detached statements"
                )
                current = Action(label=label, timestamp=s.timestamp)
                actions.append(current)
                last_action_label = None
        current.statements.append(s)
        last_ts_secs = ts_secs if ts_secs is not None else last_ts_secs
    return actions


# ── Back-compat wrappers ─────────────────────────────────────────────────────
def find_entry_by_id(log_text: str, query_id: str) -> dict | None:
    """Locate a single statement by its hex id and return the legacy dict
    shape kept for test/back-compat code. Internally now uses parse_log."""
    if not query_id or not log_text:
        return None
    target = query_id.strip().lower()
    for stmt in parse_log(log_text):
        if stmt.id == target:
            return stmt.as_dict() | {"result": stmt.combined_sql()}
    return None


def find_last_entry(log_text: str) -> dict | None:
    """Return the most recent statement that has BOTH an init and an
    execute line. Legacy dict shape; parse_log under the hood."""
    if not log_text:
        return None
    last: Statement | None = None
    for stmt in parse_log(log_text):
        if stmt.sql and stmt.params_raw:
            last = stmt
    if last is None:
        return None
    return last.as_dict() | {"result": last.combined_sql()}


# ── Internal scratch helpers ─────────────────────────────────────────────────
def _extract_timestamp(line: str) -> str | None:
    m = _TIMESTAMP_RE.match(line)
    return m.group(1) if m else None


def _ts_to_seconds(ts: str) -> int | None:
    """Convert `YYYY-MM-DD HH:MM:SS` to wall-clock seconds-since-epoch-ish.
    We don't need calendar accuracy — only a monotonic comparison within a
    log file. Days are counted as 86400 seconds, which is good enough for
    grouping statements within the same day or across a midnight rollover."""
    if not ts or len(ts) < 19:
        return None
    try:
        # YYYY-MM-DD HH:MM:SS
        y, mo, d = int(ts[0:4]), int(ts[5:7]), int(ts[8:10])
        h, mi, s = int(ts[11:13]), int(ts[14:16]), int(ts[17:19])
    except ValueError:
        return None
    # Approximate — "month*31" introduces a few-day jitter at month
    # boundaries, but we only compare within the same file/day so it's fine.
    return (((y * 12 + mo) * 31 + d) * 24 + h) * 3600 + mi * 60 + s


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

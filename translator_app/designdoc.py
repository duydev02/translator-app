import re
from collections.abc import Iterable

from .schema import (
    _filter_by_table_context,
    _filter_entries,
    _most_common,
)


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
    """Return a list of arg-expressions for every `.append(...)` call.
    Receiver-agnostic — kept for backwards compatibility / fallback."""
    return [arg for _recv, arg in _extract_appends_with_receiver(code)]


def _extract_appends_with_receiver(code):
    """Return [(receiver, arg)] for every `.append(...)` call.
    The receiver is the variable name that owns the call. For chained calls
    like `sb.append(x).append(y)` the second receiver is reported as the
    same name as the first, so callers can group by buffer."""
    results = []
    i, n = 0, len(code)
    last_receiver = None    # for chained .append(...).append(...)
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

        # Determine the receiver — walk backwards from the dot.
        k = idx - 1
        while k >= 0 and code[k].isspace():
            k -= 1
        receiver = ""
        if k >= 0 and code[k] == ')':
            # Chained call: previous append closed here. Reuse last receiver.
            receiver = last_receiver or ""
        elif k >= 0 and (code[k].isalnum() or code[k] == '_'):
            end_id = k + 1
            while k >= 0 and (code[k].isalnum() or code[k] == '_'):
                k -= 1
            receiver = code[k + 1:end_id]

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
            results.append((receiver, code[start:j]))
            if receiver:
                last_receiver = receiver
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


_TOSTRING_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z_0-9]*)\s*\.\s*toString\s*\(\s*\)\s*$')


def _build_sql_from_java(java_code):
    """Extract concatenated SQL with markers for embedded Java expressions.
    Returns (sql_text, expr_map, javadoc_info, func_info)."""
    javadoc = _parse_javadoc(java_code)
    func    = _parse_function_sig(java_code)
    clean   = _strip_java_comments(java_code)
    appends = _extract_appends_with_receiver(clean)

    # Group raw append args by receiver so we can splice helper StringBuffers
    # in when the main buffer does `sql.append(other.toString())`.
    by_buffer = {}
    order = []
    for recv, arg in appends:
        if not recv:
            continue
        if recv not in by_buffer:
            by_buffer[recv] = []
            order.append(recv)
        by_buffer[recv].append(arg)

    # Identify the main buffer. Tried in order:
    #   1. `return <buf>.toString()` — direct return of the buffer.
    #   2. The buffer that consumes the most other buffers via
    #      `<buf>.append(<other>.toString())` (i.e. the one doing the
    #      splicing — its content includes the helper buffers).
    #   3. Any `<buf>.toString()` call site referencing a known buffer
    #      (e.g. `dataBase.getPrepareStatement(<buf>.toString())`),
    #      excluding those nested inside .append(...).
    #   4. Fallback: buffer with the most appends.
    main_buffer = None
    rm = re.search(
        r'return\s+([A-Za-z_][A-Za-z_0-9]*)\s*\.\s*toString\s*\(',
        clean,
    )
    if rm and rm.group(1) in by_buffer:
        main_buffer = rm.group(1)
    if main_buffer is None and by_buffer:
        # Count how many other-buffer .toString() splices each buffer contains.
        consume_count = {b: 0 for b in by_buffer}
        for buf, args in by_buffer.items():
            for arg in args:
                for tok in _split_java_concat(arg):
                    m = _TOSTRING_RE.match(tok.strip())
                    if m and m.group(1) in by_buffer and m.group(1) != buf:
                        consume_count[buf] += 1
        best = max(consume_count.values()) if consume_count else 0
        if best > 0:
            main_buffer = next(b for b in order if consume_count[b] == best)
    if main_buffer is None:
        # Find any <buf>.toString() reference that isn't the argument of a
        # `.append(`. Use that buffer as the main — typical for code that
        # passes the SQL to a JDBC helper instead of returning it directly.
        for m in re.finditer(
            r'([A-Za-z_][A-Za-z_0-9]*)\s*\.\s*toString\s*\(',
            clean,
        ):
            buf = m.group(1)
            if buf not in by_buffer:
                continue
            # Walk back to see if this is inside an `.append(` argument.
            preceding = clean[:m.start()]
            in_append = re.search(
                r'\.\s*append\s*\(\s*$',
                preceding,
            )
            if not in_append:
                main_buffer = buf
                break
    if main_buffer is None or main_buffer not in by_buffer:
        main_buffer = max(order, key=lambda b: len(by_buffer[b])) if order else None

    expr_map, counter = {}, [0]
    def add(e):
        idx = counter[0]; counter[0] += 1
        expr_map[idx] = e.strip()
        return f"{_EXPR_OPEN}{idx}{_EXPR_CLOSE}"

    def _emit_buffer(buf_name, parts, visited):
        """Recursively emit appends for a buffer, inlining other-buffer
        toString() references. `visited` guards against cycles."""
        if not buf_name or buf_name in visited:
            return
        visited.add(buf_name)
        for arg in by_buffer.get(buf_name, []):
            for tok in _split_java_concat(arg):
                tok = tok.strip()
                if not tok:
                    continue
                if tok.startswith('"') and tok.endswith('"'):
                    parts.append(_parse_java_string(tok))
                    continue
                m = _TOSTRING_RE.match(tok)
                if m and m.group(1) in by_buffer:
                    # Splice the referenced buffer's contents in place.
                    _emit_buffer(m.group(1), parts, visited)
                    continue
                parts.append(add(tok))

    parts = []
    if main_buffer:
        _emit_buffer(main_buffer, parts, set())
    else:
        # Fallback: flatten everything (no receivers detected at all).
        for _recv, arg in appends:
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
    pre = text[:positions[0][0]].strip() if positions else text

    # Old-style comma-separated FROM (cross-join syntax). Split at top-level
    # commas so each table gets its own ref instead of leaking commas into
    # the alias of the first one.
    pre_pieces = _split_commas_top(pre) if pre else []
    if not pre_pieces:
        main, cross = {"table": "", "alias": ""}, []
    else:
        main = _parse_table_ref(pre_pieces[0])
        cross = [_parse_table_ref(p) for p in pre_pieces[1:]]

    if not positions:
        return {"main": main, "joins": [], "cross": cross}

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
    return {"main": main, "joins": joins, "cross": cross}


def _parse_sql(sql):
    sql = re.sub(r'/\*\+.*?\*/', ' ', sql, flags=re.DOTALL)
    sql = re.sub(r'\s+', ' ', sql).strip()
    # Peel off any outer parenthesised wrappers — a whole SELECT/UNION block
    # is sometimes built as `( ... )` (typical when used as a subquery later).
    while _is_paren_group(sql):
        sql = sql[1:-1].strip()
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
        r'INSERT\s+(?:INTO\s+)?(\S+)(?:\s+NOLOGGING)?\s*',
        sql, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return {"type": "INSERT", "raw": sql}
    result = {"type": "INSERT", "target": m.group(1), "columns": []}
    rest = sql[m.end():].lstrip()

    # Optional parenthesised chunk right after the target. It can be either:
    #   - a column list:   INSERT INTO t (a, b, c) VALUES (...)
    #   - a wrapped SELECT: INSERT INTO t (SELECT ... FROM ...)
    # We need real paren-matching here because a regex `[^)]*` chokes on
    # nested parens like NVL(col, 0).
    if rest.startswith('('):
        depth = 0; end = -1
        for i, c in enumerate(rest):
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end > 0:
            inside = rest[1:end].strip()
            after  = rest[end + 1:].strip()
            if inside.upper().lstrip().startswith('SELECT'):
                # Wrapped sub-select — the parens just group the SELECT.
                result["select"] = _parse_select(inside)
                return result
            # Column list; trailing `after` should hold VALUES/SELECT.
            result["columns"] = [c.strip() for c in _split_commas_top(inside) if c.strip()]
            rest = after

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
    # Allow an optional table alias between the target and SET, e.g.
    #   UPDATE my_table TR SET ...
    #   UPDATE my_table AS tr SET ...
    # The negative lookahead on `SET` keeps us from swallowing the keyword
    # itself when no alias is present.
    m = re.match(
        r'UPDATE\s+(\S+)(?:\s+(?:AS\s+)?(?!SET\b)(\S+))?\s+SET\s+(.*)',
        sql, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return {"type": "UPDATE", "raw": sql}
    target, alias, rest = m.group(1), m.group(2), m.group(3).strip()
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
    if alias:
        out["alias"] = alias
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
            if alias_map and left.upper() in alias_map:
                hint = alias_map[left.upper()]
                col_translated = translate_fn(right, _hint_table=hint)
                if col_translated != right:
                    return f"{_cased(left)}.{col_translated}"
                return f"{_cased(left)}.{_cased(right)}"
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

    # Match either a single-quoted SQL string literal OR an identifier
    # (optionally with a `alias.col` qualifier). String literals are
    # passed through untouched so we never alter the value of `'Active'` etc.
    pattern = re.compile(
        r"'(?:[^'\\]|\\.)*'"
        r"|\b([A-Za-z_][A-Za-z_0-9]*)(?:\.([A-Za-z_][A-Za-z_0-9]*))?\b"
    )

    def _sub_keep_strings(m):
        if m.group(0).startswith("'"):
            return m.group(0)
        return sub(m)

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
            result.append(pattern.sub(_sub_keep_strings, text[i:lit_end]))
            i = lit_end
    return "".join(result)


def _render_placeholders(text, expr_map, translate_fn=None, uppercase=False):
    """Replace \\uE001N\\uE002 markers with rendered Java expressions.
    When a marker is adjacent to a literal that contains real content, emit
    ' + ' between them so the design-doc preserves the Java concatenation
    structure. Purely structural punctuation (parens, commas, whitespace) is
    treated as part of the SQL — emitted verbatim with no ' + ' decoration."""
    if _EXPR_OPEN not in text:
        return text

    def _is_structural(s):
        # Empty or whitespace-only counts as no content.
        return all(c in "()[]{},* \t\n\r" for c in s)

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
            # Decorate with ' + ' only when the preceding literal has real,
            # non-structural content (avoids ugly `( + expr + )` for IN-lists).
            if last_kind == 'lit' and out and not _is_structural(out[-1]):
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
            if last_kind == 'expr' and lit.strip() and not _is_structural(lit):
                out.append(" + ")
                out.append(lit.strip())
            else:
                out.append(lit)
            last_kind = 'lit'
            i = lit_end
    return "".join(out).strip()


def _render_value(text, expr_map, translate_fn, uppercase=False):
    """Render a value expression: translate names AND expand placeholders.
    Identifier tokens (e.g. `sub.bumon_cd` on the right side of a comparison)
    do honour the uppercase flag — Java placeholder contents are still kept
    case-sensitive because they may be method names or class references."""
    t = _translate_in_text(text, translate_fn, uppercase=uppercase)
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
    if op == "=":
        disp = "＝"
    elif uppercase and op:
        # Normalize SQL keyword operators (IS NULL, BETWEEN, IN, LIKE, ...)
        # to upper case so the doc reads consistently.
        disp = re.sub(r'\s+', ' ', op.upper())
    else:
        disp = op
    right = _render_value(cond.get("right", ""), expr_map, translate_fn, uppercase=uppercase)

    conn = cond.get("connector", "") or ""
    if uppercase and conn:
        conn = conn.upper()
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


def _count_select_in_unions(sel):
    """Walk a SELECT/SELECT_UNION tree, returning (n_branches, list_of_select_dicts)."""
    if not sel:
        return 0, []
    if sel.get("type") == "SELECT_UNION":
        out = []
        for p in sel.get("parts", []):
            _, branch = _count_select_in_unions(p)
            out.extend(branch)
        return len(out), out
    return 1, [sel]


def _count_placeholders(parsed, expr_map):
    """Count `?` markers in a parsed statement and how many Java expression
    placeholders the source method substituted in."""
    qmark = 0
    expr_count = len(expr_map) if expr_map else 0

    def walk_value(v):
        nonlocal qmark
        if isinstance(v, str):
            qmark += sum(1 for ch in v if ch == "?")

    if parsed.get("type") == "INSERT":
        for v in parsed.get("values") or []:
            walk_value(v)
        for f in parsed.get("fields") or []:
            walk_value(f)
        if parsed.get("select"):
            for f in parsed["select"].get("fields", []) or []:
                walk_value(f)
    elif parsed.get("type") == "UPDATE":
        for a in parsed.get("set") or []:
            walk_value(a.get("value", ""))
    return qmark, expr_count


def _stat_target(parsed, expr_map):
    """Format a parsed `target` for display in stats. Resolves Java
    placeholders to `${var}` so users see the variable name instead of
    the raw `\\uE001N\\uE002` markers (which render as a stray digit)."""
    raw = parsed.get("target", "") or ""
    if not raw:
        return "(unknown)"
    def repl(m):
        idx = int(m.group(1))
        expr = (expr_map.get(idx, "") or "").strip()
        if not expr:
            return "${?}"
        if len(expr) > 60:
            expr = expr[:57] + "…"
        return "${" + expr + "}"
    return _EXPR_RE.sub(repl, raw)


def _build_stats_block(parsed, expr_map):
    """Return an indented list of one-liner stats describing the parsed
    statement. Empty list ⇒ no stats block is shown."""
    if not parsed or parsed.get("type") in (None, "UNKNOWN"):
        return []
    stype = parsed["type"]
    rows = []
    qmark, expr_count = _count_placeholders(parsed, expr_map)

    target_str = _stat_target(parsed, expr_map)

    if stype == "INSERT":
        sel = parsed.get("select")
        # Prefer the explicit (col_list) count when present — that's the
        # author-stated number of columns being inserted. Fall back to the
        # SELECT projection count or VALUES count.
        n_cols = len(parsed.get("columns") or [])
        n_vals = len(parsed.get("values") or []) or (
            len(sel.get("fields", []) or []) if sel else 0
        )
        if not n_cols:
            n_cols = n_vals
        rows.append(f"Target table: {target_str}")
        if n_cols:
            rows.append(f"Columns: {n_cols}")
        if n_vals and n_vals != n_cols:
            rows.append(f"Values: {n_vals}")
        if sel:
            rows.append("Source: SELECT subquery")
        elif parsed.get("values"):
            rows.append("Source: VALUES")

    elif stype == "UPDATE":
        rows.append(
            f"Target table: {target_str}"
            + (f"  (alias {parsed['alias']})" if parsed.get("alias") else "")
        )
        rows.append(f"SET columns: {len(parsed.get('set') or [])}")
        rows.append(f"WHERE conditions: {len(parsed.get('where') or [])}")

    elif stype == "DELETE":
        rows.append(f"Target table: {target_str}")
        rows.append(f"WHERE conditions: {len(parsed.get('where') or [])}")

    elif stype in ("SELECT", "SELECT_UNION"):
        n_branch, branches = _count_select_in_unions(parsed)
        if n_branch > 1:
            rows.append(f"UNION branches: {n_branch}")
        total_fields = 0
        total_tables = 0
        total_joins  = 0
        total_where  = 0
        total_group  = 0
        total_having = 0
        total_order  = 0
        for b in branches:
            total_fields += len(b.get("fields") or [])
            from_info = b.get("from") or {}
            if from_info.get("main"):
                total_tables += 1
            total_tables += len(from_info.get("cross") or [])
            total_joins  += len(from_info.get("joins") or [])
            total_where  += len(b.get("where") or [])
            total_group  += len(b.get("group_by") or [])
            total_having += len(b.get("having") or [])
            total_order  += len(b.get("order_by") or [])
        rows.append(f"Selected columns: {total_fields}"
                    + (" (DISTINCT)" if any(b.get("distinct") for b in branches) else ""))
        rows.append(f"Tables: {total_tables}"
                    + (f"  ·  JOINs: {total_joins}" if total_joins else ""))
        rows.append(f"WHERE: {total_where}"
                    + (f"  ·  HAVING: {total_having}" if total_having else "")
                    + (f"  ·  GROUP BY: {total_group}" if total_group else "")
                    + (f"  ·  ORDER BY: {total_order}" if total_order else ""))

    elif stype == "TRUNCATE":
        rows.append(f"Target table: {target_str}")

    if qmark or expr_count:
        bits = []
        if qmark:      bits.append(f"? binds: {qmark}")
        if expr_count: bits.append(f"Java embeds: {expr_count}")
        rows.append("  ·  ".join(bits))

    return [_TAB + r for r in rows]


_EXISTS_RE = re.compile(r'^\s*((?:NOT\s+)?EXISTS)\s*\((.*)\)\s*$', re.IGNORECASE | re.DOTALL)


def _is_paren_group(body):
    """True if the entire body is wrapped by a single matching pair of
    parentheses at the top level (so it represents a grouped sub-condition,
    not an IN-list or function call)."""
    body = body.strip()
    if not body.startswith('(') or not body.endswith(')'):
        return False
    depth = 0
    for i, c in enumerate(body):
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i == len(body) - 1
    return False


def _emit_condition_lines(cond, expr_map, translate_fn, uppercase, indent=0, flags=None):
    """Render a single condition. Returns a list of lines so EXISTS / NOT EXISTS
    subqueries and parenthesised groups (a OR b) can be expanded across multiple
    lines instead of being crammed onto one."""
    body = cond.get("raw", "") or ""
    conn = cond.get("connector", "") or ""
    ind  = _TAB * indent

    # Parenthesised group:   (a OR b)   →   (   /   a   /   OR b   /   )
    if not cond.get("op") and _is_paren_group(body):
        inner = body.strip()[1:-1].strip()
        sub_conds = _parse_conditions(inner)
        if sub_conds:
            out = [ind + ((conn + " ") if conn else "") + "("]
            for sc in sub_conds:
                out.extend(_emit_condition_lines(
                    sc, expr_map, translate_fn, uppercase,
                    indent=indent + 1, flags=flags,
                ))
            out.append(ind + ")")
            return out

    # EXISTS / NOT EXISTS subquery
    m = _EXISTS_RE.match(body)
    if m and not cond.get("op"):
        kw = m.group(1).upper().replace("  ", " ")
        inner = m.group(2).strip()
        try:
            sub = _parse_sql(inner)
        except Exception:
            sub = None
        if sub and sub.get("type") == "SELECT":
            header = (conn + " " if conn else "") + kw + " ("
            out = [ind + header]
            sub_flags = dict(flags or {})
            # Inside an EXISTS we don't want the "■処理区分 SELECT" header
            # to repeat — the EXISTS keyword already scopes the subquery.
            sub_flags["show_stype"] = False
            out.extend(_emit_select_or_union(
                sub, expr_map, translate_fn, uppercase,
                indent=indent + 1, flags=sub_flags,
            ))
            out.append(ind + ")")
            return out

    # Default — single line
    return [ind + _emit_condition_line(cond, expr_map, translate_fn, uppercase).lstrip()]


def _emit_update(parsed, expr_map, translate_fn, uppercase, lines, flags=None):
    flags = flags or {}
    def _emit_target():
        if not flags.get("show_target", True):
            return
        lines.append("■更新テーブル")
        target_str = _render_target(parsed.get("target", ""), expr_map, translate_fn, uppercase)
        alias = parsed.get("alias")
        if alias:
            # Show the alias so readers can map TR.xxx references in WHERE
            # back to this table.
            shown_alias = alias.upper() if uppercase else alias
            target_str = f"{target_str}  （別名：{shown_alias}）"
        lines.append(_TAB + target_str)
        lines.append("")

    def _emit_set():
        if not flags.get("show_projection", True):
            return
        lines.append("■更新項目")
        lines.append(_TAB + "カラム名" + _COL_TABS + "セット内容")
        for a in parsed.get("set", []):
            col = _render_name(a["col"], translate_fn, uppercase)
            val = _render_value(a["value"], expr_map, translate_fn, uppercase=uppercase)
            lines.append(_TAB + col + _COL_TABS + val)
        lines.append("")

    def _emit_where():
        if not flags.get("show_where", True) or not parsed.get("where"):
            return
        lines.append("■抽出条件")
        for c in parsed["where"]:
            lines.extend(_emit_condition_lines(c, expr_map, translate_fn, uppercase, indent=1, flags=flags))
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
            lines.extend(_emit_condition_lines(c, expr_map, translate_fn, uppercase, indent=1, flags=flags))
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
        def _alias_str(a):
            if not a:
                return ""
            return a.upper() if uppercase else a
        if ref.get("subquery"):
            alias = _alias_str(ref.get("alias", ""))
            out.append(ind + _TAB + "(")
            out.extend(_emit_select_or_union(
                ref["subquery"], expr_map, translate_fn, uppercase,
                indent=indent + 2, flags=flags,
            ))
            out.append(ind + _TAB + ")" + (_COL_TABS + alias if alias else ""))
        else:
            tbl = _render_name(ref.get("table", ""), translate_fn, uppercase, expr_map)
            alias = _alias_str(ref.get("alias", ""))
            out.append(ind + _TAB + tbl + (_COL_TABS + alias if alias else ""))

    def _emit_projection():
        if not flags.get("show_projection", True):
            return
        out.append(ind + "■抽出項目" + distinct_str)
        for f in parsed.get("fields", []):
            out.append(ind + _TAB + _render_value(f, expr_map, translate_fn, uppercase=uppercase))
        out.append("")

    def _emit_from_and_join():
        from_info = parsed.get("from")
        if not (from_info and from_info.get("main")):
            return
        if flags.get("show_from", True):
            out.append(ind + "■抽出テーブル")
            _emit_table_ref_item(from_info["main"])
            # Old-style comma-separated FROM tables (cross-join syntax).
            for ref in from_info.get("cross", []) or []:
                _emit_table_ref_item(ref)
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
                out.extend(_emit_condition_lines(
                    c, expr_map, translate_fn, uppercase,
                    indent=indent + 1, flags=flags))
            out.append("")

    def _emit_group():
        if flags.get("show_group", True) and parsed.get("group_by"):
            out.append(ind + "■グループ化条件")
            for g in parsed["group_by"]:
                out.append(ind + _TAB + _render_value(g, expr_map, translate_fn, uppercase=uppercase))
            out.append("")

    def _emit_having():
        if flags.get("show_having", True) and parsed.get("having"):
            out.append(ind + "■集計後抽出条件")
            for c in parsed["having"]:
                out.extend(_emit_condition_lines(
                    c, expr_map, translate_fn, uppercase,
                    indent=indent + 1, flags=flags))
            out.append("")

    def _emit_order():
        if flags.get("show_order", True) and parsed.get("order_by"):
            out.append(ind + "■並び順")
            for o in parsed["order_by"]:
                out.append(ind + _TAB + _render_value(o, expr_map, translate_fn, uppercase=uppercase))
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
                             _render_value(f, expr_map, translate_fn, uppercase=uppercase))
        elif values:
            for i, v in enumerate(values):
                lines.append(_TAB + col_label(i, v) + _COL_TABS +
                             _render_value(v, expr_map, translate_fn, uppercase=uppercase))
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
            aliases[main["alias"].upper()] = main["table"].upper()
        if main.get("subquery"):
            walk(main["subquery"])
        for j in fi.get("joins", []):
            ref = j.get("table_ref", {}) or {}
            if ref.get("alias") and ref.get("table"):
                aliases[ref["alias"].upper()] = ref["table"].upper()
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
            up = tok.upper()
            if up in table_index:
                ctx.add(up)

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


# SQL clause keywords that should start a new line. Order matters — longer
# multi-word forms must be tried before their shorter prefixes (so
# `INSERT INTO` is matched as a unit before `INSERT`).
_PRETTY_NEWLINE_KWS = sorted([
    "SELECT", "INSERT INTO", "INSERT", "UPDATE", "DELETE FROM", "DELETE",
    "TRUNCATE TABLE", "TRUNCATE",
    "SET", "VALUES", "FROM", "WHERE",
    "GROUP BY", "HAVING", "ORDER BY",
    "UNION ALL", "UNION",
    "INNER JOIN", "LEFT OUTER JOIN", "LEFT JOIN",
    "RIGHT OUTER JOIN", "RIGHT JOIN",
    "FULL OUTER JOIN", "FULL JOIN", "CROSS JOIN", "JOIN",
    "ON",
], key=len, reverse=True)
# Connectors that get a new line under the parent clause (extra indent).
_PRETTY_CONJ_KWS = ["AND", "OR"]
# Right-align width (the longest common keyword we care about — e.g. "UPDATE",
# "VALUES", "WHERE"). Wider keywords like "GROUP BY" / "UNION ALL" simply
# overflow but stay readable.
_PRETTY_WIDTH = 6


def _pretty_sql(sql: str) -> str:
    """Reformat a one-line SQL into a multi-line, right-aligned block.

    Aware of single-quoted strings, ${placeholder} markers, and parens, so we
    only break on KEYWORDS at the top nesting level. Subqueries on a clause's
    right-hand side are left intact (we don't recurse) — that keeps the
    output predictable for messy generated SQL.
    """
    if not sql:
        return sql
    s = re.sub(r"\s+", " ", sql).strip()
    n = len(s)

    # Walk the string, finding break points (keyword spans at depth 0,
    # outside strings and ${...} placeholders).
    breaks = []   # list of (start, end, kw_upper)
    i = 0
    depth = 0
    in_str = False
    in_ph  = False

    def _kw_match_at(pos):
        # Must be at a word boundary (start of buffer or non-word char before).
        if pos and (s[pos - 1].isalnum() or s[pos - 1] == "_"):
            return None
        upper_s = s[pos:].upper()
        for kw in _PRETTY_NEWLINE_KWS + _PRETTY_CONJ_KWS:
            if upper_s.startswith(kw):
                end = pos + len(kw)
                if end < n and (s[end].isalnum() or s[end] == "_"):
                    continue   # not a real keyword boundary
                # multi-word keywords use exactly one space between tokens —
                # we already collapsed whitespace, so this just works.
                return (pos, end, kw)
        return None

    while i < n:
        c = s[i]
        if in_str:
            if c == "\\" and i + 1 < n:
                i += 2; continue
            if c == "'":
                in_str = False
            i += 1; continue
        if in_ph:
            if c == "}":
                in_ph = False
            i += 1; continue
        if c == "'":
            in_str = True; i += 1; continue
        if c == "$" and i + 1 < n and s[i + 1] == "{":
            in_ph = True; i += 2; continue
        if c == "(":
            depth += 1; i += 1; continue
        if c == ")":
            depth -= 1; i += 1; continue
        if depth == 0:
            m = _kw_match_at(i)
            if m is not None:
                breaks.append(m)
                i = m[1]
                continue
        i += 1

    if not breaks:
        return s

    # Build (kw, body) pairs.
    pairs = []
    cursor = 0
    if breaks[0][0] > 0:
        head = s[:breaks[0][0]].strip()
        if head:
            pairs.append((None, head))
    for idx, (start, end, kw) in enumerate(breaks):
        next_start = breaks[idx + 1][0] if idx + 1 < len(breaks) else n
        body = s[end:next_start].strip()
        pairs.append((kw, body))
        cursor = next_start

    # Render: clause keywords right-aligned to _PRETTY_WIDTH, AND/OR indented
    # one level further so they sit visually under WHERE/ON content.
    lines = []
    for kw, body in pairs:
        if kw is None:
            lines.append(body)
            continue
        # All keywords share the same right-aligned column so they line up
        # vertically (UPDATE, SET, WHERE, AND, OR, …). Wider keywords like
        # GROUP BY simply overflow but stay readable.
        prefix = kw.rjust(_PRETTY_WIDTH)
        lines.append(prefix + (" " + body if body else ""))
    return "\n".join(lines)


def _collect_columns_referenced(parsed):
    """Walk a parsed statement and return a list of (phys_col, context) where
    context is a short human-readable string explaining where the column was
    seen (e.g. "INSERT col 1", "UPDATE SET", "WHERE", "SELECT").
    For sub-selects nested via JOIN/EXISTS we recurse but tag with prefix."""
    out = []

    def add_col(name, ctx):
        if not name:
            return
        # Strip alias prefix and bracketed function calls
        n = name.strip()
        # `tbl.col` → take the trailing identifier
        if "." in n and n.split(".")[-1].replace("_", "").isalnum():
            n = n.split(".")[-1]
        # Skip things that don't look like a bare identifier
        if not re.match(r"^[A-Za-z_][A-Za-z_0-9]*$", n):
            return
        out.append((n, ctx))

    def walk_conds(conds, ctx):
        for c in conds or []:
            add_col(c.get("left", ""), ctx)
            # right may also reference a column (alias.col on the RHS)
            r = (c.get("right") or "").strip()
            if r and re.match(r"^[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?$", r):
                add_col(r, ctx)

    def walk_select(p, prefix=""):
        if not p:
            return
        if p.get("type") == "SELECT_UNION":
            for i, b in enumerate(p.get("parts", [])):
                walk_select(b, f"{prefix}UNION#{i+1} ")
            return
        for f in p.get("fields", []) or []:
            add_col(f, f"{prefix}SELECT")
        from_info = p.get("from") or {}
        for j in from_info.get("joins", []) or []:
            walk_conds(j.get("on"), f"{prefix}JOIN ON")
            ref = j.get("table_ref") or {}
            if ref.get("subquery"):
                walk_select(ref["subquery"], f"{prefix}JOIN-sub ")
        walk_conds(p.get("where"), f"{prefix}WHERE")
        walk_conds(p.get("having"), f"{prefix}HAVING")
        for g in p.get("group_by") or []:
            add_col(g, f"{prefix}GROUP BY")
        for o in p.get("order_by") or []:
            add_col(o, f"{prefix}ORDER BY")

    t = parsed.get("type")
    if t == "INSERT":
        for i, c in enumerate(parsed.get("columns") or []):
            add_col(c, f"INSERT col {i+1}")
        if parsed.get("select"):
            walk_select(parsed["select"])
    elif t == "UPDATE":
        for a in parsed.get("set") or []:
            add_col(a.get("col", ""), "UPDATE SET")
        walk_conds(parsed.get("where"), "UPDATE WHERE")
    elif t == "DELETE":
        walk_conds(parsed.get("where"), "DELETE WHERE")
    elif t in ("SELECT", "SELECT_UNION"):
        walk_select(parsed)

    # De-duplicate while preserving order
    seen = {}
    for name, ctx in out:
        seen.setdefault(name, []).append(ctx)
    return [(n, sorted(set(ctxs))) for n, ctxs in seen.items()]


def compute_design_details(
    java_code: str,
    table_index: dict,
    column_index: dict,
    rev_table_index: dict | None = None,
    rev_column_index: dict | None = None,
    schemas=None,
    tables=None,
):
    """Build a deep-dive view of the parsed Java SQL. Returns a dict with
    everything an Inspect window needs to render. Best-effort — failures
    yield an empty/partial dict rather than raising."""
    out = {
        "ok": False,
        "stype": "",
        "stats": [],
        "warnings": [],
        "buffers": [],
        "java_placeholders": [],
        "bind_positions": [],
        "column_lineage": [],
        "ambiguous": [],
        "unknown_tokens": [],
        "reconstructed_sql": "",
    }
    try:
        sql, expr_map, _javadoc, _func = _build_sql_from_java(java_code)
    except Exception as e:
        out["warnings"].append(f"Java parse error: {e}")
        return out
    if not sql.strip():
        out["warnings"].append("No SQL found — did the method use sb.append(...)?")
        return out

    parsed = _parse_sql(sql)
    out["ok"] = True
    out["stype"] = parsed.get("type", "")
    out["stats"] = [s.lstrip("\t ") for s in _build_stats_block(parsed, expr_map)]

    # ── Reconstructed SQL — substitute <EXPR_OPEN>N<EXPR_CLOSE> with ${expr}
    def _reconstruct(text):
        if not text:
            return ""
        def repl(m):
            idx = int(m.group(1))
            expr = (expr_map.get(idx, "") or "").strip()
            # Keep it readable; truncate very long expressions.
            if len(expr) > 60:
                expr = expr[:57] + "…"
            return "${" + expr + "}"
        return re.sub(
            re.escape(_EXPR_OPEN) + r"(\d+)" + re.escape(_EXPR_CLOSE),
            repl,
            text,
        )
    out["reconstructed_sql"] = _pretty_sql(_reconstruct(sql))

    # ── Buffers
    appends = _extract_appends_with_receiver(_strip_java_comments(java_code))
    by_buffer = {}
    order = []
    for recv, _arg in appends:
        if not recv:
            continue
        if recv not in by_buffer:
            by_buffer[recv] = 0
            order.append(recv)
        by_buffer[recv] += 1
    if by_buffer:
        # Reuse the same heuristic as _build_sql_from_java to label "main"
        rm = re.search(
            r'return\s+([A-Za-z_][A-Za-z_0-9]*)\s*\.\s*toString\s*\(',
            _strip_java_comments(java_code),
        )
        main = rm.group(1) if rm and rm.group(1) in by_buffer else None
        if main is None:
            consume = {b: 0 for b in by_buffer}
            for buf, ents in by_buffer.items():
                pass  # consume calc requires full args; skip — just heuristic
            # fall back: most appends
            main = max(order, key=lambda b: by_buffer[b])
        for b in order:
            out["buffers"].append({
                "name": b,
                "appends": by_buffer[b],
                "is_main": (b == main),
            })

    # ── Java placeholders — id, expr, rendered, occurrences
    if expr_map:
        sql_with_markers = sql
        for idx in sorted(expr_map):
            marker = f"{_EXPR_OPEN}{idx}{_EXPR_CLOSE}"
            count = sql_with_markers.count(marker)
            expr = (expr_map[idx] or "").strip()
            rendered = _render_expr(expr)
            out["java_placeholders"].append({
                "id": idx,
                "expr": expr,
                "rendered": rendered,
                "occurrences": count,
            })

    # ── ? bind positions
    bind_no = 0
    def add_bind(context, value):
        nonlocal bind_no
        for ch in value or "":
            if ch == "?":
                bind_no += 1
                out["bind_positions"].append({"index": bind_no, "context": context})

    if parsed.get("type") == "INSERT":
        cols = parsed.get("columns") or []
        vals = parsed.get("values") or []
        if vals:
            for i, v in enumerate(vals):
                col = cols[i] if i < len(cols) else f"col {i+1}"
                add_bind(f"INSERT → {col}", v)
        elif parsed.get("select"):
            for f in (parsed["select"].get("fields") or []):
                add_bind("INSERT ← SELECT", f)
    elif parsed.get("type") == "UPDATE":
        for a in parsed.get("set") or []:
            add_bind(f"SET {a.get('col','')}", a.get("value", ""))
        for c in parsed.get("where") or []:
            add_bind(f"WHERE {c.get('left','')}", c.get("right", ""))
    elif parsed.get("type") == "DELETE":
        for c in parsed.get("where") or []:
            add_bind(f"WHERE {c.get('left','')}", c.get("right", ""))

    # ── Column lineage / ambiguity / unknowns
    cols_seen = _collect_columns_referenced(parsed)
    for phys, ctxs in cols_seen:
        key = phys.upper()
        if key in column_index:
            entries = _filter_entries(column_index[key], schemas=schemas, tables=tables)
            if not entries:
                continue
            chosen = _most_common(key, entries)
            grouped = {}
            for sc, pt, lt, lc in entries:
                grouped.setdefault(lc, []).append((sc, pt, lt))
            ambiguous = len(grouped) > 1
            row = {
                "phys": phys,
                "logical": chosen,
                "context": ", ".join(ctxs),
                "ambiguous": ambiguous,
                "groups": [
                    {"logical": lg, "tables": rows}
                    for lg, rows in sorted(grouped.items(), key=lambda g: -len(g[1]))
                ],
            }
            out["column_lineage"].append(row)
            if ambiguous:
                out["ambiguous"].append(row)
        elif key in (table_index or {}):
            # Tables get listed too — separate context
            pass
        else:
            out["unknown_tokens"].append(phys)

    # De-dupe unknowns
    out["unknown_tokens"] = sorted(set(out["unknown_tokens"]))

    # ── Validation warnings
    if parsed.get("type") == "INSERT":
        cols = parsed.get("columns") or []
        vals = parsed.get("values") or []
        sel = parsed.get("select") or {}
        sel_fields = sel.get("fields") or [] if sel else []
        if cols and vals and len(cols) != len(vals):
            out["warnings"].append(
                f"INSERT column count ({len(cols)}) ≠ VALUES count ({len(vals)})"
            )
        if cols and sel_fields and len(cols) != len(sel_fields):
            out["warnings"].append(
                f"INSERT column count ({len(cols)}) ≠ SELECT field count ({len(sel_fields)})"
            )

    # Alias-vs-FROM consistency: scan WHERE/JOIN for `alias.col` where alias
    # isn't bound by any FROM/JOIN/cross/sub-select.
    alias_map = _build_alias_map(parsed) or {}
    bound_aliases = {a.upper() for a in alias_map}
    referenced = set()
    def scan_conds(conds):
        for c in conds or []:
            for side in (c.get("left", ""), c.get("right", "")):
                m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)\.[A-Za-z_]", side or "")
                if m:
                    referenced.add(m.group(1).upper())
    def scan_select(p):
        if not p: return
        if p.get("type") == "SELECT_UNION":
            for b in p.get("parts", []): scan_select(b)
            return
        scan_conds(p.get("where"))
        scan_conds(p.get("having"))
        for j in (p.get("from") or {}).get("joins") or []:
            scan_conds(j.get("on"))
            if (j.get("table_ref") or {}).get("subquery"):
                scan_select(j["table_ref"]["subquery"])
    if parsed.get("type") == "UPDATE":
        scan_conds(parsed.get("where"))
        if parsed.get("alias"):
            bound_aliases.add(parsed["alias"].upper())
    elif parsed.get("type") == "DELETE":
        scan_conds(parsed.get("where"))
    elif parsed.get("type") in ("SELECT", "SELECT_UNION"):
        scan_select(parsed)
    dangling = referenced - bound_aliases
    # Filter out what looks like a real table name (might be schema-qualified)
    dangling = {a for a in dangling if a.lower() not in (table_index or {}) and a not in (table_index or {})}
    for a in sorted(dangling):
        out["warnings"].append(f"Reference to '{a}.x' but no FROM/JOIN/UPDATE binds alias '{a}'")

    return out


def compute_design_stats(java_code: str) -> list:
    """Parse `java_code` enough to produce the same per-mode statistics that
    used to live in the ■SQL概要 block. Returns a list of plain strings —
    each one a stat line, no leading whitespace — or an empty list if the
    SQL couldn't be parsed. Intended for UI display alongside (not inside)
    the design doc text."""
    try:
        sql, expr_map, _javadoc, _func = _build_sql_from_java(java_code)
    except Exception:
        return []
    if not sql.strip():
        return []
    parsed = _parse_sql(sql)
    return [s.lstrip("\t ") for s in _build_stats_block(parsed, expr_map)]


def java_to_design_doc(
    java_code: str,
    table_index: dict,
    column_index: dict,
    rev_table_index: dict,
    rev_column_index: dict,
    schemas: Iterable[str] | None = None,
    tables: Iterable[str] | None = None,
    uppercase: bool = False,
    direction: str = "forward",
    show_overview: bool = True,
    show_sql_logical: bool = True,
    show_sql_physical: bool = True,
    show_stype: bool = True,
    show_target: bool = True,
    show_projection: bool = True,
    show_from: bool = True,
    show_join: bool = True,
    show_where: bool = True,
    show_group: bool = True,
    show_having: bool = True,
    show_order: bool = True,
    show_footer: bool = True,
    show_stats: bool = True,
) -> str:
    """Top-level entry point: Java method text → design-doc string."""
    try:
        sql, expr_map, javadoc, func = _build_sql_from_java(java_code)
    except Exception as e:
        return (
            "⚠  Could not parse the Java input.\n\n"
            f"Reason : {e}\n\n"
            "Tips:\n"
            "  • Make sure you pasted the FULL method including its braces.\n"
            "  • The translator looks for `<buffer>.append(\"...\")` calls and\n"
            "    `return <buffer>.toString();`. Builder calls outside of a\n"
            "    method body (or wrapped in odd helpers) may not be detected.\n"
            "  • If the method uses an unusual SQL builder API (not\n"
            "    StringBuffer/StringBuilder), Inline Replace mode might be\n"
            "    a better fit."
        )

    if not sql.strip():
        return (
            "⚠  No SQL found.\n\n"
            "The translator extracts SQL from `<buffer>.append(\"...\")` calls.\n"
            "If your method builds its query a different way, paste the SQL\n"
            "directly and switch to Inline Replace or Translation Table mode."
        )

    parsed = _parse_sql(sql)
    if parsed.get("type") == "UNKNOWN":
        snippet = sql.strip()
        if len(snippet) > 320:
            snippet = snippet[:317] + "…"
        return (
            "⚠  Unknown SQL statement type.\n\n"
            "The Design Doc generator handles SELECT / INSERT / UPDATE /\n"
            "DELETE / TRUNCATE (with UNION, subqueries, JOINs). If you're\n"
            "trying to translate a stored-procedure call or DDL, switch to\n"
            "Inline Replace mode to see term-by-term replacements.\n\n"
            "Extracted SQL preview:\n"
            f"{snippet}"
        )

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
            # Schema keys are uppercase; physical identifiers in source code
            # may be written lowercase. Normalize for lookup only.
            key = name.upper()
            hint = _hint_table.upper() if _hint_table else _hint_table
            if key in table_index:
                e = _filter_entries(table_index[key], schemas=schemas, has_phys_table=False)
                if e:
                    return _most_common(key, e)
            if key in column_index:
                e = _filter_entries(column_index[key], schemas=schemas, tables=tables)
                if hint:
                    e = _scope_by_hint(e, hint)
                else:
                    e = _filter_by_table_context(e, ctx)
                if e:
                    return _most_common(key, e)
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
        # SELECT_UNION is an internal classification; users see plain SELECT.
        lines.append(_TAB + ("SELECT" if stype == "SELECT_UNION" else stype))
        lines.append("")

    # NOTE: the per-mode statistics block (■SQL概要) used to be emitted here.
    # It moved to a non-copyable header label in the UI so users don't end up
    # pasting summary lines into their design docs by accident. Use
    # compute_design_stats() to get the same data out-of-band.

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
        # The top-level "■処理区分 SELECT" header was already emitted above,
        # so suppress the duplicate one inside _emit_select_block.
        sel_flags = dict(flags); sel_flags["show_stype"] = False
        lines.extend(_emit_select_or_union(parsed, expr_map, translate_fn, uppercase, indent=0, flags=sel_flags))
    elif stype == "TRUNCATE":
        if show_target:
            lines.append("■対象テーブル")
            lines.append(_TAB + _render_target(parsed.get("target", ""), expr_map, translate_fn, uppercase))
            lines.append("")

    _CURRENT_ALIAS_MAP = {}   # clear module-level state
    return "\n".join(lines)


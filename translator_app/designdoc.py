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
            if alias_map and left.upper() in alias_map:
                hint = alias_map[left.upper()]
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
) -> str:
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


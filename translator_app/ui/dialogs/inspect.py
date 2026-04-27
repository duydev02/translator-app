"""Inspect SQL dialog — deep-dive view of a Design Doc translation.

Layout:
    ┌─ Inspect SQL ────────────────────────────────────────────────────┐
    │  ┌─ summary cards ──────────────────────────────────────────┐    │
    │  │ INSERT  │  R_FOO  │  24 cols │ 16 binds │ 0 warnings ✓   │    │
    │  └──────────────────────────────────────────────────────────┘    │
    │  [Overview] [? Binds 16] [Tokens 24·0·0] [Java vars 5] [Buffers] [SQL]
    │  [search: ___________________________]    [↻ Refresh]            │
    │  ┌─ table / content ──────────────────────────────────────┐      │
    │  │  ...                                                    │      │
    │  └─────────────────────────────────────────────────────────┘      │
    │                                              [Refresh] [Close]   │
    └──────────────────────────────────────────────────────────────────┘

Design notes:
- Cards sit above the notebook so the most important facts stay visible
  no matter which tab is active.
- Tables (`Treeview`s) gain a per-tab search box and sortable columns.
  Double-click a row → primary value copied to clipboard.
- The Reconstructed SQL tab syntax-highlights clauses so it reads like a
  proper SQL editor.
- Refresh re-parses the current input in place and rebuilds every tab,
  so users don't have to close the dialog when they tweak the input.
"""

import re
import tkinter as tk
from tkinter import ttk

from ...themes import THEMES
from ...designdoc import compute_design_details


# ── Public entry point ──────────────────────────────────────────────────────
def open_inspect_dialog(app):
    existing = getattr(app, "_inspect_dialog", None)
    if existing and existing.winfo_exists():
        existing.lift()
        existing.focus_force()
        return

    text = app._current_input()
    if not text or not text.strip():
        app._toast.show("No input to inspect", 1100, "info")
        return

    dlg = _InspectDialog(app, text)
    app._inspect_dialog = dlg.win
    def _on_destroy(_e=None):
        if getattr(app, "_inspect_dialog", None) is dlg.win:
            app._inspect_dialog = None
    dlg.win.bind("<Destroy>", _on_destroy)


# ── Dialog ───────────────────────────────────────────────────────────────────
class _InspectDialog:
    def __init__(self, app, text):
        self.app = app
        self.text = text
        self.t = THEMES[app._theme]

        win = tk.Toplevel(app)
        self.win = win
        win.title("Inspect SQL")
        win.geometry("1080x720")
        win.configure(bg=self.t["bg"])
        win.transient(app)

        # ── Summary cards row
        self.cards_frame = tk.Frame(win, bg=self.t["bg"])
        self.cards_frame.pack(fill="x", padx=14, pady=(12, 6))

        # ── Notebook
        self._configure_styles()
        self.nb = ttk.Notebook(win, style="Inspect.TNotebook")
        self.nb.pack(fill="both", expand=True, padx=14, pady=(2, 6))

        # ── Footer
        footer = tk.Frame(win, bg=self.t["bg"])
        footer.pack(fill="x", padx=14, pady=(0, 12))
        self._mk_btn(footer, "↻  Refresh", self.refresh).pack(side="left")
        self._mk_btn(footer, "Close", win.destroy, accent=True).pack(side="right")

        # Build tab containers (we recreate their content on every refresh)
        self.tabs = {
            "overview":  tk.Frame(self.nb, bg=self.t["bg"]),
            "binds":     tk.Frame(self.nb, bg=self.t["bg"]),
            "tokens":    tk.Frame(self.nb, bg=self.t["bg"]),
            "java":      tk.Frame(self.nb, bg=self.t["bg"]),
            "buffers":   tk.Frame(self.nb, bg=self.t["bg"]),
            "sql":       tk.Frame(self.nb, bg=self.t["bg"]),
        }
        for f in self.tabs.values():
            self.nb.add(f, text="")  # text set during refresh

        # Keyboard shortcuts: Ctrl+1..6 → tab N, Esc → close
        for i in range(1, 7):
            win.bind(f"<Control-Key-{i}>",
                     lambda e, idx=i-1: self.nb.select(idx))
            win.bind(f"<Command-Key-{i}>",
                     lambda e, idx=i-1: self.nb.select(idx))
        win.bind("<Escape>", lambda e: win.destroy())

        self.refresh()

    # ── Styling
    def _configure_styles(self):
        t = self.t
        s = ttk.Style()
        s.configure(
            "Inspect.Treeview",
            background=t["surface"], fieldbackground=t["surface"],
            foreground=t["fg"], bordercolor=t["bg"], borderwidth=0, rowheight=26,
        )
        s.configure(
            "Inspect.Treeview.Heading",
            background=t["muted_bg"], foreground=t["fg"], relief="flat",
        )
        s.map("Inspect.Treeview",
              background=[("selected", t["accent"])],
              foreground=[("selected", t["accent_fg"])])
        s.configure("Inspect.TNotebook",        background=t["bg"], borderwidth=0)
        s.configure("Inspect.TNotebook.Tab",
                    padding=[14, 6], background=t["muted_bg"], foreground=t["fg"])
        s.map("Inspect.TNotebook.Tab",
              background=[("selected", t["accent"]), ("active", t["surface"])],
              foreground=[("selected", t["accent_fg"]), ("active", t["fg"])])

    def _mk_btn(self, parent, text, command, accent=False):
        t = self.t
        return tk.Button(
            parent, text=text, font=self.app._ui_b,
            bg=t["accent"]    if accent else t["muted_bg"],
            fg=t["accent_fg"] if accent else t["muted_fg"],
            activebackground=t["accent"]    if accent else t["muted_bg"],
            activeforeground=t["accent_fg"] if accent else t["muted_fg"],
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
            command=command,
        )

    # ── Refresh: re-parse input and rebuild every tab
    def refresh(self):
        try:
            details = compute_design_details(
                self.app._current_input() or self.text,
                self.app.table_index, self.app.column_index,
                self.app.rev_table_index, self.app.rev_column_index,
                schemas=(self.app._filter_schemas or None),
                tables =(self.app._filter_tables  or None),
            )
        except Exception as e:
            details = {
                "ok": False, "stype": "", "stats": [], "warnings": [str(e)],
                "buffers": [], "java_placeholders": [], "bind_positions": [],
                "column_lineage": [], "ambiguous": [], "unknown_tokens": [],
                "reconstructed_sql": "",
            }
        self.details = details

        self._render_cards(details)

        # Update tab labels with counts
        n_binds = len(details.get("bind_positions") or [])
        n_lin   = len(details.get("column_lineage")  or [])
        n_amb   = len(details.get("ambiguous")       or [])
        n_unk   = len(details.get("unknown_tokens")  or [])
        n_java  = len(details.get("java_placeholders") or [])
        n_bufs  = len(details.get("buffers")         or [])
        n_warn  = len(details.get("warnings")        or [])

        self.nb.tab(self.tabs["overview"], text=f"Overview" + (f"  ⚠{n_warn}" if n_warn else ""))
        self.nb.tab(self.tabs["binds"],    text=f"? Binds  ({n_binds})")
        self.nb.tab(self.tabs["tokens"],   text=f"Tokens  ({n_lin}·{n_amb}·{n_unk})")
        self.nb.tab(self.tabs["java"],     text=f"Java vars  ({n_java})")
        self.nb.tab(self.tabs["buffers"],  text=f"Buffers  ({n_bufs})")
        self.nb.tab(self.tabs["sql"],      text="Reconstructed SQL")

        # Rebuild each tab content
        self._rebuild_overview(details)
        self._rebuild_binds(details)
        self._rebuild_tokens(details)
        self._rebuild_java(details)
        self._rebuild_buffers(details)
        self._rebuild_sql(details)

    # ── Header cards
    def _render_cards(self, details):
        for w in self.cards_frame.winfo_children():
            w.destroy()

        cards = self._summary_cards(details)
        for i, (label, value, kind) in enumerate(cards):
            self._draw_card(self.cards_frame, label, value, kind, col=i, total=len(cards))

    def _summary_cards(self, details):
        """Return [(label, value, kind)] — `kind` is 'info' / 'success' / 'warning' / 'danger'."""
        stype = details.get("stype") or "—"
        n_binds = len(details.get("bind_positions") or [])
        n_warn  = len(details.get("warnings") or [])
        n_amb   = len(details.get("ambiguous") or [])
        n_unk   = len(details.get("unknown_tokens") or [])

        target = self._infer_target(details)
        col_count = self._infer_main_col_count(details)

        cards = [
            ("Statement", stype, "info"),
            ("Target",    target or "—", "info"),
        ]
        if col_count is not None:
            cards.append(("Columns", str(col_count), "info"))
        cards.append(("? binds", str(n_binds), "info" if n_binds else "muted"))
        if n_warn:
            cards.append(("Warnings", str(n_warn), "danger"))
        else:
            cards.append(("Warnings", "0  ✓", "success"))
        if n_amb:
            cards.append(("Ambiguous", str(n_amb), "warning"))
        if n_unk:
            cards.append(("Unknown", str(n_unk), "warning"))
        return cards

    def _infer_target(self, details):
        # Look at stats lines for "Target table: …"
        for s in (details.get("stats") or []):
            m = re.match(r"\s*Target table:\s*(\S.*?)(?:\s*\(.*\))?\s*$", s)
            if m:
                return m.group(1)
        return None

    def _infer_main_col_count(self, details):
        # For INSERT/UPDATE we want column count; for SELECT, projection count.
        for s in (details.get("stats") or []):
            for label in ("Columns:", "SET columns:", "Selected columns:"):
                if s.startswith(label):
                    rest = s[len(label):].strip().split()
                    if rest and rest[0].isdigit():
                        return int(rest[0])
        return None

    def _draw_card(self, parent, label, value, kind, col, total):
        t = self.t
        kind_colors = {
            "info":    (t["accent"],   t["accent_fg"]),
            "success": (t["success"],  t["bg"]),
            "warning": (t["warning"],  t["bg"]),
            "danger":  (t["danger"],   t["bg"]),
            "muted":   (t["muted_bg"], t["muted_fg"]),
        }
        bg, fg = kind_colors.get(kind, kind_colors["info"])
        card = tk.Frame(parent, bg=bg, padx=14, pady=8)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0))
        parent.columnconfigure(col, weight=1, uniform="cards")
        tk.Label(card, text=label, font=self.app._small,
                 bg=bg, fg=fg).pack(anchor="w")
        # Big value font
        tk.Label(card, text=value,
                 font=("Segoe UI", 16, "bold"),
                 bg=bg, fg=fg).pack(anchor="w", pady=(2, 0))

    # ── Tab: Overview
    def _rebuild_overview(self, details):
        for w in self.tabs["overview"].winfo_children():
            w.destroy()
        f = self.tabs["overview"]
        t = self.t
        # Stats list
        tk.Label(f, text="Statistics", font=self.app._ui_b,
                 bg=t["bg"], fg=t["fg"], anchor="w").pack(fill="x", padx=8, pady=(8, 4))
        stats = details.get("stats") or []
        s_frame, s_txt = self._scrollable_text(f, height=8)
        s_frame.pack(fill="x", padx=8, pady=(0, 8))
        s_txt.configure(state="normal")
        s_txt.delete("1.0", tk.END)
        if stats:
            s_txt.insert("1.0", "\n".join(f"• {s}" for s in stats))
        else:
            s_txt.insert("1.0", "(no statistics — parsing failed)")
        s_txt.configure(state="disabled")

        # Warnings (color-coded)
        warnings = details.get("warnings") or []
        warn_color = t["danger"] if warnings else t["success"]
        tk.Label(
            f,
            text=f"Warnings  ({len(warnings)})" + ("  ⚠" if warnings else "  ✓"),
            font=self.app._ui_b, bg=t["bg"], fg=warn_color, anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 4))
        w_frame, w_txt = self._scrollable_text(f, height=8)
        w_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        w_txt.configure(state="normal")
        w_txt.delete("1.0", tk.END)
        if warnings:
            for w in warnings:
                w_txt.insert(tk.END, f"⚠  {w}\n", "warn")
            w_txt.tag_configure("warn", foreground=t["danger"])
        else:
            w_txt.insert("1.0", "✓  No warnings — every column resolved cleanly.")
            w_txt.tag_configure("ok", foreground=t["success"])
            w_txt.tag_add("ok", "1.0", "end")
        w_txt.configure(state="disabled")

    # ── Tab: ? Binds
    def _rebuild_binds(self, details):
        f = self.tabs["binds"]
        for w in f.winfo_children():
            w.destroy()
        rows = details.get("bind_positions") or []
        if not rows:
            self._empty(f, "No `?` placeholders in this SQL.")
            return
        tree, _entry = self._table_with_search(
            f, [
                ("idx", "#",        60),
                ("ctx", "Context",  900),
            ],
            data=[(b["index"], b["context"]) for b in rows],
            primary_col=1,
        )

    # ── Tab: Tokens (lineage / ambiguous / unknown)
    def _rebuild_tokens(self, details):
        f = self.tabs["tokens"]
        for w in f.winfo_children():
            w.destroy()

        nb = ttk.Notebook(f, style="Inspect.TNotebook")
        nb.pack(fill="both", expand=True, padx=2, pady=2)

        # Lineage
        lin = tk.Frame(nb, bg=self.t["bg"])
        nb.add(lin, text=f"Column lineage ({len(details.get('column_lineage') or [])})")
        rows = details.get("column_lineage") or []
        if not rows:
            self._empty(lin, "No translatable columns detected.")
        else:
            data = [
                (r["phys"], r["logical"], "⚠ yes" if r["ambiguous"] else "", r["context"])
                for r in rows
            ]
            self._table_with_search(
                lin, [
                    ("phys",    "Physical",  180),
                    ("logical", "Logical",   220),
                    ("amb",     "Ambiguous", 90),
                    ("ctx",     "Used in",   480),
                ],
                data=data, primary_col=1,
            )

        # Ambiguous
        amb = tk.Frame(nb, bg=self.t["bg"])
        nb.add(amb, text=f"Ambiguous ({len(details.get('ambiguous') or [])})")
        if not details.get("ambiguous"):
            self._empty(amb, "✓  No ambiguous columns.")
        else:
            f2, txt = self._scrollable_text(amb, height=20)
            f2.pack(fill="both", expand=True, padx=2, pady=2)
            txt.configure(state="normal")
            txt.delete("1.0", tk.END)
            txt.tag_configure("warn",   foreground=self.t["warning"])
            txt.tag_configure("muted",  foreground=self.t["fg_muted"])
            txt.tag_configure("hdr",    foreground=self.t["fg"], font=self.app._ui_b)
            for row in details["ambiguous"]:
                txt.insert(tk.END, f"⚠  {row['phys']}", "warn")
                txt.insert(tk.END, f"   →  chosen: {row['logical']}\n", "hdr")
                for g in row["groups"]:
                    n = len(g["tables"])
                    sample = ", ".join((lt or pt) for _sc, pt, lt in g["tables"][:3])
                    more = f"  +{n-3} more" if n > 3 else ""
                    txt.insert(tk.END,
                               f"      → {g['logical']}  ({n} tables: {sample}{more})\n",
                               "muted")
                txt.insert(tk.END, "\n")
            txt.configure(state="disabled")

        # Unknown
        unk = tk.Frame(nb, bg=self.t["bg"])
        nb.add(unk, text=f"Unknown ({len(details.get('unknown_tokens') or [])})")
        toks = details.get("unknown_tokens") or []
        if not toks:
            self._empty(unk, "✓  No unknown identifiers — every column resolved.")
        else:
            self._table_with_search(
                unk, [("token", "Identifier (not in schema)", 700)],
                data=[(x,) for x in toks], primary_col=0,
            )

    # ── Tab: Java vars
    def _rebuild_java(self, details):
        f = self.tabs["java"]
        for w in f.winfo_children():
            w.destroy()
        rows = details.get("java_placeholders") or []
        if not rows:
            self._empty(f, "No Java placeholders — this SQL is fully literal.")
            return
        data = [(p["id"], p["rendered"], p["expr"], p["occurrences"]) for p in rows]
        self._table_with_search(
            f, [
                ("id",       "#",                 50),
                ("rendered", "Rendered as",       240),
                ("expr",     "Java expression",   600),
                ("uses",     "Uses",              60),
            ],
            data=data, primary_col=2,
        )

    # ── Tab: Buffers
    def _rebuild_buffers(self, details):
        f = self.tabs["buffers"]
        for w in f.winfo_children():
            w.destroy()
        bufs = details.get("buffers") or []
        if not bufs or len(bufs) <= 1:
            self._empty(f, "Single StringBuffer — no buffer splicing involved.")
            return
        data = [
            (b["name"],
             "main (returned / used)" if b["is_main"] else "helper (spliced)",
             b["appends"])
            for b in bufs
        ]
        self._table_with_search(
            f, [
                ("name",   "Buffer",  180),
                ("role",   "Role",    240),
                ("count",  "Appends", 100),
            ],
            data=data, primary_col=0,
        )

    # ── Tab: Reconstructed SQL  (with simple syntax highlighting)
    def _rebuild_sql(self, details):
        f = self.tabs["sql"]
        for w in f.winfo_children():
            w.destroy()
        sql = details.get("reconstructed_sql") or ""
        if not sql.strip():
            self._empty(f, "(no SQL extracted)")
            return
        tk.Label(
            f,
            text="Java placeholders shown as ${expr}; whitespace flattened. "
                 "Useful for pasting into a query tool.",
            font=self.app._small, bg=self.t["bg"], fg=self.t["fg_muted"],
            anchor="w", justify="left",
        ).pack(fill="x", padx=10, pady=(8, 2))
        ff, txt = self._scrollable_text(f, height=24)
        ff.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        txt.configure(font=self.app._mono, state="normal")
        txt.delete("1.0", tk.END)
        txt.insert("1.0", sql)
        self._highlight_sql(txt, sql)
        txt.configure(state="disabled")

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _empty(self, parent, msg):
        tk.Label(
            parent, text=msg, bg=self.t["bg"], fg=self.t["fg_muted"],
            font=self.app._ui, anchor="w",
        ).pack(padx=20, pady=24, anchor="w")

    def _scrollable_text(self, parent, height=12):
        t = self.t
        frame = tk.Frame(parent, bg=t["bg"])
        sb = tk.Scrollbar(frame, orient="vertical")
        txt = tk.Text(
            frame, height=height, wrap="word", font=self.app._ui,
            bg=t["surface"], fg=t["fg"],
            relief="flat", borderwidth=0, padx=10, pady=8,
            yscrollcommand=sb.set,
            selectbackground=t["accent"], selectforeground=t["accent_fg"],
            inactiveselectbackground=t["accent"],
        )
        sb.configure(command=txt.yview, bg=t["bg"], troughcolor=t["bg"], bd=0)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return frame, txt

    def _table_with_search(self, parent, columns, data, primary_col=0):
        """Render a search-box + sortable Treeview pre-populated from `data`.
        Double-click a row to copy its `primary_col`-th value to clipboard.
        Returns (tree, search_entry) so callers can extend if needed."""
        t = self.t

        # Search bar
        bar = tk.Frame(parent, bg=t["bg"])
        bar.pack(fill="x", padx=2, pady=(2, 2))
        tk.Label(bar, text="🔎  Filter:", font=self.app._small,
                 bg=t["bg"], fg=t["fg_muted"]).pack(side="left", padx=(4, 6))
        var = tk.StringVar()
        entry = tk.Entry(
            bar, textvariable=var, font=self.app._ui,
            bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
            relief="flat", bd=0,
        )
        entry.pack(side="left", fill="x", expand=True, ipady=3)
        count_lbl = tk.Label(
            bar, text=f"{len(data)} rows", font=self.app._small,
            bg=t["bg"], fg=t["fg_muted"],
        )
        count_lbl.pack(side="right", padx=6)

        # Tree
        wrap = tk.Frame(parent, bg=t["bg"])
        wrap.pack(fill="both", expand=True, padx=2, pady=(0, 2))
        sb = tk.Scrollbar(wrap, orient="vertical")
        tree = ttk.Treeview(
            wrap, columns=tuple(c[0] for c in columns), show="headings",
            style="Inspect.Treeview", yscrollcommand=sb.set,
        )
        sort_state = {c[0]: False for c in columns}  # False = ascending next

        def _make_sort_cmd(col_id):
            def _sort():
                idx = next(i for i, c in enumerate(columns) if c[0] == col_id)
                desc = sort_state[col_id]
                rows = [(tree.set(k, col_id), k) for k in tree.get_children("")]
                # Numeric vs string
                def _key(v):
                    try:    return (0, float(v[0]))
                    except: return (1, str(v[0]).lower())
                rows.sort(key=_key, reverse=desc)
                for i, (_v, k) in enumerate(rows):
                    tree.move(k, "", i)
                sort_state[col_id] = not desc
            return _sort

        for cid, label, width in columns:
            tree.heading(cid, text=label, command=_make_sort_cmd(cid))
            tree.column(cid, width=width, anchor="w", stretch=True)
        sb.configure(command=tree.yview, bg=t["bg"], troughcolor=t["bg"], bd=0)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Populate (also caches for filter)
        all_rows = list(data)

        def _populate(rows):
            tree.delete(*tree.get_children())
            for r in rows:
                tree.insert("", "end", values=r)
            count_lbl.configure(text=f"{len(rows)} of {len(all_rows)} rows"
                                if len(rows) != len(all_rows)
                                else f"{len(all_rows)} rows")

        _populate(all_rows)

        def _on_filter(*_):
            q = var.get().strip().lower()
            if not q:
                _populate(all_rows)
                return
            filt = [r for r in all_rows if any(q in str(c).lower() for c in r)]
            _populate(filt)

        var.trace_add("write", _on_filter)

        # Double-click → copy primary value
        def _on_dbl(_evt):
            sel = tree.selection()
            if not sel: return
            vals = tree.item(sel[0], "values")
            if primary_col < len(vals):
                val = str(vals[primary_col])
                try:
                    self.app.clipboard_clear()
                    self.app.clipboard_append(val)
                    preview = val if len(val) <= 30 else val[:27] + "…"
                    self.app._toast.show(f"Copied: {preview}", 1100, "success")
                except Exception:
                    pass

        tree.bind("<Double-Button-1>", _on_dbl)
        return tree, entry

    # ── SQL syntax highlighting (light-touch)
    _SQL_KEYWORDS_RE = re.compile(
        r"\b(SELECT|INSERT(?:\s+INTO)?|UPDATE|DELETE(?:\s+FROM)?|TRUNCATE(?:\s+TABLE)?|"
        r"FROM|WHERE|SET|VALUES|GROUP\s+BY|HAVING|ORDER\s+BY|UNION(?:\s+ALL)?|"
        r"INNER\s+JOIN|LEFT(?:\s+OUTER)?\s+JOIN|RIGHT(?:\s+OUTER)?\s+JOIN|"
        r"FULL(?:\s+OUTER)?\s+JOIN|CROSS\s+JOIN|JOIN|ON|AND|OR|NOT|"
        r"NULL|IS|IN|BETWEEN|LIKE|EXISTS|AS|DISTINCT|"
        r"CASE|WHEN|THEN|ELSE|END)\b",
        re.IGNORECASE,
    )

    def _highlight_sql(self, txt, sql):
        t = self.t
        txt.tag_configure("kw",   foreground=t["tag_header"])
        txt.tag_configure("str",  foreground=t["tag_phys"])
        txt.tag_configure("ph",   foreground=t["tag_logical"])
        txt.tag_configure("num",  foreground=t["tag_meta"])

        # Strings: '...'
        for m in re.finditer(r"'(?:[^'\\]|\\.)*'", sql):
            txt.tag_add("str",
                        f"1.0+{m.start()}c", f"1.0+{m.end()}c")
        # ${...} placeholders
        for m in re.finditer(r"\$\{[^}]*\}", sql):
            txt.tag_add("ph",
                        f"1.0+{m.start()}c", f"1.0+{m.end()}c")
        # Keywords (skip ones that fall inside already-highlighted strings)
        for m in self._SQL_KEYWORDS_RE.finditer(sql):
            start = f"1.0+{m.start()}c"
            end   = f"1.0+{m.end()}c"
            existing = set(txt.tag_names(start))
            if "str" in existing or "ph" in existing:
                continue
            txt.tag_add("kw", start, end)
        # Numbers
        for m in re.finditer(r"\b\d+(?:\.\d+)?\b", sql):
            start = f"1.0+{m.start()}c"
            existing = set(txt.tag_names(start))
            if "str" in existing or "ph" in existing or "kw" in existing:
                continue
            txt.tag_add("num", start, f"1.0+{m.end()}c")

"""Schema browser — searchable view of every table and column in the loaded
schema, with both physical and logical names side-by-side. Lets users look up
"what is this column" without leaving the app.

Layout:
    [search box                                                        ]
    ┌─ Tables (N) ──────────┐ ┌─ Columns of <selected table> (M) ────┐
    │ phys           logical│ │ phys           logical    other-tables│
    │ R_SYOHIN     商品マスタ │ │ BUNRUI1_CD    分類１コード   …          │
    │ R_TENPO      店舗マスタ │ │ SYOHIN_CD     商品コード      …          │
    └───────────────────────┘ └─────────────────────────────────────────┘
    [actions: Copy phys · Copy logical · Add to User Map · Close]

Search is case-insensitive and matches against both physical and logical
names. Selecting a table on the left filters the columns view.
"""

import tkinter as tk
from tkinter import ttk

from ...themes import THEMES
from ...paths import CUSTOM_SCHEMA


def open_schema_browser(app, name_filter=None):
    """Open the Schema Browser.

    name_filter: optional set of physical names. When provided, the Tables
    tree is restricted to those tables, and the global Columns view is
    restricted to those columns. Used by Ctrl+Shift+B to scope the browser
    to identifiers found in the current input.
    """
    existing = getattr(app, "_schema_browser_dialog", None)
    if existing and existing.winfo_exists():
        # Re-apply the new scope so a fresh Ctrl+Shift+B re-scopes the open dialog
        existing.lift()
        existing.focus_force()
        applier = getattr(existing, "_apply_name_filter", None)
        if applier:
            applier(name_filter)
        return

    t = THEMES[app._theme]
    dlg = tk.Toplevel(app)
    app._schema_browser_dialog = dlg
    dlg.title("Schema Browser")
    dlg.geometry("1080x680")
    dlg.configure(bg=t["bg"])
    dlg.transient(app)

    # Active name-filter (set when Ctrl+Shift+B opens the browser scoped to
    # the names in the current input). Mutable via dlg._apply_name_filter().
    active_filter = {"set": set(name_filter) if name_filter else None}

    # Top: scope banner — packed first so it sits above the search bar.
    # Visible only when active_filter["set"] is non-empty.
    scope_frame = tk.Frame(dlg, bg=t["surface"])
    scope_lbl = tk.Label(
        scope_frame, text="", font=app._ui, anchor="w",
        bg=t["surface"], fg=t["fg_muted"], padx=10, pady=4,
    )
    scope_lbl.pack(side="left", fill="x", expand=True)

    # Top: search bar
    search_frame = tk.Frame(dlg, bg=t["bg"])
    search_frame.pack(fill="x", padx=14, pady=(12, 6))
    tk.Label(
        search_frame, text="🔎  Search:", font=app._ui_b,
        bg=t["bg"], fg=t["fg"],
    ).pack(side="left")
    search_var = tk.StringVar()
    search_entry = tk.Entry(
        search_frame, textvariable=search_var, font=app._ui,
        bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", bd=0,
    )
    search_entry.pack(side="left", fill="x", expand=True, padx=(8, 8), ipady=5)
    count_lbl = tk.Label(
        search_frame, text="", font=app._small,
        bg=t["bg"], fg=t["fg_muted"],
    )
    count_lbl.pack(side="right")

    # Side-by-side panes
    body = tk.Frame(dlg, bg=t["bg"])
    body.pack(fill="both", expand=True, padx=14, pady=(0, 8))
    body.columnconfigure(0, weight=1, uniform="cols")
    body.columnconfigure(1, weight=2, uniform="cols")
    body.rowconfigure(1, weight=1)

    tk.Label(body, text="Tables", font=app._ui_b, bg=t["bg"], fg=t["fg"],
             anchor="w").grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 4))
    columns_header_var = tk.StringVar(value="Columns")
    tk.Label(body, textvariable=columns_header_var, font=app._ui_b,
             bg=t["bg"], fg=t["fg"], anchor="w").grid(
        row=0, column=1, sticky="ew", padx=(4, 0), pady=(0, 4),
    )

    style = ttk.Style()
    style.configure(
        "Schema.Treeview",
        background=t["surface"], fieldbackground=t["surface"], foreground=t["fg"],
        bordercolor=t["bg"], borderwidth=0, rowheight=22,
    )
    style.configure(
        "Schema.Treeview.Heading",
        background=t["muted_bg"], foreground=t["fg"], relief="flat",
    )
    style.map("Schema.Treeview",
              background=[("selected", t["accent"])],
              foreground=[("selected", t["accent_fg"])])

    # Tables tree
    tables_frame = tk.Frame(body, bg=t["bg"])
    tables_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
    tables_sb = tk.Scrollbar(tables_frame, orient="vertical")
    tables_tree = ttk.Treeview(
        tables_frame, columns=("phys", "logical"), show="headings",
        style="Schema.Treeview", yscrollcommand=tables_sb.set,
    )
    tables_tree.heading("phys",    text="Physical name")
    tables_tree.heading("logical", text="Logical name")
    tables_tree.column("phys",    width=180, anchor="w")
    tables_tree.column("logical", width=180, anchor="w")
    tables_sb.configure(command=tables_tree.yview, bg=t["bg"], troughcolor=t["bg"], bd=0)
    tables_tree.pack(side="left", fill="both", expand=True)
    tables_sb.pack(side="right", fill="y")

    # Columns tree
    cols_frame = tk.Frame(body, bg=t["bg"])
    cols_frame.grid(row=1, column=1, sticky="nsew", padx=(4, 0))
    cols_sb = tk.Scrollbar(cols_frame, orient="vertical")
    cols_tree = ttk.Treeview(
        cols_frame, columns=("phys", "logical", "tables"), show="headings",
        style="Schema.Treeview", yscrollcommand=cols_sb.set,
    )
    cols_tree.heading("phys",    text="Physical name")
    cols_tree.heading("logical", text="Logical name")
    cols_tree.heading("tables",  text="Other tables with this column")
    cols_tree.column("phys",    width=180, anchor="w")
    cols_tree.column("logical", width=200, anchor="w")
    cols_tree.column("tables",  width=320, anchor="w", stretch=True)
    cols_sb.configure(command=cols_tree.yview, bg=t["bg"], troughcolor=t["bg"], bd=0)
    cols_tree.pack(side="left", fill="both", expand=True)
    cols_sb.pack(side="right", fill="y")

    # ── Build the dataset once
    tables = _build_table_rows(app)
    all_cols_by_table = _build_column_rows_by_table(app)
    all_cols_global = _build_column_rows_all(app)

    selected_table = {"phys": None}

    def _refresh_columns_view():
        cols_tree.delete(*cols_tree.get_children())
        q = search_var.get().strip().lower()
        scope = active_filter["set"]
        if selected_table["phys"]:
            phys = selected_table["phys"]
            rows = all_cols_by_table.get(phys, [])
            columns_header_var.set(f"Columns of {phys} ({len(rows)})")
        else:
            rows = all_cols_global
            # When no table is selected, restrict the global column list to
            # the input scope (column phys name in scope) so users see only
            # names from their pasted text.
            if scope:
                rows = [r for r in rows if r[0] in scope]
            columns_header_var.set(
                f"Columns ({'scoped' if scope and not selected_table['phys'] else 'all tables'})"
            )
        if q:
            rows = [r for r in rows if q in r[0].lower() or q in r[1].lower()]
        for r in rows[:5000]:   # cap for responsiveness
            cols_tree.insert("", "end", values=r)

    def _refresh_tables_view():
        tables_tree.delete(*tables_tree.get_children())
        q = search_var.get().strip().lower()
        scope = active_filter["set"]
        rows = tables
        if scope:
            rows = [r for r in rows if r[0] in scope]
        if q:
            rows = [r for r in rows if q in r[0].lower() or q in r[1].lower()]
        for r in rows:
            tables_tree.insert("", "end", values=r)
        suffix = "  (scoped)" if scope else ""
        count_lbl.configure(text=f"{len(rows)} tables{suffix}")

    def _on_search(*_):
        # If search is non-empty, also clear table selection so columns
        # filter against ALL columns (matches user expectation: "find any
        # column matching X across the whole schema").
        if search_var.get().strip():
            selected_table["phys"] = None
        _refresh_tables_view()
        _refresh_columns_view()

    def _on_table_select(_evt):
        sel = tables_tree.selection()
        if sel:
            phys, _logical = tables_tree.item(sel[0], "values")
            selected_table["phys"] = phys
        else:
            selected_table["phys"] = None
        _refresh_columns_view()

    search_var.trace_add("write", _on_search)
    tables_tree.bind("<<TreeviewSelect>>", _on_table_select)

    # Scope banner: shown when active_filter is non-empty. The "Clear filter"
    # button clears the filter and re-renders both trees against the full set.
    def _apply_name_filter(new_filter):
        active_filter["set"] = set(new_filter) if new_filter else None
        # Re-set search and selection to a clean state so the user sees the
        # full scoped list (not an old in-table filter).
        selected_table["phys"] = None
        search_var.set("")
        _sync_scope_banner()
        _refresh_tables_view()
        _refresh_columns_view()

    def _sync_scope_banner():
        s = active_filter["set"]
        if s:
            scope_lbl.configure(text=f"Showing only {len(s)} name(s) found in input — search and table selection still work")
            # Pack above search_frame
            scope_frame.pack(fill="x", padx=14, pady=(8, 0), before=search_frame)
        else:
            scope_frame.pack_forget()

    tk.Button(
        scope_frame, text="Clear filter", font=app._small,
        relief="flat", bd=0, bg=t["muted_bg"], fg=t["muted_fg"],
        padx=10, pady=2, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=lambda: _apply_name_filter(None),
    ).pack(side="right", padx=6, pady=2)
    # Expose so subsequent open_schema_browser calls can re-scope.
    dlg._apply_name_filter = _apply_name_filter

    # Action bar
    actions = tk.Frame(dlg, bg=t["bg"])
    actions.pack(fill="x", padx=14, pady=(0, 12))

    def _copy_from_selected_col(field_idx):
        sel = cols_tree.selection() or tables_tree.selection()
        tree = cols_tree if cols_tree.selection() else tables_tree
        if not sel:
            return
        vals = tree.item(sel[0], "values")
        if field_idx < len(vals) and vals[field_idx]:
            try:
                app.clipboard_clear()
                app.clipboard_append(vals[field_idx])
                app._toast.show(f"Copied: {vals[field_idx]}", 1100, "success")
            except Exception:
                pass

    def _add_selected_to_user_map():
        sel = cols_tree.selection()
        if not sel:
            app._toast.show("Select a column first", 1100, "info")
            return
        phys, logical, _other = cols_tree.item(sel[0], "values")
        cols_map = (app._user_map.setdefault("columns", {}))
        cols_map[phys] = logical
        from ...config import save_user_map
        try:
            save_user_map(app._user_map)
        except Exception:
            pass
        app._toast.show(f"Added '{phys}' → '{logical}' to User Map", 1500, "success")

    def _btn(parent, text, command, accent=False):
        return tk.Button(
            parent, text=text, font=app._ui_b,
            bg=t["accent"] if accent else t["muted_bg"],
            fg=t["accent_fg"] if accent else t["muted_fg"],
            activebackground=t["accent"] if accent else t["muted_bg"],
            activeforeground=t["accent_fg"] if accent else t["muted_fg"],
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
            command=command,
        )

    _btn(actions, "Copy physical",  lambda: _copy_from_selected_col(0)).pack(side="left", padx=(0, 6))
    _btn(actions, "Copy logical",   lambda: _copy_from_selected_col(1)).pack(side="left", padx=(0, 6))
    _btn(actions, "Add column → User Map", _add_selected_to_user_map).pack(side="left", padx=(0, 6))
    _btn(actions, "Close", dlg.destroy, accent=True).pack(side="right")

    # Initial render
    _sync_scope_banner()
    _refresh_tables_view()
    _refresh_columns_view()
    search_entry.focus_set()

    # Esc closes; Cmd/Ctrl+W closes; Enter on empty search clears table filter
    dlg.bind("<Escape>", lambda e: dlg.destroy())
    dlg.bind("<Command-w>", lambda e: dlg.destroy())

    def _on_destroy(_e=None):
        if getattr(app, "_schema_browser_dialog", None) is dlg:
            app._schema_browser_dialog = None
    dlg.bind("<Destroy>", _on_destroy)


# ── Data builders ────────────────────────────────────────────────────────────
def _build_table_rows(app):
    """Return [(phys, logical_concat)] for every physical table.
    Multiple logical mappings (one per schema) are joined with " · "."""
    out = []
    for phys, entries in sorted(app.table_index.items()):
        # Skip CUSTOM_SCHEMA-only entries from user map (they don't add new
        # tables, only override existing).
        non_custom = [(sc, lg) for sc, lg in entries if sc != CUSTOM_SCHEMA]
        if not non_custom:
            continue
        logical_names = sorted({lg for _sc, lg in non_custom if lg and lg != phys})
        out.append((phys, " · ".join(logical_names) or "(no logical name)"))
    return out


def _build_column_rows_by_table(app):
    """Return {phys_table: [(phys_col, logical_col, other_tables_str)]}."""
    by_table = {}
    for phys_col, entries in app.column_index.items():
        for sc, pt, lt, lc in entries:
            if sc == CUSTOM_SCHEMA:
                continue
            by_table.setdefault(pt, []).append((phys_col, lc, sc, pt, lt))
    out = {}
    for pt, rows in by_table.items():
        # Collapse duplicates (same phys, same logical) for this table
        seen = {}
        for phys_col, lc, sc, _pt, lt in rows:
            key = (phys_col, lc)
            seen.setdefault(key, []).append((sc, lt))
        per_table = []
        for (phys_col, lc), occ in sorted(seen.items()):
            # "Other tables" → list of OTHER physical tables the same column
            # appears in (excluding the current one).
            other = sorted({
                e[1] for e in app.column_index.get(phys_col, [])
                if e[0] != CUSTOM_SCHEMA and e[1] != pt
            })
            other_s = ", ".join(other[:4]) + (f" +{len(other)-4}" if len(other) > 4 else "")
            per_table.append((phys_col, lc or "", other_s))
        out[pt] = sorted(per_table)
    return out


def _build_column_rows_all(app):
    """Flat list across all tables: [(phys_col, logical_col_concat, table_concat)]."""
    grouped = {}
    for phys_col, entries in app.column_index.items():
        for sc, pt, lt, lc in entries:
            if sc == CUSTOM_SCHEMA:
                continue
            key = (phys_col, lc)
            grouped.setdefault(key, set()).add(pt)
    rows = []
    for (phys_col, lc), tables in sorted(grouped.items()):
        tlist = sorted(tables)
        ts = ", ".join(tlist[:5]) + (f" +{len(tlist)-5}" if len(tlist) > 5 else "")
        rows.append((phys_col, lc or "", ts))
    return rows

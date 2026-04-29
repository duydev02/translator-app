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

    # Top: scope banner — packed first so it sits above the body.
    # Visible only when active_filter["set"] is non-empty.
    scope_frame = tk.Frame(dlg, bg=t["surface"])
    scope_lbl = tk.Label(
        scope_frame, text="", font=app._ui, anchor="w",
        bg=t["surface"], fg=t["fg_muted"], padx=10, pady=4,
    )
    scope_lbl.pack(side="left", fill="x", expand=True)

    # Each pane gets its own search box (one global search filtering both
    # panes was confusing — picking a table would also gate its columns
    # against the same query, which excluded the actual columns).
    table_search_var = tk.StringVar()
    col_search_var   = tk.StringVar()

    # Side-by-side panes
    body = tk.Frame(dlg, bg=t["bg"])
    body.pack(fill="both", expand=True, padx=14, pady=(12, 8))
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

    # ── Tables pane (search bar above the tree)
    tables_pane = tk.Frame(body, bg=t["bg"])
    tables_pane.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
    tables_pane.rowconfigure(1, weight=1)
    tables_pane.columnconfigure(0, weight=1)

    tbl_search_row = tk.Frame(tables_pane, bg=t["bg"])
    tbl_search_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
    tk.Label(tbl_search_row, text="🔎 tables", font=app._small,
             bg=t["bg"], fg=t["fg_muted"]).pack(side="left")
    table_search_entry = tk.Entry(
        tbl_search_row, textvariable=table_search_var, font=app._ui,
        bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", bd=0,
    )
    table_search_entry.pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=4)
    tables_count_lbl = tk.Label(
        tbl_search_row, text="", font=app._small,
        bg=t["bg"], fg=t["fg_muted"],
    )
    tables_count_lbl.pack(side="right", padx=(6, 0))

    tables_frame = tk.Frame(tables_pane, bg=t["bg"])
    tables_frame.grid(row=1, column=0, sticky="nsew")
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

    # ── Columns pane (search bar + Show all + tree)
    cols_pane = tk.Frame(body, bg=t["bg"])
    cols_pane.grid(row=1, column=1, sticky="nsew", padx=(4, 0))
    cols_pane.rowconfigure(1, weight=1)
    cols_pane.columnconfigure(0, weight=1)

    col_search_row = tk.Frame(cols_pane, bg=t["bg"])
    col_search_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
    tk.Label(col_search_row, text="🔎 columns", font=app._small,
             bg=t["bg"], fg=t["fg_muted"]).pack(side="left")
    col_search_entry = tk.Entry(
        col_search_row, textvariable=col_search_var, font=app._ui,
        bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", bd=0,
    )
    col_search_entry.pack(side="left", fill="x", expand=True, padx=(6, 6), ipady=4)
    # "Show all" clears the table selection so the columns view shows
    # the global list again. Visibility is toggled by _refresh_columns_view.
    show_all_btn = tk.Button(
        col_search_row, text="Show all", font=app._small,
        relief="flat", bd=0, bg=t["muted_bg"], fg=t["muted_fg"],
        padx=10, pady=2, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=lambda: _clear_table_selection(),
    )
    cols_count_lbl = tk.Label(
        col_search_row, text="", font=app._small,
        bg=t["bg"], fg=t["fg_muted"],
    )
    cols_count_lbl.pack(side="right", padx=(6, 0))
    # show_all_btn is packed/forgotten dynamically inside _refresh_columns_view

    cols_frame = tk.Frame(cols_pane, bg=t["bg"])
    cols_frame.grid(row=1, column=0, sticky="nsew")
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
        q = col_search_var.get().strip().lower()
        scope = active_filter["set"]
        if selected_table["phys"]:
            phys = selected_table["phys"]
            rows = all_cols_by_table.get(phys, [])
            columns_header_var.set(f"Columns of {phys}")
            # Show the "Show all" button so the user can escape the per-table view
            show_all_btn.pack(side="right", padx=(0, 0))
        else:
            rows = all_cols_global
            # When no table is selected, restrict the global column list to
            # the input scope (column phys name in scope) so users see only
            # names from their pasted text.
            if scope:
                rows = [r for r in rows if r[0] in scope]
            columns_header_var.set(
                f"Columns ({'scoped to input' if scope else 'all tables'})"
            )
            show_all_btn.pack_forget()
        if q:
            rows = [r for r in rows if q in r[0].lower() or q in r[1].lower()]
        for r in rows[:5000]:   # cap for responsiveness
            cols_tree.insert("", "end", values=r)
        cols_count_lbl.configure(text=f"{len(rows)} cols")

    def _refresh_tables_view():
        tables_tree.delete(*tables_tree.get_children())
        q = table_search_var.get().strip().lower()
        scope = active_filter["set"]
        rows = tables
        if scope:
            rows = [r for r in rows if r[0] in scope]
        if q:
            rows = [r for r in rows if q in r[0].lower() or q in r[1].lower()]
        for r in rows:
            tables_tree.insert("", "end", values=r)
        suffix = "  (scoped)" if scope else ""
        tables_count_lbl.configure(text=f"{len(rows)} tables{suffix}")

    def _clear_table_selection():
        # Used by the "Show all" button: drop selection, refresh columns
        # so the global list comes back. The column search query is kept
        # — most likely the user wants to find that name across all tables.
        if tables_tree.selection():
            tables_tree.selection_remove(*tables_tree.selection())
        selected_table["phys"] = None
        _refresh_columns_view()

    def _on_table_select(_evt):
        sel = tables_tree.selection()
        if sel:
            phys, _logical = tables_tree.item(sel[0], "values")
            selected_table["phys"] = phys
        else:
            selected_table["phys"] = None
        _refresh_columns_view()

    # Each search box drives only its own pane.
    table_search_var.trace_add("write", lambda *_: _refresh_tables_view())
    col_search_var.trace_add("write",   lambda *_: _refresh_columns_view())
    tables_tree.bind("<<TreeviewSelect>>", _on_table_select)

    # Esc inside a search entry: clear it (faster than ⌫⌫⌫) — only if it
    # has text; otherwise let the dialog-level Esc binding close the window.
    def _esc_clears(var):
        def handler(_e):
            if var.get():
                var.set("")
                return "break"   # consume so dialog Esc doesn't close
            return None          # let dialog handle
        return handler
    table_search_entry.bind("<Escape>", _esc_clears(table_search_var))
    col_search_entry.bind("<Escape>",   _esc_clears(col_search_var))

    # Scope banner: shown when active_filter is non-empty. The "Clear filter"
    # button clears the filter and re-renders both trees against the full set.
    def _apply_name_filter(new_filter):
        active_filter["set"] = set(new_filter) if new_filter else None
        # Re-set search and selection to a clean state so the user sees the
        # full scoped list (not an old in-pane filter).
        if tables_tree.selection():
            tables_tree.selection_remove(*tables_tree.selection())
        selected_table["phys"] = None
        table_search_var.set("")
        col_search_var.set("")
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

    def _override_selected_logical_name():
        """Prompt the user for a logical-name override for the selected column
        and save it into the User Map (translator_custom_map.json). The
        override always wins against db_schema_output.json — useful when the
        JSON's logical name is wrong, missing, or you want a team-specific
        rendering. Re-runs translation on save."""
        sel = cols_tree.selection()
        if not sel:
            app._toast.show("Select a column first", 1100, "info")
            return
        phys, current_logical, _other = cols_tree.item(sel[0], "values")
        existing_override = (app._user_map.get("columns") or {}).get(phys, "")
        prefill = existing_override or current_logical or ""

        # Small modal prompt — prefilled, Enter saves, Esc cancels.
        prompt = tk.Toplevel(dlg)
        prompt.title("Override logical name")
        prompt.configure(bg=t["bg"])
        prompt.transient(dlg)
        prompt.grab_set()
        prompt.geometry("+%d+%d" % (dlg.winfo_rootx() + 80, dlg.winfo_rooty() + 80))

        tk.Label(
            prompt, text=f"Override logical name for column", font=app._ui_b,
            bg=t["bg"], fg=t["fg"], anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(
            prompt, text=phys, font=app._mono,
            bg=t["bg"], fg=t["accent"], anchor="w",
        ).pack(fill="x", padx=14, pady=(0, 8))
        hint = (
            f"Currently: {current_logical}" if current_logical else "Currently: (no logical name)"
        )
        if existing_override and existing_override != current_logical:
            hint += f"\nUser-Map override active: {existing_override}"
        tk.Label(
            prompt, text=hint, font=app._small,
            bg=t["bg"], fg=t["fg_muted"], justify="left", anchor="w",
        ).pack(fill="x", padx=14, pady=(0, 8))

        var = tk.StringVar(value=prefill)
        entry = tk.Entry(
            prompt, textvariable=var, font=app._ui,
            bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
            relief="flat", bd=0, width=40,
        )
        entry.pack(fill="x", padx=14, pady=(0, 10), ipady=5)
        entry.focus_set()
        entry.select_range(0, "end")

        from ...config import save_user_map

        def _save(_e=None):
            new_logical = var.get().strip()
            if not new_logical:
                app._toast.show("Logical name can't be empty — use Remove instead", 1500, "info")
                return
            (app._user_map.setdefault("columns", {}))[phys] = new_logical
            try:
                save_user_map(app._user_map)
            except Exception:
                pass
            app._toast.show(f"User Map: {phys} → {new_logical}", 1500, "success")
            prompt.destroy()
            # Re-run translation so the new override takes effect immediately.
            try:
                app.on_translate()
            except Exception:
                pass

        def _remove():
            cols_map = app._user_map.get("columns") or {}
            if phys in cols_map:
                del cols_map[phys]
                try:
                    save_user_map(app._user_map)
                except Exception:
                    pass
                app._toast.show(f"Removed override for {phys}", 1500, "success")
                try:
                    app.on_translate()
                except Exception:
                    pass
            prompt.destroy()

        btn_row = tk.Frame(prompt, bg=t["bg"])
        btn_row.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(
            btn_row, text="Save", font=app._btn, relief="flat", bd=0,
            bg=t["accent"], fg=t["accent_fg"],
            activebackground=t["accent"], activeforeground=t["accent_fg"],
            padx=18, pady=6, cursor="hand2", command=_save,
        ).pack(side="right")
        tk.Button(
            btn_row, text="Cancel", font=app._btn, relief="flat", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"],
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            padx=14, pady=6, cursor="hand2", command=prompt.destroy,
        ).pack(side="right", padx=(0, 6))
        if existing_override:
            tk.Button(
                btn_row, text="Remove override", font=app._btn, relief="flat", bd=0,
                bg=t["muted_bg"], fg=t["muted_fg"],
                activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
                padx=12, pady=6, cursor="hand2", command=_remove,
            ).pack(side="left")
        entry.bind("<Return>", _save)
        prompt.bind("<Escape>", lambda _e: prompt.destroy())

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
    override_btn = _btn(actions, "Override logical name…", _override_selected_logical_name)
    override_btn.pack(side="left", padx=(0, 6))
    # Hover hint so users learn what the User Map override is for.
    try:
        app._attach_tooltip(
            override_btn,
            "Save a personal logical-name override for the selected column.\n"
            "Stored in translator_custom_map.json. Always wins against\n"
            "db_schema_output.json. Translation re-runs immediately on save.",
        )
    except Exception:
        pass
    _btn(actions, "Close", dlg.destroy, accent=True).pack(side="right")

    # Initial render
    _sync_scope_banner()
    _refresh_tables_view()
    _refresh_columns_view()
    table_search_entry.focus_set()

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
    """Return {phys_table: [(phys_col, logical_col, other_tables_str)]}.

    Columns are ordered by their JSON declaration order (i.e. the natural
    DB-definition order from db_schema_output.json) — not alphabetically.
    Falls back to alpha sort for any column not in `app.table_column_order`
    (defensive — shouldn't happen with normal data)."""
    by_table = {}
    for phys_col, entries in app.column_index.items():
        for sc, pt, lt, lc in entries:
            if sc == CUSTOM_SCHEMA:
                continue
            by_table.setdefault(pt, []).append((phys_col, lc, sc, pt, lt))
    order_map = getattr(app, "table_column_order", {}) or {}
    out = {}
    for pt, rows in by_table.items():
        # Collapse duplicates (same phys, same logical) for this table
        seen = {}
        for phys_col, lc, sc, _pt, lt in rows:
            key = (phys_col, lc)
            seen.setdefault(key, []).append((sc, lt))
        # Build a one-row-per-(phys,logical) list with "other tables" string.
        per_table = {}
        for (phys_col, lc), occ in seen.items():
            other = sorted({
                e[1] for e in app.column_index.get(phys_col, [])
                if e[0] != CUSTOM_SCHEMA and e[1] != pt
            })
            other_s = ", ".join(other[:4]) + (f" +{len(other)-4}" if len(other) > 4 else "")
            per_table[(phys_col, lc)] = (phys_col, lc or "", other_s)
        # Order: JSON declaration order first, then anything left over A→Z.
        ordered = []
        seen_keys: set[tuple[str, str]] = set()
        for phys_col in order_map.get(pt, []):
            for key in list(per_table.keys()):
                if key in seen_keys:
                    continue
                if key[0] == phys_col:
                    ordered.append(per_table[key])
                    seen_keys.add(key)
        # Defensive trailer — anything not found in the order map (e.g. user
        # overrides or schema drift) appended A–Z so it's still visible.
        trailer = sorted(
            (row for key, row in per_table.items() if key not in seen_keys),
            key=lambda r: (r[0], r[1]),
        )
        out[pt] = ordered + trailer
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

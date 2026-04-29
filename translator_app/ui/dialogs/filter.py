import tkinter as tk

from ...themes import THEMES


def open_filter_dialog(app):
    t = THEMES[app._theme]
    dlg = tk.Toplevel(app)
    dlg.title("Translation Filter")
    dlg.geometry("680x560")
    dlg.minsize(520, 420)
    dlg.configure(bg=t["bg"])
    dlg.transient(app); dlg.grab_set()

    # Header
    tk.Label(dlg,
        text="Choose which schemas and tables to include. Empty = all.",
        font=app._ui, bg=t["bg"], fg=t["fg_muted"], anchor="w",
    ).pack(fill="x", padx=14, pady=(12, 6))

    # Main body: two columns (Schemas | Tables)
    body = tk.Frame(dlg, bg=t["bg"])
    body.pack(fill="both", expand=True, padx=14, pady=4)

    # ── Left: schemas ──
    left = tk.Frame(body, bg=t["bg"])
    left.pack(side="left", fill="both", expand=False, padx=(0, 8))
    tk.Label(left, text="Schemas", font=app._ui_b,
        bg=t["bg"], fg=t["fg"], anchor="w").pack(fill="x")

    # phys_table → set of schemas it belongs to (used to scope the Tables list)
    phys_to_schemas = {
        phys: {sch for sch, _lg in entries}
        for phys, entries in app.table_index.items()
    }

    schema_vars = {}
    schema_frame = tk.Frame(left, bg=t["surface"], padx=6, pady=6)
    schema_frame.pack(fill="both", expand=True, pady=(4, 4))
    # `_refresh_table_visibility` is defined later (after table widgets exist);
    # forward-declare a slot so schema checkbox callbacks can call it safely.
    _scope_cb = {"fn": lambda: None}
    for s in app.schemas:
        v = tk.BooleanVar(value=(s in app._filter_schemas) if app._filter_schemas else False)
        schema_vars[s] = v
        cb = tk.Checkbutton(schema_frame, text=s, variable=v,
            bg=t["surface"], fg=t["fg"], selectcolor=t["bg"],
            activebackground=t["surface"], activeforeground=t["fg"],
            font=app._ui, anchor="w", bd=0, highlightthickness=0,
            command=lambda: _scope_cb["fn"]())
        cb.pack(fill="x", anchor="w")

    sch_btns = tk.Frame(left, bg=t["bg"])
    sch_btns.pack(fill="x")
    def _set_all_schemas(value):
        for v in schema_vars.values():
            v.set(value)
        _scope_cb["fn"]()
    tk.Button(sch_btns, text="All", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=2, cursor="hand2",
        command=lambda: _set_all_schemas(True)).pack(side="left")
    tk.Button(sch_btns, text="None", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=2, cursor="hand2",
        command=lambda: _set_all_schemas(False)).pack(side="left", padx=(6, 0))

    # ── Right: tables (search + scrollable checkbox list) ──
    right = tk.Frame(body, bg=t["bg"])
    right.pack(side="left", fill="both", expand=True)

    right_head = tk.Frame(right, bg=t["bg"])
    right_head.pack(fill="x")
    tk.Label(right_head, text="Tables", font=app._ui_b,
        bg=t["bg"], fg=t["fg"], anchor="w").pack(side="left")

    search_var = tk.StringVar()
    search_ent = tk.Entry(right_head, textvariable=search_var,
        font=app._ui, relief="flat", bd=0,
        bg=t["surface"], fg=t["fg"], insertbackground=t["insert"])
    search_ent.pack(side="right", fill="x", expand=True, ipady=3, padx=(8, 0))
    tk.Label(right_head, text="🔍", font=app._ui,
        bg=t["bg"], fg=t["fg_muted"]).pack(side="right", padx=(6, 2))

    # Scrollable list
    list_wrap = tk.Frame(right, bg=t["surface"])
    list_wrap.pack(fill="both", expand=True, pady=(4, 4))

    canvas = tk.Canvas(list_wrap, bg=t["surface"], highlightthickness=0, bd=0)
    canvas.pack(side="left", fill="both", expand=True)
    vsb = tk.Scrollbar(list_wrap, orient="vertical", command=canvas.yview)
    vsb.pack(side="right", fill="y")
    app._theme_scrollbar(vsb, t)
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
    app.after(50, _bind_children_wheel)

    # Build table checkboxes (with logical name hint)
    table_vars = {}
    table_labels = []   # list of (name, widget, search_text)
    for phys in sorted(app.table_index.keys()):
        entries = app.table_index[phys]
        logical = entries[0][1] if entries else ""
        schema  = entries[0][0] if entries else ""
        v = tk.BooleanVar(value=(phys in app._filter_tables) if app._filter_tables else False)
        table_vars[phys] = v
        display = f"{phys}    ({logical})  · {schema}" if logical else f"{phys}  · {schema}"
        cb = tk.Checkbutton(inner, text=display, variable=v,
            bg=t["surface"], fg=t["fg"], selectcolor=t["bg"],
            activebackground=t["surface"], activeforeground=t["fg"],
            font=app._ui, anchor="w", bd=0, highlightthickness=0)
        cb.pack(fill="x", anchor="w")
        table_labels.append((phys, cb, f"{phys} {logical} {schema}".lower()))

    def _selected_schemas():
        return {s for s, v in schema_vars.items() if v.get()}

    def _table_in_scope(phys, sel):
        # No schema selected = no scope restriction. Otherwise only show tables
        # whose phys_table is bound to at least one selected schema.
        if not sel:
            return True
        owners = phys_to_schemas.get(phys, set())
        return bool(owners & sel)

    def _filter_tables(*_):
        q = search_var.get().strip().lower()
        sel = _selected_schemas()
        for phys, cb, hay in table_labels:
            in_scope = _table_in_scope(phys, sel)
            matches_q = (not q) or (q in hay)
            if in_scope and matches_q:
                cb.pack(fill="x", anchor="w")
            else:
                cb.pack_forget()
                # Auto-uncheck out-of-scope tables so they don't silently apply.
                if not in_scope:
                    table_vars[phys].set(False)
    search_var.trace_add("write", _filter_tables)
    _scope_cb["fn"] = _filter_tables  # wire schema toggles → table list refresh
    _filter_tables()  # apply initial scope based on existing _filter_schemas

    tbl_btns = tk.Frame(right, bg=t["bg"])
    tbl_btns.pack(fill="x")
    def _set_all_visible(value):
        q = search_var.get().strip().lower()
        sel = _selected_schemas()
        for phys, cb, hay in table_labels:
            if not _table_in_scope(phys, sel):
                continue
            if not q or q in hay:
                table_vars[phys].set(value)
    tk.Button(tbl_btns, text="All (visible)", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=2, cursor="hand2",
        command=lambda: _set_all_visible(True)).pack(side="left")
    tk.Button(tbl_btns, text="None (visible)", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=2, cursor="hand2",
        command=lambda: _set_all_visible(False)).pack(side="left", padx=(6, 0))

    # ── Footer ──
    footer = tk.Frame(dlg, bg=t["bg"])
    footer.pack(fill="x", padx=14, pady=(8, 12))

    def _clear_all():
        for v in schema_vars.values(): v.set(False)
        for v in table_vars.values():  v.set(False)
        _filter_tables()

    def _apply():
        sel_schemas = {s for s, v in schema_vars.items() if v.get()}
        sel_tables  = {t_ for t_, v in table_vars.items() if v.get()}
        # Drop any selected tables that don't belong to a selected schema —
        # otherwise they leak through and translate against another schema.
        if sel_schemas:
            sel_tables = {
                t_ for t_ in sel_tables
                if phys_to_schemas.get(t_, set()) & sel_schemas
            }
        app._filter_schemas = sel_schemas
        app._filter_tables  = sel_tables
        app._refresh_filter_btn()
        dlg.destroy()
        app.on_translate()
        total_s = len(app.schemas)
        if sel_schemas:
            total_t = sum(
                1 for owners in phys_to_schemas.values()
                if owners & sel_schemas
            )
        else:
            total_t = len(app.table_index)
        s_msg = f"{len(sel_schemas)}/{total_s} schemas" if sel_schemas else f"all {total_s} schemas"
        t_msg = f"{len(sel_tables)}/{total_t} tables"   if sel_tables  else f"all {total_t} tables"
        app._toast.show(f"Filter: {s_msg} · {t_msg}", 1500, "success")

    tk.Button(footer, text="Apply", font=app._btn, relief="flat", bd=0,
        bg=t["accent"], fg=t["accent_fg"], padx=18, pady=6, cursor="hand2",
        activebackground=t["accent"], activeforeground=t["accent_fg"],
        command=_apply).pack(side="right")
    tk.Button(footer, text="Cancel", font=app._btn, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=14, pady=6, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=dlg.destroy).pack(side="right", padx=(0, 6))
    tk.Button(footer, text="Clear all", font=app._btn, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=12, pady=6, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=_clear_all).pack(side="left")

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

    schema_vars = {}
    schema_frame = tk.Frame(left, bg=t["surface"], padx=6, pady=6)
    schema_frame.pack(fill="both", expand=True, pady=(4, 4))
    for s in app.schemas:
        v = tk.BooleanVar(value=(s in app._filter_schemas) if app._filter_schemas else False)
        schema_vars[s] = v
        cb = tk.Checkbutton(schema_frame, text=s, variable=v,
            bg=t["surface"], fg=t["fg"], selectcolor=t["bg"],
            activebackground=t["surface"], activeforeground=t["fg"],
            font=app._ui, anchor="w", bd=0, highlightthickness=0)
        cb.pack(fill="x", anchor="w")

    sch_btns = tk.Frame(left, bg=t["bg"])
    sch_btns.pack(fill="x")
    tk.Button(sch_btns, text="All", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=2, cursor="hand2",
        command=lambda: [v.set(True) for v in schema_vars.values()]).pack(side="left")
    tk.Button(sch_btns, text="None", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=2, cursor="hand2",
        command=lambda: [v.set(False) for v in schema_vars.values()]).pack(side="left", padx=(6, 0))

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

    def _apply():
        app._filter_schemas = {s for s, v in schema_vars.items() if v.get()}
        app._filter_tables  = {t_ for t_, v in table_vars.items() if v.get()}
        app._refresh_filter_btn()
        dlg.destroy()
        app.on_translate()
        app._toast.show(
            f"Filter: {len(app._filter_schemas)} schemas · {len(app._filter_tables)} tables",
            1500, "success")

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

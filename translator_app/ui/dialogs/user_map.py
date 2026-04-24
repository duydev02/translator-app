import os
import tkinter as tk
from tkinter import ttk

from ...config import save_user_map
from ...paths import USER_MAP_FILE
from ...themes import THEMES


def open_user_map_dialog(app):
    """Table-based editor for the user override map."""
    t = THEMES[app._theme]
    dlg = tk.Toplevel(app)
    dlg.title("User-Defined Overrides")
    dlg.geometry("760x600")
    dlg.minsize(620, 460)
    dlg.configure(bg=t["bg"])
    dlg.transient(app); dlg.grab_set()

    tk.Label(
        dlg,
        text=(
            "Custom physical ↔ logical mappings. These always win over "
            "db_schema_output.json and bypass schema/table filters."
        ),
        font=app._ui, bg=t["bg"], fg=t["fg_muted"],
        anchor="w", justify="left", wraplength=720,
    ).pack(fill="x", padx=14, pady=(12, 6))

    # ── Footer buttons (packed first so they reserve space) ──
    btns = tk.Frame(dlg, bg=t["bg"])
    btns.pack(side="bottom", fill="x", padx=14, pady=(0, 12))

    # File info line (bottom, below buttons)
    file_lbl = tk.Label(dlg,
        text=f"File: {USER_MAP_FILE}",
        font=app._small, bg=t["bg"], fg=t["fg_muted"], anchor="w",
    )
    file_lbl.pack(side="bottom", fill="x", padx=14, pady=(0, 4))

    # ── Notebook with two tabs ──
    nb = ttk.Notebook(dlg)
    nb.pack(fill="both", expand=True, padx=14, pady=(4, 8))

    tbl_tab, tbl_tree = _build_user_map_tab(
        app, nb, app._user_map.get("tables", {}), t)
    col_tab, col_tree = _build_user_map_tab(
        app, nb, app._user_map.get("columns", {}), t)
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
        app._user_map = new
        app._load_data()
        app._refresh_umap_btn()
        app._refresh_index_stats()
        app.on_translate()
        dlg.destroy()
        total = len(new["tables"]) + len(new["columns"])
        app._toast.show(f"Saved {total} override(s)", 1500, "success")

    def _open_externally():
        if not os.path.exists(USER_MAP_FILE):
            save_user_map({"tables": {}, "columns": {}})
        try:
            os.startfile(USER_MAP_FILE)
        except Exception as e:
            app._toast.show(f"Open failed: {e}", 2500, "error")

    tk.Button(btns, text="Save", font=app._btn, relief="flat", bd=0,
        bg=t["accent"], fg=t["accent_fg"], padx=18, pady=6, cursor="hand2",
        activebackground=t["accent"], activeforeground=t["accent_fg"],
        command=_save).pack(side="right")
    tk.Button(btns, text="Cancel", font=app._btn, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=14, pady=6, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=dlg.destroy).pack(side="right", padx=(0, 6))
    tk.Button(btns, text="📂  Open JSON file", font=app._btn, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=12, pady=6, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=_open_externally).pack(side="left")
    tk.Button(btns, text="⚠  Inconsistencies…", font=app._btn, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=12, pady=6, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=lambda: (dlg.destroy(), app.open_inconsistency_dialog())).pack(side="left", padx=(6, 0))


def _build_user_map_tab(app, parent, data, t):
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
        command=lambda: app._sort_tree(tree, "phys"))
    tree.heading("logical", text="Logical name",
        command=lambda: app._sort_tree(tree, "logical"))
    tree.column("phys",    width=220, anchor="w")
    tree.column("logical", width=400, anchor="w")

    vsb = tk.Scrollbar(tree_wrap, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    app._theme_scrollbar(vsb, t)
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
        e = tk.Entry(entry_bar, textvariable=var, font=app._ui,
            bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
            relief="flat", bd=0)
        return e

    tk.Label(entry_bar, text="Physical:", font=app._ui,
        bg=t["bg"], fg=t["fg"]).grid(row=0, column=0, sticky="w", padx=(0, 6))
    phys_ent = _mk_entry(phys_var)
    phys_ent.grid(row=0, column=1, sticky="ew", padx=(0, 10), ipady=4)

    tk.Label(entry_bar, text="Logical:", font=app._ui,
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
            app._toast.show("Both fields are required", 1500, "error")
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
        font=app._btn, relief="flat", bd=0,
        bg=t["accent"], fg=t["accent_fg"], padx=14, pady=6, cursor="hand2",
        activebackground=t["accent"], activeforeground=t["accent_fg"],
        command=_add_or_update).pack(side="left")
    tk.Button(btn_bar, text="➖  Remove",
        font=app._btn, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=14, pady=6, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=_remove_selected).pack(side="left", padx=(6, 0))

    tk.Label(btn_bar,
        text="  Enter in Physical → jumps to Logical.  Enter in Logical → Add.  Del key → Remove.",
        font=app._small, bg=t["bg"], fg=t["fg_muted"]
    ).pack(side="left", padx=10)

    return frame, tree

import os
import tkinter as tk
from tkinter import ttk, filedialog

from ...config import save_user_map
from ...themes import THEMES
from ...translate import find_column_inconsistencies


def open_inconsistency_dialog(app):
    t = THEMES[app._theme]
    issues = find_column_inconsistencies(app.column_index)

    dlg = tk.Toplevel(app)
    dlg.title("Column Name Inconsistencies")
    dlg.geometry("820x580")
    dlg.minsize(640, 460)
    dlg.configure(bg=t["bg"])
    dlg.transient(app); dlg.grab_set()

    header = tk.Label(
        dlg,
        text=(
            f"Found {len(issues)} column(s) with conflicting logical names.\n"
            "Select a variant from each row and click “Apply” to add those "
            "picks to the User Map."
        ),
        font=app._ui, bg=t["bg"], fg=t["fg_muted"],
        anchor="w", justify="left", wraplength=780,
    )
    header.pack(fill="x", padx=14, pady=(12, 6))

    # Footer first so it stays visible
    footer = tk.Frame(dlg, bg=t["bg"])
    footer.pack(side="bottom", fill="x", padx=14, pady=(0, 12))

    # Tree
    tree_wrap = tk.Frame(dlg, bg=t["bg"])
    tree_wrap.pack(fill="both", expand=True, padx=14, pady=(4, 8))

    tree = ttk.Treeview(
        tree_wrap, columns=("logical", "count", "tables"),
        show="tree headings", selectmode="extended",
    )
    tree.heading("#0",      text="Physical column")
    tree.heading("logical", text="Logical name")
    tree.heading("count",   text="# tables")
    tree.heading("tables",  text="Tables")
    tree.column("#0",      width=200, stretch=False, anchor="w")
    tree.column("logical", width=230, stretch=False, anchor="w")
    tree.column("count",   width=70,  stretch=False, anchor="center")
    tree.column("tables",  width=280, stretch=True,  anchor="w")

    vsb = tk.Scrollbar(tree_wrap, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    app._theme_scrollbar(vsb, t)
    vsb.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)

    # Populate — parent row = phys_col, child rows = variants
    child_meta = {}  # iid → (phys_col, logical)
    for issue in issues:
        phys = issue["phys_col"]
        total = sum(v["count"] for v in issue["variants"])
        parent_id = tree.insert(
            "", "end", text=phys,
            values=(f"{len(issue['variants'])} variants", total, ""),
            open=True,
        )
        for v in issue["variants"]:
            tbls = ", ".join(f"{lt}({pt})" for _, pt, lt in v["tables"][:4])
            if len(v["tables"]) > 4:
                tbls += f" … +{len(v['tables']) - 4}"
            child_id = tree.insert(
                parent_id, "end", text="",
                values=(v["logical"], v["count"], tbls),
            )
            child_meta[child_id] = (phys, v["logical"])

    if not issues:
        tk.Label(
            tree_wrap,
            text="🎉  No inconsistencies found — every column has a single logical name.",
            bg=t["bg"], fg=t["success"], font=app._ui_b,
        ).place(relx=0.5, rely=0.5, anchor="center")

    # Apply: each selected child variant → written to user_map["columns"]
    def _apply_selected():
        picks = {}
        for iid in tree.selection():
            if iid in child_meta:
                phys, logical = child_meta[iid]
                # If user picked multiple variants for the same phys_col,
                # take the last one
                picks[phys] = logical
        if not picks:
            app._toast.show("Select at least one variant first", 1800, "error")
            return
        # Merge into user map
        cols = app._user_map.get("columns") or {}
        cols.update(picks)
        app._user_map["columns"] = cols
        save_user_map(app._user_map)
        app._load_data()
        app._refresh_umap_btn()
        app._refresh_index_stats()
        app.on_translate()
        dlg.destroy()
        app._toast.show(
            f"Added {len(picks)} override(s) to User Map",
            1600, "success",
        )

    def _export_csv():
        path = filedialog.asksaveasfilename(
            title="Save inconsistency report",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            parent=dlg,
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write("Physical column,Logical variant,# tables,Tables\n")
                for issue in issues:
                    for v in issue["variants"]:
                        tbls = "; ".join(f"{lt} ({pt}) / {sc}"
                                         for sc, pt, lt in v["tables"])
                        line = f'"{issue["phys_col"]}","{v["logical"]}",{v["count"]},"{tbls}"\n'
                        f.write(line)
            app._toast.show(f"Saved {os.path.basename(path)}", 1400, "success")
        except Exception as e:
            app._toast.show(f"Save failed: {e}", 2500, "error")

    tk.Button(
        footer, text="Apply picks to User Map",
        font=app._btn, relief="flat", bd=0,
        bg=t["accent"], fg=t["accent_fg"],
        activebackground=t["accent"], activeforeground=t["accent_fg"],
        padx=16, pady=6, cursor="hand2", command=_apply_selected,
    ).pack(side="right")
    tk.Button(
        footer, text="Close",
        font=app._btn, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"],
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        padx=14, pady=6, cursor="hand2", command=dlg.destroy,
    ).pack(side="right", padx=(0, 6))
    tk.Button(
        footer, text="📄 Export CSV",
        font=app._btn, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"],
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        padx=14, pady=6, cursor="hand2", command=_export_csv,
    ).pack(side="left")

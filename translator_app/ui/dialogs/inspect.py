"""Inspect SQL dialog — deep-dive view of a Design Doc translation.

Surfaces information that doesn't fit in the design doc itself: which
StringBuffer was the main one, what each Java placeholder resolves to,
where each `?` bind goes, per-column lineage / ambiguity, unknown
identifiers, validation warnings, and the reconstructed SQL.

Layout: a Notebook with a few focused tabs. Tabs that have nothing to
show (e.g. Buffers in single-buffer code) are still created but display
a small "(none)" placeholder so the user knows the section was checked.
"""

import tkinter as tk
from tkinter import ttk

from ...themes import THEMES
from ...designdoc import compute_design_details


def open_inspect_dialog(app):
    """Open (or refocus) the Inspect SQL dialog for the active doc tab."""
    existing = getattr(app, "_inspect_dialog", None)
    if existing and existing.winfo_exists():
        existing.lift()
        existing.focus_force()
        return

    text = app._current_input()
    if not text or not text.strip():
        app._toast.show("No input to inspect", 1100, "info")
        return

    try:
        details = compute_design_details(
            text,
            app.table_index, app.column_index,
            app.rev_table_index, app.rev_column_index,
            schemas=(app._filter_schemas or None),
            tables =(app._filter_tables  or None),
        )
    except Exception as e:
        app._toast.show(f"Inspect failed: {e}", 1500, "danger")
        return

    t = THEMES[app._theme]
    dlg = tk.Toplevel(app)
    app._inspect_dialog = dlg
    dlg.title("Inspect SQL")
    dlg.geometry("980x640")
    dlg.configure(bg=t["bg"])
    dlg.transient(app)

    # Header strip with the SQL type + warnings count
    header = tk.Frame(dlg, bg=t["bg"])
    header.pack(fill="x", padx=14, pady=(12, 6))
    tk.Label(
        header,
        text=f"Statement: {details.get('stype') or '(unknown)'}",
        font=app._ui_b, bg=t["bg"], fg=t["fg"],
    ).pack(side="left")
    n_warn = len(details.get("warnings") or [])
    if n_warn:
        tk.Label(
            header,
            text=f"⚠ {n_warn} warning{'s' if n_warn != 1 else ''}",
            font=app._ui_b, bg=t["bg"], fg=t["warning"],
        ).pack(side="right")

    nb = ttk.Notebook(dlg)
    nb.pack(fill="both", expand=True, padx=14, pady=(2, 8))

    # ── Tab: Overview (stats + warnings)
    _build_overview_tab(nb, details, app, t)
    # ── Tab: Bind positions
    _build_bind_tab(nb, details, app, t)
    # ── Tab: Tokens (column lineage + ambiguous + unknown)
    _build_tokens_tab(nb, details, app, t)
    # ── Tab: Java vars (placeholders)
    _build_java_vars_tab(nb, details, app, t)
    # ── Tab: Buffers (multi-StringBuffer)
    _build_buffers_tab(nb, details, app, t)
    # ── Tab: Reconstructed SQL
    _build_sql_tab(nb, details, app, t)

    # Footer with Close button
    footer = tk.Frame(dlg, bg=t["bg"])
    footer.pack(fill="x", padx=14, pady=(0, 12))
    tk.Button(
        footer, text="Close", font=app._ui_b,
        bg=t["accent"], fg=t["accent_fg"],
        activebackground=t["accent"], activeforeground=t["accent_fg"],
        relief="flat", bd=0, padx=18, pady=6, cursor="hand2",
        command=dlg.destroy,
    ).pack(side="right")

    dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _on_destroy(_e=None):
        if getattr(app, "_inspect_dialog", None) is dlg:
            app._inspect_dialog = None
    dlg.bind("<Destroy>", _on_destroy)


# ── Tab builders ─────────────────────────────────────────────────────────────
def _scrollable_text(parent, t, app, height=12):
    """Read-only Text widget with a vertical scrollbar, themed."""
    frame = tk.Frame(parent, bg=t["bg"])
    sb = tk.Scrollbar(frame, orient="vertical")
    txt = tk.Text(
        frame, height=height, wrap="word", font=app._ui,
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


def _make_treeview(parent, columns, t, app):
    style = ttk.Style()
    style.configure(
        "Inspect.Treeview",
        background=t["surface"], fieldbackground=t["surface"], foreground=t["fg"],
        bordercolor=t["bg"], borderwidth=0, rowheight=24,
    )
    style.configure(
        "Inspect.Treeview.Heading",
        background=t["muted_bg"], foreground=t["fg"],
        relief="flat",
    )
    style.map("Inspect.Treeview", background=[("selected", t["accent"])],
              foreground=[("selected", t["accent_fg"])])
    frame = tk.Frame(parent, bg=t["bg"])
    sb = tk.Scrollbar(frame, orient="vertical")
    tree = ttk.Treeview(
        frame, columns=tuple(c[0] for c in columns), show="headings",
        style="Inspect.Treeview", yscrollcommand=sb.set,
    )
    for cid, label, width in columns:
        tree.heading(cid, text=label)
        tree.column(cid, width=width, anchor="w", stretch=True)
    sb.configure(command=tree.yview, bg=t["bg"], troughcolor=t["bg"], bd=0)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    return frame, tree


def _empty_placeholder(parent, t, app, msg="(none)"):
    f = tk.Frame(parent, bg=t["bg"])
    tk.Label(f, text=msg, bg=t["bg"], fg=t["fg_muted"], font=app._ui).pack(
        padx=20, pady=20, anchor="w"
    )
    return f


def _build_overview_tab(nb, details, app, t):
    frame = tk.Frame(nb, bg=t["bg"])
    nb.add(frame, text="Overview")

    # Stats
    stats = details.get("stats") or []
    stat_frame, stat_txt = _scrollable_text(frame, t, app, height=8)
    stat_frame.pack(fill="x", padx=8, pady=(8, 4))
    if stats:
        stat_txt.insert("1.0", "\n".join(stats))
    else:
        stat_txt.insert("1.0", "(no statistics — parsing failed)")
    stat_txt.configure(state="disabled")

    # Warnings
    warnings = details.get("warnings") or []
    tk.Label(
        frame, text=f"Warnings ({len(warnings)})",
        font=app._ui_b, bg=t["bg"], fg=t["warning" if warnings else "fg_muted"],
    ).pack(anchor="w", padx=10, pady=(8, 2))
    warn_frame, warn_txt = _scrollable_text(frame, t, app, height=8)
    warn_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    if warnings:
        warn_txt.insert("1.0", "\n".join(f"• {w}" for w in warnings))
    else:
        warn_txt.insert("1.0", "(no warnings)")
    warn_txt.configure(state="disabled")


def _build_bind_tab(nb, details, app, t):
    binds = details.get("bind_positions") or []
    nb.add(_make_bind_frame(nb, binds, app, t),
           text=f"? Binds  ({len(binds)})")


def _make_bind_frame(parent, binds, app, t):
    if not binds:
        return _empty_placeholder(parent, t, app, "No `?` placeholders in this SQL.")
    frame, tree = _make_treeview(parent, [
        ("idx", "#", 50),
        ("ctx", "Context",  900),
    ], t, app)
    for b in binds:
        tree.insert("", "end", values=(b["index"], b["context"]))
    return frame


def _build_tokens_tab(nb, details, app, t):
    frame = tk.Frame(nb, bg=t["bg"])
    n_amb = len(details.get("ambiguous") or [])
    n_unk = len(details.get("unknown_tokens") or [])
    nb.add(frame, text=f"Tokens  ({len(details.get('column_lineage') or [])} cols · {n_amb} amb · {n_unk} unk)")

    inner_nb = ttk.Notebook(frame)
    inner_nb.pack(fill="both", expand=True, padx=2, pady=4)

    # Lineage subtab
    lineage_frame = tk.Frame(inner_nb, bg=t["bg"])
    inner_nb.add(lineage_frame, text=f"Column lineage ({len(details.get('column_lineage') or [])})")
    if not details.get("column_lineage"):
        _empty_placeholder(lineage_frame, t, app,
                           "No translatable columns detected.").pack(fill="both", expand=True)
    else:
        f, tree = _make_treeview(lineage_frame, [
            ("phys", "Physical", 180),
            ("logical", "Logical", 200),
            ("amb", "Ambiguous?", 90),
            ("ctx", "Used in", 480),
        ], t, app)
        f.pack(fill="both", expand=True, padx=2, pady=2)
        for row in details["column_lineage"]:
            tree.insert("", "end", values=(
                row["phys"], row["logical"],
                "⚠ yes" if row["ambiguous"] else "",
                row["context"],
            ))

    # Ambiguous subtab
    amb_frame = tk.Frame(inner_nb, bg=t["bg"])
    inner_nb.add(amb_frame, text=f"Ambiguous ({n_amb})")
    if not details.get("ambiguous"):
        _empty_placeholder(amb_frame, t, app,
                           "No ambiguous columns 🎉").pack(fill="both", expand=True)
    else:
        f, txt = _scrollable_text(amb_frame, t, app, height=20)
        f.pack(fill="both", expand=True, padx=2, pady=2)
        lines = []
        for row in details["ambiguous"]:
            lines.append(f"⚠  {row['phys']}  →  chosen: {row['logical']}")
            for g in row["groups"]:
                tcount = len(g["tables"])
                sample = ", ".join(
                    (lt or pt) for _sc, pt, lt in g["tables"][:3]
                )
                more = f"  +{tcount-3} more" if tcount > 3 else ""
                lines.append(f"      → {g['logical']}  ({tcount} tables: {sample}{more})")
            lines.append("")
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")

    # Unknown subtab
    unk_frame = tk.Frame(inner_nb, bg=t["bg"])
    inner_nb.add(unk_frame, text=f"Unknown ({n_unk})")
    if not details.get("unknown_tokens"):
        _empty_placeholder(unk_frame, t, app,
                           "No unknown identifiers — every column resolved.").pack(fill="both", expand=True)
    else:
        f, txt = _scrollable_text(unk_frame, t, app, height=20)
        f.pack(fill="both", expand=True, padx=2, pady=2)
        txt.insert("1.0", "\n".join(details["unknown_tokens"]))
        txt.configure(state="disabled")


def _build_java_vars_tab(nb, details, app, t):
    placeholders = details.get("java_placeholders") or []
    if not placeholders:
        nb.add(_empty_placeholder(nb, t, app,
                                  "No Java placeholders — this SQL is fully literal."),
               text="Java vars  (0)")
        return
    frame, tree = _make_treeview(nb, [
        ("id", "#", 40),
        ("rendered", "Rendered as", 240),
        ("expr", "Java expression", 600),
        ("uses", "Uses", 60),
    ], t, app)
    for p in placeholders:
        tree.insert("", "end", values=(
            p["id"], p["rendered"], p["expr"], p["occurrences"],
        ))
    nb.add(frame, text=f"Java vars  ({len(placeholders)})")


def _build_buffers_tab(nb, details, app, t):
    buffers = details.get("buffers") or []
    if not buffers or len(buffers) <= 1:
        nb.add(_empty_placeholder(nb, t, app,
                                  "Single StringBuffer — no buffer splicing involved."),
               text="Buffers  (1)")
        return
    frame, tree = _make_treeview(nb, [
        ("name",   "Buffer",  180),
        ("role",   "Role",    180),
        ("count",  "Appends", 100),
    ], t, app)
    for b in buffers:
        tree.insert("", "end", values=(
            b["name"],
            "main (returned / used)" if b["is_main"] else "helper (spliced)",
            b["appends"],
        ))
    nb.add(frame, text=f"Buffers  ({len(buffers)})")


def _build_sql_tab(nb, details, app, t):
    sql = details.get("reconstructed_sql") or ""
    frame = tk.Frame(nb, bg=t["bg"])
    nb.add(frame, text="Reconstructed SQL")
    if not sql.strip():
        _empty_placeholder(frame, t, app, "(no SQL extracted)").pack(fill="both", expand=True)
        return
    info = tk.Label(
        frame,
        text="Java placeholders shown as ${expr}; whitespace flattened. "
             "Useful for pasting into a query tool.",
        font=app._small, bg=t["bg"], fg=t["fg_muted"], anchor="w", justify="left",
    )
    info.pack(fill="x", padx=10, pady=(8, 2))
    f, txt = _scrollable_text(frame, t, app, height=24)
    f.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    # Replace the Text widget's font with the app's monospace one for SQL.
    txt.configure(font=app._mono)
    txt.insert("1.0", sql)
    txt.configure(state="disabled")

"""🛠 Tools → Extract SQL from log…

Browse every prepared statement in a `stclibApp.log`, grouped under the
user request that triggered it, with primary business queries surfaced
above infrastructure noise. Click a statement to see its SQL / params /
runnable result; one click sends the result into the translator's input
so Inline Replace or Design Doc render against real values.

Layout
------
    ┌─ Top: log selector + reload ─────────────────────────────────┐
    │ Log:  [▼ recent path…             ] [Browse…] [Reload]       │
    │ 🔎 [filter…  ] ☑ Hide infrastructure   13 stmts · 4 primary  │
    ├─ Statements (treeview, grouped by user action) ──────────────┤
    │ ▼ 11:07:42  PdaHonbuIdoShijiTorikomiAction#search    (5)     │
    │   ★ 189369c1  PdaDataSelectDao    WITH    R_HANBAI…   9 ?    │
    │   · 7d3f4499  RealZaikoDataSelectDao  SELECT  VW_…    2 ?    │
    │   …                                                          │
    ├─ Detail tabs (SQL / Params / Result / Inspect) ──────────────┤
    │ <text widget showing the active tab>                         │
    ├──────────────────────────────────────────────────────────────┤
    │ Class: jp.co.…PdaDataSelectDao                               │
    │ [Direct mode] [Copy result] [Send to translator] [Close]     │
    └──────────────────────────────────────────────────────────────┘

Direct mode is preserved as a fallback for users who already have the
SQL + param blob and don't have the log file handy (e.g. data pasted
from a chat). It swaps the body for a 3-column SQL/params/result view.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

from ...config import save_settings
from ...logsql import (
    DEFAULT_NOISE_PACKAGES,
    DEFAULT_NOISE_TABLES,
    DEFAULT_PRIMARY_THRESHOLD,
    Statement,
    annotate_scores,
    combine_sql_params,
    combine_sql_params_marked,
    count_placeholders,
    extract_subst_ranges,
    group_by_action,
    parse_log,
    parse_params,
    pretty_sql,
    read_log_file,
    tokenize_sql_for_highlight,
)
from ...themes import THEMES


# Maximum recent log paths to keep in the dropdown.
MAX_RECENT_PATHS = 8


def open_log_sql_dialog(app):
    """Open (or refocus) the Extract-SQL-from-log dialog."""
    existing = getattr(app, "_log_sql_dialog", None)
    if existing and existing.winfo_exists():
        existing.lift()
        existing.focus_force()
        return

    t = THEMES[app._theme]
    dlg = tk.Toplevel(app)
    app._log_sql_dialog = dlg
    dlg.title("Extract SQL from log")
    dlg.geometry("1240x820")
    dlg.minsize(960, 600)
    dlg.configure(bg=t["bg"])
    dlg.transient(app)

    # ── Settings (with one-time migration from the v1 single-path shape)
    settings = app._settings.setdefault("log_sql", {})
    if "last_path" in settings and "recent_paths" not in settings:
        # Migrate v1 → v2: promote `last_path` into a recents list.
        last = settings.pop("last_path", "")
        settings["recent_paths"] = [last] if last else []
        settings["active_path"] = last
    settings.setdefault("recent_paths", [])
    settings.setdefault("active_path", "")
    settings.setdefault("primary_packages", [])
    settings.setdefault("noise_packages", list(DEFAULT_NOISE_PACKAGES))
    settings.setdefault("noise_tables",   list(DEFAULT_NOISE_TABLES))
    settings.setdefault("hide_infra", True)
    settings.setdefault("primary_threshold", DEFAULT_PRIMARY_THRESHOLD)

    # ── Mutable state ────────────────────────────────────────────────────
    state = {
        "actions":   [],        # list[Action] from the most recent parse
        "by_iid":    {},        # treeview iid → Statement
        "selected":  None,      # currently-selected Statement
        "direct":    False,     # direct mode (paste, no log file)
        # Sort state — None = preserve init/log order (the default).
        # When set, we sort statements *within* each action group so the
        # action grouping is preserved while users can still rank by
        # score/time/dao/etc.
        "sort_col":  None,      # column id, e.g. "score"
        "sort_desc": True,      # most-useful direction for "score" first
    }

    # ── Top: log file selector + reload ──────────────────────────────────
    # Top header has TWO rows now:
    #   Row 0: chip strip (one chip per recent path) + [+ Add] + [Reload] + [☑ Auto]
    #   Row 1: full active-path display (read-only, monospace) for context
    header = tk.Frame(dlg, bg=t["bg"])
    header.pack(fill="x", padx=14, pady=(12, 4))

    # Row 0 — chip strip + actions
    chip_row = tk.Frame(header, bg=t["bg"])
    chip_row.pack(fill="x")

    chip_strip = tk.Frame(chip_row, bg=t["bg"])
    chip_strip.pack(side="left", fill="x", expand=True)

    # Action buttons live on the right; chip strip flexes between.
    actions_right = tk.Frame(chip_row, bg=t["bg"])
    actions_right.pack(side="right")

    auto_reload_var = tk.BooleanVar(value=bool(settings.get("auto_reload", True)))

    # Row 1 — readout of the active path so the user knows what's loaded.
    path_var = tk.StringVar(value=settings.get("active_path", ""))
    active_path_lbl = tk.Label(
        header, textvariable=path_var, font=app._mono,
        bg=t["bg"], fg=t["fg_muted"], anchor="w",
    )
    active_path_lbl.pack(fill="x", pady=(2, 0))

    def _project_short_name(path: str) -> str:
        """Last 2 path segments before the filename — enough to identify
        a sub-project at a glance, e.g. `mdw_lawmaster…/log`. Falls back
        to the basename for short paths."""
        if not path:
            return "?"
        norm = path.replace("\\", "/").rstrip("/")
        parts = [p for p in norm.split("/") if p]
        if len(parts) >= 3:
            return f".../{parts[-3]}/{parts[-2]}"
        if len(parts) >= 2:
            return f".../{parts[-2]}"
        return parts[-1] if parts else path

    chip_widgets: list[tk.Widget] = []

    def _redraw_chips():
        """Rebuild the chip strip from settings['recent_paths']. The
        active chip is highlighted; click switches; right-click removes."""
        for w in chip_widgets:
            w.destroy()
        chip_widgets.clear()
        recents = settings.get("recent_paths") or []
        active = settings.get("active_path") or ""
        for path in recents:
            is_active = (path == active)
            short = _project_short_name(path)
            chip = tk.Button(
                chip_strip, text=short, font=app._small,
                relief="flat", bd=0, padx=10, pady=3, cursor="hand2",
                bg=(t["accent"] if is_active else t["muted_bg"]),
                fg=(t["accent_fg"] if is_active else t["muted_fg"]),
                activebackground=(t["accent"] if is_active else t["muted_bg"]),
                activeforeground=(t["accent_fg"] if is_active else t["muted_fg"]),
                command=lambda p=path: _switch_to_path(p),
            )
            chip.pack(side="left", padx=(0, 4), pady=2)
            # Tooltip = full path so users can verify which one's which.
            try:
                app._attach_tooltip(chip, path)
            except Exception:
                pass

            # Right-click → small menu: Remove from list / Reveal folder.
            menu = tk.Menu(chip, tearoff=0)
            menu.add_command(
                label="Remove from recent paths",
                command=lambda p=path: _remove_recent(p),
            )
            menu.add_command(
                label="Open containing folder",
                command=lambda p=path: _reveal(p),
            )
            chip.bind("<Button-3>", lambda e, m=menu:
                      m.tk_popup(e.x_root, e.y_root))
            chip_widgets.append(chip)

    def _switch_to_path(path: str):
        path_var.set(path)
        settings["active_path"] = path
        _redraw_chips()
        _on_path_chosen()

    def _remove_recent(path: str):
        recents = settings.get("recent_paths") or []
        if path in recents:
            recents.remove(path)
            settings["recent_paths"] = recents
            if settings.get("active_path") == path:
                settings["active_path"] = recents[0] if recents else ""
                path_var.set(settings["active_path"])
            _save_settings_block()
            _redraw_chips()

    def _reveal(path: str):
        try:
            folder = os.path.dirname(path) or "."
            os.startfile(folder)  # Windows-only; harmless elsewhere if missing
        except Exception:
            _notice(f"Couldn't open folder: {path}", accent=False)

    def _browse():
        initial = os.path.dirname(path_var.get()) if path_var.get() else os.getcwd()
        chosen = filedialog.askopenfilename(
            parent=dlg, title="Choose a stclibApp.log",
            initialdir=initial,
            filetypes=[("Log files", "*.log *.txt"), ("All files", "*.*")],
        )
        if chosen:
            _switch_to_path(chosen)

    # Right-side action buttons
    tk.Button(
        actions_right, text="+ Add log", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=3, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=_browse,
    ).pack(side="left", padx=(0, 4))

    reload_btn = tk.Button(
        actions_right, text="↻ Reload", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=10, pady=3, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=lambda: _on_path_chosen(force=True),
    )
    reload_btn.pack(side="left", padx=(0, 4))
    try:
        app._attach_tooltip(
            reload_btn,
            "Force re-parse the active log right now.\n"
            "Auto-reload will also pick up file changes within ~1.5s "
            "while this dialog is open.",
        )
    except Exception:
        pass

    auto_chk = tk.Checkbutton(
        actions_right, text="Auto", variable=auto_reload_var,
        bg=t["bg"], fg=t["fg"], selectcolor=t["surface"],
        activebackground=t["bg"], activeforeground=t["fg"],
        font=app._small, bd=0, highlightthickness=0,
        command=lambda: _on_auto_reload_toggle(),
    )
    auto_chk.pack(side="left")
    try:
        app._attach_tooltip(
            auto_chk,
            "When ON, the dialog watches the active log file's mtime\n"
            "while open — any change re-parses automatically.\n"
            "Off = manual Reload only.",
        )
    except Exception:
        pass

    def _on_auto_reload_toggle():
        settings["auto_reload"] = bool(auto_reload_var.get())
        _save_settings_block()
        if auto_reload_var.get():
            _schedule_auto_reload()
        # If toggled off, the next scheduled tick will see the flag and bail.

    # ── Filter bar: search + Hide infrastructure + counts ────────────────
    filter_bar = tk.Frame(dlg, bg=t["bg"])
    filter_bar.pack(fill="x", padx=14, pady=(2, 6))

    # Row 0: 🔎 search + Hide infrastructure + counts (right)
    filter_top = tk.Frame(filter_bar, bg=t["bg"])
    filter_top.pack(fill="x")
    tk.Label(filter_top, text="🔎", font=app._ui,
             bg=t["bg"], fg=t["fg_muted"]).pack(side="left")
    search_var = tk.StringVar()
    search_entry = tk.Entry(
        filter_top, textvariable=search_var, font=app._ui,
        bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", bd=0,
    )
    search_entry.pack(side="left", fill="x", expand=True, padx=(6, 8), ipady=4)

    hide_infra_var = tk.BooleanVar(value=bool(settings.get("hide_infra", True)))
    hide_infra_chk = tk.Checkbutton(
        filter_top, text="Hide infrastructure", variable=hide_infra_var,
        bg=t["bg"], fg=t["fg"], selectcolor=t["surface"],
        activebackground=t["bg"], activeforeground=t["fg"],
        font=app._ui, bd=0, highlightthickness=0,
    )
    hide_infra_chk.pack(side="left", padx=(0, 8))

    counts_lbl = tk.Label(
        filter_top, text="", font=app._small,
        bg=t["bg"], fg=t["fg_muted"],
    )
    counts_lbl.pack(side="right")

    # Row 1: statement-type chips. Each chip is a toggle; defaults all on.
    # `OTHER` covers anything not in the explicit set (CALL/MERGE/TRUNCATE/
    # CREATE/etc.) so users always have a way to see the long tail.
    type_row = tk.Frame(filter_bar, bg=t["bg"])
    type_row.pack(fill="x", pady=(4, 0))
    tk.Label(type_row, text="Type:", font=app._small,
             bg=t["bg"], fg=t["fg_muted"]).pack(side="left", padx=(0, 6))

    _PRIMARY_TYPES = ("SELECT", "INSERT", "UPDATE", "DELETE")
    type_filter_state = settings.get("type_filter") or {}
    type_vars: dict[str, tk.BooleanVar] = {}

    def _on_type_toggle():
        for k, v in type_vars.items():
            type_filter_state[k] = bool(v.get())
        settings["type_filter"] = type_filter_state
        _save_settings_block()
        if state["actions"]:
            _render_tree()

    for label in _PRIMARY_TYPES + ("OTHER",):
        # default = enabled if not previously stored, else honour saved state
        v = tk.BooleanVar(value=bool(type_filter_state.get(label, True)))
        type_vars[label] = v
        chk = tk.Checkbutton(
            type_row, text=label, variable=v,
            bg=t["bg"], fg=t["fg"], selectcolor=t["surface"],
            activebackground=t["bg"], activeforeground=t["fg"],
            font=app._small, bd=0, highlightthickness=0,
            command=_on_type_toggle,
        )
        chk.pack(side="left", padx=(0, 6))

    # "All / None" quick-toggles
    def _set_all_types(value: bool):
        for v in type_vars.values():
            v.set(value)
        _on_type_toggle()
    tk.Button(type_row, text="All", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=8, pady=1, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=lambda: _set_all_types(True)).pack(side="left", padx=(8, 0))
    tk.Button(type_row, text="None", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=8, pady=1, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=lambda: _set_all_types(False)).pack(side="left", padx=(4, 0))

    # ── Body: PanedWindow (top = treeview list, bottom = detail tabs) ────
    body = tk.PanedWindow(
        dlg, orient="vertical", bg=t["bg"], bd=0, sashwidth=6,
        sashrelief="flat", showhandle=False,
    )
    body.pack(fill="both", expand=True, padx=14, pady=(0, 4))

    # Top pane — treeview of actions/statements
    list_pane = tk.Frame(body, bg=t["bg"])
    style = ttk.Style()
    style.configure(
        "Log.Treeview",
        background=t["surface"], fieldbackground=t["surface"], foreground=t["fg"],
        bordercolor=t["bg"], borderwidth=0, rowheight=22,
    )
    style.configure(
        "Log.Treeview.Heading",
        background=t["muted_bg"], foreground=t["fg"], relief="flat",
    )
    style.map("Log.Treeview",
              background=[("selected", t["accent"])],
              foreground=[("selected", t["accent_fg"])])
    tree_sb = tk.Scrollbar(list_pane, orient="vertical")
    tree = ttk.Treeview(
        list_pane,
        columns=("ts", "id", "kind", "dao", "type", "tables", "params", "score"),
        show="tree headings",
        style="Log.Treeview",
        yscrollcommand=tree_sb.set,
    )
    # Heading text and sort defaults. `sort_default_desc` says which
    # direction the user *most likely* wants on the first click — score
    # and `?` count are typically scanned high-to-low; everything else
    # alphabetically/chronologically ascending.
    _COL_HEADINGS = (
        ("#0",     "",      None),     # tree-disclosure column
        ("ts",     "Time",  False),
        ("id",     "ID",    False),
        ("kind",   "",      None),     # ★/· badge — not sortable
        ("dao",    "DAO",   False),
        ("type",   "Type",  False),
        ("tables", "Tables",False),
        ("params", "?",     True),
        ("score",  "Score", True),
    )
    def _on_heading_click(col_id: str, default_desc: bool):
        # Click same column → flip direction. New column → default dir.
        if state["sort_col"] == col_id:
            state["sort_desc"] = not state["sort_desc"]
        else:
            state["sort_col"]  = col_id
            state["sort_desc"] = default_desc
        _refresh_heading_indicators()
        _render_tree()
    for col_id, label, default_desc in _COL_HEADINGS:
        if default_desc is None:
            tree.heading(col_id, text=label)
        else:
            tree.heading(
                col_id, text=label,
                command=lambda c=col_id, d=default_desc: _on_heading_click(c, d),
            )

    def _refresh_heading_indicators():
        """Append a small ▲/▼ to the active sort column's heading."""
        for col_id, label, default_desc in _COL_HEADINGS:
            if default_desc is None:
                continue
            if state["sort_col"] == col_id:
                arrow = " ▼" if state["sort_desc"] else " ▲"
                tree.heading(col_id, text=label + arrow)
            else:
                tree.heading(col_id, text=label)
    tree.column("#0",     width=18,  stretch=False, anchor="w")
    tree.column("ts",     width=70,  stretch=False, anchor="w")
    tree.column("id",     width=80,  stretch=False, anchor="w")
    tree.column("kind",   width=24,  stretch=False, anchor="center")
    tree.column("dao",    width=240, stretch=False, anchor="w")
    tree.column("type",   width=58,  stretch=False, anchor="w")
    tree.column("tables", width=240, stretch=True,  anchor="w")
    tree.column("params", width=40,  stretch=False, anchor="e")
    tree.column("score",  width=50,  stretch=False, anchor="e")
    tree_sb.configure(command=tree.yview, bg=t["bg"], troughcolor=t["bg"], bd=0)
    tree.pack(side="left", fill="both", expand=True)
    tree_sb.pack(side="right", fill="y")
    body.add(list_pane, minsize=180, height=320)

    # Bottom pane — detail notebook (SQL / Params / Result)
    detail_pane = tk.Frame(body, bg=t["bg"])
    nb = ttk.Notebook(detail_pane)
    nb.pack(fill="both", expand=True)

    def _make_text_tab(parent_label):
        frame = tk.Frame(nb, bg=t["bg"])
        box = scrolledtext.ScrolledText(
            frame, wrap=tk.NONE, font=app._mono, undo=True,
            bg=t["output_bg"], fg=t["fg"], insertbackground=t["insert"],
            relief="flat", borderwidth=0,
        )
        box.pack(fill="both", expand=True)
        nb.add(frame, text=parent_label)
        return box

    # Result tab gets its own toolbar (📋 Copy + ☑ Auto-copy) so the
    # primary action is right next to the content. The other tabs use the
    # plain helper.
    auto_copy_var = tk.BooleanVar(value=bool(settings.get("auto_copy", False)))

    result_frame = tk.Frame(nb, bg=t["bg"])
    result_toolbar = tk.Frame(result_frame, bg=t["bg"])
    result_toolbar.pack(fill="x", pady=(0, 4))

    copy_result_btn = tk.Button(
        result_toolbar, text="📋  Copy", font=app._small,
        relief="flat", bd=0, bg=t["muted_bg"], fg=t["muted_fg"],
        padx=10, pady=3, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=lambda: _copy_result(),
    )
    copy_result_btn.pack(side="left")
    try:
        app._attach_tooltip(
            copy_result_btn,
            "Copy the prettified result SQL to the clipboard.",
        )
    except Exception:
        pass

    auto_copy_chk = tk.Checkbutton(
        result_toolbar, text="Auto-copy", variable=auto_copy_var,
        bg=t["bg"], fg=t["fg"], selectcolor=t["surface"],
        activebackground=t["bg"], activeforeground=t["fg"],
        font=app._small, bd=0, highlightthickness=0,
        command=lambda: _on_auto_copy_toggle(),
    )
    auto_copy_chk.pack(side="left", padx=(8, 0))
    try:
        app._attach_tooltip(
            auto_copy_chk,
            "When ON, every statement you click in the list above is\n"
            "copied to the clipboard automatically — paste it straight\n"
            "into your DB tool without touching this dialog.",
        )
    except Exception:
        pass

    result_box = scrolledtext.ScrolledText(
        result_frame, wrap=tk.NONE, font=app._mono, undo=True,
        bg=t["output_bg"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", borderwidth=0,
    )
    result_box.pack(fill="both", expand=True)
    nb.add(result_frame, text="Result (filled)")

    # Highlight tag styling. Light-vs-dark-aware via the active theme.
    # `subst_value` colors values that came from bound params so users can
    # see at a glance "this came from a `?`, not from the original SQL."
    def _is_dark():
        try:
            return THEMES[app._theme].get("name", "") == "dark" or \
                   t.get("bg", "#fff").lower() in ("#1e1e1e", "#202225", "#0d1117")
        except Exception:
            return False
    if _is_dark():
        kw_color, str_color, num_color, com_color, sub_color = (
            "#c586c0", "#ce9178", "#b5cea8", "#6a9955", "#4ec9b0",
        )
    else:
        kw_color, str_color, num_color, com_color, sub_color = (
            "#0033b3", "#067d17", "#1750eb", "#8c8c8c", "#d36a00",
        )
    result_box.tag_configure("hl_keyword", foreground=kw_color)
    result_box.tag_configure("hl_string",  foreground=str_color)
    result_box.tag_configure("hl_number",  foreground=num_color)
    result_box.tag_configure("hl_comment", foreground=com_color)
    # Substituted values get a strong tint AND a subtle background so they
    # stand out even when they happen to be a string literal (which would
    # otherwise share the string color). underline=False keeps them clean.
    result_box.tag_configure(
        "hl_subst",
        foreground=sub_color, background=t.get("muted_bg", "#f0f0f0"),
    )

    # SQL / Params tabs are plain.
    sql_box    = _make_text_tab("SQL (with ?)")
    params_box = _make_text_tab("Params")
    for box in (sql_box, params_box, result_box):
        box.configure(state="disabled")
    body.add(detail_pane, minsize=180, height=380)

    # ── Status bar + actions ────────────────────────────────────────────
    status = tk.Frame(dlg, bg=t["bg"])
    status.pack(fill="x", padx=14, pady=(2, 2))
    class_lbl = tk.Label(
        status, text="Class:  —", font=app._small,
        bg=t["bg"], fg=t["fg_muted"], anchor="w",
    )
    class_lbl.pack(side="left")
    notice_lbl = tk.Label(
        status, text="", font=app._small,
        bg=t["bg"], fg=t["accent"], anchor="e",
    )
    notice_lbl.pack(side="right")

    actions = tk.Frame(dlg, bg=t["bg"])
    actions.pack(fill="x", padx=14, pady=(2, 12))

    def _btn(parent, text, command, accent=False):
        return tk.Button(
            parent, text=text, font=app._btn,
            bg=t["accent"] if accent else t["muted_bg"],
            fg=t["accent_fg"] if accent else t["muted_fg"],
            activebackground=t["accent"] if accent else t["muted_bg"],
            activeforeground=t["accent_fg"] if accent else t["muted_fg"],
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
            command=command,
        )

    direct_btn   = _btn(actions, "Direct mode…", lambda: _toggle_direct_mode())
    direct_btn.pack(side="left", padx=(0, 6))

    _btn(actions, "Close", dlg.destroy).pack(side="right")
    _btn(actions, "Send to translator input",
         lambda: _send_to_translator(), accent=True).pack(side="right", padx=(0, 6))
    _btn(actions, "Copy result", lambda: _copy_result()).pack(side="right", padx=(0, 6))

    # ── Helpers ─────────────────────────────────────────────────────────
    def _notice(msg, accent=True):
        notice_lbl.configure(
            text=msg,
            fg=(t["accent"] if accent else t["fg_muted"]),
        )

    def _set_text(box, content):
        was_disabled = (box.cget("state") == "disabled")
        if was_disabled:
            box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", content or "")
        if was_disabled:
            box.configure(state="disabled")

    def _save_settings_block():
        try:
            save_settings(app._settings)
        except Exception:
            pass

    def _push_recent(path):
        if not path:
            return
        recents = settings.get("recent_paths") or []
        if path in recents:
            recents.remove(path)
        recents.insert(0, path)
        settings["recent_paths"] = recents[:MAX_RECENT_PATHS]
        settings["active_path"] = path
        _redraw_chips()
        _save_settings_block()

    # ── Render the treeview ─────────────────────────────────────────────
    def _sort_key(s: Statement, col: str):
        """Stable sort key for `Statement` by column id. Numeric columns
        return ints/floats so `reverse=True` orders correctly; text
        columns lower-case for case-insensitive sort."""
        if col == "score":
            return s.score
        if col == "params":
            return len(s.params)
        if col == "ts":
            return s.timestamp or ""
        if col == "id":
            return s.id or ""
        if col == "dao":
            return (s.dao_short or "").lower()
        if col == "type":
            return s.statement_type or ""
        if col == "tables":
            # Sort by primary table — usually the most informative.
            return (s.target_tables[0] if s.target_tables else "").lower()
        return ""

    def _render_tree():
        """Rebuild the treeview from state['actions'] honouring search +
        Hide-infrastructure filters. Reselect the previously-selected
        statement when possible."""
        prev_sel_id = state["selected"].id if state["selected"] else None
        tree.delete(*tree.get_children())
        state["by_iid"].clear()

        q = search_var.get().strip().lower()
        hide = bool(hide_infra_var.get())
        threshold = int(settings.get("primary_threshold", DEFAULT_PRIMARY_THRESHOLD))

        # Snapshot the type filter so we can include/exclude per statement.
        # `enabled_types` is the set of statement-type strings the user
        # currently wants to see; `other_enabled` means non-primary types
        # (MERGE/TRUNCATE/CREATE/CALL/etc.) pass.
        enabled_types: set[str] = {
            k for k, v in type_vars.items()
            if k != "OTHER" and v.get()
        }
        other_enabled = bool(type_vars.get("OTHER") and type_vars["OTHER"].get())

        n_total = 0
        n_primary = 0

        for action in state["actions"]:
            visible_kids: list[Statement] = []
            for s in action.statements:
                n_total += 1
                if s.is_primary:
                    n_primary += 1
                # Hide-infra filter — keep primary only.
                if hide and not s.is_primary:
                    continue
                # Statement-type filter — chip-driven.
                stype = s.statement_type or ""
                if stype in _PRIMARY_TYPES:
                    if stype not in enabled_types:
                        continue
                else:
                    if not other_enabled:
                        continue
                # Search filter — match against id / dao / tables / sql.
                if q:
                    hay = " ".join((
                        s.id, s.dao_short, s.statement_type,
                        " ".join(s.target_tables), s.sql[:200],
                    )).lower()
                    if q not in hay:
                        continue
                visible_kids.append(s)
            if not visible_kids:
                continue
            # Apply column sort within this action group, if any.
            if state["sort_col"]:
                visible_kids.sort(
                    key=lambda s: _sort_key(s, state["sort_col"]),
                    reverse=state["sort_desc"],
                )
            # Action header row.
            n_pri = sum(1 for k in visible_kids if k.is_primary)
            ts_hm = (action.timestamp[-8:] if action.timestamp else "")
            header_text = f"  {action.label}  ({len(visible_kids)} queries · {n_pri} ★)"
            parent = tree.insert("", "end", text="▼",
                                 values=(ts_hm, "", "", header_text, "", "", "", ""),
                                 open=True, tags=("action_row",))
            for s in visible_kids:
                kind = "★" if s.is_primary else "·"
                ts_hm = (s.timestamp[-8:] if s.timestamp else "")
                tables = ", ".join(s.target_tables[:3])
                if len(s.target_tables) > 3:
                    tables += f"  +{len(s.target_tables) - 3}"
                row_iid = tree.insert(
                    parent, "end", text="",
                    values=(ts_hm, s.id, kind, s.dao_short or "(unknown)",
                            s.statement_type, tables,
                            str(len(s.params)) if s.params else ("—" if not s.params_raw else "0"),
                            str(s.score)),
                    tags=("primary_row" if s.is_primary else "stmt_row",),
                )
                state["by_iid"][row_iid] = s
                if prev_sel_id and s.id == prev_sel_id:
                    tree.selection_set(row_iid)
                    tree.see(row_iid)

        # Style tag colours.
        tree.tag_configure("action_row",  foreground=t["fg"], background=t["muted_bg"])
        tree.tag_configure("primary_row", foreground=t["accent"])
        tree.tag_configure("stmt_row",    foreground=t["fg_muted"])

        # Counts label.
        suffix = f" · matching '{q}'" if q else ""
        if hide:
            counts_lbl.configure(
                text=f"Showing {n_primary} primary  ·  {n_total - n_primary} hidden{suffix}",
            )
        else:
            counts_lbl.configure(
                text=f"{n_total} statements  ·  {n_primary} primary{suffix}",
            )

    # ── Result-pane rendering with tags ────────────────────────────────
    _HL_TAGS = ("hl_keyword", "hl_string", "hl_number", "hl_comment", "hl_subst")

    def _render_result_highlighted(text: str, subst_ranges: list[tuple[int, int]]):
        """Render `text` into the result_box with SQL syntax highlighting
        (keyword/string/number/comment) plus a substituted-value tag
        applied to each (start, end) range in `subst_ranges`."""
        result_box.configure(state="normal")
        # Drop the old tag spans before deleting text — Tk preserves tag
        # objects across delete/insert which would leak older highlights
        # otherwise.
        for tag in _HL_TAGS:
            result_box.tag_remove(tag, "1.0", "end")
        result_box.delete("1.0", "end")
        if not text:
            result_box.configure(state="disabled")
            return
        result_box.insert("1.0", text)
        # Syntax highlighting first.
        for start, end, kind in tokenize_sql_for_highlight(text):
            result_box.tag_add(f"hl_{kind}",
                               f"1.0 + {start} chars",
                               f"1.0 + {end} chars")
        # Substituted-value highlighting on top — its background tint
        # plus accent foreground stays visible even when it overlaps a
        # string-literal token from the syntax pass.
        for start, end in subst_ranges:
            result_box.tag_add("hl_subst",
                               f"1.0 + {start} chars",
                               f"1.0 + {end} chars")
        result_box.configure(state="disabled")

    # ── Statement loading ──────────────────────────────────────────────
    def _load_statement(stmt: Statement | None):
        state["selected"] = stmt
        if stmt is None:
            class_lbl.configure(text="Class:  —")
            for box in (sql_box, params_box, result_box):
                _set_text(box, "")
            return
        class_lbl.configure(
            text=f"Class:  {stmt.fqcn or '(unknown)'}   ·   id={stmt.id}",
        )
        _set_text(sql_box, stmt.sql)
        _set_text(params_box, stmt.params_raw)
        # The Result tab is the main thing — apply the lightweight
        # prettifier so a 933-char SQL renders as something the eye can
        # actually scan instead of one continuous line, then add syntax
        # highlighting + substituted-value highlighting on top.
        marked, _ = combine_sql_params_marked(stmt.sql, stmt.params)
        marked_pretty = pretty_sql(marked)
        pretty, subst_ranges = extract_subst_ranges(marked_pretty)
        _render_result_highlighted(pretty, subst_ranges)
        n_q = count_placeholders(stmt.sql)
        n_p = len(stmt.params)
        if n_q != n_p and not (n_q == 0 and not stmt.params_raw):
            _notice(f"⚠ {n_q} ?s vs {n_p} params — counts don't match", accent=False)
        else:
            _notice(f"Loaded id={stmt.id}  ·  {n_p} param(s) bound")

        # Auto-copy: if the toggle is on, push the prettified result to
        # the clipboard immediately. A short toast confirms (so the
        # user knows it happened — silent clipboard writes feel spooky).
        if auto_copy_var.get() and pretty.strip():
            try:
                app.clipboard_clear()
                app.clipboard_append(pretty)
                app._toast.show(f"Auto-copied id={stmt.id}", 900, "info")
            except Exception:
                pass

    def _on_auto_copy_toggle():
        settings["auto_copy"] = bool(auto_copy_var.get())
        _save_settings_block()
        # If turning on AND a statement is already loaded, copy it now —
        # the user expects the toggle to take effect for what they're
        # currently looking at, not just future selections.
        if auto_copy_var.get() and state.get("selected"):
            text = result_box.get("1.0", "end-1c")
            if text.strip():
                try:
                    app.clipboard_clear()
                    app.clipboard_append(text)
                    app._toast.show(
                        f"Auto-copy on — copied id={state['selected'].id}",
                        1100, "success",
                    )
                except Exception:
                    pass

    def _on_tree_select(_evt=None):
        sel = tree.selection()
        if not sel:
            return
        stmt = state["by_iid"].get(sel[0])
        if stmt is not None:
            _load_statement(stmt)

    tree.bind("<<TreeviewSelect>>", _on_tree_select)

    # ── Path-change → parse + score + render ───────────────────────────
    def _on_path_chosen(force: bool = False, _from_auto: bool = False):
        path = path_var.get().strip()
        if not path:
            _notice("Pick a log file to load", accent=False)
            return
        if not os.path.exists(path):
            _notice(f"File not found: {path}", accent=False)
            return
        text = read_log_file(path)
        if not text:
            _notice("Log file is empty or unreadable", accent=False)
            return
        # Remember which statement was selected so we can re-select after
        # an auto-reload (the user is likely to be reading a specific
        # query when the file ticks; losing their place would be jarring).
        prev_sel_id = state["selected"].id if state["selected"] else None
        stmts = parse_log(text)
        annotate_scores(
            stmts,
            primary_packages=settings.get("primary_packages") or (),
            noise_packages=settings.get("noise_packages") or DEFAULT_NOISE_PACKAGES,
            noise_tables=settings.get("noise_tables") or DEFAULT_NOISE_TABLES,
        )
        actions = group_by_action(stmts)
        state["actions"] = actions
        state["selected"] = None
        _render_tree()
        _push_recent(path)
        _capture_mtime()

        # Re-select previously-selected statement if it still exists —
        # particularly helpful for auto-reloads.
        if prev_sel_id:
            for iid, sx in state["by_iid"].items():
                if sx.id == prev_sel_id:
                    tree.selection_set(iid)
                    tree.see(iid)
                    if _from_auto:
                        try:
                            app._toast.show(
                                "Log updated — re-parsed", 900, "info",
                            )
                        except Exception:
                            pass
                    return

        # Auto-select the first primary statement if any — saves a click.
        for a in actions:
            for s in a.statements:
                if s.is_primary:
                    for iid, sx in state["by_iid"].items():
                        if sx is s:
                            tree.selection_set(iid)
                            tree.see(iid)
                            if _from_auto:
                                try:
                                    app._toast.show(
                                        "Log updated — re-parsed", 900, "info",
                                    )
                                except Exception:
                                    pass
                            return
        n_stmts = sum(len(a.statements) for a in actions)
        if _from_auto:
            try:
                app._toast.show(
                    f"Log updated — {n_stmts} statements", 900, "info",
                )
            except Exception:
                pass
        else:
            _notice(f"Loaded {n_stmts} statements")

    # Search + filter changes → re-render only (no re-parse)
    def _on_search(*_):
        if state["actions"]:
            _render_tree()
    search_var.trace_add("write", _on_search)

    def _on_hide_toggle():
        settings["hide_infra"] = bool(hide_infra_var.get())
        _save_settings_block()
        if state["actions"]:
            _render_tree()
    hide_infra_chk.configure(command=_on_hide_toggle)

    # Esc inside search clears it; otherwise dialog closes.
    def _esc_in_search(_e):
        if search_var.get():
            search_var.set("")
            return "break"
        return None
    search_entry.bind("<Escape>", _esc_in_search)

    # ── Action button handlers ─────────────────────────────────────────
    def _current_result_text() -> str:
        """Whichever Result pane is currently visible — what the user sees
        is what they copy / send. In browse mode that's the prettified
        result; in direct mode it's whatever the Process button produced."""
        if state["direct"]:
            return _get_direct_result()
        return result_box.get("1.0", "end-1c") if state["selected"] else ""

    def _copy_result():
        text = _current_result_text()
        if not text.strip():
            _notice("Nothing to copy yet", accent=False)
            return
        try:
            app.clipboard_clear()
            app.clipboard_append(text)
            app._toast.show("Result copied to clipboard", 1100, "success")
        except Exception:
            _notice("Clipboard copy failed", accent=False)

    def _send_to_translator():
        text = _current_result_text()
        if not text.strip():
            _notice("Nothing to send — pick a statement first", accent=False)
            return
        try:
            app.input_box.configure(state="normal")
            app.input_box.delete("1.0", "end")
            app.input_box.insert("1.0", text)
            app.on_translate()
            app._toast.show("Sent SQL into translator input", 1300, "success")
            _notice("Sent into translator input", accent=True)
        except Exception:
            _notice("Couldn't reach translator input", accent=False)

    # ── Direct mode (paste SQL + params, no log file) ─────────────────
    direct_panel = tk.Frame(dlg, bg=t["bg"])
    direct_panel.columnconfigure(0, weight=2, uniform="dcols")
    direct_panel.columnconfigure(1, weight=1, uniform="dcols")
    direct_panel.columnconfigure(2, weight=2, uniform="dcols")
    direct_panel.rowconfigure(1, weight=1)

    for col, lbl in enumerate(("SQL (with ?)", "PARAM(S)", "RESULT")):
        tk.Label(direct_panel, text=lbl, font=app._ui_b,
                 bg=t["bg"], fg=t["fg"], anchor="w").grid(
            row=0, column=col, sticky="ew",
            padx=(0 if col == 0 else 4, 4 if col != 2 else 0),
            pady=(0, 4),
        )

    def _make_direct_text(col):
        frame = tk.Frame(direct_panel, bg=t["bg"])
        frame.grid(row=1, column=col, sticky="nsew",
                   padx=(0 if col == 0 else 4, 4 if col != 2 else 0))
        box = scrolledtext.ScrolledText(
            frame, wrap=tk.NONE, font=app._mono, undo=True,
            bg=t["output_bg"], fg=t["fg"], insertbackground=t["insert"],
            relief="flat", borderwidth=0,
        )
        box.pack(fill="both", expand=True)
        return box

    direct_sql    = _make_direct_text(0)
    direct_params = _make_direct_text(1)
    direct_result = _make_direct_text(2)
    direct_result.configure(state="disabled")

    direct_actions = tk.Frame(direct_panel, bg=t["bg"])
    direct_actions.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))

    def _direct_process():
        sql = direct_sql.get("1.0", "end-1c")
        params = parse_params(direct_params.get("1.0", "end-1c"))
        _set_text(direct_result, pretty_sql(combine_sql_params(sql, params)))
        n_q = count_placeholders(sql)
        if n_q != len(params):
            _notice(f"⚠ {n_q} ?s vs {len(params)} params — counts don't match", accent=False)
        else:
            _notice(f"Combined {len(params)} param(s) into SQL")

    def _get_direct_result():
        return direct_result.get("1.0", "end-1c")

    _btn(direct_actions, "Process", _direct_process, accent=True).pack(side="left")
    _btn(direct_actions, "Clear",
         lambda: [_set_text(direct_sql, ""), _set_text(direct_params, ""),
                  _set_text(direct_result, "")]).pack(side="left", padx=(6, 0))

    def _toggle_direct_mode():
        state["direct"] = not state["direct"]
        if state["direct"]:
            body.pack_forget()
            filter_bar.pack_forget()
            direct_panel.pack(fill="both", expand=True, padx=14, pady=(0, 6),
                              before=status)
            direct_btn.configure(text="Browse mode…")
            _notice("Direct mode — paste SQL + params, click Process", accent=False)
        else:
            direct_panel.pack_forget()
            filter_bar.pack(fill="x", padx=14, pady=(2, 6),
                            before=body if body.winfo_manager() else status)
            body.pack(fill="both", expand=True, padx=14, pady=(0, 4),
                      before=status)
            direct_btn.configure(text="Direct mode…")
            _notice("Browse mode — pick a log to load")

    # ── Auto-reload (mtime poll) ────────────────────────────────────────
    # Track the active file's last-modified timestamp so we can re-parse
    # when the server appends new entries while the dialog is open.
    state["mtime"] = 0.0
    AUTO_RELOAD_MS = 1500  # 1.5 s — fast enough to feel "live", slow
                           # enough that reading a 10 MB log is fine.

    def _capture_mtime():
        path = path_var.get().strip()
        try:
            state["mtime"] = os.path.getmtime(path) if path and os.path.exists(path) else 0.0
        except OSError:
            state["mtime"] = 0.0

    def _schedule_auto_reload():
        # Re-arm; the tick checks the var so toggling Auto off stops it.
        dlg.after(AUTO_RELOAD_MS, _auto_reload_tick)

    def _auto_reload_tick():
        # Bail if dialog closed or auto turned off — no polling churn.
        try:
            if not dlg.winfo_exists():
                return
        except tk.TclError:
            return
        if not auto_reload_var.get():
            return
        path = path_var.get().strip()
        if path and os.path.exists(path):
            try:
                current = os.path.getmtime(path)
            except OSError:
                current = 0.0
            if current and current > state.get("mtime", 0.0):
                # File touched since last parse — re-load and update the
                # tree, preserving selection. Show a quick toast so the
                # user knows it happened (otherwise it feels magical).
                state["mtime"] = current
                _on_path_chosen(force=True, _from_auto=True)
        _schedule_auto_reload()

    # ── Initial state ───────────────────────────────────────────────────
    _redraw_chips()
    if path_var.get():
        _capture_mtime()
        # Schedule the first parse after the dialog finishes mapping so the
        # tree dims/columns settle before the first render.
        dlg.after(50, lambda: _on_path_chosen())
    else:
        _notice("Pick a stclibApp.log to begin (or click 'Direct mode' to paste)",
                accent=False)
    if auto_reload_var.get():
        _schedule_auto_reload()

    # ── Bindings + lifecycle ───────────────────────────────────────────
    dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _on_destroy(_e=None):
        if getattr(app, "_log_sql_dialog", None) is dlg:
            app._log_sql_dialog = None
    dlg.bind("<Destroy>", _on_destroy)

    search_entry.focus_set()

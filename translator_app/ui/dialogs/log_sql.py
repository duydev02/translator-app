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
    count_placeholders,
    group_by_action,
    parse_log,
    parse_params,
    read_log_file,
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
    }

    # ── Top: log file selector + reload ──────────────────────────────────
    header = tk.Frame(dlg, bg=t["bg"])
    header.pack(fill="x", padx=14, pady=(12, 4))
    header.columnconfigure(1, weight=1)

    tk.Label(header, text="Log:", font=app._ui_b,
             bg=t["bg"], fg=t["fg"]).grid(row=0, column=0, sticky="w", padx=(0, 8))

    path_var = tk.StringVar(value=settings.get("active_path", ""))
    path_combo = ttk.Combobox(
        header, textvariable=path_var, font=app._mono,
        values=settings.get("recent_paths", []),
    )
    path_combo.grid(row=0, column=1, sticky="ew", ipady=2)

    def _browse():
        initial = os.path.dirname(path_var.get()) if path_var.get() else os.getcwd()
        chosen = filedialog.askopenfilename(
            parent=dlg, title="Choose a stclibApp.log",
            initialdir=initial,
            filetypes=[("Log files", "*.log *.txt"), ("All files", "*.*")],
        )
        if chosen:
            path_var.set(chosen)
            _on_path_chosen()

    tk.Button(
        header, text="Browse…", font=app._btn, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=12, pady=4, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=_browse,
    ).grid(row=0, column=2, padx=(8, 0))

    tk.Button(
        header, text="Reload", font=app._btn, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=12, pady=4, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=lambda: _on_path_chosen(force=True),
    ).grid(row=0, column=3, padx=(6, 0))

    # ── Filter bar: search + Hide infrastructure + counts ────────────────
    filter_bar = tk.Frame(dlg, bg=t["bg"])
    filter_bar.pack(fill="x", padx=14, pady=(2, 6))

    tk.Label(filter_bar, text="🔎", font=app._ui,
             bg=t["bg"], fg=t["fg_muted"]).pack(side="left")
    search_var = tk.StringVar()
    search_entry = tk.Entry(
        filter_bar, textvariable=search_var, font=app._ui,
        bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", bd=0,
    )
    search_entry.pack(side="left", fill="x", expand=True, padx=(6, 8), ipady=4)

    hide_infra_var = tk.BooleanVar(value=bool(settings.get("hide_infra", True)))
    hide_infra_chk = tk.Checkbutton(
        filter_bar, text="Hide infrastructure", variable=hide_infra_var,
        bg=t["bg"], fg=t["fg"], selectcolor=t["surface"],
        activebackground=t["bg"], activeforeground=t["fg"],
        font=app._ui, bd=0, highlightthickness=0,
    )
    hide_infra_chk.pack(side="left", padx=(0, 8))

    counts_lbl = tk.Label(
        filter_bar, text="", font=app._small,
        bg=t["bg"], fg=t["fg_muted"],
    )
    counts_lbl.pack(side="right")

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
    tree.heading("#0",     text="")
    tree.heading("ts",     text="Time")
    tree.heading("id",     text="ID")
    tree.heading("kind",   text="")
    tree.heading("dao",    text="DAO")
    tree.heading("type",   text="Type")
    tree.heading("tables", text="Tables")
    tree.heading("params", text="?")
    tree.heading("score",  text="Score")
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

    sql_box    = _make_text_tab("SQL (with ?)")
    params_box = _make_text_tab("Params")
    result_box = _make_text_tab("Result (filled)")
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
        path_combo["values"] = settings["recent_paths"]
        _save_settings_block()

    # ── Render the treeview ─────────────────────────────────────────────
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
        _set_text(result_box, stmt.combined_sql())
        n_q = count_placeholders(stmt.sql)
        n_p = len(stmt.params)
        if n_q != n_p and not (n_q == 0 and not stmt.params_raw):
            _notice(f"⚠ {n_q} ?s vs {n_p} params — counts don't match", accent=False)
        else:
            _notice(f"Loaded id={stmt.id}  ·  {n_p} param(s) bound")

    def _on_tree_select(_evt=None):
        sel = tree.selection()
        if not sel:
            return
        stmt = state["by_iid"].get(sel[0])
        if stmt is not None:
            _load_statement(stmt)

    tree.bind("<<TreeviewSelect>>", _on_tree_select)

    # ── Path-change → parse + score + render ───────────────────────────
    def _on_path_chosen(force: bool = False):
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

        # Auto-select the first primary statement if any — saves a click.
        for a in actions:
            for s in a.statements:
                if s.is_primary:
                    for iid, sx in state["by_iid"].items():
                        if sx is s:
                            tree.selection_set(iid)
                            tree.see(iid)
                            return
        _notice(f"Loaded {sum(len(a.statements) for a in actions)} statements")

    path_combo.bind("<<ComboboxSelected>>", lambda _e: _on_path_chosen())
    path_combo.bind("<Return>",              lambda _e: _on_path_chosen())

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
    def _copy_result():
        if state["direct"]:
            text = _get_direct_result()
        else:
            text = state["selected"].combined_sql() if state["selected"] else ""
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
        if state["direct"]:
            text = _get_direct_result()
        else:
            text = state["selected"].combined_sql() if state["selected"] else ""
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
        _set_text(direct_result, combine_sql_params(sql, params))
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

    # ── Initial state ───────────────────────────────────────────────────
    if path_var.get():
        # Schedule the first parse after the dialog finishes mapping so the
        # tree dims/columns settle before the first render.
        dlg.after(50, lambda: _on_path_chosen())
    else:
        _notice("Pick a stclibApp.log to begin (or click 'Direct mode' to paste)",
                accent=False)

    # ── Bindings + lifecycle ───────────────────────────────────────────
    dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _on_destroy(_e=None):
        if getattr(app, "_log_sql_dialog", None) is dlg:
            app._log_sql_dialog = None
    dlg.bind("<Destroy>", _on_destroy)

    search_entry.focus_set() if path_var.get() else path_combo.focus_set()

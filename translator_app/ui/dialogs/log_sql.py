"""🛠 Tools → Extract SQL from log…

Mirrors the Excel macro the team uses today: pick a stclibApp.log file,
type a query id (the `id=…` you saw in the log), click Process, see the
raw SQL with `?` placeholders, the bound parameters, and the combined
runnable SQL side-by-side. Optionally drop the result straight into the
translator's input so the existing modes (Inline Replace / Design Doc)
can work on it.

Layout — three editable text panes (SQL / params / result) sandwiched
between a header (file path + id + 'Using log' toggle) and a footer of
counts and action buttons.

Persistence: last-used log path is saved into translator_settings.json
so the dialog opens with the file pre-filled next time.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, scrolledtext

from ...config import save_settings
from ...logsql import (
    combine_sql_params,
    count_placeholders,
    find_entry_by_id,
    find_last_entry,
    parse_params,
    read_log_file,
)
from ...themes import THEMES


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
    dlg.geometry("1180x720")
    dlg.minsize(900, 560)
    dlg.configure(bg=t["bg"])
    dlg.transient(app)

    # Last-used log path persisted in settings so users don't re-pick it.
    saved_path = (app._settings.get("log_sql") or {}).get("last_path", "")

    # ── Header: log file path + id + "Using log" ─────────────────────────
    header = tk.Frame(dlg, bg=t["bg"])
    header.pack(fill="x", padx=14, pady=(12, 6))
    header.columnconfigure(1, weight=1)

    tk.Label(header, text="Log file:", font=app._ui_b,
             bg=t["bg"], fg=t["fg"]).grid(row=0, column=0, sticky="w", padx=(0, 8))
    path_var = tk.StringVar(value=saved_path)
    path_entry = tk.Entry(
        header, textvariable=path_var, font=app._mono,
        bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", bd=0,
    )
    path_entry.grid(row=0, column=1, sticky="ew", ipady=5)

    def _browse():
        initial = os.path.dirname(path_var.get()) if path_var.get() else os.getcwd()
        chosen = filedialog.askopenfilename(
            parent=dlg, title="Choose a stclibApp.log",
            initialdir=initial,
            filetypes=[("Log files", "*.log *.txt"), ("All files", "*.*")],
        )
        if chosen:
            path_var.set(chosen)

    tk.Button(
        header, text="Browse…", font=app._btn, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=12, pady=4, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=_browse,
    ).grid(row=0, column=2, padx=(8, 0))

    tk.Label(header, text="Query ID:", font=app._ui_b,
             bg=t["bg"], fg=t["fg"]).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    id_var = tk.StringVar()
    id_entry = tk.Entry(
        header, textvariable=id_var, font=app._mono,
        bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", bd=0, width=18,
    )
    id_entry.grid(row=1, column=1, sticky="w", pady=(8, 0), ipady=5)

    using_log_var = tk.BooleanVar(value=True)
    using_log_chk = tk.Checkbutton(
        header, text="Using log", variable=using_log_var,
        bg=t["bg"], fg=t["fg"], selectcolor=t["surface"],
        activebackground=t["bg"], activeforeground=t["fg"],
        font=app._ui, bd=0, highlightthickness=0,
    )
    using_log_chk.grid(row=1, column=2, sticky="w", padx=(8, 0), pady=(8, 0))

    # ── Body: three panes (SQL | PARAMS | RESULT) side-by-side ───────────
    body = tk.Frame(dlg, bg=t["bg"])
    body.pack(fill="both", expand=True, padx=14, pady=(6, 6))
    body.columnconfigure(0, weight=2, uniform="cols")
    body.columnconfigure(1, weight=1, uniform="cols")
    body.columnconfigure(2, weight=2, uniform="cols")
    body.rowconfigure(1, weight=1)

    def _pane_label(text, col):
        tk.Label(body, text=text, font=app._ui_b,
                 bg=t["bg"], fg=t["fg"], anchor="w").grid(
            row=0, column=col, sticky="ew", padx=(0 if col == 0 else 4, 4 if col != 2 else 0),
            pady=(0, 4),
        )

    _pane_label("SQL (with ?)", 0)
    _pane_label("PARAM(S)",     1)
    _pane_label("RESULT",       2)

    def _make_text(col, padding):
        box = scrolledtext.ScrolledText(
            body, wrap=tk.NONE, font=app._mono, undo=True,
            bg=t["output_bg"], fg=t["fg"], insertbackground=t["insert"],
            relief="flat", borderwidth=0,
        )
        box.grid(row=1, column=col, sticky="nsew", padx=padding)
        return box

    sql_box    = _make_text(0, (0, 4))
    params_box = _make_text(1, (4, 4))
    result_box = _make_text(2, (4, 0))
    result_box.configure(state="disabled")  # always read-only

    # ── Footer: status + counts + action buttons ─────────────────────────
    status = tk.Frame(dlg, bg=t["bg"])
    status.pack(fill="x", padx=14, pady=(0, 4))
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

    counts = tk.Frame(dlg, bg=t["bg"])
    counts.pack(fill="x", padx=14, pady=(0, 6))
    counts_lbl = tk.Label(
        counts, text="SQL length: 0   ·   [?] count: 0   ·   Params count: 0",
        font=app._small, bg=t["bg"], fg=t["fg_muted"], anchor="w",
    )
    counts_lbl.pack(side="left")

    actions = tk.Frame(dlg, bg=t["bg"])
    actions.pack(fill="x", padx=14, pady=(0, 12))

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

    # ── Helpers wired to UI ──────────────────────────────────────────────
    def _set_text(box, content, *, read_only=False):
        was_disabled = (box.cget("state") == "disabled")
        if was_disabled or read_only:
            box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", content or "")
        if read_only:
            box.configure(state="disabled")
        elif was_disabled and not read_only:
            box.configure(state="normal")

    def _get_text(box):
        return box.get("1.0", "end-1c")

    def _refresh_counts(sql, params):
        n_q = count_placeholders(sql)
        counts_lbl.configure(
            text=f"SQL length: {len(sql)}   ·   [?] count: {n_q}   "
                 f"·   Params count: {len(params)}"
        )
        return n_q

    def _update_result_from_panes():
        """Re-combine SQL + params from the editable panes (used when
        'Using log' is OFF and the user is editing directly)."""
        sql = _get_text(sql_box)
        ps = parse_params(_get_text(params_box))
        n_q = _refresh_counts(sql, ps)
        _set_text(result_box, combine_sql_params(sql, ps), read_only=True)
        if n_q != len(ps):
            _notice(f"⚠ {n_q} ?s vs {len(ps)} params — counts don't match", accent=False)
        else:
            _notice(f"Combined {len(ps)} param(s) into SQL", accent=True)

    def _notice(msg, accent=True):
        notice_lbl.configure(
            text=msg,
            fg=(t["accent"] if accent else t["fg_muted"]),
        )

    def _save_path():
        d = app._settings.setdefault("log_sql", {})
        d["last_path"] = path_var.get()
        try:
            save_settings(app._settings)
        except Exception:
            pass

    def _apply_entry(entry):
        """Drop a parser result-dict into the panes."""
        if not entry:
            class_lbl.configure(text="Class:  —")
            _set_text(sql_box, "")
            _set_text(params_box, "")
            _set_text(result_box, "", read_only=True)
            _refresh_counts("", [])
            _notice("No matching id found in log", accent=False)
            return
        class_lbl.configure(text=f"Class:  {entry.get('fqcn') or '(unknown)'}")
        _set_text(sql_box, entry["sql"])
        _set_text(params_box, entry["params_raw"])
        _set_text(result_box, entry["result"], read_only=True)
        n_q = _refresh_counts(entry["sql"], entry["params"])
        if id_var.get().strip().lower() != entry["id"].lower():
            id_var.set(entry["id"])
        if n_q != len(entry["params"]):
            _notice(
                f"⚠ {n_q} ?s vs {len(entry['params'])} params — counts don't match",
                accent=False,
            )
        else:
            _notice(f"Loaded id={entry['id']}  ·  {len(entry['params'])} param(s) bound")

    def _on_process():
        if using_log_var.get():
            path = path_var.get().strip()
            qid = id_var.get().strip()
            if not path:
                _notice("Pick a log file first", accent=False)
                return
            if not qid:
                _notice("Enter a query id (the `id=` value from the log)", accent=False)
                return
            if not os.path.exists(path):
                _notice(f"File not found: {path}", accent=False)
                return
            text = read_log_file(path)
            if not text:
                _notice("Log file is empty or unreadable", accent=False)
                return
            entry = find_entry_by_id(text, qid)
            _apply_entry(entry)
            _save_path()
        else:
            # Direct mode: SQL and PARAM(S) are user-edited; just combine.
            _update_result_from_panes()

    def _on_get_last():
        path = path_var.get().strip()
        if not path or not os.path.exists(path):
            _notice("Pick a valid log file first", accent=False)
            return
        text = read_log_file(path)
        if not text:
            _notice("Log file is empty or unreadable", accent=False)
            return
        entry = find_last_entry(text)
        if entry:
            using_log_var.set(True)
            _refresh_pane_editability()
        _apply_entry(entry)
        _save_path()

    def _on_clear():
        id_var.set("")
        _set_text(sql_box, "")
        _set_text(params_box, "")
        _set_text(result_box, "", read_only=True)
        class_lbl.configure(text="Class:  —")
        _refresh_counts("", [])
        _notice("Cleared", accent=False)

    def _on_copy_result():
        text = _get_text(result_box)
        if not text.strip():
            _notice("Nothing to copy yet — press Process first", accent=False)
            return
        try:
            app.clipboard_clear()
            app.clipboard_append(text)
            app._toast.show("Result copied to clipboard", 1100, "success")
        except Exception:
            _notice("Clipboard copy failed", accent=False)

    def _on_send_to_translator():
        text = _get_text(result_box)
        if not text.strip():
            _notice("Nothing to send — press Process first", accent=False)
            return
        try:
            # Reach into the translator's active doc tab and replace its
            # input with the combined SQL. Re-runs translation immediately
            # via on_translate.
            app.input_box.configure(state="normal")
            app.input_box.delete("1.0", "end")
            app.input_box.insert("1.0", text)
            app.on_translate()
            app._toast.show("Sent SQL into translator input", 1300, "success")
            _notice("Sent into translator input", accent=True)
        except Exception:
            _notice("Couldn't reach translator input", accent=False)

    # ── Pane editability follows the "Using log" toggle ─────────────────
    def _refresh_pane_editability():
        editable = not using_log_var.get()
        for box in (sql_box, params_box):
            box.configure(state=("normal" if editable else "normal"))
            # Both states are 'normal' so users can still scroll freely.
            # We use a key-press veto in 'using log' mode instead.

    def _veto_keys_when_using_log(event):
        if using_log_var.get():
            # Allow navigation / copy keys but block edits.
            allowed = {
                "Left", "Right", "Up", "Down", "Home", "End",
                "Prior", "Next", "Tab", "ISO_Left_Tab",
            }
            if event.keysym in allowed:
                return None
            if (event.state & 0x4) and event.keysym.lower() in ("c", "a"):  # Ctrl+C, Ctrl+A
                return None
            return "break"
        return None

    sql_box.bind("<Key>",    _veto_keys_when_using_log)
    params_box.bind("<Key>", _veto_keys_when_using_log)
    using_log_chk.configure(command=_refresh_pane_editability)
    _refresh_pane_editability()

    # ── Action buttons ──────────────────────────────────────────────────
    _btn(actions, "Get last SQL", _on_get_last).pack(side="left", padx=(0, 6))
    _btn(actions, "Process", _on_process, accent=True).pack(side="left", padx=(0, 6))
    _btn(actions, "Clear", _on_clear).pack(side="left", padx=(0, 6))

    _btn(actions, "Close", dlg.destroy).pack(side="right")
    _btn(actions, "Send to translator input",
         _on_send_to_translator, accent=False).pack(side="right", padx=(0, 6))
    _btn(actions, "Copy result", _on_copy_result).pack(side="right", padx=(0, 6))

    # ── Bindings ────────────────────────────────────────────────────────
    id_entry.bind("<Return>", lambda _e: _on_process())
    path_entry.bind("<Return>", lambda _e: _on_process())
    dlg.bind("<Escape>", lambda _e: dlg.destroy())

    # ── Lifecycle ───────────────────────────────────────────────────────
    def _on_destroy(_e=None):
        if getattr(app, "_log_sql_dialog", None) is dlg:
            app._log_sql_dialog = None
    dlg.bind("<Destroy>", _on_destroy)

    # Focus the id field when a path is already known; otherwise the path.
    (id_entry if saved_path else path_entry).focus_set()

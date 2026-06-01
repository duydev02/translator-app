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
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ...config import save_settings
from ...logsql import (
    DEFAULT_NOISE_PACKAGES,
    DEFAULT_NOISE_TABLES,
    DEFAULT_PRIMARY_THRESHOLD,
    Statement,
    annotate_scores,
    combine_sql_params_marked,
    count_placeholders,
    clear_log_file,
    extract_pasted_statement,
    extract_subst_ranges,
    group_by_action,
    keep_newest_repeated_sql,
    parse_log,
    parse_params,
    pretty_sql,
    read_log_file,
    tokenize_sql_for_highlight,
)
from ...themes import THEMES
from ..widgets import LineNumberCanvas, install_treeview_cell_tooltip
from .placement import geometry_near_parent, place_dialog


# Maximum recent log paths to keep in the dropdown.
MAX_RECENT_PATHS = 8
DEFAULT_DIALOG_SIZE = (1240, 820)


def _dialog_geometry_near_parent(parent, width=DEFAULT_DIALOG_SIZE[0], height=DEFAULT_DIALOG_SIZE[1]):
    return geometry_near_parent(parent, width, height, min_width=960, min_height=600)


def open_log_sql_dialog(app, parent=None, *, embedded=False, on_close=None):
    """Open the Extract-SQL-from-log UI.

    By default this creates the historical popup dialog. When `embedded`
    is true it builds the same UI into `parent` and returns the frame so the
    main window can host Extract SQL as a first-class mode.
    """
    if not embedded:
        existing = getattr(app, "_log_sql_dialog", None)
        if existing and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return existing

    t = THEMES[app._theme]
    if embedded:
        dlg = tk.Frame(parent or app, bg=t["bg"])
        app._log_sql_panel = dlg
    else:
        dlg = tk.Toplevel(app)
        app._log_sql_dialog = dlg
        dlg.title("Extract SQL from log")
        place_dialog(dlg, app, *DEFAULT_DIALOG_SIZE, min_width=960, min_height=600)
        dlg.minsize(960, 600)
        dlg.transient(app)
    dlg.configure(bg=t["bg"])

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
    settings.setdefault("hide_redundant", False)
    settings.setdefault("recent_panel_open", True)
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
        "pending_select_id": "",
    }

    # ── Top: log file selector + reload ──────────────────────────────────
    # Top header has TWO rows now:
    #   Row 0: chip strip (one chip per recent path) + [+ Add] + [Reload] + [☑ Auto]
    #   Row 1: full active-path display (read-only, monospace) for context
    header = tk.Frame(dlg, bg=t["bg"])
    header.pack(fill="x", padx=14, pady=(14, 6))

    # Row 0 — chip strip + actions. Vertical padding (`pady=4`) gives
    # the row more breathing room so the chips don't feel cramped.
    chip_row = tk.Frame(header, bg=t["bg"])
    chip_row.pack(fill="x", pady=(0, 2))

    chip_strip = tk.Frame(chip_row, bg=t["bg"])
    chip_strip.pack(side="left", fill="x", expand=True)

    # Action buttons live on the right; chip strip flexes between.
    actions_right = tk.Frame(chip_row, bg=t["bg"])
    actions_right.pack(side="right")

    auto_reload_var = tk.BooleanVar(value=bool(settings.get("auto_reload", True)))

    # Row 1 — readout of the active path so the user knows what's loaded.
    # Indented slightly so it reads as a subtitle under the chip row.
    path_var = tk.StringVar(value=settings.get("active_path", ""))
    active_path_lbl = tk.Label(
        header, textvariable=path_var, font=app._mono,
        bg=t["bg"], fg=t["fg_muted"], anchor="w", padx=4,
    )
    active_path_lbl.pack(fill="x", pady=(4, 0))

    # Recent logs panel: denser than the chips, useful when paths look alike.
    recent_panel = tk.Frame(header, bg=t["bg"])
    recent_panel.pack(fill="x", pady=(6, 0))
    recent_panel.rowconfigure(1, weight=1)
    recent_panel.columnconfigure(0, weight=1)

    recent_head = tk.Frame(recent_panel, bg=t["bg"])
    recent_head.grid(row=0, column=0, sticky="ew")
    tk.Label(
        recent_head, text="Recent logs", font=app._small,
        bg=t["bg"], fg=t["fg_muted"], anchor="w",
    ).pack(side="left")
    recent_hint = tk.Label(
        recent_head, text="double-click to load", font=app._small,
        bg=t["bg"], fg=t["fg_muted"], anchor="e",
    )
    recent_hint.pack(side="right")
    recent_toggle_btn = tk.Button(
        recent_head, text="", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=8, pady=1, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=lambda: _toggle_recent_panel(),
    )
    recent_toggle_btn.pack(side="right", padx=(0, 8))

    recent_body = tk.Frame(recent_panel, bg=t["bg"])
    recent_body.grid(row=1, column=0, sticky="ew", pady=(2, 0))
    recent_body.columnconfigure(0, weight=1)
    recent_tree = ttk.Treeview(
        recent_body,
        columns=("name", "updated", "size", "count", "path"),
        show="headings",
        height=4,
        style="Log.Treeview",
    )
    for col, label, width, stretch in (
        ("name", "Log", 180, False),
        ("updated", "Updated", 130, False),
        ("size", "Size", 80, False),
        ("count", "Statements", 90, False),
        ("path", "Path", 520, True),
    ):
        recent_tree.heading(col, text=label)
        recent_tree.column(col, width=width, stretch=stretch, anchor="w")
    recent_tree.grid(row=0, column=0, sticky="ew")

    recent_actions = tk.Frame(recent_body, bg=t["bg"])
    recent_actions.grid(row=0, column=1, sticky="ns", padx=(8, 0))

    # Persistent per-path aliases (right-click → Rename…). Lets users
    # pin "lawdailyorder PROD" vs "lawdailyorder DEV" without staring
    # at identical-looking paths.
    settings.setdefault("aliases", {})

    # Common patterns in this codebase that add no information to the
    # chip label. We strip them so e.g.
    # `D:/…/mdw-lawdailyorder-web/log/stclibApp.log` → `lawdailyorder`.
    _STRIP_PREFIXES = ("mdw-", "mdw_", "mkm-", "mkm_")
    _STRIP_SUFFIXES = ("-web", "_web", "-app", "_app", "-service", "_service")

    def _auto_short_name(path: str) -> str:
        """Distill a path into the smallest distinguishing label.

        Strategy: walk up from the filename until we hit a path segment
        that isn't `log` / `logs` / `tmp` / `out` — that's typically the
        project folder. Then strip the common prefix/suffix noise
        (`mdw-`, `-web`, …) to leave just the project identifier."""
        if not path:
            return "?"
        norm = path.replace("\\", "/").rstrip("/")
        parts = [p for p in norm.split("/") if p]
        if not parts:
            return path
        skip_dirs = {"log", "logs", "tmp", "out", "output"}
        # Skip the filename, then any boilerplate dirs.
        idx = len(parts) - 2  # parent of the file
        while idx > 0 and parts[idx].lower() in skip_dirs:
            idx -= 1
        candidate = parts[idx] if idx >= 0 else parts[-1]
        # Strip common prefixes/suffixes once each.
        cl = candidate
        for p in _STRIP_PREFIXES:
            if cl.lower().startswith(p):
                cl = cl[len(p):]
                break
        for s in _STRIP_SUFFIXES:
            if cl.lower().endswith(s):
                cl = cl[: -len(s)]
                break
        # Don't return empty — fall back to the original segment if we
        # accidentally stripped it down to nothing.
        return cl or candidate or parts[-1]

    def _project_short_name(path: str) -> str:
        """Resolved short label for a path: explicit alias if set,
        otherwise the auto-stripped name."""
        if not path:
            return "?"
        alias = (settings.get("aliases") or {}).get(path)
        if alias:
            return alias
        return _auto_short_name(path)

    chip_widgets: list[tk.Widget] = []
    # path → most-recently-observed statement count. Filled by
    # _on_path_chosen after a parse; rendered as a `(N)` badge on the
    # chip so users see at a glance which projects have data.
    chip_counts: dict[str, int] = {}

    def _format_size(path: str) -> str:
        try:
            size = os.path.getsize(path)
        except OSError:
            return "missing"
        units = ("B", "KB", "MB", "GB")
        value = float(size)
        unit = units[0]
        for unit in units:
            if value < 1024 or unit == units[-1]:
                break
            value /= 1024
        return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"

    def _format_mtime(path: str) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
        except OSError:
            return "missing"

    def _selected_recent_path() -> str:
        sel = recent_tree.selection()
        return recent_tree.set(sel[0], "path") if sel else ""

    def _refresh_recent_panel():
        recent_tree.delete(*recent_tree.get_children())
        active = settings.get("active_path") or ""
        recents = settings.get("recent_paths") or []
        for idx, path in enumerate(recents):
            tags = ("active_recent",) if path == active else ()
            count = chip_counts.get(path)
            recent_tree.insert(
                "", "end", iid=f"recent:{idx}", tags=tags,
                values=(
                    _project_short_name(path),
                    _format_mtime(path),
                    _format_size(path),
                    "" if count is None else str(count),
                    path,
                ),
            )
        recent_tree.tag_configure("active_recent", background=t["muted_bg"], foreground=t["fg"])
        state_text = "visible" if bool(settings.get("recent_panel_open", True)) else "hidden"
        recent_hint.configure(text=f"{len(recents)} recent · {state_text}")

    def _apply_recent_panel_visibility():
        is_open = bool(settings.get("recent_panel_open", True))
        if is_open:
            if not recent_body.winfo_ismapped():
                recent_body.grid(row=1, column=0, sticky="ew", pady=(2, 0))
            recent_toggle_btn.configure(text="Hide")
        else:
            if recent_body.winfo_ismapped():
                recent_body.grid_remove()
            recent_toggle_btn.configure(text="Show")
        _refresh_recent_panel()

    def _toggle_recent_panel():
        settings["recent_panel_open"] = not bool(settings.get("recent_panel_open", True))
        _apply_recent_panel_visibility()
        _save_settings_block()

    def _load_selected_recent():
        path = _selected_recent_path()
        if path:
            _switch_to_path(path)

    def _remove_selected_recent():
        path = _selected_recent_path()
        if path:
            _remove_recent(path)

    def _reveal_selected_recent():
        path = _selected_recent_path()
        if path:
            _reveal(path)

    def _format_chip_label(path: str, idx: int, is_active: bool) -> str:
        """Build the chip display string:
            ▸ 1 lawdailyorder (47)        ← active, populated
              2 corepkgshiire             ← inactive, not yet loaded
            ⚠ 3 stale-path                ← path missing on disk
        Active = leading ▸, stale = leading ⚠. Number is the Alt+N
        hotkey so power users can switch without the mouse."""
        short = _project_short_name(path)
        exists = bool(path) and os.path.exists(path)
        prefix = "▸ " if is_active else ("⚠ " if not exists else "  ")
        # Alt+1..9 only; for #10+ we skip the digit (rare but defensive).
        digit = f"{idx + 1} " if idx < 9 else "  "
        count = chip_counts.get(path)
        suffix = f"  ({count})" if count is not None else ""
        return f"{prefix}{digit}{short}{suffix}"

    def _redraw_chips():
        """Rebuild the chip strip from settings['recent_paths']. The
        active chip is highlighted; click switches; right-click for
        Rename / Remove / Open folder; Alt+N selects by index."""
        for w in chip_widgets:
            w.destroy()
        chip_widgets.clear()
        recents = settings.get("recent_paths") or []
        active = settings.get("active_path") or ""
        for idx, path in enumerate(recents):
            is_active = (path == active)
            exists = bool(path) and os.path.exists(path)
            label = _format_chip_label(path, idx, is_active)
            # Color scheme:
            #   active   → accent foreground + bg
            #   stale    → danger foreground + muted bg
            #   normal   → muted on muted
            if is_active:
                bg, fg = t["accent"], t["accent_fg"]
            elif not exists:
                bg, fg = t["muted_bg"], t.get("danger", "#c14a4a")
            else:
                bg, fg = t["muted_bg"], t["muted_fg"]
            chip = tk.Button(
                chip_strip, text=label, font=app._small,
                relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
                bg=bg, fg=fg,
                activebackground=bg, activeforeground=fg,
                command=lambda p=path: _switch_to_path(p),
            )
            chip.pack(side="left", padx=(0, 6), pady=2)

            # Rich tooltip: full path + hotkey + load status. Helps
            # users disambiguate near-identically-named projects.
            tip_lines = [path]
            if idx < 9:
                tip_lines.append(f"Hotkey: Alt+{idx + 1}")
            cnt = chip_counts.get(path)
            if cnt is not None:
                tip_lines.append(f"Loaded: {cnt} statements")
            if not exists:
                tip_lines.append("⚠ File not found on disk")
            try:
                app._attach_tooltip(chip, "\n".join(tip_lines))
            except Exception:
                pass

            # Right-click → Rename / Remove / Reveal.
            menu = tk.Menu(chip, tearoff=0,
                bg=t["surface"], fg=t["fg"],
                activebackground=t["accent"], activeforeground=t["accent_fg"],
                bd=0, relief="flat")
            menu.add_command(
                label="Rename…",
                command=lambda p=path: _rename_chip(p),
            )
            alias_set = bool((settings.get("aliases") or {}).get(path))
            menu.add_command(
                label="Reset to auto-name",
                state=("normal" if alias_set else "disabled"),
                command=lambda p=path: _reset_alias(p),
            )
            menu.add_separator()
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
        _refresh_recent_panel()

    def _rename_chip(path: str):
        """Prompt for a friendly alias for `path`, persist it, redraw."""
        from tkinter import simpledialog
        current = _project_short_name(path)
        new_name = simpledialog.askstring(
            "Rename log",
            f"Display name for this log:\n\n{path}",
            initialvalue=current,
            parent=dlg,
        )
        if new_name is None:
            return  # cancelled
        new_name = new_name.strip()
        aliases = settings.setdefault("aliases", {})
        if not new_name:
            # Empty input → clear the alias (revert to auto).
            aliases.pop(path, None)
        else:
            aliases[path] = new_name
        _save_settings_block()
        _redraw_chips()

    def _reset_alias(path: str):
        aliases = settings.get("aliases") or {}
        if path in aliases:
            del aliases[path]
            _save_settings_block()
            _redraw_chips()

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
            chip_counts.pop(path, None)
            # An alias for a removed path is harmless but stale — purge.
            (settings.get("aliases") or {}).pop(path, None)
            if settings.get("active_path") == path:
                settings["active_path"] = recents[0] if recents else ""
                path_var.set(settings["active_path"])
            _save_settings_block()
            _redraw_chips()
            _refresh_recent_panel()

    def _reveal(path: str):
        try:
            folder = os.path.dirname(path) or "."
            os.startfile(folder)  # Windows-only; harmless elsewhere if missing
        except Exception:
            _notice(f"Couldn't open folder: {path}", accent=False)

    recent_tree.bind("<Double-Button-1>", lambda _e: _load_selected_recent())
    tk.Button(
        recent_actions, text="Open", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=8, pady=2, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=_load_selected_recent,
    ).pack(fill="x", pady=(0, 4))
    tk.Button(
        recent_actions, text="Folder", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"], padx=8, pady=2, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=_reveal_selected_recent,
    ).pack(fill="x", pady=(0, 4))
    tk.Button(
        recent_actions, text="Remove", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t.get("danger", "#c14a4a"), padx=8, pady=2,
        cursor="hand2", activebackground=t["muted_bg"],
        activeforeground=t.get("danger", "#c14a4a"),
        command=_remove_selected_recent,
    ).pack(fill="x")

    def _browse():
        initial = os.path.dirname(path_var.get()) if path_var.get() else os.getcwd()
        chosen = filedialog.askopenfilename(
            parent=dlg, title="Choose a stclibApp.log",
            initialdir=initial,
            filetypes=[("Log files", "*.log *.txt"), ("All files", "*.*")],
        )
        if chosen:
            _switch_to_path(chosen)

    def _clear_active_log():
        path = path_var.get().strip()
        if not path:
            _notice("Pick a log file to clear", accent=False)
            return
        if not os.path.exists(path):
            _notice(f"File not found: {path}", accent=False)
            return
        ok = messagebox.askyesno(
            "Clear log file",
            "Clear all content from this log file?\n\n"
            f"{path}\n\n"
            "This cannot be undone.",
            parent=dlg,
        )
        if not ok:
            return
        try:
            clear_log_file(path)
        except OSError as exc:
            _notice(f"Clear failed: {exc}", accent=False)
            return

        chip_counts[path] = 0
        state["actions"] = []
        state["by_iid"] = {}
        state["selected"] = None
        _render_tree()
        try:
            app._set_logsql_status(path=path, count=0)
        except Exception:
            pass
        _load_statement(None)
        _capture_mtime()
        _redraw_chips()
        _notice("Cleared log file")
        try:
            app._toast.show("Log file cleared", 1200, "success")
        except Exception:
            pass

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

    clear_btn = tk.Button(
        actions_right, text="Clear log", font=app._small, relief="flat", bd=0,
        bg=t["muted_bg"], fg=t.get("danger", "#c14a4a"), padx=10, pady=3,
        cursor="hand2",
        activebackground=t["muted_bg"],
        activeforeground=t.get("danger", "#c14a4a"),
        command=_clear_active_log,
    )
    clear_btn.pack(side="left", padx=(0, 4))
    try:
        app._attach_tooltip(
            clear_btn,
            "Truncate the active log file after confirmation.\n"
            "Useful before reproducing one action.",
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

    hide_redundant_var = tk.BooleanVar(value=bool(settings.get("hide_redundant", False)))
    hide_redundant_chk = tk.Checkbutton(
        filter_top, text="Hide repeats", variable=hide_redundant_var,
        bg=t["bg"], fg=t["fg"], selectcolor=t["surface"],
        activebackground=t["bg"], activeforeground=t["fg"],
        font=app._ui, bd=0, highlightthickness=0,
    )
    hide_redundant_chk.pack(side="left", padx=(0, 8))
    try:
        app._attach_tooltip(
            hide_redundant_chk,
            "Hide older repeated SQL rows inside each action group,\n"
            "leaving the newest matching DAO + SQL shape.",
        )
    except Exception:
        pass

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

    # Hover tooltips on truncated cells — DAO short name, Tables, even
    # the action-header row's label often run past their column width.
    # The custom value_fn pulls the full action label from `tags` for
    # parent rows (the header text is stored in the `dao` column slot).
    install_treeview_cell_tooltip(tree, app._tooltip)
    body.add(list_pane, minsize=180, height=320)

    # Bottom pane — detail notebook (SQL / Params / Result)
    detail_pane = tk.Frame(body, bg=t["bg"])
    nb = ttk.Notebook(detail_pane)
    nb.pack(fill="both", expand=True)

    text_boxes: list[tk.Text] = []
    line_canvases: list[LineNumberCanvas] = []

    def _extract_wrap_mode():
        try:
            return tk.WORD if bool(app._word_wrap.get()) else tk.NONE
        except Exception:
            return tk.NONE

    def _show_extract_line_numbers():
        try:
            return bool(app._show_line_numbers.get())
        except Exception:
            return False

    def _register_text_box(frame, box):
        text_boxes.append(box)
        canvas = LineNumberCanvas(frame, box, lambda: THEMES[app._theme])
        line_canvases.append(canvas)
        box.configure(wrap=_extract_wrap_mode())

    def _apply_extract_text_options():
        wrap_mode = _extract_wrap_mode()
        show_lines = _show_extract_line_numbers()
        for box in text_boxes:
            try:
                box.configure(wrap=wrap_mode)
            except Exception:
                pass
        for canvas in line_canvases:
            try:
                if show_lines and not canvas.winfo_ismapped():
                    canvas.pack(side="left", fill="y", before=canvas.text)
                elif not show_lines and canvas.winfo_ismapped():
                    canvas.pack_forget()
                if show_lines:
                    canvas._schedule()
            except Exception:
                pass
    app._log_sql_apply_text_options = _apply_extract_text_options

    def _make_text_tab(parent_label):
        frame = tk.Frame(nb, bg=t["bg"])
        box = scrolledtext.ScrolledText(
            frame, wrap=_extract_wrap_mode(), font=app._mono, undo=True,
            bg=t["output_bg"], fg=t["fg"], insertbackground=t["insert"],
            relief="flat", borderwidth=0,
        )
        _register_text_box(frame, box)
        box.pack(side="left", fill="both", expand=True)
        _apply_extract_text_options()
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
        result_toolbar, text="📋  Copy result", font=app._small,
        relief="flat", bd=0, bg=t["muted_bg"], fg=t["muted_fg"],
        padx=10, pady=3, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=lambda: _copy_result(),
    )
    copy_result_btn.pack(side="left")
    copy_menu_btn = tk.Menubutton(
        result_toolbar, text="Copy options ▾", font=app._small,
        relief="flat", bd=0, bg=t["muted_bg"], fg=t["muted_fg"],
        padx=10, pady=3, cursor="hand2",
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
    )
    copy_menu = tk.Menu(copy_menu_btn, tearoff=False, bg=t["surface"], fg=t["fg"])
    copy_menu.add_command(label="Copy formatted SQL",
                          command=lambda: _copy_current_option("formatted"))
    copy_menu.add_command(label="Copy original SQL",
                          command=lambda: _copy_current_option("original"))
    copy_menu.add_command(label="Copy params only",
                          command=lambda: _copy_current_option("params"))
    copy_menu.add_separator()
    copy_menu.add_command(label="Copy SQL + params summary",
                          command=lambda: _copy_current_option("summary"))
    copy_menu_btn.configure(menu=copy_menu)
    copy_menu_btn.pack(side="left", padx=(6, 0))
    try:
        app._attach_tooltip(
            copy_result_btn,
            "Copy the prettified result SQL to the clipboard.",
        )
        app._attach_tooltip(
            copy_menu_btn,
            "Copy the selected statement in another useful format.",
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

    result_text_frame = tk.Frame(result_frame, bg=t["bg"])
    result_text_frame.pack(fill="both", expand=True)
    result_box = scrolledtext.ScrolledText(
        result_text_frame, wrap=_extract_wrap_mode(), font=app._mono, undo=True,
        bg=t["output_bg"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", borderwidth=0,
    )
    _register_text_box(result_text_frame, result_box)
    result_box.pack(side="left", fill="both", expand=True)
    _apply_extract_text_options()
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
    def _configure_highlight_tags(box):
        box.tag_configure("hl_keyword", foreground=kw_color)
        box.tag_configure("hl_string",  foreground=str_color)
        box.tag_configure("hl_number",  foreground=num_color)
        box.tag_configure("hl_comment", foreground=com_color)
        box.tag_configure(
            "hl_search",
            background=t.get("tag_search", "#ffd966"),
            foreground=t.get("accent_fg", "#000000"),
        )
        # Substituted values get a strong tint AND a subtle background so they
        # stand out even when they happen to be a string literal (which would
        # otherwise share the string color). underline=False keeps them clean.
        box.tag_configure(
            "hl_subst",
            foreground=sub_color, background=t.get("muted_bg", "#f0f0f0"),
        )
    _configure_highlight_tags(result_box)

    # SQL tab — plain text (raw with ? placeholders, no formatting).
    sql_box    = _make_text_tab("SQL (with ?)")
    sql_box.configure(state="disabled")

    # Params tab — structured table instead of the raw
    # `[STRING:1:foo][STRING:2:bar]…` blob. Three columns (`#` /
    # `Type` / `Value`); type-coloured rows; scrollable. The raw blob
    # is preserved in `state["params_raw"]` so Copy / Send paths can
    # still produce the original bracket-encoded string if needed.
    params_frame = tk.Frame(nb, bg=t["bg"])
    params_sb = tk.Scrollbar(params_frame, orient="vertical")
    style.configure(
        "Params.Treeview",
        background=t["output_bg"], fieldbackground=t["output_bg"],
        foreground=t["fg"], bordercolor=t["bg"], borderwidth=0,
        rowheight=22,
    )
    style.configure(
        "Params.Treeview.Heading",
        background=t["muted_bg"], foreground=t["fg"], relief="flat",
    )
    style.map("Params.Treeview",
              background=[("selected", t["accent"])],
              foreground=[("selected", t["accent_fg"])])
    params_tree = ttk.Treeview(
        params_frame, columns=("idx", "type", "value"),
        show="headings", style="Params.Treeview",
        yscrollcommand=params_sb.set,
    )
    params_tree.heading("idx",   text="#")
    params_tree.heading("type",  text="Type")
    params_tree.heading("value", text="Value")
    params_tree.column("idx",   width=44,  anchor="e",  stretch=False)
    params_tree.column("type",  width=92,  anchor="w",  stretch=False)
    params_tree.column("value", width=480, anchor="w",  stretch=True)
    params_sb.configure(command=params_tree.yview,
                        bg=t["bg"], troughcolor=t["bg"], bd=0)
    params_tree.pack(side="left", fill="both", expand=True)
    params_sb.pack(side="right", fill="y")
    # Type-coloured row tags. STRING in green, numerics in purple,
    # NULL muted. Matches the colour scheme on the Result tab so
    # `'foo'` reads the same way wherever it appears.
    if _is_dark():
        ty_string, ty_num, ty_null, ty_date = (
            "#ce9178", "#b5cea8", "#6a9955", "#4ec9b0",
        )
    else:
        ty_string, ty_num, ty_null, ty_date = (
            "#067d17", "#1750eb", "#8c8c8c", "#d36a00",
        )
    params_tree.tag_configure("ty_string", foreground=ty_string)
    params_tree.tag_configure("ty_num",    foreground=ty_num)
    params_tree.tag_configure("ty_null",   foreground=ty_null)
    params_tree.tag_configure("ty_date",   foreground=ty_date)
    # Double-click a row → copy that single value to the clipboard.
    def _on_param_dblclick(_e):
        sel = params_tree.selection()
        if not sel:
            return
        val = params_tree.set(sel[0], "value")
        try:
            app.clipboard_clear()
            app.clipboard_append(val)
            app._toast.show(f"Copied: {val[:30]}", 1100, "success")
        except Exception:
            pass
    params_tree.bind("<Double-Button-1>", _on_param_dblclick)
    # Cell tooltips so long values that overflow the column are still readable.
    install_treeview_cell_tooltip(params_tree, app._tooltip)
    nb.add(params_frame, text="Params")

    for box in (sql_box, result_box):
        box.configure(state="disabled")
    body.add(detail_pane, minsize=180, height=380)

    def _render_params_table(params: list[tuple[str, str]]):
        """Populate the Params tree from a `(type, value)` list."""
        params_tree.delete(*params_tree.get_children())
        for i, (typ, val) in enumerate(params, start=1):
            t_upper = (typ or "STRING").upper()
            if t_upper == "NULL":
                tag = "ty_null"
            elif t_upper in ("DATE", "TIMESTAMP", "TIME", "DATETIME"):
                tag = "ty_date"
            elif t_upper in ("INT", "INTEGER", "LONG", "BIGINT", "SHORT",
                             "SMALLINT", "TINYINT", "DECIMAL", "NUMERIC",
                             "NUMBER", "DOUBLE", "FLOAT", "REAL"):
                tag = "ty_num"
            else:
                tag = "ty_string"
            params_tree.insert(
                "", "end",
                values=(str(i), t_upper, val),
                tags=(tag,),
            )

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

    close_command = on_close if (embedded and on_close) else dlg.destroy
    _btn(actions, "Back" if embedded else "Close", close_command).pack(side="right")
    send_btn = _btn(actions, "Send to translator input",
         lambda: _send_to_translator(), accent=True)
    send_btn.pack(side="right", padx=(0, 6))
    # Ctrl+click "Send" → new tab. Power-user shortcut on the same button
    # avoids cluttering the action bar with a second button. The visible
    # "Send to new tab" button below covers the discoverable path.
    send_btn.bind("<Control-Button-1>", lambda _e: _send_to_translator(new_tab=True))
    new_tab_btn = _btn(actions, "Send to new tab",
        lambda: _send_to_translator(new_tab=True))
    new_tab_btn.pack(side="right", padx=(0, 6))
    try:
        app._attach_tooltip(
            new_tab_btn,
            "Open the runnable SQL in a fresh translator tab instead of\n"
            "replacing the active one. Useful for side-by-side comparison.\n"
            "Tip: Ctrl+click 'Send to translator input' does the same.",
        )
    except Exception:
        pass
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
        hide_redundant = bool(hide_redundant_var.get())
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
        n_repeats_hidden = 0

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
            before_repeat_filter = len(visible_kids)
            if hide_redundant:
                visible_kids = keep_newest_repeated_sql(visible_kids)
                n_repeats_hidden += before_repeat_filter - len(visible_kids)
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
        if n_repeats_hidden:
            suffix += f"  ·  {n_repeats_hidden} repeats hidden"
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

    def _render_sql_highlighted(box, text: str, subst_ranges: list[tuple[int, int]]):
        box.configure(state="normal")
        for tag in (*_HL_TAGS, "hl_search"):
            box.tag_remove(tag, "1.0", "end")
        box.delete("1.0", "end")
        if not text:
            box.configure(state="disabled")
            return
        box.insert("1.0", text)
        for start, end, kind in tokenize_sql_for_highlight(text):
            box.tag_add(f"hl_{kind}",
                        f"1.0 + {start} chars",
                        f"1.0 + {end} chars")
        for start, end in subst_ranges:
            box.tag_add("hl_subst",
                        f"1.0 + {start} chars",
                        f"1.0 + {end} chars")
        _highlight_result_search(box)
        box.configure(state="disabled")

    def _render_result_highlighted(text: str, subst_ranges: list[tuple[int, int]]):
        """Render `text` into the result_box with SQL syntax highlighting
        (keyword/string/number/comment) plus a substituted-value tag
        applied to each (start, end) range in `subst_ranges`."""
        _render_sql_highlighted(result_box, text, subst_ranges)

    def _highlight_result_search(box=None):
        """Mirror the statement-list search term inside the active SQL text.

        The search box already filters the tree. Highlighting the same term in
        the Result tab makes long SQL easier to scan after picking a match.
        """
        target = box or result_box
        try:
            was_disabled = (target.cget("state") == "disabled")
            if was_disabled:
                target.configure(state="normal")
            target.tag_remove("hl_search", "1.0", "end")
            query = search_var.get().strip()
            if query:
                pos = "1.0"
                while True:
                    idx = target.search(query, pos, stopindex="end", nocase=True)
                    if not idx:
                        break
                    end = f"{idx}+{len(query)}c"
                    target.tag_add("hl_search", idx, end)
                    pos = end
            if was_disabled:
                target.configure(state="disabled")
        except Exception:
            pass

    # ── Statement loading ──────────────────────────────────────────────
    def _load_statement(stmt: Statement | None):
        state["selected"] = stmt
        if stmt is None:
            class_lbl.configure(text="Class:  —")
            for box in (sql_box, result_box):
                _set_text(box, "")
            _render_params_table([])
            return
        class_lbl.configure(
            text=f"Class:  {stmt.fqcn or '(unknown)'}   ·   id={stmt.id}",
        )
        _set_text(sql_box, stmt.sql)
        _render_params_table(stmt.params)
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

    def _select_statement_id(stmt_id: str) -> bool:
        if not stmt_id:
            return False
        for iid, sx in state["by_iid"].items():
            if sx.id == stmt_id:
                tree.selection_set(iid)
                tree.see(iid)
                tree.focus(iid)
                _load_statement(sx)
                return True
        return False

    def _select_source(source: dict):
        path = source.get("path") or ""
        stmt_id = source.get("id") or ""
        if path and path != path_var.get().strip():
            state["pending_select_id"] = stmt_id
            path_var.set(path)
            settings["active_path"] = path
            _redraw_chips()
            _on_path_chosen(force=True)
            return
        if not _select_statement_id(stmt_id):
            state["pending_select_id"] = stmt_id
            _on_path_chosen(force=True)

    app._log_sql_select_source = _select_source

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
        # Cache the statement count so the chip can show `(N)` and users
        # can see at a glance which projects have data loaded. Computed
        # *before* _push_recent so the chip redraw it triggers picks up
        # the count immediately (avoiding a no-count flash + re-render).
        chip_counts[path] = sum(len(a.statements) for a in actions)
        state["actions"] = actions
        state["selected"] = None
        _render_tree()
        try:
            app._set_logsql_status(path=path, count=chip_counts[path])
        except Exception:
            pass
        _push_recent(path)
        _capture_mtime()

        pending_id = state.get("pending_select_id") or ""
        if pending_id and _select_statement_id(pending_id):
            state["pending_select_id"] = ""
            _notice(f"Returned to SQL row id={pending_id}")
            return

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
        _highlight_result_search(result_box)
    search_var.trace_add("write", _on_search)

    def _on_hide_toggle():
        settings["hide_infra"] = bool(hide_infra_var.get())
        _save_settings_block()
        if state["actions"]:
            _render_tree()
    hide_infra_chk.configure(command=_on_hide_toggle)

    def _on_hide_redundant_toggle():
        settings["hide_redundant"] = bool(hide_redundant_var.get())
        _save_settings_block()
        if state["actions"]:
            _render_tree()
    hide_redundant_chk.configure(command=_on_hide_redundant_toggle)

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

    def _current_original_sql() -> str:
        if state["direct"]:
            return direct_sql.get("1.0", "end-1c")
        sel = state.get("selected")
        return sel.sql if sel else ""

    def _current_params_text() -> str:
        if state["direct"]:
            return direct_params.get("1.0", "end-1c")
        sel = state.get("selected")
        if not sel:
            return ""
        if sel.params_raw:
            return sel.params_raw
        return "\n".join(f"[{typ}:{i}:{val}]" for i, (typ, val) in enumerate(sel.params, start=1))

    def _current_summary_text() -> str:
        result = _current_result_text()
        original = _current_original_sql()
        params = _current_params_text()
        if not (result.strip() or original.strip() or params.strip()):
            return ""
        lines = []
        if state["direct"]:
            lines.append("Source: Direct extract")
        else:
            sel = state.get("selected")
            if sel:
                lines.append(f"ID: {sel.id or '(unknown)'}")
                lines.append(f"DAO: {sel.dao_short or '(unknown)'}")
                if sel.fqcn:
                    lines.append(f"Class: {sel.fqcn}")
        if original.strip():
            lines.extend(("", "Original SQL:", original.rstrip()))
        if params.strip():
            lines.extend(("", "Params:", params.rstrip()))
        if result.strip():
            lines.extend(("", "Formatted SQL:", result.rstrip()))
        return "\n".join(lines).strip()

    def _copy_text_to_clipboard(text: str, label: str):
        if not text.strip():
            _notice(f"Nothing to copy for {label}", accent=False)
            return
        try:
            app.clipboard_clear()
            app.clipboard_append(text)
            app._toast.show(f"{label} copied to clipboard", 1100, "success")
        except Exception:
            _notice("Clipboard copy failed", accent=False)

    def _copy_current_option(kind: str):
        if kind == "original":
            _copy_text_to_clipboard(_current_original_sql(), "Original SQL")
        elif kind == "params":
            _copy_text_to_clipboard(_current_params_text(), "Params")
        elif kind == "summary":
            _copy_text_to_clipboard(_current_summary_text(), "SQL + params summary")
        else:
            _copy_text_to_clipboard(_current_result_text(), "Formatted SQL")

    def _copy_result():
        _copy_current_option("formatted")

    def _current_source() -> dict | None:
        if state["direct"]:
            return None
        sel = state.get("selected")
        if not sel:
            return None
        return {
            "kind": "logsql",
            "id": sel.id,
            "path": path_var.get().strip(),
            "dao": sel.dao_short or "",
            "type": sel.statement_type or "",
        }

    def _send_to_translator(*, new_tab: bool = False):
        text = _current_result_text()
        if not text.strip():
            _notice("Nothing to send — pick a statement first", accent=False)
            return
        try:
            if new_tab:
                # Open a fresh doc tab so the active one isn't stomped —
                # useful when comparing the extracted SQL with what's
                # already in the translator. Title hints at the source so
                # the user can tell tabs apart at a glance.
                title = _suggest_send_tab_title()
                app._new_doc_tab(initial_input=text, title=title, source=_current_source())
                app._toast.show(f"Sent to new tab: {title}", 1500, "success")
                _notice("Sent to a new tab", accent=True)
            else:
                app.input_box.configure(state="normal")
                app.input_box.delete("1.0", "end")
                app.input_box.insert("1.0", text)
                app._remember_log_sql_source(_current_source())
                if embedded:
                    app._set_mode("inline")
                else:
                    app.on_translate()
                app._toast.show("Sent SQL into translator input", 1300, "success")
                _notice("Sent into translator input", accent=True)
        except Exception:
            _notice("Couldn't reach translator input", accent=False)

    def _suggest_send_tab_title() -> str:
        """Build a short, recognisable tab title from the current
        selection. Examples: `id=189369c1`, `PdaDataSelectDao#search`,
        `Direct extract`."""
        if state["direct"]:
            return "Direct extract"
        sel = state.get("selected")
        if not sel:
            return "Extracted SQL"
        parts = []
        if sel.dao_short:
            parts.append(sel.dao_short)
        if sel.id:
            parts.append(f"id={sel.id}")
        return " · ".join(parts) if parts else "Extracted SQL"

    # ── Direct mode (paste SQL + params, no log file) ─────────────────
    direct_panel = tk.Frame(dlg, bg=t["bg"])
    direct_panel.columnconfigure(0, weight=2, uniform="dcols")
    direct_panel.columnconfigure(1, weight=1, uniform="dcols")
    direct_panel.columnconfigure(2, weight=2, uniform="dcols")
    direct_panel.rowconfigure(1, weight=1)

    for col, lbl in enumerate(("SQL or copied log lines", "PARAM(S)", "RESULT")):
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
            frame, wrap=_extract_wrap_mode(), font=app._mono, undo=True,
            bg=t["output_bg"], fg=t["fg"], insertbackground=t["insert"],
            relief="flat", borderwidth=0,
        )
        _register_text_box(frame, box)
        box.pack(side="left", fill="both", expand=True)
        _apply_extract_text_options()
        return box

    direct_sql    = _make_direct_text(0)
    direct_params = _make_direct_text(1)
    direct_result = _make_direct_text(2)
    _configure_highlight_tags(direct_result)
    direct_result.configure(state="disabled")

    direct_actions = tk.Frame(direct_panel, bg=t["bg"])
    direct_actions.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))

    def _direct_process():
        sql = direct_sql.get("1.0", "end-1c")
        params_raw = direct_params.get("1.0", "end-1c")
        pasted = "\n".join(part for part in (sql, params_raw) if part.strip())
        pasted_stmt = extract_pasted_statement(pasted)
        if pasted_stmt is not None:
            sql = pasted_stmt.sql
            params_raw = pasted_stmt.params_raw
            _set_text(direct_sql, sql)
            _set_text(direct_params, params_raw)
        params = parse_params(params_raw)
        marked, _ = combine_sql_params_marked(sql, params)
        marked_pretty = pretty_sql(marked)
        pretty, subst_ranges = extract_subst_ranges(marked_pretty)
        _render_sql_highlighted(direct_result, pretty, subst_ranges)
        n_q = count_placeholders(sql)
        if n_q != len(params):
            _notice(f"⚠ {n_q} ?s vs {len(params)} params — counts don't match", accent=False)
        elif pasted_stmt is not None:
            _notice(f"Parsed log id={pasted_stmt.id} and combined {len(params)} param(s)")
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
    _apply_recent_panel_visibility()
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
    if embedded and on_close:
        dlg.bind("<Escape>", lambda _e: on_close())
    else:
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    # Alt+1..9 — switch to the Nth recent log path. The digit is also
    # printed on each chip label so the hotkey is discoverable.
    def _switch_by_index(idx: int):
        recents = settings.get("recent_paths") or []
        if 0 <= idx < len(recents):
            _switch_to_path(recents[idx])
    for n in range(1, 10):
        # `Alt-KeyPress-N` matches both the digit-row key and numeric
        # keypad on most platforms.
        dlg.bind(
            f"<Alt-KeyPress-{n}>",
            lambda _e, i=n - 1: _switch_by_index(i),
        )
        # Alt + Cmd shadow for Mac users who type Option as Alt.
        dlg.bind(
            f"<Mod1-KeyPress-{n}>",
            lambda _e, i=n - 1: _switch_by_index(i),
        )

    def _on_destroy(_e=None):
        if embedded and getattr(app, "_log_sql_panel", None) is dlg:
            app._log_sql_panel = None
        if not embedded and getattr(app, "_log_sql_dialog", None) is dlg:
            app._log_sql_dialog = None
        if getattr(app, "_log_sql_apply_text_options", None) is _apply_extract_text_options:
            app._log_sql_apply_text_options = None
        if getattr(app, "_log_sql_select_source", None) is _select_source:
            app._log_sql_select_source = None
    dlg.bind("<Destroy>", _on_destroy)

    search_entry.focus_set()
    return dlg

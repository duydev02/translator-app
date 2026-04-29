"""Command palette — fuzzy-find any command, setting, or recent translation
and run it with two keystrokes. Bound to Cmd+P / Ctrl+P.

Layout: a borderless Toplevel anchored near the top of the main window with
a single-line entry above a list of matches. Up/Down navigates, Enter runs
the selected command, Esc dismisses.

Commands are gathered from the running app instance — we don't hard-code
anything that's not actually wired up. A command is a plain dict:
    {"label": "Toggle theme", "category": "View", "run": app.toggle_theme}

Fuzzy matching: characters of the query must appear in order in the label
(case-insensitive). Score = shorter label / earlier first-match wins.
"""

import tkinter as tk

from ...themes import THEMES


def open_command_palette(app):
    existing = getattr(app, "_command_palette", None)
    if existing and existing.winfo_exists():
        existing.lift()
        existing.focus_force()
        return

    t = THEMES[app._theme]
    commands = _collect_commands(app)
    if not commands:
        app._toast.show("No commands available", 1000, "info")
        return

    dlg = tk.Toplevel(app)
    app._command_palette = dlg
    # Borderless, top-most popup.
    dlg.wm_overrideredirect(True)
    dlg.attributes("-topmost", True)
    dlg.configure(bg=t["muted_bg"])

    # Outer border via a 1px frame, inner area uses surface.
    inner = tk.Frame(dlg, bg=t["surface"])
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    entry_var = tk.StringVar()
    entry = tk.Entry(
        inner, textvariable=entry_var, font=("Segoe UI", 11),
        bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", bd=0,
    )
    entry.pack(fill="x", padx=12, pady=(10, 6), ipady=6)

    list_frame = tk.Frame(inner, bg=t["surface"])
    list_frame.pack(fill="both", expand=True, padx=4, pady=(0, 8))
    listbox = tk.Listbox(
        list_frame, font=("Segoe UI", 10), height=10,
        bg=t["surface"], fg=t["fg"],
        selectbackground=t["accent"], selectforeground=t["accent_fg"],
        activestyle="none", relief="flat", bd=0, highlightthickness=0,
    )
    listbox.pack(side="left", fill="both", expand=True)

    hint = tk.Label(
        inner, text="↑↓ navigate · Enter run · Esc close",
        font=("Segoe UI", 8), bg=t["surface"], fg=t["fg_muted"], anchor="e",
    )
    hint.pack(fill="x", padx=12, pady=(0, 8))

    state = {"matches": commands[:]}

    def _refresh():
        q = entry_var.get().strip()
        results = _fuzzy_filter(commands, q)
        state["matches"] = results
        listbox.delete(0, tk.END)
        for cmd in results[:200]:
            cat = cmd.get("category") or ""
            label = cmd.get("label") or ""
            display = f"{label}    ·    {cat}" if cat else label
            listbox.insert(tk.END, display)
        if listbox.size():
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(0)
            listbox.activate(0)

    def _run_selected(_evt=None):
        sel = listbox.curselection()
        if not sel:
            return
        cmd = state["matches"][sel[0]]
        dlg.destroy()
        try:
            cmd["run"]()
        except Exception as e:
            try:
                app._toast.show(f"Command failed: {e}", 1500, "danger")
            except Exception:
                pass

    def _move_selection(delta):
        cur = listbox.curselection()
        n = listbox.size()
        if not n:
            return
        idx = (cur[0] if cur else 0) + delta
        idx = max(0, min(n - 1, idx))
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(idx)
        listbox.activate(idx)
        listbox.see(idx)

    entry_var.trace_add("write", lambda *_: _refresh())
    entry.bind("<Down>",  lambda e: (_move_selection(1),  "break"))
    entry.bind("<Up>",    lambda e: (_move_selection(-1), "break"))
    entry.bind("<Return>", _run_selected)
    entry.bind("<Escape>", lambda e: dlg.destroy())
    listbox.bind("<Double-1>", _run_selected)
    listbox.bind("<Return>",   _run_selected)
    # Keep typing in the entry even after clicking the list.
    listbox.bind("<Button-1>", lambda e: dlg.after_idle(entry.focus_set), add="+")

    # Position: centred horizontally near the top of the app window.
    app.update_idletasks()
    aw, ah = app.winfo_width(), app.winfo_height()
    ax, ay = app.winfo_rootx(), app.winfo_rooty()
    pop_w, pop_h = 640, 360
    px = ax + max(0, (aw - pop_w) // 2)
    py = ay + 80
    sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
    px = max(4, min(px, sw - pop_w - 4))
    py = max(4, min(py, sh - pop_h - 4))
    dlg.wm_geometry(f"{pop_w}x{pop_h}+{px}+{py}")
    dlg.focus_force()
    entry.focus_set()
    _refresh()

    # Close on click-outside
    def _on_click(event):
        w = event.widget
        # Allow clicks inside the popup; close on clicks anywhere else.
        while w is not None:
            if w == dlg:
                return
            try:
                w = w.master
            except Exception:
                break
        try:
            dlg.destroy()
        except Exception:
            pass
    bind_id = app.bind("<Button-1>", _on_click, add="+")

    def _on_destroy(_e=None):
        try:
            app.unbind("<Button-1>", bind_id)
        except Exception:
            pass
        if getattr(app, "_command_palette", None) is dlg:
            app._command_palette = None
    dlg.bind("<Destroy>", _on_destroy)


# ── Command catalogue ────────────────────────────────────────────────────────
def _collect_commands(app):
    """Build the list of commands available right now. Each command is
    {label, category, run}. Categories are used purely for display, so a
    palette user can see at a glance what kind of action they're invoking."""
    cmds = []

    def add(label, run, category="Action"):
        cmds.append({"label": label, "category": category, "run": run})

    # ── Mode / direction
    try:
        add("Switch mode: Inline Replace",
            lambda: app._set_mode("inline"), "Mode")
        add("Switch mode: Design Doc",
            lambda: app._set_mode("designdoc"), "Mode")
        add("Direction: Physical → Logical",
            lambda: app._set_direction("forward"), "Direction")
        add("Direction: Logical → Physical",
            lambda: app._set_direction("reverse"), "Direction")
    except Exception:
        pass

    # ── Translate / clear / open / save
    add("Translate now",                   app.on_translate,        "Action")
    add("Clear input + output",            app.on_clear,            "Action")
    add("Copy output to clipboard",        app.on_copy,             "Clipboard")
    add("Save output to file…",            app.on_export,           "File")
    add("Open file…",                      app.on_open_file,        "File")
    add("Reload db_schema_output.json",    app.on_reload_json,      "File")

    # ── Settings toggles
    add("Toggle theme (light / dark)",     app.toggle_theme,        "View")
    add("Toggle pane orientation",         app.toggle_pane_orient,  "View")
    add("Toggle line numbers",             app.toggle_line_numbers, "View")
    try:
        def _toggle_wrap():
            app._word_wrap.set(not app._word_wrap.get())
            app._apply_word_wrap()
        add("Toggle word wrap", _toggle_wrap, "View")
    except Exception:
        pass
    try:
        def _toggle_autopaste():
            app._auto_paste.set(not app._auto_paste.get())
            app._on_auto_paste_toggle()
        add("Toggle auto-paste from clipboard", _toggle_autopaste, "View")
    except Exception:
        pass

    # ── Dialogs
    add("Open Filter…",            app.open_filter_dialog,         "Dialog")
    add("Open Exclusions…",        app.open_exclusions_dialog,     "Dialog")
    add("Open User Map…",          app.open_user_map_dialog,       "Dialog")
    try:
        add("Open Schema Browser…", app.open_schema_browser,        "Dialog")
        add("Open Schema Browser scoped to input names…",
            app.open_schema_browser_for_input,                       "Dialog")
    except Exception:
        pass
    try:
        add("Open Snippets…",       app.open_snippets_dialog,       "Dialog")
    except Exception:
        pass
    try:
        add("Inspect SQL (Design Doc only)", app.open_inspect_dialog, "Dialog")
    except Exception:
        pass
    add("Show keyboard shortcuts (Help)",  app.show_help_dialog,    "Help")

    # ── Doc tabs
    add("Doc tab: New",                lambda: app._new_doc_tab(),         "Tabs")
    add("Doc tab: Close current",      lambda: app._close_doc_tab(app._active_doc), "Tabs")
    add("Doc tab: Next",               lambda: app._cycle_doc_tab(1),      "Tabs")
    add("Doc tab: Previous",           lambda: app._cycle_doc_tab(-1),     "Tabs")

    # ── Zoom
    add("Zoom: In",     app.zoom_in,    "View")
    add("Zoom: Out",    app.zoom_out,   "View")
    add("Zoom: Reset",  app.zoom_reset, "View")

    return cmds


# ── Fuzzy matcher ────────────────────────────────────────────────────────────
def _fuzzy_filter(commands, query):
    if not query:
        return list(commands)
    q = query.lower()
    scored = []
    for cmd in commands:
        label = (cmd.get("label") or "").lower()
        category = (cmd.get("category") or "").lower()
        haystack = label + " " + category
        score = _fuzzy_score(haystack, q, label)
        if score is not None:
            scored.append((score, cmd))
    scored.sort(key=lambda s: s[0])
    return [cmd for _s, cmd in scored]


def _fuzzy_score(haystack, query, label):
    """Lower is better. None ⇒ no match."""
    # Substring shortcut — strongly preferred.
    pos = haystack.find(query)
    if pos != -1:
        return pos * 2 + len(label)
    # In-order character match.
    j = 0
    last_pos = -1
    score = 0
    for ch in query:
        idx = haystack.find(ch, j)
        if idx == -1:
            return None
        if last_pos != -1:
            score += idx - last_pos
        last_pos = idx
        j = idx + 1
    return score * 4 + len(label) + 100   # pure-fuzzy is worse than substring

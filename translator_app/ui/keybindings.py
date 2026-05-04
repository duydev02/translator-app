"""Keyboard-binding registration for the main Translator window.

Pulled out of `app.py` so the giant `__init__` / `_build` body stays
focused on layout. Importing app.py used to mean reading 30 lines of
`self.bind_all(...)` repetition; that lives here now.

Each entry is `(sequence_or_list_of_sequences, attr_name)`:
- `sequence`            — Tk event spec, e.g. "<Control-s>".
- list of sequences     — bind the same callback to every sequence (used
                          for cross-platform Cmd/Ctrl pairs).
- `attr_name`           — name of a method on the app to call (no args).
                          The dispatcher hands the Tk event in but the
                          callable doesn't have to accept it.

The order matches the historical bindings in `app.py` so behaviour is
identical after the refactor.
"""

from __future__ import annotations

# Each row: (sequence-or-list, app-method-name)
_BINDINGS = [
    ("<Control-BackSpace>", "on_clear"),
    ("<Control-Shift-C>",   "on_copy"),
    ("<Control-s>",         "on_export"),
    ("<Control-r>",         "on_reload_json"),
    ("<Control-f>",         "open_search_bar"),
    ("<Escape>",            "close_search_bar"),
    ("<F1>",                "show_help_dialog"),
    ("<Control-m>",         "toggle_mode"),
    ("<Control-Shift-D>",   "toggle_direction"),
    ("<Control-plus>",      "zoom_in"),
    ("<Control-equal>",     "zoom_in"),
    ("<Control-minus>",     "zoom_out"),
    ("<Control-0>",         "zoom_reset"),
    ("<Control-l>",         "toggle_line_numbers"),
    # Command palette — cross-platform
    (("<Control-p>", "<Command-p>"),               "open_command_palette"),
    # Schema Browser
    (("<Control-b>", "<Command-b>"),               "open_schema_browser"),
    (("<Control-Shift-B>", "<Command-Shift-B>"),   "open_schema_browser_for_input"),
    # Extract SQL from log (Ctrl+L alone toggles line numbers)
    (("<Control-Shift-L>", "<Command-Shift-L>"),   "open_log_sql_dialog"),
    # Snippets
    (("<Control-j>", "<Command-j>"),               "open_snippets_dialog"),
    # Cmd/Ctrl+Q close handler — bypasses WM_DELETE_WINDOW on macOS
    (("<Control-q>", "<Command-q>", "<Command-Q>"), "on_close"),
]

# Doc-tab bindings depend on app state at call time, so they're handled
# inline rather than via the simple `attr_name` table above.
_TAB_BINDINGS = [
    ("<Control-t>",       lambda app, e: app._new_doc_tab()),
    ("<Control-w>",       lambda app, e: app._close_doc_tab(app._active_doc)),
    ("<Control-Tab>",     lambda app, e: app._cycle_doc_tab(1)),
    ("<Control-Shift-Tab>", lambda app, e: app._cycle_doc_tab(-1)),
]


def install(app):
    """Wire every keyboard shortcut to the running app instance.

    Idempotent: if called twice, the new bindings replace the old ones
    silently (Tk's `bind_all` already overwrites)."""
    # Ctrl+Enter is bound to the input widget specifically so it fires
    # only when the user is editing.
    app.input_box.bind("<Control-Return>", app._on_ctrl_enter)

    for seq_spec, attr_name in _BINDINGS:
        method = getattr(app, attr_name, None)
        if method is None:
            continue
        sequences = seq_spec if isinstance(seq_spec, tuple) else (seq_spec,)
        for seq in sequences:
            app.bind_all(seq, _wrap(method))

    for seq, fn in _TAB_BINDINGS:
        app.bind_all(seq, lambda e, fn=fn: fn(app, e))


def _wrap(method):
    """Adapt a no-arg callable to Tk's `(event)` binding signature."""
    return lambda _e: method()

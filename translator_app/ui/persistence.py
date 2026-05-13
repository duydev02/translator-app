"""Save/load helpers used by the main window.

The translator persists state to two files:

* `translator_settings.json` — one big JSON blob covering everything from
  the active theme to per-tab content. Most code reads/writes via the
  in-memory `app._settings` dict; on a meaningful change, we flush the
  dict to disk through these helpers.
* `translator_history.txt` — recent inputs (plain text). Managed by
  `translator_app.config`.

Two persistence cadences:

* **Eager** — `persist_pref(app, key, value)` flips a single key and
  writes immediately. Used for toggles where losing the choice on a
  force-quit would be annoying (theme, line numbers, word wrap, …).
* **Debounced** — `schedule_doc_save(app)` re-stores the active doc-tab's
  input shortly after the last keystroke. Cheap insurance against
  Cmd+Q / kill / crash without firing on_close.

`finalize_on_close(app)` is the full snapshot: every state-bearing
setting plus all doc tabs. Called from `on_close`, but should also be
safe to call from any other "we're shutting down" path.
"""

from __future__ import annotations

from ..config import save_settings


# ── Doc-tab serialization ────────────────────────────────────────────────────
def serialize_doc_tabs(app):
    """Render `app._doctabs` into the JSON-friendly shape stored in
    `translator_settings.json`. Used by both the debounced save and the
    on-close save path."""
    return [
        {
            "title":        d.get("title", ""),
            "input":        d.get("input", ""),
            "mode":         d.get("mode", "inline"),
            "direction":    d.get("direction", "forward"),
            "manual_title": bool(d.get("manual_title", False)),
        }
        for d in app._doctabs
    ]


# ── Eager persistence (one key) ──────────────────────────────────────────────
def persist_pref(app, key, value):
    """Update a single setting key and flush to disk immediately.
    Quietly absorbs IOErrors so a malfunctioning fs never kills a toggle."""
    try:
        app._settings[key] = value
        save_settings(app._settings)
    except Exception:
        pass


# ── Debounced doc-tab save ───────────────────────────────────────────────────
def schedule_doc_save(app, delay_ms=2500):
    """Persist the active doc-tab's input shortly after the user stops
    typing. Reschedules on each call so a fast typist only writes once."""
    if getattr(app, "_docs_save_job", None):
        try:
            app.after_cancel(app._docs_save_job)
        except Exception:
            pass
    app._docs_save_job = app.after(delay_ms, lambda: persist_doc_tabs_now(app))


def persist_doc_tabs_now(app):
    """Capture the active doc-tab's live state and write doc_tabs +
    active_doc to disk. Called from the debounced job above and from
    on_close. Errors are swallowed."""
    app._docs_save_job = None
    try:
        app._capture_active_doc()
        app._settings["doc_tabs"]   = serialize_doc_tabs(app)
        app._settings["active_doc"] = app._active_doc
        save_settings(app._settings)
    except Exception:
        pass


# ── Full snapshot on close ───────────────────────────────────────────────────
def _collect_full_snapshot(app):
    """Build the complete state dict that's written on shutdown.
    Deliberately defensive: any single getter that fails must not block
    the rest of the snapshot."""
    snap = {}

    def _set(key, get):
        try:
            snap[key] = get()
        except Exception:
            pass

    # Core mode / theme
    _set("theme",     lambda: app._theme)
    _set("mode",      lambda: app._mode.get())
    _set("direction", lambda: app._direction.get())
    _set("filter_schemas", lambda: sorted(app._filter_schemas))
    _set("filter_tables",  lambda: sorted(app._filter_tables))

    # Design Doc section toggles
    for key, attr in (
        ("design_uppercase",         "_uppercase_var"),
        ("design_show_overview",     "_show_overview"),
        ("design_show_sql_logical",  "_show_sql_logical"),
        ("design_show_sql_physical", "_show_sql_physical"),
        ("design_show_stype",        "_show_stype"),
        ("design_show_target",       "_show_target"),
        ("design_show_projection",   "_show_projection"),
        ("design_show_from",         "_show_from"),
        ("design_show_join",         "_show_join"),
        ("design_show_where",        "_show_where"),
        ("design_show_group",        "_show_group"),
        ("design_show_having",       "_show_having"),
        ("design_show_order",        "_show_order"),
        ("design_show_footer",       "_show_footer"),
        ("design_show_stats",        "_show_stats"),
    ):
        var = getattr(app, attr, None)
        if var is not None:
            _set(key, var.get)

    # Layout / view
    _set("pane_orient",       lambda: app._pane_orient)
    _set("show_line_numbers", lambda: bool(app._show_line_numbers.get()))
    _set("word_wrap",         lambda: bool(app._word_wrap.get()))
    _set("auto_paste",        lambda: bool(app._auto_paste.get()))
    _set("font_size",         lambda: app._font_size)
    _set("geometry",          lambda: app.winfo_geometry())
    return snap


def finalize_on_close(app):
    """Write everything that needs to survive a restart and run cleanup
    side-effects (currently: append the current input to history if it
    looks substantial). Best-effort; never raises."""
    try:
        # Doc tabs first (uses _capture_active_doc to grab live input).
        app._capture_active_doc()
        app._settings["doc_tabs"]   = serialize_doc_tabs(app)
        app._settings["active_doc"] = app._active_doc
        # Full settings snapshot.
        app._settings.update(_collect_full_snapshot(app))
        save_settings(app._settings)
        # History — only save non-trivial input.
        text = (app._current_input() or "").strip()
        if text and len(text) > 10:
            try:
                app._add_history(text)
            except Exception:
                pass
    except Exception:
        pass

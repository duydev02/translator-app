"""Schema-aware autocomplete for the input Text widget.

When the user is typing inside `input_box`, watch the word at the caret
and — when it's at least 2 characters and prefix-matches a known table or
column — pop a small `Listbox` below the caret with suggestions.

Behaviour:
- ↓ / ↑                navigate.
- Tab / Enter / →      accept the highlighted suggestion (replaces the
                        current word at the caret).
- Esc / clicking away  dismiss without inserting.
- Typing               re-filters live; if no matches, the popup hides
                        until typing produces a match again.

The autocomplete reads the in-memory schema indexes the app already has
(`table_index`, `column_index`), so it stays in sync with the User Map
overrides. It runs entirely on the main thread — even at 10K columns,
the `startswith` walk is fast enough not to lag a fast typist.
"""

from __future__ import annotations

import re
import tkinter as tk

from ..themes import THEMES


# The "word" at the caret: a contiguous run of identifier chars ending at
# the cursor. Underscores allowed; first char must be a letter.
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*$")
# Minimum prefix length before we bother showing a popup (1 char would be
# noisy — a freshly-typed `R` would match every R_* table).
_MIN_PREFIX_LEN = 2
# Maximum suggestions in the popup. Anything longer is a sign you should
# narrow the prefix.
_MAX_SUGGESTIONS = 10


class Autocomplete:
    """Attach autocomplete behaviour to a single Text widget."""

    def __init__(self, app, text_widget):
        self.app = app
        self.text = text_widget
        self.popup = None
        self.listbox = None
        self.prefix = ""        # the word currently being completed
        self.prefix_start = None    # tk index where the word begins
        self._suggestions = []

        # Re-evaluate after every key release. We bind on KeyRelease (not
        # KeyPress) so the inserted character is already in the buffer and
        # the word at the caret reflects what the user just typed.
        self.text.bind("<KeyRelease>", self._on_key_release, add="+")
        self.text.bind("<Button-1>",   self._hide_on_click,  add="+")
        self.text.bind("<FocusOut>",   lambda e: self._hide_popup(), add="+")

    # ── Lifecycle helpers ───────────────────────────────────────────────────
    def _hide_popup(self):
        if self.popup is not None:
            try:
                self.popup.destroy()
            except Exception:
                pass
            self.popup = None
            self.listbox = None

    def _hide_on_click(self, _evt):
        # Any mouse click in the text moves the caret; close the popup so
        # we don't leave a stale list hanging.
        self._hide_popup()

    # ── Key handling ────────────────────────────────────────────────────────
    def _on_key_release(self, event):
        # Navigation keys are handled here even though no popup may yet
        # exist — when the popup IS open, we want them to drive the list.
        if self.popup is not None and self.listbox is not None:
            if event.keysym in ("Down", "Up"):
                self._move_selection(1 if event.keysym == "Down" else -1)
                return "break"
            if event.keysym in ("Return", "Tab", "Right"):
                self._accept_current()
                return "break"
            if event.keysym == "Escape":
                self._hide_popup()
                return "break"
        # On any other key (including modifiers / arrows when popup is
        # closed), just refresh.
        if event.keysym in (
            "Shift_L", "Shift_R", "Control_L", "Control_R",
            "Meta_L", "Meta_R", "Alt_L", "Alt_R", "Caps_Lock",
        ):
            return None
        self._refresh()

    # ── Suggestion lookup ───────────────────────────────────────────────────
    def _current_word(self):
        """Return (prefix, start_index) for the word ending at the caret,
        or (None, None) if there's no identifier-like word right behind
        the caret."""
        try:
            line_text = self.text.get("insert linestart", "insert")
        except tk.TclError:
            return None, None
        m = _WORD_RE.search(line_text)
        if not m:
            return None, None
        # Convert "Lline.col" to a real index for the start of the word.
        line, col = self.text.index("insert").split(".")
        start_col = int(col) - len(m.group(0))
        return m.group(0), f"{line}.{start_col}"

    def _suggestions_for(self, prefix):
        if not prefix or len(prefix) < _MIN_PREFIX_LEN:
            return []
        up = prefix.upper()
        out = []
        seen = set()

        def _add(name):
            if name and name not in seen and name.upper().startswith(up):
                seen.add(name)
                out.append(name)

        # Tables come first — there are usually fewer of them.
        for name in getattr(self.app, "table_index", {}):
            if len(out) >= _MAX_SUGGESTIONS:
                return out
            _add(name)
        for name in getattr(self.app, "column_index", {}):
            if len(out) >= _MAX_SUGGESTIONS:
                return out
            _add(name)
        return out

    # ── Popup rendering ─────────────────────────────────────────────────────
    def _refresh(self):
        prefix, start = self._current_word()
        if prefix is None or len(prefix) < _MIN_PREFIX_LEN:
            self._hide_popup()
            return
        suggs = self._suggestions_for(prefix)
        if not suggs:
            self._hide_popup()
            return
        # Don't bother if the only suggestion equals the typed word — the
        # user has already finished it.
        if len(suggs) == 1 and suggs[0].upper() == prefix.upper():
            self._hide_popup()
            return

        self.prefix = prefix
        self.prefix_start = start
        self._suggestions = suggs

        if self.popup is None:
            self._make_popup()
        # Re-populate the listbox.
        self.listbox.delete(0, tk.END)
        for s in suggs:
            self.listbox.insert(tk.END, s)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(0)
        self.listbox.activate(0)
        # Resize the popup to fit (capped).
        rows = min(len(suggs), _MAX_SUGGESTIONS)
        try:
            self.listbox.configure(height=rows)
        except Exception:
            pass
        self._reposition()

    def _make_popup(self):
        t = THEMES[self.app._theme]
        self.popup = tk.Toplevel(self.text)
        self.popup.wm_overrideredirect(True)
        self.popup.attributes("-topmost", True)
        self.popup.configure(bg=t["muted_bg"])
        inner = tk.Frame(self.popup, bg=t["surface"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.listbox = tk.Listbox(
            inner, font=self.app._mono,
            bg=t["surface"], fg=t["fg"],
            selectbackground=t["accent"], selectforeground=t["accent_fg"],
            activestyle="none", relief="flat", bd=0,
            highlightthickness=0,
        )
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda e: self._accept_current())
        # Don't steal focus — keep the caret in the Text widget so typing
        # continues to update the suggestions.

    def _reposition(self):
        if self.popup is None:
            return
        try:
            bbox = self.text.bbox("insert")
        except tk.TclError:
            bbox = None
        if not bbox:
            return
        x, y, _w, h = bbox
        rx = self.text.winfo_rootx() + x
        ry = self.text.winfo_rooty() + y + h + 2
        # Clamp to screen.
        sw = self.popup.winfo_screenwidth()
        sh = self.popup.winfo_screenheight()
        # Set width to the longest suggestion (in chars × char width).
        try:
            char_w = self.app._mono.measure("M")
        except Exception:
            char_w = 8
        long_chars = max((len(s) for s in self._suggestions), default=20)
        pop_w  = min(max(140, (long_chars + 2) * char_w), 480)
        try:
            row_h = self.app._mono.metrics("linespace") + 2
        except Exception:
            row_h = 16
        rows = min(len(self._suggestions), _MAX_SUGGESTIONS)
        pop_h = max(40, rows * row_h + 4)
        rx = max(0, min(rx, sw - pop_w - 4))
        ry = max(0, min(ry, sh - pop_h - 4))
        try:
            self.popup.wm_geometry(f"{pop_w}x{pop_h}+{rx}+{ry}")
        except Exception:
            pass

    # ── Selection handling ──────────────────────────────────────────────────
    def _move_selection(self, delta):
        if not self.listbox:
            return
        n = self.listbox.size()
        if n == 0:
            return
        cur = self.listbox.curselection()
        idx = (cur[0] if cur else 0) + delta
        idx = max(0, min(n - 1, idx))
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(idx)
        self.listbox.activate(idx)
        self.listbox.see(idx)

    def _accept_current(self):
        if not (self.popup and self.listbox and self.prefix_start):
            return
        sel = self.listbox.curselection()
        if not sel:
            return
        chosen = self.listbox.get(sel[0])
        # Replace the typed prefix with the chosen suggestion.
        try:
            self.text.delete(self.prefix_start, "insert")
            self.text.insert(self.prefix_start, chosen)
        except tk.TclError:
            pass
        self._hide_popup()


def attach(app, text_widget):
    """Public entry point. Caller keeps the returned object alive (storing
    it on `app` is fine) so the bindings persist for the widget's life."""
    return Autocomplete(app, text_widget)

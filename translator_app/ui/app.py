import os
import re
import sys
import tkinter as tk
from tkinter import scrolledtext, font, ttk, filedialog

from ..config import (
    load_exclusions,
    load_history,
    load_settings,
    load_user_map,
    save_exclusions,
    save_history,
    save_settings,
    save_user_map,
)
from ..designdoc import java_to_design_doc
from ..paths import BASE_DIR, CUSTOM_SCHEMA, MAX_HISTORY, USER_MAP_FILE
from ..schema import (
    _filter_by_table_context,
    _is_ambiguous,
    load_index,
    merge_user_map,
)
from ..themes import THEMES
from ..translate import (
    _exclusion_ranges,
    _find_logical_tokens,
    _overlaps_any,
    _tokens,
    find_column_inconsistencies,
    find_unknown_tokens,
    translate_inline_mode,
    translate_reverse_inline_mode,
    translate_reverse_table_mode,
    translate_table_mode,
)
from .widgets import (
    _BaseTk,
    _DND_AVAILABLE,
    LineNumberCanvas,
    Toast,
    Tooltip,
)

if _DND_AVAILABLE:
    from tkinterdnd2 import DND_FILES   # type: ignore


class TranslatorApp(_BaseTk):
    def __init__(self, json_path):
        super().__init__()

        # Load persistent state
        self._settings   = load_settings()
        self._exclusions = load_exclusions()
        self._history    = load_history()

        # Load index
        self._json_path = json_path
        self._load_data()

        # Mutable state (persisted in settings)
        self._theme       = self._settings.get("theme", "light")
        self._mode        = tk.StringVar(value=self._settings.get("mode", "inline"))
        self._direction   = tk.StringVar(value=self._settings.get("direction", "forward"))
        self._filter_schemas = set(self._settings.get("filter_schemas", []))   # empty = all
        self._filter_tables  = set(self._settings.get("filter_tables",  []))   # empty = all
        self._font_size   = int(self._settings.get("font_size", 10))
        # "vertical" = input on top, output on bottom (default)
        # "horizontal" = input on left, output on right
        self._pane_orient = self._settings.get("pane_orient", "vertical")
        if self._pane_orient not in ("vertical", "horizontal"):
            self._pane_orient = "vertical"
        # Line-number sidebar toggle (Ctrl+L)
        self._show_line_numbers = tk.BooleanVar(
            value=bool(self._settings.get("show_line_numbers", False))
        )
        # Word-wrap toggle (default on)
        self._word_wrap = tk.BooleanVar(
            value=bool(self._settings.get("word_wrap", True))
        )
        # Auto-paste-on-focus toggle (default off — only triggers when content
        # looks like SQL / Java SQL-builder, see _looks_pasteable)
        self._auto_paste = tk.BooleanVar(
            value=bool(self._settings.get("auto_paste", False))
        )
        # Tracks the last clipboard content the auto-paste considered, so we
        # don't repeatedly re-paste the same thing on every focus event.
        self._last_clip_seen = ""

        # Transient state
        self._copy_job     = None
        self._autotr_job   = None
        self._input_hi_job = None
        self._spans         = []
        self._table_context = set()
        self._tooltip       = None
        self._toast         = None

        # Multi-input doc tabs — each entry: {title, input, mode, direction}
        # Shared across tabs: filter, exclusions, user map, theme, history, font.
        saved_tabs = self._settings.get("doc_tabs") or []
        self._doctabs = []
        for d in saved_tabs:
            if not isinstance(d, dict):
                continue
            self._doctabs.append({
                "title":        d.get("title") or "",
                "input":        d.get("input") or "",
                "mode":         d.get("mode") or self._mode.get(),
                "direction":    d.get("direction") or self._direction.get(),
                "manual_title": bool(d.get("manual_title", False)),
            })
        if not self._doctabs:
            self._doctabs.append({
                "title": "Tab 1", "input": "",
                "mode": self._mode.get(),
                "direction": self._direction.get(),
                "manual_title": False,
            })
        self._active_doc = self._settings.get("active_doc", 0)
        if not (0 <= self._active_doc < len(self._doctabs)):
            self._active_doc = 0

        self.title("Translator — Legacy Schema Helper")
        # Window-title-bar icon (separate from the exe's Explorer icon).
        # Search order:
        #   1. Bundled location (sys._MEIPASS/image.ico) when running as exe
        #   2. assets/image.ico  (source-checkout layout)
        #   3. image.ico         (legacy flat layout, backward compat)
        try:
            candidates = []
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                candidates.append(os.path.join(sys._MEIPASS, "image.ico"))
            candidates.extend([
                os.path.join(BASE_DIR, "assets", "image.ico"),
                os.path.join(BASE_DIR, "image.ico"),
            ])
            for p in candidates:
                if os.path.exists(p):
                    self.iconbitmap(p)
                    break
        except Exception:
            pass
        geom = self._settings.get("geometry", "1060x800")
        self.geometry(geom)
        self.minsize(780, 540)

        self._mono    = font.Font(family="Consolas", size=self._font_size)
        self._ui      = font.Font(family="Segoe UI", size=9)
        self._ui_b    = font.Font(family="Segoe UI", size=9,  weight="bold")
        self._btn     = font.Font(family="Segoe UI", size=10, weight="bold")
        self._small   = font.Font(family="Segoe UI", size=8)

        self._build()
        self._apply_theme()
        self._refresh_mode_tabs()
        self._refresh_excl_btn()
        self._refresh_filter_btn()
        self._refresh_umap_btn()
        self._refresh_layout_btn()
        self._refresh_history_menu()
        self._set_direction_label()
        self._show_placeholder_if_empty()
        self._apply_line_numbers()
        # Apply saved word-wrap state silently (skip the toast on startup).
        self._apply_word_wrap(show_toast=False)
        # Listen for window-level focus changes for the auto-paste feature.
        self.bind("<FocusIn>", self._on_window_focus_in, add="+")
        # FocusIn doesn't fire on macOS when a child keeps focus across an
        # app-switch, so back it up with a low-frequency clipboard poll.
        self._auto_paste_poll_id = None
        if bool(self._auto_paste.get()):
            self._start_auto_paste_poll()

        # Bindings
        self.input_box.bind("<Control-Return>", self._on_ctrl_enter)
        self.bind_all("<Control-BackSpace>",  lambda e: self.on_clear())
        self.bind_all("<Control-Shift-C>",    lambda e: self.on_copy())
        self.bind_all("<Control-s>",          lambda e: self.on_export())
        self.bind_all("<Control-r>",          lambda e: self.on_reload_json())
        self.bind_all("<Control-f>",          lambda e: self.open_search_bar())
        self.bind_all("<Escape>",             lambda e: self.close_search_bar())
        self.bind_all("<F1>",                 lambda e: self.show_help_dialog())
        self.bind_all("<Control-m>",          lambda e: self.toggle_mode())
        self.bind_all("<Control-Shift-D>",    lambda e: self.toggle_direction())
        self.bind_all("<Control-plus>",       lambda e: self.zoom_in())
        self.bind_all("<Control-equal>",      lambda e: self.zoom_in())
        self.bind_all("<Control-minus>",      lambda e: self.zoom_out())
        self.bind_all("<Control-0>",          lambda e: self.zoom_reset())
        self.bind_all("<Control-l>",          lambda e: self.toggle_line_numbers())
        self.bind_all("<Control-t>",          lambda e: self._new_doc_tab())
        self.bind_all("<Control-w>",          lambda e: self._close_doc_tab(self._active_doc))
        self.bind_all("<Control-Tab>",        lambda e: self._cycle_doc_tab(1))
        self.bind_all("<Control-Shift-Tab>",  lambda e: self._cycle_doc_tab(-1))
        self._render_doctabs()
        # Load the (possibly restored) active tab's input/mode/direction
        # into the live widgets.
        self._load_doc(self._active_doc)

        # Right-click context menu
        self.input_box.bind("<Button-3>",  lambda e: self._on_right_click(e, self.input_box))
        self.output_box.bind("<Button-3>", lambda e: self._on_right_click(e, self.output_box))

        # Auto-translate + input highlight
        self.input_box.bind("<KeyRelease>", self._on_input_change)
        self.input_box.bind("<<Paste>>",    self._on_paste)

        # Focus-in / focus-out for placeholder
        self.input_box.bind("<FocusIn>",  lambda e: self._clear_placeholder())
        self.input_box.bind("<FocusOut>", lambda e: self._show_placeholder_if_empty())
        # Defensive: if the placeholder somehow ends up visible while the box
        # has focus (e.g. after Ctrl+⌫ or a programmatic clear), wipe it on
        # the very first key/click so the user never edits placeholder text.
        self.input_box.bind("<Key>",       self._guard_placeholder_keypress, add="+")
        self.input_box.bind("<Button-1>",  self._guard_placeholder_click,    add="+")

        # Hover tooltip + toast
        self._tooltip = Tooltip(self)
        self._tooltip.set_theme_fn(lambda: THEMES[self._theme])
        self._toast = Toast(self)
        self._toast.set_theme_fn(lambda: THEMES[self._theme])
        self.output_box.bind("<Motion>", self._on_output_motion)
        self.output_box.bind("<Leave>",  lambda e: self._tooltip.hide())

        # Attach hover tooltips to discoverability-critical buttons
        self._install_button_tooltips()

        # Drag & drop
        if _DND_AVAILABLE:
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self._on_file_drop)
            except Exception:
                pass

        # Save settings on exit
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        # Cmd+Q on macOS frequently bypasses WM_DELETE_WINDOW. Bind it (and
        # Ctrl+Q on other platforms) to the same close handler so the doc-tab
        # input is captured before the process exits.
        self.bind_all("<Command-q>",     lambda e: self.on_close())
        self.bind_all("<Command-Q>",     lambda e: self.on_close())
        self.bind_all("<Control-q>",     lambda e: self.on_close())
        # Pending auto-save handle for doc-tab input.
        self._docs_save_job = None

    # ── Data loading ──────────────────────────────────────────────────────────
    def _load_data(self):
        ti, ci, rti, rci, schemas = load_index(self._json_path)
        self._user_map = load_user_map()
        merge_user_map(ti, ci, rti, rci, self._user_map)

        self.table_index      = ti
        self.column_index     = ci
        self.rev_table_index  = rti
        self.rev_column_index = rci
        # CUSTOM_SCHEMA is exposed in the filter dropdown if user has any overrides
        self.schemas = list(schemas)
        if (self._user_map.get("tables") or self._user_map.get("columns")):
            if CUSTOM_SCHEMA not in self.schemas:
                self.schemas.append(CUSTOM_SCHEMA)

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build(self):
        # ── Top bar ──────────────────────────────────────────────────────────
        self._topbar = tk.Frame(self, height=48)
        self._topbar.pack(fill="x")
        self._topbar.pack_propagate(False)

        self._tab_frame = tk.Frame(self._topbar)
        self._tab_frame.pack(side="left", padx=12, pady=8)
        tab_frame = self._tab_frame

        self._tab_table = tk.Button(tab_frame, text="Translation Table",
            font=self._ui_b, relief="flat", padx=14, pady=4, cursor="hand2", bd=0,
            command=lambda: self._set_mode("table"))
        self._tab_table.pack(side="left")

        self._tab_inline = tk.Button(tab_frame, text="Inline Replace",
            font=self._ui_b, relief="flat", padx=14, pady=4, cursor="hand2", bd=0,
            command=lambda: self._set_mode("inline"))
        self._tab_inline.pack(side="left", padx=(2, 0))

        self._tab_designdoc = tk.Button(tab_frame, text="Design Doc",
            font=self._ui_b, relief="flat", padx=14, pady=4, cursor="hand2", bd=0,
            command=lambda: self._set_mode("designdoc"))
        self._tab_designdoc.pack(side="left", padx=(2, 0))

        self._tab_sep = tk.Label(tab_frame, text="│", font=self._ui_b)
        self._tab_sep.pack(side="left", padx=10)

        self._tab_forward = tk.Button(tab_frame, text="Phys → Logic",
            font=self._ui_b, relief="flat", padx=14, pady=4, cursor="hand2", bd=0,
            command=lambda: self._set_direction("forward"))
        self._tab_forward.pack(side="left")

        self._tab_reverse = tk.Button(tab_frame, text="Logic → Phys",
            font=self._ui_b, relief="flat", padx=14, pady=4, cursor="hand2", bd=0,
            command=lambda: self._set_direction("reverse"))
        self._tab_reverse.pack(side="left", padx=(2, 0))

        # Right side: help + a single Settings menu (theme, layout, filter,
        # exclusions, user map are all consolidated under here)
        self._help_btn = tk.Button(self._topbar, text="?",
            font=self._ui_b, relief="flat", padx=10, pady=4, cursor="hand2", bd=0,
            command=self.show_help_dialog)
        self._help_btn.pack(side="right", padx=(6, 12), pady=8)

        self._settings_btn = tk.Menubutton(self._topbar, text="⚙  Settings",
            font=self._ui_b, relief="flat", padx=12, pady=4, cursor="hand2", bd=0)
        self._settings_menu = tk.Menu(self._settings_btn, tearoff=0)
        self._settings_btn["menu"] = self._settings_menu
        # Items — indices used by the refresh helpers below
        self._SETTINGS_IDX_THEME      = 0
        self._SETTINGS_IDX_LAYOUT     = 1
        self._SETTINGS_IDX_LINENO     = 2
        self._SETTINGS_IDX_WRAP       = 3
        self._SETTINGS_IDX_AUTOPASTE  = 4
        # idx 5 = separator
        self._SETTINGS_IDX_FILTER     = 6
        self._SETTINGS_IDX_EXCL       = 7
        self._SETTINGS_IDX_UMAP       = 8
        # idx 9 = separator, then file ops (Open, Reload JSON)
        self._settings_menu.add_command(label="Theme",  command=self.toggle_theme)
        self._settings_menu.add_command(label="Layout", command=self.toggle_pane_orient)
        self._settings_menu.add_checkbutton(
            label="Line numbers (Ctrl+L)",
            variable=self._show_line_numbers,
            command=self._on_line_numbers_toggle_from_menu,
        )
        self._settings_menu.add_checkbutton(
            label="Word wrap",
            variable=self._word_wrap,
            command=self._apply_word_wrap,
        )
        self._settings_menu.add_checkbutton(
            label="Auto-paste from clipboard",
            variable=self._auto_paste,
            command=self._on_auto_paste_toggle,
        )
        self._settings_menu.add_separator()
        self._settings_menu.add_command(label="⚙  Filter…",     command=self.open_filter_dialog)
        self._settings_menu.add_command(label="⊘  Exclusions…", command=self.open_exclusions_dialog)
        self._settings_menu.add_command(label="🖉  User Map…",  command=self.open_user_map_dialog)
        self._settings_menu.add_separator()
        self._settings_menu.add_command(label="📂  Open file…",  command=self.on_open_file)
        self._settings_menu.add_command(label="⟳  Reload JSON", command=self.on_reload_json)
        self._settings_btn.pack(side="right", pady=8)

        # ── Doc-tab bar (multi-input) ────────────────────────────────────────
        self._doctabs_bar = tk.Frame(self, height=30)
        self._doctabs_bar.pack(fill="x", padx=12, pady=(4, 0))
        self._doctabs_bar.pack_propagate(False)
        self._doctabs_inner = tk.Frame(self._doctabs_bar)
        self._doctabs_inner.pack(side="left", fill="x", expand=True)
        self._doctabs_newbtn = tk.Button(
            self._doctabs_bar, text="+ New", font=self._ui_b,
            relief="flat", padx=10, pady=2, cursor="hand2", bd=0,
            command=self._new_doc_tab,
        )
        self._doctabs_newbtn.pack(side="right")

        # ── Paned window: input / output (orientation toggleable) ───────────
        # Using tk.PanedWindow (not ttk) because it supports `minsize`, which
        # prevents dragging the sash over the action bar.
        self._paned = tk.PanedWindow(
            self, orient=self._pane_orient,
            sashwidth=6, sashrelief="flat", bd=0, showhandle=False,
            opaqueresize=True,
        )
        self._paned.pack(fill="both", expand=True, padx=12, pady=(2, 0))

        # Top / left pane: input + header + action bar
        # Parent to self (not self._paned) so pane survives a paned.destroy()
        # during orientation toggling.
        top_pane = tk.Frame(self)
        self._top_pane = top_pane
        self._paned.add(top_pane, minsize=self._top_minsize(), stretch="always")

        # Pack order matters: anchor header top, action bar bottom FIRST
        # so those zones are reserved before the expanding input fills the middle.
        in_header = tk.Frame(top_pane)
        in_header.pack(side="top", fill="x", pady=(6, 2))

        self._lbl_in = tk.Label(in_header, text="Paste content here", font=self._ui_b, anchor="w")
        self._lbl_in.pack(side="left")

        self._hint_in = tk.Label(in_header,
            text="Ctrl+Enter translate · Ctrl+⌫ clear · F1 help",
            font=self._small, anchor="e")
        self._hint_in.pack(side="right")

        # Input box + history dropdown
        self._history_btn = tk.Menubutton(in_header, text="⌄ History",
            font=self._small, relief="flat", bd=0, padx=6, pady=0, cursor="hand2")
        self._history_menu = tk.Menu(self._history_btn, tearoff=0)
        self._history_btn["menu"] = self._history_menu
        self._history_btn.pack(side="right", padx=(6, 10))

        # Action bar — packed BEFORE the input so it reserves its vertical space
        self._actionbar = tk.Frame(top_pane, height=46)
        self._actionbar.pack(side="bottom", fill="x", pady=8)
        self._actionbar.pack_propagate(False)

        # Input fills whatever's left between header and action bar.
        # Wrap in a container so a line-number canvas can sit to its left.
        self._input_container = tk.Frame(top_pane)
        self._input_container.pack(side="top", fill="both", expand=True)
        self.input_box = scrolledtext.ScrolledText(
            self._input_container, wrap=tk.WORD, font=self._mono,
            relief="flat", borderwidth=0, padx=10, pady=8, undo=True,
        )
        self.input_box.pack(side="left", fill="both", expand=True)
        self._input_lnums = LineNumberCanvas(
            self._input_container, self.input_box, lambda: THEMES[self._theme]
        )  # packed / unpacked by _apply_line_numbers()
        # Horizontal scrollbar — only shown when word-wrap is off.
        self._input_hscroll = tk.Scrollbar(
            self._input_container, orient="horizontal",
            command=self.input_box.xview,
        )

        self._translate_btn = tk.Button(self._actionbar, text="▶  Translate  ·  Ctrl+Enter",
            font=self._btn, relief="flat", padx=20, pady=6, cursor="hand2", bd=0,
            command=self.on_translate)
        self._translate_btn.pack(side="left")

        # Clear / Open / Reload JSON were here previously. They're now reachable
        # via keyboard shortcuts (Ctrl+⌫ clear) and the Settings menu (Open file,
        # Reload JSON), keeping the action bar focused on Translate.

        # Uppercase toggle — only visible in Design Doc mode (see _refresh_mode_tabs)
        self._uppercase_var = tk.BooleanVar(value=bool(self._settings.get("design_uppercase", True)))
        self._upper_chk = tk.Checkbutton(
            self._actionbar, text="UPPERCASE columns",
            variable=self._uppercase_var, font=self._ui,
            bd=0, highlightthickness=0,
            command=self.on_translate,
        )

        # Design-doc section visibility toggles
        def _flag(key, default=True):
            return tk.BooleanVar(value=bool(self._settings.get(key, default)))

        self._show_overview     = _flag("design_show_overview")
        self._show_sql_logical  = _flag("design_show_sql_logical")
        self._show_sql_physical = _flag("design_show_sql_physical")
        self._show_stype        = _flag("design_show_stype")
        self._show_target       = _flag("design_show_target")
        self._show_projection   = _flag("design_show_projection")
        self._show_from         = _flag("design_show_from")
        self._show_join         = _flag("design_show_join")
        self._show_where        = _flag("design_show_where")
        self._show_group        = _flag("design_show_group")
        self._show_having       = _flag("design_show_having")
        self._show_order        = _flag("design_show_order")
        self._show_footer       = _flag("design_show_footer")

        self._sections_mb = tk.Button(
            self._actionbar, text="⚙ Sections ▾", font=self._ui_b,
            relief="flat", bd=0, padx=10, pady=6, cursor="hand2",
            command=self.toggle_sections_popup,
        )
        self._sections_popup = None    # held open until user clicks elsewhere / Escape
        # Not packed yet — handled by _refresh_mode_tabs

        self._status_var = tk.StringVar(value="")
        self._status_lbl = tk.Label(self._actionbar, textvariable=self._status_var,
            font=self._ui, anchor="w")
        self._status_lbl.pack(side="left", padx=14)

        # ── Bottom / right pane: output ──────────────────────────────────────
        # Parent to self (same reasoning as top_pane).
        bot_pane = tk.Frame(self)
        self._bot_pane = bot_pane
        self._paned.add(bot_pane, minsize=self._bot_minsize(), stretch="always")

        out_header = tk.Frame(bot_pane)
        out_header.pack(fill="x", pady=(2, 2))

        self._lbl_out = tk.Label(out_header, text="Translation result", font=self._ui_b, anchor="w")
        self._lbl_out.pack(side="left")

        self._copy_btn = tk.Button(out_header, text="⎘  Copy",
            font=self._ui_b, relief="flat", padx=10, pady=2, cursor="hand2", bd=0,
            command=self.on_copy)
        self._copy_btn.pack(side="right")

        self._save_btn = tk.Button(out_header, text="💾  Save…",
            font=self._ui_b, relief="flat", padx=10, pady=2, cursor="hand2", bd=0,
            command=self.on_export)
        self._save_btn.pack(side="right", padx=(0, 6))

        self._hint_out = tk.Label(out_header, text="Ctrl+Shift+C copy · Ctrl+S save · Ctrl+F find",
            font=self._small, anchor="e")
        self._hint_out.pack(side="right", padx=(0, 10))

        # Search bar (initially hidden)
        self._search_frame = tk.Frame(bot_pane)
        self._search_var = tk.StringVar()
        self._search_entry = tk.Entry(self._search_frame, textvariable=self._search_var,
            font=self._ui, relief="flat", bd=0)
        self._search_entry.pack(side="left", fill="x", expand=True, padx=(4, 6), pady=4, ipady=4)
        self._search_entry.bind("<Return>",  lambda e: self._search_next())
        self._search_entry.bind("<KeyRelease>", lambda e: self._search_highlight_all())
        self._search_prev_btn = tk.Button(self._search_frame, text="▲",
            font=self._small, relief="flat", bd=0, cursor="hand2",
            command=self._search_prev)
        self._search_prev_btn.pack(side="left", padx=2)
        self._search_next_btn = tk.Button(self._search_frame, text="▼",
            font=self._small, relief="flat", bd=0, cursor="hand2",
            command=self._search_next)
        self._search_next_btn.pack(side="left", padx=2)
        self._search_count_var = tk.StringVar(value="")
        self._search_count_lbl = tk.Label(self._search_frame,
            textvariable=self._search_count_var, font=self._small)
        self._search_count_lbl.pack(side="left", padx=6)
        self._search_close_btn = tk.Button(self._search_frame, text="✕",
            font=self._small, relief="flat", bd=0, cursor="hand2",
            command=self.close_search_bar)
        self._search_close_btn.pack(side="right", padx=4)
        # Don't pack _search_frame yet (hidden until Ctrl+F)

        self._output_container = tk.Frame(bot_pane)
        self._output_container.pack(side="top", fill="both", expand=True)
        self.output_box = scrolledtext.ScrolledText(
            self._output_container, wrap=tk.WORD, font=self._mono,
            relief="flat", borderwidth=0, padx=10, pady=8, state="disabled",
        )
        self.output_box.pack(side="left", fill="both", expand=True)
        self._output_lnums = LineNumberCanvas(
            self._output_container, self.output_box, lambda: THEMES[self._theme]
        )  # packed / unpacked by _apply_line_numbers()
        # Horizontal scrollbar — only shown when word-wrap is off.
        self._output_hscroll = tk.Scrollbar(
            self._output_container, orient="horizontal",
            command=self.output_box.xview,
        )

        # ── Status bar ──
        self._statusbar = tk.Frame(self, height=26)
        self._statusbar.pack(fill="x", side="bottom")
        self._statusbar.pack_propagate(False)

        self._sb_index = tk.Label(self._statusbar, text="", font=self._small, anchor="w")
        self._sb_index.pack(side="left", padx=6)

        self._sb_match = tk.Label(self._statusbar, text="", font=self._small, anchor="e")
        self._sb_match.pack(side="right", padx=8)

        self._refresh_index_stats()

        # Theme-tracked widget lists
        self._frames = [self._topbar, self._actionbar, self._statusbar, in_header,
                        out_header, top_pane, bot_pane, self._search_frame,
                        self._tab_frame, self._doctabs_bar, self._doctabs_inner, self]
        self._labels = [self._lbl_in, self._lbl_out, self._hint_in, self._hint_out,
                        self._sb_index, self._sb_match, self._status_lbl, self._tab_sep,
                        self._search_count_lbl]
        self._small_buttons = [self._search_prev_btn, self._search_next_btn,
                               self._search_close_btn]

        # ttk style handle (for Combobox + PanedWindow)
        self._ttk_style = ttk.Style()
        try:
            self._ttk_style.theme_use("clam")   # 'clam' respects our colors best
        except tk.TclError:
            pass

    # ── Theme ─────────────────────────────────────────────────────────────────
    def _apply_theme(self):
        t = THEMES[self._theme]
        for w in self._frames:
            w.configure(bg=t["bg"])
        for w in self._labels:
            w.configure(bg=t["bg"], fg=t["fg_muted"])
        self._lbl_in.configure(fg=t["fg"])
        self._lbl_out.configure(fg=t["fg"])
        self._status_lbl.configure(fg=t["success"])

        # Big text boxes (+ their scrollbars)
        self.input_box.configure(bg=t["surface"], fg=t["fg"], insertbackground=t["insert"])
        self.output_box.configure(bg=t["output_bg"], fg=t["fg"])
        self._theme_scrollbar(self.input_box.vbar, t)
        self._theme_scrollbar(self.output_box.vbar, t)

        self._translate_btn.configure(
            bg=t["accent"], fg=t["accent_fg"],
            activebackground=t["accent"], activeforeground=t["accent_fg"])
        for btn in (self._copy_btn, self._save_btn,
                    self._help_btn, self._history_btn,
                    self._settings_btn,
                    self._doctabs_newbtn,
                    *self._small_buttons):
            btn.configure(bg=t["muted_bg"], fg=t["muted_fg"],
                activebackground=t["muted_bg"], activeforeground=t["muted_fg"])
        # Re-render doc tabs so they pick up new theme colors
        if hasattr(self, "_doctabs_inner"):
            self._render_doctabs()
        # Settings menu colors + theme toggle label
        try:
            self._settings_menu.configure(
                bg=t["surface"], fg=t["fg"],
                activebackground=t["accent"], activeforeground=t["accent_fg"])
        except Exception:
            pass
        self._refresh_theme_menu_item()

        # Search entry
        self._search_entry.configure(bg=t["surface"], fg=t["fg"], insertbackground=t["insert"])

        # Uppercase checkbox (Design Doc mode)
        try:
            self._upper_chk.configure(
                bg=t["bg"], fg=t["fg"],
                activebackground=t["bg"], activeforeground=t["fg"],
                selectcolor=t["surface"],
            )
        except Exception:
            pass
        # Sections popup-opener button
        try:
            self._sections_mb.configure(
                bg=t["muted_bg"], fg=t["muted_fg"],
                activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            )
        except Exception:
            pass

        # ttk widgets (Combobox)
        self._apply_ttk_theme(t)

        # PanedWindow sash color
        try:
            self._paned.configure(bg=t["muted_bg"])
        except Exception:
            pass

        # Line-number canvases pick up new theme colours on next redraw
        try:
            self._input_lnums._schedule()
            self._output_lnums._schedule()
        except Exception:
            pass

        bold_mono = font.Font(family="Consolas", size=self._font_size, weight="bold")
        self.output_box.tag_configure("header",    foreground=t["tag_header"], font=bold_mono)
        self.output_box.tag_configure("physical",  foreground=t["tag_phys"])
        self.output_box.tag_configure("logical",   foreground=t["tag_logical"])
        self.output_box.tag_configure("meta",      foreground=t["tag_meta"])
        self.output_box.tag_configure("inline_table",  foreground=t["tag_table"],  underline=True)
        self.output_box.tag_configure("inline_column", foreground=t["tag_column"], underline=True)
        self.output_box.tag_configure("inline_ambig",  foreground=t["tag_ambig"],  underline=True)
        self.output_box.tag_configure("unknown",   foreground=t["warning"])
        self.output_box.tag_configure("search_match", background=t["tag_search"], foreground=t["accent_fg"])
        self.output_box.tag_configure("placeholder", foreground=t["placeholder"])

        self.input_box.tag_configure("input_known", foreground=t["tag_input_hi"])
        self.input_box.tag_configure("placeholder", foreground=t["placeholder"])

        self._refresh_mode_tabs()

    def _theme_scrollbar(self, sb, t):
        """Color a tk.Scrollbar to match the theme (best effort on Windows)."""
        try:
            sb.configure(
                bg=t["muted_bg"],
                troughcolor=t["bg"],
                activebackground=t["accent"],
                highlightthickness=0,
                borderwidth=0,
                elementborderwidth=0,
            )
        except tk.TclError:
            pass

    def _apply_ttk_theme(self, t):
        """Style the ttk widgets (Combobox, PanedWindow) to match the theme."""
        style = self._ttk_style
        # Combobox — needs field, text, arrow, dropdown-list
        style.configure("TCombobox",
            fieldbackground=t["surface"],
            background=t["muted_bg"],
            foreground=t["fg"],
            arrowcolor=t["fg"],
            bordercolor=t["muted_bg"],
            lightcolor=t["muted_bg"],
            darkcolor=t["muted_bg"],
            selectbackground=t["accent"],
            selectforeground=t["accent_fg"],
        )
        style.map("TCombobox",
            fieldbackground=[("readonly", t["surface"])],
            foreground=[("readonly", t["fg"])],
            selectbackground=[("readonly", t["surface"])],
            selectforeground=[("readonly", t["fg"])],
        )
        # Dropdown list (a separate top-level widget in Tk)
        self.option_add("*TCombobox*Listbox.background",       t["surface"])
        self.option_add("*TCombobox*Listbox.foreground",       t["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", t["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", t["accent_fg"])

        # PanedWindow sash
        style.configure("TPanedwindow", background=t["bg"])
        style.configure("Sash", background=t["muted_bg"], sashthickness=6)

        # Treeview (for user-map dialog)
        style.configure("Treeview",
            background=t["surface"], fieldbackground=t["surface"],
            foreground=t["fg"], bordercolor=t["muted_bg"],
            lightcolor=t["muted_bg"], darkcolor=t["muted_bg"],
            borderwidth=0, rowheight=24,
        )
        style.configure("Treeview.Heading",
            background=t["muted_bg"], foreground=t["fg"],
            relief="flat", borderwidth=0,
        )
        style.map("Treeview",
            background=[("selected", t["accent"])],
            foreground=[("selected", t["accent_fg"])],
        )
        style.map("Treeview.Heading",
            background=[("active", t["muted_bg"])],
        )

        # Notebook (for user-map dialog tabs)
        style.configure("TNotebook",
            background=t["bg"], borderwidth=0, tabmargins=(0, 4, 0, 0),
        )
        style.configure("TNotebook.Tab",
            background=t["muted_bg"], foreground=t["fg_muted"],
            padding=[14, 5], borderwidth=0,
        )
        style.map("TNotebook.Tab",
            background=[("selected", t["accent"]), ("active", t["muted_bg"])],
            foreground=[("selected", t["accent_fg"]), ("active", t["fg"])],
        )

    def toggle_theme(self):
        self._theme = "light" if self._theme == "dark" else "dark"
        self._apply_theme()
        self._toast.show(f"{self._theme.title()} theme", 1000, "info")

    # ── Line-number sidebar ───────────────────────────────────────────────────
    def toggle_line_numbers(self):
        self._show_line_numbers.set(not self._show_line_numbers.get())
        self._apply_line_numbers()
        self._toast.show(
            "Line numbers " + ("on" if self._show_line_numbers.get() else "off"),
            900, "info",
        )
        self._persist_pref("show_line_numbers", bool(self._show_line_numbers.get()))

    def _on_line_numbers_toggle_from_menu(self):
        # The menu's checkbutton already flipped the BooleanVar; just apply +
        # toast (avoid double-toggling like toggle_line_numbers would).
        self._apply_line_numbers()
        self._toast.show(
            "Line numbers " + ("on" if self._show_line_numbers.get() else "off"),
            900, "info",
        )
        self._persist_pref("show_line_numbers", bool(self._show_line_numbers.get()))

    # ── Word wrap ─────────────────────────────────────────────────────────────
    def _apply_word_wrap(self, show_toast=True):
        use_wrap = bool(self._word_wrap.get())
        wrap_mode = tk.WORD if use_wrap else tk.NONE
        for box, hsb in (
            (self.input_box,  self._input_hscroll),
            (self.output_box, self._output_hscroll),
        ):
            try:
                box.configure(wrap=wrap_mode)
            except Exception:
                pass
            if use_wrap:
                # Hide horizontal scrollbar; keep ScrolledText's built-in
                # vertical scroll untouched.
                try:
                    hsb.pack_forget()
                    box.configure(xscrollcommand="")
                except Exception:
                    pass
            else:
                try:
                    box.configure(xscrollcommand=hsb.set)
                    # The text widget was packed first with fill="both" and
                    # grabbed the whole container — packing the scrollbar
                    # plainly leaves it as a sliver in the corner. `before=box`
                    # inserts the scrollbar earlier in the pack slot list so
                    # it claims the bottom edge first; the text re-flows above.
                    hsb.pack(side="bottom", fill="x", before=box)
                except Exception:
                    pass
        if show_toast:
            self._toast.show(
                "Word wrap " + ("on" if use_wrap else "off"),
                900, "info",
            )
            # Persist immediately so user choice survives crashes / forced
            # quits — `on_close` may be skipped if the process is killed.
            self._persist_pref("word_wrap", use_wrap)

    def _persist_pref(self, key, value):
        """Update a single setting key and write the file right away."""
        try:
            self._settings[key] = value
            save_settings(self._settings)
        except Exception:
            pass

    # ── Auto-paste from clipboard ─────────────────────────────────────────────
    _SQL_KW_RE = re.compile(
        r'\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN|GROUP\s+BY|ORDER\s+BY|VALUES|TRUNCATE)\b',
        re.IGNORECASE,
    )
    _JAVA_SQL_HINT_RE = re.compile(
        r'(StringBuffer|StringBuilder|\.append\s*\(|sb\.append|sql\.append)',
    )

    def _looks_pasteable(self, text):
        """Decide whether clipboard content is interesting enough to auto-paste.
        We only want SQL or Java SQL-builder code — anything else (URLs, short
        words, random copy-pasted text) is ignored to avoid clobbering the
        input box with unrelated content."""
        if not text:
            return False
        s = text.strip()
        if len(s) < 20:
            return False     # too short to be meaningful SQL/Java
        # Skip if it's just a single line of plain text (no SQL keywords, no
        # Java builder hints).
        if not (self._SQL_KW_RE.search(s) or self._JAVA_SQL_HINT_RE.search(s)):
            return False
        return True

    def _on_auto_paste_toggle(self):
        on = bool(self._auto_paste.get())
        self._toast.show("Auto-paste " + ("on" if on else "off"), 900, "info")
        self._persist_pref("auto_paste", on)
        if on:
            self._maybe_auto_paste()
            self._start_auto_paste_poll()
        else:
            self._stop_auto_paste_poll()

    def _start_auto_paste_poll(self):
        """Re-check the clipboard every ~700 ms while auto-paste is on, so we
        catch app-switch round-trips where Tk doesn't emit FocusIn (e.g. macOS
        when the same child widget keeps focus throughout)."""
        if self._auto_paste_poll_id is not None:
            return
        def tick():
            self._auto_paste_poll_id = None
            if bool(self._auto_paste.get()):
                self._maybe_auto_paste()
                self._auto_paste_poll_id = self.after(700, tick)
        self._auto_paste_poll_id = self.after(700, tick)

    def _stop_auto_paste_poll(self):
        if self._auto_paste_poll_id is not None:
            try: self.after_cancel(self._auto_paste_poll_id)
            except Exception: pass
            self._auto_paste_poll_id = None

    def _on_window_focus_in(self, _event):
        # Tk fires FocusIn on whichever child widget actually receives focus,
        # not on the top-level itself, so we don't filter by widget. Repeated
        # firings within the same app-switch are de-duped by _last_clip_seen.
        if not bool(self._auto_paste.get()):
            return
        # Defer slightly so the OS finishes settling clipboard ownership.
        self.after(120, self._maybe_auto_paste)

    def _maybe_auto_paste(self):
        if not bool(self._auto_paste.get()):
            return
        try:
            clip = self.clipboard_get()
        except Exception:
            return
        if not clip or clip == self._last_clip_seen:
            return
        self._last_clip_seen = clip
        if not self._looks_pasteable(clip):
            return
        # Don't overwrite content the user is actively editing — only paste
        # into an empty input or one still showing the placeholder.
        existing = self._current_input().strip()
        if existing:
            return
        self._clear_placeholder()
        self.input_box.delete("1.0", tk.END)
        self.input_box.insert("1.0", clip)
        self._add_history(clip)
        self._schedule_autotranslate(80)
        self._toast.show("Auto-pasted from clipboard", 1100, "success")

    def _apply_line_numbers(self):
        show = bool(self._show_line_numbers.get())
        for canvas, sibling in (
            (self._input_lnums,  self.input_box),
            (self._output_lnums, self.output_box),
        ):
            if show:
                try:
                    canvas.pack(side="left", fill="y", before=sibling)
                except Exception:
                    canvas.pack(side="left", fill="y")
                canvas._schedule()
            else:
                canvas.pack_forget()

    # ── Pane orientation ──────────────────────────────────────────────────────
    def _top_minsize(self):
        return 220 if self._pane_orient == "vertical" else 380

    def _bot_minsize(self):
        return 180 if self._pane_orient == "vertical" else 320

    def _refresh_layout_btn(self):
        # Show the action the user will take (toggle target), not current state
        if self._pane_orient == "vertical":
            label = "⬌  Switch to horizontal layout"
        else:
            label = "⬍  Switch to vertical layout"
        self._settings_menu.entryconfigure(self._SETTINGS_IDX_LAYOUT, label=label)

    def _refresh_theme_menu_item(self):
        if self._theme == "dark":
            label = "☀  Switch to light theme"
        else:
            label = "🌙  Switch to dark theme"
        self._settings_menu.entryconfigure(self._SETTINGS_IDX_THEME, label=label)

    def toggle_pane_orient(self):
        self._pane_orient = "horizontal" if self._pane_orient == "vertical" else "vertical"
        self._rebuild_panes()
        self._refresh_layout_btn()
        self._apply_theme()
        self._toast.show(f"Layout: {self._pane_orient}", 900, "info")

    def _rebuild_panes(self):
        """Recreate the PanedWindow with the new orientation.
        The child panes are parented to `self`, so destroying the old
        PanedWindow leaves them intact; we just re-add them to the new one."""
        # Detach from old paned first so they don't get destroyed with it
        try:
            self._paned.forget(self._top_pane)
            self._paned.forget(self._bot_pane)
        except Exception:
            pass
        self._paned.pack_forget()
        self._paned.destroy()

        self._paned = tk.PanedWindow(
            self, orient=self._pane_orient,
            sashwidth=6, sashrelief="flat", bd=0, showhandle=False,
            opaqueresize=True,
        )
        self._paned.pack(fill="both", expand=True, padx=12, pady=(2, 0))
        self._paned.add(self._top_pane, minsize=self._top_minsize(), stretch="always")
        self._paned.add(self._bot_pane, minsize=self._bot_minsize(), stretch="always")
        # Stacking fix: the new PanedWindow was created last so it sits on top
        # of the (older) child panes. Raise the panes so they render above
        # the PW's background.
        self._top_pane.lift()
        self._bot_pane.lift()

    # ── Mode / direction / schema ─────────────────────────────────────────────
    def _set_mode(self, mode):
        self._mode.set(mode)
        self._refresh_mode_tabs()
        self.on_translate()

    def _set_direction(self, direction):
        self._direction.set(direction)
        self._refresh_mode_tabs()
        self._set_direction_label()
        self.on_translate()
        self._schedule_input_highlight()

    def _set_direction_label(self):
        if self._direction.get() == "forward":
            self._lbl_in.configure(text="Paste content here  (Physical → Logical)")
        else:
            self._lbl_in.configure(text="Paste content here  (Logical → Physical)")

    # ── Doc tabs (multi-input) ────────────────────────────────────────────────
    def _doctab_title(self, text, idx):
        if not text.strip():
            return f"Tab {idx + 1}"
        # Prefer the Javadoc description (first non-tag line inside /** ... */).
        for raw in text.splitlines():
            stripped = raw.strip()
            if stripped.startswith("/**") or stripped.startswith("/*"):
                stripped = stripped.lstrip("/*").strip()
            elif stripped.startswith("*"):
                stripped = stripped.lstrip("*").strip()
            else:
                continue
            if not stripped or stripped.startswith(("@", "/")):
                continue
            return (stripped[:20] + "…") if len(stripped) > 20 else stripped
        # Otherwise fall back to the first non-blank, non-comment line of code.
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith(("/*", "*/", "*", "//", "#", "--")):
                continue
            return (line[:20] + "…") if len(line) > 20 else line
        return f"Tab {idx + 1}"

    def _capture_active_doc(self):
        if not self._doctabs:
            return
        d = self._doctabs[self._active_doc]
        if self._is_placeholder_showing():
            d["input"] = ""
        else:
            d["input"] = self.input_box.get("1.0", "end-1c")
        d["mode"] = self._mode.get()
        d["direction"] = self._direction.get()
        if not d.get("manual_title"):
            d["title"] = self._doctab_title(d["input"], self._active_doc)

    def _load_doc(self, i):
        d = self._doctabs[i]
        self._clear_placeholder()
        self.input_box.delete("1.0", "end")
        if d["input"]:
            self.input_box.insert("1.0", d["input"])
        self._mode.set(d["mode"])
        self._direction.set(d["direction"])
        self._active_doc = i
        self._show_placeholder_if_empty()
        self._refresh_mode_tabs()
        self._set_direction_label()
        self._render_doctabs()
        self.on_translate()
        self._schedule_input_highlight()

    def _switch_doc_tab(self, i):
        if i == self._active_doc or i < 0 or i >= len(self._doctabs):
            return
        self._capture_active_doc()
        self._load_doc(i)

    def _new_doc_tab(self):
        self._capture_active_doc()
        self._doctabs.append({
            "title": f"Tab {len(self._doctabs) + 1}", "input": "",
            "mode": self._mode.get(),
            "direction": self._direction.get(),
        })
        self._load_doc(len(self._doctabs) - 1)

    def _close_doc_tab(self, i):
        if len(self._doctabs) <= 1 or i < 0 or i >= len(self._doctabs):
            return
        was_active = (i == self._active_doc)
        del self._doctabs[i]
        if was_active:
            # Pick neighbor (prefer same index, else the one before)
            new_i = min(i, len(self._doctabs) - 1)
            self._active_doc = new_i
            self._load_doc(new_i)
        else:
            if i < self._active_doc:
                self._active_doc -= 1
            self._render_doctabs()

    def _cycle_doc_tab(self, delta):
        if len(self._doctabs) <= 1:
            return
        new = (self._active_doc + delta) % len(self._doctabs)
        self._switch_doc_tab(new)

    def _render_doctabs(self):
        if not hasattr(self, "_doctabs_inner"):
            return
        for w in self._doctabs_inner.winfo_children():
            w.destroy()
        t = THEMES[self._theme]
        for i, d in enumerate(self._doctabs):
            active = (i == self._active_doc)
            bg = t["accent"] if active else t["surface"]
            fg = t["accent_fg"] if active else t["fg"]
            frame = tk.Frame(self._doctabs_inner, bg=bg)
            frame.pack(side="left", padx=(0, 4))
            label = d.get("title") or f"Tab {i + 1}"
            btn = tk.Button(
                frame, text=label, font=self._ui_b,
                relief="flat", padx=10, pady=2, cursor="hand2", bd=0,
                bg=bg, fg=fg,
                activebackground=t["accent"] if active else t["muted_bg"],
                activeforeground=t["accent_fg"] if active else t["fg"],
                command=lambda i=i: self._switch_doc_tab(i),
            )
            btn.pack(side="left")
            btn.bind("<Double-Button-1>",
                lambda e, i=i, frame=frame, btn=btn: self._begin_rename_doc_tab(i, frame, btn))
            # Right-click (Button-3) and macOS two-finger click (Button-2) open context menu
            btn.bind("<Button-2>",
                lambda e, i=i, frame=frame, btn=btn: self._show_doctab_menu(e, i, frame, btn))
            btn.bind("<Button-3>",
                lambda e, i=i, frame=frame, btn=btn: self._show_doctab_menu(e, i, frame, btn))
            if len(self._doctabs) > 1:
                close = tk.Button(
                    frame, text="×", font=self._ui_b,
                    relief="flat", padx=6, pady=0, cursor="hand2", bd=0,
                    bg=bg, fg=fg,
                    activebackground=t["danger"], activeforeground=t["accent_fg"],
                    command=lambda i=i: self._close_doc_tab(i),
                )
                close.pack(side="left")

    def _show_doctab_menu(self, event, i, frame, btn):
        # Switch to the tab first so all "current"-scoped shortcuts line up
        if i != self._active_doc:
            self._switch_doc_tab(i)
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Rename",
            command=lambda: self._begin_rename_doc_tab(i, frame, btn))
        m.add_command(label="Duplicate", command=lambda: self._duplicate_doc_tab(i))
        m.add_separator()
        multi = len(self._doctabs) > 1
        m.add_command(label="Close", state=("normal" if multi else "disabled"),
            command=lambda: self._close_doc_tab(i))
        m.add_command(label="Close Others",
            state=("normal" if multi else "disabled"),
            command=lambda: self._close_other_doc_tabs(i))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _duplicate_doc_tab(self, i):
        self._capture_active_doc()
        src = self._doctabs[i]
        copy = {
            "title":        (src.get("title") or f"Tab {i + 1}") + " (copy)",
            "input":        src.get("input", ""),
            "mode":         src.get("mode", self._mode.get()),
            "direction":    src.get("direction", self._direction.get()),
            "manual_title": True,
        }
        self._doctabs.insert(i + 1, copy)
        self._load_doc(i + 1)

    def _close_other_doc_tabs(self, i):
        if not (0 <= i < len(self._doctabs)):
            return
        self._capture_active_doc()
        keep = self._doctabs[i]
        self._doctabs = [keep]
        self._active_doc = 0
        self._load_doc(0)

    def _begin_rename_doc_tab(self, i, frame, btn):
        t = THEMES[self._theme]
        current = self._doctabs[i].get("title", f"Tab {i + 1}")
        # Destroy the whole tab frame's children and rebuild it inline as an Entry.
        for w in frame.winfo_children():
            w.destroy()
        var = tk.StringVar(value=current)
        entry = tk.Entry(
            frame, textvariable=var, font=self._ui_b,
            relief="flat", bd=0, bg=t["surface"], fg=t["fg"],
            insertbackground=t["insert"], width=max(8, len(current) + 2),
        )
        entry.pack(side="left", padx=4)
        entry.focus_set()
        entry.select_range(0, "end")

        def commit(event=None):
            new = var.get().strip()
            if new:
                self._doctabs[i]["title"] = new[:40]
                self._doctabs[i]["manual_title"] = True
            self._render_doctabs()

        def cancel(event=None):
            self._render_doctabs()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", cancel)

    def toggle_mode(self):
        self._set_mode("table" if self._mode.get() == "inline" else "inline")

    def toggle_direction(self):
        self._set_direction("reverse" if self._direction.get() == "forward" else "forward")

    def _refresh_mode_tabs(self):
        t = THEMES[self._theme]
        def style(btn, active):
            if active:
                btn.configure(bg=t["accent"], fg=t["accent_fg"],
                    activebackground=t["accent"], activeforeground=t["accent_fg"])
            else:
                btn.configure(bg=t["muted_bg"], fg=t["fg_muted"],
                    activebackground=t["muted_bg"], activeforeground=t["fg_muted"])
        mode = self._mode.get()
        style(self._tab_table,     mode == "table")
        style(self._tab_inline,    mode == "inline")
        style(self._tab_designdoc, mode == "designdoc")
        style(self._tab_forward, self._direction.get() == "forward")
        style(self._tab_reverse, self._direction.get() == "reverse")
        # Toggle Design-Doc-specific controls
        try:
            if mode == "designdoc":
                self._upper_chk.pack(side="left", padx=(10, 0))
                self._sections_mb.pack(side="left", padx=(6, 0))
            else:
                self._upper_chk.pack_forget()
                self._sections_mb.pack_forget()
        except AttributeError:
            pass


    # ── Font zoom ─────────────────────────────────────────────────────────────
    def zoom_in(self):   self._set_font_size(self._font_size + 1)
    def zoom_out(self):  self._set_font_size(max(7, self._font_size - 1))
    def zoom_reset(self): self._set_font_size(10)

    def _set_font_size(self, size):
        self._font_size = max(6, min(size, 28))
        self._mono.configure(size=self._font_size)
        self._apply_theme()  # rebuild bold_mono with new size
        try:
            self._input_lnums._schedule()
            self._output_lnums._schedule()
        except Exception:
            pass
        self._toast.show(f"Font size: {self._font_size}", 900, "info")

    # ── Translate ─────────────────────────────────────────────────────────────
    def _on_ctrl_enter(self, event=None):
        self.on_translate()
        # Explicit translate → save current input to history
        self._add_history(self._current_input())
        return "break"

    def _on_paste(self, event=None):
        # Save current input (before paste) into history, then schedule retranslate
        # after paste has actually modified the buffer.
        self.after(20, lambda: self._add_history(self._current_input()))
        self._schedule_autotranslate(80)

    def _schedule_autotranslate(self, delay_ms=300):
        if self._autotr_job:
            try: self.after_cancel(self._autotr_job)
            except Exception: pass
        self._autotr_job = self.after(delay_ms, self.on_translate)

    def on_translate(self):
        text = self._current_input()
        mode = self._mode.get()
        direction = self._direction.get()
        schemas = self._filter_schemas or None
        tables  = self._filter_tables  or None

        self._spans = []
        unknown = []

        # Skip translation entirely when input is empty / only placeholder
        if not text.strip():
            self._write_output("")
            self._status_var.set("")
            self._sb_match.configure(text="")
            self._table_context = set()
            return

        # Compute which tables are mentioned in the input — used to
        # prioritize column entries whose table is in the pasted text.
        if direction == "forward":
            self._table_context = {t for t in _tokens(text) if t in self.table_index}
        else:
            self._table_context = {n for n in self.rev_table_index if n and n in text}

        # ── Design Doc mode has its own pipeline ──
        if mode == "designdoc":
            uppercase = bool(self._uppercase_var.get())
            result = java_to_design_doc(
                text,
                self.table_index, self.column_index,
                self.rev_table_index, self.rev_column_index,
                schemas=schemas, tables=tables,
                uppercase=uppercase, direction=direction,
                show_overview    = bool(self._show_overview.get()),
                show_sql_logical = bool(self._show_sql_logical.get()),
                show_sql_physical= bool(self._show_sql_physical.get()),
                show_stype       = bool(self._show_stype.get()),
                show_target      = bool(self._show_target.get()),
                show_projection  = bool(self._show_projection.get()),
                show_from        = bool(self._show_from.get()),
                show_join        = bool(self._show_join.get()),
                show_where       = bool(self._show_where.get()),
                show_group       = bool(self._show_group.get()),
                show_having      = bool(self._show_having.get()),
                show_order       = bool(self._show_order.get()),
                show_footer      = bool(self._show_footer.get()),
            )
            # Compute hoverable spans over the rendered text so tooltips work
            # in this mode exactly like Inline Replace mode does.
            self._spans = self._compute_design_spans(result, direction)
            self._render_inline(result, unknown=None)
            # Use the same status format as Inline Replace mode.
            n_t = sum(1 for s in self._spans if s[3] == "table")
            n_c = sum(1 for s in self._spans if s[3] == "column")
            n_amb = sum(1 for s in self._spans if s[4])
            extra_txt = f"  ·  Ambiguous: {n_amb}" if n_amb else ""
            self._status_var.set(f"Tables: {n_t}  ·  Columns: {n_c}{extra_txt}")
            self._refresh_output_stats()
            return

        ctx = self._table_context
        if direction == "forward":
            if mode == "table":
                result = translate_table_mode(text, self.table_index, self.column_index,
                                              schemas=schemas, tables=tables, table_context=ctx)
                unknown = find_unknown_tokens(text, self.table_index, self.column_index, self._exclusions)
                self._render_table(result, unknown)
            else:
                translated, rmap, spans = translate_inline_mode(
                    text, self.table_index, self.column_index, self._exclusions,
                    schemas=schemas, tables=tables, table_context=ctx)
                unknown = find_unknown_tokens(text, self.table_index, self.column_index, self._exclusions)
                self._spans = spans
                self._render_inline(translated, unknown)
            tokens = _tokens(text)
            n_t = sum(1 for t in tokens if t in self.table_index)
            n_c = sum(1 for t in tokens if t in self.column_index)
        else:
            if mode == "table":
                result = translate_reverse_table_mode(text, self.rev_table_index, self.rev_column_index,
                                                      schemas=schemas, tables=tables, table_context=ctx)
                self._render_table(result, [])
            else:
                translated, rmap, spans = translate_reverse_inline_mode(
                    text, self.rev_table_index, self.rev_column_index, self._exclusions,
                    schemas=schemas, tables=tables, table_context=ctx)
                self._spans = spans
                self._render_inline(translated, [])
            found = _find_logical_tokens(text, self.rev_table_index, self.rev_column_index)
            n_t = sum(1 for _, is_t in found if is_t)
            n_c = sum(1 for _, is_t in found if not is_t)

        n_amb = sum(1 for s in self._spans if s[4])
        extra = []
        if n_amb:        extra.append(f"Ambiguous: {n_amb}")
        if unknown:      extra.append(f"Unknown: {len(unknown)}")
        extra_txt = "  ·  " + "  ·  ".join(extra) if extra else ""

        self._status_var.set(f"Tables: {n_t}  ·  Columns: {n_c}{extra_txt}")
        self._refresh_output_stats()

    # ── Clear / copy / history ────────────────────────────────────────────────
    def on_clear(self):
        self.input_box.delete("1.0", tk.END)
        self._write_output("")
        self._status_var.set("")
        self._sb_match.configure(text="")
        # If the input still has focus (typical after Ctrl+⌫), don't insert the
        # placeholder — otherwise the caret ends up sitting inside placeholder
        # text and the user can edit it as if it were real input.
        try:
            has_focus = self.focus_get() is self.input_box
        except Exception:
            has_focus = False
        if not has_focus:
            self._show_placeholder_if_empty()

    def on_copy(self):
        content = self._get_output_without_unknown()
        if not content.strip():
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self._toast.show("✔  Copied to clipboard", 1400, "success")
        if self._copy_job:
            try: self.after_cancel(self._copy_job)
            except Exception: pass
        self._copy_btn.configure(text="✔  Copied!")
        self._copy_job = self.after(1600, lambda: self._copy_btn.configure(text="⎘  Copy"))

    def _add_history(self, text):
        text = text.strip()
        if not text:
            return
        if self._history and self._history[-1] == text:
            return
        self._history = [h for h in self._history if h != text]
        self._history.append(text)
        self._history = self._history[-MAX_HISTORY:]
        save_history(self._history)
        self._refresh_history_menu()

    def _refresh_history_menu(self):
        self._history_menu.delete(0, tk.END)
        if not self._history:
            self._history_menu.add_command(label="(empty)", state="disabled")
            return
        for item in reversed(self._history):
            preview = item.replace("\n", " ⏎ ")
            if len(preview) > 60:
                preview = preview[:57] + "…"
            self._history_menu.add_command(
                label=preview, command=lambda v=item: self._load_from_history(v))
        self._history_menu.add_separator()
        self._history_menu.add_command(label="Clear history", command=self._clear_history)

    def _load_from_history(self, text):
        self._clear_placeholder()
        self.input_box.delete("1.0", tk.END)
        self.input_box.insert("1.0", text)
        self.on_translate()

    def _clear_history(self):
        self._history = []
        save_history(self._history)
        self._refresh_history_menu()

    # ── Open / reload / export ────────────────────────────────────────────────
    def on_open_file(self):
        path = filedialog.askopenfilename(
            title="Open file",
            filetypes=[("Text / SQL", "*.txt *.sql *.md"), ("All files", "*.*")],
        )
        if path:
            self._load_file_into_input(path)

    def _load_file_into_input(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="cp932") as f:
                content = f.read()
        except Exception as e:
            self._toast.show(f"Open failed: {e}", 2200, "error")
            return
        self._clear_placeholder()
        self.input_box.delete("1.0", tk.END)
        self.input_box.insert("1.0", content)
        self.on_translate()
        self._add_history(content)
        self._toast.show(f"Loaded {os.path.basename(path)}", 1500, "success")

    def on_reload_json(self):
        try:
            self._load_data()
            # Drop any filter entries that no longer exist
            self._filter_schemas &= set(self.schemas)
            self._filter_tables  &= set(self.table_index.keys())
            self._refresh_filter_btn()
            self._refresh_umap_btn()
            self._refresh_index_stats()
            self.on_translate()
            self._toast.show("✔  Reloaded JSON + user map", 1200, "success")
        except Exception as e:
            self._toast.show(f"Reload failed: {e}", 2500, "error")

    def on_export(self):
        content = self._get_output_without_unknown()
        if not content.strip():
            self._toast.show("Nothing to save", 1200, "error")
            return
        path = filedialog.asksaveasfilename(
            title="Save translation",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("Markdown", "*.md"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._toast.show(f"✔  Saved {os.path.basename(path)}", 1600, "success")
        except Exception as e:
            self._toast.show(f"Save failed: {e}", 2500, "error")

    def _on_file_drop(self, event):
        # event.data has space-separated paths wrapped in braces if they have spaces
        raw = event.data.strip()
        # tkinterdnd2 wraps paths with spaces in {braces}; parse them
        paths = re.findall(r"\{([^}]+)\}|(\S+)", raw)
        paths = [a or b for a, b in paths]
        if paths:
            self._load_file_into_input(paths[0])

    # ── Input change handler (autotranslate + highlight) ──────────────────────
    def _on_input_change(self, event=None):
        self._schedule_autotranslate(350)
        self._schedule_input_highlight()
        self._schedule_doc_save()

    def _schedule_doc_save(self, delay_ms=2500):
        """Persist the active doc-tab's input shortly after the user stops
        typing. Guarantees the input survives even when the app exits via
        Cmd+Q / kill / crash without firing on_close."""
        if self._docs_save_job:
            try: self.after_cancel(self._docs_save_job)
            except Exception: pass
        self._docs_save_job = self.after(delay_ms, self._persist_doc_tabs)

    def _persist_doc_tabs(self):
        self._docs_save_job = None
        try:
            self._capture_active_doc()
            self._settings["doc_tabs"] = [
                {
                    "title":        d.get("title", ""),
                    "input":        d.get("input", ""),
                    "mode":         d.get("mode", "inline"),
                    "direction":    d.get("direction", "forward"),
                    "manual_title": bool(d.get("manual_title", False)),
                }
                for d in self._doctabs
            ]
            self._settings["active_doc"] = self._active_doc
            save_settings(self._settings)
        except Exception:
            pass

    def _schedule_input_highlight(self, delay_ms=300):
        if self._input_hi_job:
            try: self.after_cancel(self._input_hi_job)
            except Exception: pass
        self._input_hi_job = self.after(delay_ms, self._highlight_input_tokens)

    def _highlight_input_tokens(self):
        self.input_box.tag_remove("input_known", "1.0", tk.END)
        text = self._current_input()
        if not text:
            return
        direction = self._direction.get()
        if direction == "forward":
            tokens = {t for t in _tokens(text)
                      if t in self.table_index or t in self.column_index}
            if not tokens:
                return
            pattern = re.compile(r"\b(" + "|".join(re.escape(t) for t in tokens) + r")\b")
        else:
            cands = set(self.rev_table_index.keys()) | set(self.rev_column_index.keys())
            found = {c for c in cands if c and c in text}
            if not found:
                return
            pattern = re.compile("|".join(re.escape(c) for c in sorted(found, key=len, reverse=True)))

        for m in pattern.finditer(text):
            start = f"1.0+{m.start()}c"
            end   = f"1.0+{m.end()}c"
            self.input_box.tag_add("input_known", start, end)

    # ── Placeholder ───────────────────────────────────────────────────────────
    _PLACEHOLDER_TEXT = (
        "   Paste SQL, design docs, or any text containing physical (or logical) names.\n"
        "   Ctrl+Enter translates immediately; right-click a word to manage exclusions.\n"
        "   Drag a .sql / .txt / .md file here to load it, or press F1 for shortcuts."
    )

    def _current_input(self):
        """Return input text minus placeholder."""
        text = self.input_box.get("1.0", tk.END)
        if self._is_placeholder_showing():
            return ""
        return text

    def _is_placeholder_showing(self):
        return "placeholder" in self.input_box.tag_names("1.0")

    def _show_placeholder_if_empty(self):
        content = self.input_box.get("1.0", tk.END).strip()
        if not content:
            self.input_box.delete("1.0", tk.END)
            self.input_box.insert("1.0", self._PLACEHOLDER_TEXT, "placeholder")

    def _clear_placeholder(self):
        if self._is_placeholder_showing():
            self.input_box.delete("1.0", tk.END)

    def _guard_placeholder_keypress(self, event):
        """Wipe the placeholder before the first real keystroke modifies it.
        Lets navigation / modifier keys through unchanged."""
        if not self._is_placeholder_showing():
            return None
        # Allow pure modifier / navigation keys without nuking the placeholder
        navigation = {
            "Shift_L", "Shift_R", "Control_L", "Control_R",
            "Alt_L", "Alt_R", "Meta_L", "Meta_R", "Super_L", "Super_R",
            "Caps_Lock", "Num_Lock",
            "Left", "Right", "Up", "Down", "Home", "End",
            "Page_Up", "Page_Down", "Tab", "Escape",
        }
        if event.keysym in navigation:
            return None
        # First real keystroke — clear placeholder, place caret at start, and
        # let the keystroke proceed normally.
        self._clear_placeholder()
        self.input_box.mark_set(tk.INSERT, "1.0")

    def _guard_placeholder_click(self, _event):
        if self._is_placeholder_showing():
            self._clear_placeholder()
            self.input_box.mark_set(tk.INSERT, "1.0")

    # ── Rendering ─────────────────────────────────────────────────────────────
    def _write_output(self, text=""):
        self.output_box.configure(state="normal")
        self.output_box.delete("1.0", tk.END)
        if text:
            self.output_box.insert(tk.END, text)
        self.output_box.configure(state="disabled")
        self._refresh_output_stats(text or "")

    def _refresh_output_stats(self, text=None):
        """Show line / character counts in the bottom status bar.
        Counts are computed from the actual output buffer when text is None."""
        if text is None:
            try:
                text = self.output_box.get("1.0", tk.END).rstrip("\n")
            except Exception:
                text = ""
        if not text:
            self._sb_match.configure(text="")
            return
        lines = text.count("\n") + (0 if text.endswith("\n") else 1)
        chars = len(text)
        if chars >= 1000:
            ch_str = f"{chars/1000:.1f}k chars"
        else:
            ch_str = f"{chars} chars"
        self._sb_match.configure(text=f"{lines} lines · {ch_str}  ")

    def _get_output_without_unknown(self):
        """Return the output contents minus the 未定義 section."""
        ranges = self.output_box.tag_ranges("unknown_section")
        if ranges:
            return self.output_box.get("1.0", ranges[0]).rstrip("\n")
        return self.output_box.get("1.0", tk.END).rstrip("\n")

    def _render_table(self, text, unknown=None):
        self.output_box.configure(state="normal")
        self.output_box.delete("1.0", tk.END)
        for line in text.splitlines(keepends=True):
            if line.startswith("━"):
                self.output_box.insert(tk.END, line, "header")
            elif line.startswith("  ") and "→" not in line and line.strip():
                self.output_box.insert(tk.END, line, "physical")
            elif "→" in line:
                idx = line.index("→")
                self.output_box.insert(tk.END, line[:idx + 1], "logical")
                rest = line[idx + 1:]
                b = rest.find("[")
                if b != -1:
                    self.output_box.insert(tk.END, rest[:b], "logical")
                    self.output_box.insert(tk.END, rest[b:], "meta")
                else:
                    self.output_box.insert(tk.END, rest, "logical")
            else:
                self.output_box.insert(tk.END, line)

        self._append_unknown_section(unknown)
        self.output_box.configure(state="disabled")

    def _render_inline(self, translated_text, unknown=None):
        self.output_box.configure(state="normal")
        self.output_box.delete("1.0", tk.END)

        # Clear previous span tags
        for tag in list(self.output_box.tag_names()):
            if tag.startswith("span_"):
                self.output_box.tag_delete(tag)

        spans = self._spans
        if spans:
            pos = 0
            for i, (s, e, _orig, kind, is_amb) in enumerate(spans):
                if s > pos:
                    self.output_box.insert(tk.END, translated_text[pos:s])
                base_tag = "inline_ambig" if is_amb else ("inline_table" if kind == "table" else "inline_column")
                span_tag = f"span_{i}"
                self.output_box.insert(tk.END, translated_text[s:e], (base_tag, span_tag))
                self.output_box.tag_bind(span_tag, "<Enter>",
                    lambda ev, idx=i: self._show_span_tooltip(idx, ev))
                pos = e
            if pos < len(translated_text):
                self.output_box.insert(tk.END, translated_text[pos:])
        else:
            self.output_box.insert(tk.END, translated_text)

        self._append_unknown_section(unknown)
        self.output_box.configure(state="disabled")

    def _append_unknown_section(self, unknown):
        if not unknown:
            return
        current = self.output_box.get("1.0", tk.END)
        if not current.endswith("\n\n") and current.strip():
            self.output_box.insert(tk.END, "\n\n", "unknown_section")
        self.output_box.insert(tk.END, "━━━  未定義 (Not in JSON)  ━━━\n",
                               ("header", "unknown_section"))
        for token in unknown:
            self.output_box.insert(tk.END, f"  • {token}\n",
                                   ("unknown", "unknown_section"))

    # ── Hover tooltip ─────────────────────────────────────────────────────────
    def _show_span_tooltip(self, span_idx, event):
        if span_idx >= len(self._spans):
            return
        _, _, original, kind, is_amb = self._spans[span_idx]
        direction = self._direction.get()
        ctx = self._table_context

        # Build the tooltip based on whichever index holds the name.
        # Direction-specific lookup is tried first; falls back to the other
        # side so Design-Doc spans (which may carry either physical or logical
        # names) always produce useful context.
        # Group helpers — collapse a long entry list into a single summary
        # line per distinct translation. Avoids 50-row tooltips when the
        # column appears in many tables but always maps to the same name.
        def _group_table_entries(entries):
            """Group [(schema, name)] by name → {name: [schemas]}."""
            groups = {}
            for sc, name in entries:
                groups.setdefault(name, []).append(sc)
            return groups

        def _group_col_entries(entries):
            """Group [(schema, phys_table, logical_table, target)] by target.
            Returns {target: [(schema, phys_table, logical_table)]}."""
            groups = {}
            for sc, pt, lt, target in entries:
                groups.setdefault(target, []).append((sc, pt, lt))
            return groups

        def _format_table_groups(groups):
            lines = []
            for name, schemas in sorted(groups.items(), key=lambda g: -len(g[1])):
                uniq = sorted(set(schemas))
                if len(schemas) == 1:
                    lines.append(f"  → {name}  [{schemas[0]}]")
                else:
                    schema_str = ", ".join(uniq[:3]) + (f" +{len(uniq)-3}" if len(uniq) > 3 else "")
                    lines.append(f"  → {name}  ({len(schemas)} schemas: {schema_str})")
            return lines

        def _format_col_groups(groups, max_tables_inline=3):
            lines = []
            for target, rows in sorted(groups.items(), key=lambda g: -len(g[1])):
                if len(rows) == 1:
                    sc, pt, lt = rows[0]
                    lines.append(f"  → {target}  [{lt} ({pt}) / {sc}]")
                    continue
                # Many tables share this same translation — show a compact
                # "X tables in <schemas>: a, b, c, ..." summary.
                schemas = sorted({sc for sc, _pt, _lt in rows})
                schema_part = ", ".join(schemas[:2]) + (f" +{len(schemas)-2}" if len(schemas) > 2 else "")
                sample = ", ".join(
                    f"{lt}({pt})" if lt and lt != pt else pt
                    for _sc, pt, lt in rows[:max_tables_inline]
                )
                more = f"  +{len(rows)-max_tables_inline} more" if len(rows) > max_tables_inline else ""
                lines.append(
                    f"  → {target}  ({len(rows)} tables in {schema_part})\n"
                    f"      {sample}{more}"
                )
            return lines

        def _from_fwd_table(original):
            return (f"Table: {original}",
                    _format_table_groups(_group_table_entries(self.table_index[original])))

        def _from_fwd_col(original):
            entries = _filter_by_table_context(self.column_index[original], ctx)
            return (f"Column: {original}",
                    _format_col_groups(_group_col_entries(entries)))

        def _from_rev_table(original):
            return (f"Table: {original}",
                    _format_table_groups(_group_table_entries(self.rev_table_index[original])))

        def _from_rev_col(original):
            entries = _filter_by_table_context(self.rev_column_index[original], ctx)
            return (f"Column: {original}",
                    _format_col_groups(_group_col_entries(entries)))

        header, body = None, None
        preferred = (
            [_from_fwd_table, _from_fwd_col, _from_rev_table, _from_rev_col]
            if direction == "forward"
            else [_from_rev_table, _from_rev_col, _from_fwd_table, _from_fwd_col]
        )
        fwd_tests = [
            (_from_fwd_table, lambda o: o in self.table_index),
            (_from_fwd_col,   lambda o: o in self.column_index),
            (_from_rev_table, lambda o: o in self.rev_table_index),
            (_from_rev_col,   lambda o: o in self.rev_column_index),
        ]
        order = preferred
        tests = {fn: pred for fn, pred in fwd_tests}
        for fn in order:
            if tests[fn](original):
                header, body = fn(original)
                break
        if header is None:
            return

        prefix = "⚠ Ambiguous\n" if is_amb else ""
        self._tooltip.show(prefix + header + "\n" + "\n".join(body),
                           event.x_root, event.y_root)

    def _on_output_motion(self, event):
        idx = self.output_box.index(f"@{event.x},{event.y}")
        tags = self.output_box.tag_names(idx)
        if not any(tg.startswith("span_") for tg in tags):
            self._tooltip.hide()

    def _compute_design_spans(self, text, direction):
        """Scan the rendered design-doc text and return spans for every known
        physical or logical name occurrence so hover tooltips work in this mode.
        Skips any match that overlaps an entry in the exclusion list."""
        spans = []
        # Exclusion ranges computed over the OUTPUT text so e.g. '■処理区分'
        # stays silent on hover while '処理区分' elsewhere still works.
        excl_ranges = _exclusion_ranges(text, self._exclusions)
        # Collect candidate names present anywhere in the text
        # Forward mode emits logical (Japanese) names; reverse keeps physical.
        # Accept both just in case the user mixed modes.
        logical_cands  = [n for n in (set(self.rev_table_index) | set(self.rev_column_index))
                          if n and n in text]
        physical_cands = [n for n in (set(self.table_index) | set(self.column_index))
                          if n and n in text]

        # Sort longer-first so e.g. '商品マスタ' wins over '商品'
        logical_cands.sort(key=len, reverse=True)
        physical_cands.sort(key=len, reverse=True)

        patterns = []
        if logical_cands:
            patterns.append(("logical", re.compile(
                "(" + "|".join(re.escape(c) for c in logical_cands) + ")")))
        if physical_cands:
            patterns.append(("physical", re.compile(
                r"\b(" + "|".join(re.escape(c) for c in physical_cands) + r")\b")))

        if not patterns:
            return spans

        # Collect matches from each pattern, sort by position, de-overlap
        raw = []
        for kind_tag, pat in patterns:
            for m in pat.finditer(text):
                name = m.group(0)
                raw.append((m.start(), m.end(), name, kind_tag))
        # Sort by (start, -end): longer match at same position wins
        raw.sort(key=lambda x: (x[0], -x[1]))

        last_end = -1
        for s, e, name, kind_tag in raw:
            if s < last_end:
                continue
            if _overlaps_any(s, e, excl_ranges):
                continue
            if kind_tag == "logical":
                if name in self.rev_table_index:
                    kind = "table"
                    entries = self.rev_table_index[name]
                else:
                    kind = "column"
                    entries = self.rev_column_index[name]
            else:  # physical
                if name in self.table_index:
                    kind = "table"
                    entries = self.table_index[name]
                else:
                    kind = "column"
                    entries = self.column_index[name]
            is_amb = _is_ambiguous(name, entries)
            spans.append((s, e, name, kind, is_amb))
            last_end = e

        spans.sort()
        return spans

    # ── Exclusions: right-click ───────────────────────────────────────────────
    def _on_right_click(self, event, widget):
        try:
            selected = widget.selection_get()
        except tk.TclError:
            return
        selected = selected.strip("\r\n")
        if not selected.strip():
            return

        t = THEMES[self._theme]
        menu = tk.Menu(self, tearoff=0,
            bg=t["surface"], fg=t["fg"],
            activebackground=t["accent"], activeforeground=t["accent_fg"],
            bd=0, relief="flat")
        preview = selected if len(selected) <= 40 else selected[:37] + "…"
        preview = preview.replace("\n", "⏎")

        if selected in self._exclusions:
            menu.add_command(label=f"✕  Remove from exclusions:  «{preview}»",
                command=lambda: self._remove_exclusion(selected))
        else:
            menu.add_command(label=f"⊘  Add to exclusions:  «{preview}»",
                command=lambda: self._add_exclusion(selected))
        menu.add_separator()
        menu.add_command(label="Open exclusion list…", command=self.open_exclusions_dialog)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _add_exclusion(self, text):
        text = text.strip("\r\n")
        if text and text not in self._exclusions:
            self._exclusions.append(text)
            save_exclusions(self._exclusions)
            self._refresh_excl_btn()
            self.on_translate()
            self._toast.show("Added to exclusions", 1200, "success")

    def _remove_exclusion(self, text):
        text = text.strip("\r\n")
        if text in self._exclusions:
            self._exclusions.remove(text)
            save_exclusions(self._exclusions)
            self._refresh_excl_btn()
            self.on_translate()
            self._toast.show("Removed from exclusions", 1200, "success")

    def _refresh_excl_btn(self):
        n = len(self._exclusions)
        label = f"⊘  Exclusions… ({n})" if n else "⊘  Exclusions…"
        self._settings_menu.entryconfigure(self._SETTINGS_IDX_EXCL, label=label)

    # ── User map button + dialog ──────────────────────────────────────────────
    def _refresh_umap_btn(self):
        n = len((self._user_map.get("tables") or {})) + len((self._user_map.get("columns") or {}))
        label = f"🖉  User Map… ({n})" if n else "🖉  User Map…"
        self._settings_menu.entryconfigure(self._SETTINGS_IDX_UMAP, label=label)

    def open_user_map_dialog(self):
        from .dialogs.user_map import open_user_map_dialog
        open_user_map_dialog(self)
    def _sort_tree(self, tree, col):
        """Toggle ascending/descending sort on a treeview column."""
        items = [(tree.set(k, col), k) for k in tree.get_children()]
        # Cache previous direction on the widget
        reverse = not getattr(tree, f"_sort_{col}", False)
        setattr(tree, f"_sort_{col}", reverse)
        items.sort(reverse=reverse)
        for idx, (_val, k) in enumerate(items):
            tree.move(k, "", idx)

    # ── Filter button + dialog ────────────────────────────────────────────────
    def _refresh_filter_btn(self):
        ns = len(self._filter_schemas)
        nt = len(self._filter_tables)
        parts = []
        if ns: parts.append(f"{ns}S")
        if nt: parts.append(f"{nt}T")
        label = "⚙  Filter…"
        if parts:
            label += f" ({' / '.join(parts)})"
        self._settings_menu.entryconfigure(self._SETTINGS_IDX_FILTER, label=label)

    def open_filter_dialog(self):
        from .dialogs.filter import open_filter_dialog
        open_filter_dialog(self)
    # ── Inconsistency detector ────────────────────────────────────────────────
    def open_inconsistency_dialog(self):
        from .dialogs.inconsistency import open_inconsistency_dialog
        open_inconsistency_dialog(self)
    # ── Sections popup (stays open across multiple toggles) ───────────────────
    def toggle_sections_popup(self):
        from .dialogs.sections import toggle_sections_popup
        toggle_sections_popup(self)
    # ── Exclusions dialog ─────────────────────────────────────────────────────
    def open_exclusions_dialog(self):
        from .dialogs.exclusions import open_exclusions_dialog
        open_exclusions_dialog(self)
    # ── Search bar ────────────────────────────────────────────────────────────
    def open_search_bar(self):
        if not self._search_frame.winfo_ismapped():
            self._search_frame.pack(before=self.output_box, fill="x", pady=(2, 2))
        self._search_entry.focus_set()
        self._search_entry.select_range(0, tk.END)
        self._search_highlight_all()

    def close_search_bar(self):
        if self._search_frame.winfo_ismapped():
            self._search_frame.pack_forget()
        self.output_box.tag_remove("search_match", "1.0", tk.END)
        self._search_count_var.set("")

    def _search_highlight_all(self):
        self.output_box.tag_remove("search_match", "1.0", tk.END)
        query = self._search_var.get()
        if not query:
            self._search_count_var.set("")
            return
        count = 0
        pos = "1.0"
        while True:
            idx = self.output_box.search(query, pos, stopindex=tk.END, nocase=True)
            if not idx:
                break
            end = f"{idx}+{len(query)}c"
            self.output_box.tag_add("search_match", idx, end)
            count += 1
            pos = end
        self._search_count_var.set(f"{count} matches" if count else "no matches")

    def _search_next(self, reverse=False):
        query = self._search_var.get()
        if not query:
            return
        cur = self.output_box.index(tk.INSERT)
        if reverse:
            idx = self.output_box.search(query, cur, backwards=True, nocase=True, stopindex="1.0")
            if not idx:
                idx = self.output_box.search(query, tk.END, backwards=True, nocase=True, stopindex="1.0")
        else:
            idx = self.output_box.search(query, cur, nocase=True, stopindex=tk.END)
            if not idx:
                idx = self.output_box.search(query, "1.0", nocase=True, stopindex=tk.END)
        if idx:
            end = f"{idx}+{len(query)}c"
            self.output_box.mark_set(tk.INSERT, end)
            self.output_box.see(idx)

    def _search_prev(self): self._search_next(reverse=True)

    # ── Help dialog ───────────────────────────────────────────────────────────
    def show_help_dialog(self):
        from .dialogs.help import show_help_dialog
        show_help_dialog(self)

    # ── Hover tooltips on buttons (delayed show, hide on leave) ───────────────
    def _attach_tooltip(self, widget, text, delay_ms=500):
        state = {"job": None}
        def _enter(_e):
            def _show():
                state["job"] = None
                try:
                    x = widget.winfo_rootx() + 8
                    y = widget.winfo_rooty() + widget.winfo_height() + 4
                    self._tooltip.show(text, x, y)
                except Exception:
                    pass
            state["job"] = widget.after(delay_ms, _show)
        def _leave(_e):
            if state["job"]:
                try: widget.after_cancel(state["job"])
                except Exception: pass
                state["job"] = None
            self._tooltip.hide()
        widget.bind("<Enter>", _enter, add="+")
        widget.bind("<Leave>", _leave, add="+")
        widget.bind("<ButtonPress>", _leave, add="+")

    def _install_button_tooltips(self):
        pairs = [
            (self._tab_table,    "Show original on the left and the translation on the right (Translation Table mode)"),
            (self._tab_inline,   "Replace identifiers in place inside the original text (Inline Replace mode)"),
            (self._tab_designdoc,"Generate a structured Japanese design document from SQL (Design Doc mode)"),
            (self._tab_forward,  "Translate physical → logical names"),
            (self._tab_reverse,  "Translate logical → physical names"),
            (self._settings_btn, "Theme, layout, filter, exclusions, user map, file operations"),
            (self._help_btn,     "Help & keyboard shortcuts (F1)"),
            (self._translate_btn,"Translate the input (Ctrl+Enter)"),
            (self._copy_btn,     "Copy output to clipboard (Ctrl+Shift+C)"),
            (self._save_btn,     "Save output to a file (Ctrl+S)"),
            (self._history_btn,  "Recent inputs"),
            (self._doctabs_newbtn,"Open a new document tab"),
        ]
        for widget, text in pairs:
            try:
                self._attach_tooltip(widget, text)
            except Exception:
                pass
    def _refresh_index_stats(self):
        self._sb_index.configure(
            text=f"  ● {len(self.table_index)} tables · {len(self.column_index)} columns · "
                 f"{len(self.schemas)} schema(s) loaded")

    # ── Close handler ─────────────────────────────────────────────────────────
    def on_close(self):
        # Persist settings
        try:
            self._capture_active_doc()
            self._settings["doc_tabs"] = [
                {
                    "title":        d.get("title", ""),
                    "input":        d.get("input", ""),
                    "mode":         d.get("mode", "inline"),
                    "direction":    d.get("direction", "forward"),
                    "manual_title": bool(d.get("manual_title", False)),
                }
                for d in self._doctabs
            ]
            self._settings["active_doc"] = self._active_doc
            self._settings.update({
                "theme":         self._theme,
                "mode":          self._mode.get(),
                "direction":     self._direction.get(),
                "filter_schemas":          sorted(self._filter_schemas),
                "filter_tables":           sorted(self._filter_tables),
                "design_uppercase":        bool(self._uppercase_var.get()),
                "design_show_overview":    bool(self._show_overview.get()),
                "design_show_sql_logical": bool(self._show_sql_logical.get()),
                "design_show_sql_physical":bool(self._show_sql_physical.get()),
                "design_show_stype":       bool(self._show_stype.get()),
                "design_show_target":      bool(self._show_target.get()),
                "design_show_projection":  bool(self._show_projection.get()),
                "design_show_from":        bool(self._show_from.get()),
                "design_show_join":        bool(self._show_join.get()),
                "design_show_where":       bool(self._show_where.get()),
                "design_show_group":       bool(self._show_group.get()),
                "design_show_having":      bool(self._show_having.get()),
                "design_show_order":       bool(self._show_order.get()),
                "design_show_footer":      bool(self._show_footer.get()),
                "pane_orient":             self._pane_orient,
                "show_line_numbers":       bool(self._show_line_numbers.get()),
                "word_wrap":               bool(self._word_wrap.get()),
                "auto_paste":              bool(self._auto_paste.get()),
                "font_size":     self._font_size,
                "geometry":      self.winfo_geometry(),
            })
            save_settings(self._settings)
            # Save current input to history if non-trivial
            text = self._current_input().strip()
            if text and len(text) > 10:
                self._add_history(text)
        except Exception:
            pass
        self.destroy()

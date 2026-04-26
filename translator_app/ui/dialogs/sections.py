import tkinter as tk

from ...themes import THEMES


def toggle_sections_popup(app):
    existing = app._sections_popup
    if existing and existing.winfo_exists():
        existing.destroy()
        app._sections_popup = None
        return

    t = THEMES[app._theme]
    popup = tk.Toplevel(app)
    # Hide until we've finished laying out and positioning — otherwise the
    # window manager flashes it at its default (centered) location first.
    popup.withdraw()
    popup.wm_overrideredirect(True)
    popup.attributes("-topmost", True)
    popup.configure(bg=t["muted_bg"])

    # Inner frame (acts as border)
    inner = tk.Frame(popup, bg=t["surface"], bd=0)
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    def add_check(label, var):
        cb = tk.Checkbutton(
            inner, text=label, variable=var,
            bg=t["surface"], fg=t["fg"], selectcolor=t["bg"],
            activebackground=t["accent"], activeforeground=t["accent_fg"],
            anchor="w", font=app._ui,
            bd=0, highlightthickness=0, padx=10, pady=3,
            command=app.on_translate,
        )
        cb.pack(fill="x")

    def add_sep():
        tk.Frame(inner, height=1, bg=t["muted_bg"]).pack(fill="x", padx=2, pady=3)

    add_check("■処理概要",               app._show_overview)
    add_check("【SQL論理名】",            app._show_sql_logical)
    add_check("【SQL定義名】",            app._show_sql_physical)
    add_sep()
    add_check("■処理区分",               app._show_stype)
    add_check("■対象/登録/更新テーブル",   app._show_target)
    add_check("■項目移送/更新項目/抽出項目", app._show_projection)
    add_check("■抽出テーブル (FROM)",     app._show_from)
    add_check("■結合条件 (JOIN)",        app._show_join)
    add_check("■抽出条件 (WHERE)",       app._show_where)
    add_check("■グループ化条件",          app._show_group)
    add_check("■集計後抽出条件",          app._show_having)
    add_check("■並び順",                 app._show_order)
    add_sep()
    add_check("■実行後処理",             app._show_footer)

    # Footer with explicit Done button (Esc / click-outside still work)
    add_sep()
    footer = tk.Frame(inner, bg=t["surface"])
    footer.pack(fill="x", padx=6, pady=(2, 6))
    done_btn = tk.Button(
        footer, text="✓  Done", font=app._ui_b,
        bg=t["accent"], fg=t["accent_fg"],
        activebackground=t["accent"], activeforeground=t["accent_fg"],
        relief="flat", bd=0, padx=14, pady=4, cursor="hand2",
        command=lambda: popup.destroy(),
    )
    done_btn.pack(side="right")
    hint = tk.Label(
        footer, text="Esc to close", font=app._small,
        bg=t["surface"], fg=t["fg_muted"],
    )
    hint.pack(side="left")

    # Position under the button (still withdrawn — no flash). If there isn't
    # enough room below (e.g. horizontal-pane layout pushes the action bar near
    # the bottom of the screen), flip above the button. Also clamp to the
    # screen on the right edge so the popup never spills off-screen.
    app.update_idletasks()
    btn = app._sections_mb
    pop_w = popup.winfo_reqwidth()
    pop_h = popup.winfo_reqheight()
    sw, sh = popup.winfo_screenwidth(), popup.winfo_screenheight()

    btn_x = btn.winfo_rootx()
    btn_y = btn.winfo_rooty()
    btn_h = btn.winfo_height()

    # Vertical: prefer below, fall back to above when not enough room.
    py = btn_y + btn_h + 2
    if py + pop_h > sh - 4:
        above_y = btn_y - pop_h - 2
        if above_y >= 4:
            py = above_y
        else:
            py = max(4, sh - pop_h - 4)
    # Horizontal: keep within the screen.
    px = max(4, min(btn_x, sw - pop_w - 4))

    popup.wm_geometry(f"+{px}+{py}")

    # Now reveal the popup at the correct location
    popup.deiconify()

    # Borderless Toplevels don't get keyboard focus by default, so force it
    # before binding Escape locally — otherwise the keystroke goes elsewhere.
    popup.focus_force()
    popup.bind("<Escape>", lambda e: popup.destroy())

    # Close when user clicks anywhere outside the popup (or the opener button).
    def _is_descendant(widget):
        w = widget
        while w is not None:
            if w == popup:
                return True
            try:
                w = w.master
            except Exception:
                break
        return False

    def _on_click(event):
        if event.widget is app._sections_mb:
            return  # the button itself handles toggle
        if not _is_descendant(event.widget):
            try:
                popup.destroy()
            except Exception:
                pass

    bind_id = app.bind("<Button-1>", _on_click, add="+")

    def _on_destroy(event):
        if event.widget is popup:
            try:
                app.unbind("<Button-1>", bind_id)
            except Exception:
                pass
            app._sections_popup = None

    popup.bind("<Destroy>", _on_destroy)
    app._sections_popup = popup

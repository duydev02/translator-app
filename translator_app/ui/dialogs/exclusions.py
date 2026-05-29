import tkinter as tk
from tkinter import scrolledtext

from ...config import save_exclusions
from ...themes import THEMES
from .placement import place_dialog


def open_exclusions_dialog(app):
    t = THEMES[app._theme]
    dlg = tk.Toplevel(app)
    dlg.title("Exclusion List — do not translate")
    place_dialog(dlg, app, 560, 460, min_width=420, min_height=320)
    dlg.minsize(420, 320)
    dlg.configure(bg=t["bg"])
    dlg.transient(app); dlg.grab_set()

    header = tk.Frame(dlg, bg=t["bg"])
    header.pack(fill="x", padx=14, pady=(12, 4))
    tk.Label(header,
        text="One entry per line. Any match of a listed string is preserved as-is.",
        font=app._ui, bg=t["bg"], fg=t["fg_muted"], anchor="w", justify="left"
    ).pack(fill="x")

    search_row = tk.Frame(dlg, bg=t["bg"])
    search_row.pack(fill="x", padx=14, pady=(4, 4))
    tk.Label(
        search_row, text="Search", font=app._small,
        bg=t["bg"], fg=t["fg_muted"],
    ).pack(side="left")
    search_var = tk.StringVar()
    search_entry = tk.Entry(
        search_row, textvariable=search_var, font=app._ui,
        relief="flat", bd=0, bg=t["surface"], fg=t["fg"],
        insertbackground=t["insert"],
    )
    search_entry.pack(side="left", fill="x", expand=True, padx=(6, 8), ipady=4)
    search_count = tk.Label(
        search_row, text="", font=app._small,
        bg=t["bg"], fg=t["fg_muted"],
    )
    search_count.pack(side="right")

    btns = tk.Frame(dlg, bg=t["bg"])
    btns.pack(side="bottom", fill="x", padx=14, pady=(0, 12))

    editor = scrolledtext.ScrolledText(
        dlg, wrap=tk.NONE, font=app._mono,
        bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", borderwidth=0, padx=8, pady=6,
        undo=True, autoseparators=True, maxundo=-1,
    )
    editor.pack(fill="both", expand=True, padx=14, pady=(4, 8))
    editor.insert("1.0", "\n".join(app._exclusions))
    editor.edit_reset()
    editor.tag_configure("match", background=t.get("tag_search", "#ffd966"),
                         foreground=t.get("accent_fg", "#000000"))

    def _delete_lines():
        editor.edit_separator()
        try:
            sel_first = editor.index("sel.first")
            sel_last  = editor.index("sel.last")
            start = editor.index(f"{sel_first} linestart")
            if editor.index(f"{sel_last} linestart") == sel_last:
                end = sel_last
            else:
                end = editor.index(f"{sel_last} lineend +1c")
        except tk.TclError:
            cur = editor.index("insert")
            start = editor.index(f"{cur} linestart")
            end   = editor.index(f"{cur} lineend +1c")
        editor.delete(start, end)
        editor.edit_separator()
        editor.focus_set()

    def _line_matches(line: str, query: str) -> bool:
        return query.lower() in line.lower()

    def _refresh_search(*_):
        editor.tag_remove("match", "1.0", tk.END)
        query = search_var.get().strip()
        if not query:
            search_count.configure(text="")
            return
        count = 0
        pos = "1.0"
        while True:
            idx = editor.search(query, pos, stopindex=tk.END, nocase=True)
            if not idx:
                break
            end = f"{idx}+{len(query)}c"
            editor.tag_add("match", idx, end)
            pos = end
            count += 1
        search_count.configure(text=f"{count} match{'es' if count != 1 else ''}")

    def _delete_matches():
        query = search_var.get().strip()
        if not query:
            return
        lines = editor.get("1.0", tk.END).splitlines()
        kept = [line for line in lines if not _line_matches(line, query)]
        removed = len(lines) - len(kept)
        if not removed:
            return
        editor.edit_separator()
        editor.delete("1.0", tk.END)
        editor.insert("1.0", "\n".join(kept))
        editor.edit_separator()
        _refresh_search()
        try:
            app._toast.show(f"Removed {removed} matching exclusion(s)", 1400, "info")
        except Exception:
            pass

    def _undo():
        try: editor.edit_undo()
        except tk.TclError: pass
        editor.focus_set()

    def _redo():
        try: editor.edit_redo()
        except tk.TclError: pass
        editor.focus_set()

    editor.bind("<Control-d>", lambda e: (_delete_lines(), "break")[1])
    editor.bind("<Control-y>", lambda e: (_redo(), "break")[1])
    search_var.trace_add("write", _refresh_search)
    search_entry.bind("<Escape>", lambda e: (search_var.set(""), "break")[1] if search_var.get() else None)

    def _save():
        content = editor.get("1.0", tk.END)
        lines = [ln.rstrip("\r") for ln in content.split("\n")]
        app._exclusions = [ln for ln in lines if ln.strip()]
        save_exclusions(app._exclusions)
        app._refresh_excl_btn()
        dlg.destroy()
        app.on_translate()
        app._toast.show(f"Saved {len(app._exclusions)} exclusions", 1400, "success")

    tk.Button(btns, text="Save", font=app._btn, relief="flat",
        padx=18, pady=6, cursor="hand2", bd=0,
        bg=t["accent"], fg=t["accent_fg"],
        activebackground=t["accent"], activeforeground=t["accent_fg"],
        command=_save).pack(side="right")
    tk.Button(btns, text="Cancel", font=app._btn, relief="flat",
        padx=14, pady=6, cursor="hand2", bd=0,
        bg=t["muted_bg"], fg=t["muted_fg"],
        activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
        command=dlg.destroy).pack(side="right", padx=(0, 6))

    for label, cmd in [("🗑  Delete line", _delete_lines), ("Delete matches", _delete_matches), ("↶  Undo", _undo), ("↷  Redo", _redo)]:
        tk.Button(btns, text=label, font=app._btn, relief="flat",
            padx=12, pady=6, cursor="hand2", bd=0,
            bg=t["muted_bg"], fg=t["muted_fg"],
            activebackground=t["muted_bg"], activeforeground=t["muted_fg"],
            command=cmd).pack(side="left", padx=(0, 6))

    tk.Label(dlg, text="Ctrl+D delete line · Ctrl+Z undo · Ctrl+Y redo",
        font=app._small, bg=t["bg"], fg=t["fg_muted"], anchor="w"
    ).pack(side="bottom", fill="x", padx=14, pady=(0, 4))

    editor.focus_set()

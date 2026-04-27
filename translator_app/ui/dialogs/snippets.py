"""Snippet library — save reusable Java SQL-builder methods (or any input
text) and reload them later. Stored alongside translator_settings.json so
they persist across sessions.

Snippet shape (one entry):
    {
        "name":    "INSERT R_TANPINTEN_TORIATUKAI (multi-buffer)",
        "tags":    ["insert", "tenpinten", "multi-buffer"],
        "content": "<full Java method text>",
        "created": "2026-04-27T15:34:21",
    }

UI:
    [search box                                ]   [Save current as snippet]
    ┌─ Snippets ─────────────────────────┐ ┌─ Preview ─────────────────────┐
    │ name              tags             │ │ <full content with monospace>  │
    │ ...                                │ │                                │
    └────────────────────────────────────┘ └────────────────────────────────┘
    [actions: Load · Rename · Delete · Close]
"""

import datetime as _dt
import re
import tkinter as tk
from tkinter import ttk, simpledialog

from ...themes import THEMES
from ...config import save_settings


def _now_iso():
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def _snippets(app):
    """Return the live snippets list, creating it if missing."""
    sn = app._settings.setdefault("snippets", [])
    if not isinstance(sn, list):
        sn = []
        app._settings["snippets"] = sn
    return sn


def _persist(app):
    try:
        save_settings(app._settings)
    except Exception:
        pass


def open_snippets_dialog(app):
    existing = getattr(app, "_snippets_dialog", None)
    if existing and existing.winfo_exists():
        existing.lift()
        existing.focus_force()
        return

    t = THEMES[app._theme]
    dlg = tk.Toplevel(app)
    app._snippets_dialog = dlg
    dlg.title("Snippets")
    dlg.geometry("1080x680")
    dlg.configure(bg=t["bg"])
    dlg.transient(app)

    # Top: search + Save current
    top = tk.Frame(dlg, bg=t["bg"])
    top.pack(fill="x", padx=14, pady=(12, 6))
    tk.Label(top, text="🔎  Search:", font=app._ui_b,
             bg=t["bg"], fg=t["fg"]).pack(side="left")
    search_var = tk.StringVar()
    search_entry = tk.Entry(
        top, textvariable=search_var, font=app._ui,
        bg=t["surface"], fg=t["fg"], insertbackground=t["insert"],
        relief="flat", bd=0,
    )
    search_entry.pack(side="left", fill="x", expand=True, padx=(8, 8), ipady=5)

    def _save_current():
        text = app._current_input()
        if not text or not text.strip():
            app._toast.show("Input is empty — nothing to save", 1100, "info")
            return
        name = simpledialog.askstring(
            "Save snippet",
            "Snippet name:",
            initialvalue=_default_name(text),
            parent=dlg,
        )
        if not name:
            return
        tags_raw = simpledialog.askstring(
            "Save snippet",
            "Tags (comma-separated, optional):",
            parent=dlg,
        )
        tags = [t.strip() for t in (tags_raw or "").split(",") if t.strip()]
        _snippets(app).append({
            "name": name.strip(), "tags": tags,
            "content": text, "created": _now_iso(),
        })
        _persist(app)
        _refresh()
        app._toast.show(f"Saved snippet: {name}", 1300, "success")

    save_btn = tk.Button(
        top, text="💾  Save current as snippet", font=app._ui_b,
        bg=t["accent"], fg=t["accent_fg"],
        activebackground=t["accent"], activeforeground=t["accent_fg"],
        relief="flat", bd=0, padx=12, pady=5, cursor="hand2",
        command=_save_current,
    )
    save_btn.pack(side="right")

    # Body — list + preview
    body = tk.Frame(dlg, bg=t["bg"])
    body.pack(fill="both", expand=True, padx=14, pady=(0, 8))
    body.columnconfigure(0, weight=1, uniform="sn")
    body.columnconfigure(1, weight=2, uniform="sn")
    body.rowconfigure(0, weight=1)

    # List
    list_frame = tk.Frame(body, bg=t["bg"])
    list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
    style = ttk.Style()
    style.configure(
        "Snip.Treeview",
        background=t["surface"], fieldbackground=t["surface"], foreground=t["fg"],
        bordercolor=t["bg"], borderwidth=0, rowheight=24,
    )
    style.configure(
        "Snip.Treeview.Heading",
        background=t["muted_bg"], foreground=t["fg"], relief="flat",
    )
    style.map("Snip.Treeview",
              background=[("selected", t["accent"])],
              foreground=[("selected", t["accent_fg"])])
    list_sb = tk.Scrollbar(list_frame, orient="vertical")
    tree = ttk.Treeview(
        list_frame, columns=("name", "tags", "created"), show="headings",
        style="Snip.Treeview", yscrollcommand=list_sb.set,
    )
    tree.heading("name",    text="Name")
    tree.heading("tags",    text="Tags")
    tree.heading("created", text="Created")
    tree.column("name",    width=240, anchor="w", stretch=True)
    tree.column("tags",    width=140, anchor="w")
    tree.column("created", width=140, anchor="w")
    list_sb.configure(command=tree.yview, bg=t["bg"], troughcolor=t["bg"], bd=0)
    tree.pack(side="left", fill="both", expand=True)
    list_sb.pack(side="right", fill="y")

    # Preview
    prev_frame = tk.Frame(body, bg=t["bg"])
    prev_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
    prev_sb = tk.Scrollbar(prev_frame, orient="vertical")
    prev_txt = tk.Text(
        prev_frame, font=app._mono, wrap="word",
        bg=t["surface"], fg=t["fg"],
        relief="flat", borderwidth=0, padx=10, pady=8,
        yscrollcommand=prev_sb.set,
        selectbackground=t["accent"], selectforeground=t["accent_fg"],
        inactiveselectbackground=t["accent"],
    )
    prev_sb.configure(command=prev_txt.yview, bg=t["bg"], troughcolor=t["bg"], bd=0)
    prev_txt.pack(side="left", fill="both", expand=True)
    prev_sb.pack(side="right", fill="y")
    prev_txt.configure(state="disabled")

    state = {"filtered": []}   # list of indices into snippets list

    def _refresh():
        snippets = _snippets(app)
        q = search_var.get().strip().lower()
        rows = []
        for idx, s in enumerate(snippets):
            haystack = (s.get("name", "") + " " +
                        " ".join(s.get("tags") or []) + " " +
                        s.get("content", ""))
            if q and q not in haystack.lower():
                continue
            rows.append(idx)
        state["filtered"] = rows
        tree.delete(*tree.get_children())
        for idx in rows:
            s = snippets[idx]
            tree.insert("", "end", iid=str(idx), values=(
                s.get("name", ""),
                ", ".join(s.get("tags") or []),
                (s.get("created") or "")[:16].replace("T", " "),
            ))
        # Update preview if selected snippet still visible
        sel = tree.selection()
        if sel:
            _show_preview(int(sel[0]))
        else:
            _show_preview(None)

    def _show_preview(idx):
        prev_txt.configure(state="normal")
        prev_txt.delete("1.0", tk.END)
        if idx is None:
            prev_txt.insert("1.0", "(select a snippet to preview)")
        else:
            s = _snippets(app)[idx]
            prev_txt.insert("1.0", s.get("content", ""))
        prev_txt.configure(state="disabled")

    def _on_select(_evt):
        sel = tree.selection()
        if sel:
            _show_preview(int(sel[0]))

    def _selected_snippet_idx():
        sel = tree.selection()
        return int(sel[0]) if sel else None

    def _load_selected(_evt=None):
        idx = _selected_snippet_idx()
        if idx is None:
            return
        s = _snippets(app)[idx]
        try:
            app._clear_placeholder()
            app.input_box.delete("1.0", tk.END)
            app.input_box.insert("1.0", s.get("content", ""))
            app.on_translate()
            app._toast.show(f"Loaded: {s.get('name','')}", 1100, "success")
            dlg.destroy()
        except Exception:
            pass

    def _rename_selected():
        idx = _selected_snippet_idx()
        if idx is None: return
        s = _snippets(app)[idx]
        new_name = simpledialog.askstring(
            "Rename snippet", "New name:", initialvalue=s.get("name", ""), parent=dlg,
        )
        if not new_name or not new_name.strip(): return
        s["name"] = new_name.strip()
        new_tags = simpledialog.askstring(
            "Rename snippet", "Tags (comma-separated):",
            initialvalue=", ".join(s.get("tags") or []), parent=dlg,
        )
        if new_tags is not None:
            s["tags"] = [x.strip() for x in new_tags.split(",") if x.strip()]
        _persist(app)
        _refresh()

    def _delete_selected():
        idx = _selected_snippet_idx()
        if idx is None: return
        s = _snippets(app)[idx]
        if not _confirm(dlg, f"Delete snippet '{s.get('name','')}'?"):
            return
        del _snippets(app)[idx]
        _persist(app)
        _refresh()

    tree.bind("<<TreeviewSelect>>", _on_select)
    tree.bind("<Double-1>",         _load_selected)
    tree.bind("<Return>",           _load_selected)
    search_var.trace_add("write", lambda *_: _refresh())

    # Actions
    actions = tk.Frame(dlg, bg=t["bg"])
    actions.pack(fill="x", padx=14, pady=(0, 12))

    def _btn(parent, text, command, accent=False):
        return tk.Button(
            parent, text=text, font=app._ui_b,
            bg=t["accent"] if accent else t["muted_bg"],
            fg=t["accent_fg"] if accent else t["muted_fg"],
            activebackground=t["accent"] if accent else t["muted_bg"],
            activeforeground=t["accent_fg"] if accent else t["muted_fg"],
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
            command=command,
        )

    _btn(actions, "Load into input",  _load_selected, accent=True).pack(side="left", padx=(0, 6))
    _btn(actions, "Rename",           _rename_selected).pack(side="left", padx=(0, 6))
    _btn(actions, "Delete",           _delete_selected).pack(side="left", padx=(0, 6))
    _btn(actions, "Close",            dlg.destroy).pack(side="right")

    _refresh()
    search_entry.focus_set()

    dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _on_destroy(_e=None):
        if getattr(app, "_snippets_dialog", None) is dlg:
            app._snippets_dialog = None
    dlg.bind("<Destroy>", _on_destroy)


def _default_name(text):
    """Suggest a snippet name from the input — uses the Java method name
    when present, else first non-blank line."""
    m = re.search(
        r"\b(?:public|private|protected|static|final|synchronized|\s)+"
        r"[\w<>\[\],\s]+?\s+(\w+)\s*\(",
        text,
    )
    if m:
        return m.group(1)
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith(("/*", "*", "//")):
            return s[:60]
    return "snippet"


def _confirm(parent, message):
    """Tiny confirm dialog using tk_dialog without messagebox-import bloat."""
    from tkinter import messagebox
    try:
        return bool(messagebox.askyesno("Confirm", message, parent=parent))
    except Exception:
        return True

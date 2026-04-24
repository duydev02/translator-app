import tkinter as tk
from tkinter import font

from ..themes import THEMES


# Optional drag-and-drop support (tkinterdnd2 if available)
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES   # type: ignore
    _DND_AVAILABLE = True
except Exception:
    _DND_AVAILABLE = False


# ── Line-number sidebar for Text widgets ─────────────────────────────────────
def _install_text_change_proxy(text):
    """Install a Tcl proxy on a Text widget so every internal operation that
    could change the content or the viewport fires a <<TextChange>> virtual
    event. Needed to keep the line-number sidebar in sync with scrolling,
    typing, programmatic insert/delete, etc. Idempotent."""
    if getattr(text, "_change_proxy_installed", False):
        return
    text._change_proxy_installed = True
    orig = text._w + "_orig_change_proxy"
    text.tk.call("rename", text._w, orig)

    def proxy(*args):
        try:
            result = text.tk.call((orig,) + args)
        except tk.TclError:
            return None
        if args:
            op = args[0]
            if op in ("insert", "replace", "delete", "mark", "xview", "yview"):
                try:
                    text.event_generate("<<TextChange>>", when="tail")
                except tk.TclError:
                    pass
        return result

    text.tk.createcommand(text._w, proxy)


class LineNumberCanvas(tk.Canvas):
    """Narrow canvas that draws line numbers beside a Text widget and stays
    in sync with its scrolling / resizing / editing."""

    def __init__(self, parent, text_widget, theme_fn, width=44):
        super().__init__(parent, width=width, bd=0, highlightthickness=0)
        self.text = text_widget
        self._theme_fn = theme_fn
        self._pending = False
        _install_text_change_proxy(text_widget)

        self.text.bind("<<TextChange>>", self._schedule, add="+")
        self.text.bind("<Configure>",    self._schedule, add="+")
        self.bind("<Configure>",         self._schedule, add="+")
        self._schedule()

    def _schedule(self, _event=None):
        if self._pending:
            return
        self._pending = True
        self.after_idle(self._redraw)

    def _redraw(self):
        self._pending = False
        if not self.winfo_exists():
            return
        self.delete("all")
        t = self._theme_fn()
        self.configure(bg=t["surface"])
        fg = t["fg_muted"]

        try:
            tfont = font.nametofont(str(self.text.cget("font")))
        except tk.TclError:
            tfont = font.Font(font=self.text.cget("font"))

        w = max(self.winfo_width() - 4, 4)
        i = self.text.index("@0,0")
        steps = 0
        while steps < 20000:
            try:
                dline = self.text.dlineinfo(i)
            except tk.TclError:
                break
            if dline is None:
                break
            _, y, _, h, _ = dline
            num = i.split(".")[0]
            self.create_text(
                w, y + h / 2,
                anchor="e", text=num, fill=fg, font=tfont,
            )
            nxt = self.text.index(f"{i}+1line")
            if nxt == i:
                break
            i = nxt
            steps += 1


# ── Floating UI helpers ───────────────────────────────────────────────────────
class Tooltip:
    def __init__(self, parent):
        self.parent = parent
        self.tw = None
        self._label = None
        self._theme_fn = lambda: THEMES["light"]

    def set_theme_fn(self, fn):
        self._theme_fn = fn

    def show(self, text, x_root, y_root):
        t = self._theme_fn()
        if self.tw is None:
            self.tw = tk.Toplevel(self.parent)
            self.tw.wm_overrideredirect(True)
            self.tw.attributes("-topmost", True)
            self._label = tk.Label(
                self.tw, text=text, justify="left",
                bg=t["surface"], fg=t["fg"],
                font=("Segoe UI", 9), padx=10, pady=6,
                bd=1, relief="solid",
            )
            self._label.pack()
        else:
            self._label.configure(text=text, bg=t["surface"], fg=t["fg"])
        self.tw.wm_geometry(f"+{x_root + 16}+{y_root + 18}")

    def hide(self):
        if self.tw:
            self.tw.destroy()
            self.tw = None


class Toast:
    def __init__(self, parent):
        self.parent = parent
        self.tw = None
        self._job = None
        self._theme_fn = lambda: THEMES["light"]

    def set_theme_fn(self, fn):
        self._theme_fn = fn

    def show(self, text, duration=1800, kind="info"):
        t = self._theme_fn()
        color_map = {"info": t["accent"], "success": t["success"], "error": t["danger"]}
        bg = color_map.get(kind, t["accent"])
        fg = t["accent_fg"] if kind == "info" else "#1e1e2e"
        if self.tw:
            self.tw.destroy()
        self.tw = tk.Toplevel(self.parent)
        self.tw.wm_overrideredirect(True)
        self.tw.attributes("-topmost", True)
        lbl = tk.Label(
            self.tw, text=text,
            bg=bg, fg=fg,
            font=("Segoe UI", 10, "bold"),
            padx=18, pady=8, bd=0,
        )
        lbl.pack()
        # Position in bottom-right of parent window
        self.parent.update_idletasks()
        px = self.parent.winfo_rootx() + self.parent.winfo_width() - 260
        py = self.parent.winfo_rooty() + self.parent.winfo_height() - 80
        self.tw.wm_geometry(f"+{max(px, 0)}+{max(py, 0)}")
        if self._job:
            try: self.parent.after_cancel(self._job)
            except Exception: pass
        self._job = self.parent.after(duration, self.hide)

    def hide(self):
        if self.tw:
            try: self.tw.destroy()
            except Exception: pass
            self.tw = None


# ── Main Application ──────────────────────────────────────────────────────────
_BaseTk = TkinterDnD.Tk if _DND_AVAILABLE else tk.Tk

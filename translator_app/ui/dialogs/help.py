import tkinter as tk
from tkinter import scrolledtext

from ...themes import THEMES


def show_help_dialog(app):
    t = THEMES[app._theme]
    dlg = tk.Toplevel(app)
    dlg.title("Keyboard Shortcuts & Features")
    dlg.geometry("640x520")
    dlg.configure(bg=t["bg"])
    dlg.transient(app); dlg.grab_set()

    content = (
        "──  TRANSLATION  ──\n"
        "  Ctrl+Enter           Translate now\n"
        "  Ctrl+M               Toggle Table / Inline mode\n"
        "  Ctrl+Shift+D         Toggle Forward / Reverse direction\n"
        "\n"
        "──  CLIPBOARD / FILES  ──\n"
        "  Ctrl+Shift+C         Copy output\n"
        "  Ctrl+S               Save output to file\n"
        "  Ctrl+R               Reload db_schema_output.json\n"
        "  Drag file onto window  Load file into input\n"
        "  ⚙ Settings menu      Open file… / Reload JSON\n"
        "\n"
        "──  EDITING  ──\n"
        "  Ctrl+⌫ (BackSpace)  Clear input + output\n"
        "  Ctrl+F               Open search bar\n"
        "  Esc                  Close search bar / popup\n"
        "  F3 / Shift+F3        Find next / previous\n"
        "\n"
        "──  VIEW  ──\n"
        "  Ctrl + / =           Zoom in\n"
        "  Ctrl + -             Zoom out\n"
        "  Ctrl + 0             Reset zoom\n"
        "  Ctrl + L             Toggle line numbers\n"
        "  ⚙ Settings menu      Theme / Layout toggles\n"
        "\n"
        "──  FILTER / EXCLUSIONS / USER MAP  ──\n"
        "  ⚙ Settings menu      Filter… (limit by schema/tables)\n"
        "                       Exclusions… (skip specific tokens)\n"
        "                       User Map… (custom name overrides)\n"
        "  Right-click text     Add / remove exclusion inline\n"
        "  Ctrl+D (in dialog)   Delete selected lines\n"
        "  Ctrl+Z / Ctrl+Y      Undo / redo\n"
        "\n"
        "──  INLINE MODE  ──\n"
        "  Blue underline        Table name replacement\n"
        "  Green underline       Column name replacement\n"
        "  Yellow underline + ⚠  Ambiguous (multiple logical names)\n"
        "  Hover                 Show tooltip with full context\n"
        "  Lowercase identifiers Also matched (e.g. tab_col → 名前)\n"
        "\n"
        "──  DESIGN DOC MODE  ──\n"
        "  Paste a Java method   (with sb.append(\"...\") SQL-builder)\n"
        "                        → generates ■処理区分 / ■登録テーブル /\n"
        "                          ■項目移送 … design-doc template.\n"
        "  UPPERCASE columns     Toggle to force column names to uppercase.\n"
        "  ⚙ Sections ▾          Toggle which design-doc sections to show.\n"
        "  Forward direction     Physical names → logical (Japanese).\n"
        "  Reverse direction     Keep / restore physical names.\n"
        "\n"
        "──  DOC TABS (multi-input)  ──\n"
        "  Ctrl+T               New tab\n"
        "  Ctrl+W               Close current tab\n"
        "  Ctrl+Tab             Next tab\n"
        "  Ctrl+Shift+Tab       Previous tab\n"
        "  Double-click title   Rename tab inline (Esc cancels)\n"
        "  Right-click tab      Rename / Duplicate / Close / Close Others\n"
        "\n"
        "──  MISC  ──\n"
        "  F1                   Show this help\n"
        "  History dropdown     Re-load last 10 inputs\n"
    )
    txt = scrolledtext.ScrolledText(dlg, wrap=tk.WORD, font=app._mono,
        bg=t["output_bg"], fg=t["fg"], relief="flat", borderwidth=0, padx=12, pady=10)
    txt.pack(fill="both", expand=True, padx=14, pady=(12, 8))
    txt.insert("1.0", content)
    txt.configure(state="disabled")

    tk.Button(dlg, text="Close", font=app._btn, relief="flat",
        padx=18, pady=6, cursor="hand2", bd=0,
        bg=t["accent"], fg=t["accent_fg"],
        activebackground=t["accent"], activeforeground=t["accent_fg"],
        command=dlg.destroy
    ).pack(side="bottom", pady=(0, 12))

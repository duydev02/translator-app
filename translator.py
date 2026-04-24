import os
import sys
import tkinter as tk
from tkinter import messagebox

from translator_app.paths import JSON_FILE
from translator_app.ui.app import TranslatorApp
from translator_app.ui.widgets import _DND_AVAILABLE


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not os.path.exists(JSON_FILE):
        msg = (
            f"db_schema_output.json was not found next to the application.\n\n"
            f"Expected location:\n  {JSON_FILE}\n\n"
            f"Please place db_schema_output.json in this folder and try again."
        )
        try:
            # Use a tiny Tk root so packaged (--windowed) users see a dialog
            root = tk.Tk(); root.withdraw()
            messagebox.showerror("Missing data file", msg)
        except Exception:
            print("ERROR:", msg)
        sys.exit(1)

    print(f"Loading: {JSON_FILE}")
    app = TranslatorApp(JSON_FILE)
    if not _DND_AVAILABLE:
        print("  (Install 'tkinterdnd2' for drag-and-drop file support: pip install tkinterdnd2)")
    app.mainloop()

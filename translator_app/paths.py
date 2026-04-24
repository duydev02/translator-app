import os
import sys


# ── Files ─────────────────────────────────────────────────────────────────────
def _app_dir():
    """Folder containing the exe (when frozen) or this script (in dev).

    Important for PyInstaller: sys.frozen is set and sys.executable points to
    the exe. In that case we want data files next to the exe, NOT inside the
    temporary _MEIxxxxxx extraction directory.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # __file__ is translator_app/paths.py — data files live one level above.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE_DIR        = _app_dir()
JSON_FILE       = os.path.join(BASE_DIR, "db_schema_output.json")
USER_MAP_FILE   = os.path.join(BASE_DIR, "translator_custom_map.json")
EXCLUSIONS_FILE = os.path.join(BASE_DIR, "translator_exclusions.txt")
SETTINGS_FILE   = os.path.join(BASE_DIR, "translator_settings.json")
HISTORY_FILE    = os.path.join(BASE_DIR, "translator_history.txt")

# Marker "schema" used when injecting user overrides into the indexes so the
# rest of the logic can detect them uniformly.
CUSTOM_SCHEMA   = "(custom)"

MAX_HISTORY = 10

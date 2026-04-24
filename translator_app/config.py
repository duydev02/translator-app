import json
import os

from .paths import (
    EXCLUSIONS_FILE,
    HISTORY_FILE,
    MAX_HISTORY,
    SETTINGS_FILE,
    USER_MAP_FILE,
)


# ── Simple persistence ────────────────────────────────────────────────────────
def load_exclusions():
    if not os.path.exists(EXCLUSIONS_FILE):
        return []
    with open(EXCLUSIONS_FILE, "r", encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f if ln.strip()]


def save_exclusions(exclusions):
    with open(EXCLUSIONS_FILE, "w", encoding="utf-8") as f:
        for e in exclusions:
            f.write(e + "\n")


def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        items = [x for x in content.split("\x1e") if x.strip()]
        return items[-MAX_HISTORY:]
    except Exception:
        return []


def save_history(items):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            f.write("\x1e".join(items[-MAX_HISTORY:]))
    except Exception:
        pass


def load_user_map():
    """Return {'tables': {phys: logical}, 'columns': {phys: logical}}."""
    if not os.path.exists(USER_MAP_FILE):
        return {"tables": {}, "columns": {}}
    try:
        with open(USER_MAP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "tables":  dict(data.get("tables")  or {}),
            "columns": dict(data.get("columns") or {}),
        }
    except Exception as e:
        print(f"Warning: could not parse {USER_MAP_FILE}: {e}")
        return {"tables": {}, "columns": {}}


def save_user_map(data):
    # Normalise shape before writing
    out = {
        "tables":  dict(data.get("tables")  or {}),
        "columns": dict(data.get("columns") or {}),
    }
    with open(USER_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

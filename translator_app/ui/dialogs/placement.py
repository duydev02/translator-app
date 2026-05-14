"""Dialog placement helpers.

Tk's screen-size APIs are unreliable on Windows multi-monitor desktops: they
often report only the primary monitor, which can pull child dialogs back to
screen A even when the main app is on screen B/C. These helpers center dialogs
near their parent and, on Windows, use the native monitor rectangle for the
parent window.
"""

from __future__ import annotations

import ctypes
import sys


def geometry_near_parent(parent, width, height, *, min_width=1, min_height=1, y_bias=0):
    """Return a Tk geometry string centered on parent.

    This is pure and testable. It deliberately avoids clamping to
    winfo_screenwidth()/height() unless Tk's virtual root actually contains the
    parent; otherwise multi-monitor windows get snapped back to the primary.
    """
    try:
        parent.update_idletasks()
    except Exception:
        pass

    try:
        px = int(parent.winfo_rootx())
        py = int(parent.winfo_rooty())
        pw = int(parent.winfo_width())
        ph = int(parent.winfo_height())
    except Exception:
        px, py, pw, ph = 0, 0, width, height
    if pw <= 1 or ph <= 1:
        pw, ph = width, height

    try:
        vx = int(parent.winfo_vrootx())
        vy = int(parent.winfo_vrooty())
        vw = int(parent.winfo_vrootwidth())
        vh = int(parent.winfo_vrootheight())
    except Exception:
        vx, vy, vw, vh = 0, 0, 0, 0

    width = max(int(width), int(min_width))
    height = max(int(height), int(min_height))
    virtual_contains_parent = vw > 0 and vh > 0 and vx <= px < vx + vw and vy <= py < vy + vh
    if virtual_contains_parent:
        width = min(width, max(vw - 40, int(min_width)))
        height = min(height, max(vh - 80, int(min_height)))

    x = px + (pw - width) // 2
    y = py + (ph - height) // 2 + int(y_bias)

    if virtual_contains_parent:
        x = max(vx, min(x, vx + max(vw - width, 0)))
        y = max(vy, min(y, vy + max(vh - height, 0)))

    return f"{width}x{height}{x:+d}{y:+d}"


def place_dialog(dialog, parent, width, height, *, min_width=1, min_height=1, y_bias=0):
    """Place a Toplevel near parent, using native monitor bounds on Windows."""
    width = max(int(width), int(min_width))
    height = max(int(height), int(min_height))
    try:
        dialog.geometry(geometry_near_parent(
            parent, width, height, min_width=min_width, min_height=min_height, y_bias=y_bias
        ))
    except Exception:
        pass
    if sys.platform.startswith("win"):
        _place_dialog_win32(dialog, parent, width, height, y_bias=y_bias)


def _place_dialog_win32(dialog, parent, width, height, *, y_bias=0):
    try:
        parent.update_idletasks()
        dialog.update_idletasks()
        user32 = ctypes.windll.user32
        parent_hwnd = parent.winfo_id()
        dialog_hwnd = dialog.winfo_id()

        monitor = user32.MonitorFromWindow(parent_hwnd, 2)  # MONITOR_DEFAULTTONEAREST

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_ulong),
            ]

        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return

        px = int(parent.winfo_rootx())
        py = int(parent.winfo_rooty())
        pw = int(parent.winfo_width())
        ph = int(parent.winfo_height())
        if pw <= 1 or ph <= 1:
            work = info.rcWork
            pw = work.right - work.left
            ph = work.bottom - work.top
            px, py = work.left, work.top

        work = info.rcWork
        x = px + (pw - width) // 2
        y = py + (ph - height) // 2 + int(y_bias)
        x = max(work.left, min(x, work.right - width))
        y = max(work.top, min(y, work.bottom - height))

        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        user32.SetWindowPos(dialog_hwnd, 0, x, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE)
    except Exception:
        pass

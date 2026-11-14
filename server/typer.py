"""Types text into whatever Mac app has focus, through CGEvent. ctypes only.

Constants are from the macOS SDK headers
(CGEventTypes.h). macOS must allow the app the server runs in (Terminal) under System Settings >
Privacy & Security > Accessibility. Tests reach this module through fakes only.
"""

from __future__ import annotations

import ctypes
import functools
import time

HID_TAP = 0  # kCGHIDEventTap
KEY_GAP_S = 0.002  # shortcut: untuned pause after each posted key, raise it if characters go missing


@functools.cache
def _frameworks() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    q = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    p, flag = ctypes.c_void_p, ctypes.c_bool
    for lib, name, result, args in (
        (q, "CGPreflightPostEventAccess", flag, ()),
        (q, "CGRequestPostEventAccess", flag, ()),
        (q, "CGEventCreateKeyboardEvent", p, (p, ctypes.c_uint16, flag)),
        (q, "CGEventSetFlags", None, (p, ctypes.c_uint64)),
        (q, "CGEventKeyboardSetUnicodeString", None, (p, ctypes.c_ulong, ctypes.c_char_p)),
        (q, "CGEventPost", None, (ctypes.c_uint32, p)),
        (cf, "CFRelease", None, (p,)),
    ):
        function = getattr(lib, name)
        function.restype, function.argtypes = result, args
    return q, cf


def type_text(text: str) -> None:
    """Types text into the app in front, one key down/up pair a character, so no keyboard layout is needed. Blocking."""
    q, cf = _frameworks()
    if not q.CGPreflightPostEventAccess():
        q.CGRequestPostEventAccess()  # makes macOS list the app in the pane, so the owner only flips the switch
        raise PermissionError("macOS has not allowed typing yet. Open System Settings > Privacy & Security > "
                              "Accessibility, switch on the app the server runs in, then try again.")
    for char in text:
        units = char.encode("utf-16-le")
        for down in (True, False):
            event = q.CGEventCreateKeyboardEvent(None, 0, down)
            if not event:
                raise OSError("macOS would not create a key event")
            q.CGEventSetFlags(event, 0)  # a modifier still held down must not turn letters into shortcuts
            q.CGEventKeyboardSetUnicodeString(event, len(units) // 2, units)
            q.CGEventPost(HID_TAP, event)
            cf.CFRelease(event)
            time.sleep(KEY_GAP_S)

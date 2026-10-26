"""The OS-specific modules for the running platform, under the names the rest of the app uses.

Windows: win32.py, paste.py and credentials.py. Linux (X11 sessions): x11.py, paste_x11.py and
credentials_linux.py, which offer the same functions; there `win32` below is x11.py.
"""

import sys

if sys.platform == "win32":
    from . import credentials, paste, win32
else:
    from . import credentials_linux as credentials
    from . import paste_x11 as paste
    from . import x11 as win32

ForegroundApp = win32.ForegroundApp
foreground_app = win32.foreground_app

__all__ = ["credentials", "paste", "win32", "ForegroundApp", "foreground_app"]

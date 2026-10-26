"""Writes the app icon (the tray's "ready" icon) as a multi-size .ico: make_icon.py OUT.ico"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from talk_to_mic.tray import Status, _icon_image  # noqa: E402

out = Path(sys.argv[1])
out.parent.mkdir(parents=True, exist_ok=True)
_icon_image(Status.IDLE.color, 256).save(out, sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])

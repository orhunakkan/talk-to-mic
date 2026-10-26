"""PyInstaller entry point; equivalent to `pythonw -m talk_to_mic`."""

import sys

from talk_to_mic.__main__ import main

sys.exit(main())

#!/usr/bin/env bash
# Installs talk-to-mic for the current user on Linux (X11 desktops such as Cinnamon, MATE, Xfce),
# the counterpart of the Windows installer: no root needed, a menu entry, optionally start at sign-in.
#
#   packaging/linux/install.sh [--autostart | --no-autostart]   install, or upgrade over an older install
#   packaging/linux/install.sh --uninstall                     remove it; settings are kept
#
# Installs into ~/.local/share/talk-to-mic (a copy of the app plus its own Python environment),
# with the launcher ~/.local/bin/talk-to-mic. Settings, history and logs live in ~/.config/talk-to-mic.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
PREFIX="$DATA/talk-to-mic"
LAUNCHER="$HOME/.local/bin/talk-to-mic"
DESKTOP_FILE="$DATA/applications/talk-to-mic.desktop"
AUTOSTART_FILE="$CONFIG/autostart/talk-to-mic.desktop"
ICON="$DATA/icons/hicolor/256x256/apps/talk-to-mic.png"

autostart=""
case "${1:-}" in
    --autostart) autostart=yes ;;
    --no-autostart) autostart=no ;;
    --uninstall)
        rm -rf "$PREFIX"
        rm -f "$LAUNCHER" "$DESKTOP_FILE" "$AUTOSTART_FILE" "$ICON"
        echo "talk-to-mic is uninstalled. Your settings and history are still in $CONFIG/talk-to-mic."
        echo "Quit it from the tray icon if it is still running."
        exit 0 ;;
    "") ;;
    *) sed -n '2,10p' "$0"; exit 2 ;;
esac

if [ "${XDG_SESSION_TYPE:-x11}" = "wayland" ]; then
    echo "Warning: this is a Wayland session. talk-to-mic needs X11 to watch the hotkey and type text;" >&2
    echo "choose an X11 session at the login screen." >&2
fi

# Python 3.14, as on Windows: the pins in requirements.txt have wheels for it.
python=""
for candidate in python3.14 "$(command -v uv >/dev/null && uv python find 3.14 2>/dev/null || true)"; do
    if [ -n "$candidate" ] && command -v "$candidate" >/dev/null; then python="$(command -v "$candidate")"; break; fi
done
if [ -z "$python" ]; then
    echo "Python 3.14 is needed. Install it, for example with uv (https://docs.astral.sh/uv/):" >&2
    echo "    uv python install 3.14" >&2
    exit 1
fi

# Shared libraries the Python packages don't bring: PortAudio (microphone) and Qt's X11 cursor library.
missing=()
for lib in libportaudio.so.2 libxcb-cursor.so.0; do
    ldconfig -p | grep -F "$lib" >/dev/null || missing+=("$lib")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "Missing system libraries: ${missing[*]}. On Debian, Ubuntu and Linux Mint:" >&2
    echo "    sudo apt install libportaudio2 libxcb-cursor0" >&2
    exit 1
fi

echo "Installing talk-to-mic into $PREFIX"
mkdir -p "$PREFIX"
[ -x "$PREFIX/venv/bin/python" ] || "$python" -m venv "$PREFIX/venv"
# pytest and PyInstaller are only for development and the Windows build.
grep -vE '^(pytest|pyinstaller)==' "$REPO/requirements.txt" > "$PREFIX/requirements.txt"
"$PREFIX/venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$PREFIX/requirements.txt"
rm -rf "$PREFIX/app"
mkdir -p "$PREFIX/app"
cp -r "$REPO/talk_to_mic" "$PREFIX/app/"
find "$PREFIX/app" -name __pycache__ -prune -exec rm -rf {} +

mkdir -p "$(dirname "$LAUNCHER")"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Starts talk-to-mic (installed by $REPO/packaging/linux/install.sh).
PYTHONPATH="$PREFIX/app" exec "$PREFIX/venv/bin/python" -m talk_to_mic "\$@"
EOF
chmod +x "$LAUNCHER"

mkdir -p "$(dirname "$ICON")"
"$PREFIX/venv/bin/python" "$REPO/packaging/make_icon.py" "$ICON"

desktop_entry() {
    cat <<EOF
[Desktop Entry]
Type=Application
Name=talk-to-mic
Comment=Hold a key, speak, release to type
Exec=$LAUNCHER
Icon=talk-to-mic
Terminal=false
Categories=Utility;Accessibility;
StartupNotify=false
EOF
}
mkdir -p "$(dirname "$DESKTOP_FILE")"
desktop_entry > "$DESKTOP_FILE"

if [ -z "$autostart" ]; then
    if [ -e "$AUTOSTART_FILE" ]; then
        autostart=yes
    elif [ -t 0 ]; then
        read -r -p "Start talk-to-mic when you sign in? [Y/n] " answer
        case "$answer" in [nN]*) autostart=no ;; *) autostart=yes ;; esac
    else
        autostart=no
    fi
fi
if [ "$autostart" = yes ]; then
    mkdir -p "$(dirname "$AUTOSTART_FILE")"
    # The delay lets the panel's system tray start first, so the tray icon has somewhere to dock.
    { desktop_entry; echo "X-GNOME-Autostart-enabled=true"; echo "X-GNOME-Autostart-Delay=5"; } > "$AUTOSTART_FILE"
else
    rm -f "$AUTOSTART_FILE"
fi

echo "Done. Start talk-to-mic from the menu, or run: talk-to-mic"
echo "If an older copy is running, quit it from the tray icon first."

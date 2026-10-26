# talk-to-mic

Hold-to-dictate for Windows and Linux (X11), modelled on Typeless: hold **Right Alt**, speak,
release, and the text is typed into whatever app has focus. Add **Space** while holding to ask
Claude about the selected text (Ask Anything), or **Right Shift** to translate what you say.

1. A keyboard hook detects the hold.
2. The microphone records 16 kHz mono audio while the key is held.
3. [faster-whisper](https://github.com/SYSTRAN/faster-whisper) transcribes it locally on the CPU.
4. Claude cleans it up (removes filler words, applies "no wait, I meant…" corrections, fixes
   punctuation, formats spoken lists), using the focused app's name and window title as a style hint.
5. The text is pasted with Ctrl+V, and your previous clipboard contents are put back.

Audio never leaves the machine; only the transcript (and the app name/window title) goes to Claude,
plus the selected text in Ask Anything. Settings, history and the personal dictionary are stored
locally (see [Where things are](#where-things-are)).

## Requirements

- Windows 10 or 11 (x64), or Linux with an X11 desktop session (Wayland sessions are not
  supported; see [which distributions](#quick-start-linux)).
- A microphone.
- **A CPU is all you need.** Speech recognition runs on the CPU only (int8); talk-to-mic does
  not use a GPU, even if one is installed. Expect a few seconds of processing per dictation with
  the default `small` model on a modern CPU; slower or older machines take longer (use the `base`
  model there).
- Internet access on the first start, to download the speech model (~480 MB for `small`) from
  Hugging Face. After that, dictation works offline unless you use Claude.
- Optional: an [Anthropic API key](https://console.anthropic.com/) for Claude cleanup, Ask
  Anything and Translate. Without one, dictation still works and the raw Whisper transcript is
  pasted; Ask Anything and Translate are unavailable.

## Quick start: Windows

There is no prebuilt download; you build the installer from this repository. In PowerShell:

```powershell
# 1. Tools (once): Python 3.14, Git and Inno Setup 6
winget install Python.Python.3.14
winget install Git.Git
winget install JRSoftware.InnoSetup

# 2. Get the code and its dependencies (open a new PowerShell window first, so the tools are on PATH)
git clone https://github.com/orhunakkan/talk-to-mic.git
cd talk-to-mic
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. Build the installer, then run it
.\build.ps1
.\dist\talk-to-mic-setup-1.1.0.exe     # the version in the name follows talk_to_mic/__init__.py
```

The installer works for your user only (no admin prompt): it installs into
`%LOCALAPPDATA%\Programs\talk-to-mic`, adds a Start menu entry, and can start talk-to-mic when you
sign in. Then continue with [First start](#first-start).

## Quick start: Linux

Needs an X11 session: check that `echo $XDG_SESSION_TYPE` prints `x11` (choose an X11 session at
the login screen if it says `wayland`). On Debian, Ubuntu or Linux Mint:

```bash
# 1. System libraries (PortAudio for the microphone, an X11 library Qt needs) and Git
sudo apt install libportaudio2 libxcb-cursor0 git

# 2. Python 3.14, if your distribution doesn't have it, via uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.14            # adds python3.14 to ~/.local/bin

# 3. Get the code and install talk-to-mic
git clone https://github.com/orhunakkan/talk-to-mic.git
cd talk-to-mic
packaging/linux/install.sh        # asks whether to start talk-to-mic when you sign in
```

The installer works for your user only (no root): it puts a copy of the app and its own Python
environment in `~/.local/share/talk-to-mic`, adds a menu entry and the `talk-to-mic` command, and
can add itself to `~/.config/autostart`. Start it from the menu (or run `talk-to-mic`), then continue
with [First start](#first-start).

**Tested on** Linux Mint 22 (Cinnamon, X11). Other distributions should work if they meet the same
needs, but haven't been tested:

- an X11 session: Ubuntu, Fedora and Debian start GNOME on Wayland by default, so pick the "on Xorg"
  session at the login screen;
- a system tray (Cinnamon, MATE, Xfce and KDE have one; GNOME needs the AppIndicator extension);
- PortAudio and `libxcb-cursor` under your distribution's package names (Fedora:
  `sudo dnf install portaudio xcb-util-cursor`, Arch: `sudo pacman -S portaudio xcb-util-cursor`);
- a Secret Service keyring (GNOME Keyring, KWallet) to save the API key in Settings; without one,
  set `ANTHROPIC_API_KEY` instead.

If it works, or doesn't, on another distribution, please
[open an issue](https://github.com/orhunakkan/talk-to-mic/issues).

Everything works as on Windows, with these differences:

- In terminals (GNOME Terminal, Konsole, kitty, ...) text is pasted with Ctrl+Shift+V, and Ask
  Anything reads the selection with Ctrl+Shift+C, so it works there too.
- While you hold the hotkey, Space (Ask Anything) goes to talk-to-mic even if the desktop uses
  Alt+Space itself (Cinnamon opens the window menu with it).
- F13-F24, Scroll Lock and Pause also reach the focused app when used as the hotkey (apps ignore
  them); Caps Lock is switched back after each hold.
- The tray icon needs a desktop with a system tray: Cinnamon, MATE, Xfce and KDE have one; on GNOME,
  install the AppIndicator extension.

## First start

A microphone icon appears in the tray: grey = loading, blue = ready, red = listening,
amber = transcribing. The first start downloads the speech model (it stays grey until that's done)
and opens the talk-to-mic window.

**Claude API key (optional).** Click the tray icon > Settings, paste your key under **Claude** and
click Save. It is stored in Windows Credential Manager on Windows, and in the system keyring on
Linux (GNOME Keyring on Cinnamon and GNOME, KWallet on KDE). The `ANTHROPIC_API_KEY` environment
variable also works and takes precedence:

```powershell
setx ANTHROPIC_API_KEY "sk-ant-..."                                 # Windows
```

```bash
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.profile          # Linux; sign out and in again
```

**Microphone.** talk-to-mic uses the system's default input device. If the first dictations come
out empty, see [Troubleshooting](#troubleshooting).

If you also use Typeless, quit it first (or change one app's hotkey): both use Right Alt by default.

## Using it

- Hold Right Alt for at least 0.25 s while speaking; release to paste.
- Quick taps are ignored, and Right Alt used in a combination (AltGr characters such as `@`
  on some keyboard layouts, or shortcuts) cancels the dictation, so AltGr keeps working.
- Releasing Right Alt after a dictation does not open the focused window's menu bar.
- **Ask Anything**: hold Right Alt, press Space, speak an instruction, release. With text
  selected, edits ("make this more formal", "translate to German") replace the selection; with
  nothing selected, requests to write something ("draft a polite no") are typed at the cursor;
  questions ("what does this regex do?") are answered in a card with Copy and Insert buttons.
- **Translate**: hold Right Alt, press Right Shift, speak, release; the translation into the
  language set in Settings is pasted.
- **Left Ctrl + Right Shift + V** pastes the last result again.
- While you dictate, a small bar near the bottom of the screen shows the mode and your
  microphone level, then a spinner while the text is processed. It never takes focus.
- Click the tray icon (or start talk-to-mic again from the Start menu or app menu) to open the window:
  - **Home**: words dictated, time saved and recent dictations.
  - **History**: search, copy or delete past dictations (stored only on this computer).
  - **Dictionary**: names and terms to always spell your way, with what they may sound like
    ("kubectl" sounds like "cube cuttle"); used by Whisper and by Claude.
  - **Settings**: everything below, applied as soon as you save.
- Closing the window keeps talk-to-mic running in the tray. Tray menu: open the window, copy the
  last result, turn Claude cleanup on/off, open `config.toml`, open the log, quit.

## Settings

Use the Settings page, or edit `config.toml` (created on first run with comments for every option;
see [Where things are](#where-things-are)) and restart the app. Notable ones:

| Setting | Default | Notes |
|---|---|---|
| `hotkey.key` | `right_alt` | also `right_ctrl`, `caps_lock`, `scroll_lock`, `pause`, `f13`-`f24` |
| `hotkey.ask_combo` | `space` | key pressed during the hold for Ask Anything; `""` turns it off |
| `hotkey.translate_combo` | `right_shift` | key pressed during the hold for Translate; `""` turns it off |
| `hotkey.paste_last` | `left_ctrl+right_shift+v` | `""` turns it off |
| `audio.input_device` | `""` | part of a microphone's name; empty = the system's default input |
| `translation.target_language` | `English` | any language name Claude understands |
| `history.enabled` / `keep_audio` / `retention_days` | `true` / `false` / `0` | `0` days keeps history forever |
| `ui.floating_bar` | `true` | the listening/processing bar |
| `transcription.model` | `small` | `base` is faster on slower CPUs; `large-v3-turbo` is more accurate but much slower |
| `transcription.language` | auto | set e.g. `"en"` or `"de"` to skip language detection |
| `transcription.vocabulary` | `[]` | names and jargon to spell your way (the Dictionary page is easier) |
| `cleanup.model` | `claude-haiku-5-5` | fast and cheap (~$0.0001 per dictation); `claude-opus-5-5` is the most capable but ~40x the cost |
| `cleanup.effort` | `low` | higher effort is slower and rarely needed here |
| `cleanup.send_app_context` | `true` | set `false` to stop sending the window title to Claude |

With `claude-opus-5-5` (or another Opus/Sonnet 5.5-era model) the request opts into Claude's server-side refusal fallback
(`fallbacks: "default"`), so a rare safety decline is retried on another model instead of failing.
If Claude is unreachable, declines, or no key is set, the raw transcript is pasted instead.

## Where things are

| | Windows | Linux |
|---|---|---|
| Settings (`config.toml`), history (`talk-to-mic.db`), log (`talk-to-mic.log`) | `%APPDATA%\talk-to-mic\` | `~/.config/talk-to-mic/` |
| Installed app | `%LOCALAPPDATA%\Programs\talk-to-mic` | `~/.local/share/talk-to-mic`, launcher `~/.local/bin/talk-to-mic` |
| Start at sign-in | option in the installer | `~/.config/autostart/talk-to-mic.desktop` |
| Speech models | Hugging Face cache in `%USERPROFILE%\.cache\huggingface` | `~/.cache/huggingface` |

**Updating.** Quit talk-to-mic from the tray, run `git pull` in your clone, then on Windows rebuild
and run the new installer (`.\build.ps1`), or on Linux run `packaging/linux/install.sh` again.

**Uninstalling.** Windows: Settings > Apps. Linux: `packaging/linux/install.sh --uninstall`. Both keep
your settings and history; delete the settings folder above to remove them too.

## Troubleshooting

The log (tray > Open log) says what happened to each dictation. Start talk-to-mic with `-v` to also
log the transcripts.

- **Nothing happens when I hold Right Alt.** Check that the tray icon is blue (grey means the
  speech model is still loading). Another app may use the same key (Typeless, or a launcher bound
  to Right Alt). On Linux, the session must be X11. On Windows, apps running as administrator
  ignore talk-to-mic unless it runs as administrator too.
- **The log says "Recording was silent", or nothing is pasted.** talk-to-mic recorded the wrong
  microphone. Pick the right one in Settings > Microphone, or change the system's default input
  (Windows: Settings > System > Sound > Input; Linux: Sound settings > Input). Many laptops default
  to the headset jack even with nothing plugged in.
- **The text is wrong or misspelled.** Add names and jargon on the Dictionary page, set
  `transcription.language` if you only speak one language, or try a larger model.
- **Claude cleanup, Ask Anything or Translate don't work.** Settings > Claude shows whether a key
  is connected; the log shows why a request failed. Without Claude the raw transcript is pasted.
- **There is no tray icon (Linux).** Your desktop needs a system tray (see the Linux notes above).
  The window still opens when you start talk-to-mic again from the menu.
- **Starting talk-to-mic again only opens its window.** Only one copy runs at a time; quit the
  running one from the tray first (for example, to run from source).
- **A paste didn't arrive.** The text stays on the clipboard, so you can paste it by hand, and
  Left Ctrl + Right Shift + V pastes the last result again.

## Limitations

- Transcription is CPU-only; there is no GPU (CUDA, ROCm or DirectML) acceleration.
- Windows blocks hooks and simulated keystrokes into apps running as administrator unless
  talk-to-mic also runs as administrator. If a paste is blocked, the text is left on the clipboard.
- On Linux only X11 sessions are supported; apps sandboxed away from X11 (some Flatpaks) may not
  report their name, so Claude gets only the window title as a style hint.
- Only plain text is pasted (no rich text).
- **Right Alt + Space starts Ask Anything.** In 1.0 it cancelled the dictation like any other
  AltGr combination. If your keyboard layout types a character with AltGr + Space, set Ask
  Anything to Off (or another key) in Settings.
- Ask Anything reads the selection by sending Ctrl+C. On Windows this is skipped in terminals,
  where Ctrl+C interrupts (on Linux, terminals get Ctrl+Shift+C). In editors that copy the whole line
  when nothing is selected (VS Code), that line is treated as the selection.

## Development

Needs Python 3.14. If you set this up with a coding agent, point it at [AGENTS.md](AGENTS.md).

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\pythonw.exe -m talk_to_mic                         # run from source (quit the installed copy first)
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m talk_to_mic --transcribe sample.wav   # check the model + Claude settings
.\.venv\Scripts\python.exe -m talk_to_mic -v                         # log transcripts to the log file
```

On Linux:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m talk_to_mic                          # run from source (quit the installed copy first)
.venv/bin/python -m pytest
.venv/bin/python -m talk_to_mic --transcribe sample.wav  # scripts/make-sample-wav.sh writes one (needs espeak-ng)
```

The OS-specific code is in [talk_to_mic/win32.py](talk_to_mic/win32.py),
[paste.py](talk_to_mic/paste.py), [credentials.py](talk_to_mic/credentials.py) and
[hotkey_win32.py](talk_to_mic/hotkey_win32.py) on Windows, and in [x11.py](talk_to_mic/x11.py),
[paste_x11.py](talk_to_mic/paste_x11.py), [credentials_linux.py](talk_to_mic/credentials_linux.py) and
[hotkey_x11.py](talk_to_mic/hotkey_x11.py) on Linux. [native.py](talk_to_mic/native.py) picks the
right set; everything else is shared.

See [docs/typeless-analysis.md](docs/typeless-analysis.md) for how Typeless itself works, and
[PLAN.md](PLAN.md) for the 1.1 feature plan.

### Building the Windows installer

`.\build.ps1` (needs the `.venv` above and Inno Setup 6). PyInstaller bundles Python and the
dependencies into `dist\talk-to-mic\talk-to-mic.exe` ([packaging/talk-to-mic.spec](packaging/talk-to-mic.spec)),
then Inno Setup packs that folder into `dist\talk-to-mic-setup-<version>.exe`
([packaging/installer.iss](packaging/installer.iss)). Bump `__version__` in
[talk_to_mic/__init__.py](talk_to_mic/__init__.py) for a new release. On Linux,
[packaging/linux/install.sh](packaging/linux/install.sh) installs straight from the clone; there is
no package to build.

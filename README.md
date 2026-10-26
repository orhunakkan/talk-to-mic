# talk-to-mic

Hold-to-dictate for Windows, modelled on Typeless: hold **Right Alt**, speak, release, and the
text is typed into whatever app has focus. Add **Space** while holding to ask Claude about the
selected text (Ask Anything), or **Right Shift** to translate what you say.

1. A low-level keyboard hook detects the hold.
2. The microphone records 16 kHz mono audio while the key is held.
3. [faster-whisper](https://github.com/SYSTRAN/faster-whisper) transcribes it locally on the CPU.
4. Claude cleans it up (removes filler words, applies "no wait, I meant…" corrections, fixes
   punctuation, formats spoken lists), using the focused app's name and window title as a style hint.
5. The text is pasted with Ctrl+V, and your previous clipboard contents are put back.

Audio never leaves the machine; only the transcript (and the app name/window title) goes to Claude,
plus the selected text in Ask Anything. History and the personal dictionary are stored locally in
`%APPDATA%\talk-to-mic\talk-to-mic.db`.

## Requirements

- Windows 10 or 11 (x64).
- A microphone.
- **A CPU is all you need.** Speech recognition runs on the CPU only (int8); talk-to-mic does
  not use a GPU, even if one is installed. Expect a few seconds of processing per dictation with
  the default `small` model on a modern desktop CPU; slower or older machines take longer (use
  the `base` model there).
- Optional: an [Anthropic API key](https://console.anthropic.com/) for Claude cleanup, Ask
  Anything and Translate. Without one, the raw Whisper transcript is pasted.

## Install

There is no prebuilt download; build the installer yourself (see
[Building the installer](#building-the-installer)), then run
`dist\talk-to-mic-setup-<version>.exe`. It installs for your user only (no admin prompt) into
`%LOCALAPPDATA%\Programs\talk-to-mic`, adds a Start menu entry, and can start talk-to-mic when you
sign in. Uninstall from Settings > Apps; your settings in `%APPDATA%\talk-to-mic` are kept.
Installing a newer build over an older one upgrades it.

For Claude cleanup, Ask Anything and Translate, open talk-to-mic (click the tray icon) >
Settings and paste your API key; it is stored in Windows Credential Manager. Without a key the
raw transcript is pasted. The `ANTHROPIC_API_KEY` environment variable still works and takes
precedence:

```powershell
setx ANTHROPIC_API_KEY "sk-ant-..."
```

A microphone icon appears in the tray: grey = loading, blue = ready, red = listening,
amber = transcribing. The first start downloads the Whisper model (~480 MB for `small`) and opens
the talk-to-mic window.

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
- Click the tray icon (or start talk-to-mic again from the Start menu) to open the window:
  - **Home**: words dictated, time saved and recent dictations.
  - **History**: search, copy or delete past dictations (stored only on this PC).
  - **Dictionary**: names and terms to always spell your way, with what they may sound like
    ("kubectl" sounds like "cube cuttle"); used by Whisper and by Claude.
  - **Settings**: everything below, applied as soon as you save.
- Tray menu: open the window, copy the last result, turn Claude cleanup on/off, open
  `config.toml`, open the log, quit.

## Settings

Use the Settings page, or edit `%APPDATA%\talk-to-mic\config.toml` (created on first run with
comments for every option) and restart the app. Notable ones:

| Setting | Default | Notes |
|---|---|---|
| `hotkey.key` | `right_alt` | also `right_ctrl`, `caps_lock`, `scroll_lock`, `pause`, `f13`-`f24` |
| `hotkey.ask_combo` | `space` | key pressed during the hold for Ask Anything; `""` turns it off |
| `hotkey.translate_combo` | `right_shift` | key pressed during the hold for Translate; `""` turns it off |
| `hotkey.paste_last` | `left_ctrl+right_shift+v` | `""` turns it off |
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

## Limitations

- Transcription is CPU-only; there is no GPU (CUDA, ROCm or DirectML) acceleration.
- Windows blocks hooks and simulated keystrokes into apps running as administrator unless
  talk-to-mic also runs as administrator. If a paste is blocked, the text is left on the clipboard.
- Only plain text is pasted (no rich text).
- **Right Alt + Space starts Ask Anything.** In 1.0 it cancelled the dictation like any other
  AltGr combination. If your keyboard layout types a character with AltGr + Space, set Ask
  Anything to Off (or another key) in Settings.
- Ask Anything reads the selection by sending Ctrl+C. This is skipped in terminals, where Ctrl+C
  interrupts; in editors that copy the whole line when nothing is selected (VS Code), that line is
  treated as the selection.

## Development

Needs Python 3.14. If you set this up with a coding agent, point it at [AGENTS.md](AGENTS.md).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\pythonw.exe -m talk_to_mic                         # run from source (quit the installed copy first)
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m talk_to_mic --transcribe sample.wav   # check the model + Claude settings
.\.venv\Scripts\python.exe -m talk_to_mic -v                         # log transcripts to the log file
```

See [docs/typeless-analysis.md](docs/typeless-analysis.md) for how Typeless itself works, and
[PLAN.md](PLAN.md) for the 1.1 feature plan.

### Building the installer

```powershell
winget install JRSoftware.InnoSetup   # once
.\build.ps1
```

PyInstaller bundles Python and the dependencies into `dist\talk-to-mic\talk-to-mic.exe`
([packaging/talk-to-mic.spec](packaging/talk-to-mic.spec)), then Inno Setup packs that folder into
`dist\talk-to-mic-setup-<version>.exe` ([packaging/installer.iss](packaging/installer.iss)). Bump
`__version__` in [talk_to_mic/__init__.py](talk_to_mic/__init__.py) for a new release.

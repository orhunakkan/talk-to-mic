# Notes for coding agents

talk-to-mic is a tray app for Windows and Linux X11 sessions (Python 3.14, PySide6). Read
[README.md](README.md) for what it does; this file covers setting it up and checking it works.
The commands below are for Windows; see [Linux](#linux) for the equivalents.

## Setup

Run in PowerShell from the repo root:

```powershell
py -3.14 -m venv .venv            # or: python -m venv .venv, if python is 3.14
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

- All tests should pass in a few seconds. They need no network, microphone or API key.
- Speech recognition runs on the CPU only. Don't install CUDA/torch or try to set up a GPU.
- The pins in `requirements.txt` are exact; other Python versions may not have matching wheels.

## Checking the full pipeline

The app is driven by holding a physical key. The keyboard hook ignores injected keystrokes by
design, so **an agent cannot test dictation by simulating key presses**; leave that to the user.
Test the transcribe → cleanup path from a WAV file instead:

```powershell
$env:APPDATA = "$PWD\.scratch-appdata"        # keep the user's real settings and history untouched
.\scripts\make-sample-wav.ps1 -Path .scratch-appdata\sample.wav
.\.venv\Scripts\python.exe -m talk_to_mic --transcribe .scratch-appdata\sample.wav
```

- The first run downloads the Whisper model (~480 MB for `small`) from Hugging Face, so it needs
  internet access and takes a while. Later runs use the cache.
- Expect a `raw:` line with the sentence. The `cleaned:` line needs an Anthropic API key, either
  in `ANTHROPIC_API_KEY` or saved in the app's Settings (Windows Credential Manager). Never ask
  the user to paste their key into the chat, and never write it to a file in the repo.
- `.scratch-appdata/` is git-ignored. Delete it when done.

## Running the app

```powershell
.\.venv\Scripts\pythonw.exe -m talk_to_mic
```

- Only one copy runs at a time. If an installed copy is running, a second launch exits with
  code 1 and opens the running copy's window instead. Ask the user to quit it from the tray first.
- Settings, history and logs live in `%APPDATA%\talk-to-mic\`. Set `APPDATA` to a scratch folder,
  as above, to avoid changing the user's real data.

## Building the installer

Needs Inno Setup 6 (`winget install JRSoftware.InnoSetup`), then `.\build.ps1`. The output is
`dist\talk-to-mic-setup-<version>.exe`. `build/` and `dist/` are git-ignored.

## Linux

Needs an X11 session, Python 3.14 (`uv python install 3.14` if the distribution lacks it) and
`sudo apt install libportaudio2 libxcb-cursor0` (plus `espeak-ng` for the sample WAV).

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
export APPDATA="$PWD/.scratch-appdata"          # also honoured on Linux: keeps ~/.config/talk-to-mic untouched
scripts/make-sample-wav.sh .scratch-appdata/sample.wav
.venv/bin/python -m talk_to_mic --transcribe .scratch-appdata/sample.wav
```

- The tests need no display, network, microphone or API key.
- The keyboard hook ignores keys injected with XTEST (xdotool), just as the Windows hook ignores
  injected keystrokes, so leave dictation tests to the user.
- Run the app from source with `.venv/bin/python -m talk_to_mic`. The installed copy
  (`packaging/linux/install.sh`) usually starts at sign-in, and a second copy exits with code 1, so
  ask the user to quit it from the tray first.
- Settings, history and logs live in `~/.config/talk-to-mic/` unless `APPDATA` is set. The API
  key is kept in the Secret Service keyring (Windows: Credential Manager).

## Conventions

- Keep `requirements.txt` pinned to exact versions.
- New config keys need a default in both `DEFAULT_CONFIG_TEXT` and the dataclasses in
  `talk_to_mic/config.py`. A test checks that the two match.
- Don't commit secrets, `config.toml`, `*.db`, `*.wav` or logs. `.gitignore` covers them.

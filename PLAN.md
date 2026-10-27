# Plan: talk-to-mic 1.1.0

Status: **implemented** · 2026-10-21 · `dist\talk-to-mic-setup-1.1.0.exe` built (87 MB). The
[Implementation notes](#implementation-notes) at the end list where the build differs from this plan.

## Goal

Add Ask Anything, Translate, history, a personal dictionary and a basic desktop UI. Every current
feature has to keep working exactly as it does in 1.0.0. The app stays a per-user Windows
tray app built with PyInstaller and Inno Setup.

Decisions:

| Question | Decision |
|---|---|
| Scope | History + paste-last, personal dictionary, Ask Anything + Translate, floating bar + stats |
| UI toolkit | PySide6 (Qt), using the `PySide6-Essentials` wheel only (QtCore/QtGui/QtWidgets) |
| Ask Anything result | Edits replace the selection, generated text is inserted at the cursor, answers appear in a card |
| API key | Entered in Settings and stored in Windows Credential Manager. `ANTHROPIC_API_KEY` still works and takes precedence |

## Feature scope

| Feature | talk-to-mic 1.0.0 | In this plan |
|---|---|---|
| Dictate (hold Right Alt) | ✅ done | keep as is |
| **Ask Anything** (Right Alt + Space): speak a command about the selected text, or a question | ❌ | ✅ Phase 5 |
| **Translate** (Right Alt + Right Shift) | ❌ | ✅ Phase 5 |
| **Paste last transcript** (Left Ctrl + Right Shift + V) | ❌ | ✅ Phase 2 |
| **History**: local SQLite with the text, app context and optional audio | ❌ (log file only) | ✅ Phase 2 |
| **Personal dictionary** (manual and auto-learned) | ⚠️ `transcription.vocabulary` in TOML, sent to Whisper only | ✅ Phase 4 (manual entries + "add from history"; auto-learn deferred) |
| **Floating bar** while recording | ⚠️ tray icon changes colour | ✅ Phase 6 |
| **Hub** window (home/stats, history, dictionary, settings) | ⚠️ edit `config.toml` by hand | ✅ Phase 3 |
| API key in the OS credential store | ⚠️ env var via `setx` | ✅ Phase 3 |
| Mute background audio while recording | ❌ | ⏭ later |
| Rich text insert (lists, bold) | ❌ plain text | ⏭ later |
| Rich context (URL, text around the cursor, visible text) | ⚠️ app name and window title only | ⏭ later |
| Microphone picker | ⚠️ substring in TOML | ✅ Phase 3 (dropdown in Settings) |

## Guardrails: current features must not change

1. **All 16 existing tests stay untouched and keep passing.** New behaviour gets new tests.
2. **The 1.0.0 `config.toml` keeps loading.** Every new setting has a default, and missing keys fall
   back to it, which `parse_config` already does. `DEFAULT_CONFIG_TEXT` and the dataclasses are
   updated together, which `test_default_config_text_matches_dataclass_defaults` checks.
3. **Dictation flow keeps its behaviour:** the same hotkey, the 250 ms minimum hold, AltGr
   cancelling, the Alt menu mask, clipboard save and restore, the Win+V exclusion, the
   raw-transcript fallback and single-instance mode.
4. **The tray keeps its four colours, tooltip and menu items** (status, Clean up with Claude, Open
   settings, Open log, Quit). New items are only added.
5. **Every new combo can be turned off.** Setting `ask_combo`, `translate_combo` or `paste_last` to
   `""` makes the hotkey behave exactly as it does in 1.0.0.
   > ⚠️ One deliberate change: Right Alt + Space currently *cancels* the dictation, the same way any
   > AltGr combination does. With the default `ask_combo = "space"` it starts Ask Anything
   > instead. If your keyboard layout uses AltGr+Space for a character, set `ask_combo = ""`.
6. **ANTHROPIC_API_KEY keeps precedence** over the stored credential.

## Architecture changes

```
main thread      QApplication event loop: tray, hub, floating bar, card   (was: pystray loop)
keyboard-hook    unchanged; HoldDetector also reports combos               → actions queue
controller       unchanged role; records a Job with its mode
worker           transcribe → (dictate | ask | translate) → paste/card → history
UiBridge(QObject) Qt signals that the worker and controller emit; Qt delivers them on the main thread
```

- **pystray is replaced by `QSystemTrayIcon`**, because two GUI message loops can't share the main
  thread. `tray.py` keeps the same `Tray` interface (`set_status`, `notify`, `run`, `stop`) and the
  same icon drawing. The icon is drawn with Pillow and converted to a `QIcon`, so it looks
  identical. Remove `pystray` from requirements and the `.spec` file.
- **`Job` gains `mode: Mode` (`DICTATE | ASK | TRANSLATE`), `selection: str` and `hwnd`.** This
  `Mode` is the dictation mode. The existing `hotkey.Mode` (PASS/ALT_MASK/BLOCK) gets a different
  name to avoid a clash: rename the new enum to `Intent`, not the old one.
- **One SQLite file** at `%APPDATA%\talk-to-mic\talk-to-mic.db` (stdlib `sqlite3`, WAL mode) holds
  the `history` (with FTS5 for search) and `dictionary` tables. The worker thread writes, and the UI
  reads through its own connection.
- **`settings_store.py`** writes `config.toml` with `tomlkit`, so the user's comments survive, then
  reloads `Config` and applies it live. Hotkeys, the cleanup model, beeps and the floating bar take
  effect immediately. A change to the Whisper model or engine reloads the transcriber in the worker,
  and the tray shows "Loading" in the meantime.

### New and changed files

| File | Change |
|---|---|
| `talk_to_mic/config.py` | new sections and keys (below) |
| `talk_to_mic/hotkey.py` | combos during a hold (Ask, Translate), the paste-last chord, `Intent` |
| `talk_to_mic/app.py` | runs on Qt, routes jobs by mode, records history, keeps the last result |
| `talk_to_mic/tray.py` | rewritten on `QSystemTrayIcon`; new menu items: Open talk-to-mic…, Paste last transcript |
| `talk_to_mic/cleanup.py` | dictionary block in the prompt; `ask()` and `translate()` alongside `clean()` |
| `talk_to_mic/transcriber.py` | Whisper prompt built from config vocabulary + dictionary (≤ 224 tokens) |
| `talk_to_mic/audio.py` | `Recorder.level` (RMS of the last block) for the floating bar meter |
| `talk_to_mic/paste.py` | `copy_selection()` (Ctrl+C plus a clipboard sequence-number check, then restore) |
| `talk_to_mic/credentials.py` **new** | `win32cred` read/write/delete for target `talk-to-mic/anthropic` |
| `talk_to_mic/store.py` **new** | SQLite schema and migrations, history CRUD, search, stats, dictionary CRUD |
| `talk_to_mic/settings_store.py` **new** | tomlkit round-trip save + live apply |
| `talk_to_mic/ui/bridge.py` **new** | `UiBridge` signals |
| `talk_to_mic/ui/hub.py` **new** | main window: Home, History, Dictionary, Settings |
| `talk_to_mic/ui/floating_bar.py` **new** | recording pill |
| `talk_to_mic/ui/card.py` **new** | Ask Anything answer card |
| `packaging/talk-to-mic.spec` | drop pystray hidden import, add PySide6, exclude unused Qt modules |
| `requirements.txt` | `+ PySide6-Essentials`, `+ tomlkit`, `- pystray` (pin exact versions as now) |

### New settings (all optional, defaults shown)

```toml
[hotkey]
key = "right_alt"                 # unchanged
min_hold_ms = 250                 # unchanged
# Press while holding the dictation key to switch mode. "" disables.
ask_combo = "space"
translate_combo = "right_shift"
# Chord that pastes the last result again. "" disables.
paste_last = "left_ctrl+right_shift+v"

[translation]
# Language that Translate mode writes in, e.g. "English", "Turkish".
target_language = "English"

[history]
enabled = true
keep_audio = false                # store 16 kHz audio (~32 KB/s) with each entry
retention_days = 0                # 0 = keep forever

[ui]
floating_bar = true
typing_wpm = 40                   # used for the "time saved" statistic
```

## Phases

Each phase ends with passing tests and a working app, so if the day runs short the last good phase
still ships. Sizes are relative: S < M < L.

### Phase 1: Move to Qt with no visible change (M)
- Replace pystray with `QSystemTrayIcon` and run `QApplication` on the main thread. Worker and
  controller threads are started from `App._setup` as they are now.
- Add `UiBridge`. Every `tray.set_status`/`notify` call from a background thread goes through a
  signal.
- Update the spec and requirements, then build the exe.
- **Done when:** the regression checklist below passes on the built exe, and the 16 tests pass.

### Phase 2: History and paste-last (M)
- `store.py`: `history(id, created_at, mode, process_name, window_title, raw_text, final_text,
  duration_s, word_count, audio BLOB NULL)`, an FTS5 index on raw and final text, and a
  `schema_version` table.
- The worker writes one row per successful dictation when `history.enabled`. Retention is pruned
  at startup. The last result is also kept in memory, so paste-last works when history is off.
- Hotkey chord `left_ctrl+right_shift+v`: the hook suppresses V and emits `Action.PASTE_LAST`. The
  controller **waits until Ctrl and Shift are physically released** (polls `GetAsyncKeyState` for up
  to 1 s) before calling `paste_text`, so the target app receives Ctrl+V and not Ctrl+Shift+V.
- Add a tray item "Paste last transcript".
- **Tests:** store round-trip, search, retention pruning, the chord detector (suppression,
  injected events ignored, no effect while not chorded).

### Phase 3: Hub window and API key (L)
- `ui/hub.py`, a single `QMainWindow` with a sidebar. It opens from the tray menu "Open
  talk-to-mic…" and by double-clicking the tray icon. On first run with no Claude key, it opens on
  Settings.
- **History tab:** search box, list, detail pane (final text, raw text, app, time), and Copy,
  Delete, Clear all and Add-to-dictionary buttons. Add-to-dictionary takes the selected word.
- **Settings tab:** hotkeys (dropdowns for key/combos), microphone (dropdown from
  `sounddevice.query_devices()`), Whisper engine/model/language, cleanup on/off, model, effort,
  send-app-context, translation target, history on/off, keep audio, retention, floating bar, beeps,
  and a **Claude API key** field that is masked, with Save/Test/Remove buttons. The Test button
  sends a one-token request. If the env var is set, the field is disabled and reads "Using
  ANTHROPIC_API_KEY". There is also an "Open config.toml" link.
- `credentials.py` + `Cleaner`: the key comes from the env var, otherwise from Credential Manager,
  otherwise cleanup is disabled as it is today. Saving a key rebuilds the client without a restart.
- **Tests:** `settings_store` keeps comments and unknown keys, a saved config reloads to the same
  `Config`, and credential precedence works (with `win32cred` mocked).

### Phase 4: Personal dictionary (S–M)
- `dictionary(id, term, sounds_like TEXT NULL, created_at, source)`, where source is `manual`,
  `history` or `config`. Example: term `kubectl`, sounds like `cube cuttle, kube control`.
- The Dictionary tab lists the entries and has add/edit/delete. Entries from
  `transcription.vocabulary` are shown read-only and marked "from config.toml".
- **Whisper:** `initial_prompt` = config vocabulary + dictionary terms, newest first, trimmed to
  about 224 tokens.
- **Claude:** the user message gets a `<dictionary>` block (`term ← sounds like`), and the system
  prompt gets one rule: "Use these spellings; replace a sounds-like phrase with its term only when
  it clearly refers to it."
- **Tests:** prompt building and trimming, and that `build_user_message` has no dictionary block
  when the dictionary is empty. Existing assertions stay unchanged.

### Phase 5: Ask Anything and Translate (L)
- **Hotkeys:** while the dictation key is held, pressing `ask_combo` (Space, which is suppressed) or
  `translate_combo` (Right Shift, which passes through as a modifier) sets the hold's `Intent`
  instead of cancelling. Recording has already started on key-down, so nothing is lost. Any other
  key still cancels as AltGr does today. The floating bar shows the current mode.
- **Selection capture** (Ask only, after release so Alt is no longer down): `paste.copy_selection()`
  saves the clipboard, sends Ctrl+C, waits up to 300 ms for `GetClipboardSequenceNumber` to change,
  reads the text and restores the clipboard. This is **skipped for terminals**
  (`WindowsTerminal.exe`, `conhost.exe`, `cmd.exe`, `powershell.exe`, `pwsh.exe`, `wt.exe`), where
  Ctrl+C would send an interrupt.
- **Claude `ask()`:** the input is the spoken command, the selection (which may be empty) and the
  app context. The model is forced to call a `respond` tool with
  `{kind: "replace" | "insert" | "answer", text}`:
  - `replace`: an edit of the selection. It is pasted, and the selection, which is still active,
    gets replaced.
  - `insert`: generated text with no selection (for example "write a short reply declining the
    meeting"). It is pasted at the cursor.
  - `answer`: a question about the selection or general knowledge. It is shown in the card, and
    nothing is pasted.
- **`translate()`:** clean up and translate into `translation.target_language`, then paste. If
  Claude is unavailable, the raw transcript is pasted and a toast says it was not translated, so
  the dictation is never lost. Ask with no Claude shows an error card, and nothing is pasted.
- **Card** (`ui/card.py`): a frameless, always-on-top window near the cursor's monitor. It shows
  the spoken question and the answer as selectable text, with **Copy**, **Insert** and **Close**
  buttons. Esc closes it. Insert re-activates the stored target `hwnd`
  (`SetForegroundWindow` plus an `AttachThreadInput` fallback), then pastes. The card is shown
  without activation, so typing in the target app continues.
- History records the mode, and the History tab can filter by mode.
- **Tests:** combo detection (Space or Right Shift sets the intent, Left Shift still does nothing,
  Q still cancels, combos disabled = 1.0.0 behaviour), `ask()` parsing each `kind` and its
  fallbacks with a fake client, terminal skip in `copy_selection`.

### Phase 6: Floating bar and stats (M)
- **Floating bar** (`ui/floating_bar.py`): a pill of about 220×44 px, bottom-centre of the monitor
  that holds the foreground window, 80 px above the taskbar. It uses
  `WindowStaysOnTopHint | FramelessWindowHint | Tool | WindowDoesNotAcceptFocus` plus
  `WS_EX_NOACTIVATE | WS_EX_TRANSPARENT` (click-through), so **focus never leaves the target app**.
  It shows the mode label and a level meter driven by `Recorder.level` at 30 Hz while listening,
  then a spinner with "Transcribing…". It hides when the dictation is done, cancelled, or after a
  toast for an error. It can be turned off with `ui.floating_bar`.
- **Home tab:** words dictated, number of dictations, average speaking WPM, and time saved
  (`words / typing_wpm − audio minutes`) for Today, 7 days and All time, plus the 5 most recent
  entries. All values come from `store.stats()`.
- **Tests:** `stats()` arithmetic on fixed rows, and the placement maths for the bar position.

### Phase 7: Release (S)
- Bump `__version__` to `1.1.0`, update the README (new hotkeys, UI, the Right Alt + Space note,
  privacy of history/audio).
- Run `.\build.ps1`, install over 1.0.0 and run the full regression checklist plus the new-feature
  checks on the installed exe.
- Check the installer size. PySide6-Essentials should add about 60–80 MB. If it is much more, add
  excludes in the spec (QtNetwork, QtPdf, translations, software OpenGL).

## Basic UI

```
Hub window (≈ 900×600)
┌────────────┬──────────────────────────────────────────────────────────┐
│ ● Home     │  Today          7 days          All time                 │
│   History  │  1,240 words    8,911 words     52,300 words             │
│ Dictionary │  31 min saved   3 h 40 min      21 h                     │
│   Settings │  ─────────────────────────────────────────────────────   │
│            │  Recent                                                  │
│            │  10:42  Slack     Dictate   "Sounds good, I'll send…"    │
│            │  10:31  Outlook   Ask       "Make this more formal"      │
│ v1.1.0     │  10:12  Chrome    Translate "Merhaba, toplantı…"         │
└────────────┴──────────────────────────────────────────────────────────┘

History: [search……………] [All modes ▾]      Dictionary: [+ Add]
┌─────────────────────────┬────────────┐   term        sounds like      source
│ 10:42 Slack  Sounds go… │ Final:     │   kubectl     cube cuttle      manual
│ 10:31 Outlook Make thi… │ …          │   Orhun       or hun           history
│ …                       │ Raw: …     │   Anthropic   —                config.toml
│                         │ [Copy][Del]│
└─────────────────────────┴────────────┘

Floating bar (no focus, click-through)     Ask Anything card
   ╭──────────────────────────╮            ╭───────────────────────────────╮
   │ ● Dictate  ▁▃▅▇▅▃▁▂▅     │            │ "What does this regex do?"    │
   ╰──────────────────────────╯            │ It matches ISO dates like…    │
   ╭──────────────────────────╮            │                               │
   │ ◌ Transcribing…          │            │        [Copy] [Insert] [Close]│
   ╰──────────────────────────╯            ╰───────────────────────────────╯

Tray menu
  Ready  (hold right_alt)
  Open talk-to-mic…               ← new
  Paste last transcript           ← new
  ✓ Clean up with Claude
  ───
  Open settings
  Open log
  Quit
```

Style: native Qt widgets with the Fusion style, following the system's light or dark theme. The
accent is the tray's "ready" blue `#3478F6`, red for listening and amber for processing, the same
colours as the tray icon.

## Regression checklist

Results from 2026-10-21. "Verified" means it was tested on this machine. "Needs a person" means it
needs physical key presses, which software can't simulate here: the hook ignores injected keys by
design.

- [x] Hold Right Alt, speak, release: the hold logic is covered by the 8 original detector tests (unchanged, passing) and a hook-callback test. The transcribe → clean → paste pipeline was verified with real speech audio, Whisper and Claude. Paste into a real window was verified live. **Needs a person:** the physical key press.
- [x] Taps shorter than 250 ms and AltGr+Q: original tests unchanged and passing. With the Ask Anything and Translate combos turned off, Space cancels exactly as in 1.0 (new test).
- [x] Clipboard text is restored after a paste and after reading the selection (verified live). The Win+V exclusion code is unchanged. Image clipboard formats were not retested (that code is unchanged).
- [x] Without a key, the raw transcript is pasted (original test passing). The startup toast text now also mentions Settings.
- [ ] Admin-elevated target apps: code path unchanged, not retested.
- [x] Tray: same colours, tooltip and menu items, now drawn with Qt from the same Pillow drawing. The built exe started and loaded the model. **Needs a person:** a visual check of the colour changes while dictating.
- [x] A second launch exits with code 1 and now also shows the running app's window (verified with the built exe). `--transcribe` was verified on a generated speech file.
- [ ] Installing 1.1.0 over 1.0.0: left for a manual check. The installer script only changed its version. A copy of a real 1.0.0 `config.toml` loads and saves correctly (comments kept, new sections added at the end).
- [x] An unmodified 1.0.0 `config.toml` loads with no warnings (a real user file and a test fixture).

## Risks

| Risk | Mitigation |
|---|---|
| Qt and the low-level hook thread interfere | The hook keeps its own thread and message loop as it does now. Qt only runs on the main thread, and nothing calls Qt from the hook callback. |
| Floating bar or card steals focus, so the paste lands in the wrong window | `WindowDoesNotAcceptFocus` + `WS_EX_NOACTIVATE`; `show()` without `activateWindow()`. The checklist covers this explicitly. |
| Simulated Ctrl+C misfires (terminals, apps that copy the whole line when nothing is selected, e.g. VS Code) | Terminal skip list. If the copied text equals the clipboard from before Ctrl+C, it is treated as "no selection". VS Code's copy-the-line behaviour is accepted as a known limitation for now; UIA `TextPattern` is the later fix. |
| Paste-last chord pastes Ctrl+Shift+V | Wait for the modifiers to be released before injecting the paste. |
| Installer grows a lot | Use PySide6-Essentials only, plus spec excludes. Measure in Phase 7. |
| Whisper prompt overflows with a large dictionary | Trim to about 224 tokens, newest entries first. Claude still receives the full list. |

## Deferred (not in 1.1.0)

Muting background audio while recording, rich-text paste, UI Automation context (URL, text around
the cursor), a dictionary that learns automatically from the user's edits, an onboarding wizard,
auto-update, streaming transcription.

## Implementation notes

Differences from the plan above, and why:

- **The tray menu has "Copy last transcript", not "Paste last transcript".** Clicking a tray menu
  moves focus to the taskbar, so a paste from there would land nowhere. The paste-last hotkey
  pastes as planned.
- **Ask Anything uses structured outputs (`output_config.format` with a JSON schema), not a
  forced `respond` tool.** Forced `tool_choice` returns a 400 on Claude Opus 5.5, Sonnet 5.5 and
  Fable 5.1, which are selectable models.
- **Ask Anything raises effort to at least `medium`**, because the `low` default suits cleanup
  but is too terse for answering questions.
- **An `insert` result while text is selected is shown in the card instead of pasted**, so a
  selection the user didn't ask to change is never overwritten. The card's Insert button still
  does it.
- **The "copied text equals the old clipboard means no selection" check from Risks was not
  built.** It would discard a real selection whenever that text was already on the clipboard.
  VS Code's copy-the-line behaviour remains a known limitation (documented in the README).
- **Added: a second launch (for example from the Start menu) opens the window of the running
  app**, using a named event, instead of only exiting.
- **History `word_count` counts the words spoken** (the raw transcript), which is what "words
  dictated" and "time saved" are based on.
- **Settings combos**: Space, Right/Left Shift, Right/Left Ctrl, Tab or Enter. The paste-last
  chord is any modifiers plus a letter, digit or F-key, typed as `left_ctrl+right_shift+v`.
- **Qt packaging**: the software OpenGL DLL and Qt translations are left out of the bundle. The
  installer grew from 78.7 MB to 87.4 MB.

An independent code review found six problems, all fixed before the final build:

- Paste, paste-last, Insert and selection reading now take turns on the clipboard (one lock).
  Before, two of them could overlap and paste the wrong text or lose the user's clipboard.
- Space keeps auto-repeating for a moment after Right Alt is released; those repeats are now
  swallowed too, instead of typing spaces over the selection.
- Paste-last and selection reading no longer block the hotkey thread, so a dictation started
  right after them begins on time.
- A slow app that finishes the Ctrl+C after the wait no longer leaves the selection on the
  clipboard. The wait is now up to 1 s, then any late copy is undone.
- Changing the API key or timeout while a dictation is in flight can no longer lose that
  dictation; it falls back to the raw transcript as in 1.0.
- A microphone change from Settings is applied at the next recording, on the hotkey thread,
  instead of racing a recording that is starting.

Tests: 57 automated tests (16 original, unchanged, plus 41 new). Also run by hand on this machine:
live Claude calls for cleanup, translation and all three Ask Anything kinds; the worker pipeline
with real speech audio; screenshots of every window in light and dark mode; live checks of
focus, paste, clipboard restore and selection copy on a real window; and the built exe (first
run, model load, second launch, window close).

"""Wires the pieces together.

Threads:
  keyboard-hook  - keyboard hook (Windows or X11); posts hotkey actions to the controller queue
  controller     - starts/stops the microphone in response to those actions
  worker         - loads the speech model, then transcribes, cleans up and pastes each recording
  open-listener  - waits for a second launch of the app to ask for the main window
  main           - Qt event loop: tray icon, floating bar, Ask Anything card, main window
"""

from __future__ import annotations

import copy
import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .audio import SAMPLE_RATE, START_TONE, STOP_TONE, Recorder, beep
from .cleanup import AskError, Cleaner, Term
from .config import DB_PATH, Config
from .hotkey import Action, Intent, KeyboardHook, build_detectors
from .native import ForegroundApp, credentials, foreground_app, paste, win32
from .settings_store import copy_into, save_config
from .store import Store
from .transcriber import Transcriber, create_transcriber, is_silent, to_wav
from .tray import Status, Tray, make_icon
from .ui.bridge import CardContent, UiBridge
from .ui.card import AnswerCard
from .ui.floating_bar import FloatingBar
from .vocabulary import claude_terms, whisper_terms

log = logging.getLogger(__name__)

RELOAD = object()  # worker queue item: reload the speech model with the current settings


@dataclass
class Job:
    audio: np.ndarray
    app: ForegroundApp
    intent: Intent = Intent.DICTATE
    selection: str = ""  # text selected in the target app (Ask Anything)
    selection_ready: threading.Event | None = None  # set once `selection` has been read


class App:
    def __init__(self, cfg: Config, first_run: bool = False):
        self.cfg = cfg
        self.first_run = first_run
        self.qt = QApplication.instance() or QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setApplicationName("talk-to-mic")
        self.qt.setWindowIcon(make_icon(Status.IDLE.color))
        self._startup_warning: str | None = None
        try:
            detectors = self._detectors(cfg)
        except ValueError as exc:  # a bad combo or chord: keep plain dictation working
            log.warning("%s; Ask Anything, Translate and paste-last shortcuts are off", exc)
            self._startup_warning = f"{exc}. Fix it in Settings."
            detectors = build_detectors(cfg.hotkey.key, cfg.hotkey.min_hold_ms)
        self.store = self._open_store()
        self.actions: queue.Queue[Action | None] = queue.Queue()
        self.jobs: queue.Queue = queue.Queue()
        self.recorder = Recorder(cfg.audio.input_device, cfg.audio.max_seconds)
        self._recorder_stale = False  # microphone settings changed; replace before the next recording
        self.cleaner = Cleaner(cfg.cleanup)
        self.transcriber: Transcriber | None = None
        self.hook = KeyboardHook(detectors, self.actions.put)
        self.bridge = UiBridge()
        self.tray = Tray(cfg.hotkey.key, self._cleanup_enabled, self._toggle_cleanup, self.quit,
                         on_open=self.open_hub, on_copy_last=self.copy_last)
        self.bar = FloatingBar(lambda: self.recorder.level, lambda: self.cfg.translation.target_language)
        self.card = AnswerCard(self._insert_answer)
        self.hub = None
        self.bridge.state.connect(self._show_state)
        self.bridge.card.connect(self.card.show_content)
        self.bridge.open_requested.connect(self.open_hub)
        self._recording_app = ForegroundApp("", "")
        self._intent = Intent.DICTATE  # of the recording in progress
        self._job_intent = Intent.DICTATE  # of the recording the worker is processing
        self._last_text = self._latest_history_text()
        self._busy = 0  # recordings waiting in, or being processed by, the worker
        self._busy_lock = threading.Lock()

    @staticmethod
    def _detectors(cfg: Config) -> list:
        h = cfg.hotkey
        return build_detectors(h.key, h.min_hold_ms, h.ask_combo, h.translate_combo, h.paste_last)

    def _open_store(self) -> Store | None:
        try:
            store = Store(DB_PATH)
            removed = store.prune_history(self.cfg.history.retention_days)
            if removed:
                log.info("Removed %d history entries older than %d days", removed, self.cfg.history.retention_days)
            return store
        except Exception:
            log.exception("Could not open %s; history and the dictionary are unavailable", DB_PATH)
            return None

    def _latest_history_text(self) -> str:
        if self.store is None:
            return ""
        try:
            latest = self.store.search_history(limit=1)
        except Exception:
            return ""
        return latest[0].final_text if latest else ""

    # --- lifecycle -----------------------------------------------------------

    def run(self) -> None:
        self.tray.run(setup=self._setup)

    def _setup(self, tray: Tray) -> None:
        threading.Thread(target=self._worker, name="worker", daemon=True).start()
        threading.Thread(target=self._controller, name="controller", daemon=True).start()
        threading.Thread(target=self._open_listener, name="open-listener", daemon=True).start()
        try:
            self.hook.start()
        except RuntimeError as exc:
            log.error("Keyboard hook failed: %s", exc)
            self.tray.notify(f"Could not watch the keyboard: {exc}")
        if self.cfg.cleanup.enabled and not self.cleaner.available:
            self.tray.notify("Claude cleanup is off: set ANTHROPIC_API_KEY or add a key in Settings. "
                             "Raw transcripts will be pasted.")
        if self._startup_warning:
            self.tray.notify(self._startup_warning)
        if self.first_run:
            self.open_hub("home" if self.cleaner.client else "settings")

    def quit(self) -> None:
        self.hook.stop()
        self.actions.put(None)
        self.jobs.put(None)
        self.recorder.stop()
        self.bar.hide()
        self.card.hide()
        if self.hub:
            self.hub.hide()
        self.tray.stop()

    def _open_listener(self) -> None:
        event = win32.create_open_event()
        while event and win32.wait_event(event):
            self.bridge.open_requested.emit()

    # --- UI (main thread) ----------------------------------------------------

    def open_hub(self, page: str | None = None) -> None:
        from .ui.hub import Hub  # the main window is only built when first opened

        if self.hub is None:
            self.hub = Hub(self)
        self.hub.open(page)

    def copy_last(self) -> None:
        if not self._last_text:
            self.tray.notify("Nothing to copy yet: dictate something first.")
            return
        QGuiApplication.clipboard().setText(self._last_text)

    def _show_state(self, status: Status, intent: Intent, hwnd: int) -> None:
        if self.cfg.ui.floating_bar:
            self.bar.set_state(status, intent, hwnd)
        else:
            self.bar.hide()

    def _insert_answer(self, text: str, hwnd: int) -> None:
        def run() -> None:
            win32.activate_window(hwnd)
            time.sleep(0.15)  # let the window take focus before Ctrl+V
            paste.paste_text(text, self.cfg.paste.restore_clipboard, self.cfg.paste.restore_delay_ms / 1000)

        threading.Thread(target=run, name="insert", daemon=True).start()

    # --- settings ------------------------------------------------------------

    def apply_settings(self, new: Config) -> None:
        """Saves new settings to config.toml and applies them. Raises ValueError for bad shortcuts."""
        detectors = self._detectors(new)
        new = copy.deepcopy(new)
        save_config(new)
        old = copy.deepcopy(self.cfg)
        copy_into(self.cfg, new)
        self.hook.detectors = detectors
        self.tray.hotkey_name = new.hotkey.key
        if (old.audio.input_device, old.audio.max_seconds) != (new.audio.input_device, new.audio.max_seconds):
            self._recorder_stale = True  # replaced by the controller at the next recording
        if old.cleanup.timeout_seconds != new.cleanup.timeout_seconds:
            self.cleaner.connect()
        t_old, t_new = old.transcription, new.transcription
        if (t_old.engine, t_old.model, t_old.server_url) != (t_new.engine, t_new.model, t_new.server_url):
            self.jobs.put(RELOAD)
        if self.store and new.history.retention_days != old.history.retention_days:
            self.store.prune_history(new.history.retention_days)
            self.bridge.history_changed.emit()
        log.info("Settings saved")

    def set_api_key(self, key: str | None) -> None:
        """Stores (or with None, removes) the Claude key in Credential Manager (or the keyring) and reconnects."""
        if key:
            credentials.write_api_key(key)
        else:
            credentials.delete_api_key()
        self.cleaner.connect()

    def whisper_vocabulary(self) -> list[str]:
        return whisper_terms(self.cfg.transcription.vocabulary, self.store)

    def claude_vocabulary(self) -> list[Term]:
        return claude_terms(self.cfg.transcription.vocabulary, self.store)

    # --- tray callbacks ------------------------------------------------------

    def _cleanup_enabled(self) -> bool:
        return self.cleaner.available

    def _toggle_cleanup(self) -> None:
        if self.cleaner.client is None:
            self.tray.notify(self.cleaner.disabled_reason or "Claude is not configured")
            return
        self.cfg.cleanup.enabled = not self.cfg.cleanup.enabled

    # --- controller ----------------------------------------------------------

    def _controller(self) -> None:
        while (action := self.actions.get()) is not None:
            try:
                self._handle(action)
            except Exception as exc:
                log.exception("Hotkey handling failed")
                self.tray.notify(f"Microphone error: {exc}")
                self.recorder.stop()
                self._refresh_status()

    def _handle(self, action: Action) -> None:
        if action is Action.START:
            self._recording_app = foreground_app()
            self._intent = Intent.DICTATE
            if self._recorder_stale:
                self._recorder_stale = False
                self.recorder = Recorder(self.cfg.audio.input_device, self.cfg.audio.max_seconds)
            self.recorder.start()
            if self.cfg.audio.beep:
                beep(START_TONE)
            self._refresh_status()
        elif action in (Action.ASK, Action.TRANSLATE):
            intent = Intent.ASK if action is Action.ASK else Intent.TRANSLATE
            if self.recorder.recording and intent is not self._intent:
                self._intent = intent
                self._refresh_status()
        elif action is Action.CANCEL:
            self.recorder.stop()
            self._refresh_status()
        elif action is Action.FINISH:
            audio = self.recorder.stop()
            if self.cfg.audio.beep:
                beep(STOP_TONE)
            intent, app = self._intent, self._recording_app
            log.info("Recorded %.1fs in %s%s", audio.size / SAMPLE_RATE, app.process_name or "?",
                     "" if intent is Intent.DICTATE else f" ({intent.value})")
            job = Job(audio, app, intent)
            if intent is Intent.ASK and not is_silent(audio):
                # Read the selection now, while the user's window still has focus, but on its own
                # thread so the next hotkey press isn't delayed; the worker waits for it.
                job.selection_ready = threading.Event()
                threading.Thread(target=self._read_selection, args=(job,), name="selection", daemon=True).start()
            with self._busy_lock:
                self._busy += 1
            self.jobs.put(job)
            self._refresh_status()
        elif action is Action.PASTE_LAST:
            threading.Thread(target=self._paste_last, name="paste-last", daemon=True).start()

    @staticmethod
    def _read_selection(job: Job) -> None:
        try:
            job.selection = paste.copy_selection(job.app.process_name)
        except Exception:
            log.exception("Could not read the selection")
        finally:
            job.selection_ready.set()

    def _paste_last(self) -> None:
        if not self._last_text:
            self.tray.notify("Nothing to paste yet: dictate something first.")
            return
        win32.wait_keys_released(timeout_s=2.0)  # the chord's Ctrl and Shift
        paste.paste_text(self._last_text, self.cfg.paste.restore_clipboard, self.cfg.paste.restore_delay_ms / 1000)

    def _refresh_status(self) -> None:
        intent = self._job_intent
        if self.recorder.recording:
            status, intent = Status.RECORDING, self._intent
        elif self.transcriber is None:
            status = Status.LOADING
        elif self._busy:
            status = Status.PROCESSING
        else:
            status = Status.IDLE
        self.tray.set_status(status)
        self.bridge.state.emit(status, intent, int(self._recording_app.hwnd or 0))

    # --- worker --------------------------------------------------------------

    def _load_transcriber(self) -> None:
        self.transcriber = None
        self._refresh_status()
        try:
            self.transcriber = create_transcriber(self.cfg.transcription, self.whisper_vocabulary)
        except Exception as exc:
            log.exception("Could not load the speech model")
            self.tray.notify(f"Could not load the speech model: {exc}")
        self._refresh_status()

    def _worker(self) -> None:
        self._load_transcriber()
        while (job := self.jobs.get()) is not None:
            if job is RELOAD:
                self._load_transcriber()
                continue
            self._job_intent = job.intent
            try:
                self._process(job)
            except Exception as exc:
                log.exception("Dictation failed")
                self.tray.notify(f"Dictation failed: {exc}")
            finally:
                with self._busy_lock:
                    self._busy -= 1
                self._refresh_status()

    def _process(self, job: Job) -> None:
        if is_silent(job.audio):
            log.info("Recording was silent; nothing to paste")
            return
        if self.transcriber is None:
            self.tray.notify("The speech model is not loaded; check the speech settings.")
            return
        t0 = time.perf_counter()
        raw = self.transcriber.transcribe(job.audio)
        t1 = time.perf_counter()
        log.debug("Raw transcript: %r", raw)
        if not raw:
            log.info("No speech recognised")
            return
        dictionary = self.claude_vocabulary()
        if job.intent is Intent.ASK:
            self._ask(job, raw, dictionary)
            log.info("Transcribe %.2fs, Ask Anything %.2fs", t1 - t0, time.perf_counter() - t1)
            return
        if job.intent is Intent.TRANSLATE:
            text = self.cleaner.translate(raw, self.cfg.translation.target_language, job.app, dictionary)
            if text is None:
                self.tray.notify(f"Not translated ({self.cleaner.last_error}); pasted what you said.")
                text = raw
        else:
            text = self.cleaner.clean(raw, job.app, dictionary)
        t2 = time.perf_counter()
        log.debug("Cleaned text: %r", text)
        if text:
            paste.paste_text(text, self.cfg.paste.restore_clipboard, self.cfg.paste.restore_delay_ms / 1000)
            self._last_text = text
        self._record(job, raw, text)
        log.info("Transcribe %.2fs, %s %.2fs, %d chars pasted", t1 - t0,
                 "translate" if job.intent is Intent.TRANSLATE else "cleanup", t2 - t1, len(text))

    def _ask(self, job: Job, instruction: str, dictionary: list[Term]) -> None:
        if job.selection_ready and not job.selection_ready.wait(5):
            log.warning("Reading the selection timed out; asking without it")
        try:
            result = self.cleaner.ask(instruction, job.selection, job.app, dictionary)
        except AskError as exc:
            log.warning("Ask Anything failed: %s", exc)
            self.bridge.card.emit(CardContent(instruction, str(exc), job.app.hwnd, error=True))
            return
        log.info("Ask Anything: %s (%d chars)", result.kind, len(result.text))
        if result.kind in ("replace", "insert") and result.text:
            paste.paste_text(result.text, self.cfg.paste.restore_clipboard, self.cfg.paste.restore_delay_ms / 1000)
        else:
            self.bridge.card.emit(CardContent(instruction, result.text, job.app.hwnd))
        if result.text:
            self._last_text = result.text
        self._record(job, instruction, result.text)

    def _record(self, job: Job, raw: str, final: str) -> None:
        if self.store is None or not self.cfg.history.enabled:
            return
        try:
            self.store.add_history(
                job.intent.value, job.app.process_name, job.app.window_title, raw, final,
                job.audio.size / SAMPLE_RATE, to_wav(job.audio) if self.cfg.history.keep_audio else None,
            )
        except Exception:
            log.exception("Could not save this dictation to the history")
            return
        self.bridge.history_changed.emit()

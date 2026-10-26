"""The Linux (X11) keyboard hook behind hotkey.KeyboardHook.

XInput2 raw key events report every key on every keyboard, whichever window has focus. Keys
injected through XTEST (our own Ctrl+V, xdotool) come from their own device and count as injected.

Raw events can only be watched, not swallowed, so the keys Windows swallows are grabbed instead,
on each physical keyboard device rather than on the combined "core" keyboard the window manager
and apps listen to. That way a grab beats the window manager's shortcuts (Cinnamon opens the window
menu on Alt+Space) without clashing with them, and leaves every other key alone:
- the combo keys (Space) are grabbed while a dictation hold is in progress;
- the paste-last key is grabbed while the chord's modifiers are held.
The grabs live on a second X connection, so the listening one keeps receiving raw events.

Two Windows behaviours are imitated rather than copied:
- an Alt hotkey isn't swallowed, so once the hold counts as a dictation a key with no symbol is
  tapped while Alt is down, and releasing Alt doesn't focus a menu bar (VS Code, Firefox);
- hotkeys Windows swallows (F13-F24, Caps Lock, Scroll Lock, Pause) reach the app, which does
  nothing with them, except Caps Lock: it is toggled back when released.
"""

from __future__ import annotations

import logging
import os
import select
import threading
import time
from typing import Callable

from Xlib import X, display
from Xlib.ext import xinput, xtest
from Xlib.protocol import rq

from . import x11
from .hotkey import MODIFIER_VKS, Action, ChordDetector, HoldDetector, Mode

log = logging.getLogger(__name__)

GENERIC_EVENT = 35
CAPS_LOCK_VK = 0x14
KEY_MASK = xinput.KeyPressMask | xinput.KeyReleaseMask

# python-xlib knows the raw event types but not their layout. Key events carry no valuators.
RawEventData = rq.Struct(
    xinput.DEVICEID("deviceid"),
    rq.Card32("time"),
    rq.Card32("detail"),
    xinput.DEVICEID("sourceid"),
    rq.Card16("valuators_len"),
    rq.Card32("flags"),
    rq.Pad(4),
)


class KeyboardHook:
    def __init__(self, detectors: HoldDetector | list, on_action: Callable[[Action], None]):
        # Every detector sees every event; replace the list (not its items) to reconfigure.
        self._detectors = detectors if isinstance(detectors, list) else [detectors]
        self.on_action = on_action
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._wake_r, self._wake_w = os.pipe()
        self._stopping = False
        self._reconfigured = True
        self.error: str | None = None
        # Used only on the hook thread:
        self._d: display.Display | None = None  # listens for raw events
        self._g: display.Display | None = None  # grabs keys and injects the Alt mask key
        self._keyboards: set[int] = set()  # physical keyboard devices
        self._injected_devices: set[int] = set()  # XTEST keyboards
        self._vks: dict[int, int] = {}  # keycode -> virtual key, until the keyboard mapping changes
        self._mask_keycode = 0  # a keycode with no symbol, for the Alt mask tap
        self._mask_due: tuple[HoldDetector, float] | None = None
        self._grabs: set[tuple[int, int]] = set()  # (device, keycode) grabbed on the root window
        self._warned: set[tuple[int, int]] = set()

    @property
    def detectors(self) -> list:
        return self._detectors

    @detectors.setter
    def detectors(self, value: list) -> None:
        self._detectors = value
        self._reconfigured = True
        self._wake()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="keyboard-hook", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if self.error:
            raise RuntimeError(self.error)

    def stop(self) -> None:
        self._stopping = True
        self._wake()
        if self._thread:
            self._thread.join(2)

    def _wake(self) -> None:
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass

    # --- hook thread ---------------------------------------------------------

    def _run(self) -> None:
        try:
            self._d = d = display.Display()
            self._g = g = display.Display()
            self._listen()
        except Exception as exc:
            self.error = f"X11 keyboard events are unavailable ({exc}); talk-to-mic needs an X11 session"
            self._ready.set()
            return
        self._ready.set()
        try:
            while not self._stopping:
                if self._reconfigured:
                    self._reconfigured = False
                    self._sync_grabs()
                while d.pending_events():
                    self._handle(d.next_event())
                while g.pending_events():
                    g.next_event()  # the grabbed keys themselves; the raw events already told us
                self._mask_alt()
                d.flush()
                g.flush()
                timeout = max(0.0, self._mask_due[1] - time.monotonic()) if self._mask_due else None
                readable, _, _ = select.select([d, g, self._wake_r], [], [], timeout)
                if self._wake_r in readable:
                    os.read(self._wake_r, 64)
        except Exception:
            log.exception("Keyboard hook stopped")
        finally:
            self._set_grabs(set())
            d.close()
            g.close()

    def _listen(self) -> None:
        for conn in (self._d, self._g):
            info = conn.query_extension("XInputExtension")
            if not info.present:
                raise RuntimeError("the X server has no XInput extension")
            # 2.1+ delivers raw events to the root window even while another client grabs the keyboard.
            xinput.XIQueryVersion(display=conn.display, opcode=info.major_opcode, major_version=2, minor_version=2)
        for event_type in (xinput.RawKeyPress, xinput.RawKeyRelease):
            self._d.ge_add_event_data(info.major_opcode, event_type, RawEventData)
        self._find_devices()
        self._find_mask_keycode()
        mask = xinput.RawKeyPressMask | xinput.RawKeyReleaseMask | xinput.HierarchyChangedMask
        self._d.screen().root.xinput_select_events([(xinput.AllDevices, mask)])
        self._d.sync()

    def _find_devices(self) -> None:
        keyboards = [dev for dev in self._d.xinput_query_device(xinput.AllDevices).devices
                     if dev.use == xinput.SlaveKeyboard and any(c.type == xinput.KeyClass for c in dev.classes)]
        self._injected_devices = {dev.deviceid for dev in keyboards if "XTEST" in str(dev.name)}
        self._keyboards = {dev.deviceid for dev in keyboards} - self._injected_devices

    def _find_mask_keycode(self) -> None:
        first = self._d.display.info.min_keycode
        mapping = self._d.get_keyboard_mapping(first, self._d.display.info.max_keycode - first + 1)
        self._mask_keycode = next((first + i for i, syms in enumerate(mapping) if not any(syms)), 0)

    def _handle(self, event) -> None:
        if event.type == X.MappingNotify:
            self._d.refresh_keyboard_mapping(event)
            self._vks.clear()
            self._find_mask_keycode()
            self._set_grabs(set())  # keycodes may have moved
            self._sync_grabs()
            return
        if event.type != GENERIC_EVENT:
            return
        if event.evtype == xinput.HierarchyChanged:
            self._find_devices()
            self._grabs = {grab for grab in self._grabs if grab[0] in self._keyboards}  # unplugged ones are gone
            self._sync_grabs()
            return
        if event.evtype not in (xinput.RawKeyPress, xinput.RawKeyRelease):
            return
        # Each key arrives twice: from its own keyboard and from the core keyboard. Use the first.
        if event.data.deviceid not in self._keyboards and event.data.deviceid not in self._injected_devices:
            return
        keycode, is_down = event.data.detail, event.evtype == xinput.RawKeyPress
        injected = event.data.deviceid in self._injected_devices
        vk = self._vks.get(keycode)
        if vk is None:
            vk = self._vks[keycode] = x11.vk_for_keycode(self._d, keycode)
        try:
            now = time.monotonic()
            for detector in self._detectors:
                decision = detector.feed(vk, is_down, injected, now)
                if decision.action:
                    self.on_action(decision.action)
                if decision.action is Action.START and detector.mode is Mode.ALT_MASK:
                    self._mask_due = (detector, detector.down_at + detector.min_hold_s)
            self._sync_grabs()
            if vk == CAPS_LOCK_VK and not is_down and not injected and self._is_hotkey(CAPS_LOCK_VK):
                self._tap(keycode)  # holding it to dictate toggled Caps Lock; toggle it back
        except Exception:  # keep watching the keyboard whatever a detector does
            log.exception("Keyboard hook callback failed")

    def _is_hotkey(self, vk: int) -> bool:
        return any(isinstance(dt, HoldDetector) and dt.vk == vk for dt in self._detectors)

    def _tap(self, keycode: int) -> None:
        xtest.fake_input(self._g, X.KeyPress, keycode)
        xtest.fake_input(self._g, X.KeyRelease, keycode)
        self._g.flush()

    def _mask_alt(self) -> None:
        """Windows' release_alt: Alt pressed and released on its own focuses the menu bar in
        many apps; another key in between stops that."""
        if not self._mask_due or time.monotonic() < self._mask_due[1]:
            return
        detector = self._mask_due[0]
        self._mask_due = None
        if detector in self._detectors and detector.held and not detector.interrupted and self._mask_keycode:
            self._tap(self._mask_keycode)

    # --- grabs ---------------------------------------------------------------

    def _sync_grabs(self) -> None:
        keys: set[int] = set()
        for dt in self._detectors:
            if isinstance(dt, HoldDetector) and ((dt.held and not dt.interrupted) or dt._swallowed):
                keys.update(vk for vk in dt.combos if vk not in MODIFIER_VKS)  # modifiers pass through
            elif isinstance(dt, ChordDetector) and dt.down >= dt.chord.modifiers:
                keys.add(dt.chord.vk)
        keycodes = {x11.keycode_for_vk(self._d, vk) for vk in keys} - {0}
        self._set_grabs({(dev, keycode) for dev in self._keyboards for keycode in keycodes})

    def _set_grabs(self, wanted: set[tuple[int, int]]) -> None:
        if wanted == self._grabs:
            return
        root = self._g.screen().root
        for dev, keycode in self._grabs - wanted:
            root.xinput_ungrab_keycode(dev, keycode, [xinput.AnyModifier])
        for dev, keycode in wanted - self._grabs:
            # A key grab on the keyboard device itself takes priority over the window manager's
            # grabs on the core keyboard, so the key reaches neither it nor the focused app.
            reply = root.xinput_grab_keycode(dev, X.CurrentTime, keycode, xinput.GrabModeAsync,
                                             xinput.GrabModeAsync, False, KEY_MASK, [xinput.AnyModifier])
            if reply.modifiers and (dev, keycode) not in self._warned:
                self._warned.add((dev, keycode))
                log.warning("Another app holds key %d on keyboard %d; it may also see talk-to-mic's keys", keycode, dev)
        self._g.flush()
        self._grabs = wanted

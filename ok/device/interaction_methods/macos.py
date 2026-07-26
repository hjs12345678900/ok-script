"""Foreground-only macOS input implemented with native Quartz CGEvents."""

from __future__ import annotations

import time

from ok.device.capture_methods.base import BaseCaptureMethod
from ok.device.interaction_methods.base import BaseInteraction
from ok.platform.macos.helper import MacOSHelper, MacOSHelperError
from ok.platform.macos.window import MacWindow
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

KEY_ALIASES = {
    " ": "space",
    "escape": "esc",
    "page_up": "pageup",
    "page_down": "pagedown",
    "caps_lock": "capslock",
    "lcontrol": "lctrl",
    "rcontrol": "rctrl",
    "ctrl_l": "lctrl",
    "ctrl_r": "rctrl",
    "shift_l": "lshift",
    "shift_r": "rshift",
    "alt_l": "lalt",
    "alt_r": "ralt",
    "cmd_l": "cmd",
    "cmd_r": "cmd",
    "command": "cmd",
    "meta": "cmd",
    "windows": "cmd",
    "win": "cmd",
}

MOUSE_BUTTON_NAMES = {
    "left": 0,
    "right": 1,
    "middle": 2,
}

KEY_CODES = {
    "a": 0,
    "s": 1,
    "d": 2,
    "f": 3,
    "h": 4,
    "g": 5,
    "z": 6,
    "x": 7,
    "c": 8,
    "v": 9,
    "b": 11,
    "q": 12,
    "w": 13,
    "e": 14,
    "r": 15,
    "y": 16,
    "t": 17,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "6": 22,
    "5": 23,
    "=": 24,
    "9": 25,
    "7": 26,
    "-": 27,
    "8": 28,
    "0": 29,
    "]": 30,
    "o": 31,
    "u": 32,
    "[": 33,
    "i": 34,
    "p": 35,
    "enter": 36,
    "return": 36,
    "l": 37,
    "j": 38,
    "'": 39,
    "k": 40,
    ";": 41,
    "\\": 42,
    ",": 43,
    "/": 44,
    "n": 45,
    "m": 46,
    ".": 47,
    "tab": 48,
    "space": 49,
    "`": 50,
    "backspace": 51,
    "esc": 53,
    "cmd": 55,
    "shift": 56,
    "lshift": 56,
    "capslock": 57,
    "alt": 58,
    "lalt": 58,
    "ctrl": 59,
    "lctrl": 59,
    "rshift": 60,
    "ralt": 61,
    "rctrl": 62,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f3": 99,
    "f8": 100,
    "f9": 101,
    "f11": 103,
    "f10": 109,
    "f12": 111,
    "home": 115,
    "pageup": 116,
    "delete": 117,
    "f4": 118,
    "end": 119,
    "f2": 120,
    "pagedown": 121,
    "f1": 122,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
}

MODIFIER_KEYS = frozenset(
    {
        "alt",
        "lalt",
        "ralt",
        "ctrl",
        "lctrl",
        "rctrl",
        "shift",
        "lshift",
        "rshift",
        "cmd",
    }
)


def _quartz_modifier_flags(Quartz, modifiers: set[str]):
    """Translate held key names to CGEvent modifier flags."""
    flags = 0
    modifier_masks = {
        "alt": Quartz.kCGEventFlagMaskAlternate,
        "lalt": Quartz.kCGEventFlagMaskAlternate,
        "ralt": Quartz.kCGEventFlagMaskAlternate,
        "ctrl": Quartz.kCGEventFlagMaskControl,
        "lctrl": Quartz.kCGEventFlagMaskControl,
        "rctrl": Quartz.kCGEventFlagMaskControl,
        "shift": Quartz.kCGEventFlagMaskShift,
        "lshift": Quartz.kCGEventFlagMaskShift,
        "rshift": Quartz.kCGEventFlagMaskShift,
        "cmd": Quartz.kCGEventFlagMaskCommand,
    }
    for modifier in modifiers:
        flags |= modifier_masks.get(modifier, 0)
    return flags


def _post_quartz_mouse(
    action: str,
    x: float,
    y: float,
    key: str = "left",
    down_time: float = 0.02,
    modifiers: set[str] | None = None,
) -> bool:
    """Post one mouse action in-process, avoiding a helper launch per click."""
    try:
        import Quartz

        button = MOUSE_BUTTON_NAMES.get(str(key).lower())
        if button is None:
            return False
        point = (float(x), float(y))
        flags = _quartz_modifier_flags(Quartz, modifiers or set())
        event_types = {
            "left": (
                Quartz.kCGEventLeftMouseDown,
                Quartz.kCGEventLeftMouseUp,
            ),
            "right": (
                Quartz.kCGEventRightMouseDown,
                Quartz.kCGEventRightMouseUp,
            ),
            "middle": (
                Quartz.kCGEventOtherMouseDown,
                Quartz.kCGEventOtherMouseUp,
            ),
        }

        def post(event_type):
            event = Quartz.CGEventCreateMouseEvent(
                None,
                event_type,
                point,
                button,
            )
            if event is None:
                raise RuntimeError("CGEventCreateMouseEvent returned None")
            # Always set the complete flag state, including zero. Events made
            # with a null source can otherwise inherit the physical Fn/Command
            # state; Q then becomes macOS's global Quick Note shortcut.
            Quartz.CGEventSetFlags(event, flags)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

        if action == "move":
            post(Quartz.kCGEventMouseMoved)
        elif action == "down":
            post(event_types[str(key).lower()][0])
        elif action == "up":
            post(event_types[str(key).lower()][1])
        elif action == "click":
            post(Quartz.kCGEventMouseMoved)
            post(event_types[str(key).lower()][0])
            if down_time > 0:
                time.sleep(down_time)
            post(event_types[str(key).lower()][1])
        else:
            return False
        return True
    except Exception as exc:
        logger.warning(f"in-process Quartz mouse input failed: {exc}")
        return False


def _post_quartz_key(
    action: str,
    name: str,
    down_time: float = 0.02,
    modifiers: set[str] | None = None,
) -> bool:
    """Post one mapped keyboard action in-process."""
    try:
        import Quartz

        key_code = KEY_CODES.get(str(name).lower())
        if key_code is None:
            return False
        flags = _quartz_modifier_flags(Quartz, modifiers or set())

        def post(is_down):
            event = Quartz.CGEventCreateKeyboardEvent(
                None,
                key_code,
                is_down,
            )
            if event is None:
                raise RuntimeError("CGEventCreateKeyboardEvent returned None")
            # Explicitly clearing flags is important for unmodified combat
            # keys such as Q. It prevents an inherited SecondaryFn flag from
            # turning Q into the system-wide Quick Note shortcut.
            Quartz.CGEventSetFlags(event, flags)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

        if action == "down":
            post(True)
        elif action == "up":
            post(False)
        elif action == "press":
            post(True)
            if down_time > 0:
                time.sleep(down_time)
            post(False)
        else:
            return False
        return True
    except Exception as exc:
        logger.warning(f"in-process Quartz keyboard input failed: {exc}")
        return False


def _cursor_position() -> tuple[float, float] | None:
    """Return the current global Quartz cursor position when available."""
    try:
        import Quartz

        point = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return float(point.x), float(point.y)
    except Exception:
        return None


class MacForegroundInteraction(BaseInteraction):
    """Send input only while the tracked macOS application is foreground."""

    def __init__(
        self,
        capture: BaseCaptureMethod,
        mac_window: MacWindow,
        *,
        helper: MacOSHelper | None = None,
        use_direct_mouse: bool | None = None,
        use_direct_keys: bool | None = None,
    ):
        super().__init__(capture)
        self.mac_window = mac_window
        self.helper = helper or mac_window.helper
        self.use_direct_mouse = (
            isinstance(self.helper, MacOSHelper)
            if use_direct_mouse is None
            else use_direct_mouse
        )
        self.use_direct_keys = (
            isinstance(self.helper, MacOSHelper)
            if use_direct_keys is None
            else use_direct_keys
        )
        self.check_clickable = True
        self._last_abs_position = self._resolve_position(-1, -1)
        self._held_modifiers: set[str] = set()
        if self.use_direct_mouse or self.use_direct_keys:
            logger.info(
                "macOS in-process Quartz combat input enabled "
                f"(mouse={self.use_direct_mouse}, keys={self.use_direct_keys})"
            )

    def clickable(self):
        return not self.check_clickable or self.mac_window.is_foreground()

    @staticmethod
    def _key_name(key) -> str:
        value = str(key).lower()
        return KEY_ALIASES.get(value, value)

    def _run(self, *arguments, allow_background: bool = False) -> bool:
        if not allow_background and not self.clickable():
            logger.info("macOS input skipped because the game is not foreground")
            return False
        try:
            self.helper.run(*arguments, timeout=5)
            note_interaction = getattr(self.capture, "note_interaction", None)
            if callable(note_interaction):
                note_interaction()
            return True
        except MacOSHelperError as exc:
            logger.error(f"macOS input failed: {exc}")
            return False

    def _run_mouse(
        self,
        action: str,
        x: float,
        y: float,
        *,
        key: str = "left",
        down_time: float = 0,
    ) -> bool:
        if action != "up" and not self.clickable():
            logger.info(
                "macOS direct mouse input skipped because the game is not foreground"
            )
            return False
        if self.use_direct_mouse and _post_quartz_mouse(
            action,
            x,
            y,
            key,
            down_time,
            set(self._held_modifiers),
        ):
            note_interaction = getattr(self.capture, "note_interaction", None)
            if callable(note_interaction):
                note_interaction()
            return True
        return False

    def _run_key(
        self,
        action: str,
        name: str,
        *,
        down_time: float = 0,
    ) -> bool:
        if action != "up" and not self.clickable():
            logger.info(
                "macOS direct key input skipped because the game is not foreground"
            )
            return False
        modifiers = set(self._held_modifiers)
        if action == "up":
            modifiers.discard(name)
        if self.use_direct_keys and _post_quartz_key(
            action,
            name,
            down_time,
            modifiers,
        ):
            note_interaction = getattr(self.capture, "note_interaction", None)
            if callable(note_interaction):
                note_interaction()
            return True
        return False

    def _resolve_position(self, x, y) -> tuple[float, float]:
        if x != -1 and y != -1:
            return self.capture.get_abs_cords(x, y)

        # Coordinate-free combat actions are normal attacks and heavy attacks.
        # They must land inside the tracked game window regardless of where the
        # user's system cursor currently is (including on another monitor).
        width = (
            getattr(self.capture, "width", 0)
            or getattr(self.mac_window, "capture_width", 0)
            or getattr(self.mac_window, "width", 0)
            or 1
        )
        height = (
            getattr(self.capture, "height", 0)
            or getattr(self.mac_window, "capture_height", 0)
            or getattr(self.mac_window, "height", 0)
            or 1
        )
        return self.capture.get_abs_cords(width / 2, height / 2)

    def send_key(self, key, down_time=0.02):
        name = self._key_name(key)
        if self._run_key("press", name, down_time=down_time):
            return
        self._run("key", "press", name, round(down_time * 1000))

    def send_key_down(self, key):
        name = self._key_name(key)
        sent = self._run_key("down", name) or self._run(
            "key",
            "down",
            name,
        )
        if sent and name in MODIFIER_KEYS:
            self._held_modifiers.add(name)

    def send_key_up(self, key):
        name = self._key_name(key)
        try:
            if not self._run_key("up", name):
                self._run("key", "up", name, allow_background=True)
        finally:
            self._held_modifiers.discard(name)

    def move(self, x, y):
        if not self.clickable():
            return
        abs_x, abs_y = self.capture.get_abs_cords(x, y)
        self._last_abs_position = (abs_x, abs_y)
        if self._run_mouse("move", abs_x, abs_y):
            return
        self._run("mouse", "move", abs_x, abs_y)

    def click(
        self,
        x=-1,
        y=-1,
        move_back=False,
        name=None,
        down_time=0.02,
        move=True,
        key="left",
    ):
        del move_back, name, move
        if not self.clickable():
            return
        abs_x, abs_y = self._resolve_position(x, y)
        self._last_abs_position = (abs_x, abs_y)
        if self._run_mouse(
            "click",
            abs_x,
            abs_y,
            key=key,
            down_time=down_time,
        ):
            return
        action = "modified-click" if self._held_modifiers else "click"
        arguments = [
            "mouse",
            action,
            abs_x,
            abs_y,
            key,
            round(down_time * 1000),
        ]
        if self._held_modifiers:
            arguments.extend(sorted(self._held_modifiers))
        self._run(*arguments)

    def mouse_down(self, x=-1, y=-1, name=None, key="left"):
        del name
        if not self.clickable():
            return
        abs_x, abs_y = self._resolve_position(x, y)
        self._last_abs_position = (abs_x, abs_y)
        if self._run_mouse("down", abs_x, abs_y, key=key):
            return
        self._run("mouse", "down", abs_x, abs_y, key)

    def mouse_up(self, key="left"):
        # CGEvent mouse-up requires a location, so retain the last event position.
        abs_x, abs_y = self._last_abs_position
        if self._run_mouse("up", abs_x, abs_y, key=key):
            return
        self._run(
            "mouse",
            "up",
            abs_x,
            abs_y,
            key,
            allow_background=True,
        )

    def scroll(self, x, y, scroll_amount):
        if not self.clickable():
            return
        abs_x, abs_y = self.capture.get_abs_cords(x, y)
        self._last_abs_position = (abs_x, abs_y)
        self._run("mouse", "scroll", abs_x, abs_y, int(scroll_amount))

    def swipe(self, x1, y1, x2, y2, duration, settle_time=0):
        if not self.clickable():
            return
        abs_x1, abs_y1 = self.capture.get_abs_cords(x1, y1)
        abs_x2, abs_y2 = self.capture.get_abs_cords(x2, y2)
        self._last_abs_position = (abs_x2, abs_y2)
        self._run(
            "mouse",
            "swipe",
            abs_x1,
            abs_y1,
            abs_x2,
            abs_y2,
            max(1, round(duration * 1000)),
        )
        if settle_time > 0:
            time.sleep(settle_time)

    def input_text(self, text):
        self._run("text", text)

    def should_capture(self):
        return self.clickable()

    def on_run(self):
        return self.mac_window.bring_to_front()

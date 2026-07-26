"""ScreenCaptureKit-backed macOS application-window discovery and geometry."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Pattern

from ok.platform.macos.helper import MacOSHelper
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)


@dataclass(frozen=True)
class MacWindowInfo:
    """Serializable geometry and application identity for one macOS window."""

    window_id: int
    pid: int
    bundle_id: str
    owner_name: str
    title: str
    x: float
    y: float
    width: float
    height: float
    on_screen: bool
    active: bool

    @classmethod
    def from_dict(cls, value: dict) -> "MacWindowInfo":
        return cls(
            window_id=int(value.get("windowID", 0)),
            pid=int(value.get("pid", 0)),
            bundle_id=str(value.get("bundleIdentifier") or ""),
            owner_name=str(value.get("ownerName") or ""),
            title=str(value.get("title") or ""),
            x=float(value.get("x", 0)),
            y=float(value.get("y", 0)),
            width=float(value.get("width", 0)),
            height=float(value.get("height", 0)),
            on_screen=bool(value.get("onScreen", False)),
            active=bool(value.get("active", False)),
        )


def _matches(value: str, candidates: list[str | Pattern[str]]) -> bool:
    if not candidates:
        return True
    value_folded = value.casefold()
    for candidate in candidates:
        if hasattr(candidate, "search"):
            if candidate.search(value):
                return True
        elif str(candidate).casefold() == value_folded:
            return True
    return False


class MacWindow:
    """Track one macOS game window while preserving the HwndWindow-facing API."""

    def __init__(
        self,
        exit_event,
        bundle_id: str | list[str] | None = None,
        title: str | Pattern[str] | list[str | Pattern[str]] | None = None,
        owner_name: str | list[str] | None = None,
        *,
        helper: MacOSHelper | None = None,
        global_config=None,
        device_manager=None,
        poll_interval: float = 0.5,
    ):
        self.app_exit_event = exit_event
        self.stop_event = threading.Event()
        self._refresh_lock = threading.Lock()
        self.helper = helper or MacOSHelper()
        self.device_manager = device_manager
        self.global_config = global_config
        self.poll_interval = poll_interval
        self.bundle_ids = self._as_list(bundle_id)
        self.titles = self._as_list(title)
        self.owner_names = self._as_list(owner_name)
        self.window_id = 0
        self.hwnd = 0
        self.pid = 0
        self.bundle_id = ""
        self.owner_name = ""
        self.title = ""
        self.exe_full_path = None
        self.x = 0.0
        self.y = 0.0
        self.width = 0
        self.height = 0
        self.window_width = 0
        self.window_height = 0
        self.capture_width = 0
        self.capture_height = 0
        # Window geometry and Quartz input use points. Capture frames use pixels.
        # Keep `scaling` at 1 for Qt overlay geometry and expose the pixel ratio
        # separately so callers do not accidentally divide point coordinates twice.
        self.scaling = 1.0
        self.capture_scaling = 1.0
        self.real_x_offset = 0
        self.real_y_offset = 0
        self.real_width = 0
        self.real_height = 0
        self.exists = False
        self.visible = False
        self.pos_valid = False
        self.frame_aspect_ratio = 0
        self.hwnds = []
        self.top_hwnd = 0
        self._last_error = 0.0
        self.do_update_window_size()
        self.thread = threading.Thread(
            target=self.update_window_size,
            daemon=True,
            name="macos-window-update",
        )
        self.thread.start()

    @staticmethod
    def _as_list(value):
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @property
    def capture_target_signature(self):
        return (
            self.window_id,
            self.pid,
            self.x,
            self.y,
            self.width,
            self.height,
        )

    @property
    def hwnd_title(self):
        return self.title

    def update_window(
        self,
        title=None,
        bundle_id=None,
        owner_name=None,
        **_ignored,
    ):
        if title is not None:
            self.titles = self._as_list(title)
        if bundle_id is not None:
            self.bundle_ids = self._as_list(bundle_id)
        if owner_name is not None:
            self.owner_names = self._as_list(owner_name)
        self.do_update_window_size()

    def update_frame_size(self, width: int, height: int):
        self.capture_width = int(width)
        self.capture_height = int(height)
        if width > 0 and height > 0:
            self.frame_aspect_ratio = width / height
            point_scale_x = self.width / width if self.width else 1.0
            self.capture_scaling = (
                1.0 / point_scale_x if point_scale_x else 1.0
            )

    def _select_window(self, windows: list[MacWindowInfo]) -> MacWindowInfo | None:
        selected = []
        for window in windows:
            if window.width <= 10 or window.height <= 10:
                continue
            if self.bundle_ids and not _matches(window.bundle_id, self.bundle_ids):
                continue
            if self.owner_names and not _matches(window.owner_name, self.owner_names):
                continue
            if self.titles and not _matches(window.title, self.titles):
                continue
            selected.append(window)
        if not selected:
            return None
        selected.sort(
            key=lambda item: (
                item.window_id == self.window_id,
                item.active,
                item.on_screen,
                item.width * item.height,
            ),
            reverse=True,
        )
        return selected[0]

    def find(self) -> MacWindowInfo | None:
        windows = [
            MacWindowInfo.from_dict(item)
            for item in self.helper.list_windows(on_screen_only=False)
        ]
        return self._select_window(windows)

    def do_update_window_size(self):
        if not self._refresh_lock.acquire(blocking=False):
            return
        try:
            found = self.find()
            old_geometry = (
                self.visible,
                self.x,
                self.y,
                self.width,
                self.height,
                self.window_id,
            )
            if found is None:
                self.exists = False
                self.visible = False
                self.pos_valid = False
                return
            self.window_id = found.window_id
            self.hwnd = found.window_id
            self.pid = found.pid
            self.bundle_id = found.bundle_id
            self.owner_name = found.owner_name
            self.title = found.title
            self.x = found.x
            self.y = found.y
            self.width = round(found.width)
            self.height = round(found.height)
            self.window_width = self.width
            self.window_height = self.height
            self.real_width = self.width
            self.real_height = self.height
            self.exists = True
            self.visible = found.active
            self.pos_valid = found.on_screen
            new_geometry = (
                self.visible,
                self.x,
                self.y,
                self.width,
                self.height,
                self.window_id,
            )
            if new_geometry != old_geometry:
                self._emit_window_update()
        except Exception as exc:
            if time.monotonic() - self._last_error > 5:
                logger.error(f"macOS window refresh failed: {exc}")
                self._last_error = time.monotonic()
        finally:
            self._refresh_lock.release()

    def _emit_window_update(self):
        try:
            from ok.gui.Communicate import communicate

            communicate.window.emit(
                self.visible,
                self.x,
                self.y,
                self.window_width,
                self.window_height,
                self.width,
                self.height,
                self.scaling,
            )
        except ImportError:
            pass

    def update_window_size(self):
        while not self.app_exit_event.is_set() and not self.stop_event.is_set():
            self.do_update_window_size()
            self.stop_event.wait(self.poll_interval)

    def get_abs_cords(self, x: float, y: float) -> tuple[float, float]:
        """Convert captured-frame pixels to global macOS point coordinates."""
        source_width = self.capture_width or self.width or 1
        source_height = self.capture_height or self.height or 1
        return (
            self.x + x * self.width / source_width,
            self.y + y * self.height / source_height,
        )

    def get_top_window_cords(self, x, y):
        return x, y

    def is_foreground(self):
        return self.exists and self.visible and self.pos_valid

    def bring_to_front(self):
        if not self.pid:
            self.do_update_window_size()
        if not self.pid:
            return False
        result = self.helper.run("activate", self.pid, check=False)
        if result.returncode == 0:
            self.do_update_window_size()
            return True
        logger.warning(f"failed to activate macOS app: {result.stderr.strip()}")
        return False

    def try_resize_to(self, _resize_to):
        """Window resizing is deliberately not part of the foreground MVP."""
        return False

    def stop(self):
        self.stop_event.set()

    def __str__(self):
        return (
            f"macos_{self.bundle_id}_{self.owner_name}_{self.title}_"
            f"{self.width}x{self.height}_{self.window_id}"
        )

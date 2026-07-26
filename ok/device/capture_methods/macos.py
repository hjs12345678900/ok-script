"""Continuous ScreenCaptureKit capture for a tracked macOS window."""

from __future__ import annotations

import os
import struct
import subprocess
import tempfile
import threading
import time
import zlib

import cv2
import numpy as np

from ok.device.capture_methods.base import BaseCaptureMethod
from ok.platform.macos.helper import MacOSHelper
from ok.platform.macos.window import MacWindow
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

FRAME_HEADER = struct.Struct("<4sIIII")
FRAME_MAGIC = b"OKFR"
PNG_FRAME_MAGIC = b"OKPN"
MAX_FRAME_BYTES = 256 * 1024 * 1024


def _read_exact(stream, count: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < count:
        chunk = stream.read(count - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


class MacScreenCaptureMethod(BaseCaptureMethod):
    """Keep the latest BGR frame from a native ScreenCaptureKit stream."""

    name = "ScreenCaptureKit"
    description = "Capture a macOS window with Apple's ScreenCaptureKit"

    def __init__(
        self,
        mac_window: MacWindow,
        exit_event,
        *,
        helper: MacOSHelper | None = None,
        fps: int = 30,
        first_frame_timeout: float = 5.0,
        stale_frame_timeout: float = 2.0,
        interaction_refresh_timeout: float = 1.0,
    ):
        super().__init__()
        self.mac_window = mac_window
        self.exit_event = exit_event
        self.helper = helper or mac_window.helper
        self.fps = max(1, int(fps))
        self.first_frame_timeout = first_frame_timeout
        self.stale_frame_timeout = max(0.5, float(stale_frame_timeout))
        self.interaction_refresh_timeout = max(
            0.25,
            float(interaction_refresh_timeout),
        )
        self._process: subprocess.Popen[bytes] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._latest_frame: np.ndarray | None = None
        self._last_frame_at = 0.0
        self._frame_sequence = 0
        self._delivered_frame_sequence = 0
        self._fps_sample_started = 0.0
        self._fps_sample_sequence = 0
        self._reported_stream_fps = False
        self._latest_signature: int | None = None
        self._pending_interaction_at = 0.0
        self._pending_interaction_sequence = 0
        self._pending_interaction_signature: int | None = None
        self._frame_condition = threading.Condition()
        self._stream_window_id = 0
        self._last_error = ""
        self._content_top_rows = 0
        self._raw_frame_height = 0
        # Prefer the continuous stream so the configured FPS is real. Startup,
        # interruption, and stale-frame guards below retain the system utility
        # as a safe fallback for macOS versions where SCStream is unreliable.
        self._stream_mode = "stream"
        self._fallback_requested = False
        self._system_snapshot_interval = 1 / min(self.fps, 5)
        self._fresh_frame_timeout = max(0.05, min(0.2, 3 / self.fps))

    @staticmethod
    def _detect_top_border(frame: np.ndarray) -> int:
        """Detect a narrow black macOS window border above rendered content."""
        height = frame.shape[0]
        if height <= 1:
            return 0
        row_means = frame.mean(axis=(1, 2))
        non_black = np.flatnonzero(row_means > 2)
        if non_black.size == 0:
            return 0
        top = int(non_black[0])
        max_border = max(1, min(height // 10, 120))
        return top if 0 < top <= max_border else 0

    def _normalize_content_frame(self, frame: np.ndarray) -> np.ndarray:
        """Remove the window border while preserving the configured frame size."""
        if self._content_top_rows == 0:
            self._content_top_rows = self._detect_top_border(frame)
        top = self._content_top_rows
        self._raw_frame_height = frame.shape[0]
        if top == 0:
            return frame
        content = frame[top:, :, :]
        return cv2.resize(
            content,
            (frame.shape[1], frame.shape[0]),
            interpolation=cv2.INTER_LINEAR,
        )

    @staticmethod
    def _frame_signature(frame: np.ndarray) -> int:
        """Return a cheap signature that detects a frozen repeated image."""
        y_step = max(1, frame.shape[0] // 36)
        x_step = max(1, frame.shape[1] // 64)
        sample = frame[::y_step, ::x_step]
        return zlib.crc32(sample.tobytes())

    def note_interaction(self):
        """Require captured content to refresh after a successful input event."""
        with self._frame_condition:
            if self._latest_frame is None:
                return
            self._pending_interaction_at = time.monotonic()
            self._pending_interaction_sequence = self._frame_sequence
            self._pending_interaction_signature = self._latest_signature

    @property
    def hwnd_window(self):
        """Compatibility alias for code shared with Windows capture backends."""
        return self.mac_window

    @property
    def window(self):
        return self.mac_window

    def _start_stream(self):
        window_id = self.mac_window.window_id
        if (
            window_id
            and self._process is not None
            and self._process.poll() is None
            and self._stream_window_id == window_id
        ):
            return True

        self.mac_window.do_update_window_size()
        window_id = self.mac_window.window_id
        if not window_id:
            return False
        self.close()
        stream_fps = self.fps if self._stream_mode == "stream" else min(self.fps, 5)
        command = self.helper.command(self._stream_mode, window_id, stream_fps)
        logger.info(
            f"starting ScreenCaptureKit {self._stream_mode} "
            f"for window {window_id} at {stream_fps} FPS"
        )
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._stream_window_id = window_id
        self._reader_thread = threading.Thread(
            target=self._read_frames,
            daemon=True,
            name="macos-capture-frames",
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            daemon=True,
            name="macos-capture-errors",
        )
        self._reader_thread.start()
        self._stderr_thread.start()
        return True

    def _read_frames(self):
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while process.poll() is None and not self.exit_event.is_set():
                header = _read_exact(process.stdout, FRAME_HEADER.size)
                if header is None:
                    break
                magic, width, height, bytes_per_row, payload_length = FRAME_HEADER.unpack(header)
                if magic not in (FRAME_MAGIC, PNG_FRAME_MAGIC):
                    raise ValueError(f"invalid frame magic {magic!r}")
                if width <= 0 or height <= 0 or payload_length > MAX_FRAME_BYTES:
                    raise ValueError(
                        "invalid frame metadata "
                        f"{width}x{height}, row={bytes_per_row}, bytes={payload_length}"
                    )
                if magic == FRAME_MAGIC:
                    expected_length = bytes_per_row * height
                    if (
                        bytes_per_row < width * 4
                        or bytes_per_row % 4
                        or payload_length != expected_length
                    ):
                        raise ValueError(
                            "invalid raw frame metadata "
                            f"{width}x{height}, row={bytes_per_row}, "
                            f"bytes={payload_length}"
                        )
                payload = _read_exact(process.stdout, payload_length)
                if payload is None:
                    break
                if magic == FRAME_MAGIC:
                    row_pixels = bytes_per_row // 4
                    bgra = np.frombuffer(payload, dtype=np.uint8).reshape(
                        height,
                        row_pixels,
                        4,
                    )
                    frame = bgra[:, :width, :3].copy()
                else:
                    frame = cv2.imdecode(
                        np.frombuffer(payload, dtype=np.uint8),
                        cv2.IMREAD_COLOR,
                    )
                    if (
                        frame is None
                        or frame.shape[1] != width
                        or frame.shape[0] != height
                    ):
                        raise ValueError(
                            f"invalid PNG stream frame {width}x{height}"
                        )
                frame = self._normalize_content_frame(frame)
                signature = self._frame_signature(frame)
                self.mac_window.update_frame_size(width, height)
                measured_fps = None
                with self._frame_condition:
                    if process is not self._process:
                        break
                    self._latest_frame = frame
                    frame_time = time.monotonic()
                    self._last_frame_at = frame_time
                    self._frame_sequence += 1
                    self._latest_signature = signature
                    if (
                        self._stream_mode == "stream"
                        and not self._reported_stream_fps
                    ):
                        if self._fps_sample_started == 0:
                            self._fps_sample_started = frame_time
                            self._fps_sample_sequence = self._frame_sequence
                        elif frame_time - self._fps_sample_started >= 2:
                            measured_fps = (
                                self._frame_sequence
                                - self._fps_sample_sequence
                            ) / (frame_time - self._fps_sample_started)
                            self._reported_stream_fps = True
                    if (
                        self._pending_interaction_at
                        and signature != self._pending_interaction_signature
                    ):
                        self._pending_interaction_at = 0.0
                        self._pending_interaction_sequence = 0
                        self._pending_interaction_signature = None
                    self._size = (width, height)
                    self._frame_condition.notify_all()
                if measured_fps is not None:
                    logger.info(
                        "ScreenCaptureKit live capture measured "
                        f"{measured_fps:.1f} FPS "
                        f"(configured {self.fps} FPS)"
                    )
        except Exception as exc:
            self._last_error = str(exc)
            logger.error(f"ScreenCaptureKit frame reader stopped: {exc}")
        finally:
            with self._frame_condition:
                self._frame_condition.notify_all()

    def _read_stderr(self):
        process = self._process
        if process is None or process.stderr is None:
            return
        raw = process.stderr.read()
        if raw:
            self._last_error = raw.decode("utf-8", errors="replace").strip()
            logger.error(f"ScreenCaptureKit helper: {self._last_error}")
            if (
                self._stream_mode == "stream"
                and process is self._process
            ):
                self._stream_mode = "screencapture"
                self._fallback_requested = True
                logger.warning(
                    "ScreenCaptureKit live stream failed; "
                    "fall back to macOS screencapture"
                )
            with self._frame_condition:
                self._frame_condition.notify_all()

    def _capture_system_snapshot(self):
        """Capture one window frame with macOS's stable system utility."""
        now = time.monotonic()
        with self._frame_condition:
            if (
                self._latest_frame is not None
                and now - self._last_frame_at < self._system_snapshot_interval
            ):
                return self._latest_frame

        window_id = self.mac_window.window_id
        if not window_id:
            self.mac_window.do_update_window_size()
            window_id = self.mac_window.window_id
        if not window_id:
            return None

        descriptor, path = tempfile.mkstemp(
            prefix="ok-macos-window-",
            suffix=".png",
        )
        os.close(descriptor)
        try:
            result = subprocess.run(
                [
                    "/usr/sbin/screencapture",
                    "-x",
                    "-l",
                    str(window_id),
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if result.returncode != 0:
                self._last_error = (result.stderr or result.stdout).strip()
                logger.error(
                    "macOS screencapture failed"
                    + (f": {self._last_error}" if self._last_error else "")
                )
                return None
            frame = cv2.imread(path, cv2.IMREAD_COLOR)
            if frame is None:
                self._last_error = "macOS screencapture returned an invalid PNG"
                logger.error(self._last_error)
                return None

            frame = self._normalize_content_frame(frame)
            height, width = frame.shape[:2]
            signature = self._frame_signature(frame)
            self.mac_window.update_frame_size(width, height)
            with self._frame_condition:
                self._latest_frame = frame
                self._last_frame_at = time.monotonic()
                self._frame_sequence += 1
                self._latest_signature = signature
                if (
                    self._pending_interaction_at
                    and signature != self._pending_interaction_signature
                ):
                        self._pending_interaction_at = 0.0
                        self._pending_interaction_sequence = 0
                        self._pending_interaction_signature = None
                self._size = (width, height)
                self._frame_condition.notify_all()
            return frame
        except subprocess.TimeoutExpired:
            self._last_error = "macOS screencapture timed out"
            logger.error(self._last_error)
            return None
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def do_get_frame(self):
        if self.exit_event.is_set():
            return None
        current_id = self.mac_window.window_id
        if current_id and current_id != self._stream_window_id:
            self.close()
        with self._frame_condition:
            now = time.monotonic()
            frame_age = (
                now - self._last_frame_at
                if self._latest_frame is not None and self._last_frame_at
                else 0
            )
            interaction_age = (
                now - self._pending_interaction_at
                if self._pending_interaction_at
                and self._latest_signature == self._pending_interaction_signature
                else 0
            )
        stale_reason = ""
        if (
            self._stream_mode == "stream"
            and frame_age > self.stale_frame_timeout
        ):
            stale_reason = f"no frame received for {frame_age:.1f}s"
        elif interaction_age > self.interaction_refresh_timeout:
            stale_reason = (
                f"captured pixels did not change for {interaction_age:.1f}s "
                "after input"
            )
        if stale_reason:
            logger.warning(
                f"ScreenCaptureKit frame stalled ({stale_reason}); "
                "restart the window stream"
            )
            self.close()

        with self._frame_condition:
            fallback_requested = self._fallback_requested
            self._fallback_requested = False
        if fallback_requested:
            logger.info(
                "replace interrupted live stream with macOS screencapture fallback"
            )
            self.close()

        # A failed SCStream reports -3805 from the stderr reader while this
        # method is waiting for its first frame. StartController does not ask
        # for another frame after that initial None result, so perform the
        # system screenshot fallback inside the same request.
        for _attempt in range(2):
            if self._stream_mode == "screencapture":
                return self._capture_system_snapshot()
            if not self._start_stream():
                return None
            with self._frame_condition:
                if self._latest_frame is None and not self._fallback_requested:
                    self._frame_condition.wait_for(
                        lambda: (
                            self._latest_frame is not None
                            or self._fallback_requested
                        ),
                        timeout=self.first_frame_timeout,
                    )
                elif not self._fallback_requested:
                    required_sequence = max(
                        self._delivered_frame_sequence,
                        self._pending_interaction_sequence,
                    )
                    if self._frame_sequence <= required_sequence:
                        self._frame_condition.wait_for(
                            lambda: (
                                self._frame_sequence > required_sequence
                                or self._fallback_requested
                            ),
                            timeout=self._fresh_frame_timeout,
                        )
                frame = self._latest_frame
                if frame is not None:
                    self._delivered_frame_sequence = self._frame_sequence
                fallback_requested = self._fallback_requested
                self._fallback_requested = False
            if fallback_requested:
                logger.info(
                    "restart capture immediately with macOS screencapture fallback"
                )
                self.close()
                continue
            if frame is not None:
                return frame
            logger.warning(
                "ScreenCaptureKit did not produce a first frame; "
                "fall back to macOS screencapture"
            )
            self._stream_mode = "screencapture"
            self.close()
        return None

    def connected(self):
        process = self._process
        with self._frame_condition:
            fallback_requested = self._fallback_requested
        if self.mac_window.exists and (
            fallback_requested
            or process is None
            or process.poll() is not None
        ):
            self.do_get_frame()
            process = self._process
        if self._stream_mode == "screencapture":
            return bool(
                self.mac_window.exists
                and self._latest_frame is not None
            )
        return bool(
            self.mac_window.exists
            and process is not None
            and process.poll() is None
            and self._latest_frame is not None
        )

    def clickable(self):
        return self.mac_window.is_foreground()

    def get_abs_cords(self, x, y):
        if self._content_top_rows and self._raw_frame_height:
            normalized_height = self.height or self._raw_frame_height
            content_height = self._raw_frame_height - self._content_top_rows
            y = self._content_top_rows + y * content_height / normalized_height
        return self.mac_window.get_abs_cords(x, y)

    def close(self):
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        self._stream_window_id = 0
        self._content_top_rows = 0
        self._raw_frame_height = 0
        self._fps_sample_started = 0.0
        self._fps_sample_sequence = 0
        self._reported_stream_fps = False
        with self._frame_condition:
            self._latest_frame = None
            self._last_frame_at = 0.0
            self._latest_signature = None
            self._pending_interaction_at = 0.0
            self._pending_interaction_sequence = 0
            self._pending_interaction_signature = None
            self._size = (0, 0)
            self._frame_condition.notify_all()

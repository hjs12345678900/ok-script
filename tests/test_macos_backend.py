import importlib
import io
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from ok.device.capture_methods.macos import (
    FRAME_HEADER,
    PNG_FRAME_MAGIC,
    MacScreenCaptureMethod,
)
from ok.device.interaction_methods import macos as macos_interaction_module
from ok.device.interaction_methods.macos import MacForegroundInteraction
from ok.platform.macos import helper as helper_module
from ok.platform.macos.helper import MacOSHelper
from ok.platform.macos.window import MacWindow

screenshot_module = importlib.import_module("ok.gui.debug.Screenshot")
find_annotation_font = screenshot_module.find_annotation_font


class FakeHelper:
    def __init__(self, windows=None):
        self.windows = windows or []
        self.commands = []
        self.list_calls = 0

    def list_windows(self, on_screen_only=True):
        self.list_calls += 1
        return list(self.windows)

    def run(self, *arguments, **_kwargs):
        self.commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")


def window_record(**overrides):
    result = {
        "windowID": 123,
        "pid": 456,
        "bundleIdentifier": "com.example.game",
        "ownerName": "Wuthering Waves",
        "title": "",
        "x": 100,
        "y": 50,
        "width": 1200,
        "height": 800,
        "onScreen": True,
        "active": True,
    }
    result.update(overrides)
    return result


def make_window(helper):
    stopped = threading.Event()
    stopped.set()
    return MacWindow(
        stopped,
        owner_name=["Wuthering Waves", "鸣潮"],
        helper=helper,
    )


def test_mac_window_selects_matching_active_window_and_converts_retina_coordinates():
    helper = FakeHelper(
        [
            window_record(windowID=1, ownerName="Other", active=True),
            window_record(windowID=2, active=False, width=1600, height=900),
            window_record(windowID=3, active=True),
        ]
    )
    window = make_window(helper)
    assert window.window_id == 3
    assert window.exists
    assert window.visible

    window.update_frame_size(2400, 1600)
    assert window.get_abs_cords(1200, 800) == pytest.approx((700, 450))
    assert window.capture_scaling == pytest.approx(2)
    assert window.scaling == pytest.approx(1)


def test_macos_helper_terminates_orphaned_streams_across_local_caches(
    monkeypatch,
    tmp_path,
):
    executable = tmp_path / "ok-macos-helper"
    executable.touch()
    helper = MacOSHelper(executable=executable)
    MacOSHelper._cleaned_executables.discard(str(executable.resolve()))

    class FakeProcess:
        def __init__(self, pid, ppid, command):
            self.pid = pid
            self.info = {"pid": pid, "ppid": ppid, "cmdline": command}
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    exact_path = str(executable.resolve())
    orphan = FakeProcess(10, 1, [exact_path, "stream", "123", "30"])
    active = FakeProcess(11, 99, [exact_path, "stream", "123", "30"])
    snapshot_orphan = FakeProcess(
        14,
        1,
        [exact_path, "snapshot-stream", "123", "5"],
    )
    active_snapshot = FakeProcess(
        15,
        99,
        [exact_path, "snapshot-stream", "123", "5"],
    )
    other_helper = FakeProcess(
        12,
        1,
        [str(Path("/other/ok-macos-helper")), "stream", "123", "30"],
    )
    finite_command = FakeProcess(13, 1, [exact_path, "list", "--all"])
    processes = [
        orphan,
        active,
        snapshot_orphan,
        active_snapshot,
        other_helper,
        finite_command,
    ]

    monkeypatch.setattr(helper_module.psutil, "process_iter", lambda _attrs: processes)
    monkeypatch.setattr(
        helper_module.psutil,
        "wait_procs",
        lambda candidates, timeout: (list(candidates), []),
    )

    assert helper.terminate_orphan_streams() == [10, 14, 12]
    assert orphan.terminated
    assert not active.terminated
    assert snapshot_orphan.terminated
    assert not active_snapshot.terminated
    assert other_helper.terminated
    assert not finite_command.terminated


def test_macos_helper_lists_windows_through_quartz_without_helper_command(
    monkeypatch,
    tmp_path,
):
    executable = tmp_path / "ok-macos-helper"
    executable.touch()
    helper = MacOSHelper(executable=executable)
    expected = [window_record()]
    calls = []

    monkeypatch.setattr(helper, "terminate_orphan_streams", lambda: [])
    monkeypatch.setattr(
        helper_module,
        "_quartz_window_records",
        lambda on_screen_only: calls.append(on_screen_only) or expected,
    )
    monkeypatch.setattr(
        helper,
        "run_json",
        lambda *_args, **_kwargs: pytest.fail(
            "window discovery must not invoke ScreenCaptureKit helper"
        ),
    )

    assert helper.list_windows(on_screen_only=False) == expected
    assert calls == [False]


def test_macos_helper_orphan_cleanup_degrades_when_process_listing_is_denied(
    monkeypatch,
    tmp_path,
):
    executable = tmp_path / "ok-macos-helper"
    executable.touch()
    helper = MacOSHelper(executable=executable)
    MacOSHelper._cleaned_executables.discard(str(executable.resolve()))
    monkeypatch.setattr(
        helper_module.psutil,
        "process_iter",
        lambda _attrs: (_ for _ in ()).throw(PermissionError("denied")),
    )

    assert helper.terminate_orphan_streams() == []


class FakeProcess:
    def __init__(self, payload):
        self.stdout = io.BytesIO(payload)
        self.stderr = io.BytesIO()

    def poll(self):
        return None


def test_screen_capture_prefers_configured_live_stream_by_default():
    helper = FakeHelper([window_record()])
    window = make_window(helper)
    capture = MacScreenCaptureMethod(
        window,
        threading.Event(),
        helper=helper,
        fps=30,
    )

    assert capture._stream_mode == "stream"
    assert capture._fresh_frame_timeout == pytest.approx(0.1)


def test_screen_capture_reader_removes_bgra_alpha_and_row_padding():
    helper = FakeHelper([window_record()])
    window = make_window(helper)
    stopped = threading.Event()
    capture = MacScreenCaptureMethod(window, stopped, helper=helper)

    # Two visible BGRA pixels plus one padded BGRA pixel.
    payload = bytes(
        [
            1,
            2,
            3,
            255,
            4,
            5,
            6,
            255,
            90,
            91,
            92,
            93,
        ]
    )
    header = FRAME_HEADER.pack(b"OKFR", 2, 1, 12, len(payload))
    capture._process = FakeProcess(header + payload)
    capture._read_frames()

    assert capture._latest_frame.shape == (1, 2, 3)
    np.testing.assert_array_equal(
        capture._latest_frame,
        np.array([[[1, 2, 3], [4, 5, 6]]], dtype=np.uint8),
    )
    assert window.capture_width == 2
    assert window.capture_height == 1
    assert capture._last_frame_at > 0
    assert capture._frame_sequence == 1


def test_screen_capture_reader_decodes_snapshot_stream_png():
    helper = FakeHelper([window_record()])
    window = make_window(helper)
    stopped = threading.Event()
    capture = MacScreenCaptureMethod(window, stopped, helper=helper)

    expected = np.array(
        [
            [[10, 20, 30], [40, 50, 60]],
            [[70, 80, 90], [100, 110, 120]],
        ],
        dtype=np.uint8,
    )
    encoded, png = cv2.imencode(".png", expected)
    assert encoded
    payload = png.tobytes()
    header = FRAME_HEADER.pack(PNG_FRAME_MAGIC, 2, 2, 0, len(payload))
    capture._process = FakeProcess(header + payload)
    capture._read_frames()

    np.testing.assert_array_equal(capture._latest_frame, expected)
    assert capture._frame_sequence == 1


def test_screen_capture_falls_back_after_scstream_connection_interruption():
    helper = FakeHelper([window_record()])
    window = make_window(helper)
    stopped = threading.Event()
    capture = MacScreenCaptureMethod(window, stopped, helper=helper)
    capture._stream_mode = "stream"
    capture._process = type(
        "FailedProcess",
        (),
        {
            "stderr": io.BytesIO(
                b"Error Domain=com.apple.ScreenCaptureKit.SCStreamErrorDomain "
                b"Code=-3805 application connection interrupted"
            ),
        },
    )()

    capture._read_stderr()

    assert capture._stream_mode == "screencapture"
    assert capture._fallback_requested


def test_first_frame_request_starts_system_fallback_immediately(monkeypatch):
    helper = FakeHelper([window_record()])
    window = make_window(helper)
    stopped = threading.Event()
    capture = MacScreenCaptureMethod(
        window,
        stopped,
        helper=helper,
        first_frame_timeout=0,
    )
    capture._stream_mode = "stream"
    expected = np.ones((2, 2, 3), dtype=np.uint8)
    calls = []
    capture._stream_window_id = window.window_id

    def fake_start_stream():
        calls.append(("start", capture._stream_mode))
        capture._stream_mode = "screencapture"
        capture._fallback_requested = True
        return True

    def fake_close():
        calls.append(("close",))
        capture._latest_frame = None

    def fake_system_snapshot():
        calls.append(("system",))
        capture._latest_frame = expected
        return expected

    monkeypatch.setattr(capture, "_start_stream", fake_start_stream)
    monkeypatch.setattr(capture, "close", fake_close)
    monkeypatch.setattr(
        capture,
        "_capture_system_snapshot",
        fake_system_snapshot,
    )

    assert capture.do_get_frame() is expected
    assert calls == [
        ("start", "stream"),
        ("close",),
        ("system",),
    ]


def test_connected_completes_pending_system_fallback(monkeypatch):
    helper = FakeHelper([window_record()])
    window = make_window(helper)
    stopped = threading.Event()
    capture = MacScreenCaptureMethod(window, stopped, helper=helper)
    expected = np.ones((2, 2, 3), dtype=np.uint8)
    calls = []

    class RunningProcess:
        def poll(self):
            return None

    capture._process = RunningProcess()
    capture._latest_frame = np.zeros((2, 2, 3), dtype=np.uint8)
    capture._fallback_requested = True

    def fake_get_frame():
        calls.append("get_frame")
        capture._fallback_requested = False
        capture._stream_mode = "screencapture"
        capture._latest_frame = expected
        capture._process = None
        return expected

    monkeypatch.setattr(capture, "do_get_frame", fake_get_frame)

    assert capture.connected()
    assert calls == ["get_frame"]


def test_system_screencapture_decodes_window_png(monkeypatch):
    helper = FakeHelper([window_record(width=1920, height=1080)])
    window = make_window(helper)
    stopped = threading.Event()
    capture = MacScreenCaptureMethod(window, stopped, helper=helper)
    capture._stream_mode = "screencapture"
    expected = np.full((1080, 1920, 3), 77, dtype=np.uint8)
    commands = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(cv2, "imread", lambda *_args, **_kwargs: expected)

    frame = capture._capture_system_snapshot()

    np.testing.assert_array_equal(frame, expected)
    assert commands[0][0][:4] == [
        "/usr/sbin/screencapture",
        "-x",
        "-l",
        "123",
    ]
    assert commands[0][1]["timeout"] == 3
    assert capture.width == 1920
    assert capture.height == 1080


def test_screen_capture_normalizes_top_border_and_maps_clicks_to_content():
    helper = FakeHelper([window_record(width=1920, height=1080)])
    window = make_window(helper)
    stopped = threading.Event()
    capture = MacScreenCaptureMethod(window, stopped, helper=helper)

    frame = np.full((1080, 1920, 3), 120, dtype=np.uint8)
    frame[:30, :, :] = 0
    normalized = capture._normalize_content_frame(frame)
    window.update_frame_size(1920, 1080)
    capture._size = (1920, 1080)

    assert capture._content_top_rows == 30
    assert normalized.shape == frame.shape
    assert normalized[0].mean() > 100
    assert capture.get_abs_cords(960, 54) == pytest.approx(
        (1060, 50 + 82.5),
    )


def test_healthy_screen_capture_stream_does_not_reenumerate_windows():
    helper = FakeHelper([window_record()])
    window = make_window(helper)
    stopped = threading.Event()
    capture = MacScreenCaptureMethod(window, stopped, helper=helper)
    capture._process = type(
        "RunningProcess",
        (),
        {"poll": lambda self: None},
    )()
    capture._stream_window_id = window.window_id
    initial_list_calls = helper.list_calls

    assert capture._start_stream()
    assert helper.list_calls == initial_list_calls


def test_live_stream_waits_for_a_new_frame_after_input():
    helper = FakeHelper([window_record()])
    window = make_window(helper)
    stopped = threading.Event()
    capture = MacScreenCaptureMethod(
        window,
        stopped,
        helper=helper,
        fps=30,
    )
    old_frame = np.zeros((2, 2, 3), dtype=np.uint8)
    new_frame = np.ones((2, 2, 3), dtype=np.uint8)
    capture._process = type(
        "RunningProcess",
        (),
        {"poll": lambda self: None},
    )()
    capture._stream_window_id = window.window_id
    capture._latest_frame = old_frame
    capture._latest_signature = capture._frame_signature(old_frame)
    capture._last_frame_at = time.monotonic()
    capture._frame_sequence = 1
    capture._delivered_frame_sequence = 1
    capture.note_interaction()

    def publish_frame():
        time.sleep(0.02)
        with capture._frame_condition:
            capture._latest_frame = new_frame
            capture._latest_signature = capture._frame_signature(new_frame)
            capture._last_frame_at = time.monotonic()
            capture._frame_sequence = 2
            capture._pending_interaction_at = 0
            capture._pending_interaction_sequence = 0
            capture._pending_interaction_signature = None
            capture._frame_condition.notify_all()

    publisher = threading.Thread(target=publish_frame)
    publisher.start()
    frame = capture.do_get_frame()
    publisher.join(timeout=1)

    assert frame is new_frame
    assert capture._delivered_frame_sequence == 2


def test_stalled_screen_capture_stream_restarts_before_returning_frame(monkeypatch):
    helper = FakeHelper([window_record()])
    window = make_window(helper)
    stopped = threading.Event()
    capture = MacScreenCaptureMethod(
        window,
        stopped,
        helper=helper,
        stale_frame_timeout=1,
    )
    capture._stream_mode = "stream"
    old_frame = np.zeros((2, 2, 3), dtype=np.uint8)
    new_frame = np.ones((2, 2, 3), dtype=np.uint8)
    capture._latest_frame = old_frame
    capture._last_frame_at = 1
    capture._stream_window_id = window.window_id
    calls = []

    def fake_close():
        calls.append("close")
        capture._latest_frame = None
        capture._last_frame_at = 0
        capture._stream_window_id = 0

    def fake_start_stream():
        calls.append("start")
        capture._latest_frame = new_frame
        capture._last_frame_at = time.monotonic()
        capture._stream_window_id = window.window_id
        return True

    monkeypatch.setattr(capture, "close", fake_close)
    monkeypatch.setattr(capture, "_start_stream", fake_start_stream)

    assert capture.do_get_frame() is new_frame
    assert calls == ["close", "start"]


def test_unchanged_capture_after_input_restarts_even_when_frames_are_arriving(
    monkeypatch,
):
    helper = FakeHelper([window_record()])
    window = make_window(helper)
    stopped = threading.Event()
    capture = MacScreenCaptureMethod(
        window,
        stopped,
        helper=helper,
        interaction_refresh_timeout=0.5,
    )
    capture._stream_mode = "stream"
    old_frame = np.zeros((2, 2, 3), dtype=np.uint8)
    new_frame = np.ones((2, 2, 3), dtype=np.uint8)
    capture._latest_frame = old_frame
    capture._latest_signature = capture._frame_signature(old_frame)
    capture._last_frame_at = time.monotonic()
    capture._stream_window_id = window.window_id
    capture.note_interaction()
    capture._pending_interaction_at = time.monotonic() - 1
    calls = []

    def fake_close():
        calls.append("close")
        capture._latest_frame = None
        capture._last_frame_at = 0
        capture._latest_signature = None
        capture._pending_interaction_at = 0
        capture._pending_interaction_signature = None
        capture._stream_window_id = 0

    def fake_start_stream():
        calls.append("start")
        capture._latest_frame = new_frame
        capture._latest_signature = capture._frame_signature(new_frame)
        capture._last_frame_at = time.monotonic()
        capture._stream_window_id = window.window_id
        return True

    monkeypatch.setattr(capture, "close", fake_close)
    monkeypatch.setattr(capture, "_start_stream", fake_start_stream)

    assert capture.do_get_frame() is new_frame
    assert calls == ["close", "start"]


class FakeCapture:
    width = 1200
    height = 800

    def get_abs_cords(self, x, y):
        return 100 + x / 2, 50 + y / 2


def test_in_process_quartz_posts_mouse_and_keyboard_events(monkeypatch):
    created = []
    posted = []

    class Event:
        flags = 0

    class FakeQuartz:
        kCGEventFlagMaskAlternate = 1
        kCGEventFlagMaskControl = 2
        kCGEventFlagMaskShift = 4
        kCGEventFlagMaskCommand = 8
        kCGEventMouseMoved = 10
        kCGEventLeftMouseDown = 11
        kCGEventLeftMouseUp = 12
        kCGEventRightMouseDown = 13
        kCGEventRightMouseUp = 14
        kCGEventOtherMouseDown = 15
        kCGEventOtherMouseUp = 16
        kCGHIDEventTap = 20

        @staticmethod
        def CGEventCreateMouseEvent(_source, event_type, point, button):
            event = Event()
            created.append(("mouse", event_type, point, button, event))
            return event

        @staticmethod
        def CGEventCreateKeyboardEvent(_source, key_code, is_down):
            event = Event()
            created.append(("key", key_code, is_down, event))
            return event

        @staticmethod
        def CGEventSetFlags(event, flags):
            event.flags = flags

        @staticmethod
        def CGEventPost(tap, event):
            posted.append((tap, event))

    monkeypatch.setitem(sys.modules, "Quartz", FakeQuartz)
    monkeypatch.setattr(
        macos_interaction_module.time,
        "sleep",
        lambda _seconds: None,
    )

    assert macos_interaction_module._post_quartz_mouse(
        "click",
        100,
        200,
        "left",
        0.01,
        {"alt"},
    )
    assert macos_interaction_module._post_quartz_key(
        "press",
        "e",
        0.01,
        {"shift"},
    )

    assert [event[1] for event in created[:3]] == [10, 11, 12]
    assert all(event[-1].flags == 1 for event in created[:3])
    assert created[3][1:3] == (14, True)
    assert created[4][1:3] == (14, False)
    assert created[3][-1].flags == 4
    assert created[4][-1].flags == 4
    assert len(posted) == 5


def test_in_process_quartz_explicitly_clears_unrequested_key_modifiers(
    monkeypatch,
):
    created = []

    class Event:
        # Model a null Quartz source inheriting Fn before CGEventSetFlags.
        flags = 1 << 23

    class FakeQuartz:
        kCGEventFlagMaskAlternate = 1
        kCGEventFlagMaskControl = 2
        kCGEventFlagMaskShift = 4
        kCGEventFlagMaskCommand = 8
        kCGHIDEventTap = 20

        @staticmethod
        def CGEventCreateKeyboardEvent(_source, key_code, is_down):
            event = Event()
            created.append((key_code, is_down, event))
            return event

        @staticmethod
        def CGEventSetFlags(event, flags):
            event.flags = flags

        @staticmethod
        def CGEventPost(_tap, _event):
            pass

    monkeypatch.setitem(sys.modules, "Quartz", FakeQuartz)
    monkeypatch.setattr(
        macos_interaction_module.time,
        "sleep",
        lambda _seconds: None,
    )

    assert macos_interaction_module._post_quartz_key(
        "press",
        "q",
        0.01,
        set(),
    )
    assert [event.flags for _, _, event in created] == [0, 0]


def test_ok_root_keeps_stdlib_platform_alias_after_platform_package_import():
    import platform as stdlib_platform

    import ok
    import ok.platform.macos

    assert ok.platform is not stdlib_platform
    assert ok.py_platform is stdlib_platform
    assert callable(ok.py_platform.version)


def test_foreground_interaction_maps_frame_coordinates_and_blocks_background_input():
    helper = FakeHelper()
    window = type(
        "Window",
        (),
        {
            "helper": helper,
            "is_foreground": lambda self: True,
            "bring_to_front": lambda self: True,
        },
    )()
    interaction = MacForegroundInteraction(FakeCapture(), window, helper=helper)

    interaction.click(200, 100, down_time=0.03)
    interaction.send_key("page_up")
    interaction.swipe(0, 0, 100, 50, duration=0.5)
    assert helper.commands[0] == ("mouse", "click", 200.0, 100.0, "left", 30)
    assert helper.commands[1] == ("key", "press", "pageup", 20)
    assert helper.commands[2] == (
        "mouse",
        "swipe",
        100.0,
        50.0,
        150.0,
        75.0,
        500,
    )


def test_foreground_interaction_keeps_modifier_attached_to_click():
    helper = FakeHelper()
    window = type(
        "Window",
        (),
        {
            "helper": helper,
            "is_foreground": lambda self: True,
            "bring_to_front": lambda self: True,
        },
    )()
    interaction = MacForegroundInteraction(FakeCapture(), window, helper=helper)

    interaction.send_key_down("alt")
    interaction.click(200, 100, down_time=0.03)
    interaction.send_key_up("alt")

    assert helper.commands == [
        ("key", "down", "alt"),
        ("mouse", "modified-click", 200.0, 100.0, "left", 30, "alt"),
        ("key", "up", "alt"),
    ]


def test_foreground_interaction_centres_coordinate_free_combat_mouse_actions(
    monkeypatch,
):
    helper = FakeHelper()
    window = type(
        "Window",
        (),
        {
            "helper": helper,
            "is_foreground": lambda self: True,
            "bring_to_front": lambda self: True,
        },
    )()
    monkeypatch.setattr(
        macos_interaction_module,
        "_cursor_position",
        lambda: (350.0, 450.0),
    )
    interaction = MacForegroundInteraction(FakeCapture(), window, helper=helper)

    interaction.click()
    interaction.mouse_down()
    interaction.mouse_up()

    assert helper.commands == [
        ("mouse", "click", 400.0, 250.0, "left", 20),
        ("mouse", "down", 400.0, 250.0, "left"),
        ("mouse", "up", 400.0, 250.0, "left"),
    ]


def test_foreground_interaction_uses_game_centre_when_cursor_is_unavailable(
    monkeypatch,
):
    helper = FakeHelper()
    window = type(
        "Window",
        (),
        {
            "helper": helper,
            "is_foreground": lambda self: True,
            "bring_to_front": lambda self: True,
        },
    )()
    monkeypatch.setattr(
        macos_interaction_module,
        "_cursor_position",
        lambda: None,
    )
    interaction = MacForegroundInteraction(FakeCapture(), window, helper=helper)

    interaction.click()

    assert helper.commands == [
        ("mouse", "click", 400.0, 250.0, "left", 20),
    ]


def test_foreground_interaction_uses_in_process_quartz_for_combat_input(
    monkeypatch,
):
    helper = FakeHelper()
    window = type(
        "Window",
        (),
        {
            "helper": helper,
            "is_foreground": lambda self: True,
            "bring_to_front": lambda self: True,
        },
    )()
    direct_calls = []
    direct_key_calls = []
    monkeypatch.setattr(
        macos_interaction_module,
        "_post_quartz_mouse",
        lambda *args: direct_calls.append(args) or True,
    )
    monkeypatch.setattr(
        macos_interaction_module,
        "_post_quartz_key",
        lambda *args: direct_key_calls.append(args) or True,
    )
    monkeypatch.setattr(
        macos_interaction_module,
        "_cursor_position",
        lambda: (350.0, 450.0),
    )
    interaction = MacForegroundInteraction(
        FakeCapture(),
        window,
        helper=helper,
        use_direct_mouse=True,
        use_direct_keys=True,
    )

    interaction.click(down_time=0.01)
    interaction.send_key("e", down_time=0.01)
    interaction.send_key_down("alt")
    interaction.send_key_up("alt")
    interaction.mouse_down()
    interaction.mouse_up()

    assert direct_calls == [
        ("click", 400.0, 250.0, "left", 0.01, set()),
        ("down", 400.0, 250.0, "left", 0, set()),
        ("up", 400.0, 250.0, "left", 0, set()),
    ]
    assert direct_key_calls == [
        ("press", "e", 0.01, set()),
        ("down", "alt", 0, set()),
        ("up", "alt", 0, set()),
    ]
    assert helper.commands == []


def test_direct_quartz_combat_input_is_blocked_when_game_loses_foreground(
    monkeypatch,
):
    helper = FakeHelper()
    foreground = {"value": True}
    window = type(
        "Window",
        (),
        {
            "helper": helper,
            "is_foreground": lambda self: foreground["value"],
            "bring_to_front": lambda self: True,
        },
    )()
    direct_mouse_calls = []
    direct_key_calls = []
    monkeypatch.setattr(
        macos_interaction_module,
        "_post_quartz_mouse",
        lambda *args: direct_mouse_calls.append(args) or True,
    )
    monkeypatch.setattr(
        macos_interaction_module,
        "_post_quartz_key",
        lambda *args: direct_key_calls.append(args) or True,
    )
    interaction = MacForegroundInteraction(
        FakeCapture(),
        window,
        helper=helper,
        use_direct_mouse=True,
        use_direct_keys=True,
    )

    foreground["value"] = False
    interaction.click()
    interaction.mouse_down()
    interaction.send_key("e")
    interaction.send_key_down("alt")

    assert direct_mouse_calls == []
    assert direct_key_calls == []
    assert helper.commands == []


def test_foreground_interaction_falls_back_when_direct_quartz_fails(
    monkeypatch,
):
    helper = FakeHelper()
    window = type(
        "Window",
        (),
        {
            "helper": helper,
            "is_foreground": lambda self: True,
            "bring_to_front": lambda self: True,
        },
    )()
    monkeypatch.setattr(
        macos_interaction_module,
        "_post_quartz_mouse",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        macos_interaction_module,
        "_post_quartz_key",
        lambda *_args: False,
    )
    interaction = MacForegroundInteraction(
        FakeCapture(),
        window,
        helper=helper,
        use_direct_mouse=True,
        use_direct_keys=True,
    )

    interaction.click(200, 100, down_time=0.03)
    interaction.send_key("e", down_time=0.03)

    assert helper.commands == [
        ("mouse", "click", 200.0, 100.0, "left", 30),
        ("key", "press", "e", 30),
    ]


def test_foreground_interaction_clears_modifier_even_when_key_up_fails():
    class FailingKeyUpHelper(FakeHelper):
        def run(self, *arguments, **_kwargs):
            self.commands.append(arguments)
            if arguments[:2] == ("key", "up"):
                raise helper_module.MacOSHelperError("key up failed")
            return subprocess.CompletedProcess(arguments, 0, "", "")

    helper = FailingKeyUpHelper()
    window = type(
        "Window",
        (),
        {
            "helper": helper,
            "is_foreground": lambda self: True,
            "bring_to_front": lambda self: True,
        },
    )()
    interaction = MacForegroundInteraction(FakeCapture(), window, helper=helper)

    interaction.send_key_down("alt")
    interaction.send_key_up("alt")
    interaction.click(200, 100)

    assert helper.commands[-1] == (
        "mouse",
        "click",
        200.0,
        100.0,
        "left",
        20,
    )

    window.is_foreground = lambda: False
    interaction.send_key("f2")
    assert len(helper.commands) == 3


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS build only")
def test_native_helper_builds_and_reports_boolean_permissions():
    status = MacOSHelper().permissions(prompt=False)
    assert set(status) == {"accessibility", "screenRecording"}
    assert all(isinstance(value, bool) for value in status.values())


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS system font only")
def test_macos_screenshot_font_is_available():
    assert find_annotation_font() == "/System/Library/Fonts/Hiragino Sans GB.ttc"


def test_screenshot_font_lookup_does_not_require_windows_environment(monkeypatch):
    expected_font = "/System/Library/Fonts/Hiragino Sans GB.ttc"
    monkeypatch.delenv("WINDIR", raising=False)
    monkeypatch.setattr(screenshot_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        screenshot_module.os.path,
        "isfile",
        lambda path: path == expected_font,
    )

    assert find_annotation_font() == expected_font


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows import guard")
def test_gui_hotkey_modules_import_without_windows_ctypes():
    debug_tab = importlib.import_module("ok.gui.debug.DebugTab")
    start_card = importlib.import_module("ok.gui.start.StartCard")

    assert debug_tab.windll is None
    assert start_card.windll is None

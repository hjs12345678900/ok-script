"""Build and invoke the native macOS ScreenCaptureKit/CGEvent helper."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

import psutil

from ok.util.logger import Logger

logger = Logger.get_logger(__name__)


def _quartz_window_records(on_screen_only: bool) -> list[dict[str, Any]]:
    """List macOS windows without creating a ScreenCaptureKit connection."""
    import AppKit
    import Quartz

    options = Quartz.kCGWindowListExcludeDesktopElements
    if on_screen_only:
        options |= Quartz.kCGWindowListOptionOnScreenOnly
    else:
        options |= Quartz.kCGWindowListOptionAll
    windows = Quartz.CGWindowListCopyWindowInfo(
        options,
        Quartz.kCGNullWindowID,
    ) or []
    frontmost = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    active_pid = int(frontmost.processIdentifier()) if frontmost else 0
    records = []
    for value in windows:
        bounds = value.get(Quartz.kCGWindowBounds) or {}
        pid = int(value.get(Quartz.kCGWindowOwnerPID) or 0)
        application = (
            AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(
                pid
            )
            if pid
            else None
        )
        records.append(
            {
                "windowID": int(value.get(Quartz.kCGWindowNumber) or 0),
                "pid": pid,
                "bundleIdentifier": (
                    str(application.bundleIdentifier() or "")
                    if application
                    else ""
                ),
                "ownerName": str(value.get(Quartz.kCGWindowOwnerName) or ""),
                "title": str(value.get(Quartz.kCGWindowName) or ""),
                "x": float(bounds.get("X") or 0),
                "y": float(bounds.get("Y") or 0),
                "width": float(bounds.get("Width") or 0),
                "height": float(bounds.get("Height") or 0),
                "onScreen": bool(value.get(Quartz.kCGWindowIsOnscreen, False)),
                "active": pid == active_pid,
            }
        )
    return records


class MacOSHelperError(RuntimeError):
    """Raised when the native helper cannot be built or invoked."""


class MacOSHelper:
    """Locate, build, and invoke the native macOS helper executable.

    The helper is compiled on first use instead of shipping an unsigned binary.
    Set ``OK_MACOS_HELPER`` to use a prebuilt and signed helper in packaged apps.
    """

    _build_lock = threading.Lock()
    _orphan_cleanup_lock = threading.Lock()
    _cleaned_executables: set[str] = set()

    def __init__(self, executable: str | os.PathLike[str] | None = None):
        configured = executable or os.environ.get("OK_MACOS_HELPER")
        self._configured_executable = Path(configured).expanduser() if configured else None
        self.source_path = Path(__file__).with_name("native_helper.swift")
        self.cache_path = Path("cache") / "macos" / "ok-macos-helper"

    @property
    def executable(self) -> Path:
        """Return a working helper path, compiling the bundled Swift source if needed."""
        if self._configured_executable is not None:
            path = self._configured_executable.resolve()
            if not path.is_file():
                raise MacOSHelperError(f"Configured macOS helper does not exist: {path}")
            return path

        path = self.cache_path.resolve()
        source_mtime = self.source_path.stat().st_mtime
        if path.is_file() and path.stat().st_mtime >= source_mtime:
            return path

        with self._build_lock:
            if path.is_file() and path.stat().st_mtime >= source_mtime:
                return path
            path.parent.mkdir(parents=True, exist_ok=True)
            module_cache = path.parent / "swift-module-cache"
            module_cache.mkdir(parents=True, exist_ok=True)
            base_command = [
                "swiftc",
                "-O",
                "-parse-as-library",
                "-module-cache-path",
                str(module_cache),
                str(self.source_path),
                "-o",
                str(path),
                "-framework",
                "AppKit",
                "-framework",
                "ApplicationServices",
                "-framework",
                "CoreMedia",
                "-framework",
                "CoreVideo",
                "-framework",
                "ScreenCaptureKit",
            ]
            logger.info("building native macOS helper")
            configured_sdk = os.environ.get("OK_MACOS_SDK")
            fallback_sdk = Path(
                "/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk"
            )
            sdk_candidates: list[str | None] = [configured_sdk] if configured_sdk else [None]
            if not configured_sdk and fallback_sdk.is_dir():
                sdk_candidates.append(str(fallback_sdk))
            result = None
            try:
                for sdk in sdk_candidates:
                    command = list(base_command)
                    if sdk:
                        command[1:1] = ["-sdk", sdk]
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if result.returncode == 0:
                        break
            except FileNotFoundError as exc:
                raise MacOSHelperError(
                    "swiftc is required to build the macOS helper; install Xcode Command Line Tools "
                    "or set OK_MACOS_HELPER to a prebuilt executable"
                ) from exc
            if result is None or result.returncode != 0:
                detail = ((result.stderr or result.stdout) if result else "").strip()
                raise MacOSHelperError(f"Failed to build macOS helper: {detail}")
        return path

    def command(self, *arguments: object) -> list[str]:
        """Build an argv list for the native helper."""
        return [str(self.executable), *(str(argument) for argument in arguments)]

    def run(
        self,
        *arguments: object,
        timeout: float = 10,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a finite helper command and return its captured result."""
        result = subprocess.run(
            self.command(*arguments),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise MacOSHelperError(
                f"macOS helper command failed ({result.returncode}): {detail}"
            )
        return result

    def run_json(self, *arguments: object, timeout: float = 10) -> Any:
        """Run a helper command whose stdout is a JSON document."""
        result = self.run(*arguments, timeout=timeout)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise MacOSHelperError(
                f"macOS helper returned invalid JSON: {result.stdout[:200]!r}"
            ) from exc

    def terminate_orphan_streams(self) -> list[int]:
        """Stop stale streams from an earlier crashed process.

        Only native helpers named ``ok-macos-helper``, running a long-lived
        ``stream`` or ``snapshot-stream`` command, and reparented to launchd
        (PPID 1) are eligible.
        This intentionally spans project-local cache directories: the same
        source tree can be launched from OK-WW and ok-script, and an orphan
        from either cache can otherwise keep ScreenCaptureKit occupied.
        Active streams owned by a running OK-Script process are left untouched.
        """
        executable = str(self.executable)
        with self._orphan_cleanup_lock:
            if executable in self._cleaned_executables:
                return []

            orphaned = []
            try:
                for process in psutil.process_iter(["pid", "ppid", "cmdline"]):
                    try:
                        info = process.info
                        command = info.get("cmdline") or []
                        if (
                            info.get("ppid") == 1
                            and len(command) >= 2
                            and Path(command[0]).name == "ok-macos-helper"
                            and command[1] in {"stream", "snapshot-stream"}
                        ):
                            process.terminate()
                            orphaned.append(process)
                    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                        continue
            except (PermissionError, psutil.Error) as exc:
                logger.warning(f"could not inspect orphaned macOS capture streams: {exc}")
                self._cleaned_executables.add(executable)
                return []

            _, alive = psutil.wait_procs(orphaned, timeout=1)
            for process in alive:
                try:
                    process.kill()
                except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                    continue

            self._cleaned_executables.add(executable)

        terminated = [process.pid for process in orphaned]
        if terminated:
            logger.info(f"terminated orphaned macOS capture streams {terminated}")
        return terminated

    def permissions(self, prompt: bool = False) -> dict[str, bool]:
        """Return current Screen Recording and Accessibility authorization."""
        return self.run_json("permissions", "--prompt" if prompt else "--no-prompt")

    def list_windows(self, on_screen_only: bool = True) -> list[dict[str, Any]]:
        """Return macOS windows without opening a ScreenCaptureKit session."""
        self.terminate_orphan_streams()
        return _quartz_window_records(on_screen_only)

# Compatibility shim for the old ok.device.capture module.
import sys

from ok.device.capture_methods import *

if sys.platform == "win32":
    from ok.device.capture_methods import bitblt as _bitblt


def __getattr__(name):
    if name == "render_full" and sys.platform == "win32":
        return _bitblt.render_full
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

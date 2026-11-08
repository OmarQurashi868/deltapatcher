"""xdelta3 wrapper for diff/patch operations."""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# Path to xdelta3 executable (bundled with the app)
def _get_xdelta3_exe() -> Path:
    """Get path to xdelta3.exe, works both in development and when bundled by PyInstaller."""
    if getattr(sys, "frozen", False):
        # Running in PyInstaller bundle
        base = Path(sys._MEIPASS)
    else:
        # Running from source
        base = Path(__file__).resolve().parent.parent.parent
    return base / "xdelta3.exe"


def _run_xdelta(args: list[str], input_data: bytes | None = None) -> bytes:
    """Run xdelta3.exe with given args, return stdout."""
    cmd = [str(_get_xdelta3_exe())] + args
    log.debug("Running: %s", " ".join(cmd))
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(
        cmd,
        input=input_data,
        capture_output=True,
        check=False,
        creationflags=creationflags,
    )
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace") if result.stderr else "unknown error"
        raise RuntimeError(f"xdelta3 failed (exit {result.returncode}): {err}")
    return result.stdout


def diff(source: bytes, target: bytes) -> bytes:
    """Create xdelta3 patch from source to target."""
    with tempfile.NamedTemporaryFile(delete=False) as src_f, \
         tempfile.NamedTemporaryFile(delete=False) as tgt_f, \
         tempfile.NamedTemporaryFile(delete=False) as delta_f:
        src_f.write(source)
        tgt_f.write(target)
        src_path = src_f.name
        tgt_path = tgt_f.name
        delta_path = delta_f.name

    try:
        _run_xdelta(["-f", "-e", "-s", src_path, tgt_path, delta_path])
        with open(delta_path, "rb") as f:
            return f.read()
    finally:
        for p in (src_path, tgt_path, delta_path):
            try:
                Path(p).unlink()
            except OSError:
                pass


def patch(source: bytes, delta: bytes) -> bytes:
    """Apply xdelta3 patch to source, return target."""
    with tempfile.NamedTemporaryFile(delete=False) as src_f, \
         tempfile.NamedTemporaryFile(delete=False) as delta_f, \
         tempfile.NamedTemporaryFile(delete=False) as out_f:
        src_f.write(source)
        delta_f.write(delta)
        src_path = src_f.name
        delta_path = delta_f.name
        out_path = out_f.name

    try:
        _run_xdelta(["-f", "-d", "-s", src_path, delta_path, out_path])
        with open(out_path, "rb") as f:
            return f.read()
    finally:
        for p in (src_path, delta_path, out_path):
            try:
                Path(p).unlink()
            except OSError:
                pass
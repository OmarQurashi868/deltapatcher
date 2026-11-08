"""Builds a self-contained Applier exe for one patch.

Invokes PyInstaller fresh for every patch: builds deltapatcher/applier/applier_gui.py
with the freshly-generated patch.zip bundled in as a data file, producing a single
onefile/noconsole exe that needs no separate patch file or arguments to run.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

import PyInstaller.__main__

from ..core import patch_format

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPLIER_SCRIPT = PROJECT_ROOT / "deltapatcher" / "applier" / "applier_gui.py"

log = logging.getLogger(__name__)


def build_applier_exe(
    source_dir: Path,
    target_dir: Path,
    output_exe_path: Path,
    progress_callback: callable | None = None,
) -> None:
    """Diff source_dir -> target_dir and build a patcher exe at output_exe_path."""
    output_exe_path = Path(output_exe_path)
    if output_exe_path.suffix.lower() != ".exe":
        output_exe_path = output_exe_path.with_suffix(".exe")
    output_exe_path.parent.mkdir(parents=True, exist_ok=True)

    log.info("Building patch zip from %s -> %s", source_dir, target_dir)
    patch_zip_bytes = patch_format.build_patch_zip(source_dir, target_dir, progress_callback)
    log.info("Patch zip built: %d bytes", len(patch_zip_bytes))

    with tempfile.TemporaryDirectory(prefix="deltapatcher_build_") as tmp:
        tmp_path = Path(tmp)
        patch_zip_path = tmp_path / "patch.zip"
        patch_zip_path.write_bytes(patch_zip_bytes)

        dist_dir = tmp_path / "dist"
        work_dir = tmp_path / "build"
        exe_stem = output_exe_path.stem

        args = [
            str(APPLIER_SCRIPT),
            "--onefile",
            "--noconsole",
            "--name", exe_stem,
            "--distpath", str(dist_dir),
            "--workpath", str(work_dir),
            "--specpath", str(tmp_path),
            "--paths", str(PROJECT_ROOT),
            "--add-data", f"{patch_zip_path}{os.pathsep}.",
            "-y",
        ]

        log.info("Running PyInstaller...")
        PyInstaller.__main__.run(args)
        log.info("PyInstaller completed")

        built_exe = dist_dir / f"{exe_stem}.exe"
        if not built_exe.is_file():
            raise RuntimeError(f"PyInstaller did not produce expected output: {built_exe}")
        shutil.copy2(built_exe, output_exe_path)
        log.info("Built exe copied to %s", output_exe_path)

"""Verification and application of a patch against a live directory.

Verification rule: for every file the patch cares about, the file on disk
must currently match either the pre-patch (source) version or the
post-patch (target) version. Anything else is a mismatch and aborts the
whole operation before any writes happen.

Application is backup-before-overwrite: every file touched is copied into
a temp backup directory first. If anything fails partway through, all
changes made so far are rolled back from the backups. On success the
backups are discarded.
"""
from __future__ import annotations

import os
import shutil
import stat
import zipfile
from pathlib import Path

import bsdiff4

from . import patch_format, scan


def _ensure_writable(path: Path) -> None:
    """Clear the read-only attribute if set, so it can be overwritten/deleted/removed."""
    if path.exists():
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass


def _remove_readonly_and_retry(func, path, _exc_info):
    _ensure_writable(Path(path))
    try:
        func(path)
    except OSError:
        pass


def _rmtree_best_effort(path: Path) -> None:
    shutil.rmtree(path, onexc=_remove_readonly_and_retry)


def verify(disk_root: Path, manifest_files: dict) -> list[str]:
    """Return the sorted list of relpaths that are neither source nor target version."""
    disk_root = Path(disk_root)
    mismatches: list[str] = []

    for rel, info in manifest_files.items():
        action = info["action"]
        source_hash = info["source_hash"]
        target_hash = info["target_hash"]
        disk_path = disk_root / rel
        exists = disk_path.is_file()

        if action == "none":
            if not exists or scan.hash_file(disk_path) != target_hash:
                mismatches.append(rel)
        elif action == "modify":
            if not exists:
                mismatches.append(rel)
                continue
            disk_hash = scan.hash_file(disk_path)
            if disk_hash != source_hash and disk_hash != target_hash:
                mismatches.append(rel)
        elif action == "add":
            if exists and scan.hash_file(disk_path) != target_hash:
                mismatches.append(rel)
        elif action == "delete":
            if exists and scan.hash_file(disk_path) != source_hash:
                mismatches.append(rel)

    return sorted(mismatches)


def _rollback(disk_root: Path, backup_dir: Path, performed: list[tuple[str, str]]) -> None:
    """Best-effort: restore as many files as possible even if some fail."""
    for rel, kind in reversed(performed):
        disk_path = disk_root / rel
        backup_path = backup_dir / rel
        try:
            if kind in ("delete", "modify"):
                if backup_path.is_file():
                    disk_path.parent.mkdir(parents=True, exist_ok=True)
                    _ensure_writable(disk_path)
                    shutil.copy2(backup_path, disk_path)
            elif kind == "add":
                if disk_path.is_file():
                    _ensure_writable(disk_path)
                    disk_path.unlink()
        except OSError:
            continue


def apply_patch(disk_root: Path, patch_zip_path: Path, manifest_files: dict, backup_dir: Path) -> None:
    """Apply all actions in manifest_files against disk_root.

    Raises the original exception on failure, after rolling back every
    change made during this call. Cleans up backup_dir in all cases.
    """
    disk_root = Path(disk_root)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    performed: list[tuple[str, str]] = []

    try:
        with zipfile.ZipFile(patch_zip_path) as zf:
            for rel, info in manifest_files.items():
                action = info["action"]
                if action == "none":
                    continue

                disk_path = disk_root / rel

                if action == "delete":
                    if not disk_path.is_file():
                        continue
                    backup_path = backup_dir / rel
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    _ensure_writable(disk_path)
                    shutil.copy2(disk_path, backup_path)
                    performed.append((rel, "delete"))
                    disk_path.unlink()

                elif action == "add":
                    if disk_path.is_file() and scan.hash_file(disk_path) == info["target_hash"]:
                        continue
                    disk_path.parent.mkdir(parents=True, exist_ok=True)
                    _ensure_writable(disk_path)
                    disk_path.write_bytes(patch_format.read_add_blob(zf, rel))
                    performed.append((rel, "add"))

                elif action == "modify":
                    if disk_path.is_file() and scan.hash_file(disk_path) == info["target_hash"]:
                        continue
                    backup_path = backup_dir / rel
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    _ensure_writable(disk_path)
                    shutil.copy2(disk_path, backup_path)
                    performed.append((rel, "modify"))
                    diff_bytes = patch_format.read_diff_blob(zf, rel)
                    src_bytes = backup_path.read_bytes()
                    new_bytes = bsdiff4.patch(src_bytes, diff_bytes)
                    disk_path.write_bytes(new_bytes)
    except Exception:
        _rollback(disk_root, backup_dir, performed)
        raise
    finally:
        _rmtree_best_effort(backup_dir)

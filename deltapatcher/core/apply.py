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

import logging
import os
import shutil
import stat
import zipfile
from pathlib import Path

from . import patch_format, scan, xdelta

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB limit for in-memory bsdiff ops
CHUNK_SIZE = patch_format.CHUNK_SIZE  # Must match patch_format

log = logging.getLogger(__name__)


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


def verify(
    disk_root: Path, manifest_files: dict, progress_callback: callable | None = None
) -> list[str]:
    """Return the sorted list of relpaths that are neither source nor target version.

    Args:
        disk_root: Root directory to verify
        manifest_files: File manifest from patch
        progress_callback: Optional callback(current_file: str, progress: float, total: int)
    """
    disk_root = Path(disk_root)
    mismatches: list[str] = []
    files_to_check = [(rel, info) for rel, info in manifest_files.items() if info["action"] != "none"]
    total = len(files_to_check)

    for i, (rel, info) in enumerate(files_to_check):
        if progress_callback:
            progress_callback(rel, i / total if total > 0 else 1.0, total)
        action = info["action"]
        source_hash = info["source_hash"]
        target_hash = info["target_hash"]
        disk_path = disk_root / rel
        exists = disk_path.is_file()

        if action == "none":
            if not exists or scan.hash_file(disk_path) != target_hash:
                mismatches.append(rel)
        elif action in ("modify", "modify_chunked"):
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

    if progress_callback:
        progress_callback("Verification complete", 1.0, total)
    return sorted(mismatches)


def _rollback(disk_root: Path, backup_dir: Path, performed: list[tuple[str, str]]) -> None:
    """Best-effort: restore as many files as possible even if some fail."""
    for rel, kind in reversed(performed):
        disk_path = disk_root / rel
        backup_path = backup_dir / rel
        try:
            if kind in ("delete", "modify", "modify_chunked"):
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


def _write_file_streaming(zf: zipfile.ZipFile, arcname: str, dest_path: Path) -> None:
    """Stream a file from zip to disk without loading entirely into memory."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(arcname) as src, open(dest_path, "wb") as dst:
        shutil.copyfileobj(src, dst)


def _apply_chunked_patch(
    disk_path: Path,
    zf: zipfile.ZipFile,
    rel: str,
    info: dict,
    backup_dir: Path,
) -> None:
    """Apply chunked diff to a file. Reads source in chunks, applies diffs, writes target."""
    chunk_size = info["chunk_size"]
    num_chunks = info["num_chunks"]

    log.debug("Applying chunked patch: %s (%d chunks of %d bytes)", rel, num_chunks, chunk_size)

    # Backup original file
    backup_path = backup_dir / rel
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_writable(disk_path)
    shutil.copy2(disk_path, backup_path)

    # Open source for reading, target for writing
    with open(backup_path, "rb") as src_f, open(disk_path, "wb") as dst_f:
        for chunk_idx in range(num_chunks):
            src_chunk = src_f.read(chunk_size)
            if not src_chunk:
                # Source file ended (shouldn't happen if manifest is correct)
                break

            diff_bytes = patch_format.read_chunk_diff_blob(zf, rel, chunk_idx)
            tgt_chunk = xdelta.patch(src_chunk, diff_bytes)
            dst_f.write(tgt_chunk)

            log.debug("Applied chunk %d/%d: %d -> %d bytes", chunk_idx + 1, num_chunks, len(src_chunk), len(tgt_chunk))


def apply_patch(
    disk_root: Path,
    patch_zip_path: Path,
    manifest_files: dict,
    backup_dir: Path,
    progress_callback: callable | None = None,
) -> None:
    """Apply all actions in manifest_files against disk_root.

    Raises the original exception on failure, after rolling back every
    change made during this call. Cleans up backup_dir in all cases.

    Args:
        disk_root: Root directory to patch
        patch_zip_path: Path to patch.zip
        manifest_files: File manifest from patch
        backup_dir: Directory for backup files
        progress_callback: Optional callback(current_file: str, progress: float, total: int)
    """
    disk_root = Path(disk_root)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    performed: list[tuple[str, str]] = []

    files_to_patch = [(rel, info) for rel, info in manifest_files.items() if info["action"] != "none"]
    total = len(files_to_patch)

    try:
        with zipfile.ZipFile(patch_zip_path) as zf:
            for i, (rel, info) in enumerate(files_to_patch):
                if progress_callback:
                    progress_callback(rel, i / total if total > 0 else 1.0, total)
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
                    try:
                        add_size = zf.getinfo(patch_format.ADD_PREFIX + rel).file_size
                    except KeyError:
                        raise FileNotFoundError(
                            f"Missing add blob in patch: {patch_format.ADD_PREFIX}{rel}"
                        ) from None
                    if add_size > MAX_FILE_SIZE:
                        _write_file_streaming(zf, patch_format.ADD_PREFIX + rel, disk_path)
                    else:
                        try:
                            disk_path.write_bytes(patch_format.read_add_blob(zf, rel))
                        except KeyError:
                            raise FileNotFoundError(
                                f"Missing add blob in patch: {patch_format.ADD_PREFIX}{rel}"
                            ) from None
                    performed.append((rel, "add"))

                elif action == "modify":
                    if disk_path.is_file() and scan.hash_file(disk_path) == info["target_hash"]:
                        continue
                    backup_path = backup_dir / rel
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    _ensure_writable(disk_path)
                    shutil.copy2(disk_path, backup_path)
                    performed.append((rel, "modify"))
                    try:
                        diff_bytes = patch_format.read_diff_blob(zf, rel)
                    except KeyError:
                        raise FileNotFoundError(
                            f"Missing diff blob in patch: {patch_format.DIFF_PREFIX}{rel}"
                        ) from None
                    src_size = backup_path.stat().st_size
                    if src_size > MAX_FILE_SIZE:
                        raise MemoryError(
                            f"File too large for in-memory patch: {rel} "
                            f"({src_size:,} bytes, max {MAX_FILE_SIZE:,})"
                        )
                    src_bytes = backup_path.read_bytes()
                    new_bytes = xdelta.patch(src_bytes, diff_bytes)
                    disk_path.write_bytes(new_bytes)

                elif action == "modify_chunked":
                    if disk_path.is_file() and scan.hash_file(disk_path) == info["target_hash"]:
                        continue
                    try:
                        _apply_chunked_patch(disk_path, zf, rel, info, backup_dir)
                    except KeyError as e:
                        raise FileNotFoundError(
                            f"Missing chunk diff blob in patch: {e}"
                        ) from None
                    performed.append((rel, "modify_chunked"))

        if progress_callback:
            progress_callback("Patch applied successfully", 1.0, total)
    except Exception:
        _rollback(disk_root, backup_dir, performed)
        raise
    finally:
        _rmtree_best_effort(backup_dir)

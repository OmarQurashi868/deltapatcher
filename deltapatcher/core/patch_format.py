"""Patch container format: a ZIP holding manifest.json plus per-file blobs.

manifest.json shape:
{
    "version": 1,
    "files": {
        "<relpath>": {
            "action": "modify" | "add" | "delete" | "none",
            "source_hash": "<sha256 hex>" | null,
            "target_hash": "<sha256 hex>" | null
        },
        ...
    }
}

For action "modify": diffs/<relpath> holds a bsdiff4 patch (source -> target).
For action "add": adds/<relpath> holds the full target file bytes.
For action "delete"/"none": no blob is stored.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

import bsdiff4

from . import scan

MANIFEST_NAME = "manifest.json"
DIFF_PREFIX = "diffs/"
ADD_PREFIX = "adds/"
# bsdiff4 needs ~3x file size in RAM; 100 MB limit avoids OOM on large game assets
MAX_DIFF_SIZE = 100 * 1024 * 1024
# Streaming add/copy limit (2 GB)
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024

log = logging.getLogger(__name__)


def _read_file_streaming(path: Path, zf: zipfile.ZipFile, arcname: str) -> None:
    """Stream a file into the zip without loading entirely into memory."""
    with open(path, "rb") as f:
        zf.writestr(arcname, f.read())


def build_patch_zip(
    source_dir: Path, target_dir: Path, progress_callback: callable | None = None
) -> bytes:
    """Diff source_dir against target_dir and return patch ZIP bytes.

    Args:
        source_dir: Source directory path
        target_dir: Target directory path
        progress_callback: Optional callback(current_file: str, progress: float, total: int)
    """
    source_dir = Path(source_dir)
    target_dir = Path(target_dir)
    log.info("Scanning source directory: %s", source_dir)
    source_hashes = scan.scan_directory(source_dir)
    log.info("Scanned %d files from source", len(source_hashes))
    log.info("Scanning target directory: %s", target_dir)
    target_hashes = scan.scan_directory(target_dir)
    log.info("Scanned %d files from target", len(target_hashes))
    all_paths = sorted(set(source_hashes) | set(target_hashes))
    total = len(all_paths)
    log.info("Total unique paths to process: %d", total)

    files_manifest: dict[str, dict] = {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, rel in enumerate(all_paths):
            if progress_callback:
                progress_callback(rel, i / total if total > 0 else 1.0, total)
            s_hash = source_hashes.get(rel)
            t_hash = target_hashes.get(rel)

            if s_hash is not None and t_hash is not None:
                if s_hash == t_hash:
                    action = "none"
                else:
                    src_path = source_dir / rel
                    tgt_path = target_dir / rel
                    src_size = src_path.stat().st_size
                    tgt_size = tgt_path.stat().st_size
                    log.debug("Diffing: %s (%d -> %d bytes)", rel, src_size, tgt_size)
                    if src_size > MAX_DIFF_SIZE or tgt_size > MAX_DIFF_SIZE:
                        # Too large for bsdiff - fall back to full copy ("add" action)
                        log.warning("File too large for diff, using full copy: %s (%d -> %d bytes)",
                                    rel, src_size, tgt_size)
                        action = "add"
                        _read_file_streaming(tgt_path, zf, ADD_PREFIX + rel)
                    else:
                        action = "modify"
                        src_bytes = src_path.read_bytes()
                        tgt_bytes = tgt_path.read_bytes()
                        log.debug("Running bsdiff4.diff for %s", rel)
                        diff_bytes = bsdiff4.diff(src_bytes, tgt_bytes)
                        log.debug("bsdiff4.diff complete for %s: %d bytes", rel, len(diff_bytes))
                        zf.writestr(DIFF_PREFIX + rel, diff_bytes)
            elif t_hash is not None:
                action = "add"
                tgt_path = target_dir / rel
                tgt_size = tgt_path.stat().st_size
                log.debug("Adding new file: %s (%d bytes)", rel, tgt_size)
                if tgt_size > MAX_FILE_SIZE:
                    _read_file_streaming(tgt_path, zf, ADD_PREFIX + rel)
                else:
                    zf.writestr(ADD_PREFIX + rel, tgt_path.read_bytes())
            else:
                action = "delete"
                log.debug("Marking for delete: %s", rel)

            files_manifest[rel] = {
                "action": action,
                "source_hash": s_hash,
                "target_hash": t_hash,
            }

        if progress_callback:
            progress_callback("Writing manifest...", 1.0, total)
        manifest = {"version": 1, "files": files_manifest}
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))

    log.info("Patch zip built successfully: %d bytes", buffer.tell())
    return buffer.getvalue()


def read_manifest(zf: zipfile.ZipFile) -> dict:
    with zf.open(MANIFEST_NAME) as f:
        return json.load(f)


def read_diff_blob(zf: zipfile.ZipFile, rel: str) -> bytes:
    with zf.open(DIFF_PREFIX + rel) as f:
        return f.read()


def read_add_blob(zf: zipfile.ZipFile, rel: str) -> bytes:
    with zf.open(ADD_PREFIX + rel) as f:
        return f.read()

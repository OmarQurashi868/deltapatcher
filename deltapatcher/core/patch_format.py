"""Patch container format: a ZIP holding manifest.json plus per-file blobs.

manifest.json shape:
{
    "version": 1,
    "files": {
        "<relpath>": {
            "action": "modify" | "modify_chunked" | "add" | "delete" | "none",
            "source_hash": "<sha256 hex>" | null,
            "target_hash": "<sha256 hex>" | null,
            "chunk_size": <int> | null,      # for modify_chunked
            "num_chunks": <int> | null       # for modify_chunked
        },
        ...
    }
}

For action "modify": diffs/<relpath> holds an xdelta3 patch (source -> target).
For action "modify_chunked": diffs/<relpath>.chunk001, .chunk002, ... hold per-chunk xdelta3 patches.
For action "add": adds/<relpath> holds the full target file bytes.
For action "delete"/"none": no blob is stored.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

from . import scan, xdelta

MANIFEST_NAME = "manifest.json"
DIFF_PREFIX = "diffs/"
ADD_PREFIX = "adds/"
CHUNK_SUFFIX = ".chunk"
# Files >50MB use chunked diff (16MB chunks = ~50MB RAM per chunk)
CHUNK_THRESHOLD = 50 * 1024 * 1024
CHUNK_SIZE = 16 * 1024 * 1024
# bsdiff4 needs ~3x file size in RAM; 100 MB limit avoids OOM on large game assets
MAX_DIFF_SIZE = 100 * 1024 * 1024
# Streaming add/copy limit (2 GB)
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024

log = logging.getLogger(__name__)


def _read_file_streaming(path: Path, zf: zipfile.ZipFile, arcname: str) -> None:
    """Stream a file into the zip without loading entirely into memory."""
    with open(path, "rb") as f:
        zf.writestr(arcname, f.read())


def _chunked_diff(src_path: Path, tgt_path: Path, zf: zipfile.ZipFile, rel: str, progress_callback=None) -> int:
    """Diff large file in chunks, write chunk diffs to ZIP. Returns number of chunks."""
    src_size = src_path.stat().st_size
    tgt_size = tgt_path.stat().st_size
    num_chunks = max((src_size + CHUNK_SIZE - 1) // CHUNK_SIZE, (tgt_size + CHUNK_SIZE - 1) // CHUNK_SIZE)

    log.info("Chunked diff: %s (%d -> %d bytes) in %d chunks of %d bytes",
             rel, src_size, tgt_size, num_chunks, CHUNK_SIZE)

    with open(src_path, "rb") as src_f, open(tgt_path, "rb") as tgt_f:
        for chunk_idx in range(num_chunks):
            src_chunk = src_f.read(CHUNK_SIZE)
            tgt_chunk = tgt_f.read(CHUNK_SIZE)

            if not src_chunk and not tgt_chunk:
                break

            log.debug("Diffing chunk %d/%d: %d -> %d bytes", chunk_idx + 1, num_chunks, len(src_chunk), len(tgt_chunk))
            diff_bytes = xdelta.diff(src_chunk, tgt_chunk)
            chunk_name = f"{DIFF_PREFIX}{rel}{CHUNK_SUFFIX}{chunk_idx + 1:03d}"
            zf.writestr(chunk_name, diff_bytes)
            log.debug("Chunk %d diff: %d bytes", chunk_idx + 1, len(diff_bytes))

            if progress_callback:
                progress_callback(f"{rel} (chunk {chunk_idx + 1}/{num_chunks})",
                                  (chunk_idx + 1) / num_chunks, num_chunks)

    return num_chunks


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
                    files_manifest[rel] = {
                        "action": action,
                        "source_hash": s_hash,
                        "target_hash": t_hash,
                    }
                else:
                    src_path = source_dir / rel
                    tgt_path = target_dir / rel
                    src_size = src_path.stat().st_size
                    tgt_size = tgt_path.stat().st_size
                    log.debug("Diffing: %s (%d -> %d bytes)", rel, src_size, tgt_size)

                    if src_size > MAX_FILE_SIZE or tgt_size > MAX_FILE_SIZE:
                        # Too large even for chunked - full copy
                        log.warning("File too large for chunked diff, using full copy: %s (%d -> %d bytes)",
                                    rel, src_size, tgt_size)
                        action = "add"
                        _read_file_streaming(tgt_path, zf, ADD_PREFIX + rel)
                        files_manifest[rel] = {
                            "action": action,
                            "source_hash": s_hash,
                            "target_hash": t_hash,
                        }
                    elif src_size > CHUNK_THRESHOLD or tgt_size > CHUNK_THRESHOLD:
                        # Chunked diff
                        action = "modify_chunked"
                        num_chunks = _chunked_diff(src_path, tgt_path, zf, rel, progress_callback)
                        files_manifest[rel] = {
                            "action": action,
                            "source_hash": s_hash,
                            "target_hash": t_hash,
                            "chunk_size": CHUNK_SIZE,
                            "num_chunks": num_chunks,
                        }
                    else:
                        # Regular xdelta
                        action = "modify"
                        src_bytes = src_path.read_bytes()
                        tgt_bytes = tgt_path.read_bytes()
                        log.debug("Running xdelta.diff for %s", rel)
                        diff_bytes = xdelta.diff(src_bytes, tgt_bytes)
                        log.debug("xdelta.diff complete for %s: %d bytes", rel, len(diff_bytes))
                        zf.writestr(DIFF_PREFIX + rel, diff_bytes)
                        files_manifest[rel] = {
                            "action": action,
                            "source_hash": s_hash,
                            "target_hash": t_hash,
                        }
            elif t_hash is not None:
                action = "add"
                tgt_path = target_dir / rel
                tgt_size = tgt_path.stat().st_size
                log.debug("Adding new file: %s (%d bytes)", rel, tgt_size)
                if tgt_size > MAX_FILE_SIZE:
                    _read_file_streaming(tgt_path, zf, ADD_PREFIX + rel)
                else:
                    zf.writestr(ADD_PREFIX + rel, tgt_path.read_bytes())
                files_manifest[rel] = {
                    "action": action,
                    "source_hash": s_hash,
                    "target_hash": t_hash,
                }
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


def read_chunk_diff_blob(zf: zipfile.ZipFile, rel: str, chunk_idx: int) -> bytes:
    """Read a specific chunk diff blob."""
    chunk_name = f"{DIFF_PREFIX}{rel}{CHUNK_SUFFIX}{chunk_idx + 1:03d}"
    with zf.open(chunk_name) as f:
        return f.read()


def read_add_blob(zf: zipfile.ZipFile, rel: str) -> bytes:
    with zf.open(ADD_PREFIX + rel) as f:
        return f.read()

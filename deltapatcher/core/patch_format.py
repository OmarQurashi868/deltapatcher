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
import zipfile
from pathlib import Path

import bsdiff4

from . import scan

MANIFEST_NAME = "manifest.json"
DIFF_PREFIX = "diffs/"
ADD_PREFIX = "adds/"


def build_patch_zip(source_dir: Path, target_dir: Path) -> bytes:
    """Diff source_dir against target_dir and return patch ZIP bytes."""
    source_dir = Path(source_dir)
    target_dir = Path(target_dir)
    source_hashes = scan.scan_directory(source_dir)
    target_hashes = scan.scan_directory(target_dir)
    all_paths = sorted(set(source_hashes) | set(target_hashes))

    files_manifest: dict[str, dict] = {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in all_paths:
            s_hash = source_hashes.get(rel)
            t_hash = target_hashes.get(rel)

            if s_hash is not None and t_hash is not None:
                if s_hash == t_hash:
                    action = "none"
                else:
                    action = "modify"
                    src_bytes = (source_dir / rel).read_bytes()
                    tgt_bytes = (target_dir / rel).read_bytes()
                    diff_bytes = bsdiff4.diff(src_bytes, tgt_bytes)
                    zf.writestr(DIFF_PREFIX + rel, diff_bytes)
            elif t_hash is not None:
                action = "add"
                zf.writestr(ADD_PREFIX + rel, (target_dir / rel).read_bytes())
            else:
                action = "delete"

            files_manifest[rel] = {
                "action": action,
                "source_hash": s_hash,
                "target_hash": t_hash,
            }

        manifest = {"version": 1, "files": files_manifest}
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))

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

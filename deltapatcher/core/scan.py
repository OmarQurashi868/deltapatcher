"""Recursive directory scanning and file hashing."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


def hash_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file's contents."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def scan_directory(root: Path) -> dict[str, str]:
    """Recursively hash every regular file under root.

    Returns a dict mapping POSIX-style relative paths (stable across
    Windows/Linux) to their SHA-256 hex digest. Broken symlinks are skipped.
    """
    root = Path(root)
    result: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            abs_path = Path(dirpath) / filename
            if not abs_path.is_file():
                continue
            rel_path = abs_path.relative_to(root).as_posix()
            try:
                result[rel_path] = hash_file(abs_path)
            except OSError:
                continue
    return result

"""End-to-end tests for the diff/verify/apply pipeline (no PyInstaller build).

Run with: python -m unittest discover tests
"""
from __future__ import annotations

import shutil
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from deltapatcher.core import apply as core_apply
from deltapatcher.core import patch_format


def _read_tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="dp_test_"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

        self.source_dir = self.work / "source"
        self.target_dir = self.work / "target"
        self.source_dir.mkdir()
        (self.source_dir / "unchanged.txt").write_text("same content\n")
        (self.source_dir / "modified.txt").write_bytes(bytes(range(256)) * 20)
        (self.source_dir / "deleted.txt").write_text("bye\n")
        (self.source_dir / "sub").mkdir()
        (self.source_dir / "sub" / "nested_modified.bin").write_bytes(b"\x00\x01\x02" * 500)

        shutil.copytree(self.source_dir, self.target_dir)
        (self.target_dir / "modified.txt").write_bytes(bytes(range(256))[::-1] * 20 + b"tail")
        (self.target_dir / "deleted.txt").unlink()
        (self.target_dir / "added.txt").write_text("brand new\n")
        (self.target_dir / "sub" / "nested_modified.bin").write_bytes(b"\xff\xee\xdd" * 500)

        patch_bytes = patch_format.build_patch_zip(self.source_dir, self.target_dir)
        self.patch_zip_path = self.work / "patch.zip"
        self.patch_zip_path.write_bytes(patch_bytes)
        with zipfile.ZipFile(self.patch_zip_path) as zf:
            self.manifest = patch_format.read_manifest(zf)
        self.files = self.manifest["files"]

    def test_manifest_actions(self) -> None:
        self.assertEqual(self.files["unchanged.txt"]["action"], "none")
        self.assertEqual(self.files["modified.txt"]["action"], "modify")
        self.assertEqual(self.files["deleted.txt"]["action"], "delete")
        self.assertEqual(self.files["added.txt"]["action"], "add")
        self.assertEqual(self.files["sub/nested_modified.bin"]["action"], "modify")

    def test_clean_apply_matches_target(self) -> None:
        disk_dir = self.work / "disk"
        shutil.copytree(self.source_dir, disk_dir)

        self.assertEqual(core_apply.verify(disk_dir, self.files), [])

        backup_dir = self.work / "backup1"
        core_apply.apply_patch(disk_dir, self.patch_zip_path, self.files, backup_dir)
        self.assertFalse(backup_dir.exists())
        self.assertEqual(_read_tree(disk_dir), _read_tree(self.target_dir))

    def test_reapply_on_already_patched_dir_is_noop(self) -> None:
        disk_dir = self.work / "disk"
        shutil.copytree(self.source_dir, disk_dir)
        core_apply.apply_patch(disk_dir, self.patch_zip_path, self.files, self.work / "backup1")

        self.assertEqual(core_apply.verify(disk_dir, self.files), [])
        core_apply.apply_patch(disk_dir, self.patch_zip_path, self.files, self.work / "backup2")
        self.assertEqual(_read_tree(disk_dir), _read_tree(self.target_dir))

    def test_mismatch_detected_without_writes(self) -> None:
        disk_dir = self.work / "disk_mismatch"
        shutil.copytree(self.source_dir, disk_dir)
        (disk_dir / "modified.txt").write_bytes(b"totally unexpected content, neither version")

        before = _read_tree(disk_dir)
        mismatches = core_apply.verify(disk_dir, self.files)
        self.assertEqual(mismatches, ["modified.txt"])
        self.assertEqual(_read_tree(disk_dir), before)

    def test_readonly_files_are_handled(self) -> None:
        disk_dir = self.work / "disk_readonly"
        shutil.copytree(self.source_dir, disk_dir)
        import os

        os.chmod(disk_dir / "modified.txt", stat.S_IREAD)
        os.chmod(disk_dir / "deleted.txt", stat.S_IREAD)

        self.assertEqual(core_apply.verify(disk_dir, self.files), [])
        core_apply.apply_patch(disk_dir, self.patch_zip_path, self.files, self.work / "backup")
        self.assertFalse((disk_dir / "deleted.txt").exists())
        self.assertEqual(
            (disk_dir / "modified.txt").read_bytes(),
            (self.target_dir / "modified.txt").read_bytes(),
        )

    def test_rollback_restores_state_on_failure(self) -> None:
        disk_dir = self.work / "disk_rollback"
        shutil.copytree(self.source_dir, disk_dir)
        before = _read_tree(disk_dir)

        corrupt_zip_path = self.work / "patch_corrupt.zip"
        with zipfile.ZipFile(self.patch_zip_path) as src_zf, zipfile.ZipFile(
            corrupt_zip_path, "w"
        ) as dst_zf:
            for item in src_zf.infolist():
                data = src_zf.read(item.filename)
                if item.filename == "diffs/modified.txt":
                    data = b"not a valid bsdiff4 patch at all"
                dst_zf.writestr(item, data)

        with self.assertRaises(Exception):
            core_apply.apply_patch(disk_dir, corrupt_zip_path, self.files, self.work / "backup")

        self.assertFalse((self.work / "backup").exists())
        self.assertEqual(_read_tree(disk_dir), before)


if __name__ == "__main__":
    unittest.main()

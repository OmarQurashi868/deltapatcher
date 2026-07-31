"""Entry point for the built Applier exe.

This script is bundled by PyInstaller together with a patch.zip data file
(added at build time via --add-data). It has no configuration of its own:
launch it, pick the folder to patch, done.

Flow: pick folder -> verify every tracked file is at the source or target
version (background thread, indeterminate spinner) -> on any mismatch,
show the offending files and abort with no writes -> otherwise apply the
patch with backup-before-overwrite and automatic rollback on failure.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

if not getattr(sys, "frozen", False):
    _project_root = Path(__file__).resolve().parents[2]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

from deltapatcher.core import apply as core_apply
from deltapatcher.core import patch_format

MAX_LISTED_MISMATCHES = 50


def _bundled_patch_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base / "patch.zip"


def _load_manifest(patch_zip_path: Path) -> dict:
    with zipfile.ZipFile(patch_zip_path) as zf:
        return patch_format.read_manifest(zf)


class ApplierApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Patcher")
        self.root.geometry("380x140")
        self.root.resizable(False, False)

        self.status_var = tk.StringVar(value="Preparing...")
        tk.Label(self.root, textvariable=self.status_var, wraplength=340).pack(pady=20)
        self.progress: ttk.Progressbar | None = None

        try:
            self.patch_zip_path = _bundled_patch_path()
            self.manifest = _load_manifest(self.patch_zip_path)
        except Exception as exc:
            messagebox.showerror("Patch error", f"Could not read patch data:\n\n{exc}")
            self.root.destroy()
            return

        self.root.after(200, self.pick_folder)
        self.root.mainloop()

    def pick_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select the folder to patch")
        if not folder:
            self.root.destroy()
            return
        self.target_dir = Path(folder)
        self.start_patch()

    def start_patch(self) -> None:
        self.status_var.set("Checking files...")
        self._show_spinner()
        threading.Thread(target=self._worker, daemon=True).start()

    def _show_spinner(self) -> None:
        self.progress = ttk.Progressbar(self.root, mode="indeterminate", length=280)
        self.progress.pack(pady=10)
        self.progress.start(12)

    def _hide_spinner(self) -> None:
        if self.progress is not None:
            self.progress.stop()
            self.progress.destroy()
            self.progress = None

    def _worker(self) -> None:
        try:
            files = self.manifest["files"]
            mismatches = core_apply.verify(self.target_dir, files)
            if mismatches:
                self.root.after(0, lambda: self._on_mismatch(mismatches))
                return

            self.root.after(0, lambda: self.status_var.set("Patching..."))
            backup_dir = Path(tempfile.mkdtemp(prefix="deltapatcher_backup_"))
            core_apply.apply_patch(self.target_dir, self.patch_zip_path, files, backup_dir)
            self.root.after(0, self._on_success)
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda: self._on_error(message))

    def _on_mismatch(self, mismatches: list[str]) -> None:
        self._hide_spinner()
        self.root.withdraw()
        listing = "\n".join(mismatches[:MAX_LISTED_MISMATCHES])
        if len(mismatches) > MAX_LISTED_MISMATCHES:
            listing += f"\n... and {len(mismatches) - MAX_LISTED_MISMATCHES} more"
        messagebox.showerror(
            "Patch cancelled",
            "These files don't match either the original or updated version, "
            "so patching was cancelled to avoid corrupting your files:\n\n" + listing,
        )
        self.root.destroy()

    def _on_success(self) -> None:
        self._hide_spinner()
        self.root.withdraw()
        messagebox.showinfo("Done", "Patch applied successfully.")
        self.root.destroy()

    def _on_error(self, message: str) -> None:
        self._hide_spinner()
        self.root.withdraw()
        messagebox.showerror("Patch failed", f"Patching failed and was rolled back:\n\n{message}")
        self.root.destroy()


def main() -> None:
    ApplierApp()


if __name__ == "__main__":
    main()

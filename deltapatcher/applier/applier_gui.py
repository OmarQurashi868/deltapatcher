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

import logging
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

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    log_file = Path(tempfile.gettempdir()) / "deltapatcher_applier.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )
    log.info("Applier started, log file: %s", log_file)


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
        self.root.geometry("420x180")
        self.root.resizable(False, False)

        self.status_var = tk.StringVar(value="Preparing...")
        tk.Label(self.root, textvariable=self.status_var, wraplength=380).pack(pady=(15, 5))

        # Progress section
        self.progress_frame = tk.Frame(self.root)
        self.progress_frame.pack(fill="x", padx=20, pady=(0, 10))
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress = ttk.Progressbar(
            self.progress_frame, mode="determinate", length=380, variable=self.progress_var, maximum=100
        )
        self.progress.pack(fill="x")
        self.progress_file_var = tk.StringVar(value="")
        tk.Label(self.progress_frame, textvariable=self.progress_file_var, wraplength=380, justify="left").pack(fill="x", pady=(4, 0))

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
        self.status_var.set("Verifying files...")
        self.progress_var.set(0.0)
        self.progress_file_var.set("Starting verification...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _progress_callback(self, current_file: str, progress: float, total: int) -> None:
        """Called from background thread to update progress."""
        self.root.after(0, lambda: self._update_progress(current_file, progress, total))

    def _update_progress(self, current_file: str, progress: float, total: int) -> None:
        """Update progress bar and label on main thread."""
        percent = int(progress * 100)
        self.progress_var.set(percent)
        if total > 0:
            self.progress_file_var.set(f"[{percent}%] {current_file} ({int(progress * total)}/{total})")
        else:
            self.progress_file_var.set(f"[{percent}%] {current_file}")

    def _worker(self) -> None:
        try:
            log.info("Starting patch apply: target_dir=%s", self.target_dir)
            files = self.manifest["files"]

            # Verify phase
            self.root.after(0, lambda: self.status_var.set("Verifying files..."))
            mismatches = core_apply.verify(self.target_dir, files, self._progress_callback)
            if mismatches:
                log.warning("Mismatches found: %s", mismatches)
                self.root.after(0, lambda: self._on_mismatch(mismatches))
                return

            # Apply phase
            self.root.after(0, lambda: self.status_var.set("Applying patch..."))
            self.root.after(0, lambda: self.progress_var.set(0.0))
            backup_dir = Path(tempfile.mkdtemp(prefix="deltapatcher_backup_"))
            core_apply.apply_patch(self.target_dir, self.patch_zip_path, files, backup_dir, self._progress_callback)
            log.info("Patch apply completed successfully")
            self.root.after(0, self._on_success)
        except BaseException as exc:
            log.exception("Patch apply failed: %s", exc)
            message = f"{type(exc).__name__}: {exc}"
            self.root.after(0, lambda: self._on_error(message))

    def _on_mismatch(self, mismatches: list[str]) -> None:
        self.progress_file_var.set("Verification failed - mismatches found")
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
        self.progress_var.set(100)
        self.progress_file_var.set("Complete")
        self.root.withdraw()
        messagebox.showinfo("Done", "Patch applied successfully.")
        self.root.destroy()

    def _on_error(self, message: str) -> None:
        self.progress_file_var.set("Error occurred")
        self.root.withdraw()
        messagebox.showerror("Patch failed", f"Patching failed and was rolled back:\n\n{message}")
        self.root.destroy()


def main() -> None:
    _setup_logging()
    ApplierApp()


if __name__ == "__main__":
    main()

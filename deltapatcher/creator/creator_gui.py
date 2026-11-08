"""Patch Creator: pick a source dir, a target dir, and where to save the
resulting patcher exe. Building a patch invokes PyInstaller in the background
(see build_applier.py) so it can take a little while for large directories.
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import build_applier

log = logging.getLogger(__name__)

WINDOW_WIDTH = 520
WINDOW_HEIGHT = 420


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("deltapatcher_creator.log", mode="w"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    for handler in logging.getLogger().handlers:
        if hasattr(handler, 'flush'):
            handler.flush = lambda h=handler: h.stream.flush() if hasattr(h, 'stream') else None


class CreatorApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Delta Patch Creator")
        self.root.resizable(False, False)
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.minsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.root.maxsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.source_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.browse_buttons: list[tk.Button] = []
        self.building = False

        self._add_field(
            "Source directory (folder you want to update FROM):",
            self.source_var,
            self._make_browse_dir(self.source_var),
        )
        self._add_field(
            "Target directory (folder you want to update TO):",
            self.target_var,
            self._make_browse_dir(self.target_var),
        )
        self._add_field(
            "Patch location (where to save the patcher .exe):",
            self.output_var,
            self._browse_save,
        )

        self.status_var = tk.StringVar(value="")
        tk.Label(
            self.root, textvariable=self.status_var, fg="red", wraplength=480, justify="left"
        ).pack(fill="x", padx=10, pady=(4, 0))

        # Progress section
        self.progress_frame = tk.Frame(self.root)
        self.progress_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress = ttk.Progressbar(
            self.progress_frame, mode="determinate", length=480, variable=self.progress_var, maximum=100
        )
        self.progress.pack(fill="x")
        self.progress_file_var = tk.StringVar(value="")
        tk.Label(self.progress_frame, textvariable=self.progress_file_var, wraplength=480, justify="left").pack(fill="x", pady=(4, 0))

        self.create_button = tk.Button(self.root, text="Create Patch", command=self._on_create)
        self.create_button.pack(pady=12)

        self.root.mainloop()

    def _add_field(self, label_text: str, var: tk.StringVar, browse_callback) -> None:
        tk.Label(self.root, text=label_text, anchor="w", justify="left").pack(
            fill="x", padx=10, pady=(10, 0)
        )
        row = tk.Frame(self.root)
        row.pack(fill="x", padx=10)
        tk.Entry(row, textvariable=var, width=55).pack(side="left", fill="x", expand=True)
        btn = tk.Button(row, text="Browse...", command=browse_callback)
        btn.pack(side="left", padx=(6, 0))
        self.browse_buttons.append(btn)

    def _make_browse_dir(self, var: tk.StringVar):
        def _browse() -> None:
            if self.building:
                return
            path = filedialog.askdirectory(title="Select folder")
            if path:
                var.set(path)

        return _browse

    def _browse_save(self) -> None:
        if self.building:
            return
        path = filedialog.asksaveasfilename(
            title="Save patch as", defaultextension=".exe", filetypes=[("Executable", "*.exe")]
        )
        if path:
            self.output_var.set(path)

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for btn in self.browse_buttons:
            btn.config(state=state)
        self.create_button.config(state=state)

    def _on_close(self) -> None:
        if self.building:
            return  # Ignore close during build
        self.root.destroy()

    def _on_create(self) -> None:
        source = self.source_var.get().strip()
        target = self.target_var.get().strip()
        output = self.output_var.get().strip()

        if not source or not Path(source).is_dir():
            self.status_var.set("Source directory is missing or invalid.")
            return
        if not target or not Path(target).is_dir():
            self.status_var.set("Target directory is missing or invalid.")
            return
        if not output:
            self.status_var.set("Choose a patch location.")
            return

        self.status_var.set("")
        self.building = True
        self._set_controls_enabled(False)
        self.progress_var.set(0.0)
        self.progress_file_var.set("Starting...")

        threading.Thread(
            target=self._worker, args=(Path(source), Path(target), Path(output)), daemon=True
        ).start()

    def _progress_callback(self, current_file: str, progress: float, total: int) -> None:
        """Called from background thread to update progress."""
        if self.building:
            self.root.after(0, lambda: self._update_progress(current_file, progress, total))

    def _update_progress(self, current_file: str, progress: float, total: int) -> None:
        """Update progress bar and label on main thread."""
        percent = int(progress * 100)
        self.progress_var.set(percent)
        if total > 0:
            self.progress_file_var.set(f"[{percent}%] {current_file} ({int(progress * total)}/{total})")
        else:
            self.progress_file_var.set(f"[{percent}%] {current_file}")

    def _worker(self, source: Path, target: Path, output: Path) -> None:
        try:
            log.info("Starting patch build: source=%s target=%s output=%s", source, target, output)
            for h in logging.getLogger().handlers:
                if hasattr(h, 'flush'):
                    try:
                        h.flush()
                    except Exception:
                        pass
            build_applier.build_applier_exe(source, target, output, self._progress_callback)
            log.info("Patch build completed successfully")
            self._schedule_done(True, str(output))
        except BaseException as exc:
            tb = traceback.format_exc()
            log.error("Patch build failed: %s\n%s", exc, tb)
            print(f"CRASH: {type(exc).__name__}: {exc}")
            print(tb)
            message = f"{type(exc).__name__}: {exc}"
            self._schedule_done(False, message)

    def _schedule_done(self, success: bool, message: str) -> None:
        """Safely schedule _on_done on main thread."""
        try:
            self.root.after(0, lambda: self._on_done(success, message))
        except Exception:
            # If after fails, try direct call (may happen if mainloop ending)
            self._on_done(success, message)

    def _on_done(self, success: bool, message: str) -> None:
        self.building = False
        self.progress_var.set(100)
        self.progress_file_var.set("Complete")
        self._set_controls_enabled(True)
        if success:
            messagebox.showinfo("Patch created", f"Patch executable created:\n{message}")
        else:
            messagebox.showerror("Patch creation failed", message)


def main() -> None:
    _setup_logging()
    CreatorApp()


if __name__ == "__main__":
    main()

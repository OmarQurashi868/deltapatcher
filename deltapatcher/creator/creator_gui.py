"""Patch Creator: pick a source dir, a target dir, and where to save the
resulting patcher exe. Building a patch invokes PyInstaller in the background
(see build_applier.py) so it can take a little while for large directories.
"""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import build_applier


class CreatorApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Delta Patch Creator")
        self.root.resizable(False, False)

        self.source_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.output_var = tk.StringVar()

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
            self.root, textvariable=self.status_var, fg="red", wraplength=460, justify="left"
        ).pack(fill="x", padx=10, pady=(4, 0))

        self.progress: ttk.Progressbar | None = None
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
        tk.Button(row, text="Browse...", command=browse_callback).pack(side="left", padx=(6, 0))

    def _make_browse_dir(self, var: tk.StringVar):
        def _browse() -> None:
            path = filedialog.askdirectory(title="Select folder")
            if path:
                var.set(path)

        return _browse

    def _browse_save(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save patch as", defaultextension=".exe", filetypes=[("Executable", "*.exe")]
        )
        if path:
            self.output_var.set(path)

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
        self.create_button.config(state="disabled")
        self.progress = ttk.Progressbar(self.root, mode="indeterminate", length=300)
        self.progress.pack(pady=(0, 10))
        self.progress.start(12)

        threading.Thread(
            target=self._worker, args=(Path(source), Path(target), Path(output)), daemon=True
        ).start()

    def _worker(self, source: Path, target: Path, output: Path) -> None:
        try:
            build_applier.build_applier_exe(source, target, output)
            self.root.after(0, lambda: self._on_done(True, str(output)))
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda: self._on_done(False, message))

    def _on_done(self, success: bool, message: str) -> None:
        if self.progress is not None:
            self.progress.stop()
            self.progress.destroy()
            self.progress = None
        self.create_button.config(state="normal")
        if success:
            messagebox.showinfo("Patch created", f"Patch executable created:\n{message}")
        else:
            messagebox.showerror("Patch creation failed", message)


def main() -> None:
    CreatorApp()


if __name__ == "__main__":
    main()

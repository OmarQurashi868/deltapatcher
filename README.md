# deltapatcher

Builds delta patches between two directory trees, packaged as a single
standalone patcher `.exe` that an average user can just double-click.

## How it works

- **Creator** (`run_creator.py`) — a small GUI with three fields:
  Source directory, Target directory, and Patch location. It diffs the two
  directories file-by-file (SHA-256 identity, bsdiff4 binary diffs for
  changed files) and builds a self-contained patcher exe at the chosen
  location. Building invokes PyInstaller, so it takes a bit longer than a
  simple diff would.
- **Applier** (the generated exe) — has no configuration. Double-click it,
  pick the folder to patch, and it does the rest. Before writing anything,
  it verifies every tracked file in that folder is currently at either the
  source version or the target version. If any file is neither (a
  mismatch), it aborts with no changes and lists the offending files.
  Otherwise it applies all changes with backup-before-overwrite, and
  automatically rolls back if anything fails partway through.

## Requirements

```
pip install -r requirements.txt
```

## Usage

```
python run_creator.py
```

Pick a source directory, a target directory, and where to save the
resulting patcher exe, then click "Create Patch".

## Scope

Windows only for now (PyInstaller can't cross-compile; a Linux build +
AppImage packaging would need to run on Linux). The core diff/patch/verify
logic is plain, portable Python, so that's a packaging step to add later,
not a rewrite.

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

## Installing

Requires Python 3.10+ and a C compiler (needed to build the `bsdiff4`
dependency; on Windows this means having the Visual Studio Build Tools
installed, which most dev machines already have).

```
git clone https://github.com/OmarQurashi868/deltapatcher.git
cd deltapatcher
pip install -r requirements.txt
```

## Running the Creator

```
python run_creator.py
```

This opens the Creator window. Fill in:
- **Source directory** — the folder you want to update FROM
- **Target directory** — the folder you want to update TO
- **Patch location** — where to save the resulting patcher exe

Click "Create Patch". It'll take a little while (it invokes PyInstaller
under the hood), then you'll have a standalone exe at the location you
chose — hand that to whoever needs to apply the update; they just
double-click it and pick the folder to patch.

## Running the tests

```
python -m unittest discover tests
```

## Scope

Windows only for now (PyInstaller can't cross-compile; a Linux build +
AppImage packaging would need to run on Linux). The core diff/patch/verify
logic is plain, portable Python, so that's a packaging step to add later,
not a rewrite.

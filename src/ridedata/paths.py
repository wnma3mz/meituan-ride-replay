"""Filesystem layout for the fetch and prepare stages.

CLI defaults used to be bare relative paths like `Path("data/three-years")`,
which only worked when the command ran from the project root.  Resolving them
here makes the commands work from any directory.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent


def data_root() -> Path:
    override = os.environ.get("RIDE_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return PROJECT_ROOT / "data"


def three_years() -> Path:
    return data_root() / "three-years"


def filters_file() -> Path:
    """Day-selection rules.  Absent means the built-in rules apply."""
    override = os.environ.get("RIDE_FILTERS")
    if override:
        return Path(override).expanduser().resolve()
    return PROJECT_ROOT / "filters.yaml"

"""Filesystem layout for the video pipeline.

One place resolves every path, so moving this package does not mean hunting
down a dozen `parent.parent` expressions.  `PROJECT_ROOT` walks up out of
`src/` rather than counting directory levels from a module, because modules sit
at different depths (`cli.py` vs `rides/load.py`).
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent

# Swift sources and the binaries compiled from them on first use.
SWIFT_DIR = PACKAGE_ROOT / "swift"
BIN_DIR = PACKAGE_ROOT / "bin"

# Caches that survive across runs: map snapshots, inferred routes, place names.
CACHE_DIR = PACKAGE_ROOT / "cache"
BASEMAP_CACHE = CACHE_DIR / "basemaps"
ROUTE_CACHE = CACHE_DIR / "routes.json"
LOCATION_CACHE = CACHE_DIR / "locations.json"


def data_root() -> Path:
    """Where the fetch/prepare stage left its JSON.

    Overridable via RIDE_DATA_DIR so the two halves of the project can live in
    different places without editing code.
    """
    override = os.environ.get("RIDE_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return PROJECT_ROOT / "data"


def days_root() -> Path:
    """Per-day JSON written by the selection stage; one video per file."""
    return data_root() / "three-years" / "days"


def video_out() -> Path:
    return data_root() / "video-out"

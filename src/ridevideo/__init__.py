"""Render cycling history into vertical replay videos.

Consumes the JSON produced by the `ridedata` stage and writes MP4s.  The two
packages communicate only through the data directory.
"""
from __future__ import annotations

__all__ = ["main"]


def main() -> None:
    from .cli import main as _main

    _main()

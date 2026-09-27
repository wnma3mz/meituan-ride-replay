"""Infer a path for rides that only recorded their endpoints.

MapKit has no cycling mode, so `swift/route.swift` prefers walking (the
corridors a bike actually uses, and it ignores one-way restrictions) and falls
back to driving with highways and tolls avoided on long intercity legs.  Results
are cached and always labelled as inferred so the video can say so on screen.
"""
from __future__ import annotations

import json
import subprocess

from ..paths import ROUTE_CACHE
from ..swiftkit import JsonCache, ensure_binary
from .load import INFERRED, Day


def cache_key(start: tuple[float, float], end: tuple[float, float]) -> str:
    return f"{start[0]},{start[1]}->{end[0]},{end[1]}"


def infer_one(
    start: tuple[float, float],
    end: tuple[float, float],
    cache: JsonCache | None = None,
) -> dict | None:
    """Infer one leg's route, consulting the shared cache first.

    Exposed so the HTML side can reuse this rather than reimplementing route
    inference against a second cache file.
    """
    own = cache is None
    cache = cache if cache is not None else JsonCache(ROUTE_CACHE)
    key = cache_key(start, end)
    entry = cache.get(key)
    if isinstance(entry, dict) and entry.get("points"):
        return entry
    binary = ensure_binary("route", ["MapKit"])
    if not binary:
        return None
    try:
        done = subprocess.run(
            [str(binary), str(start[1]), str(start[0]), str(end[1]), str(end[0])],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    try:
        entry = json.loads(done.stdout)
    except json.JSONDecodeError:
        return None
    cache.set(key, entry)
    if own:
        cache.flush()
    return entry


def apply(day: Day) -> int:
    """Replace endpoint-only paths with inferred polylines.  Returns count."""
    todo = [r for r in day.rides if r.track_source == INFERRED and len(r.path) <= 2]
    if not todo:
        return 0
    cache = JsonCache(ROUTE_CACHE)
    applied = 0

    for ride in todo:
        entry = infer_one(ride.start, ride.end, cache)
        if isinstance(entry, dict) and entry.get("points"):
            pts = [(float(p[0]), float(p[1])) for p in entry["points"] if len(p) >= 2]
            if len(pts) >= 2:
                ride.path = pts
                applied += 1

    cache.flush()
    return applied

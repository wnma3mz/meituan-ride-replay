"""MapKit basemaps, oversampled once per shot and reused for every frame.

Snapshots are rendered larger than the output frame so the camera can crop and
scale into them (Ken Burns).  That keeps map labels from popping mid-zoom and
costs 31 ms/frame instead of a fresh 1.5 s snapshot.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..paths import BASEMAP_CACHE
from ..swiftkit import ensure_binary as _ensure_swift
from .load import haversine

CACHE_DIR = BASEMAP_CACHE
PARALLEL = 6

# Bump when the pixel projection in swift/snap.swift changes, so stale cached
# coordinates are never reused.  v2 flipped y from AppKit's bottom-left origin
# to PIL's top-left origin.
PROJECTION_VERSION = 2


def ensure_binary() -> Path | None:
    return _ensure_swift("snap", ["MapKit", "AppKit"])


@dataclass
class Basemap:
    """An oversampled map image plus the pixel projection of its track points."""
    image: Image.Image
    points: list[tuple[float, float]]   # pixel coords, same order as requested
    size: tuple[int, int]

    def project(self, index: int) -> tuple[float, float]:
        return self.points[index]


@dataclass
class Shot:
    """A snapshot request: geographic centre, span, and the points to project."""
    center: tuple[float, float]         # (lon, lat)
    span_m: tuple[float, float]         # (lon_meters, lat_meters)
    size: tuple[int, int]               # pixel size to render
    track: list[tuple[float, float]]    # points to project into pixels

    def key(self) -> str:
        payload = json.dumps([
            PROJECTION_VERSION,
            round(self.center[0], 7), round(self.center[1], 7),
            round(self.span_m[0], 1), round(self.span_m[1], 1),
            self.size, [[round(p[0], 7), round(p[1], 7)] for p in self.track],
        ], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:20]


def shot_for(
    bbox: tuple[float, float, float, float],
    size: tuple[int, int],
    track: list[tuple[float, float]],
    padding: float = 1.30,
) -> Shot:
    """Build a shot that frames bbox with padding, matching the output aspect."""
    min_lon, max_lon, min_lat, max_lat = bbox
    clon = (min_lon + max_lon) / 2
    clat = (min_lat + max_lat) / 2
    width_m = max(haversine((min_lon, clat), (max_lon, clat)), 250.0)
    height_m = max(haversine((clon, min_lat), (clon, max_lat)), 250.0)
    width_m *= padding
    height_m *= padding
    # Match the frame aspect so nothing is squashed, then grow to cover.
    aspect = size[0] / size[1]
    if width_m / height_m < aspect:
        width_m = height_m * aspect
    else:
        height_m = width_m / aspect
    return Shot(center=(clon, clat), span_m=(width_m, height_m), size=size, track=track)


def _parse_coords(output: str) -> list[tuple[float, float]]:
    """Pull `x,y` pixel pairs out of snapbin's stdout.

    Frameworks on the snapshot path occasionally log to stdout (e.g.
    "List has 3 nodes:"), so every line has to be validated rather than assumed
    to be a coordinate.
    """
    coords: list[tuple[float, float]] = []
    for line in output.splitlines():
        parts = line.strip().split(",")
        if len(parts) != 2:
            continue
        try:
            coords.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return coords


def render(shots: list[Shot]) -> list[Basemap | None]:
    """Render shots in parallel, reusing any cached result on disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    results: list[Basemap | None] = [None] * len(shots)
    pending: list[int] = []

    for i, shot in enumerate(shots):
        key = shot.key()
        png = CACHE_DIR / f"{key}.png"
        meta = CACHE_DIR / f"{key}.json"
        if png.exists() and meta.exists():
            try:
                coords = [tuple(p) for p in json.loads(meta.read_text())]
                results[i] = Basemap(Image.open(png).convert("RGB"), coords, shot.size)
                continue
            except (OSError, json.JSONDecodeError):
                pass
        pending.append(i)

    binary = ensure_binary()
    if not binary:
        return results

    for batch_start in range(0, len(pending), PARALLEL):
        batch = pending[batch_start:batch_start + PARALLEL]
        procs = []
        for i in batch:
            shot = shots[i]
            key = shot.key()
            png = CACHE_DIR / f"{key}.png"
            args = [
                str(binary), str(shot.center[1]), str(shot.center[0]),
                str(shot.span_m[1]), str(shot.span_m[0]),
                str(shot.size[0]), str(shot.size[1]),
                "1", "1", "1", str(png),
            ]
            for lon, lat in shot.track:
                args += [str(lon), str(lat)]
            procs.append((i, png, subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)))
        for i, png, proc in procs:
            out, _ = proc.communicate()
            if proc.returncode != 0 or not png.exists():
                continue
            coords = _parse_coords(out)
            (CACHE_DIR / f"{shots[i].key()}.json").write_text(json.dumps(coords))
            results[i] = Basemap(Image.open(png).convert("RGB"), coords, shots[i].size)
    return results

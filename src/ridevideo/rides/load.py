"""Read exported ride JSON and normalise it for rendering.

Reads only from the exporter's data directory; never writes there.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..format import beijing_dt, duration_text
from ..paths import CACHE_DIR, data_root

SRC = data_root()
CACHE = CACHE_DIR

# A ride whose detail response carried more than the two endpoints has a real
# recorded track.  Everything else has to be inferred from a routing engine, and
# the video must say so.
GPS = "gps"
INFERRED = "inferred"


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Metres between two (lon, lat) points."""
    r = 6371000.0
    p = math.pi / 180
    dlat = (b[1] - a[1]) * p
    dlon = (b[0] - a[0]) * p
    h = (math.sin(dlat / 2) ** 2
         + math.cos(a[1] * p) * math.cos(b[1] * p) * math.sin(dlon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def path_length(points: list[tuple[float, float]]) -> float:
    return sum(haversine(a, b) for a, b in zip(points, points[1:]))


def parse_lonlat(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    parts = str(value).split(",")
    if len(parts) < 2:
        return None
    try:
        return (float(parts[0]), float(parts[1]))
    except ValueError:
        return None


@dataclass
class Ride:
    index: int
    start: tuple[float, float]
    end: tuple[float, float]
    path: list[tuple[float, float]]
    track_source: str
    start_ms: int
    end_ms: int
    duration_text: str
    start_name: str = ""
    end_name: str = ""

    @property
    def start_dt(self) -> datetime:
        return beijing_dt(self.start_ms)

    @property
    def end_dt(self) -> datetime:
        return beijing_dt(self.end_ms)

    @property
    def duration_hours(self) -> float:
        return (self.end_ms - self.start_ms) / 3_600_000

    @property
    def distance_m(self) -> float:
        return path_length(self.path)

    @property
    def speed_kmh(self) -> float:
        hours = self.duration_hours
        return (self.distance_m / 1000) / hours if hours > 0 else 0.0


@dataclass
class Day:
    date: str
    rides: list[Ride]
    gap_minutes: list[float] = field(default_factory=list)
    title: str = ""

    @property
    def has_inferred(self) -> bool:
        return any(r.track_source == INFERRED for r in self.rides)

    @property
    def riding_seconds(self) -> float:
        return sum((r.end_ms - r.start_ms) / 1000 for r in self.rides)

    @property
    def total_distance_m(self) -> float:
        return sum(r.distance_m for r in self.rides)

    def bbox(self) -> tuple[float, float, float, float]:
        """(min_lon, max_lon, min_lat, max_lat) over every path point."""
        pts = [p for r in self.rides for p in r.path]
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        return (min(lons), max(lons), min(lats), max(lats))


def _ride_from_order(order: dict, index: int) -> Ride | None:
    start = parse_lonlat(order.get("startLonlat"))
    end = parse_lonlat(order.get("endLonlat"))
    if not start or not end:
        return None
    if order.get("startTimestamp") is not None:
        start_ms = int(order["startTimestamp"])
        end_ms = int(order["endTimestamp"])
    else:
        # Group manifests carry ISO Beijing timestamps instead of epoch millis.
        try:
            start_ms = int(datetime.fromisoformat(order["startTimeBeijing"]).timestamp() * 1000)
            end_ms = int(datetime.fromisoformat(order["endTimeBeijing"]).timestamp() * 1000)
        except (KeyError, ValueError):
            return None
    track = [p for p in (parse_lonlat(v) for v in (order.get("trackPoints") or "").split("#")) if p]
    if len(track) > 2:
        path, source = track, GPS
    else:
        path, source = [start, end], INFERRED
    return Ride(
        index=index,
        start=start,
        end=end,
        path=path,
        track_source=source,
        start_ms=start_ms,
        end_ms=end_ms,
        # Derived rather than read from `durationText`: older exports stored a
        # minutes-only form that reported a 119-minute ride as `119分38秒`.
        duration_text=duration_text((end_ms - start_ms) / 1000),
    )


def _finish(date: str, rides: list[Ride], title: str = "") -> Day:
    for i, ride in enumerate(rides):
        ride.index = i
    gaps = [(b.start_ms - a.end_ms) / 60000 for a, b in zip(rides, rides[1:])]
    day = Day(date=date, rides=rides, gap_minutes=gaps, title=title)
    attach_names(day)
    return day


def _day_file(date: str) -> Path:
    """Locate the exported JSON for a date, preferring the three-year corpus."""
    candidates = [
        SRC / "three-years" / "days" / f"{date}.json",
        SRC / f"rides-{date}.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(f"找不到 {date} 的导出 JSON：{candidates}")


def load_day(date: str) -> Day:
    document = json.loads(_day_file(date).read_text(encoding="utf-8"))
    raw = sorted(document.get("orders", []), key=lambda o: o["startTimestamp"])
    rides = [r for r in (_ride_from_order(o, i) for i, o in enumerate(raw)) if r]
    return _finish(date, rides)


def attach_names(day: Day) -> None:
    """Fill place names from the local caches; never calls the network here."""
    names: dict[str, str] = {}
    for path in (SRC / "locations.json", CACHE / "locations.json"):
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(cached, dict):
                    names.update({k: v for k, v in cached.items() if isinstance(v, str)})
            except (OSError, json.JSONDecodeError):
                pass
    for ride in day.rides:
        ride.start_name = names.get(f"{ride.start[0]},{ride.start[1]}", "")
        ride.end_name = names.get(f"{ride.end[0]},{ride.end[1]}", "")


def missing_names(day: Day) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for ride in day.rides:
        if not ride.start_name and ride.start not in out:
            out.append(ride.start)
        if not ride.end_name and ride.end not in out:
            out.append(ride.end)
    return out

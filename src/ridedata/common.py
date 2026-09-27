"""Shared helpers for the fetch and prepare stages.

These lived in four or five copies each before, and had already drifted: the
duration formatter in particular existed in two incompatible versions, one of
which silently dropped the hour component (23810s rendered as `396分50秒`
instead of `6小时36分50秒`).
"""
from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BEIJING = timezone(timedelta(hours=8))

# A detail response carrying more than the two endpoints has a real recorded
# track; two points means the path has to be inferred from a routing engine.
FULL_TRACK_MIN_POINTS = 2

DEFAULT_THRESHOLD_MINUTES = 30.0


def beijing_dt(milliseconds: float) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1000, BEIJING)


def beijing_date(milliseconds: float) -> str:
    return beijing_dt(milliseconds).strftime("%Y-%m-%d")


def beijing_time(milliseconds: float) -> str:
    return beijing_dt(milliseconds).strftime("%H:%M:%S")


def duration_text(seconds: float) -> str:
    """Chinese duration text that does not lose the hour component.

    The old minutes-only form reported a 119-minute ride as `119分38秒`, which
    is how 21 stored orders ended up understating themselves.
    """
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分{secs:02d}秒"
    return f"{minutes}分{secs:02d}秒"


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Metres between two (lon, lat) points."""
    radius = 6_371_000.0
    rad = math.pi / 180
    d_lat = (b[1] - a[1]) * rad
    d_lon = (b[0] - a[0]) * rad
    value = (
        math.sin(d_lat / 2) ** 2
        + math.cos(a[1] * rad) * math.cos(b[1] * rad) * math.sin(d_lon / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(value))


def haversine_lonlat(a: str | None, b: str | None) -> float | None:
    """Metres between two `"lon,lat"` strings, or None if either is unusable."""
    start, end = parse_lonlat(a), parse_lonlat(b)
    if start is None or end is None:
        return None
    return haversine(start, end)


def parse_lonlat(value: str | None) -> tuple[float, float] | None:
    """Parse a `"lon,lat"` pair, tolerating extra trailing fields.

    Two earlier copies disagreed on whether `"116.4,39.9,0"` was valid; this
    accepts it and ignores the extra fields.
    """
    if not value:
        return None
    parts = str(value).split(",")
    if len(parts) < 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None


def track_point_count(value: str | None) -> int:
    """Count `#`-separated track points in a raw trackPoints string."""
    if not value:
        return 0
    return len([part for part in str(value).split("#") if part])


def has_full_track(count: int | None) -> bool:
    return bool(count) and count > FULL_TRACK_MIN_POINTS


def write_json(path: Path, value: Any, *, indent: int = 2, sort_keys: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=indent, sort_keys=sort_keys),
        encoding="utf-8",
    )


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON, returning `default` for a missing or corrupt file."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: Sequence[str]) -> None:
    """Write UTF-8 BOM CSV so Excel opens Chinese text correctly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})

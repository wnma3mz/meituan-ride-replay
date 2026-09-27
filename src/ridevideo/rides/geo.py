"""Resolve (lon, lat) to a Chinese place name via Apple's CLGeocoder.

Results are cached on disk so a rerun never repeats a lookup.
"""
from __future__ import annotations

import subprocess

from ..paths import LOCATION_CACHE
from ..swiftkit import JsonCache, ensure_binary


def _cache() -> JsonCache:
    return JsonCache(LOCATION_CACHE, indent=2, sort_keys=True)


def _pick(raw: str) -> str:
    """Prefer the most specific useful component over the full address stack."""
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    if not parts:
        return ""
    district = next((p for p in parts if p.endswith(("区", "县", "市")) and p != "北京市"), "")
    # The last component is the POI/road name; strip parenthetical directions.
    detail = parts[-1].split("(")[0].strip()
    if district and detail and detail != district:
        return f"{district}·{detail}"
    return detail or district or parts[0]


def resolve(points: list[tuple[float, float]]) -> dict[str, str]:
    """Look up any uncached points and return the full name cache."""
    cache = _cache()
    names = {k: v for k, v in cache.data.items() if isinstance(v, str)}
    todo = [p for p in points if f"{p[0]},{p[1]}" not in names]
    if not todo:
        return names
    binary = ensure_binary("geo", ["CoreLocation"])
    if not binary:
        return names
    for lon, lat in todo:
        try:
            done = subprocess.run(
                [str(binary), str(lat), str(lon)],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        line = done.stdout.strip()
        if done.returncode == 0 and line and not line.startswith("ERR"):
            name = _pick(line)
            if name:
                cache.set(f"{lon},{lat}", name)
                names[f"{lon},{lat}"] = name
    cache.flush()
    return names

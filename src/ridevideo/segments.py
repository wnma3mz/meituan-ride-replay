"""Drop invalid rides, split a day into journeys, and require a minimum distance.

Three problems this solves, in order:

1. **Invalid rides.**  A 120-minute record covering 780 metres is not a ride —
   it is a forgotten lock or a paused trip.  Average speed separates them
   cleanly: in the real corpus every questionable record sits below 4.5 km/h
   while every genuine ride is above 8.9 km/h.

2. **Unrelated journeys in one day.**  Two rides hours apart and kilometres
   away were separate outings, not one journey with a gap.  Splitting them
   produces two videos instead of one video with a meaningless transfer.

3. **Trivial journeys.**  A 3-kilometre errand does not carry a 20-second video.

Filtering has to happen here rather than in `ridedata` because an
endpoint-only ride has no distance until `route.swift` has inferred its path.
Straight-line distance would understate every ride and wrongly reject some.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .rides.load import Day, Ride, haversine

# Defaults chosen from the real corpus; override in filters.yaml under `video:`.
MIN_SPEED_KMH = 5.0
SPLIT_GAP_MINUTES = 120.0
SPLIT_GAP_KM = 2.0
MIN_TOTAL_KM = 10.0


@dataclass
class VideoRules:
    min_speed_kmh: float = MIN_SPEED_KMH
    split_gap_minutes: float = SPLIT_GAP_MINUTES
    split_gap_km: float = SPLIT_GAP_KM
    min_total_km: float = MIN_TOTAL_KM

    @classmethod
    def load(cls, path: Path | None = None) -> VideoRules:
        """Read the `video:` block from filters.yaml, if present."""
        if path is None:
            override = os.environ.get("RIDE_FILTERS")
            if override:
                path = Path(override).expanduser()
            else:
                path = Path(__file__).resolve().parent.parent.parent / "filters.yaml"
        if not path.exists():
            return cls()
        try:
            import yaml

            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return cls()
        block = document.get("video") if isinstance(document, dict) else None
        if not isinstance(block, dict):
            return cls()
        known = {f: getattr(cls, f) for f in cls.__dataclass_fields__}
        values = {}
        for key in known:
            if key in block:
                try:
                    values[key] = float(block[key])
                except (TypeError, ValueError):
                    pass
        return cls(**values)


def ride_km(ride: Ride) -> float:
    """Length along the ride's path, which by now is real or inferred."""
    if len(ride.path) < 2:
        return 0.0
    return sum(
        haversine(ride.path[i], ride.path[i + 1])
        for i in range(len(ride.path) - 1)
    ) / 1000


def ride_speed_kmh(ride: Ride) -> float:
    hours = (ride.end_ms - ride.start_ms) / 3_600_000
    if hours <= 0:
        return 0.0
    return ride_km(ride) / hours


def drop_invalid(day: Day, rules: VideoRules) -> tuple[list[Ride], list[Ride]]:
    """Partition a day's rides into (kept, dropped-as-invalid)."""
    kept, dropped = [], []
    for ride in day.rides:
        (kept if ride_speed_kmh(ride) >= rules.min_speed_kmh else dropped).append(ride)
    return kept, dropped


def split_journeys(rides: list[Ride], rules: VideoRules) -> list[list[Ride]]:
    """Cut the list wherever two consecutive rides are too far apart in time or space.

    Uses the same thresholds as the connection detection upstream and the
    on-screen transfer marker, so all three agree on what "continuous" means.
    """
    if not rides:
        return []
    ordered = sorted(rides, key=lambda r: r.start_ms)
    journeys: list[list[Ride]] = []
    current = [ordered[0]]
    for previous, ride in zip(ordered, ordered[1:]):
        gap_minutes = (ride.start_ms - previous.end_ms) / 60000
        gap_km = haversine(previous.end, ride.start) / 1000
        if gap_minutes > rules.split_gap_minutes or gap_km > rules.split_gap_km:
            journeys.append(current)
            current = []
        current.append(ride)
    journeys.append(current)
    return journeys


@dataclass
class Journey:
    """One video's worth of rides, plus why it was kept."""
    key: str
    rides: list[Ride]
    total_km: float
    index: int          # 1-based position within the day
    of: int             # how many journeys the day produced


def plan(day: Day, rules: VideoRules | None = None) -> tuple[list[Journey], dict]:
    """Decide which videos a day should produce.

    Returns the journeys plus a report explaining what was dropped, so the
    caller can say so rather than silently producing nothing.
    """
    rules = rules or VideoRules.load()
    kept, invalid = drop_invalid(day, rules)
    candidates = split_journeys(kept, rules)

    passing = [c for c in candidates if sum(ride_km(r) for r in c) >= rules.min_total_km]
    journeys: list[Journey] = []
    for position, group in enumerate(passing, 1):
        suffix = f"-{position}" if len(passing) > 1 else ""
        journeys.append(Journey(
            key=f"{day.date}{suffix}",
            rides=group,
            total_km=sum(ride_km(r) for r in group),
            index=position,
            of=len(passing),
        ))

    report = {
        "date": day.date,
        "rides": len(day.rides),
        "invalidRides": len(invalid),
        "invalidDetail": [
            {"durationMin": round((r.end_ms - r.start_ms) / 60000, 1),
             "km": round(ride_km(r), 2),
             "speedKmh": round(ride_speed_kmh(r), 1)}
            for r in invalid
        ],
        "journeys": len(journeys),
        "tooShortJourneys": len(candidates) - len(passing),
        "rules": {
            "minSpeedKmh": rules.min_speed_kmh,
            "splitGapMinutes": rules.split_gap_minutes,
            "splitGapKm": rules.split_gap_km,
            "minTotalKm": rules.min_total_km,
        },
    }
    return journeys, report


def day_for(day: Day, journey: Journey) -> Day:
    """A Day holding just this journey's rides, renumbered for rendering."""
    rides = [
        Ride(
            index=i,
            start=r.start,
            end=r.end,
            path=r.path,
            track_source=r.track_source,
            start_ms=r.start_ms,
            end_ms=r.end_ms,
            duration_text=r.duration_text,
            start_name=r.start_name,
            end_name=r.end_name,
        )
        for i, r in enumerate(journey.rides)
    ]
    gaps = [(b.start_ms - a.end_ms) / 60000 for a, b in zip(rides, rides[1:])]
    return Day(date=day.date, rides=rides, gap_minutes=gaps, title=journey.key)

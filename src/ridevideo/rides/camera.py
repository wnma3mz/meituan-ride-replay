"""Timeline and camera: map wall-clock time to frames, and frames to crops.

Two jobs:
  1. A time warp that plays rides proportional to their real duration while
     fast-forwarding idle gaps, so the on-screen clock never contradicts the
     progress bar.
  2. A camera that eases between a wide establishing view and a per-ride close
     view, expressed as crop rectangles into an oversampled basemap.
"""
from __future__ import annotations

from dataclasses import dataclass

from .load import Day, haversine

# A gap where the next ride starts this far from the previous ride's end was not
# cycled: the rider moved some other way.  Say so rather than implying a
# continuous journey.
TRANSFER_KM = 2.0

# Idle time is compressed hard but not erased: the viewer still sees the clock
# jump and gets a "waiting" caption, which keeps the day's shape honest.
GAP_SPEEDUP = 60.0
MAX_GAP_SECONDS = 1.2
TRANSFER_SECONDS = 2.0
# A leg shorter than this on screen is unreadable, however brief it really was.
MIN_RIDE_SECONDS = 2.0


def ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - pow(1 - t, 3)


@dataclass
class Segment:
    """One stretch of video time mapped onto one stretch of real time.

    `transfer` marks a gap the rider clearly did not cycle across (the next ride
    starts far from where the previous one ended), so the caption can say so
    instead of implying a continuous journey.
    """
    kind: str               # "intro" | "ride" | "gap" | "outro"
    ride_index: int         # -1 for intro/outro/gap
    video_start: float      # seconds into the video
    video_end: float
    real_start_ms: int
    real_end_ms: int
    transfer_km: float = 0.0

    @property
    def video_duration(self) -> float:
        return self.video_end - self.video_start

    def progress(self, t: float) -> float:
        if self.video_duration <= 0:
            return 1.0
        return max(0.0, min(1.0, (t - self.video_start) / self.video_duration))

    def real_ms(self, t: float) -> int:
        return round(self.real_start_ms + (self.real_end_ms - self.real_start_ms) * self.progress(t))


def suggest_ride_seconds(day: Day) -> float:
    """Scale video length to content: a 2-order group should not run as long
    as a 12-order one, and a 397-minute group needs room to breathe."""
    per_ride = 3.4
    base = len(day.rides) * per_ride
    # Long groups get a gentle bonus so their pace does not feel rushed, but
    # sub-linear so the 397-minute outlier stays watchable.
    minutes = day.riding_seconds / 60
    base += min(12.0, (minutes / 60) ** 0.65 * 3.0)
    return max(8.0, min(46.0, base))


def build_timeline(
    day: Day,
    ride_seconds: float,
    intro_seconds: float = 2.6,
    outro_seconds: float = 2.4,
) -> list[Segment]:
    """Allocate `ride_seconds` of video across rides in proportion to duration."""
    rides = day.rides
    total_ride_ms = sum(r.end_ms - r.start_ms for r in rides) or 1

    gaps: list[float] = []
    for a, b in zip(rides, rides[1:]):
        gap_ms = max(0, b.start_ms - a.end_ms)
        jump_km = haversine(a.end, b.start) / 1000
        if jump_km >= TRANSFER_KM:
            # A transfer is a story beat, not dead air: give it room to read.
            gaps.append(TRANSFER_SECONDS)
        elif gap_ms / 60000 < 1.0:
            gaps.append(0.0)
        else:
            gaps.append(min(MAX_GAP_SECONDS, gap_ms / 1000 / GAP_SPEEDUP))

    segments: list[Segment] = []
    cursor = 0.0
    segments.append(Segment("intro", -1, cursor, cursor + intro_seconds,
                            rides[0].start_ms, rides[0].start_ms))
    cursor += intro_seconds

    # Proportional time, but every ride gets a readable minimum on screen.
    # Without this a 7-minute leg next to an 86-minute one lasts 0.6 s and the
    # viewer never sees its label.
    shares = [(r.end_ms - r.start_ms) / total_ride_ms for r in rides]
    lengths = [ride_seconds * s for s in shares]
    floor = min(MIN_RIDE_SECONDS, ride_seconds / max(len(rides), 1))
    deficit = sum(max(0.0, floor - x) for x in lengths)
    surplus = sum(x - floor for x in lengths if x > floor) or 1.0
    lengths = [
        floor if x < floor else x - (x - floor) / surplus * deficit
        for x in lengths
    ]

    for i, ride in enumerate(rides):
        length = lengths[i]
        segments.append(Segment("ride", i, cursor, cursor + length, ride.start_ms, ride.end_ms))
        cursor += length
        if i < len(gaps) and gaps[i] > 0:
            jump_km = haversine(ride.end, rides[i + 1].start) / 1000
            segments.append(Segment("gap", i, cursor, cursor + gaps[i],
                                    ride.end_ms, rides[i + 1].start_ms,
                                    transfer_km=jump_km))
            cursor += gaps[i]

    segments.append(Segment("outro", -1, cursor, cursor + outro_seconds,
                            rides[-1].end_ms, rides[-1].end_ms))
    return segments


def segment_at(segments: list[Segment], t: float) -> Segment:
    for seg in segments:
        if seg.video_start <= t < seg.video_end:
            return seg
    return segments[-1]


def total_duration(segments: list[Segment]) -> float:
    return segments[-1].video_end


@dataclass
class Crop:
    """A crop window into the oversampled basemap, in basemap pixels."""
    cx: float
    cy: float
    zoom: float    # >1 means zoomed in

    def box(self, base_size: tuple[int, int], out_size: tuple[int, int]) -> tuple[float, float, float, float]:
        bw, bh = base_size
        # At zoom 1 the crop spans the full basemap height (matched aspect).
        w = bw / self.zoom
        h = bh / self.zoom
        left = self.cx - w / 2
        top = self.cy - h / 2
        # Never let the window leave the rendered map.
        left = max(0.0, min(bw - w, left))
        top = max(0.0, min(bh - h, top))
        return (left, top, left + w, top + h)


def _bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def ride_crop(
    pixels: list[tuple[float, float]],
    base_size: tuple[int, int],
    max_zoom: float = 2.2,
    margin: float = 1.45,
) -> Crop:
    """Frame one ride's pixel path as tightly as the zoom ceiling allows."""
    x0, y0, x1, y1 = _bounds(pixels)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    bw, bh = base_size
    span_x = max(x1 - x0, 1.0) * margin
    span_y = max(y1 - y0, 1.0) * margin
    zoom = min(max_zoom, bw / span_x, bh / span_y)
    return Crop(cx, cy, max(1.0, zoom))


def camera_for(
    t: float,
    segments: list[Segment],
    wide: Crop,
    ride_crops: list[Crop],
) -> Crop:
    """Interpolate the camera: wide on intro/outro, eased into each ride."""
    seg = segment_at(segments, t)
    hold = 0.30   # fraction of the ride spent settling in / pulling out

    def lerp(a: Crop, b: Crop, k: float) -> Crop:
        return Crop(a.cx + (b.cx - a.cx) * k,
                    a.cy + (b.cy - a.cy) * k,
                    a.zoom + (b.zoom - a.zoom) * k)

    if seg.kind == "intro":
        # Start slightly tighter than wide and breathe outward.
        start = Crop(wide.cx, wide.cy, wide.zoom * 1.18)
        return lerp(start, wide, ease_out(seg.progress(t)))

    if seg.kind == "outro":
        return wide

    if seg.kind == "gap":
        a = ride_crops[seg.ride_index]
        nxt = min(seg.ride_index + 1, len(ride_crops) - 1)
        b = ride_crops[nxt]
        k = ease_in_out(seg.progress(t))
        mid = lerp(a, b, k)
        if seg.transfer_km >= TRANSFER_KM:
            # Go all the way out to the establishing view: the whole point is to
            # show that the next ride starts somewhere else entirely.
            out = lerp(mid, wide, 4 * k * (1 - k))
            return Crop(out.cx, out.cy, max(1.0, out.zoom))
        # Arc out through a wider view so the jump reads as travel, not a cut.
        pull = 1 - 4 * k * (1 - k) * 0.45
        return Crop(mid.cx, mid.cy, max(1.0, mid.zoom * pull))

    target = ride_crops[seg.ride_index]
    p = seg.progress(t)
    if p < hold:
        prev = wide if seg.ride_index == 0 else ride_crops[seg.ride_index - 1]
        return lerp(prev, target, ease_in_out(p / hold))
    if p > 1 - hold:
        return lerp(target, target, 0.0)
    return target

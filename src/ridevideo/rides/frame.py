"""Compose one video frame with Pillow.

Everything is drawn at output resolution with real antialiasing and the system
PingFang font, so Chinese text renders correctly and lines are smooth.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .. import palette as palette_mod
from ..format import duration_short
from .camera import TRANSFER_KM, Crop, Segment, ease_out, segment_at
from .load import Day, haversine

FONT_PATH = "/System/Library/Fonts/PingFang.ttc"
REGULAR, MEDIUM, SEMIBOLD = 2, 4, 5

INK = (255, 255, 255)
MUTED = (178, 194, 216)
DIM = (138, 156, 180)

# Route colours come from filters.yaml; see ridevideo/palette.py.  Resolved once
# at import so every frame in a run is consistent even if the file changes.
PALETTE = palette_mod.load()
ACCENT = PALETTE.accent

SUPER = 2      # supersampling for the route overlay


@lru_cache(maxsize=256)
def font(size: int, weight: int = REGULAR) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size, index=weight)


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    size: int,
    weight: int = SEMIBOLD,
    min_size: int = 30,
) -> tuple[str, ImageFont.FreeTypeFont]:
    """Shrink then ellipsise, so a long road name can never run off frame."""
    s = size
    while s > min_size:
        f = font(s, weight)
        if draw.textlength(text, font=f) <= max_width:
            return text, f
        s -= 2
    f = font(min_size, weight)
    out = text
    while out and draw.textlength(out + "…", font=f) > max_width:
        out = out[:-1]
    return (out + "…") if out != text else text, f


def _scrim(size: tuple[int, int], height: int, top: bool, strength: int) -> Image.Image:
    """A vertical gradient that keeps text legible over any basemap."""
    w = size[0]
    grad = Image.new("L", (1, height))
    px = grad.load()
    for y in range(height):
        k = (1 - y / height) if top else (y / height)
        px[0, y] = int(strength * pow(k, 1.35))
    alpha = grad.resize((w, height), Image.BILINEAR)
    layer = Image.new("RGBA", (w, height), (6, 11, 20, 255))
    layer.putalpha(alpha)
    return layer


@dataclass
class Scene:
    """Everything needed to draw one frame."""
    day: Day
    pixels: list[list[tuple[float, float]]]   # per-ride basemap pixel paths
    cumulative_m: list[list[float]]           # per-ride cumulative metres


def build_scene(day: Day, projected: list[tuple[float, float]]) -> Scene:
    pixels: list[list[tuple[float, float]]] = []
    cursor = 0
    for ride in day.rides:
        n = len(ride.path)
        pixels.append(list(projected[cursor:cursor + n]))
        cursor += n
    cumulative = []
    for ride in day.rides:
        acc = [0.0]
        for a, b in zip(ride.path, ride.path[1:]):
            acc.append(acc[-1] + haversine(a, b))
        cumulative.append(acc)
    return Scene(day=day, pixels=pixels, cumulative_m=cumulative)


def point_at(pixels: list[tuple[float, float]], cum: list[float], fraction: float) -> tuple[float, float]:
    """Interpolate along the path by distance, not by point index."""
    total = cum[-1]
    if total <= 0 or len(pixels) < 2:
        return pixels[0]
    want = total * max(0.0, min(1.0, fraction))
    for i in range(len(cum) - 1):
        if cum[i + 1] >= want:
            span = cum[i + 1] - cum[i]
            t = (want - cum[i]) / span if span > 0 else 0.0
            ax, ay = pixels[i]
            bx, by = pixels[i + 1]
            return (ax + (bx - ax) * t, ay + (by - ay) * t)
    return pixels[-1]


def path_until(pixels: list[tuple[float, float]], cum: list[float], fraction: float) -> list[tuple[float, float]]:
    total = cum[-1]
    if total <= 0:
        return list(pixels[:1])
    want = total * max(0.0, min(1.0, fraction))
    out = [pixels[0]]
    for i in range(len(cum) - 1):
        if cum[i + 1] >= want:
            span = cum[i + 1] - cum[i]
            t = (want - cum[i]) / span if span > 0 else 0.0
            ax, ay = pixels[i]
            bx, by = pixels[i + 1]
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
            break
        out.append(pixels[i + 1])
    return out


def _to_view(
    pts: list[tuple[float, float]],
    box: tuple[float, float, float, float],
    out_size: tuple[int, int],
) -> list[tuple[float, float]]:
    left, top, right, bottom = box
    sx = out_size[0] / max(right - left, 1e-6)
    sy = out_size[1] / max(bottom - top, 1e-6)
    return [((x - left) * sx, (y - top) * sy) for x, y in pts]


def render_frame(
    t: float,
    scene: Scene,
    segments: list[Segment],
    crop: Crop,
    basemap: Image.Image,
    out_size: tuple[int, int],
) -> Image.Image:
    day = scene.day
    seg = segment_at(segments, t)
    box = crop.box(basemap.size, out_size)

    frame = basemap.crop((int(box[0]), int(box[1]), int(math.ceil(box[2])), int(math.ceil(box[3]))))
    frame = frame.resize(out_size, Image.LANCZOS)
    # Push the basemap back so the route and text carry the frame.
    frame = Image.blend(frame, Image.new("RGB", out_size, (8, 13, 24)), 0.42).convert("RGBA")

    active = seg.ride_index if seg.kind in ("ride", "gap") else (0 if seg.kind == "intro" else len(day.rides) - 1)
    if seg.kind == "ride":
        fraction = seg.progress(t)
    elif seg.kind == "intro":
        fraction = 0.0
    else:
        fraction = 1.0

    overlay = Image.new("RGBA", (out_size[0] * SUPER, out_size[1] * SUPER), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    def scaled(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(x * SUPER, y * SUPER) for x, y in _to_view(pts, box, out_size)]

    # Dashed connectors across transfers, so a gap the rider did not cycle is
    # never mistaken for part of the route.
    for i in range(len(day.rides) - 1):
        if haversine(day.rides[i].end, day.rides[i + 1].start) / 1000 < TRANSFER_KM:
            continue
        a = scaled([scene.pixels[i][-1]])[0]
        b = scaled([scene.pixels[i + 1][0]])[0]
        span = math.hypot(b[0] - a[0], b[1] - a[1])
        if span < 1:
            continue
        step = 26 * SUPER
        n = max(1, int(span // step))
        for k in range(n):
            if k % 2:
                continue
            t0, t1 = k / n, min(1.0, (k + 0.62) / n)
            seg = [(a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0),
                   (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)]
            od.line(seg, fill=(0, 0, 0, 120), width=6 * SUPER, joint="curve")
            od.line(seg, fill=PALETTE.transfer, width=3 * SUPER)

    for i in range(len(day.rides)):
        pts = scene.pixels[i]
        if len(pts) < 2:
            continue
        view = scaled(pts)
        if i != active:
            # Dark casing keeps faint routes readable over roads of any colour.
            od.line(view, fill=PALETTE.casing, width=10 * SUPER, joint="curve")
        if i < active:
            # A finished leg stays in the route's hue family, only brighter.
            # White here landed on a neutral grey over the dark basemap, so a leg
            # appeared to lose its colour the moment the camera moved on.
            od.line(view, fill=PALETTE.done, width=5 * SUPER, joint="curve")
        elif i > active:
            od.line(view, fill=PALETTE.ahead, width=5 * SUPER, joint="curve")
        else:
            od.line(view, fill=PALETTE.casing, width=15 * SUPER, joint="curve")
            # The road ahead within the current leg.  The old alpha of 46 was
            # invisible, so the moving head appeared to travel into nothing.
            od.line(view, fill=PALETTE.ahead_current, width=6 * SUPER, joint="curve")
            travelled = path_until(pts, scene.cumulative_m[i], fraction)
            if len(travelled) >= 2:
                tv = scaled(travelled)
                od.line(tv, fill=(0, 0, 0, 150), width=13 * SUPER, joint="curve")
                od.line(tv, fill=PALETTE.travelled, width=7 * SUPER, joint="curve")

    # BILINEAR is visually equivalent to LANCZOS for an exact 2x downsample and
    # roughly halves the per-frame cost.
    overlay = overlay.resize(out_size, Image.BILINEAR)
    frame = Image.alpha_composite(frame, overlay)

    # Moving head with a soft glow.
    head = point_at(scene.pixels[active], scene.cumulative_m[active], fraction)
    hx, hy = _to_view([head], box, out_size)[0]
    if -80 < hx < out_size[0] + 80 and -80 < hy < out_size[1] + 80:
        # Blur a small tile around the head instead of the whole 1080x1920 canvas.
        pulse = 30 + 5 * math.sin(t * 5.0)
        pad = int(pulse) + 48
        tile = Image.new("RGBA", (pad * 2, pad * 2), (0, 0, 0, 0))
        ImageDraw.Draw(tile).ellipse(
            [pad - pulse, pad - pulse, pad + pulse, pad + pulse], fill=ACCENT + (95,))
        tile = tile.filter(ImageFilter.GaussianBlur(16))
        ox, oy = int(hx) - pad, int(hy) - pad
        # alpha_composite rejects negative offsets, so trim the tile at the edges.
        cx0, cy0 = max(0, -ox), max(0, -oy)
        cx1 = tile.width - max(0, ox + tile.width - out_size[0])
        cy1 = tile.height - max(0, oy + tile.height - out_size[1])
        if cx1 > cx0 and cy1 > cy0:
            frame.alpha_composite(tile.crop((cx0, cy0, cx1, cy1)), (ox + cx0, oy + cy0))
        d0 = ImageDraw.Draw(frame)
        d0.ellipse([hx - 14, hy - 14, hx + 14, hy + 14], fill=(255, 255, 255, 255))
        d0.ellipse([hx - 8.5, hy - 8.5, hx + 8.5, hy + 8.5], fill=ACCENT + (255,))

    _draw_chrome(frame, t, scene, segments, seg, active, out_size)
    return frame.convert("RGB")


def _draw_chrome(
    frame: Image.Image,
    t: float,
    scene: Scene,
    segments: list[Segment],
    seg: Segment,
    active: int,
    out_size: tuple[int, int],
) -> None:
    day = scene.day
    W, H = out_size
    top_h, bot_h = 330, 660
    frame.paste(top := _scrim(out_size, top_h, True, 218), (0, 0), top)
    frame.paste(bot := _scrim(out_size, bot_h, False, 240), (0, H - bot_h), bot)
    d = ImageDraw.Draw(frame, "RGBA")

    # Header: fades in during the intro.
    intro = segments[0]
    reveal = ease_out(intro.progress(t)) if seg.kind == "intro" else 1.0
    a = int(255 * reveal)
    dt = day.rides[0].start_dt
    d.text((64, 78), f"{dt.year}年{dt.month}月{dt.day}日", font=font(62, SEMIBOLD), fill=INK + (a,))
    duration = duration_short(day.riding_seconds)
    stat = f"{len(day.rides)} 段骑行 · {duration} · {day.total_distance_m/1000:.1f} 公里"
    d.text((64, 158), stat, font=font(36, MEDIUM), fill=MUTED + (a,))

    ride = day.rides[active]
    cy = H - 452

    if seg.kind == "outro":
        # Land on the day's totals rather than holding the last ride's card.
        k = ease_out(seg.progress(t))
        fade = int(255 * min(1.0, k * 1.6))
        d.text((64, cy + 4), "合计", font=font(34, SEMIBOLD), fill=ACCENT + (fade,))
        d.text((64, cy + 58), f"{day.total_distance_m/1000:.1f} 公里",
               font=font(72, SEMIBOLD), fill=INK + (fade,))
        span = f"{day.rides[0].start_dt:%H:%M} – {day.rides[-1].end_dt:%H:%M}"
        ride_t = duration_short(day.riding_seconds)
        d.text((64, cy + 156),
               f"{len(day.rides)} 段 · 骑行 {ride_t} · {span}",
               font=font(36, MEDIUM), fill=MUTED + (fade,))
    elif seg.kind == "gap":
        wait = (day.rides[active + 1].start_ms - ride.end_ms) / 60000 if active + 1 < len(day.rides) else 0
        if seg.transfer_km >= TRANSFER_KM:
            hours_g, minutes_g = divmod(int(wait), 60)
            waited = duration_short(wait * 60) if hours_g else f"{minutes_g} 分钟"
            nxt = day.rides[active + 1] if active + 1 < len(day.rides) else ride
            d.text((64, cy + 4), "转场", font=font(34, SEMIBOLD), fill=ACCENT)
            d.text((64, cy + 58), f"移动 {seg.transfer_km:.0f} 公里",
                   font=font(64, SEMIBOLD), fill=INK)
            label, ft = fit_text(d, f"间隔 {waited} · 下一段从 {nxt.start_name or '未知地点'} 开始",
                                 W - 128, 34, weight=MEDIUM, min_size=26)
            d.text((64, cy + 152), label, font=ft, fill=MUTED)
            d.text((64, cy + 198), "非骑行移动", font=font(30, MEDIUM), fill=DIM)
        else:
            d.text((64, cy + 20), "等待", font=font(34, MEDIUM), fill=DIM)
            d.text((64, cy + 74), f"{wait:.0f} 分钟", font=font(64, SEMIBOLD), fill=INK)
            d.text((64, cy + 168), "下一段即将开始", font=font(36, MEDIUM), fill=MUTED)
    else:
        d.text((64, cy), f"第 {active + 1:02d} 段 / {len(day.rides):02d}", font=font(34, SEMIBOLD), fill=ACCENT)
        label, f1 = fit_text(d, ride.start_name or "未知地点", W - 128, 52)
        d.text((64, cy + 56), label, font=f1, fill=INK)
        d.text((64, cy + 130), "→", font=font(36, MEDIUM), fill=DIM)
        label2, f2 = fit_text(d, ride.end_name or "未知地点", W - 196, 52)
        d.text((122, cy + 126), label2, font=f2, fill=INK)
        now = datetime.fromtimestamp(seg.real_ms(t) / 1000, ride.start_dt.tzinfo)
        meta = (f"{ride.start_dt:%H:%M} → {ride.end_dt:%H:%M}   {ride.duration_text}"
                f"   {ride.distance_m/1000:.1f} 公里")
        d.text((64, cy + 206), meta, font=font(36, MEDIUM), fill=MUTED)
        d.text((W - 64 - d.textlength(f"{now:%H:%M}", font=font(40, SEMIBOLD)), cy + 202),
               f"{now:%H:%M}", font=font(40, SEMIBOLD), fill=ACCENT)

    # Progress bar: width proportional to each ride's real duration.
    by = H - 132
    total_ms = sum(r.end_ms - r.start_ms for r in day.rides) or 1
    avail = W - 128 - 4 * (len(day.rides) - 1)
    x = 64.0
    for i, r in enumerate(day.rides):
        w = (r.end_ms - r.start_ms) / total_ms * avail
        # The bar mirrors the map: same three states, same hues, so a glance at
        # either tells the same story.
        if i < active:
            fill = PALETTE.done
        else:
            fill = PALETTE.ahead[:3] + (70,)
        d.rounded_rectangle([x, by, x + w, by + 9], radius=4, fill=fill)
        if i == active and seg.kind == "ride":
            d.rounded_rectangle([x, by, x + w * seg.progress(t), by + 9], radius=4, fill=ACCENT + (255,))
        elif i == active:
            d.rounded_rectangle([x, by, x + w, by + 9], radius=4, fill=ACCENT + (255,))
        x += w + 4

    d.text((64, by + 34), f"{day.rides[0].start_dt:%H:%M}", font=font(28, MEDIUM), fill=DIM)
    end_label = f"{day.rides[-1].end_dt:%H:%M}"
    d.text((W - 64 - d.textlength(end_label, font=font(28, MEDIUM)), by + 34),
           end_label, font=font(28, MEDIUM), fill=DIM)

    if day.has_inferred:
        note = "部分路径为地图导航推算，非 GPS 原始轨迹"
        d.text((64, H - 52), note, font=font(26, REGULAR), fill=DIM)

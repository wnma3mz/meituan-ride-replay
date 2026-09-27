"""Drive the frame loop and pipe raw frames into ffmpeg."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import basemap, camera
from . import frame as frame_mod
from .load import Day

OUT_SIZE = (1080, 1920)
OVERSAMPLE = 1.5
FPS = 30

# Below this a file is a container header with no frames, not a video.
MIN_USABLE_BYTES = 100_000


def _base_size() -> tuple[int, int]:
    return (int(OUT_SIZE[0] * OVERSAMPLE), int(OUT_SIZE[1] * OVERSAMPLE))


def _discard(output: Path) -> None:
    """Remove a half-written file so a later run retries instead of skipping."""
    try:
        output.unlink(missing_ok=True)
    except OSError:
        pass


def render_day(
    day: Day,
    output: Path,
    ride_seconds: float = 26.0,
    fps: int = FPS,
    progress: bool = True,
) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg")

    base_size = _base_size()
    flat = [p for r in day.rides for p in r.path]

    # One wide basemap carries every shot: the camera crops into it, so map
    # labels never pop and we pay the snapshot cost once.
    wide_shot = basemap.shot_for(day.bbox(), base_size, flat, padding=1.25)
    maps = basemap.render([wide_shot])
    if not maps or maps[0] is None:
        raise RuntimeError("MapKit 底图渲染失败")
    wide = maps[0]

    scene = frame_mod.build_scene(day, wide.points)
    segments = camera.build_timeline(day, ride_seconds=ride_seconds)
    duration = camera.total_duration(segments)
    wide_crop = camera.Crop(base_size[0] / 2, base_size[1] / 2, 1.0)
    ride_crops = [camera.ride_crop(scene.pixels[i], base_size) for i in range(len(day.rides))]

    total_frames = max(1, int(round(duration * fps)))
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{OUT_SIZE[0]}x{OUT_SIZE[1]}", "-framerate", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-crf", "19", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output),
    ]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for i in range(total_frames):
            t = i / fps
            crop = camera.camera_for(t, segments, wide_crop, ride_crops)
            img = frame_mod.render_frame(t, scene, segments, crop, wide.image, OUT_SIZE)
            proc.stdin.write(img.tobytes())
            if progress and (i % 30 == 0 or i == total_frames - 1):
                pct = (i + 1) / total_frames * 100
                print(f"\r  渲染 {i+1}/{total_frames} 帧 ({pct:.0f}%)", end="", file=sys.stderr, flush=True)
    except BrokenPipeError as exc:
        proc.stdin.close()
        proc.wait()
        _discard(output)
        raise RuntimeError("ffmpeg 中断") from exc
    finally:
        if not proc.stdin.closed:
            proc.stdin.close()
    if progress:
        print(file=sys.stderr)
    if proc.wait() != 0:
        _discard(output)
        raise RuntimeError("ffmpeg 编码失败")
    # ffmpeg can exit 0 having written only a container header (~48 bytes).
    # Leaving that behind is worse than failing: the next batch run sees a file
    # and skips the date forever.
    if output.exists() and output.stat().st_size < MIN_USABLE_BYTES:
        size = output.stat().st_size
        _discard(output)
        raise RuntimeError(f"ffmpeg 只写出 {size} 字节，视频无效")

    return {
        "output": str(output),
        "seconds": round(duration, 2),
        "frames": total_frames,
        "rides": len(day.rides),
        "distanceKm": round(day.total_distance_m / 1000, 1),
        "hasInferred": day.has_inferred,
    }

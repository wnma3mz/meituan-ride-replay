"""Render many groups in parallel worker processes.

Each worker renders one whole video: MapKit snapshots and ffmpeg encoding are
already multi-threaded, so a modest worker count saturates the machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..paths import PROJECT_ROOT

DEFAULT_WORKERS = max(2, min(5, (os.cpu_count() or 4) // 2))

# An MP4 that ffmpeg opened but never wrote frames into is ~48 bytes.  Treating
# "file exists" as success let such a stub count as rendered, and the next run
# then skipped the date because the file was there.
MIN_VIDEO_BYTES = 100_000

# The worker uses this exit code when a day has no qualifying journey, which is
# a filtering outcome rather than an error.
EXIT_NOTHING_TO_RENDER = 3


@dataclass
class Job:
    key: str            # stable output name, e.g. 2025-04-04
    source: str         # a date, or a group directory path
    kind: str           # "day" | "group"


def discover_days(days_root: Path) -> list[Job]:
    """One job per selected day.

    A day is the unit because that is what the selection rules pick out: rides
    that connect are joined inside the video, and rides that do not are marked as
    transfers.  Grouping first would silently drop any day whose rides never
    connect — 20 of 49 days, including one 92-minute ride.
    """
    jobs: list[Job] = []
    for path in sorted(days_root.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not (document.get("orders") or []):
            continue
        jobs.append(Job(key=path.stem, source=path.stem, kind="day"))
    return jobs


def run(
    jobs: list[Job],
    out_dir: Path,
    workers: int = DEFAULT_WORKERS,
    overwrite: bool = False,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    python = sys.executable

    # A day may yield several journeys, so its outputs are `<date>.mp4` or
    # `<date>-1.mp4`, `<date>-2.mp4`.  Any existing file for the date counts as
    # done.
    def existing(key: str) -> list[Path]:
        return sorted(out_dir.glob(f"{key}.mp4")) + sorted(out_dir.glob(f"{key}-*.mp4"))

    todo = []
    skipped = []
    for job in jobs:
        if existing(job.key) and not overwrite:
            skipped.append(job.key)
        else:
            todo.append(job)

    results: list[dict] = []
    failures: list[dict] = []
    filtered: list[dict] = []
    started = time.time()
    running: list[tuple[Job, subprocess.Popen]] = []
    queue = list(todo)
    done_count = 0

    def launch(job: Job) -> tuple[Job, subprocess.Popen]:
        # --output-dir rather than --output: the worker decides how many files
        # the day produces and names them itself.
        cmd = [python, "-m", "ridevideo", str(job.source),
               "--output-dir", str(out_dir), "--quiet"]
        # Workers run as `-m`, so they need src/ importable regardless of cwd.
        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")}
        return (job, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, env=env))

    while queue or running:
        while queue and len(running) < workers:
            running.append(launch(queue.pop(0)))
        time.sleep(0.25)
        for item in list(running):
            job, proc = item
            if proc.poll() is None:
                continue
            running.remove(item)
            done_count += 1
            out, err = proc.communicate()
            produced = [p for p in existing(job.key) if p.stat().st_size >= MIN_VIDEO_BYTES]
            if proc.returncode == 0 and produced:
                try:
                    payload = json.loads(out)
                except json.JSONDecodeError:
                    payload = {"output": str(produced[0])}
                if isinstance(payload, list):
                    for entry in payload:
                        entry["key"] = entry.get("key", job.key)
                    results.extend(payload)
                    status = f"{len(payload)} 段"
                else:
                    payload["key"] = job.key
                    results.append(payload)
                    status = f"{payload.get('seconds', '?')}s"
            elif proc.returncode == EXIT_NOTHING_TO_RENDER:
                filtered.append({"key": job.key,
                                 "reason": (err or "").strip()[-200:]})
                status = "无符合条件的行程"
            else:
                failures.append({"key": job.key, "error": (err or out).strip()[-400:]})
                status = "失败"
            elapsed = time.time() - started
            total = len(todo)
            rate = done_count / elapsed if elapsed > 0 else 0
            eta = (total - done_count) / rate if rate > 0 else 0
            print(f"[{done_count}/{total}] {job.key} {status}"
                  f"  已用 {elapsed/60:.1f} 分  预计剩余 {eta/60:.1f} 分",
                  file=sys.stderr, flush=True)

    return {
        "rendered": len(results),
        "skipped": len(skipped),
        "filteredOut": len(filtered),
        "failed": len(failures),
        "elapsedMinutes": round((time.time() - started) / 60, 1),
        "videos": sorted(results, key=lambda r: r.get("key", "")),
        "filtered": filtered,
        "failures": failures,
    }

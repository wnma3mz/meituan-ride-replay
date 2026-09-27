#!/usr/bin/env python3
"""把一天的骑行记录渲染成一条竖屏回放视频。

一天就是一个视频：当天连得上的骑行段在片中自然接起来，
连不上的（相隔太远或太久）标成「转场」，一天的记录不会被丢掉。

    # 单天（读 data/three-years/days/ 或 data/rides-*.json）
    ride-video 2026-05-04

    # 批量跑全部入选日期
    ride-video --all-days --workers 5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import segments
from .paths import days_root, video_out
from .rides import batch, camera, geo, render, route
from .rides.load import attach_names, load_day, missing_names

OUT_DIR = video_out()
DAYS_ROOT = days_root()


def prepare(day, quiet: bool = False):
    """Fill in place names and infer any missing paths."""
    todo = missing_names(day)
    if todo:
        if not quiet:
            print(f"补齐 {len(todo)} 个地名…", file=sys.stderr)
        geo.resolve(todo)
        attach_names(day)
    inferred = route.apply(day)
    if inferred and not quiet:
        print(f"{inferred} 段缺少 GPS 轨迹，已用导航推算路径", file=sys.stderr)
    return day


def build_one(day, output: Path, ride_seconds: float | None, fps: int, quiet: bool) -> dict:
    seconds = ride_seconds if ride_seconds is not None else camera.suggest_ride_seconds(day)
    result = render.render_day(day, output, ride_seconds=seconds, fps=fps, progress=not quiet)
    result["key"] = day.title or day.date
    return result


def plan_day(date: str, quiet: bool = False):
    """Load a date, infer missing paths, then decide which videos it yields.

    Path inference has to come first: an endpoint-only ride has no real distance
    until route.swift has run, and the distance filters depend on it.
    """
    day = load_day(date)
    if not day.rides:
        return day, [], {"date": date, "rides": 0, "journeys": 0}
    prepare(day, quiet=quiet)
    journeys, report = segments.plan(day)
    if not quiet:
        if report["invalidRides"]:
            detail = ", ".join(
                f"{d['durationMin']:.0f}分/{d['km']:.1f}km/{d['speedKmh']:.1f}km/h"
                for d in report["invalidDetail"]
            )
            print(f"跳过 {report['invalidRides']} 段无效骑行（{detail}）", file=sys.stderr)
        if report["tooShortJourneys"]:
            print(f"跳过 {report['tooShortJourneys']} 段里程不足 "
                  f"{report['rules']['minTotalKm']:.0f}km 的行程", file=sys.stderr)
        if len(journeys) > 1:
            print(f"{date} 拆成 {len(journeys)} 段行程", file=sys.stderr)
    return day, journeys, report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="生成骑行回放视频",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("date", nargs="?", help="北京时间日期，如 2026-05-04")
    source.add_argument("--all-days", action="store_true",
                        help="批量渲染全部入选日期，每天一个视频")

    parser.add_argument("--ride-seconds", type=float, default=None,
                        help="分配给骑行段的总时长（秒）；默认按内容自适应")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None, help="批量模式的输出目录")
    parser.add_argument("--workers", type=int, default=batch.DEFAULT_WORKERS,
                        help="批量模式的并行进程数")
    parser.add_argument("--overwrite", action="store_true", help="批量模式下重渲染已存在的视频")
    parser.add_argument("--days-root", type=Path, default=DAYS_ROOT,
                        help="入选日期 JSON 所在目录")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.all_days:
        jobs = batch.discover_days(args.days_root)
        if not jobs:
            raise SystemExit(
                f"{args.days_root} 下没有找到入选日期；先跑 ride-data bulk")
        out_dir = args.output_dir or OUT_DIR
        print(f"{len(jobs)} 天，{args.workers} 路并行 → {out_dir}", file=sys.stderr)
        summary = batch.run(jobs, out_dir, workers=args.workers, overwrite=args.overwrite)
        (out_dir / "index.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in summary.items() if k != "videos"},
                         ensure_ascii=False, indent=2))
        raise SystemExit(1 if summary["failed"] else 0)

    day, journeys, report = plan_day(args.date, quiet=args.quiet)
    if not journeys:
        print(f"{args.date} 没有符合条件的行程"
              f"（{report['rides']} 段骑行，{report.get('invalidRides', 0)} 段无效，"
              f"{report.get('tooShortJourneys', 0)} 段里程不足）", file=sys.stderr)
        raise SystemExit(batch.EXIT_NOTHING_TO_RENDER)

    if args.output and len(journeys) > 1:
        raise SystemExit(
            f"{args.date} 拆成 {len(journeys)} 段行程，--output 只能指定一个文件；"
            "请改用 --output-dir")

    results = []
    for journey in journeys:
        target = args.output or (OUT_DIR / f"{journey.key}.mp4")
        if args.output_dir:
            target = args.output_dir / f"{journey.key}.mp4"
        result = build_one(segments.day_for(day, journey), target,
                           args.ride_seconds, args.fps, args.quiet)
        result["totalKm"] = round(journey.total_km, 1)
        results.append(result)
    print(json.dumps(results[0] if len(results) == 1 else results,
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

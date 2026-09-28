#!/usr/bin/env python3
"""Re-select days from an existing detail cache, without refetching anything.

Once `ride-data bulk` has fetched details, changing which days you care about is
a local operation.  Rules that test track provenance (`has_full_track`,
`all_full_track`) only work here, because the list stage does not know it yet.

    ride-data select --filter real-gps
    ride-data select --list-filters

The previous selection is archived rather than deleted, so two criteria can be
compared side by side.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from ..common import read_json, write_csv, write_json
from ..filters import (
    DEFAULT_RULE,
    DayMetrics,
    FilterError,
    describe_rules,
    get_rule,
)
from ..paths import filters_file, three_years

ARCHIVED_NAMES = [
    "days", "details", "summary.json",
    "qualifying-days.csv", "connected-groups.csv",
]

CSV_FIELDS = [
    "date", "orderCount", "totalDurationSeconds", "maxSingleDurationSeconds",
    "maxSingleDurationText", "gpsOrderCount", "detailsFetched",
    "detailsSucceeded", "connectionCount", "filter",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", type=Path, default=three_years())
    parser.add_argument(
        "--filter", dest="filter_name", default=DEFAULT_RULE,
        help=f"筛选规则名，默认 {DEFAULT_RULE}",
    )
    parser.add_argument("--filters-file", type=Path, default=None)
    parser.add_argument(
        "--list-filters", action="store_true",
        help="列出可用筛选规则后退出",
    )
    parser.add_argument(
        "--archive-name", default=None,
        help="归档目录名，默认用上一次的筛选规则名",
    )
    return parser.parse_args(argv)


def move_to_archive(root: Path, archive: Path) -> None:
    archive.mkdir(parents=True, exist_ok=True)
    for name in ARCHIVED_NAMES:
        source = root / name
        if not source.exists():
            continue
        target = archive / name
        if target.exists():
            raise FileExistsError(f"归档目标已存在：{target}")
        shutil.move(str(source), str(target))


def previous_filter_name(root: Path) -> str:
    """Name the archive after the criterion that produced it."""
    summary = read_json(root / "summary.json", default={}) or {}
    name = summary.get("filter") or summary.get("thresholdMode")
    return str(name) if name else "previous"


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    filters_path = args.filters_file or filters_file()

    if args.list_filters:
        print(describe_rules(filters_path))
        return

    try:
        rule = get_rule(args.filter_name, filters_path)
    except FilterError as exc:
        raise SystemExit(str(exc)) from exc

    root = args.data_dir.resolve()
    archive_name = args.archive_name or previous_filter_name(root)
    archive = root / "archive" / archive_name
    if not archive.exists() or not (archive / "days").exists():
        move_to_archive(root, archive)

    source_days = archive / "days"
    # `bulk` keeps details/ as a shared long-lived cache while rotating only
    # the selected manifests.  Older archives may still contain their own copy.
    source_details = archive / "details"
    if not source_details.exists():
        source_details = root / "details"
    if not source_days.exists():
        raise SystemExit(
            f"找不到 {source_days}；先跑一次 ride-data bulk 建立详情缓存")

    target_days = root / "days"
    target_details = root / "details"
    target_days.mkdir(parents=True, exist_ok=True)
    target_details.mkdir(parents=True, exist_ok=True)

    selected_days: list[dict[str, Any]] = []
    considered = 0

    for source_day in sorted(source_days.glob("*.json")):
        document = json.loads(source_day.read_text(encoding="utf-8"))
        orders = document.get("orders") or []
        if not orders:
            continue
        considered += 1
        date = document["date"]
        summary = document.get("summary") or {}
        metrics = DayMetrics.from_orders(
            date, orders,
            connection_count=int(summary.get("connectionCount") or 0),
        )
        if not rule.matches(metrics):
            continue

        longest = max(orders, key=lambda order: order.get("durationSeconds") or 0)
        document["filter"] = rule.name
        document["filterDescription"] = rule.describe()
        document.setdefault("summary", {}).update({
            "selectionCriterion": rule.describe(),
            "filter": rule.name,
            "maxSingleDurationSeconds": metrics.max_single_seconds,
            "gpsOrderCount": metrics.gps_order_count,
        })
        selected_days.append({
            "date": date,
            "orderCount": metrics.order_count,
            "totalDurationSeconds": metrics.total_seconds,
            "maxSingleDurationSeconds": metrics.max_single_seconds,
            "maxSingleDurationText": longest.get("durationText"),
            "gpsOrderCount": metrics.gps_order_count,
            "detailsFetched": len(orders),
            "detailsSucceeded": sum(o.get("detailStatus") == "ok" for o in orders),
            "connectedGroups": summary.get("connectedGroups") or [],
            "connectionCount": metrics.connection_count,
            "filter": rule.name,
        })
        write_json(target_days / source_day.name, document)
        source_detail_day = source_details / date
        target_detail_day = target_details / date
        if source_detail_day.exists() and source_detail_day != target_detail_day:
            shutil.copytree(source_detail_day, target_detail_day,
                            dirs_exist_ok=True)

    old_summary = read_json(archive / "summary.json", default={}) or {}
    write_json(root / "summary.json", {
        "fromDate": old_summary.get("fromDate"),
        "toDate": old_summary.get("toDate"),
        "orderCount": old_summary.get("orderCount"),
        "dayCount": old_summary.get("dayCount"),
        "filter": rule.name,
        "filterDescription": rule.describe(),
        "filterConditions": rule.conditions,
        "qualifyingDayCount": len(selected_days),
        "qualifyingDays": selected_days,
        "note": (
            f"筛选规则 {rule.name}：{rule.describe()}。"
            f"上一次的筛选结果已归档到 archive/{archive_name}/。"
        ),
    })
    write_csv(root / "qualifying-days.csv", selected_days, CSV_FIELDS)

    print(f"筛选规则 {rule.name}：{rule.describe()}", file=sys.stderr)
    print(json.dumps({
        "filter": rule.name,
        "qualifyingDayCount": len(selected_days),
        "consideredDayCount": considered,
        "output": str(root),
        "archivedPreviousSelection": str(archive),
        "nextStep": "ride-video --all-days",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

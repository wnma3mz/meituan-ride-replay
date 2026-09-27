"""Single entry point for the fetch and prepare stages.

    ride-data fetch 2025-04-04 --har capture.har
    ride-data bulk --har capture.har
    ride-data bulk --list-filters
    ride-data select --filter real-gps

Rendering lives in the other package: `ride-video --all-days`.
"""
from __future__ import annotations

import sys

COMMANDS = {
    "fetch": ("ridedata.fetch", "抓取某一天的订单（含轨迹详情）"),
    "bulk": ("ridedata.bulk", "抓取多年历史，按筛选规则挑日期抓详情"),
    "select": ("ridedata.prepare.selection", "用已有详情缓存换一套筛选规则"),
}


def usage() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = ["用法: ride-data <命令> [参数...]", "", "命令:"]
    for name, (_, help_text) in COMMANDS.items():
        lines.append(f"  {name.ljust(width)}  {help_text}")
    lines += ["", "每个命令的完整参数用 ride-data <命令> --help 查看。"]
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help", "help"}:
        print(usage())
        raise SystemExit(0 if len(sys.argv) > 1 else 2)

    name = sys.argv[1]
    if name not in COMMANDS:
        print(f"未知命令: {name}\n", file=sys.stderr)
        print(usage(), file=sys.stderr)
        raise SystemExit(2)

    module_name = COMMANDS[name][0]
    # Hand the remaining argv to the subcommand so its own argparse sees a
    # normal command line, with a program name that reflects how it was invoked.
    rest = sys.argv[2:]
    sys.argv = [f"ride-data {name}", *rest]
    __import__(module_name)
    sys.modules[module_name].main(rest)


if __name__ == "__main__":
    main()

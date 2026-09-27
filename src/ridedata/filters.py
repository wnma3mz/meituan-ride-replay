"""Declarative day-selection rules.

Selection used to be two hardcoded modes (`single-order` / `daily-total`) with a
threshold baked into the CLI.  Rules now live in a YAML file so a new criterion
does not need a code change, and so the criterion that produced a dataset can be
version-controlled alongside it.

A rule is a set of conditions, all of which must hold (AND).  Each condition is
named after the metric it tests, so the file reads as a description of what you
wanted:

    rules:
      long-rides:
        description: 至少一笔单程超过 30 分钟
        any_order_duration_min: 30

      weekend-gps:
        has_full_track: true
        weekday: [6, 7]

Unknown condition names are rejected at load time rather than silently ignored,
because a typo that quietly matches every day is worse than an error.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import BEIJING, has_full_track, track_point_count

DEFAULT_RULE = "long-rides"


@dataclass
class DayMetrics:
    """Everything a rule may test about one calendar day."""
    date: str
    order_count: int
    total_seconds: float
    max_single_seconds: float
    gps_order_count: int
    connection_count: int

    @property
    def weekday(self) -> int:
        """ISO weekday: Monday is 1, Sunday is 7."""
        return datetime.fromisoformat(self.date).replace(tzinfo=BEIJING).isoweekday()

    @classmethod
    def from_orders(cls, date: str, orders: list[dict[str, Any]],
                    connection_count: int = 0) -> DayMetrics:
        durations = [float(o.get("durationSeconds") or 0) for o in orders]
        gps = sum(
            1 for o in orders
            if has_full_track(o.get("trackPointCount")
                              or track_point_count(o.get("trackPoints")))
        )
        return cls(
            date=date,
            order_count=len(orders),
            total_seconds=sum(durations),
            max_single_seconds=max(durations, default=0.0),
            gps_order_count=gps,
            connection_count=connection_count,
        )


# Each condition maps a YAML key to a predicate over the metrics.  Keeping them
# in one table is what makes unknown keys detectable.
Condition = Callable[[DayMetrics, Any], bool]

CONDITIONS: dict[str, Condition] = {
    # Duration, expressed in minutes because that is how the data reads.
    "any_order_duration_min": lambda m, v: m.max_single_seconds > float(v) * 60,
    "total_duration_min": lambda m, v: m.total_seconds > float(v) * 60,
    "max_total_duration_min": lambda m, v: m.total_seconds <= float(v) * 60,
    # Counts.
    "min_orders": lambda m, v: m.order_count >= int(v),
    "max_orders": lambda m, v: m.order_count <= int(v),
    "min_connections": lambda m, v: m.connection_count >= int(v),
    # Track provenance.
    "has_full_track": lambda m, v: (m.gps_order_count > 0) is bool(v),
    "all_full_track": lambda m, v: (m.gps_order_count == m.order_count) is bool(v),
    "min_gps_orders": lambda m, v: m.gps_order_count >= int(v),
    # Calendar.
    "weekday": lambda m, v: m.weekday in _as_int_list(v),
    "date_from": lambda m, v: m.date >= str(v),
    "date_to": lambda m, v: m.date <= str(v),
}

RESERVED_KEYS = {"description"}


def _as_int_list(value: Any) -> list[int]:
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    return [int(value)]


@dataclass
class Rule:
    name: str
    conditions: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def matches(self, metrics: DayMetrics) -> bool:
        return all(
            CONDITIONS[key](metrics, value)
            for key, value in self.conditions.items()
        )

    def describe(self) -> str:
        if self.description:
            return self.description
        if not self.conditions:
            return "不筛选，选中全部日期"
        return "; ".join(f"{k}={v}" for k, v in self.conditions.items())


# Used when no filters file exists, so a fresh clone behaves sensibly and the
# historical default (any single ride over 30 minutes) is preserved.
BUILTIN_RULES: dict[str, dict[str, Any]] = {
    "long-rides": {
        "description": "至少一笔单程骑行超过 30 分钟",
        "any_order_duration_min": 30,
    },
    "daily-total": {
        "description": "当日累计骑行超过 30 分钟",
        "total_duration_min": 30,
    },
    "real-gps": {
        "description": "当天至少有一笔含真实 GPS 轨迹",
        "has_full_track": True,
    },
    "all-gps": {
        "description": "当天每一笔都含真实 GPS 轨迹",
        "all_full_track": True,
    },
    "connected": {
        "description": "当天存在连续骑行组",
        "min_connections": 1,
    },
    "all": {
        "description": "不筛选，选中全部日期",
    },
}


class FilterError(ValueError):
    """Raised for an unknown rule name or an invalid condition."""


def _validate(name: str, spec: dict[str, Any]) -> Rule:
    unknown = set(spec) - set(CONDITIONS) - RESERVED_KEYS
    if unknown:
        known = ", ".join(sorted(CONDITIONS))
        raise FilterError(
            f"规则 {name} 含未知条件 {sorted(unknown)}；可用条件：{known}")
    conditions = {k: v for k, v in spec.items() if k not in RESERVED_KEYS}
    return Rule(name=name, conditions=conditions,
                description=str(spec.get("description", "")))


def load_rules(path: Path | None = None) -> dict[str, Rule]:
    """Load rules from YAML, falling back to the built-ins.

    A rule defined in the file shadows a built-in of the same name, so the
    defaults can be tuned without copying the whole set.
    """
    specs: dict[str, dict[str, Any]] = dict(BUILTIN_RULES)
    if path and path.exists():
        import yaml

        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise FilterError(f"{path} 不是合法 YAML：{exc}") from exc
        if not isinstance(document, dict):
            raise FilterError(f"{path} 顶层应是映射，含一个 rules 键")
        file_rules = document.get("rules") or {}
        if not isinstance(file_rules, dict):
            raise FilterError(f"{path} 的 rules 应是映射：规则名 → 条件")
        for name, spec in file_rules.items():
            if spec is None:
                spec = {}
            if not isinstance(spec, dict):
                raise FilterError(f"规则 {name} 应是映射，实际是 {type(spec).__name__}")
            specs[str(name)] = spec
    return {name: _validate(name, spec) for name, spec in specs.items()}


def get_rule(name: str, path: Path | None = None) -> Rule:
    rules = load_rules(path)
    if name not in rules:
        available = ", ".join(sorted(rules))
        raise FilterError(f"未知筛选规则 {name!r}；可用：{available}")
    return rules[name]


def describe_rules(path: Path | None = None) -> str:
    rules = load_rules(path)
    width = max(len(name) for name in rules)
    lines = ["可用筛选规则："]
    for name in sorted(rules):
        marker = " (默认)" if name == DEFAULT_RULE else ""
        lines.append(f"  {name.ljust(width)}  {rules[name].describe()}{marker}")
    lines += ["", "可用条件：" + ", ".join(sorted(CONDITIONS))]
    return "\n".join(lines)

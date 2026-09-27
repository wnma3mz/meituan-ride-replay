"""Tests for the declarative selection rules."""
from __future__ import annotations

import pytest

from ridedata.filters import (
    BUILTIN_RULES,
    CONDITIONS,
    DayMetrics,
    FilterError,
    Rule,
    describe_rules,
    get_rule,
    load_rules,
)


def orders(*specs):
    """Build order dicts from (minutes, track_points) pairs."""
    out = []
    for i, (minutes, points) in enumerate(specs):
        out.append({
            "orderId": str(i),
            "durationSeconds": minutes * 60,
            "trackPointCount": points,
        })
    return out


class TestDayMetrics:
    def test_derives_counts_and_durations(self):
        m = DayMetrics.from_orders("2025-04-04", orders((10, 2), (45, 30)))
        assert m.order_count == 2
        assert m.total_seconds == 55 * 60
        assert m.max_single_seconds == 45 * 60
        assert m.gps_order_count == 1   # only the 30-point ride counts

    def test_two_points_is_not_a_real_track(self):
        m = DayMetrics.from_orders("2025-04-04", orders((10, 2), (10, 2)))
        assert m.gps_order_count == 0

    def test_counts_track_points_from_string_when_no_count_field(self):
        m = DayMetrics.from_orders("2025-04-04", [{
            "durationSeconds": 600,
            "trackPoints": "1,2#3,4#5,6",
        }])
        assert m.gps_order_count == 1

    def test_empty_day(self):
        m = DayMetrics.from_orders("2025-04-04", [])
        assert m.order_count == 0
        assert m.max_single_seconds == 0

    def test_weekday_is_iso(self):
        # 2025-04-04 was a Friday.
        assert DayMetrics.from_orders("2025-04-04", []).weekday == 5
        # 2025-04-06 was a Sunday.
        assert DayMetrics.from_orders("2025-04-06", []).weekday == 7


class TestConditions:
    def test_duration_threshold_is_strict(self):
        """Exactly at the threshold does not qualify, matching the old code."""
        m = DayMetrics.from_orders("2025-04-04", orders((30, 2)))
        assert not Rule("r", {"any_order_duration_min": 30}).matches(m)
        assert Rule("r", {"any_order_duration_min": 29}).matches(m)

    def test_conditions_are_anded(self):
        m = DayMetrics.from_orders("2025-04-04", orders((40, 2), (5, 2)))
        assert Rule("r", {"any_order_duration_min": 30, "min_orders": 2}).matches(m)
        assert not Rule("r", {"any_order_duration_min": 30, "min_orders": 3}).matches(m)

    def test_has_full_track_both_ways(self):
        with_gps = DayMetrics.from_orders("2025-04-04", orders((10, 30)))
        without = DayMetrics.from_orders("2025-04-04", orders((10, 2)))
        assert Rule("r", {"has_full_track": True}).matches(with_gps)
        assert not Rule("r", {"has_full_track": True}).matches(without)
        assert Rule("r", {"has_full_track": False}).matches(without)

    def test_all_full_track(self):
        mixed = DayMetrics.from_orders("2025-04-04", orders((10, 30), (10, 2)))
        every = DayMetrics.from_orders("2025-04-04", orders((10, 30), (10, 8)))
        assert not Rule("r", {"all_full_track": True}).matches(mixed)
        assert Rule("r", {"all_full_track": True}).matches(every)

    def test_weekday_accepts_scalar_or_list(self):
        friday = DayMetrics.from_orders("2025-04-04", orders((10, 2)))
        assert Rule("r", {"weekday": 5}).matches(friday)
        assert Rule("r", {"weekday": [6, 7]}).matches(friday) is False

    def test_date_range(self):
        m = DayMetrics.from_orders("2025-04-04", orders((10, 2)))
        assert Rule("r", {"date_from": "2025-01-01", "date_to": "2025-12-31"}).matches(m)
        assert not Rule("r", {"date_from": "2025-05-01"}).matches(m)

    def test_empty_rule_matches_everything(self):
        m = DayMetrics.from_orders("2025-04-04", orders((1, 2)))
        assert Rule("all", {}).matches(m)

    @pytest.mark.parametrize("name", sorted(CONDITIONS))
    def test_every_condition_is_callable(self, name):
        """Guards against a typo in the CONDITIONS table."""
        m = DayMetrics.from_orders("2025-04-04", orders((10, 5)))
        sample = {"weekday": 5, "date_from": "2020-01-01", "date_to": "2030-01-01"}
        CONDITIONS[name](m, sample.get(name, 1))


class TestLoadRules:
    def test_builtins_available_without_a_file(self):
        rules = load_rules(None)
        assert set(BUILTIN_RULES) <= set(rules)

    def test_file_rules_shadow_builtins(self, tmp_path):
        path = tmp_path / "filters.yaml"
        path.write_text(
            "rules:\n  long-rides:\n    any_order_duration_min: 90\n",
            encoding="utf-8")
        assert load_rules(path)["long-rides"].conditions == {
            "any_order_duration_min": 90}

    def test_new_rule_adds_to_builtins(self, tmp_path):
        path = tmp_path / "filters.yaml"
        path.write_text(
            "rules:\n  mine:\n    min_orders: 4\n", encoding="utf-8")
        rules = load_rules(path)
        assert "mine" in rules and "long-rides" in rules

    def test_unknown_condition_is_rejected(self, tmp_path):
        """A typo must fail loudly, not silently match every day."""
        path = tmp_path / "filters.yaml"
        path.write_text(
            "rules:\n  oops:\n    any_order_duraton_min: 30\n", encoding="utf-8")
        with pytest.raises(FilterError, match="未知条件"):
            load_rules(path)

    def test_description_is_not_a_condition(self, tmp_path):
        path = tmp_path / "filters.yaml"
        path.write_text(
            "rules:\n  r:\n    description: hi\n    min_orders: 1\n", encoding="utf-8")
        assert load_rules(path)["r"].conditions == {"min_orders": 1}
        assert load_rules(path)["r"].describe() == "hi"

    def test_empty_rule_body_is_allowed(self, tmp_path):
        path = tmp_path / "filters.yaml"
        path.write_text("rules:\n  everything:\n", encoding="utf-8")
        assert load_rules(path)["everything"].conditions == {}

    def test_malformed_yaml_reports_the_file(self, tmp_path):
        path = tmp_path / "filters.yaml"
        path.write_text("rules:\n  - [unclosed\n", encoding="utf-8")
        with pytest.raises(FilterError):
            load_rules(path)

    def test_rules_must_be_a_mapping(self, tmp_path):
        path = tmp_path / "filters.yaml"
        path.write_text("rules:\n  - a\n  - b\n", encoding="utf-8")
        with pytest.raises(FilterError, match="rules"):
            load_rules(path)

    def test_unknown_rule_name_lists_alternatives(self):
        with pytest.raises(FilterError, match="可用"):
            get_rule("nope", None)

    def test_shipped_filters_file_is_valid(self):
        """The filters.yaml in the repo must load and match the built-ins."""
        from ridedata.paths import PROJECT_ROOT

        shipped = PROJECT_ROOT / "filters.yaml"
        if shipped.exists():
            rules = load_rules(shipped)
            assert "long-rides" in rules

    def test_describe_lists_rules(self):
        text = describe_rules(None)
        assert "long-rides" in text and "默认" in text


class TestDefaultEquivalence:
    def test_long_rides_matches_the_old_single_order_mode(self):
        """The default rule must behave exactly like the retired CLI mode."""
        rule = get_rule("long-rides", None)
        threshold = 30 * 60
        for minutes in (10, 29, 30, 31, 90):
            m = DayMetrics.from_orders("2025-04-04", orders((minutes, 2)))
            assert rule.matches(m) == (minutes * 60 > threshold)

    def test_daily_total_matches_the_old_aggregate_mode(self):
        rule = get_rule("daily-total", None)
        m = DayMetrics.from_orders("2025-04-04", orders((20, 2), (20, 2)))
        assert rule.matches(m)              # 40 minutes total
        m2 = DayMetrics.from_orders("2025-04-04", orders((10, 2), (10, 2)))
        assert not rule.matches(m2)         # 20 minutes total

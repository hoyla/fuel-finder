from datetime import datetime, timedelta, timezone
import ast
from pathlib import Path
import random
import statistics

import pytest

from scripts.audit_trend_weighting import PriceEvent, age_group_breakdowns, compare_prices
from scripts.audit_hampel import hampel_review
from price_history import apply_hampel, resolve_window


START = datetime(2026, 8, 11, tzinfo=timezone.utc)
END = START + timedelta(days=1)


def test_unchanged_station_retained_and_reporting_frequency_separated():
    events = [
        PriceEvent(1, "unchanged", START - timedelta(days=2), 100),
        PriceEvent(2, "frequent", START, 200),
        PriceEvent(3, "frequent", START + timedelta(hours=12), 220),
        PriceEvent(4, "once", START, 150),
    ]
    daily, station_days = compare_prices(events, START, END)
    assert daily[0]["event_weighted"] == 190
    assert daily[0]["equal_reporting_station"] == 180
    assert daily[0]["hourly_reporting_station"] == 180
    assert daily[0]["equal_station_hourly"] == pytest.approx(460 / 3)
    assert daily[0]["reporting_stations"] == 2
    assert daily[0]["reconstructed_stations"] == 3
    assert daily[0]["no_change_stations"] == 1
    assert daily[0]["carried_from_prior_day_hours"] == 24
    assert sum(row["eligible_hours"] for row in station_days) == 72


def test_flagged_latest_is_not_replaced_by_older_clean_price():
    events = [
        PriceEvent(1, "station", START - timedelta(days=1), 150),
        PriceEvent(2, "station", START + timedelta(hours=6), 1.5, False),
        PriceEvent(3, "station", START + timedelta(hours=18), 160),
    ]
    daily, station_days = compare_prices(events, START, END)
    assert daily[0]["equal_station_hourly"] == 155
    assert daily[0]["event_weighted"] == 160
    assert station_days[0]["eligible_hours"] == 12
    assert station_days[0]["flagged_hours"] == 12
    assert station_days[0]["source_record_ids"] == [1, 3]


def test_mid_hour_first_observation_and_end_boundary():
    events = [
        PriceEvent(1, "station", START + timedelta(minutes=30), 150),
        PriceEvent(2, "station", END, 999),
    ]
    daily, station_days = compare_prices(events, START, END)
    assert daily[0]["event_count"] == 1
    assert station_days[0]["unknown_hours"] == 1
    assert station_days[0]["eligible_hours"] == 23
    assert daily[0]["equal_station_hourly"] == 150


def test_ties_choose_highest_record_id_without_changing_event_weights():
    events = [PriceEvent(2, "station", START, 160), PriceEvent(1, "station", START, 150)]
    daily, station_days = compare_prices(events, START, END)
    assert daily[0]["event_weighted"] == 155
    assert daily[0]["equal_station_hourly"] == 160
    assert station_days[0]["source_record_ids"] == [2]


def test_equal_station_weighting_with_partial_day_coverage():
    events = [
        PriceEvent(1, "full", START, 100),
        PriceEvent(2, "partial", START + timedelta(hours=12), 200),
    ]
    daily, _ = compare_prices(events, START, END)
    assert daily[0]["equal_station_hourly"] == 150
    assert daily[0]["hour_weighted"] == pytest.approx(400 / 3)
    assert daily[0]["full_day_stations"] == 1


def test_empty_and_completely_flagged_series():
    daily, station_days = compare_prices([], START, END)
    assert daily[0]["equal_station_hourly"] is None
    assert station_days == []
    daily, station_days = compare_prices([PriceEvent(1, "flagged", START, 150, False)], START, END)
    assert daily[0]["event_weighted"] is None
    assert daily[0]["equal_station_hourly"] is None
    assert station_days[0]["flagged_hours"] == 24


def test_previous_period_price_and_no_future_leakage():
    events = [
        PriceEvent(1, "station", START - timedelta(days=3), 140),
        PriceEvent(2, "station", START + timedelta(days=1), 160),
    ]
    daily, _ = compare_prices(events, START, END + timedelta(days=1))
    assert daily[0]["equal_station_hourly"] == 140
    assert daily[0]["event_weighted"] is None
    assert daily[1]["equal_station_hourly"] == 160


def test_old_observation_age_is_measured_without_excluding_it():
    events = [PriceEvent(1, "station", START - timedelta(days=91), 140)]
    daily, station_days = compare_prices(events, START, END)
    assert daily[0]["equal_station_hourly"] == 140
    assert daily[0]["observation_over_30_days_hours"] == 24
    assert station_days[0]["observation_over_90_days_hours"] == 24


@pytest.mark.parametrize("start,end", [(END, START), (START, START), (START + timedelta(hours=1), END)])
def test_invalid_boundaries(start, end):
    with pytest.raises(ValueError):
        compare_prices([], start, end)


@pytest.mark.parametrize("limit", ["7", "14", "30"])
def test_age_cutoff_includes_exact_boundary_only(limit):
    event = PriceEvent(1, "station", START - timedelta(days=int(limit)), 140)
    daily, station_days = compare_prices([event], START, END, limit)
    assert daily[0]["equal_station_hourly"] == 140
    assert station_days[0]["eligible_hours"] == 1
    assert station_days[0]["age_excluded_hours"] == 23


def test_recorded_today_resets_at_each_historical_midnight():
    events = [
        PriceEvent(1, "station", START - timedelta(minutes=1), 140),
        PriceEvent(2, "station", START + timedelta(hours=12, minutes=1), 150),
    ]
    daily, station_days = compare_prices(events, START, END + timedelta(days=1), "today")
    assert daily[0]["equal_station_hourly"] == 150
    assert station_days[0]["eligible_hours"] == 11
    assert station_days[0]["age_excluded_hours"] == 13
    assert daily[1]["equal_station_hourly"] is None
    assert station_days[1]["age_excluded_hours"] == 24
    assert daily[1]["reconstructed_stations"] == 0


def test_new_report_restores_coverage_but_flagged_latest_does_not():
    events = [
        PriceEvent(1, "station", START - timedelta(days=8), 140),
        PriceEvent(2, "station", START + timedelta(hours=6), 150),
        PriceEvent(3, "station", START + timedelta(hours=12), 160, False),
    ]
    daily, station_days = compare_prices(events, START, END, "7")
    assert daily[0]["equal_station_hourly"] == 150
    assert station_days[0]["age_excluded_hours"] == 6
    assert station_days[0]["eligible_hours"] == 6
    assert station_days[0]["flagged_hours"] == 12
    assert station_days[0]["source_record_ids"] == [2]


def test_no_age_limit_keeps_old_prices_and_empty_threshold_is_null():
    events = [PriceEvent(1, "station", START - timedelta(days=100), 140)]
    unlimited, _ = compare_prices(events, START, END, "none")
    limited, _ = compare_prices(events, START, END, "30")
    assert unlimited[0]["equal_station_hourly"] == 140
    assert unlimited[0]["age_excluded_hours"] == 0
    assert limited[0]["equal_station_hourly"] is None
    assert limited[0]["age_excluded_hours"] == 24


def test_unknown_age_limit_rejected():
    with pytest.raises(ValueError, match="Unknown"):
        compare_prices([], START, END, "1")


def test_age_group_breakdowns_account_for_partial_and_fully_excluded_days():
    events = [
        PriceEvent(1, "partial", START - timedelta(days=7), 140),
        PriceEvent(2, "excluded", START - timedelta(days=8), 150),
        PriceEvent(3, "flagged", START, 160, False),
    ]
    _, unlimited = compare_prices(events, START, END)
    _, filtered = compare_prices(events, START, END, "7")
    groups = age_group_breakdowns(unlimited, filtered, {"partial": {"region": "North", "forecourt_type": "Supermarket"}})
    north = next(row for row in groups if row["dimension"] == "region" and row["group"] == "North")
    unknown = next(row for row in groups if row["dimension"] == "region" and row["group"] == "Unknown")
    assert north["included_stations"] == 1
    assert north["excluded_stations"] == 0
    assert north["included_hours"] == 1
    assert north["age_excluded_hours"] == 23
    assert unknown["no_limit_stations"] == 1
    assert unknown["excluded_stations"] == 1
    assert unknown["age_excluded_hours"] == 24
    assert sum(row["no_limit_stations"] for row in groups if row["dimension"] == "forecourt_type") == 2


@pytest.mark.parametrize("granularity", ["daily", "hourly"])
def test_hampel_audit_matches_actual_api_filter_block(granularity):
    source = Path(__file__).resolve().parents[1] / "web" / "api.py"
    function = next(node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == "price_history")
    block = next(node for node in function.body if isinstance(node, ast.If) and ast.unparse(node.test) == "len(rows) >= 3")
    executable = compile(ast.Module(body=[block], type_ignores=[]), str(source), "exec")
    generator = random.Random(42)
    samples = [[], [150], [150, 200], [149, 150, 151, 190, 149, 150, 151],
               [150, None, 151, 190, None, 149, 150, 151],
               [150, 150, 150, 200, 150, 150, 150],
               [round(150 + index * 0.4, 1) for index in range(100)],
               [round(150 + generator.uniform(-4, 4), 1) for _ in range(100)]]
    for values in samples:
        rows = [{"avg_price": value} for value in values if value is not None]
        namespace = {"rows": rows, "effective_granularity": granularity, "statistics": statistics}
        exec(executable, namespace)
        actual = [row["filtered"] for row in hampel_review(values, granularity) if row["raw"] is not None]
        assert actual == [row["avg_price"] for row in rows]


def test_hampel_preserves_raw_values_and_documents_replacement():
    values = [149, 150, 151, 190, 149, 150, 151]
    reviewed = hampel_review(values)
    assert reviewed[3]["raw"] == 190
    assert reviewed[3]["filtered"] == 150
    assert reviewed[3]["median"] == 150
    assert reviewed[3]["mad"] == 1
    assert reviewed[3]["threshold"] == pytest.approx(4.4478)
    assert reviewed[3]["changed"]
    assert reviewed[3]["window_points"] == 7
    assert values[3] == 190


def test_hampel_zero_mad_does_not_remove_a_spike():
    reviewed = hampel_review([150, 150, 150, 200, 150, 150, 150])
    assert reviewed[3]["mad"] == 0
    assert reviewed[3]["filtered"] == 200
    assert not reviewed[3]["flagged"]


def test_hampel_missing_buckets_are_not_filled_and_window_uses_rows():
    values = [149, None, 150, None, 151, 190, None, 149, 150, 151]
    reviewed = hampel_review(values)
    assert reviewed[1]["filtered"] is None
    assert reviewed[5]["filtered"] == 150
    assert reviewed[5]["window_start_index"] == 0
    assert reviewed[5]["window_end_index"] == 9
    assert reviewed[5]["window_points"] == 7


def test_hampel_monotonic_rise_and_sustained_step_are_retained():
    for values in ([150 + index for index in range(20)], [150] * 10 + [180] * 10):
        assert not any(row["changed"] for row in hampel_review(values))


def test_optional_hourly_output_matches_daily_reconstruction_and_gaps():
    events = [PriceEvent(1, "early", START, 100), PriceEvent(2, "late", START + timedelta(hours=12), 200)]
    hourly = []
    daily, _ = compare_prices(events, START, END, hourly_output=hourly)
    assert len(hourly) == 24
    assert hourly[0] == {"bucket": START, "avg_price": 100, "stations": 1}
    assert hourly[12]["avg_price"] == 150
    assert hourly[12]["stations"] == 2
    assert daily[0]["equal_station_hourly"] == 150
    empty = []
    compare_prices([], START, END, hourly_output=empty)
    assert empty[0]["avg_price"] is None
    assert empty[0]["stations"] == 0


def test_reconstruction_daily_and_hourly_are_chunk_independent():
    events = [PriceEvent(1, "station", START - timedelta(days=1), 150),
              PriceEvent(2, "station", START + timedelta(hours=23), 160),
              PriceEvent(3, "station", END + timedelta(hours=12), 170)]
    full_hours = []
    full_days, _ = compare_prices(events, START, END + timedelta(days=1), hourly_output=full_hours)
    chunked_hours = []
    first, _ = compare_prices(events, START, END, hourly_output=chunked_hours)
    second, _ = compare_prices(events, END, END + timedelta(days=1), hourly_output=chunked_hours)
    assert full_days == first + second
    assert full_hours == chunked_hours


@pytest.mark.parametrize("granularity", ["daily", "hourly"])
def test_live_hampel_retains_audited_policy_for_both_series(granularity):
    values = [149, 150, None, 151, 190, 149, 150, 151]
    rows = [{"avg_price": value, "age_price": value} for value in values]
    apply_hampel(rows, granularity)
    apply_hampel(rows, granularity, "age_price")
    expected = hampel_review(values, granularity)
    assert [row["avg_price"] for row in rows] == [row["filtered"] for row in expected]
    assert [row["age_price"] for row in rows] == [row["filtered"] for row in expected]
    assert [row["unsmoothed_avg_price"] for row in rows] == values


def test_live_history_window_caps_both_explicit_and_end_only_ranges():
    now = datetime(2026, 9, 10, 10, 15, tzinfo=timezone.utc)
    start, end, capped = resolve_window("2026-01-01", "2026-09-10", None, "readonly", now)
    assert (end.date() - start.date()).days == 89
    assert end == now
    assert capped
    start, end, capped = resolve_window(None, "2026-08-31", None, "readonly", now)
    assert (end - start).days == 90
    assert not capped
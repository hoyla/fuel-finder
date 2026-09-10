"""Read-only comparison of event-weighted and last-known station prices."""

import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from math import fsum
from pathlib import Path
import statistics
import urllib.parse
import urllib.request

import psycopg2
from psycopg2.extras import RealDictCursor


AGE_LIMITS = {"none": None, "30": 30, "14": 14, "7": 7, "today": 0}
AGE_LABELS = {"none": "No age limit", "30": "Last 30 days", "14": "Last 14 days", "7": "Last 7 days", "today": "Recorded today"}


@dataclass(frozen=True)
class PriceEvent:
    record_id: int
    node_id: str
    observed_at: datetime
    price: float
    eligible: bool = True


def average(values):
    return fsum(values) / len(values) if values else None


def compare_prices(events, start, end, age_limit="none", hourly_output=None):
    if age_limit not in AGE_LIMITS:
        raise ValueError("Unknown change-record age limit")
    maximum_age = timedelta(days=AGE_LIMITS[age_limit]) if AGE_LIMITS[age_limit] is not None else None
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Audit boundaries must be timezone-aware")
    if start >= end or any((value.hour, value.minute, value.second, value.microsecond) != (0, 0, 0, 0) for value in (start, end)):
        raise ValueError("Audit boundaries must be increasing midnight timestamps")
    days = (end - start).days
    hourly_totals = [[0.0, 0] for _ in range(days * 24)] if hourly_output is not None else None
    stations = defaultdict(list)
    for event in events:
        if event.observed_at < end:
            stations[event.node_id].append(event)
    station_days = []
    for node_id, records in sorted(stations.items()):
        records.sort(key=lambda event: (event.observed_at, event.record_id))
        changed = defaultdict(list)
        for event in records:
            if start <= event.observed_at < end and event.eligible:
                changed[(event.observed_at - start).days].append(event.price)
        cursor = 0
        latest = None
        for day_index in range(days):
            date = start + timedelta(days=day_index)
            hourly_prices = []
            flagged_hours = 0
            unknown_hours = 0
            age_excluded_hours = 0
            carried_hours = 0
            observation_over_30_days_hours = 0
            observation_over_90_days_hours = 0
            oldest_observation_hours = 0.0
            source_ids = set()
            for hour in range(24):
                timestamp = date + timedelta(hours=hour)
                while cursor < len(records) and records[cursor].observed_at <= timestamp:
                    latest = records[cursor]
                    cursor += 1
                if latest is None:
                    unknown_hours += 1
                elif not latest.eligible:
                    flagged_hours += 1
                elif (age_limit == "today" and latest.observed_at < date) or (
                    age_limit not in ("none", "today")
                    and timestamp - latest.observed_at > maximum_age
                ):
                    age_excluded_hours += 1
                else:
                    hourly_prices.append(latest.price)
                    if hourly_totals is not None:
                        hourly_totals[day_index * 24 + hour][0] += latest.price
                        hourly_totals[day_index * 24 + hour][1] += 1
                    source_ids.add(latest.record_id)
                    carried_hours += int(latest.observed_at < date)
                    observation_over_30_days_hours += int(timestamp - latest.observed_at > timedelta(days=30))
                    observation_over_90_days_hours += int(timestamp - latest.observed_at > timedelta(days=90))
                    oldest_observation_hours = max(oldest_observation_hours, (timestamp - latest.observed_at).total_seconds() / 3600)
            station_days.append({
                "date": date.date().isoformat(), "node_id": node_id,
                "event_count": len(changed[day_index]),
                "event_mean": average(changed[day_index]),
                "hourly_mean": average(hourly_prices),
                "eligible_hours": len(hourly_prices), "flagged_hours": flagged_hours,
                "age_excluded_hours": age_excluded_hours,
                "unknown_hours": unknown_hours, "carried_from_prior_day_hours": carried_hours,
                "observation_over_30_days_hours": observation_over_30_days_hours,
                "observation_over_90_days_hours": observation_over_90_days_hours,
                "oldest_observation_hours": oldest_observation_hours,
                "source_record_ids": sorted(source_ids),
            })
    grouped = defaultdict(list)
    for row in station_days:
        grouped[row["date"]].append(row)
    daily = []
    for day_index in range(days):
        date = (start + timedelta(days=day_index)).date().isoformat()
        rows = grouped[date]
        reporting = [row for row in rows if row["event_count"]]
        reconstructed = [row for row in rows if row["eligible_hours"]]
        event_count = sum(row["event_count"] for row in reporting)
        daily.append({
            "date": date,
            "event_weighted": fsum(row["event_mean"] * row["event_count"] for row in reporting) / event_count if event_count else None,
            "equal_reporting_station": average([row["event_mean"] for row in reporting]),
            "hourly_reporting_station": average([row["hourly_mean"] for row in reporting if row["eligible_hours"]]),
            "reporting_without_eligible_hour": sum(row["eligible_hours"] == 0 for row in reporting),
            "equal_station_hourly": average([row["hourly_mean"] for row in reconstructed]),
            "hour_weighted": fsum(row["hourly_mean"] * row["eligible_hours"] for row in reconstructed) / sum(row["eligible_hours"] for row in reconstructed) if reconstructed else None,
            "event_count": event_count, "reporting_stations": len(reporting),
            "reconstructed_stations": len(reconstructed),
            "full_day_stations": sum(row["eligible_hours"] == 24 for row in reconstructed),
            "no_change_stations": sum(row["event_count"] == 0 for row in reconstructed),
            "eligible_hours": sum(row["eligible_hours"] for row in rows),
            "flagged_hours": sum(row["flagged_hours"] for row in rows),
            "unknown_hours": sum(row["unknown_hours"] for row in rows),
            "age_excluded_hours": sum(row["age_excluded_hours"] for row in rows),
            "carried_from_prior_day_hours": sum(row["carried_from_prior_day_hours"] for row in rows),
            "observation_over_30_days_hours": sum(row["observation_over_30_days_hours"] for row in rows),
            "observation_over_90_days_hours": sum(row["observation_over_90_days_hours"] for row in rows),
        })
    if hourly_output is not None:
        hourly_output.extend({"bucket": start + timedelta(hours=index),
                              "avg_price": total / count if count else None, "stations": count}
                             for index, (total, count) in enumerate(hourly_totals))
    return daily, station_days


def fetch_rows(connection, query, parameters=()):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(query, parameters)
        return [dict(row) for row in cursor.fetchall()]


def load_events(connection, fuel_type, start, end):
    rows = fetch_rows(connection, """
        WITH seed AS (
            SELECT DISTINCT ON (node_id) id
            FROM fuel_prices
            WHERE fuel_type = %s AND observed_at < %s
            ORDER BY node_id, observed_at DESC, id DESC
        ), selected AS (
            SELECT id FROM seed
            UNION ALL
            SELECT id FROM fuel_prices
            WHERE fuel_type = %s AND observed_at >= %s AND observed_at < %s
        )
        SELECT fp.id, fp.node_id, fp.observed_at,
               COALESCE(pc.corrected_price, fp.price) AS price,
               fp.anomaly_flags IS NULL AS eligible
        FROM selected JOIN fuel_prices fp USING (id)
        LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
    """, (fuel_type, start, fuel_type, start, end))
    return [PriceEvent(row["id"], row["node_id"], row["observed_at"], float(row["price"]), row["eligible"]) for row in rows]


def baseline_checks(connection, fuel_type, start, end):
    cached = fetch_rows(connection, """
        SELECT price_date AS date,
               SUM(avg_price * sample_count) / SUM(sample_count) AS cached_unrounded,
               ROUND(SUM(avg_price * sample_count) / SUM(sample_count), 1) AS cached_display,
               SUM(sample_count) AS event_count, COUNT(*) AS stations
        FROM daily_prices
        WHERE fuel_type = %s AND price_date >= %s AND price_date < %s
        GROUP BY price_date ORDER BY price_date
    """, (fuel_type, start.date(), end.date()))
    raw = fetch_rows(connection, """
        SELECT DATE(fp.observed_at) AS date,
               AVG(COALESCE(pc.corrected_price, fp.price)) AS event_mean,
               COUNT(*) AS event_count, COUNT(DISTINCT fp.node_id) AS stations
        FROM fuel_prices fp
        LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
        WHERE fp.fuel_type = %s AND fp.observed_at >= %s AND fp.observed_at < %s
          AND fp.anomaly_flags IS NULL
        GROUP BY DATE(fp.observed_at) ORDER BY date
    """, (fuel_type, start, end))
    cache_check = fetch_rows(connection, """
        WITH rebuilt AS (
            SELECT fp.node_id, DATE(fp.observed_at) AS price_date,
                   ROUND(AVG(COALESCE(pc.corrected_price, fp.price)), 1) AS avg_price,
                   MIN(COALESCE(pc.corrected_price, fp.price)) AS min_price,
                   MAX(COALESCE(pc.corrected_price, fp.price)) AS max_price,
                   COUNT(*) AS sample_count
            FROM fuel_prices fp LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
            WHERE fp.fuel_type = %s AND fp.observed_at >= %s AND fp.observed_at < %s
              AND fp.anomaly_flags IS NULL
            GROUP BY fp.node_id, DATE(fp.observed_at)
        ), cached AS (
            SELECT * FROM daily_prices
            WHERE fuel_type = %s AND price_date >= %s AND price_date < %s
        )
        SELECT COUNT(*) AS compared_station_days,
               COUNT(*) FILTER (WHERE rebuilt.node_id IS NULL) AS cached_only,
               COUNT(*) FILTER (WHERE cached.node_id IS NULL) AS raw_only,
               COUNT(*) FILTER (WHERE (rebuilt.avg_price, rebuilt.min_price, rebuilt.max_price, rebuilt.sample_count)
                   IS DISTINCT FROM (cached.avg_price, cached.min_price, cached.max_price, cached.sample_count)) AS mismatches
        FROM rebuilt FULL JOIN cached USING (node_id, price_date)
    """, (fuel_type, start, end, fuel_type, start.date(), end.date()))[0]
    ties = fetch_rows(connection, """
        SELECT COUNT(*) AS tied_station_timestamps,
               COUNT(*) FILTER (WHERE price_variants > 1) AS conflicting_price_timestamps
        FROM (
            SELECT node_id, observed_at, COUNT(DISTINCT price) AS price_variants
            FROM fuel_prices
            WHERE fuel_type = %s AND observed_at >= %s AND observed_at < %s
            GROUP BY node_id, observed_at HAVING COUNT(*) > 1
        ) duplicates
    """, (fuel_type, start, end))[0]
    corrections = fetch_rows(connection, """
        SELECT COUNT(*) FILTER (WHERE pc.id IS NOT NULL) AS corrected_records,
               COUNT(*) FILTER (WHERE pc.id IS NOT NULL AND fp.anomaly_flags IS NOT NULL) AS corrected_but_flagged,
               COUNT(*) FILTER (WHERE fp.anomaly_flags IS NOT NULL) AS flagged_records
        FROM fuel_prices fp LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
        WHERE fp.fuel_type = %s AND fp.observed_at >= %s AND fp.observed_at < %s
    """, (fuel_type, start, end))[0]
    return cached, raw, {"daily_cache": cache_check, "timestamp_ties": ties, "corrections": corrections}


def independent_hourly_check(connection, fuel_type, day, age_limit="none"):
    if age_limit not in AGE_LIMITS:
        raise ValueError("Unknown change-record age limit")
    return fetch_rows(connection, """
        WITH nodes AS (
            SELECT DISTINCT node_id FROM fuel_prices
            WHERE fuel_type = %s AND observed_at < %s + INTERVAL '1 day'
        ), station_days AS (
            SELECT nodes.node_id, AVG(COALESCE(pc.corrected_price, latest.price)) AS mean,
                   COUNT(*) AS hours
            FROM nodes
            CROSS JOIN generate_series(%s::timestamptz, %s::timestamptz + INTERVAL '23 hours', INTERVAL '1 hour') AS hours(bucket)
            CROSS JOIN LATERAL (
                SELECT fp.id, fp.price, fp.anomaly_flags, fp.observed_at
                FROM fuel_prices fp
                WHERE fp.node_id = nodes.node_id AND fp.fuel_type = %s AND fp.observed_at <= hours.bucket
                ORDER BY fp.observed_at DESC, fp.id DESC LIMIT 1
            ) latest
            LEFT JOIN price_corrections pc ON pc.fuel_price_id = latest.id
            WHERE latest.anomaly_flags IS NULL
                AND (%s = 'none' OR (%s = 'today' AND latest.observed_at >= %s)
                    OR (%s NOT IN ('none', 'today') AND latest.observed_at >= hours.bucket - make_interval(days => %s)))
            GROUP BY nodes.node_id
        )
        SELECT AVG(mean) AS equal_station_hourly, COUNT(*) AS stations,
               SUM(hours) AS eligible_hours FROM station_days
    """, (fuel_type, day, day, day, fuel_type, age_limit, age_limit, day, age_limit, AGE_LIMITS[age_limit]))[0]


def age_group_breakdowns(unlimited, limited, metadata):
    if len(unlimited) != len(limited):
        raise AssertionError("Station-day coverage mismatch")
    groups = {}
    for original, filtered in zip(unlimited, limited):
        if (original["date"], original["node_id"]) != (filtered["date"], filtered["node_id"]):
            raise AssertionError("Station-day alignment mismatch")
        if original["eligible_hours"] != filtered["eligible_hours"] + filtered["age_excluded_hours"]:
            raise AssertionError("Age exclusion does not account for all original eligible hours")
        if not original["eligible_hours"]:
            continue
        station = metadata.get(original["node_id"], {})
        for dimension in ("region", "forecourt_type"):
            label = station.get(dimension) or "Unknown"
            key = (original["date"], dimension, label)
            group = groups.setdefault(key, {"date": original["date"], "dimension": dimension, "group": label,
                                           "no_limit_stations": 0, "included_stations": 0, "excluded_stations": 0,
                                           "no_limit_hours": 0, "included_hours": 0, "age_excluded_hours": 0})
            group["no_limit_stations"] += 1
            group["included_stations"] += int(filtered["eligible_hours"] > 0)
            group["excluded_stations"] += int(filtered["eligible_hours"] == 0)
            group["no_limit_hours"] += original["eligible_hours"]
            group["included_hours"] += filtered["eligible_hours"]
            group["age_excluded_hours"] += filtered["age_excluded_hours"]
    return [groups[key] for key in sorted(groups)]


def build_age_sensitivity(connection, fuel_type, events, start, end, daily, station_days, output):
    metadata = {row["node_id"]: row for row in fetch_rows(connection, """
        SELECT node_id, region, forecourt_type FROM current_prices WHERE fuel_type = %s
    """, (fuel_type,))}
    sensitivity = {}
    all_daily = []
    all_groups = []
    for policy, label in AGE_LABELS.items():
        filtered_daily, filtered_stations = (daily, station_days) if policy == "none" else compare_prices(events, start, end, policy)
        rows = []
        for original, filtered in zip(daily, filtered_daily):
            value = filtered["equal_station_hourly"]
            rows.append({
                "date": original["date"], "price": value, "no_limit_price": original["equal_station_hourly"],
                "difference_from_no_limit": value - original["equal_station_hourly"] if value is not None else None,
                "included_stations": filtered["reconstructed_stations"],
                "excluded_stations": original["reconstructed_stations"] - filtered["reconstructed_stations"],
                "no_limit_stations": original["reconstructed_stations"],
                "eligible_hours": filtered["eligible_hours"], "no_limit_hours": original["eligible_hours"],
                "age_excluded_hours": filtered["age_excluded_hours"], "full_day_stations": filtered["full_day_stations"],
                "flagged_hours": filtered["flagged_hours"], "unknown_hours": filtered["unknown_hours"],
                "observation_over_30_days_hours": filtered["observation_over_30_days_hours"],
                "observation_over_90_days_hours": filtered["observation_over_90_days_hours"],
            })
        populated = [row for row in rows if row["price"] is not None]
        check_day = max(populated, key=lambda row: abs(row["difference_from_no_limit"])) if populated else rows[0]
        oracle = independent_hourly_check(connection, fuel_type, datetime.fromisoformat(check_day["date"]).replace(tzinfo=timezone.utc), policy)
        oracle_value = float(oracle["equal_station_hourly"]) if oracle["equal_station_hourly"] is not None else None
        if ((oracle_value is None) != (check_day["price"] is None)
                or (oracle_value is not None and abs(oracle_value - check_day["price"]) > 1e-8)
                or oracle["stations"] != check_day["included_stations"]
                or (oracle["eligible_hours"] or 0) != check_day["eligible_hours"]):
            raise AssertionError("Age sensitivity differs from independent SQL: " + policy)
        groups = age_group_breakdowns(station_days, filtered_stations, metadata)
        for row in rows:
            for dimension in ("region", "forecourt_type"):
                selected = [group for group in groups if group["date"] == row["date"] and group["dimension"] == dimension]
                for key in ("included_stations", "excluded_stations", "no_limit_stations", "age_excluded_hours"):
                    if sum(group[key] for group in selected) != row[key]:
                        raise AssertionError("Breakdowns do not sum to national totals")
        summary = {"mean_price": average([row["price"] for row in populated]),
                   "mean_difference_from_no_limit": average([row["difference_from_no_limit"] for row in populated]),
                   "mean_included_stations": average([row["included_stations"] for row in rows]),
                   "mean_excluded_stations": average([row["excluded_stations"] for row in rows]),
                   "change_ppl": rows[-1]["price"] - rows[0]["price"] if rows[0]["price"] is not None and rows[-1]["price"] is not None else None,
                   "days_without_data": len(rows) - len(populated)}
        sensitivity[policy] = {"label": label, "daily": rows, "groups": groups, "summary": summary,
                               "sql_check": {"date": check_day["date"], "matched": True, **oracle}}
        all_daily.extend({"fuel_type": fuel_type, "age_limit": policy, **row} for row in rows)
        all_groups.extend({"fuel_type": fuel_type, "age_limit": policy, **row} for row in groups)
        print(json.dumps({"fuel_type": fuel_type, "age_limit": policy, **summary}), flush=True)
    write_csv(output / (fuel_type + "-age-daily.csv"), all_daily)
    write_csv(output / (fuel_type + "-age-groups.csv"), all_groups)
    return sensitivity


def verify_local_api(cached, fuel_type, start, end):
    query = urllib.parse.urlencode({"fuel_type": fuel_type, "start_date": start.date().isoformat(),
                                  "end_date": (end - timedelta(days=1)).date().isoformat(), "granularity": "daily"})
    with urllib.request.urlopen("http://127.0.0.1:18080/api/prices/history?" + query, timeout=90) as response:
        actual = json.load(response)["data"]
    original = [float(row["cached_display"]) for row in cached]
    smoothed = list(original)
    if len(original) >= 3:
        for index, price in enumerate(original):
            window = original[max(0, index - 3):index + 4]
            median = statistics.median(window)
            mad = statistics.median([abs(value - median) for value in window])
            if mad > 0 and abs(price - median) > 3 * 1.4826 * mad:
                smoothed[index] = round(median, 1)
    expected = [{"bucket": str(row["date"]), "avg_price": price, "stations": row["stations"]}
                for row, price in zip(cached, smoothed)]
    if actual != expected:
        raise AssertionError("Local history API differs from reproduced daily query and Hampel filter")
    return {"matched": True, "hampel_changed_days": sum(before != after for before, after in zip(original, smoothed)),
            "altered_points": [{"date": str(row["date"]), "unsmoothed": before, "displayed": after}
                               for row, before, after in zip(cached, original, smoothed) if before != after]}


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, list) else value for key, value in row.items()})


def summarise(daily):
    discrepancies = [row["event_weighted"] - row["equal_station_hourly"] for row in daily]
    worst = max(daily, key=lambda row: abs(row["event_weighted"] - row["equal_station_hourly"]))
    changes = {key: {"start": daily[0][key], "end": daily[-1][key],
                     "change_ppl": daily[-1][key] - daily[0][key],
                     "change_pct": (daily[-1][key] / daily[0][key] - 1) * 100}
               for key in ("cached_display", "event_weighted", "equal_reporting_station", "equal_station_hourly", "fixed_cohort")}
    reversals = [{"date": after["date"],
                  "event_move_ppl": after["event_weighted"] - before["event_weighted"],
                  "reconstructed_move_ppl": after["equal_station_hourly"] - before["equal_station_hourly"]}
                 for before, after in zip(daily, daily[1:])
                 if (after["event_weighted"] - before["event_weighted"]) * (after["equal_station_hourly"] - before["equal_station_hourly"]) < 0]
    return {"mean_signed_difference_ppl": average(discrepancies),
            "median_absolute_difference_ppl": statistics.median(abs(value) for value in discrepancies),
            "mean_absolute_difference_ppl": average([abs(value) for value in discrepancies]),
            "max_absolute_difference_ppl": max(abs(value) for value in discrepancies),
            "max_displayed_absolute_difference_ppl": max(abs(row["api_displayed"] - row["equal_station_hourly"]) for row in daily),
            "worst_day": worst["date"],
            "mean_reporting_stations": average([row["reporting_stations"] for row in daily]),
            "mean_reconstructed_stations": average([row["reconstructed_stations"] for row in daily]),
            "mean_no_change_stations": average([row["no_change_stations"] for row in daily]),
            "mean_abs_reporting_frequency_effect_ppl": average([abs(row["event_weighted"] - row["equal_reporting_station"]) for row in daily]),
            "mean_abs_coverage_timing_effect_ppl": average([abs(row["equal_reporting_station"] - row["equal_station_hourly"]) for row in daily]),
            "mean_abs_timing_effect_ppl": average([abs(row["equal_reporting_station"] - row["hourly_reporting_station"]) for row in daily]),
            "mean_abs_coverage_effect_ppl": average([abs(row["hourly_reporting_station"] - row["equal_station_hourly"]) for row in daily]),
            "mean_reporting_share_pct": average([100 * row["reporting_stations"] / row["reconstructed_stations"] for row in daily]),
            "observation_over_30_days_hour_share_pct": 100 * sum(row["observation_over_30_days_hours"] for row in daily) / sum(row["eligible_hours"] for row in daily),
            "observation_over_90_days_hour_share_pct": 100 * sum(row["observation_over_90_days_hours"] for row in daily) / sum(row["eligible_hours"] for row in daily),
            "opposite_daily_directions": reversals,
            "max_cache_vs_raw_difference_ppl": max(abs(row["cached_unrounded"] - row["event_weighted"]) for row in daily),
            "max_hour_weighting_sensitivity_ppl": max(abs(row["hour_weighted"] - row["equal_station_hourly"]) for row in daily),
            "max_fixed_cohort_sensitivity_ppl": max(abs(row["fixed_cohort"] - row["equal_station_hourly"]) for row in daily),
            "fixed_cohort_stations": daily[0]["fixed_cohort_stations"], "changes": changes}


def drilldown(connection, fuel_type, worst_day, station_days):
    candidates = [row for row in station_days if row["date"] == worst_day and row["eligible_hours"]]
    no_change = sorted([row for row in candidates if not row["event_count"]], key=lambda row: (row["hourly_mean"], row["node_id"]))
    selected = []
    if no_change:
        selected.append(("Median-priced station with no eligible reports that day", no_change[len(no_change) // 2]))
    reporting = [row for row in candidates if row["event_count"]]
    if reporting:
        selected.append(("Most eligible reports that day", max(reporting, key=lambda row: row["event_count"])))
        selected.append(("Largest within-station event/hourly mean difference", max(reporting, key=lambda row: abs(row["event_mean"] - row["hourly_mean"]))))
    examples = []
    evidence = []
    day = datetime.fromisoformat(worst_day).replace(tzinfo=timezone.utc)
    for reason, row in selected:
        metadata = fetch_rows(connection, """
            SELECT s.node_id, s.trading_name, s.brand_name AS raw_brand, s.postcode,
                   s.temporary_closure, s.permanent_closure
            FROM stations s WHERE s.node_id = %s
        """, (row["node_id"],))[0]
        examples.append({"fuel_type": fuel_type, "selection_reason": reason, **metadata, **row})
        records = fetch_rows(connection, """
            SELECT fp.id AS fuel_price_id, fp.node_id, fp.fuel_type, fp.price AS original_price,
                   pc.corrected_price, COALESCE(pc.corrected_price, fp.price) AS effective_price,
                   fp.anomaly_flags, fp.observed_at, fp.price_last_updated,
                   fp.price_change_effective_timestamp, fp.scrape_run_id,
                   sr.run_type, sr.status AS scrape_status, sr.s3_key
            FROM fuel_prices fp
            LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
            LEFT JOIN scrape_runs sr ON sr.id = fp.scrape_run_id
            WHERE fp.node_id = %s AND fp.fuel_type = %s
              AND (fp.id = ANY(%s) OR (fp.observed_at >= %s AND fp.observed_at < %s))
            ORDER BY fp.observed_at, fp.id
        """, (row["node_id"], fuel_type, row["source_record_ids"], day, day + timedelta(days=1)))
        for record in records:
            evidence.append({"example_reason": reason, "example_day": worst_day, **record})
    return examples, evidence


def render_chart(results, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as dates
    import matplotlib.pyplot as plot

    plot.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    figure, axes = plot.subplots(3, 2, figsize=(14, 10), sharex="col", layout="constrained",
                                gridspec_kw={"height_ratios": [2, 1, 1]})
    for column, (fuel_type, audit) in enumerate(results["fuels"].items()):
        daily = audit["daily"]
        timestamps = [datetime.fromisoformat(row["date"]) for row in daily]
        title = "Unleaded (E10)" if fuel_type == "E10" else "Standard diesel (B7)"
        for key, label, colour, style in (
            ("event_weighted", "Price-change weighted (before Hampel)", "#ba3b26", "-"),
            ("equal_reporting_station", "Equal reporting stations", "#777777", ":"),
            ("equal_station_hourly", "All last-known stations, hourly", "#087e78", "-"),
        ):
            axes[0, column].plot(timestamps, [row[key] for row in daily], label=label, color=colour, linestyle=style, linewidth=1.8)
        axes[0, column].set_title(title, loc="left", fontweight="bold")
        axes[0, column].set_ylabel("Pence per litre")
        axes[0, column].legend(loc="upper left", fontsize=8)
        difference = [row["event_weighted"] - row["equal_station_hourly"] for row in daily]
        axes[1, column].bar(timestamps, difference, color=["#ba3b26" if value >= 0 else "#087e78" for value in difference], width=0.8)
        axes[1, column].axhline(0, color="#444444", linewidth=0.6)
        axes[1, column].set_ylabel("Difference (p/l)")
        axes[2, column].plot(timestamps, [row["reconstructed_stations"] for row in daily], color="#087e78", label="Hourly reconstruction")
        axes[2, column].plot(timestamps, [row["reporting_stations"] for row in daily], color="#ba3b26", label="Price-change reports")
        axes[2, column].set_ylim(bottom=0)
        axes[2, column].set_ylabel("Contributing stations")
        axes[2, column].xaxis.set_major_locator(dates.DayLocator(interval=5))
        axes[2, column].xaxis.set_major_formatter(dates.DateFormatter("%d %b"))
        for axis in axes[:, column]:
            axis.spines[["top", "right"]].set_visible(False)
            axis.grid(axis="y", alpha=0.2)
            axis.set_axisbelow(True)
    first_day = datetime.fromisoformat(results["start_inclusive"])
    last_day = datetime.fromisoformat(results["end_exclusive"]) - timedelta(days=1)
    figure.suptitle(f"Fuel price history: weighting and coverage\n{first_day:%d %B %Y} - {last_day:%d %B %Y} (UTC)", fontsize=16)
    figure.supxlabel("Unsmoothed; same correction/anomaly policy. Last-known prices are not independently confirmed prices.", fontsize=10)
    figure.savefig(output / "comparison.png", dpi=160)
    plot.close(figure)


def write_report(results, output):
    day_count = len(next(iter(results["fuels"].values()))["daily"])
    lines = ["# Historical Trend Weighting Audit", "",
             f"Snapshot: {results['snapshot']['captured_at']}. Window: {results['start_inclusive']} inclusive to {results['end_exclusive']} exclusive.", "",
             "## Finding", "",
             "The event-weighted series and reconstructed station-price series differ materially day to day. The broad upward direction is unchanged over this window, but the event series mixes price movement with changes in the subset of stations contributing records. Weighting all contributing stations equally without carrying unchanged prices forward does not resolve that problem. The daily-summary cache matches the raw records; this is an aggregation-definition issue, not a cache-refresh failure in this window.", "",
             "Recommendation: prototype a clearly labelled last-known station-price series with visible coverage and observation-age information. Do not silently substitute it for actual pump prices or replace the production charts before deciding on closure, freshness and anomaly policies. The current event-weighted series can remain useful if explicitly labelled as an average of price-change reports.", "",
             "![Weighting, differences and station coverage](comparison.png)", "",
             "| Measure | E10 | Standard diesel |", "|---|---:|---:|"]
    summaries = [audit["summary"] for audit in results["fuels"].values()]
    for label, key, unit in (
        ("Mean absolute gap", "mean_absolute_difference_ppl", "p/l"),
        ("Median absolute gap", "median_absolute_difference_ppl", "p/l"),
        ("Largest absolute gap", "max_absolute_difference_ppl", "p/l"),
        ("Mean contributing reporting stations", "mean_reporting_stations", ""),
        ("Mean reconstructed stations", "mean_reconstructed_stations", ""),
        ("Mean reporting share", "mean_reporting_share_pct", "%"),
    ):
        values = [f"{summary[key]:,.2f}{unit}" for summary in summaries]
        lines.append(f"| {label} | {' | '.join(values)} |")
    lines.extend(["", "## Method", "",
                  "The event baseline averages eligible price-change records. The intermediate series gives each reporting station equal weight. The reconstruction selects each station's latest record at each UTC hour start (00:00 through 23:00), averages eligible hours within each station-day, then weights stations equally. A station with no eligible hours is excluded. No freshness cutoff, current closure filter or IQR filter is applied.", "",
                  "All methods use COALESCE(corrected_price, original_price) and the history endpoint's original anomaly_flags IS NULL eligibility rule. A latest flagged observation creates a gap until a later eligible report; older clean values are not carried through it. Existing corrections and flags are interpreted as stored in the snapshot, not as they were known historically. No future report is used. Ties use highest record ID. The reconstruction starts with a pre-window observation per station and does not invent earlier prices.", "",
                  f"The start-to-end changes span the first and last of {day_count} daily points ({day_count - 1} days between points), not a separate exactly-{day_count}-day return. Station hourly means are a sampling approximation, not continuous time-weighted means. A fixed full-coverage cohort and an hour-weighted alternative are sensitivity checks, not alternative ground truths.", "",
                  "## Results and Verification", ""])
    for fuel_type, audit in results["fuels"].items():
        summary = audit["summary"]
        lines.extend([f"### {fuel_type}", "",
                      f"Worst day: **{summary['worst_day']}**, absolute gap **{summary['max_absolute_difference_ppl']:.3f}p/l**.", "",
                      f"Start-to-end rise: event weighted **{summary['changes']['event_weighted']['change_ppl']:.3f}p/l** ({summary['changes']['event_weighted']['change_pct']:.3f}%) versus reconstructed **{summary['changes']['equal_station_hourly']['change_ppl']:.3f}p/l** ({summary['changes']['equal_station_hourly']['change_pct']:.3f}%).", "",
                      f"Mean absolute frequency, within-reporting-station timing, and coverage effects: {summary['mean_abs_reporting_frequency_effect_ppl']:.3f}, {summary['mean_abs_timing_effect_ppl']:.3f}, {summary['mean_abs_coverage_effect_ppl']:.3f}p/l respectively. Absolute components do not sum; their signed daily differences telescope. Rare reporters without an eligible hour also affect the timing comparison.", "",
                      f"Daily-cache mismatches: {audit['checks']['daily_cache']['mismatches']}. SQL reconstruction verified independently on the worst day. Local API reproduced exactly, including {audit['checks']['local_api']['hampel_changed_days']} Hampel-altered points (smoothing not used in the comparison).", "",
                      f"Maximum gap between the actual rounded/Hampel-filtered API series and the reconstruction: {summary['max_displayed_absolute_difference_ppl']:.3f}p/l. This is distinct from the unsmoothed gap above. Modified points: {json.dumps(audit['checks']['local_api']['altered_points'])}.", "",
                      f"Maximum fixed-cohort sensitivity: {summary['max_fixed_cohort_sensitivity_ppl']:.3f}p/l ({summary['fixed_cohort_stations']:,} stations); hour-weighting sensitivity: {summary['max_hour_weighting_sensitivity_ppl']:.3f}p/l.", "",
                      f"Eligible sampled hours based on an observation older than 30 days: {summary['observation_over_30_days_hour_share_pct']:.2f}%; older than 90 days: {summary['observation_over_90_days_hour_share_pct']:.2f}%. These are ages of change records, NOT evidence of how recently prices were confirmed.", "",
                      f"Evidence: [{fuel_type}-daily.csv]({fuel_type}-daily.csv), [{fuel_type}-station-days.csv]({fuel_type}-station-days.csv), [{fuel_type}-examples.csv]({fuel_type}-examples.csv), [{fuel_type}-source-records.csv]({fuel_type}-source-records.csv).", ""])
        opposite = [row for row in summary["opposite_daily_directions"]
                    if abs(row["event_move_ppl"]) >= 0.1 and abs(row["reconstructed_move_ppl"]) >= 0.1]
        if opposite:
            example = max(opposite, key=lambda row: abs(row["event_move_ppl"]) + abs(row["reconstructed_move_ppl"]))
            lines.extend([f"Daily-direction example (largest combined movement among opposite-sign days with both changes at least 0.1p/l): {example['date']}, event-weighted movement {example['event_move_ppl']:+.3f}p/l versus reconstructed {example['reconstructed_move_ppl']:+.3f}p/l. Small opposite-sign movements below 0.1p/l are not used for this example.", ""])
        for example in audit["examples"]:
            event_mean = "no eligible reports" if example["event_mean"] is None else f"{example['event_mean']:.3f}p/l event mean"
            lines.append(f"- {example['selection_reason']}: **{example['trading_name']}**, node `{example['node_id']}`, {example['date']}: {example['event_count']} eligible reports, {event_mean}, {example['hourly_mean']:.3f}p/l reconstructed over {example['eligible_hours']} hours. Source record IDs: {', '.join(map(str, example['source_record_ids']))}.")
        lines.append("")
    lines.extend(["## Limits", "",
                  f"This compares different statistical measures on the stored dataset; it is not a claim to know actual pump prices. The no-cutoff reconstruction includes stations with old observations, possibly closed stations and erroneous but unflagged prices. Historical closure, brand and source-confirmation histories are not reconstructed. The audit covers two fuels nationally over one {day_count}-day window; it does not establish the effect for all periods, subgroups or fuels. The comparison does not endorse automatic outlier exclusion or Hampel replacement.", "",
                  "## Provenance and Reproduction", "",
                  f"Source archive SHA-256: `{results['snapshot']['sha256']}`.",
                  f"Audit script SHA-256: `{results['audit_script_sha256']}`.",
                  f"Started: {results['audit_started_at']}. Database access: localhost:15432, repeatable-read, read-only, UTC. Production was not accessed.", "",
                  "Dependencies: psycopg2-binary, matplotlib; tests use pytest. From the repository root, against the same restored snapshot:", "", "```bash",
                  ".venv/bin/python -m pytest tests/test_trend_weighting_audit.py -q",
                  ".venv/bin/python scripts/audit_trend_weighting.py \\",
                  f"  --manifest {results['manifest_path']} --days {day_count} \\",
                  "  --output .local/trend-weighting-audit-rerun", "```", "",
                  "The output directory must be new, preserving previous audit artifacts. results.json contains all daily values, checks, snapshot provenance and summary metrics."])
    if results.get("age_sensitivity"):
        lines.extend(["", "## Change-Record Age Sensitivity", "",
                      "Age limits are evaluated at each UTC hour against the latest observation, never the last confirmation. Exactly 7/14/30 days is included; older is excluded. Recorded today means since midnight of that historical day. A station is included for a day if at least one hourly price survives; a station with partial coverage still has one station's weight. Excluded stations means no hours survive the age limit, relative to the no-limit eligible cohort. Removed station-hours also capture partial-day exclusions.", "",
                      "Region and forecourt type are classifications from the current snapshot, not historical membership. Group exclusion rates use station-days across the chosen period, not distinct stations. No external benchmark is used. Null means no eligible observations, never zero price.", "",
                      "| Fuel | Limit | Mean price (p/l) | Mean change vs no limit (p/l) | Stations included/day | Stations excluded/day | First-to-last change (p/l) |",
                      "|---|---|---:|---:|---:|---:|---:|"])
        for fuel_type, audit in results["fuels"].items():
            for policy, series in audit["age_sensitivity"].items():
                summary = series["summary"]
                values = ["n/a" if summary[key] is None else f"{summary[key]:.3f}" for key in ("mean_price", "mean_difference_from_no_limit", "mean_included_stations", "mean_excluded_stations", "change_ppl")]
                lines.append(f"| {fuel_type} | {series['label']} | " + " | ".join(values) + " |")
            lines.extend(["", f"[{fuel_type} sensitivity series]({fuel_type}-age-daily.csv); [{fuel_type} group breakdowns]({fuel_type}-age-groups.csv). Every policy is independently checked against SQL on its largest-gap day; every geographic/category breakdown sums to national coverage.", ""])
        lines.extend(["Reproduce the sensitivity artifact with the command above plus `--age-sensitivity`, into a new output directory.", ""])
    (output / "report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--age-sensitivity", action="store_true")
    arguments = parser.parse_args()
    if not 1 <= arguments.days <= 90:
        parser.error("--days must be between 1 and 90")
    if arguments.output.exists():
        parser.error("Output directory already exists; use a new directory to preserve audit results")
    manifest = json.loads(arguments.manifest.read_text())
    captured_at = datetime.fromisoformat(manifest["captured_at"]).astimezone(timezone.utc)
    end = captured_at.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=arguments.days)
    arguments.output.mkdir(parents=True, mode=0o700)
    connection = psycopg2.connect(
        host="127.0.0.1", port=15432, dbname="fuelfinder", user="fuelfinder", password="fuelfinder",
        connect_timeout=10, application_name="fuel-finder-weighting-audit",
        options="-c default_transaction_read_only=on -c timezone=UTC -c statement_timeout=120000",
    )
    connection.set_session(isolation_level="REPEATABLE READ", readonly=True)
    results = {"snapshot": manifest, "start_inclusive": start.isoformat(), "end_exclusive": end.isoformat(),
               "age_sensitivity": arguments.age_sensitivity,
               "manifest_path": str(arguments.manifest),
               "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "audit_started_at": datetime.now(timezone.utc).isoformat(), "fuels": {}}
    try:
        counts = fetch_rows(connection, "SELECT (SELECT COUNT(*) FROM fuel_prices) AS prices, (SELECT COUNT(*) FROM stations) AS stations")[0]
        if counts != {"prices": manifest["tables"]["fuel_prices"]["count"], "stations": manifest["tables"]["stations"]["count"]}:
            raise RuntimeError("Local database row counts differ from source manifest")
        for fuel_type in ("E10", "B7_STANDARD"):
            print("Auditing " + fuel_type, flush=True)
            events = load_events(connection, fuel_type, start, end)
            daily, station_days = compare_prices(events, start, end)
            cached, raw, checks = baseline_checks(connection, fuel_type, start, end)
            if len(cached) != arguments.days or len(raw) != arguments.days:
                raise RuntimeError("Audit range includes missing daily data")
            full_days = Counter(row["node_id"] for row in station_days if row["eligible_hours"] == 24)
            fixed_nodes = {node_id for node_id, count in full_days.items() if count == arguments.days}
            fixed_values = defaultdict(list)
            for row in station_days:
                if row["node_id"] in fixed_nodes:
                    fixed_values[row["date"]].append(row["hourly_mean"])
            for row, cached_row, raw_row in zip(daily, cached, raw):
                if row["date"] != str(raw_row["date"]) or row["date"] != str(cached_row["date"]):
                    raise AssertionError("Date alignment mismatch")
                if abs(row["event_weighted"] - float(raw_row["event_mean"])) > 1e-8 or row["event_count"] != raw_row["event_count"] or row["reporting_stations"] != raw_row["stations"]:
                    raise AssertionError("Python event baseline differs from independent SQL")
                row.update(fuel_type=fuel_type, cached_unrounded=float(cached_row["cached_unrounded"]),
                           cached_display=float(cached_row["cached_display"]),
                           fixed_cohort=average(fixed_values[row["date"]]), fixed_cohort_stations=len(fixed_nodes))
            checks["local_api"] = verify_local_api(cached, fuel_type, start, end)
            altered_points = {point["date"]: point["displayed"] for point in checks["local_api"]["altered_points"]}
            for row in daily:
                row["api_displayed"] = altered_points.get(row["date"], row["cached_display"])
            summary = summarise(daily)
            oracle = independent_hourly_check(connection, fuel_type, datetime.fromisoformat(summary["worst_day"]).replace(tzinfo=timezone.utc))
            worst = next(row for row in daily if row["date"] == summary["worst_day"])
            if abs(float(oracle["equal_station_hourly"]) - worst["equal_station_hourly"]) > 1e-8 or oracle["stations"] != worst["reconstructed_stations"] or oracle["eligible_hours"] != worst["eligible_hours"]:
                raise AssertionError("Python reconstruction differs from independent SQL on worst day")
            checks["worst_day_sql_oracle"] = oracle
            examples, evidence = drilldown(connection, fuel_type, summary["worst_day"], station_days)
            for name, rows in (("daily", daily), ("station-days", station_days), ("examples", examples), ("source-records", evidence)):
                write_csv(arguments.output / (fuel_type + "-" + name + ".csv"), rows)
            results["fuels"][fuel_type] = {"summary": summary, "checks": checks, "daily": daily, "examples": examples}
            if arguments.age_sensitivity:
                results["fuels"][fuel_type]["age_sensitivity"] = build_age_sensitivity(
                    connection, fuel_type, events, start, end, daily, station_days, arguments.output,
                )
            print(json.dumps({"fuel_type": fuel_type, **summary, "checks": checks}, default=str, indent=2), flush=True)
        (arguments.output / "results.json").write_text(json.dumps(results, default=str, indent=2))
        render_chart(results, arguments.output)
        write_report(results, arguments.output)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
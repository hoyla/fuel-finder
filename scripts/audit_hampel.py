"""Evaluate the existing Hampel policy without changing application filtering."""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import statistics
import urllib.parse
import urllib.request

import psycopg2

if __package__:
    from .audit_trend_weighting import average, compare_prices, fetch_rows, load_events, write_csv
else:
    from audit_trend_weighting import average, compare_prices, fetch_rows, load_events, write_csv


def hampel_review(values, granularity="daily"):
    if granularity not in ("daily", "hourly"):
        raise ValueError("Granularity must be daily or hourly")
    populated = [(index, float(value)) for index, value in enumerate(values) if value is not None]
    prices = [price for _, price in populated]
    half_window = 24 if granularity == "hourly" else 3
    results = [{"raw": value, "filtered": value, "median": None, "mad": None,
                "threshold": None, "flagged": False, "changed": False,
                "window_start_index": None, "window_end_index": None, "window_points": 0}
               for value in values]
    for position, (index, price) in enumerate(populated):
        lower = max(0, position - half_window)
        upper = min(len(prices), position + half_window + 1)
        window = prices[lower:upper]
        median = statistics.median(window)
        mad = statistics.median([abs(value - median) for value in window])
        threshold = 3.0 * 1.4826 * mad
        flagged = len(prices) >= 3 and mad * 1.4826 > 0 and abs(price - median) > threshold
        filtered = round(median, 1) if flagged else price
        results[index] = {
            "raw": price, "filtered": filtered, "median": median, "mad": mad,
            "threshold": threshold, "flagged": flagged, "changed": filtered != price,
            "window_start_index": populated[lower][0], "window_end_index": populated[upper - 1][0],
            "window_points": len(window),
        }
    return results


def display_price(value):
    if value is None:
        return None
    return float(Decimal(str(round(float(value), 10))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def reconstruct_archive(connection, fuel_type, start, end):
    daily = []
    hourly = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=21), end)
        events = load_events(connection, fuel_type, chunk_start, chunk_end)
        chunk_daily, station_days = compare_prices(events, chunk_start, chunk_end, hourly_output=hourly)
        daily.extend({"bucket": datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc),
                      "avg_price": row["equal_station_hourly"], "stations": row["reconstructed_stations"]}
                     for row in chunk_daily)
        del station_days
        chunk_start = chunk_end
        print(f"{fuel_type}: reconstructed through {(chunk_end - timedelta(days=1)).date()}", flush=True)
    return {"daily": daily, "hourly": hourly}


def existing_archive(connection, fuel_type, start, end):
    daily = fetch_rows(connection, """
        SELECT price_date AS bucket,
               ROUND(SUM(avg_price * sample_count) / SUM(sample_count), 1) AS avg_price,
               COUNT(*) AS stations
        FROM daily_prices
        WHERE fuel_type = %s AND price_date >= %s AND price_date < %s
        GROUP BY price_date ORDER BY price_date
    """, (fuel_type, start.date(), end.date()))
    for row in daily:
        row["bucket"] = datetime.combine(row["bucket"], datetime.min.time(), timezone.utc)
    hourly = fetch_rows(connection, """
        SELECT date_trunc('hour', fp.observed_at) AS bucket,
               ROUND(AVG(COALESCE(pc.corrected_price, fp.price)), 1) AS avg_price,
               COUNT(DISTINCT fp.node_id) AS stations
        FROM fuel_prices fp LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
        WHERE fp.fuel_type = %s AND fp.observed_at >= %s AND fp.observed_at < %s
          AND fp.anomaly_flags IS NULL
        GROUP BY date_trunc('hour', fp.observed_at) ORDER BY bucket
    """, (fuel_type, start, end))
    return {"daily": daily, "hourly": hourly}


def review_series(fuel_type, method, granularity, rows):
    reviewed = hampel_review([display_price(row["avg_price"]) for row in rows], granularity)
    output = []
    for row, review in zip(rows, reviewed):
        lower = review["window_start_index"]
        upper = review["window_end_index"]
        output.append({
            "fuel_type": fuel_type, "method": method, "granularity": granularity,
            "bucket": row["bucket"].isoformat(), "stations": row["stations"],
            "unrounded_price": float(row["avg_price"]) if row["avg_price"] is not None else None,
            **review,
            "adjustment_ppl": review["filtered"] - review["raw"] if review["raw"] is not None else None,
            "window_start": rows[lower]["bucket"].isoformat() if lower is not None else None,
            "window_end": rows[upper]["bucket"].isoformat() if upper is not None else None,
            "window_clock_hours": (rows[upper]["bucket"] - rows[lower]["bucket"]).total_seconds() / 3600 if lower is not None else None,
        })
    populated = [row for row in output if row["raw"] is not None]
    changed = [row for row in populated if row["changed"]]
    movements = []
    step = timedelta(days=1) if granularity == "daily" else timedelta(hours=1)
    for before, after in zip(output, output[1:]):
        if before["raw"] is None or after["raw"] is None or datetime.fromisoformat(after["bucket"]) - datetime.fromisoformat(before["bucket"]) != step:
            continue
        movements.append({"fuel_type": fuel_type, "method": method, "granularity": granularity,
                          "previous_bucket": before["bucket"], "bucket": after["bucket"],
                          "raw_move_ppl": after["raw"] - before["raw"],
                          "filtered_move_ppl": after["filtered"] - before["filtered"],
                          "previous_stations": before["stations"], "stations": after["stations"],
                          "previous_changed": before["changed"], "changed": after["changed"]})
    summary = {"fuel_type": fuel_type, "method": method, "granularity": granularity,
               "populated_buckets": len(populated), "missing_buckets": len(output) - len(populated),
               "changed_buckets": len(changed), "changed_pct": 100 * len(changed) / len(populated) if populated else 0,
               "max_adjustment_ppl": max((abs(row["adjustment_ppl"]) for row in changed), default=0),
               "mean_abs_adjustment_ppl": average([abs(row["adjustment_ppl"]) for row in populated]),
               "zero_mad_buckets": sum(row["mad"] == 0 for row in populated),
               "zero_mad_off_median_at_least_1p": sum(row["mad"] == 0 and abs(row["raw"] - row["median"]) >= 1 for row in populated),
               "stretched_windows": sum(row["window_clock_hours"] > (48 if granularity == "hourly" else 144) for row in populated),
               "raw_first_to_last_ppl": populated[-1]["raw"] - populated[0]["raw"] if populated else None,
               "filtered_first_to_last_ppl": populated[-1]["filtered"] - populated[0]["filtered"] if populated else None}
    largest_rises = sorted((row for row in movements if row["raw_move_ppl"] > 0), key=lambda row: row["raw_move_ppl"], reverse=True)[:3]
    largest_falls = sorted((row for row in movements if row["raw_move_ppl"] < 0), key=lambda row: row["raw_move_ppl"])[:3]
    return {"summary": summary, "rows": output, "changed": changed,
            "largest_rises": largest_rises, "largest_falls": largest_falls}


def check_api(review, start, end):
    summary = review["summary"]
    parameters = urllib.parse.urlencode({"fuel_type": summary["fuel_type"], "granularity": summary["granularity"],
                                        "start_date": start.date().isoformat(), "end_date": (end - timedelta(days=1)).date().isoformat()})
    with urllib.request.urlopen("http://127.0.0.1:18080/api/prices/history?" + parameters, timeout=120) as response:
        actual = json.load(response)["data"]
    expected = [row for row in review["rows"] if row["raw"] is not None]
    if len(actual) != len(expected):
        raise AssertionError("Existing series length differs from API")
    for actual_row, expected_row in zip(actual, expected):
        if (datetime.fromisoformat(actual_row["bucket"]).replace(tzinfo=timezone.utc) != datetime.fromisoformat(expected_row["bucket"])
                or actual_row["avg_price"] != expected_row["filtered"]
                or actual_row["stations"] != expected_row["stations"]):
            raise AssertionError("Existing filtered series differs from API")
    return True


def paired_age_review(path):
    artifact = json.loads(path.read_text())
    results = []
    for fuel_type, audit in artifact["fuels"].items():
        reference = audit["age_sensitivity"]["none"]["daily"]
        reference_hampel = hampel_review([display_price(row["price"]) for row in reference])
        for policy, series in audit["age_sensitivity"].items():
            selected_hampel = hampel_review([display_price(row["price"]) for row in series["daily"]])
            for base, selected, base_filter, selected_filter in zip(reference, series["daily"], reference_hampel, selected_hampel):
                results.append({"fuel_type": fuel_type, "age_limit": policy, "date": base["date"],
                                "reference_raw": base_filter["raw"], "reference_filtered": base_filter["filtered"],
                                "selected_raw": selected_filter["raw"], "selected_filtered": selected_filter["filtered"],
                                "reference_changed": base_filter["changed"], "selected_changed": selected_filter["changed"],
                                "raw_difference": selected_filter["raw"] - base_filter["raw"] if selected_filter["raw"] is not None else None,
                                "filtered_difference": selected_filter["filtered"] - base_filter["filtered"] if selected_filter["filtered"] is not None else None,
                                "reference_stations": base["included_stations"], "selected_stations": selected["included_stations"]})
    return results


def station_values(connection, fuel_type, method, granularity, buckets):
    step = timedelta(days=1) if granularity == "daily" else timedelta(hours=1)
    events = load_events(connection, fuel_type, min(buckets), max(buckets) + step)
    values = {bucket: {} for bucket in buckets}
    record_ids = defaultdict(set)
    if method == "reconstructed" and granularity == "daily":
        for bucket in buckets:
            _, stations = compare_prices(events, bucket, bucket + step)
            for station in stations:
                record_ids[station["node_id"]].update(station["source_record_ids"])
                if station["eligible_hours"]:
                    values[bucket][station["node_id"]] = {"price": station["hourly_mean"], "weight": 1,
                                                        "hours": station["eligible_hours"]}
    elif method == "reconstructed":
        for bucket in buckets:
            latest = {}
            for event in events:
                if event.observed_at <= bucket:
                    previous = latest.get(event.node_id)
                    if previous is None or (event.observed_at, event.record_id) > (previous.observed_at, previous.record_id):
                        latest[event.node_id] = event
            for node_id, event in latest.items():
                record_ids[node_id].add(event.record_id)
                if event.eligible:
                    values[bucket][node_id] = {"price": event.price, "weight": 1, "hours": 1}
    else:
        for bucket in buckets:
            grouped = defaultdict(list)
            for event in events:
                if bucket <= event.observed_at < bucket + step:
                    record_ids[event.node_id].add(event.record_id)
                    if event.eligible:
                        grouped[event.node_id].append(event.price)
            for node_id, prices in grouped.items():
                values[bucket][node_id] = {"price": average(prices), "weight": len(prices), "hours": None}
    return values, record_ids


def trace_case(connection, review, point, reason, metadata):
    summary = review["summary"]
    current = datetime.fromisoformat(point["bucket"])
    if reason == "largest_rise":
        reference = datetime.fromisoformat(point["previous_bucket"])
    else:
        reference = datetime.fromisoformat(point["window_start"])
        if reference == current:
            reference = datetime.fromisoformat(point["window_end"])
    values, record_ids = station_values(connection, summary["fuel_type"], summary["method"], summary["granularity"], [reference, current])
    current_values = values[current]
    reference_values = values[reference]
    current_weight = sum(row["weight"] for row in current_values.values())
    reference_weight = sum(row["weight"] for row in reference_values.values())
    current_mean = sum(row["price"] * row["weight"] for row in current_values.values()) / current_weight if current_weight else None
    reference_mean = sum(row["price"] * row["weight"] for row in reference_values.values()) / reference_weight if reference_weight else None
    current_row = next(row for row in review["rows"] if row["bucket"] == point["bucket"])
    if summary["method"] == "reconstructed":
        if current_mean is None or abs(current_mean - current_row["unrounded_price"]) > 1e-8 or len(current_values) != current_row["stations"]:
            raise AssertionError("Station-level reconstruction does not match archive series")
    sources = []
    case_id = "-".join((summary["fuel_type"], summary["method"], summary["granularity"], current.strftime("%Y%m%dT%H%M"), reason))
    all_nodes = set(current_values) | set(reference_values)
    for node_id in sorted(all_nodes):
        before = reference_values.get(node_id)
        after = current_values.get(node_id)
        contribution = (after["price"] * after["weight"] / current_weight if after else 0) - (before["price"] * before["weight"] / reference_weight if before else 0)
        station = metadata.get(node_id, {})
        sources.append({"case_id": case_id, "fuel_type": summary["fuel_type"], "node_id": node_id,
                        "station": station.get("trading_name"), "current_brand": station.get("brand_name"),
                        "current_region": station.get("region"), "current_category": station.get("forecourt_type"),
                        "reference_bucket": reference.isoformat(), "bucket": current.isoformat(),
                        "reference_price": before["price"] if before else None,
                        "current_price": after["price"] if after else None,
                        "reference_weight": before["weight"] if before else 0, "current_weight": after["weight"] if after else 0,
                        "reference_hours": before["hours"] if before else 0, "current_hours": after["hours"] if after else 0,
                        "contribution_to_mean_change_ppl": contribution,
                        "source_record_ids": sorted(record_ids[node_id])})
    if current_mean is not None and reference_mean is not None:
        if abs(sum(row["contribution_to_mean_change_ppl"] for row in sources) - (current_mean - reference_mean)) > 1e-8:
            raise AssertionError("Station contributions do not reconcile")
    largest_sources = sorted(sources, key=lambda row: abs(row["contribution_to_mean_change_ppl"]), reverse=True)[:8]
    case = {"case_id": case_id, "reason": reason, "fuel_type": summary["fuel_type"],
            "method": summary["method"], "granularity": summary["granularity"],
            "reference_bucket": reference.isoformat(), **current_row,
            "reference_record_mean": reference_mean, "current_record_mean": current_mean,
            "reference_contributors": len(reference_values), "current_contributors": len(current_values),
            "entering_stations": len(set(current_values) - set(reference_values)),
            "leaving_stations": len(set(reference_values) - set(current_values)),
            "matched_stations_price_up": sum(current_values[node]["price"] > reference_values[node]["price"] for node in set(current_values) & set(reference_values)),
            "matched_stations_price_down": sum(current_values[node]["price"] < reference_values[node]["price"] for node in set(current_values) & set(reference_values)),
            "top_source_record_ids": sorted({identifier for row in largest_sources for identifier in row["source_record_ids"]})}
    return case, largest_sources


def build_evidence(connection, results, output):
    metadata_rows = fetch_rows(connection, "SELECT DISTINCT ON (node_id) node_id, trading_name, brand_name, region, forecourt_type FROM current_prices ORDER BY node_id, fuel_type")
    metadata = {row["node_id"]: row for row in metadata_rows}
    cases = []
    sources = []
    for review in results["series"]:
        summary = review["summary"]
        if summary["method"] == "reconstructed":
            selected = [(point, "hampel_intervention") for point in review["changed"]]
            if review["largest_rises"]:
                selected.append((review["largest_rises"][0], "largest_rise"))
        else:
            selected = [(point, "hampel_intervention") for point in sorted(review["changed"], key=lambda row: abs(row["adjustment_ppl"]), reverse=True)[:2]]
        for point, reason in selected:
            case, station_sources = trace_case(connection, review, point, reason, metadata)
            cases.append(case)
            sources.extend(station_sources)
        print(f"Traced {len(selected)} {summary['fuel_type']} {summary['method']} {summary['granularity']} cases", flush=True)
    identifiers = sorted({identifier for row in sources for identifier in row["source_record_ids"]})
    records = fetch_rows(connection, """
        WITH selected AS (SELECT * FROM fuel_prices WHERE id = ANY(%s)), evidence_ids AS (
            SELECT id FROM selected
            UNION
            SELECT previous.id FROM selected
            CROSS JOIN LATERAL (
                SELECT fp.id FROM fuel_prices fp
                WHERE fp.node_id = selected.node_id AND fp.fuel_type = selected.fuel_type
                  AND (fp.observed_at, fp.id) < (selected.observed_at, selected.id)
                ORDER BY fp.observed_at DESC, fp.id DESC LIMIT 1
            ) previous
            UNION
            SELECT following.id FROM selected
            CROSS JOIN LATERAL (
                SELECT fp.id FROM fuel_prices fp
                WHERE fp.node_id = selected.node_id AND fp.fuel_type = selected.fuel_type
                  AND (fp.observed_at, fp.id) > (selected.observed_at, selected.id)
                ORDER BY fp.observed_at, fp.id LIMIT 1
            ) following
        )
        SELECT fp.id AS fuel_price_id, fp.node_id, fp.fuel_type, fp.price AS original_price,
               pc.corrected_price, COALESCE(pc.corrected_price, fp.price) AS effective_price,
               fp.anomaly_flags, fp.observed_at, fp.price_last_updated, fp.price_change_effective_timestamp,
               fp.scrape_run_id, sr.run_type, sr.status AS scrape_status, sr.s3_key
        FROM fuel_prices fp JOIN evidence_ids ON evidence_ids.id = fp.id
        LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
        LEFT JOIN scrape_runs sr ON sr.id = fp.scrape_run_id
        ORDER BY fp.fuel_type, fp.node_id, fp.observed_at, fp.id
    """, (identifiers,))
    if not set(identifiers).issubset({row["fuel_price_id"] for row in records}):
        raise AssertionError("Not all evidence record IDs were resolved")
    write_csv(output / "case-review.csv", cases)
    write_csv(output / "station-contributions.csv", sources)
    write_csv(output / "source-records.csv", records)
    return {"cases": cases, "source_records": len(records), "station_contributions": len(sources)}


def endpoint_context_check(connection, results):
    checks = []
    captured = datetime.fromisoformat(results["snapshot"]["captured_at"])
    end = datetime.fromisoformat(results["end_exclusive"])
    for series in results["series"]:
        if series["summary"]["method"] != "reconstructed" or series["summary"]["granularity"] != "hourly":
            continue
        candidates = [row for row in series["changed"] if datetime.fromisoformat(row["bucket"]) >= end - timedelta(hours=24)]
        if not candidates:
            continue
        start = end - timedelta(days=3)
        events = load_events(connection, series["summary"]["fuel_type"], start, captured)
        hourly = []
        compare_prices(events, start, captured.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1), hourly_output=hourly)
        hourly = [row for row in hourly if row["bucket"] < captured]
        reviewed = review_series(series["summary"]["fuel_type"], "reconstructed", "hourly", hourly)
        by_bucket = {row["bucket"]: row for row in reviewed["rows"]}
        for candidate in candidates:
            with_context = by_bucket[candidate["bucket"]]
            checks.append({"fuel_type": candidate["fuel_type"], "bucket": candidate["bucket"],
                           "raw": candidate["raw"], "filtered_at_end_of_complete_days": candidate["filtered"],
                           "filtered_with_next_available_hours": with_context["filtered"],
                           "changed_with_next_available_hours": with_context["changed"],
                           "context_through": hourly[-1]["bucket"].isoformat()})
    return checks


def write_review_report(results, evidence, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as dates
    import matplotlib.pyplot as plot

    series = {(row["summary"]["fuel_type"], row["summary"]["method"], row["summary"]["granularity"]): row for row in results["series"]}
    figure, axes = plot.subplots(2, 2, figsize=(13, 8), layout="constrained")
    panels = [(("B7_STANDARD", "reconstructed", "daily"), "2026-03-12", "2026-04-05", "Standard diesel: largest daily rise retained"),
              (("HVO", "existing", "hourly"), "2026-08-15", "2026-08-21", "HVO: existing change-report averages"),
              (("B10", "reconstructed", "hourly"), "2026-04-21", "2026-04-25", "B10: reconstructed coverage change"),
              (("E5", "reconstructed", "hourly"), "2026-09-07", "2026-09-10", "E5: reconstructed right-edge adjustment")]
    for axis, (key, start, end, title) in zip(axes.flat, panels):
        rows = [row for row in series[key]["rows"] if start <= row["bucket"][:10] < end and row["raw"] is not None]
        timestamps = [datetime.fromisoformat(row["bucket"]) for row in rows]
        axis.plot(timestamps, [row["raw"] for row in rows], color="#087e78", label="Before Hampel", linewidth=2)
        axis.plot(timestamps, [row["filtered"] for row in rows], color="#ba3b26", label="Existing Hampel policy", linewidth=1.6, linestyle="--")
        changed = [row for row in rows if row["changed"]]
        axis.scatter([datetime.fromisoformat(row["bucket"]) for row in changed], [row["filtered"] for row in changed], color="#ba3b26", s=20, zorder=3)
        axis.set_title(title, fontsize=11, loc="left")
        axis.set_ylabel("Pence per litre")
        axis.xaxis.set_major_locator(dates.AutoDateLocator(minticks=3, maxticks=5))
        axis.xaxis.set_major_formatter(dates.DateFormatter("%d %b", tz=timezone.utc))
        axis.grid(axis="y", alpha=0.2)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(fontsize=8)
    figure.suptitle("Hampel review: existing settings, different price series", fontsize=15)
    figure.supxlabel("Local snapshot captured 10 September 2026. Source records and case selection in report.md. Times are UTC.", fontsize=9)
    figure.savefig(output / "hampel-cases.png", dpi=160)
    plot.close(figure)
    parent = Path(evidence["base_results"]).parent
    from os.path import relpath
    base_link = Path(relpath(parent, output)).as_posix()
    lines = ["# Hampel Filter Review", "",
             "## Recommendation", "",
             "Retain the production Hampel filter and the separate current-snapshot Tukey IQR exclusions. This review does not justify removing either safeguard. Reconstruction changes the statistical behaviour of the time series substantially; that is a reason to review Hampel's interventions on the new series, not to remove it as part of an aggregation change.", "",
             "For further local evaluation, keep before/after values, identify altered points, and use the same Hampel policy on both no-age-limit and age-limited series. The right edge and sparse-series window duration need explicit review before any production change. No production code, thresholds, corrections, raw records or materialised views were changed for this audit.", "",
             f"Window: {results['start']} inclusive to {results['end_exclusive']} exclusive, all six stored fuel types. The first day is only partially observed (first record {results['first_observation']}); the capture day's partial data is excluded from the main comparison. One endpoint check below adds only the next hours already present in the same snapshot.", "",
             "![Representative Hampel cases](hampel-cases.png)", "",
             "## How Often It Intervenes", "",
             "Counts are altered aggregate buckets, not individual erroneous station reports. Full-window query, national aggregates; smaller filtered cohorts and other date ranges can behave differently.", "",
             "| Fuel | Existing daily | Reconstructed daily | Existing hourly | Reconstructed hourly | Largest reconstructed hourly adjustment |",
             "|---|---:|---:|---:|---:|---:|"]
    for fuel in sorted({key[0] for key in series}):
        counts = [series[(fuel, method, granularity)]["summary"]["changed_buckets"] for granularity in ("daily", "hourly") for method in ("existing", "reconstructed")]
        maximum = series[(fuel, "reconstructed", "hourly")]["summary"]["max_adjustment_ppl"]
        lines.append(f"| {fuel} | " + " | ".join(str(count) for count in counts) + f" | {maximum:.1f}p/l |")
    lines.extend(["", f"Full denominators and diagnostics: [{base_link}/summary.csv]({base_link}/summary.csv). All 12 existing daily/hourly series matched the unchanged local API, bucket for bucket, after Hampel filtering. All 31 reconstructed interventions were traced; evidence also covers the two largest existing interventions per fuel/granularity and the largest reconstructed rise per fuel/granularity.", "",
                  "## What the Records Show", "",
                  "- Existing series: some large interventions are on buckets with only one reporting station. For example, B10 on 29 March changes from 123.7p to 184.3p (one station); HVO at 03:00 UTC on 18 August changes from 114.9p to 217.7p (one station). This demonstrates substantial stabilisation of sparse aggregates. It does not, by itself, prove either the individual report or its replacement is the true pump-price average.", "",
                  "- Reconstructed B10: 17 altered hours on 22-23 April. At a largest adjustment (22 April 22:00 UTC), 182.3p becomes 181.7p. Coverage moves from 61 to 62 stations compared with the preceding 24 hours, with no price movement among the matched stations at that anchor. BP WILSONS re-enters at 215.9p (record 409511) after flagged 1.8p reports (including 405401). This is an eligibility/composition effect; the evidence does not establish that the 215.9p report is incorrect.", "",
                  "- Reconstructed HVO: 13 altered hours on 18 August. A largest adjustment changes 192.7p to 193.4p; coverage changes from 88 to 89 stations against the preceding 24-hour anchor, with one matched station increasing and none decreasing. The newly contributing Corner filling station reports 114.9p (record 910028). Source context is retained, but pump accuracy is not independently verified.", "",
                  "- Reconstructed E5: the final complete-day hourly point (9 September 23:00 UTC) changes from 185.2p to 184.7p. Against the preceding 24-hour anchor, 1,325 matched stations have higher reported prices, 49 lower, and two enter. This is a candidate for endpoint review because the centred window is truncated, not evidence to discard Hampel globally.", "",
                  "- All six largest reconstructed daily rises are left unchanged by Hampel. Examples include standard diesel on 26 March (173.54p to 177.12p unrounded) and E10 on that day (148.15p to 149.93p), with thousands of matched stations increasing. Several largest rises coincide with the historical-archive/live-collection transition documented in the repository; these are movements in the stored observations, not an attribution to a particular real-world event or proof of its exact timing.", "",
                  "Case selection is deterministic and includes unaltered large rises as counterexamples. Station contribution checks reconcile with the reconstructed means. For intervention cases, contributions explain the difference from the filter window's first available bucket (last if the intervention itself is first), not the median-replacement amount. The top eight absolute station contributions are exported; the reconciliation used all stations. Source metadata labels are from the current snapshot.", "",
                  "## Endpoint Check", ""])
    for check in evidence["endpoint_checks"]:
        lines.append(f"- {check['fuel_type']} at {check['bucket']}: raw {check['raw']:.1f}p; filtered with complete-day boundary {check['filtered_at_end_of_complete_days']:.1f}p; filtered with the snapshot's next available hours {check['filtered_with_next_available_hours']:.1f}p (context through {check['context_through']}). No data after snapshot capture is used.")
    lines.extend(["", "## Identical Policy on Age Comparisons", "",
                  "The five age policies for E10 and standard diesel use the same daily Hampel rule on both reference and selected series over the existing 11 August-9 September audit window. Inputs are rounded to one decimal before filtering, matching the existing API's stage of rounding. Comparisons below are therefore of rounded series; the earlier unrounded sensitivity results remain unchanged.", "",
                  "| Fuel | Age limit | Reference points altered | Selected points altered | Largest effect on age difference |",
                  "|---|---|---:|---:|---:|"])
    for fuel in ("E10", "B7_STANDARD"):
        for policy in ("none", "30", "14", "7", "today"):
            rows = [row for row in results["paired_age"] if row["fuel_type"] == fuel and row["age_limit"] == policy]
            delta = max((abs(row["filtered_difference"] - row["raw_difference"]) for row in rows if row["raw_difference"] is not None), default=0)
            lines.append(f"| {fuel} | {policy} | {sum(row['reference_changed'] for row in rows)} | {sum(row['selected_changed'] for row in rows)} | {delta:.1f}p/l |")
    lines.extend(["", f"Every value before and after: [{base_link}/paired-age-daily.csv]({base_link}/paired-age-daily.csv).", "",
                  "## Boundaries of the Evidence", "",
                  "- Hampel acts on aggregate time-series buckets. Rule-based anomaly exclusions act on individual reports; Tukey IQR excludes spectacularly unusual reports in the current snapshot. Neither of those safeguards was altered. This evaluation does not apply today's IQR fence retrospectively to historical prices.",
                  "- The exact current Hampel implementation uses 7/49 returned data points, not necessarily 7 days/49 hours. Missing buckets can stretch the window: the HVO intervention above spans 843 clock hours. This matters for sparse fuels. We reproduced it rather than silently changing it.",
                  "- MAD=0 deliberately leaves the bucket unchanged in the existing implementation. Many reconstructed hourly windows have zero MAD after rounding to 0.1p. Tests retain that behaviour. Few interventions therefore do not establish that the underlying data has no errors; persistent or small errors may evade an aggregate temporal filter.",
                  "- Centred windows use later observations and are truncated at the selected range's edges. Changing the date range can change filtered values; the full-archive results are not a guarantee for every shorter query.",
                  "- Reconstructions use observed_at, stored corrections and original anomaly-flag eligibility, matching the previous audit. They are not historically versioned interpretations or independently confirmed pump prices. Old observations and possibly closed stations remain under no age limit.",
                  "- National reconstructed daily means average hourly observations within station-days and then stations equally. Reconstructed hourly means average eligible stations at the hour start. The first partial day, sparse coverage, archive import boundaries and different query cohorts limit generalisation. No independent benchmark or truth-labelled error set was used.", "",
                  "## Evidence Files", "",
                  f"- [case-review.csv](case-review.csv): {len(evidence['cases'])} traced cases, aggregate windows, station counts and movement counts.",
                  f"- [station-contributions.csv](station-contributions.csv): {evidence['station_contributions']} leading source contributions with record IDs.",
                  f"- [source-records.csv](source-records.csv): {evidence['source_records']} source records including immediate preceding/following context, original/corrected prices, flags, source timestamps, scrape IDs and S3 keys.",
                  f"- [{base_link}/interventions.csv]({base_link}/interventions.csv): every altered bucket, including cases not individually traced in the existing series.",
                  f"- [{base_link}/largest-movements.csv]({base_link}/largest-movements.csv): largest rises/falls including unaltered movements.", "",
                  "## Reproduction", "",
                  "Requires the same local PostgreSQL snapshot at 127.0.0.1:15432, local API at 18080, psycopg2-binary and matplotlib. All database access is repeatable-read/read-only. Output directories must be new.", "", "```bash",
                  ".venv/bin/python -m pytest tests/test_trend_weighting_audit.py -q",
                  ".venv/bin/python scripts/audit_hampel.py \\",
                  "  --manifest .local/production-20260910T100344Z.json \\",
                  "  --age-artifact .local/trend-age-sensitivity-20260910/results.json \\",
                  "  --output .local/hampel-review-rerun",
                  ".venv/bin/python scripts/audit_hampel.py \\",
                  "  --manifest .local/production-20260910T100344Z.json \\",
                  "  --age-artifact .local/trend-age-sensitivity-20260910/results.json \\",
                  "  --review-results .local/hampel-review-rerun/results.json \\",
                  "  --output .local/hampel-evidence-rerun", "```", "",
                  f"Snapshot SHA-256: `{results['snapshot']['sha256']}`.",
                  f"Base results SHA-256: `{evidence['base_results_sha256']}`.",
                  f"Review script SHA-256: `{evidence['review_script_sha256']}`.",
                  f"Reviewed at: {evidence['reviewed_at']}. Algorithm/API/reconstruction source hashes and complete numerical output are preserved in the JSON artifacts."])
    (output / "report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--age-artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-results", type=Path)
    arguments = parser.parse_args()
    if arguments.output.exists():
        parser.error("Use a new output directory to preserve previous results")
    arguments.output.mkdir(parents=True, mode=0o700)
    manifest = json.loads(arguments.manifest.read_text())
    captured = datetime.fromisoformat(manifest["captured_at"]).astimezone(timezone.utc)
    end = captured.replace(hour=0, minute=0, second=0, microsecond=0)
    connection = psycopg2.connect(host="127.0.0.1", port=15432, user="fuelfinder", password="fuelfinder", dbname="fuelfinder",
                                  connect_timeout=10, application_name="fuel-finder-hampel-audit",
                                  options="-c default_transaction_read_only=on -c timezone=UTC -c statement_timeout=120000")
    connection.set_session(isolation_level="REPEATABLE READ", readonly=True)
    results = {"snapshot": manifest, "audit_started_at": datetime.now(timezone.utc).isoformat(),
               "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "reconstruction_sha256": hashlib.sha256(Path(compare_prices.__code__.co_filename).read_bytes()).hexdigest(),
               "api_source_sha256": hashlib.sha256((Path(__file__).resolve().parents[1] / "web/api.py").read_bytes()).hexdigest(),
               "age_artifact_sha256": hashlib.sha256(arguments.age_artifact.read_bytes()).hexdigest(), "series": []}
    try:
        coverage = fetch_rows(connection, "SELECT COUNT(*) AS records, MIN(observed_at) AS earliest, MAX(observed_at) AS latest FROM fuel_prices")[0]
        if coverage["records"] != manifest["tables"]["fuel_prices"]["count"]:
            raise RuntimeError("Local record count does not match snapshot")
        start = coverage["earliest"].replace(hour=0, minute=0, second=0, microsecond=0)
        if arguments.review_results:
            base = json.loads(arguments.review_results.read_text())
            if base["snapshot"]["sha256"] != manifest["sha256"]:
                raise RuntimeError("Review and database snapshot manifests differ")
            evidence = build_evidence(connection, base, arguments.output)
            evidence["endpoint_checks"] = endpoint_context_check(connection, base)
            evidence.update(base_results=str(arguments.review_results),
                            base_results_sha256=hashlib.sha256(arguments.review_results.read_bytes()).hexdigest(),
                            review_script_sha256=results["script_sha256"], reviewed_at=results["audit_started_at"])
            (arguments.output / "evidence.json").write_text(json.dumps(evidence, default=str, indent=2))
            write_review_report(base, evidence, arguments.output)
            return
        results.update(start=start.isoformat(), end_exclusive=end.isoformat(), first_observation=coverage["earliest"].isoformat())
        fuels = fetch_rows(connection, "SELECT DISTINCT fuel_type FROM fuel_prices ORDER BY fuel_type")
        for fuel in fuels:
            fuel_type = fuel["fuel_type"]
            existing = existing_archive(connection, fuel_type, start, end)
            reconstructed = reconstruct_archive(connection, fuel_type, start, end)
            for granularity in ("daily", "hourly"):
                for method, source in (("existing", existing), ("reconstructed", reconstructed)):
                    review = review_series(fuel_type, method, granularity, source[granularity])
                    if method == "existing":
                        review["summary"]["api_matched"] = check_api(review, start, end)
                    results["series"].append(review)
                    write_csv(arguments.output / f"{fuel_type}-{method}-{granularity}.csv", review["rows"])
                    print(json.dumps(review["summary"], default=str), flush=True)
        results["paired_age"] = paired_age_review(arguments.age_artifact)
        write_csv(arguments.output / "paired-age-daily.csv", results["paired_age"])
        write_csv(arguments.output / "summary.csv", [review["summary"] for review in results["series"]])
        write_csv(arguments.output / "interventions.csv", [row for review in results["series"] for row in review["changed"]])
        write_csv(arguments.output / "largest-movements.csv", [row for review in results["series"] for row in review["largest_rises"] + review["largest_falls"]])
        (arguments.output / "results.json").write_text(json.dumps(results, default=str, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
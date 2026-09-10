"""Station-weighted history and age sensitivity without changing source records."""

from datetime import date, datetime, timedelta, timezone
import statistics

from psycopg2 import sql


AGE_LIMITS = {"none": None, "30": 30, "14": 14, "7": 7, "today": 0}


def resolve_window(start_date=None, end_date=None, days=None, role="readonly", now=None):
    now = now or datetime.now(timezone.utc)
    today = now.date()
    maximum = 90 if role == "readonly" else 365
    last = date.fromisoformat(end_date) if end_date else today
    first = date.fromisoformat(start_date) if start_date else last - timedelta(days=(days or (maximum if end_date else 30)) - 1)
    if first > last or last > today:
        raise ValueError("Dates must be ordered and no later than today (UTC)")
    effective_first = max(first, last - timedelta(days=maximum - 1))
    start = datetime.combine(effective_first, datetime.min.time(), timezone.utc)
    end = min(datetime.combine(last + timedelta(days=1), datetime.min.time(), timezone.utc), now)
    return start, end, effective_first != first


def select_stations(fuel_type, filters):
    conditions = [sql.SQL("fuel_type = ANY(%s)")]
    parameters = [[value.strip() for value in fuel_type.split(',') if value.strip()]]
    for parameter, column in (("region", "region"), ("country", "country"), ("rural_urban", "rural_urban"),
                              ("category", "forecourt_type"), ("node_ids", "node_id")):
        if filters.get(parameter):
            values = [value.strip() for value in filters[parameter].split(",") if value.strip()]
            if values:
                conditions.append(sql.SQL("{} = ANY(%s)").format(sql.Identifier(column)))
                parameters.append(values)
    for parameter, column in (("station", "trading_name"), ("brand", "brand_name"), ("city", "city")):
        if filters.get(parameter):
            conditions.append(sql.SQL("{} ILIKE %s").format(sql.Identifier(column)))
            parameters.append("%" + filters[parameter] + "%")
    if filters.get("postcode"):
        conditions.append(sql.SQL("REPLACE(UPPER(postcode), ' ', '') LIKE %s"))
        parameters.append(filters["postcode"].upper().replace(" ", "") + "%")
    for parameter, column in (("district", "admin_district"), ("constituency", "parliamentary_constituency")):
        if filters.get(parameter):
            conditions.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
            parameters.append(filters[parameter])
    for parameter, operator in (("min_price", ">="), ("max_price", "<=")):
        if filters.get(parameter) is not None:
            conditions.append(sql.SQL("price {} %s").format(sql.SQL(operator)))
            parameters.append(filters[parameter])
    for parameter, clause in (("supermarket_only", "is_supermarket_service_station"),
                              ("motorway_only", "is_motorway_service_station"), ("exclude_outliers", "NOT price_is_outlier")):
        if filters.get(parameter):
            conditions.append(sql.SQL(clause))
    if any(filters.get(key) for key in ("station", "brand", "city", "postcode", "category", "district", "constituency", "supermarket_only", "motorway_only", "exclude_outliers")):
        conditions.append(sql.SQL("NOT temporary_closure"))
    return sql.SQL(" AND ").join(conditions), parameters


def resolve_history_window(connection, fuel_type, filters, start_date=None, end_date=None, days=None, role="readonly", now=None):
    start, end, capped = resolve_window(start_date, end_date, days, role, now)
    if start_date is None and end_date is not None and days is None:
        selection, parameters = select_stations(fuel_type, filters)
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("""
                SELECT MIN(fp.observed_at) AS first_observation
                FROM fuel_prices fp
                WHERE fp.fuel_type = %s AND fp.observed_at < %s
                  AND fp.node_id IN (SELECT node_id FROM current_prices WHERE {selection})
            """).format(selection=selection), (fuel_type, end, *parameters))
            earliest = cursor.fetchone()["first_observation"]
        if earliest is not None:
            start, end, capped = resolve_window(earliest.astimezone(timezone.utc).date().isoformat(), end_date, None, role, now)
    return start, end, capped


def apply_hampel(rows, granularity, field="avg_price"):
    populated = [row for row in rows if row[field] is not None]
    prices = [float(row[field]) for row in populated]
    for row in rows:
        row["unsmoothed_" + field] = row[field]
        row["hampel_" + field + "_changed"] = False
    if len(populated) < 3:
        return
    half = 24 if granularity == "hourly" else 3
    for index, price in enumerate(prices):
        window = prices[max(0, index - half):index + half + 1]
        median = statistics.median(window)
        mad = statistics.median([abs(value - median) for value in window])
        if mad * 1.4826 > 0 and abs(price - median) > 3.0 * 1.4826 * mad:
            replacement = round(median, 1)
            populated[index][field] = replacement
            populated[index]["hampel_" + field + "_changed"] = replacement != price


def cached_daily(connection, fuel_type, start, end, age_limit, filters, include_groups):
    selection, parameters = select_stations(fuel_type, filters)
    policy = list(AGE_LIMITS).index(age_limit) + 1
    grouping = sql.SQL("((dp.price_date), (dp.price_date, cp.region), (dp.price_date, cp.forecourt_type))") if include_groups else sql.SQL("((dp.price_date))")
    dimension = sql.SQL("CASE WHEN GROUPING(cp.region) = 0 THEN 'region' WHEN GROUPING(cp.forecourt_type) = 0 THEN 'forecourt_type' ELSE 'national' END") if include_groups else sql.SQL("'national'")
    label = sql.SQL("CASE WHEN GROUPING(cp.region) = 0 THEN COALESCE(cp.region, 'Unknown') WHEN GROUPING(cp.forecourt_type) = 0 THEN COALESCE(cp.forecourt_type, 'Unknown') ELSE 'All' END") if include_groups else sql.SQL("'All'")
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("""
            SELECT dp.price_date::timestamp AT TIME ZONE 'UTC' AS bucket,
                   {dimension} AS dimension, {label} AS label,
                   ROUND(AVG(price_sums[1] / hour_counts[1]), 1) AS avg_price,
                   ROUND(AVG(price_sums[%s] / NULLIF(hour_counts[%s], 0)), 1) AS age_price,
                   COUNT(*) AS stations, COUNT(*) FILTER (WHERE hour_counts[%s] > 0) AS included_stations,
                   COUNT(*) FILTER (WHERE hour_counts[%s] = 0) AS excluded_stations,
                   SUM(hour_counts[1])::bigint AS reference_hours, SUM(hour_counts[%s])::bigint AS included_hours
            FROM reconstructed_daily_prices dp
            JOIN (SELECT node_id, fuel_type, region, forecourt_type FROM current_prices WHERE {selection}) cp
              ON cp.node_id = dp.node_id AND cp.fuel_type = dp.fuel_type
                        JOIN reconstructed_daily_state cache ON cache.singleton
                            AND cache.valid_from <= %s AND cache.valid_until >= %s
            WHERE dp.fuel_type = %s AND dp.price_date >= %s AND dp.price_date < %s
            GROUP BY GROUPING SETS {grouping} ORDER BY bucket, dimension, label
        """).format(selection=selection, dimension=dimension, label=label, grouping=grouping),
                       (policy, policy, policy, policy, policy, *parameters, start.date(), end.date(), fuel_type, start.date(), end.date()))
        rows = [dict(row) for row in cursor.fetchall()]
    return {"data": [row for row in rows if row["dimension"] == "national"],
            "groups": [row for row in rows if row["dimension"] != "national"]}


def reconstruct(connection, fuel_type, start, end, granularity="daily", age_limit="none", filters=None, include_groups=False):
    if age_limit not in AGE_LIMITS or granularity not in ("daily", "hourly"):
        raise ValueError("Invalid age limit or granularity")
    if granularity != "daily":
        return _reconstruct_live(connection, fuel_type, start, end, granularity, age_limit, filters, include_groups)
    with connection.cursor() as cursor:
        cursor.execute("""SELECT to_regclass('public.reconstructed_daily_state') IS NOT NULL
                          AND to_regclass('fuel_prices') = to_regclass('public.fuel_prices')
                          AND to_regclass('current_prices') = to_regclass('public.current_prices') AS usable""")
        if not cursor.fetchone()["usable"]:
            return _reconstruct_live(connection, fuel_type, start, end, granularity, age_limit, filters, include_groups)
        cursor.execute("SELECT valid_from, valid_until FROM reconstructed_daily_state WHERE singleton")
        state = cursor.fetchone()
    if not state or not state["valid_from"] or not state["valid_until"]:
        return _reconstruct_live(connection, fuel_type, start, end, granularity, age_limit, filters, include_groups)
    lower = max(start, datetime.combine(state["valid_from"], datetime.min.time(), timezone.utc))
    upper = min(end.replace(hour=0, minute=0, second=0, microsecond=0),
                datetime.combine(state["valid_until"], datetime.min.time(), timezone.utc))
    if lower >= upper:
        return _reconstruct_live(connection, fuel_type, start, end, granularity, age_limit, filters, include_groups)
    result = cached_daily(connection, fuel_type, lower, upper, age_limit, filters or {}, include_groups)
    if not result["data"]:
        result = _reconstruct_live(connection, fuel_type, lower, upper, granularity, age_limit, filters, include_groups)
    for first, last in ((start, lower), (upper, end)):
        if first < last:
            fresh = _reconstruct_live(connection, fuel_type, first, last, granularity, age_limit, filters, include_groups)
            result["data"].extend(fresh["data"])
            result["groups"].extend(fresh["groups"])
    populated = {row["bucket"]: row for row in result["data"]}
    result["data"] = []
    if populated:
        bucket = start
        while bucket < end:
            result["data"].append(populated.get(bucket, {"bucket": bucket, "dimension": "national", "label": "All",
                                                       "avg_price": None, "age_price": None, "stations": 0,
                                                       "included_stations": 0, "excluded_stations": 0, "reference_hours": 0, "included_hours": 0}))
            bucket += timedelta(days=1)
    result["groups"].sort(key=lambda row: (row["bucket"], row["dimension"], row["label"]))
    return result


def _reconstruct_live(connection, fuel_type, start, end, granularity="daily", age_limit="none", filters=None, include_groups=False):
    if age_limit not in AGE_LIMITS or granularity not in ("daily", "hourly"):
        raise ValueError("Invalid age limit or granularity")
    selection, parameters = select_stations(fuel_type, filters or {})
    grouping = sql.SQL("((bucket), (bucket, region), (bucket, forecourt_type))") if include_groups else sql.SQL("((bucket))")
    dimension = sql.SQL("CASE WHEN GROUPING(region) = 0 THEN 'region' WHEN GROUPING(forecourt_type) = 0 THEN 'forecourt_type' ELSE 'national' END") if include_groups else sql.SQL("'national'")
    label = sql.SQL("CASE WHEN GROUPING(region) = 0 THEN region WHEN GROUPING(forecourt_type) = 0 THEN forecourt_type ELSE 'All' END") if include_groups else sql.SQL("'All'")
    daily_aggregation = sql.SQL("""
         , samples AS (
             SELECT node_id, bucket, effective_price,
                 GREATEST(0, CEIL(EXTRACT(EPOCH FROM (LEAST(next_at, end_at, bucket + step) - GREATEST(first_hour, bucket))) / 3600)) AS reference_hours,
                 GREATEST(0, CEIL(EXTRACT(EPOCH FROM (LEAST(next_at, end_at, bucket + step, age_end) - GREATEST(first_hour, bucket))) / 3600)) AS selected_hours
             FROM intervals CROSS JOIN bounds
             CROSS JOIN LATERAL generate_series(date_trunc(unit, first_hour),
              LEAST(next_at, end_at) - INTERVAL '1 microsecond', step) AS periods(bucket)
         ), station_buckets AS (
             SELECT node_id, bucket,
                 SUM(effective_price * reference_hours) / SUM(reference_hours) AS reference_price,
                 SUM(effective_price * selected_hours) / NULLIF(SUM(selected_hours), 0) AS selected_price,
                 SUM(reference_hours) AS reference_hours, SUM(selected_hours) AS selected_hours
             FROM samples WHERE reference_hours > 0 GROUP BY node_id, bucket
         )
         SELECT bucket AT TIME ZONE 'UTC' AS bucket, {dimension} AS dimension, {label} AS label,
             ROUND(AVG(reference_price), 1) AS avg_price,
             ROUND(AVG(selected_price), 1) AS age_price,
             COUNT(*) AS stations, COUNT(selected_price) AS included_stations,
             COUNT(*) - COUNT(selected_price) AS excluded_stations,
             SUM(reference_hours)::bigint AS reference_hours, SUM(selected_hours)::bigint AS included_hours
         FROM station_buckets JOIN nodes USING (node_id)
         GROUP BY GROUPING SETS {grouping} ORDER BY bucket, dimension, label
        """)
    hourly_aggregation = sql.SQL("""
         , hourly_intervals AS (
             SELECT intervals.*, LEAST(next_at, end_at) AS stop_at,
                 LEAST(next_at, end_at, age_end) AS selected_stop_at
             FROM intervals CROSS JOIN bounds
             WHERE first_hour < LEAST(next_at, end_at)
         ), changes AS (
             SELECT node_id, first_hour AS bucket, effective_price AS price_delta,
                 1 AS count_delta, 0::numeric AS selected_price_delta, 0 AS selected_count_delta
             FROM hourly_intervals
             UNION ALL
             SELECT node_id, date_trunc('hour', stop_at) + CASE WHEN date_trunc('hour', stop_at) < stop_at THEN INTERVAL '1 hour' ELSE INTERVAL '0 hours' END,
                 -effective_price, -1, 0, 0 FROM hourly_intervals
             UNION ALL
             SELECT node_id, first_hour, 0, 0, effective_price, 1
             FROM hourly_intervals WHERE first_hour < selected_stop_at
             UNION ALL
             SELECT node_id, date_trunc('hour', selected_stop_at) + CASE WHEN date_trunc('hour', selected_stop_at) < selected_stop_at THEN INTERVAL '1 hour' ELSE INTERVAL '0 hours' END,
                 0, 0, -effective_price, -1 FROM hourly_intervals WHERE first_hour < selected_stop_at
         ), deltas AS (
             SELECT bucket, {dimension} AS dimension, {label} AS label,
                 SUM(price_delta) AS price_delta, SUM(count_delta) AS count_delta,
                 SUM(selected_price_delta) AS selected_price_delta, SUM(selected_count_delta) AS selected_count_delta
             FROM changes JOIN nodes USING (node_id)
             GROUP BY GROUPING SETS {grouping}
         ), labels AS (SELECT DISTINCT dimension, label FROM deltas), totals AS (
             SELECT hours.bucket, labels.dimension, labels.label,
                 SUM(COALESCE(price_delta, 0)) OVER chronological AS price_sum,
                 SUM(COALESCE(count_delta, 0)) OVER chronological AS stations,
                 SUM(COALESCE(selected_price_delta, 0)) OVER chronological AS selected_sum,
                 SUM(COALESCE(selected_count_delta, 0)) OVER chronological AS included_stations
             FROM bounds CROSS JOIN labels
             CROSS JOIN LATERAL generate_series(start_at, end_at - INTERVAL '1 microsecond', INTERVAL '1 hour') AS hours(bucket)
             LEFT JOIN deltas ON deltas.bucket = hours.bucket AND deltas.dimension = labels.dimension AND deltas.label = labels.label
             WINDOW chronological AS (PARTITION BY labels.dimension, labels.label ORDER BY hours.bucket ROWS UNBOUNDED PRECEDING)
         )
         SELECT bucket AT TIME ZONE 'UTC' AS bucket, dimension, label,
             ROUND(price_sum / NULLIF(stations, 0), 1) AS avg_price,
             ROUND(selected_sum / NULLIF(included_stations, 0), 1) AS age_price,
             stations::bigint, included_stations::bigint, (stations - included_stations)::bigint AS excluded_stations,
             stations::bigint AS reference_hours, included_stations::bigint AS included_hours
         FROM totals ORDER BY bucket, dimension, label
        """)
    query = sql.SQL("""
        WITH bounds AS (
            SELECT %s::timestamptz AT TIME ZONE 'UTC' AS start_at,
                   %s::timestamptz AT TIME ZONE 'UTC' AS end_at,
                   %s::text AS age_limit, %s::integer AS age_days,
                   %s::text AS unit, %s::interval AS step
        ), nodes AS MATERIALIZED (
            SELECT node_id, COALESCE(region, 'Unknown') AS region,
                   COALESCE(forecourt_type, 'Unknown') AS forecourt_type
            FROM current_prices WHERE {selection}
        ), seed AS (
            SELECT DISTINCT ON (fp.node_id) fp.id, fp.node_id, fp.observed_at, fp.price, fp.anomaly_flags
            FROM fuel_prices fp JOIN nodes USING (node_id) CROSS JOIN bounds
            WHERE fp.fuel_type = %s AND fp.observed_at < start_at AT TIME ZONE 'UTC'
            ORDER BY fp.node_id, fp.observed_at DESC, fp.id DESC
        ), events AS (
            SELECT * FROM seed
            UNION ALL
            SELECT fp.id, fp.node_id, fp.observed_at, fp.price, fp.anomaly_flags
            FROM fuel_prices fp JOIN nodes USING (node_id) CROSS JOIN bounds
            WHERE fp.fuel_type = %s AND fp.observed_at >= start_at AT TIME ZONE 'UTC'
              AND fp.observed_at < end_at AT TIME ZONE 'UTC'
        ), timeline AS (
            SELECT events.*, observed_at AT TIME ZONE 'UTC' AS observed,
                   LEAD(observed_at AT TIME ZONE 'UTC', 1, end_at)
                   OVER (PARTITION BY node_id ORDER BY observed_at, id) AS next_at
            FROM events CROSS JOIN bounds
        ), intervals AS (
            SELECT timeline.*, COALESCE(pc.corrected_price, timeline.price) AS effective_price,
                   date_trunc('hour', GREATEST(observed, start_at)) +
                       CASE WHEN date_trunc('hour', GREATEST(observed, start_at)) < GREATEST(observed, start_at)
                            THEN INTERVAL '1 hour' ELSE INTERVAL '0 hours' END AS first_hour,
                   CASE WHEN age_limit = 'none' THEN end_at
                        WHEN age_limit = 'today' THEN date_trunc('day', observed) + INTERVAL '1 day'
                        ELSE observed + make_interval(days => age_days) + INTERVAL '1 microsecond' END AS age_end
            FROM timeline CROSS JOIN bounds
            LEFT JOIN price_corrections pc ON pc.fuel_price_id = timeline.id
            WHERE timeline.anomaly_flags IS NULL
        )
        {aggregation}
    """).format(selection=selection, aggregation=(hourly_aggregation if granularity == "hourly" else daily_aggregation).format(dimension=dimension, label=label, grouping=grouping))
    with connection.cursor() as cursor:
        cursor.execute(query, (start, end, age_limit, AGE_LIMITS[age_limit],
                               "day" if granularity == "daily" else "hour", "1 day" if granularity == "daily" else "1 hour",
                               *parameters, fuel_type, fuel_type))
        rows = [dict(row) for row in cursor.fetchall()]
    national = {row["bucket"]: row for row in rows if row["dimension"] == "national"}
    data = []
    if national:
        bucket = start
        step = timedelta(days=1) if granularity == "daily" else timedelta(hours=1)
        while bucket < end:
            data.append(national.get(bucket, {"bucket": bucket, "dimension": "national", "label": "All",
                                             "avg_price": None, "age_price": None, "stations": 0,
                                             "included_stations": 0, "excluded_stations": 0, "reference_hours": 0, "included_hours": 0}))
            bucket += step
    return {"data": data, "groups": [row for row in rows if row["dimension"] != "national"]}


def snapshot_sensitivity(connection, fuel_type, age_limit, as_of=None):
    if age_limit not in AGE_LIMITS:
        raise ValueError("Invalid age limit")
    as_of = as_of or datetime.now(timezone.utc)
    with connection.cursor() as cursor:
        cursor.execute("""
            WITH eligible AS (
                SELECT price, COALESCE(region, 'Unknown') AS region,
                       COALESCE(forecourt_type, 'Unknown') AS forecourt_type,
                       %s = 'none' OR
                           (%s = 'today' AND observed_at >= date_trunc('day', %s::timestamptz AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') OR
                           (%s NOT IN ('none', 'today') AND observed_at >= %s::timestamptz - make_interval(days => %s)) AS included
                FROM current_prices
                WHERE fuel_type = %s AND NOT temporary_closure AND NOT price_is_outlier
            )
            SELECT CASE WHEN GROUPING(region) = 0 THEN 'region'
                        WHEN GROUPING(forecourt_type) = 0 THEN 'forecourt_type' ELSE 'national' END AS dimension,
                   CASE WHEN GROUPING(region) = 0 THEN region
                        WHEN GROUPING(forecourt_type) = 0 THEN forecourt_type ELSE 'All' END AS label,
                   ROUND(AVG(price), 1) AS avg_price,
                   ROUND(AVG(price) FILTER (WHERE included), 1) AS age_price,
                   COUNT(*) AS stations, COUNT(*) FILTER (WHERE included) AS included_stations,
                   COUNT(*) - COUNT(*) FILTER (WHERE included) AS excluded_stations
            FROM eligible GROUP BY GROUPING SETS ((), (region), (forecourt_type))
            ORDER BY dimension, label
        """, (age_limit, age_limit, as_of, age_limit, as_of, AGE_LIMITS[age_limit], fuel_type))
        rows = [dict(row) for row in cursor.fetchall()]
    return {"as_of": as_of, "fuel_type": fuel_type, "age_limit": age_limit,
            "data": next(row for row in rows if row["dimension"] == "national"),
            "groups": [row for row in rows if row["dimension"] != "national"]}
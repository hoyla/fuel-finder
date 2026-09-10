ALTER TABLE reconstructed_daily_state
    ADD COLUMN partial_date DATE,
    ADD COLUMN partial_through TIMESTAMPTZ;

CREATE TABLE reconstructed_daily_totals (
    fuel_type TEXT NOT NULL,
    price_date DATE NOT NULL,
    price_totals NUMERIC[] NOT NULL,
    station_counts INTEGER[] NOT NULL,
    hour_counts BIGINT[] NOT NULL,
    PRIMARY KEY (fuel_type, price_date),
    CHECK (array_length(price_totals, 1) = 5),
    CHECK (array_length(station_counts, 1) = 5),
    CHECK (array_length(hour_counts, 1) = 5)
);

CREATE FUNCTION refresh_reconstructed_daily_totals(from_date DATE DEFAULT NULL)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    rebuild_from DATE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('refresh_reconstructed_daily'));
    SELECT COALESCE(from_date, MIN(price_date)) INTO rebuild_from
    FROM reconstructed_daily_prices;
    IF rebuild_from IS NULL THEN
        DELETE FROM reconstructed_daily_totals;
        RETURN;
    END IF;

    DELETE FROM reconstructed_daily_totals WHERE price_date >= rebuild_from;
    INSERT INTO reconstructed_daily_totals
        (fuel_type, price_date, price_totals, station_counts, hour_counts)
    SELECT dp.fuel_type, dp.price_date,
           ARRAY[
               SUM(dp.price_sums[1] / NULLIF(dp.hour_counts[1], 0)),
               SUM(dp.price_sums[2] / NULLIF(dp.hour_counts[2], 0)),
               SUM(dp.price_sums[3] / NULLIF(dp.hour_counts[3], 0)),
               SUM(dp.price_sums[4] / NULLIF(dp.hour_counts[4], 0)),
               SUM(dp.price_sums[5] / NULLIF(dp.hour_counts[5], 0))
           ]::numeric[],
           ARRAY[
               (COUNT(*) FILTER (WHERE dp.hour_counts[1] > 0))::integer,
               (COUNT(*) FILTER (WHERE dp.hour_counts[2] > 0))::integer,
               (COUNT(*) FILTER (WHERE dp.hour_counts[3] > 0))::integer,
               (COUNT(*) FILTER (WHERE dp.hour_counts[4] > 0))::integer,
               (COUNT(*) FILTER (WHERE dp.hour_counts[5] > 0))::integer
           ]::integer[],
           ARRAY[
               SUM(dp.hour_counts[1]), SUM(dp.hour_counts[2]),
               SUM(dp.hour_counts[3]), SUM(dp.hour_counts[4]),
               SUM(dp.hour_counts[5])
           ]::bigint[]
    FROM reconstructed_daily_prices dp
    JOIN current_prices cp
      ON cp.node_id = dp.node_id AND cp.fuel_type = dp.fuel_type
    WHERE dp.price_date >= rebuild_from
    GROUP BY dp.fuel_type, dp.price_date;
END;
$$;

CREATE OR REPLACE FUNCTION invalidate_reconstructed_daily() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    affected DATE;
BEGIN
    IF TG_TABLE_NAME = 'fuel_prices' THEN
        IF TG_OP = 'DELETE' THEN
            affected := (OLD.observed_at AT TIME ZONE 'UTC')::date;
        ELSIF TG_OP = 'UPDATE' THEN
            affected := LEAST((OLD.observed_at AT TIME ZONE 'UTC')::date,
                              (NEW.observed_at AT TIME ZONE 'UTC')::date);
        ELSE
            affected := (NEW.observed_at AT TIME ZONE 'UTC')::date;
        END IF;
    ELSE
        SELECT MIN((observed_at AT TIME ZONE 'UTC')::date) INTO affected
        FROM fuel_prices
        WHERE id IN (CASE WHEN TG_OP <> 'INSERT' THEN OLD.fuel_price_id END,
                     CASE WHEN TG_OP <> 'DELETE' THEN NEW.fuel_price_id END);
    END IF;
    UPDATE reconstructed_daily_state
    SET valid_until = CASE WHEN valid_until > affected
                           THEN GREATEST(valid_from, affected)
                           ELSE valid_until END,
        partial_through = CASE WHEN partial_date >= affected THEN NULL
                               ELSE partial_through END;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION refresh_reconstructed_daily() RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    first_date DATE;
    current_date_utc DATE := (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date;
    refresh_end TIMESTAMP := CURRENT_TIMESTAMP AT TIME ZONE 'UTC';
    rebuild_from DATE;
    cached_from DATE;
    cached_until DATE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('refresh_reconstructed_daily'));
    SELECT valid_from, valid_until INTO cached_from, cached_until
    FROM reconstructed_daily_state WHERE singleton FOR UPDATE;
    SELECT MIN((observed_at AT TIME ZONE 'UTC')::date) INTO first_date FROM fuel_prices;
    IF first_date IS NULL THEN
        DELETE FROM reconstructed_daily_prices;
        DELETE FROM reconstructed_daily_totals;
        UPDATE reconstructed_daily_state
        SET valid_from = NULL, valid_until = NULL,
            partial_date = NULL, partial_through = NULL
        WHERE singleton;
        RETURN;
    END IF;

    rebuild_from := CASE
        WHEN cached_from IS NULL OR first_date < cached_from THEN first_date
        ELSE LEAST(COALESCE(cached_until, current_date_utc), current_date_utc)
    END;
    DELETE FROM reconstructed_daily_prices WHERE price_date >= rebuild_from;
    INSERT INTO reconstructed_daily_prices
        (node_id, fuel_type, price_date, price_sums, hour_counts)
    WITH seed AS (
        SELECT DISTINCT ON (node_id, fuel_type)
               id, node_id, fuel_type, observed_at, price, anomaly_flags
        FROM fuel_prices
        WHERE observed_at < rebuild_from::timestamp AT TIME ZONE 'UTC'
        ORDER BY node_id, fuel_type, observed_at DESC, id DESC
    ), events AS (
        SELECT * FROM seed
        UNION ALL
        SELECT id, node_id, fuel_type, observed_at, price, anomaly_flags
        FROM fuel_prices
        WHERE observed_at >= rebuild_from::timestamp AT TIME ZONE 'UTC'
          AND observed_at < refresh_end AT TIME ZONE 'UTC'
    ), timeline AS (
        SELECT events.*, observed_at AT TIME ZONE 'UTC' AS observed,
               LEAD(observed_at AT TIME ZONE 'UTC', 1, refresh_end)
                   OVER (PARTITION BY node_id, fuel_type ORDER BY observed_at, id) AS next_at
        FROM events
    ), intervals AS (
        SELECT timeline.*, COALESCE(pc.corrected_price, timeline.price) AS effective_price,
               date_trunc('hour', GREATEST(observed, rebuild_from::timestamp)) +
                   CASE WHEN date_trunc('hour', GREATEST(observed, rebuild_from::timestamp))
                                  < GREATEST(observed, rebuild_from::timestamp)
                        THEN INTERVAL '1 hour' ELSE INTERVAL '0 hours' END AS first_hour
        FROM timeline
        LEFT JOIN price_corrections pc ON pc.fuel_price_id = timeline.id
        WHERE anomaly_flags IS NULL
    ), samples AS (
        SELECT node_id, fuel_type, day::date AS price_date, effective_price,
               GREATEST(first_hour, day) AS sample_start,
               LEAST(next_at, day + INTERVAL '1 day') AS sample_end,
               observed
        FROM intervals
        CROSS JOIN LATERAL generate_series(
            date_trunc('day', first_hour),
            next_at - INTERVAL '1 microsecond', INTERVAL '1 day'
        ) AS days(day)
    ), counts AS (
        SELECT *,
               GREATEST(0, CEIL(EXTRACT(EPOCH FROM (sample_end - sample_start)) / 3600)) AS hours_none,
               GREATEST(0, CEIL(EXTRACT(EPOCH FROM (LEAST(sample_end, observed + INTERVAL '30 days 1 microsecond') - sample_start)) / 3600)) AS hours_30,
               GREATEST(0, CEIL(EXTRACT(EPOCH FROM (LEAST(sample_end, observed + INTERVAL '14 days 1 microsecond') - sample_start)) / 3600)) AS hours_14,
               GREATEST(0, CEIL(EXTRACT(EPOCH FROM (LEAST(sample_end, observed + INTERVAL '7 days 1 microsecond') - sample_start)) / 3600)) AS hours_7,
               GREATEST(0, CEIL(EXTRACT(EPOCH FROM (LEAST(sample_end, date_trunc('day', observed) + INTERVAL '1 day') - sample_start)) / 3600)) AS hours_today
        FROM samples
    )
    SELECT node_id, fuel_type, price_date,
           ARRAY[
               SUM(effective_price * hours_none),
               SUM(effective_price * hours_30),
               SUM(effective_price * hours_14),
               SUM(effective_price * hours_7),
               SUM(effective_price * hours_today)
           ],
           ARRAY[
               SUM(hours_none), SUM(hours_30), SUM(hours_14),
               SUM(hours_7), SUM(hours_today)
           ]::smallint[]
    FROM counts
    WHERE hours_none > 0
    GROUP BY node_id, fuel_type, price_date;

    PERFORM refresh_reconstructed_daily_totals(rebuild_from);
    UPDATE reconstructed_daily_state
    SET valid_from = first_date, valid_until = current_date_utc,
        partial_date = current_date_utc,
        partial_through = refresh_end AT TIME ZONE 'UTC'
    WHERE singleton;
END;
$$;

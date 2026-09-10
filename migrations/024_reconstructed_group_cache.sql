ALTER TABLE reconstructed_daily_state
    ADD COLUMN group_signature TEXT,
    ADD COLUMN group_row_count BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN totals_row_count BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN station_generation BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN group_generation BIGINT NOT NULL DEFAULT -1;

CREATE TABLE reconstructed_daily_groups (
    fuel_type TEXT NOT NULL,
    price_date DATE NOT NULL,
    dimension TEXT NOT NULL CHECK (dimension IN ('region', 'forecourt_type')),
    label TEXT NOT NULL,
    price_totals NUMERIC[] NOT NULL,
    station_counts INTEGER[] NOT NULL,
    hour_counts BIGINT[] NOT NULL,
    PRIMARY KEY (fuel_type, price_date, dimension, label),
    CHECK (array_length(price_totals, 1) = 5),
    CHECK (array_length(station_counts, 1) = 5),
    CHECK (array_length(hour_counts, 1) = 5)
);

-- Group refreshes update all fuels for a date range, so the existing
-- (fuel_type, price_date, node_id) primary key cannot support this predicate.
CREATE INDEX reconstructed_daily_prices_price_date_idx
    ON reconstructed_daily_prices (price_date);

CREATE FUNCTION reconstructed_daily_group_signature()
RETURNS TEXT LANGUAGE SQL STABLE AS $$
    SELECT MD5(COALESCE(JSONB_AGG(
        JSONB_BUILD_ARRAY(node_id, fuel_type, region, forecourt_type)
        ORDER BY node_id, fuel_type, region, forecourt_type
    )::text, '[]'))
    FROM (
        SELECT node_id, fuel_type,
               COALESCE(region, 'Unknown') AS region,
               COALESCE(forecourt_type, 'Unknown') AS forecourt_type
        FROM current_prices
    ) classifications;
$$;

-- National totals depend only on the station-day cache. Under the current
-- all-historic-pairs cohort contract, the current_prices join was redundant and
-- made totals unnecessarily sensitive to materialized-view refresh timing.
CREATE OR REPLACE FUNCTION refresh_reconstructed_daily_totals(from_date DATE DEFAULT NULL)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    rebuild_from DATE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('refresh_reconstructed_daily'));
    SELECT COALESCE(from_date, MIN(price_date)) INTO rebuild_from
    FROM reconstructed_daily_prices;
    IF rebuild_from IS NULL THEN
        DELETE FROM reconstructed_daily_totals;
        UPDATE reconstructed_daily_state SET totals_row_count = 0
        WHERE singleton;
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
    WHERE dp.price_date >= rebuild_from
    GROUP BY dp.fuel_type, dp.price_date;
    UPDATE reconstructed_daily_state
    SET totals_row_count = (SELECT COUNT(*) FROM reconstructed_daily_totals)
    WHERE singleton;
END;
$$;

CREATE OR REPLACE FUNCTION refresh_reconstructed_daily_groups(from_date DATE DEFAULT NULL)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    rebuild_from DATE;
    cached_signature TEXT;
    current_signature TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('refresh_reconstructed_daily'));
    -- PostgreSQL does not support LOCK TABLE on materialized views. Copy the
    -- current classifications in one statement instead, so the signature and
    -- every grouped row are derived from exactly the same MVCC snapshot even
    -- if current_prices is refreshed concurrently.
    CREATE TEMP TABLE IF NOT EXISTS reconstructed_daily_classifications (
        node_id TEXT NOT NULL,
        fuel_type TEXT NOT NULL,
        region TEXT NOT NULL,
        forecourt_type TEXT NOT NULL,
        PRIMARY KEY (node_id, fuel_type)
    ) ON COMMIT DROP;
    TRUNCATE pg_temp.reconstructed_daily_classifications;
    INSERT INTO pg_temp.reconstructed_daily_classifications
        (node_id, fuel_type, region, forecourt_type)
    SELECT node_id, fuel_type,
           COALESCE(region, 'Unknown'),
           COALESCE(forecourt_type, 'Unknown')
    FROM current_prices;
    ANALYZE pg_temp.reconstructed_daily_classifications;

    SELECT group_signature INTO cached_signature
    FROM reconstructed_daily_state WHERE singleton;
    SELECT MD5(COALESCE(JSONB_AGG(
        JSONB_BUILD_ARRAY(node_id, fuel_type, region, forecourt_type)
        ORDER BY node_id, fuel_type, region, forecourt_type
    )::text, '[]'))
    INTO current_signature
    FROM pg_temp.reconstructed_daily_classifications;

    IF cached_signature IS DISTINCT FROM current_signature THEN
        SELECT MIN(price_date) INTO rebuild_from FROM reconstructed_daily_prices;
    ELSE
        SELECT COALESCE(from_date, MIN(price_date)) INTO rebuild_from
        FROM reconstructed_daily_prices;
    END IF;
    IF rebuild_from IS NULL THEN
        DELETE FROM reconstructed_daily_groups;
        UPDATE reconstructed_daily_state
        SET group_signature = current_signature, group_row_count = 0,
            group_generation = station_generation
        WHERE singleton;
        RETURN;
    END IF;

    DELETE FROM reconstructed_daily_groups WHERE price_date >= rebuild_from;
    INSERT INTO reconstructed_daily_groups
        (fuel_type, price_date, dimension, label,
         price_totals, station_counts, hour_counts)
    SELECT dp.fuel_type, dp.price_date,
           CASE WHEN GROUPING(cp.region) = 0 THEN 'region'
                ELSE 'forecourt_type' END AS dimension,
           CASE WHEN GROUPING(cp.region) = 0 THEN cp.region
                ELSE cp.forecourt_type END AS label,
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
    JOIN pg_temp.reconstructed_daily_classifications cp
      ON cp.node_id = dp.node_id AND cp.fuel_type = dp.fuel_type
    WHERE dp.price_date >= rebuild_from
    GROUP BY GROUPING SETS (
        (dp.fuel_type, dp.price_date, cp.region),
        (dp.fuel_type, dp.price_date, cp.forecourt_type)
    );
    UPDATE reconstructed_daily_state
    SET group_signature = current_signature,
        group_row_count = (SELECT COUNT(*) FROM reconstructed_daily_groups),
        group_generation = station_generation
    WHERE singleton;
END;
$$;

-- Keep migration 023's station-day rebuild intact and wrap it with grouped-cache
-- maintenance. Capturing the invalid boundary before the inner refresh preserves
-- correction/backfill invalidation for the grouped rows as well.
ALTER FUNCTION refresh_reconstructed_daily()
    RENAME TO refresh_reconstructed_daily_prices;

-- Guard against callers invoking the renamed station-cache function directly.
-- Every station-cache mutation advances a generation; compact groups are served
-- only after their refresh records the same generation.
CREATE FUNCTION advance_reconstructed_station_generation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE reconstructed_daily_state
    SET station_generation = station_generation + 1
    WHERE singleton;
    RETURN NULL;
END;
$$;

CREATE TRIGGER advance_reconstructed_station_generation
AFTER INSERT OR UPDATE OR DELETE ON reconstructed_daily_prices
FOR EACH STATEMENT EXECUTE FUNCTION advance_reconstructed_station_generation();

CREATE FUNCTION refresh_reconstructed_daily()
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    first_date DATE;
    current_date_utc DATE := (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date;
    rebuild_from DATE;
    cached_from DATE;
    cached_until DATE;
    cached_group_signature TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('refresh_reconstructed_daily'));
    SELECT valid_from, valid_until, group_signature
    INTO cached_from, cached_until, cached_group_signature
    FROM reconstructed_daily_state WHERE singleton FOR UPDATE;
    SELECT MIN((observed_at AT TIME ZONE 'UTC')::date)
    INTO first_date FROM fuel_prices;
    rebuild_from := CASE
        WHEN first_date IS NULL THEN NULL
        WHEN cached_from IS NULL OR first_date < cached_from THEN first_date
        ELSE LEAST(COALESCE(cached_until, current_date_utc), current_date_utc)
    END;

    PERFORM refresh_reconstructed_daily_prices();
    IF first_date IS NULL OR cached_group_signature IS NULL THEN
        PERFORM refresh_reconstructed_daily_totals();
    END IF;
    PERFORM refresh_reconstructed_daily_groups(rebuild_from);
END;
$$;

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import uuid

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
import pytest

from price_history import _reconstruct_live, cached_daily, cached_partial_daily, reconstruct


@pytest.fixture
def cache_db():
    connection = psycopg2.connect(os.environ.get("DATABASE_URL", "postgresql://fuelfinder:fuelfinder@127.0.0.1:15432/fuelfinder"), cursor_factory=RealDictCursor)
    schema = "cache_test_" + uuid.uuid4().hex
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {};").format(sql.Identifier(schema)))
        cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        for table in ("fuel_prices", "current_prices", "price_corrections"):
            cursor.execute(sql.SQL("CREATE TABLE {} AS SELECT * FROM public.{} WITH NO DATA").format(sql.Identifier(table), sql.Identifier(table)))
        cursor.execute((Path(__file__).resolve().parents[1] / "migrations/022_reconstructed_daily_cache.sql").read_text())
        cursor.execute((Path(__file__).resolve().parents[1] / "migrations/023_reconstructed_history_serving_cache.sql").read_text())
        cursor.execute((Path(__file__).resolve().parents[1] / "migrations/024_reconstructed_group_cache.sql").read_text())
        cursor.execute("INSERT INTO current_prices (node_id,fuel_type,price,region,forecourt_type,temporary_closure) VALUES ('old','E10',100,'North','Independent',false),('changing','E10',220,'South','Supermarket',false)")
        cursor.execute("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date AS today")
        today = cursor.fetchone()["today"]
        start = datetime.combine(today - timedelta(days=40), datetime.min.time(), timezone.utc)
        cursor.execute("""INSERT INTO fuel_prices (id,node_id,fuel_type,price,observed_at)
                          VALUES (1,'old','E10',100,%s), (2,'changing','E10',200,%s),
                                 (3,'changing','E10',220,%s)""", (start, start + timedelta(minutes=15), start + timedelta(days=7, hours=12)))
    try:
        yield connection, start, datetime.combine(today, datetime.min.time(), timezone.utc)
    finally:
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")
            cursor.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        connection.commit()
        connection.close()


def refresh(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT refresh_reconstructed_daily()")


def state(connection):
    with connection.cursor() as cursor:
        cursor.execute("""SELECT valid_from, valid_until, partial_date, partial_through,
                                 group_signature, station_generation, group_generation
                          FROM reconstructed_daily_state""")
        return cursor.fetchone()


@pytest.mark.parametrize("policy", ["none", "30", "14", "7", "today"])
def test_cache_matches_live_all_policies_and_groups(cache_db, policy):
    connection, start, end = cache_db
    refresh(connection)
    expected = _reconstruct_live(connection, "E10", start, end, age_limit=policy, include_groups=True)
    actual = cached_daily(connection, "E10", start, end, policy, {}, True)
    assert actual == expected
    assert state(connection)["valid_until"] == end.date()
    assert state(connection)["partial_date"] == end.date()
    assert state(connection)["partial_through"] is not None
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS count FROM reconstructed_daily_prices WHERE price_date >= %s", (end.date(),))
        assert cursor.fetchone()["count"] == 2


def test_unfiltered_completed_and_partial_days_use_totals(cache_db, monkeypatch):
    connection, start, end = cache_db
    refresh(connection)
    expected_completed = _reconstruct_live(connection, "E10", start, end)
    expected_groups = _reconstruct_live(
        connection, "E10", start, end, include_groups=True,
    )
    partial_through = state(connection)["partial_through"]
    expected_partial = _reconstruct_live(connection, "E10", end, partial_through)
    expected_partial_groups = _reconstruct_live(
        connection, "E10", end, partial_through, include_groups=True,
    )
    with connection.cursor() as cursor:
        cursor.execute("""ALTER TABLE reconstructed_daily_prices
                          DISABLE TRIGGER advance_reconstructed_station_generation""")
        cursor.execute("DELETE FROM reconstructed_daily_prices")
        cursor.execute("""ALTER TABLE reconstructed_daily_prices
                          ENABLE TRIGGER advance_reconstructed_station_generation""")
    assert cached_daily(connection, "E10", start, end, "none", {}, False) == expected_completed
    assert cached_daily(connection, "E10", start, end, "none", {}, True) == expected_groups
    assert cached_partial_daily(
        connection, "E10", end, datetime.now(timezone.utc), "none", {}, False,
    ) == expected_partial
    assert cached_partial_daily(
        connection, "E10", end, datetime.now(timezone.utc), "none", {}, True,
    ) == expected_partial_groups
    monkeypatch.setattr("price_history._reconstruct_live", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live fallback used")))
    result = reconstruct(connection, "E10", start, datetime.now(timezone.utc))
    assert len(result["data"]) == (end.date() - start.date()).days + 1
    assert result["partial_through"] is not None


def test_missing_totals_fall_back_to_station_cache(cache_db, monkeypatch):
    connection, start, end = cache_db
    refresh(connection)
    expected = _reconstruct_live(connection, "E10", start, state(connection)["partial_through"])
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM reconstructed_daily_totals")
    monkeypatch.setattr("price_history._reconstruct_live", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live fallback used")))
    actual = reconstruct(connection, "E10", start, datetime.now(timezone.utc))
    assert actual["data"] == expected["data"]
    assert actual["partial_through"] is not None


def test_incomplete_totals_fall_back_to_station_cache(cache_db):
    connection, start, end = cache_db
    refresh(connection)
    expected = _reconstruct_live(connection, "E10", start, end)
    with connection.cursor() as cursor:
        cursor.execute("""DELETE FROM reconstructed_daily_totals
                          WHERE (fuel_type, price_date) = (
                              SELECT fuel_type, price_date
                              FROM reconstructed_daily_totals LIMIT 1
                          )""")
    assert cached_daily(connection, "E10", start, end, "none", {}, False) == expected


def test_missing_groups_fall_back_to_station_cache(cache_db):
    connection, start, end = cache_db
    refresh(connection)
    expected = _reconstruct_live(
        connection, "E10", start, end, include_groups=True,
    )
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM reconstructed_daily_groups")
    assert cached_daily(connection, "E10", start, end, "none", {}, True) == expected


def test_incomplete_groups_fall_back_to_station_cache(cache_db):
    connection, start, end = cache_db
    refresh(connection)
    expected = _reconstruct_live(
        connection, "E10", start, end, include_groups=True,
    )
    with connection.cursor() as cursor:
        cursor.execute("""DELETE FROM reconstructed_daily_groups
                          WHERE (fuel_type, price_date, dimension, label) = (
                              SELECT fuel_type, price_date, dimension, label
                              FROM reconstructed_daily_groups LIMIT 1
                          )""")
    assert cached_daily(connection, "E10", start, end, "none", {}, True) == expected


def test_classification_signature_prevents_stale_groups(cache_db):
    connection, start, end = cache_db
    refresh(connection)
    original_signature = state(connection)["group_signature"]
    with connection.cursor() as cursor:
        cursor.execute("UPDATE current_prices SET region = 'West' WHERE node_id = 'old'")
        cursor.execute("SELECT reconstructed_daily_group_signature() AS signature")
        assert cursor.fetchone()["signature"] != original_signature

    expected = _reconstruct_live(
        connection, "E10", start, end, include_groups=True,
    )
    # A mismatched signature rejects compact groups and uses the correct station cache.
    assert cached_daily(connection, "E10", start, end, "none", {}, True) == expected

    refresh(connection)
    assert state(connection)["group_signature"] != original_signature
    with connection.cursor() as cursor:
        cursor.execute("""ALTER TABLE reconstructed_daily_prices
                          DISABLE TRIGGER advance_reconstructed_station_generation""")
        cursor.execute("DELETE FROM reconstructed_daily_prices")
        cursor.execute("""ALTER TABLE reconstructed_daily_prices
                          ENABLE TRIGGER advance_reconstructed_station_generation""")
    # Once refreshed, the compact cache remains sufficient without station-day rows.
    assert cached_daily(connection, "E10", start, end, "none", {}, True) == expected


def test_correction_update_delete_and_flag_changes_invalidate(cache_db):
    connection, start, end = cache_db
    refresh(connection)
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO price_corrections (id,fuel_price_id,corrected_price) VALUES (1,3,230)")
    assert state(connection)["valid_until"] == (start + timedelta(days=7)).date()
    assert cached_daily(connection, "E10", start, end, "none", {}, False)["data"] == []
    refresh(connection)
    assert cached_daily(connection, "E10", start, end, "7", {}, True) == _reconstruct_live(
        connection, "E10", start, end, age_limit="7", include_groups=True,
    )
    with connection.cursor() as cursor:
        cursor.execute("UPDATE price_corrections SET corrected_price=240 WHERE id=1")
    assert state(connection)["valid_until"] == (start + timedelta(days=7)).date()
    refresh(connection)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM price_corrections WHERE id=1")
    assert state(connection)["valid_until"] == (start + timedelta(days=7)).date()
    refresh(connection)
    with connection.cursor() as cursor:
        cursor.execute("UPDATE fuel_prices SET anomaly_flags=ARRAY['test'] WHERE id=3")
    assert state(connection)["valid_until"] == (start + timedelta(days=7)).date()
    refresh(connection)
    assert cached_daily(connection, "E10", start, end, "none", {}, True) == _reconstruct_live(
        connection, "E10", start, end, include_groups=True,
    )


def test_backfill_before_cache_start_and_current_day_insert(cache_db):
    connection, start, end = cache_db
    refresh(connection)
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO fuel_prices (id,node_id,fuel_type,price,observed_at) VALUES (4,'old','E10',110,%s)", (end,))
    assert state(connection)["valid_until"] == end.date()
    assert state(connection)["partial_through"] is None
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO fuel_prices (id,node_id,fuel_type,price,observed_at) VALUES (5,'old','E10',90,%s)", (start - timedelta(days=1),))
    assert state(connection)["valid_until"] == start.date()
    refresh(connection)
    assert state(connection)["valid_from"] == (start - timedelta(days=1)).date()
    assert cached_daily(connection, "E10", start - timedelta(days=1), end, "none", {}, True) == _reconstruct_live(
        connection, "E10", start - timedelta(days=1), end, include_groups=True,
    )
    previous = cached_daily(connection, "E10", start, end, "none", {}, False)
    refresh(connection)
    assert cached_daily(connection, "E10", start, end, "none", {}, False) == previous


def test_deleted_report_rebuild_removes_obsolete_contributions(cache_db):
    connection, start, end = cache_db
    refresh(connection)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM fuel_prices WHERE id=3")
    assert state(connection)["valid_until"] == (start + timedelta(days=7)).date()
    refresh(connection)
    assert cached_daily(connection, "E10", start, end, "none", {}, True) == _reconstruct_live(
        connection, "E10", start, end, include_groups=True,
    )


def test_refresh_reuses_connection_after_commit(cache_db):
    connection, start, end = cache_db
    refresh(connection)
    connection.commit()
    refresh(connection)
    connection.commit()
    assert cached_daily(connection, "E10", start, end, "none", {}, True) == _reconstruct_live(
        connection, "E10", start, end, include_groups=True,
    )


def test_direct_station_cache_refresh_invalidates_compact_groups(cache_db):
    connection, start, end = cache_db
    refresh(connection)
    assert state(connection)["station_generation"] == state(connection)["group_generation"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT refresh_reconstructed_daily_prices()")
    assert state(connection)["station_generation"] != state(connection)["group_generation"]
    assert cached_daily(connection, "E10", start, end, "none", {}, True) == _reconstruct_live(
        connection, "E10", start, end, include_groups=True,
    )
    refresh(connection)
    assert state(connection)["station_generation"] == state(connection)["group_generation"]


def test_failed_optional_cache_refresh_rolls_back_connection():
    from db import refresh_reconstructed_history

    class FailingCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement):
            if statement == "SELECT refresh_reconstructed_daily()":
                raise RuntimeError("maintenance failed")

        def fetchone(self):
            return (True,)

    class RecordingConnection:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return FailingCursor()

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    connection = RecordingConnection()
    with pytest.raises(RuntimeError, match="maintenance failed"):
        refresh_reconstructed_history(connection)
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_empty_source_leaves_no_cache_marked_valid(cache_db):
    connection, _, _ = cache_db
    refresh(connection)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM fuel_prices")
    refresh(connection)
    assert state(connection)["valid_from"] is None
    assert state(connection)["valid_until"] is None
    assert state(connection)["partial_date"] is None
    assert state(connection)["partial_through"] is None
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS count FROM reconstructed_daily_prices")
        assert cursor.fetchone()["count"] == 0
        cursor.execute("SELECT COUNT(*) AS count FROM reconstructed_daily_groups")
        assert cursor.fetchone()["count"] == 0

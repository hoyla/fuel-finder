"""Tests for the SQL migration runner."""

import os
import tempfile

import psycopg2
import pytest

# Override MIGRATIONS_DIR before importing migrate
import migrate


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fuelfinder:fuelfinder@localhost:5432/fuelfinder",
)


@pytest.fixture
def db():
    """Connection for migration tests."""
    conn = psycopg2.connect(DATABASE_URL)
    yield conn
    conn.close()


@pytest.fixture
def migrations_dir(tmp_path):
    """Temporary migrations directory with test SQL files.
    Uses high version numbers (9001+) to avoid clashing with real migrations.
    """
    (tmp_path / "9001_create_test.sql").write_text(
        "CREATE TABLE IF NOT EXISTS _test_migrate (id SERIAL PRIMARY KEY, val TEXT);"
    )
    (tmp_path / "9002_insert_data.sql").write_text(
        "INSERT INTO _test_migrate (val) VALUES ('hello');"
    )
    (tmp_path / "9003_add_column.sql").write_text(
        "ALTER TABLE _test_migrate ADD COLUMN IF NOT EXISTS extra TEXT;"
    )
    return tmp_path


class TestDiscoverMigrations:
    def test_finds_numbered_sql_files(self, migrations_dir):
        migrate.MIGRATIONS_DIR = str(migrations_dir)
        result = migrate._discover_migrations()
        assert len(result) == 3
        assert result[0] == (9001, "9001_create_test.sql")
        assert result[1] == (9002, "9002_insert_data.sql")
        assert result[2] == (9003, "9003_add_column.sql")

    def test_sorted_by_version(self, migrations_dir):
        migrate.MIGRATIONS_DIR = str(migrations_dir)
        result = migrate._discover_migrations()
        versions = [v for v, _ in result]
        assert versions == sorted(versions)

    def test_ignores_non_sql_files(self, migrations_dir):
        (migrations_dir / "README.md").write_text("docs")
        (migrations_dir / "notes.txt").write_text("notes")
        migrate.MIGRATIONS_DIR = str(migrations_dir)
        result = migrate._discover_migrations()
        assert len(result) == 3  # only .sql files

    def test_ignores_unnumbered_sql(self, migrations_dir):
        (migrations_dir / "scratch.sql").write_text("SELECT 1;")
        migrate.MIGRATIONS_DIR = str(migrations_dir)
        result = migrate._discover_migrations()
        assert len(result) == 3

    def test_empty_directory(self, tmp_path):
        migrate.MIGRATIONS_DIR = str(tmp_path)
        assert migrate._discover_migrations() == []

    def test_missing_directory(self):
        migrate.MIGRATIONS_DIR = "/nonexistent/path"
        assert migrate._discover_migrations() == []


class TestRunMigrations:
    def _cleanup(self, db):
        with db.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS _test_migrate CASCADE")
            cur.execute("DELETE FROM schema_migrations WHERE version >= 9000")
        db.commit()

    def test_applies_all_pending(self, db, migrations_dir):
        migrate.MIGRATIONS_DIR = str(migrations_dir)
        self._cleanup(db)
        try:
            applied = migrate.run_migrations(db)
            assert len(applied) == 3
            assert "9001_create_test.sql" in applied

            with db.cursor() as cur:
                cur.execute("SELECT val FROM _test_migrate")
                assert cur.fetchone()[0] == "hello"
        finally:
            self._cleanup(db)

    def test_idempotent(self, db, migrations_dir):
        migrate.MIGRATIONS_DIR = str(migrations_dir)
        self._cleanup(db)
        try:
            first_run = migrate.run_migrations(db)
            second_run = migrate.run_migrations(db)
            assert len(first_run) == 3
            assert len(second_run) == 0
        finally:
            self._cleanup(db)

    def test_applies_only_new(self, db, migrations_dir):
        migrate.MIGRATIONS_DIR = str(migrations_dir)
        self._cleanup(db)
        try:
            # Apply first two only
            third = migrations_dir / "9003_add_column.sql"
            third_content = third.read_text()
            third.unlink()
            migrate.run_migrations(db)

            # Restore third migration
            third.write_text(third_content)
            applied = migrate.run_migrations(db)
            assert applied == ["9003_add_column.sql"]
        finally:
            self._cleanup(db)

    def test_tracks_versions(self, db, migrations_dir):
        migrate.MIGRATIONS_DIR = str(migrations_dir)
        self._cleanup(db)
        try:
            migrate.run_migrations(db)
            versions = migrate._get_applied_versions(db)
            assert {9001, 9002, 9003}.issubset(versions)
        finally:
            self._cleanup(db)

    def test_no_migrations(self, db, tmp_path):
        migrate.MIGRATIONS_DIR = str(tmp_path)
        applied = migrate.run_migrations(db)
        assert applied == []

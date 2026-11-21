"""The schema is applied once per version of init.sql (with ATLAS_TEST_DATABASE_URL), so an
`atlas` command does not wait for a table lock behind a running export."""
import os

import psycopg
import pytest
from atlas import db

DB = os.environ.get("ATLAS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB, reason="set ATLAS_TEST_DATABASE_URL to a PostgreSQL database")


@pytest.fixture
def conns():
    a, b = psycopg.connect(DB), psycopg.connect(DB)
    a.execute("DROP TABLE IF EXISTS schema_state, migrate_t")
    a.commit()
    yield a, b
    a.rollback()
    b.rollback()
    a.execute("DROP TABLE IF EXISTS schema_state, migrate_t")
    a.commit()
    a.close()
    b.close()


def test_schema_is_applied_once_per_version(conns, tmp_path):
    a, reader = conns
    sql = tmp_path / "init.sql"
    sql.write_text("CREATE TABLE IF NOT EXISTS migrate_t (x int); ALTER TABLE migrate_t ADD COLUMN IF NOT EXISTS y int;")
    assert db.migrate(a, sql) is True
    # an export streaming from the table: its read transaction stays open
    cur = reader.cursor(name="export_x")
    cur.execute("SELECT * FROM migrate_t")
    cur.fetchmany(1)
    a.execute("SET lock_timeout = '2s'")
    assert db.migrate(a, sql) is False  # would wait for the reader if it ran the ALTER TABLE again
    assert a.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    reader.rollback()
    sql.write_text(sql.read_text() + " ALTER TABLE migrate_t ADD COLUMN IF NOT EXISTS z int;")
    assert db.migrate(a, sql) is True
    assert [c.name for c in a.execute("SELECT * FROM migrate_t").description] == ["x", "y", "z"]
    a.rollback()
    assert db.migrate(a, sql) is False


def test_a_failed_schema_is_tried_again(conns, tmp_path):
    a, _ = conns
    sql = tmp_path / "init.sql"
    sql.write_text("CREATE TABLE migrate_t (x int); SELECT nope();")
    with pytest.raises(psycopg.Error):
        db.migrate(a, sql)
    a.rollback()
    sql.write_text("CREATE TABLE migrate_t (x int);")
    assert db.migrate(a, sql) is True
    # the advisory lock was released after the failure
    assert a.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()").fetchone()[0] == 0

"""Export jobs over more than one batch, no database: a fake connection that, like PostgreSQL,
closes a named (server-side) cursor when its transaction ends (issue #99)."""
from types import SimpleNamespace

import pytest
from atlas.exporter import Cancelled, Job

COLUMNS = ("iri", "class", "class_iri", "graph_iri", "label", "provider", "title", "lon", "lat", "wkt", "geojson", "wkb")


class FakeCursor:
    def __init__(self, conn, name=None):
        self.conn, self.name = conn, name
        self.itersize = 100
        self.description = [SimpleNamespace(name=c) for c in COLUMNS]
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append(sql)
        if sql.startswith("UPDATE export"):
            self._result = [(self.conn.status,)]
        else:
            self._result = list(self.conn.features)
        self._tx = self.conn.tx

    def fetchone(self):
        return self._result[0] if self._result else None

    def __iter__(self):
        for i, r in enumerate(self._result):
            if self.name and self.conn.tx != self._tx:
                raise RuntimeError(f'cursor "{self.name}" does not exist')
            if i and i % self.itersize == 0:
                self.conn.fetches += 1
            yield r


class FakeConn:
    def __init__(self, features=(), status="running", autocommit=False):
        self.features, self.status, self.autocommit = list(features), status, autocommit
        self.tx = 0
        self.commits = 0
        self.fetches = 0
        self.statements: list[str] = []

    def cursor(self, name=None):
        return FakeCursor(self, name)

    def commit(self):
        self.commits += 1
        self.tx += 1


def feature(i: int, graph: str = "g") -> tuple:
    return (f"urn:f{i}", "operational-point", "urn:OP", graph, f"op {i}", None, None, 0.0, 0.0, None, None, None)


def job(conn: FakeConn, status_conn: FakeConn, batch: int = 2) -> Job:
    j = Job.__new__(Job)
    j.conn, j.status_conn, j.batch, j.id = conn, status_conn, batch, "x"
    j.req = SimpleNamespace(sql="true", params=())
    j.done = j.total = 0
    j._last_progress = 0.0
    return j


def test_progress_between_batches_keeps_the_cursor_open():
    conn = FakeConn([feature(i) for i in range(7)])
    status = FakeConn(autocommit=True)
    j = job(conn, status)
    seen = []
    for b in j.rows():
        seen += [r["iri"] for r in b]
        j.done += len(b)
        j.progress(force=True)
    assert seen == [f"urn:f{i}" for i in range(7)]
    assert conn.commits == 0, "the export's read transaction must not be committed while it streams"
    assert sum(s.startswith("UPDATE export") for s in status.statements) == 4
    assert conn.fetches > 0, "the export should have needed more than one fetch"


def test_batches_split_by_size_and_graph():
    conn = FakeConn([feature(0, "a"), feature(1, "a"), feature(2, "a"), feature(3, "b")])
    sizes = [len(b) for b in job(conn, FakeConn(autocommit=True)).rows()]
    assert sizes == [2, 1, 1]


def test_cancel_is_read_from_the_status_connection():
    j = job(FakeConn(), FakeConn(status="cancelled", autocommit=True))
    with pytest.raises(Cancelled):
        j.progress(force=True)
    assert j.conn.commits == 0

"""CSV / XLSX export of the features matching a query state (GET /api/export).

Rows come from a server-side cursor in batches, so memory stays flat however
many features match. CSV is streamed to the client as it is produced; XLSX is
written with XlsxWriter's constant_memory mode to a temporary file (a zip
cannot be streamed) and sent when complete.
"""
from __future__ import annotations

import csv
import io
import os
import tempfile
from collections.abc import Callable, Iterator

import xlsxwriter

BATCH = 2000
XLSX_MAX_ROWS = 1_048_575  # Excel's sheet limit minus the header row
XLSX_MAX_CELL = 32_767     # Excel's cell text limit

BASE_COLUMNS = [
    ("iri", "IRI"), ("label", "Label"), ("class", "Class"), ("dataset", "Dataset"),
    ("provider", "Provider"), ("lon", "Longitude"), ("lat", "Latitude"), ("wkt", "Geometry (WKT)"),
]


class Column:
    __slots__ = ("key", "header", "facet")

    def __init__(self, key: str, header: str, facet: bool):
        self.key, self.header, self.facet = key, header, facet


def columns_for(classes: list[str], facet_defs: list[dict], only: list[str] | None = None) -> list[Column]:
    """Base columns plus one column per facet of the given classes (shared facets first,
    then each class's facets in shape order). A facet key shared by several classes is
    one column. `only`: just these facet keys (a view's parameters and checks), in that order."""
    cols = [Column(k, h, False) for k, h in BASE_COLUMNS]
    seen: set[str] = set()
    if only is not None:
        rank = {k: i for i, k in enumerate(only)}
        facet_defs = sorted((f for f in facet_defs if f["key"] in rank), key=lambda f: rank[f["key"]])
    for scope in ["*", *classes]:
        for f in facet_defs:
            if f["class"] != scope or f["key"] in seen:
                continue
            seen.add(f["key"])
            header = f["label"] + (f" [{f['unit']}]" if f.get("unit") else "")
            cols.append(Column(f["key"], header, True))
    return cols


def feature_rows(conn_factory, where_sql: str, params: list) -> Iterator[list[dict]]:
    """Batches of matching features, ordered by class (shape order) then label."""
    with conn_factory() as conn, conn.cursor(name="atlas_export") as cur:
        cur.execute(f"""
            SELECT f.iri, f.label, f.class, f.facets, d.title AS dataset, d.provider,
                   round(ST_X(ST_PointOnSurface(f.geom))::numeric, 7)::float8 AS lon,
                   round(ST_Y(ST_PointOnSurface(f.geom))::numeric, 7)::float8 AS lat,
                   ST_AsText(f.geom, 7) AS wkt
            FROM feature f
            LEFT JOIN class_def cd ON cd.class = f.class
            LEFT JOIN dataset d ON d.env = f.env AND d.graph_iri = f.graph_iri
            WHERE {where_sql}
            ORDER BY cd.position NULLS LAST, f.class, f.label NULLS LAST, f.id""", params)
        while rows := cur.fetchmany(BATCH):
            yield rows


class Values:
    """Turns stored facet values into cell values: IRIs become labels (or stay IRIs),
    several values are joined with '; ', a single number stays a number."""

    def __init__(self, labels: Callable[[set[str]], dict[str, str]], local: Callable[[str], str], use_labels: bool):
        self._lookup, self._local, self.use_labels = labels, local, use_labels
        self._cache: dict[str, str] = {}

    def prime(self, rows: list[dict]) -> None:
        if not self.use_labels:
            return
        iris = {v for r in rows for vs in r["facets"].values() for v in vs
                if isinstance(v, str) and v not in self._cache}
        if iris:
            found = self._lookup(iris)
            for i in iris:
                self._cache[i] = found.get(i) or (self._local(i) if i.startswith(("http", "urn:")) else i)

    def one(self, v):
        if isinstance(v, bool):
            return ("Yes" if v else "No") if self.use_labels else ("true" if v else "false")
        if isinstance(v, str):
            return self._cache.get(v, v) if self.use_labels else v
        return v

    def cell(self, vs):
        if not vs:
            return None
        if len(vs) == 1:
            return self.one(vs[0])
        return "; ".join(str(self.one(v)) for v in vs)


def _row(r: dict, cols: list[Column], vals: Values, class_labels: dict[str, str]) -> list:
    out = []
    for c in cols:
        if c.facet:
            out.append(vals.cell(r["facets"].get(c.key)))
        elif c.key == "class":
            out.append(class_labels.get(r["class"], r["class"]))
        else:
            out.append(r[c.key])
    return out


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(v):
    """Neutralise text a spreadsheet would run as a formula (CSV injection)."""
    if v is None:
        return ""
    if isinstance(v, str) and v.startswith(_FORMULA_START):
        try:
            float(v)
        except ValueError:
            return "'" + v
    return v


def stream_csv(batches: Iterator[list[dict]], cols: list[Column], vals: Values,
               class_labels: dict[str, str]) -> Iterator[bytes]:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    buf.write("﻿")  # BOM: lets Excel open the UTF-8 file with accents intact
    w.writerow([c.header for c in cols])
    for rows in batches:
        vals.prime(rows)
        for r in rows:
            w.writerow([_csv_safe(v) for v in _row(r, cols, vals, class_labels)])
        yield buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate()
    if buf.tell():
        yield buf.getvalue().encode("utf-8")


def _sheet_name(label: str, used: set[str]) -> str:
    base = "".join("_" if ch in '[]:*?/\\' else ch for ch in label).strip("'")[:31] or "Sheet"
    name, n = base, 2
    while name.lower() in used:
        suffix = f" ({n})"
        name, n = base[: 31 - len(suffix)] + suffix, n + 1
    used.add(name.lower())
    return name


def write_xlsx(batches: Iterator[list[dict]], cols_for_class: Callable[[str], list[Column]], vals: Values,
               class_labels: dict[str, str], info: list[tuple[str, str]]) -> str:
    """Writes one sheet per class (split when a class exceeds Excel's row limit) plus an
    'Export' sheet describing the query. Returns the temporary file's path."""
    fd, path = tempfile.mkstemp(prefix="atlas-export-", suffix=".xlsx")
    os.close(fd)
    wb = xlsxwriter.Workbook(path, {"constant_memory": True, "strings_to_formulas": False,
                                    "strings_to_urls": False, "strings_to_numbers": False})
    try:
        head = wb.add_format({"bold": True, "bg_color": "#E8EEF8", "bottom": 1})
        used: set[str] = {"export"}
        about = wb.add_worksheet("Export")
        counts: dict[str, int] = {}
        clipped = 0
        ws = None
        cur_class = None
        row = 0
        cols: list[Column] = []

        def new_sheet(cls: str):
            nonlocal ws, row
            ws = wb.add_worksheet(_sheet_name(class_labels.get(cls, cls), used))
            for i, c in enumerate(cols):
                ws.write_string(0, i, c.header, head)
                ws.set_column(i, i, 42 if c.key in ("iri", "wkt") else 24 if c.key in ("label", "dataset") else 16)
            ws.freeze_panes(1, 2)
            row = 0

        for rows in batches:
            vals.prime(rows)
            for r in rows:
                if r["class"] != cur_class:
                    if ws is not None and row:
                        ws.autofilter(0, 0, row, len(cols) - 1)
                    cur_class = r["class"]
                    cols = cols_for_class(cur_class)
                    new_sheet(cur_class)
                elif row >= XLSX_MAX_ROWS:
                    ws.autofilter(0, 0, row, len(cols) - 1)
                    new_sheet(cur_class)
                row += 1
                counts[cur_class] = counts.get(cur_class, 0) + 1
                for i, v in enumerate(_row(r, cols, vals, class_labels)):
                    if v is None:
                        continue
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        ws.write_number(row, i, v)
                    else:
                        s = str(v)
                        if len(s) > XLSX_MAX_CELL:
                            clipped += 1
                            continue
                        ws.write_string(row, i, s)
        if ws is not None and row:
            ws.autofilter(0, 0, row, len(cols) - 1)

        lines = list(info)
        lines.append(("Features", str(sum(counts.values()))))
        for cls, n in counts.items():
            lines.append((f"  {class_labels.get(cls, cls)}", str(n)))
        if clipped:
            lines.append(("Note", f"{clipped} cells (mostly long WKT geometries) exceed Excel's "
                                  f"{XLSX_MAX_CELL} character limit and were left empty; use CSV for those."))
        about.set_column(0, 0, 22)
        about.set_column(1, 1, 100)
        for i, (k, v) in enumerate(lines):
            about.write_string(i, 0, k, head if i == 0 else None)
            about.write_string(i, 1, v)
        wb.close()
    except BaseException:
        try:
            wb.close()
        finally:
            os.unlink(path)
        raise
    return path

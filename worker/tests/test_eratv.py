"""ERATV vehicle types: XML parsing, RDF conversion with the ERA ontology, the polite client
and (with ATLAS_TEST_DATABASE_URL) the harvest tables. No request to ERATV.

The XML files in data/eratv are real exports (2026-11-10) with the contact data of the
manufacturer and the authorisation holder replaced by "(removed)". vocab.ttl is the part
of ERA's ontology and SKOS (rinf-plus, graph rinf/ontology and rinf/skos) the
conversion uses."""
import json
import os
from pathlib import Path

import httpx
import pyoxigraph as ox
import pytest
from atlas.eratv import convert as cv
from atlas.eratv import vocab as vb
from atlas.eratv.client import CaptchaRequired, Client, internal_id_from_view, parse_list
from atlas.eratv.parse import parse, state_from_filename

DATA = Path(__file__).parent / "data" / "eratv"
ERA = "http://data.europa.eu/949/"
IORE = "11-256-0001-3-002-001"          # locomotive, Sweden, 2 configurations
ETR = "13-142-0008-8-001-001"           # EMU, EU authorisation, 3 energy supply systems
WAGON = "51-508-0001-8-001-001"         # freight wagon, payload per line category
OLD = "51-004-0003-4-001"               # wagon under Directive 2008/57, few 4.x parameters


@pytest.fixture(scope="module")
def vocab():
    return vb.load_file(DATA / "vocab.ttl")


def rec(tid):
    return parse((DATA / f"{tid}.xml").read_bytes())


@pytest.fixture(scope="module")
def iore(vocab):
    return cv.convert(rec(IORE), vocab)


@pytest.fixture(scope="module")
def etr(vocab):
    return cv.convert(rec(ETR), vocab)


def graph(res) -> ox.Store:
    s = ox.Store()
    s.load(res.turtle().encode(), format=ox.RdfFormat.TURTLE)
    return s


def objects(store, s, p):
    return {q.object.value for q in store.quads_for_pattern(ox.NamedNode(s), ox.NamedNode(p), None)}


# -- parsing -------------------------------------------------------------------------
def test_parse_reads_parameters_in_sequence():
    r = rec(IORE)
    assert r.type_id == IORE
    assert r.first("1.1") == "IORE M"
    # 4.13.1.1, .5, .7, .8, .9 share one <Characteristic> element
    assert r.first("4.13.1.5") == "ATC v2 (via STM in ETCS onboard system)"
    assert [p.values for p in r.all("4.13.1.8")] == [["ESC-SE-01-HiL2", "ESC-SE-05-BoL2B3"]]
    wire = r.all("4.10.5")[0]
    assert wire.config == "AC 15kV-16.7Hz"
    assert wire.pairs() == [("0004.60", "m"), ("0006.20", "m")]
    assert r.configurations() == ["1435mm / AC 15kV-16.7Hz / ATC v2",
                                  "1435mm / AC 15kV-16.7Hz / Implementing Regulation (EU) 2020/420 (Only German) Set_2"]
    assert [a.title for a in r.authorisations] == ["Sweden"]


def test_parse_drops_contact_data():
    for tid in (IORE, ETR, WAGON):
        r = rec(tid)
        codes = {p.code for p in r.params} | {p.code for a in r.authorisations for p in a.params}
        assert not any(c and (c.startswith("1.3.2") or ".2.2." in c) for c in codes), tid


def test_parse_rejects_other_xml():
    with pytest.raises(ValueError):
        parse(b"<html/>")


def test_state_from_filename():
    assert state_from_filename("attachment; filename=VehicleType_11-256-0001-3-002-001_PUBLISHED.xml") == \
        ("11-256-0001-3-002-001", "PUBLISHED")
    assert state_from_filename("attachment; filename=VehicleType_51-376-0002-8-001-001_DRAFT.xml")[1] == "DRAFT"
    assert state_from_filename(None) == (None, None)


# -- values --------------------------------------------------------------------------
def test_numbers_units_countries():
    assert cv.number("0004.60") == 4.6
    assert cv.number("Single: 00010,100") == 10.1
    assert cv.number("None") is None
    assert cv.convert_unit(30000, "kg", "http://qudt.org/vocab/unit/TONNE") == 30
    assert cv.convert_unit(1450, "mm", "http://qudt.org/vocab/unit/M") == 1.45
    assert cv.convert_unit(70, " Ν", "http://qudt.org/vocab/unit/N") == 70     # Greek capital Nu
    assert cv.countries("AT(Austria), IT(Italy)") == [cv.COUNTRY + "AUT", cv.COUNTRY + "ITA"]
    assert cv.countries("Sweden(SE)") == [cv.COUNTRY + "SWE"]


def test_vocab_lookup(vocab):
    assert vocab.props("4.1.2.1")[0].local == "maximumDesignSpeed"
    tpls = vb.TPLS_SCHEME
    assert vocab.concept(tpls, "ATC v2") == ERA + "concepts/train-protection-legacy-systems/48"
    assert vocab.concept(vb.ENERGY_SCHEME, "AC 25kV-50Hz") == ERA + "concepts/energy-supply-systems/AC10"
    pant = vocab.prop("vehiclePantographHead").scheme
    assert vocab.concept(pant, "1800mm") is not None                     # '1800mm' == '1800 mm'
    fire = vocab.prop("fireSafetyCategory").scheme
    assert vocab.concept(fire, "B") is not None                          # alias 'Cat B'
    assert vocab.concept(fire, "SRT TSI Freight Locomotive") is not None # unique prefix


def test_vocab_json_roundtrip(vocab):
    again = vb.from_json(json.loads(json.dumps(vb.to_json(vocab))))
    a = cv.convert(rec(ETR), vocab).turtle()
    b = cv.convert(rec(ETR), again).turtle()
    assert sorted(a.splitlines()) == sorted(b.splitlines())


# -- conversion ----------------------------------------------------------------------
def test_locomotive(iore):
    assert iore.coverage == 1.0 and not iore.unmapped
    g = graph(iore)
    vt = ERA + "vehicleTypes/" + IORE
    assert objects(g, vt, cv.RDF + "type") == {ERA + "VehicleType"}
    assert objects(g, vt, ERA + "vehicleTypeNumber") == {"11-256-0001-3"}
    assert objects(g, vt, ERA + "previousVehicleType") == {ERA + "vehicleTypes/11-256-0001-3-001-001"}
    assert objects(g, vt, ERA + "wheelSetGauge") == {ERA + "concepts/nominal-track-gauges/30"}
    assert objects(g, vt, ERA + "maximumDesignSpeed") == {"80"}
    assert objects(g, vt, ERA + "protectionLegacySystem") == {ERA + "concepts/train-protection-legacy-systems/48"}
    assert objects(g, vt, ERA + "staticAxleLoadWorkingOrder") == {"30000"}           # kg, as the ontology
    # values per energy supply system go to that configuration's parameter set
    cps = [c for c in objects(g, vt, ERA + "configDependentParameter") if c.endswith("ac-15kv-16-7hz")]
    assert len(cps) == 1
    assert objects(g, cps[0], ERA + "minimumContactWireHeight") == {"4.6"}
    assert objects(g, cps[0], ERA + "maximumContactWireHeight") == {"6.2"}
    assert objects(g, cps[0], ERA + "shortestDistanceBetweenPantographsInContactWithOCL") == {"10.1"}
    # the full configurations name their gauge, energy supply system and class B system
    cfg = ERA + "vehicleTypes/configurations/cfg-1435mm-ac-15kv-16-7hz-atc-v2"
    assert objects(g, cfg, ERA + "forEnergySupplySystem") == {ERA + "concepts/energy-supply-systems/AC20"}
    assert objects(g, cfg, ERA + "forOnBoardClassB") == {ERA + "concepts/train-protection-legacy-systems/48"}


def test_authorisation(iore):
    a = iore.summary["authorisations"][0]
    assert a["status"] == "Valid" and a["ein"] == "SE8020260354" and a["holder"] == "LKAB Malmtrafik AB"
    radius = [r for r in a["restrictions"] if r["code"] == "1.1"]
    assert {r["minimumCurveRadius"] for r in radius} == {90}
    assert {r["maximumSpeed"] for r in a["restrictions"] if r["code"] == "1.3"} == {80}
    g = graph(iore)
    case = next(q.subject.value for q in g.quads_for_pattern(None, ox.NamedNode(cv.VPA + "concerns"), None))
    assert objects(g, case, ERA + "areaOfUse") == {cv.COUNTRY + "SWE"}
    assert objects(g, case, ERA + "authorisingMemberState") == {cv.COUNTRY + "SWE"}
    assert "info@" not in iore.turtle()


def test_emu_with_three_energy_supply_systems(etr):
    g = graph(etr)
    vt = ERA + "vehicleTypes/" + ETR
    assert objects(g, vt, ERA + "energySupplySystem") == {
        ERA + "concepts/energy-supply-systems/" + x for x in ("AC10", "AC20", "DC30")}
    dc = next(c for c in objects(g, vt, ERA + "configDependentParameter") if c.endswith("-dc-3kv"))
    assert objects(g, dc, ERA + "minimumContactWireHeight") == {"4.45"}
    # an EU authorisation lists every country of its area of use; no single member state
    case = next(q.subject.value for q in g.quads_for_pattern(None, ox.NamedNode(cv.VPA + "concerns"), None))
    assert objects(g, case, ERA + "areaOfUse") == {cv.COUNTRY + "AUT", cv.COUNTRY + "ITA"}
    assert not objects(g, case, ERA + "authorisingMemberState")
    # '100 m in depot / 150 m for revenue service': the higher figure
    assert {r.get("minimumCurveRadius") for r in etr.summary["authorisations"][0]["restrictions"]
            if r["code"] == "1.1"} == {150}
    assert etr.summary["minHorizontalRadius"] == 100
    assert len(etr.summary["configurations"]) == 10


def test_unmapped_values_are_reported_not_guessed(vocab):
    res = cv.convert(rec(WAGON), vocab)
    assert {u["code"] for u in res.unmapped} == {"4.5.1"}
    assert 0 < res.coverage < 1
    old = cv.convert(rec(OLD), vocab)
    assert any(u["code"] == "4.3.1" and u["value"] == "T(RIV)" for u in old.unmapped)


def test_convert_needs_the_ontology():
    with pytest.raises(ValueError):
        cv.convert(rec(IORE), vb.Vocab())


# -- client --------------------------------------------------------------------------
def test_parse_list():
    rows = parse_list((DATA / "list.html").read_text(encoding="utf-8"))
    assert len(rows) == 15
    assert rows[1].type_id == IORE and rows[1].name == "IORE M" and rows[1].last_update == "2026-11-10"
    assert rows[1].status == "Valid (Sweden)"


def test_internal_id_from_view():
    assert internal_id_from_view('<a href="/Eratv/Home/ExportVehicleType/12558?exportTo=XML">') == 12558
    assert internal_id_from_view('<a href="/Eratv/Home/ExportVehicleType/0?exportTo=XML">') == 0
    assert internal_id_from_view("<html/>") is None


def _client(handler) -> Client:
    return Client(delay=0, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_client_views_before_export_and_skips_drafts():
    xml = (DATA / f"{IORE}.xml").read_bytes()
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        path = req.url.path
        if path == "/Eratv/Home/View/12558":
            return httpx.Response(200, text='<a href="/Eratv/Home/ExportVehicleType/12558?exportTo=XML">x</a>')
        if path == "/Eratv/Home/View/5000":
            return httpx.Response(200, text='<a href="/Eratv/Home/ExportVehicleType/0?exportTo=XML">x</a>')
        if path == "/Eratv/Home/ExportVehicleType/12558":
            return httpx.Response(200, content=xml, headers={
                "content-type": "application/XML",
                "content-disposition": f"attachment; filename=VehicleType_{IORE}_PUBLISHED.xml"})
        return httpx.Response(302, headers={"location": "/Eratv/Error/NotFound"})

    with _client(handler) as c:
        ex = c.export(12558)
        assert (ex.type_id, ex.state, ex.xml) == (IORE, "PUBLISHED", xml)
        assert c.export(5000).state == "NOT_PUBLIC"
        assert c.export(12549).state is None
    assert seen == ["/Eratv/Home/View/12558", "/Eratv/Home/ExportVehicleType/12558",
                    "/Eratv/Home/View/5000", "/Eratv/Home/View/12549"]          # no draft was exported


def test_client_stops_at_captcha():
    def handler(req):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<form><label>Enter the CAPTCHA:</label> url: '/Eratv/Captcha/ValidateCaptcha'</form>")
    with _client(handler) as c, pytest.raises(CaptchaRequired):
        c.view(IORE)


def test_client_backs_off_then_gives_up():
    calls, slept = [], []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, text="<html><head><title>Request Rejected</title></head></html>")
    c = Client(delay=0, transport=httpx.MockTransport(handler), sleep=slept.append)
    with pytest.raises(Exception, match="refusing"):
        c.latest()
    assert len(calls) == 5 and slept and slept[0] >= 30


# -- database (optional) -------------------------------------------------------------
DB = os.environ.get("ATLAS_TEST_DATABASE_URL")


@pytest.mark.skipif(not DB, reason="set ATLAS_TEST_DATABASE_URL to a database with the eratv_* tables")
def test_store_import_and_reconvert(vocab, tmp_path):
    import psycopg
    from atlas.eratv import harvest
    conn = psycopg.connect(DB)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM eratv_type")
    conn.commit()
    (tmp_path / f"VehicleType_{ETR}_DRAFT.xml").write_bytes(b"<VehicleType/>")
    for tid in (IORE, WAGON):
        (tmp_path / f"VehicleType_{tid}_PUBLISHED.xml").write_bytes((DATA / f"{tid}.xml").read_bytes())
    assert harvest.import_files(conn, vocab, [tmp_path]) == (2, 1)
    tid, changed = harvest.store(conn, vocab, (DATA / f"{IORE}.xml").read_bytes(), 12558, "2026-11-10")
    assert (tid, changed) == (IORE, False)
    with conn.cursor() as cur:
        cur.execute("SELECT internal_id, last_update::text, name, coverage, summary->>'typeNumber' "
                    "FROM eratv_type WHERE type_id=%s", (IORE,))
        assert cur.fetchone() == (12558, "2026-11-10", "IORE M", 1.0, "11-256-0001-3")
    assert harvest.reconvert(conn, vocab) == 2
    harvest.withdraw(conn, WAGON, "NOT_PUBLIC")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM eratv_type")
        assert cur.fetchone()[0] == 1
    conn.close()


@pytest.mark.skipif(not DB, reason="set ATLAS_TEST_DATABASE_URL to a database with the eratv_* tables")
def test_lookups_fetch_upload_and_stop_at_captcha(vocab):
    import json

    import psycopg
    from atlas.config import Eratv
    from atlas.eratv import vocab as vocab_mod
    from atlas.eratv.lookup import Lookups

    conn = psycopg.connect(DB)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM eratv_type; DELETE FROM eratv_request; DELETE FROM eratv_vocab")
        cur.execute("INSERT INTO eratv_vocab (id, env, vocab) VALUES (1, 'test', %s)",
                    (json.dumps(vocab_mod.to_json(vocab)),))
    conn.commit()
    xml = (DATA / f"{IORE}.xml").read_bytes()
    seen, captcha = [], [False]

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if captcha[0]:
            return httpx.Response(200, text="<label>Enter the CAPTCHA:</label> /Eratv/Captcha/ValidateCaptcha")
        if req.url.path == f"/Eratv/Home/View/{IORE}":
            return httpx.Response(200, text='<a href="/Eratv/Home/ExportVehicleType/12558?exportTo=XML">x</a>')
        if req.url.path == "/Eratv/Home/ExportVehicleType/12558":
            return httpx.Response(200, content=xml, headers={
                "content-type": "application/XML",
                "content-disposition": f"attachment; filename=VehicleType_{IORE}_PUBLISHED.xml"})
        return httpx.Response(302, headers={"location": "/Eratv/Error/NotFound"})

    class Cfg:
        eratv = Eratv(lookups_per_hour=3)

    lk = Lookups(Cfg(), client_factory=lambda: _client(handler))

    def ask(type_id=None, refresh=False, upload=None, filename=None):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO eratv_request (type_id, refresh, xml, filename) VALUES (%s,%s,%s,%s) RETURNING id",
                        (type_id, refresh, upload, filename))
            rid = cur.fetchone()[0]
        conn.commit()
        assert lk.step(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT status, reason, type_id, xml IS NULL FROM eratv_request WHERE id=%s", (rid,))
            return cur.fetchone()

    assert ask(IORE) == ("done", None, IORE, True)
    assert seen == [f"/Eratv/Home/View/{IORE}", "/Eratv/Home/ExportVehicleType/12558"]
    assert ask(IORE) == ("done", None, IORE, True) and len(seen) == 2          # stored: no request
    assert ask(IORE, refresh=True)[0] == "done" and len(seen) == 2             # fetched less than an hour ago
    assert ask("11-999-0000-0-001-001")[:2] == ("failed", "not_found")
    assert ask("not a type")[:2] == ("failed", "invalid")
    captcha[0] = True
    assert ask(ETR)[:2] == ("failed", "captcha")
    n = len(seen)
    assert ask(ETR)[:2] == ("failed", "captcha") and len(seen) == n            # paused: ERATV is left alone
    lk.paused_until = 0
    assert ask(ETR)[:2] == ("failed", "limit") and len(seen) == n              # 3 fetches in the last hour
    # an upload needs no request to ERATV; drafts are refused
    wagon = (DATA / f"{WAGON}.xml").read_bytes()
    assert ask(upload=wagon, filename=f"VehicleType_{WAGON}_PUBLISHED.xml") == ("done", None, WAGON, True)
    assert ask(upload=wagon, filename=f"VehicleType_{WAGON}_DRAFT.xml")[:2] == ("failed", "not_public")
    assert ask(upload=b"<html/>", filename="x.xml")[:2] == ("failed", "invalid")
    assert not lk.step(conn)
    conn.close()

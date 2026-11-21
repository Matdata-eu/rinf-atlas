"""Data quality through TripleTrail (atlas.tt_shacl): the shapes it is given, how its report is
read, and, when the `tripletrail` command is installed, the same results as atlas.shacl."""
import os
import shutil

import pyoxigraph as ox
import pytest
from atlas import quality, shacl, tt_shacl
from test_quality import ONTO, SHAPES, G, _results, _store, _validate

SH = shacl.SH
EXE = shutil.which(os.environ.get("ATLAS_TRIPLETRAIL_BIN", "tripletrail"))
needs_tt = pytest.mark.skipif(EXE is None, reason="the tripletrail command is not installed")


def _tt(st, tmp_path, limits=None):
    return tt_shacl.validate_graph(st, G, SHAPES, [ONTO], limits or shacl.Limits(), str(tmp_path / "tt"), EXE)


def test_shapes_keep_shape_iris_and_restore_lists_and_paths(tmp_path):
    st = _store(tmp_path, skolem_shapes=True)
    triples = [q.triple for q in st.quads_for_pattern(None, None, None, ox.NamedNode(SHAPES))]
    assert any(t.subject.value.startswith(shacl.SKOLEM) for t in triples if isinstance(t.subject, ox.NamedNode))
    out, excluded = tt_shacl.shapes_triples(triples, ["http://ex/NeedsComment", "Closed"])
    g = shacl.ShapesGraph(out)
    # the anonymous property shape of ex:ClosedShape keeps its skolem IRI (results name it) ...
    closed = ox.NamedNode("http://ex/ClosedShape")
    prop = g.one(closed, SH + "property")
    assert isinstance(prop, ox.NamedNode) and prop.value.startswith(shacl.SKOLEM)
    # ... while lists and paths are blank nodes again, as SHACL reads them
    nxt = g.one(ox.NamedNode("http://ex/Next"), SH + "path")
    assert isinstance(nxt, ox.BlankNode) and g.path(nxt)[0] == "^<http://ex/prev>"
    assert isinstance(g.one(closed, SH + "ignoredProperties"), ox.BlankNode)
    # left out: the named SPARQL constraint (its link dropped) and the shapes matching "Closed"
    assert excluded == {"http://ex/NeedsComment", "http://ex/ClosedShape"}
    point = ox.NamedNode("http://ex/PointShape")
    assert {o.value for o in g.objs(point, SH + "sparql")} == {"http://ex/NoSelfLink"}
    assert g.lit(closed, SH + "deactivated") == "true"


def test_report_is_deduplicated_filtered_and_capped():
    sh = "http://www.w3.org/ns/shacl#"
    report = f"""
@prefix sh: <{sh}> . @prefix ex: <http://ex/> .
[] a sh:ValidationReport ; sh:result _:r1, _:r2, _:r3, _:r4 .
_:r1 a sh:ValidationResult ; sh:focusNode ex:bad ; sh:sourceShape ex:Next ; sh:resultSeverity sh:Warning ;
  sh:sourceConstraintComponent sh:MaxCountConstraintComponent ; sh:resultPath [ sh:inversePath ex:prev ] ;
  sh:resultMessage "Zu viele"@de, "Too many"@en .
_:r2 a sh:ValidationResult ; sh:focusNode ex:bad ; sh:sourceShape ex:Next ; sh:resultSeverity sh:Warning ;
  sh:sourceConstraintComponent sh:MaxCountConstraintComponent ; sh:resultPath [ sh:inversePath ex:prev ] ;
  sh:resultMessage "Too many"@en .
_:r3 a sh:ValidationResult ; sh:focusNode ex:Concept1 ; sh:sourceShape ex:Kind ; sh:resultSeverity sh:Violation ;
  sh:sourceConstraintComponent sh:ClassConstraintComponent .
_:r4 a sh:ValidationResult ; sh:focusNode ex:good ; sh:sourceShape ex:Name ; sh:resultSeverity sh:Violation ;
  sh:sourceConstraintComponent sh:MinCountConstraintComponent ; sh:resultPath ex:name .
"""
    g = shacl.ShapesGraph(ox.parse(report.encode(), format=ox.RdfFormat.TURTLE))
    in_data = {"http://ex/bad", "http://ex/good"}
    run = shacl.Run(graph=G)
    tt_shacl.read_report(g, lambda t: t.value in in_data, shacl.Limits(max_total=1), run)
    # r2 repeats r1 (TripleTrail gives a SPARQL constraint's duplicate rows each a result),
    # r3's focus node is in the vocabularies only
    assert dict(run.totals) == {shacl.WARNING: 1, shacl.VIOLATION: 1}
    assert run.focus_with_results == {"<http://ex/bad>", "<http://ex/good>"}
    assert run.kept() == 1
    r = run.results[0]
    assert (r.message, r.path, r.path_ttl) == ("Too many", "^<http://ex/prev>", "[ sh:inversePath <http://ex/prev> ]")


def test_engine_choice(monkeypatch):
    from types import SimpleNamespace as NS
    monkeypatch.setattr(tt_shacl, "binary", lambda name: None)
    assert quality.engine(NS(engine="auto", tripletrail_bin="tripletrail")) == ("builtin", None)
    with pytest.raises(quality.ShapesError):
        quality.engine(NS(engine="tripletrail", tripletrail_bin="tripletrail"))
    monkeypatch.setattr(tt_shacl, "binary", lambda name: "/usr/bin/" + name)
    assert quality.engine(NS(engine="auto", tripletrail_bin="tripletrail")) == ("tripletrail", "/usr/bin/tripletrail")
    assert quality.engine(NS(engine="builtin", tripletrail_bin="tripletrail")) == ("builtin", None)
    # switching validators validates every graph again; the built-in one keeps today's fingerprints
    tt = quality.run_fingerprint(NS(exclude_shapes=[], engine="auto"), "abc")
    assert tt != "abc" and quality.run_fingerprint(NS(exclude_shapes=[], engine="builtin"), "abc") == "abc"


@needs_tt
@pytest.mark.parametrize("skolem", [False, True])
def test_same_results_as_the_builtin_validator(tmp_path, skolem):
    st = _store(tmp_path, skolem)
    builtin = _validate(st)
    run = _tt(st, tmp_path)

    def same(results):
        # A SPARQL constraint of a node shape that binds no ?value: TripleTrail gives the focus
        # node as sh:value (the value node of a node shape), atlas.shacl none.
        return {(f, s, c, None if c == "SPARQL" and v == f else v) for f, s, c, v in results}
    assert same(_results(run)) == same(_results(builtin))
    assert dict(run.totals) == dict(builtin.totals)
    nxt = next(r for r in run.results if r.shape.endswith("/Next"))
    assert nxt.path == "^<http://ex/prev>" and nxt.path_ttl == "[ sh:inversePath <http://ex/prev> ]"
    msgs = {r.message for r in run.results if r.focus == "<http://ex/bad>" and r.component.endswith("SPARQLConstraintComponent")}
    assert msgs == {"http://ex/bad links to itself", "No comment"}  # {$this} filled in


@needs_tt
def test_exclusions_and_caps(tmp_path):
    st = _store(tmp_path)
    run = _tt(st, tmp_path, shacl.Limits(exclude=["http://ex/PointShape", "Closed"]))
    assert {r.shape for r in run.results} == {"http://ex/UntypedShape"}
    run = _tt(st, tmp_path, shacl.Limits(exclude=["http://ex/NeedsComment"]))
    assert not any("No comment" in (r.message or "") for r in run.results)
    assert any("links to itself" in (r.message or "") for r in run.results)
    run = _tt(st, tmp_path, shacl.Limits(max_per_constraint=1, max_total=3))
    assert run.kept() == 3 and sum(run.totals.values()) > 3


def test_report_file_is_read_in_parts_with_the_same_outcome(tmp_path):
    sh = "http://www.w3.org/ns/shacl#"
    lines = ["_:rep <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <%sValidationReport> ." % sh]
    for i in range(7):
        r, focus = f"_:r{i}", f"<http://ex/n{i % 5}>"  # r5 and r6 repeat r0 and r1
        lines += [
            f"_:rep <{sh}result> {r} .",
            f"{r} <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{sh}ValidationResult> .",
            f"{r} <{sh}sourceConstraintComponent> <{sh}MaxCountConstraintComponent> .",
            f"{r} <{sh}resultPath> _:p{i} .",
            f"_:p{i} <{sh}inversePath> <http://ex/prev> .",
            f"{r} <{sh}sourceShape> <http://ex/Next> .",
            f"{r} <{sh}focusNode> {focus} .",
        ]
    # the triples of one result are not next to each other in the file
    report = tmp_path / "report.nt"
    report.write_text("\n".join(sorted(lines, key=lambda line: line.split(" ", 2)[1:])) + "\n")

    def read(**kw):
        run = shacl.Run(graph=G)
        tt_shacl.read_report_file(report, lambda t: not t.value.endswith("n4"), shacl.Limits(max_per_constraint=3),
                                  run, **kw)
        return (dict(run.totals), run.focus_with_results,
                sorted((r.focus, r.path, r.path_ttl) for r in run.results))

    whole = shacl.Run(graph=G)
    tt_shacl.read_report(shacl.ShapesGraph(ox.parse(path=str(report), format=ox.RdfFormat.N_TRIPLES)),
                         lambda t: not t.value.endswith("n4"), shacl.Limits(max_per_constraint=3), whole)
    totals, focus, kept = read(chunk=2)
    assert totals == dict(whole.totals) == {shacl.VIOLATION: 4}
    assert focus == whole.focus_with_results == {f"<http://ex/n{i}>" for i in range(4)}
    assert len(kept) == 3 and {k[1:] for k in kept} == {("^<http://ex/prev>", "[ sh:inversePath <http://ex/prev> ]")}
    assert read() == read(chunk=1)
    assert list(tmp_path.iterdir()) == [report]

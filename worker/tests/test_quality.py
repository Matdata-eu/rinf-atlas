"""Data quality layer: the SHACL validator on the mirror (no database, no network)."""
import time

import pyoxigraph as ox
import pytest
from atlas import mirror, quality, shacl

SH = shacl.SH
G = "http://ex/graph/data"
ONTO = "http://ex/graph/ontology"
SHAPES = "http://ex/graph/shapes"

ONTOLOGY = """
@prefix ex: <http://ex/> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Station rdfs:subClassOf ex:Point .
ex:Point rdfs:subClassOf ex:Element .
ex:Concept1 a ex:OpType . ex:Concept2 a ex:OpType .
"""

SHAPES_TTL = """
@prefix ex: <http://ex/> . @prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:PointShape a sh:NodeShape ; sh:targetClass ex:Point ;
  sh:property ex:Name, ex:Length, ex:Kind, ex:Code, ex:Label, ex:Prev, ex:Next, ex:Parts ;
  sh:sparql ex:NoSelfLink, ex:NeedsComment .
ex:Name a sh:PropertyShape ; sh:path ex:name ; sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:string ;
  sh:message "A point needs exactly one name." .
ex:Length a sh:PropertyShape ; sh:path ex:length ; sh:datatype xsd:double ; sh:minInclusive 0 ; sh:maxInclusive 1000 ;
  sh:severity sh:Warning .
ex:Kind a sh:PropertyShape ; sh:path ex:kind ; sh:class ex:OpType ; sh:nodeKind sh:IRI .
ex:Code a sh:PropertyShape ; sh:path ex:code ; sh:pattern "^[A-Z]{2}[0-9]+$" ; sh:maxLength 6 ;
  sh:or ( [ sh:datatype xsd:string ] [ sh:datatype xsd:token ] ) .
ex:Label a sh:PropertyShape ; sh:path rdfs:label ; sh:uniqueLang true ; sh:severity sh:Info .
ex:Prev a sh:PropertyShape ; sh:path ex:prev ; sh:disjoint ex:next .
ex:Next a sh:PropertyShape ; sh:path [ sh:inversePath ex:prev ] ; sh:maxCount 1 .
ex:Parts a sh:PropertyShape ; sh:path ex:hasPart ;
  sh:qualifiedValueShape [ sh:class ex:Station ] ; sh:qualifiedMaxCount 1 .
ex:NoSelfLink a sh:SPARQLConstraint ; sh:message "{$this} links to itself" ;
  sh:prefixes ex:prefixes ;
  sh:select "SELECT $this ?value WHERE { $this ex:prev ?value . FILTER(?value = $this) }" .
ex:NeedsComment a sh:SPARQLConstraint ; sh:message "No comment" ;
  sh:prefixes ex:prefixes ;
  sh:select "SELECT $this WHERE { FILTER NOT EXISTS { $this rdfs:comment ?c } }" .
ex:prefixes sh:declare [ sh:prefix "ex" ; sh:namespace "http://ex/"^^xsd:anyURI ] ,
                       [ sh:prefix "rdfs" ; sh:namespace "http://www.w3.org/2000/01/rdf-schema#"^^xsd:anyURI ] .

ex:UntypedShape a sh:NodeShape ;
  sh:target [ a sh:SPARQLTarget ; sh:prefixes ex:prefixes ;
              sh:select "SELECT ?this WHERE { ?this ex:prev ?x . FILTER NOT EXISTS { ?this a ?t } }" ] ;
  sh:nodeKind sh:BlankNode ; sh:message "Untyped resource" .

ex:ClosedShape a sh:NodeShape ; sh:targetClass ex:Closed ; sh:closed true ;
  sh:ignoredProperties ( <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ) ;
  sh:property [ sh:path ex:name ] .
"""

DATA = """
@prefix ex: <http://ex/> . @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:good a ex:Station ; ex:name "Good" ; ex:length "12.5"^^xsd:double ; ex:kind ex:Concept1 ; ex:code "BE123" ;
  rdfs:label "Good"@en, "Goed"@nl ; rdfs:comment "ok" ; ex:hasPart ex:good .
ex:bad a ex:Point ; ex:name "A", "B" ; ex:length "12"^^xsd:integer, 5000.0 ; ex:kind ex:Nope, "x" ;
  ex:code "be1234567" ; rdfs:label "x"@en, "y"@en ; ex:prev ex:bad, ex:good ; ex:next ex:good ;
  ex:hasPart ex:good, ex:s2 .
ex:s2 a ex:Station ; ex:name "S2" ; rdfs:comment "c" .
ex:other ex:prev ex:good .
ex:c1 a ex:Closed ; ex:name "n" ; ex:extra 1 .
ex:noname a ex:Point .
"""


def _store(tmp_path, skolem_shapes: bool = False) -> ox.Store:
    st = ox.Store()
    st.load(ONTOLOGY.encode(), format=ox.RdfFormat.TURTLE, to_graph=ox.NamedNode(ONTO))
    st.load(DATA.encode(), format=ox.RdfFormat.TURTLE, to_graph=ox.NamedNode(G))
    if skolem_shapes:  # as the worker mirrors it: blank nodes rewritten to urn:atlas:bnode: IRIs
        nt = tmp_path / "shapes.nt"
        nt.write_bytes(ox.serialize(ox.parse(SHAPES_TTL.encode(), format=ox.RdfFormat.TURTLE),
                                    format=ox.RdfFormat.N_TRIPLES))
        mirror.replace_graph(st, SHAPES, nt)
    else:
        st.load(SHAPES_TTL.encode(), format=ox.RdfFormat.TURTLE, to_graph=ox.NamedNode(SHAPES))
    return st


def _results(run):
    return {(r.focus, r.shape.rsplit("/", 1)[-1], r.component[len(SH):].replace("ConstraintComponent", ""),
             r.value) for r in run.results}


def _validate(st, **kw):
    return shacl.validate_graph(st, G, SHAPES, [ONTO], **kw)


@pytest.mark.parametrize("skolem", [False, True])
def test_core_and_sparql_constraints(tmp_path, skolem):
    run = _validate(_store(tmp_path, skolem))
    res = _results(run)
    bad, good = "<http://ex/bad>", "<http://ex/good>"
    expected = {
        (bad, "Name", "MaxCount", None),
        ("<http://ex/noname>", "Name", "MinCount", None),
        (bad, "Length", "Datatype", '"12"^^<http://www.w3.org/2001/XMLSchema#integer>'),
        (bad, "Length", "MaxInclusive", '"5000"^^<http://www.w3.org/2001/XMLSchema#decimal>'),
        (bad, "Length", "Datatype", '"5000"^^<http://www.w3.org/2001/XMLSchema#decimal>'),
        (bad, "Kind", "Class", "<http://ex/Nope>"),
        (bad, "Kind", "Class", '"x"'),
        (bad, "Kind", "NodeKind", '"x"'),
        (bad, "Code", "Pattern", '"be1234567"'),
        (bad, "Code", "MaxLength", '"be1234567"'),
        (bad, "Label", "UniqueLang", None),
        (bad, "Prev", "Disjoint", good),
        (good, "Next", "MaxCount", None),  # ex:bad and ex:other both point to it (inverse path)
        (bad, "Parts", "QualifiedMaxCount", None),
        (bad, "PointShape", "SPARQL", bad),  # $this bound by a triple pattern: runs unbound
        (bad, "PointShape", "SPARQL", None),  # $this only inside NOT EXISTS: pre-bound with VALUES
        ("<http://ex/noname>", "PointShape", "SPARQL", None),
        ("<http://ex/other>", "UntypedShape", "NodeKind", "<http://ex/other>"),  # SPARQL target
        ("<http://ex/c1>", "ClosedShape", "Closed", '"1"^^<http://www.w3.org/2001/XMLSchema#integer>'),
    }
    assert expected <= res, expected - res
    # nothing on the conforming station, and the subclass target reached it
    assert not [r for r in res if r[0] == good and r[1] not in ("Next",)]
    assert {r[0] for r in res} <= {bad, good, "<http://ex/noname>", "<http://ex/other>", "<http://ex/c1>"}
    assert not run.unsupported and not run.partial
    msgs = {r.message for r in run.results if r.value == bad and r.component.endswith("SPARQLConstraintComponent")}
    assert msgs == {"http://ex/bad links to itself"}
    assert run.totals[shacl.WARNING] == 3 and run.totals[shacl.INFO] == 1
    nxt = next(r for r in run.results if r.shape.endswith("/Next"))
    assert nxt.path == "^<http://ex/prev>" and nxt.path_ttl == "[ sh:inversePath <http://ex/prev> ]"


def test_scratch_store_gives_the_same_results(tmp_path):
    base = _results(_validate(_store(tmp_path)))
    assert _results(_validate(_store(tmp_path), scratch="")) == base
    # on disk: SPARQL runs in a child process on a copy (killable on timeout)
    st = ox.Store(str(tmp_path / "mirror"))
    for g, ttl in ((ONTO, ONTOLOGY), (G, DATA), (SHAPES, SHAPES_TTL)):
        st.load(ttl.encode(), format=ox.RdfFormat.TURTLE, to_graph=ox.NamedNode(g))
    scratch = tmp_path / "scratch"
    assert _results(_validate(st, scratch=str(scratch))) == base
    assert not scratch.exists()


def test_limits_cap_results_and_deadline(tmp_path):
    st = _store(tmp_path)
    run = _validate(st, limits=shacl.Limits(max_per_constraint=1, max_total=3))
    assert run.kept() == 3 and sum(run.totals.values()) > 3
    assert "results kept" in run.note()
    late = _validate(st, limits=shacl.Limits(deadline=time.time() - 1))
    assert late.partial and late.node_shapes_checked == 0 and "time budget" in late.note()


def test_excluded_shapes(tmp_path):
    run = _validate(_store(tmp_path), limits=shacl.Limits(exclude=["http://ex/PointShape", "Closed"]))
    assert {r.shape for r in run.results} == {"http://ex/UntypedShape"}


def test_excluded_sparql_constraint(tmp_path):
    st = _store(tmp_path)
    full = _validate(st)
    run = _validate(st, limits=shacl.Limits(exclude=["http://ex/NeedsComment"]))
    assert any("No comment" in (r.message or "") for r in full.results)
    assert not any("No comment" in (r.message or "") for r in run.results)
    # the other constraints of the same node shape still run
    assert any("links to itself" in (r.message or "") for r in run.results)
    assert run.excluded == {"http://ex/NeedsComment"} and "NeedsComment" in run.note()


def test_run_fingerprint_follows_exclusions():
    from types import SimpleNamespace as NS
    cfg = lambda ex: NS(quality=NS(exclude_shapes=ex))  # noqa: E731
    assert quality.run_fingerprint(cfg([]), "abc") == "abc"
    assert quality.run_fingerprint(cfg(["x"]), None) is None
    a, b = quality.run_fingerprint(cfg(["x", "y"]), "abc"), quality.run_fingerprint(cfg(["y", "x"]), "abc")
    assert a == b and a != "abc" and a != quality.run_fingerprint(cfg(["x"]), "abc")


def test_report_is_a_standard_validation_report(tmp_path):
    run = _validate(_store(tmp_path))
    ttl = shacl.report_turtle(run, report_iri="urn:x:report", created="2026-10-14T00:00:00Z", about="test")
    st = ox.Store()
    st.load(ttl.encode(), format=ox.RdfFormat.TURTLE)
    q = lambda s: st.query("PREFIX sh: <http://www.w3.org/ns/shacl#> " + s)  # noqa: E731
    assert bool(q("ASK { ?r a sh:ValidationReport ; sh:conforms false }"))
    assert len(list(q("SELECT ?x WHERE { ?r sh:result ?x . ?x a sh:ValidationResult ; sh:focusNode ?f ; "
                 "sh:resultSeverity ?s ; sh:sourceShape ?sh ; sh:sourceConstraintComponent ?c }"))) == len(run.results)
    assert bool(q("ASK { ?x sh:resultPath [ sh:inversePath <http://ex/prev> ] }"))
    clean = shacl.Run(graph=G)
    assert "sh:conforms true" in shacl.report_turtle(clean, report_iri="urn:x", created="2026-10-14T00:00:00Z")


def test_remote_query_timeout(tmp_path):
    st = ox.Store(str(tmp_path / "s"))
    st.extend(ox.Quad(ox.NamedNode(f"http://ex/s{i}"), ox.NamedNode("http://ex/p"), ox.Literal(str(i)))
              for i in range(3000))
    st.flush()
    del st
    rq = shacl.RemoteQueries(str(tmp_path / "s"))
    try:
        names, rows = rq.query("SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }", 30)
        assert names == ["n"] and shacl._unpack(rows[0][0]).value == "3000"
        with pytest.raises(shacl.QueryTimeout):
            rq.query("SELECT (COUNT(*) AS ?n) WHERE { ?a ?b ?c . ?d ?e ?f . ?g ?h ?i }", 0.5)
        with pytest.raises(SyntaxError):
            rq.query("SELECT nonsense", 30)
        assert len(rq.query('SELECT ?s WHERE { ?s ?p "7" }', 30)[1]) == 1  # restarted after the kill
    finally:
        rq.close()


def test_binds_this_heuristic():
    assert shacl._binds_this("SELECT $this WHERE { $this a <x> . FILTER NOT EXISTS { $this <p> ?v } }")
    assert not shacl._binds_this("SELECT $this WHERE { FILTER NOT EXISTS { $this <p> ?v } }")
    assert not shacl._binds_this("SELECT $this WHERE { MINUS { $this <http://x#p> ?v } } # $this here")


def test_shapes_turtle_restores_blank_nodes(tmp_path):
    st = _store(tmp_path, skolem_shapes=True)
    ttl = quality.shapes_turtle(st, SHAPES).decode()
    assert "urn:atlas:bnode" not in ttl
    g = ox.Store()
    g.load(ttl.encode(), format=ox.RdfFormat.TURTLE)
    assert bool(g.query("PREFIX sh: <http://www.w3.org/ns/shacl#> ASK { <http://ex/Next> sh:path [ sh:inversePath <http://ex/prev> ] }"))
    assert quality.shapes_fingerprint(st, SHAPES) == quality.shapes_fingerprint(st, SHAPES)
    assert quality.shapes_fingerprint(st, "http://ex/none") is None

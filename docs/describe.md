# Describe index

An optional index for [RINF Describe](https://github.com/Matdata-eu/rinf-describe),
which shows any RINF resource as a page laid out by SHACL DASH shapes. It is **off by
default**; with it off, the worker indexes exactly as before and none of the tables
below are written.

When on, every provider graph the worker (re)indexes also gets:

| Table | One row per | Holds |
|---|---|---|
| `describe_triple` | triple | subject, predicate, object (IRI or literal with datatype / language), graph; indexed on subject and on IRI objects for incoming links |
| `describe_doc` | resource × describe shape | the evaluated page as `jsonb`: each row of the shape, summaries over child resources, linked resources as tables, data status |
| `describe_def` | describe shape | the compiled layout (groups, rows, viewers) for the app |

Labels of the IRIs a page shows go to Atlas's `term` table, like facet values.
Nothing in Atlas's API or UI reads the describe tables.

## Switching it on

```sh
# .env
ATLAS_DESCRIBE_SHAPES_DIR=../rinf-describe/shapes

docker compose -f docker-compose.yml -f docker-compose.describe.yml up -d
```

Outside Docker: `ATLAS_DESCRIBE=1` and `ATLAS_DESCRIBE_SHAPES=<folder>`, or
`describe: {enabled: true}` in `config/environments.yaml`.

The first poll after switching it on builds the describe rows of every loaded graph
from the mirror (no download). After that, a graph's rows are replaced whenever the
graph is synced. When the describe shapes change, the documents are rebuilt from the
mirror on the next poll; `atlas describe-reindex [--env E] [--triples]` does it on
demand. Invalid describe shapes are logged and leave the describe index as it was;
Atlas's own index carries on. A failure while describing one graph never fails the
graph's Atlas indexing.

`atlas check-shapes -v` lists the compiled describe shapes too when the index is on.

## Shapes

Standard SHACL and DASH, plus a few `rd:` terms
(`https://rinf-describe.matdata.eu/ns#`); the vocabulary is documented in RINF
Describe and at the top of [`worker/atlas/describe.py`](../worker/atlas/describe.py).
One `sh:NodeShape` with `sh:targetClass` per class page:

```turtle
ex:section-of-line a sh:NodeShape ;
  sh:targetClass era:SectionOfLine ;
  sh:property [ sh:path era:maximumPermittedSpeed ; sh:name "Maximum permitted speed" ;
                rd:over [ sh:inversePath era:isPartOf ] ; rd:aggregate "range" ; rd:unit "km/h" ;
                sh:group ex:TrackCharacteristics ; dash:viewer rd:AggregateViewer ] .
```

What the worker evaluates per row:

| Row | Stored in the document |
|---|---|
| `sh:path` | `values`: the values of the path |
| `sh:path` + `rd:over` + `rd:aggregate` | `aggregate`: `range` (min, max), `distinct-count` (each value with the number of children having it) or `count` over the children `rd:over` reaches |
| `sh:path` + `sh:node` | `items`: one entry per linked resource with a cell per property of the node shape |
| other `rd:` paths (`rd:start`, `rd:end`, ...) | `params`: the values of each, for the row's viewer |

Every document also carries `dataStatus`: the parameters a resource marks
`era:notApplicable` or `era:notYetAvailable`. Paths are evaluated within the
resource's own named graph.

## Size

`describe_triple` holds a copy of every provider triple, so it is the large one:
expect it to be of the order of the mirror's size in PostgreSQL. Try it with
`ATLAS_ONLY_GRAPHS` on one country first.

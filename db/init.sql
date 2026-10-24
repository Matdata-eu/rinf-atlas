-- RINF Atlas schema. Applied by the postgis image on first start; the worker
-- also runs it on every start (idempotent) so upgrades pick up new objects.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- One row per named graph per environment: provider datasets (kind
-- 'provider') and the vocabulary graphs kept in the mirror (kind 'vocab').
CREATE TABLE IF NOT EXISTS dataset (
    env          text        NOT NULL,
    graph_iri    text        NOT NULL,
    kind         text        NOT NULL DEFAULT 'provider',
    title        text,
    provider     text,
    country      text,
    modified     timestamptz,           -- datePublished reported by ERA
    fingerprint  text,                  -- dataset id | datePublished | triple count
    triples      bigint,
    status       text        NOT NULL DEFAULT 'pending', -- pending|loading|loaded|failed|removed
    error        text,
    ingested_at  timestamptz,
    PRIMARY KEY (env, graph_iri)
);
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'provider';
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS dataset_id text;
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS file_name text;

-- Data quality: outcome of validating the dataset against ERA's SHACL shapes
-- (worker/atlas/quality.py). qa_status: null = not validated yet, ok, partial
-- (time budget reached), skipped (too large / no shapes), failed.
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_status     text;
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_violations bigint;
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_warnings   bigint;
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_infos      bigint;
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_focus      bigint;      -- focus nodes with at least one result
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_kept       bigint;      -- results stored in `violation`
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_checked_at timestamptz;
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_shapes     text;        -- fingerprint of the shapes graph used
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_seconds    real;
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_note       text;
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_source     jsonb;       -- the shapes used: kind, label, tag, version, url…
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_reason     text;        -- why skipped: too_large, no_shapes
ALTER TABLE dataset ADD COLUMN IF NOT EXISTS qa_limit      bigint;      -- the size limit a too_large graph exceeded

-- One row per validation result (capped per constraint and per dataset; the
-- counts in dataset / quality_count are complete).
CREATE TABLE IF NOT EXISTS violation (
    env        text     NOT NULL,
    graph_iri  text     NOT NULL,
    focus      text     NOT NULL,       -- focus node IRI
    severity   smallint NOT NULL,       -- 3 violation, 2 warning, 1 info
    shape      text     NOT NULL,       -- sh:sourceShape
    component  text     NOT NULL,       -- sh:sourceConstraintComponent
    path       text,                    -- SPARQL property path
    value      text,                    -- offending value, N-Triples syntax
    message    text
);
CREATE INDEX IF NOT EXISTS violation_focus_idx ON violation (env, focus);
CREATE INDEX IF NOT EXISTS violation_graph_idx ON violation (env, graph_iri);

-- Results per shape and constraint for each dataset (all of them, also those not stored).
CREATE TABLE IF NOT EXISTS quality_count (
    env        text     NOT NULL,
    graph_iri  text     NOT NULL,
    shape      text     NOT NULL,
    component  text     NOT NULL,
    severity   smallint NOT NULL,
    n          bigint   NOT NULL,
    message    text
);
CREATE INDEX IF NOT EXISTS quality_count_idx ON quality_count (env, graph_iri);

-- The sh:ValidationReport of each dataset as gzipped Turtle and, when the
-- shacl-report CLI is available to the worker, its self-contained HTML viewer.
CREATE TABLE IF NOT EXISTS quality_report (
    env        text  NOT NULL,
    graph_iri  text  NOT NULL,
    turtle_gz  bytea,
    html_gz    bytea,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (env, graph_iri)
);

-- ERA's shapes graph as validated with, for download next to the reports.
CREATE TABLE IF NOT EXISTS quality_shapes (
    env         text PRIMARY KEY,
    graph_iri   text NOT NULL,
    fingerprint text,
    triples     bigint,
    turtle_gz   bytea,
    loaded_at   timestamptz NOT NULL DEFAULT now()
);
-- Where the current shapes come from (graph or GitLab tag, version), the size limit in
-- force, and why the configured shapes could not be loaded (e.g. an unknown tag).
ALTER TABLE quality_shapes ADD COLUMN IF NOT EXISTS source      jsonb;
ALTER TABLE quality_shapes ADD COLUMN IF NOT EXISTS max_triples bigint;
ALTER TABLE quality_shapes ADD COLUMN IF NOT EXISTS error       text;

-- Every indexed infrastructure element. facets holds {facet_key: [values]}.
CREATE TABLE IF NOT EXISTS feature (
    id          bigserial   PRIMARY KEY,
    env         text        NOT NULL,
    iri         text        NOT NULL,
    class       text        NOT NULL,   -- shape key, e.g. operational-point
    class_iri   text        NOT NULL,
    graph_iri   text        NOT NULL,
    label       text,
    geom        geometry(Geometry, 4326),
    geom_source text,                   -- path|line|from (how geom was found)
    facets      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    search      tsvector,
    UNIQUE (env, iri, class)
);
ALTER TABLE feature ADD COLUMN IF NOT EXISTS geom_source text;
ALTER TABLE feature ADD COLUMN IF NOT EXISTS geom3857 geometry(Geometry, 3857);
CREATE INDEX IF NOT EXISTS feature_env_class_idx ON feature (env, class);
CREATE INDEX IF NOT EXISTS feature_env_iri_idx   ON feature (env, iri);
CREATE INDEX IF NOT EXISTS feature_graph_idx     ON feature (env, graph_iri);
CREATE INDEX IF NOT EXISTS feature_geom_idx      ON feature USING gist (geom);
CREATE INDEX IF NOT EXISTS feature_geom3857_idx  ON feature USING gist (geom3857);
CREATE INDEX IF NOT EXISTS feature_facets_idx    ON feature USING gin (facets jsonb_path_ops);
CREATE INDEX IF NOT EXISTS feature_search_idx    ON feature USING gin (search);
CREATE INDEX IF NOT EXISTS feature_label_trgm    ON feature USING gin (label gin_trgm_ops);

-- Links found by atlas:geometryFrom / atlas:lineBetween, used to derive
-- geometry for features that have none of their own (tracks, platforms...).
CREATE TABLE IF NOT EXISTS feature_link (
    env        text NOT NULL,
    graph_iri  text NOT NULL,
    iri        text NOT NULL,
    class      text NOT NULL,
    rule       text NOT NULL,           -- from | lineA | lineB
    target     text NOT NULL
);
CREATE INDEX IF NOT EXISTS feature_link_idx   ON feature_link (env, iri, class);
CREATE INDEX IF NOT EXISTS feature_link_g_idx ON feature_link (env, graph_iri);
-- "Part of" lookups from the whole (a section of line) to its parts (its tracks):
-- map colouring roll-up, vehicle compatibility and the line profile.
CREATE INDEX IF NOT EXISTS feature_link_target_idx ON feature_link (env, target) WHERE rule = 'from';

-- Labels for IRI-valued facet values (SKOS concepts, organisations, countries...).
CREATE TABLE IF NOT EXISTS term (
    env    text NOT NULL,
    iri    text NOT NULL,
    label  text NOT NULL,
    PRIMARY KEY (env, iri)
);

-- Compiled facet definitions, written by the worker from shapes/*.ttl so the
-- API and frontend never parse SHACL themselves.
CREATE TABLE IF NOT EXISTS facet_def (
    key        text    NOT NULL,       -- facet key used in feature.facets
    class      text    NOT NULL,       -- shape key, '*' for shared facets
    label      text    NOT NULL,
    kind       text    NOT NULL,       -- terms|range|boolean|text
    datatype   text,
    position   int     NOT NULL DEFAULT 100,
    PRIMARY KEY (class, key)
);
ALTER TABLE facet_def ADD COLUMN IF NOT EXISTS path text;
ALTER TABLE facet_def ADD COLUMN IF NOT EXISTS unit text;
-- atlas:colorBy: suggested in the map's "Colour by" picker, lower ranks first.
ALTER TABLE facet_def ADD COLUMN IF NOT EXISTS color_by int;

CREATE TABLE IF NOT EXISTS class_def (
    class      text PRIMARY KEY,       -- shape key
    class_iri  text NOT NULL,
    label      text NOT NULL,
    color      text,
    geometry   text,                   -- point|line|any (rendering hint)
    position   int  NOT NULL DEFAULT 100
);
ALTER TABLE class_def ADD COLUMN IF NOT EXISTS min_zoom int NOT NULL DEFAULT 0;
-- The shapes/ file the class is defined in, and how its position is found
-- (atlas:geometryPath / lineBetween / geometryFrom as compact SPARQL paths).
ALTER TABLE class_def ADD COLUMN IF NOT EXISTS shape_file text;
ALTER TABLE class_def ADD COLUMN IF NOT EXISTS position_rules jsonb;
-- Map draw order within points, lines and areas (atlas:drawOrder): higher on top.
ALTER TABLE class_def ADD COLUMN IF NOT EXISTS draw_order int;

-- The shapes/ files as written, so the app can show the implementer's shapes.
CREATE TABLE IF NOT EXISTS shape_file (
    name   text PRIMARY KEY,           -- path relative to shapes/
    turtle text NOT NULL
);

-- Vehicle compatibility rules compiled from shapes/compatibility.ttl (one row,
-- JSON of atlas.shapes.CompatSet), written by the worker with facet_def.
CREATE TABLE IF NOT EXISTS compat_def (
    id   int   PRIMARY KEY DEFAULT 1,
    def  jsonb NOT NULL
);

-- Hash of the compiled shapes the features were last indexed with, per env.
CREATE TABLE IF NOT EXISTS index_state (
    env         text PRIMARY KEY,
    shapes_hash text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Work requested from the CLI while the poll loop owns the mirror.
CREATE TABLE IF NOT EXISTS job (
    id          bigserial PRIMARY KEY,
    env         text,                   -- null = every environment
    kind        text NOT NULL,          -- sync|reindex|resync
    created_at  timestamptz NOT NULL DEFAULT now(),
    started_at  timestamptz,
    finished_at timestamptz,
    message     text
);

CREATE TABLE IF NOT EXISTS ingest_run (
    id          bigserial PRIMARY KEY,
    env         text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    new_graphs  int,
    changed     int,
    removed     int,
    message     text
);

-- ST_GeomFromText that returns NULL instead of failing on bad WKT.
CREATE OR REPLACE FUNCTION atlas_geom(wkt text) RETURNS geometry
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE g geometry;
BEGIN
    IF wkt IS NULL OR wkt = '' THEN RETURN NULL; END IF;
    g := ST_GeomFromText(wkt, 4326);
    IF NOT ST_IsValid(g) THEN g := ST_MakeValid(g); END IF;
    IF ST_IsEmpty(g) THEN RETURN NULL; END IF;
    RETURN g;
EXCEPTION WHEN others THEN
    RETURN NULL;
END $$;

-- Facet value counts per class without any filter, refreshed by the worker
-- after every change. Lets the API answer the unfiltered state instantly.
CREATE TABLE IF NOT EXISTS facet_cache (
    env    text  NOT NULL,
    class  text  NOT NULL,
    key    text  NOT NULL,
    value  jsonb NOT NULL,
    n      bigint NOT NULL
);
CREATE INDEX IF NOT EXISTS facet_cache_idx ON facet_cache (env, key);

-- Map colouring ("Colour by" a facet). One theme per environment and facet key,
-- computed by the API the first time someone colours by that facet and again
-- when the data changes (the worker empties it after every update). theme_value
-- holds one display value per feature: its own facet value, or for a section of
-- line coloured by a facet of its tracks, the values rolled up from its parts.
-- Unlogged: it is a cache and can always be recomputed.
CREATE UNLOGGED TABLE IF NOT EXISTS theme (
    id        serial      PRIMARY KEY,
    env       text        NOT NULL,
    key       text        NOT NULL,     -- facet key as in a query state, e.g. running-track/maximumPermittedSpeed
    version   text        NOT NULL,     -- data version it was computed for
    kind      text        NOT NULL,     -- terms|boolean|range
    classes   text[]      NOT NULL,     -- classes coloured by their own values
    rollup    text[]      NOT NULL,     -- line classes coloured by the values of their parts
    slots     jsonb,                    -- terms: the values that get a palette colour, in order
    breaks    jsonb,                    -- range: {"breaks": [class boundaries], "discrete": one class per value}
    lo        double precision,
    hi        double precision,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (env, key)
);
CREATE UNLOGGED TABLE IF NOT EXISTS theme_value (
    theme  int    NOT NULL REFERENCES theme (id) ON DELETE CASCADE,
    fid    bigint NOT NULL,             -- feature.id
    v      text,                        -- terms/boolean: the value, '__mixed__' when it has several
    n      double precision,            -- range: the highest value
    PRIMARY KEY (theme, fid)
);

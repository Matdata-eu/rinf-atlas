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

CREATE TABLE IF NOT EXISTS class_def (
    class      text PRIMARY KEY,       -- shape key
    class_iri  text NOT NULL,
    label      text NOT NULL,
    color      text,
    geometry   text,                   -- point|line|any (rendering hint)
    position   int  NOT NULL DEFAULT 100
);
ALTER TABLE class_def ADD COLUMN IF NOT EXISTS min_zoom int NOT NULL DEFAULT 0;

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

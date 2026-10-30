"""Settings from environment variables and config/environments.yaml."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass
class Env:
    key: str
    label: str
    sparql: str
    datasets_api: str | None = None
    portal: str | None = None
    default: bool = False
    vocab_graphs: list[str] = field(default_factory=list)
    include_graphs: list[str] = field(default_factory=list)
    exclude_graphs: list[str] = field(default_factory=list)
    # Data quality settings of this environment: the global `quality:` block with the
    # environment's own `quality:` block (and the ATLAS_QUALITY_* overrides) on top.
    quality: Quality | None = None


SLOW_SHAPES = (
    "http://data.europa.eu/949/shapes/LinesideDistanceIndicationRequiredForSoL",
    "http://data.europa.eu/949/shapes/MaxPermittedTrainLengthApplicability",
)


@dataclass
class Quality:
    """Validation of provider graphs against ERA's SHACL shapes (config `quality:`)."""
    enabled: bool = True
    # Where the shapes come from: the mirrored `shapes_graph` (default), or, when
    # `shapes_tag` is set, that tag of ERA's ontology repository on GitLab: the first of
    # `shapes_files` that exists at the tag is downloaded once and cached.
    shapes_graph: str = "http://data.europa.eu/949/graph/rinf/shacl"
    shapes_tag: str = ""
    shapes_repo: str = "era-europa-eu/public/interoperable-data-programme/era-ontology/era-ontology"
    shapes_files: list[str] = field(default_factory=lambda: ["era-shacl/ERA-RINF-shapes.ttl", "era-shapes.ttl"])
    gitlab_url: str = "https://gitlab.com"
    max_triples: int = 15_000_000
    time_budget_minutes: float = 60
    max_results_per_constraint: int = 10_000
    max_results_per_graph: int = 200_000
    html_max_results: int = 20_000
    # Left out unless configured otherwise: two ERA warnings whose SPARQL takes minutes per
    # graph (see config/environments.yaml and docs/DESIGN.md#data-quality).
    exclude_shapes: list[str] = field(default_factory=lambda: list(SLOW_SHAPES))
    shacl_report_bin: str = "shacl-report"


@dataclass
class Settings:
    envs: dict[str, Env]
    provider_graph_pattern: str
    poll_interval_minutes: float
    database_url: str
    shapes_dir: Path
    mirror_dir: Path
    tmp_dir: Path
    quality: Quality = field(default_factory=Quality)
    shapes_cache: Path = Path("/data/shapes-cache")
    # Describe index for RINF Describe (worker/atlas/describe.py): off unless ATLAS_DESCRIBE is set.
    describe: bool = False
    describe_shapes_dir: Path = Path("/app/describe-shapes")

    def env(self, key: str) -> Env:
        if key not in self.envs:
            raise KeyError(f"unknown environment {key!r}; known: {', '.join(self.envs)}")
        return self.envs[key]

    def is_provider_graph(self, env: Env, iri: str) -> bool:
        if not re.search(self.provider_graph_pattern, iri):
            return False
        if any(re.search(p, iri) for p in env.exclude_graphs):
            return False
        only = os.environ.get("ATLAS_ONLY_GRAPHS", "").strip()
        include = [s.strip() for s in only.split(",") if s.strip()] if only else env.include_graphs
        if include:
            code = iri.rstrip("/").rsplit("/", 1)[-1]
            return any(p == iri or p == code or (len(p) > 4 and re.search(p, iri)) for p in include)
        return True


@lru_cache
def settings() -> Settings:
    cfg_path = Path(os.environ.get("ATLAS_CONFIG", "/app/config/environments.yaml"))
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    global_excl = raw.get("exclude_graphs") or []
    envs = {}
    for key, e in (raw.get("environments") or {}).items():
        envs[key] = Env(
            key=key,
            label=e.get("label", key.upper()),
            sparql=e["sparql"],
            datasets_api=e.get("datasets_api"),
            portal=e.get("portal"),
            default=bool(e.get("default", False)),
            vocab_graphs=list(e.get("vocab_graphs") or []),
            include_graphs=list(e.get("include_graphs") or []),
            exclude_graphs=list(e.get("exclude_graphs") or []) + list(global_excl),
        )
    enabled = os.environ.get("ATLAS_ENVS", "").strip()
    if enabled:
        wanted = [s.strip().lower() for s in enabled.split(",") if s.strip()]
        unknown = [w for w in wanted if w not in {k.lower() for k in envs}]
        if unknown:
            raise SystemExit(f"ATLAS_ENVS: unknown environment(s) {', '.join(unknown)}; "
                             f"known: {', '.join(envs)} (see config/environments.yaml)")
        envs = {k: v for k, v in envs.items() if k.lower() in wanted}
    q = raw.get("quality") or {}
    quality = _quality(q, None)
    env_blocks = raw.get("environments") or {}
    for key, env in envs.items():
        env.quality = _quality({**q, **(env_blocks.get(key, {}).get("quality") or {})}, key)
        env.quality.enabled = env.quality.enabled and quality.enabled
    return Settings(
        envs=envs,
        provider_graph_pattern=raw.get("provider_graph_pattern", r"^http://data\.europa\.eu/949/graph/[A-Z0-9]{4}$"),
        poll_interval_minutes=float(os.environ.get("ATLAS_POLL_MINUTES", raw.get("poll_interval_minutes", 30))),
        database_url=os.environ.get("DATABASE_URL", "postgresql://atlas:atlas@db:5432/atlas"),
        shapes_dir=Path(os.environ.get("ATLAS_SHAPES", "/app/shapes")),
        mirror_dir=Path(os.environ.get("ATLAS_MIRROR", "/data/mirror")),
        tmp_dir=Path(os.environ.get("ATLAS_TMP", "/data/tmp")),
        quality=quality,
        shapes_cache=Path(os.environ.get("ATLAS_SHAPES_CACHE", "/data/shapes-cache")),
        describe=_flag(os.environ.get("ATLAS_DESCRIBE"), bool((raw.get("describe") or {}).get("enabled", False))),
        describe_shapes_dir=Path(os.environ.get("ATLAS_DESCRIBE_SHAPES", "/app/describe-shapes")),
    )


def _quality(q: dict, env: str | None) -> Quality:
    """Quality settings from a merged `quality:` block, with the environment variables on top.

    ATLAS_QUALITY_MAX_TRIPLES, ATLAS_QUALITY_MINUTES and ATLAS_QUALITY_SHAPES_TAG take either one
    value for every environment or `prod=…,uat=…` pairs."""
    d = Quality()
    max_triples = _env_value("ATLAS_QUALITY_MAX_TRIPLES", env)
    minutes = _env_value("ATLAS_QUALITY_MINUTES", env)
    tag = _env_value("ATLAS_QUALITY_SHAPES_TAG", env)
    return replace(
        d,
        enabled=_flag(os.environ.get("ATLAS_QUALITY"), bool(q.get("enabled", True))),
        shapes_graph=q.get("shapes_graph") or d.shapes_graph,
        shapes_tag=str(tag if tag is not None else (q.get("shapes_tag") or "")).strip(),
        shapes_repo=q.get("shapes_repo") or d.shapes_repo,
        shapes_files=list(q.get("shapes_files") or d.shapes_files),
        gitlab_url=(q.get("gitlab_url") or d.gitlab_url).rstrip("/"),
        max_triples=int(float(max_triples or q.get("max_triples", d.max_triples))),
        time_budget_minutes=float(minutes or q.get("time_budget_minutes", d.time_budget_minutes)),
        max_results_per_constraint=int(q.get("max_results_per_constraint", d.max_results_per_constraint)),
        max_results_per_graph=int(q.get("max_results_per_graph", d.max_results_per_graph)),
        html_max_results=int(q.get("html_max_results", d.html_max_results)),
        exclude_shapes=list(SLOW_SHAPES if "exclude_shapes" not in q else q["exclude_shapes"] or []),
        shacl_report_bin=os.environ.get("ATLAS_SHACL_REPORT_BIN", "shacl-report"),
    )


def _env_value(name: str, env: str | None) -> str | None:
    """`VALUE` (every environment) or `prod=VALUE,uat=VALUE`; None when unset for `env`."""
    v = os.environ.get(name, "").strip()
    if not v:
        return None
    if "=" not in v:
        return v
    pairs = dict(p.split("=", 1) for p in v.split(",") if "=" in p)
    return pairs.get(env).strip() if env and pairs.get(env) is not None else None


def _flag(v: str | None, default: bool) -> bool:
    if v is None or not v.strip():
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")

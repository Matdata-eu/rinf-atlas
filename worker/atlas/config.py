"""Settings from environment variables and config/environments.yaml."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
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


@dataclass
class Settings:
    envs: dict[str, Env]
    provider_graph_pattern: str
    poll_interval_minutes: float
    database_url: str
    shapes_dir: Path
    mirror_dir: Path
    tmp_dir: Path

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
    return Settings(
        envs=envs,
        provider_graph_pattern=raw.get("provider_graph_pattern", r"^http://data\.europa\.eu/949/graph/[A-Z0-9]{4}$"),
        poll_interval_minutes=float(os.environ.get("ATLAS_POLL_MINUTES", raw.get("poll_interval_minutes", 30))),
        database_url=os.environ.get("DATABASE_URL", "postgresql://atlas:atlas@db:5432/atlas"),
        shapes_dir=Path(os.environ.get("ATLAS_SHAPES", "/app/shapes")),
        mirror_dir=Path(os.environ.get("ATLAS_MIRROR", "/data/mirror")),
        tmp_dir=Path(os.environ.get("ATLAS_TMP", "/data/tmp")),
    )

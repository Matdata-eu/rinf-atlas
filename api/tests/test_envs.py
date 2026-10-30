"""/api/envs "Full describe" template, no database: `python -m pytest api/tests`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main  # noqa: E402

TEMPLATE = "https://describe.example/describe?uri={iri}&env={env}"
ENVS = {"prod": {"label": "PROD", "sparql": "https://p/sparql", "default": True},
        "uat": {"sparql": "https://u/sparql", "describe_url": ""},
        "test": {"sparql": "https://t/sparql", "describe_url": "https://other.example/{iri}"}}


def listed(monkeypatch, global_url):
    monkeypatch.setattr(main, "DESCRIBE_URL", global_url)
    monkeypatch.setattr(main, "_envs", ENVS)
    return {e["key"]: e["describe_url"] for e in main.envs()}


def test_no_template_by_default(monkeypatch):
    assert listed(monkeypatch, None) == {"prod": None, "uat": None, "test": "https://other.example/{iri}"}


def test_environment_overrides_the_global_template(monkeypatch):
    # uat's empty describe_url hides the link there only.
    assert listed(monkeypatch, TEMPLATE) == {"prod": TEMPLATE, "uat": None, "test": "https://other.example/{iri}"}

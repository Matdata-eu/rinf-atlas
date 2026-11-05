"""shacl-report's HTML viewer in the CSP sandbox (#96), no database: `python -m pytest api/tests`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sandbox import STORAGE_SHIM, with_storage_shim  # noqa: E402


def test_shim_goes_first_in_head():
    page = b'<!doctype html>\n<html><HEAD lang="en">\n<meta charset="utf-8"><script>localStorage</script></head></html>'
    out = with_storage_shim(page)
    assert out.startswith(b'<!doctype html>\n<html><HEAD lang="en">' + STORAGE_SHIM + b"\n<meta")
    assert out.count(STORAGE_SHIM) == 1


def test_shim_without_head_is_prepended():
    assert with_storage_shim(b"<p>hi</p>") == STORAGE_SHIM + b"<p>hi</p>"


def test_header_is_not_mistaken_for_head():
    page = b"<html><header>x</header><head></head>"
    assert with_storage_shim(page) == b"<html><header>x</header><head>" + STORAGE_SHIM + b"</head>"

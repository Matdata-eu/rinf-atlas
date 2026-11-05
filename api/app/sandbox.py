"""Make shacl-report's HTML viewer work inside the API's CSP sandbox.

The report page is served with `Content-Security-Policy: sandbox allow-scripts ...` and
without `allow-same-origin`, because it is generated from data published by others and must
not run with the Atlas origin's privileges. A sandboxed page has an opaque origin, so merely
reading `window.localStorage` throws a SecurityError. shacl-report's SPARQL tab (YASGUI)
persists its tabs there and fails to load (issue #96).

Rather than lift the sandbox, the page gets a tiny script first in <head> that swaps in an
in-memory Storage when the real one is unreachable: YASGUI works, nothing is persisted, and the
page keeps its opaque origin. Where storage works (e.g. a downloaded copy opened locally) the
script does nothing.
"""
from __future__ import annotations

import re

STORAGE_SHIM = b"""<script>/* rinf-atlas: in-memory storage for the sandboxed report (#96) */
(function () {
  function MemoryStorage() { var d = Object.create(null), s = this;
    Object.defineProperty(s, 'length', { get: function () { return Object.keys(d).length; } });
    s.key = function (i) { var k = Object.keys(d); return i >= 0 && i < k.length ? k[i] : null; };
    s.getItem = function (k) { k = String(k); return k in d ? d[k] : null; };
    s.setItem = function (k, v) { d[String(k)] = String(v); };
    s.removeItem = function (k) { delete d[String(k)]; };
    s.clear = function () { d = Object.create(null); }; }
  ['localStorage', 'sessionStorage'].forEach(function (name) {
    try { if (window[name]) return; } catch (e) { /* sandboxed: opaque origin */ }
    try { Object.defineProperty(window, name, { value: new MemoryStorage(), configurable: true }); } catch (e) { /* keep going */ }
  });
})();
</script>"""

_HEAD = re.compile(rb"<head\b[^>]*>", re.IGNORECASE)


def with_storage_shim(html: bytes) -> bytes:
    """The page with STORAGE_SHIM as the first thing in <head> (or at the very top if it has none)."""
    m = _HEAD.search(html, 0, 64 * 1024)
    if m:
        return html[:m.end()] + STORAGE_SHIM + html[m.end():]
    return STORAGE_SHIM + html

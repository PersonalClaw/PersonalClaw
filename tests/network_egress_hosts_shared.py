"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_network_egress_hosts.py``

Its checks:
``tests/rails/test_network_egress_hosts_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


_CORE = _ROOT / "src" / "personalclaw"


_TABLE = _ROOT / "docs" / "architecture" / "network-egress-hosts.txt"


_HOST_RE = re.compile(r"https?://([A-Za-z0-9._{}-]+)")


#: RFC 2606 / RFC 6761 names, which are guaranteed never to resolve globally, plus RFC 6762
#: `.local` — multicast DNS, resolvable only on the caller's own link. A LAN name cannot be a
#: vendor destination, and `.local` shows up as the example in the egress-allowlist panel's
#: own placeholder text ("e.g. nas.local"), which is UI copy about the user's homelab.
_RESERVED_SUFFIXES = (".example", ".invalid", ".test", ".localhost", ".example.com", ".local")


_RESERVED_EXACT = frozenset({"example.com", "example.net", "example.org", "localhost"})


def skip_reason(host: str) -> str | None:
    """Why this host is not a destination, or None if it is one.

    Order matters only for legibility; the rules are disjoint. Each is a reason a string
    CANNOT be somewhere data goes, which is the only defensible basis for skipping —
    "it looked like documentation to me" is not.
    """
    if "{" in host or "}" in host:
        return "template placeholder"
    low = host.lower()
    if low in _RESERVED_EXACT or low.endswith(_RESERVED_SUFFIXES):
        return "RFC 2606/6761 reserved name"
    try:
        ip = ipaddress.ip_address(low)
    except ValueError:
        if "." not in low:
            return "single-label name, not routable"
        return None
    if ip.is_loopback:
        return "loopback"
    if ip.is_private and not ip.is_link_local:
        return "RFC 1918 private address"
    return None


def hosts_in(text: str) -> set[str]:
    """Routable hosts in `text`, ignoring single-line comments.

    Multi-line strings and docstrings are NOT excluded: a host inside one is usually prose,
    but deciding that from a regex is exactly the guess this rail must not make. Those hosts
    are listed in the table with a "never fetched" judgment instead, which is a claim someone
    wrote down rather than a claim the scanner invented.
    """
    found: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", "//")):
            continue
        for m in _HOST_RE.finditer(line):
            host = m.group(1).lower().rstrip(".")
            if skip_reason(host) is None:
                found.add(host)
    return found


def _table() -> dict[str, str]:
    """host -> judgment, from the census table."""
    out: dict[str, str] = {}
    for line in _TABLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        host, _sep, judgment = line.partition("—")
        out[host.strip().lower()] = judgment.strip()
    return out


#: The census's sections, by the start of their header text. The section an entry sits under
#: IS its classification, so it is parsed rather than merely displayed.
_SECTIONS = (
    ("Fetched by the product, on its own initiative", "unprompted"),
    ("Fetched, but only because the user asked", "user"),
    ("Browser-side", "browser"),
    ("Never fetched", "never"),
)


def _classes() -> dict[str, str]:
    """host -> the class of the section it is listed under (``""`` before any header)."""
    out: dict[str, str] = {}
    current = ""
    for line in _TABLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("# ──"):
            title = line.strip("# ─")
            current = next((c for prefix, c in _SECTIONS if title.startswith(prefix)), title)
            continue
        if not line or line.startswith("#"):
            continue
        out[line.partition("—")[0].strip().lower()] = current
    return out


def _strings(value: object) -> list[str]:
    """Every string inside a JSON-shaped value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _record_fetches() -> dict[str, str]:
    """host -> record, for URLs the product fetches that live in shipped DATA, not code.

    Read through the parsers the fetchers use — `bundled_model.load_declaration` for the
    bundled-chat app's own copy of the sign-off record (the file `download_weight` reads its
    `source_url` from), `source_recipes.list_recipes` for the recipes' `spec` — so the rail
    sees the URL a request will actually go to, not whatever a regex finds in the file.
    """
    from personalclaw.bundled_model import load_declaration
    from personalclaw.knowledge.source_recipes import list_recipes, recipes_dir

    out: dict[str, str] = {}
    signoff = _CORE / "apps" / "native" / "bundled-chat" / "bundled-model-signoff.txt"
    declaration = load_declaration(signoff)
    assert declaration is not None, f"{signoff} did not parse — the downloader could not read it"
    for host in hosts_in(declaration.source_url):
        out.setdefault(host, str(signoff.relative_to(_ROOT)))
    for recipe in list_recipes():
        where = str((recipes_dir() / f"{recipe.id}.json").relative_to(_ROOT))
        for text in _strings(recipe.spec):
            for host in hosts_in(text):
                out.setdefault(host, where)
    return out

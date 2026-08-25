"""Anti-regrowth rail: nowhere new for the product to phone home.

`SECURITY.md` and the website both state that PersonalClaw sends no telemetry. It is
true — and nothing enforced it. An analytics endpoint, a crash reporter, or a "check for
news" ping could be added in any PR and no test, lint or review checklist would notice: the
privacy claim was documentation, not a control. That is the same defect shape as a config
field nothing reads, except the thing nobody reads here is a promise to the user.

So this is a census of DESTINATIONS. Every routable hostname appearing as a literal in
shipped code — core `src/personalclaw/**/*.py` and the SPA `web/src/**/*.{ts,tsx}` — must be
listed in `docs/architecture/network-egress-hosts.txt` with a judgment saying whether it is
fetched and by whose action. A new host reds CI; a listed host that has disappeared reds it
too, so the table stays an exact mirror rather than accumulating.

**What this cannot answer, stated so nobody reads more into a green run.** It is a census of
where, not of what: it cannot tell you what is sent to an approved host, and a new module
reaching an already-listed host does not fail. Static scanning can answer "is there anywhere
new it could go", which is the question a phone-home is; it cannot answer "what left".

Reserved and non-routable names are skipped BY RULE, not listed, because a name that cannot
resolve cannot be a destination — RFC 2606/6761 names, loopback and RFC 1918 addresses, and
`{...}` template placeholders. Skipping by rule rather than by allowlist entry is what keeps
the table short enough to actually read.

Shaped after `test_provider_boundary_residue.py`: patterns + a judgment table + a
stale-entry check + a teeth check.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CORE = _ROOT / "src" / "personalclaw"
_WEB = _ROOT / "web" / "src"
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


def _shipped_files() -> list[Path]:
    out: list[Path] = []
    for p in sorted(_CORE.rglob("*.py")):
        if "__pycache__" not in p.parts:
            out.append(p)
    for pattern in ("*.ts", "*.tsx"):
        for p in sorted(_WEB.rglob(pattern)):
            # A test fixture's URL is not something the product contacts.
            if p.name.endswith((".test.ts", ".test.tsx")) or "__tests__" in p.parts:
                continue
            out.append(p)
    return out


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


def _found() -> dict[str, str]:
    """host -> first file it appears in."""
    out: dict[str, str] = {}
    for f in _shipped_files():
        for host in hosts_in(f.read_text(encoding="utf-8")):
            out.setdefault(host, str(f.relative_to(_ROOT)))
    return out


def test_every_egress_host_is_a_declared_destination():
    """The rail: a new destination in shipped code must be written down and justified."""
    table = _table()
    found = _found()
    undeclared = sorted(f"{h} ({where})" for h, where in found.items() if h not in table)
    assert not undeclared, (
        "shipped code reaches hostnames that are not in the egress census:\n"
        + "\n".join(f"  {u}" for u in undeclared)
        + "\n\nIf this is a legitimate destination, add it to "
        "docs/architecture/network-egress-hosts.txt with a judgment saying whether it is "
        "FETCHED and by WHOSE action. If it is telemetry, it does not belong in this "
        "product at all — SECURITY.md promises there is none."
    )


def test_the_census_has_no_stale_entries():
    """A host that no longer appears must leave the table, or it stops being a mirror."""
    table = _table()
    found = _found()
    stale = sorted(h for h in table if h not in found)
    assert not stale, (
        "these hosts are declared but no longer appear in shipped code — remove them so the "
        "census keeps meaning what it says:\n" + "\n".join(f"  {s}" for s in stale)
    )


def test_every_declared_host_carries_a_judgment():
    """A bare hostname is a list, not a decision.

    The judgment is the whole value of the table: "fetched, unprompted, on a schedule" and
    "an XML namespace that is never retrieved" are both single lines here and could not be
    more different, and no scanner can tell them apart.
    """
    thin = sorted(h for h, j in _table().items() if len(j) < 30)
    assert not thin, (
        "these entries have no real judgment — say whether the host is fetched and by "
        f"whose action: {thin}"
    )


def test_the_sweep_has_teeth(tmp_path):
    """The anti-regrowth proof, in both directions.

    Without this the scan could return nothing for any input and every test above would be
    green for a product that had just added an analytics endpoint.
    """
    sneaky = "requests.post('https://telemetry.example-vendor.net/v1/events', json=payload)\n"
    assert hosts_in(sneaky) == {
        "telemetry.example-vendor.net"
    }, "the scan missed an injected analytics endpoint — it would not catch a real one"
    assert (
        hosts_in("# see https://some-doc-host.net/guide for details\n") == set()
    ), "a single-line comment was treated as egress"
    assert (
        hosts_in("url = 'https://api.example.com/v1'\n") == set()
    ), "an RFC 2606 reserved name was treated as a destination"
    assert (
        hosts_in("url = f'https://{host}/api'\n") == set()
    ), "a template placeholder was treated as a destination"
    assert (
        hosts_in('placeholder="e.g. https://nas.local"\n') == set()
    ), "an mDNS `.local` name was treated as a vendor destination"


def test_the_census_is_not_vacuous():
    """Both sides are populated. Two empty sets agree about nothing."""
    found = _found()
    table = _table()
    assert len(found) >= 15, f"the scan found only {len(found)} hosts — it is not reading"
    assert len(table) >= 15, f"the census lists only {len(table)} hosts — it looks truncated"


def test_the_release_check_is_the_only_unprompted_destination():
    """The one host the product contacts without the user asking for that thing.

    Pinned deliberately: this is the sentence the privacy posture rests on, and it should
    take a failing test to change it. This docstring used to claim an
    ``updates.check_enabled`` opt-out existed "precisely because this request happens on a
    schedule" — no such field exists anywhere in ``src/``, and the check is unconditional
    (``test_self_update.py::test_auto_update_gates_the_apply_not_the_check``). The schedule
    is real; the opt-out was not.
    """
    unprompted = sorted(h for h, j in _table().items() if "FETCHED, unprompted" in j)
    assert unprompted == ["api.github.com"], (
        "the set of unprompted destinations changed. Adding one is a product decision about "
        f"the privacy claim, not an implementation detail: {unprompted}"
    )

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

**The section an entry sits under is a claim, and it is cross-checked** against what the code
can fetch. `huggingface.co` was filed under "Never fetched" (as a citation link in the voice
bake-off) while the bundled-model download fetched it and `local_models/hf_token.py`'s token
check did too. Every test above stayed green, because a host that appears SOMEWHERE in the
table satisfies the census whatever the table says about it. So the fetch sites are derived
two ways — the URL records a downloader reads (through the same parsers it uses) and the live
URL literals of every module that can open a network connection — and a fetched host must be
filed as fetched.
"""

from __future__ import annotations

import ast
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


#: The census's sections, by the start of their header text. The section an entry sits under
#: IS its classification, so it is parsed rather than merely displayed.
_SECTIONS = (
    ("Fetched by the product, on its own initiative", "unprompted"),
    ("Fetched, but only because the user asked", "user"),
    ("Browser-side", "browser"),
    ("Never fetched", "never"),
)
_FETCHED = frozenset({"unprompted", "user"})


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


#: Modules that open network connections. `aiohttp.web` is the SERVER framework every handler
#: imports and opens nothing outbound, so it alone does not make a module network-capable.
_NET_MODULES = (
    "personalclaw.net",
    "personalclaw.sdk.net",
    "urllib.request",
    "aiohttp",
    "httpx",
    "requests",
    "http.client",
)
_NOT_NET = ("aiohttp.web", "aiohttp.test_utils")


def _is_network_capable(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [f"{node.module}.{alias.name}" for alias in node.names]
        else:
            continue
        for name in names:
            if name.startswith(_NOT_NET):
                continue
            if any(name == m or name.startswith(m + ".") for m in _NET_MODULES):
                return True
    return False


def _live_strings(tree: ast.AST) -> list[str]:
    """Every string constant that is DATA — docstrings excluded, f-string parts included."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def live_hosts_in_network_module(source: str) -> set[str]:
    """Hosts a module that can open a connection holds as live data (empty if it cannot)."""
    tree = ast.parse(source)
    if not _is_network_capable(tree):
        return set()
    return {h for text in _live_strings(tree) for h in hosts_in(text)}


def _network_module_hosts() -> dict[str, list[str]]:
    """host -> the network-capable core modules (paths under src/personalclaw) holding it live."""
    out: dict[str, list[str]] = {}
    for f in _shipped_files():
        if f.suffix != ".py":
            continue
        for host in live_hosts_in_network_module(f.read_text(encoding="utf-8")):
            out.setdefault(host, []).append(str(f.relative_to(_CORE)))
    return out


def _found() -> dict[str, str]:
    """host -> first file it appears in (a shipped fetch record counts as a file)."""
    out: dict[str, str] = {}
    for f in _shipped_files():
        for host in hosts_in(f.read_text(encoding="utf-8")):
            out.setdefault(host, str(f.relative_to(_ROOT)))
    for host, where in _record_fetches().items():
        out.setdefault(host, where)
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


def test_every_entry_sits_under_a_known_section():
    """An entry before the first header, or under a header nobody classifies, claims nothing."""
    known = {cls for _prefix, cls in _SECTIONS}
    loose = sorted(
        f"{h} ({c or 'before any header'})" for h, c in _classes().items() if c not in known
    )
    assert not loose, f"entries outside the four sections: {loose}"


def test_a_host_a_shipped_record_fetches_is_filed_as_fetched():
    """A URL in shipped DATA is fetched by the code that reads it — never "never fetched".

    The bundled default model downloads from the sign-off record's `source_url`; a watched
    source created from a recipe polls the recipe's `spec` URL. Neither literal is in a `.py`
    file, so only reading the records says where those requests go.
    """
    classes = _classes()
    misfiled = sorted(
        f"{host} ({where}) is filed as {classes.get(host) or 'unlisted'!r}"
        for host, where in _record_fetches().items()
        if classes.get(host) not in _FETCHED
    )
    assert not misfiled, (
        "a shipped record fetches these hosts, but the census does not say they are fetched:\n"
        + "\n".join(f"  {m}" for m in misfiled)
    )


def test_a_never_fetched_host_held_live_by_a_network_module_names_that_module():
    """A module that can open a connection and holds a URL as data is a fetch site until the
    census says why it is not — so a "never fetched" judgment must name every such module.

    This is what `huggingface.co` failed: `local_models/hf_token.py` sends the token to
    `huggingface.co/api/whoami-v2` through `net.fetch`, and the entry said "citation urls".
    """
    table, classes = _table(), _classes()
    unexplained = sorted(
        f"{host}: {module}"
        for host, modules in _network_module_hosts().items()
        if classes.get(host) == "never"
        for module in modules
        if module not in table[host]
    )
    assert not unexplained, (
        "a network-capable module holds these 'never fetched' hosts as live data, and the "
        "judgment does not name the module or say why it is not a request:\n"
        + "\n".join(f"  {u}" for u in unexplained)
    )


def test_the_fetch_site_derivation_has_teeth():
    """Both derivations find what they must, so a green run means something."""
    fetching = (
        '"""See https://docs.vendor-example.net/guide for the API."""\n'
        "from personalclaw.net import fetch\n"
        'URL = "https://api.vendor-example.net/v1/whoami"\n'
    )
    assert live_hosts_in_network_module(fetching) == {
        "api.vendor-example.net"
    }, "a network module's live URL constant was missed, or its docstring was read as data"
    server_only = 'from aiohttp import web\nLINK = "https://docs.vendor-example.net/x"\n'
    assert (
        live_hosts_in_network_module(server_only) == set()
    ), "a server-only module fetches nothing"
    # Positive controls on the real tree: the known fetch sites ARE found.
    modules = _network_module_hosts()
    assert "local_models/hf_token.py" in modules.get("huggingface.co", [])
    assert "self_update.py" in modules.get("api.github.com", [])
    assert "apps/native/ollama-models/provider.py" in modules.get("ollama.com", [])
    records = _record_fetches()
    assert records.get("huggingface.co", "").endswith("bundled-model-signoff.txt")
    assert {"github.com", "pypi.org", "www.reddit.com"} <= set(records)


def test_the_census_is_not_vacuous():
    """Both sides are populated. Two empty sets agree about nothing."""
    found = _found()
    table = _table()
    assert len(found) >= 15, f"the scan found only {len(found)} hosts — it is not reading"
    assert len(table) >= 15, f"the census lists only {len(table)} hosts — it looks truncated"


def test_the_release_check_is_the_only_unprompted_destination():
    """The one host the product contacts without the user asking for that thing.

    Pinned deliberately: this is the sentence the privacy posture rests on, and it should
    take a failing test to change it. ``api.github.com`` remains the sole unprompted
    destination — but it is now suppressible: ``updates.check_enabled=false`` is the egress
    kill switch (RUM-3), and with it set the check makes ZERO calls
    (``test_self_update.py::test_fetch_latest_release_kill_switch_makes_zero_calls`` +
    ``test_do_update_check_kill_switch_runs_no_subprocess``). The schedule is real; the
    opt-out is now real too, and defaults ON so this rail's "unprompted" claim still holds.
    """
    unprompted = sorted(h for h, j in _table().items() if "FETCHED, unprompted" in j)
    assert unprompted == ["api.github.com"], (
        "the set of unprompted destinations changed. Adding one is a product decision about "
        f"the privacy claim, not an implementation detail: {unprompted}"
    )

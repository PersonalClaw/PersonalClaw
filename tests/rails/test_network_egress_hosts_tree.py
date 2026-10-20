"""The source-tree checks split from
``tests/test_network_egress_hosts.py``, and every test that reads the census table in ``docs/``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from fnmatch import fnmatch
from pathlib import Path

import source_index
from network_egress_hosts_shared import _CORE, _ROOT, _record_fetches, hosts_in
from rails_repo import REPO

_WEB = _ROOT / "web" / "src"

_TABLE = REPO / "docs" / "architecture" / "network-egress-hosts.txt"


def _table() -> dict[str, str]:
    """host -> judgment, from the census table."""
    out: dict[str, str] = {}
    for line in source_index.text(_TABLE).splitlines():
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
    for line in source_index.text(_TABLE).splitlines():
        line = line.strip()
        if line.startswith("# ──"):
            title = line.strip("# ─")
            current = next((c for prefix, c in _SECTIONS if title.startswith(prefix)), title)
            continue
        if not line or line.startswith("#"):
            continue
        out[line.partition("—")[0].strip().lower()] = current
    return out


_FETCHED = frozenset({"unprompted", "user"})


def _files_under(root: Path, index_walk) -> list[Path]:
    """The files of *root*: the suite's index walk when *root* is a root the index holds, else
    the tree a test planted there (``test_licence_governance`` points ``_CORE`` and ``_WEB`` at
    one). Reading the real walk for a planted root would judge a tree that test did not build."""
    if source_index.holds(root):
        return source_index.under(index_walk(), root)
    return sorted(p for p in root.rglob("*") if p.is_file())


def _text(path: Path) -> str:
    return source_index.text(path) if source_index.holds(path) else path.read_text("utf-8")


def _tree(path: Path) -> ast.Module:
    return source_index.tree(path) if source_index.holds(path) else ast.parse(_text(path))


def _shipped_files() -> list[Path]:
    out = [p for p in _files_under(_CORE, source_index.package_files) if p.suffix == ".py"]
    for pattern in ("*.ts", "*.tsx"):
        for p in (
            w for w in _files_under(_WEB, source_index.web_files) if fnmatch(w.name, pattern)
        ):
            # A test fixture's URL is not something the product contacts.
            if p.name.endswith((".test.ts", ".test.tsx")) or "__tests__" in p.parts:
                continue
            out.append(p)
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


def live_hosts_in_network_module(source: str | ast.Module) -> set[str]:
    """Hosts a module (its text, or its tree) that can open a connection holds as live data
    (empty if it cannot)."""
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    if not _is_network_capable(tree):
        return set()
    return {h for text in _live_strings(tree) for h in hosts_in(text)}


def _network_module_hosts() -> dict[str, list[str]]:
    """host -> the network-capable core modules (paths under src/personalclaw) holding it live."""
    out: dict[str, list[str]] = {}
    for f in _shipped_files():
        if f.suffix != ".py":
            continue
        for host in live_hosts_in_network_module(_tree(f)):
            out.setdefault(host, []).append(str(f.relative_to(_CORE)))
    return out


def _found() -> dict[str, str]:
    """host -> first file it appears in (a shipped fetch record counts as a file)."""
    out: dict[str, str] = {}
    for f in _shipped_files():
        for host in hosts_in(_text(f)):
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


def test_the_release_check_is_the_only_unprompted_destination():
    """The one host the product contacts without the user asking for that thing.

    Pinned deliberately: this is the sentence the privacy posture rests on, and it should
    take a failing test to change it. ``api.github.com`` remains the sole unprompted
    destination — but it is now suppressible: ``updates.check_enabled=false`` is the egress
    kill switch, and with it set the check makes ZERO calls
    (``test_self_update.py::test_the_check_makes_zero_calls_when_checking_is_disabled`` +
    ``test_do_update_check_kill_switch_runs_no_subprocess``). The schedule is real; the
    opt-out is now real too, and defaults ON so this rail's "unprompted" claim still holds.
    """
    unprompted = sorted(h for h, j in _table().items() if "FETCHED, unprompted" in j)
    assert unprompted == ["api.github.com"], (
        "the set of unprompted destinations changed. Adding one is a product decision about "
        f"the privacy claim, not an implementation detail: {unprompted}"
    )

"""The source-tree checks split from
``tests/test_gateway_base_owner.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import source_index
from rails_repo import REPO

SRC = REPO / "src" / "personalclaw"


#: The symbols that answer "where is the gateway". A read of one of these is a resolution site.
_RESOLVERS = ("DASHBOARD_PORT", "parse_dashboard_url", "_DEFAULT_PORT")


#: Every site allowed to resolve one for itself, with the reason it is NOT a child's API base.
#: A new entry here is a deliberate decision; an unlisted one reds this rail.
_ALLOWED: dict[tuple[str, str], str] = {
    ("config/loader.py", "_DEFAULT_PORT"): "declares the literal; the one place it may live",
    ("dashboard/origin.py", "_DEFAULT_PORT"): (
        "parse_dashboard_url/dashboard_origin — what the SERVER binds and which browser "
        "origins it accepts, never where a child connects"
    ),
    ("gateway.py", "parse_dashboard_url"): "the bind decision; this process then publishes it",
    ("dashboard/state.py", "DASHBOARD_PORT"): "default arg for the bind port; always passed",
    ("dashboard/server.py", "_DEFAULT_PORT"): "same default arg, via dashboard.state",
    ("dashboard/token_auth.py", "_DEFAULT_PORT"): "token audience/origin, not an API base",
    ("dashboard/handlers/auth.py", "_DEFAULT_PORT"): "token audience, via token_auth",
    ("cli_server.py", "parse_dashboard_url"): (
        "restart: the port a fresh gateway BINDS when none was running, the gateway's own bind "
        "decision; a command CONNECTS where home_gateway.reach() says"
    ),
    ("cli_setup.py", "DASHBOARD_PORT"): "prints an example URL during setup",
    ("container_host.py", "_DEFAULT_PORT"): (
        "the README's `docker run` publishes the IMAGE's port for the host to run, never where "
        "a child connects; this process's own port is not the new container's"
    ),
    ("cli_doctor.py", "parse_dashboard_url"): "displays the CONFIGURED url as a diagnostic",
}


#: Modules that address a CHILD of this gateway. They must ask ``gateway_base`` and nothing else.
_CHILD_BASE_MODULES = (
    "mcp_core.py",
    "mcp_shared.py",
    "schedule_script.py",
    "snapshot.py",
    "acp/mcp_servers.py",
)


def _trees_under(root: Path) -> list[tuple[Path, ast.Module]]:
    """Every ``.py`` file under *root* with its tree: the package from the suite's one source
    index, a fixture root (a test's planted tree) walked and parsed here."""
    if root == SRC:
        return source_index.package_trees()
    out = []
    for py in sorted(root.rglob("*.py")):
        try:
            out.append((py, ast.parse(py.read_text(encoding="utf-8"))))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
    return out


def _resolution_sites(root: Path) -> set[tuple[str, str]]:
    """Every ``(module, resolver)`` pair read anywhere under *root*, from the AST.

    Attribute reads count too (``loader.DASHBOARD_PORT``), so re-importing under another name
    does not hide a site.
    """
    found: set[tuple[str, str]] = set()
    for py, tree in _trees_under(root):
        rel = py.relative_to(root).as_posix()
        for node in ast.walk(tree):
            name = ""
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            if name in _RESOLVERS:
                found.add((rel, name))
    return found


class TestNobodyElseResolvesTheBase:
    def test_the_resolution_sites_are_exactly_the_declared_ones(self):
        """Reds when a new site learns to resolve the gateway's address on its own.

        Two independently-derived answers that can disagree IS the defect (#2539), so a new
        resolver is a decision, not an implementation detail: add it to ``_ALLOWED`` with the
        reason it is not a child's API base, or route it through ``gateway_base``.
        """
        found = _resolution_sites(SRC)
        unlisted = sorted(found - set(_ALLOWED))
        assert not unlisted, (
            "these sites resolve the gateway address without the owner; route them through "
            f"personalclaw.gateway_base or declare them in _ALLOWED: {unlisted}"
        )

    def test_the_declared_list_has_no_dead_entries(self):
        """A stale allowlist entry is a hole that looks like a decision."""
        found = _resolution_sites(SRC)
        assert sorted(set(_ALLOWED) - found) == []

    def test_the_child_base_modules_ask_the_owner(self):
        """The modules that address a child read NONE of the resolvers themselves."""
        found = _resolution_sites(SRC)
        offenders = sorted(
            (module, symbol) for module, symbol in found if module in _CHILD_BASE_MODULES
        )
        assert not offenders, f"a child-base module resolved the port itself: {offenders}"

    def test_the_scan_reds_on_a_planted_second_resolver(self, tmp_path):
        """Vacuity floor, detection direction: prove the scanner sees a NEW site.

        A rail that cannot fail is not a rail. Planted in a throwaway tree so the check needs
        no edit to the repo.
        """
        (tmp_path / "sneaky.py").write_text(
            "from personalclaw.dashboard.origin import parse_dashboard_url\n"
            "def base():\n"
            "    _h, p = parse_dashboard_url('')\n"
            "    return f'http://localhost:{p}'\n",
            encoding="utf-8",
        )
        planted = _resolution_sites(tmp_path)
        assert ("sneaky.py", "parse_dashboard_url") in planted
        assert sorted(planted - set(_ALLOWED)) == [("sneaky.py", "parse_dashboard_url")]

    def test_the_scan_is_not_vacuous(self):
        """Vacuity floor, other direction: the rail must not be green from matching nothing.

        The bound is ABSOLUTE and counted by a different mechanism than the AST walk that
        derives the expected set — a plain textual count — so a walker that silently stopped
        finding anything cannot satisfy both.
        """
        text_hits = 0
        pattern = re.compile("|".join(rf"\b{re.escape(r)}\b" for r in _RESOLVERS))
        for py in source_index.package_files():
            text_hits += len(pattern.findall(source_index.text(py)))
        assert text_hits >= 30, f"only {text_hits} textual occurrences — the scan lost its corpus"
        assert len(_resolution_sites(SRC)) >= 10
        assert len(_ALLOWED) >= 10

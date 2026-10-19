"""Which test modules must carry the ``security`` marker, read from their source and never imported.

The rules that keep a test from being rerun into green, quarantined or selected out key on the
``security`` marker, never on a folder. This is the rule that keeps the marked set complete: a
module must declare ``pytestmark = pytest.mark.security`` (or a list holding it) when one of three
clauses names it, and the CI contract row in ``tests/rails/test_ci_contract.py`` fails on any that
does not. A module may carry the marker without matching a clause, so the set grows by hand and
never shrinks by accident. Including a test that only uses a registry for its setup costs
nothing; leaving out one that proves a boundary is the dangerous direction.

The three clauses:

1. every module under ``tests/security/``;
2. every module that imports a security registry or a fail-closed reader (``REGISTRIES``),
   anywhere in the module, a function body included;
3. the families named by file: ``NAME_PREFIXES``, and ``NAMED_MODULES`` one by one.

A clause naming something that no longer exists (a registry moved, a named module renamed) fails
the row too: otherwise a rename would shrink the set without anyone deciding it.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

MARKER = "security"

#: Clause 1: every test module under this folder of ``tests/``.
SECURITY_FOLDER = "security"

#: Clause 2: a module that imports one of these modules, or a name from one, proves something about
#: a security or fail-closed boundary, or leans on one for its setup.
REGISTRIES: dict[str, str] = {
    "personalclaw.security": "the path and command guards, the redactors and the untrusted fence",
    "personalclaw.shell_denylist": "the shell denylist every command is screened by",
    "personalclaw.apps.permissions": "which routes an app may call (ROUTE_AUTHZ and its families)",
    "personalclaw.automation_posture": "what an automation may do unattended (POSTURE_SPECS)",
    "personalclaw.safety_flags": "the fail-closed reading of a yes-or-no setting (yes_or_no)",
    "personalclaw.browse.safety_script": "what blocks a browsed page's network and devices",
}

#: Clause 3, by file-name prefix.
NAME_PREFIXES: dict[str, str] = {
    "test_apps_cannot_": "an app is refused what only the owner may do",
    "test_apps_see_only_": "an app is confined to what is its own",
}

#: Clause 3, one module at a time: the browser safety tests and the real-home and credential
#: guards. Each was read for what it asserts; a name that only mentions a credential is not here.
NAMED_MODULES: dict[str, str] = {
    "tests/test_browse_safety_script.py": "a browsed page's fetch, media and devices are blocked",
    "tests/test_browse_cdp_live.py": "a denied navigation never reaches the network, in a browser",
    "tests/test_real_home_guard.py": "the suite refuses every touch of the real home, by test",
    "tests/test_dev_tools_act_only_on_a_named_scratch_home.py": (
        "dev tools refuse the default home and a gateway that is not the named home's"
    ),
    "tests/test_each_home_keeps_its_own_keychain_items.py": (
        "a home never lists, reads, uses or deletes another home's keychain items"
    ),
    "tests/test_no_test_git_reaches_the_machines_credential_helper.py": (
        "a test's git that could sign in with the machine's credential helper is refused"
    ),
    "tests/test_importing_personalclaw_touches_no_home.py": (
        "importing the package or running a command that needs no home makes and opens none"
    ),
    "tests/test_active_home_is_the_only_home.py": (
        "one resolver decides the home, and every rail refuses an override it cannot be"
    ),
    "tests/test_credential_guard_follows_links.py": "a linked credential folder is still guarded",
    "tests/test_the_shell_refuses_listing_a_credential_folder.py": (
        "the shell refuses a command that lists a credential folder"
    ),
    "tests/test_the_shell_refuses_the_credential_store_wherever_the_home_is.py": (
        "the shell, the file tools and the sandbox refuse the store of the home in use"
    ),
    "tests/test_url_credential_redaction.py": "a login in an address is redacted everywhere",
    "tests/test_app_secrets_live_in_the_credential_store.py": (
        "an app's secret settings never land in its settings file"
    ),
    "tests/test_provider_keys_live_in_the_credential_store.py": (
        "a provider key never lands in config.json, and another owner's key is refused"
    ),
    "tests/test_mcp_server_secrets_live_in_the_credential_store.py": (
        "an MCP server's env and header secrets never land in mcp.json"
    ),
    "tests/test_mcp_argument_credentials_live_in_the_credential_store.py": (
        "a credential in an MCP server's arguments or address never lands in mcp.json"
    ),
    "tests/test_webhook_token_lives_in_the_credential_store.py": (
        "the webhook token never lands in config.json or an export"
    ),
    "tests/test_multiline_secret_is_kept_in_the_credential_store.py": (
        "a multi-line secret stays in the store and a NUL is refused"
    ),
    "tests/test_export_carries_no_credential_store_value.py": (
        "no value the store holds appears in an export or a snapshot"
    ),
    "tests/test_snapshot_carries_no_credentials.py": "no snapshot or export carries a credential",
    "tests/test_credential_write_is_atomic_and_0600.py": (
        "the store is never world-readable and never truncated by a failed write"
    ),
    "tests/test_auth_credentials.py": (
        "an unreadable login file fails closed, and no password or hash escapes"
    ),
    "tests/test_minting_a_credential_needs_a_recent_sign_in.py": (
        "an old session cannot mint a lasting credential"
    ),
    "tests/test_browse_credential_handoff.py": "a credential on a page never reaches the agent",
    "tests/test_mcp_edit_forms_mask_every_credential_they_show.py": (
        "every form masks a stored credential, and a changed mask is refused"
    ),
    "tests/test_subscription_credentials.py": (
        "a malformed or expired sign-in is not signed in; the other tool's store is never written"
    ),
    "tests/test_credential_backend.py": "a fail or null keychain is refused; the file stays 0600",
    "tests/test_credential_store.py": "a key another record owns is refused before it is read",
    "tests/test_one_credential_store.py": "a workflow step naming another record's key is refused",
    "tests/test_ci_run.py": (
        "a CI job runs with a home, a temp folder and a git of its own; a credential its git "
        "asks for fails the run"
    ),
}


def collected_modules(tests_dir: Path) -> list[Path]:
    """Every module pytest collects as tests here, by its default file patterns."""
    found = set(tests_dir.rglob("test_*.py")) | set(tests_dir.rglob("*_test.py"))
    return sorted(found)


def imported_registries(tree: ast.Module) -> set[str]:
    """The registries a module imports, at its top or inside any function or class."""
    found: set[str] = set()

    def under(name: str) -> set[str]:
        return {r for r in REGISTRIES if name == r or name.startswith(r + ".")}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found |= under(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found |= under(node.module)
            for alias in node.names:
                found |= under(f"{node.module}.{alias.name}")
    return found


def _is_the_marker(node: ast.expr) -> bool:
    """``pytest.mark.security``, spelt exactly."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == MARKER
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def carries_marker(tree: ast.Module) -> bool:
    """Whether the module's own ``pytestmark``, as last assigned at its top level, holds it."""
    value: ast.expr | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "pytestmark"
        ):
            value = node.value
    if value is None:
        return False
    if isinstance(value, (ast.List, ast.Tuple)):
        return any(_is_the_marker(element) for element in value.elts)
    return _is_the_marker(value)


def _registry_source(root: Path, module: str) -> Path | None:
    base = root / "src" / Path(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True)
class Census:
    """What the rule requires of one tree, and what that tree's modules declare."""

    required: dict[str, tuple[str, ...]]
    marked: frozenset[str]
    gone: tuple[str, ...]

    @property
    def unmarked(self) -> list[str]:
        return sorted(set(self.required) - self.marked)

    def violations(self) -> list[str]:
        out = list(self.gone)
        unmarked = self.unmarked
        if unmarked:
            out.append(
                f"{len(unmarked)} of the {len(self.required)} modules that must carry the "
                f"`{MARKER}` marker do not ({len(self.marked)} modules carry it); declare "
                f"`pytestmark = pytest.mark.{MARKER}` (in a list beside an existing mark):"
            )
            out += [f"  {m}  [{'; '.join(self.required[m])}]" for m in unmarked]
        return out


def take(root: Path) -> Census:
    """Read every test module under ``root/tests`` and apply the three clauses."""
    tests_dir = root / "tests"
    gone = [
        f"registry {r} has no source under src/: update REGISTRIES in tests/security_census.py"
        for r in REGISTRIES
        if _registry_source(root, r) is None
    ]
    gone += [
        f"{m} is named by the rule but does not exist: renamed? update NAMED_MODULES"
        for m in NAMED_MODULES
        if not (root / m).is_file()
    ]
    required: dict[str, tuple[str, ...]] = {}
    marked: set[str] = set()
    for path in collected_modules(tests_dir):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        clauses: list[str] = []
        if path.relative_to(tests_dir).parts[0] == SECURITY_FOLDER:
            clauses.append(f"1: under tests/{SECURITY_FOLDER}/")
        clauses += [f"2: imports {r}" for r in sorted(imported_registries(tree))]
        clauses += [f"3: {p}*" for p in NAME_PREFIXES if path.name.startswith(p)]
        if rel in NAMED_MODULES:
            clauses.append("3: named guard")
        if clauses:
            required[rel] = tuple(clauses)
        if carries_marker(tree):
            marked.add(rel)
    return Census(required=required, marked=frozenset(marked), gone=tuple(gone))


# ── The census line a run that checked the rule prints ───────────────────────────────────────

#: Where the line waits for the terminal summary, in the process that prints it.
CENSUS_LINE = pytest.StashKey[str]()
#: How many collected items carry the marker, and how many were collected, counted before ``-m``,
#: ``-k`` or a shard selects.
MARKED_ITEMS = pytest.StashKey[tuple[int, int]]()
#: Its key in an xdist worker's output, which the controller reads when the worker finishes.
WORKER_KEY = "security_census"


def marked_items(items: Iterable[pytest.Item]) -> int:
    return sum(1 for item in items if item.get_closest_marker(MARKER) is not None)


def census_line(census: Census, marked: int, collected: int) -> str:
    """The modules are the whole tree's; the items are only what this run collected, which is
    every item when the run collects all of ``tests/``."""
    return (
        f"security marker: {len(census.required)} modules must carry it, "
        f"{len(census.marked)} modules carry it; "
        f"{marked} of the {collected} test items this run collected carry it"
    )


def record(config: pytest.Config, census: Census) -> None:
    """Keep the line for this run's summary: in this process, or in the worker's output."""
    line = census_line(census, *config.stash.get(MARKED_ITEMS, (0, 0)))
    workeroutput = getattr(config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput[WORKER_KEY] = line
    else:
        config.stash[CENSUS_LINE] = line


class Reporter:
    """Counts the marked items as collected, and carries the census line from the xdist worker
    that checked the rule to the controller, which prints it."""

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, config: pytest.Config, items: list[pytest.Item]):
        config.stash[MARKED_ITEMS] = (marked_items(items), len(items))

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:  # noqa: ARG002 — xdist hook signature
        line = getattr(node, "workeroutput", {}).get(WORKER_KEY)
        if line:
            node.config.stash[CENSUS_LINE] = line

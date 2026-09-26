"""The PersonalClaw home has ONE resolver, and every path in `src/` follows it (issue 287).

`PERSONALCLAW_HOME` is the isolation boundary the whole project rests on: `make serve`
defaults it to `./.dev-home`, every destructive test is required to point it at `tmp_path`,
and `--seed` refuses to run against the real home. `config.loader` answers where the home is —
`resolve_config_dir()` (where it is) and `config_dir()` (that, created) — and a module that works
the answer out itself opts out of every guarantee built on the resolver: the system-directory
refusal, `~` expansion, and a test's `config_dir` isolation.

**This was found and fixed FOUR times, in four modules, each fix leaving a comment instead of a
check.** The comments are still in the tree:

* `agent.py:_bundled_hooks` — the bundled `postToolUse` hook wrote to a literal
  `~/.personalclaw/audit.log`, so *"every isolated-home session appends to the operator's
  REAL home instead of its own — a home-isolation break in a security control"* (`G31`).
* `dashboard/handlers/files.py` — the Uploads + PersonalClaw browse roots, where a dev
  gateway would *"browse AND edit the developer's REAL home via the write allowlist"* (#294).
* `dashboard/handlers/mcp.py:_canonical_mcp_json` — *"the old `Path.home()` hardcode ignored
  it"*.
* `subagent_persistence.py` — a module-level `config_dir()` constant, converted to a call.

The ratchet this file carried next — a regex for the literal spelling that EXCUSED it when the
enclosing function also mentioned `config_dir` — kept the shape it was meant to stop: thirteen
`_path_home_pclaw()` helpers each answered `config_dir()` and, on any exception, fell back to
`Path.home() / ".personalclaw"` — a different home from the one `PERSONALCLAW_HOME` named. It
also could not see the other shape at all: ten `Path(os.environ.get("PERSONALCLAW_HOME",
config_dir()))` readers, which bypass `~` expansion, the system-directory refusal and a
`config_dir` patch, and `sel.py`'s own resolver, which is why code that isolated `config_dir`
still wrote the real security log. Measured on `origin/main` before the resolver was made the
only one: 35 sites across 30 modules.

So the rule is now absolute and the scan is an AST, not a spelling: outside `config/loader.py`
nothing reads `$PERSONALCLAW_HOME`'s value, and nothing builds `~/.personalclaw` from the user's
home. A rail that needs the DEFAULT home asks `default_config_dir()`; one that must know whether
the home in use is that default asks `uses_default_home()`.

ARCC was queried first (file access + infrastructure are trigger domains). The applicable
guidance is the *isolate data from other processes* recommendation — write "to disk under a
more restrictive and user/processes specific folder location" — plus SAX-06 Outcome 3's
append-only, least-privilege treatment of audit logs. The CloudTrail/S3/KMS material in both
documents is cloud infrastructure and does not apply to a local file; noted, not stretched.
"""

from __future__ import annotations

import argparse
import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The one module allowed to work the home out.
_RESOLVER = "config/loader.py"

_ENV = "PERSONALCLAW_HOME"
_HOME_NAME = ".personalclaw"


def _text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _reads_the_variable(node: ast.AST, aliases: frozenset[str]) -> bool:
    """``os.environ.get(…)``, ``os.getenv(…)`` or ``os.environ[…]`` of the variable: its VALUE.

    Setting it (a child process's env) and testing whether it is set (``in``) are not reads, and
    neither can produce a path."""

    def names_it(arg: ast.AST) -> bool:
        return _text(arg) == _ENV or (isinstance(arg, ast.Name) and arg.id in aliases)

    if isinstance(node, ast.Call) and _call_name(node) in ("get", "getenv") and node.args:
        return names_it(node.args[0])
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        return names_it(node.slice)
    return False


def _user_home(node: ast.AST) -> bool:
    """An expression answering the USER's home directory: ``Path.home()``, ``expanduser(…)``,
    ``$HOME``."""
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name in ("home", "expanduser"):
            return True
        if name in ("get", "getenv") and node.args and _text(node.args[0]) == "HOME":
            return True
    return isinstance(node, ast.Subscript) and _text(node.slice) == "HOME"


def _home_name(node: ast.AST) -> bool:
    text = _text(node)
    if text is not None:
        return text == _HOME_NAME or text.startswith(f"~/{_HOME_NAME}")
    return (isinstance(node, ast.Name) and node.id == "CONFIG_DIR_NAME") or (
        isinstance(node, ast.Attribute) and node.attr == "CONFIG_DIR_NAME"
    )


def resolutions(source: str) -> list[tuple[int, str]]:
    """Every place ``source`` works the home out itself, as ``(line, what)``.

    Two shapes, which between them are every independent resolver the census found:

    * **reading the variable** — including through a module-level alias
      (``HOME_ENV_VAR = "PERSONALCLAW_HOME"`` in ``llm/scripted.py``);
    * **building the default home** — the user's home and the home's name in ONE expression.
      A skip-list naming ``.personalclaw`` directories, or a project's own ``.personalclaw/``
      config dir, has the name without the user's home, and is not a resolver.

    Comments are not in the AST and a docstring is a bare string statement, so an explanation
    that spells the old code out is never mistaken for it.
    """
    tree = ast.parse(source)
    aliases: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and _text(node.value) == _ENV:
            aliases |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if _text(node.value) == _ENV and isinstance(node.target, ast.Name):
                aliases.add(node.target.id)
    frozen_aliases = frozenset(aliases)
    found: set[tuple[int, str]] = set()
    composed: list[ast.AST] = []
    for node in ast.walk(tree):
        if _reads_the_variable(node, frozen_aliases):
            found.add((node.lineno, f"reads ${_ENV}"))
        if isinstance(node, (ast.BinOp, ast.Call)):
            inner = list(ast.walk(node))
            if any(_user_home(n) for n in inner) and any(_home_name(n) for n in inner):
                composed.append(node)
    # The innermost expression only: `str(Path.home() / ".personalclaw")` is one site, not two.
    for node in composed:
        below = set(ast.walk(node)) - {node}
        if not any(other in below for other in composed):
            found.add((node.lineno, f"builds ~/{_HOME_NAME}"))
    return sorted(found)


def _tree_resolutions() -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        hits = resolutions(path.read_text(encoding="utf-8"))
        if hits:
            out[rel] = hits
    return out


def test_no_module_but_the_loader_resolves_the_home():
    """🪤 The rail. A second resolver fails here with its file and line."""
    offenders = [
        f"{rel}:{line}  {what}"
        for rel, hits in _tree_resolutions().items()
        if rel != _RESOLVER
        for line, what in hits
    ]
    assert offenders == [], (
        "these sites work out the PersonalClaw home themselves instead of asking "
        "`config.loader`, so they can disagree with it — about `~`, about a refused system "
        "directory, and about a test's `config_dir` isolation:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse `config_dir()` (or `resolve_config_dir()` where creating the home would be "
        "wrong). A rail that must refuse the DEFAULT home asks `uses_default_home()`."
    )


def test_the_scan_sees_the_resolvers_own_rule():
    """🪤 Vacuity floor, on real code: the loader DOES read the variable and DOES build the
    default home — that is its job — so a scan that stopped seeing either would find them gone
    here, instead of reporting a clean tree forever."""
    kinds = {what for _, what in _tree_resolutions().get(_RESOLVER, [])}
    assert kinds == {f"reads ${_ENV}", f"builds ~/{_HOME_NAME}"}, kinds


@pytest.mark.parametrize(
    "code",
    [
        'x = Path(os.environ.get("PERSONALCLAW_HOME", config_dir()))',
        'x = os.getenv("PERSONALCLAW_HOME")',
        'x = os.environ["PERSONALCLAW_HOME"]',
        'HOME_ENV_VAR = "PERSONALCLAW_HOME"\nx = os.environ.get(HOME_ENV_VAR, "").strip()',
        'x = Path.home() / ".personalclaw"',
        'x = _P.home() / ".personalclaw" / "sessions"',
        "x = (Path.home() / CONFIG_DIR_NAME).resolve()",
        'x = os.path.expanduser("~/.personalclaw/workspace")',
        'x = Path("~/.personalclaw").expanduser()',
        'x = os.path.join(os.path.expanduser("~"), ".personalclaw")',
        'x = Path(os.environ["HOME"]) / ".personalclaw"',
    ],
)
def test_the_scan_finds_every_shape_the_census_found(code):
    assert resolutions(code), f"the scan missed a resolver: {code!r}"


@pytest.mark.parametrize(
    "code",
    [
        'chosen = "PERSONALCLAW_HOME" in os.environ',
        'env = {**os.environ, "PERSONALCLAW_HOME": str(config_dir())}',
        'env["PERSONALCLAW_HOME"] = str(home)',
        'f = Path(project) / ".personalclaw" / "projection_rules.json"',
        'SKIP = frozenset({".git", "node_modules", ".personalclaw"})',
        'x = Path.home() / ".agents" / "skills"',
        'x = Path.home() / ".claude.json"',
        'x = Path.home() / ".claude" / "agents"',
        'def f():\n    """Was `Path.home() / ".personalclaw"`, frozen at import."""\n',
    ],
)
def test_the_scan_passes_what_does_not_resolve_the_home(code):
    """The claude-code CLI's own config (``~/.claude.json``, ``~/.claude/agents``) genuinely
    lives at the user's real home and must NOT follow ``PERSONALCLAW_HOME`` — a rail that forced
    it into the active home would break MCP sync against that backend."""
    assert resolutions(code) == [], f"not a resolver, but the scan flagged it: {code!r}"


# ── the rails that ask "is this the default home?" ────────────────────────────────────────
#
# A system directory is what `resolve_config_dir()` REFUSES: the process runs on the default home
# instead. Every rail below re-derived the home itself and so took the override at its word — it
# let the operation through while the product ran on `~/.personalclaw`. `/usr/<name>` is used
# because it is refused on every platform (`/etc` resolves to `/private/etc` on macOS, which the
# resolver does not refuse) and cannot be written by the test runner.

_REFUSED = "/usr/pclaw-rail-probe-home"


def test_yolo_is_refused_when_the_override_is_a_directory_the_home_cannot_be(monkeypatch, capsys):
    from personalclaw.cli import _resolve_gateway_args

    monkeypatch.setenv("PERSONALCLAW_HOME", _REFUSED)
    ns = argparse.Namespace(
        command="gateway",
        headless=False,
        no_crons=False,
        seed=None,
        seed_replace=False,
        no_open=False,
        port=None,
        json_ready=False,
        approval="yolo",
        test_mode=False,
    )
    with pytest.raises(SystemExit) as exc:
        _resolve_gateway_args(ns)
    assert exc.value.code == 2
    assert "main gateway home" in capsys.readouterr().err


def test_seed_refuses_an_override_the_home_cannot_be(monkeypatch):
    """Before: the rail compared the override with the main home, found them different, and
    went on to ``copytree`` into ``/usr`` — while the gateway it seeded for ran on the main home.
    ``copytree``/``rmtree`` are replaced so no run of this test can write anywhere."""
    import personalclaw.seed as seed_mod

    writes: list[tuple] = []
    monkeypatch.setattr(seed_mod.shutil, "copytree", lambda *a, **k: writes.append(a))
    monkeypatch.setattr(seed_mod.shutil, "rmtree", lambda *a, **k: writes.append(a))
    monkeypatch.setenv("PERSONALCLAW_HOME", _REFUSED)
    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("empty", replace=True)
    assert excinfo.value.rail == seed_mod.SeedError.RAIL_MAIN_HOME
    assert writes == []


def test_an_ablation_overlay_refuses_an_override_the_home_cannot_be(monkeypatch):
    from personalclaw.evals.overlay import OverlayRefusedError, throwaway_home

    monkeypatch.setenv("PERSONALCLAW_HOME", _REFUSED)
    with pytest.raises(OverlayRefusedError):
        throwaway_home()


def test_the_scripted_provider_refuses_an_override_the_home_cannot_be(tmp_path, monkeypatch):
    from personalclaw.llm.scripted import (
        SCRIPT_ENV_VAR,
        ScriptedProviderRefused,
        resolve_script_path,
    )

    script = tmp_path / "script.json"
    script.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(SCRIPT_ENV_VAR, str(script))
    monkeypatch.setenv("PERSONALCLAW_HOME", _REFUSED)
    with pytest.raises(ScriptedProviderRefused):
        resolve_script_path()


def test_the_secret_path_guard_protects_the_home_in_use(monkeypatch):
    """The guard listed ``$PERSONALCLAW_HOME/<secret>`` for whatever the variable said — so with
    an override the home cannot be, it guarded a directory nothing used and left the default
    home's credentials to the other tiers."""
    from personalclaw.security import _pclaw_home_sensitive_paths

    monkeypatch.setenv("PERSONALCLAW_HOME", _REFUSED)
    guarded = _pclaw_home_sensitive_paths()
    in_use = str(pathlib.Path.home() / ".personalclaw")  # where the resolver sends a refused one
    assert guarded, "the guard lists no paths"
    assert all(p.startswith(in_use + "/") for p in guarded), guarded


# ── one answer, wherever it is asked ──────────────────────────────────────────────────────


def test_the_security_log_follows_a_config_dir_isolation(tmp_path, monkeypatch):
    """The defect as reported: isolating ``config_dir`` did not move the security log, because
    ``sel.py`` read ``PERSONALCLAW_HOME`` itself. The variable is set to a DIFFERENT directory so
    the two answers are distinguishable."""
    from personalclaw.sel import SecurityEventLog

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "the-variable"))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path / "isolated")
    (tmp_path / "isolated").mkdir()
    assert SecurityEventLog()._dir == tmp_path / "isolated"


@pytest.mark.parametrize(
    "reader",
    [
        "personalclaw.session_search:db_path",
        "personalclaw.inbound.clients:clients_path",
        "personalclaw.inbound.audit:_audit_path",
        "personalclaw.codegraph.index:default_db_path",
        "personalclaw.snapshot:_default_snapshot_dir",
    ],
)
def test_a_home_reader_answers_what_config_dir_answers(reader, tmp_path, monkeypatch):
    """``PERSONALCLAW_HOME=~/…`` as a service file or a quoted shell writes it: ``config_dir()``
    expands it, and every reader that went ``Path(os.environ.get(…))`` instead used a directory
    literally named ``~`` under whatever the working directory was."""
    import importlib

    from personalclaw.config.loader import config_dir

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_HOME", "~/iso-home")
    monkeypatch.chdir(tmp_path)
    module, _, name = reader.partition(":")
    fn = getattr(importlib.import_module(module), name)
    answer = pathlib.Path(fn("workspace") if name == "default_db_path" else fn())
    assert config_dir() == tmp_path / "iso-home"
    assert answer.is_absolute() and answer.is_relative_to(tmp_path / "iso-home"), answer
    assert not (tmp_path / "~").exists(), "a directory named `~` appeared in the cwd"


# ── the five sites, driven ────────────────────────────────────────────────────────────────
#
# The rail above is a source scan, so on its own it proves only spelling. Each fixed site is
# also executed against a temp home here, which is what proves the behaviour changed.


@pytest.fixture
def sealed_home(tmp_path, monkeypatch):
    """A temp home with `Path.home()` ITSELF redirected, so this file cannot write to the
    operator's real home even when the production fix is reverted.

    🪤 Learned the hard way, in this module. Falsifying the `hook_register` fix by reverting
    it to `Path.home() / ".personalclaw" / "hooks.json"` made the test itself register a hook
    in the operator's REAL home — a live gateway would then have run it. The test's own
    "the real home is untouched" assertion came *after* the call, so it reported the damage
    instead of preventing it.

    Patching only `config_dir` is not enough for exactly the sites this module targets: the
    bug being tested IS "ignores `config_dir`". So the escape hatch has to be sealed too, and
    the env var is what is set — it is the isolation the product actually ships, and it moves
    every reader of the home at once.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path / "fake-user"))
    (tmp_path / "fake-user").mkdir()
    return tmp_path


def test_the_hook_registration_writes_into_the_active_home(sealed_home, monkeypatch):
    """🔑 The only WRITE among the five, and the worst of them: this created the directory and
    persisted into the operator's real home, whose gateway would then RUN the hook.
    """
    from personalclaw.mcp_core import _call_tool_inner

    monkeypatch.setattr("personalclaw.mcp_core._resolve_session_key", lambda: "test", raising=False)

    _call_tool_inner("hook_register", {"hook_id": "h1", "context_summary": "why"})

    written = sealed_home / "hooks.json"
    assert written.is_file(), "the registration did not land in the active home"
    assert "h1" in written.read_text(encoding="utf-8")
    # Nothing outside the active home, even under the sealed fake `Path.home()`.
    assert not (sealed_home / "fake-user" / ".personalclaw").exists()


def test_the_session_thread_scan_reads_the_active_home(sealed_home):
    """A cross-home READ: this globbed the real home, so a tool call in an isolated session
    picked up whichever instance wrote a pid file most recently."""
    from personalclaw.mcp_core import _current_session_thread_ts

    (sealed_home / "session_pid_123.txt").write_text("1700000000.5", encoding="utf-8")
    assert _current_session_thread_ts() == "1700000000.5"

    # A pid file in a DIFFERENT home is not visible.
    other = sealed_home / "fake-user" / ".personalclaw"
    other.mkdir(parents=True, exist_ok=True)
    (other / "session_pid_999.txt").write_text("9999999999.9", encoding="utf-8")
    assert _current_session_thread_ts() == "1700000000.5"


def test_the_installed_agent_config_path_follows_the_active_home(sealed_home):
    """Both `handlers/mcp.py` sites resolve through one helper now — and one of them
    (`_remove_from_agent_file`, since replaced by `secret_refs.remove_mcp_servers`) deleted an
    entry from the file it resolved, so pointing it at the real home was a destructive write to
    the operator's config.

    Redirected by the env var, not by patching `agent.agents_dir`. The first version of this
    test did the latter and went green while the code had ALREADY been changed away from that
    constant — a test measuring a lever the code no longer pulls.
    """
    from personalclaw.dashboard.handlers.mcp import _installed_agent_json

    assert _installed_agent_json() == sealed_home / "agents" / "personalclaw.json"


def test_mcp_discovery_reads_the_active_homes_agent_config(sealed_home, monkeypatch):
    """A dev gateway discovered the OPERATOR's MCP servers. Driven through the real
    discovery function, with a server only the temp home declares."""
    import json

    import personalclaw.mcp_discovery as disc

    agents = sealed_home / "agents"
    agents.mkdir(exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {"only-in-temp-home": {"command": "echo"}}}), encoding="utf-8"
    )
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)

    merged = disc._load_agent_config()
    assert "only-in-temp-home" in merged.get(
        "mcpServers", {}
    ), "discovery did not read the active home's installed agent config"

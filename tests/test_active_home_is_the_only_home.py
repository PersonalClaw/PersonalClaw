"""Every PersonalClaw-home path in `src/` follows the ACTIVE home (issue 287).

`PERSONALCLAW_HOME` is the isolation boundary the whole project rests on: `make serve`
defaults it to `./.dev-home`, every destructive test is required to point it at `tmp_path`,
and `--seed` refuses to run against the real home. A site that spells
`Path.home() / ".personalclaw"` outright opts out of all of that.

**This has now been found and fixed FOUR times, in four different modules, and each fix left
a comment instead of a check.** The comments are still in the tree:

* `agent.py:_bundled_hooks` — the bundled `postToolUse` hook wrote to a literal
  `~/.personalclaw/audit.log`, so *"every isolated-home session appends to the operator's
  REAL home instead of its own — a home-isolation break in a security control"* (`G31`).
* `dashboard/handlers/files.py` — the Uploads + PersonalClaw browse roots, where a dev
  gateway would *"browse AND edit the developer's REAL home via the write allowlist"* (#294).
* `dashboard/handlers/mcp.py:_canonical_mcp_json` — *"the old `Path.home()` hardcode ignored
  it"*.
* `subagent_persistence.py` — a module-level `config_dir()` constant, converted to a call.

And the fourth comment says out loud what kept happening:

    NO `_canonical_mcp_json()` constant here: it was `Path.home() / ".personalclaw" /
    "mcp.json"`, computed at import time, so it ignored PERSONALCLAW_HOME exactly as the
    comment on `_canonical_mcp_json()` (above) says the old hardcode did — **the same bug,
    fixed in one function and left in three siblings.**

Measured on `origin/main` when this rail was written: five live sites still ignored the env
var — two in `handlers/mcp.py` (including `_remove_from_agent_file`, which DELETES an entry
from the file it resolves), two in `mcp_core.py` (one of them the only *write*, with an
unconditional `mkdir`), and one in `mcp_discovery.py`. So a comment naming the pattern, four
times over, did not stop the fifth. A ratchet does.

ARCC was queried first (file access + infrastructure are trigger domains). The applicable
guidance is the *isolate data from other processes* recommendation — write "to disk under a
more restrictive and user/processes specific folder location" — plus SAX-06 Outcome 3's
append-only, least-privilege treatment of audit logs. The CloudTrail/S3/KMS material in both
documents is cloud infrastructure and does not apply to a local file; noted, not stretched.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: A literal spelling of the real PersonalClaw home. Deliberately matches only
#: `.personalclaw`: `Path.home() / ".claude.json"` and `~/.claude/agents/…` are the
#: claude-code CLI's OWN global config, which genuinely lives at the user's real home and
#: must not follow `PERSONALCLAW_HOME`.
_LITERAL_HOME = re.compile(
    r"""Path\.home\(\)\s*/\s*["']\.personalclaw|expanduser\(\s*["']~/\.personalclaw"""
)

#: Tokens that make a literal spelling legitimate: the enclosing function consults the env
#: var or the canonical resolver, so the literal is the *documented fallback* rather than the
#: answer. `config_dir()` is that resolver (env-first, with a system-directory guard).
_RESOLVER_TOKENS = ("PERSONALCLAW_HOME", "config_dir", "workspace_root")

#: How many GUARDED sites exist. A CEILING (may only shrink), and it is what makes this rail
#: honest about its own blind spot.
#:
#: The classification above is a scope heuristic: it asks whether the enclosing function
#: MENTIONS a resolver, not whether *this* expression is the fallback. Telling those apart
#: needs real dataflow. So a second hardcode added inside a function that already resolves
#: the home correctly elsewhere would read as "guarded" and pass — which is precisely the
#: "fixed in one function and left in a sibling" shape this rail exists to stop, one level in.
#:
#: Measured by mutation: reverting `mcp_core._current_session_thread_ts` to the real home
#: left the `config_dir` import in scope, so the heuristic alone stayed green. The count
#: closes that hole — a NEW literal anywhere raises it, wherever it hides.
_GUARDED_CEILING = 12

#: The sites that legitimately mean the REAL home, with the reason each one does.
#:
#: A CEILING, not a floor: shrinking it is always allowed, and adding to it requires stating
#: why the active home is the wrong answer for that site. Both entries below want the real
#: home precisely BECAUSE it is the real one — they exist to protect it.
_REAL_HOME_IS_CORRECT: dict[str, str] = {
    "seed.py": (
        "`real_home()` exists so `--seed` can REFUSE to seed the operator's real home. "
        "Resolving the active home here would defeat the guard it implements."
    ),
    "cli.py": (
        "`main_home` is compared AGAINST the active home to detect that a command is "
        "pointed at the real installation. It is the comparand, not a destination."
    ),
}


def _sites() -> list[tuple[str, int, str, bool]]:
    """Every literal-home spelling in `src/`, with whether its scope resolves the home.

    Comments and docstrings are excluded: this rail must red on *code*, and the four
    historical comments quoted above all contain the literal pattern they warn about. A
    scanner that read those as violations would be unrunnable, and one that "fixed" them by
    deleting the explanation would erase the record of why the rail exists.
    """
    found: list[tuple[str, int, str, bool]] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if not _LITERAL_HOME.search(text):
            continue
        tree = ast.parse(text)
        lines = text.splitlines()
        # Every line covered by a docstring, so a warning ABOUT the pattern is not a use.
        doc_lines: set[int] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                doc_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        funcs = [
            n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for match in _LITERAL_HOME.finditer(text):
            lineno = text[: match.start()].count("\n") + 1
            line = lines[lineno - 1]
            if lineno in doc_lines or line.lstrip().startswith("#"):
                continue
            owner = None
            for fn in funcs:
                if fn.lineno <= lineno <= (fn.end_lineno or fn.lineno):
                    if owner is None or fn.lineno > owner.lineno:
                        owner = fn
            scope = (ast.get_source_segment(text, owner) if owner else text) or ""
            resolves = any(tok in scope for tok in _RESOLVER_TOKENS)
            found.append((str(path.relative_to(SRC)), lineno, line.strip(), resolves))
    return found


def test_no_module_resolves_the_personalclaw_home_by_hardcoding_it():
    """🪤 The ratchet. A literal real-home spelling is allowed only when its own scope
    consults `PERSONALCLAW_HOME` / `config_dir()` first — i.e. it is the fallback — or when
    the module is on the allowlist because the real home is genuinely what it means.

    A new unguarded site fails here with its own file and line, which is the thing four
    prose comments could not do.
    """
    offenders = [
        f"{path}:{lineno}  {code}"
        for path, lineno, code, resolves in _sites()
        if not resolves and path not in _REAL_HOME_IS_CORRECT
    ]
    assert offenders == [], (
        "these sites resolve the PersonalClaw home by hardcoding the real one, so they "
        "ignore PERSONALCLAW_HOME and reach outside the active home:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse `config_dir()` (or `AGENTS_DIR`/`_canonical_mcp_json()` where the "
        "codebase already has a resolver for that specific file). If the REAL home is "
        "genuinely what you mean, add the module to `_REAL_HOME_IS_CORRECT` with a reason."
    )


def test_the_guarded_population_only_shrinks():
    """🪤 The half the scope heuristic cannot see (see `_GUARDED_CEILING`).

    A new hardcode inside a function that already calls `config_dir()` somewhere else passes
    the classifier. It cannot pass this: the total goes up.

    Lowering the ceiling when a site is genuinely removed is always correct. Raising it means
    a literal real-home spelling was ADDED, which needs the argument written down, not a
    bumped number.
    """
    guarded = [f"{path}:{lineno}  {code}" for path, lineno, code, resolves in _sites() if resolves]
    assert len(guarded) <= _GUARDED_CEILING, (
        f"{len(guarded)} guarded literal-home sites, ceiling is {_GUARDED_CEILING}. A new one "
        "was added inside a function that already resolves the home, which the classifier "
        "cannot distinguish from a fallback:\n  " + "\n  ".join(guarded)
    )


def test_the_allowlist_has_no_stale_entries():
    """A ceiling that keeps entries for modules that no longer have a site is how an
    allowlist stops describing the code. Each entry must still be earning its exemption."""
    paths = {path for path, _, _, _ in _sites()}
    stale = sorted(set(_REAL_HOME_IS_CORRECT) - paths)
    assert stale == [], f"allowlisted modules with no literal-home site left: {stale}"


def test_every_allowlist_entry_states_a_reason():
    """The exemption is the reason, not the entry. A bare path would let the next author
    add one without arguing that the active home is the wrong answer."""
    for module, reason in _REAL_HOME_IS_CORRECT.items():
        assert len(reason) > 40, f"{module}: exemption needs a real reason, got {reason!r}"


def test_the_scanner_actually_finds_sites():
    """🪤 Vacuity floor, and the one that matters most here.

    A ratchet whose scanner silently matches nothing is worse than no ratchet: it reports
    green forever while the class returns. A regex typo, a moved `src/` layout, or an
    `ast.parse` failure would all present as "no offenders". So the scan must keep finding
    the *guarded* sites, which are real and numerous.
    """
    sites = _sites()
    assert len(sites) >= 10, f"the scanner found only {len(sites)} sites — it has gone blind"
    assert any(resolves for *_, resolves in sites), "no guarded site found; classifier broke"


def test_the_scanner_ignores_the_historical_comments_and_docstrings():
    """The fix comments quoted in this module's docstring contain the literal pattern they
    warn about. Excluding comments and docstrings is what lets the codebase keep explaining
    itself without the rail treating the explanation as the offence.

    🪤 Asserted against modules that DEMONSTRABLY carry a matching literal in prose, which is
    the second version of this test. The first asserted `agent.py` reported no sites — true,
    but for an unrelated reason (its docstring writes `~/.personalclaw/` without
    `expanduser(`, so the regex never matched it). It passed with the exclusion deleted,
    proving nothing. Found by mutation.
    """
    sites = _sites()

    # `seed.py` has the literal in BOTH its docstring (`real_home`'s, explaining the rule)
    # and its code. Exactly one site — the code — must be reported.
    seed = [s for s in sites if s[0] == "seed.py"]
    assert len(seed) == 1, f"seed.py should report only its code line, got {seed}"
    assert "return" in seed[0][2], f"the reported seed.py line is not the code: {seed[0]}"

    # `handlers/mcp.py`'s comment spells out the old hardcode across two lines. Neither is a
    # site now that both real ones are fixed.
    mcp_text = (SRC / "dashboard" / "handlers" / "mcp.py").read_text(encoding="utf-8")
    assert 'Path.home() / ".personalclaw" /' in mcp_text, (
        "the comment recording the original bug was deleted — it is the record of why this "
        "rail exists"
    )
    assert [s for s in sites if s[0] == "dashboard/handlers/mcp.py"] == []


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
    bug being tested IS "ignores `config_dir`". So the escape hatch has to be sealed too.

    The env var is what is set, not the resolver function — because several modules
    deliberately carry their OWN env-var-first resolver rather than importing `config_dir`
    (`sel.py`, `security.py`). Patching `loader.config_dir` alone left `sel()` — which
    `_call_tool_inner` calls to audit every invocation — resolving through `Path.home()` and
    creating `.personalclaw` under the fake user. Setting `PERSONALCLAW_HOME` moves all of
    them together, which is the isolation the product actually ships.
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
    (`_remove_from_agent_file`) DELETES an entry from the file it resolves, so pointing it at
    the real home was a destructive write to the operator's config.

    Redirected by the env var, not by patching `agent.AGENTS_DIR`. The first version of this
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


def test_the_claude_cli_paths_are_not_swept_up():
    """`~/.claude.json` and `~/.claude/agents/…` are the claude-code CLI's own global
    config. They genuinely live at the user's real home and must NOT follow
    PERSONALCLAW_HOME — a rail that forced them into the active home would break MCP sync
    against that backend. Asserted so a future widening of the pattern has to notice.
    """
    mcp_py = (SRC / "dashboard" / "handlers" / "mcp.py").read_text(encoding="utf-8")
    assert 'Path.home() / ".claude.json"' in mcp_py
    assert not _LITERAL_HOME.search('Path.home() / ".claude.json"')
    assert not _LITERAL_HOME.search('Path.home() / ".claude" / "agents"')

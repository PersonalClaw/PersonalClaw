"""The keystone out-of-band enable for desktop computer use (`DCU-1`, §3 floor 1).

Five groups, one per clause of the done_when — plus one group that exists entirely because
of a hazard in the atom itself.

1. **Fail closed in every direction** — absent, unreadable, non-JSON, wrong root type,
   unknown version, unknown key, and an ``enabled`` that is not the literal ``true`` all
   resolve to OFF. A parse error must never read as enabled.
2. **The refusal** — a WHAT/WHY/FIX naming the out-of-band enable step, raised (never a
   falsy return, never a simulated success), proven through a fixture tool that calls the
   real guard.
3. **No flip surface** — not in ``_EDITABLE_CONFIG``, no route/CLI/config field names it,
   the agent-reachable path checks refuse it, and the process's own module never writes it.
4. **The reachability ratchet, and its vacuity** — ⚠️ *the population of computer-use tools
   is EMPTY today.* `DCU-4` ships the tool surface, the stdio shim and the in-gateway
   dispatch chain; `DCU-3` ships the macOS driver. So a test that literally iterated "every
   computer-use tool" would pass over nothing. This group therefore (a) states the
   population size out loud so the emptiness can never be silent, (b) proves the ratchet's
   scanner actually FLAGS an entry point that skips the guard, using a synthetic module, and
   (c) runs the ratchet over the real package, where it is armed-but-unexercised until
   `DCU-4`. Group 2's fixture tool is the same function the scanner is proven on, so the
   refusal proof and the ratchet proof are about one object rather than two.
5. **Boot audit** — the resolved source + digest + outcome reach the SEL, and the gateway's
   run loop calls the boot hook.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from personalclaw.computer_use import enable_state as ES

# ── the fixture tool ─────────────────────────────────────────────────────────
#
# A stand-in computer-use tool. The ONLY thing it shares with a real one is that it routes
# through the real guard as its first statement. It exists because DCU-1 lands the guard
# before DCU-4 lands any tool, and "every computer-use tool refuses" would otherwise be a
# claim about the empty set. Its source is also the positive case the ratchet scanner is
# proven against in group 4.


def computer_fixture_press(element_index: int) -> str:
    """Stand in for a real ``computer_click``: guard first, then the (fake) work."""
    ES.require_enabled("computer_fixture_press")
    return f"pressed element {element_index}"


def computer_fixture_press_unguarded(element_index: int) -> str:
    """The same tool with the guard forgotten — what the ratchet must catch."""
    return f"pressed element {element_index}"


_ENABLED_DOC = '{"version": 1, "enabled": true}'


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated PersonalClaw home for every keystone read (never the real one)."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.delenv(ES.ENABLE_PATH_ENV, raising=False)
    ES.reset_enable_state()
    yield tmp_path
    ES.reset_enable_state()


def _arm(home_dir: Path, document: str = _ENABLED_DOC) -> Path:
    """Write ``document`` to the keystone path and drop the cache (an operator + restart)."""
    path = home_dir / "governance" / ES.ENABLE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    ES.reset_enable_state()
    return path


# ── 1. fail closed in every direction ────────────────────────────────────────


def test_absent_enable_file_is_off(home):
    """The default posture: nothing on disk, capability off, and the detail says where it
    looked so an operator is not left guessing which path to write."""
    state = ES.active_enable_state()
    assert state.enabled is False
    assert not ES.is_enabled()
    assert str(home / "governance" / ES.ENABLE_FILENAME) in state.detail


def test_the_exact_document_arms_the_keystone(home):
    """The one shape that means yes. Without this the whole suite could pass on a guard
    that always refuses, which would prove nothing about the gate."""
    _arm(home)
    state = ES.active_enable_state()
    assert state.enabled is True
    assert ES.is_enabled()
    assert state.digest, "an armed keystone must carry a digest for tamper evidence"


@pytest.mark.parametrize(
    ("document", "needle"),
    [
        ("", "not valid JSON"),
        ("{", "not valid JSON"),
        ('{"version": 1, "enabled": true', "not valid JSON"),
        ("true", "not a JSON object"),
        ("[]", "not a JSON object"),
        ('"enabled"', "not a JSON object"),
        ('{"version": 1, "enabled": true, "apps": ["Mail"]}', "does not enforce"),
        ('{"version": 1, "enabled": true, "enabeld": true}', "does not enforce"),
        ('{"version": 2, "enabled": true}', "declares version 2"),
        ('{"enabled": true}', "declares version None"),
        ('{"version": 1, "enabled": "true"}', "not the literal true"),
        ('{"version": 1, "enabled": 1}', "not the literal true"),
        ('{"version": 1, "enabled": false}', "not the literal true"),
        ('{"version": 1}', "not the literal true"),
    ],
    ids=[
        "empty-file",
        "truncated-open-brace",
        "half-flushed-write",
        "root-is-bool",
        "root-is-list",
        "root-is-string",
        "unenforced-scope-key",
        "typo-key",
        "future-version",
        "no-version",
        "stringly-true",
        "truthy-int",
        "explicit-false",
        "missing-flag",
    ],
)
def test_every_malformed_document_reads_as_off(home, document, needle):
    """Fail closed: a parse error, a wrong shape or an unenforced key must NEVER read as
    enabled. ``empty-file`` and ``half-flushed-write`` are the reason this is a JSON
    document and not a touch-a-marker file — as a marker, both would arm the machine."""
    _arm(home, document)
    state = ES.active_enable_state()
    assert state.enabled is False, f"{document!r} armed the keystone"
    assert needle in state.detail
    assert not ES.is_enabled()


def test_unenforced_key_is_refused_rather_than_ignored(home):
    """An operator writing a scope this build cannot honour means "on, NARROWED". Honouring
    the flag while dropping the scope would grant strictly more than was asked, so the
    document is refused instead — the same reasoning as the ceiling's unknown-key abort."""
    _arm(home, '{"version": 1, "enabled": true, "only_apps": ["Mail"]}')
    assert ES.is_enabled() is False


def test_unreadable_enable_file_is_off_and_does_not_raise(home):
    """A permissions problem is a refusal, not a crash — and deliberately NOT a boot abort
    the way an unreadable ceiling is: there "no bound" would be a widening, here the
    fail-closed answer (off) is a state the system runs in perfectly well."""
    path = _arm(home)
    path.chmod(0o000)
    ES.reset_enable_state()
    try:
        state = ES.active_enable_state()
        assert state.enabled is False
        assert "could not be read" in state.detail
    finally:
        path.chmod(0o600)


def test_a_mid_run_write_cannot_arm_the_running_process(home):
    """The no-mid-run-flip property. Read once and cached, so neither a tamper nor a
    legitimate enable widens the process already running — arming costs a restart the
    operator performs and can see."""
    assert ES.is_enabled() is False
    path = home / "governance" / ES.ENABLE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_ENABLED_DOC, encoding="utf-8")  # no reset: what a live gateway sees
    assert ES.is_enabled() is False
    ES.reset_enable_state()  # the restart
    assert ES.is_enabled() is True


def test_a_mid_run_delete_cannot_disarm_the_running_process(home):
    """The same cache, stated in the other direction, so the property is about the READ and
    not about which answer happens to be narrower."""
    path = _arm(home)
    assert ES.is_enabled() is True
    path.unlink()
    assert ES.is_enabled() is True
    ES.reset_enable_state()
    assert ES.is_enabled() is False


# ── 2. the refusal: WHAT/WHY/FIX, raised, never simulated ────────────────────


def test_the_fixture_tool_refuses_when_the_keystone_is_absent(home):
    """done_when: with the enable file absent, a computer-use tool refuses with a
    WHAT/WHY/FIX message pointing to the out-of-band enable step."""
    with pytest.raises(ES.ComputerUseDisabled) as exc:
        computer_fixture_press(3)
    rendered = str(exc.value)
    for label in ("WHAT:", "WHY:", "FIX:"):
        assert label in rendered, f"the refusal is missing {label}: {rendered}"
    assert exc.value.error.code == ES.ERR_DISABLED
    assert "computer_fixture_press" in rendered, "the WHAT must name the refused tool"


def test_the_refusal_fix_line_is_a_runnable_out_of_band_instruction(home):
    """A FIX that says "enable computer use" is useless. It must name the file, the exact
    bytes, the restart, and the env override that gives a real trust root."""
    error = ES.disabled_error("computer_snapshot")
    assert str(home / "governance" / ES.ENABLE_FILENAME) in error.fix
    assert ES.ENABLE_DOCUMENT in error.fix
    assert "restart" in error.fix.lower()
    assert ES.ENABLE_PATH_ENV in error.fix
    # The WHY has to say that the in-band paths are closed BY DESIGN, or a model reads the
    # refusal as a transient failure and retries by trying to edit a setting.
    for phrase in ("prompt", "tool call", "settings"):
        assert phrase in error.why.lower(), f"the WHY does not rule out a {phrase} flip"


def test_the_guard_has_no_falsy_return_path(home):
    """A silent no-op reads to a model as "the click landed", and it then reasons forward
    from a desktop state that never changed. So ``require_enabled`` either returns the armed
    state or raises — asserted structurally, because "returns None on the unhappy path" is
    the kind of regression a behavioural test only catches at the one call site it drives."""
    module = ast.parse(Path(ES.__file__).read_text(encoding="utf-8"))
    guard = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "require_enabled"
    )
    returns = [node for node in ast.walk(guard) if isinstance(node, ast.Return)]
    assert len(returns) == 1, "a second return in the guard is a second answer to mistake"
    assert isinstance(returns[0].value, ast.Name) and returns[0].value.id == "state"
    with pytest.raises(ES.ComputerUseDisabled):
        computer_fixture_press(1)


def test_the_fixture_tool_proceeds_only_once_armed(home):
    """The mirror of the refusal test: the guard is a gate, not a wall. Without this a
    permanently-refusing stub would satisfy every other test in group 2."""
    _arm(home)
    assert computer_fixture_press(7) == "pressed element 7"
    assert ES.require_enabled("computer_fixture_press").enabled is True


def test_the_refusal_code_is_registered_in_the_error_registry():
    """Consumers branch on the code, never the prose, so the code has to be in the
    append-only registry rather than a string invented at the raise site."""
    from personalclaw.errors import ERROR_CODES

    assert ES.ERR_DISABLED in ERROR_CODES
    assert "keystone" in ERROR_CODES[ES.ERR_DISABLED]


# ── 3. no flip surface: not config, not a route, not agent-writable ──────────


def test_the_keystone_has_no_config_patch_surface():
    """§3 floor 1 forbids a config field precisely because the agent can PATCH one."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    flat = json.dumps(_EDITABLE_CONFIG, default=str)
    assert "computer_use" not in flat and "computer-use" not in flat
    assert ES.ENABLE_FILENAME not in flat


def test_there_is_no_computer_use_config_field_at_all():
    """Not merely absent from the PATCH allowlist — absent from the config object, so there
    is no field a future PATCH entry could be pointed at by accident."""
    from personalclaw.config import AppConfig

    config = AppConfig()
    assert not hasattr(config, "computer_use")
    assert "computer_use" not in json.dumps(config.to_dict(), default=str)


def _shipped_root() -> Path:
    """The whole shipped ``personalclaw`` package — every handler, CLI and provider in it."""
    return Path(ES.__file__).resolve().parents[1]


def test_only_the_keystone_module_names_the_enable_file():
    """The no-write-surface proof, by census rather than by reading the routes: if nothing in
    the shipped package but this module even MENTIONS the filename or the env var, then no
    handler, CLI command, config loader or action provider can write it."""
    naming = []
    for path in sorted(_shipped_root().rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if ES.ENABLE_FILENAME in text or ES.ENABLE_PATH_ENV in text:
            naming.append(str(path.relative_to(_shipped_root())))
    assert naming == ["computer_use/enable_state.py"], (
        "a second module names the keystone file — if it WRITES it, the out-of-band "
        f"property is gone: {naming}"
    )


def test_the_keystone_module_itself_has_no_write_path():
    """Even the owning module cannot flip the state: no open()/write_text()/mkdir/unlink
    anywhere in it. So there is no in-process code path to the switch at all, not just no
    exposed one."""
    tree = ast.parse(Path(ES.__file__).read_text(encoding="utf-8"))
    writes = sorted(
        {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
            and (node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id)
            in {"open", "write_text", "write_bytes", "mkdir", "unlink", "touch", "chmod"}
        }
    )
    assert not writes, f"the keystone module can write its own switch: {writes}"


def test_the_keystone_path_is_refused_by_the_agent_path_checks():
    """``governance/`` is in the built-in sensitive-path denylist, so every agent-reachable
    path check (action denylist, files area, bash read/write hooks) refuses the file."""
    from personalclaw.security import is_sensitive_path

    assert is_sensitive_path(f"~/.personalclaw/governance/{ES.ENABLE_FILENAME}")
    assert is_sensitive_path("~/.personalclaw/governance")


def test_the_action_denylist_refuses_to_write_the_keystone():
    from personalclaw.guardrails.denylist import check_action

    decision = check_action("bash", {"path": f"~/.personalclaw/governance/{ES.ENABLE_FILENAME}"})
    assert decision.blocked and decision.matched == "builtin:sensitive_path"


def test_the_keystone_path_is_env_overridable_for_a_real_trust_root(tmp_path, monkeypatch):
    """The only switch the agent's own uid genuinely cannot rewrite: a path outside the
    home that an operator can own as another uid and chmod 0444."""
    external = tmp_path / "operator" / "dcu.enable.json"
    external.parent.mkdir(parents=True)
    external.write_text(_ENABLED_DOC, encoding="utf-8")
    monkeypatch.setenv(ES.ENABLE_PATH_ENV, str(external))
    ES.reset_enable_state()
    try:
        assert ES.enable_file_path() == external
        assert ES.is_enabled() is True
    finally:
        ES.reset_enable_state()


def test_the_keystone_path_is_not_frozen_at_import_time(home):
    """A module-scope constant built from ``config_dir()`` would bind the real home at
    import and no fixture could reach it (a recorded landmine in this repo)."""
    assert ES.enable_file_path() == home / "governance" / ES.ENABLE_FILENAME


# ── 4. the reachability ratchet, and its vacuity ─────────────────────────────
#
# The scanner. "Dispatchable entry point" = a module-level function under
# `personalclaw/computer_use/` whose name starts with `computer_` — the tool-surface naming
# convention DESKTOP-COMPUTER-USE §2 uses for all seven tools (computer_list_apps,
# computer_snapshot, computer_click, computer_type, computer_set_value, computer_scroll,
# computer_perform_action). Stated limit: this rail catches a tool that follows the
# convention and forgets the guard; it cannot catch one that abandons the convention. That
# gap is why `test_the_packages_public_surface_is_pinned` also censuses EVERY public
# function/class in the package, so a new dispatch surface of any name still trips something.

_TOOL_PREFIX = "computer_"
_GUARD_NAMES = frozenset({"require_enabled", "is_enabled"})


def _called_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return ""


def _entry_points(source: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith(_TOOL_PREFIX)
    ]


def _guards_first(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Is the guard the FIRST thing the body executes (a leading docstring aside)?

    First, not merely present: a check that runs after the driver handle is opened is not a
    gate, and a check further down the body is indistinguishable from one in dead code.
    """
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        return False
    return any(
        isinstance(node, ast.Call) and _called_name(node) in _GUARD_NAMES
        for node in ast.walk(body[0])
    )


def _unguarded(source: str) -> list[str]:
    return [fn.name for fn in _entry_points(source) if not _guards_first(fn)]


def _package_sources() -> dict[str, str]:
    package = Path(ES.__file__).parent
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(package.glob("*.py"))}


def test_the_computer_use_tool_population_is_currently_empty():
    """⚠️ THE VACUITY MARKER for this whole group — read it before trusting the ratchet.

    `DCU-1` ships the guard; `DCU-4` ships the tool surface, the stdio shim and the
    in-gateway dispatch chain, and `DCU-3` the macOS driver. So the set of dispatchable
    computer-use entry points is EMPTY right now, and the ratchet below is
    armed-but-unexercised over the real package. This test says the size out loud so that
    emptiness can never be silent, and it REDS the moment `DCU-4` adds the first tool —
    at which point the author must bump the number here and, in the same look, satisfy the
    ratchet. Do not delete this test to make that red go away.
    """
    found = {
        name: [fn.name for fn in _entry_points(source)]
        for name, source in _package_sources().items()
    }
    populated = {name: tools for name, tools in found.items() if tools}
    total = sum(len(tools) for tools in found.values())
    assert total == 0, (
        f"the computer-use tool population is no longer empty: {populated}. Update this "
        "census to the new size and confirm test_every_computer_use_entry_point_guards_first "
        "covers each new tool."
    )


def test_the_ratchet_flags_an_entry_point_that_skips_the_guard():
    """The scanner's own efficacy proof, so the empty real population cannot make the rail
    look clean when it is merely blind. Run against the two fixture tools at the top of
    this file: the guarded one passes, its unguarded twin is flagged."""
    import inspect

    guarded = inspect.getsource(computer_fixture_press)
    unguarded = inspect.getsource(computer_fixture_press_unguarded)
    assert _entry_points(guarded), "the scanner did not even RECOGNISE a computer_* tool"
    assert _unguarded(guarded) == []
    assert _unguarded(unguarded) == ["computer_fixture_press_unguarded"]


def test_the_ratchet_flags_a_guard_that_is_not_first():
    """A late check is not a gate. Synthetic, because no real module should ever contain
    this shape — and if the scanner tolerated it, the rail would pass code that opens a
    driver handle before asking whether it is allowed to."""
    late = (
        "def computer_late(index):\n"
        '    """Guarded, but only after the work."""\n'
        "    handle = open_driver()\n"
        "    require_enabled('computer_late')\n"
        "    return handle.press(index)\n"
    )
    assert _unguarded(late) == ["computer_late"]


def test_every_computer_use_entry_point_guards_first():
    """The ratchet: any future computer-use tool that dispatches without routing through
    the keystone guard as its first check reds here. Vacuous today by construction — see
    ``test_the_computer_use_tool_population_is_currently_empty`` for why, and
    ``test_the_ratchet_flags_an_entry_point_that_skips_the_guard`` for the proof that it
    detects rather than merely matching nothing."""
    offenders = {
        name: unguarded
        for name, source in _package_sources().items()
        if (unguarded := _unguarded(source))
    }
    assert not offenders, (
        f"computer-use entry point(s) that do not call the keystone guard first: {offenders}. "
        "Every dispatchable tool must begin with enable_state.require_enabled(<tool>) — "
        "DESKTOP-COMPUTER-USE §3 floor 1 puts the keystone first in the dispatch chain."
    )


def test_the_packages_public_surface_is_pinned():
    """The second rail, covering the naming gap in the first: EVERY public function/class in
    the package is censused, so a new dispatch surface that ignores the ``computer_``
    convention still trips a test. Constants are excluded — they cannot dispatch."""
    surface = {
        name: sorted(
            node.name
            for node in ast.parse(source).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not node.name.startswith("_")
        )
        for name, source in _package_sources().items()
    }
    assert surface == {
        "__init__.py": [],
        "enable_state.py": [
            "ComputerUseDisabled",
            "EnableState",
            "active_enable_state",
            "disabled_error",
            "enable_file_path",
            "ensure_computer_use_boot",
            "is_enabled",
            "load_enable_state",
            "parse_enable_document",
            "require_enabled",
            "reset_enable_state",
        ],
    }, (
        "the computer_use package grew a public function/class. If it is a dispatchable "
        "tool, name it computer_* so the keystone ratchet covers it and call "
        "enable_state.require_enabled() first; then add it here."
    )


# ── 5. boot audit ────────────────────────────────────────────────────────────


def _boot_rows(monkeypatch) -> list[dict]:
    rows: list[dict] = []

    class _Sel:
        def log_api_access(self, **kw):
            rows.append(kw)

    import personalclaw.sel as sel_mod

    monkeypatch.setattr(sel_mod, "sel", lambda: _Sel())
    ES.ensure_computer_use_boot()
    return [r for r in rows if r.get("operation") == "computer_use.enable_boot"]


def test_boot_sel_audits_an_armed_keystone(home, monkeypatch):
    """Tamper evidence: the source + digest are recorded, so a machine that was armed — or
    whose keystone changed between runs — is attributable after the fact."""
    _arm(home)
    rows = _boot_rows(monkeypatch)
    assert rows and rows[0]["outcome"] == "enabled"
    assert ES.active_enable_state().digest in rows[0]["resources"]
    assert ES.ENABLE_FILENAME in rows[0]["resources"]


def test_boot_sel_audits_a_disabled_keystone(home, monkeypatch):
    """The off case is recorded too: "the keystone was OFF at this boot" is exactly the
    baseline a later "ENABLED" row is read against."""
    rows = _boot_rows(monkeypatch)
    assert rows and rows[0]["outcome"] == "disabled"
    assert "no enable file" in rows[0]["resources"]


def test_boot_never_raises_on_a_malformed_keystone(home, monkeypatch):
    """A typo in an operator's JSON must not take the gateway down — that is the difference
    between this hook and the governance ceiling's fail-closed abort."""
    _arm(home, "{not json")
    rows = _boot_rows(monkeypatch)
    assert rows and rows[0]["outcome"] == "disabled"


def test_gateway_boot_resolves_the_keystone_once(home):
    """The posture the whole process runs under is fixed at boot, before any service can
    dispatch — so the SEL row exists even on a run where no tool is ever called."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    source = inspect.getsource(GatewayOrchestrator.run)
    called = [
        node.func.id
        for node in ast.walk(ast.parse(source.lstrip()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "ensure_computer_use_boot" in called

"""One owner for "where is THIS instance's gateway" — and it refuses rather than guessing.

Issue #2539. ``personalclaw gateway --port N`` moved where the gateway LISTENS but not what
its children believed the server's address WAS. Each child resolved that for itself — from
``parse_dashboard_url(dashboard.url)`` or from the import-time ``config.loader.DASHBOARD_PORT``
— and both fall back to the fixed ``10000``. ``dashboard.url`` is ``""`` in a default config,
so this was the DEFAULT path, not an edge case.

The consequence is not a hang. Something is listening on ``10000``: on a multi-gateway host
that is a DIFFERENT instance, with its own home, config and state. Measured end to end on two
isolated homes before the fix — instance B (bound ``127.0.0.1:10771``) fired a ``run-script``
action whose ``ctx.notify()`` was persisted into instance A's ``notifications.jsonl`` (bound
``127.0.0.1:10772``); B's own store stayed empty, the child reported ``{'ok': True}``, and B's
``.local_secret`` travelled to A in the request header.

These tests pin both halves of the fix:

* the resolution has ONE owner (``gateway_base``) fed by the socket the gateway actually bound,
  and the rail at the bottom reds when a new site learns to resolve the base on its own;
* an unresolvable base is a loud, fast refusal that names its cause — never ``10000``. Per ARCC
  SAX-04 Outcome 5, *"failing open for security-critical operations"* is a named pitfall and a
  system must *"fail fast rather than hanging indefinitely on timeout"*.
"""

from __future__ import annotations

import ast
import json
import re
import time
from pathlib import Path

import pytest

from personalclaw import gateway_base
from personalclaw.gateway_base import GatewayBaseUnresolved

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: A pid that cannot be alive. 0 is special-cased by ``os.kill``; this is out of range for
#: every platform's pid space and is refused by the liveness check.
_DEAD_PID = 2**31 - 1


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home with the SHIPPED default: an empty ``dashboard.url``.

    ``monkeypatch.context()`` is not used because the fixture must live for the whole test;
    a bare ``monkeypatch.undo()`` inside a test would revert conftest's autouse ``config_dir``
    isolation along with it.
    """
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.delenv(gateway_base.PORT_ENV, raising=False)
    (tmp_path / "config.json").write_text('{"dashboard": {"url": ""}}', encoding="utf-8")
    return tmp_path


def _set_url(home_dir: Path, url: str) -> None:
    (home_dir / "config.json").write_text(json.dumps({"dashboard": {"url": url}}), encoding="utf-8")


# ── the owner's contract ────────────────────────────────────────────────────


class TestTheOwnerRefusesRatherThanDefaulting:
    def test_an_unresolvable_base_is_a_refusal_not_the_default_port(self, home):
        """The whole point: no env, no record, no declared port ⇒ raise, never ``10000``."""
        with pytest.raises(GatewayBaseUnresolved) as exc:
            gateway_base.resolve_port()
        assert "10000" not in str(exc.value)

    def test_the_refusal_names_every_source_it_consulted(self, home):
        """A bare "could not resolve" sends the operator reading code; name the three."""
        with pytest.raises(GatewayBaseUnresolved) as exc:
            gateway_base.resolve_port()
        message = str(exc.value)
        assert gateway_base.PORT_ENV in message
        assert str(home / gateway_base.RUNTIME_FILE) in message
        assert "dashboard.url" in message

    def test_the_refusal_is_fast(self, home):
        """ARCC SAX-04 O5: "fail fast rather than hanging indefinitely on timeout"."""
        started = time.monotonic()
        with pytest.raises(GatewayBaseUnresolved):
            gateway_base.resolve_api_base()
        assert time.monotonic() - started < 2.0

    def test_a_configured_url_without_a_port_declares_no_port(self, home):
        """``parse_dashboard_url`` substitutes 10000 here; the owner must not."""
        _set_url(home, "http://my-host.example.com")
        with pytest.raises(GatewayBaseUnresolved):
            gateway_base.resolve_port()

    def test_an_explicit_configured_port_is_honoured(self, home):
        _set_url(home, "http://localhost:6777")
        assert gateway_base.resolve_port() == 6777

    def test_the_bound_port_beats_a_stale_configured_url(self, home):
        """``--port N`` must win over whatever ``dashboard.url`` still says. This IS #2539."""
        _set_url(home, "http://localhost:10772")
        gateway_base.publish(10771)
        assert gateway_base.resolve_port() == 10771
        assert gateway_base.resolve_api_base() == "http://localhost:10771"

    def test_the_record_answers_a_child_whose_environment_was_stripped(self, home, monkeypatch):
        """A sandboxed child rebuilds env from an allowlist; the in-home record still answers."""
        gateway_base.publish(10771)
        monkeypatch.delenv(gateway_base.PORT_ENV, raising=False)
        assert gateway_base.resolve_port() == 10771

    def test_a_record_naming_a_dead_pid_is_not_trusted(self, home, monkeypatch):
        """A crashed instance's record must not address children at a port it no longer holds.

        The environment projection is cleared first: it is a SEPARATE projection of the same
        fact, and leaving it set would let it answer and hide whether the liveness gate works.
        """
        gateway_base.publish(10771, pid=_DEAD_PID)
        monkeypatch.delenv(gateway_base.PORT_ENV, raising=False)
        assert gateway_base.live_port() is None
        with pytest.raises(GatewayBaseUnresolved):
            gateway_base.resolve_port()

    def test_a_malformed_record_is_ignored_not_obeyed(self, home):
        (home / gateway_base.RUNTIME_FILE).write_text("not json", encoding="utf-8")
        assert gateway_base.live_port() is None

    def test_unpublish_withdraws_the_record(self, home):
        gateway_base.publish(10771)
        assert gateway_base.live_port() == 10771
        gateway_base.unpublish()
        assert gateway_base.live_port() is None
        gateway_base.unpublish()  # idempotent: shutdown must not fail on a second pass

    def test_publishing_an_unbound_port_is_a_hard_error(self, home):
        """A gateway that cannot name its own socket cannot address its children either.

        The old ``if self._dashboard_port:`` guard made this a silent no-op, which deferred the
        same failure to the first tool call — by which time the request had already gone
        somewhere.
        """
        for bad in (0, -1):
            with pytest.raises(ValueError):
                gateway_base.publish(bad)

    def test_a_garbage_environment_port_does_not_become_the_answer(self, home, monkeypatch):
        monkeypatch.setenv(gateway_base.PORT_ENV, "not-a-number")
        _set_url(home, "http://localhost:6777")
        assert gateway_base.resolve_port() == 6777


# ── the routed sites ───────────────────────────────────────────────────────


class TestEveryChildBaseGoesThroughTheOwner:
    def test_mcp_core_asks_the_owner(self, home):
        from personalclaw import mcp_core

        gateway_base.publish(10771)
        assert mcp_core._api_base() == "http://localhost:10771"

    def test_an_mcp_tool_returns_the_refusal_as_its_result(self, home):
        """Measured on the real child: ``get_context`` answered with this text in 3s.

        The refusal must arrive as the tool's RESULT. Built outside the try it would escape
        ``run_mcp_stdio_loop`` and take the MCP server down mid-turn instead.
        """
        from personalclaw import mcp_core

        out = mcp_core._get("/api/context")
        assert gateway_base.PORT_ENV in out["error"]
        assert "10000" not in out["error"]

    def test_the_cron_launcher_carries_the_bound_port_not_the_import_time_constant(
        self, home, monkeypatch
    ):
        """The exact path the measured leak travelled.

        ``schedule_script`` wrote ``config.loader.DASHBOARD_PORT`` into the launcher's cfg. That
        constant is evaluated at IMPORT, before the gateway binds, so it is permanently the
        pre-bind value and the launcher POSTed there.
        """
        from personalclaw import schedule_script
        from personalclaw.config.loader import DASHBOARD_PORT

        bound = DASHBOARD_PORT + 771
        gateway_base.publish(bound)

        crons = home / "crons"
        crons.mkdir(parents=True, exist_ok=True)
        script = crons / "probe.py"
        script.write_text("def run(ctx):\n    return 'ok'\n", encoding="utf-8")

        seen: dict[str, object] = {}

        def _fake_run(argv, **kwargs):
            for arg in argv:
                text = str(arg)
                if "pc-cron-cfg-" in text and text.endswith(".json"):
                    seen["cfg"] = json.loads(Path(text).read_text(encoding="utf-8"))
            raise AssertionError("stop before exec")

        monkeypatch.setattr(schedule_script.subprocess, "run", _fake_run)
        monkeypatch.setattr(schedule_script, "_crons_dir", lambda: crons)
        with pytest.raises(AssertionError):
            schedule_script.run_script_sandboxed(f"{script}:run", "job", "", 5)

        assert seen["cfg"]["port"] == bound
        assert seen["cfg"]["port"] != DASHBOARD_PORT

    def test_the_cron_run_refuses_with_a_named_cause(self, home, monkeypatch):
        from personalclaw import schedule_script

        crons = home / "crons"
        crons.mkdir(parents=True, exist_ok=True)
        script = crons / "probe.py"
        script.write_text("def run(ctx):\n    return 'ok'\n", encoding="utf-8")
        monkeypatch.setattr(schedule_script, "_crons_dir", lambda: crons)

        def _never(*a, **k):
            raise AssertionError("must not spawn without a resolved base")

        monkeypatch.setattr(schedule_script.subprocess, "run", _never)
        out = schedule_script.run_script_sandboxed(f"{script}:run", "job", "", 5)
        assert out["status"] == "error"
        assert gateway_base.PORT_ENV in out["error"]

    def test_the_acp_child_env_declares_the_bound_port(self, home):
        from personalclaw.acp.mcp_servers import core_mcp_servers

        gateway_base.publish(10771)
        env = {e["name"]: e["value"] for e in core_mcp_servers(session_key="sk")[0]["env"]}
        assert env[gateway_base.PORT_ENV] == "10771"

    def test_the_acp_child_env_declares_nothing_it_cannot_resolve(self, home):
        """Handing the child a guess is worse than handing it nothing: with nothing, the child
        refuses loudly at its first tool call instead of quietly addressing a stranger."""
        from personalclaw.acp.mcp_servers import core_mcp_servers

        env = {e["name"]: e["value"] for e in core_mcp_servers(session_key="sk")[0]["env"]}
        assert gateway_base.PORT_ENV not in env

    def test_gateway_liveness_follows_the_bound_socket(self, home):
        """`DAS-10`: probing the CONFIGURED port reported "not running" on ``--port 10188``
        while the very process asking was serving the request."""
        from personalclaw import snapshot

        assert snapshot._is_gateway_running() is False
        gateway_base.publish(10771, pid=_DEAD_PID)
        assert snapshot._is_gateway_running() is False


# ── the rail: nobody else may resolve the base ─────────────────────────────

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
    ("cli.py", "DASHBOARD_PORT"): "`--port` default for a human at a terminal",
    ("cli_server.py", "parse_dashboard_url"): "resolve_client_port: --port > env > config",
    ("cli_server.py", "_DEFAULT_PORT"): "resolve_client_port's last resort, for a CLI client",
    ("cli_setup.py", "DASHBOARD_PORT"): "prints an example URL during setup",
    ("cli_doctor.py", "parse_dashboard_url"): "displays the CONFIGURED url as a diagnostic",
    ("auth/cli.py", "_DEFAULT_PORT"): "`--port` default for the login/logout CLI",
}

#: Modules that address a CHILD of this gateway. They must ask ``gateway_base`` and nothing else.
_CHILD_BASE_MODULES = (
    "mcp_core.py",
    "mcp_shared.py",
    "schedule_script.py",
    "snapshot.py",
    "acp/mcp_servers.py",
)


def _resolution_sites(root: Path) -> set[tuple[str, str]]:
    """Every ``(module, resolver)`` pair read anywhere under *root*, from the AST.

    Attribute reads count too (``loader.DASHBOARD_PORT``), so re-importing under another name
    does not hide a site.
    """
    found: set[tuple[str, str]] = set()
    for py in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
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
        for py in SRC.rglob("*.py"):
            text_hits += len(pattern.findall(py.read_text(encoding="utf-8")))
        assert text_hits >= 30, f"only {text_hits} textual occurrences — the scan lost its corpus"
        assert len(_resolution_sites(SRC)) >= 10
        assert len(_ALLOWED) >= 10

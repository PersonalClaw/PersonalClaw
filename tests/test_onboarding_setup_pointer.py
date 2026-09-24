"""``personalclaw setup`` points at the dashboard's guided first run (OU-4 / T1.4).

The dashboard owns onboarding — it installs a model provider, binds a chat model and
runs a real first success in-flow — and the CLI wizard stays credentials-first. So the
wizard's only job here is to say where that flow is, and only where a browser can reach
it. Two things are pinned:

* :func:`personalclaw.env.browser_available` — the ONE predicate, previously inlined in
  the gateway's auto-open branch. A second copy would drift, and both failures are
  silent (a pointer nobody sees, or an instruction a headless user cannot follow).
* the CALL SITES — a helper that prints the right line is worthless if ``_setup`` never
  calls it, and the gateway's auto-open must ask the shared predicate rather than
  keeping its own copy. Both are asserted structurally, not by existence.
"""

import ast
from pathlib import Path

from personalclaw import cli_setup, env

# ── the predicate ────────────────────────────────────────────────────────────

_BROWSER_ENV = ("SSH_CONNECTION", "SSH_CLIENT", "DISPLAY", "WAYLAND_DISPLAY")


def _clear_browser_env(monkeypatch):
    for name in _BROWSER_ENV:
        monkeypatch.delenv(name, raising=False)


def test_browser_available_on_a_local_linux_session(monkeypatch):
    _clear_browser_env(monkeypatch)
    monkeypatch.setattr(env.sys, "platform", "linux")
    assert env.browser_available() is True


def test_browser_unavailable_on_a_headless_ssh_session(monkeypatch):
    """The one case with no browser: an SSH shell with no display server."""
    _clear_browser_env(monkeypatch)
    monkeypatch.setattr(env.sys, "platform", "linux")
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 51000 10.0.0.9 22")
    assert env.browser_available() is False


def test_browser_available_over_ssh_with_a_forwarded_display(monkeypatch):
    """Remote is not the question — a display is. X11 forwarding can open a browser."""
    _clear_browser_env(monkeypatch)
    monkeypatch.setattr(env.sys, "platform", "linux")
    monkeypatch.setenv("SSH_CLIENT", "10.0.0.2 51000 22")
    monkeypatch.setenv("DISPLAY", "localhost:10.0")
    assert env.browser_available() is True


def test_browser_available_over_ssh_with_wayland(monkeypatch):
    _clear_browser_env(monkeypatch)
    monkeypatch.setattr(env.sys, "platform", "linux")
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 51000 10.0.0.9 22")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert env.browser_available() is True


def test_browser_available_on_macos_even_over_ssh(monkeypatch):
    """``open(1)`` reaches the console user's browser from an SSH shell."""
    _clear_browser_env(monkeypatch)
    monkeypatch.setattr(env.sys, "platform", "darwin")
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 51000 10.0.0.9 22")
    assert env.browser_available() is True


# ── the printed line ─────────────────────────────────────────────────────────


def test_pointer_names_the_dashboard_when_a_browser_is_available(monkeypatch, capsys):
    monkeypatch.setattr(cli_setup, "browser_available", lambda: True)
    cli_setup._print_dashboard_pointer()
    out = capsys.readouterr().out
    assert "dashboard" in out
    # It has to say what is in there, or it is a signpost with no destination.
    assert "model provider" in out and "first success" in out


def test_pointer_is_suppressed_with_no_browser(monkeypatch, capsys):
    monkeypatch.setattr(cli_setup, "browser_available", lambda: False)
    cli_setup._print_dashboard_pointer()
    assert capsys.readouterr().out == ""


# ── the call sites ───────────────────────────────────────────────────────────


def _fn(module_path: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {module_path}")


def test_setup_prints_the_pointer_on_every_path_that_ends_the_wizard():
    """Both of ``_setup``'s completion paths point at the flow — including ``--agent-only``.

    A helper nothing calls is an inert control. ``_setup`` finishes in two places
    (the ``agent_only`` early return and the full run), and both send the user to the
    gateway, so both owe them the pointer.
    """
    setup = _fn(cli_setup.__file__, "_setup")
    pointer_calls = [
        n
        for n in ast.walk(setup)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_print_dashboard_pointer"
    ]
    done_prints = [
        n
        for n in ast.walk(setup)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "print"
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str)
        and n.args[0].value.startswith("\nDone!")
    ]
    assert len(done_prints) == 2, "the wizard's completion lines moved — recount the paths"
    assert len(pointer_calls) == len(done_prints)


def test_gateway_asks_the_shared_predicate_instead_of_its_own_copy():
    """The gateway's auto-open branch is the predicate's other caller, not a second copy."""
    src = Path(env.__file__).with_name("gateway.py").read_text(encoding="utf-8")
    assert "browser_available()" in src
    for leaked in ("SSH_CONNECTION", "SSH_CLIENT", "WAYLAND_DISPLAY"):
        assert leaked not in src, f"gateway.py re-derives browser availability from {leaked}"

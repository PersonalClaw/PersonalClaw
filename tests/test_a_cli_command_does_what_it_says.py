"""Five CLI commands do what they say, or say why they cannot.

* ``setup``'s "Dashboard available at" names the port this home's gateway serves: the running
  gateway's, else the one a gateway of this home binds (``dashboard.url``'s port). It named the
  import-time default, 10000, whatever ``dashboard.url`` said. A ``dashboard.url`` whose port no
  URL can hold is said on stderr, and setup finishes.
* ``app new --list-types`` counts the providers the installed apps register. The CLI process
  registered none of its own before counting, so every type read 0.
* ``memory import`` and ``security deny-list`` refuse a file that is not JSON with a sentence and
  exit 1, with nothing on stdout; each printed a traceback (``deny-list`` after the built-in list).
  An empty ``config.json`` holds no patterns of its own, as the gateway reads it.
* ``learn add`` has no ``--category``: the flag set nothing, since no lesson stores a category and
  a lesson the CLI writes fires no hook, and ``learn list`` shows no category tag.
"""

from __future__ import annotations

import argparse
import json
import re

import pytest
from fakes import gateway_stand_in

from personalclaw import cli, cli_commands


def _args(*argv: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(list(argv))


@pytest.fixture
def home(tmp_path, monkeypatch, unset_env):
    unset_env("PERSONALCLAW_PORT")
    h = tmp_path / "home"
    h.mkdir()
    (h / ".local_secret").write_text("synthetic-local-secret\n")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    return h


# ── setup's dashboard line ────────────────────────────────────────────────────


def _dashboard_line(capsys) -> str:
    from personalclaw import cli_setup

    cli_setup._maybe_setup_custom_domain()
    [line] = [x for x in capsys.readouterr().out.splitlines() if "Dashboard available at" in x]
    return line


def test_setup_names_the_port_dashboard_url_gives_the_gateway(home, capsys):
    (home / "config.json").write_text(
        json.dumps({"dashboard": {"url": "http://127.0.0.1:18765"}}), encoding="utf-8"
    )
    assert _dashboard_line(capsys).endswith(":18765")


def test_setup_names_the_port_this_homes_running_gateway_serves(home, capsys):
    with gateway_stand_in(home) as gateway:
        gateway.record()
        assert _dashboard_line(capsys).endswith(f":{gateway.port}")


def test_setup_finishes_when_dashboard_url_names_no_valid_port(home, capsys):
    """Setup saves ``dashboard.url`` as typed; a port no URL can hold is said on stderr, and setup
    goes on to its end."""
    from personalclaw import cli_setup

    (home / "config.json").write_text(
        json.dumps({"dashboard": {"url": "http://127.0.0.1:80800"}}), encoding="utf-8"
    )
    cli_setup._maybe_setup_custom_domain()
    out, err = capsys.readouterr()
    assert "Dashboard available at" not in out
    assert "dashboard.url names no valid port" in err


# ── app new --list-types ──────────────────────────────────────────────────────


def test_app_new_list_types_counts_the_providers_the_installed_apps_register(capsys):
    from personalclaw import cli_app_new

    assert cli_app_new.app_cmd(argparse.Namespace(list_types=True)) == 0
    table = capsys.readouterr().out
    counted = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^(\w+)\s+.*?\s(\d+)$", table, flags=re.MULTILINE)
    }
    assert sum(counted.values()) > 0, f"every type counted 0:\n{table}"
    registered = cli_app_new._installed_counts()
    assert counted == {t: registered.get(t, 0) for t in counted}


# ── a file that is not JSON ───────────────────────────────────────────────────


def test_memory_import_refuses_a_file_that_is_not_json(home, tmp_path, capsys):
    bad = tmp_path / "export.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        cli_commands._memory_cmd(_args("memory", "import", str(bad)))
    out, err = capsys.readouterr()
    assert (stop.value.code, out) == (1, "")
    assert "is not JSON" in err and "nothing was imported" in err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    "held, said",
    [("{broken", "could not be read as JSON"), ("[1, 2]", "is not a JSON object")],
    ids=["not JSON", "not an object"],
)
def test_security_deny_list_refuses_a_config_it_cannot_read(home, capsys, held, said):
    (home / "config.json").write_text(held, encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        cli_commands._security(_args("security", "deny-list"))
    out, err = capsys.readouterr()
    assert (stop.value.code, out) == (1, "")
    assert said in err


@pytest.mark.parametrize("held", ["", "\n"], ids=["empty", "a blank line"])
def test_security_deny_list_reads_an_empty_config_as_none(home, capsys, held):
    """The config loader reads an empty ``config.json`` as absent, so the gateway enforces the
    built-in list alone: so does the list."""
    (home / "config.json").write_text(held, encoding="utf-8")
    cli_commands._security(_args("security", "deny-list"))
    out, err = capsys.readouterr()
    assert "Built-in deny patterns" in out
    assert err == ""


def test_security_deny_list_lists_the_configured_patterns(home, capsys):
    """The floor under the refusals: a config it reads lists its patterns after the built-in."""
    (home / "config.json").write_text(
        json.dumps({"hooks": {"auto_deny_tools": ["rm -rf *"]}}), encoding="utf-8"
    )
    cli_commands._security(_args("security", "deny-list"))
    out = capsys.readouterr().out
    assert "Built-in deny patterns" in out and "  ✗ rm -rf *" in out


# ── learn ─────────────────────────────────────────────────────────────────────


def test_learn_add_takes_no_category(capsys):
    with pytest.raises(SystemExit) as stop:
        _args("learn", "add", "use snake_case", "--category", "tool")
    assert stop.value.code == 2
    assert capsys.readouterr().out == ""


def test_learn_list_shows_a_lesson_without_a_category(capsys):
    cli_commands._learn(_args("learn", "add", "Prefer short replies"))
    capsys.readouterr()
    cli_commands._learn(_args("learn", "list"))
    out = capsys.readouterr().out
    assert "  Prefer short replies\n" in out
    assert "[knowledge]" not in out

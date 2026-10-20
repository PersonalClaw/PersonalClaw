"""`personalclaw doctor` reads the agent config and reports; it writes none of it.

The agent runtime config, ``agents/personalclaw.json``, names the servers the agent launches
(``mcpServers``), the tools it is offered (``tools``) and the tools it runs without asking
(``allowedTools``). Doctor used to rewrite it whenever PersonalClaw's own server was missing from
any of the three: it put ``@personalclaw-core`` back into ``tools`` and ``allowedTools``, rewrote a
command path it judged stale, printed "Auto-fixed", and dropped the issue from its summary, though
a missing server entry was never added. So a tool the owner had taken out of ``allowedTools`` ran
without asking again after one diagnostic run, and the issue filter dropped unrelated issues too.

What holds now:

* a run leaves the file byte for byte and reports each place the server is missing from;
* a missing server entry, or a command that is not a program on this machine, is an issue the run
  exits by, and it names its repair: the Doctor page's confirm-gated Fix;
* that Fix writes the server entry and its command, and adds nothing to either list;
* no run, and no Fix, reports a repair it did not make.

Every test here runs on a scratch ``HOME`` and a scratch PersonalClaw home, with the agent config
written in the shape the code parses, so no real agent CLI's configuration is ever read.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.resilience import doctor, fixes
from personalclaw.resilience.doctor import DoctorContext

CORE = "personalclaw-core"
REF = "@personalclaw-core"
#: The Fix's wire id, pinned as the string a client sends.
FIX_ID = "tools.restore-core-server"
#: The Doctor check's id, pinned the same way.
CHECK_ID = "tools.core_server"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A scratch ``HOME`` and PersonalClaw home, and a stand-in for this install's command.

    The stand-in is a script nothing runs: the server entry only names it. Every place an agent
    CLI keeps its own configuration points at an empty scratch folder.
    """
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        empty = tmp_path / f"empty-{var.lower()}"
        empty.mkdir()
        monkeypatch.setenv(var, str(empty))
    pc_home = tmp_path / "pc-home"
    pc_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc_home))
    stand_in = tmp_path / "bin" / "personalclaw"
    stand_in.parent.mkdir()
    stand_in.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stand_in.chmod(0o755)
    monkeypatch.setattr("personalclaw.agent._PERSONALCLAW_BIN", str(stand_in))
    return SimpleNamespace(
        root=tmp_path,
        pc=pc_home,
        bin=stand_in,
        config=pc_home / "agents" / "personalclaw.json",
    )


def _write_config(
    path: Path, *, entry: object, tools: list[str], allowed: list[str], raw: str | None = None
) -> bytes:
    """The agent runtime config, in the shape ``rebuild_agent_config`` writes and doctor parses.

    A second server rides along with an ``autoApprove`` list of its own, so a repair that touched
    anything but PersonalClaw's server would show. Its command can never resolve.
    """
    servers: dict[str, object] = {
        "notes": {
            "command": "/nonexistent/pc-fixture-notes-mcp",
            "args": ["--stdio"],
            "autoApprove": ["read_note"],
        }
    }
    if entry is not None:
        servers[CORE] = entry
    document = {
        "name": "personalclaw",
        "description": "Fixture agent",
        "prompt": "file:///nonexistent/pc-fixture/prompt.md",
        "tools": tools,
        "allowedTools": allowed,
        "mcpServers": servers,
    }
    data = (raw if raw is not None else json.dumps(document, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _set_up(home: SimpleNamespace) -> dict[str, object]:
    return {"command": str(home.bin), "args": ["mcp-core"]}


def _stale(home: SimpleNamespace) -> dict[str, object]:
    return {"command": str(home.root / "gone" / "personalclaw"), "args": ["mcp-core"]}


def _run_doctor(capsys: pytest.CaptureFixture[str]) -> tuple[object, str]:
    """Run the real ``personalclaw doctor`` with its host probes stubbed; its exit code and report.

    The stubs are the ones the suite's other doctor tests use for a clean run, and the healthy
    case below proves this harness exits 0, so a 1 here is the agent config's doing.
    """
    from personalclaw.cli_doctor import _doctor

    host = MagicMock(returncode=0, stdout="v22.12.0", stderr="")
    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
        patch("subprocess.run", return_value=host),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
        patch("personalclaw.ffmpeg_binary.find_ffmpeg", return_value="/usr/local/bin/ffmpeg"),
    ):
        code: object = 0
        try:
            _doctor()
        except SystemExit as exc:
            code = exc.code
    return code, capsys.readouterr().out


def _section(out: str, title: str) -> str:
    assert f"\n{title}\n" in out, out
    return out.split(f"\n{title}\n", 1)[1].split("\n\n", 1)[0]


def _summary(out: str) -> str:
    return out.rstrip().rsplit("\n", 1)[-1]


def _untouched(home: SimpleNamespace, before: bytes, stat_before: object, listing: list[str]):
    assert home.config.read_bytes() == before, "the agent config was rewritten"
    assert home.config.stat().st_mtime_ns == stat_before.st_mtime_ns  # type: ignore[attr-defined]
    assert sorted(p.name for p in home.config.parent.iterdir()) == listing


# ── the run reads and reports ──────────────────────────────────────────────────


@pytest.mark.parametrize("case", ["no-server-entry", "stale-command"])
def test_doctor_leaves_the_agent_config_byte_for_byte(home, capsys, case):
    """Missing from ``tools``, from ``allowedTools`` and from ``mcpServers`` (or there with a
    command that is gone): the file is unchanged, every row is reported, and the exit is 1."""
    entry = None if case == "no-server-entry" else _stale(home)
    before = _write_config(home.config, entry=entry, tools=["@notes"], allowed=["@notes"])
    stat_before = home.config.stat()
    listing = sorted(p.name for p in home.config.parent.iterdir())

    code, out = _run_doctor(capsys)

    _untouched(home, before, stat_before, listing)
    rows = _section(out, "MCP Tools")
    if case == "no-server-entry":
        missing = "agents/personalclaw.json has no entry for PersonalClaw's own server"
        assert f"{REF}: ❌ {missing}" in rows, rows
    else:
        gone = home.root / "gone" / "personalclaw"
        assert f"{REF}: ❌" in rows and f"starts {gone}, which is not a program" in rows, rows
    assert f"tools:       does not list {REF}" in rows, rows
    assert f"allowedTools: does not list {REF}" in rows, rows
    assert "Settings → Doctor → Tools has a Fix" in rows, rows
    assert code == 1, out
    assert f"{REF} server entry" in _summary(out), out


def test_a_set_up_server_passes_and_the_file_is_unchanged(home, capsys):
    """The control for the harness: with the server set up, the same run exits 0, so the 1 above
    is the server entry's. The file is left as it is here too."""
    before = _write_config(home.config, entry=_set_up(home), tools=[REF], allowed=[REF])
    stat_before = home.config.stat()
    listing = sorted(p.name for p in home.config.parent.iterdir())

    code, out = _run_doctor(capsys)

    _untouched(home, before, stat_before, listing)
    rows = _section(out, "MCP Tools")
    assert f"{REF}: ✅ PersonalClaw's server in agents/personalclaw.json starts {home.bin}" in rows
    assert f"tools:       lists {REF}" in rows, rows
    assert f"allowedTools: lists {REF}, so its tools run without asking" in rows, rows
    assert code in (0, None), out
    assert "✅ PersonalClaw is ready!" in out


def test_a_server_left_out_of_allowed_tools_is_the_owners_choice_not_an_issue(home, capsys):
    """Taking the server out of ``allowedTools`` is how its tools stop running without asking. The
    run says where it stands, adds it nowhere, and passes."""
    before = _write_config(home.config, entry=_set_up(home), tools=[REF], allowed=["@notes"])
    stat_before = home.config.stat()
    listing = sorted(p.name for p in home.config.parent.iterdir())

    code, out = _run_doctor(capsys)

    _untouched(home, before, stat_before, listing)
    rows = _section(out, "MCP Tools")
    assert f"allowedTools: does not list {REF}" in rows, rows
    assert "they are yours to change" in rows, rows
    assert code in (0, None), out
    assert "✅ PersonalClaw is ready!" in out


def test_a_missing_server_entry_is_never_reported_fixed(home, capsys, monkeypatch):
    """No line claims a repair, the issue stays in the summary, and so does every other issue: the
    old claim cleared any issue whose name held "config", a misconfigured dashboard URL too."""

    def _unparsable(_url: str) -> tuple[str, int]:
        raise ValueError("not a URL")

    monkeypatch.setattr("personalclaw.cli_doctor.parse_dashboard_url", _unparsable)
    before = _write_config(home.config, entry=None, tools=[REF], allowed=[REF])

    code, out = _run_doctor(capsys)

    assert home.config.read_bytes() == before
    for claim in ("Auto-fixed", "fixed stale path", "🔧"):
        assert claim not in out, out
    assert code == 1, out
    summary = _summary(out)
    assert f"{REF} server entry" in summary and "dashboard URL misconfigured" in summary, summary


def test_with_no_command_to_restore_the_row_says_there_is_no_automatic_fix(
    home, capsys, monkeypatch
):
    """When this install's command cannot be found, no Fix can act, so none is offered: the row
    says so and what to do, and the Fix itself refuses and changes nothing."""
    from personalclaw.agent import _MANAGED_MCP_SERVERS

    monkeypatch.setitem(_MANAGED_MCP_SERVERS[CORE], "command_fn", lambda: "personalclaw")
    before = _write_config(home.config, entry=None, tools=[REF], allowed=[REF])

    code, out = _run_doctor(capsys)

    rows = _section(out, "MCP Tools")
    assert "No automatic fix" in rows and "personalclaw command was not found" in rows, rows
    assert "has a Fix" not in rows, rows
    assert code == 1, out
    result = fixes.apply_fix(FIX_ID)
    assert result["ok"] is False and "not found" in result["error"], result
    assert home.config.read_bytes() == before


# ── the Doctor page: one reading, one confirm-gated Fix ─────────────────────────────────


def _check(home: SimpleNamespace) -> dict:
    report = asyncio.run(doctor.run_capability("tools", DoctorContext(home=home.pc)))
    (row,) = [p for p in report["probes"] if p["id"] == CHECK_ID]
    return row


def test_the_doctor_check_reads_the_same_file_and_offers_the_fix(home):
    before = _write_config(home.config, entry=_stale(home), tools=[REF], allowed=[])
    row = _check(home)
    assert row["ok"] is False and row["fix_id"] == FIX_ID, row
    assert "which is not a program on this machine" in row["detail"], row
    assert home.config.read_bytes() == before, "the check wrote the agent config"
    assert fixes.get_fix(FIX_ID) is not None, "the check offers a Fix the registry does not have"

    _write_config(home.config, entry=_set_up(home), tools=[REF], allowed=[])
    assert _check(home)["ok"] is True


def test_an_unreadable_config_is_left_alone_and_says_so(home):
    """A file that is not JSON is the owner's to judge: no Fix is offered, and none writes it."""
    before = _write_config(home.config, entry=None, tools=[], allowed=[], raw="{ not json\n")
    row = _check(home)
    assert row["ok"] is False and "fix_id" not in row, row
    assert row["remedy"].startswith("No automatic fix"), row
    # The next start rebuilds it from the shipped defaults, which list the server among the tools
    # that run without asking: the owner is told before it happens.
    from personalclaw.agent import get_shipped_tools

    assert REF in get_shipped_tools()["allowedTools"], "the premise of the sentence changed"
    assert "lets PersonalClaw's own tools run without asking" in row["remedy"], row
    assert fixes.apply_fix(FIX_ID)["ok"] is False
    assert home.config.read_bytes() == before


def test_a_home_with_no_agent_config_has_nothing_to_check(home, capsys):
    assert not home.config.exists()
    row = _check(home)
    assert row["ok"] is True and "the gateway writes it as it starts" in row["detail"], row
    from personalclaw.cli_doctor import _doctor_core_server

    # A file gone between the run's Agent row and this read: nothing to check, nothing to fix.
    assert _doctor_core_server(home.config) == []
    assert f"{REF}: ⏹  there is no agents/personalclaw.json" in capsys.readouterr().out
    assert not home.config.exists(), "the check created the agent config"


def _post_fix(body: dict | None, monkeypatch: pytest.MonkeyPatch) -> object:
    """``POST /api/doctor/fix/{id}``, the Doctor page's own door."""
    from personalclaw.dashboard.handlers import doctor as handlers

    monkeypatch.setattr(handlers, "_resilience_cfg", lambda: SimpleNamespace(doctor_enabled=True))
    if body is None:
        request = make_mocked_request(
            "POST", f"/api/doctor/fix/{FIX_ID}", match_info={"fix_id": FIX_ID}
        )
    else:

        async def _json() -> dict:
            return body

        request = MagicMock()
        request.match_info = {"fix_id": FIX_ID}
        request.json = _json
    return asyncio.run(handlers.api_doctor_fix_apply(request))


@pytest.mark.parametrize("case", ["no-server-entry", "stale-command"])
def test_the_fix_writes_only_what_was_asked_and_never_allowed_tools(home, monkeypatch, case):
    """The Fix puts back the server entry and its command, keeps the rest of an existing entry,
    and leaves ``tools``, ``allowedTools`` and every other server exactly as they were."""
    entry = None if case == "no-server-entry" else {**_stale(home), "autoApprove": []}
    before = _write_config(home.config, entry=entry, tools=["@notes"], allowed=["@notes"])
    was = json.loads(before)

    preview = fixes.get_fix(FIX_ID).dry_preview()  # type: ignore[union-attr]
    assert home.config.read_bytes() == before, "the preview wrote the agent config"
    assert f"{home.bin} mcp-core" in preview and "allowedTools" in preview, preview

    refused = _post_fix(None, monkeypatch)
    assert refused.status == 400  # type: ignore[attr-defined]
    assert b"confirm_required" in refused.body  # type: ignore[attr-defined]
    assert home.config.read_bytes() == before, "an unconfirmed request changed the agent config"

    applied = _post_fix({"confirm": True}, monkeypatch)
    assert applied.status == 200  # type: ignore[attr-defined]
    answer = json.loads(applied.body)  # type: ignore[attr-defined]
    assert answer["ok"] is True and f"{home.bin} mcp-core" in answer["result"], answer

    now = json.loads(home.config.read_text(encoding="utf-8"))
    expected = {"command": str(home.bin), "args": ["mcp-core"]}
    if case == "stale-command":
        expected["autoApprove"] = []
    assert now["mcpServers"][CORE] == expected
    assert now["tools"] == was["tools"] and now["allowedTools"] == was["allowedTools"]
    assert REF not in now["allowedTools"] and REF not in now["tools"]
    assert {k: v for k, v in now["mcpServers"].items() if k != CORE} == {
        k: v for k, v in was["mcpServers"].items() if k != CORE
    }
    assert {k: v for k, v in now.items() if k != "mcpServers"} == {
        k: v for k, v in was.items() if k != "mcpServers"
    }
    assert _check(home)["ok"] is True

    again = fixes.apply_fix(FIX_ID)
    assert again["ok"] is True and again["result"].startswith("Nothing changed: "), again


# ── the rail: the doctor module calls no writer ───────────────────────────────────────

#: Calls that change a file: by name, and as a method or module attribute.
_WRITERS_BY_NAME = frozenset(
    {
        "atomic_write",
        "atomic_json_write",
        "atomic_write_bytes",
        "write_mcp_document",
        "mutate_config",
        "mutate_config_async",
        "update_config",
        "apply_fix",
        "rebuild_agent_config",
        "open",
    }
)
_WRITERS_BY_ATTRIBUTE = frozenset(
    {
        "write_text",
        "write_bytes",
        "unlink",
        "rmtree",
        "mkdir",
        "makedirs",
        "touch",
        "symlink_to",
        "rename",
        "chmod",
        "save",
        "dump",
        "move",
        "copyfile",
        "copy2",
        "copytree",
        *_WRITERS_BY_NAME,
    }
)


def _writer_calls(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _WRITERS_BY_NAME:
            found.append(f"{func.id}():{node.lineno}")
        elif isinstance(func, ast.Attribute) and func.attr in _WRITERS_BY_ATTRIBUTE:
            found.append(f".{func.attr}():{node.lineno}")
    return found


def test_the_doctor_module_calls_no_writer():
    """``personalclaw doctor`` is a check: nothing in its module writes. A repair is the Doctor
    page's confirm-gated Fix (``resilience/fixes``). ``--rebuild-routing-stats`` is a refold the
    owner asks for by name, and it writes through ``routing.stats``, not here."""
    import personalclaw.cli_doctor as cli_doctor

    source = Path(inspect.getsourcefile(cli_doctor) or "").read_text(encoding="utf-8")
    assert _writer_calls(source) == []


def test_the_rail_sees_the_writes_it_exists_to_refuse():
    """The positive control: the rewrite this module used to make, and a plain file write, are
    caught; a string's ``replace`` is not a write."""
    caught = _writer_calls(
        "atomic_write(agent_path, json.dumps(data))\n"
        "path.write_text('x')\n"
        "label = str(key).replace('_', ' ')\n"
    )
    assert caught == ["atomic_write():1", ".write_text():2"], caught

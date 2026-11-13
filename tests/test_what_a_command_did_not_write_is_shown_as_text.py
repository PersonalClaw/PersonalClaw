"""What a command prints that PersonalClaw did not write reaches the terminal as text.

A subagent's reply, a saved memory, a lesson, an automation's text, a security finding or event,
a skill's or a project archive's text, what a host on the network advertises, an evaluation's
report, a template's file names and a provider's, a vector store's or an agent CLI's error are
words a model, a store, another machine or another program chose. A terminal acts on some
characters rather than showing them. Each command writes them through
``security.terminal_text``, which shows every such character as a visible escape (``\\x1b``) and
keeps line breaks, tabs and every other character as they came, and ``cli.main`` sends every
write to the streams through it too. The chat and the headless run do the same
(``test_cli_chat.py``, ``test_cli_run.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import sys
import urllib.error
from types import SimpleNamespace

import pytest

from personalclaw import cli, cli_app_new, cli_commands, cli_doctor, cli_project, home_gateway
from personalclaw.security import mask_child_output, terminal_line, terminal_text

pytestmark = pytest.mark.security

#: Characters a terminal acts on rather than shows, around a tab, which is ordinary text.
ACTS = "ok\x1b\rdone\tx\u202eend"
#: The same words as the terminal is given them: each of those characters as a visible escape.
SHOWN = "ok\\x1b\\rdone\tx\\u202eend"
#: What must never reach the terminal raw.
RAW = ("\x1b", "\r", "\u202e")


def _as_text(printed: str) -> None:
    assert SHOWN in printed, repr(printed)
    assert not any(ch in printed for ch in RAW), repr(printed)


# ── The rule ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("given", "shown"),
    [
        ("\x1b", "\\x1b"),
        ("a\rb", "a\\rb"),
        ("a\bb\x07", "a\\x08b\\x07"),
        ("\x7f", "\\x7f"),
        ("\x9b", "\\x9b"),
        ("\x85", "\\x85"),
        ("\u202a\u202b\u202c\u202d\u202e", "\\u202a\\u202b\\u202c\\u202d\\u202e"),
        ("\u2066\u2067\u2068\u2069", "\\u2066\\u2067\\u2068\\u2069"),
        ("\u2028\u2029", "\\u2028\\u2029"),
        ("\x00", "\\x00"),
    ],
)
def test_each_character_a_terminal_acts_on_is_shown_as_an_escape(given, shown):
    assert terminal_text(given) == shown


@pytest.mark.parametrize(
    "kept",
    [
        "line one\nline two",
        "a\tb",
        "no\u00a0break",
        "\U0001f469\u200d\U0001f4bb",
        "中文",
        "\u200f\u200e",
        "a back\\slash",
    ],
)
def test_ordinary_text_is_kept_as_it_came(kept):
    """VACUITY: line breaks, tabs, a no-break space, a zero-width joiner, CJK and the two
    direction marks are ordinary text, and stay."""
    assert terminal_text(kept) == kept


def test_a_crlf_is_the_line_break_it_is():
    assert terminal_text("one\r\ntwo\r") == "one\ntwo\\r"


# ── spawn ────────────────────────────────────────────────────────────────────────


class _Answer:
    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body).encode()

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


_GATEWAY = SimpleNamespace(url="http://127.0.0.1:1")


def _the_gateway_answers(monkeypatch, *bodies: dict) -> None:
    answers = iter(bodies)
    monkeypatch.setattr(home_gateway, "open_loopback", lambda req, timeout: _Answer(next(answers)))
    monkeypatch.setattr(cli_commands._time, "sleep", lambda secs: None)


def test_a_subagents_reply_is_shown_as_text(monkeypatch, capsys):
    _the_gateway_answers(monkeypatch, {"id": "a1", "task": "t"}, {"done": True, "result": ACTS})
    cli_commands._spawn_run(argparse.Namespace(task="t", fire_and_forget=False), _GATEWAY, "k")
    _as_text(capsys.readouterr().out)


def test_a_subagents_error_is_shown_as_text(monkeypatch, capsys):
    _the_gateway_answers(monkeypatch, {"id": "a1", "task": "t"}, {"done": True, "error": ACTS})
    with pytest.raises(SystemExit):
        cli_commands._spawn_run(argparse.Namespace(task="t", fire_and_forget=False), _GATEWAY, "k")
    _as_text(capsys.readouterr().err)


def test_a_subagents_task_is_shown_as_text(monkeypatch, capsys):
    """A task an agent spawned is words its model chose, in the list and when it starts."""
    _the_gateway_answers(
        monkeypatch,
        {"agents": [{"id": "a1", "done": True, "task": ACTS}]},
        {"id": "a2", "task": ACTS},
    )
    cli_commands._spawn_list(_GATEWAY, "k")
    cli_commands._spawn_run(argparse.Namespace(task="t", fire_and_forget=True), _GATEWAY, "k")
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 1, out
    assert out.endswith("a2\n"), "`spawn run --async` prints the id alone"


@pytest.mark.parametrize("body", ["json", "plain"])
@pytest.mark.parametrize(
    "asks",
    [
        lambda: cli_commands._spawn_list(_GATEWAY, "k"),
        lambda: cli_commands._spawn_run(
            argparse.Namespace(task="t", fire_and_forget=True), _GATEWAY, "k"
        ),
    ],
    ids=["list", "run"],
)
def test_the_gateways_refusal_is_shown_as_text(asks, body, monkeypatch, capsys):
    """The gateway's refusal is said in its own words: its answer's error, or the reason its
    status line gives when the answer is not JSON."""

    def refused(req, timeout):
        if body == "json":
            answer, reason = io.BytesIO(json.dumps({"error": ACTS}).encode()), "Bad"
        else:
            answer, reason = io.BytesIO(b"not json"), ACTS
        raise urllib.error.HTTPError("http://127.0.0.1:1/api/spawn", 400, reason, {}, answer)  # type: ignore[arg-type]  # noqa: E501

    monkeypatch.setattr(home_gateway, "open_loopback", refused)
    with pytest.raises(SystemExit):
        asks()
    _as_text(capsys.readouterr().err)


@pytest.mark.parametrize("fails", ["reach", "mint"])
def test_spawns_gateway_errors_are_shown_as_text(fails, monkeypatch, capsys):
    """What this home's gateway answered when it could not be reached or sign the command in is
    said as text, as chat and run say it."""

    def reach(port=None):
        if fails == "reach":
            raise home_gateway.GatewayError(ACTS)
        return _GATEWAY

    def mint(gateway):
        raise cli_commands.RunError(ACTS)

    monkeypatch.setattr(home_gateway, "reach", reach)
    monkeypatch.setattr(cli_commands, "mint_local_token", mint)
    with pytest.raises(SystemExit):
        cli_commands._spawn(argparse.Namespace(spawn_action="list", port=None))
    _as_text(capsys.readouterr().err)


# ── memory, lessons and the security audit ───────────────────────────────────────


class _Memory:
    def get_all_semantic(self) -> list[dict]:
        return [{"key": ACTS, "value_json": json.dumps(ACTS), "confidence": 1.0, "source": ACTS}]

    def search_episodic(self, query_text: str, limit: int) -> list[dict]:
        return [{"text": ACTS, "importance": 0.5, "tags": [ACTS]}]

    def get_lessons(self) -> list[dict]:
        return [{"value_json": json.dumps(ACTS)}]


@pytest.mark.parametrize("action", ["list", "search"])
def test_a_saved_memory_is_shown_as_text(action, capsys):
    """What memory holds is words a model wrote."""
    cli_commands._memory_action(action, argparse.Namespace(query="q"), None, _Memory())
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) >= 2, out


_FINDING = {"type": "semantic", "key": ACTS, "warning": "flagged", "value": ACTS}


def test_a_flagged_memory_is_shown_as_text(monkeypatch, capsys):
    """A flagged entry is shown the same way."""
    monkeypatch.setattr(cli_commands, "scan_memory", lambda: [_FINDING])
    cli_commands._memory_action("audit", argparse.Namespace(), None, None)
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 2, out


def test_the_security_audit_shows_what_it_found_as_text(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli_commands, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        cli_commands,
        "scan_history",
        lambda history: [{"file": ACTS, "warning": "a denied call", "snippet": ACTS}],
    )
    monkeypatch.setattr(cli_commands, "scan_memory", lambda: [_FINDING])
    cli_commands._security(argparse.Namespace(sec_action="audit"))
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 4, out


def test_a_security_events_command_and_error_are_shown_as_text(monkeypatch, capsys):
    """A shell call's command is words a model chose, and so is a tool's name; the event's error
    and reason quote them, and a row an older version wrote can hold anything."""
    event = {
        "timestamp": ACTS,
        "event_type": ACTS,
        "operation": ACTS,
        "outcome": ACTS,
        "source": ACTS,
        "caller_identity": ACTS,
        "resources": ACTS,
        "error": ACTS,
        "metadata": {"reason": ACTS},
        "downstream_service": ACTS,
    }
    monkeypatch.setattr(cli_commands, "sel", lambda: SimpleNamespace(recent=lambda limit: [event]))
    monkeypatch.setattr(cli_commands, "redact_event", lambda e: e)
    cli_commands._security(argparse.Namespace(sec_action="events", limit=1))
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 10, out


def test_a_lesson_is_shown_as_text(monkeypatch, capsys):
    from personalclaw import memory_locality
    from personalclaw.memory_service import MemoryService

    part = SimpleNamespace(is_global=True)

    @contextlib.contextmanager
    def record_stores(parts, **kwargs):
        yield [(part, object())]

    monkeypatch.setattr(memory_locality, "partitions", lambda: [part])
    monkeypatch.setattr(memory_locality, "record_stores", record_stores)
    monkeypatch.setattr(MemoryService, "over_vector_store", staticmethod(lambda store: _Memory()))
    cli_commands._learn(argparse.Namespace(learn_action="list"))
    _as_text(capsys.readouterr().out)


# ── doctor ───────────────────────────────────────────────────────────────────────


def test_a_providers_failed_connection_is_shown_as_text():
    """A connection test's words can quote the provider's own answer."""
    from personalclaw.providers.connection import FAILED

    reading = cli_doctor._GatewayReading(
        port=1, providers={"p": {"connection": {"state": FAILED, "detail": ACTS}}}
    )
    _as_text(cli_doctor._connection_row("p", reading))


def _raises(entry):
    raise RuntimeError(ACTS)


@pytest.mark.parametrize(
    "readiness", [lambda entry: {"state": "error", "detail": ACTS}, _raises], ids=["said", "raised"]
)
def test_an_agent_clis_readiness_is_shown_as_text(readiness, monkeypatch, capsys):
    """An agent CLI's test can quote what the CLI itself printed, and so can a check that
    raised."""
    from personalclaw.agents import runtime_tests

    monkeypatch.setattr(runtime_tests, "readiness", readiness)
    monkeypatch.setattr(runtime_tests, "adapter_install", lambda entry: None)
    issues: list[str] = []
    cli_doctor._report_acp_agent(object(), "cli", issues, start=False)
    _as_text(capsys.readouterr().out)


# ── the rule, whole ranges ───────────────────────────────────────────────────────

#: Every code point the rule writes as an escape.
_ACTED_ON = {
    *range(0x00, 0x09),
    *range(0x0B, 0x20),
    *range(0x7F, 0xA0),
    0x2028,
    0x2029,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
}


def test_the_rule_escapes_its_ranges_whole_and_nothing_else():
    """Each code point in the rule's ranges is an escape, every other one is kept: a narrower
    range, or a wider one, fails here. The line feed and the tab are not in the set."""
    wrong = []
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF or cp == 0x0D:  # a carriage return is \\r (tested above)
            continue
        ch = chr(cp)
        want = (f"\\x{cp:02x}" if cp < 0x100 else f"\\u{cp:04x}") if cp in _ACTED_ON else ch
        if terminal_text(ch) != want:
            wrong.append(hex(cp))
    assert not wrong, wrong[:20]


def test_a_childs_output_on_one_line_follows_the_same_ranges():
    """The one-line form of a child's output escapes the same ranges, and its line feed too."""
    acted = sorted(_ACTED_ON | {0x0A})
    printed = "".join(chr(cp) for cp in acted)
    shown = mask_child_output(printed, limit=None)
    assert not any(chr(cp) in shown for cp in acted), repr(shown[:80])


# ── automations ──────────────────────────────────────────────────────────────────


def _a_store(monkeypatch, tmp_path, rows):
    from personalclaw.triggers import schedule_view, store

    class _Store:
        def __init__(self, base_dir=None):
            pass

        def load(self):
            return rows

    monkeypatch.setattr(store, "TriggerStore", _Store)
    monkeypatch.setattr(store, "unreadable", lambda s: None)
    monkeypatch.setattr(schedule_view, "to_schedule_row", lambda t: {"name": ACTS, "message": ACTS})
    monkeypatch.setattr(schedule_view, "describe_cadence", lambda t: "daily")
    monkeypatch.setattr(cli_commands, "config_dir", lambda: tmp_path)


def test_an_automations_text_is_listed_as_text(monkeypatch, tmp_path, capsys):
    """An automation a model made, or a row the store could not read, is listed as text: its id,
    its name, its parse error, and its message."""
    trigger = SimpleNamespace(id=ACTS, name=ACTS, enabled=True)
    ok = SimpleNamespace(trigger=trigger, ok=True, errors=[])
    broken = SimpleNamespace(trigger=trigger, ok=False, errors=[SimpleNamespace(message=ACTS)])
    _a_store(monkeypatch, tmp_path, [ok, broken])
    cli_commands._cron(argparse.Namespace(cron_action="list"))
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 6, out


@pytest.mark.parametrize(("action", "ok"), [("pause", True), ("resume", False)])
def test_what_a_pause_or_resume_says_is_shown_as_text(action, ok, monkeypatch, tmp_path, capsys):
    """What pausing or resuming says quotes the automation's own name."""
    from personalclaw.triggers import tools

    _a_store(monkeypatch, tmp_path, [])
    monkeypatch.setattr(
        tools, "set_paused", lambda store, **kw: SimpleNamespace(ok=ok, text=f"Paused t1 ({ACTS}).")
    )
    if ok:
        cli_commands._cron(argparse.Namespace(cron_action=action, job_id="t1"))
        _as_text(capsys.readouterr().out)
    else:
        with pytest.raises(SystemExit):
            cli_commands._cron(argparse.Namespace(cron_action=action, job_id="t1"))
        _as_text(capsys.readouterr().err)


# ── skills ───────────────────────────────────────────────────────────────────────


def test_an_installed_skills_name_and_description_are_shown_as_text(monkeypatch, capsys):
    from personalclaw.skills import marketplace

    monkeypatch.setattr(
        marketplace, "list_local_skills", lambda: [{"name": ACTS, "description": ACTS}]
    )
    cli._handle_skills(argparse.Namespace(skills_command="list"))
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 2, out


def test_a_marketplaces_results_are_shown_as_text(monkeypatch, capsys):
    """What a marketplace answers is another site's text."""
    from personalclaw.skills import marketplace

    found = [SimpleNamespace(id=ACTS, description=ACTS)]
    registry = SimpleNamespace(get=lambda name: SimpleNamespace(search=lambda q: found))
    monkeypatch.setattr(marketplace, "get_default_skills_registry", lambda: registry)
    cli._handle_skills(argparse.Namespace(skills_command="search", query="q"))
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 2, out


def test_a_refused_installs_findings_are_shown_as_text(monkeypatch, tmp_path, capsys):
    """A refused install quotes the skill's own files: where a finding is and what it found."""
    from personalclaw.skills import marketplace

    finding = SimpleNamespace(
        rule="r", severity=SimpleNamespace(value="high"), path=ACTS, evidence=ACTS
    )
    refused = marketplace.SkillInstallRefused(SimpleNamespace(findings=[finding]), dangerous=True)

    def install_guarded(*a, **kw):
        raise refused

    registry = SimpleNamespace(get=lambda name: object(), install_guarded=install_guarded)
    monkeypatch.setattr(marketplace, "get_default_skills_registry", lambda: registry)
    with pytest.raises(SystemExit):
        cli._handle_skills(
            argparse.Namespace(skills_command="install", id="x", target=str(tmp_path))
        )
    _as_text(capsys.readouterr().err)


def test_a_verified_skills_file_names_are_shown_as_text(monkeypatch, tmp_path, capsys):
    """A skill's files are named by whoever made the skill."""
    from personalclaw.skills import loader, marketplace

    skill_md = tmp_path / "s" / "SKILL.md"
    report = SimpleNamespace(
        state=marketplace.EDITED,
        summary=lambda: f"s: edited ({ACTS})",
        mutated=[ACTS],
        missing=[ACTS],
        added=[ACTS],
    )
    monkeypatch.setattr(loader, "iter_skill_files", lambda d: [(ACTS, skill_md)])
    monkeypatch.setattr(marketplace, "verify_skill_integrity", lambda d: report)
    cli._handle_skills(argparse.Namespace(skills_command="verify"))
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 5, out


# ── project archives ─────────────────────────────────────────────────────────────


def test_a_project_archives_text_is_shown_as_text(monkeypatch, tmp_path, capsys):
    """An archive someone else made names its project, its members and the credentials it
    expects."""
    from personalclaw.config import loader
    from personalclaw.tasks import hierarchy

    archive = tmp_path / "a.pcproj"
    archive.write_bytes(b"x")
    plan = SimpleNamespace(
        project_name=ACTS,
        accepted=[],
        refused=[SimpleNamespace(path=ACTS, message=ACTS)],
        secrets_expected=[ACTS],
        ok=False,
    )
    monkeypatch.setattr(cli_project.pa, "projects_folder", lambda home: None)
    monkeypatch.setattr(cli_project.pa, "read_archive_plan", lambda *a, **kw: (plan, None))
    monkeypatch.setattr(
        hierarchy, "HierarchyStore", lambda: SimpleNamespace(list_projects=lambda: [])
    )
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "personalclaw.workflows.project_export.import_summary",
        lambda plan, preview: plan.project_name,
    )
    code = cli_project._import(
        argparse.Namespace(archive=str(archive), passphrase="", dry_run=True)
    )
    assert code == 0
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 3, out


def test_a_refused_archives_reason_is_shown_as_text(monkeypatch, tmp_path, capsys):
    from personalclaw.config import loader
    from personalclaw.tasks import hierarchy

    archive = tmp_path / "a.pcproj"
    archive.write_bytes(b"x")

    def refused(*a, **kw):
        raise cli_project.pa.ArchiveRefused("not_an_archive", ACTS)

    monkeypatch.setattr(cli_project.pa, "projects_folder", lambda home: None)
    monkeypatch.setattr(cli_project.pa, "read_archive_plan", refused)
    monkeypatch.setattr(
        hierarchy, "HierarchyStore", lambda: SimpleNamespace(list_projects=lambda: [])
    )
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    code = cli_project._import(
        argparse.Namespace(archive=str(archive), passphrase="", dry_run=True)
    )
    assert code == 1
    _as_text(capsys.readouterr().err)


def test_an_archives_summary_names_its_project_and_credentials_as_text(
    monkeypatch, tmp_path, capsys
):
    """The import's own summary, as it is written, names the project and every credential the
    archive expects."""
    from personalclaw.config import loader
    from personalclaw.tasks import hierarchy

    archive = tmp_path / "a.pcproj"
    archive.write_bytes(b"x")
    plan = SimpleNamespace(
        project_name=ACTS, accepted=[], refused=[], secrets_expected=[ACTS, ACTS + "2"], ok=True
    )
    monkeypatch.setattr(cli_project.pa, "projects_folder", lambda home: None)
    monkeypatch.setattr(cli_project.pa, "read_archive_plan", lambda *a, **kw: (plan, None))
    monkeypatch.setattr(
        hierarchy, "HierarchyStore", lambda: SimpleNamespace(list_projects=lambda: [])
    )
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    code = cli_project._import(
        argparse.Namespace(archive=str(archive), passphrase="", dry_run=True)
    )
    assert code == 0
    summary = capsys.readouterr().out.splitlines()[0]
    _as_text(summary)
    assert summary.count(SHOWN) == 3, summary


def test_a_project_exports_left_files_and_credentials_are_shown_as_text(
    monkeypatch, tmp_path, capsys
):
    """An export names the files it left behind and the credentials the far side must enter."""
    from personalclaw.config import loader

    plan = SimpleNamespace(
        entries=[], artifact_count=0, run_count=0, skipped=[ACTS], secrets_present=[ACTS]
    )
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        cli_project, "_resolve_project", lambda ref: SimpleNamespace(id="p1", name="p")
    )
    monkeypatch.setattr(cli_project, "_gather", lambda project_id: ([], []))
    monkeypatch.setattr(cli_project, "write_private_file", lambda path, raw: None)
    monkeypatch.setattr(cli_project.pa, "export_project_archive", lambda *a, **kw: (b"", plan))
    code = cli_project._export(
        argparse.Namespace(project="p1", passphrase="", output=str(tmp_path / "a.pcproj"))
    )
    assert code == 0
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 2, out


def test_a_config_value_is_shown_as_text(monkeypatch, capsys):
    """`config get` prints what the file holds, which a person or a program wrote."""
    from personalclaw import cli_config

    monkeypatch.setattr(
        cli_config, "sel", lambda: SimpleNamespace(log_api_access=lambda **kw: None)
    )
    monkeypatch.setattr(cli_config, "AppConfig", SimpleNamespace(load=lambda: None))
    monkeypatch.setattr(cli_config, "_merged_view", lambda cfg: {"agent": {"name": ACTS}})
    cli_config._run_config_action(argparse.Namespace(key="agent.name", reveal=False), "get")
    _as_text(capsys.readouterr().out)


# ── discover, eval, a template, the vector store, a folder's memory ──────────────


def test_what_a_host_advertises_is_shown_as_text(monkeypatch, capsys):
    """A gateway found on the network names itself and its address."""
    from personalclaw.companion import discovery

    found = [SimpleNamespace(name=ACTS, base_url="http://h/" + ACTS, requires_pairing=False)]
    monkeypatch.setattr(discovery, "resolve", lambda timeout: found)
    cli_commands._discover(argparse.Namespace(timeout=0.5, as_json=False))
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 2, out


def _an_eval(monkeypatch, tmp_path, *, judge: bool):
    from personalclaw.evals import scenarios as sc

    turn = SimpleNamespace(
        assertion_results=[
            (SimpleNamespace(type=cli_commands.AssertionType.JUDGE, value="v"), True)
        ],
        user_message="u",
        agent_response="a",
    )
    result = SimpleNamespace(
        sessions=[SimpleNamespace(turns=[turn])], passed=True, summary=lambda: {}
    )
    scenario = SimpleNamespace(
        name="s", sessions=[SimpleNamespace(turns=[1])], judge_criteria="c", description="d"
    )
    monkeypatch.setattr(sc, "install_library", lambda: None)
    monkeypatch.setattr(sc, "installed_dir", lambda: tmp_path)
    monkeypatch.setattr(cli_commands, "load_scenario", lambda path: scenario)
    monkeypatch.setattr(
        cli_commands,
        "AppConfig",
        SimpleNamespace(load=lambda: SimpleNamespace(create_provider_factory=lambda: None)),
    )

    class _Runner:
        def __init__(self, **kw):
            pass

        async def run_scenarios(self, scenarios):
            return [result]

    class _Judge:
        pass_threshold = 3

        def __init__(self, **kw):
            pass

        async def start(self):
            pass

        async def shutdown(self):
            pass

        async def judge_turn(self, *a):
            return SimpleNamespace(score=4, reason=ACTS)

    monkeypatch.setattr(cli_commands, "EvalRunner", _Runner)
    monkeypatch.setattr(cli_commands, "LLMJudge", _Judge)
    monkeypatch.setattr(cli_commands, "format_results", lambda results: ACTS)
    monkeypatch.setattr(cli_commands, "score_by_dimension", lambda results: {})
    monkeypatch.chdir(tmp_path)
    asyncio.run(
        cli_commands._run_eval(argparse.Namespace(all_scenarios=False, scenarios=[], judge=judge))
    )


@pytest.mark.parametrize("judge", [False, True], ids=["report", "judge"])
def test_an_evaluations_report_and_judge_are_shown_as_text(judge, monkeypatch, tmp_path, capsys):
    """The report quotes a turn's reply, and the judge's reason is a model's words."""
    _an_eval(monkeypatch, tmp_path, judge=judge)
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == (2 if judge else 1), out


def test_a_templates_file_names_are_shown_as_text(monkeypatch, tmp_path, capsys):
    """A template archive can come from any repository, and names its own files."""
    result = SimpleNamespace(source=ACTS, path=tmp_path, files=[ACTS])
    monkeypatch.setattr(cli_app_new, "from_template", lambda **kw: result)
    args = argparse.Namespace(name="", type="", dest=str(tmp_path), force=False)
    assert cli_app_new._from_template_cmd(args, url="", archive="a.tgz") == 0
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 2, out


def test_a_vector_stores_own_report_is_shown_as_text(monkeypatch, capsys):
    """A vector store's report is its backend's sentence."""
    from personalclaw.vector_stores import registry
    from personalclaw.vector_stores.base import VectorStoreInfo

    info = VectorStoreInfo(backend=ACTS, collection="c", reachable=False, detail=ACTS)
    provider = SimpleNamespace(name=ACTS, describe=lambda: info)
    monkeypatch.setattr(registry, "active_provider", lambda: provider)
    cli_doctor._doctor_external_vector_store()
    out = capsys.readouterr().out
    _as_text(out)
    assert out.count(SHOWN) == 3, out


def test_a_folders_memory_is_named_on_one_line():
    """A folder whose name holds a line break is named on one line."""
    part = SimpleNamespace(
        is_global=False, folder="/tmp/clone\nnext", shown="~/clone\nnext", gone=False
    )
    named = cli_commands._memory_named(part)
    assert "clone\\nnext" in named and "\n" not in named, repr(named)


def test_the_one_line_form_escapes_its_ranges_and_the_line_feed():
    """The one-line form escapes the rule's ranges and the line feed, and keeps every other
    code point."""
    wrong = []
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        if cp == 0x0A:
            want = "\\n"
        elif cp == 0x0D:
            want = "\\r"
        elif cp in _ACTED_ON:
            want = f"\\x{cp:02x}" if cp < 0x100 else f"\\u{cp:04x}"
        else:
            want = ch
        if terminal_line(ch) != want:
            wrong.append(hex(cp))
    assert not wrong, wrong[:20]


def test_a_folders_memory_is_named_as_text():
    """A folder's memory is named by the folder's path, which the folder's name is part of."""
    part = SimpleNamespace(is_global=False, folder="/tmp/a" + ACTS, shown="~/a" + ACTS, gone=False)
    _as_text(cli_commands._memory_named(part))


# ── the streams ──────────────────────────────────────────────────────────────────


def test_every_write_a_command_makes_to_its_streams_is_shown_as_text(monkeypatch, capsys):
    """``cli.main`` sends every write to stdout and stderr through the rule, so a print no site
    wrapped still reaches the terminal as text; what writes bytes, or asks what the stream is,
    gets the stream itself."""
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    monkeypatch.setattr(sys, "argv", ["personalclaw", "--version"])
    with pytest.raises(SystemExit):
        cli.main()
    capsys.readouterr()
    assert isinstance(sys.stdout, cli._TerminalStream)
    assert isinstance(sys.stderr, cli._TerminalStream)
    print(ACTS)
    print(ACTS, file=sys.stderr)
    out, err = capsys.readouterr()
    assert (out, err) == (SHOWN + "\n", SHOWN + "\n")
    assert sys.stdout.isatty() is False and sys.stdout.encoding
    # Every character the rule escapes, through write and writelines both.
    every = "".join(chr(cp) for cp in sorted(_ACTED_ON)) + "\r"
    sys.stdout.write(every)
    sys.stderr.writelines([every, every])
    out, err = capsys.readouterr()
    assert out == terminal_text(every) and err == terminal_text(every) * 2


def test_text_already_escaped_is_written_as_it_is():
    stream = io.StringIO()
    wrapped = cli._TerminalStream(stream)
    wrapped.write(terminal_text(ACTS))
    wrapped.write(ACTS)
    assert stream.getvalue() == SHOWN + SHOWN


def test_the_streams_are_wrapped_once(monkeypatch):
    """Installing the rule again leaves a stream already wrapped as it is."""
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    cli._show_the_streams_as_text()
    cli._show_the_streams_as_text()
    assert isinstance(sys.stdout, cli._TerminalStream)
    assert not isinstance(sys.stdout._stream, cli._TerminalStream)
    assert not isinstance(sys.stderr._stream, cli._TerminalStream)


def test_an_apps_setup_prompt_is_shown_as_text(monkeypatch):
    """An app's setup step names its own prompt, which input() writes past the streams at a
    terminal: it is written as text before it is shown."""
    from personalclaw import app_cli

    prompts = []

    def asked(prompt=""):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", asked)
    monkeypatch.setattr(app_cli, "_enabled_apps_with", lambda field: [("a", "m:f")])
    monkeypatch.setattr(app_cli, "_run_setup_step", lambda name, ref, ask: ask(ACTS) and "")
    assert app_cli.run_app_setup_steps() == []
    assert prompts == [SHOWN]

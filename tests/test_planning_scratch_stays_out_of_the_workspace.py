"""A loop's planner writes its step files into the loop's own folder, never into the user's tree.

Measured on a Code loop over an existing repository: the planner's brief said to write its
artifact "to `step_artifact.json` in your current directory", and its current directory is the
repository. Every step's scratch file landed in the user's tree, and one whose name the model
wrote with a backtick and a newline (``step_artifact.json`\\n``) was never cleared: an untracked
file in a public repository after the loop stopped. The file tools accepted that name.

So: the brief names the absolute path in the loop's own folder, the runner reads (and clears)
only there and never touches the workspace, the planner can reach that folder with its file
tools, and no file tool takes a path with a control character in it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.loop import kinds
from personalclaw.planning import runner as R
from personalclaw.planning.session import PlanStep

# ── the briefs name the loop's own folder ─────────────────────────────────────


def _step() -> PlanStep:
    return PlanStep(id="step-0", kind="problem_framing", title="Scope", objective="Frame it")


@pytest.mark.parametrize("kind", ["code", "goal", "design", "research"])
def test_every_step_brief_names_the_artifact_in_the_loops_own_folder(kind, tmp_path):
    kinds.ensure_loaded()
    wt = kinds.get(kind).walkthrough()
    out_dir = str(tmp_path / "loop" / "0a1b2c3d")

    brief = wt.build_step_brief(
        "fix the double-escaped digest titles",
        _step(),
        approved=[],
        workspace_dir="/home/someone/src/feedsmith",
        out_dir=out_dir,
    )

    assert f"{out_dir}/step_artifact.json" in brief
    assert "current directory" not in brief


@pytest.mark.parametrize("kind", ["code", "design"])
def test_every_design_brief_names_the_step_list_in_the_loops_own_folder(kind, tmp_path):
    kinds.ensure_loaded()
    wt = kinds.get(kind).walkthrough()
    out_dir = str(tmp_path / "loop" / "0a1b2c3d")

    brief = wt.build_design_brief(
        "fix the double-escaped digest titles", "/home/someone/src/feedsmith", out_dir=out_dir
    )

    assert f"{out_dir}/plan_steps.json" in brief
    assert "current directory" not in brief


# ── the runner reads only the loop's folder and never touches the workspace ───


class _Svc:
    def __init__(self, on_add=None):
        self._on_add = on_add
        self.removed = False

    async def add(self, **_kw):
        if self._on_add:
            self._on_add()

    def get_by_session(self, _key):
        return SimpleNamespace(id="L1", active=True)

    async def remove(self, _id):
        self.removed = True


class _State:
    def __init__(self):
        self.session = SimpleNamespace(
            _trust=False,
            acp_provider=None,
            acp_provider_agent=None,
            reasoning_effort="",
            acp_mode="",
            _extra_tool_roots=[],
        )
        self.cwd = None

    def get_or_create_session(self, **kw):
        self.cwd = kw.get("workspace_dir")
        return self.session

    def push_sessions_update(self):
        pass


def _pass(state, svc, ws: Path, files_dir: Path) -> str:
    """The text of the file the pass read ('' when it read none)."""
    return asyncio.run(
        R.run_planner_pass(
            state,
            svc,
            session_key="loop-plan-0a1b2c3d",
            agent_name="planner",
            workspace_dir=str(ws),
            files_dir=str(files_dir),
            sentinel="step_artifact.json",
            brief="b",
            app="loops",
            extra_sentinels=("plan_steps.json", "step_artifact.json"),
        )
    ).text


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    monkeypatch.setattr(R, "PLANNER_FIRST_IDLE", 0)
    ws, files_dir = tmp_path / "repo", tmp_path / "loop"
    ws.mkdir()
    files_dir.mkdir()
    return ws, files_dir


def test_the_planners_artifact_is_read_from_the_loops_folder(dirs):
    ws, files_dir = dirs
    svc = _Svc(on_add=lambda: (files_dir / "step_artifact.json").write_text('{"ok": 1}'))

    assert _pass(_State(), svc, ws, files_dir) == '{"ok": 1}'
    assert not (files_dir / "step_artifact.json").exists()  # consumed, like before


def test_a_file_of_that_name_in_the_workspace_is_not_read_and_not_touched(dirs, monkeypatch):
    ws, files_dir = dirs
    (ws / "step_artifact.json").write_text("the user's own file")
    (ws / "plan_steps.json").write_text("also the user's")
    monkeypatch.setattr(R, "PLANNER_TIMEOUT_SECS", 0.2)

    assert _pass(_State(), _Svc(), ws, files_dir) == ""
    assert (ws / "step_artifact.json").read_text() == "the user's own file"
    assert (ws / "plan_steps.json").read_text() == "also the user's"


def test_a_file_the_planner_wrote_into_the_workspace_is_moved_out_and_read(dirs):
    """A model can still write the bare name, which lands in its working directory."""
    ws, files_dir = dirs
    svc = _Svc(on_add=lambda: (ws / "step_artifact.json").write_text('{"ok": 2}'))

    assert _pass(_State(), svc, ws, files_dir) == '{"ok": 2}'
    assert sorted(p.name for p in ws.iterdir()) == []
    assert not (files_dir / "step_artifact.json").exists()


def test_a_step_pass_leaves_none_of_the_walkthrough_files_in_the_workspace(dirs):
    ws, files_dir = dirs

    def planner():
        (ws / "plan_steps.json").write_text("the planner's own scratch")
        (files_dir / "step_artifact.json").write_text('{"ok": 3}')

    assert _pass(_State(), _Svc(on_add=planner), ws, files_dir) == '{"ok": 3}'
    assert sorted(p.name for p in ws.iterdir()) == []


def test_the_planner_works_in_the_workspace_and_can_write_its_own_folder(dirs):
    ws, files_dir = dirs
    state = _State()
    svc = _Svc(on_add=lambda: (files_dir / "step_artifact.json").write_text("{}"))

    _pass(state, svc, ws, files_dir)

    assert state.cwd == str(ws)
    assert str(files_dir) in state.session._extra_tool_roots


# ── no file tool takes a path with a control character ────────────────────────

#: Every C0 control and DEL: the set the Files view already refuses in a name and a path.
_CONTROLS = [chr(code) for code in (*range(0x20), 0x7F)]


async def _invoke_each(provider, tool: str, args: dict, paths: list[str]) -> list:
    return [await provider.invoke(tool, {"path": path, **args}) for path in paths]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("write_file", {"content": "{}"}),
        ("edit_file", {"old_str": "a", "new_str": "b"}),
        ("read_file", {}),
        ("list_dir", {}),
    ],
)
def test_a_path_with_a_control_character_in_it_is_refused(tmp_path, tool, args):
    provider = NativeBuiltinToolProvider(cwd=tmp_path)
    # The name the planner wrote, then one path per control character.
    paths = ["step_artifact.json`\n", *(f"notes{ch}.md" for ch in _CONTROLS)]

    results = asyncio.run(_invoke_each(provider, tool, args, paths))

    for path, result in zip(paths, results, strict=True):
        assert result.success is False, repr(path)
        assert "control character" in (result.error or ""), (path, result.error)
    assert sorted(p.name for p in tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name",
    # The last is how macOS names a screenshot: a narrow no-break space before "AM".
    ["odd`name.md", "notes (draft).md", "café.md", "Screenshot 10.00.00\u202fAM.png"],
)
def test_an_ordinary_name_is_still_written_and_read(tmp_path, name):
    provider = NativeBuiltinToolProvider(cwd=tmp_path)

    wrote = asyncio.run(provider.invoke("write_file", {"path": name, "content": "x"}))
    read = asyncio.run(provider.invoke("read_file", {"path": name}))

    assert wrote.success is True, wrote.error
    assert read.success is True and "x" in str(read.output), read.error
    assert (tmp_path / name).read_text() == "x"


def test_a_file_whose_name_holds_a_line_break_is_not_sent_to_the_user(tmp_path, monkeypatch):
    from personalclaw import mcp_core
    from personalclaw.config import loader

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    monkeypatch.setattr(loader, "outbox_dir", lambda: outbox)
    source = tmp_path / "work" / "rep\nort.md"
    source.parent.mkdir()
    source.write_text("the weekly report")

    answer = str(mcp_core._call_tool("notify_attachment", {"path": str(source)}))

    assert "control character" in answer, answer
    assert list(outbox.iterdir()) == []


def test_an_ordinary_file_still_reaches_the_outbox(tmp_path, monkeypatch):
    from personalclaw import mcp_core
    from personalclaw.config import loader

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    monkeypatch.setattr(loader, "outbox_dir", lambda: outbox)
    monkeypatch.setattr(mcp_core, "_post", lambda *_a, **_k: {"ok": True})
    source = tmp_path / "work" / "weekly report (draft).md"
    source.parent.mkdir()
    source.write_text("the weekly report")

    mcp_core._call_tool("notify_attachment", {"path": str(source)})

    assert [p.name for p in outbox.iterdir()] == ["weekly report (draft).md"]

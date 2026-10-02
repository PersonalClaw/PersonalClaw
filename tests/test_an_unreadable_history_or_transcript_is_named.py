"""A conversation, a prompt history or a session index that will not open is named, never dropped.

Three reads in the onboarding scan answered a file that was there and would not open the way they
answer a file with nothing in it:

* A Claude Code transcript read as "no prompt in it", so it was left out of the step without a
  word, and an import of it said it held no conversation. Codex's importer names its own
  unreadable sessions; Claude Code's now names them in the same "Unreadable conversations" row,
  and an import of one is refused with why.
* A prompt history (``history.jsonl``) was counted as none, so its "Prompt history" row vanished.
* Codex's session index lost every conversation's name, and nothing said why they came over under
  their first prompts instead.

The two files are named on the scan as a round-2 config file is (``ScanResult.unreadable_files``).
Every root here is a scratch folder under ``tmp_path``; a file "will not open" because ``Path.open``
refuses that one path, which holds for a test run as root too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.home_paths import from_home
from personalclaw.onboarding_import.model import ImportCategory, NotImported
from personalclaw.onboarding_import.sources import claude_code, codex
from personalclaw.onboarding_import.sources.common import SessionUnreadable

DENIED = "could not be read (Permission denied)"


def _refuse_to_open(monkeypatch, *targets: Path) -> None:
    """``Path.open`` raises for exactly these paths, as it does for a file you may not read."""
    real = Path.open
    blocked = set(targets)

    def guarded(self, *args, **kwargs):
        if self in blocked:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)


def _claude_transcript(path: Path, prompt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "type": "user",
        "cwd": "/work/garden",
        "sessionId": path.stem,
        "timestamp": "2026-09-01T10:00:00.000Z",
        "message": {"role": "user", "content": prompt},
    }
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")


def _codex_session(root: Path, session: str, prompt: str) -> Path:
    path = root / "sessions" / "2026" / "09" / "01" / f"rollout-2026-09-01T10-00-00-{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {
            "timestamp": "2026-09-01T10:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": session, "cwd": "/work/garden"},
        },
        {
            "timestamp": "2026-09-01T10:00:02.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            },
        },
    ]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


def _history(path: Path) -> None:
    path.write_text('{"display": "plan the week"}\n{"display": "fix the build"}\n', "utf-8")


@pytest.fixture
def claude_root(tmp_path) -> Path:
    root = tmp_path / "claude-config"
    root.mkdir()
    return root


@pytest.fixture
def codex_root(tmp_path) -> Path:
    root = tmp_path / "codex-home"
    root.mkdir()
    return root


# ── a Claude Code transcript that will not open ────────────────────────────────


def test_a_claude_transcript_that_will_not_open_is_named_not_dropped(claude_root, monkeypatch):
    project = claude_root / "projects" / "-work-garden"
    _claude_transcript(project / "a1-readable.jsonl", "Plan the week.")
    locked = project / "b2-locked.jsonl"
    _claude_transcript(locked, "Fix the build.")
    _refuse_to_open(monkeypatch, locked)

    result = claude_code.scan(claude_root)
    conversations = [i for i in result.items if i.category is ImportCategory.CONVERSATIONS]
    assert [i.title for i in conversations] == ["Plan the week."]
    assert (
        NotImported(
            what="Unreadable conversations",
            count=1,
            why=f"b2-locked.jsonl in -work-garden {DENIED}.",
        )
        in result.not_imported
    )


def test_importing_a_claude_transcript_that_will_not_open_says_why(claude_root, monkeypatch):
    """Not "it holds no prompt": the file was never read, so that was a claim about nothing."""
    locked = claude_root / "projects" / "-work-garden" / "b2-locked.jsonl"
    _claude_transcript(locked, "Fix the build.")
    _refuse_to_open(monkeypatch, locked)
    with pytest.raises(SessionUnreadable) as exc:
        claude_code.read_conversation(locked)
    assert exc.value.reason == DENIED


def test_a_claude_transcript_with_no_prompt_is_still_not_named(claude_root):
    """The control: a readable file with nothing to bring over is still nothing to say."""
    project = claude_root / "projects" / "-work-garden"
    project.mkdir(parents=True)
    (project / "c3-empty.jsonl").write_text('{"type": "summary", "summary": "x"}\n', "utf-8")
    result = claude_code.scan(claude_root)
    assert [n.what for n in result.not_imported if n.what == "Unreadable conversations"] == []


# ── a prompt history that will not open ────────────────────────────────────────


@pytest.mark.parametrize(
    ("scan", "root_fixture"),
    [(claude_code.scan, "claude_root"), (codex.scan, "codex_root")],
    ids=["claude_code", "codex"],
)
def test_a_prompt_history_that_will_not_open_is_named_not_counted_as_none(
    scan, root_fixture, request, monkeypatch
):
    root = request.getfixturevalue(root_fixture)
    history = root / "history.jsonl"
    _history(history)
    _refuse_to_open(monkeypatch, history)
    result = scan(root)
    assert [n.what for n in result.not_imported if n.what == "Prompt history"] == []
    assert result.to_dict()["unreadable_files"] == [
        {"path": from_home(history), "why": "it could not be opened (Permission denied)"}
    ]


def test_a_prompt_history_that_opens_is_still_counted(claude_root):
    """The control: the row a readable history gets is unchanged."""
    _history(claude_root / "history.jsonl")
    result = claude_code.scan(claude_root)
    (row,) = [n for n in result.not_imported if n.what == "Prompt history"]
    assert row.count == 2 and result.unreadable_files == []


# ── a Codex session index that will not open ───────────────────────────────────


def test_a_codex_session_index_that_will_not_open_is_named_and_the_sessions_still_come_over(
    codex_root, monkeypatch
):
    session = "01a0aaaa-0000-7000-8000-000000000001"
    _codex_session(codex_root, session, "Why is CI red?")
    index = codex_root / "session_index.jsonl"
    index.write_text(json.dumps({"id": session, "thread_name": "CI triage"}) + "\n", "utf-8")
    _refuse_to_open(monkeypatch, index)

    result = codex.scan(codex_root)
    conversations = [i for i in result.items if i.category is ImportCategory.CONVERSATIONS]
    assert [i.title for i in conversations] == ["Why is CI red?"], "under its first prompt"
    assert result.to_dict()["unreadable_files"] == [
        {"path": from_home(index), "why": "it could not be opened (Permission denied)"}
    ]


def test_a_codex_session_index_that_opens_still_names_the_sessions(codex_root):
    """The control: a readable index still gives each session the name Codex lists it by."""
    session = "01a0aaaa-0000-7000-8000-000000000001"
    _codex_session(codex_root, session, "Why is CI red?")
    (codex_root / "session_index.jsonl").write_text(
        json.dumps({"id": session, "thread_name": "CI triage"}) + "\n", "utf-8"
    )
    result = codex.scan(codex_root)
    conversations = [i for i in result.items if i.category is ImportCategory.CONVERSATIONS]
    assert [i.title for i in conversations] == ["CI triage"]
    assert result.unreadable_files == []

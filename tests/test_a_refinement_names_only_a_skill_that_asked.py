"""A declined or retried tool call refines only a skill whose procedure asked for that call.

A user's one Deny of a tool call was filed against an imported release skill that never mentions
the tool: the skill had joined the turn because one word of the question matched it, and the call
was the model's own. The proposal then read "The user declined the `mcp/libdocs/resolve-library-id`
action this procedure asked for", which was not true, and offered to rewrite the user's skill.

A stumble about a tool call now goes to the first of the turn's skills whose procedure names the
tool, and to none when no skill does. A correction, which names no tool, still goes to the turn's
first skill.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.skills import loader as loader_mod
from personalclaw.skills import proposals, refine

_RELEASE = "imported/example_agent/release-steps"
_RELEASE_BODY = (
    "---\nname: release-steps\ndescription: Cut a release of the package\n---\n\n"
    "1. Bump the version in pyproject.toml.\n2. Run the tests with `uv run pytest`.\n"
    "3. Tag the release and update the parser's pin if needed.\n"
)
_DOCS = "docs-lookup"
_DOCS_BODY = (
    "---\nname: docs-lookup\ndescription: Look up a library's docs\n---\n\n"
    "Use libdocs: call resolve-library-id with the library's name, then query-docs.\n"
)
_DECLINED = "mcp/libdocs/resolve-library-id"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated skills home (the queue and the loader both resolve through it)."""
    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)
    import personalclaw.skills.marketplace as mp

    monkeypatch.setattr(mp, "skill_discovery_paths", lambda: [])
    assert tmp_path in proposals._proposals_dir().parents, "the proposal queue was NOT redirected"
    return tmp_path


def _filed() -> list[dict]:
    """Every proposal the store holds, read off disk. Not `list_pending()`: a proposal for an
    imported skill (a nested name) is filed in a subfolder that listing never reads, so a zero
    from it would be no evidence here."""
    root = proposals._proposals_dir()
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.rglob("*.json"))]


def _install(home, name: str, body: str) -> None:
    d = home / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")


class _State:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    def broadcast_ws(self, name: str, payload: dict) -> None:
        self.sent.append((name, payload))


class _Session:
    key = "chat-9"

    def __init__(self, used: list[str]) -> None:
        self._skills_used = [{"name": n, "state": "admitted", "loaded_tokens": 10} for n in used]
        self._learned: list = []


class _Cfg:
    skill_ladder = True
    surface_chip = True


def _turn(used: list[str], outcomes: list[tuple[str, str]], user: str = "What changed?") -> _State:
    from personalclaw.dashboard import chat_runner

    state = _State()
    chat_runner._maybe_refine_stumble(
        state, _Session(used), user, "It is a maintenance release.", outcomes, _Cfg()
    )
    return state


def test_a_deny_is_not_filed_against_a_skill_that_never_asked_for_the_call(home) -> None:
    """The measured turn: the release skill joined on a word, and the denied call was not its."""
    _install(home, _RELEASE, _RELEASE_BODY)
    state = _turn([_RELEASE], [(_DECLINED, "denied")])
    assert _filed() == []
    assert state.sent == [], "no chip announces a proposal that was not made"


def test_the_deny_goes_to_the_skill_whose_procedure_named_the_call(home) -> None:
    """Admission order still decides between skills that DID ask: the first one that names it."""
    _install(home, _RELEASE, _RELEASE_BODY)
    _install(home, _DOCS, _DOCS_BODY)
    _turn([_RELEASE, _DOCS], [(_DECLINED, "denied")])
    (filed,) = _filed()
    assert (filed["refine_target"], filed["trigger"]) == (_DOCS, "rejection")
    assert "`mcp/libdocs/resolve-library-id` action this procedure asked for" in (
        filed["procedure_md"]
    )


def test_a_retried_failure_is_held_to_the_same_rule(home) -> None:
    _install(home, _RELEASE, _RELEASE_BODY)
    _turn([_RELEASE], [(_DECLINED, "failed"), (_DECLINED, "success")])
    assert _filed() == []

    _install(home, _DOCS, _DOCS_BODY)
    _turn([_RELEASE, _DOCS], [(_DECLINED, "failed"), (_DECLINED, "success")])
    (filed,) = _filed()
    assert (filed["refine_target"], filed["trigger"]) == (_DOCS, "failure_retry")


def test_a_correction_still_goes_to_the_turns_first_skill(home) -> None:
    """It names no tool, so nothing narrows it: the floor is the behaviour it always had."""
    _install(home, _RELEASE, _RELEASE_BODY)
    state = _turn([_RELEASE], [], user="No, the pin lives in requirements.txt.")
    (filed,) = _filed()
    assert (filed["refine_target"], filed["trigger"]) == (_RELEASE, "correction")
    assert len(state.sent) == 1, "the positive control for the zero-chip assertion above"


@pytest.mark.parametrize(
    ("procedure", "tool", "named"),
    [
        ("Write the notes with write_file.", "write_file", True),
        ("Use rewrite_file for that.", "write_file", False),
        ("Call resolve-library-id first.", _DECLINED, True),
        ("Look it up with libdocs.", _DECLINED, True),
        ("Call mcp__libdocs__resolve-library-id.", _DECLINED, True),
        ("Resolve the library identity by hand.", _DECLINED, False),
        ("Search GitHub for the repository.", "mcp/github/search_repositories", True),
        ("Bump the version and tag it.", "bash", False),
    ],
)
def test_what_counts_as_a_procedure_naming_a_tool(procedure: str, tool: str, named: bool) -> None:
    assert refine.procedure_names_tool(procedure, tool) is named

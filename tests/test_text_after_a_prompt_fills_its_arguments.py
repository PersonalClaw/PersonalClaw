"""The text typed after an @prompt fills the prompt's own slots for it.

Measured in a chat: an imported Claude Code command, ``standup``, reads "For each repo in
$ARGUMENTS (default: the current directory):". The importer turned ``$ARGUMENTS`` into a declared
``{{arguments}}`` variable, "so the prompt asks for them where Claude Code took them from the
command line". The user sent ``@standup ~/src/kettle``; the chat bound only ``key=value``
tokens, so ``{{arguments}}`` rendered empty ("For each repo in  (default: the current
directory):") and the path went only into a trailing "Additional context" line. The agent worked
in its own workspace, never looked at the repository, and made up a standup.

A command run by name takes the text after its name as its arguments, so an @prompt does too:
``{{arguments}}`` is that text and ``{{arg1}}``…``{{arg9}}`` are its words, for the prompts that
declare them. Both importers produce those names, so the tests build their prompts THROUGH the
importers: the names the importers write and the names the chat fills cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from personalclaw.dashboard.chat import _expand_prompt_mention
from personalclaw.onboarding_import.sources.claude_code import command_prompt
from personalclaw.onboarding_import.sources.codex import prompt_template

STANDUP = (
    "Summarise what changed since yesterday.\n\n"
    "For each repo in $ARGUMENTS (default: the current directory):\n"
    "- list its commits since yesterday\n"
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.agent._project_dir", lambda: None)
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    monkeypatch.setenv("PERSONALCLAW_SKIP_PROMPT_SEED", "1")


def _save(tmp_path, name: str, content: str, variables: list[dict]) -> None:
    """Store a prompt where the native prompt provider reads it."""
    folder = tmp_path / ".personalclaw" / "prompts"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.yaml").write_text(
        yaml.safe_dump({"name": name, "content": content, "variables": variables}, sort_keys=False)
    )


class _Session:
    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []
        self.key = "chat-1"

    def append(self, role, text, cls):
        self.messages.append((role, text, cls))


class _State:
    def push_sessions_update(self):
        pass


def _expand(message: str) -> str:
    expanded, status = _expand_prompt_mention(message, _State(), _Session())
    assert status == "ok", (status, expanded)
    return expanded


def test_the_text_after_an_imported_command_is_its_arguments(tmp_path):
    content, variables = command_prompt(STANDUP, argument_hint="[repo-path ...]")
    _save(tmp_path, "standup", content, variables)

    expanded = _expand("@standup ~/src/kettle")

    assert "For each repo in ~/src/kettle (default: the current directory):" in expanded
    # Said once, where the prompt put it — not again as a context line the model may skip.
    assert "Additional context" not in expanded


def test_no_text_leaves_the_prompts_own_default_in_charge(tmp_path):
    content, variables = command_prompt(STANDUP, argument_hint="")
    _save(tmp_path, "standup", content, variables)

    expanded = _expand("@standup")

    assert "For each repo in  (default: the current directory):" in expanded


def test_a_named_value_still_wins_over_the_trailing_text(tmp_path):
    content, variables = command_prompt(STANDUP, argument_hint="")
    _save(tmp_path, "standup", content, variables)

    expanded = _expand('@standup arguments="~/src/eta-engine" and keep it short')

    assert "For each repo in ~/src/eta-engine (default" in expanded
    # Not consumed by a slot, so it still reaches the model.
    assert "Additional context from user: and keep it short" in expanded


def test_positional_words_fill_the_numbered_slots(tmp_path):
    content, variables = prompt_template(
        "Review PR #$1 at $2 priority. Notes: $ARGUMENTS", argument_hint=""
    )
    _save(tmp_path, "review", content, variables)

    expanded = _expand("@review 57 high")

    assert "Review PR #57 at high priority. Notes: 57 high" in expanded
    assert "Additional context" not in expanded


def test_words_past_the_numbered_slots_are_kept_as_context(tmp_path):
    content, variables = command_prompt("Triage issue $1.", argument_hint="")
    _save(tmp_path, "triage", content, variables)

    expanded = _expand("@triage 57 it only happens on Mondays")

    assert "Triage issue 57." in expanded
    assert "Additional context from user: it only happens on Mondays" in expanded


def test_a_prompt_with_no_slot_for_it_keeps_the_text_as_context(tmp_path):
    _save(tmp_path, "tidy", "Tidy the notes.", [])

    expanded = _expand("@tidy the Garden folder")

    assert "Tidy the notes." in expanded
    assert "Additional context from user: the Garden folder" in expanded

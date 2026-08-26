"""Per-runner dialect knowledge: how to frame a brief, and how to fire one shot (§4.2).

``prepare`` "renders runner-specific instructions (Claude Code wants different framing than
Gemini CLI — the dialect knows)". This module is that knowledge, in one table, so a new runner is
one row rather than a branch in the backend.

**An undeclared dialect is not fireable.** ``one_shot`` returning ``None`` means "this repo does
not know this runner's non-interactive form", and the runner backend then refuses to prepare
rather than guessing a flag. Guessing would produce a runner that launches interactively, blocks
on a TTY that does not exist, and times out — a failure indistinguishable from a stalled proposer.
The honest outcome is that the runner is skipped and the ``subagent`` fallback answers instead.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dialect:
    """One runner family's framing + non-interactive invocation form."""

    #: Dialect key, matched against ``RunnerDefinition.dialect`` then ``RunnerDefinition.id``.
    key: str
    #: Flags placed before the prompt for a single headless turn.
    one_shot_args: tuple[str, ...]
    #: A preamble prepended to the brief. Short on purpose: the brief carries the substance.
    framing: str

    def argv(self, command: tuple[str, ...], prompt: str) -> tuple[str, ...]:
        return (*command, *self.one_shot_args, prompt)

    def render(self, brief_markdown: str) -> str:
        return f"{self.framing}\n\n{brief_markdown}" if self.framing else brief_markdown


#: Framing shared by every dialect — the invariant part of the ask.
_COMMON = (
    "You are being asked for a second opinion on a task another agent could not finish. "
    "You get ONE turn. Do not ask questions; make the smallest change that unblocks the "
    "problem, edit the files on disk, and report exactly which files you changed."
)

_DIALECTS: dict[str, Dialect] = {
    # `claude -p <prompt>` is Claude Code's print (non-interactive) mode.
    "claude-code": Dialect(
        key="claude-code",
        one_shot_args=("-p",),
        framing=(
            f"{_COMMON} Prefer reading the files you are about to change before editing them; "
            "state your reasoning briefly, then act."
        ),
    ),
    # `codex exec <prompt>` is Codex's non-interactive subcommand.
    "codex": Dialect(
        key="codex",
        one_shot_args=("exec",),
        framing=(
            f"{_COMMON} Work directly in the repository; do not open a sandboxed scratch copy."
        ),
    ),
    # `gemini -p <prompt>` is Gemini CLI's non-interactive prompt flag.
    "gemini-cli": Dialect(
        key="gemini-cli",
        one_shot_args=("-p",),
        framing=(
            f"{_COMMON} Be concise — a short diagnosis plus the edit is worth more here than a "
            "long analysis."
        ),
    ),
}


def one_shot(dialect: str, runner_id: str = "") -> Dialect | None:
    """The dialect for *dialect* (falling back to *runner_id*), or ``None`` when undeclared."""
    for key in ((dialect or "").strip().lower(), (runner_id or "").strip().lower()):
        if key and key in _DIALECTS:
            return _DIALECTS[key]
    return None


def declared_dialects() -> tuple[str, ...]:
    """Every dialect this repo can fire one-shot — the inventory a test can pin."""
    return tuple(sorted(_DIALECTS))

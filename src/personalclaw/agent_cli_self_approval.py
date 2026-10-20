"""Whether an Unattended loop's agent CLI may approve its own calls: the owner's choice, per loop.

An unattended turn on an agent CLI asks the CLI for its asking mode, as an attended turn does
(``acp.permission_authority.sanitize_mode``), so every call the CLI asks about reaches the chat
runner's gate: the task mode, the deny-list and the owner-only screen, the run's bounds, and then
the run's standing grant, or the unattended fail-fast, which refuses the call with its reason.

The one exception is this choice. The owner may let one Unattended loop's agent CLI approve its own
calls (its self-approving mode), and then no host gate sees them:

* it is off for every loop until she turns it on, and turned on only with her yes to what it does
  (``"confirm": true`` on the write, :func:`consent`);
* it is offered only for a loop that runs Unattended on an agent CLI whose not-gateable residual
  PersonalClaw declares (``permission_authority.NOT_GATEABLE``), since for a CLI nobody measured
  there is no written account of what it runs without asking (:func:`not_offered`);
* each change is an audit row, either way (:func:`set_for_loop`);
* it is read at each turn (:func:`chosen_for`), so turning it off reaches the loop's next turn, and
  it holds only while the loop's own grant does (``loop.posture.runs_on_its_loops_grant``: the
  trust window ends both, and a Trust of another kind never stands in for it), under the operator
  ceiling (``sanitize_mode``).

The store is ``entity_settings/agent_cli_self_approval.json``:
``{"loops": {"<loop id>": {"allowed": true, "changed_at": "<UTC instant>"}}}``. It is read
fail-CLOSED: a file that cannot be read allows no loop's CLI anything, because it holds grants and a
read that failed is no yes. A loop's entry goes when the loop is deleted (``loop.store.delete``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The entity_settings file this choice is kept in.
_ENTITY = "agent_cli_self_approval"

#: What a turn on a loop allowed this asks its agent CLI for when it names no mode of its own.
SELF_APPROVING_MODE = "bypassPermissions"


class NotOffered(ValueError):
    """The choice cannot be turned on for this loop; the message says why, in her words."""


@dataclass(frozen=True)
class Choice:
    """One loop's choice: whether its agent CLI approves its own calls, and when it changed."""

    allowed: bool = False
    changed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "changed_at": self.changed_at}

    @classmethod
    def from_dict(cls, raw: Any) -> "Choice":
        """A stored entry: ``allowed`` as the word it spells, off for anything that spells neither
        (``safety_flags.strict_bool``), and off for an entry that is not an object."""
        from personalclaw.safety_flags import strict_bool

        if not isinstance(raw, dict):
            return cls()
        return cls(
            allowed=strict_bool(
                raw.get("allowed"), field="agent_cli_self_approval.allowed", default=False
            ),
            changed_at=str(raw.get("changed_at") or ""),
        )


def load() -> dict[str, Choice]:
    """Every loop's stored choice, by loop id. Fail-CLOSED: an unreadable store is no choice."""
    from personalclaw.providers.entity_routes import _load_entity_settings

    raw = _load_entity_settings(_ENTITY)
    if raw is None:
        # A grant that cannot be read grants nothing; the loader already said which file.
        logger.warning("agent CLI self-approval: the store is unreadable, so no loop has it")
        return {}
    loops = raw.get("loops")
    if not isinstance(loops, dict):
        return {}
    return {str(loop_id): Choice.from_dict(entry) for loop_id, entry in loops.items()}


def to_dict(choices: dict[str, Choice]) -> dict[str, Any]:
    """The stored document for *choices*."""
    return {"loops": {loop_id: choice.to_dict() for loop_id, choice in sorted(choices.items())}}


def _save(choices: dict[str, Choice]) -> None:
    from personalclaw.providers.entity_routes import _save_entity_settings

    _save_entity_settings(_ENTITY, to_dict(choices))


def for_loop(loop_id: str) -> Choice:
    """Loop *loop_id*'s choice; off when nothing is stored for it."""
    return load().get(loop_id, Choice())


def runs_on_an_agent_cli(loop: Any) -> bool:
    """Whether *loop*'s planner and workers run on an agent CLI rather than PersonalClaw's agent."""
    return str(getattr(loop, "provider", "") or "").startswith("acp")


def declared(provider: str) -> bool:
    """Whether PersonalClaw declares what agent CLI *provider* runs without asking: its measured
    not-gateable residual (``permission_authority.NOT_GATEABLE``), empty or not."""
    from personalclaw.acp.permission_authority import coverage_for

    return coverage_for(provider) is not None


def cli_name(loop: Any) -> str:
    """The name a person knows *loop*'s agent CLI by (``providers.image_input.agent_label``)."""
    from personalclaw.providers.image_input import agent_label

    return agent_label(str(getattr(loop, "provider", "") or "")) or "Its agent CLI"


def not_offered(loop: Any) -> str:
    """Why *loop* cannot be given the choice, as the loop's page says it, or ``""`` when it can."""
    if not runs_on_an_agent_cli(loop):
        return "It runs on PersonalClaw's own agent, which asks PersonalClaw about every call."
    if getattr(loop, "attended", True) is not False:
        return "It is Attended: you answer its agent CLI's calls yourself."
    if not declared(str(loop.provider)):
        return (
            f"PersonalClaw hasn't measured what {cli_name(loop)} runs without asking, so it can't "
            "let it approve its own calls."
        )
    return ""


def consent(loop: Any) -> str:
    """What turning the choice on does, as her consent is asked for it."""
    cli = cli_name(loop)
    return (
        f"{cli} will approve its own tool calls in this loop instead of asking PersonalClaw first. "
        "PersonalClaw's blocked commands, the run's folder and host limits, the files only you "
        f"may change and the loop's read-only steps no longer check them: {cli}'s own settings "
        "decide what it runs."
    )


def set_for_loop(loop: Any, allowed: bool, *, caller: str) -> Choice:
    """Store *loop*'s choice and write its audit row; the choice as stored.

    Turning it on is refused for a loop that cannot have it (:class:`NotOffered`, with why);
    turning it off always lands, since taking a grant back never needs a reason."""
    from personalclaw.instants import utc_now_iso
    from personalclaw.sel import sel

    if allowed:
        why_not = not_offered(loop)
        if why_not:
            raise NotOffered(why_not)
    choices = load()
    choice = Choice(allowed=bool(allowed), changed_at=utc_now_iso())
    choices[loop.id] = choice
    _save(choices)
    try:
        sel().log_api_access(
            caller=caller,
            operation="loop.agent_cli_self_approval",
            outcome="enabled" if allowed else "disabled",
            resources=f"loop={loop.id} cli={getattr(loop, 'provider', '') or '-'}",
        )
    except Exception:
        logger.warning("SEL audit failed for a loop's agent CLI self-approval", exc_info=True)
    return choice


def forget_loop(loop_id: str) -> None:
    """Drop *loop_id*'s choice, when the loop is deleted."""
    choices = load()
    if choices.pop(loop_id, None) is not None:
        _save(choices)


def chosen_for(session_key: str, provider_kind: str) -> bool:
    """Whether the work of *session_key*, on agent CLI *provider_kind*, is a loop's whose owner let
    that CLI approve its own calls, read now. ``False`` for any other work, for a loop that is
    gone or cannot have the choice now, and for a turn running on a CLI other than its loop's."""
    from personalclaw.acp.permission_authority import normalize_provider
    from personalclaw.loop import store
    from personalclaw.loop.manager import session_loop

    loop_id = session_loop(session_key or "")
    if not loop_id or not for_loop(loop_id).allowed:
        return False
    try:
        loop = store.get(loop_id)
    except Exception:  # noqa: BLE001 - a loop that cannot be read is given nothing
        logger.warning("agent CLI self-approval: loop %s could not be read", loop_id, exc_info=True)
        return False
    return bool(
        loop is not None
        and not not_offered(loop)
        and normalize_provider(provider_kind) == normalize_provider(loop.provider)
    )


def view(loop: Any) -> dict[str, Any]:
    """What the loop's page shows of the choice: whether it is on, and whether it can be."""
    why_not = not_offered(loop)
    return {
        "allowed": for_loop(loop.id).allowed and not why_not,
        "available": not why_not,
        "unavailable": why_not,
        "cli": cli_name(loop) if runs_on_an_agent_cli(loop) else "",
    }


__all__ = [
    "Choice",
    "NotOffered",
    "SELF_APPROVING_MODE",
    "chosen_for",
    "consent",
    "declared",
    "for_loop",
    "forget_loop",
    "load",
    "not_offered",
    "set_for_loop",
    "to_dict",
    "view",
]

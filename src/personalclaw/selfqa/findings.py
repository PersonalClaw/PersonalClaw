"""Filing a failing scenario — exactly one Inbox item and exactly one Task.

The SV-9 atom's `done_when` reads "a failing scenario files one Inbox item + one Task", and *one*
is a ceiling as much as a floor. Three Inbox items for one failure trains the user to ignore the
inbox, which costs more than filing nothing. So the count is enforced here rather than left to
the caller's discipline: :func:`file_finding` files one of each per finding and is idempotent on
the finding's key, so a retried node, a resumed run, or a node that fires twice cannot multiply
the filing.

(The atom cites this to plan Success Criterion #6; #6 itself covers the watcher, the skip record
and the UI drive, and does not mention filing. The requirement is the atom's, not #6's — the
citation is the atom's, and it is recorded here rather than silently propagated.)

Both sinks are the existing native ones — `post_to_inbox` (the in-core push sink) and the native
`TaskProvider` through `tasks.registry.create_task`. No new inbox source, no new task provider;
§5 of the plan is explicit that inventing one is the tempting wrong answer here.

The two records are deliberately different documents. The Inbox item is the *interrupt*: one
line saying what broke, plus the evidence bundle reference. The Task is the *work*: the scenario
text, the reproduction steps, and the branch name if a fix branch was opened. A user who reads
only the inbox learns that something is wrong; a user who opens the task can act on it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Label stamped on every Task this module files, so the companion's output is one query away.
FINDING_LABEL = "self-qa"


@dataclass
class _Progress:
    """How far one finding's filing got. Per-sink, not one all-or-nothing flag.

    A flag flipped after BOTH sinks succeed makes a partial failure re-post the Inbox item: the
    item lands, the Task raises, nothing is recorded, and the replay starts over from the top —
    two interrupts for one failure, which is the exact ceiling breach the criterion forbids. A
    flag flipped BEFORE either sink is the mirror defect: the Task never gets created and the
    replay reports the finding as filed. So each sink is recorded as it completes, and a replay
    does only what is left.
    """

    inbox_posted: bool = False
    inbox_item_id: str = ""
    task_id: str = ""

    @property
    def complete(self) -> bool:
        """Both sinks landed. The Task is the marker: it is written second."""
        return bool(self.task_id)


#: Filing progress per finding, so a retried node cannot double-file. Keyed on the finding's
#: `key` (sha + scenario id), which is stable across a resume.
_filed: dict[str, _Progress] = {}


def reset_filed_keys() -> None:
    """Clear the in-process filing state. For tests, and for a gateway restart's clean slate."""
    _filed.clear()


@dataclass
class ScenarioFinding:
    """One failed as-a-user scenario. `evidence_ref` points at the bundle Artifact."""

    sha: str
    scenario_id: str
    title: str
    scenario_text: str
    repro_steps: list[str] = field(default_factory=list)
    evidence_ref: str = ""
    fix_branch: str = ""

    @property
    def key(self) -> str:
        """Stable identity: the same failure from the same commit is the same finding."""
        return f"{self.sha}:{self.scenario_id}"

    def inbox_message(self) -> str:
        """The one-line interrupt. Short on purpose — the Task carries the detail."""
        head = f"Self-QA failed on {self.sha[:8]}: {self.title}"
        if self.evidence_ref:
            head += f" (evidence: {self.evidence_ref})"
        return head

    def task_description(self) -> str:
        """The actionable document: scenario, reproduction, evidence, branch."""
        lines = [f"Self-QA scenario `{self.scenario_id}` failed on commit `{self.sha}`.", ""]
        lines += ["## Scenario", self.scenario_text, ""]
        if self.repro_steps:
            lines += ["## Reproduction"]
            lines += [f"{i}. {step}" for i, step in enumerate(self.repro_steps, 1)]
            lines += [""]
        if self.evidence_ref:
            lines += ["## Evidence", self.evidence_ref, ""]
        if self.fix_branch:
            lines += [
                "## Proposed fix",
                f"A fix branch was opened at `{self.fix_branch}`. It is neither merged nor "
                "pushed — review the diff before doing either.",
                "",
            ]
        return "\n".join(lines).rstrip() + "\n"


@dataclass(frozen=True)
class FiledFinding:
    """What filing produced. `already_filed` distinguishes a dedup hit from a fresh filing."""

    inbox_item_id: str
    task_id: str
    already_filed: bool = False


async def file_finding(
    finding: ScenarioFinding,
    *,
    state: Any = None,
    provider_name: str = "native",
) -> FiledFinding:
    """File `finding` as one Inbox item and one Task. Idempotent on `finding.key`.

    Returns the two ids. A second call for the same finding returns
    ``already_filed=True`` with empty ids and files nothing — the dedup is here rather than in
    the template because a resumed run replays the node, and "the engine will only call this
    once" is an assumption that has not held.

    The Inbox item is posted first. If the Task then fails to create, the user still sees that
    something broke — the reverse ordering would leave a silent task nobody is told about. That
    partial state is *recorded*, so the replay creates only the missing Task and does not post a
    second interrupt for the same failure.
    """
    progress = _filed.get(finding.key)
    if progress is not None and progress.complete:
        logger.debug("selfqa: finding %s already filed; not filing again", finding.key)
        return FiledFinding(inbox_item_id="", task_id="", already_filed=True)

    if progress is None:
        progress = _Progress()
        _filed[finding.key] = progress

    from personalclaw.inbox_providers.native_source import post_to_inbox
    from personalclaw.tasks.registry import create_task

    if not progress.inbox_posted:
        item = post_to_inbox(
            finding.inbox_message(),
            kind="notification",
            sender_name="self-qa",
            context=f"Self-QA companion — scenario {finding.scenario_id} on {finding.sha[:8]}",
            state=state,
        )
        # Set after the call returns, so a `post_to_inbox` that RAISED is retried rather than
        # counted. Separate from the id because a gateway with no dashboard state posts nothing
        # and returns None — "posted, landed nowhere" must not read as "not yet posted".
        progress.inbox_posted = True
        progress.inbox_item_id = getattr(item, "id", "") if item is not None else ""

    task = await create_task(
        provider_name=provider_name,
        title=finding.title,
        description=finding.task_description(),
        labels=[FINDING_LABEL],
        priority="high",
        author="self-qa",
    )
    progress.task_id = task.id

    logger.info(
        "selfqa: filed finding %s — inbox=%s task=%s",
        finding.key,
        progress.inbox_item_id or "<none>",
        task.id,
    )
    return FiledFinding(inbox_item_id=progress.inbox_item_id, task_id=task.id)

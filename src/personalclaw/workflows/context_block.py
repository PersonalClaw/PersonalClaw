"""The `[ACTIVE WORKFLOWS]` context block and the staged-turn spec echo.

Two injections that make the chat surface usable, both built on the same rule:

**Never break a turn.** Every function here returns a string — empty on any failure —
and swallows its own errors. A context builder that raises takes the user's whole message
with it, and "the assistant stopped responding because a workflow row was corrupt" is a
far worse outcome than "the assistant did not mention a running workflow". This is why the
old surfacing bridge was written the same way, and why it is not negotiable here.

**Show, don't remember (WF2-R20f).** A model editing a spec from memory edits the spec it
*generated*, not the one on disk — and those diverge the moment anything else touches the
run. So the staged-turn contract puts a rendered tree AND the source spec into the model's
transient context before a mutation turn: it mutates what it just saw.

The block is deliberately terse. A run's full node list would crowd out the user's actual
message; what a model needs is "this exists, it is waiting on you, here is its id".
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Cap on the rendered block. A chat turn has a budget, and a user with forty runs must not
#: lose their own message to a workflow listing.
MAX_BLOCK_CHARS = 2_000

#: Cap on the staged-turn spec echo. Large enough for a real workflow, small enough that an
#: enormous spec degrades to "read it with workflow_get_def" instead of eating the turn.
MAX_ECHO_CHARS = 6_000

#: Runs surfaced in the block, newest first. Beyond this the block says "and N more".
MAX_RUNS_IN_BLOCK = 5


def active_workflows_block(*, project_id: str = "") -> str:
    """The `[ACTIVE WORKFLOWS]` block, or "" when there is nothing to say.

    Ordered by urgency, not recency: a run WAITING on a human comes first, because that is
    the one the user can act on. A merely-running run is informational.

    Never raises. See the module docstring.
    """
    try:
        from personalclaw.workflows import store
        from personalclaw.workflows.models import RunStatus

        runs = store.active_runs()
    except Exception:
        logger.debug("active-workflows block skipped (store unavailable)", exc_info=True)
        return ""
    if not runs:
        return ""

    try:
        if project_id:
            runs = [r for r in runs if not r.project_id or r.project_id == project_id]
        # needs_input first, then paused, then running — the order a human should act in.
        rank = {
            RunStatus.NEEDS_INPUT.value: 0,
            RunStatus.PAUSED.value: 1,
            RunStatus.RUNNING.value: 2,
        }
        runs.sort(key=lambda r: (rank.get(r.status.value, 9), r.created_at), reverse=False)
        shown, extra = runs[:MAX_RUNS_IN_BLOCK], max(0, len(runs) - MAX_RUNS_IN_BLOCK)

        lines = [
            "[ACTIVE WORKFLOWS — runs in flight right now. Use workflow_status{run_id} for "
            "detail, workflow_resume{run_id, answer} to answer one that is waiting.]"
        ]
        for run in shown:
            line = f"- {run.workflow_name} (run_id: {run.id}) — {run.status.value}"
            if run.status == RunStatus.NEEDS_INPUT:
                prompt = ""
                if isinstance(run.attention, dict):
                    prompt = str(run.attention.get("prompt", "") or "")
                # The ASK is the actionable part — without it the user is told a run needs
                # them but not what it is asking.
                line += f" — waiting on you: {prompt[:160]}" if prompt else " — waiting on you"
            elif run.error_message:
                line += f" ({run.error_message[:80]})"
            lines.append(line)
        if extra:
            lines.append(f"- …and {extra} more (workflow_status for any run_id)")
        lines.append("[End of active workflows]")
        block = "\n".join(lines)
        if len(block) > MAX_BLOCK_CHARS:
            block = block[: MAX_BLOCK_CHARS - 30].rstrip() + "\n…truncated]"
        return block + "\n\n"
    except Exception:
        logger.debug("active-workflows block skipped (render error)", exc_info=True)
        return ""


# ── the staged-turn echo (WF2-R20f) ──────────────────────────────────────────


def render_tree(node: Any, *, indent: int = 0, states: dict[str, str] | None = None) -> list[str]:
    """A readable tree of a spec, one line per node.

    Rendered ALONGSIDE the source rather than instead of it: the tree is what a model reads
    to reason about structure, the JSON is what it must edit precisely. Giving only the tree
    invites invented field names; only the JSON makes the shape hard to see.
    """
    out: list[str] = []
    try:
        kind = str(node.get("kind", "?")) if isinstance(node, dict) else "?"
        node_id = str(node.get("id", "")) if isinstance(node, dict) else ""
        prefix = "  " * indent
        label = f"{prefix}- {kind}"
        if node_id:
            label += f" #{node_id}"
        if states and node_id and node_id in states:
            label += f" [{states[node_id]}]"
        out.append(label)
        if not isinstance(node, dict):
            return out
        for child in node.get("children") or []:
            out.extend(render_tree(child, indent=indent + 1, states=states))
        body = node.get("body")
        if isinstance(body, dict):
            out.append(f"{prefix}  body:")
            out.extend(render_tree(body, indent=indent + 2, states=states))
        for label_name, case in (node.get("cases") or {}).items():
            out.append(f"{prefix}  case {label_name}:")
            out.extend(render_tree(case, indent=indent + 2, states=states))
        default = node.get("default")
        if isinstance(default, dict):
            out.append(f"{prefix}  default:")
            out.extend(render_tree(default, indent=indent + 2, states=states))
    except Exception:
        logger.debug("tree render failed", exc_info=True)
    return out


def staged_spec_echo(run_id: str) -> str:
    """The rendered+source echo that must precede a mutation turn (WF2-R20f).

    A model that edits from memory edits the spec it GENERATED, which diverges from disk the
    moment anything else touches the run — another tool, a rewind, a concurrent edit. The
    echo removes the guess: here is the current tree, here is the current source, here is
    the version to pass as `expect_version`.

    Returns "" when the run is unreadable — the caller's turn continues either way.
    """
    try:
        from personalclaw.workflows import store

        run = store.get(run_id)
        spec = store.read_spec(run_id)
        if run is None or not isinstance(spec, dict):
            return ""
        instances = store.read_state(run_id)
    except Exception:
        logger.debug("staged echo skipped (store unavailable)", exc_info=True)
        return ""

    try:
        from personalclaw.workflows.models import Node, walk

        states: dict[str, str] = {}
        try:
            for path, node in walk(Node.from_dict(spec.get("root") or {})):
                inst = instances.get(path)
                if node.id and inst is not None:
                    states[node.id] = inst.state.value
        except (ValueError, TypeError):
            pass  # an unreadable tree still echoes its source, which is the important half

        from personalclaw.workflows import secrets

        safe_spec = secrets.strip_secrets(spec)
        source = json.dumps(safe_spec, indent=2, ensure_ascii=False, default=str)
        truncated = len(source) > MAX_ECHO_CHARS
        if truncated:
            source = source[:MAX_ECHO_CHARS].rstrip()

        lines = [
            f"[WORKFLOW SPEC — run {run.id}, spec_version {run.spec_version}. This is the "
            "CURRENT state on disk. Edit THIS, not a spec you generated earlier — pass "
            f"expect_version={run.spec_version} so a concurrent change is caught.]",
            "",
            "Structure:",
            *render_tree(spec.get("root") or {}, states=states),
            "",
            "Source:",
            "```json",
            source,
            "```",
        ]
        if truncated:
            lines.append(
                "[Source truncated — call workflow_get_def or workflow_status for the "
                "full spec before editing a node you cannot see here.]"
            )
        lines.append("[End of workflow spec]")
        return "\n".join(lines) + "\n\n"
    except Exception:
        logger.debug("staged echo skipped (render error)", exc_info=True)
        return ""


#: Tools whose result should be followed by a spec echo, because the model's NEXT move is
#: likely a mutation and it must edit what it just saw (WF2-R20f).
STAGING_TOOLS = frozenset({"workflow_status", "workflow_get_def", "workflow_observe"})


def needs_staging(tool_name: str) -> bool:
    return tool_name in STAGING_TOOLS

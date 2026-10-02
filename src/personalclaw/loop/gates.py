"""Shared done-ness gate primitives for the unified loop watchdog.

These are the kind-agnostic checks the one supervisor evaluator
(:mod:`personalclaw.loop.supervisor`) runs for a policy that declares them — the
supervisor's own verification, never the
worker's self-report (the tenet: no agent certifies its own work). The watchdog
owns the lifecycle decision; these only supply the signal.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from personalclaw.cancellation import kill_timed_out
from personalclaw.security import mask_child_output

logger = logging.getLogger(__name__)

# A verify/test command is a build/lint/test run — generous bound so a real check
# (a full test suite) can finish, but a hung command can't wedge the poll loop.
VERIFY_TIMEOUT_SECS = 180

#: How much of what a check printed is kept: its END, where a test runner prints its summary and a
#: failing command its error. Read as it arrives, so a check that prints a lot never holds more.
CHECK_OUTPUT_TAIL = 2000


class CheckRefused(Exception):
    """A check the shell denylist refuses, raised where the caller's run records a failure
    (a workflow's verify gate, its guard, a ladder rung) so it fails with the rule's words
    rather than as a check that "could not be determined". The message names the rule."""


def refusal(cmd: str) -> str:
    """Why the shell denylist refuses *cmd* as a check (``security.denied_command``), or "".

    :func:`run_verify_command` asks it before running anything; a caller that records how a
    check went (a loop's pause, a stage's gate row, a workflow node's failure) asks it too, so
    the run says the check was refused and by which rule, not that it "could not run"."""
    from personalclaw.security import denied_command

    denied = denied_command((cmd or "").strip()) if (cmd or "").strip() else None
    return denied.refusal() if denied is not None else ""


def pause_for_refusal(loop_id: str, refused: str, publish: Callable[..., None]) -> None:
    """Pause a loop whose check the shell denylist refuses (*refused*, :func:`refusal`'s words),
    saying so on the loop for its owner, attended or not.

    A refusal is the one can't-run no later cycle changes, so cycling on would only spend toward
    the budget. Asked as the scheduler's question: asked again while the refusal holds, so a
    resumed loop whose check is still refused pauses again with the same words."""
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store
    from personalclaw.loop.loop import LoopStatus

    loop_files.write_question(
        loop_id,
        f"Paused: this loop's check was refused before it ran. {refused} Change the check "
        "command, or a pattern you added, then resume.",
        asked_by=loop_files.SCHEDULER_QUESTION,
    )
    store.update_status(loop_id, LoopStatus.NEEDS_INPUT)
    publish(loop_id, "needs_input", {"loop_id": loop_id})


@dataclass
class CheckReport:
    """What one run of a check command did, for the words its loop says about it.

    :func:`run_verify_command` fills one when its caller hands it one; the tristate it returns
    stays the decision. ``exit_code`` is set once the command ran to its end; ``output`` is the end
    of what it printed (its output and its errors as they came), masked as every view masks a
    child's output; ``not_run`` says why the command could not run, and is ``""`` once it ran."""

    exit_code: int | None = None
    output: str = ""
    not_run: str = ""


def _seconds(n: int) -> str:
    return f"{n} second" if n == 1 else f"{n} seconds"


async def _printed_tail(proc) -> bytes:
    """Everything *proc* prints until it exits, keeping only the last :data:`CHECK_OUTPUT_TAIL`
    bytes."""
    kept = bytearray()
    if proc.stdout is not None:
        while chunk := await proc.stdout.read(65536):
            kept += chunk
            del kept[:-CHECK_OUTPUT_TAIL]
    await proc.wait()
    return bytes(kept)


async def run_verify_command(
    cmd: str, cwd: str | None, *, label: str = "verify", report: CheckReport | None = None
) -> bool | None:
    """Run a verification command and read its exit code — the deterministic
    done-ness signal the supervisor owns.

    Returns a TRISTATE so a missing tool isn't misread as a real failure:
      * ``True``  — exit 0 (the check passed → the gate is met),
      * ``False`` — a genuine non-zero exit (the check ran + failed),
      * ``None``  — the command could NOT run (refused by the shell denylist or the
        safety screen, timed out, the binary is missing / exit 127, or its folder is
        gone). ``None`` means "can't tell" — the caller should NOT treat it as a pass.
        A caller that records the outcome on its run asks :func:`refusal` for the words
        of a refusal.

    *cwd* is the folder the command runs in. A caller passes the folder the work is in: with
    none, the command runs wherever the gateway process was started. *report*, when given, is
    filled with what the run did (:class:`CheckReport`), so its caller can say it in words.

    Best-effort + bounded; never raises. The loop is an auto-approved unattended run
    within its trust TTL, so the command executes under the host trust boundary —
    but it is still screened here, whoever persisted it: the shell denylist every
    command path asks, then the destructive-command screen.
    """
    report = report if report is not None else CheckReport()
    cmd = (cmd or "").strip()
    if not cmd:
        report.not_run = "no check command is set"
        return None
    from personalclaw.command_audit import audit_command_refusal
    from personalclaw.security import audit_bash_command, denied_command

    if (denied := denied_command(cmd)) is not None:
        audit_command_refusal(cmd, denied, source="loop_gate", operation=label)
        report.not_run = f"the shell denylist refused it ({denied.why()})"
        return None
    danger = audit_bash_command(cmd)
    if danger:
        logger.warning("loop gate: refusing to run %s command — %s", label, danger)
        report.not_run = f"the safety screen refused it ({danger})"
        return None
    try:
        # Resource ceiling: a loop verify command is agent-influenced (the loop
        # persisted it), so deliver the ``tool`` ceiling. Route through the post-exec
        # shim via an explicit ``/bin/sh -c`` — equivalent to create_subprocess_shell's
        # own shell, but the shim (prepended to argv) needs a real argv to wrap. No
        # preexec_fn: the limit is applied after exec, off the event loop's fork.
        from personalclaw.sandbox import PROFILE_TOOL, build_child_env, create_subprocess_limited

        proc = await create_subprocess_limited(
            "/bin/sh",
            "-c",
            cmd,
            profile=PROFILE_TOOL,
            cwd=cwd or None,
            # The loop's persisted command, so the child allowlist (`build_child_env`), like a
            # cron script: never a copy of the gateway's environment and the secrets in it.
            env=build_child_env(site="loop-verify"),
            # What it prints and what it reports as an error, as they came, so its report shows
            # the end a person reads (a test runner's summary, a command's error).
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # Own group: this is `/bin/sh -c <persisted command>`, so the shell forks a
            # test runner / `make` / a bundler that inherits the pipe. `proc.kill()` reaches
            # only the shell; the grandchild then holds the pipe and the reap below waits
            # for IT, turning a 180s bound into the command's own runtime. See
            # cancellation.kill_timed_out.
            start_new_session=True,
        )
    except Exception:
        logger.warning("loop gate: could not spawn %s command `%s`", label, cmd, exc_info=True)
        report.not_run = (
            f"its folder {cwd} does not exist"
            if cwd and not os.path.isdir(cwd)
            else "it could not be started (the gateway log has the error)"
        )
        return None
    try:
        printed = await asyncio.wait_for(_printed_tail(proc), timeout=VERIFY_TIMEOUT_SECS)
    except asyncio.TimeoutError:
        await kill_timed_out(proc)  # group-signalled + bounded reap (no zombie, no tree)
        logger.warning("loop gate: %s command timed out — `%s`", label, cmd)
        report.not_run = (
            f"it was still running after {_seconds(VERIFY_TIMEOUT_SECS)}, so it was stopped"
        )
        return None
    rc = proc.returncode
    report.exit_code = rc
    report.output = mask_child_output(printed, limit=CHECK_OUTPUT_TAIL, tail=True, one_line=False)
    if rc == 127:
        # The tool isn't installed here. For a verifiable gate this command IS the
        # done-ness signal, so a missing tool means the loop can NEVER self-complete
        # — surface it distinctly (not the silent "didn't pass yet" of a real fail)
        # so the un-runnable gate is diagnosable rather than a forever-spin. None.
        detail = mask_child_output(printed)
        logger.warning(
            "loop gate: %s command not runnable (exit 127 — tool missing?) `%s`%s",
            label,
            cmd,
            f" — {detail}" if detail else "",
        )
        report.not_run = "a program it runs is not installed here (exit 127)"
        return None
    return rc == 0


def verdict_is_pass(raw: str | None) -> bool:
    """Parse a strict-gate judge verdict ("PASS"/"FAIL") into a pass boolean.

    Decide on the LEADING alphabetic token, not a substring scan: a substring check
    wrongly passes a FAIL whose reason contains "pass" (e.g. "FAIL: passes 2 of 3").
    Conservative — PASS only when the first token is exactly PASS/PASSED; anything
    else (FAIL, prose, empty, ambiguous) is NOT passed (never advance on a misread)."""
    import re

    m = re.search(r"[A-Za-z]+", raw or "")
    return m is not None and m.group().upper() in ("PASS", "PASSED")


def verdict_rendered(raw: str | None) -> bool:
    """Whether the judge actually rendered a parseable PASS/FAIL verdict at all — its
    leading alphabetic token is PASS/PASSED/FAIL/FAILED. Empty (provider unavailable /
    stream errored → ``judge_verdict`` returns "") or pure prose means NO verdict was
    rendered. Callers distinguish a genuine FAIL (judge said so) from a can't-judge
    (model error/timeout): the latter must NOT count as FAIL when deterministic gates
    already passed, else a flaky judge permanently blocks a complete stage."""
    import re

    m = re.search(r"[A-Za-z]+", raw or "")
    return m is not None and m.group().upper() in ("PASS", "PASSED", "FAIL", "FAILED")


#: What a stage-gate judge may answer for ONE exit criterion: met, not met, or not answerable
#: from the evidence it was shown.
CRITERION_VERDICTS = ("pass", "fail", "cant_tell")
_CANT_TELL_SPELLINGS = {"cannot_tell": "cant_tell", "can't_tell": "cant_tell"}


def _criteria_object(raw: str) -> dict | None:
    """The first JSON object in *raw* that carries a ``criteria`` list, or None."""
    import json

    decoder = json.JSONDecoder()
    at = raw.find("{")
    while at >= 0:
        try:
            value, _end = decoder.raw_decode(raw, at)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and isinstance(value.get("criteria"), list):
            return value
        at = raw.find("{", at + 1)
    return None


def criteria_verdicts(raw: str | None, criteria: list[str]) -> tuple[list[dict], bool]:
    """A stage-gate judge's answer, one verdict per exit criterion, and whether it rendered one.

    The judge answers ``{"criteria": [{"n": 1, "verdict": "pass"|"fail"|"cant_tell", "reason":
    "…"}]}`` (``task-sdlc_stage_gate``). Each criterion gets ``{"criterion", "verdict",
    "reason"}``; one the judge did not answer, or answered with a word outside
    :data:`CRITERION_VERDICTS`, is ``cant_tell`` and says so. A one-word PASS or FAIL for the
    whole stage still counts as an answer: PASS passes every criterion, a FAIL that names none is
    ``cant_tell`` for each, because nothing says which one failed. No answer at all (an empty or
    unreadable reply: the judge's model was unavailable) is ``cant_tell`` for each, and not
    rendered, so the caller can tell a refusal from an outage (:func:`verdict_rendered`)."""
    text = raw or ""
    found = _criteria_object(text)
    if found is not None:
        answers: dict[int, dict] = {}
        for item in found["criteria"]:
            if not isinstance(item, dict):
                continue
            try:
                n = int(item["n"])
            except (KeyError, TypeError, ValueError):
                continue
            word = str(item.get("verdict") or "").strip().lower().replace("-", "_")
            word = _CANT_TELL_SPELLINGS.get(word, word)
            reason = " ".join(str(item.get("reason") or "").split())[:400]
            if word not in CRITERION_VERDICTS:
                word, reason = "cant_tell", "the judge's answer was not pass, fail or can't tell"
            answers[n] = {"verdict": word, "reason": reason}
        missing = {"verdict": "cant_tell", "reason": "the judge gave no answer for this criterion"}
        return [
            {"criterion": c, **answers.get(i, missing)} for i, c in enumerate(criteria, 1)
        ], True
    if verdict_rendered(text):
        if verdict_is_pass(text):
            whole = {"verdict": "pass", "reason": "the judge passed the stage as a whole"}
        else:
            whole = {
                "verdict": "cant_tell",
                "reason": "the judge failed the stage as a whole without saying which criterion",
            }
        return [{"criterion": c, **whole} for c in criteria], True
    none = {"verdict": "cant_tell", "reason": "the judge gave no verdict"}
    return [{"criterion": c, **none} for c in criteria], False


async def judge_verdict(prompt: str, *, loop_id: str) -> str:
    """One-shot judge over the JUDGE axis (the robust bridge path, not the
    config-only one_shot helper) — ``loops.judge_use_case``, 'reasoning' by default,
    which is deliberately NOT the ``loops`` axis the graded worker rides
    (MODEL-USE-CASES-V2; falls back to chat when unbound). The judge has NO write
    tools — any tool call it attempts is rejected. Returns the collected text (or ''
    on failure). Used by the code stage gate + any kind needing a conservative LLM
    verdict.

    The judge axis is NON-INTERACTIVE, so a >1-entry chain bound to it gets the
    call-failure advance (MODEL-USE-CASES-V2 T2.4) through the ONE shared walk in
    ``llm_helpers``: a provider failure from entry N rebuilds from N+1 instead of
    returning no verdict at all. That matters more here than almost anywhere else — an
    unrendered verdict is a can't-judge, and a can't-judge is what
    :func:`verdict_rendered` exists to keep from reading as FAIL, so without the advance
    a downed entry-0 provider silently converts a declared fallback into a permanently
    unjudgeable stage. A one-entry/unbound axis takes the plain single-resolve path,
    unchanged.

    The verdict's usage row is the loop's (*loop_id*), under the key the loop's spend is read
    by (``loop.manager.usage_key``). The judge is the axis's model itself, behind the spend guard
    whichever axis the owner put it on (``provider_bridge.resolve_metered_model``): bound to Chat
    or Code, it used to be handed the native agent, with tools, and its calls counted nowhere."""
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK
    from personalclaw.llm.events import EVENT_SPENT
    from personalclaw.llm_helpers import run_over_use_case_chain, use_case_chain
    from personalclaw.loop.judge import judge_use_case
    from personalclaw.loop.manager import usage_key
    from personalclaw.providers.provider_bridge import resolve_metered_model
    from personalclaw.usage_ledger import Attribution, recorder

    who = Attribution(source="loop", session_key=usage_key(loop_id))

    use_case = judge_use_case()
    # The chunks of the most recent FAILED attempt. ``_drain`` must RE-RAISE so the walk
    # can advance (a swallowed error would pin it to entry 0), so the partial text is
    # stashed here for the degrade paths rather than returned from there.
    partial: list[str] = []

    async def _drain(provider) -> str:
        """Collect one already-STARTED provider's verdict text, then shut it down."""
        chunks: list[str] = []
        record = recorder(provider, who)
        try:
            async for event in provider.stream(prompt):
                if event.kind == EVENT_TEXT_CHUNK:
                    chunks.append(event.text)
                elif event.kind == EVENT_PERMISSION_REQUEST:
                    # The judge must not act — deny any tool call (it should only reason).
                    try:
                        await provider.respond_permission(event, allow=False)  # type: ignore[attr-defined]  # noqa: E501
                    except Exception:
                        pass
                elif event.kind == EVENT_SPENT:
                    record(event)
                elif event.kind == EVENT_COMPLETE:
                    record(event)
                    break
        except Exception:
            partial[:] = chunks
            raise
        finally:
            try:
                await provider.shutdown()
            except Exception:
                pass
        return "".join(chunks)

    async def _start_and_drain(provider) -> str:
        """What the WALK runs per entry: this entry owns its own start + shutdown, so a
        provider that cannot even start is an advance rather than a dead verdict. Start
        is outside ``_drain`` because the plain path below must keep reporting a
        start failure at WARNING (unavailable provider) and a mid-stream failure at
        DEBUG (transient) — two different operator actions."""
        await provider.start()
        return await _drain(provider)

    chain = use_case_chain(use_case)
    if len(chain) > 1:
        try:
            return await run_over_use_case_chain(
                use_case, chain, _start_and_drain, label="loop gate judge chain"
            )
        except Exception:
            # Every entry failed. ONE warning — the walk already logged each advance — and
            # the last attempt's partial text still flows back, so ``verdict_rendered``
            # keeps distinguishing a truncated verdict from no verdict at all.
            logger.warning(
                "loop gate: every entry in the %s judge chain failed (%d entries)",
                use_case,
                len(chain),
                exc_info=True,
            )
            return "".join(partial)
    try:
        provider = resolve_metered_model(judge_use_case())
        await provider.start()
    except Exception:
        logger.warning("loop gate: judge provider unavailable", exc_info=True)
        return ""
    try:
        return await _drain(provider)
    except Exception:
        logger.debug("loop gate: judge stream errored", exc_info=True)
        return "".join(partial)

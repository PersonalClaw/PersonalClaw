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
from typing import TYPE_CHECKING

from personalclaw import run_processes
from personalclaw.cancellation import kill_timed_out, terminate_and_reap
from personalclaw.security import mask_child_output
from personalclaw.turn_streams import closing_stream

if TYPE_CHECKING:
    from personalclaw.guardrails.denylist import DenyDecision

logger = logging.getLogger(__name__)

# A verify/test command is a build/lint/test run — generous bound so a real check
# (a full test suite) can finish, but a hung command can't wedge the poll loop.
VERIFY_TIMEOUT_SECS = 180

#: How much of what a check printed is kept: its END, where a test runner prints its summary and a
#: failing command its error. Read as it arrives, so a check that prints a lot never holds more.
CHECK_OUTPUT_TAIL = 2000

#: How much of the rest is kept besides: the lines that name a file its caller watches (a test file
#: a stage changed, whose tests a long run reports long before its summary), at most this many
#: lines and characters, the first ones printed.
CHECK_NAMED_LINES = 80
CHECK_NAMED_CHARS = 4000

#: What a check is run with when its caller asks for each test to be named: the switch a test runner
#: reads from its environment for a line per test and how it ended, so the command itself is never
#: rewritten and a command that runs no such runner is unaffected. Each is added to a value the
#: check's environment already has. pytest lists every test in its closing summary, the passed
#: first and the failed last, where the end of its output is kept (``-rpsxXEf``: not ``-rA``,
#: which also prints what every passing test printed); go prints each test it runs (``-v``).
PER_TEST_REPORT_ENV = {"PYTEST_ADDOPTS": "-rpsxXEf", "GOFLAGS": "-v"}


class CheckRefused(Exception):
    """A check a denylist refuses, raised where the caller's run records a failure (a workflow's
    verify gate, its guard, a ladder rung) so it fails with the rule's words rather than as a
    check that "could not be determined". The message names the rule."""


def held(cmd: str, *, cwd: str) -> "DenyDecision":
    """The action denylist's answer for *cmd* as a check run in the folder *cwd* (``""``: the
    gateway's own) (``guardrails.denylist.check_command``).

    A check is work nobody answers, whatever started its loop or its run: it runs between a
    worker's cycles or a run's steps with nobody approving it. So it is judged as unattended work,
    and among the action denylist's rules a check that would stop, restart, update or reinstall the
    PersonalClaw it runs in is refused (``guardrails.self_destruct``), as a trigger's fire or a
    workflow step saying it is, and so is one that would delete the owner's home folder, the
    filesystem root or *cwd* (``protected_folders``). The shell denylist is left to the caller,
    which asks it next in the words every command path gives its refusal
    (``security.denied_command``)."""
    from personalclaw.guardrails.denylist import check_command
    from personalclaw.guardrails.policy import unattended_dispatch_key

    return check_command(
        (cmd or "").strip(), session_key=unattended_dispatch_key("loop_gate"), cwd=cwd
    )


def refusal(cmd: str, *, cwd: str) -> str:
    """Why *cmd*, run as a check in the folder *cwd*, is refused, or "": by the action denylist
    (:func:`held`), then by the shell denylist (``security.denied_command``), the order the action
    denylist asks its own.

    :func:`run_verify_command` asks both before running anything; a caller that records how a
    check went (a loop's pause, a stage's gate row, a workflow node's failure) asks this too, so
    the run says the check was refused and by which rule, not that it "could not run"."""
    from personalclaw.security import denied_command

    cmd = (cmd or "").strip()
    if not cmd:
        return ""
    if (decision := held(cmd, cwd=cwd)).blocked:
        return decision.refusal()
    denied = denied_command(cmd)
    return denied.refusal() if denied is not None else ""


def pause_for_refusal(loop_id: str, refused: str, publish: Callable[..., None]) -> None:
    """Pause a loop whose check a denylist refuses (*refused*, :func:`refusal`'s words), saying so
    on the loop for its owner, attended or not.

    A refusal is the one can't-run no later cycle changes, so cycling on would only spend toward
    the budget. Asked as the scheduler's question: asked again while the refusal holds, so a
    resumed loop whose check is still refused pauses again with the same words."""
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store
    from personalclaw.loop.loop import LoopStatus

    loop_files.write_question(
        loop_id,
        f"Paused: this loop's check was refused before it ran. {refused} Change the check "
        "command, or a pattern you added if one of yours refused it, then resume.",
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
    child's output; ``not_run`` says why the command could not run, and is ``""`` once it ran.

    ``watch`` names the caller cares about (the files a stage changed); ``named`` is every line of
    the output that mentions one, wherever it came (:data:`CHECK_NAMED_LINES`), masked the same
    way, so what a long run printed about them is not lost with its middle."""

    exit_code: int | None = None
    output: str = ""
    not_run: str = ""
    watch: tuple[str, ...] = ()
    named: str = ""


def _seconds(n: int) -> str:
    return f"{n} second" if n == 1 else f"{n} seconds"


async def _printed_tail(proc, watch: tuple[bytes, ...] = ()) -> tuple[bytes, bytes]:
    """Everything *proc* prints until it exits, keeping the last :data:`CHECK_OUTPUT_TAIL` bytes,
    and the first lines that name one of *watch* (:data:`CHECK_NAMED_LINES`,
    :data:`CHECK_NAMED_CHARS`)."""
    kept = bytearray()
    named = bytearray()
    lines = 0
    partial = b""

    def _note(line: bytes) -> None:
        nonlocal lines
        if lines < CHECK_NAMED_LINES and any(w in line for w in watch):
            room = CHECK_NAMED_CHARS - len(named)
            if room > 0:
                named.extend(line[:room] + b"\n")
                lines += 1

    if proc.stdout is not None:
        while chunk := await proc.stdout.read(65536):
            kept += chunk
            del kept[:-CHECK_OUTPUT_TAIL]
            if watch:
                *whole, partial = (partial + chunk).split(b"\n")
                for line in whole:
                    _note(line)
                partial = partial[-CHECK_NAMED_CHARS:]
    if watch and partial:
        _note(partial)
    await proc.wait()
    return bytes(kept), bytes(named)


def for_a_judge(
    text: str, *, what: str, source: str, source_type: str = "", source_id: str = ""
) -> tuple[str, bool]:
    """*text* from the work a loop's judge rules on (a file in the work folder, git's diff, what a
    check printed, a worker's finding) as the judge may be handed it, and whether it was withheld.

    Masked as every text a model is handed is (``security.redact_for_model``), then through the
    door every text from outside takes into a prompt (``outside_text.admit``): read by the
    injection screen, and fenced as data with its source. What the screen refuses is the door's
    sentence naming *what* it was (``outside_text.withheld``), so a criterion that turns on it is
    one its judge cannot tell, never one the text talked it into."""
    from personalclaw.outside_text import admit, withheld
    from personalclaw.security import redact_for_model

    admitted = admit(
        redact_for_model(text), source=source, source_type=source_type, source_id=source_id
    )
    if admitted.refused:
        return withheld(what, admitted.refused), True
    return admitted.text, False


def _per_test_env(env: dict[str, str]) -> dict[str, str]:
    """*env*, its test runners asked to name each test (:data:`PER_TEST_REPORT_ENV`)."""
    out = dict(env)
    for name, ask in PER_TEST_REPORT_ENV.items():
        out[name] = f"{out.get(name, '')} {ask}".strip()
    return out


async def run_verify_command(
    cmd: str,
    cwd: str | None,
    *,
    label: str = "verify",
    report: CheckReport | None = None,
    per_test: bool = False,
) -> bool | None:
    """Run a verification command and read its exit code — the deterministic
    done-ness signal the supervisor owns.

    Returns a TRISTATE so a missing tool isn't misread as a real failure:
      * ``True``  — exit 0 (the check passed → the gate is met),
      * ``False`` — a genuine non-zero exit (the check ran + failed),
      * ``None``  — the command could NOT run (refused by the action denylist, the shell
        denylist or the safety screen, timed out, the binary is missing / exit 127, or its
        folder is gone). ``None`` means "can't tell" — the caller should NOT treat it as a
        pass. A caller that records the outcome on its run asks :func:`refusal` for the
        words of a refusal.

    *cwd* is the folder the command runs in. A caller passes the folder the work is in: with
    none, the command runs wherever the gateway process was started. *report*, when given, is
    filled with what the run did (:class:`CheckReport`), so its caller can say it in words. With
    *per_test*, a test runner the command starts is asked to name each test it ran and how it
    ended (:data:`PER_TEST_REPORT_ENV`): what a judge needs to tell that one named test passed,
    where an exit code says only that all of them did.

    Best-effort + bounded; never raises. The loop is an auto-approved unattended run
    within its trust TTL, so the command executes under the host trust boundary —
    but it is still screened here, whoever persisted it: the action denylist a check is held
    to as unattended work (:func:`held`), the shell denylist every command path asks, then the
    destructive-command screen. A refusal by either denylist is recorded as every command
    path records one (``command_audit``).
    """
    report = report if report is not None else CheckReport()
    cmd = (cmd or "").strip()
    if not cmd:
        report.not_run = "no check command is set"
        return None
    from personalclaw.command_audit import audit_command_refusal
    from personalclaw.security import audit_bash_command, denied_command

    if (decision := held(cmd, cwd=cwd or "")).blocked:
        audit_command_refusal(cmd, decision, source="loop_gate", operation=label)
        report.not_run = decision.refusal().rstrip(".")
        return None
    if (denied := denied_command(cmd)) is not None:
        audit_command_refusal(cmd, denied, source="loop_gate", operation=label)
        report.not_run = f"the shell denylist refused it ({denied.why()})"
        return None
    danger = audit_bash_command(cmd, cwd=cwd or "")
    if danger:
        logger.warning("loop gate: refusing to run %s command — %s", label, danger)
        report.not_run = f"the safety screen refused it ({danger})"
        return None
    from personalclaw import sandbox
    from personalclaw.guardrails.policy import unattended_dispatch_key

    # A check runs with nobody answering it, whatever started its loop or its run, so it is held
    # to the egress tier of unattended work: where that gives it no network, it runs in the OS
    # sandbox without one.
    held_to = unattended_dispatch_key("loop_gate")
    try:
        argv, cleanup = sandbox.egress_bound_argv(["/bin/sh", "-c", cmd], run=held_to)
    except sandbox.SandboxEnforcementUnavailable as exc:
        audit_command_refusal(cmd, str(exc), source="loop_gate", operation=label, control="sandbox")
        report.not_run = str(exc).removeprefix(sandbox.NOT_RUN)
        return None
    try:
        # Resource ceiling: a loop verify command is agent-influenced (the loop
        # persisted it), so deliver the ``tool`` ceiling. Route through the post-exec
        # shim via an explicit ``/bin/sh -c`` — equivalent to create_subprocess_shell's
        # own shell, but the shim (prepended to argv) needs a real argv to wrap. No
        # preexec_fn: the limit is applied after exec, off the event loop's fork.
        from personalclaw.sandbox import PROFILE_TOOL, build_child_env, create_subprocess_limited

        # A check is a run of its own: what it starts (a test suite's server) ends when it exits.
        run = run_processes.own()
        # The loop's persisted command, so the child allowlist (`build_child_env`), like a cron
        # script: never a copy of the gateway's environment and the secrets in it.
        env = build_child_env(site="loop-verify", extra={run_processes.RUN_VARIABLE: run.mark})
        proc = await create_subprocess_limited(
            *argv,
            profile=PROFILE_TOOL,
            cwd=cwd or None,
            env=_per_test_env(env) if per_test else env,
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
        sandbox.remove_wrap(cleanup)
        logger.warning("loop gate: could not spawn %s command `%s`", label, cmd, exc_info=True)
        report.not_run = (
            f"its folder {cwd} does not exist"
            if cwd and not os.path.isdir(cwd)
            else "it could not be started (the gateway log has the error)"
        )
        return None
    watch = tuple(w.encode("utf-8") for w in report.watch if w)
    try:
        printed, named = await asyncio.wait_for(
            _printed_tail(proc, watch), timeout=VERIFY_TIMEOUT_SECS
        )
    except asyncio.TimeoutError:
        await kill_timed_out(proc)  # group-signalled + bounded reap (no zombie, no tree)
        logger.warning("loop gate: %s command timed out — `%s`", label, cmd)
        report.not_run = (
            f"it was still running after {_seconds(VERIFY_TIMEOUT_SECS)}, so it was stopped"
        )
        return None
    except asyncio.CancelledError:
        # The loop stopped while its check ran: the check is not the task's to abandon.
        await terminate_and_reap(proc)
        raise
    finally:
        await run.ended(proc.pid)
        sandbox.remove_wrap(cleanup)
    rc = proc.returncode
    report.exit_code = rc
    report.output = mask_child_output(printed, limit=CHECK_OUTPUT_TAIL, tail=True, one_line=False)
    report.named = mask_child_output(named, limit=CHECK_NAMED_CHARS, one_line=False)
    if rc and (note := sandbox.no_network_note(held_to)):
        report.output = f"{report.output}\n{note}".strip()
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
    :data:`CRITERION_VERDICTS`, is ``cant_tell`` and says so. A fail the judge marks
    ``"outside": true`` — not met for a reason outside the stage's work, so nothing its workers do
    will meet it — carries ``"outside": True``. A one-word PASS or FAIL for the
    whole stage still counts as an answer: PASS passes every criterion, a FAIL that names none is
    ``cant_tell`` for each, because nothing says which one failed. No answer at all (an empty or
    unreadable reply: the judge's model was unavailable) is ``cant_tell`` for each, and not
    rendered, so the caller can tell a refusal from an outage (:func:`verdict_rendered`)."""
    from personalclaw.safety_flags import yes_or_no

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
            if word == "fail" and yes_or_no(item.get("outside")) is True:
                answers[n]["outside"] = True
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
    (falls back to chat when unbound). The judge has NO write
    tools — any tool call it attempts is rejected. Returns the collected text (or ''
    on failure). Used by the code stage gate + any kind needing a conservative LLM
    verdict.

    The judge axis is NON-INTERACTIVE, so a >1-entry chain bound to it gets the
    call-failure advance through the ONE shared walk in
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
            async with closing_stream(provider.stream(prompt)) as events:
                async for event in events:
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

"""Shared done-ness gate primitives for the unified loop watchdog.

These are the kind-agnostic checks a :class:`personalclaw.loop.kinds.LoopKindStrategy`
calls from ``is_done_signal`` — the supervisor's own verification, never the
worker's self-report (the tenet: no agent certifies its own work). The watchdog
owns the lifecycle decision; these only supply the signal.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# A verify/test command is a build/lint/test run — generous bound so a real check
# (a full test suite) can finish, but a hung command can't wedge the poll loop.
VERIFY_TIMEOUT_SECS = 180


async def run_verify_command(cmd: str, cwd: str | None, *, label: str = "verify") -> bool | None:
    """Run a verification command and read its exit code — the deterministic
    done-ness signal the supervisor owns.

    Returns a TRISTATE so a missing tool isn't misread as a real failure:
      * ``True``  — exit 0 (the check passed → the gate is met),
      * ``False`` — a genuine non-zero exit (the check ran + failed),
      * ``None``  — the command could NOT run (blocked by the safety screen, timed
        out, or the binary is missing / exit 127). ``None`` means "can't tell" —
        the caller should NOT treat it as a pass, and the watchdog defers (it does
        not complete on an un-runnable gate, but logs it so the spin is diagnosable).

    Best-effort + bounded; never raises. The loop is an auto-approved unattended run
    within its trust TTL, so the command executes under the host trust boundary —
    but we still screen it defensively (a command persisted before validation, or a
    bypass path) and refuse anything destructive.
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return None
    from personalclaw.security import audit_bash_command

    danger = audit_bash_command(cmd)
    if danger:
        logger.warning("loop gate: refusing to run %s command — %s", label, danger)
        return None
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=cwd or None,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception:
        logger.warning("loop gate: could not spawn %s command `%s`", label, cmd, exc_info=True)
        return None
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=VERIFY_TIMEOUT_SECS)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()  # reap the killed child (no zombie)
        except ProcessLookupError:
            pass
        logger.warning("loop gate: %s command timed out — `%s`", label, cmd)
        return None
    rc = proc.returncode
    if rc == 127:
        # The tool isn't installed here. For a verifiable gate this command IS the
        # done-ness signal, so a missing tool means the loop can NEVER self-complete
        # — surface it distinctly (not the silent "didn't pass yet" of a real fail)
        # so the un-runnable gate is diagnosable rather than a forever-spin. None.
        detail = (err or b"").decode("utf-8", "replace").strip()[:200]
        logger.warning(
            "loop gate: %s command not runnable (exit 127 — tool missing?) `%s`%s",
            label,
            cmd,
            f" — {detail}" if detail else "",
        )
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


async def judge_verdict(prompt: str) -> str:
    """One-shot judge over the 'reasoning' provider (the robust bridge path, not the
    config-only one_shot helper). The judge has NO write tools — any tool call it
    attempts is rejected. Returns the collected text (or '' on failure). Used by the
    code stage gate + any kind needing a conservative LLM verdict."""
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK
    from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

    try:
        provider = resolve_provider_for_use_case("reasoning")
        await provider.start()
    except Exception:
        logger.warning("loop gate: judge provider unavailable", exc_info=True)
        return ""
    chunks: list[str] = []
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
            elif event.kind == EVENT_COMPLETE:
                break
    except Exception:
        logger.debug("loop gate: judge stream errored", exc_info=True)
    finally:
        try:
            await provider.shutdown()
        except Exception:
            pass
    return "".join(chunks)

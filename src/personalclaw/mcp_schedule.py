"""MCP server exposing scheduling tools to the ACP agent.

Runs as ``personalclaw mcp-schedule`` — ACP agent spawns it as a child process
and calls tools via JSON-RPC over stdio (MCP protocol).

Tools:
    schedule_list       — list all scheduled jobs
    schedule_add        — add a job (every/cron/at)
    schedule_update     — update an existing job
    schedule_remove     — remove a job by ID
    schedule_remove_all — remove all jobs
    schedule_pause      — pause a job
    schedule_resume     — resume a paused job
    schedule_trigger    — fire a job immediately
"""

import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any

from personalclaw.config.loader import config_dir
from personalclaw.mcp_core import _resolve_session_key
from personalclaw.schedule import (
    ScheduleService,
    compute_next_run_ts,
    format_schedule,
    get_local_tz,
)
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

# Patterns for _parse_time_string
_RE_IN_DURATION = re.compile(
    r"^in\s+(\d+)\s*(s|sec|second|seconds|m|min|minute|minutes|h|hr|hour|hours)$", re.I
)
_UNIT_SECS = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hour": 3600,
    "hours": 3600,
}


def _parse_time_string(s: str) -> float | str:
    """Parse a human time string into a Unix timestamp. Returns error string on failure."""
    s = s.strip()
    _, tz = get_local_tz()
    now = datetime.now(tz)

    # "in 5 minutes", "in 2 hours"
    m = _RE_IN_DURATION.match(s)
    if m:
        secs = int(m.group(1)) * _UNIT_SECS[m.group(2).lower()]
        return time.time() + secs

    # Try common formats with optional "tomorrow"
    tomorrow = False
    text = s
    if text.lower().startswith("tomorrow"):
        tomorrow = True
        text = re.sub(r"^at\b\s*", "", text[8:].strip())

    # "5pm", "5:30pm", "17:00", "9:30am"
    for fmt in ("%I%p", "%I:%M%p", "%H:%M", "%I %p", "%I:%M %p"):
        try:
            parsed = datetime.strptime(text, fmt)
            result = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            if tomorrow:
                result += timedelta(days=1)
            elif result <= now:
                result += timedelta(days=1)  # "5pm" when it's already 6pm → tomorrow
            return result.timestamp()
        except ValueError:
            continue

    # ISO-ish: "2026-03-28 14:00", "2026-03-28T14:00"
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=now.tzinfo)
            return parsed.timestamp()
        except ValueError:
            continue

    return f"Error: could not parse time '{s}'. Examples: '5pm', 'in 30 minutes', 'tomorrow 9am'"


def _list_tools() -> list[dict[str, Any]]:
    """Return MCP tool definitions."""
    return [
        {
            "name": "schedule_list",
            "description": "List all scheduled cron jobs",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "schedule_add",
            "description": (
                "Add a scheduled cron job. Use when the user says 'every', "
                "'daily', 'weekly', 'remind me', 'check regularly', or "
                "'schedule'. Requires name + message, plus one of: every "
                "(seconds), cron_expr, at (unix timestamp), delay (seconds "
                "from now), or at_time (human string like '5pm', "
                "'tomorrow 9am', 'in 2 hours')."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Job name"},
                    "message": {"type": "string", "description": "Message to send to agent"},
                    "every": {
                        "type": "integer",
                        "description": "Interval in seconds (min 60)",
                    },
                    "cron_expr": {
                        "type": "string",
                        "description": "Standard 5-field cron expression: "
                        '"min hour dom month dow" where dow: 0=Sun,1=Mon..6=Sat '
                        '(e.g. "0 9 * * 1-5" for weekdays at 9AM UTC, '
                        '"30 15 * * 2,4" for Tue/Thu at 3:30PM UTC)',
                    },
                    "at": {
                        "type": "number",
                        "description": "Unix timestamp for one-shot job (auto-deletes after)",
                    },
                    "delay": {
                        "type": "number",
                        "description": "Seconds from now for one-shot job (e.g. 120 for 2 minutes). "  # noqa: E501
                        "Converted to 'at' internally. Prefer this over 'at'.",
                    },
                    "at_time": {
                        "type": "string",
                        "description": "Human time string for one-shot job, parsed server-side. "
                        "Examples: '5pm', '17:00', 'tomorrow 9:30am', 'in 2 hours', "
                        "'2026-03-28 14:00'. Uses server local timezone. "
                        "Prefer this over 'at' for absolute times.",
                    },
                    "channel": {
                        "type": "string",
                        "description": "Channel ID to post results to (e.g. 'C0AP3QR7Z4M'). "
                        "If omitted, posts in the originating thread/DM.",
                    },
                    "thread_ts": {
                        "type": "string",
                        "description": "Channel thread timestamp to reply in. "
                        "Use with channel to post results as a thread reply instead of a new message.",  # noqa: E501
                    },
                    "agent": {
                        "type": "string",
                        "description": "Agent name for this job (e.g. 'my-code-agent'). "
                        "Empty or omitted uses the default personalclaw agent.",
                    },
                    "silent": {
                        "type": "boolean",
                        "description": "When true, suppress automatic message delivery. "
                        "The agent controls when to notify via send_message.",
                    },
                    "approval_mode": {
                        "type": "string",
                        "enum": ["", "auto"],
                        "description": "Tool approval mode for this job. "
                        "'auto' auto-approves all tools without prompting. "
                        "Empty or omitted uses default hook-based approval.",
                    },
                    "skip_dates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": 'ISO dates to skip (e.g. ["2026-04-06", "2026-12-25"]). '
                        "Job silently does not fire on these dates. Evaluated in job's timezone.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone for skip_dates evaluation "
                        "(e.g. 'Europe/Luxembourg'). Falls back to global config timezone.",
                    },
                    "persistent_session": {
                        "type": "boolean",
                        "description": "Whether this cron reuses one agent session across "
                        "runs (True, default) or opens a fresh session per run (False). "
                        "Set False for polling/scanner jobs with no conversational state — "
                        "avoids unbounded context growth. Set True (or omit) for "
                        "conversational reminders that should remember prior runs.",
                    },
                    "strict_schedule": {
                        "type": "boolean",
                        "description": "When true, fire exactly on schedule with no jitter. "
                        "Default false — jobs get random delay (0-20min hourly, 0-2h daily) "
                        "to spread load.",
                    },
                    "script": {
                        "type": "string",
                        "description": "Zero-token Python script 'file.py:func' under "
                        "~/.personalclaw/crons/ (runs deterministically, no LLM). "
                        "Mutually exclusive with command.",
                    },
                    "command": {
                        "type": "string",
                        "description": "Zero-token shell command (runs deterministically in the "
                        "sandbox, no LLM). Mutually exclusive with script.",
                    },
                    "zt_timeout": {
                        "type": "integer",
                        "description": "Timeout (s) for a zero-token script/command run. "
                        "0 = default (30s script / 300s command).",
                    },
                },
                "required": ["name", "message"],
            },
        },
        {
            "name": "schedule_update",
            "description": "Update an existing cron job's name, message, schedule, agent, or channel.",  # noqa: E501
            "inputSchema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID to update"},
                    "name": {"type": "string", "description": "New job name"},
                    "message": {"type": "string", "description": "New message"},
                    "cron_expr": {"type": "string", "description": "New cron expression"},
                    "every": {"type": "integer", "description": "New interval in seconds (min 60)"},
                    "agent": {"type": "string", "description": "New agent name"},
                    "channel": {"type": "string", "description": "New channel ID"},
                    "thread_ts": {
                        "type": "string",
                        "description": "New thread timestamp to reply in.",
                    },
                    "approval_mode": {
                        "type": "string",
                        "enum": ["", "auto"],
                        "description": "New tool approval mode",
                    },
                    "silent": {"type": "boolean", "description": "Whether the job runs silently"},
                    "skip_dates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "ISO dates to skip. Replaces existing list.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone for skip_dates evaluation.",
                    },
                    "strict_schedule": {
                        "type": "boolean",
                        "description": "When true, fire exactly on schedule with no jitter.",
                    },
                    "script": {
                        "type": "string",
                        "description": "Zero-token Python script 'file.py:func' under "
                        "~/.personalclaw/crons/ (runs deterministically, no LLM). "
                        "Mutually exclusive with command.",
                    },
                    "command": {
                        "type": "string",
                        "description": "Zero-token shell command (runs deterministically in the "
                        "sandbox, no LLM). Mutually exclusive with script.",
                    },
                    "zt_timeout": {
                        "type": "integer",
                        "description": "Timeout (s) for a zero-token script/command run. "
                        "0 = default (30s script / 300s command).",
                    },
                },
                "required": ["job_id"],
            },
        },
        {
            "name": "schedule_remove",
            "description": "Remove a cron job by ID",
            "inputSchema": {
                "type": "object",
                "properties": {"job_id": {"type": "string", "description": "Job ID"}},
                "required": ["job_id"],
            },
        },
        {
            "name": "schedule_remove_all",
            "description": "Remove all cron jobs",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "schedule_pause",
            "description": "Pause a cron job",
            "inputSchema": {
                "type": "object",
                "properties": {"job_id": {"type": "string", "description": "Job ID"}},
                "required": ["job_id"],
            },
        },
        {
            "name": "schedule_resume",
            "description": "Resume a paused cron job",
            "inputSchema": {
                "type": "object",
                "properties": {"job_id": {"type": "string", "description": "Job ID"}},
                "required": ["job_id"],
            },
        },
        {
            "name": "schedule_trigger",
            "description": "Fire a cron job immediately (on-demand), regardless of its schedule. "
            "Runs through the live gateway and returns at once; the run appears in execution history.",  # noqa: E501
            "inputSchema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID to trigger now"}
                },
                "required": ["job_id"],
            },
        },
        {
            "name": "schedule_natural",
            "description": (
                "Schedule a RECURRING job from a plain-English cadence — e.g. 'every "
                "weekday at 9am', 'the first of each month', 'every 30 minutes'. The "
                "cadence is converted to a validated cron expression and the job is "
                "created. For a ONE-OFF time ('in 5 minutes', 'tomorrow 3pm') use "
                "schedule_add with delay/at/at_time instead."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Job name"},
                    "message": {
                        "type": "string",
                        "description": "The agent prompt to run on each fire.",
                    },
                    "cadence": {
                        "type": "string",
                        "description": "Plain-English recurring cadence (e.g. 'every weekday at 9am').",  # noqa: E501
                    },
                    "channel": {"type": "string", "description": "Optional delivery channel id."},
                    "silent": {
                        "type": "boolean",
                        "description": "Suppress delivery (run quietly).",
                    },
                },
                "required": ["name", "message", "cadence"],
            },
        },
    ]


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate tool arguments against schema. Returns cleaned args."""
    from personalclaw.validation import MCP_SCHEDULE_SCHEMAS, validate_tool_args

    schema = MCP_SCHEDULE_SCHEMAS.get(name)
    if schema:
        cleaned = validate_tool_args(args, schema)
    else:
        cleaned = args  # tools without schemas (schedule_list, schedule_remove_all) pass through
    # Semantic check: reject past timestamps for one-shot jobs
    at_ts = cleaned.get("at")
    if at_ts is not None and at_ts < time.time():
        from personalclaw.validation import ValidationError

        raise ValidationError("at", f"timestamp {int(at_ts)} is in the past")
    return cleaned


def _call_tool(name: str, raw_args: dict[str, Any]) -> str:
    """Execute a cron tool and return the result as text."""
    from personalclaw.mcp_shared import call_tool_with_logging

    return call_tool_with_logging(
        name,
        raw_args,
        _validate_args,
        _call_tool_inner,
        session_key="mcp_schedule",
        downstream_service="personalclaw-schedule",
    )


def _nl_to_cron_blocking(cadence: str) -> tuple[str, str]:
    """Run the async NL→cron conversion to completion from this sync dispatch."""
    import asyncio

    from personalclaw.nl_to_cron import nl_to_cron

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(nl_to_cron(cadence))
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, nl_to_cron(cadence)).result(timeout=60)


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    """Execute a cron tool (post-validation)."""
    svc = ScheduleService(base_dir=config_dir())

    if name == "schedule_natural":
        cadence = str(args.get("cadence") or "").strip()
        if not cadence:
            return "Error: cadence is required."
        cron_expr, err = _nl_to_cron_blocking(cadence)
        if err:
            return f"Error: {err}"
        # Delegate to the validated schedule_add path with the derived cron expr.
        add_args = {
            "name": args.get("name", ""),
            "message": args.get("message", ""),
            "cron_expr": cron_expr,
        }
        if args.get("channel"):
            add_args["channel"] = args["channel"]
        if args.get("silent"):
            add_args["silent"] = args["silent"]
        result = _call_tool_inner("schedule_add", add_args)
        if not result.startswith("Error"):
            from personalclaw.schedule import _humanize_cron

            try:
                result += f"\n(interpreted '{cadence}' as cron: {cron_expr} — {_humanize_cron(cron_expr)})"  # noqa: E501
            except Exception:
                result += f"\n(cron: {cron_expr})"
        return result

    if name == "schedule_list":
        jobs = svc.list_jobs(include_disabled=True)
        if not jobs:
            return "No cron jobs."
        active = sum(1 for j in jobs if j.enabled)
        paused = len(jobs) - active
        header = f"{len(jobs)} cron job(s): {active} active, {paused} paused\n"
        lines: list[str] = [header]
        now = time.time()
        tz_name, local_tz = get_local_tz()
        for j in jobs:
            status = "✅ active" if j.enabled else "⏸️ paused"
            sched = format_schedule(j.schedule, tz_name=tz_name)
            nxt = compute_next_run_ts(j, now=now)
            next_line = ""
            if nxt is not None:
                delta = nxt - now
                if delta >= 86400:
                    d = int(delta // 86400)
                    h = int((delta % 86400) // 3600)
                    rel = f"in {d}d {h}h"
                elif delta >= 3600:
                    h = int(delta // 3600)
                    m = int((delta % 3600) // 60)
                    rel = f"in {h}h {m}m"
                elif delta > 0:
                    m = int(delta // 60)
                    rel = f"in {m}m" if m >= 1 else "in <1m"
                else:
                    rel = "now"
                local_str = datetime.fromtimestamp(nxt, tz=local_tz).strftime(
                    "%Y-%m-%d %I:%M %p %Z"
                )
                next_line = f"\n  Next run: {local_str} ({rel})"
            next_line = redact_credentials(redact_exfiltration_urls(next_line)[0])[0]
            san_name = redact_credentials(redact_exfiltration_urls(j.name)[0])[0]
            san_msg = redact_credentials(redact_exfiltration_urls(j.message)[0])[0]
            san_sched = redact_credentials(redact_exfiltration_urls(sched)[0])[0]
            lines.append(
                f"• {san_name} ({status})\n  ID: {j.id} | {san_sched}{next_line}\n  → {san_msg}"
            )
        return "\n".join(lines)

    if name == "schedule_add":
        n = args["name"]
        msg = args["message"]
        every = args.get("every")
        cron_arg = args.get("cron_expr")
        at_ts = args.get("at")
        delay = args.get("delay")
        at_time = args.get("at_time")
        if delay is not None and at_ts is None:
            at_ts = time.time() + delay
        if at_time is not None and at_ts is None:
            parsed = _parse_time_string(at_time)
            if isinstance(parsed, str):
                return parsed  # error message
            at_ts = parsed
        # Guard against past timestamps from any source (at, delay, at_time)
        if at_ts is not None and at_ts < time.time():
            local = datetime.fromtimestamp(at_ts).astimezone()
            return f"Error: resolved time {local.strftime('%I:%M %p %Z')} is in the past"
        channel = (args.get("channel") or "").strip() or None
        if channel is None:
            channel = os.environ.get("PERSONALCLAW_CHANNEL_ID") or None
        if not every and not cron_arg and not at_ts:
            return "Error: provide every, cron_expr, at, delay, or at_time"
        # The job's action — a deterministic command/script (zero-token, no LLM)
        # or an agent turn. ``message`` is the script's args when a script/command
        # is set, else the agent prompt.
        from personalclaw.schedule import (
            make_agent_action,
            make_command_action,
            make_script_action,
        )

        script = (args.get("script") or "").strip()
        command = (args.get("command") or "").strip()
        zt_timeout = int(args.get("zt_timeout") or 0)
        if script and command:
            return "Error: Cannot specify both script and command"
        agent = args.get("agent", "")
        approval_mode = args.get("approval_mode", "")
        if script:
            action = make_script_action(script, zt_timeout)
        elif command:
            action = make_command_action(command, zt_timeout)
        else:
            action = make_agent_action(message=msg, agent=agent, approval_mode=approval_mode)
        try:
            thread_ts = (args.get("thread_ts") or "").strip() or None
            job = svc.add_job(
                name=n,
                action=action,
                every_secs=every,
                cron_expr=cron_arg,
                at_ts=at_ts,
                channel=channel,
                thread_ts=thread_ts,
                delete_after_run=bool(at_ts),
            )
        except ValueError as e:
            return f"Error: {e}"
        silent = args.get("silent", False)
        session_key = _resolve_session_key()
        if silent:
            job.silent = True
        if session_key:
            job.session_key = session_key
        skip_dates = args.get("skip_dates", [])
        tz = args.get("timezone", "")
        if skip_dates:
            job.skip_dates = skip_dates
        if tz:
            job.timezone = tz
        # persistent_session: only override the default (True) when explicitly provided.
        # Type is enforced by validation.py SCHEDULE_ADD_SCHEMA (FieldSpec ... bool),
        # so we do NOT accept raw-truthy values here — only a real bool.
        persistent_session = args.get("persistent_session")
        persistent_session_explicit = isinstance(persistent_session, bool)
        if isinstance(persistent_session, bool):
            job.persistent_session = persistent_session
        strict_schedule = args.get("strict_schedule")
        strict_schedule_explicit = isinstance(strict_schedule, bool)
        if isinstance(strict_schedule, bool):
            job.strict_schedule = strict_schedule
        if (
            agent
            or silent
            or approval_mode
            or session_key
            or skip_dates
            or tz
            or persistent_session_explicit
            or strict_schedule_explicit
        ):
            svc._save()
        sched_str = format_schedule(job.schedule)
        return f"Added job: {job.id} ({job.name}) [{sched_str}]. Tell the user: scheduled for {sched_str}."  # noqa: E501

    if name == "schedule_update":
        from personalclaw.schedule import (
            make_agent_action,
            make_command_action,
            make_script_action,
        )

        jid = args["job_id"]
        kwargs: dict[str, Any] = {}
        if args.get("name"):
            kwargs["name"] = args["name"]
        for key in ("channel", "thread_ts"):
            if key in args:
                val = args[key]
                if key == "thread_ts":
                    val = (val or "").strip() or None
                kwargs[key] = val
        if "silent" in args:
            kwargs["silent"] = args["silent"]
        if "skip_dates" in args:
            kwargs["skip_dates"] = args["skip_dates"]
        if "timezone" in args:
            kwargs["timezone"] = args["timezone"]
        if "strict_schedule" in args:
            kwargs["strict_schedule"] = args["strict_schedule"]
        if "cron_expr" in args and args["cron_expr"]:
            kwargs["cron_expr"] = args["cron_expr"]
        if "every" in args and args["every"]:
            kwargs["every_secs"] = args["every"]
        # Action edits — script/command (zero-token) else agent prompt/agent/mode.
        # Build a full action from the patched fields layered over the current job.
        action_keys = ("message", "agent", "approval_mode", "script", "command", "zt_timeout")
        if any(k in args for k in action_keys):
            cur = next((j for j in svc.list_jobs(include_disabled=True) if j.id == jid), None)
            if cur is None:
                return f"Job not found: {jid}"
            script = (args.get("script", cur.script) or "").strip()
            command = (args.get("command", cur.command) or "").strip()
            if "script" in args and "command" not in args:
                command = ""  # switching to script mode clears command
            elif "command" in args and "script" not in args:
                script = ""
            if script and command:
                return "Error: Cannot specify both script and command"
            zt_timeout = int(args.get("zt_timeout", cur.zt_timeout) or 0)
            if script:
                kwargs["action"] = make_script_action(script, zt_timeout)
            elif command:
                kwargs["action"] = make_command_action(command, zt_timeout)
            else:
                kwargs["action"] = make_agent_action(
                    message=args.get("message", cur.message),
                    agent=args.get("agent", cur.agent_id),
                    model=cur.model,
                    approval_mode=args.get("approval_mode", cur.approval_mode),
                )
        if not kwargs:
            return "Error: no fields to update"
        try:
            updated = svc.update_job(jid, **kwargs)
        except ValueError as e:
            return f"Error: {e}"
        if not updated:
            return f"Job not found: {jid}"
        sched_str = format_schedule(updated.schedule)
        return f"Updated job: {updated.id} ({updated.name}) [{sched_str}]"

    if name == "schedule_remove":
        jid = args["job_id"]
        if svc.remove_job(jid):
            return f"Removed job: {jid}"
        return f"Job not found: {jid}"

    if name == "schedule_remove_all":
        from personalclaw.sel import sel

        jobs = svc.list_jobs(include_disabled=True)
        if not jobs:
            return "No cron jobs to remove."
        session_key = _resolve_session_key()
        is_cli = os.environ.get("PERSONALCLAW_CLI", "") == "1"
        if not is_cli:
            if not session_key:
                sel().log_tool_invocation(
                    session_key="mcp_schedule",
                    source="mcp",
                    tool_name="schedule_remove_all",
                    tool_kind="authz",
                    outcome="denied",
                    error="no session key set",
                )
                return "Error: no session key set; cannot determine job ownership."
            jobs = [j for j in jobs if j.session_key == session_key]
            if not jobs:
                return "No cron jobs owned by this session."
            sel().log_tool_invocation(
                session_key=session_key,
                source="mcp",
                tool_name="schedule_remove_all",
                tool_kind="authz",
                outcome="scoped",
                resources=f"session={session_key} count={len(jobs)}",
            )
        else:
            sel().log_tool_invocation(
                session_key="mcp_schedule",
                source="mcp",
                tool_name="schedule_remove_all",
                tool_kind="authz",
                outcome="cli_admin",
                resources=f"count={len(jobs)}",
            )
        for j in jobs:
            svc.remove_job(j.id)
        return f"Removed {len(jobs)} job(s)."

    if name == "schedule_pause":
        jid = args["job_id"]
        if svc.enable_job(jid, enabled=False):
            return f"Paused job: {jid}"
        return f"Job not found: {jid}"

    if name == "schedule_resume":
        jid = args["job_id"]
        if svc.enable_job(jid, enabled=True):
            return f"Resumed job: {jid}"
        return f"Job not found: {jid}"

    if name == "schedule_trigger":
        # Fire via the RUNNING gateway (not the fresh local svc, which has no
        # live timer) — POSTs /api/triggers/schedule:{id}/run with the secret.
        from personalclaw.schedule_trigger import trigger_schedule_job

        ok, message = trigger_schedule_job(args["job_id"])
        return message if ok else f"Error: {message}"

    return f"Unknown tool: {name}"


def run_mcp_server() -> None:
    """Run MCP stdio server — reads JSON-RPC from stdin, writes to stdout."""
    from personalclaw.mcp_shared import run_mcp_stdio_loop

    run_mcp_stdio_loop("personalclaw-schedule", "1.0.0", _list_tools, _call_tool)

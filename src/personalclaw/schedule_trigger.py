"""On-demand Schedule job triggering.

A thin helper that fires a scheduled job **immediately** by POSTing to the
running gateway's run route — never by instantiating a fresh ``ScheduleService``
(a fresh service has no live timer/reaper and would orphan the run). Reuses
PersonalClaw's internal-secret IPC (``mcp_core._post`` → ``X-Internal-Secret``),
so both the CLI (``personalclaw cron trigger``) and the ``schedule_trigger`` MCP
tool go through the same authenticated localhost path.
"""

from __future__ import annotations

import re

_JOB_ID_RE = re.compile(r"^[a-f0-9]{6,16}$")


def trigger_schedule_job(job_id: str) -> tuple[bool, str]:
    """Fire job ``job_id`` now via the running gateway. Returns ``(ok, message)``.

    Validates the id format locally, then POSTs to
    ``/api/triggers/schedule:{id}/run`` (non-blocking on the server — it spawns
    the run and returns immediately). A gateway that is down / unreachable yields
    a friendly error rather than raising.
    """
    job_id = (job_id or "").strip()
    if not _JOB_ID_RE.match(job_id):
        return False, f"invalid job id: {job_id!r}"
    # Deferred import: keeps this module importable in contexts where the MCP
    # core isn't wired, and avoids a circular import at module load.
    from personalclaw.mcp_core import _post

    resp = _post(f"/api/triggers/schedule:{job_id}/run", {})
    if not isinstance(resp, dict):
        return False, "unexpected response from gateway"
    if resp.get("error"):
        return False, str(resp["error"])
    if resp.get("ok"):
        name = resp.get("name") or job_id
        return True, f"triggered '{name}'"
    if resp.get("running"):
        return False, "job is already running"
    return False, "trigger failed"

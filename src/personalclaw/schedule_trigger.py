"""On-demand triggering of a scheduled job, for ``personalclaw cron trigger``.

A thin helper that fires a job **immediately** by POSTing to the running gateway's run route —
never by instantiating a fresh ``ScheduleService`` (a fresh service has no live timer/reaper and
would orphan the run). Reuses PersonalClaw's internal-secret IPC (``mcp_core._post`` →
``X-Internal-Secret``), the authenticated localhost path the MCP tools use.
"""

from __future__ import annotations

from urllib.parse import quote


def trigger_schedule_job(job_id: str) -> tuple[bool, str]:
    """Fire job ``job_id`` now via the running gateway. Returns ``(ok, message)``.

    ``job_id`` is a trigger store id, one ``personalclaw cron list`` shows, and the caller has
    looked it up there: the store is the one complete list of the ids the writers mint
    (``clock:<name>`` from ``cron add``, ``system:…``, ``app:<app>:<job>``,
    ``report-schedule:…``), and an app's job name can hold any character, so no pattern can
    stand in for it. The id is percent-encoded into the path, as the dashboard's Run button
    does, so no character in it can change which route the request reaches.

    POSTs to ``/api/triggers/schedule:{id}/run`` (non-blocking on the server — it spawns the run
    and returns immediately). A gateway that is down / unreachable yields a friendly error
    rather than raising.

    The call names the work it does, as every call made with the internal credential must (the
    gateway refuses one that names none): the job's own, ``cron:<id>``, as a fire of the job is
    named, unless it is made in a session's work (the command run in an agent's shell names that
    agent's chat).
    """
    job_id = (job_id or "").strip()
    if not job_id:
        return False, "no job id given"
    # Deferred import: keeps this module importable in contexts where the MCP
    # core isn't wired, and avoids a circular import at module load.
    from personalclaw import mcp_core, session_keys

    named = mcp_core._resolve_session_key()
    token = None if named else mcp_core.set_current_session_key(session_keys.TRIGGER.key(job_id))
    try:
        resp = mcp_core._post(f"/api/triggers/schedule:{quote(job_id, safe='')}/run", {})
    finally:
        if token is not None:
            mcp_core.reset_current_session_key(token)
    if not isinstance(resp, dict):
        return False, "unexpected response from gateway"
    if resp.get("error"):
        return False, str(resp["error"])
    if resp.get("refused"):
        # The gateway's own sentence — a missing grant says which and how to give it, the kill
        # switch says how to resume. "trigger failed" in its place told the caller nothing.
        return False, str(resp["refused"])
    name = resp.get("name") or job_id
    if resp.get("ok"):
        return True, f"triggered '{name}'"
    if resp.get("running"):
        return False, "job is already running"
    # The run route's note on a run that did not succeed: "failed: <why>" for an action that ran
    # and failed, or why nothing ran (an unknown or missing action provider). "trigger failed" in
    # its place hid, say, that no chat model is bound for the job's agent.
    note = str(resp.get("result") or "")
    if note.startswith("failed: "):
        return False, f"'{name}' {note}"
    if note:
        return False, f"'{name}' did not run: {note}"
    return False, "trigger failed"

"""On-demand triggering of a scheduled job, for ``personalclaw cron trigger``.

A thin helper that fires a job **immediately** by POSTing to the running gateway's run route —
never by instantiating a fresh ``ScheduleService`` (a fresh service has no live timer/reaper and
would orphan the run). The gateway is this home's (``home_gateway.reach``), and the call carries
the home's internal credential (``X-Internal-Secret``), the one the MCP tools use, to it alone.
"""

from __future__ import annotations

import sys
from urllib.parse import quote

#: The command, as the work it names.
_COMMAND = "cron-trigger"


def _at_a_terminal() -> bool:
    """Whether a person typed this command: its standard input is a terminal. A process with none,
    or one that cannot say, is a program (fail closed: a program's run is no run of yours)."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (OSError, ValueError):
        return False


def trigger_schedule_job(job_id: str) -> tuple[bool, str]:
    """Fire job ``job_id`` now via the running gateway. Returns ``(ok, message)``.

    ``job_id`` is a trigger store id, one ``personalclaw cron list`` shows, and the caller has
    looked it up there: the store is the one complete list of the ids the writers mint
    (``clock:<name>`` from ``cron add``, ``system:…``, ``app:<app>:<job>``,
    ``report-schedule:…``), and an app's job name can hold any character, so no pattern can
    stand in for it. The id is percent-encoded into the path, as the dashboard's Run button
    does, so no character in it can change which route the request reaches.

    POSTs to ``/api/triggers/schedule:{id}/run`` (non-blocking on the server — it spawns the run
    and returns immediately) on this home's running gateway. No gateway of this home running, or a
    port named that is not where it answers, is said in ``home_gateway``'s sentence, and nothing
    is sent anywhere.

    The call names the work it does, as every call made with the internal credential must (the
    gateway refuses one that names none), and that work decides whose run it is
    (`triggers.run_source.of_request`):

    * made in a session's work, that session's: the command run in an agent's shell names that
      agent's chat, and its run is the agent's, the automation firing as it fires on its own;
    * typed at a terminal (standard input is one), your own command (``session_keys.CLI``): a run
      of yours, by hand, which the hourly cap and the failure streak pass over as your Run now's;
    * run by anything else, a script or another program, a dispatch with no session
      (``guardrails.policy.unattended_dispatch_key``): the automation firing, held to every rule
      its fires keep. A program is not you pressing Run, so it does not pass over the cap.
    """
    job_id = (job_id or "").strip()
    if not job_id:
        return False, "no job id given"
    from personalclaw import home_gateway, session_keys
    from personalclaw.guardrails.policy import unattended_dispatch_key
    from personalclaw.mcp_core import _resolve_session_key

    work = _resolve_session_key()
    if not work:
        command = session_keys.CLI.key(_COMMAND)
        work = command if _at_a_terminal() else unattended_dispatch_key(command)
    try:
        gateway = home_gateway.reach()
        status, resp = gateway.post(
            f"/api/triggers/schedule:{quote(job_id, safe='')}/run",
            {},
            secret_header="X-Internal-Secret",
            work=work,
        )
    except home_gateway.GatewayError as exc:
        return False, str(exc)
    if status >= 400 or resp.get("error"):
        return False, home_gateway.error_text(resp, status)
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

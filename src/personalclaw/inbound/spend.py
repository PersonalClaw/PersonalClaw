"""The spend scope of work a caller from outside the dashboard asks for.

A client of the OpenAI-compatible endpoint, and a headless ``personalclaw run``, is work nobody
watches (``session_keys.INBOUND``). What it spends is held to the caps for such work: the day's
dollar and token ceilings every unattended call is admitted against, and a run ceiling of its own,
the headless profile's (``guardrails.budgets.safety_budget_for_inbound``), so an inbound caller can
never outspend the unattended work it sits beside. Its run is the caller's: segment one of its key,
the client's id (``inbound:<client>:<conversation>``), and ``cli`` for every headless run
(``cli_run.CLI_RUN_KEY``), so one client's spend is counted apart from another's.

:func:`spend_scope` is the one place that scope is bound, for the work it encloses: a chat turn
(``dashboard.chat_handlers``), and the endpoint's speech and transcription, which are not turns but
are the same client's work.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)


@contextmanager
def spend_scope(session_key: str) -> Iterator[str]:
    """Run the enclosed work in the spend scope of the inbound caller *session_key* names: its run
    (yielded), and the run ceiling an inbound caller is held to. The run's counter is let go when
    the work ends, so a long-lived gateway keeps no total per request."""
    from personalclaw.cli_run import CLI_RUN_KEY, CLI_SESSION_PREFIX
    from personalclaw.guardrails.budgets import (
        get_meter,
        reset_current_run_budget,
        reset_current_run_key,
        safety_budget_for_inbound,
        set_current_run_budget,
        set_current_run_key,
    )

    if session_key.startswith(CLI_SESSION_PREFIX):
        run_key = CLI_RUN_KEY
    else:
        parts = session_key.split(":")
        run_key = parts[1] if len(parts) > 1 and parts[1] else "inbound"
    key_token = set_current_run_key(run_key)
    # The ceiling beside the key: a run bound without one is a number nothing enforces.
    budget_token = set_current_run_budget(safety_budget_for_inbound())
    try:
        yield run_key
    finally:
        reset_current_run_budget(budget_token)
        reset_current_run_key(key_token)
        try:
            get_meter().end_run(run_key)
        except Exception:  # noqa: BLE001 — bookkeeping must not mask the work's outcome
            logger.debug("end_run failed for %s", run_key, exc_info=True)


__all__ = ["spend_scope"]

"""A published SDK function refusing an argument of the wrong kind — logged before it is raised.

An app often runs a whole turn inside one catch-all. The Slack app's handler does
(``except Exception: logger.exception("Unexpected error handling message")``), so when #3599 changed
``compress_thread_history`` to take a list of turns, the ``TypeError`` its old call raised became
one generic line among many, the reply said "Something went wrong", and every restored Slack
thread went on without its history. Nothing said which app, which SDK function, or what changed.

So the refusal is logged HERE, at the SDK boundary, before it is raised: the calling app's name,
the exception class, the function and parameter, what it was given and what it takes. The app can
still catch the exception; it can no longer make the break invisible.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def calling_app() -> str:
    """The app whose code is nearest on the call stack, or ``""`` when no app is on it.

    An installed app runs from ``<home>/apps/<name>/``; a bundled one from core's
    ``apps/native/<name>/``. A frame under either names its app.
    """
    from personalclaw.apps.manager import apps_dir
    from personalclaw.apps.native_contract import NATIVE_DIR

    roots = [apps_dir().resolve(), NATIVE_DIR.resolve()]
    frame = sys._getframe(1)
    while frame is not None:
        try:
            path = Path(frame.f_code.co_filename).resolve()
        except (OSError, ValueError):
            path = None
        for root in roots:
            if path is not None and path.is_relative_to(root):
                parts = path.relative_to(root).parts
                if len(parts) > 1:
                    return parts[0]
        frame = frame.f_back  # type: ignore[assignment]
    return ""


def refuse_argument(function: str, parameter: str, takes: str, value: object) -> TypeError:
    """Log, then return the ``TypeError`` for an SDK call passed the wrong kind of value.

    The caller raises what this returns (``raise refuse_argument(...)``), so the traceback
    still points at the SDK function that refused.
    """
    app = calling_app()
    error = TypeError(
        f"{function}({parameter}=…) takes {takes}; it was passed {type(value).__qualname__}"
    )
    logger.error(
        "SDK call refused (%s): %s — called by %s",
        type(error).__name__,
        error,
        f"app {app}" if app else "core",
    )
    return error

"""An SDK call that no longer fits fails loudly: at bind time, or with a log line naming the app.

#3599 changed ``compress_thread_history``'s first parameter from a ``ConversationLog`` to a list
of turns and kept its place, so the Slack app's old call still bound and then raised
``TypeError: 'ConversationLog' object is not iterable`` inside the function, where the Slack
handler's catch-all turned it into "Something went wrong" and one generic log line. The same PR
inserted ``prior_transcript`` mid-way through ``ContextBuilder.build_message``, shifting fourteen
parameters under any positional caller.

Two fixes, one per shape (both found by replaying the signature snapshot over two months of
``main``, see ``scripts/sdk_signature_snapshot.py``):

* the function refuses the old kind of value at its door, and LOGS the refusal — the exception
  class and the calling app's name — before raising, so an app's swallow cannot hide it;
* ``build_message``'s parameters after the third are keyword-only, so the next insertion cannot
  shift a positional caller (none passes more than three).
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw.apps import manager
from personalclaw.context import compress_thread_history
from personalclaw.history import ConversationLog


def _call_from_an_app(tmp_path: Path, monkeypatch, body: str):
    """Run ``body`` from a module that lives in an installed app's directory, as a channel
    app's handler does."""
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    app = tmp_path / "apps" / "demo-channel" / "demo_runtime"
    app.mkdir(parents=True)
    (app / "handler.py").write_text(body, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_demo_handler", app / "handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_pre_3599_call_is_refused_and_logged_with_the_calling_app(
    tmp_path, monkeypatch, caplog
):
    module = _call_from_an_app(
        tmp_path,
        monkeypatch,
        "from personalclaw.sdk.channel import compress_thread_history\n"
        "async def restore(log, key, query, sessions):\n"
        "    try:\n"
        "        return await compress_thread_history(log, key, query, sessions)\n"
        "    except Exception:\n"
        "        return None  # the swallow that hid it\n",
    )
    log = ConversationLog(base_dir=tmp_path / "history")
    with caplog.at_level(logging.ERROR):
        result = asyncio.run(module.restore(log, "slack:T1", "hi", MagicMock()))
    assert result is None  # the app swallowed it, exactly as Slack's handler did …
    refusals = [r for r in caplog.records if "SDK call refused" in r.getMessage()]
    assert refusals, "… and nothing said so"
    message = refusals[0].getMessage()
    assert "app demo-channel" in message and "TypeError" in message
    assert "compress_thread_history(prior_turns=…)" in message and "ConversationLog" in message


def test_the_refusal_names_what_the_parameter_takes(tmp_path):
    with pytest.raises(TypeError, match=r"list of \{role, content\} dicts \(since #3599\)"):
        asyncio.run(
            compress_thread_history(ConversationLog(base_dir=tmp_path), "k", "q", MagicMock())
        )


def test_a_list_of_turns_is_still_accepted(tmp_path):
    """The control: the current call shape reaches the function body (nothing to compress)."""
    assert asyncio.run(compress_thread_history([], "k", "q", MagicMock())) is None


def test_build_message_takes_three_positional_arguments_and_no_more():
    """A fourth positional argument used to bind silently to whatever sat fourth."""
    from personalclaw.context import ContextBuilder

    builder = ContextBuilder.__new__(ContextBuilder)
    with pytest.raises(TypeError, match="positional"):
        builder.build_message("hi", True, "session", "C123")

"""Morning triage reads runs as it reads the Inbox: as no chat's work.

What the digest collects goes to its model and into its run's record, a run of yours that every
agent's workflow tools read. A chat's own run (a batch of its subagents, or a run a Temporary or
Incognito chat started: ``workflows.chat_runs``) is that chat's, so nothing of it, its name or its
error, reaches the digest, nor a proposal an earlier digest carried about it. A run whose chat
cannot be told is read by you alone, so the digest leaves it out too. The Inbox twin is
``test_a_chats_requests_in_the_inbox_are_its_own.py::test_morning_triage_collects_only_what_is_about_no_chat``.
"""

from __future__ import annotations

import pytest

from personalclaw.proactive import collect
from personalclaw.proactive.manifest import SOURCE_RUN, build_manifest, render_manifest_lines
from personalclaw.workflows import ownership, store
from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun

pytestmark = pytest.mark.security

#: What each chat's run says, planted so the test can look for the words themselves.
TEMPORARY = ("harbour-plan", "a Temporary chat's step read the harbour lease")
INCOGNITO = ("lantern-notes", "an Incognito chat's step read the lantern festival list")
BATCH = ("subagent-batch-7", "a batch task quoted the quarterly numbers")
UNREADABLE = ("orchard-sync", "a run whose chat nothing could say read the orchard map")
YOURS = ("nightly-backup", "the backup disk is full")


@pytest.fixture
def runs(tmp_path, monkeypatch):
    """Five failed runs in a run store of the test's own: four a chat's, one yours."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)

    def failed(said: tuple[str, str], session: str, kind: OriginKind, mode: str = "") -> str:
        extra = {}
        if mode in {m.value for m in ownership.MemoryMode}:
            extra = ownership.stamp_run_mode({}, ownership.MemoryMode(mode))
        elif mode:
            extra = {ownership.RUN_MODE_KEY: mode}  # a mode this build does not know
        run = store.create(
            WorkflowRun(
                id="",
                workflow_name=said[0],
                status=RunStatus.FAILED,
                origin=RunOrigin(kind=kind, session_key=session),
                error_message=said[1],
                extra=extra,
            )
        )
        return run.id

    return {
        "temporary": failed(TEMPORARY, "dashboard:chat-t", OriginKind.CHAT, "temporary"),
        "incognito": failed(INCOGNITO, "dashboard:chat-i", OriginKind.CHAT, "incognito"),
        "batch": failed(BATCH, "dashboard:chat-o", OriginKind.SUBAGENT_TOOL),
        "unreadable": failed(UNREADABLE, "dashboard:chat-u", OriginKind.CHAT, "from-a-later-build"),
        "yours": failed(YOURS, "", OriginKind.API),
    }


def test_the_digest_collects_only_your_runs(runs):
    """🔴 Red before: the run lane read every run the store listed, a chat's own among them."""
    collected = [c for c in collect.collect_all(include_runs=True) if c.source == SOURCE_RUN]
    assert [c.source_id for c in collected] == [runs["yours"]], collected


def test_a_carried_proposal_about_a_chats_run_reads_as_gone(runs):
    """🔴 Red before: the re-check of a carried proposal read the chat's run as still wanting you."""
    for theirs in ("temporary", "incognito", "batch", "unreadable"):
        assert collect.current_item(SOURCE_RUN, runs[theirs]) is None, theirs
    assert collect.current_item(SOURCE_RUN, runs["yours"]) is not None


def test_the_digests_prompt_holds_nothing_of_a_chats_run(runs):
    """The lines the pipeline puts in its model's prompt (``render_manifest_lines`` over
    ``build_manifest``) name your run and hold no word of a chat's.
    🔴 Red before: each chat run's name and error went into the prompt."""
    lines = render_manifest_lines(build_manifest(collect.collect_runs()))
    assert YOURS[0] in lines and YOURS[1] in lines, lines
    for name, error in (TEMPORARY, INCOGNITO, BATCH, UNREADABLE):
        assert name not in lines and error not in lines, lines

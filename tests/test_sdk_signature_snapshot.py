"""Every published SDK signature is checked in, so an SDK change is a diff somebody reviewed.

#3599 changed ``compress_thread_history``'s first parameter from a ``ConversationLog`` to a list of
turns and kept its place. The PR touched ``context.py`` only; nothing in its diff said a published
signature had moved, every app import still resolved, every call still bound, and the Slack app
lost every restored thread's history inside a swallowed ``TypeError``.

``src/personalclaw/sdk/signatures.json`` records every name each ``personalclaw.sdk`` module
publishes (``scripts/sdk_signature_snapshot.py``). The first test fails until it matches the code,
so the next such change shows up as a reviewed diff in ``src/personalclaw/sdk/`` — which is also
what triggers ``ci.yml``'s ``apps-contract`` job. The rest prove the walk reaches the surface, that
it catches the change it exists for (replayed against the live function), and that writing the
snapshot refuses a parameter whose type changed in place.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import sdk_signature_snapshot as snap

REPO = Path(__file__).resolve().parents[1]


def _rendered_by_a_fresh_interpreter(home: Path, seed: str) -> str:
    """The snapshot of the SDK as a fresh interpreter imports it, rendered.

    Out of process on purpose. In this one, an SDK module first imported while an earlier test
    had patched a name it re-exports keeps that mock bound for the rest of the worker (measured:
    ``sdk.channel.config_dir`` read as a ``MagicMock`` under ``-n 4``), so an in-process walk
    would answer for the tests that happened to run first, not for the code.
    """
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from scripts import sdk_signature_snapshot as s;"
            " print(s.render(s.snapshot()), end='')",
        ],
        cwd=REPO,
        env={
            "PYTHONHASHSEED": seed,
            "PERSONALCLAW_HOME": str(home),
            "HOME": str(home),
            "PATH": os.environ.get("PATH", ""),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def test_the_checked_in_snapshot_matches_the_live_sdk(tmp_path):
    live = json.loads(_rendered_by_a_fresh_interpreter(tmp_path, "0"))
    changes = snap.diff(snap.load(), live)
    assert not changes, f"The SDK changed; {snap.REGENERATE}:\n\n{snap.report(changes)}"


def test_the_file_is_the_generator_output_byte_for_byte():
    """A hand edit that parses to the same records still has to be the canonical rendering, or
    the next regeneration shows a diff nobody made."""
    assert snap.SNAPSHOT_PATH.read_text(encoding="utf-8") == snap.render(snap.load())


def test_the_snapshot_reaches_the_whole_surface():
    """Vacuity floor: a walk that returned a handful of records would pass the test above."""
    records = snap.load()
    assert len(records) >= 400, len(records)
    kinds = {rec["kind"] for rec in records.values()}
    assert {"function", "async function", "class", "dataclass", "enum", "value"} <= kinds
    compress = records["personalclaw.sdk.channel.compress_thread_history"]
    assert [p["name"] for p in compress["params"]] == [
        "prior_turns",
        "session_key",
        "query",
        "sessions",
    ]
    assert compress["params"][0]["annotation"] == "list[dict]"
    # A class's public methods are part of its contract, with `self` left out.
    log = records["personalclaw.sdk.channel.ConversationLog"]
    assert log["members"]["history_for_model"]["params"][0]["name"] == "key"
    # A constant's value is part of the contract: it is a credential key.
    assert records["personalclaw.sdk.channel.CRED_OWNER_ID"]["value"] == "'PERSONALCLAW_OWNER_ID'"


def test_the_snapshot_is_deterministic_across_processes(tmp_path):
    """Set members are sorted and addresses dropped, so hash randomization cannot move a byte."""
    assert _rendered_by_a_fresh_interpreter(tmp_path, "12345") == (
        _rendered_by_a_fresh_interpreter(tmp_path, "54321")
    )


# ── it catches the change it exists for ────────────────────────────────────────────────────────


def _pre_3599_signature(fn):
    """``compress_thread_history`` as it was before #3599: a log, in the same place."""
    from personalclaw.history import ConversationLog

    sig = inspect.signature(fn)
    first = sig.parameters["prior_turns"].replace(
        name="conversation_log", annotation=ConversationLog
    )
    return sig.replace(parameters=[first, *list(sig.parameters.values())[1:]])


def test_a_live_signature_change_is_caught_and_named_a_silent_break(monkeypatch):
    """Replays #3599 against the REAL walk: the live function's signature is put back to the
    pre-#3599 shape, and the snapshot of the code as it stands must disagree — naming the
    parameter, both types, and the loud-break rule."""
    from personalclaw import context

    live_now = snap.snapshot()
    monkeypatch.setattr(
        context.compress_thread_history,
        "__signature__",
        _pre_3599_signature(context.compress_thread_history),
        raising=False,
    )
    before = snap.snapshot()
    changes = snap.diff(before, live_now)
    assert [c.symbol for c in changes] == ["personalclaw.sdk.channel.compress_thread_history"]
    (change,) = changes
    assert change.kind == "changed" and not change.additive
    assert change.silent_breaks == [
        "compress_thread_history: position 0 replaced: conversation_log: ConversationLog → "
        "prior_turns: list[dict]"
    ]
    assert "rename the parameter and make it keyword-only" in change.describe()


def test_the_three_silent_breaks_the_last_weeks_shipped_are_each_named():
    """The audit's findings, as records: a retyped dataclass field (#3507), a parameter inserted
    mid-list (#3599's `build_message`) and a parameter replaced in place (#3599)."""

    def p(name, annotation, **extra):
        return {"name": name, "kind": "positional_or_keyword", "annotation": annotation, **extra}

    retyped, _ = snap.silent_breaks(
        {"params": [p("api", "list[str]", default="[]"), p("memory", "str", default="''")]},
        {"params": [p("api", "list[str]", default="[]"), p("memory", "bool", default="False")]},
        "Permissions()",
        dataclass_init=True,
    )
    assert retyped == ["Permissions(): memory (position 1) retyped: str → bool"]

    shifted, _ = snap.silent_breaks(
        {"params": [p("text", "str"), p("mode", "str", default="''")]},
        {
            "params": [
                p("text", "str"),
                p("prior", "list[dict] | None", default="None"),
                p("mode", "str", default="''"),
            ]
        },
        "build_message",
    )
    assert shifted == ["build_message: position 1 was mode, is now prior"]

    # The same insertion on a DATACLASS constructor (built by keyword) is listed, not refused.
    breaks, moved = snap.silent_breaks(
        {"params": [p("a", "int", default="0"), p("z", "str", default="''")]},
        {
            "params": [
                p("a", "int", default="0"),
                p("m", "bool", default="False"),
                p("z", "str", default="''"),
            ]
        },
        "AppConfig()",
        dataclass_init=True,
    )
    assert breaks == [] and moved == ["AppConfig(): position 1 was z, is now m"]


def test_making_a_parameter_keyword_only_is_the_fix_and_not_a_break():
    """What the rule asks for must pass it: after `*`, a changed type reaches no positional
    caller, and a keyword caller of the old name fails loudly at bind time."""

    def p(name, annotation, kind="positional_or_keyword"):
        return {"name": name, "kind": kind, "annotation": annotation}

    old = {"params": [p("conversation_log", "ConversationLog"), p("key", "str")]}
    new = {
        "params": [p("prior_turns", "list[dict]", "keyword_only"), p("key", "str", "keyword_only")]
    }
    assert snap.silent_breaks(old, new, "f") == ([], [])


# ── writing the snapshot ───────────────────────────────────────────────────────────────────────


def _stored_with(path: Path, symbol: str, mutate) -> None:
    records = snap.snapshot()
    mutate(records[symbol])
    path.write_text(snap.render(records), encoding="utf-8")


def test_the_generator_refuses_to_record_a_silent_break(tmp_path, capsys):
    """Writing the snapshot is the one moment the change is in front of its author: a parameter
    that kept its place and changed its type is refused there, with the fix."""
    stored = tmp_path / "signatures.json"
    symbol = "personalclaw.sdk.channel.compress_thread_history"
    _stored_with(
        stored,
        symbol,
        lambda rec: rec["params"][0].update(name="conversation_log", annotation="ConversationLog"),
    )
    before = stored.read_text(encoding="utf-8")
    assert snap.main(["--path", str(stored)]) == 1
    out = capsys.readouterr().out
    assert "Refusing to record" in out and "position 0 replaced" in out
    assert stored.read_text(encoding="utf-8") == before, "a refused write must not write"
    assert snap.main(["--path", str(stored), "--allow-silent-break"]) == 0
    assert json.loads(stored.read_text(encoding="utf-8")) == snap.snapshot()


def test_the_generator_writes_an_additive_change_and_check_mode_names_it(tmp_path, capsys):
    stored = tmp_path / "signatures.json"
    symbol = "personalclaw.sdk.channel.parse_title"
    records = snap.snapshot()
    del records[symbol]
    stored.write_text(snap.render(records), encoding="utf-8")
    assert snap.main(["--path", str(stored), "--check"]) == 1
    assert f"ADDED: {symbol}" in capsys.readouterr().out
    assert snap.main(["--path", str(stored)]) == 0
    assert snap.main(["--path", str(stored), "--check"]) == 0


@pytest.mark.parametrize(
    ("annotation", "rendered"),
    [
        ("'ConversationLog'", "ConversationLog"),
        ("list[dict]", "list[dict]"),
    ],
)
def test_a_quoted_annotation_renders_as_its_source(annotation, rendered):
    assert snap.render_annotation(annotation) == rendered


def test_optional_and_union_render_alike():
    from typing import Optional

    assert (
        snap.render_annotation(Optional[int])
        == snap.render_annotation(int | None)
        == ("int | None")
    )

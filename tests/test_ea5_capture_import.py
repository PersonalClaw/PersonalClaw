"""EA-5 (EXTERNAL-ACCESS §8) — telemetry import for unproxyable agents.

**Fixture provenance, stated plainly:** the log shapes below are asserted from
the *adapters' declared contract* — the fields each adapter documents that it
reads — and NOT from a real captured file. No Claude Code session transcript,
OpenAI request log, or SSE dump exists anywhere in this repo to fixture from
(``git grep -ln "session.jsonl"`` finds only unrelated dashboard/history tests),
so a fixture claiming to be a real capture would be a guess wearing a test's
clothes. What these tests therefore prove is the normalisation contract — a §7.2
record with every key present, malformed input skipped and counted, idempotence
by content hash — and *not* that a given vendor's exporter emits exactly these
keys. If a real export ever disagrees, the adapter is what changes; these
assertions about the record shape stay.

Every test runs against ``tmp_path`` as ``PERSONALCLAW_HOME`` so the import
ledger is written under the temp home and never the operator's real
``~/.personalclaw``.

``stage_records`` (the sibling ``capture_store``, which owns redact → fence →
persist) is INJECTED as a double throughout. That is deliberate: this half owns
parsing and idempotence, and a suite that reached for the real store would be
testing hygiene this module is specifically not allowed to own.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from personalclaw.inbound.capture_import import (
    FORMATS,
    adapt_json,
    adapt_jsonl,
    adapt_sse,
    capture_cmd,
    file_content_hash,
    import_capture_file,
    load_ledger,
)

# Every field §7.2 declares. Asserted as a set on every record so a future
# adapter that forgets one cannot pass by omitting the key.
RECORD_FIELDS = {
    "ts",
    "dialect",
    "model_requested",
    "prompt_digest",
    "response_digest",
    "tool_calls",
    "read_paths",
    "wrote_paths",
    "tokens",
    "latency_ms",
}


class FakeStore:
    """Stand-in for ``capture_store.stage_records``.

    Accepts everything by default — the point of these tests is what the
    *adapters* produced, so the store must not be able to mask a parse bug by
    dropping records. ``skipped``/``reasons`` are settable so the merge of the
    store's losses into the import report is exercised too.
    """

    def __init__(self, *, skipped: int = 0, reasons: list[str] | None = None) -> None:
        self.calls: list[tuple[list[dict[str, Any]], str]] = []
        self.skipped = skipped
        self.reasons = reasons or []

    def __call__(self, records: list[dict[str, Any]], *, source: str) -> dict[str, Any]:
        self.calls.append((records, source))
        return {
            "imported": len(records) - self.skipped,
            "skipped": self.skipped,
            "reasons": list(self.reasons),
        }

    @property
    def records(self) -> list[dict[str, Any]]:
        assert self.calls, "the store was never called — nothing was staged"
        return self.calls[-1][0]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


# ── Fixture bodies ──


def _claude_code_jsonl() -> str:
    """A Claude Code session JSONL built from what ``adapt_jsonl`` reads.

    Line 1 a user prompt; line 2 the assistant turn that answers it, with a
    ``Read`` tool call; line 3 the tool result that tells us the read succeeded;
    line 4 a second assistant turn whose ``Write`` call has no result yet.
    """
    return "\n".join(
        json.dumps(entry)
        for entry in (
            {
                "type": "user",
                "timestamp": "2026-08-01T10:00:00Z",
                "message": {"role": "user", "content": "Summarise the README then note it"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-08-01T10:00:02Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-6",
                    "content": [
                        {"type": "text", "text": "Reading it now."},
                        {
                            "type": "tool_use",
                            "id": "toolu_01",
                            "name": "Read",
                            "input": {"file_path": "/repo/README.md"},
                        },
                    ],
                    "usage": {"input_tokens": 120, "output_tokens": 45},
                },
            },
            {
                "type": "user",
                "timestamp": "2026-08-01T10:00:03Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_01",
                            "content": "# repo",
                            "is_error": False,
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-08-01T10:00:05Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-6",
                    "content": [
                        {"type": "text", "text": "Noted."},
                        {
                            "type": "tool_use",
                            "id": "toolu_02",
                            "name": "Write",
                            "input": {"file_path": "/repo/NOTES.md", "content": "hi"},
                        },
                    ],
                    "usage": {"input_tokens": 200, "output_tokens": 12},
                },
            },
        )
    )


def _openai_request_log() -> str:
    """An OpenAI-format request log built from what ``adapt_json`` reads.

    Entry 0 is a full exchange (request + response + usage + latency). Entry 1 is
    a bare request body with no ``request`` wrapper and no response at all — the
    shape a minimal hand-rolled logger writes.
    """
    return json.dumps(
        [
            {
                "ts": "2026-08-01T11:00:00Z",
                "latency_ms": 1420,
                "request": {
                    "model": "gpt-5-mini",
                    "messages": [
                        {"role": "system", "content": "Be brief."},
                        {"role": "user", "content": "Patch the config"},
                    ],
                },
                "response": {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Patching it.",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "edit_file",
                                            "arguments": '{"file_path": "/repo/config.toml"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 88, "completion_tokens": 30},
                },
            },
            {"model": "gpt-5-mini", "messages": [{"role": "user", "content": "ping"}]},
        ],
        indent=2,
    )


def _sse_dump() -> str:
    """Two SSE response streams built from what ``adapt_sse`` reads.

    Stream 1 is OpenAI-shaped (``choices[].delta`` chunks, ``data: [DONE]``
    terminator). Stream 2 is Anthropic-shaped (``message_start`` →
    ``content_block_delta`` → ``message_delta`` → ``message_stop``).
    """
    openai_chunks = [
        {
            "id": "chatcmpl-1",
            "model": "gpt-5-mini",
            "created": 1780000000,
            "choices": [{"delta": {"content": "Hel"}}],
        },
        {"id": "chatcmpl-1", "choices": [{"delta": {"content": "lo"}}]},
        {
            "id": "chatcmpl-1",
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "/repo/a.py"}',
                                },
                            }
                        ]
                    }
                }
            ],
        },
        {
            "id": "chatcmpl-1",
            "choices": [{"delta": {}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        },
    ]
    lines = [f"data: {json.dumps(chunk)}" for chunk in openai_chunks]
    lines += ["data: [DONE]", ""]
    for event, payload in (
        (
            "message_start",
            {
                "type": "message_start",
                "message": {"model": "claude-opus-4-6", "usage": {"input_tokens": 40}},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Sure."},
            },
        ),
        ("message_delta", {"type": "message_delta", "usage": {"output_tokens": 9}}),
        ("message_stop", {"type": "message_stop"}),
    ):
        lines += [f"event: {event}", f"data: {json.dumps(payload)}", ""]
    return "\n".join(lines)


# ── 1. One test per format: every §7.2 field filled, or absent for a named reason ──


def test_jsonl_adapter_fills_the_722_record(tmp_path: Path) -> None:
    """Claude Code session JSONL → one record per assistant turn.

    Unfillable here, with reasons:
    * ``latency_ms`` — a session transcript records what was said, not the round
      trip duration. No line in the format carries it.
    * record 1's ``prompt_digest`` — the user line preceding it holds only a
      ``tool_result`` block, i.e. no human prose, so there is no prompt to digest.
    """
    log = tmp_path / "session.jsonl"
    log.write_text(_claude_code_jsonl(), encoding="utf-8")
    store = FakeStore()

    report = import_capture_file(log, fmt="jsonl", source="claude-code", stage=store)

    assert report["imported"] == 2, report
    assert report["skipped"] == 0
    assert report["reasons"] == []
    assert store.calls[-1][1] == "claude-code"

    first, second = store.records
    for record in (first, second):
        assert set(record) == RECORD_FIELDS

    assert first["ts"] == "2026-08-01T10:00:02Z"
    assert first["dialect"] == "anthropic"
    assert first["model_requested"] == "claude-opus-4-6"
    assert first["prompt_digest"] == "Summarise the README then note it"
    assert first["response_digest"] == "Reading it now."
    assert first["tokens"] == {"input": 120, "output": 45}
    assert first["read_paths"] == ["/repo/README.md"]
    assert first["wrote_paths"] == []
    # ok is True because a later line's tool_result said is_error: False.
    assert first["tool_calls"] == [
        {"name": "Read", "args_clipped": '{"file_path": "/repo/README.md"}', "ok": True}
    ]
    assert first["latency_ms"] is None

    assert second["wrote_paths"] == ["/repo/NOTES.md"]
    assert second["read_paths"] == []
    assert second["tokens"] == {"input": 200, "output": 12}
    # No tool_result followed, so ok stays None. None and False are different
    # claims: defaulting to False would invent a failure out of a truncated log.
    assert second["tool_calls"][0]["ok"] is None
    assert second["prompt_digest"] is None
    assert second["latency_ms"] is None


def test_json_adapter_fills_the_722_record(tmp_path: Path) -> None:
    """OpenAI-format request log → one record per logged exchange.

    This is the only format that can fill ``latency_ms`` (the caller wrote the
    log and is the only party that knows the round trip). Unfillable, with
    reasons:
    * ``tool_calls[].ok`` — a *request* log records the call the model asked for,
      never whether executing it worked. Nothing in the format can say.
    * entry 1's ``response_digest``/``tokens``/``latency_ms`` — that entry is a
      bare request body with no response recorded at all.
    """
    log = tmp_path / "requests.json"
    log.write_text(_openai_request_log(), encoding="utf-8")
    store = FakeStore()

    report = import_capture_file(log, fmt="json", source="cursor", stage=store)

    assert report["imported"] == 2, report
    assert report["skipped"] == 0
    assert report["reasons"] == []

    full, bare = store.records
    for record in (full, bare):
        assert set(record) == RECORD_FIELDS

    assert full["ts"] == "2026-08-01T11:00:00Z"
    assert full["dialect"] == "openai"
    assert full["model_requested"] == "gpt-5-mini"
    assert full["prompt_digest"] == "system: Be brief. user: Patch the config"
    assert full["response_digest"] == "Patching it."
    assert full["tokens"] == {"input": 88, "output": 30}
    assert full["latency_ms"] == 1420
    # arguments arrive as a JSON *string*; it is decoded only for path
    # attribution, and args_clipped keeps the wire form.
    assert full["tool_calls"] == [
        {
            "name": "edit_file",
            "args_clipped": '{"file_path": "/repo/config.toml"}',
            "ok": None,
        }
    ]
    assert full["wrote_paths"] == ["/repo/config.toml"]
    assert full["read_paths"] == []

    assert bare["prompt_digest"] == "user: ping"
    assert bare["model_requested"] == "gpt-5-mini"
    assert bare["response_digest"] is None
    assert bare["tokens"] is None
    assert bare["latency_ms"] is None
    assert bare["tool_calls"] == []


def test_sse_adapter_fills_the_722_record(tmp_path: Path) -> None:
    """Raw SSE dump → one record per response stream, both dialects.

    Two fields are unfillable by construction, not by omission:
    * ``prompt_digest`` — an SSE dump is the *response* half of the wire. The
      request is simply not in the file.
    * ``latency_ms`` — the wire dump carries no clock.
    Stream 2's ``ts`` is also None: Anthropic's event sequence has no ``created``
    field, whereas OpenAI's chunks do, so stream 1 fills it and stream 2 cannot.
    """
    log = tmp_path / "stream.sse"
    log.write_text(_sse_dump(), encoding="utf-8")
    store = FakeStore()

    report = import_capture_file(log, fmt="sse", source="aider", stage=store)

    assert report["imported"] == 2, report
    assert report["skipped"] == 0
    assert report["reasons"] == []

    openai_stream, anthropic_stream = store.records
    for record in (openai_stream, anthropic_stream):
        assert set(record) == RECORD_FIELDS
        assert record["prompt_digest"] is None
        assert record["latency_ms"] is None

    assert openai_stream["dialect"] == "openai"
    assert openai_stream["model_requested"] == "gpt-5-mini"
    assert openai_stream["ts"] == 1780000000
    # Deltas concatenated, not listed: "Hel" + "lo".
    assert openai_stream["response_digest"] == "Hello"
    assert openai_stream["tokens"] == {"input": 12, "output": 7}
    assert openai_stream["tool_calls"] == [
        {"name": "read_file", "args_clipped": '{"path": "/repo/a.py"}', "ok": None}
    ]
    assert openai_stream["read_paths"] == ["/repo/a.py"]
    assert openai_stream["wrote_paths"] == []

    assert anthropic_stream["dialect"] == "anthropic"
    assert anthropic_stream["model_requested"] == "claude-opus-4-6"
    assert anthropic_stream["response_digest"] == "Sure."
    # input_tokens from message_start, output_tokens from message_delta.
    assert anthropic_stream["tokens"] == {"input": 40, "output": 9}
    assert anthropic_stream["ts"] is None
    assert anthropic_stream["tool_calls"] == []


def test_every_declared_format_has_an_adapter() -> None:
    """A format named in ``FORMATS`` with no adapter would report, not crash —
    which is exactly how a missing adapter hides. Assert the set instead."""
    assert set(FORMATS) == {"jsonl", "json", "sse"}
    for fmt in FORMATS:
        report = import_capture_file("/nonexistent", fmt=fmt, stage=FakeStore())
        assert "unknown format" not in " ".join(report["reasons"])


# ── 2. Idempotence by content hash, with a vacuity floor ──


def test_reimport_is_a_noop_and_a_different_file_still_imports(tmp_path: Path) -> None:
    """N then 0 on the same file — and the vacuity floor that makes "0" mean
    something.

    A different file with the SAME record count must still import. Without that
    leg, a second import returning 0 is indistinguishable from an importer that
    simply stopped working after its first call.
    """
    first_file = tmp_path / "a.jsonl"
    first_file.write_text(_claude_code_jsonl(), encoding="utf-8")
    store = FakeStore()

    initial = import_capture_file(first_file, fmt="jsonl", source="claude-code", stage=store)
    assert initial["imported"] == 2
    assert initial["duplicate"] is False

    repeat = import_capture_file(first_file, fmt="jsonl", source="claude-code", stage=store)
    assert repeat["imported"] == 0
    assert repeat["duplicate"] is True
    assert repeat["skipped"] == 0
    assert any("already imported" in r for r in repeat["reasons"]), repeat["reasons"]
    # The store was not called a second time: a no-op re-import must not even
    # reach the redact/fence/persist pipeline.
    assert len(store.calls) == 1

    # Vacuity floor — a DIFFERENT file with the same record count imports.
    other = tmp_path / "b.jsonl"
    other.write_text(
        _claude_code_jsonl().replace("/repo/README.md", "/repo/CHANGELOG.md"), encoding="utf-8"
    )
    fresh = import_capture_file(other, fmt="jsonl", source="claude-code", stage=store)
    assert fresh["imported"] == 2, "a distinct file must import, or '0' above proves nothing"
    assert fresh["duplicate"] is False
    assert fresh["content_hash"] != initial["content_hash"]
    assert len(store.calls) == 2

    # Both hashes are in the ledger, under the temp home.
    ledger = load_ledger(tmp_path)
    assert set(ledger) == {initial["content_hash"], fresh["content_hash"]}
    assert (tmp_path / "capture" / "import_ledger.json").exists()


def test_content_hash_is_exact_not_normalised(tmp_path: Path) -> None:
    """The idempotence key must distinguish files a normalising fingerprint
    would collide.

    ``learning.staging.input_hash`` composes ``learning.hygiene.fingerprint``,
    which lower-cases and collapses whitespace. Two exports differing only in
    case are genuinely different exports; if the key collided, the second would
    be silently discarded as 'already imported'.
    """
    lower = tmp_path / "lower.jsonl"
    upper = tmp_path / "upper.jsonl"
    lower.write_text('{"type": "assistant", "message": {"content": "ok"}}\n', encoding="utf-8")
    upper.write_text('{"type": "assistant", "message": {"content": "OK"}}\n', encoding="utf-8")
    assert file_content_hash(lower) != file_content_hash(upper)

    store = FakeStore()
    assert import_capture_file(lower, fmt="jsonl", stage=store)["imported"] == 1
    assert import_capture_file(upper, fmt="jsonl", stage=store)["imported"] == 1


def test_ledger_records_nothing_when_nothing_staged(tmp_path: Path) -> None:
    """A file that staged zero records must stay re-importable.

    Recording the hash regardless would make one bad export permanently
    unimportable — you could never retry after fixing the store.
    """
    broken = tmp_path / "all-bad.jsonl"
    broken.write_text("not json\nalso not json\n", encoding="utf-8")
    store = FakeStore()

    report = import_capture_file(broken, fmt="jsonl", stage=store)
    assert report["imported"] == 0
    assert load_ledger(tmp_path) == {}
    assert not store.calls

    again = import_capture_file(broken, fmt="jsonl", stage=store)
    assert again["duplicate"] is False


# ── 3. Malformed lines are skipped and counted, never fatal ──


def test_malformed_lines_are_skipped_counted_and_explained(tmp_path: Path) -> None:
    """2 good lines + 3 bad ones → imported 2, skipped 3, three reasons, no raise.

    The reasons must name what to do about it. A bag of "error" strings is a
    report that cannot be acted on, so each reason is asserted to name the
    specific defect, not just its existence.
    """
    good = json.loads(_claude_code_jsonl().splitlines()[1])
    prompt = json.loads(_claude_code_jsonl().splitlines()[0])
    log = tmp_path / "messy.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(prompt),  # a user turn: folded in, not a skip
                json.dumps(good),  # record 1
                "this line is not json at all",  # skip 1
                "[1, 2, 3]",  # skip 2
                json.dumps(good),  # record 2
                json.dumps({"type": "system", "subtype": "init"}),  # skip 3
            ]
        ),
        encoding="utf-8",
    )
    store = FakeStore()

    report = import_capture_file(log, fmt="jsonl", source="claude-code", stage=store)

    assert report["imported"] == 2, report
    assert report["skipped"] == 3, report
    assert len(report["reasons"]) == 3, report["reasons"]
    joined = " | ".join(report["reasons"])

    # Each reason names the defect AND the line, so a 40k-line export is
    # navigable.
    assert "line 3: invalid JSON" in joined, joined
    assert "line 4" in joined and "expected a JSON object, got list" in joined, joined
    assert "line 6" in joined and "'system'" in joined, joined
    assert "carries no request/response turn" in joined, joined
    # Not a bag of bare "error" strings.
    assert not any(r.strip().lower() in {"error", "skipped", "bad line"} for r in report["reasons"])
    # The good records still reached the store — partial import, not abort.
    assert len(store.records) == 2


def test_store_reported_losses_merge_into_the_report(tmp_path: Path) -> None:
    """The store's own skips add to the parse skips rather than replacing them.

    Two separate loss channels reported as one number is how half a partial
    import goes unnoticed.
    """
    log = tmp_path / "messy.jsonl"
    log.write_text(
        _claude_code_jsonl() + "\nnot json\n",
        encoding="utf-8",
    )
    store = FakeStore(skipped=1, reasons=["record 0: redaction removed all content"])

    report = import_capture_file(log, fmt="jsonl", stage=store)

    assert report["imported"] == 1  # 2 parsed, store rejected 1
    assert report["skipped"] == 2  # 1 bad line + 1 store rejection
    assert any("invalid JSON" in r for r in report["reasons"])
    assert any("redaction" in r for r in report["reasons"])


def test_adapters_never_raise_on_hostile_content() -> None:
    """Direct adapter probe: no content makes an adapter raise.

    Driven at the adapter level rather than through ``import_capture_file`` so a
    ``try/except`` in the pipeline cannot be what makes this pass.
    """
    hostile = [
        "",
        "\n\n\n",
        "{",
        "null",
        "[]",
        '{"type": null}',
        '{"type": "assistant"}',
        '{"type": "assistant", "message": {"content": 42}}',
        '{"type": "user", "message": {"content": [{"type": "tool_result"}]}}',
        "data: {",
        "data: 5",
        "event: whatever",
        "\x00\x01garbage",
        json.dumps({"request": {"messages": "not a list"}}),
        json.dumps({"records": [None, 7, "x"]}),
    ]
    for adapter in (adapt_jsonl, adapt_json, adapt_sse):
        for blob in hostile:
            result = adapter(blob)  # must not raise
            assert result.skipped == len(result.reasons) or result.skipped >= 0
            for record in result.records:
                assert set(record) == RECORD_FIELDS


# ── 4. Empty file, and a file that is not the declared format ──


def test_empty_file_reports_rather_than_raising(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    store = FakeStore()

    report = import_capture_file(empty, fmt="jsonl", stage=store)

    assert report == {
        "imported": 0,
        "skipped": 0,
        "reasons": [report["reasons"][0]],
        "duplicate": False,
        "content_hash": report["content_hash"],
        "format": "jsonl",
        "source": "import",
    }
    assert "no records found" in report["reasons"][0]
    assert "empty.jsonl" in report["reasons"][0]
    assert not store.calls


@pytest.mark.parametrize(
    ("fmt", "body", "expected"),
    [
        # A JSONL file declared as json: whole-file parse fails, and the reason
        # names the flag that would have worked.
        ("json", _claude_code_jsonl(), "--format jsonl"),
        # A JSON array declared as sse: no SSE field lines anywhere.
        ("sse", _openai_request_log(), "not an SSE field line"),
        # An SSE dump declared as jsonl: 'data: {...}' is not JSON.
        ("jsonl", _sse_dump(), "invalid JSON"),
    ],
)
def test_wrong_declared_format_reports_rather_than_raising(
    tmp_path: Path, fmt: str, body: str, expected: str
) -> None:
    log = tmp_path / f"mismatch.{fmt}"
    log.write_text(body, encoding="utf-8")
    store = FakeStore()

    report = import_capture_file(log, fmt=fmt, stage=store)

    assert report["imported"] == 0, report
    assert report["skipped"] >= 1
    assert expected in " | ".join(report["reasons"]), report["reasons"]
    assert not store.calls
    assert load_ledger(tmp_path) == {}


def test_unreadable_path_and_unknown_format_report_rather_than_raising(tmp_path: Path) -> None:
    missing = import_capture_file(tmp_path / "nope.jsonl", fmt="jsonl", stage=FakeStore())
    assert missing["imported"] == 0
    assert any("cannot read" in r for r in missing["reasons"]), missing["reasons"]

    log = tmp_path / "session.jsonl"
    log.write_text(_claude_code_jsonl(), encoding="utf-8")
    bogus = import_capture_file(log, fmt="yaml", stage=FakeStore())
    assert bogus["imported"] == 0
    assert any("unknown format 'yaml'" in r for r in bogus["reasons"]), bogus["reasons"]
    # The reason names the valid set so a typo is self-correcting.
    assert all(f in " ".join(bogus["reasons"]) for f in FORMATS)


def test_corrupt_ledger_fails_open(tmp_path: Path) -> None:
    """One bad byte in the ledger must not block every future import."""
    ledger = tmp_path / "capture" / "import_ledger.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{not json", encoding="utf-8")

    log = tmp_path / "session.jsonl"
    log.write_text(_claude_code_jsonl(), encoding="utf-8")
    assert load_ledger(tmp_path) == {}
    assert import_capture_file(log, fmt="jsonl", stage=FakeStore())["imported"] == 2


# ── 5. The CLI path, end to end ──


def _parse_argv(argv: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Drive the real ``cli.main`` parser and capture the namespace it built.

    Going through ``main`` rather than hand-rolling a Namespace is the point: a
    Namespace built by hand would pass even if the subcommand were never
    registered, or if ``--format``'s dest were misspelled.
    """
    import personalclaw.cli as cli

    seen: dict[str, Any] = {}

    def _capture_dispatch(args, **kwargs):  # noqa: ANN001, ANN003
        seen["args"] = args
        return 0

    monkeypatch.setattr(cli, "_capture_cmd", _capture_dispatch)
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["personalclaw", *argv])
    cli.main()
    assert "args" in seen, "cli.main() never dispatched to the capture command"
    return seen["args"]


def test_cli_registers_capture_import_and_reports_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`personalclaw capture import <file> --format jsonl --source <label>`.

    Two legs, both needed: the parser must build the namespace ``capture_cmd``
    expects (proving registration and flag/dest names), and ``capture_cmd`` must
    print the counts (proving the operator can see a partial import happened).
    """
    log = tmp_path / "session.jsonl"
    log.write_text(_claude_code_jsonl() + "\nnot json\n", encoding="utf-8")

    args = _parse_argv(
        ["capture", "import", str(log), "--format", "jsonl", "--source", "claude-code"],
        monkeypatch,
        tmp_path,
    )
    assert args.command == "capture"
    assert args.capture_action == "import"
    assert args.file == str(log)
    assert args.format == "jsonl"
    assert args.source == "claude-code"
    assert args.as_json is False

    store = FakeStore()
    rc = capture_cmd(args, stage=store)
    out = capsys.readouterr().out

    assert rc == 0
    assert "imported 2" in out, out
    assert "skipped 1" in out, out
    assert "claude-code" in out, out
    assert "invalid JSON" in out, out
    assert store.calls[-1][1] == "claude-code"
    # The CLI wrote the ledger under the temp home, not the real one.
    assert list(load_ledger(tmp_path))


def test_cli_json_output_and_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` emits the §8 report verbatim; exit code distinguishes outcomes."""
    log = tmp_path / "session.jsonl"
    log.write_text(_claude_code_jsonl(), encoding="utf-8")

    args = _parse_argv(
        ["capture", "import", str(log), "--format", "jsonl", "--source", "cc", "--json"],
        monkeypatch,
        tmp_path,
    )
    assert args.as_json is True

    store = FakeStore()
    assert capture_cmd(args, stage=store) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["imported"] == 2
    assert payload["skipped"] == 0
    assert payload["reasons"] == []
    assert payload["source"] == "cc"

    # A duplicate re-import is the requested outcome: exit 0, nothing staged.
    assert capture_cmd(args, stage=store) == 0
    repeat = json.loads(capsys.readouterr().out)
    assert repeat["duplicate"] is True
    assert repeat["imported"] == 0
    assert len(store.calls) == 1

    # Nothing staged and not a duplicate is a failure a script can gate on.
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n", encoding="utf-8")
    bad_args = _parse_argv(
        ["capture", "import", str(bad), "--format", "jsonl"], monkeypatch, tmp_path
    )
    assert bad_args.source == "import"  # the default label
    assert capture_cmd(bad_args, stage=FakeStore()) == 1
    assert "invalid JSON" in capsys.readouterr().out


def test_cli_bare_capture_prints_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _parse_argv(["capture"], monkeypatch, tmp_path)
    assert capture_cmd(args, stage=FakeStore()) == 2
    out = capsys.readouterr().out
    assert "capture import" in out
    assert "jsonl|json|sse" in out

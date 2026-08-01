"""The `result_omitted` spill boundary at the journal write (WF2-R11).

A node output crosses three boundaries after it is produced: a journal line every later read
re-parses, a `{{nodes.x.output}}` binding a downstream node interpolates, and an SSE frame a
browser renders. An output that is fine to KEEP can be ruinous to carry inline across all
three — so the journal spills it to a file and leaves a typed stub.

Two reasons, and the second is the one a size check alone misses:

* `oversize` — past 64KB, the same boundary the live chat sanitizer uses;
* `binary` — a magic-prefix match. A 400-byte PNG is under every threshold and still
  meaningless inline: mojibake in the widget, a poisoned binding, wasted context if it
  reaches a model.

The invariant across both: nothing is ever LOST. The full value is always written to the
output file first, and the stub names the ref, so spilling costs a reader one extra read and
never the data.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.journal import (
    MAX_INLINE_OUTPUT_BYTES,
    Journal,
    is_binary_payload,
)
from personalclaw.workflows.models import WorkflowRun


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


@pytest.fixture
def journal() -> Journal:
    run = store.create(WorkflowRun(id="", workflow_name="spill"))
    return Journal(run.id)


class TestBinaryDetection:
    def test_raw_magic_prefixes_are_binary(self) -> None:
        """Raw bytes that reached a `str`. Recovered with latin-1, NOT utf-8: a PNG's leading
        `\\x89` utf-8-encodes to two bytes, so a utf-8 round-trip matches no magic number at
        all — the first version of this check had exactly that bug and detected nothing."""
        for name, magic in (
            ("png", "\x89PNG\r\n\x1a\n"),
            ("jpeg", "\xff\xd8\xff"),
            ("gif", "GIF89a"),
            ("pdf", "%PDF-1.7"),
            ("gzip", "\x1f\x8b"),
            ("zip", "PK\x03\x04"),
            ("elf", "\x7fELF"),
        ):
            assert is_binary_payload(magic + "rest of the bytes"), name

    def test_base64_carried_binary_is_detected(self) -> None:
        """The realistic carrier: a node output is JSON, and JSON cannot hold arbitrary bytes,
        so a screenshot or fetched asset arrives base64'd. A raw-bytes check alone would miss
        every real case."""
        import base64

        for name, raw in (
            ("png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64),
            ("jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 64),
            ("pdf", b"%PDF-1.7\n" + b"x" * 64),
            ("gzip", b"\x1f\x8b\x08\x00" + b"\x00" * 64),
            ("zip", b"PK\x03\x04\x14\x00" + b"\x00" * 64),
        ):
            assert is_binary_payload(base64.b64encode(raw).decode("ascii")), name

    def test_ordinary_text_is_not_binary(self) -> None:
        for text in (
            "a perfectly normal answer",
            '{"json": "output"}',
            "# a markdown report\n\nwith prose",
            "PKG_CONFIG_PATH=/usr/lib",  # starts with PK but not PK\x03\x04
            "%PD",  # a truncated prefix must not match
            "H4 is a pencil grade",  # base64 gzip prefix is H4sI, not H4
            "iVBORNOT_a_png",
            "émigré prose with an accent",  # non-ascii but text
        ):
            assert not is_binary_payload(text), text

    def test_a_container_is_never_binary(self) -> None:
        """Only strings are inspected. Treating a dict as binary because one leaf looked like
        a PNG would spill a whole useful output over one field."""
        assert not is_binary_payload({"screenshot": "\x89PNG\r\n\x1a\n..."})
        assert not is_binary_payload(["\x89PNG\r\n\x1a\n"])

    def test_empty_and_non_string_values_are_not_binary(self) -> None:
        assert not is_binary_payload("")
        assert not is_binary_payload(None)
        assert not is_binary_payload(42)


class TestInlinePath:
    def test_a_small_output_is_returned_inline(self, journal: Journal) -> None:
        ref, preview = journal.store_output("root.a", {"answer": 42})
        assert preview == {"answer": 42}
        assert ref

    def test_the_inline_preview_is_redacted(self, journal: Journal) -> None:
        """Redaction happens before the size check, so a secret cannot ride inline just
        because the payload was small."""
        _ref, preview = journal.store_output("root.a", {"note": "token=ghp_" + "a" * 36})
        assert "ghp_" + "a" * 36 not in json.dumps(preview)

    def test_a_value_exactly_at_the_boundary_stays_inline(self, journal: Journal) -> None:
        """The boundary is inclusive. An off-by-one here would spill outputs the widget could
        have shown, which is a silent loss of detail rather than a visible error."""
        pad = "x" * (MAX_INLINE_OUTPUT_BYTES - len('{"v": ""}'))
        _ref, preview = journal.store_output("root.a", {"v": pad})
        assert preview.get("result_omitted") is not True


class TestSpill:
    def test_an_oversized_output_leaves_a_stub(self, journal: Journal) -> None:
        big = {"v": "x" * (MAX_INLINE_OUTPUT_BYTES + 1000)}
        ref, preview = journal.store_output("root.a", big)
        assert preview["result_omitted"] is True
        assert preview["reason"] == "oversize"
        assert preview["output_ref"] == ref
        assert preview["bytes"] > MAX_INLINE_OUTPUT_BYTES

    def test_a_small_binary_output_still_spills(self, journal: Journal) -> None:
        """The case a size check cannot catch. 400 bytes of PNG is under every threshold and
        still meaningless inline."""
        _ref, preview = journal.store_output("root.a", "\x89PNG\r\n\x1a\n" + "b" * 400)
        assert preview["result_omitted"] is True
        assert preview["reason"] == "binary"

    def test_a_base64_asset_spills_too(self, journal: Journal) -> None:
        import base64

        payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200).decode("ascii")
        _ref, preview = journal.store_output("root.a", payload)
        assert preview["reason"] == "binary"

    def test_binary_wins_over_oversize_in_the_reason(self, journal: Journal) -> None:
        """A reader debugging a spill wants the ROOT reason. "oversize" for a 5MB PNG sends
        them looking for a truncation setting instead of at the node producing binary."""
        _ref, preview = journal.store_output(
            "root.a", "%PDF-1.7" + "x" * (MAX_INLINE_OUTPUT_BYTES + 10)
        )
        assert preview["reason"] == "binary"

    def test_the_full_value_is_always_persisted(self, journal: Journal) -> None:
        """Nothing is lost. Spilling costs a reader one extra read, never the data — which is
        what makes the stub safe to hand to a binding or a widget."""
        big = "x" * (MAX_INLINE_OUTPUT_BYTES + 500)
        _ref, preview = journal.store_output("root.a", big)
        assert preview["result_omitted"] is True
        assert store.read_output(journal.run_id, "root.a") == big

    def test_the_stub_is_json_serializable_and_small(self, journal: Journal) -> None:
        """It travels in an SSE frame and a journal line; a stub that itself needed spilling
        would defeat the point."""
        _ref, preview = journal.store_output("root.a", {"v": "x" * (MAX_INLINE_OUTPUT_BYTES + 1)})
        assert len(json.dumps(preview).encode("utf-8")) < 512

    def test_the_byte_count_is_of_the_encoded_payload(self, journal: Journal) -> None:
        """Bytes, not characters. A multi-byte output whose count was measured in characters
        would under-report by up to 4x and read as "why did that spill?"."""
        value = "é" * (MAX_INLINE_OUTPUT_BYTES // 2 + 100)  # 2 bytes each
        _ref, preview = journal.store_output("root.a", value)
        assert preview["bytes"] >= len(value.encode("utf-8"))

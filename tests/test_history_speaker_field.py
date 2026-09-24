"""AR-2 T1.2 — the additive per-message ``speaker`` field on `ConversationLog.append`.

`AGENT-ROOMS` makes exactly three additive changes to shared surfaces, and this is the one
that touches the file format every ordinary session already writes. That makes it the one
place where rooms can damage something that has nothing to do with rooms, so the rails here
are mostly about what must NOT change:

* a caller that passes no ``speaker`` writes a byte-identical line, so no existing session
  file gains a field and no reader gains a branch;
* an existing pre-``speaker`` file reads back byte-identical, because there is no backfill;
* ``append`` still writes ``agent`` to the metadata line on CREATION only — the regression
  this row exists to prevent, since a per-message field is exactly the kind of change that
  invites someone to "also" make ``agent`` per-message.

The positive half is one line: a room message round-trips its speaker through
:func:`~personalclaw.history.speaker_of`.
"""

from __future__ import annotations

import json

from personalclaw.history import ConversationLog, speaker_of


def test_a_caller_that_passes_no_speaker_writes_no_speaker_key(tmp_path):
    """The whole compatibility claim, at the byte level."""
    log = ConversationLog(base_dir=tmp_path)
    log.append("t1", "user", "hello")

    msg = json.loads((tmp_path / "t1.jsonl").read_text(encoding="utf-8").splitlines()[1])
    assert "speaker" not in msg, "an absent speaker is an absent KEY, not an empty string"


def test_an_empty_speaker_is_identical_to_not_passing_one(tmp_path):
    """Byte-for-byte, so no caller has to know which of the two forms it used."""
    a = ConversationLog(base_dir=tmp_path / "a")
    b = ConversationLog(base_dir=tmp_path / "b")
    a.append("t", "user", "hello", speaker="")
    b.append("t", "user", "hello")

    def _keys_and_values(log_dir):
        """The message line minus its timestamp — the only field that legitimately differs."""
        msg = json.loads((log_dir / "t.jsonl").read_text(encoding="utf-8").splitlines()[1])
        msg.pop("ts", None)
        return msg

    assert _keys_and_values(tmp_path / "a") == _keys_and_values(tmp_path / "b")
    assert "speaker" not in _keys_and_values(tmp_path / "a")


def test_a_pre_speaker_file_reads_back_byte_identical(tmp_path):
    """T1.2's own done-when. There is no backfill, so reading must not rewrite."""
    log = ConversationLog(base_dir=tmp_path)
    log.append("legacy", "user", "written before rooms existed")
    log.append("legacy", "assistant", "and the reply")
    path = tmp_path / "legacy.jsonl"
    before = path.read_bytes()

    messages = log.read_messages("legacy")
    assert [m["content"] for m in messages] == [
        "written before rooms existed",
        "and the reply",
    ]
    assert log.recent("legacy")
    assert log.get_metadata("legacy")

    assert path.read_bytes() == before, "a read must never rewrite the file"


def test_a_speaker_round_trips_when_one_is_supplied(tmp_path):
    log = ConversationLog(base_dir=tmp_path)
    log.append("room", "assistant", "the analyst's view", speaker="analyst")

    msg = log.read_messages("room")[0]
    assert msg["speaker"] == "analyst"
    assert speaker_of(msg) == "analyst"


def test_speaker_of_answers_the_human_for_every_absent_or_junk_value(tmp_path):
    """The tolerant half. A pre-`speaker` line must read as the human, never as a fault."""
    assert speaker_of({}) == ""
    assert speaker_of({"speaker": ""}) == ""
    assert speaker_of({"speaker": "analyst"}) == "analyst"
    # A hand-edited or third-party-written line can carry anything; the reader answers a
    # string or the human, and never raises into a transcript render.
    assert speaker_of({"speaker": None}) == ""
    assert speaker_of({"speaker": 7}) == ""
    assert speaker_of({"speaker": ["analyst"]}) == ""


def test_speaker_does_not_disturb_the_existing_provenance_fields(tmp_path):
    """`speaker` is a third, distinct thing — not a rename of either neighbour."""
    log = ConversationLog(base_dir=tmp_path)
    log.append(
        "t",
        "user",
        "hello",
        source_user="Alice",
        agent="researcher",
        speaker="analyst",
    )

    lines = (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()
    meta, msg = json.loads(lines[0]), json.loads(lines[1])
    assert meta["agent"] == "researcher", "agent stays in the metadata line"
    assert "speaker" not in meta, "speaker is per-message, never metadata"
    assert msg["source_user"] == "Alice", "a human participant on a channel"
    assert msg["speaker"] == "analyst", "the per-message author"
    assert "agent" not in msg, "agent is not per-message"


def test_agent_still_reaches_metadata_on_creation_only(tmp_path):
    """The regression T1.2 exists to prevent: a per-message field must not make `agent` one.

    `append`'s contract is that `agent` lands in the metadata line when the FILE is created
    and is ignored afterwards (`update_metadata` is the way to change it). Adding a
    per-message field is exactly the change that invites someone to "also" plumb `agent`
    through per message, which would silently give two sources of truth for a session's
    agent.
    """
    log = ConversationLog(base_dir=tmp_path)
    log.append("t", "user", "first", agent="researcher")
    log.append("t", "assistant", "second", agent="totally-different")

    assert log.get_metadata("t")["agent"] == "researcher", "creation wins; later calls are ignored"
    for msg in log.read_messages("t"):
        assert "agent" not in msg


def test_rotation_preserves_the_speaker_on_the_lines_it_keeps(tmp_path):
    """Rotation rewrites the kept lines, so a field it did not know about could be dropped.

    It is not, because `_maybe_rotate` moves the raw text of a line rather than re-encoding
    a parsed message — but that is an implementation property worth a rail, since a future
    rewrite through a typed projection would silently lose the field.
    """
    from personalclaw import history

    log = ConversationLog(base_dir=tmp_path)
    chunk = "x" * 8_000
    archive_dir = tmp_path / "archive"
    for i in range(400):
        log.append("t", "assistant", chunk, speaker=f"member-{i % 3}")
        if archive_dir.exists():
            break
    assert archive_dir.exists(), "the fixture must actually rotate for this to mean anything"

    kept = log.read_messages("t")
    assert len(kept) <= history._SESSION_KEEP_LINES
    assert all(speaker_of(m).startswith("member-") for m in kept)

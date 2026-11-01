"""A self-model row's key and a skill proposal's id are names, not security, and keep their values.

Each is a short digest: of a working pattern for the self-model's evidence rows, of a chat's key
for that chat's pending row, and of a proposal's skill, chat and time for the file it is stored
in. A digest of a chat's key is SHA-256, since the key names the chat; the pattern digest keeps
its MD5 and says it guards nothing (``usedforsecurity=False``). A pattern digest that changed would
name every evidence row stored before it differently, and the self-model's evidence would start
again beside its old rows. A proposal's id is minted once and kept on its record, so one filed
before the move to SHA-256 keeps its id. The values here are the ones each writes today.
"""

from __future__ import annotations

from personalclaw.learning import self_model_observer
from personalclaw.skills import proposals


def test_a_self_model_key_is_the_digest_its_rows_are_stored_under():
    assert self_model_observer._slug("PersonalClaw + edit_file,read_file") == "5b5a1726aeb65698"


def test_a_chats_pending_row_is_named_by_a_sha256_of_its_key():
    pending = self_model_observer._pending_key("dashboard:chat-1")
    assert pending == "user.selfmodel.pending.125e4061406b8ced"


def test_a_proposal_id_is_the_digest_its_file_is_stored_under():
    made = proposals._make_id(
        "imported/example/notes", "dashboard:chat-1", "2026-10-31T00:00:00+00:00"
    )
    assert made == "imported-example-notes-f5927599da04"

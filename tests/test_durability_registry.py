"""The versioned sync registry model.

The pure coordination model the sync cycle turns on: parse/serialize registry.json
canonically (so a CAS sha is stable), bump the local seq monotonically on export, and
name the folder each seq's copy lives in, and read a seq back from a key. No I/O, no clock — the
timestamp is passed in — so a replay is byte-deterministic.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.durability.registry import (
    REGISTRY_KEY,
    MachineEntry,
    Registry,
    machine_prefix,
    seq_of_key,
    shard_prefix,
)


class TestShardPrefix:
    def test_zero_padded_to_four(self):
        assert shard_prefix("m1", 7) == "machines/m1/seq-0007/"
        assert shard_prefix("m1", 1234) == "machines/m1/seq-1234/"

    def test_lexical_sort_is_chronological_up_to_9999(self):
        keys = [shard_prefix("m", s) for s in (1, 2, 10, 100)]
        assert keys == sorted(keys)  # zero-pad makes list-sort == seq-order

    def test_registry_key_is_the_shared_object(self):
        assert REGISTRY_KEY == "registry.json"


class TestParseSerialize:
    def test_empty_from_none_and_blank(self):
        assert Registry.loads(None).machines == {}
        assert Registry.loads("").machines == {}
        assert Registry.loads(b"   ").machines == {}

    def test_round_trip(self):
        r = Registry(machines={"m1": MachineEntry("m1", seq=3, last_export_at="t")})
        again = Registry.loads(r.to_bytes())
        assert again.seq_of("m1") == 3
        assert again.machines["m1"].last_export_at == "t"

    def test_it_holds_nothing_of_what_a_copy_contains(self):
        """The registry is the one object an encrypted sync leaves readable, so it says which seq
        is each machine's newest and when it landed — never a digest of the copy. An older
        registry's field is read past, and not written back."""
        older = json.dumps(
            {"machines": {"m1": {"seq": 2, "last_export_at": "t", "manifest_sha": "abc"}}}
        )
        r = Registry.loads(older.encode())
        assert json.loads(r.to_bytes()) == {"machines": {"m1": {"last_export_at": "t", "seq": 2}}}

    def test_serialization_is_canonical_and_byte_stable(self):
        # Two registries with the same logical content serialize to identical bytes
        # regardless of insertion order — the property a CAS sha comparison needs.
        a = Registry()
        a.bump("z", now="t1")
        a.bump("a", now="t2")
        b = Registry()
        b.bump("a", now="t2")
        b.bump("z", now="t1")
        assert a.to_bytes() == b.to_bytes()
        assert a.sha() == b.sha()

    def test_sha_changes_when_content_changes(self):
        r = Registry()
        r.bump("m1", now="t")
        before = r.sha()
        r.bump("m1", now="t2")
        assert r.sha() != before

    def test_corrupt_registry_raises_not_guesses(self):
        # A mis-parsed coordinator would let two machines both own a seq — fail loud.
        with pytest.raises(json.JSONDecodeError):
            Registry.loads(b"{not json")

    def test_malformed_machine_entry_degrades_to_zero(self):
        # A missing/garbage seq must not crash a sync — it degrades to re-publish.
        r = Registry.loads(json.dumps({"machines": {"m1": {"seq": "oops"}}}).encode())
        # int("oops") would raise inside from_dict? No — guarded: falls back to 0.
        assert r.seq_of("m1") == 0

    def test_non_dict_machine_value_skipped(self):
        r = Registry.loads(json.dumps({"machines": {"m1": "nope", "m2": {"seq": 2}}}).encode())
        assert "m1" not in r.machines and r.seq_of("m2") == 2


class TestBump:
    def test_first_bump_is_seq_one(self):
        r = Registry()
        assert r.bump("m1", now="t") == 1
        assert r.seq_of("m1") == 1

    def test_bump_is_monotonic(self):
        r = Registry()
        r.bump("m1", now="t1")
        r.bump("m1", now="t2")
        assert r.seq_of("m1") == 2
        assert r.machines["m1"].last_export_at == "t2"  # when the newest landed

    def test_seq_of_absent_machine_is_zero(self):
        assert Registry().seq_of("nobody") == 0


class TestPeers:
    def test_peers_exclude_self_sorted_seq_desc_then_id(self):
        r = Registry()
        r.bump("self", now="t")
        r.bump("b", now="t")
        r.bump("a", now="t")
        r.bump("a", now="t")  # a → seq 2
        peers = r.peers("self")
        assert [(e.machine_id, e.seq) for e in peers] == [("a", 2), ("b", 1)]

    def test_no_peers_when_only_self(self):
        r = Registry()
        r.bump("self", now="t")
        assert r.peers("self") == []


class TestSeqOfKey:
    """The inverse of ``shard_prefix``: which of a machine's seqs an object key belongs to, so a
    machine removes only its own copies' objects."""

    def test_a_key_of_a_seq_reads_back_its_seq(self):
        assert seq_of_key("m1", shard_prefix("m1", 7) + "tasks/entities.jsonl") == 7
        assert seq_of_key("m1", shard_prefix("m1", 12345) + "manifest.json") == 12345
        assert machine_prefix("m1") == "machines/m1/"

    def test_any_other_key_is_none(self):
        for key in (
            REGISTRY_KEY,
            "encryption-salt",
            shard_prefix("m2", 7) + "manifest.json",  # another machine's
            "machines/m1/seq-0007",  # the folder, no object
            "machines/m1/seq-0007/",
            "machines/m1/notes.txt",  # under the machine's folder, not a seq's
            "machines/m1/seq-00007/manifest.json",  # not the folder seq 7 is written in
            "machines/m1/seq-7/manifest.json",
            "machines/m1x/seq-0007/manifest.json",  # a machine whose id starts with this one's
        ):
            assert seq_of_key("m1", key) is None, key


class TestAdvancedOver:
    def test_reports_peers_that_moved(self):
        prior = Registry()
        prior.bump("p1", now="t")  # p1 seq 1
        cur = Registry.loads(prior.to_bytes())
        cur.bump("p1", now="t2")  # p1 → 2
        cur.bump("p2", now="t")  # new peer
        moved = {e.machine_id for e in cur.advanced_over(prior, self_id="self")}
        assert moved == {"p1", "p2"}

    def test_our_own_bump_is_not_news(self):
        prior = Registry()
        cur = Registry.loads(prior.to_bytes())
        cur.bump("self", now="t")
        assert cur.advanced_over(prior, self_id="self") == []

    def test_no_movement_is_empty(self):
        prior = Registry()
        prior.bump("p1", now="t")
        cur = Registry.loads(prior.to_bytes())
        assert cur.advanced_over(prior, self_id="self") == []

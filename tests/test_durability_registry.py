"""DURABILITY-AND-SYNC §4.1 / DAS-6c-ii-a — the versioned sync registry model.

The pure coordination model the sync cycle turns on: parse/serialize registry.json
canonically (so a CAS sha is stable), bump the local seq monotonically on export, and
tell the cycle which peer shard prefixes it hasn't pulled yet. No I/O, no clock — the
timestamp is passed in — so a replay is byte-deterministic.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.durability.registry import (
    REGISTRY_KEY,
    MachineEntry,
    Registry,
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
        r = Registry(
            machines={"m1": MachineEntry("m1", seq=3, last_export_at="t", manifest_sha="sha")}
        )
        again = Registry.loads(r.to_bytes())
        assert again.seq_of("m1") == 3
        assert again.machines["m1"].manifest_sha == "sha"
        assert again.machines["m1"].last_export_at == "t"

    def test_serialization_is_canonical_and_byte_stable(self):
        # Two registries with the same logical content serialize to identical bytes
        # regardless of insertion order — the property a CAS sha comparison needs.
        a = Registry()
        a.bump("z", manifest_sha="s1", now="t1")
        a.bump("a", manifest_sha="s2", now="t2")
        b = Registry()
        b.bump("a", manifest_sha="s2", now="t2")
        b.bump("z", manifest_sha="s1", now="t1")
        assert a.to_bytes() == b.to_bytes()
        assert a.sha() == b.sha()

    def test_sha_changes_when_content_changes(self):
        r = Registry()
        r.bump("m1", manifest_sha="s", now="t")
        before = r.sha()
        r.bump("m1", manifest_sha="s2", now="t2")
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
        assert r.bump("m1", manifest_sha="s", now="t") == 1
        assert r.seq_of("m1") == 1

    def test_bump_is_monotonic(self):
        r = Registry()
        r.bump("m1", manifest_sha="s1", now="t1")
        r.bump("m1", manifest_sha="s2", now="t2")
        assert r.seq_of("m1") == 2
        assert r.machines["m1"].manifest_sha == "s2"  # latest export's sha recorded

    def test_seq_of_absent_machine_is_zero(self):
        assert Registry().seq_of("nobody") == 0


class TestPeers:
    def test_peers_exclude_self_sorted_seq_desc_then_id(self):
        r = Registry()
        r.bump("self", manifest_sha="s", now="t")
        r.bump("b", manifest_sha="s", now="t")
        r.bump("a", manifest_sha="s", now="t")
        r.bump("a", manifest_sha="s", now="t")  # a → seq 2
        peers = r.peers("self")
        assert [(e.machine_id, e.seq) for e in peers] == [("a", 2), ("b", 1)]

    def test_no_peers_when_only_self(self):
        r = Registry()
        r.bump("self", manifest_sha="s", now="t")
        assert r.peers("self") == []


class TestNewPrefixesSince:
    def test_yields_unseen_seqs_ascending(self):
        r = Registry()
        for _ in range(3):
            r.bump("peer", manifest_sha="s", now="t")  # peer at seq 3
        # Cursor has seen seq 1; expect prefixes for 2 and 3, oldest first.
        got = r.new_prefixes_since("self", seen={"peer": 1})
        assert got == [shard_prefix("peer", 2), shard_prefix("peer", 3)]

    def test_fully_seen_peer_yields_nothing(self):
        r = Registry()
        r.bump("peer", manifest_sha="s", now="t")
        r.bump("peer", manifest_sha="s", now="t")  # seq 2
        assert r.new_prefixes_since("self", seen={"peer": 2}) == []

    def test_unseen_peer_starts_at_seq_one(self):
        r = Registry()
        r.bump("peer", manifest_sha="s", now="t")
        r.bump("peer", manifest_sha="s", now="t")  # seq 2, never seen
        got = r.new_prefixes_since("self", seen={})
        assert got == [shard_prefix("peer", 1), shard_prefix("peer", 2)]

    def test_re_poll_is_idempotent(self):
        # After merging up to seq N, re-polling the same registry yields nothing new.
        r = Registry()
        r.bump("peer", manifest_sha="s", now="t")
        r.bump("peer", manifest_sha="s", now="t")
        first = r.new_prefixes_since("self", seen={})
        assert first  # something to pull
        seen = {"peer": 2}  # cursor advanced to the highest merged seq
        assert r.new_prefixes_since("self", seen=seen) == []

    def test_multiple_peers_all_covered(self):
        r = Registry()
        r.bump("p1", manifest_sha="s", now="t")
        r.bump("p2", manifest_sha="s", now="t")
        r.bump("p2", manifest_sha="s", now="t")  # p2 → 2
        got = r.new_prefixes_since("self", seen={})
        assert shard_prefix("p1", 1) in got
        assert shard_prefix("p2", 1) in got and shard_prefix("p2", 2) in got


class TestAdvancedOver:
    def test_reports_peers_that_moved(self):
        prior = Registry()
        prior.bump("p1", manifest_sha="s", now="t")  # p1 seq 1
        cur = Registry.loads(prior.to_bytes())
        cur.bump("p1", manifest_sha="s2", now="t2")  # p1 → 2
        cur.bump("p2", manifest_sha="s", now="t")  # new peer
        moved = {e.machine_id for e in cur.advanced_over(prior, self_id="self")}
        assert moved == {"p1", "p2"}

    def test_our_own_bump_is_not_news(self):
        prior = Registry()
        cur = Registry.loads(prior.to_bytes())
        cur.bump("self", manifest_sha="s", now="t")
        assert cur.advanced_over(prior, self_id="self") == []

    def test_no_movement_is_empty(self):
        prior = Registry()
        prior.bump("p1", manifest_sha="s", now="t")
        cur = Registry.loads(prior.to_bytes())
        assert cur.advanced_over(prior, self_id="self") == []

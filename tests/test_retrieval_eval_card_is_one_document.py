"""``personalclaw retrieval-eval --card > card.json`` writes one document ``--label`` reads back.

It printed a ``=== <store> store ===`` header, the qrels, corpus and saved lines, then a card, for
each store: with both stores (the default) stdout was two cards wrapped in lines of text, which no
JSON reader takes, so the documented loop (``--card > card.json``, edit, ``--label
card.json``) could not start. Now stdout is the card for one store, or both stores' cards as a
list in ``STORES`` order, and what it did on the way is on stderr; ``--label`` reads either shape,
gives each card's labels to the store it declares, and relabels only those stores.

Driven with the bench's store, benchmark and card functions faked: what is tested is the command's
output and what it hands the bench, not the bench.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from personalclaw import cli
from personalclaw.cli_commands import _retrieval_eval
from personalclaw.evals import retrieval_bench as rb


def card(store: str) -> dict:
    return {
        "store": store,
        "queries": [{"query": f"what does {store} hold", "candidates": ["a", "b"]}],
    }


@pytest.fixture
def bench(monkeypatch, tmp_path):
    """The bench's store, benchmark, save and card functions, faked: what each was handed."""
    applied: list[tuple[str, dict]] = []

    def build_benchmark(store_kind, handle):
        return SimpleNamespace(store=store_kind, queries=(), corpus_snapshot_ref=f"{store_kind}@1")

    def apply_hand_labels(benchmark, labels):
        applied.append((benchmark.store, dict(labels)))
        return benchmark

    monkeypatch.setattr(rb, "open_store", lambda kind: (SimpleNamespace(), tmp_path / f"{kind}.db"))
    monkeypatch.setattr(rb, "build_benchmark", build_benchmark)
    monkeypatch.setattr(rb, "save_benchmark", lambda b: tmp_path / f"{b.store}.json")
    monkeypatch.setattr(rb, "retriever_for", lambda kind, handle: None)
    monkeypatch.setattr(rb, "hand_label_card", lambda benchmark, retriever: card(benchmark.store))
    monkeypatch.setattr(rb, "apply_hand_labels", apply_hand_labels)
    return applied


def _run(capsys, *flags: str) -> tuple[str, str]:
    _retrieval_eval(cli.build_parser().parse_args(["retrieval-eval", *flags]))
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_one_stores_card_is_the_whole_of_stdout(bench, capsys):
    out, err = _run(capsys, "--store", "knowledge", "--card")
    assert json.loads(out) == card("knowledge")
    assert "=== knowledge store ===" in err and "qrels:" in err


def test_both_stores_cards_are_one_list(bench, capsys):
    out, err = _run(capsys, "--card")
    assert out == json.dumps([card(kind) for kind in rb.STORES], indent=2) + "\n"
    assert all(f"=== {kind} store ===" in err for kind in rb.STORES)


def test_the_list_it_prints_is_read_back_one_store_at_a_time(bench, capsys, tmp_path):
    out, _ = _run(capsys, "--card")
    cards = json.loads(out)
    for each in cards:
        each["queries"][0]["relevant"] = [f"{each['store']}-answer"]
    path = tmp_path / "card.json"
    path.write_text(json.dumps(cards), encoding="utf-8")
    _run(capsys, "--label", str(path))
    assert bench == [(kind, {f"what does {kind} hold": [f"{kind}-answer"]}) for kind in rb.STORES]


def test_a_card_for_one_store_relabels_only_that_store(bench, capsys, tmp_path):
    one = card("memory")
    one["queries"][0]["relevant"] = []
    path = tmp_path / "card.json"
    path.write_text(json.dumps(one), encoding="utf-8")
    _run(capsys, "--label", str(path))
    assert bench == [("memory", {"what does memory hold": []})]

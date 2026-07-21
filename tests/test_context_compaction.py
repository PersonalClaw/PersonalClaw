"""Structured context compaction for the native loop (the compact hook)."""

from __future__ import annotations

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.context_compaction import (
    _drop_orphan_tool_results,
    compact,
    extract_file_refs,
    prune_tool_outputs,
    should_compact,
    total_chars,
)


def _convo(n_tool_rounds: int, tool_size: int = 2000) -> list[dict]:
    msgs: list[dict] = [{"role": "user", "content": "system + first message"}]
    for i in range(n_tool_rounds):
        msgs.append(
            {
                "role": "assistant",
                "content": f"step {i}",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "bash", "arguments": f'{{"path": "src/f{i}.py"}}'},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "X" * tool_size})
    msgs.append({"role": "user", "content": "latest request"})
    msgs.append({"role": "assistant", "content": "latest reply"})
    return msgs


# ── pruning pre-pass ──


def test_prune_shrinks_old_verbose_tool_results():
    msgs = _convo(10)
    before = total_chars(msgs)
    pruned = prune_tool_outputs(msgs)
    assert total_chars(pruned) < before
    assert len(pruned) == len(msgs)  # never drops messages (tool-pairing)


def test_prune_keeps_recent_tool_results_full():
    msgs = _convo(10)
    pruned = prune_tool_outputs(msgs)
    tool_contents = [m["content"] for m in pruned if m["role"] == "tool"]
    # The most recent few stay full (length 2000), older ones are digested.
    assert tool_contents[-1] == "X" * 2000
    assert any("pruned tool result" in c for c in tool_contents)


def test_prune_leaves_small_results_alone():
    msgs = [{"role": "tool", "tool_call_id": "c", "content": "short"}]
    assert prune_tool_outputs(msgs)[0]["content"] == "short"


def test_prune_preserves_projection_raw_ref(caplog):
    """OP4 (no double-loss): a projected result carries its tool_result_get affordance
    in-content; pruning must keep the result_id in the digest so the raw stays
    recoverable AFTER compaction — else a compacted projection is unrecoverable."""
    projected = (
        "preview\n\n[projected log output: showing 100 of 5000 chars — "
        'full result: tool_result_get(result_id="r_ab12")]' + "\n" * 700
    )
    # Old (non-recent) verbose result + 5 recent small ones so it gets pruned.
    msgs = [{"role": "tool", "tool_call_id": "c0", "content": projected}]
    msgs += [{"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 10} for i in range(1, 6)]
    pruned = prune_tool_outputs(msgs)
    digest = pruned[0]["content"]
    assert "pruned tool result" in digest
    assert 'tool_result_get(result_id="r_ab12")' in digest  # recovery handle survived
    # a result with no affordance still digests normally (no spurious handle)
    plain = [{"role": "tool", "tool_call_id": "c", "content": "y" * 800}]
    plain += [{"role": "tool", "tool_call_id": f"d{i}", "content": "z" * 10} for i in range(5)]
    assert "tool_result_get" not in prune_tool_outputs(plain)[0]["content"]


# ── file extraction ──


def test_extract_files_from_tool_args_and_content():
    msgs = _convo(3)
    msgs.append({"role": "user", "content": "also check config/loader.py please"})
    files = extract_file_refs(msgs)
    assert "src/f0.py" in files  # from tool args
    assert "config/loader.py" in files  # from content


# ── full compact ──


def test_compact_reduces_and_preserves_anchors():
    msgs = _convo(10)
    before = total_chars(msgs)
    c = compact(msgs)
    assert total_chars(c) < before
    # Latest user + assistant survive verbatim in the tail.
    assert c[-1]["content"] == "latest reply"
    assert c[-2]["content"] == "latest request"
    # A fenced compaction summary is present.
    assert any("CONTEXT COMPACTION" in str(m.get("content", "")) for m in c)


def test_compact_preserves_file_references():
    msgs = _convo(10)
    c = compact(msgs)
    blob = "\n".join(str(m.get("content", "")) for m in c)
    assert "Relevant Files" in blob
    assert "src/f0.py" in blob  # an early file (in the compacted middle) survives


def test_compact_short_convo_is_just_prepass():
    msgs = _convo(1)  # below head+tail protection
    c = compact(msgs)
    # No summary injected — too short to have a middle.
    assert not any("CONTEXT COMPACTION" in str(m.get("content", "")) for m in c)


def test_compact_uses_summarize_fn_when_given():
    msgs = _convo(10)
    c = compact(msgs, summarize_fn=lambda middle: "LLM SUMMARY HERE")
    assert any("LLM SUMMARY HERE" in str(m.get("content", "")) for m in c)


def test_drop_orphan_tool_results():
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "tool", "tool_call_id": "orphan", "content": "no matching call"},
        {
            "role": "assistant",
            "content": "a",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "paired"},
    ]
    out = _drop_orphan_tool_results(msgs)
    ids = [m.get("tool_call_id") for m in out if m["role"] == "tool"]
    assert "orphan" not in ids and "c1" in ids


# ── anti-thrashing ──


def test_should_compact_anti_thrash():
    assert should_compact([]) is True
    assert should_compact([0.5]) is True
    assert should_compact([0.05, 0.05]) is False  # 2 weak saves → skip
    assert should_compact([0.5, 0.05]) is True  # only 1 weak → still try
    assert should_compact([0.05, 0.5]) is True


# ── native loop integration ──


def _runtime():
    class _Model:
        supports_tools = True
        _model = "s"

    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="s"),
        model_provider=_Model(),
        tool_providers=[],
    )


def test_maybe_compact_skips_under_threshold():
    rt = _runtime()
    rt._messages = _convo(10)
    rt._last_context_pct = 50.0  # under 70
    before = len(rt._messages)
    rt._maybe_compact()
    assert len(rt._messages) == before  # untouched


def test_maybe_compact_fires_over_threshold():
    rt = _runtime()
    rt._messages = _convo(10)
    rt._last_context_pct = 85.0  # over 70
    before = total_chars(rt._messages)
    rt._maybe_compact()
    assert total_chars(rt._messages) < before
    assert rt._compaction_saves  # recorded a save fraction


def test_maybe_compact_anti_thrash_blocks_repeat():
    rt = _runtime()
    rt._messages = _convo(10)
    rt._last_context_pct = 85.0
    rt._compaction_saves = [0.02, 0.02]  # two prior weak saves
    before = len(rt._messages)
    rt._maybe_compact()
    assert len(rt._messages) == before  # anti-thrash skipped it

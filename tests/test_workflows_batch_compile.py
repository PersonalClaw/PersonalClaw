"""Tests for batch-spawn compilation and its hardening contract (WORK-CONTAINERS §3, S48).

Two properties carry this module, and both were measured against the engine rather than assumed.

**Per-leaf error isolation is not free.** A `parallel` with the default `join: all` goes FAILED the
moment one child fails, so one bad leaf would sink a five-way fan-out — the exact opposite of "four
still return". `join: quorum` with `quorum: 1` is what actually delivers it.

**A config key the engine does not read is not a control.** An earlier version emitted `workspace`,
`capability`, `tool_posture` and `timeout_secs` into node config; nothing in the engine reads any of
them, and `on_error: "continue"` is not a value it recognizes either. In a module whose
whole subject
is least-privilege, four keys that look like enforcement and enforce nothing is the worst possible
failure — so unenforced declarations now travel under a name that says so.
"""

import pytest

from personalclaw.workflows.batch_compile import (
    COMPILE_THRESHOLD,
    MAX_LEAVES,
    ORCHESTRATION_TOOLS,
    Capability,
    LeafTask,
    compile_batch,
    is_write_tool,
    leaf_tool_posture,
    lineage_env,
    recall_view,
    schema_to_contract,
    single_writer_lint,
)
from personalclaw.workflows.models import InstanceState, Node
from personalclaw.workflows.tick import derive_state
from personalclaw.workflows.validator import validate_node_tree


def leaves(n: int, **kw) -> list[LeafTask]:
    return [LeafTask(task=f"task number {i}", **kw) for i in range(n)]


def codes(result) -> set[str]:
    return {f.code for f in result.findings}


# ── the threshold rule ──


def test_a_single_task_does_NOT_compile():
    """Ad-hoc "go check X while I keep chatting" is chat-native delegation. Forcing a run
    record plus
    project resolution onto it is ceremony that kills the personal feel."""
    result = compile_batch(leaves(1))
    assert result.compiled is False
    assert result.spec == {}


def test_two_tasks_compile():
    assert compile_batch(leaves(COMPILE_THRESHOLD)).compiled is True


def test_the_compiled_spec_VALIDATES_against_the_engines_own_validator():
    """A compiled spec the engine would reject is a batch that fails at start with a schema error
    instead of running — worse than the fire-and-forget path it replaced."""
    result = compile_batch(leaves(3))
    issues = validate_node_tree(Node.from_dict(result.spec["root"])).issues
    assert [i for i in issues if i.severity == "error"] == []


def test_too_many_leaves_is_REFUSED():
    """Past the cap the widget is unreadable and most leaves are queued anyway. An author who wants
    more should write a workflow, where the shape is explicit."""
    result = compile_batch(leaves(MAX_LEAVES + 1))
    assert result.ok is False
    assert "too_many_leaves" in codes(result)


def test_a_refusal_still_returns_the_SPEC_it_would_have_built():
    """A refusal with no artifact leaves the author guessing at what the compiler understood."""
    result = compile_batch(leaves(MAX_LEAVES + 1))
    assert result.spec["root"]["children"]


# ── per-leaf error isolation, measured ──


def test_one_failed_leaf_does_NOT_sink_the_batch():
    """MEASURED: a `parallel` with the default `join: all` goes FAILED as soon as one child
    fails, so
    the first version of this compile would have had one bad leaf sink a five-way fan-out. The whole
    point of five parallel investigations is that four still return."""
    result = compile_batch(leaves(3))
    node = Node.from_dict(result.spec["root"])
    states = {
        "root.children[0]": InstanceState.FAILED,
        "root.children[1]": InstanceState.DONE,
        "root.children[2]": InstanceState.DONE,
    }
    assert derive_state(node, "root", states) is InstanceState.DONE


def test_EVERY_leaf_failing_still_fails_the_batch():
    """Isolation is not indifference. A batch where nothing succeeded has not succeeded, and
    reporting it DONE would be the board lying about work that produced nothing."""
    result = compile_batch(leaves(3))
    node = Node.from_dict(result.spec["root"])
    states = {f"root.children[{i}]": InstanceState.FAILED for i in range(3)}
    assert derive_state(node, "root", states) is InstanceState.FAILED


def test_the_isolation_comes_from_a_join_policy_the_engine_READS():
    config = compile_batch(leaves(2)).spec["root"]["config"]
    assert config["join"] == "quorum"
    assert config["quorum"] == 1


# ── no inert config keys ──


def test_the_node_config_carries_ONLY_keys_the_engine_reads():
    """Measured: `workspace`, `capability`, `tool_posture` and `timeout_secs` were emitted into node
    config and read by NOTHING; `on_error: "continue"` is not a value the engine recognizes either
    (it checks `fail_run` and defaults to `null_continue`). Four keys that look like controls and
    enforce nothing — in a module about least-privilege, that is the worst kind of bug."""
    config = compile_batch(leaves(2)).spec["root"]["children"][0]["config"]
    assert set(config) <= {"prompt", "model_tier", "agent", "output_contract"}


def test_the_unenforced_posture_travels_under_a_name_that_SAYS_SO():
    """A batch compiled with a read-only posture that nothing enforces is a batch running
    with ambient
    write access and a reassuring payload. Naming it is what stops the caller believing it."""
    result = compile_batch(leaves(2))
    assert result.postures
    pending = result.unenforced()
    assert any("tool-handler" in p for p in pending)
    assert any("workspace" in p for p in pending)
    assert any("per-node override" in p for p in pending)


def test_the_posture_is_per_node_so_a_caller_can_apply_it():
    result = compile_batch([LeafTask(task="a"), LeafTask(task="b", capability=Capability.MUTATING)])
    ids = [c["id"] for c in result.spec["root"]["children"]]
    assert set(result.postures) == set(ids)
    assert result.postures[ids[0]]["read_only"] is True
    assert result.postures[ids[1]]["read_only"] is False


# ── capability classes ──


def test_research_is_the_DEFAULT_capability():
    """The safe direction to be wrong in: a research leaf that needed to write fails visibly and is
    re-declared, while a mutating leaf that only needed to read has write access nobody asked for.
    """
    assert LeafTask(task="x").capability is Capability.RESEARCH


def test_a_research_leaf_is_read_only():
    assert leaf_tool_posture(Capability.RESEARCH)["read_only"] is True


def test_orchestration_tools_are_denied_at_EVERY_depth():
    """A leaf that can spawn is a leaf that can fan out without a budget, and the depth
    counter alone
    would let it happen once per level."""
    for capability in Capability:
        denied = set(leaf_tool_posture(capability)["denied_tools"])
        assert ORCHESTRATION_TOOLS <= denied


def test_a_declared_write_tool_is_honored_for_a_research_leaf():
    """An author who says a research leaf needs one has made a decision; overriding it would
    make the
    declaration pointless. Everything UNDECLARED stays denied."""
    posture = leaf_tool_posture(Capability.RESEARCH, declared=["knowledge_persist", "read_file"])
    assert posture["allowed_writers"] == ["knowledge_persist"]


@pytest.mark.parametrize(
    "name",
    ["file_write", "knowledge_persist", "bash", "git_commit", "artifact_update", "send_email"],
)
def test_a_mutating_tool_is_recognized(name):
    assert is_write_tool(name) is True


@pytest.mark.parametrize("name", ["knowledge_search", "read_file", "list_dir", "web_fetch"])
def test_a_read_tool_is_not_flagged(name):
    assert is_write_tool(name) is False


def test_the_write_marker_list_is_over_inclusive_ON_PURPOSE():
    """A read tool wrongly called a writer costs one declaration; a writer wrongly called a reader
    gives a research leaf silent write access. The asymmetry decides the direction of the guess — so
    a newly-added write tool is denied by DEFAULT rather than admitted."""
    assert is_write_tool("some_future_write_thing") is True


def test_a_research_leaf_declaring_WRITES_is_an_error():
    """A contradiction the author has to resolve: either the leaf mutates (declare it) or the paths
    are wrong. Compiling it either way would silently pick one of the two meanings."""
    result = compile_batch([LeafTask(task="a", writes=["out.md"]), LeafTask(task="b")])
    assert result.ok is False
    assert "research_leaf_writes" in codes(result)


def test_a_mutating_leaf_may_declare_writes():
    result = compile_batch(
        [
            LeafTask(task="a", capability=Capability.MUTATING, writes=["out.md"]),
            LeafTask(task="b"),
        ]
    )
    assert result.ok is True


# ── the single-writer lint ──


def test_two_leaves_writing_ONE_path_is_warned():
    """Two workers writing one file is a lost update, and it is invisible because both leaves report
    success."""
    findings = single_writer_lint(
        [
            LeafTask(task="a", capability=Capability.MUTATING, writes=["report.md"]),
            LeafTask(task="b", capability=Capability.MUTATING, writes=["report.md"]),
        ]
    )
    assert [f.code for f in findings] == ["multi_writer"]
    assert findings[0].severity == "warn"


def test_the_multi_writer_finding_NAMES_the_leaves():
    findings = single_writer_lint(
        [
            LeafTask(task="alpha work", capability=Capability.MUTATING, writes=["r.md"]),
            LeafTask(task="beta work", capability=Capability.MUTATING, writes=["r.md"]),
        ]
    )
    assert "alpha_work_0" in findings[0].message
    assert "beta_work_1" in findings[0].message


def test_a_multi_writer_collision_WARNS_rather_than_refusing():
    """The author may know the writes are to disjoint regions of one directory, and refusing would
    block a legitimate fan-out. But it must be SAID."""
    result = compile_batch(
        [
            LeafTask(task="a", capability=Capability.MUTATING, writes=["r.md"]),
            LeafTask(task="b", capability=Capability.MUTATING, writes=["r.md"]),
        ]
    )
    assert result.ok is True
    assert "multi_writer" in codes(result)


def test_disjoint_writes_are_not_warned():
    findings = single_writer_lint(
        [
            LeafTask(task="a", capability=Capability.MUTATING, writes=["a.md"]),
            LeafTask(task="b", capability=Capability.MUTATING, writes=["b.md"]),
        ]
    )
    assert findings == []


# ── dual depth enforcement ──


def test_a_nested_batch_is_refused_at_COMPILE_not_only_counted():
    """Today's no-recursion rule is PROMPT-level, so a leaf that decided to fan out again would
    succeed once per level before any counter noticed. Static rejection is what makes it a rule."""
    result = compile_batch(leaves(3), depth=1)
    assert result.ok is False
    assert "nested_batch" in codes(result)


def test_the_depth_check_fires_even_below_the_compile_threshold():
    """A single task at depth is still a spawn a leaf should not be making, and reporting `compiled:
    False` with no finding would read as "nothing to see here"."""
    result = compile_batch(leaves(1), depth=1)
    assert "nested_batch" in codes(result)


def test_a_top_level_batch_is_allowed():
    assert compile_batch(leaves(2), depth=0).ok is True


def test_lineage_rides_the_EXISTING_depth_threading_convention():
    """One threading convention, so a leaf that reads one reads both — `__wf_depth` beside the
    existing `__hook_depth` pattern rather than a new mechanism."""
    env = lineage_env(run_id="r-1", project_id="p-1", node_id="leaf_0", depth=1)
    assert env["__wf_depth"] == "1"
    assert env["__wf_run_id"] == "r-1"
    assert env["__wf_project_id"] == "p-1"
    assert env["__wf_node_id"] == "leaf_0"


def test_every_lineage_value_is_a_STRING():
    """It goes into a process environment, where a non-string is a TypeError at spawn time — the
    worst place to discover a type problem."""
    env = lineage_env(run_id="r", project_id="p", node_id="n", depth=2)
    assert all(isinstance(v, str) for v in env.values())


# ── typed leaf outputs compile to the EXISTING contract ──


def test_a_schema_compiles_into_the_engines_own_output_contract():
    """Not a second validator: the engine already checks `output_contract` before any binding
    resolves, and two validators over one field would disagree eventually — with the one that ran
    last winning silently."""
    contract = schema_to_contract({"type": "object", "required": ["findings", "confidence"]})
    assert contract == {"must_be_json": True, "required_keys": ["findings", "confidence"]}


def test_the_contract_reaches_the_compiled_node():
    result = compile_batch(
        [
            LeafTask(task="a", output_schema={"type": "object", "required": ["x"]}),
            LeafTask(task="b"),
        ]
    )
    assert result.spec["root"]["children"][0]["config"]["output_contract"]["required_keys"] == ["x"]


def test_a_schema_field_with_no_contract_equivalent_is_DROPPED():
    """An approximated check that passes malformed data is worse than no check, because it is
    believed."""
    contract = schema_to_contract(
        {"type": "object", "properties": {"x": {"maxLength": 10}}, "additionalProperties": False}
    )
    assert set(contract) == {"must_be_json"}


def test_no_schema_means_no_contract_key():
    """An empty contract on every node would make the engine run a check that can never fail, which
    reads in a spec as though outputs were being validated."""
    assert "output_contract" not in compile_batch(leaves(2)).spec["root"]["children"][0]["config"]


def test_a_malformed_schema_compiles_to_nothing_rather_than_raising():
    assert schema_to_contract("not a schema") == {}
    assert schema_to_contract(None) == {}


# ── the safety-filtered recall view ──


def test_thinking_blocks_are_stripped():
    """A recall view that leaked tool XML would put attacker-controlled markup in front of a model
    that treats markup as structure — and the leaf transcript is exactly where untrusted content
    arrives."""
    view = recall_view("before <thinking>secret reasoning</thinking> after")
    assert "secret reasoning" not in view["text"]
    assert view["control_stripped"] is True


def test_function_call_markup_is_stripped():
    view = recall_view("a <function_calls>{...}</function_calls> b")
    assert "function_calls" not in view["text"]


def test_a_clean_transcript_reports_nothing_stripped():
    view = recall_view("just plain output text")
    assert view["control_stripped"] is False
    assert view["text"] == "just plain output text"


def test_truncation_is_FLAGGED():
    """A truncated projection presented as complete is a reader believing they saw the end of a run
    they did not."""
    view = recall_view("x" * 5000, limit=100)
    assert view["truncated"] is True
    assert len(view["text"]) == 100


def test_a_short_transcript_is_not_flagged_as_truncated():
    assert recall_view("short", limit=100)["truncated"] is False


def test_redaction_goes_through_the_EXISTING_chokepoint(monkeypatch):
    """A second redactor would drift from the one that is maintained, and the drift shows up as a
    credential in a UI."""
    called = {}

    def spy(text):
        called["yes"] = True
        return text

    monkeypatch.setattr("personalclaw.security.redact", spy)
    recall_view("anything")
    assert called


def test_a_FAILED_redactor_withholds_the_view_rather_than_showing_it_raw(monkeypatch):
    """Failing closed costs the projection; failing open costs a credential."""

    def boom(_text):
        raise RuntimeError("redactor unavailable")

    monkeypatch.setattr("personalclaw.security.redact", boom)
    view = recall_view("sk-live-abc123 and other secrets")
    assert view["text"] == ""
    assert view["redacted"] is True
    assert "withheld" in view["error"]


# ── node ids ──


def test_node_ids_are_readable_AND_unique():
    """The text makes the progress widget legible; the index guarantees uniqueness when two tasks
    start with the same words."""
    result = compile_batch(
        [LeafTask(task="check the retry config"), LeafTask(task="check the retry config")]
    )
    ids = [c["id"] for c in result.spec["root"]["children"]]
    assert ids == ["check_the_retry_config_0", "check_the_retry_config_1"]


def test_an_unpunctuated_task_still_yields_an_id():
    assert LeafTask(task="!!!").node_id(0) == "task_0"


def test_the_batch_carries_the_subagent_tool_ORIGIN():
    """It is what collapses these rows on the Work board (S46) and puts them on the subagent prune
    cadence rather than the workflow one."""
    assert compile_batch(leaves(2)).spec["origin"]["kind"] == "subagent-tool"

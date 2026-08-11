"""Batch-spawn compilation and its hardening contract (WORK-CONTAINERS §3, S48 + WF2WOR-9).

Four properties carry this module, and every one of them was measured against the engine rather than
assumed.

**Per-leaf error isolation is not free.** A `parallel` with the default `join: all` goes FAILED the
moment one child fails, so one bad leaf would sink a five-way fan-out — the exact opposite of "four
still return". `join: quorum` with `quorum: 1` is what actually delivers it.

**A config key the engine does not read is not a control.** An earlier version emitted `workspace`,
`capability`, `tool_posture` and `timeout_secs` into node config; nothing in the engine read any of
them, and `on_error: "continue"` is not a value it recognizes either. In a module whose
whole subject
is least-privilege, four keys that look like enforcement and enforce nothing is the worst possible
failure — so unenforced declarations travel under a name that says so.

`capability` is the ONE that has since earned its place in node config (WF2WOR-5 C2): it is read by
`engine.leaf_spawn_env`, which writes the leaf's read-only flag into the per-session spawn env, and
`mcp_shared.leaf_tool_denial` refuses a denied tool at the handler on every call. The same rule
decided both directions — the key was withheld while nothing read it, and is emitted now that
something enforces it. `workspace` and `timeout_secs` remain out, and `unenforced()` still says so.

**The leaf contract is refused at compile AND carried into the prompt.** A declared output format
the worker never saw is a gate that fails 100% of the time, so the compile that requires the
declaration is also the thing that delivers it — and `check_output_contract` (the engine's existing
validator, not a second one) is what catches off-format output.

**Mutating leaves serialize through a `needs` chain, driven not assumed.** `frontier` never makes
two of them ready at once, research leaves stay fully parallel, and a FAILED mutator hands the lane
on rather than stranding the chain — because `_visit_parallel` satisfies a `needs` edge on any
TERMINAL
predecessor. If it stalled instead, serialization would have become a second way for one bad leaf to
sink a batch, which is what `join: quorum` exists to prevent.
"""

import pytest

from personalclaw.workflows.batch_compile import (
    COMPILE_THRESHOLD,
    FORBIDDEN_LEAF_FIELDS,
    MAX_LEAVES,
    MIN_DECLARATION_CHARS,
    ORCHESTRATION_TOOLS,
    Capability,
    LeafTask,
    boundary_lint,
    compile_batch,
    contract_lint,
    forbidden_declarations,
    is_write_tool,
    leaf_tool_posture,
    lineage_env,
    mutating_chain,
    recall_view,
    schema_to_contract,
    single_writer_lint,
)
from personalclaw.workflows.engine import check_output_contract
from personalclaw.workflows.models import InstanceState, Node
from personalclaw.workflows.tick import Limits, derive_state, frontier
from personalclaw.workflows.validator import validate_node_tree

#: A satisfied leaf contract, spelled out once. Every field is REQUIRED at compile (C2.1), so a test
#: fixture that omitted one would be testing the refusal rather than the thing it meant to test —
#: and a dataclass default would have hidden that, which is why the fields carry none.
CONTRACT = {
    "objective": "establish whether the retry ceiling is honoured",
    "output_format": "JSON object with keys findings and confidence",
    "boundary": "read only under config/; touch no source file",
}


def leaf(task: str, **kw) -> LeafTask:
    """One contract-complete leaf. `kw` overrides any contract field under test."""
    return LeafTask(task=task, **{**CONTRACT, **kw})


def leaves(n: int, **kw) -> list[LeafTask]:
    return [leaf(f"task number {i}", **kw) for i in range(n)]


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
    """Measured: `workspace`, `tool_posture` and `timeout_secs` were emitted into node config and
    read by NOTHING; `on_error: "continue"` is not a value the engine recognizes either (it checks
    `fail_run` and defaults to `null_continue`). Keys that look like controls and enforce nothing —
    in a module about least-privilege, that is the worst kind of bug.

    The rule is "only keys something READS", not a frozen list. `capability` joined the set in
    WF2WOR-5 because it acquired a reader — `engine.leaf_spawn_env` turns it into the leaf's
    read-only flag and `mcp_shared.leaf_tool_denial` enforces it per tool call. The other three are
    still absent, and `unenforced()` still names them."""
    config = compile_batch(leaves(2)).spec["root"]["children"][0]["config"]
    assert set(config) <= {
        "prompt",
        "model_tier",
        "agent",
        "model",
        "output_contract",
        "capability",
    }
    for unread in ("workspace", "tool_posture", "timeout_secs", "on_error"):
        assert unread not in config


def test_the_unenforced_posture_travels_under_a_name_that_SAYS_SO():
    """A batch compiled with a read-only posture that nothing enforces is a batch running
    with ambient
    write access and a reassuring payload. Naming it is what stops the caller believing it.

    Both directions matter, so this holds the honest complement too: the tool-handler AND
    workspace_mode lines both LEFT `unenforced()` in WF2WOR-5 when their seams were actually built,
    and claiming a pending control that now exists understates the system exactly as badly as
    claiming an absent one. `timeout_secs` stays pending because it is a real absence — the engine's
    node timeout is per-RUN and there is no per-node override to bind to."""
    result = compile_batch(leaves(2))
    assert result.postures
    pending = result.unenforced()
    assert any("per-node override" in p for p in pending)
    assert not any("tool-handler" in p for p in pending)
    assert not any("workspace_mode" in p for p in pending)
    enforced = result.enforced()
    assert any("leaf_tool_denial" in e for e in enforced)
    assert any("isolated workspace" in e for e in enforced)


def test_the_posture_is_per_node_so_a_caller_can_apply_it():
    result = compile_batch([leaf("a"), leaf("b", capability=Capability.MUTATING)])
    ids = [c["id"] for c in result.spec["root"]["children"]]
    assert set(result.postures) == set(ids)
    assert result.postures[ids[0]]["read_only"] is True
    assert result.postures[ids[1]]["read_only"] is False


# ── capability classes ──


def test_research_is_the_DEFAULT_capability():
    """The safe direction to be wrong in: a research leaf that needed to write fails visibly and is
    re-declared, while a mutating leaf that only needed to read has write access nobody asked for.
    """
    assert leaf("x").capability is Capability.RESEARCH


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
    result = compile_batch([leaf("a", writes=["out.md"]), leaf("b")])
    assert result.ok is False
    assert "research_leaf_writes" in codes(result)


def test_a_mutating_leaf_may_declare_writes():
    result = compile_batch(
        [
            leaf("a", capability=Capability.MUTATING, writes=["out.md"]),
            leaf("b"),
        ]
    )
    assert result.ok is True


# ── C2.1: the leaf contract is load-bearing (amendment (b)) ──


@pytest.mark.parametrize("field_name", ["objective", "output_format", "boundary"])
def test_a_leaf_missing_ANY_contract_field_fails_COMPILATION(field_name):
    """An ERROR, not a warning. The measured failure budget of multi-agent systems goes to
    specification drift (11.8%) and verification (~23.5%), not role confusion (1.5%) — so the
    under-specified leaf IS the failure mode, and compiling it would make the contract a
    suggestion."""
    result = compile_batch([leaf("a", **{field_name: ""}), leaf("b")])
    assert result.ok is False
    assert "leaf_contract_missing" in codes(result)
    assert field_name in " ".join(f.message for f in result.findings)


def test_the_contract_fields_have_NO_dataclass_DEFAULT():
    """A defaulted field is an unsupplied input that satisfies the gate nobody supplied. If
    `objective` defaulted to `""`, every existing caller would keep compiling and C2.1 would be a
    docstring."""
    with pytest.raises(TypeError):
        LeafTask(task="a")  # type: ignore[call-arg]


def test_a_WHITESPACE_declaration_is_treated_as_missing():
    """The field exists to carry specification, and whitespace carries none. Accepting it would make
    the requirement satisfiable by pressing space."""
    result = compile_batch([leaf("a", objective="   "), leaf("b")])
    assert result.ok is False
    assert "leaf_contract_missing" in codes(result)


def test_a_TOO_SHORT_declaration_is_also_refused():
    """ "check it" carries no more specification than "". A floor that admitted a two-word objective
    would let the requirement be satisfied without being satisfied."""
    result = compile_batch([leaf("a", objective="check it"), leaf("b")])
    assert result.ok is False
    assert "leaf_contract_thin" in codes(result)
    assert len("check it") < MIN_DECLARATION_CHARS


def test_a_complete_contract_compiles_clean():
    assert compile_batch(leaves(2)).ok is True
    assert contract_lint(leaves(2)) == []


def test_all_THREE_declarations_reach_the_leaf_PROMPT():
    """A declared output format the worker never saw is a gate that fails 100% of the time — the
    engine's `output_contract` rejects off-format output BEFORE any binding resolves, so the compile
    that requires the declaration has to be the thing that delivers it. Same for the boundary: an
    unstated boundary is not a boundary."""
    prompt = compile_batch(leaves(2)).spec["root"]["children"][0]["config"]["prompt"]
    assert CONTRACT["objective"] in prompt
    assert CONTRACT["output_format"] in prompt
    assert CONTRACT["boundary"] in prompt
    assert "task number 0" in prompt


def test_the_declared_SCHEMA_is_shown_to_the_worker_VERBATIM():
    """A paraphrase would let the prompt and `check_output_contract` drift, and the worker would
    satisfy the paraphrase and fail the check."""
    schema = {"type": "object", "required": ["findings"]}
    prompt = compile_batch([leaf("a", output_schema=schema), leaf("b")]).spec["root"]["children"][
        0
    ]["config"]["prompt"]
    assert '"required": ["findings"]' in prompt


def test_OFF_FORMAT_leaf_output_is_CAUGHT_by_the_engines_own_validator():
    """The C2.1 done_when's second half, driven end-to-end: the declared format compiles into
    `output_contract`, and `check_output_contract` — the EXISTING validator the engine already runs
    before any `{{nodes.x.output}}` binding resolves — is what refuses the off-format value. No
    second checker: two validators over one field would disagree eventually, and the one that ran
    last would win silently."""
    contract = compile_batch(
        [
            leaf("a", output_schema={"type": "object", "required": ["findings", "confidence"]}),
            leaf("b"),
        ]
    ).spec["root"]["children"][0]["config"]["output_contract"]

    assert check_output_contract("just some prose, no JSON at all", contract)
    assert check_output_contract({"findings": ["x"]}, contract)  # missing `confidence`
    assert check_output_contract({"findings": ["x"], "confidence": 0.8}, contract) == ""


def test_a_leaf_writing_INSIDE_its_own_boundary_is_refused():
    """The one place `writes` and `boundary` meet. A leaf saying "I will write db/schema.sql" and
    "do not touch db/" has declared it will do the thing it declared it must not do, and compiling
    it picks one of the two meanings silently."""
    result = compile_batch(
        [
            leaf(
                "a",
                capability=Capability.MUTATING,
                writes=["db/schema.sql"],
                boundary="anything under db/ is off limits",
            ),
            leaf("b"),
        ]
    )
    assert result.ok is False
    assert "boundary_contradicts_writes" in codes(result)


def test_writes_OUTSIDE_the_boundary_are_fine():
    """`boundary` and `writes` are DUALS, not complements — a positive declaration for the compiler
    and a negative one for the worker. Most leaves therefore trip nothing here."""
    findings = boundary_lint(
        [
            leaf(
                "a",
                capability=Capability.MUTATING,
                writes=["out/report.md"],
                boundary="anything under db/ is off limits",
            )
        ]
    )
    assert findings == []


def test_the_boundary_check_is_PATH_shaped_not_a_substring_match():
    """`writes=["reports/x.md"]` against a boundary mentioning the word "report" is a DIFFERENT
    directory, and a naive `in` would call it a contradiction. A gate that cries wolf on a
    legitimate fan-out is a gate that gets switched off."""
    findings = boundary_lint(
        [
            leaf(
                "a",
                capability=Capability.MUTATING,
                writes=["reports/x.md"],
                boundary="do not touch the production report pipeline",
            )
        ]
    )
    assert findings == []


def test_a_boundary_naming_a_PARENT_directory_still_catches_the_write():
    findings = boundary_lint(
        [
            leaf(
                "a",
                capability=Capability.MUTATING,
                writes=["src/personalclaw/engine.py"],
                boundary="never write under src/",
            )
        ]
    )
    assert [f.code for f in findings] == ["boundary_contradicts_writes"]


# ── C2.2: capability enforcement, homogeneity, and the model pin (amendment (a)/(c)) ──


def _mut_leaves(n: int, mutating: set[int]) -> list[LeafTask]:
    return [
        leaf(
            f"task number {i}",
            capability=Capability.MUTATING if i in mutating else Capability.RESEARCH,
        )
        for i in range(n)
    ]


def _drive(node: Node, *, fail: set[str] | None = None) -> list[list[str]]:
    """Drive the REAL frontier tick by tick, returning what launched on each tick.

    The engine's own scheduler, not a model of it: the S48 comment in `compile_batch` records what
    assuming join semantics cost when nobody drove them. Lane caps are lifted so the caps are not
    what serializes the leaves — otherwise a passing test would prove the LANE works, not the
    contract.
    """
    limits = Limits(lanes={"llm": MAX_LEAVES, "io": MAX_LEAVES, "compute": MAX_LEAVES})
    states: dict[str, InstanceState] = {}
    ticks: list[list[str]] = []
    failed = fail or set()
    for _ in range(MAX_LEAVES + 2):
        fr = frontier(node, states, limits=limits)
        if fr.complete or not fr.ready:
            break
        launched = [r.path for r in fr.ready]
        ticks.append(launched)
        for path in launched:
            states[path] = InstanceState.FAILED if path in failed else InstanceState.DONE
    return ticks


def test_two_MUTATING_leaves_never_become_ready_TOGETHER():
    """The C2.2 done_when, driven through `tick.frontier` rather than asserted about the spec.
    Writes stay single-threaded (amendment (c)); the `needs` chain makes the engine honour it."""
    result = compile_batch(_mut_leaves(8, {2, 5, 7}))
    node = Node.from_dict(result.spec["root"])
    mutating = {"root.children[2]", "root.children[5]", "root.children[7]"}
    for launched in _drive(node):
        assert len(set(launched) & mutating) <= 1


def test_RESEARCH_leaves_stay_fully_PARALLEL():
    """Serializing writes must not serialize reads. "Parallelize reads/analysis, single-thread
    writes" is the whole synthesis — a fan-out that ran its five investigations one at a time would
    have kept the cost and thrown away the reason."""
    result = compile_batch(_mut_leaves(8, {2, 5, 7}))
    ticks = _drive(Node.from_dict(result.spec["root"]))
    research = {f"root.children[{i}]" for i in (0, 1, 3, 4, 6)}
    assert research <= set(ticks[0])


def test_an_ALL_MUTATING_batch_runs_strictly_ONE_AT_A_TIME():
    result = compile_batch(_mut_leaves(4, {0, 1, 2, 3}))
    ticks = _drive(Node.from_dict(result.spec["root"]))
    assert [len(t) for t in ticks] == [1, 1, 1, 1]


def test_a_FAILED_mutating_leaf_HANDS_THE_LANE_ON_rather_than_stranding_the_chain():
    """MEASURED against `_visit_parallel`: a `needs` edge is satisfied by any TERMINAL predecessor —
    done, degraded, skipped or FAILED alike. Had it required success, serialization would have
    become a second way for one bad leaf to sink a batch, which is what `join: quorum` prevents."""
    result = compile_batch(_mut_leaves(4, {0, 1, 2, 3}))
    node = Node.from_dict(result.spec["root"])
    ticks = _drive(node, fail={"root.children[0]"})
    launched = [p for tick in ticks for p in tick]
    assert launched == [f"root.children[{i}]" for i in range(4)]


def test_serialization_does_not_break_the_error_ISOLATION_the_batch_already_had():
    """The pre-existing guarantee, re-driven with a chain in the tree: one failed leaf still yields
    a DONE run. A new control that quietly regressed an old one would be a net loss."""
    result = compile_batch(_mut_leaves(4, {1, 3}))
    node = Node.from_dict(result.spec["root"])
    states = {
        "root.children[0]": InstanceState.FAILED,
        "root.children[1]": InstanceState.DONE,
        "root.children[2]": InstanceState.DONE,
        "root.children[3]": InstanceState.DONE,
    }
    assert derive_state(node, "root", states) is InstanceState.DONE


def test_the_serialized_chain_VALIDATES_against_the_engines_own_validator():
    """`needs` may only name SIBLINGS in a parallel block (validator.py) — a chain that referenced
    anything else would be a spec the engine rejects at start."""
    result = compile_batch(_mut_leaves(6, {1, 3, 5}))
    issues = validate_node_tree(Node.from_dict(result.spec["root"])).issues
    assert [i for i in issues if i.severity == "error"] == []


def test_the_chain_is_in_DECLARATION_order():
    """The only order the author gave us. Alphabetical or dependency-derived ordering would both be
    inventions, and an invented order on write-bearing work makes a fan-out unreproducible."""
    leaves_ = _mut_leaves(5, {3, 0, 4})
    assert mutating_chain(leaves_) == [
        "task_number_0_0",
        "task_number_3_3",
        "task_number_4_4",
    ]


def test_a_SINGLE_mutating_leaf_emits_no_needs_edge():
    """Nothing to serialize against. An edge to itself would be a cycle, and an edge to a research
    leaf would serialize a read behind a write for no reason."""
    result = compile_batch(_mut_leaves(3, {1}))
    assert all("needs" not in c for c in result.spec["root"]["children"])


def test_serialization_is_reported_as_ENFORCED_not_as_pending():
    """`unenforced()` names what has no seam; anything actually enforced must come OUT of it or the
    honest list stops being honest. The chain IS enforced — by the frontier."""
    result = compile_batch(_mut_leaves(4, {0, 2}))
    assert any("mutating serialization" in e for e in result.enforced())
    assert not any("mutating serialization" in u for u in result.unenforced())


def test_ONE_mutator_does_not_CLAIM_serialization():
    """With one mutator there is no `needs` edge in the spec, so claiming serialization would claim
    a control the compile did not emit — true outcome, absent mechanism."""
    result = compile_batch(_mut_leaves(3, {1}))
    assert not any("mutating serialization" in e for e in result.enforced())


def test_the_leaf_contract_is_reported_as_ENFORCED():
    """It is: the compile refuses an incomplete one and the declarations ride into the prompt. A
    control this real belongs on the enforced list, or a reader cannot tell it apart from the
    posture items that are still waiting for a seam."""
    result = compile_batch(leaves(2))
    assert any("leaf contract" in e for e in result.enforced())
    assert any("output_contract" in e for e in result.enforced())


def test_NO_PERSONA_field_exists_on_the_leaf_contract():
    """An explicit `done_when` clause, and a standing prohibition rather than a one-time decision.
    The best-powered direct test of personas (162 roles, 4 model families, 2,410 questions) found NO
    improvement with per-persona effects "largely random", and persona churn is bidirectional — one
    measured case fixed 4% while breaking 18%, which is strictly worse than a uniform loss for an
    autonomous system because it destroys reproducibility."""
    assert forbidden_declarations() == []
    assert "persona" in FORBIDDEN_LEAF_FIELDS
    assert "role" in FORBIDDEN_LEAF_FIELDS


def test_a_leaf_is_HOMOGENEOUS_by_default():
    """It inherits the parent's binding, which is what makes homogeneity the default rather than a
    thing every caller must remember to ask for. An always-present `model: ""` would read in a spec
    as though every leaf were pinned to nothing in particular."""
    config = compile_batch(leaves(2)).spec["root"]["children"][0]["config"]
    assert "model" not in config


def test_a_leaf_may_PIN_a_different_model():
    """Heterogeneity by MODEL is the one measured win in the literature — up to 44% accuracy at
    matched cost, or matching the best homogeneous team at 12x lower cost."""
    result = compile_batch([leaf("a", model_ref="Bedrock:some-model-id"), leaf("b")])
    assert result.spec["root"]["children"][0]["config"]["model"] == "Bedrock:some-model-id"
    assert "model" not in result.spec["root"]["children"][1]["config"]
    assert any("model pin" in e for e in result.enforced())


def test_the_pin_field_is_NOT_named_model_on_the_leaf():
    """`mutations._FIELD_ALIASES` already maps the author-facing `model` onto `model_tier`
    (WF2-R20d), so a `workflow_edit` op saying `fields: {model: ...}` on a compiled leaf rewrites
    the TIER and leaves the pin untouched — the author would then debug a key never written."""
    from personalclaw.workflows.mutations import normalize_fields

    assert normalize_fields({"model": "x"}) == {"model_tier": "x"}
    assert not hasattr(leaf("a"), "model")


# ── the single-writer lint ──


def test_two_leaves_writing_ONE_path_is_warned():
    """Two workers writing one file is a lost update, and it is invisible because both leaves report
    success."""
    findings = single_writer_lint(
        [
            leaf("a", capability=Capability.MUTATING, writes=["report.md"]),
            leaf("b", capability=Capability.MUTATING, writes=["report.md"]),
        ]
    )
    assert [f.code for f in findings] == ["multi_writer"]
    assert findings[0].severity == "warn"


def test_the_multi_writer_finding_NAMES_the_leaves():
    findings = single_writer_lint(
        [
            leaf("alpha work", capability=Capability.MUTATING, writes=["r.md"]),
            leaf("beta work", capability=Capability.MUTATING, writes=["r.md"]),
        ]
    )
    assert "alpha_work_0" in findings[0].message
    assert "beta_work_1" in findings[0].message


def test_a_multi_writer_collision_WARNS_rather_than_refusing():
    """The author may know the writes are to disjoint regions of one directory, and refusing would
    block a legitimate fan-out. But it must be SAID."""
    result = compile_batch(
        [
            leaf("a", capability=Capability.MUTATING, writes=["r.md"]),
            leaf("b", capability=Capability.MUTATING, writes=["r.md"]),
        ]
    )
    assert result.ok is True
    assert "multi_writer" in codes(result)


def test_disjoint_writes_are_not_warned():
    findings = single_writer_lint(
        [
            leaf("a", capability=Capability.MUTATING, writes=["a.md"]),
            leaf("b", capability=Capability.MUTATING, writes=["b.md"]),
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
            leaf("a", output_schema={"type": "object", "required": ["x"]}),
            leaf("b"),
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
    result = compile_batch([leaf("check the retry config"), leaf("check the retry config")])
    ids = [c["id"] for c in result.spec["root"]["children"]]
    assert ids == ["check_the_retry_config_0", "check_the_retry_config_1"]


def test_an_unpunctuated_task_still_yields_an_id():
    assert leaf("!!!").node_id(0) == "task_0"


def test_the_batch_carries_the_subagent_tool_ORIGIN():
    """It is what collapses these rows on the Work board (S46) and puts them on the subagent prune
    cadence rather than the workflow one."""
    assert compile_batch(leaves(2)).spec["origin"]["kind"] == "subagent-tool"


# ── VC: an 8-wide fan-out with mutating leaves, driven through the real engine ──


def test_an_EIGHT_WIDE_fanout_with_three_mutators_delivers_all_eight():
    """The VC row's provable half, in ONE test so the three properties are asserted about the SAME
    driven run rather than three runs that each held one of them.

    Two VC clauses are NOT proved here and are not claimed to be: per-child cost visibility and the
    one-click kill belong to rows C1.4/C1.5, and the production `subagent_run` call site does not
    exist yet (WF2WOR-5 owns the cutover and depends on this atom). The plan's execution log records
    that split explicitly — an over-claimed VC is worse than a deferred one.
    """
    result = compile_batch(_mut_leaves(8, {2, 5, 7}), run_name="vc-eight-wide")
    assert result.ok is True
    assert len(result.spec["root"]["children"]) == 8

    node = Node.from_dict(result.spec["root"])
    assert [i for i in validate_node_tree(node).issues if i.severity == "error"] == []

    # (a) all eight leaves are delivered, and (b) no two mutators overlap.
    mutating = {"root.children[2]", "root.children[5]", "root.children[7]"}
    ticks = _drive(node)
    launched = [path for tick in ticks for path in tick]
    assert sorted(launched) == sorted(f"root.children[{i}]" for i in range(8))
    for tick in ticks:
        assert len(set(tick) & mutating) <= 1

    # (c) one failed leaf still yields a DONE run.
    states = {f"root.children[{i}]": InstanceState.DONE for i in range(8)}
    states["root.children[4]"] = InstanceState.FAILED
    assert derive_state(node, "root", states) is InstanceState.DONE

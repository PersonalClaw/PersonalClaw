"""The source-tree checks split from
``tests/test_request_boolean_census.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import pytest
import source_index
from request_boolean_census_shared import BODY_BOOLEANS, SRC, _loader_misreads, _scan

#: Names that are booleans at the doors above and text at others (a status word, a binding's or a
#: secret's value, a workflow gate's answer), so a presence test of them is not a boolean read.
_AMBIGUOUS = {"status", "value", "answer"}


def _body_module(rel: str, text: str) -> bool:
    """A module a door's body is read in: one that reads a request body, or any handler module,
    since a door hands the body it read to its helpers there (`dashboard/handlers/trigger_consent`
    reads a save's `enabled` from the body `triggers` read)."""
    return (
        rel.startswith("dashboard/handlers/")
        or "json_object_body(" in text
        or "await request.json()" in text
        or "await req.json()" in text
    )


def _tree() -> tuple[dict[tuple[str, str, str], object], list[tuple[str, str, str, str, int]], int]:
    declared: dict[tuple[str, str, str], object] = {}
    misreads: list[tuple[str, str, str, str, int]] = []
    modules = 0
    for path in source_index.package_files():
        source = source_index.text(path)
        rel = path.relative_to(SRC).as_posix()
        if not _body_module(rel, source):
            continue
        modules += 1
        found, wrong = _scan(source_index.tree(path))
        declared |= {(rel, fn, field): how for (fn, field), how in found.items()}
        misreads += [(rel, *row) for row in wrong]
    return declared, misreads, modules


@pytest.fixture(scope="module")
def tree_scan() -> tuple[dict, list, int]:
    return _tree()


def test_every_body_boolean_is_pinned(tree_scan):
    """A boolean a door starts reading (or stops reading) fails here until the table says so, with
    what an omitted field means at that door."""
    declared, _, modules = tree_scan
    assert modules >= 80, f"only {modules} modules read a request body: the scan lost its root"
    added = {k: v for k, v in declared.items() if BODY_BOOLEANS.get(k, object()) != v}
    gone = sorted(set(BODY_BOOLEANS) - set(declared))
    assert not added and not gone, (
        "Read each body boolean with request_validation.bool_field (its default the field's safe "
        "value), require_bool or optional_bool, and pin it here with what an omitted field means.\n"
        f"  read, not pinned as read: {sorted(added.items())}\n  pinned, not read: {gone}"
    )


def test_no_body_boolean_is_read_by_truthiness(tree_scan):
    _, misreads, _ = tree_scan
    switches = {field for _, _, field in BODY_BOOLEANS} - _AMBIGUOUS
    wrong = sorted(
        row
        for row in set(misreads)
        if row[3] in {"bool()", "compare", "default"} or row[2] in switches
    )
    assert not wrong, (
        "A request body's boolean read by truthiness, where the text 'false' is a yes. Read it "
        "with request_validation.bool_field / require_bool / optional_bool:\n  "
        + "\n  ".join(f"{m}:{line} {fn} [{field}] {shape}" for m, fn, field, shape, line in wrong)
    )


#: Record loaders that still read a boolean by truthiness, and why each may: what they read is
#: written by the product itself, from real booleans. None of them is a switch the owner sets, or
#: an app's manifest (``apps/manifest.py`` reads every boolean through ``strict_bool`` and refuses
#: the install of one that is not true or false). A loader added to this set is a decision to
#: defend in review; one that leaves it must leave it here too.
LOADER_BUDGET = frozenset(
    {
        # Records the product writes from its own state.
        ("artifacts/models.py", "Artifact", "live_dirty"),
        ("artifacts/models.py", "Artifact", "readonly"),
        ("browse/plans.py", "BrowsePlan", "submits"),
        ("dashboard/side_state.py", "SideState", "open"),
        ("evals/ablation.py", "AblationComponent", "off_value"),
        ("evals/overlay.py", "ComponentOverlay", "off_value"),
        ("inbound/tokens.py", "SurfaceToken", "found"),
        ("learning/surfacing_events.py", "SurfacingEvent", "used"),
        ("memory_slots.py", "SlotLine", "tombstoned"),
        ("prompt_providers/base.py", "PromptVariable", "required"),
        ("proposals_contract.py", "Proposal", "editable"),
        ("selfqa/evidence.py", "Manifest", "passed"),
        ("tasks/models.py", "Task", "due_reminder"),
        ("tasks/models.py", "WorkflowTaskBinding", "managed"),
        ("triggers/models.py", "FireRecord", "acted_on"),
        ("triggers/models.py", "FireRecord", "dismissed"),
        ("triggers/models.py", "FireRecord", "incomplete"),
        ("triggers/models.py", "FireRecord", "mutated"),
        ("triggers/review.py", "ReviewCard", "count_is_floor"),
        ("triggers/review.py", "ReviewCard", "unannounced"),
        ("triggers/web_poll.py", "CheckRecord", "reported"),
        ("triggers/web_poll.py", "WatchState", "seeded"),
        ("workflows/human_input.py", "Ask", "rerun"),
        ("workflows/human_input.py", "Ask", "unattended_suppress"),
        ("workflows/human_input.py", "AskField", "required"),
        ("workflows/models.py", "DefMetadata", "a2a_published"),
        ("workflows/models.py", "DefMetadata", "guided"),
        ("workflows/models.py", "Failure", "recoverable"),
        ("workflows/models.py", "InputParam", "required"),
        ("workflows/models.py", "NodeInstance", "cached"),
        ("workflows/models.py", "WorkflowRun", "pinned"),
    }
)


#: Each module that reads an ``mcp.json`` server's ``disabled`` switch, and how many raw reads of it
#: it keeps: the toggle's disable path asks whether it is already the real ``true``, which is a
#: question about what to write, not a reading of the switch.
_MCP_SWITCH_READERS = {
    "mcp_client.py": 0,
    "mcp_discovery.py": 0,
    "agent.py": 0,
    "dashboard/handlers/mcp.py": 1,
    "providers/mcp_instances.py": 0,
}


def _calls_named(tree, name: str) -> list:
    import ast

    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, (ast.Name, ast.Attribute))
        and (n.func.id if isinstance(n.func, ast.Name) else n.func.attr) == name
    ]


@pytest.mark.parametrize("module, raw_reads", sorted(_MCP_SWITCH_READERS.items()))
def test_an_mcp_server_switch_is_read_one_way(module, raw_reads):
    """A server's ``disabled`` switch is read through ``mcp_status.switched_off``, which reads the
    words as written and keeps a server it cannot read off.

    Why a rail: a raw ``.get("disabled")`` reads the text ``"false"`` as on, and the reader that
    does it can sit in any of these modules behind paths a single request does not reach; the
    property is which call reads the switch, counted per module."""
    tree = source_index.tree(SRC / module)
    assert _calls_named(tree, "switched_off"), f"{module} no longer reads a server's switch"
    raw = [
        c
        for c in _calls_named(tree, "get")
        if c.args and getattr(c.args[0], "value", None) == "disabled"
    ]
    assert len(raw) == raw_reads, (
        f"{module} reads a server's `disabled` itself. Read it with mcp_status.switched_off, "
        "which reads the words as written and keeps a server it cannot read off."
    )


def test_a_record_loader_reads_no_switch_by_truthiness():
    """A stored switch is read through ``safety_flags.strict_bool`` with its safe value; only the
    records in :data:`LOADER_BUDGET` may still read a boolean another way."""
    found = {
        (path.relative_to(SRC).as_posix(), cls, field)
        for path, tree in source_index.package_trees()
        for cls, field in _loader_misreads(tree)
    }
    assert found, "the loader scan found nothing at all: it no longer sees a loader"
    assert found == LOADER_BUDGET, (
        "Read a stored switch with safety_flags.strict_bool and its safe value "
        "(`absent=` for one that is on unless switched off).\n"
        f"  new: {sorted(found - LOADER_BUDGET)}\n  gone (drop from the budget): "
        f"{sorted(LOADER_BUDGET - found)}"
    )

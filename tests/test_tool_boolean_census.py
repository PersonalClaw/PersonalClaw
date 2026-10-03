"""Every boolean a core tool declares is read as the word it spells, never by truthiness.

A model sends a declared boolean as text as often as not, and ``bool("false")`` is True. So
``edit_file`` sent ``"replace_all": "false"`` replaced every match, ``knowledge_update`` sent
``"is_archived": "false"`` archived the item, ``grep`` sent ``"regex": "false"`` searched for a
regular expression, ``code_map`` sent ``"refresh": "false"`` re-indexed the whole tree, and
``reset_tools`` switched on a group it was sent ``"false"`` for. The one reader is
``safety_flags.yes_or_no``: a real boolean, or a word that spells one; anything else spells neither,
and each field reads that as its own safe value.

This file is the census that keeps it so, over every tool a model can be offered from core and
every action the control bridge offers a client:

* **native** tools and bridge actions read their arguments themselves, so each boolean is read
  through ``yes_or_no`` where its module reads the call's arguments;
* **in-process** tools (``mcp_core`` and the modules its server aggregates) check each call
  against field specs first, and a ``bool`` spec refuses anything but a real boolean, text
  included; their handlers read what passes through ``yes_or_no`` all the same, so a spec taken
  away later cannot bring truthiness back;
* **consent** (``confirm``, ``confirm_cascade``) is the JSON literal ``true`` and nothing else
  (``safety_flags.confirm_granted``, held by ``test_confirm_gate_parity.py``).

A boolean a tool starts declaring fails :func:`test_every_boolean_a_tool_declares_is_pinned` until
it is pinned here, and a read of a pinned one by truthiness fails the ban below it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import personalclaw
from personalclaw import validation
from personalclaw.safety_flags import CONFIRM_FIELDS

SRC = Path(personalclaw.__file__).resolve().parent

#: Read where the module reads the call's arguments, through ``yes_or_no`` or a reader built on it
#: (``smart_case`` takes ``grep``/``glob``'s flags as the call gave them, and reads them so).
NATIVE: dict[tuple[str, str], str] = {
    ("edit_file", "replace_all"): "agents/native/builtin_tools.py",
    ("grep", "regex"): "agents/native/builtin_tools.py",
    ("grep", "ignore_case"): "agents/native/builtin_tools.py",
    ("glob", "ignore_case"): "agents/native/builtin_tools.py",
    ("knowledge_update", "is_pinned"): "agents/native/knowledge_tool_defs.py",
    ("knowledge_update", "is_archived"): "agents/native/knowledge_tool_defs.py",
    ("task_list_create", "repeatable"): "agents/native/builtin_tools.py",
    ("task_create", "exit_criteria[].met"): "tasks/models.py",
    ("task_update", "exit_criteria[].met"): "tasks/models.py",
    ("task_create", "action_plan[].completed"): "tasks/models.py",
    ("task_update", "action_plan[].completed"): "tasks/models.py",
    ("project_run_create", "attended"): "agents/native/sdlc_tools.py",
    ("code_map", "refresh"): "tool_providers/code_map.py",
    ("toggle_automation", "enabled"): "inbound/bridge.py",
    # One boolean per tool group, so it has no key to find; read by the test below.
    ("reset_tools", "groups.*"): "agents/native/runtime.py",
}

#: In-process tools: refused by their ``bool`` field spec unless a real boolean, then read through
#: ``yes_or_no`` by the module that handles them.
VALIDATED: dict[tuple[str, str], str] = {
    ("artifact_save", "force"): "mcp_artifacts.py",
    ("image_generate", "edit"): "mcp_artifacts.py",
    ("memory_recall", "deep"): "mcp_memory.py",
    ("notify", "unfurl_links"): "mcp_core.py",
    ("notify", "unfurl_media"): "mcp_core.py",
    ("notify", "reply_broadcast"): "mcp_core.py",
    ("automation_create", "catch_up"): "mcp_automation.py",
    ("workflow_rewind", "redo_effects"): "mcp_workflows.py",
    ("workflow_rewind", "force"): "mcp_workflows.py",
    # Not advertised: an agent's resume answers no gate, but one it sends is still validated.
    ("workflow_resume", "always_allow"): "mcp_workflows.py",
}

#: Destructive consent: the literal ``true`` only.
CONSENT: frozenset[tuple[str, str]] = frozenset(
    {
        ("automation_delete", "confirm"),
        ("automation_delete_all", "confirm"),
        ("workflow_edit", "confirm_cascade"),
        ("workflow_rewind", "confirm_cascade"),
        ("workflow_run_from", "confirm_cascade"),
    }
)

#: The maps an in-process dispatcher validates against (``validation.tool_field_schema``).
_FIELD_SPEC_MAPS = (
    validation.MCP_CORE_SCHEMAS,
    validation.MCP_WORKFLOW_SCHEMAS,
    validation.MCP_AUTOMATION_SCHEMAS,
)


def _booleans(schema: object, path: str = "") -> list[str]:
    """Each boolean a JSON schema declares, by its path (``exit_criteria[].met``)."""
    if not isinstance(schema, dict):
        return []
    found = [path] if schema.get("type") == "boolean" else []
    for name, child in (schema.get("properties") or {}).items():
        found += _booleans(child, f"{path}.{name}" if path else name)
    found += _booleans(schema.get("items"), f"{path}[]")
    for branch in schema.get("anyOf") or []:
        found += _booleans(branch, path)
    return found


async def _runtime(tmp_path: Path):
    """A started native runtime with tool groups, so it declares its group-switching tool."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

    class _Group(ToolProvider):
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        @property
        def display_name(self) -> str:
            return self._name

        async def list_tools(self) -> list[ToolDefinition]:
            return [
                ToolDefinition(
                    name=f"{self._name}_probe",
                    description="probe",
                    provider=self._name,
                    parameters={"type": "object", "properties": {}},
                    requires_approval=False,
                )
            ]

        async def invoke(self, tool_name: str, arguments: dict) -> ToolResult:
            return ToolResult(success=True, output="")

    class _Model:
        supports_tools = True
        _model = "scripted"

        async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
            return
            yield  # an async generator: this runtime is never asked to answer

    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(
            name="census", provider="native", model="m", tools=[], skills=[]
        ),
        model_provider=_Model(),  # type: ignore[arg-type]
        tool_providers=[_Group("personalclaw-filesystem"), _Group("personalclaw-schedule")],
        tool_groups=["schedule"],
        cwd=tmp_path,
    )
    await runtime.start()
    return runtime


def _meta_tools(runtime) -> list:
    """The tools the native runtime answers itself."""
    return [runtime._tool_search_def, runtime._tool_schema_def, runtime._reset_tools_def]


async def _declared(tmp_path: Path) -> set[tuple[str, str]]:
    """``(tool, path)`` for every boolean a core tool declares or validates as one."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.agents.native.tools import InProcessMcpToolProvider
    from personalclaw.inbound import bridge
    from personalclaw.mcp_core import _AGGREGATED_CATEGORY_MODULES
    from personalclaw.tool_providers.calendar_events import CalendarToolProvider
    from personalclaw.tool_providers.code_map import CodeMapToolProvider

    providers = [NativeBuiltinToolProvider(tmp_path), CodeMapToolProvider(), CalendarToolProvider()]
    providers += [
        InProcessMcpToolProvider(module=module, provider_name=module, display=module)
        for module in ("personalclaw.mcp_core", *_AGGREGATED_CATEGORY_MODULES)
    ]
    declared: set[tuple[str, str]] = set()
    for provider in providers:
        for tool in await provider.list_tools():
            declared |= {(tool.name, path) for path in _booleans(tool.parameters)}
    for tool in _meta_tools(await _runtime(tmp_path)):
        for path in _booleans(tool.parameters):
            # `reset_tools` declares one boolean per group the session has.
            head, _, _ = path.partition(".")
            declared.add((tool.name, f"{head}.*" if tool.name == "reset_tools" else path))
    for action in bridge.actions():
        declared |= {(action.name, path) for path in _booleans(action.params_schema)}
    for specs in _FIELD_SPEC_MAPS:
        for name, schema in specs.items():
            declared |= {(name, spec.name) for spec in schema.fields if spec.type is bool}
    return declared


@pytest.mark.asyncio
async def test_every_boolean_a_tool_declares_is_pinned(tmp_path):
    """A boolean a tool starts declaring (or validating as one) fails here until it is read as the
    word it spells, with its own safe value, and pinned in one of the tables above."""
    declared = await _declared(tmp_path)
    pinned = set(NATIVE) | set(VALIDATED) | set(CONSENT)
    assert declared == pinned, (
        "Read each new boolean through safety_flags.yes_or_no with its field's safe value, test "
        "it with 'false', 'no', '0', '' and 'true', and pin it here.\n"
        f"  declared, not pinned: {sorted(declared - pinned)}\n"
        f"  pinned, not declared: {sorted(pinned - declared)}"
    )


def test_consent_is_only_the_confirm_family():
    assert {field for _, field in CONSENT} <= CONFIRM_FIELDS


@pytest.mark.parametrize("tool, field", sorted(VALIDATED) + sorted(CONSENT))
@pytest.mark.parametrize("sent", ["false", "true", "", 0, 1])
def test_an_in_process_boolean_is_a_boolean_field_spec(tool, field, sent):
    """Its field spec is what keeps text from its handler, so each one has a ``bool`` spec, and
    the spec refuses anything that is not a real boolean."""
    schema = validation.tool_field_schema(tool)
    assert schema is not None, f"{tool} has no field specs"
    [spec] = [s for s in schema.fields if s.name == field]
    assert spec.type is bool
    with pytest.raises(validation.ValidationError) as refused:
        validation.validate_field(sent, spec)
    assert refused.value.field == field
    assert validation.validate_field(False, spec) is False


# ── The ban: no read of a pinned boolean by truthiness ─────────────────────────────────────────


def _constant_strings(node: ast.AST, module_tuples: dict[str, set[str]]) -> set[str]:
    """The strings a ``for`` loop iterates: a literal tuple or list of them, or a module-level
    name bound to one."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return {
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        }
    if isinstance(node, ast.Name):
        return module_tuples.get(node.id, set())
    return set()


def _misreads(source: str, key: str) -> tuple[int, list[str]]:
    """``(reads, misreads)`` of the argument *key* in *source*.

    A read is ``p.get("key")`` or ``p["key"]`` on a parameter ``p`` of the enclosing function, or
    ``p[k]`` where ``k`` is the variable of a loop over constant strings that include *key*; a name
    assigned straight from a read is a read too. A misread is a read that ``bool()`` takes, ``not``
    negates, an ``if``/``while``/ternary/comprehension tests, ``and``/``or`` joins, or that is
    compared by identity or equality with a boolean literal: every shape in which ``"false"`` is a
    yes (or ``"true"`` a no).
    """
    tree = ast.parse(source)
    module_tuples = {
        target.id: _constant_strings(node.value, {})
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    reads = 0
    bad: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)}
        keyed = {
            loop.target.id
            for loop in ast.walk(fn)
            if isinstance(loop, ast.For)
            and isinstance(loop.target, ast.Name)
            and key in _constant_strings(loop.iter, module_tuples)
        }

        def is_read(node: ast.AST) -> bool:
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner, args = node.func.value, node.args
                named = node.func.attr == "get" and len(args) > 0
            elif isinstance(node, ast.Subscript):
                owner, args, named = node.value, [node.slice], True
            else:
                return False
            if not (named and isinstance(owner, ast.Name) and owner.id in params):
                return False
            first = args[0]
            if isinstance(first, ast.Constant):
                return first.value == key
            return isinstance(first, ast.Name) and first.id in keyed

        aliases = {
            target.id
            for node in ast.walk(fn)
            if isinstance(node, (ast.Assign, ast.NamedExpr)) and is_read(node.value)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }

        def reads_key(node: ast.AST) -> bool:
            return is_read(node) or (
                isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in aliases
            )

        for node in ast.walk(fn):
            if is_read(node):
                reads += 1
            tested: list[ast.AST] = []
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "bool":
                    tested += node.args
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                tested.append(node.operand)
            elif isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
                tested.append(node.test)
            elif isinstance(node, ast.comprehension):
                tested += node.ifs
            elif isinstance(node, ast.BoolOp):
                tested += node.values
            elif isinstance(node, ast.Compare):
                sides = [node.left, *node.comparators]
                if any(isinstance(s, ast.Constant) and isinstance(s.value, bool) for s in sides):
                    tested += sides
            bad += [
                f"line {getattr(t, 'lineno', 0)}: {ast.unparse(t)}" for t in tested if reads_key(t)
            ]
    return reads, bad


_KEYED = sorted(
    (tool, path, module)
    for (tool, path), module in {**NATIVE, **VALIDATED}.items()
    if not path.endswith("*")
)


@pytest.mark.parametrize("tool, path, module", _KEYED)
def test_a_pinned_boolean_is_never_read_by_truthiness(tool, path, module):
    key = path.rsplit(".", 1)[-1]
    reads, misreads = _misreads((SRC / module).read_text(encoding="utf-8"), key)
    # Non-vacuity: the table names where the call's argument is read, so a rename or a move fails
    # here rather than leaving a ban that scans nothing.
    assert reads, f"{module} no longer reads {key!r} from a call's arguments: re-pin {tool}.{path}"
    assert not misreads, (
        f"{module} reads {tool}'s {key!r} by truthiness, where 'false' is a yes. Read it with "
        "safety_flags.yes_or_no and the field's safe value:\n  " + "\n  ".join(misreads)
    )


@pytest.mark.parametrize(
    "shape",
    [
        "def f(a):\n    return bool(a.get('flag'))",
        "def f(a):\n    if a.get('flag'):\n        return 1",
        "def f(a):\n    return a.get('flag') is not False",
        "def f(a):\n    return a['flag'] is True",
        "def f(a):\n    return a.get('flag') == True",
        "def f(a):\n    return not a.get('flag')",
        "def f(a):\n    return a.get('flag') or a.get('other')",
        "def f(a):\n    return 1 if a.get('flag') else 0",
        "def f(a):\n    x = a.get('flag')\n    return bool(x)",
        "def f(a):\n    for k in ('flag', 'other'):\n        if a[k]:\n            return 1",
        "KEYS = ('flag',)\ndef f(a):\n    for k in KEYS:\n        return 1 if a[k] else 0",
        "def f(a):\n    return [k for k in a if a.get('flag')]",
    ],
)
def test_the_ban_sees_every_shape_of_a_misread(shape):
    """The ban's own control: each shape in which ``"false"`` reads as a yes is caught."""
    reads, misreads = _misreads(shape, "flag")
    assert reads and misreads, shape


@pytest.mark.parametrize(
    "shape",
    [
        "def f(a):\n    return yes_or_no(a.get('flag')) is True",
        "def f(a):\n    return yes_or_no(a['flag']) is not False",
        "def f(a):\n    x = a.get('flag')\n    return x is None or yes_or_no(x) is True",
        "def f(a):\n    return send(flag=a.get('flag'))",
        "def f(a):\n    item = load()\n    return bool(item.get('flag'))",
    ],
)
def test_the_ban_passes_the_reader_a_pass_through_and_another_dict(shape):
    """A read through the reader, a value handed on to its own reader, and a key on something
    other than the call's arguments are not misreads."""
    _, misreads = _misreads(shape, "flag")
    assert not misreads, shape


# ── `reset_tools`: one boolean per tool group ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", [False, "false", "no", "0", "", "off", "maybe", 1])
async def test_a_group_sent_anything_but_a_yes_stays_off(tmp_path, sent):
    runtime = await _runtime(tmp_path)
    runtime._reset_tools({"groups": {"schedule": sent}})
    assert "schedule" not in runtime._active_groups


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", [True, "true", "yes", " TRUE ", "on", "1"])
async def test_a_group_sent_a_yes_is_on(tmp_path, sent):
    runtime = await _runtime(tmp_path)
    runtime._reset_tools({"groups": {"schedule": sent}})
    assert "schedule" in runtime._active_groups

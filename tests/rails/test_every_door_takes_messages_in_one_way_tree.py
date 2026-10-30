"""Every door that puts a message into a chat takes it in one way, ``chat_intake.take_in``.

A message gets its id, its receipt and its one run there: a repeat of it runs nothing, and a steer
names its turn (``dashboard/chat_intake.py``). A door that writes the row a turn starts with itself,
or queues or steers a message itself, gives it none of that, so this rail fails on, anywhere in
the package but the intake and the code it hands the message to:

- an ``append(...)`` whose role is one a turn starts with (``turn_source.TURN_STARTS``: ``user``,
  ``inject``, ``subagent``, ``nudge``), given as a literal or as either arm of a conditional, in
  the first argument or as ``role=``;
- a ``queue_append(...)`` or an ``add_steer(...)``.

The intake writes those (``chat_intake.py``), and so do what it hands a message to: the chat's
queue (``chat_queue.py``), the steer it sends into the running turn and the row it becomes when the
turn takes it (``running_turn.py``), and the side conversation, which keeps a buffer of its own and
is no door into the chat (``side.py``).

A role given as a value is not a literal to read, and is the easy way past the first rule: ``role
= "user"`` a line above, or a helper that takes the role as a parameter. So every ``append`` that
writes a row with a value for its role (one given more than one argument, a ``role=``, or its
arguments unpacked) is a site, ``path::function::<the call>``, and each is listed in
:data:`BASELINE` with why it is no door: most write a row read back from a chat's file with the
role it has there, and some are not a chat's row at all (a log keyed by its conversation, a
model's cache). A new one fails by name until someone judges it, and the list only shrinks.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path

import site_baseline
import source_index

from personalclaw.turn_source import TURN_STARTS

#: Where a message is taken in, and what it is handed to.
INTAKE = frozenset(
    {
        "src/personalclaw/dashboard/chat_intake.py",
        "src/personalclaw/dashboard/chat_queue.py",
        "src/personalclaw/dashboard/running_turn.py",
        "src/personalclaw/dashboard/side.py",
    }
)
#: The calls that put a message in the queue or into the running turn.
QUEUE_AND_STEER = ("queue_append", "add_steer")
#: The appends outside the intake that write a row with a value for its role, each judged.
BASELINE = "tests/rails/baselines/rows-whose-role-is-a-value.sites.jsonl"

Files = Iterable[tuple[str, ast.Module]]


def _roles(node: ast.expr) -> set[str]:
    """The literal roles *node* can be: a string, or either arm of a conditional of them."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _roles(node.body) | _roles(node.orelse)
    return set()


def _a_value(node: ast.expr) -> bool:
    """*node* can be a role that is not a literal string."""
    if isinstance(node, ast.IfExp):
        return _a_value(node.body) or _a_value(node.orelse)
    return not (isinstance(node, ast.Constant) and isinstance(node.value, str))


def _role_given(node: ast.Call) -> list[ast.expr]:
    """What an ``append`` is given as a row's role: its first argument, and ``role=``."""
    return [*node.args[:1], *(k.value for k in node.keywords if k.arg == "role")]


def _writes_a_row_with_a_value(node: ast.Call) -> bool:
    """An ``append`` given a role and more (not a list's ``append(item)``), or its arguments
    unpacked, whose role can be a value."""
    unpacked = any(isinstance(a, ast.Starred) for a in node.args) or any(
        k.arg is None for k in node.keywords
    )
    with_a_role = len(node.args) > 1 or any(k.arg == "role" for k in node.keywords)
    return unpacked or (with_a_role and any(_a_value(g) for g in _role_given(node)))


def _calls(files: Files) -> Iterator[tuple[str, str, ast.Call, str]]:
    """Each method call outside the intake: its file, function, node and name."""
    for rel, tree in files:
        if rel in INTAKE:
            continue
        for node, scope in site_baseline.scoped(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                yield rel, scope, node, node.func.attr


def _findings(files: Files) -> list[str]:
    found: list[str] = []
    for rel, _scope, node, name in _calls(files):
        if name in QUEUE_AND_STEER:
            found.append(f"{rel}:{node.lineno}: {name}(...)")
        elif name == "append":
            starts = sorted(set().union(*map(_roles, _role_given(node))) & TURN_STARTS)
            if starts:
                found.append(f"{rel}:{node.lineno}: append({', '.join(starts)}, ...)")
    return found


def _valued(files: Files) -> list[str]:
    """The sites of the appends outside the intake that write a row with a value for its role."""
    return site_baseline.numbered(
        site_baseline.code_site(rel, scope, node)
        for rel, scope, node, name in _calls(files)
        if name == "append" and _writes_a_row_with_a_value(node)
    )


def _package() -> list[tuple[str, ast.Module]]:
    trees = source_index.package_trees()
    assert len(trees) > 500, f"walked {len(trees)} modules — vacuous"
    return [(source_index.rel(Path(path)), tree) for path, tree in trees]


def test_no_door_but_the_intake_puts_a_message_into_a_chat() -> None:
    assert _findings(_package()) == []


def test_every_row_written_with_a_value_for_its_role_is_judged() -> None:
    site_baseline.check(_valued(_package()), BASELINE)


def test_every_module_is_walked_and_only_the_intake_is_passed_over() -> None:
    """Zero skipped: the rail reads every module of the package, and the four it passes over are
    there to be passed over."""
    rels = {rel for rel, _ in _package()}
    assert len(rels) == len(source_index.package_files())
    assert INTAKE <= rels, sorted(INTAKE - rels)


def test_each_way_past_the_intake_is_caught_and_nothing_else_is() -> None:
    """The positive control: each planted door is found, beside calls that only look alike."""
    planted = ast.parse(
        "session.append('user', text, 'msg msg-u')\n"
        "session.append(\n    'subagent',\n    report,\n)\n"
        "chat.append('subagent' if report else 'inject', text)\n"
        "chat.append(role='nudge', content=text)\n"
        "session.queue_append(text)\n"
        "state.sessions.add_steer(key, text)\n"
        "session.append('assistant', text)\n"
        "session.append(row['role'], row['content'])\n"
        "rows.append('user')\n"
        "session.append('notice', text)\n"
    )
    found = _findings([("src/personalclaw/planted.py", planted)])
    lines = sorted(int(f.split(": ", 1)[0].rsplit(":", 1)[1]) for f in found)
    # `rows.append('user')` is a list of words, but its literal is a turn's role: caught, and
    # renamed or moved by whoever writes one.
    assert lines == [1, 2, 6, 7, 8, 9, 12], found
    for rel in INTAKE:
        assert _findings([(rel, planted)]) == [], f"{rel} is where a message is taken in"


#: Where a chat's file is read back, each row with the role it has there.
_CHAT_FILE_READ_BACK = "src/personalclaw/dashboard/chat_persistence.py"

_PLANTED_VALUES = """
def take_report(session, text):
    role = "user"
    session.append(role, text, "msg msg-u")


def write(session, role, text):
    session.append(role, text)


def mirror(chat, row, rows):
    chat.append(row["role"], row["content"])
    chat.append(*row)
    chat.append(**row)
    chat.append(role=row["role"], content=row["content"])
    chat.append("assistant" if row else row["role"], row["content"])
    chat.append("assistant", row["content"])
    rows.append(row)
"""


def test_a_role_given_as_a_value_is_a_site_and_a_literal_is_not() -> None:
    found = _valued([("planted.py", ast.parse(_PLANTED_VALUES))])
    assert found == [
        "planted.py::take_report::session.append(role, text, 'msg msg-u')",
        "planted.py::write::session.append(role, text)",
        "planted.py::mirror::chat.append(row['role'], row['content'])",
        "planted.py::mirror::chat.append(*row)",
        "planted.py::mirror::chat.append(**row)",
        "planted.py::mirror::chat.append(role=row['role'], content=row['content'])",
        "planted.py::mirror::chat.append('assistant' if row else row['role'], row['content'])",
    ], found
    for rel in INTAKE:
        assert _valued([(rel, ast.parse(_PLANTED_VALUES))]) == [], rel


def test_a_new_door_with_its_role_in_a_variable_fails_by_name_and_a_read_back_does_not() -> None:
    """``role = "user"`` in a new module fails the baseline, named; the chat's file read back,
    which writes each row with the role it has there, is listed and passes."""
    package = _valued(_package())
    read_back = [s for s in package if s.startswith(f"{_CHAT_FILE_READ_BACK}::")]
    assert read_back, "the chat's file read back is no longer a site: the plant below is vacuous"
    planted = _valued([("src/personalclaw/planted.py", ast.parse(_PLANTED_VALUES))])[0]
    try:
        site_baseline.check([*package, planted], BASELINE, None, prune=False)
    except AssertionError as exc:
        assert planted in str(exc), exc
        assert not any(site in str(exc) for site in read_back), exc
    else:
        raise AssertionError("a new door with its role in a variable passed")

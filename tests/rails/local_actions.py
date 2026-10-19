"""A workflow job's steps with each local composite action it calls read in place.

A step ``uses: ./<path>`` runs the composite action at ``<path>/action.yml`` (or
``action.yaml``) inside the job. The rails that read a job's setup steps (its Python, uv, Node,
cache) read them through here, so a step moved into an action is still held. Each action step
is read as GitHub runs it for that call:

- ``${{ inputs.<name> }}`` in its ``with:``, ``env:``, ``run:`` or ``working-directory:``
  becomes the call's input, or the action's default for it. Input names ignore case, as
  GitHub's do.
- It runs with the call's ``env:`` beneath its own.
- An ``if:`` of the form ``inputs.<name> == '<literal>'`` or ``!= '<literal>'``, bare or as the
  whole of one ``${{ }}``, drops the step when it is false for this call. The comparison ignores
  case, as GitHub's does. Text outside the ``${{ }}`` (a block scalar's newline, a space) makes
  the condition a string GitHub fills in, which is never empty and so always true. Any other
  condition keeps the step, a ``${{`` or ``}}`` inside the expression among them, and so does an
  input that is itself an expression, which only the runner evaluates, or a YAML boolean, whose
  word YAML 1.1 does not keep: a step this reader cannot rule out is held.
- A local action it calls is read in place the same way.

A call of a local action the reader was not given raises :class:`UnreadAction`: a rail that
cannot read an action's steps fails rather than passing without them.
"""

from __future__ import annotations

import functools
import posixpath
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

#: Where local composite actions live. ``uses:`` names one by its folder, from the root.
HOME = ".github/actions"
#: The file names GitHub reads a local action from, the first one it finds first.
ACTION_FILES = ("action.yml", "action.yaml")
_INPUT = re.compile(r"\$\{\{\s*inputs\.([A-Za-z_][\w-]*)\s*\}\}")
#: An ``if:`` that is one ``${{ }}`` and nothing else, the only text GitHub reads as the
#: expression inside it rather than as a string to fill in, with no ``${{`` or ``}}`` inside.
_ONE_EXPRESSION = re.compile(r"\$\{\{((?:(?!\$\{\{|\}\}).)*)\}\}", re.S)
#: The one condition the reader evaluates: an input compared to a literal.
_CONDITION = re.compile(r"\s*inputs\.([A-Za-z_][\w-]*)\s*(==|!=)\s*'([^']*)'\s*")
#: The step keys whose text may name an input.
_FILLED = ("run", "working-directory")


class UnreadAction(LookupError):
    """A step calls a local action whose file the reader was not given."""


def action_paths(root: Path) -> list[Path]:
    """Every local action file under :data:`HOME` in *root*, at any depth."""
    return sorted(p for name in ACTION_FILES for p in (root / HOME).glob(f"**/{name}"))


def actions_at(root: Path) -> dict[str, dict[str, Any]]:
    """Every local action in the checkout at *root*, as :func:`action_files` reads them."""
    return action_files(
        {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in action_paths(root)}
    )


@functools.lru_cache(maxsize=64)
def _parsed(text: str) -> dict[str, Any]:
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def _name(path: str) -> str:
    """A local action's folder as a ``uses:`` value names it: ``./`` and the folder."""
    return f"./{posixpath.normpath(path)}"


def action_files(tree: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Every local action in *tree* (rel path -> text), parsed, by the name `uses:` gives it.
    A text is parsed once however often it is asked for; callers only read the result."""
    found: dict[str, dict[str, Any]] = {}
    for file_name in reversed(ACTION_FILES):  # action.yml, read last, wins over action.yaml
        for rel, text in tree.items():
            folder, _, base = rel.rpartition("/")
            if base == file_name:
                found[_name(folder or ".")] = _parsed(text)
    return found


def _text(value: object) -> str:
    """A ``with:`` value or an input default as text: YAML's true is "true". YAML 1.1 reads
    ``yes``, ``no``, ``on`` and ``off`` as booleans too, where GitHub hands on the word, so
    :func:`_runs` holds a step whose condition reads a boolean."""
    if value is None:
        return ""
    return str(value).lower() if isinstance(value, bool) else str(value)


def _inputs(action: dict[str, Any], call: dict[str, Any]) -> dict[str, object]:
    """Each input by its name in lower case: the call's value, else the action's default, as
    YAML read it."""
    declared = action.get("inputs") or {}
    given = call.get("with") or {}
    values: dict[str, object] = {
        str(name).lower(): spec.get("default") if isinstance(spec, dict) else None
        for name, spec in (declared.items() if isinstance(declared, dict) else [])
    }
    if isinstance(given, dict):
        values.update({str(k).lower(): v for k, v in given.items()})
    return values


def _runs(condition: object, inputs: Mapping[str, object]) -> bool:
    """Whether a step with *condition* may run for the call: false only for an input compared
    to a literal, which GitHub evaluates false for these *inputs*."""
    if condition is None:
        return True
    text = str(condition)
    if "${{" in text:
        whole = _ONE_EXPRESSION.fullmatch(text)
        if not whole:
            # Text around the ${{ }}, a string GitHub fills in and so always runs, or a ${{ or
            # }} inside it: hold the step.
            return True
        text = whole.group(1)
    match = _CONDITION.fullmatch(text)
    if not match:
        return True  # a condition this reader cannot evaluate: hold the step
    raw = inputs.get(match.group(1).lower())
    if isinstance(raw, bool):
        return True  # a YAML boolean: GitHub hands on the word written, which YAML 1.1 lost
    value, literal = _text(raw), match.group(3)
    if "${{" in value or not (value.isascii() and literal.isascii()):
        return True  # an input only the runner evaluates, or text it may fold otherwise: hold it
    return (value.lower() == literal.lower()) == (match.group(2) == "==")


def _filled(value: object, inputs: Mapping[str, object]) -> object:
    if isinstance(value, str):
        return _INPUT.sub(lambda m: _text(inputs.get(m.group(1).lower())), value)
    return value


def _read(inner: dict[str, Any], inputs: Mapping[str, object], env: object) -> dict[str, Any]:
    """One action step as one call runs it: its inputs filled in, the call's *env* beneath its
    own."""
    read = dict(inner)
    for key in ("with", "env"):
        given = inner.get(key)
        if isinstance(given, dict):
            read[key] = {k: _filled(v, inputs) for k, v in given.items()}
    for key in _FILLED:
        if key in inner:
            read[key] = _filled(inner[key], inputs)
    own = read.get("env")
    if isinstance(env, dict) and env and (own is None or isinstance(own, dict)):
        read["env"] = {**env, **(own or {})}
    return read


def _called(
    step: dict[str, Any], actions: Mapping[str, dict[str, Any]], seen: tuple[str, ...]
) -> Iterator[dict[str, Any]]:
    """The steps a call of a local action runs, in order; nothing for any other step."""
    uses = str(step.get("uses", ""))
    if not uses.startswith("./"):
        return
    name = _name(uses)
    action = actions.get(name)
    if action is None:
        raise UnreadAction(f"`uses: {uses}` calls a local action this reader was not given")
    if name in seen:
        raise UnreadAction(f"`uses: {uses}` calls itself through {' -> '.join(seen)}")
    runs = action.get("runs") or {}
    inputs = _inputs(action, step)
    for inner in (runs.get("steps") if isinstance(runs, dict) else None) or []:
        if not isinstance(inner, dict) or not _runs(inner.get("if"), inputs):
            continue
        read = _read(inner, inputs, step.get("env"))
        yield read
        yield from _called(read, actions, (*seen, name))


def expand(
    steps: list[Any], actions: Mapping[str, dict[str, Any]]
) -> Iterator[tuple[int, dict[str, Any]]]:
    """``(index, step)`` for each step a job runs: its own, and in place of a local action's call,
    that call followed by each action step the call runs, all at the call's index."""
    for index, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        yield index, step
        for read in _called(step, actions, ()):
            yield index, read


def called(steps: list[Any], actions: Mapping[str, dict[str, Any]]) -> list[str]:
    """The folder, from the root, of each local action *steps* call, one that another action
    calls included: the files that define those steps beside the workflow's own."""
    return sorted(
        {
            posixpath.normpath(str(step["uses"]))
            for _index, step in expand(steps, actions)
            if str(step.get("uses", "")).startswith("./")
        }
    )

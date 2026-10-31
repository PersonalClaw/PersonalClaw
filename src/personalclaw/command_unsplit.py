"""Where a shell line the reader cannot split reaches the network (#5727).

:func:`~personalclaw.shell_syntax.lex` declines a line with a subshell, a group, a substitution or
a background ``&``, and :mod:`personalclaw.command_effects` establishes nothing about such a line
but a delete it may hide. Where it reaches the network is read here, in its parts. That module
reads each part and passes its reader in, with what its effects are made of, so this one imports
nothing from it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from personalclaw.shell_syntax import Word, lex, parts, simple_commands

if TYPE_CHECKING:
    from personalclaw.command_effects import CommandEffects


def unsplit_network(
    command: str,
    depth: int,
    *,
    read: Callable[[str, int], CommandEffects],
    assignments: Callable[[Sequence[Word]], tuple[int, bool]],
    nothing: CommandEffects,
    held: CommandEffects,
    max_depth: int,
) -> CommandEffects:
    """Where a line this reader cannot split reaches the network: each of its parts
    (:func:`~personalclaw.shell_syntax.parts`: the line with its subshells, groups and
    substitutions taken out, and each of those) read as a command of its own. A part that still
    cannot be read, or one that runs a program it cannot name (``$(which curl) …``), reaches a host
    it does not name, and so does a line that cannot be cut into parts: what is not read is held,
    never let through (#5727).

    *read* reads a part at a depth, *assignments* counts a command's leading assignments, *nothing*
    is the effects of a command that does nothing, *held* those of one that reaches a host it does
    not name, and a line is cut only above *max_depth*."""
    pieces = parts(command) if depth < max_depth else None
    if not pieces or pieces == [command]:
        return held
    found = nothing
    for piece in pieces:
        if not piece.strip():
            continue
        effects = read(piece, depth + 1)
        if _runs_unnamed_program(piece, assignments):
            effects = effects | held
        found = found | replace(
            nothing, network=effects.network, hosts=effects.hosts, host_unread=effects.host_unread
        )
    return found


def _runs_unnamed_program(
    command: str, assignments: Callable[[Sequence[Word]], tuple[int, bool]]
) -> bool:
    """Whether a command in *command* runs a program whose name its text does not say: one named
    by an expansion, or by what a substitution prints."""
    tokens = lex(command)
    commands = simple_commands(tokens) if tokens is not None else None
    for one in commands or ():
        k, _relays = assignments(one.words)
        if k < len(one.words) and one.words[k].opaque:
            return True
    return False

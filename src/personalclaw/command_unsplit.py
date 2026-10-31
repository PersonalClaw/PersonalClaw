"""Where a shell line the reader cannot split reaches, and what it writes (#5727, #5730).

:func:`~personalclaw.shell_syntax.lex` declines a line with a subshell, a group, a substitution or
a background ``&``, and :mod:`personalclaw.command_effects` establishes nothing about such a line
but a delete it may hide. Where it reaches the network and what it writes are read here, in its
parts. That module reads each part and passes its reader in, with what its effects are made of, so
this one imports nothing from it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from personalclaw.shell_syntax import Simple, Word, lex, parts, simple_commands

if TYPE_CHECKING:
    from personalclaw.command_effects import CommandEffects


def unsplit_effects(
    command: str,
    depth: int,
    *,
    read: Callable[[str, int], CommandEffects],
    assignments: Callable[[Sequence[Word]], tuple[int, bool]],
    moves: frozenset[str],
    runs_code: Callable[[Simple], bool],
    changes_environment: Callable[[Simple], bool],
    under: Callable[..., CommandEffects],
    relayed: Callable[[CommandEffects], CommandEffects],
    nothing: CommandEffects,
    held: CommandEffects,
    writes: CommandEffects,
    max_depth: int,
) -> CommandEffects:
    """Where a line this reader cannot split reaches the network, and what it writes: each of its
    parts (:func:`~personalclaw.shell_syntax.parts`: the line with its subshells, groups and
    substitutions taken out, and each of those) read as a command of its own. A part that still
    cannot be read, or one that runs a program it cannot name (``$(which curl) …``), reaches a host
    and writes a path it does not name, and so does a line that cannot be cut into parts: what is
    not read is held, never let through.

    Each part places the paths it writes by its own reading. A part that moves the shell or runs
    code in it (``cd``, ``eval``) may have moved the others, which ``parts`` does not place (a
    group's ``cd`` moves the line after it), so a relative path in the others names no place; and
    one that changes the shell's environment may send what the others write elsewhere
    (:func:`_changes_shell`).

    *read* reads a part at a depth; *assignments*, *moves*, *runs_code* and *changes_environment*
    are what that reading says of a command's leading assignments and of what it does to the shell,
    and *under* and *relayed* what it makes of a part's effects once the folder it runs in, or where
    its programs reach, is not known. *nothing* is the effects of a command that does nothing,
    *held* those of one that reaches a host it does not name, *writes* those of one that writes a
    path it does not name, and a line is cut only above *max_depth*."""
    pieces = parts(command) if depth < max_depth else None
    if not pieces or pieces == [command]:
        return held | writes
    shell = [
        _changes_shell(
            piece,
            depth,
            moves=moves,
            runs_code=runs_code,
            changes_environment=changes_environment,
            max_depth=max_depth,
        )
        for piece in pieces
    ]
    found = nothing
    for k, piece in enumerate(pieces):
        if not piece.strip():
            continue
        effects = read(piece, depth + 1)
        if _runs_unnamed_program(piece, assignments):
            effects = effects | held | writes
        others = shell[:k] + shell[k + 1 :]
        written = under(effects, "", lost=True) if any(moved for moved, _ in others) else effects
        if any(relays for _, relays in others):
            written = relayed(written)
        found = found | replace(
            nothing,
            network=effects.network,
            hosts=effects.hosts,
            host_unread=effects.host_unread,
            writes=written.writes,
            targets=written.targets,
            target_unread=written.target_unread,
        )
    return found


def _changes_shell(
    command: str,
    depth: int,
    *,
    moves: frozenset[str],
    runs_code: Callable[[Simple], bool],
    changes_environment: Callable[[Simple], bool],
    max_depth: int,
) -> tuple[bool, bool]:
    """Whether *command*, anywhere in it, moves the shell or runs code in it, and whether it changes
    the shell's environment or runs code: what a part of a line may do to the line's other parts.
    One this reading cannot read does both."""
    tokens = lex(command)
    commands = simple_commands(tokens) if tokens is not None else None
    if tokens is not None and commands is None:
        return True, True  # an incomplete command
    if commands is None:
        pieces = parts(command) if depth < max_depth else None
        if not pieces or pieces == [command]:
            return True, True
        found = [
            _changes_shell(
                piece,
                depth + 1,
                moves=moves,
                runs_code=runs_code,
                changes_environment=changes_environment,
                max_depth=max_depth,
            )
            for piece in pieces
        ]
        return any(moved for moved, _ in found), any(relays for _, relays in found)
    moved = relays = False
    for one in commands:
        name = one.words[0].text if one.words else ""
        runs = runs_code(one)
        moved = moved or name in moves or runs
        relays = relays or runs or changes_environment(one)
    return moved, relays


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

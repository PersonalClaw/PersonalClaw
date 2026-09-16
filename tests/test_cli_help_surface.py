"""``--help`` is a product surface: no argparse internal may ever render on it (#2904).

The bug this file exists to prevent: ``sub.add_parser("mcp-core", help=argparse.SUPPRESS)``
printed ``mcp-core            ==SUPPRESS==`` in ``personalclaw --help`` and left the command
listed in the ``{chat,run,…}`` metavar. argparse honours ``SUPPRESS`` for ordinary arguments
only — for a subparser choice it stores the sentinel *as the help text*. So the intent
("hide this") produced the exact opposite (a sentinel string, on the first surface a CLI user
reads, next to a command that was still advertised).

🔑 THE RAIL IS THE DELIVERABLE, NOT THE ROW. A test that only asserted ``mcp-core`` is hidden
would pass forever while the next hidden command repeated the same mistake. So every guard
here walks the WHOLE parser tree (one top-level parser + ~106 subparsers) and holds every
subcommand to the binary standard: **documented, or genuinely hidden — never neither.**

Each guard carries its own anti-vacuity assertion, because every one of them would pass
trivially against a walk that visited nothing or a detector that matches nothing:

* the walk is asserted to reach the real command count, and to render real help text;
* the sentinel detector is calibrated against a deliberately-broken parser (it MUST fire) as
  well as against the shipped one (it must NOT);
* the hidden-command set is asserted non-empty and asserted to name real, live commands.
"""

from __future__ import annotations

import argparse
import re

import pytest

from personalclaw.cli import (
    HIDDEN_COMMANDS,
    _add_hidden_parser,
    _hide_internal_commands,
    build_parser,
)

#: An argparse internal sentinel's SHAPE, not just today's one value. ``argparse.SUPPRESS``
#: is ``"==SUPPRESS=="``; anything else the module ever ships under the same convention is
#: caught by the same pattern. Calibrated: zero matches across the shipped tree's help
#: (see :func:`test_the_sentinel_detector_is_not_vacuous`), one match against a parser
#: built the broken way.
_SENTINEL = re.compile(r"==[A-Z_]+==")

#: A floor on the walk, not the exact count — this must not become a churn test every time a
#: subcommand is added. It is well under the ~107 parsers the tree renders today and well
#: over anything a broken walk would reach.
_MIN_PARSERS_WALKED = 60


def _walk(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()):
    """Yield ``(command path, parser)`` for ``parser`` and every subparser beneath it.

    ``_actions`` / ``_SubParsersAction`` are argparse privates; there is no public accessor
    for a parser's children, and argparse's own formatter reaches for the same names. The
    alternative — shelling out ``personalclaw <cmd> --help`` a hundred times — would import
    torch per invocation and could not be a gate.
    """
    here = path or ("personalclaw",)
    yield here, parser
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        # Aliases map several names onto ONE parser object; walking it once per alias would
        # inflate the count the anti-vacuity floor reads.
        seen: set[int] = set()
        for name, child in action.choices.items():
            if id(child) in seen:
                continue
            seen.add(id(child))
            yield from _walk(child, here + (name,))


def _rendered(parser: argparse.ArgumentParser) -> str:
    """Everything a user can make this parser print: its help body and its usage line."""
    return parser.format_help() + "\n" + parser.format_usage()


@pytest.fixture(scope="module")
def tree() -> list[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    """The real shipped parser tree, flattened once.

    ``build_parser`` exists precisely so this is possible without side effects — ``main``
    loads ``.env`` files and touches ``PERSONALCLAW_HOME`` before it parses anything.
    """
    return list(_walk(build_parser()))


def _subcommand_actions(parser: argparse.ArgumentParser) -> list[argparse._SubParsersAction]:
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]


# ── The leak itself, over the whole surface ──────────────────────────────────────


def test_no_argparse_sentinel_renders_anywhere_in_the_help_tree(tree):
    """#2904's actual defect, generalized to every parser the CLI can print."""
    leaks = []
    for path, parser in tree:
        text = _rendered(parser)
        for hit in sorted(set(_SENTINEL.findall(text))):
            leaks.append((" ".join(path), hit))
        if argparse.SUPPRESS in text:  # the specific value, stated separately on purpose
            leaks.append((" ".join(path), argparse.SUPPRESS))

    assert not leaks, (
        "an argparse internal sentinel reached user-facing help: "
        + "; ".join(f"`{where} --help` shows {what!r}" for where, what in leaks)
        + ". `help=argparse.SUPPRESS` does not hide a SUBCOMMAND — register it with "
        "cli._add_hidden_parser and declare it in cli.HIDDEN_COMMANDS instead."
    )

    # Anti-vacuity: the loop above passes trivially if the walk found nothing, or if
    # format_help() returned empty strings.
    assert len(tree) >= _MIN_PARSERS_WALKED, (
        f"the walk reached only {len(tree)} parsers — it is not covering the CLI, so a "
        f"clean result proves nothing"
    )
    top = _rendered(tree[0][1])
    assert "Chat with the agent" in top, "the top-level help rendered no command descriptions"


def test_the_sentinel_detector_is_not_vacuous():
    """The detector must FIRE on a parser built the way #2904 was built.

    Without this, a typo in ``_SENTINEL`` would make every guard above green forever. This
    also pins the stdlib behaviour the fix is built on: argparse renders the sentinel as the
    choice's help text and still lists the choice.
    """
    broken = argparse.ArgumentParser(prog="demo")
    broken_sub = broken.add_subparsers()
    broken_sub.add_parser("visible", help="a visible one")
    broken_sub.add_parser("hidden", help=argparse.SUPPRESS)

    text = _rendered(broken)
    assert _SENTINEL.search(text), "the sentinel pattern no longer matches argparse.SUPPRESS"
    assert argparse.SUPPRESS in text, "argparse stopped leaking SUPPRESS — re-derive this rail"
    assert "hidden" in text, "argparse stopped listing a SUPPRESS-marked choice in the metavar"


# ── The standard every subcommand is held to ─────────────────────────────────────


def test_every_subcommand_is_either_documented_or_declared_hidden(tree):
    """The other half of #2904: dropping ``help=`` alone yields a SILENTLY undocumented row.

    A command with no help text is not hidden — it still shows in the ``{…}`` metavar with no
    explanation. So "no help row" is only ever legitimate for a command that also opted into
    :data:`HIDDEN_COMMANDS`.
    """
    undocumented = []
    documented_count = 0
    for path, parser in tree:
        for action in _subcommand_actions(parser):
            has_row = {row.dest for row in action._get_subactions()}
            documented_count += len(has_row)
            for name in action.choices:
                if name not in has_row and name not in HIDDEN_COMMANDS:
                    undocumented.append(" ".join(path + (name,)))

    assert not undocumented, (
        f"these subcommands carry no help text and are not declared hidden: {undocumented}. "
        f"Give each one a help= string, or register it via cli._add_hidden_parser."
    )
    # Anti-vacuity: an empty `choices` everywhere would also produce an empty list above.
    assert documented_count >= _MIN_PARSERS_WALKED, (
        f"only {documented_count} documented subcommands were seen — the census is not "
        f"reading the parser tree"
    )


def test_every_hidden_command_is_absent_from_every_rendered_help(tree):
    """Hidden means hidden on all three surfaces: usage line, choices metavar, command list."""
    assert HIDDEN_COMMANDS, "HIDDEN_COMMANDS is empty — this guard would assert nothing"

    exposed = []
    for path, parser in tree:
        # `personalclaw mcp-core --help` legitimately prints its own prog name.
        if set(path) & HIDDEN_COMMANDS:
            continue
        text = _rendered(parser)
        for name in HIDDEN_COMMANDS:
            if name in text:
                exposed.append((" ".join(path), name))

    assert not exposed, "a HIDDEN_COMMANDS entry is still advertised: " + "; ".join(
        f"`{where} --help` mentions {name!r}" for where, name in exposed
    )


def test_every_hidden_command_is_still_dispatchable(tree):
    """Hiding a command must not un-register it — that would be a regression, not a fix.

    ``mcp-core`` is how an ACP CLI spawns the stdio MCP server; a hidden command that no
    longer parses is a broken integration dressed up as a tidy ``--help``.
    """
    parser = tree[0][1]
    live = {name for a in _subcommand_actions(parser) for name in a.choices}
    missing = sorted(HIDDEN_COMMANDS - live)
    assert not missing, f"HIDDEN_COMMANDS names commands that are not registered: {missing}"

    for name in sorted(HIDDEN_COMMANDS):
        args = parser.parse_args([name])
        assert args.command == name, f"`personalclaw {name}` no longer dispatches"


# ── The registration door ────────────────────────────────────────────────────────


def test_add_hidden_parser_refuses_a_name_it_was_never_told_about():
    """The membership check is what keeps "hidden" from decaying into "undocumented"."""
    parser = argparse.ArgumentParser(prog="demo")
    sub = parser.add_subparsers(dest="command")
    with pytest.raises(ValueError, match="HIDDEN_COMMANDS"):
        _add_hidden_parser(sub, "not-declared-anywhere")


def test_add_hidden_parser_ignores_a_help_string_it_is_handed():
    """Passing ``help=`` is the mistake; the helper must not forward it into argparse."""
    parser = argparse.ArgumentParser(prog="demo")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("visible", help="a visible one")
    _add_hidden_parser(sub, next(iter(HIDDEN_COMMANDS)), help=argparse.SUPPRESS)
    _hide_internal_commands(parser)

    text = _rendered(parser)
    assert not _SENTINEL.search(text), "a help= kwarg leaked through _add_hidden_parser"
    assert "visible" in text, "the visible sibling vanished — the metavar rewrite is too broad"


def test_hide_internal_commands_survives_a_command_registered_last():
    """Regression on the ORDER bug the fix's post-pass exists to avoid.

    Pinning the metavar at registration time would freeze it before later siblings existed.
    ``mcp-core`` sits mid-list in the real tree, so this asserts both directions: a hidden
    command registered before AND after a visible one leaves every visible one listed.
    """
    hidden_name = next(iter(HIDDEN_COMMANDS))
    parser = argparse.ArgumentParser(prog="demo")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("first", help="before the hidden one")
    _add_hidden_parser(sub, hidden_name)
    sub.add_parser("last", help="after the hidden one")
    _hide_internal_commands(parser)

    text = _rendered(parser)
    assert hidden_name not in text
    assert "first" in text and "last" in text, (
        "a command registered after the hidden one fell out of the metavar — the rewrite ran "
        "too early"
    )

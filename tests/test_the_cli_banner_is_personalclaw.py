"""The CLI banner draws PersonalClaw, and it is drawn in one place.

`personalclaw` with no command prints it on stderr, and the `personalclaw chat` prompt prints it
on stdout. It was drawn twice, once in `cli.py` and once in `cli_chat.py`, and a drawing is
invisible to a word search: a copy that says something else survives every search for a name.
So the art is pinned here character for character, it is defined once
(`personalclaw.constants.BANNER`), both commands print that one definition, and no other string
in the package is a drawing.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

import pytest

from personalclaw import cli, cli_chat
from personalclaw.constants import BANNER

#: The committed art: PersonalClaw in figlet's "small" font, two columns in, over the tagline.
PERSONALCLAW_ART = r"""
   ___                           _  ___ _
  | _ \___ _ _ ___ ___ _ _  __ _| |/ __| |__ ___ __ __
  |  _/ -_) '_(_-</ _ \ ' \/ _` | | (__| / _` \ V  V /
  |_| \___|_| /__/\___/_||_\__,_|_|\___|_\__,_|\_/\_/

  Your personal AI agent
"""

PACKAGE = Path(cli.__file__).resolve().parent


def test_the_banner_is_the_committed_personalclaw_art():
    assert BANNER == PERSONALCLAW_ART


def test_both_commands_print_the_one_banner():
    assert cli.BANNER is BANNER
    assert cli_chat.BANNER is BANNER


def test_personalclaw_with_no_command_prints_it_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["personalclaw"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith(PERSONALCLAW_ART)


def test_the_chat_prompt_prints_it(monkeypatch, capsys):
    def no_more_input(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", no_more_input)
    # Nothing reaches the provider or the config: the prompt ends at its first read.
    asyncio.run(cli_chat._interactive(provider=None, cfg=None))  # type: ignore[arg-type]
    assert capsys.readouterr().out.startswith(PERSONALCLAW_ART)


def _drawn(line: str) -> bool:
    """A row of drawn letters: mostly the strokes figlet draws with, hardly any letters."""
    chars = [c for c in line.strip() if not c.isspace()]
    return (
        len(chars) >= 6
        and sum(c.isalnum() for c in chars) <= 0.25 * len(chars)
        and sum(c in "_|/\\" for c in chars) >= 3
    )


def _drawn_rows(text: str) -> int:
    """The most drawn rows in a row in *text*."""
    best = run = 0
    for line in text.split("\n"):
        run = run + 1 if _drawn(line) else 0
        best = max(best, run)
    return best


def test_the_banner_is_the_only_drawing_in_the_package():
    """A second drawing is how the two copies came to say different things. Every string
    constant in every module is read, so a drawing under another name is found too."""
    drawings = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _drawn_rows(node.value) >= 3:
                    drawings.append(f"{path.relative_to(PACKAGE).as_posix()}:{node.lineno}")
    assert drawings == [f"constants.py:{_banner_line()}"], drawings


def _banner_line() -> int:
    """Where `BANNER`'s string starts in `constants.py`, or 0 when it defines none."""
    tree = ast.parse((PACKAGE / "constants.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "BANNER" for t in node.targets
        ):
            return node.value.lineno
    return 0


def test_the_drawing_detector_sees_the_banner():
    """The rail above is only as good as its detector: the banner itself must read as drawn,
    and prose, a tree listing and a table must not."""
    assert _drawn_rows(PERSONALCLAW_ART) >= 3
    assert _drawn_rows("Your personal AI agent\nsecond line\nthird line") < 3
    assert _drawn_rows("├── app.json  # the manifest\n├── provider.py\n└── LICENSE") < 3
    assert _drawn_rows("| a | b |\n|---|---|\n| 1 | 2 |") < 3

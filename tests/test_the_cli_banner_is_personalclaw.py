"""The CLI banner draws PersonalClaw, and it is drawn in one place.

`personalclaw` with no command prints it on stderr, and the `personalclaw chat` prompt prints it
on stdout. It was drawn twice, once in `cli.py` and once in `cli_chat.py`, and a drawing is
invisible to a word search: a copy that says something else survives every search for a name.
So the art is pinned here character for character, it is defined once
(`personalclaw.constants.BANNER`), both commands print that one definition, and no other string
in the package is a drawing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from the_cli_banner_is_personalclaw_shared import _drawn_rows

from personalclaw import cli, cli_chat, home_gateway
from personalclaw.constants import BANNER

#: The committed art: PersonalClaw in figlet's "small" font, two columns in, over the tagline.
PERSONALCLAW_ART = r"""
   ___                           _  ___ _
  | _ \___ _ _ ___ ___ _ _  __ _| |/ __| |__ ___ __ __
  |  _/ -_) '_(_-</ _ \ ' \/ _` | | (__| / _` \ V  V /
  |_| \___|_| /__/\___/_||_\__,_|_|\___|_\__,_|\_/\_/

  Your personal AI agent
"""


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
    # Nothing reaches a gateway: the chat is opened by the first message, and the prompt ends at
    # its first read.
    unreached = home_gateway.HomeGateway(home=Path("/home/user/.personalclaw"), port=0, pid=0)
    assert cli_chat._interactive(cli_chat._SignIn(unreached), "") == 0
    assert capsys.readouterr().out.startswith(PERSONALCLAW_ART)


def test_the_drawing_detector_sees_the_banner():
    """The rail above is only as good as its detector: the banner itself must read as drawn,
    and prose, a tree listing and a table must not."""
    assert _drawn_rows(PERSONALCLAW_ART) >= 3
    assert _drawn_rows("Your personal AI agent\nsecond line\nthird line") < 3
    assert _drawn_rows("├── app.json  # the manifest\n├── provider.py\n└── LICENSE") < 3
    assert _drawn_rows("| a | b |\n|---|---|\n| 1 | 2 |") < 3

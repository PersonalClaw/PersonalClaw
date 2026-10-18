"""Every place PersonalClaw changes its own process environment, and why it may.

The process environment is every child's: a child started with ``env`` left out inherits it, and
every child built on purpose (``env.gateway_env``, ``sandbox.build_child_env``) starts from it. A
change made to it while the process runs is therefore a change to every program started after it,
whatever it was made for. The transcription path once put a folder in front of ``PATH`` to find
ffmpeg, and every tool server, hook and script started afterwards resolved its programs from that
folder.

So every write in ``src/personalclaw`` (``os.environ[k] = v``, ``del os.environ[k]``, its
``update``/``setdefault``/``pop``/``popitem``/``clear``, ``os.putenv``/``os.unsetenv``) is
classified here once, keyed ``file::qualname``:

* ``_AS_A_COMMAND_STARTS``: the command sets up its own environment before it starts anything.
* ``_MIRRORS_THE_CREDENTIAL_STORE``: a named credential that was saved, removed, rotated or loaded
  is mirrored for the trusted children that read it. Never one whose name decides which programs
  run (``env.PROGRAM_RESOLUTION_NAMES``): the last test holds the store to that.
* ``_A_COMMAND_OF_ITS_OWN``: an evaluation command's own process, never the gateway.

No write anywhere names ``PATH`` or ``TMPDIR``, which a child is given as the process started
with them (``env.STARTUP_NAMES``). A new write reds by name until it is classified; a stale entry
reds too.
"""

from __future__ import annotations

import ast

from process_environment_writes_census_shared import writes


def test_the_census_sees_every_form_of_write():
    """A positive control: each form, in a function and at module level."""
    src = """
import os
os.environ["A"] = "1"
def f(key, env):
    os.environ[key] = "1"
    del os.environ["B"]
    os.environ.update({"PATH": "/x"}, C="1")
    os.environ.update(env)
    os.environ.setdefault("D", "1")
    os.environ.pop("E", None)
    os.environ.clear()
    os.putenv("F", "1")
    os.unsetenv("G")
    os.environ |= env
    seen = os.environ["H"]
    other = {}
    other["I"] = "1"
"""
    got = [(qual, names) for qual, _line, names in writes(ast.parse(src))]
    assert got == [
        ("<module>", ["A"]),
        ("f", [None]),
        ("f", ["B"]),
        ("f", ["PATH", "C"]),
        ("f", [None]),
        ("f", ["D"]),
        ("f", ["E"]),
        ("f", [None]),
        ("f", ["F"]),
        ("f", ["G"]),
        ("f", [None]),
    ]


def test_the_credential_mirror_never_carries_a_name_that_decides_which_programs_run():
    from personalclaw.config.credentials import mirrored_into_the_environment
    from personalclaw.env import PROGRAM_RESOLUTION_NAMES

    assert "PATH" in PROGRAM_RESOLUTION_NAMES
    assert not [n for n in sorted(PROGRAM_RESOLUTION_NAMES) if mirrored_into_the_environment(n)]
    assert mirrored_into_the_environment("EXAMPLE_SERVICE_TOKEN")

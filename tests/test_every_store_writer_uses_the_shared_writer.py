"""Every file PersonalClaw writes whole goes through the one writer, ``atomic_write``.

🔴 ``TriggerStore._write`` wrote ``triggers.json`` through a fixed ``triggers.json.tmp`` and renamed
it into place itself: the store kept the umask's mode (0644 on the usual umask), where every file
the shared writer writes in the home is 0600, and no post-write subscriber heard of the write.
Some thirty other writers did one of the two things it did. Most wrote a JSON document in place
with ``write_text``, so a crash midway left it truncated — a task, a project, a theme, the
rollback ledger of a pack import; the rest renamed a temp file of their own into place.

The shared writer (``atomic_write``, ``atomic_write_bytes``, ``atomic_json_write``) writes a unique
temp file and renames it, makes the file 0600 in a 0700 directory under the home, and announces the
write. This rail finds the two shapes a writer that goes around it takes:

* **a JSON document written in place** — ``write_text``/``write_bytes`` of what ``json.dumps``
  made, or ``json.dump`` into a handle;
* **a file renamed into place by its writer** — ``os.replace``, ``os.rename``, ``Path.replace`` or
  ``Path.rename`` of a file the same function wrote, or took from ``tempfile``.

A writer the shared writer cannot serve is listed below with why. The list must be exactly what the
scan finds, both ways: a new writer that goes around the shared one fails, and so does a listed one
that has since moved onto it.
"""

from __future__ import annotations

import ast
import stat
from pathlib import Path

from every_store_writer_uses_the_shared_writer_shared import IN_PLACE, OWN_RENAME, findings

_GOES_AROUND = """
import json
import os
import tempfile


def in_place(path, data):
    path.write_text(json.dumps(data))


def by_a_name(path, data):
    text = json.dumps(data, indent=2) + "\\n"
    path.write_text(text, encoding="utf-8")


def dumped(path, data):
    with open(path, "w") as fh:
        json.dump(data, fh)


def a_fixed_temp(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def its_own_temp(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    os.write(fd, data)
    os.close(fd)
    os.replace(tmp, path)


def outer(path, data):
    def inner():
        path.write_text(json.dumps(data))

    return inner
"""

_GOES_THROUGH = """
import json

from personalclaw.atomic_write import atomic_json_write, atomic_write


def through(path, data):
    atomic_json_write(path, data)


def compact(path, data):
    atomic_write(path, json.dumps(data, sort_keys=True))


def appended(path, row):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\\n")


def moved(src, dst):
    src.replace(dst)


def text(value):
    return value.replace("a", "b")
"""


def test_the_scan_sees_both_shapes_and_not_the_shared_writer():
    """The scan's positive control: every way around the shared writer it names, by the function
    that takes it; a writer through it, an append and a move of a file nobody wrote here, none."""
    assert findings(ast.parse(_GOES_AROUND)) == {
        "in_place": {IN_PLACE},
        "by_a_name": {IN_PLACE},
        "dumped": {IN_PLACE},
        "a_fixed_temp": {OWN_RENAME},
        "its_own_temp": {OWN_RENAME},
        "inner": {IN_PLACE},
    }
    assert findings(ast.parse(_GOES_THROUGH)) == {}


def test_the_trigger_store_is_written_0600_and_announced():
    from personalclaw import atomic_write
    from personalclaw.config.loader import config_dir
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    heard: list[Path] = []
    hook = heard.append
    atomic_write.register_post_write_hook(hook)
    try:
        TriggerStore().upsert(
            Trigger(
                id="clock:nightly",
                name="nightly",
                kind="clock",
                spec={"kind": "cron", "expr": "0 2 * * *"},
                workflow={"inline": {"provider": "notify", "config": {"title": "nightly"}}},
            )
        )
    finally:
        atomic_write.unregister_post_write_hook(hook)
    path = config_dir() / "triggers.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path in heard, "no post-write subscriber heard of the write"
    assert not path.with_suffix(".json.tmp").exists()

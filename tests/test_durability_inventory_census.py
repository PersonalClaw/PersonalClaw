"""Every home location the CODE names is classified by the durability inventory.

`durability/inventory.py` is the single manifest of what PersonalClaw's state IS, and every
backup reads it: `snapshot._everything_paths` captures what it declares, and `audit_home()` — the
`durability.inventory` Doctor probe — reports what it does not. A location nobody classified is
therefore silently absent from `personalclaw snapshot`, the command the pre-1.0 release notes tell
users to run BEFORE upgrading. Every location has to land in one of three classes, each a decision
someone wrote down beside the row:

* **claimed and snapshotted** — an entry in `INVENTORY`;
* **deliberately excluded, with the reason** — a credential entry (`credential=True`: claimed,
  never captured), or a row in `IGNORED` (runtime scratch, per-install identity);
* **regenerable** — a `derived=True` entry (claimed, rebuilt rather than restored).

There is no fourth class. This file used to hold one — `_UNDECLARED_DEBT`, a pin list of "real
state nobody decided about" — and a pin is exactly how `incident.json` stayed out of every
snapshot for a release while its test passed.

**Why a census of the source, and why it reads the AST.** Hand-written lists of paths only ever
catch the stores someone remembered. The first version of this census was four regexes over the
source text, and a week of normal use found five real stores it could not see (the Doctor
reported "Durability degraded"), each through a spelling the regexes did not know:

1. `Path(config_dir()) / "capture"` — the call is wrapped, so `config_dir() /` never matched
   (`inbound/capture_store.py`); thirteen sites are spelled this way.
2. `root = Path(base_dir) if base_dir else config_dir()` then `root / "trigger-idle"` — the home is
   the FALLBACK branch of an expression, not the whole right-hand side (`triggers/idle_poll.py`);
   a dozen trigger sidecars are spelled this way.
3. `record_routing_stats(rec, home=config_dir())` in one module and `Path(home) / _STATS_FILE`
   three calls deep in another — the home travels through PARAMETERS (`routing/stats.py`).
4. `self._path.with_name(f"{self._path.stem}.{ts}.bak{self._path.suffix}")` under a directory the
   SEL resolves as `Path.home() / ".personalclaw"` rather than through `config_dir()` at all.
5. `incident.json`, which the regexes did see — and which sat on the debt pin.

So this walks every module's syntax tree and follows the home as a VALUE: through `config_dir()`
and `Path.home() / ".personalclaw"`, wrapping calls (`Path()`, `str()`, `.resolve()`), both
branches of `a or b` / `a if c else b`, local and module names, `self.` attributes, function
return values, and parameters across module boundaries (a call that passes a home path marks the
callee's parameter, iterated to a fixed point). Segments resolve from literals, module and class
constants (imported ones included), tuple constants unpacked into `joinpath`, `Path` constants,
and f-strings, whose runtime parts become a placeholder the inventory is asked about.

**What it still cannot see, stated rather than implied.** A segment that is WHOLLY a runtime value
at the top of the home (`config_dir() / name`) has no static part to classify.
`test_the_blind_spot_is_bounded` pins how many of those exist, so a new one is a visible change;
the defence for them is `audit_home()` against a real home, wired as the Doctor probe.

**"Accounted for" is the inventory's question, not this file's.** A location is settled when
`inv.is_accounted` says so. Deciding a location means editing the inventory, never this file.
"""

from __future__ import annotations

import ast

from durability_inventory_census_shared import (
    _DYN,
    _accounted,
    _census_of,
    _HomeCensus,
    _shown,
    _unaccounted,
)

from personalclaw.durability import inventory as inv

# ── floors: the census sees each spelling (a scan that finds nothing passes everything) ──


def _planted(**modules: str) -> _HomeCensus:
    return _census_of(
        {f"personalclaw/{name}.py": ast.parse(text) for name, text in modules.items()}
    )


def test_each_spelling_that_hid_a_real_store_is_read():
    """One planted module per spelling the regex census could not see (module docstring 1-4)."""
    census = _planted(
        loader="def config_dir():\n    ...\n",
        wrapped=(
            "from pathlib import Path\nfrom personalclaw.loader import config_dir\n"
            'def d():\n    return Path(config_dir()) / "wrapped_store"\n'
        ),
        fallback=(
            "from pathlib import Path\nfrom personalclaw.loader import config_dir\n"
            "def d(base_dir=None):\n"
            "    root = Path(base_dir) if base_dir else config_dir()\n"
            '    return root / "fallback_store"\n'
        ),
        caller=(
            "from personalclaw.loader import config_dir\nfrom personalclaw.callee import record\n"
            "def go():\n    record({}, home=config_dir())\n"
        ),
        callee=(
            "from pathlib import Path\n"
            '_FILE = "param_store.json"\n'
            "def _path(home):\n    return Path(home) / _FILE\n"
            "def save(home, data):\n    _path(home).write_text(data)\n"
            "def record(row, *, home):\n    save(home, str(row))\n"
        ),
        sel=(
            "from pathlib import Path\n"
            "def _default_dir():\n    return Path.home() / '.personalclaw'\n"
            "class Log:\n"
            "    def __init__(self, base_dir=None):\n"
            "        self._dir = base_dir or _default_dir()\n"
            "        self._path = self._dir / 'events.jsonl'\n"
            "    def rotate(self, ts):\n"
            "        return self._path.with_name("
            "f'{self._path.stem}.{ts}.bak{self._path.suffix}')\n"
        ),
    )
    found = {_shown(loc) for loc in census.found}
    assert {
        "wrapped_store",
        "fallback_store",
        "param_store.json",
        "events.jsonl",
        "events.{…}.bak.jsonl",
    } <= found, found


def test_the_older_spellings_are_still_read():
    """The four the regex census knew: a literal, a constant, an f-string prefix, a bound home."""
    census = _planted(
        loader="def config_dir():\n    ...\n",
        consts='GOV = "governance"\n',
        old=(
            "import os\nfrom personalclaw.loader import config_dir\n"
            "from personalclaw.consts import GOV\n"
            '_NAME = "const_store"\n'
            "def a(pid, key):\n"
            '    config_dir() / "literal_store"\n'
            "    config_dir() / _NAME\n"
            '    config_dir() / f"session_pid_{pid}.txt"\n'
            "    base = config_dir()\n"
            '    base / f".stop-{key}"\n'
            '    os.path.join(config_dir(), "joined_store")\n'
            "    config_dir() / GOV / 'ceiling.json'\n"
        ),
    )
    found = {_shown(loc) for loc in census.found}
    assert {
        "literal_store",
        "const_store",
        "session_pid_{…}.txt",
        ".stop-{…}",
        "joined_store",
        "governance/ceiling.json",
    } <= found, found


def test_a_name_not_bound_to_the_home_is_not_followed():
    """Or every `path / "x"` in the tree becomes a phantom home location and the ratchet drowns."""
    census = _planted(
        loader="def config_dir():\n    ...\n",
        other='def f(root):\n    return root / "not_a_home_path"\n',
    )
    assert census.found == {}


def test_a_new_unclassified_writer_is_caught():
    """The rail itself, driven: a writer added without an inventory decision fails the ratchet."""
    census = _planted(
        loader="def config_dir():\n    ...\n",
        writer=(
            "from pathlib import Path\nfrom personalclaw.loader import config_dir\n"
            "def save(text):\n"
            '    (Path(config_dir()) / "brand_new_store" / "state.json").write_text(text)\n'
        ),
    )
    assert _unaccounted(census) == ["brand_new_store", "brand_new_store/state.json"]


def test_the_prefix_globs_match_a_real_filename_not_only_the_censused_prefix(tmp_path):
    """`session_pid_*` and `.stop-*` have to satisfy TWO readers: this census, which asks with a
    placeholder where the runtime part was, and `audit_home()`, which sees the real file on disk.
    Driven against real names so a glob written for one reader alone fails here.

    `tmp_path`, and `audit_home` takes the home as an argument — nothing here can reach
    `config_dir()`, which CREATES the home it resolves.
    """
    home = tmp_path
    (home / "session_pid_4711.txt").write_text("chat-1", encoding="utf-8")
    (home / ".stop-chat_1").write_text("", encoding="utf-8")
    result = inv.audit_home(home)
    assert result.unclaimed == [], f"the prefix globs miss the real filenames: {result.unclaimed}"
    for location in (f"session_pid_{_DYN}.txt", f".stop-{_DYN}"):
        assert _accounted(location), f"{_shown(location)} stopped matching its glob"


def test_themes_is_declared_so_snapshot_carries_a_custom_theme():
    """Issue 647. A theme saved from Settings › Design lands in `config_dir()/themes/<slug>.json`
    and the manifest did not know the directory existed, so `personalclaw snapshot` dropped
    every one and a restore came back without them."""
    claim = inv.claim_for("themes/nurse-handoff-night.json")
    assert claim is not None and claim.id == "themes"


# ── the ratchet ──

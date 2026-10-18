"""The source-tree checks split from
``tests/test_process_environment_writes_census.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import source_index
from process_environment_writes_census_shared import writes
from rails_repo import REPO

_AS_A_COMMAND_STARTS: dict[str, str] = {
    "_frozen_child.py::restore_environment": (
        "what the frozen bundle's own start-up added, taken back out before it runs anything"
    ),
    "_ssl_compat.py::_ensure_ssl_certs": (
        "the system CA bundle for OpenSSL, as the CLI module is first imported"
    ),
    "cli.py::main._load_named_credentials": (
        "a `.env`'s named credentials, before the command runs anything"
    ),
    "cli.py::main": "the folder this install's own resources live in, before anything runs",
    "library_env.py::keep_the_libraries_in_the_home": (
        "the libraries' own settings that keep them in the home, before an app imports one"
    ),
    "net/libraries.py::guard_the_hub": (
        "the Hugging Face library's own offline switch, as it is first imported, when it takes no "
        "client that asks the egress guard: its downloads, and its children's, are refused"
    ),
    "gateway_base.py::publish": "the port the gateway bound, once, right after it binds",
    "run_processes.py::end_what_no_run_holds": (
        "a run marker of this home the gateway was started with, dropped as it starts: it is no "
        "run's, and the children it starts with its own environment must not carry it"
    ),
}


_MIRRORS_THE_CREDENTIAL_STORE: dict[str, str] = {
    "config/credentials.py::save_credentials": "a named credential just saved",
    "config/credentials.py::delete_credential": "a credential just deleted",
    "config/credential_migration.py::rollback_credentials_to_keychain": (
        "the values a keychain migration's rollback put back in `.env`"
    ),
    "config/loader.py::AppConfig.load_credentials": "the named credentials the store holds",
    "inbound/auth.py::load_surface_token": "an inbound token another process rotated",
}


_A_COMMAND_OF_ITS_OWN: dict[str, str] = {
    "evals/overlay.py::apply_in_child": "an evals matrix cell's own process",
    "evals/skills_bench.py::_suppressing": "the skills bench command's own process",
    "eval/runner.py::EvalRunner._run_scenario_in": "the eval command's own process",
}


def _src_root() -> Path:
    return REPO / "src" / "personalclaw"


def _census() -> dict[str, list[tuple[int, list[str | None]]]]:
    root = _src_root()
    out: dict[str, list[tuple[int, list[str | None]]]] = {}
    for path, tree in source_index.package_trees():
        rel = path.relative_to(root).as_posix()
        for qual, line, names in writes(tree):
            out.setdefault(f"{rel}::{qual}", []).append((line, names))
    return out


def test_every_write_to_the_process_environment_says_why_it_may():
    census = set(_census())
    classes = (_AS_A_COMMAND_STARTS, _MIRRORS_THE_CREDENTIAL_STORE, _A_COMMAND_OF_ITS_OWN)
    classified = set().union(*classes)
    unmapped = sorted(census - classified)
    assert not unmapped, (
        "A write to the process environment changes every program started after it. Pass the "
        "value to the child that needs it instead (`env=`), or classify the write in "
        "tests/test_process_environment_writes_census.py:\n" + "\n".join(f"  {k}" for k in unmapped)
    )
    stale = sorted(classified - census)
    assert not stale, "No such write any more; remove:\n" + "\n".join(f"  {k}" for k in stale)
    assert sum(len(c) for c in classes) == len(classified), "a site is in two classes"


def test_nothing_writes_path_or_tmpdir():
    """``PATH`` and ``TMPDIR`` are every child's: a program is found by its absolute path and
    handed over, and a child that needs another ``PATH`` or scratch folder is given it in its own
    environment."""
    from personalclaw.env import STARTUP_NAMES

    assert set(STARTUP_NAMES) == {"PATH", "TMPDIR"}
    hits = [
        f"  {key} (line {line}): {name}"
        for key, sites in sorted(_census().items())
        for line, names in sites
        for name in names
        if name in STARTUP_NAMES
    ]
    assert not hits, "The process environment's PATH or TMPDIR is changed at:\n" + "\n".join(hits)

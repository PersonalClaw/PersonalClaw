"""Every file the gateway writes in a loop's folder is one of the loop's own records, or a worker's.

A loop's own records are named one by one (``owner_only.LOOP_RECORD_FILES``,
``LOOP_RECORD_DIRS``), since a worker writes its findings, its log, its deliverable and the
loop's question beside them. A list of names fails open: a record the gateway starts writing
later would be an agent's to change until someone added it. So this rail reads every name the
loop's own code and the ledger write under a loop's folder, and fails for one that is in neither
list. A new gateway file is named here first: as a record, or, if a worker is meant to write it
too, on the worker list below with the reason.
"""

from __future__ import annotations

import re

import source_index

from personalclaw import owner_only

#: The loop's own code and the ledger: every module that writes in a loop's folder.
_WRITERS = ("loop/", "ledger/")

#: What a worker is meant to write in its loop's folder, each with where it is told to.
WORKER_PLACES = {
    "findings": "every worker's finding (loop/files.finding_file)",
    "questions.json": "the loop's question, which an attended worker asks (loop/kinds/__init__.py)",
    "FINDINGS.md": "the worker's working log (agents/defaults.py)",
    "planner": "the planner's own folder (loop/files.planner_dir)",
    "plan_steps.json": "a planner pass's steps, in the planner's folder (loop/plan_walkthrough.py)",
    "step_artifact.json": "a planner step's artifact, in the planner's folder",
    "token_overrides.json": "the design worker's token patch (loop/kinds/design.py)",
}

#: The ways the code names a file in a loop's folder: a path joined to a folder, a constant that
#: holds a name, and a path written into a string.
_JOINED = re.compile(r'/ "([A-Za-z0-9_.-]+)"')
_CONSTANT = re.compile(r'^\s*_?[A-Z_]*(?:FILE|DIR|NAME|SENTINEL) = "([A-Za-z0-9_.-]+)"', re.M)
_IN_A_STRING = re.compile(r'f"(?:\{[a-z_]+\}/)?([a-z_]+)/\{')
_TASK_STEER = re.compile(r'f"(guidance)_\{task_id\}\.txt"')


def _names(text: str) -> set[str]:
    found = set(_JOINED.findall(text)) | set(_CONSTANT.findall(text))
    found |= set(_IN_A_STRING.findall(text))
    found |= {f"{stem}_<task>.txt" for stem in _TASK_STEER.findall(text)}
    return found


#: Names these modules join to folders that are not a loop's: the bundled design defaults, and
#: the folders task worktrees are cut in (``loop/worktree.py``); and the loops' root and database,
#: which the rule holds whole (``owner_only.LOOPS_ROOT``, ``LOOPS_DB_NAMES``).
_ELSEWHERE = {
    "config",
    "design",
    "default-tokens.json",
    "tokens.schema.json",
    "projects",
    "code",
    "worktrees",
    "loop",
    "loops.db",
}


def _is_a_record(name: str) -> bool:
    records = {n.casefold() for n in owner_only.LOOP_RECORD_FILES + owner_only.LOOP_RECORD_DIRS}
    return name.casefold() in records or name == "guidance_<task>.txt"


def test_the_detector_finds_the_names_the_gateway_writes():
    """The control: the reading finds a joined name, a constant and a name in a string."""
    sample = (
        'atomic_write(d / "status.json", x)\nMERGES_FILE = "merges.jsonl"\n'
        'rel = f"outputs/{_ledger_filename(p)}"\nreturn f"guidance_{task_id}.txt"\n'
    )
    assert _names(sample) == {"status.json", "merges.jsonl", "outputs", "guidance_<task>.txt"}


def test_every_name_the_gateway_writes_in_a_loops_folder_is_a_record_or_a_workers():
    unnamed: dict[str, list[str]] = {}
    sources = [
        path
        for path in source_index.package_files()
        if source_index.rel(path).startswith(tuple(f"src/personalclaw/{w}" for w in _WRITERS))
    ]
    assert len(sources) > 20, sources  # the loop's modules and the ledger's were found
    for source in sources:
        for name in _names(source_index.text(source)):
            if name in _ELSEWHERE or _is_a_record(name) or name in WORKER_PLACES:
                continue
            unnamed.setdefault(name, []).append(source_index.rel(source))
    assert unnamed == {}, (
        "a file the gateway writes in a loop's folder is neither a loop's own record "
        "(owner_only.LOOP_RECORD_FILES / LOOP_RECORD_DIRS) nor on this rail's worker list: "
        f"{unnamed}"
    )


def test_no_worker_place_is_also_a_record():
    for name in WORKER_PLACES:
        assert not _is_a_record(name), name

"""Every report a catalogue entry's Playwright step writes into the checkout is one git ignores.

run-job records a job only when the checkout is clean, at one head, before and after it (``git
status --porcelain``). So an output the tree does not ignore turns a passing job into one with no
record and no verdict. e2e-stateful's matrix and idle reports did this: all 245 of its tests
passed, and merge.yml runs 40072544109 and 40093093827 still read "no record: the checkout was not
clean at one head before and after the job". The JSON report each Playwright run writes
(``PLAYWRIGHT_JSON_OUTPUT_NAME``) and its blob report (``PLAYWRIGHT_BLOB_OUTPUT_DIR``) are named in
the entry's env, so this test reads every one and asks git whether it ignores it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from tools.ci import catalogue
from tools.ci.catalogue import REPO

#: A step's env names that are a path Playwright writes, relative to the step's working folder,
#: and whether the path is a folder.
OUTPUT_ENV = {"PLAYWRIGHT_JSON_OUTPUT_NAME": False, "PLAYWRIGHT_BLOB_OUTPUT_DIR": True}


def outputs(root: Path) -> list[tuple[str, str, str]]:
    """(entry, env name, path from the root) for each output an entry's step names, a sharded
    entry's for every shard."""
    found: list[tuple[str, str, str]] = []
    for job in catalogue.read(root).jobs:
        shards = [str(i) for i in range(1, job.shards + 1)] or ["1"]
        for step in job.command:
            env = {**job.env, **step.env}
            for name in OUTPUT_ENV:
                if name not in env:
                    continue
                for shard in shards:
                    value = env[name].replace("{shard}", shard)
                    rel = (PurePosixPath(step.workdir or ".") / value).as_posix()
                    found.append((job.id, name, rel))
    return found


def unignored(root: Path, named: list[tuple[str, str, str]]) -> list[str]:
    """Each named output the tree at *root* does not ignore. A folder is asked about a file in it,
    as Playwright writes files there, and ``--no-index`` asks the ignore rules alone."""
    probes = {rel: rel + "/report.zip" if OUTPUT_ENV[name] else rel for _, name, rel in named}
    done = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--no-index", "--stdin"],
        input="\n".join(probes.values()) + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode in (0, 1), done.stderr  # 1: none ignored
    ignored = set(done.stdout.split())
    return [
        f"{job}: its {name} writes {rel}, which the tree does not ignore, so the checkout is not"
        " clean after the job and run-job records no verdict for it"
        for job, name, rel in named
        if probes[rel] not in ignored
    ]


def test_the_tree_names_entry_outputs_to_read() -> None:
    names = {name for _, name, _ in outputs(REPO)}
    assert names == set(OUTPUT_ENV), f"the catalogue names {sorted(names)}: the rail reads nothing"


def test_every_entry_output_is_one_git_ignores() -> None:
    assert unignored(REPO, outputs(REPO)) == []


def test_the_ignore_rule_before_the_stateful_split_is_refused(tmp_path: Path) -> None:
    """The plant: the ignore rule as it stood when only ``e2e-report.json`` was written leaves the
    stateful leg's matrix and idle reports in the checkout."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "web").mkdir()
    (tmp_path / "web/.gitignore").write_text("/e2e-report.json\n/blob-report/\n", encoding="utf-8")
    named = [row for row in outputs(REPO) if row[0] == "e2e-stateful"]
    found = unignored(tmp_path, named)
    assert any("web/e2e-report-matrix.json" in v for v in found), found
    assert any("web/e2e-report-idle.json" in v for v in found), found
    assert not any("blob-report" in v for v in found), found

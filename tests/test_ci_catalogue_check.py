"""tools/ci's check of a pull request's catalogue against main's (``catalogue.against`` and
``python -m tools.ci catalogue-check``).

Every catalogue here is inline text, so this module reads nothing of the tree. The ``changes``
job's step that runs the check from main's copy is held in
``tests/rails/test_ci_tier_workflow_steps_tree.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tools.ci import catalogue
from tools.ci.__main__ import main

#: A catalogue the rules accept: the merge tier's three security jobs, two security jobs and a
#: live one in the breadth tier, and an ordinary one beside them.
_MAIN = """
[tier.merge]
jobs = ["rails", "python-suite", "python-suite-macos", "security"]

[[job]]
id = "rails"
tiers = ["change", "merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
reuse = "tree"
timeout_min = 10
command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]

[[job]]
id = "python-suite"
tiers = ["merge"]
inputs = ["src/", "tests/"]
external_inputs = ["shard-history"]
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
timeout_min = 55
shards = 4
command = [{ argv = ["uv", "run", "pytest", "--shard", "{shard}/4"] }]

[[job]]
id = "python-suite-macos"
tiers = ["merge"]
inputs = ["src/", "tests/"]
external_inputs = ["shard-history"]
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
platform = "macos-26"
timeout_min = 50
command = [{ argv = ["uv", "run", "pytest", "tests"] }]

[[job]]
id = "security"
tiers = ["merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
timeout_min = 25
command = [{ argv = ["uv", "run", "pytest", "tests/security"] }]

[[job]]
id = "corpus"
tiers = ["breadth"]
inputs = ["tests/security/"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
timeout_min = 25
command = [{ argv = ["uv", "run", "pytest", "tests/security"] }]

[[job]]
id = "suite-arm"
tiers = ["breadth"]
inputs = ["src/", "tests/"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
platform = "ubuntu-24.04-arm"
timeout_min = 50
command = [{ argv = ["uv", "run", "pytest", "tests"] }]

[[job]]
id = "nightly-audit"
tiers = ["breadth"]
inputs = [":(glob)**"]
external_inputs = ["advisory-feed"]
toolchain = ["uv"]
cache = ["uv"]
security = "ordinary"
live = true
timeout_min = 20
command = [{ argv = ["uv", "run", "python", "-m", "tools.ci", "audit"] }]

[[job]]
id = "rails-macos"
tiers = ["breadth"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
platform = "macos-26"
reuse = "tree"
timeout_min = 20
command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]
"""


def _load(text: str) -> catalogue.Catalogue:
    return catalogue.load(text, "candidate")


def _entries(text: str) -> tuple[str, list[str]]:
    head, *entries = text.split("\n[[job]]\n")
    return head, entries


def _drop(text: str, job_id: str) -> str:
    """``text`` without the entry ``job_id``."""
    head, entries = _entries(text)
    kept = [e for e in entries if not e.startswith(f'id = "{job_id}"\n')]
    assert len(kept) == len(entries) - 1, job_id
    return "\n[[job]]\n".join([head, *kept])


def _mark(text: str, job_id: str, issue: str = "#4999") -> str:
    """``text`` with the entry ``job_id`` marked retiring."""
    opening = f'id = "{job_id}"\n'
    assert text.count(opening) == 1, job_id
    return text.replace(opening, f'{opening}retiring = "{issue}"\n', 1)


def _in(text: str, job_id: str, old: str, new: str) -> str:
    """``text`` with one edit inside the entry ``job_id``."""
    head, entries = _entries(text)
    edited = [e.replace(old, new, 1) if e.startswith(f'id = "{job_id}"\n') else e for e in entries]
    assert edited != entries, (job_id, old)
    return "\n[[job]]\n".join([head, *edited])


def _against(candidate: str, trusted: str = _MAIN) -> list[str]:
    return catalogue.against(_load(candidate), _load(trusted))


# ── a security or live job main has: each refusal, and the one allowance ────────────────────


def test_main_has_no_objection_to_its_own_catalogue() -> None:
    assert _against(_MAIN) == []


@pytest.mark.parametrize("job_id", ["corpus", "suite-arm", "nightly-audit"])
def test_dropping_a_security_or_live_job_main_does_not_mark_is_refused(job_id: str) -> None:
    kind = "live" if job_id == "nightly-audit" else "security"
    assert _against(_drop(_MAIN, job_id)) == [
        f"{job_id}: main's catalogue has this {kind} job, and this catalogue drops it: mark it"
        ' retiring = "#<issue>" in a merge of its own first'
    ]


@pytest.mark.parametrize("job_id", ["suite-arm", "nightly-audit"])
def test_renaming_a_security_or_live_job_main_does_not_mark_is_refused(job_id: str) -> None:
    """The entry stays, under another id: main's job is gone all the same, and the renamed one
    is a new job of the pull request's."""
    kind = "live" if job_id == "nightly-audit" else "security"
    renamed = _MAIN.replace(f'id = "{job_id}"\n', f'id = "{job_id}-2"\n', 1)
    assert f"{job_id}-2" in {j.id for j in _load(renamed).jobs}
    assert _against(renamed) == [
        f"{job_id}: main's catalogue has this {kind} job, and this catalogue drops it: mark it"
        ' retiring = "#<issue>" in a merge of its own first'
    ]


def test_renaming_a_job_main_marks_retiring_loads() -> None:
    renamed = _MAIN.replace('id = "suite-arm"\n', 'id = "suite-arm-2"\n', 1)
    assert _against(renamed, trusted=_mark(_MAIN, "suite-arm")) == []


@pytest.mark.parametrize("job_id", ["suite-arm", "nightly-audit"])
def test_dropping_a_job_main_marks_retiring_loads(job_id: str) -> None:
    """The second of the two merges: main's catalogue carries the mark, so the entry may go."""
    assert _against(_drop(_MAIN, job_id), trusted=_mark(_MAIN, job_id)) == []


def test_marking_a_job_retiring_in_the_pull_request_that_drops_it_is_refused() -> None:
    """The mark is main's or nothing: main's copy here has none, whatever the pull request's
    own catalogue said before it dropped the entry."""
    marked_then_dropped = _drop(_mark(_MAIN, "suite-arm"), "suite-arm")
    problems = _against(marked_then_dropped)
    assert problems and problems[0].startswith(
        "suite-arm: main's catalogue has this security job, and this catalogue drops it"
    ), problems


def test_marking_a_job_retiring_keeps_it_and_loads() -> None:
    """The first of the two merges: the job is marked and still the catalogue's, so it runs."""
    marked = _load(_mark(_MAIN, "suite-arm"))
    assert marked.job("suite-arm").retiring == "#4999"
    assert catalogue.against(marked, _load(_MAIN)) == []


@pytest.mark.parametrize(
    ("job_id", "edits", "refused"),
    [
        pytest.param(
            "suite-arm",
            [('security = "security"', 'security = "ordinary"')],
            "suite-arm: security is ordinary, and main's catalogue has it security",
            id="posture",
        ),
        pytest.param(
            "nightly-audit",
            [
                ('external_inputs = ["advisory-feed"]', "external_inputs = []"),
                ("live = true\n", ""),
            ],
            "nightly-audit: live is false, and main's catalogue has it live",
            id="live",
        ),
        pytest.param(
            "corpus",
            [('tiers = ["breadth"]', 'tiers = ["change"]')],
            "corpus: it leaves the breadth tier, where main's catalogue runs this security job",
            id="tier",
        ),
    ],
)
def test_a_retiring_mark_allows_only_the_drop(
    job_id: str, edits: list[tuple[str, str]], refused: str
) -> None:
    """A job main marks retiring that the pull request keeps is held like any other: its
    posture, its live flag and its tiers. Both copies carry the mark, as on the second merge."""
    trusted = _mark(_MAIN, job_id)
    kept = trusted
    for old, new in edits:
        kept = _in(kept, job_id, old, new)
    assert _against(kept, trusted=trusted) == [refused]


def test_a_mark_on_another_job_allows_nothing_for_this_one() -> None:
    problems = _against(_drop(_MAIN, "corpus"), trusted=_mark(_MAIN, "suite-arm"))
    assert any(p.startswith("corpus: main's catalogue has this security job") for p in problems)


# ── what else main holds of a security or live job ───────────────────────────────────────────


def test_a_security_job_turned_ordinary_is_refused() -> None:
    turned = _in(_MAIN, "suite-arm", 'security = "security"', 'security = "ordinary"')
    assert _against(turned) == [
        "suite-arm: security is ordinary, and main's catalogue has it security"
    ]


def test_a_live_job_no_longer_live_is_refused() -> None:
    """A job that reads the advisory feed must stay live, so this candidate stops reading it."""
    text = _in(
        _MAIN, "nightly-audit", 'external_inputs = ["advisory-feed"]', "external_inputs = []"
    )
    text = _in(text, "nightly-audit", "live = true\n", "")
    assert _against(text) == ["nightly-audit: live is false, and main's catalogue has it live"]


def test_a_security_job_moved_out_of_its_tier_is_refused() -> None:
    moved = _in(_MAIN, "corpus", 'tiers = ["breadth"]', 'tiers = ["change"]')
    assert _against(moved) == [
        "corpus: it leaves the breadth tier, where main's catalogue runs this security job"
    ]


def test_a_security_job_given_another_tier_too_loads() -> None:
    more = _in(_MAIN, "corpus", 'tiers = ["breadth"]', 'tiers = ["change", "breadth"]')
    assert _against(more) == []


def test_a_catalogue_with_no_security_job_in_breadth_is_refused() -> None:
    """Even when main marks both breadth security jobs retiring, breadth keeps one."""
    trusted = _mark(_mark(_MAIN, "corpus"), "suite-arm")
    candidate = _drop(_drop(_MAIN, "corpus"), "suite-arm")
    assert _against(candidate, trusted=trusted) == [
        "no security job runs in the breadth tier, and main's catalogue runs one there"
    ]


def test_a_security_job_in_breadth_is_kept_not_demanded() -> None:
    """The rule is a keep: a main whose breadth tier runs no security job asks for none, so a
    catalogue that never had one there is not refused for lacking it."""
    text = _in(_MAIN, "corpus", 'tiers = ["breadth"]', 'tiers = ["change"]')
    text = _in(text, "suite-arm", 'tiers = ["breadth"]', 'tiers = ["change"]')
    assert not any(j.security == "security" for j in _load(text).tier("breadth"))
    assert _against(text, trusted=text) == []


# ── the loader, given main's catalogue as its reference ─────────────────────────────────────


def test_the_loader_refuses_a_catalogue_its_reference_refuses() -> None:
    with pytest.raises(catalogue.CatalogueError) as caught:
        catalogue.load(_drop(_MAIN, "suite-arm"), "candidate", reference=_load(_MAIN))
    assert caught.value.problems == [
        "suite-arm: main's catalogue has this security job, and this catalogue drops it: mark it"
        ' retiring = "#<issue>" in a merge of its own first'
    ]


def test_the_loader_reads_a_drop_its_reference_marks_retiring() -> None:
    reference = _load(_mark(_MAIN, "suite-arm"))
    cat = catalogue.load(_drop(_MAIN, "suite-arm"), "candidate", reference=reference)
    assert "suite-arm" not in {j.id for j in cat.jobs}


def test_the_loader_without_a_reference_reads_the_catalogue_alone() -> None:
    """The drop is only a fault against main's: alone, the same text loads."""
    assert "suite-arm" not in {j.id for j in _load(_drop(_MAIN, "suite-arm")).jobs}


def test_a_broken_entry_is_named_as_broken_not_as_dropped() -> None:
    """An entry the loader cannot read is no entry of the catalogue it builds, so it is not
    also read as one the pull request dropped."""
    broken = _in(_MAIN, "suite-arm", "timeout_min = 50", 'timeout_min = "fifty"')
    with pytest.raises(catalogue.CatalogueError) as caught:
        catalogue.load(broken, "candidate", reference=_load(_MAIN))
    problems = caught.value.problems
    assert any(p.startswith("suite-arm: timeout_min") for p in problems), problems
    assert not any("drops it" in p for p in problems), problems


def test_an_ordinary_job_may_be_dropped_without_a_mark() -> None:
    assert _against(_drop(_MAIN, "rails-macos")) == []


def test_a_new_security_job_needs_nothing_from_main() -> None:
    extra = (
        '\n[[job]]\nid = "corpus-2"\ntiers = ["breadth"]\ninputs = ["tests/security/"]\n'
        'external_inputs = []\ntoolchain = ["uv-sync"]\ncache = ["uv"]\nsecurity = "security"\n'
        'timeout_min = 25\ncommand = [{ argv = ["uv", "run", "pytest", "tests/security"] }]\n'
    )
    assert _against(_MAIN + extra) == []


# ── the retiring field ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ['"soon"', '"4999"', '"#"', '"#0"', '"#12a"', '""', "4999"])
def test_a_retiring_value_that_is_no_issue_is_refused(value: str) -> None:
    text = _MAIN.replace('id = "suite-arm"\n', f'id = "suite-arm"\nretiring = {value}\n', 1)
    with pytest.raises(catalogue.CatalogueError) as caught:
        _load(text)
    assert any(p.startswith("suite-arm: retiring ") for p in caught.value.problems)


def test_an_entry_without_a_mark_reads_as_not_retiring() -> None:
    assert _load(_MAIN).job("suite-arm").retiring is None


# ── the nightly field ───────────────────────────────────────────────────────────────────────

#: _MAIN with a live merge-tier audit beside breadth's nightly one, running its steps, and
#: inputs that leave it out of a plan they do not reach.
_WITH_AUDIT = _MAIN.replace('"security"]\n', '"security", "audit"]\n', 1) + (
    '\n[[job]]\nid = "audit"\ntiers = ["merge"]\ninputs = ["uv.lock"]\n'
    'external_inputs = ["advisory-feed"]\ntoolchain = ["uv"]\ncache = ["uv"]\n'
    'security = "ordinary"\nlive = true\ntimeout_min = 15\n'
    'command = [{ argv = ["uv", "run", "python", "-m", "tools.ci", "audit"] }]\n'
)
_MARKED = _WITH_AUDIT.replace('id = "audit"\n', 'id = "audit"\nnightly = "nightly-audit"\n', 1)


def _nightly_refused(text: str) -> list[str]:
    with pytest.raises(catalogue.CatalogueError) as caught:
        _load(text)
    return [p for p in caught.value.problems if "nightly" in p]


def test_a_live_job_marked_with_the_breadth_job_that_runs_its_steps_loads() -> None:
    assert _WITH_AUDIT != _MAIN and _MARKED != _WITH_AUDIT
    assert _load(_WITH_AUDIT).job("audit").nightly is None
    assert _load(_MARKED).job("audit").nightly == "nightly-audit"


@pytest.mark.parametrize(
    "old, new, refusal",
    [
        (
            "live = true\n",
            "",
            "audit: nightly is set, and the job is not live: a job that is not live is planned"
            " by its inputs already",
        ),
        (
            'security = "ordinary"\nlive',
            'security = "security"\nlive',
            "audit: nightly is set, and the job is security posture, which every plan holds",
        ),
        (
            'nightly = "nightly-audit"',
            'nightly = "no-such-job"',
            "audit: nightly names 'no-such-job', which is not another job",
        ),
        (
            'nightly = "nightly-audit"',
            'nightly = "audit"',
            "audit: nightly names 'audit', which is not another job",
        ),
        (
            'nightly = "nightly-audit"',
            'nightly = "rails"',
            "audit: nightly names rails, which the breadth tier does not run",
        ),
        (
            'nightly = "nightly-audit"',
            'nightly = "rails-macos"',
            "audit: nightly names rails-macos, which is not live, so a reused result could stand"
            " for its nightly run",
        ),
        (
            '"tools.ci", "audit"] }]\n',
            '"tools.ci", "audit", "--quick"] }]\n',
            "audit: nightly names nightly-audit, whose steps are not this job's",
        ),
    ],
)
def test_a_nightly_mark_that_could_skip_the_job_unseen_is_refused(
    old: str, new: str, refusal: str
) -> None:
    head, entries = _entries(_MARKED)
    audit = next(e for e in entries if e.startswith('id = "audit"\n'))
    assert old in audit, old
    text = _MARKED.replace(audit, audit.replace(old, new, 1), 1)
    assert refusal in _nightly_refused(text)


def test_a_nightly_mark_main_lacks_counts_for_nothing() -> None:
    """Read against main's catalogue, the pull request that adds the mark still plans the job:
    like ``retiring``, a mark counts once main carries it."""
    assert _against(_MARKED, _WITH_AUDIT) == []
    added = catalogue.load(_MARKED, "candidate", _load(_WITH_AUDIT))
    assert added.job("audit").nightly is None
    kept = catalogue.load(_MARKED, "candidate", _load(_MARKED))
    assert kept.job("audit").nightly == "nightly-audit"
    assert catalogue.load(_MARKED, "candidate").job("audit").nightly == "nightly-audit"


def test_a_nightly_mark_main_gives_another_job_counts_for_nothing() -> None:
    second = (
        '\n[[job]]\nid = "nightly-audit-2"\ntiers = ["breadth"]\ninputs = [":(glob)**"]\n'
        'external_inputs = ["advisory-feed"]\ntoolchain = ["uv"]\ncache = ["uv"]\n'
        'security = "ordinary"\nlive = true\ntimeout_min = 20\n'
        'command = [{ argv = ["uv", "run", "python", "-m", "tools.ci", "audit"] }]\n'
    )
    mains = _MARKED + second
    moved = mains.replace('nightly = "nightly-audit"', 'nightly = "nightly-audit-2"', 1)
    assert _load(moved).job("audit").nightly == "nightly-audit-2"
    assert catalogue.load(moved, "candidate", _load(mains)).job("audit").nightly is None


# ── python -m tools.ci catalogue-check ──────────────────────────────────────────────────────


def _files(tmp_path: Path, candidate: str, trusted: str = _MAIN) -> tuple[str, str]:
    mine, mains = tmp_path / "candidate.toml", tmp_path / "main.toml"
    mine.write_text(candidate, encoding="utf-8")
    mains.write_text(trusted, encoding="utf-8")
    return str(mine), str(mains)


def test_the_command_passes_a_catalogue_main_accepts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mine, mains = _files(tmp_path, _MAIN)
    assert main(["catalogue-check", "--catalogue", mine, "--against", mains]) == 0
    assert capsys.readouterr().out == (
        "catalogue-check: main's catalogue has no objection to this one\n"
    )


def test_the_command_fails_a_catalogue_main_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mine, mains = _files(tmp_path, _drop(_MAIN, "suite-arm"))
    assert main(["catalogue-check", "--catalogue", mine, "--against", mains]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "catalogue-check: this pull request's catalogue: suite-arm: main's catalogue has this"
        ' security job, and this catalogue drops it: mark it retiring = "#<issue>" in a merge of'
        " its own first"
    ]


def test_the_command_fails_a_catalogue_mains_rules_refuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Main's loader reads the candidate: a rule only main has applies to it."""
    mine, mains = _files(tmp_path, _MAIN.replace("timeout_min = 10", "timout_min = 10", 1))
    assert main(["catalogue-check", "--catalogue", mine, "--against", mains]) == 1
    out = capsys.readouterr().out
    assert "catalogue-check: this pull request's catalogue: rails: unknown key 'timout_min'" in out


@pytest.mark.parametrize("which", ["candidate", "main"])
def test_the_command_fails_when_a_catalogue_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], which: str
) -> None:
    mine, mains = _files(tmp_path, _MAIN)
    Path(mine if which == "candidate" else mains).unlink()
    assert main(["catalogue-check", "--catalogue", mine, "--against", mains]) == 1
    out = capsys.readouterr().out
    whose = "this pull request's" if which == "candidate" else "main's"
    assert f"catalogue-check: {whose} catalogue cannot be read" in out


def test_the_command_fails_when_mains_catalogue_does_not_load(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mine, mains = _files(tmp_path, _MAIN, trusted="[[job]\n")
    assert main(["catalogue-check", "--catalogue", mine, "--against", mains]) == 1
    assert "catalogue-check: main's catalogue: " in capsys.readouterr().out


def test_the_command_needs_mains_catalogue(tmp_path: Path) -> None:
    mine, _ = _files(tmp_path, _MAIN)
    assert main(["catalogue-check", "--catalogue", mine]) == 2


def test_no_line_the_command_prints_reads_as_a_workflow_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = _MAIN.replace('id = "rails"\n', 'id = "rails"\n"::error::x" = 1\n', 1)
    mine, mains = _files(tmp_path, text)
    assert main(["catalogue-check", "--catalogue", mine, "--against", mains]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(line.startswith("catalogue-check: ") for line in lines), lines


#: Line breaks as a TOML string escapes them, which is also how the command prints them.
_BREAKS = ("\\n", "\\r", "\\u2028")


def _duplicated_id(name: str) -> str:
    entry = f'\n[[job]]\nid = "{name}"\n'
    return _MAIN + entry + entry


def _merge_list_name(name: str) -> str:
    listed = 'jobs = ["rails", "python-suite", "python-suite-macos", "security"]'
    assert _MAIN.count(listed) == 1
    return _MAIN.replace(listed, listed[:-1] + f', "{name}"]', 1)


def _merge_security_platform(name: str) -> str:
    return _in(_MAIN, "python-suite-macos", 'platform = "macos-26"', f'platform = "{name}"')


@pytest.mark.parametrize("escaped", _BREAKS)
@pytest.mark.parametrize(
    ("plant", "said"),
    [
        pytest.param(_duplicated_id, "the id is used by 2 entries", id="duplicated-id"),
        pytest.param(_merge_list_name, "[tier.merge] lists it", id="merge-list-name"),
        pytest.param(_merge_security_platform, "platform is windows", id="merge-platform"),
    ],
)
def test_a_line_break_the_catalogue_holds_is_printed_escaped(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    plant: Callable[[str], str],
    said: str,
    escaped: str,
) -> None:
    """Some of the loader's problems quote the candidate's text as it is: a duplicated id, a
    name in ``[tier.merge]``, a merge-tier security job's platform. A line break in that text
    is printed as its escape, so every line still starts ``catalogue-check:``."""
    mine, mains = _files(tmp_path, plant(f"windows{escaped}second line"))
    assert main(["catalogue-check", "--catalogue", mine, "--against", mains]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(line.startswith("catalogue-check: ") for line in lines), lines
    shown = [line for line in lines if f"windows{escaped}second line" in line]
    assert shown and any(said in line for line in shown), lines

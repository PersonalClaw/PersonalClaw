"""Settled product decisions are superseded, never rewritten.

``docs/decisions/`` holds one file per settled product decision, and its ``README.md`` says how to
add one and how to change one. A settled decision is the record later changes build on, so it is
never edited in place and never deleted. A change adds a new decision whose ``supersedes`` names
the old slug, and in the same change sets the old file's ``status`` to ``superseded`` and its
``superseded_by`` to the new slug. Those two fields are the only edits an existing file takes.

Two halves hold it:

* **The shape**, read from the working tree, so it needs no history: every file is named
  ``<yyyy-mm-dd>-<slug>.md``, opens with the five front-matter keys in order, has the three
  sections, and every ``supersedes`` and ``superseded_by`` names a file here that names it back.
* **The comparison with the base of the change**: the commit ``BASE_SHA`` names when it is set,
  otherwise ``git merge-base HEAD origin/main``. Every file present at the base is still here, with
  everything but its ``status`` and ``superseded_by`` lines byte for byte the same. CI's test shards
  check out a single commit with no base, so there the comparison is skipped and says why. A
  ``BASE_SHA`` that names a commit this clone lacks fails, rather than passing on nothing.

The fixtures below drive both halves on a temporary repository, so neither depends on this
repository's history.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: The folder, relative to a repository root.
FOLDER = "docs/decisions"
#: The one file in the folder that is not a decision.
README = "README.md"
#: The front-matter keys, in the order every decision carries them.
FIELDS = ("title", "date", "status", "supersedes", "superseded_by")
#: The only keys an existing decision may change.
MUTABLE = frozenset({"status", "superseded_by"})
STATUSES = ("accepted", "superseded")
#: A decision's sections, in order.
SECTIONS = ("## Decision", "## Why", "## What it rules out")
NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
#: How many decisions the folder was seeded with. The live census never reads fewer, so a folder
#: that moved, or a listing that found nothing, cannot pass on an empty set.
SEEDED = 4


class BaseNotFound(RuntimeError):
    """``BASE_SHA`` names a commit this clone does not have."""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8"
    )


def _split(text: str) -> tuple[list[str], list[str]] | None:
    """A decision's front-matter lines and the lines after them, or ``None`` with no front
    matter: the first line is ``---`` and a later line closes it with another."""
    lines = text.split("\n")
    if lines[0] != "---" or "---" not in lines[1:]:
        return None
    end = lines.index("---", 1)
    return lines[1:end], lines[end + 1 :]


def parse(text: str) -> tuple[dict[str, str], str] | None:
    """A decision's front matter (its keys in file order) and its body, or ``None`` when the
    front matter is missing or a line of it is not one ``key: value``."""
    split = _split(text)
    if split is None:
        return None
    head, body = split
    fields: dict[str, str] = {}
    for line in head:
        key, colon, value = line.partition(":")
        if not colon or not key or key != key.strip() or key in fields:
            return None
        fields[key] = value.strip()
    return fields, "\n".join(body)


def frozen(text: str) -> str:
    """The decision with its two editable front-matter lines taken out: what never changes."""
    split = _split(text)
    if split is None:
        return text
    head, body = split
    kept = [line for line in head if line.partition(":")[0] not in MUTABLE]
    return "\n".join(["---", *kept, "---", *body])


def in_tree(root: Path) -> dict[str, str]:
    """The decisions in *root*'s working tree, file name to text."""
    folder = root / FOLDER
    if not folder.is_dir():
        return {}
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(folder.glob("*.md"))
        if path.name != README
    }


def at_commit(root: Path, commit: str) -> dict[str, str]:
    """The decisions *commit* held, file name to text."""
    listed = _git(root, "ls-tree", "--name-only", "-z", commit, f"{FOLDER}/")
    if listed.returncode != 0:
        raise RuntimeError(f"git ls-tree {commit} {FOLDER}/: {listed.stderr.strip()}")
    files: dict[str, str] = {}
    for path in filter(None, listed.stdout.split("\0")):
        name = path.rsplit("/", 1)[-1]
        if not name.endswith(".md") or name == README:
            continue
        shown = _git(root, "show", f"{commit}:{path}")
        if shown.returncode != 0:
            raise RuntimeError(f"git show {commit}:{path}: {shown.stderr.strip()}")
        files[name] = shown.stdout
    return files


def base_commit(root: Path, environ: Mapping[str, str]) -> str | None:
    """The commit a change is compared with: ``BASE_SHA`` when set, else the merge-base of
    ``HEAD`` and ``origin/main``, else ``None`` (no base to compare with)."""
    named = environ.get("BASE_SHA", "").strip()
    if named:
        found = _git(root, "rev-parse", "--verify", "--quiet", f"{named}^{{commit}}")
        if found.returncode != 0:
            raise BaseNotFound(
                f"BASE_SHA names {named}, which this clone does not have: fetch that commit, "
                "or unset BASE_SHA to compare with origin/main"
            )
        return found.stdout.strip()
    merged = _git(root, "merge-base", "HEAD", "origin/main")
    if merged.returncode != 0:
        return None
    return merged.stdout.strip() or None


def problems(files: Mapping[str, str]) -> list[str]:
    """What is wrong with a folder of decisions (file name to text), each a sentence."""
    found: list[str] = []
    named: dict[str, tuple[str, dict[str, str]]] = {}
    for name, text in sorted(files.items()):
        match = NAME.match(name)
        if not match:
            found.append(f"{name}: a decision file is named <yyyy-mm-dd>-<slug>.md")
            continue
        day, slug = match.groups()
        doc = parse(text)
        if doc is None:
            found.append(f"{name}: it opens with no front matter of `key: value` lines")
            continue
        fields, body = doc
        if tuple(fields) != FIELDS:
            found.append(f"{name}: its front matter is {list(fields)}, not {list(FIELDS)}")
            continue
        if not fields["title"]:
            found.append(f"{name}: it has no title")
        if fields["date"] != day:
            found.append(f"{name}: its date is {fields['date']!r}, and its name says {day}")
        if fields["status"] not in STATUSES:
            found.append(f"{name}: its status is {fields['status']!r}, not one of {STATUSES}")
        headings = tuple(line for line in body.split("\n") if line.startswith("## "))
        if headings != SECTIONS:
            found.append(f"{name}: its sections are {list(headings)}, not {list(SECTIONS)}")
        if slug in named:
            found.append(f"{name}: its slug {slug!r} is {named[slug][0]}'s too")
            continue
        named[slug] = (name, fields)
    for slug, (name, fields) in sorted(named.items()):
        older, newer = fields["supersedes"], fields["superseded_by"]
        for key, other in (("supersedes", older), ("superseded_by", newer)):
            if other == slug:
                found.append(f"{name}: its {key} names itself")
            elif other and other not in named:
                found.append(f"{name}: its {key} names {other!r}, and no decision here has it")
        if fields["status"] in STATUSES and (fields["status"] == "superseded") != bool(newer):
            found.append(
                f"{name}: its status is {fields['status']!r} and its superseded_by is "
                f"{newer!r}; a decision is superseded exactly when superseded_by names the "
                "decision that replaced it"
            )
        if newer in named and newer != slug and named[newer][1]["supersedes"] != slug:
            found.append(
                f"{name}: its superseded_by names {newer!r}, which does not supersede {slug!r}"
            )
        if older in named and older != slug and named[older][1]["superseded_by"] != slug:
            found.append(f"{name}: it supersedes {older!r}, whose superseded_by does not name it")
    return found


def rewrites(base: Mapping[str, str], head: Mapping[str, str]) -> list[str]:
    """How *head* deleted or rewrote a decision *base* already held, each a sentence."""
    found: list[str] = []
    for name, before in sorted(base.items()):
        after = head.get(name)
        if after is None:
            found.append(f"{name}: deleted. A settled decision is superseded, never deleted")
            continue
        if frozen(after) == frozen(before):
            continue
        old, new = parse(before), parse(after)
        if old is None or new is None:
            found.append(f"{name}: rewritten. Only its status and superseded_by may change")
            continue
        changed = [
            key
            for key in dict.fromkeys([*old[0], *new[0]])
            if key not in MUTABLE and old[0].get(key) != new[0].get(key)
        ]
        if old[1] != new[1]:
            changed.append("its text")
        found.append(
            f"{name}: {', '.join(changed) or 'its front matter'} changed. Only its status and "
            "superseded_by may change: supersede it with a new decision instead"
        )
    return found


# ── the live folder ──────────────────────────────────────────────────────────────────────────


def test_every_decision_is_in_the_shape_its_readme_gives() -> None:
    files = in_tree(REPO)
    assert len(files) >= SEEDED, f"{FOLDER} holds {len(files)} decisions, seeded with {SEEDED}"
    assert problems(files) == []


def test_no_settled_decision_was_rewritten_or_deleted_since_the_base() -> None:
    base = base_commit(REPO, os.environ)
    if base is None:
        pytest.skip(
            f"no base to compare {FOLDER} with: BASE_SHA is unset and this clone has no "
            "merge-base with origin/main (a single-commit checkout)"
        )
    assert rewrites(at_commit(REPO, base), in_tree(REPO)) == []


# ── the rail, driven on a repository of its own ──────────────────────────────────────────────

_IDENT = (
    "-c",
    "user.name=Decision Test",
    "-c",
    "user.email=decisions@test.invalid",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "commit.gpgsign=false",
)

SETTLED = "2026-10-01-one-way.md"
SUCCESSOR = "2026-10-02-two-ways.md"


def _decision(
    title: str,
    day: str,
    *,
    status: str = "accepted",
    supersedes: str = "",
    superseded_by: str = "",
    text: str = "We do it one way.",
) -> str:
    fields = {
        "title": title,
        "date": day,
        "status": status,
        "supersedes": supersedes,
        "superseded_by": superseded_by,
    }
    head = [f"{key}: {value}" if value else f"{key}:" for key, value in fields.items()]
    return "\n".join(
        [
            "---",
            *head,
            "---",
            "",
            "## Decision",
            "",
            text,
            "",
            "## Why",
            "",
            "One way is easier to trust.",
            "",
            "## What it rules out",
            "",
            "- A second way.",
            "",
        ]
    )


def _run(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *_IDENT, *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return done.stdout.strip()


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / FOLDER / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _commit(root: Path, message: str) -> str:
    _run(root, "add", "-A")
    _run(root, "commit", "-q", "-m", message)
    return _run(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository whose ``main`` (and its ``origin/main``) holds this repository's decisions as
    they are now, checked out on a branch made from it."""
    root = tmp_path / "repo"
    root.mkdir()
    _run(root, "init", "-q", "-b", "main")
    _write(root, {README: "# Decisions\n", **in_tree(REPO)})
    _commit(root, "the settled decisions")
    _run(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    _run(root, "checkout", "-q", "-b", "change")
    return root


def _against_the_base(root: Path) -> list[str]:
    base = base_commit(root, {})
    assert base is not None, "the fixture's branch has a merge-base with origin/main"
    return rewrites(at_commit(root, base), in_tree(root))


def _accepted(root: Path) -> tuple[str, str]:
    """The first seeded decision still accepted, as (file name, text)."""
    for name, text in sorted(in_tree(root).items()):
        doc = parse(text)
        if doc is not None and doc[0]["status"] == "accepted":
            return name, text
    raise AssertionError(f"no accepted decision in {FOLDER} to plant against")


def _set(text: str, key: str, value: str) -> str:
    """*text* with its front-matter line for *key* set to *value*."""
    lines = text.split("\n")
    end = lines.index("---", 1)
    at = next(i for i in range(1, end) if lines[i].partition(":")[0] == key)
    lines[at] = f"{key}: {value}"
    return "\n".join(lines)


def test_editing_a_seed_decisions_text_in_place_is_red(repo: Path) -> None:
    name, text = _accepted(repo)
    edited = text.replace("## Decision\n\n", "## Decision\n\nNot any more.\n\n", 1)
    assert edited != text
    _write(repo, {name: edited})
    _commit(repo, "edit the decision in place")
    assert problems(in_tree(repo)) == [], "the shape alone cannot see an in-place edit"
    found = _against_the_base(repo)
    assert len(found) == 1 and found[0].startswith(f"{name}: its text changed"), found


def test_superseding_a_seed_decision_with_a_new_file_and_the_flip_is_green(repo: Path) -> None:
    name, text = _accepted(repo)
    match = NAME.match(name)
    assert match is not None
    slug = match.group(2)
    successor = "2026-10-20-a-new-answer.md"
    flipped = _set(_set(text, "status", "superseded"), "superseded_by", "a-new-answer")
    _write(
        repo,
        {name: flipped, successor: _decision("A new answer", "2026-10-20", supersedes=slug)},
    )
    _commit(repo, "supersede the decision with a new one")
    assert _against_the_base(repo) == []
    assert problems(in_tree(repo)) == []


def test_deleting_a_seed_decision_is_red(repo: Path) -> None:
    name, _ = _accepted(repo)
    _run(repo, "rm", "-q", f"{FOLDER}/{name}")
    _commit(repo, "delete the decision")
    found = _against_the_base(repo)
    assert len(found) == 1 and found[0].startswith(f"{name}: deleted"), found


def test_rewording_a_seed_decisions_title_is_red(repo: Path) -> None:
    name, text = _accepted(repo)
    doc = parse(text)
    assert doc is not None
    _write(repo, {name: _set(text, "title", f"{doc[0]['title']}, mostly")})
    found = _against_the_base(repo)
    assert len(found) == 1 and found[0].startswith(f"{name}: title changed"), found


def test_editing_the_readme_is_not_a_rewrite(repo: Path) -> None:
    _write(repo, {README: "# Decisions\n\nHow to add one.\n"})
    _commit(repo, "say more in the readme")
    assert _against_the_base(repo) == []


def test_a_slug_that_names_no_file_is_red() -> None:
    files = {SETTLED: _decision("One way", "2026-10-01", supersedes="no-such-decision")}
    assert problems(files) == [
        f"{SETTLED}: its supersedes names 'no-such-decision', and no decision here has it"
    ]


def test_a_flip_its_successor_does_not_answer_is_red() -> None:
    files = {
        SETTLED: _decision("One way", "2026-10-01", status="superseded", superseded_by="two-ways"),
        SUCCESSOR: _decision("Two ways", "2026-10-02"),
    }
    assert problems(files) == [
        f"{SETTLED}: its superseded_by names 'two-ways', which does not supersede 'one-way'"
    ]


def test_a_superseded_status_with_no_successor_is_red() -> None:
    found = problems({SETTLED: _decision("One way", "2026-10-01", status="superseded")})
    assert len(found) == 1 and "superseded exactly when superseded_by names" in found[0], found


def test_a_file_out_of_shape_is_red() -> None:
    found = problems(
        {
            "one-way.md": _decision("One way", "2026-10-01"),
            "2026-10-03-no-front-matter.md": "## Decision\n",
            "2026-10-04-wrong-day.md": _decision("Wrong day", "2026-10-05"),
        }
    )
    assert found == [
        "2026-10-03-no-front-matter.md: it opens with no front matter of `key: value` lines",
        "2026-10-04-wrong-day.md: its date is '2026-10-05', and its name says 2026-10-04",
        "one-way.md: a decision file is named <yyyy-mm-dd>-<slug>.md",
    ]


def test_the_base_is_the_commit_base_sha_names(repo: Path) -> None:
    main = _run(repo, "rev-parse", "main")
    assert base_commit(repo, {"BASE_SHA": main}) == main
    with pytest.raises(BaseNotFound):
        base_commit(repo, {"BASE_SHA": "0" * 40})


def test_a_clone_with_no_origin_main_has_no_base(repo: Path) -> None:
    _run(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert base_commit(repo, {}) is None

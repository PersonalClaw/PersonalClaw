"""Per-site baselines: a ratchet that lists its sites instead of counting them.

A census that compares a count with a fixed number goes red on whichever of two pull requests
merges second, though neither broke it: each fixed one site and added another, and the number
each was measured against no longer holds once both land. A site baseline holds the sites
themselves, one JSON object per line, each with the reason it is allowed::

    {"site": "src/personalclaw/x.py::Handler.get::web.json_response(body)", "reason": "..."}

The file is named ``*.sites.jsonl``, and the repository's ``.gitattributes`` merges every such
file with the ``sites-set`` driver (``scripts/git_sites_merge.py``), a three-way merge of two sets
of lines: two branches that each add a line merge without a conflict, a line either branch deleted
or replaced stays gone, and two branches that each change one site's line stop as a conflict.

:func:`check` compares the sites a rail found with the file, and fails, naming each site, on

- a site the file does not list: fix it, or add its line with a reason;
- a line whose site no longer exists: delete it, since the file only shrinks;
- a site listed twice, as a merge resolved by hand can leave it.

Order is not checked. The driver and ``--prune`` both write the lines sorted by site.
``python tests/site_baseline.py --prune`` runs the rails that hold a baseline with
``SITE_BASELINE_PRUNE=1``, and each check then deletes its stale and repeated lines and sorts the
file. It never adds a line.

**A branch cannot grow its own list.** Given the pull request's base commit in ``BASE_SHA``, as
the rails job's ``base`` step fetches it, the file is compared with the base's copy of it. A line
the base does not have is an added exception: it passes only with ``"added": "YYYY-MM-DD"`` and
its reason, and each is printed and written to the job summary (``GITHUB_STEP_SUMMARY``), where
the review reads it, a line break in its reason escaped. A line the base has and the branch
deleted is a fixed site. With no base, a local run, the file is compared with the working tree
only, and the check says so.

**Nor by its file.** Every baseline file is registered in :data:`REGISTRY`, one line each: its
path, the rail that checks it, what it replaces, and the ceiling it replaced at conversion. The
registry is a site baseline of files, compared with the base the same way (:func:`check_registry`).
A baseline file the base does not have is accepted only when its registry line is new in this
branch, carries ``added`` and a reason, and names a ceiling its line count does not exceed; the
job summary then names it against that ceiling and lists each of its sites with its reason. A
registered file that is renamed or deleted fails until its registry line goes too, and a registry
line may not raise its ceiling.

**A clone without the driver.** ``npm run hooks:install`` configures it in a clone; one that never
ran it merges a baseline as git merges text, so two edits of neighbouring lines stop as a conflict
to resolve by hand. A line a hand resolution brings back, one the base deleted, the check names as
stale and ``--prune`` deletes it; it never passes a site nobody listed.

A site in code is named ``path::qualified.name::<the node's source>`` (:func:`code_site`), its
whitespace collapsed, so an edit above it or another site in the same function leaves its name
alone. Repeats of one name are told apart, in source order, as ``#2``, ``#3`` (:func:`numbered`).
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

#: The repository's root. This module sits beside the suite, not in ``tests/rails``, so that both
#: its own tests in the suite and the rails that hold the real tree can import it.
REPO = Path(__file__).resolve().parents[1]

#: The merge driver every site baseline merges with: ``merge.sites-set.driver``, which
#: ``scripts/install_git_hooks.sh`` configures to run ``scripts/git_sites_merge.py``.
DRIVER = "sites-set"

#: The ``.gitattributes`` line that merges every site baseline with :data:`DRIVER`.
SITES_ATTRIBUTE = f"*.sites.jsonl merge={DRIVER}"

#: Every baseline file, one line each, from the repository's root.
REGISTRY = "tests/rails/baselines/index.sites.jsonl"

#: The rail that holds the registry to the tree; ``--prune`` runs it with the registered rails.
REGISTRY_RAIL = (
    "tests/rails/test_site_baseline_tree.py"
    "::test_every_site_baseline_in_the_repository_is_registered"
)

#: The commit a check compares against, as the gate passes it. Unset or empty: no base.
BASE_VARIABLE = "BASE_SHA"

#: The file GitHub gives each step for its job's summary; unset off GitHub.
SUMMARY_VARIABLE = "GITHUB_STEP_SUMMARY"

#: Set to ``1`` by ``--prune``: each check deletes its stale and repeated lines.
PRUNE_VARIABLE = "SITE_BASELINE_PRUNE"

PRUNE = "python tests/site_baseline.py --prune"

#: The default for ``base``: read :data:`BASE_VARIABLE`.
FROM_THE_GATE: Any = object()


@dataclass(frozen=True)
class Line:
    """One line of a baseline: the site, why it is allowed, and the day a branch added it."""

    site: str
    reason: str
    #: ``YYYY-MM-DD``, or ``None`` when the line has none (or one that is not a date).
    added: str | None
    fields: dict[str, Any]
    #: The line as the file holds it, so a prune rewrites nothing it keeps.
    raw: str


def _is_date(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def parse(text: str, label: str) -> tuple[list[Line], list[str]]:
    """The lines of a baseline's *text*, and what is wrong with it, each naming *label* and the
    line. A line that names a site counts as listed whatever else is wrong with it, so a missing
    reason reads as that and not as an unlisted site as well."""
    lines: list[Line] = []
    problems: list[str] = []
    if text and not text.endswith("\n"):
        problems.append(
            f"{label} does not end with a newline, so a line added after its last would join it"
        )
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        where = f"{label}:{number}"
        try:
            fields = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(f"{where} is not JSON ({exc.msg})")
            continue
        if not isinstance(fields, dict):
            problems.append(f"{where} is not a JSON object")
            continue
        site, reason, added = fields.get("site"), fields.get("reason"), fields.get("added")
        if not isinstance(site, str) or not site.strip():
            problems.append(f"{where} names no site")
            continue
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"{where}: site {site} has no reason")
            reason = ""
        if added is not None and not _is_date(added):
            problems.append(f"{where}: site {site} has `added` {added!r}, not a YYYY-MM-DD date")
        lines.append(Line(site, reason, added if _is_date(added) else None, fields, raw))
    return lines, problems


def load(path: Path) -> list[Line]:
    """A baseline's lines; an :class:`AssertionError` naming every malformed line."""
    lines, problems = parse(path.read_text(encoding="utf-8"), str(path))
    if problems:
        raise AssertionError("\n".join(problems))
    return lines


# ── the base ────────────────────────────────────────────────────────────────────────────────


def _base(base: Any) -> str | None:
    if base is FROM_THE_GATE:
        base = os.environ.get(BASE_VARIABLE, "")
    return base or None


def _prune(prune: bool | None) -> bool:
    return os.environ.get(PRUNE_VARIABLE) == "1" if prune is None else prune


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8"
    )


@cache
def _require(root: Path, base: str) -> None:
    if _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode:
        raise AssertionError(
            f"the base {base} is not in this clone, so nothing can be compared with it: fetch "
            f"it first (git fetch origin {base})"
        )


@cache
def _at(root: Path, base: str, path: str) -> str | None:
    """*path* as the base commit has it; ``None`` when the base has no such file."""
    _require(root, base)
    shown = _git(root, "show", f"{base}:{path}")
    return shown.stdout if shown.returncode == 0 else None


def _lines_at(root: Path, base: str, path: str) -> list[Line] | None:
    text = _at(root, base, path)
    return None if text is None else parse(text, f"{path} at {base[:12]}")[0]


def _compared(base: str | None) -> str:
    return (
        f"compared with the base {base[:12]}"
        if base
        else "compared with the working tree (no base)"
    )


# ── one baseline ────────────────────────────────────────────────────────────────────────────


def _relative(path: str | Path, root: Path) -> str:
    path = Path(path)
    return (path.relative_to(root) if path.is_absolute() else path).as_posix()


def _rewrite(file: Path, rel: str, lines: list[Line], keep: set[str]) -> list[Line]:
    """Keep the first line of each site in *keep*, sorted; print what went."""
    kept: dict[str, Line] = {}
    for line in lines:
        if line.site in keep and line.site not in kept:
            kept[line.site] = line
        else:
            print(f"pruned {rel}: {line.site}")
    ordered = sorted(kept.values(), key=lambda line: line.site)
    file.write_text("".join(line.raw + "\n" for line in ordered), encoding="utf-8")
    return ordered


@dataclass(frozen=True)
class _Words:
    """How a check names each failure: for the sites of a baseline, or the files of the registry."""

    new: str
    stale: str
    repeated: str
    added: str


_SITES = _Words(
    new="Site {site} is not listed in {rel}: fix it, or add a line for it with a reason",
    stale="Site {site} is listed in {rel} but no longer exists: delete its line ({prune})",
    repeated=(
        "Site {site} is listed {count} times in {rel}, as a merge resolved by hand can leave "
        "it: keep one line ({prune})"
    ),
    added=(
        "Site {site} was added to the baseline in this branch; add `added` and a reason, or fix "
        "the site"
    ),
)

_FILES = _Words(
    new=(
        "{site} is not registered in {rel}: add its line, naming its rail, what it replaces and "
        "that ceiling, with `added` and a reason"
    ),
    stale="{site} is registered in {rel} but does not exist: delete its line ({prune})",
    repeated="{site} is registered {count} times in {rel}: keep one line ({prune})",
    added="{site} was added to the registry in this branch; add `added` and a reason",
)


def _compare(
    found: Iterable[str], file: Path, rel: str, prune: bool, words: _Words
) -> tuple[list[Line], list[str]]:
    """The file's lines and every way they disagree with *found*, before any base."""
    sites = Counter(found)
    failures = [
        f"the rail found {site} {count} times: tell repeats apart (site_baseline.numbered)"
        for site, count in sorted(sites.items())
        if count > 1
    ]
    if not file.is_file():
        return [], [*failures, f"{rel} does not exist"]
    text = file.read_text(encoding="utf-8")
    lines, problems = parse(text, rel)
    failures += problems
    listed = Counter(line.site for line in lines)
    readable = len(lines) == sum(1 for raw in text.splitlines() if raw.strip())
    if prune and readable:
        lines = _rewrite(file, rel, lines, set(sites))
        listed = Counter(line.site for line in lines)
    failures += [words.new.format(site=site, rel=rel) for site in sorted(set(sites) - set(listed))]
    failures += [
        words.stale.format(site=site, rel=rel, prune=PRUNE)
        for site in sorted(set(listed) - set(sites))
    ]
    failures += [
        words.repeated.format(site=site, rel=rel, count=count, prune=PRUNE)
        for site, count in sorted(listed.items())
        if count > 1
    ]
    return lines, failures


def _added_since(
    lines: list[Line], at_base: list[Line], present: set[str], words: _Words
) -> tuple[list[Line], list[str]]:
    """The lines the base does not have, each passing only with ``added`` and a reason. A line
    whose site is gone is stale, and is named as that alone: a merge resolved by hand that brings
    back a line main deleted reads as new against main, and the fix is to delete it."""
    known = {line.site for line in at_base}
    fresh = [line for line in lines if line.site not in known and line.site in present]
    added = [line for line in fresh if line.added and line.reason]
    failures = [words.added.format(site=line.site) for line in fresh if line not in added]
    return added, failures


#: A character that could end a summary line: a line break, or any other control character.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _one_line(text: str) -> str:
    """*text* with each control character written as its escape (``\\n`` for a line break), so a
    reason, which is free text, stays on its own line and cannot end the summary's fence."""
    return _CONTROL.sub(lambda char: repr(char.group())[1:-1], text)


def _summarise(heading: str, texts: list[str]) -> None:
    """Print each of *texts*, and write them under *heading* to the job summary when there is
    one: a passing test's output is captured, so the summary is where the review sees them."""
    texts = [_one_line(text) for text in texts]
    for text in texts:
        print(text)
    summary = os.environ.get(SUMMARY_VARIABLE)
    if not (texts and summary):
        return
    with open(summary, "a", encoding="utf-8") as out:
        out.write(f"### {heading}\n\n~~~text\n" + "".join(t + "\n" for t in texts) + "~~~\n\n")


def _report(rel: str, compared: str, added: list[Line]) -> None:
    """Each line this branch added to *rel*, with its date and reason (:func:`_summarise`)."""
    count = f"{len(added)} line{'s' if len(added) > 1 else ''}"
    _summarise(
        f"{rel}: {count} this branch adds ({compared})",
        [f"added {line.added}: {line.site}: {line.reason}" for line in added],
    )


def _fail(rel: str, compared: str, failures: list[str]) -> None:
    if failures:
        raise AssertionError("\n".join([f"{rel} ({compared}):", *("  " + f for f in failures)]))


def _registration(root: Path, rel: str, lines: list[Line], base: str | None) -> list[str]:
    """What is wrong with *rel*'s place in the registry: unregistered, or new in this branch
    without a registry line that admits it."""
    registry = root / REGISTRY
    entries = parse(registry.read_text(encoding="utf-8"), REGISTRY)[0] if registry.is_file() else []
    entry = next((line for line in entries if line.site == rel), None)
    if entry is None:
        return [_FILES.new.format(site=rel, rel=REGISTRY)]
    if not base or _at(root, base, rel) is not None:
        return []
    at_base = _lines_at(root, base, REGISTRY) or []
    if any(line.site == rel for line in at_base) or not (entry.added and entry.reason):
        return [
            f"{rel} is a new baseline file in this branch; its line in {REGISTRY} must be new "
            "here too, with `added` and a reason"
        ]
    ceiling = entry.fields.get("ceiling")
    if not isinstance(ceiling, int) or len(lines) > ceiling:
        return [
            f"{rel} is a new baseline of {len(lines)} sites, over the ceiling {ceiling} its "
            "registry line names"
        ]
    # Every line of a file the base lacks is one the branch adds: each reaches the summary.
    _summarise(
        f"{rel}: a new baseline file this branch adds ({_compared(base)})",
        [
            f"{rel}: a new baseline of {len(lines)} sites (it replaces "
            f"{entry.fields.get('replaces')}, ceiling {ceiling})",
            *(f"{line.site}: {line.reason}" for line in lines),
        ],
    )
    return []


def check(
    found: Iterable[str],
    path: str | Path,
    base: Any = FROM_THE_GATE,
    *,
    root: Path = REPO,
    prune: bool | None = None,
) -> list[Line]:
    """Hold the sites a rail *found* to the baseline at *path*; return the lines this branch
    added, each printed and written to the job summary (:func:`_report`). Raises
    :class:`AssertionError` naming every failure.

    *base* is the commit to compare with: by default the gate's (:data:`BASE_VARIABLE`); ``None``
    for none. *prune*, by default :data:`PRUNE_VARIABLE`, deletes stale and repeated lines first.
    """
    root = Path(root)
    found = list(found)
    rel = _relative(path, root)
    base = _base(base)
    compared = _compared(base)
    print(f"{rel}: {compared}")
    lines, failures = _compare(found, root / rel, rel, _prune(prune), _SITES)
    failures += _registration(root, rel, lines, base)
    added: list[Line] = []
    at_base = _lines_at(root, base, rel) if base else None
    if at_base is not None:
        added, more = _added_since(lines, at_base, set(found), _SITES)
        failures += more
    _report(rel, compared, added)
    _fail(rel, compared, failures)
    return added


# ── the registry ────────────────────────────────────────────────────────────────────────────


def check_registry(
    files: Iterable[str | Path],
    base: Any = FROM_THE_GATE,
    *,
    root: Path = REPO,
    prune: bool | None = None,
) -> list[Line]:
    """Hold the registry to the baseline *files* a rail found in the tree: each is registered,
    every registered file is among them, every line names its rail, what it replaces and a
    ceiling, and against *base* nothing grew. Returns the registry lines this branch added, each
    printed and written to the job summary (:func:`_report`)."""
    root = Path(root)
    base = _base(base)
    compared = _compared(base)
    print(f"{REGISTRY}: {compared}")
    files = sorted({_relative(path, root) for path in files} - {REGISTRY})
    failures: list[str] = []
    if not (root / REGISTRY).is_file():
        if base and _at(root, base, REGISTRY) is not None:
            failures.append(f"{REGISTRY} was deleted in this branch")
        failures += [f"{path} is not registered: there is no {REGISTRY}" for path in files]
        _fail(REGISTRY, compared, failures)
        return []
    lines, failures = _compare(files, root / REGISTRY, REGISTRY, _prune(prune), _FILES)
    for line in lines:
        rail, replaces, ceiling = (line.fields.get(key) for key in ("rail", "replaces", "ceiling"))
        if not isinstance(rail, str) or not (root / rail).is_file():
            failures.append(f"{line.site}: its rail {rail!r} is not a file in the repository")
        if not isinstance(replaces, str) or not replaces.strip():
            failures.append(f"{line.site}: its line names nothing it replaces")
        if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 0:
            failures.append(f"{line.site}: its ceiling {ceiling!r} is not a count")
    added: list[Line] = []
    at_base = _lines_at(root, base, REGISTRY) if base else None
    if at_base is not None:
        added, more = _added_since(lines, at_base, set(files), _FILES)
        failures += more
        before = {line.site: line.fields.get("ceiling") for line in at_base}
        for line in lines:
            was, now = before.get(line.site), line.fields.get("ceiling")
            if isinstance(was, int) and isinstance(now, int) and now > was:
                failures.append(f"{line.site} raises its ceiling from {was} to {now}")
    _report(REGISTRY, compared, added)
    _fail(REGISTRY, compared, failures)
    return added


# ── the merge driver ────────────────────────────────────────────────────────────────────────

#: Where a baseline may sit, for asking git how it merges one: beside the rails, and beside a web
#: test.
_PROBES = ("tests/rails/baselines/probe.sites.jsonl", "web/src/probe.sites.jsonl")


def attribute_problems(root: Path = REPO) -> list[str]:
    """Why git at *root* would not merge a site baseline with :data:`DRIVER`: its
    ``.gitattributes`` lacks :data:`SITES_ATTRIBUTE` as a line of its own, or git, asked about a
    baseline, answers anything but :data:`DRIVER` (a later line that overrides it, say)."""
    attributes = Path(root) / ".gitattributes"
    if not attributes.is_file():
        return [f"{root} has no .gitattributes, so no site baseline merges with {DRIVER}"]
    lines = [line.strip() for line in attributes.read_text(encoding="utf-8").splitlines()]
    problems = []
    if SITES_ATTRIBUTE not in lines:
        problems.append(f".gitattributes does not hold the line `{SITES_ATTRIBUTE}`")
    asked = _git(Path(root), "check-attr", "merge", "--", *_PROBES)
    if asked.returncode:
        return [*problems, f"git check-attr failed: {asked.stderr.strip()}"]
    for answer in asked.stdout.splitlines():
        path, _, value = answer.partition(": merge: ")
        if value != DRIVER:
            problems.append(f"git merges {path} with merge={value}, not {DRIVER}")
    return problems


# ── names for sites in code ─────────────────────────────────────────────────────────────────


def code_site(path: str, scope: str, node: ast.AST) -> str:
    """A site in code: ``path::qualified.name::<the node's source>``, whitespace collapsed."""
    return f"{path}::{scope}::{' '.join(ast.unparse(node).split())}"


def numbered(names: Iterable[str]) -> list[str]:
    """*names* in order, each repeat of one told apart as ``#2``, ``#3``."""
    seen: Counter[str] = Counter()
    out: list[str] = []
    for name in names:
        seen[name] += 1
        out.append(name if seen[name] == 1 else f"{name} #{seen[name]}")
    return out


def scoped(tree: ast.AST, scope: str = "<module>") -> Iterator[tuple[ast.AST, str]]:
    """Every node under *tree* in source order, with the qualified name of the function or
    class it is in (``Class.method``, ``outer.inner``), ``<module>`` at the top."""
    for node in ast.iter_child_nodes(tree):
        yield node, scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = node.name if scope == "<module>" else f"{scope}.{node.name}"
            yield from scoped(node, inner)
        else:
            yield from scoped(node, scope)


# ── the command line ────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tests/site_baseline.py",
        description="Delete the stale and repeated lines of the site baselines; never add one.",
    )
    parser.add_argument("--prune", action="store_true", help="run the rails in prune mode")
    parser.add_argument("rails", nargs="*", help="the rails to run (default: every registered one)")
    parser.add_argument("--root", default=str(REPO), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.prune:
        parser.print_help()
        return 2
    root = Path(args.root)
    rails = list(args.rails)
    if not rails:
        registry = root / REGISTRY
        lines = (
            parse(registry.read_text(encoding="utf-8"), REGISTRY)[0] if registry.is_file() else []
        )
        rails = list(dict.fromkeys(str(line.fields.get("rail")) for line in lines))
        rails.append(REGISTRY_RAIL)
    env = {**os.environ, PRUNE_VARIABLE: "1"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *rails], cwd=root, env=env
    ).returncode


if __name__ == "__main__":
    sys.exit(main())

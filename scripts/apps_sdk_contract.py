#!/usr/bin/env python3
"""Run the first-party apps' SDK contract checks against THIS core. ``ci.yml``'s ``apps-contract``.

Core and the apps ship separately, so a core change can break an app with nothing in core's CI
noticing: #3599 changed ``compress_thread_history``'s parameter type and the Slack app lost every
restored thread's history. PersonalClawApps #124 added the checks that would have caught it
(``apps_testkit/sdk_contract.py``); this runs them from core, against the core under review.

Three checks, over a checkout of PersonalClawApps:

1. **Every app's SDK imports resolve and are published, and every call it makes binds** — the
   testkit's static rails, over every bundle. A problem is charged to this change when it involves
   an SDK symbol whose snapshot record the change touched (``src/personalclaw/sdk/signatures.json``
   against the base ref's); one on an unchanged symbol is an app's own, reported and not charged —
   a rail that scans the apps must not make one app's bug every core PR's red. A problem that maps
   to no SDK symbol at all is charged: nothing shows it is pre-existing.
2. **An SDK change names the apps it affects in its CHANGELOG headline** — the apps that import
   a symbol the change removed or changed in a way an old caller can notice, each by its bundle
   name in backticks or by a backticked family glob that matches only affected apps. Additions
   affect no app, but still need an entry. The same rule CONTRIBUTING.md#changelog states for
   reviewers.
3. **Each bundle's own contract test** (``<bundle>/tests/test_sdk_contract.py``, the convention
   #124 started) runs against this core, with the bundle's declared dependencies installed when
   ``--install-deps`` is given. Not each app's whole suite: the contract only.

Usage::

    python scripts/apps_sdk_contract.py --apps-dir ../PersonalClawApps [--base-ref origin/main]
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import sdk_signature_snapshot as snap  # noqa: E402

SNAPSHOT_REL = "src/personalclaw/sdk/signatures.json"
CONTRACT_TEST = Path("tests") / "test_sdk_contract.py"


@dataclasses.dataclass
class Problem:
    app: str
    where: str
    text: str
    #: SDK symbols (``personalclaw.sdk.<module>.<name>``) the problem involves; empty = unmapped.
    symbols: frozenset[str]


# ── the base snapshot and what changed ───────────────────────────────────────────────────────


def git_show(ref: str, rel: str) -> str | None:
    """``rel`` at ``ref``, or ``None`` when the ref (or the file at it) does not exist."""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{rel}"], cwd=REPO, capture_output=True, text=True, check=False
    )
    return proc.stdout if proc.returncode == 0 else None


def expand_aliases(symbols: set[str], snapshot: dict[str, Any]) -> set[str]:
    """``symbols`` plus every name that is an ``alias`` of one of them (one object, two names)."""
    out = set(symbols)
    for key, rec in snapshot.items():
        if rec.get("kind") == "alias" and rec.get("of") in symbols:
            out.add(key)
    return out


# ── the apps' census, mapped onto SDK symbols ────────────────────────────────────────────────


def _symbol_index() -> dict[int, set[str]]:
    """``id(object)`` → the SDK names that publish it (the live SDK the census resolved against)."""
    index: dict[int, set[str]] = {}
    for key, obj in snap.published().items():
        index.setdefault(id(obj), set()).add(key)
    return index


def _call_symbols(call: Any, index: dict[int, set[str]]) -> set[str]:
    owner = call.owner if call.owner is not None else getattr(call.obj, "owner", None)
    target = owner if owner is not None else call.obj
    return set(index.get(id(target), set()))


def _import_symbols(imp: Any, published: Iterable[str]) -> set[str]:
    if imp.name:
        return {f"{imp.module}.{imp.name}"}
    return {key for key in published if key.startswith(f"{imp.module}.")}


def bundles(apps_dir: Path) -> list[Path]:
    return sorted(p.parent.resolve() for p in apps_dir.glob("*/app.json"))


def census_problems(apps_dir: Path, testkit: Any) -> list[Problem]:
    """Every unpublished import and unbindable call in every bundle, with the symbols involved."""
    index = _symbol_index()
    published = list(snap.published())
    problems: list[Problem] = []
    for app in bundles(apps_dir):
        census = testkit.census(app)
        by_where: dict[str, set[str]] = {}
        for imp in census.imports:
            where = f"{imp.path.relative_to(app.parent)}:{imp.lineno}"
            by_where.setdefault(where, set()).update(_import_symbols(imp, published))
        for call in census.calls:
            where = f"{call.path.relative_to(app.parent)}:{call.lineno}"
            by_where.setdefault(where, set()).update(_call_symbols(call, index))
        for line in testkit.unpublished_imports(app) + testkit.unbindable_calls(app):
            where = line.split(": ", 1)[0]
            problems.append(Problem(app.name, where, line, frozenset(by_where.get(where, ()))))
    return problems


def charged_to_change(problem: Problem, changed: set[str], *, have_base: bool) -> bool:
    """Whether ``problem`` is this change's doing rather than an app's own.

    It is when it involves an SDK symbol the change touched, or when it maps to no SDK symbol at
    all (nothing shows it predates the change). Without a base snapshot nothing can be measured,
    so nothing is charged — the one state the first run of this job is in.
    """
    if not have_base:
        return False
    return not problem.symbols or bool(problem.symbols & changed)


def affected_apps(apps_dir: Path, testkit: Any, symbols: set[str]) -> dict[str, list[str]]:
    """``{app: [where …]}`` for every bundle that imports or calls one of ``symbols``."""
    index = _symbol_index()
    published = list(snap.published())
    out: dict[str, list[str]] = {}
    for app in bundles(apps_dir):
        census = testkit.census(app)
        sites: list[str] = []
        for imp in census.imports:
            if _import_symbols(imp, published) & symbols:
                sites.append(f"{imp.path.relative_to(app.parent)}:{imp.lineno}")
        for call in census.calls:
            if _call_symbols(call, index) & symbols:
                sites.append(f"{call.path.relative_to(app.parent)}:{call.lineno}")
        if sites:
            out[app.name] = sorted(set(sites))
    return out


# ── the CHANGELOG rule ───────────────────────────────────────────────────────────────────────

#: An entry line and its headline: the CHANGELOG is headline-only, ``- **<headline>**``.
_HEADLINE = re.compile(r"^- \*\*(.+)\*\*$")
#: A code span in a headline — how an app is named: `slack-channel`.
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
#: A code span that is a family glob over bundle names — the compact form of a long list:
#: `*-channel` names every bundle whose name ends in `-channel`.
_APP_GLOB = re.compile(r"[a-z0-9-]*[*?][a-z0-9*?-]*")


def added_headlines(base_text: str | None) -> list[str]:
    """The headlines of the entries the working tree's CHANGELOG.md has that the base's does not
    (a multiset difference of lines: an entry is one line)."""
    head = collections.Counter((REPO / "CHANGELOG.md").read_text(encoding="utf-8").splitlines())
    base = collections.Counter((base_text or "").splitlines())
    return [match.group(1) for line in (head - base).elements() if (match := _HEADLINE.match(line))]


def changelog_violations(
    changes: list[snap.Change],
    affected: dict[str, list[str]],
    headlines: list[str],
    apps: Iterable[str],
) -> list[str]:
    """What the new CHANGELOG headlines fail to say about an SDK change.

    An app is named by its bundle name in backticks (`slack-channel`), or by a backticked glob
    over bundle names (`*-channel`). A glob is an exact list written short, so it may match only
    apps the change affects: one that also matches an unaffected app, or no app at all, is
    refused. Only headlines count: the CHANGELOG has no bodies.
    """
    if not changes:
        return []
    if not headlines:
        return [
            f"the SDK changed ({len(changes)} symbol(s)) and CHANGELOG.md has no new entry "
            f"(a `- **<headline>**` line); {snap.REGENERATE}"
        ]
    spans = {span for headline in headlines for span in _CODE_SPAN.findall(headline)}
    globs = sorted(span for span in spans if _APP_GLOB.fullmatch(span))
    known = sorted(set(apps) | set(affected))
    out = [
        f"no new CHANGELOG headline names `{app}`, which uses what changed at "
        f"{', '.join(sites[:3])}{' …' if len(sites) > 3 else ''}; name it in backticks, or "
        "with a backticked glob such as `*-channel` for a family of apps"
        for app, sites in sorted(affected.items())
        if app not in spans and not any(fnmatch.fnmatchcase(app, glob) for glob in globs)
    ]
    for glob in globs:
        matched = [app for app in known if fnmatch.fnmatchcase(app, glob)]
        extra = [app for app in matched if app not in affected]
        if not matched:
            out.append(f"the CHANGELOG headline's `{glob}` names no app in the apps checkout")
        elif extra:
            out.append(
                f"the CHANGELOG headline's `{glob}` also names {', '.join(extra)}, which "
                "use(s) nothing that changed; a glob may match only the apps the change affects"
            )
    return out


# ── each bundle's own contract test ──────────────────────────────────────────────────────────


def run_contract_tests(apps_dir: Path, *, install_deps: bool) -> list[str]:
    """Run every ``<bundle>/tests/test_sdk_contract.py``; one failure line per red bundle."""
    failures: list[str] = []
    env = {**os.environ, "PERSONALCLAW_SKIP_APP_BACKENDS": "1"}
    for app in bundles(apps_dir):
        test = app / CONTRACT_TEST
        if not test.is_file():
            continue
        print(f"\n── {app.name}: {CONTRACT_TEST} ──", flush=True)
        manifest = json.loads((app / "app.json").read_text(encoding="utf-8"))
        deps = (manifest.get("dependencies") or {}).get("pythonDependencies") or []
        if install_deps and deps:
            subprocess.run(["uv", "pip", "install", "--python", sys.executable, *deps], check=True)
        # `-c os.devnull`: CI checks the apps out INSIDE this repo, where pytest would otherwise
        # adopt core's pyproject (`-n auto`, coverage) as the apps' test config.
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-c",
                os.devnull,
                "--rootdir",
                str(apps_dir),
                "-p",
                "no:cacheprovider",
                str(test),
            ],
            cwd=apps_dir,
            env=env,
            check=False,
        )
        if proc.returncode != 0:
            hint = (
                ""
                if install_deps or not deps
                else f" (if its declared dependencies {deps} are missing, pass --install-deps)"
            )
            failures.append(f"{app.name}: {CONTRACT_TEST} failed against this core{hint}")
    return failures


# ── the command ──────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apps-dir", type=Path, required=True, help="a PersonalClawApps checkout")
    parser.add_argument(
        "--base-ref", default="", help="the ref this change is measured against (the PR base)"
    )
    parser.add_argument(
        "--install-deps",
        action="store_true",
        help="install each contract-tested bundle's declared dependencies first (CI)",
    )
    args = parser.parse_args(argv)
    apps_dir = args.apps_dir.resolve()
    if not (apps_dir / "apps_testkit" / "sdk_contract.py").is_file():
        print(f"{apps_dir} is not a PersonalClawApps checkout with apps_testkit/sdk_contract.py")
        return 2
    sys.path.insert(0, str(apps_dir))
    from apps_testkit import sdk_contract as testkit  # type: ignore[import-not-found]

    live = snap.snapshot()
    base_text = git_show(args.base_ref, SNAPSHOT_REL) if args.base_ref else None
    base = json.loads(base_text) if base_text else None
    changes = snap.diff(base, live) if base is not None else []
    changed = expand_aliases({c.symbol for c in changes}, {**(base or {}), **live})
    breaking = expand_aliases(
        {c.symbol for c in changes if not c.additive}, {**(base or {}), **live}
    )

    lines: list[str] = []
    failures: list[str] = []
    problems = census_problems(apps_dir, testkit)
    n_apps = len(bundles(apps_dir))
    if base is None:
        lines.append(
            f"No SDK snapshot at {args.base_ref or '(no base ref)'} to measure against, so no "
            "problem below is charged to this change."
        )
    for p in problems:
        caused = charged_to_change(p, changed, have_base=base is not None)
        (failures if caused else lines).append(f"{'❌' if caused else '⚠️  pre-existing'} {p.text}")
    affected = affected_apps(apps_dir, testkit, breaking) if breaking else {}
    for change in changes:
        for brk in change.silent_breaks:
            failures.append(f"❌ silent break: {brk}")
    if base is not None:
        failures += [
            f"❌ {v}"
            for v in changelog_violations(
                changes,
                affected,
                added_headlines(git_show(args.base_ref, "CHANGELOG.md")),
                [app.name for app in bundles(apps_dir)],
            )
        ]
    failures += [f"❌ {f}" for f in run_contract_tests(apps_dir, install_deps=args.install_deps)]

    summary = [
        f"## Apps' SDK contract: {n_apps} bundles against this core",
        f"SDK changes since {args.base_ref or 'the base'}: {len(changes)} "
        f"({len(changes) - len([c for c in changes if c.additive])} not additive)",
    ]
    for app, sites in sorted(affected.items()):
        summary.append(f"affected: {app} — {', '.join(sites[:5])}{' …' if len(sites) > 5 else ''}")
    summary += lines + failures
    summary.append("RESULT: " + ("FAIL" if failures else "pass"))
    text = "\n".join(summary)
    print(text)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

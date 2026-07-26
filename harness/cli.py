"""``python -m harness`` — validate | explain | run | scan.

The agent-facing surface of the self-development harness:

- ``validate`` — shape-validate every spec + resolve references (dangling test node-ids,
  unknown profiles/scanner-checks). The same-PR gate and CI call this.
- ``explain <task>`` — print the concrete commands + rules + tests a task must satisfy
  (the "what do I owe before this is done" surface).
- ``run <task> | --diff`` — execute the union of required profiles. ``--diff`` forces
  profiles from the touched areas (the diff can add requirements, never remove them) and
  warns when a fix-shaped change touches no spec (the same-PR rule).
- ``scan [--diff]`` — run the static boundary scanner over the whole tree or the diff.
- ``replay`` — gate recorded event-trace scenarios against checked-in baselines.
- ``resume-audit <loop_id>`` — check a loop resumes from persisted state alone (§2.4).

Exit codes: 0 == clean/pass, 1 == validation errors or a failed command, 2 == usage error
(unknown task id, no spec set). Warnings print but do not change the exit code.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from harness import scanner
from harness.diff import (
    commit_subjects_since,
    compute_diff,
    has_fix_shaped_commit,
    touches_specs,
)
from harness.profiles import get_profile, resolve_commands
from harness.selection import forced_profiles
from harness.specs import (
    KIND_TASK,
    Spec,
    SpecError,
    load_specs,
    specs_root,
    validate_all,
)
from harness.validate_refs import validate_refs

_OK = "✅"
_FAIL = "❌"
_WARN = "⚠️ "


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_or_die() -> list[Spec]:
    """Load the spec set, printing a clean error and exiting 2 on a malformed file."""
    try:
        return load_specs()
    except SpecError as exc:
        print(f"{_FAIL} {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


# ── validate ──────────────────────────────────────────────────────────────────


def cmd_validate(args: argparse.Namespace) -> int:
    specs = _load_or_die()
    if not specs:
        print(f"{_WARN}no specs found under {specs_root()}")
        return 0

    issues = validate_all(specs)
    issues.extend(
        validate_refs(specs, check_tests=not args.fast, known_scanner_checks=scanner.known_checks())
    )

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    root = _repo_root()
    for issue in issues:
        marker = _FAIL if issue.level == "error" else _WARN
        try:
            rel = issue.path.relative_to(root)
        except ValueError:
            rel = issue.path
        print(f"{marker} {rel}: {issue.message}", file=sys.stderr if errors else sys.stdout)

    n = len(specs)
    if errors:
        print(f"\n{_FAIL} {len(errors)} error(s), {len(warnings)} warning(s) across {n} specs")
        return 1
    print(f"{_OK} {n} specs valid ({len(warnings)} warning(s))")
    return 0


# ── explain ─────────────────────────────────────────────────────────────────


def _find_task(specs: list[Spec], task_id: str) -> Spec | None:
    for s in specs:
        if s.kind == KIND_TASK and s.id == task_id:
            return s
    return None


def _resolved_for_task(specs: list[Spec], task: Spec) -> tuple[list[str], list[str], list[str]]:
    """Union a task's requirements with those of its referenced scenario.

    Returns (profiles, rule_ids, test_node_ids), each de-duplicated in declaration order.
    """
    by_id = {s.id: s for s in specs if s.id}
    profiles: list[str] = []
    rules: list[str] = []
    tests: list[str] = []

    def add_from(spec: Spec) -> None:
        for p in spec.get_list("requiredProfiles"):
            if p not in profiles:
                profiles.append(p)
        for r in spec.get_list("requiredRules"):
            if r not in rules:
                rules.append(r)
        for t in spec.get_list("requiredTests"):
            if t not in tests:
                tests.append(t)

    scenario_id = task.meta.get("scenario")
    if scenario_id and str(scenario_id) in by_id:
        add_from(by_id[str(scenario_id)])
    add_from(task)

    # A referenced rule contributes its own requiredTests (a rule names the tests that
    # prove it), so a task inherits the proof obligations of every rule it must satisfy.
    for rid in list(rules):
        rule = by_id.get(rid)
        if rule:
            for t in rule.get_list("requiredTests"):
                if t not in tests:
                    tests.append(t)

    return profiles, rules, tests


def cmd_explain(args: argparse.Namespace) -> int:
    specs = _load_or_die()
    task = _find_task(specs, args.task)
    if task is None:
        print(f"{_FAIL} no task spec with id {args.task!r}", file=sys.stderr)
        return 2

    profiles, rules, tests = _resolved_for_task(specs, task)
    print(f"Task {task.id}: {task.meta.get('title', '')}")
    intent = task.meta.get("intent")
    if intent:
        print(f"  intent: {intent}")
    print(f"  rules to satisfy:   {', '.join(rules) or '(none)'}")
    print(f"  profiles required:  {', '.join(profiles) or '(none)'}")
    print(f"  tests to pass:      {', '.join(tests) or '(none)'}")
    print("  commands:")
    cmds = resolve_commands(profiles, tests)
    if not cmds:
        print("    (none resolved — check requiredProfiles / requiredTests)")
    for rc in cmds:
        print(f"    [{rc.profile}] {rc.command}")
    # Negative acceptance is the clause LEDGER prose always drops — surface it loudly.
    acc = task.meta.get("acceptance")
    if isinstance(acc, dict) and acc.get("negative"):
        print("  negative acceptance (must NOT happen):")
        neg = acc["negative"]
        for clause in neg if isinstance(neg, list) else [neg]:
            print(f"    - {clause}")
    return 0


# ── run ───────────────────────────────────────────────────────────────────────


def _execute(commands: list[str], *, dry_run: bool) -> int:
    """Run each command from the repo root, streaming output. Stops at the first failure
    (a later command shouldn't mask an earlier break). Returns the aggregate exit code."""
    root = _repo_root()
    for cmd in commands:
        print(f"\n{'» (dry-run) ' if dry_run else '» '}{cmd}")
        if dry_run:
            continue
        proc = subprocess.run(cmd, cwd=root, shell=True, check=False)
        if proc.returncode != 0:
            print(f"{_FAIL} command failed (exit {proc.returncode}): {cmd}", file=sys.stderr)
            return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if args.diff:
        return _run_diff(args)

    if not args.task:
        print(f"{_FAIL} run needs a task id (or --diff); e.g. `run T1.1`", file=sys.stderr)
        return 2

    specs = _load_or_die()
    task = _find_task(specs, args.task)
    if task is None:
        print(f"{_FAIL} no task spec with id {args.task!r}", file=sys.stderr)
        return 2

    profiles, _rules, tests = _resolved_for_task(specs, task)
    # A profile that needs test node-ids but has none resolved is a defective task spec.
    for name in profiles:
        prof = get_profile(name)
        if prof and prof.needs_tests and not tests:
            print(
                f"{_FAIL} profile {name!r} needs test node-ids but task {task.id} "
                f"resolves none (add requiredTests)",
                file=sys.stderr,
            )
            return 2

    cmds = [rc.command for rc in resolve_commands(profiles, tests)]
    scan_rc = _run_scan_if_selected(profiles)
    if not cmds:
        print(f"{_WARN}no commands resolved for task {task.id}")
        return scan_rc
    return _execute(cmds, dry_run=args.dry_run) or scan_rc


def _run_diff(args: argparse.Namespace) -> int:
    """Diff-aware run: force profiles from the touched areas, union with any task spec's
    declared profiles, then execute. The diff can only ADD profiles, never remove."""
    diff = compute_diff()
    if not diff.files:
        print(f"{_WARN}no changed files vs {diff.base[:12]} — nothing to run")
        return 0

    forced = forced_profiles(diff.files)
    print(f"changed files vs {diff.base[:12]}: {len(diff.files)}")
    for fp in forced:
        print(f"  forced [{fp.profile}]: {'; '.join(fp.reasons)}")

    # Same-PR rule (§1.4): a fix-shaped change should add/update a spec in the same change,
    # moving "every fixed bug becomes permanent" from private memory into the versioned repo.
    subjects = commit_subjects_since(_repo_root(), diff.base)
    if has_fix_shaped_commit(subjects) and not touches_specs(diff.files):
        print(
            f"{_WARN}same-PR rule: this looks like a fix "
            f"(commit subject matches fix/bug/regression) but touches no harness/specs/ — "
            f"add or update a rule/scenario spec so the fixed bug becomes a permanent check."
        )

    profiles: list[str] = []
    tests: list[str] = []
    # A task spec can add more (its profiles/tests) on top of the forced set.
    if args.task:
        specs = _load_or_die()
        task = _find_task(specs, args.task)
        if task is None:
            print(f"{_FAIL} no task spec with id {args.task!r}", file=sys.stderr)
            return 2
        profiles, _rules, tests = _resolved_for_task(specs, task)
    for fp in forced:
        if fp.profile not in profiles:
            profiles.append(fp.profile)

    cmds = [rc.command for rc in resolve_commands(profiles, tests)]
    scan_rc = _run_scan_if_selected(profiles, diff=diff)
    if not cmds:
        return scan_rc
    return _execute(cmds, dry_run=args.dry_run) or scan_rc


def _run_scan_if_selected(profiles: list[str], diff: object = None) -> int:
    """If the ``scan`` profile is selected, run the static scanner and print findings.
    Returns 1 if any ERROR-level finding is present, else 0. WARNINGs never fail."""
    if "scan" not in profiles:
        return 0
    root = _repo_root()
    if diff is not None and getattr(diff, "files", None):
        files = diff.abs_files(root)  # type: ignore[attr-defined]
        changed_lines = diff.abs_changed_lines(root)  # type: ignore[attr-defined]
    else:
        # Bare scan over tracked files (no diff): scan the whole tree.
        files = _tracked_files(root)
        changed_lines = None
    findings = scanner.scan(files, root, changed_lines=changed_lines)
    return _print_findings(findings, root)


def _print_findings(findings: list[scanner.Finding], root: Path) -> int:
    errors = [f for f in findings if f.level == scanner.ERROR]
    warnings = [f for f in findings if f.level == scanner.WARNING]
    for f in findings:
        print("\n" + f.format(root))
    if errors:
        print(f"\n{_FAIL} scanner: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    if warnings:
        print(f"\n{_WARN}scanner: {len(warnings)} warning(s) (advisory)")
    else:
        print(f"\n{_OK} scanner: clean")
    return 0


def _tracked_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return []
    return [root / ln.strip() for ln in out.splitlines() if ln.strip()]


def cmd_scan(args: argparse.Namespace) -> int:
    """Run the static boundary scanner. Over the diff (``--diff``) or the whole tree."""
    root = _repo_root()
    if args.diff:
        diff = compute_diff()
        if not diff.files:
            print(f"{_WARN}no changed files vs {diff.base[:12]}")
            return 0
        findings = scanner.scan(
            diff.abs_files(root), root, changed_lines=diff.abs_changed_lines(root)
        )
    else:
        findings = scanner.scan(_tracked_files(root), root)
    return _print_findings(findings, root)


def cmd_resume_audit(args: argparse.Namespace) -> int:
    """Fresh-session resumability audit for a loop (§2.4): can it answer
    done/verified/next/how-to-verify from persisted state alone?"""
    from harness import resume_audit

    report = resume_audit.audit_loop(args.loop_id)
    if not report.exists:
        print(f"{_FAIL} loop {args.loop_id!r} not found on disk")
        return 1
    checks = [
        ("done", report.done_answerable),
        ("verified", report.verified_answerable),
        ("next", report.next_answerable),
        ("how-to-verify", report.how_to_verify_answerable),
    ]
    for label, ok in checks:
        print(f"  {_OK if ok else _FAIL} {label}")
    if report.ok:
        print(f"{_OK} loop {args.loop_id} is fully resumable from disk")
        return 0
    for f in report.failures():
        print(f"{_FAIL} {f}")
    return 1


def cmd_replay(args: argparse.Namespace) -> int:
    """Gate every baselined replay scenario against its recording (§2.3).

    A threshold breach, a missing required scenario recording, or an unbaselined recording
    all fail. This is the command the ``replay`` profile runs.
    """
    from harness import baselines

    results = baselines.check_baselines()
    if not results:
        print(f"{_WARN}no replay scenarios/baselines found under harness/traces/")
        return 0
    failed = [r for r in results if not r.ok]
    for r in results:
        if r.ok:
            print(f"{_OK} replay {r.scenario}")
        else:
            print(f"{_FAIL} replay {r.scenario}: {'; '.join(r.failures)}")
    if failed:
        print(f"\n{_FAIL} replay: {len(failed)}/{len(results)} scenario(s) failed")
        return 1
    print(f"\n{_OK} replay: {len(results)} scenario(s) within baseline")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harness",
        description="PersonalClaw self-development harness — spec validation, "
        "explain, and required-check execution.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="Shape-validate specs + resolve references.")
    p_val.add_argument(
        "--fast",
        action="store_true",
        help="Skip the pytest --collect-only round-trip (shape only; the inner loop).",
    )
    p_val.set_defaults(func=cmd_validate)

    p_exp = sub.add_parser("explain", help="Print the commands/rules/tests a task owes.")
    p_exp.add_argument("task", help="Task spec id (e.g. T1.1)")
    p_exp.set_defaults(func=cmd_explain)

    p_run = sub.add_parser("run", help="Execute a task's required profiles (or --diff).")
    p_run.add_argument("task", nargs="?", help="Task spec id")
    p_run.add_argument("--diff", action="store_true", help="Diff-aware selection (Session 2).")
    p_run.add_argument("--dry-run", action="store_true", help="Print commands without running.")
    p_run.set_defaults(func=cmd_run)

    p_scan = sub.add_parser("scan", help="Static boundary scanner (whole tree or --diff).")
    p_scan.add_argument("--diff", action="store_true", help="Scan only files changed vs base.")
    p_scan.set_defaults(func=cmd_scan)

    p_replay = sub.add_parser("replay", help="Gate replay scenarios vs checked-in baselines.")
    p_replay.set_defaults(func=cmd_replay)

    p_resume = sub.add_parser(
        "resume-audit", help="Audit whether a loop resumes from disk alone (§2.4)."
    )
    p_resume.add_argument("loop_id", help="Loop id to audit")
    p_resume.set_defaults(func=cmd_resume_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

"""ET-3 — the registry data tier staged under ``scratch/registry/``.

The three clauses the listing policy lives or dies on are the first three tests:

1. a valid listing PASSES,
2. a ``dangerous`` scanner verdict BLOCKS the listing and the rule that fired is
   recorded,
3. a ``warning`` verdict LISTS WITH DISPLAY and blocks nothing.

Clause 3 is the one that is easy to get backwards, so it is asserted from both
directions: the row is listable AND its blocking list is empty AND the finding is
present in ``display``. Inverting the rule in ``verdict_reasons`` reds it.

**These tests are offline, and they run the real fetcher.** ``validate_registry``
reaches a repository exactly one way — ``git ls-remote`` for liveness and ``git clone
--depth 1`` to fetch — and git treats ``file://`` and ``https://`` the same. So each
test builds a throwaway git repository out of a committed fixture app tree and points
a listing row at its ``file://`` URL. No fetch is stubbed and no check is skipped;
the only difference from production is the scheme, which is why the scheme itself is
gated (``test_a_file_url_is_refused_unless_the_test_flag_is_passed``).

Every negative rail carries a vacuity assertion — the placeholder really was there,
the LICENSE really did exist before it was deleted — because a rail that silently
stops matching reads exactly like a rail that passes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from personalclaw.apps.manifest import PROVIDER_TYPES

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGED = REPO_ROOT / "scratch" / "registry"
FIXTURE_APPS = STAGED / "fixtures" / "apps"
FIXTURE_REGISTRIES = STAGED / "fixtures" / "registries"

#: The token the committed candidate documents carry in place of a local repo URL.
PLACEHOLDER = "{{REPO_BASE}}"


def _load_validator() -> Any:
    """Import the staged validator by path. It is registry-repo content, not a core
    module, so it has no import name — which is also the point: nothing in core
    depends on it."""
    path = STAGED / "validate_registry.py"
    assert path.is_file(), f"the staged validator is missing at {path}"
    spec = importlib.util.spec_from_file_location("registry_validate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], so a module executed outside sys.modules raises.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


# ── fixture repositories ─────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _commit_as_repo(path: Path) -> str:
    """Turn a directory into a one-commit git repository; return its ``file://`` URL."""
    _git(path, "init", "-q", "-b", "main")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "fixture")
    return f"file://{path}"


@pytest.fixture(scope="session")
def fixture_repos(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Every committed fixture app, built once as real git repositories."""
    base = tmp_path_factory.mktemp("registry-fixture-repos")
    built = []
    for src in sorted(FIXTURE_APPS.iterdir()):
        if not src.is_dir():
            continue
        dest = base / src.name
        shutil.copytree(src, dest)
        _commit_as_repo(dest)
        built.append(dest.name)
    # Vacuity: the three verdict fixtures must actually exist and have been committed.
    assert built == ["clean-app", "dangerous-app", "warning-app"], built
    for name in built:
        assert (base / name / ".git").is_dir(), f"{name} was not committed"
    return base


def _variant(
    tmp_path: Path,
    source: str = "clean-app",
    *,
    mutate: Callable[[Path], None] | None = None,
    name: str = "variant",
) -> str:
    """Copy a fixture app tree, optionally mutate it, commit it as its own repo."""
    dest = tmp_path / name
    shutil.copytree(FIXTURE_APPS / source, dest)
    if mutate is not None:
        mutate(dest)
    return _commit_as_repo(dest)


def _candidate(name: str, base: Path, tmp_path: Path) -> Path:
    """Materialise a committed candidate registry document against local repos."""
    raw = (FIXTURE_REGISTRIES / f"{name}.json").read_text(encoding="utf-8")
    # Vacuity: if the placeholder is ever renamed, fail loudly instead of validating a
    # URL nobody meant.
    assert PLACEHOLDER in raw, f"{name}.json no longer carries {PLACEHOLDER}"
    out = tmp_path / f"{name}.json"
    out.write_text(raw.replace(PLACEHOLDER, f"file://{base}"), encoding="utf-8")
    return out


def _row(repo: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": "registry-fixture-clean",
        "repo": repo,
        "types": ["search"],
        "permissions_declared": ["network"],
        "license": "MIT",
        "maintainer": "personalclaw",
        "added": "2026-08-18",
    }
    row.update(overrides)
    return row


def _validate_one(row: dict[str, Any]) -> Any:
    result = validator.validate_registry([row], allow_file_repos=True)
    assert result.rows_validated == 1, "the row was skipped instead of validated"
    return result.rows[0]


def _codes(reasons: list[Any]) -> list[str]:
    return [r.code for r in reasons]


# ── clause 1: a valid listing passes ─────────────────────────────────────────


def test_a_valid_sample_listing_passes(fixture_repos: Path, tmp_path: Path) -> None:
    path = _candidate("valid", fixture_repos, tmp_path)
    report = tmp_path / "report.json"
    code = validator.main([str(path), "--allow-file-repos", "--report", str(report)])
    assert code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["listable"] is True
    # Vacuity: one row was actually fetched, cloned and SCANNED. A run that validated
    # nothing would also report listable=True, which is the silent pass this asserts
    # against.
    assert payload["rows_validated"] == 1
    assert payload["rows"][0]["name"] == "registry-fixture-clean"
    assert payload["rows"][0]["verdict"] == "clean"
    assert payload["rows"][0]["blocking"] == []


# ── clause 2: a dangerous verdict blocks, with the reason recorded ────────────


def test_a_dangerous_verdict_blocks_the_listing_and_records_the_reason(
    fixture_repos: Path, tmp_path: Path
) -> None:
    path = _candidate("dangerous", fixture_repos, tmp_path)
    report = tmp_path / "report.json"
    markdown = tmp_path / "report.md"
    code = validator.main(
        [str(path), "--allow-file-repos", "--report", str(report), "--markdown", str(markdown)]
    )
    assert code == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["listable"] is False
    row = payload["rows"][0]
    assert row["listable"] is False
    assert row["verdict"] == "dangerous"

    # The RECORDED REASON, not just the exit code: which rule, in which file.
    codes = [r["code"] for r in row["blocking"]]
    assert "scanner_dangerous:destructive_root" in codes, codes
    detail = next(r["detail"] for r in row["blocking"] if r["code"].startswith("scanner_dangerous"))
    assert "scripts/install.sh" in detail
    assert "rm -rf /" in detail

    # And the reason reaches the surface a contributor actually reads.
    body = markdown.read_text(encoding="utf-8")
    assert "**Blocked" in body
    assert "destructive_root" in body
    assert "scripts/install.sh" in body


# ── clause 3: a warning verdict lists with display and never blocks ───────────


def test_a_warning_verdict_lists_with_display_and_never_blocks(
    fixture_repos: Path, tmp_path: Path
) -> None:
    path = _candidate("warning", fixture_repos, tmp_path)
    report = tmp_path / "report.json"
    markdown = tmp_path / "report.md"
    code = validator.main(
        [str(path), "--allow-file-repos", "--report", str(report), "--markdown", str(markdown)]
    )
    # Listed. This is the assertion that reds if the policy is ever inverted.
    assert code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["listable"] is True
    row = payload["rows"][0]
    assert row["listable"] is True
    assert row["blocking"] == [], "a warning must not block a listing"

    # 'warning', not 'low'. The scanner downgrades warnings for the official/trusted
    # tiers; a community registry that scanned at one of those tiers would report a
    # softer verdict than the user's own install gate will.
    assert row["verdict"] == "warning"

    # Displayed. A verdict that blocked nothing AND showed nothing would be inert.
    display = [r["code"] for r in row["display"]]
    assert "scanner_warning:curl_network" in display, display
    body = markdown.read_text(encoding="utf-8")
    assert "shown, not blocking" in body
    assert "curl_network" in body


def test_the_dangerous_and_warning_bands_are_split_by_severity_not_by_count(
    fixture_repos: Path, tmp_path: Path
) -> None:
    """The dangerous fixture also trips warning-band rules (its guard uses `echo`/
    `exit`, and the tree is otherwise the clean fixture). Blocking must be decided by
    the verdict, not by "were there any findings at all"."""
    dangerous = _validate_one(
        _row(f"file://{fixture_repos}/dangerous-app", name="registry-fixture-dangerous")
    )
    clean = _validate_one(_row(f"file://{fixture_repos}/clean-app"))
    assert dangerous.verdict == "dangerous" and not dangerous.listable
    assert clean.verdict == "clean" and clean.listable
    # Vacuity: the clean fixture really produced no findings, so "clean" is measured
    # rather than a default nobody reached.
    assert clean.findings == []


# ── the validator is not vacuous: things that must fail, do ───────────────────


def test_a_malformed_row_is_refused_by_the_schema(fixture_repos: Path, tmp_path: Path) -> None:
    path = _candidate("malformed-row", fixture_repos, tmp_path)
    report = tmp_path / "report.json"
    code = validator.main([str(path), "--allow-file-repos", "--report", str(report)])
    assert code == 1
    row = json.loads(report.read_text(encoding="utf-8"))["rows"][0]
    codes = [r["code"] for r in row["blocking"]]
    for expected in (
        "field_unknown",
        "name_invalid",
        "types_unknown",
        "permissions_invalid",
        "license_invalid",
        "added_invalid",
    ):
        assert expected in codes, f"{expected} missing from {codes}"
    # Schema refusal happens before any network reach, so no verdict was produced —
    # and `None` must never be read as clean.
    assert row["verdict"] is None


def test_a_missing_license_file_blocks(tmp_path: Path) -> None:
    def drop_license(tree: Path) -> None:
        target = tree / "LICENSE"
        assert target.is_file(), "vacuity: the clean fixture must ship a LICENSE to delete"
        target.unlink()

    row = _validate_one(_row(_variant(tmp_path, mutate=drop_license)))
    assert not row.listable
    assert "license_file_missing" in _codes(row.blocking), _codes(row.blocking)


def test_a_license_the_row_disagrees_with_blocks(tmp_path: Path) -> None:
    repo = _variant(tmp_path)
    row = _validate_one(_row(repo, license="Apache-2.0"))
    assert not row.listable
    detail = next(r.detail for r in row.blocking if r.code == "license_mismatch")
    assert "'Apache-2.0'" in detail and "'MIT'" in detail


def test_an_unparseable_manifest_is_a_failed_validation_not_a_skip(tmp_path: Path) -> None:
    def corrupt(tree: Path) -> None:
        target = tree / "app.json"
        assert target.is_file(), "vacuity: there must be an app.json to corrupt"
        target.write_text("{ not json at all", encoding="utf-8")

    row = _validate_one(_row(_variant(tmp_path, mutate=corrupt)))
    assert not row.listable, "an unreadable manifest must never pass"
    assert "manifest_unparseable" in _codes(row.blocking)
    assert row.verdict is None


def test_a_missing_manifest_is_a_failed_validation_not_a_skip(tmp_path: Path) -> None:
    def drop(tree: Path) -> None:
        assert (tree / "app.json").is_file()
        (tree / "app.json").unlink()

    row = _validate_one(_row(_variant(tmp_path, mutate=drop)))
    assert not row.listable
    assert "manifest_missing" in _codes(row.blocking)


def test_a_manifest_core_rejects_blocks(tmp_path: Path) -> None:
    """The parse leg is core's ``AppManifest.validate``, not a shape guess of our own."""

    def bad_version(tree: Path) -> None:
        data = json.loads((tree / "app.json").read_text(encoding="utf-8"))
        assert data["version"] == "1.0.0", "vacuity: the fixture's version must start valid"
        data["version"] = "one point oh"
        (tree / "app.json").write_text(json.dumps(data), encoding="utf-8")

    row = _validate_one(_row(_variant(tmp_path, mutate=bad_version)))
    assert not row.listable
    detail = next(r.detail for r in row.blocking if r.code == "manifest_invalid")
    assert "semver" in detail


def test_an_unreachable_repo_blocks(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-repo"
    assert not missing.exists(), "vacuity: this path must not exist"
    row = _validate_one(_row(f"file://{missing}"))
    assert not row.listable
    assert "repo_unreachable" in _codes(row.blocking)


def test_a_refused_fetch_is_explained_as_private_or_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forge cannot distinguish "private" from "does not exist", so it answers both
    with an authentication failure. Handing that verbatim to a contributor who simply
    mistyped their repo name sends them hunting for a credentials problem, so the
    reason says what actually happened.

    Reaching this branch at all also proves the no-prompt git environment held: git
    refused instead of blocking on a password nobody was there to type.
    """
    refusal = subprocess.CompletedProcess(
        args=["git"],
        returncode=128,
        stdout="",
        stderr="fatal: Authentication failed for 'https://github.com/x/y/'\n",
    )
    # Asserted at the CALL SITE, not just on the helper: the hint is only worth
    # anything if check_repo_live actually appends it.
    monkeypatch.setattr(validator, "_run_git", lambda *a, **k: refusal)
    reason = validator.check_repo_live("https://github.com/x/y")
    assert reason is not None and reason.code == "repo_unreachable"
    assert "Authentication failed" in reason.detail
    assert "private repository and a nonexistent one" in reason.detail

    # Vacuity: an ordinary failure must NOT get the hint, or it would be noise on
    # every unrelated git error.
    other = subprocess.CompletedProcess(
        args=["git"], returncode=128, stdout="", stderr="fatal: unable to access\n"
    )
    monkeypatch.setattr(validator, "_run_git", lambda *a, **k: other)
    plain = validator.check_repo_live("https://github.com/x/y")
    assert plain is not None
    assert "private repository and a nonexistent one" not in plain.detail


def test_a_repo_with_no_branches_blocks(tmp_path: Path) -> None:
    empty = tmp_path / "empty-repo"
    empty.mkdir()
    _git(empty, "init", "-q", "-b", "main")
    row = _validate_one(_row(f"file://{empty}"))
    assert not row.listable
    assert "repo_empty" in _codes(row.blocking)


def test_under_declared_permissions_block(tmp_path: Path) -> None:
    """The row IS the pre-install consent surface, so it may not under-declare."""
    repo = _variant(tmp_path)
    row = _validate_one(_row(repo, permissions_declared=[]))
    assert not row.listable
    detail = next(r.detail for r in row.blocking if r.code == "permissions_mismatch")
    assert "missing ['network']" in detail


def test_over_declared_permissions_block(tmp_path: Path) -> None:
    repo = _variant(tmp_path)
    row = _validate_one(_row(repo, permissions_declared=["network", "cron"]))
    assert not row.listable
    detail = next(r.detail for r in row.blocking if r.code == "permissions_mismatch")
    assert "cron" in detail


def test_a_mistyped_capability_blocks(tmp_path: Path) -> None:
    repo = _variant(tmp_path)
    row = _validate_one(_row(repo, types=["tool"]))
    assert not row.listable
    detail = next(r.detail for r in row.blocking if r.code == "types_mismatch")
    assert "['tool']" in detail and "['search']" in detail


def test_a_row_name_that_disagrees_with_the_manifest_blocks(tmp_path: Path) -> None:
    repo = _variant(tmp_path)
    row = _validate_one(_row(repo, name="something-else"))
    assert not row.listable
    assert "name_mismatch" in _codes(row.blocking)


def test_duplicate_names_are_refused(fixture_repos: Path) -> None:
    row = _row(f"file://{fixture_repos}/clean-app")
    result = validator.validate_registry([row, dict(row)], allow_file_repos=True)
    assert not result.listable
    assert "duplicate_name" in _codes(result.blocking)


# ── the fetcher's rails ──────────────────────────────────────────────────────


def test_a_file_url_is_refused_unless_the_test_flag_is_passed(
    fixture_repos: Path, tmp_path: Path
) -> None:
    """The offline affordance must not be a production hole: without
    ``--allow-file-repos`` a listing cannot aim the fetcher at the runner's disk."""
    path = _candidate("valid", fixture_repos, tmp_path)
    report = tmp_path / "report.json"
    assert validator.main([str(path), "--report", str(report)]) == 1
    row = json.loads(report.read_text(encoding="utf-8"))["rows"][0]
    assert "repo_url_scheme" in [r["code"] for r in row["blocking"]]
    # Vacuity: the SAME document passes when the flag is given, so this test is
    # measuring the flag and not some unrelated defect in the fixture.
    assert validator.main([str(path), "--allow-file-repos"]) == 0


def test_the_ci_workflow_never_passes_the_file_repo_flag() -> None:
    flag = "--allow-file-repos"
    # Vacuity: the flag exists, so its absence below means something.
    assert flag in (STAGED / "validate_registry.py").read_text(encoding="utf-8")
    workflows = sorted((STAGED / ".github" / "workflows").glob("*.yml"))
    assert len(workflows) == 3, [w.name for w in workflows]
    for workflow in workflows:
        assert flag not in workflow.read_text(encoding="utf-8"), workflow.name


def test_the_ci_workflows_name_the_index_core_actually_reads() -> None:
    """`ET-4a`. The published workflows must spell the index exactly as core does.

    This rail exists because the failure mode is UNDETECTABLE BY THE PUBLISHED REPO'S OWN
    CI. `validate-listings.yml` names the index in a `paths:` filter, so a stale name does
    not fail the workflow — it stops the workflow from firing at all, which reads as green.
    A registry whose validation silently never runs is strictly worse than one that reds.
    Measured before this rail existed: reverting all four references to the pre-`ET-4a`
    `registry.json` left the whole core suite green (98 passed), so nothing pinned them.
    """
    from personalclaw.apps.catalog import _REGISTRY_FILENAME

    workflows = sorted((STAGED / ".github" / "workflows").glob("*.yml"))
    bare = re.compile(r"(?<![-\w])registry\.json")
    naming = 0
    for workflow in workflows:
        body = workflow.read_text(encoding="utf-8")
        # Any spelling other than core's means the index is not the file being validated.
        assert not bare.search(body), f"{workflow.name} names an index core does not read"
        naming += _REGISTRY_FILENAME in body
    # Vacuity: a rule about how the workflows name the index is empty if none of them do.
    assert naming >= 2, f"only {naming} workflow(s) name {_REGISTRY_FILENAME}"


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://github.com/x/y", "repo_url_scheme"),
        ("ssh://git@github.com/x/y", "repo_url_scheme"),
        ("git@github.com:x/y.git", "repo_url_scheme"),
        ("https://user:pw@github.com/x/y", "repo_url_userinfo"),
        ("https://github.com:8443/x/y", "repo_url_port"),
        ("https://github.com/x/y\n", "repo_url_invalid"),
        ("", "repo_url_missing"),
        (17, "repo_url_missing"),
    ],
)
def test_hostile_repo_urls_are_refused(url: object, code: str) -> None:
    reason = validator.check_repo_url(url, allow_file_repos=False)
    assert reason is not None, f"{url!r} was accepted"
    assert reason.code == code, reason


def test_a_plain_https_url_is_accepted_by_the_url_check() -> None:
    """Vacuity for the table above: the check is not simply refusing everything."""
    assert validator.check_repo_url("https://github.com/x/y", allow_file_repos=False) is None


@pytest.mark.parametrize("target", ["/etc/hosts", "../../../../etc/hosts"])
def test_a_symlink_escaping_the_repo_blocks(tmp_path: Path, target: str) -> None:
    """The next step reads this tree and quotes what it finds into a public PR
    comment, so a link out of the tree is refused before anything reads it.

    Both shapes matter: git stores a symlink as whatever string the author committed,
    so an absolute target and a ``../``-climbing relative one are equally committable
    and equally escaping. The relative one is resolved against the CLONE, which is why
    the check reads ``os.readlink`` rather than trusting ``Path.resolve`` alone.
    """

    def add_symlink(tree: Path) -> None:
        link = tree / "notes.md"
        os.symlink(target, link)
        # Vacuity: it really is a symlink, and it really does leave the tree.
        assert link.is_symlink()
        resolved = (link.parent / os.readlink(link)).resolve()
        assert not str(resolved).startswith(str(tree.resolve()) + os.sep)

    row = _validate_one(_row(_variant(tmp_path, mutate=add_symlink)))
    assert not row.listable
    assert "repo_symlink_escape" in _codes(row.blocking)


def test_a_relative_symlink_inside_the_repo_is_left_alone(tmp_path: Path) -> None:
    """Vacuity for the rail above: it targets ESCAPE, not symlinks.

    The target is RELATIVE on purpose. An absolute in-repo symlink stops being in-repo
    the moment the repo is cloned somewhere else, so it is an escape too — correctly,
    and a fixture that used one would have made this test contradict the rail.
    """

    def add_symlink(tree: Path) -> None:
        os.symlink("README.md", tree / "READYOU.md")
        assert (tree / "READYOU.md").is_symlink()
        assert not Path(os.readlink(tree / "READYOU.md")).is_absolute()

    row = _validate_one(_row(_variant(tmp_path, mutate=add_symlink)))
    assert row.listable, _codes(row.blocking)


# ── incremental validation must not become a silent pass ─────────────────────


def test_an_unchanged_registry_reports_zero_rows_validated(
    fixture_repos: Path, tmp_path: Path
) -> None:
    path = _candidate("valid", fixture_repos, tmp_path)
    base = tmp_path / "base.json"
    base.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    report = tmp_path / "report.json"
    markdown = tmp_path / "report.md"
    code = validator.main(
        [
            str(path),
            "--allow-file-repos",
            "--base",
            str(base),
            "--report",
            str(report),
            "--markdown",
            str(markdown),
        ]
    )
    assert code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    # A no-op PR passes, but it must SAY it validated nothing rather than reading as
    # "everything checked out".
    assert payload["rows_validated"] == 0
    assert payload["rows_skipped_unchanged"] == 1
    assert payload["rows"] == []
    assert "No listing changes to validate" in markdown.read_text(encoding="utf-8")


def test_a_changed_row_is_revalidated_against_a_base(fixture_repos: Path, tmp_path: Path) -> None:
    path = _candidate("valid", fixture_repos, tmp_path)
    base = tmp_path / "base.json"
    base.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["apps"][0]["maintainer"] = "someone-else"
    path.write_text(json.dumps(document), encoding="utf-8")
    report = tmp_path / "report.json"
    assert (
        validator.main(
            [str(path), "--allow-file-repos", "--base", str(base), "--report", str(report)]
        )
        == 0
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["rows_validated"] == 1
    assert payload["rows_skipped_unchanged"] == 0


def test_a_ci_stamp_refresh_does_not_read_as_a_changed_listing(
    fixture_repos: Path, tmp_path: Path
) -> None:
    """Only author-owned fields decide whether a row changed — otherwise every
    ``--write`` run would make the next PR re-fetch the whole registry."""
    row = _row(f"file://{fixture_repos}/clean-app")
    stamped = dict(row, last_validated="2026-08-18T00:00:00Z", last_scan_verdict="clean")
    result = validator.validate_registry([stamped], allow_file_repos=True, base_apps=[row])
    assert result.rows_validated == 0
    assert result.rows_skipped_unchanged == 1


def test_a_registry_that_cannot_be_read_exits_two(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("[]", encoding="utf-8")
    assert validator.main([str(broken)]) == 2
    missing = tmp_path / "absent.json"
    assert validator.main([str(missing)]) == 2


# ── --write stamps the verdict into the data ──────────────────────────────────


def test_write_stamps_the_verdict_and_preserves_the_document(
    fixture_repos: Path, tmp_path: Path
) -> None:
    path = _candidate("valid", fixture_repos, tmp_path)
    before = json.loads(path.read_text(encoding="utf-8"))
    assert "last_scan_verdict" not in before["apps"][0], "vacuity: the stamp must be absent first"
    assert validator.main([str(path), "--allow-file-repos", "--write"]) == 0
    after = json.loads(path.read_text(encoding="utf-8"))
    stamped = after["apps"][0]
    assert stamped["last_scan_verdict"] == "clean"
    assert stamped["last_validated"].endswith("Z")
    # The rest of the document survives — including the $schema pointer editors use.
    assert after["$schema"] == before["$schema"]
    assert stamped["repo"] == before["apps"][0]["repo"]


def test_write_never_stamps_a_blocked_row(fixture_repos: Path, tmp_path: Path) -> None:
    path = _candidate("dangerous", fixture_repos, tmp_path)
    assert validator.main([str(path), "--allow-file-repos", "--write"]) == 1
    after = json.loads(path.read_text(encoding="utf-8"))
    assert "last_scan_verdict" not in after["apps"][0]
    assert "last_validated" not in after["apps"][0]


# ── the schema, the live data, and the policy documents ───────────────────────


def test_the_published_schema_matches_the_python_authority() -> None:
    """``app-registry.schema.json`` is generated. Regenerate with
    ``python validate_registry.py --emit-schema > app-registry.schema.json``."""
    committed = json.loads((STAGED / "app-registry.schema.json").read_text(encoding="utf-8"))
    assert committed == validator.build_schema()


def test_the_allowed_types_derive_from_cores_provider_registry() -> None:
    """A capability type added upstream must not need a hand edit here."""
    assert set(PROVIDER_TYPES) <= validator.ALLOWED_TYPES
    assert "search" in validator.ALLOWED_TYPES
    assert "telepathy" not in validator.ALLOWED_TYPES
    schema_types = set(
        validator.build_schema()["properties"]["apps"]["items"]["properties"]["types"]["items"][
            "enum"
        ]
    )
    assert schema_types == validator.ALLOWED_TYPES


def test_every_row_in_the_live_registry_satisfies_the_schema(fixture_repos: Path) -> None:
    document = json.loads((STAGED / "app-registry.json").read_text(encoding="utf-8"))
    assert isinstance(document.get("apps"), list)
    # The live file is empty until ET-6 lists the exemplars, so the fixture row rides
    # along: without it this rail would pass over zero rows and prove nothing.
    rows = list(document["apps"]) + [_row("https://github.com/personalclaw/registry-fixture")]
    assert len(rows) >= 1
    for row in rows:
        assert validator.check_row_schema(row, allow_file_repos=False) == [], row


def test_the_staged_content_is_complete() -> None:
    for expected in (
        "README.md",
        "CONTRIBUTING.md",
        "DELISTING.md",
        "app-registry.json",
        "app-registry.schema.json",
        "requirements.txt",
        "validate_registry.py",
        "fixtures/README.md",
        ".github/workflows/validate-listings.yml",
        ".github/workflows/comment-listing-verdict.yml",
        ".github/workflows/revalidate-listings.yml",
    ):
        assert (STAGED / expected).is_file(), f"{expected} is missing from scratch/registry/"


def test_the_listing_policy_states_the_three_outcomes() -> None:
    """The docs are the policy; the code enforces it. If they drift, the registry is
    promising something it does not do."""
    contributing = (STAGED / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "`dangerous` → **the listing is refused.**" in contributing
    assert "**the listing is accepted, and the verdict is published**" in contributing
    assert "not an endorsement" in (STAGED / "README.md").read_text(encoding="utf-8")
    delisting = (STAGED / "DELISTING.md").read_text(encoding="utf-8")
    assert "It does **not** uninstall anything." in delisting
    assert "14 days" in delisting


def test_the_validator_pins_the_core_version_it_scans_with() -> None:
    """A floating dependency would let the scanner's rule set — i.e. what the registry
    accepts — change without anyone deciding to."""
    requirements = (STAGED / "requirements.txt").read_text(encoding="utf-8")
    assert "personalclaw==" in requirements

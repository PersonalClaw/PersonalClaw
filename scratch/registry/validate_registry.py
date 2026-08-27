#!/usr/bin/env python3
"""PR validation for the PersonalClaw community app registry (``app-registry.json``).

CI runs this on every pull request that touches ``app-registry.json``. It answers one
question per changed listing row — **may this app be listed?** — and records the
answer, with its reason, where a reviewer reads it.

The four checks the front-door policy promises (``CONTRIBUTING.md``):

1. **repo liveness** — ``git ls-remote`` reaches the repo and it has a branch.
2. **manifest fetch + parse** — a shallow clone must carry an ``app.json`` that
   ``personalclaw.apps.manifest.AppManifest`` parses AND validates. The row's
   ``types`` and ``permissions_declared`` must match what that manifest actually
   declares, because those two fields are the pre-install consent surface the
   registry publishes on the user's behalf.
3. **license present** — a license file in the repo, a ``license`` in the
   manifest, and the row agreeing with the manifest.
4. **scanner dry-run** — ``SkillScanner`` over the clone at the ``community``
   trust tier. The verdict is recorded on every row, always. ``dangerous``
   BLOCKS the listing; ``warning`` and ``low`` are DISPLAYED and never block.
   "Dry-run" is literal: the scanner is static content inspection and this script
   never executes a single line of the fetched repo.

There is no silent pass. Every check either succeeds or produces a blocking
:class:`Reason` — an unreachable repo, an unparseable manifest or an unexpected
exception all read as "not listable, here is why", never as "skipped".

Exit codes: ``0`` every changed row is listable · ``1`` at least one row is
blocked · ``2`` the input could not be read at all (usage / malformed file).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit

from personalclaw.apps.manifest import PROVIDER_TYPES, AppManifest
from personalclaw.supply_chain import ScanReport, SkillScanner, TrustTier, Verdict

# ── The schema (the authority; app-registry.schema.json is its published mirror) ──

#: Fields a listing PR must supply. Everything else is CI-owned or rejected.
REQUIRED_FIELDS: tuple[str, ...] = (
    "name",
    "repo",
    "types",
    "permissions_declared",
    "license",
    "maintainer",
    "added",
)
#: Fields only this script writes (``--write``). A PR may leave them out, and a PR
#: that supplies them is not trusted — they are overwritten from the actual run.
CI_OWNED_FIELDS: tuple[str, ...] = ("last_validated", "last_scan_verdict")
KNOWN_FIELDS: tuple[str, ...] = REQUIRED_FIELDS + CI_OWNED_FIELDS

#: Capability types a row may claim. Derived from core's ``PROVIDER_TYPES`` so a new
#: upstream capability type is listable the day core ships it, plus the two
#: non-provider surfaces an app can be (a backend process, a UI page).
NON_PROVIDER_TYPES = frozenset({"backend", "ui"})
ALLOWED_TYPES = frozenset(PROVIDER_TYPES) | NON_PROVIDER_TYPES

#: The one verdict that blocks. Everything below it lists and is displayed.
BLOCKING_VERDICT = Verdict.DANGEROUS

#: A listing repo must be a plain ``https`` URL. ``file://`` is a TEST affordance
#: behind ``--allow-file-repos``: the CI workflow never passes it, so a PR cannot
#: aim the fetcher at the runner's filesystem.
HTTPS_SCHEME = "https"
FILE_SCHEME = "file"

LICENSE_FILENAMES: tuple[str, ...] = (
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "LICENCE",
    "LICENSE-MIT",
    "COPYING",
    "COPYING.md",
)

#: Caps so one hostile listing cannot wedge or drown a CI runner.
MAX_REPO_BYTES = 50 * 1024 * 1024
MAX_REPO_FILES = 5_000
LS_REMOTE_TIMEOUT_SECS = 60
CLONE_TIMEOUT_SECS = 180

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}" r"(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?$"
)
CONTROL_RE = re.compile(r"[\x00-\x20\x7f]")


# ── Results ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reason:
    """One recorded finding. ``code`` is stable and greppable; ``detail`` is the
    sentence a reviewer reads in the PR."""

    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass
class RowResult:
    """The verdict on one listing row."""

    name: str
    repo: str
    listable: bool = False
    #: The scanner verdict, or ``None`` when validation stopped before the scan
    #: ran. ``None`` is never "clean" — a row that never reached the scanner is
    #: blocked by whatever stopped it.
    verdict: str | None = None
    blocking: list[Reason] = field(default_factory=list)
    #: Recorded, shown, and deliberately NOT blocking (scanner warnings).
    display: list[Reason] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "repo": self.repo,
            "listable": self.listable,
            "verdict": self.verdict,
            "blocking": [r.to_dict() for r in self.blocking],
            "display": [r.to_dict() for r in self.display],
            "findings": list(self.findings),
        }


@dataclass
class RegistryResult:
    """The verdict on a whole ``app-registry.json`` (or on the rows a PR changed)."""

    rows: list[RowResult] = field(default_factory=list)
    #: File-level problems (not a valid registry document, duplicate names).
    blocking: list[Reason] = field(default_factory=list)
    #: How many rows this run actually fetched and scanned. Reported explicitly so
    #: "nothing changed" can never be mistaken for "everything checked out".
    rows_validated: int = 0
    rows_skipped_unchanged: int = 0

    @property
    def listable(self) -> bool:
        return not self.blocking and all(r.listable for r in self.rows)

    @property
    def blocked_rows(self) -> list[RowResult]:
        return [r for r in self.rows if not r.listable]

    def to_dict(self) -> dict[str, Any]:
        return {
            "listable": self.listable,
            "rows_validated": self.rows_validated,
            "rows_skipped_unchanged": self.rows_skipped_unchanged,
            "blocking": [r.to_dict() for r in self.blocking],
            "rows": [r.to_dict() for r in self.rows],
        }


# ── git (the one fetch mechanism: https in production, file:// under test) ────


def _git_env() -> dict[str, str]:
    """A git environment that fails instead of asking. A CI runner must never
    block on a credential prompt for a third-party repo, and must never offer the
    runner's own credentials to one."""
    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "echo",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GCM_INTERACTIVE": "never",
        }
    )
    for leaked in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(leaked, None)
    return env


def _run_git(args: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv list, never a shell
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_git_env(),
        check=False,
    )


def _git_error(proc: subprocess.CompletedProcess[str]) -> str:
    text = (proc.stderr or proc.stdout or "").strip().splitlines()
    return text[-1][:200] if text else f"git exited {proc.returncode}"


#: What git says when the fetch was refused rather than answered. Reaching one of
#: these is also proof the no-prompt environment worked: git gave up instead of
#: waiting for a password nobody was there to type.
_AUTH_REFUSAL_MARKERS: tuple[str, ...] = (
    "authentication failed",
    "could not read username",
    "could not read password",
    "terminal prompts disabled",
    "permission denied",
)

#: Appended when the refusal above happens, because the verbatim git message sends a
#: contributor who simply mistyped their repo name looking for a credentials problem.
_PRIVATE_OR_MISSING_HINT = (
    " A forge answers a private repository and a nonexistent one the same way, so check"
    " the URL and that the repository is public."
)


def _looks_like_auth_refusal(proc: subprocess.CompletedProcess[str]) -> bool:
    text = ((proc.stderr or "") + (proc.stdout or "")).lower()
    return any(marker in text for marker in _AUTH_REFUSAL_MARKERS)


def check_repo_url(repo: object, *, allow_file_repos: bool) -> Reason | None:
    """Reject a repo URL the fetcher must not follow."""
    if not isinstance(repo, str) or not repo:
        return Reason("repo_url_missing", "'repo' must be a non-empty string.")
    if CONTROL_RE.search(repo):
        return Reason("repo_url_invalid", "'repo' contains whitespace or control characters.")
    try:
        parts = urlsplit(repo)
    except ValueError as exc:
        return Reason("repo_url_invalid", f"'repo' is not a URL: {exc}")
    allowed = {HTTPS_SCHEME} | ({FILE_SCHEME} if allow_file_repos else set())
    if parts.scheme not in allowed:
        return Reason(
            "repo_url_scheme",
            f"'repo' must be an https:// URL, got scheme {parts.scheme or '(none)'!r}.",
        )
    if parts.scheme == HTTPS_SCHEME:
        if "@" in parts.netloc:
            return Reason("repo_url_userinfo", "'repo' must not embed credentials.")
        if not parts.hostname:
            return Reason("repo_url_invalid", "'repo' has no host.")
        if parts.port is not None:
            return Reason("repo_url_port", "'repo' must not name an explicit port.")
    return None


def check_repo_live(repo: str) -> Reason | None:
    """Liveness: the repo answers and has at least one branch."""
    try:
        proc = _run_git(["ls-remote", "--heads", "--", repo], timeout=LS_REMOTE_TIMEOUT_SECS)
    except subprocess.TimeoutExpired:
        return Reason(
            "repo_unreachable", f"'git ls-remote' timed out after {LS_REMOTE_TIMEOUT_SECS}s."
        )
    except OSError as exc:
        return Reason("repo_unreachable", f"'git ls-remote' could not run: {exc}")
    if proc.returncode != 0:
        detail = f"'git ls-remote' failed: {_git_error(proc)}"
        if _looks_like_auth_refusal(proc):
            detail += _PRIVATE_OR_MISSING_HINT
        return Reason("repo_unreachable", detail)
    if not proc.stdout.strip():
        return Reason("repo_empty", "the repo exists but has no branches.")
    return None


def clone_repo(repo: str, dest: Path) -> Reason | None:
    """Shallow-clone into ``dest``. Nothing from the clone is ever executed."""
    args = [
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
        "--quiet",
        "--config",
        "core.hooksPath=/dev/null",
        "--",
        repo,
        str(dest),
    ]
    try:
        proc = _run_git(args, timeout=CLONE_TIMEOUT_SECS)
    except subprocess.TimeoutExpired:
        return Reason("clone_failed", f"'git clone' timed out after {CLONE_TIMEOUT_SECS}s.")
    except OSError as exc:
        return Reason("clone_failed", f"'git clone' could not run: {exc}")
    if proc.returncode != 0:
        return Reason("clone_failed", f"'git clone' failed: {_git_error(proc)}")
    return None


def _content_files(clone: Path) -> Iterable[Path]:
    for path in clone.rglob("*"):
        if ".git" in path.relative_to(clone).parts:
            continue
        yield path


def check_repo_shape(clone: Path) -> Reason | None:
    """Size caps, and no symlink that reaches outside the clone.

    The escaping-symlink check matters because the next step reads this tree and
    quotes what it finds into a public PR comment: a link to ``/etc/passwd`` or to
    the runner's token file would otherwise be read and echoed."""
    total = 0
    count = 0
    for path in _content_files(clone):
        if path.is_symlink():
            target = (path.parent / os.readlink(path)).resolve()
            if not str(target).startswith(str(clone.resolve()) + os.sep):
                rel = path.relative_to(clone)
                return Reason(
                    "repo_symlink_escape",
                    f"'{rel}' is a symlink pointing outside the repo.",
                )
            continue
        if not path.is_file():
            continue
        count += 1
        total += path.stat().st_size
        if count > MAX_REPO_FILES:
            return Reason("repo_too_large", f"more than {MAX_REPO_FILES} files.")
        if total > MAX_REPO_BYTES:
            return Reason("repo_too_large", f"content exceeds {MAX_REPO_BYTES} bytes.")
    return None


# ── manifest, license, consent-surface agreement ─────────────────────────────


def load_manifest(clone: Path) -> tuple[AppManifest | None, Reason | None]:
    """Fetch+parse leg. A manifest that cannot be read, parsed or validated is a
    FAILED validation with a stated reason — never a skip."""
    path = clone / "app.json"
    if not path.is_file():
        return None, Reason("manifest_missing", "no 'app.json' at the repo root.")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, Reason("manifest_unreadable", f"'app.json' could not be read: {exc}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, Reason("manifest_unparseable", f"'app.json' is not valid JSON: {exc}")
    if not isinstance(data, dict):
        return None, Reason("manifest_not_object", "'app.json' must be a JSON object.")
    try:
        manifest = AppManifest.from_dict(data)
        errors = manifest.validate()
    except Exception as exc:  # a hostile manifest must not crash the run
        return None, Reason("manifest_unparseable", f"'app.json' could not be parsed: {exc}")
    if errors:
        return None, Reason("manifest_invalid", "; ".join(errors[:6]))
    return manifest, None


def derived_types(manifest: AppManifest) -> set[str]:
    """The capability types this manifest actually declares."""
    types = {p.type for p in manifest.all_providers() if p.type}
    if manifest.backend.entryPoint:
        types.add("backend")
    if manifest.ui.entry or manifest.ui.pages:
        types.add("ui")
    return types


def derived_permissions(manifest: AppManifest) -> set[str]:
    """The permission NAMES this manifest actually declares (``Permissions.to_dict``
    is sparse, so its keys are exactly the non-default grants)."""
    return set(manifest.permissions.to_dict().keys())


def check_license(clone: Path, row: dict[str, Any], manifest: AppManifest) -> list[Reason]:
    out: list[Reason] = []
    if not any((clone / name).is_file() for name in LICENSE_FILENAMES):
        out.append(
            Reason(
                "license_file_missing",
                "no license file at the repo root (looked for "
                f"{', '.join(LICENSE_FILENAMES[:3])}…).",
            )
        )
    declared = (manifest.license or "").strip()
    if not declared:
        out.append(Reason("manifest_license_missing", "'app.json' declares no 'license'."))
        return out
    listed = str(row.get("license", "")).strip()
    if listed != declared:
        out.append(
            Reason(
                "license_mismatch",
                f"the row says {listed!r} but 'app.json' says {declared!r}.",
            )
        )
    return out


def check_declared_surface(row: dict[str, Any], manifest: AppManifest) -> list[Reason]:
    """The row's ``types``/``permissions_declared`` ARE the pre-install consent
    surface the registry publishes. They must equal the manifest's, or the registry
    would be telling users something the app does not honour."""
    out: list[Reason] = []
    listed_types = {t for t in row.get("types", []) if isinstance(t, str)}
    actual_types = derived_types(manifest)
    if listed_types != actual_types:
        out.append(
            Reason(
                "types_mismatch",
                f"the row lists {sorted(listed_types)} but 'app.json' declares "
                f"{sorted(actual_types)}.",
            )
        )
    listed_perms = {p for p in row.get("permissions_declared", []) if isinstance(p, str)}
    actual_perms = derived_permissions(manifest)
    if listed_perms != actual_perms:
        missing = sorted(actual_perms - listed_perms)
        extra = sorted(listed_perms - actual_perms)
        parts = []
        if missing:
            parts.append(f"missing {missing}")
        if extra:
            parts.append(f"claims {extra} which 'app.json' does not request")
        out.append(
            Reason(
                "permissions_mismatch",
                "'permissions_declared' must match 'app.json': " + "; ".join(parts) + ".",
            )
        )
    return out


# ── the scanner dry-run ──────────────────────────────────────────────────────


def scan_repo(clone: Path) -> ScanReport:
    """Static content scan at the ``community`` tier — the tier that does NOT
    downgrade warnings. A community listing is listed, not endorsed."""
    return SkillScanner().scan(clone, tier=TrustTier.COMMUNITY)


def verdict_reasons(report: ScanReport) -> tuple[list[Reason], list[Reason]]:
    """Split a scan report into (blocking, display).

    ``dangerous`` blocks. Everything else is recorded and displayed and blocks
    NOTHING — a ``warning`` listing is listed with its warnings shown, which is the
    whole point of publishing the verdict pre-install."""
    blocking: list[Reason] = []
    display: list[Reason] = []
    if report.verdict is BLOCKING_VERDICT:
        for finding in report.findings:
            if finding.severity is BLOCKING_VERDICT:
                blocking.append(
                    Reason(
                        f"scanner_dangerous:{finding.rule}",
                        f"{finding.path or '(repo)'}: {finding.rule} — {finding.evidence}",
                    )
                )
        if not blocking:  # verdict without an attributable finding: still blocked
            blocking.append(Reason("scanner_dangerous", "the scanner returned 'dangerous'."))
        return blocking, display
    for finding in report.findings:
        display.append(
            Reason(
                f"scanner_{finding.severity.value}:{finding.rule}",
                f"{finding.path or '(repo)'}: {finding.rule} — {finding.evidence}",
            )
        )
    return blocking, display


# ── row + document validation ───────────────────────────────────────────────


def check_row_schema(row: object, *, allow_file_repos: bool) -> list[Reason]:
    """Structural validation. This is the authority ``app-registry.schema.json``
    mirrors (``test_registry_validation.py`` pins the two together)."""
    if not isinstance(row, dict):
        return [Reason("row_not_object", "each entry in 'apps' must be a JSON object.")]
    out: list[Reason] = []
    for missing in [f for f in REQUIRED_FIELDS if f not in row]:
        out.append(Reason("field_missing", f"missing required field {missing!r}."))
    unknown = sorted(set(row) - set(KNOWN_FIELDS))
    if unknown:
        out.append(Reason("field_unknown", f"unknown field(s) {unknown} — the schema is closed."))

    name = row.get("name")
    if "name" in row and (not isinstance(name, str) or not KEBAB_RE.match(name)):
        out.append(Reason("name_invalid", f"'name' must be kebab-case, got {name!r}."))

    if "repo" in row:
        bad_url = check_repo_url(row.get("repo"), allow_file_repos=allow_file_repos)
        if bad_url is not None:
            out.append(bad_url)

    types = row.get("types")
    if "types" in row:
        if not isinstance(types, list) or not types or not all(isinstance(t, str) for t in types):
            out.append(Reason("types_invalid", "'types' must be a non-empty list of strings."))
        else:
            unknown_types = sorted(set(types) - ALLOWED_TYPES)
            if unknown_types:
                out.append(
                    Reason(
                        "types_unknown",
                        f"'types' contains {unknown_types}, which are not PersonalClaw "
                        "capability types.",
                    )
                )

    perms = row.get("permissions_declared")
    if "permissions_declared" in row and (
        not isinstance(perms, list) or not all(isinstance(p, str) for p in perms)
    ):
        out.append(
            Reason(
                "permissions_invalid",
                "'permissions_declared' must be a list of strings (empty means none).",
            )
        )

    for text_field in ("license", "maintainer"):
        value = row.get(text_field)
        if text_field in row and (not isinstance(value, str) or not value.strip()):
            out.append(
                Reason(f"{text_field}_invalid", f"{text_field!r} must be a non-empty string.")
            )

    for date_field in ("added", "last_validated"):
        value = row.get(date_field)
        if date_field in row and (not isinstance(value, str) or not ISO_RE.match(value)):
            out.append(
                Reason(
                    f"{date_field}_invalid",
                    f"{date_field!r} must be an ISO-8601 date or timestamp, got {value!r}.",
                )
            )

    verdict = row.get("last_scan_verdict")
    if "last_scan_verdict" in row and verdict not in {v.value for v in Verdict}:
        out.append(
            Reason(
                "last_scan_verdict_invalid",
                f"'last_scan_verdict' must be one of {sorted(v.value for v in Verdict)}, "
                f"got {verdict!r}.",
            )
        )
    return out


def validate_row(row: dict[str, Any], *, allow_file_repos: bool) -> RowResult:
    """Validate one row end to end. Returns a result; never raises."""
    result = RowResult(name=str(row.get("name", "(unnamed)")), repo=str(row.get("repo", "")))
    schema_reasons = check_row_schema(row, allow_file_repos=allow_file_repos)
    if schema_reasons:
        result.blocking.extend(schema_reasons)
        return result
    try:
        with tempfile.TemporaryDirectory(prefix="registry-validate-") as tmp:
            clone = Path(tmp) / "repo"
            for step in (
                lambda: check_repo_live(result.repo),
                lambda: clone_repo(result.repo, clone),
                lambda: check_repo_shape(clone),
            ):
                reason = step()
                if reason is not None:
                    result.blocking.append(reason)
                    return result
            manifest, reason = load_manifest(clone)
            if manifest is None:
                result.blocking.append(reason or Reason("manifest_missing", "no manifest."))
                return result
            if manifest.name != result.name:
                result.blocking.append(
                    Reason(
                        "name_mismatch",
                        f"the row is named {result.name!r} but 'app.json' says "
                        f"{manifest.name!r}.",
                    )
                )
            result.blocking.extend(check_license(clone, row, manifest))
            result.blocking.extend(check_declared_surface(row, manifest))
            report = scan_repo(clone)
            result.verdict = report.verdict.value
            result.findings = [f.to_dict() for f in report.findings]
            blocking, display = verdict_reasons(report)
            result.blocking.extend(blocking)
            result.display.extend(display)
    except Exception as exc:  # never a silent pass
        result.blocking.append(
            Reason("internal_error", f"validation could not complete: {type(exc).__name__}: {exc}")
        )
        return result
    result.listable = not result.blocking
    return result


def build_schema() -> dict[str, Any]:
    """The published ``app-registry.schema.json``, generated from the constants above.

    The Python checks in this module are the authority; the schema file is their
    machine-readable mirror for editors and third-party tooling. Generating it means
    the two cannot disagree — and because core's ``PROVIDER_TYPES`` feeds
    ``ALLOWED_TYPES``, a capability type added upstream shows up here on
    regeneration rather than needing a hand edit.

    Regenerate with ``python validate_registry.py --emit-schema > app-registry.schema.json``
    (``test_registry_validation.py`` reds if the committed file has drifted).
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": (
            "https://raw.githubusercontent.com/personalclaw/registry/main/"
            "app-registry.schema.json"
        ),
        "title": "PersonalClaw community app registry",
        "description": (
            "Generated by validate_registry.py --emit-schema. Do not hand-edit: "
            "validate_registry.py is the authority and this file mirrors it."
        ),
        "type": "object",
        "required": ["apps"],
        "additionalProperties": False,
        "properties": {
            "$schema": {"type": "string"},
            "apps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(REQUIRED_FIELDS),
                    "properties": {
                        "name": {
                            "type": "string",
                            "pattern": KEBAB_RE.pattern,
                            "description": "the app's kebab-case name; must equal app.json's.",
                        },
                        "repo": {
                            "type": "string",
                            "format": "uri",
                            "pattern": "^https://",
                            "description": "public https git URL; no credentials, no port.",
                        },
                        "types": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": sorted(ALLOWED_TYPES)},
                            "description": (
                                "capability types the app declares; must equal what "
                                "app.json declares."
                            ),
                        },
                        "permissions_declared": {
                            "type": "array",
                            "uniqueItems": True,
                            "items": {"type": "string"},
                            "description": (
                                "permission names from app.json's permissions block; "
                                "must equal them exactly. This is the pre-install "
                                "consent surface the registry publishes."
                            ),
                        },
                        "license": {
                            "type": "string",
                            "minLength": 1,
                            "description": "SPDX-ish id; must equal app.json's license.",
                        },
                        "maintainer": {
                            "type": "string",
                            "minLength": 1,
                            "description": "who to contact — a git forge handle.",
                        },
                        "added": {
                            "type": "string",
                            "pattern": ISO_RE.pattern,
                            "description": "ISO-8601 date the listing was first proposed.",
                        },
                        "last_validated": {
                            "type": "string",
                            "pattern": ISO_RE.pattern,
                            "description": "CI-owned: when validation last passed.",
                        },
                        "last_scan_verdict": {
                            "type": "string",
                            "enum": sorted(v.value for v in Verdict),
                            "description": (
                                "CI-owned: the scanner dry-run verdict recorded at "
                                "that validation. 'dangerous' is never listed."
                            ),
                        },
                    },
                },
            },
        },
    }


def load_registry(path: Path) -> tuple[list[Any] | None, Reason | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, Reason("registry_unreadable", f"{path} could not be read: {exc}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, Reason("registry_unparseable", f"{path} is not valid JSON: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("apps"), list):
        return None, Reason(
            "registry_malformed",
            f"{path} must be a JSON object with an 'apps' array.",
        )
    return data["apps"], None


def _identity(row: object) -> str:
    """A row's content identity over the AUTHOR-owned fields only, so a re-run that
    only refreshed ``last_validated`` does not read as a changed listing."""
    if not isinstance(row, dict):
        return json.dumps(row, sort_keys=True)
    return json.dumps({k: row.get(k) for k in REQUIRED_FIELDS}, sort_keys=True)


def validate_registry(
    apps: list[Any],
    *,
    allow_file_repos: bool = False,
    base_apps: list[Any] | None = None,
) -> RegistryResult:
    """Validate a registry document's rows.

    ``base_apps`` (the base branch's ``apps``) restricts the run to rows a PR added
    or changed. Rows skipped that way are counted in the result, so a no-op PR
    reports ``rows_validated: 0`` rather than passing as if it checked something.
    """
    result = RegistryResult()
    names = [r.get("name") for r in apps if isinstance(r, dict)]
    duplicates = sorted({n for n in names if isinstance(n, str) and names.count(n) > 1})
    if duplicates:
        result.blocking.append(
            Reason("duplicate_name", f"'apps' lists {duplicates} more than once.")
        )
    unchanged = {_identity(r) for r in base_apps} if base_apps is not None else set()
    for row in apps:
        if _identity(row) in unchanged:
            result.rows_skipped_unchanged += 1
            continue
        if not isinstance(row, dict):
            result.rows.append(
                RowResult(
                    name="(non-object row)",
                    repo="",
                    blocking=[Reason("row_not_object", "each entry in 'apps' must be an object.")],
                )
            )
            result.rows_validated += 1
            continue
        result.rows.append(validate_row(row, allow_file_repos=allow_file_repos))
        result.rows_validated += 1
    return result


def apply_validation_stamps(apps: list[Any], result: RegistryResult, *, now: str) -> None:
    """Write the CI-owned fields onto the rows that passed. The verdict lands in the
    data so the Store card and the website can show it pre-install."""
    by_name = {r.name: r for r in result.rows if r.listable}
    for row in apps:
        if not isinstance(row, dict):
            continue
        row_result = by_name.get(str(row.get("name", "")))
        if row_result is None or row_result.verdict is None:
            continue
        row["last_validated"] = now
        row["last_scan_verdict"] = row_result.verdict


# ── reporting (this markdown IS the PR comment — read it as UI copy) ──────────


def render_markdown(result: RegistryResult) -> str:
    lines: list[str] = ["## Registry listing validation", ""]
    if result.blocking:
        lines.append("**This PR cannot be validated.**")
        lines.append("")
        for reason in result.blocking:
            lines.append(f"- `{reason.code}` — {reason.detail}")
        lines.append("")
    if not result.rows:
        if not result.blocking:
            lines.append(
                f"No listing changes to validate "
                f"({result.rows_skipped_unchanged} existing listing(s) left untouched)."
            )
        return "\n".join(lines) + "\n"

    lines.append("| Listing | Scanner verdict | Listable |")
    lines.append("|---|---|---|")
    for row in result.rows:
        verdict = f"`{row.verdict}`" if row.verdict else "not reached"
        lines.append(f"| `{row.name}` | {verdict} | {'yes' if row.listable else '**no**'} |")
    lines.append("")

    for row in result.rows:
        if not row.blocking and not row.display:
            continue
        lines.append(f"### `{row.name}`")
        lines.append("")
        if row.blocking:
            lines.append("**Blocked — this app will not be listed until these are fixed:**")
            lines.append("")
            for reason in row.blocking:
                lines.append(f"- `{reason.code}` — {reason.detail}")
            lines.append("")
        if row.display:
            lines.append(
                f"The scanner returned `{row.verdict}`. These findings are **shown, not "
                "blocking** — the registry lists community apps rather than endorsing them, "
                "so you decide before you install:"
            )
            lines.append("")
            for reason in row.display:
                lines.append(f"- `{reason.code}` — {reason.detail}")
            lines.append("")
    lines.append(
        "Every listing is community-contributed. A `clean` verdict means no pattern in the "
        "scanner's catalog matched — it is not an audit, and never an endorsement."
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "registry", type=Path, nargs="?", default=None, help="path to app-registry.json"
    )
    parser.add_argument(
        "--emit-schema",
        action="store_true",
        help="print app-registry.schema.json (generated from this module) and exit",
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=None,
        help="the base branch's app-registry.json; only added/changed rows are validated",
    )
    parser.add_argument("--report", type=Path, default=None, help="write the JSON report here")
    parser.add_argument(
        "--markdown", type=Path, default=None, help="write the PR-comment markdown here"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="stamp last_validated + last_scan_verdict onto rows that passed",
    )
    parser.add_argument(
        "--allow-file-repos",
        action="store_true",
        help="permit file:// repo URLs (test fixtures only; CI never passes this)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.emit_schema:
        sys.stdout.write(json.dumps(build_schema(), indent=2) + "\n")
        return 0
    if args.registry is None:
        parser.error("an app-registry.json path is required (or pass --emit-schema)")
    apps, load_reason = load_registry(args.registry)
    if apps is None:
        failed = RegistryResult(blocking=[load_reason] if load_reason else [])
        sys.stderr.write(render_markdown(failed))
        if args.report:
            args.report.write_text(json.dumps(failed.to_dict(), indent=2) + "\n", encoding="utf-8")
        return 2

    base_apps: list[Any] | None = None
    if args.base is not None and args.base.exists():
        base_apps, _ = load_registry(args.base)

    result = validate_registry(apps, allow_file_repos=args.allow_file_repos, base_apps=base_apps)
    markdown = render_markdown(result)
    sys.stdout.write(markdown)
    if args.report:
        args.report.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(markdown, encoding="utf-8")
    if args.write and result.listable:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        apply_validation_stamps(apps, result, now=now)
        document = json.loads(args.registry.read_text(encoding="utf-8"))
        document["apps"] = apps
        args.registry.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0 if result.listable else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

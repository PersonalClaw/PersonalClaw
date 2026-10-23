"""``python -m tools.ci release-tag``: the one way a ``v*`` release tag is created (#4584).

``.github/workflows/release-tag.yml`` runs this from main's copy when the maintainer dispatches
it with a tag and a commit. It refuses, naming why, unless:

- the tag is a release tag, by the release's own parser (``scripts/release_tags.py``);
- the commit is a full commit id on ``main`` (``trust.require_on_main``), so no tag can carry a
  ``release.yml`` from a branch;
- the commit's definitions (its workflows, actions, ``tools/ci`` and the entry points they run)
  equal ``main``'s tip's (``trust.compare``), so the tag's release run is the one ``main`` holds;
- no tag of that name exists, here or on the remote;
- the commit's own push run on ``main`` is green (#3911, :func:`main_push_green`): read in
  ``merge.yml`` (``workflows.MAIN_PUSH``).
- the commit's breadth is green (#3911, :func:`breadth_green`), for a release tag: the newest
  ``breadth.yml`` run of the commit off ``main`` is a rehearsal tag's push, with a
  digest-verified record of every attempt in which every gating leg ran and is green and every
  advisory leg (:data:`ADVISORY_LEGS`) ran, each red one owned by an open issue named for it;
  and an earlier red run of the commit is named in the owner's record
  (:data:`BREADTH_EXCEPTIONS`), after which a dispatch on the rehearsal tag may re-measure it.
  It never waits: until that run exists and has finished, it refuses;
- no ``release.yml`` run is still going (#3911, :func:`no_release_running`): every release
  shares one concurrency group, where GitHub keeps one waiting run, so a tag made behind a
  running release could have its run replaced by the next and never be released.

With ``--rehearsal`` the tag it checks for, and creates, is ``rehearsal-<tag>-<sha7>``: every
check but breadth's and the App are the same, and no ``v*`` rule or release trigger sees it, so
the whole path can be proved without a release. Its push starts ``breadth.yml`` on the commit,
which is the run the release tag's gate then reads. The short commit in the name lets a later
commit rehearse the same tag, since a tag is never moved or deleted; the gate matches the full
commit, never the name.

It reads the runs and breadth's records with ``RUNS_TOKEN``, the job's own token with
``actions: read``. With
``--create`` it then creates the annotated tag and its ref through the API with
``GH_TOKEN``, the release controller's App token: the one actor the ``release-tag-creation``
ruleset lets create a ``v*`` tag. The tag's push starts ``release.yml``. Nothing else is written,
and a tag is never moved or deleted (``release-tags`` refuses both for everyone).

#3911's gate grows with the trusted ``merge-verdict`` (#3910) once it is required. See
docs/maintainers/ci.md, "The release tags".
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import breadth_evidence, trust, workflows

API = "https://api.github.com"
MAIN = "origin/main"
#: A rehearsal creates ``rehearsal-<tag>-<sha7>`` instead: the same checks and the same App,
#: under a name no ``v*`` rule or ``release.yml`` trigger matches, so the path is proved without a
#: release. Its push starts ``breadth.yml`` (``push: tags: [rehearsal-v*]``).
REHEARSAL_PREFIX = "rehearsal-"


def tag_name(tag: str, *, rehearsal: bool = False, commit: str = "") -> str:
    """The ref name the tag is created as: *tag* itself, or its rehearsal name, which carries
    the commit's first seven characters so each commit rehearses the tag under its own name."""
    return f"{REHEARSAL_PREFIX}{tag}-{commit[:7]}" if rehearsal else tag


#: ``post(path, body)`` returns the HTTP status and the decoded JSON answer.
Post = Callable[[str, dict[str, object]], tuple[int, object]]
#: ``get(path)`` returns one API read's decoded JSON answer.
Get = Callable[[str], Any]
#: Where a run's workflow file sits, as the API's ``path`` names it.
WORKFLOW_DIR = ".github/workflows/"


def _parse_tag(repo: Path, tag: str) -> None:
    """The release's own tag parser, read from the checkout, refuses what it refuses."""
    path = repo / "scripts" / "release_tags.py"
    spec = importlib.util.spec_from_file_location("_release_tags", path)
    if spec is None or spec.loader is None:
        raise trust.Refused(f"cannot read the release's tag parser at {path}")
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks its module up in sys.modules while the class is built, so the module is
    # registered for as long as it runs, and taken out again after.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        module.parse_release_ref(tag)
    except ValueError as error:
        raise trust.Refused(str(error)) from None
    finally:
        sys.modules.pop(spec.name, None)
    if tag != tag.strip():
        raise trust.Refused(f"{tag!r} has spaces around it")


def _tag_exists(repo: Path, tag: str) -> bool:
    for args in (
        ["rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
        ["ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{tag}"],
    ):
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        if done.returncode == 0:
            return True
        if args[0] == "ls-remote" and done.returncode != 2:
            raise trust.Refused(f"cannot tell whether {tag} exists on the remote: {done.stderr}")
    return False


def check(repo: Path, tag: str, commit: str, *, main: str = MAIN, rehearsal: bool = False) -> None:
    """Refuse (``trust.Refused``) unless *commit* on *main* may be tagged *tag* (or, with
    *rehearsal*, its rehearsal name: the tag itself is still held to the release's parser)."""
    _parse_tag(repo, tag)
    trust.require_on_main(repo, commit, main=main)
    tip = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{main}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if tip.returncode != 0:
        raise trust.Refused(f"cannot read {main}: {tip.stderr.strip()}")
    changed = trust.compare(repo, base=tip.stdout.strip(), head=commit).changes
    if changed:
        names = ", ".join(f"{c.path} ({c.kind})" for c in changed)
        raise trust.Refused(
            f"{commit}'s definitions differ from {main}'s: {names}. Tag a commit whose "
            "workflows, actions and tools/ci are main's, or land the change first"
        )
    name = tag_name(tag, rehearsal=rehearsal, commit=commit)
    if _tag_exists(repo, name):
        raise trust.Refused(f"{name} exists already; a release tag is never moved")


def _runs(repository: str, workflow: str, commit: str, get: Get) -> list[dict[str, Any]]:
    """*commit*'s push runs on ``main`` of *repository* in *workflow*, as the API lists them."""
    path = (
        f"/repos/{repository}/actions/workflows/{workflow}/runs"
        f"?event=push&branch=main&head_sha={commit}&per_page=100"
    )
    try:
        answer = get(path)
    except (OSError, ValueError) as error:
        raise trust.Refused(f"cannot read {workflow}'s runs of {commit}: {error}") from None
    listed = answer.get("workflow_runs") if isinstance(answer, dict) else None
    if not isinstance(listed, list):
        raise trust.Refused(f"cannot read {workflow}'s runs of {commit}: {answer!r}")
    # The API's filters are read again here: only this commit's push to this repository's main.
    return [
        run
        for run in listed
        if isinstance(run, dict)
        and run.get("head_sha") == commit
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
        and (run.get("head_repository") or {}).get("full_name") == repository
        and str(run.get("path", "")).split("@", 1)[0] == WORKFLOW_DIR + workflow
    ]


def main_push_green(repository: str, commit: str, get: Get) -> str:
    """The URL of *commit*'s own push run on ``main``, which must have finished green; else
    ``trust.Refused``, naming the run and what it concluded.

    The commit's push runs are ``merge.yml``'s (``workflows.MAIN_PUSH``), and of them the newest
    is read. A run still going, failed, cancelled or timed out is not green, and neither is one
    whose verdict read a suite shard a newer push replaced: that shard reads cancelled, so the
    verdict is red and the run concluded failure. A run re-run to green is green: its conclusion
    is its latest attempt's (the runbook's Tagging says so).
    """
    for workflow in workflows.MAIN_PUSH:
        runs = _runs(repository, workflow, commit, get)
        if not runs:
            continue
        run = max(runs, key=lambda r: (int(r.get("run_number") or 0), int(r.get("id") or 0)))
        url = run.get("html_url") or f"run {run.get('id')}"
        if run.get("status") != "completed":
            raise trust.Refused(
                f"{commit}'s push run on main ({workflow}, {url}) is {run.get('status')}: tag "
                "it once that run is green"
            )
        if run.get("conclusion") != "success":
            raise trust.Refused(
                f"{commit}'s push run on main ({workflow}, {url}) concluded "
                f"{run.get('conclusion')}, not success: tag a commit whose own run is green"
            )
        return str(url)
    raise trust.Refused(
        f"{commit} has no push run on main in {' or '.join(workflows.MAIN_PUSH)}, so nothing "
        "proved it: tag a commit main's push ran on"
    )


BREADTH_WORKFLOW = "breadth.yml"
#: The owner's record of each red breadth run a release may pass over, read from main's checkout.
#: Only the owner writes it, by hand. That is a rule, not a check: agents act on GitHub as the
#: owner's account, so nothing here can tell who wrote an entry.
BREADTH_EXCEPTIONS = ".github/release-breadth-exceptions.json"
#: ``download(path)`` returns the bytes an API path's artifact zip holds.
Download = Callable[[str], bytes]
#: A record's zip, or its one file, larger than this is not a record this reads.
_MAX_RECORD_BYTES = 16 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{40}")
_EXCEPTION_KEYS = {"run_id", "commit", "why"}


def exception_problems(data: object) -> list[str]:
    """What is wrong with the owner's record's shape: ``{"about": str, "exceptions": [{"run_id":
    int, "commit": <full commit id>, "why": str}, ...]}``, each run named once."""
    if not isinstance(data, dict):
        return ["the record is not an object"]
    found: list[str] = []
    if set(data) != {"about", "exceptions"}:
        found.append(f"the record's keys are {sorted(data)}, not about and exceptions")
    if not isinstance(data.get("about"), str) or not str(data.get("about")).strip():
        found.append("the record's about says nothing")
    entries = data.get("exceptions")
    if not isinstance(entries, list):
        return [*found, "its exceptions are not a list"]
    seen: set[int] = set()
    for n, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != _EXCEPTION_KEYS:
            keys = sorted(entry) if isinstance(entry, dict) else entry
            found.append(f"exception {n} holds {keys!r}, not run_id, commit and why")
            continue
        run_id, commit, why = entry["run_id"], entry["commit"], entry["why"]
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id < 1:
            found.append(f"exception {n}'s run_id {run_id!r} is not a run id")
        elif run_id in seen:
            found.append(f"exception {n} names run {run_id} twice")
        else:
            seen.add(run_id)
        if not isinstance(commit, str) or not _SHA.fullmatch(commit):
            found.append(f"exception {n}'s commit {commit!r} is not a full commit id")
        if not isinstance(why, str) or not why.strip():
            found.append(f"exception {n} says no why")
    return found


def breadth_exceptions(repo: Path) -> dict[int, str]:
    """The owner's record in *repo*, run id to commit; none when there is no record."""
    path = repo / BREADTH_EXCEPTIONS
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise trust.Refused(f"cannot read {BREADTH_EXCEPTIONS}: {error}") from None
    problems = exception_problems(data)
    if problems:
        raise trust.Refused(
            f"{BREADTH_EXCEPTIONS} is not the owner's record: {'; '.join(problems)}"
        )
    return {int(e["run_id"]): str(e["commit"]) for e in data["exceptions"]}


def _ask(get: Get, path: str, what: str) -> Any:
    try:
        return get(path)
    except (OSError, ValueError) as error:
        raise trust.Refused(f"cannot read {what}: {error}") from None


def _breadth_runs(repository: str, commit: str, get: Get) -> list[dict[str, Any]]:
    """*commit*'s ``breadth.yml`` runs of *repository* off ``main``, any event."""
    what = f"{BREADTH_WORKFLOW}'s runs of {commit}"
    answer = _ask(
        get,
        f"/repos/{repository}/actions/workflows/{BREADTH_WORKFLOW}/runs"
        f"?head_sha={commit}&per_page=100",
        what,
    )
    listed = answer.get("workflow_runs") if isinstance(answer, dict) else None
    if not isinstance(listed, list):
        raise trust.Refused(f"cannot read {what}: {answer!r}")
    if len(listed) >= 100:
        raise trust.Refused(f"cannot read {what}: they fill a page, so one may be unseen")
    # Main's runs reuse earlier results, so a release never rests on one, and its reds are
    # kept in breadth's leg issues.
    return [
        run
        for run in listed
        if isinstance(run, dict)
        and run.get("head_sha") == commit
        and run.get("head_branch") != "main"
        and (run.get("head_repository") or {}).get("full_name") == repository
        and str(run.get("path", "")).split("@", 1)[0] == WORKFLOW_DIR + BREADTH_WORKFLOW
    ]


def _breadth_records(
    repository: str, run_id: int, get: Get, download: Download
) -> list[dict[str, Any]]:
    """Every record run *run_id* holds, oldest first, each verified under the digest its
    artifact's name gives (``breadth_evidence.Refused`` when one does not verify)."""
    what = f"run {run_id}'s artifacts"
    artifacts: list[dict[str, Any]] = []
    for page in range(1, 11):
        answer = _ask(
            get,
            f"/repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100&page={page}",
            what,
        )
        listed = answer.get("artifacts") if isinstance(answer, dict) else None
        total = answer.get("total_count") if isinstance(answer, dict) else None
        if not isinstance(listed, list) or not isinstance(total, int):
            raise trust.Refused(f"cannot read {what}: {answer!r}")
        artifacts += [a for a in listed if isinstance(a, dict)]
        if len(artifacts) >= total or not listed:
            break
    else:
        raise trust.Refused(f"cannot read {what}: more than ten pages")
    records: list[dict[str, Any]] = []
    for artifact in sorted(artifacts, key=lambda a: int(a.get("id") or 0)):
        name = str(artifact.get("name") or "")
        if not name.startswith(breadth_evidence.PREFIX):
            continue
        if artifact.get("expired"):
            raise breadth_evidence.Refused(f"its record {name} has expired")
        data = _ask(
            download, f"/repos/{repository}/actions/artifacts/{artifact.get('id')}/zip", name
        )
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if archive.getinfo("record.json").file_size > _MAX_RECORD_BYTES:
                    raise breadth_evidence.Refused(f"{name}'s record is too large to be one")
                body = archive.read("record.json")
        except (zipfile.BadZipFile, KeyError) as error:
            raise breadth_evidence.Refused(f"{name} holds no record.json: {error}") from None
        records.append(breadth_evidence.verify(body, name[len(breadth_evidence.PREFIX) :]))
    return records


#: The breadth legs that measure rather than gate (#3911): the next-Ubuntu canary and the two
#: legs that read its suite (``continue-on-error`` in breadth.yml), and coverage. A release
#: needs each of them run, and each red one owned by an open issue named for it; every other
#: leg (each OS, Python and architecture, random-order, security, the installer smoke, the
#: audit and the union checks) must be green.
ADVISORY_LEGS = frozenset({"canary", "planned-ids-canary", "shard-union-canary", "coverage"})
#: The conclusions a finished run's records are read under. A red leg's job fails its run, so a
#: run whose only reds are advisory concludes ``failure``; the records say which legs. Any other
#: conclusion (cancelled, timed out, never started) measured nothing a release may rest on.
_JUDGED_CONCLUSIONS = ("success", "failure")
#: Who files a leg's issue: breadth's report, as the workflow's bot, or a maintainer. On a
#: public repository anyone may open an issue under that title, and theirs owns nothing.
_ISSUE_BOT = "github-actions[bot]"
_MAINTAINERS = ("OWNER", "MEMBER", "COLLABORATOR")


def _release_findings(
    records: list[dict[str, Any]], run_attempt: int
) -> tuple[list[str], set[str]]:
    """What in a run's records refuses a release outright, and which advisory legs are red.

    The run must hold a record of every attempt up to ``run_attempt``; the newest attempt's
    must hold every expected shard, each run there (none carried, none that never ran); a gating
    shard red on any attempt refuses. An advisory shard red on any attempt makes its leg red,
    which an open issue owns (:func:`_owning_issue`)."""
    refusals: list[str] = []
    advisory: set[str] = set()
    attempts = {r.get("attempt") for r in records}
    lacking = [n for n in range(1, run_attempt + 1) if n not in attempts]
    if run_attempt < 1 or lacking:
        refusals.append(f"lacks a record of attempt {', '.join(map(str, lacking)) or run_attempt}")
    for record in records:
        found = record.get("legs")
        legs: dict[str, Any] = found if isinstance(found, dict) else {}
        newest = record.get("attempt") == run_attempt
        if newest:
            missing = sorted(breadth_evidence.expected_keys() - set(legs))
            if missing:
                refusals.append(f"lacks {', '.join(missing[:5])}")
        for key, leg in sorted(legs.items()):
            leg_id = breadth_evidence.leg_of(key)
            if newest and leg.get("reused_from"):
                refusals.append(f"carried {key} from another run")
            elif newest and leg.get("missing") and leg_id in ADVISORY_LEGS:
                refusals.append(f"advisory {key} did not run")
            elif leg.get("result") != breadth_evidence.GREEN:
                if leg_id in ADVISORY_LEGS:
                    advisory.add(leg_id)
                else:
                    refusals.append(f"{key} is red on attempt {record.get('attempt')}")
    return sorted(set(refusals)), advisory


def _owning_issue(repository: str, leg: str, get: Get) -> str | None:
    """The open issue that owns advisory *leg*'s red, titled as breadth's report titles it."""
    title = breadth_evidence.issue_title(leg)
    query = urllib.parse.urlencode(
        {"q": f'repo:{repository} is:issue is:open in:title "{title}"', "per_page": 100}
    )
    answer = _ask(get, f"/search/issues?{query}", f"the issues titled {title!r}")
    items = answer.get("items") if isinstance(answer, dict) else None
    if not isinstance(items, list) or (
        isinstance(answer, dict) and answer.get("incomplete_results")
    ):
        raise trust.Refused(f"cannot read the issues titled {title!r}: {answer!r}")
    for item in items:
        user = item.get("user") if isinstance(item, dict) else None
        if (
            isinstance(user, dict)
            and item.get("title") == title
            and item.get("state") == "open"
            and "pull_request" not in item
            and (
                (user.get("login") == _ISSUE_BOT and user.get("type") == "Bot")
                or item.get("author_association") in _MAINTAINERS
            )
        ):
            return f"#{item.get('number')}"
    return None


def _breadth_red(
    repository: str, run: dict[str, Any], tree: str, get: Get, download: Download
) -> tuple[str | None, list[str]]:
    """Why a finished run is not one a release may rest on (None when it is one), and each
    advisory red it holds with the issue that owns it."""
    if run.get("conclusion") not in _JUDGED_CONCLUSIONS:
        return f"concluded {run.get('conclusion')}", []
    run_id = int(run.get("id") or 0)
    try:
        records = _breadth_records(repository, run_id, get, download)
    except breadth_evidence.Refused as refused:
        return f"has a record that does not verify: {refused}", []
    if not records:
        return "holds no breadth record", []
    for record in records:
        if record.get("run_id") != run_id:
            return f"holds a record of run {record.get('run_id')}", []
        if record.get("tree") != tree:
            return f"holds a record of tree {record.get('tree')}, not the commit's {tree}", []
    attempt = run.get("run_attempt")
    if not isinstance(attempt, int):
        return "names no attempt", []
    refusals, advisory = _release_findings(records, attempt)
    if refusals:
        return f"does not satisfy a release: {'; '.join(refusals[:8])}", []
    owned: list[str] = []
    for leg in sorted(advisory):
        issue = _owning_issue(repository, leg, get)
        if issue is None:
            return (
                f"has advisory {leg} red with no open issue titled "
                f"'{breadth_evidence.issue_title(leg)}' to own it",
                [],
            )
        owned.append(f"{leg} {issue}")
    return None, owned


def breadth_green(repository: str, commit: str, get: Get, download: Download, *, repo: Path) -> str:
    """The URL of the breadth run a release of *commit* rests on; else ``trust.Refused``,
    naming why. It never waits: a run still going refuses.

    The newest ``breadth.yml`` run of the commit off ``main`` must be a rehearsal tag's push,
    finished, every record of it verified under its digest and naming that run and the commit's
    tree. Together the records must hold every attempt, and in the newest every shard, none
    carried; every gating shard green on every attempt; and every advisory leg run, each red one
    owned by an open issue titled as breadth's report titles it, filed by that report or a
    maintainer (:func:`_release_findings`, :func:`_owning_issue`). The advisory reds are named
    beside the URL. The run's commit is the tagged commit, whose definitions :func:`check` holds
    to ``main``'s, so the breadth.yml that ran is ``main``'s. An earlier red run of the commit
    refuses unless the owner's record (:data:`BREADTH_EXCEPTIONS`, from *repo*, ``main``'s
    checkout) names it with this commit: passing over a red is the owner's call (#4712). Once it
    does, a dispatch on the rehearsal tag may be the newest run, as the owner's re-measure; with
    no such red a dispatch decides nothing. The newest run is never passed over."""
    done = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{commit}^{{tree}}"],
        capture_output=True,
        text=True,
    )
    tree = done.stdout.strip()
    if done.returncode != 0 or not tree:
        raise trust.Refused(f"cannot read {commit}'s tree: {done.stderr.strip()}")
    runs = _breadth_runs(repository, commit, get)
    if not runs:
        raise trust.Refused(
            f"no breadth run of {commit} off main yet: rehearse the tag (release-tag.yml with "
            f"rehearsal: true), whose rehearsal-<tag>-{commit[:7]} push starts {BREADTH_WORKFLOW} "
            "on the commit, and tag once that run is green"
        )
    for run in runs:
        if run.get("status") != "completed":
            raise trust.Refused(
                f"{BREADTH_WORKFLOW}'s run of {commit} ({run.get('html_url')}) is "
                f"{run.get('status')}: tag once it has finished"
            )
    newest = max(runs, key=lambda r: int(r.get("id") or 0))
    url = str(newest.get("html_url") or f"run {newest.get('id')}")
    branch = str(newest.get("head_branch") or "")
    event = newest.get("event")
    if event not in ("push", "workflow_dispatch") or not branch.startswith(REHEARSAL_PREFIX + "v"):
        raise trust.Refused(
            f"the newest breadth run of {commit} ({url}) is a {event} on {branch}, not a "
            "rehearsal tag's push: rehearse the tag, and tag once that run is green"
        )
    why, owned = _breadth_red(repository, newest, tree, get, download)
    if why:
        raise trust.Refused(
            f"the newest breadth run of {commit} ({url}) {why}: a release rests only on every "
            "gating leg green and every advisory red owned"
        )
    excused = breadth_exceptions(repo)
    reds = 0
    for run in sorted(runs, key=lambda r: int(r.get("id") or 0)):
        if run is newest:
            continue
        why, _ = _breadth_red(repository, run, tree, get, download)
        if not why:
            continue
        reds += 1
        if excused.get(int(run.get("id") or 0)) != commit:
            raise trust.Refused(
                f"an earlier breadth run of {commit} ({run.get('html_url')}) {why}, and the "
                f"owner's record ({BREADTH_EXCEPTIONS} on main) names no reason to pass over it"
            )
    # A dispatch on the rehearsal tag is how the owner measures the commit again after a red
    # the record names; with no such red it re-measures nothing, and only the push decides.
    if event == "workflow_dispatch" and not reds:
        raise trust.Refused(
            f"the newest breadth run of {commit} ({url}) is a dispatch, which decides only as a "
            "re-measure of an earlier red run the owner's record names; there is none"
        )
    if owned:
        return f"{url} (advisory reds, each owned: {', '.join(owned)})"
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def github_download(token: str) -> Download:
    """An artifact's zip: the API answers with a redirect to a signed storage address, which is
    read without the token, so the token never leaves the API."""

    def download(path: str) -> bytes:
        request = urllib.request.Request(API + path)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("Authorization", f"Bearer {token}")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=60) as response:
                data: bytes = response.read(_MAX_RECORD_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                raise
            location = str(error.headers.get("Location") or "")
            if not location.startswith("https://"):
                raise OSError(f"{path} redirects to {location!r}, not an https address") from None
            with urllib.request.urlopen(location, timeout=60) as response:  # noqa: S310
                data = response.read(_MAX_RECORD_BYTES + 1)
        if len(data) > _MAX_RECORD_BYTES:
            raise OSError(f"{path} is larger than a record")
        return data

    return download


def github_get(token: str) -> Get:
    def get(path: str) -> Any:
        request = urllib.request.Request(API + path)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read() or b"null")

    return get


def github_post(token: str) -> Post:
    def post(path: str, body: dict[str, object]) -> tuple[int, object]:
        request = urllib.request.Request(API + path, data=json.dumps(body).encode(), method="POST")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"null")
            except ValueError:
                return error.code, None
        except OSError as error:
            return 0, f"{type(error).__name__}: {error}"

    return post


def create(repository: str, tag: str, commit: str, post: Post) -> str:
    """Create the annotated tag object and its ref; the tag object's id."""
    status, answer = post(
        f"/repos/{repository}/git/tags",
        {"tag": tag, "message": f"PersonalClaw {tag}", "object": commit, "type": "commit"},
        # *tag* is the name to create: the release tag, or its rehearsal name.
    )
    sha = answer.get("sha") if isinstance(answer, dict) else None
    if status != 201 or not isinstance(sha, str):
        raise trust.Refused(f"the tag object was not created ({status}): {answer!r}")
    status, answer = post(f"/repos/{repository}/git/refs", {"ref": f"refs/tags/{tag}", "sha": sha})
    if status != 201:
        raise trust.Refused(f"refs/tags/{tag} was not created ({status}): {answer!r}")
    return sha


RELEASE_WORKFLOW = "release.yml"
#: Every state a run is in before it finishes, as the API's ``status`` filter names them.
UNFINISHED = ("queued", "in_progress", "waiting", "requested", "pending", "action_required")


def no_release_running(repository: str, get: Get) -> None:
    """Refuse (``trust.Refused``) while any ``release.yml`` run of *repository* has not finished,
    naming it.

    Every release shares one concurrency group (``release.yml``'s ``concurrency``), which never
    cancels the run that is publishing but keeps only one run waiting: a tag made while a release
    runs waits behind it, and a third tag's run would replace it, leaving that tag with no
    release. A tag made only once the last release finished starts the one run, so through this
    path none is ever replaced. An answer it cannot read refuses, never reads as no release."""
    for status in UNFINISHED:
        path = (
            f"/repos/{repository}/actions/workflows/{RELEASE_WORKFLOW}/runs"
            f"?status={status}&per_page=100"
        )
        try:
            answer = get(path)
        except (OSError, ValueError) as error:
            raise trust.Refused(
                f"cannot read {RELEASE_WORKFLOW}'s {status} runs: {error}"
            ) from None
        listed = answer.get("workflow_runs") if isinstance(answer, dict) else None
        if not isinstance(listed, list):
            raise trust.Refused(f"cannot read {RELEASE_WORKFLOW}'s {status} runs: {answer!r}")
        for run in listed:
            if (
                isinstance(run, dict)
                and run.get("status") != "completed"
                and str(run.get("path", "")).split("@", 1)[0] == WORKFLOW_DIR + RELEASE_WORKFLOW
            ):
                url = run.get("html_url") or f"run {run.get('id')}"
                raise trust.Refused(
                    f"{RELEASE_WORKFLOW}'s run for {run.get('head_branch')} ({url}) is "
                    f"{run.get('status')}: a tag made now would wait behind it, where the next "
                    "could replace it. Tag once that release has finished"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci release-tag", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repo", default=".", type=Path, help="the checkout to read")
    parser.add_argument(
        "--repository",
        required=True,
        metavar="OWNER/NAME",
        help="the repository whose main push runs are read, and where the tag is created",
    )
    parser.add_argument("--create", action="store_true", help="create the tag")
    parser.add_argument(
        "--rehearsal", action="store_true", help=f"create {REHEARSAL_PREFIX}<tag> instead"
    )
    args = parser.parse_args(argv)
    name = tag_name(args.tag, rehearsal=args.rehearsal, commit=args.commit)
    try:
        check(args.repo, args.tag, args.commit, rehearsal=args.rehearsal)
        runs_token = os.environ.get("RUNS_TOKEN")
        if not runs_token:
            raise trust.Refused("no RUNS_TOKEN: the job's token reads the commit's push run")
        get = github_get(runs_token)
        run = main_push_green(args.repository, args.commit, get)
        if args.rehearsal:
            breadth = f"not read, since {name}'s push is what starts it"
        else:
            breadth = "green: " + breadth_green(
                args.repository, args.commit, get, github_download(runs_token), repo=args.repo
            )
        no_release_running(args.repository, get)
        print(
            f"may tag: {args.commit} on main as {name}; its push run on main is green: {run}; "
            f"its breadth is {breadth}; no release is running"
        )
        if args.create:
            token = os.environ.get("GH_TOKEN")
            if not token:
                raise trust.Refused("no GH_TOKEN: the controller's App token creates the tag")
            sha = create(args.repository, name, args.commit, github_post(token))
            print(f"created: {name} -> {args.commit} (tag object {sha})")
    except trust.Refused as refused:
        print(f"::error title=release tag refused::{refused}")
        return 1
    return 0

"""The merge verdict's record, and when a posted one is believed (#3910).

The merge controller posts its verdict as a ``merge-verdict`` check run on a pull request's head,
with its GitHub App's token. The record lives in that check run: a title and a summary for
people, and the receipt (``tools/ci/trust.py``'s ``RECEIPT_FIELDS``) as canonical JSON in one
fenced block of ``output.text`` (:func:`render_output`). Only the App that posted a check run can
change it, and it stays with the commit.

``python -m tools.ci verify-check-run <sha> --repo OWNER/NAME`` says whether the verdict on a head
counts. Every rule fails closed:

1. The App is the one ``main``'s required-status-check rule names for ``merge-verdict``. No such
   rule, a rule naming no App, two Apps, or GitHub Actions' own App refuses every verdict, so a
   job a candidate names ``merge-verdict`` satisfies nothing.
2. Every ``merge-verdict`` check run on the head is read, page by page against the API's count,
   which must hold from page to page. Only the App's runs on this head count; another App's run
   is named in the refusal.
3. The newest of the App's runs (the highest id, in whatever order the API lists them) decides,
   so a later pending or red run takes back an older green. It must be ``completed`` with
   conclusion ``success``: ``failure``, ``cancelled``, ``skipped``, ``neutral``, ``timed_out``,
   ``action_required``, ``stale`` and a run still going are refused.
4. Its output holds exactly one receipt, in trust's format, with no key given twice. The receipt
   names this head, and the head's tree as the API reads it (a squash-merged head is not on
   ``main``, so local git cannot read it), and a controller revision on ``main`` (local git,
   ``trust.require_on_main``, which refuses a shallow clone).
5. Every external revision the caller expects (``--external NAME=SHA``, a partner head) is the
   one the receipt records.

Exit codes: 0 accepted (the receipt is printed as canonical JSON on the last line), 1 refused,
2 an answer that could not be read. argparse keeps its own: ``--help`` exits 0 and a usage error
2, so acceptance is exit 0 with the ``accepted:`` line. It uses only the standard library and
imports no candidate code. See ``docs/maintainers/ci.md`` ("The merge verdict's record").
"""

from __future__ import annotations

import argparse
import dataclasses
import http.client
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import trust

#: The check run's name, and the required check main's rule names.
CHECK_NAME = "merge-verdict"
#: GitHub Actions' App: a run made by any workflow job or with any workflow token.
GITHUB_ACTIONS_APP_ID = 15368
#: The info string of the fenced block that holds the receipt.
RECEIPT_FENCE = "merge-verdict-receipt"

# Applied with ``fullmatch`` only, as trust's ids are: ``$`` under ``match`` also matches before
# a trailing newline.
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_PER_PAGE = 100
#: More check runs than this on one head is not an answer this reads.
_MAX_PAGES = 20
_OPEN = "```" + RECEIPT_FENCE
_CLOSE = "```"

Get = Callable[[str], Any]
"""One API read: a path under the API root (``/repos/...``) to its decoded JSON."""


@dataclass(frozen=True)
class Accepted:
    receipt: trust.Receipt
    check_run_id: int


@dataclass(frozen=True)
class Refused:
    reason: str


@dataclass(frozen=True)
class Unreadable:
    reason: str


Result = Accepted | Refused | Unreadable


class _Unread(Exception):
    """An answer this cannot read."""


# ── The record ────────────────────────────────────────────────────────────────────────────────


def _as_mapping(receipt: Mapping[str, object] | trust.Receipt) -> dict[str, object]:
    if isinstance(receipt, trust.Receipt):
        return dataclasses.asdict(receipt)
    return dict(receipt)


def receipt_block(receipt: Mapping[str, object] | trust.Receipt, *, check: bool = True) -> str:
    """The receipt as canonical JSON in its fenced block: an incomplete one is refused, and only
    the receipt's own fields are written. ``check=False`` writes the mapping as given."""
    data = _as_mapping(receipt)
    if check:
        data = dataclasses.asdict(trust.receipt_from(data))
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{_OPEN}\n{text}\n{_CLOSE}\n"


def render_output(
    receipt: Mapping[str, object] | trust.Receipt, *, title: str, summary: str
) -> dict[str, str]:
    """The check run's ``output``: words for people, and the receipt for :func:`read_receipt`."""
    return {"title": title, "summary": summary, "text": receipt_block(receipt)}


def _no_repeats(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise trust.Refused(f"the receipt gives {key} twice")
        seen[key] = value
    return seen


def read_receipt(check_run: Mapping[str, Any]) -> trust.Receipt:
    """The one receipt in a check run's output, in trust's format; or :class:`trust.Refused`."""
    output = check_run.get("output")
    text = output.get("text") if isinstance(output, Mapping) else None
    if not isinstance(text, str):
        raise trust.Refused("the check run has no output text, so no receipt")
    lines = text.splitlines()
    opens = [n for n, line in enumerate(lines) if line.rstrip() == _OPEN]
    if len(opens) != 1:
        found = "no receipt" if not opens else f"{len(opens)} receipts"
        raise trust.Refused(f"the check run's output holds {found}, not exactly one")
    start = opens[0] + 1
    end = next((n for n in range(start, len(lines)) if lines[n].rstrip() == _CLOSE), None)
    if end is None:
        raise trust.Refused("the receipt's block is never closed")
    try:
        data = json.loads("\n".join(lines[start:end]), object_pairs_hook=_no_repeats)
    except json.JSONDecodeError as exc:
        raise trust.Refused(f"the receipt is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise trust.Refused("the receipt is not a JSON object")
    return trust.receipt_from(data)


# ── Reading the API ───────────────────────────────────────────────────────────────────────────


def _read(get: Get, path: str) -> Any:
    try:
        return get(path)
    # HTTPException: a body cut short (IncompleteRead) or a status line the client cannot read,
    # neither of which is an OSError.
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise _Unread(f"GET {path}: {exc}") from exc


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def controller_app(get: Get, repo: str) -> int:
    """The App id main's required-status-check rule names for ``merge-verdict``."""
    rules = _read(get, f"/repos/{repo}/rules/branches/main?per_page={_PER_PAGE}")
    if not isinstance(rules, list):
        raise _Unread("main's rules are not a list")
    if len(rules) >= _PER_PAGE:
        raise _Unread("main's rules fill a page, so one may be unseen")
    ids: set[int] = set()
    named = False
    for rule in rules:
        if not isinstance(rule, Mapping):
            raise _Unread("one of main's rules is not an object")
        if rule.get("type") != "required_status_checks":
            continue
        params = rule.get("parameters")
        checks = params.get("required_status_checks") if isinstance(params, Mapping) else None
        if not isinstance(checks, list):
            raise _Unread("a required-status-check rule lists no checks")
        for check in checks:
            if not isinstance(check, Mapping) or check.get("context") != CHECK_NAME:
                continue
            named = True
            app = _int(check.get("integration_id"))
            if app is None:
                raise trust.Refused(
                    f"main's rule for {CHECK_NAME} names no App, so any poster would do"
                )
            ids.add(app)
    if not named:
        raise trust.Refused(f"main has no required {CHECK_NAME} check, so no verdict counts yet")
    if len(ids) > 1:
        raise trust.Refused(f"main's rules bind {CHECK_NAME} to more than one App ({sorted(ids)})")
    app = ids.pop()
    if app == GITHUB_ACTIONS_APP_ID:
        raise trust.Refused(
            f"main's rule binds {CHECK_NAME} to GitHub Actions, which any workflow job posts as"
        )
    return app


def _tree_of(get: Get, repo: str, sha: str) -> str:
    commit = _read(get, f"/repos/{repo}/commits/{sha}")
    inner = commit.get("commit") if isinstance(commit, Mapping) else None
    tree = inner.get("tree") if isinstance(inner, Mapping) else None
    oid = tree.get("sha") if isinstance(tree, Mapping) else None
    if not isinstance(oid, str) or not _SHA.fullmatch(oid):
        raise _Unread(f"commit {sha[:12]} names no tree")
    return oid


def _check_runs(get: Get, repo: str, sha: str) -> list[Mapping[str, Any]]:
    """Every ``merge-verdict`` check run on the commit, every page, against the API's count."""
    runs: list[Mapping[str, Any]] = []
    total: int | None = None
    for page in range(1, _MAX_PAGES + 1):
        query = urllib.parse.urlencode(
            {"check_name": CHECK_NAME, "filter": "all", "per_page": _PER_PAGE, "page": page}
        )
        answer = _read(get, f"/repos/{repo}/commits/{sha}/check-runs?{query}")
        if not isinstance(answer, Mapping):
            raise _Unread("the check runs are not an object")
        count, items = _int(answer.get("total_count")), answer.get("check_runs")
        if count is None or not isinstance(items, list):
            raise _Unread("the check runs carry no count or no list")
        if total is None:
            total = count
        elif count != total:
            raise _Unread("the check runs' count changed between pages")
        for item in items:
            if not isinstance(item, Mapping) or _int(item.get("id")) is None:
                raise _Unread("a check run is not an object with an id")
            runs.append(item)
        if len(runs) > total:
            raise _Unread("more check runs came back than the count says")
        if len(runs) == total:
            return runs
        if not items:
            raise _Unread(f"{total} check runs were counted but {len(runs)} came back")
    raise _Unread(f"more than {_MAX_PAGES} pages of check runs")


def _poster(run: Mapping[str, Any]) -> str:
    app = run.get("app")
    if not isinstance(app, Mapping):
        return "no App"
    return f"{app.get('slug') or 'an unnamed App'} (App {app.get('id')})"


# ── The rule ──────────────────────────────────────────────────────────────────────────────────


def verify(
    get: Get,
    *,
    repo: str,
    sha: str,
    git_dir: Path,
    main: str = "origin/main",
    expected_external: Mapping[str, str] | None = None,
) -> Result:
    """Whether the verdict on ``sha`` counts. Never raises: what cannot be read is Unreadable."""
    try:
        return _verify(
            get,
            repo=repo,
            sha=sha,
            git_dir=git_dir,
            main=main,
            expected=dict(expected_external or {}),
        )
    except _Unread as exc:
        return Unreadable(str(exc))
    except trust.Refused as exc:
        return Refused(str(exc))


def _verify(
    get: Get, *, repo: str, sha: str, git_dir: Path, main: str, expected: dict[str, str]
) -> Result:
    if not _REPO.fullmatch(repo):
        raise trust.Refused(f"{repo!r} is not OWNER/NAME")
    if not _SHA.fullmatch(sha):
        raise trust.Refused(f"{sha!r} is not a full commit id")
    app = controller_app(get, repo)
    tree = _tree_of(get, repo, sha)
    named = [r for r in _check_runs(get, repo, sha) if r.get("name") == CHECK_NAME]
    on_head = [r for r in named if r.get("head_sha") == sha]
    ours = [
        r for r in on_head if isinstance(r.get("app"), Mapping) and _int(r["app"].get("id")) == app
    ]
    if not ours:
        others = sorted({_poster(r) for r in on_head})
        posted = f"; posted by {', '.join(others)} instead" if others else ""
        raise trust.Refused(f"no {CHECK_NAME} check run by App {app} on {sha}{posted}")
    newest = max(ours, key=lambda r: int(r["id"]))
    run_id = int(newest["id"])
    status, conclusion = newest.get("status"), newest.get("conclusion")
    if status != "completed" or conclusion != "success":
        raise trust.Refused(
            f"the controller's newest {CHECK_NAME} run on {sha}, {run_id}, is "
            f"{status}{'/' + str(conclusion) if conclusion else ''}, not completed/success"
        )
    receipt = read_receipt(newest)
    if receipt.head_sha != sha:
        raise trust.Refused(f"the receipt names head {receipt.head_sha}, not {sha}")
    if receipt.tree_oid != tree:
        raise trust.Refused(f"the receipt names tree {receipt.tree_oid}, not the head's {tree}")
    trust.require_on_main(git_dir, receipt.controller_revision, main=main)
    for name, value in sorted(expected.items()):
        recorded = receipt.external_revisions.get(name)
        if recorded != value:
            raise trust.Refused(
                f"the receipt's external revision for {name} is {recorded or 'missing'}, "
                f"not the expected {value}"
            )
    return Accepted(receipt=receipt, check_run_id=run_id)


# ── The command ───────────────────────────────────────────────────────────────────────────────


def github_get(api_url: str, token: str) -> Get:
    """Reads from the GitHub API, with the token when one is given."""

    def get(path: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "merge-verdict",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(api_url.rstrip("/") + path, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - the API root
            return json.loads(response.read().decode("utf-8"))

    return get


def _externals(values: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for value in values:
        name, sep, sha = value.partition("=")
        if not sep or not name or not _SHA.fullmatch(sha):
            raise trust.Refused(f"--external {value!r} is not NAME=<full commit id>")
        if name in found:
            raise trust.Refused(f"--external names {name} twice")
        found[name] = sha
    return found


def main(argv: list[str] | None = None, *, get: Get | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci verify-check-run", description=__doc__.splitlines()[0]
    )
    parser.add_argument("sha", help="the pull request's head commit")
    parser.add_argument("--repo", required=True, help="OWNER/NAME")
    parser.add_argument(
        "--external",
        action="append",
        default=[],
        metavar="NAME=SHA",
        help="an external revision the receipt must record (repeatable)",
    )
    parser.add_argument(
        "--git-dir", default=".", type=Path, help="a full clone, to show the controller is on main"
    )
    parser.add_argument("--main", default="origin/main", help="the ref that is main in it")
    args = parser.parse_args(argv)
    try:
        expected = _externals(args.external)
    except trust.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    if get is None:
        env = os.environ
        get = github_get(
            env.get("GITHUB_API_URL", "https://api.github.com"), env.get("GH_TOKEN", "")
        )
    result = verify(
        get,
        repo=args.repo,
        sha=args.sha,
        git_dir=args.git_dir,
        main=args.main,
        expected_external=expected,
    )
    if isinstance(result, Accepted):
        print(f"accepted: {CHECK_NAME} check run {result.check_run_id} on {args.sha}")
        print(json.dumps(dataclasses.asdict(result.receipt), sort_keys=True, separators=(",", ":")))
        return 0
    if isinstance(result, Refused):
        print(f"refused: {result.reason}", file=sys.stderr)
        return 1
    print(f"could not read: {result.reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

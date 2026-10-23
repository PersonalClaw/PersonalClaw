"""The weekly read-only settings report: the live repository settings against the checked-in ones.

``python -m tools.ci settings-report`` reads, for each repository ``.github/settings-expected.json``
names, every setting below from the GitHub API, compares it with that file and with the
repository's own ``.github/rulesets/*.json`` on its default branch, and fails on any difference
(#3907, #4585, #4584). It sends GETs only and writes no setting.

- **Rulesets**, each one's target, enforcement, conditions and rules, against the repository's own
  files: a ruleset missing, changed or not in a file is drift. GitHub returns a ruleset's
  ``bypass_actors`` only to a caller with write access to it, so they are compared only when the
  answer carries them; the bypass *use* is read instead, below.
- **Bypass use:** the week's rule suites whose result is ``bypass``. Any one fails, except an
  actor and ref an ``allowed bypass`` entry names (the release controller's App on tags).
- **Classic protection** on the default branch, or its absence.
- **Merge methods**, **security** (secret scanning, push protection, Dependabot security
  updates), **Dependabot alerts**, **the dependency graph** (which dependency review reads),
  **CodeQL default setup**, **actions** (allowed actions and ``sha_pinning_required``),
  **workflow token** (its default permissions, and whether Actions may approve pull requests),
  **fork approval**, and **environments** (reviewers, wait timer and the deployment branch and
  tag policy).

Rulesets, environments and the dependency graph answer anyone on a public repository. The rest
need administration read, which no workflow token can hold: they are read with
``SETTINGS_READ_TOKEN``, a read-only credential the maintainer keeps in the ``settings-report``
environment, which only ``main`` may deploy to. A setting that cannot be read is drift too
(``not read``), never a pass, so a missing or expired credential fails the report.

With ``--issue-repo`` (the scheduled run on ``main``) it opens or edits the one open issue titled
"Repository settings drift" that the workflow's account opened, and closes it once a run finds
no drift. See docs/maintainers/ci.md, "The settings report".
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API = "https://api.github.com"
EXPECTED = Path(".github/settings-expected.json")
RULESETS_DIR = ".github/rulesets"
ISSUE_TITLE = "Repository settings drift"
BOT = "github-actions[bot]"
GH_TIMEOUT_SECONDS = 60
#: Pages of 100 read from any one listing before it counts as unread: far past any of these.
MAX_PAGES = 10

#: Every setting a repository's entry in the expected file names, in report order. Rulesets
#: and bypass use are read against the rulesets files and ``allowed bypass``.
SETTINGS = (
    "merge methods",
    "classic protection",
    "security",
    "dependabot alerts",
    "dependency graph",
    "codeql default setup",
    "actions",
    "workflow token",
    "fork approval",
    "environments",
    "allowed bypass",
)

#: ``fetch(path)`` returns the HTTP status and the decoded JSON body (None when there is none,
#: the error text when the API gave no answer).
Fetch = Callable[[str], tuple[int, object]]
#: ``query(text, variables)`` asks the GraphQL API, and answers as a fetch does.
Query = Callable[[str, Mapping[str, object]], tuple[int, object]]

#: The merge methods, which the repository answer shows only to a caller that may push, so a
#: read-only credential is asked through GraphQL instead.
MERGE_METHODS_QUERY = (
    "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) "
    "{ mergeCommitAllowed squashMergeAllowed rebaseMergeAllowed } }"
)


@dataclass(frozen=True)
class Unread:
    """A setting the report could not read, which fails it like any other difference."""

    why: str


@dataclass(frozen=True)
class Drift:
    repo: str
    setting: str
    expected: object
    live: object

    def line(self) -> str:
        if isinstance(self.live, Unread):
            return (
                f"{self.repo} {self.setting}: not read ({self.live.why}); "
                f"expected {_show(self.expected)}"
            )
        parts = _differences(self.expected, self.live)
        return f"{self.repo} {self.setting}: " + "; ".join(
            f"{where + ' ' if where else ''}expected {_show(want)}, live {_show(got)}"
            for where, want, got in parts
        )


def _show(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _differences(want: object, got: object, where: str = "") -> list[tuple[str, object, object]]:
    """Where two values differ: down through objects both sides have, so a line names the key."""
    if isinstance(want, dict) and isinstance(got, dict):
        found: list[tuple[str, object, object]] = []
        for key in sorted(set(want) | set(got)):
            at = f"{where}.{key}" if where else str(key)
            found += _differences(want.get(key), got.get(key), at)
        return found
    return [] if want == got else [(where, want, got)]


def github_fetch(token: str | None) -> Fetch:
    def fetch(path: str) -> tuple[int, object]:
        request = urllib.request.Request(API + path, method="GET")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"null")
            except ValueError:
                return error.code, None
        except (OSError, http.client.HTTPException, ValueError) as error:
            return 0, f"{type(error).__name__}: {error}"

    return fetch


def github_query(token: str | None) -> Query:
    def query(text: str, variables: Mapping[str, object]) -> tuple[int, object]:
        body = json.dumps({"query": text, "variables": dict(variables)}).encode()
        request = urllib.request.Request(API + "/graphql", data=body, method="POST")
        request.add_header("Accept", "application/vnd.github+json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, None
        except (OSError, http.client.HTTPException, ValueError) as error:
            return 0, f"{type(error).__name__}: {error}"

    return query


def _merge_methods(repo: str, queries: Sequence[Query]) -> dict[str, object] | Unread:
    """The merge methods from GraphQL, asked of each credential in turn until one shows them."""
    owner, _, name = repo.partition("/")
    why = "merge methods not shown, and no GraphQL credential to ask"
    for query in queries:
        status, body = query(MERGE_METHODS_QUERY, {"owner": owner, "name": name})
        data = body.get("data") if isinstance(body, dict) else None
        found = (data or {}).get("repository") if isinstance(data, dict) else None
        keys = {
            "allow_merge_commit": "mergeCommitAllowed",
            "allow_squash_merge": "squashMergeAllowed",
            "allow_rebase_merge": "rebaseMergeAllowed",
        }
        if (
            status == 200
            and isinstance(found, dict)
            and all(isinstance(found.get(v), bool) for v in keys.values())
        ):
            return {k: found[v] for k, v in keys.items()}
        errors = body.get("errors") if isinstance(body, dict) else None
        why = f"merge methods not shown; GraphQL answered {status}" + (
            f": {errors[0].get('message')}" if isinstance(errors, list) and errors else ""
        )
    return Unread(why)


def _unread(path: str, status: int, body: object) -> Unread:
    message = body.get("message") if isinstance(body, Mapping) else body
    return Unread(f"GET {path} answered {status}" + (f": {message}" if message else ""))


def _get(fetch: Fetch, path: str) -> dict[str, Any] | Unread:
    status, body = fetch(path)
    if status == 200 and isinstance(body, dict):
        return body
    return _unread(path, status, body)


def _pages(fetch: Fetch, path: str, key: str | None = None) -> list[Any] | Unread:
    """Every item of a listing, page by page; a short page ends it."""
    items: list[Any] = []
    joiner = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        at = f"{path}{joiner}per_page=100&page={page}"
        status, body = fetch(at)
        batch = body.get(key) if key and isinstance(body, dict) else body
        if status != 200 or not isinstance(batch, list):
            return _unread(at, status, body)
        items += batch
        if len(batch) < 100:
            return items
    return Unread(f"GET {path} has more than {MAX_PAGES * 100} items")


# --- Rulesets ------------------------------------------------------------------------------

#: The fields of a ruleset that say what it enforces; GitHub's ids, links and times are not.
RULESET_FIELDS = ("target", "enforcement", "conditions", "rules")


def normalize_ruleset(ruleset: Mapping[str, Any]) -> dict[str, Any]:
    """What a ruleset enforces, in one order, from a file or from the API alike."""
    out: dict[str, Any] = {k: ruleset.get(k) for k in RULESET_FIELDS}
    rules = ruleset.get("rules")
    if isinstance(rules, list):
        out["rules"] = sorted(
            ({k: v for k, v in r.items() if k in ("type", "parameters")} for r in rules),
            key=lambda r: (str(r.get("type")), _show(r.get("parameters"))),
        )
    if "bypass_actors" in ruleset:
        out["bypass_actors"] = sorted(
            (
                {k: a.get(k) for k in ("actor_type", "actor_id", "bypass_mode")}
                for a in ruleset.get("bypass_actors") or []
            ),
            key=_show,
        )
    return out


def ruleset_files(fetch: Fetch, repo: str) -> dict[str, dict[str, Any]] | Unread:
    """The repository's own ``.github/rulesets/*.json`` on its default branch, by ruleset name."""
    path = f"/repos/{repo}/contents/{RULESETS_DIR}"
    status, listing = fetch(path)
    if status == 404:
        return {}
    if status != 200 or not isinstance(listing, list):
        return _unread(path, status, listing)
    files: dict[str, dict[str, Any]] = {}
    for entry in listing:
        name = entry.get("name") if isinstance(entry, Mapping) else None
        if not isinstance(name, str) or not name.endswith(".json"):
            continue
        at = f"/repos/{repo}/contents/{RULESETS_DIR}/{urllib.parse.quote(name)}"
        got = _get(fetch, at)
        if isinstance(got, Unread):
            return got
        try:
            data = json.loads(base64.b64decode(str(got.get("content", ""))))
        except ValueError as error:
            return Unread(f"{RULESETS_DIR}/{name} is not JSON: {error}")
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            return Unread(f"{RULESETS_DIR}/{name} names no ruleset")
        files[data["name"]] = data
    return files


def live_rulesets(fetch: Fetch, repo: str) -> dict[str, dict[str, Any]] | Unread:
    listing = _pages(fetch, f"/repos/{repo}/rulesets?includes_parents=false")
    if isinstance(listing, Unread):
        return listing
    out: dict[str, dict[str, Any]] = {}
    for item in listing:
        got = _get(fetch, f"/repos/{repo}/rulesets/{item.get('id')}")
        if isinstance(got, Unread):
            return got
        out[str(got.get("name"))] = got
    return out


def compare_rulesets(repo: str, files: object, live: object) -> list[Drift]:
    if isinstance(files, Unread) or isinstance(live, Unread):
        return [
            Drift(
                repo, "rulesets", "the rulesets files", files if isinstance(files, Unread) else live
            )
        ]
    assert isinstance(files, dict) and isinstance(live, dict)
    drift: list[Drift] = []
    for name in sorted(set(files) | set(live)):
        want = normalize_ruleset(files[name]) if name in files else None
        got = normalize_ruleset(live[name]) if name in live else None
        if want is not None and got is not None and "bypass_actors" not in got:
            want.pop("bypass_actors", None)  # not shown to a read-only caller: see bypass use
        if want != got:
            drift.append(Drift(repo, f"ruleset {name}", want, got))
    return drift


def bypass_use(fetch: Fetch, repo: str, allowed: object) -> list[Drift]:
    """Each push in the last week that bypassed a rule, unless ``allowed bypass`` names it."""
    suites = _pages(
        fetch, f"/repos/{repo}/rulesets/rule-suites?time_period=week&rule_suite_result=bypass"
    )
    if isinstance(suites, Unread):
        return [Drift(repo, "bypass use", "none", suites)]
    ok = allowed if isinstance(allowed, list) else []
    drift: list[Drift] = []
    for suite in suites:
        actor, ref = suite.get("actor_name"), str(suite.get("ref", ""))
        if any(
            isinstance(a, Mapping)
            and a.get("actor_name") == actor
            and ref.startswith(str(a.get("ref_prefix")))
            for a in ok
        ):
            continue
        drift.append(
            Drift(
                repo,
                "bypass use",
                "none",
                {k: suite.get(k) for k in ("actor_name", "ref", "pushed_at", "result")},
            )
        )
    return drift


# --- The other settings --------------------------------------------------------------------


def _protection(fetch: Fetch, repo: str, branch: str) -> object:
    path = f"/repos/{repo}/branches/{branch}/protection"
    status, body = fetch(path)
    if (
        status == 404
        and isinstance(body, Mapping)
        and body.get("message") == "Branch not protected"
    ):
        return None
    if status != 200 or not isinstance(body, dict):
        return _unread(path, status, body)

    def on(key: str) -> object:
        value = body.get(key)
        return value.get("enabled") if isinstance(value, Mapping) else None

    reviews = body.get("required_pull_request_reviews")
    checks = body.get("required_status_checks")
    return {
        "required_pull_request_reviews": (
            None
            if not isinstance(reviews, Mapping)
            else {
                k: reviews.get(k)
                for k in (
                    "dismiss_stale_reviews",
                    "require_code_owner_reviews",
                    "require_last_push_approval",
                    "required_approving_review_count",
                )
            }
        ),
        "required_status_checks": (
            None
            if not isinstance(checks, Mapping)
            else {
                "strict": checks.get("strict"),
                "checks": sorted(
                    (
                        {"context": c.get("context"), "app_id": c.get("app_id")}
                        for c in checks.get("checks") or []
                    ),
                    key=_show,
                ),
            }
        ),
        **{
            k: on(k)
            for k in (
                "enforce_admins",
                "required_signatures",
                "required_linear_history",
                "allow_force_pushes",
                "allow_deletions",
                "block_creations",
                "required_conversation_resolution",
                "lock_branch",
                "allow_fork_syncing",
            )
        },
    }


def _reviewer(entry: Mapping[str, Any]) -> str:
    """``User:<login>`` or ``Team:<slug>``."""
    who = entry.get("reviewer") or {}
    return f"{entry.get('type')}:{who.get('login') or who.get('slug')}"


def _environments(fetch: Fetch, repo: str) -> object:
    listing = _pages(fetch, f"/repos/{repo}/environments", key="environments")
    if isinstance(listing, Unread):
        return listing
    out: dict[str, Any] = {}
    for env in listing:
        name = str(env.get("name"))
        entry: dict[str, Any] = {"reviewers": [], "prevent_self_review": False, "wait_timer": 0}
        for rule in env.get("protection_rules") or []:
            if rule.get("type") == "required_reviewers":
                entry["reviewers"] = sorted(_reviewer(r) for r in rule.get("reviewers") or [])
                entry["prevent_self_review"] = bool(rule.get("prevent_self_review"))
            elif rule.get("type") == "wait_timer":
                entry["wait_timer"] = rule.get("wait_timer")
            elif rule.get("type") == "branch_policy":
                pass  # the deployment policy below, which GitHub lists as a rule too
            else:
                entry.setdefault("other_rules", []).append(rule.get("type"))
        policy = env.get("deployment_branch_policy")
        if not isinstance(policy, Mapping):
            entry["deployment"] = "any ref"
        elif policy.get("protected_branches"):
            entry["deployment"] = "protected branches"
        else:
            quoted = urllib.parse.quote(name, safe="")
            refs = _pages(
                fetch,
                f"/repos/{repo}/environments/{quoted}/deployment-branch-policies",
                key="branch_policies",
            )
            if isinstance(refs, Unread):
                return refs
            entry["deployment"] = sorted(f"{p.get('type', 'branch')}:{p.get('name')}" for p in refs)
        out[name] = entry
    return out


def _dependency_graph(fetch: Fetch, repo: str) -> object:
    """Whether GitHub keeps the repository's dependency graph: its SBOM answers 200 when it does
    and 404 when it does not (dependency review then fails on every pull request)."""
    path = f"/repos/{repo}/dependency-graph/sbom"
    status, body = fetch(path)
    if status == 200 and isinstance(body, dict) and isinstance(body.get("sbom"), dict):
        return True
    if status == 404:
        return False
    return _unread(path, status, body)


def read_settings(
    repo: str, public: Fetch, admin: Fetch | None, queries: Sequence[Query] = ()
) -> dict[str, object]:
    """Every setting in ``SETTINGS`` but ``allowed bypass``, as the API answers it, or Unread."""
    no_token = Unread(
        "no SETTINGS_READ_TOKEN: administration read needs the maintainer's read-only token"
    )
    live: dict[str, object] = {
        "environments": _environments(public, repo),
        "dependency graph": _dependency_graph(public, repo),
    }
    if admin is None:
        for name in SETTINGS:
            live.setdefault(name, no_token)
        live.pop("allowed bypass")
        return live
    meta = _get(admin, f"/repos/{repo}")
    branch = "main" if isinstance(meta, Unread) else str(meta.get("default_branch") or "main")
    if isinstance(meta, Unread):
        live["merge methods"] = live["security"] = meta
    else:
        methods = {
            k: meta.get(k)
            for k in ("allow_merge_commit", "allow_squash_merge", "allow_rebase_merge")
        }
        live["merge methods"] = (
            methods if None not in methods.values() else _merge_methods(repo, queries)
        )
        sa = meta.get("security_and_analysis")
        keys = ("secret_scanning", "secret_scanning_push_protection", "dependabot_security_updates")
        if isinstance(sa, Mapping) and all(isinstance(sa.get(k), Mapping) for k in keys):
            live["security"] = {k: sa[k].get("status") for k in keys}
        else:
            live["security"] = Unread("security_and_analysis not shown (admin rights needed)")
    live["classic protection"] = _protection(admin, repo, branch)
    actions = _get(admin, f"/repos/{repo}/actions/permissions")
    live["actions"] = (
        actions
        if isinstance(actions, Unread)
        else {k: actions.get(k) for k in ("enabled", "allowed_actions", "sha_pinning_required")}
    )
    if isinstance(actions, dict) and actions.get("allowed_actions") == "selected":
        selected = _get(admin, f"/repos/{repo}/actions/permissions/selected-actions")
        if isinstance(selected, Unread):
            live["actions"] = selected
        else:
            assert isinstance(live["actions"], dict)
            live["actions"]["selected"] = selected
    workflow = _get(admin, f"/repos/{repo}/actions/permissions/workflow")
    live["workflow token"] = (
        workflow
        if isinstance(workflow, Unread)
        else {
            k: workflow.get(k)
            for k in ("default_workflow_permissions", "can_approve_pull_request_reviews")
        }
    )
    fork = _get(admin, f"/repos/{repo}/actions/permissions/fork-pr-contributor-approval")
    live["fork approval"] = fork if isinstance(fork, Unread) else fork.get("approval_policy")
    codeql = _get(admin, f"/repos/{repo}/code-scanning/default-setup")
    live["codeql default setup"] = (
        codeql
        if isinstance(codeql, Unread)
        else {
            "state": codeql.get("state"),
            "query_suite": codeql.get("query_suite"),
            "languages": sorted(codeql.get("languages") or []),
        }
    )
    # 204 enabled, 404 disabled, but only from a caller whose administration read was just shown:
    # a token without it is answered 403 or 404 alike.
    path = f"/repos/{repo}/vulnerability-alerts"
    status, body = admin(path)
    if status == 204 or (status == 200 and body is None):
        live["dependabot alerts"] = True
    elif status == 404 and not isinstance(actions, Unread):
        live["dependabot alerts"] = False
    else:
        live["dependabot alerts"] = _unread(path, status, body)
    return live


# --- The comparison ------------------------------------------------------------------------


def load_expected(path: Path) -> dict[str, dict[str, Any]]:
    """The expected file, every repository naming exactly ``SETTINGS``; ValueError otherwise."""
    data = json.loads(path.read_text(encoding="utf-8"))
    repos = data.get("repositories") if isinstance(data, dict) else None
    if not isinstance(repos, dict) or not repos:
        raise ValueError(f"{path}: no repositories")
    for repo, entry in repos.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: {repo} is not an object")
        missing, extra = set(SETTINGS) - set(entry), set(entry) - set(SETTINGS)
        if missing or extra:
            raise ValueError(
                f"{path}: {repo} must name exactly the report's settings"
                + (f"; missing {sorted(missing)}" if missing else "")
                + (f"; unknown {sorted(extra)}" if extra else "")
            )
    return repos


def compare(repo: str, expected: Mapping[str, Any], live: Mapping[str, object]) -> list[Drift]:
    drift: list[Drift] = []
    for name in SETTINGS:
        if name == "allowed bypass":
            continue
        got = live.get(name, Unread("not read"))
        if isinstance(got, Unread) or got != expected[name]:
            drift.append(Drift(repo, name, expected[name], got))
    return drift


def report(
    expected: Mapping[str, Mapping[str, Any]],
    public: Fetch,
    admin: Fetch | None,
    queries: Sequence[Query] = (),
) -> list[Drift]:
    drift: list[Drift] = []
    for repo in sorted(expected):
        drift += compare_rulesets(repo, ruleset_files(public, repo), live_rulesets(public, repo))
        drift += compare(repo, expected[repo], read_settings(repo, public, admin, queries))
        if admin is None:
            drift.append(Drift(repo, "bypass use", "none", Unread("no SETTINGS_READ_TOKEN")))
        else:
            drift += bypass_use(admin, repo, expected[repo]["allowed bypass"])
    return drift


def markdown(drift: Sequence[Drift], run_url: str) -> str:
    if not drift:
        return f"No settings drift: every setting matches its checked-in value. From {run_url}.\n"
    lines = [
        f"The weekly settings report found {len(drift)} difference(s) between the live repository "
        "settings and `.github/settings-expected.json` or a repository's `.github/rulesets/`. "
        "Put the setting back, or change the file in a pull request that says why "
        '(docs/maintainers/ci.md, "The settings report").',
        "",
    ]
    lines += [f"- {d.line()}" for d in drift]
    lines += ["", f"From {run_url}."]
    return "\n".join(lines) + "\n"


# --- The issue -----------------------------------------------------------------------------


def _gh(*args: str) -> str:
    done = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
        timeout=GH_TIMEOUT_SECONDS,
    )
    return done.stdout


def _ours(repo: str, gh: Callable[..., str]) -> list[int]:
    found: list[int] = []
    for page in range(1, MAX_PAGES + 1):
        batch = json.loads(
            gh(
                "api",
                f"repos/{repo}/issues?state=open&creator=github-actions%5Bbot%5D"
                f"&per_page=100&page={page}",
            )
        )
        if not isinstance(batch, list):
            raise ValueError("the open issues are not a list")
        found += [
            int(i["number"])
            for i in batch
            if i.get("title") == ISSUE_TITLE
            and isinstance(i.get("user"), Mapping)
            and i["user"].get("login") == BOT
            and "pull_request" not in i
        ]
        if len(batch) < 100:
            return sorted(found)
    raise ValueError(f"more than {MAX_PAGES * 100} open issues: the drift issue may be unseen")


def publish(repo: str, body: str, drifted: bool, gh: Callable[..., str] = _gh) -> int | None:
    """Open or edit the one drift issue when *drifted*; otherwise close it if one is open."""
    ours = _ours(repo, gh)
    with tempfile.TemporaryDirectory() as scratch:
        text = Path(scratch) / "body.md"
        text.write_text(body, encoding="utf-8")
        if drifted and not ours:
            url = gh(
                "issue", "create", "-R", repo, "--title", ISSUE_TITLE, "--body-file", str(text)
            )
            return int(url.strip().rsplit("/", 1)[-1])
        if drifted:
            gh("issue", "edit", str(ours[0]), "-R", repo, "--body-file", str(text))
            return ours[0]
        for number in ours:
            gh("issue", "close", str(number), "-R", repo, "--comment", body.strip())
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci settings-report", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--expected", type=Path, default=EXPECTED)
    parser.add_argument(
        "--issue-repo", help="open, edit or close the drift issue in this repository"
    )
    parser.add_argument("--run-url", default="a local run")
    args = parser.parse_args(argv)
    try:
        expected = load_expected(args.expected)
    except (OSError, ValueError) as error:
        print(f"settings-report: {error}", file=sys.stderr)
        return 2
    job_token = os.environ.get("GH_TOKEN") or None
    public = github_fetch(job_token)
    read_token = os.environ.get("SETTINGS_READ_TOKEN") or None
    admin = github_fetch(read_token) if read_token else None
    # GraphQL needs a credential: the read token first, then the job token.
    queries = [github_query(t) for t in (read_token, job_token) if t]
    drift = report(expected, public, admin, queries)
    body = markdown(drift, args.run_url)
    print(body, end="")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("## Settings report\n\n" + body)
    if args.issue_repo:
        number = publish(args.issue_repo, body, bool(drift))
        if number is not None:
            print(f"drift issue: #{number}")
    for d in drift:
        print(f"::error title=settings drift::{d.line()}")
    return 1 if drift else 0

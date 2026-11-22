"""``.github/workflows/pin-refresh.yml`` and ``tools/ci/pin_refresh.py`` (#4585).

The weekly refresh runs only on its schedule and a dispatch, on main, from one job in the
``pin-refresh`` environment whose own token reads only and whose checkout keeps no credential.
Its writes go through the pin-refresh App's token, minted from ``vars.REFRESH_APP_CLIENT_ID``
and ``secrets.REFRESH_APP_KEY``, which asks for workflows write only when a workflow file changed.
The tool moves each image whose tag has moved and each action to its newest week-old release on
its major line (or its own moved tag), every copy in the tree, and opens the edit on a branch of
its own with the token kept off every command line. Each rule fails on a planted edit.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest
import yaml
from rails_repo import REPO

from tools.ci import pin_refresh

_WORKFLOW = REPO / ".github" / "workflows" / "pin-refresh.yml"
_ON_MAIN = (
    "${{ github.ref == 'refs/heads/main' && github.repository == 'PersonalClaw/PersonalClaw' }}"
)
#: The App named by its client ID, which create-github-app-token takes in place of its
#: deprecated ``app-id`` input. Not a secret: ``gh api /apps/personalclaw-refresh`` shows it.
_CLIENT_ID = "${{ vars.REFRESH_APP_CLIENT_ID }}"
_APP_KEY = "${{ secrets.REFRESH_APP_KEY }}"
#: What a token step holds, closed: the App, its key and what the token may do, nothing past them.
#: create-github-app-token also takes ``github-api-url`` (the App's signed request to another
#: host, where whoever holds it mints tokens for its life past every permission line), ``owner``,
#: ``repositories`` and ``enterprise`` (more repositories than this one) and ``skip-token-revoke``
#: (a token alive past the job). A step's own ``env`` reaches the action's node before it runs
#: (``NODE_OPTIONS``), with the key among its inputs.
_TOKEN_INPUTS = {"client-id", "private-key"}
_TOKEN_STEP_KEYS = {"name", "id", "if", "uses", "with"}
#: The workflow's and the job's own keys, closed: an ``env`` or ``defaults`` on either reaches every
#: step, the token steps' action among them (``NODE_OPTIONS`` runs before it, beside the key).
_WORKFLOW_KEYS = {"name", "on", "concurrency", "permissions", "jobs"}
_JOB_KEYS = {"if", "runs-on", "environment", "timeout-minutes", "permissions", "steps"}


def _problems(text: str) -> list[str]:
    data = yaml.safe_load(text)
    found: list[str] = []
    on = data.get("on", data.get(True))
    keys = {"on" if key is True else str(key) for key in data}
    if keys - _WORKFLOW_KEYS:
        found.append(f"the workflow sets {sorted(keys - _WORKFLOW_KEYS)}, which reaches every step")
    if not isinstance(on, dict) or set(on) != {"schedule", "workflow_dispatch"}:
        found.append(f"runs on {on!r}, not only its schedule and a dispatch")
    if data.get("permissions") != {"contents": "read"}:
        found.append(f"the workflow holds {data.get('permissions')!r}")
    jobs = data.get("jobs") or {}
    if set(jobs) != {"refresh"}:
        return [*found, f"has jobs {sorted(jobs)}"]
    job = jobs["refresh"]
    if set(job) - _JOB_KEYS:
        found.append(f"the job sets {sorted(set(job) - _JOB_KEYS)}, which reaches every step")
    if job.get("if") != _ON_MAIN:
        found.append("the job runs off main")
    if job.get("environment") != "pin-refresh":
        found.append(f"the job's environment is {job.get('environment')!r}")
    if job.get("permissions") != {"contents": "read"}:
        found.append(f"the job holds {job.get('permissions')!r}")
    tokens = []
    for step in job.get("steps") or []:
        uses = str(step.get("uses", ""))
        given = step.get("with") or {}
        if uses.startswith("actions/checkout@") and given.get("persist-credentials") is not False:
            found.append("the checkout keeps its credentials")
        if "GITHUB_ENV" in str(step.get("run", "")):
            found.append(
                f"{step.get('name') or uses} writes the job's environment ($GITHUB_ENV), which"
                " every later step's action reads"
            )
        if uses.startswith("actions/create-github-app-token@"):
            tokens.append(step)
            if given.get("client-id") != _CLIENT_ID or given.get("private-key") != _APP_KEY:
                found.append(f"{step.get('id')} mints its token from another App or key")
            if "app-id" in given:
                found.append(f"{step.get('id')} names its App by the deprecated app-id too")
            extra = sorted(
                k
                for k in given
                if k not in _TOKEN_INPUTS | {"app-id"} and not k.startswith("permission-")
            )
            if extra:
                found.append(f"{step.get('id')} gives the token action {extra} too")
            if set(step) - _TOKEN_STEP_KEYS:
                found.append(
                    f"{step.get('id')} sets {sorted(set(step) - _TOKEN_STEP_KEYS)} on its step"
                )
            asks = {k: v for k, v in given.items() if k.startswith("permission-")}
            wants = {"permission-contents": "write", "permission-pull-requests": "write"}
            if "workflows == 'true'" in str(step.get("if")):
                wants["permission-workflows"] = "write"
            if asks != wants:
                found.append(f"{step.get('id')} asks for {asks}")
        if "secrets." in json.dumps(step) and not uses.startswith(
            "actions/create-github-app-token@"
        ):
            found.append(f"{step.get('name') or uses} reads a secret")
    if len(tokens) != 2:
        found.append(f"{len(tokens)} App-token steps, not the two")
    return found


def test_the_workflow_writes_only_through_the_apps_token_on_main() -> None:
    assert _problems(_WORKFLOW.read_text(encoding="utf-8")) == []


_PR_WRITE = "          permission-pull-requests: write\n"
#: The workflow's grant before its jobs, and the job's grant before its steps.
_TOP_GRANT = "permissions:\n  contents: read\n\njobs:\n"
_JOB_STEPS = "    permissions:\n      contents: read\n    steps:\n"
#: The first token step from its inputs to its key, where the plants below add to it.
_KEY_LINE = (
    "        with:\n          client-id: ${{ vars.REFRESH_APP_CLIENT_ID }}\n"
    "          private-key: ${{ secrets.REFRESH_APP_KEY }}\n"
)
_WORKFLOWS_WRITE = "          permission-workflows: write\n"
#: The token step that asks for workflows write, from its key to its last permission.
_WITH_WORKFLOWS = (
    "${{ secrets.REFRESH_APP_KEY }}\n          permission-contents: write\n"
    + _PR_WRITE
    + _WORKFLOWS_WRITE
)
#: The end of the token step that does not, before the step that opens the pull request.
_BEFORE_OPEN = _PR_WRITE + "      - name: Open"


@pytest.mark.parametrize(
    "old, new, says",
    [
        ("  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request:\n", "runs on"),
        (
            "    permissions:\n      contents: read\n    steps:",
            "    permissions:\n      contents: write\n    steps:",
            "the job holds",
        ),
        ("    environment: pin-refresh\n", "", "environment"),
        ("github.ref == 'refs/heads/main' && ", "", "off main"),
        ("          persist-credentials: false\n", "          fetch-depth: 1\n", "credentials"),
        (
            _WITH_WORKFLOWS,
            _WITH_WORKFLOWS.replace("REFRESH_APP_KEY", "CONTROLLER_APP_KEY"),
            "another App",
        ),
        (_BEFORE_OPEN, _BEFORE_OPEN.replace(_PR_WRITE, _PR_WRITE + _WORKFLOWS_WRITE), "asks for"),
        # Another App's client ID, or the deprecated input back beside it.
        (
            "          client-id: ${{ vars.REFRESH_APP_CLIENT_ID }}\n",
            "          client-id: ${{ vars.CONTROLLER_APP_CLIENT_ID }}\n",
            "another App",
        ),
        (
            "          client-id: ${{ vars.REFRESH_APP_CLIENT_ID }}\n",
            "          client-id: ${{ vars.REFRESH_APP_CLIENT_ID }}\n"
            "          app-id: ${{ vars.REFRESH_APP_ID }}\n",
            "deprecated app-id",
        ),
        (
            "          client-id: ${{ vars.REFRESH_APP_CLIENT_ID }}\n",
            "          app-id: ${{ vars.REFRESH_APP_ID }}\n",
            "another App",
        ),
        (
            "          RUN_ID: ${{ github.run_id }}",
            "          RUN_ID: ${{ github.run_id }}\n          KEY: ${{ secrets.REFRESH_APP_KEY }}",
            "reads a secret",
        ),
        # Any input past the App, its key and its permissions, and any key past a step's own.
        *(
            (_KEY_LINE, _KEY_LINE + f"          {line}\n", "gives the token action")
            for line in (
                "github-api-url: https://example.invalid/api/v3",
                "owner: someone-else",
                "repositories: PersonalClaw,PersonalClawApps",
                "enterprise: an-enterprise",
                "skip-token-revoke: true",
            )
        ),
        (
            _KEY_LINE,
            _KEY_LINE.replace(
                "        with:\n",
                "        env:\n          NODE_OPTIONS: --require ./x.js\n        with:\n",
            ),
            "on its step",
        ),
        (
            _KEY_LINE,
            _KEY_LINE.replace(
                "        with:\n", "        continue-on-error: true\n        with:\n"
            ),
            "on its step",
        ),
        # Or past the step: the workflow's or the job's env or defaults, or the job's environment
        # written by an earlier step.
        (_TOP_GRANT, "env:\n  NODE_OPTIONS: --require ./x.js\n" + _TOP_GRANT, "the workflow sets"),
        (_TOP_GRANT, "defaults:\n  run:\n    shell: bash\n" + _TOP_GRANT, "the workflow sets"),
        (
            _JOB_STEPS,
            _JOB_STEPS.replace(
                "    steps:\n", "    env:\n      NODE_OPTIONS: --require ./x.js\n    steps:\n"
            ),
            "the job sets",
        ),
        (
            _JOB_STEPS,
            _JOB_STEPS.replace(
                "    steps:\n", "    defaults:\n      run:\n        shell: bash\n    steps:\n"
            ),
            "the job sets",
        ),
        (
            '${RUNNER_ARCH}"\n',
            '${RUNNER_ARCH}"; echo "NODE_OPTIONS=--require ./x.js" >> "$GITHUB_ENV"\n',
            "writes the job's environment",
        ),
    ],
)
def test_each_rule_fails_on_a_planted_edit(old: str, new: str, says: str) -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert old in text, f"the plant's anchor is not in the workflow: {old!r}"
    assert any(says in p for p in _problems(text.replace(old, new, 1)))


#: The pin-refresh App's ID (#4585), which no ruleset may name as a bypass actor; not a secret.
REFRESH_APP_ID = 5868116
_RULESETS = REPO / ".github" / "rulesets"
_RULESET_API = re.compile(r"/rulesets\b|\brulesets/")


def _bypassers(rulesets: dict[str, dict]) -> list[str]:
    return [
        name
        for name, ruleset in sorted(rulesets.items())
        if any(a.get("actor_id") == REFRESH_APP_ID for a in ruleset.get("bypass_actors") or [])
    ]


def test_no_ruleset_lets_the_refresh_app_bypass_it() -> None:
    """The refresh merges like any other pull request: the App is no bypass actor anywhere."""
    rulesets = {p.name: json.loads(p.read_text()) for p in sorted(_RULESETS.glob("*.json"))}
    assert rulesets and _bypassers(rulesets) == []
    planted = {**rulesets["main-protect.json"]}
    planted["bypass_actors"] = [
        {"actor_id": REFRESH_APP_ID, "actor_type": "Integration", "bypass_mode": "always"}
    ]
    assert _bypassers({**rulesets, "main-protect.json": planted}) == ["main-protect.json"]


def test_the_refresh_reads_and_changes_no_ruleset() -> None:
    for path in (_WORKFLOW, REPO / "tools" / "ci" / "pin_refresh.py"):
        assert not _RULESET_API.search(path.read_text(encoding="utf-8")), path.name
    assert _RULESET_API.search('gh api "repos/o/r/rulesets/12"'), "the pattern reads no API path"


NOW = dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc)
OLD, MOVED, NEWER = "a" * 40, "b" * 40, "c" * 40


def _release(tag: str, days_ago: int, **extra: object) -> dict[str, object]:
    when = (NOW - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {"tag_name": tag, "published_at": when, "html_url": f"https://x/{tag}", **extra}


def _fetch(answers: dict[str, tuple[int, object]]):
    return lambda path: answers.get(path, (404, None))


def _tag(sha: str) -> tuple[int, object]:
    return 200, {"object": {"type": "commit", "sha": sha}}


def test_the_newest_week_old_release_on_the_major_line_is_chosen() -> None:
    releases = [
        _release("v1.2.0", 30),
        _release("v1.4.0", 10),
        _release("v1.5.0", 3),  # inside the cooldown
        _release("v1.6.0", 20, prerelease=True),
        _release("v1.7.0", 20, draft=True),
        _release("v2.0.0", 40),  # another major line
    ]
    fetch = _fetch({"/repos/o/r/releases?per_page=100": (200, releases)})
    assert pin_refresh.newest_release(fetch, "o/r", "v1.2.0", NOW) == ("v1.4.0", "https://x/v1.4.0")
    assert pin_refresh.newest_release(fetch, "o/r", "v1.4.0", NOW) is None
    assert pin_refresh.newest_release(_fetch({}), "o/r", "v1.2.0", NOW) is None


def test_an_action_moves_to_its_newer_release_or_its_moved_tag() -> None:
    files = [
        ("a.yml", f"      - uses: o/new/sub@{OLD} # v1.0.0\n"),
        ("b.yml", f"      - uses: o/moved@{OLD} # v3.1.0\n"),
        ("c.yml", f"      - uses: o/gone@{OLD} # v1.0.0\n"),
    ]
    fetch = _fetch(
        {
            "/repos/o/new/git/ref/tags/v1.0.0": _tag(OLD),
            "/repos/o/new/releases?per_page=100": (200, [_release("v1.1.0", 9)]),
            "/repos/o/new/git/ref/tags/v1.1.0": _tag(NEWER),
            "/repos/o/moved/git/ref/tags/v3.1.0": _tag(MOVED),
        }
    )
    updates, problems = pin_refresh.plan_actions(files, fetch, NOW)
    assert {(u.repository, u.new_sha, u.new_tag) for u in updates} == {
        ("o/new", NEWER, "v1.1.0"),
        ("o/moved", MOVED, "v3.1.0"),
    }
    assert problems == ["c.yml:1: o/gone v1.0.0: the tag is gone"]


_INDEX, _DIGEST = "sha256:" + "1" * 64, "sha256:" + "2" * 64
_NEW_INDEX, _NEW_DIGEST = "sha256:" + "3" * 64, "sha256:" + "4" * 64
_IMAGE_PIN = (
    f"    # the linux/amd64 manifest of python:3.12-slim, of index {_INDEX}\n"
    f"    container: python:3.12-slim@{_DIGEST}\n"
)


def _registry(url: str, headers: object) -> tuple[int, dict[str, str], bytes]:
    if url.startswith("https://auth.docker.io/token"):
        return 200, {}, b'{"token": "t"}'
    if url == "https://registry-1.docker.io/v2/library/python/manifests/3.12-slim":
        index = {
            "manifests": [
                {"platform": {"os": "linux", "architecture": "amd64"}, "digest": _NEW_DIGEST}
            ]
        }
        return 200, {"docker-content-digest": _NEW_INDEX}, json.dumps(index).encode()
    return 404, {}, b""


def test_a_moved_image_and_a_newer_action_move_every_copy_in_the_tree(tmp_path: Path) -> None:
    workflow = f"jobs:\n  j:\n{_IMAGE_PIN}    steps:\n      - uses: o/new@{OLD} # v1.0.0\n"
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "w.yml").write_text(workflow)
    (tmp_path / "catalogue.toml").write_text(f'digest = "{_DIGEST}"\nsha = "{OLD}"\n')
    (tmp_path / "other.txt").write_text("nothing pinned here\n")
    images, problems = pin_refresh.plan_images([("w.yml", workflow)], _registry)
    assert problems == [] and len(images) == 1
    action = pin_refresh.ActionUpdate("o/new", OLD, "v1.0.0", NEWER, "v1.1.0", "https://x/v1.1.0")
    changed = pin_refresh.apply(
        tmp_path, [".github/workflows/w.yml", "catalogue.toml", "other.txt"], [action], images
    )
    assert changed == [".github/workflows/w.yml", "catalogue.toml"]
    moved = (tmp_path / ".github" / "workflows" / "w.yml").read_text()
    assert f"o/new@{NEWER} # v1.1.0" in moved and f"index {_NEW_INDEX}" in moved
    assert f"python:3.12-slim@{_NEW_DIGEST}" in moved and OLD not in moved and _DIGEST not in moved
    assert (
        tmp_path / "catalogue.toml"
    ).read_text() == f'digest = "{_NEW_DIGEST}"\nsha = "{NEWER}"\n'


def _hex(digest: str) -> str:
    return digest.removeprefix("sha256:")


def test_a_bare_copy_of_a_moved_digest_moves_too(tmp_path: Path) -> None:
    """🔴 Red before: a digest kept without its ``sha256:`` prefix (a rail's control constant)
    stayed on the old index, so the first refresh's own rails planted against a pin the tree no
    longer held (#6383)."""
    image = pin_refresh.ImageUpdate(
        "ubuntu", "26.04", "linux/amd64", _INDEX, _DIGEST, _NEW_INDEX, _NEW_DIGEST
    )
    (tmp_path / "test_rail.py").write_text(f'_UBUNTU_INDEX = "{_hex(_INDEX)}"\n')
    (tmp_path / "longer.txt").write_text(f"{_hex(_INDEX)}0\n{'f' + _hex(_DIGEST)}\n")
    changed = pin_refresh.apply(tmp_path, ["test_rail.py", "longer.txt"], [], [image])
    assert changed == ["test_rail.py"]
    assert (tmp_path / "test_rail.py").read_text() == f'_UBUNTU_INDEX = "{_hex(_NEW_INDEX)}"\n'
    assert (
        tmp_path / "longer.txt"
    ).read_text() == f"{_hex(_INDEX)}0\n{'f' + _hex(_DIGEST)}\n", (
        "a longer run of hex that only holds the digest is another value"
    )


def test_a_rails_bare_index_and_platform_manifest_constants_move_together(tmp_path: Path) -> None:
    """🔴 Red before: the shape the release images' controls keep (``_QEMU_AMD64`` and
    ``_QEMU_INDEX``, ``_BUILDKIT_AMD64`` and ``_BUILDKIT_INDEX``: the platform manifest and the
    index, each as bare hex) would have stayed behind on those images' first refresh."""
    image = pin_refresh.ImageUpdate(
        "tonistiigi/binfmt", "qemu", "linux/amd64", _INDEX, _DIGEST, _NEW_INDEX, _NEW_DIGEST
    )
    rail = f'_QEMU_AMD64 = "{_hex(_DIGEST)}"\n_QEMU_INDEX = "{_hex(_INDEX)}"\n'
    (tmp_path / "test_supply_chain.py").write_text(rail)
    assert pin_refresh.apply(tmp_path, ["test_supply_chain.py"], [], [image]) == [
        "test_supply_chain.py"
    ]
    assert (tmp_path / "test_supply_chain.py").read_text() == (
        f'_QEMU_AMD64 = "{_hex(_NEW_DIGEST)}"\n_QEMU_INDEX = "{_hex(_NEW_INDEX)}"\n'
    )


def test_the_body_names_each_update_its_release_notes_and_its_key() -> None:
    action = pin_refresh.ActionUpdate("o/new", OLD, "v1.0.0", NEWER, "v1.1.0", "https://x/v1.1.0")
    image = pin_refresh.ImageUpdate(
        "python", "3.12-slim", "linux/amd64", _INDEX, _DIGEST, _NEW_INDEX, _NEW_DIGEST
    )
    text = pin_refresh.body([action], [image], ["z.yml:1: gone"], ["w.yml"], "the run")
    assert "v1.0.0 → v1.1.0" in text and "https://x/v1.1.0" in text
    assert "https://hub.docker.com/_/python/tags?name=3.12-slim" in text
    assert "Not resolved, so not moved" in text and "z.yml:1: gone" in text
    assert f"<!-- pin-refresh: {pin_refresh.key([action], [image])} -->" in text


class _Recorder:
    def __init__(self, open_prs: list[dict[str, object]]) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.open_prs = open_prs

    def __call__(self, args, env=None) -> str:
        self.calls.append((list(args), dict(env or {})))
        if args[:3] == ["gh", "pr", "list"]:
            return json.dumps(self.open_prs)
        if args[:2] == ["gh", "api"]:
            return "123\n"
        if args[:3] == ["gh", "pr", "create"]:
            return "https://github.com/o/r/pull/77\n"
        return ""


_TOKEN = "fake-app-token"
_TEXT = "The weekly pin refresh moves these.\n\n<!-- pin-refresh: " + "f" * 64 + " -->\n"


def test_publish_pushes_its_own_branch_with_the_token_off_every_command_line() -> None:
    older = {
        "number": 70,
        "headRefName": "pin-refresh/1",
        "author": {"login": "app/pin-refresh"},
        "body": "old",
    }
    someone = {
        "number": 71,
        "headRefName": "pin-refresh/2",
        "author": {"login": "a-person"},
        "body": "x",
    }
    run = _Recorder([older, someone])
    assert pin_refresh.publish("o/r", "pin-refresh", _TEXT, "555", _TOKEN, run) == 77
    argvs = [args for args, _ in run.calls]
    assert not any(_TOKEN in " ".join(a) for a in argvs), "the token is on a command line"
    push = next((args, env) for args, env in run.calls if args[:2] == ["git", "push"])
    assert push[0] == ["git", "push", "origin", "HEAD:refs/heads/pin-refresh/555"]
    assert push[1]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert "--force" not in " ".join(push[0])
    commit = next((args, env) for args, env in run.calls if args[:2] == ["git", "commit"])
    assert (
        "Signed-off-by: pin-refresh[bot] <123+pin-refresh[bot]@users.noreply.github.com>"
        in commit[0][-1]
    )
    assert commit[1]["GIT_AUTHOR_NAME"] == "pin-refresh[bot]"
    closed = [args for args in argvs if args[:3] == ["gh", "pr", "close"]]
    assert closed == [
        [
            "gh",
            "pr",
            "close",
            "70",
            "-R",
            "o/r",
            "--comment",
            "Superseded by #77, this week's refresh.",
        ]
    ]


def test_publish_leaves_an_open_refresh_with_the_same_updates() -> None:
    same = {
        "number": 70,
        "headRefName": "pin-refresh/1",
        "author": {"login": "app/pin-refresh"},
        "body": _TEXT,
    }
    run = _Recorder([same])
    assert pin_refresh.publish("o/r", "pin-refresh", _TEXT, "556", _TOKEN, run) is None
    assert [args[:2] for args, _ in run.calls] == [["gh", "pr"]], "it pushed or opened something"

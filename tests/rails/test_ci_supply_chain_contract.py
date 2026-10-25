"""The supply-chain rows of the CI contract (docs/maintainers/ci.md, "Supply chain").

Each row holds one rule of tools/ci/supply_chain.py over the whole tree, through the policy's
exceptions in tools/ci/supply-chain.toml, with planted violations that must fail it and plants
that must pass. The table's types, plant helpers and runner are
tests/rails/test_ci_contract.py's, so a row here is held the same way as a row there.
"""

from __future__ import annotations

import datetime
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from test_ci_contract import (
    _BREADTH,
    _MERGE_YML,
    _NGINX,
    _NODE_AMD64,
    _NODE_ARM64,
    _NODE_BASE,
    _NVMRC,
    _PREFERENCE,
    _PYTHON,
    _SETUP_ACTION,
    _UBUNTU,
    REPO,
    Control,
    Row,
    _plant,
    _plants,
    _write,
    run_row,
    table_params,
)

from tools.ci import catalogue, supply_chain

# ── The supply chain (docs/maintainers/ci.md, "Supply chain") ─────────────────────────────────
#
# tools/ci/supply_chain.py holds the rules and tools/ci/supply-chain.toml the policy they read.
# Each row checks the whole tree through the policy's exceptions and keeps its own rules'
# violations, so a plant that also breaks another rule cannot pass for the one under test.

_POLICY = supply_chain.POLICY_FILE
_BACKEND = "deploy/docker/Dockerfile.backend"
_RELEASE_YML = ".github/workflows/release.yml"
#: Where the release builds its artifacts: release.yml calls it as its `artifacts` job.
_ARTIFACTS_YML = ".github/workflows/release-artifacts.yml"
_TOOLING = ".github/workflows/release-image-tooling.yml"
_SETUP_NODE = "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0"
_LOGIN = "docker/login-action@dbcb813823bdd20940b903addbd779551569679f # v4.6.0"
_CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"
_QEMU_AMD64 = "465d3fdd28d0f2b871ba4b4ec98bd183292e96167f00d9fd40bd249f8632d705"
_QEMU_INDEX = "400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0"
_UBUNTU_INDEX = "c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944"
_BUILDKIT_AMD64 = "3eea2ed5d1766c44eb4740853abf56ba85ad40b2c8d4a22e047cbacf15f463c8"
_BUILDKIT_INDEX = "b059f8d7226d0b326bc871af489a5dddf65e36af4d20d14251b072974d9ee87c"
_WEB = "deploy/docker/Dockerfile.web"
_AGENT_CLIS = "deploy/docker/agent-clis"
_AGENT_MANIFEST = f"{_AGENT_CLIS}/package.json"
_AGENT_LOCK = f"{_AGENT_CLIS}/package-lock.json"
_AGENT_INSTALL = "RUN npm ci --omit=dev --no-fund --no-audit &&"
_AGENT_COPY = f"COPY {_AGENT_MANIFEST} {_AGENT_LOCK} ./\n"
_AGENT_SCHEDULE = "    schedule:\n      interval: weekly\n"
#: The image entry's input for everything under deploy/docker/: the Dockerfile and the agent
#: CLIs' lock among it.
_IMAGE_INPUT = '  "deploy/docker/",\n'
_AGENT_PINS: dict[str, str] = json.loads((REPO / _AGENT_MANIFEST).read_text(encoding="utf-8"))[
    "dependencies"
]
#: A CLI the agent CLIs' lock installs, and the version it pins today, so a Dependabot bump of
#: the lock leaves every plant below able to find what it edits.
_CLI = "@openai/codex"
_CLI_PIN = _AGENT_PINS[_CLI]
_CLI_MOVED = ".".join([*_CLI_PIN.split(".")[:2], str(int(_CLI_PIN.split(".")[2]) + 1)])
_LOCKED: dict[str, Any] = json.loads((REPO / _AGENT_LOCK).read_text(encoding="utf-8"))["packages"]
#: A package the lock holds that the manifest does not name, and one nested under another.
_TRANSITIVE = next(
    k
    for k in sorted(_LOCKED)
    if k.count("node_modules/") == 1 and k.removeprefix("node_modules/") not in _AGENT_PINS
)
_NESTED = next(k for k in sorted(_LOCKED) if k.count("node_modules/") > 1)
_NODE_INDEX = _NODE_BASE.rsplit(":", 1)[1]
_NODE_TAG = _NODE_BASE.split("@", 1)[0]
_PINNED_NODE_BASE = (
    f"# {_NODE_TAG}, as its multi-arch index. Platform manifests:\n"
    f"#   linux/amd64 sha256:{_NODE_AMD64}\n"
    + _NODE_ARM64
    + f"FROM mirror.gcr.io/library/{_NODE_BASE}\n"
)
#: The web image's SPA stage as it starts today: on the build platform, pinned by digest.
_SPA_FROM = f"FROM --platform=$BUILDPLATFORM mirror.gcr.io/library/{_NODE_BASE}"


def _locked(name: str) -> str:
    """The version uv.lock holds for *name*."""
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    versions = [p["version"] for p in lock["package"] if p["name"] == name]
    assert len(versions) == 1, f"uv.lock holds {name} {versions}"
    return str(versions[0])


_LOCKED_PACKAGING = _locked("packaging")


def _days(n: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=n)).isoformat()


def _copy_supply_chain_files(root: Path) -> None:
    for rel, text in supply_chain.read_tree(REPO).items():
        _write(root, rel, text)


def _supply_row(name: str, rules: tuple[str, ...], controls: tuple[Control, ...]) -> Row:
    """A row over *rules*, or over every rule and the exceptions when *rules* is empty."""

    def check(root: Path) -> list[str]:
        found = supply_chain.check(supply_chain.read_tree(root), rules=rules)
        return [str(v) for v in found if not rules or v.rule in rules]

    return Row(name=name, check=check, scaffold=_copy_supply_chain_files, controls=controls)


def _together(name: str, *controls: Control, fails: bool = True, expect: str = "") -> Control:
    def plant(root: Path) -> None:
        for control in controls:
            control.plant(root)

    return Control(name, plant, fails, expect)


def _line(name: str, rel: str, start: str, new: str = "", **kw: Any) -> Control:
    """Replace (or, with *new* empty, remove) the first line of *rel* that begins with *start*."""

    def plant(root: Path) -> None:
        lines = (root / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        index = next((i for i, line in enumerate(lines) if line.startswith(start)), None)
        assert index is not None, f"the plant {name!r} found no line {start!r} in {rel}"
        lines[index] = new + "\n" if new else ""
        _write(root, rel, "".join(lines))

    return Control(name, plant, **kw)


def _append(name: str, rel: str, text: str, **kw: Any) -> Control:
    def plant(root: Path) -> None:
        path = root / rel
        _write(root, rel, (path.read_text(encoding="utf-8") if path.exists() else "") + text)

    return Control(name, plant, **kw)


def _planted_workflow(
    name: str, steps: str, top: str = "permissions: {}\n", job: str = "", **kw: Any
) -> Control:
    text = (
        f"name: planted\non: workflow_dispatch\n{top}jobs:\n  it:\n    runs-on: ubuntu-24.04\n"
        f"{job}    steps:\n{steps}"
    )
    return _append(name, ".github/workflows/planted.yml", text, **kw)


def _run_step(command: str, **kw: Any) -> Control:
    return _planted_workflow(f"`{command}`", f"      - run: {command}\n", **kw)


def _run_block(script: str, **kw: Any) -> Control:
    """A planted step whose ``run:`` is the block *script*, a heredoc's lines and all."""
    body = "".join(f"          {line}\n" for line in script.splitlines())
    return _planted_workflow(f"`{script}`", f"      - run: |\n{body}", **kw)


def _copy(name: str, source: str, rel: str) -> Control:
    """*rel* written as a copy of the tree's *source*."""

    def plant(root: Path) -> None:
        _write(root, rel, (root / source).read_text(encoding="utf-8"))

    return Control(name, plant)


def _chain(n: int) -> tuple[Control, ...]:
    """Scripts ``tools/ci/c1.sh`` … ``c<n>.sh``, each running the next as ``sh <path>``."""
    return tuple(
        _append(f"script {i}", f"tools/ci/c{i}.sh", f"sh tools/ci/c{i + 1}.sh\n" if i < n else "")
        for i in range(1, n + 1)
    )


#: breadth.yml's install smokes, and the staged one's step as a violation names it.
_SMOKE_CURRENT = "tools/ci/install_smoke_current.sh"
_SMOKE_STAGED = supply_chain.STAGED_INSTALLER_SCRIPT
_SMOKE_SERVED = supply_chain.OWN_INSTALLER_SCRIPT
_STAGED_STEP = " ".join(supply_chain.STAGED_INSTALLER_STEP[1:])


def _planted_action(name: str, steps: str, **kw: Any) -> Control:
    """A local composite action, called by nothing: its steps run in whichever job calls it."""
    text = "name: planted\ndescription: planted\nruns:\n  using: composite\n  steps:\n" + steps
    return _append(name, ".github/actions/planted/action.yml", text, **kw)


def _exception(reference: str, file: str = ".github/workflows/planted.yml") -> str:
    """A well-formed run-install exception for *reference*, expiring in 30 days."""
    return (
        f'\n[[exception]]\nfile = "{file}"\nscope = "run-install"\nreference = "{reference}"\n'
        f'owner = "maintainers"\nreason = "planted"\nexpires = {_days(30)}\n'
    )


_WAIVED_STEP = "      - name: Install tools\n        run: pip install ruff\n"
_WAIVED_REF = "it Install tools: install of ruff names no exact version"
#: Each field of the planted exception, as its line reads.
_WAIVED_FIELDS = {
    "file": 'file = ".github/workflows/planted.yml"\n',
    "reference": f'reference = "{_WAIVED_REF}"\n',
    "scope": 'scope = "run-install"\n',
    "owner": 'owner = "maintainers"\n',
    "reason": 'reason = "planted"\n',
    "expires": f"expires = {_days(30)}\n",
}


def _waived(name: str, old: str = "", new: str = "", **kw: Any) -> Control:
    """A planted unpinned install and the exception that waives it, with *old* in the
    exception's text edited to *new*. The policy itself holds no exception to edit."""
    text = _exception(_WAIVED_REF)
    if old:
        assert old in text, f"the plant {name!r} found no {old!r} in the planted exception"
        text = text.replace(old, new, 1)
    return _together(name, _planted_workflow("", _WAIVED_STEP), _append("", _POLICY, text), **kw)


def _json_set(name: str, rel: str, keys: tuple[str, ...], value: object, **kw: Any) -> Control:
    """Set (or, with *value* None, delete) one key of *rel* read as JSON, making the tables on
    the way, so the plant edits today's lock by its structure rather than by its text."""

    def plant(root: Path) -> None:
        data = json.loads((root / rel).read_text(encoding="utf-8"))
        node = data
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        if value is None:
            assert keys[-1] in node, f"the plant {name!r} found no {keys} in {rel}"
            del node[keys[-1]]
        else:
            node[keys[-1]] = value
        _write(root, rel, json.dumps(data, indent=2) + "\n")

    return Control(name, plant, **kw)


_LISTED_CHECKOUT = (
    '\n[[audited_write]]\nworkflow = "merge.yml"\njob = "changes"\n'
    'permission = "persist-credentials"\nsteps = ["actions/checkout"]\nreason = "planted"\n'
)

SUPPLY_CHAIN_ROWS: tuple[Row, ...] = (
    _supply_row(
        "action-sha",
        ("action-sha", "image-digest"),
        (
            _plant(
                "an action by tag",
                ".github/workflows/dco.yml",
                "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3 # v9.0.0",
                "actions/github-script@v9",
            ),
            # The tier workflows (#3915) are read as every workflow is.
            _plant(
                "an action by tag in pr.yml",
                ".github/workflows/pr.yml",
                "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2",
                "actions/upload-artifact@v7",
                expect="actions/upload-artifact@v7 is not pinned by commit SHA",
            ),
            _plant(
                "an action by branch in merge.yml",
                ".github/workflows/merge.yml",
                "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2",
                "actions/download-artifact@main",
                expect="actions/download-artifact@main is not pinned by commit SHA",
            ),
            _plant(
                "an action by branch",
                _RELEASE_YML,
                "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2",
                "pypa/gh-action-pypi-publish@release/v1",
            ),
            _plant("a SHA with no release comment", ".github/workflows/nix.yml", " # v31.11.2", ""),
            _plant(
                "a SHA whose comment is a bare major",
                ".github/workflows/dco.yml",
                "# v9.0.0",
                "# v9",
            ),
            _planted_workflow("a docker:// action by tag", "      - uses: docker://alpine:3.20\n"),
            _planted_workflow(
                "a docker:// action by digest that names no platform",
                f"      - uses: docker://alpine:3.20@sha256:{'a' * 64}\n",
            ),
            _planted_workflow(
                "a docker:// action pinned to its linux/amd64 manifest passes",
                f"      # alpine:3.20, the linux/amd64 manifest of index sha256:{'b' * 64}.\n"
                f"      - uses: docker://alpine:3.20@sha256:{'a' * 64}\n",
                fails=False,
            ),
            _planted_action(
                "an action by tag in a local composite action",
                "    - uses: actions/setup-node@v4\n",
                expect="actions/setup-node@v4 is not pinned by commit SHA",
            ),
            _planted_workflow(
                "a local action the tree does not hold",
                "      - uses: ./.github/actions/missing\n",
                expect="names a local action or workflow the check cannot read",
            ),
            _together(
                "a local composite action pinned throughout passes",
                _planted_action("", f"    - uses: {_SETUP_NODE}\n"),
                _planted_workflow("", "      - uses: ./.github/actions/planted\n"),
                fails=False,
            ),
            *(
                _planted_workflow(
                    f"a `uses:` the line reader cannot read: {name}",
                    steps,
                    expect="is not written as `uses: owner/repo@<sha> # vX.Y.Z` on one line",
                )
                for name, steps in (
                    ("a flow mapping", "      - {uses: actions/checkout@v4}\n"),
                    ("a quoted key", '      - "uses": actions/checkout@v4\n'),
                    ("a space before the colon", "      - uses : actions/checkout@v4\n"),
                    (
                        "the value on the next line",
                        "      - uses:\n          actions/checkout@v4\n",
                    ),
                )
            ),
            _planted_workflow(
                "a quoted SHA pin with its release comment passes",
                '      - uses: "' + _SETUP_NODE.replace(" # ", '" # ') + "\n",
                fails=False,
            ),
            _append(
                "an action by tag in a .yaml workflow",
                ".github/workflows/planted.yaml",
                "name: planted\non: workflow_dispatch\npermissions: {}\njobs:\n  it:\n"
                "    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/checkout@v4\n",
                expect="actions/checkout@v4 is not pinned by commit SHA",
            ),
        ),
    ),
    _supply_row(
        "image-digest",
        ("image-digest",),
        (
            _plant("a workflow container by tag alone", _BREADTH, _UBUNTU, "ubuntu:26.04"),
            _plant(
                "the moving tag latest",
                _BREADTH,
                "container: ubuntu:26.04@",
                "container: ubuntu:latest@",
            ),
            _plant(
                "an image base by tag alone",
                "deploy/docker/Dockerfile.web",
                _NGINX,
                "nginx:1.27-alpine",
            ),
            # The SPA stage builds on the build platform; the flag changes nothing the pin rule
            # reads, so each base below is judged by the image after it.
            _plant(
                "a build-platform base by the moving tag latest",
                _WEB,
                _SPA_FROM,
                "FROM --platform=$BUILDPLATFORM node:latest",
                expect="node:latest is not a tag pinned by digest",
            ),
            _plant(
                "a build-platform base by tag alone",
                _WEB,
                _SPA_FROM,
                "FROM --platform=$BUILDPLATFORM mirror.gcr.io/library/" + _NODE_TAG,
                expect=f"{_NODE_TAG} is not a tag pinned by digest",
            ),
            _append(
                "a build-platform base pinned by digest with no comment above it",
                "tools/sandbox/Dockerfile",
                _SPA_FROM + "\n",
                expect="has no comment above it",
            ),
            _append(
                "a build-platform base pinned by digest under its comment passes",
                "tools/sandbox/Dockerfile",
                _PINNED_NODE_BASE.replace("FROM ", "FROM --platform=$BUILDPLATFORM "),
                fails=False,
            ),
            _plant(
                "a two-platform base without its arm64 manifest",
                "deploy/docker/Dockerfile.web",
                _NODE_ARM64,
                "",
            ),
            _plant(
                "a workflow container without its platform",
                ".github/workflows/clean-machine-walkthroughs.yml",
                "the linux/amd64 manifest",
                "the manifest",
            ),
            _plant(
                "a workflow container pinned to the index its comment names",
                _BREADTH,
                _UBUNTU,
                "ubuntu:26.04@sha256:" + _UBUNTU_INDEX,
                expect="pins the index",
            ),
            _plant(
                "a workflow container whose comment names no index",
                ".github/workflows/clean-machine-walkthroughs.yml",
                "of index",
                "of",
                expect="does not name the index",
            ),
            _plant(
                "the QEMU image pinned to its index",
                _ARTIFACTS_YML,
                _QEMU_AMD64,
                _QEMU_INDEX,
                expect="pins the index",
            ),
            *(
                _append(
                    name,
                    "tools/sandbox/Dockerfile",
                    text,
                    expect=expect,
                    fails=bool(expect),
                )
                for name, text, expect in (
                    (
                        "a base ARG by tag alone",
                        "ARG BASE=alpine:3.20\nFROM ${BASE}\n",
                        "alpine:3.20 is not a tag pinned by digest",
                    ),
                    ("a base ARG with no default", "ARG BASE\nFROM ${BASE}\n", "no default"),
                    (
                        "a bare-tag global ARG beside a pinned one redeclared in a stage",
                        "ARG BASE=alpine:3.20\nFROM scratch AS one\n"
                        + _PINNED_NODE_BASE.replace("FROM ", "ARG BASE=")
                        + "FROM ${BASE}\n",
                        "alpine:3.20 is not a tag pinned by digest",
                    ),
                    (
                        "a pinned global ARG with a stage that redeclares it passes",
                        _PINNED_NODE_BASE.replace("FROM ", "ARG BASE=")
                        + "FROM ${BASE} AS one\nARG BASE\nFROM ${BASE}\n",
                        "",
                    ),
                )
            ),
            # A workflow image the line reader once skipped: a trailing comment, an expression,
            # a flow mapping, a service.
            _plant(
                "a workflow container by tag with a trailing comment",
                _BREADTH,
                "container: " + _UBUNTU,
                "container: ubuntu:latest  # temp",
                expect="ubuntu:latest is not a tag pinned by digest",
            ),
            _plant(
                "a workflow container from a matrix expression",
                _BREADTH,
                "container: " + _UBUNTU,
                "container: ${{ matrix.image }}",
                expect="is an expression",
            ),
            _plant(
                "a workflow container as a flow mapping",
                _BREADTH,
                "container: " + _UBUNTU,
                "container: {image: ubuntu:latest}",
                expect="is not a tag pinned by digest",
            ),
            _planted_workflow(
                "a service image by tag with a trailing comment",
                "      - run: true\n",
                job="    services:\n      cache:\n        image: redis:7  # cache\n",
                expect="redis:7 is not a tag pinned by digest",
            ),
            # BuildKit is judged like QEMU.
            _plant(
                "the BuildKit image pinned to its index",
                _ARTIFACTS_YML,
                _BUILDKIT_AMD64,
                _BUILDKIT_INDEX,
                expect="pins the index",
            ),
            _plant(
                "the BuildKit image with no comment naming its index",
                _ARTIFACTS_YML,
                "          # sha256:" + _BUILDKIT_INDEX + ".\n",
                "",
                expect="does not name the index",
            ),
            # A Dockerfile comment's digests, read per platform and tied to the tag.
            _plant(
                "a base whose amd64 line lost its digest",
                _WEB,
                "#   linux/amd64 sha256:" + _NODE_AMD64 + "\n",
                "#   linux/amd64\n",
                expect="does not say its linux/amd64 digest",
            ),
            _plant(
                "a base whose platform lines list the index it pins",
                _WEB,
                _NODE_AMD64,
                _NODE_INDEX,
                expect="lists the index it pins",
            ),
            _plant(
                "a base whose tag moved without its comment",
                _WEB,
                "nginx:1.27-alpine@sha256:",
                "nginx:1.28-alpine@sha256:",
                expect="names nginx:1.28-alpine",
            ),
            # Images a run: step or a Dockerfile fetches outside FROM.
            _run_step("docker run --rm ubuntu:latest true", expect="ubuntu:latest is not"),
            _run_step("docker pull alpine:latest", expect="alpine:latest is not"),
            _plant(
                "the smoke's inline base by tag",
                _TOOLING,
                "FROM mirror.gcr.io/library/" + _NODE_BASE,
                "FROM node:latest",
                expect="node:latest is not a tag pinned by digest",
            ),
            _run_step(
                "docker run --rm ghcr.io/personalclaw/personalclaw-gateway:latest true",
                fails=False,
            ),
            _run_step(
                "docker build --tag local:ci . && docker run --rm local:ci true", fails=False
            ),
            *(
                _append(
                    name,
                    "tools/sandbox/Dockerfile",
                    _PINNED_NODE_BASE + line,
                    expect=expect,
                    fails=bool(expect),
                )
                for name, line, expect in (
                    (
                        "a COPY --from of an image by tag",
                        "COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/\n",
                        "uv:latest is not a tag pinned by digest",
                    ),
                    (
                        "a RUN --mount from an image by tag",
                        "RUN --mount=type=bind,from=alpine:3.20,source=/,target=/x true\n",
                        "alpine:3.20 is not a tag pinned by digest",
                    ),
                    ("a COPY --from of a stage passes", "COPY --from=0 /x /x\n", ""),
                )
            ),
            # A build that overrides a base ARG from a workflow.
            _together(
                "a --build-arg that overrides a base ARG",
                _append(
                    "",
                    "tools/sandbox/Dockerfile",
                    _PINNED_NODE_BASE.replace("FROM ", "ARG BASE=") + "FROM ${BASE}\n",
                ),
                _run_step(
                    "docker build --build-arg BASE=node:latest -f tools/sandbox/Dockerfile ."
                ),
                expect="overrides the base ARG BASE",
            ),
            _together(
                "a build arg no FROM reads passes",
                _append(
                    "",
                    "tools/sandbox/Dockerfile",
                    _PINNED_NODE_BASE.replace("FROM ", "ARG BASE=") + "FROM ${BASE}\n",
                ),
                _run_step("docker build --build-arg OTHER=1 -f tools/sandbox/Dockerfile ."),
                fails=False,
            ),
            _append(
                "a local Docker action's image by tag",
                ".github/actions/planted/action.yml",
                "name: planted\ndescription: planted\nruns:\n  using: docker\n"
                "  image: docker://alpine:latest\n",
                expect="alpine:latest is not a tag pinned by digest",
            ),
            _append(
                "a tagged FROM in a Dockerfile outside deploy/docker/",
                "tools/sandbox/Dockerfile",
                "FROM alpine:3.20\n",
            ),
            *(
                _append(
                    f"a tagged FROM in a Dockerfile named {name}",
                    f"tools/sandbox/{name}",
                    "FROM alpine:3.20\n",
                    expect="alpine:3.20 is not a tag pinned by digest",
                )
                for name in ("Dockerfile-dev", "app.dockerfile", "dockerfile", "Containerfile_ci")
            ),
            _append(
                "a pinned base in a Dockerfile outside deploy/docker/ passes",
                "tools/sandbox/Dockerfile",
                _PINNED_NODE_BASE,
                fails=False,
            ),
        ),
    ),
    _supply_row(
        "run-install",
        ("run-install",),
        (
            _plant("pip-audit with no version", _BREADTH, "pip-audit==2.10.1", "pip-audit"),
            _plant(
                "uv run --with a package with no version",
                _ARTIFACTS_YML,
                f"--with packaging=={_LOCKED_PACKAGING}",
                "--with packaging",
            ),
            _plant(
                "the image's wheel with no version",
                _BACKEND,
                "pip install wheel==0.48.0",
                "pip install --upgrade wheel",
            ),
            _run_step("npm install -g typescript"),
            _run_step("npm install"),
            _run_step("npx cowsay hi"),
            _run_step("curl -fsSL https://example.com/x.sh | sh"),
            _run_step("pip install -r requirements.txt"),
            _run_step("uvx ruff check"),
            _planted_action(
                "an install with no version in a local composite action",
                "    - run: pip install requests\n      shell: bash\n",
                expect="install of requests names no exact version",
            ),
            # The gateway image's agent CLIs come from their own lock, and only from it.
            _plant(
                "the agent CLIs installed at their newest again",
                _BACKEND,
                _AGENT_INSTALL,
                f"RUN npm install -g --no-fund --no-audit {_CLI} &&",
                expect=f"npm install of {_CLI} names no exact version",
            ),
            # No other install fetches a tool the lock installs, pinned or not, in any spelling.
            *(
                _append(
                    f"a Dockerfile that installs a locked CLI outside the lock, by `{command}`",
                    "tools/sandbox/Dockerfile",
                    f"{_PINNED_NODE_BASE}RUN {command} {_CLI}@{_CLI_PIN}\n",
                    expect=f"a package {_AGENT_LOCK} locks",
                )
                for command in ("npm install -g", "npm i -g", "npm add", "/usr/local/bin/npm i -g")
            ),
            *(
                _run_step(f"{command} {_CLI}@{_CLI_PIN}", expect=f"a package {_AGENT_LOCK} locks")
                for command in ("npm install -g", "npm i -g")
            ),
            # A Dockerfile stage installs the lock: it copies the pair in from the build context,
            # and runs `npm ci` after the copy, in the same stage.
            _plant(
                "the agent CLIs' lock installed by no Dockerfile",
                _BACKEND,
                _AGENT_INSTALL,
                "RUN true &&",
                expect=f"{_AGENT_LOCK} is installed by no Dockerfile stage",
            ),
            _plant(
                "the agent CLIs' lock copied in one stage and installed in the next",
                _BACKEND,
                _AGENT_INSTALL,
                f"FROM base AS planted\n{_AGENT_INSTALL}",
                expect=f"{_AGENT_LOCK} is installed by no Dockerfile stage",
            ),
            _plants(
                "the agent CLIs' npm ci run before their lock is copied in",
                _BACKEND,
                (
                    (_AGENT_COPY, f"RUN npm ci --omit=dev\n{_AGENT_COPY}"),
                    (_AGENT_INSTALL, "RUN true &&"),
                ),
                expect=f"{_AGENT_LOCK} is installed by no Dockerfile stage",
            ),
            _plant(
                "the agent CLIs' folder copied in whole, then npm ci, passes",
                _BACKEND,
                _AGENT_COPY,
                f"COPY {_AGENT_CLIS}/ ./\n",
                fails=False,
            ),
            _plant(
                "the agent CLIs' lock copied from another stage, not the build context",
                _BACKEND,
                _AGENT_COPY,
                f"COPY --from=web {_AGENT_COPY.removeprefix('COPY ')}",
                expect=f"{_AGENT_LOCK} is installed by no Dockerfile stage",
            ),
            # npx in a stage runs only what that stage's own `npm ci` installed from a lock it
            # copied in: the agent CLIs' lock is not the root one.
            _plant(
                "npx of a root-lock tool in the stage that installs only the agent CLIs",
                _BACKEND,
                "RUN groupadd --gid 10001",
                "RUN npx vite --version\n\nRUN groupadd --gid 10001",
                expect="npx vite runs a package that is neither pinned nor installed",
            ),
            _plant(
                "npx of an agent CLI after the stage installs their lock passes",
                _BACKEND,
                "RUN groupadd --gid 10001",
                "RUN npx codex --version\n\nRUN groupadd --gid 10001",
                fails=False,
            ),
            # The manifest pins each tool exactly, under `dependencies` only.
            *(
                _json_set(
                    f"an agent CLI pinned by the range {spec!r}",
                    _AGENT_MANIFEST,
                    ("dependencies", _CLI),
                    spec,
                    expect="not an exact X.Y.Z",
                )
                for spec in (f"^{_CLI_PIN}", f"{_CLI_PIN} - {_CLI_MOVED}")
            ),
            *(
                _json_set(
                    f"an image manifest that declares {field}",
                    _AGENT_MANIFEST,
                    (field, "left-pad"),
                    "^1.3.0",
                    expect=f"declares {field}",
                )
                for field in ("optionalDependencies", "peerDependencies", "devDependencies")
            ),
            _plant(
                "an image manifest that is not JSON",
                _AGENT_MANIFEST,
                "{",
                "",
                expect="does not parse as a JSON object",
            ),
            _append(
                "an image manifest that is a JSON list",
                "deploy/docker/planted/package.json",
                "[]\n",
                expect="does not parse as a JSON object",
            ),
            # The lock was generated from the manifest: the same names at the same versions in
            # the manifest, the lock's root and its node_modules entries.
            _json_set(
                "an agent CLI's pin moved without the lock",
                _AGENT_MANIFEST,
                ("dependencies", _CLI),
                _CLI_MOVED,
                expect="was not generated from its package.json",
            ),
            _json_set(
                "the lock's root pinning a CLI by a range",
                _AGENT_LOCK,
                ("packages", "", "dependencies", _CLI),
                f"^{_CLI_PIN}",
                expect="was not generated from its package.json",
            ),
            _json_set(
                "the lock's root naming a package the manifest does not",
                _AGENT_LOCK,
                ("packages", "", "dependencies", "left-pad"),
                "1.3.0",
                expect="was not generated from its package.json",
            ),
            _json_set(
                "the lock's root declaring an optional dependency",
                _AGENT_LOCK,
                ("packages", "", "optionalDependencies", "left-pad"),
                "1.3.0",
                expect="was not generated from its package.json",
            ),
            _json_set(
                "the lock installing another version of a CLI than it pins",
                _AGENT_LOCK,
                ("packages", f"node_modules/{_CLI}", "version"),
                _CLI_MOVED,
                expect="was not generated from its package.json",
            ),
            # Every package it locks, a direct one or one deep in the tree, comes from the npm
            # registry under a sha512 integrity.
            _json_set(
                "a locked CLI with a sha1 integrity",
                _AGENT_LOCK,
                ("packages", f"node_modules/{_CLI}", "integrity"),
                "sha1-" + _LOCKED[f"node_modules/{_CLI}"]["integrity"].removeprefix("sha512-"),
                expect=f"{_AGENT_LOCK}: node_modules/{_CLI} is locked without a registry URL",
            ),
            _json_set(
                "a transitive locked package with no integrity",
                _AGENT_LOCK,
                ("packages", _TRANSITIVE, "integrity"),
                None,
                expect=f"{_AGENT_LOCK}: {_TRANSITIVE} is locked without a registry URL",
            ),
            _json_set(
                "a nested locked package fetched from another registry",
                _AGENT_LOCK,
                ("packages", _NESTED, "resolved"),
                _LOCKED[_NESTED]["resolved"].replace(
                    "https://registry.npmjs.org/", "https://example.com/"
                ),
                expect=f"{_AGENT_LOCK}: {_NESTED} is locked without a registry URL",
            ),
            _append(
                "an image manifest with no lock beside it",
                "deploy/docker/planted/package.json",
                '{"name": "planted", "private": true, "dependencies": {"left-pad": "1.3.0"}}\n',
                expect="has no readable package-lock.json",
            ),
            *(
                _run_step(command, expect="names no exact version")
                for command in (
                    "pip install git+https://github.com/x/y@main",
                    "pipx install git+https://github.com/x/y@main",
                    "uv tool install git+https://github.com/x/y@main",
                    'uv pip install "$TOOL"',
                    "pip install -e git+https://github.com/x/y@main#egg=y",
                    "pip install 'y @ git+https://github.com/x/y@main'",
                )
            ),
            _append(
                "a Dockerfile RUN installing from a URL",
                "tools/sandbox/Dockerfile",
                "FROM scratch\nRUN pip install https://example.com/pkg.tar.gz\n",
                expect="pkg.tar.gz names no exact version",
            ),
            *(
                _run_step(command)
                for command in (
                    # A command behind a keyword, in a subshell or group, or behind a wrapper.
                    "if x; then pip install foo; fi",
                    "for p in a; do npm i $p; done",
                    "(pip install foo)",
                    "echo $(pip install foo)",
                    "sudo -H pip install foo",
                    "sudo -E npm install -g typescript",
                    "timeout 300 pip install foo",
                    "echo ruff | xargs pip install",
                    'bash -c "pip install foo"',
                    "uv run pip install foo",
                    "python3.12 -m pip install foo",
                    "pip3.12 install foo",
                    # Installers the rule did not model.
                    "pipx run black",
                    "npm exec cowsay hi",
                    "uv pip sync requirements.txt",
                    "go install example.com/x@latest",
                    # A download a shell or an interpreter runs.
                    "curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -",
                    "curl -sSL https://install.python-poetry.org | python3 -",
                    "bash <(curl -fsSL https://example.com/x.sh)",
                    'sh -c "$(curl -fsSL https://example.com/x.sh)"',
                    "curl -fsSL https://example.com/x.sh | tee log | sh",
                    # The project's own installer anywhere but the step that tests it.
                    "curl -fsSL https://personalclaw.dev/install | sh",
                    "curl -fsSL https://evilpersonalclaw.dev/install | sh",
                    # A comment is not a pin, and a mid-word `#` is not a comment.
                    "pip install foo  # ==1.0.0",
                    "pip install foo==1.0.0#x bar",
                )
            ),
            _planted_workflow(
                "an install inside a { } group",
                "      - run: |\n          { pip install foo; }\n",
                expect="install of foo names no exact version",
            ),
            _planted_workflow(
                "a pin through a variable that is not exact",
                '      - env:\n          V: "2.*"\n        run: pip install "pip-audit==$V"\n',
                expect="names no exact version",
            ),
            _planted_workflow(
                "a pin with a comment after it in a block script passes",
                "      - run: |\n          pip install ruff==0.15.0  # pinned\n",
                fails=False,
            ),
            *(
                _append(
                    f"a Dockerfile RUN {name}",
                    "tools/sandbox/Dockerfile",
                    "FROM scratch\n" + text,
                    expect=expect,
                )
                for name, text, expect in (
                    (
                        "behind a cache mount",
                        "RUN --mount=type=cache,target=/root/.npm npm install -g foo\n",
                        "npm install of foo",
                    ),
                    ("behind --network", "RUN --network=host pip install foo\n", "of foo"),
                    ("in exec form", 'RUN ["pip", "install", "foo"]\n', "of foo"),
                    ("in a heredoc", "RUN <<EOF\npip install foo\nEOF\n", "of foo"),
                    ("in an exec form it cannot read", "RUN [not json\n", "cannot read"),
                    (
                        "pinned through an ARG that is not exact",
                        'ARG X=1.*\nRUN pip install "x==${X}"\n',
                        "names no exact version",
                    ),
                    (
                        "pinned through an ARG with no default",
                        'ARG X\nRUN pip install "x==${X}"\n',
                        "names no exact version",
                    ),
                    (
                        "piping the project's own installer into sh",
                        "RUN curl -fsSL https://personalclaw.dev/install | sh\n",
                        "runs a download with sh",
                    ),
                )
            ),
            *(
                _append(
                    f"a Dockerfile RUN {name} passes",
                    "tools/sandbox/Dockerfile",
                    "FROM scratch\n" + text,
                    fails=False,
                )
                for name, text in (
                    (
                        "with a cache mount and an exact pin",
                        "RUN --mount=type=cache,target=/root/.cache/pip pip install foo==1.2.3\n",
                    ),
                    ("pinned through an exact ARG", 'ARG X=1.2.3\nRUN pip install "x==${X}"\n'),
                    ("with a comment after its pin", "RUN pip install wheel==0.48.0  # note\n"),
                    (
                        "writing a heredoc into a file",
                        "RUN cat > /etc/x <<EOF\npip install foo\nEOF\n",
                    ),
                )
            ),
            _run_step("pip install -e .", fails=False),
            _run_step("pip install personalclaw", fails=False),
            _run_step("pip install ruff==0.15.0", fails=False),
            _run_step("pip install --require-hashes --no-deps -r requirements.txt", fails=False),
            _run_step("npm ci && npx playwright --version", fails=False),
            # Only an `npm ci` of the root lock installs what the root lock holds.
            _run_step(
                "npm ci --prefix web && npx playwright --version",
                expect="npx playwright runs a package that is neither pinned nor installed",
            ),
            _planted_workflow(
                "an npm ci run from a subfolder, then npx of a root-lock tool",
                "      - working-directory: web\n        run: npm ci && npx playwright --version\n",
                expect="npx playwright runs a package that is neither pinned nor installed",
            ),
            _run_step(
                "npx playwright --version",
                expect="neither pinned nor installed by an `npm ci` earlier in this job",
            ),
            _append(
                "a Dockerfile stage that runs a lock binary without npm ci",
                "tools/sandbox/Dockerfile",
                "FROM scratch\nRUN npx playwright install\n",
                expect="npx playwright runs a package",
            ),
            _append(
                "a Dockerfile stage whose npm ci installs no lock it copied in",
                "tools/sandbox/Dockerfile",
                "FROM scratch\nRUN npm ci && npx playwright --version\n",
                expect="npx playwright runs a package",
            ),
            _append(
                "a Dockerfile stage that copies the root lock in, then runs npm ci, passes",
                "tools/sandbox/Dockerfile",
                "FROM scratch\nCOPY package.json package-lock.json ./\n"
                "RUN npm ci && npx playwright --version\n",
                fails=False,
            ),
            _run_step("uv pip install --no-deps dist/*.whl", fails=False),
            _run_step("npm install -g typescript@5.9.3", fails=False),
            _run_step("sudo apt-get install -y tmux", fails=False),
        ),
    ),
    # A repository script a step runs is read as `sh <path>`, the one way the check reads one,
    # and every other way of running one is a violation, never a skip (docs/maintainers/ci.md,
    # "Supply chain"). Its own row, beside run-install's, so neither runs near the test timeout.
    _supply_row(
        "run-install-scripts",
        ("run-install",),
        (
            # Read: the script is judged under the step's rules, however the step reaches it.
            *(
                _together(
                    f"an install in a script a step runs as `{how}`",
                    _append("the script", "tools/ci/planted.sh", "pip install foo\n"),
                    _run_step(how),
                    expect="tools/ci/planted.sh: install of foo names no exact version",
                )
                for how in (
                    "sh tools/ci/planted.sh",
                    "uv run sh tools/ci/planted.sh",
                    # uv run's options with a value: neither value is read as the command.
                    "uv run --python 3.12 sh tools/ci/planted.sh",
                    "uv run --extra dev --with foo==1.0 sh tools/ci/planted.sh",
                    "sudo -E sh tools/ci/planted.sh",
                    "sh .//tools/ci/planted.sh",
                    'sh "$GITHUB_WORKSPACE/tools/ci/planted.sh"',
                    "bash -ec 'sh tools/ci/planted.sh'",
                )
            ),
            _together(
                "an install in a `.bash` script a step runs",
                _append("the script", "tools/ci/planted.bash", "pip install foo\n"),
                _run_step("sh tools/ci/planted.bash"),
                expect="tools/ci/planted.bash: install of foo names no exact version",
            ),
            _run_block(
                "bash -o pipefail <<'EOF'\npip install foo\nEOF",
                expect="install of foo names no exact version",
            ),
            _together(
                "an install in a script a shell's heredoc runs",
                _append("the script", "tools/ci/planted.sh", "pip install foo\n"),
                _run_block("sh <<'EOF'\nsh tools/ci/planted.sh\nEOF"),
                expect="tools/ci/planted.sh: install of foo names no exact version",
            ),
            # Refused: every other way of running a repository script, the script clean.
            *(
                _together(
                    f"a script a step runs as `{how}`",
                    _append("the script", "tools/ci/planted.sh", "echo planted\n"),
                    _run_step(how),
                    expect=expect,
                )
                for how, expect in (
                    ("bash tools/ci/planted.sh", "runs tools/ci/planted.sh with `bash`, not as"),
                    ("bash -e tools/ci/planted.sh", "runs tools/ci/planted.sh with `bash -e`"),
                    ("sh -e tools/ci/planted.sh", "runs tools/ci/planted.sh with `sh -e`"),
                    ("dash tools/ci/planted.sh", "runs tools/ci/planted.sh with `dash`"),
                    ("uv run bash tools/ci/planted.sh", "runs tools/ci/planted.sh with `bash`"),
                    ("sudo -E bash tools/ci/planted.sh", "runs tools/ci/planted.sh with `bash`"),
                    ("bash .//tools/ci/planted.sh", "runs tools/ci/planted.sh with `bash`"),
                    (
                        "bash -o pipefail tools/ci/planted.sh",
                        "runs tools/ci/planted.sh with `bash -o pipefail`",
                    ),
                    (". tools/ci/planted.sh", "sources tools/ci/planted.sh with `.`, not as"),
                    ("source tools/ci/planted.sh", "sources tools/ci/planted.sh with `source`"),
                    ("./tools/ci/planted.sh", "runs tools/ci/planted.sh as a program, not as"),
                    ("tools/ci/planted.sh", "runs tools/ci/planted.sh as a program"),
                    (
                        '"$GITHUB_WORKSPACE/tools/ci/planted.sh"',
                        "runs tools/ci/planted.sh as a program",
                    ),
                    ("bash -c './tools/ci/planted.sh'", "runs tools/ci/planted.sh as a program"),
                    ("cat tools/ci/planted.sh | bash", "runs what a pipe feeds bash"),
                    ("bash < tools/ci/planted.sh", "feeds tools/ci/planted.sh to bash on stdin"),
                    ("bash -s < tools/ci/planted.sh", "feeds tools/ci/planted.sh to bash on stdin"),
                    (
                        "python3 -c \"import subprocess; subprocess.run(['sh',"
                        " 'tools/ci/planted.sh'])\"",
                        "names tools/ci/planted.sh in python3's -c code",
                    ),
                    (
                        'cp tools/ci/planted.sh "$RUNNER_TEMP/x.sh"',
                        "names tools/ci/planted.sh in a `cp` command the check does not read",
                    ),
                    ('sh "$ROOT/tools/ci/planted.sh"', "a script the check cannot locate"),
                )
            ),
            *(
                _together(
                    f"a script whose name has no `.sh` run as `{how}`",
                    _append("the script", rel, "#!/bin/sh\necho planted\n"),
                    _run_step(how),
                    expect=f"runs {rel} as a program, not as `sh {rel}`",
                )
                for how, rel in (
                    ("./tools/ci/planted.bash", "tools/ci/planted.bash"),
                    ("./tools/ci/planted.SH", "tools/ci/planted.SH"),
                    ("./tools/ci/planted", "tools/ci/planted"),
                )
            ),
            _run_step(
                "./.githooks/pre-push", expect="runs .githooks/pre-push as a program, not as"
            ),
            _together(
                "a script a shell's heredoc runs as `bash <path>`",
                _append("the script", "tools/ci/planted.sh", "echo planted\n"),
                _run_block("sh <<'EOF'\nbash tools/ci/planted.sh\nEOF"),
                expect="runs tools/ci/planted.sh with `bash`, not as",
            ),
            _together(
                "a script a python heredoc runs",
                _append("the script", "tools/ci/planted.sh", "echo planted\n"),
                _run_block(
                    "python3 - <<'EOF'\nimport subprocess\n"
                    "subprocess.run(['sh', 'tools/ci/planted.sh'])\nEOF"
                ),
                expect="names tools/ci/planted.sh in a heredoc python3 runs",
            ),
            _run_step("echo hi | sh", expect="runs what a pipe feeds sh"),
            _run_step("bash <<< 'echo hi'", expect="feeds a here-string to bash"),
            _run_step(
                "sh tools/ci/nowhere.sh",
                expect="runs tools/ci/nowhere.sh, which the tree does not hold",
            ),
            _run_step(
                'sh "$SCRIPTS/x.sh"',
                expect="runs $SCRIPTS/x.sh, a script the check cannot locate",
            ),
            _run_step(
                "sh /tmp/x.sh",
                expect="runs /tmp/x.sh, a file outside the repository the check cannot read",
            ),
            # A download run once written, by the step or by a script it runs.
            _run_step(
                'curl -fsSL -o "$RUNNER_TEMP/x.sh" https://example.com/x.sh'
                ' && sh "$RUNNER_TEMP/x.sh"',
                expect="runs $RUNNER_TEMP/x.sh, a file a download wrote",
            ),
            _together(
                "a download a script runs once written",
                _append(
                    "the script",
                    "tools/ci/planted.sh",
                    "wget -qO /tmp/x https://example.com/x && /tmp/x\n",
                ),
                _run_step("sh tools/ci/planted.sh"),
                expect="tools/ci/planted.sh: runs /tmp/x, a file a download wrote",
            ),
            # Scripts that run scripts: each is read, to a depth, and a loop is never followed.
            _together(
                "an install in a script a script runs",
                _append("the outer script", "tools/ci/planted.sh", "sh tools/ci/inner.sh\n"),
                _append("the inner script", "tools/ci/inner.sh", "pip install foo\n"),
                _run_step("sh tools/ci/planted.sh"),
                expect="tools/ci/planted.sh: tools/ci/inner.sh: install of foo",
            ),
            _together(
                "a chain of five scripts, one past the depth",
                *_chain(5),
                _run_step("sh tools/ci/c1.sh"),
                expect="tools/ci/c1.sh: tools/ci/c2.sh: tools/ci/c3.sh: tools/ci/c4.sh:"
                " runs tools/ci/c5.sh past the depth the check follows",
            ),
            _together(
                "a chain of four scripts, at the depth, passes",
                *_chain(4),
                _run_step("sh tools/ci/c1.sh"),
                fails=False,
            ),
            _together(
                "a script that runs itself",
                _append("the script", "tools/ci/planted.sh", "sh tools/ci/planted.sh\n"),
                _run_step("sh tools/ci/planted.sh"),
                expect="tools/ci/planted.sh: runs tools/ci/planted.sh past the depth",
            ),
            _together(
                "two scripts that run each other",
                _append("the first", "tools/ci/planted.sh", "sh tools/ci/inner.sh\n"),
                _append("the second", "tools/ci/inner.sh", "sh tools/ci/planted.sh\n"),
                _run_step("sh tools/ci/planted.sh"),
                expect="tools/ci/inner.sh: runs tools/ci/planted.sh past the depth",
            ),
            *(
                _together(
                    f"a download piped into sh by `{fetch}` in a script a step runs",
                    _append("the script", "tools/ci/planted.sh", f"{fetch} | sh\n"),
                    _run_step("sh tools/ci/planted.sh"),
                    expect="tools/ci/planted.sh: runs a download with sh: https://example.com/x",
                )
                for fetch in ("curl -fsSL https://example.com/x", "wget -qO- https://example.com/x")
            ),
            # Passes: what runs nothing of a script, and what the check reads clean.
            _together(
                "a script a step only syntax-checks, which runs nothing in it, passes",
                _append(
                    "the script",
                    "tools/ci/planted.sh",
                    "curl -fsSL https://example.com/x.sh | sh\n",
                ),
                _run_step("sh -n tools/ci/planted.sh && dash -n tools/ci/planted.sh"),
                fails=False,
            ),
            _together(
                "shellcheck reading a script passes",
                _append("the script", "tools/ci/planted.sh", "echo planted\n"),
                _run_step("shellcheck -s sh tools/ci/planted.sh"),
                fails=False,
            ),
            _together(
                "a pinned install in a script a step runs passes",
                _append("the script", "tools/ci/planted.sh", "pip install foo==1.2.3\n"),
                _run_step("sh tools/ci/planted.sh"),
                fails=False,
            ),
            _run_step(". /etc/os-release", fails=False),
            _run_block("bash <<'EOF'\necho hi\nEOF", fails=False),
            _run_step("bash -o pipefail -c 'echo hi'", fails=False),
        ),
    ),
    # breadth.yml's install-smoke: each smoke script and what it runs is read, and the two
    # installers it tests are allowed what they are tested for in one step and one script only.
    _supply_row(
        "run-install-smokes",
        ("run-install",),
        (
            *(
                _append(
                    f"`{fetch}` in {script}",
                    script,
                    f"{fetch}\n",
                    expect=f"{script}: {expect}",
                )
                for script in (_SMOKE_CURRENT, _SMOKE_STAGED, _SMOKE_SERVED)
                for fetch, expect in (
                    (
                        "curl -fsSL https://example.com/x | sh",
                        "runs a download with sh: https://example.com/x",
                    ),
                    (
                        "wget -qO- https://example.com/x | sh",
                        "runs a download with sh: https://example.com/x",
                    ),
                    (
                        'curl -fsSL -o "$RUNNER_TEMP/x.sh" https://example.com/x'
                        ' && sh "$RUNNER_TEMP/x.sh"',
                        "runs $RUNNER_TEMP/x.sh, a file a download wrote",
                    ),
                )
            ),
            _together(
                "a download piped into sh by a script the current smoke runs",
                _append("the smoke", _SMOKE_CURRENT, "sh tools/ci/planted.sh\n"),
                _append(
                    "the script", "tools/ci/planted.sh", "wget -qO- https://example.com/x | sh\n"
                ),
                expect=f"{_SMOKE_CURRENT}: tools/ci/planted.sh: runs a download with sh",
            ),
            _append(
                "a download piped into sh by the staged installer",
                supply_chain.STAGED_INSTALLER,
                "curl -fsSL https://example.com/x | sh\n",
                expect="deploy/website/install.sh: runs a download with sh: https://example.com/x",
            ),
            # The served installer: piped into sh by one script, run by one step, and nowhere else.
            _run_step(
                f"sh {_SMOKE_SERVED}",
                expect=f"{_SMOKE_SERVED}: runs a download with sh: {supply_chain.OWN_INSTALLER}",
            ),
            _plant(
                "the served installer piped in the served step's own text",
                _BREADTH,
                f"        run: sh {_SMOKE_SERVED}\n",
                "        run: curl -fsSL https://personalclaw.dev/install | sh\n",
                expect="runs a download with sh: https://personalclaw.dev/install",
            ),
            _together(
                "the served step running another script that pipes the installer",
                _append(
                    "the script",
                    "tools/ci/planted.sh",
                    "curl -fsSL https://personalclaw.dev/install | sh\n",
                ),
                _plant(
                    "the served step",
                    _BREADTH,
                    f"        run: sh {_SMOKE_SERVED}\n",
                    f"        run: sh {_SMOKE_SERVED} && sh tools/ci/planted.sh\n",
                ),
                expect="tools/ci/planted.sh: runs a download with sh:"
                " https://personalclaw.dev/install",
            ),
            _together(
                "the served step reaching the served script through another script",
                _append("the script", "tools/ci/planted.sh", f"sh {_SMOKE_SERVED}\n"),
                _plant(
                    "the served step",
                    _BREADTH,
                    f"        run: sh {_SMOKE_SERVED}\n",
                    "        run: sh tools/ci/planted.sh\n",
                ),
                expect=f"tools/ci/planted.sh: {_SMOKE_SERVED}: runs a download with sh",
            ),
            _together(
                "the served step running a copy of the served script",
                _copy("the copy", _SMOKE_SERVED, "tools/ci/served_copy.sh"),
                _plant(
                    "the served step",
                    _BREADTH,
                    f"        run: sh {_SMOKE_SERVED}\n",
                    "        run: sh tools/ci/served_copy.sh\n",
                ),
                expect="tools/ci/served_copy.sh: runs a download with sh:"
                " https://personalclaw.dev/install",
            ),
            # The staged installer: the artifact under test, through the staged step's script.
            _append(
                "a new install in the staged installer",
                supply_chain.STAGED_INSTALLER,
                "pip install foo\n",
                expect="deploy/website/install.sh: install of foo names no exact version",
            ),
            _line(
                "the staged installer no longer bootstrapping from Astral's installer",
                supply_chain.STAGED_INSTALLER,
                "UV_INSTALLER_URL=",
                'UV_INSTALLER_URL="https://example.com/uv.sh"',
                expect="no longer names Astral's uv installer",
            ),
            _run_step(
                f"sh {supply_chain.STAGED_INSTALLER}",
                expect="it run: deploy/website/install.sh: runs a download with sh",
            ),
            _run_step(
                f"sh {_SMOKE_STAGED}",
                expect=f"it run: {_SMOKE_STAGED}: {supply_chain.STAGED_INSTALLER}: runs a download",
            ),
            _plant(
                "the staged step running the staged installer in its own text",
                _BREADTH,
                f"        run: sh {_SMOKE_STAGED}\n",
                f"        run: sh {supply_chain.STAGED_INSTALLER}\n",
                expect=f"{_STAGED_STEP}: deploy/website/install.sh: runs a download with sh",
            ),
            _together(
                "the staged step reaching the staged script through another script",
                _append("the script", "tools/ci/planted.sh", f"sh {_SMOKE_STAGED}\n"),
                _plant(
                    "the staged step",
                    _BREADTH,
                    f"        run: sh {_SMOKE_STAGED}\n",
                    "        run: sh tools/ci/planted.sh\n",
                ),
                expect=f"tools/ci/planted.sh: {_SMOKE_STAGED}: deploy/website/install.sh: runs a"
                " download with sh",
            ),
            _together(
                "the staged script running a copy of the staged installer",
                _copy("the copy", supply_chain.STAGED_INSTALLER, "deploy/website/install-copy.sh"),
                _plant(
                    "the staged script",
                    _SMOKE_STAGED,
                    f"sh {supply_chain.STAGED_INSTALLER}\n",
                    "sh deploy/website/install-copy.sh\n",
                ),
                expect="deploy/website/install-copy.sh: runs a download with sh",
            ),
        ),
    ),
    _supply_row(
        "setup-floating",
        ("setup-floating",),
        (
            # The tier workflows' jobs set up uv and Node through the setup action, so these
            # plants edit the action's own steps.
            _plant(
                "setup-node with check-latest",
                _SETUP_ACTION,
                "node-version-file: .nvmrc\n",
                "node-version-file: .nvmrc\n        check-latest: true\n",
            ),
            _plant("setup-uv with no version", _SETUP_ACTION, '        version: "0.13.1"\n', ""),
            _plant(
                "setup-node with no version",
                _SETUP_ACTION,
                "        node-version-file: .nvmrc\n",
                "",
            ),
            _line(
                "setup-qemu with no image", _ARTIFACTS_YML, "          image: docker.io/tonistiigi"
            ),
            _plant(
                "setup-qemu's image by tag",
                _ARTIFACTS_YML,
                f"binfmt:qemu-v10.2.3-68@sha256:{_QEMU_AMD64}",
                "binfmt:latest",
            ),
            _plant("buildx's latest", _ARTIFACTS_YML, "version: v0.38.0", "version: latest"),
            _line("buildx with no BuildKit image", _ARTIFACTS_YML, "          driver-opts: image="),
            _planted_action(
                "setup-node on latest in a local composite action",
                f"    - uses: {_SETUP_NODE}\n      with:\n        node-version: latest\n",
                expect="node-version latest is not an exact X.Y.Z",
            ),
            _planted_workflow(
                "setup-node on a version from the matrix",
                f"      - uses: {_SETUP_NODE}\n        with:\n"
                "          node-version: ${{ matrix.node }}\n",
                job="    strategy:\n      matrix:\n        node: [latest]\n",
                expect="is an expression",
            ),
            _planted_workflow(
                "a setup action the check does not know",
                "      - uses: actions/setup-go@" + "a" * 40 + " # v5.0.0\n"
                "        with:\n          go-version: 1.22.0\n",
                expect="add its version inputs to SETUP_INPUTS",
            ),
            _planted_workflow(
                "an installer action named outside setup-*",
                "      - uses: pnpm/action-setup@" + "a" * 40 + " # v4.0.0\n"
                "        with:\n          version: 9\n",
                expect="installs a tool this check does not know",
            ),
            _plant(
                "setup-node with check-latest from an expression",
                _SETUP_ACTION,
                "node-version-file: .nvmrc\n",
                "node-version-file: .nvmrc\n"
                "        check-latest: ${{ github.event_name == 'schedule' }}\n",
                expect="sets check-latest",
            ),
            _planted_workflow(
                "a setup step whose with: is an expression",
                f"      - uses: {_SETUP_NODE}\n        with: ${{{{ matrix.with }}}}\n",
                expect="is not a mapping",
            ),
            _plant(
                "buildx's exact version without the v passes",
                _ARTIFACTS_YML,
                "version: v0.38.0",
                "version: 0.38.0",
                fails=False,
            ),
        ),
    ),
    _supply_row(
        "node-eol",
        ("node-eol", "policy-file"),
        (
            _line("the pinned line past its date", _POLICY, '"24" =', f'"24" = {_days(-1)}'),
            _line("the pinned line not in the table", _POLICY, '"24" ='),
            _line(
                "a table value that is not a date",
                _POLICY,
                '"24" =',
                '"24" = "2028-04-30"',
                expect="must be a major line with a date",
            ),
            _planted_workflow(
                "setup-node on a line past its date",
                f"      - uses: {_SETUP_NODE}\n        with:\n          node-version: 20.19.0\n",
                expect="20.19.0 reached its end of life",
            ),
            _plant(".nvmrc on a newer dated line passes", ".nvmrc", _NVMRC, "26.1.0", fails=False),
            # The floor the Intel Mac app's cryptography build holds its OpenSSL to.
            _line(
                "no OpenSSL floor for the Intel cryptography build",
                _POLICY,
                "openssl_minimum =",
                expect="cryptography_from_source must name openssl_minimum",
            ),
            _line(
                "an OpenSSL floor that is not X.Y.Z",
                _POLICY,
                "openssl_minimum =",
                'openssl_minimum = "3.6"',
                expect="cryptography_from_source must name openssl_minimum",
            ),
            # Every place a Node can come from.
            _append(
                "an OS-package nodejs in a Dockerfile",
                "tools/sandbox/Dockerfile",
                _PINNED_NODE_BASE + "RUN apt-get install -y --no-install-recommends nodejs\n",
                expect="installs Node as an OS package",
            ),
            _plant(
                "the gateway image's runtime stage back on Debian's nodejs",
                _BACKEND,
                "        git \\\n    && rm -rf /var/lib/apt/lists/*\n",
                "        git \\\n        nodejs \\\n    && rm -rf /var/lib/apt/lists/*\n",
                expect="installs Node as an OS package",
            ),
            _append(
                "a Node only in a Dockerfile, on a line past its date",
                "tools/sandbox/Dockerfile",
                _PINNED_NODE_BASE.replace(f"node:{_NVMRC}", "node:20.19.0"),
                expect="20.19.0 reached its end of life",
            ),
            _append(
                "a Node tag built from an ARG past its date",
                "tools/sandbox/Dockerfile",
                "ARG NODE_VERSION=20.19.0\nFROM node:${NODE_VERSION}-alpine@sha256:"
                + "e" * 64
                + "\n",
                expect="20.19.0 reached its end of life",
            ),
            _planted_workflow(
                "a job container on a Node line past its date",
                "      - run: true\n",
                job="    container: node:20.19.0-alpine@sha256:" + "e" * 64 + "\n",
                expect="20.19.0 reached its end of life",
            ),
            _planted_workflow(
                "a heredoc build on a Node line past its date",
                "      - run: |\n          docker build - <<'EOF'\n          FROM node:20.19.0\n"
                "          EOF\n",
                expect="20.19.0 reached its end of life",
            ),
            _planted_workflow(
                "setup-node reading a version file other than .nvmrc",
                f"      - uses: {_SETUP_NODE}\n        with:\n"
                "          node-version-file: package.json\n",
                expect="read from package.json",
            ),
        ),
    ),
    _supply_row(
        "least-privilege",
        ("least-privilege",),
        (
            _plant(
                "write-all at the top",
                _BREADTH,
                "permissions:\n  contents: read\n",
                "permissions: write-all\n",
            ),
            _planted_workflow("write-all on a job", "", job="    permissions: write-all\n"),
            _planted_workflow(
                "an unlisted contents: write on a job",
                "      - run: true\n",
                job="    permissions:\n      contents: write\n",
            ),
            _planted_workflow(
                "an unlisted write inherited from the top",
                "      - run: true\n",
                top="permissions:\n  issues: write\n",
                expect="inherited from the top",
            ),
            _plant(
                "a top-level write beside a job that grants its own",
                ".github/workflows/pr-feedback.yml",
                "  actions: read           # download the artifact from the CI run\n\njobs:",
                "  actions: read\n  pull-requests: write\n\njobs:",
                expect="top-level write",
            ),
            _plant(
                "an extra write on a listed job",
                _RELEASE_YML,
                "      contents: write\n",
                "      contents: write\n      issues: write\n",
            ),
            _planted_workflow("a job with no permissions anywhere", "      - run: true\n", top=""),
            _line(
                "a checkout that keeps its credentials",
                _MERGE_YML,
                "          persist-credentials: false",
            ),
            _plant(
                "a workflow env that hands a token to every step",
                ".github/workflows/headless-run-smoke.yml",
                _PREFERENCE,
                _PREFERENCE + "  GH_TOKEN: ${{ github.token }}\n",
            ),
            _plant(
                "a job env that hands a secret to every step",
                _BREADTH,
                '      PERSONALCLAW_SKIP_APP_BACKENDS: "1"\n',
                '      PERSONALCLAW_SKIP_APP_BACKENDS: "1"\n      SMOKE: ${{ secrets.SMOKE }}\n',
            ),
            *(
                _line(
                    f"an audited write with no {field}", _POLICY, start, expect=f"missing {field}"
                )
                for field, start in (
                    ("workflow", 'workflow = "dco.yml"'),
                    ("job", 'job = "dco"'),
                    ("permission", 'permission = "pull-requests: write"'),
                    ("steps", 'steps = ["Explain how to fix DCO"]'),
                    ("reason", 'reason = "Posts or updates the one comment that names each'),
                )
            ),
            _line(
                "an audited write for a job that is gone",
                _POLICY,
                'job = "dco"',
                'job = "gone"',
                expect="matches no job",
            ),
            _line(
                "an audited write naming a step the job lacks",
                _POLICY,
                'steps = ["Explain how to fix DCO"]',
                'steps = ["No such step"]',
                expect="no step named",
            ),
            _line(
                "an audited write for a permission the job does not hold",
                _POLICY,
                'permission = "pull-requests: write"',
                'permission = "issues: write"',
                expect="does not hold",
            ),
            _planted_action(
                "a checkout that keeps its credentials in a local composite action",
                "    - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n",
                expect="action actions/checkout: checkout keeps its credentials",
            ),
            *(
                _planted_workflow(
                    f"a job env that hands a token as {spelling}",
                    "      - run: true\n",
                    job=f"    env:\n      X: ${{{{ {spelling} }}}}\n",
                    expect="hands a token to every step",
                )
                for spelling in ("secrets['X']", "toJSON(secrets)", "GITHUB.TOKEN")
            ),
            _planted_workflow(
                "a job container's env that hands a token to every step",
                "      - run: true\n",
                job="    container:\n      image: alpine:3.20\n      env:\n"
                "        T: ${{ github.token }}\n",
                expect="container env T",
            ),
            _append(
                "a call to another repository's workflow with no permissions anywhere",
                ".github/workflows/planted.yml",
                "name: planted\non: workflow_dispatch\njobs:\n  called:\n"
                "    uses: org/repo/.github/workflows/x.yml@" + "a" * 40 + " # v1.0.0\n",
                expect="declares no permissions",
            ),
            _append(
                "a call to a local workflow with no permissions passes",
                ".github/workflows/planted.yml",
                "name: planted\non: workflow_dispatch\njobs:\n  called:\n"
                "    uses: ./.github/workflows/dco.yml\n",
                fails=False,
            ),
            _planted_workflow(
                "a job env that is an expression",
                "      - run: true\n",
                job="    env: ${{ fromJSON(inputs.env) }}\n",
                expect="is an expression, so its variables cannot be read",
            ),
            _planted_workflow(
                "a workflow env that is an expression",
                "      - run: true\n",
                top="permissions: {}\nenv: ${{ fromJSON(inputs.env) }}\n",
                expect="is an expression, so its variables cannot be read",
            ),
            _planted_workflow(
                "a checkout whose with: is an expression",
                f"      - uses: {_CHECKOUT}\n        with: ${{{{ matrix.checkout }}}}\n",
                expect="checkout keeps its credentials",
            ),
            _together(
                "one credential audit covering two unnamed checkouts",
                _planted_workflow("", f"      - uses: {_CHECKOUT}\n      - uses: {_CHECKOUT}\n"),
                _append(
                    "",
                    _POLICY,
                    '\n[[audited_write]]\nworkflow = "planted.yml"\njob = "it"\n'
                    'permission = "persist-credentials"\nsteps = ["actions/checkout"]\n'
                    'reason = "planted"\n',
                ),
                expect="names more than one checkout",
            ),
            _together(
                "a credential audit naming a checkout that does not keep them",
                _planted_workflow(
                    "",
                    f"      - name: Fetch\n        uses: {_CHECKOUT}\n        with:\n"
                    "          persist-credentials: false\n",
                ),
                _append(
                    "",
                    _POLICY,
                    '\n[[audited_write]]\nworkflow = "planted.yml"\njob = "it"\n'
                    'permission = "persist-credentials"\nsteps = ["Fetch"]\nreason = "planted"\n',
                ),
                expect="does not hold persist-credentials",
            ),
            _together(
                "a checkout that keeps its credentials passes once it is an audited write",
                _line("", _MERGE_YML, "          persist-credentials: false"),
                _append("", _POLICY, _LISTED_CHECKOUT),
                fails=False,
            ),
        ),
    ),
    _supply_row(
        "dependabot",
        ("dependabot",),
        (
            _plant(
                "the docker entry watching another folder",
                ".github/dependabot.yml",
                "    directory: /deploy/docker\n",
                "    directory: /deploy/compose\n",
            ),
            _append("a Dockerfile in a folder nothing watches", "tools/sandbox/Dockerfile", "\n"),
            _plant(
                "the agent CLIs' lock folder that no npm entry watches",
                ".github/dependabot.yml",
                f"    directory: /{_AGENT_CLIS}\n",
                "    directory: /deploy/compose\n",
                expect=f"no Dependabot npm entry watches /{_AGENT_CLIS}",
            ),
            _together(
                "the agent CLIs' lock folder that only a docker entry's glob covers",
                _plant(
                    "",
                    ".github/dependabot.yml",
                    f"    directory: /{_AGENT_CLIS}\n",
                    "    directory: /deploy/compose\n",
                ),
                _plant(
                    "",
                    ".github/dependabot.yml",
                    "    directory: /deploy/docker\n",
                    "    directories:\n      - /deploy/**\n",
                ),
                expect=f"no Dependabot npm entry watches /{_AGENT_CLIS}",
            ),
            # The entry that moves the lock waits out a week, and moves every tool as one.
            _plant(
                "the agent CLIs' npm entry with no cooldown",
                ".github/dependabot.yml",
                f"    directory: /{_AGENT_CLIS}\n{_AGENT_SCHEDULE}    cooldown:\n"
                "      default-days: 7\n",
                f"    directory: /{_AGENT_CLIS}\n{_AGENT_SCHEDULE}",
                expect=f"/{_AGENT_CLIS} waits no cooldown of at least 7 days",
            ),
            _plant(
                "the agent CLIs' npm entry with a three-day cooldown",
                ".github/dependabot.yml",
                f"    directory: /{_AGENT_CLIS}\n{_AGENT_SCHEDULE}    cooldown:\n"
                "      default-days: 7\n",
                f"    directory: /{_AGENT_CLIS}\n{_AGENT_SCHEDULE}    cooldown:\n"
                "      default-days: 3\n",
                expect=f"/{_AGENT_CLIS} waits no cooldown of at least 7 days",
            ),
            _plant(
                "the agent CLIs' npm entry with no group",
                ".github/dependabot.yml",
                '    groups:\n      agent-clis:\n        patterns: ["*"]\n',
                "",
                expect=f"/{_AGENT_CLIS} does not move every package in one group",
            ),
            _plant(
                "the agent CLIs' npm entry grouping only some packages",
                ".github/dependabot.yml",
                '      agent-clis:\n        patterns: ["*"]\n',
                '      agent-clis:\n        patterns: ["@openai/*"]\n',
                expect=f"/{_AGENT_CLIS} does not move every package in one group",
            ),
            # A Dependabot PR that moves the lock builds and smokes the image that installs it:
            # the catalogue's image entry, which the tier workflows plan by its inputs.
            _plant(
                "the image entry not planned on a change of the agent CLIs' lock",
                catalogue.CATALOGUE,
                _IMAGE_INPUT,
                f'  "{_BACKEND}",\n',
                expect=f"the catalogue's image runs on a change of {_BACKEND} but not on one of "
                f"{_AGENT_LOCK}",
            ),
            _plant(
                "the image entry taking the agent CLIs' lock back out of its inputs",
                catalogue.CATALOGUE,
                _IMAGE_INPUT,
                f'{_IMAGE_INPUT}  ":(exclude){_AGENT_LOCK}",\n',
                expect=f"but not on one of {_AGENT_LOCK}",
            ),
            _plant(
                "the image entry listing the agent CLIs' two files passes",
                catalogue.CATALOGUE,
                _IMAGE_INPUT,
                f'  "{_BACKEND}",\n  "{_AGENT_MANIFEST}",\n  "{_AGENT_LOCK}",\n',
                fails=False,
            ),
            # A workflow of its own that builds the image is held the same way.
            _append(
                "a workflow run on a pull request that changes the Dockerfile, not the lock",
                ".github/workflows/image-check.yml",
                "name: Image check\non:\n  pull_request:\n    paths:\n"
                f"      - {_BACKEND}\npermissions: {{}}\njobs: {{}}\n",
                expect=".github/workflows/image-check.yml runs on a pull_request that changes "
                f"{_BACKEND} but not on one that changes {_AGENT_LOCK}",
            ),
            _append(
                "a workflow on all of deploy/ but the agent CLIs' lock",
                ".github/workflows/image-check.yml",
                "name: Image check\non:\n  push:\n    paths:\n      - deploy/**\n"
                f"      - '!{_AGENT_CLIS}/**'\npermissions: {{}}\njobs: {{}}\n",
                expect=".github/workflows/image-check.yml runs on a push that changes "
                f"{_BACKEND} but not on one that changes {_AGENT_LOCK}",
            ),
            _plant(
                "a base named through an ARG, which Dependabot cannot move",
                _BACKEND,
                "FROM mirror.gcr.io/library/" + _PYTHON + " AS base",
                "ARG PYTHON_IMAGE=mirror.gcr.io/library/"
                + _PYTHON
                + "\nFROM ${PYTHON_IMAGE} AS base",
                expect="names its base through an ARG",
            ),
            _plant(
                "a build-platform base named through an ARG, which Dependabot cannot move",
                _WEB,
                _SPA_FROM,
                f"ARG NODE_IMAGE=mirror.gcr.io/library/{_NODE_BASE}\n"
                "FROM --platform=$BUILDPLATFORM ${NODE_IMAGE}",
                expect="FROM ${NODE_IMAGE} names its base through an ARG",
            ),
            _plant(
                "a directories: list that leaves the folder out",
                ".github/dependabot.yml",
                "    directory: /deploy/docker\n",
                "    directories:\n      - /deploy/compose\n",
                expect="watches /deploy/docker",
            ),
            _together(
                "a root Dockerfile beside a directories: list that leaves / out",
                _append("", "Dockerfile", _PINNED_NODE_BASE),
                _plant(
                    "",
                    ".github/dependabot.yml",
                    "    directory: /deploy/docker\n",
                    "    directories:\n      - /deploy/docker\n",
                ),
                expect="watches /,",
            ),
            Control(
                "a dependabot.yml that is a list",
                lambda root: _write(
                    root, ".github/dependabot.yml", "- version: 2\n- updates: []\n"
                ),
                expect="is not a mapping with an `updates:` list",
            ),
            _plant(
                "the folder in a directories: list passes",
                ".github/dependabot.yml",
                "    directory: /deploy/docker\n",
                "    directories:\n      - /deploy/docker\n",
                fails=False,
            ),
            _plant(
                "a directories: glob covering the folder passes",
                ".github/dependabot.yml",
                "    directory: /deploy/docker\n",
                "    directories:\n      - /deploy/*\n",
                fails=False,
            ),
            _plant(
                "the folder written with a trailing slash passes",
                ".github/dependabot.yml",
                "    directory: /deploy/docker\n",
                "    directory: /deploy/docker/\n",
                fails=False,
            ),
        ),
    ),
    _supply_row(
        "release-tooling",
        ("release-tooling",),
        (
            _plant("the release build's buildx moved alone", _ARTIFACTS_YML, "v0.38.0", "v0.37.0"),
            _plant("the smoke's buildx moved alone", _TOOLING, "v0.38.0", "v0.37.0"),
            _plant("the smoke's QEMU image moved alone", _TOOLING, _QEMU_AMD64, "c" * 64),
            _plant(
                "the smoke not run when release.yml changes",
                _TOOLING,
                "      - .github/workflows/release.yml\n",
                "",
            ),
            _plant(
                "the smoke not run when release-artifacts.yml changes",
                _TOOLING,
                "      - .github/workflows/release-artifacts.yml\n",
                "",
            ),
            # layout-smoke: the same pins, nothing to publish with, and only local copies.
            _plant(
                "the layout smoke's QEMU action moved alone",
                _TOOLING,
                "persist-credentials: false\n      - uses: docker/setup-qemu-action@99012661954931",
                "persist-credentials: false\n      - uses: docker/setup-qemu-action@00000000000000",
                expect="layout-smoke runs docker/setup-qemu-action as",
            ),
            _plant(
                "the layout smoke pushing its copy",
                _TOOLING,
                '"oci:$RUNNER_TEMP/copied:smoke"',
                '"docker://ghcr.io/personalclaw/personalclaw-web:smoke"',
                expect="copies an image to 'docker://",
            ),
            _plant(
                "the layout smoke holding a permission",
                _TOOLING,
                "  layout-smoke:\n    runs-on: ubuntu-24.04\n    permissions: {}\n",
                "  layout-smoke:\n    runs-on: ubuntu-24.04\n"
                "    permissions:\n      packages: write\n",
                expect="layout-smoke must hold `permissions: {}`",
            ),
            _plant(
                "the layout smoke logging in",
                _TOOLING,
                "          skopeo --version\n",
                "          skopeo --version\n          docker login ghcr.io\n",
                expect="layout-smoke step",
            ),
            _plant(
                "the layout smoke switched off",
                _TOOLING,
                "  layout-smoke:\n    runs-on: ubuntu-24.04\n",
                "  layout-smoke:\n    runs-on: ubuntu-24.04\n    if: false\n",
                expect="layout-smoke sets `if`",
            ),
            _plant(
                "the smoke holding a permission",
                _TOOLING,
                "permissions: {}\n\njobs:",
                "permissions:\n  contents: read\n\njobs:",
            ),
            _plant(
                "the smoke logging in",
                _TOOLING,
                "      - name: Build a two-platform image (no push)\n",
                f"      - uses: {_LOGIN}\n      - name: Build a two-platform image (no push)\n",
            ),
            _plant("the smoke publishing", _TOOLING, "--output type=cacheonly", "--push"),
            # The smoke must run, and must build what the release builds.
            _plant(
                "the smoke building one platform",
                _TOOLING,
                "--platform linux/amd64,linux/arm64",
                "--platform linux/amd64",
                expect="does not build linux/arm64",
            ),
            _plant(
                "the smoke job switched off",
                _TOOLING,
                "    runs-on: ubuntu-24.04\n    permissions: {}\n",
                "    runs-on: ubuntu-24.04\n    if: false\n    permissions: {}\n",
                expect="sets `if`",
            ),
            _plant(
                "the smoke's build step switched off",
                _TOOLING,
                "      - name: Build a two-platform image (no push)\n",
                "      - name: Build a two-platform image (no push)\n        if: false\n",
                expect="can be skipped",
            ),
            _plant(
                "the smoke with no build",
                _TOOLING,
                "docker buildx build --platform",
                "echo docker-buildx-build --platform",
                expect="needs exactly one",
            ),
            # Publishing in a form the old denylist missed.
            _plant(
                "the smoke writing an image that pushes",
                _TOOLING,
                "--output type=cacheonly",
                "--output type=image,push=true",
                expect="logs in or publishes",
            ),
            _plant(
                "the smoke running build-push-action",
                _TOOLING,
                "      - name: Build a two-platform image (no push)\n",
                "      - uses: docker/build-push-action@"
                + "a" * 40
                + " # v7.4.0\n        with:\n          push: true\n"
                "      - name: Build a two-platform image (no push)\n",
                expect="only the two setup actions may run there",
            ),
            _plant(
                "the smoke logging in from a run step",
                _TOOLING,
                "          docker buildx version\n",
                "          docker buildx version\n          docker login ghcr.io\n",
                expect="logs in or publishes",
            ),
            # Its base is the images' own.
            _plant(
                "the smoke's base moved alone",
                _TOOLING,
                _NODE_BASE,
                "node:20.19.0-alpine@sha256:" + "d" * 64,
                expect="the images' web stage on",
            ),
            _plant(
                "Dockerfile.web's node base moved alone",
                _WEB,
                _NODE_BASE,
                "node:20.19.0-alpine@sha256:" + "d" * 64,
                expect="the images' web stage on",
            ),
            _plant(
                "a comment changed on one side passes",
                _TOOLING,
                "# These three steps are",
                "# These steps are",
                fails=False,
            ),
        ),
    ),
    _supply_row(
        "exceptions",
        (),
        (
            _waived(
                "an expired exception",
                _WAIVED_FIELDS["expires"],
                f"expires = {_days(-1)}\n",
                expect="expired",
            ),
            _waived(
                "an exception more than 30 days out",
                _WAIVED_FIELDS["expires"],
                f"expires = {_days(31)}\n",
                expect="more than 30 days",
            ),
            _waived(
                "an exception that matches nothing",
                _WAIVED_FIELDS["reference"],
                'reference = "nothing"\n',
                expect="matches nothing",
            ),
            *(
                _waived(f"an exception with no {field}", line, "", expect=f"missing {field}")
                for field, line in _WAIVED_FIELDS.items()
            ),
            _waived(
                "an exception for a rule no exception may waive",
                _WAIVED_FIELDS["scope"],
                'scope = "least-privilege"\n',
                expect="not a rule an exception may waive",
            ),
            _waived(
                "an exception with an unknown field",
                _WAIVED_FIELDS["owner"],
                'owner = "maintainers"\nticket = "x"\n',
                expect="unknown field ticket",
            ),
            _waived(
                "an exception that waives the install under another rule",
                _WAIVED_FIELDS["scope"],
                'scope = "node-eol"\n',
                expect="install of ruff names no exact version",
            ),
            _together(
                "an exception for one install leaves a second in the same step failing",
                _planted_workflow(
                    "",
                    "      - name: Install tools\n"
                    "        run: pip install ruff && pip install black\n",
                ),
                _append(
                    "",
                    _POLICY,
                    _exception("it Install tools: install of ruff names no exact version"),
                ),
                expect="install of black names no exact version",
            ),
            _together(
                "an exception left behind once its install is pinned matches nothing",
                _planted_workflow(
                    "",
                    "      - name: Install tools\n"
                    "        run: pip install ruff==0.15.0 && npx cowsay hi\n",
                ),
                _append(
                    "",
                    _POLICY,
                    _exception("it Install tools: install of ruff names no exact version"),
                ),
                expect="matches nothing",
            ),
            _together(
                "one exception covers one of two unnamed run steps",
                _planted_workflow(
                    "", "      - run: pip install ruff\n      - run: pip install ruff\n"
                ),
                _append("", _POLICY, _exception("it run: install of ruff names no exact version")),
                expect="install of ruff names no exact version",
            ),
            _together(
                "an exception that names the one install it waives passes",
                _planted_workflow(
                    "", "      - name: Install tools\n        run: pip install ruff\n"
                ),
                _append(
                    "",
                    _POLICY,
                    _exception("it Install tools: install of ruff names no exact version"),
                ),
                fails=False,
            ),
            _waived(
                "an exception whose owner is an empty list",
                _WAIVED_FIELDS["owner"],
                "owner = []\n",
                expect="missing owner",
            ),
            _waived(
                "an exception whose reason is false",
                _WAIVED_FIELDS["reason"],
                "reason = false\n",
                expect="missing reason",
            ),
            _waived("an exception renewed exactly 30 days out passes", fails=False),
        ),
    ),
    _supply_row(
        "workflow-yaml",
        ("workflow-yaml",),
        (
            *(
                _append(name, ".github/workflows/planted.yml", text, expect=expect)
                for name, text, expect in (
                    ("a workflow that does not parse", "name: p\non: [push\n", "does not parse"),
                    ("a workflow with an unknown tag", "name: p\nx: !bogus y\n", "does not parse"),
                    ("a workflow that is a list", "- a\n- b\n", "is not a YAML mapping"),
                    (
                        "a workflow whose jobs: is a list",
                        "name: p\non: push\njobs: [a]\n",
                        "`jobs:` that is not a mapping",
                    ),
                )
            ),
            _append(
                "a local action that does not parse",
                ".github/actions/planted/action.yml",
                "name: p\nruns: [composite\n",
                expect="does not parse",
            ),
            _planted_workflow(
                "a bare = value, which GitHub reads as text, passes",
                "      - run: echo x\n        env:\n          X: =\n",
                fails=False,
            ),
        ),
    ),
)


#: The rows that need more than the suite's default cap, keyed by the row's name (see
#: test_ci_contract's ROW_TIMEOUTS). tests/timeout-marks.toml lists each one with why.
ROW_TIMEOUTS = {
    "run-install": pytest.mark.timeout(90),
    "run-install-scripts": pytest.mark.timeout(40),
}


@pytest.mark.parametrize("row", table_params(SUPPLY_CHAIN_ROWS, ROW_TIMEOUTS))
def test_the_supply_chain_holds_and_each_check_can_fail(
    row: Row, subtests, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    run_row(row, subtests, tmp_path, request)

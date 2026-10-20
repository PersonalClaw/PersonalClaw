"""CI's supply chain: what the workflows and Dockerfiles fetch is pinned, and every job holds
only what it needs.

``check`` reads a tree (repository-relative path to text): the workflows, the local actions,
every Dockerfile, the Dependabot file, ``.nvmrc``, the root npm manifest and lock, each image's
npm manifest and lock under ``deploy/docker/``, every shell script, and the policy. It returns
one ``Violation`` per broken rule, with the policy file's exceptions applied. It never raises on
a malformed input: that reads as a violation too. The rules:

- ``action-sha``: a ``uses:`` names a 40-character commit SHA and its release as ``# vX.Y.Z``,
  on one line the reader can read; a ``docker://`` action is an image and is judged as one; a
  local ``./`` reference names a file the tree holds.
- ``image-digest``: an image is a tag with a digest, never ``latest``, under a comment that names
  the tag. A workflow's image (container, service, BuildKit, ``docker run|pull``) names its
  linux/amd64 manifest and the index it belongs to, and does not pin that index. A Dockerfile's
  base (``FROM``, ``COPY --from``, ``RUN --mount=from``, a build's stdin ``FROM``) lists each
  platform's own manifest digest. A build may not override a base ``ARG``.
- ``run-install``: a command that installs a tool, wherever the shell would run it
  (tools/ci/shell_installs.py), names its exact version, takes it from a lock, or installs this
  project; a download is never run by a shell or an interpreter, but the project's own installer
  in the one step and script that test it. A step's repository scripts are read under its rules:
  a script runs as ``sh <path>``, and every other way of running one is a violation. OS packages
  are outside the rule. An image's npm manifest under ``deploy/docker/`` pins each tool exactly,
  under ``dependencies`` only; the lock beside it was generated from it, and fetches every
  package from the npm registry under a sha512 integrity; a Dockerfile stage copies the lock in
  and runs ``npm ci`` after the copy; and no other install fetches a tool that manifest names.
- ``setup-floating``: a setup or install action pins a literal exact version (or the file that
  names one), never ``check-latest``; QEMU and BuildKit images are pinned by digest; an action
  the check does not know fails until it is listed.
- ``node-eol``: every Node CI or an image runs, an OS package's ``nodejs`` included, is on a
  line the policy dates, before that date.
- ``least-privilege``: no ``write-all``, every job declares permissions or inherits declared
  ones, every write and every checkout that keeps its credentials is an audited write in the
  policy, no top-level write reaches a job unasked, and no workflow-, job- or container-level
  ``env:`` hands a token to every step.
- ``dependabot``: every folder that holds a Dockerfile has a Dependabot ``docker`` entry, and no
  base is named through an ``ARG`` Dependabot cannot move. Every image's npm manifest folder has
  an ``npm`` entry that waits a seven-day cooldown and moves every package in one group, and a
  workflow whose ``paths:`` run it on a change of a Dockerfile also runs on a change of each
  lock that Dockerfile installs.
- ``release-tooling``: the pull-request smoke of the release's QEMU and buildx steps runs
  release.yml's own pins and the images' own node base, on every change of either file, holding
  no permission, always running, building every platform the release builds, and nothing else.
- ``workflow-yaml``: every workflow and action file parses. Never waived.
- ``policy-file`` and ``exception``: the policy itself is well formed, and an exception waives
  exactly one violation.

See docs/maintainers/ci.md, "Supply chain", for what the check does not read.
"""

from __future__ import annotations

import datetime
import fnmatch
import functools
import json
import os
import posixpath
import re
import shlex
import tomllib
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import shell_installs

POLICY_FILE = "tools/ci/supply-chain.toml"
DEPENDABOT_FILE = ".github/dependabot.yml"
EXCEPTION_MAX_DAYS = 30
#: The rules an exception may waive. A write is audited in the policy, never waived for a month.
WAIVABLE = ("action-sha", "image-digest", "run-install", "setup-floating", "node-eol")
EXCEPTION_FIELDS = ("file", "reference", "scope", "owner", "reason", "expires")
AUDITED_WRITE_FIELDS = ("workflow", "job", "permission", "steps", "reason")
CREDENTIALS = "persist-credentials"

_SHA = re.compile(r"[0-9a-f]{40}")
_RELEASE = re.compile(r"v\d+\.\d+\.\d+")
_EXACT = re.compile(r"\d+\.\d+\.\d+")
_DIGEST = re.compile(r"[^\s@]+:[^\s@:]+@sha256:([0-9a-f]{64})")
_TOKEN = re.compile(
    r"\bsecrets\b|\bgithub\s*(?:\.\s*token\b|\[\s*['\"]token['\"]\s*\])", flags=re.I
)


def hands_token(value: object) -> bool:
    """Whether *value* reads a secret or the job token in a `${{ }}` expression, in any
    spelling: `secrets.X`, `secrets['X']`, `toJSON(secrets)`, `github.token`,
    `github['token']`, any case."""
    return any(_TOKEN.search(body) for body in re.findall(r"\$\{\{(.*?)\}\}", str(value), re.S))


_SKIPPED = {".git", ".local", ".worktrees", "node_modules", "dist", "build", "__pycache__"}

Tree = Mapping[str, str]
#: libyaml's loader where it is built, which reads the workflows several times faster.
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@dataclass(frozen=True)
class Violation:
    rule: str
    file: str
    #: What an exception names to waive this one: stable across unrelated edits of the file.
    reference: str
    message: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.file}: {self.message}"


#: The suffixes GitHub runs a workflow file under. Every reader of the workflows uses this pair.
WORKFLOW_SUFFIXES = (".yml", ".yaml")
#: A file whose name says Dockerfile but holds code or prose about one.
_NOT_A_DOCKERFILE = (".py", ".md", ".ts", ".json", ".yml", ".yaml", ".toml", ".sh", ".txt")


def is_workflow(rel: str) -> bool:
    return rel.startswith(".github/workflows/") and rel.endswith(WORKFLOW_SUFFIXES)


def is_dockerfile(name: str) -> bool:
    """Any spelling a build may name: ``Dockerfile``, ``Dockerfile.web``, ``Dockerfile-dev``,
    ``dockerfile``, ``app.dockerfile``, ``Containerfile``, in any case."""
    return bool(
        re.fullmatch(
            r"(?:docker|container)file(?:[._-][\w.-]+)?|[\w.-]+[._-](?:docker|container)file",
            name,
            flags=re.I,
        )
    ) and not name.lower().endswith(_NOT_A_DOCKERFILE)


def dockerfile_paths(root: Path) -> list[Path]:
    """Every Dockerfile under *root*, wherever it sits, so a new one cannot go unread."""
    found: list[Path] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIPPED and not d.startswith(".venv")]
        found += [Path(folder) / f for f in files if is_dockerfile(f)]
    return sorted(found)


def action_paths(root: Path) -> list[Path]:
    """Every local action's ``action.yml`` or ``action.yaml`` under *root*, wherever it sits."""
    found: list[Path] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIPPED and not d.startswith(".venv")]
        found += [Path(folder) / f for f in files if f in ("action.yml", "action.yaml")]
    return sorted(found)


def script_paths(root: Path) -> list[Path]:
    """Every shell script under *root*, wherever it sits: a ``.sh`` or ``.bash`` file in any
    case, or a file with no suffix whose first line opens a shell (a git hook). A step may run
    any of them, and the check reads the ones it runs."""
    found: list[Path] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIPPED and not d.startswith(".venv")]
        for name in files:
            path = Path(folder) / name
            if is_script_name(name) or ("." not in name and _opens_a_shell(path)):
                found.append(path)
    return sorted(found)


def _opens_a_shell(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            first = handle.readline(200).decode("utf-8", "replace")
    except OSError:
        return False
    return bool(_SHELL_SHEBANG.match(first))


def tree_paths(root: Path) -> list[Path]:
    """The files ``check`` reads."""
    return [
        *sorted(p for p in root.glob(".github/workflows/*") if p.suffix in WORKFLOW_SUFFIXES),
        *action_paths(root),
        *dockerfile_paths(root),
        *script_paths(root),
        root / DEPENDABOT_FILE,
        root / POLICY_FILE,
        root / ".nvmrc",
        root / "package.json",
        root / "package-lock.json",
        *image_manifest_paths(root),
    ]


def image_manifest_paths(root: Path) -> list[Path]:
    """Each image's npm manifest under deploy/docker/, with the lock beside it."""
    found: list[Path] = []
    for manifest in sorted((root / IMAGE_FOLDER).glob("**/package.json")):
        if "node_modules" not in manifest.parts:
            found += [manifest, manifest.with_name("package-lock.json")]
    return found


def read_tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") if p.is_file() else ""
        for p in tree_paths(root)
    }


# ── readers ──────────────────────────────────────────────────────────────────────────────────


def _parse(text: str) -> dict[str, Any]:
    """One workflow's YAML, parsed once per text: every rule reads every workflow. Callers read
    it and never change it. One that does not parse reads as empty here, and ``parse_problem``
    reports it, so no rule passes it in silence."""
    data = _load(text)
    return data if isinstance(data, dict) else {}


class _Loader(_LOADER):  # type: ignore[misc, valid-type]
    """The safe loader, reading a bare ``=`` as the string GitHub reads."""


_Loader.add_constructor(
    "tag:yaml.org,2002:value", lambda loader, node: loader.construct_scalar(node)
)


@functools.lru_cache(maxsize=512)
def _load(text: str) -> object:
    try:
        return yaml.load(text, Loader=_Loader)  # noqa: S506 - a safe loader
    except (yaml.YAMLError, ValueError) as error:
        return _Unreadable(str(error).splitlines()[0] if str(error) else type(error).__name__)


@dataclass(frozen=True)
class _Unreadable:
    error: str


def parse_problem(text: str) -> str | None:
    """Why a workflow or action file cannot be read as a mapping, or None."""
    data = _load(text)
    if isinstance(data, _Unreadable):
        return f"does not parse as YAML: {data.error}"
    if not isinstance(data, dict):
        return "is not a YAML mapping"
    jobs = data.get("jobs")
    if jobs is not None and not isinstance(jobs, dict):
        return "has a `jobs:` that is not a mapping"
    return None


def workflow_yaml(tree: Tree) -> list[Violation]:
    """Every workflow and local action file parses, so the rules that read the parsed YAML have
    read it. Not waivable: an unread file holds every rule's violations at once."""
    bad: list[Violation] = []
    for rel, text in sorted(tree.items()):
        if is_workflow(rel) or is_action_file(rel):
            problem = parse_problem(text)
            if problem:
                bad.append(Violation("workflow-yaml", rel, "yaml", f"{rel} {problem}"))
    return bad


def _workflows(tree: Tree) -> Iterator[tuple[str, dict[str, Any]]]:
    for rel, text in sorted(tree.items()):
        if is_workflow(rel):
            yield rel, _parse(text)


def _jobs(workflow: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for job_id, job in (workflow.get("jobs") or {}).items():
        if isinstance(job, dict):
            yield str(job_id), job


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in job.get("steps") or [] if isinstance(s, dict)]


def is_action_file(rel: str) -> bool:
    return rel.rsplit("/", 1)[-1] in ("action.yml", "action.yaml")


def _actions(tree: Tree) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every local action's ``action.yml``, parsed."""
    for rel, text in sorted(tree.items()):
        if is_action_file(rel):
            yield rel, _parse(text)


def _step_lists(tree: Tree) -> Iterator[tuple[str, str, dict[str, Any], list[dict[str, Any]]]]:
    """Every list of steps CI runs, as (file, job id, job, steps): each workflow job's, and each
    local composite action's, whose steps run inside the job that calls it. An action's job id
    is ``action`` and its job is empty."""
    for rel, workflow in _workflows(tree):
        for job_id, job in _jobs(workflow):
            yield rel, job_id, job, _steps(job)
    for rel, data in _actions(tree):
        runs = data.get("runs")
        if isinstance(runs, dict):
            yield rel, "action", {}, _steps(runs)


def action(step: dict[str, Any]) -> str:
    return str(step.get("uses", "")).split("@", 1)[0]


def step_name(step: dict[str, Any]) -> str:
    """A step's name, or its action without the ref when it has none."""
    return str(step.get("name") or action(step) or "run")


def _comment_above(lines: list[str], index: int) -> str:
    """The run of ``#`` comment lines directly above ``lines[index]``, without the markers."""
    block: list[str] = []
    i = index - 1
    while i >= 0 and lines[i].lstrip().startswith("#"):
        block.insert(0, lines[i].lstrip().lstrip("#").strip())
        i -= 1
    return " ".join(block)


def _dockerfiles(tree: Tree) -> Iterator[tuple[str, str]]:
    for rel, text in sorted(tree.items()):
        if is_dockerfile(rel.rsplit("/", 1)[-1]):
            yield rel, text


def _logical(text: str) -> list[tuple[int, str]]:
    """Shell or Dockerfile lines with their continuations joined, comments dropped."""
    out: list[tuple[int, str]] = []
    pending, start = "", 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if not pending:
            start = number
        if line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        out.append((start, pending + line))
        pending = ""
    if pending:
        out.append((start, pending))
    return out


# ── pins ─────────────────────────────────────────────────────────────────────────────────────────


def _named_tag(ref: str) -> str:
    """The ``name:tag`` an image reference pins: ``python:3.13.16-slim`` for
    ``mirror.gcr.io/library/python:3.13.16-slim@sha256:...``."""
    return ref.split("@", 1)[0].rsplit("/", 1)[-1]


def _judge_image(
    bad: list[Violation], rel: str, ref: str, comment: str, platforms: tuple[str, ...]
) -> None:
    """One image reference against the digest rule. *platforms* empty means one platform's
    manifest is pinned, and the comment must name it and the index it belongs to; otherwise the
    index is pinned and the comment lists each platform's own manifest digest. Either way the
    comment names the tag it pins, so a tag moved without its comment fails."""
    where = ref

    def fail(message: str) -> None:
        bad.append(Violation("image-digest", rel, where, f"{ref} {message}"))

    if "${{" in ref:
        fail("is an expression, so the image it names cannot be read")
        return
    pinned = _DIGEST.fullmatch(ref)
    if not pinned:
        fail("is not a tag pinned by digest")
        return
    if ref.split("@", 1)[0].rsplit(":", 1)[1] == "latest":
        fail("names the moving tag `latest`")
    if _named_tag(ref) not in comment:
        fail(f"has no comment above it that names {_named_tag(ref)}, the tag it pins")
    if platforms:
        listed = dict(re.findall(r"\b(linux/[\w/]+)\s+sha256:([0-9a-f]{64})\b", comment))
        for platform in platforms:
            digest = listed.get(platform)
            if digest is None:
                fail(f"does not say its {platform} digest")
            elif digest == pinned.group(1):
                fail(f"lists the index it pins as its {platform} digest")
        given = [listed[p] for p in platforms if p in listed]
        if len(set(given)) < len(given):
            fail("lists one digest for two platforms")
        return
    if not re.search(r"\blinux/amd64\b", comment):
        fail("does not say it is the linux/amd64 manifest")
    index = re.search(r"\bindex\s+sha256:([0-9a-f]{64})", comment)
    if not index:
        fail("does not name the index it belongs to")
    elif index.group(1) == pinned.group(1):
        fail("pins the index its comment names, not its linux/amd64 manifest")


def _uses_lines(text: str) -> Iterator[tuple[int, str, str]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"\s*(?:-\s+)?uses:\s*(['\"]?)(\S+?)\1(\s.*)?$", line)
        if match:
            yield index, match.group(2), match.group(3) or ""


def _local_target(tree: Tree, ref: str) -> bool:
    """Whether a ``uses: ./<path>`` names a file the tree holds: a reusable workflow, or a
    folder with an ``action.yml``."""
    path = ref.removeprefix("./").split("@", 1)[0].rstrip("/")
    if path.endswith(WORKFLOW_SUFFIXES):
        return path in tree
    return f"{path}/action.yml" in tree or f"{path}/action.yaml" in tree


def action_sha(tree: Tree) -> list[Violation]:
    bad: list[Violation] = []
    found = 0
    for rel, text in sorted(tree.items()):
        if not (is_workflow(rel) or is_action_file(rel)):
            continue
        lines = text.splitlines()
        for index, ref, rest in _uses_lines(text):
            if ref.startswith("./"):
                if not _local_target(tree, ref):
                    message = f"{ref} names a local action or workflow the check cannot read"
                    bad.append(Violation("action-sha", rel, ref, message))
                continue
            found += 1
            if ref.startswith("docker://"):
                comment = _comment_above(lines, index) + "\n" + rest
                _judge_image(bad, rel, ref.removeprefix("docker://"), comment, ())
                continue
            pin = ref.partition("@")[2]
            release = re.fullmatch(r"\s+#\s*(\S+)\s*", rest)
            if not _SHA.fullmatch(pin):
                bad.append(Violation("action-sha", rel, ref, f"{ref} is not pinned by commit SHA"))
            elif not release or not _RELEASE.fullmatch(release.group(1)):
                bad.append(
                    Violation("action-sha", rel, ref, f"{ref} has no exact `# vX.Y.Z` comment")
                )
        read = {ref for _, ref, _ in _uses_lines(text)}
        data = _parse(text)
        held = [str(job["uses"]) for _, job in _jobs(data) if "uses" in job]
        runs = data.get("runs")
        lists = [_steps(job) for _, job in _jobs(data)]
        lists += [_steps(runs)] if isinstance(runs, dict) else []
        held += [str(s["uses"]) for steps in lists for s in steps if "uses" in s]
        for ref in held:
            if ref not in read:
                message = (
                    f"`uses: {ref}` is not written as `uses: owner/repo@<sha> # vX.Y.Z` on "
                    "one line, so its pin cannot be read"
                )
                bad.append(Violation("action-sha", rel, ref, message))
    if not found:
        bad.append(Violation("action-sha", ".github/workflows", "", "read no `uses:` line"))
    return bad


def _global_args(lines: list[str]) -> dict[str, tuple[str | None, int]]:
    """The global ARGs, declared before the first FROM: the only ones a FROM reads. The last
    declaration of a name wins, as Docker reads them in order."""
    found: dict[str, tuple[str | None, int]] = {}
    for index, line in enumerate(lines):
        words = line.split()
        if words and words[0].upper() == "FROM":
            break
        arg = re.match(r"\s*ARG\s+(\w+)(?:=(\S*))?\s*$", line, flags=re.I)
        if arg:
            found[arg.group(1)] = (arg.group(2).strip("\"'") if arg.group(2) else None, index)
    return found


def _arg_defaults(lines: list[str]) -> dict[str, str]:
    """Every ARG default of a Dockerfile, for a ``RUN`` that reads one."""
    found: dict[str, str] = {}
    for line in lines:
        arg = re.match(r"\s*ARG\s+(\w+)=(\S+)\s*$", line, flags=re.I)
        if arg:
            found[arg.group(1)] = arg.group(2).strip("\"'")
    return found


def _resolve_arg(
    ref: str, args: dict[str, tuple[str | None, int]], at: int
) -> tuple[str | None, int]:
    """*ref* with a global ``${NAME}`` or ``$NAME`` replaced by its default, and the line the
    default is on; None when the ARG has no default."""
    variable = re.fullmatch(r"\$\{?(\w+)\}?", ref)
    if not variable:
        return ref, at
    return args.get(variable.group(1), (None, at))


#: The project's own images, which the jobs that pull them by tag are there to test.
OWN_IMAGES = re.compile(r"ghcr\.io/personalclaw/")
_TWO_PLATFORMS = ("linux/amd64", "linux/arm64")
#: `docker run` options whose next word is their value.
_DOCKER_VALUED = set(
    "--platform --entrypoint --name -v --volume -e --env --env-file -p --publish -w --workdir "
    "--network --net -u --user --mount -l --label --cpus -m --memory -h --hostname --add-host "
    "--pull --restart --cidfile --log-driver --log-opt --tmpfs --device --cap-add --cap-drop "
    "--security-opt --ulimit --shm-size --gpus --runtime --stop-signal --health-cmd".split()
)


def base_args(tree: Tree) -> set[str]:
    """The ARG names a Dockerfile's FROM reads, so a build that overrides one is refused."""
    found: set[str] = set()
    for _rel, text in _dockerfiles(tree):
        for line in text.splitlines():
            words = [w for w in line.split() if not w.startswith("--")]
            if len(words) > 1 and words[0].upper() == "FROM":
                variable = re.fullmatch(r"\$\{?(\w+)\}?", words[1])
                if variable:
                    found.add(variable.group(1))
    return found


def _dockerfile_images(bad: list[Violation], rel: str, text: str) -> tuple[int, set[str]]:
    """Judge every image a Dockerfile fetches: each FROM, each ``COPY``/``ADD --from=`` and each
    ``RUN --mount=...,from=`` that names an image rather than a stage. Returns how many it
    judged, and the FROM references it holds (for a build elsewhere that copies one)."""
    lines = text.splitlines()
    args = _global_args(lines)
    stages: set[str] = set()
    refs: set[str] = set()
    judged = 0

    def judge(ref: str, at: int, what: str) -> None:
        nonlocal judged
        resolved, line_at = _resolve_arg(ref, args, at)
        if resolved is None:
            bad.append(
                Violation("image-digest", rel, ref, f"{what} {ref} reads an ARG with no default")
            )
            return
        if resolved == "scratch" or resolved.lower() in stages or resolved.isdigit():
            return
        judged += 1
        refs.add(resolved)
        _judge_image(bad, rel, resolved, _comment_above(lines, line_at), _TWO_PLATFORMS)

    for index, line in enumerate(lines):
        words = line.split()
        if not words:
            continue
        keyword = words[0].upper()
        if keyword == "FROM":
            plain = [w for w in words[1:] if not w.startswith("--")]
            if not plain:
                continue
            name = plain[2].lower() if len(plain) > 2 and plain[1].upper() == "AS" else ""
            judge(plain[0], index, "FROM")
            stages.add(name)
        elif keyword in ("COPY", "ADD"):
            for word in words[1:]:
                if word.startswith("--from="):
                    judge(word.split("=", 1)[1], index, f"{keyword} --from")
        elif keyword == "RUN":
            for word in words[1:]:
                if not word.startswith("--mount="):
                    if not word.startswith("--"):
                        break
                    continue
                for option in word.split("=", 1)[1].split(","):
                    if option.startswith("from="):
                        judge(option.split("=", 1)[1], index, "RUN --mount from")
    return judged, refs


def _run_images(
    bad: list[Violation],
    rel: str,
    run: str,
    env: dict[str, str],
    built: set[str],
    dockerfile_refs: set[str],
    overridable: set[str],
) -> int:
    """Judge the images one ``run:`` script fetches: ``docker run|pull|create``, the FROM lines of
    a build it feeds on stdin, and a ``--build-arg`` that overrides a base ARG. *built* gathers
    the tags the job builds. Returns how many images it judged."""
    script = shell_installs.read_script(run)
    raw = run.splitlines()
    judged = 0
    for number, line in script.lines:
        for commands in shell_installs.pipelines(line):
            for words in commands:
                tool = words[0].rsplit("/", 1)[-1]
                if tool not in ("docker", "podman", "nerdctl"):
                    continue
                verb = words[1:2]
                if verb == ["buildx"]:
                    words, verb = [tool, *words[2:]], words[2:3]
                if verb == ["build"]:
                    for i, word in enumerate(words):
                        if word in ("-t", "--tag") and i + 1 < len(words):
                            built.add(words[i + 1])
                        value = (
                            word.split("=", 1)[1]
                            if word.startswith("--build-arg=")
                            else (
                                words[i + 1] if word == "--build-arg" and i + 1 < len(words) else ""
                            )
                        )
                        name = value.split("=", 1)[0]
                        if name and name in overridable:
                            message = (
                                f"overrides the base ARG {name} at build time; change the ARG's "
                                "default, where its platform digests are commented"
                            )
                            bad.append(Violation("image-digest", rel, name, message))
                    continue
                if verb not in (["run"], ["pull"], ["create"]):
                    continue
                image, skip = "", False
                for word in words[2:]:
                    if skip:
                        skip = False
                    elif word.startswith("-"):
                        skip = word in _DOCKER_VALUED
                    else:
                        image = word
                        break
                if not image:
                    continue
                value = re.sub(
                    r"\$\{?([A-Za-z_]\w*)\}?", lambda m: env.get(m.group(1), m.group(0)), image
                )
                if OWN_IMAGES.match(value) or value in built:
                    continue
                if "$" in value:
                    message = f"{image} names an image through a variable the check cannot read"
                    bad.append(Violation("image-digest", rel, image, message))
                    continue
                judged += 1
                _judge_image(bad, rel, value, _comment_above(raw, number - 1), ())
    for command, body in script.data:
        if not re.search(r"\b(?:docker|podman)\s+(?:buildx\s+)?build\b", command):
            continue
        platforms = re.search(r"--platform[= ](\S+)", command)
        wanted = tuple(platforms.group(1).split(",")) if platforms else ()
        for index, text in enumerate(body):
            words = text.split()
            if not (words and words[0].upper() == "FROM" and len(words) > 1):
                continue
            ref = [w for w in words[1:] if not w.startswith("--")][0]
            judged += 1
            if ref in dockerfile_refs:
                continue  # a base a checked-in Dockerfile pins, and is judged there
            _judge_image(bad, rel, ref, _comment_above(body, index), wanted)
    return judged


def image_digest(tree: Tree) -> list[Violation]:
    bad: list[Violation] = []
    found = 0
    dockerfile_refs: set[str] = set()
    for rel, text in _dockerfiles(tree):
        judged, refs = _dockerfile_images(bad, rel, text)
        found += judged
        dockerfile_refs |= refs
    overridable = base_args(tree)
    for rel, text in sorted(tree.items()):
        if not (is_workflow(rel) or is_action_file(rel)):
            continue
        lines = text.splitlines()
        written: set[str] = set()
        for index, line in enumerate(lines):
            match = re.match(
                r"\s+(?:-\s+)?(?:container|image):\s*(\S.*?)\s*(?:\s#.*)?$", line
            ) or re.match(r"\s+(?:driver-opts:\s*)?image=(\S+)\s*$", line)
            if not match:
                continue
            value = match.group(1).strip("\"'")
            written.add(value)
            if value.startswith("docker://"):
                continue  # a Docker action's image, judged with the action below
            found += 1
            _judge_image(bad, rel, value, _comment_above(lines, index), ())
        data = _parse(text)
        held: list[object] = []
        for _job_id, job in _jobs(data) if is_workflow(rel) else []:
            container = job.get("container")
            held.append(container.get("image") if isinstance(container, dict) else container)
            services = job.get("services")
            for service in services.values() if isinstance(services, dict) else []:
                held.append(service.get("image") if isinstance(service, dict) else service)
        for image in held:
            if image is not None and str(image) not in written:
                message = (
                    f"{image} is not written on a `container:` or `image:` line of its own, so "
                    "its pin cannot be read"
                )
                bad.append(Violation("image-digest", rel, str(image), message))
    for rel, data in _actions(tree):
        runs = data.get("runs")
        image = str(runs.get("image", "")) if isinstance(runs, dict) else ""
        if image.startswith("docker://"):
            found += 1
            lines = tree[rel].splitlines()
            at = next((i for i, line in enumerate(lines) if "docker://" in line), 0)
            _judge_image(bad, rel, image.removeprefix("docker://"), _comment_above(lines, at), ())
    workflows = dict(_workflows(tree))
    for rel, _job_id, job, steps in _step_lists(tree):
        built: set[str] = set()
        top = workflows.get(rel, {}).get("env")
        for step in steps:
            env: dict[str, str] = {}
            for scope in (top, job.get("env"), step.get("env")):
                if isinstance(scope, dict):
                    env.update({str(k): str(v) for k, v in scope.items()})
            run = str(step.get("run", ""))
            found += _run_images(bad, rel, run, env, built, dockerfile_refs, overridable)
            inputs = step.get("with")
            if isinstance(inputs, dict) and action(step) in (
                "docker/build-push-action",
                "docker/bake-action",
            ):
                for pair in re.split(r"[\n,]", str(inputs.get("build-args", ""))):
                    name = pair.strip().split("=", 1)[0]
                    if name in overridable:
                        message = f"overrides the base ARG {name} through build-args"
                        bad.append(Violation("image-digest", rel, name, message))
    if not found:
        bad.append(Violation("image-digest", ".", "", "read no container image"))
    return bad


# ── installs a run: or RUN performs ─────────────────────────────────────────────────────────

#: The project's own installer, piped into sh on purpose by the one step that tests it, from the
#: one script that step runs, and from nowhere else: not that step's own text, not another step
#: that runs the same script, not another script the step runs.
OWN_INSTALLER = "https://personalclaw.dev/install"
OWN_INSTALLER_STEP = (
    ".github/workflows/breadth.yml",
    "install-smoke",
    "smoke — the SERVED one-liner (https://personalclaw.dev/install)",
)
OWN_INSTALLER_SCRIPT = "tools/ci/install_smoke_served.sh"

#: The public installer the staged smoke runs: the artifact under test, not CI's toolchain. It
#: bootstraps uv from Astral's installer and installs this project at a floor, both on purpose
#: (its header says why). Reached through the staged step's own script, and only there, these
#: exact lines are its subject; every other line of it is read as any script is, and the uv
#: bootstrap holds only while the file still names Astral's installer.
STAGED_INSTALLER_STEP = (
    ".github/workflows/breadth.yml",
    "install-smoke",
    "smoke — the STAGED installer (deploy/website/install.sh)",
)
STAGED_INSTALLER_SCRIPT = "tools/ci/install_smoke_staged.sh"
STAGED_INSTALLER = "deploy/website/install.sh"
STAGED_INSTALLER_URL = 'UV_INSTALLER_URL="https://astral.sh/uv/install.sh"'
STAGED_INSTALLER_LINES = frozenset(
    {
        'curl -fsSL "$UV_INSTALLER_URL" | sh',
        'wget -qO- "$UV_INSTALLER_URL" | sh',
        'uv tool install --upgrade --python "$PC_PYTHON" "$PC_PACKAGE>=$PC_MIN_VERSION"',
    }
)

#: The suffixes of a repository shell script, in any case. A file with no suffix is one when its
#: first line opens a shell (a git hook's ``#!/bin/sh``).
SCRIPT_SUFFIXES = (".sh", ".bash")
_SHELL_SHEBANG = re.compile(r"#!\s*\S*/(?:env\s+(?:-\S+\s+)*)?(?:ba|z|da|k)?sh\b")
#: How a step names the checkout's root before a path: the runner's own name for it.
_ROOT_PREFIXES = ("$GITHUB_WORKSPACE/", "${GITHUB_WORKSPACE}/")
#: How deep a script that runs a script is followed; deeper is a violation, never a skip.
_SCRIPT_DEPTH = 4
#: Commands that name a script without running it.
_READERS = frozenset(
    {"shellcheck", "sha256sum", "shasum", "chmod", "test", "[", "ls", "git", "echo", "printf"}
    | {"curl", "wget"}  # what one writes and then runs is a download's, judged as one
)
#: Interpreters other than a shell, and their options whose next word is code they run.
_CODE_OPTIONS = (
    (re.compile(r"python\d*(?:\.\d+)?"), ("-c",)),
    (re.compile(r"perl|ruby"), ("-e", "-E")),
    (re.compile(r"node"), ("-e", "--eval", "-p", "--print")),
)
#: ``uv run``'s options whose next word is their value.
_UV_RUN_VALUED = frozenset(
    "--with --with-editable --with-requirements --extra --group --only-group --no-group --package"
    " --env-file --python -p --project --directory --index --default-index --index-url -i"
    " --extra-index-url --find-links -f --index-strategy --keyring-provider --resolution"
    " --prerelease --exclude-newer --link-mode --config-setting -C --cache-dir --config-file"
    " --color --allow-insecure-host --python-platform --upgrade-package -P --reinstall-package"
    " --refresh-package --no-binary-package --no-build-package".split()
)


def is_script_name(rel: str) -> bool:
    """Whether a path's name says it is a shell script."""
    return rel.lower().endswith(SCRIPT_SUFFIXES)


def is_script(rel: str, text: str) -> bool:
    """Whether the repository file *rel*, holding *text*, is a shell script: by its suffix, or,
    with no suffix, by a first line that opens a shell."""
    name = rel.rsplit("/", 1)[-1]
    return is_script_name(name) or ("." not in name and bool(_SHELL_SHEBANG.match(text)))


def _uv_run(words: list[str]) -> list[str]:
    """*words* with a leading ``uv run`` and its options taken off, so ``uv run sh x.sh`` is
    read as ``sh x.sh``."""
    if words[:2] != ["uv", "run"]:
        return words
    rest = words[2:]
    while rest and rest[0].startswith("-"):
        flag = rest[0]
        rest = rest[1 + (flag in _UV_RUN_VALUED and "=" not in flag) :]
    return shell_installs.unwrap(rest)


def _locate(word: str, downloaded: Collection[str]) -> tuple[str, str]:
    """Where the path *word* points, as (kind, path): ``downloaded`` (a file a download in the
    same text wrote), ``variable`` (a name the check cannot read), ``outside`` (an absolute path,
    a home path, or one above the checkout) or ``repository`` with the path in normal form."""
    if word in downloaded:
        return "downloaded", word
    path = word
    for prefix in _ROOT_PREFIXES:
        if path.startswith(prefix):
            path = path[len(prefix) :]
            break
    if "$" in path or "`" in path or path == "<stdin>":
        return "variable", word
    if path.startswith(("/", "~")):
        return "outside", word
    path = posixpath.normpath(path)
    if path == ".." or path.startswith("../"):
        return "outside", word
    return "repository", path


def _downloads(lines: list[tuple[int, str]]) -> set[str]:
    """The files a ``curl`` or ``wget`` of these lines writes (``-o``, ``-O``, ``--output``,
    ``--output-document``, joined to the option, at the end of a cluster such as ``-qO``, or a
    redirection), as the words name them."""
    found: set[str] = set()
    valued = {"curl": ("o", "--output"), "wget": ("O", "--output-document")}
    for _, line in lines:
        for commands in shell_installs.pipelines(line):
            for words in commands:
                option = valued.get(words[0].rsplit("/", 1)[-1])
                if option is None:
                    continue
                letter, long = option
                for i, word in enumerate(words[1:], 1):
                    after = words[i + 1] if i + 1 < len(words) else ""
                    if word in (long, ">", ">>"):
                        found.add(after)
                    elif word.startswith(f"{long}="):
                        found.add(word.split("=", 1)[1])
                    elif word.startswith(">"):
                        found.add(word.lstrip(">"))
                    elif word.startswith("-") and not word.startswith("--") and letter in word:
                        rest = word[word.index(letter) + 1 :]
                        found.add(rest or after)
    found.discard("-")
    found.discard("")
    return found


def _named_in(code: str, scripts: Collection[str]) -> list[str]:
    """The repository scripts the code *code* names: a script of the tree, or a ``.sh`` or
    ``.bash`` path, anywhere in it."""
    named = {p for p in scripts if p in code}
    named |= set(re.findall(r"[\w./-]+\.(?:sh|bash)\b", code))
    return sorted(named)


def _names_a_script(word: str, scripts: Collection[str]) -> str | None:
    """The repository script *word* names, in normal form, or None. A URL names none."""
    if "://" in word:
        return None
    kind, path = _locate(word, ())
    if kind != "repository" or not path or path == ".":
        return None
    return path if path in scripts or is_script_name(path) else None


def _command_runs(
    words: list[str], *, fed: str | None, scripts: Collection[str], downloaded: Collection[str]
) -> tuple[list[str], list[str]]:
    """The repository scripts the one command *words* runs as ``sh <path>``, which the check
    reads, and the problems of every other way it runs one, which are never skipped. *fed* is
    what its standard input is piped from: None, ``download`` (a ``curl`` or ``wget``, which the
    download rule judges) or ``pipe`` (anything else). *scripts* are the tree's shell scripts and
    *downloaded* the files a download in the same text wrote."""
    words = _uv_run(words)
    if not words:
        return [], []
    head = words[0].rsplit("/", 1)[-1]
    reads: list[str] = []
    found: list[str] = []
    placed: set[int] = {0}
    call = shell_installs.shell_call(words)
    if words[0] in (".", "source") and len(words) > 1:
        placed.add(1)
        kind, path = _locate(words[1], downloaded)
        if kind == "downloaded":
            found.append(f"sources {path}, a file a download wrote")
        elif kind == "variable":
            found.append(f"sources {path}, a script the check cannot locate")
        elif kind == "repository":
            found.append(f"sources {path} with `{words[0]}`, not as `sh {path}`")
        elif posixpath.normpath(path).startswith("../"):
            found.append(f"sources {path}, a file outside the repository the check cannot read")
    elif call is not None:
        placed.update(i for i, w in enumerate(words) if i and w == call.command)
        if call.operand_at is not None:
            placed.add(call.operand_at)
        if call.reads_only or call.command is not None:
            pass  # it runs nothing of a file; a -c script is read as lines of its own
        elif call.operand is not None:
            kind, path = _locate(call.operand, downloaded)
            shown = " ".join([head, *call.options])
            if kind == "downloaded":
                found.append(f"runs {path}, a file a download wrote")
            elif kind == "variable":
                found.append(f"runs {path}, a script the check cannot locate")
            elif kind == "outside":
                found.append(f"runs {path}, a file outside the repository the check cannot read")
            elif shown != "sh":
                found.append(f"runs {path} with `{shown}`, not as `sh {path}`")
            else:
                reads.append(path)
        elif call.stdin is not None and call.stdin[0] == "<":
            if call.stdin[1] != "/dev/null":
                what = call.stdin[1] or "a substitution"
                found.append(f"feeds {what} to {head} on stdin, not as `sh <path>`")
        elif call.stdin is not None and call.stdin[0] == "<<<":
            found.append(f"feeds a here-string to {head}, which the check does not read")
        elif call.stdin is None and fed == "pipe":
            found.append(f"runs what a pipe feeds {head}, which the check does not read")
    else:
        kind, path = _locate(words[0], downloaded)
        if kind == "downloaded":
            found.append(f"runs {path}, a file a download wrote")
        elif kind == "repository" and (path in scripts or is_script_name(path)):
            found.append(f"runs {path} as a program, not as `sh {path}`")
        elif kind == "variable" and is_script_name(path):
            found.append(f"runs {path}, a script the check cannot locate")
        elif kind == "outside" and is_script_name(path):
            found.append(f"runs {path}, a file outside the repository the check cannot read")
        for pattern, options in _CODE_OPTIONS:
            if not pattern.fullmatch(head):
                continue
            for i, word in enumerate(words[1:-1], 1):
                if word not in options:
                    continue
                placed.add(i + 1)
                found += [
                    f"names {p} in {head}'s {word} code, which the check does not read"
                    for p in _named_in(words[i + 1], scripts)
                ]
    if head not in _READERS and not (call is not None and call.reads_only):
        for i, word in enumerate(words):
            redirect = shell_installs.REDIRECT.fullmatch(word)
            if redirect and (word[0] in "<>&" or word[0].isdigit()):
                placed.update((i, i + 1) if not redirect.group(2) else (i,))
            named_path = None if i in placed else _names_a_script(word, scripts)
            if named_path is not None:
                found.append(f"names {named_path} in a `{head}` command the check does not read")
    return reads, found


def _text_runs(
    lines: list[tuple[int, str]],
    scripts: Collection[str],
    data: Collection[tuple[str, list[str]]] = (),
) -> tuple[list[str], list[str]]:
    """The repository scripts these shell lines run as ``sh <path>``, in order, and the problems
    of every other way they run one. *data* are the heredocs something other than a shell reads:
    one an interpreter reads is code, and a repository script it names is a problem too."""
    downloaded = _downloads(lines)
    reads: list[str] = []
    found: list[str] = []
    for line, body in data:
        for commands in shell_installs.pipelines(line):
            for words in commands:
                head = words[0].rsplit("/", 1)[-1]
                if any(w.startswith("<<") for w in words) and any(
                    pattern.fullmatch(head) for pattern, _ in _CODE_OPTIONS
                ):
                    found += [
                        f"names {p} in a heredoc {head} runs, which the check does not read"
                        for p in _named_in("\n".join(body), scripts)
                    ]
    for _, line in lines:
        for commands in shell_installs.pipelines(line):
            for at, words in enumerate(commands):
                upstream = {c[0].rsplit("/", 1)[-1] for c in commands[:at]}
                fed = None if not at else "download" if {"curl", "wget"} & upstream else "pipe"
                more, problems = _command_runs(
                    words, fed=fed, scripts=scripts, downloaded=downloaded
                )
                reads += more
                found += problems
    return reads, found


def tree_scripts(tree: Tree) -> frozenset[str]:
    """The shell scripts of *tree*."""
    return frozenset(rel for rel, text in tree.items() if is_script(rel, text))


def scripts_run(
    read: Callable[[str], str | None], text: str, depth: int = _SCRIPT_DEPTH
) -> list[tuple[str, str | None]]:
    """Each repository script the shell *text* runs as ``sh <path>``, the one way the check
    reads one, and each script those run in turn to *depth*, depth first, as (path, text). *read*
    gives a path's text, or None when there is no such file: that path is returned with None, so
    a caller refuses it rather than reading nothing."""
    out: list[tuple[str, str | None]] = []

    def visit(body: str, chain: tuple[str, ...]) -> None:
        reads, _ = _text_runs(shell_installs.read_script(body).lines, ())
        for path in reads:
            if path in chain or len(chain) >= depth:
                continue
            content = read(path)
            out.append((path, content))
            if content is not None:
                visit(content, (*chain, path))

    visit(text, ())
    return out


def _scripts_problems(
    tree: Tree,
    text: str,
    *,
    where: tuple[str, str, str],
    installs: Collection[str],
    have: set[str],
    values: dict[str, str],
    locked: Mapping[str, str],
    scripts: Collection[str],
    chain: tuple[str, ...] = (),
) -> list[str]:
    """The problems of the shell *text* a workflow step runs (*chain* empty) or of the
    repository script at the end of *chain*, the scripts the step ran to reach it, and of each
    repository script that text runs in turn. A script is read when it is run as ``sh <path>``;
    every other way of running one (another shell, an option, ``.`` or ``source``, a bare path,
    a pipe or a redirection into a shell, code an interpreter is handed) is a problem, as are a
    script the tree does not hold, one named through a variable, one a download wrote, and one
    deeper than :data:`_SCRIPT_DEPTH` or already in *chain*. None is skipped. *where* is the
    step (file, job id, name)."""
    served = where == OWN_INSTALLER_STEP and chain == (OWN_INSTALLER_SCRIPT,)
    own = OWN_INSTALLER if served else None
    artifact = where == STAGED_INSTALLER_STEP and chain == (
        STAGED_INSTALLER_SCRIPT,
        STAGED_INSTALLER,
    )
    read = shell_installs.read_script(text)
    lines = read.lines
    found: list[str] = []
    if artifact:
        if not any(line == STAGED_INSTALLER_URL for _, line in lines):
            found.append(f"no longer names Astral's uv installer ({STAGED_INSTALLER_URL})")
        lines = [(n, line) for n, line in lines if line not in STAGED_INSTALLER_LINES]
    found += _script_problems(
        shell_installs.Script(lines, read.data), installs, have, True, values, own, locked
    )
    reads, problems = _text_runs(read.lines, scripts, read.data)
    found += problems
    for path in reads:
        if path not in tree:
            found.append(f"runs {path}, which the tree does not hold")
            continue
        if path in chain or len(chain) + 1 > _SCRIPT_DEPTH:
            found.append(f"runs {path} past the depth the check follows")
            continue
        inner = _scripts_problems(
            tree,
            tree[path],
            where=where,
            installs=installs,
            have=have,
            values=values,
            locked=locked,
            scripts=scripts,
            chain=(*chain, path),
        )
        found += [f"{path}: {problem}" for problem in inner]
    return found


def _literal_env(*scopes: object) -> dict[str, str]:
    """The literal variables of *scopes* (workflow, job, step ``env:``), inner ones last."""
    values: dict[str, str] = {}
    for scope in scopes:
        if isinstance(scope, dict):
            for key, value in scope.items():
                if isinstance(value, (str, int, float)) and "${{" not in str(value):
                    values[str(key)] = str(value)
    return values


def _npm_ci(words: list[str]) -> bool:
    """Whether a command is `npm ci` of the lock where it runs, not of one under --prefix."""
    return words[:2] == ["npm", "ci"] and not any(w.startswith("--prefix") for w in words)


def _script_problems(
    script: shell_installs.Script,
    installs: Collection[str],
    have: set[str],
    in_workflow: bool,
    values: dict[str, str],
    own: str | None,
    locked: Mapping[str, str],
) -> list[str]:
    """Each install or download problem of *script*, in order. *installs* is what an `npm ci`
    here installs: the packages and binaries of the locks where it runs. *have* is what the
    caller's job (or stage) has installed by now, and grows with each `npm ci`. *locked* is each
    tool an image lock installs, which no other install may fetch."""
    found: list[str] = []
    for _, line in script.lines:
        found += shell_installs.download_problems(line, own)
        for commands in shell_installs.pipelines(line):
            for words in commands:
                if _npm_ci(words):
                    have |= set(installs)
                found += _outside_the_lock(words, locked)
                problem = shell_installs.command_problem(words, have, in_workflow, values)
                if problem:
                    found.append(problem)
    return found


def _numbered(problems: list[str]) -> list[str]:
    """*problems* made distinct, so an exception names exactly one: a repeat gets ``(2)``."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for problem in problems:
        seen[problem] = seen.get(problem, 0) + 1
        out.append(problem if seen[problem] == 1 else f"{problem} ({seen[problem]})")
    return out


def _run_body(line: str) -> tuple[str | None, str]:
    """A Dockerfile ``RUN`` instruction's shell text with its flags taken off, or None and why
    when it is an exec form the rule cannot read."""
    body = re.sub(r"^RUN\s+(?:--\S+\s+)*", "", line, flags=re.I).strip()
    if not body.startswith("["):
        return body, ""
    try:
        argv = json.loads(body)
    except ValueError:
        return None, "an exec form the rule cannot read"
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return None, "an exec form the rule cannot read"
    shell = shell_installs.SHELLS.fullmatch(argv[0].rsplit("/", 1)[-1])
    if shell and len(argv) >= 3 and argv[1] == "-c":
        return argv[2], ""
    return shlex.join(argv), ""


def _carried_lock(source: str) -> str:
    """The npm lock a ``COPY`` source brings in: the lock itself, or the one at the top of the
    folder it names (``.`` is the build context's root)."""
    path = source.removeprefix("./").rstrip("/")
    if path in ("", "."):
        return "package-lock.json"
    if path == "package-lock.json" or path.endswith("/package-lock.json"):
        return path
    return f"{path}/package-lock.json"


def dockerfile_runs(text: str) -> Iterator[tuple[int, str, str, frozenset[str]]]:
    """Each ``RUN`` of a Dockerfile as (line, stage, shell text, locks): heredocs it runs joined
    in, an unreadable exec form as the text ``\0`` with its reason, and the npm locks its stage
    has copied in from the build context by then (``COPY --from`` copies another stage's file,
    not the context's)."""
    script = shell_installs.read_script(text)
    stage, copied = "", frozenset[str]()
    for number, line in script.lines:
        words = line.split()
        head = words[0].upper() if words else ""
        if head == "FROM":
            named = [w for w in words[1:] if not w.startswith("--")]
            stage = named[2] if len(named) > 2 and named[1].upper() == "AS" else str(number)
            copied = frozenset()
            continue
        if head in ("COPY", "ADD") and not any(w.startswith("--from") for w in words):
            sources = [w for w in words[1:-1] if not w.startswith("--")]
            copied |= {_carried_lock(source) for source in sources}
            continue
        if head != "RUN":
            if head in _INSTRUCTIONS:
                continue
            yield number, stage, line, copied  # a heredoc body line the RUN above runs
            continue
        body, why = _run_body(line)
        yield number, stage, body if body is not None else "\0" + why, copied


#: Dockerfile instructions, so a heredoc body line is told apart from the next instruction.
_INSTRUCTIONS = {
    "FROM",
    "RUN",
    "CMD",
    "LABEL",
    "EXPOSE",
    "ENV",
    "ADD",
    "COPY",
    "ENTRYPOINT",
    "VOLUME",
    "USER",
    "WORKDIR",
    "ARG",
    "ONBUILD",
    "STOPSIGNAL",
    "HEALTHCHECK",
    "SHELL",
    "MAINTAINER",
}


def run_install(tree: Tree) -> list[Violation]:
    bad: list[Violation] = []

    @functools.cache
    def binaries(lock: str) -> frozenset[str]:
        return frozenset(lock_binaries(tree.get(lock, "")))

    locked = _image_locked_names(tree)
    scripts = tree_scripts(tree)
    workflows = dict(_workflows(tree))
    for rel, job_id, job, steps in _step_lists(tree):
        have: set[str] = set()
        top = workflows.get(rel, {}).get("env")
        for step in steps:
            name = step_name(step)
            values = _literal_env(top, job.get("env"), step.get("env"))
            # A step's `npm ci` installs the root lock when it runs from the checkout's root.
            directory = str(step.get("working-directory", "") or "")
            installs = binaries("package-lock.json") if directory in ("", ".", "./") else ()
            found = _scripts_problems(
                tree,
                str(step.get("run", "")),
                where=(rel, job_id, name),
                installs=installs,
                have=have,
                values=values,
                locked=locked,
                scripts=scripts,
            )
            for problem in _numbered(found):
                ref = f"{job_id} {name}: {problem}"
                bad.append(Violation("run-install", rel, ref, ref))
    for rel, text in _dockerfiles(tree):
        args = _arg_defaults(text.splitlines())
        found = []
        stage, have = None, set()
        for number, at_stage, body, copied in dockerfile_runs(text):
            if at_stage != stage:
                stage, have = at_stage, set()
            if body.startswith("\0"):
                found.append(f"RUN at line {number}: {body[1:]}")
                continue
            # A stage's `npm ci` installs the locks that stage has copied in, and no other.
            installs = frozenset[str]().union(*(binaries(lock) for lock in copied))
            script = shell_installs.read_script(body)
            found += _script_problems(script, installs, have, False, args, None, locked)
        for problem in _numbered(found):
            bad.append(Violation("run-install", rel, problem, problem))
    return bad + image_npm_locks(tree)


lock_binaries = shell_installs.lock_binaries


# ── the images' own npm locks ────────────────────────────────────────────────────────────────

#: Where an image's npm tools are declared: a ``package.json`` under this folder, installed by a
#: Dockerfile stage with ``npm ci`` from the ``package-lock.json`` beside it.
IMAGE_FOLDER = "deploy/docker/"
_REGISTRY = "https://registry.npmjs.org/"


def image_manifests(tree: Tree) -> list[str]:
    """Each ``package.json`` under deploy/docker/: the npm tools one image installs."""
    return sorted(
        rel
        for rel in tree
        if rel.startswith(IMAGE_FOLDER)
        and rel.endswith("/package.json")
        and "/node_modules/" not in rel
    )


def _json_object(text: str | None) -> dict[str, Any] | None:
    try:
        data = json.loads(text) if text else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


#: The fields a manifest names packages in. `npm ci --omit=dev` installs optional and peer
#: dependencies too, so an image's manifest names its tools under `dependencies` and no other.
_DEPENDENCY_FIELDS = (
    "dependencies",
    "optionalDependencies",
    "peerDependencies",
    "devDependencies",
)


def _dependencies(entry: object, field: str = "dependencies") -> dict[str, str]:
    deps = entry.get(field) if isinstance(entry, dict) else None
    return {str(k): str(v) for k, v in deps.items()} if isinstance(deps, dict) else {}


def _image_locked_names(tree: Tree) -> dict[str, str]:
    """Each tool an image's manifest names, with the lock beside it that installs it."""
    out: dict[str, str] = {}
    for rel in image_manifests(tree):
        lock = rel.rpartition("/")[0] + "/package-lock.json"
        for name in _dependencies(_json_object(tree.get(rel))):
            out.setdefault(name, lock)
    return out


def _npm_name(target: str) -> str:
    """The package an npm install target names, without its version."""
    if target.startswith("@"):
        return "@" + target[1:].split("@", 1)[0]
    return target.split("@", 1)[0]


def _outside_the_lock(words: list[str], locked: Mapping[str, str]) -> list[str]:
    """Each package an `npm install` of *words* installs that an image lock already locks."""
    if not (words and words[0].rsplit("/", 1)[-1] == "npm"):
        return []
    if words[1:2] not in (["install"], ["i"], ["add"]):
        return []
    targets, _ = shell_installs.operands(words[2:])
    return [
        f"npm install of {t} installs a package {locked[_npm_name(t)]} locks; "
        "install it from that lock with npm ci"
        for t in targets
        if _npm_name(t) in locked
    ]


def _runs_npm_ci(body: str) -> bool:
    for _, line in shell_installs.read_script(body).lines:
        for commands in shell_installs.pipelines(line):
            if any(_npm_ci(words) for words in commands):
                return True
    return False


def _locks_installed(tree: Tree) -> dict[str, set[str]]:
    """Each lock a Dockerfile stage copies in from the build context (itself or its folder)
    and then installs with a later `npm ci` of the same stage, with the Dockerfiles that do."""
    found: dict[str, set[str]] = {}
    for rel, text in _dockerfiles(tree):
        for _, _stage, body, copied in dockerfile_runs(text):
            if copied and _runs_npm_ci(body):
                for lock in copied:
                    found.setdefault(lock, set()).add(rel)
    return found


def image_npm_locks(tree: Tree) -> list[Violation]:
    """An image's npm tools come from a lock: its ``package.json`` pins each one exactly, under
    `dependencies` only, the lock beside it was generated from that file, every package it locks
    is fetched from the npm registry under a sha512 integrity, and a Dockerfile stage installs
    it with `npm ci`."""
    bad: list[Violation] = []

    def fail(rel: str, ref: str, message: str) -> None:
        bad.append(Violation("run-install", rel, ref, message))

    installed = _locks_installed(tree)
    for rel in image_manifests(tree):
        folder = rel.rpartition("/")[0]
        lock_rel = f"{folder}/package-lock.json"
        manifest = _json_object(tree.get(rel))
        if manifest is None:
            fail(rel, "manifest", f"{rel} does not parse as a JSON object")
            continue
        deps = _dependencies(manifest)
        for name, spec in sorted(deps.items()):
            if not _EXACT.fullmatch(spec):
                fail(rel, f"{name} {spec}", f"{rel}: {name} {spec} is not an exact X.Y.Z")
        for field in _DEPENDENCY_FIELDS[1:]:
            if field in manifest:
                message = (
                    f"{rel} declares {field}, which npm ci installs or resolves beside the "
                    "tools; name each tool under `dependencies`, pinned exactly"
                )
                fail(rel, field, message)
        lock = _json_object(tree.get(lock_rel))
        packages = lock.get("packages") if lock else None
        if not isinstance(packages, dict):
            message = (
                f"{rel} has no readable package-lock.json beside it, so no image can install "
                "it with npm ci"
            )
            fail(rel, "no lock", message)
            continue

        def locked_version(name: str) -> object:
            entry = packages.get(f"node_modules/{name}")
            return entry.get("version") if isinstance(entry, dict) else None

        # The same names at the same versions in the manifest, the lock's root and the
        # node_modules entries the lock installs them from.
        drift = {n for n, version in deps.items() if locked_version(n) != version}
        for field in _DEPENDENCY_FIELDS:
            want = _dependencies(manifest, field)
            held = _dependencies(packages.get(""), field)
            drift |= {n for n in set(want) | set(held) if want.get(n) != held.get(n)}
        if drift:
            differ = ", ".join(sorted(drift))
            message = (
                f"{lock_rel} was not generated from its package.json: {differ} differ; "
                f"regenerate it with `npm install --package-lock-only` in {folder}"
            )
            fail(lock_rel, "drift", message)
        for key, entry in sorted(packages.items()):
            if not key:
                continue
            resolved = str(entry.get("resolved", "")) if isinstance(entry, dict) else ""
            integrity = str(entry.get("integrity", "")) if isinstance(entry, dict) else ""
            if not (resolved.startswith(_REGISTRY) and integrity.startswith("sha512-")):
                message = (
                    f"{lock_rel}: {key} is locked without a registry URL and a sha512 integrity"
                )
                fail(lock_rel, key, message)
        if lock_rel not in installed:
            message = (
                f"{lock_rel} is installed by no Dockerfile stage: copy it in from the build "
                "context with its package.json, and run `npm ci` after the copy, in that stage"
            )
            fail(lock_rel, "not installed", message)
    return bad


# ── setup actions ────────────────────────────────────────────────────────────────────────────────

#: Each setup action's version inputs: a step sets one, and a literal one is exact.
SETUP_INPUTS: dict[str, tuple[str, ...]] = {
    "actions/setup-node": ("node-version", "node-version-file"),
    "actions/setup-python": ("python-version", "python-version-file"),
    "astral-sh/setup-uv": ("version", "version-file"),
    "docker/setup-buildx-action": ("version",),
    "docker/setup-qemu-action": ("image",),
}


#: Actions that install a tool and take no version, or only the one their pinned release
#: bundles, with the inputs that would override that version (each must be absent or exact).
INSTALLS_BUNDLED: dict[str, tuple[str, ...]] = {
    "cachix/install-nix-action": ("install_url",),
    "anchore/sbom-action/download-syft": ("syft-version",),
}
#: A name that says the action installs a toolchain, so an unlisted one fails as unknown.
_INSTALLER_NAME = re.compile(r"/(?:setup-|install-)[\w-]+$|/action-setup$|-toolchain$|-installer$")


def setup_floating(tree: Tree) -> list[Violation]:
    bad: list[Violation] = []
    for rel, job_id, _job, steps in _step_lists(tree):
        for step in steps:
            name = action(step)
            if name.startswith("./"):
                continue  # a local action: its own steps are read where it is defined
            if name not in SETUP_INPUTS and name not in INSTALLS_BUNDLED:
                if not _INSTALLER_NAME.search(name):
                    continue
            raw = step.get("with")
            inputs = raw if isinstance(raw, dict) else {}
            ref = f"{job_id} {step_name(step)}"

            def floating(message: str) -> None:
                bad.append(Violation("setup-floating", rel, ref, f"{ref}: {message}"))

            if raw is not None and not isinstance(raw, dict):
                floating(f"{name}'s `with:` is not a mapping, so its version cannot be read")
                continue
            if "check-latest" in inputs and str(inputs["check-latest"]).lower() != "false":
                floating(f"{name} sets check-latest to {inputs['check-latest']}")
            if name in INSTALLS_BUNDLED:
                for key in INSTALLS_BUNDLED[name]:
                    if key in inputs and not _EXACT.search(str(inputs[key])):
                        floating(f"{key} {inputs[key]} names no exact version")
                continue
            wanted = SETUP_INPUTS.get(name)
            if wanted is None:
                floating(
                    f"{name} installs a tool this check does not know: add its version inputs "
                    "to SETUP_INPUTS (or INSTALLS_BUNDLED) in tools/ci/supply_chain.py"
                )
                continue
            given = [key for key in wanted if key in inputs]
            if not given:
                floating(f"{name} names none of {', '.join(wanted)}")
                continue
            for key in given:
                value = str(inputs[key])
                if key.endswith("-file"):
                    continue
                if "${{" in value:
                    floating(f"{key} is an expression ({value}), so its version cannot be read")
                    continue
                if name == "docker/setup-qemu-action":
                    if not _DIGEST.fullmatch(value):
                        floating(f"image {value} is not pinned by digest")
                elif not _EXACT.fullmatch(value.removeprefix("v")):
                    floating(f"{key} {value} is not an exact X.Y.Z")
            if name == "docker/setup-buildx-action":
                image = re.search(r"\bimage=(\S+)", str(inputs.get("driver-opts", "")))
                if not image or not _DIGEST.fullmatch(image.group(1)):
                    floating("driver-opts names no BuildKit image pinned by digest")
    return bad


# ── Node end of life ─────────────────────────────────────────────────────────────────────────────


#: The Node of an OS package: the line it is on is the distribution's, which the policy cannot
#: date, so it fails unless an exception names it.
OS_PACKAGE_NODE = "os-package nodejs"
_OS_NODE = re.compile(r"\b(?:apt-get|apt|apk|dnf|yum|microdnf)\b.*\b(?:install|add)\b.*\bnodejs\b")
#: A Node image tag in any text: `node:<tag>` after a space, a slash, a quote or `//`.
_NODE_TAG = re.compile(r"(?:^|[\s/\"'=])node:([^\s@\"',)]+)")


def _node_line(tag: str) -> str:
    """The version part of a node image tag: ``24.21.0`` for ``24.21.0-alpine``."""
    return tag.split("-", 1)[0].rstrip(".")


def node_versions(tree: Tree) -> list[tuple[str, str]]:
    """Every (file, version) of Node CI or an image runs: `.nvmrc`; each setup-node version, and
    a version file other than `.nvmrc` as unreadable; every `node:<tag>` image in a workflow,
    an action or a Dockerfile, a heredoc's FROM included, with `${ARG}` resolved from its
    default; and an OS package's `nodejs`, as ``OS_PACKAGE_NODE``."""
    found = [(".nvmrc", tree.get(".nvmrc", "").strip())]
    for rel, _job_id, _job, steps in _step_lists(tree):
        for step in steps:
            inputs = step.get("with")
            if action(step) != "actions/setup-node" or not isinstance(inputs, dict):
                continue
            if inputs.get("node-version") is not None:
                found.append((rel, str(inputs["node-version"])))
            version_file = inputs.get("node-version-file")
            if version_file is not None and str(version_file) != ".nvmrc":
                found.append((rel, f"read from {version_file}"))
        for step in steps:
            for _, line in shell_installs.read_script(str(step.get("run", ""))).lines:
                if _OS_NODE.search(line):
                    found.append((rel, OS_PACKAGE_NODE))
    for rel, text in sorted(tree.items()):
        if is_workflow(rel) or is_action_file(rel):
            found += [(rel, _node_line(tag)) for tag in _NODE_TAG.findall(text)]
    for rel, text in _dockerfiles(tree):
        args = _arg_defaults(text.splitlines())
        for tag in _NODE_TAG.findall(text):
            resolved = re.sub(
                r"\$\{?([A-Za-z_]\w*)\}?", lambda m: args.get(m.group(1), m.group(0)), tag
            )
            found.append((rel, _node_line(resolved)))
        for _, _stage, body, _locks in dockerfile_runs(text):
            if _OS_NODE.search(body):
                found.append((rel, OS_PACKAGE_NODE))
    return found


def node_eol(tree: Tree, policy: Policy, today: datetime.date) -> list[Violation]:
    bad: list[Violation] = []
    for rel, version in node_versions(tree):
        if version == OS_PACKAGE_NODE:
            message = (
                "installs Node as an OS package (`nodejs`), on the distribution's line, which "
                f"{POLICY_FILE} cannot date: run it from a pinned node image instead"
            )
            bad.append(Violation("node-eol", rel, version, message))
            continue
        if version.startswith("read from "):
            message = f"setup-node reads its Node {version}, not .nvmrc, so its line is not dated"
            bad.append(Violation("node-eol", rel, version, message))
            continue
        major = re.match(r"v?(\d+)", version)
        end = policy.end_of_life.get(major.group(1)) if major else None
        if end is None:
            message = f"Node {version} is on no line {POLICY_FILE}'s node_end_of_life dates"
            bad.append(Violation("node-eol", rel, version, message))
        elif end <= today:
            message = f"Node {version} reached its end of life on {end.isoformat()}"
            bad.append(Violation("node-eol", rel, version, message))
    return bad


# ── the policy file ──────────────────────────────────────────────────────────────────────────


@dataclass
class Policy:
    end_of_life: dict[str, datetime.date]
    audited_writes: list[dict[str, Any]]
    exceptions: list[dict[str, Any]]
    problems: list[Violation]


def read_policy(text: str) -> Policy:
    policy = Policy({}, [], [], [])

    def broken(message: str) -> None:
        policy.problems.append(Violation("policy-file", POLICY_FILE, "", message))

    if not text.strip():
        broken(f"{POLICY_FILE} is missing or empty")
        return policy
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        broken(f"not valid TOML: {error}")
        return policy
    for key in sorted(set(data) - {"node_end_of_life", "audited_write", "exception"}):
        broken(f"unknown key {key}")
    table = data.get("node_end_of_life")
    if not isinstance(table, dict) or not table:
        broken("node_end_of_life must date each Node line CI may run")
    else:
        for line, value in table.items():
            if not re.fullmatch(r"\d+", line) or type(value) is not datetime.date:
                broken(f'node_end_of_life "{line}" must be a major line with a date: {value!r}')
            else:
                policy.end_of_life[line] = value
    for key, into in (("audited_write", policy.audited_writes), ("exception", policy.exceptions)):
        value = data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(e, dict) for e in value):
            broken(f"{key} must be an array of tables ([[{key}]])")
        else:
            into.extend(value)
    return policy


# ── least privilege ──────────────────────────────────────────────────────────────────────────────


def _writes(permissions: object) -> list[str]:
    if not isinstance(permissions, dict):
        return []
    return sorted(f"{scope}: write" for scope, level in permissions.items() if level == "write")


def _keeps_credentials(step: dict[str, Any]) -> bool:
    """A checkout that keeps its credentials: one that does not set the input to false, or
    whose `with:` cannot be read."""
    if action(step) != "actions/checkout":
        return False
    inputs = step.get("with")
    if inputs is not None and not isinstance(inputs, dict):
        return True
    return str((inputs or {}).get(CREDENTIALS, "")).lower() != "false"


def _audited(tree: Tree, policy: Policy) -> tuple[list[dict[str, Any]], list[Violation]]:
    """The audited-write entries that hold, and a violation for each that does not."""
    workflows = {rel.rsplit("/", 1)[1]: wf for rel, wf in _workflows(tree)}
    good: list[dict[str, Any]] = []
    bad: list[Violation] = []
    for number, entry in enumerate(policy.audited_writes, 1):
        ref = f"audited_write {number}"

        def broken(message: str) -> None:
            bad.append(Violation("least-privilege", POLICY_FILE, ref, f"{ref}: {message}"))

        steps = entry.get("steps")
        missing = [
            f
            for f in AUDITED_WRITE_FIELDS
            if not (
                isinstance(steps, list) and steps and all(isinstance(s, str) and s for s in steps)
                if f == "steps"
                else isinstance(entry.get(f), str) and entry[f].strip()
            )
        ]
        if missing:
            broken(f"missing {', '.join(missing)}")
            continue
        unknown = sorted(set(entry) - set(AUDITED_WRITE_FIELDS))
        if unknown:
            broken(f"unknown field {', '.join(unknown)}")
            continue
        workflow = workflows.get(entry["workflow"], {})
        job = dict(_jobs(workflow)).get(entry["job"])
        if job is None:
            broken(f"{entry['workflow']} {entry['job']} matches no job")
            continue
        held = _writes(job.get("permissions", workflow.get("permissions")))
        if entry["permission"] == CREDENTIALS:
            checkouts = [s for s in _steps(job) if action(s) == "actions/checkout"]
            keeping = {step_name(s) for s in checkouts if _keeps_credentials(s)}
            shared = sorted(
                n for n in entry["steps"] if sum(step_name(s) == n for s in checkouts) > 1
            )
            if shared:
                broken(
                    f"{', '.join(shared)} names more than one checkout in "
                    f"{entry['workflow']} {entry['job']}: give the one that keeps its "
                    "credentials a `name:` of its own"
                )
                continue
            holds = bool(entry["steps"]) and all(n in keeping for n in entry["steps"])
        else:
            holds = entry["permission"] in held
        if not holds:
            broken(f"{entry['workflow']} {entry['job']} does not hold {entry['permission']}")
            continue
        names = {step_name(s) for s in _steps(job)}
        absent = [s for s in entry["steps"] if s not in names]
        if absent:
            broken(f"no step named {', '.join(absent)} in {entry['workflow']} {entry['job']}")
            continue
        good.append(entry)
    return good, bad


def least_privilege(tree: Tree, policy: Policy) -> list[Violation]:
    entries, bad = _audited(tree, policy)

    def listed(workflow: str, job: str, permission: str, step: str | None = None) -> bool:
        return any(
            e["workflow"] == workflow
            and e["job"] == job
            and e["permission"] == permission
            and (step is None or step in e["steps"])
            for e in entries
        )

    def fail(rel: str, ref: str, message: str) -> None:
        bad.append(Violation("least-privilege", rel, ref, f"{ref}: {message}"))

    for rel, workflow in _workflows(tree):
        name = rel.rsplit("/", 1)[1]
        top = workflow.get("permissions")
        if top == "write-all":
            fail(rel, "permissions", "write-all grants every scope")
        top_env = workflow.get("env")
        if top_env is not None and not isinstance(top_env, dict):
            fail(
                rel,
                "env",
                "is an expression, so its variables cannot be read: set each on "
                "the step that needs it",
            )
        for key, value in (top_env if isinstance(top_env, dict) else {}).items():
            if hands_token(value):
                fail(rel, f"env {key}", "hands a token to every step; set it on the step")
        for job_id, job in _jobs(workflow):
            own = job.get("permissions")
            if own is None and top is None and not str(job.get("uses", "")).startswith("./"):
                fail(rel, job_id, "declares no permissions, and the workflow none at the top")
            if own == "write-all":
                fail(rel, job_id, "write-all grants every scope")
            container = job.get("container")
            scopes = [("env", job.get("env"))]
            if isinstance(container, dict):
                scopes.append(("container env", container.get("env")))
            for where, env in scopes:
                if env is not None and not isinstance(env, dict):
                    fail(
                        rel,
                        f"{job_id} {where}",
                        "is an expression, so its variables cannot "
                        "be read: set each on the step that needs it",
                    )
                for key, value in (env if isinstance(env, dict) else {}).items():
                    if hands_token(value):
                        fail(rel, f"{job_id} {where} {key}", "hands a token to every step")
            for permission in _writes(own if own is not None else top):
                if not listed(name, job_id, permission):
                    where = "" if own is not None else " (inherited from the top)"
                    fail(rel, f"{job_id} {permission}", f"is not an audited write{where}")
            for step in _steps(job):
                if _keeps_credentials(step) and not listed(
                    name, job_id, CREDENTIALS, step_name(step)
                ):
                    fail(rel, f"{job_id} {step_name(step)}", "checkout keeps its credentials")
        for permission in _writes(top):
            fail(
                rel,
                f"permissions {permission}",
                "a top-level write reaches every job, one added later included: grant it on the "
                "job that uses it",
            )
    # A composite action's checkout runs inside every job that calls it, so it may never keep
    # its credentials: no audited write can name all of those jobs.
    for rel, data in _actions(tree):
        runs = data.get("runs")
        for step in _steps(runs) if isinstance(runs, dict) else []:
            if _keeps_credentials(step):
                fail(rel, f"action {step_name(step)}", "checkout keeps its credentials")
    return bad


# ── Dependabot ───────────────────────────────────────────────────────────────────────────────────


def _watches(pattern: str, folder: str) -> bool:
    """Whether a Dependabot directory *pattern* covers *folder*: ``*`` is one path segment and
    ``**`` any number, as Dependabot reads them."""
    want = [p for p in pattern.strip("/").split("/") if p]
    have = [p for p in folder.strip("/").split("/") if p]

    def match(i: int, j: int) -> bool:
        if i == len(want):
            return j == len(have)
        if want[i] == "**":
            return any(match(i + 1, k) for k in range(j, len(have) + 1))
        return j < len(have) and fnmatch.fnmatchcase(have[j], want[i]) and match(i + 1, j + 1)

    return match(0, 0)


#: How long Dependabot waits after a release before it moves an image's npm tools to it.
COOLDOWN_DAYS = 7


def _directories(update: Mapping[str, Any]) -> list[str]:
    listed = update.get("directories")
    if isinstance(listed, list):
        return [str(d) for d in listed]
    return [str(update["directory"])] if "directory" in update else []


def _glob(pattern: str) -> re.Pattern[str]:
    """A workflow `paths:` glob as GitHub reads it: `*` and `?` within one path segment, `**`
    across segments, and `**/` any number of folders, none included."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        else:
            out += {"*": "[^/]*", "?": "[^/]"}.get(pattern[i], re.escape(pattern[i]))
            i += 1
    return re.compile(out)


def _selects(paths: list[str], path: str) -> bool:
    """Whether a workflow `paths:` filter runs on a change of *path*: the last pattern that
    matches decides, and a `!pattern` takes the path back out."""
    selected = False
    for pattern in paths:
        negated = pattern.startswith("!")
        if _glob(pattern[1:] if negated else pattern).fullmatch(path):
            selected = not negated
    return selected


def _image_smoke_paths(tree: Tree) -> list[Violation]:
    """A workflow whose `paths:` run it on a change of a Dockerfile also runs on a change of
    each npm lock that Dockerfile installs, so the pull request Dependabot opens to move a lock
    builds the image that installs it."""
    bad: list[Violation] = []
    installed = _locks_installed(tree)
    for rel, data in _workflows(tree):
        loaded: dict[Any, Any] = data
        triggers = loaded.get("on", loaded.get(True))
        if not isinstance(triggers, dict):
            continue
        for event in ("pull_request", "push"):
            filters = triggers.get(event)
            paths = filters.get("paths") if isinstance(filters, dict) else None
            if not isinstance(paths, list):
                continue
            patterns = [str(p) for p in paths]
            for lock, dockerfiles in sorted(installed.items()):
                if not tree.get(lock) or _selects(patterns, lock):
                    continue
                for dockerfile in sorted(d for d in dockerfiles if _selects(patterns, d)):
                    message = (
                        f"{rel} runs on a {event} that changes {dockerfile} but not on one that "
                        f"changes {lock}, which it installs, so a Dependabot pull request that "
                        "moves the lock never builds the image"
                    )
                    bad.append(Violation("dependabot", rel, f"{event} {lock}", message))
    return bad


def dependabot(tree: Tree) -> list[Violation]:
    try:
        data = yaml.safe_load(tree.get(DEPENDABOT_FILE, "")) or {}
    except yaml.YAMLError as error:
        message = f"{DEPENDABOT_FILE} does not parse as YAML: {str(error).splitlines()[0]}"
        return [Violation("dependabot", DEPENDABOT_FILE, "yaml", message)]
    updates = data.get("updates") if isinstance(data, dict) else None
    if not isinstance(updates, list):
        message = f"{DEPENDABOT_FILE} is not a mapping with an `updates:` list"
        return [Violation("dependabot", DEPENDABOT_FILE, "yaml", message)]

    def entries(ecosystem: str) -> list[dict[str, Any]]:
        return [
            u for u in updates if isinstance(u, dict) and u.get("package-ecosystem") == ecosystem
        ]

    bad: list[Violation] = []

    def fail(file: str, ref: str, message: str) -> None:
        bad.append(Violation("dependabot", file, ref, message))

    for rel in image_manifests(tree):
        folder = "/" + rel.rpartition("/")[0]
        moving = [u for u in entries("npm") if any(_watches(p, folder) for p in _directories(u))]
        if not moving:
            message = f"no Dependabot npm entry watches {folder}, so its lock never moves"
            fail(DEPENDABOT_FILE, folder, message)
        for update in moving:
            cooldown = update.get("cooldown")
            days = cooldown.get("default-days") if isinstance(cooldown, dict) else None
            if not (type(days) is int and days >= COOLDOWN_DAYS):
                message = (
                    f"the Dependabot npm entry for {folder} waits no cooldown of at least "
                    f"{COOLDOWN_DAYS} days, so a release can reach the image before a broken or "
                    "compromised one is pulled"
                )
                fail(DEPENDABOT_FILE, f"{folder} cooldown", message)
            groups = update.get("groups")
            if not (isinstance(groups, dict) and list(groups.values()) == [{"patterns": ["*"]}]):
                message = (
                    f"the Dependabot npm entry for {folder} does not move every package in one "
                    'group (`patterns: ["*"]`), so an adapter can move without the CLI it drives'
                )
                fail(DEPENDABOT_FILE, f"{folder} groups", message)
    for rel, text in _dockerfiles(tree):
        for line in text.splitlines():
            words = [w for w in line.split() if not w.startswith("--")]
            if len(words) > 1 and words[0].upper() == "FROM" and words[1].startswith("$"):
                message = (
                    f"{rel}: FROM {words[1]} names its base through an ARG, which Dependabot "
                    "cannot read or move; write the base on the FROM line"
                )
                fail(rel, words[1], message)
    patterns = [p for u in entries("docker") for p in _directories(u)]
    for folder in sorted({"/" + rel.rpartition("/")[0] for rel, _ in _dockerfiles(tree)}):
        if not any(_watches(p, folder) for p in patterns):
            message = f"no Dependabot docker entry watches {folder}, so its bases never move"
            fail(DEPENDABOT_FILE, folder, message)
    return bad + _image_smoke_paths(tree)


# ── the release's image tooling, run before a tag needs it ──────────────────────────────────

RELEASE = ".github/workflows/release.yml"
RELEASE_TOOLING = ".github/workflows/release-image-tooling.yml"
_TOOLING_ACTIONS = ("docker/setup-qemu-action", "docker/setup-buildx-action")
WEB_DOCKERFILE = "deploy/docker/Dockerfile.web"
#: The only actions pin-smoke may run: the two whose pins it proves.
_SMOKE_ACTIONS = set(_TOOLING_ACTIONS)
#: Anything in a pin-smoke `run:` that logs in to a registry or sends an image to one.
_PUBLISHES = re.compile(
    r"--push\b|push=true|type=registry|type=image\b|--load\b"
    r"|\b(?:docker|podman|buildah)\s+(?:image\s+)?push\b|\b(?:docker|podman|buildah|helm)\s+login\b"
    r"|\b(?:crane|oras|regctl|skopeo)\s|imagetools\s+create\b|compose\s+push\b"
)


def _tooling_steps(workflow: dict[str, Any], job_id: str) -> dict[str, tuple[Any, Any]]:
    job = dict(_jobs(workflow)).get(job_id) or {}
    return {
        action(s): (s.get("uses"), s.get("with") or {})
        for s in _steps(job)
        if action(s) in _TOOLING_ACTIONS
    }


def _release_platforms(workflow: dict[str, Any]) -> set[str]:
    """The platforms release.yml's images job builds."""
    job = dict(_jobs(workflow)).get("images") or {}
    for step in _steps(job):
        inputs = step.get("with")
        if action(step) == "docker/build-push-action" and isinstance(inputs, dict):
            return {p.strip() for p in str(inputs.get("platforms", "")).split(",") if p.strip()}
    return set()


def _node_base(tree: Tree) -> str | None:
    """Dockerfile.web's node base, the one the images build their web stage on."""
    for line in tree.get(WEB_DOCKERFILE, "").splitlines():
        words = [w for w in line.split() if not w.startswith("--")]
        if len(words) > 1 and words[0].upper() == "FROM" and "/node:" in "/" + words[1]:
            return words[1]
    return None


def release_tooling(tree: Tree) -> list[Violation]:
    """release-image-tooling.yml's ``pin-smoke`` runs release.yml's QEMU and buildx steps pin for
    pin, on every pull request that changes either file, holding nothing it could publish with:
    it always runs, it builds every platform the release builds, on the images' own node base,
    and its steps are only the two setup actions and a cache-only build."""
    workflows = dict(_workflows(tree))
    smoke_workflow = workflows.get(RELEASE_TOOLING, {})
    release_workflow = workflows.get(RELEASE, {})
    release = _tooling_steps(release_workflow, "images")
    smoke = _tooling_steps(smoke_workflow, "pin-smoke")
    bad: list[Violation] = []

    def fail(message: str) -> None:
        bad.append(Violation("release-tooling", RELEASE_TOOLING, "pin-smoke", message))

    for name in _TOOLING_ACTIONS:
        if name not in release:
            fail(f"release.yml's images job has no {name} step to compare")
        elif smoke.get(name) != release[name]:
            fail(f"pin-smoke runs {name} as {smoke.get(name)}, release.yml as {release[name]}")
    # PyYAML reads the bare key `on` as the boolean True.
    loaded: dict[Any, Any] = smoke_workflow
    triggers = loaded.get("on", loaded.get(True)) or {}
    pull_request = triggers.get("pull_request") if isinstance(triggers, dict) else None
    paths = (pull_request or {}).get("paths") or [] if isinstance(pull_request, dict) else []
    for wanted in (RELEASE, RELEASE_TOOLING):
        if wanted not in paths:
            fail(f"pin-smoke does not run on a pull request that changes {wanted}")
    job = dict(_jobs(smoke_workflow)).get("pin-smoke") or {}
    if job.get("permissions") != {} or smoke_workflow.get("permissions") != {}:
        fail("pin-smoke and its workflow must hold `permissions: {}`")
    for key in ("if", "continue-on-error"):
        if key in job:
            fail(f"pin-smoke sets `{key}`, so a run can pass without proving the pins")
    builds: list[dict[str, Any]] = []
    for step in _steps(job):
        name = step_name(step)
        uses = action(step)
        run = str(step.get("run", ""))
        if uses and uses not in _SMOKE_ACTIONS:
            fail(f"pin-smoke step {name} runs {uses}; only the two setup actions may run there")
        if _PUBLISHES.search(run) or any(
            _PUBLISHES.search(str(v))
            for v in (step.get("with") or {}).values()
            if isinstance(step.get("with"), dict)
        ):
            fail(f"pin-smoke step {name} logs in or publishes an image")
        if uses in _SMOKE_ACTIONS or re.search(r"\bdocker\s+buildx\s+build\b", run):
            for key in ("if", "continue-on-error"):
                if key in step:
                    fail(f"pin-smoke step {name} sets `{key}`, so it can be skipped")
        if re.search(r"\bdocker\s+buildx\s+build\b", run):
            builds.append(step)
    if len(builds) != 1:
        fail(f"pin-smoke has {len(builds)} `docker buildx build` steps; it needs exactly one")
        return bad
    run = str(builds[0]["run"])
    command = next(
        (line for line in run.splitlines() if re.search(r"\bdocker\s+buildx\s+build\b", line)), ""
    )
    outputs = re.findall(r"--output[= ](\S+)", command)
    if outputs != ["type=cacheonly"]:
        fail(
            f"pin-smoke's build writes {outputs or 'its default output'}; "
            "only `--output type=cacheonly`"
        )
    platforms = re.search(r"--platform[= ](\S+)", command)
    built = set(platforms.group(1).split(",")) if platforms else set()
    missing = sorted(_release_platforms(release_workflow) - built)
    if missing or not built:
        fail(
            f"pin-smoke does not build {', '.join(missing) or 'any platform'}, "
            "which the release builds"
        )
    froms = re.findall(r"^\s*FROM\s+(\S+)", run, flags=re.M)
    base = _node_base(tree)
    if not froms:
        fail("pin-smoke's build has no FROM to build on")
    elif base is None:
        fail(f"{WEB_DOCKERFILE} has no node base to hold pin-smoke's equal to")
    elif froms[0] != base:
        fail(f"pin-smoke builds on {froms[0]}, the images' web stage on {base}")
    return bad


# ── exceptions, and the whole check ──────────────────────────────────────────────────────────────


def apply_exceptions(
    found: list[Violation], policy: Policy, today: datetime.date
) -> list[Violation]:
    waived: set[Violation] = set()
    own: list[Violation] = []
    for number, entry in enumerate(policy.exceptions, 1):
        ref = f"exception {number}"

        def broken(message: str) -> None:
            own.append(Violation("exception", POLICY_FILE, ref, f"{ref}: {message}"))

        missing = [
            f
            for f in EXCEPTION_FIELDS
            if f not in entry
            or (f != "expires" and not (isinstance(entry[f], str) and entry[f].strip()))
        ]
        if missing:
            broken(f"missing {', '.join(missing)}")
            continue
        if sorted(set(entry) - set(EXCEPTION_FIELDS)):
            broken(f"unknown field {', '.join(sorted(set(entry) - set(EXCEPTION_FIELDS)))}")
            continue
        expires = entry["expires"]
        if type(expires) is not datetime.date:
            broken(f"expires must be a date, such as {today.isoformat()}")
            continue
        if entry["scope"] not in WAIVABLE:
            broken(f"scope {entry['scope']} is not a rule an exception may waive")
            continue
        if expires < today:
            broken(f"expired on {expires.isoformat()}: fix the reference, or renew it")
            continue
        if (expires - today).days > EXCEPTION_MAX_DAYS:
            broken(f"expires {expires.isoformat()}, more than {EXCEPTION_MAX_DAYS} days out")
            continue
        matches = [
            v
            for v in found
            if (v.rule, v.file, v.reference) == (entry["scope"], entry["file"], entry["reference"])
        ]
        if not matches:
            broken(f"{entry['scope']} on {entry['file']} {entry['reference']!r} matches nothing")
            continue
        if len(matches) > 1:
            broken(f"matches {len(matches)} violations; an exception names exactly one")
            continue
        waived.update(matches)
    return [v for v in found if v not in waived] + own


def check(tree: Tree, today: datetime.date | None = None) -> list[Violation]:
    """Every supply-chain violation in *tree*, after the policy's exceptions."""
    today = today or datetime.date.today()
    policy = read_policy(tree.get(POLICY_FILE, ""))
    rules: list[tuple[str, Callable[[], list[Violation]]]] = [
        ("action-sha", lambda: action_sha(tree)),
        ("image-digest", lambda: image_digest(tree)),
        ("run-install", lambda: run_install(tree)),
        ("setup-floating", lambda: setup_floating(tree)),
        ("node-eol", lambda: node_eol(tree, policy, today)),
        ("least-privilege", lambda: least_privilege(tree, policy)),
        ("dependabot", lambda: dependabot(tree)),
        ("release-tooling", lambda: release_tooling(tree)),
    ]
    found: list[Violation] = []
    for rule, run in rules:
        try:
            found += run()
        except (AttributeError, TypeError, KeyError, IndexError) as error:
            message = f"could not read the tree: {type(error).__name__}: {error}"
            found.append(Violation(rule, ".", "unreadable", message))
    unwaivable = workflow_yaml(tree)
    return apply_exceptions(found, policy, today) + unwaivable + policy.problems

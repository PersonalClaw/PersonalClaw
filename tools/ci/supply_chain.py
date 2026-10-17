"""CI's supply chain: every input CI fetches is pinned, and every job holds only what it needs.

``check`` reads a tree (repository-relative path to text) and returns one ``Violation`` per
broken rule, with the policy file's exceptions applied. It never raises on a malformed input:
that reads as a violation too. The rules:

- ``action-sha``: a ``uses:`` names a 40-character commit SHA and its release as ``# vX.Y.Z``;
  a ``docker://`` action is an image and is judged as one.
- ``image-digest``: an image is a tag with a digest, never ``latest``. A workflow's image names
  its linux/amd64 manifest, with the index it belongs to in the comment above, and the digest it
  pins is not that index. A Dockerfile base lists each platform's manifest digest, and a base
  named through an ``ARG`` is judged by the ``ARG``'s default, which must exist.
- ``run-install``: a ``run:`` or ``RUN`` that installs a tool names its exact version, takes it
  from a lock, or installs this project. OS packages come with the pinned image or the logged
  runner image and are outside the rule.
- ``setup-floating``: a setup action pins an exact version (or the file that names one) and
  never sets ``check-latest``; QEMU and BuildKit images are pinned by digest.
- ``node-eol``: every Node CI or an image runs is on a line the policy dates, before that date.
- ``least-privilege``: no ``write-all``, every job declares permissions or inherits declared
  ones, every write and every checkout that keeps its credentials is an audited write in the
  policy, no top-level write reaches a job unasked, and no workflow- or job-level ``env:``
  hands a token to every step.
- ``dependabot``: every folder that holds a Dockerfile has a Dependabot ``docker`` entry.
- ``release-tooling``: the pull-request smoke of the release's QEMU and buildx steps runs
  release.yml's own pins, on every change of either file, holding no permission.
- ``policy-file`` and ``exception``: the policy itself is well formed.

See docs/maintainers/ci.md, "Supply chain".
"""

from __future__ import annotations

import datetime
import functools
import json
import os
import re
import shlex
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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
_TOKEN = re.compile(r"\$\{\{[^}]*\b(?:github\.token|secrets\.\w+)")
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


def is_dockerfile(name: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:Dockerfile|Containerfile)(?:\..+)?|.+\.(?:Dockerfile|Containerfile)", name
        )
    )


def dockerfile_paths(root: Path) -> list[Path]:
    """Every Dockerfile under *root*, wherever it sits, so a new one cannot go unread."""
    found: list[Path] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIPPED and not d.startswith(".venv")]
        found += [Path(folder) / f for f in files if is_dockerfile(f)]
    return sorted(found)


def tree_paths(root: Path) -> list[Path]:
    """The files ``check`` reads."""
    return [
        *sorted(root.glob(".github/workflows/*.yml")),
        *dockerfile_paths(root),
        root / DEPENDABOT_FILE,
        root / POLICY_FILE,
        root / ".nvmrc",
        root / "package.json",
        root / "package-lock.json",
    ]


def read_tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") if p.is_file() else ""
        for p in tree_paths(root)
    }


# ── readers ──────────────────────────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=512)
def _parse(text: str) -> dict[str, Any]:
    """One workflow's YAML, parsed once per text: every rule reads every workflow. Callers read
    it and never change it."""
    try:
        data = yaml.load(text, Loader=_LOADER)  # noqa: S506 - a safe loader
    except yaml.YAMLError:
        data = None
    return data if isinstance(data, dict) else {}


def _workflows(tree: Tree) -> Iterator[tuple[str, dict[str, Any]]]:
    for rel, text in sorted(tree.items()):
        if rel.startswith(".github/workflows/"):
            yield rel, _parse(text)


def _jobs(workflow: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for job_id, job in (workflow.get("jobs") or {}).items():
        if isinstance(job, dict):
            yield str(job_id), job


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in job.get("steps") or [] if isinstance(s, dict)]


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


# ── step 2: pins ─────────────────────────────────────────────────────────────────────────────


def _judge_image(
    bad: list[Violation], rel: str, ref: str, comment: str, platforms: tuple[str, ...]
) -> None:
    """One image reference against the digest rule. *platforms* empty means one platform's
    manifest is pinned, and the comment must name it and the index it belongs to."""
    where = ref
    pinned = _DIGEST.fullmatch(ref)
    if not pinned:
        bad.append(Violation("image-digest", rel, where, f"{ref} is not a tag pinned by digest"))
        return
    if ref.split("@", 1)[0].rsplit(":", 1)[1] == "latest":
        bad.append(Violation("image-digest", rel, where, f"{ref} names the moving tag `latest`"))
    if platforms:
        for platform in platforms:
            if not re.search(rf"{platform}\b.*sha256:[0-9a-f]{{64}}", comment, flags=re.S):
                bad.append(
                    Violation(
                        "image-digest", rel, where, f"{ref} does not say its {platform} digest"
                    )
                )
        return
    if not re.search(r"\blinux/amd64\b", comment):
        bad.append(
            Violation(
                "image-digest", rel, where, f"{ref} does not say it is the linux/amd64 manifest"
            )
        )
    index = re.search(r"\bindex\s+sha256:([0-9a-f]{64})", comment)
    if not index:
        bad.append(
            Violation("image-digest", rel, where, f"{ref} does not name the index it belongs to")
        )
    elif index.group(1) == pinned.group(1):
        bad.append(
            Violation(
                "image-digest",
                rel,
                where,
                f"{ref} pins the index its comment names, not its linux/amd64 manifest",
            )
        )


def _uses_lines(text: str) -> Iterator[tuple[int, str, str]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"\s*(?:-\s+)?uses:\s*(\S+)(.*)$", line)
        if match:
            yield index, match.group(1), match.group(2)


def action_sha(tree: Tree) -> list[Violation]:
    bad: list[Violation] = []
    found = 0
    for rel, text in sorted(tree.items()):
        if not rel.startswith(".github/workflows/"):
            continue
        lines = text.splitlines()
        for index, ref, rest in _uses_lines(text):
            if ref.startswith("./"):
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
    if not found:
        bad.append(Violation("action-sha", ".github/workflows", "", "read no `uses:` line"))
    return bad


def _args(lines: list[str]) -> dict[str, tuple[str | None, int]]:
    found: dict[str, tuple[str | None, int]] = {}
    for index, line in enumerate(lines):
        arg = re.match(r"\s*ARG\s+(\w+)(?:=(\S*))?\s*$", line, flags=re.I)
        if arg:
            found[arg.group(1)] = (arg.group(2).strip("\"'") if arg.group(2) else None, index)
    return found


def image_digest(tree: Tree) -> list[Violation]:
    bad: list[Violation] = []
    found = 0
    for rel, text in sorted(tree.items()):
        if not rel.startswith(".github/workflows/"):
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines):
            match = re.match(r"\s+(?:container|image):\s*(\S+)\s*$", line)
            if match:
                found += 1
                _judge_image(bad, rel, match.group(1), _comment_above(lines, index), ())
    for rel, text in _dockerfiles(tree):
        lines = text.splitlines()
        args = _args(lines)
        stages: set[str] = set()
        for index, line in enumerate(lines):
            words = [w for w in line.split() if not w.startswith("--")]
            if not words or words[0].upper() != "FROM":
                continue
            ref, at = words[1], index
            name = words[3].lower() if len(words) > 3 and words[2].upper() == "AS" else ""
            variable = re.fullmatch(r"\$\{?(\w+)\}?", ref)
            if variable:
                default, at = args.get(variable.group(1), (None, index))
                if default is None:
                    bad.append(
                        Violation(
                            "image-digest", rel, ref, f"FROM {ref} reads an ARG with no default"
                        )
                    )
                    stages.add(name)
                    continue
                ref = default
            if ref == "scratch" or ref.lower() in stages:
                stages.add(name)
                continue
            stages.add(name)
            found += 1
            _judge_image(bad, rel, ref, _comment_above(lines, at), ("linux/amd64", "linux/arm64"))
    if not found:
        bad.append(Violation("image-digest", ".", "", "read no container image"))
    return bad


# ── step 2: installs a run: or RUN performs ──────────────────────────────────────────────────

#: Options whose next word is their value, so the value is not read as something to install.
_VALUED = set(
    "-c --constraint -i --index-url --extra-index-url -f --find-links -t --target --prefix "
    "--root --python -p --cache-dir -o --output-file --from --workspace -w -e --editable "
    "--directory --project".split()
)
_PIN = re.compile(r"[A-Za-z0-9][\w.\-]*(?:\[[\w,.\-]+\])?==[^\s*<>,!~=]+")
_NPM_PIN = re.compile(r"(?:@[^/@\s]+/)?[^@\s]+@\d+\.\d+\.\d+")
#: The project's own installer, which full.yml pipes into sh on purpose: it is the subject.
_OWN_INSTALLER = "personalclaw.dev/install"


def _own_project(target: str) -> bool:
    """This repository's own code: a path, a built wheel, or the published package itself."""
    name = re.split(r"[=<>!~\[@ ]", target, maxsplit=1)[0].lower()
    return target.startswith((".", "/", "$")) or "/" in target or name == "personalclaw"


def _operands(words: list[str]) -> tuple[list[str], set[str]]:
    targets: list[str] = []
    flags: set[str] = set()
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word.startswith("-"):
            flag = word.split("=", 1)[0]
            flags.add(flag)
            skip = flag in _VALUED and "=" not in word
        else:
            targets.append(word)
    return targets, flags


def _words(segment: str) -> list[str]:
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    while words and (re.fullmatch(r"\w+=.*", words[0]) or words[0] in ("sudo", "exec", "env")):
        words = words[1:]
    return words


def lock_binaries(lock_text: str) -> set[str]:
    """The packages the root lock installs for a workspace, and the binaries they provide."""
    try:
        packages = json.loads(lock_text or "{}").get("packages") or {}
    except ValueError:
        return set()
    found: set[str] = set()
    for key, entry in packages.items():
        if "node_modules/" in key:
            continue
        for name in {**(entry.get("dependencies") or {}), **(entry.get("devDependencies") or {})}:
            locked = packages.get(f"node_modules/{name}")
            if not locked:
                continue
            found.add(name)
            bins = locked.get("bin")
            found.update(
                bins if isinstance(bins, dict) else [name.rsplit("/", 1)[-1]] if bins else []
            )
    return found


def install_problem(segment: str, binaries: set[str], in_workflow: bool) -> str | None:
    """Why one shell command installs a tool with no exact version, or None."""
    words = _words(segment)
    if not words or words[0] in ("echo", "printf"):
        return None
    head = words[0].rsplit("/", 1)[-1]
    rest = words[1:]
    if head in ("python", "python3") and rest[:2] == ["-m", "pip"]:
        head, rest = "pip", rest[2:]
    if head == "uv" and rest[:1] in (["pip"], ["tool"]) and rest[1:2] == ["install"]:
        head, rest = "pip", rest[1:]
    elif head == "uv" and rest[:1] == ["run"]:
        withs = [rest[i + 1] for i, w in enumerate(rest[:-1]) if w == "--with"]
        withs += [w.split("=", 1)[1] for w in rest if w.startswith("--with=")]
        loose = [w for w in withs if not _PIN.fullmatch(w)]
        return f"uv run --with {', '.join(loose)} names no exact version" if loose else None
    elif head == "uvx" or (head == "uv" and rest[:2] == ["tool", "run"]):
        targets, _ = _operands(rest[2:] if head == "uv" else rest)
        source = next((rest[i + 1] for i, w in enumerate(rest[:-1]) if w == "--from"), "")
        tool = targets[0] if targets else ""
        exact = _PIN.fullmatch(source) or re.fullmatch(r"\S+@\d+\.\d+\.\d+", tool)
        return None if exact else f"uvx {tool} names no exact version"
    if head in ("pip", "pip3", "pipx") and rest[:1] == ["install"]:
        targets, flags = _operands(rest[1:])
        if flags & {"-r", "--requirement"}:
            return (
                None if "--require-hashes" in flags else "pip install -r without --require-hashes"
            )
        loose = [t for t in targets if not _PIN.fullmatch(t) and not _own_project(t)]
        return f"install of {', '.join(loose)} names no exact version" if loose else None
    if head == "npm" and rest[:1] in (["install"], ["i"], ["add"]):
        targets, _ = _operands(rest[1:])
        if not targets:
            return "npm install rewrites the lock; use npm ci" if in_workflow else None
        loose = [t for t in targets if not _NPM_PIN.fullmatch(t)]
        return f"npm install of {', '.join(loose)} names no exact version" if loose else None
    if head == "npx":
        flag = next((i for i, w in enumerate(rest) if w in ("--package", "-p")), None)
        name = rest[flag + 1] if flag is not None and flag + 1 < len(rest) else ""
        name = name or next((w for w in rest if not w.startswith("-")), "")
        if name and not re.search(r"@\d+\.\d+\.\d+$", name) and name not in binaries:
            return f"npx {name} runs a package that is neither in the lock nor pinned"
    return None


def _segments(line: str) -> list[str]:
    return [s for s in re.split(r"&&|\|\||;|(?<!\|)\|(?!\|)", line) if s.strip()]


def _line_problems(line: str, binaries: set[str], in_workflow: bool) -> list[str]:
    found: list[str] = []
    if re.search(r"\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b", line):
        if _OWN_INSTALLER not in line:
            found.append("pipes a download into a shell")
    for segment in _segments(line):
        problem = install_problem(segment, binaries, in_workflow)
        if problem:
            found.append(problem)
    return found


def run_install(tree: Tree) -> list[Violation]:
    bad: list[Violation] = []
    binaries = lock_binaries(tree.get("package-lock.json", ""))
    for rel, workflow in _workflows(tree):
        for job_id, job in _jobs(workflow):
            for step in _steps(job):
                for _, line in _logical(str(step.get("run", ""))):
                    for problem in _line_problems(line, binaries, in_workflow=True):
                        ref = f"{job_id} {step_name(step)}"
                        bad.append(Violation("run-install", rel, ref, f"{ref}: {problem}"))
    for rel, text in _dockerfiles(tree):
        for number, line in _logical(text):
            if not line.upper().startswith("RUN "):
                continue
            for problem in _line_problems(line[4:], binaries, in_workflow=False):
                bad.append(
                    Violation("run-install", rel, problem, f"RUN at line {number}: {problem}")
                )
    return bad


# ── step 2: setup actions ────────────────────────────────────────────────────────────────────

#: Each setup action's version inputs: a step sets one, and a literal one is exact.
SETUP_INPUTS: dict[str, tuple[str, ...]] = {
    "actions/setup-node": ("node-version", "node-version-file"),
    "actions/setup-python": ("python-version", "python-version-file"),
    "astral-sh/setup-uv": ("version", "version-file"),
    "docker/setup-buildx-action": ("version",),
    "docker/setup-qemu-action": ("image",),
}


def setup_floating(tree: Tree) -> list[Violation]:
    bad: list[Violation] = []
    for rel, workflow in _workflows(tree):
        for job_id, job in _jobs(workflow):
            for step in _steps(job):
                name = action(step)
                if name not in SETUP_INPUTS and not re.search(r"/setup-[\w-]+$", name):
                    continue
                inputs = step.get("with") or {}
                ref = f"{job_id} {step_name(step)}"

                def floating(message: str) -> None:
                    bad.append(Violation("setup-floating", rel, ref, f"{ref}: {message}"))

                if str(inputs.get("check-latest", "")).lower() == "true":
                    floating(f"{name} sets check-latest")
                wanted = SETUP_INPUTS.get(name, ())
                given = [key for key in wanted if key in inputs]
                if not given:
                    floating(f"{name} names none of {', '.join(wanted) or 'an exact version'}")
                    continue
                for key in given:
                    value = str(inputs[key])
                    if key.endswith("-file") or value.startswith("${{ matrix."):
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


# ── step 2: Node end of life ─────────────────────────────────────────────────────────────────


def node_versions(tree: Tree) -> list[tuple[str, str]]:
    """Every (file, version) of Node CI or an image runs."""
    found = [(".nvmrc", tree.get(".nvmrc", "").strip())]
    for rel, workflow in _workflows(tree):
        for _job_id, job in _jobs(workflow):
            for step in _steps(job):
                version = (step.get("with") or {}).get("node-version")
                if action(step) == "actions/setup-node" and version is not None:
                    found.append((rel, str(version)))
    for rel, text in _dockerfiles(tree):
        found += [(rel, tag) for tag in re.findall(r"\bnode:(\d[^\s@-]*)", text)]
    return found


def node_eol(tree: Tree, policy: Policy, today: datetime.date) -> list[Violation]:
    bad: list[Violation] = []
    for rel, version in node_versions(tree):
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


# ── step 3: least privilege ──────────────────────────────────────────────────────────────────


def _writes(permissions: object) -> list[str]:
    if not isinstance(permissions, dict):
        return []
    return sorted(f"{scope}: write" for scope, level in permissions.items() if level == "write")


def _keeps_credentials(step: dict[str, Any]) -> bool:
    inputs = step.get("with") or {}
    return action(step) == "actions/checkout" and str(inputs.get(CREDENTIALS, "")).lower() != (
        "false"
    )


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
            holds = any(_keeps_credentials(s) for s in _steps(job))
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
        for key, value in (workflow.get("env") or {}).items():
            if _TOKEN.search(str(value)):
                fail(rel, f"env {key}", "hands a token to every step; set it on the step")
        for job_id, job in _jobs(workflow):
            own = job.get("permissions")
            if own is None and top is None and "uses" not in job:
                fail(rel, job_id, "declares no permissions, and the workflow none at the top")
            if own == "write-all":
                fail(rel, job_id, "write-all grants every scope")
            for key, value in (job.get("env") or {}).items():
                if _TOKEN.search(str(value)):
                    fail(rel, f"{job_id} env {key}", "hands a token to every step")
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
    return bad


# ── step 5: Dependabot ───────────────────────────────────────────────────────────────────────


def dependabot(tree: Tree) -> list[Violation]:
    try:
        data = yaml.safe_load(tree.get(DEPENDABOT_FILE, "")) or {}
    except yaml.YAMLError:
        data = {}
    watched = {
        "/" + str(u.get("directory", "")).strip("/")
        for u in (data.get("updates") or [])
        if isinstance(u, dict) and u.get("package-ecosystem") == "docker"
    }
    bad: list[Violation] = []
    for folder in sorted({"/" + rel.rpartition("/")[0] for rel, _ in _dockerfiles(tree)}):
        if folder not in watched:
            message = f"no Dependabot docker entry watches {folder}, so its bases never move"
            bad.append(Violation("dependabot", DEPENDABOT_FILE, folder, message))
    return bad


# ── the release's image tooling, run before a tag needs it ──────────────────────────────────

RELEASE = ".github/workflows/release.yml"
RELEASE_TOOLING = ".github/workflows/release-image-tooling.yml"
_TOOLING_ACTIONS = ("docker/setup-qemu-action", "docker/setup-buildx-action")
#: A step that logs in to a registry or sends an image to one.
_PUBLISHES = re.compile(r"--push\b|type=registry|docker\s+push\b")


def _tooling_steps(workflow: dict[str, Any], job_id: str) -> dict[str, tuple[Any, Any]]:
    job = dict(_jobs(workflow)).get(job_id) or {}
    return {
        action(s): (s.get("uses"), s.get("with") or {})
        for s in _steps(job)
        if action(s) in _TOOLING_ACTIONS
    }


def release_tooling(tree: Tree) -> list[Violation]:
    """release-image-tooling.yml's ``pin-smoke`` runs release.yml's QEMU and buildx steps pin for
    pin, on every pull request that changes either file, holding nothing it could publish with."""
    workflows = dict(_workflows(tree))
    smoke_workflow = workflows.get(RELEASE_TOOLING, {})
    release = _tooling_steps(workflows.get(RELEASE, {}), "images")
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
    paths = (pull_request or {}).get("paths") or []
    for wanted in (RELEASE, RELEASE_TOOLING):
        if wanted not in paths:
            fail(f"pin-smoke does not run on a pull request that changes {wanted}")
    job = dict(_jobs(smoke_workflow)).get("pin-smoke") or {}
    if job.get("permissions") != {} or smoke_workflow.get("permissions") != {}:
        fail("pin-smoke and its workflow must hold `permissions: {}`")
    for step in _steps(job):
        if action(step) == "docker/login-action" or _PUBLISHES.search(str(step.get("run", ""))):
            fail(f"pin-smoke step {step_name(step)} logs in or publishes an image")
    return bad


# ── step 4: exceptions, and the whole check ──────────────────────────────────────────────────


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
            if f not in entry or (f != "expires" and not str(entry[f]).strip())
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
        waived.update(matches)
    return [v for v in found if v not in waived] + own


def check(tree: Tree, today: datetime.date | None = None) -> list[Violation]:
    """Every supply-chain violation in *tree*, after the policy's exceptions."""
    today = today or datetime.date.today()
    policy = read_policy(tree.get(POLICY_FILE, ""))
    found = [
        *action_sha(tree),
        *image_digest(tree),
        *run_install(tree),
        *setup_floating(tree),
        *node_eol(tree, policy, today),
        *least_privilege(tree, policy),
        *dependabot(tree),
        *release_tooling(tree),
    ]
    return apply_exceptions(found, policy, today) + policy.problems

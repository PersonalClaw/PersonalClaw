"""Run workspace + environment: the provisioning block, folder contracts, env grants (S49).

A run that touches the filesystem needs to say WHERE, and the answer has to be a declaration rather
than a convention — PersonalClaw has already been bitten by the alternative (the deleted-real-model
incident, where an in-place run's destructive step ran against real state).

Three things land, each deliberately built on machinery that already exists:

* **The `workspace` block** — `{mode, preserve_patterns, setup, teardown, env}`. Worktree mode
  reuses `loop/worktree.py` (`.worktrees/<id>` + `pclaw/task-*` branches), which is proven,
  rather than a second git implementation that would drift from it.
* **Folder contracts** (`.folder.yaml`) — validated as WARNINGS, never fatal, and unknown
  fields pass silently. That tolerance is the lesson of the 23-of-25-dropped-memories bug:
  a strict reader over a format that evolves discards the data it was meant to protect.
* **Per-project env grants** — the spawn env for a leaf is SECRET-FILTERED: only explicitly
granted
  keys reach a child. It extends the existing credential seam (`{{secret:KEY}}` + the credential
  store) rather than adding a second secret mechanism, and every surface shows presence flags only.

The asymmetries worth stating up front, because each fails in a chosen direction:

* **Setup failure does not block the run**; teardown failure does not block deletion. Setup is
  best-effort convenience — refusing to run because `npm install` failed would make the block a
  liability. But teardown runs BEFORE deletion, always, because its whole job is to stop
  services and
  sync artifacts out while the workspace still exists.
* **A reserved env var is REJECTED, not overridden.** Letting a run set `HOME` or `PATH`
is letting it
  redirect every subsequent tool invocation, including the ones that enforce the other rules here.
* **An ungranted secret is absent, not empty.** An empty string reads to a child as "this credential
  is configured and blank", which produces an authentication error instead of a missing-
  config error.

Pure functions over declarations. Provisioning I/O stays with the caller; this module decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Env vars a run may never set. Letting a run redirect `HOME`, `PATH` or the XDG dirs is letting it
#: relocate every config file, credential store and tool binary the rest of the system resolves
#: through them — including the machinery that enforces every other rule in this module.
RESERVED_ENV_VARS = frozenset(
    {
        "HOME",
        "PATH",
        "SHELL",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "PYTHONPATH",
        "PYTHONHOME",
    }
)

#: Prefixes reserved for the same reason, as prefixes: `XDG_CONFIG_HOME` and every sibling relocate
#: config resolution, and `PERSONALCLAW_` overrides would let a run repoint its own home.
RESERVED_ENV_PREFIXES = ("XDG_", "PERSONALCLAW_", "DYLD_", "LD_")


#: Folder lifecycles. `transient` is the DEFAULT for agent-originated writes — a staging zone that
#: cannot be promoted without explicit action, which is propose-don't-write enforced by the
#: filesystem rather than by a prompt.
class Lifecycle(str, Enum):
    TRANSIENT = "transient"
    TTL_STAGING = "ttl_staging"
    PERMANENT = "permanent"
    IMMUTABLE = "immutable"


#: Day-scale TTL for unprocessed run observations feeding slow extraction pipelines. Long
#: enough that
#: a weekly pass still sees everything; short enough that an abandoned pipeline does not accumulate
#: forever.
TTL_STAGING_DAYS = 14


class Mode(str, Enum):
    """Where a run's filesystem work happens.

    `IN_PLACE` is the dangerous one and is never a default: it is the mode in which a
    destructive step
    runs against real state. `SCRATCH` is the default precisely because being wrong about isolation
    should cost a copy, not the original.
    """

    SCRATCH = "scratch"
    WORKTREE = "worktree"
    IN_PLACE = "in_place"
    CONTAINER = "container"


#: Modes that isolate the run from the user's real tree. The board's suspend/resume decision (S46)
#: keys off exactly this: an isolated substrate can survive a restart, an in-place one cannot.
ISOLATED_MODES = frozenset({Mode.SCRATCH, Mode.WORKTREE, Mode.CONTAINER})


@dataclass
class WorkspaceSpec:
    """A run's declared workspace.

    `preserve_patterns` is the adoption-critical detail: a worktree with no `.env` is a
    worktree where
    every build fails, and a user whose first isolated run cannot install dependencies concludes
    isolation is broken rather than unconfigured.
    """

    mode: Mode = Mode.SCRATCH
    preserve_patterns: list[str] = field(default_factory=list)
    setup: str = ""
    teardown: str = ""
    env: dict[str, str | None] = field(default_factory=dict)
    #: Named workspaces are reused across runs; unnamed ones are per-run. A named
    #: workspace refreshes
    #: fast-forward-only — a rebase or reset would silently discard work a previous run left
    #: there.
    name: str = ""

    @property
    def isolated(self) -> bool:
        return self.mode in ISOLATED_MODES

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "preserve_patterns": list(self.preserve_patterns),
            "setup": self.setup,
            "teardown": self.teardown,
            # Values are NEVER serialized — presence flags only. A workspace block echoed into a
            # run
            # record, a journal or a UI must not be the thing that leaks a token.
            "env": {key: (value is not None) for key, value in self.env.items()},
            "name": self.name,
            "isolated": self.isolated,
        }


@dataclass
class SpecIssue:
    """One problem with a declaration. `fatal` refuses provisioning; otherwise it is advisory."""

    code: str
    message: str
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "fatal": self.fatal}


#: A glob that would copy an entire tree into an isolated workspace. Rejected because the point of
#: isolation is that the run works on a copy of what it needs, and `**` copies what it does not.
_GREEDY_PATTERNS = ("**", "**/*", "/", ".", "./", "*")


def parse_workspace(raw: Any) -> tuple[WorkspaceSpec, list[SpecIssue]]:
    """Read a `workspace:` block, returning the spec and everything wrong with it.

    An unknown MODE is fatal rather than defaulted. Defaulting it would silently run in a mode the
    author did not choose, and the modes differ in exactly the way that matters: `in_place` touches
    the user's real tree.
    """
    issues: list[SpecIssue] = []
    if raw is None:
        return WorkspaceSpec(), issues
    if not isinstance(raw, dict):
        return WorkspaceSpec(), [
            SpecIssue("workspace_not_object", "workspace must be an object", fatal=True)
        ]

    mode_raw = str(raw.get("mode", Mode.SCRATCH.value) or Mode.SCRATCH.value).strip().lower()
    try:
        mode = Mode(mode_raw)
    except ValueError:
        return WorkspaceSpec(), [
            SpecIssue(
                "unknown_mode",
                f"unknown workspace mode {mode_raw!r}; expected one of "
                f"{sorted(m.value for m in Mode)} — defaulting would run in a mode nobody chose, "
                "and `in_place` touches the real tree",
                fatal=True,
            )
        ]

    patterns = [str(p) for p in (raw.get("preserve_patterns") or []) if str(p).strip()]
    for pattern in patterns:
        if pattern.strip() in _GREEDY_PATTERNS:
            issues.append(
                SpecIssue(
                    "greedy_preserve_pattern",
                    f"preserve pattern {pattern!r} would copy the whole tree into an isolated "
                    "workspace, which defeats the isolation it is being copied into",
                    fatal=True,
                )
            )

    env, env_issues = parse_env(raw.get("env"))
    issues.extend(env_issues)

    spec = WorkspaceSpec(
        mode=mode,
        preserve_patterns=patterns,
        setup=str(raw.get("setup", "") or "").strip(),
        teardown=str(raw.get("teardown", "") or "").strip(),
        env=env,
        name=str(raw.get("name", "") or "").strip(),
    )
    if spec.mode is Mode.IN_PLACE and spec.teardown:
        issues.append(
            SpecIssue(
                "in_place_teardown",
                "teardown on an in_place workspace runs against the user's real tree — a cleanup "
                "command here deletes real work, not scratch state",
            )
        )
    return spec, issues


def parse_env(raw: Any) -> tuple[dict[str, str | None], list[SpecIssue]]:
    """Read the env section. A `None` value means "inherit from the host at spawn".

    Inheritance is a value in its own right, not an omission: `{"AWS_PROFILE": null}` says "pass
    mine through", which differs from not mentioning it (absent) and from `""` (set and empty).
    Collapsing the three would make an inherited var indistinguishable from a blank one.
    """
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [SpecIssue("env_not_object", "workspace.env must be an object", fatal=True)]
    env: dict[str, str | None] = {}
    issues: list[SpecIssue] = []
    for key, value in raw.items():
        name = str(key)
        if not is_valid_env_name(name):
            issues.append(
                SpecIssue("invalid_env_name", f"{name!r} is not a valid environment variable name")
            )
            continue
        if is_reserved_env(name):
            issues.append(
                SpecIssue(
                    "reserved_env",
                    f"{name} is reserved and cannot be overridden — redirecting it relocates every "
                    "config file, credential store or binary the system resolves through it",
                    fatal=True,
                )
            )
            continue
        env[name] = None if value is None else str(value)
    return env, issues


def is_valid_env_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""))


def is_reserved_env(name: str) -> bool:
    upper = (name or "").upper()
    if upper in RESERVED_ENV_VARS:
        return True
    return any(upper.startswith(prefix) for prefix in RESERVED_ENV_PREFIXES)


def spawn_env(
    spec: WorkspaceSpec,
    *,
    granted: dict[str, str] | None = None,
    host_env: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """The env a leaf actually receives, secret-filtered. Returns `(env, withheld_keys)`.

    Only explicitly GRANTED keys resolve to a value. An ungranted key is ABSENT rather
    than empty: an
    empty string reads to a child as "this credential is configured and blank", which produces an
    authentication failure instead of a missing-configuration error — and the first is
    much harder to
    diagnose.

    `withheld_keys` is returned rather than logged so the cockpit can show "2 declared secrets were
    not granted" instead of a child failing for reasons nobody can see.
    """
    granted = granted or {}
    host_env = host_env or {}
    out: dict[str, str] = {}
    withheld: list[str] = []
    for name, declared in spec.env.items():
        if declared is None:
            # Inherit-from-host. Still filtered: a host var that is itself a secret must be granted
            # explicitly, or "inherit my environment" would become a blanket credential grant.
            if name in granted:
                out[name] = granted[name]
            elif name in host_env and not looks_secret(name):
                out[name] = host_env[name]
            else:
                withheld.append(name)
            continue
        if declared.startswith("{{secret:"):
            key = declared[len("{{secret:") :].rstrip("}").strip()
            if key in granted:
                out[name] = granted[key]
            else:
                withheld.append(name)
            continue
        out[name] = declared
    return out, withheld


#: Name shapes that mark a value as credential-bearing. Reuses the workflow secrets
#: module's own hint
#: list AND its matcher rather than a second one — two lists would disagree about `apiKey`
#: eventually. The matcher is shared for the same reason one indirection later: while each
#: caller ran its own `any(hint in name)` loop there was nowhere to express "match `pat` as a
#: WORD", so the boundary got hand-rolled into the LIST as `_pat`/`pat_` — which then matched
#: every `..._PATH` and stripped a leaf's native-library search path.
def looks_secret(name: str) -> bool:
    """Whether an env var name looks credential-bearing."""
    from personalclaw.workflows.secrets import matches_secret_hint

    return matches_secret_hint(name)


def presence_flags(spec: WorkspaceSpec, granted: dict[str, str] | None = None) -> dict[str, Any]:
    """What a cockpit may show about env: names and presence, never values.

    Three states per key, because they need different actions: declared-and-granted works,
    declared-but-not-granted needs a grant, inherited needs nothing. Collapsing them into
    "configured / not configured" would make the second look like the third.
    """
    granted = granted or {}
    out: dict[str, str] = {}
    for name, declared in spec.env.items():
        if declared is None:
            out[name] = "inherited"
        elif declared.startswith("{{secret:"):
            key = declared[len("{{secret:") :].rstrip("}").strip()
            out[name] = "granted" if key in granted else "declared_not_granted"
        else:
            out[name] = "literal"
    return {"env": out, "count": len(out)}


#: Marker-file directory for setup idempotency. Setup runs on EVERY resume by contract, so each step
#: has to guard itself — a `npm install` that re-runs is slow, but a `git clone` that re-runs
#: fails,
#: and a setup block that fails on resume makes resume unusable.
SETUP_MARKER_DIR = ".pclaw-setup"


def setup_marker(step: str) -> str:
    """The marker path for one setup step. Content-addressed by the step text, so editing the step
    re-runs it — a marker keyed by index would skip an edited step as though it had run."""
    import hashlib

    digest = hashlib.sha256((step or "").encode("utf-8")).hexdigest()[:16]
    return f"{SETUP_MARKER_DIR}/{digest}.done"


@dataclass
class Provisioning:
    """The ordered plan for standing a workspace up, and for tearing it down.

    Order is the contract: preserve → setup → run, and teardown → delete. `teardown` before
    deletion
    is the whole reason it exists — running it after would be running it against a directory
    that no
    longer holds the services or artifacts it was meant to stop and sync.
    """

    steps: list[str] = field(default_factory=list)
    teardown_steps: list[str] = field(default_factory=list)
    issues: list[SpecIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.fatal for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": list(self.steps),
            "teardown_steps": list(self.teardown_steps),
            "issues": [i.to_dict() for i in self.issues],
            "ok": self.ok,
        }


def plan_provisioning(
    spec: WorkspaceSpec, *, issues: list[SpecIssue] | None = None
) -> Provisioning:
    """The ordered provisioning plan for a declared workspace.

    `preserve_patterns` copy-in happens BEFORE setup, because setup is what needs the
    preserved files:
    an `npm install` that runs before `.npmrc` is copied in reaches for the wrong registry.
    """
    plan = Provisioning(issues=list(issues or []))
    if spec.mode is Mode.IN_PLACE:
        plan.steps.append("use the project workspace in place (no isolation)")
    elif spec.mode is Mode.WORKTREE:
        # The proven machinery, not a second implementation: `.worktrees/<id>` + `pclaw/task-*`
        # branches under the project's own `worktrees/` dir.
        plan.steps.append("create a git worktree via loop.worktree.add_worktree")
    elif spec.mode is Mode.CONTAINER:
        plan.steps.append("provision the declared container image")
    else:
        plan.steps.append("create a per-run scratch directory")

    for pattern in spec.preserve_patterns:
        plan.steps.append(f"copy in {pattern}")
    if spec.setup:
        plan.steps.append(f"run setup (guarded by {SETUP_MARKER_DIR}/ markers): {spec.setup}")
    if spec.teardown:
        plan.teardown_steps.append(f"run teardown BEFORE deletion: {spec.teardown}")
    if spec.isolated:
        plan.teardown_steps.append("commit outstanding work to a per-run branch")
        plan.teardown_steps.append("delete the workspace")
    return plan


# ── folder contracts (R18) ──


@dataclass
class FolderContract:
    """A `.folder.yaml` declaration.

    Every field is optional and unknown fields pass silently. That tolerance is the point:
    this reads
    a format that will grow, and a strict reader over an evolving format discards the data it was
    meant to protect — the 23-of-25-dropped-memories bug class.
    """

    role: str = ""
    lifecycle: Lifecycle = Lifecycle.TRANSIENT
    agent_writable: bool = False
    required_frontmatter: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    #: Fields the reader did not recognize, KEPT rather than dropped. A round-trip that
    #: silently lost
    #: them would corrupt a newer app's contract when an older core rewrote the file.
    unknown: dict[str, Any] = field(default_factory=dict)

    @property
    def ttl_days(self) -> int:
        return TTL_STAGING_DAYS if self.lifecycle is Lifecycle.TTL_STAGING else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "lifecycle": self.lifecycle.value,
            "agent_writable": self.agent_writable,
            "required_frontmatter": list(self.required_frontmatter),
            "defaults": dict(self.defaults),
            "ttl_days": self.ttl_days,
            **self.unknown,
        }


_KNOWN_CONTRACT_FIELDS = frozenset(
    {"role", "lifecycle", "agent_writable", "required_frontmatter", "defaults"}
)


def parse_folder_contract(raw: Any) -> tuple[FolderContract, list[SpecIssue]]:
    """Read a folder contract. Every problem is a WARNING — never fatal.

    An unparseable contract yields the default contract plus a warning, because the alternative is a
    directory that becomes unusable over a typo in a metadata file. `transient` as the fallback
    lifecycle is the safe direction: content that should have been permanent and got cleaned is
    recoverable from the run that made it, while content that should have been transient and
    persisted is a leak nobody notices.
    """
    if not isinstance(raw, dict):
        return FolderContract(), [
            SpecIssue("contract_not_object", "folder contract must be an object; using defaults")
        ]
    issues: list[SpecIssue] = []
    lifecycle = Lifecycle.TRANSIENT
    raw_lifecycle = str(raw.get("lifecycle", "") or "").strip().lower()
    if raw_lifecycle:
        try:
            lifecycle = Lifecycle(raw_lifecycle)
        except ValueError:
            issues.append(
                SpecIssue(
                    "unknown_lifecycle",
                    f"unknown lifecycle {raw_lifecycle!r}; treating as transient, which is the "
                    "recoverable direction to be wrong in",
                )
            )
    unknown = {k: v for k, v in raw.items() if k not in _KNOWN_CONTRACT_FIELDS and k != "lifecycle"}
    contract = FolderContract(
        role=str(raw.get("role", "") or ""),
        lifecycle=lifecycle,
        agent_writable=bool(raw.get("agent_writable", False)),
        required_frontmatter=[str(f) for f in (raw.get("required_frontmatter") or [])],
        defaults=dict(raw.get("defaults") or {}),
        unknown=unknown,
    )
    if contract.lifecycle is Lifecycle.IMMUTABLE and contract.agent_writable:
        issues.append(
            SpecIssue(
                "immutable_but_writable",
                "an immutable folder declaring agent_writable contradicts itself; treating as "
                "NOT writable, because the immutable declaration is the one with a safety purpose",
            )
        )
        contract.agent_writable = False
    return contract, issues


def may_write(contract: FolderContract) -> tuple[bool, str]:
    """Whether an agent may write here, and why not when it may not.

    Immutable refuses; everything else honors `agent_writable`, which DEFAULTS to False.
    Defaulting to
    writable would make every folder that forgot to declare a permission an open one, and the
    forgetting is the common case.
    """
    if contract.lifecycle is Lifecycle.IMMUTABLE:
        return (
            False,
            "folder is immutable (ingested reference material or a file-drop landing zone)",
        )
    if not contract.agent_writable:
        return False, "folder does not declare agent_writable"
    return True, ""


def validate_frontmatter(contract: FolderContract, frontmatter: dict[str, Any]) -> list[SpecIssue]:
    """Check required frontmatter. WARNINGS only, by design.

    A missing field is worth saying and not worth refusing over: rejecting the write would lose the
    content to protect a metadata convention, which inverts what the convention is for.
    """
    missing = [f for f in contract.required_frontmatter if f not in (frontmatter or {})]
    if not missing:
        return []
    return [
        SpecIssue(
            "missing_frontmatter",
            f"missing required frontmatter: {', '.join(sorted(missing))} — recorded, not refused",
        )
    ]

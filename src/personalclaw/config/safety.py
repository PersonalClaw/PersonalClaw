"""Config sections for the safety and trust surface: what the system may do unattended.

One domain, eight sections, and they are grouped because they are read together: an
autonomy decision consults its guardrails, a guardrail consults its budget and its breaker,
and the egress/auth/sandbox trio decides what the process may reach at all.

Fail-safe polarity is the invariant this file exists to keep visible — guard-class flags
resolve through ``_guard_flag`` (ambiguity ⇒ enabled) and exposure-class flags through
``_expose_flag`` (ambiguity ⇒ off). Mixing them is the defect; they are one import apart
here on purpose.

Deliberately NO ``from __future__ import annotations``: ``config/schema.py`` resolves a
STRING annotation by ``eval``-ing it in ``config.loader``'s namespace with a silent
``except: return str`` fallback, so postponed annotations here would degrade this file's
schema types to ``string`` without any error. Real type objects cannot take that path.
"""

from dataclasses import dataclass, field

from personalclaw.config.coercion import _meta


@dataclass
class EgressConfig:
    """Operator overrides for the outbound egress guard (``personalclaw.net``).

    The guard blocks non-public destinations by default (loopback / RFC-1918 /
    link-local / IMDS / multicast / reserved) on every agent fetch, connector scrape,
    and webhook. These fields let a self-hoster relax that for THEIR environment —
    e.g. a homelab user whose webhook legitimately targets a LAN service — without
    weakening the default. A deny always wins over an allow.
    """

    allow_hosts: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Allowed Egress Hosts",
            "Hosts (bare domain covers subdomains) permitted to be reached even if "
            "they resolve to a private/LAN address. For homelab webhooks/services on "
            "your own network. Applies to all egress surfaces. On the EXCLUSIVE "
            "surfaces this list is the only reach there is, not a waiver on top of "
            "the public internet: automated fetches (the net-fetch action) and "
            "outbound A2A calls can reach these hosts and nothing else, so leaving it "
            "empty means those surfaces reach nowhere.",
        ),
    )
    deny_hosts: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Denied Egress Hosts",
            "Hosts (bare domain covers subdomains) the agent must never reach, even "
            "if public. A deny always overrides an allow.",
        ),
    )
    allow_private: bool = field(
        default=False,
        metadata=_meta(
            "Allow Private Networks",
            "When true, egress to private/LAN addresses is permitted globally (not "
            "just allow_hosts). Only enable on a fully trusted network — it removes "
            "SSRF protection for the whole LAN.",
        ),
    )


@dataclass
class BudgetConfig:
    """Default spend ceilings for unattended work (AUTONOMY-GUARDRAILS §1.1).

    Zero means UNLIMITED for that dimension — the conservative default so an
    existing user's unattended work is never suddenly capped on upgrade. A
    ceiling bites the ``run`` scope (one goal-loop / cron fire) and the ``day``
    scope (all unattended spend for a calendar day, per the ``spend.json`` meter).
    Per-trigger overrides arrive with AUTOMATION-SUBSTRATE (Trigger.gates); until
    then these globals apply to every unattended run.
    """

    max_tokens_per_run: int = field(
        default=0,
        metadata=_meta(
            "Max Tokens / Run",
            "Token ceiling for a single unattended run (goal-loop cycle, cron fire, "
            "subagent). 0 = unlimited. At the ceiling the run pauses into needs-input.",
        ),
    )
    max_tokens_per_day: int = field(
        default=0,
        metadata=_meta(
            "Max Tokens / Day",
            "Token ceiling for ALL unattended spend in a calendar day (across every "
            "trigger). 0 = unlimited. At the ceiling further unattended runs are "
            "skipped + paused until the next day.",
        ),
    )
    max_dollars_per_day: float = field(
        default=0.0,
        metadata=_meta(
            "Max Dollars / Day",
            "Estimated-dollar ceiling for all unattended spend in a calendar day. "
            "0 = unlimited. Estimates use provider-reported usage where available, "
            "else a conservative heuristic.",
        ),
    )


@dataclass
class BreakerConfig:
    """Per-provider circuit-breaker tuning (AUTONOMY-GUARDRAILS §2.3).

    Consumed by the model-call chokepoint's breaker registry. Defaults match the
    breaker module's built-ins; a value here overrides them for every provider.
    """

    failure_threshold: int = field(
        default=5,
        metadata=_meta(
            "Breaker Failure Threshold",
            "Consecutive failures before a provider's circuit breaker OPENs (fails "
            "fast during an outage instead of stacking timeouts).",
        ),
    )
    recovery_secs: float = field(
        default=30.0,
        metadata=_meta(
            "Breaker Recovery Seconds",
            "How long an OPEN breaker waits before allowing one HALF_OPEN probe.",
        ),
    )


@dataclass
class AutonomyConfig:
    """Earned-autonomy rung ladder thresholds (AUTONOMY-GUARDRAILS §5).

    The evidence bar one action type must clear before ``guardrails/autonomy.py``
    *proposes* its next rung. These are the operator-wide defaults; a type that
    declares its own ``PromotionRule`` on its spec keeps it.

    None of these is guard-class: no threshold here can grant autonomy on its own,
    because promotion is always a user click and the derived record only decides
    whether a proposal is filed. Demotion ignores every threshold except
    ``cooldown_days`` — it is immediate on the first rejection.
    """

    clean_approvals: int = field(
        default=10,
        metadata=_meta(
            "Clean Approvals Required",
            "How many times you must approve an action type unchanged before "
            "PersonalClaw offers to let it run at the next rung.",
        ),
    )
    min_days: int = field(
        default=7,
        metadata=_meta(
            "Minimum Track-Record Days",
            "The approvals must be spread over at least this many days — ten "
            "approvals in one afternoon is not a track record.",
        ),
    )
    max_rejections: int = field(
        default=0,
        metadata=_meta(
            "Rejections Tolerated",
            "How many rejections, undos or 👎 an action type may have in the "
            "evidence window and still be offered a promotion. 0 means none.",
        ),
    )
    cooldown_days: int = field(
        default=14,
        metadata=_meta(
            "Demotion Cooldown (days)",
            "After an action type is demoted it cannot be offered a promotion "
            "again for this many days.",
        ),
    )
    evidence_window_days: int = field(
        default=30,
        metadata=_meta(
            "Evidence Window (days)",
            "How far back the derived track record looks. Nothing older counts, "
            "so a type has to keep earning its rung.",
        ),
    )


@dataclass
class GuardrailsConfig:
    """The personal safety-floor substrate (AUTONOMY-GUARDRAILS).

    A *personal* safety floor — one user, one gateway, config plus one policy
    check per seam. Session 1 shipped the model-call chokepoint (breaker + hard
    timeout + audit + typed output); Session 2 adds spend metering + the outbound
    scan mode. Later sessions add the denylist, incident kill switch, and named
    safety profiles.
    """

    budgets: BudgetConfig = field(
        default_factory=BudgetConfig,
        metadata=_meta("Budgets", "Default spend ceilings for unattended work."),
    )
    breaker: BreakerConfig = field(
        default_factory=BreakerConfig,
        metadata=_meta("Circuit Breaker", "Per-provider model-call breaker tuning."),
    )
    autonomy: AutonomyConfig = field(
        default_factory=AutonomyConfig,
        metadata=_meta(
            "Earned Autonomy",
            "The evidence bar an action type must clear before PersonalClaw offers "
            "to let it run at the next rung.",
        ),
    )
    scan_mode: str = field(
        default="redact",
        metadata=_meta(
            "Outbound Scan Mode",
            "How the model-call seam handles secrets/PII in an outbound prompt bound "
            "for a REMOTE provider: 'warn' (log + proceed), 'redact' (substitute + "
            "proceed), or 'block' (refuse the call). Local-only providers always warn "
            "(the content never leaves the machine).",
            enum=["warn", "redact", "block"],
            # Guard-class (§5): the default must never be the leaky 'warn' (which
            # would send secrets to a remote provider). A config typo falls back to
            # this default, so it must be SAFE. Enforced by test_guardrails_flags.py.
            guard_class=True,
            safe_values=["redact", "block"],
        ),
    )


@dataclass
class AuthConfigSection:
    """Owner-login settings (REMOTE-USER-AUTH C4).

    Login is **opt-in and off by default**. That default is load-bearing: a local install
    should keep working exactly as it does today — the `?token=` link, `personalclaw token`,
    the loopback paths — without anyone opting into a password. Turning this on ADDS a second
    issuer of the same session token; it never replaces the existing ones.

    The credential itself is NOT here. The username/hash live in `auth/credentials.json` and
    the TOTP secret in the credential store, because `config.json` is a settings file people
    read, diff and paste into issues.
    """

    login_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Enable Login",
            "Offer a username/password login page as an additional way in. Off by default; "
            "the local token link keeps working either way, and remains the escape hatch if "
            "login is ever misconfigured.",
        ),
    )
    session_ttl: str = field(
        default="30d",
        metadata=_meta(
            "Session Lifetime",
            "How long a browser session lasts before you log in again (e.g. 30d, 12h). "
            "Explicitly-minted CLI tokens are unaffected.",
        ),
    )
    require_totp: bool = field(
        default=False,
        metadata=_meta(
            "Require 2FA Code",
            "Also require a time-based code at login. Set the secret up first with "
            "`personalclaw auth totp setup`, or login will be impossible.",
        ),
    )
    lockout_threshold: int = field(
        default=5,
        metadata=_meta(
            "Lockout After",
            "Failed login attempts before logins are temporarily refused.",
        ),
    )
    lockout_window: str = field(
        default="15m",
        metadata=_meta(
            "Lockout Window",
            "How long the lockout lasts, and the window failures are counted over.",
        ),
    )


@dataclass
class SecurityConfig:
    """Security controls for the agent's shell access.

    The built-in credential-exfiltration / destructive-command denylist lives in
    :mod:`personalclaw.security` (always enforced, read-only). ``denied_commands``
    here holds USER-added regexes, appended to the built-ins at screening time.
    """

    denied_commands: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Denied Commands",
            "User-added regexes for shell commands the agent must never run, "
            "appended to the always-on built-in denylist. Matched case-insensitively "
            "against the full command string.",
        ),
    )
    egress: EgressConfig = field(
        default_factory=EgressConfig,
        metadata=_meta(
            "Egress Policy",
            "Operator overrides for the outbound network guard (allow/deny hosts, "
            "private-network opt-in).",
        ),
    )
    credential_keychain: bool = field(
        default=False,
        metadata=_meta(
            "Store Credentials in the OS Keychain",
            "Keep provider credentials in the operating system's secret service "
            "(macOS Keychain, Linux Secret Service, Windows Credential Locker) instead of "
            "~/.personalclaw/.env. Turning this on changes where NEW credentials are "
            "written; secrets already in .env stay readable until you run the "
            "'Move to keychain' action, which snapshots .env first and is reversible. "
            "A machine with no usable secret service keeps writing .env at mode 0600 and "
            "says so in doctor — the switch never invents a third location.",
        ),
    )
    autonomy_denylist: list[dict] = field(
        default_factory=list,
        metadata=_meta(
            "Autonomy Denylist",
            "Path/action deny rules for autonomous action-provider runs "
            "(AUTONOMY-GUARDRAILS §1.2). Each rule is "
            "{paths:[glob], actions:[class], verdict: block|needs_human}. Enforced at "
            "every action-dispatch seam, so an app-contributed provider inherits it. "
            "Composes with (never overrides) the always-on built-in denylists.",
        ),
    )


@dataclass
class SandboxConfig:
    """Resource ceilings applied to agent-influenced child processes (PHF-1).

    These are the numeric inputs to the post-exec ceiling shim
    (``personalclaw._spawn_exec_shim``): a soft NOFILE cap, a max-child-process count
    (RLIMIT_NPROC), and a max resident-set size in MB (RLIMIT_AS). The ``ResourceCeilings``
    profiles in ``sandbox.py`` translate these into the per-spawn policy — the ``tool``
    profile applies them fully with an OOM bias, ``session_host`` deliberately raises NOFILE
    to the inherited hard limit (an ACP host multiplexes many MCP pipes; a low cap causes
    EMFILE), and ``none`` applies nothing. ``0`` disables an individual limit.

    ``cgroup_scopes`` is the one non-numeric knob here: it opts into a SECOND, Linux-only
    enforcement tier that applies the same ceilings to a child's whole subtree via a
    transient systemd user scope. It is off by default and a no-op off Linux."""

    nofile: int = field(
        default=4096,
        metadata=_meta(
            "Sandbox Max Open Files",
            "Soft RLIMIT_NOFILE ceiling for agent-influenced child processes (bash tools, "
            "app backends, MCP servers). 0 disables the NOFILE cap. ACP session hosts are "
            "exempt — they raise NOFILE to the inherited hard limit to multiplex many MCP "
            "pipes without hitting EMFILE.",
        ),
    )
    max_pids: int = field(
        default=0,
        metadata=_meta(
            "Sandbox Max Processes",
            "RLIMIT_NPROC ceiling for agent child processes. 0 disables it (the default). "
            "NOTE: RLIMIT_NPROC is a PER-USER limit that counts ALL of the user's existing "
            "processes, not just this child's subtree — so an absolute cap can break a busy "
            "host (git worktree, npm) with 'cannot fork'. Real per-subtree fork-bomb "
            "containment is the opt-in cgroup tier, not this rlimit; leave 0 unless you know "
            "the host's process budget.",
        ),
    )
    max_rss_mb: int = field(
        default=0,
        metadata=_meta(
            "Sandbox Max Memory (MB)",
            "RLIMIT_AS ceiling in megabytes for an agent child's address space. 0 disables "
            "the memory cap (the default — RLIMIT_AS is coarse and can break memory-mapped "
            "toolchains, so it is opt-in).",
        ),
    )
    cgroup_scopes: bool = field(
        default=False,
        metadata=_meta(
            "Sandbox Cgroup Scopes (Linux)",
            "Opt into the second enforcement tier: wrap an agent-influenced spawn in a "
            "transient systemd user scope (systemd-run --user --scope) carrying TasksMax / "
            "MemoryMax / MemorySwapMax derived from the ceilings above, so they bound the "
            "child's whole subtree instead of one process. This is the real fork-bomb "
            "containment RLIMIT_NPROC cannot give. Linux-only and OFF by default — a no-op "
            "where a systemd user manager is unavailable (macOS, most containers), where it "
            "leaves the post-exec rlimit shim as the only tier.",
        ),
    )
    env_passthrough: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Child Env Passthrough",
            "Extra environment variable NAMES that hook, cron-script and bash-action "
            "children inherit from the gateway, on top of the minimal base "
            "(sandbox.CHILD_ENV_BASE_NAMES: PATH, locale, home-equivalents, proxy/CA "
            "settings and the three PERSONALCLAW_* vars). Everything else is withheld — a "
            "child does not inherit the gateway's environment. Declare a name here when a "
            "script legitimately needs it (e.g. SLACK_BOT_TOKEN for a notifier, a language "
            "runtime's variable); the withheld names are listed in the debug log at each "
            "spawn. Names matching the credential floor (AWS_SECRET*, AWS_SESSION*, "
            "SSH_AUTH_SOCK, GNUPGHOME, GIT_ASKPASS) are refused even when declared.",
        ),
    )

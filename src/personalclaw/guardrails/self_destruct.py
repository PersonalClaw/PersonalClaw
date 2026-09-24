"""Refuse an unattended action that would kill the gateway executing it (WF2AUT-14).

🔴 THE HAZARD IS REACHABLE TODAY, and nothing in the product said a word about it. A clock
trigger whose action is a `bash` command dispatches through
:meth:`GatewayOrchestrator._fire_store_trigger` → :func:`personalclaw.guardrails.denylist.
enforce_action` → :class:`BashActionProvider`, which runs `/bin/sh -c command` and screens
nothing itself. And `personalclaw stop` / `personalclaw restart` are **SERVICE-FIRST**: see
`cli_server._restart`, whose first act is `service_controller.restart_service()` — if a
launchd/systemd service manages the gateway, the CLI bounces *that service*, which is the very
process hosting the run. The runner dies mid-flight, so the `ScheduleRunStore` row never reaches
a terminal state and the fire reads afterwards as a HUNG run rather than as a self-inflicted
stop. The user is left debugging a phantom.

**Measured before a line was written.** The packaged baseline
(`security.baseline_denied_command_patterns`) carries exactly three lifecycle-adjacent regexes —
`.*personal.?claw restart.*`,
`.*personal.?claw gateway restart.*` and `.*\\b(kill|pkill|killall)\\b.*\\bpersonal[-.]?claw\\b.*`
— and they are matched against literal text. So:

* `personalclaw stop`, `personalclaw update` and `personalclaw service uninstall` were **not
  screened at all**, though every one of them ends the host process.
* the one spelling that *was* screened fell to a one-line respelling:
  `PC=personalclaw; $PC restart`, `personalclaw   restart` (two spaces), `$(which personalclaw)
  restart` — none of them match, all of them kill the runner.

A screening rule matched against literal text is the bypass class this guard exists to close, so
this module classifies the **EFFECT**: it resolves shell assignments, peels wrapper programs
(`sudo`, `env`, `nohup`, `sh -c`, `python -m`, `uv run`), reduces each simple command to the
program that will actually run, and asks what that program does to *this* process.

**DISTINCT from WF2AUT-9's liveness guard, deliberately.** `skip_if_active` (the signal at
`triggers/service.py:547`, computed by `_target_active_kwargs` at `triggers/service.py:815`)
answers *"is a second CONCURRENT fire of this trigger about to trample work already in
flight?"* — it defers a fire while a worktree is dirty or a lock file is held, and it is
**fail-OPEN** on purpose (a broken `git status` reads as not-busy rather than deferring
forever). This guard answers a different question about a *single* fire: *"will this one fire
destroy its own host?"* One is about concurrency between fires, the other about a fire's effect
on the process running it; one fails open, this one fails **closed**. Neither subsumes the other
— `skip_if_active` would happily admit the only fire in flight, and this guard is indifferent to
how many fires there are.

**Unattended only.** The same operation stays available when a human asks for it: restarting the
gateway is normal administration, and a guard that forbade it outright would be a worse product.
The predicate is the ONE unattendedness decision the codebase already has —
:func:`personalclaw.guardrails.policy.is_unattended_session` — not a second notion of the word,
and the refusal names the interactive path so the user knows where to go.

**Fail CLOSED, with a bounded closed region.** A guard that refuses too much is an outage, so
"cannot classify" is not "refuse everything": the closed region is commands that reach for our
own CLI, a service manager, a process killer, or an unresolvable program sitting next to a
lifecycle verb. `systemctl restart nginx`, `docker restart my-redis` and `personalclaw status`
are classified — confidently, as *no host effect* — and pass.

**What this is NOT.** Like the denylist it plugs into, this is defense-in-depth, not a sandbox.
`curl … | sh` and `run-script` pointing at a file whose contents we never read are
unclassifiable by construction; the OS child sandbox remains the containment story. This guard
closes the shape a scheduled automation actually reaches for.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

#: What a matched command does to the host process. `kind` is the effect class; `target` is
#: whether it lands on US (`self`), on something else (`other`), or could not be pinned down
#: (`unknown`). Only `other` is admitted.
_KIND_RESTART = "restart"
_KIND_STOP = "stop"
_KIND_REINSTALL = "reinstall"
_KIND_UPDATE = "update"
_KIND_UNKNOWN = "unknown"

#: The token a command substitution or an unexpandable `$VAR` collapses to. Deliberately not a
#: shell-legal word, so it can never be confused with a real program name.
_UNRESOLVED = "\x00unresolved\x00"

#: Programs that execute their remaining words, so the guard must look THROUGH them rather than
#: at them. `env`/`nice`/`timeout` also carry operands of their own, dropped in `_peel`.
_WRAPPERS = frozenset(
    {
        "sudo",
        "doas",
        "nohup",
        "setsid",
        "stdbuf",
        "command",
        "exec",
        "builtin",
        "eval",
        "nice",
        "ionice",
        "time",
        "timeout",
        "env",
        "xargs",
    }
)
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "busybox"})
_INTERPRETERS = frozenset({"python", "python3", "python3.12", "python3.13", "py"})
_RUNNERS = frozenset({"uv", "uvx", "pipx", "poetry", "hatch", "pdm", "rye", "npx"})

#: Programs whose arguments are DATA, never code. They cannot restart anything, so a mention of
#: our name inside one is prose (`echo "personalclaw restart is dangerous"`), not an attempt.
#: Listed because the generic suspicion rule below would otherwise refuse a log line.
_DATA_PROGRAMS = frozenset(
    {
        "echo",
        "printf",
        "cat",
        "head",
        "tail",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ag",
        "sed",
        "awk",
        "cut",
        "sort",
        "uniq",
        "wc",
        "jq",
        "yq",
        "ls",
        "stat",
        "date",
        "true",
        "false",
        "test",
        "basename",
        "dirname",
        "logger",
        "tee",
        "pgrep",
        "pidof",
        "ps",
    }
)

#: Our own CLI's TOP-LEVEL subcommands that end or replace the host process, from `cli.py`'s
#: parser (`update` :661, `stop` :664, `restart` :674, `service` :703). Keyed on the first
#: non-flag word ONLY, which is what keeps `personalclaw cron update`, `personalclaw agent
#: update` and `personalclaw skills install` — nested subcommands of the same spelling — out.
_CLI_EFFECTS: dict[str, str] = {
    "restart": _KIND_RESTART,
    "stop": _KIND_STOP,
    "update": _KIND_UPDATE,
}
#: `personalclaw service <action>`: install/uninstall both stop the running gateway.
_CLI_SERVICE_EFFECTS: dict[str, str] = {
    "install": _KIND_REINSTALL,
    "uninstall": _KIND_REINSTALL,
    "restart": _KIND_RESTART,
    "stop": _KIND_STOP,
    "start": _KIND_RESTART,
}

_SYSTEMCTL_EFFECTS: dict[str, str] = {
    "restart": _KIND_RESTART,
    "try-restart": _KIND_RESTART,
    "reload-or-restart": _KIND_RESTART,
    "try-reload-or-restart": _KIND_RESTART,
    "stop": _KIND_STOP,
    "kill": _KIND_STOP,
    "disable": _KIND_STOP,
    "mask": _KIND_STOP,
}
_LAUNCHCTL_EFFECTS: dict[str, str] = {
    "unload": _KIND_STOP,
    "bootout": _KIND_STOP,
    "remove": _KIND_STOP,
    "stop": _KIND_STOP,
    "kickstart": _KIND_RESTART,
    "load": _KIND_REINSTALL,
    "bootstrap": _KIND_REINSTALL,
}
_GENERIC_SERVICE_EFFECTS: dict[str, str] = {
    "restart": _KIND_RESTART,
    "stop": _KIND_STOP,
    "kill": _KIND_STOP,
    "rm": _KIND_STOP,
    "down": _KIND_STOP,
    "reload": _KIND_RESTART,
    "start": _KIND_RESTART,
}
#: Service/container/process managers. The value picks the verb vocabulary; the TARGET decides
#: whether the effect lands on us, which is what keeps `systemctl restart nginx` and
#: `docker restart my-redis` out of the refusal — a guard that over-refuses is an outage.
_MANAGERS: dict[str, dict[str, str]] = {
    "systemctl": _SYSTEMCTL_EFFECTS,
    "launchctl": _LAUNCHCTL_EFFECTS,
    "service": _GENERIC_SERVICE_EFFECTS,
    "rc-service": _GENERIC_SERVICE_EFFECTS,
    "sv": _GENERIC_SERVICE_EFFECTS,
    "supervisorctl": _GENERIC_SERVICE_EFFECTS,
    "brew": _GENERIC_SERVICE_EFFECTS,
    "docker": _GENERIC_SERVICE_EFFECTS,
    "podman": _GENERIC_SERVICE_EFFECTS,
    "nerdctl": _GENERIC_SERVICE_EFFECTS,
    "docker-compose": _GENERIC_SERVICE_EFFECTS,
    "pm2": _GENERIC_SERVICE_EFFECTS,
}
_KILLERS = frozenset({"kill", "pkill", "killall"})

#: Every lifecycle verb in one set, for the two region tests that have no resolved program to
#: consult (an unexpandable `$CMD`, an unparseable command).
_LIFECYCLE_WORDS = frozenset(
    set(_CLI_EFFECTS)
    | set(_CLI_SERVICE_EFFECTS)
    | set(_SYSTEMCTL_EFFECTS)
    | set(_LAUNCHCTL_EFFECTS)
    | set(_GENERIC_SERVICE_EFFECTS)
    | _KILLERS
    | {"uninstall", "reinstall", "upgrade", "self-update", "shutdown", "halt", "terminate"}
)

_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
_VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
#: `$(...)` and backticks. Their VALUE is a program we will never know, so they collapse to
#: `_UNRESOLVED` — which is the whole point: `$(which personalclaw) restart` must not pass.
_SUBST = re.compile(r"\$\([^()]*\)|`[^`]*`")
_OPERATORS = frozenset({";", "&&", "||", "|", "&", "(", ")", "{", "}", "\n", "|&"})


@dataclass(frozen=True)
class HostEffect:
    """One command's effect on the process that would run it."""

    kind: str = ""  # "" (none) | restart | stop | reinstall | update | unknown
    target: str = ""  # "" | self | other | unknown
    operation: str = ""  # legible: WHAT it tried to do ("personalclaw restart")
    evidence: str = ""  # the simple command that classified it

    @property
    def refuses(self) -> bool:
        """True when this effect must be refused: it hits US, or we could not tell."""
        return bool(self.kind) and self.target != "other"

    def reason(self) -> str:
        """The refusal text. Names the operation AND the interactive path (WF2AUT-14)."""
        interactive = (
            "run it yourself from an interactive session — the dashboard's Updates panel, or "
            "your own shell — where it is not a scheduled run killing its own runner"
        )
        if self.kind == _KIND_UNKNOWN:
            return (
                f"unattended action refused: its effect on the PersonalClaw gateway running it "
                f"could not be determined ({self.operation}), so it is refused closed rather "
                f"than guessed. If this is safe, {interactive}."
            )
        verb = {
            _KIND_RESTART: "restart",
            _KIND_STOP: "stop",
            _KIND_REINSTALL: "reinstall",
            _KIND_UPDATE: "update",
        }.get(self.kind, self.kind)
        return (
            f"unattended action refused: it would {verb} the PersonalClaw gateway that is "
            f"executing it ({self.operation}), killing this run mid-flight so it would never "
            f"reach a terminal state and would read afterwards as a hung run. To {verb} "
            f"PersonalClaw, {interactive}."
        )


def _norm(word: str) -> str:
    """Lowercase, strip everything that is not alphanumeric.

    So `personal-claw`, `personal_claw`, `PersonalClaw`, `io.personalclaw.gateway` and
    `personalclaw.service` all reduce to a form containing `personalclaw`. Identity by
    NORMALIZED CONTENT is what makes a respelling not a bypass.
    """
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _self_identities() -> tuple[str, ...]:
    """Normalized names that mean "this program / this service".

    Read from the real service constants rather than hardcoded, so renaming the launchd label
    or the systemd unit cannot silently narrow the guard. Imported lazily: `personalclaw.service`
    pulls in the platform controllers, and this module is imported on a dispatch hot path.
    """
    names = ["personalclaw"]
    try:
        from personalclaw.service.common import LAUNCHD_LABEL, SERVICE_NAME

        names += [SERVICE_NAME, LAUNCHD_LABEL]
    except Exception:  # pragma: no cover - constants are packaged; the floor still holds
        pass
    return tuple(dict.fromkeys(_norm(n) for n in names if n))


def _is_self_name(word: str) -> bool:
    """True when `word` names this program or its service.

    Containment on the normalized form, not equality: a launchd label, a systemd unit, an
    absolute path and a `--flag=value` all embed the name. A co-named unit
    (`personalclaw-backup.service`) reads as ours — the closed direction, and a human can still
    restart it interactively.
    """
    normalized = _norm(word)
    if not normalized:
        return False
    return any(ident and ident in normalized for ident in _self_identities())


def _basename(word: str) -> str:
    """The program name from a possibly-qualified word.

    `/usr/local/bin/personalclaw`, `./personalclaw` and `~/.local/bin/personalclaw.exe` all
    reduce to `personalclaw`; a respelled PATH is not a bypass.
    """
    base = os.path.basename(word.strip().strip("'\""))
    if base.endswith(".exe"):
        base = base[:-4]
    return base.lower()


def _expand(text: str, env: dict[str, str]) -> str:
    """Substitute `$VAR`/`${VAR}` from `env`; anything unknown becomes `_UNRESOLVED`.

    Bounded to three passes so a self-referential assignment cannot loop. A command
    substitution is replaced before this by `_SUBST` — its value is unknowable, and the guard
    must refuse rather than assume it is harmless.
    """
    out = _SUBST.sub(_UNRESOLVED, text)
    for _ in range(3):
        if "$" not in out:
            break

        def _one(m: "re.Match[str]") -> str:
            name = m.group(1) or m.group(2) or ""
            return env.get(name, _UNRESOLVED)

        nxt = _VAR_REF.sub(_one, out)
        if nxt == out:
            break
        out = nxt
    return out


def _tokenize(line: str) -> list[str] | None:
    """Shell-lex one line into words and operators. None when the line cannot be parsed."""
    import shlex

    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def _simple_commands(command: str, env: dict[str, str]) -> tuple[list[list[str]], bool]:
    """Split `command` into simple commands, expanding variables as it goes.

    Returns `(statements, parse_failed)`. Assignments seen at statement position feed `env`, so
    `PC=personalclaw; $PC restart` resolves — the variable indirection that defeats a literal
    text screen. Newlines split first: with `punctuation_chars` a newline is whitespace, so
    two lines would otherwise read as one command.
    """
    statements: list[list[str]] = []
    parse_failed = False
    for raw_line in command.replace("\\\n", " ").split("\n"):
        line = _expand(raw_line, env)
        if not line.strip():
            continue
        tokens = _tokenize(line)
        if tokens is None:
            parse_failed = True
            continue
        current: list[str] = []
        for tok in tokens:
            if tok in _OPERATORS:
                if current:
                    statements.append(current)
                current = []
                continue
            current.append(tok)
        if current:
            statements.append(current)
    # Record assignments so a LATER statement's `$VAR` resolves. Done after the split because
    # `VAR=value` is only an assignment when it sits at the head of a statement.
    for stmt in statements:
        for tok in stmt:
            m = _ASSIGN.match(tok)
            if m:
                env.setdefault(m.group(1), m.group(2))
            else:
                break
    return statements, parse_failed


def _drop_wrapper_operands(name: str, args: list[str]) -> list[str]:
    """Drop a wrapper's own flags/operands so what remains starts at the real program."""
    rest = list(args)
    while rest:
        head = rest[0]
        if _ASSIGN.match(head):  # `env FOO=1 cmd`, `VAR=1 cmd`
            rest.pop(0)
            continue
        if head.startswith("-"):
            rest.pop(0)
            # `sudo -u alice cmd`, `nice -n 5 cmd`: the flag takes a value.
            if head in ("-u", "-U", "-g", "-n", "-p", "-k", "-s", "--user", "--group") and rest:
                rest.pop(0)
            continue
        # `timeout 5 cmd`, `nice 5 cmd`: a bare duration/priority operand.
        if name in ("timeout", "nice", "ionice") and re.fullmatch(
            r"[0-9]+(\.[0-9]+)?[smhd]?", head
        ):
            rest.pop(0)
            continue
        break
    return rest


def _shell_inline(args: list[str]) -> str | None:
    """The script a shell was handed with `-c`, if any."""
    for i, tok in enumerate(args):
        if tok in ("-c", "-lc", "-cl") and i + 1 < len(args):
            return args[i + 1]
        if tok.startswith("-") and "c" in tok.lstrip("-") and i + 1 < len(args):
            return args[i + 1]
    return None


def _non_flags(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-")]


def _classify_manager(program: str, args: list[str], evidence: str) -> HostEffect | None:
    """A service/container/process manager: read its verb, then decide WHO it lands on."""
    verbs = _MANAGERS[program]
    words = _non_flags(args)
    # `brew services restart x` / `docker compose restart x`: the verb is one word deeper.
    if words and words[0] in ("services", "compose"):
        words = words[1:]
    verb = ""
    verb_at = -1
    for i, word in enumerate(words):
        if word in verbs:
            verb = word
            verb_at = i
            break
    if not verb:
        # `service personalclaw restart` (BSD/sysv order) is covered by the loop above; a
        # manager with no lifecycle verb at all (`systemctl status x`) has no host effect.
        return None
    targets = [w for w in words[:verb_at] + words[verb_at + 1 :]]
    kind = verbs[verb]
    if any(_is_self_name(t) for t in targets):
        return HostEffect(kind, "self", f"`{program} {verb}` on this gateway's service", evidence)
    if any(_UNRESOLVED in t for t in targets) or not targets:
        # An unresolvable or absent unit next to a lifecycle verb: could be us. Closed.
        return HostEffect(
            _KIND_UNKNOWN,
            "unknown",
            f"`{program} {verb}` on a target this guard could not resolve",
            evidence,
        )
    return HostEffect(kind, "other", f"`{program} {verb}` on {targets[0]}", evidence)


def _classify_killer(program: str, args: list[str], evidence: str) -> HostEffect | None:
    """`kill`/`pkill`/`killall`. A pid we can prove is not ours is `other`, not `unknown`."""
    words = _non_flags(args)
    if any(_is_self_name(w) for w in words):
        return HostEffect(_KIND_STOP, "self", f"`{program}` targeting this gateway", evidence)
    if any(_UNRESOLVED in w for w in words) or not words:
        return HostEffect(
            _KIND_UNKNOWN,
            "unknown",
            f"`{program}` targeting a process this guard could not resolve",
            evidence,
        )
    ours = {os.getpid(), os.getppid()}
    try:
        ours.add(os.getpgid(0))
    except OSError:  # pragma: no cover - no process group on exotic platforms
        pass
    for word in words:
        if word.lstrip("-").isdigit() and abs(int(word)) in ours:
            return HostEffect(_KIND_STOP, "self", f"`{program} {word}` (this process)", evidence)
    # Every target is a concrete name/pid that is demonstrably not us — classified, not guessed.
    return HostEffect(_KIND_STOP, "other", f"`{program} {words[0]}`", evidence)


def _classify_own_cli(args: list[str], evidence: str) -> HostEffect | None:
    """Our own CLI. Keyed on the FIRST non-flag word, so nested `cron update` is not `update`."""
    words = _non_flags(args)
    if not words:
        return None  # bare `personalclaw` prints help
    head = words[0]
    if _UNRESOLVED in head:
        return HostEffect(
            _KIND_UNKNOWN,
            "unknown",
            "`personalclaw <unresolved subcommand>`",
            evidence,
        )
    if head == "service":
        action = words[1] if len(words) > 1 else ""
        if _UNRESOLVED in action:
            return HostEffect(
                _KIND_UNKNOWN, "unknown", "`personalclaw service <unresolved>`", evidence
            )
        kind = _CLI_SERVICE_EFFECTS.get(action)
        if kind:
            return HostEffect(kind, "self", f"`personalclaw service {action}`", evidence)
        return None  # `personalclaw service status` only reads
    kind = _CLI_EFFECTS.get(head)
    if kind:
        return HostEffect(kind, "self", f"`personalclaw {head}`", evidence)
    if head == "gateway" and any(w == "restart" for w in words[1:]):
        return HostEffect(_KIND_RESTART, "self", "`personalclaw gateway restart`", evidence)
    return None  # `status`, `token`, `snapshot`, `doctor`, … read or write data, not the process


def _classify_statement(tokens: list[str], depth: int) -> HostEffect | None:
    """One simple command → its host effect, or None when it has none.

    Peels wrappers first (`sudo`/`env`/`nohup`/`timeout`), recurses through `sh -c`, and only
    then asks what the surviving program does. The peel is what makes an added wrapper — or a
    `cd` in front — not a bypass.
    """
    if depth > 4:
        return None
    evidence = " ".join(tokens).replace(_UNRESOLVED, "$?")
    args = list(tokens)
    for _ in range(6):
        while args and _ASSIGN.match(args[0]):
            args.pop(0)
        if not args:
            return None
        head = _basename(args[0])
        if head in _WRAPPERS:
            args = _drop_wrapper_operands(head, args[1:])
            continue
        if head in _SHELLS:
            inline = _shell_inline(args[1:])
            if inline is None:
                return None  # an interactive/stdin shell: unclassifiable by construction
            nested = classify_host_effect(inline, _depth=depth + 1)
            return nested if nested.kind else None
        if head in _INTERPRETERS or head in _RUNNERS:
            rest = _non_flags(args[1:])
            # `python -m personalclaw restart`, `uv run personalclaw stop`, `pipx run …`.
            for i, word in enumerate(rest):
                if _is_self_name(word):
                    return _classify_own_cli(rest[i + 1 :], evidence)
            if any(_UNRESOLVED in w for w in args[1:]):
                return HostEffect(
                    _KIND_UNKNOWN, "unknown", f"`{head}` running an unresolved target", evidence
                )
            return None
        break

    program = _basename(args[0])
    rest = args[1:]

    if _UNRESOLVED in args[0]:
        # The program slot itself is a `$VAR`/`$(...)` we could not resolve. Refuse only when
        # the command sits in lifecycle territory — `$EDITOR notes.md` is not this guard's
        # business, `$CMD restart` very much is.
        if any(w in _LIFECYCLE_WORDS for w in rest) or any(_is_self_name(w) for w in rest):
            return HostEffect(
                _KIND_UNKNOWN, "unknown", "an unresolved program next to a lifecycle verb", evidence
            )
        return None

    if _is_self_name(program):
        return _classify_own_cli(rest, evidence)
    if program in _MANAGERS:
        return _classify_manager(program, rest, evidence)
    if program in _KILLERS:
        return _classify_killer(program, rest, evidence)
    if program in _DATA_PROGRAMS:
        return None  # arguments are data; a mention of our name here is prose

    # An unrecognized program reaching for us BY NAME with a lifecycle verb beside it
    # (`pm2 restart personalclaw`, `some-wrapper stop personalclaw`). We cannot say what it
    # does, which is exactly when to refuse. The verb must be a whole WORD, so a quoted
    # sentence that happens to contain "restart" is not caught.
    if any(_is_self_name(w) for w in rest) and any(w in _LIFECYCLE_WORDS for w in rest):
        return HostEffect(
            _KIND_UNKNOWN,
            "unknown",
            f"`{program}` reaching for PersonalClaw with a lifecycle verb",
            evidence,
        )
    return None


def classify_host_effect(command: str, *, _depth: int = 0) -> HostEffect:
    """Classify `command`'s effect on THIS process. `HostEffect()` means none found.

    The first refusing statement wins, so `personalclaw doctor && personalclaw stop` is refused
    on its second command — an innocent prefix does not launder what follows.
    """
    text = command or ""
    if not text.strip():
        return HostEffect()
    statements, parse_failed = _simple_commands(text, {})
    for stmt in statements:
        effect = _classify_statement(stmt, _depth)
        if effect is not None and effect.refuses:
            return effect
    if parse_failed:
        # Unbalanced quoting: we hold no token stream to reason over. Refuse only if the raw
        # text is in lifecycle territory at all, so a broken `awk` script is not an outage.
        low = text.lower()
        if any(re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", low) for w in _LIFECYCLE_WORDS) or (
            _is_self_name(low)
        ):
            return HostEffect(
                _KIND_UNKNOWN,
                "unknown",
                "a command this guard could not parse (unbalanced quoting)",
                text[:120],
            )
    return HostEffect()


def unattended_host_effect(command: str, session_key: str = "") -> HostEffect | None:
    """The refusing `HostEffect` for an UNATTENDED `command`, or None when it may proceed.

    `session_key` is the same identity every dispatch seam already threads into the denylist.
    Unattendedness is :func:`personalclaw.guardrails.policy.is_unattended_session` and nothing
    else — one notion of the word, so a change there moves this guard with it. An EMPTY key is
    treated as unattended here (the closed direction), even though that function reads it as
    attended: a caller that cannot say who is running an action has not shown that a human is
    watching.
    """
    if not (command or "").strip():
        return None
    from personalclaw.guardrails.policy import is_unattended_session

    if session_key.strip() and not is_unattended_session(session_key):
        return None  # a human is watching: restarting the gateway is ordinary administration
    effect = classify_host_effect(command)
    return effect if effect.refuses else None

"""The shell denylist: the regexes every command PersonalClaw runs for an agent or an automation
is screened against, and :func:`denied_command`, the one check that applies them.

The packaged baseline (``baseline_denylist.json``) is read and verified here at import, healed in
memory on every read, and merged with the patterns the owner added. :mod:`personalclaw.security`
keeps the tool-name rules (``is_denied``) and the other built-in controls.
"""

import functools
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources

from personalclaw.sel import SecurityEvent, SecurityEventLog

logger = logging.getLogger(__name__)


# ── Bash denied-command regexes ──
# Credential-exfiltration and destructive-command regexes applied to every command
# PersonalClaw runs for an agent or an automation (``denied_command``). Distinct
# from BUILTIN_DENY_PATTERNS (fnmatch over TOOL NAMES) and SUSPICIOUS_BASH_PATTERNS
# (substring audit signals): these are full regexes matched against the command
# string, case-insensitively. This is the single source of truth — surfaced
# read-only in the Security settings panel; users add to it via
# ``AppConfig.security.denied_commands`` (merged at read time by
# ``denied_command_patterns()``), never by editing this list.
#
# The patterns themselves live in the packaged data file
# ``personalclaw/baseline_denylist.json`` (``{version, sha256, patterns[]}``) so this
# module and ``guardrails.denylist`` read ONE source instead of two in-code copies.
# The module-level list below is a loaded copy, re-asserted against the verified
# baseline on every read: an in-process mutation (a monkeypatch, a ``sitecustomize``,
# a stray ``.clear()``) is healed rather than silently obeyed. Categories shipped, in
# order: credential exfiltration, cloud-metadata SSRF, pipe-to-shell, destructive
# filesystem, destructive cloud, disk/partition writes, reverse shells, credential env
# export, destructive SQL, unreviewed pushes, credential-file reads, and self-tampering
# with the running gateway.
BASELINE_DENYLIST_FILE = "baseline_denylist.json"


def _baseline_digest(patterns: tuple[str, ...] | list[str]) -> str:
    """The canonical baseline fingerprint: sha256 over the newline-joined patterns.

    Content- and order-sensitive, so a removal, an edit and a reordering all show up.
    """
    return hashlib.sha256("\n".join(patterns).encode("utf-8")).hexdigest()


def _read_packaged_baseline() -> tuple[int, str, tuple[str, ...]]:
    """Read and verify the packaged baseline denylist.

    Raises on a missing file, malformed JSON, an empty pattern list, or a ``sha256``
    that disagrees with the patterns shipped alongside it. That is deliberate: the
    baseline is a required packaged asset, and a security module that cannot prove
    which commands it must refuse has to fail loudly at import rather than come up
    with a shorter (or empty) denylist. A packaging miss becomes a hard error instead
    of a silent bypass.
    """
    raw = resources.files("personalclaw").joinpath(BASELINE_DENYLIST_FILE).read_text("utf-8")
    doc = json.loads(raw)
    patterns = tuple(str(p) for p in doc["patterns"])
    if not patterns:
        raise ValueError("packaged baseline denylist ships no patterns")
    declared = str(doc["sha256"])
    actual = _baseline_digest(patterns)
    if actual != declared:
        raise ValueError(
            f"packaged baseline denylist integrity failure: declares {declared}, "
            f"content hashes to {actual}"
        )
    return int(doc["version"]), declared, patterns


#: The verified baseline, read once at import. ``_BASELINE_PATTERNS`` is a tuple so the
#: snapshot cannot be emptied in place, and ``_BASELINE_SHA256`` is the fingerprint every
#: later read is checked against. After import the *file* is no longer consulted for
#: content, so deleting or rewriting it on disk cannot shrink what is enforced — the
#: periodic re-verify reports the divergence instead of adopting it.
BASELINE_DENYLIST_VERSION, _BASELINE_SHA256, _BASELINE_PATTERNS = _read_packaged_baseline()

#: The live copy every consumer has always imported, kept a ``list`` for its readers.
#: Healed from ``_BASELINE_PATTERNS`` on every ``denied_command_patterns()`` read.
BUILTIN_DENIED_COMMAND_PATTERNS: list[str] = list(_BASELINE_PATTERNS)


#: Digests of broken baseline states already reported, so an unrecoverable one is logged
#: once instead of on every screened command. A heal needs no such guard: it repairs the
#: list, so the next read takes the silent fast path.
_BASELINE_TAMPER_REPORTED: set[str] = set()


def _note_baseline_tamper(digest: str) -> bool:
    """Record ``digest`` as reported; return True the first time only."""
    if digest in _BASELINE_TAMPER_REPORTED:
        return False
    _BASELINE_TAMPER_REPORTED.add(digest)
    return True


def _log_baseline_event(event_type: str, outcome: str, detail: str, metadata: dict) -> None:
    """Best-effort SEL write for a baseline heal or a rejected shrink.

    Audit failure must never break command screening, so this swallows and logs.
    """
    try:
        SecurityEventLog().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type=event_type,
                caller_identity="",
                agent="personalclaw",
                source="security",
                operation="denied_command_patterns",
                tool_kind="execute_bash",
                outcome=outcome,
                resources=detail,
                metadata=metadata,
            )
        )
    except Exception:  # pragma: no cover - audit must not break screening
        logger.debug("baseline denylist SEL write failed", exc_info=True)


def baseline_denied_command_patterns() -> tuple[str, ...]:
    """Return the verified baseline patterns, healing tampered in-memory state.

    The fast path is one sha256 over ~110 short strings and emits nothing, so a cold,
    untampered read is silent — only a digest mismatch does any work or logs anything.

    Repair order: the immutable in-process snapshot, then a fresh read of the packaged
    file if the snapshot itself was rebound. If neither verifies, the baseline is still
    not allowed to shrink — the union of every copy seen is returned (never fewer
    patterns) and the rejected shrink is logged as a tamper attempt.
    """
    global _BASELINE_PATTERNS
    live = BUILTIN_DENIED_COMMAND_PATTERNS
    if _baseline_digest(live) == _BASELINE_SHA256:
        return _BASELINE_PATTERNS

    good = _BASELINE_PATTERNS
    if _baseline_digest(good) != _BASELINE_SHA256:
        try:
            _, _, reread = _read_packaged_baseline()
        except Exception:
            reread = ()
        if _baseline_digest(reread) == _BASELINE_SHA256:
            good = reread
            _BASELINE_PATTERNS = reread
        else:
            union = tuple(dict.fromkeys(tuple(live) + tuple(good) + tuple(reread)))
            live[:] = list(union)
            # Unrecoverable state persists across reads, and a bash-heavy session reads
            # this on every command — log once per distinct broken state, not per read.
            if _note_baseline_tamper(_baseline_digest(union)):
                _log_baseline_event(
                    "baseline_denylist_tamper_attempt",
                    "rejected",
                    "no verified baseline source available",
                    {
                        "expected_sha256": _BASELINE_SHA256,
                        "effective_count": len(union),
                        "reason": "snapshot_and_packaged_file_both_unverified",
                    },
                )
            return union

    restored = [p for p in good if p not in live]
    live[:] = list(good)
    _log_baseline_event(
        "baseline_denylist_reasserted",
        "healed",
        f"restored {len(restored)} baseline pattern(s)",
        {
            "expected_sha256": _BASELINE_SHA256,
            "baseline_version": BASELINE_DENYLIST_VERSION,
            "baseline_count": len(good),
            "restored_count": len(restored),
            "restored_sample": restored[:5],
        },
    )
    return good


def denied_command_patterns() -> list[str]:
    """Return the effective bash denied-command regexes: the packaged baseline plus any
    user-configured additions from ``AppConfig.security.denied_commands``.

    The baseline is re-asserted first, so the result is always a superset of the packaged
    baseline — built-ins cannot be removed by config *or* by mutating the in-memory list.
    User patterns are appended and deduped against the baseline, so a user entry equal to
    a built-in is a no-op rather than a way to shorten the set. This is the single source
    :func:`denied_command` (every path that runs a command) and the Security panel read.
    """
    from personalclaw.config.loader import AppConfig

    baseline = baseline_denied_command_patterns()
    seen = set(baseline)
    additions: list[str] = []
    for pat in AppConfig.load().security.denied_commands:
        if isinstance(pat, str) and pat not in seen:
            seen.add(pat)
            additions.append(pat)
    return list(baseline) + additions


def verify_baseline_denylist() -> dict:
    """Re-verify the baseline against the packaged file on disk — the periodic probe.

    Heals in-memory drift the way every read does, then re-reads the packaged file and
    compares it to the fingerprint captured at import. A file that no longer matches is
    *not* adopted: the verified in-process baseline stays in force and the divergence is
    logged as a tamper attempt. This is what catches an edit that rewrote the patterns
    *and* the ``sha256`` together — self-consistent on disk, but not what we verified.
    """
    patterns = baseline_denied_command_patterns()
    file_ok = True
    file_detail = ""
    try:
        _, _, on_disk = _read_packaged_baseline()
        file_ok = _baseline_digest(on_disk) == _BASELINE_SHA256
        if not file_ok:
            file_detail = "packaged file no longer matches the verified baseline"
    except Exception as exc:
        file_ok = False
        file_detail = f"packaged file unreadable ({type(exc).__name__})"
    if not file_ok:
        _log_baseline_event(
            "baseline_denylist_tamper_attempt",
            "rejected",
            file_detail,
            {
                "expected_sha256": _BASELINE_SHA256,
                "baseline_version": BASELINE_DENYLIST_VERSION,
                "enforced_count": len(patterns),
                "reason": "packaged_file_diverged",
            },
        )
    return {
        "version": BASELINE_DENYLIST_VERSION,
        "sha256": _BASELINE_SHA256,
        "count": len(patterns),
        "file_verified": file_ok,
        "detail": file_detail,
    }


#: The baseline patterns that refuse a change to a security setting through the command-line config
#: command: `personalclaw config set|unset` on a security setting, a whole file applied with
#: `config set --file`, and `config edit`. An agent's shell could otherwise reach every security
#: setting the dashboard keeps to the owner (`config.edit_spec.app_write_refusal`) by typing the
#: command. Only the commands PersonalClaw runs are screened, so the owner's own terminal is
#: unaffected.
CLI_SECURITY_CONFIG_PATTERNS: tuple[str, ...] = tuple(
    p for p in _BASELINE_PATTERNS if r"claw\s+(?:-\S+\s+)*config\s+" in p
)
if len(CLI_SECURITY_CONFIG_PATTERNS) != 3:  # pragma: no cover - a packaging error
    raise ValueError("packaged baseline denylist does not carry the three config-command patterns")

#: The baseline pattern that refuses a push. Kept in the verified baseline whatever the owner's
#: Settings → Security → Git push switch says: with the switch on, :func:`denied_command` skips it
#: for one plain push (`branch_publish.allowed`) and asks every other pattern as before, so the
#: baseline's content and digest never change with the switch.
GIT_PUSH_BASELINE_PATTERN = r".*git\s+(-\S+\s+[^-]\S*\s+|-\S+\s+)*push(\s.*|$)"
if GIT_PUSH_BASELINE_PATTERN not in _BASELINE_PATTERNS:  # pragma: no cover - a packaging error
    raise ValueError("packaged baseline denylist does not carry its push pattern")

#: What a refusal by one of :data:`CLI_SECURITY_CONFIG_PATTERNS` adds: why, and who may.
CLI_SECURITY_CONFIG_REASON = (
    "a security setting is changed only by the owner, in Settings → Security or by running the "
    "config command in their own terminal"
)


@dataclass(frozen=True)
class DeniedCommand:
    """The shell denylist's refusal of one command: the pattern it matched, and whose it is.

    ``added`` is a pattern added under Settings → Security → Shell denylist ("Your patterns", by
    the owner or an import); otherwise ``pattern`` is one of the packaged built-in patterns. An
    empty ``pattern`` means the added patterns could not be read at all, which refuses every
    command.
    """

    pattern: str
    added: bool

    def why(self) -> str:
        """What the command matched, in the words every surface shows."""
        if not self.pattern:
            return (
                "the shell denylist under Settings → Security could not be read, so no command "
                "runs until it can"
            )
        whose = (
            "a pattern added to the shell denylist under Settings → Security"
            if self.added
            else "one of the shell denylist's built-in patterns"
        )
        if not self.added and self.pattern in CLI_SECURITY_CONFIG_PATTERNS:
            return f"the command matches `{self.pattern}`, {whose}: {CLI_SECURITY_CONFIG_REASON}"
        if not self.added and self.pattern == GIT_PUSH_BASELINE_PATTERN:
            from personalclaw.branch_publish import refusal_clause

            return f"the command matches `{self.pattern}`, {whose}: {refusal_clause()}"
        return f"the command matches `{self.pattern}`, {whose}"

    def refusal(self) -> str:
        """The refusal as one sentence: the reason, and that nothing ran."""
        return f"Blocked: {self.why()}. It was not run."


@functools.lru_cache(maxsize=1024)
def _search_form(pattern: str) -> "re.Pattern[str] | None":
    """*pattern* compiled for :func:`denied_command`'s search, or ``None`` when it does not compile.

    A pattern that begins with ``.*`` is searched without it. A search already tries every
    position, so ``.*X`` finds a match exactly when ``X`` does (``.*`` is no group, so the
    numbering of the rest is the same, and it can always match empty). The leading ``.*`` only
    costs time: tried from every position, each attempt runs to the end of the line before it
    backs off, so a pattern that finds no match took time in the square of the line's length.
    It is kept when a quantifier follows it (``.*?``), where it is not a whole atom, and nothing
    is changed for a pattern that does not compile as written, which matches nothing, as before.
    """
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None
    if pattern.startswith(".*") and pattern[2:3] not in ("*", "+", "?", "{"):
        return re.compile(pattern[2:], re.IGNORECASE)
    return compiled


def denied_command(command: str) -> DeniedCommand | None:
    """The shell denylist's answer for *command*: the rule it matches, or None to let it run.

    The one check every path that runs a command someone wrote asks before running it: the
    native bash tool, a command an agent CLI asks the host to run, a loop's or a workflow's
    check, a workflow step or teardown, a bash action however it started, an app's setup hook,
    and a tool run from Tools → Try it. ``tests/test_every_command_path_asks_the_denylist.py``
    fails a spawn site that runs such a command without asking.

    Matched case-insensitively against :func:`denied_command_patterns` (the verified baseline,
    then the added patterns), first match wins; a pattern that does not compile matches
    nothing. Fail-closed: when the added patterns cannot be read, every command is refused
    with that reason, rather than judged by the baseline alone as though nothing were added.

    The one exemption is the owner's Settings → Security → Git push switch: while it is on, the
    baseline's push pattern (:data:`GIT_PUSH_BASELINE_PATTERN`) does not refuse one plain push of
    a branch (``branch_publish.allowed``). Every other pattern, an added one included, is asked
    of it as of any command.
    """
    from personalclaw.branch_publish import allowed

    try:
        patterns = denied_command_patterns()
    except Exception:
        logger.warning("the shell denylist could not be read; refusing the command", exc_info=True)
        return DeniedCommand(pattern="", added=True)
    baseline = set(baseline_denied_command_patterns())
    for pat in patterns:
        search = _search_form(pat)
        if search is None or not search.search(command):
            continue
        if pat == GIT_PUSH_BASELINE_PATTERN and allowed(command):
            continue
        return DeniedCommand(pattern=pat, added=pat not in baseline)
    return None

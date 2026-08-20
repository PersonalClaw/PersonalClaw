"""Built-in security controls — deny list, sensitive path protection, and audit scanning."""

import fnmatch
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs

from personalclaw.sel import SecurityEvent, SecurityEventLog

logger = logging.getLogger(__name__)

# ── Built-in Deny Patterns ──
# These are always enforced regardless of user config.
# Patterns use fnmatch (case-insensitive): * matches anything.

BUILTIN_DENY_PATTERNS: list[str] = [
    # Credential / secret access — only explicit secret-fetching tool names.
    # Credential file access is handled by the OS-level sandbox (sandbox.py)
    # which bind-mounts empty dirs over ~/.aws, ~/.gnupg, etc., and by
    # deniedCommands in the ACP agent config.  Broad "*credential*"
    # patterns caused false positives on package names (e.g.
    # CredentialValidatorServiceCDK, credential-rotation-service).
    "get_secret*",
    "read_secret*",
    # Destructive AWS operations
    "*delete_stack*",
    "*terminate_instance*",
    "*drop_table*",
    "*delete_bucket*",
    # Git push (should be explicit)
    "*git*push*",
]

# Exceptions keyed by the deny pattern they apply to. If an input matches
# a deny pattern AND one of that pattern's exceptions, the deny is skipped.
# This avoids a blanket allowlist that could bypass unrelated deny rules.
# Exceptions are NOT applied when the input contains command separators
# (;, &&, ||, |, newlines) to prevent chaining bypasses.
_DENY_EXCEPTIONS: dict[str, list[str]] = {
    "*git*push*": ["* stash push*"],
}

_CMD_SEPARATOR_RE = re.compile(r"[;\n`]|\|\|?|&&|\$\(")

# ── Sensitive Paths ──
# Directories and files that must never be read by the agent.
# Patterns are resolved relative to $HOME at check time.

_SENSITIVE_HOME_DIRS: list[str] = [
    ".aws",
    ".ssh",
    ".gnupg",
    ".gpg",
    ".config/gcloud",
    ".azure",
    ".docker/config.json",
    ".kube/config",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    ".personalclaw/.env",
    # The governance ceiling (guardrails/ceiling.py) — the operator's hard bound on every
    # run. Listed here so every agent-reachable path check (the action denylist, the files
    # area, the bash read/write hooks) refuses it: a bound the agent can rewrite is not a
    # bound. This closes the write paths a single-user machine CAN close; the stronger
    # protection is PERSONALCLAW_CEILING_FILE pointing at a root-owned file outside $HOME.
    ".personalclaw/governance",
]

# Regex for bash commands that read sensitive paths, followed by a path containing any
# sensitive dir. The list is every command that RETURNS FILE CONTENT (or copies it
# somewhere the agent can read), not every command that opens a file: what matters is
# whether the bytes come back.
#
# 🔴 Measured, because the original fifteen were not enough. Against the shipped guard,
# 15 of 18 content-returning forms passed: `grep -a . ~/.ssh/id_rsa`, `awk '{print}'
# ~/.aws/credentials`, `sed -n 1,99p ~/.netrc`, `od`, `hexdump`, `nl`, `cut`, `sort`,
# `wc`, `diff`, `tar cf - ~/.gnupg`, `rsync -a ~/.ssh/`, `jq . ~/.docker/config.json`,
# `bat`, and a python one-liner using `read_text()` instead of `open()`. Every one of
# those returns the same bytes `cat` is blocked from — the guard was enumerating the
# tools someone thought of rather than the capability.
#
# Adding a command here can only ever block MORE, and only when the command also names a
# sensitive path: `grep -r pattern .` is untouched, `grep pattern ~/.ssh/id_rsa` is not.
_READ_CMDS = (
    r"(?:cat|bat|head|tail|less|more|strings|xxd|od|hexdump|nl|base64|cp|scp|rsync|tar|"
    r"zip|gzip|dd|grep|egrep|fgrep|rg|ag|awk|sed|cut|paste|tr|sort|uniq|wc|jq|yq|diff|"
    r"cmp|open|vi|vim|nano|emacs|code)\s"
)

# An INTERPRETER invocation whose command line names a sensitive path. Deliberately broader
# than the read verbs it replaces: the old form required `open(` to appear BEFORE the path,
# so `python -c "...Path('~/.ssh/id_rsa').expanduser().read_text()"` — where the path comes
# first — passed, and so did every `node -e "fs.readFileSync(...)"`. Enumerating read verbs
# in five languages is a losing game; naming the interpreters is not.
#
# It over-blocks a one-liner that merely MENTIONS a credential path without reading it. That
# is the fail-closed direction on a narrow input, and the refusal is visible with a reason —
# where the alternative is a read that succeeds and looks like nothing happened.
_SCRIPT_OPEN = r"(?:python|ruby|perl|node|deno|bun|php|osascript)\S*\s"


#: How each language spells "the user's home" when it builds the path instead of writing it.
#: Measured need: `node -e "...readFileSync(process.env.HOME+'/.ssh/id_rsa')"` named no `~`,
#: no `$HOME` and no literal home path, so the guard saw nothing to match.
_HOME_EXPRESSIONS = (
    "$HOME",
    "~",
    "process.env.HOME",
    "os.environ[HOME]",
    "os.path.expanduser",
    "Path.home()",
    "os.homedir()",
    "ENV[HOME]",
    "%USERPROFILE%",
    "$env:USERPROFILE",
)


def strip_shell_quotes(command: str) -> str:
    """Remove quote characters so a quoted respelling reads as the path it is.

    🔴 Measured against the shipped guard: `cat ~/'.ssh'/id_rsa` and `cat ~/.s''sh/id_rsa`
    were both ALLOWED, and both are ordinary shell that reads the file — the quotes are
    invisible to the shell and opaque to a regex. Dropping them first collapses that whole
    family into the plain spelling.

    It cannot close the CONCATENATION family (`'/.s' + 'sh/id_rsa'`, `$'\x2e'ssh`): no regex
    over a command string can, because the string that names the file never exists in the
    text. That is the documented limit of this control and the reason the OS sandbox
    bind-mounts empty dirs over `~/.aws`, `~/.gnupg` and friends — the guard here is
    defence in depth, not the fence.
    """
    return command.replace("'", "").replace('"', "").replace("\\", "")


def _build_sensitive_regex() -> re.Pattern[str]:
    """Build a compiled regex matching bash reads of sensitive paths."""
    home = str(Path.home())
    home_alts = "(?:" + "|".join(re.escape(h) for h in (home, *_HOME_EXPRESSIONS)) + ")"
    escaped_dirs = [re.escape(d) for d in _SENSITIVE_HOME_DIRS]
    dirs_pattern = "|".join(escaped_dirs)
    # `[+,\s]*` between the home expression and the slash: a built path joins them with a
    # concatenation operator or a comma rather than writing them adjacent.
    return re.compile(
        rf"(?:{_READ_CMDS}.*|{_SCRIPT_OPEN}.*|.*[<>|]\s*){home_alts}[+,\s]*/(?:{dirs_pattern})"
        rf"(?:/|\s|$|['\"),])",
        re.IGNORECASE,
    )


_SENSITIVE_RE: re.Pattern[str] | None = None


def _get_sensitive_re() -> re.Pattern[str]:
    global _SENSITIVE_RE
    if _SENSITIVE_RE is None:
        _SENSITIVE_RE = _build_sensitive_regex()
    return _SENSITIVE_RE


def is_sensitive_path(path_str: str) -> bool:
    """Return True if the path points to a sensitive location.

    Works for both absolute paths and ~/relative paths.
    Used by hooks to block fs_read/ReadFile of credential files.
    """
    # Expand ~ and $HOME
    expanded = os.path.expanduser(os.path.expandvars(path_str))
    try:
        resolved = str(Path(expanded).resolve())
    except (OSError, ValueError):
        resolved = expanded

    try:
        home = str(Path.home().resolve())
    except (OSError, ValueError):
        home = str(Path.home())
    # CASE-INSENSITIVE comparison, because the comparison is the control.
    #
    # 🔴 Measured on macOS: `~/.SSH/id_rsa` was ALLOWED while `~/.ssh/id_rsa` was blocked,
    # and the default macOS filesystem (like Windows) is case-INSENSITIVE — a temp dir
    # created as `.ssh` was read back through `.SSH` and returned the file's contents. So
    # every one of the fourteen entries above, INCLUDING `~/.personalclaw/.env` and the
    # governance ceiling, was one shifted key away from being readable, across the ~70 call
    # sites that route through this function. `Path.resolve()` normalises `..`, `.`, `//`,
    # `~` and `$HOME` (all verified blocked); it does not normalise case.
    #
    # Always casefold rather than probing the filesystem per call: a per-path probe is
    # itself a control that fails when the probe fails, and on a case-SENSITIVE filesystem
    # casefolding can only over-block — a directory literally named `~/.SSH` that holds no
    # credentials would be refused, which is the safe direction for a credential guard and
    # the error a user can see and report.
    resolved_cmp = resolved.casefold()
    for sensitive_dir in _SENSITIVE_HOME_DIRS:
        sensitive_path = os.path.join(home, sensitive_dir).casefold()
        if resolved_cmp == sensitive_path or resolved_cmp.startswith(sensitive_path + os.sep):
            return True
    return False


# OS-managed roots that must never be created into / used as a workspace. Two tiers:
#   _SYSTEM_SUBTREES — the whole tree is off-limits (/etc, /usr, /System, …), children
#                      included.
#   _SYSTEM_PARENTS  — only the bare dir is off-limits; children are legitimate
#                      (/Volumes/<disk>/repo, a macOS /private/var/folders/<tmp>, /var/<x>).
# macOS realpaths /etc → /private/etc, /var → /private/var; callers resolve the path
# BEFORE this check, so the /private/* canonical forms are included. /private/var is a
# PARENT (not a subtree) because macOS user temp dirs (incl. pytest tmp_path) live under
# /private/var/folders. Single source of truth — both the Code workspace validation and
# the create-dir / browse-dirs handlers call this so the surfaces can never drift.
_SYSTEM_SUBTREES: tuple[str, ...] = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/root",
    "/System",
    "/Library",
    "/Applications",
    "/cores",
    "/Network",
    "/private/etc",
    "/private/usr",
    "/private/var/root",
)
_SYSTEM_PARENTS: tuple[str, ...] = (
    "/",
    "/Volumes",
    "/private",
    "/var",
    "/opt",
    "/mnt",
    "/media",
    "/private/var",
    "/private/tmp",
    "/tmp",
)


def is_system_path(path_str: str) -> bool:
    """True if *path_str* resolves to an OS/system root a user must never create into
    or bind as a workspace. Whole-subtree roots reject their children; mount/temp
    parents reject only the bare dir (children like /Volumes/disk/repo are fine).

    Resolves ~ and symlinks first, so ``..``/symlink forms can't bypass the check.
    Single source of truth shared by the Code workspace validation and the file
    handlers, so those surfaces can never drift apart on what counts as a system path.
    """
    expanded = os.path.expanduser(os.path.expandvars(path_str or ""))
    try:
        resolved = str(Path(expanded).resolve())
    except (OSError, ValueError):
        resolved = expanded
    if not resolved:
        return True
    # Casefolded for the same reason as `is_sensitive_path` — measured: `/etc/passwd` was
    # blocked (macOS resolves `/etc` through a symlink, which normalises the case as a side
    # effect) while `/SYSTEM/x` and `/USR/bin/x` were ALLOWED. Two roots out of the set
    # behaving differently from the rest is the tell that the comparison, not the set, is
    # the bug.
    resolved_cmp = resolved.casefold()
    if resolved_cmp in {p.casefold() for p in _SYSTEM_PARENTS}:
        return True
    for root in _SYSTEM_SUBTREES:
        root_cmp = root.casefold()
        if resolved_cmp == root_cmp or resolved_cmp.startswith(root_cmp + os.sep):
            return True
    return False


#: Chain separators, the same set the deny path uses — one vocabulary for "more than one command".
_CHAIN_SPLIT_RE = re.compile(r"(?:&&|\|\||;|\||\n)")

#: A segment that moves the shell to the user's home: `cd`, `cd ~`, `cd $HOME`, `cd "${HOME}"`.
_HOME_CD_RE = re.compile(r"^\s*cd\s*(?:~|\$HOME|\$\{HOME\})?\s*$")

#: A dot-relative path that would be sensitive if it were home-relative (`.ssh/id_rsa`).
_DOT_RELATIVE_RE = re.compile(r"(?<![\w/~$.])(\.[A-Za-z0-9_.-]+/)")


def _normalise_for_matching(command: str) -> str:
    """Rewrite spellings that name a sensitive path without spelling it the guard's way.

    Two respellings reached credentials past the regex, both measured against the shipped guard:

    * ``cat ${HOME}/.ssh/id_rsa`` — ALLOWED, while the unbraced ``$HOME`` form was blocked. The
      brace is pure syntax, so it is collapsed before matching.
    * ``cd ~ && cat .ssh/id_rsa`` — ALLOWED. The path never appears home-qualified in the text;
      the ``cd`` put the shell there. When a segment of the chain moves to home, later segments'
      dot-relative paths are rewritten as home-relative so the existing patterns see them.

    Only ever makes the guard block MORE, and only when a home-cd is actually present: without
    one, a dot-relative path is left exactly as written.
    """
    text = re.sub(r"\$\{(\w+)\}", r"$\1", command)
    segments = _CHAIN_SPLIT_RE.split(text)
    if not any(_HOME_CD_RE.match(seg) for seg in segments):
        return text
    out: list[str] = []
    at_home = False
    for seg in segments:
        if _HOME_CD_RE.match(seg):
            at_home = True
            out.append(seg)
            continue
        out.append(_DOT_RELATIVE_RE.sub(r"~/\1", seg) if at_home else seg)
    return " && ".join(out)


def is_sensitive_bash_command(command: str) -> str | None:
    """Check if a bash command reads sensitive paths.

    Returns denial reason string, or None if clean.
    """
    if _get_sensitive_re().search(strip_shell_quotes(_normalise_for_matching(command))):
        return "Blocked: command accesses sensitive credential path"
    return None


# ── URL Exfiltration Detection ──
# Detects URLs whose query strings contain credential-like data.
# Domain-agnostic: we flag the PAYLOAD, not the destination.
# Any URL with secrets in query params is suspicious regardless of domain.

_URL_RE = re.compile(r"https?://([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})(:\d+)?(/[^\s)\"'>]*)?")

# Query string length threshold — normal URLs rarely exceed this
_EXFIL_QUERY_MIN_LEN = 200

# Patterns that indicate secrets or encoded data in query params
_EXFIL_PATTERNS = re.compile(
    r"(?:"
    r"[A-Za-z0-9+/=]{40,}"  # base64-like blob (40+ chars)
    r"|%[0-9A-Fa-f]{2}(?:%[0-9A-Fa-f]{2}){20,}"  # heavy URL-encoding (20+ encoded chars)
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}"  # AWS access key ID
    r"|(?:ssh-rsa|ssh-ed25519)[\s+%]"  # SSH public key
    r"|BEGIN[\s+%](?:RSA|DSA|EC|OPENSSH)[\s+%]PRIVATE[\s+%]KEY"  # private key header
    r"|xox[bpas]-[0-9a-zA-Z-]+"  # Slack token
    r")",
    re.IGNORECASE,
)

# S3 presigned URLs contain X-Amz-Signature (a 64-char hex string) that
# matches the base64-like blob pattern above.  These are intentional
# time-limited access tokens, not leaked credentials.  Skip the exfil
# check when ALL standard presigned-URL query params are present on an
# amazonaws.com domain.  Values are validated to prevent spoofing.
_S3_PRESIGNED_RE = re.compile(
    r"X-Amz-Algorithm=AWS4-HMAC-SHA256"
    r".*X-Amz-Credential=(?:AKIA|ASIA)[A-Z0-9]{16}(?:%2F|/)"
    r".*X-Amz-Expires=\d{1,6}"
    r".*X-Amz-Signature=[0-9a-f]{64}",
    re.IGNORECASE,
)

# Only these parameter keys are allowed in a presigned URL.  Any extra
# keys cause the fast-path to reject, falling through to normal checks.
_S3_PRESIGNED_PARAMS = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-SignedHeaders",
        "X-Amz-Signature",
        "X-Amz-Security-Token",
    }
)


# Structural validators for presigned param values that would otherwise
# false-positive against _EXFIL_PATTERNS.  Each value is validated rather
# than exempted, so attacker-controlled data cannot be smuggled through.
_STS_TOKEN_RE = re.compile(r"^(?:FwoGZX|IQoJb3JpZ2lu)[A-Za-z0-9+/=%]{1,2000}$")
_CREDENTIAL_RE = re.compile(
    r"^(?:AKIA|ASIA)[A-Z0-9]{16}(?:%2F|/)[0-9]{8}"
    r"(?:%2F|/)[a-z0-9-]+(?:%2F|/)s3(?:%2F|/)aws4_request$"
)
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")

_STRUCTURAL_VALIDATORS = {
    "X-Amz-Credential": _CREDENTIAL_RE,
    "X-Amz-Signature": _SIGNATURE_RE,
    "X-Amz-Security-Token": _STS_TOKEN_RE,
}


def _is_safe_presigned(domain: str, query: str) -> bool:
    """Return True if the URL is a valid S3 presigned URL with no extra parameters."""
    if not domain.endswith(".amazonaws.com"):
        return False
    if not _S3_PRESIGNED_RE.search(query):
        return False
    params = parse_qs(query, keep_blank_values=True)
    if not _S3_PRESIGNED_PARAMS.issuperset(params.keys()):
        return False
    # Structurally validate params that would false-positive against
    # _EXFIL_PATTERNS.  No values are fully exempt — each is checked.
    for key, values in params.items():
        validator = _STRUCTURAL_VALIDATORS.get(key)
        if validator:
            for val in values:
                if not validator.match(val):
                    return False
        else:
            for val in values:
                if _EXFIL_PATTERNS.search(val):
                    return False
    return True


# Safe domains — exempt from query-length heuristic.
# Credential patterns (_EXFIL_PATTERNS) still apply to all domains.
# Note: .amazonaws.com is NOT in this list (anyone can provision buckets).
# S3 presigned URLs on .amazonaws.com are handled by _is_safe_presigned().
_SAFE_DOMAIN_SUFFIXES: tuple[str, ...] = ()


def scan_exfiltration_urls(text: str) -> list[str]:
    """Scan text for URLs that may be exfiltrating data via query params.

    Domain-agnostic — only inspects query string content for secret patterns.
    Returns list of warning strings, empty if clean.
    """
    warnings: list[str] = []
    for match in _URL_RE.finditer(text):
        domain = match.group(1)
        path_and_query = match.group(3) or ""
        qmark = path_and_query.find("?")
        if qmark == -1:
            continue

        query = path_and_query[qmark + 1 :]

        # Trusted/allowlisted domains: only flag credential patterns, skip length check
        if any(domain.endswith(s) for s in _SAFE_DOMAIN_SUFFIXES):
            if _EXFIL_PATTERNS.search(query):
                warnings.append(f"Suspicious URL with credential-like query data: {domain}")
            continue

        if len(query) >= _EXFIL_QUERY_MIN_LEN:
            # S3 presigned URLs on amazonaws.com have long queries but are safe
            if _is_safe_presigned(domain, query):
                continue
            warnings.append(
                f"Suspicious URL with long query params ({len(query)} chars): "
                f"{domain}{path_and_query[:60]}..."
            )
        elif _EXFIL_PATTERNS.search(query):
            # S3 presigned URLs on amazonaws.com match the blob pattern but are safe
            if _is_safe_presigned(domain, query):
                continue
            warnings.append(f"Suspicious URL with credential-like query data: {domain}")
    return warnings


def redact_exfiltration_urls(text: str) -> tuple[str, list[str]]:
    """Scan and redact suspicious exfiltration URLs from text.

    Returns (cleaned_text, list_of_warnings).
    """
    warnings = scan_exfiltration_urls(text)
    if not warnings:
        return text, []

    result = text
    for match in _URL_RE.finditer(text):
        domain = match.group(1)
        full_url = match.group(0)
        path_and_query = match.group(3) or ""
        qmark = path_and_query.find("?")
        if qmark == -1:
            continue

        query = path_and_query[qmark + 1 :]

        # Trusted/allowlisted domains: only redact credential patterns, not long queries
        if any(domain.endswith(s) for s in _SAFE_DOMAIN_SUFFIXES):
            if _EXFIL_PATTERNS.search(query):
                result = result.replace(full_url, f"[REDACTED: suspicious URL to {domain}]")
            continue

        if len(query) >= _EXFIL_QUERY_MIN_LEN or _EXFIL_PATTERNS.search(query):
            # S3 presigned URLs on amazonaws.com are safe — don't redact
            if _is_safe_presigned(domain, query):
                continue
            result = result.replace(full_url, f"[REDACTED: suspicious URL to {domain}]")

    return result, warnings


# ── Credential Output Redaction ──
# Catches raw credential patterns in LLM output / tool results,
# including base64-encoded variants.  Applied on all output paths
# alongside redact_exfiltration_urls().

_CREDENTIAL_PATTERNS = re.compile(
    r"(?:"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}"  # AWS access key ID
    r"|(?:SecretAccessKey|aws_secret_access_key)\s*[:=]\s*\S+"
    r"|(?:SessionToken|aws_session_token)\s*[:=]\s*\S+"
    r"|(?:AccessKeyId|aws_access_key_id)\s*[:=]\s*\S+"
    r"|BEGIN[\s](?:RSA|DSA|EC|OPENSSH)[\s]PRIVATE[\s]KEY"
    r"|xox[bpas]-[0-9a-zA-Z-]{10,}"  # Slack token
    # LLM provider API keys. These are the credentials THIS project's users actually
    # hold — an Anthropic or OpenAI key pasted into a chat was previously invisible to
    # this redactor, so it survived into any surface that redacts on the way out
    # (session search results, inbound tool output). Found by the inbound
    # sessions_search redaction test.
    r"|sk-ant-(?:api|admin)[0-9]{2}-[A-Za-z0-9_-]{20,}"  # Anthropic
    r"|sk-proj-[A-Za-z0-9_-]{20,}"  # OpenAI project key
    r"|sk-[A-Za-z0-9]{32,}"  # OpenAI classic / compatible
    r"|gh[pousr]_[A-Za-z0-9]{20,}"  # GitHub token
    r"|AIza[0-9A-Za-z_-]{35}"  # Google API key
    # Measured while wiring the ConfirmationRequest preview (S57): the patterns above missed
    # THREE shapes that a real payload carries. `sk-[A-Za-z0-9]{32,}` cannot match a key with
    # hyphens or underscores in the body (`sk-live-ABC...`), and there was no generic
    # assignment or bearer form at all — so `api_key=<anything>` and
    # `Authorization: Bearer <jwt>` both survived into a redacted preview. A preview is the
    # single most likely place for a fetched credential to reach an inbox row.
    r"|sk-[A-Za-z0-9][A-Za-z0-9_-]{20,}"  # provider keys with hyphens/underscores in the body
    # Generic `key = value` credential assignment. Keyed on the NAME so the value's shape does
    # not have to be guessed — an unknown provider's key format is exactly what a shape-based
    # pattern misses.
    r"|(?i:api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret"
    r"|password|passwd|private[_-]?key)\s*[:=]\s*[^\s,;'\"]{8,}"
    # `Authorization: Bearer <token>` / a bare bearer token.
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/-]{16,}=*"
    r")",
)

# Base64 alphabet: at least 40 chars of [A-Za-z0-9+/] ending with optional =
_B64_CHUNK_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def _decode_b64_safe(text: str) -> str:
    """Try to base64-decode chunks in text; return decoded content or ''."""
    import base64

    for m in _B64_CHUNK_RE.finditer(text):
        try:
            decoded = base64.b64decode(m.group(), validate=True).decode("utf-8", errors="ignore")
            if _CREDENTIAL_PATTERNS.search(decoded):
                return decoded
        except Exception:
            continue
    return ""


def redact_credentials(text: str) -> tuple[str, list[str]]:
    """Redact raw credential patterns from text, including base64-encoded.

    Returns (cleaned_text, list_of_warnings).
    """
    warnings: list[str] = []
    result = text

    # 1. Redact plaintext credential patterns
    for m in _CREDENTIAL_PATTERNS.finditer(result):
        matched = m.group()
        tag = "[REDACTED: credential]"
        result = result.replace(matched, tag, 1)
        warnings.append(f"Redacted credential pattern: {matched[:20]}...")

    # 2. Detect and redact base64-encoded credentials
    for m in _B64_CHUNK_RE.finditer(text):
        chunk = m.group()
        decoded = _decode_b64_safe(chunk)
        if decoded:
            result = result.replace(chunk, "[REDACTED: encoded credential]", 1)
            warnings.append(f"Redacted base64-encoded credential ({len(chunk)} chars)")

    return result, warnings


# Suspicious bash patterns to flag during audit
SUSPICIOUS_BASH_PATTERNS: list[str] = [
    "curl * | bash",
    "curl * | sh",
    "wget * | bash",
    "| bash",
    "| sh",
    "| python",
    "| perl",
    # NB: recursive-force `rm` of a critical path is handled by _RM_RF_RE below —
    # a precise, anchored matcher. Plain substrings like "rm -rf /" are deliberately
    # NOT listed: they substring-matched legitimate targeted deletes (rm -rf /tmp/x,
    # rm -rf ~/.cache/build) → false blocks, while still missing rm -rf $HOME / `.`.
    "find * -delete",
    "find * -exec rm",
    "find * -exec shred",
    "xargs rm",
    "git clean -f",
    "shred ",
    "truncate ",
    "> /dev/sd",
    "mkfs.",
    "dd if=",
    "chmod 777",
    "chmod */usr/",
    "chmod */etc/",
    "chmod */sbin/",
    "chmod */boot/",
    "chmod */lib/",
    "chmod */lib64/",
    "chown */usr/",
    "chown */etc/",
    "chown */sbin/",
    "chown */boot/",
    "chown */lib/",
    "chown */lib64/",
    "eval $(",
    "base64 -d",
    "nc -e",
    "ncat -e",
    "/dev/tcp/",
    "xp_cmdshell",
    "GRANT ALL",
    "DROP DATABASE",
    "DROP TABLE",
    "TRUNCATE TABLE",
    "aws iam create-access-key",
    "aws sts assume-role",
    "export AWS_SECRET",
    "export AWS_ACCESS",
    "curl * -d @",
    "curl * --data @",
    "curl * -F file=@",
    "curl -d @",
    "curl --data @",
    "curl -F file=@",
    "wget --post-file",
    "nc * < ",
]

# A recursive-force `rm` whose target is catastrophic — home, the cwd/parent (which
# for a Code worker IS the workspace), root, or a glob/expansion. The literal-glob
# list above can't express "target is EXACTLY '.' (not './build')", so this is a
# properly-anchored regex: any -r/-f/-R/--recursive/--force flag ordering, then a
# target of  ~  ~/  /  /*  .  ./  ..  ../  *  $HOME  ${HOME}  "$HOME"  $PWD … —
# while a NAMED target (rm -rf ./build, rm -rf node_modules, rm -rf /tmp/scratch)
# stays clean. Trailing-context ($|/|"|') keeps `~/safe/path` from matching the `~`.
_RM_RF_RE = re.compile(
    r"""\brm\s+                       # rm
        (?:-[a-z]*[rf][a-z]*\s+|--(?:recursive|force)\s+)+   # ≥1 flag incl r or f
        ['"]?                         # optional opening quote on the target
        (?:                           # — a catastrophic target, whole-token —
            /\*?                      #   /  or  /*   (root, or everything under it)
          | ~/?                       #   ~  or  ~/   (home)
          | \.{1,2}/?                 #   .  ..  ./  ../  (cwd / parent)
          | \*                        #   a bare glob in cwd
          | \$\{?(?:HOME|PWD)\}?/?    #   $HOME / ${HOME} / $PWD (optional trailing /)
        )
        (?=['"]?(?:$|\s|;|&|\|))      # target ENDS here — a real path (./build,
                                      # ~/.cache/x, /tmp/y) has more segments → no match
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ── Bash denied-command regexes ──
# Credential-exfiltration and destructive-command regexes applied to every shell
# command the agent runs (native bash tool + command-screening sites). Distinct
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
    the native bash tool, the action-provider denylist and the Security panel all read.
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


def denied_command_reason(command: str) -> str | None:
    """Return the denied pattern a command matches, or None.

    Matches ``command`` against :func:`denied_command_patterns` (built-in +
    user) case-insensitively. The native bash tool calls this before execution.
    """
    for pat in denied_command_patterns():
        try:
            if re.search(pat, command, re.IGNORECASE):
                return pat
        except re.error:
            continue
    return None


def redact(text: str) -> str:
    """Apply all redaction passes (exfiltration URLs + credentials)."""
    text = redact_exfiltration_urls(text)[0]
    text = redact_credentials(text)[0]
    return text


# The fence markers. The system prompt tells the model that anything between these is
# DATA, never instructions — so a prompt-injection in a fetched page/ticket/doc is read,
# not obeyed. Kept as module constants so the prompt wording and the wrapper agree.
UNTRUSTED_OPEN = "<untrusted_content>"
UNTRUSTED_CLOSE = "</untrusted_content>"

#: The open marker WITH its optional attributes, and the `is_fenced` predicate over it.
#:
#: 🔴 A literal `UNTRUSTED_OPEN in text` check finds NOTHING on exactly the spans that carry
#: provenance: `fence_untrusted(..., source_type=...)` emits
#: `<untrusted_content source=… source_type=…>`, so the bare-tag substring is absent. That is a
#: fail-OPEN mistake in any caller asking "is this already fenced?" — it re-wraps an origin-fenced
#: span, and the outer call escapes the inner marker, turning the origin's `source_id` /
#: `transformation_path` into literal text and destroying the provenance chain.
#:
#: `learning/hygiene` hit this first and solved it locally; promoted here so there is ONE definition
#: rather than a second regex to forget. Derived from the constant, so renaming the tag cannot leave
#: a matcher silently looking for the old name.
_OPEN_TAG_RE = re.compile(re.escape(UNTRUSTED_OPEN[:-1]) + r"(?:\s[^>]*)?>", re.IGNORECASE)


def is_fenced(text: str) -> bool:
    """Whether ``text`` already carries an untrusted-content fence (attributed or bare).

    Use this instead of `UNTRUSTED_OPEN in text` before deciding to fence: the substring form
    misses every attributed fence, which is the fail-open direction (double-wrapping).
    """
    return bool(text) and bool(_OPEN_TAG_RE.search(text))


#: Chat-template role/control tokens, neutralised in untrusted text (§7/R4 rule b).
#:
#: These are not prose — each is a wire-format marker a runtime uses to delimit turns, so untrusted
#: text carrying one can forge a role boundary that no XML fence can describe. Grouped by the family
#: that defines them so a reader can tell WHY each entry is here, and so adding a new provider's
#: tokens is an obvious edit rather than an append to an anonymous list.
#:
#: Matched case-insensitively and neutralised by breaking the token, never by deleting it: dropping
#: the span would silently change what the user's automation reads, and a reader seeing
#: `[⁄INST]` in a fenced payload learns something true about the input.
ROLE_TOKENS: tuple[str, ...] = (
    # ChatML (OpenAI-style local templates, Qwen, many fine-tunes)
    "<|im_start|>",
    "<|im_end|>",
    # Llama 3
    "<|begin_of_text|>",
    "<|end_of_text|>",
    "<|start_header_id|>",
    "<|end_header_id|>",
    "<|eot_id|>",
    "<|eom_id|>",
    # Llama 2 / Mistral instruct
    "[INST]",
    "[/INST]",
    "<<SYS>>",
    "<</SYS>>",
    # Generic sentinels shared across GPT-2-lineage tokenizers and Mistral/Gemma
    "<|endoftext|>",
    "<|endofprompt|>",
    "<start_of_turn>",
    "<end_of_turn>",
    "<|user|>",
    "<|assistant|>",
    "<|system|>",
    "</s>",
    "<s>",
)

#: The character the token is broken WITH: U+2044 FRACTION SLASH and U+2223 DIVIDES render close to
#: the originals so a human reads the payload unchanged, while a tokenizer does not match a control
#: token. Deliberately NOT a zero-width character — the memory-write scanner flags those, and fenced
#: text is sometimes persisted (`fence_untrusted`'s own docstring makes that point about escaping).
_ROLE_TOKEN_SUBS: tuple[tuple[str, str], ...] = (("|", "∣"), ("/", "⁄"))


def strip_role_tokens(text: str) -> str:
    """Neutralise chat-template role tokens in `text` (§7/R4 rule b).

    Breaks each token rather than deleting it, so the payload still reads the same to a human and
    an automation summarising its input does not silently lose a span. A token with no `|` or `/`
    to break (`[INST]`, `<<SYS>>`) is bracket-escaped instead, the same treatment
    `fence_untrusted` already gives its own markers.

    Case-insensitive: `<|IM_START|>` is the same wire token to a tokenizer that lowercases, and a
    guard that only caught the canonical casing would be trivially bypassed.
    """
    if not text:
        return text
    import re as _re

    def _neutralise(match: _re.Match[str]) -> str:
        token = match.group(0)
        for needle, replacement in _ROLE_TOKEN_SUBS:
            if needle in token:
                return token.replace(needle, replacement)
        # No separator to break (`[INST]`, `<<SYS>>`, `<start_of_turn>`): escape the brackets, the
        # same way the fence neutralises its own tag.
        return token.replace("<", "&lt;").replace(">", "&gt;").replace("[", "&#91;")

    pattern = "|".join(_re.escape(token) for token in ROLE_TOKENS)
    return _re.sub(pattern, _neutralise, text, flags=_re.IGNORECASE)


def fence_untrusted(
    text: str,
    *,
    source: str = "",
    source_type: str = "",
    source_id: str = "",
    transformation_path: str = "",
) -> str:
    """Wrap externally-sourced text so a model treats it as DATA, not instructions.

    Any text that entered from outside the user↔agent trust boundary — a fetched web
    page, a ticket/CR comment, an inbox message, an ingested document — can carry a
    prompt-injection ("ignore previous instructions, now do X"). Fencing it in
    ``<untrusted_content>`` markers (paired with the system-prompt note that the span is
    never executable) neutralises that: the model still READS the content but treats it
    as quoted data. Mirrors how PClaw already fences memory values.

    Defends against a **fence-break**: content that itself contains the close marker (a
    crafted page trying to "escape" the fence and inject trailing instructions) has its
    markers neutralised before wrapping, so the fence can't be closed early. An empty /
    whitespace-only input is returned unchanged (nothing to fence).

    Also neutralises **chat-template role tokens** (AUTOMATION-SUBSTRATE §7/R4 rule b).
    The XML fence is a convention the model is ASKED to respect; a role token is part of
    the wire format the runtime uses to mark who is speaking, so it can forge a turn
    boundary the fence cannot describe. Measured before this existed: every one of
    ChatML's ``<|im_start|>``, Llama-3's ``<|start_header_id|>``, Llama-2's ``[/INST]``
    and ``<<SYS>>``, Mistral's ``</s>`` and the bare ``<|endoftext|>`` passed through
    ``fence_untrusted`` intact. Local providers are exactly where that bites: a hosted
    API rejects or escapes stray control tokens, while a local runtime applying its own
    chat template will happily honour them.

    Carries **provenance attributes** (§7/R4 rule c): ``source_type`` (the CLASS of
    origin — ``web_watch``, ``file``, ``inbox``), ``source_id`` (which one — a url, a
    path, a message id) and ``transformation_path`` (how it got here — ``poll``,
    ``digest``, ``extract``). ``source=`` is kept and unchanged, because thirteen call
    sites pass it and it is what the existing tag-parser in ``learning/hygiene.py``
    tolerates; the three new attributes are additive and optional.

    Why all three rather than one string: "a web page said this" and "THIS page said
    this, and we summarised it on the way" are different claims, and only the second
    lets a reader (or a later audit) tell whether the text a model acted on is the text
    that arrived. Values are attribute-escaped, so a crafted ``source_id`` cannot close
    the tag it is inside — the same fence-break defence the body already gets."""
    if not text or not text.strip():
        return text
    # Neutralise any embedded fence markers so the content can't close the fence early
    # and smuggle instructions after it. Escape the tag's angle brackets (HTML-style) —
    # human-legible, and crucially adds NO invisible/zero-width chars (which the
    # memory-write scanner would flag if this fenced text were later persisted).
    safe = text.replace("<untrusted_content>", "&lt;untrusted_content&gt;").replace(
        "</untrusted_content>", "&lt;/untrusted_content&gt;"
    )
    safe = strip_role_tokens(safe)
    attrs = "".join(
        f" {name}={_fence_attr(value)}"
        for name, value in (
            ("source", source),
            ("source_type", source_type),
            ("source_id", source_id),
            ("transformation_path", transformation_path),
        )
        if value
    )
    return f"{UNTRUSTED_OPEN[:-1]}{attrs}>\n{safe}\n{UNTRUSTED_CLOSE}"


def _fence_attr(value: str) -> str:
    """One provenance attribute value, safe to sit inside the fence's own tag.

    🔴 The attribute is attacker-influenced: a `source_id` is a url or a file path that
    came from outside. Without escaping, a crafted value containing `>` would close the
    open tag early and everything after it would read as un-fenced instructions — the
    fence-break the body is already protected against, reintroduced through the label.

    Angle brackets and quotes are escaped, and newlines collapse to a space so a value
    cannot split the tag across lines. Truncated because a tag is metadata: a 4 KB url
    in the prompt prefix costs tokens on every fenced span.
    """
    flat = " ".join(str(value or "").split())[:200]
    return (
        flat.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def is_denied(tool_name: str, extra_patterns: list[str] | None = None) -> str | None:
    """Check tool name against built-in + extra deny patterns.

    Returns denial reason string, or None if allowed.
    """
    lower = tool_name.lower()
    has_separators = bool(_CMD_SEPARATOR_RE.search(lower))
    all_patterns = BUILTIN_DENY_PATTERNS + (extra_patterns or [])
    for pattern in all_patterns:
        if fnmatch.fnmatch(lower, pattern.lower()):
            exceptions = _DENY_EXCEPTIONS.get(pattern, [])
            if (
                not has_separators
                and exceptions
                and any(fnmatch.fnmatch(lower, e.lower()) for e in exceptions)
            ):
                if not _emit_deny_exception_event(tool_name, pattern):
                    return f"Blocked by security policy: {pattern}"
                continue
            return f"Blocked by security policy: {pattern}"
    return None


def _emit_deny_exception_event(tool_name: str, deny_pattern: str) -> bool:
    """Emit an SEL audit event when a deny exception is applied.

    Returns True if the event was logged successfully, False otherwise.
    The caller must NOT grant the exception if this returns False.
    """
    try:
        sel = SecurityEventLog()
        sel.log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="deny_exception",
                caller_identity="",
                agent="personalclaw",
                source="security",
                operation=tool_name,
                outcome="allowed",
                resources=f"deny_pattern={deny_pattern}",
                metadata={"deny_pattern": deny_pattern, "mechanism": "_DENY_EXCEPTIONS"},
            )
        )
        return True
    except Exception:
        logger.warning(
            "SEL audit failed for deny_exception — denying %r (fail-closed)",
            tool_name,
            exc_info=True,
        )
        return False


# ── Denial taxonomy (recoverable vs hard) ──
# When a tool call is blocked, the model needs a model-visible observation it can
# act on — not silent stalling. But the framing differs by *why* it was blocked:
#
# - RECOVERABLE (user declined this call, a user-authored hook policy, a
#   read-only gate): the agent should ADAPT — try a genuinely different approach
#   or stop and explain — but NOT repeat the same call. The observation invites
#   adaptation, not circumvention.
# - HARD (security deny-list match, sensitive-path access — credential-exfil /
#   kill-switch territory): non-negotiable. The observation states it is terminal
#   and must not be circumvented or rephrased; NO recovery hint (a hint would
#   invite bypass probing). The agent should pick a different task or stop.
#
# The per-(tool|params) failure breaker (rel-consecutive-failure-breaker) is the
# hard loop cap behind this — this only shapes the single observation.

DENY_KIND_USER = "user"  # interactive: the user declined this call
DENY_KIND_HOOK = "hook"  # a user-authored PreToolUse hook blocked it
DENY_KIND_READONLY = "readonly"  # the read-only gate blocked a write
DENY_KIND_POLICY = "policy"  # security deny-list pattern (HARD)
DENY_KIND_SENSITIVE = "sensitive"  # sensitive-path access (HARD)

_HARD_DENY_KINDS = frozenset({DENY_KIND_POLICY, DENY_KIND_SENSITIVE})

#: One fragment per branch of :func:`classify_denial` — every observation it can
#: produce contains exactly one of these. It lives beside the function that WRITES
#: the text so a consumer can recognise a denial without re-authoring the wording;
#: a caller-side regex would drift silently the first time a branch is reworded.
#: ``test_security_denial_observation.py`` drives every declared ``DENY_KIND_*``
#: through ``classify_denial`` and asserts the fragment survives, so adding a kind
#: (or rewording a branch) without updating this table reds CI.
_DENIAL_FRAGMENTS: tuple[str, ...] = (
    "blocked by a security policy",
    "blocked by the read-only gate",
    "blocked by a policy hook",
    "was declined (",
)


def is_denial_observation(text: str) -> bool:
    """True when ``text`` is an observation :func:`classify_denial` produced.

    A DENIAL and a FAILURE are both fed back to the model as ``Error: …``, but they
    are different priors: "the user/policy refuses this" is not "this tool does not
    work". Procedural memory needs to tell them apart (a denial labelled ``failed``
    teaches "prefer an alternative tool" for what is really a standing policy), and
    this is the only structural signal available at the seam that records the
    outcome — the runtime already derives ``failed`` from the same string.
    """
    return bool(text) and any(frag in text for frag in _DENIAL_FRAGMENTS)


def classify_denial(kind: str, reason: str, tool_name: str = "") -> tuple[bool, str]:
    """Map a denial to ``(recoverable, observation)`` for the model.

    ``observation`` is the text fed back as the tool's result so the agent learns
    why the call was blocked and what to do next, instead of stalling. Recoverable
    denials invite adaptation (without repeating the same call); hard denials are
    framed as terminal and non-circumventable, with no recovery hint.
    """
    tool = f" `{tool_name}`" if tool_name else ""
    if kind in _HARD_DENY_KINDS:
        return (
            False,
            f"Error: tool{tool} blocked by a security policy ({reason}). This is "
            "non-negotiable — do NOT attempt to circumvent or rephrase it. Choose "
            "a different approach that does not require this, or stop and explain.",
        )
    if kind == DENY_KIND_READONLY:
        return (
            True,
            f"Error: tool{tool} blocked by the read-only gate ({reason}). Do NOT "
            "retry the same write — use a read-only alternative, or stop and "
            "explain what you would change and why.",
        )
    if kind == DENY_KIND_HOOK:
        return (
            True,
            f"Error: tool{tool} blocked by a policy hook ({reason}). Do NOT retry "
            "the same call — try a genuinely different approach that satisfies the "
            "policy, or stop and explain the blocker to the user.",
        )
    # DENY_KIND_USER (or anything unrecognized → treat as recoverable, the safe
    # default for a non-security block).
    return (
        True,
        f"Error: tool{tool} was declined ({reason}). Do NOT retry the same call — "
        "either take a different approach or stop and ask the user how to proceed.",
    )


def audit_bash_command(command: str) -> str | None:
    """Check a bash command against suspicious patterns.

    Returns warning string, or None if clean.
    Patterns with ``*`` are matched as globs via fnmatch.
    """
    lower = command.lower()
    for pattern in SUSPICIOUS_BASH_PATTERNS:
        pat = pattern.lower()
        if "*" in pat:
            if fnmatch.fnmatch(lower, f"*{pat}*"):
                return f"Suspicious command detected: matches '{pattern}'"
        elif pat in lower:
            return f"Suspicious command detected: matches '{pattern}'"
    # Catastrophic recursive deletes the literal list can't anchor (rm -rf $HOME,
    # rm -rf ., rm -rf .., flag-order variants like rm -fr / rm -r -f).
    if _RM_RF_RE.search(command):
        return "Suspicious command detected: recursive force-delete of a critical path"
    return None


def scan_history(history_dir: Path, last_n: int = 100) -> list[dict]:
    """Scan recent conversation history for suspicious tool usage.

    Returns list of findings: [{file, line, tool, command, warning}]
    """
    findings: list[dict] = []
    if not history_dir.is_dir():
        return findings

    files = sorted(history_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    checked = 0
    for f in files:
        try:
            for line in f.read_text().splitlines():
                if checked >= last_n:
                    return findings
                checked += 1
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = entry.get("content", "")
                role = entry.get("role", "")
                if role != "assistant" or not isinstance(content, str):
                    continue
                # Check for bash commands in tool calls
                warning = audit_bash_command(content)
                if warning:
                    findings.append(
                        {
                            "file": f.name,
                            "warning": warning,
                            "snippet": content[:200],
                        }
                    )
        except OSError:
            continue
    return findings


def scan_memory() -> list[dict]:
    """Scan memory for suspicious content via the memory service. Returns findings."""
    from personalclaw.memory_service import MemoryService
    from personalclaw.vector_memory import VectorMemoryStore, _contains_injection

    findings: list[dict] = []
    try:
        store = VectorMemoryStore()
        store.init()
    except Exception:
        return findings
    svc = MemoryService.over_vector_store(store)

    # Scan semantic values
    for entry in svc.get_all_semantic():
        val = entry.get("value_json", "")
        if _contains_injection(val):
            findings.append(
                {
                    "type": "semantic",
                    "key": entry["key"],
                    "value": val[:200],
                    "warning": "Injection pattern detected",
                }
            )

    # Scan episodic texts
    for entry in svc.episodic_list(limit=1000):
        text = entry.get("text", "")
        if _contains_injection(text):
            findings.append(
                {
                    "type": "episodic",
                    "key": entry["id"],
                    "value": text[:200],
                    "warning": "Injection pattern detected",
                }
            )

    store.close()
    return findings


def should_record_observe_history(
    channel_history: object | None,
    user_authorized: bool,
) -> bool:
    """Return True if an observe-mode message should be recorded.

    Only authorized users' messages are recorded to prevent non-owner
    prompt injection via shared channel traffic.
    """
    return channel_history is not None and user_authorized


def redact_and_truncate(text: str, max_chars: int = 4000) -> str:
    """Truncate, then redact credentials and exfiltration URLs."""
    return redact_credentials(redact_exfiltration_urls((text or "")[:max_chars])[0])[0]

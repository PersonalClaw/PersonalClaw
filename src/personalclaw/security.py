"""Built-in security controls — deny list, sensitive path protection, and audit scanning."""

import fnmatch
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
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
]

# Regex for bash commands that read sensitive paths.
# Matches: cat, head, tail, less, more, strings, xxd, base64, cp, scp, open
# followed by a path containing any sensitive dir.
_READ_CMDS = r"(?:cat|head|tail|less|more|strings|xxd|base64|cp|scp|open|vi|vim|nano|code)\s"

# Matches python/ruby/perl one-liners that open sensitive paths
_SCRIPT_OPEN = r"(?:python|ruby|perl)\S*\s.*open\s*\("


def _build_sensitive_regex() -> re.Pattern[str]:
    """Build a compiled regex matching bash reads of sensitive paths."""
    home = re.escape(str(Path.home()))
    tilde = re.escape("~")
    home_var = re.escape("$HOME")
    home_alts = f"(?:{home}|{tilde}|{home_var})"
    escaped_dirs = [re.escape(d) for d in _SENSITIVE_HOME_DIRS]
    dirs_pattern = "|".join(escaped_dirs)
    return re.compile(
        rf"(?:{_READ_CMDS}.*|{_SCRIPT_OPEN}.*|.*[<>|]\s*){home_alts}/(?:{dirs_pattern})(?:/|\s|$|['\"])",
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
    for sensitive_dir in _SENSITIVE_HOME_DIRS:
        sensitive_path = os.path.join(home, sensitive_dir)
        if resolved == sensitive_path or resolved.startswith(sensitive_path + os.sep):
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
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/dev", "/proc",
    "/sys", "/root", "/System", "/Library", "/Applications", "/cores", "/Network",
    "/private/etc", "/private/usr", "/private/var/root",
)
_SYSTEM_PARENTS: tuple[str, ...] = (
    "/", "/Volumes", "/private", "/var", "/opt", "/mnt", "/media",
    "/private/var", "/private/tmp", "/tmp",
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
    if resolved in _SYSTEM_PARENTS:
        return True
    for root in _SYSTEM_SUBTREES:
        if resolved == root or resolved.startswith(root + os.sep):
            return True
    return False


def is_sensitive_bash_command(command: str) -> str | None:
    """Check if a bash command reads sensitive paths.

    Returns denial reason string, or None if clean.
    """
    if _get_sensitive_re().search(command):
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
_S3_PRESIGNED_PARAMS = frozenset({
    "X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Date",
    "X-Amz-Expires", "X-Amz-SignedHeaders", "X-Amz-Signature",
    "X-Amz-Security-Token",
})


# Structural validators for presigned param values that would otherwise
# false-positive against _EXFIL_PATTERNS.  Each value is validated rather
# than exempted, so attacker-controlled data cannot be smuggled through.
_STS_TOKEN_RE = re.compile(
    r"^(?:FwoGZX|IQoJb3JpZ2lu)[A-Za-z0-9+/=%]{1,2000}$"
)
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
BUILTIN_DENIED_COMMAND_PATTERNS: list[str] = [
    # Credential exfiltration — secrets to S3 / over the wire / via env.
    r"aws s3 cp .* s3://.*",
    r"aws s3 mv .* s3://.*",
    r"aws s3 sync .* s3://.*",
    r".*echo.*\$AWS_SECRET.*",
    r".*echo.*\$AWS_SESSION.*",
    r".*echo.*\$AWS_ACCESS.*",
    r".*printenv.*AWS.*",
    r".*personalclaw.*token",
    r".*env.*grep.*AWS.*",
    r".*python.*boto3.*get_credentials.*",
    r".*python.*botocore.*credentials.*",
    r".*curl.*169\.254\.169\.254.*",
    r".*wget.*169\.254\.169\.254.*",
    r".*curl.*\$AWS_SECRET.*",
    r".*curl.*\$AWS_ACCESS.*",
    r".*curl.*\$AWS_SESSION.*",
    # Destructive cloud operations (defense-in-depth; the agent runs unsandboxed).
    r"aws autoscaling delete-.*",
    r"aws cloudformation delete-stack.*",
    r"aws cloudformation update-termination-protection.*",
    r"aws dynamodb delete-table.*",
    r"aws ec2 delete-.*",
    r"aws ec2 terminate-instances.*",
    r"aws ecr delete-.*",
    r"aws ecs delete-.*",
    r"aws eks delete-cluster.*",
    r"aws elasticache delete-.*",
    r"aws elb delete-.*",
    r"aws elbv2 delete-.*",
    r"aws glue delete-.*",
    r"aws iam create-access-key.*",
    r"aws iam delete-.*",
    r"aws kinesis delete-.*",
    r"aws kms schedule-key-deletion.*",
    r"aws lambda delete-function.*",
    r"aws logs delete-.*",
    r"aws opensearch delete-.*",
    r"aws rds delete-.*",
    r"aws redshift delete-.*",
    r"aws route53 delete-.*",
    r"aws s3 rb.*",
    r"aws s3 rm.*",
    r"aws s3api delete-.*",
    r"aws secretsmanager delete-secret.*",
    r"aws sns delete-.*",
    r"aws sqs delete-.*",
    r"aws stepfunctions delete-.*",
    r"cdk destroy.*",
    r"kubectl delete namespace.*",
    r"pulumi destroy.*",
    r"terraform destroy.*",
    # Destructive filesystem / permission changes on system paths.
    r"chmod 777.*",
    r"chmod.*/usr/.*",
    r"chmod.*/etc/.*",
    r"chmod.*/sbin/.*",
    r"chmod.*/boot/.*",
    r"chmod.*/lib/.*",
    r"chmod.*/lib64/.*",
    r"chown.*/usr/.*",
    r"chown.*/etc/.*",
    r"chown.*/sbin/.*",
    r"chown.*/boot/.*",
    r"chown.*/lib/.*",
    r"chown.*/lib64/.*",
    r"dd if=.*",
    r"mkfs.*",
    r"rm -rf /.*",
    r"rm -rf ~.*",
    r"git reset --hard.*",
    # Pipe-to-shell.
    r"curl .* \| bash",
    r"curl .* \| sh",
    r"wget .* \| bash",
    # Reverse shells.
    r"nc -e.*",
    r"ncat -e.*",
    # Credential / secret env export.
    r"export AWS_ACCESS.*",
    r"export AWS_SECRET.*",
    # Destructive SQL.
    r"DROP DATABASE.*",
    r"DROP TABLE.*",
    r"TRUNCATE TABLE.*",
    # Unreviewed pushes (work should be reviewed before leaving the machine).
    r".*git\s+(-\S+\s+[^-]\S*\s+|-\S+\s+)*push(\s.*|$)",
    r"workspace snapshot push.*",
    r"bws snapshot push.*",
    # Reads of credential files (cat/head/tail/less/more/strings/base64/cp/python-open).
    r".*cat.*/\.aws/.*",
    r".*cat.*/\.ssh/.*",
    r".*cat.*/\.gnupg/.*",
    r".*cat.*/\.gpg/.*",
    r".*cat.*/\.netrc.*",
    r".*cat.*/\.git-credentials.*",
    r".*cat.*/\.npmrc.*",
    r".*cat.*/\.pypirc.*",
    r".*cat.*/\.docker/config\.json.*",
    r".*cat.*/\.kube/config.*",
    r".*cat.*/\.personalclaw/\.env.*",
    r".*head.*/\.aws/.*",
    r".*tail.*/\.aws/.*",
    r".*less.*/\.aws/.*",
    r".*more.*/\.aws/.*",
    r".*strings.*/\.aws/.*",
    r".*base64.*/\.aws/.*",
    r".*head.*/\.ssh/.*",
    r".*tail.*/\.ssh/.*",
    r".*less.*/\.ssh/.*",
    r".*more.*/\.ssh/.*",
    r".*strings.*/\.ssh/.*",
    r".*base64.*/\.ssh/.*",
    r".*cp.*/\.aws/.*",
    r".*cp.*/\.ssh/.*",
    r".*python.*open.*/\.aws/.*",
    r".*python.*open.*/\.ssh/.*",
    # Self-tampering — the agent must not restart/update/kill its own gateway.
    r".*personal.?claw restart.*",
    r".*personal.?claw update.*",
    r".*personal.?claw gateway restart.*",
    r".*\b(kill|pkill|killall)\b.*\bpersonal[-.]?claw\b.*",
]


def denied_command_patterns() -> list[str]:
    """Return the effective bash denied-command regexes: built-ins + any
    user-configured additions from ``AppConfig.security.denied_commands``.

    Built-ins are always enforced and cannot be removed via config; user
    patterns are appended. This is the single source the native bash tool and
    the Security panel both read.
    """
    from personalclaw.config.loader import AppConfig

    return BUILTIN_DENIED_COMMAND_PATTERNS + list(AppConfig.load().security.denied_commands)


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


def fence_untrusted(text: str, *, source: str = "") -> str:
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
    whitespace-only input is returned unchanged (nothing to fence)."""
    if not text or not text.strip():
        return text
    # Neutralise any embedded fence markers so the content can't close the fence early
    # and smuggle instructions after it. Escape the tag's angle brackets (HTML-style) —
    # human-legible, and crucially adds NO invisible/zero-width chars (which the
    # memory-write scanner would flag if this fenced text were later persisted).
    safe = text.replace("<untrusted_content>", "&lt;untrusted_content&gt;").replace(
        "</untrusted_content>", "&lt;/untrusted_content&gt;")
    label = f" source={source}" if source else ""
    return f"{UNTRUSTED_OPEN[:-1]}{label}>\n{safe}\n{UNTRUSTED_CLOSE}"


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

DENY_KIND_USER = "user"          # interactive: the user declined this call
DENY_KIND_HOOK = "hook"          # a user-authored PreToolUse hook blocked it
DENY_KIND_READONLY = "readonly"  # the read-only gate blocked a write
DENY_KIND_POLICY = "policy"      # security deny-list pattern (HARD)
DENY_KIND_SENSITIVE = "sensitive"  # sensitive-path access (HARD)

_HARD_DENY_KINDS = frozenset({DENY_KIND_POLICY, DENY_KIND_SENSITIVE})


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

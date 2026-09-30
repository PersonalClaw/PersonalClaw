"""Built-in security controls — deny list, sensitive path protection, and audit scanning."""

import fnmatch
import hashlib
import json
import logging
import logging.handlers
import os
import re
import stat
import sys
import uuid
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs

from personalclaw import address_logins
from personalclaw.command_paths import (
    HOME_PATTERNS,
    is_glob,
    named_paths,
    shell_expands_to,
    strip_shell_quotes,
)
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
    # The macOS user keychain — the OS credential store, and the one third-party secret
    # location the list missed. It was an accepted PTY working directory (#643).
    "Library/Keychains",
]

#: PersonalClaw's OWN auth and audit material, refused by BASENAME wherever it sits.
#:
#: 🔴 These were known to be secret in exactly one place and unknown here (#643).
#: ``handlers/files.py`` refuses every one of them, so ``/api/file-read`` answered
#: ``400 invalid or forbidden path`` — while this function, which the terminal cwd guard,
#: the bash read/write hooks and the action denylist all consult, returned False for all
#: of them. Measured against the shipped guard: only ``~/.personalclaw/.env`` was blocked;
#: ``sel_hmac.key``, ``.local_secret``, ``telemetry_salt`` and ``credentials/`` were not.
#:
#: What each one costs:
#:   ``sel_hmac.key``   signs the append-only security log. With it, log rows can be forged
#:                      and the chain still verifies — the one record that cannot be repaired.
#:   ``.local_secret``  the loopback auth rail (``auth/cli.py``, ``mcp_core``, ``mcp_shared``).
#:   ``telemetry_salt`` de-anonymizes recorded telemetry.
#:
#: BASENAME rather than a path, deliberately: ``PERSONALCLAW_HOME`` is user-settable, so
#: these files are not confined to one directory and a home-relative entry cannot follow
#: them. These three names are unique to PersonalClaw, so no realistic file elsewhere
#: legitimately carries one — unlike a suffix rule (``.key``/``.pem``), which would refuse a
#: project's own public certificate. ``files.py`` keeps that stricter suffix tier, scoped to
#: the dashboard's allowlisted roots where it belongs.
#:
#: ``session_key`` and ``sessions.json`` are deliberately NOT here — see
#: ``_SENSITIVE_PCLAW_HOME_ENTRIES``. The split is by a real property (is the name ours
#: alone?), not by which module happened to define it, which is what let the two lists
#: drift apart in the first place.
OWN_SECRET_BASENAMES: frozenset[str] = frozenset(
    {
        "sel_hmac.key",
        ".local_secret",
        "telemetry_salt",
    }
)

#: The credential store's file in a home (``config.loader.env_path()``), where every secret
#: PersonalClaw keeps is written when no OS keychain holds it.
CREDENTIAL_STORE_FILE = ".env"

#: Secret-bearing entries INSIDE the PersonalClaw home, resolved at check time for the ACTIVE
#: home and for the default one (:func:`_pclaw_homes`) rather than spelled from ``$HOME``.
#:
#: 🔴 Spelled from ``$HOME`` (``~/.personalclaw/.env``), an entry matches nothing once
#: ``PERSONALCLAW_HOME`` points elsewhere — and every documented container install does
#: (``/data``). Measured there: the agent's shell read the stored keys out of ``/data/.env``
#: (``cut -d= -f1`` listed the names, ``awk`` printed a key's first characters) while
#: ``~/.personalclaw/.env``, a file that did not exist, was refused. The path guard had
#: learned the active home and the shell's screen had not: two declarations of one thing.
#:
#: The home itself is NOT listed: it is a browsable dashboard root holding the knowledge
#: DB, apps and logs, so refusing it wholesale would break the files area. Only the
#: secret-bearing entries are refused.
#:
#: ``session_key`` and ``sessions.json`` live here rather than in
#: :data:`OWN_SECRET_BASENAMES` because they are secret by LOCATION, not by name: a user's
#: own project may reasonably hold a ``sessions.json``, and blocking that name everywhere
#: would make the agent's bash guard refuse an ordinary file. Reading OURS forges a session
#: token or leaks live nonces, so the path is what has to be refused.
#:
#: Split into FILES and DIRS, and both EXPORTED, so that ``handlers/files.py`` can DERIVE its
#: basename tier from this declaration instead of re-listing the same three names. That
#: re-listing is not a stylistic point: it is the mechanism of #354. ``session_key`` was
#: documented as "the signing key" in ``session_store.py`` and named a secret here, and the
#: dashboard blocklist still did not know about it, because a hand-copied list only knows what
#: someone remembered to copy. One declaration, every consumer derived.
#:
#: Names go in :data:`HOME_SECRET_FILE_BASENAMES` when the file itself is the secret, and in
#: :data:`HOME_SECRET_DIRS` when the whole subtree is (every entry beneath a dir is refused,
#: so a new file added inside one is covered on the day it is written — the property that
#: makes the omission structurally impossible rather than merely fixed once).
HOME_SECRET_FILE_BASENAMES: frozenset[str] = frozenset(
    {
        CREDENTIAL_STORE_FILE,
        "session_key",
        "sessions.json",
    }
)

#: Secret-bearing DIRECTORIES in the PersonalClaw home. The whole subtree is refused.
#:
#: 🔴 ``auth`` was MEASURED missing (#354, this fix). ``auth/credentials.json`` holds the
#: argon2id password hash, ``auth/enroll_codes.json`` and ``auth/pair_codes.json`` hold the
#: live redeemable device codes — and all three answered ``200`` with their contents through
#: ``GET /api/file-read``, because the home is a browsable dashboard root and nothing in any
#: guard named this directory. That is the SAME omission as ``session_key``, one directory
#: over, found by asking the auth layer what files it writes instead of trusting the list.
#: A dir entry rather than three basenames deliberately: ``credentials.json`` and
#: ``pair_codes.json`` are plausible names in a user's own project, and the fourth auth file
#: nobody has written yet must be covered too.
#:
#: ``governance`` holds the governance ceiling (``guardrails/ceiling.py``), the operator's hard
#: bound on every run: refused so that every agent-reachable path check (the action denylist,
#: the files area, the bash screen) refuses it, because a bound the agent can rewrite is not a
#: bound. This closes the write paths a single-user machine CAN close; the stronger protection
#: is ``PERSONALCLAW_CEILING_FILE`` pointing at a root-owned file outside the home.
HOME_SECRET_DIRS: frozenset[str] = frozenset(
    {
        "auth",
        "credentials",
        "governance",
    }
)

#: The union, in a stable order. Every consumer that wants "the secret-bearing entries of a
#: home" reads this; nothing re-lists its members.
_SENSITIVE_PCLAW_HOME_ENTRIES: tuple[str, ...] = tuple(
    sorted(HOME_SECRET_FILE_BASENAMES | HOME_SECRET_DIRS)
)


def _pclaw_homes() -> list[str]:
    """The PersonalClaw homes whose secrets every guard refuses: the ACTIVE one, and the
    default one when the active home is another folder.

    The default one too, because a home in use elsewhere does not make the owner's own home
    empty: a dev gateway on ``.dev-home`` runs beside a real ``~/.personalclaw`` holding real
    keys, and an agent of the first must not read the second's.

    Resolved per call because ``PERSONALCLAW_HOME`` is read from the environment and a
    process can legitimately see it change (tests, a dev gateway). Best-effort: a home that
    cannot be resolved is left out, so a config hiccup narrows the guard rather than
    removing it.

    🔴 Deliberately NOT ``config.loader.config_dir()``, which calls ``_ensure_dir`` and so
    CREATES the directory. A read-only path predicate that makes a directory is a bug in
    itself, and it was measurably one: with ~70 call sites, merely checking a path
    materialized the home. It surfaced as a file-listing test failing on an unexpected
    ``.personalclaw`` entry appearing inside a fixture's fake ``$HOME`` — the guard had
    created it mid-assertion. ``resolve_config_dir()`` is the same rule without the mkdir.
    It used to be re-spelled here, and the copy guarded ``$PERSONALCLAW_HOME`` as written —
    so an override the resolver refuses (a system directory) had this guarding a directory
    nothing used while the process ran on the default home.
    """
    from personalclaw.config.loader import default_config_dir, resolve_config_dir

    homes: list[str] = []
    for resolve in (resolve_config_dir, default_config_dir):
        try:
            homes.append(str(resolve()))
        except (OSError, ValueError, RuntimeError):
            continue
    return list(dict.fromkeys(homes))


def _pclaw_home_sensitive_paths() -> list[str]:
    """Absolute paths of the secret-bearing entries in each home :func:`_pclaw_homes` names."""
    return [
        os.path.join(home, entry)
        for home in _pclaw_homes()
        for entry in _SENSITIVE_PCLAW_HOME_ENTRIES
    ]


def credential_store_paths() -> list[str]:
    """The credential store's file in each home :func:`_pclaw_homes` names: what the OS sandbox
    hides from an agent's child process, where it hides a file at all."""
    return [os.path.join(home, CREDENTIAL_STORE_FILE) for home in _pclaw_homes()]


#: Another tool's SIGN-IN file, refused by NAME wherever it sits: ``.credentials.json`` is Claude
#: Code's login and Codex's MCP sign-ins, ``oauth_creds.json`` Gemini CLI's Google sign-in.
#: By name because no location can follow them: Claude Code reads its config folder from a
#: variable set per process (``CLAUDE_CONFIG_DIR``), and PersonalClaw's own Claude Code app gives
#: its sessions a folder inside the home. The name alone says what the file is, which
#: ``auth.json`` or ``hosts.yml`` does not: those are refused only where their tool keeps them
#: (:func:`_sign_in_files`).
SIGN_IN_FILE_BASENAMES: frozenset[str] = frozenset({".credentials.json", "oauth_creds.json"})


def _sign_in_files(home: str) -> list[str]:
    """Where the agent CLIs and the tools PersonalClaw drives keep the sign-in each reads itself,
    for a user whose home is *home*.

    Named by tool on purpose, the way ``_SENSITIVE_HOME_DIRS`` names ``~/.aws``: this is
    secret-detection data (``docs/architecture/provider-boundary.md``), where a location is what
    says a file is a sign-in. Each folder is found the way its tool finds it, from the variable
    that moves it; the default folder stays refused beside a moved one, since a login can still
    sit there. A provider app's declared subscription sign-in is included, so a store an app names
    is one the agent may not read.
    """
    from personalclaw.llm.subscription_credentials import registered_sources
    from personalclaw.outside_home import huggingface_home

    def moved(name: str) -> str:
        value = os.environ.get(name, "").strip()
        return os.path.expanduser(value) if value else ""

    def folders(*candidates: str) -> list[str]:
        return list(dict.fromkeys(c for c in candidates if c))

    config_home = moved("XDG_CONFIG_HOME")
    files: list[str] = []
    # Codex: its login, tokens or an API key. Its MCP sign-ins are `.credentials.json`.
    for folder in folders(os.path.join(home, ".codex"), moved("CODEX_HOME")):
        files.append(os.path.join(folder, "auth.json"))
    # Gemini CLI: the API key it reads from `.env`, and its MCP and agent sign-ins, in `.gemini`
    # under its own home variable (the user's home when unset) — or `.cache/.gemini` when it runs
    # in its macOS sandbox. Its Google sign-in is `oauth_creds.json`.
    for base in folders(home, moved("GEMINI_CLI_HOME")):
        for folder in (os.path.join(base, ".gemini"), os.path.join(base, ".cache", ".gemini")):
            for name in (CREDENTIAL_STORE_FILE, "mcp-oauth-tokens.json", "a2a-oauth-tokens.json"):
                files.append(os.path.join(folder, name))
    # The GitHub CLI and the GitLab CLI: the token each keeps when no keychain holds it.
    for folder in folders(
        os.path.join(home, ".config", "gh"),
        moved("GH_CONFIG_DIR"),
        config_home and os.path.join(config_home, "gh"),
    ):
        files.append(os.path.join(folder, "hosts.yml"))
    for folder in folders(
        os.path.join(home, ".config", "glab-cli"),
        moved("GLAB_CONFIG_DIR"),
        config_home and os.path.join(config_home, "glab-cli"),
        os.path.join(home, "Library", "Application Support", "glab-cli"),
    ):
        files.append(os.path.join(folder, "config.yml"))
    # Hugging Face: the token `huggingface-cli login` saved, and every token it keeps.
    for folder in folders(os.path.join(home, ".cache", "huggingface"), str(huggingface_home())):
        files.extend((os.path.join(folder, "token"), os.path.join(folder, "stored_tokens")))
    files.extend(folders(moved("HF_TOKEN_PATH")))
    for source in registered_sources():
        for declared in source.credential_files:
            path = os.path.expanduser(os.path.expandvars(declared))
            # A candidate naming an unset variable names no file.
            if "$" not in path:
                files.append(path)
    return files


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


def _build_sensitive_regex() -> re.Pattern[str]:
    """Build a compiled regex matching bash reads of the credential folders under ``$HOME``.

    The home is matched as the command spells it: written out, as ``~``, or as a one-liner builds
    it (``command_paths.HOME_PATTERNS``, the vocabulary the path reading also uses).
    """
    home = str(Path.home())
    home_alts = "(?:" + "|".join((re.escape(home), "~", *HOME_PATTERNS)) + ")"
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
    Used by hooks to block fs_read/ReadFile of credential files. A walk that asks about many
    paths in one pass makes one :class:`SensitivePaths` and asks it instead.
    """
    return SensitivePaths()(path_str)


class SensitivePaths:
    """:func:`is_sensitive_path`, with the protected locations resolved ONCE for a whole walk.

    Resolving every protected location — its real path, and where each link inside it points —
    was nearly all of one check's cost: 0.2 ms a path, 2.4 s of an import scan over the 11,700
    transcript files a months-long Claude Code history holds (measured). An instance resolves
    them when it is made and compares every path it is asked about with that one resolution.
    Each REQUESTED path is still resolved on its own, on every call, so a link to a protected
    file is refused exactly as :func:`is_sensitive_path` refuses it.

    Make one per walk and drop it after: a protected location that becomes a link after the
    instance was made is seen by the next one, not by this one.

    *home_credential_dirs* False leaves out the credential folders under ``$HOME``
    (``_SENSITIVE_HOME_DIRS``) and keeps the files nothing but their owner reads: PersonalClaw's
    own secrets and another tool's sign-in. The shell's screen asks that narrower question of
    every path a command names (:func:`is_sensitive_bash_command`), because a command may use
    ``~/.ssh`` without returning it (``ssh -i ~/.ssh/key``), and none needs to name the others.
    """

    def __init__(self, *, home_credential_dirs: bool = True) -> None:
        home = str(Path.home())
        other_paths = _sign_in_files(home)
        if home_credential_dirs:
            other_paths += [os.path.join(home, d) for d in _SENSITIVE_HOME_DIRS]
        # The secret entries of the ACTIVE PersonalClaw home, and of the default one beside it.
        own_paths = _pclaw_home_sensitive_paths()
        folders: dict[str, str] = {}
        self._own = tuple(set().union(*(_protected_forms(p, folders) for p in own_paths)))
        self._other = tuple(set().union(*(_protected_forms(p, folders) for p in other_paths)))
        self._own_names = frozenset(n.casefold() for n in OWN_SECRET_BASENAMES)
        self._sign_in_names = frozenset(n.casefold() for n in SIGN_IN_FILE_BASENAMES)
        #: The bare name of everything this refuses, for a reader of a command: a word like
        #: `session_key` names one of these files once a `cd` has moved the shell beside it.
        self.names = frozenset(
            {os.path.basename(p) for p in (*own_paths, *other_paths)}
            | OWN_SECRET_BASENAMES
            | SIGN_IN_FILE_BASENAMES
        )

    def __call__(self, path_str: str) -> bool:
        return bool(self.kind(path_str))

    def kind(self, path_str: str) -> str:
        """What *path_str* is: :data:`OWN_SECRET` for PersonalClaw's own credential store or auth
        and audit material, :data:`CREDENTIAL` for any other credential, ``""`` for neither."""
        # 🔴 A path the OS cannot even name is SENSITIVE, not safe (issue 352). A NUL byte makes
        # every `os.path`/`pathlib` call raise `ValueError: embedded null character`, and the
        # `except` below deliberately continues with the UNRESOLVED string — which then matches
        # no sensitive prefix, so this answered False for `/tmp/a\x00b`. Measured.
        #
        # That answer is the dangerous half of this issue. The visible symptom was a 500 out of
        # `validate_file_path`, and the tempting fix there is to catch the exception and carry on
        # — which would hand this function a path it cannot classify and take False for an
        # answer. So the refusal belongs HERE as well, ahead of every caller.
        #
        # Fail CLOSED, the direction this function already argues for below: casefolding "can
        # only over-block ... the safe direction for a credential guard and the error a user can
        # see and report". A NUL is never part of a legitimate filename — POSIX and Windows both
        # forbid it in a path component — so over-blocking costs nothing real.
        if "\x00" in path_str:
            return CREDENTIAL
        # Expand ~ and $HOME, then compare every form of the request with every form of each
        # protected location (:func:`_path_forms`).
        expanded = os.path.expanduser(os.path.expandvars(path_str))
        requested = _path_forms(expanded)
        # CASE-INSENSITIVE comparison, because the comparison is the control.
        #
        # 🔴 Measured on macOS: `~/.SSH/id_rsa` was ALLOWED while `~/.ssh/id_rsa` was blocked,
        # and the default macOS filesystem (like Windows) is case-INSENSITIVE — a temp dir
        # created as `.ssh` was read back through `.SSH` and returned the file's contents. So
        # every one of the entries, INCLUDING the credential store and the governance ceiling,
        # was one shifted key away from being readable, across the ~70 call sites that route
        # through this function. `Path.resolve()` normalises `..`, `.`, `//`, `~` and `$HOME`
        # (all verified blocked); it does not normalise case.
        #
        # Always casefold rather than probing the filesystem per call: a per-path probe is
        # itself a control that fails when the probe fails, and on a case-SENSITIVE filesystem
        # casefolding can only over-block — a directory literally named `~/.SSH` that holds no
        # credentials would be refused, which is the safe direction for a credential guard and
        # the error a user can see and report.
        #
        # PersonalClaw's own auth/audit material and another tool's sign-in, by basename, wherever
        # they sit. Casefolded for the same reason as everything else here: on macOS/Windows the
        # filesystem is case-insensitive, so `.LOCAL_SECRET` resolves to the real bytes (#690's
        # finding).
        names = {os.path.basename(form) for form in requested}
        if names & self._own_names or _under(requested, self._own):
            return OWN_SECRET
        if names & self._sign_in_names or _under(requested, self._other):
            return CREDENTIAL
        return ""


#: :meth:`SensitivePaths.kind`'s answer for PersonalClaw's own credential store and its auth and
#: audit material, and for any other credential.
OWN_SECRET = "own"
CREDENTIAL = "credential"


def _under(forms: set[str], locations: tuple[str, ...]) -> bool:
    """Whether any of *forms* is one of *locations* or inside one."""
    return any(
        form == entry or form.startswith(entry + os.sep) for form in forms for entry in locations
    )


def _path_forms(path: str) -> set[str]:
    """*path* as written and as the filesystem resolves it, both absolute and casefolded.

    As written means ``.`` and ``..`` folded and no link followed; resolved means every link
    followed. The credential guard compares both forms of a request with both forms of each
    protected location, because comparing one form lets a link walk around it. It compared
    the resolved request with each location as written, so a ``~/.aws`` or ``~/.ssh`` that is
    a symlink (a dotfile manager makes it one) was open to every file under it: the request
    resolved to the link's target, which is not under ``~/.aws``. Resolving the protected
    side alone would still leave a dangling link, which resolves to nowhere real, open by the
    name it has.

    Comparing more forms can only refuse more, which is the direction this guard errs in.
    """
    forms = {os.path.normpath(os.path.abspath(path)).casefold()}
    try:
        forms.add(os.path.realpath(path).casefold())
    except (OSError, ValueError):
        pass
    return forms


def _protected_forms(path: str, folders: dict[str, str]) -> set[str]:
    """Every form a protected location takes: :func:`_path_forms`, plus where each symlink
    directly inside it points (:func:`_linked_entries`).

    The link targets are what reach a caller that resolved the request before asking, which
    most do (``hooks.validate_file_path`` realpaths first, and so the dashboard's file reader
    does). With ``~/.ssh`` a real directory and ``~/.ssh/id_ed25519`` a link into a dotfiles
    folder, such a caller asks about the dotfiles path, which is under no protected location
    unless the link's target is one.

    *folders* holds each folder's real path once it is resolved, for the rest of the set: most
    protected locations share a folder with others (a home's secret entries, a tool's sign-in
    files), and resolving the same folder again for each was most of what building the set cost.
    A location that is itself a link is resolved whole, so its target is still where it points.
    """
    written = os.path.normpath(os.path.abspath(path))
    forms = {written.casefold()}
    try:
        folder, name = os.path.split(written)
        if not name or os.path.islink(written):
            real = os.path.realpath(written)
        else:
            if folder not in folders:
                folders[folder] = os.path.realpath(folder)
            real = os.path.join(folders[folder], name)
    except (OSError, ValueError):
        return forms
    forms.add(real.casefold())
    return forms | _linked_entries(real)


#: ``directory -> (signature, targets)`` for :func:`_linked_entries`. A few protected
#: directories per home, so it stays small; cleared wholesale if many homes pass through.
_LINKED_ENTRIES: dict[str, tuple[tuple[int, int, int, int], frozenset[str]]] = {}


def _linked_entries(directory: str) -> frozenset[str]:
    """Where the symlinks directly inside *directory* point, resolved and casefolded.

    Read again whenever the directory's inode, mtime, size or link count moves, which adding,
    removing or replacing an entry does, and otherwise answered from the last read: this runs
    on every check, and a check sits on every file a walk visits.
    """
    try:
        st = os.stat(directory)
    except (OSError, ValueError):
        return frozenset()
    if not stat.S_ISDIR(st.st_mode):
        return frozenset()
    signature = (st.st_ino, st.st_mtime_ns, st.st_size, st.st_nlink)
    cached = _LINKED_ENTRIES.get(directory)
    if cached is not None and cached[0] == signature:
        return cached[1]
    targets: set[str] = set()
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    try:
                        targets.add(os.path.realpath(entry.path).casefold())
                    except (OSError, ValueError):
                        continue
    except OSError:
        return frozenset()
    if len(_LINKED_ENTRIES) > 256:
        _LINKED_ENTRIES.clear()
    found = frozenset(targets)
    _LINKED_ENTRIES[directory] = (signature, found)
    return found


# OS-managed roots that must never be created into / used as a workspace. Two tiers:
#   _SYSTEM_SUBTREES — the whole tree is off-limits (/etc, /usr, /System, …), children
#                      included — except the running account's own home (_SUPERUSER_HOMES).
#   _SYSTEM_PARENTS  — only the bare dir is off-limits; children are legitimate
#                      (/Volumes/<disk>/repo, a macOS /private/var/folders/<tmp>, /var/<x>).
# macOS realpaths /etc → /private/etc, /var → /private/var; callers resolve the path
# BEFORE this check, so the /private/* canonical forms are included. /private/var is a
# PARENT (not a subtree) because macOS user temp dirs (incl. pytest tmp_path) live under
# /private/var/folders. Single source of truth — the Code workspace validation and the
# create-dir / browse-dirs handlers all read the subtrees through system_subtrees(), so the
# surfaces can never drift.
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
#: The superuser's home on each platform — ``/root`` on Linux, ``/var/root`` (realpath
#: ``/private/var/root``) on macOS. Both sit in :data:`_SYSTEM_SUBTREES`, and for an ordinary
#: gateway that is exactly right: it is SOMEONE ELSE's home, and nothing else guards it, because
#: :func:`is_sensitive_path` resolves its credential entries against the RUNNING account's home —
#: for a gateway running as ``alice``, ``/root/.ssh`` is not a credential path at all.
#:
#: 🔴 For a gateway running AS root (a VPS, an LXC container, ``pip install`` into a plain Python
#: image) the same entry refused the account ITS OWN home. Measured in a real browser on a fresh
#: container: the workspace picker's default location, ``~``, answered 403, and the picker went on
#: to create the user's project folder at the filesystem root. There the entry protected nothing
#: the home-keyed guards did not already cover: ``/root/.ssh`` is ``Path.home()/.ssh``, refused by
#: :func:`is_sensitive_path` (and every file route that funnels through it), and the OS sandbox
#: lays its credential masks over that same home. Its only effect was the asymmetry —
#: ``/home/alice`` is browsable for alice, ``/root`` was never browsable for root.
#:
#: So :func:`system_subtrees` drops the entry that IS the running account's home. It keys on the
#: same ``Path.home()`` those guards resolve against, which is the invariant that makes this safe:
#: the tree let in is, by construction, the tree whose credentials they cover. Only these entries
#: can be let in — a ``$HOME`` that points into ``/usr`` or ``/etc`` unlocks nothing.
_SUPERUSER_HOMES: frozenset[str] = frozenset({"/root", "/private/var/root"})
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


def system_subtrees() -> tuple[str, ...]:
    """The whole-tree system roots off-limits to THIS process: :data:`_SYSTEM_SUBTREES`, minus the
    superuser's home when it is the running account's own (see :data:`_SUPERUSER_HOMES`).

    Resolved per call, like :func:`is_sensitive_path`'s home, so both always agree on which home
    is "yours". Fails CLOSED: a home that cannot be determined exempts nothing.
    """
    try:
        own_home = str(Path.home().resolve()).casefold()
    except (OSError, ValueError, RuntimeError):
        return _SYSTEM_SUBTREES
    return tuple(
        root
        for root in _SYSTEM_SUBTREES
        if not (root in _SUPERUSER_HOMES and root.casefold() == own_home)
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
    for root in system_subtrees():
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


#: What the shell's screen says when it refuses, by what the command reached.
_SHELL_REFUSAL = {
    OWN_SECRET: "Blocked: command accesses PersonalClaw's own credential or audit key",
    CREDENTIAL: "Blocked: command accesses sensitive credential path",
}


def _credential_folders() -> tuple[str, ...]:
    """The credential folders under ``$HOME`` themselves, as written and as resolved
    (:func:`_path_forms`): each :data:`_SENSITIVE_HOME_DIRS` entry that is a folder there.

    The entries that are files (``.netrc``, ``.kube/config``…) are used by their path, and a folder
    that does not exist holds nothing a listing could show.
    """
    home = str(Path.home())
    forms: set[str] = set()
    for entry in _SENSITIVE_HOME_DIRS:
        location = os.path.join(home, entry)
        if os.path.isdir(location):
            forms |= _path_forms(location)
    return tuple(forms)


def _shows_credential_folder(word: str, path: Path, folders: tuple[str, ...]) -> bool:
    """Whether *word*, which a command names *path* with, shows what a credential folder holds:
    it names the folder itself (``ls ~/.ssh``, ``find ~/.aws``, ``cd ~/.ssh``), or it is a glob
    the shell expands to the folder or to what is inside it (``ls ~/.a*``, ``ls ~/.ssh/*``)."""
    forms = _path_forms(str(path))
    if is_glob(word):
        return _under(forms, folders) and shell_expands_to(word, path)
    return not forms.isdisjoint(folders)


def is_sensitive_bash_command(
    command: str, *, cwd: str | os.PathLike[str] | None = None
) -> str | None:
    """Why a bash command must not run because of what it would read, or None if clean.

    Three questions:

    * Does it RETURN the content of a credential folder under ``$HOME`` (``~/.ssh``, ``~/.aws``
      and the rest of :data:`_SENSITIVE_HOME_DIRS`) — ``cat``, ``grep``, a copy, a one-liner? A
      command that only uses one, such as ``ssh -i ~/.ssh/key``, passes: those keys exist to be
      used by the tools that read them.
    * Does it show what such a folder HOLDS — name the folder itself, whatever it does with it
      (``ls``, ``find``, ``tree``, ``du``, a ``cd`` into it), or a glob the shell expands to the
      folder or into it (:func:`_shows_credential_folder`)? The folder is protected, not only the
      files inside it: a listing hands over the names of the keys and profiles it holds. Refused
      with the sentence a read of a file inside gets.
    * Does it NAME a file only its owner reads — PersonalClaw's own credential store and auth and
      audit material, in the active home and the default one, or another tool's sign-in
      (:class:`SensitivePaths` without the ``$HOME`` folders)? Refused whatever it does with the
      file, since nothing an agent runs needs to name one. Every path is read the way the
      command's shell would find it (:func:`~personalclaw.command_paths.named_paths`): the homes
      as a shell or a one-liner spells them, a relative path against *cwd* (the folder it runs
      in; the home's workspace when None) and every folder a ``cd`` moves to, through a link, a
      glob or a brace list.

    🔴 The second question was asked of ``$HOME`` alone, as spellings of
    ``~/.personalclaw/…``, so it knew the credential store only where the default home keeps it.
    On every container install (``PERSONALCLAW_HOME=/data``) ``cut -d= -f1 /data/.env`` listed the
    stored secrets' names and ``awk`` printed a key, and from the workspace inside the home
    ``cat ../.env`` read the same file on any install.

    Defence in depth: a command can build a path out of pieces no reading of its text sees.
    """
    normalised = strip_shell_quotes(_normalise_for_matching(command))
    if _get_sensitive_re().search(normalised):
        return _SHELL_REFUSAL[CREDENTIAL]
    owned = SensitivePaths(home_credential_dirs=False)
    folders = _credential_folders()
    for word, path in named_paths(command, cwd=cwd, names=owned.names):
        kind = owned.kind(str(path))
        if kind:
            return _SHELL_REFUSAL[kind]
        if _shows_credential_folder(word, path, folders):
            return _SHELL_REFUSAL[CREDENTIAL]
    return None


def _escaped_run_unit(stops: str, *, escaped: str | None = None) -> str:
    """One character of a free-form span a mask replaces, as a regex that reads escapes the way
    the text does.

    The span ends at whitespace and at any of *stops* (the body of a character class). A backslash
    is taken together with the character it escapes, and the span ends BEFORE a backslash that
    escapes one of *escaped* (default: *stops*). In JSON-escaped text ``\\"`` is a quote: a mask
    that took its backslash and left the quote turned ``\\"`` into ``"`` and closed the string
    early, so the masked text no longer parsed, and a save or an edit written against it addressed
    text the stored value does not hold. A backslash before whitespace or at the end of the text is
    taken like any other character. The three alternatives begin on disjoint characters, so a span
    is still matched in one linear pass.
    """
    escaped = stops if escaped is None else escaped
    return rf"(?:[^\s{stops}\\]|\\[^\s{escaped}]|\\(?=\s|\Z))"


# ── URL Exfiltration Detection ──
# Detects URLs whose query strings contain credential-like data.
# Domain-agnostic: we flag the PAYLOAD, not the destination.
# Any URL with secrets in query params is suspicious regardless of domain.

_URL_RE = re.compile(
    r"https?://([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})(:\d+)?(/" + _escaped_run_unit(r")\"'>") + r"*)?"
)

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

#: The value of a named cloud credential: everything up to whitespace, as it always was, except
#: that it stops at an escaped quote, which is where a JSON-escaped string around it closes. A value
#: that an escaped quote opens is taken with its closing one, so the text keeps its balance.
_TOKEN_UNIT = _escaped_run_unit("", escaped="'\"")
_TOKEN_VALUE = rf"(?:\\['\"]{_TOKEN_UNIT}+(?:\\['\"])?|{_TOKEN_UNIT}+)"

#: A ``name = value`` credential's value: eight or more characters up to whitespace, a comma, a
#: semicolon or a quote. The lookahead is the length test as it always read, so an escape changes
#: only where the value ends, never whether it is masked.
_ASSIGNED_VALUE = r"(?=[^\s,;'\"]{8})" + _escaped_run_unit(r",;'\"") + "+"

_CREDENTIAL_PATTERNS = re.compile(
    r"(?:"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}"  # AWS access key ID
    rf"|(?:SecretAccessKey|aws_secret_access_key)\s*[:=]\s*{_TOKEN_VALUE}"
    rf"|(?:SessionToken|aws_session_token)\s*[:=]\s*{_TOKEN_VALUE}"
    rf"|(?:AccessKeyId|aws_access_key_id)\s*[:=]\s*{_TOKEN_VALUE}"
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
    # GitHub fine-grained PAT: `github_pat_`, 22 characters, `_`, 59 more. The classic prefix
    # rule above cannot see it, so one pasted into a CLAUDE.md or an env block went through.
    r"|github_pat_[A-Za-z0-9_]{22,}"
    r"|hf_[A-Za-z0-9]{20,}"  # HuggingFace token (static, broad-privilege)
    r"|AIza[0-9A-Za-z_-]{35}"  # Google API key
    # Measured while wiring the ConfirmationRequest preview: the patterns above missed
    # THREE shapes that a real payload carries. `sk-[A-Za-z0-9]{32,}` cannot match a key with
    # hyphens or underscores in the body (`sk-live-ABC...`), and there was no generic
    # assignment or bearer form at all — so `api_key=<anything>` and
    # `Authorization: Bearer <jwt>` both survived into a redacted preview. A preview is the
    # single most likely place for a fetched credential to reach an inbox row.
    r"|sk-[A-Za-z0-9][A-Za-z0-9_-]{20,}"  # provider keys with hyphens/underscores in the body
    # Generic `key = value` credential assignment. Keyed on the NAME so the value's shape does
    # not have to be guessed — an unknown provider's key format is exactly what a shape-based
    # pattern misses.
    #
    # A value that is already one of this module's masks is not a credential, so the pattern
    # does not take it for one (`(?!\[REDACTED:)`). Without that, a second pass over
    # `password: [REDACTED: credential]` read `[REDACTED:` as the password and wrote
    # `[REDACTED: credential] credential]`, losing the field name; and an editor's save that
    # restored masks from such a view put the stored value back without its label. It is the
    # only alternative here that could match a mask, so a second pass of `redact_credentials`,
    # or of `redact_for_display` and `redact_field` built on it, changes nothing.
    r"|(?i:api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret"
    rf"|password|passwd|private[_-]?key)\s*[:=]\s*(?!\[REDACTED:){_ASSIGNED_VALUE}"
    # `Authorization: Bearer <token>` / a bare bearer token.
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/-]{16,}=*"
    r")",
)

# Base64 alphabet: at least 40 chars of [A-Za-z0-9+/] ending with optional =
_B64_CHUNK_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def _b64_credential(chunk: str) -> str:
    """Decode ONE already-isolated base64 chunk; return it if it holds a credential, else ''.

    Split out from `_decode_b64_safe` because its only production caller already holds a
    complete `_B64_CHUNK_RE` match and was paying for a second scan of it (#2717). Re-scanning
    a match is provably a no-op here — `_B64_CHUNK_RE` is `[A-Za-z0-9+/]{40,}={0,2}` with no
    anchor and no lookaround, so running it over one of its own greedy matches yields exactly
    that match back — but "provably a no-op" is a reason to remove the scan, not to keep it.
    """
    import base64

    try:
        decoded = base64.b64decode(chunk, validate=True).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    return decoded if _CREDENTIAL_PATTERNS.search(decoded) else ""


def _decode_b64_safe(text: str) -> str:
    """Try to base64-decode chunks in text; return decoded content or ''.

    Kept as the general entry point (arbitrary text, unknown chunk boundaries). The
    per-chunk work lives in `_b64_credential` so the caller that already has a chunk can
    skip the scan.
    """
    for m in _B64_CHUNK_RE.finditer(text):
        decoded = _b64_credential(m.group())
        if decoded:
            return decoded
    return ""


def strip_url_userinfo(text: str) -> str:
    """*text* with the user name and password taken out of every URL in it:
    ``http://ada:pw@proxy:3128`` becomes ``http://proxy:3128``.

    For a value that must keep WORKING without its credential, where a redaction tag would be a
    wrong password sent to the host: a proxy address handed to a child process
    (``sandbox.build_child_env``) still routes through the proxy, and a proxy that wants a password
    answers 407 at once instead of the child waiting on a connection that never comes.
    """
    out: list[str] = []
    pos = 0
    for start, end, scheme in address_logins.url_userinfo_spans(text):
        out.append(text[pos:start])
        out.append(f"{scheme}://")
        pos = end
    if not out:
        return text
    out.append(text[pos:])
    return "".join(out)


# 🔴 A WEBHOOK URL IS ITS OWN CREDENTIAL. An incoming webhook (Slack, Discord, Teams, Zapier, a
# `hooks.` host) needs no header and no login: whoever has the URL can post as you, because the
# secret is the PATH. No rule above sees one — there is no userinfo, no query, no key name and no
# token shape — so a Slack webhook pasted into a CLAUDE.md went into memory as it was.
#
# Like the userinfo pass, it keeps the scheme and the host and replaces only the secret, so a
# redacted log still says which service a request went to. The path class excludes whitespace,
# quotes and brackets, so the redaction tag (which holds a space and brackets) can never match
# again: idempotent by construction. Each alternative is one literal-led host with no nested
# ambiguity, so the scan stays linear in the length of the text.
_WEBHOOK_URL_RE = re.compile(
    r"\b(?P<scheme>https?)://"
    r"(?P<host>"
    r"hooks\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"  # hooks.slack.com, hooks.zapier.com, …
    r"|(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks"
    r"|[A-Za-z0-9-]+\.webhook\.office\.com/webhookb2"
    r"|outlook\.office(?:365)?\.com/webhook"
    r")"
    # The length test is a lookahead, so an escape (`_escaped_run_unit`) moves only the end.
    r"/(?=[^\s\"'<>()\[\]{}]{8})" + _escaped_run_unit(r"\"'<>()\[\]{}") + "+",
    re.IGNORECASE,
)

_WEBHOOK_TAG = "[REDACTED: webhook]"


def redact_webhook_urls(text: str) -> tuple[str, list[str]]:
    """Replace the secret part of every incoming-webhook URL, keeping scheme and host."""
    warnings: list[str] = []

    def _tag(m: "re.Match[str]") -> str:
        warnings.append(f"Redacted a webhook URL to {m.group('host').split('/')[0]}")
        return f"{m.group('scheme')}://{m.group('host')}/{_WEBHOOK_TAG}"

    result = _WEBHOOK_URL_RE.sub(_tag, text)
    return result, warnings


def redact_credentials(text: str) -> tuple[str, list[str]]:
    """Redact raw credential patterns from text, including base64-encoded.

    Returns (cleaned_text, list_of_warnings).
    """
    warnings: list[str] = []

    # 0. The login an address carries (`address_logins`), FIRST, so a credential in a URL redacts
    #    the same way whatever its shape: a URL's userinfo, then an scp-style login, which has no
    #    `://`. Then a webhook URL, whose path is the credential.
    result, url_warnings = address_logins.redact_url_userinfo(text)
    warnings.extend(url_warnings)
    result, scp_warnings = address_logins.redact_scp_logins(result)
    warnings.extend(scp_warnings)
    result, webhook_warnings = redact_webhook_urls(result)
    warnings.extend(webhook_warnings)

    # 1. Redact plaintext credential patterns — ONE pass, splicing the spans the scan found.
    #
    # 🔴 This used to be `finditer` with `result = result.replace(matched, tag, 1)` INSIDE the
    # loop, i.e. a fresh copy of the whole document per match: O(matches x length). Measured on a
    # 1 MB document with 23,831 credentials, that loop was 1.318s of 1.367s total — 96% of the
    # time — and `acp/translate.py` calls this 5+ times per agent message, so a leaked env dump or
    # a token-heavy CI log cost seconds on a live turn path (#2717).
    #
    # 🪤 WHY THIS WAS NOT A DROP-IN, and why the equivalence had to be argued rather than assumed:
    # `str.replace(matched, tag, 1)` rewrites the LEFTMOST occurrence of the matched TEXT, not the
    # occurrence at the position the scan found. Those can differ in principle, so this is a
    # behaviour question, not an optimisation — which is why #2716 deliberately left it alone
    # rather than mix it into a change whose whole safety argument was byte-identical output.
    #
    # They cannot differ here, for two reasons that hold together:
    #   * `finditer` yields NON-OVERLAPPING matches in position order, and these patterns are
    #     shape/name based with no anchor or lookaround — so any earlier copy of a matched string
    #     is itself a match, and was therefore already replaced by an earlier iteration. Leftmost
    #     and found converge.
    #   * A replacement inserts only the fixed tag, so the only way to break that is for the tag
    #     to help SYNTHESISE a new, earlier match. `tests/test_redaction_span_splice.py` fuzzes
    #     exactly that, seeding tag-lookalike filler on purpose.
    # Verified differentially over 200,000 generated documents (35% deliberate repeats): zero
    # divergence. `sub` with a callback keeps the warning order the old loop produced.
    def _tag_credential(m: "re.Match[str]") -> str:
        warnings.append(f"Redacted credential pattern: {m.group()[:20]}...")
        return "[REDACTED: credential]"

    result = _CREDENTIAL_PATTERNS.sub(_tag_credential, result)

    # 2. Detect and redact base64-encoded credentials.
    #
    # Still a per-match `replace`, deliberately: this scan runs over the ORIGINAL `text` while the
    # replacement lands in `result`, which pass 1 has already rewritten. The two strings have
    # different lengths and different content, so a span from one does not address the other and a
    # splice is not available without changing what gets redacted. Left as-is because it is not the
    # cost: in the 1 MB measurement above, passes 1 and 2 together were 1.367s of which pass 1 was
    # 1.318s. `_b64_credential` replaces `_decode_b64_safe` here to skip re-scanning a chunk that
    # is already a whole match.
    for m in _B64_CHUNK_RE.finditer(text):
        chunk = m.group()
        if _b64_credential(chunk):
            result = result.replace(chunk, "[REDACTED: encoded credential]", 1)
            warnings.append(f"Redacted base64-encoded credential ({len(chunk)} chars)")

    return result, warnings


# Every mask this module writes. One expression, because `restore_masked_spans` has to
# recognise exactly what `redact_for_display` produces — a mask the inverse cannot see is a
# mask that gets persisted over real content.
_MASK_RE = re.compile(r"\[REDACTED:[^\]\n]*\]")


def redact_for_display(text: str) -> str:
    """The display mask, defined once: credentials then exfiltration URLs.

    Read paths that hand content to a UI apply BOTH redactors, in this order. Naming the
    composition here is what lets `restore_masked_spans` be a true inverse instead of a
    second, drifting guess at what a mask looks like.
    """
    masked, _ = redact_credentials(text)
    masked, _ = redact_exfiltration_urls(masked)
    return masked


def redact_for_model(text: str) -> str:
    """The mask on every text an agent's model is handed: what a tool answers, the stored text a
    prompt is assembled from, a spawned agent's task.

    It is the display mask, deliberately. The model is shown the same ``[REDACTED: …]`` chips the
    user's views show, so every save that puts a mask back (:func:`keep_masked_spans`,
    :func:`masked_edit`, :func:`keep_masked_lines`) restores a value an agent echoes exactly as it
    restores one the user's editor echoes. Idempotent, so a read that was already masked for a UI
    passes through unchanged.

    Nothing that needs a secret's value reads it back through here: a tool that needs one takes a
    ``{{secret:KEY}}`` reference and resolves it when it runs (``triggers.secrets.resolve``).
    """
    return redact_for_display(text)


#: Characters written as a visible escape rather than raw: C0 controls but TAB, DEL, the C1
#: controls (NEL among them) and the two Unicode separators some viewers break a line on.
_UNSAFE_OUTPUT_CHARS = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029]")
#: The same set less the line feed, for text a person reads with its line breaks kept.
_UNSAFE_OUTPUT_CHARS_BUT_LF = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")


def _visible_escape(match: "re.Match[str]") -> str:
    ch = match.group()
    if ch == "\n":
        return "\\n"
    if ch == "\r":
        return "\\r"
    return f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}"


def mask_child_output(
    output: "str | bytes | None",
    *,
    limit: int | None = 200,
    tail: bool = False,
    one_line: bool = True,
) -> str:
    """What a child process printed, fit to be written into a log line or an error message.

    A child's output is not PersonalClaw's own text. A hook, git, pip or a bundler can print a
    credential it read (a token, a URL with a login in it, a key from a file it opened), and what
    PersonalClaw writes that output into, the gateway log or an error a caller logs and shows,
    outlives the run and is read by people and by tools. So the output is:

    * masked the way every view masks it (:func:`redact_for_display`), BEFORE it is cut, so a
      credential that straddles the cut is masked whole instead of leaving half of it behind;
    * cut to *limit* characters (none are cut when it is ``None``): the start, or the end when
      *tail* is set, which is where a failing installer or git prints its reason;
    * written with every control character as a visible escape (``\\x1b``). With *one_line*, the
      default, a line break is one too (``\\n``), so a child cannot start a line that reads as a
      record of the log's own. ``one_line=False`` keeps line breaks, for an error a person reads.
    """
    if output is None:
        return ""
    if isinstance(output, (bytes, bytearray)):
        text = bytes(output).decode("utf-8", "replace")
    else:
        text = str(output)
    text = redact_for_display(text.strip())
    if limit is not None and len(text) > limit:
        text = text[-limit:] if tail else text[:limit]
    if one_line:
        return _UNSAFE_OUTPUT_CHARS.sub(_visible_escape, text)
    text = text.replace("\r\n", "\n")
    return _UNSAFE_OUTPUT_CHARS_BUT_LF.sub(_visible_escape, text)


class MaskingFormatter(logging.Formatter):
    """A log formatter that masks what it writes, the way every view masks
    (:func:`redact_for_display`).

    Every sink the gateway's log records reach uses it: the ``gateway.log`` file, the console
    stream a service manager keeps (launchd's log files, the systemd journal), and the Logs
    page's buffer and live stream. The record is masked after it is formatted, so a credential
    is masked wherever it sits: in the message, an argument, an exception's text or a traceback
    line. A call site that writes what a child printed still passes it through
    :func:`mask_child_output`, which also keeps it on one line; this is the floor under every
    record, not a replacement for that.

    A record it cannot mask (the masker raised, or the record's arguments do not fit its message)
    is written as its time, level and logger with its words withheld. It is never written as it
    came, and never left to the handler's error path, which writes a failing record's message and
    arguments to stderr unmasked; the sinks' handlers withhold on their own failures too
    (:class:`WithholdingHandler`).
    """

    def format(self, record: logging.LogRecord) -> str:
        try:
            return redact_for_display(super().format(record))
        except Exception as exc:  # noqa: BLE001 - a record it cannot mask is withheld
            return self._withheld(record, exc)

    def _withheld(self, record: logging.LogRecord, exc: Exception) -> str:
        """*record*'s time, level and logger, which are PersonalClaw's own, and why the rest is
        not shown."""
        stand_in = logging.makeLogRecord(
            {
                **record.__dict__,
                "msg": f"[log record withheld: it could not be masked ({type(exc).__name__})]",
                "args": None,
                "exc_info": None,
                "exc_text": None,
                "stack_info": None,
            }
        )
        try:
            return super().format(stand_in)
        except Exception:  # noqa: BLE001 - not even the record's own fields could be read
            return str(stand_in.msg)


class WithholdingHandler(logging.Handler):
    """A log handler whose failure path writes none of the record's words.

    ``logging.Handler.handleError`` writes a record it could not emit to stderr as it came: its
    message and its arguments, unmasked. For the gateway, stderr is the console its service
    manager keeps, so a ``gateway.log`` on a full disk would put each record it could not take
    there raw. This names the record that could not be written and why, and nothing it said.
    """

    def handleError(self, record: logging.LogRecord) -> None:
        if not (logging.raiseExceptions and sys.stderr):
            return
        failure = sys.exc_info()[1]
        try:
            sys.stderr.write(
                f"--- a log record from {record.name} ({record.filename}:{record.lineno}) could "
                f"not be written ({type(failure).__name__}); its words are withheld ---\n"
            )
        except Exception:  # noqa: BLE001 - nowhere is left to say it
            pass


class MaskedStreamHandler(WithholdingHandler, logging.StreamHandler):
    """The console of a masked sink. Give it :class:`MaskingFormatter`."""


class MaskedRotatingFileHandler(WithholdingHandler, logging.handlers.RotatingFileHandler):
    """The ``gateway.log`` of a masked sink. Give it :class:`MaskingFormatter`."""


#: What stands in for a value a tool handed to the code it ran, in that code's output. The same
#: text as a shape-found credential's mask, so the inverses and the model read it the same way.
_KNOWN_VALUE_MASK = "[REDACTED: credential]"

#: Shorter values are not masked by value: `1`, `true` or a region name would mask every
#: occurrence of an ordinary word. A secret that short is not one a mask can protect.
_KNOWN_VALUE_MIN_LEN = 8


def redact_known_values(text: str, values: Iterable[str]) -> str:
    """*text* with every occurrence of each of *values* replaced by a credential mask.

    For the tool that itself handed those values out: a command it ran with a resolved
    ``{{secret:KEY}}`` or a credential in its environment. The shape-based mask cannot see a
    password like ``correct-horse-battery``, and this tool knows it exactly. Longest first, so a
    value that contains another is replaced whole.
    """
    wanted = sorted(
        {v for v in values if isinstance(v, str) and len(v) >= _KNOWN_VALUE_MIN_LEN},
        key=len,
        reverse=True,
    )
    if not text or not wanted:
        return text
    pattern = re.compile("|".join(re.escape(v) for v in wanted))
    return pattern.sub(_KNOWN_VALUE_MASK, text)


def redact_values_for_display(value: Any) -> Any:
    """:func:`redact_for_display` over every string in a JSON-shaped value.

    For a read that hands out a structured blob, a trigger's action or a loop's plan, whose strings
    are text a user or an agent wrote. Dict keys and non-string leaves pass through unchanged. The
    inverse a save uses is :func:`keep_masked_values`.
    """
    if isinstance(value, str):
        return redact_for_display(value)
    if isinstance(value, dict):
        return {key: redact_values_for_display(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_values_for_display(item) for item in value]
    return value


def redact_field(text: str) -> str:
    """Both redaction passes over one EXPORTED field: exfiltration URLs then credentials.

    Applied to EVERY role, unlike the dashboard write path, which deliberately exempts
    ``user`` and ``system`` (``chat_persistence.py``) — so this is the only redaction those
    roles ever get before text leaves the machine, and defense in depth for the rest.

    One implementation across every surface that emits a transcript: conversation export and
    share (``dashboard/session_export``, ``dashboard/session_share``) and the room transcript
    write path (``rooms/store``). It lives here rather than in ``dashboard/`` because
    ``rooms/`` is domain code and may not import the HTTP surface — an upward edge the
    structural import-direction ratchet refuses.

    Distinct from :func:`redact_for_display` in pass ORDER, which is load-bearing: run first,
    the exfiltration pass masks the whole of a URL whose query carries a key shape it knows (an
    AWS key id, a Slack token, a long encoded blob), while the display order masks the key and
    keeps the URL. The two compositions give different text, so neither can be expressed as the
    other.
    """
    if not text:
        return ""
    safe, _ = redact_exfiltration_urls(str(text))
    safe, _ = redact_credentials(safe)
    return safe


def _mask_pairs(masked: str, stored: str) -> list[tuple[str, str]] | None:
    """Pair each mask in `masked` with the text it replaced in `stored`.

    Deterministic rather than fuzzy: the literal segments AROUND the masks are unchanged by
    redaction, so walking them through `stored` in order isolates each masked span exactly.
    Returns None when the walk cannot account for the whole string — the caller must then
    refuse the write rather than guess (a wrong guess writes a secret into the wrong place).
    """
    literals = _MASK_RE.split(masked)
    masks = _MASK_RE.findall(masked)
    pairs: list[tuple[str, str]] = []
    pos = 0
    for i, mask in enumerate(masks):
        prefix = literals[i]
        if not stored.startswith(prefix, pos):
            return None
        pos += len(prefix)
        following = literals[i + 1]
        # The masked span runs up to where the next unchanged literal resumes; for a mask at
        # the very end of the content, to the end of the stored text.
        end = stored.find(following, pos) if following else len(stored)
        if end < pos:
            return None
        pairs.append((mask, stored[pos:end]))
        pos = end
    if stored[pos:] != literals[-1]:
        return None
    return pairs


def restore_masked_spans(submitted: str, stored: str) -> str | None:
    """Put back any span the client is echoing as one of OUR OWN masks.

    Redaction is a display safeguard, so an editor seeded from a redacted read sends the mask
    back verbatim — and a write path with no inverse persists `[REDACTED: credential]` over the
    real content, irreversibly, on an edit the user never made to that span. This is the
    inverse: each mask in `submitted` is restored from the corresponding span of `stored`, in
    order, so every OTHER edit in the same save still lands.

    The mask is never lifted onto the wire — the plaintext only ever moves from the store back
    into the store — so the read path keeps masking unconditionally and no endpoint has to
    serve a secret to un-break editing.

    A mask is treated as a PLACEHOLDER standing for the n-th hidden value, so it restores
    wherever the user left it, even in a heavily rewritten body: keeping the marker means
    "keep the value it stands for". Deleting the marker deletes the value — that is a real
    instruction and is honoured. Two identical markers are therefore interchangeable: reordering
    them swaps which span each value lands in, which is why the mask text names its KIND.

    Returns the content to persist, or None when the stored value cannot be walked to recover
    what each mask replaced; the caller must then refuse the write rather than guess, since the
    alternative is persisting a mask over real content.
    """
    if not _MASK_RE.search(submitted):
        return submitted  # nothing echoed back → an ordinary edit, unchanged
    masked = redact_for_display(stored)
    if masked == stored:
        # Nothing in the STORED value is masked, so any mask-looking text in the submission is
        # the user's own writing. Hands off.
        return submitted
    if submitted == masked:
        return stored  # an untouched round-trip (e.g. a title-only edit) → keep the store as-is
    pairs = _mask_pairs(masked, stored)
    if pairs is None:
        return None
    pending: dict[str, list[str]] = {}
    for mask, original in pairs:
        pending.setdefault(mask, []).append(original)

    def _take(m: "re.Match[str]") -> str:
        queue = pending.get(m.group(0))
        # A mask with no original left to give is text the user typed themselves — leave it.
        return queue.pop(0) if queue else m.group(0)

    return _MASK_RE.sub(_take, submitted)


#: What a save answers when it echoes one of our masks and the stored text that mask stood for can
#: no longer be found. One sentence for every save path that restores masks.
MASK_CONFLICT = (
    "The stored copy of this content no longer lines up with the redacted version you edited, "
    "so the hidden value behind a [REDACTED: …] marker cannot be recovered. Nothing was saved. "
    "Re-open it to load the current version, or replace the marker with the value you want "
    "stored."
)


class MaskConflict(ValueError):
    """A save echoed a display mask whose stored value can no longer be located, or could only
    be placed by guessing. *message* is the sentence the refusal answers with."""

    def __init__(self, message: str = MASK_CONFLICT) -> None:
        super().__init__(message)


#: What an agent's file write answers when it would change, move or copy a value hidden from it.
HIDDEN_VALUE_KEPT = (
    "A [REDACTED: …] marker stands for a value this file holds that you were not shown. It can "
    "stay where it is, but it cannot be moved, copied or rewritten from its marker, and this "
    "change would do that. Nothing was written. Change only the text around a marker (edit_file "
    "does that exactly), and leave changing a hidden value to the user."
)

#: What an agent's edit answers when the text it names begins or ends part-way into a marker.
MARKER_CUT = (
    "old_str begins or ends inside a [REDACTED: …] marker, which stands for a value this file "
    "holds that you were not shown. Nothing was written. Include the whole marker in old_str, or "
    "none of it."
)


def mask_markers(text: str) -> list[str]:
    """Every ``[REDACTED: …]`` marker in *text*, in order."""
    return _MASK_RE.findall(text)


def _mask_segments(masked: str, stored: str) -> list[tuple[int, int, int, int]] | None:
    """Where each marker of *masked* sits and the stored span it stands for, in order:
    ``(masked_start, masked_end, stored_start, stored_end)``. ``None`` when the walk
    (:func:`_mask_pairs`) cannot account for the whole stored text."""
    pairs = _mask_pairs(masked, stored)
    if pairs is None:
        return None
    literals = _MASK_RE.split(masked)
    segments: list[tuple[int, int, int, int]] = []
    shown = kept = 0
    for index, (mask, original) in enumerate(pairs):
        shown += len(literals[index])
        kept += len(literals[index])
        segments.append((shown, shown + len(mask), kept, kept + len(original)))
        shown += len(mask)
        kept += len(original)
    return segments


def _stored_offset(segments: list[tuple[int, int, int, int]], pos: int) -> int | None:
    """The stored offset that masked offset *pos* stands for; ``None`` when *pos* falls strictly
    inside a marker, where no stored offset corresponds to it."""
    shift = 0
    for shown_start, shown_end, _kept_start, kept_end in segments:
        if pos <= shown_start:
            break
        if pos < shown_end:
            return None
        shift = kept_end - shown_end
    return pos + shift


def masked_edit(stored: str, old: str, new: str, *, replace_all: bool = False) -> tuple[str, int]:
    """*stored* with *old* replaced by *new*, where both were written against the masked view of
    it (:func:`redact_for_display`): an agent editing a file it was shown masked.

    Each occurrence is found in the MASKED text and mapped back onto the stored one, so everything
    outside it keeps its stored bytes exactly, hidden values included. A marker in *new* is put back
    from the hidden values inside the text it replaces, in order; dropping one drops that value.

    Returns ``(text, occurrences)``, the occurrences counted in the masked text: the caller refuses
    none, and more than one without *replace_all*, as it would any edit. Raises
    :class:`MaskConflict` when an occurrence begins or ends inside a marker (:data:`MARKER_CUT`),
    when *new* holds more markers of a kind than the text it replaces hides
    (:data:`HIDDEN_VALUE_KEPT`: a hidden value cannot be copied or moved), or when the masked text
    cannot be walked back onto the stored one.
    """
    shown = redact_for_display(stored)
    count = shown.count(old) if old else 0
    if count == 0 or (count > 1 and not replace_all):
        return stored, count
    times = count if replace_all else 1
    if shown == stored:
        return stored.replace(old, new, times), count
    segments = _mask_segments(shown, stored)
    if segments is None:
        raise MaskConflict()
    out: list[str] = []
    copied = searched = 0
    for _ in range(times):
        start = shown.find(old, searched)
        end = start + len(old)
        kept_start, kept_end = _stored_offset(segments, start), _stored_offset(segments, end)
        if kept_start is None or kept_end is None:
            raise MaskConflict(MARKER_CUT)
        hidden: dict[str, list[str]] = {}
        for shown_start, shown_end, orig_start, orig_end in segments:
            if start <= shown_start and shown_end <= end:
                hidden.setdefault(shown[shown_start:shown_end], []).append(
                    stored[orig_start:orig_end]
                )

        def _take(m: "re.Match[str]", hidden: dict[str, list[str]] = hidden) -> str:
            queue = hidden.get(m.group(0))
            if not queue:
                raise MaskConflict(HIDDEN_VALUE_KEPT)
            return queue.pop(0)

        out.append(stored[copied:kept_start])
        out.append(_MASK_RE.sub(_take, new))
        copied, searched = kept_end, end
    out.append(stored[copied:])
    return "".join(out), count


def keep_masked_lines(submitted: str, stored: str) -> str:
    """The text a whole-file write persists when it was written against the masked view of
    *stored* (:func:`redact_for_display`): an agent overwriting a file it was shown masked.

    Aligned line by line, so a line the write keeps exactly as it was shown keeps its stored bytes,
    hidden values included. A hidden value can only stay on its own unchanged line: a write that
    changes, removes, moves or copies a line holding a marker raises :class:`MaskConflict`
    (:data:`HIDDEN_VALUE_KEPT`), because which value it would mean cannot be known, and
    :func:`masked_edit` changes the text around a marker exactly. A file with nothing hidden is
    written as submitted.
    """
    shown = redact_for_display(stored)
    if submitted == shown:
        return stored
    if shown == stored:
        return submitted
    segments = _mask_segments(shown, stored)
    if segments is None:
        raise MaskConflict()
    import difflib

    shown_lines = shown.splitlines(keepends=True)
    submitted_lines = submitted.splitlines(keepends=True)
    starts = [0]
    for line in shown_lines:
        starts.append(starts[-1] + len(line))
    out: list[str] = []
    matcher = difflib.SequenceMatcher(None, shown_lines, submitted_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            kept_start = _stored_offset(segments, starts[i1])
            kept_end = _stored_offset(segments, starts[i2])
            if kept_start is None or kept_end is None:
                raise MaskConflict()
            out.append(stored[kept_start:kept_end])
            continue
        if any(_MASK_RE.search(line) for line in (*shown_lines[i1:i2], *submitted_lines[j1:j2])):
            raise MaskConflict(HIDDEN_VALUE_KEPT)
        out.extend(submitted_lines[j1:j2])
    return "".join(out)


def keep_masked_spans(submitted: str, stored: str) -> str:
    """The text a save persists when its editor was seeded from a :func:`redact_for_display` read.

    Every such save path calls this with the value it is about to overwrite, so a mask the client
    echoes back keeps the stored value it stood for (:func:`restore_masked_spans`). Raises
    :class:`MaskConflict` rather than persisting a mask it cannot place.
    """
    restored = restore_masked_spans(submitted, stored)
    if restored is None:
        raise MaskConflict()
    return restored


def keep_masked_values(submitted: Any, stored: Any) -> Any:
    """:func:`keep_masked_spans` over a JSON-shaped value: a loop's plan, a schedule's action.

    Each string is restored against the string stored at the same place, the same key of a dict
    or the same index of a list. A list item is first matched to a stored item that showed exactly
    what the client sent, so moving an item it did not edit keeps its value. A place where the
    store holds no string is the client's own text and passes through.
    """
    if isinstance(submitted, str):
        return keep_masked_spans(submitted, stored) if isinstance(stored, str) else submitted
    if isinstance(submitted, dict):
        base = stored if isinstance(stored, dict) else {}
        return {key: keep_masked_values(value, base.get(key)) for key, value in submitted.items()}
    if isinstance(submitted, list):
        items = stored if isinstance(stored, list) else []
        used: set[int] = set()

        def _shown_as(index: int, value: str) -> bool:
            item = items[index]
            return index not in used and isinstance(item, str) and redact_for_display(item) == value

        kept: list[Any] = []
        for index, value in enumerate(submitted):
            if isinstance(value, str) and _MASK_RE.search(value):
                # The same place first, then any other stored item not yet claimed. Two items
                # that show alike are one value each, and no stored value is given out twice.
                order = [index] if index < len(items) else []
                order += [i for i in range(len(items)) if i != index]
                match = next((i for i in order if _shown_as(i, value)), None)
                if match is not None:
                    used.add(match)
                    kept.append(items[match])
                    continue
            kept.append(keep_masked_values(value, items[index] if index < len(items) else None))
        return kept
    return submitted


def stored_name(shown: str, stored: Iterable[str]) -> str | None:
    """The stored name a client means by *shown*, a name it may have been shown masked.

    For a name that identifies a stored thing, a tag or a memory fact's key, rather than text that
    is kept: removing a masked tag has to remove the real one. *shown* itself when a stored name is
    written exactly so or it carries no mask; the one stored name that shows as it otherwise;
    ``None`` when none does. Raises :class:`MaskConflict` when two do, because the client cannot
    have told them apart.
    """
    if not _MASK_RE.search(shown):
        return shown
    names = list(stored)
    if shown in names:
        return shown
    matches = [name for name in names if redact_for_display(name) == shown]
    if len(matches) > 1:
        raise MaskConflict()
    return matches[0] if matches else None


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


#: What stands in for a text its masker failed on: fixed words, and none of the text.
WITHHELD_TEXT = "[redaction failed; text withheld]"


def redact_or_withhold(text: str) -> str:
    """*text* through :func:`redact`, or :data:`WITHHELD_TEXT` when the masker itself fails.

    For each text PersonalClaw masks before it shows, sends or stores it: a health message, a
    notification, a journal or crash record, a proposal. A masker that raised proves nothing
    about what the text holds, so none of it goes on, and the failure is logged by its type only.
    """
    try:
        return redact(text)
    except Exception as exc:  # noqa: BLE001 - any failure to mask withholds the text
        logger.warning("a text was withheld: it could not be masked (%s)", type(exc).__name__)
        return WITHHELD_TEXT


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

#: EITHER fence marker in ANY form — bare, attributed, or self-closing — for the body-escaping half
#: of `fence_untrusted`. Group 1 is the optional `/` of a close tag and group 2 is whatever followed
#: the tag name, so the substitution can escape the brackets while keeping the text legible.
#:
#: 🔴 Deliberately NOT derived from `_OPEN_TAG_RE`: that one answers "is this already fenced?", where
#: matching only the OPEN tag is correct. Escaping a body has to catch both, and an attributed CLOSE
#: (`</untrusted_content >`) is the one that ends a span early.
_UNTRUSTED_TAG_RE = re.compile(r"<(/?)untrusted_content((?:\s[^>]*)?/?)>", re.IGNORECASE)


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
    #
    # 🔴 ANY form of the tag, not the two bare spellings (#3112). The two literal replaces this
    # used to be missed an ATTRIBUTED tag: measured, a crafted claim carrying
    # `<untrusted_content source=knowledge>` came through a fenced span verbatim, so the model read
    # a span it had no way to tell from the real wrapper this function emits two lines below —
    # and a body that re-opens the fence is how a crafted close marker is made to look balanced.
    # The regex also covers `</untrusted_content bar>` and a self-closing `<untrusted_content/>`.
    # It only ever escapes MORE than before, so no previously-neutralised shape is released.
    safe = _UNTRUSTED_TAG_RE.sub(
        lambda m: f"&lt;{m.group(1)}untrusted_content{m.group(2)}&gt;", text
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

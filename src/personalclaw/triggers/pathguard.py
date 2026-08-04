"""PathGuard — realpath + symlink matching for the `paths` capability (decision 7 — S118).

**🔴 THE DEFECT THIS CLOSES.** `paths` has been a first-class member of `CAPABILITY_KEYS` since
S69, fail-closed like every other key, and rendered as a fence in the UI. But `capability_allows`
compares it with `_matches_entry` — **string matching**, built for tool names like
`mcp__github__*`. Paths are not strings for security purposes. Driven before a line was written,
against the real function:

    allowlist: ["/Users/me/notes/*"]

      ALLOW  /Users/me/notes/today.md                 # correct
      deny   /Users/me/secrets.txt                    # correct
      ALLOW  /Users/me/notes/../../.ssh/id_rsa        # 🔴 TRAVERSAL — the fence let it through
      ALLOW  /Users/me/notes/../.aws/credentials      # 🔴 TRAVERSAL

    allowlist: ["/Users/me/notes"]
      ALLOW  /Users/me/notes                          # correct
      deny   /Users/me/notesEVIL                      # correct only by accident of this entry

So a trigger fenced to a notes directory could read an SSH key, and the ledger would record the fire
as permitted. The fence was not weak, it was measuring the wrong thing.

**What this module adds, and why each part exists:**

* **Canonicalization before comparison** (`os.path.realpath` + `expanduser`). `..` is resolved away,
  so a traversal is compared as what it actually reaches rather than as the text the author typed.
* **Symlink-target matching**, which is the half canonicalization alone does not cover for the
  ALLOWLIST side: a watched directory that is itself a symlink into `/etc` must be compared at its
  target. `realpath` on both sides is what makes the two consistent.
* **Boundary-aware containment.** `startswith` on a directory is the classic prefix-sibling bug:
  `/Users/me/notes` would contain `/Users/me/notesEVIL`. Containment requires an exact match or a
  separator immediately after the root.
* **Sensitive paths are refused even when explicitly allowlisted.** `security.is_sensitive_path`
  already knows the credential locations; an allowlist entry naming one is far likelier to be a
  mistake (or an injected edit) than an intention, and `bypass_immune` in decision 7's own text
  reserves checks no allowlist may silence.

**Fail-CLOSED, unlike the kill switch.** An unresolvable path denies. That asymmetry is deliberate
and matches S117's reasoning from the other side: a stuck-closed kill switch stops work the user
depends on and looks like a broken scheduler, while a stuck-open path fence hands out filesystem
access nobody granted. When in doubt about *reach*, refuse.

**This module does not walk the filesystem** and never opens a file. It answers "would this path
be inside that scope", so it is pure, cheap enough for a per-action check, and safe to call on a
path that does not exist yet (a write target usually does not).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def canonicalize(raw: str) -> str:
    """`raw` as an absolute, symlink-resolved, `..`-free path. "" when it cannot be resolved.

    `expanduser` first so `~/notes` means what its author meant, then `realpath`, resolving both
    `..` and symlinks. Returns "" rather than raising: every caller here treats "" as a refusal, so
    an unresolvable path fails closed at one place instead of at each call site.
    """
    if not raw or not isinstance(raw, str):
        return ""
    try:
        return os.path.realpath(os.path.expanduser(os.path.expandvars(raw)))
    except (OSError, ValueError):  # a path so malformed the OS will not canonicalize it
        logger.debug("pathguard: could not canonicalize %r", raw, exc_info=True)
        return ""


def is_within(candidate: str, root: str) -> bool:
    """Whether `candidate` resolves to `root` itself or somewhere beneath it.

    Both sides are canonicalized, which is what makes a traversal or a symlinked root compare as
    what it REACHES rather than as what it says.

    Containment is checked with `os.path.commonpath`, not `startswith`: a prefix test reads
    `/Users/me/notesEVIL` as inside `/Users/me/notes`, which is the classic sibling-directory bypass
    and the kind of bug that survives review because the code looks obviously correct.
    """
    real_candidate = canonicalize(candidate)
    real_root = canonicalize(root)
    if not real_candidate or not real_root:
        return False
    if real_candidate == real_root:
        return True
    try:
        return os.path.commonpath([real_candidate, real_root]) == real_root
    except ValueError:
        # Different drives, or one relative path that survived canonicalization — not comparable,
        # so not contained.
        return False


def path_allowed(allowlist: object, candidate: str) -> tuple[bool, str]:
    """Whether the frozen `paths` allowlist permits `candidate`. Returns `(allowed, reason)`.

    Mirrors `capability_allows`' refusal discipline deliberately, so the two fences behave the same
    way on a malformed block: an absent, empty or non-list allowlist denies, and a non-string entry
    is skipped rather than coerced.

    A trailing `/*` or `*` on an entry means "and everything beneath it", which is how a user writes
    a directory scope. It is NOT fnmatch: an entry is a path root, and `commonpath` decides
    containment. Treating `*` as a general wildcard would let `/Users/*/notes` read as a scope while
    matching things its author never enumerated.
    """
    if not allowlist:
        return False, "this trigger declares no paths, so no filesystem access is permitted"
    if isinstance(allowlist, str) or not isinstance(allowlist, (list, tuple, set, frozenset)):
        return (
            False,
            "the paths allowlist must be a list; a "
            f"{type(allowlist).__name__} is refused rather than coerced, so a malformed fence "
            "cannot silently grant filesystem access",
        )

    real = canonicalize(candidate)
    if not real:
        return False, f"{candidate!r} could not be resolved to a real path, so it is refused"

    # 🔴 bypass_immune (decision 7): a sensitive path is refused even when the allowlist names it.
    # An entry pointing at `~/.ssh` or `~/.aws` is far likelier to be a mistake — or an edit
    # nobody intended — than a genuine grant, and decision 7 explicitly reserves checks no
    # allowlist may silence. Checked BEFORE the allowlist so a match cannot short-circuit it.
    from personalclaw.security import is_sensitive_path

    if is_sensitive_path(real):
        return (
            False,
            f"{real!r} is a sensitive path (credentials/keys); it is refused even when "
            "allowlisted, because no automation should reach it unattended",
        )

    for entry in allowlist:
        if not isinstance(entry, str) or not entry:
            continue
        root = entry[:-1] if entry.endswith("*") else entry
        root = root.rstrip(os.sep) or os.sep
        if is_within(real, root):
            return True, ""

    return (
        False,
        f"{real!r} is outside this trigger's frozen paths allowlist "
        f"(resolved from {candidate!r})",
    )


def unsafe_entries(allowlist: object) -> list[tuple[str, str]]:
    """Allowlist entries that cannot bound anything. Returns `(entry, why)`.

    For the doctor. A fence a user believes in but which grants everything is the failure this whole
    program keeps finding, and these two entries are the ways to write one by accident:

    * a bare `*` (or `/*`), which resolves to the filesystem root — a "scope" covering everything;
    * a relative entry, which canonicalizes against the GATEWAY's cwd rather than the user's — so it
      means different things depending on how the gateway was started, which is indistinguishable
      from a broken fence when it eventually denies something it used to allow.
    """
    out: list[tuple[str, str]] = []
    if not isinstance(allowlist, (list, tuple, set, frozenset)):
        return out
    for entry in allowlist:
        if not isinstance(entry, str) or not entry:
            continue
        stripped = entry[:-1] if entry.endswith("*") else entry
        if stripped.rstrip(os.sep) in ("", os.sep):
            out.append((entry, "matches the whole filesystem, so it bounds nothing"))
        elif not Path(os.path.expanduser(stripped)).is_absolute():
            out.append(
                (
                    entry,
                    "is a relative path, so it resolves against the gateway's working directory "
                    "rather than a fixed location",
                )
            )
    return out

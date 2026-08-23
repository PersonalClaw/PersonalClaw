"""OS-level sandbox for agent child processes.

Hides sensitive credential paths (``~/.aws``, ``~/.gnupg``, etc.) from the
ACP agent subprocess tree and exposes ``~/.ssh/known_hosts`` while hiding
other SSH files (keys, config, etc.), using platform-native isolation:

- **Linux**: fork → ``unshare(CLONE_NEWUSER)`` → parent writes identity
  UID/GID map → ``unshare(CLONE_NEWNS)`` → bind-mount empty dirs → exec.
  The child retains the real UID so all toolchains work normally.
- **macOS**: ``sandbox-exec`` with a Seatbelt profile that denies reads

The parent PersonalClaw process is completely unaffected — isolation applies
only to the spawned child.  Falls back gracefully to no sandbox when the
OS mechanism is unavailable (logged as warning).

Config: ``"sandbox": "auto" | "off"`` in ``~/.personalclaw/config.json``.
``"auto"`` (default) uses namespace sandbox on Linux, seatbelt on macOS.
"""

import ctypes
import ctypes.util
import functools
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Sensitive directories to hide from the agent subprocess tree.
# "strict" mode hides all; "standard" mode only hides non-workflow dirs.
_STRICT_DIRS: list[str] = [
    ".aws",
    ".gnupg",
    ".gpg",
    ".config/gcloud",
    ".azure",
    ".docker",
    ".kube",
]

_STANDARD_DIRS: list[str] = [
    ".gnupg",
    ".gpg",
    ".config/gcloud",
    ".azure",
    ".docker",
]

# CC mode: hides all credential dirs including .aws, but selectively exposes
# .aws/config (some Anthropic-compatible providers reach AWS via
# credential_process). All other .aws files (credentials, sso cache, etc.)
# are filesystem-hidden via bind mount.
_CC_DIRS: list[str] = [
    ".aws",
    ".gnupg",
    ".gpg",
    ".config/gcloud",
    ".azure",
    ".docker",
    ".kube",
]

# CC mode: files to expose read-only inside otherwise-hidden dirs.
# After hiding the parent dir, these are recreated with original content.
_CC_EXPOSE_FILES: list[str] = [
    ".aws/config",
]

# CC mode: individual sensitive files that aren't inside the hidden dirs above.
# These require file-level (not directory-level) sandbox enforcement.
_CC_FILES: list[str] = [
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    ".personalclaw/.env",
]

# Sensitive env var prefixes to scrub from the child environment.
# Scrubbed in ALL modes (standard + strict) — credential_process reads
# from ~/.aws/config, not env vars, so scrubbing is always safe.
_SENSITIVE_ENV_PREFIXES: list[str] = [
    "AWS_SECRET",
    "AWS_SESSION",
    "SSH_AUTH_SOCK",
    "GNUPGHOME",
    "GIT_ASKPASS",
]

# Additional credential names scrubbed only in cc/strict modes (LLM-controlled
# agent subprocesses). Mirrors the file-level deny list for ~/.personalclaw/.env:
# config/loader.py seeds these into os.environ so trusted children (gateway,
# MCP servers, cron) inherit them, but a sandboxed Claude Code agent must not
# see them via env any more than via the bind-mounted file. Use exact-name
# matches by virtue of the prefix iteration's startswith check.
_AGENT_DENIED_ENV_KEYS: list[str] = [
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_USER_TOKEN",
    "PERSONALCLAW_OWNER_ID",
]


# ── Child environment allowlist ──

#: What an agent-influenced child process inherits, BY NAME — the allowlist shape
#: EXECUTION-ISOLATION `D1` specifies, swept to the hook/cron/bash spawn sites by PHF-4.
#:
#: 🔴 MEASURED, and the reason this is an allowlist rather than another name-pattern
#: denylist: a real gateway process carried **121** environment variables, essentially all
#: of them inherited from whatever the launching shell had grown to include (terminal,
#: toolbox, agent-CLI and cloud-SDK variables). A denylist can only refuse the credential
#: shapes it already knows about; the set a child legitimately NEEDS is small and stable.
#: `config/loader.py:4008` also seeds `.env` credentials into `os.environ` deliberately so
#: "trusted children" inherit them — which is exactly the inheritance a hook or cron script
#: must not get for free.
#:
#: Widening this set is a security decision. The seam for a script that needs one more
#: variable is `sandbox.env_passthrough` in `config.json` — an OPERATOR surface. It is
#: deliberately not reachable from a trigger payload or an action config: a payload-declared
#: name would be an exfiltration channel (`{"env_passthrough": ["ANTHROPIC_API_KEY"]}`).
CHILD_ENV_BASE_NAMES: frozenset[str] = frozenset(
    {
        # Resolution + shell basics. Without PATH nothing runs at all.
        "PATH",
        "SHELL",
        "PWD",
        "TERM",
        # The ceiling shim runs ``python -m personalclaw._spawn_exec_shim`` (see
        # ``spawn_shim_argv``): dropping PYTHONPATH breaks that import in any layout where
        # the package is not on the interpreter's default path, which is a total spawn
        # OUTAGE rather than a tightening. The gateway's own PYTHONPATH is trusted — a
        # trigger payload can never set it (``bash_provider.PROTECTED_ENV_NAMES``).
        "PYTHONPATH",
        # Locale + time. A script that prints non-ASCII or formats a date reads these, and
        # the failure without them is a mojibake/UnicodeEncodeError far from the cause.
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_COLLATE",
        "LC_CTYPE",
        "LC_MESSAGES",
        "LC_MONETARY",
        "LC_NUMERIC",
        "LC_TIME",
        "TZ",
        # Home-equivalents: where a child reads its config and writes its scratch files.
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "USER",
        "LOGNAME",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
        "XDG_STATE_HOME",
        # How the network works HERE. Absent on the host this was measured on, but a
        # corporate install has them, and a script that curls or pip-installs without them
        # fails SILENTLY (a hang, then a timeout) — the worst diagnostic shape there is.
        # None are credential-shaped by the floor below, and all are inherited today, so
        # keeping them widens nothing.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        # The three PersonalClaw vars: which home, which workspace, which instance. A child
        # that loses these addresses a DIFFERENT install (the default home, port 10000).
        "PERSONALCLAW_HOME",
        "PERSONALCLAW_WORKSPACE",
        "PERSONALCLAW_PORT",
    }
)

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def env_name_is_sensitive(name: str) -> bool:
    """Whether *name* is credential-shaped by the floor (``_SENSITIVE_ENV_PREFIXES``).

    The floor applies to EVERYTHING that lands in a child environment — the inherited
    base, an operator's declared passthrough, and the values a call site injects. A floor a
    declaration could lower would not be a floor.
    """
    return any(name.startswith(prefix) for prefix in _SENSITIVE_ENV_PREFIXES)


def _declared_env_passthrough(site: str) -> set[str]:
    """The operator-declared extra variable names, floor-filtered and name-validated.

    Fail-open to "nothing declared" if the config cannot be read, matching
    ``ResourceCeilings.from_config``: a broken config must never block a spawn. Failing
    open here is safe in the security direction — it yields the NARROWER environment.
    """
    try:
        from personalclaw.config.loader import AppConfig

        declared = list(AppConfig.load().sandbox.env_passthrough)
    except Exception:
        logger.debug("sandbox.env_passthrough unreadable; passing only the base", exc_info=True)
        return set()

    out: set[str] = set()
    for raw in declared:
        name = str(raw).strip()
        if not _ENV_NAME_RE.match(name):
            logger.warning(
                "sandbox.env_passthrough entry %r is not a valid environment variable "
                "name; ignoring it (%s children)",
                raw,
                site,
            )
            continue
        if env_name_is_sensitive(name):
            logger.warning(
                "sandbox.env_passthrough names %s, which the credential floor refuses; "
                "it is NOT passed to %s children",
                name,
                site,
            )
            continue
        out.add(name)
    return out


def build_child_env(
    *,
    site: str,
    extra: "dict[str, str] | None" = None,
    source: "dict[str, str] | None" = None,
) -> dict[str, str]:
    """The environment an agent-influenced child process runs with.

    Built from :data:`CHILD_ENV_BASE_NAMES` plus the operator's declared
    ``sandbox.env_passthrough`` names, never from a copy of the parent environment, then
    layered with *extra* — the values the CALL SITE computes (a hook's event/context, a
    trigger's ``$variables``) rather than inherits.

    *site* names the spawn site in the logs. Withheld variable names are logged (names
    only, never values) so a script that breaks for want of one is diagnosable instead of a
    silent mystery — a dropped variable is otherwise indistinguishable from a bug in the
    script.
    """
    src = dict(os.environ) if source is None else dict(source)
    names = CHILD_ENV_BASE_NAMES | _declared_env_passthrough(site)
    # The floor is enforced HERE, at the one point where a name becomes a variable in a
    # child environment — not only where declarations are parsed. The parse-time check
    # exists to WARN the operator which entry was ignored and why; this one is what makes
    # the floor hold no matter how a name reached `names`.
    env = {
        name: src[name] for name in sorted(names) if name in src and not env_name_is_sensitive(name)
    }

    withheld = sorted(name for name in src if name not in env)
    if withheld:
        logger.debug(
            "%s child env: kept %d of %d variables. Withheld: %s. A script that needs one "
            "of these must be granted it BY NAME in sandbox.env_passthrough (config.json).",
            site,
            len(env),
            len(src),
            ", ".join(withheld),
        )

    for name, value in (extra or {}).items():
        if env_name_is_sensitive(name):
            logger.warning(
                "%s: refusing to set credential-shaped variable %s in the child " "environment",
                site,
                name,
            )
            continue
        env[name] = str(value)
    return env


# ── Availability probes ──


def _probe_unshare() -> bool:
    """Return True if user + mount namespaces work (Linux).

    Mirrors the SEQUENCE the real launcher uses: ``unshare(NEWUSER)`` first, then
    a SEPARATE ``unshare(NEWNS)``. This matters — some hardened kernels (Ubuntu
    23.10+ and the GitHub-hosted runners with
    ``kernel.apparmor_restrict_unprivileged_userns=1``) permit the *atomic*
    ``unshare(NEWUSER|NEWNS)`` but DENY a standalone ``unshare(NEWNS)`` once the
    process is already in an unprivileged user namespace. Probing with the combined
    flag gave a false positive on those hosts, so ``detect_backend()`` selected the
    namespace backend and the launcher then died at runtime with
    ``unshare(NEWNS) failed: errno 1`` — breaking every sandboxed script (hooks,
    scheduled scripts) in restricted containers/CI. The two-step probe fails honestly
    there, so the backend falls back to ``sandbox-exec``/``none`` instead. On a normal
    unprivileged Linux host the sequential calls both succeed (exactly what the
    launcher does in production), so this does not regress real sandboxing.
    """
    if sys.platform != "linux":
        return False
    try:
        _clone_newuser = 0x10000000
        _clone_newns = 0x00020000
        _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        _libc.unshare.argtypes = [ctypes.c_int]
        _libc.unshare.restype = ctypes.c_int
        pid = os.fork()
        if pid == 0:
            # Separate calls, matching the launcher — NOT a combined flag.
            if _libc.unshare(_clone_newuser) != 0:
                os._exit(1)
            if _libc.unshare(_clone_newns) != 0:
                os._exit(1)
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    except Exception:
        return False


def _probe_sandbox_exec() -> bool:
    """Return True if macOS ``sandbox-exec`` actually works.

    Uses a file-based profile and targets /usr/bin/true as
    fallback) to match the real sandbox_exec_argv() invocation.  macOS ≥ 26
    refuses sandbox_apply() for third-party binaries, so probing with just
    ``true`` gives false positives.
    """
    if sys.platform != "darwin":
        return False
    # macOS 26+ refuses sandbox_apply() for third-party callers entirely.
    try:
        mac_ver = platform.mac_ver()[0]
        if mac_ver:
            major = int(mac_ver.split(".")[0])
            if major >= 26:
                logger.info(
                    "sandbox-exec unavailable: macOS %s denies sandbox_apply for third-party binaries",  # noqa: E501
                    mac_ver,
                )
                return False
    except (ValueError, IndexError):
        pass
    sb = shutil.which("sandbox-exec")
    if sb is None:
        return False
    # Probe with file-based profile targeting a representative binary
    target = "/usr/bin/true"
    target_arg: list[str] = []
    fd, profile_path = tempfile.mkstemp(suffix=".sb", prefix="personalclaw_probe_")
    try:
        os.write(fd, b"(version 1)(allow default)")
        os.close(fd)
        r = subprocess.run(
            [sb, "-f", profile_path, target, *target_arg],
            capture_output=True,
            timeout=5,
        )
        if r.returncode != 0:
            logger.warning(
                "sandbox-exec probe failed (exit %d): %s",
                r.returncode,
                r.stderr.decode(errors="replace").strip(),
            )
        return r.returncode == 0
    except Exception as exc:
        logger.debug("sandbox-exec probe failed: %s", exc)
        return False
    finally:
        try:
            os.unlink(profile_path)
        except OSError:
            pass


# ── SSH version probe ──


@functools.lru_cache(maxsize=1)
def _ssh_supports_accept_new() -> bool:
    """Return True if local ssh supports ``StrictHostKeyChecking=accept-new``.

    OpenSSH 7.5+ added the ``accept-new`` value (2017). Older releases
    silently treat it as ``yes`` and refuse new hosts. We probe ``ssh -V``
    once (cached) and parse the major.minor version from stderr.
    """
    try:
        result = subprocess.run(["ssh", "-V"], capture_output=True, timeout=2, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    text = (result.stderr or b"").decode(errors="replace")
    m = re.search(r"OpenSSH_(\d+)\.(\d+)", text)
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    return (major, minor) >= (7, 5)


# ── Backend: Linux namespace sandbox ──


def _build_launcher_script(sandbox_level: str = "strict") -> str:
    """Build a Python launcher script for the Linux namespace sandbox.

    The launcher is executed as a subprocess.  It:

    1. Forks a child.
    2. Child calls ``unshare(CLONE_NEWUSER)`` and signals the parent.
    3. Parent writes identity UID/GID map (``uid uid 1``) to
       ``/proc/<child>/{setgroups,uid_map,gid_map}`` and signals back.
    4. Child calls ``unshare(CLONE_NEWNS)``, sets mount propagation private,
       bind-mounts empty dirs over credential paths, scrubs env vars,
       and ``exec``s the real command.

    The child retains the real UID/GID — no UID 0, no UID 65534.
    """
    home = str(Path.home())
    uid = os.getuid()
    gid = os.getgid()
    if sandbox_level == "standard":
        dirs = _STANDARD_DIRS
    elif sandbox_level == "cc":
        dirs = _CC_DIRS
    else:
        dirs = _STRICT_DIRS
    files = _CC_FILES if sandbox_level in ("cc", "strict") else []
    expose_files = _CC_EXPOSE_FILES if sandbox_level == "cc" else []
    env_prefixes = list(_SENSITIVE_ENV_PREFIXES)
    if sandbox_level in ("cc", "strict"):
        # Block agent subprocesses from reading credentials via os.environ
        # (the file-level bind-mount of ~/.personalclaw/.env hides them on disk;
        # config/loader.py seeds them into os.environ for trusted children
        # only — sandboxed agents must not see them either way).
        env_prefixes = env_prefixes + list(_AGENT_DENIED_ENV_KEYS)
    hide_ssh = sandbox_level == "strict"
    dirs_json = json.dumps([os.path.join(home, d) for d in dirs])
    files_json = json.dumps([os.path.join(home, f) for f in files])
    expose_json = json.dumps([(os.path.join(home, f), f.split("/")[-1]) for f in expose_files])
    env_prefixes_json = json.dumps(env_prefixes)
    ssh_dir = json.dumps(os.path.join(home, ".ssh"))
    ssh_known_hosts = json.dumps(os.path.join(home, ".ssh", "known_hosts"))
    strict_host_key_opt = (
        " -o StrictHostKeyChecking=accept-new" if _ssh_supports_accept_new() else ""
    )

    return f'''#!/usr/bin/env python3
"""Namespace sandbox launcher — spawned by PersonalClaw."""
import ctypes
import ctypes.util
import os
import sys
import tempfile

_CLONE_NEWUSER = 0x10000000
_CLONE_NEWNS   = 0x00020000
_MS_BIND       = 4096
_MS_REC        = 16384
_MS_PRIVATE    = 1 << 18

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_libc.mount.argtypes = [
    ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
    ctypes.c_ulong, ctypes.c_void_p,
]
_libc.mount.restype = ctypes.c_int
_libc.unshare.argtypes = [ctypes.c_int]
_libc.unshare.restype = ctypes.c_int

REAL_UID = {uid}
REAL_GID = {gid}
SENSITIVE_DIRS = {dirs_json}
SENSITIVE_FILES = {files_json}
EXPOSE_FILES = {expose_json}
ENV_PREFIXES = {env_prefixes_json}
SSH_DIR = {ssh_dir}
SSH_KNOWN_HOSTS = {ssh_known_hosts}
HIDE_SSH = {hide_ssh}

def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit("sandbox_launcher: no command given")

    # Two pipes for parent↔child synchronization
    c2p_r, c2p_w = os.pipe()  # child signals "unshare done"
    p2c_r, p2c_w = os.pipe()  # parent signals "maps written"

    pid = os.fork()

    if pid > 0:
        # ── Parent: write identity UID/GID map ──
        os.close(c2p_w)
        os.close(p2c_r)
        os.read(c2p_r, 1)  # wait for child to unshare(NEWUSER)
        os.close(c2p_r)
        with open(f"/proc/{{pid}}/setgroups", "w") as f:
            f.write("deny")
        with open(f"/proc/{{pid}}/uid_map", "w") as f:
            f.write(f"{{REAL_UID}} {{REAL_UID}} 1\\n")
        with open(f"/proc/{{pid}}/gid_map", "w") as f:
            f.write(f"{{REAL_GID}} {{REAL_GID}} 1\\n")
        os.write(p2c_w, b"x")  # signal child to proceed
        os.close(p2c_w)
        _, status = os.waitpid(pid, 0)
        code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
        sys.exit(code)
    else:
        # ── Child: unshare, wait for maps, mount, exec ──
        os.close(c2p_r)
        os.close(p2c_w)

        # Step 1: enter user namespace
        if _libc.unshare(_CLONE_NEWUSER) != 0:
            sys.exit(f"sandbox: unshare(NEWUSER) failed: errno {{ctypes.get_errno()}}")
        os.write(c2p_w, b"x")  # tell parent
        os.close(c2p_w)
        os.read(p2c_r, 1)  # wait for maps
        os.close(p2c_r)

        # Step 2: enter mount namespace (now we have a mapped UID)
        if _libc.unshare(_CLONE_NEWNS) != 0:
            sys.exit(f"sandbox: unshare(NEWNS) failed: errno {{ctypes.get_errno()}}")

        # Private mount propagation
        _libc.mount(None, b"/", None, _MS_REC | _MS_PRIVATE, None)

        # Pick a tmpfs-backed source dir for bind-mount empty files/dirs. Same-fs
        # binds (e.g. /tmp on ext4 over ~/.personalclaw/.env on ext4) can corrupt the
        # target's host directory entry via a kernel propagation race when the
        # private NS is torn down — leaving the host file pointing at the empty
        # source inode permanently. Cross-fs binds use distinct inode spaces and
        # cannot leak that way. Fallback chain: /run/user/$UID → /dev/shm.
        # Verify each candidate is on a different filesystem from HOME by
        # comparing st_dev — same-fs candidates provide no isolation benefit.
        _tmpfs_src = None
        try:
            _home_dev = os.stat(os.path.expanduser("~")).st_dev
        except OSError:
            _home_dev = None
        for _candidate in (f"/run/user/{{REAL_UID}}", "/dev/shm"):
            try:
                if _home_dev is not None and os.stat(_candidate).st_dev == _home_dev:
                    continue  # same fs as HOME — no isolation, race still possible
                _probe = tempfile.mkdtemp(dir=_candidate, prefix="personalclaw_sb_")
                os.rmdir(_probe)
                _tmpfs_src = _candidate
                break
            except (OSError, ValueError):
                continue
        # _tmpfs_src=None falls through to system default tempdir (typically /tmp).
        # In that case we accept the kernel-race risk because no tmpfs is
        # available — better to function (with the original regression risk)
        # than to refuse to start.

        # Pre-read files that must survive dir hiding
        expose_data = {{}}
        for src_path, filename in EXPOSE_FILES:
            if os.path.isfile(src_path):
                with open(src_path, "rb") as fh:
                    expose_data[src_path] = fh.read()

        # Bind-mount empty dirs over credential paths (per-dir tmpdir to
        # prevent content leaking across mounts via shared backing dir).
        for d in SENSITIVE_DIRS:
            target = d.encode()
            if os.path.isdir(target):
                per_dir_empty = tempfile.mkdtemp(dir=_tmpfs_src).encode()
                _libc.mount(per_dir_empty, target, None, _MS_BIND, None)

        # Restore selectively exposed files into the now-empty mounts
        for src_path, filename in EXPOSE_FILES:
            if src_path in expose_data:
                parent = os.path.dirname(src_path)
                dest = os.path.join(parent, filename)
                with open(dest, "wb") as fh:
                    fh.write(expose_data[src_path])
                os.chmod(dest, 0o444)

        # Bind-mount empty files over individual sensitive files. Source the
        # empty tempfile from a tmpfs (cross-fs) when available so the bind
        # cannot corrupt the target's host directory entry on namespace exit.
        for f in SENSITIVE_FILES:
            target = f.encode()
            if os.path.isfile(target):
                fd, empty_path = tempfile.mkstemp(dir=_tmpfs_src)
                os.close(fd)
                _libc.mount(empty_path.encode(), target, None, _MS_BIND, None)

        # .ssh: hide keys but expose known_hosts content (strict only)
        if HIDE_SSH and os.path.isdir(SSH_DIR):
            kh_data = b""
            if os.path.isfile(SSH_KNOWN_HOSTS):
                with open(SSH_KNOWN_HOSTS, "rb") as fh:
                    kh_data = fh.read()
            # Cross-fs source for the same kernel-race reason as SENSITIVE_DIRS
            # (line 371) and SENSITIVE_FILES (line 389).
            ssh_tmp = tempfile.mkdtemp(dir=_tmpfs_src).encode()
            _libc.mount(ssh_tmp, SSH_DIR.encode(), None, _MS_BIND, None)
            if kh_data:
                with open(os.path.join(SSH_DIR, "known_hosts"), "wb") as fh:
                    fh.write(kh_data)

        # Scrub sensitive env vars
        for key in list(os.environ):
            for prefix in ENV_PREFIXES:
                if key.startswith(prefix):
                    del os.environ[key]
                    break

        # Fix /etc/ssh/ssh_config.d/ ownership issue: root-owned files
        # appear as nobody:nobody inside the user namespace because UID 0
        # is unmapped. SSH refuses to load them. Bypass with -F /dev/null.
        if not os.environ.get("GIT_SSH_COMMAND"):
            os.environ["GIT_SSH_COMMAND"] = (
                "ssh -F /dev/null -o IdentityFile=~/.ssh/id_rsa"
                " -o IdentityFile=~/.ssh/id_ecdsa"
                " -o IdentityFile=~/.ssh/id_ed25519"
                " -o UserKnownHostsFile=~/.ssh/known_hosts"
                "{strict_host_key_opt}"
            )

        os.execvp(argv[0], argv)

if __name__ == "__main__":
    main()
'''


def _resolve_real_agent_bin(name: str) -> str:
    """Resolve *name* to an absolute path, or return it unchanged.

    The launcher script bind-mounts empty dirs over credential paths
    (``~/.aws``, ``~/.ssh``, etc.) before exec. If the agent binary lives
    under a hidden directory (e.g. an npm/pip user-site install in
    ``~/.local/bin``), a bare-name ``execvp`` would walk ``$PATH`` *after*
    the mounts hid it and fail. Resolving here, before namespace entry,
    pins the inode so the child can exec it regardless of what the
    in-namespace ``$PATH`` reveals.

    Already-absolute paths and unresolvable names pass through unchanged.
    """
    if os.path.isabs(name):
        return name
    resolved = shutil.which(name)
    return resolved if resolved else name


def namespace_argv(argv: list[str], sandbox_level: str = "strict") -> list[str]:
    """Wrap *argv* via the Python namespace launcher.

    The launcher forks, the parent writes identity UID/GID maps, and the
    child bind-mounts empty dirs over credential paths before exec.
    The child retains the real UID/GID.
    """
    real_argv = list(argv)
    if real_argv:
        real_argv[0] = _resolve_real_agent_bin(real_argv[0])

    script = _build_launcher_script(sandbox_level)
    fd, path = tempfile.mkstemp(suffix=".py", prefix="personalclaw_sandbox_")
    os.write(fd, script.encode())
    os.close(fd)
    os.chmod(path, 0o700)

    return [sys.executable, path, *real_argv]


# ── Backend: macOS sandbox-exec ──

_SEATBELT_PROFILE = """\
(version 1)
(allow default)
{deny_rules}
"""


def _build_seatbelt_profile(sandbox_level: str = "strict") -> str:
    """Build a Seatbelt .sb profile denying reads of sensitive dirs."""
    home = str(Path.home())
    if sandbox_level == "standard":
        dirs = _STANDARD_DIRS
    elif sandbox_level == "cc":
        # On macOS, don't hide .aws — credential_process and SSO token
        # caches live under .aws/ and Seatbelt can't do partial exposure
        # as cleanly as Linux bind mounts. Deny patterns still block LLM
        # tool reads of credential files.
        dirs = [d for d in _CC_DIRS if d != ".aws"]
    else:
        dirs = _STRICT_DIRS
    files = _CC_FILES if sandbox_level in ("cc", "strict") else []
    expose_files = _CC_EXPOSE_FILES if sandbox_level == "cc" else []
    expose_abs = {os.path.join(home, f) for f in expose_files}
    rules: list[str] = []
    for d in dirs:
        target = os.path.join(home, d)
        escaped = target.replace('"', '\\"')
        # Check if any exposed files live under this dir
        exposed_in_dir = [f for f in expose_abs if f.startswith(target + "/")]
        if exposed_in_dir:
            exceptions = " ".join(
                f'(require-not (literal "{f.replace(chr(34), chr(92)+chr(34))}"))'
                for f in exposed_in_dir
            )
            rules.append(f'(deny file-read* (require-all (subpath "{escaped}") {exceptions}))')
        else:
            rules.append(f'(deny file-read* (subpath "{escaped}"))')
    for f in files:
        target = os.path.join(home, f)
        escaped = target.replace('"', '\\"')
        rules.append(f'(deny file-read* (literal "{escaped}"))')

    # .ssh: deny all access except reading known_hosts (strict only)
    if sandbox_level == "strict":
        ssh_dir = os.path.join(home, ".ssh")
        ssh_escaped = ssh_dir.replace('"', '\\"')
        ssh_kh = os.path.join(ssh_dir, "known_hosts")
        ssh_kh_escaped = ssh_kh.replace('"', '\\"')
        rules.append(
            f'(deny file-read* (require-all (subpath "{ssh_escaped}")'
            f' (require-not (literal "{ssh_kh_escaped}"))))'
        )
        rules.append(f'(deny file-write* (subpath "{ssh_escaped}"))')

    return _SEATBELT_PROFILE.format(deny_rules="\n".join(rules))


def sandbox_exec_argv(
    argv: list[str],
    sandbox_level: str = "strict",
) -> tuple[list[str], str | None]:
    """Wrap *argv* with ``sandbox-exec -f <profile>``.

    Also scrubs sensitive env vars via ``env -u`` since Seatbelt only
    handles file-level deny rules, not environment variables.

    Returns (new_argv, tmp_profile_path).  Caller should delete the
    profile file after the child exits.
    """
    profile = _build_seatbelt_profile(sandbox_level)
    fd, path = tempfile.mkstemp(suffix=".sb", prefix="personalclaw_sandbox_")
    os.write(fd, profile.encode())
    os.close(fd)
    # Build env -u flags for sensitive vars present in current env. cc/strict
    # additionally scrub agent-denied credential keys (Slack tokens, owner id)
    # since loader.py seeds them into os.environ for trusted children only.
    prefixes = list(_SENSITIVE_ENV_PREFIXES)
    if sandbox_level in ("cc", "strict"):
        prefixes.extend(_AGENT_DENIED_ENV_KEYS)
    unset_args: list[str] = []
    for key in os.environ:
        for prefix in prefixes:
            if key.startswith(prefix):
                unset_args.extend(["-u", key])
                break
    return ["env", *unset_args, "sandbox-exec", "-f", path, *argv], path


# ── Public API ──

_backend: str | None = None  # "namespace", "sandbox-exec", "none"
_backend_config_mode: str | None = None  # config mode when backend was cached


def detect_backend(config_mode: str = "auto") -> str:
    """Detect the best available sandbox backend.

    Cached after first call; cache is invalidated if *config_mode* changes
    (e.g. user toggles agent.sandbox between "auto" and "off").
    """
    global _backend, _backend_config_mode
    if _backend is not None and _backend_config_mode == config_mode:
        return _backend
    # Invalidate on config change
    if _backend_config_mode != config_mode:
        _backend = None
        _backend_config_mode = config_mode
    if config_mode == "off":
        _backend = "none"
    elif _probe_unshare():
        _backend = "namespace"
    elif _probe_sandbox_exec():
        _backend = "sandbox-exec"
    else:
        _backend = "none"
    logger.info("Sandbox backend: %s (config_mode=%s)", _backend, config_mode)
    return _backend


def reset_backend() -> None:
    """Reset cached backend (for testing or config change)."""
    global _backend, _backend_config_mode
    _backend = None
    _backend_config_mode = None


def wrap_argv(argv: list[str], mode: str = "auto") -> tuple[list[str], str | None]:
    """Wrap a command argv with OS-level sandbox if available.

    Args:
        argv: Original command + args.
        mode: ``"auto"``/``"standard"`` (expose .aws/.ssh/.kube),
              ``"cc"`` (hide .aws but expose .aws/config for credential_process),
              ``"strict"`` (hide everything), ``"off"`` (no sandbox).

    Returns:
        (wrapped_argv, cleanup_path_or_None).
        *cleanup_path* is a temp file to delete after the child exits
        (macOS seatbelt profile or Linux launcher script).
        ``None`` when no cleanup is needed.
    """
    if mode == "off":
        return argv, None

    # "auto"/"standard" allows git-over-SSH, AWS CLI, kubectl.
    # "cc" hides .aws (exposes only .aws/config for credential_process flows).
    # "strict" hides everything.
    if mode == "strict":
        sandbox_level = "strict"
    elif mode == "cc":
        sandbox_level = "cc"
    else:
        sandbox_level = "standard"

    backend = detect_backend(config_mode=mode)

    if backend == "namespace":
        wrapped = namespace_argv(argv, sandbox_level)
        # The launcher script is argv[1] — caller should clean it up
        return wrapped, wrapped[1]
    if backend == "sandbox-exec":
        return sandbox_exec_argv(argv, sandbox_level)

    if backend == "none":
        if not getattr(wrap_argv, "_warned", False):
            logger.warning("No OS-level sandbox available — app-level checks only")
            wrap_argv._warned = True  # type: ignore[attr-defined]
    return argv, None


# ── Resource ceilings: post-exec delivery via the stdlib shim (PHF-1) ──
#
# Ceilings are delivered by prepending ``python -m personalclaw._spawn_exec_shim
# <policy> -- <argv>`` to a child's command. The shim (a pure-stdlib leaf, no core
# imports) runs setrlimit in the already-exec'd, single-threaded child and then
# ``os.execv``s the real target. This is deliberately NOT ``subprocess``'s
# ``preexec_fn``: that forces CPython off ``posix_spawn``/``vfork`` onto a full
# ``fork()`` of the many-threaded gateway and runs Python before ``exec``, so a child
# can wedge on a lock another thread held at fork time while still holding every
# inherited fd (the gateway lock, the listening socket) and blocking the event loop in
# an un-awaitable ``os.read(errpipe)``. Moving delivery past ``exec`` removes that whole
# class of hazard; coverage is identical because rlimits inherit through ``exec``.

_SHIM_MODULE = "personalclaw._spawn_exec_shim"

# The four spawn profiles. A profile names WHICH ceilings apply and whether the child
# carries an OOM-killer bias, per PLATFORM-HARDENING-FLOORS §1:
#   * ``tool``         — every ordinary agent-influenced spawn: the full NOFILE/NPROC/RSS
#                        ceiling + oom_score_adj=1000 (prefer killing agent work over the
#                        gateway).
#   * ``session_host`` — ACP session hosts. A host multiplexes many MCP stdio pipe pairs;
#                        a low NOFILE cap causes EMFILE crashes. So NOFILE is RAISED to the
#                        inherited hard limit, and there is NO OOM bias (a trusted host must
#                        not be the preferred kill target). NPROC/RSS still apply.
#   * ``build``        — frontend/npm builds: thousands of descriptors, so NOFILE follows
#                        the same raise-to-hard as session_host, but the OOM bias is KEPT.
#   * ``none``         — the user's own interactive terminal: no limits, no bias, no shim
#                        (the shim would have nothing to deliver and costs an interpreter
#                        startup per terminal open).
PROFILE_TOOL = "tool"
PROFILE_SESSION_HOST = "session_host"
PROFILE_BUILD = "build"
PROFILE_NONE = "none"

_PROFILES = frozenset({PROFILE_TOOL, PROFILE_SESSION_HOST, PROFILE_BUILD, PROFILE_NONE})


# ── Tier 2: cgroup v2 scopes (Linux) ──
#
# The RLIMIT shim is the enforcement FLOOR, and NOFILE is the only ceiling it delivers
# reliably. Neither of the other two is a per-process-TREE bound as an rlimit: RLIMIT_NPROC
# counts every process of the real uid (so it either denies every fork in the tree or bounds
# the tree not at all), and RLIMIT_AS bounds one address space rather than a tree's total —
# and on macOS it cannot be installed at all (measured; see ``_warn_unenforced_ceilings``).
# A cgroup v2 scope is the only mechanism that says "this spawn AND all of its descendants,
# together". It is opt-in (``sandbox.cgroup_scopes``) because it requires a systemd user
# session, and it wraps OUTSIDE the shim so the scope contains the shim and the exec'd
# target alike — a second layer above the NOFILE floor, never a replacement for it.

#: The unified (v2) hierarchy's root controller list. Its readability is the cheapest
#: positive evidence that this host is on cgroup v2 rather than v1/hybrid. Module-level so
#: tests can point it at a ``tmp_path`` file instead of monkeypatching ``open``.
_CGROUP2_CONTROLLERS = "/sys/fs/cgroup/cgroup.controllers"


def _cgroup_scopes_available() -> tuple[bool, str]:
    """Uncached body of :func:`probe_cgroup_scopes`. Returns ``(available, detail)``.

    Split out from the cached wrapper so a test can (a) simulate each platform combination
    through real filesystem/PATH/env inputs and (b) count how many times the underlying
    check actually runs. Never raises — every failure mode is a ``False`` with a reason.
    """
    try:
        if sys.platform != "linux":
            return False, f"not Linux ({sys.platform}) — cgroup scopes are a Linux-only tier"
        try:
            with open(_CGROUP2_CONTROLLERS, encoding="ascii") as fh:
                controllers = fh.read().split()
        except OSError as exc:
            return False, (
                f"no unified cgroup v2 hierarchy: {_CGROUP2_CONTROLLERS} unreadable "
                f"({exc.__class__.__name__}) — a v1/hybrid host cannot host a v2 scope"
            )
        run = shutil.which("systemd-run")
        if run is None:
            return False, "systemd-run is not on PATH — no systemd to create a scope"
        # A *live user bus* is the part that actually fails on a container or an SSH login
        # with no user manager. We test what is observable for free — the bus address env
        # var, else the well-known $XDG_RUNTIME_DIR/bus socket — rather than shelling out
        # to `systemctl --user is-system-running`: that costs a process spawn on every
        # gateway start and can BLOCK on a wedged user manager, which a probe on the spawn
        # path must never do. The trade-off is deliberate and one-directional: the env/
        # socket check can say "available" where `systemd-run` would still fail, and in
        # that case the spawn fails loudly with systemd's own diagnostic — which is the
        # correct outcome for an opt-in enforcement tier (far better than silently
        # dropping the ceiling the user asked for).
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            xdg = os.environ.get("XDG_RUNTIME_DIR") or ""
            sock = os.path.join(xdg, "bus") if xdg else ""
            if not sock or not os.path.exists(sock):
                return False, (
                    "no live systemd user bus (DBUS_SESSION_BUS_ADDRESS unset and "
                    "$XDG_RUNTIME_DIR/bus absent) — 'systemd-run --user' cannot connect"
                )
        return True, (
            "cgroup v2 + systemd user session available "
            f"({run}; root controllers: {' '.join(controllers) or 'none'})"
        )
    except Exception as exc:  # pragma: no cover - defensive; the branches above are total
        return False, f"probe error ({exc.__class__.__name__}) — treated as unavailable"


@functools.lru_cache(maxsize=1)
def probe_cgroup_scopes() -> tuple[bool, str]:
    """Return ``(available, detail)`` for the cgroup-scope tier. NEVER raises.

    "Available" means BOTH halves of what a ``systemd-run --user --scope`` needs:

    1. a **unified cgroup v2 hierarchy** — ``/sys/fs/cgroup/cgroup.controllers`` is
       readable (it exists only on v2; a v1/hybrid mount has no such file); and
    2. a **usable systemd user session** — ``systemd-run`` resolvable on ``PATH`` *and* an
       observable user bus (``DBUS_SESSION_BUS_ADDRESS``, else ``$XDG_RUNTIME_DIR/bus``).

    Cached at ``maxsize=1`` (the module's existing probe-caching idiom, cf.
    ``_ssh_supports_accept_new``) because it sits on the spawn path and the answer cannot
    change within a process' life; call ``probe_cgroup_scopes.cache_clear()`` in tests.

    Caveat recorded honestly rather than probed: a readable root controller list does not
    prove the ``pids``/``memory`` controllers are *delegated* to the user slice, so
    ``TasksMax``/``MemoryMax`` can still be inert on a host with an unusual delegation
    policy. The root controller names are included in *detail* so the doctor line shows
    what this host actually has.
    """
    available, detail = _cgroup_scopes_available()
    logger.debug("cgroup scope probe: available=%s (%s)", available, detail)
    return available, detail


class ResourceCeilings:
    """Translates the numeric ``sandbox.*`` config into a per-profile shim policy.

    A ceiling is a set of ``RLIMIT_*`` bounds plus an optional OOM bias. The same
    ``ResourceCeilings`` instance produces a different policy per profile (see the class
    docstring in this module): ``tool`` applies every configured cap with an OOM bias;
    ``session_host`` raises NOFILE to the inherited hard limit (the EMFILE fix) with no
    bias; ``build`` raises NOFILE and keeps the bias; ``none`` produces an empty policy so
    ``spawn_shim_argv`` returns the argv unwrapped.
    """

    #: The OOM-killer adjustment for biased profiles. +1000 is the max — it makes an
    #: agent child the first thing the kernel kills under memory pressure, protecting the
    #: gateway.
    OOM_BIAS = 1000

    def __init__(
        self,
        nofile: int = 4096,
        max_pids: int = 0,
        max_rss_mb: int = 0,
        cgroup_scopes: bool = False,
    ) -> None:
        # Defaults mirror SandboxConfig: NOFILE is the enforced floor; max_pids defaults to
        # 0 (OFF) because RLIMIT_NPROC is a PER-USER cap counting every process the user
        # already runs — an absolute default would break a busy host with 'cannot fork'.
        # cgroup_scopes defaults OFF: it is the opt-in second tier and needs a systemd user
        # session, so it must never be a silent requirement of a default spawn.
        self.nofile = int(nofile)
        self.max_pids = int(max_pids)
        self.max_rss_mb = int(max_rss_mb)
        self.cgroup_scopes = bool(cgroup_scopes)

    @classmethod
    def from_config(cls) -> "ResourceCeilings":
        """Build ceilings from the live ``sandbox.*`` config (0 = a disabled limit).

        Fail-open to the class defaults if the config cannot be loaded: a ceiling is a
        best-effort bound, and a broken/absent config must never BLOCK a spawn (that would
        turn a config typo into a total spawn outage). The defaults are already the safe
        floor (NOFILE only, no per-user NPROC cap)."""
        try:
            from personalclaw.config.loader import AppConfig

            sb = AppConfig.load().sandbox
            return cls(
                nofile=sb.nofile,
                max_pids=sb.max_pids,
                max_rss_mb=sb.max_rss_mb,
                cgroup_scopes=sb.cgroup_scopes,
            )
        except Exception:
            logger.debug("ResourceCeilings.from_config fell back to defaults", exc_info=True)
            return cls()

    def policy(self, profile: str) -> dict:
        """The shim policy dict for *profile*: ``{"limits": {...}, "oom_score_adj": int|None}``.

        An empty ``{}`` means "no ceiling" (the ``none`` profile, or an unknown profile
        treated as ``none`` fail-open — a spawn must never be BLOCKED by a ceiling typo).
        """
        if profile == PROFILE_NONE or profile not in _PROFILES:
            return {}
        limits: dict[str, list] = {}
        # NOFILE: tool clamps to the configured soft cap; session_host/build raise it to
        # the inherited hard limit (the "hard" sentinel the shim resolves in-child).
        if profile in (PROFILE_SESSION_HOST, PROFILE_BUILD):
            limits["RLIMIT_NOFILE"] = ["hard", "hard"]
        elif self.nofile > 0:
            limits["RLIMIT_NOFILE"] = [self.nofile, "hard"]
        # NPROC (fork-bomb bound) and RSS apply to every non-none profile when configured.
        if self.max_pids > 0:
            limits["RLIMIT_NPROC"] = [self.max_pids, self.max_pids]
        if self.max_rss_mb > 0:
            as_bytes = self.max_rss_mb * 1024 * 1024
            limits["RLIMIT_AS"] = [as_bytes, as_bytes]
        oom = self.OOM_BIAS if profile in (PROFILE_TOOL, PROFILE_BUILD) else None
        if not limits and oom is None:
            return {}
        return {"limits": limits, "oom_score_adj": oom}


#: Process-wide latch for the unenforced-ceiling warning. A tool-heavy turn spawns dozens of
#: children; one warning per spawn would bury the message it exists to deliver.
_UNENFORCED_CEILINGS_WARNED = False


def _warn_unenforced_ceilings(ceilings: "ResourceCeilings") -> None:
    """Warn EXACTLY ONCE per process when configured pids/RSS ceilings are not really enforced.

    Silent on two paths, both deliberate: when neither ceiling is configured (nothing is
    unenforced, so there is nothing to say), and when the cgroup-scope tier is both opted in
    and available (then pids/RSS *are* enforced, per tree).

    What macOS actually fails to enforce was MEASURED on this host (Darwin 26.6.1, CPython
    3.13), not assumed:

    * ``RLIMIT_AS`` *is* ``RLIMIT_RSS`` on Darwin (the two constants are equal) and is
      inherited as ``(RLIM_INFINITY, RLIM_INFINITY)``, yet ``setrlimit`` rejects ANY finite
      value — including a soft-only change well under the reported hard limit — with
      ``ValueError: current limit exceeds maximum limit``. The shim swallows that
      (``except (ValueError, OSError): continue``), so ``max_rss_mb`` is silently dropped:
      the ceiling is never even installed, let alone enforced.
    * ``RLIMIT_NPROC`` *does* install (hard cap 12000 inherited here) but counts every
      process of the real uid rather than this spawn's tree: under a cap of 5, the very
      FIRST ``fork()`` in the capped child failed with ``BlockingIOError(35)`` / EAGAIN
      while its own tree held one process. So the number does not mean "how many processes
      this tree may have" — below the user's existing process count it denies every fork
      (breaking legitimate tool subprocesses), above it, it bounds the tree not at all.

    Hence neither ceiling delivers "a fork bomb dies contained at ``max_pids``" on macOS.
    NOFILE, the floor, is unaffected and still applies — the warning says so, because a user
    who reads "not enforced" must not conclude the whole shim is inert.
    """
    global _UNENFORCED_CEILINGS_WARNED
    if ceilings.max_pids <= 0 and ceilings.max_rss_mb <= 0:
        return
    if ceilings.cgroup_scopes and probe_cgroup_scopes()[0]:
        return
    if _UNENFORCED_CEILINGS_WARNED:
        return
    _UNENFORCED_CEILINGS_WARNED = True
    if sys.platform == "darwin":
        why = (
            "RLIMIT_AS aliases RLIMIT_RSS on Darwin and this kernel refuses to set it to any "
            "finite value, so the RSS ceiling is silently dropped; RLIMIT_NPROC counts every "
            "process of your uid, so the pids ceiling denies forks instead of sizing the tree"
        )
    else:
        why = (
            "RLIMIT_NPROC counts every process of your uid rather than this spawn's tree, and "
            "RLIMIT_AS bounds one address space rather than the tree's total; per-tree pids/"
            "RSS containment needs a cgroup v2 scope"
        )
    logger.warning(
        "sandbox ceilings NOT enforced on this platform: sandbox.max_pids=%d (pids) and "
        "sandbox.max_rss_mb=%d (RSS) do not bound this spawn's process tree — %s. A fork bomb "
        "or memory blowup in an agent child is NOT contained. sandbox.nofile=%d (NOFILE) IS "
        "still enforced by the exec shim. For real pids/RSS containment, run on Linux with "
        "cgroup v2 + a systemd user session and set sandbox.cgroup_scopes=true (probe: %s).",
        ceilings.max_pids,
        ceilings.max_rss_mb,
        why,
        ceilings.nofile,
        probe_cgroup_scopes()[1],
    )


def cgroup_scope_argv(argv: list[str], ceilings: "ResourceCeilings") -> list[str]:
    """Wrap *argv* in a ``systemd-run --user --scope``, or return it UNCHANGED.

    Unchanged on every opted-out or unenforceable path — the tier is additive, so a host
    that cannot host a scope keeps exactly the behaviour it had before this tier existed:

    * ``ceilings.cgroup_scopes`` is False (the default — not opted in);
    * neither ``max_pids`` nor ``max_rss_mb`` is configured (a scope with no properties
      would cost a systemd round-trip per spawn and enforce nothing);
    * :func:`probe_cgroup_scopes` says the host has no v2 hierarchy / user session.

    A property is emitted only when its ceiling is configured (``> 0``): ``TasksMax=0``
    would be an accidental *total* denial rather than a disabled limit. ``MemorySwapMax=0``
    always accompanies ``MemoryMax`` — without it the tree can escape the memory cap into
    swap, which is exactly the blowup ``MemoryMax`` is there to stop.
    """
    if not argv:
        return argv
    if not ceilings.cgroup_scopes:
        return list(argv)
    if ceilings.max_pids <= 0 and ceilings.max_rss_mb <= 0:
        return list(argv)
    available, _detail = probe_cgroup_scopes()
    if not available:
        return list(argv)
    props: list[str] = []
    if ceilings.max_pids > 0:
        props.append(f"--property=TasksMax={ceilings.max_pids}")
    if ceilings.max_rss_mb > 0:
        props.append(f"--property=MemoryMax={ceilings.max_rss_mb}M")
        props.append("--property=MemorySwapMax=0")
    return ["systemd-run", "--user", "--scope", "--quiet", *props, "--", *argv]


def spawn_shim_argv(
    argv: list[str],
    profile: str = PROFILE_TOOL,
    ceilings: "ResourceCeilings | None" = None,
) -> list[str]:
    """Return *argv* prefixed with the ceiling-shim invocation for *profile*.

    ``none`` (or an empty policy) returns *argv* unchanged — no shim, no interpreter
    startup cost. Otherwise the result is
    ``[sys.executable, "-m", _SHIM_MODULE, <policy-json>, "--", *argv]``; the shim applies
    the limits in the exec'd child and ``execv``s ``argv``.

    When the cgroup-scope tier is opted in and available, that shim invocation is itself
    wrapped in a ``systemd-run --user --scope`` (see :func:`cgroup_scope_argv`) so the scope
    contains the shim *and* the exec'd target: a second enforcement layer ABOVE the NOFILE
    floor, never a substitute for it. Composing it here — the single point every
    agent-influenced spawn already funnels through, including
    :func:`create_subprocess_limited` — keeps the tier from needing a second call site.
    """
    if not argv:
        return argv
    cel = ceilings if ceilings is not None else ResourceCeilings.from_config()
    policy = cel.policy(profile)
    if not policy:
        return list(argv)
    _warn_unenforced_ceilings(cel)
    shimmed = [
        sys.executable,
        "-m",
        _SHIM_MODULE,
        json.dumps(policy, separators=(",", ":")),
        "--",
        *argv,
    ]
    return cgroup_scope_argv(shimmed, cel)


async def create_subprocess_limited(
    *argv: str,
    profile: str = PROFILE_TOOL,
    ceilings: "ResourceCeilings | None" = None,
    **kwargs: object,
):
    """``asyncio.create_subprocess_exec`` with the ceiling shim prepended.

    This is the async seam every agent-influenced spawn goes through instead of a raw
    ``create_subprocess_exec``. It NEVER passes ``preexec_fn`` (the whole point of PHF-1):
    the ceiling is delivered by the shim after ``exec``, so the parent stays on
    ``posix_spawn`` and the event loop never blocks on a forked child. Extra kwargs
    (``stdout``, ``env``, ``cwd``, ``start_new_session`` …) pass straight through.
    """
    import asyncio

    wrapped = spawn_shim_argv(list(argv), profile, ceilings)
    return await asyncio.create_subprocess_exec(*wrapped, **kwargs)  # type: ignore[arg-type]

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


# ── Availability probes ──


def _probe_unshare() -> bool:
    """Return True if user + mount namespaces work (Linux)."""
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
            ret = _libc.unshare(_clone_newuser | _clone_newns)
            os._exit(0 if ret == 0 else 1)
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
                logger.info("sandbox-exec unavailable: macOS %s denies sandbox_apply for third-party binaries", mac_ver)
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
            capture_output=True, timeout=5,
        )
        if r.returncode != 0:
            logger.warning(
                "sandbox-exec probe failed (exit %d): %s",
                r.returncode, r.stderr.decode(errors="replace").strip(),
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
        result = subprocess.run(
            ["ssh", "-V"], capture_output=True, timeout=2, check=False
        )
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

"""Every git PersonalClaw runs keeps the gateway's secrets out, and none runs a program a
repository names.

PersonalClaw runs git inside repositories an agent's shell can write: the state history, the
updater's own checkout, a loop's worktree, a run's workspace, the file browser's repository. An
agent can write a repository's ``.git`` as easily as its files, so a setting planted there (a
hook, ``core.fsmonitor``, ``core.sshCommand``, an external diff, a credential helper, an ``ext::``
remote) ran as the gateway the next time PersonalClaw asked git anything. It ran with the
gateway's whole environment too, which holds every secret saved in PersonalClaw.

``net.git.git_argv`` puts the settings that stop those programs on git's command line, and
``net.git.git_env`` gives git the child allowlist. This file:

* runs real git against a repository with each program planted: first WITHOUT the settings (the
  control, so a green is not a plant that never worked) and then with them;
* drives two call sites the same way, the state history and the updater, so the rail below is
  not the only proof that they are wired;
* holds every git spawn in the tree to ``git_argv``, except a clone into a directory PersonalClaw
  has just made, whose configuration nothing an agent wrote can reach;
* holds the environment to the allowlist and the SSH agent to commands that talk to a remote.

Every git here runs with ``HOME`` in ``tmp_path`` and the machine's system file left out, so
neither the developer's own configuration nor the machine's changes what is measured.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from personalclaw.net.git import (
    TRANSPORT_REFUSALS,
    git_argv,
    git_env,
    guarded_git_env,
    remote_refusal,
    talks_to_remote,
    transport_refusal,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("git") is None, reason="needs git"),
    pytest.mark.skipif(os.name == "nt", reason="the planted programs are POSIX shell scripts"),
]

_IDENTITY = ["-c", "user.name=Example Owner", "-c", "user.email=owner@example.com"]


def _git_version() -> tuple[int, ...]:
    if shutil.which("git") is None:
        return ()
    out = subprocess.run(["git", "version"], capture_output=True, text=True).stdout
    return tuple(int(part) for part in out.split()[2].split(".")[:2] if part.isdigit())


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A scratch HOME with an empty git configuration, whose ``.gitconfig`` and XDG file are the
    global configuration here: the owner's, as these tests plant it. git reads them only when
    ``GIT_CONFIG_GLOBAL`` names no file, so the suite's own is taken out for the test. The plants
    append to ``tmp_path/ran``."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    return tmp_path


def _env() -> dict[str, str]:
    """``git_env`` with the machine's own system file left out, for a measurement of the repo."""
    return {**git_env(site="git-neutral-test"), "GIT_CONFIG_NOSYSTEM": "1"}


def _run(args, cwd: Path, *, neutral: bool, stdin: str | None = None):
    argv = git_argv(args) if neutral else ["git", *args]
    return subprocess.run(
        argv, cwd=cwd, env=_env(), capture_output=True, text=True, input=stdin, timeout=60
    )


def _plant(tmp: Path, name: str, *, then: str = "") -> str:
    """A program that records that it ran, then runs *then* (a shell line) if given."""
    script = tmp / f"plant-{name}.sh"
    script.write_text(f'#!/bin/sh\necho {name} >> "{tmp / "ran"}"\n{then}\n', encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def _ran(tmp: Path) -> list[str]:
    marker = tmp / "ran"
    out = marker.read_text(encoding="utf-8").split() if marker.exists() else []
    marker.unlink(missing_ok=True)
    return out


@pytest.fixture
def repo(scratch):
    """A repository with one commit and one uncommitted change, and nothing planted yet."""
    path = scratch / "repo"
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=_env())
    (path / "notes.txt").write_text("one\n", encoding="utf-8")
    for args in (["add", "notes.txt"], [*_IDENTITY, "commit", "-q", "-m", "first"]):
        subprocess.run(["git", *args], cwd=path, check=True, env=_env())
    (path / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
    return path


def _config(repo: Path, key: str, value: str) -> None:
    subprocess.run(["git", "config", key, value], cwd=repo, check=True, env=_env())


def _neutralised(scratch: Path, repo: Path, args, *, stdin: str | None = None) -> None:
    """The control runs the plant; the same command through ``git_argv`` does not."""
    _run(args, repo, neutral=False, stdin=stdin)
    assert _ran(scratch), f"the control never ran the plant for git {args}: the test is vacuous"
    _run(args, repo, neutral=True, stdin=stdin)
    assert _ran(scratch) == [], f"git {args} ran a program the repository names"


def _signed_commit(repo: Path) -> str:
    """A commit carrying a signature header, which is what makes git start ``gpg.program``."""
    tree = _run(["write-tree"], repo, neutral=False).stdout.strip()
    parent = _run(["rev-parse", "HEAD"], repo, neutral=False).stdout.strip()
    body = (
        f"tree {tree}\nparent {parent}\n"
        "author Example <e@example.com> 1700000000 +0000\n"
        "committer Example <e@example.com> 1700000000 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAd\n -----END PGP SIGNATURE-----\n"
        "\nsigned\n"
    )
    return subprocess.run(
        ["git", "hash-object", "-t", "commit", "-w", "--stdin"],
        cwd=repo,
        env=_env(),
        input=body,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def remote_over_ssh(scratch):
    """``remote_over_ssh(path)``: an ``ssh://`` URL served from the bare repository at *path*.

    The owner's ssh command (their global ``core.sshCommand``, in the scratch HOME) is a stand-in
    that runs the server's side of git on this machine. A remote at a local path is refused by
    the neutral settings, so a push or a fetch that must get through goes over ssh."""
    stand_in = scratch / "ssh-stand-in"
    stand_in.write_text(
        '#!/bin/sh\n[ "$1" = "-G" ] && exit 1\nexec /bin/sh -c "$2"\n', encoding="utf-8"
    )
    stand_in.chmod(0o755)
    (Path(os.environ["HOME"]) / ".gitconfig").write_text(
        f"[core]\n\tsshCommand = {stand_in}\n", encoding="utf-8"
    )

    def _url(path: Path) -> str:
        if not path.exists():
            subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True, env=_env())
        return f"ssh://example.invalid{path.resolve()}"

    return _url


# ── each program a repository can name ─────────────────────────────────────────────────────


def test_a_hook_does_not_run(scratch, repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(Path(_plant(scratch, "hook")).read_text(encoding="utf-8"), encoding="utf-8")
    hook.chmod(0o755)
    _neutralised(scratch, repo, [*_IDENTITY, "commit", "-q", "--allow-empty", "-m", "x"])


def test_a_file_system_monitor_does_not_run(scratch, repo):
    _config(repo, "core.fsmonitor", _plant(scratch, "fsmonitor", then="exit 1"))
    _neutralised(scratch, repo, ["status", "--porcelain"])


def test_an_external_diff_does_not_run(scratch, repo):
    _config(repo, "diff.external", _plant(scratch, "external-diff"))
    _neutralised(scratch, repo, ["diff"])


def test_a_diff_driver_and_its_textconv_do_not_run(scratch, repo):
    (repo / ".gitattributes").write_text("*.txt diff=planted\n", encoding="utf-8")
    _config(repo, "diff.planted.textconv", _plant(scratch, "textconv", then='cat "$1"'))
    _neutralised(scratch, repo, ["diff"])
    _config(repo, "diff.planted.command", _plant(scratch, "driver-command"))
    _neutralised(scratch, repo, ["diff"])


def test_an_editor_does_not_run(scratch, repo):
    _config(repo, "core.editor", _plant(scratch, "editor"))
    _neutralised(scratch, repo, [*_IDENTITY, "commit", "--allow-empty"])


def test_a_credential_helper_and_an_askpass_do_not_run(scratch, repo):
    request = "protocol=https\nhost=example.invalid\n\n"
    _config(repo, "credential.helper", "!" + _plant(scratch, "credential-helper"))
    _neutralised(scratch, repo, ["credential", "fill"], stdin=request)
    subprocess.run(["git", "config", "--unset", "credential.helper"], cwd=repo, env=_env())
    _config(repo, "core.askPass", _plant(scratch, "askpass", then="echo x"))
    _neutralised(scratch, repo, ["credential", "fill"], stdin=request)


def test_a_signature_check_does_not_run_the_signing_program(scratch, repo):
    """``log.showSignature`` makes even ``git log --format=%H`` start ``gpg.program`` on a commit
    that carries a signature, and an agent can write both the setting and the commit."""
    signed = _signed_commit(repo)
    _config(repo, "gpg.program", _plant(scratch, "gpg", then="exit 1"))
    _config(repo, "log.showSignature", "true")
    _neutralised(scratch, repo, ["log", "-1", "--format=%H", signed])


def test_a_format_the_repository_chose_does_not_check_a_signature(scratch, repo):
    """``format.pretty`` is the format of a ``git show`` or ``git log`` given none; with ``%G?`` in
    it, showing a signed commit starts ``gpg.program``."""
    signed = _signed_commit(repo)
    _config(repo, "gpg.program", _plant(scratch, "gpg", then="exit 1"))
    _config(repo, "format.pretty", "format:%H %G?")
    _neutralised(scratch, repo, ["show", "-s", signed])


def test_an_annotated_tag_is_not_signed(scratch, repo):
    """``tag.forceSignAnnotated`` signs a tag given a message (``-m``) and no ``--annotate``."""
    _config(repo, "gpg.program", _plant(scratch, "gpg", then="exit 1"))
    _config(repo, "tag.forceSignAnnotated", "true")
    _run([*_IDENTITY, "tag", "-m", "note", "control"], repo, neutral=False)
    assert _ran(scratch), "the control never ran the signing program: the test is vacuous"
    _run([*_IDENTITY, "tag", "-m", "note", "neutral"], repo, neutral=True)
    assert _ran(scratch) == [], "an annotated tag ran the repository's signing program"


def test_a_repositorys_ssh_command_does_not_run(scratch, repo, monkeypatch):
    """The fetch still goes over ssh, with the plain ``ssh`` on PATH (a stand-in here that records
    it ran and fails): the repository's command is what stops, not the transport."""
    bin_dir = scratch / "bin"
    bin_dir.mkdir()
    plain = bin_dir / "ssh"
    plain.write_text(f'#!/bin/sh\necho plain-ssh >> "{scratch / "ssh-ran"}"\nexit 1\n')
    plain.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    _config(repo, "core.sshCommand", _plant(scratch, "ssh-command", then="exit 1"))
    _config(repo, "remote.planted.url", "ssh://git@example.invalid/example.git")
    _neutralised(scratch, repo, ["fetch", "-q", "planted"])
    assert "plain-ssh" in (scratch / "ssh-ran").read_text().split(), "the fetch never reached ssh"


def test_a_transport_that_runs_a_command_is_refused(scratch, repo):
    """``ext::`` runs the command in its URL. The repository's own setting allowing it (git's
    default refuses it) is what an agent plants, so that is what the command line has to beat."""
    _config(repo, "protocol.ext.allow", "always")
    _config(repo, "remote.planted.url", "ext::" + _plant(scratch, "ext-transport", then="exit 1"))
    _neutralised(scratch, repo, ["fetch", "-q", "planted"])


def test_a_remote_at_a_local_path_is_refused(scratch, repo):
    """A remote at a local path runs ``remote.<name>.uploadpack`` on this machine, and a push there
    runs the other repository's hooks, which no setting given here reaches."""
    source = scratch / "source"
    subprocess.run(["git", "init", "-q", str(source)], check=True, env=_env())
    subprocess.run(
        ["git", *_IDENTITY, "commit", "-q", "--allow-empty", "-m", "s"],
        cwd=source,
        check=True,
        env=_env(),
    )
    _config(repo, "remote.planted.url", str(source))
    _config(
        repo,
        "remote.planted.uploadpack",
        _plant(scratch, "upload-pack", then='exec git-upload-pack "$@"'),
    )
    _neutralised(scratch, repo, ["fetch", "-q", "planted"])
    refused = _run(["fetch", "-q", "planted"], repo, neutral=True)
    assert refused.returncode != 0 and "not allowed" in refused.stderr, refused.stderr


def test_a_borrowed_repositorys_refs_are_not_listed_by_a_command(scratch, repo, remote_over_ssh):
    """A repository that borrows objects from another (``objects/info/alternates``) lists that
    repository's refs during a fetch with ``core.alternateRefsCommand``."""
    lender = scratch / "lender"
    subprocess.run(["git", "init", "-q", str(lender)], check=True, env=_env())
    alternates = repo / ".git" / "objects" / "info" / "alternates"
    alternates.write_text(f"{lender / '.git' / 'objects'}\n", encoding="utf-8")
    _config(repo, "core.alternateRefsCommand", _plant(scratch, "alternate-refs"))
    _config(repo, "remote.origin.url", remote_over_ssh(scratch / "origin.git"))
    _run(["push", "-q", "origin", "HEAD:refs/heads/main"], repo, neutral=False)
    _ran(scratch)  # what the push ran is not what this test measures
    _neutralised(scratch, repo, ["fetch", "-q", "origin", "main"])


def test_a_push_is_not_signed(scratch, repo, remote_over_ssh):
    """``push.gpgSign`` signs a push to a remote that asks for a certificate."""
    url = remote_over_ssh(scratch / "origin.git")
    subprocess.run(
        ["git", "config", "receive.certNonceSeed", "example-seed"],
        cwd=scratch / "origin.git",
        check=True,
        env=_env(),
    )
    _config(repo, "remote.origin.url", url)
    _config(repo, "gpg.program", _plant(scratch, "gpg", then="exit 1"))
    _config(repo, "push.gpgSign", "if-asked")
    # A push certificate names the pusher, so the push needs an identity.
    _neutralised(scratch, repo, [*_IDENTITY, "push", "-q", "origin", "HEAD:refs/heads/main"])


@pytest.mark.skipif(_git_version() < (2, 42), reason="gc.recentObjectsHook is git 2.42+")
def test_a_garbage_collection_does_not_ask_the_repositorys_hook(scratch, repo):
    """``gc.recentObjectsHook`` runs when a collection weighs pruning an old unreachable object,
    and git starts a collection on its own after a commit or a fetch. Settings can only add to
    that hook, so the neutral settings keep the collection from pruning at all."""

    def old_unreachable_object(text: str) -> None:
        oid = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            env=_env(),
            input=text,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        loose = repo / ".git" / "objects" / oid[:2] / oid[2:]
        month_ago = loose.stat().st_mtime - 31 * 24 * 3600
        os.utime(loose, (month_ago, month_ago))

    _config(repo, "gc.recentObjectsHook", _plant(scratch, "recent-objects-hook"))
    old_unreachable_object("first unreachable\n")
    _run(["gc", "-q"], repo, neutral=False)
    assert _ran(scratch), "the control never ran the planted hook: the test is vacuous"
    old_unreachable_object("second unreachable\n")
    _run(["gc", "-q"], repo, neutral=True)
    assert _ran(scratch) == [], "a collection ran the repository's hook"


# ── a refused remote says why, and what to use instead ─────────────────────────────────────


@pytest.mark.parametrize(
    "url,refused",
    [
        ("/srv/example/sync.git", "file"),
        ("./sync.git", "file"),
        ("~/sync.git", "file"),
        ("file:///srv/example/sync.git", "file"),
        ("C:\\repos\\sync.git", "file"),
        ("ext::sh -c example", "ext"),
        ("git://example.invalid/sync.git", "git"),
        ("ssh://git@example.invalid/sync.git", ""),
        ("git@example.invalid:owner/sync.git", ""),
        ("https://example.invalid/owner/sync.git", ""),
        ("git+ssh://example.invalid/sync.git", ""),
        ("example-helper::https://example.invalid/sync", ""),
    ],
)
def test_a_remote_url_is_read_the_way_git_reads_it(url, refused):
    assert remote_refusal(url) == (TRANSPORT_REFUSALS[refused] if refused else "")


def test_each_refusal_names_its_reason_and_the_alternative():
    for transport, sentence in TRANSPORT_REFUSALS.items():
        assert "because" in sentence and sentence.endswith("Reach it over ssh or https instead.")
    assert transport_refusal("fatal: transport 'file' not allowed\n") == TRANSPORT_REFUSALS["file"]
    assert transport_refusal(b"fatal: transport 'ext' not allowed\n") == TRANSPORT_REFUSALS["ext"]
    assert transport_refusal("fatal: repository not found") == "" and transport_refusal(None) == ""


def test_the_updater_says_why_it_will_not_fetch_from_a_local_origin(scratch, repo):
    """🔴 Before, the owner read git's bare ``fatal: transport 'file' not allowed``."""
    from personalclaw import self_update

    source = scratch / "source.git"
    subprocess.run(["git", "init", "-q", "--bare", str(source)], check=True, env=_env())
    _config(repo, "remote.origin.url", str(source))

    fetched = self_update.git_fetch(str(repo), "main")

    assert fetched.returncode != 0
    assert fetched.stderr == TRANSPORT_REFUSALS["file"], fetched.stderr


def test_gits_messages_are_english_whatever_the_owners_locale(monkeypatch):
    """PersonalClaw reads some of git's messages (a refused transport, a rejected push)."""
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    env = git_env(site="t")
    assert env["LANGUAGE"] == "en" and env.get("LANG") == "de_DE.UTF-8"


# ── where the settings go, and what a remote command keeps ─────────────────────────────────


def test_the_settings_come_after_the_callers_own_so_no_caller_option_undoes_one(scratch, repo):
    elsewhere = scratch / "elsewhere-hooks"
    elsewhere.mkdir()
    hook = elsewhere / "pre-commit"
    hook.write_text(Path(_plant(scratch, "caller-hooks")).read_text(encoding="utf-8"))
    hook.chmod(0o755)
    args = [*_IDENTITY, "-c", f"core.hooksPath={elsewhere}", "commit", "-q", "--allow-empty"]
    _neutralised(scratch, repo, [*args, "-m", "x"])

    argv = git_argv(["-C", "/x", "-c", "a.b=c", "status"])
    assert argv[:5] == ["git", "-C", "/x", "-c", "a.b=c"], argv
    assert argv[-1] == "status" and "--no-pager" in argv, argv


def test_a_diff_command_gets_no_external_diff_and_no_textconv():
    assert git_argv(["show", "HEAD:x"])[-4:] == ["show", "--no-ext-diff", "--no-textconv", "HEAD:x"]
    assert "--no-textconv" not in git_argv(["status"])


def test_a_command_with_no_subcommand_is_refused():
    """``git --version -c …`` reads the settings as ``version``'s own arguments and fails."""
    with pytest.raises(ValueError):
        git_argv(["--version"])


def test_a_remote_command_keeps_the_owners_own_sign_in_and_none_of_the_repositorys(scratch):
    """Every file of the owner's counts: the global one, what it includes and the XDG one (a
    distribution's bundled file too, which git reports under no scope). What the read's own
    command line set does not: that is the neutral settings, not the owner."""
    included = scratch / "included.gitconfig"
    included.write_text(
        '[credential "https://example.com"]\n\thelper =\n\thelper = !example-owner-helper\n',
        encoding="utf-8",
    )
    (Path(os.environ["HOME"]) / ".gitconfig").write_text(
        f"[core]\n\tsshCommand = ssh -i ~/.ssh/example_key\n[include]\n\tpath = {included}\n",
        encoding="utf-8",
    )
    xdg = Path(os.environ["XDG_CONFIG_HOME"]) / "git"
    xdg.mkdir(parents=True)
    (xdg / "config").write_text("[credential]\n\thelper = example-xdg-helper\n", encoding="utf-8")

    argv = git_argv(["fetch", "origin"])
    settings = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    ssh = [s for s in settings if s.lower().startswith("core.sshcommand=")]
    assert ssh == ["core.sshcommand=ssh -i ~/.ssh/example_key"], ssh
    reset = settings.index("credential.helper=")
    assert settings.count("credential.helper=") == 1, "the read's own reset was kept as the owner's"
    for owner in (
        "credential.helper=example-xdg-helper",
        "credential.https://example.com.helper=",
        "credential.https://example.com.helper=!example-owner-helper",
    ):
        assert settings.index(owner) > reset, f"{owner} must be set again AFTER the reset"


def test_a_local_command_signs_in_with_nothing():
    settings = [a for a in git_argv(["status"]) if "=" in a]
    assert "credential.helper=" in settings and "core.sshCommand=ssh" in settings, settings
    assert talks_to_remote(["-C", "/x", "fetch"]) and not talks_to_remote(["-C", "/x", "status"])


def test_an_unreadable_owner_configuration_keeps_none_of_it(monkeypatch):
    from personalclaw.net import git as net_git

    def _boom(*_a, **_k):
        raise OSError("no git here")

    monkeypatch.setattr(net_git.subprocess, "run", _boom)
    settings = [a for a in git_argv(["fetch", "origin"]) if "=" in a]
    assert "core.sshCommand=ssh" in settings and settings[-1] == "credential.helper="


# ── the environment ────────────────────────────────────────────────────────────────────────

_SECRET = "example-secret-token-4d1e9c"


def test_git_never_sees_the_gateways_secrets(scratch, monkeypatch):
    """What git hands the programs it starts is its own environment, read here through an alias
    (in the owner's own configuration) that runs ``env``."""
    (Path(os.environ["HOME"]) / ".gitconfig").write_text("[alias]\n\tshowenv = !env\n")
    monkeypatch.setenv("EXAMPLE_SERVICE_API_TOKEN", _SECRET)
    monkeypatch.setenv("GIT_SSH_COMMAND", "example-ssh-wrapper")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hookspath'='/tmp'")

    def shown(env: dict[str, str] | None) -> str:
        return subprocess.run(
            ["git", "showenv"], cwd=scratch, env=env, capture_output=True, text=True, check=True
        ).stdout

    assert _SECRET in shown(None), "the control does not see the planted secret: vacuous"
    seen = shown(git_env(site="git-neutral-test"))
    assert _SECRET not in seen
    for name in ("GIT_SSH_COMMAND", "GIT_CONFIG_PARAMETERS", "EXAMPLE_SERVICE_API_TOKEN"):
        assert f"{name}=" not in seen, name
    assert "GIT_TERMINAL_PROMPT=0" in seen and f"HOME={os.environ['HOME']}" in seen


def test_the_state_historys_git_environment_is_the_allowlist(monkeypatch):
    from personalclaw.durability import state_history as sh

    monkeypatch.setenv("EXAMPLE_SERVICE_API_TOKEN", _SECRET)
    env = sh._git_env()
    assert _SECRET not in env.values()
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == os.devnull


def test_the_ssh_agent_reaches_only_a_git_that_talks_to_a_remote(monkeypatch):
    from personalclaw.sandbox import build_child_env

    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/example-agent.sock")
    assert git_env(site="t", remote=True)["SSH_AUTH_SOCK"] == "/tmp/example-agent.sock"
    assert "SSH_AUTH_SOCK" not in git_env(site="t")
    # The listing fetch speaks HTTPS only, and every other child keeps the floor.
    assert "SSH_AUTH_SOCK" not in guarded_git_env()
    assert "SSH_AUTH_SOCK" not in build_child_env(site="t")
    assert "SSH_AUTH_SOCK" not in build_child_env(site="t", extra={"SSH_AUTH_SOCK": "/x"})


def test_an_apps_git_gets_the_same_two_helpers(monkeypatch):
    """An app's provider runs inside the gateway too; ``personalclaw.sdk.git`` is how its git
    gets core's settings and environment rather than a copy of either."""
    from personalclaw.net import git as net_git
    from personalclaw.sdk import git as sdk_git

    assert sdk_git.git_argv is net_git.git_argv and sdk_git.talks_to_remote is talks_to_remote
    monkeypatch.setenv("EXAMPLE_SERVICE_API_TOKEN", _SECRET)
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/example-agent.sock")
    local, remote = sdk_git.git_env(), sdk_git.git_env(remote=True)
    assert _SECRET not in local.values() and _SECRET not in remote.values()
    assert "SSH_AUTH_SOCK" not in local and remote["SSH_AUTH_SOCK"] == "/tmp/example-agent.sock"
    assert local["GIT_TERMINAL_PROMPT"] == "0"


def test_a_store_source_over_ssh_is_cloned_with_the_ssh_agent(monkeypatch):
    from personalclaw.apps import source as app_source

    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/example-agent.sock")
    seen: list[dict] = []

    def _clone(argv, **kwargs):
        seen.append(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(app_source.subprocess, "run", _clone)
    resolved = app_source._clone_git("git@example.com:owner/example-apps.git")
    try:
        assert seen and seen[0].get("SSH_AUTH_SOCK") == "/tmp/example-agent.sock"
    finally:
        shutil.rmtree(resolved.path, ignore_errors=True)


# ── two call sites, driven ─────────────────────────────────────────────────────────────────


def test_the_state_history_runs_no_program_its_repository_names(scratch, monkeypatch):
    from personalclaw.durability import state_history as sh

    home = scratch / "pc-home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(scratch / "sessions"))
    root = sh.root_by_id("config", home=home)
    (home / "config.json").write_text('{"agent": {}}\n', encoding="utf-8")
    assert sh.commit(root, home=home), "the first history commit did not happen"

    gd = sh.git_dir(root, home=home)
    plant = _plant(scratch, "history-fsmonitor", then="exit 1")
    subprocess.run(["git", "--git-dir", str(gd), "config", "core.fsmonitor", plant], check=True)
    subprocess.run(
        ["git", f"--git-dir={gd}", f"--work-tree={home}", "status", "--porcelain"],
        cwd=home,
        env=_env(),
        capture_output=True,
    )
    assert _ran(scratch), "the control never ran the planted monitor: the test is vacuous"

    (home / "config.json").write_text('{"agent": {"name": "x"}}\n', encoding="utf-8")
    assert sh.commit(root, home=home), "the second history commit did not happen"
    assert _ran(scratch) == [], "the state history ran a program its repository names"


def test_the_updater_runs_no_program_its_checkout_names(scratch, repo):
    from personalclaw import self_update

    _config(repo, "core.fsmonitor", _plant(scratch, "checkout-fsmonitor", then="exit 1"))
    _run(["status", "--porcelain"], repo, neutral=False)
    assert _ran(scratch), "the control never ran the planted monitor: the test is vacuous"

    assert self_update.git_tracked_changes(str(repo)) == [" M notes.txt"]
    assert _ran(scratch) == [], "the updater ran a program its checkout names"


# ── the rail: every git spawn in the tree is neutral ───────────────────────────────────────

#: Clones into a directory PersonalClaw has just made. Their configuration is what git writes
#: for the clone, so nothing an agent wrote reaches them; and a Store source may be a local
#: path, which the neutral settings refuse.
_FRESH_CLONE = {
    "apps/catalog.py::_read_git_registry::subprocess.run",
    "apps/catalog.py::_scan_git_source::subprocess.run",
    "apps/source.py::_clone_git::subprocess.run",
    # Its own, stricter settings: no configuration file at all, HTTPS only, no credentials.
    "net/git.py::run_git_guarded::subprocess.run",
}


def _tail(call: ast.Call) -> str:
    from test_spawn_ceiling_audit import _callee

    return _callee(call).split(".")[-1]


def _argv_kind(node: ast.AST, bound: dict[str, str]) -> str:
    """``neutral`` for an argv ``git_argv`` built, ``raw`` for one that starts with a literal
    ``"git"``, ``""`` for anything else. *bound* maps the names a function set to one of those."""
    if isinstance(node, ast.Starred):
        return _argv_kind(node.value, bound)
    if isinstance(node, ast.Call) and _tail(node) == "spawn_shim_argv" and node.args:
        return _argv_kind(node.args[0], bound)
    if isinstance(node, ast.Call) and _tail(node) == "git_argv":
        return "neutral"
    if isinstance(node, ast.Name):
        return bound.get(node.id, "")
    if isinstance(node, ast.BinOp):  # ["git", ...] + args
        return _argv_kind(node.left, bound)
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        return _argv_kind(node.elts[0], bound)
    if isinstance(node, ast.Constant) and node.value == "git":
        return "raw"
    return ""


def _bound_argvs(fn: ast.AST) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            kind = _argv_kind(node.value, {})
            for target in node.targets:
                if kind and isinstance(target, ast.Name):
                    out[target.id] = kind
    return out


def _git_spawn_census() -> dict[str, set[str]]:
    """``file::qualname::callee`` → the kinds of git argv its spawn calls use."""
    from test_spawn_ceiling_audit import _SPAWN_CALLEES, _callee, _normalize, _src_root

    root = _src_root()
    out: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        class V(ast.NodeVisitor):
            def __init__(self) -> None:
                self.q: list[str] = []
                self.bound: list[dict[str, str]] = [{}]

            def visit_FunctionDef(self, n) -> None:
                self.q.append(n.name)
                self.bound.append({**self.bound[-1], **_bound_argvs(n)})
                self.generic_visit(n)
                self.bound.pop()
                self.q.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, n) -> None:
                self.q.append(n.name)
                self.generic_visit(n)
                self.q.pop()

            def visit_Call(self, n: ast.Call) -> None:
                callee = _normalize(_callee(n))
                if callee in _SPAWN_CALLEES and n.args:
                    kind = _argv_kind(n.args[0], self.bound[-1])
                    if kind:
                        key = f"{rel}::{'.'.join(self.q) or '<module>'}::{callee}"
                        out.setdefault(key, set()).add(kind)
                self.generic_visit(n)

        V().visit(tree)
    return out


def test_every_git_personalclaw_runs_is_neutral_but_a_fresh_clone():
    census = _git_spawn_census()
    raw = {key for key, kinds in census.items() if "raw" in kinds}
    assert raw == _FRESH_CLONE, (
        "a git spawn builds its argv by hand. Use `net.git.git_argv(args)` so a repository's own "
        f"configuration cannot run a program as the gateway:\n  {sorted(raw - _FRESH_CLONE)}\n"
        f"no longer a raw git spawn (drop it from _FRESH_CLONE): {sorted(_FRESH_CLONE - raw)}"
    )


def test_the_git_census_sees_the_neutral_sites():
    """Non-vacuity: the census finds the call sites that run git through ``git_argv``."""
    neutral = {key for key, kinds in _git_spawn_census().items() if "neutral" in kinds}
    for key in (
        "durability/state_history.py::_git::subprocess.run",
        "self_update.py::_run_git::subprocess.run",
        "self_update.py::commits_behind_upstream::asyncio.create_subprocess_exec",
        "dashboard/handlers/updates.py::_do_update_check::asyncio.create_subprocess_exec",
        "loop/worktree.py::_git::subprocess.run",
        "selfqa/fix_branch.py::_git::subprocess.run",
        "dashboard/handlers/files.py::_git::asyncio.create_subprocess_exec",
        "workflows/review_service.py::_git::asyncio.create_subprocess_exec",
        "triggers/liveness.py::_dirty_git_active::subprocess.run",
        "cli_doctor.py::_git_is_inside_work_tree::subprocess.run",
    ):
        assert key in neutral, key


def test_the_census_would_see_a_raw_git_built_in_a_variable():
    """The rail's matcher, on a snippet: a raw argv bound to a name first is still raw."""
    fn = ast.parse(
        "def f(args):\n"
        "    cmd = ['git', *args]\n"
        "    other = git_argv(args)\n"
        "    subprocess.run(cmd)\n"
    ).body[0]
    bound = _bound_argvs(fn)
    assert bound == {"cmd": "raw", "other": "neutral"}, bound

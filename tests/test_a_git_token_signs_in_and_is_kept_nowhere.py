"""A token an app keeps for an https remote signs git in, and git keeps it nowhere.

``git_argv(…, token=True)`` gives a command that talks to a remote one credential helper, which
answers git with the token ``git_env(…, token=…)`` put in that command's environment, in place of
the owner's own helpers. Before it, an app could hand git a token only in the remote's URL: on
git's command line, where every user of the machine can read it, and in the clone's
``.git/config``. And every git that talks to a remote ran the owner's helpers, so a keychain
entry for the same host answered first, and a sign-in that worked was stored there, replacing
the owner's own sign-in for that host.

Driven for real: a bare repository served through ``git http-backend`` behind Basic auth on
127.0.0.1, and the owner's helper a stand-in that records what git asks of it (the owner's
helpers are read from their configuration files, so the stand-in is put there the same way,
never the machine's own keychain).
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from personalclaw.net import git as net_git
from personalclaw.net.git import TOKEN_ENV, TOKEN_USERNAME_ENV, git_argv, git_env

pytestmark = [
    pytest.mark.skipif(shutil.which("git") is None, reason="needs git"),
    pytest.mark.skipif(os.name == "nt", reason="the stand-in helper is a POSIX shell script"),
]

USER = "sync-user"
TOKEN = "pc-fixture-git-token-3f9a"
OWNERS = "pc-fixture-owners-own-7c2e"
_PROXIES = ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A scratch HOME with an empty git configuration, and no proxy between git and 127.0.0.1. Its
    ``.gitconfig`` is the global configuration here, the owner's as these tests plant it: git
    reads it only when ``GIT_CONFIG_GLOBAL`` names no file, so the suite's own is taken out."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    for name in _PROXIES:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


class _Host:
    """A bare repository at ``/sync.git`` served by ``git http-backend``, which answers only a
    request signed in as :data:`USER` with *password*. ``signed_in`` records the Authorization
    header of each request."""

    def __init__(self, root: Path, password: str) -> None:
        self.root = root
        subprocess.run(["git", "init", "-q", "--bare", str(root / "sync.git")], check=True)
        want = "Basic " + base64.b64encode(f"{USER}:{password}".encode()).decode()
        self.signed_in: list[str | None] = []
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # the test output is not a request log
                pass

            def _answer(self) -> None:
                auth = self.headers.get("Authorization")
                host.signed_in.append(auth)
                if auth != want:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="sync"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._backend()

            def _backend(self) -> None:
                path, _, query = self.path.partition("?")
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                env = {
                    "PATH": os.environ["PATH"],
                    "HOME": str(root),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_PROJECT_ROOT": str(root),
                    "GIT_HTTP_EXPORT_ALL": "1",
                    "PATH_INFO": path,
                    "QUERY_STRING": query,
                    "REQUEST_METHOD": self.command,
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(len(body)),
                    "REMOTE_USER": USER,
                    "REMOTE_ADDR": "127.0.0.1",
                }
                out = subprocess.run(
                    ["git", "http-backend"], input=body, env=env, capture_output=True, check=True
                ).stdout
                cut = min(i for i in (out.find(b"\r\n\r\n"), out.find(b"\n\n")) if i != -1)
                head, rest = out[:cut].decode(), out[cut:].lstrip(b"\r\n")
                status, headers = 200, []
                for line in head.splitlines():
                    key, _, value = line.partition(":")
                    if key.lower() == "status":
                        status = int(value.split()[0])
                    elif key:
                        headers.append((key, value.strip()))
                self.send_response(status)
                for key, value in headers:
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(rest)))
                self.end_headers()
                self.wfile.write(rest)

            do_GET = do_POST = _answer

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/sync.git"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def host(scratch):
    served = _Host(scratch / "served", TOKEN)
    yield served
    served.close()


@pytest.fixture
def owners_helper(scratch, monkeypatch):
    """The owner's own credential helper, as git_argv reads it from their configuration: a
    stand-in that records each thing git asks of it (``get``, ``store``, ``erase``) and answers
    ``get`` with the owner's own password for the host."""
    record = scratch / "owners-helper-was-asked"
    helper = scratch / "owners-helper.sh"
    helper.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        f'echo "$1" >> "{record}"\n'
        f'[ "$1" = get ] && printf "username={USER}\\npassword={OWNERS}\\n"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    monkeypatch.setattr(net_git, "_owner_auth_settings", lambda: [f"credential.helper={helper}"])
    return lambda: record.read_text(encoding="utf-8").split() if record.exists() else []


def _ls_remote(url: str, *, token: bool) -> subprocess.CompletedProcess:
    args = ["ls-remote", url]
    env = git_env(site="git-token-test", remote=True, username=USER, token=TOKEN if token else "")
    return subprocess.run(
        git_argv(args, token=token),
        env={**env, "GIT_CONFIG_NOSYSTEM": "1"},
        capture_output=True,
        text=True,
        timeout=60,
    )


def _basic(password: str) -> str:
    return "Basic " + base64.b64encode(f"{USER}:{password}".encode()).decode()


def test_the_owners_helper_answers_a_sign_in_without_a_token(host, owners_helper):
    """The control: the stand-in is the owner's helper git asks, so its silence below means
    something. Its password is the wrong one for this host, and git asks it to forget it."""
    run = _ls_remote(host.url, token=False)

    assert run.returncode != 0, run.stderr
    assert owners_helper()[:1] == ["get"] and "erase" in owners_helper(), owners_helper()
    assert _basic(OWNERS) in host.signed_in, host.signed_in


def test_a_token_signs_in_and_no_helper_of_the_owners_is_asked_or_told(host, owners_helper):
    run = _ls_remote(host.url, token=True)

    assert run.returncode == 0, run.stderr
    assert host.signed_in[-1] == _basic(TOKEN), host.signed_in
    assert owners_helper() == [], "the owner's helper was asked, or told to keep the token"


def test_the_token_is_on_no_command_line(scratch):
    argv = git_argv(["fetch", "origin"], token=True)
    env = git_env(site="git-token-test", remote=True, username=USER, token=TOKEN)

    assert not any(TOKEN in part or USER in part for part in argv), argv
    assert (env[TOKEN_ENV], env[TOKEN_USERNAME_ENV]) == (TOKEN, USER)


def test_a_token_sign_in_keeps_the_owners_ssh_command_and_none_of_their_helpers(scratch):
    (Path(os.environ["HOME"]) / ".gitconfig").write_text(
        "[core]\n\tsshCommand = ssh -i ~/.ssh/example_key\n"
        '[credential]\n\thelper = example-owner-helper\n[credential "https://example.com"]\n'
        "\thelper = !example-owner-url-helper\n",
        encoding="utf-8",
    )

    settings = [a for a in git_argv(["push", "origin", "main"], token=True) if "=" in a]
    helpers = [s for s in settings if s.lower().startswith("credential.")]

    assert "core.sshcommand=ssh -i ~/.ssh/example_key" in settings, settings
    assert helpers == ["credential.helper=", f"credential.helper={net_git._TOKEN_HELPER}"]


def test_the_token_reaches_only_a_command_that_talks_to_a_remote(scratch):
    local = git_env(site="git-token-test", remote=False, username=USER, token=TOKEN)
    helpers = [a for a in git_argv(["status"], token=True) if a.startswith("credential.")]

    assert TOKEN_ENV not in local and TOKEN_USERNAME_ENV not in local, sorted(local)
    assert helpers == ["credential.helper="], helpers


@pytest.mark.parametrize("bad", ["one\ntwo", "one\rtwo", "one\0two"])
def test_a_token_or_user_name_git_would_read_as_two_answers_is_refused(bad):
    for given in ({"token": bad}, {"username": bad, "token": TOKEN}):
        with pytest.raises(ValueError, match="line break or a NUL"):
            git_env(site="git-token-test", remote=True, **given)

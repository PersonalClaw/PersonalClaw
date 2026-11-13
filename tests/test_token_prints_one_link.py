"""``personalclaw token`` prints one link, so ``personalclaw token | pbcopy`` copies it.

It printed this computer's link, then, whenever ``dashboard.url`` named an origin without
"localhost" (a ``127.0.0.1`` one included), a second ``{origin}/?token=`` line, and what the link
is and how long it lasts on stderr every time. A script piping it into ``pbcopy`` or ``open`` got
two links. Now stdout is the one thing the form typed asks for: this computer's link, the link at
``dashboard.url`` (``--remote``), the token (``--bearer``) or one JSON document (``--json``); the
guidance is ``--explain``'s, on stderr, and never holds the token. ``--remote`` with no address
to use is refused before the gateway mints anything.

Driven against a stand-in for this home's gateway; no synthetic value here is or was a credential.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fakes import gateway_stand_in

from personalclaw import cli, cli_server

T = "synthetic.placeholder.not-a-real-token"
NOW = time.time()
REPLY = {"token": T, "expires_in": 72000, "expires_at": NOW + 72000, "open_within": 72000}
TRUNCATED = object()  # a config.json cut off mid-write
DEVICES = "Settings → Devices & Access → Devices"


def _home(tmp_path: Path, dashboard_url: object) -> Path:
    home = (tmp_path / "home").resolve()
    home.mkdir(exist_ok=True)
    (home / ".local_secret").write_text("synthetic-local-secret\n")
    if dashboard_url is TRUNCATED:
        (home / "config.json").write_text('{"dashboard": {"url": "https://pc.exa')
    else:
        (home / "config.json").write_text(json.dumps({"dashboard": {"url": dashboard_url}}))
    return home


def _token(monkeypatch, tmp_path, capsys, *flags: str, url: object = "", reply=None, kind=""):
    """`personalclaw token FLAGS` with dashboard.url *url*: (exit, out, err, port, minted)."""
    home = _home(tmp_path, url)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)
    if kind:
        monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", kind)
    else:
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    routes = {("GET", "/api/token/local"): (200, reply if reply is not None else REPLY)}
    code = 0
    with gateway_stand_in(home, routes=routes) as gateway:
        gateway.record()
        try:
            cli_server._token(cli.build_parser().parse_args(["token", *flags]))
        except SystemExit as exited:
            code = exited.code
        minted = gateway.asked("/api/token/local")
    captured = capsys.readouterr()
    return code, captured.out, captured.err, gateway.port, minted


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://127.0.0.1:10000",
        "http://192.168.1.5:10000",
        "https://pc.example.com",
        "http://localhost:8888",
    ],
)
@pytest.mark.parametrize("kind", ["", "container"])
def test_the_default_is_this_computers_link_alone(monkeypatch, tmp_path, capsys, url, kind):
    code, out, err, port, _ = _token(monkeypatch, tmp_path, capsys, url=url, kind=kind)
    assert (code, out, err) == (0, f"http://127.0.0.1:{port}?token={T}\n", "")


@pytest.mark.parametrize(
    "url, origin",
    [
        ("https://pc.example.com", "https://pc.example.com"),
        ("pc.example.com:8443", "http://pc.example.com:8443"),
    ],
)
def test_remote_is_the_link_at_the_dashboard_url(monkeypatch, tmp_path, capsys, url, origin):
    code, out, err, _, _ = _token(monkeypatch, tmp_path, capsys, "--remote", url=url)
    assert (code, out, err) == (0, f"{origin}?token={T}\n", "")


@pytest.mark.parametrize(
    "url, sentence",
    [
        ("", "❌ dashboard.url is not set, so there is no address for another device's link."),
        ("ftp://pc.example.com", "❌ dashboard.url names no http or https address,"),
        (TRUNCATED, "❌ config.json could not be read:"),
    ],
)
def test_remote_with_no_address_is_refused_before_anything_is_minted(
    monkeypatch, tmp_path, capsys, url, sentence
):
    code, out, err, _, minted = _token(monkeypatch, tmp_path, capsys, "--remote", url=url)
    assert (code, out) == (1, "")
    assert sentence in err, err
    assert "leave out --remote for this computer's link" in err, err
    assert "pc.exa" not in err, "the value is never echoed: it may hold a password"
    if url == TRUNCATED:
        # Repaired where it is, not set again: `config set` writes a config it cannot read.
        assert "config set" not in err and "personalclaw config edit" in err, err
    assert minted == []


def test_bearer_is_the_token_alone(monkeypatch, tmp_path, capsys):
    code, out, err, _, _ = _token(monkeypatch, tmp_path, capsys, "--bearer")
    assert (code, out, err) == (0, f"{T}\n", "")


@pytest.mark.parametrize("url", ["https://pc.example.com", "", TRUNCATED])
def test_json_is_one_document_with_every_key(monkeypatch, tmp_path, capsys, url):
    code, out, _err, port, _ = _token(monkeypatch, tmp_path, capsys, "--json", url=url)
    remote = f"https://pc.example.com?token={T}" if url == "https://pc.example.com" else None
    doc = {
        "url": f"http://127.0.0.1:{port}?token={T}",
        "remote_url": remote,
        "token": T,
        "expires_in": 72000,
        "expires_at": REPLY["expires_at"],
        "open_within": 72000,
    }
    assert code == 0
    assert out == json.dumps(doc, indent=2) + "\n"


def test_json_says_null_for_the_lifetimes_an_older_gateway_leaves_out(
    monkeypatch, tmp_path, capsys
):
    _, out, _, _, _ = _token(monkeypatch, tmp_path, capsys, "--json", reply={"token": T})
    doc = json.loads(out)
    assert (doc["expires_in"], doc["expires_at"], doc["open_within"]) == (None, None, None)


@pytest.mark.parametrize("kind", ["", "container"])
def test_explain_says_what_it_is_on_stderr_and_never_the_token(monkeypatch, tmp_path, capsys, kind):
    code, out, err, port, _ = _token(
        monkeypatch, tmp_path, capsys, "--explain", url="https://pc.example.com", kind=kind
    )
    assert (code, out) == (0, f"http://127.0.0.1:{port}?token={T}\n")
    for said in ("sign-in link", "Authorization: Bearer", "20 hours", DEVICES):
        assert said in err, (said, err)
    assert (f"Port {port} is this container's own port" in err) == (kind == "container"), err
    assert (
        "For another device, personalclaw token --remote prints the link at "
        "https://pc.example.com." in err
    ), err
    assert T not in err


@pytest.mark.parametrize(
    "form, noted", [([], True), (["--json"], True), (["--remote"], False), (["--bearer"], False)]
)
def test_explain_names_the_containers_port_only_where_this_computers_link_is_printed(
    monkeypatch, tmp_path, capsys, form, noted
):
    """The note's port is the one this computer's link carries: --remote's link is at
    dashboard.url's port, and --bearer prints no URL."""
    code, out, err, port, _ = _token(
        monkeypatch,
        tmp_path,
        capsys,
        *form,
        "--explain",
        url="https://pc.example.com",
        kind="container",
    )
    assert code == 0
    assert (f"Port {port} is this container's own port" in err) is noted, err


def test_bearer_explain_says_it_is_a_token_a_script_sends(monkeypatch, tmp_path, capsys):
    code, out, err, _, _ = _token(monkeypatch, tmp_path, capsys, "--bearer", "--explain")
    assert (code, out) == (0, f"{T}\n")
    until = time.strftime("%H:%M on %d %B", time.localtime(REPLY["expires_at"]))
    assert err.startswith(
        "This is a token for your PersonalClaw dashboard: send it as an "
        f'"Authorization: Bearer" header from a script. It works for 20 hours, until {until}.\n'
        "Treat it like a password"
    ), err
    assert "sign-in link" not in err and "open it in a browser" not in err, err
    assert T not in err


def test_two_output_forms_at_once_are_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exited:
        cli.build_parser().parse_args(["token", "--bearer", "--json"])
    assert exited.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "not allowed with argument --bearer" in err, err


def _as_a_script(tmp_path: Path, home: Path, folder: Path) -> subprocess.CompletedProcess:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PERSONALCLAW_", "PYTEST_")) and k != "ANTHROPIC_API_KEY"
    }
    user = tmp_path / "user"
    user.mkdir(exist_ok=True)
    src = Path(__file__).resolve().parents[1] / "src"
    env.update(
        PERSONALCLAW_HOME=str(home),
        HOME=str(user),
        PYTHONIOENCODING="utf-8",
        PYTHONPATH=os.pathsep.join([str(src), env.get("PYTHONPATH", "")]),
    )
    return subprocess.run(
        [sys.executable, "-m", "personalclaw", "token"],
        cwd=folder,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


@pytest.mark.parametrize("with_a_folder_env", [False, True])
def test_piped_it_prints_the_link_and_nothing_else(tmp_path, with_a_folder_env):
    """`personalclaw token | pbcopy`: a script's stdout is the link, and its stderr is empty but
    for the note naming what a folder's `.env` gave this run, which prints every time."""
    home = _home(tmp_path, "https://pc.example.com")
    folder = Path(os.path.realpath(tmp_path)) / "work"
    folder.mkdir()
    if with_a_folder_env:
        (folder / ".env").write_text("ANTHROPIC_API_KEY=folder-key-1\n")
    routes = {("GET", "/api/token/local"): (200, REPLY)}
    with gateway_stand_in(home, routes=routes) as gateway:
        gateway.record()
        done = _as_a_script(tmp_path, home, folder)
    took = (
        f"personalclaw: took ANTHROPIC_API_KEY from {folder / '.env'}, the .env of the folder "
        "this command started in.\n"
    )
    assert (done.returncode, done.stdout, done.stderr) == (
        0,
        f"http://127.0.0.1:{gateway.port}?token={T}\n",
        took if with_a_folder_env else "",
    )

"""``app new --from-template``: fetch the published template repository, treating it as hostile.

The template repository (``github.com/PersonalClaw/app-template``) is the one copy of the
template, so its own CI is what keeps it in step with the scaffold it was generated from. What
this module proves is the other half: ``--from-template`` is the only network surface in
``cli_app_new``, and everything downstream of the socket (member names, member types, member
sizes, the response status) is metadata a malicious host controls. Every refusal in the module
has a named negative test below: non-https scheme, non-allowlisted host, userinfo credentials, a
redirect, a non-200, a traversal member, an absolute member, a symlink member, a hardlink member,
an oversized member, too many members, an empty archive, and an existing non-empty target.

The archive the positive tests serve is built the way the template repository is: a fresh
``app new app-template --type tool`` scaffold, plus the CI workflow a root-level app carries,
packed the way GitHub packs a repository tarball.

The live fetch of the real repository is not exercised here, because the suite is offline:
``test_the_default_archive_url_names_the_documented_repo`` pins the URL, and the transport is
proven against a local HTTP server with the scheme and host allowlists monkeypatched (their
shipped values are pinned by ``test_the_shipped_template_allowlists_are_narrow``, so a widened
default cannot reach a release).
"""

from __future__ import annotations

import argparse
import http.server
import io
import tarfile
import threading
from pathlib import Path

import pytest

from personalclaw.cli_app_new import (
    MAX_MEMBERS,
    TEMPLATE_ARCHIVE_URL,
    TEMPLATE_HOSTS,
    TEMPLATE_REPO,
    TEMPLATE_SCHEMES,
    ScaffoldError,
    app_cmd,
    extract_template_archive,
    fetch_template_archive,
    from_template,
    scaffold,
)

# ---------------------------------------------------------------------------
# Archive fixtures
# ---------------------------------------------------------------------------


def _tar_bytes(entries: list[tarfile.TarInfo], payloads: dict[str, bytes]) -> bytes:
    """A .tar.gz built member-by-member so a test can post an illegal member."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info in entries:
            data = payloads.get(info.name)
            if data is not None:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                tar.addfile(info)
    return buf.getvalue()


def _file_member(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.mode = 0o644
    return info


def _dir_member(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info


@pytest.fixture(scope="module")
def template_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The template repository's shape: a fresh tool scaffold plus its root-level CI workflow."""
    app = scaffold(
        "app-template",
        "tool",
        dest=tmp_path_factory.mktemp("template-repo"),
        display_name="App Template",
        description="The PersonalClaw app template: clone it, rename it, ship it.",
        author="PersonalClaw contributors",
    )
    workflow = app.path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: ci\non: [push]\n", encoding="utf-8")
    # Vacuity: every positive test below extracts this tree, so it must hold the files the
    # platform leg installs from.
    for rel in ("app.json", "provider.py", ".github/workflows/ci.yml"):
        assert (app.path / rel).is_file(), f"the template fixture is missing {rel}"
    return app.path


def _template_tarball(tree: Path, root: str = "app-template-main") -> bytes:
    """*tree*, packed the way GitHub packs a repo tarball (one wrapper dir)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(tree, arcname=root, recursive=True)
    return buf.getvalue()


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves whatever ``self.server.reply`` says: (status, headers, body)."""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        status, headers, body = self.server.reply  # type: ignore[attr-defined]
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture
def local_archive_server(monkeypatch: pytest.MonkeyPatch):
    """A loopback HTTP server the transport is allowed to talk to, for this test only."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    server.reply = (200, {"Content-Type": "application/gzip"}, b"")  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr("personalclaw.cli_app_new.TEMPLATE_SCHEMES", frozenset({"http"}))
    monkeypatch.setattr("personalclaw.cli_app_new.TEMPLATE_HOSTS", frozenset({"127.0.0.1"}))
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}/tar.gz"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_scaffold_readme_sends_the_owner_token_in_the_header_not_the_url(tmp_path) -> None:
    """``personalclaw app new``'s README authenticates every API call with a Bearer header;
    none puts the token in a URL, where it lands in shell history and any proxy log."""
    app = scaffold("header-probe", "channel", dest=tmp_path, year=2026)
    readme = (app.path / "README.md").read_text(encoding="utf-8")
    assert "token=$PERSONALCLAW_TOKEN" not in readme
    assert readme.count('-H "Authorization: Bearer $PERSONALCLAW_TOKEN"') == 3, readme


# ---------------------------------------------------------------------------
# URL validation — refused BEFORE a socket opens
# ---------------------------------------------------------------------------


def test_the_shipped_template_allowlists_are_narrow() -> None:
    """The local-server tests monkeypatch these; a widened SHIPPED default reds here."""
    assert TEMPLATE_SCHEMES == frozenset({"https"})
    assert TEMPLATE_HOSTS == frozenset({"codeload.github.com"})


def test_the_default_archive_url_names_the_documented_repo() -> None:
    assert TEMPLATE_REPO == "PersonalClaw/app-template"
    assert TEMPLATE_ARCHIVE_URL.startswith("https://codeload.github.com/PersonalClaw/app-template/")
    # No redirect is followed, so the URL must name the host that actually serves the file.
    assert "//github.com/" not in TEMPLATE_ARCHIVE_URL


@pytest.mark.parametrize(
    "url,fragment",
    [
        ("http://codeload.github.com/personalclaw/app-template/tar.gz/main", "scheme"),
        ("file:///etc/passwd", "scheme"),
        ("ftp://codeload.github.com/x.tar.gz", "scheme"),
        ("https://evil.example.com/personalclaw/app-template/tar.gz/main", "host"),
        ("https://codeload.github.com.evil.example.com/x.tar.gz", "host"),
        ("https://user:pw@codeload.github.com/x.tar.gz", "userinfo"),
    ],
)
def test_a_disallowed_template_url_is_refused(url: str, fragment: str) -> None:
    with pytest.raises(ScaffoldError) as exc:
        fetch_template_archive(url)
    assert fragment in str(exc.value)


def test_a_non_allowlisted_host_never_reaches_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assert the CALL SITE order: validation runs before the opener is ever built."""
    opened: list[str] = []

    def _explode(*args: object, **kwargs: object) -> None:
        opened.append("build_opener")
        raise AssertionError("the network was reached for a non-allowlisted host")

    monkeypatch.setattr("urllib.request.build_opener", _explode)
    with pytest.raises(ScaffoldError):
        fetch_template_archive("https://evil.example.com/x.tar.gz")
    assert opened == []


# ---------------------------------------------------------------------------
# Transport — status and redirects, against a real loopback server
# ---------------------------------------------------------------------------


def test_a_non_200_response_is_refused(local_archive_server) -> None:
    server, url = local_archive_server
    server.reply = (404, {"Content-Type": "text/plain"}, b"not found")
    with pytest.raises(ScaffoldError) as exc:
        fetch_template_archive(url)
    assert "404" in str(exc.value)


def test_a_redirect_is_refused(local_archive_server) -> None:
    """Not "no cross-host redirect" — no redirect at all, so there is no new host to trust."""
    server, url = local_archive_server
    server.reply = (302, {"Location": "https://evil.example.com/x.tar.gz"}, b"")
    with pytest.raises(ScaffoldError) as exc:
        fetch_template_archive(url)
    assert "redirect" in str(exc.value).lower()


def test_a_200_tarball_is_fetched_and_extracted(
    local_archive_server, template_tree: Path, tmp_path: Path
) -> None:
    """The whole default path — validate, fetch, extract — over a real socket."""
    server, url = local_archive_server
    server.reply = (200, {"Content-Type": "application/gzip"}, _template_tarball(template_tree))
    result = from_template(dest=tmp_path, url=url)
    assert result.source == url
    assert result.path == tmp_path / "app-template"
    assert "app.json" in result.files
    assert (result.path / "provider.py").is_file()


# ---------------------------------------------------------------------------
# Archive member refusals
# ---------------------------------------------------------------------------


def test_an_archive_member_that_escapes_the_target_is_refused(tmp_path: Path) -> None:
    data = _tar_bytes(
        [_file_member("app.json"), _file_member("../../pwned.txt")],
        {"app.json": b"{}", "../../pwned.txt": b"owned"},
    )
    with pytest.raises(ScaffoldError) as exc:
        extract_template_archive(data, target=tmp_path / "out")
    assert "escapes the target" in str(exc.value)
    assert not (tmp_path.parent / "pwned.txt").exists()


def test_a_nested_traversal_member_is_refused(tmp_path: Path) -> None:
    """``a/../../b`` normalises out of the target even though it starts with a real dir."""
    name = "app-template-main/a/../../../pwned.txt"
    data = _tar_bytes([_file_member(name)], {name: b"owned"})
    with pytest.raises(ScaffoldError) as exc:
        extract_template_archive(data, target=tmp_path / "out")
    assert "escapes the target" in str(exc.value)


def test_containment_refuses_even_if_the_name_check_is_bypassed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense in depth, asserted per layer rather than assumed.

    Disabling the ``..`` name check by hand left the two traversal tests above GREEN — the
    post-canonicalisation containment check was catching it and emitting a message that
    matched. The two refusals now say different things, and this test bypasses the first
    layer so the second is proven live on its own instead of inferred.
    """
    monkeypatch.setattr("personalclaw.cli_app_new._checked_member_name", lambda name: name)
    name = "../../pwned.txt"
    data = _tar_bytes([_file_member(name)], {name: b"owned"})
    with pytest.raises(ScaffoldError) as exc:
        extract_template_archive(data, target=tmp_path / "out")
    assert "resolves outside the target" in str(exc.value)
    assert not (tmp_path.parent / "pwned.txt").exists()


def test_an_absolute_archive_member_is_refused(tmp_path: Path) -> None:
    name = "/tmp/pwned.txt"
    data = _tar_bytes([_file_member(name)], {name: b"owned"})
    with pytest.raises(ScaffoldError) as exc:
        extract_template_archive(data, target=tmp_path / "out")
    assert "absolute path" in str(exc.value)


def test_a_symlink_archive_member_is_refused(tmp_path: Path) -> None:
    link = tarfile.TarInfo("app-template-main/escape")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../../../etc/passwd"
    data = _tar_bytes(
        [_file_member("app-template-main/app.json"), link], {"app-template-main/app.json": b"{}"}
    )
    with pytest.raises(ScaffoldError) as exc:
        extract_template_archive(data, target=tmp_path / "out")
    assert "symlink" in str(exc.value)


def test_a_hardlink_archive_member_is_refused(tmp_path: Path) -> None:
    link = tarfile.TarInfo("app-template-main/hard")
    link.type = tarfile.LNKTYPE
    link.linkname = "app-template-main/app.json"
    data = _tar_bytes(
        [_file_member("app-template-main/app.json"), link], {"app-template-main/app.json": b"{}"}
    )
    with pytest.raises(ScaffoldError) as exc:
        extract_template_archive(data, target=tmp_path / "out")
    assert "hardlink" in str(exc.value)


def test_a_fifo_archive_member_is_refused(tmp_path: Path) -> None:
    fifo = tarfile.TarInfo("app-template-main/pipe")
    fifo.type = tarfile.FIFOTYPE
    data = _tar_bytes([fifo], {})
    with pytest.raises(ScaffoldError) as exc:
        extract_template_archive(data, target=tmp_path / "out")
    assert "special" in str(exc.value)


def test_an_oversized_member_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cap is enforced on bytes actually READ, never on the size the archive claims."""
    monkeypatch.setattr("personalclaw.cli_app_new.MAX_MEMBER_BYTES", 16)
    payload = b"x" * 64
    info = _file_member("app-template-main/big.txt")
    # Lie about the size: the guard must not believe the header.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(ScaffoldError) as exc:
        extract_template_archive(buf.getvalue(), target=tmp_path / "out")
    assert "cap" in str(exc.value)


def test_too_many_members_is_refused(tmp_path: Path) -> None:
    names = [f"app-template-main/f{i}.txt" for i in range(MAX_MEMBERS + 1)]
    data = _tar_bytes([_file_member(n) for n in names], {n: b"x" for n in names})
    with pytest.raises(ScaffoldError) as exc:
        extract_template_archive(data, target=tmp_path / "out")
    assert "members" in str(exc.value)


def test_an_archive_with_no_files_is_refused(tmp_path: Path) -> None:
    data = _tar_bytes([_dir_member("app-template-main")], {})
    with pytest.raises(ScaffoldError) as exc:
        extract_template_archive(data, target=tmp_path / "out")
    assert "no files" in str(exc.value)


def test_a_non_tarball_body_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "not-a-tarball.tar.gz"
    archive.write_bytes(b"<html>404</html>")
    with pytest.raises(ScaffoldError) as exc:
        from_template(dest=tmp_path / "dst", archive=archive)
    assert "tarball" in str(exc.value)


# ---------------------------------------------------------------------------
# Target refusals
# ---------------------------------------------------------------------------


def test_an_existing_non_empty_target_is_refused_unless_forced(
    template_tree: Path, tmp_path: Path
) -> None:
    data = _template_tarball(template_tree)
    first = from_template(dest=tmp_path, archive=_write(tmp_path, data))
    assert first.files
    with pytest.raises(ScaffoldError) as exc:
        from_template(dest=tmp_path, archive=_write(tmp_path, data))
    assert "not empty" in str(exc.value)
    again = from_template(dest=tmp_path, archive=_write(tmp_path, data), force=True)
    assert again.path == first.path


def test_a_symlinked_target_is_refused(template_tree: Path, tmp_path: Path) -> None:
    real = tmp_path / "elsewhere"
    real.mkdir()
    (tmp_path / "dst").mkdir()
    (tmp_path / "dst" / "app-template").symlink_to(real, target_is_directory=True)
    with pytest.raises(ScaffoldError) as exc:
        from_template(
            dest=tmp_path / "dst", archive=_write(tmp_path, _template_tarball(template_tree))
        )
    assert "symlink" in str(exc.value)


def test_a_missing_archive_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ScaffoldError) as exc:
        from_template(dest=tmp_path, archive=tmp_path / "nope.tar.gz")
    assert "not found" in str(exc.value)


def _write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "template.tar.gz"
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# Extraction shape + the platform leg
# ---------------------------------------------------------------------------


def test_the_github_wrapper_directory_is_stripped(template_tree: Path, tmp_path: Path) -> None:
    """A GitHub tarball wraps the repo in ``<repo>-<ref>/``; the app must land at the root."""
    result = from_template(
        dest=tmp_path, archive=_write(tmp_path, _template_tarball(template_tree, "app-template-v9"))
    )
    assert (result.path / "app.json").is_file()
    assert not (result.path / "app-template-v9").exists()
    assert (result.path / ".github" / "workflows" / "ci.yml").is_file()


def test_the_fetched_template_installs_and_registers_its_provider(
    template_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The clone-to-installed claim, driven through the real install path in a fake home."""
    import personalclaw.config.loader as loader
    from personalclaw.apps import app_manager, manager
    from personalclaw.providers.registry import get_provider_registry

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: home)
    monkeypatch.setattr(manager, "config_dir", lambda: home)

    result = from_template(
        dest=tmp_path / "dst", archive=_write(tmp_path, _template_tarball(template_tree))
    )
    registry = get_provider_registry()
    try:
        installed = app_manager.install(result.path, origin="local", confirm=True)
        assert installed.ok, f"install refused: {installed.error}"
        assert app_manager.enable("app-template")
        ext = registry.get("app-template")
        assert ext is not None, "the template installed but registered no provider"
        assert ext.enabled, f"registered but not enabled — {ext.error}"
        assert ext.provider_config.type == "tool"
    finally:
        app_manager.disable("app-template")
        registry.deregister("app-template")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def _args(**kwargs: object) -> argparse.Namespace:
    base = dict(
        app_cmd="new",
        name=None,
        type=None,
        list_types=False,
        dest=".",
        display_name="",
        description="",
        author="",
        force=False,
        from_template=False,
        template_url="",
        template_archive="",
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_the_cli_fetches_from_a_local_archive(
    template_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    archive = _write(tmp_path, _template_tarball(template_tree))
    code = app_cmd(
        _args(from_template=True, dest=str(tmp_path / "dst"), template_archive=str(archive))
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "app.json" in out
    assert (tmp_path / "dst" / "app-template" / "app.json").is_file()


def test_the_cli_refuses_a_name_with_from_template(capsys: pytest.CaptureFixture) -> None:
    """Renaming is a documented four-edit step, not a half-done silent refactor."""
    assert app_cmd(_args(from_template=True, name="my-tool")) == 2
    out = capsys.readouterr().err
    assert "--type tool" in out, "the refusal must point at the path that DOES name an app"


def test_the_cli_refuses_from_template_with_a_type(capsys: pytest.CaptureFixture) -> None:
    assert app_cmd(_args(from_template=True, type="tool")) == 2
    assert "pick one" in capsys.readouterr().err


def test_the_cli_refuses_template_flags_without_from_template(
    capsys: pytest.CaptureFixture,
) -> None:
    assert app_cmd(_args(template_url="https://codeload.github.com/x/y/tar.gz/main")) == 2
    assert "--from-template" in capsys.readouterr().err


def test_the_cli_refuses_both_a_url_and_an_archive(capsys: pytest.CaptureFixture) -> None:
    code = app_cmd(
        _args(
            from_template=True,
            template_url="https://codeload.github.com/x/y/tar.gz/main",
            template_archive="/tmp/x.tar.gz",
        )
    )
    assert code == 2
    assert "not both" in capsys.readouterr().err


def test_the_cli_reports_a_refusal_as_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code = app_cmd(
        _args(from_template=True, dest=str(tmp_path), template_url="https://evil.example.com/x.tgz")
    )
    assert code == 1
    assert "not allowed" in capsys.readouterr().err

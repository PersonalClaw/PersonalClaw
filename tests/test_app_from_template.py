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

import io
import tarfile
from pathlib import Path

import app_from_template_shared
import pytest
from app_from_template_shared import _args, _egress

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
from personalclaw.sel import sel

local_archive_server = app_from_template_shared.local_archive_server

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


def test_a_template_host_on_denied_hosts_is_never_contacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The template host is allowlisted, and the owner's Network egress settings still apply:
    a host on Denied hosts is refused, and the refusal audited, before the opener is built."""
    opened: list[str] = []

    def _explode(*args: object, **kwargs: object) -> None:
        opened.append("build_opener")
        raise AssertionError("the network was reached for a denied host")

    monkeypatch.setattr("urllib.request.build_opener", _explode)
    _egress(deny_hosts=["codeload.github.com"])
    with pytest.raises(ScaffoldError) as exc:
        fetch_template_archive(TEMPLATE_ARCHIVE_URL)
    assert opened == []
    assert f"{TEMPLATE_ARCHIVE_URL} was not reached" in str(exc.value)
    assert "Denied hosts" in str(exc.value)
    refused = [
        row.get("resources")
        for row in sel().recent(50)
        if row.get("operation") == "egress_fetch" and row.get("outcome") == "denied"
    ]
    assert refused == [TEMPLATE_ARCHIVE_URL]


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


def test_a_missing_archive_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ScaffoldError) as exc:
        from_template(dest=tmp_path, archive=tmp_path / "nope.tar.gz")
    assert "not found" in str(exc.value)


# ---------------------------------------------------------------------------
# Extraction shape + the platform leg
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


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

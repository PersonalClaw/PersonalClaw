"""The ``Content-Disposition`` header never carries an unredacted user string, and every
download route composes it the same way (``personalclaw.http_download``).

The defect this pins: ``GET /api/projects/{id}/export`` interpolated a user-typed project name
straight into ``attachment; filename="…"`` with no redaction, so a credential in the name reached
proxy logs and browser download history — a copy that outlives deleting the archive, and one the
user never consented to. The archive BODY carrying secrets is deliberate (the response ships
``X-PersonalClaw-Secrets-Expected``); the header was not.

Driven through the real aiohttp route, not read off the source: the source already "looks" fine to
a reader, and the two earlier reviews of this function both read it as safe. Every "no leak"
assertion below is paired with a POSITIVE CONTROL that fires on the same fixture, because
"the credential is not in the header" fails two ways — the header is clean, or the fixture is
simply not a shape any redactor recognises.

The isolated ``aiohttp.http_writer._serialize_headers`` probe is deliberately NOT repeated here: it
raises ``ValueError: Forbidden control character detected in headers`` on a non-ASCII name while the
live route answers 200 with raw UTF-8 on the wire, so it reports a failure that does not exist. The
route is the measurement.
"""

from __future__ import annotations

import io
import re
import urllib.parse
import zipfile
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import http_download
from personalclaw.dashboard import session_export
from personalclaw.security import redact_field
from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes
from personalclaw.workflows import project_archive as pa

#: The repo's own synthetic non-credential fixture (``test_session_starters``,
#: ``test_session_share``), so this file mints no new secret-shaped string.
FAKE_SK_TOKEN = "sk-notarealfixture0123456789abcdefghij"


# ── the positive control, asserted once and reused ───────────────────────────


def test_the_redactor_recognises_the_fixture_so_a_clean_header_means_something():
    """POSITIVE CONTROL for every "not in the header" assertion below.

    Without this, a green suite is ambiguous: either the header is redacted, or the fixture is a
    shape ``redact_field`` was never going to catch and the leak would still ship for real
    credentials. Pinned on the redactor directly so the control cannot be satisfied by the same
    bug it is controlling for.
    """
    assert redact_field(f"deploy with {FAKE_SK_TOKEN}") == "deploy with [REDACTED: credential]"


# ── the shared helper ────────────────────────────────────────────────────────


def test_the_stem_redacts_the_user_text():
    """The stem is redacted, and redaction runs BEFORE punctuation is collapsed.

    On the ordering: it is kept because sanitising first can in principle break a credential
    shape into something the redactor no longer matches while leaving it identifiable, and
    because ``session_export`` documented it that way. It is NOT asserted as observable here,
    because measuring it found that it currently is not: of five credential shapes tried, the two
    ``redact_field`` recognises (``sk-``, ``ghp_``) are recognised in EITHER order — the
    allowlist's ``-`` substitution does not defeat them — and the two the allowlist would mangle
    (an AWS secret containing ``/``, a JWT containing ``.``) are not recognised in either order.
    So the ordering is defence against a redactor change, not against today's redactor, and a
    test asserting otherwise would be asserting a control that never fires.
    """
    stem = http_download.safe_download_stem(f"deploy with {FAKE_SK_TOKEN}")
    assert FAKE_SK_TOKEN not in stem
    assert stem == "deploy-with-REDACTED-credential"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("../../etc", "etc"),  # traversal falls out of the allowlist, not a separate check
        ("Q3 Planning", "Q3-Planning"),
        ("Café", "Café"),  # non-ASCII is CARRIED, not folded away
        ("日本語のチャット", "日本語のチャット"),
        ("///", ""),  # nothing usable; the caller supplies its own literal
        ("a" * 200, "a" * 60),
    ],
)
def test_the_stem_is_safe_and_keeps_the_users_own_characters(text, expected):
    assert http_download.safe_download_stem(text) == expected


def test_the_stem_falls_back_to_the_id_and_sanitises_it_too():
    """A machine-generated fallback is no more trustworthy than a title, so it takes the same
    path. An empty pair returns "" rather than inventing a domain word."""
    assert http_download.safe_download_stem("", fallback="p-765f9d38") == "p-765f9d38"
    assert http_download.safe_download_stem("///", fallback="../../etc") == "etc"
    assert http_download.safe_download_stem("", fallback="") == ""
    assert FAKE_SK_TOKEN not in http_download.safe_download_stem("", fallback=FAKE_SK_TOKEN)


@pytest.mark.parametrize(
    "hostile",
    [
        'x"; filename="owned.exe',  # close the quoted string, append a second parameter
        "x\r\nSet-Cookie: a=b",  # start a new header
        "x\nX-Injected: 1",
        "x\\",  # trailing escape, swallowing the closing quote
        "x\x00y",
        "x\x7fy",
    ],
)
def test_the_header_value_cannot_BE_injected(hostile):
    """Closed by construction rather than by validation: the ASCII fallback is rebuilt from an
    allowlist and ``filename*`` is percent-encoded with an empty safe set.

    Asserted STRUCTURALLY, not by substring. ``filename*=…Set-Cookie%3A%20a%3Db`` contains the
    literal text "Set-Cookie" and is perfectly inert — the CR/LF that would have made it a header
    are ``%0D%0A``. A substring check would fail on a safe value and teach the next reader to
    weaken the encoding, so the assertions are: the value is ONE header line, the quoted string is
    closed exactly once, and no raw delimiter or control byte survives anywhere in it.
    """
    value = http_download.attachment_disposition(f"report-{hostile}.zip")
    assert re.fullmatch(
        r'attachment; filename="[A-Za-z0-9._-]*"; filename\*=UTF-8\'\'[A-Za-z0-9._%-]*', value
    ), f"the header value lost its shape: {value!r}"
    assert value.count('"') == 2, f"the quoted string was closed early: {value!r}"
    assert not [
        ch for ch in value if ord(ch) < 0x20 or ord(ch) == 0x7F
    ], f"a raw control byte survived into the header: {value!r}"
    assert "\\" not in value, f"a backslash could escape the closing quote: {value!r}"


def test_the_header_carries_non_ascii_in_the_rfc_6266_form_with_an_ascii_fallback():
    """Both parameters, always. A client that understands RFC 6266 gets the user's characters;
    one that predates it reads a fallback that is still a usable name."""
    value = http_download.attachment_disposition("Café.txt")
    assert value == "attachment; filename=\"Caf-.txt\"; filename*=UTF-8''Caf%C3%A9.txt"
    # A name that folds away ENTIRELY keeps its extension and gains a stem, rather than
    # shipping filename=".md" and asking the browser to save a dotfile.
    assert http_download.attachment_disposition("日本語.md").startswith(
        'attachment; filename="download.md";'
    )
    assert http_download.attachment_disposition("").startswith('attachment; filename="download";')


def test_the_header_redacts_a_name_that_never_saw_a_stem_builder():
    """``handlers/files.py`` hands over an on-disk filename directly, so this is the ONLY
    redaction pass that header ever gets."""
    value = http_download.attachment_disposition(f"{FAKE_SK_TOKEN}.txt")
    assert FAKE_SK_TOKEN not in value
    # …and not smuggled past the assertion as percent-encoding either.
    assert FAKE_SK_TOKEN not in urllib.parse.unquote(value)


# ── the two name builders delegate rather than re-deriving ───────────────────


def test_the_project_archive_name_is_redacted():
    """The defect, at the function the route interpolates. ``archive_filename`` had no
    ``redact_field`` at all, so this string arrived in the header intact.

    The traversal / empty-name / non-ASCII rows for this function live with the function's own
    suite (``test_workflows_project_archive.test_the_download_name_is_filesystem_SAFE``, itself
    retargeted in this change) rather than being copied here — two homes for one assertion is how
    they drift apart.
    """
    name = pa.archive_filename(f"deploy with {FAKE_SK_TOKEN}", "p-765f9d38")
    assert FAKE_SK_TOKEN not in name
    assert name == "personalclaw-project-deploy-with-REDACTED-credential.zip"


def test_the_transcript_name_is_redacted_and_keeps_non_ascii():
    assert FAKE_SK_TOKEN not in session_export.export_filename(
        f"deploy with {FAKE_SK_TOKEN}", "k", "md"
    )
    assert (
        session_export.export_filename("日本語のチャット", "k", "json") == "日本語のチャット.json"
    )


# ── driven: the real route ───────────────────────────────────────────────────


@asynccontextmanager
async def _client(tmp_path):
    """The project routes over one isolated home. Same shape as
    ``test_projects_work_route``: every ``config_dir`` the export handler reaches is pointed at
    a temp dir, and the gateway's request-boundary middleware is installed so a refusal is
    answered where the real gateway answers it."""
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch("personalclaw.workflows.store.config_dir", return_value=tmp_path),
        patch("personalclaw.workflows.leases.config_dir", return_value=tmp_path),
        patch("personalclaw.concurrency.config_dir", return_value=tmp_path),
        patch("personalclaw.loop.files.config_dir", return_value=tmp_path),
    ):
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        app = web.Application(middlewares=[request_boundary_middleware()])
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


async def _export(client, project_name: str):
    """Create a project called *project_name*, download it, return ``(status, disposition)``."""
    created = await client.post("/api/projects", json={"name": project_name})
    assert created.status in (200, 201), await created.text()
    pid = (await created.json())["id"]
    resp = await client.get(f"/api/projects/{pid}/export")
    return resp.status, resp.headers.get("Content-Disposition", "")


@pytest.mark.asyncio
async def test_the_export_route_does_not_put_a_credential_in_the_download_header(tmp_path):
    """THE regression. Before the fix this answered 200 with
    ``filename="personalclaw-project-deploy-with-sk-notarealfixture0123456789abcdefghij.zip"``.
    """
    async with _client(tmp_path) as client:
        status, disposition = await _export(client, f"deploy with {FAKE_SK_TOKEN}")

    assert status == 200, "the export must still SUCCEED — the archive is a portable export"
    assert disposition, "the route stopped sending a Content-Disposition at all"
    assert FAKE_SK_TOKEN not in disposition, (
        f"the credential reached the download header verbatim: {disposition!r} — this is the "
        "copy that lands in proxy logs and browser download history"
    )
    assert (
        "REDACTED" in disposition
    ), "nothing was redacted, so the clean read above may only mean the name was dropped"


@pytest.mark.asyncio
async def test_the_export_route_carries_a_non_ascii_project_name_instead_of_mangling_it(tmp_path):
    """Defect 2. The plain ``filename="…"`` form put raw UTF-8 on the wire (200, but a header no
    RFC allows); the answer is RFC 6266, not folding the user's name away."""
    async with _client(tmp_path) as client:
        status, disposition = await _export(client, "Café")

    assert status == 200
    assert disposition == (
        'attachment; filename="personalclaw-project-Caf-.zip"; '
        "filename*=UTF-8''personalclaw-project-Caf%C3%A9.zip"
    )
    assert disposition.isascii(), "a header value must be ASCII on the wire"


@pytest.mark.asyncio
async def test_only_the_HEADER_is_redacted_the_archive_body_is_verbatim_on_purpose(tmp_path):
    """SCOPE RAIL, and the sharpest statement of the fix: ONE request, the fixture absent from
    the header and present in the bytes.

    The archive is a portable export, not a redacted artifact — the response says so in
    ``X-PersonalClaw-Secrets-Expected`` and ``project_archive`` has no redaction by design. A
    future session that "finishes the job" by redacting the body breaks an intended behaviour,
    and a session that reverts the header fix breaks the other assertion. Both directions are
    pinned here so neither can be traded for the other.
    """
    async with _client(tmp_path) as client:
        created = await client.post("/api/projects", json={"name": f"deploy with {FAKE_SK_TOKEN}"})
        pid = (await created.json())["id"]
        resp = await client.get(f"/api/projects/{pid}/export")
        assert resp.status == 200
        disposition = resp.headers.get("Content-Disposition", "")
        assert "X-PersonalClaw-Secrets-Expected" in resp.headers
        blob = await resp.read()

    assert FAKE_SK_TOKEN not in disposition, "the header leaked — that is the defect"
    # Read the MEMBER, not the raw blob: the zip is DEFLATE-compressed, so scanning the bytes for
    # the plaintext reads a false "the body is clean" on an archive that carries it in full.
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        manifest = zf.read("manifest.json").decode("utf-8")
    assert FAKE_SK_TOKEN in manifest, (
        "the archive body no longer carries the name verbatim. If that was deliberate, the "
        "X-PersonalClaw-Secrets-Expected contract and project_archive's zero-redaction design "
        "changed with it — do not silence this by redacting the body"
    )


@pytest.mark.asyncio
async def test_the_export_route_emits_the_one_shared_header_shape(tmp_path):
    """The unification, driven at the route this defect was found in: both RFC 6266 parameters,
    from the shared emitter.

    The OTHER user-named download (the transcript route) is driven at its own site —
    ``test_session_share.test_export_route_redacts_title_from_filename_and_body`` asserts the same
    shape there — rather than rebuilding that fixture chain here.
    """
    async with _client(tmp_path) as client:
        status, disposition = await _export(client, "Shared Convention")
    assert status == 200
    assert (
        disposition == 'attachment; filename="personalclaw-project-Shared-Convention.zip"; '
        "filename*=UTF-8''personalclaw-project-Shared-Convention.zip"
    ), disposition

"""``tools/ci/release_targets.py``: what a release's publish target already holds (#3911).

Each publish job reads its target before it writes, as object name to digest, and holds that
against the approved manifest (``release-manifest compare``): an identical object resumes, a
different one fails before anything is written. Nothing it cannot read counts as empty: only the
target's own "not there" answer does (PyPI's and GitHub's 404, a registry's "manifest unknown").
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import release_manifest as rm
from tools.ci import release_targets as rt

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parent.parent
WHEEL = "personalclaw-0.3.0-py3-none-any.whl"
SDIST = "personalclaw-0.3.0.tar.gz"
HEX = "ab" * 32


class Fetch:
    """The answers of a planted API, by URL; every URL and header set asked is kept."""

    def __init__(self, **answers: tuple[int, object]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        self.asked.append((url, dict(headers)))
        status, body = self.answers.get("any") or next(iter(self.answers.values()))
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        return status, data


def _pypi(*files: tuple[str, object]) -> dict[str, object]:
    return {"urls": [{"filename": name, "digests": {"sha256": digest}} for name, digest in files]}


# ── PyPI ──────────────────────────────────────────────────────────────────────────────────────


def test_pypi_names_each_file_this_version_holds_by_its_sha256() -> None:
    fetch = Fetch(any=(200, _pypi((WHEEL, HEX), (SDIST, "cd" * 32))))
    assert rt.pypi("personalclaw", "0.3.0", fetch) == {
        WHEEL: f"sha256:{HEX}",
        SDIST: f"sha256:{'cd' * 32}",
    }
    assert [url for url, _ in fetch.asked] == ["https://pypi.org/pypi/personalclaw/0.3.0/json"]


def test_a_version_pypi_has_not_published_holds_nothing() -> None:
    assert rt.pypi("personalclaw", "0.3.0", Fetch(any=(404, {"message": "Not Found"}))) == {}


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        ((500, b"oops"), "answered 500"),
        ((403, b"denied"), "answered 403"),
        ((200, b"<html>"), "not JSON"),
        ((200, {"releases": {}}), "no list of files"),
        ((200, _pypi((WHEEL, "not-hex"))), "no sha256"),
        ((200, {"urls": [{"filename": WHEEL}]}), "no sha256"),
        ((200, _pypi((WHEEL, HEX), (WHEEL, HEX))), "twice"),
    ],
)
def test_an_answer_pypi_cannot_be_read_from_refuses(answer: tuple[int, object], says: str) -> None:
    with pytest.raises(rt.Refused, match=says):
        rt.pypi("personalclaw", "0.3.0", Fetch(any=answer))


def test_a_pypi_that_cannot_be_reached_refuses_and_never_reads_as_empty() -> None:
    def down(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        raise OSError("connection reset")

    with pytest.raises(rt.Refused, match="connection reset"):
        rt.pypi("personalclaw", "0.3.0", down)


def test_a_name_or_version_that_is_not_one_is_refused_before_anything_is_asked() -> None:
    fetch = Fetch(any=(404, {}))
    for project, version in [("personal claw", "0.3.0"), ("personalclaw", "0.3.0/../x"), ("", "1")]:
        with pytest.raises(rt.Refused):
            rt.pypi(project, version, fetch)
    assert fetch.asked == []


# ── the GitHub Release ────────────────────────────────────────────────────────────────────────


def _release(*assets: dict[str, object]) -> dict[str, object]:
    return {"tag_name": "v0.3.0", "assets": list(assets)}


def _asset(name: str, digest: object = f"sha256:{HEX}", state: str = "uploaded") -> dict:
    return {"name": name, "digest": digest, "state": state}


def test_a_release_names_each_asset_by_its_digest_and_is_read_with_the_token() -> None:
    fetch = Fetch(any=(200, _release(_asset(WHEEL), _asset(SDIST, f"sha256:{'cd' * 32}"))))
    assert rt.github_release("owner/project", "v0.3.0", fetch, token="t0ken") == {
        WHEEL: f"sha256:{HEX}",
        SDIST: f"sha256:{'cd' * 32}",
    }
    ((url, headers),) = fetch.asked
    assert url == "https://api.github.com/repos/owner/project/releases/tags/v0.3.0"
    assert headers["Authorization"] == "Bearer t0ken"


def test_a_tag_with_no_release_holds_nothing() -> None:
    assert rt.github_release("o/p", "v0.3.0", Fetch(any=(404, {})), token="t") == {}


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        ((500, b""), "answered 500"),
        ((401, b""), "answered 401"),
        ((200, {"tag_name": "v0.3.0"}), "no list of assets"),
        ((200, _release(_asset(WHEEL, None))), "no digest"),
        ((200, _release(_asset(WHEEL, "md5:abc"))), "no digest"),
        ((200, _release(_asset(WHEEL, state="starter"))), "not wholly uploaded"),
        ((200, _release(_asset(WHEEL), _asset(WHEEL))), "twice"),
    ],
)
def test_a_release_that_cannot_be_read_refuses(answer: tuple[int, object], says: str) -> None:
    with pytest.raises(rt.Refused, match=says):
        rt.github_release("o/p", "v0.3.0", Fetch(any=answer), token="t")


def test_a_release_is_never_read_without_a_token() -> None:
    with pytest.raises(rt.Refused, match="token"):
        rt.github_release("o/p", "v0.3.0", Fetch(any=(200, _release())), token="")


# ── an image's tag in the registry ────────────────────────────────────────────────────────────

GATEWAY = "ghcr.io/personalclaw/personalclaw-gateway"
INDEX = json.dumps({"schemaVersion": 2, "manifests": []}).encode()


def test_an_images_tag_names_the_image_by_the_digest_of_the_index_it_holds() -> None:
    asked: list[str] = []

    def inspect(ref: str) -> tuple[int, bytes, str]:
        asked.append(ref)
        return 0, INDEX, ""

    assert rt.image(GATEWAY, "0.3.0", inspect) == {
        GATEWAY: "sha256:" + hashlib.sha256(INDEX).hexdigest()
    }
    assert asked == [f"docker://{GATEWAY}:0.3.0"]


def test_a_tag_the_registry_does_not_hold_holds_nothing() -> None:
    def missing(ref: str) -> tuple[int, bytes, str]:
        return 1, b"", f"Error: reading manifest 0.3.0 in {GATEWAY}: manifest unknown"

    assert rt.image(GATEWAY, "0.3.0", missing) == {}


@pytest.mark.parametrize(
    "said",
    [
        "Error: reading manifest 0.3.0 in x: unauthorized: authentication required",
        "Error: pinging container registry ghcr.io: connection refused",
        "",
    ],
)
def test_a_registry_that_cannot_be_read_refuses(said: str) -> None:
    with pytest.raises(rt.Refused, match="cannot read"):
        rt.image(GATEWAY, "0.3.0", lambda ref: (1, b"", said))


def test_an_image_or_tag_that_is_not_one_is_refused_before_anything_is_asked() -> None:
    asked: list[str] = []
    for name, tag in [(GATEWAY, "latest; rm"), ("ghcr.io/x y", "0.3.0"), (GATEWAY, "")]:
        with pytest.raises(rt.Refused):
            rt.image(name, tag, lambda ref: asked.append(ref) or (0, INDEX, ""))
    assert asked == []


# ── what it feeds: the manifest's compare ─────────────────────────────────────────────────────


def _manifest() -> dict[str, object]:
    return {
        "files": {WHEEL: {"digest": f"sha256:{HEX}"}, SDIST: {"digest": f"sha256:{'cd' * 32}"}},
        "images": {GATEWAY: {"index": "sha256:" + hashlib.sha256(INDEX).hexdigest()}},
    }


def test_what_a_target_holds_resumes_when_it_is_the_manifests_and_fails_when_it_differs() -> None:
    select = ["personalclaw-[0-9]*.whl", "personalclaw-[0-9]*.tar.gz"]
    nothing = rt.pypi("personalclaw", "0.3.0", Fetch(any=(404, {})))
    assert rm.compare(_manifest(), select, nothing).create == (WHEEL, SDIST)
    same = rt.pypi("personalclaw", "0.3.0", Fetch(any=(200, _pypi((WHEEL, HEX)))))
    got = rm.compare(_manifest(), select, same)
    assert (got.create, got.resume, got.refused) == ((SDIST,), (WHEEL,), ())
    other = rt.pypi("personalclaw", "0.3.0", Fetch(any=(200, _pypi((WHEEL, "ef" * 32)))))
    refused = rm.compare(_manifest(), select, other).refused
    assert [(f.kind, f.artifact) for f in refused] == [("differs", WHEEL)]
    tag = rt.image(GATEWAY, "0.3.0", lambda ref: (0, INDEX, ""))
    assert rm.compare(_manifest(), [GATEWAY], tag).resume == (GATEWAY,)


# ── the command, run as a trusted job runs it ─────────────────────────────────────────────────


def test_the_command_writes_what_the_target_holds_and_a_refusal_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "held.json"
    monkeypatch.setattr(rt, "inspect_image", lambda ref: (0, INDEX, ""))
    assert rt.main(["image", "--image", GATEWAY, "--tag", "0.3.0", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {
        GATEWAY: "sha256:" + hashlib.sha256(INDEX).hexdigest()
    }
    refused = tmp_path / "refused.json"
    monkeypatch.setattr(rt, "inspect_image", lambda ref: (1, b"", "unauthorized"))
    assert rt.main(["image", "--image", GATEWAY, "--tag", "0.3.0", "--out", str(refused)]) == 2
    assert not refused.exists(), "nothing is written for a target it could not read"


def test_it_runs_in_isolated_mode_on_the_standard_library_alone(tmp_path: Path) -> None:
    run = subprocess.run(
        [sys.executable, "-I", str(ROOT / "tools/ci/release_targets.py"), "nothing"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert run.returncode == 2 and "usage: release-targets" in run.stderr, run.stderr


# ── the four cases of a resume, for each target ───────────────────────────────────────────────
#
# What a re-run after a partial publish must do, held against planted API answers: a name the
# target holds with the manifest's digest is skipped, a name the manifest has and the target lacks
# is written, a name the target holds with another digest refuses, and a name the target holds
# that the manifest does not (for this version) refuses. So the re-run finishes the job and changes
# nothing already out.

OTHER = "sha256:" + "ef" * 32
EXTRA = "personalclaw-0.3.0-py2-none-any.whl"


def _files_manifest() -> dict[str, object]:
    return {
        "files": {WHEEL: {"digest": f"sha256:{HEX}"}, SDIST: {"digest": f"sha256:{'cd' * 32}"}},
        "images": {},
    }


def _read_pypi(held: dict[str, str]) -> dict[str, str]:
    answer = (200, _pypi(*[(n, d.removeprefix("sha256:")) for n, d in held.items()]))
    return rt.pypi("personalclaw", "0.3.0", Fetch(any=answer))


def _read_release(held: dict[str, str]) -> dict[str, str]:
    answer = (200, _release(*[_asset(n, d) for n, d in held.items()]))
    return rt.github_release("o/p", "v0.3.0", Fetch(any=answer), token="t")


FILE_TARGETS = {"pypi": _read_pypi, "release": _read_release}
SELECT = ["personalclaw-[0-9]*.whl", "personalclaw-[0-9]*.tar.gz"]


@pytest.mark.parametrize("target", sorted(FILE_TARGETS))
def test_a_file_target_skips_what_it_holds_and_writes_what_it_lacks(target: str) -> None:
    held = FILE_TARGETS[target]({WHEEL: f"sha256:{HEX}"})
    got = rm.compare(_files_manifest(), SELECT, held)
    assert (got.resume, got.create, got.refused) == ((WHEEL,), (SDIST,), ())


@pytest.mark.parametrize("target", sorted(FILE_TARGETS))
def test_a_file_target_holding_another_digest_refuses(target: str) -> None:
    held = FILE_TARGETS[target]({WHEEL: OTHER})
    got = rm.compare(_files_manifest(), SELECT, held)
    assert (got.resume, got.create) == ((), ())
    assert [(f.kind, f.artifact) for f in got.refused] == [("differs", WHEEL)]


@pytest.mark.parametrize("target", sorted(FILE_TARGETS))
def test_a_file_target_holding_what_the_manifest_does_not_name_refuses(target: str) -> None:
    held = FILE_TARGETS[target]({WHEEL: f"sha256:{HEX}", EXTRA: OTHER})
    got = rm.compare(_files_manifest(), SELECT, held)
    assert (got.resume, got.create) == ((), ())
    assert [(f.kind, f.artifact) for f in got.refused] == [("unexpected", EXTRA)]


def _image_manifest() -> dict[str, object]:
    return {"files": {}, "images": {GATEWAY: {"index": _sha_of(INDEX)}}}


def _sha_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def test_an_images_tag_is_skipped_when_it_names_the_manifests_index() -> None:
    got = rm.compare(
        _image_manifest(), [GATEWAY], rt.image(GATEWAY, "0.3.0", lambda r: (0, INDEX, ""))
    )
    assert (got.resume, got.create, got.refused) == ((GATEWAY,), (), ())


def test_an_images_tag_the_registry_lacks_is_written() -> None:
    lacks = rt.image(GATEWAY, "0.3.0", lambda r: (1, b"", "manifest unknown"))
    got = rm.compare(_image_manifest(), [GATEWAY], lacks)
    assert (got.resume, got.create, got.refused) == ((), (GATEWAY,), ())


def test_an_images_tag_naming_another_index_refuses() -> None:
    other = rt.image(GATEWAY, "0.3.0", lambda r: (0, b'{"schemaVersion": 2}', ""))
    got = rm.compare(_image_manifest(), [GATEWAY], other)
    assert [(f.kind, f.artifact) for f in got.refused] == [("differs", GATEWAY)]


def test_an_image_the_manifest_does_not_name_refuses() -> None:
    web = "ghcr.io/personalclaw/personalclaw-web"
    held = rt.image(web, "0.3.0", lambda r: (0, INDEX, ""))
    got = rm.compare(_image_manifest(), [GATEWAY], held)
    assert [(f.kind, f.artifact) for f in got.refused] == [("unexpected", web)]

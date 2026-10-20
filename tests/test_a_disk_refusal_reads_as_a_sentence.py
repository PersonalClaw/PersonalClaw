"""A download refused for disk space reads as a sentence, in the unit the offer states its size in,
and a disk that cannot be measured is said to be one.

After #3706 the first model download on a fresh home is checked for free space before it starts,
which left four things a user could see wrong with what they were told:

* **The machine prefix.** The route answered ``{"error": "insufficient_disk_space: needs …"}`` —
  a code and a sentence run together in one string, from before the platform envelope — so the
  onboarding card and the chat notice printed "insufficient_disk_space:" in front of the sentence.
* **The warning nobody saw.** When free space could not be measured the download still started
  (blocking a good download on a failed probe is the worse error) and the 202 carried a
  ``warning``. Nothing kept it: the job had no field for it, so the progress stream and a reload's
  list lost it, and the web client dropped it from the 202 as well.
* **Two unit systems for one size.** The card offers "138 MiB"; the refusal under it said
  "138.1 MB" — the same binary number, labelled two ways. So did the Models page, whose rows state
  MiB and whose fit chips said "needs ~4.2 GB".
* **"(0 MB free)".** The uploads store turned a disk it could not measure into 0 bytes free and
  refused the upload with "(0 MB free)", a number nobody measured.
"""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest
import test_model_fit_payload as _payload

from personalclaw.dashboard import model_downloads as M
from personalclaw.dashboard.handlers import model_downloads as H
from personalclaw.local_models import fit
from personalclaw.local_models.provider import LocalModel

# The #3706 download harness: no bytes move, the catalog is wired per test.
_download_env = _payload._download_env
_req = _payload._req
_settle = _payload._settle
_free_after_the_real_probe = _payload._free_after_the_real_probe

_MB = 1024 * 1024
_GB = 1024 * _MB


async def _start(reg, model: str):
    return await H.api_model_download_start(
        _req("POST", "/api/models/downloads", reg, body={"provider": "ollama", "model": model})
    )


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


# ── 242a. The refusal is the envelope's code and a sentence ──────────────────────────────────


@pytest.mark.asyncio
async def test_a_download_that_cannot_land_is_refused_with_a_code_and_a_sentence(
    _download_env, monkeypatch, tmp_path
) -> None:
    target = tmp_path / "home" / "models" / "bundled-chat"
    monkeypatch.setattr(shutil, "disk_usage", _free_after_the_real_probe(50 * _MB))
    _download_env(cache_dir=str(target), models=[LocalModel(name="small", size_mb=138)])
    reg = M.ModelDownloadRegistry()

    resp = await _start(reg, "small")

    assert resp.status == 400
    assert _body(resp) == {
        "error": {
            "code": "insufficient_disk_space",
            "message": (
                "Not enough free disk space for this download: it needs 138.0 MiB, "
                "and 50.0 MiB is free."
            ),
        }
    }
    assert reg.list() == []


@pytest.mark.asyncio
async def test_the_routes_other_refusals_carry_a_code_too(_download_env, tmp_path) -> None:
    """One route, one error shape: the flat answers beside the disk refusal are enveloped too."""
    _download_env(cache_dir=str(tmp_path), models=[LocalModel(name="good", size_mb=10)])
    reg = M.ModelDownloadRegistry()
    req = _req("POST", "/api/models/downloads", reg, body={"provider": "ollama", "model": "good"})

    async def _broken_json():
        raise ValueError("not JSON")

    req.json = _broken_json  # type: ignore[assignment]
    bad_json = _body(await H.api_model_download_start(req))
    listed = _body(
        await H.api_model_download_start(
            _req("POST", "/api/models/downloads", reg, body=["provider"])
        )
    )
    missing = await H.api_model_download_start(
        _req("POST", "/api/models/downloads", reg, body={"provider": "", "model": "good"})
    )

    assert bad_json["error"]["code"] == "invalid_json"
    assert listed["error"]["code"] == "invalid_body"
    assert missing.status == 400
    assert _body(missing)["error"] == {
        "code": "invalid_request",
        "message": "Missing 'provider'",
    }


def test_the_code_is_registered() -> None:
    from personalclaw.http_errors import HTTP_ERROR_CODES

    assert "insufficient_disk_space" in HTTP_ERROR_CODES


# ── 242b. The warning rides the job ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_download_on_an_unmeasurable_disk_keeps_its_warning_on_the_job(
    _download_env, tmp_path
) -> None:
    """The job carries it, so its stream and a reload's list say it too — not only the 202."""
    (tmp_path / "models").symlink_to(tmp_path / "unplugged-drive")
    _download_env(
        cache_dir=str(tmp_path / "models" / "ollama"),
        models=[LocalModel(name="good", size_mb=10)],
    )
    reg = M.ModelDownloadRegistry()

    resp = await _start(reg, "good")

    assert resp.status == 202
    started = _body(resp)
    assert started["warning"] == (
        "Free space could not be checked, so the download was not verified to fit."
    )
    # A reload re-attaches from the list, and the stream opens with the job's snapshot.
    listed = _body(await H.api_model_downloads_list(_req("GET", "/api/models/downloads", reg)))
    assert [j["warning"] for j in listed["downloads"]] == [started["warning"]]
    assert reg.get(started["id"]).to_dict()["warning"] == started["warning"]
    await _settle()


@pytest.mark.asyncio
async def test_a_download_the_check_passed_has_no_warning(_download_env, tmp_path) -> None:
    _download_env(cache_dir=str(tmp_path), models=[LocalModel(name="good", size_mb=10)])
    reg = M.ModelDownloadRegistry()
    started = _body(await _start(reg, "good"))
    assert started["warning"] == ""
    await _settle()


# ── 242c. One unit system for a model's size ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("count", "said"),
    [
        (144_811_072, "138.1 MiB"),  # the bundled chat model, as the offer card's 138 MiB
        (50 * _MB, "50.0 MiB"),
        (int(4.2 * _GB), "4.2 GiB"),
        (512 * 1024, "512.0 KiB"),
    ],
)
def test_a_model_size_is_said_in_binary_units_labelled_so(count, said) -> None:
    assert fit.size_text(count) == said


def test_the_fit_reasons_state_sizes_the_way_the_rows_do() -> None:
    red = fit.fit_verdict(size_mb=4300, budget_bytes=3 * _GB)
    yellow = fit.fit_verdict(size_mb=2900, budget_bytes=3 * _GB)
    green = fit.fit_verdict(size_mb=138, budget_bytes=3 * _GB)
    assert red.reason == "needs ~4.2 GiB, this machine has ~3.0 GiB free for models"
    assert yellow.reason == "fits, but uses most of the ~3.0 GiB available"
    assert green.reason == "fits comfortably in ~3.0 GiB"


# ── 242d. An unmeasurable disk is said to be one ─────────────────────────────────────────────


def test_an_upload_on_a_disk_that_cannot_be_measured_says_so(tmp_path, monkeypatch) -> None:
    from personalclaw.uploads import store as S

    def _unmeasurable(path):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(shutil, "disk_usage", _unmeasurable)
    uploads = S.UploadStore(root=tmp_path / "uploads")
    with pytest.raises(S.UploadError) as refused:
        uploads.init(filename="notes.txt", mime="text/plain", size=10 * _MB, target="attachment")
    assert refused.value.status == 507
    assert refused.value.message == (
        "The free space on the disk uploads are saved to could not be measured, so the upload "
        "was not started."
    )
    assert "0 MB free" not in refused.value.message


def test_an_upload_that_does_not_fit_still_names_both_numbers(tmp_path, monkeypatch) -> None:
    from personalclaw.uploads import store as S

    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=1 * _GB, used=900 * _MB, free=100 * _MB),
    )
    uploads = S.UploadStore(root=tmp_path / "uploads")
    with pytest.raises(S.UploadError) as refused:
        uploads.init(filename="notes.txt", mime="text/plain", size=10 * _MB, target="attachment")
    assert refused.value.message == "not enough free disk to receive 10 MB (100 MB free)"

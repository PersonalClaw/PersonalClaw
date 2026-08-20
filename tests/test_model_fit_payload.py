"""LMMV-8 — the fit answer as the HTTP surface serves it (payload + pre-download check).

These tests drive the HANDLERS, not :mod:`personalclaw.local_models.fit`. The module's own
arithmetic is covered by ``test_local_model_fit.py``; what is at stake here is the WIRING:
that ``GET /api/models/available`` carries the one budget and a per-row verdict, that an
unmeasured host reports ``null`` rather than ``0``, that a family quotes its MEDIAN variant
(a chip must not promise a fit the smallest variant would flatter into existence), and that
``POST /api/models/downloads`` refuses a download that cannot land while still allowing one
whose filesystem simply could not be measured.

Nothing here touches the real home: ``PERSONALCLAW_HOME`` points at ``tmp_path`` and every
catalog / host probe is stubbed.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard import model_downloads as M
from personalclaw.dashboard.handlers import model_downloads as H
from personalclaw.dashboard.handlers import model_registry as R
from personalclaw.local_models import fit
from personalclaw.local_models import registry as LR
from personalclaw.local_models.provider import LocalModel

_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    """Never the real ``~/.personalclaw``: every config/home read lands in tmp_path."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    fit.reset_gpu_probe_cache()
    yield
    fit.reset_gpu_probe_cache()


# ── GET /api/models/available ──


class _Prov:
    """A local-model provider stub — only what the payload handler reads off it."""

    display_name = "Ollama"
    searchable = True

    def __init__(self, cache_dir: str | None = None) -> None:
        self._cache_dir = cache_dir

    def cache_dir(self) -> str | None:
        return self._cache_dir


class _ConfigCatalog:
    """A config-provider catalog (OpenAI-shaped): remote models, no local weights."""

    async def list_models(self):
        class _MI:
            def to_dict(self):
                # A remote model may still declare a size; it must STILL get no fit chip,
                # because nothing is downloaded and no memory budget applies to it.
                return {"id": "gpt-x", "name": "gpt-x", "size_mb": 999_999}

        return [_MI()]


def _catalog(*models: LocalModel):
    async def _list(provider):
        return list(models)

    return _list


def _wire_payload(monkeypatch, *, host: fit.HostCapacity, models, hide_unrunnable=True):
    """Point the payload handler at a stub catalog and a KNOWN host — one probe, one budget."""
    monkeypatch.setattr(
        R, "_get_providers_from_config", lambda: [{"name": "openai", "type": "openai"}]
    )
    monkeypatch.setattr(R, "_catalog_for_config_provider", lambda p: _ConfigCatalog())

    async def _none():
        return []

    monkeypatch.setattr(R, "_discover_image_gen_models", _none)
    monkeypatch.setattr(R, "_discover_video_gen_models", _none)

    prov = _Prov()
    monkeypatch.setattr(LR, "get_provider", lambda name: prov if name == "ollama" else None)
    monkeypatch.setattr(LR, "registered", lambda: [("ollama", prov)])
    monkeypatch.setattr(LR, "catalog_for", _catalog(*models))

    monkeypatch.setattr(fit, "host_capacity", lambda target_dir=None: host)
    monkeypatch.setattr(fit, "configured_reserve_gb", lambda: 0.0)
    monkeypatch.setattr(fit, "hide_unrunnable_default", lambda: hide_unrunnable)


async def _available() -> dict:
    resp = await R.api_models_available(make_mocked_request("GET", "/api/models/available"))
    return json.loads(resp.body.decode())


def _rows(payload: dict, provider: str) -> dict[str, dict]:
    for card in payload["providers"]:
        if card["name"] == provider:
            return {m["name"]: m for m in card["models"]}
    raise AssertionError(f"no {provider!r} card in {[c['name'] for c in payload['providers']]}")


_MEASURED_8GB = fit.HostCapacity(
    total_ram_bytes=8 * _GB,
    memory_measured=True,
    unified_memory=True,
    gpu_model="Apple M2",
)

#: One family with four variants (median 6000, minimum 2000 — deliberately far apart) plus a
#: colonless model, which is a family of one.
_FAMILY = [
    LocalModel(name="qwen3:2b", size_mb=2000),
    LocalModel(name="qwen3:4b", size_mb=4000),
    LocalModel(name="qwen3:6b", size_mb=6000),
    LocalModel(name="qwen3:16b", size_mb=16000),
    LocalModel(name="piper-en", size_mb=60),
]


@pytest.mark.asyncio
async def test_payload_carries_the_fit_block(monkeypatch):
    """The response reports the ONE budget every row was judged against."""
    _wire_payload(monkeypatch, host=_MEASURED_8GB, models=_FAMILY, hide_unrunnable=False)
    payload = await _available()

    block = payload["fit"]
    assert block["budget_mb"] == 8 * 1024  # 8 GiB, reserve 0
    assert block["total_ram_mb"] == 8 * 1024
    assert block["unified_memory"] is True
    assert block["gpu_model"] == "Apple M2"
    assert block["measured"] is True
    assert block["hide_unrunnable"] is False


@pytest.mark.asyncio
async def test_hide_unrunnable_comes_from_config(monkeypatch):
    """The browse filter's default is the config-backed one, not a hardcoded True."""
    _wire_payload(monkeypatch, host=_MEASURED_8GB, models=_FAMILY, hide_unrunnable=True)
    assert (await _available())["fit"]["hide_unrunnable"] is True


@pytest.mark.asyncio
async def test_local_rows_carry_a_verdict_reason_and_need(monkeypatch):
    """Each local row: a traffic light, the reason behind it, and the bytes it needs."""
    _wire_payload(monkeypatch, host=_MEASURED_8GB, models=_FAMILY, hide_unrunnable=True)
    rows = _rows(await _available(), "ollama")

    assert rows["qwen3:2b"]["fit"] == "green"
    assert rows["qwen3:6b"]["fit"] == "yellow"  # fits, but eats most of the 8 GB
    assert rows["qwen3:16b"]["fit"] == "red"

    # The verdict is judged against the weights the row actually pulls, so the family's
    # largest variant is NOT painted with the median's verdict.
    assert rows["qwen3:16b"]["fit_need_mb"] == pytest.approx(16000, rel=0.01)
    assert rows["qwen3:2b"]["fit_need_mb"] == pytest.approx(2000, rel=0.01)

    for name in ("qwen3:2b", "qwen3:6b", "qwen3:16b", "piper-en"):
        assert rows[name]["fit_reason"], f"{name} has a verdict with no reason"
    assert "8.0 GB" in rows["qwen3:16b"]["fit_reason"]  # the refusal names the capacity


@pytest.mark.asyncio
async def test_quoted_size_is_the_family_median_not_its_minimum(monkeypatch):
    """A family quotes its MEDIAN variant — quoting the smallest flatters the family."""
    _wire_payload(monkeypatch, host=_MEASURED_8GB, models=_FAMILY, hide_unrunnable=True)
    rows = _rows(await _available(), "ollama")

    smallest = min(m.size_mb for m in _FAMILY if m.name.startswith("qwen3:"))
    median = 6000.0
    for name in ("qwen3:2b", "qwen3:4b", "qwen3:6b", "qwen3:16b"):
        assert rows[name]["quoted_size_mb"] == median, name
        assert rows[name]["quoted_size_mb"] != smallest, name

    # A colonless name is a family of one: its quote is its own size, unchanged.
    assert rows["piper-en"]["quoted_size_mb"] == 60.0


@pytest.mark.asyncio
async def test_red_row_steps_down_to_the_largest_variant_that_fits(monkeypatch):
    """The panel is handed a variant that loads instead of one that OOMs."""
    _wire_payload(monkeypatch, host=_MEASURED_8GB, models=_FAMILY, hide_unrunnable=True)
    rows = _rows(await _available(), "ollama")

    assert rows["qwen3:16b"]["fit_step_down"] == "qwen3:6b"
    # A row that already fits has nowhere to step down to.
    assert rows["qwen3:2b"]["fit_step_down"] is None
    assert rows["piper-en"]["fit_step_down"] is None


@pytest.mark.asyncio
async def test_unmeasured_host_reports_null_budget_never_zero(monkeypatch):
    """``null`` and ``0`` are different answers; only one of them hides every model."""
    unmeasured = fit.HostCapacity(total_ram_bytes=0, memory_measured=False)
    _wire_payload(monkeypatch, host=unmeasured, models=_FAMILY, hide_unrunnable=True)
    payload = await _available()

    assert payload["fit"]["budget_mb"] is None
    assert payload["fit"]["budget_mb"] != 0
    assert payload["fit"]["measured"] is False

    rows = _rows(payload, "ollama")
    for name, row in rows.items():
        assert row["fit"] == "unknown", name
        assert "could not be measured" in row["fit_reason"], name
        assert row["fit_step_down"] is None, name


@pytest.mark.asyncio
async def test_rows_without_local_weights_get_no_fit_fields(monkeypatch):
    """A config-provider row carries NO fit fields — an absent field means "no chip"."""
    _wire_payload(monkeypatch, host=_MEASURED_8GB, models=_FAMILY, hide_unrunnable=True)
    rows = _rows(await _available(), "openai")

    row = rows["gpt-x"]
    for key in ("fit", "fit_reason", "fit_need_mb", "quoted_size_mb", "fit_step_down"):
        assert key not in row, f"remote row grew a {key}"


@pytest.mark.asyncio
async def test_host_is_probed_once_per_request_not_once_per_model(monkeypatch):
    """One probe for the whole response: a per-model probe is a per-model answer."""
    calls: list[int] = []

    def _probe(target_dir=None):
        calls.append(1)
        return _MEASURED_8GB

    _wire_payload(monkeypatch, host=_MEASURED_8GB, models=_FAMILY, hide_unrunnable=True)
    monkeypatch.setattr(fit, "host_capacity", _probe)

    await _available()
    assert len(calls) == 1, f"probed {len(calls)} times for {len(_FAMILY)} models"


# ── POST /api/models/downloads ──


def _req(method, path, reg, *, body=None, match_info=None):
    app = web.Application()

    class _State:
        def model_downloads(self):
            return reg

    app["state"] = _State()
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app)
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


@pytest.fixture
def _download_env(monkeypatch):
    """Stub the download runner so no bytes move, and hand out a catalog wiring hook."""
    monkeypatch.setattr(M, "_provider", lambda name: object())
    monkeypatch.setattr(M, "_model_exists", lambda provider, model: True)
    monkeypatch.setattr(M, "_expected_size_bytes", lambda provider, model: 4 * _MB)
    monkeypatch.setattr(M, "_is_downloaded", lambda provider, model: False)
    monkeypatch.setattr(M, "_dir_size", lambda path: 0)

    async def _fetch(provider, model):
        return None

    monkeypatch.setattr(M, "_run_fetch", _fetch)

    def _wire(*, cache_dir, models):
        prov = _Prov(cache_dir)
        monkeypatch.setattr(LR, "get_provider", lambda name: prov if name == "ollama" else None)
        monkeypatch.setattr(LR, "catalog_for", _catalog(*models))

    return _wire


async def _settle():
    for _ in range(10):
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_download_refused_when_the_weights_cannot_land(_download_env, tmp_path):
    """The refusal names BOTH numbers: what it needs and what is free."""
    _download_env(
        cache_dir=str(tmp_path),
        models=[LocalModel(name="huge", size_mb=10_000_000)],  # ~10 TB
    )
    reg = M.ModelDownloadRegistry()
    resp = await H.api_model_download_start(
        _req("POST", "/api/models/downloads", reg, body={"provider": "ollama", "model": "huge"})
    )

    assert resp.status == 400
    error = json.loads(resp.body.decode())["error"]
    assert error.startswith("insufficient_disk_space:")
    both = re.search(r"needs ([\d.]+) GB, ([\d.]+) GB free", error)
    assert both, error
    assert float(both.group(1)) > float(both.group(2))
    # Refused BEFORE the fetch: no job was created for it.
    assert reg.list() == []


@pytest.mark.asyncio
async def test_unmeasurable_disk_allows_the_download_and_warns(_download_env, tmp_path):
    """A probe that could not measure the filesystem must not block a good download."""
    _download_env(
        cache_dir=str(tmp_path / "does" / "not" / "exist"),
        models=[LocalModel(name="good", size_mb=10)],
    )
    reg = M.ModelDownloadRegistry()
    resp = await H.api_model_download_start(
        _req("POST", "/api/models/downloads", reg, body={"provider": "ollama", "model": "good"})
    )

    assert resp.status == 202
    payload = json.loads(resp.body.decode())
    assert payload["model"] == "good"
    assert "could not be checked" in payload["warning"]
    await _settle()


@pytest.mark.asyncio
async def test_measurable_disk_with_room_starts_clean(_download_env, tmp_path):
    """The happy path carries no warning — the check ran and passed."""
    _download_env(cache_dir=str(tmp_path), models=[LocalModel(name="good", size_mb=10)])
    reg = M.ModelDownloadRegistry()
    resp = await H.api_model_download_start(
        _req("POST", "/api/models/downloads", reg, body={"provider": "ollama", "model": "good"})
    )

    assert resp.status == 202
    assert "warning" not in json.loads(resp.body.decode())
    await _settle()


@pytest.mark.asyncio
async def test_already_downloaded_model_is_never_refused_for_space(_download_env, tmp_path):
    """Nothing lands for a model already on disk, so nothing may be refused."""
    _download_env(
        cache_dir=str(tmp_path),
        models=[LocalModel(name="huge", size_mb=10_000_000, downloaded=True)],
    )
    reg = M.ModelDownloadRegistry()
    resp = await H.api_model_download_start(
        _req("POST", "/api/models/downloads", reg, body={"provider": "ollama", "model": "huge"})
    )

    assert resp.status == 202
    await _settle()

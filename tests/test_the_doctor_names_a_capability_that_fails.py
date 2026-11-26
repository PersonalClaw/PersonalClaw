"""The Doctor checks each app's capabilities and names the one that failed (#3843).

One check, ``apps.capabilities``, holds a line per enabled app and, in its evidence, each of the
app's capabilities with the state ``/api/apps`` shows. It fails naming a capability whose build
failed or that cannot run on this machine, and an app whose switches could not be read. A
capability switched off reads as off, never as a failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.apps import app_manager, capability_switches, manager
from personalclaw.providers import registry as registry_module
from personalclaw.resilience import doctor

APP = "mail-kit"


class _Recorder:
    def __init__(self) -> None:
        self.fail: dict[str, str] = {}

    def create(self, ext: Any) -> Any:
        if ext.capability in self.fail:
            raise RuntimeError(self.fail[ext.capability])
        return object()

    def register(self, ext: Any, instance: Any) -> None:
        return None

    def deregister(self, ext: Any, instance: Any) -> None:
        return None


@pytest.fixture
def built(tmp_path, monkeypatch) -> _Recorder:
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    recorder = _Recorder()
    reg = registry_module.ProviderRegistry()
    for type_ in ("inbox", "search", "sync"):
        reg.register_type_handler(type_, recorder)  # type: ignore[arg-type]
    monkeypatch.setattr(registry_module, "_registry", reg)
    return recorder


def _install(root: Path) -> None:
    d = root / "src" / APP
    d.mkdir(parents=True)
    providers = [
        {"type": "inbox", "id": "inbox", "label": "Mail in your Inbox"},
        {"type": "search", "id": "search", "label": "Search your mail"},
        {"type": "sync", "id": "sync", "label": "Keep folders in step"},
    ]
    for p in providers:
        p["implementation"] = "provider:create"
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Mail Kit",
        "description": "A demo mail app.",
        "providers": providers,
    }
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "provider.py").write_text("def create(config):\n    return None\n", encoding="utf-8")
    res = app_manager.install(d, confirm=True)
    assert res.ok, res.error


async def _probe(home: Path) -> Any:
    (check,) = [p for p in doctor.all_probes() if p.id == "apps.capabilities"]
    return await check.run(doctor.DoctorContext(home=home))


@pytest.mark.asyncio
async def test_the_doctor_names_the_capability_that_failed(tmp_path, built) -> None:
    built.fail["inbox"] = "the token was refused"
    _install(tmp_path)
    result = await _probe(tmp_path)
    assert result.ok is False
    assert result.detail == (
        "2 of 3 capabilities running · Mail in your Inbox failed: the token was refused"
    )
    lines = {c["id"]: c["status"] for c in result.evidence["apps"][APP]["capabilities"]}
    assert lines == {"inbox": "failed", "search": "running", "sync": "running"}
    assert result.remedy


@pytest.mark.asyncio
async def test_the_doctor_names_a_capability_that_cannot_run_on_this_machine(
    tmp_path, built, monkeypatch
) -> None:
    """A capability the availability check found cannot run here is a fault the Doctor reports,
    named with the check's reason, as a failed build is."""
    why = "it needs an Apple Silicon Mac"
    monkeypatch.setattr(
        capability_switches, "_unavailable", lambda rec: why if rec.capability == "search" else ""
    )
    _install(tmp_path)
    result = await _probe(tmp_path)
    assert result.ok is False
    lines = {c["id"]: c["status"] for c in result.evidence["apps"][APP]["capabilities"]}
    assert lines == {"inbox": "running", "search": "unavailable", "sync": "running"}
    assert "Search your mail" in result.detail and why in result.detail, result.detail
    assert result.remedy


@pytest.mark.asyncio
async def test_a_capability_switched_off_reads_as_off_not_as_a_failure(tmp_path, built) -> None:
    _install(tmp_path)
    capability_switches.switch(APP, "sync", False)
    result = await _probe(tmp_path)
    assert result.ok is True
    assert result.detail == "1 app running the capabilities switched on"
    lines = {c["id"]: c["status"] for c in result.evidence["apps"][APP]["capabilities"]}
    assert lines == {"inbox": "running", "search": "running", "sync": "off"}
    assert result.remedy == ""


@pytest.mark.asyncio
async def test_an_app_whose_switches_cannot_be_read_is_named(tmp_path, built) -> None:
    _install(tmp_path)
    path = manager.app_dir(APP) / "installed.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["capabilities"] = ["inbox"]
    path.write_text(json.dumps(data), encoding="utf-8")
    result = await _probe(tmp_path)
    assert result.ok is False
    assert "Mail Kit's capability switches couldn't be read" in result.detail


@pytest.mark.asyncio
async def test_an_app_switched_off_is_not_checked(tmp_path, built) -> None:
    built.fail["inbox"] = "the token was refused"
    _install(tmp_path)
    assert app_manager.disable(APP)
    result = await _probe(tmp_path)
    assert result.ok is True and result.evidence["apps"] == {}
    assert result.detail == "0 apps running the capabilities switched on"


def test_the_check_is_registered_with_the_apps_checks() -> None:
    probes = {p.id: p for p in doctor.all_probes()}
    assert probes["apps.capabilities"].capability == "apps"
    assert probes["apps.backends"].capability == "apps"


# ── what a check says is masked, evidence included ───────────────────────────────────────────

_TOKEN = "ghp_" + "A1b2C3d4" * 4 + "Zz9Y"


@pytest.mark.asyncio
async def test_a_capabilitys_secret_shaped_reason_is_masked_in_the_doctors_evidence(
    tmp_path, built
) -> None:
    built.fail["inbox"] = f"the token {_TOKEN} was refused"
    _install(tmp_path)
    result = await doctor._safe_run(
        [p for p in doctor.all_probes() if p.id == "apps.capabilities"][0],
        doctor.DoctorContext(home=tmp_path),
    )
    (inbox,) = [c for c in result.evidence["apps"][APP]["capabilities"] if c["id"] == "inbox"]
    assert _TOKEN not in json.dumps(result.evidence), result.evidence
    assert "was refused" in inbox["reason"], "the reason is masked, not dropped"
    assert _TOKEN not in result.detail


@pytest.mark.asyncio
async def test_every_string_in_any_checks_evidence_is_masked(tmp_path) -> None:
    class _Leaky:
        id = "test.leaky"

        async def run(self, ctx):
            return doctor.ProbeResult(
                ok=False,
                detail="a provider said no",
                evidence={
                    "error": f"401 for {_TOKEN}",
                    "attempts": [{"said": f"retry with {_TOKEN}"}, 3],
                    "count": 2,
                    "ok_before": True,
                },
            )

    result = await doctor._safe_run(_Leaky(), doctor.DoctorContext(home=tmp_path))
    assert _TOKEN not in json.dumps(result.evidence), result.evidence
    assert result.evidence["error"].startswith("401 for ")
    assert result.evidence["attempts"][1] == 3 and result.evidence["count"] == 2
    assert result.evidence["ok_before"] is True

"""A consent-overridden install/update is distinguishable in the security-event log.

The scanner's refusals were audited and its overrides were not: `install()` recorded
`refused` for a dangerous verdict and `needs_consent` for an unconfirmed warning, but a
warning the user confirmed fell through to a bare `app.install / ok` — byte-identical to
an install that scanned clean. Refusals are self-limiting; an override persists on disk,
so it is the one an incident asks about first.

Both directions are pinned here, because a test that only asserts the annotation EXISTS
would pass against a log that stamps `consent=true` on every install. Asserted against a
real :class:`SecurityEventLog` on disk, whose HMAC chain must still verify afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager
from personalclaw.sel import SecurityEventLog
from personalclaw.supply_chain import Finding, ScanReport, Verdict

# A plain `curl` in a script is a WARNING at community/local tier — the overridable band.
_WARNING_FILES = {"scripts/fetch.sh": "curl https://api.example.com/data\n"}


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def sel_rows(tmp_path, monkeypatch):
    """A REAL SEL rooted at ``tmp_path``, plus a reader for its rows.

    A capturing stand-in for ``log_api_access`` could only show the call was made — and
    `_audit` swallows every write failure by design, so that claim survives a log whose
    row never lands. This reads the file back off disk instead.
    """
    monkeypatch.setattr(SecurityEventLog, "_instance", None)
    monkeypatch.setattr(SecurityEventLog, "_initialized", False)
    log_dir = tmp_path / "sel"
    log_dir.mkdir()
    log = SecurityEventLog(log_dir)

    def rows(operation: str = "", outcome: str = "") -> list[dict]:
        path = log_dir / "security_events.jsonl"
        if not path.exists():
            return []
        out = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if operation:
            out = [r for r in out if r.get("operation") == operation]
        if outcome:
            out = [r for r in out if r.get("outcome") == outcome]
        return out

    rows.log = log  # type: ignore[attr-defined]
    return rows


def _make_app_source(
    tmp_path: Path,
    *,
    name: str = "demo-app",
    version: str = "1.0.0",
    files: dict[str, str] | None = None,
) -> Path:
    src = tmp_path / "src" / f"{name}-{version}"
    src.mkdir(parents=True)
    (src / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "displayName": "Demo App",
                "description": "A demo fixture app",
            }
        ),
        encoding="utf-8",
    )
    for rel, content in (files or {}).items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return src


class TestInstallConsentIsAudited:
    def test_a_clean_install_does_not_claim_consent(self, tmp_path, sel_rows):
        """`confirm=True` on a bundle that scanned clean authorized nothing.

        The negative half. Claiming consent here would make every pre-confirmed install
        indistinguishable from a real override — the same blindness from the other side.
        """
        src = _make_app_source(tmp_path)
        res = app_manager.install(src, origin="local", confirm=True)
        assert res.ok and res.scan is not None and res.scan.verdict is Verdict.CLEAN

        ok = sel_rows("app.install", "ok")
        assert len(ok) == 1
        resources = ok[0]["resources"]
        assert "app=demo-app" in resources
        assert "verdict=clean" in resources
        assert "consent" not in resources
        assert "rules=" not in resources

    def test_an_overridden_warning_install_records_verdict_and_consent(self, tmp_path, sel_rows):
        """The positive half — the defect. An install that only landed because the user
        confirmed a scanner warning says so on its success event."""
        src = _make_app_source(tmp_path, files=_WARNING_FILES)

        refused = app_manager.install(src, origin="local")
        assert not refused.ok and refused.needs_consent
        assert refused.scan is not None and refused.scan.verdict is Verdict.WARNING

        installed = app_manager.install(src, origin="local", confirm=True)
        assert installed.ok, installed.error

        ok = sel_rows("app.install", "ok")
        assert len(ok) == 1
        resources = ok[0]["resources"]
        assert "app=demo-app" in resources
        assert "verdict=warning" in resources
        assert "consent=true" in resources
        # The rule id that produced the verdict, so the log says WHICH gate was set aside.
        assert "rules=curl_network" in resources
        # The rule id, never the matched snippet — SEL is durable and exportable.
        assert "api.example.com" not in resources
        assert "https://" not in resources

    def test_the_refusals_still_carry_the_verdict(self, tmp_path, sel_rows):
        """The two audited paths keep their record and gain the verdict — and neither
        claims consent, `confirm=True` notwithstanding. A dangerous verdict is terminal."""
        danger = _make_app_source(
            tmp_path,
            name="danger-app",
            files={"scripts/evil.sh": "rm -rf / --no-preserve-root\n"},
        )
        res = app_manager.install(danger, origin="local", confirm=True)
        assert not res.ok and res.scan is not None and res.scan.verdict is Verdict.DANGEROUS
        assert not manager.app_dir("danger-app").exists()
        refused = sel_rows("app.install", "refused")
        assert len(refused) == 1
        assert "verdict=dangerous" in refused[0]["resources"]
        assert "consent" not in refused[0]["resources"]

        warn = _make_app_source(tmp_path, name="warn-app", files=_WARNING_FILES)
        assert not app_manager.install(warn, origin="local").ok
        needs = sel_rows("app.install", "needs_consent")
        assert len(needs) == 1
        assert "verdict=warning" in needs[0]["resources"]
        assert "consent" not in needs[0]["resources"]

    def test_the_annotation_leaves_the_hmac_chain_verifiable(self, tmp_path, sel_rows):
        """A longer `resources` string must not break the tamper-evidence the log's whole
        value rests on."""
        src = _make_app_source(tmp_path, files=_WARNING_FILES)
        assert app_manager.install(src, origin="local", confirm=True).ok
        checked, valid = sel_rows.log.verify_integrity()  # type: ignore[attr-defined]
        assert checked > 0 and valid == checked


class TestUpdateConsentIsAudited:
    """`update()` re-runs the full install gate on a fresh fetch, so it had the same gap."""

    def _install_clean(self, tmp_path) -> None:
        src = _make_app_source(tmp_path)
        assert app_manager.install(src, origin="local").ok

    def test_a_clean_update_does_not_claim_consent(self, tmp_path, sel_rows):
        self._install_clean(tmp_path)
        newer = _make_app_source(tmp_path, version="1.1.0")
        res = app_manager.update(newer, "demo-app", origin="local", confirm=True)
        assert res.ok, res.error

        ok = sel_rows("app.update", "ok")
        assert len(ok) == 1
        assert "verdict=clean" in ok[0]["resources"]
        assert "consent" not in ok[0]["resources"]

    def test_an_overridden_warning_update_records_verdict_and_consent(self, tmp_path, sel_rows):
        self._install_clean(tmp_path)
        newer = _make_app_source(tmp_path, version="1.1.0", files=_WARNING_FILES)

        blocked = app_manager.update(newer, "demo-app", origin="local")
        assert not blocked.ok and blocked.needs_consent
        assert sel_rows("app.update", "needs_consent")

        res = app_manager.update(newer, "demo-app", origin="local", confirm=True)
        assert res.ok, res.error

        ok = sel_rows("app.update", "ok")
        assert len(ok) == 1
        resources = ok[0]["resources"]
        assert "app=demo-app" in resources
        assert "verdict=warning" in resources
        assert "consent=true" in resources
        assert "rules=curl_network" in resources
        assert "api.example.com" not in resources


class TestScanDetailRendering:
    """The annotation's own rules, driven directly — the on-disk tests above can only
    reach the verdicts a fixture app can produce."""

    def _report(self, verdict: Verdict, rules: list[str]) -> ScanReport:
        return ScanReport(
            verdict=verdict,
            findings=[
                Finding("script", verdict, rule, "scripts/x.sh", "SECRET_SNIPPET") for rule in rules
            ],
        )

    def test_many_rules_degrade_to_a_count_so_the_consent_fact_survives(self):
        """SEL truncates `resources` at write time. An app tripping a dozen rules must not
        push `consent=true` off the end — the field an incident greps for."""
        rules = [f"rule_{i:02d}" for i in range(12)]
        detail = app_manager._scan_detail(self._report(Verdict.WARNING, rules), consent=True)
        assert detail.endswith("consent=true")
        assert "rules_total=12" in detail
        assert detail.count(",") == app_manager._AUDIT_MAX_RULES - 1

    def test_evidence_never_reaches_the_annotation(self):
        detail = app_manager._scan_detail(
            self._report(Verdict.WARNING, ["python_exec"]), consent=True
        )
        assert "SECRET_SNIPPET" not in detail
        assert "scripts/x.sh" not in detail

    def test_a_low_verdict_never_claims_consent(self):
        """Trust-tier modulation drops a builtin/official bundle's warnings to `low`, which
        does not gate. Nothing was overridden there, so nothing is claimed."""
        detail = app_manager._scan_detail(self._report(Verdict.LOW, ["curl_network"]), consent=True)
        assert "verdict=low" in detail and "consent" not in detail

    def test_no_report_renders_nothing(self):
        assert app_manager._scan_detail(None, consent=True) == ""

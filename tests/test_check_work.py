"""HC-4 — check-work: the bundled skill, the shared derivation core, the SDLC post-gate
hook and the chat offer chip.

The adversarial case is the point of this file: a session that CLAIMS two files and a
clean command must produce a report with zero self-reported passes — every pass carries
an observed line, the planted flaw fails, and the command nobody ran comes back
``unverifiable`` rather than green.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest

from personalclaw.check_work import (
    MAX_CHECKS,
    derive_and_run,
    derive_checks,
    reconstruct_claims,
    render_report,
    run_checks,
)


# ── The skill itself: discovered, triggerable, packaged ────────────────────────────
class TestCheckWorkSkillDiscovery:
    def test_discovered_by_native_marketplace(self):
        from personalclaw.skills.native import NativeSkillsMarketplace

        detail = NativeSkillsMarketplace().fetch("check-work")
        paths = {f["path"] for f in detail.files}
        assert "SKILL.md" in paths

    def test_frontmatter_single_line_description_and_triggers(self):
        from personalclaw.skills.marketplace import _parse_description
        from personalclaw.skills.native import _bundled_root

        skill = _bundled_root() / "check-work" / "SKILL.md"
        md = skill.read_text(encoding="utf-8")
        desc = _parse_description(skill)
        assert desc and "\n" not in desc
        assert "triggers: " in md  # the loader's trigger contract (comma-separated)

    def test_triggered_on_check_your_work(self, tmp_path, monkeypatch):
        from personalclaw.skills.loader import SkillsLoader

        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        loader = SkillsLoader(skills_path=tmp_path / "skills")
        assert "check-work" in {s["key"] for s in loader.list_skills()}
        assert "check-work" in loader.get_triggered_skills("check your work please")
        assert "check-work" not in loader.get_triggered_skills("what is the weather today")

    def test_qa_boundary_doc_present_and_referenced(self):
        from personalclaw.skills.native import _bundled_root

        root = _bundled_root() / "check-work"
        doc = root / "references" / "qa-boundary.md"
        assert doc.is_file(), "the light-vs-deep QA boundary doc must ship with the skill"
        text = doc.read_text(encoding="utf-8")
        assert "check-work" in text and "deep" in text.lower()
        # A referenced file that isn't there is worse than no reference.
        skill_md = (root / "SKILL.md").read_text(encoding="utf-8")
        for ref in re.findall(r"`(references/[\w./-]+)`", skill_md):
            assert (root / ref).is_file(), f"SKILL.md points at a missing file: {ref}"

    def test_packaged_in_the_wheel(self):
        """Both the SKILL.md glob and the references glob must be in package-data, or the
        wheel ships a skill whose own text dangles."""
        import pathlib
        import tomllib

        root = pathlib.Path(__file__).resolve().parents[1]
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        globs = data["tool"]["setuptools"]["package-data"]["personalclaw"]
        assert "skills/bundled/*/SKILL.md" in globs
        assert "skills/bundled/*/references/*.md" in globs

    def test_skill_text_only_names_real_commands(self):
        """No invented tooling: every `make <target>` the skill cites must exist."""
        import pathlib

        from personalclaw.skills.native import _bundled_root

        md = (_bundled_root() / "check-work" / "SKILL.md").read_text(encoding="utf-8")
        makefile = (pathlib.Path(__file__).resolve().parents[1] / "Makefile").read_text(
            encoding="utf-8"
        )
        targets = set(re.findall(r"^([a-zA-Z][\w-]*):", makefile, re.MULTILINE))
        cited = set(re.findall(r"`make ([a-z][\w-]*)", md))
        assert cited, "skill cites no make target — wrong file?"
        assert cited <= targets, f"skill cites nonexistent make target(s): {cited - targets}"


# ── The shared core ───────────────────────────────────────────────────────────────
class TestClaimReconstruction:
    def test_intent_is_not_a_claim(self):
        claims = reconstruct_claims("I will add `src/a.py` next.")
        assert claims == []

    def test_completion_claim_captures_file_and_ident(self):
        claims = reconstruct_claims("Added `derive_checks` to `src/personalclaw/check_work.py`.")
        assert len(claims) == 1
        assert claims[0].files == ("src/personalclaw/check_work.py",)
        assert claims[0].idents == ("derive_checks",)

    def test_command_claim_captured(self):
        claims = reconstruct_claims("`make lint` passed and `pytest -n 0 tests/x.py` is green.")
        assert claims and set(claims[0].commands) == {"make lint", "pytest -n 0 tests/x.py"}

    def test_unbackticked_path_still_counts(self):
        claims = reconstruct_claims("Created tests/test_check_work.py for this.")
        assert claims and claims[0].files == ("tests/test_check_work.py",)

    def test_hyphenated_segment_inside_a_backticked_path_is_not_a_second_path(self):
        """Measured while running this skill on its own session: the bare-path scan
        re-matched `work/SKILL.md` out of `.../bundled/check-work/SKILL.md`, producing a
        phantom claim that then FAILED — a fail nobody made. The scan now runs on the
        sentence with backticked spans removed."""
        claims = reconstruct_claims(
            "Created `src/personalclaw/skills/bundled/check-work/SKILL.md` for the skill."
        )
        assert claims and claims[0].files == (
            "src/personalclaw/skills/bundled/check-work/SKILL.md",
        )


class TestDerivation:
    def test_content_check_preferred_over_existence(self):
        claims = reconstruct_claims("Added `foo_bar` to `a/b.py`.")
        checks = derive_checks(claims)
        assert [c.kind for c in checks] == ["file_contains"]
        assert checks[0].needle == "foo_bar"

    def test_capped_at_four(self):
        text = " ".join(f"Created `pkg/f{i}.py`." for i in range(9))
        checks = derive_checks(reconstruct_claims(text))
        assert len(checks) == MAX_CHECKS

    def test_underivable_claims_are_reported_not_padded(self, tmp_path):
        report = derive_and_run("Everything looks good to me.", root=tmp_path)
        assert report.results == []
        assert report.verdict == "unverifiable"  # an empty report is never a pass
        assert "Do not" in report.note


class TestExecution:
    def test_file_exists_pass_and_fail_with_evidence(self, tmp_path):
        (tmp_path / "there.py").write_text("x = 1\n", encoding="utf-8")
        report = derive_and_run(
            "Created `there.py` and created `gone.py`.",
            root=tmp_path,
        )
        by_target = {r.check.target: r for r in report.results}
        assert by_target["there.py"].status == "pass"
        assert "bytes" in by_target["there.py"].evidence
        assert by_target["gone.py"].status == "fail"
        assert "no such path" in by_target["gone.py"].evidence
        assert report.verdict == "fail"

    def test_empty_file_is_not_a_pass(self, tmp_path):
        (tmp_path / "hollow.py").touch()
        report = derive_and_run("Created `hollow.py`.", root=tmp_path)
        assert report.results[0].status == "fail"
        assert "0 bytes" in report.results[0].evidence

    def test_content_check_quotes_the_matching_line(self, tmp_path):
        (tmp_path / "m.py").write_text("a = 0\ndef derive_checks():\n    pass\n", encoding="utf-8")
        report = derive_and_run("Added `derive_checks` to `m.py`.", root=tmp_path)
        assert report.results[0].status == "pass"
        assert ":2:" in report.results[0].evidence and "derive_checks" in report.results[0].evidence

    def test_content_check_fails_when_the_symbol_is_absent(self, tmp_path):
        (tmp_path / "m.py").write_text("a = 0\n", encoding="utf-8")
        report = derive_and_run("Added `missing_symbol` to `m.py`.", root=tmp_path)
        assert report.results[0].status == "fail"
        assert "none containing" in report.results[0].evidence

    def test_command_without_a_runner_is_unverifiable_never_a_pass(self, tmp_path):
        report = derive_and_run("`make lint` passed.", root=tmp_path)
        assert [r.status for r in report.results] == ["unverifiable"]
        assert report.verdict == "unverifiable"
        assert "re-run" in report.results[0].evidence

    def test_command_runner_tristate(self, tmp_path):
        checks = derive_checks(reconstruct_claims("`make lint` passed."))
        for ret, expect in ((True, "pass"), (False, "fail"), (None, "unverifiable")):
            got = run_checks(checks, root=tmp_path, command_runner=lambda _cmd, r=ret: r)
            assert got[0].status == expect

    def test_path_escape_is_unverifiable_not_guessed(self, tmp_path):
        report = derive_and_run("Created `../outside.py`.", root=tmp_path / "inner")
        assert report.results[0].status == "unverifiable"
        assert "outside" in report.results[0].evidence


class TestAdversarialPlantedFlaw:
    """SC 5 — a multi-step turn with a deliberately planted flaw. check-work must catch
    it with ZERO self-reported passes."""

    def test_planted_flaw_is_caught_with_no_self_reported_passes(self, tmp_path):
        (tmp_path / "widget.py").write_text(
            "def render_widget():\n    return 1\n", encoding="utf-8"
        )
        session = (
            "Implemented `render_widget` in `widget.py`. "
            "Created `tests/test_widget.py` covering it. "
            "`make lint` passed."
        )
        report = derive_and_run(session, root=tmp_path)
        statuses = {r.check.target: r.status for r in report.results}
        assert statuses["widget.py"] == "pass"
        assert statuses["tests/test_widget.py"] == "fail"  # the planted flaw
        assert statuses["make lint"] == "unverifiable"  # claimed, not observed here
        assert report.verdict == "fail"
        # Zero self-reported passes: every pass names an observed artifact, and nothing
        # that merely claimed success is recorded as passing.
        for res in report.passed:
            assert str(tmp_path) in res.evidence
        rendered = render_report(report)
        assert "FAIL" in rendered and "UNVERIFIABLE" in rendered
        assert "test -e tests/test_widget.py" in rendered  # a reader can re-run it


# ── The SDLC post-gate hook (SC 6) ────────────────────────────────────────────────
def _fake_loop(tmp_path, findings_stage: str):
    return SimpleNamespace(
        id="loop-hc4",
        kind="code",
        workspace_dir=str(tmp_path),
        loop_dir=str(tmp_path),
        plan=[{"stage": findings_stage, "title": findings_stage, "status": "active"}],
        phase_status={findings_stage: "active"},
        kind_config={},
    )


def _config_with_hook_on(monkeypatch):
    """A config object with ONLY `loops.check_work_stages` flipped on, installed as the
    one `AppConfig.load()` the hook will read."""
    from personalclaw.config import AppConfig

    cfg = AppConfig()
    cfg.loops.check_work_stages = True
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: cfg))
    return cfg


class _Ctx:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, _loop_id, kind, payload):
        self.events.append((kind, payload))


class TestSdlcPostGateHook:
    def _hook(self, loop, findings, ctx):
        from personalclaw.loop.kinds.sdlc import CodeKind

        return asyncio.run(CodeKind()._check_work_post_gate(loop, 0, findings, ctx))

    def test_off_by_default_is_a_no_op(self, tmp_path, monkeypatch):
        from personalclaw.config import AppConfig

        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
        assert AppConfig().loops.check_work_stages is False
        monkeypatch.setattr(AppConfig, "load", staticmethod(AppConfig))
        ctx = _Ctx()
        loop = _fake_loop(tmp_path, "implementation")
        findings = [{"stage": "implementation", "summary": "Created `never_written.py`."}]
        assert self._hook(loop, findings, ctx) is True
        assert ctx.events == []  # nothing ran

    def test_on_catches_a_claimed_but_missing_file(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        loaded = _config_with_hook_on(monkeypatch)
        assert loaded.loops.check_work_stages is True
        ctx = _Ctx()
        loop = _fake_loop(ws, "implementation")
        findings = [{"stage": "implementation", "summary": "Created `never_written.py`."}]
        assert self._hook(loop, findings, ctx) is False
        kinds = [k for k, _ in ctx.events]
        assert "gate_check" in kinds
        payload = dict(ctx.events[0][1])
        assert payload["label"] == "check_work" and payload["verdict"] == "fail"
        assert any(c["status"] == "fail" for c in payload["checks"])

    def test_on_passes_when_the_claim_is_true(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "written.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        _config_with_hook_on(monkeypatch)
        ctx = _Ctx()
        loop = _fake_loop(ws, "implementation")
        findings = [{"stage": "implementation", "summary": "Created `written.py`."}]
        assert self._hook(loop, findings, ctx) is True
        assert dict(ctx.events[0][1])["verdict"] == "pass"

    def test_hook_fails_open_on_a_broken_core(self, tmp_path, monkeypatch):
        """A broken hook must never be a new way to wedge a loop."""
        _config_with_hook_on(monkeypatch)
        import personalclaw.check_work as cw

        monkeypatch.setattr(cw, "derive_and_run", lambda *a, **k: 1 / 0)
        ctx = _Ctx()
        loop = _fake_loop(tmp_path, "implementation")
        findings = [{"stage": "implementation", "summary": "Created `x.py`."}]
        assert self._hook(loop, findings, ctx) is True


# ── The chat offer chip (§3.3) ────────────────────────────────────────────────────
class TestCheckWorkOfferHeuristic:
    def test_needs_three_tool_calls(self):
        from personalclaw.dashboard.chat_followups import turn_earns_check_work_offer

        assert turn_earns_check_work_offer("Done — added the file.", 3) is True
        assert turn_earns_check_work_offer("Done — added the file.", 2) is False

    def test_needs_completion_language(self):
        from personalclaw.dashboard.chat_followups import turn_earns_check_work_offer

        assert turn_earns_check_work_offer("Here is what I found so far.", 7) is False

    def test_offer_is_never_an_invocation(self, monkeypatch):
        """The chip OFFERS; it must not run anything. The broadcast carries the prompt the
        user's click will send, and nothing else happens server-side."""
        from personalclaw.dashboard import chat_followups as cf

        sent: list[tuple[str, dict]] = []
        state = SimpleNamespace(broadcast_ws=lambda kind, payload: sent.append((kind, payload)))
        session = SimpleNamespace(
            key="s1",
            is_restricted=False,
            _last_turn_errored=False,
            messages=[{"role": "assistant", "content": "Done — created three files."}],
        )
        monkeypatch.setattr(cf, "_check_work_offer_enabled", lambda: True)
        called: list[str] = []
        monkeypatch.setattr(
            "personalclaw.check_work.derive_and_run",
            lambda *a, **k: called.append("ran"),
        )
        cf.maybe_offer_check_work(state, session, 4)
        assert sent and sent[0][0] == "chat_check_work_offer"
        assert sent[0][1]["prompt"] == "check your work"
        assert called == []  # nothing was verified — the user has not clicked yet

    def test_disabled_config_offers_nothing(self, monkeypatch):
        from personalclaw.dashboard import chat_followups as cf

        sent: list = []
        state = SimpleNamespace(broadcast_ws=lambda kind, payload: sent.append((kind, payload)))
        session = SimpleNamespace(
            key="s1",
            is_restricted=False,
            _last_turn_errored=False,
            messages=[{"role": "assistant", "content": "Done — created three files."}],
        )
        monkeypatch.setattr(cf, "_check_work_offer_enabled", lambda: False)
        cf.maybe_offer_check_work(state, session, 9)
        assert sent == []


# ── Config round-trip: the four wiring points (SC 7) ──────────────────────────────
@pytest.mark.parametrize(
    "section,field_name,default",
    [("loops", "check_work_stages", False), ("dashboard", "offer_check_work", True)],
)
class TestConfigRoundTrip:
    def test_dataclass_has_meta(self, section, field_name, default):
        from dataclasses import fields

        from personalclaw.config import AppConfig

        cfg = AppConfig.load()
        target = getattr(cfg, section)
        f = {x.name: x for x in fields(target)}[field_name]
        assert f.metadata.get("label"), "wiring point 1: dataclass field needs _meta"
        assert f.metadata.get("help")
        assert getattr(target, field_name) is default

    def test_load_reads_a_written_value(self, section, field_name, default, tmp_path, monkeypatch):
        import json

        from personalclaw.config import AppConfig

        home = tmp_path / "home"
        home.mkdir()
        (home / "config.json").write_text(
            json.dumps({section: {field_name: not default}}), encoding="utf-8"
        )
        monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
        cfg = AppConfig.load()
        assert getattr(getattr(cfg, section), field_name) is (not default)

    def test_to_dict_emits_it(self, section, field_name, default):
        from personalclaw.config import AppConfig

        assert field_name in AppConfig.load().to_dict()[section]

    def test_write_path_accepts_it(self, section, field_name, default):
        """Wiring point 4: a write path exists — the PATCH allowlist for `loops`, the
        dedicated chat-prefs endpoint for the dashboard chat surface."""
        if section == "loops":
            from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

            assert _EDITABLE_CONFIG[f"loops.{field_name}"] == {"type": "bool"}
        else:
            import pathlib

            src = (
                pathlib.Path(__file__).resolve().parents[1]
                / "src/personalclaw/dashboard/handlers/files.py"
            ).read_text(encoding="utf-8")
            assert src.count(f'"{field_name}"') >= 2  # allowlist + bool coercion loop
            assert f'"{field_name}": cfg.dashboard.{field_name}' in src


def test_offer_check_work_has_a_frontend_control():
    """Wiring point 4b applies to the USER-FACING bool. `dashboard.offer_check_work`
    governs a chat chip, so it gets a Settings toggle and a typed API field.

    `loops.check_work_stages` deliberately does not: no frontend surface reads
    `config.loops` at all in this repo (its siblings `judge_use_case` and
    `stagnation_window` have no control either), so its write path is the
    `_EDITABLE_CONFIG` PATCH allowlist — the documented alternative. Inventing a Loops
    settings panel for one bool is out of this atom's scope; the day one lands, this
    field belongs in it.
    """
    import pathlib

    web = pathlib.Path(__file__).resolve().parents[1] / "web/src"
    panel = (web / "pages/settings/ChatPanel.tsx").read_text(encoding="utf-8")
    assert "offer_check_work" in panel
    api = (web / "lib/api.ts").read_text(encoding="utf-8")
    assert "offer_check_work: boolean" in api
    chip = (web / "pages/chat/CheckWorkChip.tsx").read_text(encoding="utf-8")
    assert "CheckWorkChip" in chip
    page = (web / "pages/ChatPage.tsx").read_text(encoding="utf-8")
    assert "chat_check_work_offer" in page and "<CheckWorkChip" in page

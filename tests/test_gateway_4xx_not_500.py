"""Family rail: gateway side-endpoints return 4xx (not 500) on bad input, and the
native skills marketplace never reads outside its root (#787, #441, #399 + the
unreported ``fetch()`` traversal).

The class under test: an edit/side endpoint trusting input its create/sibling
already validates — the provider dumped files for a path-shaped id, preview
crashed on the variables create rejects, bindings crashed on an unhashable
use_case, and PUT /api/loops bypassed the create gate's numeric/boolean floor.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw.skills.marketplace import SkillNotFoundError
from personalclaw.skills.native import NativeSkillsMarketplace


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    monkeypatch.setenv("PERSONALCLAW_SKIP_PROMPT_SEED", "1")


def _body(resp):
    return json.loads(resp.body.decode())


# ── native fetch: ids are names, never paths ─────────────────────────────────


@pytest.fixture()
def skill_root(tmp_path):
    root = tmp_path / "skills-root"
    (root / "good").mkdir(parents=True)
    (root / "good" / "SKILL.md").write_text("# good\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("# outside\n", encoding="utf-8")
    (outside / "secret.txt").write_text("s3cr3t", encoding="utf-8")
    return root


class TestNativeFetchTraversal:
    def test_plain_id_still_fetches(self, skill_root):
        detail = NativeSkillsMarketplace(root=skill_root).fetch("good")
        assert detail.id == "good"
        assert any(f["path"] == "SKILL.md" for f in detail.files)

    def test_dotdot_id_refused(self, skill_root):
        # ../outside names a dir that DOES carry a SKILL.md — the old code
        # happily rglob'd it (and its secret.txt) out of the root.
        with pytest.raises(SkillNotFoundError):
            NativeSkillsMarketplace(root=skill_root).fetch("../outside")

    def test_absolute_id_refused(self, skill_root, tmp_path):
        # pathlib: root / "/abs" REPLACES the root entirely.
        with pytest.raises(SkillNotFoundError):
            NativeSkillsMarketplace(root=skill_root).fetch(str(tmp_path / "outside"))

    @pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "good/.."])
    def test_path_shaped_ids_refused(self, skill_root, bad):
        with pytest.raises(SkillNotFoundError):
            NativeSkillsMarketplace(root=skill_root).fetch(bad)

    def test_symlink_out_of_root_refused(self, skill_root, tmp_path):
        link = skill_root / "sneaky"
        try:
            link.symlink_to(tmp_path / "outside")
        except OSError:
            pytest.skip("symlinks unavailable")
        with pytest.raises(SkillNotFoundError):
            NativeSkillsMarketplace(root=skill_root).fetch("sneaky")

    def test_missing_id_is_typed_not_runtimeerror(self, skill_root):
        with pytest.raises(SkillNotFoundError):
            NativeSkillsMarketplace(root=skill_root).fetch("nope")


# ── handlers: not-found is 404, malformed is 400 — never 500 ────────────────


def _query_req(query):
    r = MagicMock()
    r.rel_url.query = query
    return r


def _json_req(body, match_info=None):
    r = MagicMock()
    r.match_info = match_info or {}
    r.get = lambda *_a, **_k: "test"

    async def _json():
        return body

    r.json = _json
    return r


class TestSkillEndpoints4xx:
    @pytest.mark.asyncio
    async def test_marketplace_detail_missing_native_skill_is_404(self):
        from personalclaw.dashboard.handlers.skills import api_skills_marketplace_detail

        resp = await api_skills_marketplace_detail(
            _query_req({"id": "definitely-not-a-skill", "marketplace": "native"})
        )
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_marketplace_detail_traversal_id_is_404(self):
        from personalclaw.dashboard.handlers.skills import api_skills_marketplace_detail

        resp = await api_skills_marketplace_detail(
            _query_req({"id": "../../etc", "marketplace": "native"})
        )
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_install_missing_native_skill_is_404(self, tmp_path):
        from personalclaw.dashboard.handlers.skills import api_skills_install

        resp = await api_skills_install(
            _json_req(
                {
                    "id": "definitely-not-a-skill",
                    "marketplace": "native",
                    "target": str(tmp_path / "install-target"),
                }
            )
        )
        assert resp.status == 404


class TestPromptEndpoints4xx:
    @pytest.mark.asyncio
    async def test_preview_bad_variable_type_is_400(self):
        # Create routes the same payload through ValueError→400; preview must not 500.
        from personalclaw.dashboard.handlers.prompts import api_prompt_preview

        resp = await api_prompt_preview(
            _json_req(
                {
                    "content": "hello {{x}}",
                    "variables": [{"name": "x", "type": "bogus-type"}],
                }
            )
        )
        assert resp.status == 400
        assert "bogus-type" in _body(resp)["error"]["message"]

    @pytest.mark.asyncio
    async def test_bindings_save_unhashable_use_case_is_400(self):
        # `in frozenset` raised TypeError on a list use_case — the adjacent ref IS guarded.
        from personalclaw.dashboard.handlers.prompts import api_prompt_bindings_save

        resp = await api_prompt_bindings_save(
            _json_req({"use_case": ["not", "a", "string"], "ref": ""})
        )
        assert resp.status == 400


# ── loops: PUT mirrors the create gate's numeric/boolean floor ───────────────


class TestLoopSpecEditFloor:
    def _errs(self, patch):
        from personalclaw.loop.validation import spec_edit_errors

        return spec_edit_errors(patch, kind="goal", existing_kind_config={})

    def test_negative_max_cycles_rejected(self):
        assert any("negative" in e.lower() for e in self._errs({"max_cycles": -1}))

    def test_over_hard_cap_rejected(self):
        assert any("hard cap" in e.lower() for e in self._errs({"max_cycles": 10**9}))

    def test_non_int_max_cycles_rejected(self):
        assert any("whole number" in e.lower() for e in self._errs({"max_cycles": "lots"}))

    def test_non_int_idle_secs_rejected(self):
        assert any("idle_secs" in e for e in self._errs({"idle_secs": "abc"}))

    def test_string_false_autopilot_rejected(self):
        # int(bool("false")) == 1: the silent wrong direction the floor exists for.
        assert any("'autopilot' must be a boolean" in e for e in self._errs({"autopilot": "false"}))

    @pytest.mark.parametrize("f", ["attended", "auto_teardown_on_complete"])
    def test_sibling_bool_fields_rejected_as_strings(self, f):
        assert any(f"'{f}' must be a boolean" in e for e in self._errs({f: "true"}))

    def test_clean_patch_passes(self):
        assert self._errs({"max_cycles": 5, "idle_secs": 60, "autopilot": False}) == []

    def test_absent_fields_not_checked(self):
        assert self._errs({"name": "renamed"}) == []

    def test_create_gate_shares_the_boolean_floor(self):
        from personalclaw.loop.validation import validate

        result = validate(
            {"task": "a perfectly reasonable and detailed task description", "autopilot": "false"},
            agent_exists=True,
        )
        assert any("'autopilot' must be a boolean" in e for e in result.errors)

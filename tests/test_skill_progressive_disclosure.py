"""Two-phase progressive skill disclosure (skill-progressive-disclosure, #29).

Phase 1 = the agent's context carries a compact INDEX; Phase 2 = the agent pulls a
full body on demand via the skill_invoke tool (which also records the use, #25)."""

from __future__ import annotations

from pathlib import Path

from personalclaw.mcp_core import _call_tool_inner, _list_tools
from personalclaw.skills.loader import SkillsLoader
from personalclaw.validation import MCP_CORE_SCHEMAS


def _create_skill(base: Path, name: str, body: str) -> None:
    (base / name).mkdir(parents=True, exist_ok=True)
    (base / name / "SKILL.md").write_text(body, encoding="utf-8")


# ── the skill_invoke tool (Phase 2) ──


def test_skill_invoke_registered():
    assert "skill_invoke" in {t["name"] for t in _list_tools()}
    assert "skill_invoke" in MCP_CORE_SCHEMAS


def test_skill_invoke_returns_full_body(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    _create_skill(
        skills,
        "tiny-url",
        "---\nname: tiny-url\ndescription: shorten urls\n---\n# Tiny URL\nStep 1. do it.",
    )
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: skills)
    # usage store writes under skills_dir → temp; safe.
    out = _call_tool_inner("skill_invoke", {"name": "tiny-url"})
    assert "Step 1. do it." in out
    assert "[Skill: tiny-url]" in out
    # frontmatter stripped
    assert "description: shorten urls" not in out


def test_skill_invoke_unknown(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: skills)
    out = _call_tool_inner("skill_invoke", {"name": "nope"})
    assert out.startswith("Error")


def test_skill_invoke_requires_name(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: tmp_path)
    assert _call_tool_inner("skill_invoke", {"name": ""}).startswith("Error")


def test_skill_invoke_records_use(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    _create_skill(skills, "foo", "---\nname: foo\ndescription: d\n---\n# Foo\nbody")
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: skills)
    # usage.py binds skills_dir at import → patch it in that namespace too.
    monkeypatch.setattr("personalclaw.skills.usage.skills_dir", lambda: skills)
    _call_tool_inner("skill_invoke", {"name": "foo"})
    from personalclaw.skills.usage import SkillUsageStore

    assert SkillUsageStore(path=skills / ".usage.json").get("foo").count == 1


# ── Phase-1 index (get_context) ──


def test_index_uses_skill_invoke_not_cat(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    _create_skill(skills, "tiny-url", "---\nname: tiny-url\ndescription: shorten urls\n---\n# T\nx")
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: skills)
    loader = SkillsLoader(skills_path=skills, install_builtins=False)
    ctx = loader.get_context()
    assert "skill_invoke" in ctx
    assert "cat <path>" not in ctx
    assert "tiny-url" in ctx  # indexed


def test_index_excludes_archived(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    _create_skill(
        skills, "auto/old", "---\nname: auto/old\ndescription: stale\nstatus: archived\n---\n# x\ny"
    )
    _create_skill(skills, "live", "---\nname: live\ndescription: fresh\n---\n# x\ny")
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: skills)
    loader = SkillsLoader(skills_path=skills, install_builtins=False)
    ctx = loader.get_context()
    assert "live" in ctx
    assert "auto/old" not in ctx  # archived skill kept off the index


# ── turn-time threshold: index-only above N, inline at/below ──


def _builder_with_skills(tmp_path, n_matching: int):
    from personalclaw.context import ContextBuilder
    from personalclaw.memory import MemoryStore

    skills = tmp_path / "skills"
    for i in range(n_matching):
        # all trigger on "deploy widget" so the surfacer returns them all
        _create_skill(
            skills,
            f"s{i}",
            f"---\nname: s{i}\ndescription: deploy widget {i}\ntriggers: deploy widget\n---\n# S{i}\nBODY-{i}",  # noqa: E501
        )
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=skills, install_builtins=False),
    )


def _patch_cfg(monkeypatch, *, max_triggered: int, threshold: int):
    """Load a real AppConfig, override the two skills knobs, and pin AppConfig.load."""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    cfg.skills.max_triggered = max_triggered
    cfg.skills.progressive_disclosure_threshold = threshold
    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", classmethod(lambda cls: cfg))
    return cfg


def test_above_threshold_injects_index_only(tmp_path, monkeypatch):
    _patch_cfg(monkeypatch, max_triggered=10, threshold=3)
    builder = _builder_with_skills(tmp_path, 5)
    msg, _ = builder.build_message("deploy widget now", is_new_session=False)
    assert "INDEX only" in msg
    assert "skill_invoke" in msg
    assert "BODY-0" not in msg  # bodies NOT inlined above threshold


def test_at_threshold_inlines_bodies(tmp_path, monkeypatch):
    _patch_cfg(monkeypatch, max_triggered=10, threshold=8)
    builder = _builder_with_skills(tmp_path, 3)
    msg, _ = builder.build_message("deploy widget now", is_new_session=False)
    assert "BODY-0" in msg  # inlined at/below threshold
    assert "INDEX only" not in msg


# ── #1783: the two knobs are ORDERED, so the branch above is reachable at defaults ──
#
# `max_triggered` and `progressive_disclosure_threshold` are two absolute counts over the
# same list, and surfacing truncates that list to `max_triggered` BEFORE `context.py` asks
# `len(triggered) > progressive_disclosure_threshold`. Shipped at 3 and 8, the question had
# no reachable `True` answer: every test above had to raise `max_triggered` to 10 to see the
# index path at all, which is the tell. The clamp makes the pair ordered by construction.


def test_the_shipped_defaults_are_ordered():
    from personalclaw.config.loader import SkillsConfig

    cfg = SkillsConfig()
    assert cfg.progressive_disclosure_threshold < cfg.max_triggered, (
        "a threshold at or above max_triggered cannot be exceeded by a list capped at "
        "max_triggered — the index branch would be dead code behind a settings row"
    )


def test_an_unordered_pair_is_clamped_to_max_triggered_minus_one():
    from personalclaw.config.loader import SkillsConfig

    # The exact pre-fix shipped pair.
    cfg = SkillsConfig(max_triggered=3, progressive_disclosure_threshold=8)
    assert cfg.progressive_disclosure_threshold == 2


def test_an_already_ordered_pair_is_left_alone():
    from personalclaw.config.loader import SkillsConfig

    cfg = SkillsConfig(max_triggered=10, progressive_disclosure_threshold=8)
    assert cfg.progressive_disclosure_threshold == 8


def test_zero_still_means_disabled_and_is_never_clamped_up():
    """0 is the owner's "always inline" choice. Arithmetic must not pick it, and must not
    overwrite it — a clamp that turned 0 into 1 would silently enable a control the owner
    turned off."""
    from personalclaw.config.loader import SkillsConfig

    for max_triggered in (1, 3, 10):
        cfg = SkillsConfig(max_triggered=max_triggered, progressive_disclosure_threshold=0)
        assert cfg.progressive_disclosure_threshold == 0


def test_the_clamp_floors_at_one_rather_than_disabling_the_control():
    """`max_triggered - 1` is 0 at `max_triggered=1`, and 0 means DISABLED — so the clamp
    must floor at 1 rather than let arithmetic turn the control off."""
    from personalclaw.config.loader import SkillsConfig

    cfg = SkillsConfig(max_triggered=1, progressive_disclosure_threshold=5)
    assert cfg.progressive_disclosure_threshold == 1


def test_the_load_fallback_matches_the_dataclass_default(tmp_path, monkeypatch):
    """`load()`'s `.get()` fallback is a second default that can drift from the first."""
    from personalclaw.config import loader as loader_mod
    from personalclaw.config.loader import AppConfig, SkillsConfig

    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)
    assert (
        AppConfig.load().skills.progressive_disclosure_threshold
        == SkillsConfig().progressive_disclosure_threshold
    )


def _pristine_cfg(monkeypatch):
    """Pin a SHIPPED-DEFAULT config — no overrides, so the assertion is about the defaults."""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig()
    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", classmethod(lambda cls: cfg))
    return cfg


def test_the_index_branch_fires_on_the_shipped_defaults(tmp_path, monkeypatch):
    """The WIRING assertion, not the clamp's: a real turn on untouched config reaches the
    index path. Every other index-path test in this file raises `max_triggered` to 10 first;
    this one changes nothing, and would have failed on the pre-fix defaults."""
    _pristine_cfg(monkeypatch)
    builder = _builder_with_skills(tmp_path, 5)
    msg, _ = builder.build_message("deploy widget now", is_new_session=False)
    assert "INDEX only" in msg
    assert "BODY-0" not in msg


def test_a_single_match_still_inlines_on_the_shipped_defaults(tmp_path, monkeypatch):
    """The floor for the test above — the default is a threshold, not "always index"."""
    _pristine_cfg(monkeypatch)
    builder = _builder_with_skills(tmp_path, 1)
    msg, _ = builder.build_message("deploy widget now", is_new_session=False)
    assert "BODY-0" in msg
    assert "INDEX only" not in msg


def test_the_unreadable_config_arm_no_longer_hardcodes_a_threshold(tmp_path, monkeypatch):
    """#1783 clause 4 — `context.py`'s `except` arm used the literal `8`, re-creating the
    inert state on any config-load failure: the same bug one layer down, reachable by a
    malformed config.json. It now constructs `SkillsConfig()`, so it inherits the clamp."""
    import personalclaw.context as context_mod

    class _Boom:
        @classmethod
        def load(cls):
            raise RuntimeError("config.json is malformed")

    # Pristine first so SURFACING (which reads the loader's own AppConfig) still caps at the
    # shipped `max_triggered`; only `context.py`'s threshold lookup is made to fail.
    _pristine_cfg(monkeypatch)
    monkeypatch.setattr(context_mod, "AppConfig", _Boom)
    builder = _builder_with_skills(tmp_path, 5)
    msg, _ = builder.build_message("deploy widget now", is_new_session=False)
    assert (
        "INDEX only" in msg
    ), "a failed config load fell back to a threshold the surfaced list can never exceed"


# ── the 620-warning first run: a SHIPPED HOME carried the pre-#3328 pair ──────────────────
#
# The owner's first run on the installed app logged
# `progressive_disclosure_threshold 8 is at or above max_triggered 3, so the index branch could
# never fire; using 2` — 620 times in one session.
#
# Determined rather than guessed. The SHIPPED DEFAULT is not the culprit and neither is the
# comparison: `c58ea7cf6` (#3328) changed `default=8` → `default=2` and added the clamp above, and
# a gateway booted on an empty `PERSONALCLAW_HOME` writes `progressive_disclosure_threshold: 2`
# and logs the warning zero times (measured on a fresh home, port 10731). What still shipped the
# pre-fix pair is a HOME: `tests_fixtures/six-month-home/config.json` is package data
# (`pyproject.toml`: the `tests_fixtures` tree), it was generated from a 0.1.3 home, and `--seed
# six-month-home` therefore installs `max_triggered: 3` beside threshold `8` — which reproduces
# the owner's warning verbatim on every `AppConfig.load()`.
#
# Measured, with a control: loading each shipped fixture home through `AppConfig.load()` and
# capturing `personalclaw.config.loader` warnings gave `empty` → 0, `demo-home` → 0,
# `six-month-home` → 1, the message above. The two zeroes are what make the one a real reading.
#
# So the rail is over the shipped HOMES, not over one knob: a config this project ships must never
# be one the validator has to rewrite on load. It is stated as "nothing was rewritten" rather than
# "no warning logged" so it also covers a future validator that clamps quietly.


def _shipped_fixture_homes():
    """Every ``config.json`` under the packaged fixture homes.

    Resolved off ``personalclaw.__file__`` rather than importing ``tests_fixtures``: it is a
    data directory with no ``__init__.py``, so it imports as a namespace package whose
    ``__file__`` is ``None``.
    """
    import personalclaw

    root = Path(personalclaw.__file__).resolve().parent / "tests_fixtures"
    return sorted(p for p in root.glob("*/config.json"))


def test_shipped_fixture_homes_exist_at_all():
    """The floor for the test below: a glob that matches nothing passes vacuously."""
    homes = _shipped_fixture_homes()
    assert homes, "no shipped fixture config.json found — the rail below would be vacuous"
    assert {p.parent.name for p in homes} >= {"six-month-home"}


def test_no_shipped_home_carries_a_config_the_validator_has_to_rewrite(tmp_path, monkeypatch):
    """A shipped home must load UNCHANGED — no clamp, no override, no warning.

    Copied to a temp dir first: `AppConfig.load()` is pointed at the copy, so this can never
    write to the packaged fixture or to a real home.
    """
    import json as _json
    import shutil

    from personalclaw.config import loader as loader_mod
    from personalclaw.config.loader import AppConfig

    rewritten: list[str] = []
    for src in _shipped_fixture_homes():
        home = tmp_path / src.parent.name
        home.mkdir()
        shutil.copyfile(src, home / "config.json")
        on_disk = _json.loads(src.read_text(encoding="utf-8"))
        monkeypatch.setattr(loader_mod, "config_dir", lambda h=home: h)
        loaded = AppConfig.load()
        for section, values in on_disk.items():
            if not isinstance(values, dict):
                continue
            obj = getattr(loaded, section, None)
            if obj is None:
                continue
            for field_name, want in values.items():
                if not hasattr(obj, field_name):
                    continue
                got = getattr(obj, field_name)
                if isinstance(want, (bool, int, str)) and got != want:
                    rewritten.append(
                        f"{src.parent.name}: {section}.{field_name} "
                        f"on disk {want!r} → loaded {got!r}"
                    )
    assert not rewritten, (
        "a home this project SHIPS carries values the validator overrides on every load, so "
        "every install seeded from it warns forever about a default nobody chose: "
        + "; ".join(rewritten)
    )

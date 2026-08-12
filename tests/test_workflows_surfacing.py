"""Tests for SOP surfacing discipline (TASKS-SOPS §2 R3/R4, S58).

The governing precedent is somebody else's scar tissue, quoted in the plan: OpenSquilla shipped
auto-trigger-by-default and retreated to manual-first after pasted content kept firing workflows. So
the tests here are mostly about NOT firing, and the pasted-content case is tested directly.

Two things were checked against real code rather than assumed:

**The negative-trigger veto matches `skills/surfacing._keyword_score` exactly** on the same inputs.
One veto syntax across both surfaces means a user who learned `!` once knows it everywhere; two
implementations would leave one surface silently ignoring the other's vetoes.

**`SCOPE_LADDER` already exists** in S45's `template_pipeline`, so scope words are
reused rather than redefined — two ladders would disagree about promotion order.
"""

import re

import pytest

from personalclaw.workflows.surfacing import (
    DEFAULT_MODE_MIGRATED,
    DEFAULT_MODE_NEW,
    DIGEST_BEGIN,
    DIGEST_END,
    MAX_SUMMARY_CHARS,
    MAX_TRIGGERS,
    MAX_WHEN_TO_USE_CHARS,
    MIN_TRIGGERS,
    FreedomLevel,
    SurfaceMode,
    SurfacingMeta,
    Veto,
    collisions,
    drift,
    graduate,
    lint_metadata,
    may_suggest,
    migrate_sop,
    render_passive,
    render_suggest,
    strip_digest_fence,
    trigger_phrases,
    unreachable,
    veto_reasons,
)

DIGEST = "1. Check CI is green\n2. Never deploy on Friday"


def meta(**kw) -> SurfacingMeta:
    base = {
        "match_text": "deploy to staging, ship the release, !dry run",
        "summary": "Run the staging deploy checklist",
        "agent_digest": DIGEST,
        "surface_mode": SurfaceMode.SUGGEST,
    }
    return SurfacingMeta(**{**base, **kw})


# ── manual-first is the default ──


def test_a_NEW_def_does_not_surface():
    """The retreat position the plan's cited precedent arrived at the hard way: auto-trigger by
    default meant pasted content kept firing workflows."""
    assert DEFAULT_MODE_NEW is SurfaceMode.OFF
    assert SurfacingMeta().surface_mode is SurfaceMode.OFF


def test_a_MIGRATED_sop_keeps_surfacing():
    """It was already surfacing, and silently turning it off would look like the migration lost
    it.
    """
    assert DEFAULT_MODE_MIGRATED is SurfaceMode.PASSIVE


def test_PASSIVE_and_SUGGEST_are_separate_modes():
    """ "auto_surface: true" conflated quietly injecting guidance with proposing to execute. The
    second needs preconditions and a requirements preflight; the first needs neither.
    """
    assert {m.value for m in SurfaceMode} == {"off", "passive", "suggest"}


def test_an_UNKNOWN_mode_reads_as_OFF():
    """Erring toward silence costs one manual invocation; erring toward suggest fires workflows
    nobody enabled.
    """
    restored = SurfacingMeta.from_dict({"surface_mode": "aggressive"})
    assert restored.surface_mode is SurfaceMode.OFF


def test_a_PASSIVE_def_does_not_earn_a_suggestion():
    """Guidance is not a proposal. A passive def that suggested execution would make the two
    modes one mode with extra steps.
    """
    ok, reasons = may_suggest("deploy to staging", meta(surface_mode=SurfaceMode.PASSIVE))
    assert ok is False
    assert Veto.MODE_OFF in reasons


# ── the negative-trigger veto matches the shipped one ──


@pytest.mark.parametrize(
    "query",
    [
        "do a dry run of the deploy",
        "deploy to staging",
        "run a dry run",
        "dry the dishes",
    ],
)
def test_the_veto_AGREES_with_the_skills_implementation(query):
    """One veto syntax across both surfaces. Two implementations would leave one silently
    ignoring the other's vetoes, and a user who learned `!` once would be wrong half the time.
    """
    from personalclaw.skills.surfacing import _keyword_score

    triggers = "deploy to staging, ship the release, !dry run"
    _score, skills_negated = _keyword_score(set(re.findall(r"\w+", query.lower())), triggers)
    mine = Veto.NEGATIVE_TRIGGER in veto_reasons(query, meta(match_text=triggers))
    assert mine == skills_negated


def test_a_negative_trigger_VETOES():
    ok, reasons = may_suggest("do a dry run of the deploy to staging", meta())
    assert ok is False
    assert Veto.NEGATIVE_TRIGGER in reasons


def test_trigger_phrases_split_positive_from_negative():
    positive, negative = trigger_phrases("deploy to staging, ship it, !dry run, !rehearsal")
    assert positive == ["deploy to staging", "ship it"]
    assert negative == ["dry run", "rehearsal"]


def test_an_empty_match_text_yields_nothing():
    assert trigger_phrases("") == ([], [])
    assert trigger_phrases("  ,  , ") == ([], [])


def test_a_bare_bang_is_not_a_negative_trigger():
    """An author typo should not become a veto that matches everything."""
    assert trigger_phrases("deploy, !")[1] == []


# ── the failure this discipline exists for ──


def test_PASTED_content_never_suggests():
    """The cited failure verbatim: a paste contains every trigger phrase anybody ever wrote."""
    query = "deploy to staging\n```\nTraceback (most recent call last)\n```"
    ok, reasons = may_suggest(query, meta())
    assert ok is False
    assert Veto.PASTED_CONTENT in reasons


@pytest.mark.parametrize(
    "marker", ["```", "> quoted line", "Traceback (most recent call last)", "--- a/file.py"]
)
def test_each_paste_marker_vetoes(marker):
    assert Veto.PASTED_CONTENT in veto_reasons(f"deploy to staging {marker}", meta())


@pytest.mark.parametrize(
    "query",
    [
        "how would i deploy to staging",
        "what's the best way to deploy to staging",
        "should i deploy to staging",
        "help me decide about the deploy to staging",
    ],
)
def test_a_PLANNING_question_never_suggests(query):
    """Suggesting a run answers a question nobody asked — they asked for a plan."""
    assert Veto.PLANNING_ONLY in veto_reasons(query, meta())


def test_a_NAMED_workflow_wins():
    """The user already chose. Suggesting a different one is arguing with a stated decision."""
    reasons = veto_reasons("deploy to staging", meta(), named_workflow="other-workflow")
    assert Veto.ALREADY_NAMED in reasons


def test_a_MISSING_requirement_vetoes():
    """A suggestion to run something that cannot run is a suggestion that wastes the click and
    teaches the user to ignore the next one.
    """
    reasons = veto_reasons(
        "deploy to staging", meta(requirements=["git", "docker"]), available={"git"}
    )
    assert Veto.REQUIREMENT_MISSING in reasons


def test_satisfied_requirements_do_not_veto():
    reasons = veto_reasons(
        "deploy to staging", meta(requirements=["git"]), available={"git", "docker"}
    )
    assert Veto.REQUIREMENT_MISSING not in reasons


def test_a_FAILED_precondition_vetoes():
    ok, reasons = may_suggest("deploy to staging", meta(), preconditions_pass=False)
    assert ok is False
    assert Veto.PRECONDITION_FAILED in reasons


def test_ALL_veto_reasons_are_returned_not_just_the_first():
    """A def vetoed for three reasons has an author who should see three — returning early sends
    them to fix one and be surprised again.
    """
    reasons = veto_reasons(
        "how would i do a dry run of the deploy ```paste```",
        meta(),
        named_workflow="other",
    )
    assert len(reasons) >= 3


def test_a_CLEAN_request_suggests():
    """The discipline must not veto everything — a def that never fires is the mirror failure."""
    ok, reasons = may_suggest("deploy to staging", meta(), available=set())
    assert ok is True
    assert reasons == []


# ── the metadata lint ──


def test_ONE_trigger_phrase_is_flagged():
    """One phrase is a keyword, not a trigger surface."""
    findings = lint_metadata(meta(match_text="deploy"))
    assert any(f"{MIN_TRIGGERS}-{MAX_TRIGGERS}" in f for f in findings)


def test_TOO_MANY_trigger_phrases_are_flagged():
    """A def matching this many fires on adjacent work, which is the failure that made manual-first
    the default."""
    many = ", ".join(f"phrase {i}" for i in range(MAX_TRIGGERS + 3))
    findings = lint_metadata(meta(match_text=many))
    assert any("exceeds" in f for f in findings)


def test_a_PROSE_trigger_is_flagged():
    """A sentence matches everything weakly and nothing strongly."""
    findings = lint_metadata(meta(match_text="when you want to deploy the thing, ship it"))
    assert any("reads as prose" in f for f in findings)


def test_an_OVERLONG_summary_is_flagged():
    """A summary that runs long is a description, and `when_to_use` is where a description goes."""
    findings = lint_metadata(meta(summary="x" * (MAX_SUMMARY_CHARS + 1)))
    assert any("summary is" in f for f in findings)


def test_an_OVERLONG_when_to_use_is_flagged():
    findings = lint_metadata(meta(when_to_use="y" * (MAX_WHEN_TO_USE_CHARS + 1)))
    assert any("when_to_use is" in f for f in findings)


@pytest.mark.parametrize(
    "text",
    ["First, do this. Then that.", "Step 1: open the file", "1) run it, then check"],
)
def test_a_when_to_use_that_SUMMARIZES_STEPS_is_flagged(text):
    """A reader who can infer the procedure from metadata will follow the metadata, which is
    stale by construction.
    """
    assert any("summarize the STEPS" in f for f in lint_metadata(meta(when_to_use=text)))


def test_PASSIVE_mode_with_no_digest_is_flagged():
    """It would surface as an empty guidance block, which reads as the system having nothing to
    say.
    """
    findings = lint_metadata(meta(surface_mode=SurfaceMode.PASSIVE, agent_digest=""))
    assert any("has none" in f for f in findings)


def test_an_OFF_def_is_not_linted_for_triggers():
    """Off is a decision. Demanding trigger phrases from a def nobody wants to surface would make
    the lint noise that hides the real findings.
    """
    findings = lint_metadata(meta(match_text="", surface_mode=SurfaceMode.OFF, agent_digest=""))
    assert not any("trigger" in f for f in findings)


def test_a_CONFORMING_def_lints_clean():
    assert lint_metadata(meta()) == []


@pytest.mark.parametrize(
    "text",
    [
        "deploy to staging, ship the release",
        "back up the db, run a backup",
        "review the PR, check the diff",
        "rotate the api key, refresh a token",
    ],
)
def test_a_NORMAL_trigger_containing_an_article_is_not_prose(text):
    """Measured: an earlier detector listed bare articles and flagged "ship the release" — an
    entirely normal trigger. A detector that fires on correct input is one an author switches
    off, taking the real findings with it.
    """
    assert not any("prose" in f for f in lint_metadata(meta(match_text=text)))


@pytest.mark.parametrize(
    "text",
    [
        "when you want to deploy the thing, ship it",
        "run this in order to publish the release, ship it",
        "a very long trigger phrase that goes on and on forever, ship it",
    ],
)
def test_GENUINE_prose_is_still_flagged(text):
    """Subordinating phrases and sheer length are what distinguish a sentence from a phrase — not
    the presence of an article.
    """
    assert any("prose" in f for f in lint_metadata(meta(match_text=text)))


# ── collision checking ──


def test_a_trigger_COLLISION_is_reported():
    """Two defs answering one phrase means the matcher picks one and the author cannot tell
    which.
    """
    findings = collisions(
        "deploy to staging, ship it", {"other-def": "deploy to staging, roll back"}
    )
    assert len(findings) == 1
    assert "other-def" in findings[0]
    assert "deploy to staging" in findings[0]


def test_collision_matching_is_NORMALIZED():
    """ "Deploy To Staging" and "deploy to staging" are the same phrase to a matcher, so they must
    be the same phrase to the collision check.
    """
    assert collisions("Deploy  To  Staging", {"other": "deploy to staging"})


def test_NEAR_neighbours_are_not_collisions():
    """A fuzzy check here would report near-neighbours as conflicts and get switched off, taking the
    real collisions with it."""
    assert collisions("deploy to production", {"other": "deploy to staging"}) == []


def test_a_NEGATIVE_trigger_does_not_collide():
    """Two defs vetoing on the same phrase is not a conflict — neither of them fires on it."""
    assert collisions("!dry run, deploy", {"other": "!dry run, rollback"}) == []


def test_no_existing_defs_means_no_collisions():
    assert collisions("deploy to staging", {}) == []


# ── one source, two wrappers ──


def test_the_digest_is_rendered_VERBATIM_inside_fences():
    """A model-paraphrased do/don't rule is a rule nobody wrote, and it gets paraphrased toward
    whatever the model was already inclined to do — the behaviour the rule existed to change."""
    rendered = render_passive(meta(), name="staging-deploy")
    assert DIGEST in rendered
    assert DIGEST_BEGIN in rendered
    assert DIGEST_END in rendered


def test_the_SUGGEST_render_CONTAINS_the_passive_render():
    """One source, two wrappers. A forked copy would drift silently — both renders look
    plausible, and nobody compares them.
    """
    passive = render_passive(meta(), name="staging-deploy")
    suggest = render_suggest(meta(), name="staging-deploy")
    assert passive in suggest


def test_the_drift_check_finds_NOTHING_for_a_normal_def():
    assert drift(meta(), name="staging-deploy") == []


def test_the_suggestion_carries_the_CALL():
    suggest = render_suggest(meta(), name="staging-deploy", inputs={"env": "staging"})
    assert 'workflow_start(name="staging-deploy"' in suggest
    assert "'env': 'staging'" in suggest or '"env": "staging"' in suggest


def test_a_def_with_NO_digest_renders_no_passive_block():
    """An empty labelled block reads as the system having nothing to say, which is worse than saying
    nothing."""
    assert render_passive(meta(agent_digest=""), name="x") == ""


def test_a_def_with_no_digest_STILL_gets_a_suggestion():
    """The suggestion's value is the CALL. Withholding it for want of guidance text would hide a
    runnable workflow behind a documentation gap."""
    suggest = render_suggest(meta(agent_digest=""), name="staging-deploy")
    assert "workflow_start" in suggest


def test_the_fence_markers_round_trip():
    """The stripper ships WITH the writer so both agree on the marker text — a reader with its
    own copy would leave a stray comment in a prompt the first time either changed.
    """
    rendered = render_passive(meta(), name="x")
    assert DIGEST_BEGIN not in strip_digest_fence(rendered)
    assert DIGEST in strip_digest_fence(rendered)


def test_the_summary_rides_the_passive_header():
    assert "Run the staging deploy checklist" in render_passive(meta(), name="x")


# ── SOP migration ──


def test_a_migrated_SOP_keeps_PASSIVE_surfacing():
    result = migrate_sop({"name": "backup", "triggers": "back up the db, run a backup"})
    assert result.metadata.surface_mode is SurfaceMode.PASSIVE


def test_a_migration_does_NOT_grant_suggest():
    """Proposing execution is a new capability the user never enabled, and granting it during a
    migration would be the migration deciding on their behalf."""
    result = migrate_sop({"name": "backup", "triggers": "a b, c d"})
    assert result.metadata.surface_mode is not SurfaceMode.SUGGEST


def test_an_auto_surface_FALSE_sop_migrates_to_off():
    """That is what the user already said."""
    result = migrate_sop({"name": "quiet", "auto_surface": False, "triggers": "a b, c d"})
    assert result.metadata.surface_mode is SurfaceMode.OFF


def test_a_migration_REPORTS_what_it_assumed():
    """A migration that silently normalized something is a migration nobody can audit — and the
    SOP is a document the user wrote.
    """
    result = migrate_sop({"name": "backup", "triggers": "a b, c d"})
    assert any("execution suggestion is NOT granted" in f for f in result.findings)


def test_a_migration_carries_the_description_into_the_DIGEST():
    """Passive mode injects the digest, so a migrated SOP with no digest would surface empty."""
    result = migrate_sop(
        {"name": "backup", "triggers": "a b, c d", "description": "never skip the checksum"}
    )
    assert "never skip the checksum" in result.metadata.agent_digest


def test_a_migration_runs_the_LINT():
    result = migrate_sop({"name": "backup", "triggers": "backup"})
    assert any("trigger" in f for f in result.findings)


def test_a_migration_TRUNCATES_an_overlong_summary_rather_than_refusing():
    """Refusing the migration would lose the SOP over a formatting rule."""
    result = migrate_sop({"name": "x", "triggers": "a b, c d", "summary": "s" * 500})
    assert len(result.metadata.summary) == MAX_SUMMARY_CHARS


# ── per-def graduation ──


def test_graduation_is_PER_DEF():
    """A def earns execution-suggestion mode individually, which is what makes incremental trust
    possible. A global switch would grant it to every def the moment one proved itself."""
    promoted, error = graduate(meta(surface_mode=SurfaceMode.PASSIVE))
    assert error == ""
    assert promoted.surface_mode is SurfaceMode.SUGGEST


def test_graduation_PRESERVES_everything_else():
    original = meta(surface_mode=SurfaceMode.PASSIVE, cadence_days=30, scope="workspace")
    promoted, _ = graduate(original)
    assert promoted.cadence_days == 30
    assert promoted.scope == "workspace"
    assert promoted.agent_digest == original.agent_digest


def test_a_def_with_ONE_trigger_cannot_graduate():
    """Promoting it would turn earned trust into firing on everything."""
    promoted, error = graduate(meta(match_text="deploy", surface_mode=SurfaceMode.PASSIVE))
    assert promoted is None
    assert "trigger phrases" in error


def test_an_ALREADY_suggesting_def_is_refused_not_re_promoted():
    promoted, error = graduate(meta(surface_mode=SurfaceMode.SUGGEST))
    assert promoted is None
    assert "already" in error


# ── the reachability doctor ──


def test_an_UNREACHABLE_def_is_reported():
    """The mirror failure of over-firing, with a real number behind it: an audit found 63 silently
    unreachable skills on first run. A def nobody can reach is a def whose author believes it works.
    """
    findings = unreachable({"ghost": SurfacingMeta(surface_mode=SurfaceMode.PASSIVE)})
    assert len(findings) == 1
    assert "ghost" in findings[0]
    assert "never surface" in findings[0]


def test_an_OFF_def_is_NOT_reported_as_unreachable():
    """Off is a decision. Reporting every deliberately-disabled def would bury the real findings."""
    assert unreachable({"quiet": SurfacingMeta(surface_mode=SurfaceMode.OFF)}) == []


def test_a_REACHABLE_def_is_not_reported():
    assert unreachable({"fine": meta()}) == []


def test_the_doctor_checks_every_def():
    findings = unreachable(
        {
            "ghost": SurfacingMeta(surface_mode=SurfaceMode.SUGGEST),
            "fine": meta(),
            "also-ghost": SurfacingMeta(surface_mode=SurfaceMode.PASSIVE),
        }
    )
    assert len(findings) == 2


# ── the metadata round trip ──


def test_every_field_round_trips():
    original = SurfacingMeta(
        match_text="a b, c d, !skip",
        summary="s",
        when_to_use="w",
        agent_digest="d",
        surface_mode=SurfaceMode.SUGGEST,
        freedom_level=FreedomLevel.LOW,
        preconditions=[{"kind": "file", "path": "x"}],
        requirements=["git"],
        cadence_days=30,
        revisit_window_days=90,
        scope="workspace",
        scope_ref="proj-1",
    )
    assert SurfacingMeta.from_dict(original.to_dict()) == original


def test_an_unknown_FREEDOM_level_reads_as_medium():
    """Medium is the middle: erring high would loosen gate strictness on a def whose author meant to
    tighten it, and erring low would make an exploratory def rigid."""
    assert (
        SurfacingMeta.from_dict({"freedom_level": "chaotic"}).freedom_level is FreedomLevel.MEDIUM
    )


def test_there_is_no_guidance_LIFECYCLE_field_to_declare():
    """WF2TAS-12: `lifecycle` (`one_shot`/`session`/`until_deactivated`) was deleted, because the
    persistence it described had nowhere to happen.

    Measured: nothing in `src/` reads `agent_digest`, and `render_passive` has no production caller,
    so the passive channel this field paced does not exist yet — `test_learning_ambient` calls it an
    "unbuilt producer" in as many words. `DefMetadata` had no `lifecycle` field either, so no
    authored def could set it and `meta_from_def` could not carry it. Three members, one reachable
    default, zero observable difference. Deleted rather than documented: a field that only looks
    configurable teaches an author to declare it and wonder why nothing changed. The rebuild recipe
    (what a consumer must exist first) is recorded in the WORKFLOWS-V2-TASKS-SOPS execution log.
    """
    import dataclasses as dc

    assert "lifecycle" not in {f.name for f in dc.fields(SurfacingMeta)}
    # A def file hand-edited to carry the old key still loads: `from_dict` names what it reads, so
    # a dropped field is ignored rather than a crash on a def somebody already wrote.
    assert (
        SurfacingMeta.from_dict({"lifecycle": "until_deactivated", "summary": "s"}).summary == "s"
    )


def test_a_PRE_EXISTING_def_with_none_of_these_keys_loads():
    """Additive with empty defaults: a def written before this session must read back as OFF with no
    triggers, which is exactly today's behaviour for a def that never surfaced."""
    restored = SurfacingMeta.from_dict({})
    assert restored.surface_mode is SurfaceMode.OFF
    assert restored.match_text == ""
    assert restored.preconditions == []


def test_a_MALFORMED_precondition_entry_is_dropped():
    """A non-dict precondition cannot be evaluated, and keeping it would make the gate raise on a
    def that otherwise works."""
    restored = SurfacingMeta.from_dict({"preconditions": [{"kind": "file"}, "junk", None]})
    assert restored.preconditions == [{"kind": "file"}]


# ── the scope vocabulary is shared ──


def test_the_scope_words_reuse_S45s_ladder():
    """Two ladders would disagree about promotion order, and the disagreement would show up as a
    candidate promoted past a tier nobody meant to skip."""
    from personalclaw.workflows.template_pipeline import SCOPE_LADDER

    assert SurfacingMeta().scope in SCOPE_LADDER
    for word in SCOPE_LADDER:
        assert SurfacingMeta.from_dict({"scope": word}).scope == word

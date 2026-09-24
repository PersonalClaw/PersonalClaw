"""LV-5 — the S3 refinement arm: stumble detector → refine proposal → diff → versioned accept.

The arm is a CLASSIFIER in front of the existing proposal queue, so the negatives are tested as
hard as the positives: every "fires on" case below has a paired "and does not fire when …" that
differs in exactly one input. Two of those pairs are the ones that matter most —

* an **environment failure** never produces a refinement (a flaky network must not harden into
  procedure), and
* a turn with **no skill loaded** never produces one (there is nothing to refine, and a target
  invented from the candidate index would name a skill that had no part in the turn).

The diff is not asserted to "look like a diff": ``test_diff_is_exactly_what_accept_applies``
accepts the proposal for real and asserts the resulting skill body equals the diff's own after
side, so the previewed patch and the applied patch cannot drift.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from personalclaw import after_turn_review as atr
from personalclaw.skills import loader as loader_mod
from personalclaw.skills import overlays, proposals, refine
from personalclaw.skills.loader import SkillsLoader

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
SKILL = "release-flow"
BASE = "---\nname: release-flow\ndescription: Ship a release\n---\n\nRun `pip install`.\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated skills home.

    ``loader_mod.config_dir`` is the ONE binding both stores resolve through:
    ``proposals._proposals_dir`` and ``overlays.overlays_dir`` each re-read it per call
    (deliberately — see their comments), so patching it here redirects the queue AND the
    overlays. Asserted below rather than assumed.
    """
    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)
    import personalclaw.skills.marketplace as mp

    monkeypatch.setattr(mp, "SKILL_DISCOVERY_PATHS", [])
    assert tmp_path in proposals._proposals_dir().parents, "the proposal queue was NOT redirected"
    assert tmp_path in overlays.overlays_dir().parents, "the overlay store was NOT redirected"
    return tmp_path


def _install(home, name: str = SKILL, body: str = BASE) -> None:
    d = home / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")


def _load(name: str = SKILL) -> str | None:
    return SkillsLoader(install_builtins=False).load_skill(name)


# ── T3.1 the classifier: one test per trigger, each paired with its negative ──────────────


def test_correction_fires_and_the_same_turn_without_a_skill_does_not():
    kw = dict(user_message="No, use uv instead of pip.", assistant_text="Ran pip install.")
    fired = atr.detect_stumble(used_skills=[SKILL], **kw)
    assert fired is not None and fired.trigger == "correction"
    # VACUITY of the used_skills guard: the ONLY difference is the loaded set.
    assert atr.detect_stumble(used_skills=[], **kw) is None


def test_failure_retry_fires_only_when_the_tool_was_actually_retried():
    retried = atr.detect_stumble(
        user_message="carry on",
        assistant_text="Retried and it worked.",
        used_skills=[SKILL],
        tool_outcomes=[("shell", "failed"), ("shell", "success")],
    )
    assert retried is not None and (retried.trigger, retried.detail) == ("failure_retry", "shell")
    # A failure NOTHING followed is an abandoned step, not a worked-around procedure gap.
    assert (
        atr.detect_stumble(
            user_message="carry on",
            assistant_text="It failed.",
            used_skills=[SKILL],
            tool_outcomes=[("read_file", "success"), ("shell", "failed")],
        )
        is None
    )


def test_rejection_fires_only_when_the_denial_stood():
    stood = atr.detect_stumble(
        user_message="carry on",
        assistant_text="Understood.",
        used_skills=[SKILL],
        tool_outcomes=[("write_file", "denied")],
    )
    assert stood is not None and (stood.trigger, stood.detail) == ("rejection", "write_file")
    # Denied then succeeded = the user steered a parameter, not refused the procedure.
    assert (
        atr.detect_stumble(
            user_message="carry on",
            assistant_text="Understood.",
            used_skills=[SKILL],
            tool_outcomes=[("write_file", "denied"), ("write_file", "success")],
        )
        is None
    )


def test_an_ordinary_successful_turn_never_stumbles():
    assert (
        atr.detect_stumble(
            user_message="summarize the release notes and don't include code",
            assistant_text="Here is the summary.",
            used_skills=[SKILL],
            tool_outcomes=[("read_file", "success"), ("shell", "success")],
        )
        is None
    ), "a mid-sentence negation is a task instruction, not a correction"


@pytest.mark.parametrize(
    "user_message,assistant_text",
    [
        ("no, the shell tool is broken here", "Ran pip install."),
        ("No, that is wrong.", "The command failed: connection refused."),
    ],
)
def test_the_env_failure_fixture_never_triggers(user_message, assistant_text):
    """Either side of the turn reading as an environment failure kills the whole arm.

    Both fixtures carry a REAL correction signal, so the only thing suppressing them is the
    env-failure guardrail — which is what makes this a test of the guardrail rather than a test
    that a boring turn is boring.
    """
    assert atr.is_correction_signal(user_message), "fixture must carry a correction signal"
    assert (
        atr.detect_stumble(
            user_message=user_message,
            assistant_text=assistant_text,
            used_skills=[SKILL],
            tool_outcomes=[("shell", "failed"), ("shell", "failed")],
        )
        is None
    )


def test_the_trigger_vocabulary_is_closed_and_mapped_everywhere():
    """Every trigger the classifier can emit has a body, a description and a rendered phrase.

    A trigger with no body proposes nothing (silently); one with no phrase renders a refinement
    the user cannot attribute. Both directions are asserted, so ADDING a trigger without its
    text reds, and adding text for a trigger that cannot fire reds too.
    """
    triggers = set(atr.STUMBLE_TRIGGERS)
    assert triggers == set(refine._BODY), "trigger↔body drift"
    assert triggers == set(refine._DESCRIPTION), "trigger↔description drift"
    assert triggers == set(overlays._TRIGGER_PHRASE), "trigger↔rendered-phrase drift"
    assert len(triggers) == 3


def test_the_arm_never_calls_a_model():
    """The refinement arm's degraded floor is proposing NOTHING, not guessing.

    Asserted structurally because it is a property of the module, not of one path through it:
    no provider entry point may appear in its source at all.
    """
    src = inspect.getsource(refine)
    for forbidden in ("one_shot_completion", "llm_helpers", "ModelProvider", "completion("):
        assert forbidden not in src, f"{forbidden} reached the model-free refinement arm"


# ── T3.2 the proposal + its diff ──────────────────────────────────────────────────────────


def _propose(home, *, now=NOW, user_message="No, use uv instead of pip."):
    return refine.propose_refinement(
        trigger="correction",
        skill=SKILL,
        user_message=user_message,
        session_key="sess-1",
        now=now,
    )


def test_a_stumble_yields_exactly_one_refine_proposal_with_a_valid_diff(home):
    _install(home)
    prop = _propose(home)
    assert prop is not None
    pending = proposals.list_pending()
    assert len(pending) == 1, "a stumble files exactly one proposal"
    assert (pending[0].kind, pending[0].refine_target, pending[0].trigger) == (
        "refine",
        SKILL,
        "correction",
    )
    diff = refine.proposal_diff(prop)
    assert diff.startswith(f"--- {SKILL}/SKILL.md"), diff[:80]
    assert "@@" in diff
    added = [ln for ln in diff.split("\n") if ln.startswith("+") and not ln.startswith("+++")]
    assert any("## Refinement v1" in ln for ln in added), added
    assert any("use uv instead of pip" in ln for ln in added), added
    # The base file is untouched by PROPOSING — propose-don't-write.
    assert (home / "skills" / SKILL / "SKILL.md").read_text(encoding="utf-8") == BASE


def test_diff_is_exactly_what_accept_applies(home):
    """The previewed patch IS the applied patch — not a description of it.

    Reconstructing the after side from the diff's own ``+``/context lines and comparing it to
    the post-accept body is the only assertion that can catch the two drifting apart. A diff
    built by a second renderer would pass "looks like a unified diff" and fail here.
    """
    _install(home)
    prop = _propose(home)
    assert prop is not None
    diff = refine.proposal_diff(prop)
    before = _load()
    result = proposals.accept(prop.id)
    after = _load()
    assert result.name == SKILL and result.version == 1
    reconstructed = _apply_patch(before, diff)
    assert reconstructed == after, "the diff shown to the user is not the change accept made"


def _apply_patch(original: str, diff: str) -> str:
    """Apply a single-file unified diff by hunk. Deliberately minimal and strict.

    Strict on purpose: a hunk whose context does not match raises, so a diff that describes a
    different base cannot quietly "apply".
    """
    lines = original.split("\n")
    out: list[str] = []
    cursor = 0
    it = iter(diff.split("\n"))
    for raw in it:
        if not raw.startswith("@@"):
            continue
        start = int(raw.split("-", 1)[1].split(",", 1)[0].split(" ", 1)[0]) - 1
        out.extend(lines[cursor:start])
        cursor = start
        for body in it:
            if body.startswith("@@") or body == "":
                break
            if body.startswith("+"):
                out.append(body[1:])
            elif body.startswith("-"):
                assert lines[cursor] == body[1:], f"context mismatch at {cursor}"
                cursor += 1
            elif body.startswith(" "):
                assert lines[cursor] == body[1:], f"context mismatch at {cursor}"
                out.append(lines[cursor])
                cursor += 1
    out.extend(lines[cursor:])
    return "\n".join(out)


def test_the_daily_cap_holds_across_the_accept_that_empties_the_queue(home):
    """One refine per skill per day — counting the ACCEPTED one, not just the pending one.

    The second half is the real defect: ``accept`` deletes the queue entry, so a cap that read
    only the queue would let a second proposal through the moment the user approved the first,
    i.e. exactly when they were paying attention.
    """
    _install(home)
    assert _propose(home) is not None
    assert _propose(home, now=NOW + timedelta(hours=1)) is None, "pending half of the cap"
    assert len(proposals.list_pending()) == 1

    proposals.accept(proposals.list_pending()[0].id)
    assert proposals.list_pending() == []
    assert _propose(home, now=NOW + timedelta(hours=2)) is None, "accepted half of the cap"
    # …and the window really is a window: past it, the arm proposes again.
    assert _propose(home, now=NOW + timedelta(hours=25)) is not None


def test_a_vanished_skill_proposes_nothing(home):
    assert _propose(home) is None, "there is nothing to refine"
    assert proposals.list_pending() == []


# ── T3.3 versioned acceptance ─────────────────────────────────────────────────────────────


def test_two_accepted_refinements_are_distinguishable_by_version(home):
    """Versioned acceptance, with a READER that tells the two apart.

    Both refinements are stamped the SAME DAY on purpose: the date alone cannot distinguish
    them, so if the version were dropped from the rendered heading this test reds while a
    date-only assertion would still pass.
    """
    _install(home)
    first = _propose(home)
    assert first is not None
    assert proposals.accept(first.id).version == 1
    second = refine.propose_refinement(
        trigger="rejection",
        detail="write_file",
        skill=SKILL,
        user_message="stop",
        session_key="sess-2",
        now=NOW + timedelta(hours=25),
    )
    assert second is not None
    assert proposals.accept(second.id).version == 2

    body = _load()
    assert "## Refinement v1" in body and "## Refinement v2" in body
    # The trigger rides through accept into the rendered heading, so each version says WHY.
    assert "from a correction" in body and "from a rejected action" in body
    # …and the overlay is still ONE file, so revert is still one unlink.
    assert len(list((home / "skills" / ".overlays").rglob("*.json"))) == 1
    stored = json.loads((home / "skills" / ".overlays" / f"{SKILL}.json").read_text())
    assert [r["trigger"] for r in stored["refinements"]] == ["correction", "rejection"]


def test_reject_leaves_the_skill_untouched(home):
    _install(home)
    prop = _propose(home)
    assert prop is not None
    assert proposals.reject(prop.id) is True
    assert (home / "skills" / SKILL / "SKILL.md").read_text(encoding="utf-8") == BASE
    assert not (home / "skills" / ".overlays").exists(), "reject must not write an overlay"
    assert _load() == BASE
    assert overlays.next_version(SKILL) == 1, "a rejected refinement consumed no version"


def test_the_detail_route_serves_the_diff_and_the_version_it_would_create(home):
    """The diff has to be ON the route, derived per request.

    ``version`` is the version accept WOULD write, so it moves after an accept — asserted,
    because a constant '1' would be indistinguishable from a correct first reading.
    """
    import asyncio

    from personalclaw.dashboard.handlers import skills as skills_handlers

    class _Req:
        def __init__(self, pid: str) -> None:
            self.match_info = {"id": pid}

    _install(home)
    prop = _propose(home)
    assert prop is not None

    def _detail(pid: str) -> dict:
        resp = asyncio.run(skills_handlers.api_skill_proposal_detail(_Req(pid)))
        return json.loads(resp.text)

    payload = _detail(prop.id)
    assert payload["version"] == 1
    assert "@@" in payload["diff"]
    assert payload["trigger"] == "correction"

    proposals.accept(prop.id)
    second = refine.propose_refinement(
        trigger="correction",
        skill=SKILL,
        user_message="No, tag the release first.",
        session_key="sess-3",
        now=NOW + timedelta(hours=25),
    )
    assert second is not None
    assert _detail(second.id)["version"] == 2, "the served version must track the store"


def test_a_new_kind_proposal_carries_no_diff_and_no_version(home):
    """The derived fields are refine-only: a ``kind="new"`` accept creates, it does not version."""
    import asyncio

    from personalclaw.dashboard.handlers import skills as skills_handlers

    class _Req:
        def __init__(self, pid: str) -> None:
            self.match_info = {"id": pid}

    prop = proposals.enqueue(
        slug="brand-new",
        description="A new skill",
        triggers="x",
        procedure_md="Do the thing.",
        session_key="s",
        created_at=NOW.isoformat(timespec="seconds"),
    )
    assert prop is not None
    payload = json.loads(asyncio.run(skills_handlers.api_skill_proposal_detail(_Req(prop.id))).text)
    assert "diff" not in payload and "version" not in payload
    assert proposals.accept(prop.id).version == 0


def test_the_proposal_detail_route_is_registered(home):
    """A defined handler is not a reachable one.

    The skills routes are registered inline inside ``start_dashboard``, which cannot be driven
    without starting a server — so this scans the registration source. Comment lines are
    stripped first (a text scanner otherwise reads a commented-out route as a live one), and a
    fabricated path is asserted ABSENT so the scan cannot pass vacuously.
    """
    from personalclaw.dashboard import server as server_mod

    src = inspect.getsource(server_mod.start_dashboard)
    code = "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("#"))
    for expected in (
        'add_get("/api/skills/proposals/{id}", api_skill_proposal_detail)',
        'add_post("/api/skills/proposals/{id}/accept", api_skill_proposal_accept)',
    ):
        assert expected in code, f"{expected} is not registered — the endpoint would 404"
    assert 'add_get("/api/skills/proposals/{id}/diff"' not in code, "vacuity floor"


# ── The call site (a mechanism nothing calls is not a feature) ─────────────────────────────


class _State:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    def broadcast_ws(self, name: str, payload: dict) -> None:
        self.sent.append((name, payload))


class _Session:
    key = "sess-1"

    def __init__(self, used) -> None:
        self._skills_used = used


class _Cfg:
    skill_ladder = True
    surface_chip = True


def test_the_after_turn_seam_calls_the_stumble_arm():
    """Static half: the seam's own source calls it.

    Scoped to ``_maybe_after_turn_review``'s source rather than the whole file, so moving the
    call OUT of the seam into dead code at module level reds. The absent-name assertion is the
    vacuity floor for the scan itself.
    """
    from personalclaw.dashboard import chat_runner

    src = inspect.getsource(chat_runner._maybe_after_turn_review)
    assert "_maybe_refine_stumble(" in src, "the stumble arm is never reached from the seam"
    assert "_maybe_refine_nothing(" not in src, "vacuity floor"


def test_the_call_site_files_a_proposal_and_surfaces_the_existing_learned_chip(home):
    """Runtime half: driving the real call site files a real proposal and emits ONE chip.

    The chip is asserted to be the LV-2 ``activity_event``/``origin: proposal`` shape, not a new
    channel: that origin is what makes the chip's tap-through land on the proposals surface,
    where the diff renders.
    """
    from personalclaw.dashboard import chat_runner

    _install(home)
    state = _State()
    chat_runner._maybe_refine_stumble(
        state,
        _Session([{"name": SKILL, "state": "admitted", "loaded_tokens": 10}]),
        "No, use uv instead of pip.",
        "Ran pip install.",
        [],
        _Cfg(),
    )
    assert len(proposals.list_pending()) == 1
    assert len(state.sent) == 1
    name, payload = state.sent[0]
    assert name == "activity_event"
    assert (payload["kind"], payload["origin"]) == ("learned", "proposal")
    assert SKILL in payload["text"]


def test_the_call_site_is_silent_when_nothing_was_loaded(home):
    """Same turn, no loaded skill: no proposal, no chip. The vacuity floor for the test above."""
    from personalclaw.dashboard import chat_runner

    _install(home)
    state = _State()
    chat_runner._maybe_refine_stumble(
        state, _Session([]), "No, use uv instead of pip.", "Ran pip install.", [], _Cfg()
    )
    assert proposals.list_pending() == []
    assert state.sent == []


# ── V3: the full arc ──────────────────────────────────────────────────────────────────────


def test_v3_arc_flawed_skill_stumble_refine_approve_rerun(home):
    """flawed skill → stumble → refine proposal → approve → the re-run loads the fix.

    Inspected between every step, and the base file is compared byte-for-byte at the end: the
    whole arc must leave ``SKILL.md`` exactly as it was found (propose-don't-write, and
    WF2LEA-6's immutable base).
    """
    from personalclaw.dashboard import chat_runner

    _install(home)
    original_bytes = (home / "skills" / SKILL / "SKILL.md").read_bytes()

    # 1. the flawed skill is what a run would load — no refinement in sight.
    assert "Run `pip install`." in _load()
    assert "uv" not in _load()

    # 2. the turn that stumbles on it, driven through the real call site.
    state = _State()
    chat_runner._maybe_refine_stumble(
        state,
        _Session([{"name": SKILL, "state": "admitted", "loaded_tokens": 10}]),
        "No, never use pip here — use uv.",
        "Ran `pip install` as the skill said.",
        [],
        _Cfg(),
    )
    pending = proposals.list_pending()
    assert len(pending) == 1 and pending[0].kind == "refine"
    # The heading's date is the PROPOSAL's own `created_at` day — `overlays.render_block`
    # splits that timestamp at "T". This arc mints its proposal through the real clock, not
    # this module's frozen NOW, so derive the day instead of hardcoding one: a literal here
    # agreed with the real clock only while UTC's date matched NOW's, and broke the moment
    # UTC rolled past it. Capture before accept(), which consumes the proposal.
    day = pending[0].created_at.split("T", 1)[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", day), f"no ISO day to pin the heading on: {day!r}"
    # 3. nothing was written yet: the proposal is a proposal.
    assert (home / "skills" / SKILL / "SKILL.md").read_bytes() == original_bytes
    assert _load() == BASE

    # 4. approve — the one human action, and the only writer.
    result = proposals.accept(pending[0].id)
    assert (result.name, result.version) == (SKILL, 1)

    # 5. the re-run loads the refined skill.
    refined = _load()
    assert "use uv" in refined
    assert f"## Refinement v1 ({day}, from a correction)" in refined
    assert "Run `pip install`." in refined, "the base procedure is still there"
    # …and the base file was never touched by any of it.
    assert (home / "skills" / SKILL / "SKILL.md").read_bytes() == original_bytes
    assert proposals.list_pending() == []

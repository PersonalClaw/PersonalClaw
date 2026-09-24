"""The self-updating project-context review (LEARN E1.4 — WF2LEA-12).

The five clauses of the contract, each pinned against the REAL proposal queue, HierarchyStore and
SkillsLoader (monkeypatched to a tmp home), not hand-built state:

1. a review emits `project_instruction` / `project_file` / `project_skill` proposals with a
   per-item rationale into the §2.2 queue;
2. nothing is written until accepted — a review mutates no project context;
3. accepting applies EXACTLY the accepted item (one instruction append / one file / one skill),
   leaving pending and rejected siblings untouched;
4. a rejected item is NOT re-proposed on a second review (the queue's decision-memory block);
5. the trigger is prompt-only — the review runs from a chat tool, and nothing on a turn path
   calls it automatically.
"""

from __future__ import annotations

import pytest

from personalclaw.learning import project_context_review as pcr
from personalclaw.learning import proposals as P
from personalclaw.learning.proposals import Kind, Status
from personalclaw.tasks.hierarchy import HierarchyStore


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Point the queue, projects and skills at a tmp home. NEVER the real one — these WRITE.

    `PERSONALCLAW_HOME` is set (not just the loader symbol patched) because `HierarchyStore` and
    `SkillsLoader` bind `config_dir` at import; only the env var — which `config_dir()` re-reads
    live every call — isolates every store this review touches, not just the proposal queue.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)
    monkeypatch.setattr(P, "_audit", lambda operation, prop, outcome: None)
    return tmp_path


@pytest.fixture
def project():
    """A real project in the tmp home, so the installer writes to a resolvable target."""
    store = HierarchyStore()
    return store.create_project(name="Demo", brief="a demo project")


def _candidates():
    return [
        pcr.ReviewCandidate(
            kind=Kind.PROJECT_INSTRUCTION.value,
            body="Always run `make lint` before committing.",
            rationale="We agreed lint must pass pre-commit",
        ),
        pcr.ReviewCandidate(
            kind=Kind.PROJECT_FILE.value,
            body="# Conventions\nUse tabs, not spaces.",
            rationale="Captured the style rule the user stated",
            name="conventions.md",
        ),
        pcr.ReviewCandidate(
            kind=Kind.PROJECT_SKILL.value,
            body="---\nname: deploy\n---\nRun npm run deploy.",
            rationale="The deploy steps we just walked through",
            name="deploy-demo",
        ),
    ]


# ── clause 1: typed proposals with rationale into the queue ──


def test_review_emits_the_three_typed_proposals_with_rationale(project):
    filed = pcr.project_context_review(_candidates(), project_id=project.id)
    assert {p.kind for p in filed} == {
        Kind.PROJECT_INSTRUCTION.value,
        Kind.PROJECT_FILE.value,
        Kind.PROJECT_SKILL.value,
    }
    # The rationale rides as the title — what a reviewer reads in the queue before deciding.
    titles = {p.title for p in filed}
    assert "We agreed lint must pass pre-commit" in titles
    assert all(p.status == Status.PENDING.value for p in filed)
    assert len(P.list_pending()) == 3


def test_a_candidate_without_a_rationale_is_dropped(project):
    filed = pcr.project_context_review(
        [pcr.ReviewCandidate(kind=Kind.PROJECT_INSTRUCTION.value, body="x", rationale="")],
        project_id=project.id,
    )
    assert filed == []
    assert P.list_pending() == []


def test_no_project_id_files_nothing(project):
    assert pcr.project_context_review(_candidates(), project_id="") == []
    assert P.list_pending() == []


# ── clause 2: nothing written until accepted ──


def test_review_writes_no_project_context(project):
    pcr.project_context_review(_candidates(), project_id=project.id)
    # The project's instructions are unchanged, no context file exists, no skill was created.
    fresh = HierarchyStore().get_project(project.id)
    assert not (fresh.agent_instructions_template or "").strip()
    context_dir = HierarchyStore().context_dir(project.id)
    assert not (context_dir / "conventions.md").exists()

    from personalclaw.skills.loader import SkillsLoader

    assert SkillsLoader().load_skill("deploy-demo") is None


# ── clause 3: accepting applies EXACTLY the accepted item ──


def test_accepting_the_instruction_appends_only_it(project):
    filed = pcr.project_context_review(_candidates(), project_id=project.id)
    instr = next(p for p in filed if p.kind == Kind.PROJECT_INSTRUCTION.value)

    P.accept(instr.id, installer=_installer(), actor="user")

    fresh = HierarchyStore().get_project(project.id)
    assert "make lint" in fresh.agent_instructions_template
    # The file and skill proposals were NOT touched — per-item granularity.
    context_dir = HierarchyStore().context_dir(project.id)
    assert not (context_dir / "conventions.md").exists()
    from personalclaw.skills.loader import SkillsLoader

    assert SkillsLoader().load_skill("deploy-demo") is None
    # The other two rows remain pending for their own decision.
    assert {p.kind for p in P.list_pending()} == {
        Kind.PROJECT_FILE.value,
        Kind.PROJECT_SKILL.value,
    }


def test_accepting_the_file_writes_only_that_file(project):
    filed = pcr.project_context_review(_candidates(), project_id=project.id)
    pf = next(p for p in filed if p.kind == Kind.PROJECT_FILE.value)

    P.accept(pf.id, installer=_installer(), actor="user")

    context_dir = HierarchyStore().context_dir(project.id)
    written = (context_dir / "conventions.md").read_text(encoding="utf-8")
    assert "Use tabs" in written
    # Instructions untouched.
    assert not (HierarchyStore().get_project(project.id).agent_instructions_template or "").strip()


def test_accepting_the_skill_creates_only_that_skill(project):
    filed = pcr.project_context_review(_candidates(), project_id=project.id)
    ps = next(p for p in filed if p.kind == Kind.PROJECT_SKILL.value)

    P.accept(ps.id, installer=_installer(), actor="user")

    from personalclaw.skills.loader import SkillsLoader

    assert "npm run deploy" in (SkillsLoader().load_skill("deploy-demo") or "")


def test_an_instruction_appends_rather_than_replacing(project):
    HierarchyStore().update_project(project.id, agent_instructions_template="Existing rule.")
    filed = pcr.project_context_review(_candidates(), project_id=project.id)
    instr = next(p for p in filed if p.kind == Kind.PROJECT_INSTRUCTION.value)

    P.accept(instr.id, installer=_installer(), actor="user")

    merged = HierarchyStore().get_project(project.id).agent_instructions_template
    assert "Existing rule." in merged and "make lint" in merged


def test_an_unsafe_context_filename_fails_the_accept(project):
    filed = pcr.project_context_review(
        [
            pcr.ReviewCandidate(
                kind=Kind.PROJECT_FILE.value,
                body="x",
                rationale="traversal attempt",
                name="../escape.md",
            )
        ],
        project_id=project.id,
    )
    pf = filed[0]
    with pytest.raises(P.AcceptError):
        P.accept(pf.id, installer=_installer(), actor="user")


# ── clause 4: a rejected item is not re-proposed ──


def test_a_rejected_item_is_not_reproposed_on_a_second_review(project):
    first = pcr.project_context_review(_candidates(), project_id=project.id)
    instr = next(p for p in first if p.kind == Kind.PROJECT_INSTRUCTION.value)
    assert P.reject(instr.id, actor="user") is True

    # A second review of the SAME items: the rejected instruction is suppressed, the others re-file
    # or reinforce, but the declined one never returns to the queue.
    pcr.project_context_review(_candidates(), project_id=project.id)
    pending_kinds = [p.kind for p in P.list_pending()]
    assert Kind.PROJECT_INSTRUCTION.value not in pending_kinds


def test_an_accepted_item_is_not_reproposed(project):
    first = pcr.project_context_review(_candidates(), project_id=project.id)
    pf = next(p for p in first if p.kind == Kind.PROJECT_FILE.value)
    P.accept(pf.id, installer=_installer(), actor="user")

    again = pcr.project_context_review(_candidates(), project_id=project.id)
    assert Kind.PROJECT_FILE.value not in {p.kind for p in again}


# ── clause 5: prompt-only trigger, never automatic ──


def test_the_trigger_is_a_prompt_only_chat_tool():
    """The review is exposed as an mcp_core chat tool the agent must CALL — not a turn hook.

    A chat tool fires only when the agent invokes it in response to a user asking for a review; a
    per-turn observer (self_model_observer.observe_turn) fires every turn. The trigger being the
    former is what makes this prompt-only.
    """
    from personalclaw import mcp_core

    names = {t["name"] for t in mcp_core._list_tools()}
    assert "project_context_review" in names


def test_nothing_on_a_turn_path_calls_the_review_automatically():
    """No chat_runner / turn-loop / heartbeat call site invokes the review.

    The self-model observer is deliberately wired into chat_runner's per-turn path; the project-
    context review must NOT be, or it would stop being prompt-only. Guard the two turn-driven
    entry points by source scan.
    """
    import pathlib

    root = pathlib.Path(pcr.__file__).resolve().parents[1]
    for rel in ("dashboard/chat_runner.py", "heartbeat.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "project_context_review" not in text, rel


# ── shared installer, mirroring dashboard/handlers/learning.py::_installer_for ──


def _installer():
    def _install(prop):
        data = prop.to_dict()
        if pcr.is_project_context_proposal(data):
            pcr.install_accepted_project_context(data)

    return _install

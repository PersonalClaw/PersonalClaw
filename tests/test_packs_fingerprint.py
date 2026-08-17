"""Project-fingerprint auto-surfacing + the pack_owned update flow (AGENT-PACKS §7/§1, AP-7).

The done_when clauses here are almost all NEGATIVE properties — "no model call", "not on a
read", "installs nothing", "never re-nags", "does not clobber your edit" — and a negative is
only real if something fails when it stops holding. So each block below pins the negative:

* ``test_the_scanner_makes_no_model_call`` + ``test_the_scanner_imports_no_model_seam`` —
  zero-LLM proven twice: dynamically (the ``ModelCallGuard`` attempt-audit sink is wired to
  explode; a scan records nothing) and statically (an AST sweep over the module's imports).
  Prose alone is unfalsifiable, and the dynamic test alone would pass on a lazily-imported
  seam that simply wasn't reached by the fixture's rules.
* ``test_a_scan_needs_one_of_two_reasons`` / ``test_reading_a_project_does_not_scan`` — the
  "on project-create and on-demand ONLY" clause, asserted as the negative.
* ``test_surfacing_writes_nothing`` — propose-only: a full scan with inspect reports leaves the
  home byte-identical.
* ``test_a_rejection_is_remembered_and_never_re_nags`` — the persistence test the never-re-nag
  claim is worthless without.
* ``test_confidence_is_the_declared_ceiling_scaled_by_coverage`` — pins the arithmetic, so the
  number in the UI has a definition rather than a vibe.
* ``test_a_user_edited_component_is_skipped_with_a_drift_note`` — §1's whole point.

Every test binds ``PERSONALCLAW_HOME`` to a tmp dir; nothing here may reach the real home.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.packs import bundled as pack_bundled
from personalclaw.packs import fingerprint as fp
from personalclaw.packs import update as pack_update

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home. Bound via the env var because the stores read it live."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    return h


@pytest.fixture
def terraform_workspace(tmp_path):
    """A Terraform-SHAPED directory — the §7 / Success-7 scenario, on real files.

    Two of the ``infra-ops`` rule's globs and both of its signals are present, so a full
    coverage match is what the scanner should see. ``node_modules`` carries a decoy ``.tf`` to
    prove the skip list is doing work: counted, it would be evidence about a dependency rather
    than about this project.
    """
    ws = tmp_path / "tf-project"
    (ws / "modules" / "vpc").mkdir(parents=True)
    (ws / "node_modules" / "junk").mkdir(parents=True)
    (ws / "main.tf").write_text(
        'terraform {\n  required_version = ">= 1.5"\n}\n'
        'provider "aws" {\n  region = "us-west-2"\n}\n',
        encoding="utf-8",
    )
    (ws / "modules" / "vpc" / "main.tf").write_text(
        'resource "aws_vpc" "main" {\n  cidr_block = "10.0.0.0/16"\n}\n', encoding="utf-8"
    )
    (ws / "prod.tfvars").write_text('region = "us-west-2"\n', encoding="utf-8")
    (ws / "node_modules" / "junk" / "vendored.tf").write_text("# decoy\n", encoding="utf-8")
    return ws


@pytest.fixture
def project(terraform_workspace):
    """A Project-shaped object bound to the Terraform workspace.

    Duck-typed on the two attributes :func:`scan_project` reads (``id``/``workspace_dir``) —
    the real :class:`personalclaw.tasks.models.Project` needs a whole store to exist, and the
    scanner's contract is those two fields, not the class.
    """
    return SimpleNamespace(id="proj-tf", workspace_dir=str(terraform_workspace))


def _snapshot(root: Path) -> dict[str, str]:
    """Every file under ``root`` as path → sha256 — the byte-identity baseline."""
    import hashlib

    out: dict[str, str] = {}
    for f in sorted(root.rglob("*")):
        if f.is_file():
            out[f.relative_to(root).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


# ── zero-LLM: the property that is otherwise unfalsifiable prose ──────────────


def test_the_scanner_makes_no_model_call(home, project, monkeypatch):
    """A full scan — including every §3.1 inspect report — records ZERO model attempts.

    Wired at the ``ModelCallGuard`` attempt-audit sink (``guardrails.audit.record_attempt``),
    which every guarded model call funnels through: if any code path under a scan reached a
    provider, this raises from inside it. Asserting "no attempt row" rather than "no import"
    is what makes it a runtime proof.
    """
    calls: list[object] = []

    def _explode(rec):  # pragma: no cover - the assertion is that this never runs
        calls.append(rec)
        raise AssertionError("a fingerprint scan reached a model call")

    monkeypatch.setattr("personalclaw.guardrails.audit.record_attempt", _explode)
    monkeypatch.setattr(
        "personalclaw.llm_helpers.one_shot_completion",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("scan called the one-shot bridge")),
    )

    proposals = fp.scan_project(project, reason=fp.SCAN_REASON_CREATE)

    assert calls == []
    # The scan must actually have DONE something, or "no model call" is vacuously true of a
    # no-op. A rail that matches nothing looks clean.
    assert [p.pack for p in proposals] == ["infra-ops"]
    assert proposals[0].inspect is not None


def test_the_scanner_imports_no_model_seam():
    """Statically: ``packs/fingerprint.py`` imports nothing from a model/provider seam.

    The dynamic test above proves no call happened for ITS rules; this proves the module has no
    route to one at all, so a future rule shape cannot quietly acquire one. AST over the source
    rather than a grep, so a name inside a docstring or a comment is not a false positive.
    """
    src = Path(fp.__file__).read_text(encoding="utf-8")
    banned = (
        "personalclaw.sampling",
        "personalclaw.providers",
        "personalclaw.prompt_providers",
        "personalclaw.guardrails.model_call",
        "personalclaw.model",
        "anthropic",
        "openai",
    )
    imported: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [m for m in imported if any(m == b or m.startswith(b + ".") for b in banned)]
    assert offenders == [], f"fingerprint.py must stay zero-LLM; it imports {offenders}"


# ── on project-create and on-demand ONLY ──────────────────────────────────────


def test_a_scan_needs_one_of_two_reasons(home, project):
    """§7's "never on a background loop" is a typed refusal, not a docstring.

    A timer could not schedule a scan without inventing a reason name — and that fails loudly
    here rather than silently acquiring a new trigger surface.
    """
    assert fp.SCAN_REASONS == {"project-create", "on-demand"}
    for reason in sorted(fp.SCAN_REASONS):
        fp.scan_project(project, reason=reason, with_inspect=False)  # both are allowed
    with pytest.raises(ValueError, match="never on a background loop"):
        fp.scan_project(project, reason="timer", with_inspect=False)
    with pytest.raises(ValueError):
        fp.scan_project(project, reason="", with_inspect=False)


def test_reading_a_project_does_not_scan():
    """The negative asserted at the CALL SITE: only two functions call the scanner.

    An AST sweep over the whole package for ``scan_project(`` call sites. A read path that
    started scanning (``GET /api/projects/{id}``, a projection, a digest) would show up here as
    a third caller — which is the failure mode "on-create and on-demand only" describes, and it
    is invisible to any test that only exercises the scanner itself.
    """
    root = Path(fp.__file__).parents[1]
    callers: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover
            continue
        rel = path.relative_to(root).as_posix()
        # Attribute the call to its ENCLOSING FUNCTION, not just its file. Scoping this to
        # files would let a scan added to ``api_projects_get`` pass — same module as the
        # create handler — which is precisely the read path this clause forbids.
        for parent in ast.walk(tree):
            if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(parent):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name == "scan_project":
                    callers.add(f"{rel}::{parent.name}")
    # fingerprint.py itself never calls it; the only two are the on-demand route and the
    # project-create handler's proposal helper.
    assert sorted(callers) == [
        "dashboard/handlers/packs.py::api_pack_proposals",
        "tasks/hierarchy_handlers.py::_fingerprint_proposals",
    ], f"a third scan_project call site appeared: {sorted(callers)}"


# ── propose-only ──────────────────────────────────────────────────────────────


#: The SEL audit trail is the ONE thing a scan is expected to write (§8: every scan verdict is
#: SEL-audited, same as a skill install). It is not entity state — it is the security log
#: recording that a dry run happened — so the propose-only assertion below excludes it BY NAME
#: and then asserts it positively, rather than quietly widening to "nothing much changed".
_SEL_FILES = ("security_events.jsonl", "sel_hmac.key")


def test_surfacing_writes_nothing_but_its_own_audit_row(home, project):
    """Surfacing a proposal — inspect report included — writes NO entity or ledger state.

    A proposal is weaker than AP-4's dormant import: it does not stage, does not lock, does not
    touch the ledger, does not create a skill/template/prompt/agent. If it wrote any of those,
    "propose-only" would be a label on an install.
    """
    (home / "packs").mkdir()
    before = _snapshot(home)
    proposals = fp.scan_project(project, reason=fp.SCAN_REASON_ON_DEMAND)
    assert proposals, "the fixture workspace must match, or this proves nothing"

    after = _snapshot(home)
    new = {k: v for k, v in after.items() if before.get(k) != v}
    assert set(new) <= set(_SEL_FILES), f"a propose-only scan wrote entity state: {sorted(new)}"

    # No entity store was created at all, and no ledger row exists.
    for store in ("skills", "workflows", "prompts", "agents"):
        assert not (home / store).exists(), f"a proposal created {store}/"
    from personalclaw.packs.installed import load_installed

    assert load_installed() == []
    # §8: the scan verdict IS audited. Asserted positively so "no writes" can never be
    # satisfied by an inspect that also stopped auditing.
    assert (home / "security_events.jsonl").is_file()
    assert "pack_inspect" in (home / "security_events.jsonl").read_text(encoding="utf-8")


def test_the_card_carries_the_inspect_report_and_installs_nothing(home, project):
    """The §3.1 report rides on the card: what it WOULD install, computed without writing."""
    proposal = fp.scan_project(project, reason=fp.SCAN_REASON_ON_DEMAND)[0]
    assert proposal.inspect_error == ""
    report = proposal.inspect
    assert report is not None
    assert report["name"] == "infra-ops"
    assert report["blocked"] is False
    refs = {(c["kind"], c["orig_id"]) for c in report["components"]}
    assert ("template", "infra-change-review") in refs
    assert ("agent", "infra-reviewer") in refs
    # Nothing landed: the report is a dry run.
    assert not (home / "skills").exists()
    assert not (home / "workflows").exists()


# ── never re-nag ──────────────────────────────────────────────────────────────


def test_a_rejection_is_remembered_and_never_re_nags(home, project):
    """Reject once → a SECOND scan is silent, and the "no" is on disk (§9).

    Both halves matter. Without the persisted store the silence could be in-process memory that
    a gateway restart forgets; without the second scan the store could be a file nobody reads.
    """
    assert [p.pack for p in fp.scan_project(project, reason=fp.SCAN_REASON_CREATE)] == ["infra-ops"]

    fp.reject_proposal(project.id, "infra-ops")

    assert fp.scan_project(project, reason=fp.SCAN_REASON_ON_DEMAND) == []
    assert fp.scan_project(project, reason=fp.SCAN_REASON_CREATE) == []
    assert fp.is_rejected(project.id, "infra-ops")

    store = home / "packs" / fp.REJECTIONS_FILE
    assert store.is_file(), "a never-re-nag promise with no persistence ships inert"
    assert json.loads(store.read_text(encoding="utf-8"))["proj-tf"]["infra-ops"]


def test_a_rejection_is_scoped_to_its_project(home, project):
    """One project's "no" must not mute another project's proposal."""
    fp.reject_proposal(project.id, "infra-ops")
    other = SimpleNamespace(id="proj-other", workspace_dir=project.workspace_dir)
    assert [p.pack for p in fp.scan_project(other, reason=fp.SCAN_REASON_CREATE)] == ["infra-ops"]


def test_re_rejecting_keeps_the_first_decision(home, project):
    """The durable fact is WHEN the user said no; a re-post must not overwrite it."""
    fp.reject_proposal(project.id, "infra-ops")
    first = fp.load_rejections()["proj-tf"]["infra-ops"]
    fp.reject_proposal(project.id, "infra-ops")
    assert fp.load_rejections()["proj-tf"]["infra-ops"] == first


def test_a_rejection_needs_both_halves(home):
    with pytest.raises(ValueError):
        fp.reject_proposal("", "infra-ops")
    with pytest.raises(ValueError):
        fp.reject_proposal("proj-tf", "")


# ── the kill switch ───────────────────────────────────────────────────────────


def test_fingerprint_enabled_false_stops_scanning_entirely(home, project):
    """``packs.fingerprint_enabled = false`` returns [] before touching the filesystem."""
    off = SimpleNamespace(packs=SimpleNamespace(fingerprint_enabled=False))
    assert fp.fingerprinting_enabled(off) is False
    assert fp.scan_project(project, reason=fp.SCAN_REASON_CREATE, config=off) == []
    on = SimpleNamespace(packs=SimpleNamespace(fingerprint_enabled=True))
    assert [p.pack for p in fp.scan_project(project, reason=fp.SCAN_REASON_CREATE, config=on)] == [
        "infra-ops"
    ]


def test_the_kill_switch_is_read_from_the_real_config(home, project):
    """The toggle a user flips in Settings is the value that governs — the config is not a
    parameter the caller may forget to pass. Written to the real ``config.json`` and read back
    through ``AppConfig.load()``, so the five-point round trip is what is exercised."""
    (home / "config.json").write_text(
        json.dumps({"packs": {"fingerprint_enabled": False}}), encoding="utf-8"
    )
    assert fp.fingerprinting_enabled() is False
    assert fp.scan_project(project, reason=fp.SCAN_REASON_CREATE) == []


# ── confidence means something ────────────────────────────────────────────────


def test_confidence_is_the_declared_ceiling_scaled_by_coverage():
    """The score's definition, pinned: ``declared * coverage``, coverage = mean of the two
    matched fractions. An unexplained number in a UI is worse than none, so this is the test
    that makes the number reviewable."""
    rule = fp.Fingerprint(label="x", globs=("*.tf", "*.tfvars"), signals=("a", "b"), confidence=0.9)
    assert fp._score(rule, glob_hits=2, signal_hits=2) == 0.9  # full coverage → the ceiling
    assert fp._score(rule, glob_hits=1, signal_hits=2) == 0.68  # 0.9 * ((0.5+1.0)/2) = 0.675
    assert fp._score(rule, glob_hits=1, signal_hits=0) == 0.23  # 0.9 * ((0.5+0.0)/2) = 0.225
    # A rule with no signals scores on glob coverage alone — no phantom half-credit.
    no_sig = fp.Fingerprint(label="x", globs=("*.tf", "*.tfvars"), confidence=0.8)
    assert fp._score(no_sig, glob_hits=1, signal_hits=0) == 0.4


def test_the_card_carries_the_arithmetic_behind_its_score(home, project):
    """Every input to the number travels with it, so a UI can explain rather than assert."""
    match = fp.scan_project(project, reason=fp.SCAN_REASON_CREATE, with_inspect=False)[0].matches[0]
    assert match.label == "Terraform project"
    assert match.declared_confidence == 0.9
    assert match.confidence == 0.9  # both globs + both signals hit
    assert match.matched_globs == ["*.tf", "*.tfvars"]
    assert sorted(match.matched_signals) == ['provider "', "terraform {"]
    assert match.evidence, "a score with no example path is unreviewable"


def test_a_partial_match_scores_below_the_ceiling(home, tmp_path):
    """A dir with the file shape but none of the signals scores strictly lower than a full one —
    the property that makes the number informative rather than decorative."""
    ws = tmp_path / "shape-only"
    ws.mkdir()
    (ws / "main.tf").write_text("# no terraform block, no provider\n", encoding="utf-8")
    proj = SimpleNamespace(id="p-partial", workspace_dir=str(ws))
    proposal = fp.scan_project(proj, reason=fp.SCAN_REASON_CREATE, with_inspect=False)[0]
    assert 0 < proposal.confidence < 0.9
    assert proposal.matches[0].matched_signals == []


# ── the scanner's own honesty ─────────────────────────────────────────────────


def test_signals_alone_never_propose(tmp_path):
    """A signal is corroboration for a file shape. Without a glob hit there is no match, or a
    rule could fire on any project whose README happens to contain the word."""
    ws = tmp_path / "prose"
    ws.mkdir()
    (ws / "README.md").write_text('terraform {\nprovider "aws"\n', encoding="utf-8")
    rule = fp.Fingerprint(label="tf", globs=("*.tf",), signals=("terraform {",), confidence=0.9)
    matches, _ = fp.match_workspace(ws, [rule])
    assert matches == []


def test_a_signals_only_rule_is_dropped_at_parse():
    """A rule with no globs would have nothing to bound its reads with, and would match every
    project. Dropped rather than honoured."""
    assert fp.parse_fingerprints([{"label": "x", "signals": ["a"], "confidence": 1.0}]) == []
    assert fp.parse_fingerprints("not a list") == []
    assert len(fp.parse_fingerprints([{"globs": ["*.tf"]}, {"nonsense": True}])) == 1


def test_the_walk_skips_dependency_trees(terraform_workspace):
    """The decoy ``.tf`` under ``node_modules`` is not evidence about this project."""
    rels, _ = fp._walk(terraform_workspace)
    assert "main.tf" in rels
    assert "modules/vpc/main.tf" in rels
    assert not any("node_modules" in r for r in rels)


def test_the_scan_is_deterministic(terraform_workspace):
    """Two scans of one tree agree exactly — the property "zero-LLM" is supposed to buy."""
    rules = fp.declared_fingerprints()["infra-ops"]
    first, n1 = fp.match_workspace(terraform_workspace, rules)
    second, n2 = fp.match_workspace(terraform_workspace, rules)
    assert n1 == n2
    assert [m.to_dict() for m in first] == [m.to_dict() for m in second]


def test_no_workspace_means_no_scan(home):
    """A project with no bound codebase has nothing to fingerprint."""
    assert fp.scan_project(SimpleNamespace(id="p", workspace_dir=""), reason="on-demand") == []
    assert (
        fp.scan_project(SimpleNamespace(id="p", workspace_dir="/nope/nowhere"), reason="on-demand")
        == []
    )


def test_a_sensitive_workspace_is_refused(home, monkeypatch, terraform_workspace):
    """The scanner applies the same two guards the project-create route applies before storing
    a workspace binding — a credential dir is not a place we walk looking for file shapes."""
    monkeypatch.setattr("personalclaw.security.is_sensitive_path", lambda p: True)
    proj = SimpleNamespace(id="p-sens", workspace_dir=str(terraform_workspace))
    assert fp.scan_project(proj, reason=fp.SCAN_REASON_CREATE) == []


def test_an_installed_pack_is_never_proposed(home, project):
    """Proposing a pack the user already has is nagging, not helping."""
    from personalclaw.packs.installed import InstalledPack, record_install

    record_install(InstalledPack(name="infra-ops", version="1.0.0"))
    assert fp.scan_project(project, reason=fp.SCAN_REASON_ON_DEMAND) == []


def test_a_bundled_pack_declares_the_terraform_fingerprint():
    """The Terraform rule is DECLARED by a pack that ships in this build — a proposal a user
    cannot act on is not a proposal."""
    declared = fp.declared_fingerprints()
    assert "infra-ops" in declared
    assert pack_bundled.get_bundled("infra-ops") is not None
    labels = {r.label for r in declared["infra-ops"]}
    assert "Terraform project" in labels


# ── §1 the pack_owned update flow ─────────────────────────────────────────────


@pytest.fixture
def installed_infra(home, tmp_path):
    """``infra-ops`` installed for real, plus its rebuildable archive."""
    from personalclaw.packs.import_ import import_pack
    from personalclaw.supply_chain import TrustTier

    archive = pack_bundled.build_bundled("infra-ops", tmp_path / "infra-ops.pclaw")
    import_pack(archive, tier=TrustTier.BUILTIN)
    return archive


def test_install_stamps_a_lock_per_component(home, installed_infra):
    """The drift primitive exists on disk. Without it every later skip decision is a guess."""
    from personalclaw.packs.installed import load_installed

    rec = next(p for p in load_installed() if p.name == "infra-ops")
    assert rec.pack_owned == ["skills/infra-*", "templates/infra-*", "agents/infra-*"]
    assert set(rec.component_locks) == {
        "skill:infra-plan-review",
        "skill:infra-drift-audit",
        "agent:infra-reviewer",
        "template:infra-change-review",
    }
    for ref, lock in rec.component_locks.items():
        assert lock["computedHash"], ref
        assert lock["source"] == "pack:infra-ops@1.0.0"
        assert (home / lock["path"]).exists()


def test_an_unmodified_pack_owned_component_is_overwritten(home, installed_infra):
    from personalclaw.supply_chain import TrustTier

    plan = pack_update.plan_update("infra-ops", installed_infra, tier=TrustTier.BUILTIN)
    assert plan.skipped == []
    assert set(plan.overwritten) == {
        "skill:infra-plan-review",
        "skill:infra-drift-audit",
        "agent:infra-reviewer",
        "template:infra-change-review",
    }


def test_a_user_edited_component_is_skipped_with_a_drift_note(home, installed_infra):
    """§1/Success-8: the user's edit survives an update, and the skip is VISIBLE.

    Both halves are load-bearing. A silent skip is indistinguishable from a clobber to anyone
    reading the result, so the note is part of the contract, not a nicety.
    """
    from personalclaw.supply_chain import TrustTier

    edited = home / "skills" / "infra-plan-review" / "SKILL.md"
    mine = edited.read_text(encoding="utf-8") + "\n<!-- my own step -->\n"
    edited.write_text(mine, encoding="utf-8")

    plan = pack_update.plan_update("infra-ops", installed_infra, tier=TrustTier.BUILTIN)
    assert plan.skipped == ["skill:infra-plan-review"]
    note = next(n for n in plan.drift_notes if n.startswith("skill:infra-plan-review"))
    assert "edited since install" in note and "your version was kept" in note

    applied = pack_update.apply_update("infra-ops", installed_infra, tier=TrustTier.BUILTIN)
    assert applied.applied is True
    assert applied.skipped == ["skill:infra-plan-review"]
    assert edited.read_text(encoding="utf-8") == mine, "the update clobbered a user edit"
    # The other three DID land, so the skip is a targeted decision rather than a dead update.
    assert len(applied.overwritten) == 3


def test_a_skipped_component_keeps_its_original_lock(home, installed_infra):
    """A drifted component must still read as drifted next time — refreshing its lock would
    retroactively bless the user's edit as the pack's own bytes."""
    from personalclaw.packs.installed import load_installed
    from personalclaw.supply_chain import TrustTier

    before = next(p for p in load_installed() if p.name == "infra-ops")
    original = before.component_locks["skill:infra-plan-review"]["computedHash"]
    edited = home / "skills" / "infra-plan-review" / "SKILL.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n<!-- mine -->\n", encoding="utf-8")

    pack_update.apply_update("infra-ops", installed_infra, tier=TrustTier.BUILTIN)

    after = next(p for p in load_installed() if p.name == "infra-ops")
    assert after.component_locks["skill:infra-plan-review"]["computedHash"] == original
    second = pack_update.plan_update("infra-ops", installed_infra, tier=TrustTier.BUILTIN)
    assert second.skipped == ["skill:infra-plan-review"]


def test_a_non_pack_owned_component_is_never_touched(home, tmp_path, monkeypatch):
    """A component the manifest does not claim as ``pack_owned`` is out of an update's reach."""
    from personalclaw.packs.import_ import import_pack
    from personalclaw.supply_chain import TrustTier

    source = pack_bundled.get_bundled("infra-ops")
    assert source is not None
    manifest = json.loads((source.source / "pack.json").read_text(encoding="utf-8"))
    manifest["pack_owned"] = ["skills/infra-*"]  # templates + agents disclaimed
    staged = tmp_path / "narrow"
    import shutil

    shutil.copytree(source.source, staged)
    (staged / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        pack_bundled,
        "get_bundled",
        lambda name: (
            pack_bundled.BundledPack("infra-ops", "1.0.0", "Infra Ops", "", staged)
            if name == "infra-ops"
            else None
        ),
    )
    archive = pack_bundled.build_bundled("infra-ops", tmp_path / "narrow.pclaw")
    import_pack(archive, tier=TrustTier.BUILTIN)

    plan = pack_update.plan_update("infra-ops", archive, tier=TrustTier.BUILTIN)
    actions = {c.ref: c.action for c in plan.components}
    assert actions["template:infra-change-review"] == pack_update.ACTION_SKIP_NOT_OWNED
    assert actions["agent:infra-reviewer"] == pack_update.ACTION_SKIP_NOT_OWNED
    assert actions["skill:infra-plan-review"] == pack_update.ACTION_OVERWRITE


def test_an_unlockable_component_is_never_clobbered(home, installed_infra):
    """ "I cannot tell whether you edited this" must not resolve to "so I'll overwrite it"."""
    from personalclaw.packs.installed import load_installed, record_install
    from personalclaw.supply_chain import TrustTier

    rec = next(p for p in load_installed() if p.name == "infra-ops")
    rec.component_locks = {}  # a pack installed before locks existed
    record_install(rec)
    plan = pack_update.plan_update("infra-ops", installed_infra, tier=TrustTier.BUILTIN)
    assert {c.action for c in plan.components} == {pack_update.ACTION_SKIP_UNVERIFIABLE}
    assert plan.drift_notes and all("no install lock" in n for n in plan.drift_notes)


def test_an_update_replaces_in_place_rather_than_forking(home, installed_infra):
    """The colliding entity IS the pack's own previous copy — remapping it to
    ``<id>-imported-1`` would report success while the update never happened."""
    from personalclaw.supply_chain import TrustTier

    pack_update.apply_update("infra-ops", installed_infra, tier=TrustTier.BUILTIN)
    assert sorted(p.name for p in (home / "skills").iterdir()) == [
        "infra-drift-audit",
        "infra-plan-review",
    ]
    assert [p.name for p in (home / "workflows" / "defs").iterdir()] == ["infra-change-review"]


def test_updating_an_uninstalled_pack_is_refused(home, tmp_path):
    from personalclaw.supply_chain import TrustTier

    archive = pack_bundled.build_bundled("infra-ops", tmp_path / "p.pclaw")
    with pytest.raises(pack_update.PackUpdateError, match="not installed"):
        pack_update.plan_update("infra-ops", archive, tier=TrustTier.BUILTIN)


def test_an_update_must_be_the_same_pack(home, installed_infra, tmp_path):
    """A "health-os update" for ``infra-ops`` would overwrite one pack's components against
    another's ownership patterns."""
    from personalclaw.supply_chain import TrustTier

    other = pack_bundled.build_bundled("health-os", tmp_path / "health.pclaw")
    with pytest.raises(pack_update.PackUpdateError, match="must be the same pack"):
        pack_update.plan_update("infra-ops", other, tier=TrustTier.BUILTIN)


def test_component_digest_moves_on_any_content_change(tmp_path):
    """One algorithm for a file and a directory. An edit, an addition and a rename all move it,
    and the install lock file itself is excluded (it is provenance, not content)."""
    f = tmp_path / "one.json"
    f.write_text("{}", encoding="utf-8")
    first = pack_update.component_digest(f)
    f.write_text('{"a":1}', encoding="utf-8")
    assert pack_update.component_digest(f) != first

    d = tmp_path / "skill"
    d.mkdir()
    (d / "SKILL.md").write_text("body", encoding="utf-8")
    base = pack_update.component_digest(d)
    (d / ".pclaw-lock.json").write_text('{"installed_at": 1}', encoding="utf-8")
    assert pack_update.component_digest(d) == base, "the lock file is provenance, not content"
    (d / "extra.md").write_text("more", encoding="utf-8")
    assert pack_update.component_digest(d) != base
    assert pack_update.component_digest(tmp_path / "gone") == ""


# ── the export → wipe → import sweep, on a SECOND home ────────────────────────


def test_infra_ops_round_trips_onto_a_second_home(tmp_path, monkeypatch):
    """AP-7's validation sweep for the pack this atom adds (§Success 1's shape).

    The archive is BUILT while one home is bound and IMPORTED while a different, empty home is
    bound, so nothing here can pass because state leaked from the exporting side — the same
    two-home discipline AP-4's Domain OS round trip uses. It is a separate test from that one
    because ``infra-ops`` ships no trigger and no connector, so the assertions that matter for
    it are different: every skill locked, the agent live in the agent store, the template
    runnable, and the drift lock stamped for a later update.
    """
    from personalclaw.packs.import_ import import_pack
    from personalclaw.packs.installed import load_installed
    from personalclaw.supply_chain import TrustTier

    build_home = tmp_path / "build-home"
    build_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(build_home))
    archive = pack_bundled.build_bundled("infra-ops", tmp_path / "infra-ops.pclaw")

    fresh = tmp_path / "fresh-home"
    fresh.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))

    plan = import_pack(archive, tier=TrustTier.BUILTIN)
    assert plan.name == "infra-ops"
    assert plan.integrity_ok and plan.lint.ok and not plan.blocked

    # Skills committed through install_guarded, so each carries its own lock file.
    locked = {p.parent.name for p in (fresh / "skills").rglob(".pclaw-lock.json")}
    assert {"infra-plan-review", "infra-drift-audit"} <= locked
    # The agent and template landed where their stores read them.
    assert (fresh / "agents" / "infra-reviewer" / "agent.json").is_file()
    assert (fresh / "workflows" / "defs" / "infra-change-review" / "workflow.json").is_file()
    # The roster staged rather than deploying — nothing was hired by the import.
    rec = next(p for p in load_installed(fresh) if p.name == "infra-ops")
    assert [r["slug"] for r in rec.roster] == ["infra-reviewer"]
    assert len(rec.component_locks) == 4
    # And the BUILD home is untouched by the import — proof the two homes are really separate.
    assert not (build_home / "skills").exists()


def test_pack_owned_patterns_cover_a_directory_component():
    """``skills/infra-*`` has to cover ``skills/infra-plan-review/SKILL.md`` the way its author
    meant it, or the whole ownership rule silently matches nothing."""
    assert pack_update.is_pack_owned("skills/infra-plan-review/SKILL.md", ["skills/infra-*"])
    assert pack_update.is_pack_owned("templates/infra-x.json", ["templates/infra-*"])
    assert not pack_update.is_pack_owned("skills/other/SKILL.md", ["skills/infra-*"])
    assert not pack_update.is_pack_owned("skills/infra-x/SKILL.md", [])

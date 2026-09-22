"""OU-14 — the bundled-model admission rails, and the zero-config drive that ties them down.

Three clauses of OU-14 are engineering and are built here; the fourth — **which model ships and
the sign-off on its licence** — is the Chairman's (C9) and is deliberately absent. Nothing in
this file signs a model off, vendors a weight, or adds a real model's licence to the allowlist
as if the call were made. What it does is prove the rails have teeth, so the moment the record
is filled in the gate admits the model and the drive flips to MET on its own.

**Clause 1 — the licence-allowlist rail.** An explicit allowlist, default-DENY for anything
unlisted, and a refusal that NAMES the licence. The negative controls are the near-misses that
made this atom's licence constraint necessary in the first place — Gemma Terms, the Llama
community licence, LFM's "Open License", CC-BY-NC, OpenRAIL, research-only — every one of which
reads as permissive on a model card. Crucially, they are refused by the DEFAULT, not by a
denylist entry: :func:`test_the_refusal_is_the_default_not_a_denylist` proves an unlisted
licence nobody anticipated is refused too, which is the difference between a control and a
catalogue of the traps somebody already knew about.

**Clause 2 — the size-budget gate.** Driven against a REAL zip (a wheel is a zip) so the
measurement path is the one the release gate runs, not a mocked number. The failing direction is
pinned three ways, because each is a way an isolated clause could have gone green with nothing
bundled: over budget, a ZERO measurement, and an undeclared ceiling. Plus the two chain
refusals — a weight the record does not declare, and a record the wheel does not carry — and the
digest mismatch that stops a record from describing a different artifact.

**Clause 3 — the end-to-end drive.** ``scripts/ou14_zero_config_drive.py`` on a throwaway
``PERSONALCLAW_HOME``, one in-process resolver call. Its teeth are
:func:`test_the_drive_contradicts_a_declared_bundle_with_nothing_runnable`: hand the drive a
sign-off record with no runnable provider behind it and it must report a CONTRADICTION. A drive
that said MET there would be a rubber stamp, and this clause is the only one of the four the
escalation judged uncheatable — so it is the one that must not be cheatable here either.

**What a green run here does NOT mean.** It does not mean a model is bundled (none is), does not
mean the zero-config promise is kept (it is not — see the drive's own ``UNMET``), and cannot
judge whether a licence is *genuinely* permissive: it compares a declared identifier against an
allowlist. Whether the identifier tells the truth about the model is the sign-off, and the
sign-off is a human act.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from personalclaw import bundled_model as rail
from personalclaw.bundled_model import (
    PERMITTED_LICENCES,
    STATE_ADMITTED,
    STATE_NO_BUNDLE,
    STATE_REFUSED,
    BundleDeclaration,
    BundleDeclarationError,
    admit_bundle,
    gate_wheel,
    licence_decision,
    measure_wheel,
    parse_declaration,
    repo_declaration,
    size_decision,
    weight_shaped_members,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DRIVE = _REPO_ROOT / "scripts" / "ou14_zero_config_drive.py"
_VERIFY_WHEEL = _REPO_ROOT / "scripts" / "verify_wheel.py"

#: Licences that MUST be refused, each with the reason it is a trap rather than an obvious no.
#: Every one of these appears on a real model card that a reader could take for permissive.
_KNOWN_FALSE_LICENCES: tuple[tuple[str, str], ...] = (
    ("gemma", "Google's Gemma Terms carry use restrictions and are not Apache-2.0"),
    ("gemma-terms-of-use", "the same terms spelled out"),
    ("llama3.2", "the Llama 3.2 Community Licence: attribution + a 700M-MAU threshold"),
    ("llama3.1", "same family, same non-OSI community terms"),
    ("lfm1.0", "'LFM Open License v1.0' — 'open' in the name is not OSI"),
    ("cc-by-nc-4.0", "non-commercial: PersonalClaw redistributes commercially-usable bytes"),
    ("cc-by-nc-sa-4.0", "same, with share-alike on top"),
    ("openrail-m", "OpenRAIL carries use-based behavioural restrictions"),
    ("bigscience-openrail-m", "the same family under its original name"),
    ("research-only", "a research-only grant cannot be redistributed to end users"),
    ("qwen-research", "a research licence hiding behind a permissive-looking vendor prefix"),
    ("deepseek", "a custom vendor licence, not an OSI identifier"),
    ("apache license 2.0", "PROSE, not the SPDX identifier — see the no-fuzzy-matching rule"),
    ("Apache-2.0 (with additional use terms)", "a permissive id with restrictions bolted on"),
    ("agpl-3.0", "copyleft is not the question, but it is not on the allowlist either"),
)

_SIXTY_FOUR_HEX = "a" * 64


def _record(**overrides: object) -> str:
    """A complete sign-off record as TEXT, so the real parser is what reads it.

    The values are obvious placeholders (``example-org/example-model``, a link to
    ``example.com``) and are never a real model: this fixture exists to prove the rail's teeth,
    not to stand in for the Chairman's choice.
    """
    fields: dict[str, object] = {
        "model_id": "example-org/example-model",
        "licence": "Apache-2.0",
        "licence_url": "https://example.com/LICENSE",
        "artifact": "personalclaw/models/example.gguf",
        "sha256": _SIXTY_FOUR_HEX,
        "size_budget_bytes": 1024,
    }
    fields.update(overrides)
    return "".join(f"{key}: {value}\n" for key, value in fields.items())


def _wheel(tmp_path: Path, members: dict[str, bytes], name: str = "demo-0.0.1-py3-none-any.whl"):
    """A real zip standing in for a built wheel — a wheel IS a zip, so the path is the same."""
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member, payload in members.items():
            zf.writestr(member, payload)
    return path


# ── clause 1: the licence-allowlist rail ──────────────────────────────────────────────────────


def test_the_permitted_allowlist_is_pinned_member_for_member() -> None:
    """Widening the allowlist must be an explicit, reviewable edit in the same commit.

    Chairman C9 binds the set to genuinely OSI-permissive licences — Apache-2.0 or MIT. Pinning
    it here means a PR that quietly adds a third entry reds with this test naming it, rather
    than shipping a licence nobody ruled on.
    """
    assert set(PERMITTED_LICENCES) == {"apache-2.0", "mit"}, (
        f"the permitted-licence allowlist is {sorted(PERMITTED_LICENCES)}. Chairman C9 binds it "
        "to Apache-2.0 or MIT. Changing it is a governance decision: say what was added, why it "
        "is genuinely OSI-permissive, and update this pin in the same commit."
    )


@pytest.mark.parametrize("declared", ["Apache-2.0", "apache-2.0", "  MIT  ", "mit"])
def test_a_permitted_licence_is_admitted(declared: str) -> None:
    """The control arm. Without it every refusal below could be a rail that refuses everything."""
    decision = licence_decision(declared)
    assert decision.permitted, decision.reason
    assert decision.identifier in PERMITTED_LICENCES


@pytest.mark.parametrize(
    "declared,why", _KNOWN_FALSE_LICENCES, ids=[c[0] for c in _KNOWN_FALSE_LICENCES]
)
def test_a_known_false_licence_is_refused_and_named(declared: str, why: str) -> None:
    """Each trap is refused, and the refusal NAMES the licence it refused.

    Naming matters as much as refusing: the failure mode this replaces is a model silently
    skipped for an unreadable licence, which is a model nobody ever decided about.
    """
    decision = licence_decision(declared)
    assert not decision.permitted, f"{declared!r} was admitted — {why}"
    assert (
        declared.strip() in decision.reason
    ), f"the refusal for {declared!r} does not name the licence it refused: {decision.reason!r}"


def test_the_refusal_is_the_default_not_a_denylist() -> None:
    """An unlisted licence NOBODY anticipated is refused too.

    This is the difference between a control and a catalogue. If the rail worked by matching
    known-bad strings, a licence invented next year would sail through; the traps above would
    still pass and the gate would guard nothing new.
    """
    for invented in ("totally-new-terms-2027", "sensible-ai-licence-v3", "x", "0"):
        decision = licence_decision(invented)
        assert not decision.permitted, (
            f"{invented!r} is not on the allowlist and was admitted anyway — the rail is "
            "matching known-bad strings instead of defaulting to DENY"
        )


def test_an_absent_licence_is_refused_with_its_own_message() -> None:
    """An empty field is not a permissive licence, and the message must not read as one."""
    for empty in ("", "   ", "\t"):
        decision = licence_decision(empty)
        assert not decision.permitted
        assert "no licence is declared" in decision.reason


# ── clause 2: the size-budget gate ────────────────────────────────────────────────────────────


def test_an_artifact_inside_the_budget_is_admitted_and_both_numbers_are_named() -> None:
    """The control arm, and the pass message still reports what it measured."""
    decision = size_decision(400, 1000)
    assert decision.within_budget, decision.reason
    assert "400" in decision.reason and "1000" in decision.reason


def test_an_artifact_over_the_budget_is_refused_naming_measured_and_budget() -> None:
    """The failing direction the gate is named for."""
    decision = size_decision(1500, 1000)
    assert not decision.within_budget
    assert "1500" in decision.reason, decision.reason
    assert "1000" in decision.reason, decision.reason
    assert "500" in decision.reason, f"the refusal does not say by how much: {decision.reason}"


def test_an_artifact_exactly_at_the_budget_is_admitted() -> None:
    """The boundary is inclusive, stated so nobody has to guess from the operator."""
    assert size_decision(1000, 1000).within_budget


def test_a_zero_measurement_is_refused_because_that_is_the_vacuity() -> None:
    """A size gate that passes when nothing shipped reports 'nobody measured' as 'in budget'.

    This is the exact cheat OU-14's escalation found in three of its four clauses, so it is
    pinned as a refusal rather than left to a reader's judgment.
    """
    decision = size_decision(0, 1000)
    assert not decision.within_budget
    assert "nothing was shipped" in decision.reason


@pytest.mark.parametrize("budget", [0, -1])
def test_an_undeclared_budget_cannot_admit_anything(budget: int) -> None:
    """An unset ceiling is not permission — the owner sets the number."""
    decision = size_decision(400, budget)
    assert not decision.within_budget
    assert "no size budget is declared" in decision.reason


# ── clause 2, continued: the gate over a real wheel ───────────────────────────────────────────


def test_the_gate_measures_the_wheel_and_admits_a_conforming_bundle(tmp_path: Path) -> None:
    """End to end over a real zip: measured size and digest both come from the artifact.

    The control arm for every refusal below. A gate that refused everything would make them all
    pass while guarding nothing.
    """
    payload = b"w" * 900
    wheel = _wheel(
        tmp_path, {"personalclaw/models/example.gguf": payload, "personalclaw/x.py": b""}
    )
    declaration = parse_declaration(
        _record(sha256=hashlib.sha256(payload).hexdigest(), size_budget_bytes=1024)
    )
    assert declaration is not None
    result = gate_wheel(wheel, declaration)
    assert result.state == STATE_ADMITTED, result.refusals
    assert "900" in result.summary


def test_the_gate_refuses_an_over_budget_weight_in_a_real_wheel(tmp_path: Path) -> None:
    """The release-path failing direction, measured from the wheel rather than passed in."""
    payload = b"w" * 2048
    wheel = _wheel(tmp_path, {"personalclaw/models/example.gguf": payload})
    declaration = parse_declaration(
        _record(sha256=hashlib.sha256(payload).hexdigest(), size_budget_bytes=1024)
    )
    result = gate_wheel(wheel, declaration)
    assert result.state == STATE_REFUSED
    assert any("2048" in r and "1024" in r for r in result.refusals), result.refusals


def test_the_gate_refuses_a_weight_no_record_declares(tmp_path: Path) -> None:
    """Shipping a weight and not mentioning it must not be a way around the licence record."""
    wheel = _wheel(tmp_path, {"personalclaw/models/smuggled.gguf": b"w" * (2 * 1024 * 1024)})
    result = gate_wheel(wheel, None)
    assert result.state == STATE_REFUSED
    assert any("smuggled.gguf" in r for r in result.refusals), result.refusals


def test_the_gate_refuses_a_record_whose_artifact_is_not_in_the_wheel(tmp_path: Path) -> None:
    """A sign-off describing a model that is not there is the cheatable licence clause."""
    wheel = _wheel(tmp_path, {"personalclaw/x.py": b"x = 1\n"})
    declaration = parse_declaration(_record())
    result = gate_wheel(wheel, declaration)
    assert result.state == STATE_REFUSED
    assert any("no such member" in r for r in result.refusals), result.refusals


def test_the_gate_refuses_a_digest_that_does_not_match_the_record(tmp_path: Path) -> None:
    """The digest is what chains the recorded licence to the bytes users receive."""
    wheel = _wheel(tmp_path, {"personalclaw/models/example.gguf": b"w" * 900})
    declaration = parse_declaration(_record(sha256="b" * 64, size_budget_bytes=1024))
    result = gate_wheel(wheel, declaration)
    assert result.state == STATE_REFUSED
    assert any("hashes to" in r for r in result.refusals), result.refusals


def test_the_gate_refuses_a_non_permissive_licence_on_a_shipped_weight(tmp_path: Path) -> None:
    """Clauses 1 and 2 meeting: the allowlist is enforced against the SHIPPED artifact."""
    payload = b"w" * 900
    wheel = _wheel(tmp_path, {"personalclaw/models/example.gguf": payload})
    declaration = parse_declaration(
        _record(licence="gemma", sha256=hashlib.sha256(payload).hexdigest(), size_budget_bytes=1024)
    )
    result = gate_wheel(wheel, declaration)
    assert result.state == STATE_REFUSED
    assert any("gemma" in r for r in result.refusals), result.refusals


def test_no_bundle_is_reported_as_measured_nothing_not_as_ok(tmp_path: Path) -> None:
    """The vacuity is NAMED. This is the assertion that makes the gate a gate today.

    With nothing signed off and nothing shipped the gate does not block a release — but its
    summary has to say it measured no artifact, or a release log reads 'OK' about a check that
    never happened, which is the failure mode this atom's own escalation identified.
    """
    wheel = _wheel(tmp_path, {"personalclaw/x.py": b"x = 1\n"})
    result = gate_wheel(wheel, None)
    assert result.state == STATE_NO_BUNDLE
    assert result.ok, "nothing is signed off yet, so this must not block a release"
    assert "NO BUNDLE DECLARED" in result.summary
    assert "measured NO artifact" in result.summary
    assert "UNMET" in result.summary


def test_a_small_weight_shaped_fixture_does_not_trip_the_undeclared_scan(tmp_path: Path) -> None:
    """The floor, proven. Without it every `.bin` test fixture in the tree would red a release."""
    wheel = _wheel(tmp_path, {"personalclaw/tests_fixtures/tiny.bin": b"x" * 10})
    assert gate_wheel(wheel, None).state == STATE_NO_BUNDLE


def test_weight_shaped_members_recognises_the_formats_a_bundle_could_use() -> None:
    """The suffix set is derived from the local-model layout prober, not a second copy of it."""
    names = [
        "personalclaw/models/a.gguf",
        "personalclaw/models/b.onnx",
        "personalclaw/models/c.safetensors",
        "personalclaw/static/dist/index.html",
        "personalclaw/__init__.py",
    ]
    found = weight_shaped_members(names)
    assert found == [
        "personalclaw/models/a.gguf",
        "personalclaw/models/b.onnx",
        "personalclaw/models/c.safetensors",
    ], found
    assert ".gguf" in rail.WEIGHT_SUFFIXES and "" not in rail.WEIGHT_SUFFIXES


# ── the sign-off record: a PARTIAL record is refused, not ignored ──────────────────────────────


def test_the_repository_record_declares_no_bundle_today() -> None:
    """The owner-gated clause, asserted as a fact rather than assumed.

    If this ever fails, a model HAS been signed off — which is good news and means the drive
    below should be flipping to MET. It is asserted so the state is measured, not inferred from
    a reader's memory of a shift note.
    """
    assert repo_declaration(_REPO_ROOT) is None, (
        "a bundled model is now signed off in docs/architecture/bundled-model-signoff.txt — "
        "re-read this file's docstring: the drive's verdict and this test both flip with it"
    )


def test_the_record_form_is_readable_and_names_every_key() -> None:
    """The form must parse (as 'nothing declared') and must document all six keys.

    A form whose commented example drifts from the parser's key set is a form that produces a
    refusal on the owner's first attempt to fill it in.
    """
    text = (_REPO_ROOT / rail.DECLARATION_RELPATH).read_text(encoding="utf-8")
    assert parse_declaration(text) is None
    for key in rail.DECLARATION_KEYS:
        assert key in text, f"the sign-off form never mentions the required key {key!r}"


@pytest.mark.parametrize("dropped", rail.DECLARATION_KEYS)
def test_a_partial_record_is_refused_naming_what_is_missing(dropped: str) -> None:
    """Every single-key omission reds. Read as 'no bundle' it would ship untermed bytes."""
    lines = [ln for ln in _record().splitlines() if not ln.startswith(f"{dropped}:")]
    with pytest.raises(BundleDeclarationError, match=dropped):
        parse_declaration("\n".join(lines))


def test_a_complete_record_round_trips() -> None:
    """The control arm for the omission matrix above."""
    declaration = parse_declaration(_record())
    assert declaration == BundleDeclaration(
        model_id="example-org/example-model",
        licence="Apache-2.0",
        licence_url="https://example.com/LICENSE",
        artifact="personalclaw/models/example.gguf",
        sha256=_SIXTY_FOUR_HEX,
        size_budget_bytes=1024,
    )


def test_an_unknown_key_is_refused_rather_than_read_as_an_absent_one() -> None:
    """A typo'd key would otherwise silently become a missing key with a confusing message."""
    with pytest.raises(BundleDeclarationError, match="unknown key"):
        parse_declaration(_record() + "licence_uri: https://example.com/LICENSE\n")


def test_a_key_declared_twice_is_refused() -> None:
    """One record, one value — so there is no question which one the gate enforced."""
    with pytest.raises(BundleDeclarationError, match="declared twice"):
        parse_declaration(_record() + "licence: MIT\n")


@pytest.mark.parametrize("budget", ["488 MiB", "491MB", "1.5e9", "half a gig", "-1"])
def test_a_unit_bearing_or_non_integer_budget_is_refused(budget: str) -> None:
    """Bytes as a plain integer, because MB-vs-MiB ambiguity would land in the one number
    the gate enforces."""
    with pytest.raises(BundleDeclarationError, match="plain integer"):
        parse_declaration(_record(size_budget_bytes=budget))


@pytest.mark.parametrize("digest", ["deadbeef", "z" * 64, "A" * 63])
def test_a_malformed_digest_is_refused(digest: str) -> None:
    """The digest is the chain to the shipped bytes; a truncated one chains to nothing."""
    with pytest.raises(BundleDeclarationError, match="64 hex"):
        parse_declaration(_record(sha256=digest))


def test_an_uppercase_digest_is_accepted_and_normalised() -> None:
    """``shasum`` output case must not be a refusal — the bytes are the same."""
    declaration = parse_declaration(_record(sha256="A" * 64))
    assert declaration is not None and declaration.sha256 == "a" * 64


# ── clause 3: the end-to-end drive ────────────────────────────────────────────────────────────


def _load_by_path(path: Path, name: str):
    """Import a ``scripts/`` file by path — ``scripts/`` is not a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _drive_module():
    return _load_by_path(_DRIVE, "_ou14_drive_under_test")


def _run_drive(cwd: Path | None = None) -> tuple[int, dict[str, object]]:
    """Run the real drive script in its own process and parse its JSON.

    Its own process on purpose: the script sets ``PERSONALCLAW_HOME`` BEFORE importing
    ``personalclaw``, which is the only ordering that drives the home it reports, and it cannot
    do that inside an already-imported test session.
    """
    proc = subprocess.run(
        [sys.executable, str(_DRIVE), "--json"],
        cwd=str(cwd or _REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.stdout.strip(), f"the drive printed nothing (stderr={proc.stderr[-2000:]!r})"
    return proc.returncode, json.loads(proc.stdout)


def test_the_drive_reaches_the_resolver_on_a_throwaway_home_and_writes_no_credential() -> None:
    """Clause 4, driven. Today it measures the promise FAILING, and says so.

    The assertions that are true regardless of the owner act: the drive reaches a real
    resolution attempt on a fresh unbound home, writes no credential, and reports a verdict
    consistent with the (empty) sign-off record. What flips with the owner act is
    ``zero_config_first_chat``.
    """
    code, obs = _run_drive()
    assert obs["bundle_declared"] is False, obs
    assert obs["chat_resolved"] is False, obs
    assert obs["error_code"] == "ERR_MODEL_UNRESOLVED", obs
    assert obs["promise"] == "UNMET", obs
    assert obs["credential_written"] is False, obs
    assert obs["consistent"] is True, obs["why"]
    assert code == 0, f"exit {code}: {obs['why']}"


def test_the_drive_leaves_no_trace_in_the_throwaway_home() -> None:
    """Zero writes is part of the clause: reaching a bundled model must cost no state."""
    _code, obs = _run_drive()
    assert obs["home_entries_after"] == [], obs["home_entries_after"]


def test_the_drive_contradicts_a_declared_bundle_with_nothing_runnable(tmp_path: Path) -> None:
    """The drive's TEETH — the one clause the escalation judged uncheatable stays uncheatable.

    A sign-off record is planted in a throwaway copy of the repository's docs path while the
    tree stays exactly as committed, so the drive sees "a model is declared" and still finds
    nothing that resolves chat. It must report a CONTRADICTION and exit non-zero. A drive that
    answered MET here would be a rubber stamp for the very clause that has to stay honest.

    Nothing in the real tree is touched: the planted record lives under ``tmp_path``, and the
    drive is pointed at it by running from a directory tree whose ``docs/`` is the fake one.
    """
    fake_root = tmp_path / "repo"
    (fake_root / "docs" / "architecture").mkdir(parents=True)
    (fake_root / rail.DECLARATION_RELPATH).write_text(_record(), encoding="utf-8")
    (fake_root / "scripts").mkdir()
    (fake_root / "src").symlink_to(_REPO_ROOT / "src")
    drive_copy = fake_root / "scripts" / _DRIVE.name
    drive_copy.write_text(_DRIVE.read_text(encoding="utf-8"), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(drive_copy), "--json"],
        cwd=str(fake_root),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.stdout.strip(), f"the drive printed nothing (stderr={proc.stderr[-2000:]!r})"
    obs = json.loads(proc.stdout)
    assert obs["bundle_declared"] is True, obs
    assert obs["consistent"] is False, obs
    assert obs["promise"] == "UNMET", obs
    assert "contradiction" in str(obs["why"]).lower(), obs["why"]
    assert proc.returncode == 1, f"a contradiction must exit non-zero, got {proc.returncode}"

    # And the real record is untouched — the teeth test must not have signed anything off.
    assert repo_declaration(_REPO_ROOT) is None


def test_the_drive_refuses_the_real_home() -> None:
    """A probe that writes to ``~/.personalclaw`` is an accident, not an observation.

    Asserted IN-PROCESS on the guard itself rather than by launching the script with
    ``--home ~/.personalclaw``: if the guard were broken, that launch would be the very write
    this test exists to forbid. Driving the guard directly cannot touch the real home whether it
    works or not.
    """
    module = _drive_module()
    with pytest.raises(SystemExit, match="refusing to drive against the real home"):
        module._refuse_real_home(Path.home() / ".personalclaw")
    # The control arm: an ordinary throwaway path passes the guard, so the refusal above is
    # caused by the path and not by a guard that refuses everything.
    module._refuse_real_home(Path("/tmp/ou14-not-a-real-home"))


# ── the call sites: a perfect rail with no caller is the failure mode this repo keeps finding ──


def _verify_wheel_module():
    return _load_by_path(_VERIFY_WHEEL, "_verify_wheel_ou14")


def test_the_release_gate_has_the_bundled_model_assertion_at_all() -> None:
    """Vacuity floor. ``getattr`` so a MISSING function reports as this, not an AttributeError."""
    module = _verify_wheel_module()
    assert getattr(module, "_assert_bundled_model_admitted", None) is not None, (
        "scripts/verify_wheel.py no longer defines _assert_bundled_model_admitted — the "
        "bundled-model size/licence gate has left the release path"
    )


def test_main_actually_calls_the_bundled_model_assertion() -> None:
    """Asserted on the AST of ``main``, because a detector with no call site guards nothing.

    The same shape as ``test_verify_wheel_contract.py``'s assertion-6 call-site check, and for
    the same reason: this repository has repeatedly shipped a correct check that nothing ran.
    """
    tree = ast.parse(_VERIFY_WHEEL.read_text(encoding="utf-8"))
    main = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    assert main is not None, "scripts/verify_wheel.py has no main()"
    called = {
        node.func.id
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_assert_bundled_model_admitted" in called, (
        "main() does not call _assert_bundled_model_admitted — the gate is defined and never "
        f"runs. Calls found: {sorted(called)}"
    )


def test_the_release_gate_reports_the_no_bundle_state_out_loud(tmp_path: Path, capsys) -> None:
    """The release path's own vacuity line, driven through the REAL assertion.

    Not a source grep: the assertion is called against a wheel with no weight and its printed
    output is read back, so a future refactor that drops the summary line reds here.
    """
    module = _verify_wheel_module()
    wheel = _wheel(tmp_path, {"personalclaw/x.py": b"x = 1\n"})
    module._assert_bundled_model_admitted(wheel)
    printed = capsys.readouterr().out
    assert "NO BUNDLE DECLARED" in printed, printed


def test_the_release_gate_fails_the_build_on_an_undeclared_weight(tmp_path: Path) -> None:
    """The refusal reaches the RELEASE, not just the rail: a non-zero exit, with the reason."""
    module = _verify_wheel_module()
    wheel = _wheel(tmp_path, {"personalclaw/models/smuggled.gguf": b"w" * (2 * 1024 * 1024)})
    with pytest.raises(SystemExit) as exit_info:
        module._assert_bundled_model_admitted(wheel)
    assert exit_info.value.code == 1


def test_admit_bundle_covers_all_four_declaration_by_artifact_cases() -> None:
    """The 2x2 is exhaustive and each cell has its own state, so none can be reached by accident."""
    measured = rail.MeasuredArtifact(member="m.gguf", size_bytes=900, sha256=_SIXTY_FOUR_HEX)
    declaration = parse_declaration(_record(artifact="m.gguf"))
    assert admit_bundle(None, None).state == STATE_NO_BUNDLE
    assert admit_bundle(None, measured).state == STATE_REFUSED
    assert admit_bundle(declaration, None).state == STATE_REFUSED
    assert admit_bundle(declaration, measured).state == STATE_ADMITTED


def test_measure_wheel_finds_a_member_declared_without_the_package_prefix(tmp_path: Path) -> None:
    """A record may name the artifact as the wheel stores it or as a package-relative path."""
    wheel = _wheel(tmp_path, {"personalclaw/models/example.gguf": b"w" * 900})
    declaration = parse_declaration(_record(artifact="models/example.gguf"))
    measured = measure_wheel(wheel, declaration)
    assert measured is not None and measured.member == "personalclaw/models/example.gguf"

"""OU-14 — the bundled-model admission rails, and the zero-config drive that ties them down.

A model IS now signed off (``unsloth/SmolLM2-135M-Instruct-GGUF``, Apache-2.0) and the drive
reports MET when its weight is present. That changes what this file has to prove: the rails were
written when the record was empty, so their teeth were the only thing being asserted. Now that a
real record exists, the teeth AND the record's own contents both have to hold — an allowlisted
licence identifier, a 64-hex digest, a declared ceiling, a pinned https source — and the drive
has to stay honest in a tree that has not fetched the weight.

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
``PERSONALCLAW_HOME``, bootstrapping providers the way a real process does and COMPLETING one
chat turn. Its teeth are
:func:`test_the_drive_contradicts_a_declared_bundle_with_nothing_runnable`: hand the drive a
sign-off record whose weight is installed and with no runnable provider behind it, and it must
report a CONTRADICTION. A drive that said MET there would be a rubber stamp, and this clause is
the only one of the four the escalation judged uncheatable — so it is the one that must not be
cheatable here either.

**What a green run here does NOT mean.** It cannot judge whether a licence is *genuinely*
permissive: it compares a declared identifier against an allowlist. Whether the identifier tells
the truth about the model is the sign-off, and the sign-off is a human act. It also does not
prove the shipped weight ANSWERS — that is
``tests/test_bundled_chat_provider.py`` (the executor, against an independent reference) plus the
drive's own quoted reply on a tree that has fetched the weight.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

from personalclaw import bundled_model as rail
from personalclaw.bundled_model import (
    PERMITTED_LICENCES,
    STATE_ADMITTED,
    STATE_REFUSED,
    BundleDeclaration,
    BundleDeclarationError,
    gate_wheel,
    licence_decision,
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
    not to stand in for the owner's choice.
    """
    fields: dict[str, object] = {
        "model_id": "example-org/example-model",
        "licence": "Apache-2.0",
        "licence_url": "https://example.com/LICENSE",
        "artifact": "models/example-app/example.gguf",
        "sha256": _SIXTY_FOUR_HEX,
        "size_bytes": 1024,
        "size_budget_bytes": 1024,
        "source_url": "https://example.com/example.gguf",
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

    The owner's sign-off binds the set to genuinely OSI-permissive licences — Apache-2.0 or
    MIT. Pinning it here means a PR that quietly adds a third entry reds with this test naming
    it, rather than shipping a licence nobody ruled on.
    """
    assert set(PERMITTED_LICENCES) == {"apache-2.0", "mit"}, (
        f"the permitted-licence allowlist is {sorted(PERMITTED_LICENCES)}. The owner's "
        "sign-off binds it to Apache-2.0 or MIT. Changing it is a governance decision: say "
        "what was added, why it is genuinely OSI-permissive, and update this pin in the same "
        "commit."
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


# ── the WHEEL gate: it must carry NO weight, and stay small ────────────────────────────────────
#
# 🔴 This whole section asserts the OPPOSITE of what it asserted on 2026-09-23, when the wheel was
# required to CARRY the signed-off weight. The owner settled the shape the day after — "the
# intention was always to ship the wheel without the weight and fetch it on first run" — so a
# weight in the wheel is now the defect. Both directions are pinned here on a REAL zip, because a
# gate left pointing the old way would have refused every release, and one pointing neither way
# would let a 138 MiB weight back into a ~9 MiB wheel where PyPI's 100 MiB per-file limit turns it
# into a rejected upload of an already-tagged release.


def test_a_clean_wheel_is_admitted_and_the_summary_states_both_measurements() -> None:
    """The happy path, on the REAL built-shape wheel members. Never a bare OK."""
    wheel = _wheel(Path(tempfile.mkdtemp()), {"personalclaw/__init__.py": b"x = 1\n"})
    result = gate_wheel(wheel)
    assert result.state == STATE_ADMITTED, result.refusals
    assert result.ok is True
    assert "carries no model weight" in result.summary
    assert "ceiling" in result.summary


def test_a_weight_in_the_wheel_is_refused_and_the_glob_is_named(tmp_path: Path) -> None:
    """THE regression this gate exists for.

    The refusal has to name the CAUSE (a package-data glob) rather than the symptom, because
    the person reading it is looking at a release log and the fix is one line of pyproject.
    """
    wheel = _wheel(
        tmp_path, {"personalclaw/apps/native/x/weights/m.gguf": b"w" * (2 * 1024 * 1024)}
    )
    result = gate_wheel(wheel)
    assert result.state == STATE_REFUSED
    assert not result.ok
    joined = " ".join(result.refusals)
    assert "m.gguf" in joined
    assert "package-data" in joined
    assert "100 MiB" in joined


@pytest.mark.parametrize("suffix", [".gguf", ".safetensors", ".onnx", ".bin", ".pt"])
def test_every_weight_shape_is_refused_not_just_the_one_we_ship(
    tmp_path: Path, suffix: str
) -> None:
    """The next weight will not be a GGUF. The suffix set comes from core's own layout prober,
    so a format the local-model machinery can download is a format this gate can catch."""
    wheel = _wheel(tmp_path, {f"personalclaw/m{suffix}": b"w" * (2 * 1024 * 1024)})
    assert gate_wheel(wheel).state == STATE_REFUSED


def test_a_small_weight_shaped_fixture_does_not_trip_the_scan(tmp_path: Path) -> None:
    """The floor, proven. Without it every `.bin` test fixture in the wheel would red a
    release — and the wheel really does ship several under `tests_fixtures/`."""
    wheel = _wheel(tmp_path, {"personalclaw/tests_fixtures/tiny.bin": b"x" * 10})
    assert gate_wheel(wheel).state == STATE_ADMITTED


def test_an_oversized_wheel_is_refused_even_with_no_weight_shaped_member(tmp_path: Path) -> None:
    """The form the member scan cannot see: the same weight arriving as shards, or as one
    enormous member with an innocent name. Two assertions, one regression."""
    wheel = _wheel(tmp_path, {"personalclaw/big.dat": b"x" * (3 * 1024 * 1024)})
    result = gate_wheel(wheel, max_bytes=1024 * 1024)
    assert result.state == STATE_REFUSED
    assert "ceiling" in " ".join(result.refusals)
    # …and the same wheel passes under a ceiling that admits it, so the refusal is caused by
    # the number and not by the gate refusing everything.
    assert gate_wheel(wheel, max_bytes=64 * 1024 * 1024).state == STATE_ADMITTED


def test_the_declared_ceiling_leaves_room_for_the_wheel_we_actually_ship() -> None:
    """A ceiling below the real wheel would red every release; one at a gigabyte would catch
    nothing. 9.2 MiB is the measured 0.1.3 wheel on PyPI."""
    assert rail.MAX_WHEEL_BYTES > 10 * 1024 * 1024
    assert rail.MAX_WHEEL_BYTES < 100 * 1024 * 1024, (
        "the ceiling must stay under PyPI's 100 MiB per-file limit, or it cannot catch the "
        "regression it exists for before PyPI does"
    )


# ── verifying a DOWNLOADED weight — the security control on the fetch path ────────────────────


def _declared(**over: object):
    parsed = parse_declaration(_record(**over))
    assert parsed is not None
    return parsed


def test_verified_bytes_are_admitted_and_the_digest_is_reported(tmp_path: Path) -> None:
    payload = b"w" * 4096
    digest = hashlib.sha256(payload).hexdigest()
    path = tmp_path / "m.gguf"
    path.write_bytes(payload)
    result = rail.verify_download(
        path, _declared(sha256=digest, size_bytes=len(payload), size_budget_bytes=len(payload))
    )
    assert result.outcome == rail.DOWNLOAD_OK
    assert result.ok and result.sha256 == digest and result.bytes_received == len(payload)


def test_an_absent_or_empty_download_is_truncated_not_ok(tmp_path: Path) -> None:
    declaration = _declared(size_bytes=4096, size_budget_bytes=8192)
    assert rail.verify_download(tmp_path / "nope.gguf", declaration).outcome == (
        rail.DOWNLOAD_TRUNCATED
    )
    empty = tmp_path / "empty.gguf"
    empty.write_bytes(b"")
    assert rail.verify_download(empty, declaration).outcome == rail.DOWNLOAD_TRUNCATED


def test_a_short_transfer_is_truncated_by_ARITHMETIC_before_the_digest(tmp_path: Path) -> None:
    """Named separately, and deliberately cheap.

    A half-downloaded 138 MiB file must be diagnosed without hashing 70 MiB of it, and the
    message must say "start the download again" — which is right for this failure and wrong for
    a digest mismatch.
    """
    path = tmp_path / "m.gguf"
    path.write_bytes(b"w" * 100)
    result = rail.verify_download(path, _declared(size_bytes=4096, size_budget_bytes=8192))
    assert result.outcome == rail.DOWNLOAD_TRUNCATED
    assert "start the download again" in result.detail


def test_an_over_budget_download_is_refused_by_its_own_outcome(tmp_path: Path) -> None:
    path = tmp_path / "m.gguf"
    path.write_bytes(b"w" * 9000)
    result = rail.verify_download(path, _declared(size_bytes=4096, size_budget_bytes=8192))
    assert result.outcome == rail.DOWNLOAD_OVER_BUDGET


def test_a_digest_mismatch_says_retrying_is_not_the_fix(tmp_path: Path) -> None:
    """The security control, and the one failure whose advice is NOT "try again".

    The source pins an immutable revision, so bytes that hash differently mean the pin is wrong
    or something rewrote the transfer. Telling a user to retry would send them round a loop.
    """
    payload = b"w" * 4096
    path = tmp_path / "m.gguf"
    path.write_bytes(payload)
    result = rail.verify_download(
        path, _declared(sha256="b" * 64, size_bytes=len(payload), size_budget_bytes=len(payload))
    )
    assert result.outcome == rail.DOWNLOAD_DIGEST_MISMATCH
    assert not result.ok
    assert "retrying the same URL is not the fix" in result.detail
    assert hashlib.sha256(payload).hexdigest() in result.detail


def test_sha256_file_chunks_rather_than_reading_the_whole_file(tmp_path: Path) -> None:
    """A 138 MiB file must not be held twice in memory; the chunk size is a parameter so the
    loop is exercised rather than assumed."""
    payload = bytes(range(256)) * 1000
    path = tmp_path / "big.bin"
    path.write_bytes(payload)
    assert rail.sha256_file(path, chunk=64) == hashlib.sha256(payload).hexdigest()


def test_the_four_download_outcomes_are_four_distinct_values() -> None:
    """Their whole reason for existing: no network, a bad status, a short transfer and wrong
    bytes need four different things from a user, and one "download failed" flattens them."""
    outcomes = {
        rail.DOWNLOAD_OK,
        rail.DOWNLOAD_UNREACHABLE,
        rail.DOWNLOAD_BAD_STATUS,
        rail.DOWNLOAD_TRUNCATED,
        rail.DOWNLOAD_DIGEST_MISMATCH,
        rail.DOWNLOAD_OVER_BUDGET,
    }
    assert len(outcomes) == 6
    # A cancel is the download manager's job state, not a verdict about the bytes, so it has no
    # outcome here — and must not grow one: a second spelling is a second authority.
    assert not hasattr(rail, "DOWNLOAD_CANCELLED")


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


def test_the_repository_record_signs_off_a_complete_permissive_bundle() -> None:
    """THE sign-off, asserted field by field on the real committed record.

    Every one of these is a way the record could be filled in and still be wrong in a way no
    other rail would catch: a licence outside the allowlist (the C9 constraint), an artifact
    path the wheel's package-data glob could never carry, a placeholder digest, an unset
    ceiling, or a source that is not a pinned https URL. The generic parser tests below use a
    fixture; this one uses the bytes that ship.
    """
    declaration = repo_declaration(_REPO_ROOT)
    assert declaration is not None, (
        "no bundled model is signed off in docs/architecture/bundled-model-signoff.txt — "
        "OU-14's whole point is that one is"
    )
    assert declaration.model_id == "unsloth/SmolLM2-135M-Instruct-GGUF"
    assert licence_decision(declaration.licence).permitted, declaration.licence
    assert declaration.licence_url.startswith("https://")
    assert declaration.artifact.startswith("models/"), (
        "the artifact is the path the DOWNLOAD lands at, relative to $PERSONALCLAW_HOME — a "
        "`personalclaw/...` wheel member would describe a location nothing writes"
    )
    assert declaration.artifact.endswith(".gguf")
    assert declaration.size_bytes == 144_811_072
    assert declaration.sha256 != _SIXTY_FOUR_HEX, "the digest is still the placeholder"
    assert declaration.size_budget_bytes >= 144_811_072, "the ceiling is below the shipped weight"
    assert declaration.source_url.startswith("https://huggingface.co/")
    # Pinned to an immutable revision, not a branch: `/resolve/main/` would let the bytes
    # behind the signed-off digest change under the record.
    assert "/resolve/main/" not in declaration.source_url


def test_packaging_ships_no_weight_glob_and_does_ship_the_record() -> None:
    """The two packaging facts this shape depends on, asserted on pyproject itself.

    A `*.gguf` glob returning is the regression the wheel gate catches at release time; this
    catches it at PR time, which is cheaper. And the RECORD must ship, because the runtime reads
    the source pin, the digest and the size out of it — a wheel without it is an install that
    can never fetch its model.
    """
    import tomllib

    with (_REPO_ROOT / "pyproject.toml").open("rb") as handle:
        globs = tomllib.load(handle)["tool"]["setuptools"]["package-data"]["personalclaw"]
    # Parsed, not grepped: the block's own prose explains why there is no `*.gguf` entry, and a
    # substring search over the file would match that explanation.
    weighty = [g for g in globs if any(s in g for s in rail.WEIGHT_SUFFIXES)]
    assert weighty == [], (
        f"package-data names weight globs again: {weighty}. The weight is fetched at first use, "
        "not shipped, and a 138 MiB member puts the wheel over PyPI's 100 MiB per-file limit."
    )
    assert "apps/native/*/bundled-model-signoff.txt" in globs
    # The app-dir copy is a SYMLINK to the one authority under docs/, so git holds exactly one
    # record and setuptools dereferences it into the wheel. Two committed copies would be two
    # things that can disagree about which model an install fetches.
    link = _REPO_ROOT / "src/personalclaw/apps/native/bundled-chat/bundled-model-signoff.txt"
    assert link.is_symlink(), f"{link} must be a symlink to the docs/ record, not a copy"
    assert link.resolve() == (_REPO_ROOT / rail.DECLARATION_RELPATH).resolve()


def test_the_record_documents_every_key_it_requires() -> None:
    """A record whose prose drifts from the parser's key set is a record whose next editor
    produces a refusal on their first attempt."""
    text = (_REPO_ROOT / rail.DECLARATION_RELPATH).read_text(encoding="utf-8")
    for key in rail.DECLARATION_KEYS:
        assert key in text, f"the sign-off record never mentions the required key {key!r}"


def test_the_fetch_script_carries_no_url_or_digest_of_its_own() -> None:
    """One fact, one place.

    ``scripts/fetch_bundled_model.py`` reads the source URL and the digest out of the record. If
    it ever grew its own copy, the two could disagree about which bytes were signed off — and
    the disagreement would be invisible, because each half would look internally consistent.
    """
    declaration = repo_declaration(_REPO_ROOT)
    assert declaration is not None
    for path in (
        _REPO_ROOT / "scripts" / "fetch_bundled_model.py",
        _REPO_ROOT / "src/personalclaw/apps/native/bundled-chat/provider.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert declaration.source_url not in source, path
        assert declaration.sha256 not in source, path
        # A hostname is the drift that matters: a downloader that knew where to go would still
        # go there after the record was repointed. (`.lower()` would also match the RoPE
        # comment's `convert_hf_to_gguf.py` attribution, which is prose about arithmetic.)
        assert "huggingface.co" not in source, path


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
        artifact="models/example-app/example.gguf",
        sha256=_SIXTY_FOUR_HEX,
        size_bytes=1024,
        size_budget_bytes=1024,
        source_url="https://example.com/example.gguf",
    )


def test_an_unknown_key_is_refused_rather_than_read_as_an_absent_one() -> None:
    """A typo'd key would otherwise silently become a missing key with a confusing message."""
    with pytest.raises(BundleDeclarationError, match="unknown key"):
        parse_declaration(_record() + "licence_uri: https://example.com/LICENSE\n")


def test_a_key_declared_twice_is_refused() -> None:
    """One record, one value — so there is no question which one the gate enforced."""
    with pytest.raises(BundleDeclarationError, match="declared twice"):
        parse_declaration(_record() + "licence: MIT\n")


@pytest.mark.parametrize("budget", ["150 MiB", "491MB", "1.5e9", "half a gig", "-1"])
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


def test_the_drive_is_consistent_and_credential_free_whatever_this_tree_carries() -> None:
    """Clause 4, driven — and written to hold in BOTH trees, without skipping in either.

    A CI checkout has no weight (it is not in git); a release build and a dev tree that ran
    ``make bundled-model`` do. Those are genuinely different observations, so the invariants
    asserted here are the ones that must hold in both: a signed-off record, an internally
    CONSISTENT verdict, no credential, no persisted provider row — and, where the weight IS
    present, a completed turn with real text.

    Written this way rather than with a skip because the weight-present arm is precisely the
    arm OU-14 is about: ``pytest.skip`` on a CI runner would silently retire it.
    """
    code, obs = _run_drive()
    assert obs["bundle_declared"] is True, obs
    assert obs["bundle_licence"] == "Apache-2.0", obs
    assert obs["credential_written"] is False, obs
    assert obs["providers_persisted"] is False, obs
    assert obs["consistent"] is True, obs["why"]
    assert code == 0, f"exit {code}: {obs['why']}"
    if obs["weight_present"]:
        assert obs["promise"] == "MET", obs["why"]
        assert obs["chat_resolved"] is True, obs
        assert obs["chat_model_provider"] == "BundledChatProvider", obs
        assert str(obs["first_turn_reply"] or "").strip(), (
            "the weight is installed and chat resolved, but the first turn produced no text — "
            "resolution is not the clause"
        )
    else:
        assert obs["promise"] == "UNMET", obs["why"]
        assert obs["chat_resolved"] is False, obs
        assert obs["error_code"] == "ERR_MODEL_UNRESOLVED", obs
        assert "fetch_bundled_model" in str(obs["why"]), obs["why"]


def test_the_drive_writes_no_credential_and_no_provider_row() -> None:
    """The cost half of the clause.

    NOT "the home is untouched": bootstrapping providers legitimately creates first-boot state
    (an ``apps/`` dir, the memory store) exactly as a real gateway does, and asserting emptiness
    would have forced the drive to stop being representative. What must stay absent is anything
    the zero-config path is not allowed to cost — a credential, and a PERSISTED provider entry.
    An in-memory floor entry vanishes with the app; a ``config.json`` row would outlive it and
    become a stale pin naming a provider that is gone.
    """
    _code, obs = _run_drive()
    assert obs["credential_written"] is False, obs
    assert obs["providers_persisted"] is False, obs
    assert ".env" not in obs["home_entries_after"], obs["home_entries_after"]


def test_the_drive_and_the_runtime_read_ONE_record() -> None:
    """The drive must assert against the record the RUNTIME uses, not a second read of the repo.

    Measured while writing this: an earlier drive called ``bundled_model.repo_declaration`` on
    the repo root while the app read its own packaged copy. Planting a record at the repo root
    therefore changed what the drive *reported* and nothing about what the app *did* — a probe
    that can disagree with the thing it is probing. The drive now asks the app, and this asserts
    it keeps doing so, because the failure is silent: both readers return a valid record.
    """
    source = _DRIVE.read_text(encoding="utf-8")
    assert "app._declaration()" in source
    assert "repo_declaration" not in source, (
        "the drive reads the record from the repo again — it must read the one the app reads, "
        "or it can report on a record the runtime is not using"
    )


@pytest.mark.parametrize(
    "overrides,want_consistent,want_promise,want_phrase",
    [
        # THE contradiction: the weight ships and nothing runs it.
        (
            {"weight_present": True, "chat_resolved": False, "error_code": "ERR_MODEL_UNRESOLVED"},
            False,
            "UNMET",
            "contradiction",
        ),
        # Resolved, but the turn produced nothing. Resolution is NOT the clause.
        ({"first_turn_reply": ""}, False, "UNMET", "NO text"),
        # A credential was written — the zero-config path must cost no secret.
        ({"credential_written": True}, False, "UNMET", "credential"),
        # A provider row was persisted — that outlives the bundle and becomes a stale pin.
        ({"providers_persisted": True}, False, "UNMET", "config.json"),
        # Something resolved that no record declares.
        (
            {"bundle_declared": False, "bundle_model_id": None},
            False,
            "MET",
            "NOTHING is signed off",
        ),
        # No network: declared, the fetch failed, correctly unresolved. CONSISTENT + UNMET, and
        # the REASON is carried through rather than flattened into "no model".
        (
            {
                "weight_present": False,
                "chat_resolved": False,
                "first_turn_reply": None,
                "fetch_error": "[unreachable] could not reach the model source",
            },
            True,
            "UNMET",
            "unreachable",
        ),
        # The kept promise.
        ({}, True, "MET", "COMPLETED"),
    ],
)
def test_the_drive_verdict_cannot_be_talked_into_met(
    overrides: dict, want_consistent: bool, want_promise: str, want_phrase: str
) -> None:
    """The drive's TEETH — every way a green could be manufactured, refused one by one.

    Driven on ``verdict()`` in-process rather than by launching the script, because each branch
    needs a world the script cannot be *put* in from outside: "the weight is installed and
    nothing resolves it" requires a tree that carries the weight AND has no bundled provider,
    which is not a state a test can construct without either deleting the app from the source
    tree or adding a production off-switch that exists only for this test. ``verdict()`` is
    where the MET/UNMET decision actually lives and it is a pure function of the observation, so
    driving it directly tests the decision rather than a re-implementation of it — and the
    subprocess test above already proves the real script's observation feeds this function.
    """
    module = _drive_module()
    observation: dict[str, object] = {
        "bundle_declared": True,
        "bundle_model_id": "example-org/example-model",
        "weight_present": True,
        "chat_resolved": True,
        "chat_provider": "NativeAgentRuntime",
        "chat_model_provider": "BundledChatProvider",
        "first_turn_reply": "Paris.",
        "credential_written": False,
        "providers_persisted": False,
        "error_code": None,
        "fetch_error": "",
    }
    observation.update(overrides)
    consistent, promise, why = module.verdict(observation)
    assert consistent is want_consistent, why
    assert promise == want_promise, why
    assert want_phrase.lower() in why.lower(), why


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
    assert getattr(module, "_assert_no_bundled_weight_in_wheel", None) is not None, (
        "scripts/verify_wheel.py no longer defines _assert_no_bundled_weight_in_wheel — the "
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
    assert "_assert_no_bundled_weight_in_wheel" in called, (
        "main() does not call _assert_no_bundled_weight_in_wheel — the gate is defined and never "
        f"runs. Calls found: {sorted(called)}"
    )


def test_the_release_gate_admits_the_wheel_we_actually_build(tmp_path: Path, capsys) -> None:
    """The direction that matters most: a NORMAL wheel must PASS.

    This is the arm a mis-pointed gate breaks. On 2026-09-23 assertion 7 required the wheel to
    CARRY the signed-off weight; if it had been left that way, every release from then on would
    have failed here — and it would have failed on a tag, after the version bump. Driven through
    the REAL assertion so a future flip reds on a PR instead.
    """
    module = _verify_wheel_module()
    wheel = _wheel(tmp_path, {"personalclaw/__init__.py": b"x = 1\n"})
    module._assert_no_bundled_weight_in_wheel(wheel)
    printed = capsys.readouterr().out
    assert "signed off under Apache-2.0" in printed, printed
    assert "carries no model weight" in printed, printed


def test_the_release_gate_fails_the_build_on_a_weight_in_the_wheel(tmp_path: Path) -> None:
    """The refusal reaches the RELEASE, not just the rail: a non-zero exit, with the reason.

    A weight in the wheel is the defect now. PyPI's 100 MiB per-file limit means the alternative
    place to learn this is a rejected upload of an already-tagged release.
    """
    module = _verify_wheel_module()
    wheel = _wheel(tmp_path, {"personalclaw/weights/smuggled.gguf": b"w" * (2 * 1024 * 1024)})
    with pytest.raises(SystemExit) as exit_info:
        module._assert_no_bundled_weight_in_wheel(wheel)
    assert exit_info.value.code == 1


def test_the_release_gate_still_reads_the_record_and_its_licence(tmp_path: Path) -> None:
    """The record is not decorative on the release path even though the weight is not in the
    wheel: it carries the source pin, the digest and the size that every install's first-run
    fetch depends on, so a release shipping an unreadable or non-permissive one ships an install
    that can never fetch its model. Asserted by pointing the rail at a tree whose record is
    broken and checking the gate exits."""
    module = _verify_wheel_module()
    wheel = _wheel(tmp_path, {"personalclaw/__init__.py": b"x = 1\n"})
    broken = tmp_path / "root"
    (broken / "docs" / "architecture").mkdir(parents=True)
    (broken / rail.DECLARATION_RELPATH).write_text("licence: Apache-2.0\n", encoding="utf-8")
    module._load_bundled_model_rail = lambda: (broken, rail)  # type: ignore[attr-defined]
    with pytest.raises(SystemExit) as exit_info:
        module._assert_no_bundled_weight_in_wheel(wheel)
    assert exit_info.value.code == 1

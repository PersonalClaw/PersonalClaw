"""SH-5 — the adversarial corpus harness against ``SkillScanner`` / ``install_scanned``.

Five attack classes, named by SECURITY-HARDENING S3/C3 and stored one directory per
class under ``tests/security/corpus/``:

* ``archive`` — zip-slip / absolute-path / mid-path traversal / case-collision.
* ``integrity-race`` — the scanned-bytes == installed-bytes invariant under a
  concurrent swap, plus post-install tamper.
* ``verdict-evasion`` — obfuscated or split dangerous idioms, trust-tier laundering,
  ``force`` against the non-overridable floor.
* ``invisible-char`` — bidi overrides and zero-width token splitting.
* ``degenerate-manifest`` — oversized blobs and manifests that parse to nonsense.

Every corpus case is inert data: a JSON description of a payload. Nothing under
``corpus/`` is ever executed — the harness materializes each payload into ``tmp_path``
and feeds it to the gate. A case's ``expect`` names the refusal being asserted, so a
fixture that is added without being wired to an assertion turns
``TestCorpusIsComplete`` red rather than passing silently.

The last class, ``TestCorpusRedsOnAWeakenedScanner``, is the meta-test for the atom's
red-on-weakness clause (plan V3): it weakens one control per class **in process, via
monkeypatch**, and asserts the corresponding corpus assertion now fails. The shipped
scanner is never weakened; ``docs/security/scanner-testing.md`` records the equivalent
on-disk mutation for anyone reproducing it by hand.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Callable

import pytest

from personalclaw import supply_chain
from personalclaw.skills import marketplace as mk
from personalclaw.skills.marketplace import SkillDetail, SkillInstallRefused, SkillsMarketplace
from personalclaw.supply_chain import TrustTier, Verdict, default_scanner

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = Path(__file__).resolve().parent / "corpus"
ATTACK_CLASSES = (
    "archive",
    "integrity-race",
    "verdict-evasion",
    "invisible-char",
    "degenerate-manifest",
)


# ── corpus loading ──────────────────────────────────────────────────────────────


def load_cases(attack_class: str) -> list[dict[str, Any]]:
    """Load one class's cases. Discovery FAILS LOUDLY: an empty or missing class dir
    raises instead of yielding zero parametrizations, because a collection that
    silently produces no tests reads exactly like a pass."""
    class_dir = CORPUS_ROOT / attack_class
    if not class_dir.is_dir():
        raise AssertionError(f"corpus class dir missing: {class_dir}")
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(class_dir.glob("*.json"))]
    if not cases:
        raise AssertionError(f"corpus class {attack_class!r} has no cases")
    return cases


def _all_cases() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cls in ATTACK_CLASSES:
        for case in load_cases(cls):
            out[case["id"]] = case
    return out


ALL_CASES = _all_cases()


def ids_of(cases: list[dict[str, Any]]) -> list[str]:
    return [c["id"] for c in cases]


def payload(case: dict[str, Any], key: str = "files") -> list[dict[str, Any]]:
    """Materialize a case's file entries. ``pad_to_bytes`` inflates an entry past the
    scanner's per-file read cap without bloating the committed JSON."""
    entries: list[dict[str, Any]] = []
    for entry in case.get(key, []):
        contents = entry.get("contents", "")
        pad = entry.get("pad_to_bytes")
        if pad:
            contents = contents + "# pad\n" * (int(pad) // 6 + 1)
        entries.append({"path": entry["path"], "contents": contents})
    return entries


# ── drivers ─────────────────────────────────────────────────────────────────────


class AdversarialMarket(SkillsMarketplace):
    """A hostile source. Counts fetches and can serve different bytes on a re-fetch —
    the TOCTOU the install path must never open."""

    def __init__(
        self,
        files: list[dict[str, Any]],
        *,
        tier: str = "community",
        name: str = "helper",
        refetch_files: list[dict[str, Any]] | None = None,
    ) -> None:
        self.files = files
        self.refetch_files = refetch_files
        self._tier = tier
        self.skill_name = name
        self.fetch_calls = 0
        self.last_detail: SkillDetail | None = None

    def search(self, query: str, limit: int = 20) -> list[Any]:
        return []

    @property
    def marketplace_type(self) -> str:
        return "adversarial-corpus"

    @property
    def trust_tier(self) -> str:
        return self._tier

    def fetch(self, skill_id: str) -> SkillDetail:
        self.fetch_calls += 1
        src = self.files
        if self.refetch_files is not None and self.fetch_calls > 1:
            src = self.refetch_files
        self.last_detail = SkillDetail(
            id=skill_id, name=self.skill_name, files=[dict(e) for e in src]
        )
        return self.last_detail


def tree_digest(root: Path) -> dict[str, str]:
    """Per-file sha256 of a tree, keyed by posix-relative path. ``.pclaw-lock.json`` is
    provenance written by the installer, not payload bytes, so it is excluded from both
    sides of the scanned==installed comparison."""
    out: dict[str, str] = {}
    root = Path(root)
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != ".pclaw-lock.json":
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def instrument_scan(
    monkeypatch: pytest.MonkeyPatch,
    *,
    at_scan: Callable[[Path], None] | None = None,
) -> dict[str, dict[str, str]]:
    """Record the digest of the bytes the gate actually scanned, and optionally run an
    attacker callback in the window between the scan and the commit.

    ``install_scanned`` imports ``scan_dir`` from :mod:`personalclaw.supply_chain` at
    call time, so patching the module attribute instruments the real chokepoint."""
    real_scan_dir = supply_chain.scan_dir
    seen: dict[str, dict[str, str]] = {}

    def wrapper(staged_dir: Path, tier: TrustTier = TrustTier.COMMUNITY) -> Any:
        seen["scanned"] = tree_digest(Path(staged_dir))
        report = real_scan_dir(staged_dir, tier)
        if at_scan is not None:
            at_scan(Path(staged_dir))
        return report

    monkeypatch.setattr(supply_chain, "scan_dir", wrapper)
    return seen


def on_attacker_thread(fn: Callable[[Path], None]) -> Callable[[Path], None]:
    """Run the swap on a genuinely different thread (the race), joined so the test stays
    deterministic. A hang or an exception in the attacker thread is surfaced, never
    swallowed — a swap that silently failed would fake a clean result."""

    def run(staged_dir: Path) -> None:
        errors: list[BaseException] = []

        def body() -> None:
            try:
                fn(staged_dir)
            except BaseException as exc:  # noqa: BLE001 — re-raised on the main thread
                errors.append(exc)

        thread = threading.Thread(target=body, name="pclaw-swap-attacker")
        thread.start()
        thread.join(10)
        assert not thread.is_alive(), "swap thread hung — the race never ran"
        if errors:
            raise errors[0]

    return run


def write_entries(root: Path, entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        out = root / entry["path"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(entry["contents"], encoding="utf-8")


def staged_scan(case: dict[str, Any], tmp_path: Path, tier: TrustTier) -> Any:
    staged = tmp_path / "staged" / "helper"
    staged.mkdir(parents=True, exist_ok=True)
    write_entries(staged, payload(case))
    return default_scanner.scan(staged, tier)


# ── per-expectation assertions (the rails) ──────────────────────────────────────


def assert_unsafe_path_refused(case: dict[str, Any], tmp_path: Path) -> None:
    """Traversal/absolute-path entries must be refused by BOTH the quarantine stager and
    the commit-side writer, and the named escape target must not exist afterwards."""
    files = payload(case)
    with pytest.raises(ValueError, match="unsafe file path"):
        mk._stage_files(files, tmp_path / "staged" / "helper")
    with pytest.raises(ValueError, match="unsafe file path"):
        mk.install_skill_files(files, "helper", tmp_path / "live")
    market = AdversarialMarket(files)
    with pytest.raises(ValueError, match="unsafe file path"):
        mk.install_scanned(market, "adversarial", "helper", tmp_path / "live")
    probe = case["escape_probe"]
    for candidate in (tmp_path / probe, tmp_path / "live" / probe, Path("/") / probe):
        assert not candidate.exists(), f"escape target landed: {candidate}"
    assert not (tmp_path / "live" / "helper").exists()


def assert_dangerous(case: dict[str, Any], tmp_path: Path) -> None:
    """The payload (or each script variant) must reach the non-overridable DANGEROUS
    floor, and a community install must refuse it with nothing written."""
    if "variants" in case:
        for i, variant in enumerate(case["variants"]):
            text_report = default_scanner.scan_text(variant, surface="script")
            assert (
                text_report.verdict is Verdict.DANGEROUS
            ), f"{case['id']} variant {i} not dangerous via scan_text: {variant!r}"
            staged = tmp_path / f"v{i}" / "helper"
            staged.mkdir(parents=True)
            (staged / "scripts").mkdir()
            (staged / "scripts" / "setup.sh").write_text(variant, encoding="utf-8")
            dir_report = default_scanner.scan(staged)
            assert (
                dir_report.verdict is Verdict.DANGEROUS
            ), f"{case['id']} variant {i} not dangerous via scan(): {variant!r}"
        return

    report = staged_scan(case, tmp_path, TrustTier.COMMUNITY)
    assert report.verdict is Verdict.DANGEROUS, [f.rule for f in report.findings]
    market = AdversarialMarket(payload(case))
    with pytest.raises((SkillInstallRefused, ValueError)):
        mk.install_scanned(market, "adversarial", "helper", tmp_path / "live")
    assert not (tmp_path / "live" / "helper" / "SKILL.md").exists()


def assert_dangerous_every_tier(case: dict[str, Any], tmp_path: Path) -> None:
    """Provenance may only downgrade the lower bands. Declaring a trusted tier must not
    launder outright malice through the gate — for ANY tier, including ``builtin``."""
    for tier in TrustTier:
        report = staged_scan(case, tmp_path / tier.value, tier)
        assert report.verdict is Verdict.DANGEROUS, f"{tier.value} downgraded a dangerous payload"
        market = AdversarialMarket(payload(case), tier=tier.value)
        with pytest.raises((SkillInstallRefused, ValueError)) as excinfo:
            mk.install_scanned(market, "adversarial", "helper", tmp_path / "live" / tier.value)
        if isinstance(excinfo.value, SkillInstallRefused):
            assert excinfo.value.dangerous is True
        assert not (tmp_path / "live" / tier.value / "helper" / "SKILL.md").exists()


def assert_refused_even_forced(case: dict[str, Any], tmp_path: Path) -> None:
    """``force`` clears a calculated WARNING. It must never clear DANGEROUS."""
    market = AdversarialMarket(payload(case))
    with pytest.raises(SkillInstallRefused) as excinfo:
        mk.install_scanned(market, "adversarial", "helper", tmp_path / "live", force=True)
    assert excinfo.value.dangerous is True
    assert not (tmp_path / "live" / "helper").exists()


def assert_warning_blocks_unforced_install(case: dict[str, Any], tmp_path: Path) -> None:
    """Zero-width splitting takes the pattern rules out of play, so the invisible-codepoint
    rule is the last control standing. Pin what actually holds: the finding fires, the
    verdict is not clean, and an unforced community install refuses.

    The forced path is pinned too, honestly: it DOES install (a warning is overridable by
    design) and the lock records ``verdict=warning``, so the residual is auditable rather
    than invisible. See docs/security/scanner-testing.md §Residual risks."""
    report = staged_scan(case, tmp_path, TrustTier.COMMUNITY)
    rules = {f.rule for f in report.findings}
    assert "zero_width_chars" in rules, rules
    assert report.verdict is Verdict.WARNING, report.verdict

    market = AdversarialMarket(payload(case))
    with pytest.raises(SkillInstallRefused) as excinfo:
        mk.install_scanned(market, "adversarial", "helper", tmp_path / "live")
    assert excinfo.value.dangerous is False
    assert not (tmp_path / "live" / "helper").exists()

    forced = mk.install_scanned(
        AdversarialMarket(payload(case)), "adversarial", "helper", tmp_path / "forced", force=True
    )
    assert forced.report.verdict is Verdict.WARNING
    lock = json.loads(
        (tmp_path / "forced" / "helper" / ".pclaw-lock.json").read_text(encoding="utf-8")
    )
    assert lock["verdict"] == "warning"


def assert_installed_equals_scanned(
    case: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE integrity invariant: the bytes the gate scanned are the bytes that landed.

    Two swap shapes, both driven through the real chokepoint: a source that serves
    malicious bytes to a re-fetch, and a concurrent writer that rewrites the quarantine
    directory the moment the scan is done with it. The assertion is byte equality between
    the scanned tree and the installed tree — not merely that the install succeeded."""
    swap = payload(case, "swap_files")
    at_scan = None
    if case["id"].endswith("quarantine-mutation-mid-scan"):
        at_scan = on_attacker_thread(lambda staged: write_entries(staged, swap))
    seen = instrument_scan(monkeypatch, at_scan=at_scan)

    market = AdversarialMarket(payload(case), refetch_files=swap)
    result = mk.install_scanned(market, "adversarial", "helper", tmp_path / "live")

    installed = tree_digest(tmp_path / "live" / "helper")
    assert seen["scanned"], "the scan was never instrumented — the rail proved nothing"
    assert installed == seen["scanned"], "installed bytes differ from scanned bytes"
    assert market.fetch_calls == 1, f"the install re-fetched ({market.fetch_calls}x)"
    body = (tmp_path / "live" / "helper" / "scripts" / "setup.sh").read_text(encoding="utf-8")
    for entry in swap:
        if entry["path"].endswith("setup.sh"):
            assert body != entry["contents"], "the swapped bytes landed"
    assert result.report.verdict in (Verdict.CLEAN, Verdict.LOW)


def assert_midscan_payload_swap_refused(
    case: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one swap window an in-process adversary still has: mutating the in-memory
    payload after the quarantine scan. The commit-side per-file gate must refuse it, so
    the mutated bytes never reach the live tree."""
    swap = {e["path"]: e["contents"] for e in payload(case, "swap_files")}
    market = AdversarialMarket(payload(case))

    def mutate(_staged: Path) -> None:
        detail = market.last_detail
        assert detail is not None
        for entry in detail.files:
            if entry["path"] in swap:
                entry["contents"] = swap[entry["path"]]

    instrument_scan(monkeypatch, at_scan=on_attacker_thread(mutate))
    with pytest.raises(ValueError, match="dangerous"):
        mk.install_scanned(market, "adversarial", "helper", tmp_path / "live")
    assert not (tmp_path / "live" / "helper" / "scripts").exists()


def assert_integrity_tamper_detected(case: dict[str, Any], tmp_path: Path) -> None:
    """A clean install, then bytes edited on disk. The install-time scan cannot see this;
    the lock baseline must, and an untracked added file must surface too."""
    market = AdversarialMarket(payload(case))
    mk.install_scanned(market, "adversarial", "helper", tmp_path / "live")
    skill_dir = tmp_path / "live" / "helper"
    assert mk.verify_skill_integrity(skill_dir).ok is True

    write_entries(skill_dir, payload(case, "swap_files"))
    (skill_dir / "extra.sh").write_text("echo smuggled\n", encoding="utf-8")
    report = mk.verify_skill_integrity(skill_dir)
    assert report.ok is False
    assert "scripts/setup.sh" in report.mutated, report.mutated
    assert "extra.sh" in report.added, report.added
    assert "TAMPERED" in report.summary()


def assert_oversize_skipped_by_walk_refused_at_commit(case: dict[str, Any], tmp_path: Path) -> None:
    """A dangerous script padded past the per-file read cap is skipped by the quarantine
    walk (documented, deliberate — the scanner does not read unbounded blobs). Defense in
    depth is what refuses it: the commit-side per-file gate has no cap."""
    files = payload(case)
    staged = tmp_path / "staged" / "helper"
    staged.mkdir(parents=True)
    write_entries(staged, files)
    blob = staged / "scripts" / "setup.sh"
    assert blob.stat().st_size > supply_chain._MAX_FILE_BYTES
    assert default_scanner.scan(staged).verdict is Verdict.CLEAN, "cap behaviour changed"

    with pytest.raises(ValueError, match="dangerous"):
        mk.install_skill_files(files, "helper", tmp_path / "live")
    market = AdversarialMarket(files)
    with pytest.raises(ValueError, match="dangerous"):
        mk.install_scanned(market, "adversarial", "helper", tmp_path / "live2")
    assert not (tmp_path / "live2" / "helper" / "scripts").exists()


def assert_manifest_rejected(case: dict[str, Any], tmp_path: Path) -> None:
    """A manifest that parses to nonsense must stop the write, and no SKILL.md may land."""
    files = payload(case)
    with pytest.raises(ValueError, match="SKILL.md"):
        mk.install_skill_files(files, "helper", tmp_path / "live")
    assert not (tmp_path / "live" / "helper" / "SKILL.md").exists()
    market = AdversarialMarket(files)
    with pytest.raises(ValueError, match="SKILL.md"):
        mk.install_scanned(market, "adversarial", "helper", tmp_path / "live2")
    assert not (tmp_path / "live2" / "helper" / "SKILL.md").exists()


# ``expect`` → rail. A corpus case whose expect is absent here is a fixture nobody
# asserts on, and TestCorpusIsComplete reds on it.
NEEDS_MONKEYPATCH = {"installed_equals_scanned", "midscan_payload_swap_refused"}
HANDLERS: dict[str, Callable[..., None]] = {
    "unsafe_path_refused": assert_unsafe_path_refused,
    "dangerous": assert_dangerous,
    "dangerous_every_tier": assert_dangerous_every_tier,
    "refused_even_forced": assert_refused_even_forced,
    "warning_blocks_unforced_install": assert_warning_blocks_unforced_install,
    "installed_equals_scanned": assert_installed_equals_scanned,
    "midscan_payload_swap_refused": assert_midscan_payload_swap_refused,
    "integrity_tamper_detected": assert_integrity_tamper_detected,
    "oversize_skipped_by_walk_refused_at_commit": assert_oversize_skipped_by_walk_refused_at_commit,
    "manifest_rejected": assert_manifest_rejected,
}


def expect_rail_red(rail: Callable[..., None], *args: Any) -> None:
    """Assert a corpus rail FAILS. A failing rail raises either ``AssertionError`` or
    pytest's ``Failed`` (from a ``pytest.raises`` block that did not raise) — the latter
    derives from ``BaseException``, not ``Exception``, so it must be named explicitly.
    Anything else propagates: a TypeError would mean the demo broke, not that the rail
    caught the weakness."""
    try:
        rail(*args)
    except BaseException as exc:  # noqa: BLE001 — narrowed immediately below
        if type(exc).__name__ in {"AssertionError", "Failed"}:
            return
        raise
    raise AssertionError(f"{rail.__name__} still passed against a weakened scanner")


def run_case(case: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    handler = HANDLERS[case["expect"]]
    if case["expect"] in NEEDS_MONKEYPATCH:
        handler(case, tmp_path, monkeypatch)
    else:
        handler(case, tmp_path)


# ── the five classes ────────────────────────────────────────────────────────────


class TestArchiveClass:
    """Zip-slip, absolute-path escape, mid-path traversal, case-collision."""

    @pytest.mark.parametrize("case", load_cases("archive"), ids=ids_of(load_cases("archive")))
    def test_case(self, case, tmp_path, monkeypatch):
        run_case(case, tmp_path, monkeypatch)


class TestIntegrityRaceClass:
    """scanned-bytes == installed-bytes under a concurrent swap, plus post-install tamper."""

    @pytest.mark.parametrize(
        "case", load_cases("integrity-race"), ids=ids_of(load_cases("integrity-race"))
    )
    def test_case(self, case, tmp_path, monkeypatch):
        run_case(case, tmp_path, monkeypatch)


class TestVerdictEvasionClass:
    """Split/obfuscated dangerous idioms, tier laundering, force against the floor."""

    @pytest.mark.parametrize(
        "case", load_cases("verdict-evasion"), ids=ids_of(load_cases("verdict-evasion"))
    )
    def test_case(self, case, tmp_path, monkeypatch):
        run_case(case, tmp_path, monkeypatch)


class TestInvisibleCharClass:
    """Bidi overrides and zero-width token splitting."""

    @pytest.mark.parametrize(
        "case", load_cases("invisible-char"), ids=ids_of(load_cases("invisible-char"))
    )
    def test_case(self, case, tmp_path, monkeypatch):
        run_case(case, tmp_path, monkeypatch)


class TestDegenerateManifestClass:
    """Oversized blobs and manifests that parse to nonsense."""

    @pytest.mark.parametrize(
        "case", load_cases("degenerate-manifest"), ids=ids_of(load_cases("degenerate-manifest"))
    )
    def test_case(self, case, tmp_path, monkeypatch):
        run_case(case, tmp_path, monkeypatch)


# ── the corpus's own floor ──────────────────────────────────────────────────────


class TestCorpusIsComplete:
    """The vacuity floor. A corpus that quietly stopped asserting anything — a class dir
    emptied, a fixture added but never wired, a payload that became executable — must
    fail here rather than look like a clean run."""

    def test_all_five_attack_classes_present_and_populated(self):
        assert sorted(p.name for p in CORPUS_ROOT.iterdir() if p.is_dir()) == sorted(ATTACK_CLASSES)
        for cls in ATTACK_CLASSES:
            assert load_cases(cls), cls

    def test_every_case_is_wired_to_an_assertion(self):
        for case_id, case in ALL_CASES.items():
            assert case["expect"] in HANDLERS, f"{case_id}: unhandled expect {case['expect']!r}"

    def test_case_metadata_is_coherent(self):
        for cls in ATTACK_CLASSES:
            for case in load_cases(cls):
                assert case["class"] == cls, case["id"]
                assert case["id"].startswith(f"{cls}/"), case["id"]
                assert case["summary"].strip(), case["id"]
                assert case.get("files") or case.get("variants"), case["id"]
        assert len(ALL_CASES) == sum(len(load_cases(c)) for c in ATTACK_CLASSES), "duplicate ids"

    def test_corpus_payloads_are_inert(self):
        """A malicious sample must never be runnable by the test run itself: every corpus
        file is JSON data, and none carries an executable bit."""
        for path in sorted(CORPUS_ROOT.rglob("*")):
            if path.is_file():
                assert path.suffix == ".json", f"non-JSON corpus artifact: {path}"
                assert not path.stat().st_mode & 0o111, f"executable corpus file: {path}"

    def test_methodology_doc_documents_every_class(self):
        doc = REPO_ROOT / "docs" / "security" / "scanner-testing.md"
        assert doc.is_file(), f"missing published methodology: {doc}"
        text = doc.read_text(encoding="utf-8")
        for cls in ATTACK_CLASSES:
            assert cls in text, f"{cls} undocumented in scanner-testing.md"
        assert "tests/security/corpus" in text
        assert "pytest" in text

    def test_nightly_job_runs_the_corpus(self):
        wf = REPO_ROOT / ".github" / "workflows" / "full.yml"
        assert wf.is_file(), f"missing workflow: {wf}"
        text = wf.read_text(encoding="utf-8")
        assert "security-corpus:" in text, "no security-corpus job in full.yml"
        assert "tests/security" in text, "the nightly job does not run the corpus"
        assert "schedule:" in text and "cron:" in text


class TestCorpusRedsOnAWeakenedScanner:
    """Plan V3 — prove the corpus is load-bearing.

    Each test weakens ONE control for the duration of the test (monkeypatch only, so the
    shipped scanner is untouched) and asserts the matching corpus rail now fails. If a
    control were already dead, its rail would pass under the weakening and this class
    would red — which is the point."""

    @staticmethod
    def _case(case_id: str) -> dict[str, Any]:
        return ALL_CASES[case_id]

    def test_dropping_destructive_root_reds_verdict_evasion(self, tmp_path, monkeypatch):
        case = self._case("verdict-evasion/destructive-root-variants")
        assert_dangerous(case, tmp_path / "intact")
        monkeypatch.setattr(
            supply_chain,
            "_DANGEROUS_SCRIPT",
            tuple(r for r in supply_chain._DANGEROUS_SCRIPT if r[0] != "destructive_root"),
        )
        expect_rail_red(assert_dangerous, case, tmp_path / "weakened")

    def test_emptying_invisible_charset_reds_invisible_char(self, tmp_path, monkeypatch):
        case = self._case("invisible-char/bidi-override-in-manifest")
        assert_dangerous_every_tier(case, tmp_path / "intact")
        monkeypatch.setattr(supply_chain, "_INVISIBLE_CHARS", set())
        expect_rail_red(assert_dangerous_every_tier, case, tmp_path / "weakened")

    def test_neutering_manifest_validation_reds_degenerate_manifest(self, tmp_path, monkeypatch):
        case = self._case("degenerate-manifest/missing-frontmatter")
        assert_manifest_rejected(case, tmp_path / "intact")
        monkeypatch.setattr(mk, "_validate_skill_md", lambda contents: [])
        expect_rail_red(assert_manifest_rejected, case, tmp_path / "weakened")

    def test_silently_dropping_unsafe_entries_reds_archive(self, tmp_path, monkeypatch):
        """The weakness here is the plausible one: a stager that skips the traversal entry
        instead of refusing the install. Nothing escapes, so the demo stays safe — but the
        install would proceed, and the archive rail must catch exactly that."""
        case = self._case("archive/zip-slip-parent-escape")
        assert_unsafe_path_refused(case, tmp_path / "intact")

        def lenient_stage(files, staged_skill):
            staged_skill = Path(staged_skill)
            staged_skill.mkdir(parents=True, exist_ok=True)
            for entry in files:
                rel = entry.get("path", "")
                if ".." in rel or rel.startswith("/"):
                    continue  # the weakness: skip, don't refuse
                out = staged_skill / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(mk._entry_bytes(entry))

        monkeypatch.setattr(mk, "_stage_files", lenient_stage)
        expect_rail_red(assert_unsafe_path_refused, case, tmp_path / "weakened")

    def test_committing_bytes_other_than_the_scanned_ones_reds_the_race(
        self, tmp_path, monkeypatch
    ):
        """The weakness: the commit writes something other than the bytes the scan read —
        the TOCTOU ``install_scanned`` is built to avoid. The mutation here is deliberately
        NOT malicious (a trailing comment), which is the point: byte equality catches a
        post-scan substitution even when the substituted bytes look clean."""
        case = self._case("integrity-race/refetch-swap")
        assert_installed_equals_scanned(case, tmp_path / "intact", monkeypatch)
        monkeypatch.undo()

        real_writer = mk.install_skill_files

        def substituting_writer(files, skill_name, target_base):
            swapped = [dict(entry) for entry in files]
            for entry in swapped:
                if entry.get("path", "").endswith("setup.sh"):
                    entry["contents"] = entry.get("contents", "") + "# substituted\n"
            return real_writer(swapped, skill_name, target_base)

        monkeypatch.setattr(mk, "install_skill_files", substituting_writer)
        expect_rail_red(assert_installed_equals_scanned, case, tmp_path / "weakened", monkeypatch)

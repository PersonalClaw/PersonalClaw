"""OU-14 — the admission rails for the default chat model PersonalClaw fetches on first run.

**The shape this module gates, and the one it used to.** The default chat model is NOT in the
wheel. Owner decision (2026-09-24): *"the intention was always to ship the wheel without the
weight and fetch it on first run"*. The weight is 138 MiB — over PyPI's 100 MiB per-file limit,
and a ~147 MiB wheel for every install is a cost most users would pay for a model they replace
on day one. So it is downloaded ONCE into ``$PERSONALCLAW_HOME``, at the user's explicit
go-ahead, and verified against a recorded digest.

That inverts what a release has to prove. An earlier revision of this module gated a wheel that
CARRIED the weight; it now gates a wheel that must not:

* :func:`gate_wheel` — the wheel carries **no** weight-shaped member and stays **small**. Both
  halves matter and for the same reason: nothing else in the release pipeline is looking, and a
  weight that crept back in would put the wheel over PyPI's limit where the failure surfaces as
  a rejected upload on a tag rather than as a red test on a PR.
* :func:`licence_decision` — an explicit allowlist of permitted licence identifiers,
  **default-DENY** for anything unlisted, and a refusal that NAMES the licence it refused.
* :func:`verify_download` — the fetched bytes are the signed-off bytes. This one changed
  CLASS, not just wording: bytes that arrive over the network at runtime are an untrusted
  input in a way a wheel member never was, so the digest check went from hygiene to the
  security control on the path.
* :func:`size_decision` — an artifact over the declared ceiling is refused, naming both
  numbers. The ceiling now bounds a DOWNLOAD rather than a wheel delta, which is what stops a
  moved upstream file from pulling gigabytes onto a user's disk.
* :func:`admit_transfer` — that same ceiling applied WHILE the bytes arrive (the announced
  ``Content-Length`` first, then the running count), because a ceiling checked only once the
  whole file has landed has already let the disk pay for it.

What is NOT here is the bundle CHOICE: which model, under which licence, from which pinned
revision, at which digest, is recorded in :data:`DECLARATION_RELPATH` — the bundled-chat app's
own ``bundled-model-signoff.txt`` — and that record, not this module, is what a reader
consults. As of OU-14 it names ``unsloth/SmolLM2-135M-Instruct-GGUF`` under Apache-2.0.

**Why default-DENY and no fuzzy licence matching.** The known-false cases this rail exists for
all *look* permissive: Gemma ships under Google's own Gemma Terms with use restrictions,
Llama-3.x under a community licence with an MAU threshold, LFM under "LFM Open License". A
matcher loose enough to accept the prose "Apache License 2.0" is loose enough to accept "Gemma
Terms (Apache-2.0-style)", so the comparison is against the SPDX identifier, exactly, after
nothing more than a strip-and-lowercase. Prose is refused with a message that says to write the
identifier — a refusal, never a silent skip, because a model quietly skipped for an unreadable
licence is a model nobody ever decided about.

**Why a zero measurement is refused.** A size gate that passes when nothing was measured
reports "nobody looked" as "within budget", which is the vacuity OU-14's own escalation named.
So a zero-byte download is refused, and so is an unset ceiling: an unset number is not
permission.

Everything in this module is pure stdlib and reads no configuration, so the release gate
(``scripts/verify_wheel.py``, through ``scripts/installed_bundled_model_probe.py``) runs the
INSTALLED copy of it against the wheel that installed it, and the bundled-chat app can call it
against a download with nothing else imported.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from personalclaw.local_models.layouts import DIRECT_FILE_EXTENSIONS

#: The ONLY licence identifiers a bundled weight may carry (the owner's sign-off: "genuinely
#: OSI-permissive — Apache-2.0 or MIT"). Compared exactly, lowercased. Widening this set is a
#: governance change, not a maintenance one: ``tests/test_bundled_model_gate.py`` pins the set
#: member-for-member so adding an entry reds until the pin is edited in the same reviewable
#: commit.
PERMITTED_LICENCES: frozenset[str] = frozenset({"apache-2.0", "mit"})

#: Where the owner's sign-off record lives, relative to the repository root — inside the
#: bundled-chat app that reads it, because that is the only location every install carries. It
#: was once under ``docs/`` with a symlink into the app, and the container image, which copies
#: only ``src/``, shipped the link without its target. A RELATIVE path on purpose: this module
#: ships inside the wheel, where no repository exists, so resolving a default absolute path here
#: would be a lie that only fails at a release gate. Refusals name it so a maintainer knows which
#: file to edit; the runtime reads the app's own copy.
DECLARATION_RELPATH = "src/personalclaw/apps/native/bundled-chat/bundled-model-signoff.txt"

#: Every key a complete sign-off record carries. All seven are required — see
#: :func:`parse_declaration` for why a partial record is refused rather than ignored.
#:
#: ``source_url`` is the seventh. The weight is neither in git nor in the wheel, so this is
#: where the bytes come from — pinned to an immutable upstream revision. Whatever performs the
#: fetch reads the URL and the digest from THIS record rather than carrying its own copy: one
#: fact, one place, because a pinned URL living in a downloader beside a digest living here is
#: two things that can disagree about which bytes were signed off, and the disagreement would
#: be invisible because each half looks internally consistent.
#:
#: ``artifact`` is the file's path RELATIVE TO ``$PERSONALCLAW_HOME`` — where the download
#: lands — not a wheel member. It moved when the weight left the wheel; a record still naming
#: a ``personalclaw/...`` member would describe a location nothing writes.
DECLARATION_KEYS: tuple[str, ...] = (
    "model_id",
    "licence",
    "licence_url",
    "artifact",
    "sha256",
    "size_bytes",
    "size_budget_bytes",
    "source_url",
)

#: Suffixes that mark a file as a model weight. Derived from the local-model layout prober's own
#: list rather than a second copy of it (``""`` dropped — every wheel member has a name, and an
#: extensionless member is not evidence of a weight).
WEIGHT_SUFFIXES: tuple[str, ...] = tuple(ext for ext in DIRECT_FILE_EXTENSIONS if ext)

#: A wheel member has to be at least this big to read as a weight rather than a fixture. The
#: smallest candidate in the feasibility study is 88 MB, so 1 MiB is two orders of magnitude of
#: slack; it exists only so the weight scan does not trip on a kilobyte-sized ``.bin`` fixture.
WEIGHT_FLOOR_BYTES = 1 * 1024 * 1024

#: The wheel's own ceiling. Measured: ``personalclaw-0.1.3-py3-none-any.whl`` is 9.2 MiB on
#: PyPI, so 32 MiB is generous room for the SPA and the bundled skills/templates to grow while
#: still redding LOUDLY on the one regression this exists to catch — a model weight creeping
#: back into ``package-data``. Without it, that regression's first symptom would be PyPI
#: rejecting the upload of an already-tagged release, which is the worst possible place to
#: learn it. Raise this only alongside a measurement of what actually grew.
MAX_WHEEL_BYTES = 32 * 1024 * 1024

#: Gate states.
STATE_ADMITTED = "admitted"
STATE_REFUSED = "refused"

#: Named download failures. Each is a DIFFERENT thing for the user to do about it, which is the
#: whole reason they are separate values rather than one "download failed": no network means
#: retry when you have one, a bad status means the pinned upstream moved, a short transfer
#: means retry now, and a digest mismatch means the bytes are not the signed-off bytes and
#: retrying is not the answer.
DOWNLOAD_OK = "ok"
DOWNLOAD_UNREACHABLE = "unreachable"
DOWNLOAD_BAD_STATUS = "bad-status"
DOWNLOAD_TRUNCATED = "truncated"
DOWNLOAD_DIGEST_MISMATCH = "digest-mismatch"
DOWNLOAD_OVER_BUDGET = "over-budget"
# There is deliberately no `cancelled` outcome. A cancel is not a verdict about the bytes — it is
# a job-lifecycle event, and it already has one honest representation: the download manager's
# own `cancelled` job state (`GET /api/models/downloads`), reached because the fetch lets
# `CancelledError` propagate. A second spelling here would be a second authority for the same
# fact, and it was emitted by nothing when it existed.


class BundleDeclarationError(ValueError):
    """The sign-off record exists but cannot be read as a complete declaration.

    Raised rather than returning ``None`` because "half a sign-off" must not be
    indistinguishable from "no sign-off": the second is today's honest state, the first is a
    weight shipping with no recorded terms.
    """


@dataclass(frozen=True)
class LicenceDecision:
    """Whether a candidate bundled model's licence is permitted, and why."""

    raw: str
    """Exactly what the record declared, for the refusal message."""

    identifier: str
    """The normalised comparison key (stripped, lowercased)."""

    permitted: bool

    reason: str
    """One sentence. On a refusal it NAMES the licence and says what would clear it."""


@dataclass(frozen=True)
class SizeDecision:
    """Whether a measured artifact fits the declared budget, and by how much."""

    measured_bytes: int
    budget_bytes: int
    within_budget: bool
    reason: str
    """One sentence naming BOTH numbers, on the pass as well as the refusal."""


@dataclass(frozen=True)
class BundleDeclaration:
    """A complete owner sign-off: the model, its licence, the shipped bytes, the ceiling."""

    model_id: str
    licence: str
    licence_url: str
    artifact: str
    sha256: str
    size_bytes: int
    size_budget_bytes: int
    source_url: str


@dataclass(frozen=True)
class WheelWeight:
    """A weight-shaped member found inside a wheel. Its existence is the failure."""

    member: str
    size_bytes: int


@dataclass(frozen=True)
class BundleGateResult:
    """The release gate's answer: admitted or refused, every refusal named."""

    state: str
    refusals: tuple[str, ...]
    summary: str
    """The line a release log prints. Never a bare ``OK`` — it always says what was measured."""

    @property
    def ok(self) -> bool:
        return self.state != STATE_REFUSED


@dataclass(frozen=True)
class DownloadResult:
    """What a fetch of the signed-off weight did, in terms a user can act on.

    ``outcome`` is one of the ``DOWNLOAD_*`` constants; ``detail`` is the sentence a surface
    shows. There is no ``bool`` here on purpose: "it failed" is not actionable and four of the
    outcomes have four different answers.
    """

    outcome: str
    detail: str
    bytes_received: int = 0
    sha256: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == DOWNLOAD_OK


# ── the licence-allowlist rail ────────────────────────────────────────────────────────────────


def normalise_licence(raw: str) -> str:
    """The comparison key for a declared licence: stripped and lowercased, nothing more.

    Deliberately not a normaliser. No alias table, no punctuation folding, no "License"
    suffix stripping — see the module docstring: the near-misses this rail exists to refuse are
    all strings that read as permissive.
    """
    return (raw or "").strip().lower()


def licence_decision(raw: str) -> LicenceDecision:
    """Decide whether *raw* is a permitted licence for a redistributed weight.

    Default-DENY: anything not in :data:`PERMITTED_LICENCES` is refused, and the refusal names
    the licence. There is no third "unknown, skip it" outcome, because a skipped model is a
    model nobody decided about.
    """
    identifier = normalise_licence(raw)
    permitted = sorted(PERMITTED_LICENCES)
    if not identifier:
        return LicenceDecision(
            raw=raw,
            identifier="",
            permitted=False,
            reason=(
                "no licence is declared for the bundled model — redistribution needs one; "
                f"declare the SPDX identifier, one of {permitted}"
            ),
        )
    if identifier in PERMITTED_LICENCES:
        return LicenceDecision(
            raw=raw,
            identifier=identifier,
            permitted=True,
            reason=f"licence {identifier!r} is on the permitted allowlist {permitted}",
        )
    return LicenceDecision(
        raw=raw,
        identifier=identifier,
        permitted=False,
        reason=(
            f"licence {raw.strip()!r} is NOT on the permitted allowlist {permitted} — refused. "
            "Only a genuinely OSI-permissive licence may be redistributed in the wheel "
            "(an owner ruling); a custom, community, non-commercial, research-only or "
            "'open'-in-name-only licence is not one, whatever the model card calls it. If this "
            "IS one of the permitted licences, declare its SPDX identifier exactly — this rail "
            "does not match prose, because a matcher loose enough to accept prose accepts a "
            "restricted licence dressed as a permissive one."
        ),
    )


# ── the size-budget gate ──────────────────────────────────────────────────────────────────────


def _mib(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MiB"


def size_decision(measured_bytes: int, budget_bytes: int) -> SizeDecision:
    """Decide whether *measured_bytes* fits *budget_bytes*, naming both numbers either way.

    Three refusals, not one:

    * **over budget** — the case the gate is named for.
    * **a zero measurement** — a gate that passes when nothing shipped is the vacuity OU-14's
      escalation found in three of its four clauses.
    * **an undeclared budget** — a ceiling of zero cannot admit anything. The owner sets the
      number; an unset number is not permission.
    """
    if budget_bytes <= 0:
        return SizeDecision(
            measured_bytes=measured_bytes,
            budget_bytes=budget_bytes,
            within_budget=False,
            reason=(
                f"no size budget is declared (size_budget_bytes={budget_bytes}), so the "
                f"measured {measured_bytes} bytes ({_mib(measured_bytes)}) cannot be admitted: "
                "an unset ceiling is not permission. Declare the documented ceiling in "
                f"{DECLARATION_RELPATH}."
            ),
        )
    if measured_bytes <= 0:
        return SizeDecision(
            measured_bytes=measured_bytes,
            budget_bytes=budget_bytes,
            within_budget=False,
            reason=(
                f"the bundled artifact measured {measured_bytes} bytes against a budget of "
                f"{budget_bytes} ({_mib(budget_bytes)}) — nothing was shipped. A size gate that "
                "passes on a zero measurement reports 'nobody measured' as 'within budget', "
                "which is the one thing this gate exists not to do."
            ),
        )
    if measured_bytes > budget_bytes:
        over = measured_bytes - budget_bytes
        return SizeDecision(
            measured_bytes=measured_bytes,
            budget_bytes=budget_bytes,
            within_budget=False,
            reason=(
                f"the bundled artifact measured {measured_bytes} bytes ({_mib(measured_bytes)}) "
                f"and the declared budget is {budget_bytes} bytes ({_mib(budget_bytes)}) — over "
                f"by {over} bytes ({_mib(over)}). Ship a smaller quant, or raise the documented "
                f"ceiling in {DECLARATION_RELPATH} as a deliberate, reviewable change."
            ),
        )
    headroom = budget_bytes - measured_bytes
    return SizeDecision(
        measured_bytes=measured_bytes,
        budget_bytes=budget_bytes,
        within_budget=True,
        reason=(
            f"the bundled artifact measured {measured_bytes} bytes ({_mib(measured_bytes)}) "
            f"against a declared budget of {budget_bytes} bytes ({_mib(budget_bytes)}) — "
            f"{headroom} bytes ({_mib(headroom)}) of headroom"
        ),
    )


# ── the sign-off record ───────────────────────────────────────────────────────────────────────


def parse_declaration(text: str) -> BundleDeclaration | None:
    """Read a sign-off record. ``None`` means *nothing is declared* — today's honest state.

    A record with SOME keys raises :class:`BundleDeclarationError` instead, naming what is
    missing. The distinction is the whole safety property: "no sign-off" is a state the release
    gate reports and tolerates, "half a sign-off" is a weight shipping with incomplete terms.
    """
    values: dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        # A comment is a line starting with `#`, or a ` #` after a value. Not any `#`: a licence
        # URL may legitimately carry a fragment, and truncating one silently would leave the
        # record pointing at the wrong section of a licence page.
        line = raw_line.strip()
        if line.startswith("#"):
            continue
        line = line.split(" #", 1)[0].strip()
        if not line:
            continue
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if not sep:
            raise BundleDeclarationError(
                f"{DECLARATION_RELPATH}:{lineno}: {raw_line.strip()!r} is not a `key: value` "
                f"line. Keys: {list(DECLARATION_KEYS)}."
            )
        if key not in DECLARATION_KEYS:
            raise BundleDeclarationError(
                f"{DECLARATION_RELPATH}:{lineno}: unknown key {key!r}. A typo'd key would "
                f"otherwise read as an absent one. Keys: {list(DECLARATION_KEYS)}."
            )
        if key in values:
            raise BundleDeclarationError(
                f"{DECLARATION_RELPATH}:{lineno}: {key!r} is declared twice — one record, one "
                "value, so there is no question which one the gate enforced."
            )
        value = value.strip()
        if value:
            values[key] = value

    if not values:
        return None

    missing = [key for key in DECLARATION_KEYS if key not in values]
    if missing:
        raise BundleDeclarationError(
            f"{DECLARATION_RELPATH} declares {sorted(values)} but not {missing}. A partial "
            "sign-off is refused, not ignored: read as 'no bundle' it would let a weight ship "
            "with no recorded licence, digest or ceiling."
        )

    integers: dict[str, int] = {}
    for key in ("size_bytes", "size_budget_bytes"):
        raw = values[key].replace("_", "")
        if not raw.isdigit():
            raise BundleDeclarationError(
                f"{DECLARATION_RELPATH}: {key} must be a plain integer number of BYTES, got "
                f"{values[key]!r}. Bytes and not '138 MiB' because a unit-bearing number is "
                "where MB-vs-MiB ambiguity enters the ones a gate and a progress bar use."
            )
        integers[key] = int(raw)
    if integers["size_bytes"] > integers["size_budget_bytes"]:
        raise BundleDeclarationError(
            f"{DECLARATION_RELPATH}: size_bytes ({integers['size_bytes']}) is larger than "
            f"size_budget_bytes ({integers['size_budget_bytes']}). The ceiling has to admit the "
            "thing it is the ceiling for, or every download of the signed-off model is refused "
            "by arithmetic before it starts."
        )
    digest = values["sha256"].strip().lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise BundleDeclarationError(
            f"{DECLARATION_RELPATH}: sha256 must be 64 hex characters, got {values['sha256']!r}. "
            "This field is what chains the sign-off to the bytes actually shipped."
        )
    return BundleDeclaration(
        model_id=values["model_id"],
        licence=values["licence"],
        licence_url=values["licence_url"],
        artifact=values["artifact"],
        sha256=digest,
        size_bytes=integers["size_bytes"],
        size_budget_bytes=integers["size_budget_bytes"],
        source_url=values["source_url"],
    )


def load_declaration(path: Path) -> BundleDeclaration | None:
    """:func:`parse_declaration` over *path*. A missing file reads as nothing declared."""
    if not path.exists():
        return None
    return parse_declaration(path.read_text(encoding="utf-8"))


def repo_declaration(repo_root: Path) -> BundleDeclaration | None:
    """The sign-off record of the repository rooted at *repo_root*."""
    return load_declaration(repo_root / DECLARATION_RELPATH)


# ── the release gate: the wheel must carry NO weight, and must stay small ──────────────────────


def weight_shaped_members(names: Iterable[str]) -> list[str]:
    """Members of a wheel namelist whose NAME reads as a model weight.

    Name-only, so it can run over a namelist without reading bytes.
    """
    return sorted(name for name in names if name.lower().endswith(WEIGHT_SUFFIXES))


def wheel_weights(wheel: Path) -> list[WheelWeight]:
    """Every weight-shaped member in *wheel* above :data:`WEIGHT_FLOOR_BYTES`.

    The floor is what keeps a kilobyte-sized ``.bin`` test fixture — of which the wheel ships
    several under ``tests_fixtures/`` — from reading as a shipped model.
    """
    with zipfile.ZipFile(wheel) as zf:
        return [
            WheelWeight(member=info.filename, size_bytes=info.file_size)
            for info in zf.infolist()
            if info.filename.lower().endswith(WEIGHT_SUFFIXES)
            and info.file_size >= WEIGHT_FLOOR_BYTES
        ]


def gate_wheel(wheel: Path, *, max_bytes: int = MAX_WHEEL_BYTES) -> BundleGateResult:
    """The release decision: this wheel carries no model weight and is not bloated.

    Two assertions, one gate, because they are two symptoms of one regression. The default
    chat model is fetched at runtime (owner decision — see the module docstring), so a
    weight-shaped member in the wheel means ``package-data`` grew a glob it should not have;
    and the first thing that goes wrong when it does is the wheel crossing PyPI's 100 MiB
    per-file limit, where the failure surfaces as a rejected upload of an already-tagged
    release. Checking the size as well catches the same regression arriving as a directory of
    shards, none of them individually weight-shaped.

    The summary always states both measurements, so a release log cannot print a bare ``OK``
    for a gate that measured nothing.
    """
    total = wheel.stat().st_size
    weights = wheel_weights(wheel)
    refusals: list[str] = []
    if weights:
        listed = ", ".join(f"{w.member} ({_mib(w.size_bytes)})" for w in weights)
        refusals.append(
            f"this wheel carries {len(weights)} model-weight-shaped member(s): {listed}. The "
            "default chat model is fetched at first use into $PERSONALCLAW_HOME, NOT shipped — "
            "a weight here is a `package-data` glob that should not exist (a `*.gguf` /"
            " `*.safetensors` / `*.onnx` entry in pyproject.toml), and it puts the wheel over "
            "PyPI's 100 MiB per-file limit where the failure is a rejected upload on a tag."
        )
    if total > max_bytes:
        refusals.append(
            f"this wheel is {total} bytes ({_mib(total)}), over the {max_bytes}-byte "
            f"({_mib(max_bytes)}) ceiling. Find what grew before raising the ceiling: the "
            "regression this number exists to catch is a model weight arriving as package "
            "data, which PyPI would reject at upload time rather than here."
        )
    if refusals:
        return BundleGateResult(
            state=STATE_REFUSED,
            refusals=tuple(refusals),
            summary=(
                f"REFUSED: wheel {_mib(total)}, {len(weights)} weight-shaped member(s), "
                f"{len(refusals)} refusal(s)"
            ),
        )
    return BundleGateResult(
        state=STATE_ADMITTED,
        refusals=(),
        summary=(
            f"ADMITTED: wheel is {total} bytes ({_mib(total)}) against a {_mib(max_bytes)} "
            "ceiling and carries no model weight — the default chat model is fetched at first "
            "use, so shipping one here would be the defect"
        ),
    )


# ── verifying a downloaded weight ─────────────────────────────────────────────────────────────


def sha256_file(path: Path, *, chunk: int = 1024 * 1024) -> str:
    """The hex sha256 of *path*, read in chunks so a 138 MiB file is never held twice."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def admit_transfer(
    declaration: BundleDeclaration, byte_count: int, *, announced: bool
) -> DownloadResult | None:
    """The size ceiling applied to a transfer that is still RUNNING — ``None`` while it fits.

    :func:`verify_download` judges a finished file, which is too late for the one thing the
    ceiling exists to prevent: a moved upstream file pulling gigabytes onto a user's disk before
    anything looks at it. So a fetch asks here twice — once with the ``Content-Length`` the
    source ANNOUNCED, before a byte is written, and again with the running count after every
    chunk — and stops the moment the answer is no. Same ceiling, same arithmetic
    (:func:`size_decision`), so the in-flight refusal and the post-transfer one cannot disagree
    about where the line is.

    ``announced`` picks the sentence, because the two refusals leave different things behind:
    an announced size is refused with nothing downloaded, a running count after the partial
    file was already removed.
    """
    budget = declaration.size_budget_bytes
    if budget <= 0:
        # An unset ceiling is not permission — the same refusal, in the same words, as a
        # finished file gets.
        return DownloadResult(
            outcome=DOWNLOAD_OVER_BUDGET,
            detail=size_decision(byte_count, budget).reason,
            bytes_received=byte_count,
        )
    if byte_count <= budget:
        return None
    signed = (
        f"{declaration.model_id} is signed off at {declaration.size_bytes} bytes "
        f"({_mib(declaration.size_bytes)})"
    )
    if announced:
        detail = (
            f"the model source says this file is {byte_count} bytes ({_mib(byte_count)}), over "
            f"the {budget}-byte ({_mib(budget)}) ceiling this download is allowed, so nothing "
            f"was downloaded. {signed}, so the source is no longer serving the signed-off file — "
            "retrying the same URL is not the fix."
        )
    else:
        detail = (
            f"the model source had sent {byte_count} bytes ({_mib(byte_count)}), past the "
            f"{budget}-byte ({_mib(budget)}) ceiling this download is allowed, so the transfer "
            f"was stopped and the partial file removed. {signed}, so the source is no longer "
            "serving the signed-off file — retrying the same URL is not the fix."
        )
    return DownloadResult(outcome=DOWNLOAD_OVER_BUDGET, detail=detail, bytes_received=byte_count)


def verify_download(path: Path, declaration: BundleDeclaration) -> DownloadResult:
    """Are the bytes at *path* the bytes *declaration* signed off?

    This is the security control on the fetch path, not a checksum for corruption's sake. A
    wheel member was trusted because the wheel was; bytes that arrived over the network are
    not, and the digest is the only thing standing between "the model the owner reviewed" and
    "whatever answered that URL". So the refusals are specific, and each names a DIFFERENT
    thing to do about it:

    * nothing there, or empty, or short of the declared ``size_bytes`` — ``truncated``; the
      transfer did not finish, and retrying is exactly right. Checked by arithmetic before the
      digest, so a half-downloaded 138 MiB file is diagnosed without hashing 70 MiB of it.
    * over the declared ceiling — ``over-budget``; the upstream file is no longer the file that
      was signed off, so this stops before a user's disk pays for the difference.
    * a digest mismatch — ``digest-mismatch``. Its own outcome because "try again" is the wrong
      advice: the source pins an immutable revision, so a mismatch means the pin is wrong or
      something between here and there changed the bytes.
    """
    if not path.is_file():
        return DownloadResult(
            outcome=DOWNLOAD_TRUNCATED,
            detail=f"no file at {path} — nothing was downloaded, so there is nothing to verify",
        )
    size = path.stat().st_size
    if size == 0:
        return DownloadResult(
            outcome=DOWNLOAD_TRUNCATED,
            detail=f"{path} is empty — the transfer produced no bytes",
            bytes_received=0,
        )
    if size < declaration.size_bytes:
        return DownloadResult(
            outcome=DOWNLOAD_TRUNCATED,
            detail=(
                f"the transfer stopped at {size} bytes ({_mib(size)}) of the "
                f"{declaration.size_bytes} ({_mib(declaration.size_bytes)}) "
                f"{declaration.model_id} is signed off as. The partial file was discarded; "
                "start the download again."
            ),
            bytes_received=size,
        )
    budget = size_decision(size, declaration.size_budget_bytes)
    if not budget.within_budget:
        return DownloadResult(
            outcome=DOWNLOAD_OVER_BUDGET, detail=budget.reason, bytes_received=size
        )
    actual = sha256_file(path)
    if actual != declaration.sha256:
        return DownloadResult(
            outcome=DOWNLOAD_DIGEST_MISMATCH,
            detail=(
                f"the downloaded bytes hash to {actual} but {declaration.model_id} is signed "
                f"off as {declaration.sha256}. These are NOT the reviewed bytes, so they were "
                "discarded rather than used. The source URL pins an immutable revision, so "
                "this means either that pin is wrong or something rewrote the transfer — "
                "retrying the same URL is not the fix."
            ),
            bytes_received=size,
            sha256=actual,
        )
    return DownloadResult(
        outcome=DOWNLOAD_OK,
        detail=(
            f"{declaration.model_id} verified: {size} bytes ({_mib(size)}), sha256 "
            f"{actual[:12]}… matches the sign-off record"
        ),
        bytes_received=size,
        sha256=actual,
    )

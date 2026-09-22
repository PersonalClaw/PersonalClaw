"""OU-14 — the two admission rails for a bundled default chat model.

**What ships here and what deliberately does not.** PersonalClaw's zero-config promise is a
working first chat turn with no provider bound, no key and no Ollama — which means
REDISTRIBUTING a model weight in the wheel and the image. Redistribution is a licence act, and
the size of the thing redistributed is a packaging act, so both need a control rather than a
paragraph. This module is those two controls:

* :func:`licence_decision` — an explicit allowlist of permitted licence identifiers,
  **default-DENY** for anything unlisted, and a refusal that NAMES the licence it refused.
* :func:`size_decision` — an artifact over the declared budget is refused, and the refusal
  names the measured size AND the budget.

What is NOT here, by design, is the bundle CHOICE: which model ships and the sign-off on its
licence are the owner's (Chairman C9). ``docs/architecture/bundled-model-signoff.txt`` is the
form that decision gets recorded in, and it is empty today. So :func:`gate_wheel` has a third
state besides admitted/refused — ``no-bundle`` — and the whole point of naming it is that a
release log must not print a bare ``OK`` for a gate that measured nothing.

**Why default-DENY and no fuzzy licence matching.** The known-false cases this rail exists for
all *look* permissive: Gemma ships under Google's own Gemma Terms with use restrictions,
Llama-3.x under a community licence with an MAU threshold, LFM under "LFM Open License". A
matcher loose enough to accept the prose "Apache License 2.0" is loose enough to accept "Gemma
Terms (Apache-2.0-style)", so the comparison is against the SPDX identifier, exactly, after
nothing more than a strip-and-lowercase. Prose is refused with a message that says to write the
identifier — a refusal, never a silent skip, because a model quietly skipped for an unreadable
licence is a model nobody ever decided about.

**Why the size gate refuses a zero measurement.** A gate that passes when the wheel carries no
weight is the vacuity the OU-14 escalation named: three of its four clauses could go green with
no model bundled and nothing to run. So a declared bundle whose artifact measures zero bytes is
refused, a wheel carrying a weight that nothing here declares is refused, and a declared
artifact the wheel does not carry is refused. The three refusals are what chain the licence
record to the shipped bytes.

Everything in this module is pure stdlib and reads no configuration, so the release gate
(``scripts/verify_wheel.py``) can call it against a built wheel on a bare runner.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from personalclaw.local_models.layouts import DIRECT_FILE_EXTENSIONS

#: The ONLY licence identifiers a bundled weight may carry (Chairman C9: "genuinely
#: OSI-permissive — Apache-2.0 or MIT"). Compared exactly, lowercased. Widening this set is a
#: governance change, not a maintenance one: ``tests/test_bundled_model_gate.py`` pins the set
#: member-for-member so adding an entry reds until the pin is edited in the same reviewable
#: commit.
PERMITTED_LICENCES: frozenset[str] = frozenset({"apache-2.0", "mit"})

#: Where the owner's sign-off record lives, relative to the repository root. A RELATIVE path on
#: purpose: this module ships inside the wheel, where no repository exists, so resolving a
#: default absolute path here would be a lie that only fails at a release gate.
DECLARATION_RELPATH = "docs/architecture/bundled-model-signoff.txt"

#: Every key a complete sign-off record carries. All six are required — see
#: :func:`parse_declaration` for why a partial record is refused rather than ignored.
DECLARATION_KEYS: tuple[str, ...] = (
    "model_id",
    "licence",
    "licence_url",
    "artifact",
    "sha256",
    "size_budget_bytes",
)

#: Suffixes that mark a file as a model weight. Derived from the local-model layout prober's own
#: list rather than a second copy of it (``""`` dropped — every wheel member has a name, and an
#: extensionless member is not evidence of a weight).
WEIGHT_SUFFIXES: tuple[str, ...] = tuple(ext for ext in DIRECT_FILE_EXTENSIONS if ext)

#: A wheel member has to be at least this big to read as a weight rather than a fixture. The
#: smallest bundleable candidate in the feasibility study is 88 MB, so 1 MiB is two orders of
#: magnitude of slack; it exists only so the undeclared-weight scan does not trip on a
#: kilobyte-sized ``.bin`` test fixture.
WEIGHT_FLOOR_BYTES = 1 * 1024 * 1024

#: Gate states. ``no-bundle`` is neither a pass nor a failure of the *bundle*: it is the
#: accurate report that nothing is signed off, and it exists so a release log cannot say ``OK``
#: about a measurement that never happened.
STATE_NO_BUNDLE = "no-bundle"
STATE_ADMITTED = "admitted"
STATE_REFUSED = "refused"


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
    size_budget_bytes: int


@dataclass(frozen=True)
class MeasuredArtifact:
    """What a built wheel actually carries, as measured from the wheel."""

    member: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class BundleGateResult:
    """The release gate's answer: one of three states, every refusal named."""

    state: str
    refusals: tuple[str, ...]
    summary: str
    """The line a release log prints. Never a bare ``OK`` — it always says what was measured."""

    @property
    def ok(self) -> bool:
        """True when the gate does not block the release.

        ``no-bundle`` is OK because nothing is signed off yet; :attr:`summary` is what stops
        that from reading as "the bundle was verified".
        """
        return self.state != STATE_REFUSED


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
            "(Chairman C9); a custom, community, non-commercial, research-only or "
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

    budget_raw = values["size_budget_bytes"].replace("_", "")
    if not budget_raw.isdigit():
        raise BundleDeclarationError(
            f"{DECLARATION_RELPATH}: size_budget_bytes must be a plain integer number of "
            f"BYTES, got {values['size_budget_bytes']!r}. Bytes and not '488 MiB' because a "
            "unit-bearing ceiling is where MB-vs-MiB ambiguity enters the one number the gate "
            "enforces."
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
        size_budget_bytes=int(budget_raw),
    )


def load_declaration(path: Path) -> BundleDeclaration | None:
    """:func:`parse_declaration` over *path*. A missing file reads as nothing declared."""
    if not path.exists():
        return None
    return parse_declaration(path.read_text(encoding="utf-8"))


def repo_declaration(repo_root: Path) -> BundleDeclaration | None:
    """The sign-off record of the repository rooted at *repo_root*."""
    return load_declaration(repo_root / DECLARATION_RELPATH)


# ── measuring a built wheel ───────────────────────────────────────────────────────────────────


def weight_shaped_members(names: Iterable[str]) -> list[str]:
    """Members of a wheel namelist whose NAME reads as a model weight.

    Name-only, so it can run over a namelist without reading bytes. Used for the
    undeclared-weight scan: a wheel that grew a weight nothing signed off must red, or the
    licence record is a thing you can ship around.
    """
    return sorted(name for name in names if name.lower().endswith(WEIGHT_SUFFIXES))


def measure_wheel(wheel: Path, declaration: BundleDeclaration | None) -> MeasuredArtifact | None:
    """Measure the bundled weight inside *wheel*, from the wheel's own bytes.

    With a declaration, the declared member is looked up by name (exact, then by suffix so a
    record may name the path without the ``personalclaw/`` prefix). With none, the wheel is
    scanned for any weight-shaped member above :data:`WEIGHT_FLOOR_BYTES` — which is what makes
    "ship a weight and don't mention it" a refusal rather than a pass.

    Returns ``None`` when there is nothing to measure.
    """
    with zipfile.ZipFile(wheel) as zf:
        infos = zf.infolist()
        if declaration is not None:
            wanted = declaration.artifact.strip().lstrip("/")
            match = next((i for i in infos if i.filename == wanted), None)
            if match is None:
                match = next((i for i in infos if i.filename.endswith("/" + wanted)), None)
            if match is None:
                return None
        else:
            candidates = [
                i
                for i in infos
                if i.filename.lower().endswith(WEIGHT_SUFFIXES)
                and i.file_size >= WEIGHT_FLOOR_BYTES
            ]
            if not candidates:
                return None
            match = max(candidates, key=lambda i: i.file_size)
        digest = hashlib.sha256()
        with zf.open(match) as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    return MeasuredArtifact(
        member=match.filename, size_bytes=match.file_size, sha256=digest.hexdigest()
    )


# ── the combined gate ─────────────────────────────────────────────────────────────────────────


def admit_bundle(
    declaration: BundleDeclaration | None, measured: MeasuredArtifact | None
) -> BundleGateResult:
    """The release decision, with every refusal named.

    The four cases are deliberately all distinct, because three of them are ways an isolated
    clause could have gone green with nothing bundled:

    * nothing declared, nothing shipped → ``no-bundle``, and the summary SAYS the gate measured
      nothing rather than printing an OK.
    * nothing declared, a weight shipped → refused. The sign-off is not optional.
    * declared, nothing shipped → refused. A record that describes a model the wheel does not
      carry is the cheatable form of the licence clause.
    * both → the licence allowlist, the digest chain and the size budget, all three.
    """
    if declaration is None and measured is None:
        return BundleGateResult(
            state=STATE_NO_BUNDLE,
            refusals=(),
            summary=(
                "NO BUNDLE DECLARED — no model is signed off in "
                f"{DECLARATION_RELPATH} and the wheel carries no weight-shaped member above "
                f"{_mib(WEIGHT_FLOOR_BYTES)}. This gate measured NO artifact, so it is not "
                "evidence about one: the zero-config first chat turn is UNMET until a model is "
                "chosen and its licence signed off (owner-gated, Chairman C9)."
            ),
        )
    if declaration is None and measured is not None:
        return BundleGateResult(
            state=STATE_REFUSED,
            refusals=(
                f"the wheel carries a weight-shaped member {measured.member!r} of "
                f"{measured.size_bytes} bytes ({_mib(measured.size_bytes)}) that nothing "
                f"declares. Record the model id, its licence, the licence link, this member's "
                f"sha256 and the documented size ceiling in {DECLARATION_RELPATH} — "
                "redistributing a weight with no recorded terms is the act this rail exists to "
                "stop.",
            ),
            summary=(
                f"REFUSED: an undeclared weight ({measured.member}, "
                f"{_mib(measured.size_bytes)}) is in the wheel"
            ),
        )
    assert declaration is not None  # narrowed by the two branches above
    if measured is None:
        return BundleGateResult(
            state=STATE_REFUSED,
            refusals=(
                f"the sign-off record declares model {declaration.model_id!r} shipping as "
                f"{declaration.artifact!r}, but the built wheel carries no such member. Either "
                "the build did not package the weight (check the package-data glob) or the "
                "record describes a model that is not there — which is exactly the shape of a "
                "licence clause that passes with nothing bundled.",
            ),
            summary=(
                f"REFUSED: {declaration.model_id} is signed off but "
                f"{declaration.artifact} is not in the wheel"
            ),
        )

    refusals: list[str] = []
    licence = licence_decision(declaration.licence)
    if not licence.permitted:
        refusals.append(licence.reason)
    if measured.sha256 != declaration.sha256:
        refusals.append(
            f"the shipped weight {measured.member!r} hashes to {measured.sha256} but the "
            f"sign-off record declares {declaration.sha256}. The digest is what ties the "
            "recorded licence to the bytes users receive; a mismatch means the record describes "
            "a different artifact."
        )
    size = size_decision(measured.size_bytes, declaration.size_budget_bytes)
    if not size.within_budget:
        refusals.append(size.reason)

    if refusals:
        return BundleGateResult(
            state=STATE_REFUSED,
            refusals=tuple(refusals),
            summary=(
                f"REFUSED: {len(refusals)} bundled-model refusal(s) for "
                f"{declaration.model_id} ({measured.member}, {_mib(measured.size_bytes)})"
            ),
        )
    return BundleGateResult(
        state=STATE_ADMITTED,
        refusals=(),
        summary=(
            f"ADMITTED: {declaration.model_id} under {licence.identifier}, "
            f"{measured.member} sha256 {measured.sha256[:12]}…, {size.reason}"
        ),
    )


def gate_wheel(wheel: Path, declaration: BundleDeclaration | None) -> BundleGateResult:
    """:func:`measure_wheel` then :func:`admit_bundle` — the one call a release gate makes."""
    return admit_bundle(declaration, measure_wheel(wheel, declaration))

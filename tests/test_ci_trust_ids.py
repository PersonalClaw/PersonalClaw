"""``tools/ci/trust.py`` reads an object id or a digest as the whole string, never a prefix of it.

A pattern anchored with ``$`` and applied with ``re.match`` also matches before a trailing
newline, so ``"<40 hex>\\n"`` passed as a full commit id. Every id the module reads (the commit
``require_on_main`` takes, a receipt's ``controller_revision``, ``head_sha`` and ``tree_oid``, each
of its ``external_revisions``) and every evidence digest must be exactly the form, end to end. The
module takes a SHA-1 id (40 hex) and a SHA-256 id (64 hex), in lower case.

Each row is refused before any git call, so no repository is needed: the positive controls show a
well-formed id gets past the format check (and is refused later, for the missing repository).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tools.ci import trust

pytestmark = pytest.mark.security

SHA1 = "a" * 40
SHA256 = "b" * 64
DIGEST = "sha256:" + "c" * 64

#: Every malformed id, and why it is malformed.
BAD_IDS = [
    pytest.param(SHA1 + "\n", id="40 hex and a trailing newline"),
    pytest.param(SHA1 + "\r\n", id="40 hex and a trailing CRLF"),
    pytest.param("\n" + SHA1, id="40 hex after a leading newline"),
    pytest.param(SHA1[:-1], id="39 hex"),
    pytest.param(SHA1 + "a", id="41 hex"),
    pytest.param(SHA1.upper(), id="40 upper-case hex"),
    pytest.param(SHA1[:-1] + "A", id="40 hex ending in an upper-case letter"),
    pytest.param(SHA256 + "\n", id="64 hex and a trailing newline"),
    pytest.param(SHA256[:-1], id="63 hex"),
    pytest.param(SHA256 + "b", id="65 hex"),
    pytest.param(SHA256.upper(), id="64 upper-case hex"),
    pytest.param(SHA1 + " ", id="40 hex and a trailing space"),
]
assert len(BAD_IDS) == 12

BAD_DIGESTS = [
    pytest.param(DIGEST + "\n", id="a digest and a trailing newline"),
    pytest.param(DIGEST[:-1], id="a digest of 63 hex"),
    pytest.param(DIGEST + "c", id="a digest of 65 hex"),
    pytest.param("sha256:" + ("c" * 64).upper(), id="an upper-case digest"),
    pytest.param("SHA256:" + "c" * 64, id="an upper-case algorithm"),
]
assert len(BAD_DIGESTS) == 5


def _receipt(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "controller_revision": SHA1,
        "head_sha": SHA1,
        "tree_oid": SHA1,
        "external_revisions": {"PersonalClaw/PersonalClawApps": SHA1},
        "toolchain": {"python": "3.13.16"},
        "evidence_digests": {"lint": DIGEST},
    }
    data.update(overrides)
    return data


def _refusal(call: Callable[[], object]) -> str:
    with pytest.raises(trust.Refused) as refused:
        call()
    return str(refused.value)


# ── The rows: each site refuses each malformed id before reading git ──────────────────────────


@pytest.mark.parametrize("bad", BAD_IDS)
def test_require_on_main_reads_the_whole_id(tmp_path: Path, bad: str) -> None:
    reason = _refusal(lambda: trust.require_on_main(tmp_path, bad, main="main"))
    assert "is not a full commit id" in reason


@pytest.mark.parametrize("field", ["controller_revision", "head_sha", "tree_oid"])
@pytest.mark.parametrize("bad", BAD_IDS)
def test_a_receipts_ids_are_read_whole(tmp_path: Path, field: str, bad: str) -> None:
    reason = _refusal(lambda: trust.check_receipt(tmp_path, _receipt(**{field: bad}), main="main"))
    assert f"{field} must be a full object id" in reason


@pytest.mark.parametrize("bad", BAD_IDS)
def test_a_receipts_external_revisions_are_read_whole(tmp_path: Path, bad: str) -> None:
    data = _receipt(external_revisions={"PersonalClaw/PersonalClawApps": bad})
    reason = _refusal(lambda: trust.check_receipt(tmp_path, data, main="main"))
    assert "external_revisions has a malformed value" in reason


@pytest.mark.parametrize("bad", BAD_DIGESTS)
def test_a_receipts_evidence_digests_are_read_whole(tmp_path: Path, bad: str) -> None:
    data = _receipt(evidence_digests={"lint": bad})
    reason = _refusal(lambda: trust.check_receipt(tmp_path, data, main="main"))
    assert "evidence_digests has a malformed value" in reason


# ── The positive controls: a well-formed id passes the format check ──────────────────────────


@pytest.mark.parametrize("good", [SHA1, SHA256], ids=["40 hex", "64 hex"])
def test_a_well_formed_id_gets_past_the_format_check(tmp_path: Path, good: str) -> None:
    """With no repository there, the refusal is git's, not the format's."""
    reason = _refusal(lambda: trust.require_on_main(tmp_path, good, main="main"))
    assert "is not a full commit id" not in reason

    data = _receipt(
        controller_revision=good,
        head_sha=good,
        tree_oid=good,
        external_revisions={"PersonalClaw/PersonalClawApps": good},
    )
    reason = _refusal(lambda: trust.check_receipt(tmp_path, data, main="main"))
    assert "must be a full object id" not in reason
    assert "malformed value" not in reason

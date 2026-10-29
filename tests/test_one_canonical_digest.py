"""Every digest a yes is bound to is ``ledger.hashing.canonical_digest``, and none recorded moves.

Shared contracts §2.4: the SHA-256 hex of ``json.dumps(value, sort_keys=True, ensure_ascii=False,
separators=(",", ":"), default=str)`` as UTF-8. The workflow version digest an automation's Allow
records, a tool definition's digest the owner's trust in its labels is sealed to, and a study's
registration hash were each that form spelled out where it was used; they are calls to the one
function now. The digests below were recorded by the code before that change, and are pinned as
literal hex so this cannot pass by computing both sides with the new function. Each value holds
text other than ASCII, which is where a second spelling (``ensure_ascii=True``) would differ.
``tests/rails/test_digests_have_one_canonical_form.py`` holds the rest of the package to it.
"""

from __future__ import annotations

import datetime as dt
import hashlib

from personalclaw import mcp_read_only_trust
from personalclaw.evals.studies import StudyRegistration
from personalclaw.ledger.hashing import canonical_digest
from personalclaw.workflows import versions

WORKFLOW = {
    "name": "café-run",
    "version": 3,
    "created_at": "2026-10-04T15:17:09Z",
    "root": {"kind": "transform", "id": "résumé", "config": {"expr": "done — ✓"}},
    "metadata": {"a2a_published": True, "owner_note": "naïve"},
}
TOOL = {
    "name": "search_notes",
    "description": "Search the notes — quickly, in café or résumé",
    "inputSchema": {
        "type": "object",
        "properties": {"q": {"type": "string", "description": "the words"}},
        "required": ["q"],
    },
    "annotations": {"readOnlyHint": True, "title": "Search ✓"},
}
STUDY = StudyRegistration(
    study_id="st-1",
    subject={"template_id": "café"},
    hypothesis="the naïve arm wins",
    inputs=("a", "b"),
    k=3,
    rubric_sha256="0" * 64,
    registered_ts=1791127029.5,
)


def test_recorded_digests_still_match() -> None:
    assert versions.digest(WORKFLOW) == (
        "5c93ccf4f5344c48676e13ac74ccced4910b85b35aca946c4d0fc7e92c450ca5"
    ), "an automation's Allow recorded for this version no longer matches it"
    assert mcp_read_only_trust.digest(TOOL) == (
        "ec8502d2d23a2c99edce6f241f22ad3aed686f232b06e722362f0e91521edb77"
    ), "a tool the owner trusted reads as changed"
    assert mcp_read_only_trust.part_digests(TOOL) == {
        "description": "9c6b16878c681255425cc06059531312a4f8f110f12fdde3fb3f791f5eea2d59",
        "inputSchema": "3143da5d7972f235145d87f197bf96fe4c128ac411721f7739f8f6b953e1ac9b",
        "annotations": "0c6fba08915327d5620d4259fe51184edc8f9df58441b4d6279080cd38a268c6",
    }
    assert STUDY.sha256() == (
        "126b9d0c2f9e653d3165bd60f0cf02590def06dd261da1145e8a21495bd36171"
    ), "a registered study's seal would call it tampered"


def test_the_canonical_digest_is_the_compact_form() -> None:
    """Sorted keys at every depth, no whitespace, text as itself in UTF-8, and ``str`` of a value
    JSON cannot hold."""
    compact = '{"a":[1,{"c":"é","d":null}],"b":"2026-10-04 15:17:09+00:00"}'
    value = {
        "b": dt.datetime(2026, 10, 4, 15, 17, 9, tzinfo=dt.UTC),
        "a": [1, {"d": None, "c": "é"}],
    }
    assert canonical_digest(value) == hashlib.sha256(compact.encode("utf-8")).hexdigest()
    assert canonical_digest(value) == (
        "739c6be49235da6bf6092ddff05f7f1629db42caf51d7e80768dc219bb312f7b"
    )

"""One precondition for every whole-document write: a save from a stale page is refused.

A settings surface that saves a WHOLE list or document builds it from the copy it read. When
that copy is older than what is stored — another tab saved since, or the gateway wrote the same
data itself — the save used to replace the newer value, and the change made elsewhere vanished
without a word. Nothing was wrong with either write on its own; the loss is in the gap between
the read and the write, so no per-field validation could see it.

The contract, once for every such route:

* **Every read of such a document carries its revision.** :func:`revision_of` is a digest of the
  document's canonical JSON, so it needs no counter in any store: whoever changes the data — a
  second tab, the agent, a background job, a hand edit of the file — changes the revision by
  construction, and a writer that forgets to bump anything cannot exist.
* **Every whole-document write names the revision it was based on**, in ``If-Match`` — the header
  the artifact editor already sends for the same reason (``artifacts/handlers._if_match``).
* **A stale base is refused, before anything is written** — :func:`stale_write_refusal`, called
  with the document as it is stored at the moment of the write:

  - no ``If-Match`` → ``428 revision_required``;
  - a revision other than the current one → ``409 stale_write``.

Neither refusal carries the current revision. The only way to learn one is to read the document
it describes, so a client cannot "fix" a 409 by resending its stale copy with a fresher number —
which is exactly the overwrite this exists to stop. The web client keeps what the user typed, says
the document changed elsewhere, and offers to reload and re-apply the change or to review the
difference (``web/src/lib/staleWrite.ts``).

Where a write can be a per-item operation instead — adding or removing one name from an
allowlist — it is one, and needs no revision at all: an add or a remove applied to what is stored
now cannot undo anyone else's change.

The digest is of the SAME projection the read hands out. A route that masks part of a document
(a stored secret) takes the revision of the masked form, so a revision never encodes more than
its reader could already see.

A route that audits a refusal records which one it was, with :func:`refusal_outcome` — never
words of its own. Each route used to write its own ("stale base", ``stale_write``, a bare
``denied``), and none could tell the two refusals apart.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from aiohttp import web

from personalclaw.http_errors import json_error

__all__ = [
    "OUTCOME_REVISION_REQUIRED",
    "OUTCOME_STALE_WRITE",
    "REVISION_HEADER",
    "claimed_revision",
    "refusal_outcome",
    "revision_of",
    "stale_write_refusal",
]

#: The request header a whole-document write names its base revision in.
REVISION_HEADER = "If-Match"

#: The audit outcome of each refusal, named after its wire code. Two words, because they are two
#: events. A ``409 stale_write`` kept a change made elsewhere from being undone. A ``428
#: revision_required`` came from a writer that names no base at all — an older client, a script,
#: an app page that cannot send the header — and nothing about it was stale; recording it as a
#: stale base told the operator a concurrent edit happened when none did. Both are in the Denied
#: family (``sel.AUDIT_OUTCOME_FAMILIES``): in each the write was asked for and refused.
OUTCOME_STALE_WRITE = "denied_stale_write"
OUTCOME_REVISION_REQUIRED = "denied_revision_required"


def revision_of(document: Any) -> str:
    """The revision a read reports for *document* — a digest of its canonical JSON.

    Key order and whitespace do not change it; any change to a value does. ``ensure_ascii`` keeps
    the canonical form encodable whatever a string holds (a lone surrogate would otherwise raise
    on encode), and ``default=str`` keeps a non-JSON leaf (a timestamp object) from making a
    document un-revisionable.
    """
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]


def claimed_revision(request: web.Request) -> str:
    """The revision the request says it was based on, or ``""`` when it names none.

    Accepts the bare token and the quoted and weak forms HTTP clients add on their own
    (``"a1b2"``, ``W/"a1b2"``) — the same tolerance the artifact precondition has.
    """
    raw = (request.headers.get(REVISION_HEADER) or "").strip()
    if raw.startswith("W/"):
        raw = raw[2:]
    return raw.strip().strip('"').strip()


def stale_write_refusal(request: web.Request, current: Any, *, what: str) -> web.Response | None:
    """The refusal a whole-document write gets when its base is not *current*, else ``None``.

    Call it with the document exactly as its read would present it, at the moment of the write —
    inside the store's transaction (a ``config.json`` write: `config.transactions`, whose lock
    every process takes), under the route's lock, or with no ``await`` between this check and the
    write — so nothing can land between the comparison and the save.

    *what* names the document inside the message — a config path, or "the skill 'x'".
    """
    claimed = claimed_revision(request)
    if not claimed:
        return json_error(
            "revision_required",
            message=(
                f"This write is checked against the copy of {what} it was built from, so it must "
                f"name that copy: read {what} and send its revision in {REVISION_HEADER}."
            ),
            status=428,
        )
    if claimed != revision_of(current):
        return json_error(
            "stale_write",
            message=(
                f"This write replaces {what}, which changed after the copy it was built from was "
                "read — saving it would have undone that change. Nothing was saved: read it "
                "again, re-apply the edit, and save."
            ),
            status=409,
        )
    return None


def refusal_outcome(refusal: web.Response) -> str:
    """The audit outcome for a refusal :func:`stale_write_refusal` returned — which of the two it
    was, read off the refusal itself, so a route cannot word it differently from the answer."""
    return OUTCOME_REVISION_REQUIRED if refusal.status == 428 else OUTCOME_STALE_WRITE

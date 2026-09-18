"""The ONE rule for "these two saves are the same artifact" (#290).

Two independent writers can reach the library with the same deliverable, and for loops
that is not an accident — it is what the kind briefs ask for. A goal loop's brief tells
the worker to ``artifact_save`` its output *tagged* ``loop:<id>``
(``loop/kinds/goal.py``), and on completion the framework graduates the same on-disk
deliverable to an artifact carrying that same tag
(``loop/watchdog.py:_register_deliverable_artifact``). So one document arrives twice.

The dedupe that was supposed to catch that keyed on ``source_path``, which
``artifact_save`` has no parameter for and therefore never sets — a key that cannot
match, so the check could never fire in either order. What DOES exist on both calls is
the pair this module keys on:

* the ``loop:<id>`` **tag**, which both writers are told to set, and
* the **content**, which is byte-identical when it is the same deliverable.

A shared tag alone is too weak (``draft`` says nothing about subject) and identical
content alone is too weak (a copied template is legitimately duplicated). Together they
are a strong claim, and — importantly — a claim both writers can evaluate, which is what
makes the rule expressible ONCE here rather than approximated on each side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.artifacts.models import Artifact

__all__ = ["find_same_deliverable"]


def find_same_deliverable(
    prov: Any,
    *,
    tags: list[str] | None,
    content: str,
    exclude_slug: str = "",
) -> "Artifact | None":
    """The existing artifact that already IS *content* under one of *tags*, or ``None``.

    Provider-agnostic on purpose: it takes the provider rather than living on the
    ``ArtifactProvider`` ABC, so a backend cannot ship a divergent notion of "same
    deliverable" — there is one definition and every writer consults it.

    Only ``loop:``-scoped tags are considered. That is a deliberate bound, not shyness:
    an ownership tag names a *run* whose output is singular, whereas a descriptive tag
    (``draft``, ``report``) does not, and content-identity dedupe under a descriptive tag
    would silently fold two genuinely separate documents together. Widen this only with a
    tag namespace that makes the same singular-subject claim.

    Never raises — a dedupe that fails must degrade to "no duplicate found", which yields
    the pre-existing behaviour (a second artifact) rather than losing a save.
    """
    if not content:
        return None
    scoped = [t for t in (tags or []) if isinstance(t, str) and t.startswith("loop:")]
    if not scoped:
        return None
    try:
        for tag in scoped:
            for candidate in prov.list(tag=tag):  # newest-first
                if exclude_slug and candidate.slug == exclude_slug:
                    continue
                # A frozen artifact (SM-9) refuses every content mutation with
                # `PermissionError`, so returning one here would convert a duplicate into a
                # FAILED save — strictly worse than the duplicate. It can never be the
                # adoption target; skip it and let the caller create its own.
                if getattr(candidate, "readonly", False):
                    continue
                full = prov.get(candidate.slug)
                if full is None or getattr(full, "readonly", False):
                    continue
                if (full.content or "") == content:
                    return full
    except Exception:  # noqa: BLE001 — a dedupe miss is recoverable; a raised save is not
        return None
    return None

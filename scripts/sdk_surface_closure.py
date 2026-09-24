"""One definition of the app SDK's API closure — what a published surface requires.

``personalclaw.sdk.*`` is the only import path an installable app may use, so the facade's
exported types are a *contract*: an app that receives a published value has to be able to
NAME the types that value's fields and methods mention. Walking that requirement relation
to a fixed point answers two different questions with one traversal, and both callers live
in this repo:

* **The gap direction** — a core type that the published surface REQUIRES but no ``sdk``
  module exports. That is a broken contract (the app must derive or re-declare the type),
  and ``tests/test_sdk_surface_is_public.py`` asserts the set is empty.
* **The consumed direction** — an ``sdk`` export that ANOTHER export's field or method
  signature names. That is a *reader*, and
  ``scripts/generate_inert_surface_baseline.py``'s ``sdk_export`` census needs it.

The second one exists because the census's ordinary reader test cannot work for this kind.
Every other surface it censuses has an in-repo consumer by construction — a config key is
read by ``load()``, a trigger kind is dispatched under ``triggers/``. **An SDK export's
consumer is outside the tree by definition**: installable app bundles live in a separate
repository. So "no ``from personalclaw.sdk… import X`` anywhere in this repo" cannot tell a
public API type apart from an unkept promise, and scoring it as inert penalises exactly the
thing the boundary exists to do — the cheapest way to satisfy the counter would be to stop
exporting types apps need. The API closure is the missing in-repo reader: when a *published*
signature names a type, that signature is the thing that requires it, and it is mechanically
checkable rather than asserted.

WHAT STILL COUNTS AS INERT, because the census must keep its teeth: an export reachable
from nothing at all. Measured on the tree that introduced this rule — 170 of the 229
``sdk_export`` surfaces stay reported, 54 of them exported *types* that no published
signature names (the provider protocols an app subclasses, the service objects it is handed,
the error classes it catches) and 116 functions and constants, which have no signature for
this rule to reach them through. The rule removes 59; it does not empty the counter.

TWO NARROWING RULINGS, both deliberate and both pinned by tests:

* **A type that names only ITSELF does not clear itself.** ``AppManifest.from_dict()``
  returns ``AppManifest`` and ``EgressPolicy.with_overrides()`` returns ``EgressPolicy``;
  a self-referential constructor is present on most dataclasses in this tree, so counting it
  would clear nearly every type and leave the counter meaning nothing. Four exports rely on
  this today (``channel.AppConfig``, ``channel.Stats``, ``manifest.AppManifest``,
  ``net.EgressPolicy``): each carries a self-edge, each is still reported.
* **Only exported CLASSES are owners; an exported FUNCTION's signature does not clear.**
  This one is a real judgement call rather than an oversight, and it was measured before
  being made. Nine residual types would clear if function signatures counted
  (``net.fetch`` → ``FetchResponse``, ``channel.guard_inbound`` → ``TrustVerdict``, …) — but
  the two directions share one walk on purpose, and admitting function roots to that walk
  opens **22 new gaps**: ``AuthConfig``, ``ScheduleJob``, ``FetchResponse``,
  ``McpClientRegistry`` and eighteen more would become types the facade owes an app, one of
  them (``triggers.tools.ToolResult``) colliding by name with an already-exported type. That
  is a second tranche of the same defect and a separate change; it is not a free
  generalisation of this one. Widening here without widening the gap rail would give the two
  halves different definitions of "published signature", which is the incoherence this
  module exists to prevent.

DETERMINISM. The walk's output is consumed as set membership and rendered sorted, so
traversal order cannot reach the baseline. ``surface_closure`` is cached because the census
renders twice per test session and the walk imports every ``sdk`` submodule; the returned
mappings must be treated as read-only.
"""

from __future__ import annotations

import dataclasses
import functools
import importlib
import inspect
import pkgutil
import types
import typing
from typing import Any

#: ``AppConfig`` is exported (a channel app is handed the live config), and ~45 section
#: dataclasses hang off it — ``config.inbox``, ``config.memory``, ``config.guardrails``, … An
#: app READS those by attribute access and never needs to NAME their types, so re-exporting
#: forty-five sections would make a facade whose whole claim is thinness fat, to buy nothing.
#: Stated here rather than left implicit because an undeclared exception is how the five-of-
#: twenty-one manifest subset survived: nothing said whether the missing names were a decision.
CLOSURE_EXEMPT_PREFIX = "personalclaw.config."


def sdk_submodules() -> dict[str, types.ModuleType]:
    """Every ``personalclaw.sdk`` submodule, keyed by its bare name (``"manifest"``)."""
    from personalclaw import sdk

    return {
        mi.name: importlib.import_module(f"personalclaw.sdk.{mi.name}")
        for mi in pkgutil.iter_modules(sdk.__path__)
    }


def core_types_in(annotation: Any) -> list[type]:
    """Every concrete ``personalclaw`` class an annotation mentions, unwrapping generics."""
    found: list[type] = []
    for arg in getattr(annotation, "__args__", ()) or ():
        found.extend(core_types_in(arg))
    if isinstance(annotation, type) and getattr(annotation, "__module__", "").startswith(
        "personalclaw"
    ):
        found.append(annotation)
    return found


def types_an_app_must_name(obj: type) -> list[tuple[str, type]]:
    """``(where, type)`` for every core type a caller of ``obj`` has to be able to spell.

    Two sources, and both are load-bearing: a dataclass FIELD (you cannot construct a value
    for a field whose type you cannot import) and a public method SIGNATURE — its parameters
    (you cannot pass one) and its return (you cannot annotate what you got back).
    """
    out: list[tuple[str, type]] = []
    try:
        for field, ann in typing.get_type_hints(obj).items():
            out.extend((f"field {field}", t) for t in core_types_in(ann))
    except Exception:  # pragma: no cover - unresolvable forward ref
        pass
    for meth_name, meth in inspect.getmembers(obj, callable):
        if meth_name.startswith("_"):
            continue
        if not str(getattr(meth, "__module__", "")).startswith("personalclaw"):
            continue  # inherited from object/abc, not part of this contract
        try:
            hints = typing.get_type_hints(meth)
        except Exception:  # pragma: no cover - unresolvable forward ref
            continue
        for arg, ann in hints.items():
            out.extend((f"{meth_name}() {arg}", t) for t in core_types_in(ann))
    return out


@dataclasses.dataclass(frozen=True)
class SurfaceClosure:
    """The two directions of one traversal, plus the counters that prove it ran.

    ``gaps`` maps ``"module.QualName"`` of an UNEXPORTED required type to the fields and
    signatures that require it. ``consumed`` maps ``"<sdk submodule>.<exported name>"`` of an
    EXPORTED type to the same — that is the census's reader evidence. ``roots`` counts
    exported classes and ``edges`` counts resolved type references; either at zero satisfies
    an emptiness assertion trivially, so both are reported rather than inferred.

    The mappings are shared with every other caller (``surface_closure`` is cached) and must
    not be mutated.
    """

    gaps: dict[str, set[str]]
    consumed: dict[str, set[str]]
    roots: int
    edges: int


@functools.cache
def surface_closure(pretend_missing: frozenset[str] = frozenset()) -> SurfaceClosure:
    """Walk the published surface to a fixed point; see ``SurfaceClosure`` for the result.

    ``pretend_missing`` holds ``"module.QualName"`` keys to treat as NOT exported. Only the
    falsification test passes it: dropping one real name reproduces the pre-fix state exactly,
    which is how this walk proves it can fail at all.
    """
    exported: set[int] = set()
    labels: dict[int, list[str]] = {}
    roots: list[type] = []
    for mod_name, mod in sorted(sdk_submodules().items()):
        for name in getattr(mod, "__all__", None) or []:
            obj = getattr(mod, name, None)
            if obj is None:
                continue
            if isinstance(obj, type) and f"{obj.__module__}.{obj.__qualname__}" in pretend_missing:
                continue
            exported.add(id(obj))
            labels.setdefault(id(obj), []).append(f"{mod_name}.{name}")
            if isinstance(obj, type):
                roots.append(obj)

    gaps: dict[str, set[str]] = {}
    consumed: dict[str, set[str]] = {}
    edges = 0
    seen = {id(t) for t in roots}
    frontier = roots
    while frontier:  # a newly-required type drags in its own requirements
        nxt: list[type] = []
        for owner in frontier:
            for where, t in types_an_app_must_name(owner):
                edges += 1
                reason = f"{owner.__qualname__}.{where}"
                if id(t) in exported:
                    # An export named by ANOTHER export's signature has a reader. A type
                    # that names only itself vouches for itself, which clears nothing.
                    if t is not owner:
                        for label in labels[id(t)]:
                            consumed.setdefault(label, set()).add(reason)
                    continue
                if t.__module__.startswith(CLOSURE_EXEMPT_PREFIX):
                    continue
                if t.__qualname__.startswith("_"):
                    continue  # a private type is a separate defect, caught elsewhere
                gaps.setdefault(f"{t.__module__}.{t.__qualname__}", set()).add(reason)
                if id(t) not in seen:
                    seen.add(id(t))
                    nxt.append(t)
        frontier = nxt
    return SurfaceClosure(gaps=gaps, consumed=consumed, roots=len(roots), edges=edges)


def consumed_exports() -> frozenset[str]:
    """``{"<sdk submodule>.<exported name>"}`` for every export another export's signature
    names — the ``sdk_export`` census's reader set."""
    return frozenset(surface_closure().consumed)

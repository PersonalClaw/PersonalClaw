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
from nothing at all. Measured on the tree that widened this rule to function roots (#3511) —
159 of the 248 ``sdk_export`` surfaces stay reported, 43 of them exported *types* that no
published signature names (the provider protocols an app subclasses, the service objects it
is handed, the error classes it catches) and 116 functions and constants, which have no
signature for this rule to reach them THROUGH. Admitting a function as a ROOT does not make
it a TARGET: ``core_types_in`` yields only types, so a function can never be the thing an
annotation names, and the 116 are untouched by the widening. The rule removes 89 of 248; it
does not empty the counter — and 21 of the 89 are the function half, measured by re-rendering
the census with the function seeding disabled (180 reported, 64 types).

WHO OWNS A PUBLISHED SIGNATURE — both exported CLASSES and exported FUNCTIONS (#3511). The
class half (a dataclass field, a public method's parameters and return) landed first; the
function half was measured and deferred in the same change, then taken here. It is the same
defect with the same consequence: an app calling ``net.fetch`` cannot annotate what it got
back, so a typed integration is impossible and the failure surfaces as prose or ``Any``. A
surface closed under fields and methods but not under functions is closed against one kind of
caller and open against another, and nothing in the boundary's stated intent draws that line.
Measured when it landed: admitting function roots CLEARS 11 still-inert exports (9 named
directly by a function signature — ``net.evaluate`` → ``GuardDecision``,
``channel.guard_inbound`` → ``TrustVerdict``, … — plus 2 more, ``model.ModelCatalog`` and
``skill.SkillsMarketplace``, reached only because a function root drags a new class into the
frontier whose own methods then name them) and OPENS 22 new gaps — 19 exported, and the three
under ``personalclaw.dashboard.`` held out as a declared exemption whose reason is another
rail, recorded on ``CLOSURE_EXEMPT_PREFIX`` below.

WHAT IS NOT A ROOT, stated rather than left implicit. The facade's ``__all__`` holds exactly
four shapes besides classes: 136 plain functions (roots), 51 constants and values (no
signature to walk), 2 ``Literal[...]`` aliases (``cli.DoctorStatus``, ``model.CancelOutcome``
— an alias of values, not a call), and 2 re-exported MODULES (``channel.session_restrictions``,
``channel.trust_mode``). The modules are deliberately not walked: a module in ``__all__``
publishes an unbounded namespace rather than one signature, so rooting it would make the
closure's scope depend on a core module's internals. That a module is exported at all is a
separate question about this facade, not a gap in this walk.

TWO RULINGS THAT NARROW IT, both deliberate and both pinned by tests:

* **A type that names only ITSELF does not clear itself.** ``AppManifest.from_dict()``
  returns ``AppManifest`` and ``EgressPolicy.with_overrides()`` returns ``EgressPolicy``;
  a self-referential constructor is present on most dataclasses in this tree, so counting it
  would clear nearly every type and leave the counter meaning nothing. 34 exports carry a
  self-edge and the ruling is LOAD-BEARING for three — ``channel.AppConfig``,
  ``channel.Stats``, ``manifest.AppManifest``: each carries a self-edge, each is still
  reported. ``net.EgressPolicy`` was a fourth until #3511, when admitting function roots gave
  it three real readers (``net.evaluate(policy)``, ``net.fetch(policy)``,
  ``net.egress_policy_for() -> EgressPolicy``) and it left the set WITHOUT this ruling moving
  — which is why the three are pinned by name in ``test_inert_surface_baseline.py`` rather
  than counted. A count would have read that clear as the ruling weakening.
* **No name is published twice meaning two different types.** Widening to function roots
  required ``triggers.tools.ToolResult`` — the return of three published ``sdk.channel``
  functions — while ``sdk.tool.ToolResult`` was already
  ``tool_providers.base.ToolResult``, a disjoint dataclass (``success``/``output``/``error``/
  ``agent_error`` against ``ok``/``text``/``data``). An app author writing
  ``from personalclaw.sdk.x import ToolResult`` must get exactly one thing. Resolved by
  RENAMING the less established of the two to ``AutomationToolResult``, not by aliasing it in
  the facade: measured, the triggers one had **zero** by-name importers anywhere in this repo
  and **zero** references in the apps repo, with all 49 of its references inside its own
  defining module; ``tool_providers.base.ToolResult`` is imported by name by **all eleven**
  first-party apps (690 references). Neither name appears in ``docs/``, so the tie-break is
  the facade's actual consumers. A facade-only alias was rejected because it leaves core with
  two ``ToolResult``s — the next sdk module to face ``triggers.tools`` re-mints the collision,
  and a traceback then disagrees with the docs about what the type is called. Keeping it OUT of
  the facade was the third option and the wrong one here: it would have left three published
  ``sdk.channel`` functions with an unnameable return, which is the defect rather than a ruling
  about it. The only types held out of the 22 are the three the ``personalclaw.dashboard.``
  exemption covers, for a reason that has nothing to do with naming.

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

#: Declared exemptions from the closure. Stated here rather than left implicit because an
#: undeclared exception is how the five-of-twenty-one manifest subset survived: nothing said
#: whether the missing names were a decision. ``str.startswith`` takes the tuple directly.
#:
#: * ``personalclaw.config.`` — ``AppConfig`` is exported (a channel app is handed the live
#:   config) and ~45 section dataclasses hang off it: ``config.inbox``, ``config.memory``,
#:   ``config.guardrails``, … An app READS those by attribute access and never needs to NAME
#:   their types, so re-exporting forty-five sections would make a facade whose whole claim is
#:   thinness fat, to buy nothing.
#:
#: * ``personalclaw.dashboard.`` — added by #3511, and the reason is a DIFFERENT rail rather than
#:   a judgement about thinness. Admitting function roots made ``DashboardState`` owed (it is the
#:   first parameter of the published ``channel.save_session_to_history``) and ``SseRegistry`` /
#:   ``SseHub`` owed through it (``DashboardState.loop_sse()`` and siblings return them).
#:   Exporting them requires ``sdk/channel.py`` to import ``personalclaw.dashboard.state`` and
#:   ``.sse``, which are the FIFTH and SIXTH ``core-must-not-import-the-http-surface`` edges on
#:   that file — and that rule grandfathers the four it already has for exactly this reason:
#:   *"Shrink-only GRANDFATHERS those instead of an exemption list — an allowlist is a thing that
#:   rots, a measured floor is not."* So the HTTP surface is deliberately not part of the app
#:   type contract, and the structural ratchet is the mechanism that says so.
#:
#:   THE RESIDUAL IS REAL AND IS NOT CLOSED BY THIS EXEMPTION: ``save_session_to_history`` stays
#:   published with a parameter type an app cannot name. The honest reading is that a function
#:   whose parameter type the layering forbids publishing does not belong on the boundary, but it
#:   is driven by a bundled channel app in a separate repository and pinned by
#:   ``test_the_promoted_names_resolve_and_the_private_ones_are_gone``, so removing it is a
#:   cross-repo change and not this one. Recorded so the next reader finds a decision and its
#:   cost rather than a silent skip; ``test_inert_surface_baseline`` asserts both that this
#:   exemption is load-bearing and that the structural baseline still pins the four edges the
#:   argument rests on, so the justification cannot rot into prose.
CLOSURE_EXEMPT_PREFIX = ("personalclaw.config.", "personalclaw.dashboard.")


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


def signature_requirements(fn: Any) -> list[tuple[str, type]]:
    """``(parameter-name-or-"return", type)`` for every core type ``fn``'s signature mentions.

    THE one definition of "what a signature names", shared by a published FUNCTION (its own
    parameters and return) and a published class's public METHODS. Both reach it through the
    same ``get_type_hints`` resolution and the same ``core_types_in`` unwrapping, so no caller
    of this module can end up with its own notion of what a signature published.
    """
    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # pragma: no cover - unresolvable forward ref
        return []
    return [(arg, t) for arg, ann in hints.items() for t in core_types_in(ann)]


def types_an_app_must_name(obj: type) -> list[tuple[str, type]]:
    """``(where, type)`` for every core type a caller of CLASS ``obj`` has to be able to spell.

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
        out.extend((f"{meth_name}() {arg}", t) for arg, t in signature_requirements(meth))
    return out


def types_a_caller_must_name(fn: Any) -> list[tuple[str, type]]:
    """``(where, type)`` for every core type a caller of published FUNCTION ``fn`` must spell.

    The function analogue of ``types_an_app_must_name`` and the whole of #3511: an app that
    calls ``net.fetch`` has to annotate what it got back, and an app that calls
    ``channel.resolve_bind_host`` has to be able to construct the argument. A method already
    counted for exactly this reason; a module-level function is the same signature with no
    ``self``.
    """
    return [(f"() {arg}", t) for arg, t in signature_requirements(fn)]


@dataclasses.dataclass(frozen=True)
class SurfaceClosure:
    """The two directions of one traversal, plus the counters that prove it ran.

    ``gaps`` maps ``"module.QualName"`` of an UNEXPORTED required type to the fields and
    signatures that require it. ``consumed`` maps ``"<sdk submodule>.<exported name>"`` of an
    EXPORTED type to the same — that is the census's reader evidence. ``roots`` counts
    exported classes, ``function_roots`` counts exported functions and ``edges`` counts
    resolved type references; ANY of the three at zero satisfies an emptiness assertion
    trivially, so all three are reported rather than inferred. ``function_roots`` is separate
    from ``roots`` because the two root kinds are detected by different predicates, and a
    regression in the function one (a decorator that makes ``inspect.isfunction`` false) would
    otherwise hide inside a healthy class count.

    The mappings are shared with every other caller (``surface_closure`` is cached) and must
    not be mutated.
    """

    gaps: dict[str, set[str]]
    consumed: dict[str, set[str]]
    roots: int
    function_roots: int
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
    function_roots: list[tuple[str, Any]] = []
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
            elif inspect.isfunction(obj):
                function_roots.append((f"{mod_name}.{name}", obj))

    gaps: dict[str, set[str]] = {}
    consumed: dict[str, set[str]] = {}
    edges = 0
    seen = {id(t) for t in roots}
    # Each frontier entry carries the PREFIX its requirements are blamed on, because the two
    # root kinds read differently at a call site: a class's is `Task.transition() to`, a
    # function's is the facade path an app actually imports — `channel.resolve_bind_host()
    # auth_cfg`. Composing `prefix + where` keeps that difference in the seeding rather than
    # re-deriving it per edge from the shape of the string.
    frontier: list[tuple[Any, str, list[tuple[str, type]]]] = [
        (cls, f"{cls.__qualname__}.", types_an_app_must_name(cls)) for cls in roots
    ] + [(fn, label, types_a_caller_must_name(fn)) for label, fn in function_roots]
    while frontier:  # a newly-required type drags in its own requirements
        nxt: list[tuple[Any, str, list[tuple[str, type]]]] = []
        for owner, prefix, requirements in frontier:
            for where, t in requirements:
                edges += 1
                reason = f"{prefix}{where}"
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
                    nxt.append((t, f"{t.__qualname__}.", types_an_app_must_name(t)))
        frontier = nxt
    return SurfaceClosure(
        gaps=gaps,
        consumed=consumed,
        roots=len(roots),
        function_roots=len(function_roots),
        edges=edges,
    )


def consumed_exports() -> frozenset[str]:
    """``{"<sdk submodule>.<exported name>"}`` for every export another export's signature
    names — the ``sdk_export`` census's reader set."""
    return frozenset(surface_closure().consumed)

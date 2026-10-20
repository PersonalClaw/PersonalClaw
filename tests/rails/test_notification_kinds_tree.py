"""The source-tree checks split from
``tests/test_notification_kinds.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index

from personalclaw import notification_kinds as nk


def test_EVERY_EMITTER_IN_THE_TREE_IS_REGISTERED():
    """The direction the round-trip test below cannot see.

    🪤 That test iterates `_ATTENTION_FLAT` — the kinds that ARE registered — so it can only ever
    check the compliant population. It cannot notice a kind that is absent from the map entirely,
    which is exactly how `learning/proposal` and `planning/scratchpad`'s `planning/proposal` shipped
    unregistered: `resolve_kind` fell open to system/generic on every emission, so they carried
    GENERIC's severity and mode rather than their own, never appeared as a row in Settings →
    Notifications (`rules_document()` iterates the registry), and the daily digest grouped
    them under **"Skill proposal"**, because a pair with no wire string collapses onto the
    bare `kind`.

    So this sweeps the POPULATION: every literal `(source, kind)` an `emit_attention_item` call site
    in the tree passes must resolve to a registered pair. Source-level, because that is where a new
    emitter is added — a runtime sweep would only see the paths a test happens to exercise.
    """
    import ast

    pairs: dict[tuple[str, str], str] = {}
    for path, tree in source_index.package_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name != "emit_attention_item":
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            src, kind = kw.get("source"), kw.get("kind")
            # Only LITERAL pairs can be checked statically; a computed one (an app's dynamic
            # kind_suffix) is covered by its own registration path.
            if isinstance(src, ast.Constant) and isinstance(kind, ast.Constant):
                if isinstance(src.value, str) and isinstance(kind.value, str):
                    pairs[(src.value, kind.value)] = f"{path.name}:{node.lineno}"

    assert len(pairs) >= 6, f"the sweep found only {len(pairs)} literal emitters — parser drift?"
    registered = {(k.source, k.kind) for k in nk.all_kinds()}
    missing = sorted(
        f"{s}/{k}  ({where})" for (s, k), where in pairs.items() if (s, k) not in registered
    )
    assert not missing, (
        "these emitters are not in the registry, so they lose their own severity, mode and "
        "settings row, and the digest groups them under whichever kind their bare wire string "
        "collides with:\n  " + "\n  ".join(missing)
    )


def _emitted_kind_strings() -> set[str]:
    """Flat kind strings passed to a real ``.notify(...)`` call anywhere in src/.

    Dynamic first arguments (the loop watchdog's event map, the notify action provider's
    config-driven kind) can't be read statically; their possible values are covered by
    the dedicated tests below.
    """
    found: set[str] = set()
    for _path, tree in source_index.package_trees():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "notify" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return found


def test_no_call_site_passes_a_bare_string_literal():
    """Every emitter goes through a named constant, so a typo can't invent a kind.

    Before this, `notify("warnign", …)` delivered a generic notification forever with no
    error. The constant makes the same typo an ImportError at startup.
    """
    literals = sorted(_emitted_kind_strings())
    assert not literals, (
        f"these .notify() sites still pass bare literals: {literals} — "
        "use a notification_kinds.* constant"
    )


def _emitted_constant_names() -> set[str]:
    """``notification_kinds.X`` attribute names passed as the kind at a call site."""
    found: set[str] = set()
    for _path, tree in source_index.package_trees():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "notify" or not node.args:
                continue
            first = node.args[0]
            if (
                isinstance(first, ast.Attribute)
                and isinstance(first.value, ast.Name)
                and first.value.id == "notification_kinds"
            ):
                found.add(first.attr)
    return found


def test_the_ast_sweep_actually_finds_call_sites():
    """Guards the guards: a refactor that renames notify() must not silently pass.

    Both sweeps above return the empty set when they find nothing, which would make
    `test_no_call_site_passes_a_bare_string_literal` vacuously green. Assert the tree
    really does contain migrated call sites.
    """
    assert len(_emitted_constant_names()) >= 8


def test_every_emitted_constant_resolves():
    """Every constant used at a call site exists and maps to a REGISTERED pair.

    Checks `_WIRE_TO_PAIR` — "every wire string this build understands" — and not `_LEGACY_FLAT`,
    for the reason `test_every_wire_constant_resolves` already records for itself: legacy
    membership is a claim about HISTORY, and requiring it forces a brand-new kind to pretend it has
    some. The loop watchdog's `LOOP_FAILED`/`LOOP_COMPLETE` (#341) are the first emitted constants
    to make that distinction bite; before them every emitted constant happened to be legacy.

    Strengthened while being corrected: the resolution must land on a real registration, not merely
    be present in the map. A constant that resolves to the generic fallback is the silent downgrade
    this test exists to catch, and a membership check alone cannot see it.
    """
    unknown = sorted(n for n in _emitted_constant_names() if not hasattr(nk, n))
    assert not unknown, f"call sites reference nonexistent constants: {unknown}"
    unregistered = sorted(
        n for n in _emitted_constant_names() if getattr(nk, n) not in nk._WIRE_TO_PAIR
    )
    assert not unregistered, (
        f"these constants are emitted but unregistered: {unregistered} — "
        "add a NotificationKind + a wire-string entry"
    )
    generic = sorted(
        n
        for n in _emitted_constant_names()
        if nk.kind_for_legacy(getattr(nk, n)).kind == nk.GENERIC_KIND
        and getattr(nk, n) != nk.GENERIC
    )
    assert not generic, (
        f"these emitted constants resolve to the generic fallback: {generic} — they carry "
        "GENERIC's severity and mode and show no row of their own in Settings → Notifications"
    )

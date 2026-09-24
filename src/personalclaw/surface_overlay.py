"""The L2 surface-overlay producer — user/agent surface overrides as DATA.

AMBIENT-SURFACES §6 names three surface layers (L0 core, L1 app, L2 user/agent) and
`surface_layers.py` + `ui/surfaces/layers.ts` build the ceiling, the boundary and the
refusals for all three. Until this module there was **no L2 producer**: nothing wrote a
user/agent overlay, so the layer was declared and empty. This is the loader that fills
it, and it is deliberately the narrowest thing that can.

Threat posture (owner ruling, 2026-08-26 — this module is built TO it, and every clause
below has a refusal and a test):

1. **An overlay is DATA, never code.** It is a declarative tree of *references* to
   already-registered component names plus their props — the genui DSL body the model
   already writes, whose parser (`ui/genui/parse.ts`) resolves scalars and ids and
   nothing else. There is no eval, no import, no JSX and no expression language. Every
   value this loader accepts is a string, a list or a dict with a CLOSED key set; a shape
   that would need to execute has nowhere to land.
2. **Unknown component names are REFUSED at load, not rendered and not silently
   dropped.** A dropped node is an invisible failure. Note the deliberate difference from
   the chat path: `GenUiWidget` drops ONE bad line so the rest of a model's answer still
   paints, whereas an overlay is a saved artifact a human or agent authored on purpose —
   half of it rendering is worse than a named refusal. So one bad node refuses the WHOLE
   overlay, and the refusal names the offending component. (Enforced on the FE, where the
   registry lives — `ui/surfaces/overlay.ts`.)
3. **Shadowing a core or app-registered component name is refused at load**, through the
   SAME `registerLayerComponent` an app's L1 module goes through — one rule, both
   producers, one code path. An overlay's ``define`` entries are how it can shadow at
   all, so the rule is not vacuous.
4. **Props are host-schema validated** against the target component's declared schema
   (`validateInvocation`), because an unvalidated prop is the injection surface.
5. **Path containment:** overlays load only from ``$PERSONALCLAW_HOME/surfaces/``. Every
   candidate goes through :func:`resolve_overlay_path`, which resolves the real path and
   refuses anything that lands outside — a ``..`` traversal, an absolute path, or a
   symlink pointing out of the directory.
6. **The existing layer machinery is reused, not paralleled.** The ceiling, `LayerBoundary`
   and the error boundary are already layer-generic; this producer FEEDS them. Safe mode
   (``#/dashboard?safe=1`` / ``--safe-surfaces``) forces ``maxLayer=0``, so the FE loader
   does not even fetch and `registerLayerComponent` would refuse an L2 name anyway.

Two consequences of "data, never code" worth stating out loud, because they are why this
format has no knobs a reader might look for:

* **A ``define`` composite takes NO args.** Substituting a caller's argument into a saved
  sub-tree is an expression language, which clause 1 refuses. So a composite declares
  ``args: []`` and `validateInvocation` refuses any arg passed to it as ``excess-args``.
* **A composite has no ``group``.** Groups are a frontend enum (`registry.ts` ``GROUPS``);
  duplicating a closed set across the wire buys a drift risk for a knob nobody needs, so
  every composite registers in ``Layout``.

Refusals come back as :class:`personalclaw.errors.AgentError` envelopes on a **200**, not
as HTTP errors — the same call the tile-action refusal makes and for the same reason: the
FE renders the refusal beside the surface it belongs to, and a 4xx would make "your
overlay names a component that does not exist" indistinguishable from a broken request.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.errors import AgentError

#: The directory under ``$PERSONALCLAW_HOME`` overlays load from. Nothing else is read.
OVERLAY_DIRNAME = "surfaces"

#: A single overlay file's ceiling. An overlay is a hand/agent-authored layout, not a
#: data dump; a megabyte of it is a mistake or an attack, and either way refusing is
#: cheaper than parsing it on every dashboard paint.
MAX_OVERLAY_BYTES = 64 * 1024

#: The surface ids an overlay may target. CLOSED on purpose: an overlay naming a surface
#: nothing renders would be an invisible failure (clause 2), so an unknown id is refused
#: here rather than silently matching no call site. Adding one = a row here plus a
#: ``<SurfaceOverlay surface="…">`` call site, and `test_surface_overlay.py` asserts the
#: pair so the two cannot drift.
OVERLAYABLE_SURFACES = frozenset({"dashboard"})

#: The closed top-level key set. An unknown key is a typo or a shape this loader does not
#: validate — both are refusals, never "ignored".
_FILE_KEYS = frozenset({"surface", "title", "body", "define"})

#: The closed key set of one ``define`` entry.
_DEFINE_KEYS = frozenset({"name", "description", "body"})

#: A component name: the same shape a model may write in the DSL.
_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")


def _code_path(detail: str, *, fix: str) -> AgentError:
    return AgentError(
        code="ERR_SURFACE_OVERLAY_PATH",
        what=detail,
        why=(
            "Surface overlays are read only from the surfaces/ directory inside "
            "PERSONALCLAW_HOME. A path that resolves outside it — a traversal, an "
            "absolute path, or a symlink pointing away — is refused before it is read."
        ),
        fix=fix,
    )


def _code_invalid(detail: str, *, fix: str) -> AgentError:
    return AgentError(
        code="ERR_SURFACE_OVERLAY_INVALID",
        what=detail,
        why=(
            "An overlay is DATA: a JSON object with a closed key set whose values are "
            "strings, lists and objects. Anything else is refused rather than partially "
            "applied, because half a surface is worse than a named refusal."
        ),
        fix=fix,
    )


@dataclass(frozen=True)
class OverlayRefusal:
    """One overlay that would not load, and why. Rendered, never swallowed."""

    file: str
    error: AgentError

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "error": self.error.to_dict()}


@dataclass(frozen=True)
class SurfaceOverlay:
    """One accepted overlay: which surface it targets and the tree it contributes."""

    file: str
    surface: str
    title: str
    body: str
    #: ``[{"name", "description", "body"}]`` — data sub-trees the FE registers at L2.
    define: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "surface": self.surface,
            "title": self.title,
            "body": self.body,
            "define": [dict(d) for d in self.define],
        }


def surfaces_dir() -> Path:
    """``$PERSONALCLAW_HOME/surfaces`` — resolved through ``config_dir()`` every call.

    Never cached at import: a test (and the dev home) redirects ``config_dir`` and a
    module-level constant would keep reading the owner's real home.
    """
    from personalclaw.config import config_dir

    return config_dir() / OVERLAY_DIRNAME


def resolve_overlay_path(name: str) -> Path:
    """The containment gate (clause 5). Returns the real path, or raises ``ValueError``.

    The ONE place a candidate path is decided, so the directory scan and any future
    by-name read cannot diverge into two rules. ``name`` is a bare file name: a separator,
    a ``..`` segment, or an absolute path is refused before the filesystem is touched, and
    a symlink that resolves out of the directory is refused after.
    """
    root = surfaces_dir()
    if not name or name in {".", ".."}:
        raise ValueError("an overlay file name may not be empty")
    if "/" in name or "\\" in name or Path(name).is_absolute():
        raise ValueError(f"{name!r} is not a bare file name inside surfaces/")
    candidate = root / name
    try:
        real = candidate.resolve()
        real_root = root.resolve()
    except OSError as exc:  # pragma: no cover - unreadable mount
        raise ValueError(f"{name!r} could not be resolved: {exc}") from exc
    if real != real_root and not real.is_relative_to(real_root):
        raise ValueError(f"{name!r} resolves outside surfaces/ ({real})")
    return real


def _parse_define(raw: Any) -> tuple[dict[str, str], ...]:
    """Validate the ``define`` list. Raises ``ValueError`` with the offending detail."""
    if not isinstance(raw, list):
        raise ValueError('"define" must be a list of {name, body} objects')
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f'"define"[{i}] must be an object')
        unknown = sorted(set(entry) - _DEFINE_KEYS)
        if unknown:
            raise ValueError(f'"define"[{i}] has unknown key(s): {", ".join(unknown)}')
        name = entry.get("name")
        body = entry.get("body")
        desc = entry.get("description", "")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise ValueError(f'"define"[{i}].name must be a CamelCase component name')
        if not isinstance(body, str) or not body.strip():
            raise ValueError(f'"define"[{i}].body must be a non-empty DSL string')
        if not isinstance(desc, str):
            raise ValueError(f'"define"[{i}].description must be a string')
        if name in seen:
            raise ValueError(f'"define" declares {name!r} twice')
        seen.add(name)
        out.append({"name": name, "description": desc, "body": body})
    return tuple(out)


def _parse_overlay(name: str, text: str) -> SurfaceOverlay:
    """Validate one overlay document. Raises ``ValueError`` with the offending detail."""
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("the top level must be a JSON object")
    unknown = sorted(set(doc) - _FILE_KEYS)
    if unknown:
        raise ValueError(f"unknown top-level key(s): {', '.join(unknown)}")
    surface = doc.get("surface")
    if not isinstance(surface, str) or surface not in OVERLAYABLE_SURFACES:
        raise ValueError(
            f'"surface" must be one of: {", ".join(sorted(OVERLAYABLE_SURFACES))} '
            f"(got {surface!r})"
        )
    body = doc.get("body", "")
    if not isinstance(body, str):
        raise ValueError('"body" must be a string holding the genui DSL')
    title = doc.get("title", "")
    if not isinstance(title, str):
        raise ValueError('"title" must be a string')
    define = _parse_define(doc.get("define", []))
    if not body.strip() and not define:
        raise ValueError('an overlay must contribute something — "body" or "define"')
    return SurfaceOverlay(file=name, surface=surface, title=title, body=body, define=define)


def load_overlays() -> tuple[list[SurfaceOverlay], list[OverlayRefusal]]:
    """Every ``*.json`` overlay in ``surfaces/``, plus a named refusal for each that failed.

    Never raises: a missing directory is the common case (no overlays) and one unreadable
    file must not hide the others. Ordering is by file name so the rendered band is stable
    across reads.
    """
    root = surfaces_dir()
    accepted: list[SurfaceOverlay] = []
    refused: list[OverlayRefusal] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except (OSError, NotADirectoryError):
        return accepted, refused
    for entry in entries:
        if not entry.name.endswith(".json"):
            continue
        try:
            # The containment gate runs on the NAME, so the scan and a by-name read share
            # one rule. A symlink inside surfaces/ pointing out resolves out and is refused.
            real = resolve_overlay_path(entry.name)
        except ValueError as exc:
            refused.append(
                OverlayRefusal(
                    entry.name,
                    _code_path(
                        str(exc),
                        fix=(
                            f"Move the overlay's real file inside {root} and remove the "
                            "link that points away from it."
                        ),
                    ),
                )
            )
            continue
        if not real.is_file():
            refused.append(
                OverlayRefusal(
                    entry.name,
                    _code_path(
                        f"{entry.name!r} is not a regular file",
                        fix="An overlay is a single .json file; remove or rename this entry.",
                    ),
                )
            )
            continue
        try:
            size = real.stat().st_size
        except OSError as exc:
            refused.append(
                OverlayRefusal(
                    entry.name,
                    _code_path(
                        f"{entry.name!r} could not be read: {exc}",
                        fix="Check the file's permissions, then reload the dashboard.",
                    ),
                )
            )
            continue
        if size > MAX_OVERLAY_BYTES:
            refused.append(
                OverlayRefusal(
                    entry.name,
                    _code_invalid(
                        f"{entry.name!r} is {size} bytes, over the {MAX_OVERLAY_BYTES}-byte "
                        "overlay ceiling",
                        fix="Split the overlay, or move the data it embeds into an artifact.",
                    ),
                )
            )
            continue
        try:
            text = real.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            refused.append(
                OverlayRefusal(
                    entry.name,
                    _code_invalid(
                        f"{entry.name!r} is not readable UTF-8 text: {exc}",
                        fix="Rewrite the overlay as UTF-8 JSON.",
                    ),
                )
            )
            continue
        try:
            accepted.append(_parse_overlay(entry.name, text))
        except ValueError as exc:
            refused.append(
                OverlayRefusal(
                    entry.name,
                    _code_invalid(
                        f"{entry.name!r} {exc}",
                        fix=(
                            'An overlay is {"surface", "title"?, "body"?, "define"?} — '
                            "fix the named key and reload the dashboard."
                        ),
                    ),
                )
            )
    return accepted, refused


def overlay_payload() -> dict[str, Any]:
    """The wire shape ``GET /api/surfaces/overlays`` returns (accepted + refusals)."""
    accepted, refused = load_overlays()
    return {
        "overlays": [o.to_dict() for o in accepted],
        "refusals": [r.to_dict() for r in refused],
        "dir": str(surfaces_dir()),
    }

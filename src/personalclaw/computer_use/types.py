"""The platform-neutral vocabulary every accessibility driver speaks (`DCU-3`, §2).

One element shape, one fingerprint definition, one error envelope — shared by the macOS driver
today and by the Windows/Linux drivers `DCU-6` will add. The alternative, each driver inventing
its own dictionary keys, would put the dispatch's secure-field screen
(:func:`personalclaw.computer_use.policy.check_input_target`) at the mercy of whichever driver
answered: that screen reads ``role``/``subrole``/label/value keys off the element, so a driver
that spelled them differently would be screened against nothing and would pass.

**Every text field is a ``str``, never ``None``.** ``check_input_target`` refuses a screened key
whose value is not a string ("a driver that hands a list where a role goes has produced a target
this build does not understand"), so an absent title must serialise as ``""`` rather than as
``null``. :meth:`Element.to_dict` is the single place that guarantees it, which is why drivers
build :class:`Element` instances instead of assembling dictionaries by hand.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

#: The most elements one window walk will report. A ceiling on both the walk's cost and the
#: size of what a model reads; a window exposing more is truncated and says so. The ceilinged
#: spawn bounds a runaway walk from the outside (§3.5), this bounds it from the inside.
MAX_ELEMENTS = 500

#: How deep a tree walk descends. Accessibility trees on real applications are shallow; a depth
#: this large means either a pathological UI or a cycle, and stopping is the honest answer.
MAX_DEPTH = 25


@dataclass(frozen=True)
class Element:
    """One addressable element of a walked window, at a stable index.

    ``frame`` is screen coordinates — it exists for the located-coordinate path §2 reserves for
    canvas/custom-drawn UI, and it participates in the fingerprint so a moved window
    invalidates the indices taken from it.
    """

    index: int
    role: str = ""
    subrole: str = ""
    title: str = ""
    value: str = ""
    placeholder: str = ""
    description: str = ""
    help: str = ""
    enabled: bool = True
    frame: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    actions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """The wire form the dispatch screens and the model reads.

        The plain spellings (``role``, not ``AXRole``) are deliberate: ``policy`` reads both,
        and the plain ones are the ones a Windows or Linux driver can also produce honestly.
        """
        x, y, width, height = self.frame
        return {
            "index": self.index,
            "role": self.role,
            "subrole": self.subrole,
            "title": self.title,
            "value": self.value,
            "placeholder": self.placeholder,
            "description": self.description,
            "help": self.help,
            "enabled": self.enabled,
            "frame": {"x": x, "y": y, "width": width, "height": height},
            "actions": list(self.actions),
        }


@dataclass
class WindowWalk:
    """The result of one window walk: the elements, plus the driver's opaque handles.

    ``handles`` is parallel to ``elements`` and is **never interpreted** outside the FFI module
    that produced it — the driver's op layer indexes it and hands the entry straight back. That
    is what keeps every OS type inside one auditable module
    (``test_the_macos_driver_holds_no_ctypes`` asserts the driver imports no ``ctypes``).
    """

    elements: list[Element] = field(default_factory=list)
    handles: list[Any] = field(default_factory=list)
    truncated: bool = False


def fingerprint_of(elements: list[Element]) -> str:
    """The structural summary a stale index is caught by.

    **Structure, not content.** Role, subrole, title, enabled-ness, geometry, advertised
    actions and the element count go in; the element's ``value`` does NOT. That split is the
    whole usefulness of the check: including ``value`` would mean typing one character into a
    field invalidated the very snapshot the model is working through, so every second
    ``computer_type`` would refuse and the honest fix would be to stop checking. Excluding it
    means the fingerprint answers the question §2 actually asks — *has this window moved or
    changed shape since it was walked* — for which a button's title flipping ``Play``→``Pause``
    and a window being dragged both count, and a user finishing a sentence does not.

    Digested rather than stored raw so the comparison is a fixed-size string equality, and so
    the dispatch (which holds it across calls) never holds window text it has no use for.
    """
    summary = [
        [
            element.role,
            element.subrole,
            element.title,
            element.enabled,
            [round(coordinate, 1) for coordinate in element.frame],
            list(element.actions),
        ]
        for element in elements
    ]
    payload = json.dumps([len(elements), summary], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class DriverError:
    """A driver failure as a typed VALUE, with :meth:`to_dict` as its only wire form.

    A typed object rather than a module-level ``def error_envelope(...) -> dict`` on purpose.
    This codebase has repeatedly re-derived the ``{"error": {"code", ...}}`` wire shape as a
    tiny local helper — PL-8 deleted thirteen such clones and the structural-duplication ratchet
    counts the survivors — and each clone is a place the envelope drifts silently, because every
    caller's test asserts against its own copy. Making the shape a value with ONE serialiser
    gives the driver layer what :class:`~personalclaw.errors.AgentError` gives the agent layer:
    somewhere for the fields to be named once. The dispatch turns this into an ``AgentError``;
    the two envelopes stay deliberately separate (the two-error-envelope ruling), and this is a
    third, narrower one — the stdio protocol between the gateway and its ceilinged child.

    WHAT/WHY/FIX are all required. A driver never reports failure as a falsy or empty result: to
    a model, an empty answer reads as "the click landed and there was nothing to report".
    """

    code: str
    message: str
    why: str
    fix: str

    def to_dict(self) -> dict[str, Any]:
        """The envelope :mod:`~personalclaw.computer_use.driver_host` forwards on stdout."""
        return {
            "error": {"code": self.code, "message": self.message, "why": self.why, "fix": self.fix}
        }


class DriverRefusal(Exception):
    """A refusal the driver can state precisely, carrying its typed :class:`DriverError`.

    Raised inside the op layer and serialised at the op boundary, so the deep code that
    discovers a stale fingerprint does not have to thread a return value out through every
    caller.
    """

    def __init__(self, error: DriverError) -> None:
        self.error = error
        super().__init__(error.message)

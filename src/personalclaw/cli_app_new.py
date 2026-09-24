"""``personalclaw app new`` — scaffold a third-party app, typed from the live registry.

Two halves, and the split is the point:

* **The type table is DERIVED, never listed here.** ``--list-types`` asks the running
  process what provider types exist (``manifest._providers_section`` → ``PROVIDER_TYPES``
  + ``providers.registry``), so an upstream capability type added to core shows up in the
  table with no edit to this generator. A hand-kept copy of that list is exactly the drift
  this repo keeps finding: a scaffold that can only make yesterday's app types.
* **The provider stub is DERIVED from the SDK contract.** For a type whose contract is
  published on the app boundary (``personalclaw.sdk.<type>``), the ABC and its abstract
  methods are introspected and the stub is generated from those real signatures. A type
  with no published SDK ABC (``agent``, ``task``, ``workflow``, ``duty_gate`` today) gets an
  honestly-labelled duck-typed stub instead of a fabricated import — apps import core ONLY
  via ``personalclaw.sdk.*``, so the generator refuses to teach a boundary violation.
  (``notification`` left that list with `TSE2-5`: ``sdk/notification.py`` now publishes
  ``NotificationDeliveryProvider``, so the stub is derived like any other contract's.)
* **The generated suite CALLS the type's conformance kit** when the type ships one
  (``channel`` today — see :data:`_CONFORMANCE_KITS`). A suite that asserts only the
  stub's shape reports ``7 passed`` while saying nothing about the contract core resolves
  the app through, which is exactly how a freshly scaffolded channel app used to sail past
  the two vendor seams it was missing (issue #2577). The kit call ships with a negative
  test beside it, because a kit call that can never fail is the same silence relocated.

Generated output is MIT-licensed, carries no credentials or placeholder secrets, and is
validated against the REAL manifest validator (:meth:`AppManifest.validate`) before
``scaffold`` returns — a generator that can emit an invalid app.json is a generator that
ships broken apps.

``--from-template`` is the third path: fetch the published template repo instead of
generating. It is the one part of this module that touches the network, so it is written
as a hostile-input surface — see :func:`from_template` and the refusals around it.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import inspect
import io
import json
import re
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

# The single scaffolded app version + the files every type emits. Kept here so the
# conformance test can assert the file set without re-deriving it.
SCAFFOLD_VERSION = "0.1.0"
SCAFFOLD_FILES = (
    "app.json",
    "provider.py",
    "app_cli.py",
    "test_provider.py",
    "README.md",
    "LICENSE",
)

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# A name-ish abstract property returns the app's own identity rather than "" — the
# per-type registries key providers by `.name`/`.source_name`/`.delivery_name`, so an empty
# string there registers the provider under a key nothing can resolve.
#
# `delivery_name` joins for exactly the reason `source_name` is here (`TSE2-5`): this is a
# CLOSED vocabulary matched by exact name, so a contract whose identity property is spelled
# any other way falls through to `_body_for`, whose neutral `-> str` return is `return ""`.
# Measured: `test_app_scaffold[notification]` refused the generated provider with
# *"must expose a non-empty delivery_name"* — the generator produced a stub the handler
# rejects, which looks like an app bug and is a generator bug.
_NAME_PROPERTIES = ("name", "source_name", "provider_name", "delivery_name")
_DISPLAY_PROPERTIES = ("display_name", "displayName")

# (member name, is async, signature, one-line doc) for a type whose runtime handler
# REFUSES to register a provider missing that member, but which publishes no ABC on the
# app boundary to derive it from. Read off the handler's own refusal — DutyGateTypeHandler
# raises "must expose an async on_duty(now, ctx)" — not invented here. This is NOT a second
# type list: a type absent from this table still scaffolds and still appears in
# ``--list-types``; the table only adds members a handler would otherwise reject.
_DUCK_CONTRACT_MEMBERS: dict[str, tuple[tuple[str, bool, str, str], ...]] = {
    "duty_gate": (
        (
            "on_duty",
            True,
            "(self, now, ctx)",
            "Is the user on duty right now? Return a DutyVerdict.",
        ),
    ),
}


# ---------------------------------------------------------------------------
# The derived type table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeContract:
    """The provider contract the generator found for one provider type.

    ``sdk_module``/``abc_name`` are empty when the type publishes no ABC on the app
    boundary; ``methods`` is then empty too and the stub is duck-typed.
    """

    type: str
    sdk_module: str = ""
    abc_name: str = ""
    methods: tuple[str, ...] = ()

    @property
    def has_abc(self) -> bool:
        return bool(self.sdk_module and self.abc_name)

    @property
    def label(self) -> str:
        """How the contract renders in the ``--list-types`` table."""
        if not self.has_abc:
            return "-  (duck-typed stub)"
        return f"{self.sdk_module}:{self.abc_name}"


@dataclass
class TypeRow:
    """One row of the ``--list-types`` table."""

    type: str
    contract: TypeContract
    installed: int = 0


def provider_types() -> list[str]:
    """Every provider type this build accepts, from the runtime registry.

    Reuses ``manifest._providers_section`` — the same derivation the agent manifest and
    the generated ``reference/providers.md`` publish — so the scaffold, the manifest and
    the docs can never disagree about what types exist.
    """
    from personalclaw.manifest import _providers_section

    section = _providers_section()
    return [str(t) for t in section.get("types", [])]


def _installed_counts() -> dict[str, int]:
    """How many providers of each type are registered in THIS process."""
    from personalclaw.manifest import _providers_section

    counts: dict[str, int] = {}
    for entry in _providers_section().get("registered", []):
        counts[str(entry.get("type", ""))] = counts.get(str(entry.get("type", "")), 0) + 1
    return counts


def _camel(type_name: str) -> str:
    return "".join(part.capitalize() for part in type_name.split("_") if part)


def _sdk_module_candidates(type_name: str) -> list[str]:
    """SDK module names that could publish this type's contract, best first."""
    cands = [type_name, type_name.replace("_", "")]
    if type_name.endswith("s"):
        cands.append(type_name[:-1])
    else:
        # …and the PLURAL, the mirror of the rule above: `trigger` publishes its contract in
        # `personalclaw.sdk.triggers` (TSE-4), which the singular-only ladder could not see, so the
        # generator emitted a duck-typed stub whose provider `TriggerTypeHandler` then refused to
        # register. `sdk/triggers.py` is the only plural module on the boundary, so this adds one
        # resolution and changes none.
        cands.append(f"{type_name}s")
    out: list[str] = []
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out


def _abstract_methods(cls: type) -> frozenset[str]:
    """``__abstractmethods__`` as a plain set (it is not on ``type``'s public surface)."""
    return frozenset(str(m) for m in getattr(cls, "__abstractmethods__", ()))


def _abcs_in(module: Any) -> list[tuple[str, type]]:
    """Abstract classes a module exports (``__all__`` when it declares one)."""
    exported = set(getattr(module, "__all__", ()) or ())
    found: list[tuple[str, type]] = []
    for name, obj in vars(module).items():
        if name.startswith("_") or not inspect.isclass(obj):
            continue
        if not getattr(obj, "__abstractmethods__", None):
            continue
        if exported and name not in exported:
            continue
        found.append((name, obj))
    return found


def resolve_contract(type_name: str) -> TypeContract:
    """Find the SDK ABC for ``type_name``, or an empty contract when none is published.

    The ladder is deliberate: an exact ``<Type>Provider`` wins, then a ``*Provider``
    carrying the type's token (``channel`` → ``ChannelTransportProvider``), then a module
    that publishes exactly one ABC (``inbox`` → ``MessageSourceProvider``). Anything
    ambiguous resolves to no contract rather than a guess.
    """
    camel = _camel(type_name)
    for mod_name in _sdk_module_candidates(type_name):
        try:
            module = importlib.import_module(f"personalclaw.sdk.{mod_name}")
        except ImportError:
            continue
        candidates = _abcs_in(module)
        if not candidates:
            continue
        exact = [c for c in candidates if c[0] == f"{camel}Provider"]
        tokened = [c for c in candidates if camel in c[0] and c[0].endswith("Provider")]
        chosen: tuple[str, type] | None = None
        if exact:
            chosen = exact[0]
        elif tokened:
            chosen = tokened[0]
        elif len(candidates) == 1:
            chosen = candidates[0]
        if chosen is None:
            continue
        abc_name, abc_obj = chosen
        methods = tuple(sorted(_abstract_methods(abc_obj)))
        return TypeContract(
            type=type_name,
            sdk_module=f"personalclaw.sdk.{mod_name}",
            abc_name=abc_name,
            methods=methods,
        )
    return TypeContract(
        type=type_name,
        methods=tuple(m[0] for m in _DUCK_CONTRACT_MEMBERS.get(type_name, ())),
    )


def provider_type_rows() -> list[TypeRow]:
    """The full ``--list-types`` table, derived at call time."""
    counts = _installed_counts()
    return [
        TypeRow(type=t, contract=resolve_contract(t), installed=counts.get(t, 0))
        for t in provider_types()
    ]


def render_type_table(rows: list[TypeRow]) -> str:
    """Render the derived table as the CLI prints it."""
    head_type, head_contract, head_installed = "TYPE", "SDK CONTRACT (app boundary)", "REGISTERED"
    w_type = max(len(head_type), *(len(r.type) for r in rows)) if rows else len(head_type)
    w_contract = (
        max(len(head_contract), *(len(r.contract.label) for r in rows))
        if rows
        else len(head_contract)
    )
    lines = [
        f"Provider types ({len(rows)}) — derived at runtime from the provider registry",
        "",
        f"{head_type:<{w_type}}  {head_contract:<{w_contract}}  {head_installed}",
        f"{'-' * w_type}  {'-' * w_contract}  {'-' * len(head_installed)}",
    ]
    for row in rows:
        lines.append(f"{row.type:<{w_type}}  {row.contract.label:<{w_contract}}  {row.installed}")
    lines += [
        "",
        "A type with no SDK contract gets a duck-typed stub: its entity is owned outside",
        "the app boundary today, and apps import core only via personalclaw.sdk.*.",
        "",
        "Scaffold one:  personalclaw app new my-app --type " + (rows[0].type if rows else "tool"),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stub generation
# ---------------------------------------------------------------------------


def _render_default(param: inspect.Parameter) -> inspect.Parameter:
    """Keep a default only when it round-trips as a literal.

    A default that reprs to an object address (``<object at 0x…>``) would generate
    code that does not parse, so it degrades to ``None``.
    """
    if param.default is inspect.Parameter.empty:
        return param.replace(annotation=inspect.Parameter.empty)
    default = param.default
    keep: Any = None
    if isinstance(default, (str, int, float, bool, bytes, type(None))):
        keep = default
    elif isinstance(default, (tuple, frozenset)) and not default:
        keep = default
    return param.replace(annotation=inspect.Parameter.empty, default=keep)


def _signature_text(func: Callable[..., Any]) -> str:
    """``(self, query, *, depth='balanced')`` — annotations stripped so the stub parses."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):  # pragma: no cover - builtins have no signature
        return "(self, *args, **kwargs)"
    params = [_render_default(p) for p in sig.parameters.values()]
    if not params or params[0].name != "self":
        params = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD), *params]
    return str(sig.replace(parameters=params, return_annotation=inspect.Signature.empty))


_NEUTRAL_RETURNS: tuple[tuple[str, str], ...] = (
    ("bool", "return False"),
    ("str", 'return ""'),
    ("int", "return 0"),
    ("float", "return 0.0"),
    ("list", "return []"),
    ("dict", "return {}"),
    ("tuple", "return ()"),
    ("set", "return set()"),
    ("none", "return None"),
)


def _annotation_text(func: Callable[..., Any]) -> str:
    try:
        ann = inspect.signature(func).return_annotation
    except (TypeError, ValueError):  # pragma: no cover
        return ""
    if ann is inspect.Signature.empty:
        return ""
    return ann if isinstance(ann, str) else getattr(ann, "__name__", str(ann))


def _body_for(name: str, func: Callable[..., Any], abc_name: str) -> list[str]:
    """The stub body: neutral for a plainly-typed return, explicit refusal otherwise."""
    ann = _annotation_text(func).strip().lower()
    is_gen = "iterator" in ann or "generator" in ann
    if not is_gen:
        for token, stmt in _NEUTRAL_RETURNS:
            if ann == token or ann.startswith(token + "["):
                return [stmt]
    lines = [
        f'raise NotImplementedError("{name}: implement this against {abc_name}")',
    ]
    if is_gen:
        # A `yield` after the raise keeps this an async generator, which is what the
        # contract's return annotation promises the caller.
        lines.append("yield  # pragma: no cover - keeps this an async generator")
    return lines


@dataclass
class _Member:
    """One generated member of the provider stub."""

    name: str
    is_property: bool
    is_async: bool
    signature: str
    body: list[str]
    doc: str = ""


def _members_for(contract: TypeContract, *, app_name: str, display_name: str) -> list[_Member]:
    """Every member the stub must define, derived from the contract."""
    members: list[_Member] = []
    if not contract.has_abc:
        members = [
            _Member(
                name=name,
                is_property=False,
                is_async=is_async,
                signature=signature,
                body=[f'raise NotImplementedError("{name}: core requires this member")'],
                doc=doc,
            )
            for name, is_async, signature, doc in _DUCK_CONTRACT_MEMBERS.get(contract.type, ())
        ]
        return _with_identity(members, app_name=app_name, display_name=display_name)
    module = importlib.import_module(contract.sdk_module)
    abc_obj = getattr(module, contract.abc_name)
    for name in contract.methods:
        attr = inspect.getattr_static(abc_obj, name)
        is_property = isinstance(attr, property)
        func = attr.fget if is_property and attr.fget is not None else attr
        if not callable(func):  # pragma: no cover - abstract attributes are callables
            continue
        if is_property and name in _NAME_PROPERTIES:
            body = [f'return "{app_name}"']
        elif is_property and name in _DISPLAY_PROPERTIES:
            body = [f'return "{display_name}"']
        else:
            body = _body_for(name, func, contract.abc_name)
        members.append(
            _Member(
                name=name,
                is_property=is_property,
                is_async=inspect.iscoroutinefunction(func)
                or inspect.isasyncgenfunction(func)
                or "asynciterator" in _annotation_text(func).replace(" ", "").lower(),
                signature=_signature_text(func),
                body=body,
                doc=(
                    (inspect.getdoc(func) or "").strip().splitlines()[0]
                    if inspect.getdoc(func)
                    else ""
                ),
            )
        )
    return _with_identity(members, app_name=app_name, display_name=display_name)


def _with_identity(members: list[_Member], *, app_name: str, display_name: str) -> list[_Member]:
    """Guarantee the identity pair every registry + Settings surface reads."""
    have = {m.name for m in members}
    # Every per-type registry keys a provider by `.name`; the Settings surfaces read
    # `.display_name`. A contract that declares only a variant (``inbox`` declares
    # ``source_name``) still needs the canonical pair, so the check is on the exact name.
    if "name" not in have:
        members.insert(
            0,
            _Member(
                name="name",
                is_property=True,
                is_async=False,
                signature="(self)",
                body=[f'return "{app_name}"'],
                doc="The key this provider registers under.",
            ),
        )
    if "display_name" not in have:
        members.insert(
            1,
            _Member(
                name="display_name",
                is_property=True,
                is_async=False,
                signature="(self)",
                body=[f'return "{display_name}"'],
                doc="How the provider is labelled in the dashboard.",
            ),
        )
    return members


def _render_member(member: _Member) -> str:
    lines: list[str] = []
    if member.is_property:
        lines.append("    @property")
    prefix = "    async def" if member.is_async else "    def"
    lines.append(f"{prefix} {member.name}{member.signature}:")
    if member.doc:
        lines.append(f'        """{member.doc}"""')
    for stmt in member.body:
        lines.append(f"        {stmt}")
    return "\n".join(lines)


def _class_name(app_name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[-_]", app_name) if part) + "Provider"


def _logger_root(app_name: str) -> str:
    return app_name.replace("-", "_")


# ---------------------------------------------------------------------------
# Conformance kits — the generated suite has to CALL one
# ---------------------------------------------------------------------------
#
# A scaffolded bundle's suite used to assert only the stub's SHAPE: the factory, the
# identity pair, no leftover abstract methods, settings reaching the provider. Seven
# tests, ``7 passed``, and not one word about the contract core actually resolves the app
# through. Measured on ``--type channel`` (issue #2577): the generated app declares one of
# the four channel seams, ``assert_channel_contract`` appeared ZERO times in the bundle,
# and the author was told nothing — a green suite with no signal, which is worse than a
# warning because it reads as confirmation.
#
# So a type whose contract ships an executable conformance kit gets that kit CALLED from
# the generated ``test_provider.py``, with a negative test beside it. The negative test is
# not decoration: a kit call that cannot fail is the same silence with a new location, and
# every shipped channel app's own suite carries the same guard.
#
# This table is explicit rather than derived, unlike the type table and the provider stub.
# A kit's CALL is not introspectable from the ABC: its keyword arguments, and what a
# deliberately non-conformant provider of that type looks like, are contract knowledge, not
# signature knowledge. What IS checked mechanically is that the names still resolve on the
# app boundary — :func:`resolve_kit` imports them, so a kit core renames or moves fails
# ``app new`` loudly instead of generating an import the author discovers.


@dataclass(frozen=True)
class ConformanceKit:
    """The executable contract a generated bundle of one provider type must call."""

    type: str
    #: The SDK facade that re-exports the kit — apps import core ONLY via ``personalclaw.sdk.*``.
    sdk_module: str
    #: The entry point the generated test calls.
    assert_fn: str
    #: The exception it raises, which the generated negative test expects.
    error_name: str
    #: Extra SDK names the generated tests need (to build a non-conformant provider).
    extra_names: tuple[str, ...] = ()

    @property
    def imported_names(self) -> tuple[str, ...]:
        return (self.assert_fn, self.error_name, *self.extra_names)


_CONFORMANCE_KITS: dict[str, ConformanceKit] = {
    "channel": ConformanceKit(
        type="channel",
        sdk_module="personalclaw.sdk.channel",
        assert_fn="assert_channel_contract",
        error_name="ChannelContractError",
        # The negative test declares `inbound=True` on a provider with no inbound path,
        # which is the kit's own honesty clause and the mistake a channel author makes.
        extra_names=("ChannelCapabilities",),
    ),
}


def resolve_kit(type_name: str) -> ConformanceKit | None:
    """The conformance kit for ``type_name``, proven importable, or ``None``.

    ``None`` means this type ships no kit — most types do not, and that is a fact about
    core, not a defect in the scaffold. A kit that is NAMED but no longer resolvable is a
    different thing entirely: it would generate a bundle whose suite fails on an
    ImportError the author has no way to fix, so it raises here (the same posture as the
    generated-manifest validation in :func:`scaffold`).
    """
    kit = _CONFORMANCE_KITS.get(type_name)
    if kit is None:
        return None
    try:
        module = importlib.import_module(kit.sdk_module)
    except ImportError as exc:  # pragma: no cover - a missing SDK facade breaks far more
        raise ScaffoldError(
            f"the {type_name} conformance kit names {kit.sdk_module}, which does not "
            f"import ({exc}) — this is a scaffold bug, please report it"
        ) from exc
    missing = [name for name in kit.imported_names if not hasattr(module, name)]
    if missing:
        raise ScaffoldError(
            f"the {type_name} conformance kit names {missing} on {kit.sdk_module}, which "
            "does not export them — this is a scaffold bug, please report it"
        )
    return kit


# ---------------------------------------------------------------------------
# File templates
# ---------------------------------------------------------------------------


#: The copyright holder written when ``--author`` was not given. A copyright line names a
#: PERSON OR AN ORGANISATION; an app's display name is an artefact and holds nothing, so the
#: scaffold must not quietly invent one from it. The previous fallback did exactly that and
#: shipped `Copyright (c) 2026 Channel Null` to four published exemplar repositories — a
#: grant naming a thing that cannot own a copyright, which is a defective licence rather
#: than a cosmetic blemish. An obviously-unfilled placeholder is the honest alternative: it
#: cannot be mistaken for a real holder, and `app_cmd` says so out loud. Angle brackets are
#: the MIT template's own convention for a field the licensor must complete.
_UNFILLED_COPYRIGHT_HOLDER = "<your name>"


def _license_text(holder: str, year: int) -> str:
    """The MIT grant, verbatim, with *holder* on the copyright line.

    *holder* must be a person or an organisation — never an app name, display name or any
    other artefact. Callers that have no author pass :data:`_UNFILLED_COPYRIGHT_HOLDER`.
    """
    return f"""MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def _manifest_dict(
    *,
    name: str,
    type_name: str,
    display_name: str,
    description: str,
    author: str,
) -> dict[str, Any]:
    """The generated ``app.json``, minimum-permission by construction.

    No ``permissions`` block at all: the Store shows declared permissions as the
    install-consent surface, so a scaffold that pre-declares network or filesystem
    access would train every new app to over-ask.
    """
    manifest: dict[str, Any] = {
        "name": name,
        "version": SCAFFOLD_VERSION,
        "displayName": display_name,
        "description": description,
        "icon": "Blocks",
        "license": "MIT",
        "tags": [type_name],
        # Plan 32 CLI seams — a setup step and a doctor probe, both in app_cli.py.
        "cli": {"setup": "app_cli:setup", "doctor": "app_cli:doctor"},
        # Plan 32 logger roots — read WITHOUT importing app code, so core log setup can
        # set this app's level from Settings.
        "loggerRoots": [_logger_root(name)],
        "provider": {
            "type": type_name,
            "implementation": "provider:create_provider",
            "settingsSchema": {
                "type": "object",
                "properties": {
                    "timeout_secs": {
                        "type": "integer",
                        "default": 20,
                        "x-meta": {
                            "label": "Request timeout",
                            "help": "Seconds this provider waits before giving up.",
                        },
                    }
                },
            },
        },
    }
    if author:
        manifest["author"] = author
    return manifest


def _render_provider_py(
    contract: TypeContract, *, app_name: str, display_name: str, class_name: str
) -> str:
    members = _members_for(contract, app_name=app_name, display_name=display_name)
    if contract.has_abc:
        header = f'"""The {app_name} {contract.type} provider.\n\n'
        header += (
            f"Implements {contract.abc_name} from {contract.sdk_module} — the contract core\n"
            "resolves this app through. Every method below is a stub: fill them in, and keep\n"
            "imports on the SDK surface (personalclaw.sdk.*), never a core internal.\n"
        )
        header += '"""\n\n'
        imports = f"from {contract.sdk_module} import {contract.abc_name}\n"
        bases = f"({contract.abc_name})"
    else:
        header = f'"""The {app_name} {contract.type} provider.\n\n'
        header += (
            f"The {contract.type} entity publishes no ABC on the app boundary yet, so this is\n"
            "a duck-typed stub: core resolves it by attribute, not by subclass. Keep imports\n"
            "on the SDK surface (personalclaw.sdk.*), never a core internal.\n"
        )
        header += '"""\n\n'
        imports = ""
        bases = ""
    body = "\n\n".join(_render_member(m) for m in members)
    return f"""{header}from __future__ import annotations

import logging
from typing import Any

{imports}
logger = logging.getLogger("{_logger_root(app_name)}")


class {class_name}{bases}:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = dict(config or {{}})
        self._timeout = int(self._config.get("timeout_secs", 20))

{body}


def create_provider(config: dict[str, Any] | None = None) -> {class_name}:
    \"\"\"Manifest factory — core calls this with this app's saved settings.\"\"\"
    return {class_name}(config)
"""


def _render_app_cli_py(*, app_name: str, display_name: str) -> str:
    return f'''"""CLI seams for {app_name} (plan 32): a setup step and a doctor probe.

``personalclaw setup`` calls :func:`setup` after the core steps; ``personalclaw doctor``
calls :func:`doctor` and renders the lines it returns as this app's section.
"""

from __future__ import annotations

from personalclaw.sdk.cli import DoctorLine, SetupContext


def setup(ctx: SetupContext) -> None:
    """Run this app's interactive setup step. Collect what the provider needs here."""
    ctx.print("{display_name}: nothing to configure yet.")


def doctor() -> list[DoctorLine]:
    """Report this app's health to ``personalclaw doctor``."""
    return [
        DoctorLine(
            label="{display_name}",
            status="ok",
            detail="provider stub installed — no checks declared yet",
        )
    ]
'''


def _render_channel_kit_tests(kit: ConformanceKit, *, class_name: str) -> str:
    """The ``channel`` kit call + the negative test that proves it can fail.

    The break chosen for the negative test is the kit's own inbound-honesty clause: a
    transport that declares ``inbound=True`` while having no inbound path at all. That is
    a real channel-author mistake (the flag and the receive loop live in different places),
    it names a clause rather than a typo, and it cannot start passing by accident.
    """
    return f'''

# ── Core's conformance kit: the ONE executable statement of the channel contract ──
#
# `{kit.assert_fn}` drives the clauses every channel shares — identity + info(),
# capability-dict completeness, connect/send echo shapes, health/test agreement, the
# unpaired-DM pairing flow (canned reply + exactly one deduped owner request), and
# non-owner group content entering a session FENCED. It raises `{kit.error_name}`
# naming the violated obligation, so a failure reads as an obligation, not an expression.
#
# It ALSO advises: watch pytest's warnings summary. A scaffolded channel app declares one
# seam of four, so the kit warns that this bundle registers no `inbox` message source and
# no `trigger_source`. Those warnings are your checklist, not a failure — one vendor app
# owns every seam that vendor touches. Add them to app.json's `providers` array, or record
# the exemption with `no_inbox_source_reason=` / `no_trigger_source_reason=`.
#
# Grow the call as you fill the stub in:
#   * declare `inbound=True` in capabilities() and pass `inbound_via="<your handler>"`;
#   * ship a ChannelDelivery and pass `delivery=`, `fake_backend=`, `min_edit_interval=`
#     and `clock=` to switch the delivery + streaming-throttle clauses on.
#
# See docs/guides/build-a-channel-app.md for the full checklist.


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway PERSONALCLAW_HOME, so no test here can touch the real one.

    The kit drives REAL core seams: its unknown-sender clause reads and writes the trust
    store, whose path resolves through `config_dir()` — i.e. `PERSONALCLAW_HOME`, re-read
    live on every call. Pointing that at a per-test tmp dir isolates the trust store, the
    app store and the credential store at once. Without it the clause asserting the
    DEFAULT 'pairing' policy would fail for anyone who has changed their own.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def test_meets_the_channel_contract(isolated_home: Path) -> None:
    {kit.assert_fn}(create_provider({{}}))


def test_the_conformance_kit_can_actually_fail(isolated_home: Path) -> None:
    """A kit call that can never fail is the same silence with a new location.

    So prove this one bites. Declaring `inbound=True` with no inbound path is the kit's
    inbound-honesty clause: nothing on the platform could route to such a transport.
    """

    class NoInboundPath({class_name}):
        def capabilities(self) -> ChannelCapabilities:
            return ChannelCapabilities(inbound=True)

    with pytest.raises({kit.error_name}):
        {kit.assert_fn}(NoInboundPath({{}}))
'''


#: Per-type renderer for the kit block appended to the generated ``test_provider.py``.
#: A kit in :data:`_CONFORMANCE_KITS` without a renderer here is a scaffold bug, caught by
#: :func:`resolve_kit`'s caller rather than by generating a file with a dangling call.
_KIT_RENDERERS: dict[str, Callable[..., str]] = {
    "channel": _render_channel_kit_tests,
}


def _render_test_provider_py(
    contract: TypeContract, *, app_name: str, display_name: str, class_name: str
) -> str:
    # Double quotes, not ``repr()``: generated code should already be black-clean, so an
    # apps repo (or this repo's own pre-commit hook, over staged-repos/app-template) reformatting
    # it cannot make the file differ from what the generator emits. Method names are Python
    # identifiers, so there is nothing to escape.
    method_list = ", ".join(f'"{m}"' for m in contract.methods)
    contract_note = (
        f"Contract: {contract.sdk_module}:{contract.abc_name}"
        if contract.has_abc
        else f"Contract: duck-typed ({contract.type} publishes no SDK ABC yet)"
    )
    kit = resolve_kit(contract.type)
    kit_note = ""
    kit_imports = ""
    kit_block = ""
    if kit is not None:
        render = _KIT_RENDERERS.get(kit.type)
        if render is None:  # pragma: no cover - guarded by test_app_scaffold
            raise ScaffoldError(
                f"the {kit.type} conformance kit has no generated-test renderer — this is "
                "a scaffold bug, please report it"
            )
        kit_note = (
            f"\nThe suite also calls `{kit.assert_fn}` — core's {contract.type} conformance\n"
            "kit, which is the contract the platform holds this provider to rather than the\n"
            "shape this file happens to assert. Read its advisories in pytest's warnings\n"
            "summary; the block at the bottom of this file explains them.\n"
        )
        # Trailing blank line included, so a type WITHOUT a kit renders byte-identically
        # to before this block existed. Exploded with a magic trailing comma: black keeps
        # that form, so the file it emits is the file black would emit.
        kit_imports = (
            "from pathlib import Path\n\nimport pytest\n\n"
            f"from {kit.sdk_module} import (\n"
            + "".join(f"    {name},\n" for name in sorted(kit.imported_names))
            + ")\n\n"
        )
        kit_block = render(kit, class_name=class_name)
    return f'''"""Stub-level contract tests for the {app_name} {contract.type} provider.

{contract_note}

These run with no network, no credentials and no gateway: they assert the provider
SHAPE core depends on, so a change that breaks registration fails here first. Add your
behaviour tests beside them as you fill the stub in.
{kit_note}"""

from __future__ import annotations

{kit_imports}from provider import {class_name}, create_provider

CONTRACT_METHODS = ({method_list}{"," if len(contract.methods) == 1 else ""})


def test_factory_returns_the_provider() -> None:
    assert isinstance(create_provider({{}}), {class_name})


def test_factory_accepts_no_config() -> None:
    assert isinstance(create_provider(None), {class_name})


def test_nothing_abstract_is_left() -> None:
    """An unimplemented abstract method makes the provider uninstantiable."""
    assert not getattr({class_name}, "__abstractmethods__", frozenset())


def test_registers_under_the_app_name() -> None:
    """Every per-type registry keys a provider by `.name`."""
    assert create_provider({{}}).name == "{app_name}"


def test_declares_its_display_name() -> None:
    assert create_provider({{}}).display_name == "{display_name}"


def test_every_contract_method_is_declared_on_the_stub() -> None:
    """Inherited-but-unimplemented is the drift this catches."""
    for name in CONTRACT_METHODS:
        assert name in vars({class_name}), f"{{name}} is not implemented on the stub"


def test_settings_reach_the_provider() -> None:
    assert create_provider({{"timeout_secs": 5}})._timeout == 5
{kit_block}'''


def _render_readme(
    contract: TypeContract, *, app_name: str, display_name: str, description: str
) -> str:
    contract_line = (
        f"`{contract.abc_name}` from `{contract.sdk_module}`"
        if contract.has_abc
        else f"a duck-typed `{contract.type}` provider (no SDK ABC published yet)"
    )
    methods = (
        "\n".join(f"- `{m}`" for m in contract.methods)
        if contract.methods
        else "- (no abstract methods — core resolves this provider by attribute)"
    )
    kit = resolve_kit(contract.type)
    # A README that does not mention the kit leaves the author reading a warnings summary
    # with no idea what produced it — the advisories ARE the missing-seam checklist.
    kit_section = (
        ""
        if kit is None
        else f"""
## The conformance kit

`test_provider.py` calls `{kit.assert_fn}` from `{kit.sdk_module}` — core's one
executable statement of the {contract.type} contract, and the same kit every shipped
{contract.type} app runs against. Two things to know on your first `pytest` run:

- It **advises** about the companion seams this scaffold does not declare. A vendor app
  owns every seam that vendor touches, so a {contract.type}-only bundle warns that it
  registers no `inbox` message source and no `trigger_source`. Read those warnings as the
  checklist they are: add the providers to `app.json`, or record why this vendor has no
  such semantics with `no_inbox_source_reason=` / `no_trigger_source_reason=`.
- The negative test beside the call is load-bearing. A kit call that cannot fail proves
  nothing, so keep one non-conformant case in the suite as you grow the provider.
"""
    )
    return f"""# {display_name}

{description}

A PersonalClaw **{contract.type}** app. It implements {contract_line}.

## What to fill in

`provider.py` holds one stub per contract member:

{methods}

Each stub either returns a neutral value or raises `NotImplementedError` — replace the
bodies, then extend `test_provider.py` with tests for what you wrote. Import core only
through `personalclaw.sdk.*`; a deep core import will break on the next release.
{kit_section}
## Run the tests

```bash
pytest {app_name}
```

## Install it

From the dashboard: **Store → Add source → local path**, point it at this directory,
then install and enable it. Or from a shell against a running gateway — the gateway takes
the owner token as a `?token=` query parameter (`personalclaw token` prints a URL
carrying it), not an `Authorization` header:

```bash
curl -X POST "$PERSONALCLAW_URL/api/apps?token=$PERSONALCLAW_TOKEN" \\
  -H 'Content-Type: application/json' \\
  -d '{{"source": "'"$PWD"'", "confirm": true}}'
curl -X POST "$PERSONALCLAW_URL/api/apps/{app_name}/enable?token=$PERSONALCLAW_TOKEN"
```

Enabling the app registers the provider; disabling it unregisters it. `personalclaw
doctor` shows this app's section from `app_cli.py`.

## Declare only what you need

`app.json` declares no permissions. Add a `permissions` block only for what the provider
actually uses — the Store shows those permissions as the install-consent surface, so an
over-broad declaration costs you installs.

## License

MIT — see `LICENSE`.
"""


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------


class ScaffoldError(Exception):
    """A refusal the CLI turns into a message + non-zero exit."""


@dataclass
class ScaffoldResult:
    path: Path
    type: str
    contract: TypeContract
    files: list[str] = field(default_factory=list)


def scaffold(
    name: str,
    type_name: str,
    *,
    dest: Path,
    display_name: str = "",
    description: str = "",
    author: str = "",
    force: bool = False,
    year: int | None = None,
) -> ScaffoldResult:
    """Write a complete, installable app for ``type_name`` into ``dest / name``.

    Validates its own output with :meth:`AppManifest.validate` before returning: a
    generator that can emit an app.json core would refuse is worse than no generator.
    """
    from personalclaw.apps.manifest import AppManifest

    if not _KEBAB_RE.match(name):
        raise ScaffoldError(
            f"app name must be kebab-case (lowercase alphanumeric + hyphens), got: {name!r}"
        )
    known = provider_types()
    if type_name not in known:
        raise ScaffoldError(
            f"unknown provider type: {type_name!r}\n"
            f"known types: {', '.join(known)}\n"
            "Run `personalclaw app new --list-types` for the full table."
        )
    target = Path(dest) / name
    if target.exists() and any(target.iterdir()) and not force:
        raise ScaffoldError(f"{target} already exists and is not empty (use --force to overwrite)")

    display = display_name or " ".join(p.capitalize() for p in name.split("-"))
    desc = description or f"A PersonalClaw {type_name} provider."
    contract = resolve_contract(type_name)
    class_name = _class_name(name)

    manifest_data = _manifest_dict(
        name=name,
        type_name=type_name,
        display_name=display,
        description=desc,
        author=author,
    )
    errors = AppManifest.from_dict(manifest_data).validate()
    if errors:
        raise ScaffoldError(
            "generated app.json is invalid — this is a scaffold bug, please report it:\n  "
            + "\n  ".join(errors)
        )

    files = {
        "app.json": json.dumps(manifest_data, indent=2) + "\n",
        "provider.py": _render_provider_py(
            contract, app_name=name, display_name=display, class_name=class_name
        ),
        "app_cli.py": _render_app_cli_py(app_name=name, display_name=display),
        "test_provider.py": _render_test_provider_py(
            contract, app_name=name, display_name=display, class_name=class_name
        ),
        "README.md": _render_readme(
            contract, app_name=name, display_name=display, description=desc
        ),
        "LICENSE": _license_text(
            author or _UNFILLED_COPYRIGHT_HOLDER, year or _dt.date.today().year
        ),
    }
    target.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        (target / rel).write_text(content, encoding="utf-8")
    return ScaffoldResult(path=target, type=type_name, contract=contract, files=sorted(files))


# ---------------------------------------------------------------------------
# --from-template: fetch the published template repo
# ---------------------------------------------------------------------------
#
# This is the only network surface in the module, and everything it consumes is
# attacker-shaped: the response body is a tar archive whose member names, member types
# and member sizes are all metadata a malicious host controls. So the rules here are
# allowlist-first and fail-closed, and each one has a named negative test in
# ``tests/test_app_from_template.py``:
#
#   * URL: https only, host on an allowlist, no userinfo, no non-default port — checked
#     BEFORE any socket is opened (``test_a_non_allowlisted_host_never_reaches_the_network``).
#   * Response: exactly 200, and NO redirect is followed at all. Not "no cross-host
#     redirect" — no redirect, so there is no host to re-check (the allowlisted host is
#     the host that answers).
#   * Members: regular files and directories only (a symlink, hardlink, device or fifo
#     member is refused), names relative with no ``..`` component, and every written path
#     re-verified for containment AFTER canonicalisation.
#   * Sizes: per-member and whole-archive byte caps, and a member count cap, enforced on
#     bytes actually READ — never on the size the archive claims.
#   * Target: refused when it exists and is non-empty (``--force`` overrides), and never
#     followed through a symlink.
#
# Nothing shells out: no ``git clone``, no ``curl``. The URL never reaches a shell.

TEMPLATE_REPO = "PersonalClaw/app-template"
TEMPLATE_REF = "main"
#: codeload serves the tarball directly. ``github.com/…/archive/….tar.gz`` would answer
#: 302 to this host, and we refuse redirects — so we ask the host that actually serves it.
TEMPLATE_ARCHIVE_URL = (
    f"https://codeload.github.com/{TEMPLATE_REPO}/tar.gz/refs/heads/{TEMPLATE_REF}"
)

#: Schemes and hosts ``--template-url`` may name. Allowlists, not "no localhost" denylists.
#: Both are read at call time so a test can point the transport at a local server; the
#: SHIPPED values are pinned by ``test_the_shipped_template_allowlists_are_narrow`` so a
#: widened default cannot reach a release.
TEMPLATE_SCHEMES = frozenset({"https"})
TEMPLATE_HOSTS = frozenset({"codeload.github.com"})

MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024
MAX_MEMBERS = 400
_FETCH_TIMEOUT_SECS = 30


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect rather than re-validating a new host mid-fetch."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str):
        raise ScaffoldError(
            f"refusing to follow a redirect ({code}) to {newurl} — the template host must "
            f"serve the archive directly"
        )


@dataclass
class TemplateResult:
    """What ``--from-template`` wrote, and where it came from."""

    path: Path
    source: str
    files: list[str] = field(default_factory=list)


def _validate_template_url(url: str) -> str:
    """Refuse anything but https-on-an-allowlisted-host, before opening a socket."""
    parts = urlsplit(url)
    if parts.scheme not in TEMPLATE_SCHEMES:
        raise ScaffoldError(
            f"template URL scheme {parts.scheme or '(none)'!r} is not allowed — "
            f"allowed: {', '.join(sorted(TEMPLATE_SCHEMES))}. "
            "Use --template-archive for a tarball already on disk."
        )
    if parts.username or parts.password:
        raise ScaffoldError("template URL must not carry credentials in the userinfo field")
    host = (parts.hostname or "").lower()
    if host not in TEMPLATE_HOSTS:
        raise ScaffoldError(
            f"template host {host or '(none)'!r} is not allowed — "
            f"allowed: {', '.join(sorted(TEMPLATE_HOSTS))}"
        )
    return url


def _read_capped(stream: Any, limit: int, what: str) -> bytes:
    """Read at most ``limit`` bytes, refusing the moment the stream exceeds it."""
    data = stream.read(limit + 1)
    if data is None:  # pragma: no cover - a stream that returns None is already broken
        return b""
    if len(data) > limit:
        raise ScaffoldError(f"{what} is larger than the {limit} byte cap — refusing")
    return bytes(data)


def fetch_template_archive(url: str) -> bytes:
    """GET ``url`` and return the tarball bytes. https + allowlist + no redirects + 200."""
    _validate_template_url(url)
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/gzip"})
    try:
        with opener.open(request, timeout=_FETCH_TIMEOUT_SECS) as response:
            status = int(getattr(response, "status", 0) or 0)
            if status != 200:
                raise ScaffoldError(f"template fetch returned HTTP {status} (expected 200)")
            return _read_capped(response, MAX_ARCHIVE_BYTES, "template archive")
    except urllib.error.HTTPError as exc:
        raise ScaffoldError(f"template fetch returned HTTP {exc.code} (expected 200)") from exc
    except urllib.error.URLError as exc:
        raise ScaffoldError(f"template fetch failed: {exc.reason}") from exc
    except OSError as exc:  # pragma: no cover - socket-level failures are environmental
        raise ScaffoldError(f"template fetch failed: {exc}") from exc


def _checked_member_name(name: str) -> str:
    """The member's path, or a refusal. Relative, no ``..``, no drive, no absolute root."""
    raw = name.replace("\\", "/")
    if not raw or raw in (".", "./"):
        return ""
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ScaffoldError(f"refusing archive member with an absolute path: {name!r}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ScaffoldError(f"refusing archive member that escapes the target: {name!r}")
    return "/".join(parts)


def _strip_root(names: list[str]) -> str:
    """The single top-level directory a GitHub tarball wraps everything in, if there is one."""
    roots = {n.split("/", 1)[0] for n in names if n}
    if len(roots) == 1 and any("/" in n for n in names):
        return roots.pop()
    return ""


def _prepare_target(dest: Path, name: str, *, force: bool) -> Path:
    """Resolve ``dest/name``, refusing a symlink or an existing non-empty directory."""
    base = Path(dest)
    target = base / name
    if target.is_symlink():
        raise ScaffoldError(f"{target} is a symlink — refusing to write through it")
    if target.exists():
        if not target.is_dir():
            raise ScaffoldError(f"{target} exists and is not a directory")
        if any(target.iterdir()) and not force:
            raise ScaffoldError(
                f"{target} already exists and is not empty (use --force to overwrite)"
            )
    return target


def extract_template_archive(data: bytes, *, target: Path, force: bool = False) -> list[str]:
    """Extract a template tarball into ``target``, writing only what the rules above allow.

    Members are written one at a time from their own file objects — not via
    ``extractall`` — so containment is re-verified after canonicalisation for every single
    path, and no tar feature (link, device, sparse, pax path override) can act on our
    behalf.
    """
    target = _prepare_target(target.parent, target.name, force=force)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        members = tar.getmembers()
        if len(members) > MAX_MEMBERS:
            raise ScaffoldError(f"template archive has {len(members)} members (cap {MAX_MEMBERS})")
        checked: list[tuple[tarfile.TarInfo, str]] = []
        for member in members:
            if not (member.isreg() or member.isdir()):
                kind = "symlink" if member.issym() else "hardlink" if member.islnk() else "special"
                raise ScaffoldError(
                    f"refusing {kind} archive member {member.name!r} — the template may "
                    "contain regular files and directories only"
                )
            rel = _checked_member_name(member.name)
            if rel:
                checked.append((member, rel))
        root = _strip_root([rel for _, rel in checked])

        target.mkdir(parents=True, exist_ok=True)
        anchor = target.resolve()
        written: list[str] = []
        budget = MAX_ARCHIVE_BYTES
        for member, rel in checked:
            inner = rel[len(root) + 1 :] if root and rel.startswith(root + "/") else rel
            if root and rel == root:
                continue
            if not inner:
                continue
            out = (anchor / inner).resolve()
            if out != anchor and anchor not in out.parents:
                # Distinct wording from the name-shape refusal above ON PURPOSE: these are
                # two independent layers, and a test asserting one message must not pass
                # because the other fired. This one is the backstop that survives a bad
                # name check — ``test_containment_refuses_even_if_the_name_check_is_bypassed``
                # bypasses the first layer to prove this layer is live on its own.
                raise ScaffoldError(
                    f"refusing archive member that resolves outside the target: {rel!r}"
                )
            if member.isdir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            source = tar.extractfile(member)
            if source is None:  # pragma: no cover - isreg() members always open
                continue
            payload = _read_capped(source, min(MAX_MEMBER_BYTES, budget), f"member {inner!r}")
            budget -= len(payload)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(payload)
            written.append(inner)
    if not written:
        raise ScaffoldError(
            "template archive contained no files — refusing to call this a template"
        )
    return sorted(written)


def from_template(
    *,
    dest: Path,
    url: str = "",
    archive: Path | None = None,
    force: bool = False,
    dir_name: str = "",
) -> TemplateResult:
    """Materialise the template repo into ``dest/<dir_name>``.

    ``archive`` reads a tarball already on disk (offline, and how the tests prove
    extraction without the org repo existing); otherwise ``url`` — default
    :data:`TEMPLATE_ARCHIVE_URL` — is fetched.
    """
    name = dir_name or TEMPLATE_REPO.split("/")[-1]
    if not _KEBAB_RE.match(name):
        raise ScaffoldError(f"template directory name must be kebab-case, got: {name!r}")
    target = Path(dest) / name
    if archive is not None:
        path = Path(archive)
        if not path.is_file():
            raise ScaffoldError(f"template archive not found: {path}")
        data = _read_capped(path.open("rb"), MAX_ARCHIVE_BYTES, f"template archive {path.name!r}")
        source = str(path)
    else:
        source = url or TEMPLATE_ARCHIVE_URL
        data = fetch_template_archive(source)
    try:
        files = extract_template_archive(data, target=target, force=force)
    except tarfile.TarError as exc:
        raise ScaffoldError(f"template archive is not a readable tarball: {exc}") from exc
    return TemplateResult(path=target, source=source, files=files)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def add_parser(sub: Any) -> None:
    """Wire ``personalclaw app new`` onto the top-level subparsers."""
    app_parser = sub.add_parser("app", help="Scaffold a new PersonalClaw app")
    app_sub = app_parser.add_subparsers(dest="app_cmd")
    new = app_sub.add_parser("new", help="Generate an installable app for a provider type")
    new.add_argument("name", nargs="?", help="App name, kebab-case (e.g. my-search)")
    new.add_argument("--type", dest="type", help="Provider type (see --list-types)")
    new.add_argument(
        "--list-types",
        action="store_true",
        help="Print the provider types this build accepts, derived from the registry",
    )
    new.add_argument("--dir", dest="dest", default=".", help="Directory to create the app in")
    new.add_argument("--display-name", dest="display_name", default="", help="Human-readable name")
    new.add_argument("--description", default="", help="One-line description")
    new.add_argument("--author", default="", help="Author (also the MIT copyright holder)")
    new.add_argument("--force", action="store_true", help="Overwrite a non-empty target directory")
    new.add_argument(
        "--from-template",
        dest="from_template",
        action="store_true",
        help=f"Fetch the {TEMPLATE_REPO} repo instead of generating (fork-and-go)",
    )
    new.add_argument(
        "--template-url",
        dest="template_url",
        default="",
        help=f"Override the template archive URL (https, host must be one of: "
        f"{', '.join(sorted(TEMPLATE_HOSTS))})",
    )
    new.add_argument(
        "--template-archive",
        dest="template_archive",
        default="",
        help="Read the template from a .tar.gz already on disk instead of the network",
    )


def _from_template_cmd(args: argparse.Namespace, *, url: str, archive: str) -> int:
    """``personalclaw app new --from-template`` — returns the process exit code."""
    if args.name:
        print(
            f"error: --from-template fetches {TEMPLATE_REPO} verbatim, so it cannot also "
            f"name the app.\n"
            f"       Fetch it and rename in place (the template README is the walkthrough):\n"
            f"         personalclaw app new --from-template\n"
            f"       Or generate a named app directly:\n"
            f"         personalclaw app new {args.name} --type tool"
        )
        return 2
    if args.type:
        print("error: --from-template and --type are different paths — pick one")
        return 2
    if url and archive:
        print("error: pass either --template-url or --template-archive, not both")
        return 2
    try:
        result = from_template(
            dest=Path(args.dest),
            url=url,
            archive=Path(archive) if archive else None,
            force=args.force,
        )
    except ScaffoldError as exc:
        print(f"error: {exc}")
        return 1
    print(f"Fetched {result.source}")
    print(f"Created {result.path} — {len(result.files)} files")
    for rel in result.files:
        print(f"  {rel}")
    print("")
    print("Next:")
    print(f"  pytest {result.path}")
    print(f"  read {result.path / 'README.md'} — clone-to-installed walkthrough")
    return 0


def app_cmd(args: argparse.Namespace) -> int:
    """``personalclaw app …`` — returns the process exit code."""
    if getattr(args, "app_cmd", None) != "new":
        print("Usage: personalclaw app new [NAME --type TYPE | --list-types]")
        return 2
    if getattr(args, "list_types", False):
        print(render_type_table(provider_type_rows()))
        return 0
    template_url = getattr(args, "template_url", "") or ""
    template_archive = getattr(args, "template_archive", "") or ""
    if (template_url or template_archive) and not getattr(args, "from_template", False):
        print("error: --template-url/--template-archive only apply with --from-template")
        return 2
    if getattr(args, "from_template", False):
        return _from_template_cmd(args, url=template_url, archive=template_archive)
    if not args.name or not args.type:
        print("Usage: personalclaw app new NAME --type TYPE")
        print("       personalclaw app new --list-types")
        return 2
    try:
        result = scaffold(
            args.name,
            args.type,
            dest=Path(args.dest),
            display_name=args.display_name,
            description=args.description,
            author=args.author,
            force=args.force,
        )
    except ScaffoldError as exc:
        print(f"error: {exc}")
        return 1
    print(f"Created {result.path} — a {result.type} app")
    for rel in result.files:
        print(f"  {rel}")
    print("")
    print(f"Contract: {result.contract.label}")
    print("Next:")
    print(f"  pytest {result.path}")
    print(f"  install it from this local path (see {result.path / 'README.md'})")
    if not args.author:
        # Said here rather than left to the README: the LICENSE is the app's grant, and a
        # copyright line reading `<your name>` grants nothing until someone fills it in. An
        # author who never opens LICENSE would otherwise publish an unheld licence.
        print(
            f"  fill in LICENSE's copyright holder (currently "
            f"{_UNFILLED_COPYRIGHT_HOLDER!r}) — or re-run with --author"
        )
    return 0

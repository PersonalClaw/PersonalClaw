"""What an app declares about each provider it contributes: its type, implementation, settings,
autonomy bounds and where it runs (``ProviderConfig``), and the types a provider may have.

Split out of ``apps/manifest.py``, which re-exports every name here, so a reader keeps importing
them from the manifest module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from personalclaw.safety_flags import strict_bool


def _not_booleans(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Each of *keys* that *data* declares as something other than ``true`` or ``false``."""
    return {key: data[key] for key in keys if key in data and not isinstance(data[key], bool)}


def _not_lists(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Each of *keys* that *data* declares as something other than a list, as written: refused
    by ``validate`` rather than read as an empty list (#3841)."""
    return {key: data[key] for key in keys if key in data and not isinstance(data[key], list)}


def _refusals(prefix: str, record: dict[str, Any]) -> list[str]:
    """The install refusal of each boolean in *record*: its name, and its value as JSON."""
    return [
        f"{prefix}{key} must be true or false, got {json.dumps(value, default=str)[:60]}"
        for key, value in record.items()
    ]


# ---------------------------------------------------------------------------
# Provider declaration (extension system)
# ---------------------------------------------------------------------------

PROVIDER_TYPES = frozenset(
    {
        "model",
        "agent",
        "task",
        "channel",
        "inbox",
        "skills",
        "knowledge",
        "memory",
        "notification",
        "tool",
        "workflow",
        "search",
        "action",
        "prompt",
        # An app-contributed is-the-user-on-duty predicate. Its
        # `DutyGateTypeHandler` lands in the same commit.
        "duty_gate",
        # An app-contributed sync transport (git-sync, dir-sync). Its
        # `SyncTypeHandler` lands in the same commit.
        "sync",
        # An app-contributed sandbox provider (container/VM isolation
        # tier). Its `SandboxTypeHandler` lands in the same commit. The `none`
        # provider is a core builtin, not an app.
        "sandbox",
        # An app-contributed ORIGIN of trigger events. The app emits
        # typed events onto the one event bus under a namespaced source (`app:<name>:<event>`),
        # which `kind: event` triggers match with the existing `{source, pattern}` spec — no new
        # trigger kind. Its `TriggerSourceTypeHandler` lands in the same commit.
        "trigger_source",
        # An app-contributed OCR ENGINE — reads text out of
        # pixels with NO model bound. Deliberately not the `model` type: an OCR engine takes no
        # prompt and must be byte-stable for the same input, which the model seam does not
        # promise. Its `OcrTypeHandler` lands in the same commit.
        "ocr",
        # An app-contributed STORE of trigger ROWS — a shared or
        # team trigger backend. NOT `trigger_source` above: that supplies the STIMULUS (live
        # observer, pushes events), this supplies the RULE (passive store, serves definitions).
        # Rows only, never execution: the local TriggerService fires, and only rows whose `author`
        # is the owner. Its `TriggerTypeHandler` lands in the same commit.
        "trigger",
        # An app-contributed EXTERNAL chunk-vector index —
        # the user's own Qdrant/pgvector/Chroma serving knowledge vector search in place of the
        # bundled sqlite-vec/vec0 index. NOT the `memory` type: that supplies a memory RECORD
        # store (records plus their own vector layer); this supplies only the chunk-vector INDEX
        # for the knowledge library, and the chunk rows themselves stay local. Its
        # `VectorStoreTypeHandler` lands in the same commit.
        "vector_store",
    }
)
# NOTE: this set MUST equal the runtime type-handler registry
# (providers/registry.py register_type_handler(...) calls). ``prompt`` was a
# registered handler (PromptTypeHandler) but was missing here — so
# ProviderConfig.validate() rejected any prompt provider manifest, blocking
# reinstall/update + third-party prompt providers.
# native-prompts is native (auto-seeded, bypasses install-time validation), which
# masked it. test_manifest_types_match_handlers guards this equality going forward.

_HOOK_OR_ENTRYPOINT_RE = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$"
)

# Where a provider's heavy work runs. This is a field on
# the EXISTING ``model`` type, deliberately NOT a new provider type: PROVIDER_TYPES above
# and the ``_TypeHandler`` set are untouched, so ``test_manifest_types_match_handlers``
# stays green by construction and every registration seam is unchanged.
EXECUTION_IN_PROCESS = "in-process"
EXECUTION_SIDECAR = "sidecar"
#: In-process is the DEFAULT and stays it. A sidecar is earned by a crash history
#: (there is no blanket sidecar migration): flipping the default would silently change the
#: runtime of every provider already installed on every machine.
EXECUTION_MODES = frozenset({EXECUTION_IN_PROCESS, EXECUTION_SIDECAR})


@dataclass
class AutonomyConfig:
    """An app-contributed action's declared autonomy bounds.

    ``floor`` is the rung the action starts at; ``ceiling`` is the rung it can never pass
    however much track record accrues. Both name a rung from
    ``guardrails.autonomy.RUNGS`` (``draft_only`` → ``one_tap`` → ``auto_with_undo`` →
    ``autonomous``). Empty means UNDECLARED, and an undeclared action behaves exactly as it
    did before this block existed — the block is purely additive, and a manifest without
    one round-trips byte-identically.

    Two things this deliberately does NOT let an app say:

    * **``leaves_machine``.** Core derives it from the app's own ``permissions.network``
      declaration. An app that could self-certify "my action stays on this machine" would
      be self-certifying its way to the top of the ladder.
    * **``autonomous`` for an action that reaches the network.** ``ceiling`` is clamped to
      ``clamp_untrusted_ceiling``'s bound at registration, LOUDLY (a log line and a SEL
      row naming both the declared and the granted ceiling). A manifest's claim has had no
      in-tree review, and a silent downgrade is a recorded finding in this codebase.
    """

    floor: str = ""
    ceiling: str = ""

    def validate(self) -> list[str]:
        from personalclaw.guardrails.autonomy import RUNGS

        errors: list[str] = []
        for label, value in (("floor", self.floor), ("ceiling", self.ceiling)):
            if value and value not in RUNGS:
                errors.append(
                    f"provider.autonomy.{label} must be one of {list(RUNGS)}, got: {value!r}"
                )
        if self.floor and self.ceiling and self.floor in RUNGS and self.ceiling in RUNGS:
            if RUNGS.index(self.ceiling) < RUNGS.index(self.floor):
                errors.append(
                    f"provider.autonomy.ceiling {self.ceiling!r} is below its floor {self.floor!r}"
                )
        return errors

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.floor:
            d["floor"] = self.floor
        if self.ceiling:
            d["ceiling"] = self.ceiling
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutonomyConfig":
        return cls(floor=str(data.get("floor", "")), ceiling=str(data.get("ceiling", "")))


@dataclass
class ProviderConfig:
    """Declares that this extension provides a pluggable provider implementation.

    ``type`` identifies the entity class (model, agent, task, etc.).
    ``implementation`` is a Python entry point in ``module.path:factory_fn``
    format, resolved relative to the extension's directory.  The factory
    receives the extension's current config dict and returns a provider instance.
    ``settingsSchema`` is a JSON Schema (Draft-07 + x-meta) describing
    user-configurable settings for this provider.
    """

    type: str = ""
    implementation: str = ""
    multiInstance: bool = False  # noqa: N815
    settingsSchema: dict[str, Any] = field(default_factory=dict)  # noqa: N815
    capabilities: list[str] = field(default_factory=list)
    # The CONCRETE provider type this model app registers into the LLM registry
    # (e.g. "bedrock", "openai", "google") — distinct from ``type`` above, which is
    # the entity CLASS ("model"). Used by the Add-instance UI to submit the right
    # type. Empty for non-model providers (agent/task/…) that don't register an
    # LLM provider type.
    providerType: str = ""  # noqa: N815
    # Optional entity sub-grouping within a provider type. Hook providers, for
    # example, are all ``type: "hook"`` but each acts on a distinct entity
    # (task, agent, comms, …). The Settings UI sub-groups cards of one type by
    # this value so "Create Task Hook" sits under a "Task Hook Provider" group.
    # Empty → the UI treats the provider as belonging to its type's default group.
    entity: str = ""
    # The rung bounds for an ``action`` provider, keyed
    # ``app:<app>.<action>`` when it registers. Additive and optional — a manifest with no
    # ``autonomy`` block declares no action type, and its action keeps exactly the
    # pre-ladder behaviour (denylist + capability fence + creation-time grant).
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    # Where this provider's heavy work RUNS.
    # ``in-process`` (the default, and what every existing app keeps) imports the
    # provider into the gateway. ``sidecar`` runs it in a child process with its own
    # venv (``local_models/sidecar.py``), so a native-lib crash kills the child instead
    # of the gateway. Deliberately NOT a new ``type``: ``PROVIDER_TYPES`` and the
    # ``_TypeHandler`` set are untouched, so registration, the app-name registry key and
    # the duck-typed local-model contract all still hold.
    execution: str = EXECUTION_IN_PROCESS
    # The capability's id within its app: kebab-case, stable across versions, and the key
    # (``<app>/<id>``) everything the capability owns is kept under. Empty in a manifest written
    # before ids (#3841): :meth:`AppManifest.capability_ids` derives one from the type then.
    id: str = ""
    #: What the capability does, in plain words: the name a person reads wherever it appears.
    label: str = ""
    description: str = ""
    #: The ids this capability was known by in earlier versions, so an update carries what was
    #: kept under an old id to this one.
    renamedFrom: list[str] = field(default_factory=list)  # noqa: N815
    not_booleans: dict[str, Any] = field(default_factory=dict, init=False, compare=False)
    not_lists: dict[str, Any] = field(default_factory=dict, init=False, compare=False)

    def validate(self) -> list[str]:
        errors: list[str] = _refusals("provider.", self.not_booleans)
        if self.execution not in EXECUTION_MODES:
            errors.append(
                f"provider.execution must be one of {sorted(EXECUTION_MODES)}, "
                f"got: {self.execution!r}"
            )
        if not self.type:
            errors.append("provider.type is required")
        elif self.type not in PROVIDER_TYPES:
            errors.append(
                f"provider.type must be one of {sorted(PROVIDER_TYPES)}, got: {self.type!r}"
            )
        if not self.implementation:
            errors.append("provider.implementation is required")
        elif not _HOOK_OR_ENTRYPOINT_RE.match(self.implementation):
            errors.append(
                f"provider.implementation must be 'module.path:factory_fn', "
                f"got: {self.implementation!r}"
            )
        errors.extend(self.autonomy.validate())
        return errors

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.id:
            d["id"] = self.id
        if self.label:
            d["label"] = self.label
        if self.description:
            d["description"] = self.description
        if self.renamedFrom:
            d["renamedFrom"] = list(self.renamedFrom)
        if self.type:
            d["type"] = self.type
        if self.implementation:
            d["implementation"] = self.implementation
        if self.multiInstance:
            d["multiInstance"] = True
        if self.settingsSchema:
            d["settingsSchema"] = self.settingsSchema
        if self.capabilities:
            d["capabilities"] = self.capabilities
        if self.entity:
            d["entity"] = self.entity
        if self.providerType:
            d["providerType"] = self.providerType
        autonomy_d = self.autonomy.to_dict()
        if autonomy_d:
            d["autonomy"] = autonomy_d
        # Only emitted when it is NOT the default, so an in-process manifest round-trips
        # byte-identically and no existing app.json grows a key it never declared.
        if self.execution != EXECUTION_IN_PROCESS:
            d["execution"] = self.execution
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderConfig":
        autonomy_raw = data.get("autonomy", {})
        renamed = data.get("renamedFrom", [])
        provider = cls(
            type=str(data.get("type", "")),
            implementation=str(data.get("implementation", "")),
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
            renamedFrom=[str(x) for x in renamed] if isinstance(renamed, list) else [],
            multiInstance=strict_bool(data.get("multiInstance"), field="multiInstance"),
            settingsSchema=dict(data.get("settingsSchema", {})),  # noqa: N815
            capabilities=[str(c) for c in data.get("capabilities", [])],
            entity=str(data.get("entity", "")),
            providerType=str(data.get("providerType", "")),  # noqa: N815
            autonomy=(
                AutonomyConfig.from_dict(autonomy_raw)
                if isinstance(autonomy_raw, dict)
                else AutonomyConfig()
            ),
            execution=str(data.get("execution", EXECUTION_IN_PROCESS) or EXECUTION_IN_PROCESS),
        )
        provider.not_booleans = _not_booleans(data, "multiInstance")
        provider.not_lists = _not_lists(data, "renamedFrom")
        return provider


# ---------------------------------------------------------------------------
# Declared quality bar
# ---------------------------------------------------------------------------

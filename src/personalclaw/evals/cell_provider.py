"""The declared provider binding one matrix cell may run against.

A cell runs in a spawned child pointed at a throwaway ``PERSONALCLAW_HOME`` seeded from
the scenario's ``fixture_home``. That home carries no ``providers[]`` and no
``active_models.json``, and no provider app is installed under its ``apps/`` — so the
only model a cell could ever reach was the offline ``scripted`` replay, and a paired
study run off one canned script is a fabricated comparison.

Two separate things are missing, and this module supplies exactly two:

1. **the selection** — ONE ``providers[]`` entry plus a ONE-use-case
   ``active_models.json`` written into the throwaway home, so the normal resolution path
   (``active_model_refs`` → ``_resolve_from_config_registry``) has a ref to resolve;
2. **the buildable type** — the wire protocol registered into THIS process's provider
   registry, because ``llm/registry._CONFIG_TYPE_MAP`` is empty by design after the
   provider-as-app migration and a cell home has no app to register one.

**Why this shape and not an env passthrough of the operator's config.** The grant a cell
gets has to be *expressed*, never inherited:

* a study declares ONE use case and ONE ``Provider:model`` ref — not "whatever the
  operator has configured". :func:`resolve_binding` reads exactly the one named
  ``providers[]`` entry out of the invoking home and the registered spec of its type,
  and carries nothing else across;
* the SECRET is never serialized. A binding may name ONE environment variable
  (:attr:`CellProviderBinding.api_key_env`); the runner forwards that one variable's
  value under :data:`CELL_KEY_ENV` and nothing else. A binding that names none gets a
  placeholder credential and can therefore only reach an unauthenticated endpoint;
* with no binding at all a cell behaves exactly as it always did — ``scripted`` stays
  the default, and :func:`apply_in_child` is a no-op that writes nothing.

:func:`apply_in_child` goes through :func:`personalclaw.evals.overlay.throwaway_home`,
the same refusal the ablation overlay and the gate arm use, so a mis-spawned cell becomes
an honest ``VERIFIER_ABSENT`` rather than writing a provider entry into the operator's
real home.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Env var carrying the JSON binding from parent to child. Read once, in the child.
BINDING_ENV = "PERSONALCLAW_EVAL_PROVIDER_BINDING"

#: Env var carrying the ONE secret a binding named, under a CELL-SCOPED name rather than
#: the provider's own ``api_key_env``. The rename is the point: nothing in the child can
#: satisfy an installed app's generic key variable by accident, so the only secret reachable
#: inside a cell is the one the study asked for by name.
CELL_KEY_ENV = "PERSONALCLAW_EVAL_PROVIDER_KEY"

#: The provider TYPE a bound cell registers for itself. Deliberately NOT an installed app's
#: type name: a distinct type cannot collide with, or be mistaken for, a provider the
#: operator installed, so a cell artifact says plainly that this entry was minted by a study.
CELL_PROVIDER_TYPE = "eval_cell_provider"


class CellBindingError(RuntimeError):
    """A declared provider binding cannot be honoured.

    Raised by :func:`resolve_binding` when the study names a ref the invoking home does not
    configure, and by :func:`apply_in_child` when the child is not pointed at a throwaway
    home. Fatal on purpose in both directions: a study that asked for a real provider and
    silently got the offline replay instead is the fabricated comparison this whole seam
    exists to prevent.
    """


@dataclass(frozen=True)
class CellProviderBinding:
    """ONE use case bound to ONE model on ONE provider, for the cells of ONE study.

    ``provider_name`` is the entry name a ``Provider:model`` ref is qualified by, so the
    ref a cell resolves is the ref the operator bound and the pin already names.
    ``protocol`` is the wire dialect (``openai``/``anthropic``), not a vendor.

    Carries no secret — see :data:`CELL_KEY_ENV`.
    """

    use_case: str
    provider_name: str
    model: str
    protocol: str = "openai"
    base_url: str = ""
    #: NAME of the single environment variable whose value the runner forwards. Empty ⇒ the
    #: cell gets a placeholder credential and can only reach an unauthenticated endpoint.
    api_key_env: str = ""
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        from personalclaw.providers.use_cases import VALID_USE_CASES

        if self.use_case not in VALID_USE_CASES:
            raise ValueError(f"unknown use case {self.use_case!r} for a cell provider binding")
        if self.protocol not in ("openai", "anthropic"):
            raise ValueError(f"unknown wire protocol {self.protocol!r} — expected openai/anthropic")
        if not self.provider_name or not self.model:
            raise ValueError("a cell provider binding needs both a provider name and a model")

    def model_ref(self) -> str:
        """The ``Provider:model`` ref this binding resolves — the pin's own spelling."""
        return f"{self.provider_name}:{self.model}"

    def to_dict(self) -> dict:
        return {
            "use_case": self.use_case,
            "provider_name": self.provider_name,
            "model": self.model,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "max_tokens": self.max_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CellProviderBinding":
        raw_max = data.get("max_tokens")
        return cls(
            use_case=str(data.get("use_case", "")),
            provider_name=str(data.get("provider_name", "")),
            model=str(data.get("model", "")),
            protocol=str(data.get("protocol", "openai")),
            base_url=str(data.get("base_url", "") or ""),
            api_key_env=str(data.get("api_key_env", "") or ""),
            max_tokens=(int(raw_max) if raw_max is not None else None),
        )


# ── the parent side ──────────────────────────────────────────────────────────


def resolve_binding(model_ref: str, *, use_case: str = "chat") -> CellProviderBinding:
    """Build the binding for ONE ``Provider:model`` ref out of the INVOKING home.

    Reads only what the ref names: the matching ``providers[]`` entry (for its endpoint,
    model and type) and — when an installed app registered that type — the app's
    :class:`~personalclaw.llm.branded_specs.BrandedProviderSpec` (for the wire protocol,
    the key variable's NAME, and ``max_tokens``). Nothing else about the operator's config
    is read, and no secret is read at all.

    Splits on the FIRST colon only: a model id may itself contain one (``gemma4:12b``), so
    ``LocalOllama:gemma4:12b`` is provider ``LocalOllama`` and model ``gemma4:12b``.
    """
    from personalclaw.llm.branded_specs import registered_spec

    ref = str(model_ref or "").strip()
    if ":" not in ref:
        raise CellBindingError(
            f"{ref!r} is not a Provider:model ref — a study must name the provider so the "
            "cell resolves the same entry the pin records"
        )
    provider_name, model = ref.split(":", 1)
    entry = _config_provider(provider_name)
    if entry is None:
        raise CellBindingError(
            f"no providers[] entry named {provider_name!r} in this home — bind the model in "
            "Settings → Models first, so the cell runs the provider the pin names"
        )
    options = dict(entry.get("options") or {})
    spec = registered_spec(str(entry.get("type") or ""))
    return CellProviderBinding(
        use_case=use_case,
        provider_name=provider_name,
        model=model or str(entry.get("model") or ""),
        protocol=(spec.protocol if spec is not None else "openai"),
        base_url=str(options.get("base_url") or options.get("endpoint") or ""),
        api_key_env=(spec.api_key_env if spec is not None else ""),
        max_tokens=(spec.max_tokens if spec is not None else None),
    )


def _config_provider(name: str) -> dict | None:
    """The invoking home's ``providers[]`` entry called ``name``, or ``None``.

    Read straight off ``config.json`` rather than through the in-memory registry: the
    parent of a matrix run is a script, not the gateway, so nothing has synced the entries
    and the registry would be empty.
    """
    from personalclaw.config.loader import config_path

    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for raw in data.get("providers") or []:
        if isinstance(raw, dict) and str(raw.get("name") or "") == name:
            return raw
    return None


def encode(binding: CellProviderBinding) -> str:
    """Render the binding for :data:`BINDING_ENV` (compact, sorted, stable)."""
    return json.dumps(binding.to_dict(), separators=(",", ":"), sort_keys=True)


def decode(text: str) -> CellProviderBinding | None:
    """Parse :data:`BINDING_ENV`. Absent/garbage ⇒ ``None`` (an unbound, scripted cell)."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("unparseable eval provider binding in env; cell stays unbound")
        return None
    if not isinstance(data, dict):
        return None
    try:
        return CellProviderBinding.from_dict(data)
    except ValueError:
        logger.warning("invalid eval provider binding in env; cell stays unbound")
        return None


def from_env(env: dict | None = None) -> CellProviderBinding | None:
    """The binding this process was spawned with, if any."""
    source = os.environ if env is None else env
    return decode(str(source.get(BINDING_ENV) or ""))


def spawn_env_for(
    base_env: dict, binding: CellProviderBinding | None, *, source: dict | None = None
) -> dict:
    """``base_env`` PLUS the binding and, if it names one, the ONE forwarded secret.

    ``source`` is where the named variable's value is read from (the parent's own
    environment by default). This is the ONLY place a secret enters a cell's environment,
    and it happens only because the study named the variable — which is the whole
    difference between an expressed grant and the ambient inheritance an
    ``os.environ.copy()`` performs for free.

    Returns a new dict; neither ``base_env`` nor ``os.environ`` is mutated.
    """
    env = dict(base_env)
    if binding is None:
        return env
    env[BINDING_ENV] = encode(binding)
    if binding.api_key_env:
        src = os.environ if source is None else source
        secret = str(src.get(binding.api_key_env) or "")
        if secret:
            env[CELL_KEY_ENV] = secret
        else:
            logger.warning(
                "eval provider binding names %s, which is not set here — the cell runs with "
                "a placeholder credential and will fail against an authenticated endpoint",
                binding.api_key_env,
            )
    return env


# ── the child side ───────────────────────────────────────────────────────────


def _spec_for(binding: CellProviderBinding):
    """The :class:`BrandedProviderSpec` this binding registers, under the cell type."""
    from personalclaw.llm.branded_specs import BrandedProviderSpec

    return BrandedProviderSpec(
        type=CELL_PROVIDER_TYPE,
        protocol=binding.protocol,
        default_base_url=binding.base_url,
        default_model=binding.model,
        max_tokens=binding.max_tokens,
        capabilities=cell_capabilities(binding.use_case),
        notes=(
            f"Declared eval-cell binding: {binding.model_ref()} over the "
            f"{binding.protocol}-compatible protocol. Minted per run; never installed."
        ),
    )


def cell_capabilities(use_case: str) -> frozenset:
    """The capability set a cell bound for ``use_case`` may serve — and no other.

    Narrow by construction: a cell bound for ``chat`` must not become the implicit provider
    for ``embedding`` or ``image_modality``, which are use cases the study never declared.
    A chat-class binding also declares ``CODE_TOOLS`` because the native loop only offers
    tool schemas to a type that does, and a benchmark that silently measured zero tool
    calls would read as "the skill needs no tools" — the same trap the scripted fixture's
    capability set is pinned against.
    """
    from personalclaw.llm.capabilities import Capability
    from personalclaw.providers.use_cases import parent_capability

    parent = parent_capability(use_case)
    if parent == "chat":
        return frozenset({Capability.CHAT, Capability.CODE_TOOLS})
    try:
        return frozenset({Capability(parent)})
    except ValueError as exc:
        raise CellBindingError(
            f"use case {use_case!r} has no provider capability a cell could declare"
        ) from exc


def _register_cell_type(binding: CellProviderBinding) -> str:
    """Register :data:`CELL_PROVIDER_TYPE` into THIS process's registry. Returns a label.

    This is the second half of the fix: ``_CONFIG_TYPE_MAP`` is empty after the
    provider-as-app migration, so without a type registered here the entry below is
    unbuildable no matter how well the selection resolves. Registered in-process only —
    nothing is written under the cell home's ``apps/``, so the grant dies with the cell.
    """
    from personalclaw.llm.branded_specs import build_protocol_provider
    from personalclaw.llm.capabilities import ProviderCapability
    from personalclaw.llm.credentials import Credential
    from personalclaw.llm.registry import (
        ProviderEntry,
        ProviderResolutionError,
        get_default_registry,
    )

    spec = _spec_for(binding)

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: object):
        del session_key  # the protocol clients are stateless
        secret = os.environ.get(CELL_KEY_ENV, "")
        credential = Credential(
            name=CELL_PROVIDER_TYPE,
            kind="api_key" if secret else "none",
            # An unauthenticated endpoint (a local runtime) ignores this, but the OpenAI SDK
            # client constructor requires a populated secret to exist at all.
            secret=secret or "unused",
            source="env" if secret else "none",
        )
        options = dict(entry.options or {})
        options.pop("base_url", None)
        options.pop("endpoint", None)
        return build_protocol_provider(
            spec,
            model=entry.model or binding.model,
            credential=credential,
            base_url=binding.base_url,
            extra_options=options,
        )

    capability = ProviderCapability(
        type=CELL_PROVIDER_TYPE,
        capabilities=spec.capabilities,
        supports_streaming=True,
        supports_tools=True,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=0,
        notes=spec.notes,
    )
    try:
        get_default_registry().register_type(capability, _factory)
    except ProviderResolutionError:
        logger.debug("cell provider type already registered in this process")
    return f"registry:type={CELL_PROVIDER_TYPE}"


def apply_in_child(binding: CellProviderBinding | None) -> list[str]:
    """Bind ``binding`` inside THIS cell's throwaway home. Returns what changed.

    ``None`` ⇒ ``[]`` and not one byte written: an unbound cell resolves exactly what it
    resolved before this seam existed.

    Raises :class:`CellBindingError` when the process is not pointed at a throwaway home —
    the same rail the ablation overlay and the gate arm stage behind, because a
    ``providers[]`` entry written into the operator's real home is a permanent edit made by
    a run that promised to touch nothing.
    """
    if binding is None:
        return []

    from personalclaw.evals import overlay as overlay_lib
    from personalclaw.llm.registry import sync_entries_from_config
    from personalclaw.providers.use_cases import save_active_models

    try:
        overlay_lib.throwaway_home()
    except overlay_lib.OverlayRefusedError as exc:
        raise CellBindingError(f"refusing to bind a provider in this home: {exc}") from exc

    changed = [
        overlay_lib.patch_child_config(
            "providers",
            [
                {
                    "name": binding.provider_name,
                    "type": CELL_PROVIDER_TYPE,
                    "model": binding.model,
                    "options": {"base_url": binding.base_url} if binding.base_url else {},
                }
            ],
        )
    ]
    save_active_models({binding.use_case: [binding.model_ref()]})
    changed.append(f"active_models.json:{binding.use_case}={binding.model_ref()}")
    changed.append(_register_cell_type(binding))
    # Replays the entry just written into the in-memory registry, which is what
    # ``_resolve_from_config_registry`` walks. Called AFTER the type registration so the
    # entry inherits the declared capability set instead of an empty one.
    synced = sync_entries_from_config()
    changed.append(f"registry:entries={synced}")
    return changed

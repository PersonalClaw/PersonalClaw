"""``--seed-local-model`` — bind a local Ollama provider into ``$PERSONALCLAW_HOME``.

Why this is a step BESIDE the fixture rather than content INSIDE it: ``seed()`` is a
bare ``shutil.copytree`` of a checked-in tree (see :mod:`personalclaw.seed`), so
everything it writes is byte-identical on every machine. A model binding is not
machine-independent — it names an endpoint that may or may not be listening and a
model that may or may not be pulled. Baking one operator's endpoint into the fixture
would ship a demo home that cannot serve a turn anywhere else, and the copy has no
hook where a probe could run. So the fixture stays machine-independent and this
module does the machine-dependent half, conditionally.

The whole point is that it DEGRADES rather than half-populates. Nothing is written
unless every precondition holds:

* the endpoint answers Ollama's ``/api/tags``,
* that answer contains a model to bind,
* the ``ollama-models`` provider app is installed in the home (or installable from a
  local app source).

If any of those is missing the home is left exactly as the fixture wrote it and the
reason is printed. A demo seed that errors, hangs, or writes a ``providers[]`` entry
no installed app can build is worse than one that populates nothing, because the
operator then has to diagnose a broken home instead of reading one line of output.

No credential is involved anywhere. A local Ollama needs none, which is why it is the
right provider for a committed demo path — do not extend this to a cloud provider,
which would.

Two verbs share every precondition. :func:`bind_local_model` makes the endpoint the chat
model (onboarding's "Use this model", and this seed step). :func:`add_local_model` adds it
as one more provider beside one already set up and changes no binding, so a cloud provider
chosen first stays what chat uses.
"""

import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from personalclaw import memory_writes

if TYPE_CHECKING:
    from personalclaw.llm.catalog import ModelInfo

logger = logging.getLogger(__name__)

#: Where a stock Ollama listens. Overridable per-machine — see :data:`ENDPOINT_ENV`
#: and the ``endpoint`` argument. Deliberately ``localhost`` (not ``127.0.0.1``) to
#: match the ``ollama-models`` app's own default, so a home bound here and a home
#: bound through Settings → Models carry the same string.
DEFAULT_ENDPOINT = "http://localhost:11434"

ENDPOINT_ENV = "PERSONALCLAW_LOCAL_MODEL_ENDPOINT"
MODEL_ENV = "PERSONALCLAW_LOCAL_MODEL"
EMBEDDING_MODEL_ENV = "PERSONALCLAW_LOCAL_EMBEDDING_MODEL"
APPS_DIR_ENV = "PERSONALCLAW_LOCAL_MODEL_APPS_DIR"

#: The provider app that registers the ``ollama`` type. Core registers no real
#: provider type of its own (``llm/registry.py``'s ``_CONFIG_TYPE_MAP`` is empty by
#: design after the provider-as-app migration), so without this app installed a
#: ``providers[]`` entry of this type is a name nothing can build.
PROVIDER_APP = "ollama-models"
PROVIDER_TYPE = "ollama"

#: Display name of the written ``providers[]`` entry. It shows up in Settings →
#: Models and in the ``"<provider>:<model>"`` refs in ``active_models.json``, so it is
#: prose a screenshot can carry rather than a slug.
PROVIDER_ENTRY_NAME = "Local Ollama"

#: Probe budget. Short on purpose: the common case on a machine without Ollama is a
#: refused connection (instant), but a firewalled host can black-hole the SYN, and a
#: seed step must not hang there. Two seconds is well past a loopback round-trip.
PROBE_TIMEOUT_SECS = 2.0

#: How long the provider app's own model listing may take before the pick falls back to
#: what the model ids suggest. That listing asks the server about each model, which a
#: local server answers in well under a second.
DESCRIBE_BUDGET_SECS = 5.0

#: The capability tag a provider puts on a chat model it says calls tools
#: (``ModelInfo.capabilities``). Every turn the agent sends offers it tools.
CALLS_TOOLS = "tools"

#: Request timeout written onto the provider entry. A cold local model has to load
#: from disk into VRAM before it emits a first token, which on a 12B q4 model is tens
#: of seconds — the app's own 120s default is the floor for a demo turn that must not
#: die on the first prompt.
REQUEST_TIMEOUT_SECS = 300

# Outcome statuses. `bound` and `added` are the only ones that write anything.
BOUND = "bound"
ADDED = "added"
ALREADY_BOUND = "already_bound"
SKIPPED_NO_SERVER = "skipped_no_server"
SKIPPED_NO_MODEL = "skipped_no_model"
SKIPPED_NO_PROVIDER_APP = "skipped_no_provider_app"
SKIPPED_CONFIG_UNREADABLE = "skipped_config_unreadable"
SKIPPED_NAME_TAKEN = "skipped_name_taken"


@dataclass
class BindResult:
    """What the bind step decided, and why.

    ``wrote`` is the list of home-relative files touched — empty for every skip
    status, which is the invariant the no-model direction is tested against.
    """

    status: str
    detail: str
    endpoint: str = ""
    model: str = ""
    embedding_model: str = ""
    provider_name: str = ""
    wrote: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the endpoint is a provider now — bound, added, or already there.

        A skip is NOT a failure: on a machine with no Ollama the intended outcome is
        "seeded, unbound, and said so", so callers must not treat it as an error.
        """
        return self.status in (BOUND, ADDED, ALREADY_BOUND)


def _probe_models(endpoint: str, *, timeout: float | None = None) -> list[dict] | None:
    """Return Ollama's ``/api/tags`` model list, or None when unreachable.

    Probes the dialect the provider will actually SPEAK. The ``ollama-models`` app is
    a client of Ollama's ``/api/*`` endpoints, not of the OpenAI-compatible ``/v1``
    shim, so an endpoint that serves only ``/v1/models`` is not bindable here and
    correctly reads as unreachable.

    Uses ``urllib`` rather than ``httpx`` so the seed path pulls in no provider SDK.

    ``timeout`` overrides the default per-probe budget, :data:`PROBE_TIMEOUT_SECS` as it
    stands when the probe runs. The onboarding LAN scan
    (:mod:`personalclaw.local_model_detect`) reuses THIS probe with a much shorter
    budget so a whole subnet sweep stays inside a few seconds — one ``/api/tags``
    implementation, not a second socket.
    """
    url = endpoint.rstrip("/") + "/api/tags"
    budget = PROBE_TIMEOUT_SECS if timeout is None else timeout
    try:
        with urllib.request.urlopen(url, timeout=budget) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("local model probe failed for %s: %s", url, exc)
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    return models if isinstance(models, list) else []


def _described_by_provider(endpoint: str, *, budget: float) -> "list[ModelInfo] | None":
    """The models ``endpoint`` serves as the provider app itself describes them, or None.

    The app that registers :data:`PROVIDER_TYPE` registers a catalog for it too, and that
    catalog reads what each model does from the server's own record of it — whether it reads
    images, whether it calls tools — where a model's id says neither. Core asks it through the
    registry's catalog seam and names no wire format of its own here.

    None when no loaded app registers one (a CLI run before the gateway loads its apps), or
    when its listing fails or runs past ``budget``: what the ids suggest then stands.
    """
    from personalclaw.llm.catalog import ModelInfo
    from personalclaw.llm.registry import get_default_registry

    factory = get_default_registry().catalog_of(PROVIDER_TYPE)
    if factory is None:
        return None

    async def _listing() -> list[ModelInfo]:
        return await asyncio.wait_for(factory({"endpoint": endpoint}).list_models(), budget)

    try:
        # A loop of its own on a thread of its own, so a caller on any thread can ask, one
        # already running a loop included.
        with memory_writes.ScopeCarryingExecutor(max_workers=1) as pool:
            rows = pool.submit(asyncio.run, _listing()).result()
    except Exception:  # noqa: BLE001 — the app's description refines the pick, it never gates it
        logger.debug("the provider's model listing for %s failed", endpoint, exc_info=True)
        return None
    described = [row for row in rows if isinstance(row, ModelInfo) and row.id]
    return described or None


def _from_tags(models: list[dict]) -> "list[ModelInfo]":
    """``/api/tags`` rows as models, each with the capabilities its id and families suggest.

    Capability inference is shared with every model app via
    ``llm.catalog.infer_capabilities`` — importing it here is what keeps an embedding
    model from being auto-bound to ``chat`` (and vice versa) by a second, drifting
    copy of the same name heuristics.
    """
    from personalclaw.llm.catalog import ModelInfo, infer_capabilities

    out: list[ModelInfo] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("model") or m.get("name") or "")
        if not mid:
            continue
        details = m.get("details")
        families = details.get("families") if isinstance(details, dict) else None
        out.append(
            ModelInfo(
                id=mid,
                name=mid,
                capabilities=infer_capabilities(
                    mid, families if isinstance(families, list) else None
                ),
                extra={"modified_at": str(m.get("modified_at") or "")},
            )
        )
    return out


def endpoint_models(
    endpoint: str,
    *,
    timeout: float | None = None,
    describe_budget: float | None = None,
) -> "list[ModelInfo] | None":
    """The models ``endpoint`` serves, each with what it can be bound for; None when unreachable.

    Reachability is the one cheap ``/api/tags`` probe (:func:`_probe_models`). What each model
    can do is the provider app's description where a loaded app gives one
    (:func:`_described_by_provider`), else what the ids suggest. A budget not given is the
    module's own (:data:`PROBE_TIMEOUT_SECS`, :data:`DESCRIBE_BUDGET_SECS`) as it stands when the
    call is made.
    """
    tags = _probe_models(endpoint, timeout=timeout)
    if tags is None:
        return None
    if not tags:
        return []
    budget = DESCRIBE_BUDGET_SECS if describe_budget is None else describe_budget
    return _described_by_provider(endpoint, budget=budget) or _from_tags(tags)


def pick_model(models: "list[ModelInfo]", want: str) -> str:
    """The model to bind for ``want``: one whose capabilities include it, newest first.

    For chat, a model its provider says calls tools (:data:`CALLS_TOOLS`) comes before one it
    does not: every turn the agent sends offers it tools, and a model that cannot call them
    answers without them. Whether a model also reads images counts neither for it nor against
    it. Recency is ``modified_at`` where the listing carries it — the
    model an operator last pulled or ran is the one they meant — so the order never rests on
    the server's incidental response order.
    """
    able = [m for m in models if want in (m.capabilities or [])]
    able.sort(key=lambda m: str((m.extra or {}).get("modified_at") or ""), reverse=True)
    if want == "chat":
        able.sort(key=lambda m: CALLS_TOOLS not in (m.capabilities or []))
    return able[0].id if able else ""


def _installed_provider_app() -> bool:
    """True when the provider app is installed AND enabled in this home."""
    from personalclaw.apps import manager as apps_manager

    try:
        for app in apps_manager.list_apps():
            if app.get("name") == PROVIDER_APP and app.get("enabled", True):
                return True
    except Exception:  # noqa: BLE001 — a broken apps/ dir reads as "not installed"
        logger.debug("could not list installed apps", exc_info=True)
    return False


def _candidate_app_dirs(apps_dir: str | None) -> list[Path]:
    """Directories that might contain the ``ollama-models`` app, best first.

    Local sources ONLY. A demo-seed helper must not clone from the network behind the
    operator's back: the published apps repo is a Store *listing* default precisely so
    that installing from it stays an explicit per-app consent, and this step does not
    get to bypass that. An operator with no local checkout installs the Ollama app
    from the App Store first, which puts this on the already-installed path.
    """
    out: list[Path] = []
    explicit = apps_dir or os.environ.get(APPS_DIR_ENV, "")
    if explicit:
        out.append(Path(explicit).expanduser())
    try:
        from personalclaw.apps import catalog as apps_catalog

        out.extend(Path(p).expanduser() for p in apps_catalog.list_local_sources())
        first_party = apps_catalog._first_party_source()  # noqa: SLF001 — same subsystem
        if first_party is not None:
            out.append(first_party)
    except Exception:  # noqa: BLE001 — source discovery is best-effort
        logger.debug("local app-source discovery failed", exc_info=True)
    return out


def _resolve_app_source(apps_dir: str | None) -> Path | None:
    """Find the ``ollama-models`` app directory in a local source, or None.

    Accepts either a source root holding ``ollama-models/app.json`` or a path pointing
    straight at the app dir, because both are things an operator reasonably types.
    """
    for base in _candidate_app_dirs(apps_dir):
        for candidate in (base / PROVIDER_APP, base):
            if (candidate / "app.json").is_file() and candidate.name == PROVIDER_APP:
                return candidate
    return None


def _entry_named(data: dict, name: str) -> dict | None:
    providers = data.get("providers")
    if not isinstance(providers, list):
        return None
    return next((p for p in providers if isinstance(p, dict) and p.get("name") == name), None)


def _entry_endpoint(entry: dict) -> str:
    """Where an entry of this type sends: its ``endpoint`` option, else the app's default."""
    options = entry.get("options")
    endpoint = options.get("endpoint") if isinstance(options, dict) else None
    return str(endpoint or DEFAULT_ENDPOINT).rstrip("/")


def _configured_entry(name: str) -> dict | None:
    """The ``config.json`` provider entry named *name*, or None.

    Raises :class:`~personalclaw.config.loader.ConfigPreserveError` for a file that exists and
    cannot be read: "no entry" would be a guess, and the write that followed it used to replace
    the whole unreadable file with one ``providers[]`` entry.
    """
    from personalclaw.config.loader import config_path, read_config_for_merge

    return _entry_named(read_config_for_merge(config_path()), name)


def instance_at(endpoint: str) -> str:
    """The name of the provider instance of this type already at ``endpoint``, or ``""``.

    However it was made — this module's bind or add, or by hand in Settings → Providers — so a
    discovered endpoint that is already set up is said as set up. ``""`` too when ``config.json``
    cannot be read: nothing is known, and the add itself refuses such a file in words.
    """
    from personalclaw.config.loader import ConfigPreserveError, config_path, read_config_for_merge

    try:
        data = read_config_for_merge(config_path())
    except ConfigPreserveError:
        return ""
    providers = data.get("providers")
    want = endpoint.rstrip("/")
    for p in providers if isinstance(providers, list) else []:
        if isinstance(p, dict) and p.get("type") == PROVIDER_TYPE and _entry_endpoint(p) == want:
            return str(p.get("name") or "")
    return ""


def _write_provider_entry(*, endpoint: str, model: str, embedding_model: str) -> None:
    """Append the ``providers[]`` entry in the shape the Settings API persists.

    Same keys, same nesting as ``dashboard/handlers/providers.api_provider_create`` —
    a hand-written entry that drifts from that shape is one the UI cannot edit. No
    ``credential`` key is emitted: the absence IS the contract, and the ollama factory
    only resolves a credential when the entry declares one. In the config transaction, and
    only if no entry of that name has appeared since the check: a second bind appends nothing.
    """
    from personalclaw.config.transactions import mutate_config

    options: dict[str, object] = {
        "endpoint": endpoint,
        "default_model": model,
        "timeout_secs": REQUEST_TIMEOUT_SECS,
    }
    if embedding_model:
        options["embedding_model"] = embedding_model

    def _append(data: dict) -> None:
        if _entry_named(data, PROVIDER_ENTRY_NAME) is not None:
            return
        providers = data.get("providers")
        if not isinstance(providers, list):
            providers = data["providers"] = []
        providers.append(
            {
                "name": PROVIDER_ENTRY_NAME,
                "type": PROVIDER_TYPE,
                "model": model,
                "options": options,
            }
        )

    mutate_config(_append)


def _write_active_models(*, model: str, embedding_model: str) -> None:
    """Bind the use cases, merging into any existing ``active_models.json``.

    Only ``chat`` and (when an embedding model was found) ``embedding`` are written.
    The chat sub-categories (``reasoning``, ``code_tools``, ``background``, …) are
    deliberately left unbound: they fall back to the parent ``chat`` binding, so
    pinning them would add rows that say nothing and would then have to be re-pinned
    by hand every time the demo model changes.
    """
    from personalclaw.providers.use_cases import load_active_models, save_active_models

    active = load_active_models()
    active["chat"] = [f"{PROVIDER_ENTRY_NAME}:{model}"]
    if embedding_model:
        active["embedding"] = [f"{PROVIDER_ENTRY_NAME}:{embedding_model}"]
    save_active_models(active)


@dataclass
class _Plan:
    """Everything a bind or an add would write, resolved before the first write."""

    endpoint: str
    chat_model: str
    embedding_model: str
    unpulled: bool
    source: Path | None


def _plan(
    endpoint: str, want_model: str, want_embedding: str, apps_dir: str | None
) -> "_Plan | BindResult":
    """Resolve every precondition, or the :class:`BindResult` that says which one failed.

    Writes nothing, which is what makes every skip leave the home untouched.
    """
    from personalclaw.config.loader import ConfigPreserveError

    try:
        existing = _configured_entry(PROVIDER_ENTRY_NAME)
    except ConfigPreserveError as exc:
        return BindResult(
            status=SKIPPED_CONFIG_UNREADABLE,
            detail=(
                f"{exc} — nothing was written. Repair config.json (`personalclaw doctor` says "
                f"what is wrong with it) and re-run."
            ),
            endpoint=endpoint,
        )
    if existing is not None:
        there = _entry_endpoint(existing)
        if there == endpoint.rstrip("/"):
            return BindResult(
                status=ALREADY_BOUND,
                detail=(
                    f"a provider entry named {PROVIDER_ENTRY_NAME!r} already exists in "
                    f"config.json — leaving it alone"
                ),
                endpoint=endpoint,
                provider_name=PROVIDER_ENTRY_NAME,
            )
        return BindResult(
            status=SKIPPED_NAME_TAKEN,
            detail=(
                f"The provider {PROVIDER_ENTRY_NAME!r} already uses {there}, so nothing was "
                f"written for {endpoint}. Add it under another name in Settings → Providers."
            ),
            endpoint=endpoint,
        )

    models = endpoint_models(endpoint)
    if models is None:
        return BindResult(
            status=SKIPPED_NO_SERVER,
            detail=(
                f"no Ollama answered {endpoint}/api/tags — the home is seeded but no "
                f"model is bound, so chat, approvals and artifacts stay empty. Start "
                f"Ollama and re-run, or set ${ENDPOINT_ENV} to a reachable endpoint."
            ),
            endpoint=endpoint,
        )

    chat_model = want_model or pick_model(models, "chat")
    if not chat_model:
        return BindResult(
            status=SKIPPED_NO_MODEL,
            detail=(
                f"{endpoint} is reachable but has no chat-capable model pulled — "
                f"nothing was bound. Pull one (e.g. `ollama pull llama3.2:3b`) and "
                f"re-run, or name one with ${MODEL_ENV}."
            ),
            endpoint=endpoint,
        )

    source: Path | None = None
    if not _installed_provider_app():
        source = _resolve_app_source(apps_dir)
        if source is None:
            return BindResult(
                status=SKIPPED_NO_PROVIDER_APP,
                detail=(
                    f"{endpoint} is reachable with model {chat_model!r}, but the "
                    f"{PROVIDER_APP!r} app is not installed in this home and no local "
                    f"app source has it. Nothing was written — a providers[] entry "
                    f"whose type no installed app registers is unbuildable. Install "
                    f"the Ollama app from the App Store, or point ${APPS_DIR_ENV} at a "
                    f"checkout of the apps repo."
                ),
                endpoint=endpoint,
                model=chat_model,
            )

    # A named-but-absent model is still bound: naming it is an explicit instruction,
    # and Ollama can pull it later. Say so rather than silently binding a dead ref.
    return _Plan(
        endpoint=endpoint,
        chat_model=chat_model,
        embedding_model=want_embedding or pick_model(models, "embedding"),
        unpulled=chat_model not in {m.id for m in models},
        source=source,
    )


def _install_provider_app(plan: _Plan) -> "list[str] | BindResult":
    """Install the provider app when the plan found it in a local source; what that wrote."""
    if plan.source is None:
        return []
    from personalclaw.apps import app_manager
    from personalclaw.supply_chain import Verdict

    # `--seed-local-model` is the operator asking for exactly this app, which is consent
    # to install it — bound to the bytes reviewed here. It is NOT consent to scanner
    # warnings nobody has read, so a warning still refuses, as it always did.
    review = app_manager.preview(plan.source, origin="local")
    if review.scan is not None and review.scan.verdict is Verdict.WARNING:
        result, why = review, "install needs consent: scanner raised warnings"
    elif review.consent:
        result = app_manager.install(
            plan.source, origin="local", consent=review.consent, caller="seed_local_model"
        )
        why = result.error
    else:
        result, why = review, review.error
    if not result.ok:
        return BindResult(
            status=SKIPPED_NO_PROVIDER_APP,
            detail=(
                f"installing {PROVIDER_APP!r} from {plan.source} failed "
                f"({why or 'unknown error'}) — nothing was written."
            ),
            endpoint=plan.endpoint,
            model=plan.chat_model,
        )
    return [f"apps/{PROVIDER_APP}/"]


def bind_local_model(
    *,
    endpoint: str | None = None,
    model: str | None = None,
    embedding_model: str | None = None,
    apps_dir: str | None = None,
) -> BindResult:
    """Bind a local Ollama provider into ``$PERSONALCLAW_HOME``, or explain why not.

    Every argument falls back to its env var and then to a sensible default, so the
    step is configurable per machine without a flag and without a machine-specific
    value ever being committed.

    Writes at most three things, and only after ALL preconditions hold: the provider
    app (installed if it isn't already), the ``config.json`` ``providers[]`` entry, and
    the ``active_models.json`` use-case binding. Resolution runs to completion BEFORE
    the first write, which is what makes a skip leave the home untouched.
    """
    endpoint = (endpoint or os.environ.get(ENDPOINT_ENV, "") or DEFAULT_ENDPOINT).strip()
    want_model = (model or os.environ.get(MODEL_ENV, "")).strip()
    want_embedding = (embedding_model or os.environ.get(EMBEDDING_MODEL_ENV, "")).strip()

    plan = _plan(endpoint, want_model, want_embedding, apps_dir)
    if isinstance(plan, BindResult):
        return plan
    wrote = _install_provider_app(plan)
    if isinstance(wrote, BindResult):
        return wrote

    _write_provider_entry(
        endpoint=endpoint, model=plan.chat_model, embedding_model=plan.embedding_model
    )
    wrote.append("config.json")
    _write_active_models(model=plan.chat_model, embedding_model=plan.embedding_model)
    wrote.append("active_models.json")

    detail = f"bound {PROVIDER_ENTRY_NAME!r} -> {plan.chat_model} at {endpoint}"
    if plan.embedding_model:
        detail += f" (embedding: {plan.embedding_model})"
    if plan.unpulled:
        detail += f" — note: {plan.chat_model!r} is not pulled yet on this endpoint"
    return BindResult(
        status=BOUND,
        detail=detail,
        endpoint=endpoint,
        model=plan.chat_model,
        embedding_model=plan.embedding_model,
        provider_name=PROVIDER_ENTRY_NAME,
        wrote=wrote,
    )


def add_local_model(*, endpoint: str, apps_dir: str | None = None) -> BindResult:
    """Add a local Ollama as one more provider instance, binding no use case.

    The way to set it up BESIDE a provider already chosen: the entry is appended to
    ``providers[]`` and ``active_models.json`` is not touched, so what chat, embedding and every
    other use case use stays as it was. The entry names the model the endpoint offers for chat
    (and for embedding, when it has one) as its own defaults, which is what a binding to it later
    starts from. Same preconditions, and the same refusal to write anything when one fails, as
    :func:`bind_local_model`.
    """
    endpoint = endpoint.strip()
    plan = _plan(endpoint, "", "", apps_dir)
    if isinstance(plan, BindResult):
        return plan
    wrote = _install_provider_app(plan)
    if isinstance(wrote, BindResult):
        return wrote

    _write_provider_entry(
        endpoint=endpoint, model=plan.chat_model, embedding_model=plan.embedding_model
    )
    wrote.append("config.json")
    return BindResult(
        status=ADDED,
        detail=(
            f"added {PROVIDER_ENTRY_NAME!r} at {endpoint} ({plan.chat_model}); "
            f"no use case was rebound"
        ),
        endpoint=endpoint,
        model=plan.chat_model,
        embedding_model=plan.embedding_model,
        provider_name=PROVIDER_ENTRY_NAME,
        wrote=wrote,
    )


def seed_local_model_cmd(args) -> int:  # noqa: ANN001 — argparse.Namespace at call site
    """CLI entry point. Always returns 0 — a skip is an outcome, not a failure.

    Prints one line either way, prefixed so it is greppable next to the gateway's own
    startup noise. Returning non-zero on "no Ollama here" would abort the gateway for
    the majority of people running ``--seed demo-home``, which is the opposite of
    degrading gracefully.
    """
    result = bind_local_model(
        endpoint=getattr(args, "local_model_endpoint", None),
        model=getattr(args, "local_model", None),
        apps_dir=getattr(args, "local_model_apps_dir", None),
    )
    stream = sys.stdout if result.ok else sys.stderr
    print(f"seed-local-model: {result.status}: {result.detail}", file=stream)
    return 0


def _main(argv: list[str] | None = None) -> int:
    """``python -m personalclaw.seed_local_model`` — bind an EXISTING home.

    The gateway flag covers "seed and bind in one command"; this entry point covers a
    home that already exists and must not be re-seeded (an evals cell home, a
    research-lab home), without booting a server to do it.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m personalclaw.seed_local_model",
        description=(
            "Bind a local Ollama provider into $PERSONALCLAW_HOME. Writes nothing "
            "when no local Ollama is reachable."
        ),
    )
    parser.add_argument("--local-model-endpoint", metavar="URL", default=None)
    parser.add_argument("--local-model", metavar="MODEL_ID", default=None)
    parser.add_argument("--local-model-apps-dir", metavar="DIR", default=None)
    return seed_local_model_cmd(parser.parse_args(argv))


if __name__ == "__main__":  # pragma: no cover — exercised via _main in tests
    raise SystemExit(_main())

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
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

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

#: Request timeout written onto the provider entry. A cold local model has to load
#: from disk into VRAM before it emits a first token, which on a 12B q4 model is tens
#: of seconds — the app's own 120s default is the floor for a demo turn that must not
#: die on the first prompt.
REQUEST_TIMEOUT_SECS = 300

# Outcome statuses. `bound` is the only one that writes anything.
BOUND = "bound"
ALREADY_BOUND = "already_bound"
SKIPPED_NO_SERVER = "skipped_no_server"
SKIPPED_NO_MODEL = "skipped_no_model"
SKIPPED_NO_PROVIDER_APP = "skipped_no_provider_app"


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
        """True when a model is bound — including when it already was.

        A skip is NOT a failure: on a machine with no Ollama the intended outcome is
        "seeded, unbound, and said so", so callers must not treat it as an error.
        """
        return self.status in (BOUND, ALREADY_BOUND)


def _probe_models(endpoint: str) -> list[dict] | None:
    """Return Ollama's ``/api/tags`` model list, or None when unreachable.

    Probes the dialect the provider will actually SPEAK. The ``ollama-models`` app is
    a client of Ollama's ``/api/*`` endpoints, not of the OpenAI-compatible ``/v1``
    shim, so an endpoint that serves only ``/v1/models`` is not bindable here and
    correctly reads as unreachable.

    Uses ``urllib`` rather than ``httpx`` so the seed path pulls in no provider SDK.
    """
    url = endpoint.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_SECS) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("local model probe failed for %s: %s", url, exc)
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    return models if isinstance(models, list) else []


def _by_recency(models: list[dict]) -> list[dict]:
    """Sort tags most-recently-modified first.

    ``modified_at`` is a documented field of the ``/api/tags`` payload, so ordering on
    it is stable rather than relying on the server's incidental response order. Recency
    is the right default because the model an operator last pulled or ran is the one
    they meant to demo with.
    """
    return sorted(models, key=lambda m: str(m.get("modified_at") or ""), reverse=True)


def _pick(models: list[dict], want: str) -> str:
    """Return the newest model id whose inferred capabilities include ``want``.

    Capability inference is shared with every model app via
    ``llm.catalog.infer_capabilities`` — importing it here is what keeps an embedding
    model from being auto-bound to ``chat`` (and vice versa) by a second, drifting
    copy of the same name heuristics.
    """
    from personalclaw.llm.catalog import infer_capabilities

    for m in _by_recency(models):
        mid = str(m.get("model") or m.get("name") or "")
        if not mid:
            continue
        families = (m.get("details") or {}).get("families") if isinstance(m, dict) else None
        caps = infer_capabilities(mid, families if isinstance(families, list) else None)
        if want in caps:
            return mid
    return ""


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


def _config_has_entry(name: str) -> bool:
    from personalclaw.config.loader import config_path

    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return False
    providers = data.get("providers")
    if not isinstance(providers, list):
        return False
    return any(isinstance(p, dict) and p.get("name") == name for p in providers)


def _write_provider_entry(*, endpoint: str, model: str, embedding_model: str) -> None:
    """Append the ``providers[]`` entry in the shape the Settings API persists.

    Same keys, same nesting as ``dashboard/handlers/providers.api_provider_create`` —
    a hand-written entry that drifts from that shape is one the UI cannot edit. No
    ``credential`` key is emitted: the absence IS the contract, and the ollama factory
    only resolves a credential when the entry declares one.
    """
    from personalclaw.atomic_write import atomic_write
    from personalclaw.config.loader import config_path

    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    options: dict[str, object] = {
        "endpoint": endpoint,
        "default_model": model,
        "timeout_secs": REQUEST_TIMEOUT_SECS,
    }
    if embedding_model:
        options["embedding_model"] = embedding_model
    providers = data.setdefault("providers", [])
    if not isinstance(providers, list):
        providers = []
        data["providers"] = providers
    providers.append(
        {
            "name": PROVIDER_ENTRY_NAME,
            "type": PROVIDER_TYPE,
            "model": model,
            "options": options,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, indent=2) + "\n", fsync=True)


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

    if _config_has_entry(PROVIDER_ENTRY_NAME):
        return BindResult(
            status=ALREADY_BOUND,
            detail=(
                f"a provider entry named {PROVIDER_ENTRY_NAME!r} already exists in "
                f"config.json — leaving it alone"
            ),
            endpoint=endpoint,
            provider_name=PROVIDER_ENTRY_NAME,
        )

    models = _probe_models(endpoint)
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

    chat_model = want_model or _pick(models, "chat")
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
    embed_model = want_embedding or _pick(models, "embedding")

    # A named-but-absent model is still bound: naming it is an explicit instruction,
    # and Ollama can pull it later. Say so rather than silently binding a dead ref.
    have = {str(m.get("model") or m.get("name") or "") for m in models}
    unpulled = chat_model not in have

    installed = _installed_provider_app()
    source: Path | None = None
    if not installed:
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

    wrote: list[str] = []
    if source is not None:
        from personalclaw.apps import app_manager

        result = app_manager.install(source, origin="local", caller="seed_local_model")
        if not result.ok:
            return BindResult(
                status=SKIPPED_NO_PROVIDER_APP,
                detail=(
                    f"installing {PROVIDER_APP!r} from {source} failed "
                    f"({result.error or 'unknown error'}) — nothing was written."
                ),
                endpoint=endpoint,
                model=chat_model,
            )
        wrote.append(f"apps/{PROVIDER_APP}/")

    _write_provider_entry(endpoint=endpoint, model=chat_model, embedding_model=embed_model)
    wrote.append("config.json")
    _write_active_models(model=chat_model, embedding_model=embed_model)
    wrote.append("active_models.json")

    detail = f"bound {PROVIDER_ENTRY_NAME!r} -> {chat_model} at {endpoint}"
    if embed_model:
        detail += f" (embedding: {embed_model})"
    if unpulled:
        detail += f" — note: {chat_model!r} is not pulled yet on this endpoint"
    return BindResult(
        status=BOUND,
        detail=detail,
        endpoint=endpoint,
        model=chat_model,
        embedding_model=embed_model,
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

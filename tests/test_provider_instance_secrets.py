"""A sensitive setting is write-only on EVERY route that carries a config — derived, not listed.

``295b48f7b`` made ``x-meta.sensitive`` write-only on ``GET/PATCH /api/providers/{name}/config``
and pinned it with a rail that parametrizes over the two modules serving *that* route. The rail
was a hand-written list of two, so it had nothing to say about a third surface — and there were
three more. Measured against a live gateway (``openai-models``: ``multiInstance: true``,
``api_key`` sensitive), on the parked parent:

* ``POST   /api/providers/{name}/instances``      → the freshly-saved key, in the clear
* ``PUT    /api/providers/{name}/instances/{id}`` → the same, on every save
* ``GET    /api/providers/{name}/instances/{id}`` → the stored key
* ``GET    /api/providers/{name}/instances``      → EVERY instance's key, in ONE body, needing
  no operator action at all
* ``GET    /api/apps/{name}``                     → the stored config verbatim, while
  ``GET /api/apps/{name}/config`` — the same file, the same flag, two functions further down
  the same module — had masked since #43

Eleven bundled model apps declare exactly that shape (``openai-models``, ``anthropic-models``,
``google-models``, ``groq-models``, ``mistral-models``, ``deepseek-models``, ``together-models``,
``openrouter-models``, ``alibaba-models``, ``openai-compatible``, ``anthropic-compatible``).

**Which client reaches the list route, measured rather than assumed.** A cold
Settings → Providers load calls it once per enabled multi-instance provider — measured, for
``mcp-tools`` and ``openai-tools``. It does NOT call it for a **model** provider, which renders
through ``ModelBackends.tsx`` off ``/api/model-providers``. So those eleven apps leaked to any
token-holding API client rather than to the browser. The rail below is deliberately indifferent
to that: it asks whether a config reaches a response body, not who reads the body, because a
route is the boundary a client is on the far side of.

**Why this file's rail is DERIVED.** A list of route names is what let three of those five sit
unnoticed, so nothing here enumerates a route. :func:`scan` walks the tree, learns which store
functions return a config-or-instance object *from those stores' own return annotations*, taints
their results through each route handler, and reports every tainted value that reaches a
``json_response`` payload without passing through :mod:`personalclaw.apps.secret_fields`. Add a
sixth config-carrying route and it is classified whether or not anyone remembers this file.

A derived rail that finds nothing is indistinguishable from a clean tree, so the census carries
vacuity floors (:func:`test_the_scan_is_not_vacuous`,
:func:`test_the_store_derivation_still_discriminates`) and a planted-regression proof against the
REAL shipped source (:func:`test_reverting_the_mask_in_instance_routes_is_counted`).
"""

from __future__ import annotations

import ast
import json
import pathlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps.secret_fields import SECRET_MASK

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"

_SECRET = "sk-proj-NOT-A-REAL-KEY-just-a-fixture"

_SCHEMA = {
    "type": "object",
    "properties": {
        "api_key": {"type": "string", "x-meta": {"label": "API Key", "sensitive": True}},
        "default_model": {"type": "string"},
    },
}


# ── behaviour: the five surfaces, driven through the real route table ──────────


class _FakeProviderConfig:
    type = "model"
    entity = ""
    capabilities: list[str] = []
    multiInstance = True
    settingsSchema = _SCHEMA


class _FakeExt:
    name = "fake-models"
    enabled = False  # keep the registry re-cycle / tool-refresh paths out of the way
    error = ""
    provider_config = _FakeProviderConfig()


class _FakeRegistry:
    def get(self, name):
        return _FakeExt() if name == "fake-models" else None

    def disable(self, name):  # pragma: no cover - not reached with enabled=False
        return None

    def enable(self, name):  # pragma: no cover - not reached with enabled=False
        return True


@asynccontextmanager
async def _client(tmp_path: Path):
    from personalclaw.providers import instance_routes

    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch("personalclaw.providers.registry.get_provider_registry", lambda: _FakeRegistry()),
    ):
        app = web.Application()
        instance_routes.register_instance_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client


_BASE = "/api/providers/fake-models/instances"


def _stored(tmp_path: Path, instance_id: str) -> dict:
    path = tmp_path / "extensions" / "fake-models" / "instances" / f"{instance_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))["config"]


async def _create(client, **config) -> tuple[str, str]:
    """Create an instance; return ``(id, raw response text)``."""
    r = await client.post(_BASE, json={"display_name": "Primary", "config": config})
    raw = await r.text()
    assert r.status == 201, raw
    return json.loads(raw)["instance"]["id"], raw


@pytest.mark.asyncio
async def test_create_does_not_echo_the_key_it_was_just_given(tmp_path):
    async with _client(tmp_path) as client:
        instance_id, raw = await _create(client, api_key=_SECRET, default_model="gpt-4o")
        assert _SECRET not in raw, "POST echoed the API key it had just been handed"
        body = json.loads(raw)["instance"]
        assert body["config"]["api_key"] == SECRET_MASK
        assert body["config"]["default_model"] == "gpt-4o", "a non-sensitive field passes through"
        assert body["_secret_set"] == ["api_key"]
        # …and the real value is on disk, unharmed.
        assert _stored(tmp_path, instance_id)["api_key"] == _SECRET


@pytest.mark.asyncio
async def test_the_list_route_masks_every_instance(tmp_path):
    """The worst of the five: no operator action, N secrets, one body."""
    async with _client(tmp_path) as client:
        await _create(client, api_key=_SECRET, default_model="gpt-4o")
        await _create(client, api_key=_SECRET + "-second", default_model="gpt-4o-mini")

        raw = await (await client.get(_BASE)).text()
        assert _SECRET not in raw, "the list route handed out stored API keys in the clear"
        instances = json.loads(raw)["instances"]
        assert len(instances) == 2, "the fixture did not produce two instances"
        for inst in instances:
            assert inst["config"]["api_key"] == SECRET_MASK
            assert inst["_secret_set"] == ["api_key"]


@pytest.mark.asyncio
async def test_the_single_read_route_masks(tmp_path):
    async with _client(tmp_path) as client:
        instance_id, _ = await _create(client, api_key=_SECRET)
        raw = await (await client.get(f"{_BASE}/{instance_id}")).text()
        assert _SECRET not in raw
        assert json.loads(raw)["instance"]["config"]["api_key"] == SECRET_MASK


@pytest.mark.asyncio
async def test_the_single_read_route_refuses_an_unregistered_provider(tmp_path):
    """Fail CLOSED. Without the extension there is no schema, so there is no way to know
    which fields are sensitive — and "no schema" must not be read as "nothing to withhold".
    Every sibling route on this surface already 404s here; this one used to answer with the
    raw stored config instead."""
    async with _client(tmp_path) as client:
        r = await client.get("/api/providers/not-registered/instances/anything")
        assert r.status == 404, await r.text()


@pytest.mark.asyncio
async def test_an_unregistered_provider_is_refused_even_when_the_instance_exists(tmp_path):
    """The same guard, pinned so the 404 cannot come from anywhere else.

    404 for a provider with no instances proves nothing: the missing-instance branch two
    lines below answers 404 too. Measured — a mutant replacing the registry refusal with
    ``schema = ext.provider_config.settingsSchema if ext else {}`` (fail OPEN: no schema,
    therefore nothing sensitive, therefore the stored config verbatim) left the test above
    GREEN. Plant the instance on disk under a name the registry does not know and the
    registry guard becomes the only thing that can refuse.
    """
    path = tmp_path / "extensions" / "ghost-models" / "instances" / "abc123.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": "abc123",
                "extension_name": "ghost-models",
                "display_name": "Ghost",
                "config": {"api_key": _SECRET},
                "enabled": True,
            }
        ),
        encoding="utf-8",
    )

    async with _client(tmp_path) as client:
        r = await client.get("/api/providers/ghost-models/instances/abc123")
        raw = await r.text()
        assert r.status == 404, raw
        assert _SECRET not in raw, "answered with a stored key for a provider it has no schema for"


@pytest.mark.asyncio
async def test_update_does_not_echo_the_key(tmp_path):
    async with _client(tmp_path) as client:
        instance_id, _ = await _create(client, api_key=_SECRET)
        r = await client.put(
            f"{_BASE}/{instance_id}",
            json={"config": {"api_key": _SECRET, "default_model": "gpt-4o-mini"}},
        )
        raw = await r.text()
        assert r.status == 200, raw
        assert _SECRET not in raw, "PUT echoed the key it had just been given"
        assert json.loads(raw)["instance"]["config"]["api_key"] == SECRET_MASK


@pytest.mark.asyncio
async def test_saving_the_mask_back_does_not_erase_the_stored_key(tmp_path):
    """The hazard masking the reads CREATES, and the reason the write half is not optional.

    ``MultiInstanceCard`` seeds its editor from the list route's config and PUTs the whole
    dict back. Once that config arrives masked, changing a model id would otherwise write
    ``••••••••`` over a working API key — a worse defect than the leak.
    """
    async with _client(tmp_path) as client:
        instance_id, _ = await _create(client, api_key=_SECRET, default_model="gpt-4o")
        r = await client.put(
            f"{_BASE}/{instance_id}",
            json={"config": {"api_key": SECRET_MASK, "default_model": "gpt-4o-mini"}},
        )
        assert r.status == 200, await r.text()
        stored = _stored(tmp_path, instance_id)
        assert stored["api_key"] == _SECRET, "the mask sentinel overwrote a working key"
        assert stored["default_model"] == "gpt-4o-mini", "the real edit was still saved"


@pytest.mark.asyncio
async def test_saving_a_blank_secret_over_a_stored_one_keeps_it(tmp_path):
    """The second shape the editor produces: the input is blanked, so a save sends "".","""
    async with _client(tmp_path) as client:
        instance_id, _ = await _create(client, api_key=_SECRET, default_model="gpt-4o")
        r = await client.put(
            f"{_BASE}/{instance_id}",
            json={"config": {"api_key": "", "default_model": "gpt-5"}},
        )
        assert r.status == 200, await r.text()
        stored = _stored(tmp_path, instance_id)
        assert stored["api_key"] == _SECRET
        assert stored["default_model"] == "gpt-5"


@pytest.mark.asyncio
async def test_a_real_rotated_key_still_overwrites(tmp_path):
    """Masking must not make a credential unchangeable."""
    async with _client(tmp_path) as client:
        instance_id, _ = await _create(client, api_key=_SECRET)
        r = await client.put(
            f"{_BASE}/{instance_id}", json={"config": {"api_key": "sk-ROTATED-fixture"}}
        )
        assert r.status == 200, await r.text()
        assert _stored(tmp_path, instance_id)["api_key"] == "sk-ROTATED-fixture"


@pytest.mark.asyncio
async def test_the_mask_sentinel_is_never_stored_as_a_credential(tmp_path):
    """A client handed a mask by a read route must not be able to persist the SENTINEL.

    Nothing is stored at create time, so there is no secret to preserve — but writing
    ``••••••••`` as the API key would produce an instance that looks configured and cannot
    work, and whose "credential" is a constant published in this repo.
    """
    async with _client(tmp_path) as client:
        instance_id, raw = await _create(client, api_key=SECRET_MASK, default_model="gpt-4o")
        assert "api_key" not in json.loads(raw)["instance"]["config"]
        assert "api_key" not in _stored(tmp_path, instance_id)


@pytest.mark.asyncio
async def test_a_refused_write_does_not_quote_the_submitted_value_back(tmp_path):
    """ARCC SAX-06 Outcome 3, "error handling without information disclosure".

    A 422 that echoed the offending body would re-leak what masking just withheld. Measured
    clean on the parked parent and pinned here: the validator reports the field's LABEL and
    what the schema expected, never what arrived.
    """
    async with _client(tmp_path) as client:
        r = await client.post(
            _BASE,
            json={"display_name": "Bad", "config": {"api_key": _SECRET, "default_model": 12345}},
        )
        raw = await r.text()
        assert r.status == 422, raw
        assert _SECRET not in raw, "a validation refusal quoted the submitted secret back"
        assert "12345" not in raw, "a validation refusal quoted the submitted value back"


# ── the derived rail: which routes carry a config, and do they all mask? ───────
#
# Nothing below names a route. The population is re-derived on every run from the stores'
# own type annotations and the handlers' own dataflow, because a hand-written list is the
# mechanism that let three of the five leaks above survive a fix aimed at the other two.

#: The modules whose public functions hand out a provider/extension config-or-instance
#: object. This is the census's one seed, and it is the smallest honest one: "a provider
#: config object" has to be defined somewhere, and it is defined by the modules that read,
#: write and transform the store. It is not a route list — no route name, method or path
#: appears anywhere in this file — and it is self-checking:
#: :func:`test_the_store_derivation_still_discriminates` fails if any of these stops existing
#: or stops exposing a config-bearing reader, and
#: :func:`test_no_config_carrying_wire_shape_escapes_the_census` independently catches a config
#: reaching the wire from a source this set does not know about.
#:
#: ``secret_fields`` belongs here for a reason that is easy to miss: ``mask_instance`` is
#: recognised as a MASKER, but ``preserve_unchanged_secrets`` RETURNS a config into which the
#: real stored credential has just been folded back. Omitting it would leave the very dict a
#: write route is about to persist untainted.
CONFIG_SOURCE_MODULES = {
    "personalclaw.providers.settings",
    "personalclaw.providers.instances",
    "personalclaw.providers.mcp_instances",
    "personalclaw.apps.app_config",
    "personalclaw.apps.secret_fields",
}

#: The masking owner. One module, by construction — pinned by
#: ``test_provider_config_secrets.py::test_the_masking_policy_has_exactly_one_implementation``.
MASKERS = {"mask_secrets", "mask_instance"}

#: Payload keys that carry a config-or-instance object on the wire. Used ONLY by the
#: independent completeness check, whose job is to catch a store ``CONFIG_SOURCE_MODULES`` does not
#: know. Keys are the wire vocabulary, not route identities: a new route that answers with a
#: ``config`` is caught by name-of-field, whatever its path is.
WIRE_KEYS = {"config", "instance", "instances"}

#: Vacuity floor for the walk. Measured at 1097 modules on this tree; a scan that walks a
#: handful would report zero violations for the wrong reason.
FILES_SCANNED_FLOOR = 800

#: Emitted values the census REFUSES to classify, because the config passed through a
#: consuming call on its way to the payload. A CEILING (may only shrink) — a site the scanner
#: declines to classify must not be a site it declines to COUNT, or laundering a config
#: through one helper would become the way around the rail above.
#:
#: Measured at **1** on this tree: ``providers/routes.py``'s ``handle_patch_config`` emits
#: ``errors = ProviderSettings.validate(body, schema)``, where ``body`` carries the restored
#: real secret. It is not a leak, and that is a MEASURED claim rather than an assumption —
#: ``apps/schema_validate.py`` builds every message from the field's label and the SCHEMA's
#: own expectations (``must be one of {enum}``, ``at least {min_len} characters``) and never
#: interpolates the submitted value. Driven against a live gateway, a 422 carrying a real key
#: answered ``["OpenAI API Key: must be a string"]``. The 1 is bought and then PINNED by
#: :func:`test_a_refused_write_does_not_quote_the_submitted_value_back`, so the slack cannot
#: later be spent on a genuine echo.
DECLASSIFIED_CEILING = 1

#: Vacuity floor for the derivation itself. Measured at **9** classified surfaces on this tree:
#: the five listed in the module docstring plus the four ``295b48f7b`` had already masked
#: (``api_app_config_get``/``_put`` and ``providers/routes.py``'s ``handle_get_config``/
#: ``handle_patch_config``). The floor is set below the measurement on purpose — it exists to
#: catch a taint analysis that stopped resolving, not to pin an exact population, and a new
#: config-carrying route is expected to raise the measured count without touching this number.
CONFIG_BEARING_SURFACE_FLOOR = 5


def _returns_a_config(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Does this store function hand out a config-or-instance object?

    Read off its OWN return annotation rather than listed anywhere, which is what makes
    ``delete_instance`` (``-> bool``), ``save`` (``-> None``), ``validate`` (``-> list[str]``)
    and ``config_path`` (``-> Path``) drop out without anybody deciding they should. An
    ``ExtensionInstance`` in any arity (bare, ``| None``, ``list[...]``) or a plain
    ``dict[str, Any]`` is a config.
    """
    if fn.name.startswith("_") or fn.returns is None:
        return False
    ann = ast.unparse(fn.returns)
    return "ExtensionInstance" in ann or ann.replace(" ", "") == "dict[str,Any]"


def _config_bearing_producers() -> dict[str, dict[str, Any]]:
    """``{store module: {"functions": {...}, "classes": {Class: {method, ...}}}}``.

    Module-level functions and class methods are kept APART because a seed has to be matched
    by its call spelling, not by its bare name. ``ExtensionInstance.to_dict`` returns
    ``dict[str, Any]`` and is therefore a producer — matched barely, it would make every
    ``.to_dict()`` in the tree a config source, which is exactly the false-positive flood the
    first draft of this census produced (~60 handlers, none of them provider surfaces).
    """
    out: dict[str, dict[str, Any]] = {}
    for dotted in sorted(CONFIG_SOURCE_MODULES):
        path = SRC.parent / (dotted.replace(".", "/") + ".py")
        info: dict[str, Any] = {"functions": set(), "classes": {}}
        out[dotted] = info
        if not path.is_file():  # surfaced by test_the_store_derivation_still_discriminates
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _returns_a_config(node):
                    info["functions"].add(node.name)
            elif isinstance(node, ast.ClassDef):
                methods = {
                    m.name
                    for m in node.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and _returns_a_config(m)
                }
                if methods:
                    info["classes"][node.name] = methods
    return out


def _seed_spellings(tree: ast.Module) -> set[str]:
    """How THIS module spells a call to a config-bearing producer.

    Resolved through the module's own imports — including the function-local imports these
    handlers actually use — so ``get_instance(...)``, ``_mcp.get_instance(...)`` and
    ``ProviderSettings.load(...)`` each resolve, while an unrelated local ``update()`` or a
    ``.to_dict()`` on some other object does not. Matching on ``ast.unparse(call.func)`` is
    what buys that: the spelling carries the binding, the bare name does not.
    """
    producers = _config_bearing_producers()
    seeds: set[str] = set()

    def _from_module(local: str, info: dict[str, Any]) -> None:
        for fname in info["functions"]:
            seeds.add(f"{local}.{fname}")
        for cls, methods in info["classes"].items():
            for method in methods:
                seeds.add(f"{local}.{cls}.{method}")

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                # `from <store> import <symbol>`
                info = producers.get(node.module)
                if info is not None:
                    if alias.name in info["functions"]:
                        seeds.add(local)
                    for method in info["classes"].get(alias.name, set()):
                        seeds.add(f"{local}.{method}")
                # `from <package> import <store module>`
                sub = producers.get(f"{node.module}.{alias.name}")
                if sub is not None:
                    _from_module(local, sub)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                info = producers.get(alias.name)
                if info is None:
                    continue
                # `import a.b.c` binds `a`, and calls spell the full dotted path.
                _from_module(alias.asname or alias.name, info)
    return seeds


@dataclass
class Surface:
    """One route handler that lets a config-or-instance object reach a response body."""

    module: str
    handler: str
    line: int
    #: ``(line, expression)`` for every tainted value reaching a payload WITHOUT masking.
    unmasked: list[tuple[int, str]] = field(default_factory=list)
    #: ``(line, binding)`` for every emitted value the census REFUSES to classify: it was
    #: derived from a config but through a consuming call, so whether it still carries a
    #: secret cannot be read statically. Loud by construction — see
    #: :data:`DECLASSIFIED_CEILING`.
    declassified: list[tuple[int, str]] = field(default_factory=list)

    @property
    def masked(self) -> bool:
        return not self.unmasked


@dataclass
class Census:
    files_scanned: int = 0
    surfaces: list[Surface] = field(default_factory=list)
    #: ``(module, line, key)`` for every ``json_response`` payload dict carrying a WIRE_KEYS
    #: key — the independent completeness net (see the module docstring).
    wire_shaped: list[tuple[str, int, str]] = field(default_factory=list)

    @property
    def violations(self) -> list[Surface]:
        return [s for s in self.surfaces if not s.masked]

    @property
    def declassifications(self) -> list[tuple[str, str, int, str]]:
        return [
            (s.module, s.handler, line, expr)
            for s in self.surfaces
            for line, expr in s.declassified
        ]


def _is_json_response(call: ast.Call) -> bool:
    f = call.func
    return (isinstance(f, ast.Attribute) and f.attr == "json_response") or (
        isinstance(f, ast.Name) and f.id == "json_response"
    )


def _spelling(call: ast.Call) -> str:
    """The callee exactly as written: ``f()`` → ``f``, ``_mcp.get_instance()`` →
    ``_mcp.get_instance``. Matched against :func:`_seed_spellings`, so the binding counts."""
    try:
        return ast.unparse(call.func)
    except Exception:  # pragma: no cover - defensive
        return ""


def _is_seed(call: ast.Call, seeds: set[str]) -> bool:
    """A call that hands out a RAW config.

    A masking call is excluded even though ``mask_instance`` matches the producer rule (it
    lives in a config-source module and returns ``dict[str, Any]``): what it hands back is a
    config with its secrets already withheld, so counting it as a raw source would report
    every correctly-masked route as a leak. ``_flow`` checks masker before seed for the same
    reason.
    """
    return not _is_masker(call) and _spelling(call) in seeds


def _is_masker(call: ast.Call) -> bool:
    """The masking owner, matched on the bare name — it is imported directly at every site
    and lives in exactly one module (pinned by ``test_provider_config_secrets.py``)."""
    f = call.func
    name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
    return name in MASKERS


def _mentions_seed(node: ast.AST, seeds: set[str]) -> bool:
    """Does this subtree CALL a config-bearing producer?"""
    return any(isinstance(n, ast.Call) and _is_seed(n, seeds) for n in ast.walk(node))


def _touches(value: ast.expr, tainted: set[str], seeds: set[str]) -> bool:
    """Does a config value appear ANYWHERE in this expression, however it is consumed?"""
    return _mentions_seed(value, seeds) or any(
        isinstance(n, ast.Name) and n.id in tainted for n in ast.walk(value)
    )


@dataclass
class _Flow:
    """What an expression does with a config value: carries it, masks it, or launders it."""

    carries: bool = False
    masked: bool = False


def _flow(value: ast.expr, tainted: set[str], seeds: set[str]) -> _Flow:
    """Whether *value* still IS a config object, and whether masking was applied.

    Deliberately strict about what propagates. Plumbing does — a name, an attribute, a
    subscript, a container, an ``await``, a producer call, a masking call. An arbitrary OTHER
    call does **not**, even when a tainted value is one of its arguments, because such a call
    returns a different object: ``ProviderSettings.validate(body, schema)`` is handed the
    config and answers with ``list[str]`` of schema complaints. The loose rule ("mentions a
    tainted name") was measured first and reported that validator's error list as a leaking
    config on a route that masks correctly.

    The blind spot this buys is real and is COUNTED rather than shrugged at: a config
    laundered through some other helper whose result is then emitted stops propagating here,
    so it is recorded as a declassification (see :attr:`Census.declassified`) instead of
    silently vanishing.
    """
    if isinstance(value, ast.Name):
        return _Flow(carries=value.id in tainted)
    if isinstance(value, (ast.Attribute, ast.Subscript, ast.Starred, ast.Await)):
        return _flow(value.value, tainted, seeds)
    if isinstance(value, ast.Call):
        if _is_masker(value):
            inner = any(_flow(a, tainted, seeds).carries for a in value.args) or _mentions_seed(
                value, seeds
            )
            return _Flow(carries=inner, masked=inner)
        if _is_seed(value, seeds):
            return _Flow(carries=True)
        return _Flow()  # a consuming call: whatever comes back is not the config
    if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        flows = [_flow(e, tainted, seeds) for e in value.elts]
        return _Flow(
            carries=any(f.carries for f in flows),
            masked=bool(flows) and all(f.masked for f in flows if f.carries),
        )
    if isinstance(value, ast.Dict):
        flows = [_flow(v, tainted, seeds) for v in value.values if v is not None]
        return _Flow(
            carries=any(f.carries for f in flows),
            masked=bool(flows) and all(f.masked for f in flows if f.carries),
        )
    if isinstance(value, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return _flow(value.elt, tainted, seeds)
    if isinstance(value, ast.DictComp):
        return _flow(value.value, tainted, seeds)
    if isinstance(value, ast.IfExp):
        body, orelse = _flow(value.body, tainted, seeds), _flow(value.orelse, tainted, seeds)
        return _Flow(
            carries=body.carries or orelse.carries,
            masked=(body.masked or not body.carries) and (orelse.masked or not orelse.carries),
        )
    return _Flow()


def _taint(fn: ast.AST, seeds: set[str]) -> tuple[set[str], set[str], dict[str, tuple[int, str]]]:
    """``(tainted, sanitized, declassified)`` for the names bound inside one handler.

    A name is TAINTED when it is bound to an expression that still *is* a config object
    (:func:`_flow`), and additionally SANITIZED when that expression came out of a masking
    call. DECLASSIFIED records the honest blind spot: the name was bound from an expression
    that touched a config but did not carry it — a consuming call. Iterated to a fixpoint, so
    binding order does not matter (``inst = get_instance(...)`` then
    ``wire = mask_instance(inst, ...)`` needs two passes).

    A comprehension target inherits its iterable's taint, which is what makes
    ``[mask_instance(inst, schema) for inst in instances]`` — the list route's real shape —
    resolve to a masked emit rather than to nothing.
    """
    tainted: set[str] = set()
    sanitized: set[str] = set()
    declassified: dict[str, tuple[int, str]] = {}

    def _bind(target: ast.expr, value: ast.expr, line: int) -> bool:
        flow = _flow(value, tainted, seeds)
        grew = False
        for leaf in ast.walk(target):
            if not isinstance(leaf, ast.Name):
                continue
            if flow.carries:
                if leaf.id not in tainted:
                    tainted.add(leaf.id)
                    grew = True
                if flow.masked and leaf.id not in sanitized:
                    sanitized.add(leaf.id)
                    grew = True
            elif _touches(value, tainted, seeds) and leaf.id not in declassified:
                declassified[leaf.id] = (line, ast.unparse(value))
                grew = True
        return grew

    while True:
        grew = False
        for node in ast.walk(fn):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                if node.value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    grew |= _bind(target, node.value, node.lineno)
            elif isinstance(node, ast.comprehension):
                grew |= _bind(node.target, node.iter, getattr(node.iter, "lineno", 0))
        if not grew:
            break
    # A name that ended up genuinely tainted is not also a declassification.
    for name in tainted:
        declassified.pop(name, None)
    return tainted, sanitized, declassified


def _emitted(node: ast.AST, masked: bool = False):
    """Yield ``(subexpression, masked)`` for everything in a payload that reaches the wire.

    A comprehension's **iterable and target are plumbing**, not wire values — only its element
    is serialized. Walking them as if they were is what made the correctly-masked list route
    read as a violation on the first measurement: ``[mask_instance(inst, schema) for inst in
    instances]`` emits masked records, but a naive ``ast.walk`` also sees the bare ``instances``
    and the bare loop target and calls each an unmasked config on the wire.
    """
    yield node, masked
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        yield from _emitted(node.elt, masked)
        return
    if isinstance(node, ast.DictComp):
        yield from _emitted(node.key, masked)
        yield from _emitted(node.value, masked)
        return
    inner = masked or (isinstance(node, ast.Call) and _is_masker(node))
    for child in ast.iter_child_nodes(node):
        yield from _emitted(child, inner)


def scan_source(source: str, rel: str, census: Census) -> None:
    """Classify one module's route handlers.

    Split out of :func:`scan` so the mechanism can be exercised against source written in
    this file — a census whose only test is "the tree currently reports zero" cannot tell a
    clean tree from a broken detector.
    """
    tree = ast.parse(source)
    census.files_scanned += 1
    seeds = _seed_spellings(tree)

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_json_response(node) and node.args):
            continue
        payload = node.args[0]
        if not isinstance(payload, ast.Dict):
            continue
        for key in payload.keys:
            if isinstance(key, ast.Constant) and key.value in WIRE_KEYS:
                census.wire_shaped.append((rel, node.lineno, key.value))

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tainted, sanitized, declassified = _taint(fn, seeds)
        if not (tainted or declassified):
            continue
        surface = Surface(module=rel, handler=fn.name, line=fn.lineno)
        reaches = False
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and _is_json_response(node) and node.args):
                continue
            for leaf, masked in _emitted(node.args[0]):
                if isinstance(leaf, ast.Name) and leaf.id in declassified:
                    line, expr = declassified[leaf.id]
                    surface.declassified.append((line, f"{leaf.id} = {expr}"))
                    continue
                carries = (isinstance(leaf, ast.Name) and leaf.id in tainted) or (
                    isinstance(leaf, ast.Call) and _is_seed(leaf, seeds)
                )
                if not carries:
                    continue
                reaches = True
                clean = masked or (isinstance(leaf, ast.Name) and leaf.id in sanitized)
                if not clean:
                    surface.unmasked.append((node.lineno, ast.unparse(leaf)))
        if reaches or surface.declassified:
            census.surfaces.append(surface)


def scan() -> Census:
    """Walk every module under ``src/personalclaw`` and classify its config-bearing routes."""
    census = Census()
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        try:
            scan_source(source, str(path.relative_to(SRC.parent.parent)), census)
        except SyntaxError:  # pragma: no cover - defensive
            continue
    return census


# ── vacuity: a census that measures nothing must not read as clean ────────────


def test_the_store_derivation_still_discriminates():
    """The seed of the whole census: the stores still say which of their functions hand out
    a config, and the annotation rule still tells those apart from the ones that do not.

    Without this, a renamed store or a dropped annotation would empty ``seeds``, every
    handler would taint nothing, and the violation ratchet below would pass triumphantly
    while measuring an empty set.
    """
    producers = _config_bearing_producers()
    assert set(producers) == CONFIG_SOURCE_MODULES, producers
    for dotted, info in producers.items():
        assert info["functions"] or info["classes"], (
            f"{dotted} exposes no config-bearing reader at all — it moved, or its return "
            f"annotations were dropped, and every seed derived from it is now empty"
        )

    settings = producers["personalclaw.providers.settings"]
    instances = producers["personalclaw.providers.instances"]
    app_config = producers["personalclaw.apps.app_config"]

    # The positive: the readers the shipped handlers actually call.
    assert {"load", "update"} <= settings["classes"].get("ProviderSettings", set())
    assert {"list_instances", "get_instance", "create_instance", "update_instance"} <= instances[
        "functions"
    ]
    assert {"read_config", "write_config"} <= app_config["functions"]

    # The negative, which is what makes the rule a rule and not a name filter: these return a
    # bool / None / list[str] / Path, so they cannot carry a secret to the wire. A census that
    # flagged the DELETE route would be demanding masking on a `{"ok": true}` body — which is
    # how a rail teaches people to route around it.
    assert "delete_instance" not in instances["functions"]
    for dropped in ("save", "validate", "config_path"):
        assert dropped not in settings["classes"].get("ProviderSettings", set()), dropped

    # And the reason seeds are matched by SPELLING rather than by name: `to_dict` IS a
    # producer, so a bare-name match would make every `.to_dict()` in the tree a config
    # source. Measured on the first draft: ~60 unrelated handlers, none of them provider
    # surfaces. It must stay reachable only as `ExtensionInstance.to_dict`.
    assert "to_dict" in instances["classes"].get("ExtensionInstance", set())
    assert "to_dict" not in instances["functions"]


def test_the_scan_is_not_vacuous():
    """Every assertion below is only as good as the walk that produced it."""
    census = scan()
    assert census.files_scanned >= FILES_SCANNED_FLOOR, (
        f"the census walked only {census.files_scanned} modules (floor "
        f"{FILES_SCANNED_FLOOR}) — it lost its root, so 'no violations' means nothing. "
        f"Check SRC={SRC}."
    )
    assert len(census.surfaces) >= CONFIG_BEARING_SURFACE_FLOOR, (
        f"the census found only {len(census.surfaces)} route handlers carrying a provider/"
        f"extension config to the wire (floor {CONFIG_BEARING_SURFACE_FLOOR}). The taint "
        f"analysis stopped resolving, so the ratchet below is passing on an empty set. "
        f"Found: {[(s.module, s.handler) for s in census.surfaces]}"
    )
    assert census.wire_shaped, "no config-shaped payload found at all — the matcher is broken"


_SYNTHETIC = '''
from aiohttp import web

from personalclaw.apps.secret_fields import mask_instance, mask_secrets
from personalclaw.providers.instances import delete_instance, get_instance, list_instances
from personalclaw.providers.settings import ProviderSettings


async def leaks_an_instance(request):
    inst = get_instance("x", "y")
    return web.json_response({"instance": inst.to_dict()})


async def leaks_a_list(request):
    instances = list_instances("x")
    return web.json_response({"instances": [i.to_dict() for i in instances]})


async def leaks_a_config_through_a_local(request):
    stored = ProviderSettings.load("x")
    return web.json_response({"config": stored})


async def masks_an_instance(request):
    inst = get_instance("x", "y")
    return web.json_response({"instance": mask_instance(inst, {})})


async def masks_a_config_through_a_local(request):
    masked, secret_set = mask_secrets(ProviderSettings.load("x"), {})
    return web.json_response({"config": masked, "_secret_set": secret_set})


async def carries_no_config(request):
    """A carrier that returns a bool must not be taken for a config.

    This is the shape DELETE has, and a census that flagged it would demand masking on a
    route that answers ``{"ok": true}`` — which is how a rail teaches people to ignore it.
    """
    deleted = delete_instance("x", "y")
    return web.json_response({"ok": deleted})
'''


def test_the_census_counts_a_leak_and_clears_a_masked_route():
    """The detector, proved on source written here — both directions.

    "Finds leaks" alone is satisfiable by a census that calls everything a leak, so the
    masked pair must come back clean in the same pass, and the ``-> bool`` handler must not
    be classified as a config surface at all.
    """
    census = Census()
    scan_source(_SYNTHETIC, "synthetic.py", census)
    by_name = {s.handler: s for s in census.surfaces}

    for leaky in ("leaks_an_instance", "leaks_a_list", "leaks_a_config_through_a_local"):
        assert leaky in by_name, f"{leaky} was not classified as a config-bearing surface"
        assert not by_name[leaky].masked, f"{leaky} reads as masked — the detector is blind"

    for clean in ("masks_an_instance", "masks_a_config_through_a_local"):
        assert clean in by_name, f"{clean} was not classified as a config-bearing surface"
        assert by_name[clean].masked, (
            f"{clean} routes through secret_fields and still reads as a violation: "
            f"{by_name[clean].unmasked}"
        )

    assert "carries_no_config" not in by_name, (
        "a handler whose only store call returns a bool was classified as carrying a config; "
        "the annotation-derived seed set is not discriminating"
    )


#: The line the plant swaps out — a REAL masked emit in the shipped instance list route.
_INSTANCE_ROUTES = SRC / "providers" / "instance_routes.py"
_MASKED_LINE = '            "instances": [mask_instance(inst, schema) for inst in instances],'
_PLANTED_LINE = '            "instances": [inst.to_dict() for inst in instances],'


def test_reverting_the_mask_in_instance_routes_is_counted():
    """The vacuity floor for "violations == 0".

    Zero violations is satisfied by a working census AND by one that has stopped detecting,
    and telling those apart is the entire job here. The synthetic pair above proves the
    mechanism on source written in this file; this proves it on the source that actually
    ships, through the exact expression that actually leaked — a comprehension over a
    tainted iterable, whose target inherits taint only because ``_taint`` walks
    ``ast.comprehension``. A synthetic module cannot vouch for that.

    Nothing is written to disk: the plant is a string swap on source read into memory.
    """
    real = _INSTANCE_ROUTES.read_text(encoding="utf-8")

    # 1) The module as it ships is clean, so a red below is the plant and not the tree.
    baseline = Census()
    scan_source(real, "providers/instance_routes.py", baseline)
    assert baseline.surfaces, "the census classified no surface in instance_routes.py at all"
    assert not baseline.violations, (
        "instance_routes.py already reports a violation, so this floor cannot attribute the "
        f"plant: {[(s.handler, s.unmasked) for s in baseline.violations]}"
    )

    # 2) The plant APPLIED. A swap that silently matched nothing would make step 3 a
    #    tautology about unmodified source — the exact shape of a vacuous guard.
    planted = real.replace(_MASKED_LINE, _PLANTED_LINE, 1)
    assert planted != real, (
        f"the planted-regression swap matched nothing in {_INSTANCE_ROUTES.name}; "
        f"_MASKED_LINE has drifted from the shipped source, so this floor measures nothing. "
        f"Re-anchor it on a live mask_instance(...) line."
    )
    assert len(planted) != len(real), "the swap applied but changed no bytes"

    # 3) The detector sees it.
    census = Census()
    scan_source(planted, "providers/instance_routes.py", census)
    assert len(census.violations) == 1, (
        "reverting a real mask_instance call to the raw disk serializer was NOT counted, so "
        "the zero-violation ratchet is measuring the detector rather than the code. "
        f"Surfaces: {[(s.handler, s.unmasked) for s in census.surfaces]}"
    )
    assert census.violations[0].handler == "handle_list_instances", census.violations[0]


# ── the ratchets ──────────────────────────────────────────────────────────────


def test_no_route_hands_a_provider_config_to_the_wire_unmasked():
    """The rail. Derived population, zero tolerance.

    Every route handler that lets a config-or-instance object reach a ``json_response``
    payload must route it through :mod:`personalclaw.apps.secret_fields`. Five handlers
    failed this on the parked parent; the point of deriving the population rather than
    listing it is that a sixth is classified without anyone remembering to add it here.
    """
    census = scan()
    assert not census.violations, (
        "a route hands a provider/extension config to the wire without masking its "
        "x-meta.sensitive fields. Mask it with apps.secret_fields.mask_secrets (a config "
        "dict) or mask_instance (an instance record) — and if it also WRITES, pair that with "
        "preserve_unchanged_secrets or the first save will erase the stored credential:\n"
        + "\n".join(
            f"  {s.module}:{ln} {s.handler}() → {expr}"
            for s in census.violations
            for ln, expr in s.unmasked
        )
    )


def test_values_the_census_cannot_classify_do_not_grow():
    """The scanner's own blind spot, held to a ceiling instead of a shrug.

    Every row here is a payload value derived from a config through a *consuming* call, so
    whether it still carries a secret is not statically readable. That is precisely where a
    new leak would go to avoid the rail above, which is why growth reds: a config reaching the
    wire must either still be a config (and be masked) or be refused here (and be counted).
    """
    census = scan()
    assert census.files_scanned >= FILES_SCANNED_FLOOR, (
        f"walked only {census.files_scanned} modules (floor {FILES_SCANNED_FLOOR}) — a "
        f"shrunken walk makes this ceiling meaningless."
    )
    found = census.declassifications
    assert len(found) <= DECLASSIFIED_CEILING, (
        f"{len(found)} emitted values are derived from a provider config through a consuming "
        f"call (ceiling {DECLASSIFIED_CEILING}), so the rail cannot vouch for them. Build the "
        f"response value from the masked config at the call site instead:\n"
        + "\n".join(f"  {m}:{ln} {h}() → {e}" for m, h, ln, e in found)
    )


def test_every_config_bearing_surface_also_preserves_secrets_if_it_writes():
    """Masking a read without guarding the matching write is a worse bug than the leak.

    Derived the same way: a handler that carries a config to the wire AND is bound to a
    mutating verb must reference ``preserve_unchanged_secrets``, because the form that reads
    a masked config is the form that PUTs it back. Route METHODS are read off the router
    registrations, so no method is hard-coded per handler here.
    """
    writers: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        rel = str(path.relative_to(SRC.parent.parent))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            verb = node.func.attr
            if verb not in ("add_post", "add_put", "add_patch") or len(node.args) < 2:
                continue
            handler = node.args[1]
            name = handler.id if isinstance(handler, ast.Name) else getattr(handler, "attr", "")
            if name:
                writers.setdefault(rel, set()).add(name)

    census = scan()
    offenders: list[str] = []
    for surface in census.surfaces:
        if surface.handler not in writers.get(surface.module, set()):
            continue
        source = (SRC.parent.parent / surface.module).read_text(encoding="utf-8")
        fn = next(
            n
            for n in ast.walk(ast.parse(source))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == surface.handler
        )
        if "preserve_unchanged_secrets" not in ast.unparse(fn):
            offenders.append(f"  {surface.module} {surface.handler}()")

    assert not offenders, (
        "a mutating route carries a provider config to the wire but never calls "
        "preserve_unchanged_secrets. Because the reads are masked, the form that saves this "
        "config PUTs the mask back — so the first save of an unrelated field writes "
        f"'{SECRET_MASK}' over a working credential:\n" + "\n".join(offenders)
    )


def test_no_config_carrying_wire_shape_escapes_the_census():
    """The independent completeness net for ``CONFIG_SOURCE_MODULES``.

    The taint census above is only as complete as its seed: a config served from a store
    nobody added to ``CONFIG_SOURCE_MODULES`` would taint nothing and pass. This check derives from
    the WIRE instead — every ``json_response`` payload carrying a ``config``/``instance``/
    ``instances`` key — and asserts each such module is one the taint census classified. The
    two derivations cover each other's blind spot: a new store reds HERE, a new route on a
    known store reds above.
    """
    census = scan()
    shaped = {module for module, _ln, _key in census.wire_shaped}
    classified = {s.module for s in census.surfaces}
    unexplained = sorted(shaped - classified)
    assert not unexplained, (
        "a route answers with a config/instance-shaped payload that the taint census never "
        "classified — so its secrets are not covered by the rail above. Either it serves a "
        "config store missing from CONFIG_SOURCE_MODULES (add it) or it builds the payload through "
        "indirection the taint analysis cannot follow (build it at the call site):\n"
        + "\n".join(f"  {m}" for m in unexplained)
    )

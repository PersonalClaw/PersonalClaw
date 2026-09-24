"""AECO-2 — the per-source spec reaches an APP-bundled knowledge provider.

The ceiling this removes, measured: a core poll-capable provider is constructed with a
``KnowledgeStore`` handle and reads its own source row (``dir_source``/``feed_source``/
``web_source``/``connector_pack`` all open ``poll`` with ``self._store.get_source(...)``),
so it has always had the row's validated ``spec``. An APP provider reaches core only through
``personalclaw.sdk.*`` and holds no store, so all it ever got was a ``source_id`` it could
not resolve — which left a per-INSTALL setting as its only configuration and capped one
install at ONE watched source. ``git-repo`` (AECO-1) is that exact shape: its ``repo`` comes
from app settings while every source row it polls carries ``spec={}``.

So the atom's claims are counting and provenance claims, and each test below asserts one:

* TWO sources of ONE provider instance, polled through the real engine, each yield items
  attributable to ITS OWN spec — the test the per-install shape structurally cannot pass;
* the delivered spec is re-read at POLL time, so a row edited mid-tick reaches the provider
  current rather than as the snapshot ``tick()`` took at the top (the value would otherwise
  be threaded correctly from the wrong source);
* a provider declaring NEITHER extra is called exactly as before — byte-identical items,
  and no keyword at all — which is what makes this additive across the SDK boundary into
  already-installed apps rather than a break;
* ONE negotiation covers both extras, so ``poll`` keeps one contract;
* what crosses the boundary is a private, JSON-native ``dict`` — no core object, and a
  provider that mutates it cannot reach the engine's row.

Everything here imports the provider surface the way an APP must (``personalclaw.sdk.*``)
so the boundary this atom widens is exercised through the published facade, not the core
module path.
"""

import pytest

from personalclaw.knowledge.source_engine import SourceEngine
from personalclaw.knowledge.store import KnowledgeStore

# Imported through the SDK deliberately: an app may use no other path, so a test that
# reached for `personalclaw.knowledge_providers.base` would prove the mechanism works for
# core and leave the atom's actual claim (it works for an APP) unmeasured.
from personalclaw.sdk.knowledge import (
    ENGINE_POLL_KWARGS,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
    resolve_source_spec,
)

PROVIDER_NAME = "spec-aware-app"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


class _FakeQueue:
    def __init__(self):
        self.enqueued: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.enqueued.append(item_id)

    def recover_pending(self) -> int:
        return 0


def _cfg(**over):
    from personalclaw.config.loader import SourcesConfig

    base = dict(
        enabled=True,
        poll_interval_default_secs=1,
        network_floor_secs=0,
        max_sources=100,
        max_items_per_poll=50,
    )
    base.update(over)
    return SourcesConfig(**base)


class _SpecAwareProvider(KnowledgeSourceProvider):
    """An app-shaped provider: ONE instance, per-install defaults, per-source spec.

    Stands in for ``git-repo``-after-adoption. It emits one item per configured
    ``workspace``, titled from the RESOLVED spec, so an item's provenance names the source
    row that produced it — the only way to tell "each source got its own spec" apart from
    "both sources got the same spec twice".
    """

    SPEC_KEYS = ("workspace", "label")

    def __init__(self, *, workspace: str = "", label: str = "install-default"):
        self._settings = {"workspace": workspace, "label": label}
        self.seen: list[tuple[str, dict]] = []

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Spec-aware App"

    async def list_sources(self):
        return []

    async def search(self, query: str, limit: int = 10):
        return []

    async def get_item(self, item_id: str):
        return None

    def validate_spec(self, spec: dict) -> tuple[bool, str]:
        _, err = resolve_source_spec(spec, defaults=self._settings, allowed=self.SPEC_KEYS)
        return (False, err) if err else (True, "")

    async def poll(self, source_id: str, cursor: str = "", *, spec=None, policy=None):
        cfg, err = resolve_source_spec(spec, defaults=self._settings, allowed=self.SPEC_KEYS)
        self.seen.append((source_id, spec if spec is None else dict(spec)))
        if err:
            return SourcePollResult(cursor=cursor, error=err)
        workspace = str(cfg.get("workspace") or "")
        if not workspace:
            return SourcePollResult(cursor=cursor, error="no workspace configured")
        return SourcePollResult(
            items=[
                SourceItem(
                    guid=f"{workspace}/doc-1",
                    title=f"{cfg['label']}: {workspace}",
                    content=f"content of {workspace}",
                )
            ],
            cursor=f"seen:{workspace}",
        )


class _PlainProvider(KnowledgeSourceProvider):
    """The pre-AECO-2 shape: the two-argument base contract, declaring no extra.

    Its whole job is the additive claim — it records the keywords it was handed so "called
    exactly as before" is asserted on the CALL, not inferred from the items coming out.
    """

    def __init__(self):
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "plain-app"

    @property
    def display_name(self) -> str:
        return "Plain App"

    async def list_sources(self):
        return []

    async def search(self, query: str, limit: int = 10):
        return []

    async def get_item(self, item_id: str):
        return None

    async def poll(self, source_id: str, cursor: str = ""):
        self.calls.append({"source_id": source_id, "cursor": cursor})
        return SourcePollResult(
            items=[SourceItem(guid="fixed-guid", title="fixed title", content="fixed content")],
            cursor="fixed-cursor",
        )


class _PolicyOnlyProvider(_PlainProvider):
    """Declares ``policy`` and not ``spec`` — the WS-3+ fetching shape, unchanged."""

    @property
    def name(self) -> str:
        return "policy-app"

    async def poll(self, source_id: str, cursor: str = "", *, policy=None):
        self.calls.append({"source_id": source_id, "cursor": cursor, "policy": policy})
        return SourcePollResult(cursor=cursor)


def _engine(store, provider):
    queue = _FakeQueue()
    engine = SourceEngine(
        store,
        queue,
        providers_lister=lambda: [provider],
        config_loader=lambda: _cfg(),
    )
    return engine, queue


def _titles(store, sid):
    return [
        r["title"]
        for r in store.db.execute(
            "SELECT title FROM items WHERE source_id = ? ORDER BY guid", (sid,)
        ).fetchall()
    ]


# ── clause 1: two sources of ONE provider, each attributable to ITS spec ──────────────


@pytest.mark.asyncio
async def test_two_sources_of_one_provider_each_poll_its_own_spec(store):
    """The done_when's headline: one install, two sources, two different specs.

    Both rows name the same ``provider``, and one provider INSTANCE (one set of install
    settings) serves both — the per-install shape would emit the same workspace twice.
    """
    provider = _SpecAwareProvider(workspace="", label="acme")
    engine, _queue = _engine(store, provider)
    first = store.create_source(
        name="alpha", provider=PROVIDER_NAME, kind="app", spec={"workspace": "alpha-ws"}
    )
    second = store.create_source(
        name="beta", provider=PROVIDER_NAME, kind="app", spec={"workspace": "beta-ws"}
    )

    assert await engine.poll_source(store.get_source(first), _cfg()) == 1
    assert await engine.poll_source(store.get_source(second), _cfg()) == 1

    # Provenance, not just a count: each row's items carry ITS workspace.
    assert _titles(store, first) == ["acme: alpha-ws"]
    assert _titles(store, second) == ["acme: beta-ws"]
    # And the two polls were handed two DIFFERENT specs, keyed by source id.
    assert provider.seen == [
        (first, {"workspace": "alpha-ws"}),
        (second, {"workspace": "beta-ws"}),
    ]
    # The cursors are per-source too, so neither poll's position overwrites the other's.
    assert store.get_source_cursor(first) == "seen:alpha-ws"
    assert store.get_source_cursor(second) == "seen:beta-ws"


@pytest.mark.asyncio
async def test_the_per_install_setting_alone_cannot_tell_two_sources_apart(store):
    """The falsification the done_when names, asserted rather than asserted-about.

    With the spec EMPTY on both rows, the install default is all the provider has — and the
    two sources collide on one guid, so the second poll indexes nothing new. This is the
    ``git-repo``-at-AECO-1 shape, and it failing here is what makes the test above a real
    discrimination instead of a tautology.
    """
    provider = _SpecAwareProvider(workspace="only-ws", label="acme")
    engine, _queue = _engine(store, provider)
    first = store.create_source(name="a", provider=PROVIDER_NAME, kind="app", spec={})
    second = store.create_source(name="b", provider=PROVIDER_NAME, kind="app", spec={})

    await engine.poll_source(store.get_source(first), _cfg())
    await engine.poll_source(store.get_source(second), _cfg())

    assert _titles(store, first) == ["acme: only-ws"]
    assert _titles(store, second) == ["acme: only-ws"]  # the SAME upstream, twice over


# ── the delivered value's SOURCE, not merely its thread ───────────────────────────────


@pytest.mark.asyncio
async def test_delivered_spec_is_reread_at_poll_time_not_the_tick_snapshot(store):
    """``tick()`` snapshots every due row once, then polls them in sequence.

    A row edited while a long tick is in flight must reach its provider CURRENT — a core
    provider never sees that skew because it re-reads inside ``poll``. Driven for real:
    polling the first source edits the second's spec, so the second poll in the same tick
    is the one that would have been handed a stale value.
    """
    provider = _SpecAwareProvider(label="acme")
    engine, _queue = _engine(store, provider)
    first = store.create_source(
        name="a", provider=PROVIDER_NAME, kind="app", spec={"workspace": "first-ws"}
    )
    second = store.create_source(
        name="b", provider=PROVIDER_NAME, kind="app", spec={"workspace": "stale-ws"}
    )

    real_poll = provider.poll
    edited = {"done": False}

    async def _poll_then_edit(source_id, cursor="", *, spec=None, policy=None):
        result = await real_poll(source_id, cursor, spec=spec, policy=policy)
        if not edited["done"]:
            edited["done"] = True
            store.update_source(second, spec={"workspace": "fresh-ws"})
        return result

    provider.poll = _poll_then_edit  # type: ignore[method-assign]
    await engine.tick()

    assert _titles(store, first) == ["acme: first-ws"]
    # The edit landed BETWEEN the snapshot and this poll, and the fresh value is what
    # arrived — `stale-ws` here would mean the tick's dict was delivered.
    assert _titles(store, second) == ["acme: fresh-ws"]


@pytest.mark.asyncio
async def test_a_provider_mutating_the_delivered_spec_cannot_reach_the_row(store):
    """It crosses into provider code, so what it gets must be nobody else's object.

    A provider that ``pop``s its own options while parsing them is ordinary; that reaching
    back into the row the engine goes on to record a poll against would not be. MEASURED,
    not assumed to need a defensive copy: the engine takes no copy, because
    ``KnowledgeStore._serialize_source`` ``json.loads`` the column on every read, so every
    delivery is already a fresh object graph. Asserted BOTH ways below — the property, and
    the per-read freshness that is the only reason the property holds — so the day a row
    cache lands in the store, this reds instead of quietly sharing state with an app.
    """
    provider = _SpecAwareProvider(label="acme")

    async def _poll_and_wreck(source_id, cursor="", *, spec=None, policy=None):
        spec.clear()
        spec["workspace"] = "hijacked"
        spec.setdefault("nested", {})["deep"] = True
        return SourcePollResult(cursor=cursor)

    provider.poll = _poll_and_wreck  # type: ignore[method-assign]
    engine, _queue = _engine(store, provider)
    sid = store.create_source(
        name="a", provider=PROVIDER_NAME, kind="app", spec={"workspace": "mine", "nested": {}}
    )

    await engine.poll_source(store.get_source(sid), _cfg())

    assert store.get_source(sid)["spec"] == {"workspace": "mine", "nested": {}}
    # The mechanism, asserted directly: two deliveries of one row share no object, all the
    # way down. A cached row would satisfy the row-unchanged assert above and fail here.
    first, second = engine._source_spec(sid), engine._source_spec(sid)
    assert first == second
    assert first is not second
    assert first["nested"] is not second["nested"]


@pytest.mark.asyncio
async def test_the_delivered_spec_is_a_plain_json_native_dict(store):
    """Clause 3, as a property of the value rather than of the import graph.

    An accessor that handed over a core object — a row wrapper, a ``sqlite3.Row``, a store
    handle hanging off it — would make the app's code depend on a core internal without any
    import for the boundary lint to catch.
    """
    provider = _SpecAwareProvider(label="acme")
    engine, _queue = _engine(store, provider)
    sid = store.create_source(
        name="a",
        provider=PROVIDER_NAME,
        kind="app",
        spec={"workspace": "w", "label": "row", "n": 3, "on": True, "xs": [1, "two"]},
    )

    await engine.poll_source(store.get_source(sid), _cfg())

    _source_id, delivered = provider.seen[-1]
    assert type(delivered) is dict
    stack = [delivered]
    while stack:
        node = stack.pop()
        for key, value in node.items():
            assert type(key) is str
            assert type(value) in (str, int, float, bool, dict, list, type(None)), value
            if isinstance(value, dict):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(v for v in value if isinstance(v, dict))


# ── clause 4: one contract, and the old shape untouched ───────────────────────────────


@pytest.mark.asyncio
async def test_a_provider_declaring_no_extra_is_called_with_no_keyword(store):
    """Additive, asserted at the CALL: the two-argument contract is passed two arguments.

    An engine that passed ``spec=`` unconditionally would ``TypeError`` here — and because
    ``poll_source`` swallows a provider fault into a degraded health status, that break
    would have shown up as a quietly unhealthy source rather than a failure.
    """
    provider = _PlainProvider()
    engine, queue = _engine(store, provider)
    sid = store.create_source(name="a", provider="plain-app", kind="app", spec={"ignored": 1})

    assert await engine.poll_source(store.get_source(sid), _cfg()) == 1

    assert provider.calls == [{"source_id": sid, "cursor": ""}]
    assert _titles(store, sid) == ["fixed title"]
    assert len(queue.enqueued) == 1
    assert store.get_source(sid)["health_status"] == "ok"


@pytest.mark.asyncio
async def test_policy_only_provider_still_receives_only_policy(store):
    """One negotiation, both extras: the ``policy`` half is unchanged by the ``spec`` half."""
    provider = _PolicyOnlyProvider()
    engine, _queue = _engine(store, provider)
    sid = store.create_source(name="a", provider="policy-app", kind="app", spec={"k": "v"})

    await engine.poll_source(store.get_source(sid), _cfg())

    (call,) = provider.calls
    assert set(call) == {"source_id", "cursor", "policy"}
    assert call["policy"] is not None


def test_the_engine_negotiates_exactly_the_declared_extras(store):
    """``_poll_kwargs`` is the ONE place extras are chosen, for all of ENGINE_POLL_KWARGS.

    The last line is the rail that keeps the ABC's documented vocabulary honest: the engine
    iterates ``ENGINE_POLL_KWARGS`` and looks each name up in its supplier table, so a name
    added to the contract without a supplier raises here rather than becoming a keyword the
    ABC promises and no provider can ever be handed.
    """
    engine, _queue = _engine(store, _PlainProvider())
    sid = store.create_source(name="a", provider="plain-app", kind="app", spec={"k": "v"})

    assert engine._poll_kwargs(_PlainProvider(), sid) == {}
    assert set(engine._poll_kwargs(_PolicyOnlyProvider(), sid)) == {"policy"}
    assert set(ENGINE_POLL_KWARGS) == {"spec", "policy"}
    assert set(engine._poll_kwargs(_SpecAwareProvider(), sid)) == set(ENGINE_POLL_KWARGS)


def test_the_engine_has_exactly_one_poll_call_site():
    """Clause 4 structurally: no second ``provider.poll`` shape left behind.

    Two call sites is how this started — ``policy`` in one, bare in the other — and adding
    the spec that way would have made four for one contract.
    """
    from pathlib import Path

    import personalclaw.knowledge.source_engine as mod

    body = Path(mod.__file__).read_text(encoding="utf-8")
    assert body.count("await provider.poll(") == 1
    assert "**self._poll_kwargs(" in body


# ── resolve_source_spec: the published accessor, fail-CLOSED ──────────────────────────


def test_resolve_source_spec_row_wins_over_the_install_default():
    resolved, err = resolve_source_spec(
        {"workspace": "per-source"},
        defaults={"workspace": "per-install", "label": "kept"},
        allowed=("workspace", "label"),
    )
    assert err == ""
    assert resolved == {"workspace": "per-source", "label": "kept"}


def test_resolve_source_spec_refuses_an_unknown_key():
    """A typo must be a refusal, not a silent fall-back to the install default — which
    would poll the wrong upstream forever while looking configured."""
    resolved, err = resolve_source_spec(
        {"workspaces": "typo"}, defaults={"workspace": "d"}, allowed=("workspace",)
    )
    assert resolved == {}
    assert "unknown key(s) ['workspaces']" in err


def test_resolve_source_spec_treats_a_blank_as_inherit():
    """An empty field in a create form means "use the install setting", not "clear it"."""
    resolved, err = resolve_source_spec(
        {"workspace": "", "label": None}, defaults={"workspace": "d", "label": "l"}
    )
    assert err == ""
    assert resolved == {"workspace": "d", "label": "l"}


def test_resolve_source_spec_refuses_a_non_object():
    resolved, err = resolve_source_spec(["not", "an", "object"])  # type: ignore[arg-type]
    assert resolved == {}
    assert err == "spec must be an object"


def test_resolve_source_spec_with_no_allowed_list_stays_open():
    """``allowed`` is opt-in: a provider that has not closed its key set is not silently
    given a closed one, because that would refuse specs it used to accept."""
    resolved, err = resolve_source_spec({"anything": 1}, defaults={"a": 2})
    assert err == ""
    assert resolved == {"a": 2, "anything": 1}

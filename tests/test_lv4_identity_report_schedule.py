"""LV-4's schedule half: the clock job, the cadence, and what ``off`` actually stops.

`tests/test_lv4_identity_report.py` covers the composition and delivery half. This file covers
the three clauses that were left `todo`, and each one is written against the failure it exists to
catch rather than against the code that implements it:

1. **"compressed-clock fixture fires the job."** The proof is that the report is *PRODUCED*, not
   that a row was armed. A whole trigger subsystem in this repo once shipped where every fire
   reached a mailbox nobody opened, so an "enqueued" assertion is worth nothing here. Two legs:
   the armed row is SELECTED by the real ``service.due_ids`` when a compressed clock passes its
   fire time, and driving it through the real ``GatewayOrchestrator._fire_store_trigger`` leaves a
   versioned artifact and one inbox row on disk.

2. **"cadence monthly | weekly | off."** Asserted as a CONVERGENCE (a cadence change reaches the
   spec without the user touching a trigger) and as ONE vocabulary — the same three words in
   `learning_report`, in the PATCH allowlist and in the frontend strip, compared to each other
   rather than to three hand-written copies.

3. **"`off` disables cleanly."** Two independent refusals, and they must fail INDEPENDENTLY of the
   weekly legs or the pair is measuring one thing: the reconciler disables the row (so nothing is
   selected) *and* ``execute`` returns before composing anything (so a row a user re-enabled by
   hand still produces nothing). "Fires and discards" is not a pass — the artifact store and the
   inbox are both inspected for absence, against a seeded home that WOULD have produced a report.

Plus the two round-trip points `test_config_roundtrip.py` provably cannot see: the write path (the
real PATCH handler, end to end onto disk and back through ``AppConfig.load``) and the frontend
control (asserted at its call site here, and driven by a click in
`web/src/pages/learning/identityReportCadence.test.tsx`).

🔴 **One DISCOVERY drove a change to shipped code.** ``delivery_dedup_key`` was hardcoded to the
calendar month, and `emit_attention_item` returns the existing open row and fires no second
notification for a repeated key — so weeks 2, 3 and 4 of every month would have written a new
artifact version and told nobody. It is keyed on the report's own period now, and both directions
are railed below.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw import learning_report as LR
from personalclaw.action_providers import identity_report_provider as P
from personalclaw.action_providers.base import ActionContext
from personalclaw.skills import loader as loader_mod
from personalclaw.skills.loader import AutoSkillProvenance, SkillsLoader

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

WEB = Path(__file__).resolve().parents[1] / "web" / "src"


# ── fixtures ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Every store this path touches, under ``tmp_path``, with each redirect PROVEN.

    Five stores bind ``config_dir`` at IMPORT — the sibling file's header records why patching
    the loader alone leaves three of them writing into the real ``~/.personalclaw``, which
    matters unusually much here because half these tests assert that NOTHING was written.
    """
    import personalclaw.artifacts.native as native_mod
    import personalclaw.config.loader as loader_pkg
    import personalclaw.dashboard.state as state_mod
    import personalclaw.inbox as inbox_mod
    import personalclaw.providers.entity_routes as entity_mod
    import personalclaw.skills.marketplace as mp
    from personalclaw.skills import proposals

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    for mod in (loader_mod, loader_pkg, inbox_mod, native_mod, entity_mod, state_mod):
        monkeypatch.setattr(mod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mp, "SKILL_DISCOVERY_PATHS", [])

    assert loader_pkg.config_dir() == tmp_path
    assert loader_mod.skills_dir() == tmp_path / "skills"
    assert str(proposals._proposals_dir()).startswith(str(tmp_path))
    assert str(inbox_mod.InboxStore()._path).startswith(str(tmp_path))
    assert str(native_mod.NativeArtifactProvider()._root).startswith(str(tmp_path))
    assert str(state_mod._notifications_path()).startswith(str(tmp_path))
    return tmp_path


@pytest.fixture
def artifacts(home, monkeypatch):
    """The artifact provider, pinned to ``tmp_path`` in the process-wide registry.

    ``monkeypatch.setitem`` rather than a patched ``config_dir``: whichever test first calls
    ``get_provider()`` freezes the native provider's root, and a later ``config_dir`` patch
    cannot undo that.
    """
    from personalclaw.artifacts import registry
    from personalclaw.artifacts.native import NativeArtifactProvider

    provider = NativeArtifactProvider(root=home / "artifacts")
    monkeypatch.setitem(registry._providers, "native", provider)
    assert registry.get_provider() is provider
    return provider


@pytest.fixture
def store(home):
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore(base_dir=home)


_EMBED_DIM = 16


def _embed(text: str) -> list[float]:
    """One-hot on the text's hash. A CONSTANT stub makes every lesson a >85% duplicate of the
    last, so ``write_lesson`` drops it and a seeded fixture silently holds one row."""
    idx = int(hashlib.sha256(text.encode()).hexdigest(), 16) % _EMBED_DIM
    return [1.0 if i == idx else 0.0 for i in range(_EMBED_DIM)]


def _state(tmp_path):
    """A DashboardState with a real vector store wired where the provider looks for it.

    Wired through ``context_builder.memory.vector_store`` on purpose — that is the attribute
    chain ``identity_report_provider._vector_store`` reads, so a test that attached the store
    anywhere else would be measuring its own fixture rather than the provider's lookup.
    """
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.memory import MemoryStore
    from personalclaw.vector_memory import VectorMemoryStore

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    vs = VectorMemoryStore(db_path=Path(tmp_path) / "memory.db", embedding_dim=_EMBED_DIM)
    vs.init()
    vs.embed_fn = _embed
    mem = MemoryStore(workspace=ws)
    mem.init()
    mem.vector_store = vs
    cb = MagicMock()
    cb.memory = mem
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0, context_builder=cb)
    assert P._vector_store(state) is vs, "the provider's store lookup does not find the fixture"
    return state, vs


def _wire_services(state, monkeypatch):
    from personalclaw.action_providers import services as svc

    wired = svc.ActionServices(state=state, spawn_background=lambda coro: None)
    monkeypatch.setattr(svc, "_services", wired)
    assert svc.get_action_services() is wired
    return wired


def _write_cadence(value: str) -> None:
    """Write the cadence into the (already redirected) config.json and PROVE the read-back.

    Through the real file rather than a patched ``AppConfig.load``, so ``load()``'s field mapping
    is exercised on every use — that is the round-trip point where a field present in
    ``to_dict()`` but missing from ``load()`` silently reverts to its default.
    """
    from personalclaw.config.loader import AppConfig, config_path

    path = config_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("learning", {})["identity_report_cadence"] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    assert AppConfig.load().learning.identity_report_cadence == value


def _async(value):
    """A zero-arg coroutine returning *value* — the shape `request.json` has."""

    async def _call():
        return value

    return _call


def _seed(vs) -> None:
    """Enough learned material that a delivery has something to report.

    Load-bearing for every absence assertion below: against an EMPTY home a report is still
    delivered, so "no artifact" would not distinguish `off` from "nothing to say".
    """
    from personalclaw.preference_facets import upsert_facet

    upsert_facet(vs, "style", "prefers terse replies", cue="explicit", now=NOW - timedelta(days=1))
    name = SkillsLoader(install_builtins=False).create_auto_skill(
        "fresh-thing",
        description="what fresh-thing does",
        triggers="fresh-thing",
        procedure_md="1. do fresh-thing",
        provenance=AutoSkillProvenance(
            session_key="sess:1", created_at=(NOW - timedelta(days=2)).isoformat()
        ),
    )
    assert name, "create_auto_skill refused the seed"


def _patch_model(monkeypatch, text: str) -> None:
    """Pin the ONE background completion. Patched on the MODULE the caller resolves it from."""
    import personalclaw.llm_helpers as helpers

    async def _fake(*_a, **_k):
        return text

    monkeypatch.setattr(helpers, "one_shot_completion", _fake)


def _report_rows(home: Path) -> list:
    from personalclaw.inbox import InboxStore

    inbox = InboxStore()
    inbox.load()
    return [i for i in inbox.items.values() if i.item_kind == "report"]


# ── one vocabulary, four readers ────────────────────────────────────────────────────


class TestOneVocabulary:
    """`off` is a MEMBER of the cadence, not a sibling bool, and the word list has one home.

    The plan's §T2.5 named two fields (`identity_report_enabled` AND a cadence). One is shipped,
    deliberately: two switches for one concern make `enabled=true, cadence=off` and
    `enabled=false, cadence=weekly` contradictions a reconciler must invent a precedence for,
    and one of the two is then always a control that silently does nothing.
    """

    def test_there_is_no_second_enable_flag_beside_the_cadence(self):
        from dataclasses import fields

        from personalclaw.config.learning import LearningConfig

        names = {f.name for f in fields(LearningConfig)}
        assert "identity_report_cadence" in names
        assert "identity_report_enabled" not in names, (
            "a second switch for one concern — `enabled` and `cadence: off` can disagree, and "
            "whichever loses is a setting that silently does nothing"
        )

    def test_the_patch_allowlist_enum_is_the_module_s_own_vocabulary(self):
        """Compared to `learning_report`'s tuple, not to a hand-written triple.

        `guardrails.scan_mode` keeps three hand-copied `warn/redact/block` lists; this is the
        assertion that stops a fourth copy of THIS vocabulary drifting.
        """
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        spec = _EDITABLE_CONFIG["learning.identity_report_cadence"]
        assert spec["type"] == "enum"
        assert spec["values"] == list(LR.IDENTITY_REPORT_CADENCES)

    def test_the_frontend_strip_offers_the_same_three_words(self):
        """The FE copy of the vocabulary, read out of the source rather than trusted.

        A strip missing `off` would leave the switch unreachable from the only surface that
        shows the report — the field would round-trip and no user could ever set it.
        """
        src = (WEB / "pages" / "learning" / "IdentityReportPanel.tsx").read_text(encoding="utf-8")
        # Sliced to the array literal's own closing `]` on its own line, NOT to the first `]` in
        # the text: the declaration carries a type annotation (`{ key: Cadence; label: string }[]`)
        # whose brackets come first, and slicing there found ZERO keys — a scan that reads as an
        # empty list would have compared `[] != [...]` and reported the wrong defect.
        block = src.split("const CADENCE_OPTIONS")[1].split("\n]")[0]
        keys = re.findall(r"key: '([a-z]+)'", block)
        assert keys == list(LR.IDENTITY_REPORT_CADENCES), keys

    def test_every_cadence_but_off_has_a_cron_expression(self):
        """And `off` deliberately has none — there is no expression meaning "never", so the
        reconciler disables the row instead of inventing one."""
        assert set(P._CADENCE_CRON) == set(LR.IDENTITY_REPORT_CADENCES) - {LR.CADENCE_OFF}
        from personalclaw.triggers.arm import arm
        from personalclaw.triggers.models import Trigger

        for cadence, expr in P._CADENCE_CRON.items():
            t = Trigger(id="t", name="t", kind="clock", spec={"kind": "cron", "expr": expr})
            assert arm(t), f"the {cadence} expression {expr!r} does not arm"

    def test_an_unknown_word_reads_as_the_default_not_as_off(self, home):
        """A typo must not switch the report off: "monthly " and "monthy" would then be
        indistinguishable from a deliberate opt-out, which is a state nobody can diagnose."""
        assert LR.normalize_cadence("monthy") == LR.DEFAULT_CADENCE
        assert LR.normalize_cadence("") == LR.DEFAULT_CADENCE
        assert LR.normalize_cadence(" WEEKLY ") == LR.CADENCE_WEEKLY
        # And through the loader, which is where a real typo arrives — a hand-edited config.json.
        from personalclaw.config.loader import AppConfig, config_path

        assert home  # the redirect fixture is what makes the write below safe
        config_path().write_text(
            json.dumps({"learning": {"identity_report_cadence": "monthy"}}), encoding="utf-8"
        )
        assert AppConfig.load().learning.identity_report_cadence == LR.DEFAULT_CADENCE


# ── round-trip point 4: the write path, end to end ──────────────────────────────────


class TestTheWritePath:
    """`test_config_roundtrip.py` covers dataclass+_meta, `load()` and `to_dict()`. These are the
    two it provably cannot see."""

    def test_the_real_patch_handler_persists_the_cadence_and_load_reads_it_back(self, home):
        """Driven through `api_personalclaw_config_patch`, not through a helper.

        A test that called `coerce_edit_value` directly would prove the validator works and say
        nothing about whether the field is reachable — which is the whole content of "a write
        path". The handler resolves `config_path()` lazily off `config_dir()`, so the `home`
        fixture's redirect already reaches it; asserting the file lands under `tmp_path` below is
        what proves that rather than assuming it.
        """
        import personalclaw.dashboard.handlers.core as core
        from personalclaw.config.loader import AppConfig, config_path

        assert str(config_path()).startswith(str(home))
        config_path().write_text("{}", encoding="utf-8")

        async def _patch(value):
            req = MagicMock()
            req.app = {"state": MagicMock()}
            req.headers = {}
            # A real caller identity: the handler writes a SEL row naming it, and a MagicMock
            # here fails JSON serialization inside the audit log rather than in the code
            # under test.
            req.get = lambda k, d=None: {"user": "owner"}.get(k, d)
            req.json = _async(dict(path="learning.identity_report_cadence", value=value))
            return await core.api_personalclaw_config_patch(req)

        resp = asyncio.run(_patch(LR.CADENCE_WEEKLY))
        assert resp.status == 200, resp.text
        on_disk = json.loads(config_path().read_text(encoding="utf-8"))
        assert on_disk["learning"]["identity_report_cadence"] == LR.CADENCE_WEEKLY
        assert AppConfig.load().learning.identity_report_cadence == LR.CADENCE_WEEKLY

        # VACUITY FLOOR: the handler must REFUSE a word outside the enum. Without this the
        # assertion above would also pass on a handler that wrote anything it was handed, and
        # the `values` list would be decoration.
        bad = asyncio.run(_patch("fortnightly"))
        assert bad.status == 400, bad.text
        assert AppConfig.load().learning.identity_report_cadence == LR.CADENCE_WEEKLY

    def test_the_frontend_control_writes_that_exact_path(self):
        """Round-trip point 5, at its call site. The click is driven in
        `web/src/pages/learning/identityReportCadence.test.tsx`; this is the census that fails
        if the panel stops writing the field at all."""
        src = (WEB / "pages" / "learning" / "IdentityReportPanel.tsx").read_text(encoding="utf-8")
        assert "api.patchConfig('learning.identity_report_cadence'" in src


# ── the cadence converges onto ONE system trigger ───────────────────────────────────


class TestTheTriggerConverges:
    def test_reconcile_creates_one_armed_system_clock_trigger(self, home, store):
        _cadence_default = LR.DEFAULT_CADENCE
        P.reconcile_identity_report_trigger(store)

        row = store.get(P.IDENTITY_REPORT_TRIGGER_ID)
        assert row is not None, "the report's trigger was not registered"
        t = row.trigger
        assert t.created_by == "system" and t.kind == "clock"
        assert t.spec["expr"] == P._CADENCE_CRON[_cadence_default]
        assert t.enabled is True
        assert t.workflow["inline"]["provider"] == P.PROVIDER_NAME
        assert t.delivery == "none", "a cron-result ping about a ping"
        assert row.ok, [i.message for i in row.errors]
        # ARMED. A registered-but-unarmed trigger never fires — the S108 defect one level up.
        assert t.next_fire_at, "the trigger was registered without a next fire"

    def test_reconcile_is_idempotent_and_mints_exactly_one_row(self, home, store):
        for _ in range(3):
            P.reconcile_identity_report_trigger(store)
        mine = [r for r in store.load() if r.trigger.id == P.IDENTITY_REPORT_TRIGGER_ID]
        assert len(mine) == 1, [r.trigger.id for r in store.load()]

    def test_a_cadence_change_CONVERGES_without_touching_the_trigger(self, home, store):
        """The digest's contract: a user who changes this on the Learning page must not have to
        know a trigger exists somewhere to be re-registered."""
        P.reconcile_identity_report_trigger(store)
        assert store.get(P.IDENTITY_REPORT_TRIGGER_ID).trigger.spec["expr"] == "0 9 1 * *"

        _write_cadence(LR.CADENCE_WEEKLY)
        P.reconcile_identity_report_trigger(store)

        t = store.get(P.IDENTITY_REPORT_TRIGGER_ID).trigger
        assert t.spec["expr"] == "0 9 * * 1", "the weekly cadence never reached the spec"
        assert t.enabled is True
        assert t.next_fire_at, "converged without re-arming — the next fire is stale"

    def test_reconcile_preserves_spec_keys_it_does_not_own(self, home, store):
        """`timezone`/`skip_dates` are the quietly-losable keys a cadence edit drops when it
        replaces the spec wholesale — contract §1.3 and S101 each paid for this once."""
        P.reconcile_identity_report_trigger(store)
        t = store.get(P.IDENTITY_REPORT_TRIGGER_ID).trigger
        t.spec = {**t.spec, "timezone": "Europe/Berlin", "skip_dates": ["2026-12-25"]}
        store.upsert(t)

        _write_cadence(LR.CADENCE_WEEKLY)
        P.reconcile_identity_report_trigger(store)

        spec = store.get(P.IDENTITY_REPORT_TRIGGER_ID).trigger.spec
        assert spec["expr"] == "0 9 * * 1"
        assert spec["timezone"] == "Europe/Berlin" and spec["skip_dates"] == ["2026-12-25"]

    def test_an_unreadable_cadence_leaves_the_row_exactly_as_it_was(self, home, store, monkeypatch):
        """Not "default to monthly". A reconciler that guessed would re-enable a report the user
        had switched off, on a transient config read failure."""
        _write_cadence(LR.CADENCE_OFF)
        P.reconcile_identity_report_trigger(store)
        before = store.get(P.IDENTITY_REPORT_TRIGGER_ID).trigger
        assert before.enabled is False

        monkeypatch.setattr(LR, "configured_cadence", lambda: "")
        P.reconcile_identity_report_trigger(store)

        after = store.get(P.IDENTITY_REPORT_TRIGGER_ID).trigger
        assert after.enabled is False, "an unreadable config re-enabled a disabled report"

    def test_the_reconciler_is_called_at_boot(self):
        """A reconciler with no caller is the whole defect this atom existed to close: the
        delivery function shipped with a POST route as its only caller.

        Matched as a CALL through the AST, not as a substring: the name also appears in the
        `from ... import` line beside it, so a `toContain`-style scan stays green when the call
        is commented out and only the import survives. Measured — the first version of this
        assertion did exactly that under M1.
        """
        import ast
        import inspect
        import textwrap

        from personalclaw.gateway import GatewayOrchestrator

        tree = ast.parse(textwrap.dedent(inspect.getsource(GatewayOrchestrator)))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "reconcile_identity_report_trigger" in called, sorted(
            n for n in called if "reconcile" in n
        )
        # VACUITY: the walk really finds this shape of call, so an empty `called` set (a broken
        # parse) cannot read as a pass.
        assert "reconcile_digest_cron" in called


# ── the job FIRES, and firing PRODUCES a report ─────────────────────────────────────


class TestTheJobFires:
    """ "Fires" means the document exists afterwards. A whole trigger subsystem in this repo
    shipped where every fire reached a mailbox nobody opened and every run in the history had
    been started by hand, so an "enqueued" assertion proves nothing here."""

    def test_a_compressed_clock_selects_the_armed_row_as_due(self, home, store):
        """Through the real `service.due_ids`, at a `now` past the armed fire time."""
        from personalclaw.triggers.service import due_ids, to_epoch

        P.reconcile_identity_report_trigger(store)
        t = store.get(P.IDENTITY_REPORT_TRIGGER_ID).trigger
        fire_at = to_epoch(t.next_fire_at)
        assert fire_at > 0

        rows = [r.trigger for r in store.load()]
        assert P.IDENTITY_REPORT_TRIGGER_ID in due_ids(rows, now=fire_at + 1)
        # VACUITY: one second BEFORE its fire time it must not be due, or "due" would mean
        # "exists" and the compressed clock would be doing nothing.
        assert P.IDENTITY_REPORT_TRIGGER_ID not in due_ids(rows, now=fire_at - 60)

    @pytest.mark.asyncio
    async def test_firing_it_through_the_real_dispatch_writes_the_artifact_and_the_inbox_row(
        self, home, artifacts, store, tmp_path, monkeypatch
    ):
        """The end-to-end clause, through `GatewayOrchestrator._fire_store_trigger`.

        That is the ONE dispatch every store-backed fire passes through — it resolves the
        provider out of the registry, screens the payload, applies the denylist and routes the
        rung. A test that called `provider.execute` directly would skip all of it, including the
        `ALLOWED_HOOK_PROVIDERS` / `WRITE_CAPABLE_PROVIDERS` / rung-declaration wiring that a
        provider missing from one set fails at exactly this moment.
        """
        from personalclaw.gateway import GatewayOrchestrator

        state, vs = _state(tmp_path)
        _seed(vs)
        _wire_services(state, monkeypatch)
        _patch_model(monkeypatch, "You favour brevity.")
        _write_cadence(LR.CADENCE_WEEKLY)
        P.reconcile_identity_report_trigger(store)
        trigger = store.get(P.IDENTITY_REPORT_TRIGGER_ID).trigger

        assert artifacts.get(LR.ARTIFACT_SLUG) is None, "the fixture started with a report"
        await object.__new__(GatewayOrchestrator)._fire_store_trigger(trigger, {})

        stored = artifacts.get(LR.ARTIFACT_SLUG)
        assert stored is not None, "the fire produced no artifact — a discarded fire"
        assert "How I've adapted to you" in stored.content
        assert "You favour brevity." in stored.content
        rows = _report_rows(home)
        assert len(rows) == 1, "the fire produced no inbox item"
        assert rows[0].refs["artifact"] == LR.ARTIFACT_SLUG

    @pytest.mark.asyncio
    async def test_the_weekly_cadence_delivers_a_weekly_window(
        self, home, artifacts, tmp_path, monkeypatch
    ):
        """The cadence has to reach the DOCUMENT, not only the cron. A weekly report carrying a
        30-day window says "not used this period" about a skill used nine days ago."""
        state, vs = _state(tmp_path)
        _seed(vs)
        _wire_services(state, monkeypatch)
        _patch_model(monkeypatch, "")
        _write_cadence(LR.CADENCE_WEEKLY)

        result = await P.IdentityReportActionProvider().execute({}, ActionContext(event="t"))

        assert result.success, result.error
        stored = artifacts.get(LR.ARTIFACT_SLUG)
        assert stored is not None
        assert f"Period: {LR.MIN_WINDOW_DAYS} days" in stored.content, stored.content[:200]

        # The floor: the monthly cadence delivers the monthly window through the same call.
        _write_cadence(LR.CADENCE_MONTHLY)
        await P.IdentityReportActionProvider().execute({}, ActionContext(event="t"))
        assert f"Period: {LR.DEFAULT_WINDOW_DAYS} days" in artifacts.get(LR.ARTIFACT_SLUG).content

    @pytest.mark.asyncio
    async def test_a_partial_delivery_is_reported_as_a_failure_not_a_quiet_success(
        self, home, tmp_path, monkeypatch
    ):
        """`deliver_identity_report` never raises — it returns a record with an empty slug. The
        one failure that matters (the document was not persisted) must not read as a green run.

        No `artifacts` fixture here on purpose: with no provider registered the artifact write
        cannot succeed, which is the real shape of this failure.
        """
        from personalclaw.artifacts import registry

        state, vs = _state(tmp_path)
        _seed(vs)
        _wire_services(state, monkeypatch)
        _patch_model(monkeypatch, "")
        _write_cadence(LR.CADENCE_MONTHLY)
        monkeypatch.setattr(registry, "get_provider", lambda *a, **k: None)

        result = await P.IdentityReportActionProvider().execute({}, ActionContext(event="t"))

        assert result.success is False
        assert "artifact" in (result.error or "")

    @pytest.mark.asyncio
    async def test_an_unreadable_cadence_reports_rather_than_guessing_monthly(
        self, home, artifacts, tmp_path, monkeypatch
    ):
        state, vs = _state(tmp_path)
        _seed(vs)
        _wire_services(state, monkeypatch)
        monkeypatch.setattr(LR, "configured_cadence", lambda: "")

        result = await P.IdentityReportActionProvider().execute({}, ActionContext(event="t"))

        assert result.success is False and "unreadable" in (result.error or "")
        assert artifacts.get(LR.ARTIFACT_SLUG) is None, "it delivered against an unreadable config"


# ── `off` disables CLEANLY — two independent refusals ───────────────────────────────


class TestOffDisablesCleanly:
    """Both halves, and they must red independently of the weekly legs above.

    Deleting `execute`'s `off` branch reds `test_off_produces_nothing_at_all` and leaves the
    weekly legs green; pointing the weekly cron at the monthly expression reds the weekly legs
    and leaves these green. A mutation that reds both would mean the pair measures one thing.
    """

    def test_off_disables_the_row_instead_of_deleting_it(self, home, store):
        """Disabled, not deleted: a deleted row is indistinguishable from a feature that was
        never built, and the switch has to stay visible on the Triggers page."""
        P.reconcile_identity_report_trigger(store)
        assert store.get(P.IDENTITY_REPORT_TRIGGER_ID).trigger.enabled is True

        _write_cadence(LR.CADENCE_OFF)
        P.reconcile_identity_report_trigger(store)

        row = store.get(P.IDENTITY_REPORT_TRIGGER_ID)
        assert row is not None, "`off` deleted the row instead of disabling it"
        assert row.trigger.enabled is False
        # And it keeps the expression it would use if switched back on, so the Triggers page
        # reads "monthly, disabled" rather than a blank schedule.
        assert row.trigger.spec["expr"] == P._CADENCE_CRON[LR.DEFAULT_CADENCE]

    def test_a_disabled_row_is_never_selected_by_the_clock(self, home, store):
        """The arming-side refusal, through the real `due_ids` at a `now` well past the fire."""
        from personalclaw.triggers.service import due_ids, to_epoch

        P.reconcile_identity_report_trigger(store)
        fire_at = to_epoch(store.get(P.IDENTITY_REPORT_TRIGGER_ID).trigger.next_fire_at)
        enabled_rows = [r.trigger for r in store.load()]
        assert P.IDENTITY_REPORT_TRIGGER_ID in due_ids(enabled_rows, now=fire_at + 1), (
            "the floor failed: the row was not due even while enabled, so the assertion below "
            "would pass for the wrong reason"
        )

        _write_cadence(LR.CADENCE_OFF)
        P.reconcile_identity_report_trigger(store)
        off_rows = [r.trigger for r in store.load()]
        assert P.IDENTITY_REPORT_TRIGGER_ID not in due_ids(off_rows, now=fire_at + 1)

    @pytest.mark.asyncio
    async def test_off_produces_nothing_at_all(self, home, artifacts, tmp_path, monkeypatch):
        """🔴 THE PRODUCING-SIDE REFUSAL. Not "fires and discards".

        Reachable in production even though the reconciler disables the row: a user can
        re-enable it by hand on the Triggers page, and a fire that produced a report against an
        explicit `off` would be the config lying. The home is SEEDED, so absence here means
        refusal — against an empty home a report is still delivered and this would prove nothing.
        """
        state, vs = _state(tmp_path)
        _seed(vs)
        _wire_services(state, monkeypatch)
        _write_cadence(LR.CADENCE_OFF)

        def _boom(*_a, **_k):
            raise AssertionError("`off` composed the report anyway")

        # Not just "no output" — the gather itself must not run. A cadence that composed and
        # then dropped the result is the inert control this clause exists to refuse.
        monkeypatch.setattr(LR, "compose_identity_report", _boom)

        result = await P.IdentityReportActionProvider().execute({}, ActionContext(event="t"))

        assert result.success is True, "a deliberate opt-out is not an error"
        assert "off" in (result.stdout or "")
        assert artifacts.get(LR.ARTIFACT_SLUG) is None, "`off` wrote an artifact"
        assert _report_rows(home) == [], "`off` raised an inbox item"
        assert state._notification_log == [], "`off` sent a notification"

    @pytest.mark.asyncio
    async def test_the_same_fixture_DOES_produce_when_the_cadence_is_weekly(
        self, home, artifacts, tmp_path, monkeypatch
    ):
        """The vacuity partner for the test above, and the one that must stay green under the
        mutation that reds it. Same seed, same wiring, same call — only the cadence differs."""
        state, vs = _state(tmp_path)
        _seed(vs)
        _wire_services(state, monkeypatch)
        _patch_model(monkeypatch, "")
        _write_cadence(LR.CADENCE_WEEKLY)

        result = await P.IdentityReportActionProvider().execute({}, ActionContext(event="t"))

        assert result.success is True, result.error
        assert artifacts.get(LR.ARTIFACT_SLUG) is not None, "the floor produced nothing"
        assert len(_report_rows(home)) == 1


# ── the dedup key follows the cadence (the DISCOVERY) ───────────────────────────────


class TestTheDedupKeyFollowsThePeriod:
    """🔴 The key was hardcoded to the calendar month. `emit_attention_item` returns the existing
    open row and fires NO second notification for a repeated key, so weeks 2-4 of every month
    would have written a new artifact version and told nobody — a scheduled job whose output is
    silently discarded, which is this codebase's inert-control defect wearing a cron."""

    def test_a_weekly_report_buckets_by_iso_week_and_a_monthly_one_by_month(self):
        aug20 = "2026-08-20T12:00:00+00:00"
        aug27 = "2026-08-27T12:00:00+00:00"
        weekly = [
            LR.delivery_dedup_key(LR.IdentityReport(window_days=LR.MIN_WINDOW_DAYS, generated_at=d))
            for d in (aug20, aug27)
        ]
        assert weekly[0] != weekly[1], "two different weeks share one key — 1 of 4 would be told"
        monthly = [
            LR.delivery_dedup_key(
                LR.IdentityReport(window_days=LR.DEFAULT_WINDOW_DAYS, generated_at=d)
            )
            for d in (aug20, aug27)
        ]
        assert monthly[0] == monthly[1], "one month must stay one key"

    def test_the_iso_week_key_does_not_collide_across_a_new_year(self):
        """`%Y-W%V` is the trap: ISO week 1 of 2027 starts in December 2026, so a calendar year
        paired with an ISO week number gives two different weeks the same bucket."""
        dec28_2026 = "2026-12-28T12:00:00+00:00"  # ISO 2026-W53
        jan04_2027 = "2027-01-04T12:00:00+00:00"  # ISO 2027-W01
        keys = {
            LR.delivery_dedup_key(LR.IdentityReport(window_days=7, generated_at=d))
            for d in (dec28_2026, jan04_2027)
        }
        assert len(keys) == 2, keys

    @pytest.mark.asyncio
    async def test_a_second_weekly_delivery_in_a_NEW_week_raises_a_second_item(
        self, home, artifacts, tmp_path, monkeypatch
    ):
        """The behaviour the month key silently prevented, plus its own floor: the same week
        reuses the row and pings once."""
        state, vs = _state(tmp_path)
        _seed(vs)
        _patch_model(monkeypatch, "")

        first = await LR.deliver_identity_report(state, window_days=7, vs=vs, now=NOW)
        same = await LR.deliver_identity_report(
            state, window_days=7, vs=vs, now=NOW + timedelta(days=1)
        )
        assert same.inbox_item_id == first.inbox_item_id, "the same week minted a second row"
        assert len(state._notification_log) == 1, "the same week pinged twice"

        later = await LR.deliver_identity_report(
            state, window_days=7, vs=vs, now=NOW + timedelta(days=8)
        )
        assert later.inbox_item_id != first.inbox_item_id, (
            "a NEW week reused the row — with the month key this was weeks 2, 3 and 4 of every "
            "month written and never announced"
        )
        assert len(state._notification_log) == 2


# ── the preview states the period the job will deliver ──────────────────────────────


class TestThePreviewAgreesWithTheJob:
    @pytest.mark.asyncio
    async def test_the_get_route_derives_its_window_from_the_cadence_and_ships_it(
        self, home, tmp_path
    ):
        """Hardcoding 30 made a weekly install's panel say "last 30 days" about a document its
        own cron writes over 7 — a config that changed the product without changing anything the
        user could see."""
        from personalclaw.dashboard.handlers import learning as H

        state, _vs = _state(tmp_path)
        req = MagicMock()
        req.app = {"state": state}
        req.headers = {"X-Session-Key": "dashboard:ui"}
        req.query = {}
        req.get = lambda k, d=None: {"user": "owner"}.get(k, d)

        _write_cadence(LR.CADENCE_WEEKLY)
        body = json.loads((await H.api_learning_identity_report(req)).body.decode())
        assert body["window_days"] == LR.MIN_WINDOW_DAYS
        assert body["cadence"] == LR.CADENCE_WEEKLY

        _write_cadence(LR.CADENCE_MONTHLY)
        body = json.loads((await H.api_learning_identity_report(req)).body.decode())
        assert body["window_days"] == LR.DEFAULT_WINDOW_DAYS
        assert body["cadence"] == LR.CADENCE_MONTHLY

        # An explicit `?days=` still WINS — the preview is still a preview.
        req.query = {"days": "365"}
        body = json.loads((await H.api_learning_identity_report(req)).body.decode())
        assert body["window_days"] == LR.MAX_WINDOW_DAYS

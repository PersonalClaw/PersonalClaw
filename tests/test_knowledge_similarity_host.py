"""KL-13 — the similarity-edge pass's HOST REGISTRATION.

The clause under test: *"the edge pass runs on the KL-14 maintenance host, never inline on the
write path."* That is two claims, and each fails in its own silent way, so each is asserted
separately here:

* **It runs on the host.** Not "a name is in a dict" — a registered name proves a dict entry and
  nothing about reachability. Every positive assertion below drives `maintenance.execute()` and
  measures what the registered callable actually invoked, with the host's batch size read off the
  call rather than assumed.
* **It is RESUMABLE, not a sweep.** `batched=True` is the substance of this registration, not a
  detail. `similarity_pass` returns items PROCESSED, so the value is remaining work and the
  host's sub-batch loop is what drains the library. Marked `batched=False` the pass would still
  import, still register and still look wired while draining exactly ONE batch per tick — a
  library larger than a batch would never converge, and nothing in `MaintenanceResult` would
  say so. :func:`test_the_similarity_pass_DRAINS_across_sub_batches` is the assertion that flips
  red on that mistake; the flag check alone would not.
* **It never runs inline.** Asserted at the write path with a counting stand-in installed: N
  real `create_typed_item` calls must invoke it ZERO times and leave a watermark, and one host
  run must then invoke it exactly once. A stamp file cannot express that half — a watermark that
  coalesces perfectly and a store that also did the work inline leave identical state, and only
  the call count separates them.

**The stand-in, stated plainly.** `knowledge/similarity_edges.py` is a sibling atom's file and
is NOT on this branch; creating it here would collide at assembly. So every assertion that needs
a callable installs a stand-in module (see :func:`_install_pass`). That makes the HOST WIRING —
registration, the `batched` contract, the drain loop, the batch-size pass-through, the write-path
negative — genuinely tested, and leaves the pass's own behaviour untested here (it belongs to the
pass's suite). :func:`test_the_real_similarity_module_satisfies_the_registered_contract` is the
seam that closes at assembly: it skips loudly while the module is absent and binds the moment it
lands, so a signature mismatch fails in this suite rather than at the first live tick.
"""

from __future__ import annotations

import inspect
import types

import pytest

from personalclaw import knowledge as knowledge_pkg
from personalclaw.knowledge import maintenance, maintenance_passes
from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home — the host writes a state file into `config_dir()`."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    maintenance.clear_passes()
    maintenance.set_in_flight_probe(None)
    yield tmp_path
    maintenance.clear_passes()
    maintenance.set_in_flight_probe(None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real store AND an isolated home: the watermark lives in `config_dir()`, so without the
    env override this suite would write into the real ~/.personalclaw."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    maintenance.clear_passes()
    maintenance.set_in_flight_probe(None)
    s = KnowledgeStore(str(tmp_path / "knowledge.db"))
    yield s
    s.close()
    maintenance.clear_passes()
    maintenance.set_in_flight_probe(None)


def _install_pass(monkeypatch, fn):
    """Install a stand-in `knowledge.similarity_edges` whose `similarity_pass` is `fn`.

    The real module is a sibling atom's file and is deliberately not created here. This works
    because `from personalclaw.knowledge import similarity_edges` resolves the package ATTRIBUTE
    before the import system attempts a submodule import, so setting the attribute is enough for
    the registered pass's lazy import to find the stand-in. It is also durable in the other
    direction: once the real module lands, `monkeypatch.setattr` shadows it for the duration of
    the test and restores it afterwards, so these assertions keep measuring the HOST rather than
    silently starting to measure the pass.
    """
    module = types.ModuleType("personalclaw.knowledge.similarity_edges")
    module.similarity_pass = fn  # type: ignore[attr-defined]
    monkeypatch.setattr(knowledge_pkg, "similarity_edges", module, raising=False)
    return module


def _recorder(returns: list[int]):
    """A stand-in pass that records every `batch_size` it is handed and replays `returns`.

    Past the end of `returns` it reports 0 — "drained" — so a drain-loop bug shows up as a call
    count, never as an IndexError that a reader could mistake for an unrelated crash.
    """
    calls: list[int] = []

    def _run(*, batch_size: int = 0) -> int:
        calls.append(batch_size)
        return returns[len(calls) - 1] if len(calls) <= len(returns) else 0

    return calls, _run


# ── it is registered on the host ──────────────────────────────────────────


def test_the_similarity_pass_registers(home):
    names = maintenance_passes.register_all()
    assert maintenance_passes.PASS_SIMILARITY_EDGES in names
    assert maintenance_passes.PASS_SIMILARITY_EDGES in maintenance.registered_passes()


def test_registering_twice_does_not_double_the_similarity_pass(home, monkeypatch):
    """ "ONE edge pass, not N" has to survive `register_all` running twice."""
    calls, run = _recorder([])
    _install_pass(monkeypatch, run)
    maintenance.mark_dirty(now=1000.0)
    maintenance_passes.register_all()
    maintenance_passes.register_all()
    maintenance.execute(batch_size=5)
    assert calls == [5], f"a re-registered similarity pass ran {len(calls)} times"


# ── it is RESUMABLE, not a whole-store sweep ──────────────────────────────


def test_the_similarity_pass_is_registered_as_RESUMABLE_not_a_sweep(home):
    maintenance_passes.register_all()
    entry = maintenance._PASSES[maintenance_passes.PASS_SIMILARITY_EDGES]
    assert entry.batched is True, "a real backlog marked single-sweep drains one batch per tick"


def test_the_similarity_pass_DRAINS_across_sub_batches(home, monkeypatch):
    """What `batched=True` buys, asserted THROUGH the host rather than read off the flag.

    Three sub-batches with work and then an empty one: the drain loop must keep claiming while
    the pass reports work and stop the moment it reports none. Registered `batched=False` the
    host calls it exactly once, so this test goes red at `len(calls) == 1` and the reported total
    collapses to the first batch — which is precisely the silent failure the flag prevents.
    """
    calls, run = _recorder([5, 5, 2, 0])
    _install_pass(monkeypatch, run)
    maintenance.mark_dirty(now=1000.0)
    maintenance_passes.register_all()

    result = maintenance.execute(batch_size=5)

    assert len(calls) == 4, f"the backlog was not drained across sub-batches: {calls}"
    assert result.per_pass[maintenance_passes.PASS_SIMILARITY_EDGES] == 12


def test_a_similarity_backlog_that_never_finishes_is_bounded(home, monkeypatch):
    """A pass that always claims more costs one tick, not the loop."""
    calls: list[int] = []

    def _always_more(*, batch_size: int = 0) -> int:
        calls.append(batch_size)
        return 1

    _install_pass(monkeypatch, _always_more)
    maintenance.mark_dirty(now=1000.0)
    maintenance_passes.register_all()
    maintenance.execute(max_batches=4)
    assert len(calls) == 4, f"an always-more similarity pass ran {len(calls)} times, not the cap"


# ── the call site, not the registration ───────────────────────────────────


def test_the_registered_pass_REACHES_similarity_pass_with_the_hosts_batch_size(home, monkeypatch):
    """A registered name proves a dict entry; it does not prove the callable reaches the work.

    This drives `execute` and asserts `similarity_edges.similarity_pass` was the thing invoked,
    with the host's batch size passed through rather than a constant of this module's own.
    """
    calls, run = _recorder([])
    _install_pass(monkeypatch, run)
    maintenance.mark_dirty(now=1000.0)
    maintenance_passes.register_all()

    maintenance.execute(batch_size=7)

    assert calls == [7], f"the registered pass did not reach similarity_pass with 7: {calls}"


def test_a_batch_size_of_zero_defers_to_the_passs_own_default(home, monkeypatch):
    """`batch_size=0` means "the host has no opinion", not "process nothing".

    Forwarding a literal 0 would hand the pass an empty claim forever; duplicating the pass's
    default in this module would fork one number across two files. So the keyword is omitted and
    the pass's own default binds, which is what this asserts.
    """
    seen: list[int] = []

    def _run(*, batch_size: int = 33) -> int:
        seen.append(batch_size)
        return 0

    _install_pass(monkeypatch, _run)
    assert maintenance_passes._similarity_edge_pass(batch_size=0) == 0
    assert seen == [33], f"a zero batch size was forwarded instead of deferred: {seen}"


def test_a_broken_similarity_module_does_not_cost_the_other_passes(home, monkeypatch):
    """Each pass is independently guarded — the failure mode this host replaces was "no cadence".

    The import is lazy (inside the pass), which is what keeps the knowledge WRITE path from
    dragging in the edge builder. The consequence is that an absent or broken module surfaces at
    RUN time, where `execute`'s per-pass guard contains it: the similarity pass gets an error
    entry and every other registered pass still gets its tick.
    """

    def _boom(*, batch_size: int = 0) -> int:
        raise ModuleNotFoundError("No module named 'personalclaw.knowledge.similarity_edges'")

    _install_pass(monkeypatch, _boom)
    sentinel, run = _recorder([])
    maintenance.mark_dirty(now=1000.0)
    maintenance_passes.register_all()
    maintenance.register_pass("zz_sentinel", run)

    result = maintenance.execute(batch_size=5)

    name = maintenance_passes.PASS_SIMILARITY_EDGES
    assert name in result.errors and "ModuleNotFoundError" in result.errors[name]
    assert sentinel == [5], "a failing similarity pass cost an independent pass its cadence"


# ── never inline on the write path ────────────────────────────────────────


def test_the_similarity_pass_does_NOT_run_inline_on_the_write_path(store, monkeypatch):
    """The clause's negative half, asserted at the real write path.

    N real `create_typed_item` calls must invoke the pass ZERO times while still moving the
    watermark, and ONE host run must then invoke it exactly once. Both halves are needed: the
    zero alone would pass on a pass that is never reachable at all, and the one alone would pass
    on a store that did the work inline as well.

    The stand-in returns 0, so the drain loop stops after one call — that makes "ran once" an
    assertion about the HOST's cadence rather than about how many sub-batches a real backlog
    happens to claim.
    """
    calls, _run = _recorder([])
    _install_pass(monkeypatch, _run)
    maintenance_passes.register_all()

    n = 5
    for i in range(n):
        assert store.create_typed_item(item_type="note", title=f"note {i}", content="body")

    assert calls == [], f"{len(calls)} similarity passes ran INLINE during {n} writes"
    assert maintenance.is_dirty(), "the writes left no watermark for the host to act on"

    result = maintenance.execute(batch_size=9)

    # Two asserts, not one: a single `calls == [9]` reports "expected exactly 1" even when the
    # count is right and only the batch size is wrong, which sends a reader after the wrong bug.
    assert len(calls) == 1, f"{n} writes drove {len(calls)} host passes; expected exactly 1"
    assert calls == [9], f"the host's batch size did not reach the pass: {calls}"
    assert result.per_pass[maintenance_passes.PASS_SIMILARITY_EDGES] == 0
    assert not maintenance.is_dirty()


def test_the_inline_assertion_is_sensitive_to_a_call(store, monkeypatch):
    """Vacuity partner for the test above.

    `calls == []` after the writes is only evidence if the recorder would have caught a call.
    Here the pass is invoked directly once, in place of the write path, and the SAME recorder
    must register it — so the empty list above means "the write path did not call it", not "this
    file cannot observe a call".
    """
    calls, run = _recorder([])
    _install_pass(monkeypatch, run)
    maintenance_passes.register_all()

    maintenance._PASSES[maintenance_passes.PASS_SIMILARITY_EDGES].run(batch_size=3)

    assert calls == [3], "the recorder cannot observe a call, so the inline assertion is vacuous"


# ── the seam that closes at assembly ──────────────────────────────────────


def test_the_real_similarity_module_satisfies_the_registered_contract():
    """The stand-in must not outlive the real thing.

    Every other assertion in this file installs a stand-in because
    `knowledge/similarity_edges.py` is a sibling atom's file. This test is the only one that
    touches the real module, and it binds the moment that file lands: a `similarity_pass` that
    does not take `batch_size` as a keyword fails HERE, in the suite that owns the registration,
    rather than at the first live maintenance tick.
    """
    try:
        from personalclaw.knowledge import similarity_edges
    except ImportError:
        # ImportError, not ModuleNotFoundError: `from <package> import <absent submodule>` fails
        # in the fromlist resolution, which raises the parent class. Catching only the narrower
        # one turned this skip into an ERROR — measured, not assumed.
        pytest.skip(
            "knowledge/similarity_edges.py is a sibling atom's file and is not on this branch, "
            "so the registered pass is UNVERIFIED against the real callable — everything else "
            "here runs against a stand-in. This skip must become a pass at assembly; a skip "
            "that survives the merge means the pass module never landed and the registration "
            "points at nothing."
        )

    run = getattr(similarity_edges, "similarity_pass", None)
    assert run is not None, "similarity_edges has no similarity_pass for the registration to call"
    param = inspect.signature(run).parameters.get("batch_size")
    assert param is not None, "similarity_pass takes no batch_size; the host's bound is ignored"
    assert (
        param.kind is inspect.Parameter.KEYWORD_ONLY
    ), "the registered pass calls similarity_pass(batch_size=...) by keyword"

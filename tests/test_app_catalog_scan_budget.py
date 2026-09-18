"""#408 — the Store catalog build is bounded overall, and a failing source backs off.

One unreachable git source made the Store take 135s to open and re-pay ~60s on every
load after. Three separate things made that possible:

* **No overall deadline.** ``available_catalog`` calls its scanners in sequence and each
  walks its sources serially. The only bounds were per-git-process (``timeout=60``/``30``
  in the registry read, ``90`` in the subdir scan), so the cost was N_sources × those
  timeouts with nothing capping the sum — and the per-process timeout does not bound the
  DNS/TCP connect below it, which is where a blackholed address spends its time.
* **A transient failure was never cached** (``return None  # transient git error → don't
  cache``), so a *permanently* bad source paid full price on every single load forever.
* The failure was **discarded**, so the Store could not say which source was at fault.

The rails here are parity properties over the scanners, not one call site: a deadline
honoured by the git-subdir loop but not the registry loop would leave the Store just as
slow, and that asymmetry is invisible to a test that only drives ``available_catalog``
with one source. Every network-touching scanner is asserted directly, and the list is
derived from the module so a scanner added later is not silently exempt.

Hermetic: ``subprocess.run`` is faked, so no test here reaches a network.
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest

from personalclaw.apps import catalog

# The scanners that walk configured sources performing git operations. Both must honour
# the deadline; historically only bounding one was the easy half-fix.
_NETWORK_SCANNERS = ("_scan_registries", "_scan_git_sources")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolated home + a clean cache, so no test here reads the real ~/.personalclaw."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(catalog, "config_dir", lambda: tmp_path)
    # Drop the shipped default sources: these tests are about the sources they add.
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "none"))
    catalog._registry_cache.clear()
    catalog._git_scan_cache.clear()
    catalog._registry_failures.clear()
    yield tmp_path
    catalog._registry_cache.clear()
    catalog._git_scan_cache.clear()
    catalog._registry_failures.clear()


def _sources(tmp_path, *urls: str) -> None:
    p = tmp_path / "apps" / "app-sources.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"git": list(urls)}), encoding="utf-8")


class _Blackhole:
    """A fake ``git`` that burns its whole timeout then raises, exactly like a clone
    against a non-routable address. Records every call so a rail can assert that a
    skipped source performed NO git work at all."""

    def __init__(self, cost: float = 0.4):
        self.cost = cost
        self.calls: list[tuple[float, float | None]] = []

    def __call__(self, argv, *a, **kw):
        budget = kw.get("timeout")
        self.calls.append((time.monotonic(), budget))
        # Sleep for the timeout the caller allowed (capped by our own cost so the suite
        # stays fast), then fail the way a timed-out clone does.
        time.sleep(min(self.cost, budget if budget is not None else self.cost))
        raise subprocess.TimeoutExpired(cmd=argv, timeout=budget or 0)


def test_the_scanner_list_is_not_stale():
    """Vacuity floor. If a scanner is renamed, the parity rails below would silently
    cover nothing — so assert the names still resolve and still take a deadline."""
    import inspect

    for name in _NETWORK_SCANNERS:
        fn = getattr(catalog, name, None)
        assert fn is not None, f"{name} no longer exists — the parity rails below are dead"
        assert (
            "deadline" in inspect.signature(fn).parameters
        ), f"{name} takes no deadline, so the overall catalog budget cannot bound it"


@pytest.mark.parametrize("scanner", _NETWORK_SCANNERS)
def test_every_network_scanner_stops_at_an_expired_deadline(scanner, tmp_path, monkeypatch):
    """An already-expired budget means NO git work: not a shorter clone, none at all."""
    _sources(tmp_path, "https://10.255.255.1/a.git", "https://10.255.255.1/b.git")
    fake = _Blackhole()
    monkeypatch.setattr(subprocess, "run", fake)

    out = getattr(catalog, scanner)(now=time.time(), deadline=time.monotonic() - 1.0)

    assert out == []
    assert fake.calls == [], f"{scanner} ran {len(fake.calls)} git calls past its deadline"


@pytest.mark.parametrize("scanner", _NETWORK_SCANNERS)
def test_every_network_scanner_clamps_a_git_timeout_to_the_remaining_budget(
    scanner, tmp_path, monkeypatch
):
    """Stopping between sources is not enough — the FIRST source could still burn its
    full 60/90s timeout and blow the budget on its own. Each git call may only be given
    the time that is actually left."""
    _sources(tmp_path, "https://10.255.255.1/a.git")
    fake = _Blackhole()
    monkeypatch.setattr(subprocess, "run", fake)

    getattr(catalog, scanner)(now=time.time(), deadline=time.monotonic() + 2.0)

    assert fake.calls, f"{scanner} made no git call, so this rail proves nothing"
    for _at, budget in fake.calls:
        assert budget is not None, f"{scanner} issued a git call with no timeout"
        assert (
            budget <= 2.0 + 0.5
        ), f"{scanner} allowed a {budget}s git call with only ~2s of budget left"


def test_available_catalog_is_bounded_regardless_of_how_many_bad_sources(tmp_path, monkeypatch):
    """The whole build, not each attempt. Eight blackholed sources across both scanners
    must not cost eight timeouts in series."""
    _sources(tmp_path, *[f"https://10.255.255.1/r{i}.git" for i in range(8)])
    monkeypatch.setattr(subprocess, "run", _Blackhole(cost=0.5))
    monkeypatch.setattr(catalog, "_CATALOG_BUDGET_SECS", 2.0)

    t0 = time.monotonic()
    payload = catalog.available_catalog()
    elapsed = time.monotonic() - t0

    assert elapsed <= 2.0 + 1.5, f"catalog build took {elapsed:.1f}s against a 2s budget"
    # Degrades LOUDLY: the sources it could not reach are named on the wire, so the Store
    # can say which one to remove instead of just showing fewer apps.
    assert payload["unavailableSources"], "skipped sources are not reported on the wire"
    assert all("source" in u and "reason" in u for u in payload["unavailableSources"])


def test_a_failing_registry_source_is_not_re_cloned_on_the_next_load(tmp_path, monkeypatch):
    """The repeat cost. A source that just failed is backed off, so the second load does
    no git work for it at all."""
    _sources(tmp_path, "https://10.255.255.1/a.git")
    fake = _Blackhole()
    monkeypatch.setattr(subprocess, "run", fake)

    catalog._scan_registries(now=time.time(), deadline=time.monotonic() + 30.0)
    first = len(fake.calls)
    assert first >= 1, "the first scan did not attempt the source, so this rail is vacuous"

    catalog._scan_registries(now=time.time(), deadline=time.monotonic() + 30.0)
    assert len(fake.calls) == first, "a just-failed source was cloned again immediately"


def test_a_backed_off_source_is_retried_once_its_window_elapses(tmp_path, monkeypatch):
    """Backoff, not a death sentence. A source that went away temporarily must come back
    on its own — caching the failure must never make 'unreachable' permanent."""
    _sources(tmp_path, "https://10.255.255.1/a.git")
    fake = _Blackhole()
    monkeypatch.setattr(subprocess, "run", fake)

    t = time.time()
    catalog._scan_registries(now=t, deadline=time.monotonic() + 30.0)
    first = len(fake.calls)

    # Past the longest backoff window the source could have earned.
    catalog._scan_registries(
        now=t + catalog._REGISTRY_FAIL_MAX_SECS + 1.0, deadline=time.monotonic() + 30.0
    )
    assert len(fake.calls) > first, "a recovered source is never retried — permanently dead"


def test_backoff_grows_with_consecutive_failures(tmp_path, monkeypatch):
    """'Blipped once' and 'has failed ten times' get different treatment — that is the
    distinction the old `don't cache` comment was reaching for and never made."""
    _sources(tmp_path, "https://10.255.255.1/a.git")
    monkeypatch.setattr(subprocess, "run", _Blackhole())

    url = "https://10.255.255.1/a.git"
    windows = []
    t = time.time()
    for _ in range(4):
        catalog._scan_registries(now=t, deadline=time.monotonic() + 30.0)
        windows.append(catalog._registry_backoff_secs(url))
        t += catalog._REGISTRY_FAIL_MAX_SECS + 1.0  # past the window, so the next try runs

    assert windows == sorted(windows), f"backoff did not grow: {windows}"
    assert windows[-1] > windows[0], f"backoff never grew past its base: {windows}"
    assert windows[-1] <= catalog._REGISTRY_FAIL_MAX_SECS


def test_a_recovered_source_clears_its_failure_record(tmp_path, monkeypatch):
    """On success the counter resets, so one bad week does not leave a good source on a
    one-hour backoff forever."""
    url = "https://10.255.255.1/a.git"
    _sources(tmp_path, url)
    monkeypatch.setattr(subprocess, "run", _Blackhole())
    catalog._scan_registries(now=time.time(), deadline=time.monotonic() + 30.0)
    assert url in catalog._registry_failures

    # Now the source answers: a registry index with one pointer.
    def _ok(argv, *a, **kw):
        if "clone" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"apps": [{"name": "demo", "repo": url}]}), ""
        )

    monkeypatch.setattr(subprocess, "run", _ok)
    catalog._registry_cache.clear()
    catalog._scan_registries(
        now=time.time() + catalog._REGISTRY_FAIL_MAX_SECS + 1.0,
        deadline=time.monotonic() + 30.0,
    )
    assert url not in catalog._registry_failures, "a source that recovered stayed backed off"


def test_a_known_failing_source_does_not_starve_a_healthy_one(tmp_path, monkeypatch):
    """A shared budget is spent in iteration order, so a dead source listed FIRST used to
    consume it and push the healthy source behind it into `reason: "budget"` — measured on
    a seeded home, the Store's first open then showed none of the real source's apps. The
    sources that answered last time are tried first."""
    dead, good = "https://10.255.255.1/dead.git", "https://example.invalid/good.git"
    _sources(tmp_path, dead, good)
    # `dead` already has a failure record from an earlier round.
    catalog._note_registry_failure(dead, now=time.time() - catalog._REGISTRY_FAIL_MAX_SECS - 1)

    order = catalog._scan_order(catalog.list_git_sources())
    assert order.index(good) < order.index(dead), f"a known-failing source is tried first: {order}"

    attempted: list[str] = []

    def _record(argv, *a, **kw):
        attempted.append(next((x for x in argv if x.startswith("http")), ""))
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout") or 0)

    monkeypatch.setattr(subprocess, "run", _record)
    catalog._scan_registries(now=time.time(), deadline=time.monotonic() + 30.0)
    assert attempted and attempted[0] == good, f"budget spent on the dead source first: {attempted}"


def test_the_order_is_otherwise_stable(tmp_path):
    """An ordering hint must not reshuffle a healthy configuration — the user's listed order
    is meaningful (it is the precedence they see in Settings)."""
    urls = [f"https://example.invalid/{c}.git" for c in "abcd"]
    _sources(tmp_path, *urls)
    assert catalog._scan_order(catalog.list_git_sources()) == catalog.list_git_sources()


def test_one_dead_source_cannot_consume_the_whole_budget(tmp_path, monkeypatch):
    """The bound that a single total misses. Clamping each git call to "whatever is left"
    hands the first blackholed source the ENTIRE remaining budget in one connect — measured
    live, that made every load cost the full budget and returned zero apps from the healthy
    source. No single git call may exceed the per-source ceiling."""
    _sources(tmp_path, "https://10.255.255.1/a.git", "https://10.255.255.1/b.git")
    fake = _Blackhole(cost=0.2)
    monkeypatch.setattr(subprocess, "run", fake)

    # A budget far larger than the ceiling: the clamp must come from the ceiling, not the
    # remainder, or this passes for the wrong reason.
    catalog._scan_registries(now=time.time(), deadline=time.monotonic() + 600.0)

    assert fake.calls, "no git call was made, so this rail proves nothing"
    for _at, budget in fake.calls:
        assert budget <= catalog._CATALOG_PER_SOURCE_SECS, (
            f"a git call was allowed {budget}s, above the "
            f"{catalog._CATALOG_PER_SOURCE_SECS}s per-source ceiling"
        )


def test_the_per_source_ceiling_leaves_room_for_other_sources():
    """The ceiling is only meaningful if it is a real fraction of the total — equal to it
    would reintroduce the single-source monopoly this pair of bounds exists to prevent."""
    assert catalog._CATALOG_PER_SOURCE_SECS < catalog._CATALOG_BUDGET_SECS
    # Room for at least two sources to each take a full ceiling.
    assert catalog._CATALOG_PER_SOURCE_SECS * 2 <= catalog._CATALOG_BUDGET_SECS

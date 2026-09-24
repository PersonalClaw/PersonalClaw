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
import logging
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


# ---------------------------------------------------------------------------
# #1814 — the backoff made a dead source cheap, which also made it SILENT. For a
# user-added source that is correct: its owner chose the URL and the Store names it under
# ``unavailableSources``. For the SHIPPED default nobody chose it, so the only symptom is
# a Store that quietly has fewer cards than it should, with nothing in the log to act on.
# One WARNING per backoff streak is the whole fix — "per streak" because a line per
# failure is exactly the repeat cost the backoff above exists to remove.
# ---------------------------------------------------------------------------


def _ok_registry(url: str):
    """A fake ``git`` for a source that ANSWERS with a one-pointer registry index."""

    def _run(argv, *a, **kw):
        if "clone" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"apps": [{"name": "demo", "repo": url}]}), ""
        )

    return _run


def test_a_dead_shipped_default_warns_once_per_streak_and_a_user_source_never(
    tmp_path, monkeypatch, caplog
):
    """The two halves of the rule in one drive: the shipped default gets exactly ONE
    warning across a four-failure streak, and the user-added source beside it gets none at
    any level above debug."""
    user_url = "https://10.255.255.1/mine.git"
    _sources(tmp_path, catalog._REGISTRY_GIT_SOURCE, user_url)
    monkeypatch.setattr(subprocess, "run", _Blackhole())

    t = time.time()
    with caplog.at_level(logging.DEBUG, logger="personalclaw.apps.catalog"):
        for _ in range(4):
            catalog._scan_registries(now=t, deadline=time.monotonic() + 30.0)
            t += catalog._REGISTRY_FAIL_MAX_SECS + 1.0  # past the window → the next try runs

    # Premise first: both sources really did fail four times in a row, or "one warning"
    # would be satisfied by a drive that only ever failed once.
    assert catalog._registry_failures[catalog._REGISTRY_GIT_SOURCE][1] == 4
    assert catalog._registry_failures[user_url][1] == 4

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    on_default = [r for r in warnings if catalog._REGISTRY_GIT_SOURCE in r.getMessage()]
    assert len(on_default) == 1, f"expected one warning per streak, got {len(on_default)}"
    assert not [
        r for r in warnings if user_url in r.getMessage()
    ], "a user-added dead source was promoted above debug"
    # Positive control for that zero: the user source IS logged, just at debug — so the
    # assertion above is a real level discrimination and not a broken message match.
    assert [r for r in caplog.records if user_url in r.getMessage()]


def test_a_recovered_default_warns_again_on_its_next_streak(tmp_path, monkeypatch, caplog):
    """Per STREAK, not once per process. A module-level "already warned" flag would pass
    the count rail above and then stay silent through every later outage — the source
    recovers and breaks again, and the second break is as worth reporting as the first."""
    url = catalog._REGISTRY_GIT_SOURCE
    _sources(tmp_path, url)
    t = time.time()

    with caplog.at_level(logging.DEBUG, logger="personalclaw.apps.catalog"):
        monkeypatch.setattr(subprocess, "run", _Blackhole())
        catalog._scan_registries(now=t, deadline=time.monotonic() + 30.0)

        # It answers: the record clears, which is what ends the streak.
        monkeypatch.setattr(subprocess, "run", _ok_registry(url))
        catalog._registry_cache.clear()
        t += catalog._REGISTRY_FAIL_MAX_SECS + 1.0
        catalog._scan_registries(now=t, deadline=time.monotonic() + 30.0)
        assert url not in catalog._registry_failures, "premise: the streak did not end"

        # ...and goes away again. That is a NEW streak.
        monkeypatch.setattr(subprocess, "run", _Blackhole())
        catalog._registry_cache.clear()
        t += catalog._REGISTRY_TTL_SECS + 1.0
        catalog._scan_registries(now=t, deadline=time.monotonic() + 30.0)

    on_default = [
        r for r in caplog.records if r.levelno >= logging.WARNING and url in r.getMessage()
    ]
    assert len(on_default) == 2, f"expected one warning per streak over two streaks: {on_default}"


# ── The REASON a git source failed, not just that it did ─────────────────────────────
#
# Measured on a fresh `python:3.13-slim` container installed from the published wheel —
# which is what `pip install personalclaw` on a minimal machine looks like. `github.com`
# resolved and an HTTPS GET of the repository's `info/refs` returned **200**, yet
# `GET /api/apps/catalog` reported both shipped sources as `reason: "unreachable"`. The
# machine simply had no `git`, and every git source is read by shelling out to it.
#
# That mattered twice over: the Store's badge promises "it will be retried automatically"
# (no retry can ever help), and first-run setup's REQUIRED model lane rendered "No model
# provider app is available…" — an assertion of absence from a read that never happened.
# So the reason has to distinguish a missing dependency from a network fact.


def test_a_machine_with_no_git_says_so_instead_of_calling_the_source_unreachable(
    tmp_path, monkeypatch
):
    url = "https://example.invalid/apps.git"
    _sources(tmp_path, url)
    monkeypatch.setattr(subprocess, "run", _Blackhole())
    monkeypatch.setattr(
        catalog.shutil, "which", lambda name: None if name == "git" else "/bin/" + name
    )

    unavailable: list[dict[str, str]] = []
    catalog._scan_registries(
        now=time.time(), deadline=time.monotonic() + 30.0, unavailable=unavailable
    )

    assert unavailable, "premise: the source did not fail, so this rail proves nothing"
    assert [u["reason"] for u in unavailable] == ["no-git"], unavailable


def test_with_git_present_a_failing_source_is_still_reported_unreachable(tmp_path, monkeypatch):
    """The control. Without this, `no-git` could be returned unconditionally and the test
    above would still pass while every network failure lost its correct reason."""
    url = "https://example.invalid/apps.git"
    _sources(tmp_path, url)
    monkeypatch.setattr(subprocess, "run", _Blackhole())
    monkeypatch.setattr(catalog.shutil, "which", lambda name: "/usr/bin/" + name)

    unavailable: list[dict[str, str]] = []
    catalog._scan_registries(
        now=time.time(), deadline=time.monotonic() + 30.0, unavailable=unavailable
    )

    assert [u["reason"] for u in unavailable] == ["unreachable"], unavailable


def test_the_budget_reason_survives_a_missing_git(tmp_path, monkeypatch):
    """The two reasons are about different events and must not collapse into one. A source
    the scan ATTEMPTED failed for a knowable reason (`no-git`); a source it never reached
    was not attempted at all, so `no-git` would be a guess about a read that never
    happened — "budget" stays the honest word for that one, on the same machine, in the
    same build."""
    _sources(tmp_path, "https://example.invalid/a.git", "https://example.invalid/b.git")
    monkeypatch.setattr(subprocess, "run", _Blackhole(cost=0.5))
    monkeypatch.setattr(catalog.shutil, "which", lambda name: None if name == "git" else "/bin/x")

    unavailable: list[dict[str, str]] = []
    catalog._scan_registries(
        now=time.time(), deadline=time.monotonic() + 0.2, unavailable=unavailable
    )

    by_source = {u["source"]: u["reason"] for u in unavailable}
    assert by_source == {
        "https://example.invalid/a.git": "no-git",
        "https://example.invalid/b.git": "budget",
    }, unavailable

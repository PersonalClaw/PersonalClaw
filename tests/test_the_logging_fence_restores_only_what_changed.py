"""The logging fence (``conftest._restore_personalclaw_logging``) puts back what a test changed,
and costs about the same however many loggers the process holds.

The fence wraps every test. It used to call ``setLevel`` on every ``personalclaw.*`` logger in
its teardown, and each ``setLevel`` clears logging's cache by walking every logger, so one
teardown cost (personalclaw loggers) x (all loggers). With every module imported that is 873 of
994 loggers, and a teardown took about 270 ms. It now puts back only the loggers whose level,
``propagate`` or ``disabled`` differ from before the test.

Each test drives the fence's own generator: set up, change something, tear down.
"""

from __future__ import annotations

import logging
import time

import conftest
import pytest

FENCE = conftest._restore_personalclaw_logging._get_wrapped_function()

#: Loggers planted under one name, removed again after each test that plants them.
PLANT = "personalclaw.zz_logging_fence_plant"


def _fenced(change) -> None:
    """Run ``change`` inside one pass of the fence."""
    fence = FENCE()
    next(fence)
    try:
        change()
    finally:
        with pytest.raises(StopIteration):
            next(fence)


@pytest.fixture
def planted():
    """``n`` loggers under :data:`PLANT`, removed from logging's registry after the test."""
    made: list[str] = []

    def plant(n: int) -> list[logging.Logger]:
        names = [f"{PLANT}.l{i}" for i in range(len(made), len(made) + n)]
        made.extend(names)
        return [logging.getLogger(name) for name in names]

    yield plant
    manager = logging.Logger.manager
    with logging._lock:  # type: ignore[attr-defined]
        for name in [*made, PLANT]:
            manager.loggerDict.pop(name, None)
    manager._clear_cache()


def test_a_level_changed_on_one_logger_of_many_is_put_back(planted) -> None:
    loggers = planted(50)
    target = loggers[17]
    target.setLevel(logging.INFO)
    _fenced(lambda: target.setLevel(logging.ERROR))
    assert target.level == logging.INFO
    assert all(lg.level == logging.NOTSET for lg in loggers if lg is not target)


@pytest.mark.parametrize(
    "attribute, during",
    [("propagate", False), ("disabled", True)],
    ids=["propagate", "disabled"],
)
def test_a_changed_propagate_or_disabled_is_put_back(planted, attribute, during) -> None:
    [lg] = planted(1)
    was = getattr(lg, attribute)
    _fenced(lambda: setattr(lg, attribute, during))
    assert getattr(lg, attribute) == was


def test_the_product_logger_itself_is_put_back() -> None:
    product = logging.getLogger("personalclaw")
    was = (product.level, product.propagate, product.disabled)

    def change() -> None:
        product.setLevel(logging.CRITICAL)
        product.propagate = not product.propagate
        product.disabled = not product.disabled

    _fenced(change)
    assert (product.level, product.propagate, product.disabled) == was


def test_a_logger_the_test_created_is_reset_to_notset(planted) -> None:
    made: list[logging.Logger] = []

    def change() -> None:
        [lg] = planted(1)
        lg.setLevel(logging.ERROR)
        made.append(lg)

    _fenced(change)
    assert made[0].level == logging.NOTSET


def test_after_a_restore_a_child_sees_its_parents_level_again(planted) -> None:
    """A child's answer to ``isEnabledFor`` is cached; after the fence puts its parent's level
    back, the child answers from the restored level, not the cached one."""
    parent, child = planted(1)[0], None
    child = logging.getLogger(f"{parent.name}.child")
    parent.setLevel(logging.DEBUG)

    def change() -> None:
        parent.setLevel(logging.ERROR)
        assert not child.isEnabledFor(logging.INFO)  # cached under ERROR

    _fenced(change)
    assert child.isEnabledFor(logging.INFO)
    logging.Logger.manager.loggerDict.pop(child.name, None)


def _median_seconds(fn, runs: int = 9) -> float:
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        times.append(time.perf_counter() - started)
    return sorted(times)[runs // 2]


#: A pass of the fence that changes nothing may cost at most this many of logging's cache
#: clears, measured in the same run, so the bound holds whatever the machine's load. The fence
#: walks the logger registry twice, and puts back the root's and the sinks' levels, which clear
#: the cache as they always have. Measured on a laptop at load 33-42 with 800 planted loggers: the
#: fence cost 12.6-29.9 clears a pass over twelve runs; restoring every logger with setLevel, as the
#: fence used to, cost 1,437-2,374 over three. 100 is over three times the slowest correct pass and
#: fourteen times below the fastest restore-everything pass.
CLEARS = 100

#: Loggers planted for the timing control.
PLANTED = 800


def test_a_pass_that_changes_nothing_costs_a_few_cache_clears_however_many_loggers(
    planted, record_property
) -> None:
    planted(PLANTED)
    manager = logging.Logger.manager
    one_clear = _median_seconds(manager._clear_cache, runs=21)
    one_pass = _median_seconds(lambda: _fenced(lambda: None), runs=21)
    record_property("cache_clears_per_pass", round(one_pass / one_clear, 2))
    assert one_pass < CLEARS * one_clear, (
        f"one pass {one_pass * 1e3:.2f} ms is {one_pass / one_clear:.0f} cache clears "
        f"({one_clear * 1e3:.3f} ms each) over {len(manager.loggerDict)} loggers"
    )

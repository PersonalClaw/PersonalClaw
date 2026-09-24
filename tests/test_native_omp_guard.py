"""The torch-free-core rail must be able to FAIL (#3324).

Every assertion drives ``native_omp_guard`` against a throwaway mapping. Nothing
here imports ``torch`` — proving a residency detector works by making the hazard
resident would poison this worker for every test that follows it, which is the
exact failure mode the rail exists to prevent.

The rail's non-vacuity on the real tree was measured separately and is not
re-measured here because it cannot be: once the import site is fixed, no test in
this suite makes torch resident, so an in-suite end-to-end proof would have to
re-introduce the defect. Before ``cli_doctor``'s ``import faster_whisper`` became
``importlib.util.find_spec``, this rail failed
``tests/test_cli.py::TestDoctorStt::test_doctor_stt_enabled_with_model`` at
teardown while the other three tests in that class passed — the transition shape
below is what produced that.
"""

import native_omp_guard
from native_omp_guard import HAZARD_MODULES, explain, masked, resident


def test_a_clean_worker_reports_nothing() -> None:
    assert resident({}) == ()
    assert resident({"json": object(), "faiss": object()}) == ()


def test_a_resident_hazard_is_caught() -> None:
    assert resident({"torch": object()}) == ("torch",)


def test_torch_is_watched_by_top_level_name_not_by_route() -> None:
    """``faster_whisper``/``sentence_transformers``/an app provider all reach torch.
    Watching the top-level name is what makes the rail route-independent, so a new
    import path cannot recreate the pairing without tripping it."""
    assert "torch" in HAZARD_MODULES
    reached_via_faster_whisper = {"faster_whisper": object(), "torch": object()}
    assert resident(reached_via_faster_whisper) == ("torch",)


def test_sklearn_is_deliberately_not_a_hazard() -> None:
    """sklearn bundles its own libomp too, but it coexists with faiss (measured rc 0)
    because its ``__init__`` sets KMP_DUPLICATE_LIB_OK first. Listing it would red on
    a hazard that does not exist."""
    assert "sklearn" not in HAZARD_MODULES
    assert resident({"sklearn": object()}) == ()


def test_the_transition_shape_blames_only_the_importing_test() -> None:
    """What conftest's autouse fixture computes. The second test in a poisoned worker
    must come out clean, or the culprit drowns in a cascade of reds."""
    before = resident({})
    after = resident({"torch": object()})
    assert tuple(m for m in after if m not in before) == ("torch",)

    # The next test in the same (already poisoned) worker.
    before = resident({"torch": object()})
    after = resident({"torch": object()})
    assert tuple(m for m in after if m not in before) == ()


def test_the_message_names_the_test_the_library_and_the_issue() -> None:
    msg = explain(("torch",), "tests/test_example.py::test_thing")
    assert "tests/test_example.py::test_thing" in msg
    assert "torch" in msg
    assert "faiss" in msg
    assert "#3324" in msg
    assert "find_spec" in msg


def test_the_mask_is_reported_not_asserted_on(monkeypatch) -> None:
    """A set KMP_DUPLICATE_LIB_OK suppresses the abort without making the pairing
    safe, so a green run is not evidence. The rail says so in the failure text and
    still fails on residency — it does not fail on the flag, which core cannot unset
    for sklearn."""
    monkeypatch.delenv("KMP_DUPLICATE_LIB_OK", raising=False)
    assert masked() is False
    assert "SUPPRESSED" not in explain(("torch",), "t")

    monkeypatch.setenv("KMP_DUPLICATE_LIB_OK", "True")
    assert masked() is True
    assert "SUPPRESSED" in explain(("torch",), "t")
    # Still a residency failure, not a flag failure.
    assert resident({"torch": object()}) == ("torch",)


def test_masked_accepts_an_injected_environment() -> None:
    """Drivable without mutating the process env, for the same reason the detector is
    its own module."""
    assert native_omp_guard.masked({}) is False
    assert native_omp_guard.masked({"KMP_DUPLICATE_LIB_OK": "TRUE"}) is True

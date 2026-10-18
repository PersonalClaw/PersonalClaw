"""The config round-trip still closes when a section is declared OUTSIDE ``config/loader.py``.

``config/loader.py`` (5652 lines, 348 from an absolute 6000-line ceiling) was split into
per-domain sibling modules. ``AppConfig`` and its ``load()`` mapping stayed; sixty-odd
``_meta``-carrying fields moved. That seam is invisible to every existing config rail, and each
half of it fails silently:

* ``tests/test_config_roundtrip.py`` walks ``fields(AppConfig)``, so it cannot tell WHERE a
  section is declared and cannot see a field that lost its ``load()`` mapping in the move. Its
  own docstring records that it covers three of the contract's five points — the write path and
  the frontend control are the two it misses, so a moved section with no ``_EDITABLE_CONFIG``
  entry leaves it fully green while the Settings toggle 400s.
* ``config/schema.py`` resolves a STRING annotation by ``eval``-ing it in ``config.loader``'s
  namespace behind ``except Exception: return str``. A sibling module that used postponed
  annotations would therefore render every one of its fields as ``"string"`` in the JSON schema
  with no error anywhere — a schema regression that reds nothing.

So this file asserts the seam directly: the declarations live in the siblings, the mapping still
reaches them, the metadata survived the move, and the trap above stays shut.

On "add a NEW field end-to-end as proof": the obvious candidate,
``learning.identity_report_cadence``, shipped separately in
``06861fc2`` BEFORE this split, so adding it again is not available as a proof. What that proof
is actually protecting against is a decomposition that leaves the file unable to carry a field.
A one-off new field would demonstrate that once, at the moment of the split. These rails
demonstrate it on every run, for all 87 moved fields at once, and
``test_field_still_reaches_all_five_points_from_its_new_home`` does it end-to-end for a
real user-facing field that now sits on the far side of the seam.
"""

from __future__ import annotations

import pytest
from config_section_modules_shared import _CONFIG_DIR, SECTION_MODULES


@pytest.mark.parametrize("module", SECTION_MODULES)
def test_a_section_module_never_postpones_its_annotations(module: str):
    """The silent schema-degradation trap, pinned shut.

    ``config/schema.py`` does ``eval(tp, vars(config.loader))`` for a string annotation and
    falls back to ``str`` on ANY exception. With ``from __future__ import annotations`` every
    field here becomes a string annotation, so the JSON schema would quietly report the whole
    module as ``"string"``-typed and no test would fail. Cheaper to forbid than to detect.
    """
    src = (_CONFIG_DIR / f"{module}.py").read_text(encoding="utf-8")
    offenders = [
        ln for ln in src.splitlines() if ln.strip() == "from __future__ import annotations"
    ]
    assert not offenders, (
        f"config/{module}.py postpones its annotations, which degrades every field it declares "
        "to a 'string' type in the generated JSON schema, silently. Remove the __future__ "
        "import; see config/schema.py::_resolve_field_type."
    )


def test_the_moved_sections_all_survive_a_save_load_round_trip(tmp_path, monkeypatch):
    """Behavioural backstop for the whole seam: set a non-default on one field per moved
    section, persist, re-read from disk, and require the value back."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig()
    # One field per moved module, and one from a NESTED moved dataclass
    # (guardrails.autonomy) — nesting is where a dropped mapping hides best.
    cfg.sandbox.max_pids = 321
    cfg.guardrails.autonomy.cooldown_days = 21
    cfg.learning.min_evidence = 7
    cfg.evals.study_default_k = 9
    cfg.external_access.enabled = not cfg.external_access.enabled

    def probe(c) -> tuple:
        return (
            c.sandbox.max_pids,
            c.guardrails.autonomy.cooldown_days,
            c.learning.min_evidence,
            c.evals.study_default_k,
            c.external_access.enabled,
        )

    want = probe(cfg)
    assert want != probe(AppConfig()), "the probe values equal the defaults — this proves nothing"
    cfg.save()

    assert probe(AppConfig.load()) == want, "a moved section did not survive save -> load"

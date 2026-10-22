"""ONE phase vocabulary for every piece of work, gateway to web.

What an agent is doing is said by the gateway as one of a closed set of phases
(``personalclaw.work_phase``), each with one tone, one of the four shared families (quiet, active,
waiting, blocked) and the copy row its line is written from. Every surface takes the phase's tone,
family and words from ``web/src/lib/workPhase.ts``. Two tables of one vocabulary drift — the loop
status said "Stalled" on one page and "Stagnant" on the next (``test_loop_status_vocabulary.py``)
— so this rail pins the two equal in both directions, and pins the families the owner settled.

**Both drift directions are pinned.** A phase the gateway can send with no row on the web reds this,
and a web row naming a phase the gateway never sends reds it too.

**Every scan carries a vacuity floor**, and the family check is a function proven to fail on a
planted wrong row, so a parser that stops matching cannot read as a clean pass.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from personalclaw import work_phase
from personalclaw.work_phase import PHASES, SYSTEM_STATES, Family, WorkPhase

_REPO = Path(__file__).resolve().parent.parent
_TS = _REPO / "web" / "src" / "lib" / "workPhase.ts"

pytestmark = pytest.mark.skipif(not _TS.parent.exists(), reason="web sources not present")

#: The fifteen phases: the twelve of the contract, the two waits a turn can be held in, and a chat's
#: own idle.
ALL_PHASES = {
    "queued",
    "thinking",
    "searching",
    "tool",
    "planning",
    "delegating",
    "streaming",
    "listening",
    "approval",
    "input",
    "done",
    "error",
    "retrying",
    "waiting_model",
    "idle",
}

#: The families the owner settled: every live phase is active, the two that wait on her answer are
#: waiting, a failure is blocked, and a finish and nothing running are quiet.
SETTLED_FAMILIES = {
    **{
        p: "active"
        for p in (
            "queued",
            "thinking",
            "searching",
            "tool",
            "planning",
            "delegating",
            "streaming",
            "listening",
            "retrying",
            "waiting_model",
        )
    },
    "approval": "waiting",
    "input": "waiting",
    "error": "blocked",
    "done": "quiet",
    "idle": "quiet",
}

#: The gateway's own states, by the family each reads as.
SETTLED_SYSTEM = {"idle": "quiet", "restarting": "active", "offline": "blocked"}

#: The tones settled for this contract: live work is the scheme's primary (coral) — `queued` with
#: it — the microphone stays warn like the system's own dot, her OK is warn, her answer info, a
#: finish ok and a failure danger; nothing running carries no accent.
SETTLED_TONES = {
    **{
        p: "primary"
        for p in (
            "queued",
            "thinking",
            "searching",
            "tool",
            "planning",
            "delegating",
            "streaming",
            "retrying",
            "waiting_model",
        )
    },
    "listening": "warn",
    "approval": "warn",
    "input": "info",
    "done": "ok",
    "error": "danger",
    "idle": "neutral",
}

#: One row of the web table: ``queued: { tone: 'primary', family: 'active', lineKey: 'queued', …``.
_ROW = re.compile(
    r"^\s{2}(\w+):\s*\{\s*tone:\s*'(\w+)',\s*family:\s*'(\w+)',\s*lineKey:\s*'(\w+)'",
    re.MULTILINE,
)
_SYSTEM_ROW = re.compile(r"^\s{2}(\w+):\s*'(\w+)',?\s*$", re.MULTILINE)


def _block(name: str) -> str:
    text = _TS.read_text(encoding="utf-8")
    parts = text.split(f"export const {name}", 1)
    assert len(parts) == 2, f"{_TS.name} no longer declares {name} — parser drift?"
    return parts[1].split("\n}", 1)[0]


def _web_phases() -> dict[str, tuple[str, str, str]]:
    rows = {m.group(1): m.group(2, 3, 4) for m in _ROW.finditer(_block("WORK_PHASE_LOOKS"))}
    assert len(rows) >= 12, f"parsed {len(rows)} phase rows out of {_TS.name} — vacuous"
    return rows


def _web_system_families() -> dict[str, str]:
    rows = dict(_SYSTEM_ROW.findall(_block("SYSTEM_STATE_FAMILIES")))
    assert rows, f"parsed no system states out of {_TS.name} — vacuous"
    return rows


def unsettled(families: Mapping[str, str], settled: Mapping[str, str]) -> list[str]:
    """Every row of *families* that is not the settled family, and every settled row it lacks."""
    wrong = [
        f"{p}: {f} (settled: {settled.get(p)})" for p, f in families.items() if settled.get(p) != f
    ]
    return wrong + [f"{p}: missing" for p in settled if p not in families]


def test_the_gateway_lists_the_fifteen_phases() -> None:
    assert {p.value for p in WorkPhase} == ALL_PHASES
    assert set(PHASES) == set(WorkPhase), "a phase with no tone, family or line key"


def test_both_sides_list_the_same_phases() -> None:
    web = set(_web_phases())
    gateway = {p.value for p in WorkPhase}
    assert not gateway - web, f"phases the web cannot show: {sorted(gateway - web)}"
    assert not web - gateway, f"phases the gateway never sends: {sorted(web - gateway)}"


def test_each_phase_has_one_tone_family_and_line_key_on_both_sides() -> None:
    web = _web_phases()
    for phase, look in PHASES.items():
        assert web[phase.value] == (look.tone, look.family.value, look.line_key), phase


def test_the_families_are_the_ones_the_owner_settled() -> None:
    gateway = {p.value: look.family.value for p, look in PHASES.items()}
    assert unsettled(gateway, SETTLED_FAMILIES) == []
    assert unsettled({p: row[1] for p, row in _web_phases().items()}, SETTLED_FAMILIES) == []
    assert {s: f.value for s, f in SYSTEM_STATES.items()} == SETTLED_SYSTEM
    assert _web_system_families() == SETTLED_SYSTEM


def test_the_family_check_fails_on_a_planted_wrong_row() -> None:
    """The positive control: the check is a function, and it reds a table with one row moved."""
    planted = {**SETTLED_FAMILIES, "queued": "quiet"}
    assert unsettled(planted, SETTLED_FAMILIES) == ["queued: quiet (settled: active)"]
    del planted["error"]
    assert "error: missing" in unsettled(planted, SETTLED_FAMILIES)


def test_the_tones_are_the_settled_ones() -> None:
    assert {p.value: look.tone for p, look in PHASES.items()} == SETTLED_TONES


def test_each_family_is_read_from_the_one_table() -> None:
    for phase, look in PHASES.items():
        assert work_phase.family_of(phase) is look.family
        assert work_phase.family_of(phase.value) is look.family
    assert {f.value for f in Family} == {"quiet", "active", "waiting", "blocked"}


#: One row of a web status table: ``  review: 'approval',``.
_STATUS_ROW = re.compile(r"^\s{2}(\w+):\s*'(\w+)',\s*$", re.MULTILINE)


#: The loop's statuses are beside its words, in the one loop-status registry.
_LOOP_TS = _REPO / "web" / "src" / "lib" / "loopStatus.ts"


def _web_status_phases(name: str, path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    parts = text.split(f"export const {name}", 1)
    assert len(parts) == 2, f"{path.name} no longer declares {name} — parser drift?"
    rows = dict(_STATUS_ROW.findall(parts[1].split("\n}", 1)[0]))
    assert len(rows) >= 9, f"parsed {len(rows)} rows of {name} out of {path.name} — vacuous"
    return rows


@pytest.mark.parametrize(
    "name,path,gateway",
    [
        ("LOOP_STATUS_PHASES", _LOOP_TS, work_phase.LOOP_STATUS_PHASES),
        ("RUN_STATUS_PHASES", _TS, work_phase.RUN_STATUS_PHASES),
    ],
)
def test_the_web_paints_each_status_as_the_phase_the_gateway_says(
    name: str, path: Path, gateway: Mapping[str, tuple[WorkPhase, str]]
) -> None:
    """A loop's and a run's status are painted in their phase's tone on the web (#3833), so the
    web's status-to-phase tables are the gateway's, in both directions."""
    assert _web_status_phases(name, path) == {
        status: phase.value for status, (phase, _) in gateway.items()
    }

"""``docs/roadmap/atoms/`` is a projection of committed inputs, and must stay one.

An atom's state was spread over six surfaces that disagreed. Measured on ``2ec3ae86e`` the
three headline counts were **684 atoms / 650 done** (``dag.json``), **640 / 356 done**
(``atomic/README.md``) and **648 / "146 remaining"** (``roadmap-dashboard.html``) — three
different answers to how finished the project is, and no rail comparing them. The per-atom
records collapse that into one file per atom.

A consolidation that can drift is worse than none: a reader trusts it precisely because it
claims to be complete. So this module is the ratchet — it re-renders every record from the
committed inputs and reds if any committed copy differs, and it proves it has teeth rather
than asserting a tautology.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.gen_atom_records import (
    ATOMS_DIR,
    DAG_PATH,
    VERDICTS,
    build_context,
    render_all,
    scope_refs,
)

REMEDY = "run `python3 tools/gen_atom_records.py` and commit the result"


@pytest.fixture(scope="module")
def catalog() -> dict:
    return json.loads(DAG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rendered(catalog: dict) -> dict[str, str]:
    out = render_all(catalog)
    # Vacuity guard: a renderer that produced nothing would make every assertion below pass.
    assert len(out) > 500, f"only {len(out)} records rendered — did dag.json's shape change?"
    return out


def test_every_committed_record_matches_its_renderer(rendered: dict[str, str]) -> None:
    """The whole point: no record can drift from the inputs it claims to project."""
    for name, text in sorted(rendered.items()):
        path = ATOMS_DIR / name
        assert path.exists(), f"{name} is missing — {REMEDY}"
        assert path.read_text(encoding="utf-8") == text, f"{name} is stale — {REMEDY}"


def test_no_record_survives_its_atom(rendered: dict[str, str]) -> None:
    """An atom deleted from dag.json must not leave a file claiming it still exists."""
    committed = {p.name for p in ATOMS_DIR.glob("*.md")}
    orphaned = committed - set(rendered)
    assert not orphaned, f"{sorted(orphaned)} name no atom in dag.json — {REMEDY}"


def test_there_is_exactly_one_record_per_atom(catalog: dict, rendered: dict[str, str]) -> None:
    atoms = {a["id"] for p in catalog["plans"] for a in (p.get("atoms") or [])}
    records = {n[:-3] for n in rendered if n != "INDEX.md"}
    assert records == atoms, f"record set and atom set differ: {records ^ atoms}"


def test_the_ratchet_has_teeth(catalog: dict) -> None:
    """Falsify it: a status flip must change that atom's rendered record.

    Without this, ``render_all`` could ignore ``dag.json`` entirely and the assertion above
    would still pass against any committed corpus.
    """
    mutated = json.loads(json.dumps(catalog))
    victim = next(
        a for p in mutated["plans"] for a in (p.get("atoms") or []) if a["status"] == "done"
    )
    victim["status"] = "todo"
    before = render_all(catalog)[f"{victim['id']}.md"]
    after = render_all(mutated)[f"{victim['id']}.md"]
    assert after != before, "flipping a status changed nothing in the record — no teeth"
    assert "**Status** | `done`" in before and "**Status** | `todo`" in after


def test_a_verdict_cannot_be_filed_against_a_nonexistent_atom(
    catalog: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo'd id must RAISE, not sit in the file attached to nothing.

    That is the failure mode which would make the audit look complete while the atom it was
    meant for still read ``unaudited`` — so this drives the loader rather than restating that
    the id is unknown.
    """
    from tools import gen_atom_records as mod

    known = set(build_context(catalog)["by_id"])
    assert "PP-61" not in known, "the fixture id must not be a real atom"

    bad = tmp_path / "verdicts.json"
    bad.write_text(json.dumps({"PP-61": {"verdict": "confirmed", "evidence": ["x"]}}))
    monkeypatch.setattr(mod, "VERDICTS_PATH", bad)
    with pytest.raises(SystemExit, match="names no atom"):
        mod.load_verdicts(known)

    # …and an unknown verdict VALUE is refused too, so the vocabulary cannot quietly widen.
    bad.write_text(json.dumps({"OO-7": {"verdict": "probably-fine"}}))
    with pytest.raises(SystemExit, match="not one of"):
        mod.load_verdicts(known)


def test_every_recorded_verdict_names_a_real_atom_and_a_known_value(catalog: dict) -> None:
    raw = json.loads((ATOMS_DIR / "verdicts.json").read_text(encoding="utf-8"))
    known = {a["id"] for p in catalog["plans"] for a in (p.get("atoms") or [])}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        assert key in known, f"verdicts.json: {key} names no atom"
        assert value.get("verdict") in VERDICTS, f"verdicts.json: {key} has an unknown verdict"
        # A verdict is a claim about evidence: `confirmed` without named evidence is a vote.
        if value["verdict"] in {"confirmed", "partial", "contradicted"}:
            assert value.get(
                "evidence"
            ), f"verdicts.json: {key} is {value['verdict']} with no evidence"


def test_scope_references_resolve_for_almost_every_atom(catalog: dict) -> None:
    """The translation layer must actually translate.

    🔑 The plans do NOT refer to their work by atom id — they use per-plan vocabularies
    (`T1.1`, `Slice 0`, `cy51`, `G1.4`, `R8`, `Session 4`). Scanning only for atom ids left
    **211 of 650 done atoms** with no recorded evidence at all, which reads as "this never
    landed". Resolving the scope tokens cut that to 2. This test pins the improvement so a
    plan adopting a NEW vocabulary shows up as a coverage regression rather than as silent
    emptiness.

    🪤 AND THE METRIC IS EVIDENCE COVERAGE, NOT TOKEN COVERAGE. Asserting "few scopes lack a
    token" measures the wrong thing: 136 scopes resolve no token, yet only 2 atoms end up with
    no evidence, because a commit or an atom-id mention covers most of them. What a reader
    actually needs is that no atom's record is EMPTY — so that is what is pinned.
    """
    rendered_now = render_all(catalog)
    empty = []
    for name, text in rendered_now.items():
        if name == "INDEX.md":
            continue
        counts = [
            int(re.search(pattern, text).group(1))
            for pattern in (
                r"### Commits \((\d+)\)",
                r"atom id in the roadmap prose \((\d+)\)",
                r"scope references \((\d+) line",
            )
        ]
        if sum(counts) == 0:
            empty.append(name[:-3])
    # 2 today: AR-4 (todo, genuinely never started) and one whose scope cites a bracketed log
    # tag rather than a task number. Headroom of a few, so an honest new atom does not red the
    # suite the moment it is minted, but a new plan vocabulary going unread still shows up.
    assert len(empty) <= 6, (
        f"{len(empty)} atom records carry no evidence from any channel — a plan is likely "
        f"using a vocabulary SCOPE_REF_RES does not know: {sorted(empty)}"
    )
    # And prove the translation is load-bearing rather than incidental: the majority of atoms
    # must resolve at least one scope token, since that is the channel that fixed 209 of 211.
    atoms = [a for p in catalog["plans"] for a in (p.get("atoms") or [])]
    with_token = [a["id"] for a in atoms if scope_refs(a)]
    assert len(with_token) > len(atoms) // 2, (
        f"only {len(with_token)} of {len(atoms)} scopes resolve a token — the translation "
        "layer has stopped working"
    )


def test_the_index_reports_real_audit_coverage(rendered: dict[str, str], catalog: dict) -> None:
    """The index's own coverage table must agree with verdicts.json, not be decorative."""
    index = rendered["INDEX.md"]
    raw = json.loads((ATOMS_DIR / "verdicts.json").read_text(encoding="utf-8"))
    confirmed = sum(
        1 for k, v in raw.items() if not k.startswith("_") and v.get("verdict") == "confirmed"
    )
    match = re.search(r"\| `confirmed` \| (\d+) \|", index)
    assert match, "the index lost its audit-coverage table"
    assert (
        int(match.group(1)) == confirmed
    ), "the index's confirmed count disagrees with verdicts.json"
    total = len({a["id"] for p in catalog["plans"] for a in (p.get("atoms") or [])})
    unaudited = re.search(r"\| `unaudited` \| (\d+) \|", index)
    assert unaudited, "the index lost its unaudited count"
    # Every atom lands in exactly one verdict bucket, so the buckets must sum to the catalog.
    buckets = sum(int(m) for m in re.findall(r"\| `(?:\w+)` \| (\d+) \| ", index))
    assert buckets == total, f"verdict buckets sum to {buckets}, not {total} — an atom is uncounted"

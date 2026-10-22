"""Every buddy element has a row in the originality record; a later row names its task (#4570).

Every element of the buddy's design is compared side by side with other products' status indicators
and assistant orbs, from public material, and recorded in ``docs/design/buddy/decisions/
originality.md``: one row each, in product terms, with its verdict (``distinct`` or ``changed``,
or ``pending`` for later work that names the task resolving it). The elements are read from the
design files themselves, so a surface, a transition or a sound added later without a row reds this
test:

- the character, one row per aspect of the check (the owner ruling of 2026-10-18 on #3830): the
  outline, the badge, the wash, the poses, the gestures, the presence and the whole orb;
- each surface of the room budget (``surfaces.md``) and docking on both display shapes;
- the four visual families;
- each transition that ships in v0.3.0 (``motion.md``'s Transitions table), and the play and drop
  transitions of its later work;
- the notification treatment: its icon, its line, what a tap does;
- the sound event map (``sound.md``), the portable export and the bridges, all later work.

The record ends with the maintainer's dated sign-off.
"""

from __future__ import annotations

import re
from pathlib import Path

import source_index
from rails_repo import REPO

ROOT = REPO
DESIGN = ROOT / "docs" / "design" / "buddy"
RECORD = DESIGN / "decisions" / "originality.md"

VERDICTS = {"distinct", "changed", "pending"}
#: The tasks a later row may name as the one that resolves it.
LATER_TASKS = ("#3894", "#3895", "#3900", "#3903", "PersonalClawApps#164")

CHARACTER = ("outline", "badge", "wash", "poses", "gestures", "presence", "whole-orb")
FAMILIES = ("quiet", "active", "waiting", "blocked")
NOTIFICATION = ("icon", "line", "tap")


def slug(text: str, words: int = 6) -> str:
    """An element's id from its name in the design: lower case, punctuation and asides dropped."""
    text = re.sub(r"\([^)]*\)", " ", text.replace("*", "").replace("`", ""))
    parts = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(parts[:words])


def table_after(text: str, heading: str) -> list[list[str]]:
    """The cells of the first table under ``heading``, header and rule rows left out."""
    at = text.index(heading)
    rows: list[list[str]] = []
    for line in text[at:].splitlines()[1:]:
        if line.startswith("#") and rows:
            break
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    return rows[2:]


def shipping_elements(design: Path = DESIGN) -> set[str]:
    surfaces = (design / "surfaces.md").read_text(encoding="utf-8")
    motion = (design / "motion.md").read_text(encoding="utf-8")
    out = {f"character.{a}" for a in CHARACTER}
    out |= {f"layout.{slug(r[0])}" for r in table_after(surfaces, "## The room budget")}
    out |= {"layout.docked-notch", "layout.docked-plain"}
    out |= {f"family.{f}" for f in FAMILIES}
    out |= {f"transition.{slug(r[0])}" for r in table_after(motion, "## Transitions")}
    out |= {f"notification.{n}" for n in NOTIFICATION}
    return out


def later_elements(design: Path = DESIGN) -> set[str]:
    motion = (design / "motion.md").read_text(encoding="utf-8")
    sound = (design / "sound.md").read_text(encoding="utf-8")
    out: set[str] = set()
    for row in table_after(motion, "### Play and drop transitions"):
        kind = "drop" if slug(row[0]) in {"file-over", "swallow", "catch"} else "play"
        out.add(f"{kind}.{slug(row[0])}")
    out |= {f"sound.{slug(r[0])}" for r in table_after(sound, "### Event map")}
    out |= {"export.sprite-sheet", "bridge.companions"}
    return out


def record_rows(text: str) -> dict[str, list[tuple[str, str, str]]]:
    """Each section's rows, ``(id, verdict, the rest)``, keyed ``ships`` or ``later``."""
    sections: dict[str, list[tuple[str, str, str]]] = {"ships": [], "later": []}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip().lower()
            current = (
                "ships"
                if title.startswith("ships in v0.3.0")
                else "later" if title.startswith("later work") else None
            )
            continue
        m = re.match(r"\|\s*`([a-z0-9.-]+)`\s*\|(.*)$", line)
        if not m or current is None:
            continue
        cells = [c.strip() for c in m.group(2).strip().strip("|").split("|")]
        verdict = cells[1].strip("*").lower() if len(cells) > 1 else ""
        sections[current].append((m.group(1), verdict, " | ".join(cells)))
    return sections


def problems(text: str, ships: set[str], later: set[str]) -> list[str]:
    """Everything wrong with a record, for the given elements; empty when it holds."""
    out: list[str] = []
    rows = record_rows(text)
    seen: dict[str, int] = {}
    for section in rows.values():
        for rid, _, _ in section:
            seen[rid] = seen.get(rid, 0) + 1
    for rid, n in sorted(seen.items()):
        if n > 1:
            out.append(f"{rid} has {n} rows")
    shipped = {rid for rid, _, _ in rows["ships"]}
    later_ids = {rid for rid, _, _ in rows["later"]}
    for rid in sorted(ships - shipped):
        out.append(f"{rid} ships in v0.3.0 and has no row in the shipping part")
    for rid in sorted(later - later_ids - shipped):
        out.append(f"{rid} is later work and has no row")
    for rid in sorted((shipped | later_ids) - ships - later):
        out.append(f"{rid} is a row for no element the design names")
    for rid, verdict, rest in rows["ships"] + rows["later"]:
        if verdict not in VERDICTS:
            out.append(f"{rid}: verdict {verdict!r} is not one of {sorted(VERDICTS)}")
    for rid, verdict, _ in rows["ships"]:
        if verdict == "pending":
            out.append(f"{rid} ships in v0.3.0 and reads pending")
    for rid, verdict, rest in rows["later"]:
        if verdict == "pending" and not any(task in rest for task in LATER_TASKS):
            out.append(f"{rid} reads pending and names no task that resolves it")
    if not re.search(
        r"^## Sign-off\n+Signed off by the maintainer on \d{4}-\d{2}-\d{2}\b", text, re.M
    ):
        out.append("the record ends with no dated sign-off by the maintainer")
    return out


def test_the_record_holds_every_element_and_the_maintainer_signed_it():
    ships, later = shipping_elements(), later_elements()
    assert len(ships) >= 40, f"vacuity floor: the shipping elements read {len(ships)}"
    assert len(later) >= 15, f"vacuity floor: the later elements read {len(later)}"
    assert problems(RECORD.read_text(encoding="utf-8"), ships, later) == []


def test_the_docked_layout_on_a_notched_display_is_changed_as_settled():
    rows = dict(
        (rid, (verdict, rest))
        for rid, verdict, rest in record_rows(RECORD.read_text(encoding="utf-8"))["ships"]
    )
    verdict, rest = rows["layout.docked-notch"]
    assert verdict == "changed"
    assert "below the notch" in rest
    surfaces = (DESIGN / "surfaces.md").read_text(encoding="utf-8")
    docking = surfaces[surfaces.index("## Docking") :]
    docking = docking[: docking.index("\n## ", 3)]
    assert (
        "left wing" not in docking and "right wing" not in docking
    ), "surfaces.md no longer docks orbs in the wings"
    assert "below the notch" in docking


def test_public_text_names_no_other_product():
    """The record and the design it changed describe the buddy in product terms only."""
    files = source_index.under(source_index.docs_files(), DESIGN, (".md",))
    assert RECORD in files and DESIGN / "surfaces.md" in files
    for path in files:
        text = source_index.text(path)
        for name in (
            "Siri",
            "Dynamic Island",
            "Alexa",
            "Cortana",
            "Copilot",
            "Gemini",
            "ChatGPT",
            "Clippy",
            "Teams",
            "Notch",
        ):
            assert name not in text, f"{path.relative_to(ROOT)} names {name}"


# ── The test sees what it is for: each planted failure fails it ──────────────────────────────────

FIXTURE_SHIPS = {"layout.top-edge-peek", "transition.peek-in"}
FIXTURE_LATER = {"play.greeting"}
SIGNED = "\n## Sign-off\n\nSigned off by the maintainer on 2026-10-22.\n"


def fixture(ships_rows: str, later_rows: str, sign: str = SIGNED) -> str:
    head = "| Element | In product terms | Verdict | What changed and why | Date |\n"
    head += "|---|---|---|---|---|\n"
    ships = f"## Ships in v0.3.0\n\n{head}{ships_rows}"
    return f"# Record\n\n{ships}\n## Later work\n\n{head}{later_rows}{sign}"


GOOD_SHIPS = (
    "| `layout.top-edge-peek` | the orb just below the top edge | distinct | — | 2026-10-22 |\n"
    "| `transition.peek-in` | the orb slides down | distinct | — | 2026-10-22 |\n"
)
GOOD_LATER = "| `play.greeting` | a wave | pending | with #3894 | 2026-10-22 |\n"


def test_a_well_formed_fixture_holds():
    assert problems(fixture(GOOD_SHIPS, GOOD_LATER), FIXTURE_SHIPS, FIXTURE_LATER) == []


def test_a_transition_added_without_a_row_fails(tmp_path):
    design = tmp_path / "buddy"
    design.mkdir()
    for name in ("surfaces.md", "motion.md", "sound.md"):
        (design / name).write_text((DESIGN / name).read_text(encoding="utf-8"), encoding="utf-8")
    motion = (design / "motion.md").read_text(encoding="utf-8")
    rule = "|---|---|---|---|---|---|---|\n"
    first = motion.index(rule, motion.index("## Transitions")) + len(rule)
    planted = (
        "| Shimmer | a planted reaction | the dots shimmer | `snappy` | ~100 ms | yes | instant |\n"
    )
    (design / "motion.md").write_text(motion[:first] + planted + motion[first:], encoding="utf-8")
    found = problems(
        RECORD.read_text(encoding="utf-8"), shipping_elements(design), later_elements(design)
    )
    assert "transition.shimmer ships in v0.3.0 and has no row in the shipping part" in found


def test_a_pending_shipping_row_fails():
    ships = GOOD_SHIPS.replace(
        "| `transition.peek-in` | the orb slides down | distinct |",
        "| `transition.peek-in` | the orb slides down | pending |",
    )
    assert "transition.peek-in ships in v0.3.0 and reads pending" in problems(
        fixture(ships, GOOD_LATER), FIXTURE_SHIPS, FIXTURE_LATER
    )


def test_a_later_row_naming_no_task_fails():
    later = GOOD_LATER.replace("with #3894", "later")
    assert "play.greeting reads pending and names no task that resolves it" in problems(
        fixture(GOOD_SHIPS, later), FIXTURE_SHIPS, FIXTURE_LATER
    )


def test_a_later_section_moved_into_the_shipping_part_fails():
    moved = fixture(GOOD_SHIPS + GOOD_LATER, "")
    assert "play.greeting ships in v0.3.0 and reads pending" in problems(
        moved, FIXTURE_SHIPS, FIXTURE_LATER
    )


def test_an_unsigned_record_fails():
    assert "the record ends with no dated sign-off by the maintainer" in problems(
        fixture(GOOD_SHIPS, GOOD_LATER, sign="\n## Sign-off\n\nTo be signed.\n"),
        FIXTURE_SHIPS,
        FIXTURE_LATER,
    )

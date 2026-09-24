"""Wiring point 5: an allowlisted config path must be reachable from a Settings control.

The config round-trip contract is five points — dataclass + ``_meta``, ``load()``, ``to_dict()``,
the ``_EDITABLE_CONFIG`` PATCH allowlist, and (for a user-facing field) a frontend control.
``tests/test_config_roundtrip.py`` covers points 1-3 and cannot see point 5 at all, so a whole
config SECTION could be editable, bounded, help-texted and read by the backend while no control
anywhere in ``web/`` wrote it. Issues #752 (``feedback.*``) and #2801 (``loops.*``) are two
instances; #2801 measured the class at **nine sections / 67 keys** and named ``workflows`` (21) as
the largest single block.

This module is the rail for that measurement, scoped to exactly those nine sections. It is derived
(the allowlist is IMPORTED, not parsed) rather than a pinned list of keys, so a new key added to any
of the nine reds here on the day it lands.

🪤 COMMENTS ARE STRIPPED FIRST. An unstripped scan would read a disabled example or explanatory
sentence as a control — the failure this repo has now had at least five times (the
primitive-adoption ratchet counting a ``<button>`` in a comment; the load-error scanner counting
a ``.catch`` it was documenting).

🪤 WHAT THIS RAIL DOES *NOT* CLAIM. It proves a WRITE PATH reaches each key, not that the control is
reachable, correctly bounded or correctly labelled. Those are separate claims, and they are made
where they can actually be made: ``web/src/pages/settings/configSectionControls.test.tsx`` renders
the new panels and asserts the exact dotted path each control PATCHes plus its bounds, and
``web/src/design/settingsHubCoverage.test.ts`` proves each panel is reachable from the hub at all.
A text scan alone would pass with every rendered element deleted.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
WEB_SRC = REPO / "web/src"

#: The nine sections #2801 measured as having NO frontend writer of any form. Two of the nine turned
#: out to be FALSE POSITIVES of that measurement, and they are kept in scope deliberately — a rail
#: that dropped them would stop noticing if their controls were ever removed:
#:
#: * ``external_access`` — ``ExternalAccessPanel`` writes all 13 through ``patchFlag(path)`` and
#:   ``patchCap(path)``, i.e. ``api.patchConfig`` with a path held in a VARIABLE (built from the
#:   server's surface list). Neither of #2801's two instruments — an exact dotted literal, or a
#:   ``patchConfig(`section.${…}`)`` template — can see that shape.
#: * ``security`` — all four are written by dedicated ``api.set*`` helpers that raw-``patch`` the
#:   config endpoint (``setUserDeniedCommands``, ``setSecurityEgress``, ``setCredentialKeychain``,
#:   ``setMcpElicitationServers``). #2801's own caveat anticipated exactly this ("a section could be
#:   written by a dedicated route rather than ``patchConfig``") and checked two candidates; these
#:   four were not among them.
IN_SCOPE_SECTIONS = (
    "workflows",
    "external_access",
    "routing",
    "local_models",
    "sandbox",
    "feedback",
    "loops",
    "tools",
    "security",
)

#: Allowlisted paths in scope that deliberately have NO control, each with the reason.
#:
#: 🔑 EVERY ENTRY HERE IS AN *INERT* PATH — a different defect from a path with no control. An
#: inert path validates, persists and reads back while governing nothing, so its ``_meta`` help is
#: a promise the code does not keep. Giving one a Settings control does not fix it; it makes the
#: promise more convincing to more users. The fix is to wire the reader or drop the allowlist row.
#:
#: The staleness guard below re-measures the inertness, so an entry cannot outlive its reason: the
#: day someone wires a reader, this rail says the exclusion is stale and the path needs a control.
#:
INERT_NO_CONTROL = {
    "routing.energy_sampling": (
        "Zero readers. `_meta` promises 'record a rough energy estimate for local calls, so local "
        "cost is visible as something other than $0' and nothing records one. Same class as #465's "
        "twelve, found by this pass rather than that audit (the allowlist has grown from 103 paths "
        "to 238 since)."
    ),
}

#: Modules that are PLUMBING for a config field — a mention here is not a reader. Mirrors the census
#: method issue #465 used, which reproduced both #322's and #364's findings exactly.
PLUMBING = (
    "src/personalclaw/config/loader.py",
    "src/personalclaw/config/safety.py",
    "src/personalclaw/config/learning.py",
    "src/personalclaw/config/external_access.py",
    "src/personalclaw/config/coercion.py",
    "src/personalclaw/dashboard/handlers/core.py",
)

_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/")
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.MULTILINE)


def _strip_comments(text: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


def _frontend_sources() -> dict[str, str]:
    """Every non-test ``.ts``/``.tsx`` under ``web/src``, comments stripped."""
    out: dict[str, str] = {}
    for p in sorted([*WEB_SRC.rglob("*.ts"), *WEB_SRC.rglob("*.tsx")]):
        if ".test." in p.name:
            continue
        out[str(p.relative_to(REPO))] = _strip_comments(p.read_text(encoding="utf-8"))
    return out


def _allowlist() -> dict[str, dict]:
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    return _EDITABLE_CONFIG


def _in_scope(allowlist: dict[str, dict]) -> list[str]:
    return [k for k in allowlist if k.split(".")[0] in IN_SCOPE_SECTIONS]


# Three writer shapes, all three live in the tree today. Each is matched separately so the vacuity
# test below can prove none of them silently stopped resolving.
_WRITE_VERB = re.compile(r"patchConfig\s*\(|/api/config/personalclaw")
#: `` `prefix.${…}` `` and `` `prefix.${…}.suffix` `` — the template form, with the hole standing in
#: for one path segment.
_TEMPLATE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)\.\$\{[^}]*\}((?:\.[A-Za-z0-9_]+)*)`")


def _writers(path_key: str, sources: dict[str, str]) -> list[str]:
    """Files holding a write path that reaches `path_key`, by any of the three real shapes."""
    hits: list[str] = []
    quoted = [f"'{path_key}'", f'"{path_key}"', f"`{path_key}`"]
    for rel, text in sources.items():
        # 1. The exact dotted path, in a file that also carries a config write. The second half is
        #    what keeps a TYPE declaration (`lib/api.ts`'s interfaces name many of these keys) from
        #    counting as a control.
        if any(q in text for q in quoted) and _WRITE_VERB.search(text):
            hits.append(rel)
            continue
        # 2 & 3. A template writer whose hole structurally matches this key: `ambient.${key}` covers
        #    `ambient.<one segment>`, and `external_access.${s.surface}.enabled` covers
        #    `external_access.<one segment>.enabled`.
        matched = False
        for m in _TEMPLATE.finditer(text):
            prefix, suffix = m.group(1), m.group(2)
            pattern = re.escape(prefix) + r"\.[A-Za-z0-9_]+" + re.escape(suffix)
            if re.fullmatch(pattern, path_key) and _WRITE_VERB.search(text):
                matched = True
                break
        if matched:
            hits.append(rel)
    return hits


def _non_plumbing_readers(leaf: str) -> list[str]:
    """Files under ``src/personalclaw`` naming `leaf` outside the plumbing set."""
    out: list[str] = []
    for p in sorted((REPO / "src/personalclaw").rglob("*.py")):
        rel = str(p.relative_to(REPO))
        if rel in PLUMBING:
            continue
        if leaf in p.read_text(encoding="utf-8"):
            out.append(rel)
    return out


@pytest.fixture(scope="module")
def sources() -> dict[str, str]:
    return _frontend_sources()


def test_the_scan_finds_real_populations() -> None:
    """VACUITY. A matcher that stopped resolving would make every assertion below trivially green
    over empty sets — the one failure a coverage rail cannot afford. Floors, not exact counts, so
    adding a key to one of the nine sections does not fail here first.
    """
    allowlist = _allowlist()
    keys = _in_scope(allowlist)
    # 234 as measured after #465 deleted all six ruled allowlist rows. The floor sits just under
    # that, not under the 240 reading that preceded the cleanup — a floor above the live count
    # would red on the very cleanup the rail asks for.
    assert len(allowlist) >= 231, f"the allowlist import must find the paths, got {len(allowlist)}"
    assert len(keys) >= 60, f"the nine in-scope sections must hold the keys, got {len(keys)}"
    srcs = _frontend_sources()
    assert len(srcs) >= 400, f"the frontend scan must find the sources, got {len(srcs)}"
    # Every section named in scope must actually exist in the allowlist — a renamed section would
    # otherwise silently drop out of coverage while this file still claimed to check it.
    present = {k.split(".")[0] for k in keys}
    assert present == set(IN_SCOPE_SECTIONS), f"scope drifted: {present ^ set(IN_SCOPE_SECTIONS)}"


def test_all_three_writer_shapes_are_exercised(sources: dict[str, str]) -> None:
    """Each of the three detectors must match something real.

    Without this, a detector that broke would just shrink the covered set and the coverage test
    below would report the shortfall as a MISSING CONTROL — sending the next reader to build a
    control that already exists. Three shapes, three live examples:
    """
    # 1. Exact dotted literal: the feedback master switch (#752's headline field).
    assert _writers("feedback.enabled", sources), "the literal-path detector matched nothing"
    # 2. Single-hole template: `patchConfig(`workflows.${key}`)`-style section writers.
    assert _writers(
        "loops.judge_use_case", sources
    ), "the section-template detector matched nothing"
    # 3. Mid-path hole: `external_access.${s.surface}.enabled`, from the server's surface list.
    assert _writers(
        "external_access.openai.enabled", sources
    ), "the mid-path detector matched nothing"


def test_every_allowlisted_path_in_these_nine_sections_has_a_control(
    sources: dict[str, str],
) -> None:
    """The rail. A path on the PATCH allowlist is a PROMISE that a user may change it; a promise
    with no control is only reachable by a raw API call or a hand edit of ``config.json``.
    """
    missing = [
        k for k in _in_scope(_allowlist()) if k not in INERT_NO_CONTROL and not _writers(k, sources)
    ]
    assert missing == [], (
        "These config paths are PATCH-editable and no Settings control writes them — the defect of "
        "#752 and #2801. Add a control to the panel that owns the section (the `ToggleRow` / "
        "`NumberRow` / `SegRow` / `SelectRow` / `TextRow` / `StrListField` family in "
        "`web/src/pages/settings/settingsUI.tsx` maps one-to-one onto `_EDITABLE_CONFIG`'s types), "
        "or — if the path turns out to govern nothing — take it OFF the allowlist rather than "
        "dressing it up:\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize("path_key", sorted(INERT_NO_CONTROL))
def test_an_inert_exclusion_cannot_outlive_its_reason(path_key: str) -> None:
    """Each no-control exclusion is excluded because the path is INERT. Re-measure that, so the
    exclusion expires the moment someone wires a reader — otherwise this list becomes the cheap way
    to make the rail above green, which is how an allowlist of exemptions eats the defect it was
    written to bound.
    """
    leaf = path_key.split(".")[-1]
    readers = _non_plumbing_readers(leaf)
    assert readers == [], (
        f"`{path_key}` is excluded from control coverage because it is inert, but it now has "
        f"readers: {readers}. Either it is wired (drop it from INERT_NO_CONTROL and give it a "
        f"Settings control) or those files only mention it (narrow the check). Reason on record: "
        f"{INERT_NO_CONTROL[path_key]}"
    )


def test_the_inert_exclusions_are_still_on_the_allowlist() -> None:
    """The mirror direction: a stale exclusion for a path that no longer exists reads as coverage of
    something gone, and hides that the list was never revisited.
    """
    allowlist = _allowlist()
    stale = [k for k in INERT_NO_CONTROL if k not in allowlist]
    assert stale == [], f"INERT_NO_CONTROL names paths that are not on the allowlist: {stale}"


def test_a_type_declaration_alone_does_not_count_as_a_control() -> None:
    """Calibration for the detector's second clause, by falsification.

    ``lib/api.ts`` NAMES many config keys in its interfaces. If a mention there were enough, an
    inert-but-typed path would read as controlled and this whole rail would be decorative. So a file
    holding the literal with no write verb must not count.
    """
    typed_only = "export interface X { routing_energy: number }\n'routing.energy_sampling'\n"
    fake = {"web/src/lib/api.ts": typed_only}
    assert _writers("routing.energy_sampling", fake) == []
    # And the same text WITH a write verb does count — otherwise the clause would reject everything
    # and the rail would fail closed for the wrong reason.
    fake_writing = {"web/src/p.tsx": "api.patchConfig('routing.energy_sampling', v)"}
    assert _writers("routing.energy_sampling", fake_writing) == ["web/src/p.tsx"]


def test_a_commented_out_path_does_not_count_as_a_control() -> None:
    """Calibration for the comment stripping, by falsification — the hazard this file's own
    subjects create. Both new panels name the inert paths they omit, in prose, so a scan that read
    comments would report them as controlled.
    """
    live = "api.patchConfig('routing.energy_sampling', v)"
    assert _strip_comments(f"// {live}").strip() == ""
    assert _strip_comments(f"/* {live} */").strip() == ""
    assert (
        _writers("routing.energy_sampling", {"web/src/p.tsx": _strip_comments(f"// {live}")}) == []
    )


def test_the_two_panels_this_change_added_exist_and_are_registered() -> None:
    """The nineteen `workflows.*` and four `loops.*` controls needed somewhere to live, and a panel
    file that exists but is not in ``SUBPAGES`` is reachable by nothing. The hub/axe-manifest halves
    of that are checked in ``web/src/design/settingsHubCoverage.test.ts`` and
    ``settingsSubpageCoverage.test.ts``; this asserts the registration those two compare against.
    """
    page = (WEB_SRC / "pages/settings/SettingsPage.tsx").read_text(encoding="utf-8")
    for panel, sub in (("WorkflowsPanel", "workflows"), ("LoopsPanel", "loops")):
        assert (WEB_SRC / f"pages/settings/{panel}.tsx").exists(), f"{panel}.tsx is missing"
        assert f"import {{ {panel} }}" in page, f"{panel} is not imported by SettingsPage"
        assert f"id: '{sub}'" in page, f"{sub} is not a SUBPAGES id"

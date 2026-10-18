"""The source-tree checks split from
``tests/test_settings_control_coverage.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

import pytest
import source_index
from settings_control_coverage_shared import INERT_NO_CONTROL, REPO, _allowlist

#: The nine sections #2801 measured as having NO frontend writer of any form. Two of the nine turned
#: out to be FALSE POSITIVES of that measurement, and they are kept in scope deliberately — a rail
#: that dropped them would stop noticing if their controls were ever removed:
#:
#: * ``external_access`` — ``ExternalAccessPanel`` writes all 13 through ``patchFlag(path)`` and
#:   ``patchCap(path)``, i.e. ``api.patchConfig`` with a path held in a VARIABLE (built from the
#:   server's surface list). Neither of #2801's two instruments — an exact dotted literal, or a
#:   ``patchConfig(`section.${…}`)`` template — can see that shape.
#: * ``security`` — all four are written by dedicated ``api`` helpers over the config endpoint
#:   (``addDeniedCommand``/``removeDeniedCommand`` and ``grantMcpElicitation``/
#:   ``revokeMcpElicitation`` edit one entry through ``patchConfigItem``; ``setSecurityEgress`` and
#:   ``setCredentialKeychain`` write their value). #2801's own caveat anticipated exactly this ("a
#:   section could be written by a dedicated route rather than ``patchConfig``") and checked two
#:   candidates; these four were not among them.
#:
#: ``auth`` is the tenth, added when its last two keys got a control:
#: ``auth.lockout_threshold`` and ``auth.lockout_window`` were allowlisted, bounded and read by
#: every sign-in door, and only a hand edit of ``config.json`` could change them. ``background``
#: was born in scope: its three limits were constants until they became settings with a control.
IN_SCOPE_SECTIONS = (
    "workflows",
    "external_access",
    "routing",
    "local_models",
    "background",
    "sandbox",
    "feedback",
    "loops",
    "tools",
    "security",
    "auth",
)


#: Modules that are PLUMBING for a config field — a mention here is not a reader. Mirrors the census
#: method issue #465 used, which reproduced both #322's and #364's findings exactly.
PLUMBING = (
    "src/personalclaw/config/loader.py",
    "src/personalclaw/config/safety.py",
    "src/personalclaw/config/learning.py",
    "src/personalclaw/config/external_access.py",
    "src/personalclaw/config/coercion.py",
    "src/personalclaw/config/editable.py",
    "src/personalclaw/dashboard/handlers/core.py",
)


_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/")


_LINE_COMMENT = re.compile(r"^\s*//.*$", re.MULTILINE)


def _strip_comments(text: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


def _frontend_sources() -> dict[str, str]:
    """Every non-test ``.ts``/``.tsx`` under ``web/src``, comments stripped."""
    out: dict[str, str] = {}
    for p in [p for p in source_index.web_files() if p.suffix in {".ts", ".tsx"}]:
        if ".test." in p.name:
            continue
        out[str(p.relative_to(REPO))] = _strip_comments(source_index.text(p))
    return out


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
    for p in source_index.package_files():
        rel = str(p.relative_to(REPO))
        if rel in PLUMBING:
            continue
        if leaf in source_index.text(p):
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
    # 234 as measured after #465 deleted six allowlist rows. The floor sits just under
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

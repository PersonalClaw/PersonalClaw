"""The source-tree checks split from
``tests/test_app_manifest_secret_fields.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import json
import re

from app_manifest_secret_fields_shared import (
    _all_setting_names,
    _manifests,
    _schema_property_blocks,
)

# The resolution of "where do first-party apps live" has ONE owner already — reuse it rather
# than re-deriving it here. A second answer to that question is the defect shape this rail
# exists to catch, and issue 1777 is what a wrong answer costs.
from app_roots import _app_roots, skip_unless_required

from personalclaw.apps.secret_fields import is_credential_field_name


def test_the_sibling_rails_verdicts_match_ours_over_the_live_catalog() -> None:
    """Two deliberate implementations, one verdict — measured, not trusted.

    ``PersonalClawApps``' ``settings-schema-posture`` job keeps its own anchored regex because
    it is a stated pure-stdlib job with no core install, so it cannot consume this predicate
    (the module docstring carries the full reason). That makes drift possible, and drift is
    exactly what shipped: for 16 days core required ``x-meta.sensitive`` on ``max_tokens``
    while the sibling did not, and **nothing compared them**. This is that comparison.

    It compares VERDICTS over the fields that actually exist, not patterns: the sibling's
    verdict is "matches ``CRED_CLASS`` and is not exempted", as an ``(app, field)`` pair in
    ``PATH_VALUED_EXEMPT`` or as a name in ``NOT_A_CREDENTIAL``; ours is
    ``is_credential_field_name``. Two spellings may legitimately disagree about a hypothetical
    name; they may not disagree about a shipped one, because then one of the two repos is
    demanding an annotation the other calls wrong.

    The sibling's pattern and exemptions are read out of its source rather than retyped here, so
    this cannot pass against a stale copy of them. A by-name entry can name a field only core
    ships (``run-workflow-action``'s ``idempotency_key``), which the sibling cannot read, so this
    is also that entry's floor: it fails a name no shipped field carries. Skips — naming what it
    did not check — where no sibling clone is resolved, which is every CI runner.
    """
    sibling = next(
        (
            candidate
            for root in _app_roots()
            for candidate in [root / ".github" / "scripts" / "check_settings_schema_posture.py"]
            if candidate.is_file()
        ),
        None,
    )
    if sibling is None:
        skip_unless_required(
            "no sibling apps clone with a settings-schema-posture rail was resolved, so the "
            "two implementations were not compared at all. Roots searched: "
            f"{[str(r) for r in _app_roots()]}"
        )

    source = sibling.read_text(encoding="utf-8")
    pattern = re.search(r"CRED_CLASS = re\.compile\(\s*\n\s*r\"(?P<body>.+?)\",", source)
    assert pattern, (
        f"{sibling} no longer spells CRED_CLASS the way this comparison reads it, so the "
        "comparison would silently check nothing. Re-derive the extraction rather than "
        "deleting the check."
    )
    sibling_class = re.compile(pattern.group("body"), re.I)

    # Read the exemptions as (app, field) PAIRS, scoped to their own block. The sibling exempts
    # a subject, not a name — another app's `ssh_key` is still a credential there — so matching
    # the bare field would make this comparison quietly more permissive than the gate it reads.
    block = re.search(r"PATH_VALUED_EXEMPT\s*=\s*\{(?P<body>.*?)\n\}", source, re.S)
    assert block, (
        f"{sibling} no longer spells PATH_VALUED_EXEMPT the way this comparison reads it. "
        "Without it every exempted pair reads as a disagreement, so re-derive the extraction."
    )
    exempt = set(re.findall(r'\(\s*"(?P<a>[^"]+)"\s*,\s*"(?P<f>[^"]+)"\s*\)', block.group("body")))
    assert exempt, "the sibling's exemption block parsed as empty, which would fake disagreements"

    # And the NAMES it exempts in every app, one quoted string per line. The block is optional:
    # a sibling without one exempts no name, and reading none can only add a disagreement here,
    # never hide one.
    named = re.search(r"NOT_A_CREDENTIAL\s*=\s*\{(?P<body>.*?)\n\}", source, re.S)
    by_name = set(re.findall(r'^\s*"(?P<n>[^"]+)",', named.group("body"), re.M)) if named else set()
    assert by_name or not named, "the sibling's by-name exemption block parsed as empty"

    disagreements: list[str] = []
    names_compared = 0
    leaves: set[str] = set()
    for path in _manifests():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # pragma: no cover - covered by the corpus arm
            continue
        if not isinstance(manifest, dict):
            continue
        app = path.parent.name
        for props in _schema_property_blocks(manifest):
            for dotted in _all_setting_names(props):
                leaf = dotted.rsplit(".", 1)[-1]
                names_compared += 1
                leaves.add(leaf)
                theirs = (
                    bool(sibling_class.search(leaf))
                    and (app, leaf) not in exempt
                    and leaf not in by_name
                )
                ours = is_credential_field_name(leaf)
                if theirs != ours:
                    disagreements.append(
                        f"{path.parent.name}: {dotted} — sibling says "
                        f"{'credential' if theirs else 'ordinary'}, core says "
                        f"{'credential' if ours else 'ordinary'}"
                    )

    assert names_compared, "no settings fields were compared, so this proves nothing"
    unshipped = sorted(by_name - leaves)
    assert not unshipped, (
        f"{sibling} exempts {unshipped} by name, and no shipped settings field carries that "
        "name. The sibling cannot read core's bundles, so this is the floor for its by-name "
        "entries: delete the entry there rather than leave it widening its rule."
    )
    assert not disagreements, (
        "the two deliberate implementations of `credential-shaped` disagree about a field "
        "that SHIPS, so one repo requires an `x-meta.sensitive` annotation the other calls "
        "wrong — the `max_tokens` failure, recurring. Reconcile them (and record which one "
        "moved and why) rather than adding an exception:\n  " + "\n  ".join(sorted(disagreements))
    )

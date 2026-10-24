"""The source-tree checks split from
``tests/test_http_error_codes_append_only.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

import pytest
import site_baseline
from rails_repo import REPO
from wire_error_envelopes import EMITTER_SITE_FLOOR, scan, scan_source

from personalclaw.http_errors import HTTP_ERROR_CODES

#: The emitter call sites whose code is an expression (``exc.reason``, an f-string, a local),
#: one line each with the reason it is allowed, in the per-site format of
#: ``tests/site_baseline.py``. An f-string is the one place a brand-new, unregistered wire code
#: can enter without the registry check below noticing, so the list only shrinks: a site that
#: passes a literal again has its line deleted, and a new one needs its own line, with ``added``
#: and a reason, which the rails job lists in its summary. It replaced the count
#: ``_DYNAMIC_CODE_SITE_CEILING = 19``, whose notes are now its lines' reasons.
DYNAMIC_CODE_SITES = "tests/rails/baselines/dynamic-error-codes.sites.jsonl"


def test_every_emitted_code_is_registered():
    """The registry cannot fall behind the emitter.

    Walks every ``json_error`` call site in ``src/personalclaw`` and resolves its code
    statically (a literal, or one level of module-constant indirection). Every resolved
    code must have a registry row — that is what makes the registry the contract rather
    than a decorative list.
    """
    census = scan()
    assert len(census.emitter_sites) >= EMITTER_SITE_FLOOR, (
        f"this rail inspected only {len(census.emitter_sites)} json_error call sites, "
        f"below the {EMITTER_SITE_FLOOR} the census counted — the matcher stopped "
        f"matching, so a clean result here means nothing. Fix the scan before trusting it."
    )
    assert census.emitter_literal_codes, "no code resolved statically — the resolver is broken"
    unregistered = sorted(
        {
            (code, f"{f}:{ln}")
            for f, ln, code in census.emitter_literal_codes
            if code not in HTTP_ERROR_CODES
        }
    )
    assert not unregistered, (
        "these json_error codes have no HTTP_ERROR_CODES row. A wire code is a stable "
        "surface; register it (with its one-line meaning) in the same change that ships "
        "it:\n  " + "\n  ".join(f"{code}  ({site})" for code, site in unregistered)
    )


def test_every_dynamic_code_site_is_listed_with_its_reason():
    """An expression in the code slot is the one hole in the check above.

    A ``f"pack_refused_{exc.reason}"`` mints a wire code the static check cannot see. Every
    such site that exists is listed, one line each with the reason it is allowed, in
    :data:`DYNAMIC_CODE_SITES`: a site the list does not hold is a new code entering
    unregistered, and fails by name.
    """
    census = scan()
    assert len(census.emitter_sites) >= EMITTER_SITE_FLOOR, (
        f"inspected only {len(census.emitter_sites)} json_error sites (floor "
        f"{EMITTER_SITE_FLOOR}) — the matcher is broken, not the code."
    )
    assert len(census.dynamic_code_sites) == len(
        census.emitter_dynamic_sites
    ), "a json_error site with a computed code was found and not named, so no list holds it"
    site_baseline.check(census.dynamic_code_sites, DYNAMIC_CODE_SITES)


#: A module that adds one computed code, twice in one handler, beside a literal one.
_PLANTED = """\
from personalclaw.http_errors import json_error


async def api_planted(request):
    try:
        return json_error("not_found", status=404)
    except KeyError as exc:
        return json_error(f"planted_{exc}", status=400)
    except ValueError as exc:
        return json_error(f"planted_{exc}", status=400)
"""
_PLANTED_REL = "src/personalclaw/dashboard/handlers/planted.py"
_PLANTED_SITE = f"{_PLANTED_REL}::api_planted::json_error(f'planted_{{exc}}', status=400)"


@pytest.mark.timeout(50)
def test_a_planted_dynamic_code_site_fails_by_name_and_a_fixed_one_by_its_line():
    """The positive control, on the real list: the tree's sites with a planted module's two
    computed codes fail, naming each (the second in the handler as ``#2``), and the literal one
    is no site; the tree's sites less one, as if that site passed a literal again, fail on its
    stale line. Neither run writes the list."""
    listed = (REPO / DYNAMIC_CODE_SITES).read_text(encoding="utf-8")
    census = scan()
    scan_source(_PLANTED, _PLANTED_REL, census)
    with pytest.raises(AssertionError) as failed:
        site_baseline.check(census.dynamic_code_sites, DYNAMIC_CODE_SITES, None, prune=False)
    message = str(failed.value)
    assert f"Site {_PLANTED_SITE} is not listed" in message, message
    assert f"Site {_PLANTED_SITE} #2 is not listed" in message, message
    assert message.count("is not listed") == 2, message
    fixed, *rest = scan().dynamic_code_sites
    with pytest.raises(AssertionError, match=f"Site {re.escape(fixed)} is listed in"):
        site_baseline.check(rest, DYNAMIC_CODE_SITES, None, prune=False)
    assert (REPO / DYNAMIC_CODE_SITES).read_text(encoding="utf-8") == listed

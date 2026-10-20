"""The source-tree checks split from
``tests/test_http_error_codes_append_only.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from wire_error_envelopes import EMITTER_SITE_FLOOR, scan

from personalclaw.http_errors import HTTP_ERROR_CODES

#: Emitter call sites whose code is an expression (``exc.reason``, an f-string, a
#: local). A CEILING: an f-string is the one place a brand-new, unregistered wire
#: code can enter without this rail noticing, so the number of such sites may not
#: grow. Measured when the ceiling was set: 13 in voice_profiles (``exc.reason``), 2 in packs
#: (``f"pack_refused_{...}"``), 1 in devices (a local chosen from its constants).
#:
#: 17th: ``inbound/openai_dialect.openai_error`` forwards a keyword-only ``code``
#: parameter into :func:`json_error`. It is NOT the hazard this ceiling describes — no
#: code is COMPUTED anywhere on that path; every one of its call sites passes a bare
#: literal, so nothing can be minted that a static reader cannot see. The scanner's
#: existing wrapper-following resolves ``json_response`` PAYLOAD wrappers and module-level
#: string constants, neither of which covers a forwarded code parameter, so the site reads
#: as dynamic despite being fully enumerable.
#:
#: The hole that indirection would otherwise open is closed one level up instead, by
#: ``tests/test_openai_dialect.py::test_every_dialect_error_code_is_a_registered_literal``
#: — it parses that module, asserts every ``openai_error`` call passes a literal, and
#: asserts each of those literals is in ``HTTP_ERROR_CODES``. That is the same guarantee
#: this ceiling protects, proven at the indirection level that actually exists. Raising
#: this number for a computed code would still be wrong.
#:
#: 18th: ``request_validation.RequestValidationError.response`` renders the refusal a
#: handler raised, passing the exception's own ``code``. Admitted on exactly the 17th's
#: reasoning and no new reasoning: nothing is COMPUTED on that path either — every
#: ``raise RequestValidationError(...)`` in that module passes a bare literal, so the set of
#: codes reachable here is closed and statically enumerable; the scanner simply cannot
#: follow a value through an exception attribute, any more than through a forwarded
#: parameter.
#:
#: Closed one level up by
#: ``tests/test_request_validation.py::test_every_code_this_module_raises_is_a_registered_literal``
#: — it parses ``request_validation.py``, asserts every raise passes a literal (failing on
#: any that does not) and asserts each literal is in ``HTTP_ERROR_CODES``. Deliberately
#: enumerated from the source rather than from a list, so a fifth code cannot be added
#: unregistered. Raising this number for a genuinely computed code would still be wrong.
#:
#: 19th: ``dashboard/handlers/packs.api_pack_uninstall`` (#3634) answers
#: ``json_error(exc.code, ...)`` for a ``packs.uninstall.PackUninstallError``. Admitted on the
#: 18th's reasoning: every ``raise PackUninstallError(...)`` passes a bare literal, so the set is
#: closed. It is also the case this ceiling exists for, because it shipped with two of its three
#: codes (``pack_in_use``, ``pack_uninstall_incomplete``) missing from the registry, invisible to
#: the check above. They are registered in the change that admits the site, and the site is
#: closed one level up by
#: ``tests/test_packs_uninstall.py::test_every_code_an_uninstall_raises_is_a_registered_literal``,
#: enumerated from the source the same way.
_DYNAMIC_CODE_SITE_CEILING = 19


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


def test_dynamic_code_sites_do_not_grow():
    """An expression in the code slot is the one hole in the check above.

    A ``f"pack_refused_{exc.reason}"`` mints a wire code the static check cannot see.
    Every such site that exists is accounted for, one by one, in the notes on
    ``_DYNAMIC_CODE_SITE_CEILING``; one more is a new code entering unregistered, so the
    ceiling is the rail.
    """
    census = scan()
    assert len(census.emitter_sites) >= EMITTER_SITE_FLOOR, (
        f"inspected only {len(census.emitter_sites)} json_error sites (floor "
        f"{EMITTER_SITE_FLOOR}) — the matcher is broken, not the code."
    )
    assert len(census.emitter_dynamic_sites) <= _DYNAMIC_CODE_SITE_CEILING, (
        f"{len(census.emitter_dynamic_sites)} json_error sites compute their code "
        f"(ceiling {_DYNAMIC_CODE_SITE_CEILING}). Pass a literal so the registry check "
        f"can see it:\n  " + "\n  ".join(f"{f}:{ln}" for f, ln in census.emitter_dynamic_sites)
    )

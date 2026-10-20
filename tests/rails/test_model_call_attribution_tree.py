"""The source-tree checks split from
``tests/test_model_call_attribution.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index

from personalclaw.guardrails.audit import CALLERS


class TestTheVocabularyIsClosed:
    def test_every_member_of_the_vocabulary_has_a_production_binder(self):
        """No declared-but-unbound member. A vocabulary entry nothing binds is an inert
        surface, and this repo's whole inert-surface census exists because that keeps
        happening. Readers in ``tests/`` deliberately do not count."""
        found: set[str] = set()
        for path in source_index.package_files():
            text = source_index.text(path)
            for name in CALLERS:
                if f'caller_scope("{name}")' in text:
                    found.add(name)
        assert found, "vacuity guard: the scan matched nothing at all"
        assert found == set(CALLERS), f"unbound caller(s): {sorted(set(CALLERS) - found)}"

"""Property tests for the manifest's extended CronEntry.

Covers serialization round-trip of every CronEntry field (including the
extended scheduling fields: agent_sequence, env, persistent_session, silent).
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from personalclaw.apps.manifest import AppManifest, CronEntry

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def _env_dict() -> st.SearchStrategy[dict[str, str]]:
    """Generate environment variable dicts."""
    key = st.from_regex(r"[A-Z][A-Z0-9_]{0,10}", fullmatch=True)
    val = st.text(
        min_size=0,
        max_size=20,
        alphabet=st.characters(categories=("L", "N", "P")),
    )
    return st.dictionaries(key, val, max_size=3)


def _cron_entry() -> st.SearchStrategy[CronEntry]:
    """Generate CronEntry with extended fields."""
    return st.builds(
        CronEntry,
        name=st.from_regex(r"[a-z][a-z0-9-]{0,15}", fullmatch=True),
        every=st.integers(min_value=0, max_value=86400),
        cron_expr=st.one_of(
            st.just(""),
            st.just("* * * * *"),
            st.just("0 */6 * * *"),
        ),
        agent=st.one_of(
            st.just(""),
            st.from_regex(r"[a-z][a-z0-9-]{0,10}", fullmatch=True),
        ),
        message=st.text(
            min_size=0,
            max_size=50,
            alphabet=st.characters(categories=("L", "N", "P", "Z")),
        ),
        agent_sequence=st.lists(
            st.from_regex(r"[a-z][a-z0-9-]{0,10}", fullmatch=True),
            max_size=3,
        ),
        env=_env_dict(),
        persistent_session=st.booleans(),
        silent=st.booleans(),
    )


# ---------------------------------------------------------------------------
# Round-trip properties
# ---------------------------------------------------------------------------


class TestCronRoundTrip:
    """CronEntry round-trips through serialization, alone and in a manifest."""

    @settings(max_examples=100)
    @given(crons=st.lists(_cron_entry(), min_size=0, max_size=3))
    def test_manifest_crons_round_trip(self, crons: list[CronEntry]) -> None:
        """For any valid manifest with extended crons, serializing then
        deserializing produces an equivalent manifest."""
        manifest = AppManifest(
            name="test-app",
            version="1.0.0",
            displayName="Test App",
            description="A test app",
            crons=crons,
        )
        serialized = manifest.to_dict()
        restored = AppManifest.from_dict(serialized)

        assert len(restored.crons) == len(crons)
        for orig, rest in zip(crons, restored.crons):
            assert rest.name == orig.name
            assert rest.every == orig.every
            assert rest.cron_expr == orig.cron_expr
            assert rest.agent == orig.agent
            assert rest.message == orig.message
            assert rest.agent_sequence == orig.agent_sequence
            assert rest.env == orig.env
            assert rest.persistent_session == orig.persistent_session
            assert rest.silent == orig.silent

    @settings(max_examples=50)
    @given(cron=_cron_entry())
    def test_cron_entry_round_trip(self, cron: CronEntry) -> None:
        """CronEntry round-trips through to_dict/from_dict."""
        d = cron.to_dict()
        restored = CronEntry.from_dict(d)
        assert restored.name == cron.name
        assert restored.every == cron.every
        assert restored.cron_expr == cron.cron_expr
        assert restored.agent == cron.agent
        assert restored.message == cron.message
        assert restored.agent_sequence == cron.agent_sequence
        assert restored.env == cron.env
        assert restored.persistent_session == cron.persistent_session
        assert restored.silent == cron.silent

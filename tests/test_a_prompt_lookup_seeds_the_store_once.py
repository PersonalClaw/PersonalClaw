"""A prompt lookup seeds the prompt store once, and again only when something it put there is gone.

Every prompt lookup made sure the bundled prompts were in the store by seeding them again: it read
and parsed every bundled snippet's stored copy, every bundled app's manifest and every prompt those
apps ship, ten lookups to a turn, all of it on the event loop that answers every request. Measured:
2.6 s of the 3.2 s a test's turn took to reach its model, and 1.2 s of every turn in a home already
seeded. Under coverage on a busy runner, a test's turn missed its 5 s budget to start at all.
"""

from __future__ import annotations

import pytest

from personalclaw.apps import prompt_registry
from personalclaw.prompt_providers import native_provider
from personalclaw.prompt_providers import registry as prompts


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    """A home of the test's own, seeding on, and the in-process registries as a boot finds them."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.delenv("PERSONALCLAW_SKIP_PROMPT_SEED", raising=False)
    prompts._providers.clear()
    prompt_registry.clear()
    yield tmp_path
    prompts._providers.clear()
    prompt_registry.clear()


class _Reads:
    """Counts what a lookup parses: stored prompts and snippets, and app prompt definitions."""

    def __init__(self, monkeypatch) -> None:
        self.parsed = 0
        real = native_provider._yaml_loads

        def counted(text: str):
            self.parsed += 1
            return real(text)

        monkeypatch.setattr(native_provider, "_yaml_loads", counted)


def test_a_lookup_after_the_seed_parses_nothing_and_the_store_is_kept_whole(
    _store, tmp_path, monkeypatch
):
    prompts._ensure_default_providers_registered()  # a boot: the store is seeded
    seeded = native_provider.prompt_file("system-chat")
    assert seeded.is_file(), "vacuity floor: the first lookup seeds the store"
    app_use_cases = set(prompt_registry.use_cases())
    assert "knowledge_extraction" in app_use_cases, "vacuity floor: bundled app prompts seed"

    reads = _Reads(monkeypatch)
    for _ in range(10):  # a turn's lookups
        prompts._ensure_default_providers_registered()
    assert reads.parsed == 0, f"ten lookups after the seed parsed {reads.parsed} stored files"

    # What the seed put there and is gone comes back at the next lookup, as it always did.
    seeded.unlink()
    prompts._ensure_default_providers_registered()
    assert seeded.is_file(), "a seeded prompt deleted meanwhile was not seeded again"
    prompt_registry.clear()
    prompts._ensure_default_providers_registered()
    assert set(prompt_registry.use_cases()) >= app_use_cases, "the app use-cases stayed forgotten"
    # And another home is a store of its own, seeded at its first lookup.
    other = tmp_path / "other-home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(other))
    prompts._ensure_default_providers_registered()
    assert (other / "prompts" / "system-chat.yaml").is_file(), "another home was never seeded"

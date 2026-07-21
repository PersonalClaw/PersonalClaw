"""Hypothesis property tests for the provider registry, credentials, and lazy imports.

- Registry closure: every entry's declared capabilities are a subset of its type's.
- Credential non-leakage: CredentialStore.list() never returns secret values.
- Lazy SDK imports: importing the providers package pulls in no vendor SDK.
"""

import tempfile
from pathlib import Path

import pytest

pytest.importorskip("hypothesis")

from hypothesis import (  # noqa: E402
    assume,
    given,
    settings,
)
from hypothesis import strategies as st  # noqa: E402

from personalclaw.llm.capabilities import (  # noqa: E402
    Capability,
    ProviderCapability,
)
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry  # noqa: E402

# ── Shared helpers ────────────────────────────────────────────────────────────

_ALL_CAPS = list(Capability)
# The capabilities meaningful to bind a provider entry to (was the deleted
# router's _BINDABLE_USE_CASES; inlined here for the property strategies).
_BINDABLE_USE_CASES = frozenset(
    {
        Capability.CHAT,
        Capability.CODE_TOOLS,
        Capability.SUMMARIZATION,
        Capability.PLANNING,
        Capability.EMBEDDING,
        Capability.VISION,
    }
)
_BINDABLE_LIST = sorted(_BINDABLE_USE_CASES, key=lambda c: c.value)


def _make_reg_with_entries(
    entries: list[ProviderEntry],
) -> ProviderRegistry:
    """Build a fresh ProviderRegistry with a 'test' type and the given entries."""
    reg = ProviderRegistry()
    cap = ProviderCapability(
        type="test",
        capabilities=frozenset(_BINDABLE_USE_CASES | {Capability.EMBEDDING}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=True,
        supports_vision=False,
        max_context_tokens=0,
    )
    reg.register_type(cap, lambda *, entry, **kw: None)
    for e in entries:
        reg.register_entry(e)
    return reg


_entry_names = st.from_regex(r"[a-z][a-z0-9-]{1,10}", fullmatch=True)
_bindable_cap = st.sampled_from(_BINDABLE_LIST)
_cap_subset = st.frozensets(st.sampled_from(list(_BINDABLE_USE_CASES)), min_size=1)


@st.composite
def _entry_strategy(draw):
    name = draw(_entry_names)
    caps = draw(_cap_subset)
    return ProviderEntry(
        name=name,
        type="test",
        model="m",
        declared_capabilities=caps,
    )


@st.composite
def _unique_entries(draw, min_size=1, max_size=5):
    """Draw a list of ProviderEntry objects with unique names."""
    entries = draw(st.lists(_entry_strategy(), min_size=min_size, max_size=max_size))
    seen: set[str] = set()
    unique = []
    for e in entries:
        if e.name not in seen:
            seen.add(e.name)
            unique.append(e)
    assume(len(unique) >= min_size)
    return unique


# ── Registry closure ──────────────────────────────────────────────────────


@given(entries=_unique_entries())
@settings(max_examples=50)
def test_registry_closure(entries):
    """Every entry's declared_capabilities is a subset of its type's capability set."""
    reg = _make_reg_with_entries(entries)
    type_caps = reg.capability_of("test").capabilities
    for e in reg.list_entries():
        assert e.declared_capabilities.issubset(
            type_caps
        ), f"closure violated for {e.name}: {e.declared_capabilities} ⊄ {type_caps}"


# ── Credential non-leakage ────────────────────────────────────────────────


def _make_store_with_secret(tmp_dir: str, name: str, value: str):
    from personalclaw.llm.credentials import CredentialStore

    store = CredentialStore(Path(tmp_dir) / "creds.json")
    store.save({name: {"value": value}})
    store.reload()
    return store


def test_credential_list_strips_secrets():
    """CredentialStore.list() returns entries with secret=None."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store_with_secret(tmp, "my-key", "super-secret-value")
        entries = store.list()
        for cred in entries:
            assert (
                cred.secret is None
            ), f"credential '{cred.name}' leaked a non-None secret in list()"


@given(secret=st.text(min_size=1, max_size=64))
@settings(max_examples=30)
def test_list_never_leaks_secret(secret):
    """No matter what secret is stored, list() never returns it."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store_with_secret(tmp, "k", secret)
        for cred in store.list():
            assert cred.secret is None


# ── Lazy SDK imports ─────────────────────────────────────────────────────


def test_providers_import_no_sdk_leakage():
    """Importing personalclaw.providers does not trigger anthropic/openai/httpx."""
    import os as _os
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    repo_src = str(_Path(__file__).resolve().parent.parent / "src")
    env = {**_os.environ, "PYTHONPATH": repo_src}
    result = subprocess.run(
        [
            _sys.executable,
            "-c",
            (
                "import sys; "
                "import personalclaw.llm; "
                "leaked = [m for m in sys.modules if m in "
                "('anthropic','openai','httpx')]; "
                "print(leaked); "
                "sys.exit(1 if leaked else 0)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert (
        result.returncode == 0
    ), f"SDK modules loaded on import: {result.stdout.strip()}\n{result.stderr[:300]}"

"""`GET /api/model-providers` reported every provider's stored key as missing.

(The field was `credential_status`; it is `key_in_store` now — whether the instance's key
lives in the credential store. Whether an instance CONNECTS is its measured `connection`,
see `providers/connection.py`, and no longer inferred from a credential's presence.)

Co-located with issue 2217's census work: the census resolves `credentials.json` from
`dashboard/handlers/providers.py`, and reading that one write path is what surfaced this.

`CredentialStore.__init__` takes a **home directory** and derives `<home>/credentials.json`
and `<home>/.env` from it — its own docstring says so ("Construction reads
``<home>/credentials.json``"). Measured on the tree: eleven call sites construct one, and ten
pass a home. This one passed `config_dir() / "credentials.json"`, so the store looked for
`credentials.json/credentials.json`, loaded no descriptors, and `resolve()` raised `KeyError`
for every name — swallowed by the handler's `except Exception` into `"missing"`.

So a correctly configured provider rendered as having no credential, on the surface whose
whole job is to report whether the credential is there.
"""

from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field

import pytest

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"


@dataclass
class _Entry:
    name: str
    type: str
    model: str
    credential: str | None
    declared_capabilities: tuple = field(default_factory=tuple)
    options: dict = field(default_factory=dict)


class _Registry:
    def __init__(self, entries):
        self._entries = entries

    def list_entries(self):
        return self._entries

    def capability_of(self, _type):  # no static descriptor — handler falls back
        raise LookupError("no capability descriptor")

    def build_catalog(self, _entry):  # no catalog: its connection reads "untestable"
        return None


async def _statuses(monkeypatch, home, entries) -> dict[str, bool]:
    """Drive the real handler and return {provider name: key_in_store}."""
    from personalclaw.config import loader as config_loader
    from personalclaw.dashboard.handlers import providers as handler
    from personalclaw.llm import registry as llm_registry

    monkeypatch.setattr(config_loader, "config_dir", lambda: home)
    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: _Registry(entries))
    resp = await handler.api_providers_list(object())
    return {p["name"]: p["key_in_store"] for p in json.loads(resp.text)["providers"]}


@pytest.mark.asyncio
async def test_a_configured_credential_reports_ok_not_missing(monkeypatch, tmp_path):
    """The defect, at the endpoint. `sk-configured` is right there in `credentials.json`."""
    (tmp_path / "credentials.json").write_text(
        json.dumps({"my-key": {"type": "api_key", "value": "sk-configured"}}),
        encoding="utf-8",
    )
    entries = [_Entry("openrouter", "openai_compatible", "gpt-4o", "my-key")]

    assert await _statuses(monkeypatch, tmp_path, entries) == {"openrouter": True}


@pytest.mark.asyncio
async def test_a_genuinely_absent_credential_still_reports_missing(monkeypatch, tmp_path):
    """The pair: "always there" would satisfy the test above and be just as wrong."""
    (tmp_path / "credentials.json").write_text(
        json.dumps({"my-key": {"type": "api_key"}}), encoding="utf-8"
    )
    entries = [_Entry("openrouter", "openai_compatible", "gpt-4o", "my-key")]

    assert await _statuses(monkeypatch, tmp_path, entries) == {"openrouter": False}


@pytest.mark.asyncio
async def test_an_undeclared_credential_name_still_reports_missing(monkeypatch, tmp_path):
    """The `KeyError` branch, kept reachable for the reason it exists rather than as a
    side-effect of pointing the store at a path that cannot hold anything."""
    (tmp_path / "credentials.json").write_text(json.dumps({"other": {}}), encoding="utf-8")
    entries = [_Entry("openrouter", "openai_compatible", "gpt-4o", "absent-key")]

    assert await _statuses(monkeypatch, tmp_path, entries) == {"openrouter": False}


@pytest.mark.asyncio
async def test_a_provider_declaring_no_credential_has_no_key_in_the_store(monkeypatch, tmp_path):
    """`credential=None` never reaches the store at all. It used to read "ok" here — which
    is how an instance with no key anywhere was badged "✓ Configured"."""
    entries = [_Entry("ollama", "openai_compatible", "llama3", None)]

    assert await _statuses(monkeypatch, tmp_path, entries) == {"ollama": False}


def test_no_call_site_passes_the_credentials_file_as_the_home():
    """The rail, because the fix is one argument and the mistake is invisible at the call site.

    `CredentialStore(home)` and `CredentialStore(home / "credentials.json")` are both valid
    Python that construct without raising — the store simply resolves a path that cannot exist
    and every `resolve` raises `KeyError`. Nothing else in the tree fails, which is how this
    survived. Asserted over the source so a future call site cannot re-make it.
    """
    offenders = []
    pattern = re.compile(r"CredentialStore\([^#]*?credentials\.json")
    for path in _SRC.rglob("*.py"):
        for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(_SRC)}:{num}: {line.strip()}")
    assert not offenders, (
        "CredentialStore takes the HOME and derives `<home>/credentials.json` itself; these "
        "pass the file, so the store reads `credentials.json/credentials.json`:\n"
        + "\n".join(offenders)
    )


def test_the_rail_would_catch_the_defect_it_was_written_for():
    """Floor: the regex must match the line this issue fixed, or the rail above is vacuous.

    Written red-first and it EARNED its keep: the first version used `[^)]*`, which can never
    cross the `)` in `config_dir()`, so it matched nothing and would have passed over the very
    line it was written to ban.
    """
    pattern = re.compile(r"CredentialStore\([^#]*?credentials\.json")
    assert pattern.search('store = CredentialStore(config_dir() / "credentials.json")')
    assert pattern.search('CredentialStore(home / "credentials.json")')
    assert not pattern.search("store = CredentialStore(config_dir())")
    assert not pattern.search("CredentialStore(home)")
    assert not pattern.search(
        "store = CredentialStore(config_dir())  # derives credentials.json itself"
    ), "a comment naming the file must not read as a call site passing it"

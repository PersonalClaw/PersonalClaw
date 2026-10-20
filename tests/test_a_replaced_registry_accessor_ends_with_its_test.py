"""A test that replaces the provider registry's accessor leaves every module the real one.

🔴 The defect. Many tests give themselves a private provider registry by replacing the accessor,
``monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)``.
Undoing that puts the registry module's name back, but a module IMPORTED while it was replaced
bound the replacement by name (``from personalclaw.llm.registry import get_default_registry``)
and kept it. ``test_a_named_model_is_said_not_swapped.py`` was the first test on its worker to
import ``personalclaw.sdk.model`` and ``personalclaw.sdk.provider_helpers`` that way, so every
provider app loaded later on that worker registered its type into that test's registry, which
nothing read again. ``test_best_of_n_provider_outage.py`` then failed all fifteen of its tests
with "type 'ollama', which no loaded app provides", but only in an order where it came after. Two
image tests did the same, and ``test_inert_surface_baseline.py`` then reported
``sdk.model.ProviderRegistry`` inert, because the accessor whose signature returns it was their
lambda.

``tests/conftest.py``'s ``_restore_provider_registry`` now starts every test by putting the real
accessor back in every module of ours that binds it. This file runs that order in a session of
its own, serially: one test replaces the accessor while the SDK is first imported, and the next
must find the real accessor in the SDK and its app's type in the real registry.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent

_REPLACES = """
import importlib
import sys

from personalclaw.llm import registry


def test_replaces_the_accessor_while_the_sdk_is_imported(monkeypatch):
    private = registry.ProviderRegistry()
    monkeypatch.setattr(registry, "get_default_registry", lambda: private)
    sys.modules.pop("personalclaw.sdk.model", None)
    model = importlib.import_module("personalclaw.sdk.model")
    assert model.get_default_registry() is private
"""

_READS = """
from personalclaw.llm import registry
from personalclaw.llm.capabilities import Capability, ProviderCapability


def test_the_next_test_registers_into_the_real_registry():
    from personalclaw.sdk import model

    assert model.get_default_registry is registry.get_default_registry
    model.get_default_registry().register_type(
        ProviderCapability(
            type="accessor-probe",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=False,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=1024,
        ),
        lambda **_kw: None,
    )
    assert "accessor-probe" in registry.get_default_registry()._factories
"""


def test_the_test_after_a_replaced_accessor_reads_the_real_registry(tmp_path: Path) -> None:
    (tmp_path / "test_a_replaces.py").write_text(textwrap.dedent(_REPLACES))
    (tmp_path / "test_b_reads.py").write_text(textwrap.dedent(_READS))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    # This suite's own conftest, as a plugin, over the two files in this order and on one process.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
            "-q",
            "--color=no",
            "test_a_replaces.py",
            "test_b_reads.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "2 passed" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-2000:]

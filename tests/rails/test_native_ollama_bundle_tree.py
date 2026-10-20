"""The source-tree checks split from
``tests/test_native_ollama_bundle.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import json

import source_index
from native_ollama_bundle_shared import _SRC_DIR

from personalclaw.apps.native_contract import NATIVE_DIR


def test_core_declares_no_ollama_provider_type():
    """The rule's negative half: no core module registers or implements an ollama provider.

    Ollama's de-coring removed a hardcoded integration from core, and bundling must not put
    one back. The two spellings that would count are a ``register_type`` call naming the
    type and a factory core owns, so both are checked against the source of every core
    module outside the bundled tree.

    Two Ollama-shaped things in core are deliberately NOT flagged, because neither is a
    provider registration and both predate this change: ``local_model_detect.py`` /
    ``seed_local_model.py`` (the credential-free on-ramp, which probes an endpoint and
    writes a ``providers[]`` entry) and ``guardrails/model_call.py``'s local-endpoint
    classification. They are coupling to the *name*, which is a separate debt; they do not
    make core a provider.
    """
    offenders: dict[str, list[str]] = {}
    for path in source_index.package_files():
        if NATIVE_DIR in path.parents or "egg-info" in str(path):
            continue
        text = source_index.text(path)
        hits = []
        if "register_type(OLLAMA" in text or 'register_type("ollama' in text:
            hits.append("registers the ollama provider type")
        if "def create_ollama_provider" in text or "class OllamaProvider" in text:
            hits.append("implements an ollama provider")
        if 'register_catalog("ollama' in text:
            hits.append("registers the ollama catalog")
        if hits:
            offenders[str(path.relative_to(_SRC_DIR))] = hits
    assert not offenders, (
        "core re-acquired a hardcoded Ollama provider registration — the exact thing the rule "
        "forbids. The registration belongs in the app's own provider.py, reached through "
        "personalclaw.sdk.model:\n" + json.dumps(offenders, indent=2)
    )

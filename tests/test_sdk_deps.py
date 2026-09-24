"""Optional-SDK import guard (plan 34 T1.4).

``require_sdk`` backs the lazy imports in the hosted-provider adapters after the
``openai`` / ``anthropic`` SDKs were demoted out of core dependencies. These
tests pin the two behaviours the adapters rely on: a present SDK imports
transparently, and an absent one raises a ``MissingSDKError`` that (a) names the
exact ``pip install personalclaw[<extra>]`` remedy and (b) is still catchable by
existing ``except ImportError`` guards.
"""

from __future__ import annotations

import sys

import pytest

from personalclaw._sdk_deps import MissingSDKError, require_sdk


def test_require_sdk_returns_module_when_present() -> None:
    # sys is always importable — require_sdk must hand back the real module.
    mod = require_sdk("sys", "irrelevant-extra")
    assert mod is sys


def test_require_sdk_raises_missing_with_remedy() -> None:
    with pytest.raises(MissingSDKError) as exc:
        require_sdk("personalclaw_definitely_absent_pkg", "openai")
    msg = str(exc.value)
    assert "personalclaw_definitely_absent_pkg" in msg
    # The exact remedy a user must run — this is the whole point of T1.4.
    assert "pip install 'personalclaw[openai]'" in msg
    assert "personalclaw doctor" in msg


def test_missing_sdk_error_is_import_error() -> None:
    # STT/TTS is_available() probes use `except ImportError`; MissingSDKError
    # must be caught by them so an absent SDK degrades to "unavailable".
    assert issubclass(MissingSDKError, ImportError)
    with pytest.raises(ImportError):
        require_sdk("personalclaw_definitely_absent_pkg", "anthropic")


def test_feature_phrase_included_when_given() -> None:
    with pytest.raises(MissingSDKError) as exc:
        require_sdk(
            "personalclaw_definitely_absent_pkg",
            "openai",
            feature="the OpenAI chat provider",
        )
    assert "required by the OpenAI chat provider" in str(exc.value)

"""Optional-SDK import guard (plan 34 T1.4).

Provider adapters for hosted vendors (OpenAI, Anthropic) import their SDK
*lazily* so that a bare ``pip install personalclaw`` stays lean — the SDKs are
demoted to the ``[openai]`` / ``[anthropic]`` extras and also ship via the
branded provider apps' manifest ``pythonDependencies``. When such an adapter is
actually constructed/used without its SDK present, the raw
``ModuleNotFoundError`` is unhelpful; ``require_sdk`` replaces it with a
``MissingSDKError`` that names the exact remedy and the doctor hint.
"""

from __future__ import annotations

import importlib
from types import ModuleType


class MissingSDKError(ImportError):
    """An optional provider SDK is not installed.

    The message names the exact ``pip install personalclaw[<extra>]`` remedy so a
    user hitting a hosted provider without its SDK knows precisely what to run.
    Subclasses ``ImportError`` so existing ``except ImportError`` guards (the STT/
    TTS ``is_available`` probes) keep treating it as "SDK absent".
    """


def require_sdk(module: str, extra: str, *, feature: str | None = None) -> ModuleType:
    """Import *module*, or raise :class:`MissingSDKError` with the remedy.

    Args:
        module: the importable module name (e.g. ``"openai"``).
        extra: the packaging extra that provides it (e.g. ``"openai"``), used in
            the ``pip install personalclaw[<extra>]`` remedy.
        feature: optional human phrase for what needed it (e.g. ``"the OpenAI
            chat provider"``) — sharpens the error when several adapters share an
            SDK.

    Returns:
        The imported module.
    """
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        needed_by = f" required by {feature}" if feature else ""
        raise MissingSDKError(
            f"The {module!r} SDK is not installed{needed_by}. Install it with "
            f"`pip install 'personalclaw[{extra}]'` (or `uv pip install "
            f"'personalclaw[{extra}]'`). If you installed a branded provider app, "
            f"reinstalling the app will pull it in. Run `personalclaw doctor` to "
            f"check provider dependencies."
        ) from exc

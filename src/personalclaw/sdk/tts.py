"""SDK: the text-to-speech (TTS) provider ABC.

Stable re-export of the ``TtsProvider`` ABC + ``TtsVoice`` — a TTS app implements
these and its factory returns a provider that core's tts registry resolves the ``tts``
use-case to. The core streaming voice-reply orchestration (strip/split/stitch/upload)
drives any provider through ``TtsProvider.synthesize`` and stays in core; a local TTS
backend's own synthesis (e.g. a piper subprocess) lives in the app, sandboxed via
``personalclaw.sdk.util.sandbox_wrap_argv``.
"""

from personalclaw.tts.provider import LocalTtsProvider, TtsProvider, TtsVoice  # noqa: F401

__all__ = ["TtsProvider", "LocalTtsProvider", "TtsVoice"]

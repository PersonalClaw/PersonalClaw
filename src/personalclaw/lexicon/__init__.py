"""Lexicon (core LEX) — the personal vocabulary + learned-corrections service.

Auto-built from the knowledge graph's entities, it (a) BIASES the STT decoder toward the
user's terms before transcription and (b) CORRECTS mis-heard terms after. Lives in core so
it improves every audio/video item + all voice input; surfaced + editable via the
Vocabulary UI (/api/lexicon/*).
"""

from personalclaw.lexicon.service import (  # noqa: F401
    CorrectionOutcome,
    LexiconService,
    get_lexicon_service,
    select_bias_terms,
)
from personalclaw.lexicon.store import LexiconStore, lexicon_db_path  # noqa: F401

__all__ = [
    "LexiconService",
    "LexiconStore",
    "CorrectionOutcome",
    "get_lexicon_service",
    "select_bias_terms",
    "lexicon_db_path",
]

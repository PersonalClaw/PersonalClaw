"""Pure query classifier for model routing (MODEL-ROUTING-TELEMETRY §2, MRT-1a).

Routing must not spend an LLM call to decide which model to route to, so the "what kind of
request is this" decision is a pure, deterministic heuristic — length bands, code-fence / regex
signals, the use-case label, and whether a structured (JSON-schema) output was requested. It maps
every request into ONE small fixed vocabulary:

    short_chat | code | summarize | extract_structured | long_reasoning

The vocabulary is a module constant, VERSIONED (:data:`CLASSIFIER_VERSION`) so the stats layer can
bucket by ``(use_case, query_class)`` and start fresh buckets when the vocabulary changes rather
than polluting old ones. Unclassifiable input falls back to ``short_chat`` — the cheapest-model-safe
default (routing a genuinely tiny/unknown request to the smallest model is never the wrong call).

No I/O, no model call, no state — same inputs always yield the same class, so a route decision is
reproducible and the classifier is trivially testable. The scoring/reordering that CONSUMES a class
lives in later slices; this module only assigns one.
"""

from __future__ import annotations

import re

# ── the fixed vocabulary (versioned) ─────────────────────────────────────────
SHORT_CHAT = "short_chat"
CODE = "code"
SUMMARIZE = "summarize"
EXTRACT_STRUCTURED = "extract_structured"
LONG_REASONING = "long_reasoning"

#: Every class the classifier can emit, in a stable order. A router candidate pool is scored per
#: (use_case, query_class), so this set is the closed vocabulary those buckets key on.
QUERY_CLASSES: tuple[str, ...] = (
    SHORT_CHAT,
    CODE,
    SUMMARIZE,
    EXTRACT_STRUCTURED,
    LONG_REASONING,
)

#: Bump when the vocabulary or the heuristic's class boundaries change. The stats file records
#: this so a vocabulary change starts fresh buckets instead of mixing incomparable history.
CLASSIFIER_VERSION = 1

# ── length bands (characters) ────────────────────────────────────────────────
# A short request is chat-sized; a long one is reasoning-sized. Bands, not a single threshold,
# so the middle is decided by content signals rather than length alone.
_SHORT_MAX = 240
_LONG_MIN = 2000

# ── content signals ──────────────────────────────────────────────────────────
# A fenced code block, or a run of code-ish punctuation/keywords, marks a coding request.
# Keywords require code-shaped context (a following identifier/paren, or a bracket/arrow), so an
# English sentence using "function"/"class"/"return" as ordinary words is NOT miscalled code.
_CODE_FENCE = re.compile(r"```|~~~")
_CODE_SIGNAL = re.compile(
    # Python-style def/class: keyword + name + "(" or ":" — unambiguous declaration shape.
    r"\bdef\s+\w+\s*\(|\bclass\s+\w+\s*[:(]"
    # A callable/keyword declaration with a paren: function(, async def, etc.
    r"|\bfunction\s+\w+\s*\(|\bfunction\s*\("
    # Typed var/const declarations: keyword + name + "=" (assignment), not bare prose.
    r"|\b(const|let|var)\s+\w+\s*="
    # Imports at line start (real import statements), not "import" used as an English verb.
    r"|^\s*(import|from)\s+[\w.]"
    # SQL verbs travel together (a lone "from"/"where" in prose is common; a SELECT…FROM isn't).
    r"|\bSELECT\b.*\bFROM\b"
    # Punctuation/operators that only appear in code.
    r"|[{};]\s*$|=>|::|</?[a-z][\w-]*>|\bstack ?trace\b|\btraceback\b",
    re.MULTILINE | re.IGNORECASE,
)
# An explicit ask to condense existing material.
_SUMMARIZE_SIGNAL = re.compile(
    r"\b(summar(?:ise|ize|y)|tl;?dr|recap|condense|digest|key points|"
    r"in (?:a few|one|two|three) (?:sentences|words|bullets)|"
    r"boil(?: it)? down|the gist)\b",
    re.IGNORECASE,
)
# An explicit ask for a machine-readable/structured answer (beyond the schema flag).
_STRUCTURED_SIGNAL = re.compile(
    r"\b(json|yaml|csv|as a table|valid schema|json schema|"
    r"return (?:a|an|the) (?:object|array|list|dict)|"
    r"structured (?:output|response|data)|key[- ]value pairs?)\b",
    re.IGNORECASE,
)
# Words that mark a request as reasoning-heavy even when it isn't long.
_REASONING_SIGNAL = re.compile(
    r"\b(step[- ]by[- ]step|reason through|think through|analy[sz]e|trade[- ]?offs?|"
    r"pros and cons|prove|derive|explain why|root cause|design a|architect|"
    r"compare and contrast|evaluate the)\b",
    re.IGNORECASE,
)

# ── use-case grounding ───────────────────────────────────────────────────────
# PClaw's own chat sub-categories (providers/use_cases.py CHAT_SUBCATEGORIES) carry a strong
# prior about the kind of work, independent of the text. Kept as bare strings so this pure module
# never imports the provider layer.
_CODE_USE_CASES = frozenset({"code_tools"})
_REASONING_USE_CASES = frozenset({"reasoning"})


def classify_query(
    text: str,
    use_case: str = "",
    *,
    wants_structured_output: bool = False,
) -> str:
    """Classify one request into the fixed :data:`QUERY_CLASSES` vocabulary.

    ``text`` is the prompt (joined content for a multi-message call); ``use_case`` is the binding
    label the call resolves under (a strong prior); ``wants_structured_output`` is True when the
    caller requested a JSON-schema-constrained answer (the capability channel), which is the
    strongest ``extract_structured`` signal there is.

    Deterministic and pure. Precedence is chosen so the most specific, cheapest-to-detect signal
    wins and the fallback is always the safe cheap default:

      1. explicit structured-output request → ``extract_structured``;
      2. a code fence or use_case=code_tools or dense code signals → ``code``;
      3. an explicit "condense this" ask → ``summarize``;
      4. long text, use_case=reasoning, or reasoning-marker words → ``long_reasoning``;
      5. otherwise → ``short_chat``.
    """
    body = text or ""
    stripped = body.strip()
    length = len(stripped)

    # 1. Structured output is an explicit contract on the RESPONSE shape — it outranks length or
    #    topic: a one-line "give me JSON" is still an extract_structured request.
    if wants_structured_output or _STRUCTURED_SIGNAL.search(stripped):
        return EXTRACT_STRUCTURED

    # 2. Code: a fence is unambiguous; the use-case prior is strong; otherwise require a real code
    #    signal (not just one stray brace) so prose about "the function of X" isn't miscalled.
    if _CODE_FENCE.search(body) or use_case in _CODE_USE_CASES or _CODE_SIGNAL.search(body):
        return CODE

    # 3. Summarize: an explicit ask to condense. Checked before the reasoning/length band so
    #    "summarize this long doc" routes to a summarizer, not a reasoner.
    if _SUMMARIZE_SIGNAL.search(stripped):
        return SUMMARIZE

    # 4. Long reasoning: genuinely long input, the reasoning use-case, or reasoning-marker words.
    if (
        length >= _LONG_MIN
        or use_case in _REASONING_USE_CASES
        or _REASONING_SIGNAL.search(stripped)
    ):
        return LONG_REASONING

    # 5. Anything short and unmarked — including empty/whitespace — is chat-sized. short_chat is
    #    the cheapest-model-safe default, so an unclassifiable request never over-provisions.
    if length <= _SHORT_MAX:
        return SHORT_CHAT

    # Mid-length with no content signal and no reasoning use-case: still ordinary chat.
    return SHORT_CHAT

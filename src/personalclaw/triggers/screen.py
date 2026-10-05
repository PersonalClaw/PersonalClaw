"""The pre-LLM injection screen + frozen capability set.

An unattended fire reads text nobody vetted — a webhook body, a watched file, an inbox message — and
hands it to a model that can act. Two independent controls, because either alone is insufficient:

1. **The screen** (this module's `screen()`) rejects payloads carrying injection attempts BEFORE
   any token is spent. Regex, ~0.2ms, zero cost.
2. **The frozen capability set** bounds what the run can do even if the screen misses something.
   A payload cannot cause any action outside the
   trigger's frozen capability set — verified adversarially, not asserted.

Defence in depth is the point: a screen is a filter, not a proof, and the honest design assumes it
will be evaded.

**Measured before writing.** `vector_memory._INJECTION_PATTERNS` (14 patterns) is the only screen in
the repo, and it is private to memory writes. Probed against six OWASP injection groups it caught
**5 of 18** adversarial payloads — 0 of 3 on token smuggling, jailbreak, and indirect injection —
while tripping on **2 of 3 ordinary sentences** ("summarize the system prompt design doc", "act as
if the deploy already happened"). So it is wrong in BOTH directions, and reusing it would have
shipped a control that blocks real work and misses real attacks.

Both failure directions matter here and they are not symmetric:

* A **miss** admits an attack, and the capability fence is what stops it becoming an action.
* A **false positive** on ordinary text silently kills a legitimate automation. That is why the
  patterns below require an imperative/second-person frame rather than matching bare topic words:
  `system\\s*prompt` alone flags anyone who mentions the phrase.

Normalization runs FIRST, because the smuggling group exists to defeat naive matching: zero-width
characters, homoglyph digits, and base64 all carry `ignore all previous instructions` past a literal
regex. Pure functions; the caller decides what to do with a verdict.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Zero-width and bidi-control characters. Individually invisible, collectively enough to break
#: every pattern below by splitting a keyword mid-word. Stripped before matching rather than
#: rejected on sight: they legitimately appear in text pasted from rich sources, so their PRESENCE
#: is not the attack — what they hide is.
_INVISIBLE = dict.fromkeys(
    [
        0x00AD,  # soft hyphen
        0x200B,  # zero-width space
        0x200C,  # zero-width non-joiner
        0x200D,  # zero-width joiner
        0x200E,  # left-to-right mark
        0x200F,  # right-to-left mark
        0x202A,  # bidi embedding
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2060,  # word joiner
        0xFEFF,  # BOM / zero-width no-break space
    ]
)

#: Homoglyph folds for leet-style evasion (`1gn0re all prev1ous 1nstruct10ns`). Deliberately a SMALL
#: map: folding aggressively (every visually-similar Unicode codepoint) turns ordinary text into
#: keyword soup and drives false positives, which is the more damaging direction here.
_HOMOGLYPHS = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a"})

#: Minimum length before a token is considered a base64 payload worth decoding. Short strings decode
#: to noise constantly, and every false decode is a chance to match a pattern in garbage.
_B64_MIN_LEN = 24

_B64_RE = re.compile(r"[A-Za-z0-9+/]{%d,}={0,2}" % _B64_MIN_LEN)


class Verdict(str, Enum):
    """The screen's answer.

    `SUSPICIOUS` exists as a distinct middle state so a caller can fence-and-proceed rather than
    facing a binary "run it or drop it" choice. Collapsing it into BLOCK would make the screen
    unusable at scale (too many legitimate payloads discuss instructions); collapsing it into CLEAN
    would waste the signal.
    """

    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    BLOCKED = "blocked"


#: The space between two words a pattern below reads together: any run of spaces, as `normalize`
#: folds it to one (so the raw and folded passes agree), and never a line break.
_GAP = r"[^\S\n]+"
#: The words that name what a model was told to do.
_ORDERS = r"(instruction|prompt|direction|directive|guideline|rule)s?\b"
#: Orders placed before this text, which are the model's own rather than a document's.
_EARLIER = r"(?:previous|prior|above|earlier)" + _GAP
#: "all", "any", "all of": a quantifier before the orders.
_ALL = r"(?:all|any)" + _GAP + r"(?:of" + _GAP + r")?"
#: The model's own orders, of any kind a possessive names: "your system prompt", "your rules".
_YOUR = r"your" + _GAP + r"(?:(?:previous|prior|original|initial|current|system|safety|core)"
_YOUR += _GAP + r")?"
#: The text a side task is set on: "this page", "it", "the above".
_THIS = r"(?:this|it|these|the" + _GAP + r"(?:above|following))(?:" + _GAP + r"[a-z]{2,12})?"
#: Dropping orders, said as an order and not as a refusal: "do not ignore your instructions" and
#: "I cannot disregard the above instructions" ask for nothing. The verb is looked for before the
#: refusal is, so the look back runs only where a verb starts.
_VERBS = r"ignore|disregard|forget|discard"
_DROP = rf"\b(?={_VERBS})(?<!\bnot )(?<!cannot )(?<!n't )(?<!n’t )(?<!\bnever )({_VERBS})"
_DROP += _GAP

#: The six OWASP injection groups, each with the patterns that identify it. Every pattern keys on
#: what the text asks of the model reading it (to drop what it was told, take a new role, show its
#: configuration, take on a side task) and never on a topic word, a command or a markup character
#: alone: "ignore your instructions", not the word "instructions". The existing memory screen's bare
#: `system\s*prompt` flags "summarize the system prompt design doc", and `act\s+as\s+if` flags
#: "act as if the deploy already happened". A screen that blocks ordinary sentences gets disabled
#: by its users, which is a worse outcome than a narrower screen.
#:
#: What reaches the screen is mostly code and its output: a tool's result, an agent's reply, a
#: watched file. So a pattern that cannot tell a take-over from a Markdown table, a pipeline, a
#: stack trace or a commit message is scoped to the take-over or dropped, never widened
#: (`tests/test_the_injection_screen_passes_ordinary_text_and_refuses_a_take_over.py` is the table
#: every pattern must pass). A scoped pattern matches a subset of what its looser form matched,
#: apart from a run of spaces longer than that form allowed, and never reads across a line.
INJECTION_GROUPS: dict[str, tuple[str, ...]] = {
    "override": (
        # Drop the model's earlier orders: "ignore all previous instructions", "forget the above
        # rules". Only a quantifier and an article may stand between: "ignore the previous failing
        # run; the rule is flaky" is a review, and a command is not an order of the model's
        # ("forget the earlier command and use make test").
        _DROP + f"(?:{_ALL})?" + r"(?:(?:the|your)" + _GAP + r")?" + _EARLIER + _ORDERS,
        # Drop YOUR orders: "disregard your instructions", "ignore your system prompt". Not "my
        # earlier comment about the prompt", which the writer takes back.
        _DROP + f"(?:{_ALL})?" + _YOUR + _ORDERS,
        # Drop every one: "ignore all instructions". Not prompts or rules: "ignore all prompts" is a
        # CLI flag, and "to ignore all generated files, add these rules" is advice.
        _DROP + _ALL + r"(?:the" + _GAP + r")?(instruction|directive)s?\b",
        # Override YOUR orders or the earlier ones: "override your safety rules". "Override the
        # system prompt per agent" is a setting, and "later rules override earlier rules" is how a
        # stylesheet works.
        r"\b(override|overrule|supersede)\b[^\w.\n]{0,3}(?:"
        + (_YOUR + r"(instruction|prompt|rule|directive|guardrail|safety)s?\b|")
        + (r"(?:all" + _GAP + r")?(?:the" + _GAP + r")?" + _EARLIER)
        + r"(instruction|prompt|directive)s?\b)",
        # Hand the model a new purpose: "your new instructions are ...". Not a task or a goal:
        # "your new task is on Thursday's list" is what a task assistant says.
        r"\byour\s+new\s+(instruction|directive|purpose)s?\s*(?:are\b|is\b|:)",
        # Not here: a "Note:" or "Important:" line that says ignore or override, and a line that
        # opens "New instructions:" or "New rule:". Neither names an order of the model's, so
        # "Note: ignore the deprecation warnings" and a release note's "New rule:" read the same.
    ),
    "token_smuggling": (
        # Matched only AFTER normalization, so these are the post-fold forms.
        r"\bignore\s*all\s*previous\s*instructions?\b",
        r"\bdisregard\s*(all\s*)?(previous|prior)\s*(instructions?|prompts?)\b",
    ),
    "persona_hijack": (
        # Not a state a service reports to a person: "you are now subscribed", "logged in".
        r"\byou\s+are\s+(now|from\s+now\s+on)\b(?![^\S\n]+(?:subscribed|unsubscribed|logged|signed|"
        r"connected|disconnected|registered|enrolled|verified|following|able|ready|set\s+up|"
        r"all\s+set|up\s+to\s+date|on\s+(?:the|version|v?\d))\b)",
        # Addressed: "from now on you are ...", "from now on, respond ...". Not "from now on, the
        # bot will reply in the thread".
        r"\bfrom\s+now\s+on,?" + _GAP + r"(you|respond|reply|act|behave)\b",
        # Not "imagine": "imagine you have a million rows" is an explanation.
        r"\b(pretend|roleplay|role-play)\b[^.\n]{0,20}\byou\s+(are|were|have)\b",
        r"\bact\s+as\s+(a|an|the)?\s*(unrestricted|uncensored|jailbroken|evil|malicious|rogue)\b",
        # Requires an ADOPTION verb: bare "new persona" is ordinary product vocabulary ("our new
        # persona research covers three segments") and flagged it as suspicious — measured.
        r"\b(adopt|assume|become|switch\s+to|take\s+on)\b[^.\n]{0,16}\b(new|different|alternate)?"
        r"\s*persona\b",
        r"\byour\s+(new|real|true)\s+persona\s+is\b",
        r"\byou\s+will\s+(now\s+)?respond\s+as\b",
    ),
    "jailbreak": (
        # Not developer or debug mode: a browser, a phone and an editor each have one.
        r"\b(enable|activate|enter|switch\s+to)\b[^.\n]{0,16}\b(god|dan|unrestricted|unfiltered|"
        r"jailbreak)\s*mode\b",
        # No rules for YOU: "no rule applies here" is a code comment.
        r"\byou\b[^.\n]{0,16}\b(no|without|ignore)\b[^.\n]{0,16}\b(restrictions?|limitations?|"
        r"filters?|guardrails?|safety|rules?)\b[^.\n]{0,16}\b(apply|now|anymore|here)\b",
        r"\bthis\s+is\s+(a\s+)?(hypothetical|fictional|simulation|test)\b[^.\n]{0,30}\b(no\s+"
        r"rules?|nothing\s+is\s+forbidden|anything\s+is\s+allowed|no\s+restrictions?)\b",
        r"\b(for\s+)?(educational|research|academic)\s+purposes?\s+only\b[^.\n]{0,40}\b(bypass|"
        r"ignore|disable|circumvent)\b",
        # YOUR safety: "disable the spam filter" and "bypass the cache policy" are settings.
        (r"\b(bypass|circumvent|disable|turn\s+off)\b[^\w.\n]{0,3}(?:all" + _GAP + r")?your" + _GAP)
        + (r"(?:\w{1,8}" + _GAP + r")?(safety|filter|guardrail|restriction|policy|protection)s?\b"),
    ),
    "prompt_leaking": (
        # YOUR prompt or THE system prompt: "print the prompt before sending it" debugs an app.
        r"\b(repeat|print|show|reveal|output|display|echo|dump)\b[^.\n]{0,30}\b(?:your\s+"
        r"(system\s+prompt|initial\s+instructions?|original\s+instructions?|prompt|instructions?)"
        r"|the\s+(system\s+prompt|initial\s+instructions?|original\s+instructions?))\b",
        r"\bwhat\s+(were|are)\s+your\s+(original|initial|system|actual)\s+"
        r"(instructions?|prompts?|rules?)\b",
        r"\b(print|show|repeat|output|reveal)\b[^.\n]{0,20}\beverything\s+(above|before)\b",
        r"\b(verbatim|word\s+for\s+word|exactly)\b[^.\n]{0,24}\b(system\s+prompt|"
        r"your\s+instructions?)\b",
    ),
    "indirect": (
        # Injections hidden in structural markup a model may read but a human reviewer will not.
        # A comment that opens by addressing a model ("<!-- AI: ...") and not one that mentions an
        # agent ("<!-- Generated by the docs agent, do not edit -->").
        r"<!--\s*(?:(?:note\s+to|to|dear|hey|attention)\s+(?:the\s+)?)?(ai|assistant|agent|llm|"
        r"system)\b\s*[:,]",
        # Not "[[AI|artificial intelligence]]", a wiki link with its label.
        r"\[\[\s*(system|assistant|ai|agent)\s*:",
        # A role or instructions tag whose words speak to the model ("<system>You have ..."). Not
        # a Maven project's "<system>GitHub</system>" or an app's own prompt template.
        r"<\s*(system|assistant)\s*>\s*(?:you|your|ignore|disregard|forget)\b",
        r"<\s*/?\s*(instruction|system_prompt)s?\s*>\s*(?:you|your|ignore|disregard|forget)\b",
        # A side task for whoever reads THIS text: "When summarizing, also run ...", "when reading
        # this page, send ...". Not "When analyzing the logs, I'll also run the test suite", which
        # names its own object and its own doer.
        (r"\bwhen\s+(summariz|process|read|analyz)\w*(?:" + _GAP + _THIS + r")?,?" + _GAP)
        + (r"(?:also" + _GAP + r")?(run|execute|exec|eval|curl|wget|send|post|email|upload)\b"),
        # Not here: a pipe into a shell or an interpreter. It is inert until something tells the
        # model to run it, which the pattern above reads, and the same pipe is a README's install
        # line, a table's cell and a regex's alternation.
    ),
}

#: Groups whose match is a hard BLOCK. Override and smuggling have no legitimate reading in an
#: untrusted payload: nobody writes "ignore all previous instructions" in a webhook body by
#: accident, and smuggling is only detectable at all because someone tried to hide it.
#: Persona/jailbreak/leaking get `SUSPICIOUS` — they overlap with real discussion of AI behaviour,
#: and a fenced run is the proportionate response.
BLOCKING_GROUPS: frozenset[str] = frozenset({"override", "token_smuggling", "indirect"})

_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    group: tuple(re.compile(p, re.IGNORECASE) for p in patterns)
    for group, patterns in INJECTION_GROUPS.items()
}


def normalize(text: str) -> str:
    """Fold the evasions the smuggling group exists to exploit.

    NFKC → strip invisibles → casefold → homoglyph fold → collapse whitespace. Order matters: NFKC
    first turns compatibility forms into their canonical equivalents (so a fullwidth `ｉｇｎｏｒｅ`
    becomes matchable), and invisibles are removed before whitespace collapse so a zero-width space
    inside a word closes up rather than becoming a word boundary.

    Whitespace is collapsed, not deleted: deleting it would fuse ordinary adjacent words into
    accidental keyword matches, which is a false-positive source in the direction that kills real
    work.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    folded = folded.casefold().translate(_HOMOGLYPHS)
    return re.sub(r"[ \t ]+", " ", folded)


def decoded_segments(text: str, *, limit: int = 8) -> list[str]:
    """Base64-looking runs decoded to text, for the smuggling group.

    Decoding is bounded (`limit` segments) because a payload can contain arbitrarily many base64-ish
    runs, and screening is supposed to cost ~0.2ms — an unbounded decode is a cheap way to make the
    security check itself the denial of service.

    A segment that does not decode to mostly-printable text is dropped rather than matched: random
    base64 decodes to bytes that can contain anything, and matching patterns inside binary garbage
    produces false positives with no attacker involved.
    """
    out: list[str] = []
    for match in _B64_RE.finditer(text or ""):
        if len(out) >= limit:
            break
        chunk = match.group(0)
        # Base64 needs length % 4 == 0; pad rather than skip, since a run embedded in prose is often
        # captured without its padding.
        padded = chunk + "=" * (-len(chunk) % 4)
        try:
            raw = base64.b64decode(padded, validate=True)
        except (binascii.Error, ValueError):
            continue
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        printable = sum(1 for c in decoded if c.isprintable() or c.isspace())
        if decoded.strip() and printable / max(1, len(decoded)) > 0.9:
            out.append(decoded)
    return out


@dataclass
class ScreenResult:
    """One screening verdict, with everything a ledger row needs.

    `matched_group` and `matched_pattern` are both carried because a blocked payload's
    row must NAME the pattern: "blocked_injection" with no detail is unauditable, and a user who
    thinks the screen is wrong has nothing to appeal against.
    """

    verdict: str
    matched_group: str = ""
    matched_pattern: str = ""
    #: Every group that matched, not just the first — a payload hitting four groups is a different
    #: thing from one borderline match, and a reviewer should see that.
    groups: tuple[str, ...] = ()
    #: True when the match was only visible after normalization/decoding. Recorded because a hidden
    #: attempt is strictly more hostile than a plain one: nobody smuggles by accident.
    evaded: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.verdict == Verdict.BLOCKED.value

    @property
    def clean(self) -> bool:
        return self.verdict == Verdict.CLEAN.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "matched_group": self.matched_group,
            "matched_pattern": self.matched_pattern,
            "groups": list(self.groups),
            "evaded": self.evaded,
            "notes": list(self.notes),
        }


def screen(text: str) -> ScreenResult:
    """Screen an untrusted payload before any token is spent.

    Runs the patterns three times — raw, normalized, and over decoded base64 — because each pass
    sees what the others cannot. The raw pass catches markup-shaped injections whose case and
    spacing carry meaning; the normalized pass catches smuggling; the decode pass catches payloads
    hidden entirely from both.

    Never raises. A screen that throws on a weird payload is a screen that fails OPEN under exactly
    the input an attacker controls, so every stage is defensive and the fallthrough is CLEAN only
    when no stage matched.
    """
    if not text or not text.strip():
        return ScreenResult(verdict=Verdict.CLEAN.value)

    hits: list[tuple[str, str, bool]] = []
    raw = text
    normalized = normalize(text)

    # `evaded` means "this match was only visible AFTER folding", never "the text changed when
    # folded". Comparing `normalized != raw.casefold()` marked ordinary text as evasion,
    # because homoglyph folding rewrites the digits in "Q3 numbers" to "qe numbers" — so every
    # payload containing a digit escalated to a BLOCK. The flag is now derived per pattern: it is
    # set only when the raw pass missed and the folded pass hit, which is the actual signal of
    # hiding. The patterns are already IGNORECASE, so a raw hit covers any case spelling — case
    # alone is never evasion, only invisibles/homoglyphs/base64 are.
    raw_hits: set[tuple[str, str]] = set()
    for group, patterns in _COMPILED.items():
        for pattern in patterns:
            try:
                if pattern.search(raw):
                    raw_hits.add((group, pattern.pattern))
                    hits.append((group, pattern.pattern, False))
                    break
            except Exception:  # pragma: no cover - a regex engine failure must not fail open
                continue

    for group, patterns in _COMPILED.items():
        for pattern in patterns:
            try:
                if pattern.search(normalized):
                    hits.append((group, pattern.pattern, (group, pattern.pattern) not in raw_hits))
                    break
            except Exception:  # pragma: no cover
                continue

    for segment in decoded_segments(text):
        folded = normalize(segment)
        for group, patterns in _COMPILED.items():
            for pattern in patterns:
                if pattern.search(folded):
                    hits.append((group, pattern.pattern, True))
                    break

    if not hits:
        return ScreenResult(verdict=Verdict.CLEAN.value)

    groups = tuple(sorted({g for g, _p, _e in hits}))
    evaded = any(e for _g, _p, e in hits)
    # A BLOCKING group anywhere wins; otherwise the payload is suspicious and gets fenced. An
    # evaded match is ALSO a block regardless of group: hiding the attempt is itself the evidence
    # of intent, and treating a smuggled persona hijack as merely "suspicious" would reward the
    # obfuscation.
    # Named `hit_*` rather than reusing `group`/`pattern`: those are bound in the scan loops
    # above to a compiled `Pattern`, so mypy unifies the types and rejects the assignment.
    blocking = [(g, p) for g, p, _e in hits if g in BLOCKING_GROUPS]
    if blocking or evaded:
        hit_group, hit_pattern = (blocking or [(hits[0][0], hits[0][1])])[0]
        notes = [f"matched the {hit_group} group"]
        if evaded:
            notes.append("the match was hidden by encoding or invisible characters")
        return ScreenResult(
            verdict=Verdict.BLOCKED.value,
            matched_group=hit_group,
            matched_pattern=hit_pattern,
            groups=groups,
            evaded=evaded,
            notes=notes,
        )
    hit_group, hit_pattern, _ = hits[0]
    return ScreenResult(
        verdict=Verdict.SUSPICIOUS.value,
        matched_group=hit_group,
        matched_pattern=hit_pattern,
        groups=groups,
        notes=[f"matched the {hit_group} group; the payload is fenced rather than dropped"],
    )


# ── the frozen capability set (the second adversarial control) ──

#: Capability keys a trigger may declare. A closed vocabulary, because an allowlist whose KEYS are
#: open is not an allowlist: a typo'd `tool` (singular) would silently grant nothing and read as
#: "no restriction" to any code that checks `capabilities.get("tools")`.
CAPABILITY_KEYS: frozenset[str] = frozenset({"tools", "providers", "paths", "env", "network"})

#: What an EMPTY capability set means. `deny` — a trigger that declared nothing gets nothing.
#:
#: This is the load-bearing choice in the whole module. The permissive reading ("unspecified means
#: unrestricted") is the more natural one to implement and it makes the fence decorative: every
#: trigger authored before capabilities existed, and every one whose author skipped the field, would
#: run unbounded. Deny-by-default means such a trigger fails visibly and gets fixed, which is the
#: direction that cannot silently lose a security property.
EMPTY_MEANS = "deny"


@dataclass
class CapabilityDecision:
    """Whether one action is inside a trigger's frozen set.

    Carries the reason because a refusal a user cannot explain is a refusal they will work around —
    typically by widening the allowlist far past what the automation needed.
    """

    allowed: bool
    reason: str = ""
    #: The capability key consulted, so a ledger row says WHICH fence refused.
    key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason, "key": self.key}


def _matches_entry(value: str, entry: str) -> bool:
    """Whether `value` satisfies one allowlist entry.

    Supports a trailing `*` prefix-glob only (`mcp__github__*`), NOT arbitrary fnmatch. A bare `*`
    inside an entry would let `*danger*` read as an allowance, and `?`-style wildcards make an
    allowlist hard to reason about at a glance — which is the property that matters most for a
    security control someone edits by hand.
    """
    if not entry:
        return False
    if entry == "*":
        return True
    if entry.endswith("*"):
        return value.startswith(entry[:-1])
    return value == entry


#: 🔴 THE READ-ONLY DEFAULT, as data. Auto-fired triggers (clock/event/file/webhook/
#: view/web_watch) default to read-only action providers; write-capable actions require explicit
#: opt-in rendered as a badge on the Automations row.
#:
#: Classified by what each provider DOES, read from its own module — not by grepping for
#: write-shaped calls, which mis-sorted two on the first pass (`run-script` runs a sandboxed Python
#: script and IS write-capable; `knowledge-retrieve` only queries).
#:
#: A provider absent from BOTH sets is treated as write-capable by `provider_is_read_only`. An
#: unclassified action must not become a hole — the same direction `EMPTY_MEANS` takes, and the same
#: reason: a new provider added without a line here fails visibly rather than running unbounded.
READ_ONLY_PROVIDERS: frozenset[str] = frozenset(
    {
        "notify",  # raises a dashboard notification
        "send-message",  # delivers to a channel the user already configured
        "create-task",  # files a task row — the user's own inbox, no external effect
        "knowledge-retrieve",  # queries the knowledge store
        "knowledge-health",  # deterministic store health report, zero tokens
        "knowledge-gaps",  # finds referenced-but-unwritten entities, zero tokens
        "artifact_inspect",  # reads a run's own artifacts, path-escape confined; no side effect
        # Read-only git inspection (hex-validated refs, fixed argv, no shell) whose only
        # effect is a run-ledger row. Zero tokens and nothing user-visible, so it belongs with
        # the deterministic knowledge probes above rather than with the writers below.
        "selfqa-triage",
        # The check-work verification node. Zero tokens and bounded filesystem READS
        # confined under the given root (`check_work._resolve` refuses an escape); it never
        # shells out — the provider injects no command runner, so a command check is reported
        # `unverifiable` rather than executed. Its only output is the report in the node's own
        # result, so it belongs with the deterministic probes above.
        "check-work",
    }
)

#: Providers that need explicit opt-in. Spelled out rather than derived as "everything else" so the
#: security-relevant list is greppable and reviewable in one place — and so a diff that moves a
#: provider between the two sets is visible as exactly that.
WRITE_CAPABLE_PROVIDERS: frozenset[str] = frozenset(
    {
        "bash",  # arbitrary shell
        # Drives a declared app route, and a route can write, send or delete: the app's own
        # permissions bound what its backend may do, not which of its routes a trigger runs
        # unasked. It is read-only exactly when the route it drives DECLARES `readOnly` (the
        # declared effect, `CallAppRouteActionProvider.effect`), which `provider_is_read_only`
        # asks with the action's config (`READ_ONLY_WHEN_DECLARED`).
        "call-app-route",
        "run-script",  # sandboxed Python, but still executes author-supplied code
        "run-prompt",  # spawns an LLM turn with the unattended toolset
        "invoke-agent",  # same, with an agent persona
        "run-workflow",  # a whole workflow run
        "knowledge-persist",  # writes the knowledge store
        "knowledge-consolidate",  # `apply: true` writes the consolidation
        # Writes typed item relations, and the edges it writes were PROPOSED BY A
        # MODEL over stored claims that can have come from a web page. The write is narrow by
        # construction (closed five-verb vocabulary, both endpoints must be real rows,
        # confidence clamped below 1.0, no free text stored), but "narrow" is not "read-only":
        # it mutates the store unattended, so it belongs on the side of this table that needs
        # an explicit opt-in.
        "knowledge-relate",
        "artifact-update",  # mutates an artifact
        "render-report",  # writes the spec artifact + its derived export
        "notification-digest",  # writes an inbox item
        "usage-recap",  # emits a notification — unattended, so it needs the opt-in
        # The remediation engine DELETES history files, prunes the SEL and rebuilds indexes,
        # unattended, forever — the most destructive local writer in this table, and the only one
        # whose failure mode is silent (an absent prune is invisible by nature). A frozen grant is
        # required, and this is the only honest side of the table for it.
        "self-remediation",
        # The HEARTBEAT.md task queue: every task is an unattended agent turn with its tools, on a
        # 60-second clock, forever — the strictest side of this table, like `run-prompt`.
        "heartbeat-tasks",
        # The morning digest: writes a knowledge item AND notifies, on a cron, forever. It
        # also spends a model call over SCRAPED text, which crosses the untrusted-input
        # boundary — the strictest side of this table is the only honest one for it.
        "source-digest",
        # The periodic identity report: writes a versioned artifact AND raises an inbox item,
        # on a cron, forever, and spends one background model call over FENCED user prose (a
        # facet's text came from a turn and a turn can carry an injection —
        # `narrate_identity_report` fences it for exactly that reason). Same side of the table as
        # `source-digest` above and for the same three reasons.
        "identity-report",
        # Propose-don't-write is about the KNOWLEDGE store, not about this fence: filing still
        # writes a durable proposal row and raises an inbox item, exactly like
        # `notification-digest` above. A path that puts things in front of the user unattended
        # needs the opt-in — an unbounded one turns the review queue into the noise it exists
        # to prevent.
        "knowledge-propose",
        # A scheduled research report spends a model call and writes a knowledge item
        # unattended, on a cron, forever. Write-capable is the only honest side of this table for
        # it — and being explicit here is what keeps the fail-closed default from being the reason.
        "knowledge-report",
        # Files a Task AND raises an inbox item. `create-task` sits in the read-only table
        # above because a task row has no external effect, but the inbox item is the same
        # unattended "puts something in front of the user" write that puts `notification-digest`
        # on this side — so the pair lands here, on the stricter of the two classifications.
        "selfqa-file-finding",
        # Registers an evidence Artifact AND, on a confirmed failure with the flag on,
        # opens a LOCAL `pclaw/selfqa-<sha8>` branch (never pushed). `create-task` sits in the
        # read-only table because a task row has no external effect, but writing an artifact and
        # creating a branch are unattended local writes, so this lands on the stricter side beside
        # `selfqa-file-finding`.
        "selfqa-evidence",
        # The commit watch STARTS a workflow run unattended on every push — the same
        # spend-and-act write that puts `run-workflow` itself on this side. The delta half is
        # read-only git, but the pair lands on the stricter classification, exactly as
        # `selfqa-file-finding` records directly above.
        "selfqa-commit-watch",
        # The triage digest spends two background model calls and DELIVERS a notification,
        # unattended, on a cron, forever. It executes no proposal — that is the `inbox-op`
        # under a budget floor — but "proposes nothing and only notifies" is still the same
        # unattended puts-something-in-front-of-the-user write that puts `notification-digest`
        # and `usage-recap` on this side, and the spend alone earns the opt-in.
        "triage-digest",
        # `inbox-op` mutates inbox rows — archives, dismisses, mutes a thread, writes a
        # draft. Every one is reversible, which is what earns it the auto-execution class, but
        # reversible is not read-only: an unattended cron that could dismiss the user's inbox
        # without an explicit capability opt-in is precisely what this table exists to prevent.
        "inbox-op",
        # Drives a real browser. Write-capable is not a close call
        # — a SUBMIT is an irreversible POST on somebody else's site — but even a read-only browse
        # belongs here, because the loop spends a model call PER STEP over attacker-controlled page
        # text. That is both the untrusted-input boundary and an unbounded unattended
        # spend, and either alone earns the opt-in.
        "browse",
        # `net-fetch` performs a GET, and a GET is NOT read-only for this table's purpose.
        # Three reasons, each sufficient on its own:
        #   * it LEAVES THE MACHINE. Every other id in the read-only set above stays local (a
        #     dashboard row, a task row, a knowledge query, a run's own artifacts). A request to a
        #     third party is observable by that third party, and on a schedule it is a repeating
        #     signal about when this machine is awake and what it watches.
        #   * its OUTPUT is attacker-controlled. The provider fences and bounds the body, but the
        #     next node in the template is free to feed that text to a model — the same
        #     untrusted-input boundary that puts `source-digest` and `browse` on this side.
        #   * `provider_is_read_only` fails CLOSED, so omitting the line entirely would land it here
        #     anyway. Spelling it out is what makes the decision reviewable instead of accidental —
        #     which is this table's stated whole value, and what `test_capability_table_ids`
        #     enforces in both directions.
        # The egress allow-list is a real control but it is the OPERATOR's, set once globally; this
        # opt-in is the per-trigger one, and neither substitutes for the other.
        "net-fetch",
        # Best-of-N spends N sampling calls PLUS one judge pass per surviving candidate,
        # every one a real model call. It writes nothing a user sees (its output is the node's
        # own result and one bounded derived-telemetry row), but an unattended cron that could
        # multiply model spend by up to 5 without an explicit opt-in is the same unattended-spend
        # shape that puts `triage-digest` on this side — and the spend alone earns the opt-in.
        "best-of-n",
        # The optimize-harness scoring step spends two model calls per recorded run it scores a
        # candidate against. It writes nothing, but unattended model spend earns the opt-in, as
        # it does for `best-of-n` directly above.
        "optimize-score",
        # The second-opinion handoff spawns a cataloged runner (or a subagent) one-shot
        # with write access to a real workspace — the strictest side of this table is the only
        # honest one for it. Note the disk re-diff that gates ACCEPTANCE is not a substitute for
        # this opt-in: the proposer's edits are already on disk by the time the re-diff runs, so
        # what the gate protects is whether we BELIEVE the result, not whether files were written.
        "second-opinion",
        # The outbound A2A half: `a2a-call` hands a task to an external agent over the network.
        # Fail-closed already answers this one correctly — `provider_is_read_only` returns False
        # for any unclassified name — but the classification is stated rather than inferred
        # because the honest reason is specific: a delivered A2A task is IRREVERSIBLE in a way
        # no local write is. There is no un-send, the remote side may bill and act on it, and the
        # response is attacker-controlled text this fence exists to screen. Note `webhook`, the
        # app-delivered precedent, is absent from both sets and rides the fail-closed default;
        # naming this one is the house rule applied, not a behavior change.
        "a2a-call",
    }
)


# Classified ids whose provider ships in a first-party APP BUNDLE rather than core's
# registry. `list_action_providers()` cannot see these — an app is installed separately, and
# core's test suite installs none — so their absence from the registry is expected and is NOT
# the "entry that fences nothing" the phantom-id rail exists to catch.
#
# This set exists because the rail was written for core-native providers and read every
# unregistered classified id as misleading. Its motivating case really was misleading:
# `knowledge-maintain` was a MODULE name that no provider answered to. An app-delivered id is
# the opposite — a real provider, reachable the moment its bundle is installed. Conflating the
# two would force app-delivered providers to stay UNCLASSIFIED to keep the rail green, which
# is how `webhook` ended up in neither table: not a decision, just the only way to pass. That
# leaves the most consequential providers as the only unreviewable ones, inverting the point
# of a table whose whole value is being greppable in one place.
APP_DELIVERED_PROVIDERS: frozenset[str] = frozenset(
    {
        # The provider lives in PersonalClawApps as `a2a-action`.
        "a2a-call",
    }
)


#: Write-capable providers whose effect depends on what they are pointed at: read-only for one
#: action exactly when that action DECLARES a read (`ActionProvider.effect` returns
#: ``RiskLevel.SAFE`` for its config). Each is listed in `WRITE_CAPABLE_PROVIDERS` too, which is
#: what it is when nothing declares otherwise.
READ_ONLY_WHEN_DECLARED: frozenset[str] = frozenset({"call-app-route"})


def provider_is_read_only(provider: str, config: dict[str, Any] | None = None) -> bool:
    """Whether `provider` is safe to auto-fire without an explicit capability opt-in.

    Fails CLOSED for an unknown name: an action nobody classified is treated as write-capable, so a
    provider added without a line in the tables above needs an opt-in rather than inheriting the
    permissive default. That is the same choice `EMPTY_MEANS` makes one level up.

    *config* is the action's own config, for a provider in `READ_ONLY_WHEN_DECLARED`: read-only
    when that action declares a read, and without a config never.
    """
    name = (provider or "").strip()
    if not name:
        return False
    if name in READ_ONLY_WHEN_DECLARED:
        if not isinstance(config, dict):
            return False
        from personalclaw.action_providers.registry import action_effect
        from personalclaw.tool_providers.base import RiskLevel

        return action_effect(name, config) == RiskLevel.SAFE
    return name in READ_ONLY_PROVIDERS


def action_config(trigger: Any) -> dict[str, Any]:
    """The config of `trigger`'s declared action, from either stored shape (`{"inline": {provider,
    config}}` or the flat `{provider, config}`); ``{}`` when it declares none."""
    workflow = getattr(trigger, "workflow", None)
    if not isinstance(workflow, dict):
        return {}
    inline = workflow.get("inline") if isinstance(workflow.get("inline"), dict) else None
    config = (inline or workflow).get("config")
    return dict(config) if isinstance(config, dict) else {}


def requested_capabilities(trigger: Any) -> dict[str, list[str]]:
    """What a trigger's own declared action asks for, in the fence's vocabulary.

    🔴 THE GAP THIS FILLS. `FireContext.requested` defaulted to `{}` and **nothing in
    production ever populated it** — the only real construction (`service.tick`) omitted it, so
    `if ctx.requested:` was always false and the frozen-capability fence had never run on a real
    fire. It passed its own unit tests, which supplied `requested` by hand. Same shape as the old
    `existing_claim` gap: a gate whose input nobody supplied.

    Reads both action shapes, because a real store holds both — `workflow.inline` for a migrated
    cron, a flat `{provider, config}` for one the chat tools created.

    A workflow REF (`workflow.ref`) requests nothing here: the def's own nodes are fenced by the
    workflow engine's capability layer, and naming the ref as a "provider" would refuse every
    workflow-backed trigger against a set that never lists def names.
    """
    workflow = getattr(trigger, "workflow", None)
    if not isinstance(workflow, dict):
        return {}
    inline = workflow.get("inline") if isinstance(workflow.get("inline"), dict) else None
    provider = str((inline or workflow).get("provider") or "").strip()
    if not provider:
        return {}
    return {"providers": [provider]}


def capabilities_for_action(trigger: Any) -> dict[str, Any]:
    """The `capabilities` block a trigger's declared ACTION implies.

    Distinct from `freeze_capabilities` below, which NORMALIZES a block the author supplied. This
    one DERIVES the block from the action the trigger already carries, so a writer can freeze the
    right set without asking the user to restate a choice they made by picking the action.

    Every non-manual trigger carries a `capabilities` block frozen at save time, and
    write-capable actions require explicit opt-in. The opt-in is the owner's yes to the action,
    asked where they author it (the create dialog, the editor, `cron add --yes`) or given by the
    code that makes one of PersonalClaw's own triggers — so this records that yes rather than asking
    twice. Authoring alone is not it: a trigger the chat makes waits for the owner's Allow
    (`triggers.grants`). `provider_is_read_only` is what decides whether a row needs one.

    🔴 WHY THIS EXISTS. Measured: NO writer set `capabilities` — not `tools.create`, not the
    app-cron reconciler, not the digest reconciler, not the CLI, not the API. And every one of them
    creates a WRITE-CAPABLE action (`invoke-agent`, `run-prompt`, `notification-digest`), so wiring
    the fence without freezing at save would refuse 100% of real automations on their next fire.

    Read-only actions get an EMPTY block deliberately: the fence permits them without one, and
    writing `{"providers": ["notify"]}` would imply an opt-in the user never had to make — which
    matters the day someone edits that trigger's action to something write-capable and the stale
    block silently grants it.

    Existing rows are never rewritten here. A trigger whose block does not cover its action is
    refused on every run, which is visible and fixable — the direction that cannot silently lose
    the property. `automation doctor` reports it, the Triggers page offers Allow, and the
    owner's yes is the only thing that grants it (`triggers.grants`).
    """
    requested = requested_capabilities(trigger)
    config = action_config(trigger)
    providers = [p for p in requested.get("providers", []) if not provider_is_read_only(p, config)]
    return {"providers": providers} if providers else {}


def capability_allows(
    capabilities: dict[str, Any] | None,
    *,
    key: str,
    value: str,
) -> CapabilityDecision:
    """Whether a frozen capability set permits one concrete action. Fails CLOSED.

    Every refusal path is deliberate:

    * An **unknown key** is denied. `gate_failure_mode` already establishes that an unclassified
    gate
      refuses; a capability nobody declared must not be a hole.
    * An **absent or empty** set is denied (`EMPTY_MEANS`). The permissive reading would make the
      fence decorative for every trigger that skipped the field.
    * A **non-list** value is denied rather than coerced. `{"tools": "bash"}` looks like it grants
      bash; coercing a string to a one-element list would make a malformed allowlist WORK, and a
      security control that tolerates the wrong shape teaches people to write it.
    """
    if key not in CAPABILITY_KEYS:
        return CapabilityDecision(
            allowed=False,
            key=key,
            reason=f"unknown capability {key!r}; expected one of "
            f"{', '.join(sorted(CAPABILITY_KEYS))}",
        )
    if not capabilities:
        return CapabilityDecision(
            allowed=False,
            key=key,
            reason="this trigger declares no capabilities, so nothing is permitted",
        )
    entries = capabilities.get(key)
    if entries is None:
        return CapabilityDecision(
            allowed=False,
            key=key,
            # Not `key[:-1]` — naive singularization rendered "network" as "no networ is permitted".
            reason=f"this trigger declares no {key}, so none is permitted",
        )
    if not isinstance(entries, (list, tuple, set, frozenset)):
        return CapabilityDecision(
            allowed=False,
            key=key,
            reason=f"the {key} allowlist must be a list; a {type(entries).__name__} is refused "
            "rather than coerced, so a malformed fence cannot silently grant access",
        )
    # 🔴 PATHS ARE NOT STRINGS. `_matches_entry` is prefix matching built for tool names, and
    # it let a traversal through: with `paths: ["/Users/me/notes/*"]` it ALLOWED
    # `/Users/me/notes/../../.ssh/id_rsa`. Measured against this very function before PathGuard
    # existed. So the `paths` key is decided by canonicalized containment instead — same fail-closed
    # discipline, correct comparison.
    if key == "paths":
        from personalclaw.triggers.pathguard import path_allowed

        allowed, reason = path_allowed(entries, value)
        return CapabilityDecision(allowed=allowed, key=key, reason=reason)

    for entry in entries:
        if isinstance(entry, str) and _matches_entry(value, entry):
            return CapabilityDecision(allowed=True, key=key)
    return CapabilityDecision(
        allowed=False,
        key=key,
        reason=f"{value!r} is not in this trigger's frozen {key} allowlist",
    )


def ungranted_providers(trigger: Any) -> list[str]:
    """The write-capable providers `trigger`'s action runs that its frozen block does not permit.

    The question the fence asks at fire time, asked before one: `[]` for a read-only action or a
    row that holds its grant, otherwise what the owner would have to grant before it may run. Both
    dispatches and every switch-on and edit ask it (`triggers.grants`).
    """
    frozen = getattr(trigger, "capabilities", None)
    block = frozen if isinstance(frozen, dict) else {}
    return [
        provider
        for provider in capabilities_for_action(trigger).get("providers", [])
        if not capability_allows(block, key="providers", value=provider).allowed
    ]


def grant_action(trigger: Any) -> list[str]:
    """Freeze the providers `trigger`'s action runs into its block. Returns what was granted.

    This is the owner saying yes, so only a caller holding that yes may call it, through
    `triggers.grants.give`: the Triggers page's switch, the create dialog and the editor, after
    their consent question, and the CLI after `--yes`. Nothing that runs unattended grants — a
    boot, a chat tool or an import writing this block would be authority nobody gave. A
    `providers` value that is not a list is replaced rather than extended, because the fence
    refuses a non-list and extending one would grant nothing.
    """
    granted = ungranted_providers(trigger)
    if not granted:
        return []
    current = getattr(trigger, "capabilities", None)
    block = dict(current) if isinstance(current, dict) else {}
    held = block.get("providers")
    providers = [p for p in held if isinstance(p, str)] if isinstance(held, (list, tuple)) else []
    block["providers"] = [*providers, *(p for p in granted if p not in providers)]
    trigger.capabilities = block
    return granted


def freeze_capabilities(capabilities: dict[str, Any] | None) -> dict[str, list[str]]:
    """Normalize a capability block for persistence AT SAVE.

    Frozen at save, not resolved at fire time, because a trigger authored when a provider was
    harmless must not inherit whatever that provider can do a year later. Normalizing here means the
    stored shape is always the shape `capability_allows` expects, so a hand-edited store cannot
    produce a set that reads as permissive.

    Unknown keys are DROPPED rather than kept: a retained `tool` typo sitting beside a real `tools`
    entry is a fence a reader will misread.
    """
    out: dict[str, list[str]] = {}
    for key in sorted(CAPABILITY_KEYS):
        raw = (capabilities or {}).get(key)
        if isinstance(raw, str):
            # A bare string is preserved as a single entry HERE (a save-time convenience) even
            # though `capability_allows` refuses one at check time — normalizing on the way in is
            # how the store ends up with the right shape, rather than tolerating the wrong one on
            # the way out.
            out[key] = [raw]
        elif isinstance(raw, (list, tuple, set, frozenset)):
            out[key] = sorted({str(v) for v in raw if isinstance(v, str) and v})
    return out


#: Payload keys that carry text from OUTSIDE the trust boundary, per kind. The injection
#: screen reads
#: these; everything else in a payload is substrate-set structure (ids, counts, timestamps).
#:
#: 🔴 An ALLOWLIST of untrusted keys rather than "screen everything", and the direction is chosen the
#: opposite way from the env denylist for a reason: screening a trigger id or a URL against the
#: OWASP override patterns produces false BLOCKS, and a blocked fire is never auto-retried
#: (`blocked_injection` is terminal by design). A false positive here permanently kills a working
#: automation, so the screen must see exactly the fields that carry prose.
UNTRUSTED_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "web_watch": ("new_items",),
    "file": ("changed", "paths", "added", "modified", "removed"),
    "event": ("value",),
    "webhook": ("body", "text", "payload"),
    "inbox": ("body", "text"),
    # A lifecycle hook's event (`hooks.hand_on`): the prompt a turn answers and a tool's result.
    # Its context line is the hook's own to screen; the call it names rides as it was made.
    "lifecycle": ("prompt", "tool_response"),
}

#: Keys screened for EVERY kind — a payload shape that carries prose regardless of source.
_ALWAYS_UNTRUSTED: tuple[str, ...] = ("payload_text", "content", "message", "summary")


def prose_keys(kind: str = "") -> frozenset[str]:
    """The payload keys whose values carry words from outside for a fire of *kind*: its own
    (:data:`UNTRUSTED_PAYLOAD_KEYS`) and the ones every kind's may (:data:`_ALWAYS_UNTRUSTED`).

    The one definition a fire's payload is read by on its way to its action
    (``outside_text.admit_payload``), which screens and fences the words under these keys in one
    walk, however deeply they nest: a `web_watch` fire's text arrives as `new_items: [...]`, and
    a screen reading one set of keys beside a fence protecting another is how a payload slips
    between them. Ids, URLs and counts under other keys go on as they are, since screening them
    produces the false blocks this allowlist exists to avoid.
    """
    return frozenset(UNTRUSTED_PAYLOAD_KEYS.get(kind, ())) | frozenset(_ALWAYS_UNTRUSTED)


def unfenced_actions(
    capabilities: dict[str, Any] | None, *, requested: dict[str, list[str]]
) -> list[tuple[str, str, str]]:
    """Every requested action the frozen set refuses. Returns `(key, value, reason)`.

    The adversarial-verification helper: instead of asserting "the fence
    works", a test enumerates what a payload TRIED to do and proves the refused list covers all
    outside the allowlist. Returning the reasons too means a failing assertion says which fence let
    it through.
    """
    out: list[tuple[str, str, str]] = []
    for key, values in (requested or {}).items():
        for value in values:
            decision = capability_allows(capabilities, key=key, value=value)
            if not decision.allowed:
                out.append((key, value, decision.reason))
    return out


# ── typed budget / triage ledger rows (the "zero silent drops") ──

#: The three refusal paths a fire can take before it ever reaches a model, each with the `Outcome`
#: it must record. Named as data so `screen_to_outcome` cannot drift from the vocabulary, and so a
#: test can assert the mapping is total rather than trusting a chain of `if`s.
_SCREEN_OUTCOMES: dict[str, str] = {
    Verdict.BLOCKED.value: "blocked_injection",
    Verdict.SUSPICIOUS.value: "ran",
    Verdict.CLEAN.value: "ran",
}


def screen_to_outcome(verdict: str) -> str:
    """The fire `Outcome` a screen verdict produces.

    `SUSPICIOUS` maps to `ran`, not to a refusal: the payload is FENCED and the run proceeds, so
    recording a suppression would be a lie in the ledger — and the ledger's honesty rule cuts both
    ways. An
    unrecognized verdict maps to `blocked_injection`, the fail-closed direction: a verdict nobody
    classified must not become a run.
    """
    return _SCREEN_OUTCOMES.get(verdict, "blocked_injection")


def screen_ledger_row(
    *,
    trigger_id: str,
    result: ScreenResult,
    source: str = "",
) -> dict[str, Any] | None:
    """The ledger row for a screened payload, or None when nothing needs recording.

    Returns None only for a CLEAN verdict — a clean screen is the absence of an event, and writing a
    row per clean fire would bury the real ones. Everything else records, including `SUSPICIOUS`,
    because "we fenced this and ran it anyway" is exactly the decision a user needs to be able to
    audit after the fact.

    The row NAMES the matched pattern: `blocked_injection` with no detail is unauditable, and
    a user who believes the screen is wrong has nothing to appeal against.
    """
    if result.clean:
        return None
    row: dict[str, Any] = {
        "trigger_id": trigger_id,
        "outcome": screen_to_outcome(result.verdict),
        "reason": (
            f"the injection screen matched the {result.matched_group} group"
            + (" (hidden by encoding)" if result.evaded else "")
        ),
        "screen_verdict": result.verdict,
        "screen_group": result.matched_group,
        "screen_pattern": result.matched_pattern,
        "screen_groups": list(result.groups),
        "screen_evaded": result.evaded,
        "retryable": False if result.blocked else True,
    }
    if source:
        row["source"] = source
    return row


def capability_ledger_row(
    *, trigger_id: str, decision: CapabilityDecision, value: str
) -> dict[str, Any] | None:
    """The ledger row for a capability refusal, or None when the action was allowed.

    A refused action MUST leave a row. This is the difference between a fence and a silent failure:
    an unattended run whose action was dropped with no trace looks identical to a run with nothing
    to
    do, and the user's automation quietly stops working.
    """
    if decision.allowed:
        return None
    return {
        "trigger_id": trigger_id,
        "outcome": "refused",
        "reason": decision.reason,
        "capability_key": decision.key,
        "capability_value": value,
        # Never auto-retried: retrying an action the fence refused is either pointless (the
        # allowlist has not changed) or an attack loop probing for a gap.
        "retryable": False,
    }


def budget_ledger_row(
    *,
    trigger_id: str,
    spent: float,
    ceiling: float,
    window: str = "day",
    check_failed: bool = False,
) -> dict[str, Any]:
    """The row for a budget decision. ALWAYS returns a row — there is no silent budget skip.

    `check_failed` is the fail-OPEN case, and it still writes a row saying
    the check could not complete. That combination is the whole point: a budget probe that hangs
    must not stop every automation on the machine (so the fire proceeds), but a fire that ran with
    no verified budget check is a fact the user must find later. Failing open silently would
    make an unbounded spend indistinguishable from a normal day.
    """
    if check_failed:
        return {
            "trigger_id": trigger_id,
            "outcome": "ran",
            "reason": "the budget check could not complete; the fire proceeded (budget gates fail "
            "open so a broken probe cannot wedge every automation) — spend was NOT verified",
            "budget_window": window,
            "budget_verified": False,
            "retryable": True,
        }
    breached = ceiling > 0 and spent >= ceiling
    return {
        "trigger_id": trigger_id,
        "outcome": "skipped_budget" if breached else "ran",
        "reason": (
            f"the {window} budget ceiling of {ceiling:g} was already reached (spent {spent:g})"
            if breached
            else ""
        ),
        "budget_window": window,
        "budget_spent": spent,
        "budget_ceiling": ceiling,
        "budget_verified": True,
        # Retryable: the next window resets, so this is a wait, not a permanent refusal.
        "retryable": True,
    }

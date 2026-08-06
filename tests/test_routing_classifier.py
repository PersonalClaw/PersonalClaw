"""MODEL-ROUTING-TELEMETRY §2 / MRT-1a — the pure query classifier.

Routing can't spend an LLM call to decide, so classify_query is a pure heuristic mapping a
request into a small fixed vocabulary. These lock the class boundaries, the precedence between
competing signals, and the cheapest-safe fallback — and that it is genuinely pure/deterministic.
"""

from __future__ import annotations

from personalclaw.routing import classifier as c


class TestVocabulary:
    def test_vocab_is_the_five_class_set(self):
        assert set(c.QUERY_CLASSES) == {
            "short_chat",
            "code",
            "summarize",
            "extract_structured",
            "long_reasoning",
        }
        # Stable order (buckets key on it) and no duplicates.
        assert len(c.QUERY_CLASSES) == 5

    def test_version_is_an_int_constant(self):
        assert isinstance(c.CLASSIFIER_VERSION, int) and c.CLASSIFIER_VERSION >= 1

    def test_every_output_is_in_the_vocabulary(self):
        samples = [
            "",
            "hi",
            "```python\nx=1\n```",
            "summarize this",
            "return json",
            "x " * 3000,
            "reason through the trade-offs",
            "def f(): return 1",
        ]
        for s in samples:
            assert c.classify_query(s) in c.QUERY_CLASSES


class TestStructured:
    def test_explicit_flag_wins_over_everything(self):
        # Even a long reasoning-flavored prompt is extract_structured when the schema flag is set.
        long_reasoning = "analyze the trade-offs step by step. " * 100
        assert (
            c.classify_query(long_reasoning, "reasoning", wants_structured_output=True)
            == "extract_structured"
        )

    def test_json_ask_in_text(self):
        assert c.classify_query("give me the answer as JSON") == "extract_structured"
        assert c.classify_query("return a list of the fields") == "extract_structured"

    def test_structured_outranks_code(self):
        # A fenced snippet but the ask is for JSON out → structured wins (precedence 1 > 2).
        assert c.classify_query("```\n{}\n```\nreturn valid json schema") == "extract_structured"


class TestCode:
    def test_code_fence(self):
        assert c.classify_query("here:\n```python\ndef f():\n    return 1\n```") == "code"

    def test_code_use_case_prior(self):
        # Even a short plain sentence routes to code under the code_tools binding.
        assert c.classify_query("make it faster", use_case="code_tools") == "code"

    def test_code_keyword_signal(self):
        assert c.classify_query("class Foo: def bar(self): return SELECT * FROM t") == "code"

    def test_prose_about_a_function_is_not_code(self):
        # "the function of the heart" has no real code signal — must NOT be miscalled code.
        assert c.classify_query("what is the function of the mitochondria in a cell?") != "code"


class TestSummarize:
    def test_summarize_ask(self):
        assert c.classify_query("summarize this article for me") == "summarize"
        assert c.classify_query("tl;dr?") == "summarize"

    def test_summarize_outranks_length(self):
        # A long doc with an explicit condense ask → summarize, not long_reasoning.
        doc = "lorem ipsum " * 500
        assert c.classify_query(f"give me the key points: {doc}") == "summarize"


class TestLongReasoning:
    def test_long_text(self):
        assert c.classify_query("x " * 1500) == "long_reasoning"  # > _LONG_MIN chars

    def test_reasoning_use_case(self):
        assert c.classify_query("which is better?", use_case="reasoning") == "long_reasoning"

    def test_reasoning_marker_words(self):
        assert (
            c.classify_query("walk me through the pros and cons of each approach")
            == "long_reasoning"
        )


class TestShortChatAndFallback:
    def test_short_greeting(self):
        assert c.classify_query("hey, how are you?") == "short_chat"

    def test_empty_is_short_chat(self):
        assert c.classify_query("") == "short_chat"
        assert c.classify_query("   \n  ") == "short_chat"

    def test_none_text_is_safe(self):
        # A None slipping in must not crash — it's the cheapest-safe default.
        assert c.classify_query(None) == "short_chat"  # type: ignore[arg-type]

    def test_mid_length_unmarked_is_short_chat(self):
        mid = "tell me about your day " * 20  # between short and long, no signal
        assert c.classify_query(mid) == "short_chat"


class TestPurity:
    def test_deterministic(self):
        prompt = "summarize the reasoning in ```code``` as json"
        assert c.classify_query(prompt, "reasoning") == c.classify_query(prompt, "reasoning")

    def test_use_case_default_is_empty(self):
        # Callable with just text (use_case optional) — the guard-audit path may lack a use_case.
        assert c.classify_query("hi") == "short_chat"

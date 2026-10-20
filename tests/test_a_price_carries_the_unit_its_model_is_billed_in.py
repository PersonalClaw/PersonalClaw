"""A model is priced in the unit it is billed in, and its calls are counted in that unit.

An image model is billed per image, by its size and quality; a video model per second of video;
speech-to-text per minute of audio; text-to-speech per character. Model prices could only hold a
price per 1M tokens, so Amazon Nova Canvas could not be given one ("Set a price" asked for dollars
per 1M tokens), and no image, video, transcription or speech call was counted anywhere: not in
Usage, and not by the daily dollar cap, so an automation could spend on them without limit.

Now a price row names its unit (``token`` when it names none), the shipped table carries the media
models first-party providers serve, "Set a price" takes any of the units, and every image, video,
transcription and speech call goes through one metering seam (``guardrails.media_call``): an
unattended one is weighed against the dollar caps before it runs and charged what it cost after, a
person's own is not capped (as a person's chat turn is not), and both write a Usage row that names
the unit and how many of it the call was billed for.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
from personalclaw import spend_day
from personalclaw.guardrails import budgets as budgets_mod
from personalclaw.guardrails.budgets import get_meter
from personalclaw.guardrails.failure import BudgetExceededError
from personalclaw.guardrails.media_call import MediaCall, metered_media_call
from personalclaw.image_gen.provider import ImageGenModel, ImageGenProvider, ImageResult
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.rates import rate_entry, rate_for, rates_view, set_rate, unit_rate_for

_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nGENERATED").decode()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    rates_mod._overlay_cache = None
    budgets_mod.reset_meter()
    yield tmp_path
    rates_mod._overlay_cache = None
    budgets_mod.reset_meter()


def _cap(home, dollars: float) -> None:
    (home / "config.json").write_text(
        json.dumps({"guardrails": {"budgets": {"max_dollars_per_day": dollars}}}),
        encoding="utf-8",
    )


def _rows(home) -> list[dict]:
    path = home / "usage" / "turns.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ── The shipped rows ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("size", "quality", "dollars"),
    [
        ("", "", 0.04),
        ("1024x1024", "", 0.04),
        ("1280x720", "", 0.04),
        ("1024x1024", "premium", 0.06),
        ("2048x2048", "", 0.06),
        ("2048x2048", "premium", 0.08),
        ("4096x4096", "", None),
    ],
)
def test_an_image_model_is_priced_per_image_by_size_and_quality(size, quality, dollars, home):
    rate = unit_rate_for("bedrock", "amazon.nova-canvas-v1:0", "image", home=home)

    assert rate is not None and rate.source == "builtin" and rate.vendor == "Amazon"
    cost = rate.cost(1, size=size, quality=quality)
    assert cost == (pytest.approx(dollars) if dollars is not None else None)


def test_an_image_model_has_no_price_per_token(home):
    assert rate_for("bedrock", "amazon.nova-canvas-v1:0", home=home) is None


@pytest.mark.parametrize(
    ("provider", "model", "unit", "quantity", "dollars"),
    [
        ("bedrock", "amazon.nova-reel-v1:1", "second", 6, 0.48),
        ("bedrock", "amazon-transcribe", "minute", 10, 0.24),
        ("openai", "whisper-1", "minute", 10, 0.06),
        ("openai", "tts-1", "character", 2_000, 0.03),
        ("openai", "tts-1-hd", "character", 2_000, 0.06),
    ],
)
def test_media_models_are_priced_in_their_unit(provider, model, unit, quantity, dollars, home):
    rate = unit_rate_for(provider, model, unit, home=home)

    assert rate is not None, f"{provider}:{model} has no price per {unit}"
    assert rate.cost(quantity) == pytest.approx(dollars)


def test_a_price_in_one_unit_does_not_price_a_call_billed_in_another(home):
    assert (
        unit_rate_for("bedrock", "global.anthropic.claude-sonnet-5-5", "image", home=home) is None
    )
    assert unit_rate_for("bedrock", "amazon.nova-canvas-v1:0", "second", home=home) is None


# ── Set a price ──────────────────────────────────────────────────────────────────────────────


def test_a_price_per_image_is_set_and_counted_at(home):
    key, row = rate_entry({"key": "studio:flux-pro", "unit": "image", "per_image": 0.05})
    set_rate(key, row, home=home)

    rate = unit_rate_for("studio", "flux-pro", "image", home=home)
    assert rate is not None and rate.source == "overlay" and rate.cost(2) == pytest.approx(0.1)
    stored = json.loads((home / "config.json").read_text(encoding="utf-8"))
    row = stored["model_prices"]["overrides"]["studio:flux-pro"]
    assert {k: v for k, v in row.items() if k != "recorded"} == {"unit": "image", "per_image": 0.05}


def test_prices_by_size_and_quality_are_set(home):
    key, row = rate_entry(
        {
            "key": "studio:flux-pro",
            "unit": "image",
            "tiers": [
                {"size": "1024x1024", "quality": "standard", "per_image": 0.04},
                {"size": "2048x2048", "quality": "standard", "per_image": 0.07},
            ],
            "default_size": "1024x1024",
            "default_quality": "standard",
        }
    )
    set_rate(key, row, home=home)

    rate = unit_rate_for("studio", "flux-pro", "image", home=home)
    assert rate is not None
    assert rate.cost(1) == pytest.approx(0.04)
    assert rate.cost(1, size="1536x1536") == pytest.approx(0.07)


@pytest.mark.parametrize(
    ("unit", "field", "value"),
    [("second", "per_second", 0.5), ("minute", "per_minute", 0.01), ("character", "per_mchar", 16)],
)
def test_a_price_per_second_minute_or_character_is_set(unit, field, value, home):
    key, row = rate_entry({"key": "studio:m", "unit": unit, field: value})
    set_rate(key, row, home=home)

    rate = unit_rate_for("studio", "m", unit, home=home)
    assert rate is not None and rate.source == "overlay"


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ({"key": "a:b", "unit": "image"}, "Give the price per image"),
        ({"key": "a:b", "unit": "frame", "per_frame": 1}, "A price is per 1M tokens, image"),
        ({"key": "a:b", "unit": "image", "per_image": 0.04, "in_per_mtok": 3}, "no field"),
        (
            {"key": "a:b", "unit": "image", "tiers": [{"size": "big", "per_image": 0.04}]},
            "width x height",
        ),
        ({"key": "a:b", "unit": "minute", "per_minute": -1}, "zero or more"),
        (
            {"key": "a:b", "unit": "image", "per_image": 0.04, "tiers": [{"per_image": 0.04}]},
            "either one price per image or prices by size and quality",
        ),
    ],
)
def test_a_price_that_cannot_be_counted_is_refused_saying_why(body, said):
    with pytest.raises(ValueError, match=said):
        rate_entry(body)


def test_model_prices_show_each_models_unit(home):
    set_rate("studio:flux-pro", {"unit": "image", "per_image": 0.05}, home=home)

    view = rates_view([("bedrock", "amazon.nova-canvas-v1:0"), ("studio", "flux-pro")], home=home)

    canvas, flux = view["models"]
    assert (canvas["unit"], canvas["priced"], canvas["vendor"]) == ("image", True, "Amazon")
    assert {"size": "2048x2048", "quality": "premium", "per_image": 0.08} in canvas["tiers"]
    assert (flux["unit"], flux["per_unit"], flux["source"]) == ("image", 0.05, "overlay")
    assert view["rates"] == [
        {
            "key": "studio:flux-pro",
            "unit": "image",
            "per_unit": 0.05,
            "tiers": [],
            "default_size": "",
            "default_quality": "",
            "recorded": spend_day.today(),
        }
    ]


# ── Metering ─────────────────────────────────────────────────────────────────────────────────


def _image(provider: str, model: str, *, size: str = "") -> MediaCall:
    return MediaCall(provider=provider, model=model, unit="image", quantity=1, size=size)


async def _made(sent: list[str]) -> list[str]:
    sent.append("made")
    return ["picture"]


def test_an_unattended_image_is_weighed_and_charged_at_its_price(home):
    _cap(home, 4.0)
    sent: list[str] = []

    out = asyncio.run(
        metered_media_call(
            _image("bedrock", "amazon.nova-canvas-v1:0"),
            lambda: _made(sent),
            session_key="cron:morning-brief",
            billed=len,
        )
    )

    assert out == ["picture"] and sent == ["made"]
    assert get_meter().day_totals().dollars == pytest.approx(0.04)
    (row,) = _rows(home)
    assert (row["provider"], row["model"], row["unit"], row["quantity"]) == (
        "bedrock",
        "amazon.nova-canvas-v1:0",
        "image",
        1,
    )
    assert row["cost_usd"] == pytest.approx(0.04) and row["priced"] is True
    assert row["source"] == "background"


def test_an_unattended_image_nothing_prices_is_refused_before_it_is_made(home):
    _cap(home, 4.0)
    sent: list[str] = []

    with pytest.raises(BudgetExceededError) as refused:
        asyncio.run(
            metered_media_call(
                _image("studio", "flux-pro"), lambda: _made(sent), session_key="cron:x"
            )
        )

    assert sent == []
    assert refused.value.sentence().startswith(
        "studio:flux-pro has no price, so the daily dollar budget cannot count what a call to it "
        "would spend: set its price in Settings → Usage → Model prices"
    )


def test_an_image_you_ask_for_is_not_capped_but_is_counted(home):
    _cap(home, 4.0)
    sent: list[str] = []

    asyncio.run(
        metered_media_call(
            _image("studio", "flux-pro"), lambda: _made(sent), session_key="dashboard:chat-1"
        )
    )

    assert sent == ["made"]
    assert get_meter().day_totals().dollars == 0.0
    (row,) = _rows(home)
    assert (row["unit"], row["quantity"], row["priced"], row["source"]) == (
        "image",
        1,
        False,
        "chat",
    )


def test_a_run_too_poor_for_one_more_image_is_refused(home):
    _cap(home, 0.05)
    get_meter().charge(0, 0.03)

    with pytest.raises(BudgetExceededError):
        asyncio.run(
            metered_media_call(
                _image("bedrock", "amazon.nova-canvas-v1:0", size="2048x2048"),
                lambda: _made([]),
                session_key="cron:x",
            )
        )


def test_a_transcription_whose_length_cannot_be_read_is_refused_under_a_dollar_cap(home):
    _cap(home, 4.0)
    call = MediaCall(provider="bedrock", model="amazon-transcribe", unit="minute", quantity=None)

    with pytest.raises(BudgetExceededError) as refused:
        asyncio.run(metered_media_call(call, lambda: _made([]), session_key="cron:x"))

    assert "how long" in refused.value.sentence()


def test_a_transcription_is_charged_for_the_minutes_it_ran(home):
    _cap(home, 4.0)
    call = MediaCall(provider="bedrock", model="amazon-transcribe", unit="minute", quantity=10)

    asyncio.run(metered_media_call(call, lambda: _made([]), session_key="cron:x"))

    assert get_meter().day_totals().dollars == pytest.approx(0.24)
    (row,) = _rows(home)
    assert (row["unit"], row["quantity"]) == ("minute", 10)


def test_a_call_that_fails_is_charged_nothing(home):
    _cap(home, 4.0)

    async def _fails():
        raise RuntimeError("the runtime went away")

    with pytest.raises(RuntimeError):
        asyncio.run(
            metered_media_call(
                _image("bedrock", "amazon.nova-canvas-v1:0"), _fails, session_key="cron:x"
            )
        )

    assert get_meter().day_totals().dollars == 0.0
    assert get_meter().held() == (0, 0.0)
    assert _rows(home) == []


# ── The seams ────────────────────────────────────────────────────────────────────────────────


class _Studio(ImageGenProvider):
    @property
    def name(self) -> str:
        return "studio"

    @property
    def display_name(self) -> str:
        return "Studio"

    async def is_available(self) -> bool:
        return True

    async def list_models(self):
        return [ImageGenModel(name="flux-pro")]

    async def generate(self, prompt, *, model="", size="", n=1, **opts):
        return [ImageResult(b64=_PNG_B64, mime="image/png")]

    async def edit(self, prompt, *, source_image, mask="", model="", size="", n=1, **opts):
        return [ImageResult(b64=_PNG_B64, mime="image/png")]


@pytest.fixture
def studio(home, monkeypatch):
    from personalclaw.artifacts import native
    from personalclaw.artifacts import registry as art_reg
    from personalclaw.image_gen import registry as ig_reg

    store = native.NativeArtifactProvider(root=home / "artifacts")
    monkeypatch.setattr(art_reg, "get_provider", lambda name="native": store)
    monkeypatch.setattr(ig_reg, "active_image_gen", lambda: (_Studio(), "flux-pro"))
    return store


def test_the_image_tool_is_refused_in_an_automation_when_its_model_has_no_price(
    studio, home, monkeypatch
):
    from personalclaw.mcp_artifacts import _call_tool_inner

    _cap(home, 4.0)
    monkeypatch.setattr("personalclaw.mcp_artifacts._resolve_session_key", lambda: "cron:x")

    out = _call_tool_inner("image_generate", {"prompt": "a red bicycle"})

    assert out.startswith("Error:") and "studio:flux-pro has no price" in out
    assert studio.list(kind="image") == []


def test_the_image_tool_is_counted_in_usage_at_the_price_set_for_it(studio, home, monkeypatch):
    from personalclaw.mcp_artifacts import _call_tool_inner

    _cap(home, 4.0)
    set_rate("studio:flux-pro", {"unit": "image", "per_image": 0.05}, home=home)
    monkeypatch.setattr("personalclaw.mcp_artifacts._resolve_session_key", lambda: "cron:x")

    out = _call_tool_inner("image_generate", {"prompt": "a red bicycle"})

    assert "Generated image" in out
    assert get_meter().day_totals().dollars == pytest.approx(0.05)
    (row,) = _rows(home)
    assert (row["provider"], row["model"], row["unit"], row["quantity"]) == (
        "studio",
        "flux-pro",
        "image",
        1,
    )


def test_speech_is_counted_by_the_characters_it_spoke(home, monkeypatch):
    from personalclaw.guardrails.budgets import reset_current_run_key, set_current_run_key
    from personalclaw.tts.provider import TtsProvider
    from personalclaw.voice_reply import synthesize_speech

    class _Voice(TtsProvider):
        @property
        def name(self) -> str:
            return "openai"

        @property
        def display_name(self) -> str:
            return "Voice"

        async def is_available(self) -> bool:
            return True

        async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
            return str(home / "said.mp3")

    _cap(home, 4.0)
    token = set_current_run_key("cron:evening-digest")
    try:
        path = asyncio.run(synthesize_speech(_Voice(), "x" * 2_000, voice="tts-1"))
    finally:
        reset_current_run_key(token)

    assert path == str(home / "said.mp3")
    assert get_meter().day_totals().dollars == pytest.approx(0.03)
    (row,) = _rows(home)
    assert (row["model"], row["unit"], row["quantity"]) == ("tts-1", "character", 2_000)


def test_a_transcription_is_counted_by_its_minutes(home, monkeypatch):
    from personalclaw import transcribe
    from personalclaw.stt.provider import SttProvider

    class _Ears(SttProvider):
        @property
        def name(self) -> str:
            return "openai"

        @property
        def display_name(self) -> str:
            return "Ears"

        async def is_available(self) -> bool:
            return True

        async def transcribe(self, audio_path, model="", language=""):
            return "hello there"

    audio = home / "note.wav"
    audio.write_bytes(b"RIFF")
    monkeypatch.setattr(transcribe, "_resolve", lambda path: (_Ears(), "whisper-1", ""))
    monkeypatch.setattr(transcribe, "audio_seconds", lambda path: 120.0)

    text = asyncio.run(transcribe.transcribe_audio(str(audio)))

    assert text == "hello there"
    (row,) = _rows(home)
    assert (row["model"], row["unit"], row["quantity"], row["cost_usd"]) == (
        "whisper-1",
        "minute",
        2.0,
        pytest.approx(0.012),
    )


def test_an_automations_speech_a_cap_refuses_is_not_spoken_and_says_why(home, caplog):
    """``synthesize_speech`` answers None for any failure, a cap's refusal included, and the
    refusal is logged in the words that say how to lift it."""
    from personalclaw.guardrails.budgets import reset_current_run_key, set_current_run_key
    from personalclaw.tts.provider import TtsProvider
    from personalclaw.voice_reply import synthesize_speech

    spoken: list[str] = []

    class _Voice(TtsProvider):
        @property
        def name(self) -> str:
            return "studio-voice"

        @property
        def display_name(self) -> str:
            return "Voice"

        async def is_available(self) -> bool:
            return True

        async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
            spoken.append(text)
            return str(home / "said.mp3")

    _cap(home, 4.0)
    token = set_current_run_key("cron:evening-digest")
    try:
        path = asyncio.run(synthesize_speech(_Voice(), "Good evening.", voice="narrator-2"))
    finally:
        reset_current_run_key(token)

    assert path is None and spoken == []
    assert "studio-voice:narrator-2 has no price" in caplog.text


def test_an_import_whose_transcription_a_cap_refuses_says_why_as_speech_to_text_does(
    home, monkeypatch
):
    from personalclaw import transcribe
    from personalclaw.stt.provider import SttError, SttProvider

    heard: list[str] = []

    class _Ears(SttProvider):
        @property
        def name(self) -> str:
            return "studio-ears"

        @property
        def display_name(self) -> str:
            return "Ears"

        async def is_available(self) -> bool:
            return True

        async def transcribe(self, audio_path, model="", language=""):
            heard.append(audio_path)
            return "hello"

    audio = home / "talk.wav"
    audio.write_bytes(b"RIFF")
    _cap(home, 4.0)
    monkeypatch.setattr(transcribe, "_resolve", lambda path: (_Ears(), "ears-1", ""))
    monkeypatch.setattr(transcribe, "audio_seconds", lambda path: 90.0)

    with pytest.raises(SttError, match="studio-ears:ears-1 has no price"):
        asyncio.run(transcribe.transcribe_audio_detailed(str(audio), unattended=True))
    assert heard == []


def test_an_image_at_a_size_no_price_covers_is_refused_saying_so(home):
    _cap(home, 4.0)

    with pytest.raises(BudgetExceededError) as refused:
        asyncio.run(
            metered_media_call(
                _image("bedrock", "amazon.nova-canvas-v1:0", size="4096x4096"),
                lambda: _made([]),
                session_key="cron:x",
            )
        )

    assert refused.value.sentence().startswith(
        "bedrock:amazon.nova-canvas-v1:0 has no price for a 4096x4096 standard image, so the daily "
        "dollar budget cannot count what a call to it would spend"
    )


def test_a_call_that_made_nothing_is_charged_nothing(home):
    _cap(home, 4.0)

    async def _nothing():
        return []

    out = asyncio.run(
        metered_media_call(
            _image("bedrock", "amazon.nova-canvas-v1:0"),
            _nothing,
            session_key="cron:x",
            billed=len,
        )
    )

    assert out == []
    assert get_meter().day_totals().dollars == 0.0
    assert get_meter().held() == (0, 0.0)
    assert _rows(home) == []

"""A generated image or video is saved whole, or refused saying why: never saved cut off.

🔴 Before: the image and video tools fetched the URL a provider answered with under CONNECTOR,
whose 10 MB cap cut a longer clip short, and saved the cut file as though it were the video. The
fetch's ``truncated`` was never read. A download the egress guard refused raised out of the tool,
and one answered with an error read as "no resolvable bytes".

The fetch is answered the way ``net.client.fetch`` answers: at most the policy's ``max_bytes``,
flagged ``truncated`` when there was more.
"""

from __future__ import annotations

import pytest

from personalclaw.image_gen.provider import ImageGenModel, ImageGenProvider, ImageResult
from personalclaw.net.client import EgressBlocked, FetchResponse
from personalclaw.net.guard import GuardDecision
from personalclaw.video_gen.provider import VideoGenModel, VideoGenProvider, VideoResult

VIDEO_URL = "https://video-store.example/clip-test.mp4"
IMAGE_URL = "https://image-store.example/still-test.png"


class _Clip(VideoGenProvider):
    @property
    def name(self) -> str:
        return "clips"

    @property
    def display_name(self) -> str:
        return "Clips"

    async def is_available(self) -> bool:
        return True

    async def list_models(self):
        return [VideoGenModel(name="clip-1")]

    async def generate(self, prompt, **_kw):
        return [VideoResult(url=VIDEO_URL, mime="video/mp4")]


class _Still(ImageGenProvider):
    @property
    def name(self) -> str:
        return "stills"

    @property
    def display_name(self) -> str:
        return "Stills"

    async def is_available(self) -> bool:
        return True

    async def list_models(self):
        return [ImageGenModel(name="still-1")]

    async def generate(self, prompt, **_kw):
        return [ImageResult(url=IMAGE_URL, mime="image/png")]

    async def edit(self, prompt, *, source_image, **_kw):
        return []


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The artifact store under ``tmp_path``, with the fake image and video providers bound."""
    from personalclaw.artifacts import native
    from personalclaw.artifacts import registry as art_reg
    from personalclaw.image_gen import registry as ig_reg
    from personalclaw.video_gen import registry as vg_reg

    prov = native.NativeArtifactProvider(root=tmp_path)
    monkeypatch.setattr(art_reg, "get_provider", lambda name="native": prov)
    monkeypatch.setattr(vg_reg, "active_video_gen", lambda: (_Clip(), "clip-1"))
    monkeypatch.setattr(ig_reg, "active_image_gen", lambda: (_Still(), "still-1"))
    monkeypatch.setattr("personalclaw.mcp_artifacts._resolve_session_key", lambda: None)
    return prov


def _served(
    monkeypatch,
    body: bytes = b"",
    *,
    status: int = 200,
    ctype: str = "video/mp4",
    raises: BaseException | None = None,
) -> dict:
    """Answer the download as the real client does, and record the policy it was asked under."""
    seen: dict = {}

    async def _fetch(url, *, policy, **_kw):
        seen.update(url=url, policy=policy)
        if raises is not None:
            raise raises
        return FetchResponse(
            url=url,
            status=status,
            headers={"Content-Type": ctype},
            body=body[: policy.max_bytes],
            truncated=len(body) > policy.max_bytes,
        )

    monkeypatch.setattr("personalclaw.net.fetch", _fetch)
    return seen


def _video() -> str:
    from personalclaw.mcp_artifacts import _call_tool_inner

    return str(_call_tool_inner("video_generate", {"prompt": "a heron at dawn"}))


def _image() -> str:
    from personalclaw.mcp_artifacts import _call_tool_inner

    return str(_call_tool_inner("image_generate", {"prompt": "a heron at dawn"}))


def test_a_video_larger_than_the_old_cap_is_saved_whole(store, monkeypatch):
    clip = b"v" * 15_000_000
    _served(monkeypatch, clip)

    assert _video().startswith("Generated video")
    [art] = store.list(kind="video")
    data, mime = store.raw_bytes(art.slug)
    assert (len(data), mime) == (len(clip), "video/mp4"), "saved whole, not cut at 10 MB"


def test_a_video_over_the_cap_is_refused_and_nothing_is_saved(store, monkeypatch):
    from personalclaw.net import policy

    monkeypatch.setattr(policy, "MEDIA", policy.MEDIA.with_overrides(max_bytes=2_000_000))
    _served(monkeypatch, b"v" * 2_000_001)

    assert _video() == (
        "Error: The video is larger than 2 MB, the most PersonalClaw saves from a generation, so "
        "it was not saved. Generate a shorter or lower-resolution video."
    )
    assert store.list(kind="video") == []


def test_the_download_is_asked_under_the_media_policy(store, monkeypatch):
    """Not CONNECTOR, whose 10 MB and 20 s are a page's, and with the operator's own egress
    settings layered on, as every other fetch is."""
    from personalclaw.net.policy import MEDIA, METADATA_SERVICE_HOSTS

    seen = _served(monkeypatch, b"v" * 100)
    _video()

    asked = seen["policy"]
    assert (asked.name, asked.max_bytes, asked.timeout_s) == ("media", MEDIA.max_bytes, 180.0)
    assert set(METADATA_SERVICE_HOSTS) <= set(asked.deny_hosts)


def test_a_download_the_egress_settings_refuse_says_where_to_look(store, monkeypatch):
    refused = GuardDecision(
        allow=False,
        url=VIDEO_URL,
        reason="host is on the egress deny list",
        category="deny_list",
    )
    _served(monkeypatch, raises=EgressBlocked(refused))

    assert _video() == (
        "Error: The video was made, but PersonalClaw's network settings refused its download "
        "from video-store.example, so it was not saved. Check Allowed hosts and Denied hosts in "
        "Settings → Security → Network egress, then generate the video again. Details: host is "
        "on the egress deny list"
    )
    assert store.list(kind="video") == []


def test_a_host_that_cannot_be_found_says_to_check_the_connection(store, monkeypatch):
    unresolvable = GuardDecision(
        allow=False,
        url=VIDEO_URL,
        reason="could not resolve video-store.example",
        category="unresolvable",
    )
    _served(monkeypatch, raises=EgressBlocked(unresolvable))

    assert _video() == (
        "Error: The video was made, but its host video-store.example couldn't be found, so it "
        "was not saved. Check this computer's internet connection, then generate the video "
        "again. Details: could not resolve video-store.example"
    )


def test_an_address_answered_with_an_error_says_to_generate_again(store, monkeypatch):
    """It read "could not be saved (no resolvable bytes)", which says neither why nor what next."""
    _served(monkeypatch, b"<Error>AccessDenied</Error>", status=403, ctype="application/xml")

    assert _video() == (
        "Error: The video was made, but its download from video-store.example answered HTTP 403, "
        "so it was not saved. Generate the video again: the address a provider hands back can "
        "expire."
    )


def test_an_image_larger_than_the_old_cap_is_saved_whole(store, monkeypatch):
    still = b"i" * 12_000_000
    _served(monkeypatch, still, ctype="image/png")

    assert _image().startswith("Generated image")
    [art] = store.list(kind="image")
    data, mime = store.raw_bytes(art.slug)
    assert (len(data), mime) == (len(still), "image/png")


def test_a_regenerated_image_that_cannot_be_downloaded_says_why(store, monkeypatch):
    from personalclaw.mcp_artifacts import regenerate_image_at_slug

    _served(monkeypatch, b"", status=410, ctype="text/plain")

    assert regenerate_image_at_slug(store, "a-heron", "a heron at dawn") == (
        False,
        "The image was made, but its download from image-store.example answered HTTP 410, so it "
        "was not saved. Generate the image again: the address a provider hands back can expire.",
    )

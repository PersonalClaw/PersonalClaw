"""A video's frames are taken evenly across its whole length, and the item says which.

Frame extraction sampled one frame every 10 seconds from the start, capped at 8, so a 6-minute
walkthrough was seen only through its first 70 seconds, and nothing on the item said so. It now
takes up to 8 frames at even points across the probed length, the steps that read them take
theirs spread across the same span, and the item records where the frames came from.

The fixture is a generated video whose brightness encodes the second it was taken at, so each
extracted frame says for itself which part of the video it came from.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from personalclaw.knowledge.pipeline.nodes import media_nodes
from personalclaw.knowledge.pipeline.nodes.media_nodes import (
    FrameExtractNode,
    OcrNode,
    VideoClassifyNode,
    VisionNode,
)
from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput


def _ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found is None or shutil.which("ffprobe") is None:
        pytest.skip("needs ffmpeg and ffprobe to build and read the fixture video")
    return found


def _video(directory: Path, seconds: int) -> Path:
    """A *seconds*-long video at one frame a second, each frame's grey level its second."""
    path = directory / "walkthrough.mp4"
    subprocess.run(
        [
            _ffmpeg(),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=64x36:r=1:d={seconds},format=gray,"
            f"geq=lum='N*255/{max(1, seconds - 1)}'",
            "-c:v",
            "mpeg4",
            "-q:v",
            "2",
            "-g",
            "10",
            "-pix_fmt",
            "yuv420p",
            "-map_metadata",
            "-1",
            str(path),
        ],
        check=True,
    )
    return path


def _second_of(frame: str, seconds: int) -> float:
    """The second a fixture frame was taken at, read from its grey level."""
    from PIL import Image, ImageStat

    with Image.open(frame) as im:
        level = ImageStat.Stat(im.convert("L")).mean[0]
    return level * max(1, seconds - 1) / 255


def _ctx(video: Path, **params) -> NodeContext:
    return NodeContext(
        item_id="v-1",
        item_type="video",
        file_path=str(video),
        work_dir=str(video.parent),
        params=params or None,
    )


@pytest.mark.asyncio
async def test_a_six_minute_videos_frames_span_all_six_minutes(tmp_path):
    """🔴 Red before: the 8 frames were the video's first 70 seconds (0, 10, … 70)."""
    video = _video(tmp_path, 360)

    out = await FrameExtractNode().run({}, _ctx(video))

    assert out.success and len(out.artifacts) == 8
    taken = [_second_of(frame, 360) for frame in out.artifacts]
    assert taken == sorted(taken), "the frames are not in time order"
    assert taken[0] < 45 and taken[-1] > 315, taken
    assert all(35 <= b - a <= 55 for a, b in zip(taken, taken[1:])), taken
    assert out.metadata["frame_times"] == [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5]
    assert out.metadata["duration"] == pytest.approx(360, abs=0.5)
    assert out.metadata["frame_count"] == 8


@pytest.mark.asyncio
async def test_a_short_clip_gets_a_frame_for_each_second_it_has(tmp_path):
    """One slot a second. At one frame a second, the last slot's middle (4.5 s) falls inside the
    clip's last frame, which ffmpeg cannot seek to (it gives the first frame at or after a time),
    so that slot's frame is the one its slot starts with."""
    video = _video(tmp_path, 5)

    out = await FrameExtractNode().run({}, _ctx(video))

    assert out.metadata["frame_times"] == [0.5, 1.5, 2.5, 3.5, 4.0]
    assert len(out.artifacts) == 5
    assert round(_second_of(out.artifacts[-1], 5)) == 4


@pytest.mark.asyncio
async def test_a_video_whose_length_cannot_be_read_says_its_frames_are_from_the_start(
    tmp_path, monkeypatch
):
    """Without a length there is nothing to spread over: one frame every 10 seconds from the
    start, and a duration of 0, which is how the item knows to say so."""
    video = _video(tmp_path, 40)

    async def _unknown(path):
        return 0.0

    monkeypatch.setattr(media_nodes, "media_seconds", _unknown)

    out = await FrameExtractNode().run({}, _ctx(video))

    assert out.metadata["duration"] == 0.0
    assert out.metadata["frame_times"][:4] == [0.0, 10.0, 20.0, 30.0]
    assert len(out.artifacts) == 4, "a frame was kept past the end of the video"


@pytest.mark.asyncio
async def test_a_denser_pass_fills_in_between_the_frames_already_taken(tmp_path):
    """The classifier can ask for denser sampling; each pass adds frames between the ones
    already taken, across the whole span, rather than a burst from its start."""
    video = _video(tmp_path, 360)
    node = FrameExtractNode()
    await node.run({}, _ctx(video))

    out = await node.run(
        {}, _ctx(video, loop_iteration=1, dense_regions=[{"start": 0, "end": 360}])
    )

    times = out.metadata["frame_times"]
    assert len(times) == 16 and times == sorted(times)
    assert times[:4] == [0.0, 22.5, 45.0, 67.5]
    assert times[-1] == 337.5
    taken = [_second_of(frame, 360) for frame in out.artifacts]
    assert taken == sorted(taken) and taken[-1] > 315


def _frames(n: int) -> NodeOutput:
    return NodeOutput(
        node_type="frame_extract",
        backend="ffmpeg",
        pooled=False,
        artifacts=[f"/frames/v-1.frame_{i:09d}.jpg" for i in range(n)],
        metadata={"frame_count": n, "duration": 360.0},
    )


@pytest.mark.asyncio
async def test_vision_reads_frames_spread_across_the_video_not_its_first_ones(monkeypatch):
    """🔴 Red before: the description was made from the first 4 frames, half the video."""
    seen: list[list[str]] = []

    async def _complete(use_case, prompt, *, images=None, **kw):
        seen.append(list(images or []))
        return "a description"

    monkeypatch.setattr(media_nodes, "complete_text", _complete)
    await VisionNode().run(
        {"frame_extract": _frames(8)}, NodeContext(item_id="v-1", item_type="video")
    )

    assert seen == [[f"/frames/v-1.frame_{i:09d}.jpg" for i in (1, 3, 5, 7)]]


@pytest.mark.asyncio
async def test_the_classifier_looks_at_frames_spread_across_the_video(monkeypatch):
    seen: list[list[str]] = []

    async def _complete(use_case, prompt, *, images=None, **kw):
        seen.append(list(images or []))
        return "talking-head; dense=no"

    monkeypatch.setattr(media_nodes, "complete_text", _complete)
    await VideoClassifyNode().run(
        {"frame_extract": _frames(8)}, NodeContext(item_id="v-1", item_type="video")
    )

    assert seen == [[f"/frames/v-1.frame_{i:09d}.jpg" for i in (0, 2, 3, 4, 6, 7)]]


@pytest.mark.asyncio
async def test_a_text_heavy_videos_words_are_read_from_frames_across_it(monkeypatch, tmp_path):
    """🔴 Red before: the text reader read the video's first frame only, so the words on a
    walkthrough's later slides never reached the item. It reads frames spread across all of
    them, in time order, and an image on its own is still read on its own."""
    from PIL import Image

    frames = []
    for i in range(8):
        frame = tmp_path / f"v-1.frame_{i:09d}.jpg"
        Image.new("RGB", (8, 8), (i * 30, 0, 0)).save(frame, "JPEG")
        frames.append(str(frame))
    asked: list[tuple[str, list[str]]] = []

    async def _complete(use_case, prompt, *, images=None, **kw):
        asked.append((prompt, list(images or [])))
        return "Agenda"

    monkeypatch.setattr(media_nodes, "complete_text", _complete)
    shots = NodeOutput(
        node_type="frame_extract",
        backend="ffmpeg",
        pooled=False,
        artifacts=frames,
        metadata={"frame_count": 8, "duration": 360.0},
    )
    await OcrNode().run({"frame_extract": shots}, NodeContext(item_id="v-1", item_type="video"))
    await OcrNode().run({}, NodeContext(item_id="p-1", item_type="image", file_path=frames[0]))

    (video_prompt, video_frames), (image_prompt, image_frames) = asked
    assert video_frames == [frames[i] for i in (1, 3, 5, 7)]
    assert "frames sampled in time order from a video" in video_prompt
    assert image_frames == [frames[0]]
    assert "in this image" in image_prompt


def test_the_ingest_records_on_the_item_where_its_frames_came_from(tmp_path):
    """The item says which part of the video was looked at: the runner promotes the frame step's
    facts onto ``file_metadata``, where the item page reads them."""
    import asyncio

    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.pipeline.runner import ingest_item
    from personalclaw.knowledge.store import KnowledgeStore

    ensure_nodes_registered()
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    video = _video(tmp_path, 360)
    iid = store.create_typed_item(item_type="video", title="walkthrough", content="")
    store.update_item(iid, file_path=str(video))
    store.db.commit()

    asyncio.run(ingest_item(store, iid))

    meta = store.get_item(iid)["file_metadata"]
    assert meta["frames_sampled"] == 8
    assert meta["frame_times"] == [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5]
    assert meta["video_seconds"] == pytest.approx(360, abs=0.5)

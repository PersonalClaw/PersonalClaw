"""Media + video processing nodes.

Pure-python nodes (exif, thumbnail, ffmpeg split, frame-extract) need no model.
Model-backed nodes resolve DIRECTLY to a default capability binding — OCR / vision /
video-classify → ``image_modality`` (with nothing bound, a chat model that takes images),
consolidation → ``chat``, transcription → ``stt`` — and **gracefully skip** when no model
serves (the executor checks ``registry.unserved_reason`` first). There are no dedicated
ingestion use-cases / per-role overrides: ingestion simply uses the model you bound for that
capability.

The video graph (graphs.py ``VideoGraph``) wires these into the worked-example DAG:
a/v split → transcription ‖ (frame-extract → classify → conditional ocr|vision →
consolidate).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import subprocess

from personalclaw.cancellation import kill_timed_out
from personalclaw.ffmpeg_binary import ffmpeg_not_found, find_ffmpeg, find_ffprobe
from personalclaw.knowledge.pipeline.nodes._llm import complete_text
from personalclaw.knowledge.pipeline.registry import register_node
from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput

logger = logging.getLogger(__name__)

_FRAME_CAP = 8  # max frames one sampling pass takes from a video (bounds cost)
#: How far apart frames are taken when a video's length cannot be read: from its start, since
#: there is no length to spread them over.
_FALLBACK_STEP_S = 10.0
#: Where each denser pass puts its frames inside the slots the first pass spread across the
#: span, as a fraction of a slot: the first pass takes the middle of each (0.5), and the denser
#: ones its start and its quarters, so after all of them the frames sit a quarter-slot apart.
_DENSER_OFFSETS = (0.0, 0.25, 0.75)
#: How many sampled frames the classifier, the description and the text reader are made from.
_CLASSIFY_FRAMES = 6
_VISION_FRAMES = 4
_OCR_FRAMES = 4
#: How long ffprobe may take to read a file's length before it is stopped.
_PROBE_TIMEOUT_S = 15.0


async def media_seconds(path: str) -> float:
    """How long the media file at *path* runs, in seconds, as its container says; 0.0 when there
    is no ffprobe, no file, or no length to read (a stream that does not record one).

    A child whose wait yields to the event loop: the blocking probes this replaces held the loop,
    and every request the gateway was serving, for as long as ffprobe took."""
    ffprobe = find_ffprobe()
    if not path or not ffprobe:
        return 0.0
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        logger.debug("ffprobe could not start for %s", path, exc_info=True)
        return 0.0
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_PROBE_TIMEOUT_S)
    except asyncio.TimeoutError:
        await kill_timed_out(proc)
        logger.warning("ffprobe did not read the length of %s within %ss", path, _PROBE_TIMEOUT_S)
        return 0.0
    except asyncio.CancelledError:
        await kill_timed_out(proc)
        raise
    try:
        seconds = float((out or b"").decode("utf-8", "replace").strip() or 0)
    except ValueError:  # "N/A": the container does not say
        return 0.0
    return seconds if math.isfinite(seconds) and seconds > 0 else 0.0


def _spread(start: float, span: float, count: int, offset: float) -> list[tuple[float, float]]:
    """*count* equal slots across [start, start + span): for each, the time *offset* of the way
    through it, and the time it starts."""
    slot = span / count
    return [
        (round(start + (i + offset) * slot, 3), round(start + i * slot, 3)) for i in range(count)
    ]


def _evenly(items: list, count: int) -> list:
    """*count* of *items*, spread across the whole list in its order (all of them when there are
    no more than that): what a step that reads a few of a video's frames reads, so they cover the
    video rather than its opening."""
    if len(items) <= count:
        return list(items)
    return [items[int((i + 0.5) * len(items) / count)] for i in range(count)]


def _cannot_read(node_type: str, backend: str, ctx: NodeContext) -> NodeOutput:
    """The failed step for a video with no file to read, or with no ffmpeg to read it: that one
    in the words the owner reads where the step shows (``ffmpeg_not_found``)."""
    error = ffmpeg_not_found() if ctx.file_path else "no file"
    return NodeOutput(
        node_type=node_type, backend=backend, success=False, error=error, pooled=False
    )


async def _lexicon_bias_terms(ctx: NodeContext) -> list[str] | None:
    """Pre-decode bias terms for this item's transcription: the Lexicon's hook.

    Returns the Lexicon's ranked, budget-capped term list — context-scoped to the item's
    siblings when the Lexicon can resolve them, else the globally top-weighted terms.
    Returns ``None`` when the Lexicon isn't present/enabled or has nothing to offer, in
    which case the STT provider transcribes with no bias (today's behavior). Best-effort:
    a Lexicon error must never fail transcription."""
    try:
        from personalclaw.lexicon import select_bias_terms
    except Exception:
        return None
    try:
        terms = await select_bias_terms(context_item_id=ctx.item_id)
        return terms or None
    except Exception:
        logger.debug("lexicon bias-term selection failed (non-fatal)", exc_info=True)
        return None


# ── image: pure-python ──


class ExifNode:
    node_type = "exif"
    backend = "pillow"
    uses_use_case = None

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        if not ctx.file_path:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error="no file"
            )
        meta: dict = {}
        try:
            from PIL import Image  # type: ignore

            with Image.open(ctx.file_path) as im:
                meta = {
                    "width": im.width,
                    "height": im.height,
                    "format": im.format,
                    "mode": im.mode,
                }
        except Exception as exc:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error=str(exc)
            )
        # Structural only — feeds metadata, not the text pool.
        return NodeOutput(
            node_type=self.node_type, backend=self.backend, metadata=meta, pooled=False
        )


# ── image: model-backed ──


class OcrNode:
    node_type = "ocr"
    backend = "vision-llm"
    # OCR reads text from an image → the image-understanding capability. Ingestion uses
    # the default use-case bindings directly (no dedicated ingestion override).
    uses_use_case = "image_modality"

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        # OCR a single image (ctx.file_path) OR frames passed by an upstream node.
        from personalclaw.knowledge.pipeline.nodes.ocr_nodes import ocr_rejection
        from personalclaw.ocr.filetype import partition_images

        images = _images_from(inputs, ctx)
        if not images:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error="no image"
            )
        # The SAME true-type gate the engine backend uses, applied BEFORE the model sees the
        # bytes. This backend used to skip it, so a plain text file named `.png` was uploaded
        # to a vision model unchecked while its sibling backend refused the identical file —
        # the gate belongs to the `ocr` node type, not to whichever backend happened to run.
        accepted, rejected = partition_images(images)
        if not accepted:
            return ocr_rejection(self.node_type, self.backend, rejected)
        # A video hands its sampled frames: their words are read from frames spread across all
        # of them, in time order, as the description reads its scene. The first frame alone was
        # one moment of the video, and the words on every later slide never reached the item.
        if ctx.item_type == "video" and len(accepted) > 1:
            prompt = (
                "These are frames sampled in time order from a video. Transcribe ALL text visible "
                "in them verbatim, in time order, writing text that stays on screen across frames "
                "once. Output only the text, no commentary."
            )
            read = _evenly(accepted, _OCR_FRAMES)
        else:
            prompt = "Transcribe ALL text visible in this image verbatim. Output only the text, no commentary."  # noqa: E501
            read = accepted[:1]
        text = await complete_text(self.uses_use_case, prompt, images=read)
        meta: dict = {"ocr_rejected": rejected} if rejected else {}
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            text=text,
            metadata=meta,
            classification="text-heavy",
        )


class VisionNode:
    node_type = "vision"
    backend = "vision-llm"
    uses_use_case = "image_modality"

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        images = _images_from(inputs, ctx)
        if not images:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error="no image"
            )
        # A single-image item → describe the one image. A video hands several sampled
        # frames → describe them together as one scene (up to 4, in time order, spread across
        # the frames it has) so the description reflects the whole clip: its first 4 frames
        # were the first half of it.
        multi = len(images) > 1
        prompt = (
            "These are frames sampled in time order from a video. Describe what the video "
            "shows overall: subjects, setting, notable objects, any on-screen text, and what it conveys."  # noqa: E501
            if multi
            else "Describe this image in detail: subjects, setting, notable objects, any text, and overall meaning."  # noqa: E501
        )
        text = await complete_text(
            self.uses_use_case, prompt, images=_evenly(images, _VISION_FRAMES)
        )
        return NodeOutput(node_type=self.node_type, backend=self.backend, text=text)


# ── audio ──


class TranscriptionNode:
    node_type = "transcription"
    backend = "stt"
    uses_use_case = "stt"  # REUSE stt (transcription == the stt capability)

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        audio = _audio_from(inputs, ctx)
        if not audio:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error="no audio"
            )
        try:
            from personalclaw.transcribe import transcribe_audio_detailed

            # Lexicon hook: bias the decoder toward the user's Lexicon terms (context-scoped
            # to this item's siblings when available, else globally top-weighted). No-op
            # for providers without supports_bias_terms.
            bias_terms = await _lexicon_bias_terms(ctx)
            # An import is unattended work whoever started it: held to the dollar caps.
            result = await transcribe_audio_detailed(audio, bias_terms=bias_terms, unattended=True)
        except Exception as exc:
            # No transcript, and the sentence says why (speech-to-text off, no model, the
            # provider's own reason, or a provider that gave back nothing): the step FAILED.
            # This used to answer "done" with an empty transcript whenever the provider came
            # back with nothing, so a video whose narration was never transcribed read
            # "Transcription done".
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error=str(exc)
            )
        if not (result.text or "").strip():
            # The provider heard no speech (silence, music): a true result, not a failure. It
            # pools nothing, so the item is not flagged as a lying extractor, and ``no_speech``
            # is promoted onto the item so it SAYS there is no transcript and why.
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                text="",
                pooled=False,
                metadata={"no_speech": True},
            )
        # Flat text flows to FTS + embeddings unchanged; the structured transcript (segments
        # + word timestamps) rides in metadata["transcript"]: the runner persists node
        # metadata to extracted_contents, so it needs no items/extracted_contents schema change.
        metadata: dict = {}
        if result.segments:
            metadata["transcript"] = result.to_dict()
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            text=result.text or "",
            metadata=metadata,
        )


class LexiconCorrectionNode:
    """Post-decode phonetic correction over a structured transcript.

    Runs after speaker_fusion (or after transcription when no diarization). Reads the
    upstream ``metadata['transcript']`` (the TranscriptResult JSON), asks the Lexicon to
    correct mis-heard terms (hybrid: auto-apply learned/high-confidence, propose the rest),
    and re-emits the corrected transcript + ``corrections_applied``/``corrections_suggested``
    so the UI can render accept/reject highlights. No model, no tokens.

    Skips gracefully (passes the transcript through unchanged) when the Lexicon is empty or
    the item carries no structured transcript — so transcription works with or without the
    Lexicon."""

    node_type = "lexicon_correction"
    backend = "lexicon"
    uses_use_case = None  # deterministic; no model

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        transcript = _transcript_from(inputs)
        flat = _transcript_flat_text(inputs)
        if transcript is None:
            # Nothing structured to correct — pass the flat text through unchanged. With no
            # text either (no speech, or the transcription failed) there is nothing to pool,
            # and pooling an empty pass-through would flag the item as a lying extractor.
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, text=flat, pooled=bool(flat)
            )
        try:
            from personalclaw.lexicon import current_lexicon
            from personalclaw.stt.provider import (
                TranscriptResult,
                TranscriptSegment,
                TranscriptWord,
            )

            svc = current_lexicon()
            if svc.store.count_terms() == 0:
                return NodeOutput(
                    node_type=self.node_type,
                    backend=self.backend,
                    text=flat,
                    metadata={"transcript": transcript},
                )
            # Rehydrate the dict → TranscriptResult so the service mutates typed objects.
            result = TranscriptResult(
                text=transcript.get("text", ""),
                language=transcript.get("language", ""),
                duration=transcript.get("duration", 0.0),
                segments=[
                    TranscriptSegment(
                        start=s.get("start", 0.0),
                        end=s.get("end", 0.0),
                        text=s.get("text", ""),
                        speaker=s.get("speaker"),
                        words=[
                            TranscriptWord(
                                w.get("start", 0.0),
                                w.get("end", 0.0),
                                w.get("word", ""),
                                w.get("prob", 1.0),
                            )
                            for w in s.get("words", [])
                        ],
                    )
                    for s in transcript.get("segments", [])
                ],
            )
            outcome = svc.correct(result)
        except Exception:
            logger.debug(
                "lexicon_correction failed (non-fatal); passing transcript through", exc_info=True
            )
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                text=flat,
                metadata={"transcript": transcript},
            )

        corrected = result.to_dict()
        meta = {
            "transcript": corrected,
            "corrections_applied": [c.__dict__ for c in outcome.applied],
            "corrections_suggested": [c.__dict__ for c in outcome.suggested],
        }
        # The corrected words keep their speakers. This used to hand on the transcript's plain
        # text, so a two-voice recording lost the speaker labels speaker_fusion had put on it as
        # soon as the Lexicon held a term.
        text = _speaker_attributed_text(corrected) or result.text or flat
        return NodeOutput(node_type=self.node_type, backend=self.backend, text=text, metadata=meta)


class DiarizationNode:
    """Speaker diarization ("who spoke when"). Emits ``metadata['speaker_turns']``
    = [{start,end,speaker}]. The executor skips it when no diarization model is bound, so the
    audio graph works with or without one. Structural (``pooled=False``): its product is the
    turns speaker_fusion reads, never text of its own."""

    node_type = "diarization"
    backend = "diarization"
    uses_use_case = "diarization"

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        audio = _audio_from(inputs, ctx)
        if not audio:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error="no audio"
            )
        try:
            from personalclaw.diarize import diarize_audio

            turns = await diarize_audio(audio)
        except Exception as exc:
            # A bound model that could not diarize FAILED, with the sentence saying why. This
            # used to be read as "no speakers" and reported done: a two-voice clip the
            # provider could not even decode came back with one undivided transcript.
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error=str(exc),
                pooled=False,
            )
        meta = {
            "speaker_turns": [{"start": t.start, "end": t.end, "speaker": t.speaker} for t in turns]
        }
        return NodeOutput(
            node_type=self.node_type, backend=self.backend, metadata=meta, pooled=False
        )


class SpeakerFusionNode:
    """Deterministic fusion (no model, no tokens): assign each transcript
    word/segment the speaker whose diarization turn has MAXIMUM temporal overlap, then roll
    words up to segments (splitting a segment when the speaker changes mid-segment). Emits
    the transcript with ``segment.speaker`` filled + a speaker-attributed flat text. When no
    speaker turns are present (no diarization model), passes the transcript through
    unchanged — so transcription works with or without diarization."""

    node_type = "speaker_fusion"
    backend = "native"
    uses_use_case = None

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        transcript = _transcript_from(inputs)
        turns = _speaker_turns_from(inputs)
        flat = _transcript_flat_text(inputs)
        if transcript is None:
            # No structured transcript to label. An empty pass-through pools nothing, for the
            # reason lexicon_correction gives.
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, text=flat, pooled=bool(flat)
            )
        if not turns:
            # No diarization → pass the transcript through untouched.
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                text=flat,
                metadata={"transcript": transcript},
            )
        fused = _fuse_speakers(transcript, turns)
        attributed = _speaker_attributed_text(fused)
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            text=attributed or flat,
            metadata={"transcript": fused},
        )


def _speaker_turns_from(inputs: dict) -> list[dict]:
    for o in inputs.values():
        if o and isinstance(o.metadata, dict) and isinstance(o.metadata.get("speaker_turns"), list):
            return o.metadata["speaker_turns"]
    return []


def _speaker_for(start: float, end: float, turns: list[dict]) -> str | None:
    """The speaker whose turn overlaps [start,end) most (max temporal overlap)."""
    best, best_ov = None, 0.0
    for t in turns:
        ov = min(end, t.get("end", 0.0)) - max(start, t.get("start", 0.0))
        if ov > best_ov:
            best_ov, best = ov, t.get("speaker")
    return best


def _fuse_speakers(transcript: dict, turns: list[dict]) -> dict:
    """Return a copy of *transcript* with each segment's ``speaker`` set by max-overlap,
    splitting a segment when its words span multiple speakers."""
    out_segments: list[dict] = []
    for seg in transcript.get("segments", []):
        words = seg.get("words", [])
        if not words:
            seg = {
                **seg,
                "speaker": _speaker_for(seg.get("start", 0.0), seg.get("end", 0.0), turns),
            }
            out_segments.append(seg)
            continue
        # Walk words; start a new sub-segment whenever the speaker changes.
        cur_words: list[dict] = []
        cur_spk: str | None = None
        for w in words:
            spk = _speaker_for(w.get("start", 0.0), w.get("end", 0.0), turns)
            if cur_words and spk != cur_spk:
                out_segments.append(_segment_from_words(cur_words, cur_spk))
                cur_words = []
            cur_spk = spk
            cur_words.append(w)
        if cur_words:
            out_segments.append(_segment_from_words(cur_words, cur_spk))
    return {**transcript, "segments": out_segments}


def _segment_from_words(words: list[dict], speaker: str | None) -> dict:
    return {
        "start": words[0].get("start", 0.0),
        "end": words[-1].get("end", 0.0),
        "text": "".join(w.get("word", "") for w in words).strip(),
        "speaker": speaker,
        "words": words,
    }


def _speaker_attributed_text(transcript: dict) -> str:
    """Flat text prefixed by speaker labels ("SPEAKER_00: …") for the consolidation arm, or
    ``""`` when fewer than two people speak in it: a label then tells no voices apart, and a
    one-voice memo read "SPEAKER_00: …" from its first word."""
    if len({s.get("speaker") for s in transcript.get("segments", []) if s.get("speaker")}) < 2:
        return ""
    lines: list[str] = []
    last_spk = None
    for seg in transcript.get("segments", []):
        spk = seg.get("speaker")
        text = seg.get("text", "")
        if not text:
            continue
        if spk and spk != last_spk:
            lines.append(f"{spk}: {text}")
            last_spk = spk
        else:
            lines.append(text)
    return "\n".join(lines)


def _transcript_from(inputs: dict) -> dict | None:
    """The structured transcript dict from the nearest upstream node that carries one."""
    for o in inputs.values():
        if o and isinstance(o.metadata, dict) and isinstance(o.metadata.get("transcript"), dict):
            return o.metadata["transcript"]
    return None


def _transcript_flat_text(inputs: dict) -> str:
    for o in inputs.values():
        if o and o.text:
            return o.text
    return ""


# ── video: pure-python structural ──


class AvSplitNode:
    """Split a video into an audio track + keep the video path (ffmpeg)."""

    node_type = "av_split"
    backend = "ffmpeg"
    uses_use_case = None

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        ff = find_ffmpeg()
        if not ctx.file_path or not ff:
            return _cannot_read(self.node_type, self.backend, ctx)
        work = ctx.work_dir or os.path.dirname(ctx.file_path)
        audio_out = os.path.join(work, f"{ctx.item_id}.audio.wav")
        cmd = [ff, "-y", "-i", ctx.file_path, "-vn", "-ac", "1", "-ar", "16000", audio_out]
        rc = await _run_cmd(cmd)
        meta = {"video": ctx.file_path}
        if rc == 0 and os.path.exists(audio_out):
            meta["audio"] = audio_out
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            pooled=False,
            artifacts=[audio_out] if "audio" in meta else [],
            metadata=meta,
        )


class FrameExtractNode:
    """Sample frames from the video (ffmpeg): up to :data:`_FRAME_CAP` at even points across its
    whole length, one in the middle of each of that many equal slots, so a 6-minute walkthrough
    is seen through all six minutes. It used to take one every 10 seconds from the start, and
    the 8 frames were the video's first 70 seconds. A video whose length cannot be read still
    gets frames every 10 seconds from its start, and its ``duration`` of 0 says so.

    When the adaptive loop hands back ``dense_regions`` (timestamp ranges the classifier flagged
    as content-heavy), each pass adds a frame between those already taken in every slot of the
    region, so a 1 h video with 10 min of screen-share gets tight sampling only around those 10
    min, spread across them rather than bunched at their start.

    A frame is named for the millisecond it was taken at, so the frames sort in time order, and
    ``frame_times`` is read back from what is on disk: what the item says it looked at is what
    there is."""

    node_type = "frame_extract"
    backend = "ffmpeg"
    uses_use_case = None

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        ff = find_ffmpeg()
        if not ctx.file_path or not ff:
            return _cannot_read(self.node_type, self.backend, ctx)
        work = ctx.work_dir or os.path.dirname(ctx.file_path)
        params = ctx.params or {}
        dense_regions = params.get("dense_regions") or []
        iteration = int(params.get("loop_iteration", 0))
        duration = await media_seconds(ctx.file_path)

        meta: dict = {}
        if dense_regions:
            offset = _DENSER_OFFSETS[min(max(iteration, 1), len(_DENSER_OFFSETS)) - 1]
            plan = [t for region in dense_regions for t in _denser(region, duration, offset)]
            meta = {"dense_iteration": iteration, "dense_regions": dense_regions}
        else:
            # A first pass starts clean, so frames another run of this item took are not
            # counted as this one's.
            for stale in self._existing_frames(work, ctx.item_id):
                try:
                    os.unlink(stale)
                except OSError:
                    logger.debug("could not remove an earlier frame %s", stale, exc_info=True)
            if duration > 0:
                plan = _spread(0.0, duration, max(1, min(_FRAME_CAP, int(duration))), 0.5)
            else:
                plan = [(i * _FALLBACK_STEP_S,) * 2 for i in range(_FRAME_CAP)]
        for at, slot_start in plan:
            # ffmpeg gives the first frame AT or after a time, so a slot's middle that falls
            # inside a video's last frame (one frame a second, a still picture) finds none: the
            # frame at the start of that slot is taken instead.
            if not await _take_frame(ff, ctx.file_path, work, ctx.item_id, at) and slot_start < at:
                await _take_frame(ff, ctx.file_path, work, ctx.item_id, slot_start)
        frames = self._existing_frames(work, ctx.item_id)
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            pooled=False,
            artifacts=frames,
            metadata={
                "frame_count": len(frames),
                "frame_times": [_frame_time(frame) for frame in frames],
                "duration": round(duration, 3),
                **meta,
            },
        )

    @staticmethod
    def _existing_frames(work: str, item_id: str) -> list[str]:
        """This item's frames in *work*, in time order (their names are their times)."""
        if not os.path.isdir(work):
            return []
        prefix = f"{item_id}.frame_"
        return sorted(
            os.path.join(work, f)
            for f in os.listdir(work)
            if f.startswith(prefix) and f.endswith(".jpg")
        )


async def _take_frame(ff: str, video: str, work: str, item_id: str, at: float) -> bool:
    """Take the frame at *at* seconds into *video*: whether there was one to take. Seeking
    first, ffmpeg decodes from the keyframe before *at*, not the whole video up to it."""
    out = _frame_path(work, item_id, at)
    await _run_cmd(
        [
            ff,
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{at:.3f}",
            "-i",
            video,
            "-frames:v",
            "1",
            "-an",
            out,
        ]
    )
    return os.path.isfile(out)


def _denser(region: dict, duration: float, offset: float) -> list[tuple[float, float]]:
    """One denser pass over *region* ``{start, end}``: a frame at *offset* of each slot. An end
    at or before the start means the rest of the clip; with no length to know that by, the slots
    are the 10-second ones a video of unknown length was sampled on."""
    start = max(0.0, float(region.get("start", 0) or 0))
    end = float(region.get("end", 0) or 0)
    if end <= start:
        end = duration
    if end > start:
        return _spread(start, end - start, _FRAME_CAP, offset)
    return _spread(start, _FRAME_CAP * _FALLBACK_STEP_S, _FRAME_CAP, offset)


def _frame_path(work: str, item_id: str, at: float) -> str:
    """Where the frame taken at *at* seconds goes: named for its millisecond, zero-padded, so the
    names sort in time order."""
    return os.path.join(work, f"{item_id}.frame_{int(round(at * 1000)):09d}.jpg")


def _frame_time(frame: str) -> float:
    """The second a frame was taken at, read back from its name (:func:`_frame_path`)."""
    stem = os.path.basename(frame).rsplit(".frame_", 1)[-1].split(".", 1)[0]
    try:
        return int(stem) / 1000
    except ValueError:
        return 0.0


# ── video: model-backed ──


class VideoClassifyNode:
    """Classify the video's dominant frame kind AND drive the adaptive re-sampling
    loop. It inspects the sampled frames, and when a content-heavy segment
    (screen-share/diagram/whiteboard/slides) appears under-sampled it emits
    classification ``needs-denser`` + the ``dense_regions`` (timestamp ranges) to
    resample — the executor loops back to frame_extract (bounded to max_iters). Once
    coverage is sufficient (or the loop budget is spent), it emits the terminal
    content verdict (text-heavy / visual / talking-head) that routes OCR vs vision."""

    node_type = "video_classify"
    backend = "vision-llm"
    uses_use_case = "image_modality"
    _MAX_DENSE_ITERS = 3  # must match the graph's loop_edge max_iters

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        frames = _frames_from(inputs)
        if not frames:
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error="no frames",
                pooled=False,
            )
        iteration = int((ctx.params or {}).get("loop_iteration", 0))

        # Ask the model both for the dominant content kind AND whether specific time
        # regions are dense/content-heavy enough to warrant tighter sampling. We keep
        # the contract simple + robust: one word verdict, then optional region hints.
        prompt = (
            "These are sampled frames from a video (in time order). First classify the "
            "DOMINANT content with EXACTLY one of: text-heavy (slides/docs/code/screen-share), "
            "visual (scenes/objects/diagrams/whiteboard), talking-head (a person speaking).\n"
            "Then, if the video appears to contain dense information that a sparse sample "
            "would miss (screen-share, whiteboard, diagrams, rapidly-changing slides), say so.\n"
            "Reply as: '<verdict>; dense=<yes|no>'. Example: 'text-heavy; dense=yes'."
        )
        # Frames spread across all of them, in time order: the first 6 were the opening only.
        raw = await complete_text(
            self.uses_use_case, prompt, images=_evenly(frames, _CLASSIFY_FRAMES)
        )
        v = (raw or "").strip().lower()
        content_cls = (
            "text-heavy" if "text" in v else "talking-head" if "talking" in v else "visual"
        )
        wants_dense = "dense=yes" in v or ("dense" in v and "yes" in v)

        # Content-heavy AND flagged dense AND still within the loop budget → request a
        # denser pass, over the whole timeline (`_dense_regions`).
        if (
            wants_dense
            and content_cls in ("text-heavy", "visual")
            and iteration < self._MAX_DENSE_ITERS
        ):
            regions = self._dense_regions(inputs)
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                classification="needs-denser",
                metadata={
                    "verdict": content_cls,
                    "dense": True,
                    "dense_regions": regions,
                    "iteration": iteration,
                },
                pooled=False,
            )

        # Carry the frames through as artifacts. ocr/vision are DIRECT successors of
        # video_classify (edges: classify→vision/ocr) and the executor feeds a node only
        # its direct predecessors' outputs — so without this, the downstream vision/ocr
        # node sees no frame_extract output and falls back to the raw .mp4 (which a vision
        # model can't read → empty extraction → consolidate fails). Passing frames here
        # is what lets vision/ocr actually receive the sampled JPGs.
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            classification=content_cls,
            artifacts=list(frames),
            metadata={"verdict": content_cls, "dense": wants_dense, "iterations": iteration},
            pooled=False,
        )

    def _dense_regions(self, inputs: dict) -> list[dict]:
        """Timestamp ranges to resample densely: the whole clip, by the length the frame step
        read (an end of 0, when it read none, means to the end of the clip)."""
        return [{"start": 0, "end": _frames_duration(inputs)}]


class VideoConsolidateNode:
    """Reasoning-LLM fan-in: merge per-frame OCR/vision + transcript into one video description."""

    node_type = "video_consolidate"
    backend = "reasoning-llm"
    uses_use_case = "chat"  # consolidation is chat-model reasoning (default binding)

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        pieces = []
        # The transcript reaches this step through the last step that worked on it
        # (lexicon_correction, in the video graph): this read only "transcription", which is not
        # one of its inputs, so a video's narration was never part of its description.
        for label, steps in (
            ("ocr", ("ocr",)),
            ("vision", ("vision",)),
            ("transcription", ("lexicon_correction", "speaker_fusion", "transcription")),
        ):
            o = next((inputs[s] for s in steps if s in inputs), None)
            if o and o.success and o.text:
                pieces.append(f"[{label}]\n{o.text}")
        if not pieces:
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error="nothing to consolidate",
            )
        merged_src = "\n\n".join(pieces)
        text = await complete_text(
            self.uses_use_case,
            "Below are extracted signals from a video (frame text/descriptions and/or an audio "
            "transcript). Write a single coherent description of what the video contains and conveys.\n\n"  # noqa: E501
            + merged_src,
        )
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            text=text or merged_src,
            metadata={"sources": [p.split("]")[0].strip("[") for p in pieces]},
        )


# ── input helpers ──


def _images_from(inputs: dict, ctx: NodeContext) -> list[str]:
    frames = _frames_from(inputs)
    if frames:
        return frames
    return [ctx.file_path] if ctx.file_path else []


def _frames_duration(inputs: dict) -> float:
    """The length of the video, in seconds, as the frame step read it; 0.0 when it read none."""
    for o in inputs.values():
        if o and o.node_type == "frame_extract" and isinstance(o.metadata, dict):
            return float(o.metadata.get("duration") or 0.0)
    return 0.0


def _frames_from(inputs: dict) -> list[str]:
    for o in inputs.values():
        if o and o.artifacts and o.node_type in ("frame_extract",):
            return list(o.artifacts)
    # fall back: any upstream artifacts that look like images
    for o in inputs.values():
        if o and o.artifacts:
            imgs = [a for a in o.artifacts if a.lower().endswith((".jpg", ".jpeg", ".png"))]
            if imgs:
                return imgs
    return []


def _audio_from(inputs: dict, ctx: NodeContext) -> str:
    for o in inputs.values():
        if o and isinstance(o.metadata, dict) and o.metadata.get("audio"):
            return str(o.metadata["audio"])
        for a in (o.artifacts if o else []):
            if a.lower().endswith((".wav", ".mp3", ".m4a", ".flac")):
                return a
    # a raw audio item → its own file
    if ctx.item_type == "audio" and ctx.file_path:
        return ctx.file_path
    return ""


async def _run_cmd(cmd: list[str]) -> int:
    """Run an ffmpeg command to its end: its exit status, or 1 when it could not start.

    A cancelled run (its step out of time, or the gateway stopping) kills the ffmpeg before the
    cancel goes on: it used to run on after its step was given up on. ffmpeg starts no programs of
    its own, so killing it leaves nothing behind. Its stdin is closed, so it never reads the
    gateway's terminal for keys."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        logger.debug("ffmpeg command could not start: %s", cmd[:2], exc_info=True)
        return 1
    try:
        return await proc.wait()
    except asyncio.CancelledError:
        await kill_timed_out(proc)
        raise


def register() -> None:
    for node in (
        ExifNode(),
        OcrNode(),
        VisionNode(),
        TranscriptionNode(),
        AvSplitNode(),
        FrameExtractNode(),
        VideoClassifyNode(),
        VideoConsolidateNode(),
        LexiconCorrectionNode(),
        DiarizationNode(),
        SpeakerFusionNode(),
    ):
        register_node(node)

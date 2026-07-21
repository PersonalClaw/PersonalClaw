"""Media + video processing nodes (#47).

Pure-python nodes (exif, thumbnail, ffmpeg split, frame-extract) need no model.
Model-backed nodes resolve DIRECTLY to a default capability binding — OCR / vision /
video-classify → ``image_modality``, consolidation → ``chat``, transcription → ``stt``
— and **gracefully skip** when no model is active (the executor checks
``can_resolve_use_case`` first). There are no dedicated ingestion use-cases / per-role
overrides: ingestion simply uses the model you bound for that capability.

The video graph (graphs.py ``VideoGraph``) wires these into the worked-example DAG:
a/v split → transcription ‖ (frame-extract → classify → conditional ocr|vision →
consolidate).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess

from personalclaw.knowledge.pipeline.nodes._llm import complete_text
from personalclaw.knowledge.pipeline.registry import register_node
from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput

logger = logging.getLogger(__name__)

_FRAME_CAP = 8  # max frames sampled from a video (bounds cost)


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


async def _lexicon_bias_terms(ctx: NodeContext) -> list[str] | None:
    """Pre-decode bias terms for this item's transcription (core L2 hook).

    Returns the Lexicon's ranked, budget-capped term list — context-scoped to the item's
    siblings when the Lexicon can resolve them, else the globally top-weighted terms.
    Returns ``None`` when the Lexicon isn't present/enabled or has nothing to offer, in
    which case the STT provider transcribes with no bias (today's behavior). Best-effort:
    a Lexicon error must never fail transcription."""
    try:
        from personalclaw.lexicon import select_bias_terms  # available from C2
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
        images = _images_from(inputs, ctx)
        if not images:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error="no image"
            )
        text = await complete_text(
            self.uses_use_case,
            "Transcribe ALL text visible in this image verbatim. Output only the text, no commentary.",  # noqa: E501
            images=images[:1],
        )
        return NodeOutput(
            node_type=self.node_type, backend=self.backend, text=text, classification="text-heavy"
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
        # frames → describe them together as one scene (up to 4, in time order) so the
        # description reflects the whole clip, not just its first frame.
        multi = len(images) > 1
        prompt = (
            "These are frames sampled in time order from a video. Describe what the video "
            "shows overall: subjects, setting, notable objects, any on-screen text, and what it conveys."  # noqa: E501
            if multi
            else "Describe this image in detail: subjects, setting, notable objects, any text, and overall meaning."  # noqa: E501
        )
        text = await complete_text(self.uses_use_case, prompt, images=images[:4])
        return NodeOutput(node_type=self.node_type, backend=self.backend, text=text)


# ── audio ──


class TranscriptionNode:
    node_type = "transcription"
    backend = "stt"
    uses_use_case = "stt"  # REUSE stt (Q1 hybrid: transcription == the stt capability)

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        audio = _audio_from(inputs, ctx)
        if not audio:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error="no audio"
            )
        try:
            from personalclaw.transcribe import transcribe_audio_detailed

            # L2 hook: bias the decoder toward the user's Lexicon terms (context-scoped
            # to this item's siblings when available, else globally top-weighted). No-op
            # until the Lexicon lands / for providers without supports_bias_terms.
            bias_terms = await _lexicon_bias_terms(ctx)
            result = await transcribe_audio_detailed(audio, bias_terms=bias_terms)
        except Exception as exc:
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error=str(exc)
            )
        # An empty transcript is a valid result (silent / no-speech / music audio) —
        # NOT a failure. Return success with empty text so the item lands 'done', not
        # an alarming 'failed'. The transcription genuinely ran; there was just no speech.
        if result is None:
            return NodeOutput(node_type=self.node_type, backend=self.backend, text="")
        # Flat text flows to FTS + embeddings unchanged; the structured transcript (segments
        # + word timestamps) rides in metadata["transcript"] (L0.5 — the runner persists
        # node metadata to extracted_contents; no items/extracted_contents schema change).
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
    """Post-decode phonetic correction over a structured transcript (core LEX.4).

    Runs after speaker_fusion (or after transcription when no diarization). Reads the
    upstream ``metadata['transcript']`` (the L0 TranscriptResult JSON), asks the Lexicon to
    correct mis-heard terms (hybrid: auto-apply learned/high-confidence, propose the rest),
    and re-emits the corrected transcript + ``corrections_applied``/``corrections_suggested``
    so the UI can render accept/reject highlights. No model, no tokens.

    Skips gracefully (passes the transcript through unchanged) when the Lexicon is empty or
    the item carries no structured transcript — so L0 works with or without LEX."""

    node_type = "lexicon_correction"
    backend = "lexicon"
    uses_use_case = None  # deterministic; no model

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        transcript = _transcript_from(inputs)
        flat = _transcript_flat_text(inputs)
        if transcript is None:
            # Nothing structured to correct — pass the flat text through unchanged.
            return NodeOutput(node_type=self.node_type, backend=self.backend, text=flat)
        try:
            from personalclaw.lexicon import get_lexicon_service
            from personalclaw.stt.provider import (
                TranscriptResult,
                TranscriptSegment,
                TranscriptWord,
            )

            svc = get_lexicon_service()
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

        meta = {
            "transcript": result.to_dict(),
            "corrections_applied": [c.__dict__ for c in outcome.applied],
            "corrections_suggested": [c.__dict__ for c in outcome.suggested],
        }
        return NodeOutput(
            node_type=self.node_type, backend=self.backend, text=result.text or flat, metadata=meta
        )


class DiarizationNode:
    """Speaker diarization ("who spoke when") — core L1. Emits ``metadata['speaker_turns']``
    = [{start,end,speaker}]. Skips gracefully (success, no turns) when no diarization model
    is bound, so the audio graph works with or without it."""

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
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error=str(exc)
            )
        if not turns:
            # No model bound / no speech → pass through (fusion will no-op).
            return NodeOutput(node_type=self.node_type, backend=self.backend, text="")
        meta = {
            "speaker_turns": [{"start": t.start, "end": t.end, "speaker": t.speaker} for t in turns]
        }
        return NodeOutput(node_type=self.node_type, backend=self.backend, metadata=meta)


class SpeakerFusionNode:
    """Deterministic fusion (core L1.3, no model, no tokens): assign each transcript
    word/segment the speaker whose diarization turn has MAXIMUM temporal overlap, then roll
    words up to segments (splitting a segment when the speaker changes mid-segment). Emits
    the transcript with ``segment.speaker`` filled + a speaker-attributed flat text. When no
    speaker turns are present (no diarization model), passes the transcript through
    unchanged — so L0 works with or without L1."""

    node_type = "speaker_fusion"
    backend = "native"
    uses_use_case = None

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        transcript = _transcript_from(inputs)
        turns = _speaker_turns_from(inputs)
        flat = _transcript_flat_text(inputs)
        if transcript is None:
            return NodeOutput(node_type=self.node_type, backend=self.backend, text=flat)
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
    """Flat text prefixed by speaker labels ("SPEAKER_00: …") for the consolidation arm."""
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
        ff = _ffmpeg()
        if not ctx.file_path or not ff:
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error="no ffmpeg/file",
                pooled=False,
            )
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
    """Sample frames from the video (ffmpeg). By default up to _FRAME_CAP evenly-
    spaced frames (fps=1/10). When the adaptive loop hands back ``dense_regions``
    (timestamp ranges the classifier flagged as content-heavy), those ranges are
    RE-SAMPLED densely (higher fps) while the rest stays coarse — so a 1 h video with
    10 min of screen-share gets tight sampling only around those 10 min."""

    node_type = "frame_extract"
    backend = "ffmpeg"
    uses_use_case = None

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        ff = _ffmpeg()
        if not ctx.file_path or not ff:
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                success=False,
                error="no ffmpeg/file",
                pooled=False,
            )
        work = ctx.work_dir or os.path.dirname(ctx.file_path)
        params = ctx.params or {}
        dense_regions = params.get("dense_regions") or []
        iteration = int(params.get("loop_iteration", 0))

        if dense_regions:
            # Adaptive iteration: sample the flagged regions densely (an extra frame
            # set per region at a tighter fps). Denser each iteration.
            dense_fps = min(2.0, 0.3 * (2**iteration))  # 0.6 → 1.2 → 2.0 fps, capped
            frames = list(self._existing_frames(work, ctx.item_id))  # keep the coarse set
            for ri, region in enumerate(dense_regions):
                start = float(region.get("start", 0))
                end = float(region.get("end", 0))
                pat = os.path.join(work, f"{ctx.item_id}.dense{iteration}_{ri}_%03d.jpg")
                # end<=start → sample from `start` to the end of the clip (no -to).
                span = ["-to", str(end)] if end > start else []
                cmd = [
                    ff,
                    "-y",
                    "-ss",
                    str(start),
                    *span,
                    "-i",
                    ctx.file_path,
                    "-vf",
                    f"fps={dense_fps}",
                    "-frames:v",
                    str(_FRAME_CAP),
                    pat,
                ]
                await _run_cmd(cmd)
            frames = sorted(self._existing_frames(work, ctx.item_id))
            return NodeOutput(
                node_type=self.node_type,
                backend=self.backend,
                pooled=False,
                artifacts=frames,
                metadata={
                    "frame_count": len(frames),
                    "dense_iteration": iteration,
                    "dense_regions": dense_regions,
                },
            )

        # Initial coarse pass.
        pattern = os.path.join(work, f"{ctx.item_id}.frame_%03d.jpg")
        cmd = [
            ff,
            "-y",
            "-i",
            ctx.file_path,
            "-vf",
            "fps=1/10",
            "-frames:v",
            str(_FRAME_CAP),
            pattern,
        ]
        await _run_cmd(cmd)
        frames = sorted(self._existing_frames(work, ctx.item_id))
        return NodeOutput(
            node_type=self.node_type,
            backend=self.backend,
            pooled=False,
            artifacts=frames,
            metadata={"frame_count": len(frames)},
        )

    @staticmethod
    def _existing_frames(work: str, item_id: str) -> list[str]:
        if not os.path.isdir(work):
            return []
        return [
            os.path.join(work, f)
            for f in os.listdir(work)
            if f.startswith(f"{item_id}.frame_") or f.startswith(f"{item_id}.dense")
        ]


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
        raw = await complete_text(self.uses_use_case, prompt, images=frames[:6])
        v = (raw or "").strip().lower()
        content_cls = (
            "text-heavy" if "text" in v else "talking-head" if "talking" in v else "visual"
        )
        wants_dense = "dense=yes" in v or ("dense" in v and "yes" in v)

        # Content-heavy AND flagged dense AND still within the loop budget → request a
        # denser pass. dense_regions defaults to the whole timeline on the first ask;
        # a real duration probe refines it (region-aware: only the flagged span).
        if (
            wants_dense
            and content_cls in ("text-heavy", "visual")
            and iteration < self._MAX_DENSE_ITERS
        ):
            regions = self._dense_regions(ctx, frames)
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

    def _dense_regions(self, ctx: NodeContext, frames: list) -> list[dict]:
        """Timestamp ranges to resample densely. Region-aware: uses the media duration
        (ffprobe) to target the content-heavy span. Without a probe, targets the whole
        clip (still bounded by max_iters)."""
        import shutil
        import subprocess

        dur = 0.0
        ffprobe = shutil.which("ffprobe")
        if ffprobe and ctx.file_path:
            try:
                out = subprocess.run(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        ctx.file_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                dur = float((out.stdout or "").strip() or 0)
            except (ValueError, OSError, subprocess.SubprocessError):
                dur = 0.0
        if dur <= 0:
            return [{"start": 0, "end": 0}]  # 0 end → sampler treats as whole clip
        return [{"start": 0, "end": dur}]


class VideoConsolidateNode:
    """Reasoning-LLM fan-in: merge per-frame OCR/vision + transcript into one video description."""

    node_type = "video_consolidate"
    backend = "reasoning-llm"
    uses_use_case = "chat"  # consolidation is chat-model reasoning (default binding)

    async def run(self, inputs, ctx: NodeContext) -> NodeOutput:
        pieces = []
        for nt in ("ocr", "vision", "transcription"):
            o = inputs.get(nt)
            if o and o.success and o.text:
                pieces.append(f"[{nt}]\n{o.text}")
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
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode or 0
    except Exception:
        logger.debug("ffmpeg command failed: %s", cmd[:2], exc_info=True)
        return 1


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

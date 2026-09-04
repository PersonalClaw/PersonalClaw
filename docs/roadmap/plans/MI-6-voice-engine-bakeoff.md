# MI-6 — Voice-clone engine bake-off: OmniVoice vs CosyVoice

**Atom:** `MI-6` (the "MI-2c" engine-selection remainder of MULTIMODAL-IO §2.2).
**This deliverable:** the bake-off only — a reproducible scorecard, a chosen engine,
and the loser's rejection notes, committed here per the §2.2 instruction that "the
loser's evaluation notes land in the plan dir, not as a second app." The rest of MI-6
(real inference in `apps/voice-clone-tts`, resumable weight download, the LMM-V2
through-clone selftest, and the sidecar-kill typed crash reason) is **deferred to a
follow-up run** — see [Deferred scope](#deferred-scope).

**Verdict up front: ship k2-fsa OmniVoice. Reject FunAudioLLM CosyVoice.**
Weighted score **0.906 vs 0.658** over the kept criteria.

Reproduce this scorecard at any time — it is pure, offline, and takes no model:

```
python -m personalclaw.evals.voice_engine_bakeoff          # scored matrix + verdict
python -m personalclaw.evals.voice_engine_bakeoff --json   # same, machine-readable
```

The harness lives at `src/personalclaw/evals/voice_engine_bakeoff.py`; it is the source
of truth and this doc is its written-up output. It is deliberately NOT the ES-10 traffic
bake-off (`personalclaw.evals.bakeoff`), which replays the user's own LLM traffic through
candidate *routing* models — this is a one-shot *engine-selection* spike.

## Candidates

| Engine | Repo / card | License | Architecture | Cloning |
|---|---|---|---|---|
| **k2-fsa OmniVoice** | [github.com/k2-fsa/OmniVoice](https://github.com/k2-fsa/OmniVoice) · [HF](https://huggingface.co/k2-fsa/OmniVoice) | Apache-2.0 | diffusion-LM-style discrete NAR | zero-shot from a 3–10 s clip; primary trained task |
| **FunAudioLLM CosyVoice** | [github.com/FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice) · [paper](https://arxiv.org/pdf/2505.17589) | Apache-2.0 | LLM + flow-matching, streaming | zero-shot cross-lingual; one of several modes |

Both are genuine, actively-maintained, Apache-2.0 zero-shot cloning engines. This was not
a straw-man pairing — CosyVoice is the more starred project (≈23.4k vs ≈9.7k) and wins two
criteria outright (below).

## Criteria matrix

Weights encode PersonalClaw's use case — **local, macOS-first, cloning-first, single
user** — not a generic leaderboard. **Every `raw` value is literature (cited) or an
explicit "unknown"; no number here was measured on the build host.** The `score` column
is the reviewer's judgment mapping of that evidence onto this product, in `[0, 1]`.

| Criterion | Weight | OmniVoice (raw → score) | CosyVoice (raw → score) | Provenance |
|---|---:|---|---|---|
| Platform support (Apple Silicon / MPS) | 0.25 | `device_map="mps"` documented + community [MLX weights](https://huggingface.co/mlx-community/OmniVoice) → **1.00** | README is CUDA / nvidia-docker / vLLM / TensorRT-LLM centric, no first-class MPS → **0.40** | judgment on cited READMEs |
| Clone quality proxy (CER/SS) | 0.20 | SOTA-cloning claim; ASR-verified-lossless in its RTF bench; no public CER/SS table → **0.80** | Fun-CosyVoice3-0.5B-RL: test-en WER 1.68 / SS 69.5, test-zh CER 0.81 / SS 77.4 → **0.90** | literature |
| Install weight / complexity | 0.15 | single `pip install omnivoice` (PyPI); `pynini` via conda only for the text-norm extra → **0.90** | recursive-submodule clone + conda py3.10 + `requirements.txt` + optional `ttsfrd` wheels + `sox` → **0.40** | judgment on cited READMEs |
| Inference latency (RTF / first-packet) | 0.15 | RTF 0.0899 (batch=1, H100, fp16, `num_step=32`); ~0.025 batched/accelerated → **0.80** | bi-streaming first-packet as low as 150 ms → **0.85** | literature |
| Weight footprint on disk | 0.10 | **not published** in README / model card at spike time → **excluded** | 0.5B params (CosyVoice2/3-0.5B) → 0.70 | literature / unknown |
| License permissiveness | 0.10 | Apache-2.0 → **1.00** | Apache-2.0 → **1.00** | literature |
| Language coverage | 0.05 | 600+ languages → **1.00** | 9 languages + 18+ Chinese dialects → **0.50** | literature |
| **Weighted score (kept criteria)** | | **0.906** | **0.658** | |

Footprint is **excluded from the aggregate, not guessed**: OmniVoice does not publish an
on-disk weight size, so scoring a published 0.5B against a blank would be dishonest. Its
weight is renormalized across the kept criteria and the number is flagged for measurement
at integration time.

Latency and quality are **not directly comparable across the two** — OmniVoice publishes
offline RTF on an H100; CosyVoice publishes streaming first-packet latency and CER/SS on
its own test sets. They are scored as informed judgments within each axis, and CosyVoice
is (correctly) given the edge on both. The decision does not turn on either.

## Why OmniVoice wins

The three load-bearing axes for a local Mac assistant all favor OmniVoice, and together
they outweigh CosyVoice's quality/latency edges:

1. **Apple-Silicon/MPS is first-class** — a documented `device_map="mps"` path plus a
   community MLX build. CosyVoice's documented runtime is CUDA/nvidia-docker/vLLM/
   TensorRT-LLM; MPS is left to third-party forks. PClaw synthesizes on the user's Mac.
2. **The install stays a double-click** — one PyPI package versus a recursive-submodule
   clone, a conda environment, and optional `ttsfrd`/`sox` system dependencies. The
   heavier install directly fights the "install is a double-click or it doesn't ship"
   tenet.
3. **Cloning is the primary trained task**, and OmniVoice ships a clone-prompt
   persistence API (`create_voice_clone_prompt` → `VoiceClonePrompt.save/load`) that maps
   cleanly onto §2.2's "bounded LRU of precomputed clone prompts" and the locked-voice
   reference-conditioning seam. Broad 600+-language zero-shot coverage is a bonus for a
   general assistant.

OmniVoice is also already the engine the MULTIMODAL-IO entity schema (voice_profiles,
lock flow, consent columns) was mined from, so choosing it keeps the data model and the
engine coherent.

## Rejection notes — FunAudioLLM CosyVoice

Kept here per MI-2 §2.2 (loser's notes to the plan dir; not shipped as a second app).

CosyVoice is the **stronger engine on two axes** and this is not a lopsided call:

- It **edges published clone quality** — it reports concrete CER/SS/WER numbers
  (test-en WER 1.68 / SS 69.5, test-zh CER 0.81 / SS 77.4) where OmniVoice offers a
  qualitative SOTA claim.
- It **edges streaming latency** — bi-streaming first-packet as low as 150 ms, purpose-
  built for real-time streaming synthesis.
- Its **community and deployment maturity are higher** — more stars, and a first-class
  CUDA/vLLM/TensorRT-LLM serving story.

It loses for **this** product because every one of those strengths is one a single-user,
local, macOS sidecar cannot cash in:

- **No first-class Apple-Silicon/MPS path.** The documented runtime is CUDA/nvidia-
  docker/vLLM/TensorRT-LLM. PClaw runs on the user's Mac, so this is disqualifying on the
  highest-weighted axis.
- **Heavy install.** Recursive-submodule clone + conda py3.10 + `requirements.txt` +
  optional `ttsfrd` wheels + `sox` system deps — the opposite of a double-click.
- **Narrower zero-shot coverage.** ~9 languages + Chinese dialects vs 600+.
- **Its wins don't apply.** Server-side streaming first-packet latency and CUDA
  throughput are exactly the advantages a local, non-streaming, single-stream personal
  synthesis path never exercises.

**Reconsider CosyVoice if** PClaw ever grows a server-side or CUDA-backed synthesis tier,
or if a first-class MPS path lands upstream — at that point its quality/latency/maturity
edges would start to count.

## Provenance / honesty

- **Nothing in the matrix was measured on the build host.** All quantitative values are
  vendor-published (cited) or explicitly unknown. The `[0, 1]` scores are the reviewer's
  judgment mapping, kept separate from the raw values in both the doc and the harness.
- **No fabricated benchmarks.** Where a vendor does not publish a number (OmniVoice's
  on-disk footprint), the harness records `unknown` and excludes the criterion rather
  than inventing a value.
- The `measure_fixture_rtf(...)` path in the harness runs **real** fixture inference —
  but only when the engine package and its weights are actually installed and reference
  `*.wav` fixtures exist. On any host lacking those it returns a typed, reasoned **skip**
  (verified: `engine package 'omnivoice' not installed on this host`). That is the
  runnable-but-skipped contract; it never emits a made-up latency.

## Deferred scope

The rest of MI-6, for the follow-up implementation run (this run is the bake-off only):

- **Fixture-axis measurements** — MPS latency and RAM on the reference-audio fixtures
  (§2.2's fixture set). The harness is wired for it via `measure_fixture_rtf`; run it
  once OmniVoice weights are installed to replace the literature latency with measured
  numbers and to fill the excluded footprint cell.
- **Real zero-shot cloning inference** in `apps/voice-clone-tts` (not a stub), with a
  **resumable weight download** that survives an interrupted fetch.
- **LMM-V2 through-clone selftest** — synthesize a clone through a reference-audio
  fixture end to end.
- **Sidecar resilience** — a sidecar killed mid-inference leaves the gateway up with a
  typed crash reason.

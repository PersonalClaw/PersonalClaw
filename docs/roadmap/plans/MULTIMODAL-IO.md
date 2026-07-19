# Plan: Multimodal I/O — Voice Profiles, Cloning TTS, Duplex Hardening, Screen-Context Channel

**Status:** PROPOSED (created 2026-07-13 from research synthesis, promoted from backlog)
**Created:** 2026-07-13
**Wave:** 2-3 — rides LOCAL-MODEL-MANAGER-V2 (Wave 0): the cloning engine ships as a sidecar-isolated local provider using LMM-V2's execution modes, catalog contract, and capability matrix. §5 (screen share) is v2-independent but shares this plan's multimodal chat surface.
**Depends on:** LOCAL-MODEL-MANAGER-V2 (sidecar runner §3, `CapabilityMatrix` §2.1, `catalog.json` §2.3, selftest §6). Nothing here blocks or is blocked by the WORKFLOWS-V2 program.
**Scope:** promote voice from a piper-voice-name string to a first-class `voice_profiles` entity (clone/design kinds, lock-from-history, consent-as-provenance); a cloning-capable TTS engine evaluated as a provider beside piper; per-channel/per-subagent voice bindings with an explicit precedence chain; a duplex-loop hardening pack (confirmation gating, echo filter, STT mute, pre-TTS cleaning); and an opt-in ephemeral screen-share context source for interactive chat — observation only, nothing persisted unless pinned.

---

## Research Integration (2026-07-13)

- **NEW-9** (voice_profiles entity: kind clone|design, ref_audio+ref_text, seed, lock-from-history, consent-as-provenance; `supports_cloning`/`supports_voice_design` capability flags; cloning engine beside piper; per-channel/per-subagent voice bindings with explicit > binding > default > built-in precedence) → §1, §2, §3. Sources: `omnivoice-studio` (entity schema, lock flow, consent columns, MCP client-binding precedence — all verified in OmniVoice source), `moss-audio` (capability-matrix reinforcement).
- **NEW-9 amendment** (voice duplex-loop hardening pack: confirmation-phrase execution gating, TTS-echo filter with 3-consecutive-word match, STT mute during TTS playback via start/stop callbacks, pre-TTS text cleaning stripping code/URLs/paths/flags, voice-origin "transcription may be inaccurate" disclaimer) → §4. Source: `agenticseek` (working implementation of every mechanism).
- **NEW-28** (opt-in ephemeral screen-share as chat context — observation only, no control, visible indicator, nothing persisted unless pinned; explicitly DISTINCT from the approved AUTO-R20 `observe` trigger kind) → §5. Source: `tryfriday`.
- **Overlap honored:** NEW-9's LocalModel contract extensions land as *additions to* approved LOCAL-MODEL-MANAGER-V2 mechanisms (NEW-8), never as parallel machinery — §2 states each extension against the LMM-V2 section it rides. NEW-28 references approved **AUTO-R20** (WORKFLOWS-V2-AUTOMATION-SUBSTRATE §1.2 `observe` trigger kind: background capture daemon → idle filter → batched extraction, Phase 2, app-delivered); this plan builds ONLY the live interactive-session remainder (§5.1 states the boundary). The `.ovsvoice`-style portable signed bundle is deliberately **deferred to NEW-12** (Agent Packs & Portable Bundles) — export/import of voice profiles belongs to the general bundle format, not a one-off (§7).

---

## Overview

PersonalClaw already has a working but *minimal* voice surface, and no screen-context surface at all. Verified starting points (code read 2026-07-13):

- **TTS inference axis:** `tts/provider.py:28 TtsProvider` — `synthesize(text, voice="", output_path="", *, speed=1.0, **opts)`; `LocalTtsProvider` (:75) mixes in `LocalModelProvider` and bridges downloadable voices to the uniform local-model contract (`list_voices → list_models`). Piper subclasses `LocalTtsProvider`; OpenAI TTS subclasses plain `TtsProvider`. The two axes (inference vs management) are independent by design.
- **Voice resolution today is a flat triple, not an entity:** `tts/registry.py:113 active_voice_params()` resolves `{provider, voice, speed, speech_voice, enabled, auto_speak}` from `active_models.json` `tts` binding + `~/.personalclaw/extensions/use_case_settings/tts.json`. `voice` is a bare string (a piper `.onnx` name or a hosted persona id). There is no seed, no reference audio, no history, no per-surface variation — the exact gap NEW-9 names.
- **Synthesis endpoint:** `dashboard/chat_voice.py:api_voice_synthesize` (`POST /api/voice/synthesize`, wired at `dashboard/server.py:693`) — sentence-chunked streaming via `voice_reply.streaming_voice_reply`, broadcasting `voice_chunk`/`voice_complete` WS events. It already runs `redact_exfiltration_urls` + `redact_credentials` on the text — the pre-TTS cleaning pass (§4.3) extends this exact spot.
- **STT endpoint:** `POST /api/stt/transcribe` (`dashboard/handlers/core.py:83`, route `dashboard/server.py:474`). The FE mic drives it; there is no duplex loop discipline (no echo filter, no mute-during-playback, no confirmation gating).
- **Name collision to avoid:** `AgentConfig.voice` (`config/loader.py:813`) is the agent's *persona text* rendered through the `agent-voice-layer` prompt snippet (`_compose_voice`, loader.py:111) — it has nothing to do with TTS. This plan's entity is named `voice_profiles` and the binding store `voice_bindings.json`; no field named bare `voice` is added anywhere near config to keep the two axes unconfusable.
- **Vision path for screen frames:** the `image_modality` use-case exists and resolves through the standard bridge (`providers/use_cases.py:39`; `provider_bridge.py:34 _CAPABILITY_TO_ENUM` maps it to VISION). The knowledge pipeline already consumes it (`knowledge/pipeline/graphs.py:61`). Chat attachments are injected per-turn by `chat_runner.py:646 _inject_attachment_content` (uploads-dir files only) — the screen-frame injection seam (§5.3) is a sibling of this function, not a fork of the chat loop.
- **What LMM-V2 gives us to ride:** sidecar execution mode (`ProviderConfig.execution: "sidecar"`, LMM-V2 §3) for the torch-heavy cloning engine; `CapabilityMatrix` on `LocalModel` (LMM-V2 §2.1) to carry the new flags; `catalog.json` model cards (§2.3) for the engine's models; the real-inference selftest (§6) to prove cloning actually runs.

**Soul guardrail:** one user, one machine. Voice cloning here is *your own voice or voices you have reference audio for*, on your hardware, with consent recorded as provenance — no gallery, no sharing service, no watermark infrastructure (that belongs with NEW-12's bundle format if profiles ever ship off-machine). Screen share is your screen, to your configured model, with a visible indicator, and it evaporates when you stop. Learning derived from either surface stays propose-don't-write (LEARNING-FLYWHEEL's queue).

---

## 1. The `voice_profiles` Entity

### 1.1 Schema and store

Per-entity JSON files, following the tasks pattern (recon: persistence-security — per-entity JSON + `atomic_write`, no sqlite for entity families):

```
~/.personalclaw/voice_profiles/
  vp-<8hex>.json                 # the profile record
  vp-<8hex>/ref_audio.<ext>      # reference clip (clone kind)
  vp-<8hex>/locked.wav           # lock-from-history artifact (§1.2)
  vp-<8hex>/consent.<ext>        # consent recording (§1.3)
  vp-<8hex>/history/<n>.wav      # bounded generation history (last 10, LRU)
```

```python
@dataclass
class VoiceProfile:
    id: str                      # vp-<8hex>, server-generated
    name: str
    kind: str                    # "clone" | "design"
    provider: str                # app name of the engine that renders it ("piper-tts", "omnivoice-tts")
    model: str                   # engine voice/model id (piper .onnx name, engine model id)
    # clone kind:
    ref_audio: str = ""          # relative path inside the profile dir
    ref_text: str = ""           # transcript of the reference clip
    # design kind:
    design_params: dict = ...    # engine-interpreted category picks (gender/age/accent/pitch/emotion)
    instruct: str = ""           # sanitized freeform style instruction
    # shared:
    seed: int = 0                # 0 = unseeded
    language: str = ""
    speed: float = 1.0
    locked: bool = False         # §1.2
    # consent-as-provenance (§1.3):
    verified_own_voice: bool = False
    consent_text: str = ""
    consent_audio: str = ""      # relative path; verification requires the artifact
    consent_recorded_at: str = ""
    created_at: str = ""
```

- **Kind semantics:** `clone` profiles carry `ref_audio + ref_text` and require a provider whose capability matrix declares `supports_cloning` (§2.1); `design` profiles carry `design_params + instruct` and require `supports_voice_design`. A plain piper voice is representable too: `kind: "design"` with empty params — every existing voice selection migrates losslessly (§6 migration).
- **CRUD surface:** `GET/POST /api/voice/profiles`, `GET/PUT/DELETE /api/voice/profiles/{id}`, multipart ref-audio upload riding the existing resumable-upload store (`dashboard/handlers/uploads.py`) with `target: voice_profile`. Profile ids validated `[A-Za-z0-9_-]{1,64}`-style with symlink-resolved containment under the profiles dir (OmniVoice's CWE-22 idiom; same posture as the uploads store).
- **Entity events:** profile mutations broadcast typed WS events (`voice_profile_created/updated/locked/deleted`) via `DashboardState.broadcast_ws` — the same single-surface eventing chat uses today. When AUTOMATION-SUBSTRATE's entity-CRUD bus lands, these become bus events for free (its Phase 1 already plans entity-CRUD sources).

### 1.2 Lock-from-history — freeze the voice you liked

Every synthesis routed through a profile appends `{path, seed, text_hash, created_at}` to the profile's bounded generation history (last 10, files LRU-pruned). `POST /api/voice/profiles/{id}/lock {history_index}` copies that generation's audio to `locked.wav`, pins its `seed` into the profile, and sets `locked: true`; unlock clears both. While locked, the resolver (§3) always passes the pinned seed, and for clone-capable engines the locked audio becomes the reference-conditioning input — "the voice I got on Tuesday" becomes reproducible instead of a diffusion lottery. This is OmniVoice's verified lock flow (`POST /profiles/{id}/lock` copying a `generation_history` item + pinning seed) adapted to file-store PClaw.

### 1.3 Consent-as-provenance

Copied from OmniVoice's verified design, scoped down to personal use:

- Consent columns are **provenance, not biometrics**: `verified_own_voice` may only flip true when an actual consent recording exists (≥1s audio artifact AND non-empty `consent_text`) — recomputed from the files on read, never trusted from a hand-edited JSON flag (the non-forgeable rule).
- **Gating rule:** consent gates only *agentic and off-machine* features — a subagent/channel binding (§3) to a clone-kind profile of a real person's voice warns without `verified_own_voice`; a future NEW-12 bundle export of a clone profile *requires* it. **Plain local synthesis is never gated** — the user typing "read this aloud" on their own machine is not an ethics checkpoint.
- Consent record/revoke are explicit endpoints (`POST /api/voice/profiles/{id}/consent`, `DELETE …/consent`), SEL-audited (`sel.py`) like other trust-relevant transitions.

---

## 2. Provider Capability Flags + a Cloning Engine Beside Piper

### 2.1 `supports_cloning` / `supports_voice_design` — where they live

Two homes, both additive, honoring the LMM-V2 contract it rides:

1. **Per-model:** LMM-V2's `CapabilityMatrix` (LOCAL-MODEL-MANAGER-V2 §2.1, `local_models/provider.py`) gains two fields: `supports_cloning: bool = False`, `supports_voice_design: bool = False`. They ride `catalog.json` model cards and `GET /api/models/available` into the FE exactly as LMM-V2 specifies — no new payload plumbing.
2. **Per-provider (inference axis):** `TtsProvider` (`tts/provider.py:28`) gains non-abstract class attrs `supports_cloning: bool = False`, `supports_voice_design: bool = False`, plus one optional kwarg surface: `synthesize(..., ref_audio: str = "", ref_text: str = "", seed: int = 0, instruct: str = "", design_params: dict | None = None)` — all defaulted, threading through the existing `**opts` convention so piper/OpenAI implementations compile unchanged. Features gate on the flags instead of silently ignoring params (the OmniVoice rule: capability flags, not silent fallbacks) — a clone-kind profile bound to a non-cloning provider is a 409 with a typed reason (`cloning_unsupported:<provider>`), never a wrong-voice synthesis.

### 2.2 The cloning engine — one new model-type app

Evaluated engines (both verified in the OmniVoice source as its cloning backends): **k2-fsa OmniVoice** (default diffusion TTS, zero-shot clone from a 3–10s clip, bounded LRU of precomputed clone prompts) and **CosyVoice 3**. Session 2 runs a spike on both against the same fixture set (clip lengths, MPS latency, RAM) and ships ONE as `apps/voice-clone-tts` — the loser's evaluation notes land in the plan dir, not as a second app.

Plug-in fidelity (recon: providers.md "How a NEW provider plugs in" #2):

- App manifest `provider: {type: "model", implementation: "provider:create_provider", capabilities: ["tts"], execution: "sidecar"}` — **sidecar from day one** (LMM-V2 §3): the engine is torch-heavy diffusion, exactly the crash class sidecars exist for. Its venv, resumable install job, child-reported RSS, and generation counters are all LMM-V2 machinery consumed as-is.
- The provider class subclasses `LocalTtsProvider` (so its downloadable engine weights surface through the uniform `list_models/download_model/delete_model` contract and the LMM-V2 multi-layout probe) and declares `supports_cloning = True` (+ `supports_voice_design` if the engine's instruct mode survives the spike).
- `ModelTypeHandler` registration is untouched: duck-typed `is_local_model_provider`, registry keyed by APP name, registered into both `local_models/registry` and `tts/registry`; bundled-population survival across `refresh_providers()` (the two-population invariant) inherited from LMM-V2's regression test.
- Models declared in the app's `catalog.json` with `size_mb`, `runtime: "torch"`, `runtime_contract`, `matrix: {supports_cloning: true, …}`; the LMM-V2 selftest for this provider synthesizes a fixed phrase **through a clone reference fixture** — proving the cloning path specifically, not just model load.
- Piper is untouched and remains the default lightweight engine. `active_models.json` `tts` binding continues to work; profiles reference a provider explicitly so both engines coexist.

### 2.3 What this deliberately does NOT add

- **No new provider TYPE** — `PROVIDER_TYPES` and the `_TypeHandler` set are untouched; voice profiles are an entity + resolver, not a provider family (same stance as "no space provider type", `providers/registry.py:555`).
- **No new action providers** — nothing is added to `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`). Stated explicitly: TTS-as-a-hook-action ("speak on trigger fire") is a notification-delivery question owned by AMBIENT-SURFACES/AUTOMATION-SUBSTRATE, not smuggled in here.
- **No `.ovsvoice`-style signed bundles** — export/import defers to NEW-12's general portable-bundle format (which already absorbed the OmniVoice checklist: schema_version, non-forgeable provenance, zip-slip defense). This plan only guarantees the profile store is *bundle-able* (self-contained dir per profile, no absolute paths in records).

---

## 3. Per-Surface Voice Bindings — explicit > binding > default > built-in

### 3.1 Binding store and precedence

One JSON store, `~/.personalclaw/voice_bindings.json` (atomic_write), sibling of `active_models.json`:

```json
{ "channel:slack": "vp-a1b2c3d4",
  "channel:webui": "vp-9f8e7d6c",
  "agent:research-agent": "vp-a1b2c3d4",
  "client:claude-desktop": "vp-..." }
```

Resolution — the OmniVoice 4-level precedence chain, verbatim:

1. **explicit** — a `profile_id` passed by the caller (a chat "speak as X" affordance, a future NEW-10 `/v1/audio/speech` `voice` param) always wins;
2. **binding** — the surface key (`channel:<transport-name>` from the channel-transport registry; `agent:<slug>` from config.json `agents{}`; `client:<id>` reserved for NEW-10's inbound API — the key namespace is defined now so the inbound API plugs in without a migration);
3. **default** — the user's default profile (`default` key in the same store);
4. **built-in** — today's behavior exactly: `active_voice_params()`'s flat resolution from the `tts` binding + use-case settings.

### 3.2 Where resolution hooks

`tts/registry.py:active_voice_params()` gains an optional `surface: str = ""` parameter and becomes profile-aware: it walks the precedence chain, loads the winning `VoiceProfile`, and returns the same dict shape **plus** `{profile_id, ref_audio, ref_text, seed, instruct, design_params}` — callers that ignore the new keys keep working (`dashboard/chat_voice.py` passes them into `streaming_voice_reply`, which forwards to `synthesize(**opts)`). No caller is *forced* to know about profiles; level-4 fallback means an empty profiles store reproduces today's behavior bit-for-bit.

Surface identity per caller: `api_voice_synthesize` derives it from the session's origin (webui sessions → `channel:webui`; a Slack-origin session → `channel:slack`); subagent-attributed speech passes `agent:<slug>`. Settings → Voice grows a bindings table (surface rows × profile picker) beside the existing speed/persona fields in `VoicePanel.tsx`.

---

## 4. Duplex-Loop Hardening Pack

All five mechanisms are verified working code in agenticSeek; PClaw's split is that STT capture lives in the **browser** (FE mic → `POST /api/stt/transcribe`) while TTS is server-rendered and FE-played (`voice_chunk` WS events) — so the pack lands as one shared backend module + one FE hook, not a monolithic loop.

New module `voice/duplex.py` (pure functions, unit-testable, no I/O):

### 4.1 Confirmation-phrase execution gating

In continuous-listen mode (the FE's hands-free toggle), transcript chunks **accumulate** without firing a turn until a confirmation phrase matches ("do it", "go ahead", "send it", "execute" — configurable list); exit phrases ("cancel", "never mind") clear the buffer. `is_confirmation(text)` / `is_exit(text)` live in `duplex.py`; the accumulation buffer is FE state (it owns the mic). Push-to-talk and typed input are unaffected — gating applies only to the hands-free path, where a half-finished thought must not become an executed instruction.

### 4.2 TTS-echo filter + STT mute during playback

Two layers, both agenticSeek-proven:

- **Mute (FE):** the voice playback hook exposes start/stop callbacks; while `voice_chunk` audio plays, the FE suspends recognition and drops queued mic buffers (drain + reset on resume). This kills most echo for free.
- **Echo filter (BE, defense-in-depth for speaker bleed):** `is_echo(transcript, last_tts_text) -> bool` — drop any transcript sharing **≥3 consecutive words** with the last TTS output, checked both directions. `api_voice_synthesize` records the last spoken text per session (in-memory, bounded); `api_stt_transcribe` consults it when the request is flagged `duplex: true`. A filtered transcript returns `{text: "", filtered: "echo"}` so the FE shows *why* nothing happened instead of looking deaf.

### 4.3 Pre-TTS text cleaning

`clean_for_speech(text) -> str` in `duplex.py`, applied in `api_voice_synthesize` **after** the existing `redact_exfiltration_urls`/`redact_credentials` calls (the seam already exists at `chat_voice.py:50-51`): strip fenced/inline code, reduce URLs to their domain, reduce file paths to the filename, drop CLI flags, and trim markdown decoration. Applied only on the synthesis path — the chat transcript keeps the full text; only the *audio* is cleaned.

### 4.4 Voice-origin disclaimer

Turns whose input arrived via STT get one appended line — "(Transcribed from voice; transcription may be inaccurate.)" — before entering the chat runner, so the model self-corrects on garbled homophones instead of confidently misreading them. Applied at the transcribe handler's return (the FE includes it when submitting), flagged in the session JSONL metadata as `input_origin: voice` for honest history.

### 4.5 Config

One new `VoiceConfig` section (dataclass beside `DashboardConfig`), wired through the FOUR points (recon gotcha #1): (a) fields with `_meta(label, help)` — `confirmation_phrases: list[str]`, `exit_phrases: list[str]`, `echo_filter_enabled: bool = True`, `duplex_mute_enabled: bool = True`, `clean_for_speech_enabled: bool = True`, `voice_disclaimer_enabled: bool = True`; (b) `AppConfig.load()` explicit mapping; (c) `to_dict()` new top-level section; (d) `_EDITABLE_CONFIG` PATCH paths + `VoicePanel.tsx` fields for all six. Guard-class note: the four booleans are convenience features, not safety guards — plain defaults, no fail-safe-parsing ceremony needed (contrast AUTONOMY-GUARDRAILS §5).

---

## 5. Screen-Context Observation Channel (NEW-28)

### 5.1 Boundary vs AUTO-R20 — stated once, structurally

Approved **AUTO-R20** (WORKFLOWS-V2-AUTOMATION-SUBSTRATE §1.2, `observe` trigger kind, Phase 2, app-delivered) is the *background* half: a capture daemon, idle filter, frame accumulator, batched typed extraction into memory/knowledge/tasks. This section builds ONLY the *live interactive* remainder: the user, in a chat session, presses "Share screen"; the assistant sees what they see for the duration; nothing runs when the session isn't looking. No daemon, no scheduler, no extraction pipeline, no trigger kind — a session-scoped context source. If AUTO-R20 ships later, the two share nothing but the vision use-case; they are deliberately separate machines (a trigger vs a chat affordance).

### 5.2 Capture — FE-owned, indicator-honest

- The composer gains a screen-share toggle (visible only when `dashboard.screen_share_enabled` is on — the opt-in master switch, OFF by default). Toggling on calls `navigator.mediaDevices.getDisplayMedia` — the **browser's own picker and its native "sharing this screen" indicator** do the consent + visibility work; PClaw adds its own persistent in-app banner (chat header chip, pulsing) for the duration. Explicit stop: the chip, the browser's stop button, or ending the session — all three tear down the stream.
- Frames are sampled client-side: downscale to ≤1568px long edge, JPEG-encode, and capture **on send** (the frame accompanying a user turn) plus an optional "Look again" affordance mid-conversation. No continuous streaming to the backend — personal-scale means the model sees the screen when addressed, not a 30fps firehose.

### 5.3 Injection — a sibling of the attachment seam

- `POST /api/chat/screen-frame {session, frame_b64}` stages the frame in an **in-memory per-session slot** (latest-wins, one frame, never written to disk). The chat runner, at the same point `_inject_attachment_content` (`chat_runner.py:646`) runs, drains the slot and attaches the frame as an image content part on that turn.
- **Model routing:** the frame goes solely to the configured model provider. If the session's bound chat model declares vision (the `image_modality`/VISION capability the bridge already maps, `provider_bridge.py:34`), the image part rides the normal turn. If not, degrade honestly (the platform's machine-readable-degradation idiom): a one-shot `resolve_provider_for_use_case("image_modality")` describe call converts the frame to fenced text — `fence_untrusted(description, source="screen-share")` — injected instead, with the turn annotated `screen_context: described` so the user knows the model read a description, not pixels. No vision-capable binding at all → the toggle renders disabled with the reason ("bind a vision model in Settings → Models").
- **Untrusted-content posture:** a screen frame can contain hostile text (a webpage telling the assistant to exfiltrate). The described-text path is fenced (above); for the native-image path the turn carries a system-side note that on-screen instructions are content, not commands — same doctrine as inbox fencing, applied to the one surface where fencing-by-markup can't wrap pixels.

### 5.4 Ephemerality and pinning

- **Nothing persists by default:** the frame slot is in-memory; frames are not written to uploads, sessions JSONL stores only a `screen_context: true` marker on the turn (never the image bytes); ending share clears the slot.
- **Pin = deliberate promotion:** a "Pin frame" affordance on any screen-context turn writes that frame to the uploads dir through the normal upload store (becoming an ordinary attachment, visible in the file panel, session-scoped). From there, adding it to **knowledge** is the user's ordinary explicit ingest action — pinned screenshots are user items and belong to knowledge.db *only when the user sends them there*; nothing about screen share touches memory.db ever (memory = harness mechanics; a screenshot is not harness mechanics — boundary honored by construction, since the only write path is the uploads store).
- **Restrictions honored:** incognito/temporary sessions (`session_restrictions.py`) disable pinning (writes suppressed is the incognito contract); share itself still works — observation is a read.

### 5.5 Config

`dashboard.screen_share_enabled: bool = False` on the existing `DashboardConfig` — four wiring points as ever: `_meta` on the field, `AppConfig.load()` mapping, `to_dict()` (existing section, so the per-field mapping is the work), `_EDITABLE_CONFIG` path `dashboard.screen_share_enabled` + a toggle in Settings. SEL-audit share start/stop events (trust-relevant, cheap).

---

## 6. Migration & Compatibility

- **Zero-profile behavior is today's behavior:** precedence level 4 (§3.1) delegates to the current flat resolution; no profile, binding, or config write is required after upgrade.
- **Optional one-click migration:** Settings → Voice offers "Create a profile from my current voice" — synthesizing a `design`-kind profile from the active `tts` binding + speed/persona settings and making it the default. Never automatic (a silent migration that renames the user's voice selection is exactly the config-migration surprise class to avoid).
- **`use_case_settings/tts.json` keeps its role** for provider-agnostic behavior (`enabled`, `auto_speak`); `speed`/`speech_voice` remain the built-in-tier values and are shadowed by profile fields when a profile resolves.

---

## 7. Disposition & Dependency Notes

| Item | Disposition |
|---|---|
| `TtsProvider` / `LocalTtsProvider` ABCs | **EXTENDED, additive** — capability class attrs + defaulted synthesize kwargs; existing implementations compile unchanged |
| `tts/registry.py:active_voice_params` | **EXTENDED** — optional `surface` param + profile-aware resolution; dict shape is a superset |
| `dashboard/chat_voice.py` | **EXTENDED** — clean_for_speech after the existing redaction calls; last-TTS-text recording; surface derivation |
| `active_models.json` `tts` binding | **KEPT** — built-in precedence tier; profiles reference providers explicitly on top |
| `AgentConfig.voice` (persona text) | **UNTOUCHED** — different axis; naming discipline keeps them apart |
| Cloning engine app | **NEW** `apps/voice-clone-tts` (model type, sidecar) — rides LMM-V2 §3/§2.3/§6 wholesale |
| `CapabilityMatrix` (LMM-V2 §2.1) | **EXTENDED by two fields** — coordinated with the LMM-V2 sessions (land after its Session 1) |
| Portable voice bundles | **DEFERRED to NEW-12** — profile dirs are self-contained so the future exporter needs no schema change here |
| Speak-on-trigger / TTS actions | **DEFERRED** to AMBIENT-SURFACES / AUTOMATION-SUBSTRATE delivery contract — no `ALLOWED_HOOK_PROVIDERS` change here |
| Background screen observation | **NOT THIS PLAN** — AUTO-R20 owns it (Phase 2, app-delivered); §5 is the live-session remainder only |
| OpenAI-compat `/v1/audio/*` + per-client-id bindings | **NEW-10's** — §3.1 reserves the `client:` binding namespace so it plugs in cleanly |
| Snapshot/portability coverage | `voice_profiles/` + `voice_bindings.json` join the known-partial-coverage list (persistence recon gotcha #10); noted, not fixed here |

**Sequencing:** Session 1-2 need LMM-V2's `CapabilityMatrix` + sidecar runner on main (its Sessions 1 & 4). §4 and §5 have no LMM-V2 dependency and can land any time — hence Wave 2-3 with early slices pullable forward if LMM-V2 slips.

---

## Implementation Effort

**~5 sessions, Wave 2-3:**

- **Session 1 — entity + resolver:** `VoiceProfile` store (CRUD, path containment, ref-audio upload target), lock-from-history, consent record/verify/revoke with SEL audit; `voice_bindings.json` + 4-level precedence in `active_voice_params(surface=…)`; zero-profile fallback regression test.
- **Session 2 — cloning engine:** OmniVoice-vs-CosyVoice spike on fixtures; ship `apps/voice-clone-tts` as a sidecar model app with `catalog.json`, capability-matrix flags, clone-path selftest; `TtsProvider` flag/kwarg extension; typed `cloning_unsupported` refusal.
- **Session 3 — duplex pack:** `voice/duplex.py` (confirmation/exit matching, `is_echo`, `clean_for_speech`) + unit suite; FE hands-free accumulation + mute-during-playback hook; transcribe-handler echo consult + disclaimer + `input_origin` metadata; `VoiceConfig` through all four wiring points + `VoicePanel.tsx`.
- **Session 4 — screen-context channel:** FE toggle + getDisplayMedia capture + banner chip + frame-on-send; `POST /api/chat/screen-frame` in-memory slot; chat-runner injection beside `_inject_attachment_content` with vision/described routing + fencing; pin-to-uploads; incognito guard; `dashboard.screen_share_enabled` wiring.
- **Session 5 — bindings UI + validation sweep:** Settings → Voice bindings table + profile manager UI (create/clone/design/lock/consent flows); one-click migration affordance; as-a-user validation of the full matrix (profile CRUD × lock × both engines × per-surface bindings × duplex behaviors × screen share on both vision and non-vision models), deep-mutation style per the validation method.

---

## Risks

| Risk | Mitigation |
|---|---|
| Cloning engine too heavy for typical hardware (diffusion TTS latency/RAM) | Sidecar isolation caps blast radius; piper stays the default; the spike (Session 2) measures MPS latency before committing; profiles degrade to a design-kind rendering on piper if the engine is absent (typed reason, not silence) |
| Voice cloning misuse concerns | Consent-as-provenance with non-forgeable verification; agentic/binding features warn unverified; local-only scope (no sharing surface exists until NEW-12, which requires verification to export) |
| Echo filter false-positives eating real user speech (user quotes the assistant) | 3-consecutive-word threshold (agenticSeek-proven), duplex-flagged requests only, `filtered: "echo"` surfaced in the FE so the drop is visible, config off-switch |
| Confirmation gating feels laggy / user forgets the phrase | Applies only to hands-free mode; buffer contents rendered live in the composer so accumulated text is visible; phrases configurable |
| Screen frames leaking to disk via logs/history | Structural: the only frame container is the in-memory slot + explicit pin path; session JSONL stores a boolean marker; a test asserts no image bytes in session files after a share turn |
| Prompt injection via on-screen content | Described-path fenced via `fence_untrusted(source="screen-share")`; native-image path carries the content-not-commands note; share is observation-only so there is no action surface to hijack directly |
| Non-vision chat model bound → confusing behavior | Honest degradation: described-text substitution annotated `screen_context: described`; toggle disabled with reason when nothing can resolve `image_modality` |
| Profile/binding stores drift from LMM-V2's capability payloads | Flags live in ONE place (`CapabilityMatrix`) and are read, never copied; binding validation re-checks the flag at synthesis time, not only at bind time |
| Name confusion with `AgentConfig.voice` persona field | Naming discipline (§ Overview) + a docstring cross-reference on both; no bare `voice` config key added |

---

## Success Criteria

1. Create a clone-kind profile from a 5s reference clip, synthesize through the cloning engine, hit "lock" on a liked generation from history — subsequent syntheses are seed-pinned and audibly consistent; unlock restores variation.
2. A clone profile of someone else's voice binds to a subagent only past a consent warning; recording consent (real audio + text) flips `verified_own_voice` — and hand-editing the JSON flag alone does NOT (recomputed from artifacts).
3. Bind profile A to `channel:webui` and profile B to `agent:research-agent`; an explicit `profile_id` on a synthesize call beats both; deleting all profiles reproduces today's flat piper behavior exactly (level-4 fallback, regression-tested).
4. Kill the cloning sidecar mid-synthesis: the gateway survives, the caller gets the typed sidecar-crash reason, piper-bound surfaces are unaffected (LMM-V2 isolation inherited, proven on this engine).
5. In hands-free mode, dictating a request does nothing until "go ahead"; the assistant's own spoken reply never re-enters as input (mute + echo filter, verified with speaker audio); a code-and-URL-heavy answer is spoken without reading a URL aloud while the transcript shows full text.
6. A voice-originated turn carries the disclaimer + `input_origin: voice` metadata; a homophone-garbled transcription visibly self-corrects.
7. With screen share active: the browser indicator AND the in-app chip both show; a sent message answers a question about what's on screen (vision model) or about its fenced description (non-vision model, annotated); stopping share + inspecting `~/.personalclaw` finds zero frame bytes anywhere.
8. Pinning a frame produces an ordinary uploads attachment; an incognito session refuses the pin; nothing from either surface ever appears in `memory.db`, and knowledge ingestion of a pinned frame happens only via the user's explicit action.
9. All new config fields round-trip through load/save/PATCH (four-wiring-points verified by the schema reachability tests) and every toggle changes live behavior as-a-user.
10. `ALLOWED_HOOK_PROVIDERS`, `PROVIDER_TYPES`, and the type-handler set are byte-identical before and after this plan lands (no accidental provider-family creep — asserted in review).

# MULTIMODAL-IO — atomic plans

**Source plan:** [`MULTIMODAL-IO`](../plans/MULTIMODAL-IO.md)  
**Code:** `MI`  
**Source status:** proposed

5 atoms along the plan's own session seams. MI-1 (entity+resolver), MI-3 (duplex), MI-4 (screen-context) are independent and buildable now. MI-2 (cloning engine) carries the sole cross-plan edge on LOCAL-MODEL-MANAGER-V2. MI-5 is the UI + full-matrix validation capstone depending on MI-1..MI-4. MI-1 (entity+resolver) and MI-3 (duplex) are DONE; MI-2, MI-4, MI-5 remain.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `MI-1` | ✅ | voice_profiles entity store + resolver (CRUD, lock-from-history, consent-as-provenance, per-surface bindings + 4-level precedence) | — | voice_profiles CRUD + typed WS events + resumable ref-audio upload (target: voice_profile) + symlink-contained ids work; lock/unlock pins seed + locked.wav from bounded history; consent record/verify/revoke SEL-audited with verified_own_voice recomputed from artifacts (hand-edited JSON flag alone does NOT flip it); active_voice_params(surface=) walks the 4-level chain returning the superset dict; deleting all profiles reproduces today's flat piper output (zero-profile regression test green) |
| `MI-2` | ⬜ | Cloning-capable TTS engine app beside piper + capability flags (supports_cloning/supports_voice_design) + typed refusal | `MI-1`, `EXT:LOCAL-MODEL-MANAGER-V2:sidecar runner + CapabilityMatrix + catalog.json + real-inference selftest on main` | OmniVoice-vs-CosyVoice spike run on fixtures with the loser's notes in the plan dir; apps/voice-clone-tts ships as a sidecar LocalTtsProvider app with catalog.json cards (runtime torch, matrix flags), declaring supports_cloning; CapabilityMatrix + TtsProvider gain supports_cloning/supports_voice_design plus defaulted ref_audio/ref_text/seed/instruct/design_params kwargs (piper/OpenAI compile unchanged); a clone-kind profile bound to a non-cloning provider returns 409 cloning_unsupported:<provider>; LMM-V2 selftest synthesizes through a clone reference fixture; killing the sidecar mid-synthesis leaves the gateway up with a typed crash reason |
| `MI-3` | ✅ | Duplex-loop hardening pack (confirmation gating, echo filter + STT mute, pre-TTS cleaning, voice disclaimer, VoiceConfig) | — | voice/duplex.py pure functions (is_confirmation/is_exit/is_echo 3-consecutive-word/clean_for_speech) with unit suite; FE hands-free transcript accumulation + mute-during-playback hook draining mic buffers; api_voice_synthesize records last-TTS-text and cleans after redact calls; api_stt_transcribe consults echo on duplex:true returning {text:'',filtered:'echo'}; voice turns carry the disclaimer + input_origin:voice in session JSONL; VoiceConfig six fields round-trip through dataclass/_meta, load(), to_dict(), _EDITABLE_CONFIG PATCH + VoicePanel.tsx controls |
| `MI-4` | ⬜ | Screen-context observation channel — opt-in ephemeral screen share into interactive chat (§5 live-session remainder only) | — | composer screen-share toggle gated by dashboard.screen_share_enabled (OFF by default) with in-app pulsing chip + browser indicator both showing; POST /api/chat/screen-frame stages a latest-wins in-memory per-session frame; chat runner drains it as an image part for vision models, else one-shot describe → fence_untrusted(source='screen-share') with turn annotated screen_context:described, toggle disabled with reason when no vision binding resolves; Pin writes through the uploads store (suppressed in incognito); test asserts zero image bytes anywhere under ~/.personalclaw after a share turn; screen_share_enabled round-trips through all four wiring points + Settings toggle |
| `MI-5` | ⬜ | Voice bindings + profile-manager UI, one-click migration, and full-matrix as-a-user validation sweep | `MI-1`, `MI-2`, `MI-3`, `MI-4` | Settings → Voice shows a bindings table and profile manager driving MI-1 CRUD + MI-2 engine end-to-end from the UI; one-click migration synthesizes a design-kind profile from the active tts binding and sets it default (only on explicit action); full-matrix as-a-user validation passes across profile CRUD × lock × both engines × per-surface bindings × duplex behaviors × screen share on vision and non-vision models; ALLOWED_HOOK_PROVIDERS, PROVIDER_TYPES, and the type-handler set assert byte-identical before/after |

## Atom scopes

### `MI-1` — voice_profiles entity store + resolver (CRUD, lock-from-history, consent-as-provenance, per-surface bindings + 4-level precedence)

**Status:** done

§1 The voice_profiles Entity (§1.1 schema/per-entity JSON store + path containment + ref-audio upload target; §1.2 lock-from-history; §1.3 consent-as-provenance, SEL-audited); §3 Per-Surface Voice Bindings (voice_bindings.json + explicit>binding>default>built-in precedence in active_voice_params(surface=)); §6 zero-profile migration fallback

**Done when:** voice_profiles CRUD + typed WS events + resumable ref-audio upload (target: voice_profile) + symlink-contained ids work; lock/unlock pins seed + locked.wav from bounded history; consent record/verify/revoke SEL-audited with verified_own_voice recomputed from artifacts (hand-edited JSON flag alone does NOT flip it); active_voice_params(surface=) walks the 4-level chain returning the superset dict; deleting all profiles reproduces today's flat piper output (zero-profile regression test green)


**DONE.** `voice/profiles.py` owns the entity (per-entity JSON + a self-contained
`vp-<8hex>/` artifact dir, `atomic_write`, 0600), `voice/bindings.py` owns
`voice_bindings.json`, and `tts/registry.active_voice_params(surface=, profile_id=)`
walks explicit > binding > default > built-in — returning a strict superset dict when a
profile wins and the pre-profile six keys byte-for-byte when none does (zero-profile
regression asserted both ways: empty store, and delete-every-profile). 15 routes under
`/api/voice/profiles|bindings|resolve` with typed WS events
(`voice_profile_created/updated/locked/deleted`); ref-audio and consent clips ride the
existing resumable upload store as `target: voice_profile` with a `ref_audio|consent`
slot and an audio-only gate.

Four rails are proven by outcome, each with a recorded falsification: (1)
`verified_own_voice` is recomputed from artifacts on every read — the consent recording
is *discovered on disk*, not read from a path field, so a hand-edited
`"verified_own_voice": true` still reads unverified; (2) ids are symlink-contained —
traversal ids are refused by pattern and every derived path is resolved against the
resolved root, so a planted `vp-escape -> /outside` is refused rather than followed (and
delete unlinks a symlink instead of recursing through it); (3) revoking consent BLOCKS
use — `GET …/audio` on a clone-kind profile answers 403 `consent_required` once the
recording is gone, and record/verify/revoke are SEL-audited with ids + verdicts only (no
consent text, no audio bytes); (4) a resumed upload is byte-identical to a single one,
and an abandoned partial is never served (no artifact, `complete` refused). History is
bounded at `HISTORY_MAX=10` records AND files, asserted with 25 appends.

DEVIATION: the conditioning keys (`ref_audio`/`seed`/`instruct`/`design_params`) are
*carried* by the resolver but not threaded into `TtsProvider.synthesize` — that kwarg
surface and its capability gating are MI-2's, and handing a reference clip to
non-cloning piper today would be exactly the silent wrong-voice synthesis §2.1 forbids.
No UI here either: the bindings table + profile manager are MI-5's declared scope.
`voice_profiles/` + `voice_bindings.json` join the state inventory (`domain=config`,
`MERGE_UNION_BY_ID` + tombstones), with `*/history` marked `derived_within` — a
generation clip is disposable render output, while the reference clip, the locked clip
and the consent recording are authoritative user content and stay covered.

### `MI-2` — Cloning-capable TTS engine app beside piper + capability flags (supports_cloning/supports_voice_design) + typed refusal

**Status:** todo

§2 Provider Capability Flags + a Cloning Engine Beside Piper (§2.1 CapabilityMatrix + TtsProvider flags & defaulted synthesize kwargs; §2.2 apps/voice-clone-tts sidecar model app from OmniVoice-vs-CosyVoice spike; §2.3 no new provider type / no hook-action creep)

**Done when:** OmniVoice-vs-CosyVoice spike run on fixtures with the loser's notes in the plan dir; apps/voice-clone-tts ships as a sidecar LocalTtsProvider app with catalog.json cards (runtime torch, matrix flags), declaring supports_cloning; CapabilityMatrix + TtsProvider gain supports_cloning/supports_voice_design plus defaulted ref_audio/ref_text/seed/instruct/design_params kwargs (piper/OpenAI compile unchanged); a clone-kind profile bound to a non-cloning provider returns 409 cloning_unsupported:<provider>; LMM-V2 selftest synthesizes through a clone reference fixture; killing the sidecar mid-synthesis leaves the gateway up with a typed crash reason

### `MI-3` — Duplex-loop hardening pack (confirmation gating, echo filter + STT mute, pre-TTS cleaning, voice disclaimer, VoiceConfig)

**Status:** done

§4 Duplex-Loop Hardening Pack (§4.1 confirmation-phrase gating; §4.2 TTS-echo filter + mute-during-playback; §4.3 clean_for_speech after existing redaction; §4.4 voice-origin disclaimer + input_origin metadata; §4.5 VoiceConfig via the four wiring points)

**Done when:** voice/duplex.py pure functions (is_confirmation/is_exit/is_echo 3-consecutive-word/clean_for_speech) with unit suite; FE hands-free transcript accumulation + mute-during-playback hook draining mic buffers; api_voice_synthesize records last-TTS-text and cleans after redact calls; api_stt_transcribe consults echo on duplex:true returning {text:'',filtered:'echo'}; voice turns carry the disclaimer + input_origin:voice in session JSONL; VoiceConfig six fields round-trip through dataclass/_meta, load(), to_dict(), _EDITABLE_CONFIG PATCH + VoicePanel.tsx controls


**DONE.** `voice/duplex.py` owns the four pure rules (tail-anchored `is_confirmation`/`is_exit`, 3-consecutive-word `is_echo`, `clean_for_speech`) with a 69-case unit suite; `web/src/ui/composer/duplex.ts` mirrors the two phrase matchers for the frontend accumulation buffer and `useMicRecorder` runs the hands-free segment loop, draining the mic on mute-during-playback. `api_voice_synthesize` cleans after redaction and records the last spoken text on `DashboardState` (bounded, in-memory); `api_stt_transcribe` consults it on `?duplex=true` and answers `{text:'',filtered:'echo'}`; a voice turn carries the disclaimer + `input_origin:voice` into the session JSONL. `VoiceConfig`'s six fields round-trip through all four wiring points with a Hands-free section in `VoicePanel.tsx`.

### `MI-4` — Screen-context observation channel — opt-in ephemeral screen share into interactive chat (§5 live-session remainder only)

**Status:** todo

§5 Screen-Context Observation Channel (§5.2 FE getDisplayMedia capture + in-app banner chip, frame-on-send; §5.3 POST /api/chat/screen-frame in-memory slot + injection beside _inject_attachment_content with vision-native vs fenced-described routing; §5.4 ephemerality + pin-to-uploads + incognito guard; §5.5 dashboard.screen_share_enabled config + SEL share start/stop)

**Done when:** composer screen-share toggle gated by dashboard.screen_share_enabled (OFF by default) with in-app pulsing chip + browser indicator both showing; POST /api/chat/screen-frame stages a latest-wins in-memory per-session frame; chat runner drains it as an image part for vision models, else one-shot describe → fence_untrusted(source='screen-share') with turn annotated screen_context:described, toggle disabled with reason when no vision binding resolves; Pin writes through the uploads store (suppressed in incognito); test asserts zero image bytes anywhere under ~/.personalclaw after a share turn; screen_share_enabled round-trips through all four wiring points + Settings toggle

### `MI-5` — Voice bindings + profile-manager UI, one-click migration, and full-matrix as-a-user validation sweep

**Status:** todo

Implementation Effort Session 5 (Settings → Voice bindings table [surface × profile picker] + profile manager UI for create/clone/design/lock/consent flows; §6 one-click 'create a profile from my current voice' migration, never automatic; deep-mutation full-matrix validation); Success Criterion 10 provider-family byte-identical assertion

**Done when:** Settings → Voice shows a bindings table and profile manager driving MI-1 CRUD + MI-2 engine end-to-end from the UI; one-click migration synthesizes a design-kind profile from the active tts binding and sets it default (only on explicit action); full-matrix as-a-user validation passes across profile CRUD × lock × both engines × per-surface bindings × duplex behaviors × screen share on vision and non-vision models; ALLOWED_HOOK_PROVIDERS, PROVIDER_TYPES, and the type-handler set assert byte-identical before/after


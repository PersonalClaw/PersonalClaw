# Changelog

All notable changes to PersonalClaw are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."

## [Unreleased]

### Added
- **Browse can now click a `<canvas>`: an opt-in vision-grounding fallback, using a vision model you pull yourself (BA-10).** The browse loop addresses elements by a stable ref derived from the DOM, which means a page whose only control is drawn on a `<canvas>`, an image-map or a WebGL surface exposed *nothing* it could act on — `extract_page` yields zero refs, so every action the model could emit named nothing and the run parked `stuck` having described a control it could not reach. A run may now opt into one extra action, `CLICK_VISION <what you want to click>`: the model says in words what it wants, a vision model grounds that description to a point on the step's own screenshot, and the loop actuates it with a located `Input.dispatchMouseEvent`. It resolves through the **existing** `image_modality` capability (Settings → Models) — no new provider, no new client, no bundled weights — so it is fully local against a model you pull, and **nothing ships in the wheel or image**. It is **off by default and never inferred**: set `vision_grounding: true` on the browse action config to enable it per task. A page with no refs does not switch it on, and a failed `CLICK <ref>` never falls back to a coordinate, mirroring the desktop driver's rule that `auto` never resolves onto a coordinate method — the automatic widening would fire exactly when the model is most wrong about the page. With no vision model bound the first `CLICK_VISION` **parks with a typed "no vision model available" reason** naming a model to pull, rather than quietly doing nothing. Recommended models are **Apache-2.0**: `qwen2.5vl:7b` (official Ollama, ~6GB) as the default, or Holo1.5-7B for grounding-heavy use. Every located click is audited under its own SEL operation, `browse.click:vision`, so a coordinate click is one filter away from every ref-addressed one. The located path is **click-only** — there is deliberately no coordinate *type*, because a field the extraction never classified is a field whose credential status nobody checked — and it **refuses a CAPTCHA or one-time-code challenge before any image reaches a model**, since a human-verification widget is itself a canvas with no addressable element and would otherwise be the first thing this path clicked.
- **"When PersonalClaw is not the right tool (yet)" — ten situations where a new reader should walk away today, and the two README claims that contradicted the code.** A limitations page is the item nobody writes and everybody needs, and the README's caveats were real but scattered across a feature table, a pre-1.0 banner and a platform matrix — so no single surface answered "is this for me". [`docs/guides/when-not-to-use-personalclaw.md`](docs/guides/when-not-to-use-personalclaw.md) answers it in ten numbered scenarios, each naming the file or the recorded decision that makes it true: no migration machinery (pre-1.0 clean breaks); no model ships, so a fully offline install needs the reader's own Ollama; single-user by construction with *"no hub in core, ever"*; the phone is the dashboard installed as a PWA, not a store app; Windows native ruled **no-go** by its own audit; the desktop shell unsigned and built from a checkout; core registering exactly one channel transport (the Web UI — Slack arrives as an app, Telegram and Discord do not exist); no search provider bundled; and the app platform *vetting* installs rather than confining them afterwards. The distinction the page is built around is **`no` versus `never`**: two items (no accounts for a second person, no hosted service) are permanent design boundaries expected to hold at 1.0, and saying so is the difference between "wait" and "use something else". A scannable ten-row version sits in the README ahead of Quickstart, so the decision comes before the install rather than after it. Two claims changed because the code disagreed: (1) the README called `ollama-models` "a **removable app** you install from the Store" — it is packaged in the wheel, seeded into the home at first boot by `seed_builtin_apps()`, and `_is_native()` refuses `disable`, `uninstall`, `uninstall_keep_data` and `force_uninstall`, so it is neither removable nor Store-installed; (2) the Python badge read `3.12+` against `requires-python = ">=3.12,<3.14"`, promising versions the package refuses to install on, and now reads `3.12 | 3.13`. The `self-hosted` badge also linked to a bare `#`, the only no-op link in the repo, and now points at the section that explains local-first. Every link and anchor on the new page is verified against the repo's own docs-lint rail, with a deliberately-broken-link control proving the rail sees the file.
- **A loop kind can now be started as a workflow run: `general` is the first (PP-16 session 1).** A "loop" and a "workflow run" have always been two names for the same thing — one work unit with two implementations — and `workflows/loop_aliases.py` already recorded which bundled template replaced each of the five loop kinds. Nothing acted on that answer: its only caller walked the table forward to name output files, so a `general` loop still became a row in `loops.db` driven by a separate watchdog. There is now a launch path (`workflows.service.start_kind_run`) that takes a legacy kind and starts the template as a real run, and a bundled template can declare its own done-ness rule in a `supervisor.convergence` block that the engine parses into the `SupervisorPolicy` it already resolves per run. `general` is the only kind ported; the other four are **refused by name** (`WF_LOOP_KIND_NOT_PORTED`) rather than silently started on a template that carries none of their behaviour, and an unknown kind is refused without guessing a template. Their existing loop-path behaviour is unchanged. A malformed `convergence` block is an authoring error with a named code (`WF_SUPERVISOR_CONVERGENCE_NOT_OBJECT`, `WF_SUPERVISOR_UNKNOWN_CONVERGENCE_FIELD`, `WF_SUPERVISOR_BAD_DONE_SIGNAL`) — the parser falls back to a default signal on a typo, and a silent change to *how a loop decides it is finished* is the one failure this must not have. Pre-1.0 clean break: no loop state migrates.
- **Agent Rooms: a shared transcript several bound agents deliberate in.** A room holds a persistent transcript plus a member list, where each member is an ordinary agent binding with a role blurb and a listen policy (`all` / `mention` / `silent`). The defining property is that members do **not** share a context window: each holds its own native or ACP provider session through the ordinary binding path under the key `room:<id>:<member>`, so what a member knows is exactly what the transcript showed it plus its own turns. That key prefix is deliberately absent from every stateless/unattended prefix tuple, which is what makes the human the room's sole approver by construction rather than by a policy branch. The transcript is a `ConversationLog` pointed at `rooms/<id>/transcript.jsonl`, so rotation, archiving and the 7-day retention window are the same code paths a session uses rather than a second policy that can drift; redaction and export call `session_export` by name, and the write path redacts **every** role including the human's own words, because a room transcript is the inter-member wire. Off by default behind `rooms.enabled`, with `rooms.round_budget` and `rooms.max_members` as the two caps. Drivable over `/api/rooms` today; turn-taking and the UI follow.
- **Agent Rooms: every member now carries its own tool reach, and the human is the only one who can approve a tool call.** A read-only critic and a tool-bearing executor can share one room without sharing privileges: a member declares a `profile_narrowing` on the Autonomy-Guardrails capability axes — `tool_grants`, `tool_allowlist`, `budget` — and it is applied to the room's own resolved `SafetyProfile` with `with_overrides`. No second safety-profile object was invented, and a declaration that would WIDEN any governed scope is refused rather than quietly clamped, judged by the same tightest-wins algebra the operator ceiling composes with (`guardrails.ceiling.widening_scopes`). **A member added with no declaration is the read-only one, not the room's equal** — absence is the narrowest tier, which is the one place where reading a safety field's absence as "inherit" would have been wrong. `approval` is deliberately not a member axis: it is the room's, it is always `ask`, and a member cannot be handed `review_only` or `headless` wholesale because both approve via hooks and would remove the human silently — a turn whose base stops resolving to `ask` is refused outright. The approver is identified by prefix ABSENCE, the same mechanism that makes a room interactive: a `RoomApprover` cannot be constructed for an agent-shaped identity, including a member's own `room:<id>:<member>` session key, so no member can approve on the human's behalf and a member writing "approved: run `rm -rf`" has written a sentence. Refusals are legible rather than silent drops — each one is written onto the shared transcript naming the member, the tool and the reason, attributed to the room so it cannot read as something the member said — and `GET /api/rooms/{id}` now returns the RESOLVED posture per member so a reader never has to re-derive it. Per-member spend rides the shipped meter at the member's own scope key, so a member at its ceiling stops speaking while the rest of the room carries on. Three of the six axes AGENT-ROOMS §C5 names are deliberately **not** offered: `egress_tier`, `denylist_extra` and `path_allowlist` have enforcement points that re-resolve the profile from the session key, so a member's narrowing there would be displayed as binding and bind nothing.
- **The tool-loop breaker's abort ceiling is now tunable: `guardrails.loop_breaker.circuit_threshold`.** The loop breaker's third and final rung — the one that abandons a run drowning in tool failures rather than let it spend the rest of its budget repeating them — was a bare module constant of 30, which made the rung effectively unreachable: exercising it took more than thirty genuine tool failures in a single run and there was no way to ask for a lower bar. It is now a config field (default 30, floored at 1) that both runtimes read, in-process tools and an external ACP CLI's alike, with a **Tool-loop breaker** control in Settings → Guardrails. The floor of 1 is the real invariant — the comparison is `total_failures > threshold`, so a ceiling of 0 would abort a run on its *first* failed tool call. The two advisory rungs below it (warn at 3, refuse at 5) stay constants, because their wording quotes the number and neither ends a run. Settings' old **Circuit breaker** section is now **Provider circuit breaker**: two different breakers are configurable here and they break different things — that one fails a model *provider* fast during an outage, this one stops one *run*.
- **A fresh install now ships a working model provider: `ollama-models` is bundled.** Before this, `pip install personalclaw` booted with **zero** registered chat providers and zero embedding providers — none of the 30 bundled apps declared a `model` type, and onboarding's own copy says *"Model provider. Required — nothing else works without one."* Reaching a working agent required network access to GitHub, a shallow clone of a **second** repository, and a manual Store install; the credential-free "bind a local Ollama" on-ramp refused with *"the 'ollama-models' app is not installed in this home and no local app source has it"*. Offline, a fresh install was a brick. `ollama-models` was chosen because it is the only chat-capable provider needing neither a credential nor a new wheel — it runs on `httpx` and `aiohttp`, which core already depends on, so `pip install personalclaw` resolves exactly the same dependency set as before — and it declares `embedding` as well as `chat`, closing both lanes with one bundle. It is bundled **through the app mechanism** and nothing else: the app registers its own provider type from its own `provider.py` via `personalclaw.sdk.model`, core's generic `ModelTypeHandler` does the rest, and core acquired no provider registration, factory or registry special case. Existing installs seed it on the next boot like any first-run seed.
- **`StructuredOutput` is exported from `personalclaw.sdk.model`.** A model app that supports server-side schema enforcement has to name the enum member, and the enum was not re-exported — so the Ollama provider reconstructed it as `type(ProviderCapability.__dataclass_fields__["structured_output"].default)`, which returns the right object at runtime but is unreadable and defeats type checking entirely. Promoting the symbol is what the native capability contract's own lint message prescribes.
- **The inert-surface baseline now detects runtime-editable config that round-trips but has no production reader.** [#465](https://github.com/PersonalClaw/PersonalClaw/issues/465). The sixth `config_reader` census kind is AST-based: dataclass declarations, `load()` mappings, serialization, allowlist literals, comments, prose strings and imported module names do not count as consumption; a config accessor counts only when code outside `config/` calls it. Its calibration reproduces the five historical Chat findings and `agent.sandbox` without being fooled by the unrelated sandbox module, label copy or comments. This remains a reference census rather than a whole-program reachability analyzer: a genuine read in an unreachable production branch clears a path.
- **A skill now says HOW it came to exist, not only which tier it lives in.** `GET /api/skills` gains `provenance` on both listing sites (global and agent-local), read from the skill's own frontmatter `source:` marker — `taught` for one promoted from a session draft, `auto` for one the extractor wrote, empty for a hand-authored skill. Two producers were already writing that marker and nothing read it: the wire's `source` field is derived purely from the directory a skill sits in, so a skill the user explicitly taught the agent rendered the same `local` chip as one dropped into that directory by hand, and there was no way to review after the fact what a session had put in the library. `provenance` is deliberately a **second** field rather than extra values folded into `source`: `source` is what decides whether the inspector offers Edit and Delete, and a taught skill is an ordinary editable `local` skill — overriding it would have locked the user out of the skill their own session just taught. The Skills list marks the row beside `always`/`tampered` and the inspector carries the sentence; a hand-authored skill renders nothing, which is most rows. The emitted vocabulary is closed to the two values the producers actually write, because frontmatter is free-form text and this value reaches a badge.
- **CSV is a generated document format, and it stores as text rather than as a binary body.** `document_create`/`sheet_create` gain `format: "csv"` via a `csv_writer` that renders one `SheetModel` sheet through the standard-library dialect, so quoting of commas, quotes and embedded newlines is the stdlib's problem and not a hand-rolled escape. It is reachable the moment it is registered: the `format` parameter carries no enum and `document_formats` answers straight from `available_formats()`, so the new writer shows up in the agent-facing list with no second wiring step. The substantive half is that CSV is the first generated format that is **not** binary, which the create path previously assumed for every format — it sent all output through `create_binary`/`update_binary` and capped it at `MAX_BINARY_CONTENT_BYTES`. A CSV taking that path would have been stored as an opaque body with no diffable draft state and measured against the wrong cap, so `_document_create` now branches on `is_binary_kind(fmt)` and routes text formats through `create`/`update` with `MAX_CONTENT_BYTES`; the size refusal quotes whichever cap actually applied. CSV refuses a multi-sheet workbook and a prose document model outright, because the format has no sheet concept and silently concatenating tables or dropping all but the first sheet is worse than saying so.
- **A shrink-only ceiling on spacing the Density slider cannot move.** `tokens.css` builds the spacing ramp as `calc(<px> * var(--space-scale))` and `[data-ui="dense"]`/`[data-ui="cli"]` re-scale that one variable to 0.8/0.68 — that is the entire mechanism behind Appearance → Density, a shipped user-facing control. Tailwind's default numeric scale (`p-6` = 1.5rem) is not wired to it and cannot be, since the `@theme` block overrides only the named rungs and no `--spacing` base. So a raw numeric spacing utility is **inert under a control the app advertises**: `py-2xl/px-m` goes 24/12px → 19.2/9.6 → 16.32/8.16 across the three levels while `py-6/px-3` stays frozen at 24/12. `web/src/design/spacingTokenRamp.test.ts` measures the tree and ratchets the half of that population which is a straight defect. Measured 2026-09-20: **4911 raw spacing utilities, 3135 (64%) landing exactly on a rung** (4/8/12/16/20/24/28px → `xs`…`3xl`, so the conversion is arithmetic and pixel-identical at the default density) and **1776 (36%) on half-steps the ramp has never had**, dominated by 6px ×996 and 2px ×510. Only the first number is a gate, shrink-only: a new `gap-2` reds it, converting one lowers it, and it may never be raised. The second is recorded, not bounded — a 6px need has no tokenised form until the ramp gains rungs, and forbidding it would push authors to a worse value, which is the owner's call and not a rail's. It ships as the **ceiling alone, with no conversion sweep**, deliberately: the number is useful the moment it exists, and landing it first means a sweep can proceed in any slices without the count drifting back up in between. The premise is asserted before the gate (the multiplier exists, all seven rungs really are `calc(px * var(--space-scale))`, and dense/cli really re-scale it), so if a future edit hard-codes a rung the rail says its own reason died instead of silently measuring nothing; the matcher carries positive and negative controls (`gap-2` caught, `gap-s` not, `-mx-1` out of scope, `grid-cols-2`/`w-4` not spacing) and a vacuity floor on the census. Both legs were confirmed failable: adding one `gap-2` reds the ratchet by name at 3136, and hard-coding `--spacing-m` reds the premise.
- **Frontend whitespace hygiene is enforced by CI instead of by whoever reads the diff.** [#1106](https://github.com/PersonalClaw/PersonalClaw/issues/1106). There is no JS/TS linter in `web/` — no eslint, prettier or biome — and adding one is explicitly declined (a large dependency, a style argument to settle, and a first run that rewrites hundreds of files, none of it worth it for whitespace). So the gap is closed the way the other 18 rails in `web/src/design/` close theirs: `whitespaceRail.test.ts` walks the tree with `node:fs` and asserts per file, no new dependency, naming every offender in the failure message so a red run says which files rather than just failing. Three checks, all three of the ones the issue asks for: a final newline on every non-empty text file, no space or tab at end of line, no CRLF. The census is `git ls-files --cached --others --exclude-standard -- web` rather than a glob over `src`, because the regression that motivated this (a contributor PR shipping two files with no trailing newline, caught only by a review round trip) is not confined to `src` — `e2e/`, `scripts/` and the workspace-root config files are hand-edited too — and `--others` is what makes a file added in the current working tree count, since a rail that only saw committed files could not fail until after the commit it exists to prevent. Binary files are dropped by a NUL test, without which the 38 PNG visual snapshots and the three woff2 fonts fail all three checks on their payload bytes. Measured on the tree it landed on: **1558 text files of 1574 paths, 0 offenders on all three checks**, so it lands green with no allowlist and no cleanup sweep — it only stops the next regression. A vacuity floor asserts the census exceeds 1000, because every other assertion is over that list and a silently collapsed census would make all three unfailable. Each check was also confirmed to FAIL on a file deliberately written to violate it, since a hygiene rail that cannot go red is worse than none. `web/.editorconfig` is the other half — it stops an editor producing the problem rather than reporting it after CI. It sits in `web/` with `root = true` deliberately, and the measurement is why: 79 prompt snippets under `src/personalclaw/config/prompt_snippets/` plus `CHANGELOG.md` end **without** a final newline and four PDF fixtures carry trailing whitespace in their payload, so a repository-root `insert_final_newline`/`trim_trailing_whitespace` would declare a convention the tree violates in 80 places and invite an editor to append bytes to prompt text that reaches a model. Python already has this enforcement (flake8 W291/W293/W391 via `make lint`), so the editor rule and the CI rule now cover exactly the same tree — neither promises more than the other proves.
- **A pack's staged roster could be deployed only by `curl`. `POST /api/packs/{name}/roster/deploy` shipped complete — route, handler, `deploy_roster`, and the `roster` rows already on the `/api/packs/installed` wire — with no control anywhere in the dashboard** (AGENT-PACKS §4.2, AP-4). Settings → Packs now shows a **Deploy roster** button on any installed pack that shipped one, and renders the result rather than only a count: the `always` tier that went live, the `phase-N`/`as-needed` members that stayed installed-but-dormant, and any `always` persona whose profile is gone from the store. Naming all three is the point — the staged-roster contract is that *only* the `always` tier is hired, so a line that said "deployed 3" would imply the whole team was, and a `missing` row is a real state (a persona deleted after install) that must be reported rather than silently dropped. The button carries its own narrow in-flight flag so it publishes `aria-busy` for exactly the action it owns while still disabling its siblings, a fresh attempt clears any prior result so a 404 can never sit beside a stale "now live" panel, and the control is hidden entirely for a pack with no roster. `web/src/pages/settings/packRosterDeploy.test.tsx` drives the rendered row: the gate, the deployed/dormant/missing split, and the toast wording for each of the three outcomes.
- **Nine config sections were PATCH-editable, backend-read and reachable from NO control in the dashboard. They have controls now — two new Settings panels and five new sections on existing ones.** [#2801](https://github.com/PersonalClaw/PersonalClaw/issues/2801) · [#752](https://github.com/PersonalClaw/PersonalClaw/issues/752). This is the inverse of the usual dead-control defect: every one of these fields was already complete on the backend — dataclass with `_meta` help, `load()`, `to_dict()`, a bounded row on the `_EDITABLE_CONFIG` PATCH allowlist, and a live reader — so the *only* missing wiring point was the one a user touches. The single write path was a raw `PATCH /api/config/personalclaw` or a hand edit of `config.json`. New **Settings → Workflows** (19 controls over the engine's allowlisted keys: the master switch, the two typed lane caps, both node timeouts, the task-claim lease, the three model tiers, surfacing mode, match threshold, fan-out and retention caps, approval lifetime, default quiet hours, default duty gate, default workspace mode and teardown) and **Settings → Autonomous loops** (all four `loops.*` — the judge model axis as a select over the six axes the save path accepts, because a loop's judge is deliberately bound off the axis its worker rides so a reviewer mistake is not correlated with the mistake it is reviewing; the stagnation window, whose floor of 2 is structural since a window of 1 can only compare a cycle with itself; check-work-after-gates; and sparse task worktrees). Five sections joined the panels that already own their mechanism rather than becoming panels of their own: **Routing & Efficiency → Router** (six `routing.*`, including the adaptive-routing master switch — a panel that looked like it configured routing could not reach the switch that turns routing on, and these six write `config.json` where the policy list beside them writes `routing_policy.json`), **Models → Local runtime** (six `local_models.*` memory-headroom and sidecar knobs, beside the loaded-models bar that one of them already governed read-only), **Security → Child process ceilings** (five `sandbox.*` — the limits between a runaway tool and the host; only the BOUNDS are editable and the copy says so, because which files may never be captured and which env names are refused are code-level floors no PATCH can widen), **AI feedback → Tuning** (four `feedback.*`, the important one being the collection kill switch, whose two halves were both built — every `/api/feedback` route 404s when it is off and the thumbs self-hide on the first 404 — with no way to throw it; retire threshold is a fractional stepper because at a step of 1 a rate in 0.1–0.9 could not express the value it displayed), and **Chat → Background compression** (the two `tools.bg_compress_*` paths). `settingsUI` gains three shared rows — `SegRow`, `SelectRow` and `TextRow` — so the row family now maps one-to-one onto `_EDITABLE_CONFIG`'s value types instead of stopping at bool/int/str_list; `TextRow` commits on Enter or an explicit Save and never per keystroke, because a per-letter PATCH would send a request per character and let the allowlist refuse a half-typed `22:0` mid-word. **Two allowlisted paths in scope deliberately got NO control** — `workflows.max_active_runs` and `routing.energy_sampling` — because they are *inert* (zero readers outside the plumbing), which is a different defect ([#465](https://github.com/PersonalClaw/PersonalClaw/issues/465)): a control over an inert knob only makes a promise the code ignores more convincing. Three rails keep this closed: `tests/test_settings_control_coverage.py` IMPORTS the allowlist rather than parsing it and requires a writer for every key in the nine sections (so a new key reds on the day it lands), re-measures each inert exemption so it cannot outlive its reason, and is calibrated by falsification in both directions — a type declaration alone and a commented-out path must NOT count, since both new panels name the paths they omit in prose beside the keys they control. `web/src/pages/settings/configSectionControls.test.tsx` and `feedbackControlsAndSavePosture.test.tsx` drive the rendered controls and assert the exact dotted path each click PATCHes plus its bounds — the failure this class invites is not a missing element but a control that renders, flips, and writes the WRONG section prefix, because the fastest way to write a settings panel is to copy the neighbour.
- **`knowledge.synthesis_window` and `knowledge.max_mentions_per_claim` now do what they say.** [#465](https://github.com/PersonalClaw/PersonalClaw/issues/465). Both were runtime-editable and read by nothing. `synthesis_window` bounds how many recent findings a long-running watcher's synthesis stage sees per cycle — the failure it exists to prevent is invisible (nothing errors; each cycle just costs more than the last until a run hits a context limit hours in), and every sibling read used the module default of 20 regardless of what you set. `longrun`'s own note even claimed the config "overrides it". A bare `| window` pipe honours the same value; an explicit `| window(N)` still overrides it. `max_mentions_per_claim` caps how many independent sources one claim accumulates: `Claim.add_mention` deduplicated by source but applied no count limit, so a claim on a high-traffic subject grew its evidence list without bound on every write. Both fall back to the safe direction when config is unreadable — the window keeps its bound, and the mention cap declines to discard provenance.
- **The contradiction judge's typed relations are now stored instead of discarded (WF2KNO-10).** `contradiction-review` spent a metered model call naming the relation between two knowledge items — `derived_from`, `depends_on`, `part_of` and the rest — and then interpolated the answer into a summary string and threw it away. The two existing `item_relations` writers are both fed from the *free* deterministic tier, whose verb is derived from the source-precedence ladder and so can only ever be `supersedes` or `contradicts`; the structural three were unreachable from any path. A new zero-token `knowledge-relate` action provider closes the loop: the judge now declares a JSON schema (without one its output stayed a raw string and the parser built for it would have read nothing on every run, forever), and a write-back node persists what it proposed through `contradiction.parse_edge_proposals` into `store.add_item_relation`. The model's answer is treated as untrusted input and narrowed at the write end — a closed five-verb vocabulary, both endpoints required to be real rows (which is what a hallucinated item id is not), confidence clamped below 1.0 so an opinion cannot outrank a deterministic proof, `provenance: inferred` on every row, at most ten edges per pass, and the justification prose reported but never stored. Its output also separates "the judge proposed nothing" from "the judge's answer could not be read", because those are opposite facts and collapsing them is how this wire stayed silently inert through several passes that each looked fine. `store.add_item_relation` now validates through `semantics.validate_relation` rather than re-implementing half of it: the two checks it duplicated are enforced once, and the two it skipped — an unknown `provenance` was stored verbatim and `confidence` was written unclamped — are now enforced at all.
- **A notification can now say WHO it is for, and one addressed to somebody else is visible here but fired nowhere here (`TSE2-5`).** `DashboardState.notify` is "THE single delivery choke point for every emitter", and it had two policy layers — the global gate (*should this be delivered*) and the per-kind rule (*how loudly*) — with no way to express *to whom*, so every note reached **this** dashboard by construction. That was correct while every row in every store was the owner's, and stopped being correct when the shared multi-owner inbox shipped: a teammate's item wanting attention fired a toast, a phone push, an OS banner and a digest line at whoever happened to be sitting here. Notes now carry an **`addressee`** — the same `owner_username` slug runs, entities and inbox items already carry, **not** a second owner vocabulary — supplied by `inbox.emit_attention_item` from the item's own owner, because the notification is a *view* of the item. A foreign-addressed note is **persisted and listed** (it appears in the bell and `GET /api/notifications`, marked `withheld_reason: foreign_addressee`) and the whole fire half is withheld: no websocket broadcast, no `push`, no `native` banner, no digest entry. This is the shipped foreign-**trigger** posture applied to the attention path rather than a second inert-ness mechanism — `triggers/ownership.py` withholds a foreign row from the ARM read while the LISTING read keeps it visible, and `is_locally_addressed` is `is_owner_authored` clause for clause (empty addressee, or no configured username, reads as the owner's — so an install with no shared source behaves exactly as it did, and every note written before this field existed still fires). The decision sits after `never` and before the three delivery modes deliberately: `digest` and `badge` are local deliveries too, and a foreign note batched into the morning digest is a foreign note fired one day late. **Delivery also became pluggable, which is what makes "addressed elsewhere" mean something other than "dropped":** the `notification` provider type was in `PROVIDER_TYPES` with a handler that said so in as many words — *"No provider declares type=notification; pluggable delivery backends remain a future design"* — running each app's factory at enable-time and discarding the result, so a backend could never be reached. It is a real handler now (`NotificationTypeHandler` → `notification_providers/registry.py`, published as `personalclaw.sdk.notification.NotificationDeliveryProvider`): a foreign-addressed note is offered to the registered backends that say they can reach its addressee, and the accepting one's name is recorded in `routed_to`. With none installed — the ordinary single-user case — `routed_to` is `""` and the note is simply visible-but-inert, because "nobody could reach them" must never read as "delivered". Core still learns nothing about how to reach another person; the transport belongs to the app. One generator bug fell out of making the type real: `personalclaw app new --type notification` emitted a stub whose identity property returned `""` (the scaffold's name-property allowlist is matched by exact spelling and did not know `delivery_name`), so the handler refused the app it had just generated — measured, fixed, and the scaffolded provider now registers.
- **`sharing_policy: shared` knowledge now actually goes somewhere: the provider contract gains its outbound half (TSE2-4).** WORK-CONTAINERS shipped `sharing_policy` as a *cross-container* filter, so a `shared` item showed up in another of your own projects and nowhere else — there was no `push` anywhere in `knowledge_providers/`, so a knowledge provider could only ever receive. Now `KnowledgeProvider.push(item)` is the mirror of `ingest`, and `knowledge-persist` — the only writer of the policy — offers each `shared` item to every registered provider and reports which accepted in its `shared_to` output. The gate lives in one place (`knowledge.sharing.push_shared_item`): a `private` item is never offered to a provider at all, so no provider can widen the default, and a provider with no outbound half **declines** (the ABC returns `None`) rather than being counted as a delivery. What crosses is an allowlist of four attribution keys — `contributor` plus `project_id`/`run_id`/`sharing_policy` — not the item's whole metadata blob, so a claim ledger and its citations stay home. Coming back, `SourceEngine` carries those same four keys onto the row it writes and files it under its container's tag, and both readers then name the teammate: the project Knowledge view shows a `from <handle>` chip, and the session brief — the one path by which knowledge reaches a run's prompt — renders the contributor **on** the existing federated-source label *inside* `fence_untrusted`. Labelled and fenced, both: the label says whose text it is, the fence is what stops the run acting on it. Only foreign contributions are labelled, per the shipped `contributor_label` bargain — a label on every line would hide the one case it exists for. A push never fails the local write: the row is the record, the push is a courtesy, and an unreachable team store logs and returns nothing shared.
- **One install of a knowledge connector can now watch MANY sources: the engine hands `poll` the source row's validated `spec` (AECO-2).** Every poll-capable provider in core is constructed with a `KnowledgeStore` handle and reads its own source row, so it has always had that row's `spec` — but an app-bundled provider reaches core only through `personalclaw.sdk.*` and holds no store, so all it ever received was a `source_id` it could not resolve. The `spec` column has been persisted and validated since WS-2 (`create_source(spec=...)`); what was missing was purely DELIVERY, and the consequence was a hard ceiling: an app's configuration could only be a per-INSTALL setting, so one install meant one watched source — a Notion connector could never watch two workspaces, and the newly-shipped `git-repo` app could never index two repositories. A provider now OPTS IN by naming `spec` on its own `poll`, and gets that source's spec re-read **at poll time** and handed over as a private object. Re-read rather than taken from the tick's snapshot on purpose: `tick()` lists every due row once and then polls them in sequence, so a row edited during a long tick would have reached a late provider stale — a core provider never sees that skew because it does its own `get_source` inside `poll`, and shipping apps the snapshot would have handed them a second-class version of the same value. `sdk.knowledge.resolve_source_spec` is the published merge: a source's spec over the app's per-install defaults, with a CLOSED key set, so a misspelled key is REFUSED when the source is saved instead of silently indexing the install default forever while the spec says otherwise. **Additive, not a break**: the negotiation is one function over one `provider.poll(...)` call site (it replaces the two shapes the engine used to branch between for `policy`), a provider that names neither extra is called exactly as before, and a source with an empty spec round-trips byte-identical items. No new network egress. Driven end to end on a real gateway: one `git-repo` install, three sources — one inheriting the install default, one naming a second repository, one naming that repository with its own `include` globs — each ingested its own tree, kept its own commit cursor at its own repo's HEAD, and a commit in one repository left the others untouched.
- **Bring your own vector store: knowledge vector search can run against your own Qdrant (or pgvector, or Chroma) instead of the built-in `sqlite-vec` index (KBVS-1).** `vector_store` is a new provider type (`sdk.vector_store.VectorStoreProvider` — four methods: `upsert`, `delete_item`, `query`, `describe`), and the first-party **Qdrant Vector Store** app fills it. Enable exactly one such app and that backend replaces `vec0` as the chunk half of hybrid retrieval's vector arm: chunk vectors are written to it as documents are ingested, it answers the nearest-neighbour query, and its hits fuse through RRF alongside the unchanged FTS5 keyword and graph arms. **Enabling the app IS the binding** — there is deliberately no second `knowledge.vector_store_provider` config field, because a name in `config.json` plus a set of enabled apps is two places to say one thing and they drift the first time an app is disabled without editing config. Two enabled at once is **refused**, logged with both names, and retrieval falls back to the built-in index: silently answering out of one of two stores the user bound is the silent-wrong-recall defect, and refusing is repairable. **What moved is narrow, on purpose.** Only the chunk-vector *search* is external. Chunk ROWS stay local — the `chunks` table keeps the text, the section/line locator, the embedding BLOB and the RET-4 fingerprint, because re-chunking, re-embedding, staleness detection and the Doctor all read them — so an unreachable store costs recall for a query, never data. Whole-document (title + summary) vectors stay local too: one vector per item, no roll-up, no index to keep in step, so externalizing them would only double the surface that can be down. And core keeps everything that turns a similarity into a rank — the `_VECTOR_MIN_SIMILARITY` floor, the MAX roll-up to the parent document, the archived/active/fingerprint liveness join and RRF — so a backend can move where the vector math happens without being able to change what a search *means*. The one thing genuinely delegated is the cosine: `vec0` is a candidate generator core re-scores, whereas an external store computes the distance itself (that is the point of aiming search at Qdrant), so the seam requires cosine similarity in `[-1, 1]` in descending order and the Qdrant app's tests assert its returned score equals the cosine core would have computed to within 1e-6 — otherwise binding a store would silently re-tune a calibrated threshold. **A bound-but-unreachable backend contributes nothing and says so at WARNING; it does NOT fall back to the local index.** Keyword, graph and whole-document vector results still answer, so a search degrades in recall and never fails — the same capability-degradation shape as `MemoryService`'s fallback chain, where degradation is an explicit named path rather than a fiction inside one class. **Core carries no vector-store client**, and a rail proves it: `git grep --untracked` over `src/personalclaw/` for `qdrant|pgvector|chromadb|weaviate|pinecone|milvus`, with comment and docstring lines computed out via `tokenize`+`ast` rather than guessed at with a `startswith('#')` heuristic — `--untracked` is load-bearing, since plain `git grep` cannot see a client dropped into a brand-new module until after review. **With nothing bound, retrieval is unchanged, measured rather than asserted**: the full `HybridRetriever` output over 7 queries x archived/unarchived, plus the raw vector arm's ranks and chunk locators, is byte-identical (SHA256 `7b4e7c6c…d0b5d50`, 16,846 bytes) between this branch and its merge base. **Honest coverage:** Qdrant is proven end to end against its real engine running in-process; the same code path over HTTP to a separate Qdrant server is tested only up to the client constructor; pgvector and Chroma are a published, vendor-neutral seam and no implementation — the app's README says so rather than implying three backends ship.
- **A Session Map mark now says what the turn DID, and you can tell the map to show fewer of them (SSM-3, SSM-14).** Two gaps at opposite ends of the same rail. First, an assistant mark's label was the first 140 characters of the reply, which on a real turn is a preamble — "Sure thing. Let me walk through it." indexes nothing. Each completed turn now persists a one-line **summary label** on the assistant message's `meta` (`summary`), and the map serves it in place of those opening words: the distilled request plus what the turn actually did — the tools it ran, how many failed, whether it ended in an error. That last part is the point, because it is the one thing no raw transcript line contains. It is **extractive, not generated**: no model is called, so there is no per-turn cost and no provider dependency, and a turn that produced nothing to report persists **no label at all** rather than a restatement — the mark falls back to the opening words, exactly as before. The label is redacted where it is written, not where it is read, because `meta` is not scrubbed on the way out the way `content` is. Additive: an older message simply carries no label. Second, on a long tool-heavy session the sub-event ticks outnumber the turns, so the rail reads as a column of tool marks; **Design → Layout → "Session map detail"** now switches it between every mark and turns-only, persisted in the appearance store (localStorage) and applied above both the rail and the mobile drawer so the two forms never index different lists. Deliberately **not** a `config.json` field — nothing about a per-surface view preference needs to sync across devices — and a stale or hand-edited stored value degrades to the default instead of blanking the rail.
- **A scanned PDF stops ingesting EMPTY: `ocr` is a new provider type, and a PDF with no text layer is rasterized and read (KOCR-1, KOCR-2).** Before this, `readers.FileReader._read_pdf` returned whatever `pdfplumber` extracted and nothing else — for a scan that is `''` per page, so the document persisted with none of its words and nothing anywhere saying its text had not been read. Now `document_read` reports `text_layer: false`, a **conditional** graph edge routes only those documents to `pdf_rasterize` → `ocr`, and the OCR'd text lands in the item's pool. The branch being conditional is what makes "no double-OCR" structural rather than a promise: a PDF with its own text layer never reaches the rasterizer, so no OCR backend is constructed at all. With nothing able to read a scan, the item now carries an explicit `meta.ocr = "unavailable"` with empty content and **no** exception — the same report-don't-raise contract `_read_pdf` already had for a missing `pdfplumber` — instead of a silent empty ingest. **Core ships no OCR engine**: `ocr` is a provider type (`sdk.ocr.OcrProvider`) that a removable app fills, deliberately not a `model` serving `image_modality`, because an engine takes no prompt and must be byte-stable for the same input — a property the model seam does not promise. The `ocr` node now has two backends, model-backed (`vision-llm`) and engine-backed (`engine`), and the executor runs whichever **can** run: a bound vision model still wins, an installed engine covers the no-model user, and with neither the node skips exactly as it did before. A backend you pinned yourself is never substituted. Ceilings come from ARCC `cnt_eMkU5kkpTaEk65` ("Secure File Uploads"): the **bytes** decide whether a file is an image, never its extension or its name — a `.png` holding a PDF is refused before any decoder sees it, and so is a real PNG named `.jpg` — plus 64 MiB per image, 40 pages per document, and a per-page pixel budget computed so that a page declaring 20,000 × 20,000 points renders *below* 1× rather than allocating 400 megapixels. Measured end to end through the gateway with the first-party `rapidocr` app installed: the 120-page fixture rendered exactly 40 pages, the scan's pixel-only token came back as text, and the text-layer PDF's own extraction was byte-identical with `pdf_rasterize` and `ocr` both untaken.
- **A clean run down one branch of an either/or no longer reports as `partial`.** `ExecutionResult` counted *every* unrun node as a skip, so a graph with a conditional branch had no way to report success — found the moment the document graph gained its scan branch: every normal PDF and .docx would have started telling the user something was missing from a document that had been read completely. A branch whose edge did not match is now `not_taken`, which is a different fact from `skipped` (a model or engine that should have been there was not) and does not downgrade the item. Relatedly, RET-2's `no_extractable_text` verdict no longer fires on a document another node recovered: on the first real gateway ingest of an OCR'd scan the item's content *was* the OCR'd text while its status simultaneously said no text could be extracted from it. It still fires when no node produced any text, which is the defect it exists for.
- **A relevance reranker arm for knowledge retrieval, OFF by default (KBVS-2).** `HybridRetriever` gains a post-fusion relevance stage: turn on `knowledge.rerank_enabled` (off by default) and the top RRF-fused candidates (`knowledge.rerank_candidates`, default 20 — always at least the requested result count) are sent to the active `reasoning` model for a 0–10 relevance pass, through the existing model-use-case seam (`one_shot_completion`) — no vendor-specific branch in core. A model/transport failure, an empty response (a thinking model on a too-small output budget), or a response naming no real candidate id all fail OPEN to the un-reranked RRF order; reranking is a relevance stage, never a security control, so an outage can never break a search. `personalclaw retrieval-eval` (`personalclaw.evals.retrieval_bench`) now measures the reranked arm's own P@k/R@k as one more row (`rerank`) alongside `keyword`/`graph`/`vector` in the SAME published table the CLI and the dashboard's Retrieval Bench panel already render — no CLI, handler, or frontend code changed, because both already iterate the table generically. When the reranker never gets a usable model response (no provider bound, a timeout, a degenerate parse) that row reads `not measured` (`p_at_k`/`r_at_k` both `null`), never the baseline's own score standing in for a measurement that did not happen. **It ships OFF because it was measured and did not earn the cost, not because it was left unproven:** on a 15-document hand-labelled corpus with a real model bound (Bedrock `claude-haiku-4-5`, `reasoning`), `personalclaw retrieval-eval` scored the baseline `keyword+graph+vector` at P@5 0.2000 / R@5 1.0000 and the `rerank` row at P@5 0.1600 / R@5 0.8000 over the same 5 scored queries — reranking moved one relevant document OUT of the top 5, so on that corpus it is a regression. The same run with no provider bound reported `not measured` for that row instead, which is how the two facts stay distinguishable.
- **The Session Map is reachable: chat now carries an in-session index rail, and on a phone it becomes a tappable drawer (SSM-11, SSM-10).** The map's components shipped over six earlier changes and had **no production consumer** — six built, tested modules that no user could open. A "Session map" control in the chat header now shows a narrow segmented rail in the transcript's left gutter: one tick per turn plus one per tool call, approval and error inside it, the ticks that are on screen lit in the accent, a hover/focus card naming the role, time, request and reply, and click or Enter to jump. The rail is a **sibling of the scroll container, not a child**, so "stays fixed while the transcript scrolls" is structural rather than a `sticky` rule that can drift. Open/closed lives in the URL (`?map=0`), so it is deep-linkable and survives a refresh, and at ≤768px the rail collapses to a `SidePanel` drawer whose rows are 44px tappable targets and whose width persists like every other dock — because this map is the **only** in-session navigation, so the mobile form may not simply drop it.
- **A mark jump now lands on the turn you clicked.** Chat's turn-node registry was keyed by a turn's **array position** while every session-map mark carries `visibleIndex`; those agree only on a transcript that was never collapsed, and `hydrateTurns` collapses native-loop re-injections and merges consecutive assistant messages — so on a tool-using conversation a jump resolved an **earlier** turn's node. One rule (`markCoordOf`) now keys the registry, the marks and the Activity → Index anchors, so the three cannot disagree; a regression test builds its fixture by running the real `hydrateTurns` over a transcript that collapses, because a hand-written fixture cannot fail this. Same coordinate gap `branchLineage.ts` already documented for the fork coordinate, in a second place.
- **An upgrade can no longer silently empty a store: a committed `PERSONALCLAW_HOME` written by the PREVIOUS RELEASE now boots on every build, and one test reads every store back by name (RET-1).** The abandonment driver behind this is the only one with a verified explicit switch-away — an upgrade that breaks state, where a downgrade did *not* undo the damage — and its shape is not a crash: a store comes back **empty** while everything else looks fine, so the loss is found weeks later. `personalclaw gateway --seed six-month-home` seeds that home (a fictional, non-personal home lab plus a reading habit, spread across six months of dates), and `tests/test_state_survival.py` asserts, **per store and individually by name** — `workspace/knowledge/knowledge.db`, `memory.db`, the markdown memory under `workspace/memory/`, `loop/loops.db`, the `cron-history/` run history, `entity_settings/*.json` and `config.json` — a non-zero row/key count **equal to a committed manifest's** plus a successful typed read of one **named** record out of each, after running the boot passes that could destroy state (`MemoryStore.init()`, `VectorMemoryStore.init()`'s migrations, and the `loop/<id>/` orphan reap). Three things make it non-cheatable: the fixture is written by **0.1.3's own writers** (`scripts/generate_six_month_home_fixture.py` refuses to run on any other version, and a rail re-proves the shape gap from `config.json`'s key set, because `__version__` is still `0.1.3` on `main` and cannot discriminate); counts are compared against **committed data**, never against numbers re-derived from the fixture at test time; and the assertions are per store, so one store dropping to zero cannot hide inside a total. Measured against thirteen deliberate mutations — each store emptied, a JSON column blanked, a value reset to its shipped default, a database corrupted — the suite reds on every one.
- **`personalclaw footprint` reports where your disk went, and the gateway now actually gives it back (RET-3).** Nothing anywhere printed bytes-on-disk per store, so "PersonalClaw is using 4 GB" was something you could only discover with `du`; and the number alone is not actionable, so the command records a sample on every run and reports a **growth rate derived from two samples taken at different times** plus the store growing fastest. One sample prints `not yet measurable`, never a fabricated `0 B/day` — a fake zero is indistinguishable from a store that genuinely stopped growing, which is the one reading this report exists to give. Underneath, **two shipped controls that were wired to nothing are now live**: `watchdog.prune_runs` had zero non-test callers (the only non-test mention on `main` was a *comment*) and `workflows.retention_per_def` was declared, clamped, loaded and PATCH-writable with **zero readers** — a knob wired to nothing and a pruner called by nothing are the same bug from two ends. The durability loop now prunes every def past its retention and then **reclaims the bytes that deletion freed** (FTS5 merge → `PRAGMA optimize` → `VACUUM`), because deleting SQLite rows does not shrink the file: the pages are marked free and reused later, so retention could delete every expired run and the disk would not move. Measured on a seeded home: `memory.db` 13,832,192 → 8,192 bytes. Reclaim rides the same tick as graph maintenance and is **outside the `durability.auto_backup` gate** — turning off scheduled backups must not silently stop reclaiming disk — is rate-limited to daily because VACUUM rewrites every store, and shares a lock with `--reclaim` so a hand-run pass cannot collide with the scheduled one. This also fixes a **double count** the probe exposed: five manifest entries are declared *inside* another (`workspace/` owns `knowledge.db`, `knowledge/files` and `lexicon.db`; `workflows/` owns `runs.db`; `loop/` owns `loops.db`), so a naive per-store sum billed those bytes twice and the container row absorbed its child's growth — pointing "which store is growing?" at the wrong store. Every byte under the home now belongs to exactly one row.
- **A chat session now has a durable index: `GET /api/chat/sessions/{session}/map`.** It answers the session's *marks* — one per turn plus one per indexable sub-event (tool call, approval, error) — each carrying the turn's role, timestamp, a markdown-stripped one-line preview and `visibleIndex`, the same `at_message_index` coordinate `POST .../fork` speaks. A chat that is only on disk is rehydrated to answer, so the index is available before the transcript is opened. Alongside it, each turn's **cost / tokens / duration / context %** are now persisted as a structured record on the assistant message's `meta` (`turn_telemetry`) instead of existing only as the live "Turn complete" sentence — so a reload no longer loses a turn's economics. The record keeps its honesty flags: `priced: false` means "no price row for this model", never "free", and `context_pct: null` means the provider measured nothing, never 0%. Additive on both sides — an older message simply carries no record (SEMANTIC-SESSION-MAP SSM-2).
- **The first-run essentials step now offers a zero-key on-ramp for a local Ollama — on this machine and, opt-in, on your network.** Binding a model used to mean picking a provider app, installing it, pasting an API key and testing it; if you already run Ollama, none of that is needed. The model lane now auto-detects an Ollama on `localhost` (a loopback probe over the same `/api/tags` seed path `--seed-local-model` uses — no new socket, no key field) and offers a one-click bind that reaches a working chat turn. A **"Scan my local network"** button, off by default and run only when you press it, sweeps your own private (RFC-1918) subnet for an Ollama answering `/api/tags` on its default port and surfaces each reachable endpoint the same way. Safe by construction: the scan probes **only** private addresses (every candidate is re-classified through the egress guard's host classifier before a connection opens), is **time-bounded** (a black-holed host cannot hang the wizard), and surfaces an endpoint **only after a live probe** — a card is never shown on a guess, and nothing scans the network on first boot. The bind rides the credential-free `bind_local_model` path, so **no API key is ever asked for and none is written to `config.json`**. When no Ollama is reachable locally or on the LAN, the lane offers no bind card and the catalog below is unchanged. Three new routes (`GET /api/onboarding/local-model`, `POST …/scan`, `POST …/bind`); the LAN scan is logged as a security-relevant network action (ONBOARDING-UX OU-13).
- `personalclaw gateway --seed …` gains **`--seed-local-model`**, which binds a local Ollama provider into the seeded `$PERSONALCLAW_HOME` so a demo home can actually run a turn. A seeded home previously bound no model, which made the three surfaces that exist only as the *product* of a turn permanently empty — sessions `0`, `/api/approvals` `[]` (an approval is written by the tool-permission gate on a real turn), `/api/artifacts` `[]` (artifacts are agent-produced) — so the demo could not show chat, an approval or an artifact. The step probes Ollama's `/api/tags` and, only when a server answers with a usable model, confirms or installs the `ollama-models` provider app, writes the `providers[]` entry, and binds the `chat` use case (plus `embedding` when the endpoint has an embedding model). No credential is involved; a local Ollama needs none, which is why it is the provider on this path. **It degrades rather than half-populates**: nothing listening, no chat model pulled, or no provider app available means *nothing is written* — the home is exactly what the fixture copied, the gateway starts normally, and one line names the unmet precondition. Endpoint and model are configurable by flag or env with stock local defaults, so no machine-specific value is committed. The fixture itself stays model-free by design (it is a byte-identical copy on every machine, and a bare `copytree` has no hook where a probe could run); `python -m personalclaw.seed_local_model` binds a home that already exists, without re-seeding or booting a gateway.
- The dashboard gains a **Desktop live view** widget (DCU-7): the computer-use action feed straight off the audit log (every attempt, allowed or refused), an optional picture-in-picture mirror of the screenshots the model already read, and an optional cursor-motion overlay that draws where a click will land. All three are observation-only — a census test pins that the computer-use tool surface is byte-identical with the views on, so watching grants the agent nothing. Renders only what already exists; when desktop computer use is off (the default), the widget says so and explains the out-of-band arming step.
- The desktop app now ships for **Linux x86-64** (DC-6): every release attaches an AppImage and a `.deb`, built and smoke-tested by CI from the release tag. Both are **unsigned by design** — Linux has no OS-level signing gate, so the release page is the integrity story; the [desktop guide](docs/guides/desktop.md) says exactly what to expect on install. Windows remains unavailable, deliberately: the shell follows platform support, never leads it, and the native-Windows backend port is a documented no-go (WSL2 and Docker Desktop are the supported Windows paths).
- Durable tmux-backed run workers gain their **spawn** half (EI-6 §5.1). With `agent.durable_sessions` on (off by default) and tmux installed, an isolated run's workspace setup steps now execute inside a detached tmux session on PersonalClaw's own socket, named deterministically `pclaw-<project>-<run>-<workflow>` — the exact name the boot recovery sweep already recomputes. Killing the gateway mid-`npm install` now leaves the install running under the tmux daemon; on restart the sweep reattaches (the run suspends with a Resume affordance instead of tombstoning, the journal flags the resume), and a step still running from a previous gateway life is waited for rather than run twice concurrently. Every failure to arrange durability — tmux absent, a refused session, a session that dies without reporting — falls back to today's bare subprocess, so the flag can never break a run. The tool resource ceiling still applies inside the session, and a run's teardown kills its durable worker before the workspace is deleted. With the flag off, behavior is byte-identical; no new persisted state.
- **An MCP server can now ask *you* a question mid-tool-call, through the approval card PersonalClaw already had** (MBR-1, the MCP spec's `elicitation/create`). The right is **denied by default and granted per server** — `security.mcp_elicitation_servers` is an allowlist that ships empty, toggled per server on Settings → Tools, so a server added for one read tool never silently acquires the right to interrupt you. A granted question arrives as an ordinary approval card attributed to the server that asked (`mcp_elicitation:<name>`); your yes or no is returned to the server as the protocol result. The server controls only the prose in its question: the attribution line is computed from the server's configured name and cannot be forged by the text, and that text is carried as data through the same redaction the approval store already applies, so an exfiltration URL in a prompt is redacted before you see it and injection prose is shown to you verbatim rather than obeyed. A question nobody answers is **cancelled, not left hanging** — it is bounded by the window its answer could still be delivered in (derived from the MCP tool-call ceiling, deliberately not a knob), the card is withdrawn from the UI at that point, and the server is told `cancel` rather than a refusal you never made. A form a yes/no cannot truthfully fill — anything asking for a string — is refused with a typed protocol error instead of being answered with a fabricated default, and URL-mode elicitation is refused outright because sending you to a third party's page to type a credential is not a confirmation. Servers that were never granted the right advertise no `elicitation` capability at all, and one that asks anyway gets a typed error back immediately instead of a stall. PersonalClaw's own inbound MCP server surface still does not issue elicitations; this is the client half only.
- The Learning page gains a **Lab vs field** panel (ES-9): one row per subject (bundled template or registered action type) showing its pinned lab score beside its live field record — 👍/👎 rate, edit-before-approve rate, and approval/rejection/undo rates derived from the feedback and earned-autonomy ledgers, computed by query and stored nowhere new. A subject whose lab score rose while its field trend fell is flagged `lab_field_divergence` — the honest "lab says better, is it?" check — and that flag now mechanically files an autonomy demotion for the divergent subject (an action type loses its own standing grant; a template's standing grants are voided wholesale, the same consequence a failed pre-registered study already carries). The demotion is gated by `evals.enabled`: turning evals off suspends the demotion but never hides the flag, and it fires at most once per standing grant. The panel reads `GET /api/evals/field-metrics` (read-only — a GET never demotes).
- **The published HTTP route reference is now generated, and its count is measured rather than asserted** (`docs/reference/api-routes.md`). The gateway registers 848 route handlers over 689 distinct paths; the page renders all of them from the same census that produces the offline reference shipped inside the wheel and the live `GET /api/manifest`, grouped by a 127-row family index so the shape of the surface is readable before the list is. `scripts/generate_api_route_reference.py` writes it and refuses to run when the imported package and the write target are different checkouts — the trap that had a sibling generator silently rendering one tree's routes into another's file. `tests/test_docs_api_reference.py` byte-compares the committed page against a fresh render, so a route added without a row reds CI instead of becoming an undocumented surface.
- **A geometry gate over first run: every control and the product title must lie inside the scrollable range, at four viewports** (`web/e2e/onboardingGeometry.spec.ts`, wired into CI's `e2e-a11y` job). The onboarding screen shipped with its `<h1>` measured at **-247px** (1280x700) and its "go back to step 1" button at **-141px** — above a scroll origin that `scrollTop` clamps at 0, so unreachable by mouse, keyboard and screen reader at every viewport height — while **every** frontend rail was green, because no gate in `web/` computed geometry at all: jsdom has no layout engine, so `clientHeight` is 0 and every `getBoundingClientRect()` is zeros there, and neither `design/scrollRegionNamed` nor `design/overlaySurfaceA11y` even renders `Onboarding`. The new rail asserts the property rather than the CSS shape — `contentTop >= 0` and `contentBottom <= scrollHeight` for the `<h1>` and every operable control — at 1280x800, 1280x700, 1024x600 and 390x844, plus that the box really scrolls, that `scrollIntoView` really lands each control, that the page does not pan sideways on a phone, and the two keyboard clauses (focus survives a step change; every visible control is in the tab ring). It was proven to FAIL before it was trusted: against the shipped build 9 of 15 legs red, and against an isolated re-introduction of `items-center` on the scroller 7 of 15. It carries its own anti-vacuity floor — a fixed step-2 payload so all four viewports overflow (a CI runner has no other agent tool installed, so the live scan renders one line and the whole rail would pass on the broken build), an assertion that each viewport still overflows, and the measured offsets written into `web/test-results/` so a run's numbers outlive its verdict.
- **Homebrew and Nix are now real install paths, and a fresh-install validator proves it on a machine that does not already have them (DIST-12).** Two convenience channels shipped together because they answer the same question in opposite ways. **Homebrew** lives in the new public tap [`PersonalClaw/homebrew-tap`](https://github.com/PersonalClaw/homebrew-tap), so `brew install personalclaw/tap/personalclaw` resolves; its formula pins and `sha256`-verifies the published PyPI sdist, then resolves the dependency closure from PyPI into a dedicated `python@3.13` virtualenv. That is **not hermetic**, deliberately, and the alternative was measured rather than assumed: `virtualenv_install_with_resources` builds every vendored `resource` from source, and this closure cannot be built from source at all — `pdfplumber` requires `pypdfium2`, whose source build is a prebuilt PDFium with no Homebrew formula to link against. The venv is created in `post_install`, not `install`, for a second measured reason: Homebrew rewrites the Mach-O dylib ID of every `MH_DYLIB` in a keg after `install`, this closure carries **22** of them (`nh3`, `cryptography`, `tree-sitter-language-pack`, Pillow's 17 bundled codec dylibs, pypdfium2's `libpdfium.dylib`), they are linked with no header padding, and the first rewrite fails with `Updated load commands do not fit in the header` — which made `brew install` exit 1 while the keg installed completely. There is no formula-level opt-out, but Homebrew orders `link` → `fix_dynamic_linkage` → `post_install`, so a venv built in the last step is simply not there for the scan. **Nix** is the opposite trade: `flake.nix` + `nix/personalclaw.nix` give `nix run .#personalclaw -- --version` a fully pinned closure through `flake.lock`, and `nix flake check` runs that exact command as its check. It packages the **published wheel** rather than the checkout, because the dashboard SPA is built by `make web-build` with Node and a Python build of a bare tree yields a gateway serving no dashboard — asserted in `postInstall` rather than trusted. The `nixpkgs` input is pinned to a release branch, not `nixos-unstable`, because a released wheel's bounds are frozen at release time: `nixos-unstable` carries reportlab 5.0.1 against the wheel's `reportlab<5` and `nixos-25.11` carries tree-sitter-language-pack 0.10.0 against its `>=1.0`, so the two failures point in opposite directions. Both channels are exercised by `scripts/fresh_install_validate.sh` (`pip` · `brew` · `nix`), which runs each install in a throwaway container because a dev box cannot test the one claim an install path makes — that it works where the dependency is not already present. Its brew leg runs linuxbrew on the host architecture and says so: Homebrew refuses an x86_64 CPU without SSSE3 and reads `/proc/cpuinfo`, so a QEMU-emulated amd64 container on Apple Silicon cannot run it and `QEMU_CPU=max` does not help. macOS evidence comes from the tap's own CI on a clean GitHub-hosted runner. README's install matrix gains both rows with their caveats, and the release runbook gains a per-release convenience-channel smoke checklist — including the one trap that actually bites, that a release's *wheel metadata* and `pyproject.toml` disagree (0.1.3's wheel asks for `croniter<7` and `reportlab<5` where the tree now says `croniter<3` and `reportlab<6`).

### Changed
- **This repository is public, and now publishes only what is fit to publish — `temp-screenshots/` is gone, `scratch/` is `staged-repos/`, and a rail reds if either class comes back.** `git ls-files` is exactly what a clone receives, and `git rm` takes a path out of the tree but never out of history, so an unfit path is effectively permanent once merged. Four defects, one class. **(1)** `temp-screenshots/` held **80 files, 20.79 MB** of per-PR before/after review evidence accumulated across ~40 PRs — including the three largest files this repository has ever tracked (1.80 MB, 1.38 MB, 0.97 MB). Nothing rendered it, and its only mention anywhere was a parenthetical in `docs/demo/CLICKPATH.md` explaining why a *video* was too big to commit; that sentence's own size claim had already gone stale against it, and is true again now. The deliberately published `docs/screenshots/` (light + dark, referenced by README and SHOWCASE, with its capture script beside it) is untouched. **(2)** `scratch/` is **renamed, not deleted**: its content is load-bearing — the app template is pinned byte-for-byte to the generator by `test_app_from_template.py`, the community registry is validated by a `full.yml` job, and four of its LICENSE files are inventoried in `docs/architecture/licence-identity.txt` — but a directory literally named `scratch` in a public repo tells a reader to ignore exactly the thing two rails depend on. It is now `staged-repos/`, which says what it is: sibling repositories' content staged here until the maintainer publishes it. **(3)** `.gitignore` gains `/.worktrees/` and `/.local/`; without them a plain `git add -A` staged 25 paths, every linked worktree as an **embedded git repository** — a gitlink pointing at a local absolute path that exists on no other machine, which git warns produces a clone that "will not contain the contents of the embedded repository and will not know how to obtain it" ([#3413](https://github.com/PersonalClaw/PersonalClaw/issues/3413)). **(4)** Two `web/` tests carried the maintainer's real home path as inert fixture strings, publishing a username and machine layout; both now use the `/Users/me` placeholder the first of them already used three lines away. Guarding all of it: `publication-hygiene-baseline.json` + `tests/test_publication_hygiene_baseline.py`, a denylist of path shapes asserted against `git ls-files` — residue names, residue suffixes, build output, embedded worktrees, dev-home and secret material, dotenvs with real values, oversized binaries (text exempt, so the CHANGELOG stays legal), and absolute paths into a real developer's home. That last rule is an **allowlist of placeholder names** rather than a denylist of real ones, because a denylist would have to write the maintainer's username into the public repo to keep it out. No shipped surface changes — the wheel packages `src/` only — and nothing was rewritten out of history.
- **`AGENT.md` is gone; `AGENTS.md` is the only agent brief in the repo.** Two root files one character apart is a trap rather than two documents: `AGENTS.md` is the conventional name and carried the doctrine, the git rules and §"Shared conventions", while `AGENT.md` held a 94-line mechanical gotcha list — so an agent or contributor opening the wrong one got the wrong half of the contract. Every gotcha moved into `AGENTS.md` as §"Mechanical gotchas", each keeping its `harness/specs/` cross-reference; the one body that was already duplicated (the config round-trip four points) now points at the Doctrine section instead of restating it a third time. `CLAUDE.md` was already a one-line pointer and stays one. Corrected in the same pass: the repo map still listed `docs/roadmap/` as though present, four sections above the line that correctly says the maintainer's roadmap is not in this repo.
- **The five Settings switches that relax a security or safety default now confirm before they take effect.** [#753](https://github.com/PersonalClaw/PersonalClaw/issues/753). Turning ON YOLO mode or "Propose fix branches", checking "Allow all private networks", or turning OFF either "Offer password sign-in" or "Require a 2FA code" previously wrote the config on a single click. Each now raises the confirm dialog the app already ships, and each dialog names its own consequence rather than asking a generic "are you sure" — lock-out risk for the sign-in switches, the removal of SSRF protection for private-network egress, the absence of any expiry for YOLO. Declining leaves the switch untouched and writes nothing. The **tightening** direction stays one-click in every case: a confirmation there would be friction with no security value. The gate is an opt-in `confirmOn` prop on the shared `ToggleRow`, not a `ToggleRow`-wide default, so the harmless switches in the same settings family are unaffected.
- **Workflow dispatch preparation and dashboard WebSocket delivery now live in focused sibling modules.** [#3253](https://github.com/PersonalClaw/PersonalClaw/issues/3253). This is a behavior-preserving structural extraction: `workflows/engine.py` and `dashboard/state.py` both move below the next watch-band tier, making `dashboard/handlers/triggers.py` the new 2,528-line controller and raising live headroom from 154 to 272 lines. Internal helper imports move cleanly to `workflows.engine_support` and `dashboard.ws_state`; no compatibility re-export layer is retained. No persisted shape or production behavior changes. As with any pre-1.0 class-B update, run `personalclaw snapshot` before upgrading.
- **`workflow_start` now validates inputs against the same tree-derived parameter contract shown by `workflow_plan`.** Missing required bindings return the extraction contract's `missing` list and actionable `follow_up` before a run is created, including bindings a definition forgot to declare. Complete payloads continue through the normal type/default/preflight start gate. The unused `PreFill` family was deleted in the same clean break; run `personalclaw snapshot` before upgrading if you depend on pre-1.0 workflow behavior.
- **Loop end-state labels now come from the structured `stop_reason`, not free-text `error_message` prose (AG-14).** A non-genuine completion whose reason names a cycle, cost, deadline, or worker ceiling reads **Ended early** across the loop list, cockpits, chat progress card, and active-work widget; a monitor whose cycle budget is its intended watch window now stamps `done` and remains a genuine **Completed** run. Clean break under the pre-1.0 banner: rows written before `stop_reason` was persisted carry an empty reason and now read as completed even when legacy error prose is present. No fallback, shim, or migration is carried; run `personalclaw snapshot` before upgrading if preserving the previous rendered history matters.
- **66 more spacing values now obey the Density slider (Appearance → Density), in 29 whole files.** The shrink-only ceiling landed first and on purpose without a sweep, so this is slice 1 of the conversion it exists to enable: `web/src/design/spacingTokenRamp.test.ts`'s `MAPPABLE_CEILING` is re-stated **3135 → 3069**, measured on `a96ec3d0c`. Every conversion is a raw numeric utility whose pixel value lands exactly on a ramp rung — `gap-1`/`py-2`/`px-3`/`pr-7` → `gap-xs`/`py-s`/`px-m`/`pr-3xl` against `--spacing-xs…3xl` = 4/8/12/16/20/24/28px — so all 66 are **pixel-identical at the default density** and simply start tracking `--space-scale` (0.8 dense, 0.68 cli) instead of staying frozen. The unit of the sweep is a **whole file**, never a hunk: a file is converted only if *every* raw spacing utility in it maps exactly, because a half-converted file is worse than an unconverted one — the converted half shrinks under the slider while the rest does not, which is a new visual defect at non-default density rather than the old inert one. That rule is what kept two of the 31 candidate files out: `chat/WorkflowProgressCard.tsx` (`py-0.5`) and `settings/ChatPanel.tsx` (`gap-y-1.5`) hold half-steps the ramp has never had, and a 6px or 2px need has no tokenised form until the owner decides whether the ramp gains rungs. The same rule (plus the ramp having no negative utilities) is why a padding paired with a negative margin — `ui/TextLink`'s `py-1 -my-1` hit-target idiom — is not touched by this or any later slice. The half-step population is unchanged at **1776**, so the total falls 4911 → 4845 entirely out of the defect half. Failability: reverting a single conversion (`StaleNotice`'s `gap-xs` → `gap-1`) reds the ratchet by name at 3070.
- **A tool's risk level now gates the control that runs it, and "Always for this agent" only promises a saved grant when it will actually save one ([#506](https://github.com/PersonalClaw/PersonalClaw/issues/506), [#541](https://github.com/PersonalClaw/PersonalClaw/issues/541), [#683](https://github.com/PersonalClaw/PersonalClaw/issues/683)).** `risk_level` was computed everywhere and enforced nowhere: `POST /api/tools/invoke` resolved a call's effective risk, spent it on the audit row, and then invoked the provider unconditionally; the Tools → **Try it** confirm was one warn-toned inline step, byte-identical for `artifact_list` and for `bash`; the chat approval card's risk chip described itself in code as "a purely INFORMATIONAL chip"; and ticking `bash` into an agent's tool allowlist cost exactly one click. **What changes for you:** a call whose *effective* risk resolves as `destructive` is now refused by the route with `403 risk_confirmation_required` unless the request names the tier (`"confirm_risk": "destructive"`) — cron script authors pass `ctx.call_tool(..., confirm_risk="destructive")`. Read-only invocations still downgrade to `safe`, so `ctx.call_tool("bash", {"command": "ls"})` is unaffected, and the `safe`/`caution` tiers are not gated at all. In the UI the ceremony scales instead of being uniform: `safe` keeps its existing inline "Confirm & run", `caution` takes a modal with a named verb, `destructive` requires typing the tool's own name, granting an agent a destructive tool asks first (and *removing* one never does), and a destructive chat approval withholds the two standing-grant scopes until you tick an explicit unlock — Allow-once is never withheld, so a prompt is always answerable in one click at its narrowest scope. **And the grant card stopped over-claiming:** "This agent" promised *"Saved on this agent … in this chat and future ones"* unconditionally, while the backend degraded the grant to session scope for a reserved system agent, an agent with no saved profile, or an ACP-bound chat, and said so only in the log. The card now names the agent the grant will be saved on, or says "this chat only" when it cannot be saved; the approve response reports what it actually did; and the transcript records `trust_agent` versus `trust_agent_session` instead of the `approved` that made a standing grant indistinguishable from a single confirmation. A duplicate `Session.mark_permission_resolved` writer — no production callers, and a `decision="approved"` default on the field that records a security decision — is deleted. Class-B behavior change, clean break under the pre-1.0 banner: no gate, no migration. Existing transcripts are untouched (unknown resolution values already render honestly), and a cron script that invokes a destructive tool without acknowledging the tier will start failing closed until the keyword is added.
- **⚠️ PERSONALCLAW NOW TRACKS RELEASES, NOT `main` — AND THE UPDATE IT APPLIES IS THE ONE YOU CHOSE (RUM-1…11).** This is the user-facing summary of the whole release-update program, because the pieces landed one atom at a time and the *flip* is one change: before it, an in-app update meant "become the tip of `main`" — the dashboard's "Update & Restart" ran `git pull`, the unattended auto-update ran `git reset --hard origin/<branch>` (destructive, on by default), and a wheel install upgraded to a blind `releases/latest` no setting could redirect. After it, every install kind rides the release your **channel** and **pin** resolve to: a git clone runs `git fetch --tags` + `git checkout <tag>`, a wheel installs `personalclaw==<tag>`, a container pulls the matching `:X.Y.Z`/`:X.Y`/`:beta` image tag, and `git reset --hard origin/` is gone from every apply path. **Four things you should set deliberately**, all in **Settings → Updates** (new this release — six controls over the six `updates.*` fields, where there used to be two switches over the same block) or via `personalclaw config set`: `updates.channel` (`stable` default · `beta` for release candidates · `nightly` = track your branch, git clones only); `updates.pin` (stay on an exact release; it overrides the channel everywhere, and a pin naming no published release is *refused* rather than silently upgrading you); `updates.auto` (**`off` is now the default** — an available update only notifies; `staged` applies at the next safe point, holding while a session or subagent is in flight); `updates.check_enabled` + `updates.check_interval_hours` (the check is the project's one outbound call, it can be turned **off** entirely, and its cadence is yours). **Rolling back is now a supported path in both directions**: `personalclaw update --to 0.2.0` pins that release and installs it, and the panel offers **Roll back to v&lt;previous&gt;** once PersonalClaw has seen your version change at least once (`updates.last_version`, recorded at gateway startup — however the version changed, including a hand-typed `pip install -U`). **⚠️ RUN `personalclaw snapshot` BEFORE ANY UPDATE OR ROLLBACK.** Pre-1.0 releases carry no data migrations in *either* direction, so moving between them can meet state written by the other build; the snapshot is the only way back. **One legacy mapping, applied once on load**: a home carrying the old `auto_update: true` becomes `channel=stable` + `auto=staged` — i.e. an install that was riding raw `main` unattended starts riding stable release tags instead — `auto_update: false` becomes `auto=off`, and `dashboard.update_dev_mode: true` becomes `channel=nightly`. Nothing else in your config is touched.
- **Inbox maintenance no longer runs on its own 6h loop — the remediation engine owns it, like every other store-tidying pass (PR2-11).** The inbox service kept a private `_MAINTENANCE_EVERY_SECS = 6h` timer inside its poll loop that ran retention cleanup, dismissed-set pruning and the feedback retire-candidate check — the last duplicate maintenance cadence PR2-8 left standing after it retired the heartbeat's copies into the health-scored engine. That timer is deleted; the poll loop only polls now. The same work is a registered remediation job, `inbox.maintenance`, driven by a measured `inbox_maintenance_backlog` deficit (expired items when auto-cleanup is on + prunable dismissals + pending retire proposals), so a pass fires when there is actually something to do rather than every 6h regardless — and it shows up on the Doctor → Maintenance deficit list and runs on demand via **Run maintenance now** like the others. Because the inbox store lives in memory on the running service, the job drives that live instance and its work is bounced onto the loop that owns the store, so nothing is mutated from the engine's worker thread (no forked store, no concurrent-mutation race). With `resilience.remediation.enabled=false` the pass does not run — exactly what "disabled" means for every other automation — and it stays callable through `POST /api/doctor/remediation/run`; a headless/CLI process (no gateway) runs no inbox maintenance, same as before. This is the last piece of §4.4 criterion #6, "old maintenance no longer runs independently," now true for the inbox too. Class-B behavior change (WHEN maintenance runs), clean break under the pre-1.0 banner: no gate, no migration — `personalclaw snapshot` before upgrading, as usual ([#2913](https://github.com/PersonalClaw/PersonalClaw/issues/2913)).
- **A release candidate no longer moves `:latest`, a `-beta` tag is no longer published as a stable release, and the moving `:X.Y` / `:beta` image tags the updater pulls now actually exist (RUM-8).** `release.yml` answered "what does this tag publish?" three times in three shell dialects, and two of them were wrong. The `images` job pushed `:X.Y.Z` **and `:latest` unconditionally**, so tagging `v0.3.0-rc.1` moved `personalclaw-{gateway,web}:latest` — and because the compose file resolves `${PERSONALCLAW_IMAGE_TAG:-latest}`, the next `docker compose pull` on any container install running the default silently landed on a release candidate. The GitHub-Release step decided prerelease-ness with `[ "$REF" != "${REF%rc*}" ] && echo --prerelease`, a suffix test that matched `rc` and nothing else: `v0.3.0-beta.2` published as a full, non-prerelease Release, which `stable`'s `select_target` then offered to every user as an upgrade. Meanwhile the tags the updater *pulls* were published by nothing at all — RUM-7 shipped `select_image_tag`, resolving `stable` → the moving minor `:X.Y` and `beta` → `:beta`, and [the container guide](docs/guides/containers.md) documents both, but no job ever created either tag, so the `PERSONALCLAW_IMAGE_TAG=0.2 docker compose pull` the Updates panel printed named an image that does not exist. All three now come from **one parser**, `scripts/release_tags.py`, which the workflow and its test read the same way: a stable `vX.Y.Z` publishes `:X.Y.Z`, `:X.Y` and `:latest` and claims the GitHub "Latest" pointer explicitly; a prerelease `vX.Y.Z-rc.N` / `-beta.N` publishes `:X.Y.Z-rc.N` and **only** `:beta`, and gets `--prerelease`. A prerelease deliberately withholds even its **own** minor tag — once `v0.3.0` ships, `:0.3` has to mean the stable 0.3 line, and someone who pinned `:0.3` never opted into candidates. A tag the parser cannot classify (no `v` prefix, not `X.Y.Z`, or carrying `+build` metadata — a `+` is not legal in a Docker tag and rewriting it would mint a tag the pin resolver can never ask for) now **fails the release before the build runs** rather than falling through to publishing the moving tags. `tests/test_release_tag_semantics.py` asserts the whole matrix including the negative cases (no prerelease publishes `:latest` or any bare `X.Y`), that no job hardcodes a moving tag any more, and that every tag `select_image_tag` can name is one the pipeline publishes — so the publisher and the updater cannot drift apart again. No config change, no user action; the first release cut after this lands is the first one whose `:X.Y` and `:beta` tags exist.
- **A container install's update commands now carry the image tag your `updates` channel/pin resolves to, not a bare `latest` (RUM-7).** The Settings → Updates panel and `personalclaw update` on a container install used to print an unconditional `docker compose … pull` + `up -d`, which the compose file resolves to `${PERSONALCLAW_IMAGE_TAG:-latest}` — so `latest` regardless of `updates.channel`/`updates.pin`. Both surfaces now resolve the channel/pin through `self_update.resolve_image_tag(channel, pin)` and prefix `PERSONALCLAW_IMAGE_TAG=<tag>` onto **both** commands (the pull and the recreate run as separate processes, so the tag has to be on each): `stable` (default) → the moving minor `:X.Y`, `beta` → `:beta`, and a version pin → that exact immutable `:X.Y.Z`. A pin that matches **no** release now **refuses** — the panel says which pin matched nothing and the CLI prints "No release matches the pinned version" — rather than silently pulling `latest`, mirroring the wheel pin-miss refusal (RUM-6). This brings the container kind level with the git (RUM-4) and wheel (RUM-6) kinds, so every install kind honors the same `updates` block. The moving `:X.Y` / `:beta` tags are published by the release pipeline in RUM-8. No config change (consumes RUM-1's `updates` block); clean break under the pre-1.0 banner.
- **A pip / pipx / uv wheel install now upgrades to the release your `updates` channel/pin selects, not a blind `releases/latest` (RUM-6).** `personalclaw update` and the Settings → Updates "Update & Restart" button on a wheel install used to run `install -U personalclaw==<releases/latest>` — always the newest non-prerelease — so setting `updates.channel="beta"` or pinning `updates.pin="0.2.1"` (both added in RUM-1/RUM-2) changed nothing for a wheel: it still chased the stable latest. Both apply paths (`_update_pip` in the CLI, `_apply_pip_update` in the dashboard) now resolve the target through `self_update.resolve_wheel_target(channel, pin)` and install exactly `personalclaw==<resolved tag>`: `stable` (default) rides the newest non-prerelease, `beta` includes release candidates, and a version pin overrides both and installs that exact version. A pin that matches **no** release now **refuses** rather than silently upgrading to latest — the whole point of a pin is "stay exactly here." The git-only `nightly` channel has no published wheel, so a wheel install on `nightly` rides `stable` instead. Offline with no pin still upgrades unpinned (`-U personalclaw`), exactly as before. This brings the wheel kind level with the git kind (RUM-4), so every install kind now honors the same `updates` block. No config change (consumes RUM-1's `updates` block); clean break under the pre-1.0 banner.
- **Unattended auto-update is now OPT-IN and STAGED, and the always-on `auto_update` bool is retired (RUM-5).** The default flips from "apply every update unattended" to notify-only: the new `updates.auto` field (added in RUM-1, now the sole control) defaults to `"off"`, so an available update raises the `update_available` notification and is **never** applied on its own. Setting it to `"staged"` applies at the next safe point — the apply **holds** while any chat session or background subagent is in flight (reusing the same `_active_work_snapshot()` the manual-restart confirm gate uses) and fires only once that work drains, landing solely on the resolved channel/pin **release tag**, never on raw `main` (the apply is RUM-4's release path). The legacy top-level `auto_update` boolean, its `POST /api/update/auto` endpoint, and its `_EDITABLE_CONFIG` allowlist entry are **removed**; the Settings → Updates and onboarding "Update automatically" toggles now write `updates.auto` (on ⇒ `staged`, off ⇒ `off`) through the validated config PATCH. A home written with `auto_update=true` and no `updates` block loads to `updates.auto="staged"` + `channel="stable"` automatically (the RUM-1 backfill reads the raw legacy key), so an existing auto-updating git user stops riding raw `main` and starts riding stable release tags. Class-B behavior change, clean break under the pre-1.0 banner: no gate, no migration — run `personalclaw snapshot` before upgrading.
- **The in-app updater no longer tracks raw `main` — a git checkout rides release TAGS by channel, and the destructive `git reset --hard origin/main` is gone from every unattended and dashboard apply path (RUM-4).** Before this, "Update & Restart" on a git install ran `git pull` and the unattended auto-update ran `git reset --hard origin/<branch>` — the checkout followed the *tip of `main`*, which is unreleased, unpinned code, and the hard reset could silently discard tracked edits. Now the git kind resolves the `updates` channel/pin (added in RUM-1) to a release tag and applies it with `git fetch --tags` + `git checkout <tag>`, exactly like every other install kind: `stable` (default) rides the newest non-prerelease, `beta` includes release candidates, and a version pin overrides both. Being on the resolved tag is "up to date" even when `main` has newer commits. The git-only **`nightly`** channel is the ONE path that still tracks the current branch, and it advances by **fast-forward only** on a clean tree — a diverged or dirty tree is refused and left untouched, never reset. The `dashboard.update_dev_mode` bool and its `POST /api/update/dev-mode` endpoint are **retired**: their job is now `updates.channel` (`nightly` vs `stable`), and a home written with `dashboard.update_dev_mode=true` loads to `channel="nightly"` automatically (RUM-1 backfill). Class-B behavior change, clean break under the pre-1.0 banner: no gate, no migration — run `personalclaw snapshot` before upgrading if you kept local edits on a checkout.
- **The install scanner's terminal tier is no longer shell-only: destruction written in Python is refused, and a bundle that deletes your home directory can no longer install with a clean bill of health.** Every rule in `SkillScanner`'s DANGEROUS band was a shell string (`rm -rf /`, the fork bomb, `mkfs`/`dd`, `curl|sh`), so the same destruction spelled in the language bundles are actually written in was not merely un-refused — it was **clean, with zero findings**. Measured at the community tier, one payload per one-file bundle: `shutil.rmtree(os.path.expanduser("~"))`, `shutil.rmtree("/")`, a `Path("/").rglob("*")` loop calling `unlink`, and `open("/etc/hosts", "w")` all scored `clean` while `rm -rf /` in a `.sh` file beside them was terminal. Three new rules close it — `destructive_delete` (removes a path your account or the machine owns), `destructive_walk` (sweeps a whole tree and deletes what it yields), `destructive_truncate` (blanks an OS-owned file) — all at the same non-overridable severity as the shell rules, per the owner's ruling on the tier. **Decided on the `ast`, never on the text**: these are call sites, and a text scan cannot tell a call from a mention — a comment warning against `shutil.rmtree`, a test asserting it is never used, or a denylist naming it as a forbidden writer would all have matched. Import aliases are resolved (`import shutil as sh`, `from shutil import rmtree as rt`) and a name the file binds exactly once is followed one hop, so the two-line spelling is not a way out; the rules run on any script surface whose extension does not name *another* language, so an extension-less `scripts/setup` with a python shebang is not a parking spot either. **The severity turns on the TARGET, not the call**, exactly as `destructive_root` already keeps `rm -rf /tmp/build` out of the terminal band: root, home and the OS-owned trees are terminal, while `/tmp`, `/var/folders`, `/dev/null`, `~/.cache/...` and any target the analysis cannot resolve to one of those are not. Measured against the 64 shipped app bundles at `PersonalClawApps@origin/main`: verdicts unchanged, `clean=48 warning=16 dangerous=0`, **zero newly blocked installs**. The finding goes through the same execution-reachability scoping #2605 added and reports `reachable` with its reason, and the same call commented out or quoted in a docstring produces **no finding at all** — `ast.parse` discards commentary before the rule can see it, which is stricter than the consentable warning a re-scored regex match earns ([#2607](https://github.com/PersonalClaw/PersonalClaw/issues/2607)).
- `run_chat` is no longer exported from `personalclaw.sdk.channel` (EA-7 step 3). It was an ungated second route past the channel sender-trust gate; a channel app now reaches a turn only through `services.deliver_channel_inbound`, which applies `guard_inbound` unconditionally. All four bundled channel apps (discord, telegram, email, slack) already migrated — update installed apps alongside this core upgrade; an app version still importing `run_chat` fails at import time. The function itself is unchanged at `personalclaw.dashboard.chat.run_chat` for the owner's own surfaces.
- Workflow runs gain a sparse `policy_overrides` overlay (PP-16 seam 4d): the five per-instance supervisor knobs (`attended`, `autopilot`, `max_cycles`, `idle_secs`, `success_criteria`) can now be persisted per run, composed on top of the template/kind defaults at resolution time. The `runs` table gains a `policy_overrides` column additively — an existing home is upgraded in place on first open (same default as a fresh install), no data migration. A run that overrides nothing persists nothing and behaves exactly as before.
- The install one-liner now verifies something, and stops claiming to verify what it does not ([#2582](https://github.com/PersonalClaw/PersonalClaw/issues/2582)). `curl -fsSL https://personalclaw.dev/install | sh` made two network fetches and checked the integrity of neither. Three changes, none of which give up what the script is built around. **(1) A downgrade floor on the PyPI install** — `uv tool install --upgrade 'personalclaw>=X.Y.Z'`, set to the *previous* release. `--upgrade` still resolves to the newest version, so on an honest index the floor forbids nothing (measured: `>=0.1.2` installs 0.1.3); a rolled-back or yanked-and-replaced index goes from a silent old install (measured `+ personalclaw==0.1.0`, exit 0) to a loud resolver error. It lags by one release deliberately: a floor equal to the version in `pyproject.toml` is unsatisfiable in the window before that release reaches PyPI and its mirrors, which would break every install and the container install-smoke with it. A test pins the constant to `CHANGELOG.md`'s second-newest release heading, so it cannot rot into an unchecked hand-typed version. **(2) An honest trust boundary on the `uv` bootstrap.** The comment above the `astral.sh` fetch no longer says the installer "verifies its own downloads" — true of the uv *binaries* that installer goes on to fetch, false of the bootstrap script we pipe into `sh`, which is the line it appeared to defend. It now states the real chain (TLS to astral.sh plus Astral's release hygiene, nothing else) and that this is the same posture as rustup, nvm and uv's own documented bootstrap. **(3) A real user-facing verify path.** `deploy/website/install.sh.sha256`, until now documented only as a maintainer drift-detection artifact, is written up as an integrity check users can run: [Verify the one-liner](docs/guides/getting-started.md#verify-the-one-liner) fetches the served script and its digest **from different origins** — the digest from this GitHub repo — which is the entire security value, because a digest served by the host that serves the script would be handed to you by the same attacker. What it does *not* prove is stated alongside it. Nothing in either fetch is signature-verified: the wheel does carry [PEP 740](https://peps.python.org/pep-0740/) provenance from Trusted Publishing, but no released `uv` or `pip` checks it at install time.
- **The inbox has one definition of "open" and publishes one count.** `GET /api/inbox/status` no longer carries `pending_count`; it carries `open_count` (PENDING **or** SEEN), and `GET /api/inbox/pending` becomes `GET /api/inbox/open` and answers with that same set. Clean break under the pre-1.0 banner — no dual field, no aliased route: a second count is a second definition waiting for a surface to render it, which is precisely how this broke. The open set is now one `inbox.OPEN_STATUSES` frozenset consumed by the store, the kinds endpoint, the bulk sweep, the emit-dedup check, the workflow gate resolver, the needs-input reminder policy and the proactive digest lane — six modules that each spelled it themselves — with `RESOLVED_STATUSES` as its declared complement and a test asserting the two partition `ItemStatus` exactly, so a new attention state cannot default into either. The frontend derives `isOpen`/`OPEN_STATUSES` from `lib/attentionLanes`' exhaustive status record, and a cross-language test parses that record to pin it to the server's set ([#493](https://github.com/PersonalClaw/PersonalClaw/issues/493)).
- The `runs` table no longer declares a `task_list_id` column and `WorkflowRun` no longer carries the field (PP-16 seam 4c). The column was never populated or read — per-phase task lists are projected from run state instead. Clean break under the pre-1.0 banner: no migration; an existing home keeps the inert column, which new code neither reads nor writes. `personalclaw snapshot` before upgrading is advised as usual.
- **⚠️ TIMED TRIGGERS WITH NO EXPLICIT TIMEZONE NOW FIRE AT THEIR LOCAL WALL-CLOCK TIME, NOT AT UTC.** A clock trigger, cron job or scheduled report that never declared a zone was evaluated in **UTC**, so a `30 8 * * *` "remind me at 08:30" fired at 08:30 UTC — measured on a `America/Los_Angeles` host as `01:30` local, a **7-hour** error — and it was silent: the trigger armed, nothing warned, and the only symptom was a notification at an odd hour. Resolution is now **explicit zone → `config.timezone` → this machine's zone (`TZ`, the `/etc/localtime` symlink target, `/etc/timezone`) → UTC as a last resort**, and `personalclaw doctor` reports a UTC last resort as a ⚠️ naming the consequence in hours rather than as an informational line. **Every existing trigger that relied on the old default changes the hour it fires**; this is a clean break under the pre-1.0 banner — no gate, no migration, no dual path. Run `personalclaw snapshot` before upgrading, and after upgrading check Automations for any schedule you had *deliberately* pinned to UTC by leaving the field blank (declare `UTC` explicitly to keep it). A zone that is present but **not** an IANA name is now refused where it is authored — `POST /api/triggers` rejects `"timezone": "CEST"` with a message naming the abbreviation trap instead of arming the row 2 hours off, `personalclaw setup` re-prompts, and a hand-edited row reports "will not arm" rather than firing at the wrong time. Six functions had each derived their own answer to this question and disagreed (`schedule.get_local_tz` returned `UTC` on a PDT host and is what the Triggers and Calendar pages report as `server_tz`; `triggers/calendar._resolve_zone` fell through to server-local while `triggers/arm._trigger_tz` fell through to UTC, so the week grid struck a different calendar column than the engine skipped; `knowledge/report_schedules._effective_tz`, written specifically to compensate for the UTC default, resolved to `"UTC"` itself on a stock install and so compensated for nothing). All of them now call one owner, `personalclaw.timezones`, with a lint rail that reds when a seventh resolver appears ([#2520](https://github.com/PersonalClaw/PersonalClaw/issues/2520)).
- **The eval report can now tell "not recorded" from "recorded as zero/none"** in the three places it could not, and it says so in one vocabulary rather than three. `REPORT_SCHEMA` moves to `2` — ES-17 added `provider_binding` and left it at `1`, so a consumer holding a report with no such key could not tell "provenance was never recorded" from "provenance was recorded and says nothing was bound"; consumers now read the schema the report STATES, and the dashboard's `'provider_binding' in report` workaround is gone with it. A benchmark cell reports `tokens: null` (never `0`) plus `tokens_recorded`/`unrecorded_attempts` when its provider omitted its `usage` block — Ollama's OpenAI-compatible endpoint does this intermittently, and the cell used to come back `{observed: true, attempts: 2, tokens: 0}`, feeding the §4 token-ratio metric a silent zero while the run looked complete. The run-level ratio now REFUSES (`token_ratio: null`, verdict `tokens_unrecorded`) instead of averaging across cells that could not report, the report carries run-level `tokens_recorded`/`unrecorded_spend_cells`, and the Loop-2 gate's token total refuses the same way (its dollar ceiling is unaffected — `dollars_est` is a real estimate over real attempts, and only the token count is absent). The pin gains `cell_model_fingerprint`/`cell_model_fp` naming what the run's CELLS could reach, beside the existing `model_fp` which names the invoking HOME's binding — measured on `main`, a bound run and a run where every cell failed with `ProviderResolutionError` carried the identical `model_fp` `5970c589da34` — and `results.tsv` gains an appended `cell_model_fp` column so a ledger row can tell an offline run from a real-model one. Class B/S, clean break under the pre-1.0 banner: no migration, an existing report/pin/ledger row simply reads as `unrecorded`, which is the honest answer for an artifact written before the fields existed. `personalclaw snapshot` before upgrading is advised as usual ([#2540](https://github.com/PersonalClaw/PersonalClaw/issues/2540), [#2561](https://github.com/PersonalClaw/PersonalClaw/issues/2561), [#2562](https://github.com/PersonalClaw/PersonalClaw/issues/2562)).

### Removed
- Removed `docs/launch/` from the repository. All four files declared themselves
  `**State:** draft, internal, not published` in their own headers while being served from a
  public repository, and none was linked from any published page. One additionally published
  the scope set of a live access token. A `publication-hygiene-baseline.json` path rule now
  refuses the directory's return.
- **⚠️ Four remaining runtime-editable config paths that governed nothing are gone: `workflows.max_active_runs`, `knowledge.conflict_model_pass`, `knowledge.lint_every_n_persists`, and `learning.min_session_score`.** [#465](https://github.com/PersonalClaw/PersonalClaw/issues/465). Each validated, persisted and read back while no reachable feature consumed the value. Removing `max_active_runs` adds no concurrency cap and changes no run-start behaviour; that admission gap remains separate work. The knowledge toggles named schedulers or switches that do not exist, and the learning score path would require building its producer before a config threshold could govern anything. Stored values are ignored on load. This completes the ruled six deletions together with `knowledge.idempotent_persist` and `workflows.max_concurrent_nodes`. Clean break under the pre-1.0 banner: run `personalclaw snapshot` before upgrading.
- **⚠️ Two earlier runtime-editable config fields that governed nothing are gone: `knowledge.idempotent_persist` and `workflows.max_concurrent_nodes`.** [#465](https://github.com/PersonalClaw/PersonalClaw/issues/465). Both validated, persisted and read back cleanly through `PATCH /api/config/personalclaw` while no code anywhere read the value — so the round trip convinced you the setting had taken effect and nothing changed. Neither is replaced, because in both cases the honest fix was deletion rather than a reader. `idempotent_persist` is an **invariant, not a setting**: content-derived identity prevents retries and rewinds from manufacturing a second near-identical item that later reads as independent corroboration. `max_concurrent_nodes` claimed to be a total partitioned across typed lanes, while the two live per-lane fields already own that quantity. Stored values are ignored. The four-path entry above completes the ruled six deletions.
- **Chat's Activity → **Index** tab is gone; the Session Map is the session's index (SSM-13).** The Activity panel opened on an "Index" tab — a flat outline of your own messages whose rows scrolled to that turn — and the Session Map superseded it: the map is always on screen rather than behind a panel, and it marks tool calls, approvals, errors and subagents as well as user turns, so it indexes the parts of a long session you actually go looking for. Keeping both would have been two indexes of one transcript that have to be kept saying the same thing, so the tab, its list body and the `ChatActivity.index` model behind it are **deleted** rather than hidden or deprecated — there is no flag to bring it back. **What this changes for you:** the Activity panel now opens on **Files**, and its tabs are Files / Links (+ Subagents and Side when in play); to jump to a turn, use the map's rail at the right of the transcript (or its drawer on touch) — click a mark, or Tab to the rail and use the arrow keys plus Enter. Nothing is lost in the move: the previous release already routed the Index tab's jump through the map's single handler and coordinate (SSM-12), so the rail lands the same turn the outline did, and the map additionally lists every user turn the outline listed. No config change and no data change — this was a view surface, so nothing is stored differently and nothing needs migrating.

### Fixed
- **An unreadable spend ceiling no longer reads as an unlimited one, and the four unattended seams now refuse rather than spend against an unknown.** [#3458](https://github.com/PersonalClaw/PersonalClaw/issues/3458). `budget_from_config` swallowed every config-read failure and answered `Budget()` — which `is_unlimited` — so a `config.json` the operator *had* set `guardrails.budgets.max_tokens_per_day` in silently lost that ceiling on the one path an unattended loop spends real money through. An unknown resolved into a permission, the same shape as #3456 and #3457. **Driven, not inferred:** a real config whose `max_tokens_per_day` is the string `"lots"` makes the schema validator log the type mismatch and then `int('lots')` raise out of `AppConfig.load()`, and `budget_from_config()` answered `Budget(max_tokens=0, max_dollars=0.0)`. **There is no restrictive number to substitute** — `0` means unlimited, so a "safe default" would be a ceiling nobody chose — so the builder now refuses to answer (`BudgetConfigUnreadable`) and each consumer decides what the unknown means at its own seam. `proactive/autoexec.py` had already made that decision in prose (*"an unverified ceiling authorises nothing"*) and **could not act on it**: the swallow handed back a budget that reads as *under* the ceiling, so the written refusal took the happy path. It now fires, with no change to its own code. The subagent spawn, the subagent fan-out's run ceiling and an app worker's supervisor sweep follow it — each refusing with a sentence naming the unverifiable ceiling — and the gateway's pre-dispatch gate for **every** clock, file, webhook and chained fire pauses and emits one notification instead of dispatching. `action_providers/browse_provider.py` keeps its documented fail-open, now named as a decision rather than an accident: it is consulted inside a loop whose every model call already passes `ModelCallGuard`. **A spend-counter failure keeps every one of those fail-opens**, asserted — this is about the operator's decision being lost, not about any bookkeeping error — and an *absent* config is still legitimately unlimited, also asserted, because conflating "set nothing" with "could not read" would invent a limit nobody chose.
- **First run no longer tells you your name is saved before it has been saved.** The name step's subtitle read *"Saved on the server, so it follows you across devices"* while the step was still being filled in — that is Settings → Account's sentence, where it is true because that panel writes on change. In first-run setup the identity write is deliberately the LAST thing the flow does (`finish()`, so the server name stays the single source of `onboarded` rather than a second copy in the resume state), which made the past tense a claim about a write that had not happened — on the product's very first screen. Measured on a fresh container installed from the published wheel: typing a name and pressing Continue issued **zero** write requests, `GET /api/onboarding` still answered `step: "name"`, `localStorage` held nothing but `nav-collapsed`/`appearance`/`mode`, and a reload came back to step 1 with an empty field — all after the collapsed step row had already shown `Ada Lovelace · @ada-lovelace` back as a completed step. It now reads **"Saved when you finish setup, so it then follows you across devices"**: the same promise, with the moment it is kept stated instead of implied. The deferred write itself is unchanged — it is the deliberate design, and the fix is to stop overclaiming it rather than to add a second place the name lives. A test pins the copy against that timing in both directions, so if the write ever moves earlier the sentence is required to move back with it.
- **The subagent cwd-refusal test no longer cries "a guard stopped auditing" roughly half the time it runs in CI.** [#3425](https://github.com/PersonalClaw/PersonalClaw/issues/3425). `test_spawn_with_invalid_cwd_rejects_and_emits_sel` identified its subject by `"spawn refused" in info.error` — a prefix the memory, incident and budget gates *also* produce, each of which returns a done `SubagentInfo`, leaves `_running_count` alone, and logs a **different** SEL outcome. So when an earlier gate answered, all four of the test's leading assertions still passed and only the audit-row check reddened, with `assert 0 == 1 + where 0 = len([])` — which reads exactly like the genuine defect class "the guard refuses correctly but stopped emitting its audit row", on diffs that could not affect it. The precondition is now explicit (`_only_the_cwd_gate_can_fire` neutralises all three earlier gates, so the cwd gate is the only one that can answer), and the primary assertion is the **whole** list of logged outcomes rather than a filtered count — an earlier gate now reds by naming itself instead of reporting a row as missing. The `info.error` check got stricter, not looser: it asserts the cwd gate's exact message. A new negative control makes all three earlier gates hostile at once and requires the cwd outcome to still be the only one logged, so the neutralisation cannot silently decay, and a fourth gate added ahead of the cwd check cannot re-introduce the ambiguity. No product code changed — the emission at `subagent.py:1172` is unguarded and on the same straight-line block as the `error` the test already observed, so "refuses but stops logging" was never structurally reachable. Side effect worth having: the test no longer meters the real home's `spend.json`.
- **The Inbox no longer tells a fresh install its inbox “is not connected yet” while its own banner says the native source is active.** On a fresh container `GET /api/inbox/status` returns `{"enabled": false, "native_source_active": true}` — `enabled` covers the **poll** providers only, and `handlers_inbox.py` says so at the site ("the native source is ALWAYS active (push-based agent→inbox sink); the poll-based providers run only when cfg.inbox.enabled"). The blank slate read `enabled` anyway, so `#/inbox` rendered the headline **“Inbox is not connected yet”** with **“Enable a source to begin”** and a **Connect a source** button directly beneath its own **“Native source active — agents post here directly”** status banner; the header’s **Capture a note** then delivered an item into that same inbox with `enabled` still `false`. Nothing 4xx’d, the console was clean and the gateway log had no traceback — the page was handed the truth and rendered its opposite, which is the confident-empty-state family with the swallow removed and the fabrication left in. The axis was wrong, not the flag: a never-used inbox and a cleared one differ on whether anything ever **arrived**, so the blank slate now branches on `neverReceived` (read off the items list this branch already has, which also removes the unknown-status gap the previous rail left open) and reads **“Nothing has arrived yet”** with copy that explains agents post directly and offers a filesystem/Slack source as an extra. **“Inbox zero” survives for the one state where it is true** — a queue the user cleared — and still carries no call to action. The source offer is now withheld when a poll source is already collecting, which the old gate could not express.
- **Prompt-cache counts now survive the native runtime, which is why every ledger row read a structural zero.** [#3434](https://github.com/PersonalClaw/PersonalClaw/issues/3434). All three terminal `AgentEvent` constructions in `agents/native/runtime.py` omitted `cache_read_tokens` and `cache_creation_tokens`, and the file contained **zero** references to either field name. The counts were produced correctly by the adapters and dropped one layer up, for **every** provider including Anthropic — measured: `input_tokens=77` / `output_tokens=1` arrived intact while a provider-reported `cache_read_tokens=4224` arrived **0**. That absence had been attributed to "Bedrock declined the checkpoint", a much narrower explanation; the real cause is provider-independent, and a structural zero is indistinguishable from "caching is not working", which is how it stayed invisible. The runtime now accumulates both counts across a turn's inferences and puts them on all three terminal events (the normal exit, the max-turns exit, and the cancelled exit — a stop between ReAct cycles used to discard the cached half of what it had already spent). **Accumulating, not assigning, is load-bearing:** `input_tokens` on that same event is a turn total and every consumer reconstructs the whole served prompt as `input + cache_creation + cache_read` (`stats.py:160`, `pricing.py:169`, `llm/openai.py:444`), so a last-inference-only cache count would divide a whole-turn numerator by a single-inference denominator. **The single-store AST rail was widened deliberately, not worked around:** `tests/test_cache_counters_single_store.py` forbids any cache-named accumulator, so the runtime is now a named exemption beside the existing read-side one, and it is floored by a test that reds three ways — if the module stops accumulating (stale exemption), if an accumulator becomes `self._x` or a subscript (it could then outlive the turn, which is the long-lived tally the rail exists to forbid), or if it stops summing `input_tokens` in the same function. Renaming the locals to dodge the scanner was rejected: it would leave the invariant unchecked while looking checked. Pinned by tests spanning a vendor-shaped usage object through each adapter's real `_read_cache_usage` to the terminal event, for both the Anthropic and OpenAI dialects, each parametrised over a non-zero and a zero row so neither a structural zero nor a fabricated constant can pass.
- **`npm run typecheck` never read a single Playwright spec, and the first run that did found a real defect.** `web/tsconfig.json` is `include: ["src"]` and `web/tsconfig.sw.json` covers only the service worker, so **fourteen** files — the thirteen `.ts` files under `web/e2e/` plus `playwright.config.ts` — were outside every typecheck program and a type error in a spec shipped silently. One was shipping: `walkthrough.spec.ts` built its skip message from `opener.skip`, reading the property off the *recipe* object instead of off the `OpenResult` the recipe returned, so **every skipped keyboard-walkthrough test reported its reason as `undefined`** — in a test whose own comment says *"The recipe supplies its own reason so the report says which it was."* A new `web/tsconfig.e2e.json` covers the specs and their config and is chained into `npm run typecheck` exactly as `tsconfig.sw.json` already is, which puts it in CI's `typecheck:web` step with no workflow edit. It is a separate program rather than a widened `include` because the specs are not Vite inputs and resolve the Playwright runner's types, not the app's. Guarding the SCOPE rather than just the result, `tests/test_web_typecheck_covers_every_spec.py` walks every tracked `web/**/*.ts(x)` file and asserts each falls inside some program — the hole existed because *nothing asserted what the gate looks at*, and a passing typecheck only ever says the files it opened were clean, never that it opened the ones that matter. Three files remain outside, each named in that test's exclusion table with its measured cost rather than left silent: `e2e/a11y.spec.ts` (five `TS2322`s because two copies of `playwright-core` resolve in this tree — 1.62.1 hoisted for `@axe-core/playwright`'s peer range, 1.63.0 nested under `playwright` — so `AxeBuilder({ page })` declares a `Page` from one and is handed one from the other; `npm dedupe` clears all five but rewrites `package-lock.json` by ~6900 lines, which is a dependency change, not a typecheck change), plus `vite.config.ts` (two `TS7016`s from untyped `./scripts/*.mjs` imports) and `vitest.config.ts` (one `TS2769`). The gate was proven non-vacuous by restoring the original defect: `tsc -p tsconfig.e2e.json` exits 2 naming `walkthrough.spec.ts(306,86)`, and exits 0 with the fix.
- **Two update-path tests no longer fail on the act of cutting a release — both were rigged to break at the moment the thing they guard reaches users.** They were the last mechanical blockers on the v0.2.0 cut, and neither described a product defect. The first, `test_pip_installs_the_channel_pin_resolved_spec`, drives the wheel updater over a fixed releases list and asserted that pinning `0.2.0` installs `personalclaw==0.2.0` — but it inherited the project's own `__version__` as an unstated precondition, and `_update_pip` correctly declines to reinstall the version already running. So the row passed only while the project was *not* 0.2.0, and every row carried the same landmine at a different future release (the stable row at 0.2.1, the beta row at 0.3.0). Bumping the literal would have moved the landmine one release along, so instead the running version is now pinned in the fixture, below every tag in the list, which removes the project's version from the test altogether; the precondition is **asserted** per row, so a future collision reds with `assert '0.2.0' != '0.2.0'` at the fixture instead of an opaque `spawns=[]` at the end, and a new floor test holds the three rows at three distinct versions so the non-vacuity the docstring claims cannot quietly decay. The second, `test_changelog_records_the_main_tracking_to_release_tracking_flip`, required the class-B entry for the RUM-1…11 update flip to sit in `## [Unreleased]` — the one section a release cut empties by definition, moving its contents into `## [X.Y.Z]`. The assertion is now about the entry **existing**, in whichever released-or-unreleased section holds it, and is scoped **tighter** in exchange: the `personalclaw snapshot` advice and the four `updates.*` field names are required in the flip entry itself, where a reader meets them, rather than anywhere in a section that also holds dozens of unrelated entries — which is what the old code comment claimed without enforcing. No product behaviour changed, and the open product question the first test brushes against — whether pinning your already-installed version should reinstall it — is deliberately left unanswered.
- **A browse run that got stuck or was refused by the egress policy now explains itself in a sentence instead of printing a reason code.** `_park_sentence` documents itself as "the exhaustive projection of the park vocabulary" and warns that "a park reason with no sentence is a leaked identifier on a product surface" — but two of the seven reasons had never had one, so a parked run has been telling users *"Browse stopped early (stuck)"* and *"Browse stopped early (navigation_blocked)"*. Both now read as sentences, and a rail walks the module's own `PARK_*` constants so the next park reason added without one fails a test instead of shipping. Found by the BA-10 vision-grounding rail, which needed the same projection for its own two new reasons.
- **First-run setup no longer tells you a chat model is ready when chat cannot use it, and when it cannot, it names the actual reason.** The model step reported "ready" off `GET /api/onboarding`'s `needs_model`, which is derived from a deliberately **no-instantiate** probe (`can_resolve_use_case` — it also backs every workflow preflight and runs on a hot GET, so it may not build anything). That probe answers "resolvable" the moment `active_models.json` holds a ref for the use case, *without checking the ref still resolves* — and writing that ref is the **last thing the step does**, so the step was reading its own write back as proof. Measured: on a home whose `config.json` carries `{"name": "my-openai", "type": "openai"}` with no installed app registering that type, plus a `chat` binding to it, `needs_model` is `false` — a green *"A chat model is configured — you're ready"* and an enabled Continue — while real chat resolution raises `ERR_MODEL_UNRESOLVED`. A declaration and a build are different claims, and every cause that matters here (a ref naming a provider `config.json` no longer has; an entry present but not registered in the running gateway; a type with no factory because no app claims it, or its app is installed but disabled, or it failed to load; a capability that does not cover the use case; a credential with no secret) fires at build time, where that probe cannot see it. **What changes for you:** the step now runs the build before it reports anything — `GET /api/onboarding/model-check`, which resolves a chat provider exactly as chat does and shuts it down again — so the green tick, Continue and the done-screen recap are all reached only by a model that actually built, on every entry path (a re-entered flow, a keyed provider you just configured, and the one-click local-Ollama bind alike). A refusal shows the **bridge's own** WHY and FIX, relayed field for field rather than paraphrased, so the number of causes you can tell apart is the number the backend can tell apart; "the check could not run" is reported as its own third state rather than as a broken model; and "Set up later" stays on screen throughout, so a broken home is never trapped on the step. Two narrower fabrications went with it. A provider test answering `{ok: true, status: "no_probe"}` — which means *nothing was tested*, because the type registers no catalog — was being treated as a passed connection test, so the step promised it had tested "for real" when it had not; it now says what saving does do, and where nothing could be tested it says the model list is the first evidence the provider answers. And an empty model list is no longer reported as `No chat-capable models were discovered for this provider`: `GET /api/models/chat` gathers catalogs with `return_exceptions=True` and drops a raising provider silently, so a wrong key, a dead endpoint and a provider that genuinely offers no chat model all arrive as the same empty array (measured: an `ollama` entry pointed at `127.0.0.1:19999` yields `[]`). The step now asks the provider's own connectivity probe and reports which of the three it is — *reached and offering nothing*, *could not be reached* with the server's reason, or *un-probeable, so an empty list proves nothing* — and when the flow never learned which provider is configured it states both possibilities instead of picking one.
- **A credential typed into a project name no longer reaches the download filename, and download filenames now carry non-ASCII names instead of dropping them.** Exporting a project served `Content-Disposition: attachment; filename="personalclaw-project-<your project name>.zip"` with the name interpolated raw — no redaction — so a secret pasted into a project title left the machine a second time, in proxy logs and browser download history, where deleting the archive never reaches it. The archive *body* carrying secrets is deliberate and unchanged (the response still declares `X-PersonalClaw-Secrets-Expected`); only the header was wrong. The cause was that five download routes each composed this header themselves in three incompatible conventions, so the sibling thirty lines away was already redacting correctly: `personalclaw/http_download.py` is now the single emitter and all five call it. It redacts the name, emits RFC 6266's `filename*=UTF-8''` beside an ASCII `filename=` fallback, and closes header injection by rebuilding that fallback from an allowlist. Two visible consequences: a chat or project named `日本語のチャット` or `Café` now downloads under its own name rather than as `chat.json`, and transcript filenames keep the title's capitalisation (`My-Chat-Q3-Review.md`, not `my-chat-q3-review.md`) and fall back to the session key rather than a generic `chat` when a title has nothing usable in it. A new `content-disposition-header` duplication ratchet holds the count of hand-rolled emitters at zero.
- **`DCU-3`'s live stale-index validator can now get past its own first assertion, and it no longer leaves TextEdit on the operator's screen when a clause fails.** `scripts/dcu3_stale_index_validate.py` had never run past `_preflight()` on any host — every recorded attempt stopped at the absent macOS Accessibility grant with `attempts: 0` — so **none of its assertions had ever executed**, and the first one to run was wrong by construction. `_write_by_index`'s guard compares the driver ops an acting dispatch caused against `["snapshot", "set_value"]`, but nothing reset the op window before the dispatch, so it also counted the launch-wait poll and the caller's own `computer_snapshot`: unreachable at every call site, and off by an amount that varied with how long TextEdit took to launch. The first run to reach it reported `['snapshot', 'snapshot', 'snapshot', 'set_value']`. The drain now happens immediately before the acting dispatch, exactly as `_expect_stale` already did it. Second, teardown sat at the end of the happy path rather than in a `finally`, so that same failing run left TextEdit running with an unsaved scratch document on a real desktop — it is now a `finally`, and quitting is still scoped to `launched_by_this_run` so an app the operator already had open is never touched. Third, the past-TTL leg's launch wait asked only "is any window walkable", which is true the instant a *previously* opened document is on screen; it now waits for the document it just opened to actually be in front. Fourth, that leg's non-vacuity premise — "nothing but age changed across the sleep" — was measurably false for a reason its own comment had not anticipated: the leg opens a second, unedited document precisely so the *edited* one cannot retitle itself mid-sleep, but TextEdit **autosaves** the edited document about twelve seconds later and that brings it back to the front, so the leg woke up walking a different document (measured: front window held `B` for ~9s, then flipped to `A` for the rest of the 31.5s sleep). The edited document is now taken out of the picture before the clock starts — the app is quit, which is what discards the unsaved marker, and relaunched with the TTL leg's document as its only window — and when the run did *not* launch the app it refuses that leg by name instead, because quitting would discard the operator's unsaved work and their open documents make the premise unestablishable anyway. Both new behaviours are pinned by rails that were falsified: removing the drain reds `test_the_acting_helper_ignores_driver_ops_from_before_its_own_dispatch`, and moving teardown off the `finally` reds `test_teardown_runs_when_a_clause_fails`. No product code changed; this is the harness, and `DCU-3`'s live clause stays open on a remaining environmental precondition (a quiescent desktop — a window's own title bar collapses from 13 elements to 12 when it loses focus, which the fingerprint correctly reports as a changed window).
- **`personalclaw config --help` no longer advertises a key the command refuses, and a rail now holds every key it advertises to that standard.** [#3395](https://github.com/PersonalClaw/PersonalClaw/issues/3395). The worked example on three separate screens — the `config` epilog plus the `key` help on both `config get --help` and `config set --help` — was `dashboard.port`, and both of the epilog's own commands exited **1** with `❌ Unknown key: dashboard.port`. The key has never existed: `DashboardConfig` has 22 fields and no `port` among them, so a first-time operator following the command's own example met a refusal. It was not a rename, either. **The persisted gateway port already ships, under a different name**: it is the port inside `dashboard.url`, which `parse_dashboard_url()` parses and `--port` then overrides (`gateway.py`'s `_init_dashboard`). So the four sites now name `dashboard.url`, which is both a key that resolves and the honest port-shaped example. A real `dashboard.port` field was considered and **deliberately rejected**, on three measured grounds rather than on effort: it would be a second config-file answer to a question `gateway_base` is explicit about owning once (*"Two independently-derived answers that can disagree IS the defect, so no second resolution path is added"*); `dashboard.url`'s port is load-bearing for the browser **Origin allowlist**, so a separate port field would let the bound socket and the allowlist disagree silently rather than loudly; and the session cookie is port-scoped (`pc_token_{port}`), so the field could not be runtime-editable like every other `dashboard.*` key and would be the block's first knob that lies until a restart. **The rail is the agreement, not the key** — the specific fix would otherwise decay exactly as this one did, since [#3135](https://github.com/PersonalClaw/PersonalClaw/pull/3135) edited the very lines between the two broken ones and shipped them. `tests/test_cli_help_surface.py` now extracts every dotted config key the `config` subtree's help renders and resolves each against the same `AppConfig.to_dict()` the command resolves against, calibrated both ways: the extractor must still *see* `dashboard.port` written the way the epilog wrote it, and the model must still judge it dead.
- **`personalclaw config set --file` no longer reports `✅` for a deletion it did not apply, and `config unset` gives removal a path at all.** [#3125](https://github.com/PersonalClaw/PersonalClaw/issues/3125). This path preserves top-level blocks the submitted document does not name, which is what stops a handed-back file deleting `providers[]` by omission ([#951](https://github.com/PersonalClaw/PersonalClaw/issues/951)) — and is exactly why an operator who deleted the legacy `slack` block on purpose was told `✅ Config loaded from g.json` at **exit 0** while `bot_token` stayed on disk. The security consequence is the point: a user who removes a credential through the documented round-trip was told they had succeeded. There was also no escape hatch to point them at — `unset`, `--replace` and `--remove` all grepped to **zero**, so no supported way to delete a block existed. One signal cannot mean both "leave this alone" and "delete this", so the two jobs are now separate. `--file` keeps preservation and **refuses** (exit 1) when the document omits a block that is in `config.json`, naming every block it could not apply and pointing at the verb that does remove one; the documented `config get --reveal > f.json` round-trip is unaffected, because `config get` prints the merged view and therefore names every top-level key on disk. `config unset KEY` removes a dotted key or a whole block, and **refuses a key that is not in the file** rather than reporting success — `config unset slak` answering `✅` while `slack` survives is the same false-success defect with the same consequence. Deliberately one verb and not also a `--prune` flag: two spellings of removal is the bug family this module already carries scars from. The mask-resolution refusal keeps precedence over the new one, since an unresolvable `••••••••` would destroy the only copy of a credential and earns the more specific message. **Retired in the same change:** `--file` used to accept a *partial* document, applying its keys and silently keeping the rest. That overlay behaviour was the ambiguity itself, `config set <key> <value>` already covers changing one key (with validation `--file` does not run), and the test that pinned it now pins the refusal. Also folded in: the print/audit/exit triple behind all five refusals here is written once, because the audit row is the part a sixth would forget — an unaudited silent refusal is indistinguishable from a working write. Separately, `config get`'s credential masking (fixed in #3135) gains the rail it was missing: every existing case drove `api_key` or `bot_token`, so all of them would have passed against a hard-coded pair of names. A new case proves the property instead — a credential-shaped field in a block **no list in the repo names** is masked, with a non-secret sibling in the same block asserted to survive in the clear, so "mask everything" cannot pass it either.
- **The packaged desktop app carries its data files, its `sdk.*` submodules and its own `personalclaw-core` MCP server.** Driving the shipped macOS bundle found five defects that a green suite could not see, because every one of them exists only in the artefact: the suite runs against the source tree, where the missing files are on disk. `personalclaw-backend.spec` hand-transcribed the wheel's `[tool.setuptools.package-data]` globs into its own `datas` list — a copy of a growing list, and it had drifted **eleven of thirty**, so the installed app had no `agents/runner_catalog.json` (Settings → Agents rendered no runner rows and had no adapter to gate an unattended spawn on) and no `tool_providers/rules_builtin.json` (the builtin tool-projection rule pack was skipped), each logging a `FileNotFoundError` the user never saw. Three of the spec's own patterns named paths that **do not exist** (`eval/scenarios` — the directory is `evals/`; `slack-manifest.yaml`; `scripts`), silently dropped by an `os.path.exists` filter, so a dead pattern and a live one looked identical. Separately, an app imports core **only** through `personalclaw.sdk.*` and `providers.registry` resolves that import at enable time via importlib, which static analysis never sees — so the packaged app could not enable a single one of the four extensions it shipped (`brave-search` → `sdk.search`, `voice-clone-tts` → `sdk.tts`, `telegram-channel` → `sdk.channel`, `discord-channel` → `sdk.trigger_source`, all `ModuleNotFoundError`): the app platform was entirely non-functional in the packaged build. And the agent resolves a `personalclaw` console script to launch its own core MCP server; an `.app` has none and no interpreter to put one beside, so the resolution fell through to a bare name, `personalclaw-core` was **dropped**, and the assistant simply could not do things — one WARNING as the only evidence. **The spec no longer keeps its own copy.** `scripts/backend_bundle_manifest.py` derives the bundle's payload from the same `package-data` declaration the wheel is built from, and the SDK module list from the directory, so declaring a data file for the wheel now reaches the frozen bundle by that act alone. The agent asks whether it is frozen and uses `sys.executable`, which in a bundle *is* the CLI (its entry script is `personalclaw/__main__.py`); before this, nothing under `src/personalclaw` asked that question anywhere — a `grep` for `sys.frozen` returned zero, which is why this whole class stayed invisible. **The rail is the point:** `tests/test_backend_bundle_manifest.py` asserts the derivation is complete without running PyInstaller (a bundle build is minutes, and CI installs PyInstaller only in `release.yml`'s two desktop jobs) — set equality against an independent re-glob of pyproject, no vacuous glob, no undeclared non-Python asset anywhere under the package tree, every SDK submodule collected, and the spec actually consuming it. It found a sixth defect on its first run, in the other direction: `native-knowledge`'s `app.json` names four `prompts/*.yaml` files that **no** `package-data` glob carried, so `pip install personalclaw` shipped that app pointing at prompts the wheel does not contain — the frozen bundle had them only by the accident of copying a whole directory, which is the accident that hid it. Per-file rather than per-directory `datas` also stops fifteen `personalclaw.config.*` / `*.bundled` modules being shipped twice, once as modules and once as data.
- **"Check for updates" no longer runs `git fetch` inside an app bundle.** The update check treated a set `PERSONALCLAW_PROJECT_DIR` as proof of a git checkout, and the Electron shell sets it to `…/Contents/Resources` inside the bundle, which carries no repository — so on a dmg install the check shelled out and failed, twelve times in one session, each logging `git fetch failed (rc=128): fatal: not a git repository`. It now asks `self_update.detect_install_kind()` first, the same decision `POST /api/update` already dispatches on, and the non-git kinds take the release-tag comparison that was always there. The install-kind taxonomy also answers from the **artefact** now: a frozen process reports `desktop` without needing `PERSONALCLAW_INSTALL_KIND` set, so the correct refusal ("download the new version") no longer depends on an environment variable being present — with the env unset a frozen bundle used to answer `pip`, whose apply path pip-installs into `sys.executable`, which inside a bundle is the PyInstaller launcher with no interpreter to upgrade.
- **The config is validated once per file content instead of once per request, and three retired keys are consumed instead of reported.** 1144 of ~2000 lines in one browsing session of `gateway.log` were the same warning, `Config: unrecognized top-level keys: auto_update` — which is how eight `FileNotFoundError` tracebacks in the same window were easy to miss. A warning repeated a thousand times is not a louder warning; it is a quieter log. Two independent defects produced that number and fixing either alone leaves the other. First, `auto_update` was **half-known**: `AppConfig.load_with_migration_state` read the retired key as a RUM-1 legacy-backfill input while `config.validation`, one call earlier, had never heard of it — so every load both migrated the home correctly *and* reported its config as containing an unrecognized key. The fold now happens in the validator, where the repo's retired-key vocabulary already lives, and the key is **consumed and removed** rather than allowlisted into permanence, so the next `save()` rewrites `config.json` without it. Reviving `auto_update` as a dataclass field was the other option and is the wrong one: `updates.auto` already carries the full config round-trip, so a second field for the same setting would be exactly the dual path the tenets forbid. Its sibling `dashboard.update_dev_mode` moved with it so the vocabulary is not split across two files, and the retired `inbound` section — replaced outright by `external_access` with no back-read — joins them; that third one was found by the new rail, once `auto_update` stopped warning and the shipped `six-month-home` fixture still logged a key. An explicit `updates` field still wins over a legacy flag. Second, `AppConfig.load()` is a pure read called from ~300 sites and it re-ran jsonschema over the whole sixty-section schema **and re-logged every finding** each time; it is now memoised on a digest of the file's raw text, so a write invalidates it with nothing to remember to call. The memo holds one entry and stores only the field *paths* the pass stripped, never a value, so a cache on the config path cannot become a place config secrets accumulate — and a cache hit still applies those strips, so a second load can never read an invalid value where the first read the default.
- **Inbox, Apps and Settings reach a terminal state on a first run instead of spinning forever; the onboarding page scrolls; and a structured tool argument no longer kills a subagent from inside the approval path.** Four first-run defects the product owner hit on a fresh install of the packaged app. (1) **The spinner had no terminal branch for a fetch that never SETTLES**, and the error contract was defeated one layer below the hook: `useQuery` derives `loading` as `data === undefined && inFlight`, so a promise that never settles never clears `inFlight` and never sets `error`, making every call site's failure branch structurally unreachable. Measured against a gateway on a fresh home — with every `/api/**` read answered by a hard 500 all three surfaces already showed a legible error and a Retry, and with the same reads *held open* every one of them span forever: Inbox on "Loading inbox items…", Apps on "Loading apps…", Settings on 34 `aria-busy` regions and 72 skeletons, still going at 15s. `lib/data/store.ts` now bounds the READER's wait (8s, under the 10s terminal-state bar) rather than the request: the deadline does not abort the fetch and does not drop it from the dedup table, so a slow-but-successful response still lands and repaints — and `useQuery` clears a reader's error when a value lands, so a late arrival is not hidden behind a stale "not responding". Two remaining `(B)`-class sites in the same surface were fixed at the site as [#3394](https://github.com/PersonalClaw/PersonalClaw/issues/3394) prescribes — bind `error`, delete the swallowing fallback: Settings → Models read `api.modelsAvailable().catch(() => [])` + `api.modelsActive().catch(() => ({}))` and gated on `data` alone, so a failed read painted "No models discovered" (a confident claim about a page that never loaded) and a hung one kept its skeleton; Settings → Chat's muted-agents field sat on "Checking…" with no error bound. (2) **The onboarding page could not be scrolled to either edge**, and `overflow-y-auto` was already present — which is what makes it look impossible until measured. `items-center` on the scroll container distributes overflow to BOTH ends and the part past the start edge is unreachable (`scrollTop` clamps at 0 and `scrollHeight` does not count it), and the hero was `absolute bottom-full`, out of flow above the flow origin. Measured at 1280×700 on the longest step, before: at `scrollTop: 0` — already the top of the range — the panel's top sat at -96.5px and `<h1>Welcome to PersonalClaw` at -201.5px, while "Skip setup" ended at 800.5px; after: h1 at +140 and the last control at 676, and at 1024×600 h1 at +140 and the control at 576. The centring moved to a `min-h-full` box inside a plain block scroller, which keeps the centred look while there is room and degrades to top-aligned-and-scrollable when there is not, and the hero is in flow. (3) **A security control raised `TypeError: expected string or bytes-like object, got 'dict'` out of `scan_exfiltration_urls` and took the whole subagent down through the approval path.** `DashboardState.request_approval` declared `tool_input: str`; the approval path carries `AgentEvent.tool_input`, typed `Any` because the native loop puts a dict there, and the gateway handed it over unchanged. The annotation was a claim, not a conversion. The boundary now coerces once through the same `tool_input_to_str` the chat card's `input_preview` already used (moved to the neutral `task_modes`, beside the `extract_bash_command` that owns the same "`tool_input` is `Any`" problem), so the string that is scanned is byte-identical to the string that is shown. The posture is deliberate and stated rather than defensive: the scanner keeps its `str` contract and keeps raising on a non-`str`, because a redactor that silently accepted anything would let the scanned and displayed strings diverge; nothing is skipped, since a dict is JSON-encoded and every URL and credential inside a structured argument is now scanned, which is strictly more than before; and the screening verdict still reads the RAW value, so `is_read_only` describes the call the shell would run. (4) **A home this project SHIPS carried a config the validator rewrites on every load**, which is where the owner's 620 repetitions of `progressive_disclosure_threshold 8 is at or above max_triggered 3` came from. The shipped default is not at fault and neither is the comparison — [#3328](https://github.com/PersonalClaw/PersonalClaw/issues/3328) already changed `default=8` to `2` and added the clamp, and a gateway on an empty home writes `2` and logs this zero times (measured). `tests_fixtures/six-month-home/config.json` is package data generated from a 0.1.3 home and still paired threshold `8` with `max_triggered: 3`, so any install seeded from it reproduced the warning verbatim. The rail is over the shipped homes rather than the one knob: a config this project ships must load unchanged, with `empty` and `demo-home` as the controls that make the one hit a real reading.
- **Prompt-cache savings are now reported on OpenAI-family models, which had silently reported a flat zero on every cached turn.** `PCS-6` built the producer that reads a provider's cache counts onto `LLMEvent` — but only for Anthropic. The OpenAI adapter never got one, because "OpenAI needs no cache *marker*" was read as "there is no cache *number* to read". There is: OpenAI-family caching is AUTOMATIC, the vendor applies the discount unasked, and it reports the hit on `usage.prompt_tokens_details` (`cached_tokens` for a read, `cache_write_tokens` for a write). `llm/openai.py` read only `prompt_tokens` / `completion_tokens` at both of its usage sites, so the number was dropped one layer below the telemetry and the turn-complete line, the usage ledger and the cost rollup all showed `0` cached tokens and `$0.00` saved on prompts the vendor had already discounted. This affects **every** app riding core's `OpenAIProvider`, not just `openai-models`: `deepseek-models`, `google-models` and `openrouter-models` all declare `AUTOMATIC` and all reported zero. Both usage sites now read the nested counts through a defensive `_read_cache_usage`, mirroring the Anthropic reader name-for-name — an endpoint that does not cache, an older SDK, or a malformed value all yield `0` and never raise, so a local/Ollama-style provider keeps reporting honest zeros with its request unchanged. **Reading the field is not a straight copy, and getting that wrong would have been worse than the zero it replaced.** `LLMEvent`'s three prompt buckets are contractually disjoint — `input_tokens` *excludes* the cached span, which is why `stats.cache_hit_pct` adds all three to recover the whole prompt and `pricing.estimate_cost` bills them additively. Anthropic's wire satisfies that natively (`input_tokens` and `cache_*_input_tokens` are separate populations); OpenAI's does not, because `prompt_tokens_details` is a **breakdown of** `prompt_tokens` rather than a sibling of it. Verified on a recorded live pair — the same prefix sent twice returned `prompt_tokens=5218` on both turns while `cached_tokens` went `0 → 5120`, so had the two been disjoint the second turn's total would have fallen to the 98-token remainder. Copying the field across as-is would therefore have billed the cached span twice (a ~48% overstated turn cost) and halved the reported hit rate (49.5% where the truth is 98.1%) — corrupting the two numbers the cache work exists to prove. The overlap is resolved in the adapter that owns the dialect, so core keeps the single contract it documents, and the context gauge still measures the **whole** served prompt by reconstructing it from the three buckets, since a cached turn must not read as a smaller context: the model still saw every token. A test class pins the partition with the naive overlapping copy as a firing positive control — under that copy 11 of the file's 24 cases red.
- **A loop template's first iteration no longer dies on its own `{{last.… | default(…)}}` guard.** [#3404](https://github.com/PersonalClaw/PersonalClaw/issues/3404). Five bundled templates — `design-project`, `general-project`, `goal-pursuit-open-ended`, `goal-pursuit-verifiable` and `goal-pursuit-monitor` — write a loop-body prompt as `{{last.output.summary | default("(this is the first pass …)")}}`, the documented idiom for "there is no previous iteration yet". Every one of them failed on its **first node** with `binding failed: unresolved reference at 'last'`, because the resolver's first-cycle escape was keyed to a *different* root: it matched only `previous`, a spelling **zero** of the 32 bundled templates use. The `| default(...)` could not help, and this is the part worth remembering — a pipe runs only *after* the reference resolves, so the path walk raised before any pipe was reached. **What this changes for you:** those templates now complete their first iteration, with the default's own text in the prompt the model receives. The escape is keyed on a **positive** first-iteration signal (iteration index 0, and not a `foreach` item index, which is a different zero) rather than on the root merely being absent, so a later iteration that genuinely loses its `last` still fails loudly instead of telling the model "this is the first pass" for the rest of the loop — a prompt quietly missing its input is the one outcome worse than the failure this replaces. A loop's own `config.condition` is unaffected: it is evaluated after a body run, where `last` was always present, which is why four templates read it there without ever hitting this.
- **A binding failure no longer tells you to add the `| default(...)` pipe your expression already has.** [#3404](https://github.com/PersonalClaw/PersonalClaw/issues/3404). The remediation on a failed node — the one line a dead run gives you — was a single fixed string for every binding failure there is: *"check the referenced node id and field exist, or add a `| default(...)` pipe if the value is genuinely optional."* For an unresolved reference that advice is not merely unhelpful, it is closed: the validator one module over already documented that a default cannot rescue an unsatisfiable path. So an author who had written the guard was told the guard was the missing fix. The remediation now comes from the raise site, which is the only place that knows which failure this is, and it says what the missing root **holds** (`last` → "the previous iteration of the loop this node is in") rather than sending you back to the spelling — because a missing root is a *context* error, and no amount of checking the path surfaces it. A misspelled root points at the vocabulary instead, and a missing leaf under a resolved value gets the one case where `default` really is the tool: it covers a path that resolves to null, not one that is absent.
- **A fan-out's `[i/total]` marker reports the item count on both surfaces that render it.** [#3403](https://github.com/PersonalClaw/PersonalClaw/issues/3403). The denominator existed so that "a fan-out of twelve does not render as twelve identical rows", and both surfaces carrying it derived it independently and got two different wrong answers. The **run's node list** counted instances sharing a spec path, which strips every iteration marker — so a three-item fan-out inside a two-iteration loop reported **6**, across six rows that each claimed to be item 1–3 of 6. The **live event stream** counted the instance map at the moment a node was dispatched, when it holds only the items started so far — so a twelve-item fan-out streamed no marker at all, then `[2/2]`, `[3/3]` … `[12/12]`, a denominator that carried no information whatsoever and that the run view preferred over the snapshot's. There is now **one** denominator: the resolved item count, computed where the items are resolved, carried to dispatch and persisted on the instance, so both surfaces read the same number and a reload shows the same marker. **What this changes for you:** a twelve-item fan-out reads `[1/12] … [12/12]`, and a fan-out inside a loop reads `[1/3] [2/3] [3/3]` on every iteration instead of `[1/6]`. A loop's own iteration coordinate still counts — nothing knows how many iterations an `until_dry` loop will run until it stops — but it now counts only its own expansion, so a nested fan-out no longer inflates it and it no longer inflates the fan-out.
- **Your local models no longer disappear when you delete one of two endpoints of the same model provider, and a healthy endpoint no longer reports itself as "not available on this machine".** [#3410](https://github.com/PersonalClaw/PersonalClaw/issues/3410). A `multiInstance` model app's factory returns **one provider per enabled instance**, and `ModelTypeHandler.register` put each of them into the local-model registry under the **same** key — the app name — so the last one silently overwrote the rest. Because `list_instances` orders instances by a random hex id, *which* instance owned that key was effectively a coin flip that changed whenever an instance was added or removed: with two Ollama endpoints, one healthy and one dead, `GET /api/models/local/ollama-models/health` answered `ok: false` in one id order and `ok: true` in the other, from the same two endpoints. The mirror half was worse than a lost key: `deregister` called `unregister_provider(app)` once per provider in the list, so tearing down **one** instance of two **emptied** the app's entry and left the surviving, still-enabled instance unreachable on the download, health and binding surfaces — which reads to a user as "my models vanished". **A longer key would have been a worse bug, and that is measured rather than assumed:** the registry key is a path segment on the five `/api/models/local/{provider}/…` routes, a body field on two more, and the provider half of every `provider:model` ref in `active_models.json` — and `use_cases.split_ref` splits a ref on the **first** colon, so an `app:instance` key cannot round-trip through one. The moment the app name stopped being a registry key, `_prune_removed_providers` (which keeps a ref only when its first-colon prefix names a known provider) would have **deleted every existing binding** for that app. So the app keeps exactly one identity and that identity now answers for **all** of its instances: a new `local_models/multi_instance.py` aggregates them in `list_instances` order, availability is "any instance can serve", the catalog and search are the deduped union, and a download or a delete **fans out to every instance** instead of picking one — which removes the arbitrary choice rather than making it deterministic. `deregister` now removes only the instances it was handed and drops the app's entry only when none remain. The multiplicity is also no longer invisible: `/health` now reads `1 of 2 instances ready — laptop: ready; deadbox: not available on this machine`, so adding a second instance can no longer silently repoint the provider with no surface saying so. **The identity bug underneath was that the per-instance tag never applied at all:** `create()` sets `provider.name = f"{app}:{instance}"` behind `if not hasattr(provider, "name")`, and the real Ollama app declares `name` as a read-only property returning `"ollama"` for every instance — so the guard always saw an existing attribute and the tag was a no-op. `instance_id`/`instance_label` are now stamped unconditionally, because they are core's bookkeeping rather than the provider's identity, and registry identity must not depend on whether a provider happened to leave an attribute writable. Pinned by 16 cases including determinism as a property (the same scenario run twice, asserted byte-identical) and both list orders of the filed two-endpoint repro; restoring either half of the old behaviour reds 13 of them.
- **`ERR_MODEL_UNRESOLVED` now states the cause that actually fired instead of asserting one cause for all of them.** [#3408](https://github.com/PersonalClaw/PersonalClaw/issues/3408). `_resolve_from_config_registry` returns a bare `None` for every reason an active model ref cannot be built, and the stale-pin raise turned that into a single unconditional sentence: *"the active ref names provider 'X', which is absent from config.json (its app isn't installed or configured)"*, with the fix *"install 'X' in the App Store"*. For the measured case — a `vllm`-typed entry that **is** in `config.json` and **is** in the live registry, whose type simply has no registered factory — that `why` is false and that fix is a dead end, because `X` is the provider **entry** name the user typed in Settings, not an app name, so the Store has no row for it. This is the primary remediation surface for a total chat outage, so a wrong cause there is worse than no cause. The `why`/`fix` pair is now derived from whichever question fails first: the use case maps to no provider capability at all · `config.json` could not be read (said as *unknown*, not guessed either way) · no entry by that name is in `config.json` (the original sentence, now only when true) · the entry **is** in `config.json` but was never registered into the running gateway · its `type` has no registered factory, split three ways into *no installed app claims that type* / *the app is installed but **disabled*** / *the app is installed and enabled but failed to load* — and this branch names the **type** (`'vllm'`), which is the token the Store and `POST /api/providers` speak · the entry does not declare the capability the use case needs · the credential it names has no secret in the credential store. Anything left over gets a deliberately unsure sentence naming the log line to read and the model id to check, rather than a confident wrong cause. *"The model name is not offered by that provider"* is deliberately **not** enumerated: `model_override` is threaded into the build kwargs unvalidated, so a wrong model id does not make resolution return `None` — a factory that rejects one lands in the unsure branch, which is why that branch names the model id. **Both rails that described the old sentence were retargeted in the same change rather than left describing nothing**: `test_no_provider_first_run_rail.py`'s stale-pin fixture was hand-typed, had already drifted from production, and was used as the *negative* case of the frontend's no-model text discriminator — so a reword could not red it. It is now built by calling the real diagnosis, and the frontend's copied-verbatim fixture was updated with a second envelope for the missing-type-factory shape. Twelve cases, one per cause; restoring the unconditional pair reds all of them.
- **A scheduled script can finally read a tool refusal instead of crashing on it.** [#3407](https://github.com/PersonalClaw/PersonalClaw/issues/3407). `ScriptContext.call_tool` documents *"Returns the result dict"*, but its `_post` helper used a bare `urllib.request.urlopen`, which **raises `HTTPError` on any 4xx and discards the response body**. So every refusal `/api/tools/invoke` can issue was unreadable to a cron-script author — `risk_confirmation_required` (403), `tool_disabled` (403), unknown tool or provider (404) — and the reason the route sent was thrown away with the body. The docstring's own teaching example was precisely the broken case: it tells authors a call without `confirm_risk="destructive"` is refused with `403 risk_confirmation_required`, and the guard that implies (`r = ctx.call_tool(...)`, `if not r["ok"]: …`) could never run, because the call had already raised. `_post` now catches `HTTPError`, reads the body back, and returns the parsed result dict with `ok: False` and the route's `error.code` intact, so a refusal is data a script branches on. A 4xx whose body is not JSON still returns a dict rather than raising. The docstring was updated in the same change to state the contract it now keeps, and the test runs its example **lifted verbatim out of the shipped docstring** rather than retyped — a docstring example that does not work is the defect, so the example and the thing under test are the same bytes by construction. Driven as a real sandboxed subprocess against a loopback stand-in for the route, with the `confirm_risk`-set arm as the control; reverting `_post` reds both arms with the filed `HTTPError: HTTP Error 403: Forbidden`.
- **A content search that hits its deadline inside the worker thread now answers 504 with guidance and records the audit row, instead of letting the exception escape the handler.** [#3399](https://github.com/PersonalClaw/PersonalClaw/issues/3399). The handler's `try` around `asyncio.wait_for` named only `TimeoutError`, and the Python fallback's in-thread deadline rail raises `_ContentSearchTimedOut` — deliberately not a `TimeoutError`, because that lineage would put the stop signal inside the reach of the walk's `except OSError: continue`. The thread's own deadline is computed *before* `wait_for` starts its clock, so the in-thread rail can win the race; when it did, the 504, the SEL `outcome="error"` row and the "Narrow the directory or include glob" message were **all three** skipped at once. Both are now named in the handler's `except`, and the sentinel keeps its plain `Exception` base — re-basing it onto `TimeoutError` would reintroduce the swallow its own docstring exists to prevent. The new case asserts all three consequences in one drive, with the audit row read back through the real `GET /api/security/audit` surface rather than off a mock.
- **"Credential-shaped" is now one predicate instead of three, so the app-manifest rail no longer calls `max_tokens` a credential.** [#3401](https://github.com/PersonalClaw/PersonalClaw/issues/3401). The rail that requires a credential-shaped app setting to declare `x-meta.sensitive` matched an **unanchored substring** list containing `token`, so `max_tokens` — an integer request parameter, `{"type": "integer", "minimum": 0}` — was a credential and the rail reddened on two shipped manifests (`bedrock-models`, `claude-subscription`). That red was the **default** outcome in any workspace holding both clones, because the corpus resolver finds the first-party apps clone as a sibling of the checkout; a CI runner has no sibling, so CI stayed green and only humans and agent lanes ever saw it — the worst place for a false red, since neither remedy the failure offered was available. Marking an integer `x-meta.sensitive` makes the config form render it `type="password"` with write-only blank-input behaviour (masking a non-secret and making a normal numeric setting un-editable), and it cannot be renamed because `max_tokens` is the provider API's own parameter name. **The instance fix was not the fix.** Replacing the substring list with a copy of the sibling repo's anchored regex cleared `max_tokens` and left the *class* open: measured, that regex also flags `sort_key` and `cache_key` — dict lookups, identical in kind — and misses `aws_access_key_id`, `credentials` and `apiKey`, which are real credentials. A rail that duplicates a pattern is still a second answer to one question. So the rail now **imports** `personalclaw.apps.secret_fields.is_credential_field_name`, the predicate core's maskers already run on every `personalclaw config get`, and defines nothing itself. One rule states it: a credential noun must be the name's **last** word (a trailing `id` naming the same subject is transparent), and a bare `key` additionally needs a qualifier. So a ceiling (`max_tokens`, `token_limit`, `context_budget_tokens`), a location (`secrets_dir`, `api_key_env`), an identifier (`sort_key`, `cache_key`, `semantic_keys`, `client_id`) and a longer word that merely contains one (`tokenizer`, `monkey_patch`, `keyboard`, `keyring_backend`) are all ordinary fields, while `api_key`, `secret_access_key`, `aws_access_key_id`, `session_token`, `signing_key`, `client_secret`, `passphrase` and `credentials` are not. **Measured over all 100 first-party manifests** (257 setting occurrences, 108 distinct leaf names): against what the maskers already did, **nothing moves** — the same 8 names across 28 sites are flagged, so no runtime masking behaviour changes; against the copied regex, one name moves, `rsync-sync.ssh_key`, which stops being credential-shaped and therefore no longer needs the subject-exemption list that existed only to un-flag it. `api_keys` moves the other way, into the required set, because it is the plural of a credential and the document masker already masks every string inside a credential-named list. The sibling repo keeps its own regex **deliberately** — its CI job is a stated pure-stdlib job with no core install, so a contributor runs byte-identically what CI runs, and making it import core would put an apps-CI gate behind a git resolve of core's `main` — and that divergence is now **measured rather than trusted**: a new rail imports the sibling's pattern wherever both clones are present and reds if the two reach different verdicts about any field that actually ships, which is the assertion whose absence let this diverge in the first place.
- **A room's export reports when the room was created, not when someone first spoke in it — and a room nobody has spoken in exports a real date instead of an empty one.** [#3402](https://github.com/PersonalClaw/PersonalClaw/issues/3402). `export_payload` handed the renderer the **transcript log's** metadata, and that log is created lazily on the first message write, so its `created_at` is the first message's timestamp while the room's own creation time sat unused on the `Room` object returned from the same call. Measured live: a room created at 14:37:50 whose first message landed at 14:38:07 exported `2026-09-23T14:38:07` — 17.6 seconds of drift on a room used immediately, and a week's worth for a room that sits idle before anyone speaks, with `GET /api/rooms/{id}` returning the correct value in the same request cycle. A room nobody has spoken in has no transcript at all, so its metadata line was `{}`: the JSON export carried `created_at: ""` and the Markdown header skipped its `Created:` row entirely, going straight from the title to `Messages: 0`. Both formats now read the room's creation time, and the merge happens in `export_payload` rather than at the HTTP handoff so the payload's own contract — "everything a renderer needs" — is honest and a second caller cannot re-derive it differently. The metadata dict is copied before the merge, because `ConversationLog.get_metadata` hands back its own mtime-keyed cache entry and writing into it would publish the room's creation time into the transcript log's cached metadata for every later reader.
- **The `.catch(() => …)` swallow census can now score a boolean or scalar fabrication, so it can finally ratchet on one.** [#3409](https://github.com/PersonalClaw/PersonalClaw/issues/3409). `SWALLOW_SHAPE` carried `[]`, `null`, `undefined`, `''`, `""`, `{}` and the structured literal — and no `true`, no `false`, no digit. The consequence was exact and was measured at **one file position**: inserting `.catch(() => [])` moved the census 0 → 1 and red CI, while inserting `.catch(() => setGone(true))` moved it 0 → 0 and left the suite 33/33 green. The same mutation in the same place, differing only in what the handler fabricated. #532's own close-condition criterion 1 had named the shape all along (*"…or a boolean 'gone' flag"*), so the selector was one shape short of the criterion it was scored against. Widening it surfaced **17 sites across 14 files** that no rail could see. **The widening had to land in two places, which is why it could hide:** `FABRICATES`'s bare-expression-body alternation was a hand-copied duplicate of `SWALLOW_EMPTY`, so adding a member to `SWALLOW_SHAPE` alone would have scored the parenthesised, inline-setter and braced spellings while `.catch(() => true)` — the plainest one — still read zero; a named `SWALLOW_BARE` now makes a member reach every form or none. **Calibrated in both directions:** all four mutations (array, boolean, braced boolean, numeric) now red at the identical file position where previously only the array did, and the negative controls hold — `setLoading(false)` beside a recorded error stays 0 (the `RECORDS_THE_ERROR` veto), `trueish` does not read as `true`, `falsey` does not read as `false`, and form C (`.catch(() => {})`) still fabricates nothing. **A fourth syntactic form was closed at zero cost:** the handler matcher required a *parenthesised* parameter list, so `.catch(e => setRows([]))` was invisible to both forms whatever it fabricated — it was modelled while the tree held 0 instances, the only moment such a widening moves no number. The remaining unmodelled forms are now enumerated with their measured populations rather than left to folklore: `.catch(fnReference)` is out at 7 sites of which all 7 are *captures*, `try/catch` statements are out at 181 candidate sites dominated by `JSON.parse`/`localStorage` guards, and `.then(onOk, onErr)` is out at **0 genuine sites** — a naive regex scores 34 there by reading `useQuery`'s options object as a second `.then` argument, so that boundary was verified paren-aware rather than asserted.
- **The swallow census stopped deleting source before counting it: a `//` comment containing an unclosed `/*` was blanking every line down to the next `*/`.** [#3409](https://github.com/PersonalClaw/PersonalClaw/issues/3409). `codeOf` was two regexes and it blanked **block** comments *first*, so a line comment carrying an unclosed `/*` — an `/api/*` glob cited in prose, this repo's own house style — opened a block that the block pass closed at the next `*/` anywhere in the file. **Measured: 34 line comments carry an unclosed `/*`**, and the span they hid reached 4,677 code characters in `design/consistencyAudit.report.ts`, 2,478 in `pages/loops/DesignCockpitPage.tsx` and 2,275 in `pages/notifications/notificationMeta.ts`. This sat *upstream of every other shape*: no widening of `SWALLOW_SHAPE` can score a site that preprocessing already removed from its input, and unlike the enumerable 17-site scalar gap this one hid a span whose length depended on where the next `*/` happened to fall, so the hidden population was not knowable without fixing it first. **The honest result is worth stating precisely, because it is not the dramatic one:** the three counts are 119 sites before any fix, **119 after fixing the stripper alone**, and 131 after also widening the shapes — the hidden spans happened to contain no fabricating `.catch`. The fix is justified by the unbounded hazard, not by a count it moved. **Fixed by reuse, not by a third implementation:** `design/tokenLintRule`'s `stripComments` is the #3347/#3337 fix for exactly this defect in exactly this corpus (`//` beats `/*`, a `/*` inside a string cannot open a block, template literals are line-local, `://` is a scheme separator, and a `/*` preceded by alnum or `[` is division or a regex class), verified across all 1559 `web/src` files and returning one entry per input line so line numbers survive. **Two independent censuses in this repo have now been defeated by naive comment handling**, which is the argument for sharing one implementation rather than writing a third. Three controls pin it — a `//` comment with two asterisks, a `/*` inside a string literal, and several unclosed openers in a row, each followed by a known swallow — and each fixture deliberately ends in an ordinary JSDoc, because *that* is what supplies the closing `*/`: the first version of these controls omitted it and was **vacuous**, staying green with the stripper reverted. The scanner's `endState` is asserted too, since a tracker left stuck open reads as "the rest of the file is clean".
- **A failed model-catalog or file-roots read no longer renders as "you have none".** [#3394](https://github.com/PersonalClaw/PersonalClaw/issues/3394). Three shared hooks discarded their rejection and reported `loading: false` with an empty collection, which is the eternal-spinner defect's confident twin — the spinner stops and the surface states the opposite of what happened. `useModelCatalog`'s docstring said so out loud (*"Degrades to an empty list if the backend isn't reachable"*), and that sentence was the defect written down as a feature: an unreachable `/api/models` rendered a model picker claiming the user has no models, indistinguishable from an account with none, with nothing announced and no retry. `useActiveChatModelOptions` is the sharper one, because its list is the set a user may *pin an agent to* — rendering it empty reads as "no model is active" on a box with three bound. `useFileRoots` rendered a file explorer with no roots, which is also exactly what a correctly-locked-down gateway looks like. All three capture the rejection now and their four consumer surfaces render it where the user is looking: `ScheduleForm`, `AgentForm` and `AgentDetail`'s reserved-model editor each get a `FieldError` beside the picker, and `FilesSection` renders `LoadError` with a working retry *in place of* the workbench, because the roots are the page's premise rather than decoration. Two further sites that fabricated a **setting** are fixed under the same ruling that closed #532 row 19 — an unread switch is not an off switch: the terminal's tmux-persistence toggle read OFF off an unread config (the file's own comment already documented `null` as "not answered yet, toggle hidden"), and chat's auto-nudge panel told the user **"Disabled on this server (`PERSONALCLAW_AUTONUDGE=0`)"** — naming a specific environment variable as the cause of a read that had merely failed.
- **A new ratchet scores a `useQuery` invocation's fetcher and its destructuring together, because either check alone passes half the class.** [#3394](https://github.com/PersonalClaw/PersonalClaw/issues/3394). `useQuery` returns `error` and `status`, and the contract was defeated on both sides of it: **47 invocations across 31 files** have a fetcher that `.catch`es its own rejection, so `error` is *structurally* unreachable — the hook is handed a successful empty value and even a call site that binds `error` correctly can never fire it — while **69 invocations across 36 files** propagate the rejection and bind neither `error` nor `status`, so the error state collapses into a permanent loading and the surface spins forever. A rail that inspects the destructuring passes every instance of the first kind; a rail that inspects the fetcher misses every instance of the second; and no check aimed at the hook itself sees either, because the defect lives one layer below it and one layer above it. The new `§D` section scores one invocation twice against per-file budgets that may only fall, and **both arms are proven failable**: dropping the `error` binding from a single call site reds it while touching no `.catch` at all — which is precisely the mutation the pre-existing whole-file swallow census stays green on. The two budgets record a measured population rather than shipping at zero, because most of both halves is a per-surface product decision (what a retry re-runs, whether a cached copy should still paint) rather than a deletable line; each has a coverage floor so it cannot go vacuous, and there is deliberately **no regenerate mode** — on a budget like this, regenerating is not a convenience but a way to bless a new swallow by re-running a script instead of writing down why a site is correct.
- **`LedgerRailsPanel` no longer renders a token FLOOR as a plain number on the same run page where `IntrospectPanel` discloses it.** [#3400](https://github.com/PersonalClaw/PersonalClaw/issues/3400). `RunStats` got the #3218 absence disclosure and `RailTotals` never did: it carried `tokens: int | None` and `cost_usd: float | None` and no `tokens_recorded`/`cost_recorded` field, so `rail_totals` kept only the rows whose `tokens` was an int, summed the survivors, and presented the partial sum as a total. On a mixed run — step A records `tokens=100`, step B carries no key — `IntrospectPanel` rendered `≥100` while `LedgerRailsPanel` rendered `100`, two aggregates over one journal, on screen at once, and a user could watch them disagree. The panel's docstring claimed to be *"absent-aware"*, and it was, but only through `None`, which is honest solely when **no** step carried a count. `RailTotals` now carries both flags, mirroring `RunStats.tokens_recorded`/`.priced` exactly, flipped `False` whenever any projected row lacked the key; both cells route through the same `runTokensStat`/`runCostStat` helpers `IntrospectPanel` uses, so the two aggregates cannot diverge by construction. **Absence has two shapes and only one of them is `null`**, which is now stated on the canonical payload type: `null` means nothing was ever measured, and a number with its flag `false` is a floor. The pre-existing `None` sentinel is deliberately untouched — `test_a_loop_shaped_step_reads_absent_not_zero_for_money` already pins it as a distinct fact from a floor. The comment at `IntrospectPanel.tsx:121` reading *"The two aggregates now agree"* was aspirational when written and is true now.
- **A never-fired store trigger no longer reads `never run` in the list and ok-green "Firing on its own" one click later.** [#3396](https://github.com/PersonalClaw/PersonalClaw/issues/3396). For a trigger with `health: ok`, `state: active`, `run_count: 0` — the state right after a user creates one — the triggers list drew a neutral never-run dot while the store inspector drew an ok-green tick, with `No runs recorded yet.` printed directly underneath it. `StoreTriggerDetail.tsx` still called `triggerHealthMeta()` and reached around the `triggerStatusMeta()` reconciler every other surface uses; the two functions answer different questions and only one of them had been told whether anything ran. It now calls the reconciler with the same `run_count` gate the list uses — reused verbatim from `storeToTrigger` rather than reinvented, since store rows carry no run-outcome timestamp at all — so the two surfaces cannot disagree by construction. Measured before and after by bundling `scheduleMeta.ts` standalone: the panel and the list now return the identical label and tone for that row, and flipping `hasRun` still flips the answer, which is the control proving the gate is what is being exercised. Two rails moved with it, and one of them was **asserting the defect**: the renderer census in `reapedRunReadsAsFailure.test.ts` had one case per renderer and did not enumerate the only renderer that bypassed the reconciler, so a fourth was added; and `storeTriggerStatus.test.ts` asserted `triggerHealthMeta('ok','active').tone` *is* ok-green — a green assertion on the exact value that is wrong for a never-fired trigger. That one assertion was retargeted through the reconciler with `hasRun` stated in both directions. Its stopped-state assertions, which are correct, are untouched.
- **The macOS sandbox wrap no longer resolves its own enforcement binaries through the PATH of the child it is about to confine.** `sandbox_exec_argv` emitted `["env", …, "sandbox-exec", "-f", <profile>, *argv]` with **both** of its own binaries as bare names, and the process that actually resolves them is the ceiling shim's `os.execvp` running *in the child* (`_spawn_exec_shim`). So a child whose PATH is narrowed — a legitimate hardening posture, and what an ACP readiness probe against a fake CLI does deliberately — died at exec with `_spawn_exec_shim: cannot exec 'env': [Errno 2]` **before any enforcement was applied**, replacing the child's real startup cause with a diagnostic naming the shim. The second half is why this is a security fix and not only a legibility one: a PATH an agent can influence could shadow `env` or `sandbox-exec` and defeat the confinement outright. Both are now resolved by the **wrapper**, at argv-build time, against the POSIX system utility path (`confstr("CS_PATH")`, i.e. `/usr/bin:/bin:/usr/sbin:/sbin`) and never `$PATH` — resolution still runs through `shutil.which`, so a name absent from an image fails honestly instead of yielding a hardcoded path that does not exist. The `env -u` scrubbing behaviour is untouched; only how the two binaries are *named* changed. The availability probe was fixed the same way and for the same reason — an agent-influenceable `$PATH` must not be able to point the *capability check* at a stand-in either — and it now fails **closed** on a host missing either binary, with an explicit log, rather than selecting a backend whose argv would die at exec. A direct call that cannot resolve both raises `SandboxEnforcementUnavailable` before the temp profile is written, so a refusal leaks no file. **Reproduced before it was fixed**, which mattered because the local suite never took this branch: `_probe_sandbox_exec` resolves `sandbox-exec` through `$PATH`, so a test that narrows PATH makes backend detection answer `none` and skip the wrap entirely — the path is only reached when an *earlier* test in the same process already warmed the memoised host-fact probe, which is what three macOS CI legs did and a single-file local run does not. With that cache warmed, `test_handshake_error_quotes_the_child_stderr` failed locally with CI's exact message, and passes after the change with the wrap verifiably still applied (`/usr/bin/env` + `/usr/bin/sandbox-exec`, both absolute, both present) under an emptied PATH. **Three rails pinned the defect and were retargeted rather than deleted:** two asserted the bare strings `"env" == argv[0]` and `"sandbox-exec" in argv` (`test_sandbox_argv.py`), and one accepted the OS wrapper by bare name inside the ceiling shim (`test_cron_script_ceiling.py`, now compared on the basename). A new case asserts the property in both directions — both binaries absolute, existing, and under the system utility path — gated on the capability rather than on `sys.platform`, since a darwin host without `sandbox-exec` has nothing to measure and the platform string does not say so.
- **The `e2e-a11y` gate no longer blames the Session Map rail for a chat turn that was never sent, and its keyboard-activation clause no longer depends on how tall the last turn rendered.** Two of the browser gate's Session Map tests activated a rail mark and then asserted that `"<prompt> (k)"` — a prompt number derived from a **loop index** — had come on screen. That couples the assertion to two things a test cannot see: that the *k*th send became the *k*th turn, and that the *k*th mark of a kind belongs to it. Both broke in run `35949502119`, on a pull request that changed **zero** `web/` files. `End` lands the roving cursor on the *last* mark, which for a six-turn session is mark 17 of 18 — the newest turn's **activity** mark, whose coordinate is the assistant turn's node — so Space jumped there correctly (scrollTop 0 → 952 of 1271) and the test then demanded a *different* element, the newest user prompt, which sits above that node and is off the top at the resting offset; whether it is visible at all is a function of the last turn's rendered height, so a pass was as unsound as the failure. The landing proof is now read off the mark that was activated: the rail's own live region must announce `Jumped to turn N of M` for the turn that mark names, and the mark must become `data-current` — the rail's `IntersectionObserver` reading of "this turn is on screen", which a centred `scrollIntoView` satisfies in every clamping case, so a working jump always passes and a broken one never can. The WCAG 2.1.1 clauses (Tab reaches the rail, the arrows rove, Enter and Space each move the transcript, and not one pointer event happens) are unchanged, and each activation now carries a `not.toHaveAttribute('data-current')` control so "it lit up" cannot be a reading of where the transcript already was. Nothing was skipped, retried, or given a longer timeout.
- **`driveScriptedTurns` now guarantees the turn count it is asked for, instead of silently starting a session short by one.** The helper's per-turn barrier was `expect(Stop).toHaveCount(0)` — an *absence*, and it is true both after a turn ends and **before it starts**, because the composer's action button only morphs to Stop once the stream is live. Measured in the same failing run: turn 1's wait returned in 386 ms while turn 1's stream was still to come, and turn 2's wait then absorbed 5594 ms of it, so turn 2 was typed and sent *into a live run* — and that send was swallowed. The session ended with five user turns for six sends, prompt `(2)` was absent from the transcript, and the red surfaced 300 lines later as "the rail tick did not bring its turn on screen". The barrier is now a positive, monotone count of the transcript's own per-turn action rows (one per user turn, one per assistant turn, both rendered only when nothing is streaming), so it cannot be satisfied before the turn it guards begins, and a lost send fails **at the send that lost it** with a message that says so. This restores the discipline `chat.spec.ts` states and the helper's docstring already claimed: composer state and transcript state are two *independent* readings of "finished", and both belong inside the loop. `sandbox_exec_argv` emitted `["env", …, "sandbox-exec", "-f", <profile>, *argv]` with **both** of its own binaries as bare names, and the process that actually resolves them is the ceiling shim's `os.execvp` running *in the child* (`_spawn_exec_shim`). So a child whose PATH is narrowed — a legitimate hardening posture, and what an ACP readiness probe against a fake CLI does deliberately — died at exec with `_spawn_exec_shim: cannot exec 'env': [Errno 2]` **before any enforcement was applied**, replacing the child's real startup cause with a diagnostic naming the shim. The second half is why this is a security fix and not only a legibility one: a PATH an agent can influence could shadow `env` or `sandbox-exec` and defeat the confinement outright. Both are now resolved by the **wrapper**, at argv-build time, against the POSIX system utility path (`confstr("CS_PATH")`, i.e. `/usr/bin:/bin:/usr/sbin:/sbin`) and never `$PATH` — resolution still runs through `shutil.which`, so a name absent from an image fails honestly instead of yielding a hardcoded path that does not exist. The `env -u` scrubbing behaviour is untouched; only how the two binaries are *named* changed. The availability probe was fixed the same way and for the same reason — an agent-influenceable `$PATH` must not be able to point the *capability check* at a stand-in either — and it now fails **closed** on a host missing either binary, with an explicit log, rather than selecting a backend whose argv would die at exec. A direct call that cannot resolve both raises `SandboxEnforcementUnavailable` before the temp profile is written, so a refusal leaks no file. **Reproduced before it was fixed**, which mattered because the local suite never took this branch: `_probe_sandbox_exec` resolves `sandbox-exec` through `$PATH`, so a test that narrows PATH makes backend detection answer `none` and skip the wrap entirely — the path is only reached when an *earlier* test in the same process already warmed the memoised host-fact probe, which is what three macOS CI legs did and a single-file local run does not. With that cache warmed, `test_handshake_error_quotes_the_child_stderr` failed locally with CI's exact message, and passes after the change with the wrap verifiably still applied (`/usr/bin/env` + `/usr/bin/sandbox-exec`, both absolute, both present) under an emptied PATH. **Three rails pinned the defect and were retargeted rather than deleted:** two asserted the bare strings `"env" == argv[0]` and `"sandbox-exec" in argv` (`test_sandbox_argv.py`), and one accepted the OS wrapper by bare name inside the ceiling shim (`test_cron_script_ceiling.py`, now compared on the basename). A new case asserts the property in both directions — both binaries absolute, existing, and under the system utility path — gated on the capability rather than on `sys.platform`, since a darwin host without `sandbox-exec` has nothing to measure and the platform string does not say so.
- **`test_the_host_profile_forbids_a_nested_sandbox` no longer reports a failure on a host that refuses nested sandboxing outright.** The test proves a *narrow* claim — that the host profile's `deny` rules are what make an inner `sandbox_apply` fail — and it established that attribution with its own control: nesting a permissive `(allow default)` profile inside another permissive one must succeed, so a failure under the host profile is not attributable to nesting as such. On GitHub's `macos-14` image that premise is simply false. The narrow fact matters and is worth stating, because the obvious reading of the symptom is wrong: that runner does **not** deny `sandbox_apply` outright. It **permits a single-level application** — which is precisely why `detect_backend`'s own probe (one profile, one child) passes there and the pre-existing backend skip does not fire — and refuses only a **nested** one, with `sandbox_apply: Operation not permitted` and rc 71. So the control failed and the leg went red on a host property, not on a defect, and a precondition that probed mere *availability* would have answered "capable" and left the leg red. The test now treats that specific refusal as a measured **capability precondition** and skips with the observed rc and stderr quoted into the reason, because the assertions below it would still pass on such a host *for the wrong reason*: a blanket refusal of nesting is indistinguishable from a profile-attributable one, and reporting that as this test passing would be a green that does not mean what the test's name says. The branch is deliberately narrow — it is not a platform, image, or darwin check, it matches only the `sandbox_apply` refusal shape, and **any other** control failure still falls through to the hard assertion and reds. On a normal Mac nothing changes: all 14 cases in the file run their real assertions with zero skips.
- **An unreadable `entity_settings/inbox.json` no longer ENABLES retention cleanup and deletes the items you told it to keep.** ([#3398](https://github.com/PersonalClaw/PersonalClaw/issues/3398)) The entity-settings loader collapsed "this file could not be read" into the same `{}` it returns for "this file was never written", and `load_inbox_settings()` merged that over `{"auto_cleanup_enabled": True, "retention_days": 90}`. So a stored `{false, 3650}` read back as `{true, 90}` — measured identically under all three corruptions a file on your disk can actually take (truncated JSON, non-UTF-8 bytes, a payload that is not an object) — and the maintenance pass acted on it through `cleanup_by_retention`, which deletes items older than the window *regardless of status*. One-way, no snapshot, no undo: a settings file damaged by a hand edit, a sync conflict or filesystem damage silently discarded 3560 days of inbox history, and the only trace was one `Discarding unreadable entity settings` line in the log. **The asymmetry is the fix, not a different default.** "Absent" and "unreadable" were byte-identical at that boundary, so nothing downstream *could* tell a first run from a lost setting; the loader now answers `{}`, the stored object, or `None`, and it deliberately picks no fallback for `None` — the safe fallback is a property of what the caller does with the answer, not of the file. Every one of the twelve call sites now states its own choice in code, eleven of them fail-open exactly as before (a dismissal store, a pin list, an onboarding flag or a routing mute that cannot be read costs noise, not data), and the inbox refuses: cleanup is **suppressed** until the file is repaired or removed, with a log line that says so instead of the generic discard warning. An absent file still means "first run, use the defaults" and still cleans up at 90 days — pinned in both directions, because collapsing them back either way returns the data loss or silently stops cleanup on every fresh install. **The rail that made this invisible is retargeted, not padded.** Three tests named `test_*_entity_settings_fail_open_*` certified fail-open as the *shared loader's* contract, using `agent_routing`, the one entity where fail-open is harmless — while the entity whose default deletes had no corrupt-file test at all. They now pin what is actually true (the loader signals; `agents/routing.py` chooses open at its own site) and the inbox's five-row table sits beside them, carrying both controls: a readable refusal reads `3650` back, and a readable *consent* still deletes the same expired item, so a `run_maintenance` that had quietly stopped deleting could not pass.
- **`personalclaw doctor` no longer loads `torch` to answer a yes/no question, which is what aborted macOS verification runs inside `faiss`.** [#3324](https://github.com/PersonalClaw/PersonalClaw/issues/3324). On macOS arm64 `torch/lib/libomp.dylib` and `faiss/.dylibs/libomp.dylib` are two copies of LLVM's OpenMP runtime. Loading both packages is harmless; *initializing* the second runtime is not, and `faiss` initializes its copy the first time it enters a parallel region — its `search`, never its `add`. So the pairing is invisible to an import-order probe and surfaces as `OMP: Error #15` plus `SIGABRT` from inside `faiss/swigfaiss.py:search`, which under xdist reads as `worker 'gwN' crashed` blamed on whichever test happened to hold the worker. That is why this walked between two different tests, two different `vector_memory` call sites and four different shards across five reproductions while the only stable thing was the native frame. **The single residency site was one line:** `cli_doctor.py`'s `import faster_whisper`, whose own comment says the question it asks is merely *"is this dep present in this dev env"* — `importlib.util.find_spec` answers that without executing the package. It is now the only spelling, and it is enforced rather than remembered: `tests/native_omp_guard.py` fails the test that moves `torch` from absent to resident, blaming the *transition* so the culprit is named instead of drowning in a cascade of reds from every later test in the poisoned worker. Before the change the rail failed `tests/test_cli.py::TestDoctorStt::test_doctor_stt_enabled_with_model` at teardown while the other three tests in that class passed; a rail that cannot go red is not a rail. The doctor line now reads `faster_whisper: ✅ installed` rather than `✅ importable`, which is the honest word for what `find_spec` proves — a package that is installed but broken now surfaces at first use instead of here, deliberately traded for not aborting the process that is diagnosing it. **`KMP_DUPLICATE_LIB_OK=TRUE` was rejected and is not shipped**, for a reason the measurements sharpened: it does silence the abort, and `sklearn/__init__.py` already sets it via `os.environ.setdefault` — so `sentence_transformers`, which imports sklearn, has been suppressing this pairing all along rather than avoiding it, and a green run under that flag is undefined behaviour underneath the memory store rather than evidence. That is also why the rail watches residency, which is forbidden, instead of the crash, which is maskable. **What this does not fix**, measured and recorded rather than assumed: the shipped `faster-whisper` STT app loads `faster_whisper` in the gateway's own process and brings `torch` with no sklearn to set that flag, so with `faiss` present the gateway still aborts on the next episodic dedup search (rc 134 through core's real `write_episodic`). Whether an in-process native model app may share the gateway process at all is an architectural call, so #3324 stays open on that remainder; `docs/guides/platforms.md` documents the hazard and advises a remote STT provider on macOS until then.
- **The desktop app's dashboard window runs inside the Chromium process sandbox again, and a test can now see whether its bridge actually loaded.** [#3348](https://github.com/PersonalClaw/PersonalClaw/issues/3348). Restoring a genuinely dead native bridge had turned the renderer sandbox off (`sandbox: false`), and it only ever needed to be off for **one line**: `preload.js` did a relative `require("./capabilities")` for its capability names and IPC channels, and a sandboxed preload's polyfilled `require` resolves `electron` plus three builtins (`events`, `timers`, `url`) and nothing else. So that line threw, the whole preload was skipped, and `window.pclawDesktop` was simply **absent** — Electron reports this on `preload-error`, a channel nothing listened to, which is why a dead bridge never logged anything. The two constants are now inlined, and the flag is gone: measured on a real Electron renderer, the bridge comes back with the same five members (`capabilities`, `loginItem`, `notifications`, `onStatus`, `pushToTalk`) and zero preload errors, on `about:blank` and on the real `loading.html` file document. **Why the boundary is worth the inline:** this is the window that loads the dashboard, and the dashboard renders agent- and app-authored HTML and script — widget frames put it in `sandbox="allow-scripts"` blob iframes, plus the artifact and file previews. That iframe attribute is a *web-platform* boundary; the Chromium process sandbox is an *OS* one, and Chromium gives no guarantee that a null-origin blob or `srcdoc` frame lands in its own renderer process. Nothing was known to be exploitable — `contextIsolation`, `nodeIntegration: false` and the loopback-only bridge gate all held throughout — this was defence in depth given up for a reason that turned out to be optional. The gateway switcher window keeps `sandbox: false` deliberately: its own preload does still need a relative require. **The rail that forbade this fix is gone too, and not by being deleted.** It asserted the literal token `sandbox: false` on every preload-bearing window, which pinned one *spelling* of a correct pairing and reddened the secure version. It now asserts the **property**, in both directions and read off the preload each window actually names: a preload that needs Node must have the flag, and a preload that does not must not have it — so both "the bridge is dead" and "an OS boundary was spent for nothing" fail the build. **And the suite can finally observe the bridge at all.** All 359 desktop cases were assertions over *source text*; prepending `throw new Error(…)` to `preload.js` left them at 359 pass / 0 fail while a real renderer got `undefined`. Two new tiers close that: one executes the preload in Node under a `require` shim shaped like the sandboxed renderer's (headless, every platform, so CI is covered), and one drives a real Electron binary and reads `window.pclawDesktop` out of the renderer. Each carries its own control — the switcher's Node-needing preload must be *refused* by the shim, and must come back `undefined` from the real renderer with the sandbox on and `object` with it off, so the rail is a measurement rather than a hope. Both mutations the report named now red: a throwing preload reds 3 cases, and restoring the relative require reds 5.
- **Uninstalling an app while keeping its data, or updating an app, no longer refuses because the app's own background process touched a file.** [#3324](https://github.com/PersonalClaw/PersonalClaw/issues/3324). Both operations copy the app's live `data/` before they change anything, and `shutil.copytree` lists a directory first and copies each entry afterwards — so any file that disappears inside that window aborts the whole copy, and this ladder reads any copy failure as "fail closed, remove nothing". The user then saw a refusal they did not cause and could not act on: *"data/ could not be copied out"*, nothing removed, app still installed. **Measured, not hypothesised:** an app whose `data/` holds a git checkout hits this routinely, because `git commit` ends by spawning `git maintenance run --auto --quiet --detach` and that child is **detached** — it outlives the commit the app waited for and holds `.git/objects/maintenance.lock`, created and removed inside our walk. Confirmed on git 2.54.0 by polling for that exact path, and it is what failed two macOS legs of `main`'s `Full` run 35764976454. A git checkout is only the clearest case; an SQLite WAL or an app's own lockfile has the same exposure. The copy now waits for the tree to settle and retries it whole, up to four attempts with a doubling backoff. It deliberately does **not** skip whatever vanished: a missing entry is usually a lock file worth ignoring, but it is also what a git repack looks like from outside — loose objects are *renamed* into a new packfile — so skipping would trade a loud refusal for a silently corrupt copy of your work, which is the one outcome this ladder exists to prevent. A fault that leaves its source on disk (out of disk space, permissions, I/O error) is not retried at all and fails closed on the first attempt exactly as before, and a tree that never settles still ends in the same refusal naming the same file.
- **Citing an issue number inside a block comment no longer fails token-lint as a "raw color hex."** [#3337](https://github.com/PersonalClaw/PersonalClaw/issues/3337). The hex pattern is `#[0-9a-fA-F]{3,8}\b`, and every decimal digit is a hex digit, so it matches any 3-to-8-digit issue reference — `#532`, `#1783`, `#2680`. Comments were supposed to be exempt, but both consumers decided "is this a comment?" from each line's **own** first characters (`//`, `*`, `/*`), and an interior line of a multi-line `{/* … */}` JSX comment carries no marker of its own. So a comment citing its issue number — the convention this project's code comments run on — was linted as code and reported a raw colour that was never there. Tightening the pattern to 3/4/6/8 digits would not have helped: `#1783` is four. Comment state is now **tracked across lines** by one scanner with a twin on each side (`web/src/design/tokenLintRule.ts` for the host frontend, `personalclaw/apps/quality.py` for the shipped app-bundle gate that verifies a `quality.designSystem: "v2"` claim), so an app author citing an issue number in a block comment no longer trips the bundle quality gate either. Two scoping decisions were measured rather than assumed, because a tracker that leaves block state stuck open reads as "the rest of the file is clean" and silently stops catching the raw hexes the rail exists for: the scanner is **string-aware** and gives `//` precedence over `/*` (all three shapes ship today — `'/*EDITMODE-BEGIN*/'`, `'… #/settings/* subpages'`, and a `/*` quoted inside a `//` comment), and a **backtick is an ordinary character rather than a tracked template state**, because tracking template literals desynchronised on a backtick inside a regex literal in three real files and telling a regex literal from a division needs real parser context. Not tracking them is also the stricter direction: template content stays linted, so a raw hex in a css-in-template is still caught. Measured over all 665 host frontend sources, the change flags **zero** fewer genuine violations and **zero** more, and the only lines whose verdict changed are four issue references inside JSX block comments. Both halves are pinned against the same 24-case corpus (`apps/token_lint_comment_cases.json`, read by both languages' tests — a regex can be shared as data, a lexer cannot), carrying positive controls a too-greedy tracker must still fail, and the host lint now asserts that no source file ends outside code state.
- **A stray `/*` in code no longer blanks out the lines under it and takes a raw colour hex with it.** [#3347](https://github.com/PersonalClaw/PersonalClaw/issues/3347). token-lint's comment tracker treated every `/*` as a block opener, including one that cannot be: `<p>3/*off</p>` in JSX text, or the two class members in `const GLOB = /[/*]/`. That opened a block, the next **unrelated** `/* … */` in the file closed it, and everything between was discarded as comment text — so a raw hex inside that span was reported by nobody while the scan still ended in `code` state and the bundle still earned `designSystem: "v2"`. **This is the shape the EOF control cannot see, and that is the point:** `end_state` reports the state the scan ENDED in, which is not the claim "the tracker read every line correctly" — a bogus block that *closes* leaves no residue for it to refuse on. Measured on a 6-line file, the tracker reported 1 of the 2 hexes present, against a control of 2 with the stray removed. The fix is the one-character look-behind that already shipped for `://`, one condition in each twin: a `/*` whose immediately preceding character is ASCII-alphanumeric or `[` is not an opener. Over all 1559 `web/src/**/*.{ts,tsx}` files that changes **0** verdicts, **0** EOF states and **0** lines of stripped code — all 54 of the corpus's 6761 alnum/`[`-preceded `/*` occurrences are route globs inside a string or behind `//` prose (`/api/*`, `#/settings/*`, `image/*`), where string-awareness already gave that answer — and a start-of-file `/*` still opens, so #3337 stays fixed. **The boundary is exactly one character wide and is pinned in both directions:** `<p>3/*off</p>` is now read correctly, while `<p>use /* as a wildcard</p>` — a SPACE before the `/*`, indistinguishable from a real opener without parser context — still opens a block and still fails STRICT through `end_state`. One previously-pinned case reverses with this, in the stronger direction: `const GLOB = /[/*]/` used to fail the whole bundle by name on a premise that was never true (nothing in that file is commented out) and is now simply linted, so the hex below it is reported as the line violation it is.
- **A chat on a local model now reports a real context percentage and compacts before it overflows, instead of reading a confident 0% forever.** The bundled **Ollama** provider initialised `_last_context_pct` to `0.0` and assigned it nowhere — the only occurrence of the name in the file was its own initialiser — so every Ollama session published a measured-looking 0% at any history size. That is worse than publishing nothing, because both consumers of the gauge key off `None`: the composer draws a plain dot for `None` and a filled ring for a number, and the native loop's compaction gate compares the provider's number against its 70% threshold and falls back to the char-based estimate **only** when the field is `None` — and `0.0 is not None`. So the fabricated zero failed the threshold *and* made the backstop that exists to cover an absent gauge unreachable: compaction was structurally disabled, history grew, and Ollama truncated the prompt silently (measured: HTTP 200, no exception, nothing for a reactive retry to catch). Measured on Ollama 0.34.2, a real turn at **26682 prompt tokens against a 32768-token served window reported `0.0` where the truth was 81.43%**. The two completion paths now assign the percentage they already had the numerator for, and report `None` — never a number — when there is nothing to divide by. The denominator is the other half, and it is a **deployment** fact no table can hold: the shared model table and Ollama's own `/api/show` both answer with the model's *architectural* maximum (262144 for this model, against 32768 actually served and 128000 in the table), so sizing against them hides the overflow even once the numerator is right. Resolution is now, most authoritative first: a **`context_window` you declare on the binding** (Settings → Models → edit an instance → *Served context window*, blank to auto-detect — set it whenever you run Ollama with an explicit `num_ctx`); then **`GET /api/ps`**, which reports the window the model was actually loaded with, probed after the first turn because that endpoint lists only *loaded* models, memoized because the served window cannot change without a reload, and best-effort so a failed probe cannot break a turn; then a deliberately **conservative floor**, because guessing low merely compacts early whereas guessing high truncates the prompt with no error at all. The probe is vendor-specific and therefore lives in the app bundle; core keeps only the floor. Two smaller defects fell out of wiring the declaration and are fixed with it. **(1) The override had no reader on its only user-facing path.** `declared_context_window` demanded an `int`, while Settings' forms build provider options as `Record<string, string>` and `.trim()` every value and the handler persists the body verbatim — so the number a user typed arrived as `"32768"` and coerced to "undeclared". The one shared reader now accepts the numeric string the write path actually stores (`0`, `-1`, `True`, `""` and `"auto"` still mean undeclared, since a zero would reach a caller's `chars / window` and divide by it). The sibling `timeout_secs` option shipped with the identical inversion. **(2) The declaration could not reach a *measured* gauge.** Both provider adapters called `model_context_window(model, default)` positionally and the `local`/`override` keywords are keyword-only, so no declared value could reach the percentage the composer renders or the number the compaction gate compares — the declaration steered only the char **estimate**, which is precisely the path an endpoint that *does* report usage never takes. They pass `override=` now, and deliberately never `local=`: an operator's declaration is a truth claim and belongs in a measurement, whereas the conservative floor is a floor for an estimate, and substituting it into a real token count would have displayed 651% for the turn above. With nothing declared, every gauge is exactly the table lookup it has always been ([#2364](https://github.com/PersonalClaw/PersonalClaw/issues/2364), [#1774](https://github.com/PersonalClaw/PersonalClaw/issues/1774)).
- **The paired evals now score against a model that actually resolved, and refuse to score when none does.** [#2680](https://github.com/PersonalClaw/PersonalClaw/issues/2680). The loop-2 skill gate, the monthly ablation report and the skills bench each spawned their two arms with **no provider binding at all** — measured on `main`, all three, not just the gate the issue named. An eval cell is deliberately spawned into a throwaway home with a name-allowlisted environment and inherits no credentials, so "no binding" meant no model: both arms replayed byte-identically and the delta came back **0.0**. That zero is not a null result, it is a verdict — for the ablation, `0.0` *is* `REMOVE`, the one verdict that files a retirement proposal, so the failure mode was a recommendation to delete a component on the strength of a measurement nobody took; for the gate it read as "this candidate skill does not help". A new **Settings → Evaluations → "Benchmark model"** (`evals.benchmark_model_ref`) names the `Provider:model` those three runs score against, and every run now records what resolved: a directly bound run, a run that fell back to your default chat chain (the artifact says which position answered), and a run where nothing resolved are three distinguishable states in the persisted artifact rather than one. **What this changes for you:** a gate, ablation or bench run in a home where no model resolves now **refuses**, naming the unmet precondition (`evals.benchmark_model_ref`), where before it returned a score of zero — so an unbound home stops producing conclusions, which is the point. Leave the field empty to keep using your default chat model. The eval runner's refusal to inherit ambient credentials is unchanged and was not routed around; the binding is *expressed*, which is what lets the run's pin record a real `cell_model_fp` instead of `no_model`. Verified end to end against a local Ollama and in CI against a loopback endpoint: two arms whose staged artifacts differ now score **differently** (0.5 vs 1.0), which byte-identical replay could never show.
- **A watched page that builds itself with JavaScript now reports content instead of nothing, forever.** The gateway browse tier (the last escalation, reached only when a plain fetch *and* a headless render both saw an empty shell) navigated and then read the DOM in the same breath. `GatedCdpSession.navigate` only sends `Page.navigate` — it does not await the load event — so the read landed on the pre-render shell: no extractable text, a soft `ok=False` with the note "browse tick rendered no extractable text", and, by that path's own contract, **no advance of the content cursor**. The tick runner had accepted an optional post-navigate `settle` wait since it was written and its only production call site passed none, so every JS-rendered watched source failed this way on every poll, quietly and indefinitely. That call site now supplies a bounded wait: re-read the DOM every 250 ms until it is no longer a shell by the same measurement the earlier tiers escalate on *and* two consecutive reads agree, giving up at a 10 s ceiling and extracting whatever the DOM holds. An already-rendered page pays one 250 ms confirmation and nothing more; a page that never renders reports exactly what it reported before. A slow page stays a soft failure — the wait never raises and cannot hang a scheduled tick.
- **Re-ingesting a document now re-embeds only the sections that changed.** [#1783](https://github.com/PersonalClaw/PersonalClaw/issues/1783). The differential-refresh comparison existed but was never reached: the per-section digest map loaded off the item and *nothing wrote it*, so every re-poll of a watched source paid to re-embed every chunk of the whole document — a one-paragraph edit to a 40k-character report cost a full re-embed. The chunk-embed path now stores the digests it wrote, compares them on the next pass, and carries unchanged sections' vectors forward instead of sending them to the provider again; an unedited re-ingest makes zero embedding calls. Digests are keyed by heading text, not position, so inserting a section does not invalidate the ones after it. A carried-forward vector is refused unless its stored embedding fingerprint equals the model active now, so this can never mix two models' vectors in one item. The chunk-row set is still replaced wholesale, which is what keeps the ANN index and a shortened document in step. Along the way the second, incompatible chunk-hash form was deleted (`consolidation.chunk_hashes`, built on the dedup hash, which strips punctuation and so hashed `A > B` and `A < B` equal — a refresh on it would never have noticed that edit); `semantics.chunk_hash` is the one form. No stored data used the deleted form, so there is nothing to migrate.
- **A bundled app's CODE now reaches an already-installed home, not just its `app.json`.** `seed_builtin_apps` seeds once and then re-synced the manifest alone, so a `provider.py` fix shipped in a new wheel reached a *fresh* home and never an upgraded one — and because a native app is locked against `POST /api/apps/{name}/update`, there was no in-band repair at all. Both bundled apps that own their provider code (`personalclaw-ui-docs`, `ollama-models`) would have been frozen at whatever release first seeded them. Every packaged file is now re-synced on boot; `data/` (user config) and `installed.json` (enabled state) are still never touched, and `__pycache__` is excluded from both the first-seed copy and the resync so one machine's bytecode never lands in another's home.
- **`apps/native/*/*.py` is a declared package-data glob.** setuptools 84 already carried a bundled app's provider module into the wheel with no glob declared (measured: a `.txt` or `.yaml` beside it is dropped, a `.py` is not), but `build-system.requires` is an unbounded `setuptools>=64`, so that was a future silent regression — a manifest naming a module the wheel does not ship, visible only from a real `pip install`.
- **`POST /api/tools/invoke` can now run the nine filesystem and shell tools it always listed.** [#3310](https://github.com/PersonalClaw/PersonalClaw/issues/3310). `read_file`, `write_file`, `edit_file`, `list_dir`, `glob`, `grep`, `repo_map`, `bash` and `tool_result_get` answered `404 tool not found`, because the route resolved providers through the registry alone while the platform bundle is deliberately kept out of it (it is cwd-coupled, so it is built per caller). Three of its four consumers already prepended it; the one that executes did not. The route now builds one per call, confined to the configured workspace root — so the Tools page's "Try it" button runs the tools the same page lists, and a zero-token Schedule `script` gets the filesystem and shell reach `ScriptContext.call_tool`'s own documented examples assume. An unresolved workspace root refuses with `503 workspace_unresolved` rather than running them in whatever directory the gateway was started in. Path confinement and the destructive-risk confirmation gate are unchanged.
- **Workflow `rewind` and `run-from` now stop at the same committed-effect boundary as edits, and tool-bearing stages are inside that boundary.** Both re-entry verbs first return their computed cascade preview and require `confirm_cascade=true` before resetting completed work; the dashboard names every `committed_effects` node in that confirmation instead of showing only the re-run count. Effect attempt, preflight, retry, and terminal records now follow the dispatcher that can invoke tools, so stage-only workflows cannot silently re-fire external work. Class-B clean break under the pre-1.0 banner: callers that previously relied on implicit confirmation must resubmit explicitly. Run `personalclaw snapshot` before upgrading ([#2589](https://github.com/PersonalClaw/PersonalClaw/issues/2589)).
- **Notification settings no longer advertise production-unowned rows.** [#341](https://github.com/PersonalClaw/PersonalClaw/issues/341). The unreachable `loop/stalled` registration is deleted because blocked and stagnant loops already emit through the durable `loop/needs_input` path; historical `system/session` wires remain resolvable but no longer render or accept rule writes; and a successfully persisted research-report finding now raises its registered durable attention item. Each configurable kind declares the production module that owns it, and a registry rail verifies those modules exist and import the notification contract. This is a pre-1.0 clean break for the inert matrix rows: existing `loop/stalled` rules are tolerated and ignored. Run `personalclaw snapshot` before upgrading if you want a rollback point.
- **Pre-first-pass days no longer render as silent capture failures.** [#460](https://github.com/PersonalClaw/PersonalClaw/issues/460). The capture-week payload now exposes the local calendar day of the first pass, keeps days before that floor out of `silent_days`, and renders them as a distinct neutral out-of-scope state. The warning chip applies the same floor, while an empty floor preserves the quiet never-ran caption and a missing field preserves the conservative stale-payload warning.
- **Projects now expose both sides of their archived lifecycle.** [#356](https://github.com/PersonalClaw/PersonalClaw/issues/356). An active project's header offers Archive with confirmation that its work is preserved while it leaves new-work pickers; an archived project's header offers Restore. Both actions use the existing persisted project-update path, so the detail header, Projects list badge, and project pickers converge on the saved status.
- **Creating or editing a skill now rejects an invalid `SKILL.md` before touching disk.** Both dashboard write routes require parseable frontmatter whose `name` matches the skill key, a non-empty `description`, and a body no larger than 50,000 characters. They share the same loader-owned validator used by marketplace installs, so create and update cannot drift into separate format checks; a rejected update leaves the prior file unchanged ([#435](https://github.com/PersonalClaw/PersonalClaw/issues/435)).
- **The Automations week grid now defines both request bounds as explicit instants before comparing or projecting them.** [#497](https://github.com/PersonalClaw/PersonalClaw/issues/497). The browser sends each drawn local bound with its own UTC offset, including different offsets on opposite sides of a DST transition. The gateway resolves any remaining offset-free API value in its reported `server_tz`, then canonicalizes both bounds to UTC before validation and calendar projection. A gateway process running in another OS zone can no longer exclude fires near the browser week's edge, and an offset-qualified bound paired with a naive one no longer raises an aware/naive comparison `TypeError`.
- **Current macOS releases now use the sandbox capability they actually provide instead of being rejected by version number.** [#3271](https://github.com/PersonalClaw/PersonalClaw/issues/3271). `_probe_sandbox_exec` no longer returns `False` before probing on macOS 26 and later; it runs a file-profile probe against the current Python interpreter and lets that measured result select the backend. On hosts where the probe succeeds, `wrap_argv(..., mode="strict")` now engages `sandbox-exec` instead of returning the command unchanged while logging "app-level checks only"; an absent binary or failed runtime probe still falls back explicitly.
- **`personalclaw agent list` measures its columns instead of hardcoding them, so the table is aligned on a fresh install.** [#2949](https://github.com/PersonalClaw/PersonalClaw/issues/2949). The NAME column was padded to a fixed 20 characters while three of the agents shipped by default are longer than that — `personalclaw-code-planner` and `personalclaw-goal-planner` at 25, `personalclaw-template-refiner` at 29 — so every column after NAME shifted right by 5–9 characters on those rows before the user had created anything. It was invisible while the later fields were empty and scrambled the table the moment any of them were populated. `render_agent_table` now derives each width from its header and its rows, the way `render_type_table` already does for `--list-types`, rather than a second hand-tuned set of constants. The default ` *` marker is measured as part of the NAME cell instead of being concatenated inside a fixed-width field, so marking the default can neither eat the name's budget nor push the row out on its own.
- **A credential in a chat title no longer reaches the export download's filename (SM-8).** Exporting a chat derives the download name from its title, and `export_filename` sanitised that title without redacting it first — so a title carrying a secret put that secret verbatim into the `Content-Disposition` header, where it lands in the saved filename, in the browser's download history, and in any proxy log that records response headers. The export *body* was already redacted; only the name it was saved under was not, which is the more durable of the two leaks. `export_filename` now runs the shared `redact_field` before punctuation sanitising, and in that order deliberately: sanitising first rewrites punctuation inside a credential and can break it into a shape the redactor no longer recognises while leaving it perfectly identifiable to a reader. One redaction implementation still serves every export and share surface rather than a second pass that redacts slightly less.
- **The full local pytest gate no longer changes verdict according to installer choice, worker ordering, host shell startup, or scheduler delay.** [#684](https://github.com/PersonalClaw/PersonalClaw/issues/684). Plain editable installs now resolve the same cron parser major as CI's lock; snapshot fixtures change SEL homes without replacing a public class object already imported elsewhere; the terminal test asserts the environment at the subprocess boundary instead of executing a developer's login files; CPU-only workflow performance checks measure CPU time; and the inbound rate-cap test inspects the refusal it observed instead of making a later request after the bucket may have refilled. Two clean-main runs measured 8 and 7 failing node IDs, with a 6-node intersection and a 3-node symmetric difference. Five mutation controls restore the corrected failure modes and all five make their rails red.
- **Code-loop runnability now distinguishes a missing command binary from a project that has not been scaffolded yet.** [#319](https://github.com/PersonalClaw/PersonalClaw/issues/319). The SDLC gate resolves the leading command word before applying its existing project-manifest check, publishes `binary_not_on_path` instead of the false `project not buildable yet` label, and turns a repeated missing-binary stall into the actionable remedy: edit the stored command, then resume. Commands remain persisted, displayed, and executed verbatim — no interpreter substitution or host-specific rewrite. The Code cockpit receives the same computed host diagnostic, annotates the affected build/test action, and disables the dead terminal launch while leaving a manifest-missing command available.
- **A run ledger whose completed steps never recorded token counts no longer reports `tokens: 0`.** [#2630](https://github.com/PersonalClaw/PersonalClaw/issues/2630). `run_totals` now carries the existing nullable-scalar disclosure — `tokens_recorded` beside `tokens`, with `null` for an unrecorded count and a genuine recorded zero left as `0` — independently of `priced`, which continues to mean only that cost is known. One absent or explicit-null step taints the aggregate instead of making unknown usage look free. The workflow budget pre-charge now consults that disclosure: a resumed run with a token cap pauses before scheduling when its inherited spend is unrecorded, while uncapped runs and recorded-zero runs keep their existing behavior. Harvested baselines and the template-ledger API inherit the same honest shape from the aggregate; the current frontend ledger tab renders only step counts, so there was no token-total surface to change.
- **The token total a user actually reads now carries the same disclosure as the ledger's own aggregate.** [#3218](https://github.com/PersonalClaw/PersonalClaw/issues/3218). #2630 fixed `ledger.reader.run_totals` and left its twin: `workflows/introspection.py::run_stats` projects the SAME `events.jsonl` for the same run and summed the raw `event.get("tokens", 0)`, so absent and explicit-null collapsed into `0` while the primitive reported `null`. Four of seven cases disagreed — absent key, explicit null, a mixed run, and the loop-shaped step that `LoopJournal.cycle` writes with no `tokens` key at all, which is EVERY loop run — and the divergent one is the one on screen, because `IntrospectPanel`'s Tokens cell is the only rendered token aggregate and `run_totals` has no surface. `RunStats` gains `tokens_recorded` beside `tokens` (kept separate from `priced`, since a step can book a cost and report no count, and one flag over both would have to lie to one of its two readers), the flag reaches the wire through `to_dict`, and the cell routes through a new `runTokensStat` — `≥N` for a floor, the shipped `not recorded` words when no count was seen at all, and a plain number for a genuine recorded `0`. It lives in `web/src/lib/unrecorded.ts` beside the already-shipped `tokensUnrecorded` rather than in `runCost.ts`, because the word is `unrecorded` and not `priced`. `tokens` stays an `int` the economics strip can sort on: the honesty rides beside the number, exactly as `cost_usd` decided in #2566. Three rails close it — the seven-case agreement table asserted on the DISCLOSED shape (the only form in which a floor and a measurement are comparable), an API-level case with a step that recorded no count, since the existing agreement rail runs an echo provider that always records one and so passed through the whole divergence, and a new `event.tokens` entry in the unrecorded-vocabulary detector, whose `run_totals.tokens` patterns matched the AGGREGATE and therefore could never see the second producer reading the raw event one level below it.
- **The persistence toggle no longer lights up for a setting the host cannot honour, the durable-workers hint now answers the requirement it names, and "sessions survive a restart" has exactly one owner.** [#545](https://github.com/PersonalClaw/PersonalClaw/issues/545). Publishing `persist_available` fixed the headline — the Terminal header's *hint* stopped promising tmux-backed survival on a host with no tmux — and left the same shape behind in three places. The control itself stayed **enabled and lit**: `aria-pressed` reported ON for a flag `_persist_enabled` can only ever answer `False` to, and clicking it wrote a value that does nothing. Settings → Agent's "Durable worker sessions" row said *"Requires the tmux binary; without it this has no effect"* — naming a precondition it never consulted, twenty lines from a page that already had the answer on the wire. And both sentences were spelled at the call site, which is two independent derivations of one promise: precisely how the original bug shipped. So the claim itself is owned. `web/src/lib/persistClaim.ts` holds the fact (`persistAvailableFrom`, `usePersistAvailable`) and every sentence about it (`persistToggleCopy`, `durableWorkersHint`), behind functions that take the capability as an **argument**, so a surface cannot state the promise without consulting the host; the unavailable case returns `disabled: true` and `active: false` whatever the saved flag says, and the not-yet-answered case claims nothing at all. On the backend, `dashboard/handlers/terminal.py`'s private `_tmux_available()` is **deleted** — it was a byte-identical copy of `tmux_substrate.tmux_available()` in the module whose own docstring says that two copies of a tmux fact is how a reaper, a boot sweep and a label come to disagree — so the gate and the published capability are now provably the same call, and a test can no longer move one without the other. `persist_available` also rides the panel-**disabled** branch of `GET /api/terminal/sessions`, which previously returned its own literal payload without the key: since an absent field reads as UNAVAILABLE on the client (the only safe direction), omitting it there did not read as "unknown" but as "this host has no tmux", inventing a limitation on a host that has one — and Settings asks that endpoint for a feature that has nothing to do with the terminal panel. Three derived rails hold the boundary: `persistClaimHasOneOwner.test.ts` walks `web/src` and fails if any non-test source contains a tmux-survival sentence outside the owner (measured: exactly the two surfaces before, the owner alone after), `test_the_tmux_probe_has_exactly_one_owner` forbids a second `shutil.which("tmux")` anywhere outside `tmux_substrate`, and the probe is hardened so a failed capability read leaves the asking surface at "no answer" rather than replacing a settings form with an error boundary — which is what it did, for a hint.
- **An automation can no longer be EDITED into a state it could never run from, and the doctor names the rows already on disk that are.** [#779](https://github.com/PersonalClaw/PersonalClaw/issues/779) / [#687](https://github.com/PersonalClaw/PersonalClaw/issues/687). `create` has refused an unregistered action provider and an unparseable cron since those issues landed — and `update` refused neither, so the check was a create-time check with a PATCH-shaped hole beside it: save `notify` + `0 9 * * *`, then patch the provider to a name nothing dispatches or the expression to `99 99 * * *`, and the row is right back to the enabled-listed-and-never-armed state the refusal exists to prevent (`arm` refuses an unparseable spec rather than guessing a cadence, so `next_fire_at` is empty and `service.due_ids`, which selects on exactly that, never surfaces it). Both doors now answer through ONE function each — `spec_error_refusal` and `unregistered_action_provider_refusal`, extracted from `create` rather than written a second time, because two copies of a refusal are two sentences for one field and they drift the first time either moves. **The doctor gained the one finding it structurally could not produce:** `unknown_action_provider`. Its single provider-shaped check, `unfenced_write_action`, was neutralised by construction — `screen.capabilities_for_action` freezes the bogus provider name INTO the row's capability grant, so `action not in granted` was False and the row read healthy — and it is reported INSTEAD of the fence finding, never alongside it, because "re-save to freeze the grant" is not a fix for a name that resolves to nothing. The set it reads, `dispatchable_action_providers()`, is deliberately the same live registry the write path refuses against (built-ins registered first, since they register lazily on first action execution and a cold read would report every automation as unknown), so a create form and a doctor cannot disagree about whether a name is real. **An unparseable cron is deliberately NOT a second doctor finding:** `semantic_spec_issues` already owns that rule beside the fire path and the doctor already folds it as `unfireable_spec`; what was missing there was a rail, which this adds. **On the frontend, the cron field validates against croniter's grammar instead of counting tokens.** The old check was `split(/\s+/).length === 5`, wrong in both directions — `99 99 * * *` has five fields and `@daily` has one — so it passed the value the server refuses and flagged the macro the server accepts. `cronExpr.ts` is a one-sided validator (it refuses only what croniter refuses, so it cannot strand a user on a working expression) railed against a 363-line corpus shared with `tests/test_cron_expr_corpus.py`, and both surfaces that render the schedule form — Trigger create and the schedule edit panel — gate Save on the same exported reason the field renders, so the disabled button and the red field always say the same thing.
- **`pip install personalclaw` on Python 3.14 is now refused at install time instead of succeeding and handing you a connector-pack parser that refuses every import.** [#2621](https://github.com/PersonalClaw/PersonalClaw/issues/2621). `requires-python` was `>=3.12` — an open promise over every future interpreter — while `full.yml`'s matrix verified exactly two, so 3.14 sat inside the declared support range and outside everything that checks it. What 3.14 actually does there is worse than the metadata being optimistic: the connector-pack parse fence denies `importlib`, and the fence's third mechanism NEUTERS the escape-bearing callables on already-imported denied modules, so on 3.14 it replaces the import machinery's own `importlib._bootstrap._find_and_load`. Measured against the shipped harness (`harness_source()`, run under `python3.14 -I` with a 3.13 control): the legitimate script `import csv, datetime, hashlib, html.parser, io, json, re` — the repo's own vacuity fixture, the one asserting the fence is not "everything is refused" — yields **zero rows** and `ImportError: … import of 'importlib._bootstrap._find_and_load' is refused`, where 3.13 returns its row. So every pack on 3.14 parses to nothing, and the refusal names an importlib internal rather than the module the script asked for. The range is now `>=3.12,<3.14`, which is the resolution [#2621](https://github.com/PersonalClaw/PersonalClaw/issues/2621) lists first: cheap, honest, and reversible in the same change that makes 3.14 pass. **Deliberately NOT done: relaxing `assert "'os'" in failure.detail` to accept the importlib spelling.** That assertion is what proves a refusal names the *offending module* rather than wherever the interpreter happened to raise, and widening it would have turned the vacuity test's own failure mode green while leaving pack parsing broken — the issue says so explicitly, and the measurement above is why it is right. Fixing the fence for 3.14 means changing which attributes of the import machinery the neuter pass may touch, which is a sandbox-control decision and is not taken here. **The claim is now enforced rather than asserted:** `tests/test_dependency_bounds.py` derives both the support range and the version-specific `Programming Language :: Python ::` classifiers from `full.yml`'s matrix — on both ends, so the floor tracks the oldest verified interpreter and the ceiling the first unverified one — with a vacuity floor that reds if the matrix parse ever collapses to nothing. Widening support now requires adding the interpreter to the matrix in the same commit; the metadata can no longer promise what nothing runs.
- **A trigger set to fire faster than the 900s LLM-invoking floor now says so — in the form, on the list, and in the doctor — and a cosmetic edit no longer re-phases its cadence.** [#531](https://github.com/PersonalClaw/PersonalClaw/issues/531). `models.validate_spec` has raised a warning for every sub-floor interval since S87, and its own comment promised the row would be "visibly flagged". It was not, in three separate places at once. The wire projection passed `row.errors` only — there was no `warnings` key on the schedule or store-trigger row at all — so the store computed the warning on every load and no surface could read it; measured on a live gateway against the trigger the create page produces for *Interval / 1 / minutes*, `row.warnings` named the problem and the wire answered `broken: []`. `GET /api/triggers/doctor` answered `healthy: true, findings: []` on that same home, because `diagnose` reads projected dicts and `semantic_spec_issues` owns fire-path semantics and **neither re-runs `validate_spec`** — so the whole structural half (every unknown spec key, every missing required field, and the floor) stopped at the store. And `triggers/store.py::health()`, the one function in the package that summed `r.warnings`, had **zero production callers**: the only two were in its own test file. Both severities are now derived from the loaded row by a single owner (`_issue_messages`) for every projection, which also fixes the create, update and toggle responses — all three answered `broken: []` for a row the list showed as broken, so "the list and the write responses answer in the same shape" held for the keys and not their contents. The doctor folds `row.issues` for **every** kind rather than re-deriving the floor rule a second time (a second copy is a second owner, and they drift the first time the number moves); errors land as `invalid_spec`, warnings as `spec_warning`, each namespaced `schedule:`/`store:` so a finding joins back onto the row it is about. The list renders a warn-toned `· check schedule` with the messages in `title`, shown only when the row has no error — two verdicts side by side make the user decide which to believe — and the interval control states the floor at author time. **`health()` is deleted rather than given a caller**: a count is strictly weaker than the per-row, by-name signal that now reaches two surfaces, and inventing a consumer to justify a producer is the defect this issue is about one level up. Separately, the same edit path **re-armed the trigger on every cosmetic save**: `cadence_changed` was set from a key's PRESENCE in four hand-maintained places and the edit form sends `timezone` on every save, so two consecutive renames of one hourly job moved its next fire 04:33:08 → 04:34:21, each save re-phasing the interval by the wall time since the last one, and a job 59 minutes into its hour lost the hour. It is now derived by comparing the resulting clock spec against the stored one, with absent and empty collapsed exactly as `arm.cadence_next_fire` reads them (the form posts `timezone: ""` and `skip_dates: []` on every save, so a raw `!=` would call every cosmetic edit a cadence change) and `strict` the only declared non-cadence key — so a spec key added later re-arms by default, which is the safe direction. **The floor is an advisory, not a gate, at every one of these surfaces**: the backend deliberately warns rather than refusing (R1 makes it overridable — a fast local-model poll is a legitimate choice), so the form does not block Save, does not mark the input `aria-invalid`, and uses `role="status"` rather than `role="alert"`. Out of scope and deliberately not taken: the `secsToInterval`/`intervalToSecs` display pair stays lossy. A sub-minute stored cadence is preserved rather than displayed, by the narrow "don't send an untouched field" guard that already landed in `draftToPayload` and is railed by `renameKeepsItsCadence.test.ts`.
- **Four defects in the workflows engine, each one a documented promise the code did not keep.** **(1) A forked run can be started** ([#372](https://github.com/PersonalClaw/PersonalClaw/issues/372)). `_apply_fork` left its child in DRAFT deliberately — *"starting it is the caller's decision, because a fork is usually created to be edited before it runs"* — and the caller had no verb to decide with, so every use of **Fork run** added a run that could never execute. The nine run verbs all address a run already running, and two of them said so in their own remediation, which closed the loop: `run-from` answered *"resume the run before run_from"*, and `resume`'s no-token path popped `pause_requested` (a key a draft never has), saved, and answered `{"resumed": true}` with the status still `draft` — so the remediation pointed at the one call guaranteed to do nothing, and neither end was wrong on its own. There is now `POST /api/workflows/runs/{id}/start` and a `workflow_start_draft` tool, both gated on the run's lifecycle PHASE rather than the literal status (the same gate the policy-overlay editor uses, so the two prelaunch-only operations cannot drift); `resume` refuses a run that never launched and names `start`; and the re-entry remediation names `start` for a prelaunch run and `resume` for a launched one. Deliberately *not* a widened `start_run` — that takes a definition NAME and creates the row it starts, so pointing it at a fork would mint a second run and strand the lineage the fork recorded. On the UI side the run header had TWO branches for a THREE-phase lifecycle: `draft` is not terminal, so it fell into the live arm and rendered **Steer + Pause + Cancel** — controls for work in flight, on a run with no work in flight — with Pause itself inert (#370). It now reads **Start + Cancel** before launch, through the `isPrelaunch` set `workflowMeta` had already defined for the overrides editor. **(2) A nested workflow is bounded by its node's timeout, not by a hardcoded minute** ([#381](https://github.com/PersonalClaw/PersonalClaw/issues/381)). `dispatch()`'s one production call site passed no `timeout=`, so the signature default of 60s won and a `subworkflow` node stopped waiting for its child after exactly a minute regardless of configuration — recording *"subworkflow is still running"* with an **empty** outputs dict, which breaks every `{{nodes.child.output.x}}` binding downstream, for work the child then completed. No knob reached it: `node_timeout_total` was applied to the outer node kill and nowhere else, so the node's real budget and the wait it was meant to bound never met, while the dispatcher's own docstring asserted the opposite. The node's budget now reaches both dispatchers that wait on something (the child-run wait and an action provider's call), **minus a small reserve** — and that reserve is the point rather than tidiness: a dispatcher handed the same value as the outer `asyncio.wait_for` always loses the race (its timer is armed later), and losing it replaces the honest DEGRADED result, which names `child_run_id` so the live child is findable, with a bare timeout failure that names nothing. **(3) A workflow-materialized task is titled with the name its author wrote** ([#382](https://github.com/PersonalClaw/PersonalClaw/issues/382)). `plan_materialization` read `config.label`; a census of the bundled set found **92** materializing nodes carrying a node-level `label` and **zero** carrying `config.label` — so the `node_id` fallback was not a fallback at all, it was the only branch anything took, and it masked the miss. Boards filled with `recall`, `weigh`, `gaps`, `n`, `nested`, and because a node id is unique only within a run, four separate tasks rendered as four identical rows in List, Cards, Kanban and the dependency graph. `label` is now a declared field on the spec node model rather than an unknown key surviving in `extra`, the controller's projection passes it, and the read prefers it while still honouring `config.label` for an author who put it there. Fingerprints change for future materializations, which under the pre-1.0 banner is a clean break. **(4) Engine-owned fields on a workflow-managed task are actually protected** ([#390](https://github.com/PersonalClaw/PersonalClaw/issues/390)). `materialize.reject_write` was implemented, unit-tested, and had **zero production callers** — its only other mention in the tree was a comment asserting the invariant as though it held (*"one writer for a managed task's status, and a refusal … for every other path"*), while `PUT /api/tasks/{id} {"preview": "…"}` on a managed task answered 200 and overwrote the projection. The harm its docstring names is a board that disagrees with the run it is showing, and the user believes the board. The rule now reaches all three non-engine doors — `PUT /api/tasks/{id}`, `POST /api/tasks/bulk` with `op: update` (refused in phase 1, so one managed row cannot land beside honest edits), and the agent's `task_update` tool, whose ten-field allowlist excluded most engine fields but not `status` — through one resolver, with a new `engine_owned_field` 409 carrying `reject_write`'s own message so the alternative is named. Deliberately **not** enforced inside the `update_task` façade: the contract is an actor asymmetry, the engine and the loop both write through that façade, and a façade cannot know who is calling. Every refusal is paired with a rail proving a user-owned field still writes and an unmanaged task is untouched — a guard that made a projected row read-only would be its own defect.
- **Settings → Notifications is a promise the app now keeps, in the five places it was breaking it.** The per-kind delivery matrix is the one surface that states what will happen to each kind of notification, and five separate defects made it describe behaviour the system did not have: a row that could not fire, a reset that could not reset, a count of something else, and a delivery that never arrived. **(1) "Notify = a toast" was false — no notification raised a toast, in any mode** ([#343](https://github.com/PersonalClaw/PersonalClaw/issues/343)). `ne:toast` had six dispatchers and not one was on the notification path: all three consumers of a `notification` WS frame merely refetched the list, so `Badge` and `Notify` were observationally identical apart from up to 15s of poll latency, and the kind registry's own stated safety property — *"today every emitter that passes the global gate produces a toast"* — was describing something that did not happen. The gateway had already decided everything needed, because the three quieter modes return **before** `_broadcast`: a `notification` frame on the wire is by construction an `immediate` delivery. So a shell-level hook relays that decision to the toast host rather than re-deriving it, which is the division `nativeNotifications.ts` already uses (the gateway decides, the renderer actuates), and it is mounted in the app shell so the promise holds on every route rather than only where a notification list happens to be. `channel_dm` joined the dimmed-target list beside `push` in the same change: it is the one target with nothing behind it — `notification_rules.TARGETS`' own comment calls it *"accepted and persisted but inert"* — and it was the only one presented as a plain, live choice, so ticking it silently did nothing. **(2) Rows the matrix offered could never be delivered to at all** ([#341](https://github.com/PersonalClaw/PersonalClaw/issues/341)). `notify()` takes a flat wire string, so a registered `(source, kind)` pair is reachable only if some string maps onto it — and five did not: `loop/complete`, `loop/failed`, `loop/stalled`, `cron/failed`, and `guardrails/autonomy_revocation`, whose registration comment asserted the exact opposite ("its bare kind IS its wire string"). **Measured 5 of 32, not the 6 of 23 the report names**; the registry has grown since filing and the loop family had meanwhile been partly re-routed through the attention path, so the count had to be re-derived rather than quoted. The consequence was inverted controls: setting **Loop failed → Never** did nothing, while the control that *did* govern loop failures was **System error** — so quietening unrelated noise silently stopped reporting broken loops. The loop watchdog and the trigger substrate now emit their own typed kinds, and **the delivery gate reads severity from the registry instead of a local wire-string table**, which is what makes that safe: the old table ranked everything outside `{error, warning, inbox_alert}` as info, so a loop failure switched to its own kind would have dropped from rank 3 to rank 1 and started being eaten by quiet hours. Every historical kind keeps its exact rank (pinned), and the kinds whose declared severity the gate had been ignoring — `loop/needs_input`, `system/agent_request`, `approval/requested`, all SEV_WARNING in the registry and shown as severity 2 in the matrix — stop being filtered as severity 1. Its other half: **quiet hours dropped notifications you have to answer entirely**, so a loop that needed an answer at 02:00 left *no record at all* while its durable inbox row still counted toward the badge — the reason the gap was invisible. Those now return a third posture, `quiet`, and are recorded as a `badge`: persisted, counted, auditable, silent. That is deliberately a downgrade rather than the bypass the report suggested — a bypass raises a toast at 02:00, which is the one thing quiet hours exists to prevent — and `mute_all`/`min_severity` still drop, because both are the user saying "not at all" rather than "not now". The carve-out is **not `attention` alone**, which the report's suggested fix named and which the tree contradicts: `attention` means "this persists a durable row", and the info-ranked attention kinds use that for the opposite purpose — `learning/report`'s registration states that `immediate` + SEV_INFO is *"what make quiet hours suppress the PING while the artifact stays durable"*, and `test_lv4_identity_report` asserts an empty log for exactly that case. Severity draws the line the registry already drew: the SEV_WARNING attention kinds are the "you must decide" ones (`loop/needs_input`, `system/agent_request`, `guardrails/autonomy_revocation`, whose own comment names the first two as its precedent), and those are precisely the two the report measured as dropped. An info-ranked attention kind keeps its designed suppression, pinned by its own test. **(3) "reset" saved the default instead of clearing the rule** ([#285](https://github.com/PersonalClaw/PersonalClaw/issues/285)). It was `save(key, {mode: default_mode})`, so "back to default" produced "explicitly set to the same value as default": the row kept `configured: true` forever and stopped tracking the registry, meaning a later change to a kind's default would silently miss every user who had ever pressed reset, indistinguishably from one who never touched the row. It also masked itself — the chip rendered only while `mode !== default_mode`, so the buggy reset hid the only control that could have revealed the pin, leaving no UI path back to inheriting. The rules PUT now accepts `null` as "this key has no rule" (it is already a per-key partial merge, so that is a value in its own vocabulary), the chip follows `configured` alone, and an empty `{}` write — the reported `targets: []` route to the same phantom override — no longer persists. Its tooltip also stopped naming a mode the user cannot see: `immediate` is called **Notify** on every pill in that matrix. **(4) `GET /api/notifications`' `unread` counted pending INBOX items** ([#422](https://github.com/PersonalClaw/PersonalClaw/issues/422)), not unacked notifications. The inbox pivot on `unread_count()` is deliberate and documented, and wrong under this endpoint's key: measured live, the field read 33 while every badge rendered 41 over the same 74 rows, and the two drift independently. No frontend read it — `NotificationBell`, `NotificationsPage` and `HeroPulse` each compute `!n.acked` themselves — so it was dead weight that read as authoritative to exactly the consumer with no reason to doubt it. Corrected rather than deleted, since three surfaces already agree what the number means. **(5) The two `cron` rows had no emitter** ([#415](https://github.com/PersonalClaw/PersonalClaw/issues/415)): the ScheduleService removal deleted every one and left "Scheduled job failed"/"Scheduled job result" as configurable controls nothing could trigger, with the failure landing on `system/error` — a row the user never configured, and one they cannot quieten without also quietening every other error. The kind is restored rather than the rows retired, and **not by the report's own one-liner**, which would have emitted `cron/*` unconditionally from a substrate that also carries webhook, event, file and web_watch outcomes and so labelled a webhook's result "Scheduled job result"; the caller passes the one bit the decision needs (`trigger.kind == "clock"`). `cron/failed` was re-ranked to SEV_ERROR so today's delivery is what continues to happen — the severity contract that comment had arranged was already inverted as a side effect of the removal, and this makes it a decision. The stale comments in `notification_kinds.py` and `notificationMeta.ts` that still reasoned about `gateway.py:1299` went with it. **Why the suite could not see any of this:** it walked the wire maps — the compliant population — so it could check only kinds that were already reachable; a registered pair absent from every map was invisible to it. The new rail asserts the property directly, that every registered matrix row is reachable from some wire string, with a pinned one-entry exemption list that can only shrink. **Still open from the same reports:** `loop/stalled` and `system/session` remain rows nothing emits. `system/session`'s wire constant has no call site at all, and `loop/stalled`'s events (`stagnant`, `blocked`) are deliberately delivered as `loop/needs_input` because a loop waiting on you is a standing request rather than a moment — re-pointing them would move stalled and blocked loops out of the "Loop needs your input" rule, which is a product decision and not a routing fix.
- **An IPv4 address or a date in your prompt reaches the model as itself, not as `[REDACTED_PHONE]`.** [#3111](https://github.com/PersonalClaw/PersonalClaw/issues/3111). The outbound scan's phone pattern put its `{7,}` length floor on the separator *character* class, which counts dots and hyphens toward the length, so six digits of `10.0.0.12` and eight of `2026-09-19` both cleared a bar meant to mean "seven digits". Under the shipped default (`scan_mode` is `redact`) every IPv4 address and ISO-8601 date in an outbound prompt was substituted out before the call left the machine: `The gateway binds 127.0.0.1 by default.` arrived as `The gateway binds [REDACTED_PHONE] by default.`, and a timestamp lost only its date-plus-hour, so `2026-09-19 12:30:45 UTC` arrived as `[REDACTED_PHONE]:30:45 UTC` and stayed syntactically plausible with nothing downstream able to tell it had been mangled. The reported consequence is a judge node spending its one metered call answering that it could not compare a placeholder, then returning an empty conflict list: a false negative, not an error. The same count also feeds `findings`, so under `scan_mode=block` a prompt that merely mentioned a private IP was refused as PII-bearing, and the guardrail audit reported a `phone` category that was never there. **The candidate span is unchanged, and the narrowing is what moved:** a candidate must now carry seven actual digits, and spans that positively parse as one of three named non-phone grammars (dotted-quad IPv4 with validated octets, ISO-8601 date with its optional time/offset tail, plain two-part decimal) are masked out before candidates are scanned. Both narrowings only ever *discard* candidates, so nothing became newly redactable and no real phone number gained a path through, which is the property that keeps this from being a weakening rather than a fix. Masking rather than skipping is load-bearing: the candidate class spans whitespace, so `127.0.0.1 555-010-4477` is a single candidate, and dropping it for containing an address would have carried the phone number out untouched beside every IP in the prompt. `999.999.999.999` is *not* spared, because it is not an address and a string the pass cannot identify keeps its redaction. The phone pass had no test in either direction before this, which is how it shipped; it now has ten phone formats pinned as redacted, twenty-one non-phone shapes pinned as surviving, both orders of a phone beside a spared shape, an idempotency rail, and a generated-corpus rail asserting that no span the old pattern caught is released without one of the three named reasons (0 violations over 300k strings). **Still open from the same report:** the run ledger records the prompt *before* redaction, so replays and evals read text the model never saw. That half is untouched here.
- **`personalclaw doctor` now reports only what its checks actually established.** Three reports, one defect: a check that states a conclusion it never measured. **(1)** A git **worktree** or **submodule** checkout read as `git repo: ⚠️  not a git repo`, because the test was `(proj / ".git").is_dir()` and in both layouts `.git` is a regular FILE holding a `gitdir:` pointer — so the row was wrong for the very layout this project's own dev flow runs on, and its false branch also swallowed a case it never looked at (a project dir that is a SUBDIRECTORY of a checkout, where there is no marker here and git is still inside a work tree). What the check had established was "no `.git` directory here"; what it printed was a verdict about the repository. git is now the authority — it is the thing that is right about worktrees, submodules, subdirectories and bare repos — with the `.git` marker naming the SHAPE it found and answering alone when git cannot be asked, and a third state, `⏭  cannot tell`, for when nothing established either answer. Advisory either way: no branch appends to `issues`, so doctor's exit status is unchanged ([#2907](https://github.com/PersonalClaw/PersonalClaw/issues/2907)). **(2)** The node check warned `frontend needs Node 18+` over `Fix: install Node.js >= 16` — one requirement printed as two numbers, at both of the two sites, so a reader could not tell which minimum the software enforces. Both halves now interpolate the one `_MIN_NODE_VERSION` the comparison itself uses, which makes the contradiction structurally unstatable rather than merely corrected; and the `except` branch that printed `node: ✅` *after* the version parse failed — claiming the check had passed on the one fact it had not got — now reads `⏭  version unknown` ([#2908](https://github.com/PersonalClaw/PersonalClaw/issues/2908)). **(3)** On a fresh install with zero credentials stored and no `.env` file at all, both doctor surfaces asserted `credentials stored in .env at mode 0600` and printed the path of the file that did not exist — the CLI row hardcoded the literal without stat-ing anything and the `security.credential_backend` probe rendered `mode or '0600'`, while the probe's own evidence carried the honest `env_mode: ""`. 0600 is the mode the dotenv fallback **promises**; printing a promise as an observation also meant a `.env` sitting at **0640** reported `0600`, so the defect hid a real permissions problem as well as inventing a fake one. Both surfaces now render from one `credential_store_state()` — the same "single source of truth for both doctor surfaces" rule `credential_backend_warning()` already follows — which keeps `env_exists` and `env_readable` separate from `env_mode`, so absent, measured, and uninspectable are three different sentences and the promise is only ever stated in the future tense. The probe's evidence gained `env_exists`/`env_readable` so the file's absence is stated rather than inferred from an empty string, and because the CLI row now shows the mode it read, a loose one carries the sentence the probe already had ([#2922](https://github.com/PersonalClaw/PersonalClaw/issues/2922)).
- **The kiro-cli runner id the product advertised could not be bound, and now it can.** `runner_catalog.json` published this runner as `id: "kiro"` / `runtime_id: "acp:kiro"`, and `GET /api/agent-runners` handed that id straight to the user — but the only bundle implementing the provider registers `acp:kiro-cli`, so binding the advertised id and sending a turn failed with `ProviderResolutionError: unknown provider entry 'acp:kiro'; known entries: ['acp:claude-code', 'acp:codex', 'acp:kiro-cli']`, with the CLI installed, authenticated and the bundle enabled. The same turn on `acp:kiro-cli` ran fine, which is what made this a naming defect rather than a capability limit: **the product advertised one id and accepted another.** claude-code and codex never had it — their rows match their bundles exactly, so kiro was the only one of the three that disagreed with itself. Two knock-ons went with it, neither of which a reader should have had to derive: `definition_for_runtime("acp:kiro-cli")` returned `None`, so kiro joined **no** health row and **no** capability sidecar — which is why its runner row could report a version while its capabilities read `null` — and with `agents.unattended_requires_verified_adapter` on, an unattended kiro spawn was refused for "no runner-catalog row" (that flag defaults `false`, so it was a live trap only for whoever turned it on). **The catalog was corrected, not the registry aliased:** the row is now `id: "kiro-cli"` / `runtime_id: "acp:kiro-cli"` / `display_name: "Kiro CLI"`, which is the spelling the rest of the tree already used — the `NOT_GATEABLE` registry keys on `kiro-cli`, and the web layer's own `categoryLabel('kiro-cli')` already rendered `Kiro CLI` — so the catalog row was the sole outlier. Adding an alias to the provider registry so that `acp:kiro` resolved would have been a compat shim for a name nothing should have published; `acp:kiro` now honestly resolves to nothing. **Why the existing rail could not see it:** the shipped-rows test asserted only that every `runtime_id` starts with `acp:`, which `acp:kiro` satisfies — a shape check, where being *bindable* is the property that matters, and nothing joined the catalog to any other provider registry. The new rail uses the host's own canonicalizer as that join: every shipped row's `runtime_id` must already BE the canonical provider name, never one of `permission_authority._PROVIDER_ALIASES`' aliases for it. That alias table maps `"kiro" -> "kiro-cli"`, which is precisely why the permission layer kept working while the provider registry — which has no alias table — refused; an alias in this column means one surface is compensating for a name and the binding surface is not. Asserted as canonical-form equality rather than against a hardcoded id list, so a new row that ships an alias reds without anyone remembering to extend anything. Measured as-a-user against `kiro-cli 2.22.1`: after the fix `GET /api/agent-runners?probe=1` publishes `id: "kiro-cli"`, `runtime_id: "acp:kiro-cli"`, `health.ok: true`, `version: 2.22.1`, and both knock-ons are gone (`AAPX-2`).
- A memory **entity** can be deleted, and the deletion sticks. The entity list was create-only — no route, no client method, no control — while the same panel proposes NEW entities to accept, so the list grew and could never shrink and a mistyped entity (the type select defaults to `person` and applies silently) was permanent. `MemoryGraph.delete_entity` had been there since the graph shipped with zero callers above it; the studio's own comment gave the reason, that removal "has to reason about the links pointing at it" — a question the store had already answered, so the fix is to state that answer in the confirm dialog ("3 memories link to it. Those links are dropped — the memories themselves stay.") rather than withhold the action. Two things that would have made the delete cosmetic are fixed with it: the seeders behind Health's rebuild (`memory_linker`'s facet and knowledge passes) matched live rows only and would have re-created a deleted entity under a fresh id, so a **tombstone now outranks every automatic source** and only an explicit declaration — or accepting the name again as a proposal — brings it back; and the alias index is invalidated on delete, so a removed entity stops matching text instead of being re-linked until the next restart. Re-declaring an existing name with a *different* type is now refused with the existing type named and the way out, instead of answering `{ok: true}` and changing nothing — same name + same type stays idempotent, which is what alias merging and re-seeding rely on ([#524](https://github.com/PersonalClaw/PersonalClaw/issues/524)).
- **The documented `config get > f.json` → edit → `config set --file f.json` loop no longer deletes every provider you have configured.** [#951](https://github.com/PersonalClaw/PersonalClaw/issues/951) was fixed for `config set <key> <value>` — the path its title names — and two siblings on the same root cause were left standing, which together made the round-trip destroy *more* than the original single-key write did. `config get` printed `AppConfig.to_dict()`, a serialize-from-known-fields snapshot of the ~40 sections the dataclass models; `providers`, `use_cases`, `slack` and `meta` are not among them (`providers` is read directly off the raw dict), so a bare `config get providers` answered **`❌ Unknown key: providers`, exit 1, for a block sitting in the file**, and `config get` with no key emitted a document systematically missing all four. `config set --file` then wrote that document back **wholesale**. Driven end to end against a home holding a real key, all four blocks were gone afterwards and the command printed `✅ Config loaded from …` and exited 0 — deletion by omission, on the one path whose entire purpose is restoring a config the operator believes is complete. Both now go through the same primitive the single-key write uses, so the three write paths agree instead of being fixed one at a time; **`config get` shows what the file holds** (and still refuses a key that is genuinely absent, which is the half that keeps it honest), and **`--file` merges rather than replaces**. Preservation is additionally **derived** rather than enumerated: `AppConfig.save()` carried a hand-written `("providers", "use_cases", "slack")` copy-forward tuple, so a *fourth* unmodeled top-level block would have been dropped by every write until somebody remembered to extend it — the same bug waiting for a new key. The rule is now "every top-level key this write did not itself serialise is copied forward", which needs no list; `meta` stays authoritative because the save stamps it. The duplicated "can I safely overwrite this?" reader is gone with it — `save()` raised `ConfigPreserveError` while the CLI printed and exited 1 from its own copy of the same logic, and a second spelling of that question is how one path gets fixed while the other does not. `config set --file` now also refuses an unreadable config (absent is safe to write over, unreadable is not) and a JSON document that parses but is not an object, both of which previously replaced the file. The rail asserts the property rather than the call: a config write must have *consulted the existing document* before reaching `atomic_write` — either by starting from it or by merging its remaining keys forward — with a vacuity floor that fails if the scan stops finding the three known write sites ([#951](https://github.com/PersonalClaw/PersonalClaw/issues/951)).
- **`personalclaw config get` no longer prints your provider API keys and Slack tokens, and the fix does not delete them instead.** [#3125](https://github.com/PersonalClaw/PersonalClaw/issues/3125). Making `config get` show what the file holds (#3119, above) was right; printing the credentials in it was not. The blocks core does not model are exactly the ones that hold secrets — `providers` is *the only copy* of an API key entered in the dashboard, and the legacy `slack` block holds a bot and an app token — so a display path that copied every unmodeled key through verbatim put three credentials on stdout for a `config get` with no key at all. Credentials are now withheld by default, printed as `••••••••` with a note on **stderr** naming what was withheld (a silent redaction is indistinguishable from a config that has no secrets in it), and `--reveal` prints them for the operator who actually needs them. **The half that matters more is the write side.** Masking the read alone would have converted a disclosure into the deletion of the only copy of the credential: `config get > f.json` → edit → `config set --file f.json` is a documented loop, and the merge that protects it is key-level and shallow on purpose — so a masked document already *names* `providers`, nothing is copied forward, and the placeholder is what `atomic_write` persists. A field arriving as the placeholder now means "keep the stored value", matched by **path** so the write cannot drift from whatever the read masked, and a placeholder that cannot be matched to a stored value **refuses the write** rather than guessing — `providers` records pair by their own `name`, because an index-paired restore over a reordered list would silently write one instance's key onto another. One policy, not a fourth copy of it: the CLI imports `apps/secret_fields.py`, the same module both config routes use, and the rail that asserts the policy has exactly one implementation now covers three modules instead of two — it was HTTP-scoped, which is precisely why it could not see a read path that serves no route. Sensitivity on this surface is **derived from the field name**, because no `settingsSchema` describes these blocks at all (`providers` is a raw list, `slack` is not a provider extension) and a schema lookup would go vacuous for an app that is not installed — turning the mask off for exactly the config someone is inspecting by hand. Measured against the real `AppConfig`: it matches one of ~340 modelled names and masks none of the 26 non-empty modelled strings.
- **Launching a loop from Plan Review no longer wipes every `kind_config` field that screen doesn't render.** A **research** loop reached Launch and lost its subtopics, output template, output manner, primary deliverable, breadth/depth budget and `granularity` dial — everything the multi-step research intake had just authored — with a `200` and no warning, at the one click a user cannot undo. Measured end to end on a real gateway: the stored `kind_config` went from **16 keys to 2** (`{goal_type, sub_goals}`), and the generated `brief.md` lost all three marker strings the control run carried. Two of the losses bite beyond the missing text: `granularity: "forever"` became absent, and `granularity.dial_for` falls back to `_DIAL["balanced"]` on a missing key, so a loop the user explicitly set to run **forever** would self-complete as soon as `returns_exhausted` tripped at threshold 2.0 / window 2; and a wiped `primary_deliverable` also degrades the judge's ground-truth read, which resolves deliverables out of `kind_config`. Research routes through this goal-shaped screen deliberately (`LoopsSection.tsx` excludes only `design`), so the screen that owns five or six goal keys was writing the column that holds *every* kind's fields. **`kind_config` is now a PATCH on `PUT /api/loops/{id}`, merged over the stored config.** Every other field on that route was already a patch — an absent key is left alone, which is why a name-only or `workspace_dir`-only PUT works — and `kind_config` was the one exception, because it is a single JSON column written with one `json.dumps`. The two halves of the request had therefore **disagreed**: `validation.spec_edit_errors` already merged the body's `kind_config` over the stored one "so a partial patch is judged in context", so the request was screened as a patch and written as a replacement. The merge is deliberately on the **server**, not in the browser: `store.get_redacted` passes `kind_config` through `files._redact_value`, so the config a client holds is a redacted view, and the obvious client-side fix (spread your own copy back into the write) would have persisted redaction placeholders over the user's real text — trading a dropped field for a corrupted one. The flip side is that omission now means *keep*, so a caller must be able to **clear** a key it owns: an explicit `null` deletes it, and Plan Review now names all six of its keys on every launch, sending `null` for the ones it holds no value for. That clear is load-bearing rather than tidy — `instrument.py` resolves `kind_config["verify_command"]` as the reproduce anchor without re-reading `goal_type`, so a goal switched away from `verifiable` has to be able to drop the command it no longer runs on. `store.update_spec` still replaces the column and its two in-process callers still merge explicitly before calling it, so there is one merge per layer and no dual path ([#411](https://github.com/PersonalClaw/PersonalClaw/issues/411)).
- **`personalclaw snapshot` now carries your provider API keys, and the durability census stopped counting decisions that had already been made as debt.** `credentials.json` — the provider credential descriptors `CredentialStore` writes at `0600` — was declared in neither the durability inventory nor its ignore list, so the command the pre-1.0 release notes tell users to run **before upgrading** did not carry it, and a restore came back with every model provider's key gone. That is the same asset [#951](https://github.com/PersonalClaw/PersonalClaw/issues/951) destroyed from the other side, a `config set` dropping `providers[]` with the keys in it. It is now a `secret=True` entry in the named `security` component, which is the deliberate path for key material (copy-if-missing, chmod `0600`) — not `everything`, because secrets are excluded from the generic restore pass on purpose, so an entry riding that projection would be captured and never returned. Declaring it needed no guess, which is what separates it from the stores that stay pinned: the risky field is `merge`, and for a secret it is structurally unreachable — shards are built from `export_entries()`, which drops secrets, and the merge only ever runs on rows imported from a shard. Same posture, same argument, as `auth/` in [#130](https://github.com/PersonalClaw/PersonalClaw/issues/130). **The census rail behind this had a blind spot of its own:** the inventory accounts for a path two ways — an entry **claims** it, or `IGNORED` deliberately excludes it — and `audit_home()` has always honoured both, while the static census read only the claims plus its own hand-written copy of `IGNORED`'s rows. So five locations settled the *correct* way for machine-local state (`session_key`, `sessions.json`, `update_check.json`, `update_releases.json`, `doctor`) were still counted as undeclared debt, and because the stale-pin ratchet could only notice a pin that became *declared*, the debt set could shrink by declaring and never by ignoring. Both surfaces now ask one exported predicate, `inventory.is_accounted`; the duplicated exception list is gone, and its one non-duplicate row, `loop.md`, moved to `IGNORED`, where `audit_home()` can finally see it — while it lived only in the test, the production guard reported it as unmanaged drift on every home that had ever run a loop. Measured on this branch, `_UNDECLARED_DEBT` drops from **27 to 21**, and those 21 are the genuinely-undecided stores. **A co-located defect found in the same write path:** `GET /api/model-providers` reported `credential_status: "missing"` for every provider that has a credential, because it passed `config_dir() / "credentials.json"` where `CredentialStore` expects the **home directory** and derives that filename itself — so it read `credentials.json/credentials.json`, loaded nothing, and swallowed the resulting `KeyError` into "missing". Ten of the eleven call sites in the tree already passed a home; a source rail now bans the eleventh shape, with a vacuity floor that caught its own first regex matching nothing ([#2217](https://github.com/PersonalClaw/PersonalClaw/issues/2217)).
- **`personalclaw config set` no longer deletes every configured model provider.** One `config set agent.log_level DEBUG` destroyed 10 provider instances and, for `openai_compatible`, the only copy of the API keys entered in the dashboard — reporting `✅` while doing it. The command served the whole file out of `AppConfig.to_dict()`, a fixed literal of the 40-odd sections the loader models, and wrote it straight through `atomic_write` without going through `AppConfig.save()`. `providers` is not in that literal: it is read DIRECTLY off the raw dict (`validation._DIRECT_READ_TOP_KEYS = {"providers", "meta", "slack"}`) while being the canonical store for provider instances, so the round-trip did not *empty* `providers`, it never serialized it. `use_cases`, `slack` and `meta` went the same way. The two write paths had **disagreed** about this: the dashboard PATCH already read the raw JSON, applied the one validated field and wrote the merged document back, which is why the API preserved these blocks while the CLI destroyed them — and why the fix is to make the CLI do the same read → apply → write-merged sequence rather than to special-case the one key in the bug title, which would have left the other three destroyed. Resolving the dotted key and *applying* it were one operation (a setter that refused a missing parent), and splitting them is the whole change: the Unknown-key check stays on the model dict, where every section is materialized, while the write lands on the raw document, where a section the operator never touched is legitimately absent. Two consequences worth stating. `config set` now writes only what was actually set over what was actually there, instead of materializing a fully defaulted 22 KB config as a side effect of changing one knob. And a config it **cannot read** is now refused (exit 1) rather than overwritten: merging only preserves `providers` if the base was genuinely read, so a file caught mid-flush by a concurrent writer or held by a permission blip would otherwise have serialized a base that never saw the block and deleted it again — the same "absent is safe to write over, unreadable is not" rule `AppConfig.save()` enforces with `ConfigPreserveError`. An **empty** file stays `absent` by that rule, since zero bytes hold no block a write could destroy and refusing would leave a config truncated by a crashed write permanently unwritable. No `config.json.bak` was added: with the write no longer destructive there is nothing to roll back, and copying a file that holds provider API keys to a second unmanaged path on every `config set` would trade a fixed data-loss bug for a standing secret-sprawl one. The rail asserts all four blocks together, plus the refusal and its vacuity floor, because a per-key check passes while the other three keys are still being dropped ([#951](https://github.com/PersonalClaw/PersonalClaw/issues/951)).
- **A tool call's input has one owner, so a persisted chat renders the same as a live one.** `renderToolInput` patched a resolved `inputObj` onto a COPY of the segment before dispatching to a native override, while the OUTPUT path handed the segment over untouched (issue 682). A renderer that read `seg.inputObj` directly therefore looked correct on the input path and rendered nothing on the output path — `web_fetch`'s card lost the URL line it is titled by on every session reloaded from history or ACP, where the input arrives as a string. Both paths now dispatch the segment as the caller gave it and `resolveInputObj` is the single owner of where a call's input object comes from, so a raw read fails on BOTH paths, where the registry-derived parity rail catches it instead of one path masking it.
- **The health strip no longer goes coral just because you set a login password.** `audit_home()` is wired as the `durability.inventory` Doctor probe, and `auth/` was declared in neither `INVENTORY` nor `IGNORED` — so `GET /api/doctor` returned `worst: "durability"` with `unclaimed: ["auth/"]` on any install where the owner had ever set a password, enrolled 2FA, or generated a pairing code. `credentials.py`, `enrollment.py` and `pairing.py` all resolve into `config_dir() / "auth"`, so doing the security-recommended thing turned the strip coral — the exact ordering hazard of wiring a probe before its manifest is complete, materialized on one path. `auth/` is now declared, and the argued half is **which posture**: `secret=True`, not `IGNORED`. Both make the probe green and only one is correct. `IGNORED` was the cheap fix available and would mean a user restoring their own snapshot comes back locked out of their own gateway, since `auth/credentials.json` is the argon2id login hash — a restore that silently drops the login is worse than the false alarm it fixes. `secret=True` is the posture the manifest already states for that case, and already applies to the neighbouring `credentials` tree: **excluded from exports** (via the `secret` projection that replaces `portability.EXPORT_EXCLUDE`) but **captured by snapshots on purpose**, so a backup can restore the credential store. Deliberately distinct from `machine_id` / `session_key` / `sessions.json`, which stay `IGNORED` because they are per-install *identity* — carrying those would let a restored copy masquerade as the machine it came from, whereas `auth/` is the owner's own credential store and travels with the owner. Declared as the whole tree rather than per-file: the pair/enrol code files expire on their own, and claiming only `credentials.json` would leave the directory unclaimed and the probe coral, which is the bug. The `auth` row is retired from `_UNDECLARED_DEBT`, which the census's stale-pin rail requires ([#130](https://github.com/PersonalClaw/PersonalClaw/issues/130)).
- **A scanned document read only as far as the page cap now SAYS so, and the OCR true-type gate covers both OCR backends instead of one (KOCR-2).** Two gaps measured live on a running gateway, each invisible to the rail that claimed the ground. **(1) The truncation was unreportable.** `pdf_rasterize` enforces the 40-page ceiling correctly and returns `pages_capped` / `page_cap` / `pages_rasterized` — and those three facts went nowhere, because that node is `pooled=False`, so its metadata feeds the next node and reaches no user-visible surface. A 120-page text-less PDF therefore stored the text of exactly 40 pages with `page_count: 120` beside it and nothing anywhere saying the read had stopped: the item's `file_metadata` carried only `content_hash`, `format`, `page_count` and `node_phases`, and the `ocr` pool row's metadata was empty. The shipped rail stayed green throughout because it asserts `_render`'s **return value** rather than anything persisted — the defect's camouflage. The runner now promotes the three facts onto the item as `ocr_pages_capped` / `ocr_page_cap` / `ocr_pages_rasterized` (the same promotion `node_phases` already gets, for the same reason: a ceiling nobody can see is indistinguishable from no ceiling), the knowledge detail view renders a **"OCR read first 40 of 120 pages"** caveat next to the page count, and a re-ingest that no longer caps clears the keys rather than claiming a stale truncation. The new rail reads `get_item`, not `_render`, and a one-page scan must carry no truncation claim at all — so the assertion cannot be satisfied by hardcoding it. **(2) The gate was engine-path-only.** `ocr/filetype.py`'s byte-level true-type check was called solely inside `OcrEngineNode.run`, so with no OCR engine app installed the executor resolved the `vision-llm` backend instead and that path checked nothing: uploading a plain-text file named `not_really.png` returned HTTP 200 with `node_phases` `ocr=done`, no `ocr=rejected` marker and an empty pool. Identical bytes, opposite handling, decided by which backend happened to be resolvable — and ARCC `cnt_eMkU5kkpTaEk65` ("Secure File Uploads") requires validating the true type *before* a consumer processes the file, which the model path was not doing. The gate now belongs to the `ocr` **node type**: one shared `partition_images` in core (promoted on `sdk.ocr` for app bundles) that both backends call before a decoder or a model sees the bytes, and one shared refusal shape so a surface keyed on `ocr == "rejected"` cannot go blind because the other backend spelled it differently. The rail hands the same liar file to both registered backends and requires the same refusal from each, and asserts the vision model was **never invoked** — not merely that the output says "rejected". Class-B behaviour change: a non-allowlisted or mislabeled file that previously reached the vision model for OCR is now refused (its EXIF/vision description path is unchanged). Clean break under the pre-1.0 banner — no gate, no migration.
- **Settings → Agent defaults no longer shows a "Sandbox" switch that makes no sandbox decision.** `agent.sandbox` (`"auto"`/`"off"`) round-tripped through the dataclass, `load()`, `to_dict()`, the `_EDITABLE_CONFIG` PATCH allowlist, the panel control and the config reference — and **nothing read it**. Sandboxing is decided by the `mode` argument threaded into `sandbox.wrap_argv`, which reaches `detect_backend(config_mode=...)` from a per-provider option (`sandbox_mode`, resolved in `llm/acp_agent.py`) or a literal at each call site; the config field never entered that chain, so "Sandbox → Off" changed nothing and a configuration review of `sandbox: "auto"` implied a guarantee the system was not making. The failure direction was fail-safe — the default kept the sandbox on, so nothing was silently disabled — but a security control whose switch is disconnected from its enforcement is worth removing rather than leaving ambiguous. **The field is removed, not wired**, on all six surfaces plus the `detect_backend` docstring that named it (which is what made it look wired in the first place). Wiring it would have required choosing which of `wrap_argv`'s five call sites a global toggle overrides — including two deliberately hardcoded boundaries, `knowledge_providers/pack_parse.py`'s `"strict"` for untrusted knowledge-pack parsing and `schedule_script.py`'s `"standard"` — and what precedence it takes over an explicit provider option: a sandbox policy this codebase has not decided, and not one to mint while closing a bug. Removal changes no runtime behaviour by construction, because the field had no reader. Anyone who wants the OS path sandbox off still sets the provider option `sandbox_mode`, which is unchanged. One rail asserts the absence across the dataclass, the allowlist, `to_dict()`, the panel, the config reference and `sandbox.py` **together**, because a per-surface check passes while any single surface still carries the field ([#364](https://github.com/PersonalClaw/PersonalClaw/issues/364)).
- **A loop's first cycle is credited again: a fast first cycle no longer leaves its SDLC stage un-advanced.** The watchdog seeded its progress baseline from the *current* finding count on first observation and `continue`d past the progress branch, so a cycle whose worker finished before the first 5s poll was absorbed into the baseline — never counted, and, because the kind's `on_new_cycle` hook hangs off that same branch, never staged. The smaller the task the likelier the worker won the race, so this bit hardest on exactly the simple loops a user tries first: a healthy-looking `running` cockpit with stage 1 complete and the remaining stages pinned `blocked`/`waiting`, and **Resume made it worse** — the re-seed clause also fires when `_last_activity < started_at`, so the obvious recovery re-absorbed the uncounted finding. The liveness clock and the progress baseline were two different concerns sharing one branch; they are separated now. `_last_activity` still refreshes on first sight and after a (re)start, while `_last_count` seeds at **0** on genuine first sight and is left alone across a re-seed, and the poll falls through to the progress branch whenever there is uncredited work. A poll with nothing new to credit still returns exactly where it did, so a fresh loop's seeding poll is unchanged. The rail is a parity property over the kind registry — every registered kind that declares `on_new_cycle` must credit a pre-existing cycle-1 finding, computed from `registered_kinds()` so a kind added later is covered without editing the test — plus the converse, that Resume does not re-fire the hook for a cycle already credited. This also fixes the same swallow after a **gateway restart**, where a fresh watchdog meets running loops whose findings are already on disk ([#320](https://github.com/PersonalClaw/PersonalClaw/issues/320)).
- **One unreachable git source no longer makes the Store take two minutes to open, or re-pay that cost on every load.** Measured on a seeded home: adding a single blackholed URL took a cold catalog load to **135.55s**, and roughly 60s on every load after. Three causes, all fixed. **(1) Nothing bounded the total.** `available_catalog` calls its scanners in sequence and each walks its sources serially; the only bounds were per-git-process (`timeout=60`/`30` in the registry read, `90` in the subdir scan), which bound one clone and never the sum — and do not bound the DNS/TCP connect underneath, which is where a blackholed address spends its time. The build now carries a wall-clock budget that **both** network scanners honour, and each git call is clamped to the time actually left. Bounding one scanner and not the other would have changed nothing, since a bad source is walked by both on a cold load. A total alone is also not enough: clamping each call to "whatever is left" hands one dead source the entire budget in a single connect — at a 45s total that produced a 45s load returning *zero* apps from the healthy source — so no single source may exceed its own per-source ceiling, which sits above a legitimate clone and well below the total. **(2) A transient failure was never cached** (`return None  # transient git error → don't cache`), so a *permanently* bad source paid full price forever. A failing source is now cached and backed off geometrically from a short base up to the success TTL — never longer, so a repo that went away and came back is always retried and "unreachable" never becomes permanent — and one success clears the streak. Known-failing sources are also tried **last**, so a dead source cannot starve a healthy one out of the shared budget. **(3) The failure was discarded**, so nothing named the source at fault and the natural diagnosis was "the Store is broken". `unavailableSources` now travels on the wire and the Store badges the offending row (`Unavailable`, or `Skipped` when the budget cut it) with a tooltip saying it will be retried. Same configuration after the fix: **53.31s** cold — returning all 65 apps, with both bad sources named — and **0.5–0.7s** on every load after ([#408](https://github.com/PersonalClaw/PersonalClaw/issues/408)).
- **A parallel loop phase no longer loses a task's worktree to a race inside `git worktree add`.** `worktree.add_worktrees` fans creation out through a bounded thread pool, and `git worktree add` is not safe against a concurrent `git worktree add` in the same repo — it fails HARD, not slowly. Measured on git 2.55: an add writes `.git/worktrees/<id>/gitdir` **before** that entry's `commondir`, so a sibling entry is briefly discoverable while its `commondir` is still a zero-byte file; polling the directory through a wide batch caught that state directly (`commondir=0 gitdir=118`), and forging it makes a concurrent add die on demand with `fatal: failed to read .git/worktrees/<sibling>/commondir: Undefined error: 0`, exit 128 → `add_worktree` returns `None` and the scheduler loses that task's worktree (~1 round in 20 for an 8-wide batch locally; it is what turned `main` red on the macOS/3.12 matrix leg). The same forging shows the blast radius is **exactly one command** — `checkout`, `sparse-checkout set` and `status` inside a worktree all survive the half-born sibling — so the fix serializes only **registration**: `git worktree add` now always runs `--no-checkout` under a per-repository lock, and the hydrating `checkout` (the half that costs the time the pool exists to overlap) stays in the pool. Two assertions pin both halves, neither of them a stopwatch: registrations peak at one in flight, and two hydrations must still meet at a rendezvous.
- **Trigger "Silent" is a switch again, not a one-way latch you can turn on and never off.** Creating or editing a schedule trigger with **Silent OFF and no notify channel** wrote `delivery = ""`, a value the `delivery` vocabulary does not define — and three readers turned that blank into `"none"`, which *is* the silent value: `parse_trigger`'s `str(data.get("delivery", "none") or "none")`, `delivery.route_for(trigger, ok=True)`'s identical `or "none"` on the fire path, and `is_silent` reporting `silent: true` to a user who chose OFF. `PUT {"silent": false}` on an already-silent row re-derived `channel_of() == ""` (prefix-only) and landed back in the same `else ""` branch, so the only way to reach a non-silent state was to *also* set a channel. Both write paths now emit the vocabulary's real "deliver normally" value, `"inbox"` — the same default its failure peer `Trigger.failure_delivery` already carries — and both coercions are gone, so a blank travels as a blank and `is_muted` stays the single place that decides what silence means. That also repairs rows already on disk. `delivery` is additionally **validated** on load: `"emial"` used to parse through with zero issues and reach `deliver`, which mutes only `"none"`, so a typo stored a route nothing honoured and nothing reported; an unreadable value now raises a parse `Issue` and falls back to `"inbox"` rather than to the dataclass default, because that default is silence and a route we cannot read is a bug that must not quietly become a mute. An **absent** `delivery` still takes the declared default — absent means "nothing was said" where `""` means a writer said blank, the same distinction `failure_delivery` had to re-learn. **This stopped being cosmetic after the issue was filed:** when reported, `delivery` had no fire-path consumer; since then `gateway._deliver_fire_outcome` wired `build_delivery(destination=route_for(…))` and `delivery.deliver` enforces `if is_muted(...): return False`, so every "Silent OFF, no channel" row on disk was genuinely dropping the success notification its owner asked to receive. Failures were unaffected throughout (`route_for(ok=False)` returns `failure_delivery`, default `"inbox"`), which is what kept this short of data loss ([#450](https://github.com/PersonalClaw/PersonalClaw/issues/450)).
- **A cancelled CI run no longer publishes a verdict, and `main`'s verification run is no longer killed by the next merge.** Three verification surfaces were reporting things that had not happened. **(1)** `ci.yml`'s `test` gate and `full.yml`'s `matrix` gate aggregate a sharded matrix and were both `if: ${{ always() }}` — which runs a job *even when its run was cancelled*, at which point `needs.<shard>.result` is `cancelled` and the gate's `!= success` test turned that into `exit 1`. The concurrency group deliberately collapses a branch's `pull_request` and `push` runs, so one of the two is always cancelled, so **every PR carried a red `test` that no test produced**: measured on `9801cf068`, the cancelled run reported all twelve of its other jobs `cancelled` and `test` → `failure` while the surviving run reported `test` → `success`, and check-runs key on the SHA so both sat on that one commit with `mergeStateStatus` permanently `UNSTABLE`. Both gates are now `!cancelled()`, so a cancelled run reports **no** verdict — while a *failing* shard still reds them, because `fail-fast: false` means a shard failure never cancels the run (`success()` would have hidden real failures behind a "skipped"). A leg cancelled on a live run is its own branch and says **unproven**, not "shards failed". **(2)** `full.yml` is `main`'s verification and its group was `cancel-in-progress: true`, so every merge erased the answer the previous one was computing: measured over the last 400 main runs, **324 cancelled · 46 failure · 29 success**, with **zero** successes in the last 40 and the last completed run at 2026-09-17T15:40Z — a gap nothing could see, because a cancelled run carries no conclusion a reader treats as red. The group now **queues** (`cancel-in-progress: false`): an in-flight verification always finishes, and GitHub supersedes the older *pending* run, so the cost stays at roughly one in flight plus one waiting rather than fanning a 24-leg matrix out per merge. What "`main` is verified" now means, precisely: **main's HEAD converges on a run that completed**, and no running verification is ever killed — not that every intermediate commit gets its own run. `ci.yml` still cancels in progress; that dedupe is what stopped every PR running the whole matrix twice, and the rail asserts it survives ([#2946](https://github.com/PersonalClaw/PersonalClaw/issues/2946)).
- **The wheel gate can no longer pass on a wheel it did not exercise.** `scripts/verify_wheel.py` printed `PASS: wheel contract met` while the gateway it had just booted logged two ERROR tracebacks — `Failed to enable extension run-workflow-action` and a missing `create_schedule_provider` factory — because the script captured that output and asserted nothing about it. The wheel carried three `apps/native/` directories that existed **neither in git nor on disk**: `python -m build` does not clear `build/`, and setuptools re-used the stale staging tree, so anything ever deleted from `src/personalclaw/**` could reappear in a wheel built by the documented release command (inert here; a deleted module that still *imports* would run). `--build` now clears `build/` and `dist/` first — the safety lives in the command, since `DIST-3` names the bare `python -m build` as *the* release command — and a **sixth** contract assertion fails the gate when any bundled extension reports a load failure, reading the full transcript from before the READY line through shutdown. The marker strings are pinned by test to the `providers/registry.py` lines that emit them, so rewording a log message reds the rail instead of silently disarming the gate, and the detector is driven from both sides plus an AST check that `_boot_and_probe` actually calls it ([#2758](https://github.com/PersonalClaw/PersonalClaw/issues/2758)).
- **The bound-model-deletion rail's temp exemption no longer allows a delete rooted at the SHARED temp base.** `tests/real_model_root_guard.py` exempted `candidate == tmp_root or tmp_root in candidate.parents`, and that first arm made `tempfile.gettempdir()` — the one directory every xdist worker's `tmp_path` hangs off — its own permitted delete root, which is exactly the cross-test delete the rail exists to stop. A guard satisfiable by the shared base is not a guard. The exemption is now strictly-inside only; per-test roots under the base stay allowed (that carve-out exists because a `TMPDIR` under `$HOME` otherwise reds 55 cells), and named model roots were and remain refused at any depth, unconditionally, before the carve-out is consulted ([#2999](https://github.com/PersonalClaw/PersonalClaw/issues/2999)).
- **A read-only session GET no longer WRITES a session workspace for any id the caller invents.** `session_workspace.workspace_dir()` created the directory it resolved — correct for the write paths, and the docstring said so — but read and probe paths called it too, so a lookup of something that does not exist was a `mkdir`. Three routes did it: `GET /api/chat/sessions/{s}/tool-result/{rid}` and `GET /api/sessions/{id}/agents/{agent_id}` answered `404` while creating, and `GET …/agents/{agent_id}/stream` answered `200` while creating. The `tool-result` one was the worst: its nested `tool_results/` made the ghost dir **non-empty**, and the only reaper wired into the gateway (`session_pid.cleanup_orphaned_sessions`) removes empty dirs only, so 26 sequential `404` reads left 26 directories that survived every restart. The resolver is now split — `workspace_path()` resolves, `workspace_dir()` resolves *and* creates — with the same split in the raw tool-result store (`_store_path` / `_store_dir`); the same root cause also made `purge_session()` report it had deleted a session that never existed, because the resolve created the dir the `is_dir()` test then found. Id validation is unchanged (traversal was never reachable). The rail is a **parity** one, not per-route: a static call-graph walk fails if any function classified read-only reaches a creating resolver, and a coverage guard fails if a new public function in either module is left unclassified ([#2993](https://github.com/PersonalClaw/PersonalClaw/issues/2993)).
- **A brand-new conversation's title, pin, colour, folder, tags and `never_archive` now survive a restart instead of being accepted `200 {"ok": true}` and lost.** Two message-count guards swallowed *metadata* writes that have nothing to do with messages: the 5s flush loop skipped any session with no messages, and `save_session_to_history` returned early on `not msgs` **before** it reached the metadata line — so `/pin` and `/folder`, which pass `force=True` precisely to demand an immediate write, got nothing (`force` only bypasses the shorter-buffer overwrite guard *below* that return). `never_archive` was the sharpest case: the one flag whose entire job is to stop the auto-archiver could not protect a conversation with no turns yet. `title` had a **third** source that a fix aimed only at those two guards would have missed — `ConversationLog.set_title` merges into an existing metadata line and returns silently when there is no file to merge into, and the rename handler never marked the session dirty either. A transcript-less conversation now persists its metadata line alone, gated twice: it will not write when the **disk** holds turns the buffer does not (so a forced save can never trade a real transcript for an empty buffer — which also closes the same hole for a buffer holding only `chunk`/`done`/`streaming` rows, which the old `not msgs` waved straight through), and it will not mint a file for a pristine tab whose metadata line carries nothing beyond what create itself set ([#2969](https://github.com/PersonalClaw/PersonalClaw/issues/2969)).
- **An ABSENT field on a session-metadata PATCH is no longer read as a request to CLEAR it.** `PATCH /api/chat/sessions/{s}/color`, `/pin`, `/folder` and `/natural-voice` each read their field with a bare `body.get(...)`, which cannot distinguish "the caller said null/false/empty" from "the caller never mentioned this field" — so an empty body, or a body with a typo'd key (`{"colour": 7}`), returned `200 {"ok": true}` and wiped the value. No shipped frontend caller sends an empty body, but all four are agent- and app-callable, so the failure mode was a silent destructive write reported as success. All four now refuse with a `400` naming the required field and how to clear it explicitly, which is the line `/title`, `/lifecycle` and `/tags` already drew — and `/natural-voice` is included because the comment the house rule is quoted from lives in that very handler, which did not apply it to its own missing-field case. An **explicit** clear (`{"color_index": null}`, `{"pinned": false}`, `{"folder_id": ""}`, `{"natural_voice": ""}`) still works ([#2970](https://github.com/PersonalClaw/PersonalClaw/issues/2970)).
- **`{"confirm": "false"}` no longer reads as a YES on a destructive door — one strict predicate replaced two incompatible ones across 24 gates (issue 3000).** `confirm` shipped implemented two ways: nine body-reading doors compared it to the literal `true`, nine used plain Python truthiness. `bool("false")` is `True`, so a body that literally said do-not-confirm read as confirmed and `POST /api/knowledge/items/{id}/merge` **deleted the merged-away item at HTTP 200**. Every truthy JSON value was a yes (`"false"`, `"0"`, `"no"`, `{"nested": 1}`, `["x"]`); only `0`/`[]`/`{}` refused — and `"false"` is the value clients actually send, because a bool that has been through a template, a query string or a model's JSON emitter arrives as a string. The same shape sat on the tag merge (its own comment calls it "strictly more destructive than `delete_tag`"), the restructure apply, the durability history revert, the task-list reset and the lexicon wipe. `personalclaw.safety_flags.confirm_granted` is now the only reader: consent is the JSON literal `true` and nothing else, which is fail-closed on all 24 doors. `tests/test_confirm_gate_parity.py` is a **ban on the pattern rather than a checklist** — no module outside `safety_flags` may read a confirm field at all — because a per-route rail is exactly what left issue 2983 open after eight of nine doors were fixed. Two consequences to know: a client sending the *string* `"true"` is now refused (with a message naming the literal), and `POST /api/durability/import?confirm=1` / `?confirm=yes` are refused — `?confirm=true`, the only spelling the docs ever promised, is unaffected.
- **`security.egress` no longer accepts a host entry the matcher can never match, so a denylist that reads as blocking a domain family can no longer block nothing (issue 2956).** `*`, `*.example.com`, `""` and `"."` were all accepted with 200 OK, persisted to `config.json`, and echoed back by `GET /api/security/egress` as configured policy — while `net.guard.host_matches` implements exactly one rule ("a bare domain covers its subdomains") and no glob. So `deny_hosts: ["*.example.com"]` blocked **nothing** (the bare form blocks both the apex and its subdomains), and `allow_hosts: ["*"]` on an exclusive tier refused everything while the refusal counted the dead entry as `1 host(s) allowed`. Refused at the write boundary rather than taught to the matcher: a glob would mint a second spelling for a policy that already has one, differing in precisely the apex-domain case a user cares about. Entries are also normalised (lowercased, trailing dot stripped) so the value stored and shown IS the value enforced, and a hand-edited `config.json` that bypasses the write boundary now logs a WARNING naming the inert entry instead of skipping it in silence.
- **`GET /api/security/audit?token=…` no longer 400s as an unknown filter, so a query-token client can read the audit trail at all (issue 2927).** `?token=` is the gateway's query-token credential — read by the auth middleware and deliberately not stripped from `request.query` — and this route was the only one that diffed the *entire* query string against its filter allowlist. The result was a catch-22: without the token auth answered 403, with it the handler answered 400 `unknown_filter`, including on a deep-link to the audit URL with the startup token before the cookie is set. Reserved auth params are now subtracted before the diff (`token_auth.RESERVED_QUERY_PARAMS`) rather than added to the filter set, so the credential still never reaches a SEL query as a filter, and a genuinely typo'd filter is still refused.
- **`doctor` now names the one bypass-behind-a-proxy combination that silently hands the internet a token-free dashboard (RUA-5).** `PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1` skips the token for any request whose *resolved* client address is private. On a home LAN that is what it sounds like. Behind a reverse proxy it is not: the address the middleware resolves is the **proxy's own** — `127.0.0.1` for a local tunnel daemon, `172.18.x.x` on a compose bridge, both private — so every request the proxy forwards, from anywhere on the internet, is admitted with no token. Measured against `/api/config` on a `127.0.0.1` peer: **token-free `200` in 3 of the 4 probe shapes**, the only refusal being the shape whose proxy sets `X-Real-IP` (the one header the code reads — RUA-6, above). The `remote.reachability` row (Settings → Doctor, `GET /api/doctor`) now **fails** when the bypass is armed on an instance that also declares `trusted_proxies` or a `public_url`, naming both so the variable you exported can be connected to the config you wrote. `docs/reference/configuration.md` also stopped asserting the opposite of the measured behaviour — it said *"public origins still need a token"*, which is false for exactly the dominant deployment shape. **It is a diagnostic and nothing else: no admission decision changes.** All four shapes still return exactly the status codes they returned before (three of them the `200` this row now warns about), and the row is unchanged — `ok`, `detail` and `evidence` — whenever the variable is unset or no proxy is declared; both directions are asserted, as is the unchanged `403` for all four shapes with the bypass off. Whether that `200` should become a `403` is a separate owner decision; this makes it visible in the meantime. The probe reaches the fact through `origin.local_network_bypass_enabled()` — the module that already mirrors the middleware's short-circuits — rather than re-spelling the env var, so the diagnostic cannot drift from the behaviour it reports on, and a rail asserts that. RUA-6's own rail was also hardened after two measured blind spots: it could not see a header read written as `headers["…"]` (adding a live `X-Forwarded-For` subscript read left it fully green), and its "named-as-ignored" escape hatch let RUA-6's *original* defective table row pass unchanged.
- **The forwarded-header contract is true: `trusted_proxies` and `docs/guides/remote-access.md` now name the header the code actually reads (RUA-6).** Both promised `X-Forwarded-Proto` / `X-Forwarded-For` were "honored only from a configured trusted proxy"; nothing in `src/` read either one. The single forwarded header honoured is `X-Real-IP`. An operator who followed the guide configured their proxy to send headers the gateway ignores, and every request kept binding to the tunnel's address. The promises were corrected rather than the code widened — two headers carrying the client address needs a precedence rule between a trusted and a forgeable source, `X-Forwarded-For` is a list needing a hop-count policy nothing here wants, and `X-Forwarded-Proto` has no consumer because the `Secure` cookie and `wss://` CSP come from `dashboard.public_url`. `tests/test_forwarded_header_docs_match_code.py` reds on whichever side drifts next.
- **An app that declares `permissions.api: ["/api/ws"]` no longer gets an owner shell with it (#2964).** `permissions.api` is a path-PREFIX allowlist, and `/api/ws` is a prefix of `/api/ws/terminal/{session_id}` — the built-in CLI panel's PTY — so declaring the event socket also granted an interactive shell running as you, in your `$HOME`, with your real `~/.personalclaw` credential store readable from it. `api_terminal_ws` authorized on `request.get("user")` plus the feature flag and never consulted `request["app"]`, both of which an app-scoped request satisfies. The shipped first-party `menu-bar-companion` declares exactly that scope, and install consent showed you the string `/api/ws` with nothing on the screen saying "and a terminal" — so this was a **consent-integrity** defect, not merely an over-broad grant. A prefix says nothing about power, so no adjustment to the matching grammar fixes it: `apps/permissions.OWNER_ONLY_API_PATHS` is now a closed registry of the capabilities no app declaration reaches, **including `"*"`** — the terminal and its session routes, computer-use, the credential store and secrets vault, the security audit log and SEL rotation, your login password and second factor, gateway restart, and local token minting. Matching is segment-aware in both directions (`/api/auth` covers `/api/auth/password` and does **not** cover `/api/auth-status`), the refusal names the capability rather than "not in declared permissions" so a developer can tell policy from bug, and a manifest that literally names one of these paths now **fails to install** with the sanctioned alternative in the message (ship a backend, or declare `permissions.desktop`). Your own access to every one of these surfaces is unchanged — the refusal is scoped to requests carrying an app identity. A property rail asserts it over every `/api` route the source registers, so a new child of an already-classified root is covered the day it is written.
- **A 56 KB `.docx` no longer costs 153 seconds of gateway CPU, and the office parsers finally inherit the zip-bomb posture the rest of the codebase has shipped for years (#2747).** `documents/`'s `.docx`/`.xlsx`/`.pptx` parsers handed raw bytes to python-docx / openpyxl / python-pptx, each of which opens the archive itself — so `doc_parser.py`'s `_MAX_ZIP_ENTRY`/`_MAX_DECOMPRESS`/actual-decompressed-size checks reached none of them, and the only bound in the path (`MAX_BINARY_CONTENT_BYTES`, 16 MiB) bounds the **compressed** size. A valid 56,457-byte `.docx` holding 200,000 one-character paragraphs parsed in 153 s; at that ratio a 16 MiB upload is roughly 12 hours and 23 GB. `documents/limits.py` now holds both caps in one place. **Archive caps refuse**, before a byte reaches a document library: the *actual* decompressed size of each XML part (50 MB) and of all of them together (100 MB), plus a part-count ceiling — answered on the wire as a new `document_too_large` 413 whose message points at `GET /api/artifacts/{slug}/raw`, which still serves the whole file, because a refusal you cannot get past is worse than the problem. **Structural caps truncate and say so**: 20,000 blocks, 500,000 cells, 1,000 slides each append a `size_limit` loss through the report the editor already shows, so an enormous real document opens and tells you what fit instead of claiming to be broken. The caps are calibrated against real Word- and Excel-authored documents *and* against ordinary large ones, which overturned the obvious choice — a normal 2,000 × 100 export carries 10.9 MB of XML in a single part, so a cap sized from the largest real file would have refused it.
- **Swapping to a different embedding model of the SAME dimension no longer leaves semantic search silently scoring the old model's vectors, and the re-index can no longer report success over a half-converted library (RET-4).** Two guards existed and both were dimension tests: the chunk ANN index partitions per dimension with the dimension in the table *name* (`chunk_vec_384`), and the retriever skips any stored vector whose length differs from the live query vector. `all-MiniLM-L6-v2`, `bge-small-en` and `gte-small` are all 384-dimensional, so switching between them produced vectors that were exactly as long as every guard wanted and from a different vector space — cosine returned a number that meant nothing, and nothing in the product could tell. Worse, the re-index could not have fixed it if it had noticed: `clear_embeddings` + `reembed_all` rewrite the *item* vectors and never touch `chunks`, and the chunk backfill only visits items with **no** chunk rows, so every passage vector survived a model switch untouched, permanently. Each chunk row now records the `(embedding_model_id, provider)` that embedded it; the retrieval arms score only vectors the *active* model produced; `knowledge_search` answers with a typed **`stale_index`** reason (RET-2's vocabulary, extended) naming which documents are affected instead of a bare empty result set; `doctor`'s `knowledge.searchability` row lists them with the right remedy (re-index, not re-ingest); and the embedding re-index re-embeds the passage layer in place, reports the **integer count of chunk rows it changed**, and **refuses to report `done`** while any chunk still carries a previous model's fingerprint. Chunk ids survive the re-index, so citations are not orphaned. A database written before this carries NULL fingerprints, which read as stale rather than being back-stamped with a model that may not have produced them — so an upgraded library reports a re-index is due, which is the true statement.
- **`deploy/compose/compose.yaml` is finally true to its own header, and the guide no longer describes the failure backwards (DIST-16).** The header advertised "run a release without checking out the source tree", and then two of the three services declared a bare `env_file: - ../../.env`. Per the Compose spec a relative `env_file` resolves from the compose **file's** parent directory and `required` defaults to **true**, so a copy of `compose.yaml` on its own did not quietly skip your keys — it **failed to start at all** with `env file … not found`, before pulling an image. `docs/guides/platforms.md` told the reader their "keys silently do not load", sending anyone debugging it after the one thing Compose does not do here. Both services now declare both candidate locations as **optional** long-syntax entries — `./.env` beside the compose file (the standalone layout) and `../../.env` at the repo root (the from-a-checkout layout, last so it wins when both exist) — so a downloaded `compose.yaml` resolves with nothing but itself present, and an existing repo-root `.env` keeps working. A test copies the file alone into a scratch directory and runs `compose config` there, backed by a structural rail that pins the optional-`env_file` property even where no container runtime is installed.
- **An app-scoped WebSocket no longer receives events its manifest never declared.** `broadcast_ws` enforced the untrusted-app sandbox gate (`permissions.events`) correctly, but three producers wrote to sockets without going through it: `subscribe_logs` handed the ring-buffer replay straight to the socket, `_RingLogHandler.emit` walked `state._ws_log_subscribers` and pushed every live log line itself, and the on-connect sessions push plus the subagent subscriber set used a raw fan-out helper. So an app that declared no `log` event still got the owner's entire backend log stream — redacted, but the owner's — and the session list, on a surface built to be sandboxed. The gate now sits under every send path: `_dispatch_ws` is the one writer and it cannot be called without naming an event type, and naming one means passing `_ws_may_receive`, so the raw helper is deleted rather than left beside the gated one. An owner/dashboard connection is unchanged and still receives the full stream. The rail is the deliverable: `tests/test_ws_app_event_gate.py` asserts both directions on the same fixture — an app socket gets neither the session list nor the log stream, an owner socket still gets the sessions, the ring replay and live logs — so a future producer that bypasses the gate reds the suite instead of shipping ([#2963](https://github.com/PersonalClaw/PersonalClaw/issues/2963)).
- Workflow tools now pass through the shared MCP boundary: their 19 argument schemas are actually enforced (they were defined but never consulted), calls are SEL-logged, and a compiled batch leaf can no longer call workflow_start past the orchestration denial. The Security panel's "Tool schemas" stat counts tools with enforced validation (from the dispatch maps) instead of *_SCHEMA module constants.
- **A subprocess whose deadline expires is now killed AND reaped, with its process group, at twelve more spawn sites.** The gateway's own auto-update was the worst of them, and it is the twin of the manual `/api/update` pipeline — the reaping fix had landed only on the twin. Its `pip install -e .` had a deadline and **no `TimeoutError` handler at all**, so a timeout unwound to the function's outer `except Exception` and the child was never signalled: measured against a forking stub with a 1s deadline, **2 live processes** (the child and its forked build backend) still running after the call returned and the event loop closed — and this runs on the auto-update poll, so they accumulate for the life of the gateway. Routing it to the owner was necessary but **not sufficient**, which is the part worth reading: the spawn also lacked `start_new_session=True`, so `cancellation.kill_timed_out` *correctly refused* to signal a process group (the child shared the gateway's, and a `killpg` there signals the gateway itself), fell back to a single-pid kill, and the build backend survived holding the pipe — measured, then fixed by giving the spawn its own session exactly as the twin already did. Also fixed: two workflow sites that killed without reaping, the frontend `npm ci`/`npm run build` and `self_update`'s `git fetch` (which killed the wrapper by pid and left the tree that was actually burning the deadline running), `voice_reply`'s ffmpeg stitch (whose timeout was swallowed by a bare `except Exception`), the container-backend CLI (documented as "bounded" when only the *wait* was bounded), and the desktop computer-use driver, whose refusal message promised "the child was killed rather than left running" while nothing killed it — the one child in the tree that holds the operator's physical input layer. All of them now route through the single owner, `cancellation.kill_timed_out`, which checks process-group leadership before signalling a group and **bounds** the reap: `asyncio`'s `Process.wait()` resolves on inherited-pipe disconnect rather than on reaping, so an unbounded post-kill drain is the child's own duration wearing a timeout's name. Two hand-rolled copies of that group-kill-then-drain pair are deleted (`artifacts/build.py`'s `_kill_tree` and `action_providers/bash_provider.py`'s inline `os.killpg`). **The rail is the deliverable:** a derived AST census keyed per **site** — `file::qualname::variable`, not per file — requires every async spawn's timeout path to reach the owner or be listed with a reason, in both directions (a listed site that starts routing must be delisted), plus a spawn with no timeout handler at all being a recorded decision rather than an omission. No linked issue: follow-up class from [#432](https://github.com/PersonalClaw/PersonalClaw/issues/432).
- **A `PERSONALCLAW_AUTH_MODE` the runtime cannot honor is now NAMED at startup and by `doctor`, instead of being downgraded in silence (SL-8).** `AuthConfig.from_env` recognized exactly one value — `none` — and returned the `local_token` default for everything else, so an operator who set `PERSONALCLAW_AUTH_MODE=oauth2` believed they had enforced IdP SSO and was in fact on a shared bearer token, with nothing logged, nothing printed, and no way to tell from the running gateway. `api_key` behaved the same way, and so did a typo. Both modes are declared in `AuthMode` and their request-side halves are built (`dashboard/token_auth.py` validates a Bearer key, `auth/oidc.py` verifies an OIDC JWT), but no configuration populates `AuthConfig`'s per-mode fields, so `from_env` cannot hand them a usable config — a documented pre-1.0 limitation that the runtime now states out loud rather than leaving to the docs. A new `classify_auth_mode_request()` describes the request without acting on it (what was asked for, which mode is in force, and why the two differ), `from_env` logs its sentence at WARNING, and `personalclaw doctor` prints the same sentence. **The admission decision is unchanged**: an unhonorable value still leaves `local_token` in force, which fails CLOSED — a client presenting the credential the operator configured is refused, not admitted — so the cost of the old behavior was lost access plus a false belief about the posture, never weakened auth. Because of that, the doctor line is a ⚠️ and not an `issues` entry: `doctor`'s exit status means what it meant before. The selectable set lives in one mapping (`SELECTABLE_MODES`) that the classifier reads rather than re-deciding, with a test rail that reds if a mode is added to `AuthMode` without landing in either that mapping or `UNSELECTABLE_MODES`, so a fifth mode cannot reintroduce the silence. The echoed value is length-capped — an auth mode is a short keyword, never a credential, but a pasted blob should not become a multi-kilobyte log line.
- **The task API's doors now validate what they accept, so a wrong-but-plausible request is refused instead of answered with a quietly wrong result.** Four filed defects, one cause: the task read/write routes skipped guards their own siblings already enforce, so each answered `2xx` describing a scope it had not applied. **(1)** An unknown `?provider=` meant "every provider" on all seven read/write doors, so `DELETE /api/tasks/{id}?provider=jira` deleted the **native** task and answered `{"ok": true}`, and `PUT` with the same name renamed it — while `POST /api/tasks` already refused it, i.e. the create door validated and the other seven did not. The registry conflated *no provider given* (legitimately "search them all") with *a name I do not recognize* (only ever a client error); those are now one resolver, so all eight doors answer `400` naming the provider, exactly as the artifacts registry — same pluggable-provider design — already did. `?provider=` empty still means "no scope", and `/api/tasks/graph`'s documented native fallback is deliberately untouched. **(2)** `limit`/`offset` went straight into a Python slice with no lower clamp, so `?limit=-1` returned a silently **short** page (5 of 6) beside a `total` that still said 6, and `?offset=-2` returned an empty one; both are now clamped with the same `max(1, min(...))` idiom every sibling paginated route uses, and the response reports the window it actually applied. The agent's `task_list` tool advertised an unconstrained `integer` and turned an explicit `limit: 0` into 25 — so `limit=0` meant "none" over HTTP and "all" to a model; it now shares one page ceiling with the route and advertises `minimum`/`maximum` in its schema. **(3)** Neither parent id on the write path was validated: `POST/PUT /api/tasks` accepted a nonexistent `task_list_id` at 201/200 and **stored** it, and swallowed a bad `project_id` outright (`except ValueError: return` discarded the store's own rejection), so a task was born into the orphan state — while the sibling `POST /api/task-lists` refused the identical id. Both parents are now resolved before the write on create, update **and** bulk (in bulk's validate-all phase, so one dangling parent aborts the batch rather than landing an orphan beside sound rows). **(4)** `DELETE /api/task-lists/{id}` was a bare `delete_task_list()` with no task cascade, so its tasks survived pointing at a deleted list with their `project` label blanked — and then outlived the project delete too, because that cascade resolves doomed tasks by project *name*, which the list delete had already blanked. It now cascades by `task_list_id` (the live FK) and reports `deleted_tasks`, sharing one delete loop with the project cascade rather than being a second mechanism that happens to agree ([#2983](https://github.com/PersonalClaw/PersonalClaw/issues/2983), [#2984](https://github.com/PersonalClaw/PersonalClaw/issues/2984), [#2977](https://github.com/PersonalClaw/PersonalClaw/issues/2977), [#2976](https://github.com/PersonalClaw/PersonalClaw/issues/2976)).
- Discover's engagement probes now measure the user, not the system: four of the ten auto-hide checks counted machine-produced signals, so tips vanished before they could teach — the automation tip was unreachable on every install (boot registers the notification-digest trigger before the first interaction), the skills tip hid after one plain chat message (passive turn-time injection of bundled skills), the inbox tip hid on the first system-generated proposal (presence, not interaction, on a surface built to receive system items), and the memory tip hid on auto-consolidation rows a user never reviewed. Each check now excludes the baseline the way `_engaged_apps`/`_engaged_projects` always did: automation skips `created_by="system"` triggers, skills requires a skill beyond the bundled set, inbox requires a user gesture (an item moved off pending — excluding the system-written FILTERED state — or a favorite), and memory counts the editor's `user_explicit` rows via a new `user_curated` stat ([#458](https://github.com/PersonalClaw/PersonalClaw/issues/458), [#670](https://github.com/PersonalClaw/PersonalClaw/issues/670)).
- **Content-hashed `/assets/*` Vite bundles no longer defeat the browser cache.** `no_cache_middleware` set `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` (plus `Pragma`/`Expires`) on every response via `resp.headers.setdefault(...)`, including the ~22 MB of hashed JS/CSS chunks Vite ships under `/assets/*-<hash>.js|css` — URLs that change whenever their content does, so a full re-download on every page load and every in-app navigation bought nothing. The middleware now recognizes the literal `/assets/` prefix and answers `Cache-Control: public, max-age=31536000, immutable` there instead — safe because the route is unauthenticated (`token_auth._BYPASS_PREFIXES`) and carries no per-user data. Every other response the middleware touches keeps the original no-store policy exactly: the HTML entry point, API routes, and the OTHER UI-transport static mounts that also skip auth but are unhashed/stable-named (`/fonts`, `/sprites`, `/vendor`) — long-lived-caching those would serve stale content past a rebuild, so only the genuinely content-addressed prefix is exempted ([#2933](https://github.com/PersonalClaw/PersonalClaw/issues/2933)).
- **An ingest that leaves nothing you can find no longer reports success.** Two documents measurably persisted `processing_status: "done"` with no error while being unretrievable: an **image-only PDF** (a scan), where `document_read` reported `done` and `pdfplumber` extracted `''` from every page — the item kept only a synthesized descriptor (`"Document: scan.pdf (pdf, 1 pages, 3 KB)"`), i.e. **none of the document's words** — and **any document ingested with no embedding provider bound**, which wrote **zero** vectors and **zero** chunks. In both cases the library said the item was there and no query could reach it, which is the same failure users left a competitor over (AnythingLLM #6143: *"the embedding step silently writes nothing… RAG retrieval returns no sources, while the app reports success"*). Such an item now persists the **named** status **`unsearchable`** — never `done` — plus one of three typed reasons (`no_extractable_text` / `no_embedding_provider` / `not_indexed`) and a plain-language sentence saying what to do about it. The verdict is computed from what actually **landed** (rows in `chunks`, the item's own vector, its stored text), not from any stage's self-report, because the self-reports were exactly what was untrustworthy. Two surfaces read that one recorded fact, so they cannot disagree: a new Doctor row (**`knowledge.searchability`**) lists **one row per affected item**, naming which document is unreachable and how to fix it, and **`knowledge_search`** now answers with the typed reason instead of a bare `(no matching knowledge items)` — a claim about your library's *contents* that an unretrievable item makes false. Re-ingesting after binding an embedder (or adding a text version of a scan) clears the status and the row. Deliberately unchanged: an image uploaded with no OCR/vision model is **not** flagged — its extractors were *skipped*, which the product already reports as `partial`, and only a step that claimed success while writing nothing is the failure this targets (RET-2).
- **`personalclaw --help` no longer prints argparse's internal `==SUPPRESS==` sentinel, and the internal command it was meant to hide is now genuinely hidden.** `mcp-core` — the stdio MCP server an ACP CLI spawns, never a command a human types — was registered with `help=argparse.SUPPRESS`, and the comment beside it said "not user-facing". argparse honours `SUPPRESS` for ordinary *arguments* only: for a subparser **choice** it stores the sentinel as that choice's help text and renders it verbatim, so the very first surface a CLI user reads showed `mcp-core            ==SUPPRESS==` **and** still listed `mcp-core` in the `{chat,run,…}` metavar on both the usage line and the positional-args line — the exact opposite of the stated intent, twice over. Hiding a subcommand genuinely takes both halves and neither is `SUPPRESS`: omit `help=` entirely (argparse then builds no help row at all) *and* pin the choices metavar to the visible names, recomputed once over the finished parser tree rather than at registration time (`mcp-core` sits mid-list, so pinning it early would have silently dropped every command added after it). Both now live behind one door — `cli.HIDDEN_COMMANDS` plus `cli._add_hidden_parser`, which refuses a name that is not declared hidden, so the next internal command cannot land as *silently undocumented* instead. `mcp-core` remains fully dispatchable; it is simply not advertised. **The rail is the deliverable, not the row:** `tests/test_cli_help_surface.py` walks the whole parser tree (one top-level parser + ~106 subparsers), renders every one, and holds each subcommand to a binary standard — documented, or declared hidden and then absent from every rendered surface — with no argparse sentinel anywhere. Its sentinel detector is calibrated in both directions (it must fire against a parser deliberately built the broken way, and must not against the shipped one), so a future `help=SUPPRESS` on a subparser fails the gate rather than shipping. The parser construction moved out of `main()` into `cli.build_parser()` to make that walk possible without `main`'s `.env` loading and `PERSONALCLAW_HOME` touch. Also corrected the doc row that already claimed this was true: `docs/reference/cli.md` still advertised `personalclaw mcp-schedule`, retired with the `schedule_*` tool aliases in [#328](https://github.com/PersonalClaw/PersonalClaw/pull/328) ([#2904](https://github.com/PersonalClaw/PersonalClaw/issues/2904)).
- **Accepting a learning proposal installs it, or says it cannot.** `POST /api/learning/proposals/{id}/accept` took its installer as an *optional* injected argument and skipped the install entirely when nobody passed one — so an accept could answer `200 {"ok": true, "status": "accepted"}`, write nothing, and still record the decision, after which the queue's own anti-nag memory returned "already accepted" for a change that had never been applied and no proposer could ever raise it again. Measured against an isolated home: four kinds with live producers did exactly that — `retirement` (the skill it proposed retiring was still on disk afterwards), `tier_migration`, `template`, and `knowledge_draft` (zero rows written to the knowledge store) — while `skill` installed correctly, so the page's "nothing is ever installed without your accept" was true for one kind and inverted for four. The queue now **resolves** its installer through one owner (`personalclaw.learning.installers`) rather than asking callers to remember an argument, which also collapses a dispatch that had been spelled twice and had already diverged (the Learning surface installed a self-model principle; the inbox's Approve on the same row did not). A kind nothing can install yet is a **409 naming the kind**, records no decision, and stays in the queue — retryable the moment an installer lands — and a self-model principle that could not be written for want of a reachable memory store is refused for the same reason rather than quietly banked. Injection survives only as an explicit override, for the one caller that genuinely owns its own write ([#677](https://github.com/PersonalClaw/PersonalClaw/issues/677)).
- **Creating a loop on a fresh install with no model bound now says so, instead of silently building a bare-defaults plan.** `POST /api/loops/classify` — the kind-aware intake analyzer behind the composer and Plan Review — is a model call, and with nothing bound it answered `200 {"classified": false}`: byte-for-byte indistinguishable from "the model returned garbage", so a first-run user got the generic "couldn't auto-analyze" fallback and a loop built from safe defaults rather than "you have no model connected yet." It now preflights the same no-instantiate probe behind onboarding's `needs_model` (`can_resolve_use_case`) and answers the calm `409 model_unresolved` envelope — the wire peer of the chat path's `ERR_MODEL_UNRESOLVED`, its message carrying the "no model provider resolves for use case" phrasing the dashboard's no-model matcher keys on — so the composer surfaces "Could not analyze the task — is a model configured?" and the door to setup is one hop away. A bound instance is unchanged: `can_resolve_use_case` is true whenever any usable model resolves (background falls back to the chat chain), so the preflight fires only on a genuinely unbound home. Landed as an **invariant, not a one-off**: `tests/test_no_provider_first_run_rail.py` is a *growing enumeration* of the model-dependent first-run surfaces — `chat`, `suggestions`, `knowledge` ingest, `generate-intelligence` and `loops/classify` — each asserted on a no-provider home to yield the calm no-model signal (an envelope/turn-error matching the no-model matcher, or a declared degraded/empty state) and never a raw 500/traceback or a silent success; a deliberately-added surface that answers a bare 500 fails the rail by construction, so the next first-run route that leaks a crash or a fake success is caught rather than shipped. The knowledge surfaces were found already-legible (they ingest to `processing_status='partial'` naming the model as unavailable — an earlier runner fix), so they ride the rail as regression guards; classify was the one surface still failing open. **Class B under the pre-1.0 banner** (ONBOARDING-UX OU-12).
- **The unattended auto-update no longer silently discards a user's uncommitted tracked-file edits.** `_auto_apply_update` — the scheduled, no-one-watching path taken when `auto_update` is on — ran `git fetch` + `git reset --hard origin/main`, and its one nod to safety was a `logger.warning("… discarding local tracked-file changes …")` that it then **ignored and reset anyway**. So an update that fired while you had edits in the working tree threw them away with no prompt and no way back — the worst shape of the hazard, because there is nobody there to read the warning. It now **refuses**: on a dirty tree it logs an actionable line, surfaces a paused state in the Updates panel ("commit or stash your local changes first"), and leaves the tree exactly as it was — the next check applies cleanly once you commit or `git stash`. This unifies the unattended path onto the **same** predicate the interactive `personalclaw update` already used (`self_update.git_tracked_changes`), so "is it safe to hard-reset?" is answered in one place, not two divergent ones. Untracked files (task specs, notes) survive a reset and never block an update, exactly as before. Narrow interim safety fix — it does not change *how* updates are fetched (the release-based-update redesign is a separate, in-research effort) (RUM-4).
- **A native-Windows gateway no longer `ImportError`s at boot on the POSIX-only `resource` module.** The run loop raised its own file-descriptor ceiling through a bare `import resource` (`gateway.py`) — a courtesy for macOS's low default `NOFILE`, but `resource` is POSIX-only, so a native-Windows process died at gateway boot before it could serve, and you could not even reach "run it and see what breaks." The import now lives behind `personalclaw.resource_limits`, guarded exactly the way `_spawn_exec_shim.py` already guards it, and the facility's two consumers route through that one helper: the gateway's `NOFILE` raise (`raise_fd_limit`, a no-op that raises nothing when `resource` is absent) and a new `personalclaw doctor` row (`sandbox.resource_limits`) that reports rlimit availability rather than leaving the platform fact invisible. On a POSIX host the raise is byte-identical to the old inline code — soft cap lifted to `min(hard, 10240)` when below, never above the inherited hard cap. This is a hardening, not the native-Windows port, which stays a documented no-go (WSL2 and Docker Desktop are the supported Windows paths); it removes a boot-time crash with no upside. Its degraded branch is test-locked with `resource` simulated-absent (WIN-1, from the windows-native-audit proposal).
- **The desktop app now tells the gateway it IS the desktop app, so a packaged install stops offering to `pip install -U` its own frozen backend.** `detect_install_kind()` resolves `PERSONALCLAW_INSTALL_KIND` first, then probes `PERSONALCLAW_PROJECT_DIR` for a `.git`, then falls back to `pip` — and the Electron shell set the project dir but never the kind. Inside a packaged app that dir is `…/resources`, which carries no `.git`, so **the gateway in the shipped Linux AppImage/.deb classified itself as a pip install**: Settings → Updates showed "Install type: pip / uv install" and an in-app **Update** button whose apply path runs `<installer> install -U personalclaw==<tag>` against `sys.executable` — the frozen PyInstaller binary, which has no interpreter to upgrade and no site-packages to upgrade into. The gateway is also a child of the shell, so the re-exec that apply ends with is wrong even where the install would work. The shell now declares the kind, and it wins over an inherited value (a stray `PERSONALCLAW_INSTALL_KIND=container` in a shell profile must not make an .app print `docker compose` instructions). **This was a three-consumer value that nothing produced**: `_APPLY_METHOD["desktop"] = "desktop_delegate"`, the panel's desktop branch and `personalclaw update`'s desktop branch all shipped and were all unreachable, while every test of the kind set the env var itself — a suite green over a value no shipped code emitted. Set unconditionally rather than only when packaged: "was this gateway spawned by the desktop shell?" is the question the kind answers, and a panel-triggered `git pull` + re-exec is just as wrong under `npm start` from a checkout, where the shell still holds the process handle and the stdout pipe it reads the READY line from. The spawn env moved out of an object literal inside `startGateway` into `desktop/gatewayEnv.js`, because an inline literal is assertable only by reading `main.js` as text — which is why the omission survived; `desktop/test/gatewayEnv.test.js` now executes the builder and `tests/test_desktop_install_kind.py` pins it to `self_update.INSTALL_KINDS` from the Python side ([#2673](https://github.com/PersonalClaw/PersonalClaw/issues/2673)).
- **The three surfaces that tell a desktop user how to update stopped promising an updater the shell does not ship.** All three said the app "updates itself" — wording written for the electron-updater half of `DC-1`, which is not a dependency in `desktop/package.json` and is not called anywhere in `desktop/`. Nothing in the shell checks for a release, so a user who followed that instruction waited for an offer that never arrives. The panel note, `POST /api/update`'s `detail` and `personalclaw update`'s desktop branch now name the release page, `docs/guides/desktop.md` gains an **Updating** section saying there is no in-app update yet and why (frozen backend, shell-owned lifecycle), and a rail reds **when `electron-updater` does become a dependency** so restoring the self-update wording happens in that same commit rather than being forgotten again ([#2673](https://github.com/PersonalClaw/PersonalClaw/issues/2673)).
- **Every channel's outbound reply now goes to that channel, so a multi-provider install stops losing answers.** `channel_delivery` was ONE handle held in TWO places — `GatewayOrchestrator._channel_delivery` and `DashboardState.channel_delivery` — and every shipped transport writes both at `start_inbound`. Measured by executing the real registration path: three registrations leave `<telegramDelivery>` in both slots, and apps load alphabetically, so a **Discord message was received correctly, the agent ran, and the answer was handed to TelegramDelivery carrying a Discord channel id** — the reply lost for every provider but the alphabetically last one. Because one overwrite hit a slot on two unrelated objects, it took out the gateway path and the dashboard path at once, which is why it read as two mysteries rather than one. The routing key was missing on *both* sides and that is why nothing caught it: the 18-method `ChannelDelivery` protocol has no provider member, and the session→channel link is `(thread_ts, channel_id)` with no provider — yet a channel id means nothing without one, since `deliver_text("C123", …)` is answerable only by the provider that issued `C123`. Meanwhile the **inbound** door already takes the provider explicitly, from the same caller at the same lifecycle point; that asymmetry was the bug. There is now one registry keyed by provider, owned by `channel_delivery` rather than by either object that held a slot, and over it **two named resolvers matching the partition the 25 call sites actually form**: `delivery_for(provider)` for a REPLY, where exactly one provider can deliver and `None` means **do not send** rather than "use whatever is available"; and `owner_reachable()` for a cron result, heartbeat summary, approval prompt or subagent reply, which address the owner via `open_dm` with no origin channel to honour and whose pick is **sorted** so it cannot depend on which app booted first. The origin provider is read where it was already stamped — the one inbound door creates the session with `app=provider`. Both former attributes become property pairs whose setters register, so shipped apps keep working unchanged; `register_channel_delivery` gains the provider argument its inbound twin already takes, optional only so a core upgrade cannot break a not-yet-updated app. Not in scope, deliberately: `/api/channels/reply-targets` still lists one provider's channels while three may be connected — that is picker completeness, not routing. **Class B under the pre-1.0 banner** ([#959](https://github.com/PersonalClaw/PersonalClaw/issues/959)); `personalclaw snapshot` before upgrading, as usual.
- **⚠️ THREE DESTRUCTIVE ROUTES NOW REQUIRE `confirm: true`, AND TWO UI CONTROLS ASK BEFORE THEY DESTROY.** `POST /api/knowledge/tags/{id}/merge`, `POST /api/task-lists/{list_id}/reset` and `POST /api/lexicon/reset` refuse a body without `confirm: true` (`400 confirm_required`); the bundled dashboard sends it and prompts first, so only a non-UI caller sees a change. Each had a SIBLING that already gated, so the bar was stated and these did not meet it: tag merge rewrites every item carrying the source tag and then **deletes that tag from the taxonomy** — strictly more than `delete_tag`, whose prompt spells out its blast radius four lines away — while `merge_items` in the same handler module has always required `confirm: true`. Repeatable-list reset is the **only path that empties `execution_notes`**, the record of what was actually done on each task, behind a bare icon button; its two existing server guards decide whether a reset is *legal*, not whether it was *intended*. Lexicon reset drops every term **and every user-authored correction** — "rebuild repopulates" holds only for terms derived from the graph, so the corrections someone typed one at a time are gone for good. Both new prompts state the measured blast radius (items retagged, tasks reopened, criteria un-completed, notes destroyed) rather than "are you sure", because neither verb reads as destructive and a prompt without the numbers is a speed bump instead of a decision. Separately, **merging a nonexistent tag answered `200 {"ok": true, "moved": 0, "already": 0}`** — byte-identical to a legitimate merge of an empty tag, which `list_tags`' own docstring confirms is a real part of the taxonomy — so "absent" could not be told from "zero"; the store now raises naming which side is missing and the route answers `404 tag_not_found`, with an invalid merge still `400`. A rail asserts that every POST route whose path carries a destructive verb (`merge|reset|purge|wipe|clear|prune|revoke|rotate`) either gates on confirm or is listed with a reason; it found five more routes, each read and classified individually — `devices/{id}/revoke` (security containment: friction *is* the harm), `sel/rotate` (recovery, archives rather than discards), `notifications/clear` (transient view state) and `feedback/producers/clear` (un-suppresses — the inverse of destructive) are exempt with those reasons recorded, and a stale exemption naming a dead route reds. **Class B under the pre-1.0 banner** ([#606](https://github.com/PersonalClaw/PersonalClaw/issues/606), [#604](https://github.com/PersonalClaw/PersonalClaw/issues/604)) — a script POSTing to any of the three needs `confirm: true` added; `personalclaw snapshot` before upgrading, as usual.
- **A chat session's on-disk identity has one owner, so a send can no longer destroy a transcript or resurrect a deleted conversation.** Two writers answered "which stored conversation does this key name?" and "does the persisted side already hold more than my buffer?" from IN-MEMORY facts, where both questions are about the disk. Measured on a live gateway: a send to an archived, non-resident session turned 1258 B / 2 turns / `closed=true` into **686 B / 1 turn / `closed=false`** — a blank buffer written straight over a real transcript, because the save guard asked `_resumed_count > 0 and len(msgs) <= _resumed_count`, a fact about the *buffer*; at `0` it never fires however much the file holds. The guard now asks the disk (same run after the fix: 1741 B / 3 turns / `closed=true`, every original message verbatim). Separately, `DELETE /api/chat/sessions/{key}` is a hard delete precisely so a destroyed conversation cannot come back, and two writers undid that: `POST /api/chat` and `POST /api/chat/sessions/{key}/resume` both answered 200 on a hard-deleted key and re-materialised the conversation carrying only the resurrecting turn — "delete" had degraded to "close", reachable from a stale tab or a retried request. Both now ask one owner, `chat_persistence.session_key_exists()`, and refuse `404 session_not_found`, matching the 47 of 49 sibling `{session}`-addressed routes. Un-archiving is explicit too: every save previously cleared `closed` just by rebuilding the meta line — including the shutdown flush, which un-archived every still-resident archived session with no user in the loop. The site set is **derived, not listed**: an AST taint analysis over every `ConversationLog` method whose first parameter is `key` found 41 keyed call sites with 13 bypasses; there are now 42 with 0, and a rail reds on a new bypass of either shape, with a vacuity floor in both directions. An unreadable-but-present log deliberately reads as "exists" rather than "never persisted", so a send is never refused on a session whose file is right there. **Class B under the pre-1.0 banner** — a hard-deleted key answers 404 where it answered 200, and archived-session write semantics change; `personalclaw snapshot` before upgrading, as usual.
- **A path containing a NUL byte is refused instead of crashing the request — and the credential guard now fails closed on it.** Every `os.path`/`pathlib` call raises `ValueError: embedded null character` on such a path, and `hooks.validate_file_path` had no guard, so `?path=/tmp/a%00b` left an unhandled exception to surface as a raw **500** from every endpoint funnelling through it: file-read, file-write, file-move, create-dir, uploads and prompts. Measured on all three validators by executing them rather than reading them, the third result is why the obvious fix would have been wrong: `is_sensitive_path` does **not** raise — it already catches `(OSError, ValueError)` around `Path.resolve()` and continues with the *unresolved* string, which matches no sensitive prefix, so it answered "not sensitive" for a path it could not read. Simply catching the exception in `validate_file_path` and carrying on would hand that classifier a path it cannot classify and take `False` for an answer, **turning a crash into a possible bypass**. So all three now refuse, each in the way it already documents: `validate_file_path` returns `None` (its docstring promises "the canonical path or None if rejected", and its callers are HTTP handlers), `safe_read_file` raises `PermissionError` (the refusal it documents and its callers already handle), and `is_sensitive_path` returns `True` — failing **closed**, the direction that function's own casefolding comment already argues for, since over-blocking "is the safe direction for a credential guard and the error a user can see and report". A NUL is never part of a legitimate filename — POSIX and Windows both forbid it in a path component — so no legitimate use is broken ([#352](https://github.com/PersonalClaw/PersonalClaw/issues/352)).
- **Five MCP paths ignored `PERSONALCLAW_HOME` and reached into the real home instead — one of them wrote there.** A session running under an isolated home could read the operator's agent config (`_find_server_spec_anywhere`), **DELETE a server from it** (`_remove_from_agent_file`), glob another instance's `session_pid` files (`_current_session_thread_ts`), discover the operator's MCP servers (`_load_agent_config`), and — worst — **register a hook into the operator's real home** (`hook_register`, which also `mkdir`s there), whose gateway would then run it. All five now resolve through the active home. Note the issue's own claim (#287) was already fixed on main and verified by execution: `agent._bundled_hooks` resolves the bundled postToolUse hook's `audit.log` through `config_dir()`, so a run under `PERSONALCLAW_HOME=/tmp/x` writes to `/tmp/x/audit.log`. **The class was not fixed** — and it had been found and patched four separate times in four modules (`agent.py` G31, `handlers/files.py` #294, `_canonical_mcp_json`, `subagent_persistence`), each time leaving a comment instead of a check. One of those comments says it outright: *"the same bug, fixed in one function and left in three siblings."* So the durable part is not the five edits but a **ratchet**: `test_active_home_is_the_only_home` scans `src/`, permits a literal real-home spelling only where the enclosing scope resolves the home first, carries a per-site allowlist (`seed.py`'s `real_home` and `cli.py`'s `main_home` legitimately mean the real home — they exist to protect it), a ceiling on the guarded population, and a vacuity floor, because a ratchet whose scanner silently matches nothing is worse than no ratchet. Measured at 13 live sites: 12 guarded, 0 offenders, 1 allowlisted ([#287](https://github.com/PersonalClaw/PersonalClaw/issues/287)).
- The **Tools page no longer badges an installed community bundle `built-in`** — the same word core's own first-party providers get. The badge was a binary (`platform` when the provider was locked, `built-in` otherwise) and an installed app is exactly the shape that fell through it: native kind, not locked. So the install dialog disclosed "Unsigned — community tier", the user consented to *that*, and the page they later audit from ("what is running here, and where did it come from?") answered in the *reassuring* direction. It has a third state now, derived from where the provider came from rather than from whether it happens to be locked: `platform` for provider-locked core, `built-in` for core, and the bundle's **actual tier** for an installed one — spelled by one shared map (`web/src/lib/trustTier`) that the install dialog's signature row now reads too, because two independent literals is how the two surfaces came to describe the same bytes differently. This needed provenance on the wire, which `GET /api/tools` did not carry: every tool now reports its provider's `tier`, resolved from the tier the install gate **recorded** in `installed.json` (new field — it is the only value that knows a verified maintainer signature raised a community bundle to `official`), falling back to what the app's `origin` earns for a record written before the field existed. An external MCP server reports `""` rather than a tier it never earned, and an unknown provenance reads as `community`, never as first-party ([#2627](https://github.com/PersonalClaw/PersonalClaw/issues/2627)).
- A successful local-source add now **shows up in the Manage Sources panel**. The POST persisted, the API listed the source and the Store grid showed its apps, but the panel still read "No local sources" with the input's placeholder restored — the one surface a user watches to confirm the action reported the opposite of what happened. It was not a missing refetch: the handler already invalidated the catalog, and `invalidateKeys` deliberately keeps the stale value so an open panel does not blank, so the panel kept painting the pre-write list for as long as `GET /api/apps/catalog` took — the most expensive read in the app (registry scans plus a shallow clone per git source, behind a 5-minute TTL), and indefinitely if it failed. Both add endpoints already returned the authoritative post-write list and both handlers were discarding it; the panel now paints from that and the full catalog read still lands last. Same fix for the git-source twin ([#2627](https://github.com/PersonalClaw/PersonalClaw/issues/2627)).
- **`--port` now reaches the tool subprocesses too.** `personalclaw gateway --port N` moved where the gateway *listened* but not what its children believed the server's address *was*: each child resolved that for itself, from `parse_dashboard_url(dashboard.url)` or from the import-time `DASHBOARD_PORT`, and **both fall back to a fixed `10000`** — with `dashboard.url` empty in a default config, that was the default path. This was not a hang but a **cross-instance leak**: something *is* listening on `10000`, and on a host running two instances that is the other one, with its own home, config and state. Measured end to end on two isolated homes — instance B (bound `127.0.0.1:10771`) fired a `run-script` action whose `ctx.notify()` was persisted into instance A's `notifications.jsonl` (bound `127.0.0.1:10772`) while B's own store stayed empty, the child reported `{'ok': True}`, and B's `.local_secret` travelled to A in the request header. Resolution now has **one owner** (`personalclaw.gateway_base`) fed by the socket the gateway actually bound and published, and an unresolvable base is a **loud, fast refusal naming its cause** rather than a guess at the default port — a read there is a cross-instance information leak and a write corrupts another instance's state. Every child path is routed through it (the `personalclaw-core` MCP server, the MCP tool-policy read, the ACP child env, the sandboxed cron launcher) and the gateway-liveness probe that gates a destructive restore now follows the bound socket instead of the configured port, which was wrong in both directions. A lint rail reds when a new site learns to resolve the base on its own ([#2539](https://github.com/PersonalClaw/PersonalClaw/issues/2539)).
- Every listing in the community app registry now carries a real **scan verdict** before you install it. All four shipped listings (`channel-null`, `inbox-github-notifications`, `watched-source-github`, `action-home-assistant`) had an absent `last_scan_verdict`, so the pre-install trust surface rendered four "No scan on record" cards and had never once been exercised against a verdict — the stamper existed and worked, and nothing called it, because the three workflows that would live under `scratch/registry/.github/workflows/` and GitHub runs workflows only from the repo root. All four now measure `clean`. `full.yml` gains a `registry-verdicts` job that re-validates every listing against a copy and reds when a committed verdict is absent or stale, and the offline floor in `tests/test_registry_validation.py` reds on a PR the moment a listing loses its stamp. The floor is two-directional on purpose: an *absent* verdict renders identically to an unscanned listing, so a silently dropped stamp would otherwise have been invisible ([#2555](https://github.com/PersonalClaw/PersonalClaw/issues/2555)).
- The files surface now derives its credential blocklist from one declaration instead of a hand-copied list, and the PersonalClaw home's `auth/` directory is refused along with it. `auth/credentials.json` (the argon2id password hash), `auth/enroll_codes.json` and `auth/pair_codes.json` (live redeemable device codes) were readable through `GET /api/file-read`, which is the same omission `session_key` was: the names were known to be secret in the auth layer and unknown to the guard, because the guard kept its own copy of them. `handlers/files.py` now reads `security.HOME_SECRET_FILE_BASENAMES`, so the next secret added to that declaration is refused on every path-taking route without anyone having to remember. The new rail test asks the auth modules where they keep their files rather than restating the names, so an unprotected auth file fails the build ([#354](https://github.com/PersonalClaw/PersonalClaw/issues/354)).
- A provider's **sensitive settings are write-only again on the route the dashboard actually uses**. `GET /api/providers/{name}/config` returned every `x-meta.sensitive` field verbatim, and `PATCH` echoed the freshly-saved value back — while `/api/apps/{name}/config`, reading the *same* file and honouring the *same* flag, masked both. Settings → Providers calls the providers route, so a configured channel app's credentials (slack-channel's Bot + App tokens, discord/telegram's bot token, any third-party app's API key) travelled to the browser on every panel open, sat in the form's state, and were revealable on screen through the field's show/hide toggle. Both routes now share one policy in `personalclaw.apps.secret_fields`: a set secret is replaced by the mask sentinel and reported in `_secret_set`, and a PATCH carrying the mask back (or an empty value over a stored one) keeps what is stored, so saving an unrelated field on the same form cannot erase a working credential. The providers responses gain `_secret_set` alongside `config`; nothing on disk changes, and a real new value still overwrites. Found while reproducing the Slack dashboard-config issues ([#952](https://github.com/PersonalClaw/PersonalClaw/issues/952), [#953](https://github.com/PersonalClaw/PersonalClaw/issues/953)) against a live gateway — the Slack-side halves of those two ship in PersonalClawApps.
- A keep-data uninstall (`Uninstall`, the middle removal rung) no longer destroys or silently overwrites an earlier copy of the app's `data/` that is still on disk. `apps/.{name}.data` (the parked copy) and `apps/.quarantine/{name}.data.staged` (the stage) are both copies of the user's data carrying no label, and four sites decided which was authoritative from the order functions happened to be called in rather than by looking — **three of which returned `True` and audited `ok` while losing data**: a second keep-data uninstall `rmtree`'d the stage a *failed* park had left as the last copy (35 files in, 0 out); `force_uninstall`, which this rung calls to do its removal, discarded an unconsumed park before the new one was made; the park's own `rmtree(target, ignore_errors=True)` swallowed a real permissions failure, after which `shutil.move` found a directory at the destination and moved the stage **inside** it, so the next install restored the stale copy with the user's current work buried in a nested subdirectory; and a `shutil.move` that fell back to `copytree` (which it does on **any** `os.rename` failure, not only across devices) left a partial copy at the parked path that the next install restored as if it were whole (34 of 35 files). One predicate now owns "is there an earlier unconsumed copy?" and the rung **refuses** before copying or removing anything, with a new `data=unconsumed_copy <paths>` fact, a `logger.error`, and the paths on the removal-confirm dialog so the refusal arrives before the click rather than as the endpoint's misleading `404 app not installed`. Nothing is deleted or moved aside on the user's behalf — the "cleanup" for each of these is a delete in a failure path, which is how the original defect happened. Because the destination is then provably absent, the park is a single same-filesystem `Path.rename`: **atomic**, so a partial parked copy is now unreachable rather than detected, and the `rmtree` of the destination is gone. `force_uninstall` still wipes everything including the parked copy, unchanged ([#2585](https://github.com/PersonalClaw/PersonalClaw/issues/2585), following [#2574](https://github.com/PersonalClaw/PersonalClaw/issues/2574)).
- The ledger's run totals now say whether a dollar figure was actually measured. `ledger.reader.run_totals` seeded its accumulators at zero and summed `cost_usd` with a `0.0` default, but the primitive has **two** producers and only one books a cost: a workflow `step_completed` always carries `cost_usd` (a `0.0` there is a real observation — a free local model), while a loop `step_completed` carries no cost key at all, because loop money lives in `usage/turns.jsonl` where `loop.manager.loop_spend` reads it, deliberately, rather than being booked twice. So every loop that had ever run reported `cost_usd: 0.0` — byte-identical to a genuinely-free run, with no key distinguishing them. `run_totals`, `RunStats` and `TemplateCard` now carry **`priced`** beside the dollar figure, reusing `loop_spend`'s and the usage ledger's existing word for exactly this fact rather than minting a synonym: `priced: false` means the number is a FLOOR, and one unpriced constituent taints the total the same way it taints a usage rollup. `cost_usd` stays a plain float, so no caller's arithmetic changes. The cockpit's introspection panel is the consumer that reached the surface: an unpriced run rendered `~$0.0000` in the Cost cell and now reads "not recorded", the p50/p95 percentiles over a partly-uncosted sample are marked `≥`, and the "what is costing money" answer distinguishes "every step was measured and cost nothing" from "nobody recorded what this cost". Additive on the wire — `GET /api/workflows/{name}/ledger` and `…/runs/{id}/introspect` gain a key and change no existing one ([#2566](https://github.com/PersonalClaw/PersonalClaw/issues/2566)).
- A local model downloaded from a `catalog.json` now reports its **on-disk** state, so the truncated-model **Repair** button can finally appear — and finally repairs. Building the model list from a declarative catalog only probed the disk when the caller passed an optional `cache_root`, and the one shipped app that adopted the catalog didn't, so `downloaded` read False for weights sitting right there and `integrity` — whose sole writer is that probe — was never set at all. The chip and the Repair action the Models panel gates on `integrity === 'truncated'` therefore could not render in any build. The root is now derived from the provider's own `cache_dir()` (the same resolution the download runner already used, and now the only implementation of it), so the documented one-liner carries the verdict with nothing to opt into. Two consequences came with it: a mid-download model is no longer mistaken for a truncated one — the excuse is a partial artifact in the same directory the bytes were counted from, rather than an injected set of in-flight names that nothing outside a test ever filled — and Repair no longer short-circuits. "Already downloaded" now means "downloaded **intact**"; a truncated model previously satisfied the skip and the button answered an instant success while re-fetching nothing. Measured end to end on a 1800 MB card holding 3 MB: chip and button appear, one click pulls the real weights, the chip clears ([#1776](https://github.com/PersonalClaw/PersonalClaw/issues/1776)).
- A model id that escapes its cache root is refused instead of followed. Every layout probe joins the id onto the cache root, and for a catalog-driven provider that id comes from an **app-authored** card, so a `..` segment read — and, through the delete sweep, would have removed — files outside the root; measured before the guard, a card named `../SECRETS` made the size probe sum bytes from a sibling directory. The containment check sits at the single choke point all four probes share and fails closed (no candidates, so the model simply reads as absent). It validates the id's canonical segments rather than the resolved path, so a legitimate model directory symlinked onto another volume still reads as downloaded.
- The diff view's committed-side read (`GET /api/file-git-original`) no longer leaks a process per timed-out read, no longer answers `HTTP 500` when git cannot be executed, and no longer serves a directory listing as a file's committed content. All three were one cause — the endpoint had re-implemented the module's shared git invoker instead of calling it, and the copy forgot, one by one, what the original already knew. Measured on the pre-fix code: a timed-out `git show` was neither killed nor reaped, leaving **two live processes per timeout** (the `git` child and its grandchild) for as long as the panel was open; an unexecutable git escaped as `PermissionError` → `HTTP 500`, while the sibling git endpoints degraded to an empty payload; and because `git show HEAD:<dir>` exits 0 for a directory, the endpoint returned `{"content": "tree HEAD:sub\n\na.txt\nb.txt\n", "exists": true}` — a directory index rendered as the original side of a diff. The copy is deleted and the read goes through the one invoker, which now asks `git cat-file blob` (a non-blob is a refusal, not a listing) and reaps a blown deadline through the shared group-signalling helper. That helper fixed the survivor too: with a forking git (an fsmonitor hook, an LFS filter), the old pid-only kill turned a 1.00s deadline into a **135.48s** request, because `Process.wait()` resolves when the inherited pipe closes rather than when the child is reaped. The identical unreaped-timeout shape in the file-search endpoint (ripgrep, 15s) is fixed alongside it, and an AST rail keeps the module at exactly one git invoker ([#432](https://github.com/PersonalClaw/PersonalClaw/issues/432)).
- An agent write now validates the SHAPE of every field before storing it. `POST /api/agents` and `PUT /api/agents/{name}` checked the name thoroughly and the three list fields with an `isinstance` guard, then handed the other thirteen values straight to the config dataclass — so `{"description": 12345}` answered `200 {"ok": true}` and an int landed in `config.json` where a string is declared, after which the loader logged "type mismatch … using default" on *every* load forever (the repair it names only handles one-level paths, and `agents.<name>.<field>` is three). A ~1200-level-deep value was worse: a raw 500 with a traceback out of the config writer's `dataclasses.asdict`, reachable through a list field (`tools: [<deep>]`) as well as a scalar. All three write paths — including `PATCH /api/agents/detail/{name}`, which writes the runtime config the ACP agent reads at boot — now share one spec table checked by the same `coerce_edit_value` the Settings PATCH path, `PUT /api/config/personalclaw` and `personalclaw config set` already use, plus one body-nesting bound; a wrong type is a 400 that names the field, and nothing is written before the refusal. Two knock-ons: `natural_voice` requires a real boolean (`bool("false")` was `True`, so a request to switch it off switched it on), and a wrong-typed `skills`/`tools`/`triggers` is refused rather than silently ignored while answering 200 — clearing a list stays `[]`. Length bounds are deliberately non-binding, so a 200 KB system prompt and a 100 000-entry trigger list still save. Creating an agent under a reserved or retired system name is now refused too: `personalclaw-autonomous` returned `ok:true` and then vanished (the migration prunes it), and on a config with no `agents` map `personalclaw-lite` could be created with the caller's own prompt — which then became the built-in background worker's profile permanently, since seeding is add-if-missing and PUT/DELETE both refuse a reserved name ([#349](https://github.com/PersonalClaw/PersonalClaw/issues/349)).
- An OpenAI-compatible provider now discovers the models its endpoint actually lists, and says why when it cannot. The discovery URL was built with `if not base.endswith("/v1"): base += "/v1"`, so any base whose version is spelled another way got a **second** version segment: `https://api.z.ai/api/coding/paas/v4` was fetched as `…/paas/v4/v1/models` and Google's OpenAI shim `…/v1beta/openai/` as `…/v1beta/openai/v1/models` — both 404, both reported as zero models. `/v1` is now appended only when no path segment names a version, so the bare-host case the old check protected still works and the version-in-the-middle case does too (measured: of the thirteen base URLs the shipped provider apps declare, only those two change). Separately, "this endpoint serves no models" and "I never got a list out of it" were the same value — a blocked host, nothing listening, 401/403/404/500, a sign-in page returned as HTML, and a non-OpenAI envelope all returned `[]` at every log level, from a row that read `Configured` with `credential_status: ok`. Discovery failures now carry the URL tried and the HTTP status, log at WARNING, surface on the provider row, and give **Test connection** the real cause instead of one generic "No models available (check key/endpoint)" — which also stops it counting a curated fallback list it never reached ([#955](https://github.com/PersonalClaw/PersonalClaw/issues/955)).
- Knowledge search no longer hands back the whole library for a term the library happens to talk about — and no longer collapses to a single result when one title happens to match. The post-fusion "relevance cliff" that was meant to trim the weak tail could never do that job, so it is gone: results are fused with RRF, where a score is `Σ 1/(60+rank)` — a function of position and arm count that carries no information at all about how well anything matched, which is why the same reasoning already appears in `consolidation.py` ("a threshold is meaningless without its number space") and in the retrieve provider's own comment. Measured over 22 queries on a 26-item library, the consecutive-gap cut fired on 12 of them, and in every one the firing drop was the *title boost* (1/61 = 0.01639), with the arms' own contribution landing between -0.00190 and +0.00126 and never once producing the drop by itself — so which way it went depended only on whether some title happened to match the query: with one matching it returned 1 of 23 candidates for `scrub`/`raidz`/`smart`, 3 for `resilver` and 9 for `draid`; with none matching it returned all 23 for `zfs` and all 25 for `a`. The repair is upstream, in the one place absolute match quality still exists: **every arm now qualifies its own hits before fusion flattens them into ranks**, which is exactly what the vector arm already did with `_VECTOR_MIN_SIMILARITY`. The graph arm was the one with no qualification at all — a depth-2 traversal reaches the whole connected entity component, so it returned the same 23 of 26 items for `draid`, `resilver`, `scrub` and `smart` alike, of which 5, 17, 19 and 21 respectively contained the query term nowhere in title, summary, content or tags. Hop distance is that arm's own absolute scale and it was being computed and then discarded: an item that mentions an entity the query matched **directly** is a hit; an item reached only through a neighbour is the arm's fallback, ranked when nothing mentions a direct match (so an entity that exists but is mentioned by nothing still traverses). Measured after, over the same library through `GET /api/knowledge/items`: `draid` 9 → 18, `resilver` 3 → 6, `scrub` 1 → 4, `raidz` 1 → 11, `disk` 1 → 10, `zpool` 4 → 8, `zfs` 23 → 23 — and zero returned items with the term absent, for every query, where the pre-fix fused list carried 5 to 21 of them. Precision restored where the cut never fired, recall restored where it fired on a title. The arm-qualification contract is keyed by the arm vocabulary itself, so a fourth arm cannot be added without declaring one. Two things this deliberately does **not** decide, both being judgements about what counts as relevant enough rather than defects: a one-character query still prefix-expands to nearly the library (`a` → 25 of 26), and a search that legitimately matches most of a single-topic library is still broad, which is what makes a smart shelf built on such a term feel uncurated ([#392](https://github.com/PersonalClaw/PersonalClaw/issues/392)).
- **Removed** six orphaned `/api/memory/*` embedding endpoints (`embedding-status`, `embedding-models`, `enable-embeddings`, `disable-embeddings`, `activate-model`, `delete-model`) and the dead `setup_step`/`setup_error`/`can_retry` progress machine behind them. They were a second control plane for embeddings with **no frontend caller and no `api.ts` wrapper** anywhere — core, `web/`, or the apps repo — and every capability they offered already exists on the reachable, UI-wired plane (binding an embedding model in Settings › Models, `DELETE /api/models/local/…`, the downloads plane). The state machine's three diagnostics and its "Click Enable to retry" copy were written for a button nothing rendered. **The bug this carried:** the enable path also set `memory.migrated = true`, which makes markdown memory stop being read or written — so enabling embeddings through the orphan silently switched off markdown consolidation while the reachable path left it on. `migrated` belongs to migration, and the migration endpoint already sets it only when a migration actually produced entries; nothing else does now ([#522](https://github.com/PersonalClaw/PersonalClaw/issues/522)).
- A loop deleted while its cockpit is open now says so instead of rendering the loop forever. `runFold` has set a `deleted` flag on that lifecycle event since it was written and **no component ever read it**, the 30s fallback poll collapsed a 404 and a dropped connection into the same `null` (so it could only treat a post-load null as a transient blip), and every action then hit a swallowed `.catch(() => null)` — leaving live-looking buttons that silently did nothing. All three signals now reach the one "Loop not found" state the page already had, and that state was itself nested inside a never-loaded branch, so it was unreachable for exactly the case this is about. A 404 is treated as authoritative at any point; any other failure is still the blip it was, so a one-second hiccup cannot evict a live cockpit. Failed actions are reported through the shared helper the sibling Design cockpit already used. A rail keyed off the flag's own producer now closes the class rather than this one instance: it replays every lifecycle event through the real reducer to discover which flags can actually be written, cross-checks the `RunFlags` interface against `emptyRunFlags()` so the two cannot drift, and then requires each written flag to have a non-test reader outside `runFold.ts` — so the next flag added to `RunFlags` cannot ship inert the way `deleted` did ([#558](https://github.com/PersonalClaw/PersonalClaw/issues/558)).
- A knowledge **intent is now editable and pausable**, which turns three dead controls back on. `IntentEditor` mounted only on the new-intent branch (`selectedIntent.id ? <IntentDetail/> : <IntentEditor/>`), so an existing intent was read-only, the backend's documented upsert-with-id path had no caller, the list's `off` badge was unreachable markup, and `propose_skill` was frozen at creation. It was also the only writer of an intent anywhere in the product, and it hard-coded `enabled: true` — so nothing could pause one. That is a **cost** defect: an active intent spends a model call per saved item, and Run-on-existing fans out one per existing item (measured against a live gateway: `evaluated` 5 active → 0 paused over the same five items), and Delete — which destroys every outcome the intent gathered — was the only way to stop it. Each row now carries a Pause/Resume switch, the badge says **Paused** with the consequence on its `title`, the detail panel offers **Edit** (so goal, type filter, propose-skill and active state are all editable), edit mode rides the app-wide `?edit=1` primitive, and a paused Run reports the pause instead of "No matches in your existing items". Pausing is non-destructive: only deletion cascades into outcomes ([#542](https://github.com/PersonalClaw/PersonalClaw/issues/542)).
- The inbox no longer offers **Approve / Deny** on a workflow run that already ended. `GET /api/workflows/runs/{id}/continuations` checked only that the run *existed*, so a run that failed hours ago still handed out live resume tokens — and the inbox faithfully rendered a filled Approve button beside a "Handled" chip. Clicking it could not resurrect anything: `resume_run` has no live controller to apply the answer to and refuses with `WF_RUN_NOT_LIVE`, so the user spent the click and got a raw error code. The route now advertises nothing for a run in any of the four terminal states (parametrized over the closed `TERMINAL_RUN_STATUSES` vocabulary rather than over `failed` alone), and sends the run's status so a client can say *why* nothing is answerable. The pending record itself is untouched — six other consumers read it, including the rewind drop and the controller's idempotency check, so the filter lives at the HTTP surface rather than in `list_continuations`. The inbox gate now distinguishes three facts that had shared one sentence: a terminal run says which way it ended, a failed lookup says it could not check, and only a genuinely answered gate still reads "already answered" ([#583](https://github.com/PersonalClaw/PersonalClaw/issues/583)).
- **A widget's Submit button can no longer fail in silence.** Three defects stacked into one field report (a user hit Submit twice, months apart, and ended up copying 200 lines of text out of a widget to paste into chat): (1) `dashboard.widget_density: less` — a preference documented as *how often* the agent uses widgets — silently dropped the entire `data-action` return-channel section from the model's instructions, so the agent wrote a plain Submit button that could never post; (2) a real `<form>` submit is blocked by the widget sandbox (`allow-scripts`, no `allow-forms`) and reported only to the frame's own console; (3) `widget-error` had a producer and **no consumer** on the HTML widget host, so even a widget whose script threw failed mute. Now: both density variants document the return channel (frequency is the only difference, and an unrecognised density falls back to the default instead of blanking the block); a form submit is claimed by the child script — it SENDS through the documented channel when the form carries a `data-action`, which also makes Enter-in-a-text-field work, and otherwise reports an error naming the attribute and the sandbox; and the host surfaces `widget-error` as a toast ([#2263](https://github.com/PersonalClaw/PersonalClaw/issues/2263)).
- Entity **aliases** are now written, so the knowledge graph's deterministic alias pre-pass can actually match one. The `entities.aliases` column, `add_entity`'s `aliases=` parameter and the pre-pass's alias indexing all shipped in v0.1.0 and no production path ever wrote a value: the extraction prompt never asked for aliases, and the ingestion writer — the store's only entity writer — dropped them even when a model volunteered them (measured: two offered, `[]` stored). Four readers were therefore indexing canonical names only — mention linking, `find_entity`'s alias resolution, the memory graph's entity seeding, and the STT lexicon's vocabulary boost — so the "a document writes the entity by its handle, not its canonical name" case the pre-pass exists for was unreachable outside its own tests. The prompt now asks for the other spellings **the chunk itself uses**, and the writer forwards them — but only surfaces the document's text actually contains, checked with the same `AliasIndex` that decides what a mention is rather than a second matcher, because `find_entity` resolves BY alias and one invented surface would silently fold the next distinct entity into this one. A later document's spelling enriches an entity that already exists (the way `backfill_entity_description` already worked), established surfaces survive the re-ingest that deletes and re-creates the entity, and the set is deduplicated case-insensitively and capped. Measured on one fixture: the pre-pass index goes from 1 surface form to 3, a note naming only `SPRW` links 1 entity instead of 0, `find_entity('SPRW')` resolves to `Sparrow` instead of `None` (so a variant no longer mints a second entity), the memory graph's seeding carries the aliases across, and the lexicon boosts 2 alias surfaces instead of 0. An item's entity chips now name the aliases, so a link the document never spells out is explainable ([#1779](https://github.com/PersonalClaw/PersonalClaw/issues/1779)).
- The **Tasks** page now loads every task instead of the server's default first 50, and says so when it cannot. No caller sent a `limit`, so the page silently became a partial view past 50 tasks — and because every view there derives structure from that one array, the failure was worse than a short list: the dependency graph drops edges to ids it does not hold, so a task whose prerequisite was row 51 drew as a clean **unblocked** node; the prerequisite picker could only offer the first 50 candidates; "what depends on this" under-reported. The gateway itself was also deriving `block_reason` from the returned page, so it answered `is_blocked: false` for a task whose blocker sat on another page — a wrong answer, not a missing one. `/api/tasks` now computes blocked-ness over the whole matching set, reports an honest `total` (it previously capped at 500 per provider, having asked each provider exactly once), and declares `complete`; the client pages to completeness and states "Showing N of M tasks" with the derived consequence when a bound truncated it. The fourteen internal callers that meant "all of them" and spelled it `limit=10_000` / `limit=500` — a loop's progress count, a project delete, a task-list reset gate — now use the named `collect_tasks` primitive, so none of them silently truncates on a busy install ([#485](https://github.com/PersonalClaw/PersonalClaw/issues/485)).
- A project or task-list **name** is now validated the same way on create and update, and is length-capped. The two verbs disagreed, in opposite directions: create called `.strip()` on the raw body value and answered an unhandled **500** for every non-string (`AttributeError: 'int' object has no attribute 'strip'`), while update called `str()` first and so *invented* a value instead of refusing one — `{"name": null}`, plausibly meaning "clear it", persisted a project literally called `None`, and a dict persisted as Python's `repr` (`{'a': 1}`, Python syntax, not JSON) inside `project.json`. Neither side coerces now: a wrong type is a 400 naming the field and the type it got. `agent_instructions_template` was worse still — assigned with no coercion *and* no check, so a dict round-tripped into the stored JSON as a nested object where every reader expects a string; it and `brief` are gated too. Names are also capped at 200 characters (**refused, not truncated** — a silently shortened name is the same invent-a-value mistake, and can collide with an existing one), matching the cap `record_path` already enforces on ids, and both name fields carry it client-side so the limit is reachable before a save ([#456](https://github.com/PersonalClaw/PersonalClaw/issues/456)), ([#514](https://github.com/PersonalClaw/PersonalClaw/issues/514)).
- The project hub's task-list **count badge** now renders. It has read `task_count` since the initial public commit while no endpoint ever emitted the field, so `typeof count === 'number'` was always false and the badge was silently absent — the sibling project row does the same thing correctly with `task_list_count`, so the pattern was proven and this endpoint just never adopted it. `GET /api/task-lists` now emits it from a single aggregation (not one scan per row), and **omits the field entirely when the count cannot be computed** rather than sending `0` — a missing reading hides the badge, whereas a zero would assert "this list is empty" ([#514](https://github.com/PersonalClaw/PersonalClaw/issues/514)).
- The inbox header and its own filters no longer describe different sets, and **glancing at an item no longer decrements the count**. The header rendered the server's PENDING-only count while every filter, classification count and kind chip on the same screen counted PENDING **or** SEEN — measured on a seeded store: header **33**, Open filter **37**, four read-but-unanswered rows apart, with neither number wrong on its own terms. Because opening a row marks it SEEN, merely *looking* at an item moved the header 33 → 32 while the filters stayed at 37 and nothing was resolved; the `dismiss-all` confirm was sized from the same field, so it offered to dismiss 33 items and the endpoint answered `{"dismissed": 37}`. A row you have read but have not answered is still your work, so the count is now the open set everywhere and only an ACTION moves it. Three more count surfaces are fixed by the same ruling: Mission Control's lanes, the Action Center and the hero "inbox" pill were fed a PENDING-only endpoint while their own status table calls `seen` open, so a glance in the inbox deleted the row from the dashboard; and the mobile companion's inbox — a resolve-only surface — hid every row you had already opened. The read/unread distinction stays exactly where it belongs: the per-row accent rail and unread dot ([#493](https://github.com/PersonalClaw/PersonalClaw/issues/493)).
- An app's `icon` can no longer crash the dashboard, and it is now validated at install rather than only at paint. `resolveAppIcon` indexed lucide's module namespace and trusted the result (`reg[name] ?? Blocks`), but **seven of its letter-starting exports are not icons** — `icons`, `default`, `module.exports`, `createLucideIcon`, `useLucideContext`, `LucideProvider`, `Icon` — five of which throw when rendered and two of which fail quietly (`LucideProvider` renders nothing; `Icon` wants an `iconNode` a manifest cannot supply). Because the sidebar nav resolves every app's icon through that function, one word in one app's manifest took down the whole dashboard rather than one card. Resolution now matches on IDENTITY against lucide's own icon registry — which rejects all seven while keeping every alias working, since an alias is the same component object as its canonical name — and an exhaustive rail renders the resolver's output for every letter-starting export so the next lucide release that adds a non-icon cannot reintroduce the class. Two things are new here on top of that: a **second, independent layer validates the manifest at install**, so an `icon` (top-level or per UI page) must be a bare identifier and an emoji, a phrase or a path is refused with a message naming the value instead of silently becoming the generic glyph, and a traversing `iconUrl` is refused alongside the manifest's other app-supplied paths; and **icon names now resolve case-insensitively**, which fixes a shipped first-party app (`meta-muse-spark` declares `"brain"`) that had been rendering the fallback glyph with no error anywhere. The fold is built from the same identity predicate, so it is not a second unguarded door — a rejected export cannot re-enter by being spelled differently — and it compares COMPONENTS rather than counting names, because lucide ships case-only spelling pairs (`Grid2x2Check`/`Grid2X2Check`, `Move3d`/`Move3D`, `Grid3x3`) that export the same object: dropping on the collision alone refused 7 names it had nothing to choose between. A genuinely value-distinct pair still falls back rather than coin-flipping ([#579](https://github.com/PersonalClaw/PersonalClaw/issues/579)).
- The Doctor's health score now says what it is excluding, and so does `personalclaw doctor`. Measured on a home with 25 knowledge items and no embedding model bound: `GET /api/doctor/remediation` returned `score: 100` beside `knowledge_missing_embeddings ×25, penalty 12.5, reachable: false`, and the panel could say only `· not fixable yet`. The score is right — an unreachable deficit is at its floor and excluded, because penalising it would keep the score permanently red for something no maintenance run can improve — but "yet" promises a later pass, and no pass will ever reach this: what is missing is an embedding model, not a tick of the clock. The engine computed that reason and dropped it one line later, so a `Deficit` now carries `blocked_by`, one user-facing sentence naming the prerequisite and the next step, produced once in core and rendered verbatim by both surfaces. `personalclaw doctor` grows a **Maintenance** section (score vs target, every non-zero deficit, each blocker) — it previously printed no part of this payload at all, saying `embeddings: ⏹ disabled` and nothing about the 25 items now keyword-only; a backlog is informational there and never fails the doctor. The plan line stops merging two opposite states ("nothing above is fixable" vs "no deficits measured"), the run ledger carries when it ran and what each job did (a pass whose jobs all threw used to render identically to one that did the work), the health verdict states when it was taken, and the Run-now toast takes its level from the result instead of the literal `success` — a run returning `jobs: []` / `target_score already met` raised a green success toast that meant "done". A new rail derives the field list from the payload's own shape, so a doctor field cannot ship without a reader ([#538](https://github.com/PersonalClaw/PersonalClaw/issues/538)).
- A routing mute can now be undone from **Settings → Chat → Agent routing → Muted agents**, and the panel stops promising a control it did not have. Dismissing an agent's routing suggestion three times mutes it permanently — there is no expiry, and the mute is checked before the cooldown, so neither the "Dismiss cooldown" field nor the section's master switch could clear it — while the cooldown hint told the user three dismissals "mute it until you re-enable". The new row lists every muted agent with its dismissal count and an Unmute, and it lists the **store's own keys** rather than the agent catalog, because a mute can outlive its agent: `dismiss` never checked that the name was a real agent, an agent can be deleted while muted, and a reserved built-in renders no Advanced section — three classes the existing per-agent Unmute on the agent detail page structurally could not reach. That per-agent control also stopped reporting a muted agent as "Active — eligible for auto-routing suggestions": the suppression store's identity is case-insensitive, so a mixed-case agent name (including the shipped default, `PersonalClaw`) never matched the canonical key it was compared against. The third dismissal now says so at the moment it happens, and names where to undo it, instead of the agent silently going quiet forever ([#414](https://github.com/PersonalClaw/PersonalClaw/issues/414)).
- The **skill-proposal queue can now be emptied, and it stops refilling itself.** The 409-forever half of this cycle was fixed earlier; two links survived and kept proposals at 89% of the open inbox. **Refill:** any producer could file an unbounded number of proposals about ONE skill — measured 20 of 20 same-target proposals admitted in twenty minutes, 20 open inbox rows for one skill — because the "one refine per skill per day" rule lived in the stumble arm and only the stumble arm asked for it, while the after-turn skill ladder and the auto-skill synthesizer went straight to the queue. That rule is now at the sink (`proposals.enqueue`), keyed on the same "which skill is this about" resolution `accept` uses, so every producer — including a future fourth — inherits it; proposals already on disk are untouched and still all acceptable, since no enqueue-time rail can reach them. **Reducibility:** dismissing a proposal's inbox row left the record `pending`, so the inbox and the Skills page reported two counts of one queue (measured 18 open rows against 19 pending proposals) and `dismiss-all` over 32 rows moved "Proposals (32)" by zero. A dismissed row now answers the proposal it mirrors, through one dismissal owner shared by both terminal-transition paths — the mapping `reject()` already declared in the other direction. Rows of other kinds (a workflow gate) are keyed out and untouched. **Reach:** the Dismiss-all control was gated on the *pending* count while the sweep covers every *open* row, so merely reading the queue made the only bulk control vanish; the status endpoint now publishes `open_count`, the button and its confirm read it, and the confirm says what the sweep costs (including that proposals are rejected). **Visibility:** `personalclaw skills list` and the loop classifier's capability catalog still walked skill roots one level deep, so every accepted proposal's `auto/<slug>` skill was invisible to both (3 on disk, 0 listed) — all four listing surfaces now share the loader's own `iter_skill_files` ([#409](https://github.com/PersonalClaw/PersonalClaw/issues/409))
- **Set default** on an agent now confirms before rewriting the global `default_agent` — the agent every new chat starts with changed on a single unconfirmed click, while Delete in the same component both confirms and refuses to touch the default. The dialog names both agents (“New chats currently start with X. They will start with Y instead.”), which matters because the previous default's name appeared nowhere else in the interaction — there was no way back short of an out-of-band API call ([#666](https://github.com/PersonalClaw/PersonalClaw/issues/666)).
- A lifecycle trigger's **Test** button is now a rehearsal, not a fire: it no longer writes into the trigger's real `run_count`/`last run`/`last status` (“Ran 2× · ok” could previously describe a trigger that had never actually fired — both runs were Test clicks), and the action's payload is tagged the way the event-trigger test path already tags it, so a provider can tell a rehearsal from the real thing. The notify action marks tagged rehearsals with a `[test]` title prefix on BOTH trigger paths — a test notification is no longer indistinguishable from a live alert in the inbox — and the panel's Test button now refreshes like its save/delete/toggle siblings. Every guardrail gate (incident, denylist, rung routing) still applies to a test fire, unchanged ([#609](https://github.com/PersonalClaw/PersonalClaw/issues/609)).
- The **YOLO mode** toggle (Settings → Agent defaults) now applies immediately instead of at the next gateway start: the config field's only reader was the startup seed, so flipping it changed the file while the running instance kept its previous posture — worst in the OFF direction, where revoking the approval bypass silently did nothing and the UI read `false` while approvals stayed bypassed until restart. The config PATCH now drives the live trust mechanism both ways: enabling mirrors the startup path (audit-gated, permanent — deliberately unlike the chat pill's expiring override), and disabling revokes at once through the same path a TTL expiry takes, clearing untrusted per-session auto-approve policies; the row's hint now states the immediate, non-expiring behavior ([#672](https://github.com/PersonalClaw/PersonalClaw/issues/672)).
- A refused inbox-settings write now rolls back instead of keeping the rejected value on screen: Retention accepted `-5`/`0`/`99999`, and even once the failure notification landed, the optimistic merge stood — the field showed the refused value as saved until a reload silently restored the stored one. Both copies of the panel (the inbox drawer and the settings page, kept in field-parity) now restore the pre-patch value on rejection, the same sanctioned rollback their sibling toggles and the speech panel already use ([#624](https://github.com/PersonalClaw/PersonalClaw/issues/624)).
- The app **Configure** dialog now reads its schema's `required` array. A required field rendered identical to an optional one, `Save` was gated on busy-ness alone, and the only feedback was a server 400 quoting the **schema key** (`missing required config key: 'title_template'`) that the user then had to map back to the row labelled "Task Title" — on at least six shipped apps whose required field is the load-bearing one (`bash-action.command`, `run-script-action.script`, `create-task-action.title_template`). A required field now carries the same ` *` marker the trigger action-config form already used plus `aria-required` on the control, both Save buttons (the Apps modal and the Settings › Apps row) disable with a reason naming the missing **labels**, and the refusal also lives in the shared hook so a third consumer cannot forget it. The emptiness rule is shared with the other schema-driven forms and is deliberately type-aware: `false` and `0` count as filled, and a write-only sensitive field whose secret is already stored stays satisfied while its input is blank — otherwise that form could never be saved again ([#491](https://github.com/PersonalClaw/PersonalClaw/issues/491)).
- Config validation now enforces the constraint keywords a manifest declares, through **one** validator instead of two that had drifted. `minimum`/`maximum`/`exclusiveMinimum`/`exclusiveMaximum` for numbers and `minLength`/`maxLength`/`pattern` for strings were accepted in a schema and silently ignored — and this was live, not hypothetical: `native-vector-memory`'s **Confidence Threshold** declares `[0.0, 1.0]` and accepted `5`, `-1` and even `true`, while `browse-action`'s **Step limit** declares `minimum: 1` and accepted `0`. The provider settings path (the reachable one — 64 shipped manifests declare a `settingsSchema`, none declares the app-level `configSchema`) enforced no bound at all and accepted `true` for an `integer`, because `bool` is an `int` subclass. Both paths now delegate to one shared module whose supported keyword set is named, so a keyword cannot be honored on one surface and ignored on the other; refusals name the field's **label** — the same string the form row shows — instead of a raw manifest key; and both forms mirror the constraints as native input attributes so a save is no longer the first feedback. Each path keeps its own deliberate object-level policy (an app refuses an unknown key, a provider ignores one so a config carrying an older manifest's key still saves) ([#616](https://github.com/PersonalClaw/PersonalClaw/issues/616)). Constraints apply only to correctly-typed values, a broken manifest regex is logged and skipped rather than walling off the user, and the config form mirrors the declared bounds as native input attributes so the field hints before the round trip ([#616](https://github.com/PersonalClaw/PersonalClaw/issues/616)).
- Manually reclassifying an inbox item now marks the verdict as the user's own ("Set by you") instead of keeping the AI's confidence in the overridden verdict, and the classification feedback thumbs hide once no machine judgment is on display — Mark wrong can no longer file training feedback against a classification the model never produced.
- One-line previews are plain text: inbox rows strip markdown marks from both the painted preview and the announced name (a digest's ** and ## no longer render literally or get read aloud), and the artifact card's clipped excerpt is aria-hidden — a decorative thumbnail no longer walks 600 chars of raw markdown into the accessible tree.
- The week grid and the server now agree on one projection window: the grid sends its exact local-week end (7 local calendar days = 167h/169h across a DST transition, not a fixed 168h), so a real fire is no longer dropped on spring-forward weeks and no phantom hour is drawn on fall-back weeks; any out-of-window occurrence is disclosed in the caption instead of vanishing.
- The Files page keeps unsaved edits across a rename: it now owns the draft cache FileViewer documents, the cache entry moves with the file (no more "Rename and discard" consent — nothing is discarded), a confirmed close purges the draft so a discarded edit cannot resurrect on reopen, and the Code cockpit's two programmatic close paths (workspace switch, worker delete) purge theirs too.
- The **Speaking speed** slider (Settings → Speech & Transcription) no longer tells every provider the same story: its Fast/Slow ends and "lower is faster" hint were Piper's `--length-scale` semantics, which are exactly backwards for OpenAI-compatible remote voices (the same raw number is the API's multiplier, where higher is faster) — so dragging toward "Fast" made remote speech slower. The ends and hint now follow the bound provider family while the stored value stays raw, since both backends consume it correctly. And a Gemini-bound voice no longer shows the slider or the persona select at all — its speech API has no speaking-rate parameter and ignores the shared personas, so the panel states that instead of rendering two dead controls ([#657](https://github.com/PersonalClaw/PersonalClaw/issues/657)).
- Editing a prompt or snippet whose body contains a credential-shaped string no longer destroys it. The read path redacts before returning, the edit form seeds its draft from that response and sends the mask back verbatim, and the write path had no inverse — so saving after ANY edit, even a title-only one, persisted `[REDACTED: credential]` over the real content, irreversibly (prompts keep no version history). The write path now restores every mask it is handed from the stored value before persisting, in order, so all the user's other edits in the same save still land; deleting a marker still deletes the value it stood for, and a body whose stored copy no longer lines up is refused with a 409 rather than saved. Redaction is unchanged on the wire — the plaintext moves store-to-store only, so no endpoint has to serve a secret to make editing work — and the display mask is now defined once (`redact_for_display`) so its inverse cannot drift from it. Prompt snippets carried the same read/write asymmetry and are fixed with the same seam ([#440](https://github.com/PersonalClaw/PersonalClaw/issues/440)).
- Acking one notification can no longer permanently delete up to 200 older ones. The load path capped the in-memory log at 200 rows while the file legitimately holds up to 400 between trims — and ack/unack/delete rewrite the file *from memory*, so after a restart the first "Mark read" click silently truncated the log to whatever had loaded. Memory now mirrors the file exactly (lossless load, lossless rewrite), and the size cap is enforced at the one seam where rows are born: past 2× the cap, memory and file drop to the newest 200 together ([#420](https://github.com/PersonalClaw/PersonalClaw/issues/420)).
- A retired built-in app no longer lingers as an undeletable broken card. The ScheduleService retirement deleted `create_schedule_provider` from core and stopped bundling `personalclaw-schedule-tools`, but nothing removed it from an existing install, because `seed_builtin_apps()` only ever walked FORWARD over what the wheel still ships. On a home upgraded across six releases the app therefore stayed installed and enabled, was locked native so disable and uninstall both refused, rendered beside the working built-ins with an "Installed" badge and no error, answered `404 not installed` to `DELETE` while the list said it was installed, and logged an `AttributeError` on every single boot — with hand-editing the home as the only escape. Seeding now has a reverse pass: a seed-marker name whose packaged native source is gone is reconciled by asking the CODE rather than the marker. If its provider named a core factory that no longer exists the app cannot work, so it is unlocked, disabled and removed — unless its `data/` holds anything, in which case it is unlocked and disabled and left for the user, because a retirement must not delete what someone may still want. If the implementation is not a dead core factory the app was merely de-cored, so it is unlocked and keeps running as an ordinary user-manageable app. The hardcoded one-shot that did this for a single app is deleted, being one instance of the same rule ([#368](https://github.com/PersonalClaw/PersonalClaw/issues/368), [#334](https://github.com/PersonalClaw/PersonalClaw/issues/334)).
- An upgraded home no longer ends up with two of a system-owned scheduled job. The reconcilers that register the notification digest, source digest, usage recap, self-remediation and identity-report triggers look their row up by a DETERMINISTIC id — deliberately, since a generated slug would make every restart add another copy — while the boot import of the legacy `crons.json` upserts by id, equally deliberately, so a hand-authored row survives a re-import. Neither side is wrong alone and nothing reconciled across them, so a home upgraded from the legacy scheduler carried the migrated copy under its random id AND the freshly reconciled copy: two identical enabled rows on the same cron, armed for the same instant, indistinguishable in the Triggers list, and the job ran twice a day. Convergence is now keyed on the JOB (the inline action provider) rather than the id, in one shared primitive all five reconcilers call — so no future system job can be duplicated the same way. A row authored by the user or an agent for the same action is never touched, and a retired duplicate's `enabled` flag is adopted rather than discarded, so a digest the user had switched off does not come back on ([#396](https://github.com/PersonalClaw/PersonalClaw/issues/396)).
- Resetting a Repeatable task list now actually returns it to a not-yet-run state. Reset wrote three fields and its docstring named those same three, so `action_plan` — whose steps each carry their own `completed` flag — was in neither: a weekly checklist reset for its next run displayed every step ticked and struck through, and the user either unticked each one by hand or worked from a plan claiming it was already done. What a reset clears is now ONE table over `Task`'s fields with an exhaustiveness test, the same shape this module already uses for field coercion — so a per-run field nobody remembers is a red test rather than the next silent omission. That table also caught three more fields carrying the last run's state into the next one: `evidence` ("a done task with no evidence is a claim" — evidence from a finished run is not evidence about this one), `attempts`, and `preview`/`blocked_kind`/`blocked_reason_kind`, which rendered a blocked badge on a freshly reopened task. Notes, research notes, and everything defining the work are explicitly preserved, each with a stated reason ([#389](https://github.com/PersonalClaw/PersonalClaw/issues/389)).
- The tool inspector no longer walks a user into a dead end on a disabled tool: Try it kept offering the full Run → Confirm flow and only failed after arguments were filled in, via the server's 403 refusal. The run action now says why it can't fire ("Disabled — turn it on in the tools list to run it") with a matching header pill, while the parameter form stays as documentation. Locked tools keep their Run flow deliberately — the invoke endpoint exempts them from refusal, so locked means non-toggleable, not non-runnable ([#868](https://github.com/PersonalClaw/PersonalClaw/issues/868)).
- Artifact tags are now editable where they display: the details rail's static pills become the house chip editor (add on Enter, named remove buttons), giving `PATCH /api/artifacts/{slug}`'s long-accepted `tags` field its first UI writer — previously whatever an agent set was what you had, while the sibling `collection` field got an editor and the list endpoint's tag filter stayed load-bearing for loop cockpits. Writes are pessimistic (persist, then repaint from the reload), so a refused write leaves the stored tags on screen and says why ([#669](https://github.com/PersonalClaw/PersonalClaw/issues/669)).
- Reordering an action plan no longer deletes the completed steps: a locked row is deliberately rendered outside the reorder group so it cannot be dragged, which means Motion's `onReorder` can only describe the DRAGGABLE rows — and the write-back treated that partial array as the whole list, so one drag destroyed every ticked step (six of ten on a half-finished plan) and Save persisted it, under a tooltip promising "A completed step keeps its place". The merge now lives in the `Reorderable` primitive that removed those rows: it splices the reordered draggables back around every locked row at the index it already held, so each consumer receives what `onReorder`'s type has always promised — the full list, in its new order — and a row missing from the report keeps its place rather than vanishing ([#487](https://github.com/PersonalClaw/PersonalClaw/issues/487)).
- Picking a personality now actually changes the assistant's voice: the chat send path attaches the active personality's persona theme, wiring the frontend half of a persona-injection path whose backend was fully built but read a field no client ever sent. Claw Arcade's promised playful voice gains its persona snippet (registered in the catalog and the injector's theme set), and rails pin the theme set to the shipped snippet files so the three lists cannot drift apart again ([#650](https://github.com/PersonalClaw/PersonalClaw/issues/650)).
- An unsaved edit to a markdown memory doc (Preferences / Projects / History) now survives clicking another Studio item. The editor tracked dirty state and displayed "Unsaved changes", but nothing gated navigation: the Studio unmounts the editor on every selection change, so the text was discarded silently — no confirm, no browser-exit warning, no retained draft — on files that are injected into every agent prompt. The draft now lives in a host-owned per-doc cache (the same shape the file and artifact viewers already take from their hosts), so switching away and back restores the edit exactly, still marked unsaved; the disk read still happens on every mount, so a restored draft is measured against CURRENT content and a Save cannot silently overwrite a change made elsewhere. A cached draft is restored only once the read has SUCCEEDED, so the retention sits on top of the failed-read guard rather than around it: a doc that could not be read stays uneditable and the draft waits in the cache for the retry. A refused save keeps its draft. The browser-exit guard that lived inline in the file-tabs hook is extracted to `useUnsavedGuard` and shared, so this is a second consumer rather than a second copy. The textarea also announced only "edit text, multi-line" — it now carries the doc's own name, read from the same list the Studio renders ([#525](https://github.com/PersonalClaw/PersonalClaw/issues/525)).
- **Install consent now distinguishes "this app asked for nothing" from "nobody has read its manifest yet", on both surfaces that ask you to consent.** The Store's detail panel hid its Permissions section whenever permissions were empty, so the 33 of 36 Store apps that declare no block rendered no section at all and `PermissionList`'s own honest "None — this app is granted no gateway capability" copy was unreachable; the install modal had the opposite failure, because `CatalogEntry.to_dict` ships a `permissions` object for *every* row — `{}` for a declared-none manifest **and** `{}` for a registry pointer whose manifest is not fetched until install — and `{}` is truthy, so a pointer took the known branch and asserted an app was granted nothing when nobody had read it. A new `consentKnown` flag on catalog entries is the one authority (false only at the pointer builder, true at all three manifest-backed builders), and one `consentPermissions` helper is where all four consent call sites — the Store detail panel, the card grid, Manage Sources, and the first-run essentials step — derive it, so the two cases cannot diverge per caller again.
- Code cockpit findings now attribute to tasks: the finding ingest canonicalizes model-authored stage labels ('1 — Write bell_times.py', 'Stage 2/2 — Verify & QA') against the loop's plan at the one write into the ledger, and the cockpit's matcher gains a normalized fallback for already-recorded labels — a 12-cycle blocked loop's entire reasoning trail (including the cycle that diagnosed the blockage) was invisible behind 'No activity yet' on every task.
- The Vocabulary section's promises are now kept on both ends: the composer's mic dictation route was calling the flat transcriber — a function with no bias parameter at all — so the personal lexicon and learned corrections biased only knowledge audio/video ingestion while four copy sites claimed "mic input" too. The mic path now resolves the lexicon's bias terms before decoding and runs the learned-corrections pass after, exactly as ingestion does (best-effort — a lexicon failure never breaks dictation), with credential redaction pinned downstream of correction, which re-derives flat text from raw words. And learned corrections finally have a writer: `lexiconAddCorrection` existed with zero call sites (its handler docstring named two callers that don't exist), so the section gains an add-a-fix form (heard → meant) and copy that describes the real mechanism instead of an unbuilt transcript-edit capture flow ([#658](https://github.com/PersonalClaw/PersonalClaw/issues/658)).
- The app enable/disable toggle now reads Activate/Deactivate on every surface (card, menus, detail panel) — the detail panel's old "Install" implied a re-download that never happens, since a deactivated app's files stay on disk; "Install" is reserved for real store downloads.
- Deleting a single notification now confirms first: the per-row Delete removed the entry from disk on one unconfirmed click with no undo, while Clear all on the same page confirms and every sibling per-row delete in the app gates on the shared confirm dialog. The dialog names the notification it is about to remove and states that it cannot be undone; dismissing it deletes nothing ([#628](https://github.com/PersonalClaw/PersonalClaw/issues/628)).
- The Settings → Apps tile now counts what its caption says: it read "0 installed apps" on an instance with 33 installed, because the stat deliberately filters to non-provider apps (providers configure under Settings › Providers) but captioned the filtered count with the unqualified noun. The stat is now "N apps with settings" — the subject of the panel it opens — with the installed total as context ("of 33 installed"), so a zero reads as "none of your apps contribute settings here" rather than "you have no apps" ([#615](https://github.com/PersonalClaw/PersonalClaw/issues/615)).
- The agent detail panel now names its trigger bindings instead of printing raw hex ids: bound triggers resolve through the same endpoint the picker built its options from and render as "name · event", matching Skills/Tools (whose stored values are already labels). A binding whose trigger was since deleted is marked "trigger no longer exists" with the id kept legible for cleanup — and when the trigger list cannot be fetched, the panel shows the raw id and claims nothing ([#629](https://github.com/PersonalClaw/PersonalClaw/issues/629)).
- The trigger list's lifecycle badge now reads each event's real fire path: globally-fired events (like MemoryWrite) no longer wear "dormant" while actually firing, agent-scoped events with no referencing agent say "no agent references this", "dormant" is reserved for events nothing fires, and the create form discloses agent scoping at the point of choice.
- The artifact viewer tells the truth about a version that failed to load — a danger banner with Back to current instead of showing CURRENT content under a "historical vN (read-only)" claim with Revert armed for a nonexistent version — and closing version-compare (or the cockpit's diff) no longer throws Monaco's TextModel-disposed error: both DiffEditor sites detach their models through one shared teardown hook.
- An exited terminal pane now retires itself from the run-in-terminal bridge: hasActiveTerminal() no longer reports a dead pane as live, so a queued "Run in terminal" command is no longer claimed and burned against a shell that already showed "Process exited" — it opens a fresh pane instead.
- Prompt authoring now reads the same variable grammar the engine renders: inline typed declarations ({{ name::type }}, {{ name::select::[a, b] }}) appear in the undeclared-placeholders strip with their declared type and options, and the add-chip creates exactly the variable row the declaration asks for. The old client regex was blind to every typed form, leaving the engine's typed-variable capability unreachable from the UI.
- A project's linked **Artifacts** row now actually lists its loops' deliverables: `/api/projects/{id}/linked` filtered artifacts on `project_id`, which the loop-deliverable convention never wrote — those artifacts carry a `loop:<id>` tag instead, so the row was permanently empty on exactly the artifact class a project is guaranteed to produce. The endpoint now matches either linkage in one pass (the stamped `project_id`, or a `loop:` tag naming a loop the project owns), deduplicated — so pre-existing deliverables surface immediately while newly created artifacts keep the cleaner stamped contract ([#639](https://github.com/PersonalClaw/PersonalClaw/issues/639)).
- Dismiss all now records the same per-item dismiss engagement signal for every item it sweeps, so the strongest topic-rejection gesture trains inbox ranking instead of being discarded (one store write per sweep).
- Generate draft no longer runs on inbox items that can never be replied to: `can_reply` gated only the Send button, so on a read-only item the model ran, a full reply persisted, the row gained a `draft` badge — and Send stayed disabled. The server now refuses drafting before the model call with the same message the send gate uses, and the composer only renders where a reply can actually go (a draft saved before the gate still displays, with why it cannot be sent) ([#621](https://github.com/PersonalClaw/PersonalClaw/issues/621)).
- Quiet hours no longer accepts a zero-length window: `08:00 → 08:00` saved with a Saved ✓ while the delivery gate documents start == end as never matching — quiet hours read as enabled and suppressed nothing (the opposite of the all-day quiet a user plausibly meant). The same domain guard that already rejects unparseable times now refuses the degenerate pair with a message naming the consequence and the all-day escape (00:00 → 23:59); the check runs on the effective merged pair so a single-key update cannot land start onto the stored end, and compares parsed minutes so `8:00` vs `08:00` cannot dodge it ([#627](https://github.com/PersonalClaw/PersonalClaw/issues/627)).
- Project guard refusals now speak the UI's own vocabulary: deleting or renaming a protected project says "the built-in project 'Personal' cannot be deleted" instead of "the **default** project…" — a singular article that was returned for two different projects and clashed with the Built-in badge those rows already carry. The internal constant follows (`BUILTIN_PROJECTS`), so no "default" concept survives to mislead the next reader ([#638](https://github.com/PersonalClaw/PersonalClaw/issues/638)).
- Artifact list responses no longer serve a fabricated `live_dirty`: the flag is computed per read by the detail path while list rows come off persisted metadata that never stores it — so the same artifact reported `False` in the list and `True` in the detail, and every consumer had learned to distrust the list copy. The flag (like `content`, its computed-per-read twin) is now simply absent from list-shaped responses and stays computed on content-bearing ones, so a reader cannot mistake the default for an answer ([#630](https://github.com/PersonalClaw/PersonalClaw/issues/630)).
- The Agents page's group headers now count what the group shows: every group filters its rows through the search box, and the no-match empty states already say so, but each header's `count` read the unfiltered catalog — so searching for something absent rendered `Native | 8` directly above "No matching agents". Both the Native and per-provider Discovered headers now count the same filtered list their rows render from, and a Discovered group's search miss reads "No matching agents." instead of the empty-catalog "No agents discovered." ([#667](https://github.com/PersonalClaw/PersonalClaw/issues/667)).
- Creating an artifact with an unrecognized `kind` is now a 400 naming the allowed set instead of a silent success that stored the artifact as `widget` — the sandboxed-*execution* kind, so a typo like `"markdwon"` or a plausible `"md"` had its content treated as executable widget payload rather than prose, and lost the comment layer without explanation (sandboxed kinds are not commentable). The write path now fails loudly the way the binary path already does for exactly this shape; an *absent* kind keeps its documented `widget` default, since saving a chat widget is the primary flow ([#633](https://github.com/PersonalClaw/PersonalClaw/issues/633)).
- A reaped trigger run reads as a failure everywhere it renders: the reaper writes `health_status: degraded` plus the reap reason while the run-store row still says `success`, and both the detail panel's Last-run badge and the list's schedule-row dot read the two fields through a bare `last_run_status || last_status` chain — so the one record where the fields disagree rendered a green "ok · 1d ago" two lines above the red "Reaped after 1818s" banner. One shared reconciler (`lastRunMeta`) now makes a non-ok health rollup dominate the run row on both surfaces, rendering through the existing health vocabulary (degraded/parked/failing) with the legacy `error` value keeping its shape; an ok or absent health defers to the run row exactly as before ([#685](https://github.com/PersonalClaw/PersonalClaw/issues/685)).
- The **YOLO mode** toggle (Settings → Agent defaults) now applies immediately instead of at the next gateway start: the config field's only reader was the startup seed, so flipping it changed the file while the running instance kept its previous posture — worst in the OFF direction, where revoking the approval bypass silently did nothing and the UI read `false` while approvals stayed bypassed until restart. The config PATCH now drives the live trust mechanism both ways: enabling mirrors the startup path (audit-gated, permanent — deliberately unlike the chat pill's expiring override), and disabling revokes at once through the same path a TTL expiry takes, clearing untrusted per-session auto-approve policies; the row's hint now states the immediate, non-expiring behavior ([#672](https://github.com/PersonalClaw/PersonalClaw/issues/672)).
- **Reset everything to defaults** (Settings → Design) now also resets the Dark/Light/Auto preference: mode lives in its own store (localStorage `mode`) separate from the appearance overrides, so the reset left a Light interface light — and mode is exactly the control an unusable-contrast recovery needs. The out-of-the-box preference is now a named constant shared by the first-load fallback and the reset, so the two cannot drift ([#675](https://github.com/PersonalClaw/PersonalClaw/issues/675)).
- `GET /api/artifacts/{slug}/versions/{n}` now self-describes as the version it carries: `native.get()` read the head metadata and swapped only the content, so v1 and v5 both reported `version: 5` next to different bytes — any caller labeling a version from its own payload mislabeled every historical fetch. Both branches (text and binary raw-ref) now set `version` alongside the content swap; the head fetch is unchanged ([#504](https://github.com/PersonalClaw/PersonalClaw/issues/504)).
- Prompt render and preview now accept the same variable-value payload: preview took the map under `values` while render/launch/snippet-render demanded `variables`, and each silently ignored the other's key — so render returned a false `missing required variable` for a variable that **was** supplied (pointing the caller at the template instead of the request), and preview returned `ok: true` with unsubstituted braces. All four endpoints now share one contract: the documented key wins, the sibling key is accepted as an alias when it holds a value map, and preview's `variables` declarations **list** keeps its existing meaning ([#635](https://github.com/PersonalClaw/PersonalClaw/issues/635)).
- The Week grid's empty state no longer blames cron for an empty week: the hint still said "Only enabled interval schedules are plotted. A cron-expression trigger is not projected here yet" — pre-S103 copy from before the cron stepper landed, kept alive while cron supplies every plotted fire on a typical home (measured: 15 of 15). The false diagnosis steered users away from a working feature; the hint now names the true causes — only enabled schedules with a fire inside the viewed week are plotted (interval and cron alike), a disabled trigger has no fires, and a one-shot is not projected yet ([#686](https://github.com/PersonalClaw/PersonalClaw/issues/686)).
- Discarding a session skill draft or rejecting a skill proposal that no longer exists now returns 404 — matching the sibling skill-delete in the same module — instead of `200 {ok: false}`. No UI caller read that flag (the api wrapper only rejects on non-2xx), so a double-submit or a stale surface (the Skills page, Inbox, and dashboard Action Center all offer the same proposal) reported "Rejected"/"Forgotten" for a request that changed nothing; the existing failure paths in those callers now fire honestly. The `*` bulk clear stays an idempotent 200 with its honest `cleared` count ([#636](https://github.com/PersonalClaw/PersonalClaw/issues/636)).
- Routing notes can be cleared again: emptying the field and saving PUT an empty `content` that the backend rejected with 400 `content required` — yet an agent with no note is a supported state everywhere else (a missing metadata file reads as `""`), so a note, once set, could never be removed from the UI. An empty (or whitespace-only) PUT now clears the stored note — the file is removed so the canonical empty representation stays "absent" — and clearing an agent that has no note is an idempotent 200. The editor's silently-swallowed error half was fixed separately and already surfaces failures ([#668](https://github.com/PersonalClaw/PersonalClaw/issues/668)).
- An existing tool-output projection rule can be edited normally again: the rule editor saved the whole list on **every keystroke** and the follow-up refresh re-seeded the input from server state, so keystrokes typed during the round-trip were silently discarded (measured: one of five survived) — a regex could not be corrected at all except by delete-and-re-add. The row editor now uses the same draft-and-commit shape as the add form in the same panel: edits land in a local draft and save once when focus leaves the row (or on Enter), and a failed save keeps the draft on screen instead of discarding the typing ([#674](https://github.com/PersonalClaw/PersonalClaw/issues/674)).
- The prompt **Tags** field in the edit form can hold more than one tag again: it was a raw input whose value was re-parsed on every keystroke (`split(',')` → trim → `filter(Boolean)` → `join`), so the comma was eaten the moment it was typed and `red,green` became the single tag `redgreen` — while the placeholder instructed "comma-separated". The field is now the same `ChipInput` primitive the create form (`PromptForm`) already uses for this exact field: it drafts locally and commits a chip on comma/Enter ([#681](https://github.com/PersonalClaw/PersonalClaw/issues/681)).
- Signing in with a **correct** password from a LAN/tunnel address no longer reports "Wrong username or password." Three layers independently converted a CSRF origin rejection into a credentials error: the origin branch in the auth routes returned `auth_invalid_credentials`, the server-level CSRF middleware answered in plain text (which the login page parsed as `{}`), and the page JS defaulted every unmodelled error to the credentials message. Origin rejections now return the new Tier-S code `auth_origin_not_allowed` (mirroring `device_pair_origin_rejected`), the middleware emits the standard JSON error envelope, the login page names the rejected origin and the setting that fixes it (`PERSONALCLAW_CORS_ORIGINS` / `dashboard.url`), unknown errors fall back to "Sign-in failed (HTTP N)." instead of blaming the password, and the audit log records `login_origin_rejected` instead of `login_failed` ([#963](https://github.com/PersonalClaw/PersonalClaw/issues/963)).
- The agent edit form's **Name** field no longer silently swallows typing: the lock was enforced by discarding writes inside `onChange`, so the field looked pixel-identical to the editable Description below it — focusable, no disabled/readOnly/aria cue — and threw keystrokes away. It now uses `TextInput`'s real `disabled` affordance (dimmed, out of the tab order, announced to assistive tech, with "Names are fixed after creation" as the reason) and the field hint explains *why*: a rename would orphan every reference to the agent ([#665](https://github.com/PersonalClaw/PersonalClaw/issues/665)).
- The dashboard's Action Center and the HeroPulse "inbox" pill no longer double-count skill proposals: most pending inbox items are proposal *mirrors* (`item_kind: proposal`, `refs.skill_proposal`) of the same proposals the triage queue lists directly with Accept/Reject, so every open proposal rendered twice, "+N more to triage" over-reported ~2×, and the pill badged "31 inbox" for one real message. The Action Center now keeps the actionable proposal row and drops its mirror (a mirror whose proposal is missing from the slice stays visible — degrade, not vanish), and the pill counts non-mirror messages only ([#816](https://github.com/PersonalClaw/PersonalClaw/issues/816)).
- `POST /api/artifacts` no longer reports a larger body than it saved: the store persists a text body sliced to `MAX_CONTENT_BYTES` (1 MiB) but `create()` echoed the full un-sliced input in its 201 response, so a >1 MiB save looked successful at full size while a subsequent `GET` returned only the first MiB. `create()` now returns the persisted (capped) body — matching what `update()`/`get()` already return by re-reading from disk — so the create response and the next read agree ([#781](https://github.com/PersonalClaw/PersonalClaw/issues/781)).
- Setting the backend log level from **Settings → Agent defaults** now takes effect immediately instead of only at the next restart. That control writes `agent.log_level` through the generic config PATCH, which persisted the key but applied nothing live — so it silently diverged from **Diagnostics**, which writes the identical key AND sets every logger + rotating file handler. The apply logic is now a single shared helper (`apply_log_level`) that both surfaces use, invoked from the config PATCH path when `agent.log_level` changes ([#673](https://github.com/PersonalClaw/PersonalClaw/issues/673)).

- Artifact history now records exactly the real changes. The `update()` save/snapshot/event triad was wired by three independent conditions, producing two opposite bugs: **Snapshot** on an unmodified artifact cut a byte-identical duplicate version and logged a false `iterated` event ([#692](https://github.com/PersonalClaw/PersonalClaw/issues/692)), while a plain **Save** of an edit logged *no* event at all — the append lived inside the `snapshot` branch, so the UI's `event_type` was dropped and the edit left no history trace ([#291](https://github.com/PersonalClaw/PersonalClaw/issues/291)). The triad now keys on actual change: a snapshot whose bytes match the latest version is a no-op (no version, no event, no recency bump), and a content edit records its event (`edited`) whether or not it also cut a version.
- Context compaction no longer silently disables itself on providers that report no token usage — the normal local-model configuration, where an OpenAI-compatible endpoint rejects `stream_options` and the usage chunk never arrives. The gauge (`_last_context_pct`) stayed unmeasured, so threshold compaction could never fire and history grew without bound until the model broke. `_maybe_compact` now falls back to a conservative char-based estimate (total chars ÷ 3.0 chars/token over the model's window) purely as the compaction trigger when the gauge is unmeasured; the estimate is never displayed as a measurement and a provider-reported gauge always wins. Served-window accuracy and reactive length-rejection recovery are tracked separately ([#2364](https://github.com/PersonalClaw/PersonalClaw/issues/2364)) ([#1774](https://github.com/PersonalClaw/PersonalClaw/issues/1774)).
- Task lists now reject a duplicate name within one project, matching how projects reject duplicate project names: `create_task_list` had no uniqueness check, so a project could hold two lists of the same name — including two "General" lists, which made a `project_id`-only task attach to whichever id sorted first, arbitrarily. Creation now refuses the duplicate (per project — every project still has its own "General"), and the General auto-attach picks the OLDEST match so a project migrated with pre-existing duplicates still lands the task deterministically on the original list ([#777](https://github.com/PersonalClaw/PersonalClaw/issues/777)).

- The backend `skipped` task status now has a frontend representation: `TaskStatus` and the `STATUSES` vocabulary omitted it, so a skipped task matched no Kanban column (invisible on the board), rendered with the not-started glyph in the list, and sorted among active work. `skipped` is now in the type and `STATUSES` (its own "Skipped" label and icon), and joins the display-`TERMINAL` set (done, cancelled) so it sorts and strikes through with finished/declined work — while remaining outside the backend's dependency-terminal set (a skipped prerequisite still does not satisfy a dependency) ([#776](https://github.com/PersonalClaw/PersonalClaw/issues/776)).
- Saving a session template with `reasoning_effort: "max"` is no longer rejected: the template validator's `_VALID_EFFORTS` allowlist omitted `max`, while the composer offers it, the native runtime accepts it, and the OpenAI adapter maps it to `high` — so the one value that could not be stored as a template default was one every other layer honours. `max` is now in the allowlist ([#772](https://github.com/PersonalClaw/PersonalClaw/issues/772)).
- A tagged local-model id (`family:tag`, the normal Ollama form — `llama3.1:8b`, `qwen2.5:0.5b-instruct-q4_0`) now resolves to its FAMILY's context window instead of the 200k default: `model_context_window` split the id and kept the tail (right for a `Provider:` qualifier, wrong for `family:tag` where the family is the head), so the adaptive memory-injection budget scaled a local model's prompt to a window 1.5–6x larger than it actually has. The separator tier now tries an exact match on the head as well ([#2340](https://github.com/PersonalClaw/PersonalClaw/issues/2340)).
- A schedule the trigger store kept despite a malformed field (lenient load) now flags "needs attention" on Settings → Triggers instead of listing as a healthy-looking row: the backend already carried the parse errors as `broken`, but the `ScheduleJob` type omitted the field, `scheduleToTrigger` dropped it, and the indicator was gated to `kind === 'store'` — so a schedule that silently could not fire gave the user no signal. The field is now typed and mapped, and the indicator flags any trigger carrying parse errors ([#2342](https://github.com/PersonalClaw/PersonalClaw/issues/2342)).
- The chat approval wait no longer raises `UnboundLocalError` in its `finally` when the wait is cancelled (gateway shutdown, client disconnect, navigation away, or a CI timeout): `outcome` is now bound before the try, so the cancellation propagates unmasked and the mirrored inbox approval item is resolved instead of left asking for a decision the turn is already tearing down ([#1536](https://github.com/PersonalClaw/PersonalClaw/issues/1536)).
- Dashboard failure feedback: Action Center approve/reject/accept/dismiss now report a failed action with the server's own message instead of an empty catch that made a 409 look like a dead button ([#324](https://github.com/PersonalClaw/PersonalClaw/issues/324)); bulk task Complete/Delete reads the per-item outcomes from the 200 body and names refused items instead of reporting success ([#478](https://github.com/PersonalClaw/PersonalClaw/issues/478)); the chat workflow progress card only collapses on a real 404 — transient fetch failures keep the card and offer Try again instead of silently erasing it ([#549](https://github.com/PersonalClaw/PersonalClaw/issues/549)).
- Task/project integrity: completing a task now refuses while a BLOCKS prerequisite is still open (the DONE write enforced only the task's own exit criteria, so a kanban drag could complete a task with an unfinished prerequisite, strand a `blocked_reason_kind="auto"` stamp on the done row, and inflate graph completion — [#475](https://github.com/PersonalClaw/PersonalClaw/issues/475)); and deleting a project now cascades its tasks instead of orphaning them pointing at dead task-list ids, unreachable from every scoped view ([#457](https://github.com/PersonalClaw/PersonalClaw/issues/457)).
- Task/project integrity, create path: `create_task(status="done", …)` now runs the same completion gates as the update path instead of persisting a done row straight from a caller-supplied status — a task born with `status=done` while an exit criterion is unfinished or a BLOCKS prerequisite is non-terminal is refused (400), closing the create-side backdoor to the `can_mark_complete()==False` DONE row that [#475](https://github.com/PersonalClaw/PersonalClaw/issues/475)'s gate refuses only on update. A genuinely-complete row (no unfinished criteria, no live prerequisite) still creates, so backfilling a historically-done task is unaffected ([#2337](https://github.com/PersonalClaw/PersonalClaw/issues/2337)).
- Trigger specs are now validated at the door: `POST /api/triggers` and the chat/agent create tool refuse invalid cron expressions, 6/7-field (seconds-cadence) expressions, malformed skip dates, and unregistered action providers instead of persisting enabled rows that can never fire or dispatch; sub-900s cron cadences and skip dates the schedule never fires on are warned about at creation and reported by the trigger doctor ([#483](https://github.com/PersonalClaw/PersonalClaw/issues/483), [#687](https://github.com/PersonalClaw/PersonalClaw/issues/687), [#612](https://github.com/PersonalClaw/PersonalClaw/issues/612), [#560](https://github.com/PersonalClaw/PersonalClaw/issues/560), [#270](https://github.com/PersonalClaw/PersonalClaw/issues/270), [#779](https://github.com/PersonalClaw/PersonalClaw/issues/779)).
- The native agent loop now retries ONE pre-stream inference transient per turn (provider 5xx class, timeout) with the taxonomy's correction note and guard-shaped audit rows — a single provider blip no longer kills the chat turn ([#2287](https://github.com/PersonalClaw/PersonalClaw/issues/2287), [#252](https://github.com/PersonalClaw/PersonalClaw/issues/252)). Mid-stream failures after visible output, second failures, and non-retryable modes (open breaker, budget) propagate unchanged; max_tokens truncation never enters the retry path.
- Regenerate on a failed chat turn now works as a clean Retry of that turn's own user message instead of returning HTTP 400 (first-turn failure) or silently truncating the failed turn's message and replaying the previous question (later failures). Error bubbles are never stashed as answer variants, and the retry carries no "vary your previous answer" hint ([#254](https://github.com/PersonalClaw/PersonalClaw/issues/254)).

- **Chat:** a message containing a single token past SQLite's LIKE-pattern cap (a base64 paste, a JWT, minified JS) no longer kills the turn with a raw “LIKE or GLOB pattern too complex” error — the episodic-recall keyword fallback drops oversized tokens (they carry no recall value) and degrades to no-matches on any SQLite operational refusal instead of propagating (#369).
- **Dashboard:** a client disconnecting while a broadcast is in flight (navigating away from a streaming response) no longer logs an unretrieved-task ERROR traceback — WebSocket sends are awaited under a guard that logs at DEBUG and reaps the dead client immediately rather than on the next broadcast (#312).
- **Security (skills marketplace):** the native skills provider's `fetch()` joined an
  unvalidated skill id onto its root, so a path-shaped id (`../…`, or an absolute path —
  which *replaces* the root under `pathlib`) enumerated and returned files from arbitrary
  directories. Ids are now names only (no separators, no `..`, not absolute) and the
  resolved directory must be a direct child of the marketplace root; violations and
  missing skills raise a typed `SkillNotFoundError`, which the dashboard maps to 404 —
  marketplace detail and install no longer return 500 for a nonexistent skill (#787).
- **Prompts:** previewing a template whose `variables[]` carries an unknown `type` now
  returns 400 with the message (create already did); saving a prompt binding with a
  non-string `use_case` returns 400 instead of crashing on the frozenset membership
  test (#441).
- **Loops:** `PUT /api/loops/{id}` now runs the same numeric/boolean floor as create — a
  negative or over-cap `max_cycles`, a non-integer `idle_secs`, and a quoted-string
  boolean (`autopilot: "false"`, which the store's `bool()` coercion silently turned ON)
  are rejected with 400; the create gate gained the same explicit-boolean floor (#399).
- **Security (egress guard):** an operator allow-listed hostname that resolves (or DNS-rebinds) to a cloud metadata / link-local address (the AWS/Azure/GCP instance-credential endpoint, `fe80::/10`, Alibaba's metadata IP) is now refused post-resolution, for every policy. `deny_hosts` matches the URL's hostname before DNS, so it could not see the rebind; the homelab private-LAN opt-in is unchanged.
- Three views that show less than they were given now say so, and the numbers they state are the numbers they honour. Knowledge → **Graph** draws each entity's strongest relations and leaves the weaker ones out; measured on a 154-entity library it drew 768 of 1,540 relations beneath a header chip reading "relations 1540", and its only caption was the zoom level — the payload has carried the difference (`thinning.edges_total` / `edges_kept`) the whole time and no client read it. The canvas caption now reads "154 entities · 768 of 1,540 relations · 100%" and says where the rest is (clicking an entity lists all of its relations, uncapped). The **file content search** stopped at its 500-match cap and summarised itself as "500 matches"; it now reads "first 500 matches — narrow the search to see the rest". **Settings → Backups → Conflicts to review** showed the newest 50 of a longer queue with no total anywhere; it now states "50 of 62 conflicts — the most recent are kept." The caps themselves are unchanged (they are what keeps a canvas readable and a search fast) — the silence was the defect. A new rail reads the gateway's own route table, finds every payload that declares a truncation, and reds when one reaches a surface that says nothing ([#808](https://github.com/PersonalClaw/PersonalClaw/issues/808)).
- **The API reference no longer claims to be complete when it is not.** `docs/reference/api-overview.md` opened by stating it *"lists every route with a one-liner"*; measured through the production route census it named 248 of 689 distinct paths, leaving 441 absent with nothing to distinguish a documented route from a missing one. The hand-maintained table is deleted rather than extended — it was only ever accurate on the day it was written, which is why it drifted this far — and the page is now orientation: base URL and auth modes, request/response and error-envelope conventions, where routes are registered, and the behaviours no route name or docstring first line implies (`tools/invoke`'s effective-risk gate, `durability/import`'s validate-when-`mode`-is-omitted, write-only secret values, server-derived comment authors). The route list moved to the generated `api-routes.md`, and a ceiling test keeps the overview from growing a catalogue back.
- **Advancing an onboarding step no longer drops focus on the floor, the step you are on is a real heading, and the step body stops spending a fifth of a phone screen on alignment.** The control that had focus — the name field or its Continue arrow — lives in the step body, which unmounts as the step collapses, so focus fell to `<body>`: a keyboard user's next Tab restarted from the top of the document and a screen-reader user was told they had advanced with no way to get to where they now were (WCAG 2.4.3). The newly-active step now takes focus on its title, which is an `<h2>` — first run had exactly one `<h1>` and no `<h2>` at all, so the five steps were invisible to heading navigation on the screen whose complaint was that it is hard to navigate. One heading at a time, on the active row, because a done row's header is a `<button>` and `<h2>` inside `<button>` is invalid content; the set semantics stay on the real `<ol>`/`<li>` + `aria-current="step"`. The ring is drawn inset (`-outline-offset-2`), or the row's `overflow-hidden` clips it away and the user is moved silently. Focusing the heading also scrolls the new step into view. Separately, the step body's 4.75rem hanging indent — worth its width at 1024px and up — now applies only from `sm` up: at 390px it was 76px of a 342px card, i.e. 22% of the screen spent on alignment. Measured at 390×844, the step body's content box goes 264px → 324px, the name field 204px → 264px, and the handle hint wraps over 4 lines instead of 5.

### Added

- **Proposals can now show whether they would have helped on YOUR work.** When PersonalClaw suggests
  a new skill or template, the card in **Learning → Proposals** can now carry a second measurement
  beside the existing gate: it replays a few real turns from your own captured coding sessions twice
  — once without the candidate, once with it — and shows both scores. So instead of only "here is
  where this idea came from", the card can say "on three of your own turns this scored 4.2 before and
  1.5 after". Each replayed case links back to the exact captured turn it came from, so a claim about
  your work is checkable against the turn that produced it.
  **It is evidence, never a veto.** A "made things worse" verdict is a sentence on the card and
  nothing more — Accept stays enabled, because you may know something three replayed turns do not.
  Nothing is ever installed without you.
  **It is off until you fund it, and it never guesses a number.** Two new settings turn it on:
  *Replay Evidence On Proposals* and a *Replay Budget (USD)* ceiling. A budget of 0 means it does not
  run at all, deliberately — an unbounded background pass that spends money is the one thing this must
  never be. If the budget runs out mid-pass, the remaining proposals say "deferred on the replay
  budget" and get replayed later, rather than quietly showing nothing. And if nothing could be
  measured — no captured turns to replay, or the scoring model returned something unreadable — the
  card says "not measured" or "not replayed" and why. It never draws an unmeasured score as 0.0.

- **Automations can now read a web page.** A new **Fetch a URL** action (`net-fetch`) is selectable
  wherever an action is — a workflow's action node, a schedule or event trigger, a lifecycle hook —
  so a monitor or ingest automation can pull a page without a browser and without a script. Before
  this, fetching was something only PersonalClaw's own internals could do; there was no action you
  could put in a workflow.
  **It reaches nothing until you say so, and that is deliberate.** Automated fetches are limited to
  the hosts on your **Settings → Security → Allowed Egress Hosts** list, and that list is exclusive
  for this action rather than an exception on top of the open internet: with nothing listed, every
  fetch is refused. The refusal names the host and points at the setting, so a blocked automation is
  a two-click fix rather than a mystery. Your deny list still overrides your allow list, and the
  cloud metadata endpoints stay denied even if you list them by hand.
  Everything a page returns is treated as untrusted: the text is size-capped before it reaches a
  model (a 2 MB page cannot quietly become your biggest bill), wrapped so its contents are read as
  data and never as instructions, and stamped with where it came from. Response headers are not
  handed back at all, so a third party's cookie cannot end up in a prompt. A URL with a username or
  password in it is refused rather than sent — this action never carries credentials. Redirects are
  re-checked at every hop against the same allow-list, so an allowed host cannot bounce an
  automation somewhere else.
  It fetches only — it never posts, updates or deletes. Sending data outward is still the separate
  Webhook action.
- **"Open at login" is now a working switch in Settings, and it agrees with the menu bar.** Settings →
  Security → Desktop capabilities already listed **Open at login** — but it read "Granted" whenever the
  desktop app was running, whether or not PersonalClaw was actually registered to start at login, and
  there was no way to change it from there. The only real control was the menu bar's own checkbox.
  It is a live switch now, and both places drive the *same* registration: flip either one and the other
  follows. Previously a flip in one surface would have left the other showing a stale value until you
  restarted the app.
  macOS owns this setting, so PersonalClaw keeps no copy of it — both surfaces read it back from the OS
  every time they draw. Removing PersonalClaw from System Settings → General → Login Items shows up here
  instead of leaving a switch stuck on, and if macOS declines the change the switch says so and stays
  where the OS left it. On Windows and Linux the switch is visible but disabled, with the reason.
  In a browser tab there is no switch at all: registering a login item needs the desktop app, and a
  toggle that could not do anything would be worse than an honest absence.
- **You can put your own note in your inbox.** Everything the inbox held until now was put there by
  something else — a rule fired, a run needed an answer, a source polled a message in, an app raised
  a proposal. There was no way to add a thought of your own. The menu-bar app has offered
  **Quick Capture Note…** for a while, and it was not doing anything: it opened the Inbox and wrote
  nothing, because the endpoint behind it did not exist yet.
  It exists now, and both doors lead to it. Pick **Quick Capture Note…** from the menu bar, or press
  **Capture a note** in the Inbox header, and you get one box to type into. The first line becomes the
  note's subject in your list; everything after it is the body. Notes land as their own **Notes** kind,
  so they filter apart from messages and requests, and they stay until you handle or dismiss them like
  anything else in the queue.
  Your notes do **not** toast at you — you just wrote them, so there is nothing to tell you. They
  appear in the queue and count toward your unread badge, and if you would rather be pinged anyway,
  Settings → Notifications has a **Note you captured** row you can switch to Notify. A note is also
  never second-guessed by the verification pass: it is what you said, not a claim to check.
  A note is saved before you are told it was saved. If the write fails, the box keeps your text and
  says so rather than closing on an empty promise — and a note too long to store is refused with the
  actual length, never silently trimmed.

- **Notification rules can now deliver as real OS notifications.** The **Desktop** delivery target in
  Settings → Notifications was accepted and stored from the day the rules matrix shipped, but nothing
  acted on it — it was dimmed and labelled as needing the desktop app, and ticking it changed nothing
  even when the desktop app was running. It works now: a rule set to **Notify** with Desktop ticked
  raises a native notification whenever the desktop app is connected, and clicking it brings
  PersonalClaw forward on the surface the note came from (an inbox alert opens Inbox, a loop's progress
  opens Loops, a skill proposal opens Skills).
  It is deliberately per-kind and never global. A kind set to **Badge** or **Digest** raises nothing
  even with Desktop ticked — those modes mean "do not interrupt me", and a banner is an interruption.
  And the dashboard stays the record: a native notification is an addition, not a replacement, so
  closing the desktop app loses nothing. The rule falls back to the dashboard bell it would have used
  anyway, and the stored note says why.
  macOS never reports whether notifications are authorized, so PersonalClaw does not claim a state it
  cannot read — if you have turned them off in System Settings, ticking Desktop will do nothing, and
  that is the OS's answer rather than a broken toggle.

- **Your phone can wake you when a run is blocked on your approval — and the notification carries
  nothing but two ids.** A tool call waiting on a decision now sends a push whose entire payload is
  `{"kind": "approval", "item_id": "<id>"}`. Tap it and the companion screen opens on *that* card,
  scrolled to and focused, with the whole decision on screen: tool, full arguments, where the request
  came from, how long it has been waiting. Approve, and the run continues. If it timed out while your
  phone was in your pocket, the screen says so instead of leaving you hunting for a card that no
  longer exists.
  **No push service ever sees what is being approved.** Not the tool, not its arguments, not the
  session, not even a title — the words you read in the notification are composed on your phone from
  the `kind`, out of a fixed table. Your phone fetches the actual decision from your own gateway.
  That is the only claim that holds for both transports, since one of them is not encrypted at all.
  Two transports, in **Settings → Companion apps → Phone push**: **web push** (your browser's own
  subscription, one-time `personalclaw push init` to generate the keypair, stored per device) or
  **ntfy** (fully self-hosted — paste your topic's https URL and no third party is involved at all;
  plain http is refused, because an unencrypted ping would put the id on the wire in the clear).
  `personalclaw push test` sends one ping and prints the exact payload, so you can read the ids-only
  promise rather than take it on trust. Which notifications reach the phone is yours to set in
  **Settings → Notifications** — *Approval needed* is a row there like everything else, and it ships
  with the phone among its targets. On iOS the dashboard has to be installed to your home screen
  first; the companion screen says that rather than showing a button that cannot work. Setup is in
  [Reaching your dashboard from outside your home network](docs/guides/remote-access.md).
- **A reviewer's findings now get triaged by you before anything touches your code.** When a workflow
  review stage reports problems in the `Finding` shape it has always been asked for
  (`severity / location / problem / why / recommended_fix`), those findings are recorded against the
  run and shown in a **Review** panel on the run page — each one accept or reject, one at a time. Only
  what you accept is sent back to the worker that wrote the diff, as a follow-up instruction it picks
  up on its next iteration. Nothing is written on your behalf: reject everything and nothing is sent,
  decide nothing and nothing is sent.
  **Every finding is checked against your actual diff before you see it.** A comment pointing at a
  line that isn't in the diff, at a file the run never touched, at a filename that matches two files,
  or at a line whose contents have since changed is shown as *unverifiable* with the reason in plain
  words — and cannot be accepted at all. Applying a real critique at the wrong line is the worst thing
  this feature could do, so a finding whose anchor no longer holds is never quietly relocated. The
  check re-runs when you open the panel and again when you submit, so an accept that went stale while
  you were reading is refused rather than dispatched.
  **The findings you reject are recorded against the reviewer.** They land in the same calibration
  record a human override of a judge does, which is what makes "this gate only ever cries wolf"
  something the system can notice instead of something you have to remember.
  **`auto_fixable` is a property, not a permission.** A finding the reviewer marked as a mechanical
  edit is labelled as one, and it still has to be accepted before it can be applied — and only up to
  `Minor`. A `Critical` fix worth making is worth watching land.

- **Your watched sources now write you a morning digest, without you scheduling anything.** At 07:00
  PersonalClaw reads whatever your watched web sources collected since the last digest, writes one
  short note into your library summarising it, and sends you one notification pointing at it. One
  note and one notification per morning, however many items came in — a digest that posted per source
  would be the flood it exists to prevent. Everything was already there except the alarm clock; this
  is the alarm clock.
  **A quiet morning is silent.** No new items means no note, no notification, and no model call — so
  this costs nothing on an install with no sources, and it does not train you to ignore it by saying
  "nothing happened" every day.
  **It respects your notification settings, because it does not have its own.** The digest is
  delivered through the same gate as everything else, so muting all notifications, raising the
  minimum severity, or being inside quiet hours suppresses it. The note is still written either
  way — your library is not a notification.
  **It cannot post the same digest twice.** The window it read is only marked as read once the note
  is safely written, so a restart or a retry re-reads at most one morning rather than skipping one,
  and a second run over an already-summarised window does nothing at all.
  **Scraped pages still cannot give it instructions.** Every title and body goes into the summary
  prompt wrapped as untrusted data, and the run's only powers are writing that one note and sending
  that one notification — no tools, no shell. It shows up as `system:source-digest` under
  Automations if you want to run it by hand, retime it, or turn it off.

- **App cards now tell you whether an app is tested, styled like the rest of PersonalClaw, and
  accessible — and for our own apps, a card that claims it and isn't fails the build.** An app can
  declare a `quality` block in its manifest (`tested`, `designSystem`, `a11y`) and the Store shows it
  as badges. For a first-party app that is not a nice sentiment: CI runs the app's own tests, lints its
  frontend against the same design-token rule the dashboard is held to, and checks its accessibility
  scan against the version actually shipping — so declaring a bar it doesn't meet turns the build red
  rather than making the card look nicer. A stale scan from the previous release doesn't count.
  **An app that says nothing gets no badges.** Not a row of red marks — nothing. Saying "we haven't
  audited this yet" and saying nothing at all are different, and both are different from passing, so
  the card shows all three differently. A third-party app's badges are its author's word, which is
  why the tooltip says *declares*, not *verified*.

- **"Is this template edit actually better?" is now a question you can answer, not settle by
  taste.** When PersonalClaw proposes a change to one of your workflow templates, it now also
  registers a formal A/B study for that change — the old template and the new one, run side by
  side over your own past runs, judged blind. The study's design is fixed and hashed *before* the
  first run, so nobody (including PersonalClaw) can move the goalposts after seeing the numbers;
  editing the scoring rubric mid-study voids the study instead of quietly changing the answer.
  **Nothing spends your money without you asking.** Registering a study is free and automatic.
  Running one costs real model calls, so it is always something you invoke —
  `personalclaw study --list` shows what is registered, and `personalclaw study --run <id>
  --dry-run` tells you exactly how many calls it would take *before* it takes them.
  **A study that cannot measure anything says so.** Too few past runs to be meaningful is
  reported as low power rather than dressed up as a result, and no past runs at all is a refusal
  with an exit code — not a green tick over an empty list. Two candidate versions that turn out
  to be identical are refused outright, because that would report a confident "no difference"
  about a comparison that never happened.

- **A resumed session no longer redoes yesterday's finished work.** Picking a long task back up used
  to hand the agent whatever prose survived and leave it to *infer* how far it had got — and it
  inferred wrong, cheerfully re-running a step that had already completed. A resumed or compacted
  session now carries a short, explicit record of what actually happened, **read out of the logs the
  run already wrote** — which steps completed, which failed, which files were touched, which choices
  were already settled. Nothing in it is written by a model, so it cannot invent a completion: if a
  fact was not recorded, it is not in there.
  **A failed step stays failed.** A step that was tried and did not work is reported as failed, and a
  tool call whose result was never recorded is reported as unfinished — never quietly rounded up to
  "done". Forgetting that something finished costs one repeated step; believing something finished
  when it did not silently skips work you asked for, which is worse.
  **It is a record, not a to-do list**, so the agent reads it as background rather than as
  instructions, and it is small enough to survive compaction without crowding out what you just
  asked. Compaction now carries it through instead of folding it into the summary.
  **And if the record disagrees with your files, the resume stops.** If it says a file exists and it
  is gone — the branch changed under you, the tree was reverted — the turn refuses and names the
  file, instead of carrying on from a picture that is already wrong.
- **A big skill can no longer take the conversation.** A matched skill's body used to be pasted
  into the prompt before anything measured it, so a long skill simply took the window and the
  conversation got whatever was left — and "why did my skill not take effect?" had no answer
  anywhere. Skill bodies now compete for the prompt on the same budget as every other injected
  block, on declared priority rather than on which one was pasted first.
  **Each skill declares what it may spend.** A new optional `context_tier` in a skill's
  frontmatter — `light` (1,000 tokens), `standard` (3,000, the default) or `heavy` (8,000) — is
  that skill's own ceiling, and all the skills in one turn additionally share a 16,000-token
  aggregate. An omitted or misspelled tier is treated as `standard`, so a typo never quietly
  shrinks a skill you rely on.
  **Over its ceiling, a skill loads in a REDUCED form — never cut off mid-sentence.** What goes
  into the prompt is the skill's own one-line description and its declared `resources:` entry
  points, complete, plus the call that loads the whole thing on demand. A body sliced at a byte
  boundary is worse than a shorter complete one: nothing in the text tells the reader it is half,
  and half a procedure fails at step four. A skill that declares *neither* a description nor
  resources has nothing to reduce to, so it is refused and named rather than summarized by
  guesswork.
  **And you are told, in the turn, which skill was reduced or refused and why** — with the
  numbers: this body is 42,458 tokens, its tier allows 3,000, call `skill_invoke` for the rest.
  Three outcomes, no fourth: admitted, reduced, refused.
- **A stale browser tab now says it is stale instead of going blank.** The gateway has always
  published an API version; nothing ever checked it, so a page left open across an upgrade — or a
  cached bundle a service worker held onto — kept calling the new server with the old client's
  assumptions and broke somewhere deep, on whichever field had quietly changed shape. The dashboard
  now tells the gateway which version it was built for, the gateway compares it in one place, and a
  version it cannot speak is refused with a sentence that names both versions and which side is out
  of date: "this client was built for API version 1; this gateway speaks 2-3. Upgrade the client —
  reload the page to fetch the current build." Every accepted request also comes back stamped with
  the version it was treated as, so `curl -sD-` can answer "which contract am I on?" without
  guessing. Nothing you rely on to *recover* is gated — the page itself, its assets, the login door,
  the health probe and the manifest that publishes the version all answer as before — and a caller
  that declares no version at all (a script, a `curl`) is treated as the oldest version still
  supported rather than assumed current, so it keeps working today and is told plainly, rather than
  breaking silently, on the day support for it ends.
- **The app stops showing you an old number and then quietly changing it.** Every screen used to
  read its data through a hand-rolled cache — 124 files reached for the same helper, and a few
  surfaces kept private caches of their own beside it. A cached page painted instantly, which is
  good, but it painted with total confidence whether the value had landed forty milliseconds or
  forty minutes ago, and then swapped it for a different one a moment later. That repaint reads as a
  bug even when both numbers were once true. There is now one data layer, and a cached first paint
  is either **fresh** or **says "Updating…"** while it is being re-read. Measured on the old build:
  the Settings → Inbox card painted "30 day retention" after a reload when the server already said
  7, with nothing on screen to indicate it, and then became 7.
  **A change is reflected everywhere, immediately, without a reload.** Accept a proposal on one
  screen and the badge counting it on another updates itself; delete something in one list and a
  picker elsewhere stops offering it. Each write now declares which data it affects, so nothing has
  to be refreshed by hand and no surface is left describing something that has already changed.
  **A screen that could not load its data says so, instead of saying you have nothing.** "We
  couldn't load your items" with a Retry is a different message from "you have no items yet", and
  they are no longer interchangeable.
  **And opening a page asks the server once.** Several cards reading the same thing used to make the
  same request several times over; they now share one.
- **Your knowledge library can live as plain markdown files you own, and you can edit them.** A
  knowledge item used to be reachable only through PersonalClaw's database. Turn
  `knowledge.vault_mode` on and every item is also written out as a human-readable markdown file
  under your home — YAML front-matter carrying its identity and its relations, wikilinks you can
  follow, readable in Obsidian or `grep` or any text editor, with or without PersonalClaw running.
  **`two_way` means an edit you make in a text editor is read back, not overwritten** — which is the
  whole difference between an export and ownership. It reuses the memory vault's projector rather
  than adding a second one, so both vaults are the same artifact in two directories.
  **Nothing is silently resolved.** A page that changed in your editor *and* in the app since the
  last sync is not merged, not overwritten, and not quietly filed toward the database: nothing is
  written on either side, your text is left exactly as you typed it, `sync_conflict:` appears in the
  page's front-matter, and the Doctor reports it as a page waiting on you.
  **Deletion means deletion, in both directions.** Delete a page in your file manager and it stays
  deleted — it is not re-created on the next sync, and your item is not deleted either (a missing
  file is an ambiguous signal, not an instruction). Delete an item in the app and its file goes with
  it, leaving no stale page behind.
  **Off by default, and it cannot run away with your files.** The projection is opt-in, an
  unreadable config resolves to off, it runs in bounded batches on the existing maintenance cadence
  rather than a loop of its own, and an item too large to project is refused and reported rather
  than truncated.
- **A loop now tells you what it cost.** The loop cockpit carries a spend pill beside the elapsed
  time, reading the same per-turn ledger the Usage panel reads. It covers the loop's worker *and*
  every parallel task worker it fanned out into, so a loop that split into five workers reports one
  figure rather than a fifth of the truth.
  **What the number does not include is written next to it, not buried.** Planning is a separate
  session doing separate work, so planning spend is shown as its own amount (`~$1.25 + ~$0.4000
  planning`) instead of being folded into the run total or quietly dropped. If any model involved had
  no price row, the tooltip says the figure is a floor and the real total is higher — and a loop with
  nothing recorded says exactly that, rather than showing a confident `$0.00` that cannot tell
  free-and-local apart from not-yet-known.

- **A turn that will not fit says so before it runs, and says what to do about it.** PersonalClaw
  used to find out a prompt was too big by sending it and reading the provider's error back — after
  you had already waited. The context is now measured against the bound model's real window *before*
  the call, and exactly one of three things happens: it fits; it fits after compression, and you are
  told which block was compressed and from what size to what; or it cannot fit, and the turn is
  refused with the specific oversized block named — that tool result, that retrieved document, not a
  generic "context overflow" — plus a fix: shorten that block, run `/compact`, or switch to a model
  with a larger window.
  **Room to answer is part of the budget.** A prompt that fills the window exactly leaves nothing for
  the reply and fails the same way one that is too long does, so the bound is the window minus the
  reply reserve — the same number the model is handed as its output limit, not a second guess at it.
  **You are warned while there is still room to act.** A long session now reports its headroom as it
  tightens, instead of only once it is gone.
  **An unmeasurable window hides nothing and blocks nothing.** If neither the model catalog nor the
  window table names your model, the turn proceeds and the pressure reads as unmeasured: "we could
  not measure this" and "there is plenty of room" are different answers, and a hardcoded default
  standing in for either is a guess dressed as a fact. In the same spirit, the one silent drop that
  was already there — the session-context cap quietly shortening long history and telling only a
  server log — now tells you.
- **Ask your library a question about structure and get a traversal, not a guess.** "What links to
  this note", "what does it depend on", "what is under this tag", "what changed since Friday",
  "which of my claims contradict each other" are graph questions, and answering them by semantic
  similarity got them right only by coincidence. The agent now has a `knowledge_structural` tool
  that walks the relations your library already holds — typed links, citations, the tag tree, edit
  times, recorded contradictions — and every answer **carries the chain that reached it**, so you
  can see *why* an item came back instead of taking it on faith. Structural and semantic retrieval
  compose rather than competing: restrict to a tag subtree first, then rank what is inside it by
  meaning. And when there is genuinely nothing there, it says which fact was missing — no such
  item, no such relation, no such tag — instead of quietly handing back the closest thing it
  could find.
- **Independent lookups in one turn now run at the same time.** When the agent asked for several
  things it could have looked up simultaneously — a few greps, a couple of file reads, a repo map —
  it did them strictly one after another, and you waited for the sum. A representative
  eight-lookup turn over a 1,200-file repo took **2,219 ms on average and swung between 1.4 and
  3.6 seconds**; it now takes **942 ms and never exceeds 993 ms**. That is 2.35× faster on the
  average, and the *worst* new sample beats the *best* old one — the old spread was the serial
  accumulation showing through. Calls that could interfere still take turns: a write to a file
  waits for the reads of that file, a call needing your approval runs by itself, and anything the
  system cannot classify runs alone rather than optimistically. Results come back in the order
  they were requested, so nothing you see is reordered.
- **Long lists stay fast however long they get.** Sessions, inbox, knowledge, workflow runs and the
  diagnostics log now render only the rows on screen. On a real store of 5,000 chat sessions the
  session list was rendering all 5,000 rows and 175,688 DOM nodes: typing in its search box took
  **137 ms per keystroke**, scrolling ran at **5 fps**, and reaching the last row took **12
  seconds**. It now renders 18 rows and 1,337 nodes no matter how much you have — **13 ms** per
  keystroke, smooth scrolling, **1.3 s** to the last row, and those numbers no longer grow with
  your library. Below 64 rows nothing changes at all, so short lists behave exactly as before.
  Keyboard navigation still reaches rows that aren't rendered yet (End really does go to the last
  one), a row you had selected is still selected when you scroll back to it, screen readers still
  announce the true total rather than the visible slice, and a link straight to a row still scrolls
  to it — that last one was quietly broken in the inbox for any row deeper than about 400. One
  honest trade: browser find (Ctrl+F) only searches what's on screen, so each list now says so and
  points at its own search field, which searches everything.
- **Pair a phone or a second browser with your gateway over your home network.** Settings → Devices
  already handed you a pairing code and a link; opening that link on the other device now lands on a
  screen that actually redeems it, instead of a "token required" wall telling you to run a command in
  a terminal the joining device does not have. The code arrives pre-filled from the link, you can
  give the device a name, and it signs in. Paired devices are listed with their name, what kind of
  device they are, when they were last seen and how they got in; revoking one locks it out
  immediately, on this gateway and on disk, so it stays out across a restart.
  **The redeem screen is reachable without a session, and that is all it is.** Gating the one page a
  joining device must open behind the session it exists to create would be circular, so it is exempt
  from the token check for the same reason the sign-in page is. The page is a fixed document with no
  secret on it — the code is read out of the link by the browser and never written into the page by
  the server — and every grant still happens at the pairing endpoint, behind its origin check, its
  per-address lockout and its single-use short-lived code. A browser that is already signed in is
  sent home rather than offered the form: redeeming a code there would quietly turn your own laptop
  into a "device" and strand the session it replaced.
  **Pairing across the network needs the gateway to know its own address.** The link points at
  whatever address you are reading the dashboard on, and the gateway only accepts a pairing request
  from an address it recognises. If you reach the dashboard over your LAN, set the dashboard URL to
  that address; until you do, a device that opens the link is told so in words rather than being
  refused without explanation.
- **Ask for plainer prose without editing a prompt.** A new **Natural voice** control in the chat
  composer asks for writing that reads like a person wrote it: answer in the first sentence, no
  "Great question" opener, no summary paragraph repeating what you just read, no "let me know if
  you'd like me to" when nothing was asked, and the shortest accurate word instead of "leverage" or
  "delve". It names the patterns to avoid, because "sound natural" measurably does nothing.
  The same switch lives on an agent definition, so a preference travels with that agent into every
  chat that uses it — and a single conversation can override that agent for itself, without editing
  the agent. **It changes only how the reply reads.** Every fact, number, path, unit and caveat
  stays; a refusal stays a refusal, stated as fully and directly as before, because plainer prose is
  not softer prose. The state shows on the pill (including when an agent is what turned it on), so
  when the writing changes you can see what changed it.
- **Stop actually stops.** Pressing stop mid-turn used to acknowledge the request and then let the
  work carry on: the model request already in flight was awaited and its answer discarded, tool
  calls already queued for that turn all ran anyway, a shell command kept going, and a spawned
  subagent finished its whole task. Stop now reaches every one of those — the in-flight request is
  cancelled at the provider, remaining queued calls are dropped without executing, a running
  command's **whole process tree** is terminated and reaped (a shell's children were the ones most
  likely to survive and keep holding a lock or a file handle), and spawned subagents are stopped
  with it. A cancelled turn also keeps the tokens it spent instead of dropping them from your usage,
  and the transcript distinguishes **you stopped this** from **this was cancelled**, because those
  are different things to read a week later. Pressing stop when nothing is running remains a no-op
  rather than corrupting the next turn.
- **Know whether a model will actually run on your machine before you download it.** Every model in
  the download lists now carries a fit chip — green, yellow, red — computed from this machine's real
  memory budget: total RAM, minus a reserve held back for the OS and the inference runtime, plus a
  discrete graphics card's own VRAM where there is one. A browse filter hides models this device
  cannot run, and Settings → Models gains both the reserve and that filter's default.
  **Unified memory is counted once.** On an Apple Silicon Mac — or any machine with integrated
  graphics — the GPU's memory *is* system memory. Adding the two together reports a budget larger
  than the machine physically has, promises a fit, and then runs out of memory at load time, after
  you already waited through a multi-gigabyte download. Only a discrete card adds a second pool.
  **An unknown budget hides nothing.** If this machine's memory could not be measured, every model
  stays listed and its chip reads unknown: "we could not measure this" and "nothing fits" are
  different answers, and only one of them should take models off your screen. For the same reason a
  model family quotes its **median** variant rather than its smallest, so the chip cannot promise a
  fit you will not get from the variant you actually pick, and the download panel steps down to the
  largest variant that does fit. A download with nowhere to land is refused with both numbers named
  — what it needs and what is free — but when the filesystem cannot be measured at all, the check is
  skipped with a warning instead of blocking a download that would have been fine.
- **Put back one file, not your whole configuration.** Time Travel could already roll a whole area
  of your state back to an earlier point; now you can pick individual files out of a change and
  restore just those. Rollback and revert keep their distinct meanings per file — rolling back a file
  discards the later edits to it, reverting undoes only that one change and keeps everything after —
  and the confirmation names which files it is about to touch, because a dialog that says "roll back
  to this point" while applying to two files out of forty is describing the wrong blast radius.
  The preview stays mandatory by construction: a confirmation now has to match the exact file set it
  was shown, so a selection that changed after you previewed is refused rather than quietly applied.
  **Changes made while you were away are labelled as such.** Writes from unattended work now record
  themselves as background, so the panel's "what changed while I slept" filter has something real to
  separate from your own edits.
  **A settings change now actually reaches your history.** Saving a setting wrote the file in a way
  the history recorder never saw, so the configuration area stayed empty and "roll back my settings"
  had nothing to roll back to. Found by driving the real thing rather than trusting the tests.

- **See every device paired with your gateway, and cut one off.** Settings gains a **Devices** page:
  each paired phone, tablet or browser with its name, what kind of device it is, when it was last
  actually seen, how it got in, when it paired and when its session runs out. "Pair a device" gives
  you a one-time code and a link to open on the other device, with the expiry counting down and both
  values copyable. Revoking asks first — naming the device it is about to lock out — and the lockout
  is real: it drops the session in memory *and* on disk, so a revoked device stays out across a
  restart. A revoke that fails says so instead of quietly leaving the device connected.
  **"Last seen" is honest about not knowing.** A device that paired and never came back reads
  **never**, not the time it paired — the distinction matters precisely because you would use this
  column to decide a device is no longer in use. The timestamp is written where a device's request is
  authorised, at most once a minute per device, and it can never delay or block a login: if that
  write fails, you get a stale timestamp, never a locked-out device.
  The pairing screen shows the link and code rather than a scannable QR image, and says so where the
  image would go — the link already contains the code, so a second browser on your network needs
  nothing else.

- **Plan a task before anything runs, from the chat you are already in.** The composer's **Add** menu
  gains **Plan this first**: the chat drafts a plan, hands it to you as editable markdown, and runs
  nothing until you approve it. You can rewrite the plan by hand, comment to have it redrafted, or
  approve it and watch the work follow what you approved. It is manual only — a quick question is
  never interrupted by a planning step — and the no-execute promise is enforced by the same tool gate
  that backs `ask`/`plan` task modes, not by asking the model nicely: while a plan is awaiting review a
  mutating tool is refused, and the task-mode pill declines to drop out of `plan` until you approve or
  cancel. Turning it on mid-task parks the run in flight, keeps the whole transcript, and continues
  from your approved plan. It reuses the planning walkthrough the loops already use, so there is one
  planner in the product rather than two.

- **See everything your agents are doing at a glance.** The dashboard gains an **Agent world** — an
  ambient scene where every running loop, live chat and background subagent is a body in orbit,
  pulled toward the centre as it starts to want you: waiting on you nearest, then waiting for
  approval, then working, with idle furthest out. Loops draw their cycle progress as an arc; a run
  parked on a tool approval reads as *waiting*, not *busy*. State changes glide between orbits
  instead of jumping. Under `prefers-reduced-motion` it is a still picture, not a slow one — nothing
  orbits, nothing pulses, and the layout is identical. The scene always carries the same facts in
  plain text ("1 waiting on you, 2 working"), so nobody has to see the animation, and it falls back
  to a list where a browser blocks canvas. A failed read says the world is *unknown* rather than
  showing a calm, empty sky.
  For anyone building on it: the underlying `AgentActivityFeed` is a documented read contract
  (`docs/architecture/agent-activity-feed.md`) that folds `/api/loops`, chat sessions, subagents and
  approvals into one typed shape, refreshed by existing WebSocket envelopes **as signals only**. Apps
  will be able to contribute their own worlds against it without asking for a single permission.
- **The desktop app has a live menu-bar item, and quitting it no longer risks the gateway.**
  The macOS menu-bar menu now shows pending approvals and running loops and refreshes itself,
  with every row deep-linking into the dashboard (approvals stay clickable at zero, so the row
  is a way in rather than a control that greys out). **Open at Login** is there too — off by
  default, reversible from the same checkbox or from System Settings → General → Login Items,
  registering only this app bundle with no launch agent and no administrator password, and
  turning it on twice cannot leave two entries behind. **Quit now waits for the gateway to
  actually exit** instead of signalling it and disappearing, escalating after a grace period
  and saying so in the log if the gateway still will not stop — the old path could leave an
  orphaned gateway holding the port after a slow shutdown. If the menu-bar item cannot be
  created, closing the window closes it for real rather than hiding it, so a failed tray can
  never leave a running app with no way back to its window.
  *Quick Capture opens the Inbox for now; writing the note itself is not built yet.*
- **Undo a bad edit instead of restoring a backup.** PersonalClaw now keeps a local, continuous
  git history of the state you and the assistant actually edit — configuration and entity settings,
  `skills/`, prompts and prompt snippets, per-project context, and the memory markdown tree. A commit
  is scheduled about ten seconds after a write and tightens toward immediate under sustained editing,
  so history costs nothing and lags nothing; the memory tree is also committed hourly, which bounds
  how much memory history can ever be missing to one hour. Settings → Backups → **Time travel** shows
  the per-root timeline with a "what changed while I slept" filter, and offers two distinct verbs:
  **roll back** (go to a point in time; the changes you set aside stay listed, so you can come
  forward) and **undo just this** (reverse one change and keep later edits, failing loudly and
  changing nothing if a later edit touched the same lines). Both are preview-first and the *server*
  enforces it — a confirming request must echo the head the preview returned, so nothing destructive
  can run against a tree you did not see, and a preview that went stale is refused rather than
  applied. Secrets are excluded from the history structurally (each repository ignores everything and
  re-includes only the declared paths) and are **preserved across a rollback** — your credential
  store and `.env` are never committed and never deleted. This history is local-only: it is never
  synced, exported, or included in a snapshot. New setting `durability.time_travel` (on by default;
  needs `git` installed, and degrades to "no history" without it).

- **Apps can subscribe to platform events they declare.** An app that declares
  `permissions.eventSubscriptions` now receives `session.created`, `knowledge.ingested` and
  `task.completed` through its existing message inbox — no new route, and the install consent screen lists the
  events it will get. Delivery is **deny-by-default and exact-match**: an app that declares nothing receives
  nothing, and `task.*` or `task.completed.extra` match no event. A subscription grants **timing, not content** —
  payloads carry identifiers only, so subscribing never widens what an app can read.

- **Pair a phone or tablet as its own device.** `POST /api/devices/pair/start` mints a short-lived,
  single-use code (shown as a QR by the Devices panel); `pair/complete` redeems it into an ordinary
  owner session — no new token type — and `GET/DELETE /api/devices` lists and revokes them. A revoked
  device is locked out on its next request and stays locked across a gateway restart. Every route
  writes a security-log entry, denials included.
  **Breaking (pre-1.0):** `sessions.json` rows now carry an issuer and an optional device record
  instead of a bare expiry. Rows in the old shape are **discarded on read**, because a row with no
  issuer is a live session the device registry can neither describe nor revoke. The cost is one
  `personalclaw token` re-mint. Run `personalclaw snapshot` before upgrading if you want a way back.

- **Nudge an artifact's look without spending a message on it.** A generated card, chart or
  dashboard can now declare its own tunables, and the **Iterate** rail beside it turns them into
  real controls — a colour swatch, a slider, a switch. Drag one and the artifact restyles as you
  move, with nothing sent anywhere: no model call, no request, no turn. When you like it, **Save as
  a new version** reads what the preview is actually showing, writes those values back into the
  artifact itself and snapshots a version, so a reload comes back the way you left it and the old
  look is still one click away in the version history. Everything else in the artifact is left
  exactly as the agent wrote it.
- **Point at what is wrong instead of describing it.** Switch on **Mark elements**, click the parts
  of a rendered artifact you want changed, and type one short note each. Sending it produces a
  single message that names every element you marked, so the agent fixes all of them in one pass
  rather than guessing which "second heading" you meant. On a loop's output it goes to that loop as
  guidance; anywhere else it opens a chat and asks for the same artifact to be refreshed in place.
- **A pinned dashboard tile can now keep its own numbers up to date, for free.** Bind a tile to a
  skeleton (an artifact whose body carries `{{...}}` slots) plus the data sources that fill them,
  give it a refresh interval, and the tile re-renders itself on that cadence — same layout, new
  data, and **no model call**, because the refresh is pure substitution rather than an agent
  rewriting your panel. The tile header tells you the truth about it: how long ago it last
  refreshed, one dot per data source (hover for that source's own error), and a link to the exact
  ledger row with the cost — which reads `0 tokens` and means it. If a source fails, the tile keeps
  showing the last good content and turns its dot red instead of going blank. Tiles fire nothing
  while incident mode is on, and a tile's sources are limited to read-only ones.

- **Sync through storage you don't trust, and it still can't read your data.** Turn on
  `durability.sync_encrypt` and everything PersonalClaw sends to a shared bucket or folder is
  encrypted on this machine first — your tasks, memory and knowledge arrive as bytes the storage
  provider cannot open. What stays readable is only the routing: which machine wrote which batch,
  so a second machine can still work out what to fetch without holding your passphrase. Every
  machine that knows the passphrase reads every other's data; anyone who doesn't gets nothing, and
  a single altered byte is refused rather than quietly accepted. Backups → sync status now tells you
  plainly whether your data *is* encrypted instead of echoing the setting back at you. The default
  does the sensible thing per destination — on for cloud storage and shared folders, off for a
  private git repo, where a readable history is the reason you chose it — and you can override it
  either way. The passphrase lives in the credential store, never in a config file, so it stays out
  of exports and out of the app's own history. Two honest caveats: if encryption is switched on and
  no passphrase is stored, sync **stops** rather than sending your data in the clear; and a
  forgotten passphrase means the remote copies are unreadable — the data on this machine is
  untouched, so you start a fresh sync location rather than losing anything.
- **Mistyping your sync passphrase is now a mistake you can take back.** Get it wrong and the
  batches from your other machines are held, not thrown away: fix the passphrase and the very next
  sync merges everything it was holding. Previously a single sync with the wrong passphrase would
  have skipped those batches permanently, and correcting it afterwards would not have brought them
  back.
- **Saved the same article twice? Knowledge will now tell you, and fold the two together.** Open a
  knowledge item and the **More details** panel lists anything that looks like the same document
  again, each with the reason it was matched and how long ago it arrived, so you can judge the claim
  rather than take it on trust — and **Open** lets you read the other copy before you decide.
  **Merge into this item** keeps the one you are looking at and moves everything off the other one:
  its collections, tags, entity mentions and highlights all end up on the copy you kept, so merging
  never quietly undoes curation you did on the wrong copy. It asks first, and the question names
  which copy survives, which one gets deleted, and that it cannot be undone. If the check itself
  fails, it says so and offers to retry, instead of shrugging and showing you a clean library.
- **Hold a thought, press a key, keep your hands where they are.** In the desktop app a global
  shortcut (**⌘⇧Space** by default, and configurable in Settings → Speech & Transcription) starts
  recording your microphone from wherever you are, even with another app in front. Press it again
  and what you said is transcribed and dropped into the composer **at your cursor** — your existing
  draft is kept, not replaced. While it is listening you can see it in three places at once: macOS's
  own orange microphone dot, `● Listening` in the PersonalClaw menu-bar item, and a chip beside the
  composer. The middle one matters because the shortcut is global: if the window were hidden behind
  something, an in-window indicator would be an indicator you cannot see. Ending the recording
  releases the microphone — the app does not sit on an open mic between captures — and a capture you
  forget about stops itself after two minutes. Pick a shortcut another app already owns and Settings
  says so and keeps the one you had, rather than failing quietly until the next launch. Two honest
  limits: the shortcut **toggles** rather than working while physically held, because reading key
  releases system-wide would mean asking for a far broader permission than dictation deserves; and
  PersonalClaw records **the microphone only** — never system audio, since on macOS the only route to
  that is the screen-recording permission, and asking to record your screen in order to record sound
  is not a trade worth making quietly.
- **The design-system tool can list what it has, instead of making the assistant guess.** Asking
  the assistant to build a page used to mean it searched the UI kit by keyword — and a search needs
  a word you already know. `ui_list` just names everything: every component with its one-line
  description, or every design token. Behind it, a bundled app can now carry its own provider code
  instead of pointing at something inside PersonalClaw, so capabilities like this one grow in the
  app that owns them.
- **PersonalClaw can now notice what a project is and suggest a pack for it — and it only ever
  suggests.** Point a project at a Terraform directory and **Settings → Packs** offers the new
  **Infra Ops** pack, with the reason attached: which file patterns and which content signals
  matched, out of how many the rule declared, the example files it found, and the full list of what
  installing would put on your machine. Nothing is read by a model — it is file-shape matching, and
  it runs only when you create a project or press **Suggest packs**, never on a timer. Say "not for
  this project" once and it is remembered for that project forever. The whole thing is off if you
  turn off **Project fingerprinting**. Same panel now has the **pack store** (install any pack that
  ships with your build) and, for packs you already have, **Check for update** — which shows you
  what it would replace *and what it would leave alone* before you apply it. A pack update never
  overwrites a file you have edited: your version is kept and the skip is named on screen, with the
  reason, so a respected edit never looks like a silent one.
- **Branch a conversation from any message, and see where a branch came from.** Hovering any
  past message — your question or the assistant's answer — reveals a **Branch from here**
  button that copies the conversation up to that point into a new chat, leaving this one
  exactly as it was. Branch the same answer twice to take it two directions, or branch a
  branch; each one is a real, separate chat with its own context. The new chat carries a
  **Branched from** link back to the one it came from, which stays there after a reload and
  follows the original if you rename it. Nothing is overwritten, so there is no confirmation
  to click through.
- **A bad edit is no longer permanent.** Before the agent's first write to a file in a turn,
  PersonalClaw now saves that file's current bytes. When a turn wrecks something, `/rewind-to-turn N`
  first *shows* you what it would change — the exact files, with diffs — and only writes after you
  confirm. It restores the files and leaves the conversation alone, so the transcript still shows what
  happened (`/undo` remains the one that rolls back the chat). Credential files like `.env` are never
  copied at all, which also means a rewind will not restore one — the preview says so rather than
  quietly skipping it. Tune the store under **Settings → Chat → File checkpoints**.

- **Your phone can find this machine on its own now, if you ask it to.** Getting a companion
  device onto your gateway used to start with reading an IP address off one screen and typing it
  into another. Turn on **Settings → Companion apps → LAN discovery** and the gateway announces
  itself on your local network by name instead — the same mechanism that makes printers and
  speakers show up — so `personalclaw discover` on the other device prints it and the URL to use.
  It is **off until you turn it on**, because announcing a service on a network is a choice and
  the answer is obviously different on your own Wi-Fi than in a café. Turning it off does not cost
  you access, only typing: the address and the pairing code both work exactly as before, and
  nothing about pairing depends on discovery being on. What gets announced is four things — the
  name you chose, the port, "this will want you to pair", and a version number — and the panel
  shows you that record verbatim, because the honest way to answer "what did I just publish about
  myself" is to let you read it. There is no token in it, no session, and nothing you have said to
  the assistant. Discovery only ever says *where* this machine is; what decides *whether* a device
  gets in is still the token or the pairing code, unchanged. If your gateway is only listening on
  itself, it announces nothing at all and the panel tells you why rather than pretending the
  switch did something — a service at `127.0.0.1` means a different thing on every device that
  hears about it, so advertising one would be a lie. There is a new guide,
  [Companion apps](docs/guides/companion-apps.md), including the parts that do not work yet.
- **Two ready-made setups you can install in one go — Personal CFO and Health OS.** Each one
  arrives as a whole working thing rather than a pile of parts: the skills, the agent, a
  runnable template, a prompt, and one scheduled automation. Nothing about them starts running
  on its own. The automation lands **switched off**, waiting in Automations until you turn it
  on. The outside service each pack would like — a finance connector, a health-records
  connector — asks you whether to set it up, swap in one of your own, or skip it; skipping is a
  real answer, and anything that depended on it says plainly that it is unavailable instead of
  failing later for no visible reason. And each pack ships a short "finish setup" interview you
  run when you are ready, which asks where your statements or your journal live and remembers
  the answer; until you have answered, the pack tells you exactly which question is still open.
- **A pack can bring a team, and only the people you actually hired show up.** A pack's roster
  lists each agent with a tier. Installing it puts every persona on your machine, but one click
  deploys only the `always` tier — the rest stay parked, ready to bring in later, and nothing
  quietly turns them on. If a pack's roster points at an agent it forgot to include, the install
  stops before writing anything and names the exact missing reference, rather than installing a
  team with a hole in it.
- **Paste a prompt card and turn it into something you can actually use.** Those long "life OS"
  prompts people share can now be pasted in and converted into a real prompt, a multi-step
  template, or an agent — whichever it actually is. The pasted text is treated as data, never as
  instructions, so a card that tries to talk to the assistant gets described rather than obeyed.
  Nothing is saved until you look at the result and accept it; you see exactly what would be
  written first, and rejecting it is remembered so it does not come back.
- **Share a setup as one link.** A pack can be handed over as a single JSON file instead of a
  zip, small enough to paste. Importing one goes through exactly the same checks a pack file
  does, and every piece inside carries its own fingerprint — change one byte anywhere and the
  whole import is refused before anything is written.
- **Take your data out — all of it, or just the part you asked for.** Settings → Import/Export now
  has three buttons instead of one: everything, just your knowledge (your documents, with their
  filenames), or just what the assistant remembers about you. Credentials never travel, and neither
  do rebuildable caches like search indexes — a stale index restored next to newer data is worse than
  no index. Archives made from now on carry a checksum for every file inside them, so importing one
  tells you whether it arrived intact *before* it writes anything; older archives still import, and
  say plainly that there was nothing to verify them against. Settings → Backups gained an archive
  browser: what is in each snapshot broken down by area, whether the monthly restore rehearsal passed
  on it, and a preview-then-restore that shows you the plan first.
- **You can now edit the memory registers the assistant reads every session, and see who your
  memories are about.** Memory → Studio grew two new kinds beside Facts and Episodes: **Slots**,
  the small always-injected registers (persona, preferences, pending items, glossary, self notes,
  self model), and **Entities**, the people, projects and tools your memories name. Editing a slot
  shows its live budget, and if a line will not fit nothing is written — you are told exactly which
  of your own lines to drop instead. Removing one retires it rather than deleting it, so a later
  reflection pass cannot quietly put it back. Selecting a fact now shows which entities it links
  to, with the reason for each link, which is how you answer "why is this in my context?". Names
  that keep coming up but are not entities yet queue up for a yes/no instead of being created
  behind your back. The graph canvas can switch from records to the entity map, coloured by
  neighbourhood and filterable by link type, provenance and confidence — and **Export as HTML** in
  Memory → Health saves it as one file you can mail or archive, which opens years later with no
  server and no scripts in it. Settings gained the vault folder, a topology-orientation switch, the
  claim-attribution switch, and a budget for what slots may cost each turn.

- **See what a run actually built, in a browser.** When a code run leaves a dev server running in
  its workspace, the run's **Workspace** panel now shows a **Preview** block with an **Open
  Preview** link straight to `localhost:<port>`. Ports are matched to the run by which process is
  working inside its workspace, so one run never offers you another run's server, and the list is
  checked each time you open the panel — a server you have since stopped simply is not there. If
  nothing is listening the panel says so, and if the machine has no way to look it says *that*
  instead, because "your server isn't running" and "nothing checked" are different problems. Local
  only: this is your machine, with no tunnel and nothing shared.

### Fixed

- **Searching the skill store said "No results" when there was nothing to search.** The store installs
  skills from a catalogue, and none ships by default — so every search came back empty and told you to
  try a different search term, which blamed your query for the absence of anything to look in. It now
  says that no catalogue is set up and what to do about it, and it keeps saying "No results" in the case
  that actually means that. A failed search still reads as a failed search rather than claiming anything
  about your configuration.
  Your own skills and the bundled ones are unaffected, and the integrity badge on installed skills is
  unchanged.
- **Selecting many conversations at once could file them under a tag or folder that doesn't exist.**
  Tagging or moving one conversation checks that the tag or folder is real — moving one into a folder
  that isn't there answers "folder not found". Doing the same to a selection of them checked nothing and
  reported success. Nothing broke on screen, because the list quietly ignores a tag it doesn't recognise
  and shows an unknown folder as ungrouped, so the stored result was wrong while looking right. Both now
  refuse with a message naming what wasn't found, and refuse before anything is written, so a selection
  of forty is never half-changed. Clearing a folder by passing no folder at all still works — that isn't
  a missing folder, it's "ungrouped".

- **Two knowledge shelves could have the same name, with nothing to tell them apart.** Tags have always
  refused a duplicate name and offer to merge instead; shelves accepted one silently, so the Library rail
  could show two identical chips and only the address bar distinguished them. A duplicate name is now
  refused, naming the shelf that already has it, on both creating and renaming — and names that differ
  only by capitals or surrounding spaces count as duplicates, since a reader cannot tell those apart
  either. Re-saving a shelf under its own name, which is what happens when you change its icon, is not
  treated as a clash with itself.
- **The dashboard file explorer's "Uploads" and "PersonalClaw" roots now follow the active home
  instead of a hardcoded `~/.personalclaw`.** On a gateway running with a custom `PERSONALCLAW_HOME`,
  those two roots resolved to the developer's real `~/.personalclaw` no matter which home the gateway
  was actually using — and because the explorer's roots also define what it is allowed to WRITE, it
  could browse *and* edit the real home rather than the active one. Both roots now resolve through the
  active home (`config_dir()`), so an instance on a custom home stays inside its own tree. (#294)

- **The audit log's "Rotate" control described a key rotation it never performed.** The confirm dialog
  asked to "Rotate the audit-log signing key?" and promised that "past entries stay verifiable under the
  old key" — but the action only archives the current log to a timestamped file beside it and starts a
  fresh hash-chain: the signing key is created once and never rewritten, and the archived entries leave
  the dashboard's verify and browse views entirely, so nothing stayed "verifiable" there. The control now
  says what it does — it offers to "Archive the audit log and start a new chain", notes that the existing
  entries move to a timestamped archive and that the signing key is unchanged, and on success it names the
  archive file the log was moved to. The behaviour did not change, only the words did — and they are now
  true. (#534)

- **Regenerating a project's agent-instruction files could destroy your own notes, or write them
  to the wrong place entirely.** PersonalClaw can keep a managed block inside a project's
  `CLAUDE.md` / `AGENTS.md` / `.cursorrules`, fenced by markers so everything you write around it is
  left alone. Three faults broke that promise. The splice found its markers by plain text search, so
  a marker shown as an example inside a code block — or a second managed block — was mistaken for the
  real fence, and the content between the wrong pair was overwritten on the next regeneration. The
  block's own contents were written out verbatim, so a project name, brief, memory or document title
  that happened to contain a marker line silently closed the block early and set up the same
  corruption. And the destination directory was never checked, so a project bound to `/` or to your
  home directory would have had agent files planted at the filesystem root or straight into `$HOME`.
  All three are fixed: a marker now counts only as a whole line outside any code fence, a marker
  inside a value is escaped so it can never close the block, and an unsafe destination (a relative
  path, the home directory itself, a credential directory, or an OS/system root) is refused — both
  when the workspace is bound and again before any file is written. When the markers are genuinely
  malformed the regeneration refuses rather than guess, and every write is now atomic, so a crash
  mid-write can no longer leave a half-written file. (#358)

- **The check that keeps installed apps off PersonalClaw's internals had never actually run.** Apps are
  meant to reach core only through the published SDK, and one test enforces that. It looked for the apps
  folder in a place that does not exist — in a clone, in a git worktree, or in a development workspace
  alike — and reported itself as skipped every time, so nothing would have objected if an app had reached
  past the boundary. It now finds the apps wherever they are, checks the ones that ship inside
  PersonalClaw as well, and fails if it ever has nothing to check. Pointed at a real apps checkout it
  examines 196 files, all of which pass.
- **Choosing a project while editing a task now moves the task.** Picking a project when you created
  a task worked. Picking one while editing an existing task appeared to work, saved without
  complaint, and changed nothing — the dropdown was back to its old value the next time the task
  loaded. The two screens send exactly the same thing; only the create path knew what to do with it.
  Editing now resolves it the same way, into the project's General list, and an explicit list you
  picked yourself still wins over the project.
- **A crash no longer explains itself to you as "Server got itself in trouble."** When something broke
  on the server in a way it had not anticipated, the web framework's own diagnostic page was pasted
  into the red line under whatever field you were filling in — and, since that line is announced to
  screen readers, read out loud. It was not a sentence written for anyone; it just happened to be
  short enough and plain enough to slip past the checks that already caught proxy error pages and
  walls of markup. You now see the status instead, which is less colourful and considerably more
  true: it tells you the request reached the server and the server failed, which is the actionable
  part. Messages the backend deliberately writes are untouched, including on a server error — "could
  not read the document" still says that, rather than hiding behind a number.

- **A list-typed action setting is no longer thrown away without a word.** A trigger action's Labels
  field advertises a list — its badge says `string[]` and its placeholder is `[ … ]` — and a value
  typed into it was saved as whatever you typed, as plain text. The action that received it wanted a
  real list, did not get one, and quietly continued without it: the task it created came out with no
  labels at all, and nothing anywhere said so. Everything else in the same form saved correctly, which
  made it easy to miss entirely. Now the value is converted to the shape the action declares before
  anything is saved, and if what you typed is not a valid list the save stops and tells you which
  field, instead of succeeding and dropping it. This applies to every action setting of that kind, on
  both the trigger and the lifecycle-hook forms.
- **"Speak replies aloud" did nothing: replies were spoken whether it was on or off.** The toggle in
  Settings › Speech & Transcription saved correctly and was loaded on every synthesis — and then nothing
  looked at it, so pressing Speak on a message produced audio with text-to-speech switched off. It is
  honoured now, and refused at the endpoint rather than only hidden in the interface, so a channel or an
  app cannot speak past it either. The refusal names the switch instead of just reporting that speech is
  unavailable.

- **The "Streaming transcription" toggle is gone, because there is nothing behind it.** It offered to
  "transcribe incrementally as you speak (when supported)", saved cleanly, and had no effect: there is no
  streaming transcription anywhere in PersonalClaw for it to turn on. A switch that cannot work is worse
  than no switch, because you flip it and conclude the feature is running. Nothing else changes —
  transcription itself is untouched.

- **Editing an artifact and navigating away lost the edit.** Typing in an artifact's editor and then
  clicking anywhere else in the app discarded the change: no prompt on the way out, nothing restored on
  return. The editor already knew the text was unsaved — the Save button had enabled — and the buffer was
  dropped anyway. Unsaved artifact edits are now kept for the rest of the session, so leaving the page
  and coming back brings them with you, and saving clears them as you would expect.
  Viewing an older version of an artifact is unaffected: it shows that version, never a pending edit to
  the current one. And the recovery is held in memory only, so reloading the page still starts clean.

- **`personalclaw snapshot` left your custom themes behind.** A theme you save from Settings › Design is
  kept on the server, and the manifest that decides what a snapshot contains did not know that folder
  existed — so every theme you had authored was missing from the backup, and restoring brought back an
  instance without them. This matters more than an ordinary gap: the release notes tell you to run
  `personalclaw snapshot` before upgrading, so the one command offered as the safety net was not covering
  this. Themes are now part of it, and of an export, and two instances syncing keep both sides' themes
  rather than one replacing the other.
  **A check now walks the source and fails if a new location is added without deciding whether it
  belongs in a backup.** The nine folders fixed before this one, and this tenth, were all found by
  somebody noticing. That is what the check replaces. It also records, out loud, roughly twenty further
  locations that are still missing from backups — each needs its own decision about how two copies should
  be merged, and guessing that is worse than leaving it visible.
- **When a model's answer is cut off mid-tool-call, it is now told that, instead of being told it
  forgot something.** A response that hit its length limit part-way through calling a tool left the
  call's arguments incomplete, and PersonalClaw read them as *no arguments at all* — so the tool
  complained about a missing field, and the model was blamed for a call it had made correctly. The
  real cause was never recorded anywhere: no message, no counter, nothing in the run's history, so it
  was invisible even on review. The two failures are now told apart and named: a cut-off answer says
  it was cut off and to be more concise, and genuinely malformed arguments say that instead. Arguments
  wrapped in code fences, or sent as text containing the object, are also read correctly now rather
  than discarded — the non-conversational path already handled both.

- **Renaming a file you were editing asked permission after the fact, then stranded the tab.** The
  rename went to disk first, and only then did a dialog ask whether to discard your unsaved changes —
  so Cancel cancelled the wrong thing. It left the editor open on a name that no longer existed, and
  the next Save failed against a file that was gone, silently: no message, nothing on screen, the
  edits still in front of you with nowhere to go. Now the question comes first and Cancel means what
  it says — nothing is renamed and your edits are where you left them. It names the files at risk,
  including when you rename a whole folder with several open. And a file with no unsaved changes is
  no longer closed at all: the tab follows it to its new name, because wanting to rename something is
  not a reason to stop looking at it.
- **Workflow controls no longer answer confidently about a run that is not there.** Asking to skip or
  dismiss an approval on a run id that did not exist — a typo, or a run already deleted — came back
  reporting success, and even said the approval was still waiting. Rewinding, re-running from a step,
  or editing such a run reported that it was not running and told you to resume it first, which you
  could not do because there was nothing to resume; previewing an edit reported a server fault. All
  four now say plainly that there is no such run, which is what the other eight controls already did.
  **And a finished run can no longer be "resumed."** Answering a gate on a workflow that had already
  completed, failed, or been cancelled reported success and quietly wrote to the finished run on the
  way through. It now refuses and says the run is already complete, matching cancel, pause and steer.
- **A trigger's skip dates could be set once and never changed.** Adding a holiday when you created a
  scheduled trigger worked, and the date survived every later edit — including a cadence change, which
  goes out of its way to preserve it. But *editing* the list did nothing: the save returned success, the
  form redrew from the response and looked right, and the old list was still on disk the next time you
  loaded the page. The field was simply missing from the list of things the update endpoint would accept.
  It is accepted now, clearing the list counts as a real edit rather than as "nothing sent", and adding a
  date the trigger is already armed for moves the next run instead of letting that one last run through.
  **The switch that could not stick is gone from where it never belonged.** "Auto-approve tools" sat in a
  trigger's delivery settings, but it is a property of the *action* — only an agent action can run tools,
  so only an agent action can skip approving them. Set anywhere else it was discarded before the request
  was even sent, which is why it always read back off. Agent triggers keep the switch where it works; on
  the create page the Action block's own **Approval** field owns it, alongside the rest of that action's
  settings. A switch that reports a setting the run will not honor is worse than no switch at all,
  especially this one: it was the difference between an unattended trigger running and an unattended
  trigger waiting all night for someone to approve it.
- **Starring an inbox item now shows you something.** Favoriting worked and was saved, and then no
  surface anywhere told you which items you had starred: no star on the row, no way to filter to your
  favorites, no count. The only thing that read the flag was the label of the button you had just
  pressed, in the panel you were already looking at — so on an instance with four starred items among
  forty, nothing distinguished them. The flag was not doing nothing, which made it harder to notice:
  it fed the ranking, so it worked for the ranking and not for the person who set it. Starred items now
  carry a star in the list, and a **Favorites** filter appears with a count once you have starred
  something. The Knowledge library already did all of this for the same field; inbox now matches it,
  including the word a screen reader reads out.

- **The Doctor's "backfill missing knowledge embeddings" repair could not repair anything, and said it
  had.** It always reported *re-embedded 0 item(s)* — in every install, whatever your library held. Two
  independent faults: it handed the re-index a plain function where an embedding model was expected, so
  every item was left without a vector, and it then read a count that was never reported, so the number
  it printed was always zero. Nothing was ever corrupted, but the items stayed keyword-only and the
  message implied there had been nothing to do. Both are fixed, so the repair now drains the backlog and
  the number it reports is the number of items it actually embedded.
  **It also no longer re-embeds your whole library.** It ran over every item every time, which on a large
  library is a lot of work to redo every six hours; it now touches only the items that are missing a
  vector, which is what the repair has always been named for.
  **And a repair that achieves nothing now says so.** Reporting a clean zero also took the repair's
  six-hour cooldown, so a failure hid itself and then declined to retry. A pass that embeds nothing is
  reported as a failure and will try again; a pass that embeds some but not all keeps its progress and
  tells you how many are still waiting.

- **Two settings saved at the same moment could lose one of them.** Both ways PersonalClaw writes its
  config file read the whole file, change one thing, and write it all back. One of the two took a lock
  while it did that and the other did not, so if they overlapped, the second one to finish wrote a copy
  that had never seen the first one's change and that setting quietly reverted. The file was never
  corrupted — writing is atomic — but "atomic" only means never half-written, not that someone else's
  edit survives. Both paths now take the same lock, and only for the read-change-write itself: a request
  that is going to be rejected is still rejected immediately rather than queueing behind a save.
- **Renaming an automation replaced what it does.** Changing the name of a scheduled automation whose
  action was anything other than an agent prompt — a notification, a digest, a remediation — silently
  swapped that action for an empty agent task and saved successfully. The notification title was gone,
  the list row changed to "Invoke Agent", and there was no warning of any kind. One character of a name
  was enough.
  The cause was that the edit form could not tell what kind of action the automation had, so it assumed
  an agent prompt and sent one. It now reads the real action, says plainly that the action is left as it
  is when you save changes from there, and sends nothing that could overwrite it. Editing an agent
  prompt is unchanged, including clearing one.

- **A too-long file or folder name reported a server error instead of telling you the name was too
  long.** Typing a name past the length the filesystem allows into the explorer's New file or New folder
  field failed with a generic server error from three of the four write actions, while the fourth
  answered differently again for the same input. The name is something you typed, so a server error was
  the wrong thing to say. All four now refuse it with the limit and the length you actually used, and
  nothing is moved or created on the way to the refusal.
  The limit is counted in bytes rather than characters, because that is what filesystems count: a name of
  sixty-four emoji is short to read and over the limit to store, and counting characters would have
  accepted exactly the names that then fail.

- **When the model was unavailable, knowledge enrichment told you it had found nothing.** Every failure
  and timeout in the knowledge model pool was turned into an empty answer, and an empty answer is
  indistinguishable from a model that ran perfectly well and found nothing to say. So a model that was
  still warming up, or unreachable, or too slow, was reported to you as a clean result with nothing in
  it. Three places already knew how to say the truthful thing and none of them could be reached:
  running an intent over your library said *"No matches in your existing items"* instead of *"Couldn't
  evaluate N items — the model may still be warming up"*; an item finished as fully enriched instead of
  being flagged **Incomplete** with *"insights: model unavailable"*; and the live progress view showed
  entity extraction as done rather than failed.
  The pool now says which of the two happened, and all three surfaces report accordingly. A model that
  genuinely answers nothing is still just that — nothing is now reported as a failure that wasn't one,
  which is the distinction the whole fix rests on.
- **Anything named like a path was treated as a credential — which broke a bundled template and
  stripped native libraries out of a workflow's leaves.** PersonalClaw decides whether a name holds a
  secret by its shape, so `OPENAI_API_KEY` is hidden and `LANG` is not. One of those shape rules looked
  for `pat` — a personal access token — as a bare run of characters, which also matched every name
  containing **path**, **pattern**, **patch** or **compat**. Three things followed from that, and none
  of them said anything about credentials:
  **The bundled Project planning template could not be saved.** It declares an expected output field
  called `critical_path`, which read as a secret-named field holding a literal — and a spec with a
  literal credential in it is refused outright, on purpose. So saving that template failed with "the
  spec contains literal credentials — use {{secret:KEY}} instead", pointing at a field whose value is
  the word `array`. Any workflow of your own with a `file_path`, `output_path` or `script_path` setting
  was refused the same way.
  **A workflow's own settings came back blank when you opened it.** Values that read as secrets are
  replaced by "a value is set" before a workflow is shown to you, so a step configured with a file path
  displayed as configured-but-hidden instead of the path you typed. The agent running that workflow
  read the same redacted copy, so it could not see its own paths either.
  **And a batch's parallel branches lost their native-library search path.** A branch runs with the
  parent's environment minus its credentials, so `DYLD_LIBRARY_PATH`, `LD_LIBRARY_PATH`,
  `PKG_CONFIG_PATH` and the rest were removed as if they were tokens. A branch that needed a compiled
  extension then failed to load it and reported broken code rather than a missing setting. Plain `PATH`
  was never affected, which is why this was easy to miss.
  `pat` is now matched as a whole word, so a path stays a path. Nothing stopped being protected:
  `GITHUB_PAT`, `GH_PAT`, `PAT_GITHUB` and a plain `PAT` are all still withheld from a branch, and a
  bare `PAT` is newly recognised where before only the underscored spellings were.
- **Saying "approve it" stopped working for good once a run had answered its first gate.** Answering a
  workflow's approval without quoting a resume token — just "approve it" — works by finding the one gate
  still waiting. Gates that had already been answered were still being counted as waiting, so from the
  second approval onward the run looked like it had two open questions and refused to guess between
  them, telling you to name one by token instead. That never came back on its own: every later approval
  in that run had to be answered by token, and the more gates the run had answered, the more it
  complained about. A run whose only gate had already been answered was told its token was unknown,
  which pointed at the wrong thing entirely — the token was fine, there was simply nothing left to
  answer, and it now says that.
  Genuine ambiguity still refuses, because two gates really waiting at once is a question only you can
  settle — and it now names them, rather than just asking for a token it did not tell you where to find.
  Answered approvals are still kept on disk for the audit trail; they are simply no longer mistaken for
  open ones, and a rewind can now clear them instead of leaving them behind forever.

- **Creating a knowledge intent could silently delete one you already had.** An intent is identified by
  a name derived from the goal you type, so two goals worded almost the same — "track homelab drive
  health" and "Track homelab drive health!" — resolved to the same one. The second replaced the first
  and the app reported it as created, so the intent you lost was gone with no message, no trace, and
  nothing gathered against it any more. Saving a goal that would land on an existing intent now says so
  and names the goal already covering it, so you can edit that one or reword this one. Editing an
  intent's goal is untouched: rewording is the point of an edit.
  **Anyone writing in a non-Latin script was much worse off than that.** The derived name keeps only
  letters a-z and digits, so a goal in Japanese, Chinese, Korean, Greek, Hebrew or Cyrillic left nothing
  to derive from and every single one of them got the *same* name. In practice you could keep one
  intent, and each new one destroyed it. Those goals now get distinct names, so they behave like any
  other.

- **Merging a knowledge tag into one nested underneath it made the tag disappear from the tree.**
  Folding a parent tag into its own child is a reasonable thing to do — it means "these are the same
  thing, keep the child's name" — but it left the surviving tag pointing back at itself through its own
  parents. A tag in that state is neither top-level nor beneath one, so the tag manager could not place
  it: it fell to the bottom of the list, flat and detached from where you had filed it, and took
  everything nested under it along. The survivor now takes the place of the tag that was merged away, so
  the branch stays where you put it.

- **Knowledge search's keyword fallback never returned anything, in any install.** When the smarter
  search is unavailable — no embedding model configured, or it fails — searching your knowledge is
  meant to fall back to plain keyword matching. That fallback matched two database columns that can
  never be equal, so it found nothing, ever, and the answer was reported as a successful search with
  no results rather than as a search that could not run.
  **Fixing that alone would not have been enough**, which is worth saying because the obvious fix
  looks complete. The search text was handed to the database as an *expression*, so anything but a
  single bare word was a syntax error that the same code quietly turned back into "no results". A
  hyphen was enough: searching `cold-start` failed. Both halves are fixed, and the query now goes
  through the same quoting the rest of the knowledge store already used — so the two agree instead of
  disagreeing on every input.
  **And the last-resort search took `%` and `_` as wildcards.** Searching `a_b` matched `axb`, which
  is a result you cannot explain, on the fallback that runs when everything cleverer has failed.
  Those characters are searched for literally now.

- **A loop interrupted by a restart could stay stuck "running" forever, with nothing working on it.**
  Bringing loops back after a crash or a restart was a one-shot step during startup, and if anything
  at all went wrong in it the failure was written to the log and startup carried on. There was no
  second attempt: for the rest of that session every loop the step should have picked up sat there
  saying *running*, which reads as "still working" when in fact nothing was. **Loops are now brought
  back by the supervisor that watches them, on its first pass**, so an attempt that fails is simply
  retried on the next one a few seconds later — the same way workflow runs have always been recovered.
  A loop with a workspace that went missing while you were away is still parked with a question rather
  than restarted against a folder that is no longer there.
  **Two related fixes came with it.** Starting up no longer waits for that recovery, so a restart with
  several half-planned loops is not held behind however long it takes to resume them — the dashboard
  comes up immediately and the loops catch up on their own. And a healthy loop resting between cycles
  is no longer mistaken for a dead one: it used to be possible to restart work that was perfectly
  fine, which also silently reset the approval window you had granted it.

- **Turning a tool off on the Tools page now turns it off everywhere.** It hid the tool from the agent,
  which is what it said in the code and what it did. But PersonalClaw has a second way to run a tool,
  and that one went straight to the provider without ever reading your preferences, so a tool showing a
  "disabled" chip still ran when something invoked it directly.
  **The path that mattered is scheduled work.** A cron script reaches its tools through exactly that
  route, on purpose, so it gets the same set the agent has. It was getting the set *before* your
  preferences were applied. The realistic version of this bug is a scheduled job quietly using a tool
  you turned off weeks ago. It is refused now, with a message saying where to re-enable it.
  **The primitives cannot be locked away by this.** `bash`, file reads and the platform provider are
  never disableable, and the refusal reuses the same exemption the agent's side already had rather than
  restating it — so a stray preference row cannot leave a scheduled script unable to run anything.

- **The skill-proposal queue could fill up and never empty.** When PersonalClaw notices you doing
  something repeatedly it writes up a skill and asks you to approve it. On a long-running install those
  requests had become 89% of everything waiting for your attention, and **87% of them could not be
  approved at all** — the button returned an error, permanently, and the request stayed in the list.
  **Approving one is what broke the next.** A skill is written once and never twice, so the first
  approval for a topic claimed the name and every later suggestion about that same topic was refused
  forever. But a twenty-first suggestion about your loop workflow *is* a refinement of the skill you
  already have, whatever the request is labelled — so it is applied as one now, as a small overlay
  that leaves the original untouched and can be undone by deleting one file. Requests already stuck in
  your queue become approvable again with nothing to run.
  **PersonalClaw also stops mislabelling them.** A suggestion about a skill you already have is filed
  as a refinement in the first place, so the list says "Refine a skill" instead of "New skill
  proposed", which is the truth and is what you need to decide.
  **The approved skills were invisible.** They live in a sub-folder, and the Skills page only ever
  looked one level deep — so skills that were being loaded into every conversation did not appear
  anywhere you could read or delete them. They do now.
  **And the request that announced it never cleared.** Answering a request wrote to a copy of the
  attention list rather than the live one, so the row stayed open forever, still asking for a decision
  you had already made.
  **"Dismiss all" now means all.** It skipped anything you had opened, because opening a row marks it
  as read — so browsing your queue quietly put those rows beyond the reach of the only bulk control.
- **Editing a task can no longer corrupt it.** Saving an edit wrote whatever you sent, exactly as
  sent, with no check that it was the right sort of value — because the checks all ran when a task was
  first built and an edit never re-ran them. What that let through, each measured on a real install:
  a task given a non-numeric position vanished from every screen while its file stayed on disk,
  holding its name and unreachable through the app; a task given a single label as plain text instead
  of a list took **the whole Tasks page** down into an error screen, for every task, and stayed broken
  after the offending task was deleted; a task given a number where its notes belong made the task
  **search box fail for every search**, including searches that had nothing to do with it; and a task
  given a single completion criterion as plain text had it split into one criterion per letter, none
  of which can ever be ticked, so the task could never be marked done.
  **One place now decides what each field is**, and both creating and editing go through it — they
  used to disagree, so the same value could be accepted one way and rejected the other. A value that
  can't be used is refused with a message naming the field, instead of being stored and discovered
  later.
  **Tasks already damaged by this repair themselves.** Reading a task no longer gives up when one
  field is unusable: that field falls back to its default and the rest of the task loads, so it
  becomes visible, editable and deletable again with nothing to run and no data lost beyond the
  value that was already meaningless. The task-search box starts working again on its own.

- **Scheduled automations now actually run.** Every trigger on a clock was deciding it was due,
  writing that down, and advancing its next run time — and then doing nothing. No action ever ran. The
  dashboard showed the trigger enabled with a next-run time ticking forward the whole time, which is
  the worst possible version of this: a promise kept on screen and nowhere else. Measured on a real
  install, every run in the history had been started by hand or replayed; not one had come from a
  schedule.
  **Why nothing was visible.** A fire was being handed to a mailbox belonging to a chat session that
  nothing ever opens, so it was delivered to nobody and quietly discarded. A fire does not need a chat
  session — it needs somewhere to go — so one with no mailbox now runs directly.
  **A trigger no longer jams itself for an hour.** Each fire takes out a marker saying "this one is
  busy", released when the run finishes. A fire that never ran never released it, so the trigger sat
  showing as running for a full hour, skipped every slot in between, and refused the Run button with
  "already running". Anything that ends without running now hands the marker back — including the
  deliberate case where a trigger is skipped because you happen to be mid-conversation with it.
  **Everything downstream was alive and unreachable.** Run history, the automatic pausing of a
  repeatedly failing automation, and the notification when one fails were all already built and all
  waiting on a fire that never arrived. They start working with no further change.
  **And a trigger attached to a conversation was being delivered to one place and collected from
  another.** Two pieces of code worked out where a fire should go and only one of them accounted for
  the conversation it was bound to.

- **Four places where sending the wrong kind of value did something worse than refuse it.** Each one
  already had the right answer written a few lines away, in a sibling that handles the same field
  correctly — so each of these now does what its neighbour does.
  **Editing an agent could silently switch off its tools.** Sending a single tool name where a list
  belongs *deleted the whole field* and reported success — on the built-in agent, whose file is the
  live configuration PersonalClaw reads at startup. Its two sibling routes have always ignored a
  wrong-typed list instead of acting on it. Sending an empty list still clears it, which is how you
  say "none" on purpose.
  **A malformed message crashed the chat request instead of refusing it.** Sending a number or a list
  where the message text belongs returned a bare server error with nothing to read. It's a clear
  refusal now, naming the field.
  **Reordering a chat folder crashed on a value the identical tag route quietly ignored.** Two
  controls for the same thing, disagreeing about the same input.
  **And one out-of-range number could make your whole artifacts library unreadable.** A value too
  large to be a number serialises as something that isn't valid data, and the browser rejects the
  entire response rather than the one row — so every artifact vanished from the page until the
  offending record was found. Out-of-range values are recorded as text now, so they stay visible
  without breaking anything around them.
  **Two more things while in there:** deleting an agent through this route now consults the real list
  of protected built-in agents rather than three hard-coded filenames (it covered one of five), and
  edits to the live agent configuration are written atomically and recorded in the audit log — this
  was the one path that did neither.

- **Switching an automation off now survives a restart.** Turning one off worked, and stayed off on
  disk. Then the next start-up read the old scheduling file, which still said the automation was on,
  and put it back — enabled *and* armed, so it went straight back to counting down to its next run. A
  stop button that a restart undoes is not a stop button. Whether an automation is switched on is a
  record of something a person did, so it now travels with the rest of that history instead of being
  re-derived from a file that predates the decision.
  **The subtlety that made this two lines rather than one:** the code that carries this history
  forward skipped any value it considered empty, and in Python `False` counts as empty. So simply
  listing "switched on/off" alongside the rest would have carried *on* and quietly dropped *off* — the
  one value that needed carrying. A yes-or-no answer is never missing; `False` is an answer.

- **Asking for an automation to be created switched off now creates it switched off.** The request was
  accepted, the field was dropped, and you got a live automation already scheduled for its next run.
  It is honoured now, it is not armed while off, and asking for it in a way that isn't a plain
  yes-or-no is refused rather than guessed at — because the text `"false"` counts as *true* in the
  language this is written in, so guessing would have turned the request into its opposite. When
  PersonalClaw creates one for you and tells you about it, that message also stops claiming a
  switched-off automation is "active now".

- **Restarting PersonalClaw quietly moved a chat onto a different agent.** If you had pointed a chat
  at an external coding CLI, or put it in Ask or Plan mode, a restart threw both away and the next
  message you sent ran on the built-in agent instead — with a different set of tools and a different
  idea of what it was allowed to touch, and nothing on screen to say so. A chat you had deliberately
  put in Plan mode came back in Agent mode, free to make changes. The binding was in fact being
  written to disk when you picked it; the very next turn then overwrote the file without it.
  **A chat now keeps the agent and the mode you gave it across a restart.** And if the agent you
  chose genuinely cannot be brought back, the chat says which one it could not restore and that the
  built-in agent has different tools and different limits — once, in the turn's activity, rather than
  looking like an ordinary reply. Being moved onto another agent without being told is the part that
  actually costs you something.

- **Restarting PersonalClaw mid-conversation lost what the agent had actually done.** A chat running
  on an external coding CLI came back knowing only what was written in the transcript. Everything the
  agent had learned from *doing* the work — what a command printed, what a file turned out to
  contain — was gone, so it would ask again, or guess. **A restart now picks the conversation back up
  inside the CLI itself**, with the agent's own memory of the session intact: it can still tell you
  the output of a command it ran before the restart. Three separate things had to be fixed for this,
  and none of them was the one it looked like: the stored conversation id was being thrown away at
  every start (checked against a file nothing has ever written), the request to reopen the
  conversation was gated on the same missing file, and a chat pointed at an external CLI was, on this
  one path, quietly answered by the built-in model instead — which is why a restart used to feel like
  talking to a different agent that had read your notes.
  **And when a CLI genuinely cannot pick a conversation back up**, the chat now says *"Session
  restored from history"* rather than *"Session resumed"*. It is a smaller promise and it is the
  truthful one: the conversation is rebuilt from what was written down, so what was said survives and
  what was merely *done* does not. A line claiming a full resume it did not get is worse than no line.

- **The assistant learned nothing from turns run through an external coding CLI.** PersonalClaw keeps
  a quiet record of which tools actually work for which kind of job, and leans on it later. That
  record was only ever written by the built-in agent. Run the same work through an external CLI
  (Claude Code, Codex, kiro and friends) and **nothing was written at all** — a turn with six tool
  calls in it, three of them failing, left no trace. All the learning that comes from watching your
  own tools succeed and fail was silently switched off for anyone using an external agent, which is
  most people. Those turns are now recorded on exactly the same footing as the built-in agent's.
  **A failing tool is now reported as failing.** Underneath, the reason for the silence was that the
  "this call failed" mark was being dropped in translation, one step after it was written. Two other
  things were quietly relying on it, and both start working again: a failed tool call in the
  transcript is now coloured as failed rather than looking like a success, and the brake that stops
  the agent hammering the same broken command over and over can finally see the failures it counts.
  Before this, an external agent could fail the identical call six times in a row and get no warning
  and no stop.
- **A read-only command is no longer called "destructive", and read-only tools no longer wait on
  you.** Approval cards, the tool audit log and the "approval needed" inbox notification all print
  how risky a tool call is. For sessions running through an external coding CLI, that number was
  arriving from the wrong place. Those agents announce a tool in two steps — first "I am about to run
  a shell command", then a moment later the command itself — and the risk was being decided at step
  one, when the only thing known was "a shell command". With nothing to read, it printed the worst
  case: a plain `pwd; ls` was audited as **destructive**. In the same sessions the reverse also
  happened — the approval request for a file read arrived without the "this is a read" label the
  agent had already sent one message earlier, so **Read**, **Search** and **Fetch** never qualified
  as safe and kept raising a card even with "auto-approve read-only tools" turned on.
  **The label the agent sent is now carried across to the approval request** it belongs to, and a
  command the assistant supplies inline is read there too. So a read is labelled a read and
  auto-approves when you asked for that, and a read-only command is labelled safe.
  **A command nobody could read is now labelled "caution", not "destructive"** — a card still comes
  up, so nothing runs behind your back, but the audit trail no longer asserts something nobody
  measured. **It is still blocked in Ask and Plan mode**: honest labelling is not permission.
- **The approval card now tells you which tool it is asking about.** On sessions running through an
  external coding CLI, approval cards and the tool audit trail could read just `unknown` — you were
  being asked to approve something the card could not name, and the audit log recorded
  `unknown ｜ approved` afterwards. The name was never missing; those agents announce a tool in two
  messages and some of them put the human-readable name only in the first one, which the approval
  request did not carry over. It does now, alongside the risk label. A card still reads `unknown` in
  the one case where it is true — when the agent never named the tool in either message — because a
  name that was guessed would be worse than one that is missing.
- **Ask mode no longer refuses a read-only `ls`.** Ask and Plan mode allow inspection and block
  changes, but they decide from the command text — and when that text was attached to the approval
  request rather than to the earlier announcement, it was being dropped, leaving the gate to guess
  from the tool's display name. A name like "Running: ls -la" reads as an action, so a plain
  directory listing was refused in a mode that exists to let you look around. The command is now
  read wherever the agent put it.
- **The session line said "Session created" on every single turn, and named the wrong runtime.** The
  activity line on the Loop and Code cockpits — `Session created · <agent> · <model> · via <runtime>`
  — got both halves wrong. It announced a *creation* on turn forty of the same conversation, so the
  one signal that would have told you an agent had lost its history and started over was the same
  sentence you saw when nothing had happened at all. And the runtime it named was the little
  protocol adapter PersonalClaw launches (`acp:claude-agent-acp`), not the CLI you actually chose
  (`acp:claude-code`) — or, when the adapter had to be fetched on demand, the fetching tool itself:
  `via acp:npx`, which names nothing you have ever heard of.
  **The line now says what happened.** A session that was genuinely started says **created**; one
  restored from a saved conversation says **resumed**; a turn served by the session already running
  says **continued** — a state the line previously had no word for and so reported as a creation.
  **And it names the runtime you picked**, on every turn, however that runtime was launched. Which
  matters beyond the label: the same misread name was also the key PersonalClaw used to look up a
  backend's known permission-gating limitations and to stamp its audit records, so under an
  on-demand adapter fetch a *documented* limitation could read as an unexplained gap.
- **The assistant learned "never more" as a permanent rule.** Ask it to answer in one sentence "from
  now on, never more" and it wrote itself a standing rule reading `Never: never more`, showed you
  `Learned: never more`, and kept the row forever. The word "never" was enough on its own: whatever
  followed it up to the next full stop became the prohibition, so an intensifier ("never more"), an
  idiom ("never mind the tests"), and a proverb ("better late than never") all read as hard rules.
  This was the loosest thing the assistant learned and also the most permanent one, because a "never"
  rule is stored as a lesson — the place standing always/never rules live — while an ordinary style
  preference is stored as a preference that fades unless you repeat it.
  **A "never" is now only learned when it prohibits an action** — the words after it have to name
  something to not do. Real rules are unaffected: "never force-push to main", "don't ever delete my
  notes", "never commit secrets to the repo" are all still learned, and a genuine rule stated after a
  false one in the same message is now picked up where before the fragment won and stopped the search.
  Quantity phrasings like "never more than one sentence" are deliberately not treated as
  prohibitions; say it as a preference ("keep responses short") and it is learned as one.
- **A reasoning-effort setting that the coding CLI cannot honor is now refused instead of
  silently stored.** Some external coding CLIs report that they have no reasoning-effort
  control at all, and the composer already hides the pill for those. The API did not agree:
  it accepted an effort anyway, saved it onto the session and read it back afterwards, so the
  setting looked applied when nothing would ever act on it. Both places that set it now check
  what the runtime actually declared and refuse anything outside it, naming the runtime and
  the options it does offer. The opposite case is fixed too — a CLI offering its own value
  (say `xhigh`) had it rejected, because the check compared against a fixed
  low/medium/high/max list instead of the CLI's own. Clearing the setting is always allowed.
- **A reasoning-effort setting no longer quietly lapses partway through long-running work.**
  Some external coding CLIs treat a session as finished after one turn, so for long jobs the app
  opens a fresh session per cycle. It re-applied the agent, the model and the permission mode on each
  one but not the reasoning effort — so from the second cycle onward the work continued at the CLI's
  default effort while everything on screen still said otherwise. It is re-applied now. The same
  path also skipped draining the tool-server startup messages before beginning the turn, which is now
  done too.
- **A tool blocked by Ask or Plan mode no longer ends the whole conversation.** When one of these
  modes refused a tool on an external coding CLI, the turn could stop dead — the assistant's reply
  became "*Conversation interrupted*", the model never learned its tool had been refused, and you
  never got the short "(Ask mode — only read-only tools run)" explanation. The cause was the message
  sent back: refusing a tool reported *the whole turn was cancelled* rather than *this one tool was
  declined*, so the CLI reasonably stopped. A refusal now says exactly that, using the CLI's own
  "reject" choice, so the assistant can acknowledge it and carry on — which is already how it behaves
  when you press Reject on an approval card yourself.

- **The context gauge said "0%" on turns that were nearly full.** The little ring on the model pill,
  and the "Turn complete" line under a finished turn, both printed a context percentage on every
  turn — including turns where the agent behind the chat had never reported one. The number they
  printed was zero, so a session carrying a large amount of context read as completely empty. A
  driven session showed `context 0%` on fourteen consecutive turns while the window was filling up.
  A gauge that states a number it was never given is worse than no gauge: it invites you to keep
  going right up to the point where the conversation gets truncated.
  **An unmeasured context now shows nothing at all** — no ring, no percentage — rather than zero.
  **A genuinely empty context still shows 0%**, because that is a real answer and hiding it would be
  the same mistake pointed the other way. This also fixes two decisions that were reading the fake
  zero: the background session was recycled as "no readings ever arrived" when it had in fact
  measured an empty window, and the automatic-compaction check no longer treats an unknown gauge as
  a low one.
- **Typing `/compact` at a coding-CLI agent killed the whole turn.** Any message starting with a
  slash was sent as a *command* to whichever agent was bound, without ever asking whether that agent
  understands commands. None of the three CLIs we drive does, so the answer came back "Method not
  found" and the turn died on an error card — you got no reply at all, and `/compact` compacted
  nothing. A slash command now goes out as a command only to an agent that says it can run one;
  otherwise your message is answered as an ordinary question and an inline line tells you the
  command was not run natively, so you are never handed a plain answer while believing a command
  executed. If a command does fail as unknown *after* the agent has already started replying, the
  turn stops and says why rather than silently starting a second one — re-asking would duplicate the
  reply you can already see and bill the work twice. Any other command failure still surfaces as the
  failure it is.
- **The sign-in page said "Sign-in failed" no matter what went wrong.** It read the error out of the
  wrong place in the response, so a wrong password, a rate-limited address, a password-sign-in that
  is switched off and an already-used device code all produced the same unhelpful sentence — and the
  field for a two-factor code could never appear when the server asked for one, because the branch
  that reveals it never ran. Each failure now says what it is.
- **The audit log's "Failed" filter hid most failures.** Settings → Audit log offers outcome filter
  pills, and they were defined in the dashboard as two literal words — `denied` and `failed` — while
  the code that writes the log uses sixty-two different outcome words. So "Failed" matched only the
  four places that happen to write `failed`, and missed every `failure` and every `error`; "Denied"
  missed `rejected`, `blocked` and `refused`. On a tamper-evident record of what your agent did, a
  filter that quietly leaves matching entries out is the worst possible failure — you read the empty
  list as "nothing went wrong". Found by driving the panel: a real terminal-session delete that had
  recorded `error` was invisible behind the Failed pill. The pills now cover whole families of
  outcomes, and the families are defined next to the log itself rather than in the dashboard, so a
  new outcome word reaches the filter without anyone remembering to update the UI. Each pill names
  the outcomes it covers on hover.

- **Setting a chat's working directory with a mistyped field no longer silently unbinds it.**
  `POST /api/chat/sessions/{id}/workspace-dir` read a missing `workspace_dir` key as "clear it", so a
  body like `{"dir": "/some/path"}` answered `{"ok": true, "workspace_dir": ""}` and left the session
  with no working directory at all — while the caller had every reason to think it had just set one.
  For a chat bound to an external agent CLI that binding decides where the CLI actually runs, so the
  agent would go on to read and write in whatever directory the host resolved instead. The request is
  now refused with a 400 that names the one deliberate way to unset it (an explicit empty string,
  which still works). Found by driving a real `acp:kiro-cli` session, not by reading the code.
- **Settings → Prompts named four of its forty-four rows.** The panel lists every bindable runtime
  context — the prompt that serves chat, the one that writes a conversation title, the one that turns
  "every Tuesday at 9" into a cron expression, each loop judge and planning brief. Four of them had a
  human name and a description; the rest showed their internal key (`nl_to_cron`,
  `history_compression`, `cycle_judge_skeptic`) with no explanation of what binding it, and a screen
  reader announced the picker as "Prompt for nl_to_cron". They also arrived as one undifferentiated
  list of forty-four. Every context now names and describes itself, and the rows are grouped — agent
  system prompts, internal task prompts, loop and orchestration prompts, evaluation prompts — using
  the grouping the bundled-prompt catalog had already declared for this exact purpose and never sent
  to the dashboard. The description comes from the prompt catalog itself rather than a table in the
  dashboard, so a context added later (including one contributed by an installed app, which is where
  four of these rows come from) arrives already described instead of appearing as a bare key.

- **A scheduler tick wrote its history into the wrong PersonalClaw home.** Two writers on the tick
  path — the suppressed-fire ledger row and the hourly rate meter that reads it back — built their
  run store from the *active* home instead of the home the tick was actually running under. Anything
  that drove a tick against an isolated home (an ad-hoc script, a dev drive, a diagnostic) therefore
  appended its rows to the real `~/.personalclaw/cron-history/`, and then measured its rate caps
  against that same foreign history — so a cap could be satisfied, or exhausted, by fires belonging
  to a different install. Both now resolve through one funnel rooted at the tick's own home, matching
  the rule already documented for trigger claims: a record describing one store must not live in
  another. No behaviour change for the gateway, the CLI or the dashboard, where the two roots were
  always the same directory. If you have driven ticks outside the normal gateway, your real
  `cron-history/` may hold stray rows for job ids you do not recognise; they are inert bookkeeping
  and safe to delete once you have checked them.
- **An agent CLI could run in your home directory instead of the folder you gave the chat.** Picking
  an agent for a chat used to run its CLI in a hardcoded `~/.personalclaw/workspace` rather than the
  working directory bound to that session — so files landed outside the folder you chose, and a
  gateway or test started with `PERSONALCLAW_HOME` pointing elsewhere still wrote into the real home.
  The session's working directory now reaches the process, agent discovery uses the configured
  workspace, and an agent that declares no default directory of its own no longer relocates a chat
  you had already pointed somewhere. An agent that *does* declare one still opens there, unchanged.
- **A "Pre tool use" hook that blocks nothing now says so.** Only a lifecycle hook an agent's
  trigger list references can reject a tool; an unreferenced one still runs, but its exit code is
  read by nobody. Both looked identical on the Triggers page — and the unreferenced one was the more
  convincing of the two, because it fired on every tool call and its run count climbed. Measured: a
  hook exiting 2 to deny a file write fired three times while all three writes landed. A blocking
  hook now carries **Not enforcing** or **Enforcing** wherever it is listed, the run count on an
  unarmed one is labelled advisory, and the reply to creating one says which state it starts in.
  Hooks on the other lifecycle events are unchanged: they have nothing to arm, so they are not
  badged. Nothing about when a hook blocks changed — only whether you can tell.
- **A chat with no folder set could run its agent CLI wherever the app itself happened to be
  started.** If no workspace directory resolved — none configured, or the configured one turned out
  to be missing or a credential folder that is never usable as a workspace — a chat you had not
  pointed anywhere fell back to the app's own current directory, which on a service install is
  whatever the system launcher chose. Files the agent wrote landed somewhere you had no way to find.
  Such a session is now refused before the CLI starts, with a message naming what to set and where.
  A chat that already has a working directory, and one whose workspace resolves normally, are
  unaffected.
- **Tinted "chip" buttons had unreadable labels in six of the twelve colour schemes.** The label
  colour on a tonal button (the primary-tinted CTA — "Open Chat" on the dashboard is one) was a fixed
  coral, but the tint underneath it is painted from whichever scheme you picked. So the label's
  contrast depended on a scheme it knew nothing about: in dark mode Mono, Amber, Phosphor, Jade,
  Honey and Forest all fell below WCAG AA at rest, worst 4.07:1 against a 4.5 floor, and once the
  hover tint is counted the default coral scheme missed it too. The label now takes the active
  scheme's own accent shade instead of a frozen value, so it follows the scheme — including a custom
  primary you pick yourself. **Visible change:** tonal button labels are a shade lighter in dark mode
  and a shade deeper in light mode, in every scheme including the default. A new rail composites the
  translucent pair over all 12 schemes × 2 modes × 3 surfaces × {rest, hover} and fails the build on
  any combination below AA, which is what was missing — the old guard only checked the opaque
  filled-button pair.

- **"Unattended runs need a verified adapter" only covered one kind of unattended run.** The switch
  promised to refuse background work onto an external agent CLI whose ACP adapter has no verified
  provenance, and it did — for a subagent. A cron fire, a loop-cycle worker, the background session,
  an inbox or side sweep, a channel delivery and a trigger dispatch all went through unchecked,
  because each one had to volunteer that it was unattended and only one of them did. Whether a spawn
  is unattended is now decided from the session itself, using the same rule the safety profiles
  already use, so all of them are covered without opting in. Interactive chat is still never gated.
  If you have this switch on and an unverified adapter, background runs that used to launch will now
  be refused by name — that is the switch doing what it said.

- **Settings → Agents could show you a stale runner reading as if it were current.** A runner row
  printed "healthy, v2.1.4, 58 ms" with no indication of when that was measured, so a check from last
  week looked exactly like one from a minute ago. Rows whose measurement is older than the new
  **Runner health check interval** (Settings → Agent defaults, default one hour) now say **check
  overdue** next to the reading instead of quietly presenting it as the present state. A runner that
  was never probed still says so — it is not reported as overdue.
- **A provider that rides a CLI subscription could look signed in and still fail.** For a model
  provider whose vendor bills by subscription — no API key to paste, just an agent CLI you already
  signed into — one of the two ways such an app gets built never looked for that sign-in, so the
  provider came up holding a placeholder secret and every request failed as unauthorized with your
  login sitting right there. Both paths now resolve the credential the same way, from the same code.
  **Test connection** can see the sign-in too: a signed-in provider is genuinely probed, and a
  signed-out one is told to sign in — where it used to print the truncated "No API key configured
  (set it or )", naming an environment variable a subscription app deliberately does not have.
  Finally, restoring a conversation no longer quietly swaps your pinned model: a provider app that
  serves a model family is now recognized as serving it instead of only the providers built into
  PersonalClaw.
- **"Possible duplicates" only looked at your 25 newest items.** The panel that tells you a second
  copy of a knowledge item exists compared it against a handful of the most recently added items of
  the same kind — so the case it exists for, an old copy and a new copy of the same document with a
  grown library in between, was the case it could not see. It said nothing, which looks exactly like
  having no duplicates. It now checks every item in the library: titles are compared first (cheap),
  and the expensive content comparison runs only on what a title match survives — on a
  2,000-item library the whole check takes about 15 ms and finds the duplicate, where the old
  25-item version took 6 ms and found nothing. Candidates are also listed strongest match first, and each one now says
  how similar it actually is ("Same title · content similarity 0.99") instead of repeating one fixed
  phrase for every match, weak or near-identical.
- **An export could carry the same database twice, and one copy was the unsafe one.** Databases
  that live inside your workspace (the knowledge and lexicon stores) were written into a portable
  export twice: once as a proper checkpointed backup, and again as a plain file copy taken while the
  store was open. The plain copy landed second, so unzipping overwrote the good copy with it, and the
  `-wal`/`-shm` working files travelled alongside. Worst case, when a database was too damaged to
  back up safely, the export said it was skipping that store and then shipped the raw copy anyway,
  listing it in the archive's manifest as if it had been verified. Each database now leaves through
  the safe backup path or not at all, and an archive's manifest again matches what is inside it. A
  database you put in your own workspace yourself still travels as before.

- **Your ready-task list was in no particular order.** The list behind **Tasks → Ready** (and the
  same list an agent reads when it asks what to work on next) came back in whatever order the store
  happened to return — most-recently-updated first — so a task due today sat below one due next
  month, and a task blocking three others sat below one blocking none. A due date you had set was
  read by nothing at all. Ready work is now ranked the way you would expect: priority first, then
  how many other tasks it unblocks, with an extra bump once it is overdue, and ties broken
  consistently so the order is stable between refreshes. Nothing about how tasks are stored
  changed, so no existing task moves except in where it appears in the list.

- **Forking a conversation could cut it earlier than the message you clicked.** In a chat with
  tool calls or multi-part answers, the fork was measured by the position of the bubble on
  screen rather than the message in the transcript, and those drift apart — so you got a
  plausible-looking copy that quietly stopped short, sometimes before the answer you were
  forking from. It now cuts exactly where you clicked.

- **A "replace everything" restore could run while the app was running.** Over the web API it was
  supposed to refuse and, on any port other than the default, it silently didn't — it checked whether
  something was listening on the port it had been *configured* with rather than the one actually in
  use. It now refuses outright, because the request being served is proof the app is up. Your previous
  state was never lost either way: a replace moves what it displaces aside before writing.

- **Buttons inside a generated widget now work everywhere you can see the widget.** A widget the
  agent builds can carry real controls, and clicking one used to only do something while you were
  looking at it inside a conversation — the same button in an artifact's preview or on a dashboard
  tile quietly did nothing. Now it opens a chat and your click arrives as the first thing you said,
  form fields and all; pressed inside a conversation it still answers in that conversation rather
  than starting a new one. A widget still cannot press its own buttons — only a real click counts —
  and it cannot pad your message either: anything past 16 KB is cut with a visible …truncated so you
  can see it was shortened.
- **Home, the Inbox and Discover now arrive in sequence instead of all at once.** Their sections
  fade and rise in one after another, about 44ms apart, so a page reads as composed rather than as
  eight widgets landing in the same frame. It costs you nothing: everything is on screen and
  clickable from the first frame — the movement only decorates the arrival, it never holds content
  back. How much cascade there is follows the Expressiveness slider in Settings › Design (calmer is
  tighter, not dead), and if your system asks for reduced motion there is no cascade at all — not a
  faster one. It also plays once, when you arrive at a page, so a refresh, a new inbox item or a
  filter change never makes the page flicker through it again.
- **A guided tour of the app, and you can take it again whenever you like.** The last screen of
  setup now offers a quick walk through the five places that matter: the sidebar, chat, the Inbox,
  where anything risky waits for your permission, and Settings. It happens on the real app — each
  stop dims the page and puts a ring around the actual thing it is talking about, then takes you to
  the next one — so you are being shown your own dashboard, not a slideshow of screenshots. Escape
  ends it at any point and so does clicking anywhere outside the card, and what you are left with is
  the app, fully working, back on the page you started from. Nothing about it is recorded: there is
  no progress saved, no "you have seen this" flag, and no step reports anything anywhere, which is
  also why it can simply be replayed instead of resumed. To take it again, open Discover — there is
  a "Replay the tour" card at the top that cannot be dismissed away, so it is still there after you
  have dismissed every tip on the page or turned tips off entirely. If your system asks for reduced
  motion the spotlight stops pulsing and the card stops sliding; nothing is slowed down, it is just
  still.
- **Empty pages now explain themselves and give you something to press.** Every one of the seven
  main surfaces — Loops, Workflows, Knowledge, Memory, Skills, Tasks and Triggers — says what the
  thing it holds actually is when you have none of it, and offers the one obvious next step
  instead of a blank column. Workflows was the worst of them: two empty screens that told you to
  go find a control somewhere else. Now "No workflow runs yet" hands you a button that takes you
  to the definitions, and an empty definitions list starts you from a template. The Memory panel
  had no real empty states at all — just a few grey sentences — and now matches the rest of the
  app, with a way to add your first fact right there. A page you have merely filtered down to
  nothing keeps the explanation but drops the create button, because offering to make your first
  task when you already have ninety is just noise.
- **The audit log can now answer "what did my agent actually do?" — and tell you which record was
  tampered with.** Settings → Audit log used to hand you the last 200 events and filter them in
  your browser, so anything older was simply unreachable. It now pages properly: you get the most
  recent events, a "Load older events" button walks back through the whole log, and the filters
  (operation, caller, downstream service, and a from/to date range) run on the server, so they
  search everything rather than only what happened to be on screen. Paging is stable while your
  agent keeps working — new events landing mid-scroll can no longer make a page repeat rows or,
  worse, quietly skip them. Each row now carries its own tamper check: if a record was altered on
  disk after it was written, that row is called out on its own and counted in a banner at the top,
  and "Verify" tells you how many of how many events still check out. Nothing here leaks
  credentials — a token that appeared in a command shows as `[REDACTED: credential]` while the
  rest of the command stays readable, so an entry is still useful for working out what happened.
  Finally, "Export" downloads exactly what you are looking at as a `.jsonl` file, one event per
  line, safe to attach to a bug report and readable by anything that speaks JSON. Reading this log
  is yours alone: an installed app can never fetch it, even one that asks for it.
- **Artifacts are now findable from knowledge search.** Anything written into a text artifact —
  markdown, HTML, plain text, JSON, CSV — becomes searchable in Knowledge, so an answer you or the
  agent wrote into an artifact last month turns up in the one place you look for what you know.
  Artifacts stay in the Artifacts library and are **never listed as knowledge items**: they only
  appear as search results, labelled "Artifact" with a link straight back to the real thing, so your
  library's counts and lists do not double. Editing an artifact refreshes what search finds, renaming
  it refreshes the title, and deleting it removes it from search entirely — no leftovers. Indexing is
  **local**: a mirrored artifact never reaches a model, and any credential in an artifact's body is
  stripped before it is indexed. If you already have artifacts, they are indexed once the first time
  this runs and never re-indexed on later restarts. Widgets, React and SVG artifacts are left out on
  purpose — their bodies are code, and indexing them would bury your notes under variable names. The
  whole thing is one switch in **Settings → Sources → Artifacts** (on by default) and takes effect
  without a restart.
- **First run now picks up where you left it, and you can walk out of it at any point.** Reload
  the page halfway through setup and you come back to the step you were on rather than the
  beginning — the apps you installed stay installed, and a card you already ran still counts as
  your first success. You do have to type your name again, because your name is only saved at the
  end and inventing one for you would be worse. Every step also has a way out now: one link under
  the stepper leaves setup and drops you straight into a working dashboard, and if you leave
  before telling us your name it says which name it will use so nothing is renamed behind your
  back. Everything you skipped is still in Settings, and skipping never takes a feature away.
  The last screen finishes the job: it recaps what actually happened, then hands you the three
  things worth knowing on day one — where work comes back to you (with a link straight to the
  Inbox instead of the dashboard), the live dial that decides how much the interface moves, and a
  switch that opens the whole sidebar from the start if a short one is not for you. Those are the
  real Settings controls, not previews, so moving them here sticks. And `personalclaw setup` now
  says where the guided setup lives — one line, and only when you are somewhere a browser can
  actually open it.
- **Moving between pages now crossfades instead of cutting.** Clicking a nav item — or going
  back and forward in your browser — fades the old page into the new one on the same curve the
  rest of the app moves on, so navigating feels continuous rather than like a hard cut. It is
  purely cosmetic and deliberately cannot get in the way: the address bar and the page itself
  change immediately whether the animation runs, fails, or is not supported by your browser at
  all, so nothing is ever waiting on a fade. If your system asks for reduced motion there is no
  fade at all, just the instant swap. Opening a detail panel, switching a tab, typing in a search
  box and following a redirect all stay instant on purpose — those are refinements, not
  navigation, and fading the page under them would fight what you were doing.
- **The two shipped personalities now arrive with their own motion and their own tone.** Picking
  **Claw Arcade** in Settings → Design also sets the backdrop to sparkle dots on an offset lattice
  and turns the motion language up to its bold end; **Retro Terminal** does the opposite on the same
  dials — square dots on a straight grid, and springs flattened to no overshoot at all. If you have
  sound cues switched on, a finished turn in Claw Arcade is an arcade coin and an approval in Retro
  Terminal is a terminal bell; the panel tells you which moments an identity re-voices, so a
  different tone is never a mystery. Everything here stays **yours to change**: each dial lands in
  your own Appearance settings, so moving a slider afterwards sticks, and switching back to
  PersonalClaw puts every dial back to its default rather than leaving one pinned. Sound is still
  off until you ask for it, still silent in a background tab and under Reduce Motion, and the
  sparkle backdrop is a single still frame — never a moving one — when your system asks for reduced
  motion.
- **You can show the assistant your screen for one message — off until you turn it on.** Settings
  → Chat has a new **Share screen in chat** switch. It ships **off**, and while it is off there is
  no control in the composer *and* the server refuses a frame outright, so nothing can start
  sharing your screen by asking. Switch it on and the composer grows a **Share screen** button: your
  browser's own dialog picks the screen or window, your browser's own indicator stays lit the whole
  time, and PersonalClaw adds a pulsing **Sharing screen** chip in the chat header so you can also
  see which conversation is being shown frames. One frame is captured at the moment you send a
  message — not a stream — it is held in memory for that single turn, never written to disk, and
  dropped as soon as it is used; sending a second message replaces it rather than piling up. If the
  model you are talking to cannot read images, a vision model describes the frame instead and the
  turn says so, so you always know whether it saw pixels or a description. Stopping the share (the
  chip, your browser's stop button, or closing the tab) clears everything immediately. If you want
  to keep a frame, "+" → **Pin shared frame** saves it as an ordinary attachment — the only way one
  ever reaches your disk, and it is refused in a temporary or incognito chat.
- **You can snip a region of your screen into a message, on any platform.** "+" → **Capture
  screen area** now works everywhere, not just on a Mac. This is a different thing from **Share
  screen** above: sharing shows the assistant a live screen for one turn and keeps nothing, while a
  snip produces an ordinary PNG attachment you can see, remove and send — the same chip a
  dragged-in file gets, read the same way. On macOS it still uses the system snipping tool, which is
  the better crosshair; everywhere else your browser asks which screen or window to capture, exactly
  **one frame** is taken, and the capture is **stopped before you crop** — so nothing keeps
  watching your screen while you decide, and your browser's own indicator goes out immediately. Then
  you crop it in the app: drag a region, or use the arrow keys (hold Alt to resize) with the whole
  capture selected to begin with, so there is nothing here you can only do with a mouse. It tells you
  the exact pixel size you are about to attach. Escape cancels and leaves nothing behind — no
  attachment, no file on disk. Where a browser cannot capture a screen at all (iOS Safari) the menu
  item is simply not there, rather than there and broken. One fix rides along: a capture taken with
  the macOS tool is now actually **read** on send. Its chip has always been visible, but what was in
  it never reached the assistant — it does now, the same as any other attachment.
- **Optional sound cues, off until you turn them on.** Settings → Design → Personality has a new
  switch that gives you a brief tone when a turn finishes, when a tool approval needs you, and when
  something fails — nothing else. It ships **off**, and even switched on it stays quiet while the tab
  is in the background or while your system asks for reduced motion. The tones are generated in the
  browser, so no audio file is downloaded and none ships in the app.
- **First run now ends with three things you can actually do, not a tour.** A new **Try one** step
  saves a note and asks your own library a question, sets a real 9:00 AM reminder and fires it once
  so you see what it will say, and starts a real one-cycle loop — each showing what actually
  happened rather than a preview, each finished in about a second, and none of them spending a
  single token. Skip any or all of them. If a call fails, the card shows the exact error the server
  gave and offers a one-click jump to the Settings panel that owns it.
- **A knowledge item now has a reading mode, and a passage you highlight in it stays
  highlighted.** Open anything with a text body from Knowledge and press **Reading mode**: the
  article gets the column, real editorial type instead of metadata-sized text, and a ring showing
  how far through it you are alongside a rough reading time. Select a passage and press **Highlight
  selection** (or the pill that appears at the selection) to keep it, with an optional note about
  why it matters. Your highlights are marked in the text when you come back, and they are also
  listed under **More details**, so they are on the item whether or not you are reading it. If you
  later edit the body out from under a highlight it stops being marked but is never thrown away,
  and merging two copies of an item keeps the highlights from both.
- **You can now see which local models are eating your RAM, and free one.** Settings → Models
  grows an **On this machine** section — a memory bar for the whole machine and a row per model
  that is actually loaded right now, each with an **Unload** button — and the dashboard gets the
  same band. The useful part is the attribution: a model stays in memory after you bind a
  different one, and those rows are marked *not bound* and sorted to the top, because they are the
  ones worth reclaiming. Unloading is safe and repeatable; the model loads again the next time
  something needs it, and the bar moves so you can see the memory actually came back.
- **A model provider can now run in its own process, so a crash in a native library can no longer
  take the gateway down with it.** An app opts in with `"execution": "sidecar"` in its manifest
  and gets its own Python environment for its heavy dependencies — no more waiting for a gateway
  restart after installing them. If the child process dies mid-request you get a clear typed
  error instead of a hung page, the next request starts a fresh one, and search keeps working
  without restarting anything. In-process stays the default for every existing provider; nothing
  changes unless an app asks for it. Installing a sidecar's environment is a resumable background
  job: if it is interrupted, re-running picks up from the step that failed rather than starting
  over, and it tells you the one thing to do about a failure rather than only what broke.
- **A new install now opens on a short sidebar that grows as you use the app.** Instead of
  eighteen destinations on day one, a fresh setup shows five — Home, Chat, Inbox, Store and
  Settings — plus an **Everything +13** row at the bottom of the list. Nothing is locked away:
  open any other surface from a link, from search (⌘K) or from Discover and it renders exactly as
  before *and* joins your sidebar for good, so the rail ends up matching what you actually use.
  One click on **Everything** shows all of them permanently, and the same row (or
  **Settings → Design → Navigation → Show every surface**) puts it back — turning it back off
  keeps every surface you had already opened. **If you are upgrading, nothing changes:** an
  existing install keeps its full sidebar, because the short rail only ever starts for a setup
  that ran the first-run flow. The preference is per browser, like your theme and sidebar width.
- **You can now open your memory in Obsidian and edit it there.** Settings → Memory → **Memory
  vault** replaces the old on/off toggle with three choices. **Mirror** writes memory out as a
  browsable markdown vault — a page per fact, per episode and per person/project/tool it knows
  about, wired together with `[[wikilinks]]` so Obsidian's graph view works — and regenerates it
  from the store, so hand edits are overwritten. **Two-way** reads your edits back: change a fact
  page above its `personalclaw:generated` marker, hit **Sync now** (or just finish a session), and
  your version replaces what was stored — your edit wins even over a fact you originally typed in
  the dashboard, and it shows up in the memory event log so you can undo it.
  Nothing is merged on a guess. Each page carries a hash of its own body, so the sync knows exactly
  which pages you touched; if it cannot read your edit with confidence — the heading is gone, the
  page is empty, or it is an episode, which stays read-only because evidence should not be
  rewritten — it **leaves your text exactly as you wrote it**, marks the page `sync_conflict`, and
  lists it under Settings → Memory → **Health** alongside broken links, pages it does not recognise
  and edits still waiting to be read back. Person/project pages keep a compiled summary on top and
  an append-only timeline below that no sync ever reorders.
  Drop a document in the vault's `raw/` folder and the next sync files it under **Knowledge**, never
  into memory. `personalclaw snapshot` now includes the vault, so an edit you have not synced yet is
  backed up with everything else.

- **An empty Triggers page now offers four working starters instead of a blank form.** Morning
  briefing, weekly digest, nightly check and a standup reminder each show their cadence in your
  own locale's clock, and picking one opens the ordinary create form already filled in — review it,
  change anything, save. The cards are fully keyboard-operable, and the blank "New trigger" path is
  exactly where it was.
- **PersonalClaw can now watch things for you, and new entries land in your library on their
  own.** Knowledge → **Sources** is a new page where you point it at three kinds of thing: a
  **web page** (a changelog, blog index, category or newsroom page), a **feed** (RSS, Atom, JSON
  Feed or a CSV export, with ready-made recipes for Hacker News and GitHub), or a **folder on this
  machine** (new and edited files are indexed; a deleted file's item is archived, never destroyed).
  Each source polls on a cadence you pick and every new entry becomes an ordinary knowledge item —
  searchable, in the graph, pickable with `@` in the composer — with no extra wiring.
  For a web page, paste the URL and press **Preview** first: it runs the detection once and shows
  you the items it *would* save and which detector found them, so you can tune the stack and only
  then save. **No model is involved in any of this** — detection, parsing and de-duplication are
  plain deterministic code, so a source costs zero tokens to watch. If you want that guarantee to
  extend all the way through ingestion, set a source to **Raw**: its items get indexed and embedded
  locally and never reach a model at all, and the source is labelled **no AI** wherever you see it.
  The same story arriving from two sources becomes ONE item carrying both attributions, and polling
  the same feed twice adds nothing.
  When a source stops working the page tells you which fix to try, because the two common failures
  need **opposite** answers: a page that rendered fine but yielded nothing is usually the wrong URL
  (auto-detection reads pages that LIST entries, not homepages or single posts) and you get a field
  to point it somewhere better; a page that builds itself with JavaScript needs the render tier and
  you get a one-press button to allow it. Each row also shows when it last ran, how many entries
  arrived, whether it had to climb to the expensive fetch tier, and when it will check again — and
  you can pause any source without losing what it has already collected.
  Every fetch goes through the same guarded egress path the rest of PersonalClaw uses (host
  classification, private-address denial, per-redirect re-checks, byte caps and a per-poll request
  budget), a watched folder is refused if it points at a sensitive location, and scraped content is
  sanitised at extraction and fenced at every model boundary.
- **App installs now check who published the bundle, not just what's in it.** A publisher can sign
  an app bundle, and the Store verifies that signature on the staged copy **before** the security
  scan and before anything reaches your app tree — so a bundle whose contents don't match its
  signature never gets as far as running its install hook. The install-consent surface now says
  which it is: "Signed by PersonalClaw", "Unsigned — community tier", or a refusal that names the
  file that doesn't match. The signature covers **every file** in the bundle, not just its manifest,
  so swapping a script after signing is caught. Unsigned apps install exactly as they always have,
  at community tier — signing earns extra trust, it is not a new wall. An *invalid* signature is
  refused outright and cannot be clicked through, because a tampered artifact isn't a risk you're in
  a position to accept. Maintainers sign with `scripts/sign_app.py`; the scheme, the rejected
  alternatives and the workflow are in `docs/security/signing.md`.
- **You can install the phone companion to your home screen.** PersonalClaw now ships a web-app
  manifest and a service worker, so the approvals companion opens from a home-screen icon as its own
  full-screen app instead of a browser tab, and its shell still loads when the connection drops. What
  it will never do is show you stale data: API responses are never written to, or served from, the
  offline cache — so an approval you see on the phone is one that is genuinely still waiting, not a
  copy of one that already timed out. Installing requires reaching your gateway over `localhost` or an
  https tunnel, because browsers only run service workers in a secure context; over a plain-http LAN
  address the dashboard works exactly as before and says why installing is unavailable.
- **Memory now decides what to do with a new fact instead of just piling it on.** When a session is
  consolidated, PersonalClaw first collects the facts it extracted, then looks up what it already
  knows that might collide with each one, and only then decides per fact: add it, update the
  existing entry, retire the entry it contradicts, or do nothing because it is already known.
  **Retiring is never a delete** — the old entry stays readable with a pointer to what replaced it,
  and the change is in the memory event log, so a wrong call is recoverable. When it genuinely
  cannot tell which of two contradictory facts is true, it **keeps both and tells you**: the pair
  shows up as an undecided contradiction in the memory Health checks rather than being averaged
  away into one confident-sounding answer. Adjudication costs one extra cheap model call, and only
  when something actually collided; if that call fails, every fact is still saved exactly as before.
- **Memory can record whose claim something is (opt-in).** Turn on "Attribute Claims to Who Said
  Them" and a fact that is only true because somebody said it is stored as a claim with its holder —
  you, the assistant, a named person, or an outside source — and injected into context that way
  ("Alex believes…, weight 0.40") instead of as established fact. Second-hand claims are capped
  lower than first-hand ones, and a lower-authority claim can never retire something you stated
  yourself. Off by default; with it off, every memory is stored unattributed exactly as before.
- **An optional topology block orients a new session in your memory graph.** "Topology Orientation
  Block" groups your linked entities into neighbourhoods and shows the biggest few (with their
  leading entities) at the start of a new session, so the assistant knows which areas exist before
  it searches. Costs a few hundred characters of context and stays off by default; the grouping is
  fully deterministic, so the same graph always produces the same neighbourhoods.
- **Papers now ingest as papers.** Drop a PDF into the Knowledge Library, or save an arXiv link, a
  DOI or any `.pdf` URL, and PersonalClaw reads its shape rather than just its words: it detects the
  sections (abstract, introduction, method, results, discussion, conclusion, references) and stores
  three purpose-cut views beside the full text — a *brief* for "what is this claiming", a *body* for
  "how did they do it and what happened", with the bibliography stripped out of both, and a *meta*
  cut of the front matter. The bibliography itself is parsed into references keyed by arXiv id, DOI,
  title or author-and-year, so a paper arrives with its citations already listed. All of it is plain
  deterministic parsing: the same file always yields the same sections, and no model is involved at
  any point. Saving an arXiv or PDF link used to run the HTML page-scraper over PDF bytes and store
  the resulting noise; it now fetches the document itself. Originals are cached by content hash, so
  re-saving the same paper, or regenerating an item, costs no network at all.
- **An app can now teach PersonalClaw to watch a source it has never heard of — by shipping a
  parser, not a client.** A connector pack is an ordinary app that declares which URL to fetch
  (a template, plus which of your saved credentials to send as a header) and a small script that
  turns the response into knowledge items. PersonalClaw does the fetching itself, through the same
  guarded network path everything else uses, and hands the downloaded body to the pack's script on
  standard input. **The script never gets a network connection of its own** — it cannot open a
  socket, start a process, or call into system libraries, and if it tries, the whole batch is
  thrown away rather than partly imported. The same is true of a script that misbehaves by
  accident: garbage output, a run that stops halfway, or an item missing the fields needed to
  recognise it again produces zero items and a specific reason, never a half-imported feed that
  looks like a source that went quiet. Packs are bounded by a time limit and size caps, and a
  pack must ask for network permission in its manifest, so what you are agreeing to is visible
  at install.
- **Watching a site you have a link to now starts with "we already know this one".** The
  Sources create screen opens with a box for the URL you already have: paste it and PersonalClaw
  checks a bundled directory of site recipes before you touch a single setting. It covers GitHub
  releases and trending, Hacker News, PyPI project releases, a subreddit, a Substack newsletter,
  and generic changelog/release-notes pages. Pick a match and the form arrives filled in from the
  URL you pasted — a GitHub repository link becomes its releases feed, and the screen shows you
  that it will be watching the feed rather than the page you typed. If nothing covers your URL it
  says so plainly and you carry on choosing a kind yourself, exactly as before.

- **A voice is now a thing you own, not a dropdown value.** Voice profiles hold a name, the
  engine that renders them, a reference clip, a pinned seed and a spoken-consent record, and you
  can bind a different one per surface — one voice in the web dashboard, another for a Slack
  channel, another for a specific agent — with an explicit "speak as this one" request always
  winning over any binding. When you hear a generation you like you can lock it: PersonalClaw
  copies that clip and pins its seed, so the voice stops being a lottery until you unlock it
  again. Reference and consent clips upload resumably, so a long clip that stalls picks up where
  it left off instead of starting over, and a half-finished upload is never treated as a usable
  clip. Two guarantees are structural rather than promised: consent for a cloned voice is
  re-derived from the recording on disk every single time it is read — editing the saved flag by
  hand proves nothing — and revoking consent immediately stops that voice's audio from being
  served back out, with every consent record/verify/revoke written to the security audit log
  (ids and verdicts only, never the recording or what you said). If you create no profiles,
  nothing changes: speech resolves exactly as it did before.

- **Proposals are now one thing you approve in one place — and an app can raise one.** Anything that
  suggests a change (a skill the system wants to extract, a workflow it wants to run, an action, or
  an installed app's own suggestion) files the same kind of proposal, and the Inbox's new Proposals
  view shows what approving would actually DO before you click. Approving runs it through the
  machinery that already exists for that kind of work — no separate approval path per producer — and
  **if the apply fails, the proposal stays in your inbox with the error on it** rather than quietly
  disappearing as if it had worked. Batch approve is deliberately narrow: it lights up only when
  every selected proposal comes from the same source and is the same kind, so "approve all" can never
  sweep together four unrelated changes you reviewed as one. Proposals whose payload is marked
  editable can be edited before approving, and what you edited is exactly what runs. An app must
  declare each proposal kind it may raise in its manifest (`permissions.proposals`), which you see at
  install time; an undeclared kind is refused, and one app can never propose an action that calls
  into another app. Every app-raised proposal is recorded in the security event log.
- **An HTML artifact can now be opened as a real page, not just previewed in a card.** Deploy a
  widget/HTML artifact from its detail view and it is served at a stable in-app URL
  (`/artifacts/serve/<slug>/`) you can open in a pane beside the artifact or in a new tab, so a
  generated dashboard or tool can actually be clicked through instead of only read. The library
  toolbar lists everything currently serving with its URL, and "Tear down" un-publishes it while
  keeping the artifact and its history. This is local-only on purpose: the page sits behind the same
  session auth as the rest of the dashboard — there is no public link — and it is fenced by a strict
  content-security policy that leaves it unable to call PersonalClaw's own API, so an artifact
  written by a model cannot use your session to act on your instance. Deleting an artifact
  automatically stops serving it, and a request that tries to climb out of the artifact's own folder
  is refused rather than answered.
- **You can point PersonalClaw at an outside skill catalog and browse it in the Skills store.** Add a
  catalog under `packs.skill_catalogs` — a JSON index endpoint, or a repo laid out as
  `skills/<slug>/SKILL.md` — and it shows up as one more source alongside the bundled skills, with
  per-source match counts on the source filter so a large catalog tells you how much of it matched,
  not just how many rows fit on screen. A catalog is treated as untrusted third-party content
  whatever you think of its author: it installs through exactly the same guarded path as every other
  marketplace (staged to quarantine, scanned at community trust, then committed with a lock file you
  can re-verify later), so a malicious skill is refused before anything reaches your skills tree.
  Catalog fetches go through the same network guard as every other outbound connector — a catalog URL
  can't be talked into reaching a private address — and browsing a big index costs one fetch and no
  model tokens; only the skill you choose to install is ever downloaded. One unreachable catalog is
  skipped with a log line instead of emptying your store.
- **When two machines edit the same thing while offline, nothing is overwritten — you get asked.**
  Multi-machine sync now tells a real conflict apart from an ordinary catch-up: if both machines
  changed the same record since they last agreed on it, the change is not applied. Your local copy
  stays exactly as it is, both versions are kept, and the divergence lands in a conflict review queue
  as a needs-review item — memory conflicts on the memory review surface, knowledge conflicts on the
  knowledge one. A background model pass drafts a suggested merge with a short rationale for you to
  look at, and that draft is only ever a suggestion: PersonalClaw never applies it for you. With no
  model configured (or when the model is unavailable) the conflict still appears — just without a
  suggestion. A one-sided change, where only one machine moved, keeps merging automatically as before.
- **Memory now has slots: a handful of small, always-there notes about you, instead of facts the
  assistant has to go looking for.** Six of them exist by default — persona, preferences, pending
  items, self-notes, glossary (per workspace) and self-model — and whatever is in them is put in
  front of the assistant at the start of every session, so standing context like "call me by my
  first name" or "in this project, 'run' means the deploy script" stops depending on whether a
  search happened to surface it. Each slot has a size limit and the whole block has a hard ceiling,
  because something injected on every turn costs you context forever. When a slot is full, the write
  is **refused and you are told what would have to go** — with the specific lines named — rather than
  quietly trimmed or quietly dropped: losing something you asked to be remembered without saying so
  is the one outcome worth failing loudly to avoid. Slots start out empty and nothing is written
  until you put something in one. Anything the assistant adds to a slot on its own is only ever
  appended, and a line **you** delete is never brought back, no matter how many times it re-observes
  it. (An editor for slots arrives with the memory dashboard work; today they are reachable through
  the memory API.)

- **PersonalClaw can now try a local model first for background work, and fall back to a cloud
  model when it can't.** Bind two models to a background use case — say a local one and a cloud one
  under Reasoning — turn routing on, and PersonalClaw will reach for the local model first, moving
  on to the next model you bound if it's unavailable or too slow. It only ever reorders the models
  you already chose: it never adds one, never removes one, and a model that can't be reached still
  reports a clear error rather than being quietly swapped for something else. Settings → Models →
  Routing gains a policy table showing exactly which model each kind of request tries first and why,
  with three ways to overrule it: a mode (off / prefer local / learn from results), a pin (always
  local, always cloud, or one exact model), and manual reordering. Off by default; changes to the
  table are recorded to the security event log, since routing decides which models see your prompts.
- **"Check this work" — verification that actually runs, instead of a second opinion from the same
  voice.** A new bundled `check-work` skill answers "did that actually work?" by reconstructing what
  the session CLAIMED, deriving 2-4 executable checks from those specific claims (does the file
  exist, does it contain the symbol the claim named, does the command re-run clean), running them
  with real tool calls, and reporting pass/fail with the observed line quoted as evidence. A check it
  cannot execute is reported **unverifiable** with the reason — never assumed passing — and a session
  from which no check can be derived is told so rather than handed a generic checklist. After a turn
  that did real multi-step work and said it was done, chat now offers a **Check this work** chip: it
  only offers, and the checks run when you click it, so verification never spends your tokens or your
  latency without you asking (**Settings → Chat → Offer 'Check this work'**, on by default).
  Unattended SDLC loops can run the same derivation after a stage's gate passes
  (`loops.check_work_stages`, off by default) — which catches the case a gate command can't see: the
  command passed, but the stage claimed a file it never wrote.
- **Ask for a few versions and get the best one, with the others one click away.** Say "give me 3
  versions and pick the best" and PersonalClaw drafts the candidates **in parallel** — each at a
  different temperature, so they are genuinely different answers rather than the same one three
  times — then judges them against criteria you confirm and leads with the winner. The runners-up sit
  in a collapsible list with their scores, and "use #2" switches to that candidate verbatim, no
  re-drafting. Because N candidates cost N model calls, it always confirms the count (capped at 5)
  and what "best" means before spending anything, and every call is metered and logged like any other
  model call. If one candidate fails you still get the rest, judged; if the judge is unavailable you
  get one answer clearly labeled as unranked; if everything fails it says so instead of inventing an
  answer. Each run also records an anonymous line — how many candidates, how far apart their scores
  were, which one won — so it can eventually tell you when sampling is actually worth the extra cost
  and when it isn't.
- **Apps can now share data with each other, read-only, only when both sides agree.** An app that
  wants to expose its stored data declares `storageShared: true`; an app that wants to read another's
  data names it in `storageRead`. A read is granted only when BOTH are declared — neither app can
  reach into the other one-sidedly — and the reader gets a strictly read-only view (writing to shared
  data fails; to send another app data, apps still go through the app-messaging broker). Both
  declarations are shown on the install-consent screen so you see, before installing, which apps an
  app shares with or reads, and each active grant is recorded to the security event log.
- **Evaluation scenarios are now yours to keep, version and extend.** The four bundled scenarios
  install into `~/.personalclaw/evals/scenarios/` on first use instead of hiding inside the
  installation, so `personalclaw eval` runs — and the offline eval substrate scores — the same
  library you can edit and add your own scenarios to. An upgrade refreshes a bundled scenario only
  when it ships a newer version than the copy in your home, so your edits survive updates. Each
  scenario also names the seeded fixture home it runs over, so a run starts from a known clean state
  rather than from whatever your home happens to contain — and nothing an eval run does can touch
  your real home. Every recorded eval result now carries a pin (which scenario, which models, which
  prompts, which config), and a result that can't be attributed is refused rather than filed
  misleadingly: "did the score move, or did something underneath it move?" is now answerable.
- **Voice input can now run hands-free, and spoken replies stop talking over you.** Beside the
  push-to-talk mic there is a hands-free toggle: keep talking and your dictation accumulates in the
  composer, and nothing is sent until you say a confirmation phrase ("go ahead", "do it") — say
  "cancel" and the draft is thrown away, so a half-finished thought can't become an executed
  instruction. While a reply plays aloud the microphone is released and whatever it captured is
  discarded, and any transcription that repeats three consecutive words the assistant just said is
  dropped as echo, with the dashboard saying so rather than looking deaf. Spoken text is cleaned
  first — code blocks, URLs, file paths and CLI flags are no longer read out letter by letter, while
  the transcript keeps the full text — and a dictated turn tells the model it came from speech so it
  self-corrects misheard words. All six knobs live in **Settings → Speech & Transcription →
  Hands-free voice**.
- **Approval memory: teach the assistant what it may do without asking again.** A new
  `triage_rules` tool lists, adds, and revokes standing approve/deny rules for the coming
  proactive digest, each shown with how often it has fired and where it came from. A **deny rule
  always beats an approve rule**, however narrowly the approve was written, so blocking a class of
  action is the safe move. New **Proactive** settings section — the digest schedule, its
  classifier gate, and the auto-execution cap — with triage and auto-execution both OFF by
  default. Nothing runs or acts until you turn them on.
- **Attention notifications can now ask for a second opinion before interrupting you.** Turn on
  verification for a proposal or agent-request rule and, before its notification fires, a cheap
  background check judges whether the claim holds. Only a clear refutation withholds it — every
  uncertain, unchecked, or model-unavailable case still delivers, so a real request is never
  silently dropped. Withheld items appear under a new **Filtered** view with a one-click **Restore**
  that delivers the notification you held back. (Off by default; opt in per rule.)
- **A Companion apps settings section — turn on LAN discovery so phone/desktop clients can find this gateway.** Settings → Companion apps adds a **LAN discovery** toggle (off by default — announcing a service on your network is an opt-in) and an **instance name** field (the friendly label a client shows; empty falls back to the machine hostname). This is the configuration foundation for the companion-app clients; the discovery advertiser itself arrives next.
- **You can now replay a finished workflow run and see exactly where an edit would change it.**
  `personalclaw workflow replay <run_id>` re-drives the run's decision path against its OWN recorded
  responses — it calls no model and spends nothing — and compares the result to the path the run
  actually took, reporting the first step that moved. Replaying an unchanged run reproduces its
  trajectory exactly; edit a step's prompt and replay names that step as the first to diverge, which
  is the question a mid-run edit really asks: *what did my change actually affect, and from where?*
  Divergence is a normal answer, not an error — a template edit is supposed to diverge, and the verb
  tells you where rather than failing.
- **A run's introspection now shows how its branches and judges actually decided, across the
  template's history.** A workflow that routes on a condition, or gates on a judge, journaled each
  decision one run at a time and never added them up — so a branch that has taken the same case
  every single time, or a case no run has ever reached, was invisible. The cockpit's introspection
  panel now has an **Edges** section: for each branch it shows the case distribution, and for each
  judge the verdict distribution. It flags two things a legible plan should not have — a **case
  never taken** (declared but dead), and a **selector doing no work** (a branch that always routes
  one way, or a judge that always returns the same verdict). Both are held to the same sample bar as
  the "never said no" gate badge: over a handful of runs a case simply hasn't been sampled yet, and
  a warning there would just teach you to ignore the panel — so nothing fires until there is a real
  history behind it.
- **A workflow template can now learn from its own runs — and you stay in control of every change.**
  Open a template and you'll find three new things. A **Versions** tab shows its history as an
  append-only list: each accepted change is a new version, you can see what changed between two of
  them as a typed diff, and **rolling back is one click** — it just re-points to an older version,
  nothing is rewritten, and a run always records the exact version it executed. A **Run Ledger** tab
  shows the template's recent runs at a glance. A **maturity badge** says how proven the template is
  (a gate that has never rejected a bad run is not yet "proven", and the badge is honest about that).
  And a **Refine now** button runs a propose-only refiner over the template's failure history: it
  reads what went wrong, and if the evidence supports it, files ONE reviewable proposal to improve
  the template. It can only propose — it can never edit a template, install a skill, or change what
  makes a template fire. You accept a proposal to apply it (which creates the next version), or reject
  it. Nothing changes until you say so.
- **Accepting a skill refinement no longer rewrites the skill.** A refinement now applies as a small
  sidecar file layered onto the skill when it loads, so the original is never touched — which means
  reverting a refinement is deleting one file, and a marketplace skill's integrity lock stays intact.

- **Runs now record what LANDED, not just what they did.** A workflow that made a measurable
  decision could already journal the bet it was making and have it graded once the horizon passed.
  That was the only thing in the system able to do it. Now any producer can open the same kind of
  question, and two more already do: **publishing an artifact** asks whether anyone ever consumed it
  (a week's horizon), and **stopping to ask you a question** asks whether interrupting you was worth
  it — graded from your own answer, so an approval reads as the interruption landing, a rejection as
  a bet that lost, and a gate nobody ever answered as an interruption that went nowhere. Two
  restraints kept from the original: a question whose ground truth cannot be read closes as
  *inconclusive* rather than being invented, and that weaker evidence ages out about four times
  faster than a real measurement; and only a decision's outcome files anything for you to review —
  the rest are recorded in the run's own ledger, because "this artifact's outcome is inconclusive"
  is not something you can act on.

- **You can now ask which runs of a template went a different way — and get warned when they start
  going a worse way.** Every run now carries a *trajectory signature*: a fingerprint of the exact path
  it took — which steps ran, in what order, how each resolved, which branches it skipped. Two runs of
  a template that took the same path share the same signature, so "show me the runs that did something
  different" is finally a question with an answer, per run on its introspection view and per template
  at `GET /api/workflows/{name}/trajectory`. And when a template's recent runs shift onto a path that
  fails more often than the one they used to take, that shift is surfaced as a risk — not on the third
  run of a brand-new template (that is just a template that has barely run), but once there is enough
  history for the shift to be real. It costs nothing to compute: it is read straight from the ledger
  each run already writes, with no new tracking of any kind.
- **Work that nobody reads now says so.** Every watchdog until now measured whether a scheduled run
  was still *working* — findings, wall-time, errors, stagnation. None asked whether anyone ever
  opened what it produced, so a template quietly writing a deliverable on a cadence into a document
  nobody reads looked perfectly healthy. Now, when the last three deliverables of one work unit each
  sat a full week without being opened, pinned or edited, it appears in your review queue as a
  proposal to **pause or retire** it, naming the runs and the documents involved.
  It **never stops anything by itself.** "Nobody has looked yet" and "nobody will ever look" are
  different facts and only you can tell them apart, so this reports and waits — accepting the
  proposal does not change a schedule either. Three further restraints so it stays worth reading:
  one open, one pin or one edit anywhere in those three cycles and it stays silent; a document that
  was deleted counts as *unknown*, never as unread; and if you reject the finding once, it is not
  raised again.
- **A second starter home, for looking around before you commit anything.**
  `personalclaw gateway --seed demo-home` fills a scratch home with a system that has clearly been
  used for a couple of weeks: two projects that each carry a real brief, three lists, and ten tasks
  spread across in-progress, done, blocked and cancelled — one of them blocked on another task by
  name, with exit criteria part-ticked and notes explaining why the hard one is still open — plus
  written-up memory covering preferences, current project context and two days of history. It also
  arrives past the first-run setup, so it opens on the dashboard rather than the wizard. Use it to
  take screenshots, try a surface, or see what a populated PersonalClaw looks like without touching
  your own data: `--seed` **refuses your real home outright**, so point `PERSONALCLAW_HOME` at a
  throwaway directory and delete it when you are done. Two things it deliberately leaves empty —
  knowledge items and loops — because those live in databases rather than files and a starter home
  is only a file copy; create one of each by hand if a screenshot needs them.

### Changed
- **What finishes a loop is now written down in one place instead of decided in five.** Every loop
  kind — general, goal, code, design, research — used to carry its own Python answer to "is this
  loop done?", so there was no way to read the rule without reading five modules, and two of the
  five turned out to answer nothing at all. Each kind now *declares* its convergence: which
  mechanism decides done-ness (a command it runs, a judge it commissions, its own per-cycle
  orchestration, or never), whether reaching the cycle budget counts as a clean finish, and whether
  the stall detector applies. One supervisor reads that declaration for every kind. Behaviour is
  unchanged — same commands, same judges, same skeptic pass, same calibration canary, same
  budget and stall handling — but the rule is now inspectable, and a new loop kind gets convergence
  by declaring a row rather than by shipping code the engine has to trust.
- **PersonalClaw no longer writes anything into your coding CLI's own config, and an ACP agent app can
  no longer ask it to.** A CLI-side config seeder existed for a coding CLI that ignored the tool list
  we hand it when a session opens. Driven end to end against all three shipped CLIs, no such CLI
  exists — each one honours the list passed over the protocol, verified by watching PersonalClaw's own
  tool server actually start underneath it while none of the CLIs' config files mentioned us at all.
  The seeder never ran either: nothing ever supplied the setting that switched it on. It is now
  deleted rather than left lying around, so there is one way the tools reach a session instead of two,
  and enabling or disabling an agent app touches nothing of yours outside PersonalClaw's own home.
  **SDK note for app authors:** `register_acp_cli_entry` no longer accepts `agent_config_dir`. A
  bundle still passing it fails loudly at import instead of quietly seeding nothing — which is the
  point of removing it rather than ignoring it. Nothing else about the call changes, and no CLI needs
  the argument, so a bundle that never declared it is unaffected.
- **The agent can no longer edit a file it has not read.** An edit computed against a stale or
  imagined version of a file used to succeed silently and revert whatever someone else had just
  changed. Now a write to an existing file is admitted only if that file's *current* content was
  actually shown to the agent first, and a write that cannot prove it is refused with the exact read
  to do instead — so the agent fixes itself in one step rather than clobbering your work. The check is
  on what was really observed, not on whether a read happened: a read of a *different* file does not
  count, and neither does one whose output was cut off before the part being edited, because reading
  the first page of a long file does not tell you what is on the fifth. **Creating a new file needs no
  read** (there is nothing to lose), but overwriting an existing one is held to the same bar as an
  edit, and it must have been seen in full — an overwrite replaces everything, including the part you
  never saw. If a file changed on disk after the agent read it, the write is refused too, and the
  refusal says so; `bash` is the one exception, since a shell command names no target the check could
  hold it to.
- **Rewinding a conversation no longer throws the old ending away — and that history is now
  stored inside your chats.** Editing a message from earlier in a conversation used to delete
  everything after it. It still replays from that point, but the turns that came off are kept on
  the message you edited: a divider at the rewind point tells you how many are held, you can
  expand it to read them, and **restore** rebuilds "everything up to the edit, plus the old
  ending" as a *new* session, leaving the chat you are in untouched. Five rewinds' worth are kept
  per message; a sixth pushes out the oldest. **This adds a field to saved chat messages.** A chat
  written before this update loads exactly as it did and simply has nothing held at any turn — but
  a chat written *after* it, opened by an older PersonalClaw, will not show the retained endings,
  and rewinding there goes back to discarding them. As with any 0.x state-shape change, run
  `personalclaw snapshot` before updating if you want a restore point.
- **Finding something in a long conversation now works properly with a keyboard and a screen
  reader.** `Esc` closes the find bar from every one of its controls rather than only from the
  text field, `↑`/`↓` cycle matches instead of moving the caret, and closing the bar puts your
  focus back where it was instead of dropping you at the top of the page. A screen reader now
  hears the position in words — "Match 3 of 17", or "No matches" — rather than the bare digits
  on screen, and it is told when follow-up suggestions appear, which was a change you could
  previously only see. On a phone-width screen the find bar now spans the column, so on a narrow
  phone it no longer hangs off the edge of the screen.
- **The Optimize button now knows who said what, and leaves an already-good prompt alone.** The
  recent conversation it sends along is labelled by speaker and ordered oldest-to-newest, and each
  turn gets twice the room it used to — so asking it to "add a test for that file from earlier"
  resolves to the actual file, instead of guessing from an unattributed blob. Twice the room means
  the ten turns no longer collide with the size limit; when a limit is hit, whole turns are dropped
  from the oldest end rather than a turn being cut in half and arriving attributed to nobody. And a
  prompt that is already specific now comes back untouched: the optimizer is told to say so in one
  word instead of paraphrasing your prompt back at you, which used to be how a good prompt got
  quietly reworded. Reverting still restores exactly what you typed — and now puts the cursor back
  in the composer, since you reverted in order to keep typing.
- **The assistant now needs to see a habit work three times, not twice, before it offers to make it
  a standing principle.** Its self-model — the one part that learns from what quietly *works*
  instead of from corrections — proposes a behavioural principle only after three reinforcements.
  Twice is the count a coincidence reaches, and a principle is always-on: it changes how every
  later answer is shaped, which is a change you would struggle to trace back. Working theories,
  which announce themselves as guesses, keep the lower bar. As before, nothing is installed on its
  own — it is still a proposal you accept or reject.
- **A step that reads another step's output no longer needs a hand-written ordering — and steps in
  different branches of a workflow can now feed each other.** The engine now derives "run after"
  directly from "reads the output of": if a step binds `{{nodes.other.output}}`, the scheduler holds
  it until `other` has finished, wherever the two sit in the workflow. Two things follow. A step that
  reads a sibling running alongside it is simply held rather than refused, so you no longer hand-write
  an ordering the engine could see for itself. And a shape that used to be impossible now works: a
  workflow can fan out into parallel branches and have a later step pull results from *across* those
  branches — a diamond spanning two containers — instead of being told the branches cannot reference
  each other. A hand-written `needs` still has one job, expressing an ordering that is not about data
  (a lock, "publish only after the announcement went out"); it is now checked against what the data
  already implies — the engine warns when a `needs` merely restates a binding, and refuses one the
  workflow's structure could never honour. No bundled template changed how it schedules.
- **Autonomous loops now learn the way workflows do.** The self-improvement loop — the part of the
  system that reviews finished work and proposes better ways to do it next time — could only ever see
  *workflow* runs. The long-running autonomous loops (goal, code, design, research) kept their cycle
  findings and done-ness verdicts in a separate place it never looked, so a loop that ran for weeks
  contributed nothing to what the system learned. Loops now record their cycles, assessments, stalls
  and reaps to the same shared log workflows use, and a finished loop mines its own history for
  proposals — so three loops that keep taking the same successful path can now surface as "this looks
  like a procedure worth naming." **This moves where a loop's findings and verdicts are stored.** A
  loop that finished *before* this update keeps its files, but its old findings/verdicts won't appear
  in the cockpit or feed learning; loops going forward are unaffected. As with any 0.x state-shape
  change, run `personalclaw snapshot` before updating if you want a restore point.
- **A fan-out step that shares a limited resource now waits its turn instead of racing.** A workflow
  that spreads work across parallel branches can mark a step as needing a lease on a named resource;
  the engine admits only one holder at a time and the rest wait, and the lease survives a gateway
  restart so a crash mid-fan-out does not double-claim. Steps can also declare a bake-in delay before
  a result is trusted, or roll back a step whose measured quality regressed. Workflows that declare
  none of these behave exactly as before.

- **A workflow that reads another step's output now refuses to save unless that step is guaranteed to
  run first.** The engine kept two separate pictures of how steps relate: what must run before what,
  and what reads whose output. Nothing checked they agreed, so a template could pull
  `{{nodes.some_step.output}}` from a step running *alongside* it — and instead of a clear complaint,
  the run failed partway through with "binding failed: check the referenced node id and field exist",
  pointing at a step id that was perfectly correct. That is now a typed error at save time
  (`WF_UNORDERED_DEP`) naming the reader, the step it reads, and why the ordering is missing. **No
  bundled template was affected** — all 19 were checked first, and every one already ordered its
  steps correctly. Nothing about how workflows run has changed.

- **A workflow step that declares what its output will contain is now checked against the steps that
  read it.** A step can declare an output contract — "this must be JSON, and it must contain these
  keys" — and the engine has always enforced it on the *producing* step. Nothing ever compared it to
  the steps reading that output, so a template could bind `{{nodes.classify.output.summary}}` from a
  step whose own contract promises `findings`, save perfectly clean, and then die partway through the
  run on an unresolvable reference. That is now a typed error at save time
  (`WF_UNSATISFIABLE_OUTPUT_REF`) naming the reader, the step it reads, the key it wanted and the
  keys the producer actually guarantees. In a workflow that already uses contracts, a step read at a
  sub-path but declaring none raises an advisory warning instead, listing the readers that would
  benefit. **No bundled template was affected** — all 19 were censused first: none declares an output
  contract today, so nothing shipped changes and nothing new appears in validation output. No new
  contract vocabulary was added, and nothing about how workflows run has changed.


- **A loop that keeps working but stops getting anywhere now stalls, even when it insists it is
  making progress.** The supervisor's stall detector used to read one number the worker itself
  writes — `new_findings_count` — and treated a missing number as "progressing". So a worker that
  reported *any* nonzero count, or simply stopped reporting one, could spin for its entire cycle
  budget on your money without anything noticing. The supervisor now also watches two things the
  worker cannot write for itself: whether the cycle report is **byte-identical** to the previous
  cycles', and whether every cycle **checked the same sources or touched the same files**. Either
  one stalls the loop to *"needs direction"* with the reason on the card, so you can steer it
  instead of paying for another ten identical cycles. The self-reported count is still used — it is
  the cheapest and clearest signal when it is honest — it just can no longer overrule what the
  supervisor can see, and its silence no longer counts as progress.
  A monitor loop still never stalls (a quiet cycle is the point), and a loop kind that records
  nothing to compare is judged only on content, so nothing stalls for lack of data. The number of
  no-progress cycles it takes is now a setting, `loops.stagnation_window` (default 5, minimum 2),
  read live — no restart needed.

- **A loop's judge no longer runs on the same model as the worker it grades.** Autonomous goal
  loops never let the worker certify its own work — a separate judge, in its own session with its
  own prompt and no write tools, decides whether a cycle is done. But that judge was resolving the
  same model binding as the worker, so the two shared a blind spot on exactly the question the judge
  exists to answer. The judge now resolves its own axis, `loops.judge_use_case`, which defaults to
  **Reasoning** — so if you have pinned a stronger model to Reasoning in Settings → Models, that is
  now the model that decides done-ness, and the loop's work still runs on the Loops binding. Set
  `loops.judge_use_case` to `loops` in `config.json` to put both back on one binding.
  A judge whose model is unavailable behaves exactly as before: the cycle is **deferred**, never
  reported complete, and the warning in the log now names the binding to go check.

- **The approval prompt now tells you what a tool call can touch, and how far your answer
  reaches.** When the agent asks permission to run a tool, the card is a four-part brief instead
  of a tool name and four buttons: **what** (the tool and its arguments), **why** (the one-line
  purpose, when the agent gave one), **what it can touch** — chips like "Runs a command", "Writes
  files", "Uses the network", "Reads only" — and **how far the answer reaches**, a
  "Remember this choice" picker (*Just this once* · *This chat* · *This agent*) that spells out, in
  plain text, exactly what gets remembered before you answer. Then one **Allow** and one **Deny**.
  The out-of-context approval toast (an approval raised in a chat you are not looking at) carries
  the same one-line summary, so "another chat session needs approval to run bash (runs a command)"
  is legible without opening it.
  Two deliberate restraints: the chips are **claims, not an audit** — only facets the system can
  actually establish are shown, and when it can establish nothing it says nothing rather than
  painting four reassuring negatives; and the brief **never advocates**. Nothing recommends
  approving, no answer is preselected or focused, and neither verb is styled as "the" action —
  the only thing chosen for you is the narrowest scope, which remembers nothing.

### Security

- **A "read-only" background task could write to your memory, open a webhook, and schedule itself.**
  Work PersonalClaw starts on its own — a scheduled research run, a parallel investigation branch — is
  marked read-only, and the check enforcing that guessed from the tool's name: anything containing
  *write*, *delete*, *create* and a dozen similar words was blocked. Tools named after what they do
  rather than how they do it slipped through, and most tools are. Counting across every tool that ships,
  **59 of 70 were treated as harmless**, among them saving to your memory, forgetting from it,
  registering a webhook that lets an outside system message you later, scheduling a recurring task,
  cancelling or rewinding a workflow run, saving an artifact, generating an image, stopping a loop, and
  notifying you on Slack or Discord. Read-only work could also start more background work, so one task
  could quietly become many.
  Every tool that ships is now classified individually, from what its own documentation says it does
  rather than from its name — which cut both ways: two tools that read like writers state plainly that
  they only read, and are still allowed. Read-only work keeps every genuine read, because a research
  task that cannot read is useless.
  **A new tool can no longer be added without deciding.** A test walks the live tool registries and
  fails, naming the tool, if one is neither a known read nor a classified write. That check is what
  makes "blocked unless we said otherwise" true, rather than a comment claiming it.
  One deliberate exception: a tool that *proposes* something for you to accept or dismiss stays
  available, because a proposal is not a change until you accept it, and one of PersonalClaw's own
  shipped workflows is built precisely to propose and nothing else.

- **Uninstalling or disabling an app did not stop it, and uninstalling briefly gave it more access
  than it had.** An app proves who it is with a short-lived token it holds for up to an hour, and the
  gateway checks each of its requests against the permissions the app declared at install. That check
  needed to read the app's manifest, and when it could not read one it allowed the request instead of
  refusing it. Uninstalling removes the manifest. So for up to an hour after you uninstalled an app, its
  token still worked and it was no longer confined to the permissions you approved: it could reach any
  endpoint, including your saved credentials. The same held for an app whose manifest stopped being
  readable, which an app that can write its own folder could arrange for itself.
  **Disabling had the mirror-image problem.** Enabling and disabling are recorded separately from the
  permissions file, and the check only ever read the permissions, so a disabled app kept exactly the
  access it had. Issuing a *new* token to a disabled app was already refused, so this only affected a
  token it already held, for the remainder of that hour.
  An app's lifecycle is now checked on every request, and a request that cannot be positively
  authorized is refused rather than allowed. Disabling takes effect on the next request, uninstalling
  ends access immediately, and an unreadable manifest is a refusal. Every refusal is recorded in the
  security log with which of the reasons applied. Nothing changes for an app you have installed and
  enabled: it keeps precisely the access it declared, and no more.

- **PersonalClaw's own keys were protected in the files area and nowhere else.** The file browser
  refuses to read them — the key that signs your security log, the loopback auth secret, the session
  signing key. The guard that the agent's shell commands and the terminal both consult had never been
  told about any of them, so it allowed what the file browser refused. An agent running an ordinary
  `cat` on the key that signs the security log was not stopped, and with that key a log entry can be
  forged and still pass verification, which is the one record here that cannot be repaired afterwards.
  All of them are now refused by name wherever they are, so the two agree.
  **If you moved your PersonalClaw folder, the protections were weaker still.** The list was written
  relative to your home directory, so pointing `PERSONALCLAW_HOME` somewhere else — which every
  development setup does — left even `.env` and the governance ceiling unprotected. The ceiling is the
  hard limit you set on what any run may spend and do, and it is meant to be one thing the agent cannot
  rewrite. It is resolved against wherever your folder actually is now.
  **One honest limit.** A terminal session is a real shell running as you, so it can read any file you
  can, and no change here alters that. What changed is that a credential directory can no longer be
  used as a terminal's starting directory, and every path an *agent* reads through is now covered.
  Your own projects are untouched: a `.pem`, a private key or a `sessions.json` of your own is still
  yours to read.

- **A password inside a URL was invisible to every place PersonalClaw redacts secrets.** The
  redaction knows what a secret *looks like* — the key formats the big providers use, and lines of the
  form `api_key = …`. A credential carried by *position* instead, in the `user:password@` part of a
  URL, matched none of those. So a git remote like `https://you:yourtoken@github.com/you/repo.git`
  went through the diagnostics log, the security audit log, agent output and confirmation previews
  completely intact.
  **It looked covered, and that is worth saying.** One test claimed to check exactly this — and
  planted a GitHub token as the password, which the existing rules already recognised on shape alone.
  It passed for the wrong reason. Substitute an ordinary password and it fails. Both cases are checked
  now.
  **The host is deliberately kept.** Only the credential is replaced, so "the clone of
  github.com/acme/repo failed" is still readable in your logs. Removing a secret should not cost you
  the ability to see what went wrong.
  **The security audit log gets this first**, because it is the one place that cannot be cleaned up
  afterwards: it is a tamper-evident chain, so rewriting an old entry would break it. A secret written
  there is there for good.
  **And a source URL carrying a password is now refused outright** when you add an app source, rather
  than accepted and then redacted downstream. The message says why. Ordinary remotes are unaffected —
  including `git@github.com:owner/repo.git` and `ssh://git@host/repo`, where the username before the
  `@` is a username and not a secret.

- **Adding an app source no longer accepts anything you type.** `not-a-git-url` was stored silently
  and then appeared in the Store as its own source heading with no apps under it and nothing saying it
  was broken. It is refused now, with a message naming the forms that work. (A *valid* source that
  turns out to be unreachable still shows as an empty group; that part is unchanged.)

- **Three more ways a path could leave the folders PersonalClaw is allowed to touch.** All three are
  the same mistake as the record-id one above, reached differently, and all three were reproduced
  before being fixed.
  **"Reveal in Finder" checked less than every other file operation.** It was the one file endpoint
  that never asked whether a path was inside a folder the dashboard surfaces, so `/etc/hosts` and
  another install's home both worked — and it is the endpoint that hands the path to your operating
  system to open with whatever it thinks the file is. The checks it *did* have could not catch that: a
  path needs no `..` and no credential-looking name to simply be somewhere else.
  **A one-line file could redirect the git panel at any repository on the machine.** Git lets a `.git`
  entry be a file that points somewhere else — that is how worktrees work — and PersonalClaw was
  checking where the pointer *sat* rather than where it *pointed*. A pointer written inside a folder
  it was allowed to browse therefore aimed the whole git panel anywhere, and a whole-commit diff would
  hand back `.env` contents that reading the file directly refuses. The check ran on every request; it
  was measuring the wrong path. Worktrees of repositories you can already browse are unaffected.
  **Installing a skill checked every file in it and not the folder they go into.** A skill whose name
  was a path escaped the skills folder entirely. A safe file path underneath an unsafe folder is not a
  safe path. The same hole existed in the quarantine folder used to scan a skill *before* installing
  it — so it escaped ahead of the security scan, which could not object to a write it had not been
  asked about yet. That one was found while fixing the reported one.

- **A record id can no longer address a file outside its own store.** Projects, task lists, tasks,
  task comments, learning proposals, skill proposals and attribution records were each stored as a
  file named by putting an id into a directory — and in Python that expression is not a join: hand it
  an absolute path and the directory is discarded entirely. So a URL could name any file on the disk.
  Measured, not theorised: deleting a "project" removed an arbitrary **directory and everything under
  it**, and reading or deleting a "task" reached any `.json` file on the machine.
  **The refusal lives in the store, not at the door.** The tools, the workflow actions and the CLI all
  reach these stores without passing through an HTTP handler, so a check on the way in would have left
  three ways around it. One resolver now owns the question for every store, which is also what makes
  the next store inherit the answer instead of having to remember it.
  **A refused id says so, instead of looking like a missing record.** These stores answer a read they
  cannot complete with "not found", which is how this survived being looked for — a rejected path and
  a file that isn't there were indistinguishable. A malformed id is now a `400` naming the parameter
  it came from, and the refusal is deliberately built so those "not found" fallbacks cannot swallow it.
  **What changes for you:** an id containing a `/`, a `\`, a `..`, or more than 200 characters is
  refused. No id PersonalClaw has ever generated looks like that, so ordinary use is unaffected.

- **An argument a tool call carries can no longer lower that call's risk.** `command` is an ordinary
  argument name, and the approval gate read it out of *any* tool's arguments to answer "what shell
  command is this call going to run?". So a destructive tool that happened to carry one was judged by a
  string that was never going to be executed: deleting a workflow definition, with `command: "ls"`
  alongside it, resolved to **safe** and was auto-approved with no prompt under "trust reads".
  **The same string also unlocked Ask and Plan mode**, whose entire promise is that nothing is
  executed. That half was not in the report; it turned up because a test asserting "this tool is
  denied in Ask mode" is a different assertion from "this tool needs approval", and both were wrong.
  **One question, asked in one place.** "Is this call a shell invocation?" now has a single answer that
  every gate consults, recognising all three ways a shell call actually arrives — the agent's declared
  kind, the tool's own name, and the `Running: <command>` title an agent sends when the command rides
  inline. Anything else is a tool whose arguments are data.
  **Your read-only `bash` still doesn't prompt.** That is what "trust reads" is for, and the shell path
  is unchanged: `ls` is still safe, `rm -rf` is still destructive, and both still reach the same gate.
  What changed is that a tool which runs no shell can no longer borrow the answer.
  **One thing got stricter on the way:** a shell call whose command text never reached PersonalClaw
  used to be treated as a read, because "bash" carries no dangerous-sounding word in its name. It is
  now treated as a command nobody has read, which means it is shown to you rather than assumed safe.

- **Ways *in* now share one gate instead of each inventing their own.** The read-only MCP endpoint
  used to be the only inbound surface, and it carried its own answer to "am I allowed to serve
  this?". Four more surfaces are on the way, so that answer moved into one place they all pass
  through — and it got considerably stricter on the way.
  **Four switches stack, and any one of them says no.** A master switch that takes every inbound
  surface down at once; a per-surface switch; a per-client switch, so you can cut off one
  integration without turning off the surface it uses; and the guardrails incident flag, which
  suspends every inbound request while unattended work is paused. All four are re-read on every
  request, so turning one off takes effect on the next call — never on the next restart.
  **Every one of them fails closed.** A missing, corrupt, or unreadable switch reads as *off*, an
  unreadable incident file reads as *active*, and a client registry that will not parse
  authenticates nobody. This is the opposite of how a *guard* flag behaves, and deliberately so: a
  guard that cannot read its flag must keep protecting, while a door that cannot read its lock must
  stay shut.
  **A caller is now a client, not just a token.** Each integration gets its own record with a label
  and its own bindings — which surfaces it may reach, which agent, which tools, what scope — and
  those bindings are **pins, not defaults**: a request that asks for something it is not bound to is
  refused and logged, never quietly given the bound value instead. Only a hash of each token is
  stored, so revoking a client is deleting its record. Each client has its own rate ceiling, and one
  that keeps hitting it is disabled automatically with a notification, rather than being throttled
  forever in silence.
  **And everything that comes back out is fenced.** Content returned to an external caller is
  wrapped as data with its origin attached — down to which client asked — so a model reading it
  cannot mistake it for instructions. Every request, allowed or refused, is written to an audit
  trail, and the security-relevant ones also reach the security event log.

- **Inbound settings and tokens moved, and old ones stop working.** The config section is now
  `external_access` rather than `inbound`, so `personalclaw config set inbound.mcp.enabled true`
  becomes `personalclaw config set external_access.mcp.enabled true` — plus the new master switch,
  `external_access.enabled`, which must also be on. Surface tokens now live in the credential store
  (your keychain, or `.env` at `0600`) instead of a bespoke `.inbound_<surface>_token` file, so an
  existing MCP token needs re-minting with `personalclaw inbound token create mcp`. A token is also
  now refused if it equals *another* surface's token — five surfaces sharing one bearer would
  collapse five separately revocable credentials into one. `external_access.public_url` and
  `allow_remote` are not editable from the dashboard at all: the endpoint refuses them rather than
  ignoring them, because a security boundary that moves on one request is not a boundary. Client
  records join snapshots and exports; the request audit trail deliberately does not.

- **A `.env` reached a file checkpoint through a symlink.** File checkpoints — the backups behind
  `/rewind-to-turn` — never copy credential-shaped files like `.env`. That rule was applied to the
  name the agent wrote to, so a file called something harmless that was really a link to your `.env`
  was copied anyway, secret and all, into a store that lives under your home and travels in
  snapshots. The check now looks at what will actually be read, not just what it is called, which
  also closes the same trick pointed at `~/.aws/`. Nothing else changes: an excluded file is still
  reported in the rewind preview as "not captured" rather than silently skipped.

- **A rewind now refuses to write outside the workspace it belongs to.** Restoring files is the one
  place PersonalClaw writes a previously recorded path back to disk, so it no longer takes that path
  on trust: a destination that resolves outside the session's own workspace — through a `..`
  component or a symlink planted out of the tree — is refused and reported, and a rewind that
  refused anything is reported as incomplete rather than as a success. Files inside the workspace
  restore exactly as before.

- **An app can no longer change the version of a library PersonalClaw itself depends on.** Apps are
  allowed to bring their own Python packages, and they are installed into the same environment
  PersonalClaw runs from — so until now an app could declare, say, an older `numpy` than the one
  running underneath you, and installing it would quietly swap that library out from under a live
  gateway. Now the install is refused before anything is downloaded, and the message names the
  package, the version you are actually running, and the version the app asked for, so you can see
  the exact disagreement. Nothing you already have installed is affected: an app may still bring any
  library PersonalClaw does not itself depend on, which covers every one of the provider apps that
  ship with it — the AI provider SDKs are optional extras, not part of the core set. If the check
  cannot prove a request is safe (a version specifier it cannot read, or a package whose installed
  version it cannot determine) it refuses rather than guessing. What is still true, and now written
  down under Security limitations, is that an app can *add* packages to that shared environment, so
  install apps you trust.
- **Credentials can now live in your OS keychain, and Doctor tells you where they actually are.**
  Set `PERSONALCLAW_CREDENTIAL_BACKEND=keychain` and new secrets go to the macOS Keychain, Linux
  Secret Service or Windows Credential Locker (`pip install 'personalclaw[keychain]'`) instead of
  `~/.personalclaw/.env`. Reading is unchanged everywhere — nothing you use has to know which store
  answered. On a headless box with no secret service the request **falls back to `.env` at mode
  0600**, never to a plaintext file somewhere else and never to looser permissions, and Doctor says
  so rather than claiming a keychain you don't have. Doctor reports the store that is actually
  holding your secrets, not the one you asked for.
- **Unattended automations now run read-only by default, and you're asked before one runs scripts
  in a project folder.** Three changes narrow what work runs while no one is watching. A background
  *research* spawn — the kind a cron or an agent fires with no human present — now starts in a
  read-only capability class: it can read, search and fetch, but a write or execute tool (including
  a shell) is refused at the tool-approval layer unless the automation was created with an explicit
  write grant (`capability: mutating` on the trigger). A read-only research run that quietly gains
  write is exactly the escalation this closes. Second, when a finished background run hands its
  result back into an unattended session for follow-up, that turn no longer blanket-auto-approves
  whatever tool it reaches for — it resolves through the one safety-profile path the rest of
  unattended work already uses, so the security hooks screen it; an interactive chat, where you are
  present, is unchanged. Third, the first time an automation wants to run scripts in a project
  folder it stays in **Preview** — read-only, no script execution — and asks you to **Trust** the
  folder; the decision persists (`project_trust.json`, keyed by the resolved directory), so it asks
  once, and only Trust lets it write or run project scripts there. Manage it at
  `POST /api/guardrails/project-trust`. **Honest limitation:** these bound what an *unattended* run
  may do by default; a run you explicitly grant `mutating`, or a folder you Trust, has the access
  you gave it — the point is that it is a decision you made, not a default it inherited.
- **The built-in command denylist now repairs itself.** The 112 always-on patterns that refuse
  credential exfiltration, destructive commands and self-tampering used to live only as a list
  inside a Python module. Anything running in the same process — a stray monkeypatch, an agent that
  talked a tool into editing module state, a future refactor — could shorten that list, and every
  command screened afterwards would quietly obey the shorter version. The patterns now ship as a
  packaged data file with a sha256 over their exact contents and order, and every read re-checks the
  live list against it: a shortened, edited or reordered denylist is restored before the next command
  is screened, and the repair is written to your security event log as
  `baseline_denylist_reasserted` (with which patterns came back). A refused attempt to shrink the set
  is logged as `baseline_denylist_tamper_attempt`. Your own additions in
  `security.denied_commands` work exactly as before — they are appended, deduped against the
  built-ins, and can only ever make the list longer. `personalclaw doctor` gained a
  **Baseline command denylist integrity** row that re-verifies the packaged file and reports a
  divergence without ever adopting it, so a tampered file cannot shrink what is enforced.
  **Honest limitation:** this is protection against drift and against an agent tampering at runtime,
  not against you. Anyone who can edit the installed package before the process starts can change
  the baseline — it is your machine.
- **Settings → Security now shows which denylist is actually protecting you.** The panel listed 112
  patterns without saying where they came from, so there was no way to tell a healthy instance from a
  drifted one. It now shows the baseline's version, the sha256 recorded at release, how many patterns
  are enforced, and whether the packaged file still matches — and if it no longer matches, the quiet
  status line becomes an alert that says so while confirming the verified patterns are still being
  enforced. The baseline is read-only on this surface by design: there is no control that can edit,
  reorder or remove one of its patterns. Your own list is now summarised as **"N user additions"**
  counted from what actually takes effect, so an entry that merely repeats a built-in is reported as
  adding nothing instead of inflating the number. The panel states the same honest limitation the
  release notes do — the check proves the patterns match what shipped, and anyone who can edit the
  installed package before PersonalClaw starts owns the baseline; the full statement lives in
  `docs/security/threat-model.md`. A failed read of either the posture counts or the denylist now
  renders an error with the server's message and a Retry, replacing a silent empty list that was
  indistinguishable from "nothing is blocked".
- **Installed apps' backends no longer inherit PersonalClaw's environment.** ⚠️ **This changes
  behaviour for an app backend that read a variable it never declared.** An app with a backend runs
  it as a subprocess, and that subprocess used to start from a full copy of the gateway's own
  environment — which, because PersonalClaw deliberately puts your `.env` credentials there so
  "trusted children" can see them, meant every installed app's backend could read every credential
  you had configured, plus (measured on a real gateway) about **130** other variables it had no
  declared need for, including your SSH agent socket, AWS settings and your git identity. An app
  backend is the least-trusted long-running process in the system — third-party code, scanned but
  not trusted at install — so it now gets the same *minimal* environment hooks and cron scripts got
  in the previous release: `PATH`, `SHELL`, `PWD`, `TERM`, `PYTHONPATH`, your locale and `TZ`,
  `HOME`/`TMPDIR`/`USER`/`XDG_*`, your proxy and CA-bundle settings, and
  `PERSONALCLAW_HOME`/`_WORKSPACE`/`_PORT`. The four variables the app contract promises are
  unchanged: `PORT`, `PERSONALCLAW_APP_NAME`, `PERSONALCLAW_APP_SECRET`, and
  `PERSONALCLAW_APP_DATA_DIR` for an app that declares the `storage` permission. Everything else is
  withheld. **All 44 first-party apps were booted against this change and are unaffected** — the two
  that ship a backend (Growth, Minutes) read only `PORT` and `PERSONALCLAW_APP_DATA_DIR`, and the
  model/search apps read their API-key fallbacks in the gateway process itself, not in a backend, so
  a key exported in your shell still works for them exactly as before. **If a third-party app's
  backend stops working for want of an environment variable, you have two ways to fix it:** move the
  value into PersonalClaw's credential store and configure it on the app's instance (the supported
  route — every first-party model app treats the environment variable as a *fallback* to a
  configured credential), or, if the app genuinely needs the ambient variable, name it in
  `sandbox.env_passthrough` (`personalclaw config set sandbox.env_passthrough '["MY_VAR"]'`). Note
  that `sandbox.env_passthrough` is global — a name declared there is visible to your hooks and cron
  scripts too, not just to app backends — and that credential-shaped names (`AWS_SECRET*`,
  `AWS_SESSION*`, `SSH_AUTH_SOCK`, `GNUPGHOME`, `GIT_ASKPASS`) stay refused even if you declare
  them. To see what a backend is missing, run the gateway with `--verbose`: each backend launch logs
  the names it withheld.
- **A scheduled Python script can no longer exhaust PersonalClaw's file descriptors.** A
  `run-script` cron ran inside the OS sandbox with a minimal environment, but with no cap on what
  it could *consume* — so a script that leaked open files could starve the gateway of descriptors,
  something an agent `bash` command has been unable to do for a while. Scheduled scripts now run
  under the same resource ceiling as every other agent-driven child: the `sandbox.nofile` limit
  (default 4096) applies to the script and everything it starts. A script that hits it gets an
  ordinary `OSError`/`EMFILE`, which surfaces in the job's run history. If you have a legitimately
  descriptor-hungry script, raise the limit with `personalclaw config set sandbox.nofile 16384`.
- **The Store now tells you which other apps an app may message.** An app can declare that it may
  send messages to other installed apps, and PersonalClaw really does enforce that list: a brokered
  message is the only way one app can reach another, and a target the app did not declare is refused
  and written to the security log. But the Store never showed you the list. Installing an app that
  declared it could message your mail and notes apps looked identical to installing one that could
  message nothing — the permission was enforced behind your back rather than consented to. Both the
  install-consent panel and the installed-app panel now name each target among the permissions the
  gateway enforces. A wildcard target is spelled out rather than shown as-is, because it grants more
  than it looks like: an app declaring `mail-*` reads as "any app whose name starts with mail-",
  which covers apps you have not installed yet, and `*` reads as "any installed app". An app that
  declared no target is stated too — "App messaging: none — it declared no target, and the gateway
  broker is the only way one app can reach another, so it can message no other app" — so silence is
  never left to your imagination. Nothing about what an app can do has changed; the enforcement was
  already there and is unchanged. No first-party app declares this permission today, so nothing in
  your Library will start showing a messaging row.
- **The Store no longer implies PersonalClaw confines an app's network access.** An app's manifest can
  declare a `network` permission, and the Store used to list it as a bullet under "Permissions"
  alongside storage, scheduled jobs and background agents — all of which the gateway really does
  enforce. This one it does not, and cannot: an app's code runs inside PersonalClaw's own process (or,
  for the handful of apps that ship a backend, as an ordinary OS process of its own), so there is no
  point at which the platform can intercept the app's outbound traffic. The misleading half was the
  quiet one: an app that declared `network: false` showed **no** network row at all, which read as a
  guarantee that it had been blocked — including for the only two first-party apps that ship a
  backend, Growth and Minutes, both of which declare exactly that. The app detail and install-consent
  panels now show the network claim *outside* the list of permissions the gateway enforces, marked
  advisory, and show it whether or not the app declares one: "Network access: declared / not declared
  — advisory only. PersonalClaw does not confine an app's outbound traffic: this app's code can reach
  the network either way. The declaration is disclosure, not containment." Nothing about what an app
  can do has changed — only what the interface promises. The controls that really do bound an installed
  app are unchanged: its `api` permission bounds what it may ask the gateway for, and the supply-chain
  scanner still gates what you can install in the first place.
  Platform note: on Linux the same mechanism also carries the optional `sandbox.max_pids` and
  `sandbox.max_rss_mb` bounds (both off by default) plus the OOM-killer preference that protects
  the gateway; on macOS only the descriptor limit is enforced.
- **Your hooks and cron scripts no longer inherit PersonalClaw's environment.** ⚠️ **This changes
  behaviour for any hook, cron script or bash action that read an inherited environment variable.**
  A hook command, a `run-script` cron script and a bash action used to start from a copy of the
  gateway's own environment with a few names filtered out — measured on a real gateway, that was
  **121** variables, and PersonalClaw deliberately puts your `.env` credentials in there so
  "trusted children" can see them. A one-line hook (`printenv`) could read them. Those children now
  get a *minimal* environment built from a fixed list instead: `PATH`, `SHELL`, `PWD`, `TERM`,
  `PYTHONPATH`, your locale and `TZ`, `HOME`/`TMPDIR`/`USER`/`XDG_*`, your proxy and CA-bundle
  settings, and `PERSONALCLAW_HOME`/`_WORKSPACE`/`_PORT` — plus, for a hook, the
  `PERSONALCLAW_HOOK_EVENT`/`_CONTEXT` variables and the trigger's `$variables` exactly as before.
  Everything else is withheld. **If a script of yours needs one more variable, name it in
  `sandbox.env_passthrough`** (`personalclaw config set sandbox.env_passthrough '["SLACK_BOT_TOKEN"]'`,
  or the config API) — for example a Slack token for a notifier script, or a language runtime's
  variable. Credential-shaped names (`AWS_SECRET*`, `AWS_SESSION*`, `SSH_AUTH_SOCK`, `GNUPGHOME`,
  `GIT_ASKPASS`) stay refused even if you declare them. To see what a script is missing, run the
  gateway with `--verbose`: every spawn logs the names it withheld. Unchanged: cron scripts still
  receive PersonalClaw's internal secret and port through the temp file they always did, so scripts
  that call back into the API keep working.
- **Scheduled, file-watch, webhook and chained automations now honour the action denylist — they
  never did.** ⚠️ **This changes behaviour for automations that already exist.** The denylist that
  refuses an automated action touching a credential path or running a destructive/exfiltrating
  command was enforced when a *script hook* or a *memory-event trigger* fired an action, but not on
  the busiest path of all: the one every clock, file-watch, webhook and chained trigger dispatches
  through. That seam had the incident kill switch and the autonomy ladder, so the omission was easy
  to miss — the denylist was lost when the old scheduled-job dispatcher was retired and its
  replacement was never re-wired. From this release all three dispatch paths enforce it, which also
  means an action contributed by an installed **app** inherits the denylist wherever it is fired.
  A refused fire does not run, is recorded in the automation's run history as a **skipped gate**
  (not a failure, so it will not count toward auto-pausing your automation) naming the rule that
  matched, is written to the security event log, and — for a `needs_human` rule — raises a
  notification. What this can affect, measured before shipping: only `bash` (its `command`),
  `run-script` (its script name) and `run-prompt` (its `cwd`) carry a field the denylist inspects;
  the other shipped action types are unaffected. If one of your scheduled commands stops running,
  the likely built-in patterns are `rm -rf ~…` / `rm -rf /…` and `git … push` — the same patterns
  the assistant's own shell tool has always refused, which is the point: this is not a new policy,
  it is an existing one that one seam was skipping. Nothing new is configured by default
  (`security.autonomy_denylist` stays empty); to allow a command that is being refused, adjust the
  command rather than the guardrail.
- **A governance ceiling an operator writes once now bounds every unattended run — and the safety
  profile it bounds is finally read at all.** PersonalClaw already described each run's posture in a
  `SafetyProfile` (approval, tool grants, egress tier, path denies, budget, secret-scan mode), but
  nothing consulted the tier/grants/denies, and every automated dispatch seam — clock, file, webhook
  and chained triggers, memory-event triggers, and script hooks fired without a parent session —
  asked for its posture with an *empty* session identity, which classified as "a human is watching"
  and resolved the **interactive** posture. Automated work has been running under the interactive
  profile. Now: those seams identify themselves as unattended, so they resolve the `headless`
  posture, and an optional operator file at `$PERSONALCLAW_HOME/governance/ceiling.json` (or an
  absolute path in `PERSONALCLAW_CEILING_FILE`) sets a hard bound that a run can only make
  *stricter* — never looser. Six governed scopes (`approval`, `scan`, `egress`, `paths`, `tools`,
  `budget`); for example `{"version": 1, "scopes": {"approval": {"value": "ask"}, "paths":
  {"mode": "closed", "allow": ["~/workspace/**"]}}}` means nothing on this machine auto-approves and
  automated actions may only touch your workspace. **No file means no change**: absent a ceiling,
  behaviour is exactly what it was. A malformed one is a hard stop with a WHAT/WHY/FIX message
  rather than a silent start, because "governance could not be established" is not a degraded mode.
  The file is deliberately not editable through the app (it is absent from the config PATCH
  allowlist and its directory is on the built-in sensitive-path denylist, so the agent's own write
  paths refuse it), it is read once at start-up so an edit cannot widen a running gateway, and every
  clamp is logged and written to the security event log. What it cannot do is stop a process running
  as you from editing the file and restarting — for a real trust root, point
  `PERSONALCLAW_CEILING_FILE` at a root-owned `0444` file outside your home. See
  `docs/architecture/security.md`.
  Two effects of the seam correction you may notice even with no ceiling file, both by design: an
  automation whose action type is allowed to run fully on its own now runs with the **undo handle and
  the passive "this happened" notification** kept (the "runs on its own, silently" rung is reserved
  for work a human is watching, since nobody is there to notice otherwise) — it still runs, it is
  just no longer invisible; and outbound secret scanning for automated runs follows your configured
  mode (redact by default) instead of warn-only.
- **An egress "allow-list" now actually restricts.** `allow_hosts` only ever *waived* the
  private-address block, so the `registry` and `listed` egress tiers reached every public host
  exactly like the default — a limit in name only. Policies can now be exclusive (only listed hosts
  are reachable, checked before DNS resolution), which is what lets a run's egress tier — or a
  ceiling of `{"egress": {"value": "listed"}}` — genuinely confine outbound traffic. The agent's web
  fetch and watched-source polls both honour it, and a tier of `off` refuses the request with a
  visible reason instead of making it.
- **A watched-source poll now honours your denied hosts on the headless-browser tier too.** The
  JavaScript-rendering tier passed a hardcoded policy, so `Security → Network` deny-hosts applied to
  the plain fetch and were ignored on the render path. Both tiers now use the same resolved policy.
- **An auto-approval grant for a spawned subagent can be refused by the ceiling.** The trust toggle,
  `--approval yolo`, an `approval_mode: auto` caller and the config default could each widen a
  subagent to auto-approve tool calls; none of them consulted an operator bound. With a ceiling of
  `{"approval": {"value": "ask"}}` the grant is refused and the refusal is audited. Without a
  ceiling file, the toggles behave exactly as before.
- **Path rules are matched correctly.** The action denylist compared paths as strings without
  anchoring them, so a relative path like `../../etc/passwd` could slip past a deny of `/etc/**`,
  and `**` was treated as a single-level `*`, so `~/.ssh/**` missed `~/.ssh/sub/key`. There is now
  one matcher: the queried path is expanded and absolutized, patterns are never rewritten, and `**`
  crosses directories.

- **App backends now authenticate inbound requests, closing a direct-to-port bypass.** An app's
  backend subprocess binds on loopback (`127.0.0.1:<port>`), which is a network boundary, not an
  authorization one — before this change any local process that found the port could talk to the
  backend directly, bypassing the gateway proxy and therefore session auth and the app-permission
  middleware. Every request the proxy forwards now carries an HMAC signature
  (`X-PersonalClaw-Proxy: <ts>:<hmac>` over `<ts>:<METHOD>:<path?query>:<sha256(body)>`, ±60s
  replay window, constant-time compare) keyed by a per-app 256-bit secret minted 0600 at
  `apps_dir()/<app>/.app_secret`. The backend verifies it **fail-closed** via the new
  `personalclaw.sdk.security.require_proxy_signature()` middleware — no/stale/bad signature ⇒ 401
  before any route runs; a backend that cannot obtain a verifiable secret does not start. `/health`
  is exempt so the watchdog can probe it. Both first-party backends adopt the middleware. This
  proves a request came from the gateway proxy; it does not encrypt loopback traffic or defend
  against a process that can read the root-only secret file (see
  `docs/architecture/app-platform.md`).

### Fixed

- **Generated documents no longer show up in your library as broken images, and a generated PDF
  finally previews.** A Word document, spreadsheet, deck, PDF or video that PersonalClaw made for
  you had its card drawn as a picture — so the grid showed the browser's torn-page glyph for a
  file that had been created perfectly. Those cards now show the icon for what they actually are,
  in that format's own colour, while an image artifact still shows its real thumbnail. Opening a
  generated PDF used to show nothing at all: the viewer knew how to display a PDF sitting in your
  files but not one the agent had just produced, and quietly came up empty. It now displays either
  one, and if a PDF genuinely cannot be found it says so instead of offering to open a file that
  is not there. An artifact of a kind the app does not recognise now reads "Unknown kind" rather
  than borrowing the name of a real one — that impersonation is why every generated document was
  labelled "Widget" for four releases with nothing anywhere reporting a problem.
- **The Loops page no longer tells you that you have no loops when it simply could not load
  them.** If the request failed, the page said "No loops yet" and invited you to start your
  first — the most confident possible way to say the opposite of what happened, to someone whose
  loops were fine and merely unreachable. It now says "Couldn't load your loops", shows the
  server's own reason, and gives you a Retry that puts the list back. The Memory audit log had a
  smaller version of the same problem: one sentence, "No matching events", served both a log that
  recorded nothing and a filter that matched nothing. Those are different facts and now read
  differently.
- **A security-audit write that fails is no longer swallowed.** When the subagent reaper
  force-kills a subagent that blew its deadline, it writes one security-event row — the only
  record that the kill happened. That write sat inside a catch-all `except`, so on a home that
  could not be written (read-only, full, permissions) the kill went ahead and the audit row was
  lost with nothing raised: an unauditable kill that looked identical to an audited one. The
  failure now surfaces (logged per-agent by the reaper sweep, which continues with the other
  agents) rather than being absorbed. Every other audit write on this path already behaved this
  way; this one was the exception.
- **Developer-facing: the test suite no longer leaks SQLite handles, and the self-dev harness works
  in a git worktree.** A full run printed ~1,600 `unclosed database` resource warnings from 95 test
  files — every sqlite-backed store was built by a fixture and never closed — and closing them
  exposed a real isolation bug: the process-wide knowledge store was memoized across tests, so
  tests were searching an earlier test's database. Separately, the harness resolved its interpreter
  as the cwd-relative `.venv/bin/python`, which does not exist in a worktree, so
  `python -m harness validate` could not collect the suite there and three of its tests failed in
  every worktree.

- **A lesson saved for one project no longer becomes a rule for every project.** The
  `memory_remember` tool offers `scope: "workspace"` and the workspace-identity prompt block
  promises such a lesson is "only visible in this working directory" — but `POST /api/lessons`
  read neither the scope nor the workspace name, and neither `write_lesson` layer had a scope
  parameter, so the lesson was stored **global** and the caller was told `ok`. A workspace lesson
  is now stored against the working directory it was taught in (`realpath`, matched exactly), and
  is injected only into sessions running in that directory. Global lessons are unchanged: every
  lesson saved before this release is global and still applies everywhere, and no migration runs.
  The endpoint now **refuses instead of downgrading** — `scope="workspace"` without a workspace, a
  workspace that is not an absolute path, or an unrecognized scope each return 400 rather than
  quietly writing a global lesson. Your lesson list still shows every lesson (and now says which
  workspace each belongs to), so a workspace lesson stays visible to manage and delete;
  `GET /api/lessons?workspace=<absolute path>` asks for one directory's view.

- **`until_dry` workflow loops now end when the work reports no progress, instead of always running
  to their iteration cap.** A loop can declare which field of its iteration output counts as
  progress (`progress_field`), and two shipped templates do — `goal-pursuit-open-ended`
  (`new_findings_count`) and `general-project` (`meaningful_progress`) — but the engine never read
  it. Dryness was measured over the *whole* output of the last node in the iteration, which in both
  templates is a judge stage returning a populated JSON object; so a cycle that honestly reported
  `new_findings_count: 0` still counted as progress, the "two clean cycles in a row" streak never
  completed, and the run paid for a model call per iteration up to `max_iterations` (12 and 6) to
  learn nothing. A declared field now decides, wherever in the iteration it was emitted: zero,
  false, blank, empty or null means the cycle surfaced nothing new; anything else is progress. Loops
  that declare no field — the majority, including `audit-sweep` and `deep-research` — keep the
  previous whole-output rule unchanged. A declared field an iteration did not emit also falls back
  to that rule rather than counting as dryness: ending a run because the body forgot a key would
  silently truncate real work, which is worse than paying for one more iteration. `streak` is
  unchanged and still means N *consecutive* dry iterations.
- **Run history no longer says "ran" for automations that did not run.** The run feed translates
  each store's own status word into a typed outcome, and four of the statuses actually being written
  had no entry in that translation — so they landed on a fallback nobody had chosen for them. A
  lifecycle hook that only *launched* background work, and a hook the incident kill switch stopped
  **before** it reached its action, both showed as "ran"; a payload the injection screen blocked, and
  every fire suppressed by quiet hours, a budget cap, an overlap or a triage decision, all showed as
  a red "failed". Each now shows what it was: `deferred` ("outcome not yet known"), a neutral grey
  suppression that folds into the archived half of the feed with its reason, and the shield-marked
  `blocked`. Suppressed and screened rows are also marked as ledger entries rather than openable
  runs, since neither ever reached a runner. This was a reporting fix only — the trigger
  autopause counter reads the stored rows directly and was never affected, so no automation's
  pause/resume behaviour changes. A status the build cannot classify is now logged by name and never
  reported as a success.

### Changed

- **Knowledge search now finds the passage, not just the document — and tells you which passage.**
  Semantic search used to compare your query against one vector per item, built from the item's title
  and summary, so an answer buried on page 12 of a long document was effectively invisible to it: the
  document either matched as a whole or not at all. Search now compares your query against the
  individual passages the library indexes for each document, and scores each document by its single
  best-matching passage. Two things change for you. Long documents become findable by what is *inside*
  them, which is where most of a real library's value sits. And a result that matched semantically now
  carries a citation — the section heading and line range of the passage that actually matched — where
  before, if none of your literal query words appeared in the text, the result could only name the
  document and point at its top. A document with no indexed passages yet (anything added before this
  release, until it is re-indexed) still matches the way it always did, so nothing becomes unfindable
  in the meantime. Ranking, the relevance-cliff cutoff and the similarity threshold are unchanged;
  only the evidence feeding them got better. Indexing many passages per document also means many more
  vectors to compare, so this release pairs the change with the index described next.

- **Semantic search on a large library got about twenty times faster.** Comparing your query against
  every indexed passage was done one vector at a time in Python, which is fine for a few hundred notes
  and slow for a real library — on a 300-document library with five passages each, measured at roughly
  40 ms of vector work per query, growing in a straight line from there (a 5,000-document library would
  have spent over half a second on every search). PersonalClaw now keeps a vector index inside the same
  knowledge database file and asks it for the nearest passages instead of reading them all: the same
  measurement drops to about 2 ms, and the results are **identical** — same documents, same order, same
  citations — because the index only narrows down which passages to score, and the scoring, the
  similarity threshold, the ranking and the relevance-cliff cutoff are the ones you already had. If
  your Python's SQLite cannot load extensions, search keeps working exactly as before at the old speed;
  it says so once in the log, and `personalclaw doctor` shows a line explaining why search is slower
  rather than leaving you to guess it is broken. Libraries indexed before this release are brought into
  the index automatically on the first search after upgrading.

- **The library you already have becomes searchable by content, without you doing anything.** The two
  changes above only help documents PersonalClaw has broken into passages, and it only started doing
  that on the way in — so everything you added *before* it kept matching the old way, on its title and
  summary alone. That was the gap: on a library of six long documents, asking "how far behind can a
  replica get before it stops serving reads" returned one result, the document whose *title* was the
  closest match and which did not contain the answer anywhere, with no citation, while the document
  that actually answered it was not returned at all. PersonalClaw now indexes the passages of your
  existing documents in the background, starting the next time it launches, and after that the same
  question returns the right document first and cites the section that answers it — the heading and the
  exact lines. Three things about how it does that. It works in small batches, so a library of any size
  costs the same in memory; it can be interrupted at any point — quit, crash, power cut — and picks up
  exactly where it stopped, without redoing or skipping a document; and searching *while* it is partway
  through is safe: documents it has not reached yet keep matching the way they always did, so nothing
  becomes unfindable in the meantime. Running it a second time does nothing, by design. It reads your
  documents and adds passages; it never rewrites anything you can see, and it leaves the whole-document
  index alone. Expect the database to grow — measured on a 300-document library, indexing 2,100
  passages took it from 1.5 MB to 11 MB and took about nine seconds. If no embedding model is available
  yet, it says so in the log and waits for the next launch.
- **Anthropic models now reuse the stable head of a conversation instead of re-reading it every
  turn.** Anthropic bills prompt content it has already seen at a fraction of the normal input
  price, but only when the request marks where the reusable part ends — and PersonalClaw never sent
  that mark, so every turn paid full price for the same assembled context, memory and skills. It is
  sent now, on the last piece of stable content in each request, which means from the second turn of
  a conversation onward that whole prefix is billed at the reduced rate and comes back faster.
  Nothing about *what* the model is told changes: the same words in the same order, just flagged as
  reusable. Only Anthropic-family models are affected — providers that cache on their own
  (OpenAI-compatible endpoints) already benefited from the prompt reordering in the previous
  release, and a provider with no cache support sends byte-for-byte the request it sent before.
  Anthropic reports how much of each request it served from cache; showing that back to you as a
  per-turn saving is still to come.
- **The Retro Terminal and Claw Arcade personalities now skin the error surfaces too.** Picking one
  of the two shipped personalities in Settings → Design changed the palette, the wordmark and the tab
  title, but a failed page and the incident banner still looked exactly like the default identity —
  the two moments where an identity is most noticeable stayed generic. Retro Terminal now draws them
  as a hard-edged mono-type terminal frame and Claw Arcade as a dashed cabinet panel. This is a
  **skin and nothing else**: the wording, the Retry and Resume buttons, and the fact that the incident
  banner announces itself to a screen reader are byte-for-byte the same under every personality, and
  each treatment's colours are checked against WCAG AA in both light and dark before it can ship
  (worst measured pairing 4.98:1). If you use a standard colour scheme — which is everyone who has
  not deliberately picked a personality — **nothing changes**: both surfaces render exactly the markup
  they did before, asserted against the previous release's output rather than promised.
- **The Retro Terminal personality now lays a CRT raster over the whole shell.** Picking it in
  Settings → Design recoloured the app and renamed the wordmark, but the shell itself still looked
  like an ordinary web app — the one thing a terminal identity is supposed to feel like was missing.
  It now draws a fine lattice of scanlines across the app with a single soft band travelling slowly
  down it. The raster takes its ink from the active colour scheme rather than a fixed green, so it
  belongs to whatever palette the personality names. **If you have Reduce Motion turned on, the band
  is not drawn at all** and you get the still raster — and turning the system setting on or off takes
  effect immediately, without reloading. The overlay cannot be clicked and is invisible to screen
  readers, and it deliberately sits *under* dialogs, toasts and the update overlay, so anything asking
  you to make a decision stays crisp. On any standard colour scheme — everyone who has not
  deliberately picked a personality — **nothing renders and nothing downloads**: the overlay ships as
  its own small chunk that is only fetched when a personality that uses it is active.

- **A goal loop's judge verdict now shows you what the supervisor checked for itself.** When a loop
  declares something checkable — a verify command, or a named deliverable file — PersonalClaw does
  not take the worker's word that it worked: it runs the command and reads the file itself, and
  weighs that over the worker's account. It has done so for a while, but only the *judge* saw the
  result; you got a one-line reason and no way to tell whether it rested on an independent
  observation or on the worker's own narration. A cycle's verdict panel now lists what was
  independently observed — the command it ran and what that command returned, and each deliverable
  it read — so "done" comes with its receipts. A cycle with nothing checkable to observe says
  nothing new, which is the honest answer for a goal that named no anchor. Under the hood this is
  one verdict record instead of three: the loop's private vocabulary was folded into the workflow
  judge contract, so a loop cycle and a workflow gate now describe "was this good enough?" the same
  way, including the marginal-value and regression signals that drive when a loop decides it has
  stopped making progress. Verdicts stored by an earlier version still display correctly.
- **"Reduce motion" now actually stops the springs — and the Bounciness slider reaches everything it
  claimed to.** If your operating system is set to reduce motion, PersonalClaw relied on a framework
  setting that neutralises movement *across the screen* but leaves the underlying spring running, so
  anything animating opacity or a blur still bounced its way in. Every spring in the app now collapses
  to an instant swap under that setting, in one place, so a new animation cannot escape it. Separately,
  menus and popovers were reading your **Bounciness** value once when the app loaded and then ignoring
  it: moving the slider changed nothing for them until a full reload. They now read it every time they
  open. The app's spring presets are also down to one named set of four — *snappy*, *smooth*, *fluid*
  and *playful* — so motion is consistent between surfaces that used to pick from two overlapping
  lists; a few entrances (dialogs, the update overlay, the composer) are slightly quicker and bouncier
  as a result, and Settings → Design → Motion tunes all of them. **Three new sliders in that same
  group** control drag and swipe feel: how far a dragged card stretches past its edge, and the flick
  speed *or* distance at which a swipe dismisses it (a toast can now be flicked away quickly or hauled
  away slowly — previously only one worked, at values you could not change).

- **Housekeeping now runs when your machine actually needs it, instead of on a fixed clock — and
  one system does it, not two.** PersonalClaw's minute-by-minute heartbeat used to carry its own
  maintenance schedule: rebuild the memory search index every 15 minutes whether or not anything
  had changed, and once a day trim old daily-history files, trim the security event log, and age
  the skill library. The self-healing Maintenance engine on the Doctor page (Settings → Doctor) was
  doing the same kind of work from measured evidence, so the two overlapped. Those four passes are
  now jobs the engine owns and schedules from what it can measure — how many memory files disagree
  with the search index, how many history files are past your retention setting, how many
  security-log entries are past theirs, how many skills are due for aging — so each one runs when
  there is genuinely something to do and is recorded in the Doctor's run history with what it did.
  Two consequences worth knowing. **If you turn the engine off** (`resilience.remediation.enabled`),
  the heartbeat picks all four back up on its original schedule, so you are never left with no
  housekeeping at all. **And the memory search index is now reconciled properly:** deleting old
  history used to leave its text in the search index until the next rebuild, so a search could quote
  a file that no longer existed; the prune now removes both together. Skill *tamper detection* is
  also finally scheduled — a skill whose files changed after installation used to be noticed only if
  you happened to open the Skills page, and is now checked on every maintenance pass and shown on
  the Doctor page (it is reported, not silently "fixed": re-recording a changed skill's fingerprint
  would hide the very change you need to see).
- **A workflow judge now has to show its work, and a PASS it cannot justify is refused.** A `judge`
  gate used to be asked for exactly one word — `PASS`, `RETRY`, `ESCALATE` or `REJECT` — which
  cannot carry a score, a citation or a reason. Every rule the judge contract states ("a PASS
  without cited proof is invalid", "any rubric criterion below its target fails the stage") was
  therefore written down and applied to nothing: on `goal-pursuit-open-ended` the template asked
  for a full verdict object and the engine appended the one-word demand right after it, so the gate
  shipped two contradictory instructions. Judges are now asked for the verdict object and the
  engine validates it: a PASS carrying neither `proof` nor `evidence_refs` is refused, the rubric
  criteria your template declares in `runtime_hints.judge` are compared against their
  `target_score` for the first time, the overall score is recomputed from the dimension scores
  (the model's own number is kept beside it so any drift is visible), the judge's evidence has
  retry/iteration markers stripped before it reads it ("attempt 4 of 5" tells a judge how much
  patience is left), and a gate that opted into `self_judge` now parks for a human instead of
  approving its own work. The six templates whose judge STAGE already produced this object have it
  validated and carried forward — into the review handoff, or into the next cycle's worker prompt
  as the critique to start from — instead of discarded.

  **What you may notice.** Judge answers cost more than they did (an object instead of a word), and
  a judge that replies with prose instead of JSON now fails its gate with a named protocol error
  rather than a guess. Enforcement was deliberately scoped so it cannot break a working template: a
  run that declares no rubric has nothing to fall short of, the prompt spells out the exact score
  keys the check will look for, and a restated key still counts. This is a behaviour change to
  every judge gate and judge stage — `personalclaw snapshot` before upgrading if you have judged
  runs in flight. Templates you wrote yourself that hand-write a "reply with one word" judge prompt
  should drop that line; the engine supplies the shape.

- **A workflow plan now tells you which of its stops survive an unattended run.** The autonomy
  offer routinely recommends `per_stage` while still offering `unattended`, and the plan preview
  listed the confirmations for the *recommended* mode only — so choosing "run it unattended" meant
  giving up an unnamed set of stops. The preview gained an `unattended_interrupts` block naming, per
  confirmation, whether unattended still stops for it and which interrupt fires (`irreversible`, or
  `uninferable` for a credential or payment detail nobody can guess). A credential ask now reports
  as *uninferable* rather than *irreversible* — the same stop, but it tells you to supply a value
  instead of sending you looking for a blast radius. Advisory only: what an unattended run may
  actually do is unchanged, and still decided by the engine's gate policy.
- **A `foreach` with `on_item_error: collect` now has defined behaviour, and it collects.**
  `collect` was accepted by the validator and advertised in the workflow capabilities catalog, but
  no code branched on it: it fell through to the generic container outcome, so it neither halted
  early like `halt` nor tolerated failures like `skip` — a mixture nothing had specified. It now
  means one thing: **run every item to completion, then fail the run if any item failed**, with the
  per-item failures written to the run ledger as one `items_collected` record (item index, label,
  node, failure class and cause). `skip` (the default) and `halt` are unchanged. **If you have a
  spec using `collect`,** its verdict is what it was before — the run still fails when an item
  fails — but the behaviour is now guaranteed rather than incidental, and you can read which items
  broke out of the ledger instead of reconstructing it from per-node events. Use `skip` if you want
  a fan-out's failures tolerated and the run to complete.

- **Security docs now describe what the sandbox actually does — credential-hiding, not
  confinement.** `docs/architecture/security.md` gains an explicit "what the sandbox does and does
  not do" section (no network/process/write confinement beyond `~/.ssh`); the "bounded by
  guardrails" claim is scoped to *unattended* work (interactive chat is never gated); and the
  desktop shell is described as an experimental macOS-only build, not a shipped platform — matching
  what CI actually releases. Documentation only; every change narrows a claim rather than widening
  one.

- **The desktop app can now tell the dashboard what it is actually allowed to do.** The macOS shell
  gained a typed capability bridge — `window.pclawDesktop.capabilities` — covering the microphone,
  screen recording, native notifications, the menu-bar item, a global hotkey and open-at-login. Each
  one can be probed for its real OS permission state and, where macOS allows an app to ask, requested;
  the request raises exactly one system dialog, and a capability already denied routes you to System
  Settings instead of silently doing nothing. Two capabilities are honest about their limits rather
  than guessing: macOS gives an app no way to prompt for Screen Recording and no way to read whether
  notifications are authorized, so those are labelled as such and offer no button that would do
  nothing. On boot the shell registers this manifest with your gateway over loopback, so
  **Settings → Security → Desktop capabilities** shows the truth — and in an ordinary browser tab it
  says "Desktop app not connected" instead of listing native permissions a tab could never grant.
  Apps can reach a capability only by declaring it (`"permissions": {"desktop": ["audio_capture"]}`);
  the gateway mediates every such call, refuses an undeclared one, records the refusal in the security
  event log, and the Store names the capabilities an app asked for before you install it.

- **First run now sets you up with a working model instead of pointing at Settings.** The second step
  of the welcome flow used to be a readiness check: if no model provider was configured it told you
  chat could not run, offered a link to Settings, and left you to find your own way. It is now an
  **Essential apps** step that does the work in place. Four groups are listed from the app catalog —
  a **model provider** (required; nothing else works without one), plus optional **web search**,
  **speech** (transcription/voice) and a **messaging channel** — and for the model you can go from
  nothing to a bound, tested model without leaving the flow: install the provider app, fill in its
  own settings fields, run its real connection **Test**, then pick which model chat should use. If
  the Test fails you see the provider's actual error and can correct it right there; skipping every
  optional group still gets you to a working dashboard. **Nothing installs by itself.** Each app has
  a **Review** step that shows exactly what installing grants it — the same permission disclosure,
  scheduled-job list and scanner warning the Store shows — and installs only when you click that
  card's own Install button. Where you are in the flow, and which of the four you set up, is now
  remembered on the server.
- **Prompt caching is now a switch you can find, in Settings → Models.** Providers that support it
  can be asked to cache the stable front of your prompt — the assembled context that does not change
  from turn to turn — so a long conversation stops re-paying for the same tokens on every turn.
  That was already happening; there was no way to see it or stop it. There is now a **Prompt
  caching** switch beside your model bindings, on by default. It is on by default because caching is
  transparent: the model is shown exactly the same tokens either way, and a provider without cache
  support is unaffected. Turn it off when you are debugging a provider and want caching ruled out —
  nothing else changes when you do. In particular, **what the model is shown and in what order is
  identical either way**: the ordering of the served prompt is a correctness property, not part of
  the caching feature, so the switch does not quietly serve you a different prompt.

- **You can approve what PersonalClaw is waiting on from your phone.** A run that stops to ask
  permission used to stay stopped until you were back at a desk, because the only place the question
  appeared was the desktop dashboard. There is now a phone route at **`#/companion`** — open it on
  your phone (over your tailnet or however you reach your gateway) and it shows every pending tool
  approval with the whole decision on screen: the tool, its **full arguments** (not a truncated
  preview — you should never approve something you cannot read), why it wants to run, which session
  or automation asked, and how long it has been blocked. Allow and Deny are thumb-sized, and the
  answer lands on the same gateway the dashboard talks to, so a run held up by a permission prompt
  proceeds the moment you tap. Two things it deliberately will *not* do: if it cannot reach your
  gateway it says so and offers a retry, rather than showing an empty list that would read as
  "nothing needs you"; and if an answer fails to send, the card comes back instead of quietly
  disappearing. The rest of the companion — running loops, inbox, notifications — is named on the
  page as not built yet rather than shown as empty, and push notifications for approvals are still
  to come. This first release has no app-store app and no offline install; it is a page you open in
  your phone's browser.
- **PersonalClaw can now earn autonomy one action at a time, and lose it instantly.** The safety
  floor used to be binary — an unattended action was read-only, or it was a permission you granted
  when you created the automation — so a reply draft you had approved unchanged forty times still
  asked every time. There is now a per-action-type ladder: **draft only → one tap → run with undo →
  autonomous**, where each action type declares the rung it starts at and a ceiling it can never
  pass (anything that leaves your machine stops below "autonomous" unless its declaration says
  otherwise). The track record behind a promotion is **recomputed every time from what already
  happened** — the approvals in your security event log and the 👎 you have given that action's
  output — and never stored as a score, so it cannot outlive the evidence. Two rules make it safe:
  **PersonalClaw never promotes itself** (clearing the bar files a suggestion; only your click
  grants a rung), and **one rejection demotes immediately** and starts a cooldown before the
  suggestion can come back. `personalclaw incident on` holds every action at "one tap" or below
  until you resume, whatever it had earned. Thresholds live under Settings (`guardrails.autonomy`:
  approvals required, days they must span, rejections tolerated, cooldown, evidence window), the
  grants and demotions are saved to `autonomy_rungs.json` and travel with `personalclaw snapshot`,
  and an unreadable or unrecognized entry grants nothing. This release ships the mechanism; the
  action types that use it, and the ladder panel that shows it, follow.
- **The autonomy ladder now actually decides whether an automated action runs.** Every built-in
  action declares its rung, and an app can declare one for its own action with an `autonomy:
  {floor, ceiling}` block on its provider — which the lifecycle-hook, data-event and
  clock/file/webhook trigger paths all honour. An action held at **draft only** files a proposal
  saying what it would have done; held at **one tap** it files a request for you to decide; at
  **run with undo** it runs and tells you quietly, keeping a handle on what it created; at
  **autonomous** it just runs. Held actions leave a real row in your inbox and a typed entry in the
  automation's history, never a silent stop. Two things an app cannot do: it cannot claim its action
  stays on your machine (PersonalClaw decides that from the network permission the app already
  declares), and it cannot claim the top rung for an action that reaches the network — that request
  is lowered to "run with undo", and both your log and the security event log say so rather than
  quietly overruling the app. Actions that carry no declaration behave exactly as before: the
  denylist, the kill switch and the permission you granted when you created the automation are
  unchanged, and nothing you already run stops running.
- **You can now see, grant and take back what each automation may do on its own.** The autonomy
  ladder had no face: it decided quietly, and there was no way to find out why an action was allowed
  to run unattended, no way to accept a rung it had earned, and — worst — no way to actually undo an
  action that ran at "run with undo", however loudly the notification offered one. Settings →
  Guardrails now lists every governed action with the rung it runs at, a plain sentence saying WHERE
  that permission came from (declared that way · you promoted it on this date, with the record you
  were shown · granted, but held down right now by incident mode), the track record behind its next
  rung, and its demotion history. The same chip rides every row on the Triggers page, so you can
  scan a list of automations and see which ones act on their own. **Promotion is still only ever
  your click** — when an action clears the bar, PersonalClaw files a proposal in your inbox and
  waits; nothing in the system can promote anything, and a request asking for a rung above an
  action's declared ceiling, or during a cooldown, is refused with the reason. Two buttons take
  autonomy back: **Hand back** returns an action to the rung it was declared with, and **Undo** on
  an automatic action's notification (or in the panel) really reverses it — the task an automation
  filed is deleted — **and** stops that action from doing it by itself again. The undo asks the
  provider that created the thing to take it back, since only it knows what "undo" means for its own
  effect, and it works from PersonalClaw's own record of what ran: if that record is gone, the thing
  was already deleted, or nothing installed can reverse it, the undo refuses and says which, and
  crucially leaves the action's earned rung alone — a broken undo request can never be a way to
  quietly degrade what your automations are allowed to do.
- **You can share a chat as a read-only artifact — inside your own instance, never on the
  internet.** Right-click a chat in Chat History → **Share as read-only artifact** and the
  conversation is saved into your artifacts library as a Markdown record, then opened for you. The
  body is the *same* credential-redacted transcript the Export action produces (redaction covers
  your own messages too, which the chat log itself does not), and the artifact is frozen: it can be
  read, downloaded, or deleted, but never edited — a record you can point at, not a document that
  can drift from what happened. It is created only when you ask, on an authenticated request: there
  is no public link, no share token, and nothing shares a chat automatically. Incognito and
  temporary chats refuse to be shared, since an artifact is durable and those chats promise not to
  be. Export is unchanged.
- **A Routing & Efficiency panel in Settings shows which model is efficient for which kind of
  work.** Settings → Routing & Efficiency lets you pick a use case (chat / code & tools / reasoning)
  and a request kind (short chat, code, summarize, extract structured, long reasoning — both
  round-trip the URL) and shows, per model, the real success rate, p50/p95 latency, and cost per
  call for that kind of request, with the ones on the efficiency **frontier** (not beaten on all of
  quality, speed, and cost) flagged and floated to the top. Observation only — it visualizes
  already-recorded telemetry and does not change how requests are routed. A bucket with no data yet
  shows a friendly "fills in as models handle this kind of request" note; a local/zero-cost model
  reads "free," never a misleading "$0.00."
- **A Usage panel in Settings shows what you're spending.** Settings → Usage renders Today / 7-day /
  30-day cost + token totals (the period control round-trips the URL), a by-model and a by-source
  table with each row's share, a cache-savings line, and — when you've set a daily budget in
  Guardrails — a read-only "spent $X of your $Y cap" (automations only; interactive chat is
  uncapped). A period that includes a model with no price row shows a "partial — N unpriced models"
  marker instead of a misleadingly complete figure. Observation only — nothing here caps or throttles
  a turn. This completes cost observability: per-turn, per-conversation, and per-account.
- **The chat header shows what the whole conversation has cost.** A cost chip — e.g.
  `$0.19 · 46k tokens` — appears in the session header once a chat has recorded usage, reading the
  per-turn ledger scoped to that session. A conversation whose models are all priced shows a real
  dollar figure; one that used a model with no price row shows `unpriced` rather than a misleadingly
  precise total.
- **The "Turn complete" line now shows what the turn cost.** When a turn finishes, its telemetry
  line (in the collapsible per-turn details) reports real USD plus in/out token counts — e.g.
  `$0.0123 · 1,200 in / 340 out tokens`. A model with no price row shows `unpriced` rather than a
  misleading `$0.00`, and a cache fragment appears only when the provider actually reported cached
  tokens. Cost is provider-reported when available, otherwise derived from the pricing table.
- **`personalclaw doctor` now reports your SQLite driver and its capabilities.** The Dependencies
  section shows the resolved driver (`pysqlite3` or the stdlib `sqlite3`), its version, and whether
  FTS5 and JSON1 are compiled in — with a fix hint (`pip install pysqlite3-binary`) when FTS5 is
  missing, since the knowledge and memory search paths need it. Under the hood the driver is now
  selected in one place (`sqlite_compat`) instead of seven, so every subsystem shares one honest
  answer.
- **Memory-backed answers cite their sources, and say so when memory is empty.** When a reply
  draws on episodic memory recalled for the turn, it can cite a fact inline as `[Memory N]`, and the
  chat renders each such token as a chip that deep-links to that episode in Settings → Memory. The
  system prompt also instructs the model to answer only from the recalled memory — to say it doesn't
  have something in memory rather than present an un-recalled fact as remembered. A citation resolves
  through a per-message manifest keyed by the memory's record id (never the model's echoed text), so
  a mis-cited or hallucinated `[Memory N]` degrades to plain text instead of a wrong link.
- **A muted agent can be un-muted from its detail page.** When the auto-router stops suggesting an
  agent because you dismissed its chip enough times, the agent's Advanced → Routing status now shows
  that it's muted and offers an Unmute control to make it eligible for suggestions again — previously
  the mute was invisible and irreversible from the UI.
- **Local models now carry a capability matrix and a runtime/license contract from a declarative
  catalog.** A local-model provider can describe its models in a `catalog.json` — per-model feature
  flags (word/segment timestamps, speaker labels, hotword budget, languages), runtime and
  runtime-contract tags, SPDX license, and context/output budgets — which flow to
  `GET /api/models/available` and render as chips in Settings → Models. A non-commercial license
  shows a warning chip at bind time; a deprecated model shows a chip but stays bindable; and a
  download whose weights are incomplete (under 60% of the declared size) is flagged `truncated` with
  a Repair action that re-downloads it. Config-only pipeline repos (no local weights) are never
  mis-flagged.
- **Mid-run steering now takes effect, and the judge leaves a paper trail.** A workflow's decision
  layers are wired into the run loop: an instruction you queue while a loop is running is consumed
  at the next iteration boundary and re-ranks the plan (rather than sitting unread until the run
  ends); every judge-gate verdict is recorded to the Run Ledger with its evidence chain, and a
  human overriding a judge records the divergence — so the flywheel can tell a human-steered
  outcome from an autonomous one. A judge gate that has never rejected across enough runs is now
  flagged as a "nodding loop" and blocked from becoming its kind's default. The loop breaker keeps
  a single authority (no duplicate trip path).

### Fixed

- **An automation fired from a background write could be dropped without a trace when the retry
  it was owed was skipped.** Some fires are recorded on disk instead of running immediately — a
  memory write from the CLI, for example, has no event loop to run an action on, so the fire is
  parked and picked up on the next scheduler tick. If picking it up failed, the fire was marked
  handled anyway and deleted: a warning in the log, and an automation that never ran. It is now
  retried. A failure that happened *before* the automation could have started anything is held and
  tried again on later ticks, up to five times, and the attempt count survives a restart, so a
  temporary problem no longer costs you the fire. A failure that happened *after* the automation
  was handed off is never retried, because running it twice is worse than not knowing whether it
  finished. A fire that can never work — a malformed record, or five failed attempts — is dropped
  once, loudly, with the reason, instead of stalling every automation behind it. Two identical
  fires parked within five minutes of each other now run once, which is the same rule already
  applied to a webhook a sender retried or a file saved twice.
- **A workflow set to `on_overlap: queue` started a second run alongside the first instead of
  queueing it.** The policy did the opposite of its name: with a run already in flight, `queue`
  matched no branch in the code that applies the setting and fell through to "start now", so a
  trigger that fired every minute against a slow workflow stacked runs without bound — the exact
  thing the default (`skip`) exists to prevent. `queue` now means what it says: the start is saved
  as an unstarted run and begins when the run in flight ends, whether that happens while the
  gateway is up or after a restart. The queue holds one pending start (the run after next would do
  the same work with staler inputs), and a start refused by that limit says so — in the trigger's
  recorded outcome and in the log — rather than reporting itself as queued. An unstarted run you
  created yourself is never picked up by this: only starts the overlap policy queued are, and the
  distinction is recorded on the run. Trigger history shows a queued fire as deferred with its own
  reason, so it is no longer indistinguishable from one that ran.
- **The Inbox's Mentions and Email filters could never match anything.** The dashboard has
  filtered and counted items by kind for a while, and the inbox stored a kind per row — but the
  message-source seam every provider builds on had no field for it, so every message a source
  polled arrived as a plain "message" no matter what it really was. Mail landed
  indistinguishable from a chat message, and two of the kind chips were unreachable by
  construction. A source can now state what a message is (`IncomingMessage.kind` — `message`,
  `mention` or `email`) and that value is what gets stored, filtered and counted. Nothing infers
  a kind from the text of a message: a mention is something the source knows from its payload,
  not a guess about whose name appears in a sentence. A source that declares a kind the dashboard
  cannot render keeps its message — the row still arrives as a plain message — and the mistake is
  logged with the source that made it rather than silently accepted.
- **`personalclaw update` was a dead end unless you had installed from git.** If you installed the
  documented way — `pipx install personalclaw`, `pip install personalclaw`, `uv tool install
  personalclaw` — `personalclaw update` printed "❌ PERSONALCLAW_PROJECT_DIR not set — cannot locate
  source tree" and exited 1, because the CLI still ran the old git-only pipeline while the
  install-kind-aware updater the dashboard uses lived elsewhere. The command now behaves per install
  kind: a **wheel install** upgrades itself in place (`-U personalclaw==<latest>`, no source tree, no
  Node) and tells you to `personalclaw restart`; a **git checkout** keeps the fetch + reset + rebuild
  pipeline and honours Developer update mode (ride release tags by default, every commit when it's
  on); a **container** prints the two `docker compose pull` / `up -d` commands rather than pretending
  it can patch an image; a **desktop** install defers to the app's own updater. An install kind it
  does not recognize says what it detected and refuses to guess, instead of falling back to
  `git reset --hard` on a tree it may not own.
- **`personalclaw update` could run `git reset --hard` without anyone agreeing to it.** With
  uncommitted tracked changes, the confirmation prompt used to read whatever was on stdin — so from
  cron, a pipe, or `< /dev/null` it could take a piped "y" and discard your work, or crash with an
  `EOFError` traceback. Without a terminal it now refuses the destructive reset, names the files at
  risk and the remedy (`git stash` or commit), and exits non-zero. Declining at a real prompt still
  exits 0 — that's a choice, not a failure. Untracked files are no longer listed as at risk, because
  a reset does not touch them.
- **A detached-HEAD update fetched a branch that does not exist.** When git reported no branch (a
  checkout parked on a release tag), the updater fell back to a hardcoded branch name this project
  has never used, so the fetch failed with a confusing git error. It now resolves the branch
  honestly: the branch you are on, else the remote's own `HEAD` (read locally, so it still works
  offline), else what the remote reports, else `main`.
- **Deleting a knowledge item mid-enrichment crashed its background pipeline with a noisy
  error.** If you deleted an item within the ~30 seconds its tags/insights were still being
  extracted, the enrichment pipeline hit a `FOREIGN KEY constraint failed` error, logged a full
  traceback, and then tried to record a "failed" status on the row you had just deleted. Deletion
  itself always worked, but the orphaned pipeline made noise it shouldn't. A delete during
  enrichment now aborts the pipeline quietly — the item is gone, so there is nothing left to
  enrich. Genuine mid-pipeline failures (where the item still exists) still record as failed.
- **Renaming the built-in Personal or Repeatable project quietly broke your projects.** The API
  let you rename a default project even though the UI hides the button — and because the system
  re-creates any missing default by name, the rename left you with two Personal (or two
  Repeatable) projects: the original, now holding your task lists but no longer the one new work
  routes to, and a fresh empty duplicate. Worse, neither could be cleaned up through the app.
  Renaming a default is now refused with a clear message (other edits like its brief or workspace
  still work), and the delete guard now protects a project by its current name rather than a
  sticky internal flag, so any stray duplicate left behind by the old bug can finally be deleted.
- **"Run now" did nothing for almost every automation, while reporting success.** Clicking Run now
  (or dry-run's live counterpart) on a schedule or automation reported that it had run, but no
  action executed and nothing appeared in the run history — the Run button just sat on "Running…"
  waiting for a run that had never started. Automations still fired on their own schedule, so
  nothing looked broken until you tried to test one by hand. Manual runs now execute the
  automation's action, and a run that cannot start says so instead of claiming success.
- **A manual "Run now" left no trace and the "Running…" pill never cleared.** Even after Run now
  began actually executing the action, the run was recorded nowhere — the run history gained no
  row, the "last run" time never advanced, and so the animated "Running…" indicator waited forever
  for a completion it could not see. A manual run is now written to the automation's run history
  (tagged as a manual run) and advances its last-run time, so the history updates and the pill
  clears. A manual run that fails is recorded as a failed run rather than swallowed. Testing an
  automation by hand still never counts against its own fire limit.
- **Knowledge ingest reported steps as finished that never ran.** The per-item ingest view marked
  entity extraction, intent matching, and embedding as done on every item regardless of what
  actually happened — so with no embedding model bound, an item that stored zero vectors still
  showed "Embed ✓". Each step now reports its real outcome: done when it did the work, skipped
  when there was nothing to do (no model bound, no intents defined), failed when it errored.
- **Accepting a "refine an existing skill" proposal always failed with an error.** When the
  system proposed refining a skill you already have (rather than creating a brand-new one),
  clicking Accept returned "could not write skill … (invalid, oversized, or exists)" and the
  proposal stayed stuck in the queue forever — the only way out was to reject it and lose the
  improvement. Accept now updates the named skill in place, appending the refinement under a
  dated heading so the original skill and any earlier refinements are preserved. New-skill
  proposals are unaffected, and a refine whose target skill was since deleted quietly falls
  back to creating a new one instead of erroring.
- **Importing a memory file that wasn't a JSON object failed with an unhelpful server error.**
  Handing `POST /api/memory/import` (or `personalclaw memory import`) a JSON list, string, number,
  `null`, or `true` crashed the import instead of telling you the file was the wrong shape. Both
  now say so plainly, and a valid export imports as before.
- **A request that named no task mode relaxed every chat to full execution.** `POST
  /api/chat/task-mode` read a missing `mode` as `agent`, and a missing `session` as "all
  sessions", so one under-specified request could take every chat you had set to Ask or
  Plan and hand it full tool access. An absent or non-string `mode` is now refused the
  same way an invalid one always was, and the response names the sessions it changed so a
  caller can tell a one-chat change from a fleet-wide one.
- **A project could be pointed at your credential directories.** Binding a project's workspace
  accepted `~/.ssh`, `~/.aws` or an OS system tree with no complaint — the same paths the
  terminal refuses to open in — and a chat started under that project inherited the path as the
  working directory of an unsandboxed agent. Binding one is now refused, on exactly the check
  the terminal already used. Editing a project also stops accepting field names it never wrote:
  an unrecognised field now comes back as an error naming it, instead of a silent no-op or a
  blank "server error".
- **The dashboard could be tricked into handing over your secrets by changing the case of a
  filename.** The file panel refuses to open credential files — the session signing key, the
  local secret, the telemetry salt, `.env`, and anything ending in `.key`/`.pem`/`.secret`. On
  macOS and Windows, where the filesystem ignores upper/lowercase, that block was case-sensitive
  and could be walked straight past: asking for `.LOCAL_SECRET` instead of `.local_secret`
  returned the real bytes. The same hole let the write endpoint clobber a protected file under a
  different-case name. The block is now case-insensitive and also compares file identity, so no
  spelling — including hard links or Windows short names — reaches a protected file, in either
  the read or write direction.

- **One app could borrow another app's permission to run an agent, and read agent runs that
  weren't its own.** The permission check on the app agent-run endpoints read the app name out of
  the URL instead of asking who was actually calling, so an app allowed to reach `/api/apps/*`
  could name any agent-permitted app in the path and get an auto-approving background agent run.
  Separately, polling a run's status never checked who owned the run: any app holding the `agent`
  permission could read the task text and result of runs started by the dashboard, a schedule, or
  a different app. Both endpoints now gate on the calling app's verified identity, and a run is
  only readable by the app that started it. No app in the Store declares the `agent` permission
  today, so nothing shipped was exposed — this closes the hole before the first app needs it.
- **Discover's "see goal loops" tip opened a blank new-loop form instead of your loops.** The
  button pointed at an address the app no longer serves, so it redirected to the composer and
  asked "What do you want to accomplish?" — the opposite of showing you what already exists. It
  now opens the loop list.
- **Changing your embedding model silently stopped the assistant remembering anything.** If the
  embedding model changed after the memory index was built, every attempt to save a new memory
  failed — and nothing said so. Recall and the nightly consolidation pass broke the same way for
  the same reason. Memories are now kept whenever this happens, with a warning naming both sizes
  and telling you to re-embed, so the worst case is that older memories are temporarily missing
  from semantic search instead of new ones being lost entirely. If you have hit this, your
  memories are still there: re-embed to bring them back into search.
- **A task comment could be signed as anyone, and never taken back.** The comment endpoint took
  the author straight from the request, so any client reaching your gateway could post a comment
  under your name — and with no delete, a forged one was permanent short of hand-editing files.
  The author is now derived on the server from your configured username, sending an `author` is
  rejected outright rather than quietly ignored, and comments can be deleted from the task panel.
  A malformed comment body now answers 400 instead of failing with a server error.
- **`personalclaw app new` no longer names your app as its own copyright holder, and every licence file in the tree is now held to the real MIT grant.** The scaffolder wrote `_license_text(author or display, …)`, so an app generated without `--author` got `Copyright (c) 2026 Channel Null` — a grant naming an **artefact**, which holds no copyright and leaves nobody to ask for permission. Four published exemplar repositories shipped it. The fallback is now an obviously-unfilled `<your name>` placeholder and the CLI says so on the way out (`fill in LICENSE's copyright holder … or re-run with --author`), because a placeholder nobody is told about is the same defect with extra steps. `--author` still lands verbatim. The reason this survived is worth recording: every existing case in `tests/test_app_scaffold.py` passed `author="Scaffold Test"`, so the fallback branch was exercised by **nothing**. **Two gaps closed in the rail, not one.** `tests/test_licence_governance.py` pinned the root `LICENSE`'s grant by sha256 and checked every other file only for the string `MIT` — so the five sibling licence files (the bundled `ollama-models` app's, the app template's, the three registry fixtures') were pinned by nothing, and a *paraphrased* grant under an intact `MIT License` title was invisible by construction. The grant check now covers every licence file in the tree. And because that hash deliberately excludes the copyright line, the **holder** was checked by nothing at all: two new assertions require every licence file to name `PersonalClaw contributors`, and — the generator's rule, defined once and driven from the scaffolder's own test rather than re-derived — forbid any licence naming the app beside it, in all three spellings the broken fallback could produce (`displayName`, the bare kebab `name`, and the title-cased expansion `channel-null` → `Channel Null` that appears in no file as a field value). Both new rails were falsified before being trusted: mutating the bundled app's holder to `Ollama Models` reds the two holder checks while the grant check stays green, paraphrasing its grant reds the grant check alone, and restoring `author or display` reds the scaffolder's test naming `'Channel Null'` exactly. The census in `docs/architecture/licence-identity.txt` gains its judgment for all of this, and one stale claim in it is **corrected**: it named the Store consent card and `apps/quality.py` as the readers of a bundled app's licence. Neither reads one — `apps/quality.py` contains the string zero times. The real readers are the apps repo's `manifest-validate` job and the registry's `validate_registry.py:check_license`, both verified in code.

## [0.1.3] — 2026-07-30

The **attention-and-access** release. Two themes:

**One place for everything waiting on you.** The inbox stops being a message list and becomes
the single attention surface — a goal loop that needs a decision, a proposed skill, and a tool
approval you walked away from all land there as items you can answer in place, instead of a
toast that scrolls past while the work stays stalled. Delivery becomes a choice per kind of
notification (notify / badge / digest / never) rather than one global severity floor, with a
daily digest for the noisy kinds.

**Reach your own assistant from anywhere.** Sessions now survive a restart (they didn't — every
restart logged you out, and away from home that meant locked out), and an optional password
sign-in with 2FA and device pairing lets a browser anywhere get in. It is off by default and
purely additive: the local token link keeps working and remains the way back in, so a login you
misconfigure cannot lock you out of your own box.

Plus: artifacts get a real library, knowledge gets shelves and a proper tag taxonomy, the agent
navigates code by symbol instead of grepping blind, backups run and verify themselves, and
👍/👎 on AI judgments starts actually teaching.


> **Note (0.x clean break):** model bindings in `active_models.json` now carry
> ordered fallback-chain semantics. Old stores read cleanly (a single binding is a
> one-entry chain); consider `personalclaw snapshot` before upgrading, per the
> pre-1.0 banner.
>
> **Note (0.x clean break):** true rewind adds a `rewound` field to persisted chat
> messages (the retained discarded tail). Old sessions read cleanly (missing field =
> today's behavior — no migration); consider `personalclaw snapshot` before upgrading.
>
> **Note (0.x clean break):** knowledge-item tags move from a JSON column into their own
> tables, and the old column is dropped. Opening your library migrates it in place — the
> upgrade is verified against duplicates, blanks, non-ASCII and malformed values, and
> refuses to drop the column if any tag would be lost. Consider `personalclaw snapshot`
> before upgrading, per the pre-1.0 banner.
>
> **Note (0.x clean break):** the unread badge now counts unresolved **inbox** items instead
> of unacknowledged notifications, so **it resets once on upgrade** — any old unacked toasts
> stop contributing to it. Nothing is lost: the notification list keeps its full history and
> becomes a delivery audit. The badge is more honest afterwards (dismissing a toast no longer
> hides work that is still outstanding, and handling something in the inbox actually clears
> it). Your inbox alert keywords move to notification rules automatically. Consider
> `personalclaw snapshot` before upgrading, per the pre-1.0 banner.

### Added

- **Sign in from outside your home network.** Reaching your own dashboard while away used to
  mean being at the machine — the only way in was a token link you had to mint locally. You can
  now set a password (`personalclaw auth set-password`) and turn on a sign-in page, so a browser
  anywhere can log in for a session. **It is off by default and it is additive:** the token link
  and the loopback paths keep working exactly as before, and they stay the way back in if you
  ever forget the password — a login you misconfigure cannot lock you out of your own box.
  Optional 2FA (`personalclaw auth totp setup`) adds a time-based code. Failed attempts are rate
  limited with a lockout, and every attempt is recorded in the audit log.
- **Pair a phone without typing your password into it.** `personalclaw auth enroll` prints a
  short code you enter once on the other device. It works exactly once, expires in five minutes,
  and is stored only as a hash — so the worst case for a code you lose on a screen is that you
  run the command again.
- **Sessions survive a restart.** Previously every gateway restart invalidated every token: on a
  local box you re-ran `personalclaw token`, and away from home you were simply locked out,
  because minting a URL required being at the machine. The signing key and the session records
  are now persisted (both `0600`). `personalclaw auth revoke --all` ends every session, and that
  survives a restart too.
- **Hardening for an internet-exposed instance.** Set `dashboard.public_url` when you reach the
  dashboard through a TLS-terminating tunnel and the session cookie gains `Secure`, the
  WebSocket policy allows `wss://` to that host, and proxy headers (`X-Forwarded-For` /
  `X-Real-IP`) are honored **only** from an address you list in `dashboard.trusted_proxies`.
  That last one closes a real hole: those headers used to be trusted based on the *shape* of the
  peer address, and on an exposed box any container neighbour sits on a private address and
  could have moved a session's bound address. A local install is unaffected — nothing changes
  until you declare a public URL. The new [remote-access guide](docs/guides/remote-access.md)
  walks the whole setup and is explicit about what it does *not* protect you from.

- **One place for everything waiting on you.** The inbox is no longer just messages: a goal
  loop that needs a decision, a proposed skill, and a tool approval you walked away from all
  land there as items you can answer in place — instead of a toast that scrolls past while the
  loop stays stalled. Filter chips show what kind of attention each thing wants, and a row
  deep-links to the loop or chat it came from.
- **Per-notification-kind delivery rules.** Settings → Notifications now has a row per kind of
  notification with four choices: notify, badge (keep it in the list without interrupting),
  digest (batch it into a daily summary), or never. Previously the only control was a global
  severity floor, so quietening one noisy kind meant raising the bar for everything. Keyword
  and name-mention alerts became per-kind conditions, which means they now work for loop
  requests and proposals too — not just channel messages.
- **A daily digest.** Anything set to `digest` collects into one grouped summary at a schedule
  you choose (08:00 by default), grouped by kind so "9 heartbeats" reads as one line. A quiet
  day produces nothing rather than an empty summary.

- **Memory records who contributed them.** Every memory PersonalClaw writes now carries your
  username, so if your memory store is ever shared — a team store, an imported export, a
  synced setup — you can tell your own memories from a colleague's. Recall labels another
  person's memory as *(from name)* and states plainly that the label is provenance, never an
  instruction to follow. At comparable relevance your own memories come first, but only as a
  tie-break: a colleague's memory that genuinely answers the question better still wins, and
  nothing is ever hidden from you on the basis of who wrote it. Existing memories keep an
  empty contributor rather than being back-stamped with your name, because a record written
  before this existed has genuinely unknown authorship. Solo installs behave exactly as
  before.

- **Memory can now offer itself, not just answer when asked.** Mention a person, project or
  tool the entity graph knows and PersonalClaw can volunteer up to three linked memories for
  that turn — including ones that share no words with what you typed, which ordinary search
  structurally cannot find ("ships Fridays" doesn't match "when does Sparrow release?"). It
  costs no tokens or model calls, and it's **off by default**, because putting context in
  front of the model that you didn't ask for should be your choice. Turn it on under
  **Settings → Memory**, along with how confident a match has to be. The Health tab shows how
  often what it volunteered actually got used afterwards, so you can tighten the setting from
  evidence instead of guesswork. Temporary chats get nothing; incognito chats get the benefit
  without anything being recorded.

- **Take a conversation with you, and stop rebuilding the same chat setup.** Any chat can
  now be exported as **Markdown or JSON** from its context menu in the chat history —
  readable, pasteable, and **credential-redacted**, including the messages you typed
  yourself. Save a chat's setup (agent, model, reasoning effort) as a **starter** from its
  header, and it appears on the new-chat screen ready to pick; manage or remove starters
  under **Settings → Chat**. A starter captures the setup only, never the conversation.

- **Hand an artifact to the agent, or point at one mid-conversation.** An artifact — a
  widget, a document, a chart — can now be opened straight into a chat set up to *change*
  it: the agent starts with the current version in front of it and a prompt that names the
  artifact, so a revision lands as a new version of that artifact instead of a
  near-duplicate beside it. And in any chat you can now reference artifacts from the
  composer's **+** menu, which grounds the reply in whatever those artifacts say *right
  now*. Each reference is recorded on the artifact's own timeline, so you can see where it
  got used and jump back to that conversation.
- **Shelves for your knowledge library — including ones that fill themselves.** Saved
  documents, notes and links were one flat list. You can now group them onto
  **collections**: a *manual* shelf holds whatever you put on it, and a **smart** shelf
  holds whatever matches a search you name once — so "everything about the borrow
  checker" stays current on its own as you save more, with nothing to re-run and no
  backfill step.

  Items can sit on several shelves at once, and a shelf is a *view*, not a container:
  deleting one leaves every document in your library untouched (the confirmation says
  so). Each shelf has its own URL, so you can link straight to one. Alongside it, items
  gain a **reading state** — unread → reading → read, because "reading" is the state a
  reading list exists to represent — and a **favorite** star. Marking something read
  deliberately does *not* count as editing it, so working through a backlog won't
  reshuffle a library sorted by recency.
- **Clean up a long chat list in one action, and let old chats retire themselves.**
  Conversations pile up, and until now the only tools were one-at-a-time. You can now
  select many chats at once and **archive**, restore, tag, re-file, or exempt them in a
  single action — and chats you haven't touched in a while (30 days by default) move to
  **Archived** on their own.

  Archiving is not deleting, and that's the point: an archived chat keeps its full
  transcript, **stays searchable**, and is one click from coming back. That's what makes
  it safe to do automatically. Anything you want kept in the list forever can be pinned
  "never archive", and opening or replying to an archived chat brings it back by itself.
  Set the window in Settings, or set it to 0 to switch auto-archive off entirely.

  Two deliberate limits: bulk **delete** is not offered beside archive — irreversible
  actions shouldn't sit one mis-click from reversible ones — and chats with no recorded
  activity yet (everything from before this shipped) are never auto-archived, so
  upgrading can't sweep away your history.
- **On a shared task board, your assistant only works on *your* tasks.** If tasks come
  from somewhere other people also write to, task rows now show who a task belongs to,
  and you can switch between "Mine" and "Everyone" — the filter appears only when
  someone else's work is actually there, so nothing changes for a solo setup.

  The part that matters most is invisible: ready-task counts, the "what should I do
  next" picker, and the agent's own work selection all count and choose **only your
  tasks**. Someone else's task can never quietly become something your assistant picks
  up. Dependencies are still honored across everyone, so a task of yours blocked by a
  colleague's unfinished work is correctly *not* ready rather than falsely startable.
- **Decks and PDFs too — and anything already saved can become a document.**
  **deck_create** turns a markdown outline into a real PowerPoint deck: each `##` is a
  slide, the lines under it become bullets, and `<!-- notes: ... -->` becomes that slide's
  speaker notes. PDF generation works on every install rather than depending on whatever
  converter happens to be on the machine.

  And the library now works in both directions: point `document_create` at a saved
  knowledge item or a note you already have, and it comes back out as a Word document you
  can send. Same generator, so an exported document is no different from a freshly
  written one.

- **It can make you a Word document or a spreadsheet you can actually send.**
  PersonalClaw could read .docx, .xlsx, .pptx and .pdf but could not produce a single
  one — everything it generated stayed inside the app. Ask for a document now and you get
  a real file: **document_create** turns markdown into a Word document (headings, bullets,
  numbered lists, tables, code, page breaks) and **sheet_create** builds a spreadsheet
  with a bold header row and frozen panes.

  Numbers stay numbers, so the result can be summed and charted — a spreadsheet full of
  text-formatted numbers is the main way generated ones turn out useless. Both land in
  your Artifacts library with version history, so re-generating one updates it in place
  instead of leaving a near-duplicate beside it, and each has a download button plus a
  text preview.

  Verified by opening the output in real applications, not just our own reader: macOS
  identifies the files as genuine Office documents and renders them correctly.

  Two things this fixed along the way. **Tables in Word documents you upload were being
  silently dropped** — the reader only looked at paragraphs, so often the densest
  information in a document was invisible to search and to the agent. And **generated
  videos were being stored as images**, which made them unplayable; video is now a real
  artifact type with a working player.

- **Tags are a real taxonomy now — nest them, rename them, merge them.** Tags were a
  flat list of strings stapled to each item, so a typo meant editing every item that
  carried it and there was no way to express that "tokio" is a kind of "rust". The new
  **Tags** view on the Knowledge page shows every tag with how many items actually use
  it, and lets you rename, nest one under another, merge two together, or delete one —
  from a right-click.

  Renaming is instant and applies everywhere at once. Merging moves every item onto the
  surviving tag and takes nested tags with it rather than orphaning them. Deleting a tag
  removes it from your items but never deletes the items, and its nested tags become
  top-level rather than disappearing with it. Unused tags are kept on purpose: a tag you
  built is part of your taxonomy even when nothing carries it this week.

  Two things this quietly fixed. **Tags with non-Latin characters were unsearchable** —
  a tag like 日本語 was stored in an escaped form the search index couldn't match, so it
  simply never came up; it does now. And the tags you write by hand are now marked as
  yours, so automatic enrichment can refresh the tags it generated without ever
  overwriting one you chose.

- **Your reading state and favorites are now visible, and filterable.** Marking a saved
  item as reading, read, or a favorite already worked — but nothing showed it anywhere,
  so favoriting was effectively write-only: you could star something and then had no way
  to find it again. Items now carry a **star** for favorites and a **reading** badge, a
  read item's title dims, and chips let you filter to Reading, Unread, Read or Favorites
  (each appearing only when there's something to show, with a count).

  Favorites finally have their own mark instead of borrowing the pin icon — pinning
  floats an item to the top of the list, favoriting is a personal bookmark, and they read
  as different things now. The reader gained the same two controls beside Pin and
  Archive. Unread items stay unbadged on purpose: it's the default state, and marking
  every new item would just be noise.

- **Curate a whole shelf of saved items in one action.** Working through a knowledge
  library one item at a time is what makes nobody do it. Select many items and **mark
  read or unread, favorite, add to a shelf, or archive** them together. Each action
  reports what actually happened — "38 shelved · 2 already there" — because a selection
  can go stale between the click and the request, and a partial success is not a failure.

  Marking a backlog read deliberately does *not* count as editing those items, so
  catching up won't reshuffle a library sorted by recency. Bulk **delete** is not
  offered beside these: everything here is reversible, and an irreversible action
  shouldn't sit one mis-click away from a safe one.
- **See what changed between two versions of an artifact.** An artifact keeps every
  version, but the only way to tell what actually moved between two of them was to open
  each in turn and compare by eye. **Compare versions** now shows a real side-by-side
  diff — for images, the two versions themselves, before and after. It opens on the two
  most recent versions, since "what changed in the last pass?" is usually the question,
  and you can pick any pair or swap which side is which.

  Whitespace-only changes are shown rather than hidden: an agent re-rendering a widget
  often re-indents it, and reporting "nothing changed" would be a lie.
- **Backups now happen on their own, and they get checked.** PersonalClaw takes a
  full snapshot nightly and exports whatever changed every hour, so how much you can
  lose is bounded by an hour rather than by when you last remembered to run
  `personalclaw snapshot`.

  Retention keeps a *spread* instead of a window — two weeks of daily snapshots, then
  weekly, then monthly — so a year of history costs about 30 files and you can still
  reach back to January. Settings → **Backups** is where all of this lives: when each
  job last ran, a button to run one now, how long each tier is kept, and the snapshot
  list marking exactly which files the current settings would remove — before anything
  is removed. Restoring stays a command-line action, and the screen says so, because a
  restore has to replace live state while the gateway is stopped.

  Once a month it also runs a **restore drill**: the newest snapshot is unpacked into
  a temporary directory and every database inside it is integrity-checked, then you
  get a pass/fail notification. A backup nobody has restored is a hope, not a backup —
  and a failure is reported as a warning, so it isn't hidden by quiet hours.
  `durability.auto_backup` turns the schedule off if you'd rather do it by hand.

  Fixed while building it: the incremental export had been unable to notice memory
  changes at all. Databases run in WAL mode, so a saved change lands in a companion
  file and the main database's timestamp never moves — the export saw "nothing
  changed" through an entire session of work.
- **Find any chat by what was said in it.** Chat search now runs against a real
  full-text index of your transcripts instead of scanning the 500 most recent files,
  so a conversation from months ago is as findable as yesterday's — and each result
  shows the matching passage with your terms highlighted, so you can tell at a glance
  which chat is the one you meant.

  On a 120-chat history a content search returns in about 30 milliseconds. The index
  keeps itself current as you chat and repairs itself on a schedule, so there is
  nothing to maintain. **Incognito and temporary chats are never indexed** — and a
  chat you switch to incognito after the fact disappears from search immediately.
  If the index is ever unavailable, search quietly falls back to the previous
  behavior rather than failing.

  Fixed along the way: a chat marked incognito *after* some of it was written could
  still appear in content search, because only its saved mode was checked and that
  still read "persistent". Both search paths now honor the live setting.
- **The agent navigates your code by symbol instead of grepping blind.** Asking
  "where is this function defined and what calls it?" used to cost a grep, a couple
  of file reads, and often another grep — every round-trip spending tokens on
  navigation instead of the actual work. A new `code_map` tool answers it in one
  call from a tree-sitter index of your workspace, and `code_map_overview` gives the
  shape of an unfamiliar codebase (the most-referenced modules and their public
  surface) in one read.

  Indexing Python, TypeScript, JavaScript, Rust and Go: on a 1,500-file repository
  the first pass takes about four seconds and later passes are effectively
  instant, since only changed files are re-read. The same index makes SDLC planning
  passes start from a map of the codebase rather than exploring for it, and ranks
  the chat composer's `@` file picker so widely-used modules surface above
  same-named leaves.

  It is strictly an accelerator: with no index, no parsers, or an unparseable file,
  everything falls back to the grep-and-read behavior it had before, and the tool
  says so plainly rather than guessing.
- **Memory now knows what it's *about*.** Every memory is linked to the people,
  projects and tools it names, so asking "what do I know about Ana?" follows those
  links instead of hoping a similarity search surfaces everything. A memory about a
  standup, a stored fact about a repo, and a lesson from last month finally connect
  when they concern the same thing.

  Linking happens the moment a memory is written and **costs nothing** — no tokens,
  no model call, just exact-name matching against the people and projects you've
  named. Links are typed, so the graph distinguishes a memory that's *about* you
  from one that merely mentions a project.

  Unknown names are **proposed, never invented**: a name that shows up across three
  separate memories appears in Memory → Health for one-click accept, because a junk
  entity quietly degrades every future search. Everything is reversible through the
  existing memory undo. Find it under **Settings → Memory → Health**, where
  *Rebuild links* seeds entities from what you've already stored and links your
  whole history in one pass. `memory.graph_enabled` turns it off — existing links
  are kept, so turning it back on needs no rebuild.

  > **Note (0.x clean break):** this adds tables to `memory.db` (schema v7). Old
  > stores upgrade in place on first run with no data loss and nothing to migrate;
  > consider `personalclaw snapshot` beforehand, per the pre-1.0 banner.
- **Your IDE can now actually ask your assistant things.** The MCP endpoint ships with
  six read-only tools: recall what the assistant remembers, search your saved documents,
  list or read a task, search past conversations by what was said, and check what the
  instance can currently do. Every answer is wrapped as data rather than instructions,
  conversation results are credential-redacted, and temporary or incognito chats are
  never searchable. There is no path from any of these to a write — the tool list is
  short and hand-written precisely so that stays true.

  **Security fix found while building it:** credential redaction did not recognize LLM
  provider API keys. Anthropic, OpenAI, GitHub, and Google keys pasted into a chat were
  invisible to the redactor, so they could survive into anything that strips secrets on
  the way out — session-search results and conversation titles included. All of those
  key shapes are now redacted everywhere redaction applies.
- **Point your IDE at your assistant: a read-only MCP endpoint.** PersonalClaw can
  now answer questions from a local MCP client (your IDE, an MCP inspector) over
  `POST /mcp` — JSON-RPC 2.0, with `initialize`, `tools/list` and `tools/call`.
  It is **off until you deliberately turn it on**, and turning it on takes two
  separate steps that are both re-checked on every request:

  ```bash
  personalclaw inbound token create mcp        # printed once — copy it now
  personalclaw config set inbound.mcp.enabled true
  ```

  Because both are checked per request, `inbound.mcp.enabled false` is an immediate
  kill switch — no restart. The surface answers **loopback callers only** unless you
  explicitly declare a public URL and opt into remote access in the config file;
  neither of those knobs is editable from the dashboard, so widening your network
  exposure can't be one mis-click in a browser. Requests are capped on every
  dimension an outside caller controls (body size, rate, concurrency, result size),
  refused requests come back with a real JSON-RPC error, and every request — allowed
  or refused — is recorded in `<home>/inbound_audit.jsonl`, with refusals also
  landing in the security event log. This release ships the surface with **no tools
  yet**: a client can connect and see an empty table. The five curated read-only
  tools (memory, knowledge, tasks, sessions, status) follow next, and by
  construction they can only ever read — there is no path from an inbound request to
  a write.
- **Tool groups: the agent loads the tools it needs, not all of them.** Every tool
  provider is now an activatable **group** (`schedule`, `artifacts`, `memory`, one
  per MCP server or app, …), and a session can run with only the groups it needs —
  so unused capabilities cost one line of catalog instead of every schema. Measured
  on the 69-tool built-in surface: a background session's tool block drops **56%
  (~6,900 tokens per turn)**. The agent manages this itself via a new `reset_tools`
  tool that takes the *final* set of groups it wants.
  Capability is never reduced, only context: **every tool stays callable by name
  even while its group is inactive**, each inactive group advertises itself in one
  line, and `tool_search` still searches everything — naming the activation step
  when it finds a tool in an inactive group. Off by default (`tools.groups_enabled`),
  and interactive chat keeps every group active even when on, so nothing changes for
  the chat you're watching; background, loop, and subagent runs start focused
  (tune per surface with `tools.group_defaults`). `GET /api/tools` now reports each
  tool's group.
- **The artifacts library: live previews, search, and collections.** The new
  Artifacts page is now a real library: a responsive grid where every card renders
  a **live preview** — widgets/HTML/React/documents/SVG in the same sandboxed,
  theme-injected frame chat widgets use (scaled down, inert, never re-implemented),
  images from their raw bytes, prose as an excerpt. Previews mount lazily as cards
  approach the viewport and at most a dozen live frames exist at once (older ones
  quietly demote to placeholders), so a 200-artifact library scrolls smoothly. The
  toolbar filters by text, kind, source, and **collection** — all URL-backed, so a
  filtered view is shareable — plus Recent/Name/Kind sort. Opening a card gives the
  full-page detail (version rail, events timeline, edit/snapshot/revert) with
  `?v=N` deep-linking a historical snapshot read-only, and a header control to
  assign the artifact to a collection. File-backed cards show a "source changed"
  drift badge.
- **Artifacts get their own page.** Artifacts — the named, versioned outputs agents
  produce — moved out of the Files page into their own top-level **Artifacts** nav
  entry (`#/artifacts`, deep-linkable per slug). Files now shows only your raw file
  roots; old `#/files/<slug>` artifact links (in past chats and event timelines)
  redirect automatically to the artifact's new home, and "Save as artifact" from a
  file jumps you there. The viewer itself is unchanged — render, edit/snapshot,
  version history + revert, and the events timeline all work exactly as before.
- **Artifacts: collections + save-time dedup.** Saved artifacts can now carry a
  **collection** label (a free-form grouping for the coming library), settable at
  save time and reassignable later, and filterable via `GET /api/artifacts?collection=`
  and the `artifact_list` tool. And saving no longer silently mints duplicates: a
  fresh `artifact_save` (or `POST /api/artifacts`) whose name matches an existing
  artifact now **refuses with a hint** — the tool tells the agent to update the
  existing slug or pass `force`, and the REST route returns `409 similar_artifact_exists`
  with the existing slug (bypass with `?force=1`). File-backed saves keep their
  existing source-path dedup. Pre-existing artifacts load unchanged (tolerant read).
- **Agent routing: suggest the right specialist, never route silently.** Give an
  installed agent a **Specialty** and comma-separated **Routing hints** (in the agent
  editor), and when a message in a default-agent chat clearly fits it, a quiet
  "route to `<agent>`?" chip appears above the composer. One click re-targets the
  session (via the existing agent-switch path); the ✕ dismisses it and suppresses
  that agent for a cooldown (three dismissals mute it until you re-enable). It is a
  **proposal** — nothing changes until you click — and classification is
  deterministic-first (keyword-phrase overlap, then embedding cosine when an
  embedding model is bound), with the LLM never in the hot path. Silent auto-routing
  is deliberately out of scope. Route/dismiss also feed the routing pair's accuracy
  into Settings → AI feedback. Tune it in Settings → Chat → Agent routing
  (`agents_routing.*`); zero behavior change until you author routing metadata.
- **Chat craft: seven chat-surface mechanics.** The chat surface gains the pieces
  the sibling platforms proved out. **True rewind** — edit ANY past user message and
  replay from there; the discarded answers are kept in this chat's history (viewable
  under a "rewound from here" divider, restorable as a fork) and the provider context
  rebuilds from the truncated transcript, so the agent never references the undone
  turns. **Queue with manners** — each queued message now has an "Interrupt now" that
  gracefully stops the running turn and runs that message next. **Find in
  conversation** — Cmd/Ctrl+F opens an in-chat find bar (count, next/prev, jump-to-
  match) that highlights every occurrence without ever re-rendering the markdown.
  **Quote toolbar** — selecting transcript text floats a Quote + Copy toolbar; Quote
  inserts an attributed blockquote (who said it) into the composer, now from keyboard
  and touch selections too. **Follow-up chips** — after each reply, 2-3 suggested next
  messages appear via one cheap background call (never blocks the turn; skipped for
  temporary/incognito chats and silent when no model is bound; toggle in Settings →
  Chat). **Smoother streaming** — the reveal snaps to word boundaries so text lands in
  whole words, with a new Settings → Chat "Streaming text reveal" (smooth | immediate)
  control.
- **Background compression keeps long chats fast.** Old, idle conversation history
  is now topic-segmented and compressed in the background on the maintenance
  cadence — the always-on complement to on-demand tool-output projection. A
  transcript untouched for a week (default) is split into topics (by embedding drift
  when an embedding model is bound; a deterministic turn-count fallback otherwise),
  then compressed by attention: the most-recent topic stays verbatim, middle topics
  reduce to their request/response pairs, and the oldest tier is summarized by a
  cheap background model. It only ever touches sessions **at rest** (never a live
  turn), incognito/temporary chats are skipped entirely, every dropped span is
  archived first (fully recoverable) and any tool-result recovery handle is
  preserved, and savings land in the TokenJuice ledger under `bg_topic`. Toggle and
  idle window live in Settings → Chat config (`tools.bg_compress_enabled` /
  `tools.bg_compress_idle_days`); disabling it stops the pass within one tick.
- **Feedback that actually teaches: 👍/👎 on AI judgments.** Inbox classifications,
  drafted replies, digests, and loop findings now carry a quiet thumbs pair. 👍 is
  silent-positive ("Mark accurate" — it only feeds the accuracy denominator); 👎
  optionally takes a one-line "why". Every verdict is attributed to the source that
  produced the judgment — the bound prompt, the loop judge, a workflow's surfacing —
  and per-source rolling accuracy lives in Settings → AI feedback (honest counts,
  shown only after enough verdicts). A source that keeps being wrong **stops
  surfacing** and raises a one-time "retire this rule?" notification with a deep
  link; snooze or clear it after an edit. Everything is deterministic counting —
  no model calls, and feedback never leaves the instance. Apps record feedback on
  their own judgments via `personalclaw.sdk.feedback` / `POST /api/feedback`
  (namespaced server-side, so an app can never impersonate a core source).
- **Investigate anywhere: chat about any entity with its context pre-loaded.** Inbox
  items and loop findings (more surfaces to follow) gain an "Investigate in chat"
  button that opens a fresh chat carrying the entity's full context — composed
  server-side from the owning store, injected as fenced untrusted data on your
  first message (never pasted into your visible text), with the composer pre-filled
  with an editable opening question. The session opens in read-only **Ask** mode —
  investigating never mutates the entity; you escalate the mode yourself. A header
  chip deep-links back to the source. Apps get the same primitive via
  `useInvestigate` in the app SDK.
- **Model use-cases v2: routing sub-categories + fallback chains.** Chat work is
  now routable by kind — `background` (titles, tags, suggestions, digests,
  consolidation), `orchestration` (supervising turns and model-less subagents),
  `loops` (goal-loop workers and judges), alongside the existing `code_tools` and
  `reasoning` — each bindable in Settings → Models under a new **Chat routing**
  group, falling back to your Chat chain when unbound. Bind a cheap or local model
  to `background` and housekeeping chores stop burning your flagship chat model.
- **Every model binding is an ordered fallback chain.** The first model is the
  default; later entries take over when an earlier provider's circuit breaker is
  open or a call fails (background calls advance mid-batch; a failed chain surfaces
  one clear error). The Models panel gains a chain editor with reordering and
  per-entry provider-health dots; the composer's model pick sits above the chain —
  if the picked model fails, the chain takes over.
- **Type-routed tool-output compressors.** Large tool results now project smarter: a
  JSON array of thousands of items becomes a per-field schema (names, types, ranges,
  null counts) plus the first/last item verbatim; a large code file becomes a
  signatures-and-docstrings outline with a line map (`code` is a new content type,
  sniffed conservatively). The full raw always stays one `tool_result_get` away.
- **Projection rules: three layers + line operations.** A builtin rule pack now
  recognises common command output (git, pytest, npm, docker, cargo…) so e.g. a
  `git diff` run through the shell projects as a diff; a repo can ship its own
  `.personalclaw/projection_rules.json` (project layer, beats user rules); and every
  rule may carry declarative line operations — head/tail window, keep/skip filters,
  and a fold-repeats counter — editable in Settings → Tool output.
- **Background prose summarizer.** Long natural-language output on background paths
  can be model-summarized with a guaranteed deterministic fallback (never wired into
  the synchronous tool path).

- **"Investigate in chat" is now on everything worth asking about.** The affordance
  that shipped for inbox items and loop findings now covers eleven more kinds:
  notifications, tasks, schedule runs, triggers, loop cycles, knowledge items, memory
  records and lessons, Doctor findings, crash reports, and audit events. One click opens
  a read-only chat already carrying that entity's context, with the question already
  written for you.
  Failures get the richest context, which is the point: investigating a failed cron or
  loop notification pulls in **the run it's about** — its task, status, latest finding,
  the job's cadence and consecutive-failure count — and asks "why did this fail?".
  A Doctor finding re-runs its probes and brings the offered fix's *dry-run preview*
  (never applying it). A learned lesson brings its provenance and the chain of beliefs
  it replaced, and asks "why do you believe this?". An audit entry brings the other
  entries from the same approval flow, so one decision reads as one story.
- **Tool groups are now visible, and they hide what can't work.** The Tools page shows
  the group partition — every group with its tool count, which ones are always loaded,
  and what each kind of session starts with — plus the switch to turn grouping on or
  off. Each tool provider is labeled with the group it belongs to. And groups whose
  capability isn't configured (subagent tools with no model bound, say) are now hidden
  entirely rather than offered in a state where they'd fail; asking to activate one
  says so plainly instead of quietly doing nothing. New `GET /api/tools/groups` reports
  the partition for anything else that needs it.

- **Personalities: themes that carry an identity, not just a palette.** Settings → Design
  now offers a **personality** — one switch that sets the color scheme, the wordmark, the
  browser tab title, and the interface density together, and can offer the assistant a
  matching name. Two are included as starting points (a mono-green **Retro Terminal** with
  a terse operator voice, and **Claw Arcade**), alongside the default PersonalClaw identity.
  Renaming the assistant is **offered, never assumed**: activating a personality shows a
  toggle naming the exact setting it would change, and declining it switches the look while
  leaving your configuration alone. Switching back restores everything, including the name.
  Every personality's palette goes through the same accessibility contrast checks as the
  built-in schemes, so a personality can't ship an unreadable theme.

- **A username, so your contributions stay attributable.** Settings → Account now takes
  a short handle (Settings suggests one from your name), and PersonalClaw stamps it onto
  things you create — tasks and task comments carry an author. It's a label, not a login:
  nothing signs in with it, and leaving it empty keeps records unattributed exactly as
  before. Renaming it affects future writes only; existing records keep the name they
  were written with, because rewriting them would falsify the very history attribution
  exists to preserve.

- **Backups you can actually read and verify: `personalclaw backup`.** Alongside the
  opaque snapshot tarball, state can now be exported as **deterministic shards** —
  one canonical JSONL file per store plus a SHA-256 manifest. Identical state always
  produces identical bytes, so the export diffs cleanly (adding one task shows up as
  exactly one added line — `git log` over your shards becomes a readable history of
  what the assistant learned) and a future sync never re-uploads data that didn't
  change. `personalclaw backup validate` re-derives every shard's size, row count and
  checksum, re-parses every row, and **exits non-zero** on any problem, so you can run
  it from cron: a backup nobody has verified is a hope, not a backup. `--incremental`
  re-exports only what changed. Secrets are never included — shards are the
  representation that leaves your machine.

### Fixed

- **`personalclaw logout` never actually revoked anything.** It printed
  "All dashboard sessions revoked" and returned success while the gateway refused the request
  (403) and every session kept working — the endpoint was gated behind the very dashboard
  session it was meant to end. Found by running the command against a real gateway rather than
  reading the code.

- **Auto-archive skipped the very chats it existed to tidy — and you couldn't see or
  change the rule.** Chats are archived after a period of inactivity (30 days by default),
  but the sweep only considered conversations currently loaded in memory. Since old chats
  aren't loaded until you open them, a conversation idle for months was exactly the one the
  rule could never reach: it ran hourly and reported that nothing was stale. The sweep now
  covers chats that exist only on disk, archiving them in place without loading them.
  Separately, the threshold had no control anywhere in the app — it now lives under
  **Settings → Chat → Context & lifecycle**, showing how many chats are currently stale so
  you can see what the rule would do before it does it. Archiving remains fully
  reversible; nothing is deleted.

- **The "Steer" button never steered.** Typing while an answer was streaming showed a
  button labelled *"Steer — send into the running turn"*, and clicking it queued the
  message for *after* that turn instead — then displayed a card reading "1 queued · sent
  one at a time as each turn finishes", contradicting the button you had just pressed. The
  message was never lost, but it never did what the label promised. Steering now actually
  reaches the answer being written, and the message appears above the composer marked as
  steered rather than queued.

  Four independent faults each guaranteed the failure, which is why it survived: the
  frontend asked for `followup` regardless of the button; the lookup used a session key the
  session manager never registers, so the steer path could not match a session *at all*;
  the drain lived only after a tool batch, so a plain-prose turn — the most common kind —
  ran past it and discarded the message; and the confirmation event was filtered out as
  status noise, so even a successful steer was invisible. A steer sent to a runtime with
  no delivery path used to be buffered and silently dropped while the API answered
  `{"steered": true}`, growing an unread backlog for the life of the process; it now
  queues visibly instead. Mid-turn handling also gained a **Steer** option in
  Settings → Chat, alongside Queue and Replace — the policy field shipped earlier with no
  control at all.
- **Knowledge and memory could never embed with a config-defined provider.** With an
  embedding model bound to a provider you configured yourself (an Ollama endpoint, say),
  ingested knowledge items sat at "processing" with no embedding **forever** — no error,
  no notification, just nothing. Semantic search and the entity graph had nothing to work
  with, and memory's semantic layer could not embed at all. Chat through the very same
  provider worked, which made it look like embedding was broken rather than unavailable.
  The cause: configured providers are replayed into the model registry during gateway
  startup, but the background embed pass could run before or outside that path and then
  saw an empty registry. It now replays the configured providers itself when a lookup
  misses, so the embed succeeds instead of silently returning nothing — and a provider
  that genuinely is not configured still reports that plainly rather than retrying. (#47)
- **Binding a model can no longer fail silently.** `PUT /api/models/active/{use_case}`
  answered "ok" in two cases where the binding had not actually taken. A request whose
  body never mentioned `models` — an automation or a person reasonably guessing the key
  name — was read as "clear this binding", so it **unset the use-case's model and still
  reported success**. And on a fresh install (a config with no providers configured yet)
  the unknown-provider check was skipped entirely, so a model reference naming a provider
  that does not exist was stored unchallenged as a dead binding. Now a body without
  `models` is a `400` that names the keys it did receive, clearing requires an explicit
  `{"models": []}`, and an unknown provider is rejected whether or not other providers are
  configured. The model *id* is still deliberately not checked against the discovered
  catalog — a real provider that is slow to enumerate its models must not have valid
  references rejected. (#48)
- **Settings and the Store no longer blink to a loading skeleton when you touch
  anything.** Clicking "Check" for updates, flipping a toggle, rotating a key, adding a
  lexicon entry, or finishing an app install tore the whole panel (or the entire apps
  grid) down to a skeleton and rebuilt it — reading as a jarring full-page refresh even
  though the data had barely changed. One shared cause: the stale-while-revalidate cache
  dropped its value the instant a panel asked to reload, so every panel's "no data yet →
  show a skeleton" branch fired on the way to fresh data that was already in flight.
  Reloading now *holds* what is on screen and swaps in the new data when it lands, which
  is what stale-while-revalidate was supposed to mean. Switching to a genuinely different
  resource still clears, so one page's rows can never paint under another's filter. The
  fix is in the shared data hook, so all **88 reload sites across 32 panels** are covered
  at once. (#52)
- **Installing an app and updating PersonalClaw both failed on a `uv` virtualenv.**
  A `uv venv` ships no `pip` module — uv is the installer — but four separate code
  paths hardcoded `python -m pip install`, so each died with `No module named pip`
  on the project's own documented dev setup and on the uv-based end-user install
  path. Installing **any** app that declares `pythonDependencies` failed, and
  Settings → Updates showed "Update failed — pip upgrade failed" forever with no way
  to see why (the real cause was logged but never sent to the UI). Now one resolver
  picks the installer that actually exists — **uv** (targeted at the running
  interpreter, so the packages land where the gateway imports from) or **pip** — and
  says so plainly, naming both remedies, when neither is available. The update panel
  now shows the **real** failure reason instead of a static label, with the
  installer's colour codes stripped and the meaningful line first. The same resolver
  also fixes startup dependency repair and the git-checkout updater's editable
  install, which had the identical assumption. (#46, #51)
- **`personalclaw snapshot` was not backing up everything — and could copy a live
  database unsafely.** Two real problems, both closed. Your **tasks, projects,
  autonomous runs, artifacts, prompts, workflows, agents, installed apps, and
  per-entity settings were in no backup at all** — the snapshot carried a
  hand-written file list that had drifted from what the app actually stores, so a
  "full backup" could silently omit your entire task board. And only two of the
  five databases were copied with SQLite's safe backup API; the rest
  (knowledge, lexicon, autonomous-run records) were copied as plain files while
  the gateway held them open, which can capture a half-written database. In a
  reproduction of that case, a raw copy lost **2000 of 4000 rows**; the fixed path
  captures all of them.
  There is now one declared inventory of every store, which the snapshot and the
  portable export both read — plus a test that fails the build the moment a new
  store or database is added without being declared, so this can't drift again.
- **Snapshots of a non-default home no longer land in your real home.** With
  `PERSONALCLAW_HOME` set, the archive went to `~/.personalclaw/snapshots` anyway,
  mixing two installs' backups in the directory that retention pruning walks.

### Changed

- **Breaking-change policy is now written down, and it distinguishes maintainer from
  contributor.** During 0.x the maintainer keeps making backward-incompatible
  clean-break architectural changes with no migrations — that stays true, and the
  README now says plainly that this is expected to last a while, because
  migration-backed discipline arrives deliberately late, once the architecture stops
  moving. What's new is the other half: **contributors are
  not expected to make breaking changes.** Contributor guidance stays
  lifecycle-shaped — additive by default, no hand-rolled gate or migration
  machinery, and surface a needed break in an issue or PR description instead of
  shipping it, so the maintainer decides whether to take it, reshape it, or schedule
  it. See [CONTRIBUTING.md](CONTRIBUTING.md#breaking-changes); the PR template's change
  class now spells out both paths.

Forward-looking work is tracked in the maintainer's roadmap, which is not published in this repository.

## [0.1.2] — 2026-07-26

The **safety-and-resilience** release: the autonomy guardrails program (kill switch,
spend budgets, denylist, outbound scanning, named safety profiles), the full Platform
Resilience program (Doctor health probes, no-model degraded mode, mid-turn message
policy, confirm-gated fixes + trust simulators + crash capture, and a health-scored
self-maintenance engine), first-party apps in the Store on a plain install, the
legibility surfaces (self-documenting UI kit, Discover, routed project context, offline
agent reference), and a render-smoke gate that closes the v0.1.0 blank-dashboard hole.

### Added

- **One health-scored maintenance engine replaces scattered upkeep.** PersonalClaw now
  computes a **health score** (100 − measured deficits: knowledge items missing an
  embedding, orphaned stale locks, skills due for aging — each capped, and an *unfixable*
  deficit like "no embedder bound" is excluded rather than held against a score you can't
  improve) and runs a **dependency-ordered remediation plan** to raise it: re-index,
  orphan-prune, skill-age, stopping when the target score is reached, the per-run dollar
  cap is spent, or the plan is exhausted. It runs itself on an **adaptive heartbeat cadence**
  (further apart when healthy, sooner when degraded) and is visible + runnable on demand
  from Settings → Doctor → Maintenance, with a run ledger. Deterministic jobs are free;
  model-touching jobs (future) charge the guardrails spend meter. Every run is idempotent
  (per-job cooldowns) and the whole engine is one toggle — disabling it falls back to the
  legacy per-tick heartbeat maintenance. This is the final slice of the **Platform
  Resilience** program (Doctor · degraded mode · mid-turn · fixes/simulators/crash-capture ·
  this engine).
- **The Doctor can now fix what it finds, explain what it surfaces, and remember what
  crashed.** Three additions to the health surface (all Settings → Doctor):
  **confirm-gated fixes** — a finding that has a repair (a `static/dist` copy shadowing
  the runtime symlink, stale locks/rollback leftovers, model bindings pointing at removed
  providers) shows a **Fix** button with a read-only preview; nothing auto-applies, a
  two-step confirm runs it, and every application is security-audited and touches harness
  mechanics only (never your content). A **per-provider selftest** fires a tiny real
  inference per capability (a one-token chat / short embed) for true ground-truth instead
  of a reachability guess. A **surfacing simulator** dry-runs the skill scorer in explain
  mode — type a query and see, per candidate, the keyword/semantic scores, the thresholds,
  and exactly why each skill was included or excluded (zero model calls). And **structured
  crash capture**: an unhandled failure at a turn/loop/gateway boundary now writes one
  redacted, recoverable artifact under `~/.personalclaw/crashes/` (capped, never uploaded)
  that the Doctor surfaces as a card — a mid-stream death leaves a record instead of a lost
  stack trace.
- **Mid-turn message policy: queue (default) or cancel-and-replace.** A follow-up sent
  while a turn is still generating now follows a *declared* policy. The default,
  **`queue`**, is today's behavior formalized — the message is delivered next turn. Opt
  into **`cancel_and_replace`** (a platform default in Settings, overridable per channel)
  and a rapid follow-up instead cancels the in-flight answer and starts fresh with the new
  message — no stale ghost response, no wasted compute. A per-session debounce coalesces a
  burst of messages into ONE cancel + the last message. The guard is strict: only
  **interactive** turns (the web chat, a channel DM) are ever cancel-and-replaced —
  unattended work (goal loops, cron, subagents, the heartbeat) always queues, so a user
  message can never pull the rug out from under a background job. Built on the existing
  soft-cancel verb and turn-end queue drain (no new dispatch path); a new
  `resilience/active_jobs.py` tracks each turn's origin as the bookkeeping behind the
  decision.
- **No-model degraded mode: the assistant stays useful, and honest, with no model bound.**
  Every model-dependent surface now declares its **LLM-free floor** explicitly, so an
  offline laptop (dead ollama, wiped cache, no API key) degrades by design instead of
  error-walling: search drops from hybrid to keyword (FTS) + graph + recency ranking;
  the inbox keeps raising keyword/name-mention alerts (only auto-classify/draft/digest
  pause); knowledge still captures documents (only entity/insight extraction is skipped,
  marking the item partial); memory keeps its deterministic preference-facet capture;
  speech features turn visibly off rather than erroring; and chat says so plainly rather
  than faking a reply. A compact **degraded chip** appears in the shell (with a popover
  listing each degraded surface, its floor, and any pending-enrichment backlog) whenever a
  surface is running on its floor, and a notification fires on the transition down
  (`warning`) and on recovery (`info`). A lint test asserts every non-interactive
  model-call site maps to a registered contract, so a future surface can't ship without
  declaring its floor. New `GET /api/resilience/degraded`; two guard-class config switches
  (`resilience.doctor_enabled`, `resilience.degraded_indicator` — a missing/unknown value
  keeps the surface visible).
- **A Doctor tab now diagnoses every subsystem from one read-only view.** Settings →
  Doctor runs **tiered health probes** — process → socket → cheap-RPC → per-capability
  — across memory (db + faiss consistency), channels, local models (availability +
  phantom bindings), app backends (+ interrupted-update leftovers), the SPA
  `static/dist` symlink (the stale-SPA bug-class), and model-provider breakers
  (composed from the guardrails audit). The core doctrine is enforced: **a degraded
  capability never marks the gateway down and never suggests a restart** — only a
  core-tier failure does. Every probe is read-only and fail-safe (an exception
  becomes a failed row, never a 500), and secrets are redacted from probe output. New
  endpoints `GET /api/doctor` (all capabilities, cached 30s) and
  `GET /api/doctor/{capability}` (re-run one card); the dashboard System Health strip
  gains a one-line rollup that appears only when something needs attention and links
  to the tab. Confirm-gated auto-fixes and the trust/debug simulators land in later
  Platform-Resilience sessions.
- **First-party apps now appear in the Store on a plain install.** The published
  first-party apps repository (`github.com/PersonalClaw/PersonalClawApps`) ships as
  a default Store source, so a bare `pip install personalclaw` surfaces every
  first-party app — model providers (OpenAI, Anthropic, Bedrock, Ollama, …),
  search, speech, channels — without the dev workspace tree. They appear
  **uninstalled**: nothing runs until you click Install, so the per-app
  install-consent + provider-agnostic-core contracts are unchanged. The source is a
  built-in default (not user-removable); the dev filesystem source and the
  `PERSONALCLAW_FIRST_PARTY_APPS_DIR` override still work for offline/local-clone
  development. The Store's catalog scan is cached (5-minute TTL) and runs off the
  event loop, so the first open clones once in the background.
- **Every non-interactive model call now passes through one guarded seam.**
  Background LLM calls (the `reasoning` axis behind `one_shot_completion`, the
  goal-loop judges, the loop gates, web-extract) are now wrapped in a
  **model-call guard** — the LLM twin of the network egress chokepoint. It adds a
  **per-provider circuit breaker** (opens after N consecutive failures, half-opens
  after a recovery window): during a provider outage an overnight run fails in
  microseconds instead of stacking timeouts. It adds a **hard wall-clock timeout**
  on every call, and an **attempt-level JSONL audit trail**
  (`~/.personalclaw/model_calls.jsonl`, one line per attempt, trimmed to the most
  recent entries) recording provider, model, latency, tokens, and outcome. The
  **interactive chat stream is deliberately untouched** — a human is watching it.
  `one_shot_completion` also gains a typed **`output_type`** option: pass `dict`
  or `list` to require a parseable JSON shape, and a parse miss is retried once
  with a targeted correction note before raising a loud `OutputContractError` —
  replacing the silent `None` degrade that `parse_llm_json` returned at every
  call site (migrated: web-extract, inbox classify). Goal-loop and eval judge
  verdicts gain a bounded **`reasoning`** field written before the verdict, so a
  structured-output constraint no longer suppresses the judge's chain of thought.
  A new graded provider capability descriptor (`structured_output`:
  `none`/`json_mode`/`json_schema`) lets provider apps opt into native
  schema enforcement in a later change; until then every provider gets the
  universal parse-with-retry path. This is a **clean break** (pre-1.0): the new
  audit trail is additive on-disk state under `~/.personalclaw/` — **run
  `personalclaw snapshot` before upgrading** if you want a rollback point.
  (AUTONOMY-GUARDRAILS §2, Session 1.)
- **Unattended spend now has budgets, and outbound prompts are scanned for
  secrets.** A new **Guardrails** settings section (`config.json` → `guardrails`)
  adds daily spend ceilings for unattended work: set a **max tokens/day** or
  **max dollars/day** and when the day's automated spend hits the ceiling, further
  unattended LLM calls are refused (a cron agent fire is skipped with a one-time
  "daily automation budget reached" notification, a subagent spawn is refused) —
  interactive chat is never budget-gated. Spend is metered at the model-call seam
  into `~/.personalclaw/spend.json` (per-day, pruned after 30 days), with dollars
  estimated from the existing per-model price table (provider-reported cost
  preferred). Every outbound prompt bound for a **remote** provider is scanned for
  secrets/PII (AWS keys, private keys, Slack tokens, emails, phone numbers) and
  handled per a configurable **scan mode**: `warn` (log + send), `redact`
  (substitute + send, the default), or `block` (refuse the call); a local provider
  is always `warn` since its content never leaves the machine. The circuit-breaker
  thresholds from Session 1 are now configurable here too (failure threshold,
  recovery seconds). Defaults are **unlimited budget + redact**, so an existing
  install's behavior is unchanged until you set a ceiling. Clean break (pre-1.0):
  additive on-disk state (`spend.json`) — **run `personalclaw snapshot` before
  upgrading** for a rollback point. (AUTONOMY-GUARDRAILS §1.1, §2.2, Session 2.)
- **A kill switch, a path/action denylist, and a live-write guard for unattended
  work.** Three safety-floor controls land. (1) **`personalclaw incident on`** (and
  `POST /api/incident`) suspends every unattended fire — cron, hooks, event
  triggers, subagent spawns — within one poll interval; **interactive chat keeps
  working**, and resuming requires an explicit `personalclaw incident off` (or
  `POST /api/incident/resume {confirm:true}`). Activation/resume are tamper-evidently
  logged. (2) A **path/action denylist** (`security.autonomy_denylist`, rules of
  `{paths, actions, verdict: block|needs_human}`) is enforced at all three
  action-dispatch seams (script hooks, scheduled jobs, memory-event triggers), so
  an app-contributed action provider inherits it without cooperating; it composes
  with the always-on built-in sensitive-path + destructive-command denylists. A
  `needs_human` rule holds the action and raises a needs-input notification instead
  of dropping it. (3) **`PERSONALCLAW_DISABLE_LIVE_WRITES=1`** makes live,
  hard-to-reverse writes (deleting a downloaded model, a non-GET request to a
  non-loopback host) refuse with a loud typed error instead of executing — and it
  is auto-set for the whole test suite, structurally closing the bug class where a
  destructive test once deleted a real bound model. Guard flags parse fail-safe
  (a missing/typo'd value keeps the guard ON), and the outbound scan now defaults
  to `redact` (never the leaky `warn`), enforced by a schema test. Clean break
  (pre-1.0): additive config + an `incident.json` flag file — **run `personalclaw
  snapshot` before upgrading**. (AUTONOMY-GUARDRAILS §1.2–§1.4, §5, Session 3.)
- **A Guardrails settings surface, a provider-health view, and named safety
  profiles.** The safety floor gets its cockpit and its posture layer. A new
  **Settings → Guardrails** panel gathers the incident kill switch (with a
  one-click toggle), the daily spend budgets, the outbound scan mode, and the
  circuit-breaker tuning — and a **provider-health view** derived from the
  model-call audit (per-provider breaker state, pass rate, p50/p90/p99 latency,
  recent failure modes; `GET /api/models/health`, computed from files already on
  disk — no telemetry). A **persistent incident banner** now shows on every page
  while incident mode is active, with inline Resume. Under the hood, **named safety
  profiles** (`interactive` / `coding` / `review-only` / `cleanup` / `incident` /
  `headless`) become the single object that decides approval + tool grants + egress
  tier + budget + scan for a run; unattended runs (cron, subagents, channel, inbox,
  loop workers) resolve to the read-only **`headless`** profile *by construction*
  from their session key, and a curated **package-registry egress tier** lets a
  sandboxed run reach pypi/npm/crates/GitHub/… without opening the whole internet.
  Defaults preserve today's behavior. Clean break (pre-1.0), additive. (AUTONOMY-
  GUARDRAILS §2.5, §3, §4.2, §4.4, Session 4.)
- **The animated dot-wave backdrop is now a choosable background style.** A new
  **Background** control in Settings → Design → Backdrop & motion switches the
  surface behind chat, the new-chat composer, and onboarding between four modes:
  `waves` (the animated breathing dot-wave surface, default), `still` (the same
  dot field frozen — the lattice without the motion), `glow` (only the soft light
  hugging the composer, no dots), and `none` (a plain, empty canvas). The choice
  persists in your appearance settings and applies live with no reload. Motion
  modes still honor `prefers-reduced-motion` (they render one static frame).
- **PersonalClaw describes its own UI kit, guides you to the parts of itself you
  haven't tried, and hands external agents a routed project context.** Three
  legibility surfaces land together. (1) The `ui/` component kit is now
  self-documenting: each primitive ships a `.doc.ts` object (purpose, props,
  best-practice tenet) compiled into `ui-docs.json` at build time, and two agent
  tools — `ui_search(query)` for a budgeted brief and `ui_get(name)` for
  machine-readable props — let an app-building agent find the right primitive
  instead of hand-rolling chrome; a drift test fails the build if a primitive ships
  without its doc. (2) A **Discover** surface guides you through the parts of
  PersonalClaw you haven't tried yet — a hand-authored catalog of user-facing areas
  (Chat, goal loops, automation, Tasks, Projects, Inbox, Knowledge, Memory, Skills,
  Apps), each a one- or two-sentence lesson with a deep link into the page that owns
  it. It is deliberately NOT tool-derived: the tool surface is an implementation
  detail you're never meant to drive by hand. The dashboard shows a rotating
  spotlight of the first few; a dedicated **Discover hub** (`#/discover`, also in the
  command palette) lists every tip grouped by area. A tip leaves the feed two ways,
  both hide-only: an explicit dismiss that persists forever, and an auto-hide once
  you've actually used that area (detected from state that already exists — a chat on
  disk, a knowledge item, a scheduled job…). It only points and hides, never enables
  anything, and the whole surface is behind the `legibility.discover_tips` config
  flag. (3) PersonalClaw
  can act as a **routed-context provider** for external coding agents: per project it
  assembles a tiered manifest — hard rules/brief at the top, scored memories + skills
  + knowledge *pointers* in the middle, and an L0 catalog of what was NOT loaded (with
  the tool to pull each) at the bottom — exposed as the in-process `get_context` MCP
  tool and, opt-in per project (`legibility.context_adapters`, default off), rendered
  into the project's `CLAUDE.md` / `AGENTS.md` / `.cursorrules` inside a
  `<!-- PCLAW:START -->` fence that regenerates in place and never touches your own
  content outside the markers. Memory-derived and knowledge-derived content stay under
  distinct headings, and knowledge items render as titled pointers — never inlined
  bodies. A "Refresh context files" action on the project page (re)writes the block.
- **Apps surface their skills and backend routes to the agent (declared, not
  discovered).** An app now declares two legible surfaces in `app.json`, both
  readable without executing app code. `skills[]` names SKILL.md directories the
  app ships and OWNS: on enable they seed into the user skills tree **through the
  supply-chain chokepoint** (quarantine → scan at the app's trust tier →
  `.pclaw-lock.json` provenance) — an app skill never bypasses the gate just
  because it arrived inside an app — and are removed provenance-keyed on disable,
  never touching a user's own or another app's skill. `backend.routes[]` names the
  app's agent-callable HTTP surface (`op`, method, path, summary, param/body
  hints); one generic tool provider turns every enabled app's `agentCallable`
  routes into `app_<name>_<op>` tools (risk keyed off the verb) and drives them
  through the existing loopback reverse proxy, and a `call-app-route` action lets
  hooks/crons fire the same routes — both share one resolver so the callable gate
  can't diverge. The routes also render into `GET /api/manifest`'s `app_surfaces[]`
  (a non-callable route documents the surface with `tool: null`), and a declared
  route whose backend answers 404 raises a one-shot drift notification so a
  dead-declared route is caught the moment it's called. First-party Growth (17
  routes) and Minutes (24 routes) ship their route tables.
- **Offline agent reference + `pclaw-api` skill** — an agent driving PersonalClaw
  from outside a running gateway now reads exact tool/route signatures instead of
  guessing them. The distribution ships a generated markdown reference
  (`personalclaw/reference/`: every registered tool with its input schema +
  examples, the agent-callable HTTP routes, and the provider taxonomy) rendered
  from the same source as the live `GET /api/manifest`, plus a bundled `pclaw-api`
  operator skill (the never-guess-copy-it + verify-after-mutate discipline). Locate
  the files from the installed binary with the new `personalclaw doctor --paths`,
  which prints the resolved reference / config / skills / install directories. A
  drift test byte-compares the checked-in reference against a fresh render, so a
  tool or route added without its metadata reddens the build.
- **Render-smoke gate** (`npm run smoke:render`): the built SPA is now loaded
  in headless Chromium — key routes must mount real content with no uncaught
  errors — before any frontend-affecting push (repository-owned pre-push hook,
  `npm run hooks:install`) and on every PR (CI `web` job). Closes the
  verification hole behind the v0.1.0 blank dashboard, where typecheck, unit
  tests, and the production build all passed without ever rendering the
  artifact in a browser.

### Changed

- **The dashboard's system indicators are now a docked bottom rail.** The System
  strip (uptime, version, CPU/memory/network/disk/load, triggers, subagents, and
  the update action) was the last item in the scrolling column; it's now a
  shell-like rail pinned to the bottom edge, so the live indicators stay visible
  while the rest of the dashboard scrolls. The dashboard header's at-a-glance
  pulse strip sheds two now-redundant indicators: the gateway connectivity pill
  ("Live/Offline") and the gateway-version pill. The app shell's top-right corner
  already carries a live connectivity dot on every page, and its expanded system
  card now shows the gateway version (sourced from `/api/system`) — so the header
  strip is just the live count pills. The rail itself is width-responsive (a CSS
  container query, keyed to the content-width preset + sidebar, not the viewport):
  it sheds the decorative CPU sparkline and the metric word-labels — icon + value
  keep carrying the reading, with the full text on hover — to stay on one line as
  the available width tightens, and the "Details →" action stays anchored to the
  right edge.

## [0.1.1] — 2026-07-22

### Fixed

- **Blank dashboard in v0.1.0 (critical).** The released SPA crashed at first
  render with `TypeError: Cannot read properties of null (reading 'useContext')`
  — a dependency-group bump had split the installed tree across React 18 and
  React-DOM 19 (the classic dual-React invalid-hook failure), so every install
  kind (pip/uv, container, git) served an empty page. The web toolchain is
  reverted to its known-good React-18 set, a root npm `overrides` pins
  `@types/react`/`@types/react-dom` so transitive packages cannot drag React-19
  types back in, and the lockfile is regenerated from a clean install so the
  declared and resolved trees agree.
- **`monaco-editor` was never declared as a dependency** — it is a peer of
  `@monaco-editor/react` and imported directly, but resolved only by lockfile
  accident; a clean reinstall broke the build. Now a direct dependency
  (`^0.55.1`, the version v0.1.0 shipped transitively).

## [0.1.0] — 2026-07-19

### Added

- **App-contributed CLI seams** — an app can now hook into `personalclaw setup` and
  `personalclaw doctor` via manifest `cli.setup` / `cli.doctor` (`module:function`),
  and declare its log namespaces via `loggerRoots`. `personalclaw setup --app <name>`
  runs just one app's setup step. Core names no channel vendor in its CLI.
- **CI & release engineering** — GitHub Actions for both repos: `ci.yml`
  (lint/test/web/rails, ≤10-min budget) and `full.yml` (3.12/3.13 × ubuntu/macos
  matrix, audit, coverage) on core; manifest-validate/tests/boundary on the apps repo.
  A tag-triggered `release.yml` builds the wheel (with the prebuilt SPA) + multi-arch
  GHCR images, publishes to PyPI via Trusted Publishing behind an owner-approval gate,
  and attaches an SBOM + build-provenance attestations. `uv.lock` pins the dependency
  graph (CI installs `--locked`); Dependabot watches pip/npm/actions weekly. See the
  [supply-chain posture](README.md#supply-chain).

### Changed

- **Provider-boundary completion (Slack residue retired from core):** the Slack
  channel app now ships its own token/slash-command setup and doctor probe (via the
  new `cli.setup`/`cli.doctor` seams) instead of living hardcoded in core's CLI; app
  logger roots are derived from installed manifests (`constants.APP_LOGGER_ROOTS`
  removed); `slack-sdk` is no longer a core runtime dependency (kept as the `[slack]`
  extra, and the slack-channel app declares it via manifest `pythonDependencies`, which
  the app-install pipeline installs). A residue-sweep test + a machine-checked keeps
  table (`docs/architecture/provider-boundary-keeps.txt`) prevent vendor residue from
  regrowing in core.
- **LLM SDKs demoted out of core dependencies (`openai`, `anthropic`):** a bare
  `pip install personalclaw` no longer pulls the OpenAI or Anthropic SDKs. They now
  ship via (a) the `[openai]` / `[anthropic]` packaging extras for pip/uv users, and
  (b) the branded provider apps' manifest `dependencies.pythonDependencies`, which the
  app-install pipeline installs into the shared venv (plan 32 T2.1). The provider
  adapters import their SDK lazily and now raise a clear `MissingSDKError` naming the
  exact `pip install 'personalclaw[openai]'` remedy (and `personalclaw doctor`) when a
  hosted provider is used without its SDK. This trims the default install; users who
  install a provider app or the matching extra are unaffected (plan 34 T1.4).
- **Self-update is now install-kind aware (git · pip · container · desktop):** the
  in-app updater (Settings → Updates) and the update check no longer assume a git
  checkout. The availability signal is the **latest GitHub release tag** (ETag-cached,
  offline-tolerant) compared against the running version — tags are the release truth
  for every install path. Apply adapts to the install kind: a **git** checkout runs the
  existing pull → reinstall → rebuild → restart pipeline (with a new *Developer update
  mode* toggle, `dashboard.update_dev_mode`, to track every commit instead of only
  tagged releases); a **pip/uv/pipx** install runs `pip install -U personalclaw==<tag>`
  into its own interpreter and gracefully re-execs (no web build — the wheel ships the
  dashboard); a **container** install shows the exact `docker compose … pull && up -d`
  commands (no in-place apply); a **desktop** install delegates to the app shell. The
  Updates panel renders the right affordance per kind, and git installs also surface
  commits-behind as secondary info.

  This is a **clean break** (pre-1.0): the old git-only updater is replaced directly,
  not gated — the migration-backed gate machinery is deferred, so there is no
  `update_kind_aware` gate to flip (owner decision 2026-07-20). Behavior change: a git
  checkout now updates on new *release tags* by default instead of every commit — flip
  *Developer update mode* on to restore per-commit updates. **Run `personalclaw
  snapshot` before updating.** (plan 34 S4.)

### Removed

- **`personalclaw gateway --slack-only`** — the legacy alias for `--headless` is
  removed. Use `--headless`.

### Fixed

- **Release wheel now bundles the SPA when built via `python -m build`.** The release
  pipeline (and `make build`) build the sdist first, then build the wheel from that
  sdist; the built `web/dist` was not included in the sdist, so the wheel-from-sdist
  shipped without the dashboard and failed `scripts/verify_wheel.py`. A new
  `MANIFEST.in` grafts `web/dist` into the sdist, which also makes the sdist itself
  self-contained (a wheel built from the PyPI sdist serves the dashboard too). Guarded
  by `tests/test_sdist_bundles_spa.py`. (plan 34; caught in the release dry-run.)


Initial public release — the first end-to-end PersonalClaw: a self-hosted, local-first,
provider-agnostic personal AI agent behind one gateway and one web dashboard.

### Added

- **Agentic chat** — multi-session chat with tool use and approval controls, session
  forking/undo, answer variants/regenerate, folders/tags/kanban, side conversations,
  per-session model and reasoning-effort overrides, and temporary/incognito memory modes.
- **Goal loops** — give the agent a target; it classifies, plans, and loops autonomously
  under a deterministic supervisor you can pause, nudge, or stop.
- **Memory** — layered semantic/episodic/procedural memory with active recall, after-turn
  learning from corrections, promotion of repeated facts, and an Obsidian-compatible vault.
- **Knowledge base** — document/media/web ingestion, AI enrichment, entity extraction, a
  knowledge graph, and semantic search wired into chat context.
- **Skills** — SKILL.md procedures with a marketplace, supply-chain scanning on install,
  session-scoped ephemeral skills, and an approval inbox for agent-proposed skills.
- **Automation** — cron/interval/webhook triggers, background subagents, a channel-watching
  inbox with drafted replies, and workflow SOPs surfaced on match.
- **App platform** — a permission-gated, scanner-gated Store: model providers, search,
  speech (STT/TTS), local models, channel connectors, agents, and full backend+UI apps,
  each installed through a quarantine → scan → consent lifecycle with subprocess isolation.
- **Agent runtimes** — the built-in native loop plus external CLI agents over ACP
  (Agent Client Protocol) as pluggable runtimes.
- **Model layer** — per-use-case model bindings (chat, background, embedding, ingestion,
  speech) over 16 provider apps; nothing is hardwired to a vendor.
- **Security** — four auth modes (loopback-forced `none`), command screening (denylist +
  suspicious-pattern watchers), an OS child sandbox, one egress chokepoint with host
  policy, untrusted-content fencing, a non-overridable "dangerous" install verdict, an
  HMAC-chained tamper-evident security event log, and credential-excluding exports.
- **Delivery surfaces** — local gateway, Docker Compose, systemd/launchd service install,
  a desktop shell, and portable snapshot/restore.

### Notes

- Single-user, self-hosted, MIT-licensed. **Zero telemetry** — no usage data leaves your
  machine.
- Requires Python 3.12+; a model-provider API key (or a local Ollama) to start chatting.

[Unreleased]: https://github.com/PersonalClaw/PersonalClaw/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/PersonalClaw/PersonalClaw/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/PersonalClaw/PersonalClaw/releases/tag/v0.1.0

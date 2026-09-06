# The launch-demo click-path

The 60–90s silent capture that opens the launch is the first thing a stranger sees, so the
thing that has to outlive the video file is **the path itself**. This document is that
path, and [`capture_demo.mjs`](capture_demo.mjs) is its executable half: same beat ids,
same per-beat seconds. A recording nobody can re-make is a liability, so when the UI moves
the script fails loudly on the missing target and this table is what tells you what the
replacement beat has to accomplish.

Everything below is recorded against the **`demo-home` seed fixture** (the one
`personalclaw gateway --seed demo-home` installs), never an empty home and never a
hand-arranged one. Read [§ What the demo home cannot show](#what-the-demo-home-cannot-show)
before assuming a beat is missing by accident.

## Recording it

```bash
# 1. the gateway serves the built SPA
npm ci && npm run build --workspace web   # or: make web-build

# 2. an ISOLATED, seeded home on a free high port — never ~/.personalclaw, and never
#    a port another process already owns
PERSONALCLAW_HOME=/tmp/personalclaw-demo \
PERSONALCLAW_WORKSPACE=/tmp/personalclaw-demo/workspace \
PERSONALCLAW_AUTH_MODE=none \
  personalclaw gateway --seed demo-home --seed-replace --port 17410 --no-open

# 3. play the path and record it
PCLAW_URL=http://127.0.0.1:17410 node docs/demo/capture_demo.mjs
```

Output: `personalclaw-tour.webm` (Playwright's screencast) and, when `ffmpeg` is on PATH,
`personalclaw-tour.mp4` — 1280×720, H.264, **no audio track at all**. Silence is not a
mixing decision here; the screencast never records audio and the transcode passes `-an`.

Viewport is 1440×810 (16:9, the aspect a site hero wants) and the theme is dark, which is
the design's home ground — near-black Night Canvas is the app background per
[`web/DESIGN.md`](../../web/DESIGN.md).

## The path

Eight beats, 82s total. "Understands" is the one sentence the beat has to land; if a
rewritten beat lands that sentence, it is a valid substitute.

| # | Beat | s | What is clicked | What the viewer should understand |
|---|---|---|---|---|
| 1 | `B1-home` | 14 | Land on Home. Pointer crosses the Needs-you tile, then the health footer. | This is one local process serving a real dashboard — uptime, version, CPU/memory/disk, seeded work already in it. Not a tenant of anything. |
| 2 | `B2-chat` | 11 | Nav → **Chat**. Click the composer, type a real prompt. Pointer rests on the Persistent / Temporary / Incognito pills. | This is where you talk to it, and how long a conversation persists is a per-message choice you make, not a policy someone else set. |
| 3 | `B3-receipts` | 9 | Nav → **Settings**. Pointer crosses the Security card, then the audit-log card. | The safety surface is countable: N denied-command rules, N suspicious patterns, N redaction paths, and an append-only audit log that says "chain intact" with the number of events it verified. |
| 4 | `B4-guardrails` | 10 | Type `guardrail` in the settings search, click the **Guardrails** card. Pointer crosses the incident kill switch, then the outbound-scan mode. | Unattended work runs under a ceiling you set — a kill switch, a daily token/dollar cap, and an outbound secret scan you can set to warn, redact or block. Interactive chat is never touched by any of it. |
| 5 | `B5-loop` | 14 | Deep-link to the cross-project loop list, click **View all loops**, click the seeded loop. | A goal loop is a durable object: a goal, an explicit *done-when*, a completed state, and a numbered set of recorded findings — with the latest finding quoted verbatim. |
| 6 | `B6-knowledge` | 10 | Nav → **Knowledge**. Type `digest` in the search box. | The corpus is local and the search is real: five items with types and tags narrow to the matching note as you type. |
| 7 | `B7-tasks` | 10 | Nav → **Tasks**, then the **Kanban board** view. Pointer rests on the Blocked column. | Work is tracked with real dependencies, and a dependency actually blocks — the board has a populated Blocked column, not a decorative one. |
| 8 | `B8-close` | 4 | Nav → **Home**. | Land back where you started. One gateway, one machine, yours. |

Beat 5 is the only navigation that is not a click on visible chrome. It is deliberate and
it is not a shortcut: v0.1.3's nav rail has no Loops item — a loop is normally reached from
the project that owns it, and on the seeded fixture that project row is not a link — so the
cross-project list is reached by route. Say so if anyone asks; do not restage it as a click.

## What the demo home cannot show

`DL-5` specifies the arc **chat → approval → loop → knowledge → artifact**. Three of those
five beats cannot be shown honestly on the seeded demo home, so the path above is the
shortened, honest version rather than a full-length one with a staged screen in it.

| Specified beat | Status | Why |
|---|---|---|
| chat | **surface only** | The seed configures no model provider (it has no app, no credential, no binding — by design; it is a fixture, not an account). The chat surface, the composer and the persistence modes are all real; an agent *turn* is not available, so the script types a prompt and never sends it. |
| approval | **substituted** | A tool approval only exists while an agent turn is waiting on one — the pending-approval set is in-memory and written from the agent's tool-permission hook. No model means no turn means no approval, and there is no seeded one to show. Beats 3–4 show the gate's *policy* instead (guardrails, scan mode, audit chain) and are labelled as policy, not as an approval happening. |
| loop | **real** | The fixture ships one completed loop with its goal, done-when, and six recorded findings. |
| knowledge | **real** | Five real items in the store, searched through the real index. |
| artifact | **dropped** | Artifacts are produced by the agent into the home's `artifacts/` tree; the fixture has none and the UI has no hand-authoring affordance. Creating one out of band would be scratch state dressed as a demo. Beat 7 (real task dependencies) takes the slot. |

A 55-second honest demo beats a 75-second one with a mocked screen; this is an 82-second
honest one. Two of the five specified beats are shown as specified, one is shown as surface
only, one is substituted, and one is dropped. **When the demo home grows a bindable local
model, beats 2 and 4 become the real chat turn and the real approval prompt** — that is the
single change that would let this path meet the specified arc, and it belongs to the
fixture, not to this script.

## Where the file lives

The capture is **not committed to this repo.** At 82s the H.264 file is ~1.2 MB and the
webm ~6.6 MB; this repo has no Git LFS and no committed media convention above a few
hundred kilobytes (the largest tracked binaries are the ~250–540 KB screenshots under
`docs/screenshots/` and `temp-screenshots/`). A megabyte-plus binary that is regenerated
every release would be a permanent, unshrinkable addition to every clone, and unlike the
screenshots nothing in this repo renders it.

So: the script is committed, the path is committed, the artefact is regenerated. The video
belongs in the surface that shows it — see the handoff below.

## Site-hero handoff (not reachable from this repo)

The site hero lives in the **`personalclaw.dev` repo**, in `src/pages/index.astro`. Nothing
committed here can be referenced from it automatically: the website's doc-sync pulls
`docs/{guides,reference,architecture,security,research}` and *deliberately excludes*
`docs/screenshots/` and `docs/design/` as assets, so the site carries its own copies of
product media under `src/assets/` and `public/`.

Referencing the capture from the hero therefore needs one commit in that repo, not this
one:

1. Drop `personalclaw-tour.mp4` at `public/media/personalclaw-tour.mp4`.
2. Reference it from the hero in `src/pages/index.astro`. The hero slot currently holds
   `<SystemWindow />`, the still-screenshot carousel — whether the capture replaces that
   carousel or sits beside it as a "watch the 82-second tour" affordance is a site-design
   call, and the carousel has the better largest-contentful-paint story, so the default
   recommendation is *beside*, not *instead of*.
3. If it is embedded as a `<video>`: `muted playsinline preload="metadata"` and a poster
   frame, so a hero that autoplays stays silent and cheap.

## Brand cards

The 1280×640 repo social-preview cards that ship with this launch are generated, not drawn.
See [`docs/brand/README.md`](../brand/README.md).

# The launch-demo click-path

The 60–90s silent capture that opens the launch is the first thing a stranger sees, so the
thing that has to outlive the video file is **the path itself**. This document is that
path, and [`capture_demo.mjs`](capture_demo.mjs) is its executable half: same beat ids,
same per-beat seconds. A recording nobody can re-make is a liability, so when the UI moves
the script fails loudly on the missing target and this table is what tells you what the
replacement beat has to accomplish.

Everything below is recorded against the **`demo-home` seed fixture with a local model
bound** (`personalclaw gateway --seed demo-home --seed-local-model`), never an empty home
and never a hand-arranged one. The model binding is what makes the specified arc showable:
chat, approval and artifact exist only as the product of a real turn, so before
`--seed-local-model` the first version of this path had to drop or substitute three of the
five beats. Read [§ What is real, and what is still not shown](#what-is-real-and-what-is-still-not-shown)
before assuming a beat is missing by accident.

## Recording it

```bash
# 1. the gateway serves the built SPA
npm ci && npm run build --workspace web   # or: make web-build

# 2. an ISOLATED, seeded home on a free high port, with a local model bound — never
#    ~/.personalclaw, and never a port another process already owns.
#
#    PERSONALCLAW_PORT, not `--port`: `--port` moves the gateway but NOT its MCP tool
#    subprocesses, which resolve their API base from `cfg.dashboard.url` and fall back to
#    127.0.0.1:10000 (issue #2539). With `--port` the artifact_save call in beat 3 is
#    dispatched at whatever gateway owns 10000 and hangs. PERSONALCLAW_PORT overrides both.
PERSONALCLAW_HOME=/private/tmp/personalclaw-demo \
PERSONALCLAW_WORKSPACE=/private/tmp/personalclaw-demo/workspace \
PERSONALCLAW_AUTH_MODE=none \
PERSONALCLAW_PORT=18420 \
  personalclaw gateway --seed demo-home --seed-replace --seed-local-model \
    --approval interactive --no-open

# 3. play the path and record it
PCLAW_URL=http://127.0.0.1:18420 node docs/demo/capture_demo.mjs
```

`--approval interactive` is load-bearing, not a precaution: it is what makes the agent ask
before every tool call, which is the thing beat 3 exists to show. Recording under `reads`
or `yolo` would produce a smoother take of a product that does not ask.

Two env overrides exist for re-recording, both documented in the script's header:
`DEMO_SESSION` skips the pre-roll and re-uses a session already parked on the gate (a
broken target otherwise costs four minutes of model time per attempt), and
`DEMO_ARTIFACT` names the artifact when the model did not take the slug the prompt asked
for.

Output: `personalclaw-tour.webm` (Playwright's screencast) and, when `ffmpeg` is on PATH,
`personalclaw-tour.mp4` — 1280×720, H.264, **no audio track at all**. Silence is not a
mixing decision here; the screencast never records audio, the transcode passes `-an`, and
the resulting mp4 carries exactly one stream.

Viewport is 1440×810 (16:9, the aspect a site hero wants) and the theme is dark, which is
the design's home ground — near-black Night Canvas is the app background per
[`web/DESIGN.md`](../../web/DESIGN.md).

## The path

Seven beats, 83s budgeted. "Understands" is the one sentence the beat has to land; if a
rewritten beat lands that sentence, it is a valid substitute.

| # | Beat | s | What is clicked | What the viewer should understand |
|---|---|---|---|---|
| 1 | `B1-home` | 9 | Land on Home. Pointer crosses the health footer. | This is one local process serving a real dashboard — uptime, version, CPU/memory/disk, seeded work already in it. Not a tenant of anything. |
| 2 | `B2-chat` | 14 | Open the running chat session. Pointer crosses the prompt that was sent, then the model's pending tool call. | You asked it something in your own words, and the words in the call it now wants to make are the model's own — a real turn, on a model running on this machine. |
| 3 | `B3-approval` | 18 | Pointer crosses the **Caution** risk chip, the **Writes files** blast-radius chip, and the remember-scope picker. Then click **Allow**. | It stopped and asked before writing anything. The ask states the tool, the arguments, the risk, what the call can touch, and how far your answer reaches — and the narrowest scope, the one that remembers nothing, is the one already selected. |
| 4 | `B4-loop` | 14 | Deep-link to the cross-project loop list, click **View all loops**, click the seeded loop. | A goal loop is a durable object: a goal, an explicit *done-when*, a completed state, and a numbered set of recorded findings — with the latest finding quoted verbatim. |
| 5 | `B5-knowledge` | 12 | Nav → **Knowledge**. Type `digest` in the search box. | The corpus is local and the search is real: five items with types and tags narrow to the matching note as you type. |
| 6 | `B6-artifact` | 12 | Nav → **Artifacts**, click the new artifact. Pointer crosses its DETAILS footer. | The thing you allowed actually happened. The document the agent wrote is on your disk, rendered, versioned at v1, with one recorded event — not a chat message claiming it wrote something. |
| 7 | `B7-close` | 4 | Nav → **Home**. | Land back where you started. One gateway, one machine, yours. |

Beats 2 and 4 are the only navigations that are not clicks on visible chrome, and both are
deliberate. Beat 4: v0.1.3's nav rail has no Loops item — a loop is normally reached from
the project that owns it, and on the seeded fixture that project row is not a link — so the
cross-project list is reached by route. Beat 2: the recorded take opens one *specific*
session, and a fresh browser profile's Chat lands on a new empty one, so the parked session
is reached by route too. Say so if anyone asks; do not restage either as a click.

Beats 3 and 6 are the pair that has to hold together. The artifact in beat 6 exists **only**
because the click in beat 3 happened: the beats are separated by the loop and knowledge
beats, which is also what gives the tool time to run, and beat 6 looks the artifact up by
the slug the prompt asked for so that a model which named it something else fails the beat
loudly rather than quietly showing a different file.

## Why the turn is started before the recording

The capture has two phases and only the second is recorded. The first — `preroll()` in the
script — sends the prompt in a non-recording browser context and waits for the turn to park
on the tool-permission gate. **This is a latency decision, not a staging one.**

A local 12B model on a shared machine took **254s** to reach the tool call on the run this
document describes, and 97–200s on the runs before it; the variance is queue contention on
the Ollama server, not the model. A 60–90s asset cannot contain three minutes of spinner,
and padding it with one would be a worse asset than starting the clock at the moment the
product does something. So the recording opens on the parked gate.

Everything the recording shows is real and none of it is pre-decided:

- The prompt is a user's sentence, typed into the real composer, and it is on screen in
  beat 2 for the viewer to read. It names the three long-reads so the model has material —
  a digest of "Source 1, Source 2, Source 3" would be a real turn producing a worthless
  document — but it does **not** tell the model which tools to call or forbid it any.
- The gate is genuinely pending when the recording starts. It survives the browser being
  closed and re-opened because a chat approval is persisted on the session's `permission`
  message, not held in a page's memory; the pre-roll and the recorded take are different
  browser contexts, which is how that is proven rather than assumed.
- The Allow click in beat 3 is the real decision on that real pending request. It is not
  auto-approved, the gate is not weakened, `--approval interactive` is in force, and the
  scope left selected is the narrowest one.
- On the recorded run the model made two `knowledge_search` calls (auto-approved as reads,
  visibly ticked in the transcript) and then `artifact_save`, which parked. **Nothing was
  approved off camera.** The pre-roll *can* approve a precursor call the model chose to
  make before the write — it prints the tool name of each one it approves, so this claim
  stays checkable against the run log — and it stops the moment the write parks so the one
  decision on camera is always the write.

The audit log is the receipt. For the recorded run the `artifact_save` chain is
`invoked → approved → success → completed`, and the `approved` entry carries
`{'reason': 'interactive', 'risk': 'caution'}` with its hash link intact.

## What is real, and what is still not shown

`DL-5` specifies the arc **chat → approval → loop → knowledge → artifact**. All five are
now shown, in that order, and none is substituted or staged.

| Specified beat | Status | Evidence |
|---|---|---|
| chat | **real** | A turn against `gemma4:12b` bound into the seeded home by `--seed-local-model`. The user's prompt and the model's own generated text are both on screen in beat 2. Previously surface-only: the fixture bound no provider, so the old script typed a prompt and never sent it. |
| approval | **real** | The turn parks on the tool-permission gate for `artifact_save`, risk `caution`; a human click on **Allow** resolves it and the tool runs. Previously substituted with policy screens (guardrails, scan mode, audit chain), honestly labelled as policy. |
| loop | **real** | The fixture ships one completed loop with its goal, done-when, and six recorded findings. |
| knowledge | **real** | Five real items in the store, searched through the real index — and on the recorded run the agent searched that same corpus itself, twice, before writing. |
| artifact | **real** | `reading-digest-week-34`, markdown, v1, written by the agent into the home's `artifacts/` tree by the approved call, opened and rendered in beat 6. Previously dropped: no model meant no agent-produced artifact, and the UI has no hand-authoring affordance. |

Three things the capture does **not** show, stated here so nobody has to infer them:

- **The prompt being typed and sent.** That is the pre-roll, for the latency reason above.
  The prompt itself is legible on screen; the keystrokes are not.
- **The composer's Persistent / Temporary / Incognito pills.** The old beat 2 rested on
  them. They are a choice made for a *new* chat and are not mounted on a session that
  already has a turn in it, so this path cannot honestly show them and does not pretend to.
  Persistence is real; it is just not on this route.
- **A clean top-right corner.** The shell shows a `Transcription degraded` chip throughout,
  and it is telling the truth: `--seed-local-model` binds chat and embedding models, and
  nothing local serves speech-to-text, so that surface is running on its no-model floor.
  Removing it would mean either hiding a truthful warning or binding an STT model the
  fixture cannot carry. It stays.

The path is also **shorter than its predecessor by a beat** — the settings/receipts and
guardrails beats are gone. They existed as stand-ins for the approval beat, and a stand-in
for something you can now show is just spent time. Their seconds went to the real gate.

### On the model's output

The viewer will judge the product by what is on screen, so: the digest the model wrote is
**good enough to ship, not better than that**. 89–110 words, one accurate sentence per
read, and an *Action* line each — and on the recorded run the summaries carry substance
the model got from searching the local corpus, not from the prompt. What it is not is
distinctive prose; the *Action* lines in particular read like an LLM being helpful ("Set up
a new RSS reader to organize my personal news feeds"). A 12B local model is what a
credential-free committed path can use, and this is what one produces. If the hero needs
better copy on screen, the lever is a bigger model at record time, not a hand-edited
artifact — an artifact edited into shape would make beat 6 a lie about beat 3.

## Where the file lives

The capture is **not committed to this repo.** At 84s the H.264 file is 1.2 MB and the webm
7.5 MB; this repo has no Git LFS and no committed media convention above a few hundred
kilobytes (the largest tracked binaries are the ~250–540 KB screenshots under
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
   carousel or sits beside it as a "watch the 84-second tour" affordance is a site-design
   call, and the carousel has the better largest-contentful-paint story, so the default
   recommendation is *beside*, not *instead of*.
3. If it is embedded as a `<video>`: `muted playsinline preload="metadata"` and a poster
   frame, so a hero that autoplays stays silent and cheap.

## Brand cards

The 1280×640 repo social-preview cards that ship with this launch are generated, not drawn.
See [`docs/brand/README.md`](../brand/README.md).

#!/usr/bin/env node
// PersonalClaw launch-demo capture — plays the scripted click-path in CLICKPATH.md
// against a gateway seeded with the `demo-home` fixture AND a bound local model, and
// records it silently.
//
// The click-path doc is the contract; this file is its executable half. Every beat
// below carries the same id as the beat in CLICKPATH.md, and the per-beat `seconds`
// here are the timings that doc publishes. Change one, change both.
//
// WHY a script and not a hand-driven screen recording: the UI moves. A hand-driven
// capture is unreproducible the moment a nav label or a route changes, so the
// recording rots and nobody can re-make it. This script fails loudly on a missing
// target instead, which is the signal to update the path.
//
// TWO PHASES. The recorded take is the second one. The first — `preroll()` — sends a
// real prompt in a NON-recording context and waits for the turn to park on the
// tool-permission gate, because a local 12B model needs 1.5-4 minutes on a shared
// machine to get there and a 60-90s asset cannot contain that dead time. Nothing is
// staged by this: the prompt, the model's output, the gate, the human decision and the
// artifact are all real, and the recorded take is where the gate is read and approved.
// See CLICKPATH.md § "Why the turn is started before the recording".
//
// Prereqs:
//   1. `npm ci && npm run build --workspace web` (the gateway serves the built SPA;
//      see `make web-build`, which also links src/personalclaw/static/dist).
//   2. A local Ollama with a chat model pulled (nothing else is bindable without a
//      credential, which is why it is the provider a committed path can use).
//   3. A gateway on an ISOLATED home seeded from the demo fixture WITH that model
//      bound — never your real ~/.personalclaw, and never a port another process owns.
//      Use PERSONALCLAW_PORT, not `--port`: `--port` moves the gateway but not its MCP
//      tool subprocesses, which resolve from `cfg.dashboard.url` and default to
//      127.0.0.1:10000 (issue #2539), so a tool call would hang on someone else's
//      gateway.
//
//        PERSONALCLAW_HOME=/private/tmp/personalclaw-demo \
//        PERSONALCLAW_WORKSPACE=/private/tmp/personalclaw-demo/workspace \
//        PERSONALCLAW_AUTH_MODE=none \
//        PERSONALCLAW_PORT=18420 \
//          personalclaw gateway --seed demo-home --seed-replace --seed-local-model \
//            --approval interactive --no-open
//
// Usage:
//   PCLAW_URL=http://127.0.0.1:18420 node docs/demo/capture_demo.mjs
//
//   DEMO_SESSION=<chat-key>  skip the pre-roll and re-record against a session that is
//                            ALREADY parked on an artifact_save gate (a re-record after
//                            a broken target costs 4 minutes of model time otherwise).
//   DEMO_ARTIFACT=<slug>     the artifact the recorded approval produces, when the model
//                            did not take the name the prompt asked for.
//
// Output: <OUT_DIR>/personalclaw-tour.webm, plus personalclaw-tour.mp4 when ffmpeg
// is on PATH. Silent by construction — Playwright's screencast records no audio.
// OUT_DIR defaults to /tmp/personalclaw-demo-media because the capture is far too large to
// commit to this repo; see CLICKPATH.md § "Where the file lives".

import { chromium } from 'playwright';
import { mkdir, rename, stat } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';

const BASE = process.env.PCLAW_URL || 'http://127.0.0.1:18420';
const OUT_DIR = process.env.OUT_DIR || '/tmp/personalclaw-demo-media';
const VIEWPORT = { width: 1440, height: 810 }; // 16:9 — the aspect a site hero wants
const NAME = 'personalclaw-tour';

// The artifact the recorded approval produces. The prompt asks for this slug by name;
// the model is what actually passes it, so B6 fails loudly rather than quietly showing
// some other artifact if it chose differently. DEMO_ARTIFACT overrides.
const ARTIFACT = process.env.DEMO_ARTIFACT || 'reading-digest-week-34';
// The library grid renders an artifact's display NAME, and the name is the model's to
// choose: asked for `reading-digest-week-34` it saved that slug under the display name
// "Reading Digest Week 34". Matching the slug literally therefore fails on an artifact
// that is right there on screen, so the beat matches the slug's words in either spelling.
const ARTIFACT_RX = new RegExp(ARTIFACT.replace(/-/g, '[- ]'), 'i');

// The prompt the pre-roll sends. Deliberately a USER's sentence and not tool plumbing:
// it names the three long-reads (so the model has material and the digest reads like a
// digest rather than "Source 1, Source 2"), names the artifact and its kind, and caps the
// length. It does NOT tell the model which tools to call or forbid it any — what the
// model chooses to do is part of what is being shown.
const PROMPT = 'Draft this week’s reading digest from my three saved long-reads — '
  + '"Why RSS outlived its obituaries", "SQLite as an application file format", and '
  + '"Picking an embedding model for a small personal corpus" — one sentence each, then a '
  + `line on what I’m doing about it. Save it as a markdown artifact called ${ARTIFACT}. `
  + 'Under 140 words.';

// The gate the recorded take approves. Anything the model asks for BEFORE this one is a
// precursor it chose (it likes to list the artifact store first); the pre-roll approves
// those off camera and names each one, so that the one decision on camera is the WRITE.
const RECORDED_TOOL = 'artifact_save';

// ── the synthetic pointer ─────────────────────────────────────────────────────
// Playwright's screencast does not draw a cursor, so a click-path recording would
// show effects with no visible cause. This overlay is driven by the SAME real mouse
// events Playwright dispatches (it only listens), so it reports the input rather
// than re-enacting it. Coral is reserved for "the agent is alive" (web/DESIGN.md,
// the One Voice Rule), so the pointer itself is neutral white and only the click
// ripple borrows the brand coral.
function installCursor() {
  // Init scripts run at document_start, BEFORE the parser has created <html>, so a
  // straight append here would throw on a null documentElement and the pointer would
  // silently never exist. Mount on DOMContentLoaded when the document is still parsing.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  } else {
    mount();
  }
  function mount() {
  const style = document.createElement('style');
  style.textContent = `
    #pc-demo-cursor{position:fixed;left:-40px;top:-40px;width:15px;height:15px;border-radius:50%;
      pointer-events:none;z-index:2147483647;transform:translate(-50%,-50%);
      background:radial-gradient(circle at 34% 32%,#fff,#e3e3e3 58%,rgba(227,227,227,.35) 72%,rgba(227,227,227,0) 74%);
      box-shadow:0 1px 6px rgba(0,0,0,.55),0 0 0 1px rgba(15,15,15,.55)}
    .pc-demo-ripple{position:fixed;width:16px;height:16px;border-radius:50%;pointer-events:none;
      z-index:2147483646;transform:translate(-50%,-50%);border:2px solid #ff6b5b;
      animation:pc-demo-ripple 520ms cubic-bezier(.16,.84,.44,1) forwards}
    @keyframes pc-demo-ripple{from{opacity:.9;width:14px;height:14px}
      to{opacity:0;width:64px;height:64px}}`;
  document.documentElement.appendChild(style);
  const dot = document.createElement('div');
  dot.id = 'pc-demo-cursor';
  document.documentElement.appendChild(dot);
  addEventListener('mousemove', (e) => {
    dot.style.left = `${e.clientX}px`;
    dot.style.top = `${e.clientY}px`;
  }, true);
  addEventListener('mousedown', (e) => {
    const r = document.createElement('div');
    r.className = 'pc-demo-ripple';
    r.style.left = `${e.clientX}px`;
    r.style.top = `${e.clientY}px`;
    document.documentElement.appendChild(r);
    setTimeout(() => r.remove(), 600);
  }, true);
  }
}

// ── driving helpers ───────────────────────────────────────────────────────────
let page;

/** A nav-rail item by its exact label. Scoped to <nav> because several labels
 *  (Chat, Knowledge, Tasks) also appear as dashboard quick-launch chips. */
const nav = (label) => page.locator('nav').getByRole('button', { name: label, exact: true }).first();

/** The pending permission card's Allow control. Named off the button's accessible
 *  name, which carries the tool AND the remember-scope ("Allow artifact_save — just
 *  this once: …"), so this cannot resolve to some other Allow in the shell. */
const allowBtn = (p) => (p || page).getByRole('button', { name: /^Allow / });

/** The first VISIBLE match, not the first match. Several of the shell's labels are
 *  duplicated across responsive variants where only one branch is mounted visible
 *  (the health footer has a wide and a narrow copy), so `.first()` alone can resolve
 *  to a hidden twin and hang. */
async function firstVisible(loc, what) {
  const deadline = Date.now() + 8000;
  do {
    for (const cand of await loc.all()) {
      if (await cand.isVisible()) return cand;
    }
    await page.waitForTimeout(200);
  } while (Date.now() < deadline);
  throw new Error(`click-path broken: no visible target for ${what}`);
}

/** Move the real mouse to an element's centre in visible steps. */
async function glide(loc, what, { xFrac = 0.5, yFrac = 0.5 } = {}) {
  // The scroll-into-view is load-bearing, not defensive: `page.mouse` fires at raw
  // viewport coordinates and does NOT auto-scroll the way `locator.click()` does, so
  // a target parked just past the fold (the nav rail's own Settings row sits at
  // y=814 in an 810px viewport) would silently receive nothing at all.
  const el = await firstVisible(loc, what);
  await el.scrollIntoViewIfNeeded();
  await page.waitForTimeout(140);
  const box = await el.boundingBox();
  if (!box) throw new Error(`click-path broken: ${what} has no box`);
  if (box.y < 0 || box.y + box.height > VIEWPORT.height) {
    throw new Error(`click-path broken: ${what} does not fit the viewport`);
  }
  await page.mouse.move(box.x + box.width * xFrac, box.y + box.height * yFrac, { steps: 26 });
  await page.waitForTimeout(180);
}

/** Glide, then a real press/release at that point. */
async function tap(loc, what, opts) {
  await glide(loc, what, opts);
  await page.mouse.down();
  await page.waitForTimeout(90);
  await page.mouse.up();
  await page.waitForTimeout(450);
}

/** Wait for something a REAL agent turn has to produce, and say what was being waited
 *  for when it never arrives. Separate from `firstVisible`'s 8s because these waits are
 *  on model and tool latency, not on a render. */
async function awaitTarget(loc, what, ms) {
  const deadline = Date.now() + ms;
  do {
    for (const cand of await loc.all()) {
      if (await cand.isVisible().catch(() => false)) return cand;
    }
    await page.waitForTimeout(400);
  } while (Date.now() < deadline);
  throw new Error(`click-path broken: ${what} never appeared within ${(ms / 1000) | 0}s`);
}

const dwell = (ms) => page.waitForTimeout(ms);

// ── phase 1: the pre-roll (NOT recorded) ──────────────────────────────────────
/** Send the prompt, and leave the turn parked on the tool-permission gate that the
 *  recorded take approves. Returns the chat session key.
 *
 *  This exists because of model latency, not because of anything the gate does. The
 *  gate is NOT weakened here: `--approval interactive` still asks about every call,
 *  this approves only the precursor calls the model chose to make BEFORE the write, it
 *  logs each one it approved, and it stops the moment the write parks — so the decision
 *  on camera is a real one, on a real pending request, made by a human click.
 */
async function preroll(browser) {
  const ctx = await browser.newContext({ viewport: VIEWPORT });
  await ctx.addInitScript(() => localStorage.setItem('mode', 'dark'));
  const p = await ctx.newPage();
  await p.goto(`${BASE}/#/chat`, { waitUntil: 'domcontentloaded', timeout: 40000 });
  await p.getByLabel('Message input').waitFor({ state: 'visible', timeout: 40000 });
  await p.getByLabel('Message input').click();
  await p.keyboard.type(PROMPT, { delay: 4 });
  await p.getByRole('button', { name: 'Send message' }).click();
  const sent = Date.now();
  console.log('· pre-roll: prompt sent, waiting for the turn to park on a gate');

  const allow = allowBtn(p);
  let session = '';
  for (let i = 0; i < 5; i++) {
    const budget = i === 0 ? 480000 : 300000;
    const deadline = Date.now() + budget;
    let name = null;
    while (Date.now() < deadline) {
      if (await allow.count() > 0) { name = await allow.first().getAttribute('aria-label'); break; }
      await p.waitForTimeout(500);
    }
    if (!name) {
      throw new Error(`pre-roll: no permission gate within ${(budget / 1000) | 0}s — the turn `
        + 'never reached a tool call. Is a chat model actually bound (--seed-local-model), and '
        + 'is the Ollama it names answering?');
    }
    session = p.url().split('#/chat/')[1] || session;
    if (name.startsWith(`Allow ${RECORDED_TOOL} `)) {
      console.log(`· pre-roll: parked on ${RECORDED_TOOL} after `
        + `${((Date.now() - sent) / 1000).toFixed(0)}s — left PENDING for the recorded take`
        + ` (session ${session})`);
      await ctx.close();
      return session;
    }
    // A precursor the model chose. Approve it off camera and NAME it, so the doc's claim
    // about what the recording does and does not show stays checkable against this log.
    console.log(`· pre-roll: approving a precursor call off camera — ${name.split(' — ')[0]}`);
    await p.waitForTimeout(900); // the card animates in; clicking mid-animation detaches it
    await allow.first().click({ timeout: 20000 });
  }
  throw new Error(`pre-roll: the model made 5 tool calls without reaching ${RECORDED_TOOL}`);
}

// ── the beats ─────────────────────────────────────────────────────────────────
// `seconds` is the wall-clock budget for the beat; the runner pads whatever the
// actions do not spend, and warns when a beat overruns (that is how the total
// stays inside the 60-90s window the launch asset is specified at).
let SESSION = '';

const BEATS = [
  {
    id: 'B1-home',
    seconds: 9,
    async act() {
      // `domcontentloaded`, not `networkidle`: a home with a model bound and a turn in
      // flight polls continuously, so the network never goes idle and a networkidle wait
      // would time out on a perfectly healthy dashboard. The beat waits for the thing it
      // is about instead, which is also what makes it fail loudly if that thing moves.
      await page.goto(`${BASE}/#/dashboard`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await awaitTarget(page.getByText('uptime', { exact: true }), 'the health footer', 30000);
      await dwell(1200);
      await glide(page.getByText('uptime', { exact: true }), 'the health footer');
      await dwell(1600);
    },
  },
  {
    id: 'B2-chat',
    seconds: 14,
    async act() {
      // Deep link, not a rail click: the parked session is a specific one, and a fresh
      // browser profile's Chat opens a NEW empty chat. Documented in CLICKPATH.md so the
      // beat is not mistaken for a click on visible chrome.
      await page.goto(`${BASE}/#/chat/${SESSION}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await awaitTarget(page.getByText('Permission needed'), 'the parked turn', 30000);
      await dwell(1200);
      await glide(page.getByText('three saved long-reads', { exact: false }), 'the prompt that was sent');
      await dwell(1800);
      // The collapsed tool row carries the model's OWN generated text as its argument
      // preview — that is real output on screen, before it has been allowed to land.
      // NOT the composer's Persistent/Temporary/Incognito pills, which the old path used
      // here: those are a choice made for a NEW chat and are not mounted on a session that
      // already has a turn in it, so this beat cannot honestly show them.
      await glide(page.getByText('Artifact save', { exact: false }), 'the model’s pending call');
      await dwell(2000);
    },
  },
  {
    id: 'B3-approval',
    seconds: 18,
    async act() {
      await glide(page.getByText('Caution', { exact: true }), 'the risk chip');
      await dwell(1400);
      await glide(page.getByText('Writes files', { exact: true }), 'the blast-radius chip');
      await dwell(1400);
      await glide(page.getByText('Just this once', { exact: true }), 'the remember-scope picker');
      await dwell(1600);
      await tap(allowBtn(), 'Allow');
      // The tool runs for real here. The row flips to the settled outcome, which is the
      // permanent record of the decision that was just made.
      await awaitTarget(page.getByText(`${RECORDED_TOOL} — approved`), 'the settled outcome line', 60000);
      await dwell(2600);
    },
  },
  {
    id: 'B4-loop',
    seconds: 14,
    async act() {
      // Deep link, not a rail click: v0.1.3's nav rail has no Loops item (a loop is
      // reached from the project that owns it, and the cross-project list lives at
      // this route). Documented in CLICKPATH.md so the beat is not mistaken for one.
      await page.goto(`${BASE}/#/loops/history`, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await awaitTarget(page.getByRole('button', { name: 'View all loops' }), 'View all loops', 25000);
      await dwell(800);
      await tap(page.getByRole('button', { name: 'View all loops' }), 'View all loops');
      await dwell(1200);
      await tap(page.getByText('Weekly reading digest quality pass'), 'the seeded loop');
      await dwell(1600);
      await glide(page.getByText('LATEST FINDING', { exact: false }), 'the loop’s recorded finding');
      await dwell(2400);
    },
  },
  {
    id: 'B5-knowledge',
    seconds: 12,
    async act() {
      await tap(nav('Knowledge'), 'nav → Knowledge');
      await dwell(1400);
      await tap(page.getByPlaceholder('Search knowledge'), 'the knowledge search box');
      await page.keyboard.type('digest', { delay: 110 });
      await dwell(2000);
      await glide(page.getByText('What makes a weekly digest actually get read', { exact: true }), 'the matching note');
      await dwell(1400);
    },
  },
  {
    id: 'B6-artifact',
    seconds: 12,
    async act() {
      await tap(nav('Artifacts'), 'nav → Artifacts');
      // This artifact exists only because the gate in B3 was approved. It is looked up by
      // the slug the prompt asked for, so a model that named it something else fails the
      // beat loudly instead of the capture quietly showing some other artifact.
      await awaitTarget(page.getByText(ARTIFACT_RX), `the ${ARTIFACT} artifact in the library`, 30000);
      await dwell(1000);
      await tap(page.getByText(ARTIFACT_RX), 'the new artifact');
      await dwell(2200);
      await glide(page.getByText('DETAILS', { exact: false }), 'the artifact’s version and event count');
      await dwell(2200);
    },
  },
  {
    id: 'B7-close',
    seconds: 4,
    async act() {
      await tap(nav('Home'), 'nav → Home');
      await dwell(2000);
    },
  },
];

// ── runner ────────────────────────────────────────────────────────────────────
const budget = BEATS.reduce((n, b) => n + b.seconds, 0);
if (budget < 60 || budget > 90) {
  throw new Error(`beat budget is ${budget}s — the launch asset is specified at 60-90s`);
}

await mkdir(OUT_DIR, { recursive: true });
const browser = await chromium.launch();

SESSION = process.env.DEMO_SESSION || (await preroll(browser));
if (!SESSION) throw new Error('no chat session key — cannot open the parked turn');

const ctx = await browser.newContext({
  viewport: VIEWPORT,
  deviceScaleFactor: 1, // 1:1 with the screencast; 2 would upscale then downsample
  reducedMotion: 'no-preference', // the motion design is part of what is being shown
  recordVideo: { dir: OUT_DIR, size: VIEWPORT },
});
// Dark is the design's home ground (web/DESIGN.md: Night Canvas #0f0f0f is the app
// background) and it is what the site hero is built on.
await ctx.addInitScript(() => {
  localStorage.setItem('mode', 'dark');
  document.documentElement.setAttribute('data-mode', 'dark');
});
await ctx.addInitScript(installCursor);
page = await ctx.newPage();

const started = Date.now();
for (const b of BEATS) {
  const t0 = Date.now();
  await b.act();
  const rest = b.seconds * 1000 - (Date.now() - t0);
  if (rest > 0) await dwell(rest);
  else console.warn(`! ${b.id} overran its ${b.seconds}s budget by ${-rest}ms`);
  console.log(`✓ ${b.id} (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
}
const elapsed = (Date.now() - started) / 1000;
console.log(`total ${elapsed.toFixed(1)}s`);

const video = page.video();
await ctx.close(); // flushes the screencast
await browser.close();

const webm = `${OUT_DIR}/${NAME}.webm`;
await rename(await video.path(), webm);
console.log(`✓ ${webm} (${((await stat(webm)).size / 1e6).toFixed(2)} MB)`);

// mp4 is what a site hero <video> wants; ffmpeg is optional so the capture still
// succeeds without it. `-an` is the silence guarantee, restated at the transcode.
if (existsSync('/opt/homebrew/bin/ffmpeg') || spawnSync('which', ['ffmpeg']).status === 0) {
  const mp4 = `${OUT_DIR}/${NAME}.mp4`;
  const r = spawnSync('ffmpeg', [
    '-y', '-i', webm, '-an',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '30',
    '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
    '-vf', 'scale=1280:-2', mp4,
  ], { stdio: ['ignore', 'ignore', 'inherit'] });
  if (r.status === 0) console.log(`✓ ${mp4} (${((await stat(mp4)).size / 1e6).toFixed(2)} MB)`);
  else console.warn('! ffmpeg transcode failed — the webm above is still usable');
} else {
  console.warn('! ffmpeg not found — skipping the mp4 transcode');
}

// The declared budget is checked before a browser is launched; this checks what was
// actually recorded. A beat that waits on a real agent turn can overrun, and a capture
// that quietly lands at 95s is out of spec whatever the table says — so the files are
// written first (they are still the evidence of what happened) and then this fails.
if (elapsed < 60 || elapsed > 90) {
  throw new Error(`the recorded take is ${elapsed.toFixed(1)}s — the launch asset is `
    + 'specified at 60-90s. The files above are the run that overran; re-time the beats '
    + '(the per-beat lines say which one) before shipping it.');
}

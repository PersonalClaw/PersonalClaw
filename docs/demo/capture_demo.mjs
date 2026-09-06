#!/usr/bin/env node
// PersonalClaw launch-demo capture — plays the scripted click-path in CLICKPATH.md
// against a gateway seeded with the `demo-home` fixture and records it silently.
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
// Prereqs:
//   1. `npm ci && npm run build --workspace web` (the gateway serves the built SPA;
//      see `make web-build`, which also links src/personalclaw/static/dist).
//   2. A gateway on an ISOLATED home seeded from the demo fixture — never your real
//      ~/.personalclaw, and never port 10000 if something else already owns it:
//
//        PERSONALCLAW_HOME=/tmp/personalclaw-demo \
//        PERSONALCLAW_WORKSPACE=/tmp/personalclaw-demo/workspace \
//        PERSONALCLAW_AUTH_MODE=none \
//        personalclaw gateway --seed demo-home --seed-replace --port 17410 --no-open
//
// Usage:
//   PCLAW_URL=http://127.0.0.1:17410 node docs/demo/capture_demo.mjs
//
// Output: <OUT_DIR>/personalclaw-tour.webm, plus personalclaw-tour.mp4 when ffmpeg
// is on PATH. Silent by construction — Playwright's screencast records no audio.
// OUT_DIR defaults to /tmp/personalclaw-demo-media because the capture is far too large to
// commit to this repo; see CLICKPATH.md § "Where the file lives".

import { chromium } from 'playwright';
import { mkdir, rename, stat } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';

const BASE = process.env.PCLAW_URL || 'http://127.0.0.1:17410';
const OUT_DIR = process.env.OUT_DIR || '/tmp/personalclaw-demo-media';
const VIEWPORT = { width: 1440, height: 810 }; // 16:9 — the aspect a site hero wants
const NAME = 'personalclaw-tour';

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

const dwell = (ms) => page.waitForTimeout(ms);

// ── the beats ─────────────────────────────────────────────────────────────────
// `seconds` is the wall-clock budget for the beat; the runner pads whatever the
// actions do not spend, and warns when a beat overruns (that is how the total
// stays inside the 60-90s window the launch asset is specified at).
const BEATS = [
  {
    id: 'B1-home',
    seconds: 14,
    async act() {
      await page.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle', timeout: 30000 });
      await dwell(1200);
      await glide(page.getByText('Needs you', { exact: true }), 'the Needs-you tile');
      await dwell(900);
      await glide(page.getByText('uptime', { exact: true }), 'the health footer');
      await dwell(1400);
    },
  },
  {
    id: 'B2-chat',
    seconds: 11,
    async act() {
      await tap(nav('Chat'), 'nav → Chat');
      await dwell(1200);
      await tap(page.getByLabel('Message input'), 'the composer');
      await page.keyboard.type('Summarise the three saved long-reads into this week\u2019s digest.', { delay: 44 });
      await dwell(700);
      await glide(page.getByText('Incognito', { exact: true }), 'the persistence pills');
      await dwell(1400);
    },
  },
  {
    id: 'B3-receipts',
    seconds: 9,
    async act() {
      await tap(nav('Settings'), 'nav → Settings');
      await dwell(1400);
      await glide(page.getByText('denied-command rules'), 'the Security card');
      await dwell(1500);
      await glide(page.getByText('Chain intact', { exact: true }), 'the audit-log chain');
      await dwell(1600);
    },
  },
  {
    id: 'B4-guardrails',
    seconds: 10,
    async act() {
      await tap(page.getByPlaceholder('Search settings'), 'the settings search');
      await page.keyboard.type('guardrail', { delay: 95 });
      await dwell(1100);
      await tap(page.getByText('Guardrails', { exact: true }), 'the Guardrails card');
      await dwell(1300);
      await glide(page.getByText('Incident mode', { exact: true }), 'the incident kill switch');
      await dwell(1000);
      await glide(page.getByText('Scan mode', { exact: true }), 'the outbound-scan mode');
      await dwell(1400);
    },
  },
  {
    id: 'B5-loop',
    seconds: 14,
    async act() {
      // Deep link, not a rail click: v0.1.3's nav rail has no Loops item (a loop is
      // reached from the project that owns it, and the cross-project list lives at
      // this route). Documented in CLICKPATH.md so the beat is not mistaken for one.
      await page.goto(`${BASE}/#/loops/history`, { waitUntil: 'networkidle', timeout: 20000 });
      await dwell(1200);
      await tap(page.getByRole('button', { name: 'View all loops' }), 'View all loops');
      await dwell(1600);
      await tap(page.getByText('Weekly reading digest quality pass'), 'the seeded loop');
      await dwell(2200);
      await glide(page.getByText('LATEST FINDING', { exact: false }), 'the loop\u2019s recorded finding');
      await dwell(3000);
    },
  },
  {
    id: 'B6-knowledge',
    seconds: 10,
    async act() {
      await tap(nav('Knowledge'), 'nav → Knowledge');
      await dwell(1500);
      await tap(page.getByPlaceholder('Search knowledge'), 'the knowledge search box');
      await page.keyboard.type('digest', { delay: 120 });
      await dwell(2200);
      await glide(page.getByText('What makes a weekly digest actually get read', { exact: true }), 'the matching note');
      await dwell(1300);
    },
  },
  {
    id: 'B7-tasks',
    seconds: 10,
    async act() {
      await tap(nav('Tasks'), 'nav → Tasks');
      await dwell(1400);
      await tap(page.getByRole('tab', { name: 'Kanban board' }), 'the Kanban view');
      await dwell(1600);
      await glide(page.getByText('Blocked', { exact: true }), 'the Blocked column');
      await dwell(1500);
    },
  },
  {
    id: 'B8-close',
    seconds: 4,
    async act() {
      await tap(nav('Home'), 'nav → Home');
      await dwell(2000);
    },
  },
];

// ── runner ────────────────────────────────────────────────────────────────────
const total = BEATS.reduce((n, b) => n + b.seconds, 0);
if (total < 60 || total > 90) {
  throw new Error(`beat budget is ${total}s — the launch asset is specified at 60-90s`);
}

await mkdir(OUT_DIR, { recursive: true });
const browser = await chromium.launch();
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
console.log(`total ${((Date.now() - started) / 1000).toFixed(1)}s`);

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

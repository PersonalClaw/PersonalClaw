#!/usr/bin/env node
// PersonalClaw screenshot capture — light + dark, every route, reproducibly.
//
// Prereqs:
//   1. A gateway on a SCRATCH home with a configured model provider and (ideally) seeded
//      scenario data:
//        PERSONALCLAW_HOME=/tmp/pc-showcase personalclaw gateway --seed demo-home \
//          --seed-replace --seed-local-model --port auto --no-open
//   2. Playwright:  npm i -D playwright && npx playwright install chromium
//
// Usage:
//   node docs/screenshots/capture.mjs --home /tmp/pc-showcase
//   (or PERSONALCLAW_HOME=/tmp/pc-showcase node docs/screenshots/capture.mjs)
//
// There is no URL to give it: the gateway is the one that home's runtime record names, and the
// browser is signed in through that home's local secret (scripts/lib/named_home.mjs). No
// home, or the default one (the install's own data), is refused before a browser starts.
//
// Output: docs/screenshots/{light,dark}/NN-<route>.png
//
// Theme is toggled via localStorage `mode` (light|dark) + the data-mode attribute,
// matching how the SPA persists it. Extend ROUTES as new pages land.

import { mkdir } from 'node:fs/promises';
import { homeArg, scratchGatewayOrExit, signIn } from '../../scripts/lib/named_home.mjs';

const gateway = scratchGatewayOrExit(homeArg());
const { chromium } = await import('playwright');
const VIEWPORT = { width: 1440, height: 900 };

// NN-name → hash route. Keep numbering stable so the showcase references don't drift.
// `01-onboarding` is captured separately, BEFORE a model is configured (the first-run
// wizard only shows pre-setup); every route below assumes a configured, seeded instance.
const ROUTES = [
  ['01-dashboard',       '#/dashboard'],
  ['02-chat',            '#/chat'],
  ['03-knowledge',       '#/knowledge'],
  ['04-tasks',           '#/tasks?view=board'],
  ['05-loops',           '#/loop'],
  ['06-triggers',        '#/triggers'],
  ['07-workflows',       '#/workflows'],
  ['08-memory',          '#/settings/memory'],
  ['09-apps',            '#/apps?view=native'],
  ['10-skills',          '#/skills'],
  ['11-settings',        '#/settings'],
  ['12-settings-models', '#/settings/models'],
  ['13-agents',          '#/agents'],
];

for (const mode of ['light', 'dark']) await mkdir(new URL(`./${mode}/`, import.meta.url), { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 2 });
await signIn(ctx, gateway);
const page = await ctx.newPage();
console.log(`capturing ${gateway.home} at ${gateway.url}`);

for (const mode of ['light', 'dark']) {
  await page.addInitScript(m => {
    localStorage.setItem('mode', m);
    document.documentElement.setAttribute('data-mode', m);
  }, mode);
  for (const [name, route] of ROUTES) {
    try {
      await page.goto(`${gateway.url}/${route}`, { waitUntil: 'networkidle', timeout: 20000 });
      await page.waitForTimeout(700); // let motion settle
      await page.screenshot({ path: `docs/screenshots/${mode}/${name}.png` });
      console.log(`✓ ${mode}/${name}.png`);
    } catch (e) {
      console.warn(`✗ ${mode}/${name}: ${e.message}`);
    }
  }
}

await browser.close();

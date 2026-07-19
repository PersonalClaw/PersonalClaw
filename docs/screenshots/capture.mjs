#!/usr/bin/env node
// PersonalClaw screenshot capture — light + dark, every route, reproducibly.
//
// Prereqs:
//   1. A gateway running with a configured model provider and (ideally) seeded
//      scenario data:  personalclaw gateway --seed demo --json-ready
//      (copy the printed port + token, or run with PERSONALCLAW_AUTH_MODE=none
//      on loopback for a token-free local capture).
//   2. Playwright:  npm i -D playwright && npx playwright install chromium
//
// Usage:
//   PCLAW_URL=http://localhost:10000 PCLAW_TOKEN=... node docs/screenshots/capture.mjs
//
// Output: docs/screenshots/{light,dark}/NN-<route>.png
//
// Theme is toggled via localStorage `mode` (light|dark) + the data-mode attribute,
// matching how the SPA persists it. Extend ROUTES as new pages land.

import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';

const BASE = process.env.PCLAW_URL || 'http://localhost:10000';
const TOKEN = process.env.PCLAW_TOKEN || '';
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
if (TOKEN) await ctx.addInitScript(t => localStorage.setItem('token', t), TOKEN);
const page = await ctx.newPage();

for (const mode of ['light', 'dark']) {
  await page.addInitScript(m => {
    localStorage.setItem('mode', m);
    document.documentElement.setAttribute('data-mode', m);
  }, mode);
  for (const [name, route] of ROUTES) {
    try {
      await page.goto(`${BASE}/${route}`, { waitUntil: 'networkidle', timeout: 20000 });
      await page.waitForTimeout(700); // let motion settle
      await page.screenshot({ path: `docs/screenshots/${mode}/${name}.png` });
      console.log(`✓ ${mode}/${name}.png`);
    } catch (e) {
      console.warn(`✗ ${mode}/${name}: ${e.message}`);
    }
  }
}

await browser.close();

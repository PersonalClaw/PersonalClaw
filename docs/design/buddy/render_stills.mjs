#!/usr/bin/env node
// The buddy's still orbs as the desktop app loads them (#3896): every PNG the tray and the dock
// show, rendered from the drawn stills in docs/design/buddy/poses/ (character.md, "The drawn
// poses"), so no raster is painted by hand and none can drift from the drawing.
//
//   desktop/buddy/tray-<still>-<mode>.png, @2x      18/36  mode ink   the Linux tray
//   desktop/buddy/tray-<still>Template.png, @2x     18/36  template   the macOS menu bar, which
//                                                                     recolours a template image
//   desktop/buddy/dock-needs-you-<mode>.png         512    colour     the macOS dock while
//                                                                     something needs the owner
//
// Each <still> is idle, working, needs-you, error, listening or offline, and each <mode> light or
// dark: the shell takes the one its system appearance asks for. The tray's come from the 18 px
// drawings, the dock's from the 22 px colour one, the largest the set draws. The Linux tray's are
// the template drawing (tray-<still>-18-template.svg) painted in the mode's own ink (the sheet's
// `--sheet-template`, the scheme's on-surface): at 18 and 36 px the colour drawing's dots, shaded by
// their opacity, hold under 3:1 against the bar they sit on, and the one ink holds it for every
// still (#3896, desktop/test/buddyStillsContrast.test.js). Each SVG is drawn full-frame on a transparent ground in Playwright's Chromium, as
// docs/brand/render_marks.mjs draws the claw mark.
//
// A colour drawing paints with design tokens (`var(--color-orb-alive)`, `var(--color-warn)`, …),
// which the contact sheet poses/sheets/tray.svg resolves for each mode in its own style (written by
// web/src/buddyRig/poseSources.ts from the token sheets). The render reads them there, so a still
// takes the colours the sheet shows.
//
// It also writes docs/design/buddy/stills.json: the sha256 of each source and of this script, and
// each output's path, size, source and sha256. desktop/test/buddyStills.test.js holds the tree to
// that record, so a drawing changed without a re-render, or a raster edited by hand, fails.
//
// Usage:  node docs/design/buddy/render_stills.mjs           render and write every file
//         node docs/design/buddy/render_stills.mjs --check   render in memory and exit 1 if any
//                                                            committed raster differs from it

import { chromium } from 'playwright';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..', '..');
const POSES = 'docs/design/buddy/poses';
const MANIFEST = path.join(HERE, 'stills.json');
const SHEET = `${POSES}/sheets/tray.svg`;
const MODES = ['light', 'dark'];

const STILLS = ['idle', 'working', 'needs-you', 'error', 'listening', 'offline'];

const OUTPUTS = [
  ...STILLS.flatMap((still) => [
    ...MODES.flatMap((mode) => [
      { path: `desktop/buddy/tray-${still}-${mode}.png`, size: 18, source: `${POSES}/tray-${still}-18-template.svg`, mode, ink: true },
      { path: `desktop/buddy/tray-${still}-${mode}@2x.png`, size: 36, source: `${POSES}/tray-${still}-18-template.svg`, mode, ink: true },
    ]),
    { path: `desktop/buddy/tray-${still}Template.png`, size: 18, source: `${POSES}/tray-${still}-18-template.svg`, mode: null },
    { path: `desktop/buddy/tray-${still}Template@2x.png`, size: 36, source: `${POSES}/tray-${still}-18-template.svg`, mode: null },
  ]),
  ...MODES.map((mode) => ({ path: `desktop/buddy/dock-needs-you-${mode}.png`, size: 512, source: `${POSES}/tray-needs-you-22.svg`, mode })),
];

const sha256 = (buf) => createHash('sha256').update(buf).digest('hex');

const sources = new Map();
for (const out of OUTPUTS) {
  if (!sources.has(out.source)) sources.set(out.source, await readFile(path.join(REPO, out.source)));
}
const scriptBytes = await readFile(fileURLToPath(import.meta.url));
const sheetBytes = await readFile(path.join(REPO, SHEET));

/** Each mode's token values, as the sheet's style declares them under `.light` and `.dark`. */
const TOKENS = Object.fromEntries(MODES.map((mode) => {
  const block = new RegExp(`\\.${mode} \\{([^}]*)\\}`).exec(sheetBytes.toString('utf8'));
  if (!block || !/--color-orb-alive:/.test(block[1])) throw new Error(`${SHEET} declares no ${mode} tokens; update this script with it`);
  return [mode, block[1].trim()];
}));

/** A template drawing painted in the mode's own ink, in place of the black the system recolours. */
const inked = (markup) => markup.replaceAll('fill: black', 'fill: var(--sheet-template)');

function html(markup, size, mode) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
  :root{${mode ? TOKENS[mode] : ''}}
  *{margin:0;padding:0}
  html,body{width:${size}px;height:${size}px;background:transparent;overflow:hidden}
  svg{display:block;width:${size}px;height:${size}px}
  </style></head><body>${markup}</body></html>`;
}

const check = process.argv.includes('--check');
const browser = await chromium.launch();
const rendered = [];
try {
  for (const out of OUTPUTS) {
    const ctx = await browser.newContext({ viewport: { width: out.size, height: out.size }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    const markup = sources.get(out.source).toString('utf8');
    if (out.ink && !markup.includes('fill: black')) throw new Error(`${out.source} has no black to paint in the mode's ink`);
    await page.setContent(html(out.ink ? inked(markup) : markup, out.size, out.mode), { waitUntil: 'load' });
    const png = await page.screenshot({ omitBackground: true });
    await ctx.close();
    rendered.push({ ...out, png });
  }
} finally {
  await browser.close();
}

const manifest = {
  about: 'Written by docs/design/buddy/render_stills.mjs. Re-run it after a change to a drawing; do not edit by hand.',
  script_sha256: sha256(scriptBytes),
  sources: [...sources, [SHEET, sheetBytes]].map(([source, bytes]) => ({ path: source, sha256: sha256(bytes) })),
  outputs: rendered.map((r) => ({ path: r.path, size: r.size, source: r.source, ...(r.ink ? { ink: `--sheet-template (${r.mode})` } : {}), sha256: sha256(r.png) })),
};
const manifestText = `${JSON.stringify(manifest, null, 2)}\n`;

if (check) {
  const stale = [];
  for (const r of rendered) {
    let committed = null;
    try {
      committed = await readFile(path.join(REPO, r.path));
    } catch {
      // A missing file is stale, reported below.
    }
    if (!committed || !committed.equals(r.png)) stale.push(r.path);
  }
  let committedManifest = null;
  try {
    committedManifest = await readFile(MANIFEST, 'utf8');
  } catch {
    // Reported below.
  }
  if (committedManifest !== manifestText) stale.push('docs/design/buddy/stills.json');
  if (stale.length) {
    console.error(`✗ differs from what render_stills.mjs renders: ${stale.join(', ')}`);
    console.error('  run: node docs/design/buddy/render_stills.mjs');
    process.exit(1);
  }
  console.log(`✓ ${rendered.length} rasters and docs/design/buddy/stills.json match the render`);
} else {
  await mkdir(path.join(REPO, 'desktop', 'buddy'), { recursive: true });
  for (const r of rendered) {
    await writeFile(path.join(REPO, r.path), r.png);
    console.log(`✓ ${r.path}  ${r.size}x${r.size}`);
  }
  await writeFile(MANIFEST, manifestText);
  console.log('✓ docs/design/buddy/stills.json');
}

#!/usr/bin/env node
// PersonalClaw mark rasters — every PNG of the claw mark, rendered from one source.
//
// The source is the web app's own mark, web/public/claw.svg (the same file as
// docs/brand/personalclaw-mark.svg). Each raster is that SVG drawn full-frame on a
// transparent ground in Playwright's Chromium, the browser social_preview.mjs uses, so
// no raster is painted by hand and none can drift from the mark the web app draws.
//
//   desktop/icon.png                1024  gradient   the app icon (electron-builder derives
//                                                    the macOS .icns and Linux sizes from it)
//   desktop/tray.png, tray@2x.png    18/36 gradient   the Linux menu-bar image
//   desktop/trayTemplate.png, @2x    18/36 black      the macOS menu-bar template image: a
//                                                    black silhouette with alpha, which macOS
//                                                    recolours for a light, dark or tinted bar
//   docs/brand/personalclaw-mark.png 512  gradient   the README header logo
//
// It also writes docs/brand/marks.json: the sha256 of the claw.svg and of this script it
// rendered with, and each output's path, size and sha256. desktop/test/brandMarks.test.js
// holds the tree to that record, so a mark changed without a re-render, or a raster edited
// by hand, fails the desktop tests.
//
// Usage:  node docs/brand/render_marks.mjs           render and write every file
//         node docs/brand/render_marks.mjs --check   render in memory and exit 1 if any
//                                                    committed raster differs from it

import { chromium } from 'playwright';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const SOURCE = 'web/public/claw.svg';
const MANIFEST = path.join(HERE, 'marks.json');

/** `fill` is the gradient the SVG defines, or a flat colour for the template images. */
const OUTPUTS = [
  { path: 'desktop/icon.png', size: 1024, fill: 'gradient' },
  { path: 'desktop/tray.png', size: 18, fill: 'gradient' },
  { path: 'desktop/tray@2x.png', size: 36, fill: 'gradient' },
  { path: 'desktop/trayTemplate.png', size: 18, fill: '#000000' },
  { path: 'desktop/trayTemplate@2x.png', size: 36, fill: '#000000' },
  { path: 'docs/brand/personalclaw-mark.png', size: 512, fill: 'gradient' },
];

const sha256 = (buf) => createHash('sha256').update(buf).digest('hex');

const svgBytes = await readFile(path.join(REPO, SOURCE));
const scriptBytes = await readFile(fileURLToPath(import.meta.url));
const svg = svgBytes.toString('utf8');
if ((svg.match(/<path\b/g) || []).length !== 1 || !/fill="url\(#g\)"/.test(svg)) {
  throw new Error(`${SOURCE} is no longer one path filled by url(#g); update this script with it`);
}

/** The mark with its path filled flat, for the template images. */
const flat = (color) => svg.replace('fill="url(#g)"', `fill="${color}"`);

function html(markup, size) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
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
    const ctx = await browser.newContext({
      viewport: { width: out.size, height: out.size },
      deviceScaleFactor: 1,
    });
    const page = await ctx.newPage();
    const markup = out.fill === 'gradient' ? svg : flat(out.fill);
    await page.setContent(html(markup, out.size), { waitUntil: 'load' });
    const png = await page.screenshot({ omitBackground: true });
    await ctx.close();
    rendered.push({ ...out, png });
  }
} finally {
  await browser.close();
}

const manifest = {
  about: 'Written by docs/brand/render_marks.mjs. Re-run it after a change to the source; do not edit by hand.',
  source: SOURCE,
  source_sha256: sha256(svgBytes),
  script_sha256: sha256(scriptBytes),
  outputs: rendered.map((r) => ({ path: r.path, size: r.size, sha256: sha256(r.png) })),
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
  if (committedManifest !== manifestText) stale.push('docs/brand/marks.json');
  if (stale.length) {
    console.error(`✗ differs from what render_marks.mjs renders: ${stale.join(', ')}`);
    console.error('  run: node docs/brand/render_marks.mjs');
    process.exit(1);
  }
  console.log(`✓ ${rendered.length} rasters and docs/brand/marks.json match the render`);
} else {
  for (const r of rendered) {
    await writeFile(path.join(REPO, r.path), r.png);
    console.log(`✓ ${r.path}  ${r.size}x${r.size}`);
  }
  await writeFile(MANIFEST, manifestText);
  console.log('✓ docs/brand/marks.json');
}

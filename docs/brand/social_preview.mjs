#!/usr/bin/env node
// PersonalClaw social-preview generator — 1280x640 PNGs for the GitHub repo
// "Social preview" slot (Settings → General → Social preview) on both product repos.
//
// 1280x640 is GitHub's own recommended size for that slot; it is deliberately NOT the
// 1200x600 the website already ships at /brand/social-preview.png, which is the og:image
// declared in the site's BaseLayout. These are the repo cards, not the site card.
//
// The palette, the type scale and the weights are read out of web/DESIGN.md rather than
// eyeballed — see TOKENS below, where every value carries the DESIGN.md name it comes
// from. The typefaces are the repo's own vendored variable fonts (web/public/fonts/),
// and the mark is the shipped web/public/claw.svg, so the cards are the design system
// rather than a lookalike.
//
// Usage:  node docs/brand/social_preview.mjs
// Output: docs/brand/social-preview-{core,apps}.png (committed — see README.md)

import { chromium } from 'playwright';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const FONTS = path.join(REPO, 'web', 'public', 'fonts');
const SIZE = { width: 1280, height: 640 };

// web/DESIGN.md § 2 (Color) and § 3 (Typography). Names are DESIGN.md's names.
const TOKENS = {
  canvasNight: '#0f0f0f',
  surface: '#131314',
  surfaceContainer: '#1e1f20',
  surfaceHigh: '#282a2c',
  coralPrimary: '#ff6b5b',
  // DESIGN.md's hover tier. It is also what the site hero's tagline already uses
  // (personalclaw.dev tokens.css --coral-bright: #ff937d), so the repo card and the
  // site card speak in the same voice at the same brightness.
  coralEmphasis: '#ff9a86',
  amberSecondary: '#ffb454',
  ink: '#e3e3e3',
  inkVar: '#c4c7c5',
  inkLow: '#9a9b9c',
  outlineVariant: '#444746',
  // "the tail of the brand spectrum" — the gradient the mark and the accents ride.
  spectrum: '#c85a48, #ff6b5b, #ff9a7a, #ffb454',
};

const CARDS = [
  {
    file: 'social-preview-core.png',
    repo: 'PersonalClaw/PersonalClaw',
    // The site hero's own line (personalclaw.dev src/pages/index.astro) — one voice
    // across the site card and the repo card.
    line: ['One agentic OS.', 'Your machine. Your rules.'],
    support:
      'Chat, autonomous goal loops, memory, knowledge, and automation behind one gateway you own.',
    chips: ['MIT licensed', 'Zero telemetry', 'Self-hosted'],
  },
  {
    file: 'social-preview-apps.png',
    repo: 'PersonalClaw/PersonalClawApps',
    line: ['First-party apps.', 'Every one replaceable.'],
    support:
      'Model providers, search, agents, channels and tools — each installed through the scanner-gated Store.',
    chips: ['Scanner-gated', 'Permission-scoped', 'Swap any part'],
  },
];

async function fontFace(family, file, weightRange) {
  const b64 = (await readFile(path.join(FONTS, file))).toString('base64');
  return `@font-face{font-family:'${family}';font-weight:${weightRange};font-display:block;
    src:url(data:font/woff2;base64,${b64}) format('woff2')}`;
}

const mark = await readFile(path.join(REPO, 'web', 'public', 'claw.svg'), 'utf8');
const faces = [
  await fontFace('PC Sans', 'dm-sans.woff2', '100 1000'),
  await fontFace('PC Mono', 'jetbrains-mono.woff2', '100 800'),
].join('\n');

/** One card as a standalone document. Sizes are the DESIGN.md rungs in px at the
 *  card's own scale: a 1280x640 card is ~2.7x the 16px web root, so the display rung
 *  (2.625rem) lands near 112px and the caption rung (0.75rem) near 26px. */
function html(card) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
  ${faces}
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{width:${SIZE.width}px;height:${SIZE.height}px}
  body{background:${TOKENS.canvasNight};color:${TOKENS.ink};
    font-family:'PC Sans',system-ui,sans-serif;-webkit-font-smoothing:antialiased;
    display:flex;overflow:hidden;position:relative}
  /* The app's own "agent is alive" bloom: one warm coral glow, no second accent.
     DESIGN.md's One Voice Rule — coral is the only accent on this card. */
  .bloom{position:absolute;inset:0;
    background:
      radial-gradient(920px 620px at 8% -18%, rgba(255,107,91,.20), rgba(255,107,91,0) 62%),
      radial-gradient(820px 760px at 94% 34%, rgba(255,107,91,.085), rgba(255,107,91,0) 64%),
      radial-gradient(680px 520px at 108% 118%, rgba(255,180,84,.10), rgba(255,180,84,0) 64%)}
  /* Tone, not line (DESIGN.md): the panel is a surface step, edged by a hairline. */
  .panel{position:absolute;inset:0;
    background:linear-gradient(160deg, ${TOKENS.surface} 0%, ${TOKENS.canvasNight} 68%);
    opacity:.55}
  .frame{position:relative;display:flex;flex-direction:column;justify-content:center;
    gap:26px;padding:70px 88px;width:100%}
  .rule{position:absolute;left:0;top:0;height:5px;width:100%;
    background:linear-gradient(90deg, ${TOKENS.spectrum})}
  .mark{width:64px;height:64px;filter:drop-shadow(0 0 24px rgba(255,107,91,.30))}
  .mark svg{width:100%;height:100%;display:block}
  .wordmark{display:flex;align-items:center;gap:24px}
  /* Display rung: big type gets LIGHTER (wght 280-360) — the neural-expressive
     signature, and the weight the site's own hero <h1> uses (320). */
  .wordmark h1{font-size:96px;line-height:.94;font-weight:320;letter-spacing:-.028em;
    color:${TOKENS.ink}}
  /* The tagline is solid coral at a FIRMER weight and a SMALLER size than the
     wordmark — the site hero's .hero-line treatment (wght 520), not a gradient
     across text, which is not in the type vocabulary. */
  .tagline{font-size:44px;line-height:1.1;font-weight:520;letter-spacing:-.012em;
    color:${TOKENS.coralEmphasis}}
  /* Body rung at ink-var: secondary text, held under the 75ch prose ceiling. */
  p{font-size:27px;line-height:1.48;font-weight:400;color:${TOKENS.inkVar};max-width:47ch}
  .chips{display:flex;gap:14px}
  /* Tonal containers, pill-shaped, hairline-edged — no colored side-stripes. */
  .chips span{font-size:22px;font-weight:470;color:${TOKENS.inkVar};
    background:${TOKENS.surfaceContainer};border:1px solid ${TOKENS.outlineVariant};
    border-radius:999px;padding:11px 22px}
  /* Ink Low is metadata only, never body copy (DESIGN.md). */
  .slug{position:absolute;right:88px;bottom:56px;font-family:'PC Mono',ui-monospace,monospace;
    font-size:22px;font-weight:450;color:${TOKENS.inkLow};letter-spacing:.01em}
  </style></head><body>
  <div class="bloom"></div><div class="panel"></div>
  <div class="rule"></div>
  <div class="frame">
    <div class="wordmark"><div class="mark">${mark}</div><h1>PersonalClaw</h1></div>
    <p class="tagline">${card.line[0]}<br>${card.line[1]}</p>
    <p>${card.support}</p>
    <div class="chips">${card.chips.map((c) => `<span>${c}</span>`).join('')}</div>
  </div>
  <div class="slug">${card.repo}</div>
  </body></html>`;
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: SIZE, deviceScaleFactor: 1 });
const page = await ctx.newPage();
for (const card of CARDS) {
  await page.setContent(html(card), { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(HERE, card.file) });
  console.log(`✓ docs/brand/${card.file}  ${SIZE.width}x${SIZE.height}`);
}
await browser.close();

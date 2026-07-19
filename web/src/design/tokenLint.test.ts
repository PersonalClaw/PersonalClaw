import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// ── Token-lint (component-redesign Slice 0) ────────────────────────────────
// Design-system adherence guard: no raw color hex or raw px literals in app
// source. Everything must go through design tokens (--color-*, --radius-*,
// --spacing-*, tailwind scale). The design/ dir is exempt — it DEFINES the
// tokens. This test is the ratchet that keeps adherence at 0 after the sweep;
// as files are cleaned they leave the allowlist, and the allowlist may only
// shrink. A NEW violation in an already-clean file fails the build.

// vitest runs from the web/ package dir; source lives in web/src.
const SRC = join(process.cwd(), 'src')

// Directories/files that DEFINE tokens or legitimately carry raw values
// (canvas/SVG math, syntax-highlight palettes). Never app-chrome styling.
const EXEMPT_DIRS = ['design/']
const EXEMPT_FILES = [
  'ui/DotGlow.tsx',      // canvas particle field — rgb() math, not chrome
  'ui/ClawMark.tsx',     // brand glyph SVG — gradient stop coords
  'ui/Spark.tsx',        // canvas spark — numeric physics
  'ui/WavyProgress.tsx', // SVG path math
  // Content-TYPE brand colors: per-format identity (React cyan #61dafb, HTML
  // orange, JSON gold …) — deliberate, NOT app-chrome theming; a format's brand
  // color isn't a PClaw scheme token. Documented Tier-D non-compliance.
  'pages/files/fileMeta.ts',
  'ui/content/registerBuiltins.ts',
  'ui/content/exporters.ts',
  // Terminal emulator (xterm.js) theme needs literal hex for its own renderer;
  // it can't consume CSS vars. Its bg/fg track the light/dark mode explicitly.
  'pages/terminal/TerminalView.tsx',
  // Code-reveal views mimic a VS Code editor surface (bg/fg/gutter) — an
  // editor-chrome palette, not app theming; parallels the terminal/Monaco.
  'pages/code/DiffReveal.tsx',
  'pages/code/TypingReveal.tsx',
  // Scheme-definition layer: these carry the DEFAULT coral hex as the fallback
  // when a scheme hasn't loaded yet — they DEFINE the color identity (like
  // design/), so a literal is correct here, not a token reference.
  'app/appearance.tsx',
  'pages/settings/settingsWidgets.tsx',
]

// Files still carrying raw values as the sweep proceeds. This allowlist may
// only SHRINK — a file removed from here that regresses fails the test. Tier
// work deletes entries as each component is cleaned.
const ALLOWLIST = new Set<string>(loadAllowlist())

function loadAllowlist(): string[] {
  try {
    const raw = readFileSync(join(SRC, 'design/tokenLint.allowlist.json'), 'utf8')
    return JSON.parse(raw) as string[]
  } catch { return [] }
}

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    const rel = relative(SRC, p).replace(/\\/g, '/')
    if (EXEMPT_DIRS.some((d) => rel.startsWith(d))) continue
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

// A raw color hex in a style/className context. We look for #rgb/#rrggbb that
// aren't part of a URL fragment or a comment marker. This is the HARD rule —
// hardcoded colors bypass the theme/scheme system and must reach 0.
const HEX = /#[0-9a-fA-F]{3,8}\b/
// A raw px literal INSIDE an inline style object, where a design token genuinely
// applies (font-size / spacing / radius). Arbitrary Tailwind values (min-w-[200px],
// border-l-[3px]) are pragmatic one-off layout dims — not flagged. And these
// inline-px CONTEXTS are legitimately px and have NO meaningful token, so they're
// excluded (checked per-line below): CSS grid track sizing (minmax/repeat), border/
// outline hairline WIDTHS (the color there is already a token), and computed pixel
// heights/widths (Math.min(...), calc()). What remains flagged: bare fontSize/
// padding/margin/gap/width/height px literals that SHOULD use the scale.
const RAW_PX = /style=\{\{[^}]*?\b\d+px\b/
// Legitimate inline-px contexts a design token doesn't cover — not violations.
const PX_OK_CONTEXT = /minmax\(|repeat\(|\bmin\(|\bmax\(|\bclamp\(|\b(border|outline)(-[a-z]+)?:\s*[^;}]*\d+px|border[A-Z][a-zA-Z]*:\s*[`'"]?\s*\$?\{?[^}]*\d+px|Math\.(min|max)\(/

function violations(file: string): string[] {
  const text = readFileSync(file, 'utf8')
  const hits: string[] = []
  text.split('\n').forEach((line, i) => {
    // Skip comment-only lines (design rationale often cites hex/px in prose).
    const trimmed = line.trim()
    if (trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')) return
    if (HEX.test(line)) hits.push(`${i + 1}: hex — ${trimmed.slice(0, 80)}`)
    // A px inside a calc() that already references a token (e.g.
    // calc(var(--content-width) + 160px)) is a legitimate token+offset. Grid
    // track sizing, hairline border/outline widths, and computed Math.min/max px
    // are also legitimate (PX_OK_CONTEXT). Flag only inline-style px that a real
    // spacing/font/radius token should cover.
    if (RAW_PX.test(line) && !/calc\([^)]*var\(/.test(line) && !PX_OK_CONTEXT.test(line)) hits.push(`${i + 1}: px — ${trimmed.slice(0, 80)}`)
  })
  return hits
}

describe('token-lint: design-system adherence', () => {
  const files = walk(SRC)

  it('finds source files to lint', () => {
    expect(files.length).toBeGreaterThan(100)
  })

  it('no raw hex/px outside design/ (except the shrinking allowlist)', () => {
    const offenders: Record<string, string[]> = {}
    for (const f of files) {
      const rel = relative(SRC, f).replace(/\\/g, '/')
      if (EXEMPT_FILES.includes(rel) || ALLOWLIST.has(rel)) continue
      const v = violations(f)
      if (v.length) offenders[rel] = v
    }
    expect(offenders, `Raw hex/px found (route through tokens):\n${JSON.stringify(offenders, null, 2)}`).toEqual({})
  })

  it('allowlist only contains files that still have violations (no stale entries)', () => {
    const stale: string[] = []
    for (const rel of ALLOWLIST) {
      const full = join(SRC, rel)
      try {
        if (EXEMPT_FILES.includes(rel)) { stale.push(rel); continue }
        if (violations(full).length === 0) stale.push(rel)
      } catch { stale.push(rel) }  // file gone → remove from allowlist
    }
    expect(stale, `These files are clean/gone — remove from the allowlist:\n${stale.join('\n')}`).toEqual([])
  })
})

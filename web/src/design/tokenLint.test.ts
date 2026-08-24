import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { lineViolations } from './tokenLintRule'

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

// The rule itself now lives in ./tokenLintRule (APE-4) so an APP BUNDLE can be
// linted by the same patterns — see that file and token_lint_rules.json. The rule
// is unchanged: hex is the HARD rule (hardcoded colors bypass the theme/scheme
// system and must reach 0); inline-style px is flagged only where a real
// spacing/font/radius token should cover it, so grid track sizing, hairline
// border/outline widths, computed Math.min/max px and calc(var(…) + Npx) are not
// violations. What remains flagged: bare fontSize/padding/margin/gap/width/height
// px literals that SHOULD use the scale.
function violations(file: string): string[] {
  const text = readFileSync(file, 'utf8')
  const hits: string[] = []
  text.split('\n').forEach((line, i) => {
    // Skip comment-only lines (design rationale often cites hex/px in prose).
    const trimmed = line.trim()
    if (trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')) return
    for (const kind of lineViolations(line)) hits.push(`${i + 1}: ${kind} — ${trimmed.slice(0, 80)}`)
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

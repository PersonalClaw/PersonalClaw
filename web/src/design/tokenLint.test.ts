import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { lineViolations, stripComments } from './tokenLintRule'

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
//
// Comments are excluded by TRACKING block state across lines (`stripComments`, #3337),
// not by inspecting each line's own first characters. The old shape-based skip could not
// see an INTERIOR line of a multi-line `{/* … */}` block — which carries no marker — so
// it linted one as code, and since every decimal digit is a hex digit the HEX pattern
// matched any 3-to-8-digit issue reference. Citing `#1783` in a comment reddened the rail.
function violations(file: string): string[] {
  const text = readFileSync(file, 'utf8')
  const lines = text.split('\n')
  const hits: string[] = []
  // Report the ORIGINAL line, not the stripped one: a violation should read the way the
  // author wrote it. Only the VERDICT runs on code-only text.
  stripComments(text).code.forEach((code, i) => {
    for (const kind of lineViolations(code)) {
      hits.push(`${i + 1}: ${kind} — ${lines[i].trim().slice(0, 80)}`)
    }
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

  it('an issue reference inside a block comment is not a raw hex (#3337)', () => {
    // The reported defect, as a unit: `SkillInspector.tsx:65`'s shape — a six-line
    // `{/* … */}` whose third line cites the issue number and carries no marker of its
    // own. Both mutations on the real file went 3-passed (drop the `(#1783)`, or prefix
    // the line with `//`), so the reference IS the whole match and the comment skip was
    // the only thing that would have covered it.
    const jsx = [
      '  return (',
      '    {/* Which surface this is: the inspector for a',
      '        persistently-wrong auto skill (#1783). The inspector,',
      '        not the list row — the row only summarises. */}',
      '    <div className="bg-surface-high" />',
      '  )',
    ].join('\n')
    expect(stripComments(jsx).code.flatMap(lineViolations)).toEqual([])
    // …and the rail is not now blind: the SAME text with the comment markers removed,
    // and a genuine raw hex after a CLOSED block, are both still caught.
    expect(stripComments('  const label = "auto skill (#1783)"').code.flatMap(lineViolations))
      .toEqual(['hex'])
    expect(stripComments('/* see #1783 */\nconst c = \'#1a2b3c\'').code.flatMap(lineViolations))
      .toEqual(['hex'])
  })

  it('no source file ends outside code state — the tracker is not stuck open', () => {
    // THE control on this change. A tracker that leaves block-comment (or template)
    // state open reads as "the rest of the file is clean" and silently stops catching
    // raw hexes — the rail's entire value, lost without a red. Over the real corpus,
    // every file must return to `code` by EOF. A genuinely unterminated `/*` in source
    // would also land here, which is the right place for it.
    const stuck = files
      .map((f) => [relative(SRC, f).replace(/\\/g, '/'), stripComments(readFileSync(f, 'utf8')).endState] as const)
      .filter(([, state]) => state !== 'code')
    expect(stuck, `Comment/template state left open at EOF:\n${JSON.stringify(stuck, null, 2)}`).toEqual([])
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

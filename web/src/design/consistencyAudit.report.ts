// ── Design-System Consistency Audit — reporter (Plan: DESIGN-SYSTEM-CONSISTENCY, S1/T1.1) ──
//
// This module is the *measurement engine* for the consistency audit. Unlike
// tokenLint.test.ts (a pass/fail RATCHET that only guards hex/inline-px in
// not-yet-clean files), this reporter INVENTORIES every design-value drift in
// web/src across five categories, plus a primitive-adoption scan, grouped by
// file:line. It emits a machine-readable JSON inventory that the audit doc
// (docs/design/consistency-audit.md) and the S1 report task consume.
//
// It makes NO fixes and fails NO build — it only measures. Run via the
// companion test (consistencyAudit.test.ts) or import scanDrift() directly.

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

export type DriftCategory =
  | 'color'      // raw hex / rgb() / hsl() color literals
  | 'spacing'    // inline-style px for padding/margin/gap/width/height/inset
  | 'radius'     // inline-style borderRadius px literals
  | 'shadow'     // inline boxShadow with raw px/color (not a token)
  | 'duration'   // raw transition/animation ms literals

export type PrimitiveDrift =
  | 'raw-button'   // <button> outside ui/ — should be Button/IconButton
  | 'raw-input'    // <input>/<textarea>/<select> — form primitive candidates
  | 'raw-dialog'   // ad-hoc modal markup — should be Modal

export interface DriftHit {
  file: string        // relative to web/src
  line: number
  category: DriftCategory
  snippet: string
}

export interface PrimitiveHit {
  file: string
  kind: PrimitiveDrift
  count: number
}

export interface AuditResult {
  drift: DriftHit[]
  byFile: Record<string, Record<DriftCategory, number>>
  byCategory: Record<DriftCategory, number>
  primitives: PrimitiveHit[]
  primitivesByFile: Record<string, Record<PrimitiveDrift, number>>
  totals: { driftHits: number; filesScanned: number; filesWithDrift: number }
}

const SRC = join(process.cwd(), 'src')

// design/ DEFINES tokens — never a drift source.
const EXEMPT_DIRS = ['design/']
// Files that legitimately carry raw values (canvas math, brand palettes,
// editor/terminal chrome, scheme-fallback definitions) — same rationale as
// tokenLint.test.ts's EXEMPT_FILES. These are excluded from the COLOR category
// only (their raw hex is intentional identity, not drift), but their layout/
// spacing drift is still measured.
const COLOR_EXEMPT_FILES = new Set<string>([
  'ui/DotGlow.tsx',
  'ui/ClawMark.tsx',
  'ui/Spark.tsx',
  'ui/WavyProgress.tsx',
  'pages/files/fileMeta.ts',
  'ui/content/registerBuiltins.ts',
  'ui/content/exporters.ts',
  'pages/terminal/TerminalView.tsx',
  'pages/code/DiffReveal.tsx',
  'pages/code/TypingReveal.tsx',
  'app/appearance.tsx',
  'pages/settings/settingsWidgets.tsx',
])

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

const HEX = /#[0-9a-fA-F]{3,8}\b/
const RGB_HSL = /\b(rgba?|hsla?)\(/
// Inline-style px in a spacing/size property. Excludes legitimate px contexts
// (grid track sizing, hairline borders, computed Math/calc px, token+offset).
const RAW_PX = /style=\{\{[^}]*?\b\d+px\b/
const PX_OK_CONTEXT = /minmax\(|repeat\(|\bmin\(|\bmax\(|\bclamp\(|\b(border|outline)(-[a-z]+)?:\s*[^;}]*\d+px|border[A-Z][a-zA-Z]*:\s*[`'"]?\s*\$?\{?[^}]*\d+px|Math\.(min|max)\(/
const BORDER_RADIUS = /borderRadius:\s*[`'"]?\s*\d+px/
const BOX_SHADOW = /boxShadow:\s*[`'"][^`'"]*\d+px[^`'"]*[`'"]/
// Raw duration: transition/animation with a bare ms number in inline style, or
// a tailwind duration-<n> arbitrary that isn't a motion token.
const DURATION = /transition[^:]*:\s*[^;}]*\b\d+ms|animation[^:]*:\s*[^;}]*\b\d+ms|duration-\[\d+ms\]/

function isCommentLine(trimmed: string): boolean {
  return trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')
}

function scanFile(file: string, rel: string): DriftHit[] {
  const text = readFileSync(file, 'utf8')
  const hits: DriftHit[] = []
  const colorExempt = COLOR_EXEMPT_FILES.has(rel)
  text.split('\n').forEach((line, i) => {
    const trimmed = line.trim()
    if (isCommentLine(trimmed)) return
    const at = (category: DriftCategory) =>
      hits.push({ file: rel, line: i + 1, category, snippet: trimmed.slice(0, 100) })

    if (!colorExempt && (HEX.test(line) || RGB_HSL.test(line))) at('color')
    if (BORDER_RADIUS.test(line)) at('radius')
    else if (RAW_PX.test(line) && !/calc\([^)]*var\(/.test(line) && !PX_OK_CONTEXT.test(line)) at('spacing')
    if (BOX_SHADOW.test(line) && !/var\(--/.test(line)) at('shadow')
    if (DURATION.test(line) && !/var\(--/.test(line)) at('duration')
  })
  return hits
}

const CATEGORIES: DriftCategory[] = ['color', 'spacing', 'radius', 'shadow', 'duration']

function scanPrimitives(file: string, rel: string): PrimitiveHit[] {
  // ui/ primitives ARE the canonical elements — don't flag their internals.
  if (rel.startsWith('ui/')) return []
  const text = readFileSync(file, 'utf8')
  const count = (re: RegExp) => (text.match(re) ?? []).length
  const out: PrimitiveHit[] = []
  const btn = count(/<button[\s>]/g)
  const inp = count(/<(input|textarea|select)[\s>]/g)
  const dlg = count(/role=["']dialog["']|<dialog[\s>]/g)
  if (btn) out.push({ file: rel, kind: 'raw-button', count: btn })
  if (inp) out.push({ file: rel, kind: 'raw-input', count: inp })
  if (dlg) out.push({ file: rel, kind: 'raw-dialog', count: dlg })
  return out
}

export function scanDrift(): AuditResult {
  const files = walk(SRC)
  const drift: DriftHit[] = []
  const primitives: PrimitiveHit[] = []
  for (const f of files) {
    const rel = relative(SRC, f).replace(/\\/g, '/')
    drift.push(...scanFile(f, rel))
    primitives.push(...scanPrimitives(f, rel))
  }

  const byFile: Record<string, Record<DriftCategory, number>> = {}
  const byCategory = Object.fromEntries(CATEGORIES.map((c) => [c, 0])) as Record<DriftCategory, number>
  for (const h of drift) {
    byFile[h.file] ??= Object.fromEntries(CATEGORIES.map((c) => [c, 0])) as Record<DriftCategory, number>
    byFile[h.file][h.category]++
    byCategory[h.category]++
  }

  const primitivesByFile: Record<string, Record<PrimitiveDrift, number>> = {}
  for (const p of primitives) {
    primitivesByFile[p.file] ??= { 'raw-button': 0, 'raw-input': 0, 'raw-dialog': 0 }
    primitivesByFile[p.file][p.kind] += p.count
  }

  return {
    drift,
    byFile,
    byCategory,
    primitives,
    primitivesByFile,
    totals: {
      driftHits: drift.length,
      filesScanned: files.length,
      filesWithDrift: Object.keys(byFile).length,
    },
  }
}

// ── a11y coverage scan (S1: reduced-motion + focus-visible) ────────────────
// The app has GLOBAL safety nets: tokens.css has a `*` reduced-motion rule and
// a global `:focus-visible` ring, and App.tsx wraps everything in Framer's
// <MotionConfig reducedMotion="user">. So the meaningful measurement is not
// "how many files honor reduced-motion" (global covers CSS + JS) but "how many
// controls null their outline WITHOUT a local focus-visible replacement" —
// those rely entirely on the global ring, which is the intended design, but a
// count tells us the blast radius if the global rule were ever removed.

export interface A11yCoverage {
  outlineNoneFiles: string[]      // files that set outline-none / outline:none
  outlineNoneCount: number        // total occurrences
  localFocusVisibleFiles: string[] // files with a local focus-visible override
  reducedMotionFiles: string[]    // files with an explicit prefers-reduced-motion
  animatedFiles: number           // files using transition-/animate-/animation:
  hasGlobalReducedMotion: boolean // tokens.css `*` reduced-motion rule present
  hasGlobalFocusRing: boolean     // tokens.css global :focus-visible ring present
}

const OUTLINE_NONE = /outline-none|outline:\s*none|outline:\s*0\b/
const FOCUS_VISIBLE = /focus-visible|:focus-visible/
const REDUCED_MOTION = /prefers-reduced-motion/
const ANIMATED = /transition-|animate-|animation:|@keyframes/

function walkAll(dir: string, exts: RegExp): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...walkAll(p, exts))
    else if (exts.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

export function scanA11y(): A11yCoverage {
  const files = walkAll(SRC, /\.(tsx?|css)$/)
  const outlineNoneFiles: string[] = []
  const localFocusVisibleFiles: string[] = []
  const reducedMotionFiles: string[] = []
  let outlineNoneCount = 0
  let animatedFiles = 0
  let hasGlobalReducedMotion = false
  let hasGlobalFocusRing = false

  for (const f of files) {
    const rel = relative(SRC, f).replace(/\\/g, '/')
    const text = readFileSync(f, 'utf8')
    const onMatches = text.match(new RegExp(OUTLINE_NONE, 'g'))
    if (onMatches && !rel.startsWith('design/')) {
      outlineNoneFiles.push(rel)
      outlineNoneCount += onMatches.length
    }
    if (FOCUS_VISIBLE.test(text) && !rel.startsWith('design/')) localFocusVisibleFiles.push(rel)
    if (REDUCED_MOTION.test(text) && !rel.startsWith('design/')) reducedMotionFiles.push(rel)
    if (ANIMATED.test(text)) animatedFiles++
    if (rel === 'design/tokens.css') {
      hasGlobalReducedMotion = /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[^}]*\*/.test(text)
      hasGlobalFocusRing = /:focus-visible\s*\{[^}]*outline/.test(text)
    }
  }

  return {
    outlineNoneFiles,
    outlineNoneCount,
    localFocusVisibleFiles,
    reducedMotionFiles,
    animatedFiles,
    hasGlobalReducedMotion,
    hasGlobalFocusRing,
  }
}

// Rank files by a weighted drift score for the audit's "worst offenders" list.
// Weights: color drift is worst (bypasses theming), then primitive adoption
// (bespoke chrome), then spacing/radius/shadow/duration.
export function rankFiles(res: AuditResult): { file: string; score: number; detail: string }[] {
  const scores = new Map<string, number>()
  const bump = (file: string, n: number) => scores.set(file, (scores.get(file) ?? 0) + n)
  for (const h of res.drift) {
    const w = h.category === 'color' ? 5 : h.category === 'shadow' ? 3 : 2
    bump(h.file, w)
  }
  for (const p of res.primitives) {
    const w = p.kind === 'raw-dialog' ? 4 : p.kind === 'raw-button' ? 2 : 1
    bump(p.file, p.count * w)
  }
  return [...scores.entries()]
    .map(([file, score]) => {
      const d = res.byFile[file]
      const pf = res.primitivesByFile[file]
      const parts: string[] = []
      if (d) for (const c of CATEGORIES) if (d[c]) parts.push(`${d[c]} ${c}`)
      if (pf) {
        if (pf['raw-button']) parts.push(`${pf['raw-button']} raw-button`)
        if (pf['raw-input']) parts.push(`${pf['raw-input']} raw-input`)
        if (pf['raw-dialog']) parts.push(`${pf['raw-dialog']} raw-dialog`)
      }
      return { file, score, detail: parts.join(', ') }
    })
    .sort((a, b) => b.score - a.score)
}

// ── Inline font-weight scan (design-system consistency, S2/S3) ──────────────
// Counts INLINE `fontVariationSettings: '"wght" <n>'` across web/src (excl.
// design/, which is the fvs() helper's home). These should use fvs()/.fw-* or
// a data-type role. Used by primitiveAdoption.test.ts as a ratchet so the count
// may only shrink — a new inline weight turns CI red, locking in the migration.
export function countInlineFontWeights(): { total: number; byFile: Record<string, number> } {
  const files = walk(SRC)
  const re = /fontVariationSettings:\s*[`'"]\s*"wght"/
  const byFile: Record<string, number> = {}
  let total = 0
  for (const f of files) {
    const rel = relative(SRC, f).replace(/\\/g, '/')
    const text = readFileSync(f, 'utf8')
    let n = 0
    for (const line of text.split('\n')) {
      const t = line.trim()
      if (t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')) continue
      if (re.test(line)) n++
    }
    if (n) { byFile[rel] = n; total += n }
  }
  return { total, byFile }
}

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
import { createRequire } from 'node:module'
import { dirname, join, relative, resolve } from 'node:path'

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

// ── Inert-utility scan (issue #556) ─────────────────────────────────────────
// A `text-*`/`bg-*`/`border-*` class naming a token that does not exist is not
// a lint nit — it emits NO CSS at all, so the styling is silently absent and
// the element renders at whatever it inherited. That is invisible to every
// existing rail: token-lint checks raw hex/px (a wrong VALUE), primitive-
// adoption checks bespoke elements, and TypeScript never sees inside a string.
// ConflictPanel.tsx shipped five such classes (`text-muted`, `border-border`,
// `bg-accent-subtle`, `text-accent`) and the panel had no visual hierarchy.
//
// The oracle is Tailwind itself, not a token-name list: we load the real design
// system from design/tokens.css and ask it to compile each candidate. A class
// that compiles to nothing is inert. Asking the compiler (rather than
// regex-matching `--color-*` names) is what makes this correct for the whole
// utility surface — variants (`hover:`, `group-hover/dock:`), opacity modifiers
// (`/40`), arbitrary values (`text-[0.75rem]`, `border-l-[3px]`), non-color
// utilities that share the prefixes (`text-center`, `border-t`, `bg-gradient-
// to-br`) and static colors (`bg-white`) all resolve exactly as they do in the
// build, so none of them can become a false positive.

/** A utility class that Tailwind compiles to nothing — it emits no CSS. */
export interface InertUtilityHit {
  file: string        // relative to web/src
  line: number
  /** The full candidate as written, variants included (e.g. `hover:bg-surface-2`). */
  utility: string
  /** The bare utility with variants and any opacity modifier stripped. */
  base: string
}

/** Compiles a Tailwind candidate against the app's real design system. */
export type UtilityOracle = (candidate: string) => boolean

/** Loads design/tokens.css into a live Tailwind design system and returns an
 *  oracle that reports whether a candidate class emits any CSS. Tailwind's
 *  loader is the same one @tailwindcss/vite drives at build time, so the answer
 *  matches the shipped bundle by construction. */
export async function loadUtilityOracle(): Promise<UtilityOracle> {
  const { __unstable__loadDesignSystem } = await import('tailwindcss')
  const require_ = createRequire(import.meta.url)
  const entry = join(SRC, 'design/tokens.css')
  const ds = await __unstable__loadDesignSystem(readFileSync(entry, 'utf8'), {
    base: dirname(entry),
    loadStylesheet: async (id: string, base: string) => {
      // `@import "tailwindcss"` resolves through node, everything else is relative.
      const path = id === 'tailwindcss' ? require_.resolve('tailwindcss/index.css') : resolve(base, id)
      return { path, base: dirname(path), content: readFileSync(path, 'utf8') }
    },
    // tokens.css is pure CSS — a plugin/config import would mean the entry
    // changed shape, and silently returning an empty module would make every
    // plugin utility look inert. Fail loudly instead.
    loadModule: async (id: string) => {
      throw new Error(`inert-utility scan: unexpected JS import ${id} from tokens.css`)
    },
  })
  return (candidate: string) => ds.candidatesToCss([candidate])[0] !== null
}

/** Class selectors hand-authored in design/*.css (`.text-shimmer`, `.glass`, …).
 *  These are real CSS but Tailwind knows nothing about them, so they'd read as
 *  inert. Reading the selectors keeps the exemption self-maintaining. */
function handAuthoredClasses(): Set<string> {
  const out = new Set<string>()
  const dir = join(SRC, 'design')
  for (const entry of readdirSync(dir)) {
    if (!entry.endsWith('.css')) continue
    const text = readFileSync(join(dir, entry), 'utf8')
    for (const m of text.matchAll(/\.(-?[a-zA-Z_][\w-]*)/g)) out.add(m[1])
  }
  return out
}

/** Extracts the VALUE of every `className=`/`class=` attribute — a quoted
 *  string, or a balanced-brace expression (template literals, ternaries,
 *  concatenations). Scoping to these regions is what keeps prose, SVG attribute
 *  allowlists (`'text-anchor'`) and doc-comment class names out of the scan. */
function classAttributeRegions(text: string): { offset: number; body: string }[] {
  const out: { offset: number; body: string }[] = []
  const re = /\bclass(?:Name)?\s*=\s*/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    const i = m.index + m[0].length
    if (text[i] === '{') {
      let depth = 0
      let j = i
      for (; j < text.length; j++) {
        if (text[j] === '{') depth++
        else if (text[j] === '}') { depth--; if (!depth) break }
      }
      out.push({ offset: i + 1, body: text.slice(i + 1, j) })
    } else if (text[i] === '"' || text[i] === "'" || text[i] === '`') {
      const j = text.indexOf(text[i], i + 1)
      if (j > 0) out.push({ offset: i + 1, body: text.slice(i + 1, j) })
    }
  }
  return out
}

// Split a className body on characters that can't appear in a Tailwind class,
// so each token is a WHOLE candidate — variants and modifiers attached. Handing
// the compiler a whole token is essential: `bg-primary` alone is valid, so a
// scanner that stripped `group-hover/handle:` off `group-hover/handle:bg-primary`
// would test a different class than the one in the source.
const CLASS_TOKEN_SPLIT = /[^A-Za-z0-9_@:./![\]%&>*+~(),#='"-]+|["'`]/
// Only utilities in the three prefixes this guard covers (bare or after a variant).
const SCANNED_PREFIX = /(?:^|:)!?-?(?:text|bg|border)-/

/** Scans every `className` in web/src for utilities that compile to no CSS. */
export async function scanInertUtilities(): Promise<InertUtilityHit[]> {
  const isLive = await loadUtilityOracle()
  const handAuthored = handAuthoredClasses()
  const hits: InertUtilityHit[] = []
  for (const file of walk(SRC)) {
    const rel = relative(SRC, file).replace(/\\/g, '/')
    const text = readFileSync(file, 'utf8')
    const lineStarts = [0]
    for (let i = 0; i < text.length; i++) if (text[i] === '\n') lineStarts.push(i + 1)
    const lineAt = (idx: number) => {
      let lo = 0
      let hi = lineStarts.length - 1
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1
        if (lineStarts[mid] <= idx) lo = mid
        else hi = mid - 1
      }
      return lo + 1
    }
    for (const { offset, body } of classAttributeRegions(text)) {
      let cursor = 0
      for (const token of body.split(CLASS_TOKEN_SPLIT)) {
        const at = body.indexOf(token, cursor)
        cursor = at + token.length
        if (!token || !SCANNED_PREFIX.test(token)) continue
        const bare = token.replace(/^.*:/, '').replace(/^!/, '')
        const base = bare.replace(/\/(?:\d+|\[[^\]]*\])$/, '')
        if (handAuthored.has(base)) continue
        if (isLive(token)) continue
        hits.push({ file: rel, line: lineAt(offset + at), utility: token, base })
      }
    }
  }
  return hits
}

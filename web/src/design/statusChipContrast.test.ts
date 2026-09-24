/**
 * A STATUS CHIP PAINTS ITS TONE TWICE — AS THE INK **AND** AS THE BACKGROUND — AND NOTHING SWEPT
 * THE SECOND ONE.
 *
 * The idiom, 88 sites across 50 files (census below):
 *
 *     style={{ background: `color-mix(in srgb, ${tone} 16%, transparent)`, color: tone }}
 *
 * So the ground is a translucent wash of the ink itself. Every percent of tint drags the ground
 * TOWARD the ink and spends contrast, and because the wash is translucent the ratio also depends on
 * whichever surface tier the chip happens to land on. Neither is knowable at the call site, and
 * `schemeContrast.test.ts` asserted the tone against the BARE tier — the one ground the chip never
 * actually has. Measured live on `#/tasks?view=cards`, the 12px "In progress" chip:
 *
 *     --color-info → bare --color-surface-container    5.82     ← all the old rail checked. Passed.
 *     --color-info → its own 16% tint on that ground   4.4992   ← axe `color-contrast`, serious
 *
 * One hundredth under 4.5. Swept over 12 schemes × 2 modes that single chip was under AA in
 * **13 of 24** combos (worst 3.4165, slate/light on the canvas) and the 18% chip in
 * `tasks/TaskDetail.tsx` in **16 of 24** (worst 3.3253, the same cell). `--color-info` is the ONLY
 * semantic tone a scheme retints, and that is the whole shape of the defect: `ok`/`warn`/`danger`
 * are global values in `tokens.css`, so they are scheme-invariant and pass everywhere the tree uses
 * them at ≤16% (worst 4.5200), while `info` was supplied twelve times over by `schemes.ts` and
 * swept on a tinted ground zero times.
 *
 * THE FIX WAS THE INK, PER SCHEME — hue angle held, chroma held (0.88–1.00 of the original; given up
 * only where sRGB cannot hold the lightness), lightness nudged AWAY from the mid-tone: brighter in
 * dark, darker in light. It is deliberately NOT a call-site override and NOT a new token: the
 * semantic tones have no `-emphasis` or `-container` sibling to reach for the way `primary` does
 * (`--color-on-primary-tint` → `--color-primary-emphasis`, and `#1257`'s accent chip → the
 * container pair), and minting one per tone × 12 schemes to solve a lightness problem would be a
 * new vocabulary instead of a corrected value. The derivation lives in `tokens.css` beside
 * `--color-info`.
 *
 * ── WHY NEITHER EXISTING GATE COULD SEE THIS ────────────────────────────────────────────────────
 *
 *   · axe only ever drives the DEFAULT scheme, so 11 of 12 were invisible to it by construction.
 *   · `#/tasks?view=cards` is not in `e2e/routes.ts` at all, and the plain `tasks` route that IS
 *     there is `needsData: true` against a webServer started on a FRESH temp home with no `--seed`
 *     — so the list renders empty and the chip axe would have judged does not exist. A chip with no
 *     data is a chip no mechanical pass can measure.
 *   · `schemeContrast.test.ts` already sweeps `info` per scheme, but against
 *     `--color-surface-container` bare. That assertion passes at 5.82 while the painted chip is
 *     4.4992 — a rail can be green on the right token, the right scheme and the wrong ground.
 *
 * ── TRAPS THIS FILE ENCODES, EACH ONE MEASURED ──────────────────────────────────────────────────
 *
 * 🪤 THE COMPOSITE MUST BE QUANTIZED TO 8 BITS. Blending #5e99f9 at 16% over #1e1f20 gives
 *    (40.24, 50.52, 66.72). Carried as floats that reads **4.5214** and the chip looks compliant;
 *    rounded to `#283343`, which is what the framebuffer paints and what axe-core's `flattenColors`
 *    computes (`Math.round(r / αo)`), it is **4.4992**. The un-rounded number is how you conclude
 *    there is no bug.
 *
 * 🪤 A `color-mix(…, transparent)` DOES NOT COMPUTE TO `rgba()`. `getComputedStyle` returns
 *    `color(srgb 0.368627 0.6 0.976471 / 0.16)`, so a probe whose parser only knows `rgb()/rgba()`
 *    reports ZERO tinted chips on a page full of them. (#1257 recorded the sibling of this: an
 *    `/80` alpha ink parsed as an rgb triple yielded 1.46 dark / 18.51 light — nonsense.)
 *
 * 🪤 `--color-surface-high` IS NOT A REFERENCE GROUND HERE, and excluding it is a measurement, not
 *    a convenience — but NOT for the reason a first draft of this comment gave. It claimed "the BARE
 *    ink on that tier is already 4.42, so no ink value fixes it". That number is `surface-highest`
 *    (bare 4.3399), not `surface-high`; re-measured, the bare ink on `surface-high` (`#282a2c`) is
 *    **5.0764** and perfectly fine, so the tint IS what breaks it and an ink value COULD in
 *    principle fix it. The real reason to exclude it is the size of that ink move: `TaskCard` lifts
 *    to `hover:bg-surface-high`, where the chip reads **3.9278** before this change and **4.1094**
 *    after, and clearing 4.5 there needs roughly `#7aaeff` — which is not a nudge, and which on
 *    `surface-container` (the RESTING ground, where thirty cards sit) would read 5.41 and visibly
 *    wash the tone pale. A hover tier that lifts ten sRGB steps toward the ink is a GROUND problem,
 *    and the fix is for the chip's ground to stop moving under it, which is a different change.
 *    `schemeContrast.test.ts` refuses `surface-highest` on the neighbouring reasoning ("testing it
 *    would invent a failure the default itself doesn't meet") and there the bare-ink figure really
 *    does hold. The hovered reading is filed as an open finding, not smuggled into the ink budget.
 *
 * 🪤 THE PARSER DEPTH-TRACKS BRACES AND IS ANCHORED ON THE TINT, NOT ON `style={`. Two reasons.
 *    A JSX opening tag does not end at the first `>` (nor the first that is not `=>`) — an
 *    `action={<X …>label</X>}` attribute contains several — so no line- or tag-window scan is
 *    sound. And 6 of the 88 sites reach `style` through a ternary (`? { background: …, color: … }`),
 *    where anchoring on `style={` misses them outright: an earlier draft of this census read 81.
 *
 * 🪤 AN ICON TILE IS NOT A CHIP AND MUST NOT BE COUNTED. Six sites
 *    (`agents/AgentsListPage:264`, `skills/SkillsPage:156`, `knowledge/KnowledgeListPage:721`,
 *    `prompts/PromptsListPage:197`, `triggers/TriggersListPage:250`, `knowledge/KnowledgeCreatePage:38`)
 *    put the tint on a `size-10` wrapper and the tone on an `<Icon>` CHILD — a different object, so
 *    the same-object test below excludes them, correctly: a glyph carries the 3:1 non-text floor,
 *    and `PRODUCT-POLISH.md` §5 records that family as killed on measurement with an owner verdict
 *    at `design/accent.ts`. An earlier census that matched "the rest of the line" swept them in and
 *    inflated the population by six.
 *
 * 🪤 A BORDER IS NOT A BACKGROUND. Four sites tint at 35–40% — `ChatPage:2967`, `FileTree:249`,
 *    `WorkspacePicker:186`, `ArtifactViewer:291` — and every one is a `border` / `borderColor`
 *    hairline, so the text still sits on the parent's opaque tier and 1.4.3 is satisfied there.
 *    Counting them (an earlier draft did) manufactures four failures at 2.9–3.2 that no reader can
 *    see, and would have argued for an ink nudge four times larger than the real defect needs.
 */

import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { SCHEMES } from './schemes'

const WEB = process.cwd()
const SRC = join(WEB, 'src')

// ── WCAG 2.1 relative luminance + contrast ratio (sRGB) ─────────────────────────────────────────
// Kept local, per this directory's convention (`schemeContrast`, `errorTreatments`, `onDangerInk`
// each carry their own copy so a shared helper cannot drift one rail while fixing another).
function luminance(hex: string): number {
  const h = hex.replace('#', '')
  const chan = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * chan(0) + 0.7152 * chan(2) + 0.0722 * chan(4)
}
function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

const rgb = (hex: string): [number, number, number] => {
  const h = hex.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number]
}
const hex = (c: number[]) => '#' + c.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')

/** `contrast(tone, color-mix(in srgb, tone <pct>%, transparent) over <ground>)`.
 *
 *  The `Math.round` is load-bearing and matches axe-core's `flattenColors`, which ends
 *  `Math.round(r / αo)` — and matches the 8-bit framebuffer, which is what a reader's eye receives.
 *  Without it the flagship failure reads 4.5214 instead of 4.4992 and this whole rail is green on a
 *  defect axe reports. */
function tintedChipRatio(tone: string, ground: string, pct: number): number {
  const a = pct / 100, I = rgb(tone), G = rgb(ground)
  return contrast(tone, hex(I.map((v, i) => Math.round(a * v + (1 - a) * G[i]))))
}

/** THE SIZE THRESHOLD, SPELLED OUT, because "large text" is the one way a 4.5 finding gets talked
 *  down to 3:1. WCAG 2.1 SC 1.4.3: large text is ≥18.66px BOLD or ≥24px at any weight; everything
 *  else carries 4.5:1. A 12px chip is not large at any weight, so it never gets the 3:1 relief. */
export function aaFloor(px: number, bold: boolean): 3 | 4.5 {
  const large = px >= 24 || (px >= 18.66 && bold)
  return large ? 3 : 4.5
}

/** `tokens.css` split at the `.light` RULE BLOCK — not at the first occurrence of the string
 *  ".light", which appears in prose earlier in the file and made a sibling rail read the DARK
 *  canvas as the light one (the trap `schemeContrast.test.ts` records). */
function blocks(): { dark: string; light: string } {
  const src = readFileSync(join(SRC, 'design/tokens.css'), 'utf8')
  const at = src.search(/\.light\s*\{/)
  if (at < 0) throw new Error('could not find the .light rule block in tokens.css')
  return { dark: src.slice(0, at), light: src.slice(at) }
}
const BLOCKS = blocks()
type Mode = 'dark' | 'light'
function token(name: string, mode: Mode): string {
  const m = BLOCKS[mode].match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`))
  if (!m) throw new Error(`--color-${name} has no direct hex value in the ${mode} block of tokens.css`)
  return m[1]
}

// ── The population, derived from the tree ───────────────────────────────────────────────────────

type Site = { file: string; line: number; tone: string; pct: number; literal: boolean }

const TINT =
  /\b(?:background|backgroundColor)\s*:\s*[`'"]?\s*color-mix\(in srgb,\s*(\$\{[^}]+\}|[^,]+?)\s+([\d.]+)%,\s*transparent\)/g

/** The brace-balanced object literal enclosing `idx`, found by DEPTH-TRACKING `{`/`}` outward in
 *  both directions. See the header's parser trap: anchoring on `style={` misses the ternary form,
 *  and no `>`-terminated window is sound inside JSX. */
function enclosingObject(src: string, idx: number): string | null {
  let depth = 0, start = -1
  for (let i = idx; i >= 0; i--) {
    const c = src[i]
    if (c === '}') depth++
    else if (c === '{') { if (depth === 0) { start = i; break } depth-- }
  }
  if (start < 0) return null
  depth = 0
  for (let i = start; i < src.length; i++) {
    const c = src[i]
    if (c === '{') depth++
    else if (c === '}') { depth--; if (depth === 0) return src.slice(start, i + 1) }
  }
  return null
}

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.tsx?$/.test(name) && !/\.(test|doc)\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

/** Every element whose `style` object puts ONE tone in both `color` and a
 *  `color-mix(…, transparent)` BACKGROUND — the tone-as-ink-and-tint family. */
function chipSites(): Site[] {
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const sites: Site[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    for (const m of src.matchAll(TINT)) {
      const tone = m[1].replace(/^\$\{/, '').replace(/\}$/, '').replace(/^['"`]|['"`]$/g, '').trim()
      const obj = enclosingObject(src, m.index!)
      if (!obj) continue
      const ink = new RegExp(`(?:^|[{,\\s])color:\\s*['"\`]?(?:\\$\\{)?${esc(tone)}(?:\\})?['"\`]?\\s*[,}]`)
      if (!ink.test(obj)) continue
      sites.push({
        file: abs.slice(SRC.length + 1), line: src.slice(0, m.index!).split('\n').length,
        tone, pct: Number(m[2]), literal: /^var\(--color-[a-z-]+\)$/.test(tone),
      })
    }
  }
  return sites
}

const SITES = chipSites()

// ── The reference grounds ───────────────────────────────────────────────────────────────────────
//
// The four tiers a chip RESTS on, read per mode from source so a retint cannot drift the guard.
// `surface-high`/`-highest` are excluded on measurement — see the header's third trap.
const RESTING = ['canvas', 'surface', 'surface-low', 'surface-container'] as const

// ── The tone vocabulary, READ OUT OF THE PRIMITIVE ──────────────────────────────────────────────
//
// 🔴 A HAND-WRITTEN TONE LIST CANNOT NOTICE A TONE IT DOES NOT MENTION (#3493). This was
// `const GLOBAL_TONES = ['ok', 'warn', 'danger']` plus `info` swept per scheme — four of the SIX
// tones `ui/StatusPill` vends. The two it never named, `neutral` and `primary`, are exactly where the
// next defect was: `neutral` drew its text in `--color-outline-variant`, a hairline/border value, and
// read **1.6346 dark** / **1.1381 light** over its own 16% tint — all 8 resting cells under AA, on ten
// call sites. The old list was not wrong about the tones it swept; it was silent about the ones it
// did not, and silence is indistinguishable from a pass.
//
// So the list is DERIVED. Every tone in `TONE_VAR` is either swept below or named in `UNSWEPT_TONES`
// with its measurement, and the assertion further down closes the set both ways: a SEVENTH tone reds
// this file until someone sweeps it or argues for it, and a tone that stops being vended reds the
// exclusion pin. The old prose said "`on-surface-low`/`-var` … are the ink RAMP rather than semantic
// tones and … are covered by the ceiling ratchet, not swept here" — that is no longer true of
// `on-surface-low`, which is now `neutral`'s ink and is swept like any other.

/** `tone → '--color-…'`, parsed out of `ui/StatusPill.tsx`'s `TONE_VAR`.
 *
 *  🪤 THE PARSE IS THE VACUITY HOLE, so it is controlled rather than trusted: a tone whose ink stops
 *  being a plain `var(--color-x)` literal (an imported constant, a `color-mix`, a ternary) simply
 *  would not match, and the sweep would silently shrink by one — the same failure this file exists to
 *  end. `tone_vocabulary` below cross-checks these keys against the `StatusPillTone` UNION parsed
 *  from the same file, so a skipped entry reds instead of vanishing. */
function pillToneInks(): Record<string, string> {
  const src = readFileSync(join(SRC, 'ui/StatusPill.tsx'), 'utf8')
  const block = src.match(/const TONE_VAR:[^=]*=\s*\{([\s\S]*?)\n\}/)
  if (!block) throw new Error('could not find the TONE_VAR map in ui/StatusPill.tsx')
  const out: Record<string, string> = {}
  for (const m of block[1].matchAll(/^\s*([a-z]+):\s*'var\((--color-[a-z-]+)\)',/gm)) out[m[1]] = m[2]
  return out
}
const PILL_TONES = pillToneInks()

/** The `StatusPillTone` union's members, from the same file — the control on the parse above. */
function pillToneUnion(): string[] {
  const src = readFileSync(join(SRC, 'ui/StatusPill.tsx'), 'utf8')
  const m = src.match(/export type StatusPillTone\s*=\s*([^\n]+)/)
  if (!m) throw new Error('could not find the StatusPillTone union in ui/StatusPill.tsx')
  return [...m[1].matchAll(/'([a-z]+)'/g)].map((x) => x[1])
}

/** Which `--color-*` tokens a scheme supplies, read off a scheme rather than listed. A tone whose
 *  ink is one of these is swept once PER SCHEME; anything else is a global value in `tokens.css`. */
const SCHEME_SUPPLIED = new Set(Object.keys(SCHEMES[0].colors))

/** THE BORDER/HAIRLINE TOKENS, BY NAME — the class of value that must never be a text ink.
 *
 *  This is a denylist rather than a threshold because the property is structural, not numeric: a
 *  hairline exists to be *barely* separable from its surface, so being close to the ground is the
 *  token's job. `no_hairline_ink` asserts no tone uses one, and `hairline_unreadable` asserts these
 *  values really are unreadable on every tier — so the denylist carries its own justification and
 *  cannot rot into a superstition. `--color-outline` is listed for the same reason plus a structural
 *  tell: it is `#8e918f` in BOTH modes, and an ink that does not invert with the mode cannot be
 *  readable in both.
 *
 *  🔴 SCOPE: THIS GUARDS `ui/StatusPill`'s MAP AND NOTHING ELSE. `pillToneInks` reads one file, so a
 *  tone map anywhere else in the tree is invisible to it. A census run for #3493 found exactly one
 *  other live case — `pages/learning/learningMeta.ts`'s `DAY_TONE.out_of_scope`, `--color-outline` as
 *  text ink at 3.1833 on the light `surface-container` — filed as #3504, which is where widening this
 *  to every tone map belongs. Do not widen it here while that site is still red: a tree-wide version
 *  of `no_hairline_ink` fails on `DAY_TONE` today. */
const HAIRLINE_TOKENS = new Set(['--color-outline', '--color-outline-variant'])

/** The one tone this rail does NOT sweep — a MEASURED exclusion with a filed issue, not an omission.
 *
 *  `primary` is genuinely under AA and no token substitution fixes it: swept as a chip ink it is
 *  under 4.5 in **42 of 96** cells (12 schemes × 2 modes × 4 resting tiers at 16%), worst 3.4870
 *  (`rose`/light/canvas, `#d22b6f`), and **every one of the 42 is LIGHT mode** — dark passes 48/48.
 *  `--color-primary-emphasis`, the sibling this file's header points at, only reaches 20/96 (worst
 *  3.8997, the same cell). So the fix is a retune of `--color-primary`'s LIGHT value across all 12
 *  schemes, which lands on every button fill, focus ring (`tokens.css` `:focus-visible`), loader and
 *  glow in the product at once — and `schemes.ts` records that those light values were deliberately
 *  chosen to be "≥4.5:1 as white-text button fill AND as text on white", i.e. against the BARE
 *  ground, which is the same mistake the `--color-info` retune corrected. That is an accent-identity
 *  change, not an ink pick, so it is filed as **#3503** rather than smuggled in here.
 *
 *  🔴 IT IS NOT A LOWERED BAR. The floor stays 4.5 for every tone that is swept; this names the one
 *  tone that is not, so "unswept" is legible instead of invisible. The cardinality pin below means a
 *  SECOND entry has to be argued for in review. Widening the sweep to six tones against a threshold
 *  that accommodated 3.4870 would have been strictly worse than four against the real one: it reads as
 *  broader coverage while asserting less. */
const UNSWEPT_TONES = new Map<string, string>([
  ['primary', '#3503 — 42/96 cells under AA, worst 3.4870 rose/light/canvas, needs a 12-scheme accent retune'],
])

function inks(mode: Mode): Array<[string, string]> {
  const out: Array<[string, string]> = []
  for (const [tone, tok] of Object.entries(PILL_TONES)) {
    if (UNSWEPT_TONES.has(tone)) continue
    if (SCHEME_SUPPLIED.has(tok)) {
      for (const s of SCHEMES) out.push([`${tone}:${s.id}`, s.colors[tok][mode]])
    } else {
      out.push([tone, token(tok.replace('--color-', ''), mode)])
    }
  }
  return out
}

// ═══ Tone COVERAGE — the half of #3493 that was not the ink ══════════════════════════════════════
//
// Closed both ways, because each direction fails differently: an unswept tone ships a defect (what
// happened), and a stale exclusion claims a gap that is no longer there (how the fix rots).
describe('the tone vocabulary is closed: every StatusPill tone is swept or named', () => {
  it('tone_vocabulary: the parsed TONE_VAR keys are exactly the StatusPillTone union', () => {
    // The control on `pillToneInks`'s regex. Without it, an ink that stopped being a plain
    // `var(--color-x)` literal would drop out of the sweep and nothing would say so.
    const union = pillToneUnion()
    expect(union.length, 'members of the StatusPillTone union').toBe(6)
    expect(Object.keys(PILL_TONES).sort(), 'TONE_VAR entries the parser could read').toEqual(union.slice().sort())
    for (const [tone, tok] of Object.entries(PILL_TONES)) {
      expect(tok, `tone \`${tone}\``).toMatch(/^--color-[a-z-]+$/)
    }
  })

  it('tone_coverage: swept ∪ unswept is the whole vocabulary, and unswept is exactly one', () => {
    const swept = Object.keys(PILL_TONES).filter((t) => !UNSWEPT_TONES.has(t))
    expect(swept.sort(), 'tones this file measures against 4.5').toEqual(
      ['danger', 'info', 'neutral', 'ok', 'warn'],
    )
    // Cardinality pinned separately from identity: a second exclusion is a review conversation, and
    // absorbing it into the set comparison above would let one arrive with the first.
    expect([...UNSWEPT_TONES.keys()], 'tones deliberately not swept — see UNSWEPT_TONES').toEqual(['primary'])
    for (const t of UNSWEPT_TONES.keys()) {
      expect(PILL_TONES[t], `\`${t}\` is excluded but is no longer a StatusPill tone — drop the entry`).toBeTruthy()
      expect(UNSWEPT_TONES.get(t)!.length, `\`${t}\`'s exclusion must carry its measurement`).toBeGreaterThan(40)
    }
  })

  it('no_hairline_ink: no tone draws its text in a border/hairline token', () => {
    // The generalization of #3493, and the reason it is a denylist: this is the property the ratio
    // sweep below would also catch, asserted by NAME so the failure message says *why* rather than
    // just how far under. Positive control first — a denylist that matches nothing reads identical
    // to a clean tree (see this file's "write the control that makes the selector fire" siblings).
    expect(HAIRLINE_TOKENS.has('--color-outline-variant'), 'the guard must fire on the #3493 value').toBe(true)
    for (const [tone, tok] of Object.entries(PILL_TONES)) {
      expect(HAIRLINE_TOKENS.has(tok),
        `tone \`${tone}\` draws text in ${tok}, a border/hairline token. A hairline is designed to be ` +
        `barely separable from its surface, so no choice of ground and no pinned compositing base can ` +
        `make it readable — pick an ink-ramp value (\`--color-on-surface-low\` is \`neutral\`'s).`).toBe(false)
    }
  })

  it('hairline_unreadable: the denylisted values are inadmissible as ink, and outline-variant on every tier', () => {
    // The denylist's own justification, so it cannot rot into a superstition — and the in-test
    // vacuity floor for `no_hairline_ink`, which would pass just as happily if these tokens were
    // fine. Inverted on purpose: these cells must FAIL.
    //
    // 🪤 THE FIRST DRAFT ASSERTED "every cell under 4.5" AND ITS OWN CONTROL DISPROVED IT:
    // `--color-outline` (`#8e918f`, the SAME value in both modes) reads **4.8936** on the dark canvas
    // and clears AA there, while failing the other six cells (2.4891 worst, light canvas). That is
    // worth more than the tidier claim, because it is the exact trap the sweep exists for — a token
    // checked against one ground looks admissible, and the ground a chip lands on is not knowable at
    // the call site. So the predicate is admissibility: a pill ink must clear 4.5 on ALL eight cells,
    // and a token with even one failing cell cannot be one.
    const failing = new Map<string, number>()
    for (const tok of HAIRLINE_TOKENS) {
      let under = 0
      for (const mode of ['dark', 'light'] as Mode[]) {
        for (const ground of RESTING) {
          const ink = token(tok.replace('--color-', ''), mode)
          if (tintedChipRatio(ink, token(ground, mode), 16) < aaFloor(12, false)) under++
        }
      }
      failing.set(tok, under)
    }
    const detail = [...failing].map(([t, n]) => `${t} ${n}/8`).join(', ')
    for (const [tok, under] of failing) {
      expect(under, `${tok} now clears AA on all 8 resting cells (${detail}), so it is admissible as a ` +
        `pill ink — either it stopped being a hairline (drop it from HAIRLINE_TOKENS and sweep it as a ` +
        `tone) or a ground moved`).toBeGreaterThan(0)
    }
    // The load-bearing half of #3493's claim, asserted separately: for `--color-outline-variant` it is
    // not "some grounds are bad", it is that NO ground rescues it — every tier, both modes, 1.14–1.89.
    expect(failing.get('--color-outline-variant'),
      `#3493's "no choice of ground fixes it" — every resting cell must be under AA (${detail})`).toBe(8)
  })
})

// ═══ Tier 1 — every strength up to 16%, on every resting tier, in every scheme, in both modes ════
//
// 640 cells (was 600 before `neutral` joined the sweep — 4 global tones + 12 `info:*` inks × 4 tiers
// × 5 strengths × 2 modes). Worst: 4.5200 (`danger` dark, surface-container, 16%); `neutral`'s worst
// is 4.5472 on the same cell. With the previous `info` values, 171 of the then-600 were under 4.5 and
// EVERY ONE was an `info:*` cell — which is the evidence that that diagnosis (only the scheme-retinted
// tone drifted) and its fix were the same size as the defect. `neutral` is the same shape of finding
// one tone over: 8 of its 8 sixteen-percent cells were under AA and all 8 are now clear.
describe('status-chip tone over its own ≤16% tint clears AA on every resting tier, every scheme', () => {
  it('has the full curated scheme set (a sweep over an empty list passes forever)', () => {
    expect(SCHEMES.length, 'curated schemes').toBeGreaterThanOrEqual(12)
    expect(SCHEMES.every((s) => /^#[0-9a-f]{6}$/i.test(s.colors['--color-info'].dark))).toBe(true)
    expect(SCHEMES.every((s) => /^#[0-9a-f]{6}$/i.test(s.colors['--color-info'].light))).toBe(true)
  })

  it('sweeps every ink the derived vocabulary yields (the cell count, not the tone count)', () => {
    // The tone list is now parsed rather than written (see `pillToneInks`), so the population floor
    // moves here: four global tones + one `info:*` ink per scheme. A parse that silently read five
    // tones instead of six would leave `tone_vocabulary` red AND this count short, and the pair names
    // which half broke.
    for (const mode of ['dark', 'light'] as Mode[]) {
      const list = inks(mode)
      expect(list.length, `${mode} inks swept`).toBe(4 + SCHEMES.length)
      expect(list.map(([n]) => n), `${mode} must include the tone #3493 added`).toContain('neutral')
      expect(list.every(([, ink]) => /^#[0-9a-f]{6}$/i.test(ink)), `${mode} inks are all hex`).toBe(true)
    }
  })

  it('reads real, per-mode ground values out of tokens.css', () => {
    for (const g of RESTING) {
      expect(token(g, 'dark'), `${g} dark`).toMatch(/^#[0-9a-f]{6}$/i)
      expect(token(g, 'dark'), `${g} must differ per mode`).not.toBe(token(g, 'light'))
    }
  })

  it('the three global tones are the same values their aliases resolve to', () => {
    // So sweeping `ok`/`warn`/`danger` genuinely covers the `success`/`warning`/`error` spellings
    // that 15 of the literal-tone sites use. Asserted on the DARK block only, and that is the
    // point: the aliases are declared ONCE as `var()` references, so they follow whichever mode's
    // value is live and there is nothing per-mode to check. A `.light` redeclaration would be the
    // bug — it would let one spelling of one tone diverge from the other in exactly one mode.
    for (const [alias, canon] of [['success', 'ok'], ['warning', 'warn'], ['error', 'danger']]) {
      expect(BLOCKS.dark, `--color-${alias}`).toMatch(
        new RegExp(`--color-${alias}:\\s*var\\(--color-${canon}\\)`),
      )
      expect(BLOCKS.light, `--color-${alias} must not be redeclared in .light`).not.toMatch(
        new RegExp(`--color-${alias}:`),
      )
    }
  })

  for (const mode of ['dark', 'light'] as Mode[]) {
    for (const ground of RESTING) {
      for (const pct of [8, 10, 12, 14, 16]) {
        it(`${mode}: ≥AA on ${ground} at ${pct}%`, () => {
          for (const [name, ink] of inks(mode)) {
            const r = tintedChipRatio(ink, token(ground, mode), pct)
            // 12px and 13px chips only — never the 3:1 large-text relief. `aaFloor` is asserted
            // against the real rendered sizes further down.
            expect(r, `${name} (${ink}) on its own ${pct}% tint over ${ground} ${mode} = ${r.toFixed(4)}`)
              .toBeGreaterThanOrEqual(aaFloor(12, false))
          }
        })
      }
    }
  }
})

// ═══ Tier 2 — 18%, the strongest strength in the tree, for the token this cycle retuned ══════════
//
// 96 cells (12 schemes × 2 modes × 4 tiers). Worst after the fix: 4.5528 (rose/light on the canvas).
// Before it, **48 of the 96** were under AA — the 13px `sm.tone` chip in `tasks/TaskDetail.tsx`,
// worst 3.3253 (slate/light on the canvas). Counted the other way, by scheme × mode rather than by
// cell, 16 of the 24 combos failed on at least one resting tier. `info` is swept on ALL FOUR tiers at
// this strength because the retune targeted the worst tier of each mode, so it holds on every tier by
// construction.
//
// 🔴 `ok`/`warn`/`danger` ARE DELIBERATELY NOT SWEPT AT 18%, AND THAT IS AN UNFIXED FINDING, NOT AN
// OVERSIGHT. Measured, they miss AA at that strength on three of the four tiers:
//
//     danger  dark  surface-container  4.3543      ok      light canvas       4.4313
//     danger  light canvas             4.4029      warn    light surface-low  4.4823
//     warn    light canvas             4.4198      danger  light surface-low  4.4767
//
// Four 12px text sites are at 18% with one of those tones — `artifacts/ArtifactViewer.tsx:253`
// ("source changed"), `prompts/VariableRow.tsx:40` (the required toggle),
// `skills/MarketplaceDetail.tsx:80` (a scan-severity badge) and `ChatPage.tsx:4338` (which uses
// `--color-secondary`, a token with NO `.light` value at all — it inherits the dark amber). Whether
// each is a live violation depends on the tier it rests on, and none of the four was reachable with
// data on this cycle's seeded home, so the honest position is: the cells are recorded, the sweep does
// not claim them, and the ceiling ratchet below stops a FIFTH appearing. Moving `ok`/`warn`/`danger`
// is also a far wider blast radius than moving `info`: they are global values, so a nudge lands on
// every error surface, every progress bar and every icon in the product at once, and
// `errorTreatments.test.ts` holds `--color-danger` to its own bar.
describe('the 18% chip clears AA on every resting tier, every scheme (the retuned token)', () => {
  for (const mode of ['dark', 'light'] as Mode[]) {
    for (const ground of RESTING) {
      it(`${mode}: info ≥AA on ${ground} at 18%`, () => {
        for (const s of SCHEMES) {
          const ink = s.colors['--color-info'][mode]
          const r = tintedChipRatio(ink, token(ground, mode), 18)
          expect(r, `info:${s.id} (${ink}) on its own 18% tint over ${ground} ${mode} = ${r.toFixed(4)}`)
            .toBeGreaterThanOrEqual(aaFloor(13, false))
        }
      })
    }
  }
})

// ═══ The size threshold is explicit, and no chip in this family can claim large-text relief ══════
describe('the applicable floor is 4.5, derived from the size, not assumed', () => {
  it('aaFloor implements SC 1.4.3 large text exactly', () => {
    expect(aaFloor(12, false)).toBe(4.5)
    expect(aaFloor(13, false)).toBe(4.5)
    expect(aaFloor(12, true), 'bold does not make 12px large').toBe(4.5)
    expect(aaFloor(18.65, true), 'just under the bold threshold').toBe(4.5)
    expect(aaFloor(18.66, true), '≥18.66px BOLD is large').toBe(3)
    expect(aaFloor(18.66, false), '≥18.66px at normal weight is NOT large').toBe(4.5)
    expect(aaFloor(23.9, false)).toBe(4.5)
    expect(aaFloor(24, false), '≥24px at any weight is large').toBe(3)
  })

  it('every type size declared on a chip in this family is under the large-text threshold', () => {
    // Read the size off the site rather than pinning it: a chip that grows past 18.66px would be
    // allowed 3:1 and this rail must notice rather than keep asserting 4.5 by assumption.
    const sizes: Array<{ site: string; px: number }> = []
    for (const s of SITES) {
      const src = readFileSync(join(SRC, s.file), 'utf8').split('\n')
      const window = [src[s.line - 2] ?? '', src[s.line - 1] ?? ''].join(' ')
      for (const m of window.matchAll(/text-\[([\d.]+)(rem|px)\]/g)) {
        sizes.push({ site: `${s.file}:${s.line}`, px: Number(m[1]) * (m[2] === 'rem' ? 16 : 1) })
      }
    }
    expect(sizes.length, 'chip sites that declare an explicit type size').toBeGreaterThanOrEqual(15)
    const large = sizes.filter((s) => s.px >= 18.66)
    expect(large, `these could claim large-text relief and the sweep above assumes they cannot:\n${
      large.map((s) => `${s.site} — ${s.px}px`).join('\n')}`).toEqual([])
  })
})

// ═══ The ratchet, written from the other side ════════════════════════════════════════════════════
describe('the tone-as-ink-and-tint family stays inside the swept envelope', () => {
  it('the census is not vacuously empty', () => {
    // Pinned near the measured 88 so a parser regression (or a call site rewritten past the regex)
    // cannot turn this whole file into a sweep over nothing.
    expect(SITES.length, 'tone-as-ink-and-background-tint sites').toBeGreaterThanOrEqual(80)
    expect(SITES.filter((s) => s.literal).length, 'literal-tone sites').toBeGreaterThanOrEqual(60)
    expect(SITES.filter((s) => !s.literal).length, 'registry-tone sites').toBeGreaterThanOrEqual(20)
    // The flagship, by file and line, so a move that silently drops it from the family is caught.
    expect(SITES.some((s) => s.file === 'pages/tasks/TasksListPage.tsx' && s.pct === 16)).toBe(true)
    expect(SITES.some((s) => s.file === 'pages/tasks/TaskDetail.tsx' && s.pct === 18)).toBe(true)
  })

  /** The identity of a site, for the two pins below: FILE + TONE EXPRESSION + STRENGTH, and
   *  deliberately NOT the line number.
   *
   *  🪤 A LINE-NUMBER PIN IN A RATCHET IS A RAIL THAT REDS ON SOMEBODY ELSE'S DIFF. This file was
   *  first written pinning `file:line`, and rebasing it onto a `main` that had merely SHORTENED
   *  `ChatPage.tsx` reddened both pins below — 4338→4266, 4361→4289, 4496→4424 — with the chips
   *  themselves untouched. That is the failure mode where the next contributor deletes the
   *  assertion instead of reading it. Keying on the tone expression is strictly no weaker in the
   *  dimension that matters: it still distinguishes the TWO 18% chips inside `ChatPage.tsx` from
   *  each other (`--color-secondary` vs the tag colour), and the cardinality pin below closes the
   *  case where a new chip copies an existing file+tone pair exactly. */
  const key = (s: Site) => `${s.file} @${s.pct}% ${s.tone.replace(/\s+/g, ' ').trim()}`

  // The two sites above 18% are named, with the reason each is out of the ink budget rather than
  // simply unfixed. A THIRD one reds this test and has to be argued for.
  const ABOVE_CEILING = new Set([
    // A knowledge-TYPE hue on a selected filter chip. `KnowledgeListPage.tsx` already carries the
    // ruling beside it: no `<tone>-container` sibling exists, the coral container would be visually
    // wrong, and "if one ever fails, it needs its own container value, not a guess made from this
    // one". Respected, not re-litigated.
    'pages/knowledge/KnowledgeListPage.tsx @20% tone',
    // A user-chosen chat-tag colour (`t.color`). The tone is USER DATA, so no token value can
    // constrain it — this one needs a contrast-aware ink picked at render time, which is a
    // different change from a scheme retune.
    "pages/ChatPage.tsx @22% t.color || 'var(--color-primary)",
  ])

  it('no chip tints above the 18% ceiling these tiers were swept at', () => {
    const over = SITES.filter((s) => s.pct > 18).map(key)
    const unexpected = over.filter((s) => !ABOVE_CEILING.has(s))
    expect(unexpected, `above the swept ceiling with no recorded reason:\n${unexpected.join('\n')}\n` +
      `Sweep that strength in this file first, or use one that is already swept (≤16% on any resting ` +
      `tier, 18% on --color-surface).`).toEqual([])
    expect(over.length, 'the two recorded exceptions still exist (or this allowance is stale)').toBe(ABOVE_CEILING.size)
  })

  it('the 18% population is exactly the eight sites whose tones were reasoned about', () => {
    // The pin is the point. `info` clears 18% on every tier; `ok`/`warn`/`danger` do NOT (see the
    // tier-2 header for the six failing cells), and `--color-secondary` has no light value at all.
    // So a NINTH 18% chip is not a cosmetic addition — it is a contrast question, and it reds here
    // until someone measures the tier it rests on and either sweeps that cell or drops to ≤16%.
    const at18 = SITES.filter((s) => s.pct === 18)
    expect(at18.map(key).sort(), 'a new 18% chip needs its resting tier measured before it can be added here')
      .toEqual([
        // --color-secondary — a token with NO `.light` value at all (see tier 2)
        'pages/ChatPage.tsx @18% var(--color-secondary)',
        // a user-chosen tag colour, else --color-primary
        "pages/ChatPage.tsx @18% tagById[tid].color || 'var(--color-primary)",
        'pages/artifacts/ArtifactViewer.tsx @18% var(--color-warning)',   // 12px text
        "pages/loops/LoopCockpitPage.tsx @18% verdict.done ? 'var(--color-ok)' : 'var(--color-primary)",
        'pages/prompts/VariableRow.tsx @18% var(--color-danger)',          // 12px text
        // --color-danger / --color-warning, 12px text
        "pages/skills/MarketplaceDetail.tsx @18% var(--color-${f.severity === 'dangerous' ? 'danger' : 'warning'})",
        'pages/tasks/TaskDetail.tsx @18% sm.tone',   // the status chip — `info` here, 13px, swept above
        'ui/UpdateProgressOverlay.tsx @18% var(--color-success)', // a size-6 GLYPH badge: 3:1, 4.91
      ].sort())
    // Cardinality, separately: a ninth chip that duplicated an existing file+tone+strength triple
    // exactly would be absorbed by the set comparison above and must not be.
    expect(at18.length, '18% chip sites').toBe(8)
  })
})

// ═══ The three declarations of --color-info agree ════════════════════════════════════════════════
//
// `schemes.ts` says "Light primary/emphasis/info mirror tokenRegistry's AA-verified shades — keep in
// sync", and nothing enforced it. The value now exists three times: the `tokens.css` fallback, the
// `coral` scheme (the default), and `tokenRegistry`'s Appearance-panel default. A fix applied to two
// of the three ships a chip whose contrast depends on which one the user's stored overrides came
// from — which is precisely the failure mode `--color-on-primary-tint` was written to end.
describe('--color-info is declared once, in three places that must agree', () => {
  const coral = SCHEMES.find((s) => s.id === 'coral')!
  it('tokens.css matches the coral scheme', () => {
    expect(token('info', 'dark')).toBe(coral.colors['--color-info'].dark)
    expect(token('info', 'light')).toBe(coral.colors['--color-info'].light)
  })
  it('tokenRegistry matches the coral scheme', () => {
    const reg = readFileSync(join(SRC, 'design/tokenRegistry.ts'), 'utf8')
    const m = reg.match(/c\('--color-info',[^)]*'(#[0-9a-fA-F]{6})',\s*'(#[0-9a-fA-F]{6})'\)/)
    expect(m, "tokenRegistry declares --color-info with two hex values").toBeTruthy()
    expect(m![1]).toBe(coral.colors['--color-info'].dark)
    expect(m![2]).toBe(coral.colors['--color-info'].light)
  })
})

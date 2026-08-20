/**
 * FLUID-MOTION atom FM-7 — the reduced-motion off-switch as an APP-WIDE property.
 *
 * Seven `*.reducedMotion.test.tsx` files already ship, and each proves ONE component takes an
 * instant branch. `motion.test.ts` proves the four `physics` presets collapse — from a HAND
 * LIST (`const PRESETS = ['snappy','smooth','fluid','playful']`). Neither shape can answer the
 * question FM-7 actually asks: **does anything, anywhere, still animate when the user asked the
 * platform for less motion?** A per-component test says nothing about the component added next
 * week; a hand list says nothing about the transition family added next quarter.
 *
 * So this file asserts the app-wide property in the only two ways the unit tier can:
 *
 *   1. **Reflection over the whole export surface of `design/motion.ts`.** Every export is
 *      reached programmatically — objects walked, getters read, functions invoked through an
 *      argument ladder, variant resolvers called — twice: once with the media query answering
 *      NO and once answering YES. Nothing is named for coverage, so a new export is covered the
 *      moment it exists (and an export the ladder cannot invoke fails LOUDLY rather than being
 *      skipped). The measured defect this was written against: `physics.*` routes through
 *      `bouncy()` and collapses, while the sibling family `export const spring = {...}` is a
 *      static object literal whose three spatial members carry `type: 'spring'` unconditionally
 *      and are used by 61 non-test modules.
 *   2. **A source census** for spring parameters minted outside that module, so a component
 *      cannot re-create by hand what the module gates.
 *
 * ── What this file CANNOT prove, and nobody should read a green here as ──────────────────────
 * jsdom runs no animations, computes no layout, paints nothing and has no frame clock. So this
 * rail cannot observe that an element actually stayed still, cannot measure a frame rate or a
 * dropped frame, cannot evaluate the `@media (prefers-reduced-motion: reduce)` block in
 * `tokens.css`, cannot check that framer-motion honours the transition it was handed, and
 * cannot see motion that originates in CSS keyframes, Web Animations, canvas or WebGL. It
 * proves the VALUES the app hands the animation layer, and the absence of hand-rolled springs
 * in source. The browser-level measurement is a separate atom.
 *
 * Both scans below are TEXT scans over source, and comments are not stripped by grep or by
 * `readFileSync`. Naming a spring parameter in prose would otherwise enrol the file doing the
 * naming — including this one. `blankComments()` blanks every comment (preserving line
 * structure) before either regex runs, and `describe('census self-checks')` proves the blanking
 * is load-bearing rather than decorative.
 */

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

// ─────────────────────────────────────────────────────────────────────────────
// 1. The media-query stub — installed at MODULE SCOPE, before `motion.ts` is imported.
//
// `src/test/setup.ts` installs a matchMedia only when jsdom has none, so this
// `defineProperty` wins. It is deliberately FLIPPABLE (a `let`, read inside the stub) rather
// than fixed: the whole rail is a comparison of the same access paths under both answers, and a
// fixed stub would need two files that could drift apart. Flipping is only safe because
// `motion.ts` reads the query at CALL time through `prefersReducedMotion()` and caches nothing
// — `stubTakesEffect` below is the positive control that proves the flip actually lands.
// ─────────────────────────────────────────────────────────────────────────────

let REDUCED = false

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) =>
    ({
      matches: REDUCED && query.includes('prefers-reduced-motion'),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
      onchange: null,
    }) as unknown as MediaQueryList,
})

const motionModule = (await import('./motion')) as unknown as Record<string, unknown>

// ─────────────────────────────────────────────────────────────────────────────
// 2. Reflection over the export surface
// ─────────────────────────────────────────────────────────────────────────────

type SpringKind = 'explicit' | 'inferred'

/** The parameters framer-motion reads as spring physics. With NO explicit `type`, any one of
 *  these is enough for framer to infer a spring — which is why `instant` states
 *  `type: 'tween'` outright and why an untyped object carrying one of these counts here. */
const SPRING_PARAM_KEYS = ['stiffness', 'damping', 'mass', 'bounce', 'restSpeed', 'restDelta'] as const

function springKind(value: unknown): SpringKind | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null
  const o = value as Record<string, unknown>
  if (o['type'] === 'spring') return 'explicit'
  if (o['type'] === undefined && SPRING_PARAM_KEYS.some((k) => typeof o[k] === 'number')) return 'inferred'
  return null
}

const NOOP = (): void => {}

/** The argument ladder. Every exported function is called with each tuple; the ones that throw
 *  are discarded and the ones that return are walked. This is what makes the enumeration
 *  hand-list-free: a new `export function` is probed automatically. `[NOOP]` exists for
 *  callback-taking exports (`viewTransition`), the number pairs for threshold/velocity exports
 *  (`swipeDismiss`, `expr`) so BOTH sides of an internal branch get reached. */
const ARG_TUPLES: readonly unknown[][] = [[], [NOOP], [0], [0, 0], [1, 1], [1e6, 1e6], [0.5, 0.5]]

const MAX_DEPTH = 6

interface Probe {
  /** every object-shaped value reached, keyed by the access path that reached it */
  values: Map<string, unknown>
  /** exports for which EVERY tuple threw — the ladder needs extending, fail closed */
  uninvokable: string[]
  /** nested functions (variant resolvers) that threw when resolved */
  nestedFailures: string[]
  exportNames: string[]
}

function visit(value: unknown, path: string, out: Probe, ancestors: readonly object[], depth: number): void {
  if (depth > MAX_DEPTH) return
  if (typeof value === 'function') {
    if (value === NOOP) return
    let resolved: unknown
    try {
      resolved = (value as () => unknown)()
    } catch {
      out.nestedFailures.push(path)
      return
    }
    visit(resolved, `${path}()`, out, ancestors, depth + 1)
    return
  }
  if (value === null || typeof value !== 'object') return
  if (ancestors.includes(value as object)) return
  out.values.set(path, value)
  const next = [...ancestors, value as object]
  if (Array.isArray(value)) {
    value.forEach((v, i) => visit(v, `${path}[${i}]`, out, next, depth + 1))
    return
  }
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    visit(v, `${path}.${k}`, out, next, depth + 1)
  }
}

function probe(reduced: boolean): Probe {
  REDUCED = reduced
  const out: Probe = { values: new Map(), uninvokable: [], nestedFailures: [], exportNames: [] }
  for (const [name, value] of Object.entries(motionModule)) {
    out.exportNames.push(name)
    if (typeof value === 'function') {
      let invoked = 0
      ARG_TUPLES.forEach((args, i) => {
        let result: unknown
        try {
          result = (value as (...a: unknown[]) => unknown)(...args)
        } catch {
          return
        }
        invoked += 1
        visit(result, `${name}(#${i})`, out, [], 0)
      })
      if (invoked === 0) out.uninvokable.push(name)
    } else {
      visit(value, name, out, [], 0)
    }
  }
  return out
}

function springPaths(p: Probe): string[] {
  return [...p.values.entries()]
    .filter(([, v]) => springKind(v) !== null)
    .map(([path]) => path)
    .sort()
}

const ALLOWED = probe(false)
const REDUCED_PROBE = probe(true)

describe('reduced motion is an app-wide property of design/motion.ts, not a per-preset one', () => {
  it('reaches every export — nothing is skipped, and a new export cannot hide', () => {
    // Vacuity floor #1: an enumeration that reached nothing would satisfy every
    // "no springs under reduced motion" assertion below for free.
    expect(
      ALLOWED.exportNames.length,
      `Only ${ALLOWED.exportNames.length} exports were enumerated from design/motion.ts. ` +
        `That is too few to be the real module — the import or the reflection is broken.`,
    ).toBeGreaterThanOrEqual(15)

    expect(
      ALLOWED.uninvokable,
      `These exports threw for EVERY argument tuple, so nothing about them was measured. ` +
        `Add a tuple to ARG_TUPLES that satisfies them — do not leave them unprobed.`,
    ).toEqual([])

    expect(
      ALLOWED.nestedFailures.concat(REDUCED_PROBE.nestedFailures),
      'A nested function (variant resolver) threw while being resolved, so its transition was never inspected.',
    ).toEqual([])

    // The two passes must cover the same surface, or a path-paired comparison is meaningless.
    expect(REDUCED_PROBE.exportNames).toEqual(ALLOWED.exportNames)
  })

  it('the media-query stub actually takes — both passes measure what they claim', () => {
    const prefersReducedMotion = motionModule['prefersReducedMotion'] as () => boolean
    REDUCED = true
    expect(prefersReducedMotion()).toBe(true)
    REDUCED = false
    expect(prefersReducedMotion()).toBe(false)
  })

  it('finds real springs when motion is ALLOWED — the vacuity floor for the assertion below', () => {
    const found = springPaths(ALLOWED)
    // Vacuity floor #2: if the walker cannot see a spring at all, "no springs under reduced
    // motion" is a tautology. It must find several, and it must find a KNOWN one.
    expect(
      found.length,
      `The walker found ${found.length} spring-shaped transitions with motion allowed. ` +
        `It must find several, or it is not actually reading the module's transitions.`,
    ).toBeGreaterThanOrEqual(6)
    expect(found, 'Known-positive sample: the raw spatial tier must be visible to the walker.').toContain(
      'spring.spatialFast',
    )
    expect(
      found.some((p) => p.startsWith('physics.')),
      'Known-positive sample: the named physics presets must be visible to the walker.',
    ).toBe(true)
  })

  it('NO export yields a spring under prefers-reduced-motion — every family, not just the gated one', () => {
    const offenders = springPaths(REDUCED_PROBE).map((path) => `${path} (${springKind(REDUCED_PROBE.values.get(path))})`)
    expect(
      offenders,
      `These transitions exported by design/motion.ts still animate as springs when the user ` +
        `asked for reduced motion:\n  ${offenders.join('\n  ')}\n` +
        `A transition family must route through the same gate as physics.* — being a static ` +
        `object literal instead of a getter is exactly how a family escapes the off-switch.`,
    ).toEqual([])
  })

  it('every collapsed spring states a non-spring TYPE, so a leftover stiffness cannot re-infer one', () => {
    // Real call sites spread a preset and override one field:
    //   { ...physics.fluid, stiffness: 240 }
    // If the collapsed value carried no explicit `type`, that leftover parameter would let
    // framer infer a spring again and the collapse would leak straight back through the spread.
    // Scoped to paths that WERE springs with motion allowed: those are the only paths where a
    // collapse happens, so this cannot be satisfied by a path that never animated.
    const leaky: string[] = []
    for (const path of springPaths(ALLOWED)) {
      if (!REDUCED_PROBE.values.has(path)) continue // absent = no transition at all, the strongest answer
      const collapsed = REDUCED_PROBE.values.get(path) as Record<string, unknown>
      const type = collapsed['type']
      if (typeof type !== 'string' || type === 'spring') {
        leaky.push(`${path} -> type=${JSON.stringify(type)}`)
      }
    }
    expect(
      leaky,
      `These reduced-motion transitions carry no explicit non-spring \`type\`, so a call site ` +
        `spreading a leftover spring parameter onto them re-infers a spring:\n  ${leaky.join('\n  ')}`,
    ).toEqual([])
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 3. The source census — no module outside design/motion.ts mints spring physics
// ─────────────────────────────────────────────────────────────────────────────

const CWD = process.cwd()
const SRC_ROOT = join(CWD, 'src')
/** The one sanctioned home for spring parameters. Excluded from the offender lists and used as
 *  the census's known-positive control. */
const MOTION_MODULE = 'src/design/motion.ts'

/** Every non-test `.ts`/`.tsx` under a root, as cwd-relative POSIX paths.
 *
 *  A missing root yields `[]` rather than throwing, which is deliberate: it makes the corpus
 *  floor below the thing that catches a mistyped root, and `census self-checks` proves that
 *  floor fires. Test files are excluded because a test legitimately constructs a spring in
 *  order to assert something about it. */
function collectSources(root: string): string[] {
  const out: string[] = []
  // The catch is what turns a missing root into an empty corpus instead of a throw. Inlined as
  // its own function so the `Dirent` element type stays inferred rather than annotated (the
  // Buffer/string overload pair makes an explicit annotation version-fragile).
  const readSafe = (dir: string) => {
    try {
      return readdirSync(dir, { withFileTypes: true })
    } catch {
      return []
    }
  }
  const walkDir = (dir: string): void => {
    for (const e of readSafe(dir)) {
      const full = join(dir, e.name)
      if (e.isDirectory()) {
        if (e.name === 'node_modules' || e.name === 'dist') continue
        walkDir(full)
        continue
      }
      if (!/\.tsx?$/.test(e.name)) continue
      if (/\.(test|spec)\.tsx?$/.test(e.name)) continue
      out.push(full.slice(CWD.length + 1).split('\\').join('/'))
    }
  }
  walkDir(root)
  return out.sort()
}

/** Blank every comment, PRESERVING line structure so reported line numbers stay true.
 *
 *  Load-bearing, not hygiene: `ui/motion/vocabulary.ts` documents the spread hazard in prose
 *  and `design/motion.ts` documents its whole gate in prose. Scanning raw text counts those
 *  sentences as code. The `[^:]` guard on the line-comment arm keeps `https://` from being read
 *  as the start of a comment. */
function blankComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:])\/\/[^\n]*/g, (m, lead: string) => lead + ' '.repeat(m.length - lead.length))
}

/** A literal spring transition: `type: 'spring'`. Nothing outside the module may write one. */
const LITERAL_SPRING = /\btype\s*:\s*['"]spring['"]/g
/** Bare spring physics in a transition. Without an explicit `type` framer infers a spring from
 *  any of these, so they are the second way to mint one. `\s*:` keeps `stiffnessBonus:` — a
 *  named constant, not a transition field — out of the count. */
const SPRING_PARAM = /\b(?:stiffness|damping|bounce)\s*:/g

interface Hit {
  file: string
  line: number
  text: string
}

function scan(files: string[], pattern: RegExp): Hit[] {
  const hits: Hit[] = []
  for (const file of files) {
    let raw: string
    try {
      raw = readFileSync(join(CWD, file), 'utf8')
    } catch {
      continue
    }
    blankComments(raw)
      .split('\n')
      .forEach((line, i) => {
        if (new RegExp(pattern.source).test(line)) {
          hits.push({ file, line: i + 1, text: raw.split('\n')[i].trim() })
        }
      })
  }
  return hits
}

function tally(hits: Hit[]): string[] {
  const counts = new Map<string, number>()
  for (const h of hits) counts.set(h.file, (counts.get(h.file) ?? 0) + 1)
  return [...counts.entries()].map(([f, n]) => `${f}:${n}`).sort()
}

const SOURCES = collectSources(SRC_ROOT)
const OUTSIDE_MODULE = SOURCES.filter((f) => f !== MOTION_MODULE)

/** Shrink-only baseline: files outside `design/motion.ts` that write bare spring physics.
 *
 *  Every entry here spreads an ALREADY-GATED preset and re-tunes one number
 *  (`{ ...physics.fluid, stiffness: N }`), which is safe precisely because the gated preset
 *  collapses to `instant` with an explicit `type: 'tween'` — see the reflection assertion
 *  above, which is what keeps that true. Compared with `toEqual`, not `<=`, so BOTH directions
 *  are a red: a new site fails until it is reviewed and listed, and refactoring a site away
 *  fails until its entry is deleted in the same commit. A permissive allowlist would let the
 *  second case rot silently. */
const SPRING_PARAM_BASELINE: readonly string[] = [
  'src/ui/ComposerStage.tsx:1',
  'src/ui/SidePanel.tsx:1',
  'src/ui/motion/vocabulary.ts:1',
]

describe('no module outside design/motion.ts mints spring physics', () => {
  it('writes no literal spring transition', () => {
    const offenders = scan(OUTSIDE_MODULE, LITERAL_SPRING).map((h) => `${h.file}:${h.line}  ${h.text}`)
    expect(
      offenders,
      `A literal spring transition was written outside design/motion.ts:\n  ${offenders.join('\n  ')}\n` +
        `Springs are minted in ONE place so ONE gate can zero them. Use a physics.* preset.`,
    ).toEqual([])
  })

  it('mints bare spring physics only at the reviewed sites (shrink-only baseline)', () => {
    expect(
      tally(scan(OUTSIDE_MODULE, SPRING_PARAM)),
      `The set of modules writing bare spring physics changed. A NEW entry must be reviewed: it ` +
        `is only safe if it spreads a gated preset rather than building a spring from scratch. ` +
        `A REMOVED entry must be deleted from SPRING_PARAM_BASELINE in the same commit.`,
    ).toEqual([...SPRING_PARAM_BASELINE])
  })

  it('every baselined site re-tunes a gated preset rather than building a spring', () => {
    // This is what closes the loop with the literal-spring rail: a spread cannot produce a
    // spring unless its SOURCE is one, and the only place a spring may be minted is the module.
    const fromScratch = scan(OUTSIDE_MODULE, SPRING_PARAM)
      .filter((h) => !h.text.includes('...'))
      .map((h) => `${h.file}:${h.line}  ${h.text}`)
    expect(
      fromScratch,
      `These sites write spring physics WITHOUT spreading a gated preset, so nothing collapses ` +
        `them under reduced motion:\n  ${fromScratch.join('\n  ')}`,
    ).toEqual([])
  })
})

describe('census self-checks — the floors that stop a silent pass', () => {
  it('scanned a non-trivial corpus, including known files', () => {
    // Vacuity floor #3: a glob matching nothing passes every "no offenders" assertion above.
    expect(
      SOURCES.length,
      `The census corpus is ${SOURCES.length} files. A mistyped root yields an empty corpus, ` +
        `and an empty corpus passes every offender assertion in this file.`,
    ).toBeGreaterThanOrEqual(300)
    expect(SOURCES).toContain(MOTION_MODULE)
    expect(SOURCES).toContain('src/ui/SidePanel.tsx')
    expect(SOURCES.some((f) => /\.test\.tsx?$/.test(f)), 'test files must be excluded').toBe(false)
  })

  it('an absent root yields an empty corpus, which the floor above rejects', () => {
    // Proves the floor is load-bearing rather than incidentally true.
    expect(collectSources(join(CWD, 'src-does-not-exist'))).toEqual([])
    expect(collectSources(join(CWD, 'src-does-not-exist')).length).toBeLessThan(300)
  })

  it('both patterns match known-positive code in the sanctioned module', () => {
    // Vacuity floor #4: a broken regex finds no offenders anywhere. motion.ts is excluded from
    // the offender lists precisely because it is full of legitimate matches, which makes it the
    // ideal positive control.
    const literal = scan([MOTION_MODULE], LITERAL_SPRING)
    const params = scan([MOTION_MODULE], SPRING_PARAM)
    expect(literal.length, 'LITERAL_SPRING must match the module that mints springs').toBeGreaterThanOrEqual(3)
    expect(params.length, 'SPRING_PARAM must match the module that mints springs').toBeGreaterThanOrEqual(3)
  })

  it('comment blanking removes prose that would otherwise be counted as code', () => {
    // ui/motion/vocabulary.ts documents the spread hazard in a docstring. Raw, those sentences
    // are indistinguishable from the one real occurrence on the return line.
    const raw = readFileSync(join(CWD, 'src/ui/motion/vocabulary.ts'), 'utf8')
    const rawHits = raw.split('\n').filter((l) => new RegExp(SPRING_PARAM.source).test(l)).length
    const blankedHits = blankComments(raw).split('\n').filter((l) => new RegExp(SPRING_PARAM.source).test(l)).length
    expect(rawHits, 'the control file must carry commented mentions, or it proves nothing').toBeGreaterThan(1)
    expect(blankedHits, 'blanking must remove the commented mentions').toBeLessThan(rawHits)
    expect(blankedHits).toBe(1)
  })

  it('blanking preserves line numbering and does not swallow code after a URL', () => {
    const src = ['const a = 1 // stiffness: 9', 'const u = "https://x" // c', '/* stiffness: 9', ' */', 'const b = 2']
      .join('\n')
    const blanked = blankComments(src)
    expect(blanked.split('\n')).toHaveLength(5)
    expect(new RegExp(SPRING_PARAM.source).test(blanked)).toBe(false)
    expect(blanked.split('\n')[1]).toContain('https://x')
    expect(blanked.split('\n')[4]).toBe('const b = 2')
  })
})

// ── A getter read at MODULE SCOPE freezes the gate for the session ────────────────────────
//
// 🔴 The defect class this closes, found twice while building FM-7 and fixed both times.
// The gated presets are GETTERS: they read the media query at property-access time. Held in a
// module-scope object literal, that access happens ONCE at import, so the answer is frozen for
// the whole session and a user who enables reduced motion mid-session keeps the spring.
// `design/motion.ts`'s own `overlayEnter.exit` carried it, and so did
// `ui/chat/MessageUser.tsx`'s `travelEnter`. Both are functions now. Two instances in one atom
// is a pattern, not a coincidence, so it gets a rail.
//
// 🪤 Scope by BRACE BALANCE, never by a line window. A 14-line window written while
// investigating this reported `ui/SearchField.tsx` as an offender: it walked out of a
// `const OVERLAY_FOCUS = '<tailwind classes>'` string and into a *different* declaration
// twelve lines later that legitimately reads `physics.snappy` inside a render function. A
// window is not a scope.
const GATED_READ = /\b(?:spring|physics)\.[A-Za-z_]\w*/

/** Module-scope `const NAME = {...}` / `= [...]` initializers, brace-balanced. Arrow and
 *  function initializers are excluded: those re-read the getter on every call, which is the
 *  correct shape and the fix for every offender. */
function moduleScopeInitializers(text: string): { name: string; body: string; line: number }[] {
    const lines = text.split('\n')
    const out: { name: string; body: string; line: number }[] = []
    for (let i = 0; i < lines.length; i++) {
        const m = /^const\s+([A-Za-z_]\w*)\s*(?::[^=]+)?=\s*(.*)$/.exec(lines[i])
        if (!m) continue
        const head = m[2]
        if (head.includes('=>') || head.startsWith('function')) continue
        let depth = 0
        let started = false
        const body: string[] = []
        for (let j = i; j < lines.length; j++) {
            body.push(lines[j])
            for (const ch of lines[j]) {
                if (ch === '{' || ch === '[' || ch === '(') { depth++; started = true }
                else if (ch === '}' || ch === ']' || ch === ')') depth--
            }
            if (started && depth <= 0) break
            if (!started && j > i) break        // a one-line scalar const
        }
        out.push({ name: m[1], body: body.join('\n'), line: i + 1 })
    }
    return out
}

describe('a gated preset is never read at module scope', () => {
    it('sees the shape it exists to catch, and NOT the one that fooled a line window', () => {
        // Positive: the pre-fix `travelEnter`, verbatim in shape.
        const offender = moduleScopeInitializers(
            "const travelEnter = {\n  animate: { transition: spring.spatialSlow },\n}\n",
        ).filter((d) => GATED_READ.test(d.body))
        expect(offender.map((d) => d.name)).toEqual(['travelEnter'])

        // Negative: a scalar const, then an unrelated declaration that legitimately reads a
        // preset inside a render function. A window-based scan reports this; a balanced one must not.
        const innocent = moduleScopeInitializers(
            "const OVERLAY_FOCUS = 'focus:ring-primary/50'\n\nfunction Clear() {\n" +
                "  return <m.div transition={physics.snappy} />\n}\n",
        ).filter((d) => GATED_READ.test(d.body))
        expect(innocent, 'a line window walked into the next declaration and called it an offender')
            .toEqual([])

        // Arrow initializers are the FIX, so they must never be flagged.
        expect(
            moduleScopeInitializers(
                "const travelEnter = () => ({\n  animate: { transition: spring.spatialSlow },\n})\n",
            ).filter((d) => GATED_READ.test(d.body)),
        ).toEqual([])
    })

    it('no source file reads a gated preset at module scope', () => {
        const files = collectSources(SRC_ROOT)
        expect(files.length, 'the corpus is empty — this census proves nothing').toBeGreaterThan(300)
        const offenders: string[] = []
        for (const file of files) {
            const text = blankComments(readFileSync(join(CWD, file), 'utf8'))
            for (const decl of moduleScopeInitializers(text)) {
                if (GATED_READ.test(decl.body)) offenders.push(`${file}:${decl.line} ${decl.name}`)
            }
        }
        expect(offenders, [
            'a gated transition preset is read at module scope, which resolves the',
            'reduced-motion gate once at import and freezes it for the session.',
            'Wrap the initializer in a function so the getter is read per render.',
        ].join(' ')).toEqual([])
    })
})

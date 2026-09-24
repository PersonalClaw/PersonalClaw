/**
 * PERSONALITY-THEMES §S1/§S2 (contract C5) — structural invariants for the
 * personality registry.
 *
 * A personality is an identity a user can switch to, so the guarantees have to be
 * structural rather than promised in prose:
 *
 * - Its palette MUST come from `SCHEMES`, because that's what puts it inside
 *   `schemeContrast.test.ts`'s WCAG sweep. A personality carrying its own colors
 *   could ship an inaccessible palette that nothing checks.
 * - Its behavior block MUST stay within the closed, typed set — the property that
 *   lets a future app-contributed personality be validated against an allowlist
 *   instead of opening an arbitrary-code path.
 * - Every id it names — a scheme, a shell element, an error treatment, a cue voice,
 *   a dial's token — MUST resolve. Each of those failures degrades to the SAFE
 *   outcome (no element, no skin, the default tone, no dial), which is exactly why
 *   nothing would notice: a dangling id makes an identity look merely plain.
 * - The default identity MUST exist and be fully restorable, or "switch back"
 *   silently leaves the shell personalized.
 *
 * 🔑 EVERY RAIL IS PROVEN RED BY A BROKEN FIXTURE. A check that only ever runs over
 * a valid registry is indistinguishable from a check that cannot fail, so each
 * invariant here is a named function run over BOTH the real registry (must be clean)
 * and a fixture that breaks exactly it (must be flagged) — and the fixture must trip
 * ONLY its own rail, so a sloppy fixture can't make a broken rail look alive. The
 * mapping is asserted total in both directions: a new invariant without a falsifying
 * fixture fails this file.
 *
 * The last section is different in kind: it reads SOURCE, because "no cue is declared
 * without the master-toggle gate" is a claim about reachability, and the way a future
 * contributor would break it is to make sound somewhere other than `playCue`.
 */

import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { SCHEMES } from './schemes'
import {
  DEFAULT_PERSONALITY,
  PERSONALITIES,
  PERSONALITY_DIAL_TOKENS,
  getPersonality,
  getShellElement,
  resolvePersonality,
  type Personality,
  type PersonalityDials,
} from './personalities'
import { getErrorTreatment } from './errorTreatments'
import { CUES } from './soundCues'
import { TOKENS } from './tokenRegistry'

const SCHEME_IDS = new Set(SCHEMES.map((s) => s.id))

// The closed set of behavior keys. Adding one here is the deliberate act; a typo
// or a smuggled-in field fails this test.
const ALLOWED_BEHAVIOR_KEYS = new Set([
  'displayName',
  'wordmarkLabel',
  'faviconHref',
  'personaSnippet',
  'uiDensity',
  'documentTitle',
  'shellElement',
  'errorTreatment',
  'soundCues',
  'dials',
])

const ALLOWED_DENSITY = new Set(['comfortable', 'dense', 'cli'])
/** The three cue POINTS. Spelled out rather than derived, so re-voicing a moment can
 *  never quietly become "declare a new moment". */
const CUE_POINTS = new Set(['turn_complete', 'approval_needed', 'error'])

/** One structural invariant. Returns a human violation string, or `null` when the
 *  entry satisfies it. Named so the same implementation serves the real registry and
 *  the falsifying fixtures — two copies of the logic would let the fixture prove a
 *  rail that isn't the one shipping. */
interface Rail {
  id: string
  title: string
  check: (p: Personality) => string | null
}

const RAILS: Rail[] = [
  {
    id: 'baseScheme',
    title: 'names a baseScheme that exists in SCHEMES',
    // This is what puts every personality's palette through the contrast sweep.
    check: (p) =>
      SCHEME_IDS.has(p.baseScheme) ? null : `${p.id} → unknown baseScheme '${p.baseScheme}'`,
  },
  {
    id: 'behaviorKeys',
    title: 'keeps every behavior key inside the closed set',
    check: (p) => {
      const bad = Object.keys(p.behavior).filter((k) => !ALLOWED_BEHAVIOR_KEYS.has(k))
      return bad.length ? `${p.id}.behavior has un-allowlisted key(s) ${bad.join(', ')}` : null
    },
  },
  {
    id: 'uiDensity',
    title: 'maps uiDensity onto the existing density axis',
    check: (p) =>
      !p.behavior.uiDensity || ALLOWED_DENSITY.has(p.behavior.uiDensity)
        ? null
        : `${p.id} → unknown uiDensity '${p.behavior.uiDensity}'`,
  },
  {
    id: 'favicon',
    title: 'uses a bundled local favicon, never a remote URL',
    // A remote favicon would be an outbound request keyed to a UI preference.
    check: (p) => {
      const href = p.behavior.faviconHref
      if (!href) return null
      return href.startsWith('/') && !/^https?:/i.test(href)
        ? null
        : `${p.id} → non-local faviconHref '${href}'`
    },
  },
  {
    id: 'personaSnippet',
    title: 'follows the bundled persona-<id> naming the backend validates',
    check: (p) => {
      const s = p.behavior.personaSnippet
      if (!s) return null
      return s === `persona-${p.id}` ? null : `${p.id} → personaSnippet '${s}' breaks the naming`
    },
  },
  {
    id: 'shellElement',
    title: 'names a shellElement that resolves in its closed map',
    // Same failure shape as a dangling treatment id, one layer worse: a shell
    // element that does not resolve mounts NOTHING, which is also what a correct
    // standard scheme does — so the personality would look merely plain rather
    // than broken. The registry's own contract (laziness, the decorative
    // invariants) is asserted by rendering in `shellElements.test.tsx`.
    check: (p) => {
      const id = p.behavior.shellElement
      if (!id) return null
      return getShellElement(id) ? null : `${p.id} → dangling shellElement '${id}'`
    },
  },
  {
    id: 'errorTreatment',
    title: 'names an errorTreatment that resolves in its closed map',
    // A dangling id would silently degrade to "no treatment" — the safe outcome,
    // and therefore the one nothing would notice.
    check: (p) => {
      const id = p.behavior.errorTreatment
      if (!id) return null
      return getErrorTreatment(id) ? null : `${p.id} → dangling errorTreatment '${id}'`
    },
  },
  {
    id: 'cueVoices',
    title: 're-voices only real cue POINTS, and only with registered voices',
    // Both halves matter. A key outside the three points would be a personality
    // inventing a moment to make noise at; a value outside `CUES` would resolve to
    // the point's own voice, so the arcade's coin would silently become the plain
    // tone and the only symptom would be "it sounds normal".
    check: (p) => {
      const map = p.behavior.soundCues
      if (!map) return null
      for (const [point, voice] of Object.entries(map)) {
        if (!CUE_POINTS.has(point)) return `${p.id} → '${point}' is not a cue point`
        if (!Object.hasOwn(CUES, voice)) return `${p.id} → unregistered cue voice '${voice}'`
      }
      return null
    },
  },
  {
    id: 'dialTokens',
    title: 'presets only dials that name a real token, within that token’s range',
    // A dial is applied by writing its token through the appearance store, so the
    // token has to exist AND carry a `runtimeKey` — without one the write sets a CSS
    // var that the canvas/motion bridge never reads, and the identity quietly arrives
    // without its temperament. The range check catches the same failure one step in:
    // `setScalar` does not clamp, and the sliders' min/max are the declared domain.
    check: (p) => {
      const dials = p.behavior.dials
      if (!dials) return null
      for (const [dial, value] of Object.entries(dials)) {
        const varName = PERSONALITY_DIAL_TOKENS[dial as keyof PersonalityDials]
        if (!varName) return `${p.id} → '${dial}' is not a declared dial`
        const token = TOKENS.find((t) => t.varName === varName)
        if (!token) return `${p.id} → dial '${dial}' names missing token '${varName}'`
        if (token.kind === 'color' || !token.runtimeKey) {
          return `${p.id} → dial '${dial}' names '${varName}', which feeds no runtime dial`
        }
        if (typeof value === 'number') {
          if (token.kind !== 'scalar') return `${p.id} → dial '${dial}' is not a scalar token`
          if (value < token.min || value > token.max) {
            return `${p.id} → dial '${dial}'=${value} outside ${token.min}..${token.max}`
          }
        } else {
          if (token.kind !== 'select') return `${p.id} → dial '${dial}' is not a select token`
          if (!token.options.includes(value)) {
            return `${p.id} → dial '${dial}'='${value}' is not an offered value`
          }
        }
      }
      return null
    },
  },
  {
    id: 'labelAndHint',
    title: 'carries a human label and hint',
    check: (p) =>
      p.label.trim() && p.hint.trim() ? null : `${p.id} → empty label or hint`,
  },
]

/** A valid entry to mutate. Built here rather than borrowed from the registry so a
 *  fixture stays valid when the real entries change. */
const VALID: Personality = {
  id: 'fixture',
  label: 'Fixture',
  hint: 'A valid entry, used as the base for every broken one below.',
  baseScheme: SCHEMES[0].id,
  behavior: {},
}

/** One entry per rail, each breaking exactly that rail. `as never` casts are the
 *  point: every one of these is a compile error in the registry, and the whole
 *  reason these rails exist is the value that arrives from OUTSIDE the compiler — a
 *  persisted override, a hand-edited file, the plan's forward-hooked app-contributed
 *  manifest. */
const BROKEN: Record<string, Personality> = {
  baseScheme: { ...VALID, baseScheme: 'no-such-scheme' },
  behaviorKeys: { ...VALID, behavior: { injectedCss: 'body{display:none}' } as never },
  uiDensity: { ...VALID, behavior: { uiDensity: 'roomy' as never } },
  favicon: { ...VALID, behavior: { faviconHref: 'https://cdn.example.com/f.svg' } },
  personaSnippet: { ...VALID, behavior: { personaSnippet: 'persona-lumon' } },
  shellElement: { ...VALID, behavior: { shellElement: 'no-such-element' as never } },
  errorTreatment: { ...VALID, behavior: { errorTreatment: 'no-such-treatment' as never } },
  cueVoices: { ...VALID, behavior: { soundCues: { turn_complete: 'ka-ching' as never } } },
  dialTokens: { ...VALID, behavior: { dials: { dotShape: 'hexagram' as never } } },
  labelAndHint: { ...VALID, hint: '   ' },
}

const violations = (list: Personality[], rail: Rail) =>
  list.map(rail.check).filter((v): v is string => v !== null)

/** `[readable title, rail]` pairs, so `it.each`'s `%s` names the invariant. */
const CASES = RAILS.map((r) => [`${r.id} — ${r.title}`, r] as const)

describe('the real registry satisfies every structural invariant', () => {
  it.each(CASES)('%s', (_title, rail) => {
    expect(violations(PERSONALITIES, rail)).toEqual([])
  })

  it('has at least the default plus two alternatives to prove switching', () => {
    expect(PERSONALITIES.length).toBeGreaterThanOrEqual(3)
  })

  it('declares unique ids', () => {
    const ids = PERSONALITIES.map((p) => p.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe('every invariant goes red on a broken entry', () => {
  it('every rail has a falsifying fixture, and every fixture a rail', () => {
    // The meta-floor. Without it, adding a rail and forgetting its fixture would
    // land a check nothing has ever seen fail — which is the failure mode this whole
    // section exists to prevent.
    expect(Object.keys(BROKEN).sort()).toEqual(RAILS.map((r) => r.id).sort())
  })

  it('the base fixture is itself CLEAN — or every mutation below proves nothing', () => {
    for (const rail of RAILS) expect(rail.check(VALID), rail.id).toBeNull()
  })

  it.each(CASES)('%s — flags its own broken fixture', (_title, rail) => {
    expect(rail.check(BROKEN[rail.id]), `${rail.id} did not flag its own fixture`).not.toBeNull()
  })

  it.each(CASES)('%s — its fixture trips no OTHER rail', (_title, rail) => {
    // Isolation. A fixture that breaks three things at once would let a dead rail
    // look alive, because *something* went red.
    const tripped = RAILS.filter((r) => r.check(BROKEN[rail.id]) !== null).map((r) => r.id)
    expect(tripped).toEqual([rail.id])
  })
})

describe('the conditional rails scan a non-empty population', () => {
  // Six of the ten rails only look at entries that DECLARE the field. If no shipped
  // personality declares one, that rail is green forever over the real registry and
  // the fixture half above is the only thing keeping it honest. These floors mean
  // deleting a declaration from the registry reddens here rather than silently
  // retiring a rail.
  const declares = (pick: (p: Personality) => unknown) => PERSONALITIES.filter((p) => pick(p)).map((p) => p.id)

  it.each([
    ['faviconHref', (p: Personality) => p.behavior.faviconHref],
    ['personaSnippet', (p: Personality) => p.behavior.personaSnippet],
    ['uiDensity', (p: Personality) => p.behavior.uiDensity],
    ['shellElement', (p: Personality) => p.behavior.shellElement],
    ['errorTreatment', (p: Personality) => p.behavior.errorTreatment],
    ['soundCues', (p: Personality) => p.behavior.soundCues],
    ['dials', (p: Personality) => p.behavior.dials],
  ])('at least one personality declares %s', (_field, pick) => {
    expect(declares(pick)).not.toEqual([])
  })

  it('both proof identities are fully specified — every behavior except the default’s', () => {
    // PT-5's "both proof personalities fully specified". Between them the two proofs
    // must exercise EVERY key in the closed block, or the registry shape is described
    // rather than demonstrated and a field can rot unread.
    const proofs = PERSONALITIES.filter((p) => p.id !== DEFAULT_PERSONALITY)
    const covered = new Set(proofs.flatMap((p) => Object.keys(p.behavior)))
    expect([...ALLOWED_BEHAVIOR_KEYS].filter((k) => !covered.has(k))).toEqual([])
  })

  it('every registered cue voice is reachable from some personality or is a cue point', () => {
    // A voice nobody declares and no point defaults to is a recipe that can never
    // sound — the "declared kind without a runtime" shape. Adding `coin_blip` without
    // the arcade declaring it would fail here.
    const declared = new Set<string>(
      PERSONALITIES.flatMap((p) => Object.values(p.behavior.soundCues ?? {})),
    )
    const unreachable = Object.keys(CUES).filter(
      (voice) => !CUE_POINTS.has(voice) && !declared.has(voice),
    )
    expect(unreachable, 'these cue recipes can never play').toEqual([])
  })
})

// ── "No cue is declared without the master-toggle gate" ─────────────────────
//
// The registry half of that claim is the `cueVoices` rail above. This is the
// reachability half, and it reads source because that is where the bypass lives: a
// contributor who wants a sound in a new place does not edit the registry, they call
// the Web Audio API somewhere convenient. Three ways that could happen, each pinned.

const WEB = process.cwd() // vitest runs from web/
const CUE_MODULE = 'src/design/soundCues.ts'

function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}

/** Every non-test source file under `web/src`. Tests are excluded deliberately: the
 *  cue tests must import `CUES` to assert the recipes, and a rail that forbade that
 *  would forbid testing the thing it protects. */
function sourceFiles(dir = join(WEB, 'src')): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...sourceFiles(p))
    else if (/\.tsx?$/.test(entry) && !/\.(test|spec)\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

/** The body of a top-level `function <name>(…) {…}`, extracted by brace balance.
 *  Reading the whole module would let a guard sitting in some other function satisfy
 *  a check about this one. */
function functionBody(code: string, name: string): string {
  const start = code.indexOf(`function ${name}(`)
  if (start < 0) return ''
  const open = code.indexOf('{', start)
  if (open < 0) return ''
  let depth = 0
  for (let i = open; i < code.length; i++) {
    if (code[i] === '{') depth++
    else if (code[i] === '}' && --depth === 0) return code.slice(open + 1, i)
  }
  return ''
}

describe('a cue can only sound through the gate', () => {
  const raw = readFileSync(join(WEB, CUE_MODULE), 'utf8')
  const code = stripComments(raw)
  const body = functionBody(code, 'playCue')

  it('the extractor found real code, and only playCue’s', () => {
    // Floor for every assertion in this block. An empty extraction would satisfy the
    // "no synth outside playCue" check below and fail nothing.
    expect(body.length, 'playCue’s body was not found').toBeGreaterThan(80)
    expect(body).toContain('synth(')
    expect(functionBody(code, 'thisFunctionDoesNotExist')).toBe('')
    // And the stripper works, or a guard mentioned in a COMMENT would count as one.
    expect(stripComments('x /* soundCuesEnabled() */ y')).not.toMatch(/soundCuesEnabled\(\)/)
  })

  it('playCue checks all three suppressors BEFORE it reaches the synth', () => {
    const upToSynth = body.slice(0, body.indexOf('synth('))
    expect(upToSynth, 'the master toggle').toMatch(/soundCuesEnabled\(\)/)
    expect(upToSynth, 'reduced motion').toMatch(/prefersReducedMotion\(\)/)
    expect(upToSynth, 'a hidden tab').toMatch(/document\.hidden/)
  })

  it('playCue is the ONLY caller of synth — no second, ungated path', () => {
    // The gates are worth nothing if a sibling export can reach the synthesiser. This
    // counts call sites across the module and subtracts the ones inside playCue.
    const total = [...code.matchAll(/(?<!function )\bsynth\(/g)].length
    const inside = [...body.matchAll(/\bsynth\(/g)].length
    expect(total, 'the counter found no call at all').toBeGreaterThan(0)
    expect(total - inside, 'something outside playCue calls synth').toBe(0)
  })

  it('the synthesiser and the recipes-to-sound path are module-private', () => {
    // `CUES` IS exported (the picker's prose and these tests read it), but `synth` is
    // the only thing that turns a recipe into sound, and it must stay unreachable.
    expect(code, 'synth must not be exported').not.toMatch(/export\s+(function\s+)?synth\b/)
    expect(code).toMatch(/^function synth\(/m)
  })

  it('no module outside the cue module can synthesise a tone', () => {
    // The real bypass: import `CUES`, build an oscillator, skip every gate. Nothing
    // else in web/src creates an oscillator today — the TTS player decodes buffers and
    // the recorder analyses a mic stream, neither of which can voice a cue — so this
    // stays a tight rail rather than an allowlist.
    const offenders = sourceFiles()
      .map((f) => f.slice(WEB.length + 1))
      .filter((rel) => rel !== CUE_MODULE)
      .filter((rel) => /createOscillator\s*\(/.test(stripComments(readFileSync(join(WEB, rel), 'utf8'))))
    expect(offenders, 'a tone built here would answer to no toggle — call playCue instead')
      .toEqual([])
  })

  it('the source sweep is real: exactly one module synthesises, and it is the cue module', () => {
    // Vacuity floor for the sweep. A walker returning nothing, or a regex that never
    // matches, would report "no offenders" on a tree full of them.
    const all = sourceFiles().map((f) => f.slice(WEB.length + 1))
    expect(all.length, 'the walker must find web/src').toBeGreaterThan(200)
    expect(all, 'the sweep must include the cue module itself').toContain(CUE_MODULE)
    const withOsc = all.filter((rel) =>
      /createOscillator\s*\(/.test(readFileSync(join(WEB, rel), 'utf8')),
    )
    expect(withOsc).toEqual([CUE_MODULE])
  })
})

describe('the default identity is restorable', () => {
  it('the default id resolves to a real entry', () => {
    expect(getPersonality(DEFAULT_PERSONALITY)).toBeDefined()
  })

  it('the default carries no assistant rename', () => {
    // Restoring the default must not itself propose a name — it CLEARS the name.
    expect(getPersonality(DEFAULT_PERSONALITY)?.behavior.displayName).toBeUndefined()
  })

  it('the default declares no cue voice, no dial, no shell element, no treatment', () => {
    // "Switch back" has to be a real restore, and every one of these is a behavior
    // whose residue you would only notice later: a tone you did not choose, a sparkle
    // backdrop, an overlay, a skinned error panel.
    const b = getPersonality(DEFAULT_PERSONALITY)!.behavior
    expect(b.soundCues).toBeUndefined()
    expect(b.dials).toBeUndefined()
    expect(b.shellElement).toBeUndefined()
    expect(b.errorTreatment).toBeUndefined()
  })

  it('an unknown or removed id falls back to the default, never a broken state', () => {
    expect(resolvePersonality('was-removed-in-a-later-release').id).toBe(DEFAULT_PERSONALITY)
    expect(resolvePersonality(undefined).id).toBe(DEFAULT_PERSONALITY)
  })
})

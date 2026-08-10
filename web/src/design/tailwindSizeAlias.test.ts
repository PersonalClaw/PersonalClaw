import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Tailwind's font-size ALIASES bypass the app's type ramp ────────────────────
//
// This app sizes type with explicit rem values on a documented ramp (`text-[0.75rem]`,
// `text-[0.8125rem]`, `text-[0.9375rem]`, …) — that is what makes a step reviewable and what the
// design hook checks against DESIGN.md. Tailwind also ships named aliases (`text-sm`, `text-base`,
// `text-lg`, …) which resolve to values NOT on that ramp: `text-sm` is 0.875rem (14px) where the
// neighbouring ramp step is 0.8125rem (13px).
//
// Ten sites had drifted onto `text-sm`, against hundreds using the ramp:
//
//   settings/PacksPanel        the ONLY one of 52 settings panels using it, for an inline empty
//                              line whose six siblings all use text-[0.8125rem]
//   loops/DesignCockpitPage    ×4 loading/empty lines
//   loops/LoopsSection         ×2, loops/DesignSystemPreview ×1
//   knowledge/ConflictPanel    ×1 panel body
//   apps/appConfigForm         ×1 — a raw <input>, diverging from the shared form family, which
//                              uses text-[0.8125rem] at EVERY size (forms.tsx sm/md)
//
// All ten converged onto 0.8125rem: the value each site's own neighbours already used, so this is
// a 1px correction toward the local majority rather than a redesign.
//
// WHY A RAIL AND NOT JUST A FIX: nothing enforced this. `tokenLint` covers colour tokens and
// `primitiveAdoption` counts raw chrome; neither looks at font-size aliases, which is exactly how
// ten sites accumulated. The design hook flags off-ramp literals but is advisory per-edit, not a
// gate. This test is the gate.
//
// SCOPED TO pages/ + ui/ ON PURPOSE. It does not police every alias Tailwind offers — only the
// font-size family, and only where app type lives. A vendored/demo file or a deliberate future step
// should be added to ALLOWED with its reason rather than silently widening the regex.

const SRC = join(process.cwd(), 'src')

/** Tailwind's named font-size scale. `text-<color>` and `text-[…]` are unaffected: the regex
 *  requires the alias to be the WHOLE utility, so `text-on-surface-low` and `text-[0.75rem]`
 *  never match. */
const SIZE_ALIASES = ['xs', 'sm', 'base', 'lg', 'xl', '2xl', '3xl', '4xl', '5xl', '6xl', '7xl', '8xl', '9xl']
const ALIAS_RE = new RegExp(String.raw`(?<![-\w])text-(${SIZE_ALIASES.join('|')})(?![-\w])`)

/** Deliberate exceptions, each with a reason. Empty today — added only with justification. */
const ALLOWED: Record<string, string> = {}

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) { out.push(...walk(p)); continue }
    if (/\.(tsx|ts)$/.test(name) && !/\.test\.(tsx|ts)$/.test(name)) out.push(p)
  }
  return out
}

describe('type sizes stay on the app ramp, not Tailwind aliases', () => {
  it('no page or ui file uses a text-<size> alias', () => {
    const offenders: string[] = []
    for (const abs of [...walk(join(SRC, 'pages')), ...walk(join(SRC, 'ui'))]) {
      const rel = abs.slice(SRC.length + 1)
      if (rel in ALLOWED) continue
      const src = readFileSync(abs, 'utf8')
      src.split('\n').forEach((line, i) => {
        if (ALIAS_RE.test(line)) offenders.push(`${rel}:${i + 1}`)
      })
    }
    expect(
      offenders,
      `Tailwind font-size alias(es) found — use an explicit ramp value (e.g. text-[0.8125rem]):\n  ` +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the regex matches a real alias', () => {
    // A rail that cannot fire is worse than none, so prove the matcher works in both directions.
    expect(ALIAS_RE.test('className="text-on-surface-low text-sm"')).toBe(true)
    expect(ALIAS_RE.test('className="p-3 text-base"')).toBe(true)
    expect(ALIAS_RE.test('className="text-2xl"')).toBe(true)
  })

  it('the regex does NOT match colour tokens or explicit ramp values', () => {
    // These are the shapes a careless `text-sm` regex would break on: the app's colour utilities
    // and its arbitrary-value sizes both start with `text-`.
    expect(ALIAS_RE.test('className="text-on-surface-low"')).toBe(false)
    expect(ALIAS_RE.test('className="text-[0.8125rem]"')).toBe(false)
    expect(ALIAS_RE.test('className="text-success text-[0.75rem]"')).toBe(false)
    expect(ALIAS_RE.test('className="text-center"')).toBe(false)
    expect(ALIAS_RE.test('data-type="title-l"')).toBe(false)
  })
})

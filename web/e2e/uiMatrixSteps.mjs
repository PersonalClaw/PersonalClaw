// The UI quality matrix's step names, read from `surfaceChecks.ts`'s source, for the report
// checker (`assert-no-route-skips.mjs`), which runs under plain `node` and cannot import the
// TypeScript module and the browser libraries it pulls in. `src/design/uiMatrixSteps.test.ts`
// holds this to `STEP_NAMES` itself, so the two can never disagree quietly.

/** The step names `runMatrix` reports, in its order, from the text of `surfaceChecks.ts`.
 *  Throws when a piece it needs cannot be found, so a moved literal is a loud failure. */
export function stepNamesFromSource(src) {
  const block = (re, what) => {
    const m = src.match(re)
    if (!m) throw new Error(`could not locate ${what} in e2e/surfaceChecks.ts — re-point uiMatrixSteps.mjs`)
    return m[1]
  }
  const steps = Object.fromEntries(
    [...block(/export const STEPS = \{([\s\S]*?)\} as const/, 'STEPS').matchAll(/(\w+): '([^']+)'/g)].map((m) => [m[1], m[2]]),
  )
  const numbers = (s) => [...s.matchAll(/\d+/g)].map((m) => Number(m[0]))
  const widths = numbers(block(/export const WIDTHS = \[([^\]]*)\]/, 'WIDTHS'))
  const compact = numbers(block(/export const COMPACT_WIDTHS = \[([^\]]*)\]/, 'COMPACT_WIDTHS'))
  const overflow = block(/export const overflowStep = \(w: number\): string => `([^`]*)`/, 'overflowStep')
  const target = block(/export const targetStep = \(w: number\): string => `([^`]*)`/, 'targetStep')
  const order = [...block(/export const STEP_NAMES[^=]*= \[([\s\S]*?)\n\]/, 'STEP_NAMES').matchAll(/STEPS\.(\w+)/g)].map((m) => m[1])
  if (!order.length || !widths.length || !compact.length) throw new Error('e2e/surfaceChecks.ts parsed as an EMPTY matrix')
  const at = (template, w) => template.replace('${w}', String(w))
  return [
    ...order.map((k) => {
      if (!(k in steps)) throw new Error(`STEP_NAMES names STEPS.${k}, which STEPS does not define`)
      return steps[k]
    }),
    ...widths.flatMap((w) => (compact.includes(w) ? [at(overflow, w), at(target, w)] : [at(overflow, w)])),
  ]
}

/** Every step title in one Playwright JSON result, nested steps included. */
export function stepTitles(result) {
  const out = []
  const walk = (steps) => {
    for (const s of steps ?? []) {
      out.push(s.title)
      walk(s.steps)
    }
  }
  walk(result?.steps)
  return out
}

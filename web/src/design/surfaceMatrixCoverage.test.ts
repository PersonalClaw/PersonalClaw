import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import * as tiers from '../../e2e/routes'

// ── The UI quality matrix visits every surface the specs it replaced visited ─────────────────────
//
// `e2e/surfaces.spec.ts` replaced the route and opener tests of `a11y.spec.ts` and the three legs
// of `walkthrough.spec.ts` (#3941). Each of those visited a fixed population: the routes of four
// tiers of `e2e/routes.ts`, and the opened surfaces in `OPENERS`. The list below is that
// population as it stood when the matrix took over, written out, so it does not move with the code
// it checks: a tier the matrix stops spreading, a route deleted from a tier, or an opener dropped
// from the loop is a red here, naming the surface, instead of a quieter report.
//
// A surface may leave this list only by leaving the app (its route or opener is gone from the
// manifest too); `routeManifestParity.test.ts` holds the manifest to the app's own routes.

/** Every route `a11y.spec.ts` and `walkthrough.spec.ts` visited, once per theme. */
const VISITED_ROUTES = [
  'dashboard', 'chat', 'projects', 'knowledge', 'tasks', 'inbox', 'triggers', 'files', 'artifacts',
  'terminal', 'agents', 'tools', 'skills', 'learning', 'workflows', 'prompts', 'apps', 'settings',
  'settings/account', 'settings/design', 'settings/chat', 'settings/providers', 'settings/models',
  'settings/search', 'settings/prompts', 'settings/memory', 'settings/evals', 'settings/agent',
  'settings/voice', 'settings/apps', 'settings/inbox', 'settings/documents', 'settings/notifications',
  'settings/security', 'settings/secrets', 'settings/devices', 'settings/sender-trust',
  'settings/guardrails', 'settings/external-access', 'settings/audit', 'settings/system',
  'settings/doctor', 'settings/diagnostics', 'settings/tool-output', 'settings/feedback',
  'settings/usage', 'settings/routing', 'settings/legibility', 'settings/workflows',
  'settings/loops', 'settings/ambient', 'settings/companion', 'settings/sources', 'settings/packs',
  'settings/archive', 'settings/portability', 'settings/durability', 'settings/updates',
  'knowledge?view=graph', 'knowledge/item/not-a-real-item?read=1', 'mission-control',
  'notifications', 'discover', 'app/not-a-real-app', 'app/e2e-ui-fixture',
] as const

/** Every opened surface both specs visited, by its `OPENERS` label. */
const VISITED_OPENERS = [
  'command palette', 'chat slash menu', 'knowledge peek dock', 'inbox peek dock', 'new project modal',
] as const

const WEB = process.cwd()
const read = (rel: string) => readFileSync(join(WEB, rel), 'utf8')

type Entry = { route: string }
const TIERS = tiers as unknown as Record<string, unknown>

/** The routes the surface spec visits: every tier of routes.ts it spreads into `SURFACES`. */
function matrixRoutes(spec = read('e2e/surfaces.spec.ts')): string[] {
  const line = spec.match(/export const SURFACES = \[([^\]]*)\]/)
  if (!line) return []
  return [...line[1].matchAll(/\.\.\.([A-Z_]+)/g)].flatMap((m) => {
    const tier = TIERS[m[1]]
    return Array.isArray(tier) ? (tier as Entry[]).map((e) => e.route) : []
  })
}

/** The opener labels the surface spec visits: OPENERS' labels, when the spec loops over OPENERS. */
function matrixOpeners(spec = read('e2e/surfaces.spec.ts'), helpers = read('e2e/helpers.ts')): string[] {
  if (!/for \(const opener of OPENERS\)/.test(spec)) return []
  const block = helpers.match(/export const OPENERS[^=]*= \[[\s\S]*?\n\]/)
  return block ? [...block[0].matchAll(/label: '([^']+)'/g)].map((m) => m[1]) : []
}

/** What a population lost against the frozen one. */
const dropped = (visited: readonly string[], now: string[]) => visited.filter((s) => !now.includes(s))

describe('the UI quality matrix visits every surface the specs it replaced visited', () => {
  it('reads the matrix (a parser that stopped matching would drop everything and say so)', () => {
    expect(matrixRoutes().length).toBeGreaterThanOrEqual(VISITED_ROUTES.length)
    expect(matrixOpeners().length).toBeGreaterThanOrEqual(VISITED_OPENERS.length)
  })

  it('visits every route a11y.spec.ts and walkthrough.spec.ts visited', () => {
    expect(
      dropped(VISITED_ROUTES, matrixRoutes()),
      'e2e/surfaces.spec.ts no longer visits these routes, which the specs it replaced did. Spread ' +
        'their tier into SURFACES again, or remove the route from the app and from this list.',
    ).toEqual([])
  })

  it('visits every opened surface both specs visited', () => {
    expect(
      dropped(VISITED_OPENERS, matrixOpeners()),
      'e2e/surfaces.spec.ts no longer opens these surfaces, which the specs it replaced did.',
    ).toEqual([])
  })

  it('fails on a matrix that drops a tier, a route or an opener', () => {
    const spec = read('e2e/surfaces.spec.ts')
    const noSettings = spec.replace('...SETTINGS_ROUTES, ', '')
    expect(noSettings, 'the plant found no SETTINGS_ROUTES spread').not.toEqual(spec)
    expect(dropped(VISITED_ROUTES, matrixRoutes(noSettings))).toContain('settings/memory')

    const oneRouteFewer = matrixRoutes().filter((r) => r !== 'mission-control')
    expect(dropped(VISITED_ROUTES, oneRouteFewer)).toEqual(['mission-control'])

    const noOpeners = spec.replace('for (const opener of OPENERS)', 'for (const opener of [])')
    expect(noOpeners, 'the plant found no OPENERS loop').not.toEqual(spec)
    expect(dropped(VISITED_OPENERS, matrixOpeners(noOpeners))).toEqual([...VISITED_OPENERS])

    const helpers = read('e2e/helpers.ts').replace("label: 'inbox peek dock'", "label: 'renamed dock'")
    expect(dropped(VISITED_OPENERS, matrixOpeners(spec, helpers))).toEqual(['inbox peek dock'])
  })
})

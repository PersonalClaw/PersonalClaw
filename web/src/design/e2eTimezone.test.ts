import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { RUN_TIMEZONE, gatewayEnvPrefix } from '../../e2e/runPlaces'

// ── A browser run reads one timezone, set in one place ────────────────────────────────────────
//
// The gateway's (`TZ`) and the browser's (`use.timezoneId`) are both RUN_TIMEZONE, from
// `e2e/runPlaces.ts`. Left to the machine, a trigger's next run read PDT on the Mac that took the
// matrix's baselines and UTC on CI, so a surface was a red on one of them for its clock alone
// (#3905). A spec that sets a timezone of its own would bring that back for every surface it
// opens, so each one that does is named here, and a planted one proves the search finds it.

const WEB = process.cwd()
const E2E = join(WEB, 'e2e')

/** What sets a timezone: Playwright's option, the process variable, or the CDP override. */
const SETS_A_TIMEZONE = /\btimezoneId\b|\bprocess\.env\.TZ\b|\bTZ\s*=|setTimezoneOverride/

/** The places allowed to: the constant and the gateway's environment, and the config's option. */
const THE_ONE_PLACE = new Set(['runPlaces.ts', 'playwright.config.ts'])

/** Whether a config pins its runner's own zone to RUN_TIMEZONE, as a top-level statement. */
function pinsTheRunner(config: string): boolean {
  return /^process\.env\.TZ = RUN_TIMEZONE$/m.test(config)
}

/** Each file in `files` that sets a timezone of its own, by name. */
function ownTimezones(files: ReadonlyArray<readonly [string, string]>): string[] {
  return files.filter(([name, text]) => !THE_ONE_PLACE.has(name) && SETS_A_TIMEZONE.test(text)).map(([name]) => name)
}

const tree = (): Array<[string, string]> => [
  ...readdirSync(E2E)
    .filter((f) => f.endsWith('.ts') || f.endsWith('.mjs'))
    .map((f): [string, string] => [f, readFileSync(join(E2E, f), 'utf8')]),
  ['playwright.config.ts', readFileSync(join(WEB, 'playwright.config.ts'), 'utf8')],
]

describe('the browser run reads one timezone', () => {
  it('the gateway, the browser and the runner all read RUN_TIMEZONE', () => {
    expect(RUN_TIMEZONE).toBe('Etc/UTC')
    expect(gatewayEnvPrefix()).toContain(`TZ=${RUN_TIMEZONE}; export TZ;`)
    const config = readFileSync(join(WEB, 'playwright.config.ts'), 'utf8')
    expect(config).toMatch(/timezoneId: RUN_TIMEZONE,/)
    expect(pinsTheRunner(config)).toBe(true)
  })

  it('names a config whose runner reads the machine\'s zone', () => {
    // The specs' fixed clocks and stubbed weeks are built in the runner: unpinned, a Mac's run put
    // the matrix's week 8 hours from CI's and named America/Los_Angeles as its zone.
    const config = readFileSync(join(WEB, 'playwright.config.ts'), 'utf8')
    expect(pinsTheRunner(config.replace(/^process\.env\.TZ = RUN_TIMEZONE\n/m, ''))).toBe(false)
    expect(pinsTheRunner(config.replace(/^process\.env\.TZ = RUN_TIMEZONE$/m, "process.env.TZ = 'America/Los_Angeles'"))).toBe(false)
  })

  it('the runner of this very test reads the zone a run would', () => {
    // A worker of a real run inherits TZ from the config; here, the one the config would set
    // gives the same `Date` arithmetic the matrix's stubbed week does.
    const was = process.env.TZ
    process.env.TZ = RUN_TIMEZONE
    try {
      const d = new Date('2026-11-02T00:00:00')
      d.setHours(8)
      expect(d.toISOString()).toBe('2026-11-02T08:00:00.000Z')
      expect(['UTC', 'Etc/UTC']).toContain(Intl.DateTimeFormat().resolvedOptions().timeZone)
    } finally {
      if (was === undefined) delete process.env.TZ
      else process.env.TZ = was
    }
  })

  it('no spec or helper sets a timezone of its own', () => {
    const files = tree()
    expect(files.length, 'no e2e sources found: this check would pass vacuously').toBeGreaterThan(20)
    expect(ownTimezones(files)).toEqual([])
  })

  it('names a planted spec that sets its own', () => {
    const planted: Array<[string, string]> = [
      ['clock.spec.ts', "test.use({ timezoneId: 'America/Los_Angeles' })"],
      ['context.spec.ts', "const c = await browser.newContext({ timezoneId: 'Asia/Tokyo' })"],
      ['env.spec.ts', "process.env.TZ = 'Europe/Paris'"],
      ['cdp.spec.ts', "await cdp.send('Emulation.setTimezoneOverride', { timezoneId: 'UTC' })"],
      ['quiet.spec.ts', "await page.clock.setFixedTime(new Date('2026-10-23T00:00:00Z'))"],
    ]
    expect(ownTimezones([...tree(), ...planted])).toEqual(['clock.spec.ts', 'context.spec.ts', 'env.spec.ts', 'cdp.spec.ts'])
  })
})

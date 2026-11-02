import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── The browser run greets the demo persona (#5868) ──────────────────────────────────────────────
//
// The browser suite's home (`web/e2e/fixtures/home/config.json`) seeds the name its pages greet,
// so the committed accessibility trees and layout baselines, and the screenshots taken with them,
// say that name ("Good afternoon, <name>"). It was a real person's. It is the demo persona's now,
// the one the bundled demo home greets (`src/personalclaw/tests_fixtures/demo-home`), and so is the
// name the specs type into onboarding or serve to a page. The name is read from the demo home, so
// nothing here spells it.

const WEB = process.cwd()
const ROOT = join(WEB, '..')
const nameIn = (path: string): string => JSON.parse(readFileSync(path, 'utf8')).dashboard.user_name
const PERSONA = nameIn(join(ROOT, 'src/personalclaw/tests_fixtures/demo-home/config.json'))

describe('the browser run greets the demo persona', () => {
  it('its home is seeded with the demo home\'s persona', () => {
    expect(PERSONA, 'the demo home names a persona').toBeTruthy()
    expect(nameIn(join(WEB, 'e2e/fixtures/home/config.json'))).toBe(PERSONA)
  })

  it('every greeting its accessibility trees and layout baselines pin names that persona', () => {
    const seen: string[] = []
    const others: string[] = []
    for (const dir of ['e2e/__aria__', 'e2e/__ui-baselines__']) {
      for (const file of readdirSync(join(WEB, dir))) {
        const text = readFileSync(join(WEB, dir, file), 'utf8')
        for (const m of text.matchAll(/Good <time of day>, ([^"\\\n]+)/g)) {
          seen.push(file)
          if (m[1].trim() !== PERSONA) others.push(`${dir}/${file}`)
        }
      }
    }
    expect(seen.length, 'the baselines pin a greeting at all').toBeGreaterThan(0)
    expect([...new Set(others)], 'baselines that greet anyone but the persona').toEqual([])
  })

  it('the specs that type a name into onboarding, or serve one to a page, use it too', () => {
    const others: string[] = []
    for (const file of readdirSync(join(WEB, 'e2e')).filter((f) => f.endsWith('.spec.ts'))) {
      const text = readFileSync(join(WEB, 'e2e', file), 'utf8')
      for (const m of text.matchAll(/name: 'Your name' \}\)\.fill\('([^']*)'\)|userName: '([^']*)'/g)) {
        const name = m[1] ?? m[2]
        if (name !== PERSONA) others.push(`${file}: a name that is not the persona's`)
      }
    }
    expect(others).toEqual([])
  })
})

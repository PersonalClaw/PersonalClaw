import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── #492: every consent surface answers the host-page question ───────────────────────
//
// The row itself is tested in `hostPageConsent.test.tsx`. This is the reachability half:
// a disclosure that renders correctly and is never passed the fact is not a disclosure.
// `PermissionList` deliberately renders NOTHING when `hostUi` is omitted (asserting "no
// browser code" about an app nobody read would be worse than silence), so omission fails
// open — which makes a call-site rail the only thing standing between that design and the
// original defect coming back one surface at a time.
//
// COUNTED PER CALL SITE, not as bare membership. A rail that only asked "does some
// PermissionList pass hostUi?" stays green with three of four sites missing, which is how
// a defect multiplies behind a green check. `consentDisclosesAbsent.test.ts` established
// the pattern for `ConsentModal` (`expect(seen).toBe(4)`); this mirrors it for both
// components and for both files that render them.

const SITES = [
  join('src/pages/apps', 'AppsSection.tsx'),
  join('src/app/onboarding', 'EssentialsStep.tsx'),
]

const src = (rel: string) => readFileSync(join(process.cwd(), rel), 'utf8')

/** Source with comments stripped, so a `<PermissionList` inside a code comment cannot
 *  satisfy — or trip — a count. */
const code = (rel: string) =>
  src(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('every consent surface is handed the host-page fact (#492)', () => {
  it('every PermissionList render passes hostUi', () => {
    const perFile: Record<string, number> = {}
    for (const rel of SITES) {
      const matches = [...code(rel).matchAll(/<PermissionList\b[^>]*\/?>/g)]
      perFile[rel] = matches.length
      for (const m of matches) {
        expect(m[0], `${rel}: a PermissionList renders without the host-page fact`)
          .toMatch(/hostUi=\{consentHostUi\(/)
      }
    }
    // Three renders today: the Store's installed-app panel, its pre-install card, and the
    // onboarding card. A fourth that forgets must fail this, not pass it vacuously.
    expect(perFile['src/pages/apps/AppsSection.tsx']).toBe(2)
    expect(perFile['src/app/onboarding/EssentialsStep.tsx']).toBe(1)
  })

  it('every ConsentModal render passes hostUi', () => {
    let seen = 0
    for (const rel of SITES) {
      for (const m of code(rel).matchAll(/<ConsentModal\b[\s\S]{0,700}?\/>/g)) {
        seen += 1
        expect(m[0], `${rel}: a ConsentModal renders without the host-page fact`)
          .toMatch(/hostUi=\{consentHostUi\(/)
      }
    }
    // The same four the issue-614 rail counts — they are the same modals.
    expect(seen).toBe(4)
  })

  it('the onboarding card gates on consentKnown, like the Store does', () => {
    // This card still carried the hide-when-empty gate issue 614 removed from the Store
    // panel (`entry.permissions && Object.keys(entry.permissions).length > 0`), so an app
    // declaring nothing rendered no disclosure at all — and #492's row lives inside
    // `PermissionList`. The app that declares nothing and still runs in this page is
    // precisely the one the issue is about, so the gate is load-bearing here twice.
    const onboarding = code('src/app/onboarding/EssentialsStep.tsx')
    expect(onboarding).toMatch(/entry\.consentKnown \?/)
    expect(onboarding).not.toMatch(
      /entry\.permissions && Object\.keys\(entry\.permissions\)\.length > 0/,
    )
    expect(onboarding).toMatch(/not known yet/i)
  })

  it('no Store row fabricates the answer it is about to disclose', () => {
    // The defect this catches was invisible to every rail above and to typecheck, and was
    // found only by driving the real Store: `storeUniverse` normalised each catalog entry
    // with `hasUI: false` — a placeholder from when the field existed for installed apps
    // only — so `consentHostUi` was handed a hard-coded answer and the panel for an app
    // that DOES ship a UI read "Runs in this dashboard page: no". Counting call sites
    // proves the fact is passed; this proves it is not invented on the way.
    const src = code('src/pages/apps/AppsSection.tsx')
    expect(src).not.toMatch(/hasUI:\s*(?:false|true)\b/)
    expect(src).toMatch(/hasUI: Boolean\(e\.hasUI\)/)
  })

  it('consentHostUi is the one authority, and it declines to guess', () => {
    const consent = code('src/pages/apps/installConsent.tsx')
    expect(consent).toMatch(/export function consentHostUi/)
    expect(consent).toMatch(/if \(!a\) return undefined/)
    // The two facts stay separate on the wire AND in the reading.
    expect(consent).toMatch(/page: Boolean\(a\.hasUI\), components: Boolean\(a\.uiComponents\)/)
  })
})

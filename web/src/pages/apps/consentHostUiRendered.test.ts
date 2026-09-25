import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
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
// A CENSUS OF EVERY PRODUCTION FILE, not a list of known ones. Every pre-install surface now
// renders the one disclosure (`AppDisclosureView`), so the whole population of `PermissionList`
// renders is two — that view and the installed-app panel — and a third, anywhere in the tree,
// is a surface that went around the one disclosure. `consentPythonDeps.test.tsx` counts the
// same population for the Python-dependency fact.

const SRC = join(process.cwd(), 'src')

/** Source with comments stripped, so a `<PermissionList` inside a code comment cannot
 *  satisfy — or trip — a count. */
const strip = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const code = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

function productionFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    if (statSync(p).isDirectory()) return n === 'test' ? [] : productionFiles(p)
    return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : []
  })
}

describe('every consent surface is handed the host-page fact (#492)', () => {
  it('every PermissionList render passes hostUi, and there are exactly two', () => {
    const renders = productionFiles(SRC).flatMap((abs) =>
      [...strip(readFileSync(abs, 'utf8')).matchAll(/<PermissionList\b[^>]*\/?>/g)]
        .map((m) => ({ rel: abs.slice(SRC.length + 1), tag: m[0] })))
    for (const r of renders) {
      expect(r.tag, `${r.rel}: a PermissionList renders without the host-page fact`)
        .toMatch(/hostUi=\{consentHostUi\(/)
    }
    // The one pre-install disclosure (the install dialog and the Store detail panel both render
    // it) and the installed-app panel. A third that forgets must fail this, not pass vacuously.
    expect(renders.map((r) => r.rel).sort()).toEqual(['pages/apps/AppsSection.tsx', 'pages/apps/installConsent.tsx'])
  })

  it('the pre-install disclosure reads the fact from the disclosure it renders', () => {
    // For the dialog that is the SERVER'S review of the bytes, so a registry listing whose
    // catalog row ships `hasUI: false` (its manifest unread) is still disclosed as what it is.
    expect(code('pages/apps/installConsent.tsx')).toMatch(/hostUi=\{consentHostUi\(disclosure\)\}/)
  })

  it('the onboarding step renders no disclosure of its own — the Store dialog is its consent', () => {
    // This card once carried its own copy of the Store's disclosure — with the hide-when-empty
    // gate issue 614 had removed from the Store, so an app declaring nothing rendered no
    // disclosure at all, and #492's row with it. It now opens the Store's dialog, so it cannot
    // disclose less than the Store does.
    const onboarding = code('app/onboarding/EssentialsStep.tsx')
    expect(onboarding).toMatch(/useAppInstall\(/)
    expect(onboarding).not.toMatch(/<(?:PermissionList|CronConsentList|AppDisclosureView|ScanReport)\b/)
  })

  it('no Store row fabricates the answer it is about to disclose', () => {
    // The defect this catches was invisible to every rail above and to typecheck, and was
    // found only by driving the real Store: `storeUniverse` normalised each catalog entry
    // with `hasUI: false` — a placeholder from when the field existed for installed apps
    // only — so `consentHostUi` was handed a hard-coded answer and the panel for an app
    // that DOES ship a UI read "Runs in this dashboard page: no". Counting call sites
    // proves the fact is passed; this proves it is not invented on the way.
    const src = code('pages/apps/AppsSection.tsx')
    expect(src).not.toMatch(/hasUI:\s*(?:false|true)\b/)
    expect(src).toMatch(/hasUI: Boolean\(e\.hasUI\)/)
  })

  it('consentHostUi is the one authority, and it declines to guess', () => {
    const consent = code('pages/apps/installConsent.tsx')
    expect(consent).toMatch(/export function consentHostUi/)
    expect(consent).toMatch(/if \(!a\) return undefined/)
    // The two facts stay separate on the wire AND in the reading.
    expect(consent).toMatch(/page: Boolean\(a\.hasUI\), components: Boolean\(a\.uiComponents\)/)
  })
})

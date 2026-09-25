import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Issue 614: absent permissions are DISCLOSED, not hidden ─────────────────────────
//
// The consent panel gated its Permissions section on non-empty permissions, so the 33
// of 36 Store apps that declare no block rendered NO section at all — "asked for
// nothing" was indistinguishable from silence, and PermissionList's own honest empty
// copy ("None — this app is granted no gateway capability") was unreachable from the
// Store. The gate now keys on consentKnown (was a manifest actually read?): a
// declared-none manifest reaches PermissionList's disclosure, while a registry
// pointer — whose manifest isn't read until its install is reviewed — says the
// permissions aren't known YET instead of pretending they're none.

const SRC = join(process.cwd(), 'src')
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (f: string) => strip(readFileSync(join(SRC, 'pages/apps', f), 'utf8'))

function productionFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    if (statSync(p).isDirectory()) return n === 'test' ? [] : productionFiles(p)
    return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : []
  })
}

describe('install consent discloses absent permissions (issue 614)', () => {
  it('the Store detail panel keys on disclosureOf, not permissions-emptiness', () => {
    const src = read('AppsSection.tsx')
    expect(src).toMatch(/const disclosure = disclosureOf\(item\)/)
    expect(src).toMatch(/\{disclosure \? \(\s*<AppDisclosureView disclosure=\{disclosure\}/)
    // The old hide-when-empty gate is gone.
    expect(src).not.toMatch(/item\.permissions && Object\.keys\(item\.permissions\)\.length > 0/)
  })

  it("a registry pointer says the permissions aren't known yet, and when they will be", () => {
    const src = read('AppsSection.tsx')
    expect(src).toMatch(/not known yet/i)
    expect(src).toMatch(/read when\s+you choose Install/)
    expect(src).toMatch(/You will see everything it gets before anything is installed/)
  })

  it('PermissionList still owns the declared-none copy (the branch this fix makes reachable)', () => {
    expect(read('installConsent.tsx')).toMatch(/None — this app is granted no gateway capability/)
  })

  // ── The install path never faces the question ──────────────────────────────────────
  //
  // Issue 614 was first closed on the Store panel's sibling, the install modal, whose guard
  // could not express the distinction: `to_dict` is `asdict`, so the wire ships
  // `permissions: {}` for a registry pointer too, `{}` is truthy, and the pointer took the KNOWN
  // branch and asserted "granted no gateway capability" about a manifest nobody had read. The
  // install dialog now discloses the SERVER'S reading of the staged bytes, which exists for a
  // pointer as much as for a local card — so no catalog row reaches it at all, and
  // `disclosureOf` is left as the one reader of the flag, for the one surface that still shows
  // a catalog row's grants before any review.

  it('the install dialog discloses the review, never a catalog row', () => {
    const consent = read('installConsent.tsx')
    expect(consent).toMatch(/r\.disclosure && <AppDisclosureView disclosure=\{r\.disclosure\}/)
    // …and what it is handed to review is a SOURCE, not a catalog row.
    expect(consent).toMatch(/api\.previewApp\(target\.source, target\.update\)/)
  })

  it('disclosureOf is the one authority for known-vs-not-known', () => {
    const consent = read('installConsent.tsx')
    expect(consent).toMatch(/export function disclosureOf/)
    expect(consent).toMatch(/if \(!entry\?\.consentKnown\) return undefined/)
    // No other production file reads the flag, so no surface can re-derive the distinction.
    const readers = productionFiles(SRC)
      .filter((abs) => /\.consentKnown\b/.test(strip(readFileSync(abs, 'utf8'))))
      .map((abs) => abs.slice(SRC.length + 1))
    expect(readers).toEqual(['pages/apps/installConsent.tsx'])
  })
})

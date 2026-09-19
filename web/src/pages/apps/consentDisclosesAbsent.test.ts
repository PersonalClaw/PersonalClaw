import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Issue 614: absent permissions are DISCLOSED, not hidden ─────────────────────────
//
// The consent panel gated its Permissions section on non-empty permissions, so the 33
// of 36 Store apps that declare no block rendered NO section at all — "asked for
// nothing" was indistinguishable from silence, and PermissionList's own honest empty
// copy ("None — this app is granted no gateway capability") was unreachable from the
// Store. The gate now keys on consentKnown (was a manifest actually read?): a
// declared-none manifest reaches PermissionList's disclosure, while a registry
// pointer — whose manifest isn't fetched until install — says the permissions aren't
// known YET instead of pretending they're none.

const read = (f: string) =>
  readFileSync(join(process.cwd(), 'src/pages/apps', f), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')

describe('install consent discloses absent permissions (issue 614)', () => {
  it('the consent section keys on consentKnown, not permissions-emptiness', () => {
    const src = read('AppsSection.tsx')
    expect(src).toMatch(/item\.consentKnown \?/)
    // The old hide-when-empty gate is gone from the consent panel.
    expect(src).not.toMatch(/item\.permissions && Object\.keys\(item\.permissions\)\.length > 0 &&\s*\(\s*<PermissionList/)
  })

  it('a known manifest reaches PermissionList even when it declared nothing', () => {
    const src = read('AppsSection.tsx')
    const gate = src.slice(src.indexOf('item.consentKnown ?'))
    expect(gate).toMatch(/<PermissionList perms=\{item\.permissions \?\? \{\}\}/)
  })

  it("a registry pointer says the permissions aren't known yet", () => {
    const src = read('AppsSection.tsx')
    expect(src).toMatch(/not known yet/i)
    expect(src).toMatch(/read at install/i)
  })

  it("PermissionList still owns the declared-none copy (the branch this fix makes reachable)", () => {
    const consent = read('installConsent.tsx')
    expect(consent).toMatch(/None — this app is granted no gateway capability/)
  })

  // ── The SECOND reader (the install path) ──────────────────────────────────────────
  //
  // Issue 614 was closed on the Store panel's sibling, `ConsentModal`, whose guard is
  // `permissions ? … : "could not read this app's declared permissions"`. That guard
  // cannot express the distinction on its own: `to_dict` is `asdict`, so the wire ships
  // `permissions: {}` for a registry pointer too, and `{}` is truthy — the pointer took
  // the KNOWN branch and asserted "granted no gateway capability" about a manifest
  // nobody had read. `consentPermissions` is the one place the `undefined` the modal
  // documents actually gets derived, and every caller must route through it.

  it('every ConsentModal caller derives its grants through consentPermissions', () => {
    const callers = [
      join('src/pages/apps', 'AppsSection.tsx'),
      join('src/app/onboarding', 'EssentialsStep.tsx'),
    ]
    let seen = 0
    for (const rel of callers) {
      const src = readFileSync(join(process.cwd(), rel), 'utf8')
      for (const m of src.matchAll(/<ConsentModal\b[\s\S]{0,600}?\/?>/g)) {
        seen += 1
        expect(m[0], `${rel}: a ConsentModal passes permissions straight from the row`)
          .toMatch(/permissions=\{consentPermissions\(/)
      }
    }
    // The module header names FOUR callers; a fifth that forgot must fail this, not pass
    // it vacuously.
    expect(seen).toBe(4)
  })

  it('consentPermissions is the one authority for known-vs-not-known', () => {
    const consent = read('installConsent.tsx')
    expect(consent).toMatch(/export function consentPermissions/)
    expect(consent).toMatch(/if \(!entry\?\.consentKnown\) return undefined/)
  })
})

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { scanInertUtilities, loadUtilityOracle } from './consistencyAudit.report'

// ── Inert-utility rail (issue #556) ─────────────────────────────────────────
// The fourth consistency rail, alongside token-lint (tokenLint.test.ts: raw
// hex/px — a wrong VALUE), primitive-adoption (primitiveAdoption.test.ts:
// bespoke chrome) and the axe scan in e2e/. This one catches a class that names
// a token which does not exist: Tailwind emits NO rule for it, so the style is
// silently ABSENT and the element renders at whatever it inherited. Nothing else
// can see it — TypeScript never looks inside a className string, and the other
// rails only inspect values and elements.
//
// ConflictPanel.tsx is why this exists: it shipped `text-muted` (x6),
// `border-border`, `bg-accent-subtle` and `text-accent`, none of which is a
// token, so the whole panel had no visual hierarchy and no one noticed.
//
// The oracle is Tailwind itself compiling against design/tokens.css (see
// scanInertUtilities), so the verdict matches the shipped bundle by
// construction — variants, opacity modifiers, arbitrary values and non-color
// utilities on the same prefixes all resolve exactly as they do in the build.
//
// Runs in the existing CI `web` job (vitest) — no browser and no build needed.

interface Allowlist { allow: Record<string, string[]> }

/** Which token family a dead utility was reaching for, so the failure names the
 *  right scale. Reporting `rounded-m` as "no --color-* token" sent a reader to
 *  the wrong half of tokens.css — the whole cost of an inert utility is that it
 *  is silent, so a misleading diagnostic keeps it silent for one round longer. */
function tokenFamilyFor(utility: string): string {
  const base = utility.replace(/^(?:[\w@/[\]().-]+:)+/, '').replace(/^!?-?/, '')
  if (base.startsWith('rounded-')) return '--radius-*'
  if (base.startsWith('gap-')) return '--spacing-*'
  return '--color-*'
}

function loadAllowlist(): Allowlist {
  const raw = readFileSync(join(process.cwd(), 'src/design/inertUtilities.allowlist.json'), 'utf8')
  const j = JSON.parse(raw) as Allowlist
  return { allow: j.allow ?? {} }
}

describe('inert-utility rail (a text-/bg-/border-/rounded-/gap- class must emit CSS)', () => {
  it('the oracle agrees with the design tokens (it can tell live from inert)', async () => {
    // Guards the guard: if the loader ever silently produced an empty design
    // system, every candidate would look inert and the allowlist assertions
    // below would still pass by accident. Assert both directions on the exact
    // pairs from #556 — the token that exists and the name that never did.
    const isLive = await loadUtilityOracle()
    for (const live of ['text-on-surface-low', 'border-outline-variant', 'text-primary', 'bg-surface']) {
      expect(isLive(live), `${live} is a real token and must compile`).toBe(true)
    }
    for (const dead of ['text-muted', 'border-border', 'bg-accent-subtle', 'text-accent']) {
      expect(isLive(dead), `${dead} has no token and must NOT compile`).toBe(false)
    }
    // Non-color utilities sharing the prefixes, variants, opacity modifiers and
    // arbitrary values must all read as live — these are the false positives
    // that would make the rail unusable.
    for (const live of [
      'text-center', 'border-t', 'bg-transparent', 'bg-white', 'text-sm',
      'bg-gradient-to-br', 'text-on-surface-low/40', 'hover:bg-primary/15',
      'group-hover/dock:text-on-surface', 'text-[0.75rem]', 'border-l-[3px]',
    ]) {
      expect(isLive(live), `${live} must not be reported as inert`).toBe(true)
    }
    // The rounded-*/gap-* families, added to SCANNED_PREFIX after two whole families of
    // dead CSS shipped unnoticed: `gap-2xs` at 49 sites (no `--spacing-2xs`, so every
    // gap collapsed to 0) and `rounded-m` at 12 (the radius scale spells 12px `md`).
    // Both directions are asserted on those exact names, because the two scales do NOT
    // share spellings — `gap-m` is live while `rounded-m` is dead, and `gap-2xs` is dead
    // while `gap-2xl` is live. That asymmetry is the whole trap, so it is pinned here.
    for (const live of [
      'rounded-md', 'rounded-lg', 'rounded-pill', 'rounded-squircle', 'rounded-l-md',
      'rounded-none', 'gap-xs', 'gap-s', 'gap-m', 'gap-2xl', 'gap-1', 'gap-x-2',
      'sm:gap-xs',
    ]) {
      expect(isLive(live), `${live} must not be reported as inert`).toBe(true)
    }
    for (const dead of ['rounded-m', 'gap-2xs', 'sm:gap-2xs', 'rounded-mdd', 'gap-nope']) {
      expect(isLive(dead), `${dead} has no token and must NOT compile`).toBe(false)
    }
  })

  it('no NEW inert utility outside the shrinking allowlist', async () => {
    const { allow } = loadAllowlist()
    const offenders: Record<string, string[]> = {}
    for (const hit of await scanInertUtilities()) {
      if (allow[hit.file]?.includes(hit.base)) continue
      offenders[hit.file] ??= []
      offenders[hit.file].push(`${hit.line}: ${hit.utility} — no ${tokenFamilyFor(hit.utility)} token; emits NO CSS`)
    }
    expect(
      offenders,
      'These utilities compile to nothing, so their styling is silently absent. ' +
        'Use a token that exists (see design/tokens.css), or add the token if it is ' +
        'genuinely new. Note the scales do NOT share spellings: spacing is ' +
        'xs/s/m/l/xl/2xs/2xl/3xl while radius is xs/sm/md/lg/lgi/xl/xli/2xl — so ' +
        '`rounded-m` is dead even though `gap-m` is live:\n' +
        JSON.stringify(offenders, null, 2),
    ).toEqual({})
  })

  it('allowlist has no stale entries (a fixed utility must leave it)', async () => {
    const { allow } = loadAllowlist()
    const live = await scanInertUtilities()
    const stale: string[] = []
    for (const [file, utilities] of Object.entries(allow)) {
      for (const base of utilities) {
        if (!live.some((h) => h.file === file && h.base === base)) stale.push(`${file} → ${base}`)
      }
    }
    expect(
      stale,
      `These allowlisted utilities are no longer inert (fixed or gone) — remove them from ` +
        `src/design/inertUtilities.allowlist.json in the same commit:\n${stale.join('\n')}`,
    ).toEqual([])
  })
})

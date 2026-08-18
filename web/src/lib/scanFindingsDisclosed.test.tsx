import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SCAN_FINDINGS_SHOWN, hiddenFindingsNote } from './scanFindings'
import { ScanReport } from '../pages/apps/installConsent'

// ── A truncated security-findings list says how much it is hiding ───────────────────────────────
//
// Two surfaces render `scan.findings` for the same decision — "do I trust this enough to install
// it?" — and both stopped at eight with nothing said. On `apps/installConsent` there was no total
// anywhere on the screen, so eight findings read as ALL the findings: a user consenting to an app
// with fourteen was shown eight and told nothing. `skills/MarketplaceDetail` states the true count
// in its heading ("Security scan flagged 14 warning(s)") and then lists eight, so the number was
// honest while the list quietly was not.
//
// 🪤 THE CAP IS NOT THE DEFECT. Bounding a findings list on a modal is a reasonable layout choice;
// the defect is silence about the bound. The fix keeps both caps and discloses them, from ONE
// constant — each file had chosen its own `8`, and two independently-picked limits drift.
//
// 🪤 THE FILE ALREADY NAMED THE BUG, again. `installConsent`'s own comment says the surface exists so
// "the scanner findings and the 'Install anyway' action are reachable without re-typing the source"
// and that a warning verdict "must show the same scanner findings" wherever the install started. A
// list that stops early with no note contradicts the reason the surface exists.

const SRC = join(process.cwd(), 'src')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

const CONSENT = ['pages/apps/installConsent.tsx', 'pages/skills/MarketplaceDetail.tsx']

describe('the residue sentence', () => {
  it('says nothing when nothing is hidden', () => {
    expect(hiddenFindingsNote(0)).toBeNull()
    expect(hiddenFindingsNote(SCAN_FINDINGS_SHOWN), 'exactly at the cap hides nothing').toBeNull()
    expect(hiddenFindingsNote(SCAN_FINDINGS_SHOWN - 1)).toBeNull()
  })

  it('counts the residue, not the total', () => {
    // The number a user needs is what is MISSING. "14 findings" is already in the heading; "+6 more"
    // is the fact the list alone cannot carry.
    expect(hiddenFindingsNote(SCAN_FINDINGS_SHOWN + 6)).toBe('+6 more findings not shown')
    expect(hiddenFindingsNote(SCAN_FINDINGS_SHOWN + 1), 'singular at one').toBe('+1 more finding not shown')
  })
})

describe('both consent surfaces disclose their cap', () => {
  it('neither hardcodes its own limit any more', () => {
    for (const rel of CONSENT) {
      const src = read(rel)
      expect(src, `${rel} must slice by the shared constant`).toMatch(/\.slice\(0, SCAN_FINDINGS_SHOWN\)/)
      // 🪤 Counted against the findings list specifically: these files legitimately slice OTHER
      // things (a trailing `*` off a permission pattern), so a blanket "no literal slice" would fail
      // on correct code.
      expect(src, `${rel} must not re-choose the limit`).not.toMatch(/findings\.slice\(\s*0\s*,\s*\d/)
      expect(src, `${rel} must import the shared rules`).toMatch(
        /import \{ SCAN_FINDINGS_SHOWN, hiddenFindingsNote \} from '(\.\.\/)+lib\/scanFindings'/,
      )
    }
  })

  it('each renders the residue, gated on there being one', () => {
    for (const rel of CONSENT) {
      const src = read(rel)
      expect(src, `${rel} must render the note`).toMatch(/hiddenFindingsNote\([\w.?]+\.findings\.length\)/)
      // Gated, not unconditional: an always-rendered empty element is a blank row on a clean scan.
      expect((src.match(/hiddenFindingsNote\(/g) ?? []).length,
        `${rel}: once to decide, once to render`).toBeGreaterThanOrEqual(2)
    }
  })

  it('the app-install report states the total, since nothing else on that screen did', () => {
    const src = read('pages/apps/installConsent.tsx')
    expect(src, 'the verdict line carries the count').toMatch(
      /Security scan: \{v\}[\s\S]{0,140}scan\.findings\.length\} finding\$\{/,
    )
  })

  it('the skill surface keeps the total it already had', () => {
    // It was never wrong — only incomplete. This must not be "tidied" into the other shape.
    const src = read('pages/skills/MarketplaceDetail.tsx')
    expect(src).toMatch(/Security scan flagged \$\{blocked\.scan\?\.findings\?\.length \?\? 0\} warning/)
  })

  it('no OTHER surface lists scan findings without the note — the census', () => {
    // Population-keyed, so a third consent surface cannot arrive silent. Anything that truncates a
    // findings list has to route through the shared rules.
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n: string) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : []
      })
    // 🪤 Keyed on `scan.findings` — the SECURITY-scan shape — not on the word "findings". A looser
    // sweep flagged `chat/SdlcProgressCard` and `code/CodeCockpitPage`, which slice an SDLC loop's
    // REVIEW findings to show the most recent few (`slice(-3)`, `slice(-CAP)`). That is a recent-
    // activity tail on a live progress surface, not a security list a user is consenting against —
    // a different concept wearing the same noun, and "fixing" it would have been a non-defect.
    const renderers = walk(SRC)
      .map((abs) => ({ rel: abs.replace(SRC + '/', ''), src: strip(readFileSync(abs, 'utf8')) }))
      .filter(({ src }) => /scan\??\.findings[\s\S]{0,40}\.slice\(\s*0/.test(src) && src.includes('.map('))
    expect(renderers.length, 'the census must find the consent surfaces').toBeGreaterThanOrEqual(2)
    const silent = renderers.filter(({ src }) => !src.includes('hiddenFindingsNote')).map((r) => r.rel)
    expect(silent, 'a truncated findings list must say how many it hides').toEqual([])
  })
})

describe('what the consent screen actually reads', () => {
  const scanOf = (n: number) => ({
    verdict: 'warning' as const,
    findings: Array.from({ length: n }, (_, i) => ({
      rule: `rule_${i}`, severity: 'warning' as const, path: `f${i}.py`, evidence: '',
    })),
  })

  it('a 14-finding scan says 14, lists 8, and admits the other 6', () => {
    // The rendered sentence, not the source. This is the whole point of the change: a user deciding
    // whether to install must be able to tell the list is not the whole list.
    render(<ScanReport scan={scanOf(14) as never} />)
    expect(screen.getByText(/Security scan: warning/)).toBeTruthy()
    expect(screen.getByText(/14 findings/), 'the total').toBeTruthy()
    expect(screen.getAllByText(/^rule_/).length, 'eight listed').toBe(SCAN_FINDINGS_SHOWN)
    expect(screen.getByText('+6 more findings not shown'), 'and the residue').toBeTruthy()
  })

  it('a scan inside the cap says neither a residue nor a lie', () => {
    render(<ScanReport scan={scanOf(3) as never} />)
    expect(screen.getByText(/3 findings/)).toBeTruthy()
    expect(screen.queryByText(/more findings? not shown/), 'nothing is hidden').toBeNull()
  })

  it('a clean scan carries no count at all — there is nothing to count', () => {
    render(<ScanReport scan={{ verdict: 'clean', findings: [] } as never} />)
    expect(screen.getByText(/Security scan: clean/)).toBeTruthy()
    expect(screen.queryByText(/finding/), 'no "0 findings" noise on a clean install').toBeNull()
  })
})

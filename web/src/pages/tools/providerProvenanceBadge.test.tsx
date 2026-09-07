import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TRUST_TIER_LABEL } from '../../lib/trustTier'

// ── The Tools page must not call an installed community bundle `built-in` (#2627) ─────────────────
//
// The install dialog carefully discloses "Unsigned — community tier" and the user consents to THAT.
// The Tools page then badged the same bundle `built-in` — the identical word core's own first-party
// providers get. The badge was a binary, `providerLocked ? 'platform' : 'built-in'`, and an installed
// community app is exactly the shape that falls through it: `native` kind, not locked.
//
// 🔑 WHY THIS IS NOT COSMETIC. The tiers exist to inform consent. The Tools page is where a user
// later goes to audit what is running and where it came from — and at that moment the tier was gone
// and the answer was wrong in the REASSURING direction. Compare the adjacent handling in the same
// component: MCP groups get a live health dot with a tone and a detail tooltip, so provenance
// plainly mattered elsewhere on this page.
//
// 🪤 THREE STATES ARE ASSERTED, NOT ONE. A fix that renders every native group as its tier trades one
// wrong label for another: a genuine core provider must still read `built-in` and a locked one must
// still read `platform`. Both are pinned below, from the same mount as the new state.
//
// 🪤 AND THE TWO SURFACES ARE ASSERTED AGAINST THE SHARED MAP, NOT AGAINST TWO LITERALS. Two
// independent literals *is* the defect — it is how the badge and the dialog came to describe the same
// bytes differently — so a test that hardcoded "community tier" on both sides would pass on the day
// they drift again. Everything here reads `TRUST_TIER_LABEL`, and the source guard at the bottom
// pins that neither surface has re-grown a literal of its own.

const LOCKED = 'personalclaw-filesystem'  // ToolsPage's LOCKED_NATIVE_PROVIDER

const tool = (name: string, provider: string, tier: string | undefined) => ({
  name, provider, description: `${name} description`, parameters: {},
  requires_approval: false, risk_level: 'safe', ...(tier === undefined ? {} : { tier }),
})

/** Three native providers on one page: the locked platform one, a core one, and an installed
 *  community bundle. Deliberately ONE mount — the three states have to be simultaneously true. */
const THREE_PROVIDERS = [
  tool('read_file', LOCKED, 'builtin'),
  tool('memory_search', 'personalclaw-memory', 'builtin'),
  tool('spec_outline', 'spec-builder', 'community'),
]

function mockApi(tools: unknown[]) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({ tools, load_failures: [] }),
      mcpServers: () => Promise.resolve([]),
      importableMcp: () => Promise.resolve([]),
      mcpPoolStats: () => Promise.resolve({ available: false }),
      toolGroups: () => Promise.resolve(null),
    },
  }))
}

async function mount(tools: unknown[]) {
  mockApi(tools)
  const { ToolsPage } = await import('./ToolsPage')
  render(<ToolsPage query={{}} setQuery={() => {}} />)
  // Every group renders after the composed fetcher resolves; anchor on the last provider's tool.
  await waitFor(() => expect(screen.getByText(String((tools.at(-1) as { name: string }).name))).toBeInTheDocument())
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the Tools provenance badge has three states, derived from where the provider came from', () => {
  it('an installed community bundle reads its own tier and NEVER `built-in`', async () => {
    await mount(THREE_PROVIDERS)
    // The word the install dialog uses for the same bundle, from the one shared map.
    const community = TRUST_TIER_LABEL.community
    expect(screen.getAllByText(community).length, `the bundle is badged "${community}"`).toBeGreaterThan(0)
    // The whole point: the reassuring word must not appear for it. Asserted over the WHOLE page,
    // because the defect was a badge and a later one could be a tooltip or a second pill.
    const page = document.body.textContent || ''
    const builtInCount = page.split(TRUST_TIER_LABEL.builtin).length - 1
    // `built-in` is still on the page ONCE — for the genuine core provider below. What must not
    // happen is a second occurrence, which is what an installed bundle wrongly wearing it looks like.
    expect(builtInCount, `"${TRUST_TIER_LABEL.builtin}" belongs to core alone`).toBe(1)
  })

  it('a genuine core provider still reads `built-in`', async () => {
    await mount(THREE_PROVIDERS)
    const badge = screen.getByText(TRUST_TIER_LABEL.builtin)
    expect(badge).toBeInTheDocument()
    // …and it is the badge on the CORE group, not on the installed one. The group header and its
    // badge are siblings, so walk up to their shared row.
    const row = badge.parentElement
    expect(row?.textContent, 'the built-in badge sits beside the core provider').toContain('personalclaw-memory')
    expect(row?.textContent, 'not beside the installed bundle').not.toContain('spec-builder')
  })

  it('a provider-locked core provider still reads `platform`', async () => {
    await mount(THREE_PROVIDERS)
    const badge = screen.getByText('platform')
    expect(badge.parentElement?.textContent, 'the platform badge sits beside the locked provider').toContain(LOCKED)
  })

  it('an UNKNOWN tier renders no provenance badge at all — never `built-in`', async () => {
    // Legacy or stale-cached rows carry no `tier`. Absence must not read as
    // shipped-with-the-product (that is the defect) and must not cry wolf as `community tier`
    // about the platform's own providers either. So the honest render is nothing.
    await mount([tool('mystery_tool', 'unknown-provider', undefined)])
    const page = document.body.textContent || ''
    expect(page, 'the provider is still listed').toContain('unknown-provider')
    expect(page, 'but no tier is claimed for it').not.toContain(TRUST_TIER_LABEL.builtin)
    expect(page).not.toContain(TRUST_TIER_LABEL.community)
    expect(page).not.toContain('platform')
  })
})

describe('the Tools badge and the install dialog spell the same tier the same way', () => {
  it('both surfaces render the string the shared map defines for `community`', async () => {
    // 🔑 THIS IS THE TEST THAT STOPS THE DRIFT RECURRING. It asserts nothing about the words
    // themselves — it takes them from `TRUST_TIER_LABEL` and demands both surfaces agree with it.
    // Reword the map and both surfaces move together; reword one surface and this reds.
    const expected = TRUST_TIER_LABEL.community

    await mount(THREE_PROVIDERS)
    const badgeText = screen.getAllByText(expected)
    expect(badgeText.length, 'the Tools badge').toBeGreaterThan(0)
    const toolsPageText = document.body.textContent || ''

    // The install dialog's signature row, over the SAME tier the catalog reported for the bundle.
    const { ScanReport } = await import('../apps/installConsent')
    const { container } = render(
      <ScanReport scan={{
        verdict: 'warning', tier: 'community', findings: [],
        signature: { state: 'unsigned', signer: '', reason: '' },
      } as never} />,
    )
    const dialogText = container.textContent || ''
    expect(dialogText, 'the install dialog').toContain(expected)
    // Belt and braces: the exact phrase the dialog composes, present verbatim on both surfaces.
    expect(dialogText).toContain(`Unsigned — ${expected}`)
    expect(toolsPageText).toContain(expected)
  })
})

describe('neither surface keeps a tier literal of its own', () => {
  // Source guards. The runtime assertions above pass just as well against two hardcoded literals
  // that happen to agree today; these are what make the SHARED source load-bearing.
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const read = (rel: string) => strip(readFileSync(join(process.cwd(), rel), 'utf8'))

  it('ToolsPage derives the badge from the shared map, not from a `built-in` literal', () => {
    const code = read('src/pages/tools/ToolsPage.tsx')
    // A quoted, standalone `built-in` is the literal the defect was made of. (The empty-state
    // sentence mentions "built-in actions" mid-prose, which is copy, not a badge.)
    expect(code, 'no standalone built-in literal').not.toMatch(/(['"`])built-in\1/)
    expect(code, 'the badge reads the shared map').toMatch(/trustTierLabel\(/)
  })

  it('installConsent derives the signature row from the shared map, not from a `community tier` literal', () => {
    const code = read('src/pages/apps/installConsent.tsx')
    expect(code, 'no hardcoded tier phrase').not.toMatch(/community tier/)
    expect(code, 'the row reads the shared map').toMatch(/trustTierLabel\(/)
  })
})

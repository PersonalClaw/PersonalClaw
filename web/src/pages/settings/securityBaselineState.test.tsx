import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, render, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── SH-10 · the Security panel says WHICH denylist is in force, and whether it drifted ──────
//
// `/api/security/denied-commands` used to return two bare arrays, so the panel could show 112
// patterns without being able to say where they came from, whether they still matched what
// shipped, or how many of the user's own entries actually changed anything. It now carries a
// `baseline` block (version, sha256, enforced count, `verified`) and `user_additions`.
//
// 🔑 THREE CLAUSES HERE ARE TRIVIAL TO FAKE, so each is pinned against the fake:
//
//   • "a tamper fixture flips the indicator" — a hardcoded green chip reads identically on the
//     happy path. The diverged fixture asserts the ROLE and the copy both change.
//   • "'N user additions' shown" — `user.length` is the obvious implementation and it is wrong:
//     the server dedupes an entry equal to a built-in, so a 3-entry config can add ONE pattern.
//   • "read-only" — asserted as the ABSENCE of any control inside the baseline region, measured
//     by role, not by reading the JSX.
//
// 🪤 COLD sessionStorage per test. `useQuery(..., { persist: true })` seeds from
// sessionStorage, and the settings hub writes a substituted `null` under the SAME
// `settings:security` key — a warm cache masks both the failure branch and the fresh payload.
//
// 🔑 THE WORDING IS PART OF THE FEATURE. The chip says the baseline "matches what shipped", never
// "tamper-proof" or "secure": the digest is anti-drift and anti-LLM-tamper, not anti-owner
// (docs/security/threat-model.md). The last test pins that, because an enthusiastic copy edit to
// "verified secure" would be a claim the mechanism cannot support.

const PANEL = join(process.cwd(), 'src/pages/settings/SecurityPanel.tsx')

const SHA = '2b7db3c6d0be84890aff1ad3bf2bcbcbf3bdf5cb6b991079734db1ee10c6e872'
const BUILTIN = Array.from({ length: 112 }, (_, i) => `deny-pattern-${i}`)

const VERIFIED = { version: 1, sha256: SHA, count: 112, verified: true, detail: '' }
const DIVERGED = {
  version: 1, sha256: SHA, count: 112, verified: false,
  detail: 'packaged file no longer matches the verified baseline',
}

type Over = { baseline?: object; user?: string[]; user_additions?: number }

const payload = (over: Over = {}) => ({
  builtin: BUILTIN, user: [], baseline: VERIFIED, user_additions: 0, ...over,
})

async function mount(opts: { denied?: Over | 'reject'; stats?: 'reject' } = {}) {
  vi.resetModules()
  sessionStorage.clear()
  vi.doMock('../../lib/api', () => ({
    api: {
      securityStats: () => opts.stats === 'reject'
        ? Promise.reject(new Error('probe-induced 500 on /api/security/stats'))
        : Promise.resolve({
          denied_commands: 112, suspicious_patterns: 20, tool_schemas: 30, redaction_paths: 5,
        }),
      deniedCommands: () => opts.denied === 'reject'
        ? Promise.reject(new Error('probe-induced 500 on /api/security/denied-commands'))
        : Promise.resolve(payload(opts.denied)),
      securityEgress: () => Promise.resolve({ allow_hosts: [], deny_hosts: [], allow_private: false }),
      setUserDeniedCommands: () => Promise.resolve({}),
      setSecurityEgress: () => Promise.resolve({}),
      // DC-2's desktop-capabilities section renders inside this SAME panel. This is a
      // TOTAL module mock (no `...actual`), so an unstubbed read is `undefined` and the
      // panel throws before the baseline region ever renders. Not-connected is the right
      // default here: this file is about the denylist, not the shell.
      desktopState: () => Promise.resolve({
        connected: false, shell: null, capabilities: {}, registered_at: '', last_seen: '',
      }),
      // SH-2's credential-storage section renders inside this SAME panel, and its read is
      // deliberately BARE (a swallowed failure there would render as "0 credentials"), so an
      // unstubbed read rejects and the section shows its error branch. `.env` with nothing
      // pending is the right default here: this file is about the denylist.
      credentialStore: () => Promise.resolve({
        migration: 'credentials_to_keychain', backend: 'dotenv', requested: 'dotenv',
        blocked: true, pending_keys: [], pending: 0, keychain_keys: 0,
        rollback_available: false, snapshot_name: '.env.pre-keychain', verified: true,
        verification: { checked: 0, missing: [], still_in_dotenv: [] },
      }),
    },
  }))
  const { SecurityPanel } = await import('./SecurityPanel')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<SecurityPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the baseline indicator renders the verified state', () => {
  it('names the version, the enforced count and the digest in the a11y tree', async () => {
    const r = await mount()
    // Measured as a NAME, not as text: `status` takes no name from its content, so without the
    // explicit label this chip would be an unnamed live region — visible, but announced as
    // nothing at all.
    const chip = r.getByRole('status', {
      name: /Baseline v1 matches what shipped: 112 patterns verified against the release sha256/,
    })
    expect(chip.textContent).toContain('Baseline v1 matches what shipped')
    expect(chip.textContent).toContain('112 patterns')
    expect(chip.textContent, 'the digest is shown truncated').toContain(SHA.slice(0, 12))
    expect(r.queryByRole('alert'), 'a verified baseline is not an alert').toBeNull()
  })

  it('a tamper fixture flips the indicator — role AND copy', async () => {
    const r = await mount({ denied: { baseline: DIVERGED } })

    expect(r.queryByRole('status'), 'the quiet form must be gone').toBeNull()
    const chip = r.getByRole('alert', {
      name: /Baseline v1 does not match what shipped: packaged file no longer matches the verified baseline\. The 112 verified patterns are still enforced\./,
    })
    expect(chip.textContent).toContain('does NOT match what shipped')
    // The panel must not go quiet about what is protecting the user right now: the diverged file
    // is reported, never adopted, so the enforced count is unchanged and says so.
    expect(chip.textContent).toContain('112 verified patterns are still enforced')
    expect(r.getAllByText('deny-pattern-0').length, 'the baseline still renders').toBe(1)
  })
})

describe("'N user additions' counts what actually widened the set", () => {
  it('shows the addition count, not the length of the config list', async () => {
    // Three entries in config; the server deduped two that equalled a built-in.
    const r = await mount({
      denied: { user: ['deny-pattern-0', 'mine-a', 'deny-pattern-7'], user_additions: 1 },
    })
    const text = r.container.textContent ?? ''

    expect(text).toContain('1 user addition on top of the baseline')
    expect(text, 'the naive count would say 3').not.toContain('3 user additions')
    // The shadowed remainder is named rather than silently dropped — otherwise the panel shows
    // three rows and claims one addition with no explanation for the gap.
    expect(text).toContain('2 of your 3 entries already match a baseline pattern and add nothing')
  })

  it('pluralises, and stays silent about shadowing when there is none', async () => {
    const r = await mount({ denied: { user: ['mine-a', 'mine-b'], user_additions: 2 } })
    const text = r.container.textContent ?? ''
    expect(text).toContain('2 user additions on top of the baseline')
    expect(text).not.toContain('already match a baseline pattern')
  })

  it('reads zero as zero', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('0 user additions on top of the baseline')
  })
})

describe('the baseline is read-only in the UI', () => {
  it('the baseline region contains no control that could submit a change', async () => {
    const r = await mount({ denied: { user: ['mine-a'], user_additions: 1 } })
    const region = r.getByRole('group', { name: /^Baseline shell denylist patterns \(112\), read-only$/ })

    // Measured by role INSIDE the region — the point is the ABSENCE of an affordance, and a JSX
    // read cannot prove that the way the a11y tree can.
    for (const role of ['button', 'textbox', 'checkbox', 'combobox', 'switch'] as const) {
      expect(within(region).queryAllByRole(role),
        `a ${role} inside the baseline region would be a write path`).toEqual([])
    }
    expect(region.querySelectorAll('input, button, select, textarea, [contenteditable]').length).toBe(0)

    // 🪤 THE VACUITY GUARD. A region query that finds nothing looks identical to a region that is
    // correctly read-only, so the same queries must find the user list's controls — proving the
    // scan can see a write path when there is one. This is a read-only baseline, not a frozen panel.
    expect(r.getByRole('button', { name: 'Remove mine-a' })).toBeTruthy()
    expect(r.getByRole('textbox', { name: 'Add a shell denylist pattern (regex)' })).toBeTruthy()
  })

  it('no write path addresses the baseline at all', async () => {
    // The panel's only denylist mutation is the user list. If a future edit wires an editable
    // baseline, the pattern below is what it would have to add.
    const src = readFileSync(PANEL, 'utf8')
    expect(src).toContain('api.setUserDeniedCommands(next)')
    expect(src, 'nothing here may write the baseline').not.toMatch(/set\w*Baseline|baseline:\s*\[/)
  })
})

describe('a failed read says so instead of showing an empty denylist', () => {
  it('the denylist failure renders an alert with the server message and a retry', async () => {
    // 🔑 THE WORST LIE THIS PANEL COULD TELL. `.catch(() => null)` rendered NOTHING for a failed
    // denied-commands read: no list, no message, no alert — indistinguishable from an instance
    // with nothing blocked.
    const r = await mount({ denied: 'reject' })

    const alert = r.getByRole('alert')
    expect(alert.textContent).toContain("Couldn't load your shell denylist patterns")
    expect(alert.textContent, "the server's own message must be on the page")
      .toContain('probe-induced 500 on /api/security/denied-commands')
    expect(r.getByRole('button', { name: /Retry/ })).toBeTruthy()
    expect(r.queryByText('deny-pattern-0'), 'and no fabricated list').toBeNull()
  })

  it('the gating stats failure replaces the panel rather than shimmering forever', async () => {
    const r = await mount({ stats: 'reject' })

    const alert = r.getByRole('alert')
    expect(alert.textContent).toContain("Couldn't load your security settings")
    expect(alert.textContent).toContain('probe-induced 500 on /api/security/stats')
    // A substituted `null` passed the old `if (!s)` gate straight into the skeleton, which then
    // never resolved: an `aria-busy` shimmer that means "loading" forever.
    expect(r.container.querySelector('[aria-busy="true"]')).toBeNull()
    expect(r.container.textContent, 'and no fabricated zero counts').not.toContain('Denied commands')
  })

  it('both reads are bare, so the rejection can reach the hook at all', async () => {
    const src = readFileSync(PANEL, 'utf8')
    for (const call of ['api.securityStats()', 'api.deniedCommands()']) {
      const line = src.split('\n').find((l) => l.includes(call)) ?? ''
      expect(line, `${call} must not swallow its rejection`).not.toMatch(/\.catch\(\(\)\s*=>/)
    }
  })
})

describe('each Add button explains ITS OWN empty draft', () => {
  it('the denylist button asks for a pattern and the host button asks for a host', async () => {
    // Found while reading this file for SH-10: the two `unavailableWhen` reasons were exactly
    // SWAPPED — the shell-denylist Add said "Enter a host first" and the egress Add said "Enter a
    // pattern first". Both are `title`-named, so the wrong noun is what a hover and a screen reader
    // both got. Pinned by role name so a future re-swap cannot pass.
    const r = await mount()
    // 🪤 The reason lands in `title`, and these buttons have CONTENT ("Add"), so content wins the
    // accessible name — all three announce "Add". The title is what a hover and a description read,
    // so that is what gets measured; the three Adds are told apart by which section they sit in.
    const titles = [...r.container.querySelectorAll('button[title]')].map((b) => b.getAttribute('title'))
    expect(titles.filter((t) => t === 'Enter a pattern first').length, 'one denylist Add').toBe(1)
    expect(titles.filter((t) => t === 'Enter a host first').length, 'two host Adds (allow + deny)').toBe(2)
  })
})

describe('the panel does not overclaim what the digest proves', () => {
  it('says "matches what shipped" and never "tamper-proof" or "secure"', async () => {
    // The mechanism is anti-drift and anti-LLM-tamper, NOT anti-owner: whoever can edit the
    // installed package before startup owns the baseline. Copy that promised more would be the
    // panel's own lie, and this surface is the one place a self-hoster reads that promise.
    const text = (await mount()).container.textContent ?? ''
    expect(text).not.toMatch(/tamper-proof|tamperproof|cannot be changed|guaranteed/i)
    expect(text).toContain('matches what shipped')
    expect(text, 'the limitation is stated on the surface, not only in docs/')
      .toContain('Anyone who can edit the installed package before PersonalClaw starts owns the baseline')
  })
})

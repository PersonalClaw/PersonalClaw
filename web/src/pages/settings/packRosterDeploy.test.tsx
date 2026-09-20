import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, within } from '@testing-library/react'
import type { InstalledPackRec, PackRosterDeployRec } from '../../lib/api'

// ── The one-click roster deploy control (AGENT-PACKS §4.2, AP-4) ───────────────
//
// The backend `POST /api/packs/{name}/roster/deploy` (`api_pack_roster_deploy`) deploys ONLY
// the `always` tier and returns `{ok, pack, deployed, dormant, missing}`. It was reachable but
// unreachable-from-the-UI: `PacksPanel` told the user "its roster is staged until you deploy
// them" and offered no deploy button. These tests drive the real `PackRow`: they prove the
// button posts with the pack name, renders the typed result, and — the load-bearing half —
// shows the DORMANT tier so the surface never implies the whole roster went live.
//
// The `always`-only contract has a vacuity partner in each assertion: a result with a `phase-2`
// member that DID appear under "Now live" would be the always-tier rule silently broken, so the
// dormant assertions render differently from the deployed ones.

const staged: InstalledPackRec = {
  name: 'personal-cfo',
  version: '1.0.0',
  components: [],
  connectors: [],
  connector_markers: [],
  setup_skill: '',
  setup_pending: false,
  installed_at: '',
  roster: [
    { slug: 'cfo', name: 'CFO', description: '', label: '', icon: '', color: '', activation: 'always', target: 'cfo' },
    { slug: 'cfo-tax-analyst', name: 'Tax', description: '', label: '', icon: '', color: '', activation: 'phase-2', target: 'cfo-tax-analyst' },
  ],
}

const RESULT: PackRosterDeployRec = {
  ok: true, pack: 'personal-cfo', deployed: ['cfo'], dormant: ['cfo-tax-analyst'], missing: [],
}

/** Mount the REAL PackRow with `../../lib/api` and `../../app/appSdk` doMocked, so the deploy
 *  goes through `api.packRosterDeploy` and every `notify` is captured. Returns the pack name
 *  the client was actually called with (a button wired to the wrong name cannot pass) and the
 *  toast log. */
async function mountRow(
  pack: InstalledPackRec,
  result: PackRosterDeployRec = RESULT,
  opts: { fail?: boolean } = {},
) {
  vi.resetModules()
  const calls: string[] = []
  const notified: Array<{ msg: string; kind: string }> = []
  vi.doMock('../../app/appSdk', () => ({
    notify: (msg: string, kind: string) => { notified.push({ msg, kind }) },
  }))
  vi.doMock('../../lib/api', () => ({
    api: {
      packRosterDeploy: (name: string) => {
        calls.push(name)
        return opts.fail ? Promise.reject(new Error('pack ships no roster')) : Promise.resolve(result)
      },
      // Referenced by PackRow's other handlers but never reached — no test clicks them.
      packUpdate: () => Promise.resolve({ ok: true, update: {} }),
      packFinishSetup: () => Promise.resolve({}),
    },
  }))
  const { PackRow } = await import('./PacksPanel')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<PackRow pack={pack} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return { r, calls, notified, body: () => r.container.textContent ?? '' }
}

async function clickDeploy(r: ReturnType<typeof render>) {
  await act(async () => {
    fireEvent.click(within(r.container).getByRole('button', { name: /deploy roster/i }))
    await new Promise((res) => setTimeout(res, 0))
  })
}

describe('the deploy control appears only for a staged roster', () => {
  it('offers a Deploy roster button when the pack ships a roster', async () => {
    const { r } = await mountRow(staged)
    expect(within(r.container).queryByRole('button', { name: /deploy roster/i })).not.toBeNull()
  })

  it('offers no deploy control for a pack that ships no roster', async () => {
    const { r } = await mountRow({ ...staged, roster: [] })
    expect(within(r.container).queryByRole('button', { name: /deploy roster/i })).toBeNull()
  })

  it('survives a ledger record with no roster field at all', async () => {
    // A row written before the field existed is a real case, not an error.
    const { r } = await mountRow({ ...staged, roster: undefined })
    expect(within(r.container).queryByRole('button', { name: /deploy roster/i })).toBeNull()
  })
})

describe('deploying posts to the endpoint and renders the typed result', () => {
  it('posts to the roster-deploy endpoint with the pack name', async () => {
    const { r, calls } = await mountRow(staged)
    await clickDeploy(r)
    expect(calls).toEqual(['personal-cfo'])
  })

  it('renders the deployed always-tier agent after a successful deploy', async () => {
    const { r, body } = await mountRow(staged)
    await clickDeploy(r)
    const t = body()
    expect(t).toContain('Deployed 1 always-tier agent')
    expect(t).toContain('Now live')
  })

  it('reports the always-tier deploy in a success toast', async () => {
    const { r, notified } = await mountRow(staged)
    await clickDeploy(r)
    expect(notified.some((n) => n.kind === 'success' && /Deployed 1 always-tier agent/.test(n.msg))).toBe(true)
  })
})

describe('the result reflects that ONLY the always tier deploys', () => {
  it('shows the phase-2 member as dormant, never as deployed', async () => {
    const { r, body } = await mountRow(staged)
    await clickDeploy(r)
    const t = body()
    // Exactly ONE agent went live (the always tier) — a "Deployed 2" would be the phase-2
    // member wrongly hired.
    expect(t).toContain('Deployed 1 always-tier agent')
    expect(t).not.toContain('Deployed 2')
    // …and the phase-2 member is named under Dormant / staged-for-later, not hired.
    expect(t).toContain('1 staged for later, not hired')
    expect(t).toContain('Dormant')
    expect(t).toContain('cfo-tax-analyst')
    // Scoped, not just "somewhere on screen": the always member is under "Now live" and the
    // phase-2 member is NOT — the strongest form of the always-tier-only contract.
    const nowLive = within(r.container).getByText('Now live').parentElement
    expect(nowLive?.textContent).toContain('cfo')
    expect(nowLive?.textContent).not.toContain('cfo-tax-analyst')
  })

  it('names an always-tier persona that went missing rather than dropping it', async () => {
    // deploy_roster reports (not raises) a persona deleted after install; the surface must say so.
    const { r, body } = await mountRow(staged, {
      ok: true, pack: 'personal-cfo', deployed: [], dormant: ['cfo-tax-analyst'], missing: ['cfo'],
    })
    await clickDeploy(r)
    const t = body()
    expect(t).toContain('No always-tier agent deployed')
    expect(t).toContain('Installed then removed, so not deployed: cfo')
  })
})

describe('the in-flight state announces itself (aria-busy), not just visually', () => {
  it('publishes aria-busy on the deploy button while deploying, then clears', async () => {
    // The deploy button owns a NARROW `loading={deploying}` flag so it — and only it — publishes
    // `aria-busy` for the action it runs. `disabled={busy}` alone announces nothing to assistive
    // tech (the repo's `busyIsNotAnnounced` census records exactly this). Proven locally by
    // holding the request open across the assertion.
    vi.resetModules()
    let resolve!: (v: PackRosterDeployRec) => void
    const pending = new Promise<PackRosterDeployRec>((res) => { resolve = res })
    vi.doMock('../../app/appSdk', () => ({ notify: () => {} }))
    vi.doMock('../../lib/api', () => ({
      api: {
        packRosterDeploy: () => pending,
        packUpdate: () => Promise.resolve({ ok: true, update: {} }),
        packFinishSetup: () => Promise.resolve({}),
      },
    }))
    const { PackRow } = await import('./PacksPanel')
    let r!: ReturnType<typeof render>
    await act(async () => { r = render(<PackRow pack={staged} />); await Promise.resolve() })
    const btn = () => within(r.container).getByRole('button', { name: /deploy roster/i })
    expect(btn().getAttribute('aria-busy')).not.toBe('true')
    await act(async () => { fireEvent.click(btn()); await Promise.resolve() })
    expect(btn().getAttribute('aria-busy')).toBe('true')
    await act(async () => { resolve(RESULT); await new Promise((res) => setTimeout(res, 0)) })
    expect(btn().getAttribute('aria-busy')).not.toBe('true')
    expect(r.container.textContent).toContain('Now live')
  })
})

describe('a failed deploy is announced, not silent', () => {
  it('surfaces the error and shows no stale result', async () => {
    const { r, body, notified } = await mountRow(staged, RESULT, { fail: true })
    await clickDeploy(r)
    expect(notified.some((n) => n.kind === 'error' && /Couldn't deploy .*roster/.test(n.msg))).toBe(true)
    expect(body()).not.toContain('Now live')
  })
})

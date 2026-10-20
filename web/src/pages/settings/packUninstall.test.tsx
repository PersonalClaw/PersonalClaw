import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { InstalledPackRec, PackUninstallRec } from '../../lib/api'

// ── An installed pack can be uninstalled (Settings → Packs) ──────────────────────────────────────
//
// Each installed pack offered Finish setup, Deploy roster, Add triggers and Check for update, and no
// way to remove it. The row now has Uninstall: a dry run first, then a dialog that says what goes,
// what stays and why, and only then the removal. A pack with an agent or automation deployed from it
// still live is not removed at all — the row names each one and the page to remove it from.

const PACK: InstalledPackRec = {
  name: 'personal-cfo', version: '1.0.0',
  components: ['skill:cfo-statement-fetch', 'skill:cfo-budget-review', 'agent:cfo', 'prompt:cfo-spending-digest'],
  connectors: [], connector_markers: [], setup_skill: '', setup_pending: false, installed_at: '',
}

const PLAN: PackUninstallRec = {
  pack: 'personal-cfo', version: '1.0.0', applied: false,
  removed: ['skill:cfo-statement-fetch', 'agent:cfo', 'prompt:cfo-spending-digest', 'template:cfo-monthly-review', 'trigger:cfo-spending-digest', 'skill:personal-cfo-setup'],
  kept: [{ ref: 'skill:cfo-budget-review', reason: 'you edited it after it was installed, so it stays' }],
  missing: [], in_use: [], servers: ['finance-statements'],
}

let packUninstall: Mock<(name: string, confirm: boolean) => Promise<{ ok: boolean; uninstall: PackUninstallRec }>>
let confirmDestructive: Mock<(title: string, body: ReactNode, opts?: { confirmLabel?: string }) => Promise<boolean>>
let notified: Array<[string, string]>

async function mountRow(dry: PackUninstallRec, confirmed = true) {
  notified = []
  packUninstall = vi.fn(async (_name: string, confirm: boolean) => ({
    ok: true, uninstall: confirm ? { ...dry, applied: true } : dry,
  }))
  confirmDestructive = vi.fn(() => Promise.resolve(confirmed))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: { packUninstall: (n: string, c: boolean) => packUninstall(n, c) },
  }))
  vi.doMock('../../ui/dialog', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    confirmDestructive,
  }))
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (msg: string, tone: string) => { notified.push([msg, tone]) },
  }))
  const { PackRow } = await import('./PacksPanel')
  const onChanged = vi.fn()
  render(<PackRow pack={PACK} onChanged={onChanged} />)
  fireEvent.click(screen.getByRole('button', { name: 'Uninstall personal-cfo' }))
  return onChanged
}

beforeEach(() => { vi.resetModules() })

describe('uninstalling a pack', () => {
  it('🔴 asks with what goes and what stays, then removes it and refreshes the list', async () => {
    const onChanged = await mountRow(PLAN)
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect(packUninstall.mock.calls).toEqual([['personal-cfo', false], ['personal-cfo', true]])
    const [title, body, opts] = confirmDestructive.mock.calls[0] as [string, ReactNode, { confirmLabel: string }]
    expect(title).toBe('Uninstall personal-cfo?')
    expect(opts.confirmLabel).toBe('Uninstall')
    const shown = render(<>{body}</>).container.textContent ?? ''
    expect(shown).toContain('Removes 2 skills, 1 agent definition, 1 prompt, 1 workflow and 1 staged automation.')
    expect(shown).toContain('skill:cfo-budget-review: you edited it after it was installed, so it stays.')
    expect(shown).toContain('The MCP server you set up for it (finance-statements) stays')
    expect(notified).toEqual([['Uninstalled personal-cfo — 1 component kept.', 'success']])
  })

  it('declining the dialog removes nothing', async () => {
    const onChanged = await mountRow(PLAN, false)
    await waitFor(() => expect(confirmDestructive).toHaveBeenCalled())
    expect(packUninstall.mock.calls).toEqual([['personal-cfo', false]])
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('every action on the row is named after its pack, so two packs never share a name', async () => {
    await mountRow(PLAN)
    const { PackRow } = await import('./PacksPanel')
    render(<PackRow onChanged={() => {}} pack={{
      ...PACK, name: 'health-os', setup_pending: true, setup_skill: 'health-os-setup',
      staged_triggers: ['weekly'],
      roster: [{ slug: 'coach', name: 'Coach', description: '', label: '', icon: '', color: '', activation: 'always', target: 'coach' }],
    }} />)
    for (const name of [
      'Finish setup for health-os', 'Add triggers to Automations from health-os',
      'Deploy roster for health-os', 'Check for update to health-os', 'Uninstall health-os',
    ]) expect(screen.getByRole('button', { name })).toBeTruthy()
    expect(screen.queryAllByRole('button', { name: 'Uninstall' }), 'no bare shared name').toHaveLength(0)
  })

  it('a pack still in use is not removed, and the row names what must go first and where', async () => {
    const onChanged = await mountRow({
      ...PLAN,
      in_use: [
        { kind: 'agent', id: 'cfo', name: 'cfo' },
        { kind: 'automation', id: 'pack-personal-cfo-spending-digest', name: 'Spending digest' },
      ],
    })
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('personal-cfo is still in use, so nothing was removed.')
    expect(alert.textContent).toContain('Agent cfo, in Agents')
    expect(alert.textContent).toContain('Automation Spending digest, in Automations')
    expect(screen.getByRole('link', { name: 'Agents' }).getAttribute('href')).toBe('#/agents')
    expect(screen.getByRole('link', { name: 'Automations' }).getAttribute('href')).toBe('#/triggers')
    expect(confirmDestructive).not.toHaveBeenCalled()
    expect(packUninstall.mock.calls).toEqual([['personal-cfo', false]])
    expect(onChanged).not.toHaveBeenCalled()
  })
})

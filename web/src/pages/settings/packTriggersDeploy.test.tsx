import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { PackRow } from './PacksPanel'
import type { InstalledPackRec, PackTriggersDeployRec } from '../../lib/api'

// ── The enable path for a pack's staged triggers (AP-7) ────────────────────────────────────────
//
// Pack install lands a pack's triggers staged-and-DISABLED (a pack must never arm automation).
// `packs/import_.py` stages them; `packs/triggers.deploy_triggers` is the enable path that makes
// them visible in Automations — STILL disabled, for the user to arm one at a time. This file
// proves the row surfaces that path only when there is something to add, calls it, and reports
// the result HONESTLY: "added, disabled — go enable them", never "enabled".
//
// The safety-critical assertion is the copy. A result line that said the triggers were enabled
// would be describing an automation the user never switched on — the exact thing the
// disabled-on-deploy posture exists to prevent — so the test asserts "(disabled)" is present and
// the affordance is "Add", not "Enable".

const packTriggersDeploy = vi.fn()
vi.mock('../../lib/api', () => ({ api: { packTriggersDeploy: (...a: unknown[]) => packTriggersDeploy(...a) } }))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

const base: InstalledPackRec = {
  name: 'cfo-pack',
  version: '1.2.0',
  components: ['trigger:cfo-spending-digest'],
  connectors: [],
  connector_markers: [],
  setup_skill: '',
  setup_pending: false,
  installed_at: '2026-08-01T09:30:00Z',
  staged_triggers: ['cfo-spending-digest', 'cfo-month-end'],
}

const deployRec = (over: Partial<PackTriggersDeployRec> = {}): PackTriggersDeployRec => ({
  ok: true, pack: 'cfo-pack', deployed: ['cfo-spending-digest', 'cfo-month-end'], skipped: [], ...over,
})

const addButton = () => screen.queryByRole('button', { name: /^Add triggers to Automations from / })

describe('the "Add triggers to Automations" control', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    packTriggersDeploy.mockResolvedValue(deployRec())
  })

  it('shows only when the pack staged triggers', () => {
    render(<PackRow pack={base} onChanged={() => {}} />)
    expect(addButton()).toBeTruthy()
  })

  it('is absent when the pack staged none — and when the field is missing entirely', () => {
    const { rerender } = render(<PackRow pack={{ ...base, staged_triggers: [] }} onChanged={() => {}} />)
    expect(addButton()).toBeNull()
    // A ledger row written before the field existed is a real case, not an error.
    rerender(<PackRow pack={{ ...base, staged_triggers: undefined }} onChanged={() => {}} />)
    expect(addButton()).toBeNull()
  })

  it('never labels the action "Enable" — the deploy lands them disabled', () => {
    render(<PackRow pack={base} onChanged={() => {}} />)
    expect(screen.queryByRole('button', { name: /enable/i })).toBeNull()
  })
})

describe('deploying the staged triggers', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    packTriggersDeploy.mockResolvedValue(deployRec())
  })

  it('calls packTriggersDeploy with the pack name on click', async () => {
    render(<PackRow pack={base} onChanged={() => {}} />)
    fireEvent.click(addButton()!)
    await waitFor(() => expect(packTriggersDeploy).toHaveBeenCalledWith('cfo-pack'))
  })

  it('reports the count HONESTLY as disabled, and links to Automations to arm them', async () => {
    const { container } = render(<PackRow pack={base} onChanged={() => {}} />)
    fireEvent.click(addButton()!)
    await waitFor(() => expect(container.textContent).toContain('Added 2 triggers to'))
    const text = container.textContent ?? ''
    expect(text).toContain('(disabled)')
    expect(text).toMatch(/review and enable each in Automations/)
    // The link goes to the Automations page, where each trigger is armed.
    const link = screen.getByRole('link', { name: 'Automations' })
    expect(link.getAttribute('href')).toBe('#/triggers')
  })

  it('pluralises a single deployed trigger correctly', async () => {
    packTriggersDeploy.mockResolvedValue(deployRec({ deployed: ['cfo-spending-digest'] }))
    const { container } = render(<PackRow pack={base} onChanged={() => {}} />)
    fireEvent.click(addButton()!)
    await waitFor(() => expect(container.textContent).toContain('Added 1 trigger to'))
    expect(container.textContent).not.toContain('Added 1 triggers')
  })

  it('surfaces staged files too broken to add, rather than hiding them', async () => {
    packTriggersDeploy.mockResolvedValue(deployRec({ deployed: ['cfo-spending-digest'], skipped: ['cfo-month-end'] }))
    const { container } = render(<PackRow pack={base} onChanged={() => {}} />)
    fireEvent.click(addButton()!)
    await waitFor(() => expect(container.textContent).toContain('too broken to add'))
    expect(container.textContent).toContain('cfo-month-end')
  })
})

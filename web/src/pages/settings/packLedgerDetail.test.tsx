import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { PackRow } from './PacksPanel'
import type { InstalledPackRec } from '../../lib/api'

vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

// ── What an installed pack actually put on this machine ────────────────────────
//
// `packs/installed.json` is described by its own module docstring as "the READER surface behind
// two done_when contracts" — it exists so a surface can answer "what did this pack do" WITHOUT
// re-deriving it. `PackRow` read 4 of the record's 8 fields: name, version, connector_markers,
// setup_pending. An installed pack was a name, a version, and a list of what FAILED.
//
//   components         ["skill:cfo-report", "trigger:month-end", …] — what it installed. The whole
//                      point of the ledger, and never shown.
//   connectors         the per-connector ConnectorResolution: mode (configure|substitute|skip),
//                      server_name, error, credentials_saved. The row showed only
//                      `connector_markers` — the SKIPS — so a pack with three connectors
//                      configured and none skipped looked identical to one with no connectors.
//   installed_at       a durable record of a past event nothing else can re-derive.
//   setup_skill        the committed skill id behind the "Finish setup" chip.
//
// NOTE ON THE SCANNER: the `wire` lens flagged `setup_skill` + `installed_at` (2 of 8). It MISSED
// `components` and `connectors`, both genuinely unread — the second time the lens undercounted
// (it missed `plan` on RemediationSnapshot too). Census every field of a flagged interface by
// hand; the lens finds the interface, not the field set.
//
// `setup_pending` is NOT drift — it gates the Finish-setup chip and always did. Verified before
// touching anything, since the module docstring names that chip explicitly.

const base: InstalledPackRec = {
  name: 'cfo-pack',
  version: '1.2.0',
  components: ['skill:cfo-report', 'trigger:month-end'],
  connectors: [
    { name: 'quickbooks', mode: 'configure', server_name: 'qb-mcp', marker: '', credentials_saved: ['qb_token'], error: '' },
    { name: 'slack', mode: 'substitute', server_name: 'slack-alt', marker: '', credentials_saved: [], error: '' },
    { name: 'netsuite', mode: 'skip', server_name: '', marker: 'connector_missing:netsuite', credentials_saved: [], error: 'no credential provided' },
  ],
  connector_markers: ['connector_missing:netsuite'],
  setup_skill: 'cfo-pack-setup',
  setup_pending: false,
  installed_at: '2026-08-01T09:30:00Z',
}

const text = (p: InstalledPackRec) => render(<PackRow pack={p} />).container.textContent ?? ''

describe('components — what the pack installed', () => {
  it('lists every installed component', () => {
    const t = text(base)
    expect(t).toContain('Installed')
    expect(t).toContain('skill:cfo-report')
    expect(t).toContain('trigger:month-end')
  })

  it('omits the Installed line for a pack that installed nothing', () => {
    // Asserted on the LABEL boundary, not the bare word: "Installed" is also the prefix of the
    // "Installed 8/1/2026" date line, so a substring check here passes/fails for the wrong reason.
    const { container } = render(<PackRow pack={{ ...base, components: [] }} />)
    expect([...container.querySelectorAll('span')].map((s) => s.textContent))
      .not.toContain('Installed')
    expect(container.textContent).toContain('Installed 8/1/2026')  // the date line survives
  })

  it('survives a record with no components key at all', () => {
    // A ledger row written before a field existed is a real case, not an error.
    const t = text({ ...base, components: undefined as unknown as string[] })
    expect(t).toContain('cfo-pack')
  })
})

describe('connectors — how each one resolved, not just which failed', () => {
  it('names every connector and its mode', () => {
    const t = text(base)
    expect(t).toContain('quickbooks')
    expect(t).toContain('configure')
    expect(t).toContain('slack')
    expect(t).toContain('substitute')
    expect(t).toContain('netsuite')
    expect(t).toContain('skip')
  })

  it('shows which server a configured connector wrote', () => {
    expect(text(base)).toContain('qb-mcp')
  })

  it('shows the credential KEYS saved, which is the audit fact', () => {
    // "proving a credential reached the store, not the pack" — names only, never values.
    expect(text(base)).toContain('saved qb_token')
  })

  it('distinguishes a degraded skip from a pack that never asked', () => {
    // `error` is set only when a configure/substitute DEGRADED to skip. Without it, "skip" reads
    // the same whether the pack declined to configure or tried and failed.
    expect(text(base)).toContain('no credential provided')
  })

  it('tones a skipped connector as a warning and leaves the others neutral', () => {
    const { container } = render(<PackRow pack={base} />)
    const warned = [...container.querySelectorAll('.text-warn')].map((e) => e.textContent)
    expect(warned).toContain('netsuite')
    expect(warned).not.toContain('quickbooks')
  })

  it('still shows configured connectors for a pack with NO skips', () => {
    // The old row rendered only `connector_markers`, so this pack showed nothing about its
    // connectors at all — indistinguishable from a pack that declared none.
    const t = text({ ...base, connectors: [base.connectors[0]], connector_markers: [] })
    expect(t).toContain('quickbooks')
    expect(t).toContain('qb-mcp')
  })
})

describe('setup_skill and installed_at', () => {
  it('names the setup skill once setup is no longer pending', () => {
    expect(text(base)).toContain('Setup skill: cfo-pack-setup')
  })

  it('does NOT name it while the chip is showing', () => {
    // A pending pack already has the affordance; naming the id beside it is redundant.
    const t = text({ ...base, setup_pending: true })
    expect(t).toContain('Finish setup')
    expect(t).not.toContain('Setup skill:')
  })

  it('shows the install date', () => {
    expect(text(base)).toContain('Installed 8/1/2026')
  })

  it('renders nothing rather than "Invalid Date" for a malformed timestamp', () => {
    const t = text({ ...base, installed_at: 'not-a-date' })
    expect(t).not.toContain('Invalid Date')
  })

  it('omits the date line when the field is empty', () => {
    const t = text({ ...base, installed_at: '', components: [], connectors: [], setup_skill: '' })
    expect(t).not.toMatch(/Installed \d/)
  })
})

describe('the detail block gate', () => {
  it('renders for a pack whose ONLY extra fact is its install date', () => {
    // Gating on components/connectors alone would hide this — the same
    // activity-vs-existence mistake the MCP pool tile made.
    const t = text({ ...base, components: [], connectors: [], setup_skill: '', connector_markers: [] })
    expect(t).toContain('Installed 8/1/2026')
  })

  it('renders no detail block for a bare record', () => {
    const { container } = render(<PackRow pack={{
      name: 'bare', version: '0.1', components: [], connectors: [], connector_markers: [],
      setup_skill: '', setup_pending: false, installed_at: '',
    }} />)
    // The name row survives; nothing else is invented.
    expect(container.textContent).toContain('bare')
    expect(container.querySelector('.border-t')).toBeNull()
  })
})

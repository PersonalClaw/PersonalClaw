import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A toggle whose effect is invisible from the machine you set it on ──────────────────────────────
//
// `companion.discovery_enabled` (CA-4) asks the gateway to advertise itself on the LAN. The backend
// (CA-5) deliberately REFUSES on a loopback-only bind: announcing 127.0.0.1 publishes an address that
// resolves, on every other device, to that device. So "enabled" and "advertising" are two states, and
// the designed case — on, and correctly doing nothing — is the one a flag-only panel gets wrong.
//
// 🪤 THE FAILURE THIS RAIL EXISTS FOR: a switch reading "on" beside no explanation, while the log file
// (which nobody opens) carries the reason. The panel therefore reads the LIVE advertiser and prints
// the backend's own sentence for the reason code.
//
// 🪤 AND THE STATUS IS A WORD, not a colour (1.4.1) — 'Advertising' / 'Not advertising'.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const CFG = { companion: { discovery_enabled: true, instance_name: 'Living room Mac' } }

const discovery = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    personalclawConfig: () => Promise.resolve(CFG),
    patchConfig: () => Promise.resolve({}),
    companionDiscovery: () => discovery(),
  },
}))

const ADVERTISING = {
  advertising: true,
  reason: 'advertising',
  detail: 'Advertising on the local network.',
  service_type: '_personalclaw._tcp.local.',
  instance_name: 'Living room Mac',
  port: 10166,
  addresses: ['192.168.1.37'],
  txt: { name: 'Living room Mac', port: '10166', requires_pairing: '1', schema: '1' },
}

const LOOPBACK = {
  advertising: false,
  reason: 'loopback_only',
  detail:
    'LAN discovery is on, but this gateway is bound to loopback only, so nothing on your ' +
    'network could reach it. Not advertising.',
  service_type: '_personalclaw._tcp.local.',
  instance_name: '',
  port: 0,
  addresses: [],
  txt: {},
}

describe('the Companion panel reports what LAN discovery is actually doing', () => {
  beforeEach(() => { discovery.mockReset() })

  it('says Advertising, in words, when the advertiser is live', async () => {
    discovery.mockResolvedValue(ADVERTISING)
    const { CompanionPanel } = await import('./CompanionPanel')
    render(<CompanionPanel />)
    expect(await screen.findByText('Advertising')).toBeTruthy()
  })

  it('shows the record verbatim, so the owner can read what the network is told', async () => {
    discovery.mockResolvedValue(ADVERTISING)
    const { CompanionPanel } = await import('./CompanionPanel')
    render(<CompanionPanel />)
    expect(await screen.findByText('_personalclaw._tcp.local.')).toBeTruthy()
    expect(screen.getByText('192.168.1.37:10166')).toBeTruthy()
    expect(screen.getByText('requires_pairing')).toBeTruthy()
    // The claim the panel makes about the record, on screen beside the record.
    expect(screen.getByText(/no token, no session and no content/i)).toBeTruthy()
  })

  it('says Not advertising AND why, when the toggle is on but the bind is loopback-only', async () => {
    discovery.mockResolvedValue(LOOPBACK)
    const { CompanionPanel } = await import('./CompanionPanel')
    render(<CompanionPanel />)
    expect(await screen.findByText('Not advertising')).toBeTruthy()
    // THE POINT: the reason reaches the user, not just the log.
    expect(screen.getByText(/bound to loopback only/)).toBeTruthy()
    // And no record table, which would imply a broadcast exists and is empty.
    expect(screen.queryByText('_personalclaw._tcp.local.')).toBeNull()
  })

  it('renders the backend sentence rather than mapping the reason code itself', () => {
    // A second vocabulary here is how "on but inert" drifts back into reading as "on": the
    // backend owns the closed reason set AND its wording, so both surfaces always agree.
    const code = read('pages/settings/CompanionPanel.tsx')
    expect(code).toMatch(/hint=\{discovery\.detail\}/)
    expect(code, 'no local copy of the reason wording').not.toMatch(/loopback_only/)
  })

  it('states the status in words, so it is not carried by colour alone (1.4.1)', () => {
    const code = read('pages/settings/CompanionPanel.tsx')
    expect(code).toMatch(/'Advertising' : 'Not advertising'/)
  })

  it('re-reads the live state after the toggle is PATCHed', () => {
    // The vacuity floor for the whole rail: without this the panel would keep showing the
    // pre-toggle reality while the switch had already moved.
    const code = read('pages/settings/CompanionPanel.tsx')
    expect(code).toMatch(/refreshDiscovery\(\)/)
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

// ── "Suggested for your projects" names a project it could not scan ─────────────────────────────
//
// `GET /api/packs/proposals` logged a project whose scan failed and went on, so a scan that failed
// for every project answered the same empty list as one that matched nothing — and this section then
// said "No pack matches any project's workspace. Bind a project to a codebase directory…" to a user
// whose projects ARE bound. The route now names each project it could not scan (`unscanned`); the
// section says which and why, and only claims "no match" when every project was scanned.

const LEDGER = { project_id: 'p-ledger', project: 'Ledger', reason: "[Errno 13] Permission denied: '/srv/ledger'" }

function mockApi(answer: unknown) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: { packProposals: () => Promise.resolve(answer) },
  }))
}

async function mount() {
  const { ProposalsSection } = await import('./PacksPanel')
  render(<ProposalsSection onInstalled={() => {}} />)
}

beforeEach(() => { vi.resetModules() })

describe('pack suggestions say which projects could not be scanned', () => {
  it('names the project and why, and does not claim that nothing matched', async () => {
    mockApi({ proposals: [], unscanned: [LEDGER] })
    await mount()
    const said = await screen.findByText(/Couldn't scan one project for suggestions/)
    expect(said.closest('[role="status"]')).not.toBeNull()
    expect(screen.getByText('Ledger')).toBeInTheDocument()
    expect(screen.getByText(/Permission denied: '\/srv\/ledger'/)).toBeInTheDocument()
    expect(screen.queryByText(/No pack matches any project's workspace/)).toBeNull()
  })

  it('still says nothing matched when every project was scanned', async () => {
    mockApi({ proposals: [], unscanned: [] })
    await mount()
    expect(await screen.findByText(/No pack matches any project's workspace/)).toBeInTheDocument()
    expect(screen.queryByText(/Couldn't scan/)).toBeNull()
  })
})

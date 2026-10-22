// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { DiscoverResponse } from '../../lib/api'

// ── One Discover tip in an empty state, and in first run (#3818) ──────────────────────────────────
//
// Discover leaves the sidebar's reach for the places it helps: an empty Library, Automations or
// Capabilities page, and the end of first run. Each shows one tip from the same catalogue the
// Discover page reads, with its link and its dismiss: the first tip still showing (not dismissed,
// its area not yet used) that points somewhere other than the page it sits on, since the page
// already says how to fill itself. A dismissed tip goes, and the next takes its place. Off, unread
// or unreadable, nothing shows: a tip is never what an empty state is about.

const discover = vi.fn<() => Promise<DiscoverResponse>>()
const dismiss = vi.fn<(id: string) => Promise<{ ok: boolean; dismissed: string[] }>>()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<typeof import('../../lib/api')>()),
  api: { discover: () => discover(), dismissDiscoverTip: (id: string) => dismiss(id) },
}))

const { DiscoverTip } = await import('./DiscoverTip')
const { resetDataStore } = await import('../../lib/data/store')

const tip = (id: string, route: string, title: string) => ({
  id, area: 'Area', title, lesson: `What ${title} does.`, try_it: { route, label: `Open ${id}` },
})
const feed = (tips = [tip('knowledge', 'knowledge', 'Build a knowledge base'), tip('skills', 'skills', 'Teach it a skill')]): DiscoverResponse => ({
  enabled: true, areas: [{ area: 'Area', tips }], visible_count: tips.length, total: tips.length, dismissed_count: 0,
} as DiscoverResponse)

beforeEach(() => {
  resetDataStore()
  discover.mockReset()
  dismiss.mockReset().mockResolvedValue({ ok: true, dismissed: ['skills'] })
})
afterEach(() => cleanup())

describe('a Discover tip in an empty state', () => {
  it('shows the first tip that points somewhere else, with its lesson and its link', async () => {
    discover.mockResolvedValue(feed())
    render(<DiscoverTip here="knowledge" />)
    const region = await screen.findByRole('complementary', { name: 'A tip from Discover' })
    expect(region.textContent).toContain('Teach it a skill')
    expect(region.textContent).toContain('What Teach it a skill does.')
    expect(screen.getByRole('link', { name: 'Open skills' }).getAttribute('href')).toBe('#/skills')
    expect(screen.queryByText('Build a knowledge base'), 'not the page it sits on').toBeNull()
  })

  it('a dismissed tip goes, through the catalogue\'s own dismiss, and the next takes its place', async () => {
    discover.mockResolvedValueOnce(feed([tip('skills', 'skills', 'Teach it a skill'), tip('apps', 'apps', 'Install an app')]))
      .mockResolvedValue(feed([tip('apps', 'apps', 'Install an app')]))
    render(<DiscoverTip here="knowledge" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss this tip' }))
    await waitFor(() => expect(dismiss).toHaveBeenCalledWith('skills'))
    expect(await screen.findByText('Install an app')).toBeTruthy()
    expect(screen.queryByText('Teach it a skill')).toBeNull()
  })

  it('in first run, its link leaves through the way out it is handed', async () => {
    discover.mockResolvedValue(feed())
    const onGo = vi.fn()
    render(<DiscoverTip here="onboarding" onGo={onGo} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open knowledge' }))
    expect(onGo).toHaveBeenCalledWith('knowledge')
  })

  it.each([
    ['off', () => discover.mockResolvedValue({ ...feed(), enabled: false })],
    ['with nothing left to show', () => discover.mockResolvedValue(feed([tip('knowledge', 'knowledge', 'Build a knowledge base')]))],
    ['unreadable', () => discover.mockRejectedValue(new Error('down'))],
  ])('shows nothing while Discover is %s', async (_, arrange) => {
    arrange()
    const { container } = render(<DiscoverTip here="knowledge" />)
    await waitFor(() => expect(discover).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 20))
    expect(container.textContent).toBe('')
  })
})

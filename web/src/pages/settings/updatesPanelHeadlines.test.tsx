import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { UpdateCheck } from '../../lib/api'
import { repoDocUrl } from '../../lib/repoDocs'

// ── The Updates panel with a headline-only CHANGELOG ───────────────────────────────────────────────
//
// Every entry is one line, `- **<headline>**`, with no body. Two things on this panel read as if the
// bodies were still there:
//  · a git install's "Update available" line printed the CHANGELOG lines added upstream (`changes`) as
//    caption text — `- **A new thing.** - **Another.**`, marks and all, run together into one line;
//  · the card listed a release's breaking changes as bare headlines ("Updates.", "API breaks.") with
//    nowhere to read what each asks of you, because that text moved to the Updating guide.

const useQuery = vi.fn()
vi.mock('../../lib/data', () => ({
  useQuery: (...a: unknown[]) => useQuery(...a),
  invalidateKeys: vi.fn(),
}))

const { UpdatesPanel, UPGRADE_NOTES_DOC } = await import('./UpdatesPanel')

function mountWith(info: UpdateCheck, changelog = '## [Unreleased]\n\n### Added\n\n- **A thing.**\n') {
  useQuery.mockReturnValue({ data: { info, changelog }, loading: false, error: null, refresh: vi.fn() })
  return render(<UpdatesPanel />)
}

const GIT_UPDATE: UpdateCheck = {
  available: true, checked: true, auto: 'off', kind: 'git', current: '0.2.0', latest: '0.2.1',
  update_available: true, channel: 'stable', check_enabled: true,
  changes: ['## [0.2.1] — 2026-10-01', '', '### Added', '', '- **A new thing.**', '- **Another thing.**'].join('\n'),
}

describe('the Updates panel reads a headline-only CHANGELOG', () => {
  it('lists the entries an update brings, as a list and without their markdown marks', async () => {
    const { container } = mountWith(GIT_UPDATE)
    const box = await screen.findByRole('group', { name: 'What the update brings' })
    expect([...box.querySelectorAll('li')].map((li) => li.textContent?.trim())).toEqual(['A new thing.', 'Another thing.'])
    expect(box.querySelector('strong'), 'a headline-only entry is shown plain').toBeNull()
    expect(container.textContent, 'no raw markdown reaches the page').not.toContain('- **')
    expect(container.textContent).toContain('What the update brings:')
  })

  it('says a new version is ready when the diff added no entry', async () => {
    const { container } = mountWith({ ...GIT_UPDATE, changes: '## [0.2.1] — 2026-10-01' })
    await waitFor(() => expect(container.textContent).toContain('A new version is ready to install.'))
    expect(screen.queryByRole('group', { name: 'What the update brings' })).toBeNull()
  })

  it('links the changelog to the upgrade notes, and the notes exist where it points', async () => {
    mountWith({ ...GIT_UPDATE, available: false, update_available: false, changes: '' })
    const link = await screen.findByRole('link', { name: /upgrade notes/i })
    expect(link.getAttribute('href')).toBe(repoDocUrl(UPGRADE_NOTES_DOC))
    const [path, anchor] = UPGRADE_NOTES_DOC.split('#')
    const guide = readFileSync(join(process.cwd(), '..', path), 'utf8')
    const heading = guide.split('\n').find((l) => /^#{1,6} /.test(l) && l.replace(/^#+ /, '').toLowerCase().replace(/\s+/g, '-') === anchor)
    expect(heading, `${path} has no heading whose anchor is #${anchor}`).toBeTruthy()
  })
})

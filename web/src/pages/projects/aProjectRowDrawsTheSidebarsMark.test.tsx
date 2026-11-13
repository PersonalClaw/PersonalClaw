import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ProjectThreadRow } from './ProjectThreads'
import { ORB_STATE_OF_PIP } from '../../lib/orbState'
import { PIP } from '../../ui/ThreadRail'
import type { Thread, ThreadPip } from '../../lib/useThreads'

// ── A project page's chat row draws the sidebar's mark (#6117) ──────────────────────────────────
//
// The project page lists a project's chats with the same pips the sidebar marks. Each row draws the
// orb's 12 px still mark of its pip's state, as the sidebar does, in the family's shared state token
// (the page keeps those; the sidebar has colours of its own), with its word beside it.

const t = (key: string, pip: ThreadPip | null): Thread => ({
  key, kind: 'chat', id: key, title: `Chat ${key}`, projectId: 'p1', route: `chat/${key}`,
  pip, pinned: false, activeAt: 0, folderId: '', tagIds: [],
})

afterEach(cleanup)

describe("a project page's chat row", () => {
  it.each(Object.keys(PIP) as ThreadPip[])('%s draws the still mark of its state in its family’s token, with its word', (pip) => {
    const { container } = render(<ul><ProjectThreadRow thread={t(pip, pip)} onOpen={() => {}} /></ul>)
    const mark = container.querySelector<HTMLElement>('[data-orb-mark]')
    expect(mark, 'no mark').not.toBeNull()
    expect(mark!.dataset.orbMark).toBe(ORB_STATE_OF_PIP[pip].state)
    expect(mark!.dataset.orbInkToken).toBe(`--color-state-${PIP[pip].family}`)
    expect(Boolean(mark!.querySelector('[data-orb-badge]'))).toBe(Boolean(ORB_STATE_OF_PIP[pip].badge))
    expect(screen.getByRole('button').textContent).toContain(PIP[pip].word)
    expect(container.querySelector('[data-state-family] > span.size-2'), 'the old dot is drawn').toBeNull()
  })

  it('a calm row has no mark', () => {
    const { container } = render(<ul><ProjectThreadRow thread={t('calm', null)} onOpen={() => {}} /></ul>)
    expect(container.querySelector('[data-orb-mark]')).toBeNull()
  })
})

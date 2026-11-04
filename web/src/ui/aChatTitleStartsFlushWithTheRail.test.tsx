import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ThreadRail } from './ThreadRail'
import type { Thread, ThreadPip } from '../lib/useThreads'

// ── A chat's title in the sidebar starts flush with the rail's other rows ───────────────────────
//
// The rail is narrow and a chat's title needs a few words to make sense, so none of its width goes
// to an indent. A row was padded `pl-3xl` on the left, so its title started well inside the rail,
// under its group header's name, while New chat, the group headers and the destinations above all
// start at the rail's own edge padding (`px-s`). Now a row is padded as they are: its title starts
// at the same left edge, grouped or not, with a state dot or without (the dot sits at the row's
// right end, so it pushes nothing right, and a row with no state keeps no room for one).
//
// jsdom lays nothing out, so this reads the rows' padding classes; `aChatTitleStartsFlushWithTheRail
// .spec.ts` measures the same edge in a real browser.

const LONG = 'Plan the venue walkthrough for the November meetup and confirm the AV list'
const t = (key: string, pip: ThreadPip | null, title = `Chat ${key}`): Thread => ({
  key, kind: 'chat', id: key, title, projectId: '', route: `chat/${key}`,
  pip, pinned: false, activeAt: 0, folderId: '', tagIds: [],
})

function rail() {
  const threads = [t('long', null, LONG), t('running', 'running'), t('waiting', 'waiting'), t('unread', 'unread'), t('calm', null)]
  return render(
    <ThreadRail groups={[{ id: 'folder:', name: 'No folder', threads }, { id: 'folder:talks', name: 'Talks', threads: [] }]}
      selectedKey="running" status="ready" onOpen={() => {}} onRetry={() => {}} onNewThread={() => {}} />,
  )
}

/** The left padding utility of an element's class list: `px-*` or `pl-*`. */
function leftPad(el: Element): string[] {
  return el.className.split(/\s+/).filter((c) => /^(px|pl)-/.test(c))
}

afterEach(cleanup)

describe("a sidebar row's title", () => {
  it('is padded on the left as New chat and the group headers are, and nothing more', () => {
    rail()
    const newChat = screen.getByRole('button', { name: /^New chat/ })
    const header = screen.getByRole('button', { name: /^No folder/ })
    expect(leftPad(newChat)).toEqual(['px-s'])
    expect(leftPad(header)).toEqual(['px-s'])

    const rows = [...document.querySelectorAll('[data-thread-key]')]
    expect(rows.length, 'every row is read').toBe(5)
    for (const row of rows) {
      expect(leftPad(row), row.getAttribute('data-thread-key') ?? '').toEqual(['px-s'])
    }
  })

  it('keeps its title first in the row whatever its state, the dot after it', () => {
    rail()
    for (const key of ['running', 'waiting', 'unread']) {
      const row = document.querySelector(`[data-thread-key="${key}"]`)!
      const children = [...row.children].filter((c) => !c.hasAttribute('aria-hidden'))
      expect(children[0].textContent, key).toBe(`Chat ${key}`)
      expect(children.at(-1)?.hasAttribute('data-state-family'), key).toBe(true)
    }
  })

  it('an empty group says so at the same edge', () => {
    rail()
    // Open the empty group: its "No chats yet" line starts where the rows do.
    const talks = screen.getByRole('button', { name: /^Talks/ })
    if (talks.getAttribute('aria-expanded') !== 'true') fireEvent.click(talks)
    const empty = screen.getByText('No chats yet')
    expect(leftPad(empty)).toEqual(['px-s'])
  })
})

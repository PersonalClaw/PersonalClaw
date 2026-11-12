import { describe, it, expect, afterEach, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ThreadRail } from './ThreadRail'
import type { Thread, ThreadPip } from '../lib/useThreads'

// ── A sidebar row's state is its mark ──────────────────────────────────────────────────────────
//
// Each row of the sidebar's chat history said its state in words after its title: "unread",
// "running", "waiting on you", "failed". The words took the room the title needed and repeated
// what a mark says at a glance. A row now shows its state as the orb's 12 px still mark, and the
// state stays in the row's name and in the row's tooltip, so neither colour nor shape is the only
// cue. Which colour and which form each state takes is in
// `aRowsMarkIsGreenWhileItRunsAndOrangeWhileItWaits.test.tsx`.

const t = (key: string, pip: ThreadPip | null): Thread => ({
  key, kind: 'chat', id: key, title: `Chat ${key}`, projectId: '', route: `chat/${key}`,
  pip, pinned: false, activeAt: 0, folderId: '', tagIds: [],
})
const STATES: Array<[ThreadPip, string, string]> = [
  ['unread', 'quiet', 'unread'],
  ['running', 'active', 'running'],
  ['waiting', 'waiting', 'waiting on you'],
  ['failed', 'blocked', 'failed'],
]

function rail() {
  const threads = [...STATES.map(([pip]) => t(pip, pip)), t('calm', null)]
  return render(<ThreadRail rows={threads} selectedKey="" status="ready"
    onOpen={() => {}} onRetry={() => {}} />)
}
const row = (pip: string, word: string) => screen.getByRole('button', { name: `Chat ${pip}, ${word}` })
const markOf = (el: HTMLElement) => el.querySelector<HTMLElement>('[data-state-family]')

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe("a sidebar row's state", () => {
  it('says no state in words on the row', () => {
    rail()
    for (const [pip, , word] of STATES) {
      // Words only: a mark's × is decoration. Only the badge is taken out, so a state word on the
      // row, hidden or not, would still be read here.
      const words = row(pip, word).cloneNode(true) as HTMLElement
      for (const el of words.querySelectorAll('[data-orb-badge]')) el.remove()
      expect(words.textContent).toBe(`Chat ${pip}`)
    }
  })

  it('is one mark, in its state\'s family, and a calm row has none', () => {
    rail()
    for (const [pip, family, word] of STATES) {
      expect(row(pip, word).querySelectorAll('[data-state-family]'), pip).toHaveLength(1)
      expect(markOf(row(pip, word))?.dataset.stateFamily, pip).toBe(family)
    }
    expect(markOf(screen.getByRole('button', { name: 'Chat calm' }))).toBeNull()
  })

  it('keeps the state in the row\'s name', () => {
    rail()
    for (const [pip, , word] of STATES) expect(row(pip, word)).toBeTruthy()
  })
})

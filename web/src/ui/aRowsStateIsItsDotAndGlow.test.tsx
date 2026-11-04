import { describe, it, expect, afterEach, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ThreadRail } from './ThreadRail'
import type { Thread, ThreadPip } from '../lib/useThreads'

// ── A sidebar row's state is its dot ───────────────────────────────────────────────────────────
//
// Each row of the sidebar's chat history said its state in words after its title: "unread",
// "running", "waiting on you", "failed". The words took the room the title needed and repeated
// what a coloured dot says at a glance. A row now shows its state as a dot, and the state stays in
// the row's name and in the dot's tooltip, so colour is never the only cue. Which colour each state
// takes, which glow and what reduced motion draws is the owner's rule, in
// `aRowsDotIsGreenWhileItRunsAndOrangeWhileItWaits.test.tsx`.

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
  return render(<ThreadRail groups={[{ id: 'folder:', name: 'No folder', threads }]} selectedKey="" status="ready"
    onOpen={() => {}} onRetry={() => {}} onNewThread={() => {}} />)
}
const row = (pip: string, word: string) => screen.getByRole('button', { name: `Chat ${pip}, ${word}` })
const dotOf = (el: HTMLElement) => el.querySelector<HTMLElement>('[data-state-family]')

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe("a sidebar row's state", () => {
  it('says no state in words on the row', () => {
    rail()
    for (const [pip, , word] of STATES) {
      expect(row(pip, word).textContent).toBe(`Chat ${pip}`)
    }
  })

  it('is one dot, in its state\'s family, and a calm row has none', () => {
    rail()
    for (const [pip, family, word] of STATES) {
      expect(row(pip, word).querySelectorAll('[data-state-family]'), pip).toHaveLength(1)
      expect(dotOf(row(pip, word))?.dataset.stateFamily, pip).toBe(family)
    }
    expect(dotOf(screen.getByRole('button', { name: 'Chat calm' }))).toBeNull()
  })

  it('keeps the state in the row\'s name', () => {
    rail()
    for (const [pip, , word] of STATES) expect(row(pip, word)).toBeTruthy()
  })
})

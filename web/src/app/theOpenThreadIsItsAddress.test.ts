import { describe, expect, it } from 'vitest'
import type { Thread } from '../lib/useThreads'
import { threadAt } from './threadRail'

// ── The open thread is the one the address names (#3814) ─────────────────────────────────────────
//
// The rail keeps no selection of its own: the thread it lights is the one the address opens, so a
// reload, Back, Forward and a copied address all light the same row, and nothing can light a row
// the page is not showing. A thread keeps the address of its kind (`#/chat/<key>`, `#/loops/<id>`,
// `#/code/<id>`, a run-backed loop's `#/workflows/runs/<run>`), the issue's default for the thread
// address, so every address that opened a thread before still opens it.
//
// `sub` arrives decoded (`useHashRoute` decodes each segment), and a thread's route is encoded, so a
// chat key with a `:` or a `/` in it must still match.

function chat(key: string): Thread {
  return { key: `chat:${key}`, kind: 'chat', id: key, title: key, projectId: '', route: `chat/${encodeURIComponent(key)}`, pip: null, pinned: false, activeAt: 0 }
}

function loop(id: string, kind: 'loop' | 'code' = 'loop', route = kind === 'code' ? `code/${id}` : `loops/${id}`): Thread {
  return { key: `loop:${id}`, kind, id, title: id, projectId: '', route, pip: null, pinned: false, activeAt: 0 }
}

const GROUPS = [{
  threads: [
    chat('chat-1'), chat('web:7f3a'), chat('a'), chat('a/b'),
    loop('a1b2c3d4'), loop('e5f6a7b8', 'code'), loop('c9d0e1f2', 'loop', 'workflows/runs/run-42'),
  ],
}]

describe('the open thread', () => {
  it('is the chat the address opens', () => {
    expect(threadAt('chat', 'chat-1', GROUPS)).toBe('chat:chat-1')
    expect(threadAt('chat', 'web:7f3a', GROUPS)).toBe('chat:web:7f3a')
  })

  it('is matched whole, so a chat whose key extends another is not lit for it', () => {
    expect(threadAt('chat', 'a/b', GROUPS)).toBe('chat:a/b')
    expect(threadAt('chat', 'a', GROUPS)).toBe('chat:a')
  })

  it('is none on the chat pages that are not a thread', () => {
    for (const sub of ['', 'new', 'history', 'room', 'room/r-1']) expect(threadAt('chat', sub, GROUPS)).toBe('')
  })

  it('is none for a chat the rail does not list', () => {
    expect(threadAt('chat', 'loop-a1b2c3d4-worker', GROUPS)).toBe('')
  })

  it("is the loop or code session the address opens, on any of its own sub-pages", () => {
    expect(threadAt('loops', 'a1b2c3d4', GROUPS)).toBe('loop:a1b2c3d4')
    expect(threadAt('loops', 'a1b2c3d4/cycles', GROUPS)).toBe('loop:a1b2c3d4')
    expect(threadAt('code', 'e5f6a7b8', GROUPS)).toBe('loop:e5f6a7b8')
  })

  it("is a code session opened by a loop address, an address it had before", () => {
    expect(threadAt('loops', 'e5f6a7b8', GROUPS)).toBe('loop:e5f6a7b8')
  })

  it("is the run-backed loop whose run page is open", () => {
    expect(threadAt('workflows', 'runs/run-42', GROUPS)).toBe('loop:c9d0e1f2')
  })

  it('is none on a page that is no thread', () => {
    expect(threadAt('loops', 'history', GROUPS)).toBe('')
    expect(threadAt('loops', '', GROUPS)).toBe('')
    expect(threadAt('settings', 'agent', GROUPS)).toBe('')
    expect(threadAt('workflows', 'runs', GROUPS)).toBe('')
  })
})

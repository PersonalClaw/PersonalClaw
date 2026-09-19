import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { commentStore } from './commentStore'
import { installFakeDocCommentServer, type FakeDocCommentServer } from './fakeDocCommentServer'

// ── Issue 429: document comments were localStorage-only ────────────────────────
//
// `commentStore` persisted to ONE global browser key (`doc-comments-v1`) and nothing else:
//
//     :76  const raw = localStorage.getItem(KEY)
//     :86  try { localStorage.setItem(KEY, JSON.stringify(comments)) } catch { /* ignore */ }
//
// so annotations were the only thing a user creates in this app that a cleared cache
// destroys, `personalclaw snapshot` could not carry them (the server never saw them, which
// is also why they were absent from the durability inventory), and they were invisible on a
// second device. Task comments next door were a real server-side store the whole time.
//
// These tests pin the two halves that make the difference: the write REACHES the server,
// and a write that FAILS says so instead of being swallowed the way the old
// `catch { /* ignore */ }` swallowed both the read and the write.

let server: FakeDocCommentServer

beforeEach(async () => {
  server = installFakeDocCommentServer()
  await commentStore.clear()
})
afterEach(() => { server.restore() })

describe('a document comment is persisted server-side', () => {
  it('reaches the server rather than the browser', async () => {
    await commentStore.add({ docId: 'rates.md', docLabel: 'rates.md', quote: 'q', comment: 'mine' })
    expect(server.rows()).toHaveLength(1)
    expect(server.rows()[0].doc_id).toBe('rates.md')
    expect(server.rows()[0].comment).toBe('mine')
  })

  it('writes nothing to localStorage — the old store was the ONLY copy', () => {
    // The specific key the issue names. A reintroduced write-through cache reds this, which
    // is the point: two writers over one list have no rule for which wins on a second device.
    expect(localStorage.getItem('doc-comments-v1')).toBeNull()
  })

  it('takes the id the SERVER assigned, not one the browser minted', async () => {
    const saved = await commentStore.add({ docId: 'a.md', docLabel: 'a.md', quote: 'q', comment: 'c' })
    expect(saved?.id).toBe(server.rows()[0].id)
    expect(saved?.id.startsWith('pending-')).toBe(false)
  })

  it('converts the wire\'s epoch SECONDS to the milliseconds this interface documents', async () => {
    const saved = await commentStore.add({ docId: 'a.md', docLabel: 'a.md', quote: 'q', comment: 'c' })
    expect(saved?.ts).toBe(server.rows()[0].ts * 1000)
  })

  it('hydrates the deck from the server, so another device\'s comment is visible here', async () => {
    // What localStorage could not do: the same annotation on a second browser.
    server.restore()
    server = installFakeDocCommentServer([
      { id: 'srv-elsewhere', doc_id: 'rates.md', doc_label: 'rates.md', comment: 'left on my laptop' },
    ])
    let seen = 0
    const unsubscribe = commentStore.subscribe(() => { seen += 1 })
    // `subscribe` kicks hydration; give the fetch its microtask.
    await new Promise((r) => setTimeout(r, 0))
    unsubscribe()
    expect(seen).toBeGreaterThan(0)
    expect(commentStore.all().map((c) => c.comment)).toContain('left on my laptop')
  })

  it('round-trips an edit and a delete through the server', async () => {
    const saved = await commentStore.add({ docId: 'a.md', docLabel: 'a.md', quote: 'q', comment: 'first' })
    await commentStore.update(saved!.id, { comment: 'second' })
    expect(server.rows()[0].comment).toBe('second')
    await commentStore.remove(saved!.id)
    expect(server.rows()).toHaveLength(0)
  })

  it('removes a batch in ONE call so a bulk dismiss cannot half-fail', async () => {
    const a = await commentStore.add({ docId: 'a.md', docLabel: 'a.md', quote: 'q', comment: 'a' })
    const b = await commentStore.add({ docId: 'b.md', docLabel: 'b.md', quote: 'q', comment: 'b' })
    await commentStore.removeMany([a!.id, b!.id])
    expect(server.rows()).toHaveLength(0)
    expect(commentStore.all()).toHaveLength(0)
  })
})

describe('a failed write is reported, not swallowed', () => {
  /** The toast channel `notify` dispatches on. */
  function captureToasts(): { messages: string[]; stop: () => void } {
    const messages: string[] = []
    const onToast = (e: Event) => {
      messages.push(String((e as CustomEvent).detail?.message ?? ''))
    }
    window.addEventListener('ne:toast', onToast)
    return { messages, stop: () => window.removeEventListener('ne:toast', onToast) }
  }

  it('drops the optimistic card and says so when the save fails', async () => {
    const toasts = captureToasts()
    server.breakWrites('network down')
    const saved = await commentStore.add({ docId: 'a.md', docLabel: 'a.md', quote: 'q', comment: 'lost?' })
    toasts.stop()
    expect(saved).toBeNull()
    // 🔴 The heart of the issue. The old store's `catch { /* ignore */ }` left the card on
    // screen looking saved while nothing had been written anywhere.
    expect(commentStore.all()).toHaveLength(0)
    expect(toasts.messages.join(' ')).toMatch(/save that comment/i)
  })

  it('restores the previous body when an edit fails', async () => {
    const saved = await commentStore.add({ docId: 'a.md', docLabel: 'a.md', quote: 'q', comment: 'original' })
    const toasts = captureToasts()
    server.breakWrites()
    await commentStore.update(saved!.id, { comment: 'edited' })
    toasts.stop()
    expect(commentStore.all()[0].comment).toBe('original')
    expect(toasts.messages.join(' ')).toMatch(/save that edit/i)
  })

  it('puts a comment back when its delete fails', async () => {
    const saved = await commentStore.add({ docId: 'a.md', docLabel: 'a.md', quote: 'q', comment: 'keep me' })
    const toasts = captureToasts()
    server.breakWrites()
    await commentStore.remove(saved!.id)
    toasts.stop()
    expect(commentStore.all()).toHaveLength(1)
    expect(toasts.messages.join(' ')).toMatch(/remove that comment/i)
  })

  it('reports a delete the server says it never held', async () => {
    const toasts = captureToasts()
    await commentStore.remove('no-such-id')
    toasts.stop()
    // A 404 is a real refusal, not a no-op success: the caller named a row that is not there.
    expect(toasts.messages.join(' ')).toMatch(/remove that comment/i)
  })
})

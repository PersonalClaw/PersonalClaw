import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import { memoryBackend, setStoreForTests } from './browserStore'
import { draftSessions, loadDraft, saveDraft } from './drafts'
import { forgetChatHere } from './forgetChatHere'
import { hold, pending } from './outbox'

// ── Nothing this browser kept of a chat outlives its sign-in, or the chat (#4012) ─────────────
//
// Signing out clears every draft and every send waiting for the gateway, before the sign-out is
// sent, so one that cannot reach the gateway leaves none behind; so does a signed-out screen that
// says the device was signed out elsewhere. A sign-in that only ran out keeps them, for the owner
// who signs straight back in. Deleting a chat clears its own.

const h = vi.hoisted(() => ({ pairFetch: vi.fn() }))
vi.mock('./pair', async (importOriginal) => ({ ...(await importOriginal<typeof import('./pair')>()), pairFetch: h.pairFetch }))

const draft = (text: string) => ({ text, attachments: [], mentions: [], pastes: [] })

beforeEach(async () => {
  setStoreForTests(memoryBackend())
  await saveDraft('chat-1', draft('one'))
  await saveDraft('chat-2', draft('two'))
  await hold({ client_message_id: 'msg_01cccccccccccccccccccccccc', session: 'chat-1', message: 'waiting' }, true)
  await hold({ client_message_id: 'msg_01dddddddddddddddddddddddd', session: 'chat-2', message: 'waiting too' }, true)
  h.pairFetch.mockReset()
})
afterEach(() => { cleanup(); setStoreForTests(null) })

describe('signing out', () => {
  it('clears every draft and waiting send, even when the gateway cannot be reached', async () => {
    h.pairFetch.mockRejectedValue(new TypeError('Failed to fetch'))
    const { api } = await import('./api')
    await expect(api.authLogout()).rejects.toThrow()
    expect(await draftSessions()).toEqual(new Set())
    expect([...await pending('chat-1'), ...await pending('chat-2')]).toEqual([])
  })

  it('clears them on the signed-out screen when the device was signed out, not when its sign-in ran out', async () => {
    const { SignedOutScreen } = await import('../app/SignedOutScreen')
    render(<SignedOutScreen notice={{ message: 'Your sign-in ran out.', code: 'session_expired', reason: 'expired' }} />)
    await new Promise((r) => setTimeout(r, 10))
    expect((await loadDraft('chat-1'))?.text).toBe('one')
    cleanup()
    render(<SignedOutScreen notice={{ message: 'This device was signed out.', code: 'session_signed_out', reason: 'signed_out_elsewhere' }} />)
    await waitFor(async () => expect(await draftSessions()).toEqual(new Set()))
    expect(await pending('chat-2')).toEqual([])
  })
})

describe('deleting a chat', () => {
  it('clears its draft and its waiting sends, and no other chat\'s', async () => {
    await forgetChatHere('chat-1')
    expect(await loadDraft('chat-1')).toBeNull()
    expect(await pending('chat-1')).toEqual([])
    expect((await loadDraft('chat-2'))?.text).toBe('two')
    expect(await pending('chat-2')).toHaveLength(1)
  })
})

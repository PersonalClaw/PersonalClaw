import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type MemoryMode } from '../../../lib/api'
import { clearDraft, hasContent, loadDraft, saveDraft, type Draft, type DraftContent } from '../../../lib/drafts'
import { flush, hold, isStale, pending, remove, type FlushResult, type HeldSend, type OutboxItem } from '../../../lib/outbox'
import { useChatSocket } from '../../../lib/useChatSocket'

// ── What the composer keeps through a reload, and what waits for PersonalClaw (#4012) ─────────
//
// `useKeptDraft`: each chat's draft, in this browser (`lib/drafts`): restored when the chat opens,
// written `DRAFT_WRITE_MS` after the last change, cleared when it is emptied (its send) or on
// Discard draft. A Temporary or Incognito chat keeps none, and switching a chat to one clears it.
// `useWaitingSends`: the sends the gateway could not take (`lib/outbox`), shown as "Waiting to
// send" and posted once each when the chat's socket is open again.

export const DRAFT_WRITE_MS = 500

/** The key a chat's draft is kept under: its session, or the new chat's (one per project). */
export function draftKey(sessionId: string | null, projectId = ''): string {
  return sessionId || `new-chat:${projectId}`
}

export function keepsDrafts(mode: MemoryMode): boolean {
  return mode === 'persistent'
}

export function useKeptDraft({ keyed, mode, content, restore, seeded }: {
  keyed: string
  mode: MemoryMode
  /** What the composer holds now, as a draft. */
  content: DraftContent
  /** Put a kept draft back into the composer. */
  restore: (d: Draft) => void
  /** The composer opened with text of its own (a launch's seed, a staged start): no draft is
   *  restored over it. */
  seeded: boolean
}): { restored: boolean; discard: () => void } {
  const [restored, setRestored] = useState(false)
  const contentRef = useRef(content)
  contentRef.current = content
  // Nothing is written before the kept draft is read, so a draft is never written over before it
  // is restored; what was typed meanwhile is written once it is.
  const [readFor, setReadFor] = useState('')
  const ready = readFor === keyed

  useEffect(() => {
    setRestored(false)
    let live = true
    if (!keepsDrafts(mode)) { void clearDraft(keyed); setReadFor(keyed); return }
    void loadDraft(keyed).then((d) => {
      if (!live) return
      // What the owner typed while it was read wins over what was kept.
      if (d && hasContent(d) && !seeded && !hasContent(contentRef.current)) { restore(d); setRestored(true) }
      setReadFor(keyed)
    })
    return () => { live = false }
    // Read once per chat; `mode` is checked on its own below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyed])

  // A switch to Temporary or Incognito takes the draft out of this browser at once.
  useEffect(() => { if (!keepsDrafts(mode)) void clearDraft(keyed) }, [mode, keyed])

  const serialized = JSON.stringify(content)
  useEffect(() => {
    if (!ready || !keepsDrafts(mode)) return
    const t = window.setTimeout(() => { void saveDraft(keyed, contentRef.current) }, DRAFT_WRITE_MS)
    return () => window.clearTimeout(t)
  }, [serialized, keyed, mode, ready])

  const discard = useCallback(() => { setRestored(false); void clearDraft(keyed) }, [keyed])
  return { restored, discard }
}

/** Whether the chat *session* is still on the gateway: only a 404 says it is not. */
async function chatIsThere(session: string): Promise<boolean> {
  try {
    await api.chatSessionDetail(session)
    return true
  } catch (e) {
    return !(e instanceof ApiError && e.status === 404)
  }
}

const post = (s: HeldSend) =>
  api.sendChat(s.message, s.session, s.meta, s.queue_mode, s.input_origin, { messageId: s.client_message_id })

export function useWaitingSends({ sessionId, onFlushed }: {
  sessionId: string | null
  /** What a flush did: the composer draws each sent one's bubble and says what was refused or
   *  not sent. */
  onFlushed: (r: FlushResult) => void
}) {
  const [waiting, setWaiting] = useState<OutboxItem[]>([])
  const reread = useCallback(async () => setWaiting(sessionId ? await pending(sessionId) : []), [sessionId])
  useEffect(() => { void reread() }, [reread])
  const flushing = useRef(false)
  const onFlushedRef = useRef(onFlushed)
  onFlushedRef.current = onFlushed

  const flushNow = useCallback(async (includeStale = false) => {
    if (!sessionId || flushing.current) return
    flushing.current = true
    try {
      const r = await flush(sessionId, post, () => chatIsThere(sessionId), { includeStale })
      if (r.sent.length || r.refused.length || r.dropped.length) onFlushedRef.current(r)
    } finally {
      flushing.current = false
      await reread()
    }
  }, [sessionId, reread])

  // Posted once the chat's socket is open: on its first open (a reload with PersonalClaw up) and
  // on every reopen after a drop.
  useChatSocket(() => {}, null, (connected) => { if (connected) void flushNow() })

  /** Hold *send*: the gateway could not take it. False when the chat holds `MAX_PER_CHAT`. */
  const holdSend = useCallback(async (send: HeldSend, chatExisted: boolean) => {
    const kept = await hold(send, chatExisted)
    await reread()
    return kept
  }, [reread])

  const sendNow = useCallback(() => flushNow(true), [flushNow])
  const removeSend = useCallback(async (id: string) => { await remove(id); await reread() }, [reread])
  const stale = waiting.some((i) => isStale(i))
  return { waiting, stale, holdSend, sendNow, removeSend, flushNow }
}

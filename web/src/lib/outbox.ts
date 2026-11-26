import { ApiError } from './api'
import { browserStore } from './browserStore'

// ── A send the gateway could not take, waiting for PersonalClaw to be back ────────────────────
//
// A message sent while the connection is down, or while PersonalClaw restarts, waits in this
// browser's `outbox` store (`browserStore.ts`) with the `client_message_id` it was sent with, and
// the composer shows it as "Waiting to send" (#4012). It is posted again when the chat's socket is
// back, in the order it was held and once each: the id makes a send the gateway had in fact taken
// answer `duplicate: true`, so it shows one bubble, not two.
//
// Only two failures are held (`isHeld`): a request that never reached the gateway (the fetch
// itself failed), and one answered `503 gateway_restarting` (a restart that drains first). Any
// other refusal is an answer: it is shown, and never kept to send again.
//
// Nothing is ever sent into a deleted chat. Deleting a chat clears its waiting sends here, and a
// send held for a chat that existed is posted only once the chat is read back: one deleted
// meanwhile, in another tab or on another device, answers 404, and its sends go unsent and are
// said to have been. A chat's first message is held as it was (the chat does not exist yet).

/** After this long a waiting send asks "Send now or remove" rather than going on its own. */
export const STALE_AFTER_MS = 24 * 60 * 60 * 1000
export const MAX_PER_CHAT = 50

/** What a held send posts again: `api.sendChat`'s arguments, its id among them. */
export interface HeldSend {
  client_message_id: string
  session: string
  message: string
  meta?: Record<string, unknown>
  queue_mode?: string
  input_origin?: string
}

export interface OutboxItem extends HeldSend {
  /** When it was held, in ms since the epoch. */
  held_at: number
  /** Whether its chat was already on the gateway when it was held: false only for a first
   *  message, which makes the chat. */
  chat_existed: boolean
}

/** Whether the send's failure *err* is one a send waits through rather than an answer. */
export function isHeld(err: unknown): boolean {
  if (err instanceof ApiError) return err.status === 503 && err.code === 'gateway_restarting'
  if (err instanceof DOMException && err.name === 'AbortError') return false
  return err instanceof TypeError
}

/** Whether a waiting send has waited long enough to ask before it goes. */
export function isStale(item: Pick<OutboxItem, 'held_at'>, now = Date.now()): boolean {
  return now - item.held_at >= STALE_AFTER_MS
}

async function all(): Promise<OutboxItem[]> {
  const kept = await browserStore().entries<OutboxItem>('outbox')
  return kept.map(([, v]) => v).filter((v) => v && typeof v.client_message_id === 'string')
}

/** *session*'s waiting sends, in the order they were held. */
export async function pending(session: string): Promise<OutboxItem[]> {
  return (await all())
    .filter((i) => i.session === session)
    .sort((a, b) => a.held_at - b.held_at || (a.client_message_id < b.client_message_id ? -1 : 1))
}

/** Hold *send* for *session*. Refused (returns false) past `MAX_PER_CHAT`, or for a send with no
 *  id, which could not be told apart from a second copy of itself. */
export async function hold(send: HeldSend, chatExisted: boolean, now = Date.now()): Promise<boolean> {
  if (!send.session || !send.client_message_id) return false
  const waiting = await pending(send.session)
  if (waiting.some((i) => i.client_message_id === send.client_message_id)) return true
  if (waiting.length >= MAX_PER_CHAT) return false
  await browserStore().put<OutboxItem>('outbox', send.client_message_id, { ...send, held_at: now, chat_existed: chatExisted })
  return true
}

export async function remove(clientMessageId: string): Promise<void> {
  await browserStore().delete('outbox', clientMessageId)
}

/** Every waiting send of *session* goes unsent: its chat was deleted. */
export async function clearChat(session: string): Promise<void> {
  for (const item of await pending(session)) await remove(item.client_message_id)
}

/** Every waiting send goes unsent: on sign-out. */
export async function clearAllWaiting(): Promise<void> {
  await browserStore().clear('outbox')
}

export interface FlushResult {
  /** Sent, or found already sent (`duplicate: true`), each with the gateway's answer. */
  sent: (OutboxItem & { answer: unknown })[]
  /** Answered with a refusal: shown, never sent again. */
  refused: { item: OutboxItem; error: unknown }[]
  /** Not sent: their chat was deleted. */
  dropped: OutboxItem[]
  /** Still waiting: the gateway was not reached again, or they are a day old and ask first. */
  waiting: OutboxItem[]
}

/** Post *session*'s waiting sends with *post*, in order, once each. A send answered with a
 *  receipt or a refusal goes from the outbox; a failure that is held (`isHeld`) stops the flush,
 *  so what is behind it keeps its place. *chatExists* reads the chat back: a chat that existed when
 *  a send was held and is gone now takes none of them. A day-old send is left for the owner's
 *  Send now, unless *includeStale*. */
export function flush(
  session: string,
  post: (send: HeldSend) => Promise<unknown>,
  chatExists: () => Promise<boolean>,
  opts: { includeStale?: boolean; now?: number } = {},
): Promise<FlushResult> {
  // One flush of a chat at a time in this page: a socket's open and a Send now at once share one,
  // so no send is posted twice from here. Another tab's flush of the same send carries the same id,
  // which the gateway answers `duplicate: true` without running it again.
  const running = inFlight.get(session)
  if (running) return running.then(() => ({ sent: [], refused: [], dropped: [], waiting: [] }))
  const run = flushOnce(session, post, chatExists, opts).finally(() => inFlight.delete(session))
  inFlight.set(session, run)
  return run
}

const inFlight = new Map<string, Promise<FlushResult>>()

async function flushOnce(
  session: string,
  post: (send: HeldSend) => Promise<unknown>,
  chatExists: () => Promise<boolean>,
  { includeStale = false, now = Date.now() }: { includeStale?: boolean; now?: number },
): Promise<FlushResult> {
  const result: FlushResult = { sent: [], refused: [], dropped: [], waiting: [] }
  const items = await pending(session)
  if (!items.length) return result
  if (items.some((i) => i.chat_existed) && !(await chatExists())) {
    for (const item of items.filter((i) => i.chat_existed)) {
      await remove(item.client_message_id)
      result.dropped.push(item)
    }
  }
  const sendable = items.filter((i) => !result.dropped.includes(i))
  for (let at = 0; at < sendable.length; at++) {
    const item = sendable[at]
    if (!includeStale && isStale(item, now)) {
      result.waiting.push(...sendable.slice(at))
      break
    }
    const { held_at: _h, chat_existed: _c, ...send } = item
    try {
      const answer = await post(send)
      await remove(item.client_message_id)
      result.sent.push({ ...item, answer })
    } catch (error) {
      if (isHeld(error)) {
        result.waiting.push(...sendable.slice(at))
        break
      }
      await remove(item.client_message_id)
      result.refused.push({ item, error })
    }
  }
  return result
}

import type { MemoryMode } from './api'
import type { ComposerValue } from '../ui/composer/types'

/** ── A draft handed to a new chat, in memory ──────────────────────────────────────────────────
 *
 *  A project's start box sends its draft from a new chat bound to the project: it stages what the
 *  owner wrote and picked here under a one-use token, then opens `#/chat/new?project=<id>&start=<token>`
 *  (`app/linkState.ts`'s writer, which writes `start` only for the staged token). The new chat
 *  takes the stage once, by that token, and sends it through its own first send
 *  (`chat/composer/useChatComposer`), so the chat is made with the project and the picks made in
 *  the box. The text and the files never ride the address: a reload, Back, or a copied link finds
 *  no stage and opens an empty new chat, since a link restores state and never acts. The precedent
 *  is the staged widget action (`ui/widget/stagedAction`).
 *
 *  A leaf, importing types only, so the chat's launch writer can check a token without importing
 *  the composer. One stage at a time: a second hand-off replaces the first. */

/** What the owner picked in the box, as the chat's composer holds it. */
export interface NewChatPicks extends Partial<ComposerValue> {
  memoryMode?: MemoryMode
  /** A reply style chosen in Chat preferences, by its id. */
  replyStyle?: string
  /** An explanations choice from Chat preferences, by its id. */
  explain?: string
}

export interface NewChatHandoff {
  /** The draft, sent as the chat's first message. */
  text: string
  /** Files attached in the box, attached to the chat before it sends. */
  files: File[]
  picks: NewChatPicks
  /** The project the chat is bound to. A stage is taken only by a chat in this project. */
  project: string
}

let staged: (NewChatHandoff & { token: string }) | null = null

function newToken(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

/** Stage *handoff*, replacing any stage before it. Returns its one-use token. */
export function stageNewChat(handoff: NewChatHandoff): string {
  const token = newToken()
  staged = { ...handoff, files: [...handoff.files], picks: { ...handoff.picks }, token }
  return token
}

/** Whether *token* is the staged hand-off's own: the only `start` a chat's address carries. */
export function isStagedNewChat(token: unknown): token is string {
  return typeof token === 'string' && !!token && staged?.token === token
}

/** The staged hand-off for *token* in *project*, taken: once taken, nothing is staged. null for
 *  any other token or project, and the stage is left where it is. */
export function takeNewChat(token: string, project: string): NewChatHandoff | null {
  if (!isStagedNewChat(token) || !staged || staged.project !== project) return null
  const { token: _t, ...handoff } = staged
  staged = null
  return handoff
}

/** Drop the stage (tests). */
export function clearNewChat(): void {
  staged = null
}

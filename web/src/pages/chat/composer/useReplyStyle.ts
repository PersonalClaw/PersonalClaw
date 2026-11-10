import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type ReplyStyle, type ReplyStylePreview, type ReplyStyleState } from '../../../lib/api'
import { notify } from '../../../app/appSdk'

/** What a pick in Chat preferences came to: written (`saved`), held until the chat exists
 *  (`held`), refused by the server (`failed`, the row back as it was), or overtaken by a later pick
 *  whose answer is the one that counts (`stale`). */
export type PickResult = 'saved' | 'held' | 'failed' | 'stale'

/** Runs a chat's preference writes one after another: each starts once the one before it has
 *  answered, so the server takes the picks in the order they were made and the last answer is
 *  what the chat holds. `useChatComposer` shares one between this hook and `useExplainChoice`. */
export type InTurn = <T>(write: () => Promise<T>) => Promise<T>

/** A queue of writes, each in its turn (`InTurn`). */
export function useInTurn(): InTurn {
  const tail = useRef<Promise<unknown>>(Promise.resolve())
  return useCallback(<T,>(write: () => Promise<T>): Promise<T> => {
    const run = tail.current.then(write)
    tail.current = run.catch(() => {})
    return run
  }, [])
}

/** What Chat preferences' Reply style section shows for the open chat. */
export interface ReplyStylePillState {
  /** What THIS conversation states: a style id, or `''` to follow its agent. */
  choice: string
  /** The style the next turn resolves to, as the backend resolved it. */
  effective: string
  /** The scope that decided it, or `''` before the chat exists (the pill then shows `choice`). */
  source: string
  /** What the bound agent's definition states. */
  agentDefault: string
  /** Which voice the chat's turns carry and its text, as the backend previews it. */
  preview: ReplyStylePreview | null
  styles: ReplyStyle[]
  /** The server's sentence for the latest pick it refused, until the next pick. */
  refused: string
  onSelect: (style: string) => Promise<PickResult>
}

const EMPTY = { choice: '', effective: 'default', source: '', agentDefault: '', preview: null as ReplyStylePreview | null }

function fromResolved(r: Partial<ReplyStyleState>) {
  return {
    choice: r.reply_style ?? '',
    effective: r.reply_style_effective ?? 'default',
    source: r.reply_style_source ?? '',
    agentDefault: r.reply_style_agent_default ?? '',
    preview: r.reply_style_preview ?? null,
  }
}

const told = (error: string) => notify(`Couldn't save this chat's reply style: ${error}`, 'error')

/** The open chat's reply style.
 *
 *  It RESOLVES NOTHING: the order lives once, in the backend (`reply_style.REPLY_STYLE_PRECEDENCE`),
 *  so the state shown is what the session detail carried (`restore`) or what a PATCH answered,
 *  never the click itself. A pick made before the chat exists has nothing to PATCH: it is held
 *  and written when the chat is created (`persistOnCreate`).
 *  A pick shows at once. Picks reach the server in turn (`InTurn`): one a later pick has overtaken
 *  before its turn is not sent, every answer is recorded as what the chat holds, and only the
 *  latest pick sets what the section shows or tells a refusal, which goes back to the last pick
 *  the server took and is said in the section (`refused`) as well as in a notice. */
export function useReplyStyle(currentSession: () => string | null, shared?: InTurn) {
  const own = useInTurn()
  const inTurn = shared ?? own
  const [state, setState] = useState(EMPTY)
  const [styles, setStyles] = useState<ReplyStyle[]>([])
  const [refused, setRefused] = useState('')
  const latest = useRef(0)
  /** What the server last answered or a detail carried: what a refused pick goes back to. */
  const confirmed = useRef(EMPTY)
  /** A pick made before the chat exists, written once it is created. */
  const held = useRef('')
  const adopt = useCallback((next: typeof EMPTY) => { confirmed.current = next; setState(next) }, [])

  useEffect(() => {
    let live = true
    api.replyStyles().then((r) => { if (live) setStyles(r?.styles ?? []) }).catch(() => {})
    return () => { live = false }
  }, [])

  /** One write, in its turn. A pick a later one has overtaken by then is not sent. */
  const write = useCallback(async (session: string, style: string, overtaken: () => boolean = () => false) => {
    try {
      return { r: await inTurn(() => (overtaken() ? Promise.resolve(null) : api.setSessionReplyStyle(session, style))), error: '' }
    } catch (e) {
      return { r: null, error: String((e as Error)?.message || e) }
    }
  }, [inTurn])

  const onSelect = useCallback(async (style: string): Promise<PickResult> => {
    const session = currentSession()
    const pick = ++latest.current
    setRefused('')
    if (!session) { held.current = style; setState((s) => ({ ...s, choice: style, source: '' })); return 'held' }
    setState((s) => ({ ...s, choice: style }))
    const { r, error } = await write(session, style, () => pick !== latest.current)
    // Every answer is what the chat holds now, the picks before it included.
    if (r) confirmed.current = fromResolved(r)
    // Only the latest pick is shown, and only its refusal told.
    if (pick !== latest.current) return 'stale'
    setState(confirmed.current)
    if (!r) { setRefused(error); told(error); return 'failed' }
    return 'saved'
  }, [currentSession, write])

  /** Adopt the RESOLVED state a session detail carries, on every open. */
  const restore = useCallback((d: Partial<ReplyStyleState>) => adopt(fromResolved(d)), [adopt])

  /** An explanations answer: its preview is the whole text the next turn appends. The reply style
   *  it carries too is this hook's own answers' to say. */
  const adoptPreview = useCallback((r: Partial<ReplyStyleState>) => {
    const preview = r.reply_style_preview ?? null
    confirmed.current = { ...confirmed.current, preview }
    setState((s) => ({ ...s, preview }))
  }, [])

  /** Write a pick made before the chat existed onto the chat just created. */
  const persistOnCreate = useCallback(async (session: string) => {
    const style = held.current
    held.current = ''
    if (!style) return
    const { r, error } = await write(session, style)
    if (r) adopt(fromResolved(r))
    else told(error)
  }, [write, adopt])

  const pill: ReplyStylePillState = { ...state, styles, refused, onSelect }
  return { pill, restore, adoptPreview, persistOnCreate }
}

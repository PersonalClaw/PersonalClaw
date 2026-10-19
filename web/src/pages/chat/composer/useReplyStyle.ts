import { useCallback, useEffect, useState } from 'react'
import { api, type ReplyStyle, type ReplyStylePreview, type ReplyStyleState } from '../../../lib/api'
import { notify } from '../../../app/appSdk'

/** What the composer's reply-style picker shows for the open chat. */
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
  onSelect: (style: string) => Promise<void>
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

/** The open chat's reply style.
 *
 *  It RESOLVES NOTHING: the order lives once, in the backend (`reply_style.REPLY_STYLE_PRECEDENCE`),
 *  so the state shown is what the session detail carried (`restore`) or what a PATCH answered,
 *  never the click itself. A pick made before the chat exists has nothing to PATCH: it is held
 *  as a choice with `source: ''` and written when the chat is created (`persistOnCreate`).
 *  A failed write is told, as every composer pick is, and the picker keeps what it showed. */
export function useReplyStyle(currentSession: () => string | null) {
  const [state, setState] = useState(EMPTY)
  const [styles, setStyles] = useState<ReplyStyle[]>([])

  useEffect(() => {
    let live = true
    api.replyStyles().then((r) => { if (live) setStyles(r?.styles ?? []) }).catch(() => {})
    return () => { live = false }
  }, [])

  const write = useCallback(async (session: string, style: string) => {
    try {
      return await api.setSessionReplyStyle(session, style)
    } catch (e) {
      notify(`Couldn't apply this reply style to this session: ${String((e as Error)?.message || e)}`, 'error')
      return null
    }
  }, [])

  const onSelect = useCallback(async (style: string) => {
    const session = currentSession()
    if (!session) { setState((s) => ({ ...s, choice: style, source: '' })); return }
    const r = await write(session, style)
    if (r) setState(fromResolved(r))
  }, [currentSession, write])

  /** Adopt the RESOLVED state a session detail carries, on every open. */
  const restore = useCallback((d: Partial<ReplyStyleState>) => setState(fromResolved(d)), [])

  /** Write a pick made before the chat existed onto the chat just created. */
  const persistOnCreate = useCallback(async (session: string) => {
    if (!state.choice) return
    const r = await write(session, state.choice)
    if (r) setState(fromResolved(r))
  }, [state.choice, write])

  const pill: ReplyStylePillState = { ...state, styles, onSelect }
  return { pill, restore, persistOnCreate }
}

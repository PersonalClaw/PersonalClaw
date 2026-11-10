import { useCallback, useRef, useState } from 'react'
import { api, type ExplainState, type ReplyStyleState } from '../../../lib/api'
import { notify } from '../../../app/appSdk'
import { useInTurn, type InTurn, type PickResult } from './useReplyStyle'

/** What Chat preferences' Explanations section shows for the open chat. */
export interface ExplainChoiceState {
  /** What THIS chat states: an `explanations.json` id, or `''` to follow your default. */
  choice: string
  /** The choice the next turn resolves to, as the backend resolved it. */
  effective: string
  /** The scope that decided it, or `''` before the chat exists (the section then shows `choice`). */
  source: string
  /** Your default (`agent.explain`), as the session detail carried it; `''` until read. */
  homeDefault: string
  /** The server's sentence for the latest pick it refused, until the next pick. */
  refused: string
  onSelect: (explain: string) => Promise<PickResult>
}

const EMPTY = { choice: '', effective: 'default', source: '', homeDefault: '' }

function fromResolved(r: Partial<ExplainState>) {
  return {
    choice: r.explain ?? '',
    effective: r.explain_effective ?? 'default',
    source: r.explain_source ?? '',
    homeDefault: r.explain_default ?? '',
  }
}

const toldExplain = (error: string) => notify(`Couldn't save this chat's explanations: ${error}`, 'error')

/** The open chat's explanations choice: `useReplyStyle`'s twin, beside it.
 *
 *  It RESOLVES NOTHING: the order lives once, in the backend (`explanations.EXPLAIN_PRECEDENCE`).
 *  A pick before the chat exists is held and written when it is created (`persistOnCreate`).
 *  Picks reach the server in turn (`InTurn`), as the reply style's do and in the same queue; a
 *  refused latest pick goes back to the last one the server took, and says so in the section
 *  (`refused`) as well as in a notice. Each answer also carries the reply-style state, whose
 *  preview it changes, for `onVoice`. */
export function useExplainChoice(
  currentSession: () => string | null,
  onVoice: (r: Partial<ReplyStyleState>) => void,
  shared?: InTurn,
) {
  const own = useInTurn()
  const inTurn = shared ?? own
  const [state, setState] = useState(EMPTY)
  const [refused, setRefused] = useState('')
  const latest = useRef(0)
  /** What the server last answered or a detail carried: what a refused pick goes back to. */
  const confirmed = useRef(EMPTY)
  /** A pick made before the chat exists, written once it is created. */
  const held = useRef('')
  const adopt = useCallback((next: typeof EMPTY) => { confirmed.current = next; setState(next) }, [])

  /** One write, in its turn. A pick a later one has overtaken by then is not sent. */
  const write = useCallback(async (session: string, explain: string, overtaken: () => boolean = () => false) => {
    try {
      return { r: await inTurn(() => (overtaken() ? Promise.resolve(null) : api.setSessionExplain(session, explain))), error: '' }
    } catch (e) {
      return { r: null, error: String((e as Error)?.message || e) }
    }
  }, [inTurn])

  const onSelect = useCallback(async (explain: string): Promise<PickResult> => {
    const session = currentSession()
    const pick = ++latest.current
    setRefused('')
    if (!session) { held.current = explain; setState((s) => ({ ...s, choice: explain, source: '' })); return 'held' }
    setState((s) => ({ ...s, choice: explain }))
    const { r, error } = await write(session, explain, () => pick !== latest.current)
    // Every answer is what the chat holds now; its preview is the Reply style section's too.
    if (r) { confirmed.current = fromResolved(r); onVoice(r) }
    if (pick !== latest.current) return 'stale'
    setState(confirmed.current)
    if (!r) { setRefused(error); toldExplain(error); return 'failed' }
    return 'saved'
  }, [currentSession, write, onVoice])

  /** Adopt the RESOLVED state a session detail carries, on every open. */
  const restore = useCallback((d: Partial<ExplainState>) => adopt(fromResolved(d)), [adopt])

  /** Your default, read when Chat preferences opens on a chat not created yet (no detail carries
   *  it then): `agent.explain` from the config. */
  const readHomeDefault = useCallback(async () => {
    try {
      const cfg = await api.personalclawConfig()
      const explain = String(cfg?.agent?.explain ?? 'default')
      confirmed.current = { ...confirmed.current, homeDefault: explain }
      setState((s) => ({ ...s, homeDefault: explain }))
    } catch { /* the row then names no default: "Your default" alone */ }
  }, [])

  /** Write a pick made before the chat existed onto the chat just created. */
  const persistOnCreate = useCallback(async (session: string) => {
    const explain = held.current
    held.current = ''
    if (!explain) return
    const { r, error } = await write(session, explain)
    if (r) { adopt(fromResolved(r)); onVoice(r) }
    else toldExplain(error)
  }, [write, onVoice, adopt])

  const choice: ExplainChoiceState = { ...state, refused, onSelect }
  return { choice, restore, readHomeDefault, persistOnCreate }
}

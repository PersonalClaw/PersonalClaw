import { useEffect, useRef } from 'react'
import type { TaskMode } from '../../../lib/api'
import type { RouteProps } from '../../../app/useQueryState'

/** The task modes a chat has, as its address spells them (`?mode=`). */
const TASK_MODES: readonly TaskMode[] = ['agent', 'ask', 'plan', 'build']

/** The task mode the address names, or null for none, or for one the chat does not have. */
export function taskModeFromQuery(query: Record<string, string>): TaskMode | null {
  const mode = query.mode
  return (TASK_MODES as readonly string[]).includes(mode ?? '') ? (mode as TaskMode) : null
}

/**
 * What to write to the address so it names the chat's task mode, or null when it already does.
 *
 * Agent is the default and is written as no key. Nothing is written until the chat knows its own
 * mode (`postureKnown`): a new thread knows it at once, an existing thread once its first read is
 * in, so an address is never corrected to a mode the thread has not said it has.
 */
export function modeQueryPatch({ query, taskMode, postureKnown }: {
  query: Record<string, string>
  taskMode: TaskMode
  postureKnown: boolean
}): Record<string, string | null> | null {
  if (!postureKnown) return null
  const want = taskMode === 'agent' ? null : taskMode
  const has = query.mode === undefined || query.mode === '' ? null : query.mode
  return has === want ? null : { mode: want }
}

/**
 * The chat's task mode in its address (`?mode=`), both ways, for what the person SEES only.
 *
 * - A new thread starts in the address's mode (`initialTaskMode` at mount) and follows the address
 *   when it moves to another mode (Back, Forward, a link into the open window). An address with no
 *   mode names Agent, as Agent is written as no key.
 * - An existing thread keeps the mode its gateway copy has; the address is corrected to it.
 * - Every mode the chat shows is written back, as a replace, once it is known.
 *
 * Nothing here writes a mode to the gateway. A new thread's mode reaches the gateway only when the
 * person sends, as a mode picked on the pill does; an address never changes how a chat runs.
 */
export function useTaskModeInUrl({ query, setQuery, sessionId, taskMode, followMode, postureKnown }: {
  query: Record<string, string>
  setQuery: RouteProps['setQuery']
  sessionId: string | null
  taskMode: TaskMode
  /** Moves a new thread to the mode the address moved to, without writing it anywhere. The caller
   *  sets the thread up as a new thread a link opens: approval back to Normal with it, so a link
   *  never leaves an auto-approve the person picked for another mode. */
  followMode: (mode: TaskMode) => void
  postureKnown: boolean
}): void {
  const named = taskModeFromQuery(query)
  const addressMode: TaskMode = named ?? 'agent'
  // Follow the address only when it MOVES: a mode just picked on the pill is ahead of the address
  // until the write below lands, and must not be pulled back to the address's old value.
  const lastNamed = useRef(named)
  useEffect(() => {
    if (lastNamed.current === named) return
    lastNamed.current = named
    if (!sessionId && addressMode !== taskMode) followMode(addressMode)
  }, [named]) // eslint-disable-line react-hooks/exhaustive-deps

  // In the render where the address has just moved a new thread to another mode, the pill has not
  // caught up yet: writing now would put the old mode back over the address the person just went to.
  const following = !sessionId && addressMode !== taskMode && named !== lastNamed.current
  const patch = following ? null : modeQueryPatch({ query, taskMode, postureKnown })
  const patchKey = patch ? JSON.stringify(patch) : ''
  useEffect(() => {
    if (!patch) return
    if ('mode' in patch) lastNamed.current = patch.mode ? taskModeFromQuery({ mode: patch.mode }) : null
    setQuery(patch, { replace: true })
  }, [patchKey]) // eslint-disable-line react-hooks/exhaustive-deps
}

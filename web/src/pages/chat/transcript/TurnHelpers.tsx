import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Ban, Check, CircleStop, Loader2 } from 'lucide-react'
import { api, ApiError, type SpawnedAgentStatus } from '../../../lib/api'
import { useChatSocket, type WsMessage } from '../../../lib/useChatSocket'
import { reportActionFailure } from '../../../app/reportingWrite'
import { Button } from '../../../ui/Button'
import { coreToolName } from '../coreToolName'
import { foldSubagentEvent } from '../subagentCards'
import type { SubagentCard } from '../chatTypes'

// ── The helpers a step started, under that step (#3820) ─────────────────────────────────────────
//
// "Background subagents appear inside the parent turn's collapsed steps, not only in the pane's
// Subagents tab." A `subagent_run` call that started helpers lists their ids in its result, which
// the chat keeps with the call, so its turn finds them again after a reload. Each helper is a row
// under the call: its task, its agent and its state, read from the gateway (`GET /api/spawn/{id}`)
// and then from its own live frames. A running one has a Stop. The Activity panel's Subagents tab
// stays the chat's full list.

/** A helper a call started, as its result names it. */
export interface HelperRef { id: string; agent: string; task: string }

/** The line each started helper takes in `subagent_run`'s result (`mcp_subagents.py`):
 *  `  <8 hex> (<agent>): <task>`, the agent left out when none was named. */
const HELPER_LINE = /^ {2}([0-9a-f]{8})(?: \(([^)]*)\))?: (.*)$/

/** The helpers a `subagent_run` call started, from its result, whichever runtime made the call.
 *  A batch (two or more tasks) starts a run instead, which its own card shows, and a task that
 *  did not start has no helper: neither lists one. */
export function helpersFromTool(tool: string | undefined, output: string | undefined): HelperRef[] {
  if (coreToolName(tool) !== 'subagent_run' || !output) return []
  const lines = output.split('\n')
  const at = lines.findIndex((l) => /^Spawned \d+ subagent\(s\)/.test(l))
  if (at < 0) return []
  const out: HelperRef[] = []
  for (const line of lines.slice(at + 1)) {
    const m = HELPER_LINE.exec(line)
    if (!m) break
    out.push({ id: m[1], agent: m[2] ?? '', task: m[3] })
  }
  return out
}

/** A helper's card from the gateway's read of it, kept over what the call's result said. */
function cardOf(ref: HelperRef, s: SpawnedAgentStatus): SubagentCard {
  return {
    id: ref.id, task: s.title || s.task || ref.task, agent: s.agent || ref.agent, done: !!s.done,
    lastTool: s.last_tool || undefined, error: s.error || null, result: s.result,
    ...(s.stopped ? { stopped: true } : {}),
  }
}

/** One helper's card: the chat's live card once it has one, else the gateway's read of it with
 *  the helper's own frames folded on. A frame for a helper the chat never saw start (a page loaded
 *  after it did) reaches its row this way. `gone`: the gateway no longer knows it (a list cleared,
 *  a helper removed), so its state is not known. */
function useHelper({ id, agent, task }: HelperRef, session: string | undefined, live: SubagentCard | undefined): { sub: SubagentCard; gone: boolean; readError: string } {
  const [card, setCard] = useState<SubagentCard>({ id, task, agent, done: false })
  const [gone, setGone] = useState(false)
  const [readError, setReadError] = useState('')
  const read = useCallback(() => {
    api.spawnedAgentStatus(id)
      .then((s) => { setCard(cardOf({ id, agent, task }, s)); setGone(false); setReadError('') })
      // The call's own words stay. A 404 says the gateway has no state to give; any other failure
      // says the state was not read, rather than leaving the row saying it runs.
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setGone(true)
        else setReadError(e instanceof Error ? e.message : String(e))
      })
  }, [id, agent, task])
  // Read once while the chat has no live card for it; a reconnect reads it again.
  const hasLive = !!live
  useEffect(() => { if (!hasLive) read() }, [hasLive, read])
  useChatSocket((m: WsMessage) => {
    if (!m.type.startsWith('subagent_') || m.data?.id !== id) return
    if (session && m.data?.session && m.data.session !== session) return
    setCard((c) => foldSubagentEvent([c], m.type, m.data)[0] ?? c)
    setGone(false)
    setReadError('')
  }, read)
  return live ? { sub: live, gone: false, readError: '' } : { sub: card, gone, readError }
}

export function TurnHelpers({ refs, live, session }: {
  refs: HelperRef[]
  /** The chat's live helper cards (the Subagents tab's list). */
  live: SubagentCard[]
  /** The chat's key as its frames name it. */
  session?: string
}) {
  if (!refs.length) return null
  return (
    <ul aria-label="Helpers this step started" className="ml-l flex flex-col gap-xs border-l border-outline-variant/40 pl-m">
      {refs.map((r) => <HelperRow key={r.id} helperRef={r} session={session} live={live.find((c) => c.id === r.id)} />)}
    </ul>
  )
}

function HelperRow({ helperRef, session, live }: { helperRef: HelperRef; session?: string; live?: SubagentCard }) {
  const { sub, gone, readError } = useHelper(helperRef, session, live)
  const [stopping, setStopping] = useState(false)
  const unrun = sub.done && !!sub.neverRan
  const stopped = sub.done && !!sub.stopped && !unrun
  const failed = sub.done && !!sub.error && !unrun && !stopped
  const state = gone && !sub.done ? 'No longer tracked' : readError && !sub.done ? `State not read: ${readError}` : !sub.done
    ? `Running${sub.lastTool ? ` · ${sub.lastTool}` : ''}`
    : unrun ? (sub.declined ? 'Not started: you declined' : 'Not started')
      : stopped ? 'Stopped' : failed ? `Failed: ${sub.error}` : 'Done'
  const unread = !!readError && !sub.done && !gone
  const Icon = gone && !sub.done ? CircleStop : unread ? AlertTriangle : !sub.done ? Loader2 : unrun ? Ban : stopped ? CircleStop : failed ? AlertTriangle : Check
  const tone = failed ? 'text-danger' : sub.done && !unrun && !stopped ? 'text-primary' : 'text-on-surface-low'
  const stop = () => {
    setStopping(true)
    api.cancelSpawnedAgent(sub.id)
      .catch(reportActionFailure('stop this helper'))
      .finally(() => setStopping(false))
  }
  return (
    <li className="flex min-w-0 items-center gap-s py-xs">
      <Icon size={14} aria-hidden className={`shrink-0 ${tone} ${!sub.done && !gone && !unread ? 'animate-spin' : ''}`} />
      <span className="min-w-0 flex-1">
        <span data-type="label-s" className="block truncate text-on-surface" title={sub.task}>{sub.title || sub.task || 'A helper'}</span>
        <span data-type="caption" className={`block truncate ${failed ? 'text-danger' : 'text-on-surface-low'}`}>
          {sub.agent || 'helper'} · {state}
        </span>
      </span>
      {!sub.done && !gone && (
        <Button variant="ghost" size="xs" loading={stopping} onClick={stop}
          ariaLabel={`Stop helper: ${sub.title || sub.task || sub.id}`}>Stop</Button>
      )}
    </li>
  )
}

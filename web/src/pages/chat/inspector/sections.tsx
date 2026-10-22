import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Box, FilePen } from 'lucide-react'
import { api, type McpServer, type ProjectItem, type RunningList, type RunningRow } from '../../../lib/api'
import { useChatSocket, type WsMessage } from '../../../lib/useChatSocket'
import { fmtElapsed } from '../../../lib/duration'
import { reportActionFailure } from '../../../app/reportingWrite'
import { useContextReading } from '../../../app/pinnedContext'
import { Button } from '../../../ui/Button'
import { TextLink } from '../../../ui/TextLink'
import { InlineError } from '../../../ui/InlineError'
import { RowHitTarget } from '../../../ui/RowHitTarget'
import { ContextView, contextLineSaid } from '../../../ui/composer/ContextDisclosure'
import { serverHealth } from '../../tools/serverHealth'
import { useChatEnvironment, openTheBoundary } from './chatEnvironment'
import type { ChatOutput } from './chatOutputs'

// ── The session inspector's sections that read beyond the transcript (#3820) ────────────────────
//
// Environment, Context, Connectors, Outputs and Background tasks. Each is a folded summary line
// and an open body. None holds an authority of its own: Environment opens the composer's Execution
// Boundary control, a connector's sign-in and its settings are on the Tools page, and a task's Stop
// is the same call its own page makes. Opening a section reads; it never signs in, reconnects or
// stops anything.

const ROW = 'group relative flex min-h-11 items-center gap-s rounded-md px-s py-xs transition-colors hover:bg-surface-high has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary'

const baseName = (p: string) => p.replace(/\/+$/, '').split('/').pop() || p

// ── Environment ──

/** The folded line: the permission mode, the folder and the reach. */
export function useEnvironmentSummary(): string {
  const env = useChatEnvironment()
  return env ? [env.permission, baseName(env.folder), env.reach.label].join(' · ') : 'Not read yet'
}

/** The chat's project by name, read once it has one. */
function useProjectName(id: string): string {
  const [projects, setProjects] = useState<ProjectItem[] | null>(null)
  const [loadError, setLoadError] = useState('')
  useEffect(() => {
    if (!id) return
    api.projects().then(setProjects).catch((e: unknown) => setLoadError(e instanceof Error ? e.message : String(e)))
  }, [id])
  if (!id) return 'None'
  const named = projects?.find((p) => p.id === id)?.name
  return named || (loadError ? `${id} (its name was not read: ${loadError})` : id)
}

export function EnvironmentBody() {
  const env = useChatEnvironment()
  const project = useProjectName(env?.projectId ?? '')
  if (!env) return <p data-type="body-s" className="text-on-surface-low">The composer has not said where this chat works yet.</p>
  const rows: [string, string][] = [
    ['Project', project], ['Working folder', env.folder], ['Sandbox', env.sandbox.said], ['Network', env.reach.said],
    ['Permission', env.permission], ['Memory', env.memory],
  ]
  return (
    <div className="flex flex-col gap-s">
      <dl className="grid grid-cols-[auto_1fr] gap-x-m gap-y-xs" data-type="body-s">
        {rows.map(([term, said]) => (
          <div key={term} className="contents">
            <dt className="text-on-surface-low">{term}</dt>
            <dd className="min-w-0 break-words text-on-surface-var">{said}</dd>
          </div>
        ))}
      </dl>
      {/* The composer's control is where these change; this opens it. */}
      <Button variant="secondary" size="sm" onClick={openTheBoundary} className="self-start">Change in the composer</Button>
    </div>
  )
}

// ── Context ──

export function useContextSummary(): string {
  const reading = useContextReading()
  return reading ? contextLineSaid(reading).line : 'Not measured yet'
}

/** The composer's Context view, beside the thread. `compact` is the chat's `/compact`, absent while
 *  a turn runs. */
export function ContextBody({ compact, navigate }: { compact?: () => void; navigate: (p: string) => void }) {
  const reading = useContextReading()
  if (!reading) return <p data-type="body-s" className="text-on-surface-low">This chat's context has not been measured yet.</p>
  const actions: Record<string, (() => void) | undefined> = {
    tools: () => navigate('tools'), memory: () => navigate('memory?tab=recall'), compact,
  }
  return (
    <div className="flex flex-col gap-s">
      <ContextView pct={reading.pct} window={reading.window} parts={reading.parts} cost={reading.cost} actionFor={(a) => actions[a]} />
    </div>
  )
}

// ── Connectors ──

/** How many connectors show before Show more. */
const CONNECTORS_SHOWN = 4

const healthy = (server: McpServer) => serverHealth(server).state === 'ready'
const off = (server: McpServer) => serverHealth(server).state === 'disabled'
/** Where a server sorts: the ones that need her, then the working ones, then the ones off. */
const rankOf = (server: McpServer) => (off(server) ? 'off' : healthy(server) ? 'ready' : 'needs')
const RANK = { needs: 0, ready: 1, off: 2 } as const

/** The tool servers, the ones that need something first, then the working ones, then the ones
 *  switched off: each kept in the order the gateway lists it. */
export function connectorsInOrder(servers: McpServer[]): McpServer[] {
  return servers.map((server, i) => ({ server, i }))
    .sort((a, b) => RANK[rankOf(a.server)] - RANK[rankOf(b.server)] || a.i - b.i).map(({ server }) => server)
}

export function useConnectors() {
  const [servers, setServers] = useState<McpServer[] | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const read = useCallback(() => {
    api.mcpServers().then((s) => { setServers(s); setLoadError(null) }).catch((e: unknown) => setLoadError(e))
  }, [])
  useEffect(read, [read])
  return { servers, loadError, read }
}

export function connectorsSummary(servers: McpServer[] | null, loadError: unknown): string {
  if (loadError) return 'Not read'
  if (!servers) return 'Reading…'
  if (!servers.length) return 'None'
  const needs = servers.filter((s) => !healthy(s) && !off(s)).length
  const ready = servers.filter(healthy).length
  return [needs ? `${needs} need${needs === 1 ? 's' : ''} you` : '', `${ready} ready`].filter(Boolean).join(' · ')
}

export function ConnectorsBody({ servers, loadError, read }: ReturnType<typeof useConnectors>) {
  const [all, setAll] = useState(false)
  const [reconnecting, setReconnecting] = useState('')
  if (loadError) return <InlineError onRetry={read}>{`The tool servers were not read: ${loadError instanceof Error ? loadError.message : String(loadError)}`}</InlineError>
  if (!servers) return <p data-type="body-s" className="text-on-surface-low">Reading the tool servers…</p>
  if (!servers.length) return <p data-type="body-s" className="text-on-surface-low">No tool servers are connected. <TextLink href="#/tools">Add one on the Tools page</TextLink>.</p>
  const ordered = connectorsInOrder(servers)
  const shown = all ? ordered : ordered.slice(0, CONNECTORS_SHOWN)
  const reconnect = (name: string) => {
    setReconnecting(name)
    api.reconnectMcp(name).then(read, reportActionFailure(`reconnect ${name}`)).finally(() => setReconnecting(''))
  }
  return (
    <div className="flex flex-col gap-xs">
      <ul className="flex flex-col gap-xs">
        {shown.map((s) => {
          const h = serverHealth(s)
          const manage = `#/tools?open=${encodeURIComponent(s.name)}`
          return (
            <li key={s.name} className="flex min-w-0 flex-col gap-xs rounded-md px-s py-xs">
              <div className="flex min-w-0 items-center gap-s">
                <span aria-hidden className="size-2 shrink-0 rounded-full" style={{ background: h.tone }} />
                <span data-type="label-s" className="min-w-0 flex-1 truncate text-on-surface">{s.name}</span>
                <span data-type="caption" className="shrink-0 text-on-surface-low">{h.state}</span>
              </div>
              {h.detail && h.state !== 'ready' && <p data-type="caption" className="text-on-surface-low">{h.detail}</p>}
              {/* The actions the Tools page offers, by the same calls: a sign-in and an Allow are
                  given there, where the server is described. */}
              <div className="flex flex-wrap items-center gap-s">
                {(h.state === 'error' || h.state.startsWith('failed')) && (
                  <Button variant="secondary" size="xs" loading={reconnecting === s.name} onClick={() => reconnect(s.name)}
                    ariaLabel={`Reconnect ${s.name}`}>Reconnect</Button>
                )}
                {h.state === 'sign-in needed' && <TextLink href={manage} size="sm">Sign in on the Tools page</TextLink>}
                {h.state === 'waiting for your Allow' && <TextLink href={manage} size="sm">Review on the Tools page</TextLink>}
                <TextLink href={manage} size="sm" aria-label={`Manage ${s.name}`}>Manage</TextLink>
              </div>
            </li>
          )
        })}
      </ul>
      {ordered.length > CONNECTORS_SHOWN && (
        // One control that keeps its place and its focus: only its words change.
        <Button variant="ghost" size="xs" className="self-start" onClick={() => setAll((v) => !v)} ariaExpanded={all}>
          {all ? 'Show less' : `Show ${ordered.length - CONNECTORS_SHOWN} more`}
        </Button>
      )}
    </div>
  )
}

// ── Outputs ──

export function outputsSummary(outputs: ChatOutput[]): string {
  if (!outputs.length) return 'Nothing made yet'
  const artifacts = outputs.filter((o) => o.kind === 'artifact').length
  const files = outputs.length - artifacts
  return [artifacts ? `${artifacts} artifact${artifacts === 1 ? '' : 's'}` : '', files ? `${files} file${files === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ')
}

/** What the chat made, the latest first, each opening in the detail tile beside the thread. */
export function OutputsBody({ outputs, open, current }: { outputs: ChatOutput[]; open: (address: string) => void; current: string | null }) {
  if (!outputs.length) return <p data-type="body-s" className="text-on-surface-low">An artifact this chat saves, or a file it writes, shows here.</p>
  return (
    <ul className="flex flex-col gap-px">
      {outputs.map((o) => {
        const Icon = o.kind === 'artifact' ? Box : FilePen
        return (
          <li key={o.address} tabIndex={-1} onClick={() => open(o.address)} className={ROW}>
            <RowHitTarget label={`Open ${o.name}`} selected={current === o.address} />
            <Icon size={14} aria-hidden className="shrink-0 text-on-surface-low" />
            <span className="min-w-0 flex-1">
              <span data-type="body-s" className="block truncate text-on-surface">{o.name}</span>
              <span data-type="caption" className="block truncate text-on-surface-low">{o.kind === 'artifact' ? 'Artifact' : o.path}</span>
            </span>
          </li>
        )
      })}
    </ul>
  )
}

// ── Background tasks ──

/** The kinds a chat's own background work comes in, beside its helpers (which the chat's own
 *  frames keep). */
const BACKGROUND_KINDS = new Set(['workflow', 'loop', 'trigger', 'webhook'])

/** The chat's work in `GET /api/running`, read on mount and again when the list moves or the link
 *  comes back (the frame is a signal to read, never a payload). */
export function useChatBackground(session: string | undefined) {
  const [list, setList] = useState<RunningList | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const read = useCallback(() => {
    if (!session) return
    api.running().then((l) => { setList(l); setLoadError(null) }).catch((e: unknown) => setLoadError(e))
  }, [session])
  useEffect(read, [read])
  // The list moves up to every 150 ms while work runs; one read a second keeps up with it.
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (pending.current) clearTimeout(pending.current) }, [])
  useChatSocket((m: WsMessage) => {
    if (m.type !== 'running_changed' || pending.current) return
    pending.current = setTimeout(() => { pending.current = null; read() }, 1000)
  }, read)
  const mine = (rows: RunningRow[] | undefined) => (rows ?? []).filter((r) => r.session === session && BACKGROUND_KINDS.has(r.kind))
  // When each of the chat's helpers started, which its own frames do not say.
  const helperStarts = new Map((list?.running ?? []).filter((r) => r.kind === 'subagent').map((r) => [idOf(r.work), r.started_at]))
  return { running: mine(list?.running), finished: mine(list?.finished), helperStarts, loadError, read }
}

/** "for 1m 19s", from when it started, kept current while anything runs. */
export function useNow(live: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [live])
  return now
}
export const since = (at: string | null | undefined, now: number) => {
  const t = at ? Date.parse(at) : NaN
  return Number.isNaN(t) ? '' : `for ${fmtElapsed(Math.max(0, Math.round((now - t) / 1000)))}`
}

/** A row's own id within its kind (`workflow:abc` is `abc`). */
const idOf = (work: string) => work.slice(work.indexOf(':') + 1)

export function backgroundSummary(helpersRunning: number, running: RunningRow[], finished: RunningRow[], helpersDone: number): string {
  const live = helpersRunning + running.length
  const ended = helpersDone + finished.length
  if (!live && !ended) return 'Nothing in the background'
  return [live ? `${live} running` : '', ended ? `${ended} finished` : ''].filter(Boolean).join(' · ')
}

/** The detail tile a row opens in: a run, and a loop backed by one, as the run (`run:<id>`); a loop
 *  as the loop (`loop:<id>`); null for work the tile shows nothing of, which opens its own page. */
export function besideOf(row: Pick<RunningRow, 'kind' | 'work'>): string | null {
  if (row.work.startsWith('workflow:')) return `run:${idOf(row.work)}`
  if (row.kind === 'loop' && row.work.startsWith('loop:')) return `loop:${idOf(row.work)}`
  return null
}

/** The chat's runs and loops: each running one with how long it has run and its Stop (a run's, the
 *  call its own page makes) or its page, and the count and the last few of the ones that ended. A
 *  run or a loop opens in the detail tile (`besideOf`); anything else opens its own page. */
export function BackgroundRuns({ running, finished, loadError, read, open, current, navigate }: Omit<ReturnType<typeof useChatBackground>, 'helperStarts'> & {
  open: (address: string) => void; current: string | null; navigate: (p: string) => void
}) {
  const now = useNow(running.length > 0)
  const [stopping, setStopping] = useState('')
  const stop = (row: RunningRow) => {
    setStopping(row.work)
    api.cancelWorkflowRun(idOf(row.work)).then(read, reportActionFailure(`stop ${row.name || 'the run'}`)).finally(() => setStopping(''))
  }
  const tail = useMemo(() => finished.slice(0, 3), [finished])
  const go = (row: RunningRow) => {
    const beside = besideOf(row)
    if (beside) open(beside)
    else if (row.route) navigate(row.route.replace(/^#\/?/, ''))
  }
  if (loadError && !running.length) return <InlineError onRetry={read}>{`The chat's runs were not read: ${loadError instanceof Error ? loadError.message : String(loadError)}`}</InlineError>
  if (!running.length && !finished.length) return null
  return (
    <div className="flex flex-col gap-xs">
      {running.length > 0 && (
        <ul aria-label="Running" className="flex flex-col gap-px">
          {running.map((row) => (
            <li key={row.work} tabIndex={-1} onClick={() => go(row)} className={ROW}>
              <RowHitTarget label={`Open ${row.name || row.kind}`} selected={!!current && current === besideOf(row)} />
              <span className="min-w-0 flex-1">
                <span data-type="body-s" className="block truncate text-on-surface">{row.name || row.kind}</span>
                <span data-type="caption" className="block truncate text-on-surface-low">
                  {[row.line, since(row.started_at, now)].filter(Boolean).join(' · ')}
                </span>
              </span>
              {/* Its own control: the row's open does not run with it. */}
              {row.kind === 'workflow' && (
                <Button variant="ghost" size="xs" loading={stopping === row.work} onClick={(e) => { e.stopPropagation(); stop(row) }}
                  ariaLabel={`Stop ${row.name || 'the run'}`}>Stop</Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {finished.length > 0 && (
        <div className="flex flex-col gap-px">
          <p data-type="caption" className="px-s text-on-surface-low">{`${finished.length} finished`}</p>
          <ul aria-label="Just finished" className="flex flex-col gap-px">
            {tail.map((row) => (
              <li key={row.work} data-type="caption" className="flex min-w-0 gap-s px-s text-on-surface-var">
                <span className="min-w-0 flex-1 truncate">{row.name || row.kind}</span>
                <span className="shrink-0 text-on-surface-low">{row.phase}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

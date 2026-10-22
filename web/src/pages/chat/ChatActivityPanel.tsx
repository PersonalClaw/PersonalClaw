import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { unavailableWhen } from '../../ui/unavailable'
import { fvs } from '../../design/fontWeight'
import { motion } from 'framer-motion'
import { FileText, Link2, ExternalLink, MessagesSquare, ArrowUp, Loader2, Bot, Check, AlertTriangle, OctagonX, Ban, CircleStop } from 'lucide-react'
import { api } from '../../lib/api'
import { reportActionFailure } from '../../app/reportingWrite'
import { Markdown } from '../../ui/Markdown'
import { Button } from '../../ui/Button'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { WebLink } from '../../ui/WebLink'
import { fadeEnter } from '../../design/motion'
import { SlotEmptyState } from '../dashboard/widgets/kit'
import type { ChatActivity, SubagentCard } from './chatTypes'
import { SECTION_TITLES, useInspectorLayout, type SectionId } from './inspector/inspectorLayout'
import { InspectorSection } from './inspector/InspectorSection'
import type { ChatOutput } from './inspector/chatOutputs'
import {
  BackgroundRuns, ConnectorsBody, ContextBody, EnvironmentBody, OutputsBody, backgroundSummary, connectorsSummary,
  outputsSummary, since, useChatBackground, useConnectors, useContextSummary, useEnvironmentSummary, useNow,
} from './inspector/sections'

export interface SidePanelData {
  msgs: { q: string; a: string; runId: string; done: boolean }[]
  busy: boolean
  onAsk: (q: string) => void
  onOpen: () => void
}

/** What the inspector's sections that reach past the transcript need from the chat page. */
export interface InspectorData {
  /** The chat's key, whose background work the inspector lists. */
  session?: string
  /** What the chat made (`inspector/chatOutputs`). */
  outputs: ChatOutput[]
  /** Open a detail beside the thread, in the chat's pane host (`beside/detailBeside`). */
  openDetail: (address: string) => void
  /** The address of the detail open now, whose row says so. */
  detail: string | null
  /** The chat's `/compact`, absent while a turn runs. */
  compact?: () => void
  navigate: (p: string) => void
  /** Whether the column can be reordered here (not in the phone's drawer). */
  canMove: boolean
}

/** The chat's session inspector (#3820's Oct 18 amendment) — the CONTENT of the chat's docked
 *  Activity rail (the outer `SidePanel` primitive owns the frame: title bar, close, resize,
 *  expand-to-full). A slim column of sections, each a disclosure with a one-line summary while it
 *  is folded, which the owner opens, folds and moves up and down; the column keeps that for this
 *  browser (`inspector/inspectorLayout`):
 *   • Environment — the project, the working folder, the sandbox, the reach, the permission and the
 *     memory mode, as the composer's Execution Boundary shows them; its Change opens that control.
 *   • Context — the composer's Context view: used and total, free, when Compact is offered, and what
 *     fills it part by part, with each part's action.
 *   • Connectors — the tool servers, the ones that need something first; Reconnect, and the Tools
 *     page for a sign-in, an Allow and everything else.
 *   • Outputs — the artifacts the chat saved and the files it wrote, each opening in the detail
 *     tile beside the thread.
 *   • Files — files touched this session; click → open in the file side panel.
 *   • Sources — the links the chat's answers cite that its own turns retrieved (a web search's
 *     result, a page a fetch read; `turnSources`), by the titles they came back with. A link the
 *     model wrote from memory is no source and is not listed.
 *   • Background tasks — the helpers the chat started and its runs and loops, each running one with
 *     how long it has run and its Stop, and the ones that just ended; a helper or a run opens in the
 *     detail tile.
 *   • Side question — an isolated throwaway Q&A against the frozen session context.
 *  Opening a section reads what it shows; none signs in, reconnects or stops anything by being
 *  opened, and closing the rail or the tile stops nothing.
 *
 *  This panel owns NO navigation. It used to open on an "Index" tab — a user-message
 *  outline whose rows jumped to a turn — and the Session Map superseded it: the
 *  map is the in-session index, always-available rather than behind a panel, and it
 *  marks tool calls / approvals / errors / subagents as well as user turns. The tab
 *  was deleted rather than kept beside it, so there is exactly one jump surface.
 *  `indexTabRetired.test.tsx` is the rail that keeps it that way. */
export function ChatActivityPanel({ activity, onOpenFile, subagents = [], onKillFanout, side, inspector }: {
  activity: ChatActivity
  onOpenFile: (path: string) => void
  subagents?: SubagentCard[]
  onKillFanout?: () => void  // kill EVERY running child of this chat's fan-out
  side?: SidePanelData
  inspector?: InspectorData
}) {
  const { layout, toggle, move } = useInspectorLayout()
  const sideOpen = layout.open.includes('side')
  // opening the Side section opens the throwaway side buffer (lazy).
  useEffect(() => { if (sideOpen) side?.onOpen() }, [sideOpen]) // eslint-disable-line react-hooks/exhaustive-deps
  const environment = useEnvironmentSummary()
  const context = useContextSummary()
  const connectors = useConnectors()
  const background = useChatBackground(inspector?.session)
  const now = useNow(subagents.some((s) => !s.done))
  const outputs = inspector?.outputs ?? []
  const open = inspector?.openDetail
  const current = inspector?.detail ?? null
  // A batch's tasks stop with their batch's run (its card's Open), not with the chat's fan-out, so
  // the fan-out's Stop counts and stops the chat's own helpers alone.
  const ownRunning = subagents.filter((s) => !s.done && !s.run)
  const helpersRunning = subagents.filter((s) => !s.done).length

  const sections: Record<SectionId, { summary: string; body: ReactNode } | null> = {
    environment: { summary: environment, body: <EnvironmentBody /> },
    context: { summary: context, body: <ContextBody compact={inspector?.compact} navigate={inspector?.navigate ?? (() => {})} /> },
    connectors: { summary: connectorsSummary(connectors.servers, connectors.loadError), body: <ConnectorsBody {...connectors} /> },
    outputs: inspector ? { summary: outputsSummary(outputs), body: <OutputsBody outputs={outputs} open={inspector.openDetail} current={current} /> } : null,
    files: {
      summary: activity.files.length ? `${activity.files.length} file${activity.files.length === 1 ? '' : 's'}` : 'None yet',
      body: activity.files.length === 0
        ? <Empty icon={FileText} text="No files referenced yet." />
        : <div className="flex flex-col gap-px">
            {activity.files.map((f) => (
              <motion.button key={f.path} type="button" onClick={() => onOpenFile(f.path)} title={f.path}
                variants={fadeEnter} initial="initial" animate="animate"
                className="group flex items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-surface-high">
                <FileText size={14} className="shrink-0 text-on-surface-low" />
                <span className="min-w-0 flex-1">
                  <span data-type="body-s" className="block truncate font-mono text-on-surface">{f.name}</span>
                  <span data-type="caption" className="block truncate text-on-surface-low">{f.path}</span>
                </span>
              </motion.button>
            ))}
          </div>,
    },
    sources: {
      summary: activity.sources.length ? `${activity.sources.length} source${activity.sources.length === 1 ? '' : 's'}` : 'None yet',
      body: activity.sources.length === 0
        ? <Empty icon={Link2} text="No sources yet: a link shows here once an answer cites a page its turn searched or read." />
        : <div className="flex flex-col gap-px">
            {/* Each links out the way the answer's own links do (`WebLink`). */}
            {activity.sources.map((l) => (
              <motion.div key={l.url} variants={fadeEnter} initial="initial" animate="animate">
                <WebLink href={l.url} title={l.url}
                  className="group flex items-center gap-2 rounded-md px-2.5 py-2 transition-colors hover:bg-surface-high">
                  <Link2 size={14} className="shrink-0 text-on-surface-low" />
                  <span className="min-w-0 flex-1">
                    <span data-type="body-s" className="block truncate text-on-surface">{l.title}</span>
                    <span data-type="caption" className="block truncate text-on-surface-low">{l.url}</span>
                  </span>
                  <ExternalLink size={12} className="shrink-0 text-on-surface-low opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100" />
                </WebLink>
              </motion.div>
            ))}
          </div>,
    },
    background: {
      summary: backgroundSummary(helpersRunning, background.running, background.finished, subagents.length - helpersRunning),
      body: (
        <div className="flex flex-col gap-s">
          {subagents.length === 0 && !background.running.length && !background.finished.length && !background.loadError
            ? <Empty icon={Bot} text="Nothing in the background: a helper or a run this chat starts shows here." />
            : null}
          {onKillFanout && ownRunning.length > 0 && (
            <Button variant="danger" size="sm" onClick={onKillFanout} className="self-end"
              title="Stop every running subagent this chat started itself">
              <OctagonX size={13} /> Stop fan-out ({ownRunning.length})
            </Button>
          )}
          {subagents.length > 0 && (
            <div className="flex flex-col gap-1.5">
              {subagents.map((s) => (
                <SubagentRow key={s.id} sub={s} now={now} startedAt={background.helperStarts.get(s.id)}
                  onOpen={open ? () => open(`helper:${s.id}`) : undefined} current={current === `helper:${s.id}`} />
              ))}
            </div>
          )}
          <BackgroundRuns running={background.running} finished={background.finished} loadError={background.loadError} read={background.read}
            open={open ?? (() => {})} current={current} navigate={inspector?.navigate ?? (() => {})} />
        </div>
      ),
    },
    side: side ? { summary: side.msgs.length ? `${side.msgs.length} asked` : 'Ask beside the chat', body: <SideChat side={side} /> } : null,
  }
  const shown = layout.order.filter((id) => sections[id])

  return (
    <div data-inspector className="-mx-l -my-l flex h-full flex-col overflow-y-auto">
      {shown.map((id, i) => (
        <InspectorSection key={id} title={SECTION_TITLES[id]} summary={sections[id]!.summary}
          open={layout.open.includes(id)} onToggle={() => toggle(id)}
          onMove={inspector?.canMove === false ? undefined : (by) => move(id, by)} first={i === 0} last={i === shown.length - 1}>
          {sections[id]!.body}
        </InspectorSection>
      ))}
    </div>
  )
}

/** Panel-slot empty — the canonical SlotEmptyState (patterns.md: slot-empty, not
 *  page-empty), centered in the tab panel's space. */
function Empty({ icon: Icon, text }: { icon: typeof FileText; text: string }) {
  return (
    <div className="flex justify-center px-4 py-10">
      <SlotEmptyState icon={Icon}>{text}</SlotEmptyState>
    </div>
  )
}

/** A live subagent card: agent + task, running/done/failed/stopped status, latest tool and how
 *  long it has run while running, and (on done) an expandable result. Driven by subagent_* WS
 *  events; the report of one that ran also starts its turn in the chat. One that never ran (her
 *  Deny of its start, a start nobody allowed in time) is "declined" or "not started", not failed,
 *  and says why; one she stopped is "stopped". A running one has its own Stop (`DELETE
 *  /api/spawn/{id}`, as its row in its turn), and `onOpen` opens it in the detail tile. */
function SubagentRow({ sub, now, startedAt, onOpen, current = false }: {
  sub: SubagentCard; now?: number; startedAt?: string | null; onOpen?: () => void; current?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [stopping, setStopping] = useState(false)
  const unrun = sub.done && !!sub.neverRan
  const stopped = sub.done && !!sub.stopped && !unrun
  const failed = sub.done && !!sub.error && !unrun && !stopped
  const status = unrun ? 'unrun' : stopped ? 'stopped' : failed ? 'failed' : sub.done ? 'done' : 'running'
  const tone = failed ? 'var(--color-danger)' : sub.done && !unrun && !stopped ? 'var(--color-primary)' : 'var(--color-on-surface-low)'
  const ran = status === 'running' && now !== undefined ? since(startedAt, now) : ''
  const stop = (e: MouseEvent) => {
    e.stopPropagation()
    setStopping(true)
    api.cancelSpawnedAgent(sub.id).catch(reportActionFailure('stop this helper')).finally(() => setStopping(false))
  }
  return (
    <motion.div
      variants={fadeEnter} initial="initial" animate="animate" tabIndex={onOpen ? -1 : undefined} onClick={onOpen}
      className={`relative rounded-lg border border-outline-variant/50 bg-surface-high/40 px-2.5 py-2 ${onOpen ? 'has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary' : ''}`}>
      {onOpen && <RowHitTarget label={`Open ${sub.title || sub.task || 'the helper'}`} selected={current} />}
      <div className="flex items-center gap-2">
        <span className="shrink-0" style={{ color: tone }}>
          {status === 'running' ? <Loader2 size={14} className="animate-spin" /> : unrun ? <Ban size={14} /> : stopped ? <CircleStop size={14} /> : failed ? <AlertTriangle size={14} /> : <Check size={14} />}
        </span>
        <span className="min-w-0 flex-1">
          <span data-type="label-s" className="block truncate text-on-surface" style={fvs(500)} title={sub.task}>{sub.title || sub.task || '(task)'}</span>
          <span data-type="caption" className="block truncate text-on-surface-low">
            {sub.agent || 'subagent'}
            {sub.run ? ' · a task of a batch' : ''}
            {unrun ? (sub.declined ? ' · declined' : ' · not started') : ''}
            {stopped ? ' · stopped' : ''}
            {status === 'running' && sub.lastTool ? ` · ${sub.lastTool}` : ''}
            {ran ? ` · ${ran}` : ''}
            {sub.done && sub.elapsed !== undefined ? ` · ${sub.elapsed.toFixed(1)}s` : ''}
            {sub.done && sub.costUsd !== undefined && sub.costUsd > 0 ? ` · $${sub.costUsd < 0.01 ? sub.costUsd.toFixed(4) : sub.costUsd.toFixed(2)}` : ''}
            {sub.done && sub.tokens ? ` · ${sub.tokens.toLocaleString()} tok` : ''}
            {status === 'running' && !sub.lastTool ? ' · working…' : ''}
          </span>
        </span>
        {status === 'running' && (
          <Button variant="ghost" size="xs" loading={stopping} onClick={stop}
            ariaLabel={`Stop helper: ${sub.title || sub.task || sub.id}`}>Stop</Button>
        )}
      </div>
      {(failed || unrun) && <div data-type="caption" className={`mt-1.5 ${failed ? 'text-danger' : 'text-on-surface-low'}`}>{sub.error}</div>}
      {sub.done && !failed && sub.result && (
        <div className="mt-1.5">
          <button type="button" onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }} aria-expanded={open}
            data-type="caption" className="text-on-surface-low hover:text-on-surface-var transition-colors">
            {open ? 'Hide result' : 'Show result'}
          </button>
          {open && (
            <div data-type="caption" className="mt-1 max-h-60 overflow-y-auto rounded-md bg-surface/60 px-2 py-1.5 [&_*:first-child]:mt-0 [&_*:last-child]:mb-0">
              <Markdown>{sub.result}</Markdown>
            </div>
          )}
        </div>
      )}
    </motion.div>
  )
}

/** Side chat — an isolated Q&A against a frozen snapshot of the session (no tools,
 *  nothing persisted to the main transcript). Transcript scrolls; a mini-composer
 *  is pinned at the bottom. Enter sends. */
function SideChat({ side }: { side: SidePanelData }) {
  const [q, setQ] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }) }, [side.msgs])
  const ask = () => { const t = q.trim(); if (!t || side.busy) return; side.onAsk(t); setQ('') }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto p-3">
        {side.msgs.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-10 text-center text-on-surface-low">
            <MessagesSquare size={20} className="opacity-40" />
            <span data-type="body-s">Ask a side question — answered against this conversation's context, without touching the main transcript.</span>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {side.msgs.map((m, i) => (
              <div key={i} className="flex flex-col gap-1.5">
                <div data-type="body-s" className="self-end rounded-xl bg-surface-container px-3 py-1.5 text-on-surface" style={{ maxWidth: '90%' }}>{m.q}</div>
                <div data-type="body-s" className="text-on-surface-var [&_p]:my-1">
                  {m.a ? <Markdown>{m.a}</Markdown> : <Loader2 size={13} className="animate-spin text-on-surface-low" />}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 p-2">
        <div className="flex items-end gap-1.5 rounded-xl bg-surface-container px-2.5 py-1.5 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
          <textarea value={q} onChange={(e) => setQ(e.target.value)} rows={1} placeholder="Ask the side…"
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() } }}
            data-type="body-s" className="max-h-24 min-h-0 flex-1 resize-none bg-transparent text-on-surface outline-none placeholder:text-on-surface-low" />
          <button type="button" onClick={ask} aria-label="Ask side"
            {...unavailableWhen(!q.trim(), 'Type a question first', { busy: side.busy })}
            className="grid size-7 shrink-0 place-items-center rounded-full disabled:opacity-40 aria-disabled:opacity-40 aria-disabled:cursor-not-allowed"
            style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
            {side.busy ? <Loader2 size={13} className="animate-spin" /> : <ArrowUp size={14} />}
          </button>
        </div>
      </div>
    </div>
  )
}

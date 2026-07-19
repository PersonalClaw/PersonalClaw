import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ListTree, FileText, Link2, MessageSquare, ExternalLink, MessagesSquare, ArrowUp, Loader2, Bot, Check, AlertTriangle } from 'lucide-react'
import { Markdown } from '../../ui/Markdown'
import { spring } from '../../design/motion'
import type { ChatActivity, SubagentCard } from './chatTypes'

type Tab = 'index' | 'files' | 'links' | 'subagents' | 'side'

export interface SidePanelData {
  msgs: { q: string; a: string; runId: string; done: boolean }[]
  busy: boolean
  onAsk: (q: string) => void
  onOpen: () => void
}

/** Chat-only activity panel (Stage 5) — the CONTENT of the chat's docked side
 *  panel (the outer `SidePanel` primitive owns the frame: title bar, close, resize,
 *  expand-to-full). Three tabs derived entirely client-side from the conversation:
 *   • Index — user-message outline (markdown); click → scroll to that turn.
 *   • Files — files touched this session; click → open in the file side panel.
 *   • Links — http(s) URLs surfaced in the conversation.
 *  (+ Side — an isolated throwaway Q&A against the frozen session context.) */
export function ChatActivityPanel({ activity, onJumpTo, onOpenFile, subagents = [], side }: {
  activity: ChatActivity
  onJumpTo: (turnIndex: number) => void
  onOpenFile: (path: string) => void
  subagents?: SubagentCard[]
  side?: SidePanelData
}) {
  const [tab, setTab] = useState<Tab>('index')
  // opening the Side tab opens the throwaway side buffer (lazy).
  useEffect(() => { if (tab === 'side') side?.onOpen() }, [tab]) // eslint-disable-line react-hooks/exhaustive-deps
  const counts = { index: activity.index.length, files: activity.files.length, links: activity.links.length }

  // Ordered tab descriptors — drive both the tablist render and arrow-key nav.
  // Subagents tab appears only once at least one has been spawned this session.
  const TABS: { key: Tab; label: string; icon: typeof ListTree; count: number }[] = [
    { key: 'index', label: 'Index', icon: ListTree, count: counts.index },
    { key: 'files', label: 'Files', icon: FileText, count: counts.files },
    { key: 'links', label: 'Links', icon: Link2, count: counts.links },
    ...(subagents.length ? [{ key: 'subagents' as Tab, label: 'Subagents', icon: Bot, count: subagents.length }] : []),
    ...(side ? [{ key: 'side' as Tab, label: 'Side', icon: MessagesSquare, count: side.msgs.length }] : []),
  ]
  // Roving arrow-key nav across the tablist (←/→/Home/End), per WAI-ARIA.
  const onTabKey = (e: React.KeyboardEvent) => {
    const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End']
    if (!keys.includes(e.key)) return
    e.preventDefault()
    const cur = TABS.findIndex((t) => t.key === tab)
    const last = TABS.length - 1
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? last
      : e.key === 'ArrowLeft' ? (cur <= 0 ? last : cur - 1)
      : (cur >= last ? 0 : cur + 1)
    setTab(TABS[next].key)
    document.getElementById(`act-tab-${TABS[next].key}`)?.focus()
  }

  // Docked content: full-height flex column that fills the SidePanel body. The tab
  // strip is sticky at the top; the active tab-panel scrolls below it. No own
  // frame/close/resize — the SidePanel primitive provides all of that.
  return (
    <div className="-mx-l -my-l flex h-full flex-col">
      {/* Segmented tab strip — pill track, raised active tab, centered. */}
      <div role="tablist" aria-label="Activity" onKeyDown={onTabKey}
        className="sticky top-0 z-10 flex items-center justify-center border-b border-outline-variant/40 bg-surface/95 px-2 py-1.5">
        <div className="flex w-full items-center gap-0.5 rounded-pill bg-surface-low p-[3px]">
          {TABS.map((t) => (
            <button key={t.key} type="button" role="tab" id={`act-tab-${t.key}`} aria-controls={`act-panel-${t.key}`}
              aria-selected={tab === t.key} tabIndex={tab === t.key ? 0 : -1} title={t.label} aria-label={t.label}
              onClick={() => setTab(t.key)}
              className={`flex-1 inline-flex items-center justify-center gap-[5px] h-7 rounded-pill border-none text-[0.75rem] cursor-pointer transition-colors duration-150 ${
                tab === t.key
                  ? 'bg-surface text-on-surface shadow-[0_1px_3px_rgb(0_0_0/0.08)]'
                  : 'bg-transparent text-on-surface-low'
              }`}
              style={{ fontVariationSettings: '"wght" 470' }}>
              <t.icon size={13} /> {t.label}
              {t.count > 0 && (
                <span className={`rounded-pill px-[5px] text-[0.65rem] tabular-nums ${
                  tab === t.key ? 'bg-primary/14 text-primary' : 'bg-surface-highest'
                }`}>{t.count}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {tab === 'side' && side ? (
        <div role="tabpanel" id="act-panel-side" aria-labelledby="act-tab-side" className="flex min-h-0 flex-1 flex-col">
          <SideChat side={side} />
        </div>
      ) : (
      <div role="tabpanel" id={`act-panel-${tab}`} aria-labelledby={`act-tab-${tab}`} className="min-h-0 flex-1 overflow-y-auto p-2">
        {tab === 'index' && (
          activity.index.length === 0
            ? <Empty icon={MessageSquare} text="No messages yet." />
            : <div className="flex flex-col gap-px">
                {activity.index.map((e, i) => (
                  <motion.button key={e.turnIndex} type="button" onClick={() => onJumpTo(e.turnIndex)} title={e.label}
                    initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: Math.min(i * 0.03, 0.3) }}
                    className="block w-full truncate rounded-md px-2.5 py-2 text-left text-on-surface-var text-[0.8125rem] leading-snug transition-colors hover:bg-surface-high hover:text-on-surface [&_*]:!my-0 [&_*]:!inline [&_p]:truncate">
                    <Markdown className="truncate">{e.label}</Markdown>
                  </motion.button>
                ))}
              </div>
        )}
        {tab === 'files' && (
          activity.files.length === 0
            ? <Empty icon={FileText} text="No files referenced yet." />
            : <div className="flex flex-col gap-px">
                {activity.files.map((f, i) => (
                  <motion.button key={f.path} type="button" onClick={() => onOpenFile(f.path)} title={f.path}
                    initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: Math.min(i * 0.03, 0.3) }}
                    className="group flex items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-surface-high">
                    <FileText size={14} className="shrink-0 text-on-surface-low" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-on-surface text-[0.8125rem]">{f.name}</span>
                      <span className="block truncate text-on-surface-low text-[0.7rem]">{f.path}</span>
                    </span>
                  </motion.button>
                ))}
              </div>
        )}
        {tab === 'links' && (
          activity.links.length === 0
            ? <Empty icon={Link2} text="No links surfaced yet." />
            : <div className="flex flex-col gap-px">
                {activity.links.map((l, i) => (
                  <motion.a key={l.url} href={l.url} target="_blank" rel="noreferrer" title={l.url}
                    initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: Math.min(i * 0.03, 0.3) }}
                    className="group flex items-center gap-2 rounded-md px-2.5 py-2 transition-colors hover:bg-surface-high">
                    <Link2 size={14} className="shrink-0 text-on-surface-low" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-on-surface text-[0.8125rem]">{l.label}</span>
                      <span className="block truncate text-on-surface-low text-[0.7rem]">{l.url}</span>
                    </span>
                    <ExternalLink size={12} className="shrink-0 text-on-surface-low opacity-0 transition-opacity group-hover:opacity-100" />
                  </motion.a>
                ))}
              </div>
        )}
        {tab === 'subagents' && (
          subagents.length === 0
            ? <Empty icon={Bot} text="No subagents spawned yet." />
            : <div className="flex flex-col gap-1.5">
                {subagents.map((s, i) => <SubagentRow key={s.id} sub={s} index={i} />)}
              </div>
        )}
      </div>
      )}
    </div>
  )
}

// active tab shows its label; inactive tabs are icon-only (with a tooltip) so all
// four + the close button fit comfortably in the ~300px header. A real role="tab"

function Empty({ icon: Icon, text }: { icon: typeof ListTree; text: string }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center text-on-surface-low">
      <Icon size={20} className="opacity-40" />
      <span className="text-[0.8125rem]">{text}</span>
    </div>
  )
}

/** A live subagent card: agent + task, running/done/failed status, latest tool
 *  while running, and (on done) an expandable result. Driven by subagent_* WS
 *  events; the final output also posts to the transcript as a completion event. */
function SubagentRow({ sub, index = 0 }: { sub: SubagentCard; index?: number }) {
  const [open, setOpen] = useState(false)
  const failed = sub.done && !!sub.error
  const status = failed ? 'failed' : sub.done ? 'done' : 'running'
  const tone = failed ? 'var(--color-danger)' : sub.done ? 'var(--color-primary)' : 'var(--color-on-surface-low)'
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}
      className="rounded-lg border border-outline-variant/50 bg-surface-high/40 px-2.5 py-2">
      <div className="flex items-center gap-2">
        <span className="shrink-0" style={{ color: tone }}>
          {status === 'running' ? <Loader2 size={14} className="animate-spin" /> : failed ? <AlertTriangle size={14} /> : <Check size={14} />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 500' }} title={sub.task}>{sub.task || '(task)'}</span>
          <span className="block truncate text-on-surface-low text-[0.7rem]">
            {sub.agent || 'subagent'}
            {status === 'running' && sub.lastTool ? ` · ${sub.lastTool}` : ''}
            {sub.done && sub.elapsed !== undefined ? ` · ${sub.elapsed.toFixed(1)}s` : ''}
            {status === 'running' && !sub.lastTool ? ' · working…' : ''}
          </span>
        </span>
      </div>
      {failed && <div className="mt-1.5 text-[0.7rem] text-danger">{sub.error}</div>}
      {sub.done && !failed && sub.result && (
        <div className="mt-1.5">
          <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
            className="text-[0.7rem] text-on-surface-low hover:text-on-surface-var transition-colors">
            {open ? 'Hide result' : 'Show result'}
          </button>
          {open && (
            <div className="mt-1 max-h-60 overflow-y-auto rounded-md bg-surface/60 px-2 py-1.5 text-[0.75rem] [&_*:first-child]:mt-0 [&_*:last-child]:mb-0">
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
            <span className="text-[0.8125rem]">Ask a side question — answered against this conversation's context, without touching the main transcript.</span>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {side.msgs.map((m, i) => (
              <div key={i} className="flex flex-col gap-1.5">
                <div className="self-end rounded-xl bg-surface-container px-3 py-1.5 text-on-surface text-[0.8125rem]" style={{ maxWidth: '90%' }}>{m.q}</div>
                <div className="text-on-surface-var text-[0.8125rem] [&_p]:my-1">
                  {m.a ? <Markdown>{m.a}</Markdown> : <Loader2 size={13} className="animate-spin text-on-surface-low" />}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 p-2">
        <div className="flex items-end gap-1.5 rounded-xl bg-surface-container px-2.5 py-1.5">
          <textarea value={q} onChange={(e) => setQ(e.target.value)} rows={1} placeholder="Ask the side…"
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() } }}
            className="max-h-24 min-h-0 flex-1 resize-none bg-transparent text-on-surface text-[0.8125rem] outline-none placeholder:text-on-surface-low" />
          <button type="button" onClick={ask} disabled={!q.trim() || side.busy} aria-label="Ask side"
            className="grid size-7 shrink-0 place-items-center rounded-full disabled:opacity-40"
            style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
            {side.busy ? <Loader2 size={13} className="animate-spin" /> : <ArrowUp size={14} />}
          </button>
        </div>
      </div>
    </div>
  )
}

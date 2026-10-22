import { lazy, Suspense, type ReactNode } from 'react'
import { ArrowLeft, Bot, Box, FilePen, SearchX, Workflow, type LucideIcon } from 'lucide-react'
import type { PaneContent } from '../../../ui/PaneHost'
import { Button } from '../../../ui/Button'
import { Markdown } from '../../../ui/Markdown'
import { ChatFileViewer } from '../ChatFilePanel'
import { WorkflowProgressCard } from '../WorkflowProgressCard'
import { useHelper, type HelperRef } from '../transcript/TurnHelpers'
import type { ChatOutput } from '../inspector/chatOutputs'
import type { SubagentCard } from '../chatTypes'

// ── A detail beside the reading ────────────────────────────────────────────────────────────────
//
// The detail tile (#3820's Oct 18 amendment): what the session inspector and the thread's own cards
// open beside the turns, in the same pane host a reply's block opens in (`blockBeside`). Its
// address keeps it, a kind and an id, so a reload, Back and a copied link open the same one:
//   `artifact:<slug>` an artifact the chat saved, in its viewer
//   `file:<path>`     a file the chat wrote, in the chat's file viewer
//   `run:<id>`        a workflow run, as its live card
//   `helper:<id>`     a helper the chat started: its task, its state and its report
// The pane host gives focus back to the row it was opened from when it closes, and closing it
// stops nothing: the work beside it and the work it shows go on.

const ArtifactViewer = lazy(() => import('../../artifacts/ArtifactViewer').then((m) => ({ default: m.ArtifactViewer })))

const KINDS = ['artifact', 'file', 'run', 'helper'] as const
type DetailKind = typeof KINDS[number]

/** The detail `address` names, or null for any other address (a reply's block). */
export function detailOf(address: string | null): { kind: DetailKind; id: string } | null {
  const at = address?.indexOf(':') ?? -1
  if (!address || at <= 0) return null
  const kind = address.slice(0, at) as DetailKind
  const id = address.slice(at + 1)
  return KINDS.includes(kind) && id ? { kind, id } : null
}

const ICONS: Record<DetailKind, LucideIcon> = { artifact: Box, file: FilePen, run: Workflow, helper: Bot }
const LABELS: Record<DetailKind, string> = { artifact: 'artifact', file: 'file', run: 'run', helper: 'helper' }

/** The pane for a detail address, or null for an address that names none (a block's, or nothing
 *  open). `back`, on a phone, returns to the inspector the detail replaced. */
export function detailPane({ address, outputs, subagents, helpers = [], session, ready, open, onClose, back }: {
  address: string | null
  outputs: ChatOutput[]
  subagents: SubagentCard[]
  /** The helpers the chat's calls started, as their results named them: a helper's name before a
   *  live frame or a read of it says more (after a reload). */
  helpers?: HelperRef[]
  session?: string
  /** Whether the chat's turns are on screen. */
  ready: boolean
  /** Open another detail in the tile (an artifact's source file). */
  open: (address: string) => void
  onClose: () => void
  back?: () => void
}): PaneContent | null {
  const detail = detailOf(address)
  if (!detail || !ready) return null
  const { kind, id } = detail
  const made = outputs.find((o) => o.address === address)
  const helper = subagents.find((s) => s.id === id)
  const named = helpers.find((r) => r.id === id)
  const title = kind === 'artifact' ? (made?.name || id)
    : kind === 'file' ? (made?.name || id.split('/').pop() || id)
      : kind === 'run' ? 'Workflow run'
        : (helper?.title || helper?.task || named?.task || 'Helper')
  const frame = (body: ReactNode) => (
    <div className="flex h-full min-h-0 flex-col">
      {back && (
        <div className="shrink-0 border-b border-outline-variant/40 px-m py-xs">
          <Button variant="ghost" size="sm" onClick={back}><ArrowLeft size={14} /> Back to Activity</Button>
        </div>
      )}
      {body}
    </div>
  )
  const body = kind === 'artifact'
    ? (
      <div className="min-h-0 flex-1 overflow-auto">
        <Suspense fallback={<p data-type="body-s" className="px-l py-m text-on-surface-low">Opening the artifact…</p>}>
          {/* Deleted from here, it is gone from the tile too; its source file opens in its place. */}
          <ArtifactViewer slug={id} onChanged={() => {}} onDeleted={onClose} onOpenSourceFile={(path) => open(`file:${path}`)} />
        </Suspense>
      </div>
    )
    : kind === 'file'
      ? <div className="flex min-h-0 flex-1 flex-col"><ChatFileViewer path={id} /></div>
      : kind === 'run'
        ? (
          <div tabIndex={0} role="group" aria-label="The run, beside the conversation" className="min-h-0 flex-1 overflow-auto px-m focus-visible:-outline-offset-2">
            <WorkflowProgressCard refObj={{ runId: id, created: false }} inPane />
          </div>
        )
        : <HelperDetail id={id} live={helper} named={named} session={session} />
  return { id: address!, title, label: LABELS[kind], icon: ICONS[kind] ?? SearchX, onClose, body: frame(body) }
}

/** A helper, beside the thread: what it was asked, by which agent, how it stands, and its report. */
function HelperDetail({ id, live, named, session }: { id: string; live?: SubagentCard; named?: HelperRef; session?: string }) {
  const { sub, gone, readError } = useHelper({ id, agent: live?.agent ?? named?.agent ?? '', task: live?.task ?? named?.task ?? '' }, session, live)
  const unrun = sub.done && !!sub.neverRan
  const stopped = sub.done && !!sub.stopped && !unrun
  const failed = sub.done && !!sub.error && !unrun && !stopped
  const state = gone && !sub.done ? 'No longer tracked' : readError && !sub.done ? `State not read: ${readError}`
    : !sub.done ? `Running${sub.lastTool ? ` · ${sub.lastTool}` : ''}`
      : unrun ? (sub.declined ? 'Not started: you declined' : 'Not started') : stopped ? 'Stopped' : failed ? `Failed: ${sub.error}` : 'Done'
  return (
    <div tabIndex={0} role="group" aria-label="The helper, beside the conversation" className="flex min-h-0 flex-1 flex-col gap-s overflow-auto px-l py-m focus-visible:-outline-offset-2">
      <dl className="grid grid-cols-[auto_1fr] gap-x-m gap-y-xs" data-type="body-s">
        <dt className="text-on-surface-low">Task</dt><dd className="min-w-0 break-words text-on-surface">{sub.task || '(not said)'}</dd>
        <dt className="text-on-surface-low">Agent</dt><dd className="text-on-surface-var">{sub.agent || 'helper'}</dd>
        <dt className="text-on-surface-low">State</dt><dd className={failed ? 'text-danger' : 'text-on-surface-var'}>{state}</dd>
      </dl>
      {sub.result
        ? <div data-type="body-s" className="border-t border-outline-variant/30 pt-s"><Markdown>{sub.result}</Markdown></div>
        : sub.done ? null : <p data-type="caption" className="text-on-surface-low">Its report shows here when it finishes, and starts its own turn in the chat.</p>}
    </div>
  )
}

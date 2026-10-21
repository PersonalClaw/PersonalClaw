import { useEffect, useId, useRef, useState } from 'react'
import { Bot, ClipboardList, Eye, FolderCog, Hammer, MessageSquare, Shield, ShieldCheck, Zap, type LucideIcon } from 'lucide-react'
import { Eyebrow } from '../../../ui/Eyebrow'
import { Popover, MenuRow } from '../../../ui/Popover'
import { PillButton } from '../../../ui/composer/controls'
import { menuCursorKeydown, useMenuCursor } from '../../../lib/useMenuCursor'
import { api, type ApprovalMode, type ChatBoundary, type MemoryMode, type TaskMode } from '../../../lib/api'
import { MEMORY_MODES } from '../memoryModeCopy'

// ── Where the next turn may act, beside the composer ──────────────────────────────────────────────
//
// Two controls in the composer's footer decide what the next turn may do, before it runs:
//
// - The task mode (Agent, Ask, Plan, Build): what the agent may do in this chat, beside the agent-
//   and-model pill. It is the "autonomy" the redesign names.
// - The Execution Boundary control: where the turn may act. It shows the permission mode, the
//   working folder, the sandbox its commands run in, the network its work reaches and the memory
//   mode, and changes the permission mode, the folder (an open chat) and the memory mode (a new
//   chat, the one place it is chosen).
//
// Both change what they change through the calls the chat header's pills made (`POST
// /api/chat/mode`, `POST /api/chat/task-mode`), and both show only what the gateway took: a pick is
// shown once the gateway answers it, and one it refuses leaves the control as it was, with the
// gateway's reason beside it. The sandbox and the network are the gateway's words
// (`GET /api/chat/boundary`), shown as they come.
//
// Each menu is a `role="menu"` of radio items with the shared cursor (`lib/useMenuCursor`): it
// opens on the checked item, the arrows move without choosing anything, Enter or Space chooses, and
// Escape or Tab closes it with focus back on its pill. Moving never chooses, because choosing Trust
// or YOLO answers the approvals the chat is waiting on.

/** One choice of a mode menu: its words and its hint, which the pill's tooltip says whole. */
interface Mode<T extends string> { id: T; label: string; icon: LucideIcon; hint: string }

/** The permission modes, with the hints the header's pill gave them, word for word: security copy
 *  is not reworded in a move. */
export const PERMISSION_MODES: Mode<ApprovalMode>[] = [
  { id: 'normal', label: 'Normal', icon: Shield, hint: 'a tool that only reads runs; the rest ask when they need approval' },
  { id: 'trust_reads', label: 'Trust reads', icon: Eye, hint: 'read-only shell commands run without asking too' },
  { id: 'trust', label: 'Trust', icon: ShieldCheck, hint: 'auto-approve every tool in this chat' },
  { id: 'yolo', label: 'YOLO', icon: Zap, hint: 'auto-approve everywhere; auto-expires, re-enable to extend' },
]

/** The task modes, orthogonal to the permission mode: which tools run and how the agent frames the
 *  work. The hints are the header pill's, word for word. */
export const TASK_MODES: Mode<TaskMode>[] = [
  { id: 'agent', label: 'Agent', icon: Bot, hint: 'full execution (default)' },
  { id: 'ask', label: 'Ask', icon: MessageSquare, hint: 'read-only Q&A; mutating tools are blocked' },
  { id: 'plan', label: 'Plan', icon: ClipboardList, hint: 'plan the work without executing any tool' },
  { id: 'build', label: 'Build', icon: Hammer, hint: 'scoped to producing an artifact / widget / skill' },
]

/** A choice as its tooltip says it: "Trust — auto-approve every tool in this chat". */
const sentence = (m: { label: string; hint: string }) => `${m.label} — ${m.hint}`

/** A pick the gateway answers: `null` once it has taken it, else the reason it refused. */
export type Choose<T> = (value: T) => Promise<string | null>

/** What the gateway said when it refused the last pick, beside the pill until it is next opened. */
function Refusal({ reason }: { reason: string }) {
  if (!reason) return null
  return <span role="alert" data-type="caption" className="max-w-[20rem] truncate text-danger" title={reason}>{reason}</span>
}

/** A pick-one menu's rows, with the shared cursor. Given `disabledReason`, every row stays
 *  reachable, dimmed, and says why it is not hers to pick. */
function ModeRows<T extends string>({ modes, value, onPick, disabledReason, tabIndexFor }: {
  modes: Mode<T>[]; value: T; onPick: (id: T) => void; disabledReason?: string
  /** The menu's roving tab stop, by the row's place in it. */
  tabIndexFor: (i: number) => number
}) {
  return (
    <>
      {modes.map((m, i) => {
        const Icon = m.icon
        return (
          <MenuRow key={m.id} role="menuitemradio" tabIndex={tabIndexFor(i)} label={m.label} hint={m.hint}
            title={disabledReason ? `${sentence(m)}. ${disabledReason}` : sentence(m)} nameIsLabel
            selected={m.id === value} disabled={!!disabledReason} icon={<Icon size={16} />}
            onClick={disabledReason ? undefined : () => onPick(m.id)} />
        )
      })}
    </>
  )
}

/** The task mode, beside the agent-and-model pill. */
export function TaskModePill({ value, onChoose }: { value: TaskMode; onChoose: Choose<TaskMode> }) {
  const [refusal, setRefusal] = useState('')
  const cur = TASK_MODES.find((m) => m.id === value) ?? TASK_MODES[0]
  const Icon = cur.icon
  const choose = async (id: TaskMode) => {
    setRefusal('')
    if (id === value) return
    setRefusal((await onChoose(id)) ?? '')
  }
  return (
    <span className="inline-flex min-w-0 items-center gap-xs">
      <Popover portal width={280} trigger={(open, toggle) => (
        <PillButton icon={<Icon size={16} strokeWidth={2} />} label={cur.label} dimension="Task mode" open={open}
          title={sentence(cur)} toggle={() => { if (!open) setRefusal(''); toggle() }} />
      )}>
        {(close) => <TaskModeMenu value={value} close={close} onPick={(id) => { close(); void choose(id) }} />}
      </Popover>
      <Refusal reason={refusal} />
    </span>
  )
}

function TaskModeMenu({ value, close, onPick }: { value: TaskMode; close: () => void; onPick: (id: TaskMode) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const { move, tabIndexFor } = useMenuCursor({
    containerRef: ref, count: TASK_MODES.length, openKey: 'open',
    initialIndex: Math.max(0, TASK_MODES.findIndex((m) => m.id === value)),
  })
  return (
    <div ref={ref} role="menu" aria-label="Task mode" className="flex flex-col gap-0.5"
      onKeyDown={(e) => { menuCursorKeydown(e.nativeEvent, { move, dismiss: close }) }}>
      <ModeRows modes={TASK_MODES} value={value} onPick={onPick} tabIndexFor={tabIndexFor} />
    </div>
  )
}

/** The sandbox and the reach of the next turn, read from the gateway: `undefined` while it is being
 *  read, `null` when it could not be. An open chat is read once its own read has landed (`ready`),
 *  for the agent it runs on; a new chat for the agent CLI it is set to (`runtime`), or the built-in
 *  agent. A change of either reads it again. */
export function useBoundaryReading(session: string | null, ready: boolean, runtime: string): ChatBoundary | null | undefined {
  const [reading, setReading] = useState<ChatBoundary | null | undefined>(undefined)
  useEffect(() => {
    if (!ready) return
    let alive = true
    setReading(undefined)
    api.chatBoundary({ session: session ?? '', runtime }).then(
      (r) => { if (alive) setReading(r) },
      () => { if (alive) setReading(null) },
    )
    return () => { alive = false }
  }, [session, ready, runtime])
  return reading
}

/** What a reading says while it has nothing to say yet, or could not be read: a gateway that does
 *  not serve the reading (an older one) answers nothing this can show, and says so rather than
 *  anything about the sandbox or the network. */
function said(reading: ChatBoundary | null | undefined, part: 'sandbox' | 'reach'): { label: string; said: string } {
  if (reading === undefined) return { label: 'Reading…', said: 'Reading…' }
  const r = reading?.[part]
  if (!r || typeof r.label !== 'string' || typeof r.said !== 'string') return { label: 'Not read', said: 'Not read: the gateway did not answer.' }
  return r
}

/** The Execution Boundary control: where the next turn may act, before it runs.
 *
 *  `folder` is the working folder in words (a path, or "the workspace"); `onChangeFolder` opens the
 *  Working directory prompt, for a chat that has started. `onMemoryMode` is given only for a chat
 *  not started yet, whose memory mode is chosen here; a started chat's is shown. `lockedReason` is
 *  why the permission mode is not the owner's to change (an app's conversation). */
export function ExecutionBoundary({ approval, onApproval, lockedReason, memoryMode, onMemoryMode, folder, onChangeFolder, reading }: {
  approval: ApprovalMode
  onApproval: Choose<ApprovalMode>
  lockedReason?: string
  memoryMode: MemoryMode
  onMemoryMode?: (m: MemoryMode) => void
  folder: string
  onChangeFolder?: () => void
  reading: ChatBoundary | null | undefined
}) {
  const [refusal, setRefusal] = useState('')
  const cur = PERMISSION_MODES.find((m) => m.id === approval) ?? PERMISSION_MODES[0]
  const memory = MEMORY_MODES.find((m) => m.id === memoryMode) ?? MEMORY_MODES[0]
  const Icon = cur.icon
  const sandbox = said(reading, 'sandbox')
  const reach = said(reading, 'reach')
  // A chat that keeps nothing says so on the control itself, so it is never mistaken for one that
  // does.
  const label = memoryMode === 'persistent' ? cur.label : `${cur.label} · ${memory.label}`
  // The whole boundary on one line, for a reader that does not open the control.
  const summary = [
    `Permission: ${cur.label}`, `Working folder: ${folder}`, `Sandbox: ${sandbox.label}`,
    `Network: ${reach.label}`, `Memory: ${memory.label}`,
  ].join(' · ')
  const choose = async (id: ApprovalMode) => {
    setRefusal('')
    if (id === approval) return
    setRefusal((await onApproval(id)) ?? '')
  }
  return (
    <span className="inline-flex min-w-0 items-center gap-xs">
      <Popover portal width={340} trigger={(open, toggle) => (
        <PillButton icon={<Icon size={16} strokeWidth={2} />} label={label} dimension="Execution boundary" open={open}
          wide={memoryMode !== 'persistent'} title={summary} toggle={() => { if (!open) setRefusal(''); toggle() }} />
      )}>
        {(close) => (
          <BoundaryPanel approval={approval} lockedReason={lockedReason} memoryMode={memoryMode} onMemoryMode={onMemoryMode}
            folder={folder} onChangeFolder={onChangeFolder} sandbox={sandbox.said} reach={reach.said} close={close}
            onPick={(id) => { close(); void choose(id) }} />
        )}
      </Popover>
      <Refusal reason={refusal} />
    </span>
  )
}

/** The control's open panel: the menu of what it changes, and what it shows under it. */
function BoundaryPanel({ approval, lockedReason, memoryMode, onMemoryMode, folder, onChangeFolder, sandbox, reach, close, onPick }: {
  approval: ApprovalMode; lockedReason?: string; memoryMode: MemoryMode; onMemoryMode?: (m: MemoryMode) => void
  folder: string; onChangeFolder?: () => void; sandbox: string; reach: string; close: () => void; onPick: (id: ApprovalMode) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const factsId = useId()
  const lockId = useId()
  const memory = MEMORY_MODES.find((m) => m.id === memoryMode) ?? MEMORY_MODES[0]
  const count = PERMISSION_MODES.length + (onMemoryMode ? MEMORY_MODES.length : 0) + (onChangeFolder ? 1 : 0)
  const { move, tabIndexFor } = useMenuCursor({
    containerRef: ref, count, openKey: 'open',
    initialIndex: Math.max(0, PERMISSION_MODES.findIndex((m) => m.id === approval)),
  })
  return (
    <div className="flex max-h-[480px] flex-col overflow-y-auto">
      {/* The menu holds what the control changes; what it only shows is its description, read as
          the menu opens and drawn under it. */}
      <div ref={ref} role="menu" aria-label="Execution boundary" aria-describedby={factsId} className="flex flex-col gap-0.5"
        onKeyDown={(e) => { menuCursorKeydown(e.nativeEvent, { move, dismiss: close }) }}>
        <div role="group" aria-label="Permission mode" aria-describedby={lockedReason ? lockId : undefined}>
          <div aria-hidden><Eyebrow className="px-m pt-s pb-1">Permission mode</Eyebrow></div>
          <ModeRows modes={PERMISSION_MODES} value={approval} onPick={onPick} disabledReason={lockedReason} tabIndexFor={tabIndexFor} />
          {lockedReason && <p id={lockId} aria-hidden data-type="caption" className="px-m pb-1 text-on-surface-var">{lockedReason}</p>}
        </div>
        {onMemoryMode && (
          <div role="group" aria-label="Memory" className="mt-xs border-t border-outline-variant/30">
            <div aria-hidden><Eyebrow className="px-m pt-s pb-1">Memory</Eyebrow></div>
            {MEMORY_MODES.map((m, i) => (
              <MenuRow key={m.id} role="menuitemradio" tabIndex={tabIndexFor(PERMISSION_MODES.length + i)} label={m.label} hint={m.hint} title={`${m.label} — ${m.hint}`} nameIsLabel
                selected={m.id === memoryMode} onClick={() => { onMemoryMode(m.id); close() }} />
            ))}
          </div>
        )}
        {onChangeFolder && (
          <div role="group" aria-label="Working folder" className="mt-xs border-t border-outline-variant/30 pt-xs">
            <MenuRow role="menuitem" tabIndex={tabIndexFor(count - 1)} icon={<FolderCog size={16} />} label="Change working folder…" hint={folder}
              title={folder} nameIsLabel onClick={() => { close(); onChangeFolder() }} />
          </div>
        )}
      </div>
      <dl id={factsId} data-type="caption" className="mt-xs flex flex-col gap-xs border-t border-outline-variant/30 px-m pt-s pb-xs text-on-surface-var">
        <Fact term="Working folder" said={folder} />
        <Fact term="Sandbox" said={sandbox} />
        <Fact term="Network" said={reach} />
        {!onMemoryMode && <Fact term="Memory" said={`${memory.label} — ${memory.hint}`} />}
      </dl>
    </div>
  )
}

/** One thing the control shows. Read as "Sandbox: <sentence>" when the menu's description is said
 *  as one line, so the colon and the space after it are there for a reader, not drawn. */
function Fact({ term, said: words }: { term: string; said: string }) {
  return (
    <div>
      <dt className="text-on-surface-low">{term}<span className="sr-only">: </span></dt>
      <dd className="break-words text-on-surface">{words}<span className="sr-only"> </span></dd>
    </div>
  )
}

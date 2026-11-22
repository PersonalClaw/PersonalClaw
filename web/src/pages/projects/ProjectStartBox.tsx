import { useEffect, useRef, useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import { chatLaunchPath } from '../../app/linkState'
import { failureSentence } from '../../app/reportingWrite'
import { api, type ApprovalMode, type MemoryMode } from '../../lib/api'
import { stageNewChat, type NewChatPicks } from '../../lib/newChatHandoff'
import { useComposerData } from '../../lib/useComposerData'
import { Composer } from '../../ui/Composer'
import type { ComposerControls, ComposerValue } from '../../ui/composer/types'
import { IconButton } from '../../ui/IconButton'
import { returnFocusTo } from '../../ui/useFocusTrap'
import { ChatPreferencesGear, ChatPreferencesWindow, changedHere } from '../chat/composer/ChatPreferences'
import { ExecutionBoundary, TaskModePill, useBoundaryReading } from '../chat/composer/ExecutionBoundary'
import { useExplainChoice } from '../chat/composer/useExplainChoice'
import { useInTurn, useReplyStyle } from '../chat/composer/useReplyStyle'

/** ── A project's start box: the chat's own composer, handing its draft to a new chat here ──────
 *
 *  The shared `Composer`, with the chat's pills: "+" (attach), the agent-and-model pill, Chat
 *  preferences, the task mode and the Execution Boundary (the permission mode, "Normal" as in
 *  Chat, with the folder the chat will work in, the project's, in its panel). Enter stages the
 *  draft, its files and its picks in memory under a one-use token (`lib/newChatHandoff`) and opens
 *  `#/chat/new?project=<id>&start=<token>`; the new chat takes the stage once and sends it through
 *  its own first send, so the chat is made in the project with the picks made here. The text never
 *  rides the address, so a reload, Back or a copied link opens an empty new chat.
 *
 *  Off with *offReason* (an archived project, or PersonalClaw unreachable): the draft stays, and
 *  Enter says why. With the project's folder missing, a line under the box says so. */

/** Optimize is in Chat preferences, which offers it only in a chat; mic and the slash menu are
 *  the chat's own. */
const START_CONTROLS: ComposerControls = {
  agent: true, model: true, approval: false, reasoning: true,
  attach: true, mic: false, optimize: false, slash: false,
}

const NEW_SELECTION: ComposerValue = { agent: '', model: 'Auto', approval: 'normal', taskMode: 'agent', reasoning: '' }

/** Why the gateway would refuse permission mode *mode* for a chat made now, or null. The box holds
 *  its pick until the chat is made, so it asks the gateway's own rule first, as the pick is made
 *  (a new chat's boundary reading says the modes the operator ceiling refuses), and never shows a
 *  mode the chat would not run in. Normal is never refused, and asks nothing. */
async function refusalOf(mode: ApprovalMode): Promise<string | null> {
  if (mode === 'normal') return null
  let reading
  try {
    reading = await api.chatBoundary()
  } catch (e) {
    return failureSentence('check whether this computer allows this permission mode', e)
  }
  const p = reading?.permission
  if (!p || !Array.isArray(p.refused) || !p.refused.includes(mode)) return null
  return typeof p.said === 'string' && p.said ? p.said : "This computer's operator settings don't allow this permission mode."
}

export function ProjectStartBox({ projectId, name, folder, offReason, folderMissing, navigate, fill, focusOnMount = false }: {
  projectId: string
  name: string
  /** Where the chat will work: the project's folder, or the workspace with none bound. */
  folder: string
  /** Why the box can't start a chat now; the draft stays. */
  offReason?: string
  folderMissing: boolean
  navigate: (to: string) => void
  /** A prompt to put in the box (a first run's starter), the owner's to send; a new `at` each time. */
  fill?: { text: string; at: number } | null
  /** Focus the box as it mounts: a project nothing has happened in yet starts here. */
  focusOnMount?: boolean
}) {
  const data = useComposerData()
  const [text, setText] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [selection, setSelection] = useState<ComposerValue>(NEW_SELECTION)
  const [memoryMode, setMemoryMode] = useState<MemoryMode>('persistent')
  // Chat preferences before the chat exists: each hook holds its pick (no session to write to), and
  // the hand-off carries what was picked here to the new chat, which writes it as it is made.
  const prefWrites = useInTurn()
  const replyStyle = useReplyStyle(() => null, prefWrites)
  const explain = useExplainChoice(() => null, replyStyle.adoptPreview, prefWrites)
  const picked = useRef<Pick<NewChatPicks, 'replyStyle' | 'explain'>>({})
  const [preferencesOpen, setPreferencesOpen] = useState(false)
  const gearWrap = useRef<HTMLSpanElement>(null)
  const reading = useBoundaryReading(null, true, '')
  const changed = useRef<Partial<ComposerValue>>({})
  const box = useRef<HTMLDivElement>(null)
  const focusBox = () => box.current?.querySelector<HTMLElement>('[contenteditable="true"], textarea')?.focus()
  useEffect(() => {
    if (!fill) return
    setText(fill.text)
    requestAnimationFrame(focusBox)
  }, [fill?.at]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (focusOnMount) requestAnimationFrame(focusBox) }, [focusOnMount])

  const start = () => {
    const draft = text.trim()
    if (!draft || offReason) return
    const picks: NewChatPicks = { ...changed.current, ...(memoryMode !== 'persistent' ? { memoryMode } : {}), ...picked.current }
    const token = stageNewChat({ text: draft, files, picks, project: projectId })
    navigate(chatLaunchPath({ project: projectId, start: token }))
  }
  const pick = (patch: Partial<ComposerValue>) => {
    changed.current = { ...changed.current, ...patch }
    setSelection((s) => ({ ...s, ...patch }))
  }

  return (
    <div ref={box} className="flex min-w-0 flex-col gap-xs">
      <Composer value={text} onChange={setText} onSend={start} placeholder={`Start a chat in ${name}`}
        controls={START_CONTROLS} data={data} selection={selection} onSelect={pick}
        onAttach={(fs) => setFiles((prev) => [...prev, ...fs.filter((f) => !prev.some((p) => p.name === f.name && p.size === f.size))])}
        sendHeldReason={offReason} settled
        hostPills={(
          <>
            <ChatPreferencesGear wrapRef={gearWrap} open={preferencesOpen} changed={changedHere(replyStyle.pill, explain.choice)}
              onOpen={() => setPreferencesOpen(true)} />
            <TaskModePill value={selection.taskMode ?? 'agent'} onChoose={async (m) => { pick({ taskMode: m }); return null }} />
            <ExecutionBoundary approval={selection.approval ?? 'normal'} onApproval={async (a) => {
              const refused = await refusalOf(a)
              if (!refused) pick({ approval: a })
              return refused
            }}
              memoryMode={memoryMode} onMemoryMode={setMemoryMode} folder={folder} reading={reading} />
          </>
        )} />
      {files.length > 0 ? (
        <ul aria-label="Attached files" className="flex min-w-0 flex-wrap gap-xs">
          {files.map((f) => (
            <li key={`${f.name}:${f.size}`} className="inline-flex min-w-0 items-center gap-0.5 rounded-full border border-outline-variant/40 py-0.5 pl-s pr-0.5">
              <span data-type="body-s" className="truncate text-on-surface">{f.name}</span>
              <IconButton icon={X} label={`Remove ${f.name}`} size={24} iconSize={13} onClick={() => setFiles((prev) => prev.filter((p) => p !== f))} />
            </li>
          ))}
        </ul>
      ) : null}
      {folderMissing ? (
        <p data-type="body-s" className="text-on-surface-var">{`${name}'s folder is missing, so chats here can't open its files. Choose another folder in About this project.`}</p>
      ) : null}
      <AnimatePresence onExitComplete={() => returnFocusTo(gearWrap.current?.querySelector('button'))}>
        {preferencesOpen ? (
          <ChatPreferencesWindow
            replyStyle={{ ...replyStyle.pill, onSelect: (s) => { picked.current = { ...picked.current, replyStyle: s }; return replyStyle.pill.onSelect(s) } }}
            explain={{ ...explain.choice, onSelect: (x) => { picked.current = { ...picked.current, explain: x }; return explain.choice.onSelect(x) } }}
            readHomeDefault={() => { void explain.readHomeDefault() }}
            canOptimize={false} onOptimize={() => setPreferencesOpen(false)}
            onChangeDefaults={() => { setPreferencesOpen(false); navigate('settings/agent') }}
            onClose={() => setPreferencesOpen(false)} />
        ) : null}
      </AnimatePresence>
    </div>
  )
}

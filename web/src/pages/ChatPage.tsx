import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import { Edit3, History, Search, MessageSquare, Trash2, Activity, Brain, Gauge, ChevronRight, ChevronDown, Quote, PanelRight, Clipboard, X, Pin, FileText, BookText, AlertTriangle, Pencil, Sparkles, Link2, Check, Repeat, Folder, FolderPlus, Tag as TagIcon, Columns3, List as ListIcon, EyeOff, Clock, Loader2, Wrench, Target, Code2 as CodeIcon, Paperclip, ExternalLink, ArrowDown, ArrowLeft, ArrowRight, ArrowUp, FolderKanban, GripVertical, Bot, ShieldCheck, Shield, Eye, Zap, ClipboardList, Hammer, Camera, NotebookPen, FolderCog, type LucideIcon } from 'lucide-react'
import { IconButton } from '../ui/IconButton'
import { TopBar } from '../ui/TopBar'
import { SidePanel } from '../ui/SidePanel'
import { Button } from '../ui/Button'
import { Segmented } from '../ui/Segmented'
import { ContextMenu, type ContextMenuItem } from '../ui/motion'
import { ProjectPicker } from '../ui/ProjectPicker'
import { HeaderActions, HeaderControl, HeaderSegmented, HeaderModePill } from '../ui/HeaderActions'
import { ClawMark } from '../ui/ClawMark'
import { ComposerStage } from '../ui/ComposerStage'
import { CollapseColumnButton, CollapsedBoardColumn, boardGridTemplate, useBoardCollapse } from '../ui/BoardCollapse'
import { PromptPalette } from './chat/PromptPalette'
import { SessionSkillsReview } from './chat/SessionSkillsReview'
import { DotGlow } from '../ui/DotGlow'
import { EmptyState, ListSkeleton, Skeleton } from '../ui/ListScaffold'
import { MessageUser } from '../ui/chat/MessageUser'
import { MessageAssistant } from '../ui/chat/MessageAssistant'
import { Spark } from '../ui/Spark'
import { StreamingIndicator } from '../ui/chat/StreamingIndicator'
import { Markdown } from '../ui/Markdown'
import { ToolCard } from './chat/ToolCard'
import { onToolResultFull } from './chat/toolResultBridge'
import { SdlcProgressCard, sdlcRefFromTool } from './chat/SdlcProgressCard'
import { ApprovalCard } from './chat/ApprovalCard'
import { ChatFilePanel } from './chat/ChatFilePanel'
import { sameSessionTarget, type CommentTarget } from '../ui/content/commentTarget'
import { ChatActivityPanel } from './chat/ChatActivityPanel'
import { AssistantActions, UserActions } from './chat/MessageActions'
import { parseOptions, parseSwitchToAgent } from './chat/parseAssistant'
import { type PasteBlock, shouldCollapsePaste, nextSeq, makePasteId, markerFor, expandPasteMarkers, pruneBlocks } from './chat/pasteBlocks'
import { Modal } from '../ui/Modal'
import { confirm, promptInput } from '../ui/dialog'
import { type ChatTurn, type Segment, type ToolSegment, type ApprovalSegment, type ActivitySegment, type SubagentCard, type HistMsg, userTurn, assistantTurn, hydrateTurns, turnText, deriveActivity } from './chat/chatTypes'
import { useIdentity, firstNameOf } from '../app/identity'
import { useIsMac } from '../app/usePlatform'
import { notify } from '../app/appSdk'
import { spring, stagger, listItemEnter, expr } from '../design/motion'
import { api, type ApprovalMode, type TaskMode, type ReasoningEffort, type ChatSessionSummary, type ChatHistoryMsg, type DiscoveredAgent, type MemoryMode, type NudgeLoop, type ChatFolder, type ChatTag, type RetagJob } from '../lib/api'
import { useChatSocket, type WsMessage } from '../lib/useChatSocket'
import { useStreamCoalescer } from './chat/useStreamCoalescer'
import { applyCoalescedFlush, insertActivity } from './chat/coalesceReducers'
import { useCachedData, invalidateCache } from '../lib/useCachedData'
import { useComposerData } from '../lib/useComposerData'
import type { ComposerControls, ComposerValue } from '../ui/composer/types'
import { Popover, MenuRow } from '../ui/Popover'
import { useQueryFlag, useQueryParam, type RouteProps } from '../app/useQueryState'

// Instant-paint cache for opened chat sessions. The transcript load is bespoke
// (hydrates the full segment model + restores selection/queue/side chat), so it
// can't use useCachedData directly — but we still want a revisited chat to paint
// its messages INSTANTLY instead of skeleton-then-fetch. We cache the last detail
// response per session id in sessionStorage; on mount we seed turns/title from it
// synchronously (no skeleton), then the normal load revalidates and overwrites.
// Keyed off the same detail the effect already fetches, so it's always consistent.
type ChatDetail = Awaited<ReturnType<typeof api.chatSessionDetail>>
const _CHAT_DETAIL_SS = 'chat-detail:'
function readCachedDetail(key: string): ChatDetail | null {
  try {
    const raw = sessionStorage.getItem(_CHAT_DETAIL_SS + key)
    return raw == null ? null : (JSON.parse(raw) as ChatDetail)
  } catch { return null }
}
function writeCachedDetail(key: string, d: ChatDetail): void {
  // Never cache a running turn's partial transcript — it would paint a stale,
  // mid-stream snapshot on revisit. Only settled sessions are safe to seed from.
  if (d.running) return
  try { sessionStorage.setItem(_CHAT_DETAIL_SS + key, JSON.stringify(d)) } catch { /* quota/serialize — skip cache */ }
}

// The approval-card scope picker's one vocabulary (resolved with the user): a per-
// approval SCOPE choice, not a mode toggle. `approved` = allow once; `trust` = allow
// all tools this session (sets session trust); `trust_agent` = always allow all tools
// for this agent (persists AgentProfile.approval_mode="auto") + this session; `rejected`
// = deny. (`trust_reads`/`yolo` remain valid backend actions the Permission axis uses,
// but the card no longer offers them — the card speaks only scope.)
type ApproveAction = 'approved' | 'rejected' | 'trust' | 'trust_agent' | 'trust_reads' | 'yolo'

// Memory mode for the NEXT new session — lives in the chat header gearbox (not
// the composer). Mirrors the composer's old MemoryPill options.
const MEMORY_MODES: { id: MemoryMode; label: string; hint: string }[] = [
  { id: 'persistent', label: 'Persistent', hint: 'Remember across sessions' },
  { id: 'temporary', label: 'Temporary', hint: 'Forget when the session ends' },
  { id: 'incognito', label: 'Incognito', hint: 'No memory read or write' },
]

// Options for the chat-header segmented controls. Permission mirrors the
// composer's approval modes; memory mirrors MEMORY_MODES — both as the canonical
// Segmented slider rather than a menu.
// `title` carries the same explanatory hint the composer's ApprovalPill shows,
// so hovering a header tab (now the primary approval control) tells the user
// what e.g. "YOLO" or "Plan" actually does rather than just its name.
const APPROVAL_SLIDER = [
  { key: 'normal', label: 'Normal', icon: Shield, title: 'Normal — ask before every tool' },
  { key: 'trust_reads', label: 'Trust reads', icon: Eye, title: 'Trust reads — auto-approve read-only tools' },
  { key: 'trust', label: 'Trust', icon: ShieldCheck, title: 'Trust — auto-approve every tool in this chat' },
  { key: 'yolo', label: 'YOLO', icon: Zap, title: 'YOLO — auto-approve everywhere; auto-expires, re-enable to extend' },
]

// Task mode — ORTHOGONAL to approval (which gates *whether* a tool auto-approves).
// Task mode gates *which* tools run + how the agent frames the work, layered on the
// active agent. Plan moved here from the approval slider (it was never an approval
// posture — it suppresses execution). Applies live + mid-chat like approval.
const TASK_MODE_SLIDER = [
  { key: 'agent', label: 'Agent', icon: Bot, title: 'Agent — full execution (default)' },
  { key: 'ask', label: 'Ask', icon: MessageSquare, title: 'Ask — read-only Q&A; mutating tools are blocked' },
  { key: 'plan', label: 'Plan', icon: ClipboardList, title: 'Plan — plan the work without executing any tool' },
  { key: 'build', label: 'Build', icon: Hammer, title: 'Build — scoped to producing an artifact / widget / skill' },
]

function greeting(name: string): string {
  const h = new Date().getHours()
  const part = h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening'
  return `Good ${part}, ${firstNameOf(name)}`
}

/** Contextual prompt-starter chips on the empty-chat hero. Sourced from the
 *  background-computed /api/suggestions (memory + recent activity), so they're
 *  personal, not generic. Clicking one fills the composer (the user reviews, then
 *  sends) rather than firing immediately. Silent when none are available. */
function SuggestionChips({ onPick }: { onPick: (s: string) => void }) {
  const { data } = useCachedData('chat:suggestions', () => api.suggestions().then((r) => r.suggestions).catch(() => [] as string[]), { persist: true })
  const items = (data ?? []).slice(0, 6)
  if (!items.length) return null
  return (
    <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }}>
      {items.map((s, i) => (
        <motion.button key={i} type="button" onClick={() => onPick(s)}
          initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: 0.04 * i }}
          className="rounded-pill border border-outline-variant/60 bg-surface-container px-3.5 py-2 text-left text-[0.82rem] text-on-surface-var transition-colors hover:border-primary/40 hover:bg-surface-high hover:text-on-surface">
          {s}
        </motion.button>
      ))}
    </div>
  )
}

/** Relative-time label for the history list (compact: "2m", "3h", "5d", "1w"). */
function relTimeShort(iso?: string): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (!t) return ''
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  if (s < 604800) return `${Math.floor(s / 86400)}d`
  return `${Math.floor(s / 604800)}w`
}

/** Body of the new-chat "Chat history" SidePanel: the most-recent manual sessions
 *  (title + when), each opening its session; a "View all" row deep-links to the
 *  full chat-history page. Reuses the same `chat:sessions` cache the history page
 *  paints from (instant, no extra fetch). */
function ChatHistorySidePanelBody({ navigate, onOpen }: { navigate: (p: string) => void; onOpen: (key: string) => void }) {
  const { data } = useCachedData<ChatSessionSummary[]>('chat:sessions', () => api.chatSessions().catch(() => []), { persist: false })
  const recent = useMemo(() => {
    const all = data ?? []
    return all
      .filter((s) => (s.origin ?? 'manual') === 'manual')
      .sort((a, b) => (Date.parse(b.last_activity_ts || b.last_ts || b.created || '') || 0) - (Date.parse(a.last_activity_ts || a.last_ts || a.created || '') || 0))
      .slice(0, 20)
  }, [data])
  return (
    <div className="flex flex-col gap-1">
      {data === undefined ? (
        <div className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">Loading…</div>
      ) : recent.length === 0 ? (
        <div className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">No chats yet.</div>
      ) : (
        <motion.div variants={{ animate: { transition: stagger(0.03) } }} initial="initial" animate="animate" className="flex flex-col gap-0.5">
          {recent.map((s) => (
            <motion.button key={s.key} type="button" variants={listItemEnter} onClick={() => onOpen(s.key)}
              whileHover={{ x: expr(3, 0.3) }} transition={spring.spatialFast}
              className="group flex items-center gap-s rounded-md px-2 py-2 text-left transition-colors hover:bg-surface-high">
              <MessageSquare size={14} className="shrink-0 text-on-surface-low group-hover:text-primary transition-colors" />
              <span className="min-w-0 flex-1 truncate text-on-surface-var text-[0.875rem] group-hover:text-on-surface">{s.title || 'Untitled chat'}</span>
              <span className="shrink-0 text-on-surface-low text-[0.6875rem] tabular-nums">{relTimeShort(s.last_activity_ts || s.last_ts || s.created)}</span>
            </motion.button>
          ))}
        </motion.div>
      )}
      {/* deep-link to the full, filterable/organizable chat-history page */}
      <button type="button" onClick={() => navigate('chat/history')}
        className="mt-1 flex items-center justify-center gap-1.5 rounded-md border border-outline-variant/40 px-2 py-2 text-on-surface-var text-[0.8125rem] transition-colors hover:bg-surface-high hover:text-on-surface"
        style={{ fontVariationSettings: '"wght" 470' }}>
        View all chats <ArrowRight size={13} className="shrink-0" />
      </button>
    </div>
  )
}

/** Body of the history page's session PEEK panel: a LIVE mini-chat — the latest
 *  turns plus a compact composer for quick replies without opening the full chat
 *  UI. Streams over the shared WS; "Continue" (full page) is a small control in
 *  the composer's action row. */
function SessionPeekBody({ sessionKey, onOpen }: { sessionKey: string; onOpen: () => void }) {
  const [detail, setDetail] = useState<{ title: string; messages: ChatHistoryMsg[] } | null>(null)
  const [failed, setFailed] = useState(false)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  // The in-flight streamed reply (grows chunk by chunk; folds into `detail` on done).
  const [streamText, setStreamText] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let alive = true
    setDetail(null); setFailed(false); setStreamText(null); setBusy(false)
    api.chatSessionDetail(sessionKey)
      .then((d) => { if (alive) setDetail({ title: d.title, messages: d.messages ?? [] }) })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [sessionKey])

  // Live stream: append chunks to the pending reply; land it on chat_done.
  useChatSocket((m) => {
    const d = m.data || {}
    if (d.session !== sessionKey) return
    if (m.type === 'chat_chunk') {
      setStreamText((t) => (t ?? '') + String(d.content ?? ''))
    } else if (m.type === 'chat_done') {
      setBusy(false)
      setStreamText((t) => {
        if (t) setDetail((prev) => prev && { ...prev, messages: [...prev.messages, { role: 'assistant', content: t } as ChatHistoryMsg] })
        return null
      })
    }
  })
  // Keep the tail in view as messages stream in.
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [detail?.messages.length, streamText])

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput(''); setBusy(true)
    setDetail((prev) => prev && { ...prev, messages: [...prev.messages, { role: 'user', content: text } as ChatHistoryMsg] })
    try { await api.sendChat(text, sessionKey) }
    catch { setBusy(false); notify('Could not send the message', 'error') }
  }

  if (failed) return <p className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">Couldn't load this chat.</p>
  if (!detail) return <ListSkeleton rows={5} />

  // Latest turns matter most in a peek; show the TAIL of the transcript, capped
  // so the panel stays snappy on long chats.
  const shown = detail.messages.filter((m) => m.role === 'user' || m.role === 'assistant').slice(-12)
  return (
    <div className="flex h-full min-h-0 flex-col gap-m">
      <div className="flex min-h-0 flex-1 flex-col gap-m overflow-y-auto">
        {shown.length === 0 && !streamText ? (
          <p className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">No messages yet — say hi below.</p>
        ) : shown.map((m, i) => (
          m.role === 'user' ? (
            <div key={i} className="ml-6 self-end rounded-lg bg-surface-high px-m py-s">
              <p className="whitespace-pre-wrap break-words text-on-surface text-[0.875rem] leading-relaxed">{String(m.content || '').slice(0, 800)}</p>
            </div>
          ) : (
            <div key={i} className="mr-2 min-w-0 text-[0.875rem]">
              <Markdown className="[&_p]:text-[0.875rem]">{parseSwitchToAgent(parseOptions(String(m.content || '').slice(0, 2000)).body).body}</Markdown>
            </div>
          )
        ))}
        {/* the streaming reply, growing live */}
        {streamText && (
          <div className="mr-2 min-w-0 text-[0.875rem]">
            <Markdown className="[&_p]:text-[0.875rem]">{streamText}</Markdown>
          </div>
        )}
        {busy && !streamText && (
          <div className="flex items-center gap-s">
            <Spark size={16} />
            <motion.span className="text-on-surface-low text-[0.8125rem]" animate={{ opacity: [0.5, 1, 0.5] }} transition={{ duration: 1.8, ease: 'easeInOut', repeat: Infinity }}>Thinking…</motion.span>
          </div>
        )}
        <div ref={endRef} />
      </div>
      {/* Mini composer — quick replies in-session; "Continue" opens the full UI.
          radius-lg tier: the 2xl token is a 48px sheet-scale round — outsized on a
          compact panel composer; lg stays proportionate AND tracks the user's
          global roundness slider like every token radius. */}
      <div className="shrink-0 rounded-lg bg-surface-container p-s shadow-[var(--shadow-composer)]">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder="Quick reply…"
          rows={2}
          aria-label="Quick reply"
          className="w-full resize-none bg-transparent px-s py-xs text-on-surface text-[0.875rem] outline-none placeholder:text-on-surface-low"
        />
        <div className="flex items-center gap-s">
          <button type="button" onClick={onOpen} title="Open the full chat UI"
            className="inline-flex items-center gap-1 rounded-pill px-m h-7 text-on-surface-low text-[0.75rem] transition-colors hover:bg-surface-high hover:text-on-surface">
            Continue in full chat <ArrowRight size={11} className="shrink-0" />
          </button>
          <motion.button type="button" onClick={send} disabled={!input.trim() || busy}
            whileTap={{ scale: 0.92 }} transition={spring.spatialFast}
            aria-label="Send"
            className="ml-auto inline-flex size-8 shrink-0 items-center justify-center rounded-pill bg-primary text-on-primary transition-colors hover:bg-primary-emphasis disabled:opacity-40 disabled:pointer-events-none">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <ArrowUp size={14} />}
          </motion.button>
        </div>
      </div>
    </div>
  )
}

const CHAT_CONTROLS: ComposerControls = {
  agent: true, model: true, approval: false, reasoning: true,
  attach: true, mic: true, optimize: true, slash: true,
}

// Authoritative "/help" output — the commands the dashboard handles directly.
// These run as instant GUI actions; other "/…" text is sent to the agent.
const SLASH_HELP = [
  '## Slash commands',
  '',
  'These run instantly in the dashboard — they don’t go to the model:',
  '',
  '- `/help` — show this list',
  '- `/optimize <prompt>` — optimize the prompt, then send the optimized version',
  '- `/clear` — start a fresh chat',
  '- `/prompts` — open the saved-prompt palette (`/prompts <name>` invokes one directly)',
  '- `/model` — switch the model for this chat',
  '- `/agent` — switch the agent for this chat',
  '- `/effort` — set the reasoning effort for this chat',
  '- `/project` — scope this new chat to a project (before it starts)',
  '- `/tools` — open the Tools page',
  '- `/undo [N]` — roll back the last N conversation turns (default 1; side effects are not reverted)',
  '- `/compact` — compact the conversation to free up context',
  '',
  'Type `/` in the message box any time to see and filter the full list.',
].join('\n')

/** Chat routing — the nav target (#/chat) lands on the HISTORY list by default;
 *  #/chat/new is a fresh NEW chat (reachable via "New chat" on the history page
 *  and the "History" button on the new-chat page); opening a session deep-links
 *  to #/chat/<sessionKey>. No left sidebar. */
export function ChatPage({ sub, navigate, navEpoch = 0, query, setQuery }: { sub: string; navigate: (p: string, opts?: { replace?: boolean }) => void; navEpoch?: number; query?: Record<string, string>; setQuery?: RouteProps['setQuery'] }) {
  const seg = (sub || '').split('/')[0]
  // A ?project=<id> on the bare/new route opens a fresh chat PRE-BOUND to that project
  // (the project page's "Chat" launch). It takes precedence over the history landing.
  const projectId = query?.project || ''
  // ?seed=<text> pre-fills the composer of a fresh chat (e.g. the design cockpit's
  // "Build with chat" hands the agent the loop id + token system + canvas contract).
  const seed = query?.seed || ''
  // ?agent=<name> pre-selects the agent on a fresh chat (SDK launchChat option).
  const agentParam = query?.agent || ''
  // The session's own view-panels (activity rail, open-file) ride ?activity / ?file
  // so they're Back-closable + refresh-stable. Threaded down to ChatSession.
  const q = query ?? {}
  const setQ: RouteProps['setQuery'] = setQuery ?? (() => {})
  if (projectId && (!seg || seg === 'new')) return <ChatSession key={`new-proj-${projectId}-${navEpoch}`} sessionId={null} navigate={navigate} query={q} setQuery={setQ} projectId={projectId} seed={seed} agent={agentParam} />
  // #/chat/history → the history list. (Chat history is also reachable as a
  // right-docked rail from the new-chat page, so bare #/chat lands on new chat.)
  if (seg === 'history') return <ChatHistoryPage navigate={navigate} query={q} setQuery={setQ} />
  // bare #/chat AND #/chat/new → a fresh NEW chat (the default landing — the Chat
  // nav target opens straight into a new conversation). The key folds in navEpoch
  // so clicking "New chat" always remounts a fresh session even when the URL was
  // silently rewritten by the composer's replaceState (the "New Chat stuck" fix).
  if (!seg || seg === 'new') return <ChatSession key={`new-${navEpoch}`} sessionId={null} navigate={navigate} query={q} setQuery={setQ} seed={seed} agent={agentParam} />
  // else it's a session key to resume (deep-linked; keyed off `sub` only so
  // unrelated navigations don't remount/reload it).
  return <ChatSession key={sub} sessionId={sub} navigate={navigate} query={q} setQuery={setQ} />
}

function ChatSession({ sessionId, navigate, query, setQuery, projectId: initialProjectId = '', seed = '', agent: initialAgent = '' }: { sessionId: string | null; navigate: (p: string, opts?: { replace?: boolean }) => void; query: Record<string, string>; setQuery: RouteProps['setQuery']; projectId?: string; seed?: string; agent?: string }) {
  const data = useComposerData()
  const { name } = useIdentity()
  // The project this chat scopes under. Seeded from the launch URL (?project=<id> from a
  // project page's Chat button), but ALSO user-pickable on a bare new chat via the
  // composer's project chooser (the vision's "optional project chooser"). Frozen once the
  // session starts (project_id is fixed at create, like memory mode).
  const [projectId, setProjectId] = useState(initialProjectId)
  useEffect(() => { setProjectId(initialProjectId) }, [initialProjectId])
  // Name of the project this chat is bound to — shown as a header chip so the user knows
  // the chat is scoped to that project's workspace.
  const [projectName, setProjectName] = useState('')
  useEffect(() => {
    if (!projectId) { setProjectName(''); return }
    let alive = true
    api.project(projectId).then((p) => { if (alive) setProjectName(p?.name || '') }).catch(() => {})
    return () => { alive = false }
  }, [projectId])
  // Instant-paint seed: if we have a cached detail for this session, hydrate its
  // turns synchronously so the transcript paints on the FIRST frame (skeleton only
  // shows for a genuinely-uncached first open). The load effect below revalidates.
  const seededDetail = useRef<ChatDetail | null>(sessionId ? readCachedDetail(sessionId) : null).current
  const [turns, setTurns] = useState<ChatTurn[]>(
    () => (seededDetail ? hydrateTurns(seededDetail.messages || [], false) : []),
  )
  const [input, setInput] = useState(seed)
  const [streaming, setStreaming] = useState(false)
  // Synchronous mirror of `streaming` for send()'s queue-vs-fresh-turn decision.
  // Two sends fired in one tick both close over the stale `streaming=false` state
  // (React hasn't re-rendered), so the 2nd would wrongly start a fresh turn instead
  // of queuing. This ref flips the instant a turn is committed, so the 2nd send
  // sees it and queues. Kept in sync with the state setter everywhere it changes.
  const streamingRef = useRef(false)
  // Bumped when a turn settles (streaming → false) so the session-skills review
  // (skill-ephemeral-promotion) re-checks for drafts the agent just captured.
  const [sessionSkillsEpoch, setSessionSkillsEpoch] = useState(0)
  const markStreaming = (v: boolean) => {
    if (streamingRef.current && !v) setSessionSkillsEpoch((n) => n + 1)
    streamingRef.current = v
    setStreaming(v)
  }
  const [composerFocused, setComposerFocused] = useState(false)
  const [promptPaletteOpen, setPromptPaletteOpen] = useState(false)
  // Bumped to open the model / agent / effort / project pickers for the "/model",
  // "/agent", "/effort" and "/project" GUI-affordance slash commands (see handleSlashCommand).
  const [openModelSignal, setOpenModelSignal] = useState(0)
  const [openAgentSignal, setOpenAgentSignal] = useState(0)
  const [openReasoningSignal, setOpenReasoningSignal] = useState(0)
  const [openProjectSignal, setOpenProjectSignal] = useState(0)
  // Only show the skeleton on a genuine cold open (session with nothing cached).
  // A cache hit paints instantly and revalidates silently in the background.
  const [loadingHistory, setLoadingHistory] = useState(!!sessionId && !seededDetail)
  const composerRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  // true when the transcript is scrolled away from the bottom — drives the
  // "jump to latest" pill (so streamed content arriving above the fold isn't lost).
  const [scrolledUp, setScrolledUp] = useState(false)
  // WS link state — false while the socket is down (drives the reconnecting cue).
  const [wsConnected, setWsConnected] = useState(true)
  // glow-travel target (Stage 3): while a turn is in flight, this ref points at
  // the active turn's DOM node so a glow SPLITS OFF the composer and travels to
  // sit behind it; cleared on done so the split-off light fades and the composer
  // glow stands alone. DotGlow reads `.current` live each animation frame.
  const glowTargetRef = useRef<HTMLDivElement | null>(null)
  // a SMALL, fixed-size, centered anchor at the top of the active turn. The glow
  // targets THIS (not the growing/right-aligned turn box) so the pool stays a
  // compact, symmetric, stable shape — no content-fit cutout, no expand-as-it-
  // streams. Mounted only while streaming; React re-points it to the newest last
  // turn as turns append.
  const glowAnchorRef = useRef<HTMLDivElement | null>(null)
  // activity panel (Stage 5) — Index/Files/Links, chat-only, toggled from header.
  // URL-backed (?activity=1, push → Back closes it; refresh restores it).
  const [activityOpen, setActivityOpen] = useQueryFlag(query, setQuery, 'activity')
  // New-chat page: a right-docked chat-history panel (the shared SidePanel), toggled
  // from the header. URL-bound (?history=1) so Back closes it + refresh restores it.
  const [historyOpen, setHistoryOpen] = useQueryFlag(query, setQuery, 'history')
  // per-turn DOM nodes so the Index tab can scroll to a turn.
  const turnNodes = useRef<Map<number, HTMLDivElement>>(new Map())
  // composer extras: @-mentioned file paths (sent as meta.files) + large-paste
  // blocks (collapsed to cards + inline [Paste #N] markers, expanded on send).
  const [mentionedFiles, setMentionedFiles] = useState<string[]>([])
  // @-mentioned knowledge library items (id+name) → threaded into send meta.knowledge;
  // the backend inlines each item's content for the turn.
  const [mentionedKnowledge, setMentionedKnowledge] = useState<{ id: string; name: string }[]>([])
  const [knowledgePickerOpen, setKnowledgePickerOpen] = useState(false)  // "Add knowledge to prompt" picker
  const [pasteBlocks, setPasteBlocks] = useState<PasteBlock[]>([])
  // uploaded-attachment workspace paths (threaded into send meta.files, B0) +
  // composer extras: prompt history (↑/↓), context-usage %, optimize-in-flight,
  // memory mode for the next NEW session, queued-while-streaming message.
  const [attachedPaths, setAttachedPaths] = useState<string[]>([])
  const isMac = useIsMac()  // gates the macOS-only "Capture screenshot" composer action
  // Live upload progress for a large attach (chunked/resumable). name → pct; a
  // small file completes in one POST and never shows here.
  const [uploads, setUploads] = useState<{ name: string; pct: number }[]>([])
  // AbortController for the in-flight attach upload, so the user can cancel it.
  const uploadAbortRef = useRef<AbortController | null>(null)
  const [promptHistory, setPromptHistory] = useState<string[]>([])
  const [contextPct, setContextPct] = useState(0)
  const [optimizing, setOptimizing] = useState(false)
  // the draft as it was just before an optimize-prompt rewrite, so the user can
  // revert if they don't like the optimized version (otherwise it's lost).
  const [preOptimize, setPreOptimize] = useState<string | null>(null)
  const [micError, setMicError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)  // transient confirmation (brief/workspace-dir)
  // Upload rejection (oversize / upload failure) — a message the user must ACT on
  // (pick a smaller file), so it's dismissible-but-persistent, NOT a 6s-vanishing
  // transient like micError.
  const [attachError, setAttachError] = useState<string | null>(null)
  const [memoryMode, setMemoryMode] = useState<MemoryMode>('persistent')
  // "Show full result" (tool-io-rendering TC4): the full raw of a projected tool
  // result, fetched on demand from the per-session store + shown in a modal. The
  // OPEN state is the URL (?result=<rawRef>, push); the fetched body + the tool
  // name (for the title) are derived — the ref is stable so a refresh re-fetches.
  const [resultRef, setResultRef] = useQueryParam(query, setQuery, 'result', '')
  const resultToolRef = useRef<string>('')
  const [resultBody, setResultBody] = useState<{ content: string; length: number } | null>(null)
  // Messages typed while a turn is streaming are QUEUED server-side (FIFO) and
  // shown above the composer; the backend dispatches them one-by-one as each turn
  // finishes. Driven by the queue_push / queue_pop / queue_cancel WS events.
  const [queued, setQueued] = useState<{ id: string; content: string }[]>([])
  // Async subagents (fire-and-forget) spawned this turn — live cards driven by
  // the subagent_spawn / subagent_tool / subagent_done WS events. Their final
  // output posts to the transcript as a "[Subagent completion event]" message
  // when the parent turn finishes (backend-injected).
  const [subagents, setSubagents] = useState<SubagentCard[]>([])
  // side chat (stage 6) — isolated Q&A in the activity panel's Side tab. Each
  // entry is {q, a}; `a` streams in via the chat.side_result WS event by run_id.
  const [sideMsgs, setSideMsgs] = useState<{ q: string; a: string; runId: string; done: boolean }[]>([])
  const [sideBusy, setSideBusy] = useState(false)
  const sideOpenedRef = useRef(false)  // side buffer opened/exists for this session

  const sessionRef = useRef<string | null>(sessionId)
  // In-flight session-creation promise. ensureSession checks a ref then awaits an
  // async create; two sends fired back-to-back on a brand-new chat (send #1, then
  // send #2 before create resolves) would BOTH see sessionRef=null and create two
  // sessions — send #2's turn lands in an orphaned session and is silently lost.
  // Memoizing the promise makes concurrent callers await the SAME creation.
  const ensureInFlightRef = useRef<Promise<string> | null>(null)
  // Wall-clock of the last WS frame seen for THIS session — drives the idle
  // approval-reconciler: a turn that parks on an approval sends no `chat_done`
  // and goes silent, so if a lost/early `approval` frame left no card, the stream
  // just stalls. When streaming stays quiet, we reconcile from session detail.
  const lastWsActivityRef = useRef<number>(0)
  const [selection, setSelection] = useState<ComposerValue>({ agent: initialAgent, model: 'Auto', approval: 'normal', taskMode: 'agent', reasoning: '' })
  // the resumed session's agent/model binding (from detail), restored into the
  // composer selection once discovered agents load. bindingNonce re-fires the
  // restore effect when a new session's binding arrives.
  const sessionBindingRef = useRef<{ agent: string; model: string; acp_provider: string; acp_provider_agent: string; reasoning_effort: string } | null>(null)
  const [bindingNonce, setBindingNonce] = useState(0)
  const [statusText, setStatusText] = useState('')
  const [latestActivity, setLatestActivity] = useState<string | null>(null)
  // The docked open-file peek panel. URL-backed (?file=<path>, push → Back closes;
  // refresh/deep-link reopens the same file beside the transcript).
  const [openFileRaw, setOpenFileRaw] = useQueryParam(query, setQuery, 'file', '')
  const openFile = openFileRaw || null
  const setOpenFile = (p: string | null) => setOpenFileRaw(p || '')
  // session title + header actions (#64): rename inline, LLM-regenerate, copy link.
  const [title, setTitle] = useState(seededDetail?.title || '')
  const [renaming, setRenaming] = useState(false)
  const [renameVal, setRenameVal] = useState('')
  const [linkCopied, setLinkCopied] = useState(false)
  const [regenningTitle, setRegenningTitle] = useState(false)
  // when true, the next chat_chunk starts a FRESH text segment (after a tool /
  // chat_segment boundary) rather than appending to the prior text run.
  const breakText = useRef(false)
  // P15 rAF stream coalescer: chat_chunk pushes into this; it flushes ONE growing
  // reveal per animation frame (instead of a setTurns per chunk) via onFlush, which
  // replaces the ACTIVE text run's text with the revealed-so-far prefix. `coalescing`
  // marks whether the trailing segment is the coalescer's active text run (so onFlush
  // replaces vs. appends). flushNow() on every segment boundary lands buffered text
  // before the run changes; reset() clears for the next run.
  const coalescing = useRef(false)
  const coalescer = useStreamCoalescer((revealed) => {
    patchLastAssistant((segs) => {
      const r = applyCoalescedFlush(segs, revealed, coalescing.current)
      coalescing.current = r.coalescing
      return r.segs
    })
  })
  const started = turns.length > 0
  // show the thinking indicator while streaming and the active assistant turn
  // has produced nothing renderable yet (no text/tool/approval segment)
  const lastTurn = turns[turns.length - 1]
  // thinking indicator only when the active turn has produced nothing visible
  // (an activity line counts as visible, so we don't stack two indicators)
  const showThinking = streaming && (!lastTurn || lastTurn.role === 'user' || lastTurn.segments.length === 0)

  // Screen-reader announcement: the visual "Thinking"/glow cue is silent to
  // assistive tech. A polite live region narrates the streaming lifecycle so a
  // SR user knows the assistant is working and when the reply has landed.
  const [srAnnounce, setSrAnnounce] = useState('')
  const wasStreamingRef = useRef(false)
  useEffect(() => {
    if (streaming && !wasStreamingRef.current) setSrAnnounce(statusText || 'Assistant is responding…')
    else if (!streaming && wasStreamingRef.current) setSrAnnounce('Response complete.')
    else if (streaming && statusText) setSrAnnounce(statusText)
    wasStreamingRef.current = streaming
  }, [streaming, statusText])

  // ── segment helpers: mutate the LAST assistant turn immutably ──
  const ensureAssistant = (list: ChatTurn[]): ChatTurn[] =>
    (list.length && list[list.length - 1].role === 'assistant') ? list : [...list, assistantTurn()]
  const patchLastAssistant = (fn: (segs: Segment[]) => Segment[]) =>
    setTurns((prev) => {
      const list = ensureAssistant(prev)
      const i = list.length - 1
      const next = [...list]
      next[i] = { ...next[i], segments: fn([...next[i].segments]) }
      return next
    })

  // Resume a deep-linked session: hydrate its messages from history.
  useEffect(() => {
    sessionRef.current = sessionId
    coalescer.reset(); coalescing.current = false  // drop any in-flight reveal from the prior session
    setQueued([])  // queue is per-session; clear when the open session changes
    setSubagents([])  // subagent cards are per-session too
    if (!sessionId) { setTurns([]); setLoadingHistory(false); return }
    let alive = true
    // Only skeleton if we have nothing seeded from cache; a cache hit already
    // painted the transcript and we revalidate silently underneath it.
    if (!seededDetail) setLoadingHistory(true)
    api.chatSessionDetail(sessionId).then((d) => {
      if (!alive) return
      // cache the settled detail so the next revisit paints instantly (writeCachedDetail
      // skips running turns to avoid seeding a stale mid-stream snapshot).
      writeCachedDetail(sessionId, d)
      // hydrate the FULL segment model (text + tool + approval) so a refreshed /
      // revisited session renders identically to a live one.
      const hydrated = hydrateTurns(d.messages || [], d.running)
      // BUT: this effect also fires right after a NEW chat's first send navigates
      // `new → chat/{key}` (sessionId change → REMOUNT). At that instant the just-sent
      // user turn was painted from the instant-paint seed and the assistant reply is
      // streaming in, but the server snapshot fetched here can be MID-PERSIST — carrying
      // the assistant reply but not yet the user message. Blindly replacing turns with
      // it drops the user's OWN message until a manual reload. Guard: never let a load
      // REDUCE the painted transcript below what we already show — if the hydrated
      // snapshot has FEWER turns than what's painted (seed + live stream), it's stale;
      // keep ours and let a later revalidation settle the canonical view. (streamingRef
      // can't gate this — it's a fresh `false` on the remounted instance.)
      setTurns((prev) => (hydrated.length >= prev.length ? hydrated : prev))
      // rehydrate any still-pending queued messages (mid-stream FIFO) so a reload
      // mid-queue shows them again above the composer.
      setQueued(Array.isArray(d.queue) ? d.queue.filter((q) => q && q.id).map((q) => ({ id: q.id, content: q.content })) : [])
      // Seed ↑/↓ prompt-history from the conversation's existing user turns, so
      // recall works immediately on a revisited chat (not only after sending a
      // new message this render). Oldest→newest, deduped against repeats.
      setPromptHistory(hydrated.reduce<string[]>((acc, t) => {
        if (t.role !== 'user') return acc
        const txt = turnText(t).trim()
        if (txt && acc[acc.length - 1] !== txt) acc.push(txt)
        return acc
      }, []).slice(-50))
      setTitle(d.title || '')
      // Restore BOTH composer axes to the session's actual posture. Unlike
      // agent/model (which must resolve against the discovered-agent catalog
      // below), task_mode + approval are plain enums the backend hands back
      // directly — set them now so a reopened/reloaded chat shows the real mode
      // instead of silently reverting the segmented controls to their defaults.
      setSelection((s) => ({
        ...s,
        taskMode: (d.task_mode || 'agent') as TaskMode,
        approval: (d.approval || 'normal') as ApprovalMode,
      }))
      // Restore the session's memory mode too, so mode-gated affordances (e.g. Fork,
      // which the backend refuses on a non-persistent session) reflect the real
      // posture of a reopened chat instead of the 'persistent' default.
      setMemoryMode((d.memory_mode || 'persistent') as MemoryMode)
      // remember the session's agent/model binding so the composer restores the
      // SAME selection it was using (resolved against discovered agents below,
      // once they've loaded).
      sessionBindingRef.current = {
        agent: d.agent || '', model: d.model || '',
        acp_provider: d.acp_provider || '', acp_provider_agent: d.acp_provider_agent || '',
        reasoning_effort: d.reasoning_effort || '',
      }
      setBindingNonce((n) => n + 1)
      // restore the persisted side chat (flat role list → {q,a} pairs).
      if (d.side?.messages?.length) {
        const pairs: { q: string; a: string; runId: string; done: boolean }[] = []
        for (const m of d.side.messages) {
          if (m.role === 'user') pairs.push({ q: m.content, a: '', runId: '', done: true })
          else if (pairs.length) pairs[pairs.length - 1].a += m.content
        }
        setSideMsgs(pairs)
        sideOpenedRef.current = true  // buffer already exists server-side
      }
      // resuming a still-running turn: show the live indicators and make the first
      // incoming chunk start a fresh text run (don't concat onto hydrated text).
      if (d.running) { markStreaming(true); breakText.current = true }
      setLoadingHistory(false)
    }).catch(() => { if (alive) setLoadingHistory(false) })
    return () => { alive = false }
  }, [sessionId])

  // Restore the composer selection from the resumed session's binding, once both
  // the binding (from detail) and the discovered-agent catalog have loaded. ACP
  // sessions store provider + provider_agent (+ effort) → map back to the
  // discovered agent's DISPLAY name; native sessions use agent/model directly.
  useEffect(() => {
    const b = sessionBindingRef.current
    if (!b) return
    // reasoning_effort is a per-turn session SETTING (no longer an effort-agent),
    // so restore it for BOTH native and ACP sessions.
    const reasoning = (b.reasoning_effort || '') as ReasoningEffort
    if (b.acp_provider) {
      const list = data.discovered?.[b.acp_provider] ?? []
      const match = list.find((a) => a.provider_agent === b.acp_provider_agent)
      if (match) setSelection((s) => ({ ...s, agent: match.name, model: b.model || 'Auto', reasoning }))
    } else if (b.agent || b.model || reasoning) {
      setSelection((s) => ({ ...s, agent: b.agent, model: b.model || 'Auto', reasoning }))
    }
  }, [bindingNonce, data.discovered])

  const onWs = useCallback((m: WsMessage) => {
    const s = sessionRef.current
    const d = m.data || {}
    // approval events are keyed by id, not session — but still gate on session
    if (!s || (d.session !== s && d.session !== undefined)) return
    lastWsActivityRef.current = Date.now()  // for the idle approval-reconciler
    switch (m.type) {
      case 'chat_chunk': {
        setStatusText('')
        const chunk = String(d.content ?? '')
        // A boundary (tool/approval/segment/chat_done/new-turn) set breakText → start a
        // FRESH coalesced run. Just RESET: every boundary that sets breakText already
        // called flushNow() itself to land its buffered tail, and flushNow() only
        // drains (revealed=pending) — it never CLEARS pending. So calling flushNow()
        // again HERE re-emits the PRIOR turn's full text into THIS (new) turn's segment
        // before reset() wipes it → turn N+1 visibly absorbed turn N's answer (K44).
        // reset() alone discards the stale buffer and opens a clean new run (coalescing
        // flips false so onFlush appends a fresh segment).
        // A boundary (tool/approval/segment/chat_done) or a fresh send set breakText →
        // start a NEW coalesced run. reset() alone (NOT flushNow) is correct: every
        // boundary that sets breakText already called flushNow() to land its buffered
        // tail, and flushNow only drains (revealed=pending) — it never CLEARS pending.
        // Calling flushNow() again here would re-emit the PRIOR run's full text into
        // the NEW turn's segment before reset() wipes it. reset() discards the stale
        // buffer and opens a clean run (coalescing flips false so onFlush appends fresh).
        if (breakText.current) { coalescer.reset(); coalescing.current = false; breakText.current = false }
        coalescer.push(chunk)  // rAF-coalesced; onFlush does the setTurns once/frame
        break
      }
      case 'chat_status': setStatusText(String(d.status ?? '')); break
      // A non-streamed message appended server-side (the only one that reaches
      // the UI this way today is a turn-level `error` — e.g. a provider/model
      // rejection). Without this the turn ends blank ("no response").
      case 'chat_message': {
        if (d.session && d.session !== sessionRef.current) break
        if (d.role === 'error') {
          coalescer.flushNow()  // land buffered text before the error segment
          markStreaming(false); setStatusText(''); setLatestActivity(null)
          patchLastAssistant((segs) => [...segs, { kind: 'error', text: String(d.content ?? 'The model returned an error.') }])
        }
        break
      }
      case 'activity_event': {
        // Coarse native activity. Skip generic status/session noise (covered by
        // the thinking indicator); surface only substantive lines (tool/hook/
        // permission/context), and only if this turn has no real tool cards yet.
        const kind = String(d.kind ?? '')
        const text = String(d.text ?? '')
        if (kind === 'status' || kind === 'session' || !text) break
        setLatestActivity(text)
        // insertActivity (pure, K42-tested): keeps a mid-stream activity line BEFORE
        // the coalescer's active text run so the next flush replaces-in-place instead
        // of pushing a duplicate; de-dupes adjacent identical lines; tool cards win.
        patchLastAssistant((segs) => insertActivity(segs, text, kind, coalescing.current))
        break
      }
      case 'tool_call': {
        coalescer.flushNow()  // land any buffered text before the tool card
        const id = String(d.tool_call_id ?? '')
        patchLastAssistant((segs) => {
          // a tool_call_update (resolved input/title) refines the existing card
          // in place rather than pushing a duplicate — agents stream the real
          // args after an initially-empty tool_call frame.
          const existing = segs.find((sg) => sg.kind === 'tool' && sg.id === id) as ToolSegment | undefined
          if (existing) {
            // keep the STABLE tool name; the update carries the refined summary
            // (command/file) as `detail` + the resolved input.
            if (d.input_preview) existing.input = String(d.input_preview)
            if (d.input !== undefined && d.input !== null) existing.inputObj = d.input
            if (d.detail) existing.detail = String(d.detail)
            if (d.tool && !d.update) existing.tool = String(d.tool)
            return [...segs]
          }
          segs.push({ kind: 'tool', id, tool: String(d.tool ?? 'tool'), detail: d.detail ? String(d.detail) : undefined,
            toolKind: String(d.kind ?? ''), input: String(d.input_preview ?? ''),
            inputObj: (d.input !== undefined && d.input !== null) ? d.input : undefined,
            purpose: String(d.purpose ?? ''), auto: !!d.auto, done: false })
          return segs
        })
        breakText.current = true  // text after a tool starts a new run
        break
      }
      case 'tool_result':
        patchLastAssistant((segs) => segs.map((sg) =>
          sg.kind === 'tool' && sg.id === String(d.tool_call_id ?? '')
            ? { ...sg, output: String(d.output ?? ''), done: true,
                contentType: d.content_type ? String(d.content_type) : sg.contentType,
                rawRef: d.raw_ref ? String(d.raw_ref) : sg.rawRef,
                truncated: d.truncated != null ? !!d.truncated : sg.truncated,
                originalLength: d.original_length != null ? Number(d.original_length) : sg.originalLength,
                recoveryHints: Array.isArray(d.recovery_hints) && d.recovery_hints.length
                  ? (d.recovery_hints as string[]) : sg.recoveryHints,
                ok: d.ok === false ? false : sg.ok }
            : sg))
        break
      case 'approval':
        coalescer.flushNow()  // land buffered text before the approval card
        patchLastAssistant((segs) => {
          const id = String(d.id ?? '')
          if (segs.some((sg) => sg.kind === 'approval' && sg.id === id)) return segs
          segs.push({ kind: 'approval', id, tool: String(d.tool ?? 'tool'), input: String(d.tool_input ?? ''), purpose: String(d.tool_purpose ?? ''), risk: (d.risk ? String(d.risk) : undefined) as ApprovalSegment['risk'] })
          return segs
        })
        breakText.current = true
        break
      case 'approval_resolved':
        setTurns((prev) => prev.map((t) => ({ ...t, segments: t.segments.map((sg) =>
          sg.kind === 'approval' && sg.id === String(d.id ?? '') ? { ...sg, resolved: d.approved ? 'approved' : 'rejected' } as ApprovalSegment : sg) })))
        break
      case 'chat_segment': coalescer.flushNow(); breakText.current = true; break
      // A regenerated answer landed (fresh reply → new variant) OR the user switched
      // which variant is active (here or in another tab). The backend has already
      // swapped the stored content; reflect it in place: replace the LAST assistant
      // turn's text with the echoed content and update the ‹n/N› switcher state. No
      // refetch — the event is authoritative. (Only meaningful once >1 variant.)
      case 'chat_variant_switch': {
        if (d.session !== sessionRef.current) break
        const content = String(d.content ?? '')
        const index = typeof d.index === 'number' ? (d.index as number) : 0
        const count = typeof d.count === 'number' ? (d.count as number) : undefined
        setTurns((prev) => {
          const i = prev.map((t) => t.role).lastIndexOf('assistant')
          if (i < 0) return prev
          const next = [...prev]
          // Collapse the assistant turn to a single text segment holding the active
          // variant — a regenerated answer is prose, so any prior tool/activity
          // segments belonged to the replaced version and must not bleed through.
          next[i] = { ...next[i], segments: [{ kind: 'text', text: content }], variantIdx: index,
            variantCount: count ?? next[i].variantCount }
          return next
        })
        break
      }
      case 'context_usage':
        if (d.session === sessionRef.current && typeof d.pct === 'number') setContextPct(d.pct as number)
        break
      // A title resolved server-side (auto-titled after the first turn, or renamed
      // from elsewhere). Reflect it live in the header of the open session, so the
      // bare session key swaps to the real title with no reload.
      case 'session_title': {
        const key = String(d.key ?? '')
        const t = String(d.title ?? '')
        if (key && t && key === sessionRef.current) setTitle(t)
        break
      }
      case 'chat_done': {
        coalescer.flushNow()  // fully reveal any buffered tail before the turn closes
        breakText.current = true; markStreaming(false); setStatusText(''); setLatestActivity(null)
        // Mid-stream messages are queued SERVER-side and the backend dispatches
        // the next one itself (streaming will flip back on via the next turn's
        // events). Nothing to drain client-side.
        // Refresh the instant-paint cache from the now-current turns so a revisit
        // seeds the LATEST transcript. This is a silent background write (no
        // skeleton, no visible reload) — the open tab already shows the right
        // content from streaming; we only bring the cached snapshot up to date.
        const sk = sessionRef.current
        if (sk) api.chatSessionDetail(sk).then((d) => writeCachedDetail(sk, d)).catch(() => {})
        break
      }
      // Visible message queue (mid-stream sends). The server owns the FIFO; these
      // events keep the strip above the composer in sync.
      case 'queue_push': {
        if (d.session !== sessionRef.current) break
        const id = String(d.queue_id ?? ''); const content = String(d.content ?? '')
        if (id) setQueued((prev) => (prev.some((q) => q.id === id) ? prev : [...prev, { id, content }]))
        break
      }
      case 'queue_pop':
      case 'queue_cancel': {
        if (d.session !== sessionRef.current) break
        const id = String(d.queue_id ?? '')
        if (id) setQueued((prev) => prev.filter((q) => q.id !== id))
        break
      }
      // A queued message the server just dequeued and is about to run. Normal sends
      // add the user bubble optimistically; queued ones only had a strip card, so
      // render the bubble now (the strip card is removed by the paired queue_pop).
      // breakText so the next chat_chunk starts a fresh assistant turn beneath it.
      case 'chat_user_message': {
        if (d.session !== sessionRef.current) break
        const content = String(d.content ?? '')
        if (!content) break
        breakText.current = true
        setTurns((prev) => [...prev, userTurn(content, d.ts ? String(d.ts) : undefined)])
        break
      }
      // Async subagent lifecycle (fire-and-forget). Cards live in the Activity
      // panel's Subagents tab; the final output also posts to the transcript.
      case 'subagent_spawn': {
        if (d.session !== sessionRef.current) break
        const id = String(d.id ?? '')
        if (!id) break
        setSubagents((prev) => prev.some((s) => s.id === id) ? prev
          : [...prev, { id, task: String(d.task ?? ''), agent: String(d.agent ?? ''), done: false }])
        break
      }
      case 'subagent_tool': {
        if (d.session !== sessionRef.current) break
        const id = String(d.id ?? '')
        if (id) setSubagents((prev) => prev.map((s) => s.id === id ? { ...s, lastTool: String(d.tool ?? '') } : s))
        break
      }
      case 'subagent_done': {
        if (d.session !== sessionRef.current) break
        const id = String(d.id ?? '')
        if (id) setSubagents((prev) => prev.map((s) => s.id === id
          ? { ...s, done: true, error: (d.error as string | null) ?? null, elapsed: typeof d.elapsed === 'number' ? d.elapsed : undefined, result: String(d.result ?? '') }
          : s))
        break
      }
      // Speak (stage 4): the backend streams base64 WAV per sentence for
      // immediate playback. Queue them so sentences play in order.
      case 'voice_chunk': if (d.audio) enqueueAudio(String(d.audio)); break
      // Side chat (stage 6): deltas stream by run_id. Match the entry by runId,
      // but fall back to the last not-yet-done entry — frames can arrive before
      // the sideTurn POST resolves and stamps the runId onto the entry.
      case 'chat.side_result': {
        const rid = String(d.run_id ?? '')
        const delta = String(d.delta ?? '')
        const done = !!d.done
        setSideMsgs((prev) => {
          let idx = prev.findIndex((m) => m.runId === rid)
          if (idx < 0) idx = prev.map((m) => !m.done).lastIndexOf(true)  // last open entry
          if (idx < 0) return prev
          const next = [...prev]
          next[idx] = { ...next[idx], runId: next[idx].runId || rid, a: next[idx].a + delta, done: done || next[idx].done }
          return next
        })
        if (done) setSideBusy(false)
        break
      }
    }
  }, [])
  // On WS reconnect, re-sync the bound session from the server: messages that
  // arrived during the outage were missed, and a turn that finished while the
  // socket was down would otherwise leave the UI stuck on "Thinking…". Re-hydrate
  // authoritatively (server's `running` flag corrects the streaming state).
  const resyncOnReconnect = useCallback(() => {
    const s = sessionRef.current
    if (!s) return
    api.chatSessionDetail(s).then((d) => {
      if (sessionRef.current !== s) return  // navigated away mid-fetch
      setTurns(hydrateTurns(d.messages || [], d.running))
      markStreaming(!!d.running)
      if (!d.running) setStatusText('')
    }).catch(() => {})
  }, [])
  useChatSocket(onWs, resyncOnReconnect, setWsConnected)

  // Idle approval-reconciler. A turn that parks on an approval sends no `chat_done`
  // and goes silent; if the `approval` WS frame was lost/early (arrived before the
  // socket delivered it, with no reconnect to trigger resyncOnReconnect), the card
  // never appears and the turn looks stuck until a manual reload. So while
  // streaming, if the WS has been quiet for a beat, reconcile from session detail:
  // when it reports pending_approval but the transcript shows no unresolved
  // approval segment, re-hydrate (which surfaces the persisted permission card).
  // Self-healing + cheap (fires only during a silent-while-streaming window).
  useEffect(() => {
    if (!streaming) return
    const iv = window.setInterval(() => {
      const s = sessionRef.current
      if (!s) return
      if (Date.now() - lastWsActivityRef.current < 3500) return  // WS still active — no need
      const showingApproval = turns.some((t) => t.segments.some((sg) => sg.kind === 'approval' && !(sg as ApprovalSegment).resolved))
      if (showingApproval) return  // card already up
      api.chatSessionDetail(s).then((d) => {
        if (sessionRef.current !== s) return
        if (!d.pending_approval) return  // genuinely just quiet (e.g. long model think) — leave it
        // Server is parked on an approval the client isn't showing → recover it.
        setTurns(hydrateTurns(d.messages || [], d.running))
        lastWsActivityRef.current = Date.now()  // don't re-fire every tick
      }).catch(() => {})
    }, 2000)
    return () => window.clearInterval(iv)
  }, [streaming, turns])

  // Global "/" shortcut → focus the composer (GitHub/Slack-style), unless the
  // user is already typing in a field or a menu/modal owns the key.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      // The composer is a CodeMirror editor (.cm-content), not a <textarea>.
      const cm = composerRef.current?.querySelector<HTMLElement>('.cm-content')
      if (cm) { e.preventDefault(); cm.focus() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Widget action bridge: an interactive `<widget>` button posts widget-action,
  // WidgetFrame re-dispatches `ne:widget-action`, and we send it as a chat turn.
  useEffect(() => {
    const onWidgetAction = (e: Event) => {
      const text = (e as CustomEvent).detail?.text
      if (typeof text === 'string' && text.trim()) send(text)
    }
    window.addEventListener('ne:widget-action', onWidgetAction as EventListener)
    return () => window.removeEventListener('ne:widget-action', onWidgetAction as EventListener)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const approve = useCallback((id: string, action: ApproveAction) => {
    const s = sessionRef.current
    if (s) api.approve(s, action, id).catch(() => {})
    // A card action that raises the session's standing posture must move the
    // Permission-mode pill to match — otherwise the pill keeps claiming "Normal —
    // ask before every tool" while the session silently auto-approves (a dishonest
    // state). `trust`/`trust_agent` → this chat is now trusted; `trust_reads` →
    // read-only auto; `yolo` → everywhere. `approved`/`rejected` are single-shot and
    // leave the mode alone. Mirror-only (no extra API call — the approve request
    // already set the server-side flag).
    const raised: ApprovalMode | null =
      action === 'trust' || action === 'trust_agent' ? 'trust'
      : action === 'trust_reads' ? 'trust_reads'
      : action === 'yolo' ? 'yolo'
      : null
    if (raised) setSelection((sel) => (sel.approval === raised ? sel : { ...sel, approval: raised }))
  }, [])

  // Auto-scroll the transcript to the bottom as content streams in — but only if
  // the user is already near the bottom (don't yank them while reading up).
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 200
    if (nearBottom) endRef.current?.scrollIntoView({ block: 'end' })
  }, [turns, streaming, showThinking])

  // Track distance from the bottom so a "jump to latest" pill can show when the
  // user has scrolled up (e.g. reading history while a reply streams in below).
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => setScrolledUp(el.scrollHeight - el.scrollTop - el.clientHeight > 240)
    onScroll()
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [started])

  // keep paste blocks in sync if the user manually deletes a [📋 Paste #N] marker.
  useEffect(() => {
    setPasteBlocks((prev) => (prev.length ? pruneBlocks(input, prev) : prev))
  }, [input])

  // Glow-travel: aim the split-off light at the active turn's small anchor while
  // streaming; clear it when the turn finishes so the light fades out (DotGlow
  // lerps the fade) and the composer glow stands alone. Re-points as turns append.
  useEffect(() => {
    glowTargetRef.current = streaming ? glowAnchorRef.current : null
  }, [streaming, turns])

  // "Show full result" (tool-io-rendering TC4): a tool card asked to reveal the
  // full raw of a projected result → the modal is URL-backed (?result=<rawRef>,
  // push → Back closes it; the ref is a stable per-session id so a refresh/deep-link
  // re-fetches). The card's bridge event just writes the param (+ stashes the tool
  // name for the title); the fetch effect below does the work off `resultRef`.
  useEffect(() => onToolResultFull(({ rawRef, tool }) => {
    resultToolRef.current = tool
    setResultRef(rawRef)
  }), []) // eslint-disable-line react-hooks/exhaustive-deps
  // Fetch the full result body whenever ?result names a ref (click, refresh, or
  // deep-link). Keyed on the ref + session so a reload re-fetches from the stable
  // per-session store; cleared when the param goes away (Back / close).
  useEffect(() => {
    if (!resultRef) { setResultBody(null); return }
    const sess = sessionRef.current
    if (!sess) return
    let alive = true
    setResultBody(null)
    fetch(`/api/chat/sessions/${encodeURIComponent(sess)}/tool-result/${encodeURIComponent(resultRef)}`,
      { headers: { 'X-Session-Key': 'dashboard:ui' } })
      // Surface the backend's actual reason (not-found vs evicted vs bad-grep)
      // rather than blanket-labeling every failure "expired" — that conflation
      // once masked a key-mismatch bug as a benign expiry.
      .then(async (res) => {
        if (res.ok) return res.json()
        const reason = await res.json().catch(() => null)
        throw new Error(reason?.error || `HTTP ${res.status}`)
      })
      .then((d) => { if (alive) setResultBody({ content: String(d.content ?? ''), length: Number(d.length ?? 0) }) })
      .catch((e) => { if (alive) setResultBody({ content: `(couldn't load the full result: ${String(e?.message || e)})`, length: 0 }) })
    return () => { alive = false }
  }, [resultRef])

  async function ensureSession(seedMessages?: HistMsg[]): Promise<string> {
    if (sessionRef.current) return sessionRef.current
    // Concurrent callers (rapid back-to-back sends on a brand-new chat) share the
    // SAME creation instead of each minting a session — see ensureInFlightRef.
    if (ensureInFlightRef.current) return ensureInFlightRef.current
    const p = (async () => {
      const acp = acpFor(selection.agent)
      const created = await api.createChatSession({
        // only pass agent/model for NATIVE agents at create; ACP binds below
        agent: acp ? undefined : (selection.agent || undefined),
        model: acp ? undefined : (selection.model && selection.model !== 'Auto' ? selection.model : undefined),
        // memory mode is a create-time property of the session (not per-message).
        memory_mode: memoryMode !== 'persistent' ? memoryMode : undefined,
        // Bind the chat to a project when launched from one — the backend scopes the
        // session to the project's workspace + (Slice 6 D2) feeds its loop history/context.
        project_id: projectId || undefined,
      })
      sessionRef.current = created.key
      // The navigate below changes the URL seg from `new` → the session key, which
      // REMOUNTS ChatSession (different React key) — dropping the optimistic user
      // turn we just added. Seed the instant-paint cache with the just-sent user
      // message(s) FIRST, so the remounted instance hydrates them synchronously
      // (seededDetail → loadingHistory=false): the user's message shows on the first
      // frame with NO skeleton over it, and only the pending agent reply loads.
      if (seedMessages?.length) {
        writeCachedDetail(created.key, { key: created.key, title: '', messages: seedMessages, running: false } as unknown as ChatDetail)
      }
      // Replace (not push) so the freshly-created session id backfills the URL
      // without adding a history entry — and via the router, not a raw
      // history.replaceState bypass.
      navigate(`chat/${created.key}`, { replace: true })
      if (acp) await api.setSessionAcpAgent(created.key, { provider: acp.providerId, provider_agent: acp.agent.provider_agent, model: selection.model && selection.model !== 'Auto' ? selection.model : undefined }).catch(() => {})
      if (selection.approval !== 'normal') await api.setApprovalMode(selection.approval, created.key).catch(() => {})
      if (selection.taskMode !== 'agent') await api.setTaskMode(selection.taskMode, created.key).catch(() => {})
      // Persist a pre-start reasoning-effort pick (applySelection couldn't, since the
      // session didn't exist yet).
      if (selection.reasoning) await api.setReasoningEffort(created.key, selection.reasoning).catch(() => {})
      return created.key
    })()
    ensureInFlightRef.current = p
    try {
      return await p
    } finally {
      ensureInFlightRef.current = null
    }
  }

  // ── Slash commands (Hybrid model) ──────────────────────────────────────
  // A curated set of commands map to an INSTANT GUI action and never reach the
  // model (that's the honest behavior — the model would only improvise them).
  // `/compact` and any other command fall through to the backend, which either
  // handles them server-side (e.g. compaction) or dispatches to the native
  // harness. Returns true when handled client-side so send() stops.
  function handleSlashCommand(t: string): boolean {
    if (!t.startsWith('/')) return false
    const [cmd, ...rest] = t.split(/\s+/)
    const arg = rest.join(' ').trim()
    switch (cmd) {
      case '/clear':
        setInput(''); navigate('chat/new'); return true
      case '/prompts':
        // "/prompts <name>" still flows to the backend (it invokes the named
        // prompt); bare "/prompts" opens the palette here.
        if (arg) return false
        setInput(''); setPromptPaletteOpen(true); return true
      case '/model':
        setInput(''); setOpenModelSignal((n) => n + 1); return true
      case '/agent':
        setInput(''); setOpenAgentSignal((n) => n + 1); return true
      case '/effort':
        setInput(''); setOpenReasoningSignal((n) => n + 1); return true
      case '/tools':
        setInput(''); navigate('tools'); return true
      case '/project':
        // Project binding is a create-time choice — only actionable before the
        // chat starts. Once started it's frozen (the header shows the binding).
        setInput('')
        if (!started) setOpenProjectSignal((n) => n + 1)
        return true
      case '/help':
        setInput('')
        setTurns((prev) => [...prev, userTurn(t), assistantTurn(SLASH_HELP)])
        return true
      default:
        return false
    }
  }

  async function send(text = input, opts?: { original?: string }) {
    const t = text.trim()
    if (!t) return
    // Use the synchronous streamingRef (not the `streaming` state) for the queue-vs-
    // fresh-turn decision: two sends in one tick both see the stale state, but the
    // ref flips the instant the first turn commits — so the second correctly queues.
    const isStreaming = streamingRef.current
    // /optimize <prompt> — one-shot: optimize the given prompt in the background,
    // then send the optimized version as the turn (bubble shows original + optimized).
    // Bare "/optimize" with no prompt just clears (nothing to optimize).
    if (!isStreaming && !opts?.original) {
      const m = t.match(/^\/optimize(?:\s+([\s\S]+))?$/i)
      if (m) {
        const prompt = (m[1] ?? '').trim()
        setInput('')
        if (prompt) void optimizeAndSend(prompt)
        return
      }
      // /undo [N] — roll back the last N conversation turns (default 1). Async (hits the
      // backend + re-hydrates), so handled here alongside /optimize rather than in the
      // sync GUI-command switch. No-op on an unstarted session.
      const u = t.match(/^\/undo(?:\s+(\d+))?$/i)
      if (u) {
        setInput('')
        const n = Math.max(1, parseInt(u[1] ?? '1', 10) || 1)
        void undoTurns(n)
        return
      }
    }
    // GUI-affordance slash commands run instantly and never hit the model.
    if (!isStreaming && handleSlashCommand(t)) return
    // Optimize provenance: if this send carries an explicit `original` (from
    // /optimize), use it; otherwise the Sparkles preview path leaves the optimized
    // text in the input with `preOptimize` holding what the user first typed.
    const original = opts?.original ?? (preOptimize !== null && preOptimize.trim() !== t ? preOptimize.trim() : undefined)
    // Mid-run send → QUEUE it server-side (FIFO). The backend dispatches queued
    // messages one-by-one as each turn finishes, and echoes queue_push so the
    // strip above the composer shows it (with a cancel affordance). The optimistic
    // input clear + the queue_push echo keep the UI responsive.
    if (isStreaming) {
      setInput('')
      ensureSession().then((s) => api.sendChat(t, s, undefined, 'followup')).catch(() => {})
      return
    }
    // The bubble keeps the prompt as typed (paste markers shown as chips); the
    // MODEL receives the markers expanded to the full pasted content.
    const blocks = pasteBlocks
    const llmText = expandPasteMarkers(t, blocks)
    // meta.files = @-mentioned workspace files + uploaded attachments (B0).
    const files = [...mentionedFiles, ...attachedPaths]
    // keep the paste blocks on the turn so the bubble renders [Paste #N] as
    // inspectable chips (only those still referenced in the sent text).
    const turnPastes = pruneBlocks(t, blocks).map((b) => ({ seq: b.seq, lines: b.lines, content: b.content }))
    // Stamp a client ts and pass it to the backend so it stores the SAME ts on the
    // user message. The server otherwise skips broadcasting the user echo ("FE adds
    // optimistically"), leaving a live turn's ts undefined until a reload — which
    // broke Edit & resend (it locates the message by ts → "index or ts required").
    const clientTs = new Date().toISOString()
    // When optimized: bubble shows `original` as primary + `t` (optimized) collapsed.
    setTurns((prev) => [...prev, userTurn(original ?? t, clientTs, turnPastes.length ? turnPastes : undefined, files, original ? t : undefined)])
    setPromptHistory((prev) => { const h = original ?? t; return (prev[prev.length - 1] === h ? prev : [...prev, h]).slice(-50) })
    const knowledgeIds = mentionedKnowledge.map((k) => k.id)
    // breakText=TRUE: a fresh send must open a NEW coalesced text run. A follow-up in
    // an existing chat streams in right after the prior turn — the backend does NOT
    // always emit a chat_done/chat_segment boundary between turns (esp. YOLO/queued
    // dispatch), so without this the new turn's chunks would append onto the PRIOR
    // turn's still-live coalescer run → turn N+1's bubble absorbed turn N's whole
    // answer (K44). true is safe on the very first turn too (reset on an empty core
    // is a no-op). We add the user turn locally above, so the next chat_chunk's
    // reset() lands the fresh assistant turn beneath it.
    setInput(''); setPreOptimize(null); markStreaming(true); breakText.current = true
    setPasteBlocks([]); setMentionedFiles([]); setAttachedPaths([]); setMentionedKnowledge([])
    try {
      const meta: Record<string, unknown> = { client_ts: clientTs }
      if (files.length) meta.files = files
      if (knowledgeIds.length) meta.knowledge = knowledgeIds
      // persist paste blocks so chips survive reload — hydrateTurns re-collapses
      // the expanded content back to [Paste #N] markers using these.
      if (turnPastes.length) meta.pastes = turnPastes
      // record the user's original prompt so the optimized-turn bubble can show
      // both after reload (content sent to the model is the optimized text).
      if (original) meta.original = original
      // On a brand-new chat, seed the instant-paint cache with THIS user message so
      // the post-create remount paints it immediately (no skeleton over the user's
      // own words) — mirrors the persisted history shape hydrateTurns expects.
      const seed: HistMsg[] = [{ role: 'user', content: llmText, ts: clientTs, meta: meta as HistMsg['meta'] }]
      await api.sendChat(llmText, await ensureSession(seed), meta)
    }
    catch (e) { markStreaming(false); patchLastAssistant((segs) => [...segs, { kind: 'text', text: `⚠️ ${(e as Error).message}` }]) }
  }

  // Optimize the current draft via the prompt optimizer (last 10 turns as context).
  async function optimize() {
    const t = input.trim()
    if (!t || optimizing) return
    setOptimizing(true)
    try {
      const ctx = turns.slice(-10).map((tn) => turnText(tn).slice(0, 200)).join('\n')
      const r = await api.optimizePrompt(t, ctx)
      if (r.changed && r.optimized) { setPreOptimize(input); setInput(r.optimized) }
    } catch { /* keep the draft on failure */ }
    finally { setOptimizing(false) }
  }
  // /optimize one-shot: optimize `raw` in the background, then send the optimized
  // text (recording `raw` as the original so the bubble shows both). If the
  // optimizer returns unchanged or errors, just send the original as-is.
  async function optimizeAndSend(raw: string) {
    setOptimizing(true)
    let optimized = ''
    try {
      const ctx = turns.slice(-10).map((tn) => turnText(tn).slice(0, 200)).join('\n')
      const r = await api.optimizePrompt(raw, ctx)
      if (r.changed && r.optimized && r.optimized.trim() !== raw) optimized = r.optimized.trim()
    } catch { /* fall through — send the original unchanged */ }
    finally { setOptimizing(false) }
    if (optimized) await send(optimized, { original: raw })
    else await send(raw)
  }
  // restore the pre-optimize draft (the optimize rewrite is otherwise lossy).
  function revertOptimize() {
    if (preOptimize === null) return
    setInput(preOptimize)
    setPreOptimize(null)
  }
  // /undo [N] — roll back N conversation turns via the backend, then re-hydrate the
  // transcript from the truncated server state (so the UI matches disk) + append an
  // honest notice that side effects were NOT reverted (power-user-surfaces P7).
  async function undoTurns(n: number) {
    const s = sessionRef.current
    if (!s) return  // nothing started yet
    try {
      const r = await api.undoChat(s, n)
      const d = await api.chatSessionDetail(s)
      const rehydrated = hydrateTurns(d.messages || [], false)
      setTurns([...rehydrated, assistantTurn(r.notice)])
    } catch { /* leave the transcript as-is on failure */ }
  }
  async function transcribe(blob: Blob): Promise<string> {
    const r = await api.transcribeAudio(blob)
    // Surface failures: otherwise a denied/unconfigured STT just drops the
    // recording silently after the spinner — the user has no idea why no text
    // appeared. The notice auto-clears so it doesn't linger.
    if (r.error) {
      const msg = /not available/i.test(r.error)
        ? 'Voice input needs a speech-to-text model — configure one in Settings → AI & Models.'
        : `Couldn’t transcribe audio: ${r.error}`
      setMicError(msg)
      window.setTimeout(() => setMicError(null), 6000)
      return ''
    }
    setMicError(null)
    return r.text ?? ''
  }

  // ── composer extras (mentions + paste) ──
  function onMentionFile(file: { path: string; name: string }) {
    setMentionedFiles((prev) => (prev.includes(file.path) ? prev : [...prev, file.path]))
  }
  function onMentionKnowledge(item: { id: string; name: string }) {
    setMentionedKnowledge((prev) => (prev.some((k) => k.id === item.id) ? prev : [...prev, item]))
  }
  // large paste → a removable card + an inline [📋 Paste #N] marker. The composer
  // is a CodeMirror editor (not a <textarea>); appending the marker via the
  // controlled value re-syncs the editor doc and lands the cursor at the end, so
  // we append the marker and refocus the editor for continued typing.
  function onLargePaste(text: string): boolean {
    if (!shouldCollapsePaste(text)) return false
    const seq = nextSeq(pasteBlocks)
    const block: PasteBlock = { id: makePasteId(seq), seq, lines: text.split('\n').length, content: text }
    const marker = markerFor(seq)
    setPasteBlocks((prev) => [...prev, block])
    setInput((prev) => prev + marker)
    requestAnimationFrame(() => composerRef.current?.querySelector<HTMLElement>('.cm-content')?.focus())
    return true
  }
  function removePaste(seq: number) {
    setPasteBlocks((prev) => prev.filter((b) => b.seq !== seq))
    setInput((prev) => prev.replace(markerFor(seq), '').replace(/  +/g, ' '))
  }

  async function stop() {
    markStreaming(false)
    if (sessionRef.current) await api.stopChat(sessionRef.current).catch(() => {})
  }

  // ── message actions (stage 4) ──
  const [editingTurn, setEditingTurn] = useState<number | null>(null)

  async function regenerate() {
    const s = sessionRef.current
    if (!s || streaming) return
    // drop the last assistant turn locally; the fresh reply streams in via WS.
    setTurns((prev) => {
      const i = prev.map((t) => t.role).lastIndexOf('assistant')
      return i >= 0 ? prev.slice(0, i) : prev
    })
    markStreaming(true); breakText.current = true
    try { await api.regenerate(s) }
    catch (e) { markStreaming(false); patchLastAssistant((segs) => [...segs, { kind: 'text', text: `⚠️ ${(e as Error).message}` }]) }
  }

  // Page to a prior/next regenerated answer. The backend swaps the active variant
  // and broadcasts chat_variant_switch, which the WS handler applies in place — so
  // this just fires the request (no optimistic mutation, the echo is authoritative).
  async function switchVariant(index: number) {
    const s = sessionRef.current
    if (!s || streaming) return
    try { await api.switchVariant(s, index) }
    catch (e) {
      setMicError(`Couldn’t switch answer: ${(e as Error).message}`)
      window.setTimeout(() => setMicError(null), 6000)
    }
  }

  // index into the VISIBLE user/assistant list (what the backend's fork +
  // edit-resend index expects), skipping non-message turns. Here every turn is
  // user/assistant, so it's just the turn index.
  function forkAt(turnIndex: number) {
    const s = sessionRef.current
    if (!s) return
    api.forkSession(s, turnIndex)
      .then((r) => { if (r?.key) navigate(`chat/${r.key}`) })
      .catch((e: Error) => {
        // Surface the failure instead of a silent no-op — the user clicked Fork
        // and nothing happening looks broken.
        setMicError(`Couldn’t fork this chat: ${e.message}`)
        window.setTimeout(() => setMicError(null), 6000)
      })
  }

  async function editResend(turnIndex: number, content: string) {
    const s = sessionRef.current
    const t = content.trim()
    if (!s || !t || streaming) return
    const turn = turns[turnIndex]
    setEditingTurn(null)
    // Locate the message by the ORIGINAL turn's ts (backend truncates from there),
    // and stamp the re-added turn with a FRESH ts that the backend also stores —
    // so an immediate SECOND edit-resend still has a matching ts (the backend
    // re-appends the edited message, which would otherwise get a new server ts the
    // FE doesn't know). Falls back to the index when the original turn has no ts.
    const newTs = new Date().toISOString()
    setTurns((prev) => [...prev.slice(0, turnIndex), userTurn(t, newTs)])
    // breakText=TRUE: the re-sent turn's fresh reply must open a NEW coalesced run.
    // We truncate the turns above, but the coalescer core still holds the PRIOR
    // answer's buffer; without breakText the incoming chunks append onto that stale
    // run → the new answer renders glued onto the old one (K44/K45). The next
    // chat_chunk's reset() discards the stale buffer and starts clean.
    markStreaming(true); breakText.current = true
    try { await api.editResend(s, t, turn?.ts, turnIndex, newTs) }
    catch (e) { markStreaming(false); patchLastAssistant((segs) => [...segs, { kind: 'text', text: `⚠️ ${(e as Error).message}` }]) }
  }

  // Voice playback uses the Web Audio API, not an <audio> element: Chrome
  // rejects piper's WAV stream via HTMLAudioElement.play() with NotSupportedError
  // even though the bytes are valid (decodeAudioData decodes them fine). The
  // AudioContext is created + resumed inside the Speak click gesture so the
  // chunks — which arrive 1-2s later over WS, outside the activation window —
  // still play (a context resumed during a gesture stays running).
  const audioCtxRef = useRef<AudioContext | null>(null)
  const audioPlayHeadRef = useRef(0)  // ctx-time cursor so chunks queue gaplessly
  const audioSourcesRef = useRef<AudioBufferSourceNode[]>([])  // live sources, for Stop
  // Which assistant turn is currently being spoken (drives the play/stop button).
  // Set when Speak is clicked, cleared when the last scheduled chunk finishes or
  // the user stops. Generation counter ignores stale chunks after a stop/restart.
  const [speakingTurn, setSpeakingTurn] = useState<number | null>(null)
  const speakGenRef = useRef(0)

  function getAudioCtx(): AudioContext | null {
    if (!audioCtxRef.current) {
      const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!Ctor) return null
      audioCtxRef.current = new Ctor()
    }
    return audioCtxRef.current
  }

  function stopSpeak() {
    speakGenRef.current++  // invalidate in-flight chunks from the stopped run
    for (const src of audioSourcesRef.current) { try { src.stop() } catch { /* already ended */ } }
    audioSourcesRef.current = []
    audioPlayHeadRef.current = 0
    setSpeakingTurn(null)
  }

  function speak(text: string, turnIndex: number) {
    // Toggle: clicking Speak on the turn that's already playing stops it.
    if (speakingTurn === turnIndex) { stopSpeak(); return }
    stopSpeak()  // stop any other turn first — only one plays at a time
    const s = sessionRef.current
    // Prime audio within the click's user-activation so later chunks can play.
    const ctx = getAudioCtx()
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {})
    speakGenRef.current++
    setSpeakingTurn(turnIndex)
    // Surface TTS failures (no voice selected → 503, synth error) instead of
    // silently doing nothing after the Speak button's brief spinner.
    return api.voiceSynthesize(text, s ?? '').catch((e: Error) => {
      setSpeakingTurn((cur) => (cur === turnIndex ? null : cur))
      const msg = /TTS voice|no.*voice|Settings/i.test(e.message)
        ? 'Text-to-speech needs a voice — choose one in Settings → AI & Models.'
        : `Couldn’t play audio: ${e.message}`
      setMicError(msg)
      window.setTimeout(() => setMicError(null), 6000)
    })
  }
  // voice_chunk WAV stream → decode + schedule on the AudioContext timeline so
  // sentences play back-to-back without gaps or overlap. decodeAudioData is
  // async, so guard ordering with a per-chunk schedule against a running cursor.
  async function enqueueAudio(b64: string) {
    const ctx = getAudioCtx()
    if (!ctx) return
    if (ctx.state === 'suspended') await ctx.resume().catch(() => {})
    const gen = speakGenRef.current
    let bytes: Uint8Array
    try {
      const bin = atob(b64)
      bytes = new Uint8Array(bin.length)
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
    } catch { return }
    let buf: AudioBuffer
    try { buf = await ctx.decodeAudioData(bytes.buffer.slice(0) as ArrayBuffer) }
    catch { return }
    if (gen !== speakGenRef.current) return  // a stop/restart happened mid-decode
    const now = ctx.currentTime
    const startAt = Math.max(now, audioPlayHeadRef.current)
    const src = ctx.createBufferSource()
    src.buffer = buf
    src.connect(ctx.destination)
    src.start(startAt)
    audioSourcesRef.current.push(src)
    audioPlayHeadRef.current = startAt + buf.duration
    // When a source ends, drop it; if it was the last one, playback is done.
    src.onended = () => {
      audioSourcesRef.current = audioSourcesRef.current.filter((x) => x !== src)
      if (gen === speakGenRef.current && audioSourcesRef.current.length === 0) setSpeakingTurn(null)
    }
  }

  // Insert a rendered prompt (from the prompt palette) into the composer at the end,
  // then focus so the user can keep editing before sending.
  function insertPrompt(text: string) {
    if (!text) return
    setInput((prev) => (prev ? `${prev}\n${text}` : text))
    requestAnimationFrame(() => composerRef.current?.querySelector<HTMLElement>('.cm-content')?.focus())
  }

  // select-to-quote: insert the highlighted passage into the composer as a quote.
  function quoteToComposer(text: string) {
    const q = text.trim()
    if (!q) return
    const block = q.split('\n').map((l) => `> ${l}`).join('\n')
    setInput((prev) => (prev ? `${prev}\n\n${block}\n\n` : `${block}\n\n`))
    // The composer is a CodeMirror editor (.cm-content), not a <textarea> — focus
    // it so the user can keep typing after quoting a selection.
    composerRef.current?.querySelector<HTMLElement>('.cm-content')?.focus()
  }

  // activity panel: Index tab jumps to a turn by scrolling its node into view.
  const activity = useMemo(() => deriveActivity(turns), [turns])
  function jumpToTurn(turnIndex: number) {
    const node = turnNodes.current.get(turnIndex)
    node?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  // ── side chat (stage 6) ──
  async function openSide() {
    const s = sessionRef.current
    if (!s || sideOpenedRef.current) return
    sideOpenedRef.current = true
    await api.sideOpen(s).catch(() => {})
  }
  async function askSide(question: string) {
    const s = sessionRef.current
    const q = question.trim()
    if (!s || !q || sideBusy) return
    await openSide()
    setSideBusy(true)
    // push the entry FIRST (runId filled once the POST resolves) so streamed
    // frames that arrive before the response have an open entry to attach to.
    setSideMsgs((prev) => [...prev, { q, a: '', runId: '', done: false }])
    try {
      const res = await api.sideTurn(s, q)
      setSideMsgs((prev) => { const i = prev.length - 1; if (i < 0) return prev; const n = [...prev]; if (!n[i].runId) n[i] = { ...n[i], runId: res.run_id }; return n })
    } catch (e) {
      // Don't silently drop the question — keep it visible with an error answer
      // so the user knows the side request failed (and can retry).
      setSideBusy(false)
      setSideMsgs((prev) => { const i = prev.length - 1; if (i < 0) return prev; const n = [...prev]; n[i] = { ...n[i], a: `⚠️ Couldn’t answer: ${(e as Error).message}`, done: true }; return n })
    }
  }

  // resolve a selected agent NAME to its ACP discovery info (provider id +
  // provider_agent), or null if it's a native agent.
  function acpFor(agentName: string): { providerId: string; agent: DiscoveredAgent } | null {
    for (const [providerId, list] of Object.entries(data.discovered ?? {})) {
      const agent = list.find((dd) => dd.name === agentName)
      if (agent) return { providerId, agent }
    }
    return null
  }

  function applySelection(patch: Partial<ComposerValue>) {
    const nextSel = { ...selection, ...patch }
    setSelection(nextSel)
    const s = sessionRef.current
    if (!s) return
    const acp = acpFor(nextSel.agent)
    if (patch.agent) {
      // ACP agents bind via /acp-agent (provider + provider_agent + model);
      // native agents via /agent.
      if (acp) api.setSessionAcpAgent(s, { provider: acp.providerId, provider_agent: acp.agent.provider_agent, model: nextSel.model && nextSel.model !== 'Auto' ? nextSel.model : undefined }).catch(() => {})
      else api.setSessionAgent(s, patch.agent).catch(() => {})
    }
    if (patch.model && !patch.agent) {
      // model-only change: ACP model goes through /acp-agent too (re-bind w/ model)
      if (acp) api.setSessionAcpAgent(s, { provider: acp.providerId, provider_agent: acp.agent.provider_agent, model: patch.model === 'Auto' ? undefined : patch.model }).catch(() => {})
      else api.setSessionModel(s, patch.model === 'Auto' ? '' : patch.model).catch(() => {})
    }
    if (patch.approval) api.setApprovalMode(patch.approval as ApprovalMode, s).catch(() => {})
    if (patch.taskMode) api.setTaskMode(patch.taskMode as TaskMode, s).catch(() => {})
    if (patch.reasoning !== undefined) api.setReasoningEffort(s, patch.reasoning as ReasoningEffort).catch(() => {})
  }

  // TM8: the model proposed a switch out of a restricted mode and the user clicked
  // "Switch to Agent & run it". Flip the session to Agent (UI toggle + backend) and
  // resume the work — the click IS the consent that makes the escalation safe (no
  // silent self-escalation out of a read-only posture). The backend flip is awaited
  // before resending so the continuation turn runs under Agent's gate + framing.
  async function switchToAgentAndRun(continuation: string) {
    setSelection((sel) => ({ ...sel, taskMode: 'agent' }))
    const s = sessionRef.current
    if (s) await api.setTaskMode('agent', s).catch(() => {})
    const text = continuation.trim() || 'Go ahead and do it.'
    await send(text)
  }

  // ── session title actions (#64) ──
  function beginRename() { setRenameVal(title || ''); setRenaming(true) }
  async function commitRename() {
    const s = sessionRef.current
    const v = renameVal.trim()
    setRenaming(false)
    if (!s || !v || v === title) return
    setTitle(v)
    await api.renameSession(s, v).catch(() => {})
  }
  async function regenTitle() {
    const s = sessionRef.current
    if (!s || regenningTitle) return  // guard against concurrent clicks during the AI call
    setRegenningTitle(true)
    try {
      const r = await api.generateTitle(s).catch(() => null)
      if (r?.title) setTitle(r.title)
    } finally {
      setRegenningTitle(false)
    }
  }
  async function copyLink() {
    const s = sessionRef.current
    if (!s) return
    const url = `${location.origin}${location.pathname}#/chat/${encodeURIComponent(s)}`
    try { await navigator.clipboard.writeText(url) } catch { /* clipboard blocked */ }
    setLinkCopied(true)
    window.setTimeout(() => setLinkCopied(false), 1600)
  }
  // Silently prime the next turn with background context — no visible message, no
  // turn triggered; consumed + prepended on the next user send.
  async function briefAgent() {
    const s = sessionRef.current
    if (!s) return
    const content = await promptInput({
      title: 'Brief the agent', type: 'textarea',
      label: 'Background context to prime the next reply (not shown in the transcript)',
      placeholder: 'Paste a spec, reference, or correction…', confirmLabel: 'Add context',
    })
    if (!content?.trim()) return
    try { await api.briefSession(s, content.trim()); setToast('Context added — it primes your next message.') }
    catch (e) { setToast((e as Error).message || 'Failed to add context') }
    window.setTimeout(() => setToast(null), 2600)
  }
  // Set the live session's working directory (agent cwd + memory-partition scope).
  async function setWorkspaceDir() {
    const s = sessionRef.current
    if (!s) return
    const dir = await promptInput({
      title: 'Working directory', label: 'Absolute path for the agent’s working directory',
      placeholder: '/Users/you/project', confirmLabel: 'Set',
    })
    if (dir == null) return
    try { await api.setSessionWorkspaceDir(s, dir.trim()); setToast(dir.trim() ? `Working directory set to ${dir.trim()}` : 'Working directory cleared') }
    catch (e) { setToast((e as Error).message || 'Failed to set working directory') }
    window.setTimeout(() => setToast(null), 2600)
  }

  async function attach(files: File[]) {
    setAttachError(null)
    // Client-side per-filetype pre-check → reject oversize BEFORE uploading a byte,
    // with the same category message the server would give (better UX than a late 413).
    const { precheck } = await import('../lib/chunkedUpload')
    const ok: File[] = []
    const rejected: string[] = []
    for (const f of files) {
      const err = await precheck(f)
      if (err) rejected.push(err)
      else ok.push(f)
    }
    if (rejected.length) setAttachError(rejected.join(' · '))
    if (!ok.length) return
    // Progress rows for large (chunked) files; small files POST in one shot. A shared
    // AbortController lets the user cancel the in-flight upload from a progress row.
    const ctrl = new AbortController()
    uploadAbortRef.current = ctrl
    setUploads(ok.map((f) => ({ name: f.name, pct: 0 })))
    const r = await api.uploadFiles(ok, (idx, p) => {
      setUploads((prev) => prev.map((u, i) => (i === idx ? { ...u, pct: p.pct } : u)))
    }, ctrl.signal).catch(async (e) => {
      // A user cancel is not an error — just clear silently; other failures surface.
      // (abort is named inconsistently across engines — isAbortError normalises it.)
      const { isAbortError } = await import('../lib/chunkedUpload')
      if (!isAbortError(e)) setAttachError((e as Error).message)
      return { paths: [] as string[] }
    })
    uploadAbortRef.current = null
    setUploads([])
    const paths = (r as { paths?: string[] }).paths ?? []
    // Thread uploaded paths into the next send's meta.files (B0) + show them as
    // removable chips alongside @-mentioned files.
    if (paths.length) setAttachedPaths((prev) => [...prev, ...paths.filter((p) => !prev.includes(p))])
  }

  // macOS: interactive region capture → attach the resulting PNG to the next send.
  // The screenshot lands in a server dir already readable by the send path, so we
  // thread its path straight into attachedPaths (same pipeline as an upload result).
  async function captureScreenshot() {
    setAttachError(null)
    try {
      const r = await api.screenshot()
      if (r.error) { setAttachError(r.error); return }
      if (r.path) setAttachedPaths((prev) => (prev.includes(r.path) ? prev : [...prev, r.path]))
      // r.path === '' means the user cancelled the capture — no-op, no error.
    } catch (e) { setAttachError((e as Error).message) }
  }

  const stage = (
    <div className="w-full" style={{ maxWidth: 'var(--content-width)' }}>
      {/* Memory-mode notice: incognito/temporary sessions look identical to a
          normal one otherwise, so surface a subtle reminder above the composer
          that this chat won't be remembered — important before the user types. */}
      {memoryMode !== 'persistent' && (
        <div className="mb-2 flex items-center gap-1.5 text-[0.75rem] text-on-surface-low">
          {memoryMode === 'incognito' ? <EyeOff size={13} className="shrink-0" /> : <Clock size={13} className="shrink-0" />}
          <span>{memoryMode === 'incognito'
            ? 'Incognito — no memory is read or written, and this chat stays out of your history.'
            : 'Temporary — this chat is forgotten when the session ends.'}</span>
        </div>
      )}
      {/* Voice-input failure notice: STT errors (mic denied, no STT model,
          backend failure) otherwise vanish silently after the spinner. */}
      {micError && (
        <div className="mb-2 flex items-center gap-1.5 text-[0.75rem] text-danger">
          <AlertTriangle size={13} className="shrink-0" /><span>{micError}</span>
        </div>
      )}
      {toast && (
        <div className="mb-2 flex items-center gap-1.5 text-[0.75rem] text-on-surface-var">
          <Check size={13} className="shrink-0 text-ok" /><span>{toast}</span>
        </div>
      )}
      {/* Upload rejection (oversize / failure) — persistent + dismissible, since the
          user must act on it (choose a smaller file), unlike the transient micError. */}
      {attachError && (
        <div role="alert" className="mb-2 flex items-start gap-1.5 rounded-md px-2.5 py-1.5 text-[0.75rem]"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}>
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1 break-words">{attachError}</span>
          <button type="button" onClick={() => setAttachError(null)} aria-label="Dismiss"
            className="shrink-0 opacity-70 hover:opacity-100"><X size={12} /></button>
        </div>
      )}
      {/* Large-attachment upload progress (chunked/resumable). Small files POST in
          one shot and never appear here. */}
      {uploads.length > 0 && (
        <div className="mb-2 flex flex-col gap-1 rounded-lg bg-surface-container/60 px-3 py-2">
          {uploads.map((u) => (
            <div key={u.name} className="flex items-center gap-2.5 text-[0.75rem] text-on-surface-var">
              <Loader2 size={13} className="shrink-0 animate-spin text-primary" />
              <span className="max-w-[40%] shrink-0 truncate" title={u.name}>{u.name}</span>
              {/* The bar takes the row's slack (prominent), pct + cancel stay compact —
                  so it reads as one aligned progress control, not scattered bits. */}
              <span className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-high">
                <span className="block h-full rounded-full bg-primary transition-[width] duration-200" style={{ width: `${u.pct}%` }} />
              </span>
              <span className="shrink-0 tabular-nums text-on-surface-low">{u.pct}%</span>
              <button type="button" onClick={() => uploadAbortRef.current?.abort()} aria-label="Cancel upload"
                title="Cancel upload" className="shrink-0 rounded p-0.5 text-on-surface-low hover:text-danger"><X size={13} /></button>
            </div>
          ))}
        </div>
      )}
      <MentionChips paths={[...mentionedFiles, ...attachedPaths]}
        onRemove={(p) => { setMentionedFiles((prev) => prev.filter((x) => x !== p)); setAttachedPaths((prev) => prev.filter((x) => x !== p)) }}
        onOpen={setOpenFile} />
      <KnowledgeChips items={mentionedKnowledge}
        onRemove={(id) => setMentionedKnowledge((prev) => prev.filter((k) => k.id !== id))} />
      <PasteCards blocks={pasteBlocks} onRemove={removePaste} />
      {/* revert-optimize: the optimize rewrite replaces the draft in place, so
          offer a one-click undo back to what the user originally typed. */}
      {preOptimize !== null && (
        <button type="button" onClick={revertOptimize}
          className="mb-2 inline-flex items-center gap-1.5 rounded-pill bg-surface-high px-2.5 py-1 text-[0.75rem] text-on-surface-var hover:bg-surface-highest transition-colors">
          <Repeat size={12} className="shrink-0" /> Optimized — revert to original
        </button>
      )}
      {/* Queued messages (typed mid-stream) — the backend sends them one-by-one as
          each turn finishes; each can be cancelled while still pending. */}
      <QueueStack items={queued}
        onCancel={(id) => { setQueued((prev) => prev.filter((q) => q.id !== id)); const s = sessionRef.current; if (s) api.cancelQueued(s, id).catch(() => {}) }}
        onEdit={(id, content) => {
          // Honest "edit": there's no queue-edit endpoint, so cancel the pending item
          // and drop its text back in the composer for the user to revise + resend
          // (avoids a fake in-place edit that would silently re-queue at the back).
          setQueued((prev) => prev.filter((q) => q.id !== id)); const s = sessionRef.current; if (s) api.cancelQueued(s, id).catch(() => {})
          setInput((cur) => (cur.trim() ? cur : content))
        }} />
      <div className="relative">
        {/* Saved-prompt palette + auto-nudge now live INSIDE the composer's "+"
            menu (onOpenPrompts + plusMenuExtra) — no more chips overlapping the
            composer's top edge. The palette modal still renders here. */}
        {promptPaletteOpen && (
          <PromptPalette
            onInsert={insertPrompt}
            onSend={(t) => { const full = input.trim() ? `${input}\n${t}` : t; setInput(''); void send(full) }}
            onClose={() => setPromptPaletteOpen(false)} />
        )}
        {knowledgePickerOpen && (
          <KnowledgeContextPicker
            attached={mentionedKnowledge}
            onPick={(item) => onMentionKnowledge(item)}
            onRemove={(id) => setMentionedKnowledge((prev) => prev.filter((k) => k.id !== id))}
            onClose={() => setKnowledgePickerOpen(false)} />
        )}
        {started && sessionRef.current && (
          <div className="mb-1 flex justify-center">
            <SessionSkillsReview sessionKey={sessionRef.current} agent={selection.agent || undefined} refreshKey={sessionSkillsEpoch} />
          </div>
        )}
        <ComposerStage ref={composerRef} value={input} onChange={(v) => { setInput(v); if (preOptimize !== null) setPreOptimize(null) }} onSend={() => send()}
          streaming={streaming} onStop={stop} controls={CHAT_CONTROLS} data={data}
          selection={selection} onSelect={applySelection} onAttach={attach} onFocusChange={setComposerFocused}
          onOpenPrompts={() => setPromptPaletteOpen(true)}
          plusMenuExtra={(close) => (
            <>
              <MenuRow icon={<BookText size={16} />} label="Add knowledge" hint="Search the library → attach to the prompt" onClick={() => { close(); setKnowledgePickerOpen(true) }} />
              {isMac && <MenuRow icon={<Camera size={16} />} label="Capture screenshot" hint="Snip a region → attach" onClick={() => { close(); void captureScreenshot() }} />}
              {started && sessionRef.current && <AutoNudgeMenuItem session={sessionRef.current!} onOpen={close} />}
            </>
          )}
          onMentionFile={onMentionFile} onMentionKnowledge={onMentionKnowledge} onLargePaste={onLargePaste}
          openModelSignal={openModelSignal} openAgentSignal={openAgentSignal} openReasoningSignal={openReasoningSignal}
          onOptimize={optimize} optimizing={optimizing} history={promptHistory}
          onTranscribe={transcribe} onMicError={(m) => { setMicError(m); window.setTimeout(() => setMicError(null), 6000) }} canQueue contextPct={contextPct} />
      </div>
      {/* CREATE-TIME session setup — project binding + memory mode. Both are frozen
          once the chat starts, so they are NOT composer controls (the composer's
          controls stay live for the whole chat); they live just below the new-chat
          composer and disappear once started. Keyed off !sessionId (a genuinely NEW
          chat) rather than !started, so they don't flash while an existing session's
          history is still loading (turns empty → started false, but sessionId set).
          Project scoping opens via /project. */}
      {!sessionId && (
        <div className="mt-2.5 flex flex-wrap items-center justify-center gap-x-3 gap-y-2">
          <ProjectPicker value={projectId} onChange={setProjectId}
            emptyLabel="No project" emptyHint="" openSignal={openProjectSignal} />
          <Segmented ariaLabel="Memory mode" size="sm" value={memoryMode}
            options={MEMORY_MODES.map((m) => ({ key: m.id, label: m.label, title: `${m.label} — ${m.hint}` }))}
            onChange={(v) => setMemoryMode(v as MemoryMode)} />
        </div>
      )}
    </div>
  )

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <DotGlow intensity={composerFocused ? 1.6 : 1} composerRef={composerRef} focusRef={glowTargetRef} />

      {/* keepCornerPadding: the chat's docked panels (Activity, File peek) are flex
          siblings BELOW this bar — they never sit over the shell's fixed top-right
          corner. So the header must always reserve the corner clearance, else its
          right cluster slides UNDER the shell controls when a panel is open. */}
      <TopBar
        keepCornerPadding
        left={!started ? (
          // New-chat page: the title area is the body hero (greeting). The chat-history
          // panel opener lives in the RIGHT cluster (panel-opener = rightmost, per the
          // header ordering tenet), not here.
          undefined
        ) : (
          renaming ? (
            <input autoFocus value={renameVal} onChange={(e) => setRenameVal(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); else if (e.key === 'Escape') setRenaming(false) }}
              className="h-8 min-w-[200px] max-w-[420px] rounded-md bg-surface-high px-2 text-on-surface text-[0.9375rem] outline-none" />
          ) : (
            <div className="flex items-center gap-1.5 min-w-0">
              {/* Back to the chat history list — replaces the separate right-side
                  "Chat history" button (it sits left of the title, its natural home). */}
              <IconButton icon={ArrowLeft} label="Back to chat history" size={40} onClick={() => navigate('chat/history')} />
              <button type="button" onClick={beginRename} title="Rename chat"
                className="group inline-flex items-center gap-1.5 min-w-0 max-w-[420px] text-on-surface hover:text-on-surface-var transition-colors">
                <span data-type="title-l" className="truncate">{title || 'Chat'}</span>
                <Pencil size={13} className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
              </button>
              {/* Regenerate title — a small magic-stars affordance hugging the title's
                  top-right edge, not a space-hungry header control. */}
              {sessionRef.current && (
                <button type="button" onClick={regenTitle} disabled={regenningTitle}
                  title="Regenerate title" aria-label="Regenerate title"
                  className="shrink-0 -ml-0.5 self-start inline-flex size-5 items-center justify-center rounded-full text-on-surface-low hover:text-primary hover:bg-surface-high transition-colors disabled:opacity-50">
                  {regenningTitle ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                </button>
              )}
              {/* Project binding stays visible once started — the chat is scoped to this
                  project's workspace + context; click to open the project. */}
              {projectName && (
                <button type="button" onClick={() => navigate(`projects/${projectId}`)}
                  className="inline-flex shrink-0 items-center gap-1 rounded-pill bg-surface-high px-2 py-0.5 text-[0.75rem] text-on-surface-var hover:text-on-surface" title={`Scoped to project: ${projectName}`}>
                  <FolderKanban size={12} className="text-primary" /> {projectName}
                </button>
              )}
              {/* copy chat link — lives next to the title (its subject). */}
              {sessionRef.current && (
                <IconButton icon={linkCopied ? Check : Link2} label={linkCopied ? 'Link copied' : 'Copy chat link'} size={40} onClick={copyLink} />
              )}
            </div>
          )
        )}
        right={
          // Two live mode selectors (Task, Permission) + New chat / Regen / Activity.
          // Task + Permission are hover-expand mode pills (WidthPill idiom): each shows
          // ONLY the current selection at rest and expands to the full option list on
          // hover — so at rest the header spends width on ONE pill per axis, not all N
          // options. They live in the 4-tier cluster (primary, never-overflow) so they
          // collapse to icon-only when tight and always stay visible.
          //
          // Header ordering tenet: the side-panel opener is the RIGHTMOST control. On
          // the new-chat page that's "Chat history"; once started it's "Activity".
          <HeaderActions className="max-w-[70vw]">
            <HeaderModePill ariaLabel="Task mode" value={selection.taskMode ?? 'agent'}
              options={TASK_MODE_SLIDER} onChange={(v) => applySelection({ taskMode: v as TaskMode })} />
            <HeaderModePill ariaLabel="Permission mode" value={selection.approval ?? 'normal'}
              options={APPROVAL_SLIDER} onChange={(v) => applySelection({ approval: v as ApprovalMode })} />
            {started && sessionRef.current && (
              <HeaderControl icon={NotebookPen} label="Brief the agent" priority="low" onClick={briefAgent} />
            )}
            {started && sessionRef.current && (
              <HeaderControl icon={FolderCog} label="Working directory" priority="low" onClick={setWorkspaceDir} />
            )}
            <HeaderControl icon={Edit3} label="New chat" variant="primary" priority="primary" onClick={() => navigate('chat/new')} />
            {started && (
              <HeaderControl icon={PanelRight} label="Activity" active={activityOpen} onClick={() => setActivityOpen(!activityOpen)} />
            )}
            {!started && (
              <HeaderControl icon={History} label="Chat history" active={historyOpen} onClick={() => setHistoryOpen(!historyOpen)} />
            )}
          </HeaderActions>} />

      {/* body row: chat column + (optional) right-docked panels that PUSH the chat
          narrower (flex siblings) — the FILE peek and the ACTIVITY rail. Both use
          the shared SidePanel primitive so they match every other page. */}
      <div className="relative flex min-h-0 flex-1">
        <div className="relative flex min-w-0 flex-1 flex-col">
          {loadingHistory ? (
            // Opening an existing session: paint the chat frame instantly (header +
            // docked composer are already live around this column) and skeleton the
            // message area while history loads — never a bare "Loading…" text.
            <>
              <div className="relative flex-1 overflow-y-auto">
                <MessagesSkeleton />
              </div>
              <div className="relative shrink-0 px-l pb-l">
                <div className="mx-auto flex flex-col items-center" style={{ maxWidth: 'var(--content-width)' }}>
                  {stage}
                </div>
              </div>
            </>
          ) : !started ? (
            <div className="relative flex-1 flex flex-col items-center justify-center px-l">
              <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring.spatialDefault}
                className="flex flex-col items-center gap-l mb-2xl">
                <ClawMark size={44} animated blob />
                <h1 data-type="display-s" className="text-on-surface text-center">{greeting(name)}</h1>
              </motion.div>
              <div className="flex w-full flex-col items-center gap-2xl" style={{ maxWidth: 'var(--content-width)' }}>
                {stage}
                <SuggestionChips onPick={(s) => setInput(s)} />
              </div>
            </div>
          ) : (
            <>
              <div ref={scrollRef} className="relative flex-1 overflow-y-auto">
                <SelectionQuote scrollRef={scrollRef} onQuote={quoteToComposer} />
                <div className="mx-auto flex flex-col gap-2xl px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
                  {turns.map((turn, i) => {
                    const isLast = i === turns.length - 1
                    const turnTextOf = (t: ChatTurn) => t.segments.map((s) => (s.kind === 'text' ? s.text : '')).join('')
                    return (
                      <div key={i} className="relative"
                        ref={(el) => { if (el) turnNodes.current.set(i, el); else turnNodes.current.delete(i) }}>
                        {/* small, fixed, centered glow anchor at the top of the
                            ACTIVE turn — the traveling light targets this stable
                            point (not the growing turn box), so the pool stays
                            compact + symmetric and never expands as text streams. */}
                        {isLast && streaming && (
                          <div ref={glowAnchorRef} aria-hidden className="pointer-events-none absolute left-1/2 -top-2 size-px -translate-x-1/2" />
                        )}
                        {turn.role === 'user' ? (
                          editingTurn === i ? (
                            <UserEditor initial={turnTextOf(turn)} onCancel={() => setEditingTurn(null)} onSubmit={(v) => editResend(i, v)} />
                          ) : (
                            <div className="group/msg">
                              <MessageUser fromComposer={isLast} onFileClick={setOpenFile} pastes={turn.pastes} optimized={turn.optimized}>{turnTextOf(turn)}</MessageUser>
                              {turn.files && turn.files.length > 0 && <TurnAttachments paths={turn.files} onOpenFile={setOpenFile} />}
                              {!streaming && <UserActions text={turnTextOf(turn)} canFork={memoryMode === 'persistent'} onEdit={() => setEditingTurn(i)} onFork={() => forkAt(i)} />}
                            </div>
                          )
                        ) : (
                          <MessageAssistant actions={!(isLast && streaming) && (
                            <AssistantActions text={turnText(turn)} isLast={isLast} canFork={memoryMode === 'persistent'}
                              variantCount={turn.variantCount} variantIdx={turn.variantIdx}
                              onCopy={() => {}} onRegenerate={regenerate} onFork={() => forkAt(i)}
                              onSwitchVariant={isLast ? switchVariant : undefined}
                              speaking={speakingTurn === i} onSpeak={() => speak(turnText(turn), i)} />
                          )}>
                            <AssistantSegments segments={turn.segments} isLast={isLast} messageTs={turn.ts} streaming={isLast && streaming} onApprove={approve} onOption={(t) => send(t)} onSwitchToAgent={switchToAgentAndRun} onOpenFile={setOpenFile} chatSessionKey={sessionRef.current ?? undefined} />
                          </MessageAssistant>
                        )}
                      </div>
                    )
                  })}
                  <AnimatePresence>
                    {streaming && showThinking && (
                      <StreamingIndicator statusText={statusText} activity={latestActivity} />
                    )}
                  </AnimatePresence>
                  <div ref={endRef} />
                  {/* visually-hidden polite live region — narrates streaming
                      lifecycle to screen readers (the glow/Thinking cue is visual-only). */}
                  <div aria-live="polite" className="sr-only">{srAnnounce}</div>
                </div>
              </div>
              <div className="relative shrink-0 px-l pb-l">
                {/* reconnecting cue — the WS dropped; state will re-sync on
                    reconnect (cycle 59), but tell the user the link is down. */}
                <AnimatePresence>
                  {!wsConnected && (
                    <motion.div role="status"
                      initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} transition={spring.spatialFast}
                      className="absolute left-1/2 -top-10 z-20 -translate-x-1/2 inline-flex items-center gap-1.5 rounded-pill border bg-surface/95 px-3 h-8 text-[0.75rem] shadow-md backdrop-blur-md"
                      style={{ color: 'var(--color-warn)', borderColor: 'color-mix(in srgb, var(--color-warn) 40%, transparent)' }}>
                      <Loader2 size={13} className="animate-spin" /> Reconnecting…
                    </motion.div>
                  )}
                </AnimatePresence>
                {/* jump-to-latest pill — appears when scrolled up so streamed
                    content arriving below the fold is one click away. */}
                <AnimatePresence>
                  {scrolledUp && (
                    <motion.button type="button" onClick={() => endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })}
                      aria-label="Jump to latest message"
                      initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} transition={spring.spatialFast}
                      className="absolute left-1/2 -top-2 z-20 -translate-x-1/2 inline-flex items-center gap-1.5 rounded-pill border border-outline-variant/50 bg-surface/95 px-3 h-8 text-on-surface-var text-[0.75rem] shadow-md backdrop-blur-md transition-colors hover:bg-surface-high hover:text-on-surface">
                      <ArrowDown size={13} /> Jump to latest
                    </motion.button>
                  )}
                </AnimatePresence>
                <div className="mx-auto flex flex-col items-center" style={{ maxWidth: 'var(--content-width)' }}>
                  {stage}
                </div>
              </div>
            </>
          )}
        </div>

        {/* A file opened INSIDE this chat: comments route to THIS session (not a
            new one) so the agent the user is already talking to picks up the
            feedback in context. */}
        <AnimatePresenceFilePanel path={openFile} onClose={() => setOpenFile(null)}
          commentTarget={sameSessionTarget((msg) => { send(msg) })} />

        {/* Activity rail — a standard right-docked SidePanel (Index / Files / Links
            / Side), a flex sibling that pushes the chat narrower (not a floating
            overlay). URL-bound: ?activity=1 (Back closes; refresh restores). */}
        <AnimatePresence>
          {activityOpen && started && (
            <SidePanel title="Activity" icon={<Activity size={18} className="text-primary" />} storeKey="chat-activity-w"
              fillHeight urlKey={{ key: 'activity', setQuery }} onClose={() => setActivityOpen(false)}>
              <ChatActivityPanel activity={activity} onJumpTo={jumpToTurn} onOpenFile={setOpenFile} subagents={subagents}
                side={{ msgs: sideMsgs, busy: sideBusy, onAsk: askSide, onOpen: openSide }} />
            </SidePanel>
          )}
        </AnimatePresence>
        {/* New-chat page: a right-docked chat-history panel via the SHARED SidePanel
            primitive (matches Activity/File panels app-wide). Lists recent chats to
            resume + a "View all" link to the full history page. */}
        <AnimatePresence>
          {historyOpen && !started && (
            <SidePanel title="Chat history" icon={<History size={18} className="text-primary" />} storeKey="chat-history-w"
              fillHeight urlKey={{ key: 'history', setQuery }} onClose={() => setHistoryOpen(false)}>
              <ChatHistorySidePanelBody navigate={navigate} onOpen={(key) => navigate(`chat/${key}`)} />
            </SidePanel>
          )}
        </AnimatePresence>
      </div>
      {resultRef && (
        <Modal title={`${resultToolRef.current || 'Tool'} — full result`} onClose={() => setResultRef('')}>
          <div className="flex flex-col gap-2 p-l" style={{ minWidth: 520, maxWidth: 900 }}>
            {resultBody === null ? (
              <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem]"><Loader2 size={13} className="animate-spin" /> Loading…</div>
            ) : (
              <>
                {resultBody.length > 0 && (
                  <div className="text-on-surface-low text-[0.7rem]">{resultBody.length.toLocaleString()} chars</div>
                )}
                <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-3 py-2 font-mono text-on-surface-var text-[0.72rem] leading-relaxed">{resultBody.content}</pre>
              </>
            )}
          </div>
        </Modal>
      )}
    </div>
  )
}

/** File panel wrapper (kept out of the big return for clarity). No `key={path}`:
 *  switching from one file to another while the panel is open swaps content IN
 *  PLACE (FileViewer re-fetches on path change) rather than running a jarring
 *  collapse-and-reexpand. AnimatePresence still animates the true open/close. */
function AnimatePresenceFilePanel({ path, onClose, commentTarget }: { path: string | null; onClose: () => void; commentTarget?: CommentTarget }) {
  return (
    <AnimatePresence>
      {path && <ChatFilePanel path={path} onClose={onClose} commentTarget={commentTarget} />}
    </AnimatePresence>
  )
}

/** Attachment chips shown on a SENT user turn (right-aligned under the bubble),
 *  one per file attached to that turn. Clicking a chip opens a preview modal:
 *  the EXTRACTED content the agent saw (fetched on open) + a button to open the
 *  ORIGINAL file in the file panel. So the user can always see what they attached
 *  and exactly what was fed to the model. */
function TurnAttachments({ paths, onOpenFile }: { paths: string[]; onOpenFile: (p: string) => void }) {
  const [peek, setPeek] = useState<string | null>(null)
  const base = (p: string) => (p.replace(/\/+$/, '').split('/').pop() || p).replace(/^[0-9a-f]{32}_/, '')
  return (
    <div className="mt-1.5 flex flex-wrap justify-end gap-1.5">
      {paths.map((p) => (
        <button key={p} type="button" onClick={() => setPeek(p)} title={`Preview ${base(p)}`}
          className="inline-flex items-center gap-1.5 rounded-pill border border-outline-variant/50 bg-surface-container px-2.5 py-1 text-[0.75rem] text-on-surface-var transition-colors hover:bg-surface-high hover:text-on-surface">
          <Paperclip size={11} className="shrink-0 text-on-surface-low" />
          <span className="max-w-[200px] truncate">{base(p)}</span>
        </button>
      ))}
      {peek && <AttachmentPeekModal path={peek} name={base(peek)} onOpenFile={onOpenFile} onClose={() => setPeek(null)} />}
    </div>
  )
}

/** Preview an attachment: its extracted text content (what the agent saw) +
 *  open-original. Extraction is fetched on open (awaits the upload-time job). */
function AttachmentPeekModal({ path, name, onOpenFile, onClose }: { path: string; name: string; onOpenFile: (p: string) => void; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    setLoading(true)
    api.attachmentExtract(path)
      .then((r) => { if (alive) setText(r.text || '') })
      .catch(() => { if (alive) setText('') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [path])
  return (
    <Modal title={name} icon={<Paperclip size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <button type="button" onClick={() => { onOpenFile(path); onClose() }}
          className="self-start inline-flex items-center gap-1.5 rounded-pill border border-outline-variant/50 px-m h-8 text-[0.8125rem] text-primary hover:bg-surface-high transition-colors">
          <ExternalLink size={14} /> Open original file
        </button>
        <div>
          <div className="mb-1 text-on-surface-low text-[0.7rem] uppercase tracking-wide">Extracted content (what the agent saw)</div>
          {loading ? (
            <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem] py-3"><Loader2 size={14} className="animate-spin" /> Extracting…</div>
          ) : text ? (
            <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-m py-2 font-mono text-on-surface-var text-[0.75rem] leading-relaxed">{text}</pre>
          ) : (
            <p className="text-on-surface-low text-[0.8125rem]">No extractable text content (e.g. an image with no OCR configured).</p>
          )}
        </div>
      </div>
    </Modal>
  )
}

/** Removable attachment cards for large pastes, shown ABOVE the composer. Each
/** Highlighted chips for @-mentioned files, shown ABOVE the composer. Clicking
 *  the chip reveals the FULL path inline (so the user knows exactly which file)
 *  and offers Open (file panel); ✕ removes the attachment. */
function MentionChips({ paths, onRemove, onOpen }: { paths: string[]; onRemove: (p: string) => void; onOpen: (p: string) => void }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  if (!paths.length) return null
  // Uploaded files are saved as `<uuid4-hex>_<original-name>`; strip that
  // collision-avoidance prefix so the chip shows the clean name the user dropped.
  const base = (p: string) => (p.replace(/\/+$/, '').split('/').pop() || p).replace(/^[0-9a-f]{32}_/, '')
  return (
    <div className="mb-2 flex flex-wrap gap-2">
      {paths.map((p) => {
        const open = expanded === p
        return (
          <div key={p} className="flex items-center gap-1.5 rounded-lg border border-primary/40 bg-primary/10 px-2.5 py-1.5 text-[0.8125rem]"
            style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
            <FileText size={13} className="shrink-0 text-primary" />
            <button type="button" onClick={() => setExpanded(open ? null : p)} title={open ? 'Collapse' : 'Show full path'}
              className="min-w-0 text-left font-mono text-on-surface">
              {open ? <span className="break-all">{p}</span> : base(p)}
            </button>
            {open && (
              <button type="button" onClick={() => onOpen(p)} title="Open file"
                className="shrink-0 rounded px-1.5 text-primary text-[0.7rem] hover:bg-surface-high">Open</button>
            )}
            <button type="button" onClick={() => onRemove(p)} aria-label="Remove file"
              className="shrink-0 rounded p-0.5 text-on-surface-low hover:text-danger"><X size={13} /></button>
          </div>
        )
      })}
    </div>
  )
}

/** "Add knowledge to prompt" — a search picker over the library that shows each
 *  result's token cost against a budget, so the user can attach relevant context
 *  without blowing the window. Selecting toggles the item into mentionedKnowledge
 *  (the same pipeline as an @-mention); the backend inlines it at send. */
function KnowledgeContextPicker({ attached, onPick, onRemove, onClose }: {
  attached: { id: string; name: string }[]
  onPick: (item: { id: string; name: string }) => void
  onRemove: (id: string) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState<import('../lib/api').KnowledgeContextResult | null>(null)
  const [loading, setLoading] = useState(false)
  const MAX = 4000
  const attachedIds = new Set(attached.map((a) => a.id))
  useEffect(() => {
    const query = q.trim()
    if (!query) { setRes(null); return }
    let alive = true
    setLoading(true)
    const t = window.setTimeout(() => {
      api.knowledgeSearchForContext(query, MAX).then((r) => { if (alive) setRes(r) }).catch(() => { if (alive) setRes(null) }).finally(() => { if (alive) setLoading(false) })
    }, 250)
    return () => { alive = false; clearTimeout(t) }
  }, [q])
  // Running budget = sum of tokens of currently-attached results the search surfaced.
  const attachedTokens = (res?.results ?? []).filter((r) => attachedIds.has(r.id)).reduce((n, r) => n + r.tokens, 0)
  const pct = Math.min(100, Math.round((attachedTokens / MAX) * 100))
  return (
    <Modal title="Add knowledge to prompt" icon={<BookText size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-m" style={{ minWidth: 420 }}>
        <div className="relative">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-low" />
          <input value={q} onChange={(e) => setQ(e.target.value)} autoFocus placeholder="Search your knowledge library…"
            className="h-10 w-full rounded-lg bg-surface-high pl-9 pr-3 text-[0.9rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        </div>
        {attached.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between text-[0.72rem] text-on-surface-low">
              <span>{attached.length} attached{attachedTokens ? ` · ~${attachedTokens} tokens` : ''}</span>
              {attachedTokens > 0 && <span className="tabular-nums">{pct}% of {MAX}</span>}
            </div>
            {attachedTokens > 0 && (
              <div className="h-1 w-full overflow-hidden rounded-pill bg-surface-high">
                <div className="h-full rounded-pill" style={{ width: `${pct}%`, background: pct > 90 ? 'var(--color-warn)' : 'var(--color-primary)' }} />
              </div>
            )}
          </div>
        )}
        <div className="max-h-[46vh] overflow-y-auto flex flex-col gap-1.5">
          {loading && !res ? <div className="grid place-items-center py-6 text-on-surface-low"><Loader2 size={16} className="animate-spin" /></div>
            : !q.trim() ? <p className="py-6 text-center text-on-surface-low text-[0.82rem]">Type to search notes, gists, bookmarks, docs…</p>
            : (res?.results.length ?? 0) === 0 ? <p className="py-6 text-center text-on-surface-low text-[0.82rem]">No matches for “{q}”.</p>
            : res!.results.map((r) => {
              const on = attachedIds.has(r.id)
              return (
                <button key={r.id} type="button"
                  onClick={() => on ? onRemove(r.id) : onPick({ id: r.id, name: r.title })}
                  className="flex items-start gap-2 rounded-lg px-3 py-2 text-left transition-colors"
                  style={on ? { background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)', outline: '1px solid color-mix(in srgb, var(--color-primary) 40%, transparent)' } : { background: 'var(--color-surface-container)' }}>
                  <span className="mt-0.5 shrink-0 grid size-4 place-items-center rounded border" style={on ? { background: 'var(--color-primary)', borderColor: 'var(--color-primary)', color: 'var(--color-on-primary)' } : { borderColor: 'var(--color-outline-variant)' }}>{on && <Check size={11} />}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 truncate text-on-surface text-[0.85rem]" style={{ fontVariationSettings: '"wght" 500' }}>{r.title}</span>
                      <span className="ml-auto shrink-0 tabular-nums text-on-surface-low text-[0.68rem]">~{r.tokens} tok</span>
                    </div>
                    {r.summary && <div className="mt-0.5 line-clamp-2 text-on-surface-low text-[0.75rem]">{r.summary}</div>}
                  </div>
                </button>
              )
            })}
        </div>
        <div className="flex justify-end"><Button size="sm" onClick={onClose}>Done</Button></div>
      </div>
    </Modal>
  )
}

/** Removable chips for @-mentioned knowledge-library items, shown ABOVE the
 *  composer. Each pairs with an inline `@name` token in the prompt; the item's
 *  content is inlined by the backend at send. ✕ removes the reference. */
function KnowledgeChips({ items, onRemove }: { items: { id: string; name: string }[]; onRemove: (id: string) => void }) {
  if (!items.length) return null
  return (
    <div className="mb-2 flex flex-wrap gap-2">
      {items.map((k) => (
        <div key={k.id} className="flex items-center gap-1.5 rounded-lg border border-primary/40 px-2.5 py-1.5 text-[0.8125rem]"
          style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
          <BookText size={13} className="shrink-0 text-primary" />
          <span className="min-w-0 truncate text-on-surface" title={k.name}>{k.name}</span>
          <button type="button" onClick={() => onRemove(k.id)} aria-label="Remove knowledge reference"
            className="shrink-0 rounded p-0.5 text-on-surface-low hover:text-danger"><X size={13} /></button>
        </div>
      ))}
    </div>
  )
}

/** The mid-stream message queue, shown directly above the composer. Each item is
 *  a message the user sent while a turn was streaming; the backend dispatches them
 *  FIFO as turns finish. A pending item can be cancelled (removes it server-side).
 *  Numbered so the send order is obvious. */
/** P18a — QueueStack: the queued-message deck rendered as PHYSICAL stacked cards
 *  (top = next to run), overlapping with a translateY/scale/opacity depth falloff
 *  (the Toaster/Sonner idiom), depth offsets scaled by `expr()`. Only the TOP card
 *  shows its full text + actions; deeper cards peek behind it, and expanding on hover
 *  fans them out. Reduced-motion / refined expressiveness collapses to a flat list.
 *
 *  Actions are honest to what the backend supports (cancel-only — there is no
 *  reorder/edit endpoint, so we DON'T fake persistent reorder): Cancel removes the
 *  item; Edit cancels it AND drops its text back into the composer to resend (no
 *  false "in-place edit" that would silently move it to the back of the FIFO). */
function QueueStack({ items, onCancel, onEdit }: {
  items: { id: string; content: string }[]
  onCancel: (id: string) => void
  onEdit: (id: string, content: string) => void
}) {
  const reduce = useReducedMotion()
  const [expanded, setExpanded] = useState(false)
  if (!items.length) return null
  // Flat list when reduced-motion OR a small queue (a single card needs no deck).
  const stacked = !reduce && items.length > 1 && !expanded
  // Depth falloff for the collapsed deck: each card behind the top peeks down a few
  // px + shrinks + fades, scaled by expressiveness (refined → a tighter, calmer deck).
  const peekY = expr(7, 0.4)      // px each deeper card drops
  const peekScale = expr(0.04, 0.5)
  const maxPeek = 3               // cards visibly peeking behind the top

  const header = (
    <button type="button" onClick={() => items.length > 1 && setExpanded((e) => !e)}
      className={`flex items-center gap-1.5 px-1 text-[0.6875rem] uppercase tracking-wide text-on-surface-low ${items.length > 1 ? 'hover:text-on-surface-var' : 'cursor-default'}`}>
      <Clock size={11} className="shrink-0" /> {items.length} queued · sent one at a time as each turn finishes
      {items.length > 1 && <ChevronDown size={11} className={`shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />}
    </button>
  )

  const card = (q: { id: string; content: string }, i: number, depth: number) => (
    <motion.div key={q.id} layout
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={stacked
        ? { opacity: depth === 0 ? 1 : Math.max(0, 1 - depth * 0.28), y: -depth * peekY, scale: 1 - depth * peekScale }
        : { opacity: 1, y: 0, scale: 1 }}
      exit={reduce ? undefined : { opacity: 0, y: 8, transition: spring.spatialFast }}
      transition={spring.spatialDefault}
      style={stacked ? { position: depth === 0 ? 'relative' : 'absolute', insetInline: 0, top: 0, zIndex: maxPeek - depth } : undefined}
      className="group/q flex items-center gap-2 rounded-lg border border-outline-variant/50 bg-surface-high/60 px-2.5 py-1.5 text-[0.8125rem]">
      <span className="shrink-0 tabular-nums text-on-surface-low">{i + 1}</span>
      <span className="min-w-0 flex-1 truncate text-on-surface" title={q.content}>{q.content}</span>
      {/* Actions only on the top card in stacked mode (deeper cards are non-interactive peeks). */}
      {(!stacked || depth === 0) && (
        <span className="flex shrink-0 items-center gap-0.5">
          <button type="button" onClick={() => onEdit(q.id, q.content)} aria-label="Edit queued message"
            className="rounded p-0.5 text-on-surface-low opacity-0 transition-opacity hover:text-primary group-hover/q:opacity-100"><Pencil size={12} /></button>
          <button type="button" onClick={() => onCancel(q.id)} aria-label="Cancel queued message"
            className="rounded p-0.5 text-on-surface-low hover:text-danger"><X size={13} /></button>
        </span>
      )}
    </motion.div>
  )

  return (
    <div className="mb-2 flex flex-col gap-1.5">
      {header}
      {stacked ? (
        // Collapsed deck: the top card in flow, up to `maxPeek` cards absolutely
        // stacked behind it. A wrapper reserves height for the peek offset.
        <div className="relative" style={{ paddingTop: Math.min(items.length - 1, maxPeek) * peekY }}>
          {items.slice(0, maxPeek + 1).map((q, i) => card(q, i, i)).reverse()}
        </div>
      ) : (
        <AnimatePresence initial={false}>
          <div className="flex flex-col gap-1.5">{items.map((q, i) => card(q, i, 0))}</div>
        </AnimatePresence>
      )}
    </div>
  )
}

/** Message-area skeleton shown while a chat session's history loads. Alternates a
 *  right-aligned user bubble and a left-aligned assistant block so the shape reads
 *  as a conversation the instant the page paints — no bare "Loading…" text. The
 *  chrome (header + composer) is already live around it; only this area is pending. */
function MessagesSkeleton() {
  const rows = [
    { me: true, w: 'w-1/3' }, { me: false, w: 'w-3/4' },
    { me: true, w: 'w-2/5' }, { me: false, w: 'w-2/3' },
  ]
  return (
    <div className="mx-auto flex flex-col gap-2xl px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}
      role="status" aria-busy="true" aria-label="Loading conversation">
      {rows.map((r, i) => (
        <div key={i} className={`flex flex-col gap-2 ${r.me ? 'items-end' : 'items-start'}`}>
          <Skeleton className={`h-4 ${r.w} ${r.me ? 'max-w-[70%]' : ''}`} />
          {!r.me && <><Skeleton className="h-4 w-11/12" /><Skeleton className="h-4 w-4/5" /></>}
        </div>
      ))}
    </div>
  )
}

/** Removable attachment cards for large pastes, shown ABOVE the composer. Each
 *  pairs with an inline `[Paste #N]` marker in the prompt (so the position is
 *  visible); ✕ removes both. Click to preview the pasted content. */
function PasteCards({ blocks, onRemove }: { blocks: PasteBlock[]; onRemove: (seq: number) => void }) {
  const [preview, setPreview] = useState<PasteBlock | null>(null)
  if (!blocks.length) return null
  return (
    <>
      <div className="mb-2 flex flex-wrap gap-2">
        {blocks.map((b) => (
          <div key={b.id} className="group flex items-center gap-2 rounded-lg border border-outline-variant/50 bg-surface-container px-2.5 py-1.5">
            <Clipboard size={13} className="shrink-0 text-primary" />
            <button type="button" onClick={() => setPreview(b)} className="text-left text-on-surface text-[0.8125rem] hover:underline">
              Paste #{b.seq} <span className="text-on-surface-low">· {b.lines} line{b.lines === 1 ? '' : 's'}</span>
            </button>
            <button type="button" onClick={() => onRemove(b.seq)} aria-label={`Remove paste #${b.seq}`}
              className="shrink-0 rounded p-0.5 text-on-surface-low hover:text-danger"><X size={13} /></button>
          </div>
        ))}
      </div>
      <AnimatePresence>
        {preview && (
          <Modal title={`Paste #${preview.seq} · ${preview.lines} lines`} icon={<Clipboard size={18} className="text-primary" />} onClose={() => setPreview(null)}>
            <pre className="overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-m py-s font-mono text-on-surface-var text-[0.8125rem] leading-relaxed">{preview.content}</pre>
          </Modal>
        )}
      </AnimatePresence>
    </>
  )
}

/** Inline editor for a user turn (Edit & resend). Replaces the bubble with a
 *  right-aligned textarea + Cancel/Resend; ⌘↵ submits, Esc cancels. */
function UserEditor({ initial, onSubmit, onCancel }: { initial: string; onSubmit: (v: string) => void; onCancel: () => void }) {
  const [v, setV] = useState(initial)
  return (
    <div className="flex flex-col items-end gap-2">
      <textarea autoFocus value={v} onChange={(e) => setV(e.target.value)} rows={Math.min(10, v.split('\n').length + 1)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') { e.preventDefault(); onCancel() }
          else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); onSubmit(v) }
        }}
        className="w-full resize-none rounded-2xl bg-surface-container px-5 py-4 text-on-surface text-[16px] leading-relaxed outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50"
        style={{ maxWidth: 452 }} />
      <div className="flex items-center gap-2">
        <button type="button" onClick={onCancel} className="rounded-pill px-3 h-8 text-on-surface-low text-[0.8125rem] hover:bg-surface-high hover:text-on-surface">Cancel</button>
        <button type="button" onClick={() => onSubmit(v)} disabled={!v.trim()}
          className="rounded-pill px-4 h-8 text-[0.8125rem] disabled:opacity-40" style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>Resend</button>
      </div>
    </div>
  )
}

/** Select-to-quote — when the user selects text inside the transcript, float a
 *  "Quote" button near the selection that drops the passage into the composer. */
function SelectionQuote({ scrollRef, onQuote }: { scrollRef: React.RefObject<HTMLDivElement | null>; onQuote: (text: string) => void }) {
  const [pos, setPos] = useState<{ x: number; y: number; text: string } | null>(null)
  const btnRef = useRef<HTMLButtonElement | null>(null)
  useEffect(() => {
    const root = scrollRef.current
    if (!root) return
    const onUp = (e: MouseEvent) => {
      // a click ON the quote button must NOT recompute/clear before its own
      // handler runs — bail so the button's onMouseDown wins.
      if (btnRef.current && e.target instanceof Node && btnRef.current.contains(e.target)) return
      const sel = window.getSelection()
      const text = sel?.toString().trim() ?? ''
      if (!text || !sel || sel.rangeCount === 0) { setPos(null); return }
      const range = sel.getRangeAt(0)
      // only within the transcript
      if (!root.contains(range.commonAncestorContainer)) { setPos(null); return }
      const r = range.getBoundingClientRect()
      const pr = root.getBoundingClientRect()
      // The button is position:absolute inside the SCROLLING root, so its
      // coordinates are content-relative, not viewport-relative. Add scrollLeft/
      // scrollTop or it drifts far from the selection once the transcript scrolls.
      setPos({
        x: r.left - pr.left + root.scrollLeft + r.width / 2,
        y: r.top - pr.top + root.scrollTop - 8,
        text,
      })
    }
    // clear on a fresh mousedown that ISN'T the quote button (don't unmount the
    // button mid-click — that was eating the quote action).
    const onDown = (e: MouseEvent) => {
      if (btnRef.current && e.target instanceof Node && btnRef.current.contains(e.target)) return
      setPos(null)
    }
    document.addEventListener('mouseup', onUp)
    root.addEventListener('mousedown', onDown)
    return () => { document.removeEventListener('mouseup', onUp); root.removeEventListener('mousedown', onDown) }
  }, [scrollRef])
  if (!pos) return null
  return (
    <button ref={btnRef} type="button" onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); onQuote(pos.text); setPos(null); window.getSelection()?.removeAllRanges() }}
      className="absolute z-30 -translate-x-1/2 -translate-y-full inline-flex items-center gap-1.5 rounded-pill bg-surface-highest px-3 h-8 text-on-surface text-[0.8125rem] shadow-lg ring-1 ring-outline-variant/50 hover:bg-surface-high"
      style={{ left: pos.x, top: pos.y }}>
      <Quote size={13} className="text-primary" /> Quote
    </button>
  )
}

/** Render an assistant turn's ordered segments. Text segments get OPTIONS
 *  stripped (→ suggestion chips, last turn only) and referenced file paths
 *  surfaced as clickable chips below the prose. */
function AssistantSegments({ segments, isLast, messageTs, streaming, onApprove, onOption, onSwitchToAgent, onOpenFile, chatSessionKey }: {
  segments: Segment[]; isLast: boolean
  messageTs?: string
  streaming?: boolean
  onApprove: (id: string, action: ApproveAction) => void
  onOption: (text: string) => void
  onSwitchToAgent: (continuation: string) => void
  onOpenFile: (path: string) => void
  chatSessionKey?: string
}) {
  // collect OPTIONS from the turn's text (chips shown only on the last turn)
  const fullText = segments.filter((s) => s.kind === 'text').map((s) => (s as { text: string }).text).join('\n')
  const { options } = parseOptions(fullText)
  // A restricted-mode turn may OFFER a one-click escalation to Agent (TM8).
  const { switchTo } = parseSwitchToAgent(fullText)

  // Transparency signals (what FED the turn / what was LEARNED / telemetry) are
  // pulled OUT of the inline flow and consolidated into one collapsible ledger at
  // the turn footer — holistic, non-intrusive, on demand (not three scattered lines).
  const ledger: { fed?: string; learned?: string; stats?: string } = {}
  for (const s of segments) {
    if (s.kind !== 'activity') continue
    const ak = (s as ActivitySegment).activityKind
    if (ak === 'context') ledger.fed = (s as ActivitySegment).text
    else if (ak === 'learned') ledger.learned = (s as ActivitySegment).text
    else if (ak === 'stats') ledger.stats = (s as ActivitySegment).text
  }
  const hasLedger = Boolean(ledger.fed || ledger.learned || ledger.stats)

  // Render one segment as its own card/line. Tool/approval/error cards carry their
  // OWN leading icon + status glyph, so there is no separate timeline dot+rail (the
  // old dot duplicated each card's ✓ and never centered on the connector). An SDLC
  // tool result becomes a live progress widget; text renders as prose.
  const renderItem = (seg: Segment, i: number): React.ReactNode => {
    if (seg.kind === 'tool') {
      const t = seg as ToolSegment
      // A Code project / Goal Loop created or started from chat renders as a live
      // progress widget (status + stages/sub-goals + activity + cockpit link)
      // instead of a bare tool log line — once its result has landed with an id.
      const sdlc = t.done ? sdlcRefFromTool(t.tool, t.output) : null
      if (sdlc) return <SdlcProgressCard key={seg.id || i} refObj={sdlc} />
      return <ToolCard key={seg.id || i} seg={t} />
    }
    if (seg.kind === 'activity') return <ActivityLine key={i} seg={seg as ActivitySegment} />
    if (seg.kind === 'error') return <ErrorLine key={i} text={(seg as { text: string }).text} />
    if (seg.kind === 'approval') {
      const ap = seg as ApprovalSegment
      return <ApprovalCard key={ap.id || i} seg={ap} onAct={onApprove} />
    }
    if (seg.kind === 'text') {
      // hide the raw [OPTIONS: …] and [SWITCH_TO_AGENT: …] markers from the prose
      const body = parseSwitchToAgent(parseOptions(seg.text).body).body
      return body ? <Markdown key={i} onFileClick={onOpenFile} chatSessionKey={chatSessionKey} messageTs={messageTs} streaming={streaming}>{body}</Markdown> : null
    }
    return null
  }
  const isProcess = (s: Segment) =>
    s.kind === 'tool' || s.kind === 'error' || s.kind === 'approval' ||
    (s.kind === 'activity' && !['context', 'learned', 'stats'].includes((s as ActivitySegment).activityKind || ''))

  // Split the turn into the agent's WORK (tool calls, narration, approvals — up to
  // and including the last process step) and its FINAL ANSWER (trailing text after
  // the last step). Once the turn is complete the work folds into a compact
  // "Worked through N steps" disclosure so the user reads the answer first and
  // opens the intermediate steps only on demand. While streaming, work stays open.
  const processIdxs = segments.flatMap((s, i) => (isProcess(s) ? [i] : []))
  const lastProcessIdx = processIdxs.length ? processIdxs[processIdxs.length - 1] : -1
  const stepCount = processIdxs.length
  const toolNames = [...new Set(
    segments.filter((s, i) => i <= lastProcessIdx && s.kind === 'tool').map((s) => (s as ToolSegment).tool),
  )]
  const workSegs = lastProcessIdx >= 0 ? segments.slice(0, lastProcessIdx + 1) : []
  const finalSegs = (lastProcessIdx >= 0 ? segments.slice(lastProcessIdx + 1) : segments).filter((s) => s.kind === 'text')

  // An SDLC create/start/status segment becomes a LIVE progress card that must stay
  // visible — never buried inside the collapsed "Worked through N steps" disclosure
  // (the card is a living, auto-refreshing widget, not a log line). Pull those out of
  // the work fold + render them at the top level between the work + the final answer.
  const isSdlc = (s: Segment) => s.kind === 'tool'
    && !!(s as ToolSegment).done && !!sdlcRefFromTool((s as ToolSegment).tool, (s as ToolSegment).output)
  const sdlcNodes = segments.filter(isSdlc).map(renderItem).filter(Boolean)
  const workNodes = workSegs.filter((s) => !isSdlc(s)).map(renderItem).filter(Boolean)
  const finalNodes = finalSegs.map(renderItem).filter(Boolean)
  const hasFinal = finalNodes.length > 0
  // Collapse the work only when the turn is done AND produced a final answer to
  // focus on; otherwise show it inline (still streaming, or it ended on a step).
  const collapseWork = !streaming && hasFinal && stepCount > 0

  return (
    <>
      {workNodes.length > 0 && (
        collapseWork
          ? <AgentWork stepCount={stepCount} toolNames={toolNames}>{workNodes}</AgentWork>
          : <div className="flex flex-col gap-1">{workNodes}</div>
      )}
      {/* Live SDLC progress cards stay at the top level, always visible — never
          folded into the collapsed work disclosure. */}
      {sdlcNodes.length > 0 && <div className="flex flex-col gap-1">{sdlcNodes}</div>}
      {finalNodes}

      {hasLedger && <ContextLedger fed={ledger.fed} learned={ledger.learned} stats={ledger.stats} />}

      {/* Agent-driven one-click escalation (TM8): the model proposed a switch out
          of a restricted mode; the user approves with a single click, which flips
          the session to Agent AND runs the continuation. Shown on the last turn
          once it's done (the consent gate that keeps Ask/Plan from self-escalating). */}
      {isLast && !streaming && switchTo !== null && (
        <div className="mt-3">
          <button type="button" onClick={() => onSwitchToAgent(switchTo)}
            className="inline-flex items-center gap-1.5 rounded-pill bg-primary px-4 h-9 text-on-primary text-[0.8125rem] font-medium transition-opacity hover:opacity-90">
            <Bot size={15} strokeWidth={2.2} />
            Switch to Agent &amp; run it
          </button>
        </div>
      )}

      {isLast && options.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {options.map((opt) => (
            <button key={opt} type="button" onClick={() => onOption(opt)}
              className="rounded-pill border border-outline-variant/50 bg-surface-low/40 px-3 h-8 text-on-surface-var text-[0.8125rem] transition-colors hover:border-primary/50 hover:bg-surface-high hover:text-on-surface">
              {opt}
            </button>
          ))}
        </div>
      )}
    </>
  )
}

/** A turn-level error callout — a provider/model rejection (or any failed turn)
 *  rendered as a red note so the turn is never silently blank. */
function ErrorLine({ text }: { text: string }) {
  return (
    <div className="my-1 flex items-start gap-2 rounded-lg px-3 py-2 text-[0.8125rem]"
      style={{ background: 'color-mix(in srgb, var(--color-danger) 10%, transparent)', color: 'var(--color-danger)' }}>
      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
      <span className="min-w-0 whitespace-pre-wrap break-words">{text}</span>
    </div>
  )
}

/** The agent's intermediate work for a completed turn, folded into one compact
 *  disclosure so the FINAL ANSWER leads and the steps that produced it open only
 *  on demand. Summarizes as "Worked through N steps" + the distinct tools used.
 *  Collapsed by default; expanding reveals the original tool/approval/activity
 *  cards unchanged. (Replaces the old dot+rail timeline, whose connector added
 *  little once every step was followed by prose and whose dot ✓ duplicated each
 *  card's own status glyph.) */
function AgentWork({ stepCount, toolNames, children }: { stepCount: number; toolNames: string[]; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const summary = toolNames.length
    ? `${toolNames.slice(0, 3).join(', ')}${toolNames.length > 3 ? ` +${toolNames.length - 3} more` : ''}`
    : ''
  return (
    <div className="mb-1.5">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="group/work flex w-full items-center gap-1.5 rounded-md py-1 text-left text-on-surface-low/85 text-[0.75rem] transition-colors hover:text-on-surface-low">
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={spring.spatialFast} className="shrink-0 opacity-60">
          <ChevronRight size={12} />
        </motion.span>
        <Wrench size={12} className="shrink-0 opacity-70" />
        <span className="shrink-0" style={{ fontVariationSettings: '"wght" 500' }}>
          {open ? 'Hide work' : `Worked through ${stepCount} ${stepCount === 1 ? 'step' : 'steps'}`}
        </span>
        {!open && summary && <span className="min-w-0 truncate text-on-surface-low/60">· {summary}</span>}
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            transition={spring.spatialFast} className="overflow-hidden">
            <div className="mt-1 ml-1.5 flex flex-col gap-1 border-l border-outline-variant/40 pl-3">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** A quiet inline activity line for LIVE progress (status / session / tool steps).
 *  Context-provenance, learning, and telemetry are NOT rendered here — they're
 *  consolidated into the per-turn {@link ContextLedger} footer instead. */
function ActivityLine({ seg }: { seg: ActivitySegment }) {
  return (
    <div className="my-1 flex items-center gap-1.5 text-on-surface-low text-[0.75rem]">
      <Activity size={12} className="shrink-0 opacity-70" /><span>{seg.text}</span>
    </div>
  )
}

/** Holistic per-turn context-transparency footer. Consolidates the three
 *  provenance signals — what context FED the turn (memory/lessons/knowledge/
 *  skills/workflows), what the turn LEARNED & saved (after-turn review), and the
 *  turn TELEMETRY — into one quiet, collapsed-by-default affordance. The
 *  high-signal "learned" flag stays visible even collapsed (so the user always
 *  sees, and can open to undo, what was persisted). On demand, never intrusive. */
function ContextLedger({ fed, learned, stats }: { fed?: string; learned?: string; stats?: string }) {
  const [open, setOpen] = useState(false)
  const fedChars = fed?.match(/([\d,]+)\s*chars/)?.[1] ?? ''
  // "Learned: <text>" → just the text for the expanded row.
  const learnedText = learned?.replace(/^Learned:\s*/i, '').trim() ?? ''
  const summary = open
    ? 'Context & learning'
    : [fed && 'recalled context', learned && 'learned 1', stats && 'telemetry'].filter(Boolean).join(' · ') || 'Turn details'
  return (
    <div className="mt-2 mb-1">
      <button type="button" onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-pill text-on-surface-low/80 text-[0.7rem] transition-colors hover:text-on-surface-low"
        title={open ? 'Hide what fed this turn and what was learned' : 'What fed this turn · what was learned'}>
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={spring.spatialFast} className="shrink-0 opacity-60">
          <ChevronRight size={11} />
        </motion.span>
        <Brain size={11} className="shrink-0 opacity-70" />
        <span>{summary}</span>
        {!open && learned && <Sparkles size={11} className="shrink-0 text-primary/80" />}
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            transition={spring.spatialFast} className="overflow-hidden">
            <div className="mt-1.5 ml-1.5 flex flex-col gap-1.5 border-l border-outline-variant/40 pl-3 text-[0.72rem] text-on-surface-low">
              {fed && (
                <LedgerRow icon={Brain} label="Fed this turn">
                  Recalled relevant context{fedChars ? ` · ${fedChars} chars` : ''} — saved memories, learned lessons, earlier conversation, and episodic history, assembled and prepended to the prompt.
                </LedgerRow>
              )}
              {learned && (
                <LedgerRow icon={Sparkles} label="Learned & saved">
                  <span className="text-on-surface-var">{learnedText || 'A preference was captured.'}</span>
                  {' '}<a href="#/settings/memory" className="text-primary/90 hover:underline">Manage in Memory →</a>
                </LedgerRow>
              )}
              {stats && (
                <LedgerRow icon={Gauge} label="Telemetry">
                  <span className="whitespace-pre-wrap break-words">{stats}</span>
                </LedgerRow>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** One labeled row inside the {@link ContextLedger}. */
function LedgerRow({ icon: Icon, label, children }: { icon: LucideIcon; label: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-1.5">
      <Icon size={11} className="mt-[0.15rem] shrink-0 opacity-70" />
      <div className="min-w-0">
        <span className="font-medium text-on-surface-low/90">{label}:</span>{' '}
        {children}
      </div>
    </div>
  )
}

/** Dedicated sessions LIST page (#/chat/history) — search, manage, open. */
function ChatHistoryPage({ navigate, query, setQuery }: { navigate: (p: string) => void; query: Record<string, string>; setQuery: RouteProps['setQuery'] }) {
  // Instant-paint cache: sessions revalidate often (in-memory, persist:false);
  // folders/tags rarely change so they survive a hard reload (persist:true).
  const { data: cachedSessions, refresh: refreshSessions } = useCachedData<ChatSessionSummary[]>('chat:sessions', () => api.chatSessions().catch(() => []), { persist: false })
  const { data: foldersData, refresh: refreshFolders } = useCachedData<ChatFolder[]>('chat:folders', () => api.chatFolders().catch(() => []), { persist: true })
  const { data: tagsData, refresh: refreshTags } = useCachedData<ChatTag[]>('chat:tags', () => api.chatTags().catch(() => []), { persist: true })
  const folders = foldersData ?? []
  const tags = tagsData ?? []
  // Local optimistic overlay so pin/folder/tag mutations paint instantly; it
  // re-syncs whenever the revalidated cache lands.
  const [optimistic, setSessions] = useState<ChatSessionSummary[] | null>(null)
  useEffect(() => { if (cachedSessions !== undefined) setSessions(cachedSessions) }, [cachedSessions])
  const sessions = optimistic
  // View/filter state rides the URL (PLAN 7 unified-URL pattern, matching every
  // other list page) so the chat list is deep-linkable + reload-stable + back/
  // forward-navigable. All use replace:true — they're filters, not navigation
  // steps, so they update the current entry rather than spamming history.
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [viewRaw, setViewRaw] = useQueryParam(query, setQuery, 'view', 'list', { replace: true })
  const view: 'list' | 'board' = viewRaw === 'board' ? 'board' : 'list'
  const setView = (v: 'list' | 'board') => setViewRaw(v)
  // Session PEEK — clicking a card opens the transcript preview in the standard
  // right side panel first (?peek=<key>, push — Back closes); the panel's expand
  // control is the road into the full chat (#/chat/<key>).
  const [peekKey, setPeekKey] = useQueryParam(query, setQuery, 'peek', '')
  const peekSession = peekKey ? (sessions ?? []).find((s) => s.key === peekKey) ?? null : null
  // Tag filter — a Set persisted as a comma-joined URL value.
  const [tagsRaw, setTagsRaw] = useQueryParam(query, setQuery, 'tags', '', { replace: true })
  const tagFilter = useMemo(() => new Set(tagsRaw.split(',').map((t) => t.trim()).filter(Boolean)), [tagsRaw])
  const setTagFilter = (next: Set<string>) => setTagsRaw([...next].join(','))
  // Origin scope — by default the history shows only the user's OWN chats; goal-loop
  // and code-project worker sessions are hidden behind this filter so they don't
  // bury manual conversations, but stay reachable when the user wants to dive in.
  const [originRaw, setOriginRaw] = useQueryParam(query, setQuery, 'origin', 'manual', { replace: true })
  const origin: 'manual' | 'loop' | 'code' | 'channel' | 'all' =
    originRaw === 'loop' || originRaw === 'code' || originRaw === 'channel' || originRaw === 'all' ? originRaw : 'manual'
  const setOrigin = (o: 'manual' | 'loop' | 'code' | 'channel' | 'all') => setOriginRaw(o)

  const load = useCallback(() => {
    invalidateCache('chat:sessions')
    refreshSessions(); refreshFolders(); refreshTags()
  }, [refreshSessions, refreshFolders, refreshTags])

  // ── Magic re-tag (board view): batch AI re-evaluation of every session's
  // tags. POST starts the backend job; progress arrives over the shared WS
  // (retag_progress/retag_done) and each changed session lands via the same
  // sessions refresh the rest of the page uses — the board repaints live.
  const [retag, setRetag] = useState<RetagJob | null>(null)
  const retagRunning = retag?.status === 'running'
  const retagUpdatedRef = useRef(0)
  useEffect(() => {
    // Hydrate mid-job state on mount/reload so the button reflects reality.
    api.retagStatus().then((j) => { if (j && j.status === 'running') setRetag(j) }).catch(() => {})
  }, [])
  useChatSocket((m: WsMessage) => {
    if (m.type !== 'retag_progress' && m.type !== 'retag_done') return
    const job = m.data as unknown as RetagJob
    setRetag(job)
    // Refresh the list only when a session actually changed (or terminal) so
    // the board re-paints tags in place without a fetch per progress frame.
    const changed = (job.updated ?? 0) !== retagUpdatedRef.current
    retagUpdatedRef.current = job.updated ?? 0
    if (changed || m.type === 'retag_done') { load(); refreshTags() }
    if (m.type === 'retag_done') {
      if (job.status === 'done') notify(`Re-tagged ${job.updated ?? 0} of ${job.total ?? 0} chats`, 'success')
      else if (job.status === 'error') notify(`Re-tagging failed: ${job.error || 'unknown error'}`, 'error')
      else if (job.status === 'cancelled') notify('Re-tagging cancelled', 'info')
    }
  })
  async function startRetag() {
    if (retagRunning) { await api.cancelRetag().catch(() => {}); return }
    if (!(await confirm({
      title: 'Generate tags for all chats?',
      body: 'Every chat is re-read and tags generated: fitting tags added, stale ones corrected, obsolete ones removed. Incognito and temporary chats are never touched.',
      confirmLabel: 'Generate tags',
    }))) return
    try {
      const job = await api.retagAllSessions()
      setRetag(job)
    } catch (e) {
      notify(`Couldn't start re-tagging: ${String((e as Error)?.message || e)}`, 'error')
    }
  }

  const tagById = useMemo(() => { const m: Record<string, ChatTag> = {}; for (const t of tags) m[t.id] = t; return m }, [tags])
  const n = q.trim().toLowerCase()
  const recency = (s: ChatSessionSummary) => Date.parse(s.last_activity_ts || s.last_ts || s.created || '') || 0
  // Full-text conversation search: the local filter (title/key/preview) only sees
  // what the list row carries. For "I remember SAYING X" we also query
  // /api/sessions/search, which scans the persisted JSONL bodies, and union those
  // keys in. Debounced; keys normalized (the search returns dashboard_-prefixed
  // keys, the list uses the stripped form).
  const [contentKeys, setContentKeys] = useState<Set<string> | null>(null)
  // List-view drag-to-folder: the chat key being dragged + the folder group hovered
  // (id, or '' for the ungrouped group → clears the folder). Mirrors the Board's
  // tag drag, reusing setFolder as the drop action.
  const [folderDragKey, setFolderDragKey] = useState<string | null>(null)
  const [overFolder, setOverFolder] = useState<string | null>(null)
  useEffect(() => {
    const query = q.trim()
    if (query.length < 2) { setContentKeys(null); return }
    let alive = true
    const t = window.setTimeout(() => {
      api.sessionsSearch(query).then((rows) => {
        if (!alive) return
        setContentKeys(new Set(rows.map((r) => r.key.replace(/^dashboard[_:]/, ''))))
      }).catch(() => { if (alive) setContentKeys(null) })
    }, 300)
    return () => { alive = false; clearTimeout(t) }
  }, [q])
  const matches = useCallback((s: ChatSessionSummary) => {
    const sOrigin = s.origin ?? 'manual'
    // 'all' shows everything; otherwise the row's origin must match the scope
    // (campaign workers surface under 'all' only — there's no dedicated tab yet).
    if (origin !== 'all' && sOrigin !== origin) return false
    // Query match = local (title/key/preview) OR a backend content hit on this key.
    if (n) {
      const local = `${s.title} ${s.key} ${s.source_label ?? ''} ${s.prompt_preview ?? ''} ${s.last_message ?? ''}`.toLowerCase().includes(n)
      const inContent = contentKeys?.has(s.key) ?? false
      if (!local && !inContent) return false
    }
    if (tagFilter.size && !(s.tags ?? []).some((t) => tagFilter.has(t))) return false
    return true
  }, [n, tagFilter, origin, contentKeys])
  const filtered = (sessions ?? []).filter(matches).slice()
    .sort((a, b) => (Number(!!b.pinned) - Number(!!a.pinned)) || (recency(b) - recency(a)))
  // Per-origin counts for the scope tabs (so the user sees how many loop/code
  // chats exist without switching). Campaign workers fold into the 'all' total.
  const originCounts = useMemo(() => {
    const c = { manual: 0, loop: 0, code: 0, channel: 0, all: 0 }
    for (const s of sessions ?? []) {
      c.all++
      const o = s.origin ?? 'manual'
      if (o === 'manual') c.manual++
      else if (o === 'loop') c.loop++
      else if (o === 'code') c.code++
      else if (o === 'channel') c.channel++
    }
    return c
  }, [sessions])

  async function del(s: ChatSessionSummary) {
    if (!(await confirm({
      title: 'Delete chat?',
      body: `"${s.title || s.key}" and its history will be permanently removed.`,
      danger: true, confirmLabel: 'Delete',
    }))) return
    // Surface a real failure instead of swallowing it — the dialog promised the
    // chat would be "permanently removed", so a silent failure that leaves it in the
    // list (as the old .catch(()=>{}) did) is a lie to the user.
    try {
      await api.deleteChatSession(s.key)
    } catch (e) {
      notify(`Couldn't delete this chat: ${String((e as Error)?.message || e)}`, 'error')
      return
    }
    try { sessionStorage.removeItem(_CHAT_DETAIL_SS + s.key) } catch { /* ignore */ }
    load()
  }
  async function togglePin(key: string, pinned: boolean) {
    setSessions((prev) => prev && prev.map((s) => (s.key === key ? { ...s, pinned } : s)))
    await api.pinChatSession(key, pinned).catch(() => load())
  }
  async function setFolder(key: string, folderId: string | null) {
    setSessions((prev) => prev && prev.map((s) => (s.key === key ? { ...s, folder_id: folderId || '' } : s)))
    await api.setSessionFolder(key, folderId).catch(() => load())
  }
  async function toggleTag(key: string, tagId: string) {
    const s = (sessions ?? []).find((x) => x.key === key)
    const next = new Set(s?.tags ?? [])
    next.has(tagId) ? next.delete(tagId) : next.add(tagId)
    const arr = [...next]
    setSessions((prev) => prev && prev.map((x) => (x.key === key ? { ...x, tags: arr } : x)))
    await api.setSessionTags(key, arr).catch(() => load())
  }
  // Board drag-drop MOVE semantics: the chat leaves the SOURCE column (its tag
  // is removed) and joins the target one (its tag is added). Unrelated tags are
  // untouched — a session tagged A+B dragged out of column A keeps B. Dropping
  // on Untagged removes ONLY the source column's tag (not all tags); dragging
  // out of Untagged just adds the target tag. No-op when nothing changes.
  async function setColumnTag(key: string, toTagId: string | null, fromTagId: string | null) {
    const s = (sessions ?? []).find((x) => x.key === key)
    if (!s) return
    const cur = s.tags ?? []
    const next = new Set(cur)
    if (fromTagId) next.delete(fromTagId)
    if (toTagId) next.add(toTagId)
    const arr = [...next]
    if (arr.length === cur.length && cur.every((t) => next.has(t))) return
    setSessions((prev) => prev && prev.map((x) => (x.key === key ? { ...x, tags: arr } : x)))
    await api.setSessionTags(key, arr).catch(() => load())
  }
  async function createFolder() {
    const name = await promptInput({ title: 'New folder', label: 'Folder name', placeholder: 'e.g. Research', confirmLabel: 'Create' })
    if (!name) return
    await api.createChatFolder(name).catch(() => {})
    load()
  }

  // Group the filtered list by folder for the list view (ungrouped last).
  const byFolder = useMemo(() => {
    const groups: { folder: ChatFolder | null; items: ChatSessionSummary[] }[] = []
    for (const f of folders) groups.push({ folder: f, items: filtered.filter((s) => s.folder_id === f.id) })
    groups.push({ folder: null, items: filtered.filter((s) => !s.folder_id || !folders.some((f) => f.id === s.folder_id)) })
    return groups.filter((g) => g.items.length > 0 || g.folder)
  }, [folders, filtered])

  const card = (s: ChatSessionSummary) => {
    // Scoped right-click actions — reuse the row's existing handlers. Folder
    // assignment appears as flat "Move to …" items (the primitive is single-level).
    const menuItems: ContextMenuItem[] = [
      { icon: <Eye size={15} />, label: 'Peek', onSelect: () => setPeekKey(s.key) },
      { icon: <MessageSquare size={15} />, label: 'Open', onSelect: () => navigate(`chat/${s.key}`) },
      { icon: <Pin size={15} />, label: s.pinned ? 'Unpin' : 'Pin to top', onSelect: () => togglePin(s.key, !s.pinned) },
      ...(s.folder_id ? [{ icon: <Folder size={15} />, label: 'Remove from folder', onSelect: () => setFolder(s.key, null) }] : []),
      ...folders.filter((f) => f.id !== s.folder_id).map((f) => ({ icon: <Folder size={15} />, label: `Move to ${f.name}`, onSelect: () => setFolder(s.key, f.id) })),
      { icon: <Trash2 size={15} />, label: 'Delete', danger: true, onSelect: () => del(s) },
    ]
    return (
    <ContextMenu key={s.key} items={menuItems}>
    {/* Drag transport: setData stays synchronous (must happen inside dragstart);
        the STATE set defers a frame — flushing a re-render while Chrome commits
        the native drag cancels it (instant dragend). select-none so the WHOLE
        card initiates the drag: selectable text (title, badges) is its own
        native drag source and steals the gesture from the wrapper. The grip is
        a pure hover affordance (pointer-events-none). */}
    <div role="button" tabIndex={0} onClick={() => setPeekKey(peekKey === s.key ? '' : s.key)}
      draggable
      onDragStart={(e) => { e.dataTransfer.setData('text/plain', s.key); e.dataTransfer.effectAllowed = 'move'; requestAnimationFrame(() => setFolderDragKey(s.key)) }}
      onDragEnd={() => { setFolderDragKey(null); setOverFolder(null) }}
      className="group relative flex cursor-grab active:cursor-grabbing select-none items-center gap-3 rounded-xl bg-surface-container px-4 py-3 transition-colors hover:bg-surface-high">
      <GripVertical size={13} className="pointer-events-none absolute left-0.5 top-1/2 -translate-y-1/2 text-on-surface-low opacity-0 group-hover:opacity-100 transition-opacity" />
      <span className="grid size-9 shrink-0 place-items-center rounded-lg" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
        <MessageSquare size={17} className="text-primary" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{s.title || s.key}</div>
        <div className="flex items-center gap-1.5 flex-wrap text-on-surface-low text-[0.8125rem]">
          <span>{s.messages} message{s.messages === 1 ? '' : 's'}{s.running ? ' · running' : ''}{s.model ? ` · ${s.model}` : ''}</span>
          {/* Origin chip on worker chats — names the loop / code project and opens its
              cockpit (not the raw chat) so the user dives into context. The 'loop' origin
              covers every non-code kind (general/goal/design), so the label is neutral. */}
          {s.origin && s.origin !== 'manual' && (() => {
            const kind = s.origin === 'code' ? 'code project' : s.origin === 'loop' ? 'loop' : s.origin === 'channel' ? 'channel' : 'campaign'
            const label = s.source_label || s.source_id || kind
            const canOpen = !!s.source_id && (s.origin === 'code' || s.origin === 'loop')
            return (
              <button type="button" disabled={!canOpen}
                onClick={(e) => { e.stopPropagation(); if (canOpen) navigate(`${s.origin === 'code' ? 'code' : 'loops'}/${s.source_id}`) }}
                title={canOpen ? `From ${kind} “${label}” — open its cockpit` : `From a ${kind}`}
                className={`inline-flex items-center gap-1 rounded-pill px-1.5 h-[18px] text-[0.6875rem] transition-colors ${canOpen ? 'hover:brightness-125 cursor-pointer' : 'cursor-default'}`}
                style={{ background: 'color-mix(in srgb, var(--color-secondary) 18%, transparent)', color: 'var(--color-secondary)' }}>
                {s.origin === 'code' ? <CodeIcon size={10} /> : <Target size={10} />}
                {label}
              </button>
            )
          })()}
          {(s.tags ?? []).map((tid) => tagById[tid] && (
            <span key={tid} className="inline-flex items-center rounded-pill px-1.5 h-[18px] text-[0.6875rem]"
              style={{ background: `color-mix(in srgb, ${tagById[tid].color || 'var(--color-primary)'} 18%, transparent)`, color: tagById[tid].color || 'var(--color-primary)' }}>{tagById[tid].name}</span>
          ))}
        </div>
      </div>
      {/* assign folder + tags */}
      <SessionOrgMenu s={s} folders={folders} tags={tags} onSetFolder={setFolder} onToggleTag={toggleTag} />
      <button onClick={(e) => { e.stopPropagation(); togglePin(s.key, !s.pinned) }} aria-label={s.pinned ? 'Unpin chat' : 'Pin chat'} title={s.pinned ? 'Unpin' : 'Pin to top'}
        className={`shrink-0 rounded-md p-1.5 transition-opacity hover:bg-surface-highest ${s.pinned ? 'text-primary opacity-100' : 'text-on-surface-low opacity-0 group-hover:opacity-100'}`}>
        <Pin size={14} className={s.pinned ? 'fill-current' : ''} />
      </button>
      <button onClick={(e) => { e.stopPropagation(); del(s) }} aria-label="Delete chat"
        className="shrink-0 rounded-md p-1.5 text-on-surface-low opacity-0 transition-opacity hover:text-danger group-hover:opacity-100"><Trash2 size={14} /></button>
    </div>
    </ContextMenu>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<span data-type="title-l" className="text-on-surface">Chat history</span>}
        right={<HeaderActions className="max-w-[60vw]">
          <HeaderSegmented ariaLabel="View" value={view}
            options={[{ key: 'list', label: 'List view', icon: ListIcon }, { key: 'board', label: 'Board view (by tag)', icon: Columns3 }]}
            onChange={(v) => setView(v as 'list' | 'board')} />
          {/* Magic re-tag — AI re-evaluates every chat's tags (backend batch job;
              live progress via WS). While running the button shows progress and
              clicking it cancels. Available in BOTH views: tags drive the board's
              columns AND the list's filter chips. */}
          <HeaderControl
            icon={retagRunning ? Loader2 : Sparkles}
            label={retagRunning ? `Generating ${retag?.done ?? 0}/${retag?.total ?? '…'} — click to cancel` : 'Generate Tags'}
            active={retagRunning} priority="default" onClick={startRetag}
            className={retagRunning ? '[&>svg]:animate-spin' : undefined} />
          {/* Direct actions — New chat is primary (kept longest); folder is low.
              Tags are now managed inline on each chat (add/remove from the org
              menu on each card) — no dedicated management surface needed. */}
          <HeaderControl icon={FolderPlus} label="New folder" priority="low" onClick={createFolder} />
          <HeaderControl icon={Edit3} label="New chat" variant="primary" priority="primary" onClick={() => navigate('chat/new')} />
        </HeaderActions>} />
      {/* body row: the chat-history column + (optional) the right-docked
          "Manage folders & tags" rail, a flex sibling that PUSHES the column
          narrower (matches the Activity/Chat-history rails app-wide). */}
      <div className="flex min-h-0 flex-1">
      {/* Shell: search + filter chips stay fixed (shrink-0); only the body scrolls
          (list) or hosts a height-bounded board (board view) — the page itself
          never overflows. */}
      <div className="flex min-w-0 flex-1 min-h-0 flex-col">
        <div className="mx-auto w-full px-l pt-l shrink-0" style={{ maxWidth: 'var(--content-width)' }}>
          {/* Render the search/filter chrome whenever the list is loading OR
              non-empty — so the page paints fully (real controls + skeleton rows
              below) during load, and only the genuine empty-state hides it. */}
          {(sessions === null || sessions.length > 0) && (<>
            {/* Origin scope — only shown once worker chats exist (otherwise the
                history is all-manual and the tabs would be noise). Defaults to the
                user's own chats; loop/code workers live behind their tabs. */}
            {(originCounts.loop > 0 || originCounts.code > 0 || originCounts.channel > 0) && (
              <div className="mb-m">
                <Segmented ariaLabel="Chat origin" value={origin} onChange={(v) => setOrigin(v as typeof origin)}
                  options={[
                    { key: 'manual', label: `Chat Sessions${originCounts.manual ? ` ${originCounts.manual}` : ''}` },
                    ...(originCounts.loop > 0 ? [{ key: 'loop', label: `Loops ${originCounts.loop}` }] : []),
                    ...(originCounts.code > 0 ? [{ key: 'code', label: `Code ${originCounts.code}` }] : []),
                    ...(originCounts.channel > 0 ? [{ key: 'channel', label: `Channels ${originCounts.channel}` }] : []),
                    { key: 'all', label: 'All' },
                  ]} />
              </div>
            )}
            <div className="relative mb-m">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-low pointer-events-none" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search chats — title or anything said" aria-label="Search chats"
                type="search" autoFocus
                onKeyDown={(e) => { if (e.key === 'Escape' && q) { e.preventDefault(); e.stopPropagation(); setQ('') } }}
                className="h-10 w-full rounded-pill bg-surface-high pl-9 pr-9 text-[0.9375rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
              {q && (
                <button type="button" onClick={() => setQ('')} aria-label="Clear search"
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 inline-flex size-6 items-center justify-center rounded-full text-on-surface-low hover:bg-surface-highest hover:text-on-surface">
                  <X size={14} />
                </button>
              )}
            </div>
            {tags.length > 0 && (
              <div className="mb-m flex flex-wrap items-center gap-1.5">
                <span className="text-on-surface-low text-[0.75rem] mr-1">Filter:</span>
                {tags.map((t) => {
                  const on = tagFilter.has(t.id)
                  return (
                    <button key={t.id} type="button" onClick={() => { const nx = new Set(tagFilter); nx.has(t.id) ? nx.delete(t.id) : nx.add(t.id); setTagFilter(nx) }}
                      className="inline-flex items-center gap-1 rounded-pill px-2 h-7 text-[0.75rem] transition-colors"
                      style={on ? { background: `color-mix(in srgb, ${t.color || 'var(--color-primary)'} 22%, transparent)`, color: t.color || 'var(--color-primary)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>
                      <TagIcon size={11} /> {t.name}
                    </button>
                  )
                })}
                {tagFilter.size > 0 && <button type="button" onClick={() => setTagFilter(new Set())} className="text-on-surface-low text-[0.75rem] hover:text-on-surface px-1">Clear</button>}
              </div>
            )}
          </>)}
        </div>

        {sessions === null ? <div className="flex-1 min-h-0"><ListSkeleton rows={6} /></div>
          : sessions.length === 0 ? <div className="flex-1 min-h-0"><EmptyState icon={MessageSquare} title="No chats yet" hint="Start a conversation — your sessions will appear here to search and revisit." action={{ label: 'New chat', onClick: () => navigate('chat/new'), icon: Edit3 }} /></div>
          : filtered.length === 0 ? <div className="flex-1 min-h-0"><EmptyState icon={Search} title="No matches" hint="Try a different search or tag filter." /></div>
          : view === 'board' ? (
            // Board fills the remaining height; columns are height-bounded and
            // each column's list scrolls on its own (kanban shell). Centered +
            // bounded to the shell content-width preset like every other page.
            <div className="flex-1 min-h-0 px-l pb-l">
              <div className="mx-auto h-full w-full" style={{ maxWidth: 'var(--content-width)' }}>
                <TagBoard sessions={filtered} tags={tags} card={card} onMove={setColumnTag} />
              </div>
            </div>
          )
          : (
            <div className="flex-1 min-h-0 overflow-y-auto">
              <div className="mx-auto w-full px-l pb-l flex flex-col gap-l" style={{ maxWidth: 'var(--content-width)' }}>
                {byFolder.map((g) => {
                  // Each folder group (incl. the ungrouped one) is a drop target:
                  // dropping a dragged chat sets its folder (null for ungrouped).
                  const dropId = g.folder?.id ?? ''
                  const isOver = folderDragKey != null && overFolder === dropId
                  return (
                  <div key={g.folder?.id ?? '_ungrouped'}
                    onDragOver={folderDragKey ? (e) => { e.preventDefault(); if (overFolder !== dropId) setOverFolder(dropId) } : undefined}
                    onDragLeave={folderDragKey ? (e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOverFolder((c) => (c === dropId ? null : c)) } : undefined}
                    onDrop={folderDragKey ? (e) => { e.preventDefault(); const k = e.dataTransfer.getData('text/plain') || folderDragKey; if (k) setFolder(k, g.folder?.id ?? null); setOverFolder(null); setFolderDragKey(null) } : undefined}
                    className={`rounded-xl transition-colors ${isOver ? 'bg-primary/10 outline-2 outline-dashed outline-primary/50' : ''}`}>
                    {g.folder && (
                      <div className="mb-2 flex items-center gap-1.5 text-on-surface-var text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 500' }}>
                        <Folder size={14} /> {g.folder.name}
                        <span className="text-on-surface-low">({g.items.length})</span>
                      </div>
                    )}
                    {g.items.length === 0 ? <div className="text-on-surface-low text-[0.8125rem] italic pl-5">{folderDragKey ? 'Drop here to move into this folder' : 'Empty'}</div>
                      : <div className="flex flex-col gap-s">{g.items.map(card)}</div>}
                  </div>
                  )
                })}
              </div>
            </div>
          )}
      </div>
      {/* Session peek — the standard right side panel showing the transcript
          preview; expand navigates into the full chat. Takes precedence over the
          manage rail (one right dock at a time). */}
      <AnimatePresence>
        {peekKey && (
          <SidePanel key={peekKey} title={peekSession?.title || peekKey} icon={<MessageSquare size={18} className="text-primary" />}
            storeKey="chat-peek-w" fillHeight urlKey={{ key: 'peek', setQuery }}
            onExpand={() => navigate(`chat/${peekKey}`)}
            onClose={() => setPeekKey('')}>
            <SessionPeekBody sessionKey={peekKey} onOpen={() => navigate(`chat/${peekKey}`)} />
          </SidePanel>
        )}
      </AnimatePresence>
      </div>
    </div>
  )
}

/** Per-session folder + tag assignment menu (hover-revealed in a row). */
function SessionOrgMenu({ s, folders, tags, onSetFolder, onToggleTag }: {
  s: ChatSessionSummary; folders: ChatFolder[]; tags: ChatTag[]
  onSetFolder: (key: string, folderId: string | null) => void; onToggleTag: (key: string, tagId: string) => void
}) {
  return (
    <div onClick={(e) => e.stopPropagation()}>
      {/* align right + placement bottom: the trigger sits near the card's right
          edge in a downward-scrolling list, so the menu must extend leftward
          (inward — a left-aligned flyout spills past the card and forces a
          horizontal scrollbar) and downward (the default upward placement clips
          off-screen for cards near the top of the list). portal: the menu is
          used inside overflow-clipping containers (board columns, the list
          scroller) — inline absolute positioning gets CLIPPED at their edges;
          the body portal escapes them (and closes on scroll, see Popover). */}
      <Popover width={240} align="right" placement="bottom" portal trigger={(open, toggle) => (
        <button type="button" onClick={toggle} aria-label="Organize chat" title="Folder & tags"
          className={`shrink-0 rounded-md p-1.5 transition-opacity hover:bg-surface-highest ${open ? 'text-primary opacity-100' : 'text-on-surface-low opacity-0 group-hover:opacity-100'}`}>
          <TagIcon size={14} />
        </button>
      )}>
        {() => (
          <div className="max-h-[320px] overflow-y-auto py-1">
            {folders.length > 0 && <div className="px-m pt-1 pb-0.5 text-[0.7rem] uppercase tracking-wide text-on-surface-low">Folder</div>}
            {folders.length > 0 && (
              <MenuRow label="— none —" selected={!s.folder_id} onClick={() => onSetFolder(s.key, null)} />
            )}
            {folders.map((f) => <MenuRow key={f.id} label={f.name} icon={<Folder size={14} />} selected={s.folder_id === f.id} onClick={() => onSetFolder(s.key, f.id)} />)}
            {tags.length > 0 && <div className="px-m pt-2 pb-0.5 text-[0.7rem] uppercase tracking-wide text-on-surface-low">Tags</div>}
            {tags.map((t) => (
              <MenuRow key={t.id} label={t.name} selected={(s.tags ?? []).includes(t.id)} onClick={() => onToggleTag(s.key, t.id)} />
            ))}
            {folders.length === 0 && tags.length === 0 && <div className="px-m py-2 text-[0.8125rem] text-on-surface-low">Create a folder or tag first.</div>}
          </div>
        )}
      </Popover>
    </div>
  )
}


/** Kanban board: one column per tag (+ untagged), each holding the matching
 *  sessions. Designed as a fixed SHELL (mirrors the Tasks board): the board fills
 *  the available height; columns flow in a responsive grid (auto-fit, min 260px)
 *  and share the height equally when they wrap, so no column hides off-screen.
 *  Only each column's session list scrolls independently — the page never
 *  overflows horizontally or vertically. Read+navigate (reuses the list card). */
function TagBoard({ sessions, tags, card, onMove }: {
  sessions: ChatSessionSummary[]; tags: ChatTag[]; card: (s: ChatSessionSummary) => React.ReactNode
  /** Drop a chat onto a column — MOVE semantics: toTagId is the target column's
   *  tag (null for Untagged), fromTagId the column it was dragged out of. */
  onMove?: (key: string, toTagId: string | null, fromTagId: string | null) => void
}) {
  const [dragKey, setDragKey] = useState<string | null>(null)
  // The source COLUMN of the in-flight drag — the drop handler only knows the
  // target, but move semantics need to remove the source column's tag too.
  // A ref (not state): it must be readable at drop time without re-rendering.
  const dragFromCol = useRef<string | null>(null)
  const [overCol, setOverCol] = useState<string | null>(null)
  const collapse = useBoardCollapse('board-collapsed:chat')
  const columns: { id: string; tagId: string | null; label: string; color?: string; items: ChatSessionSummary[] }[] = [
    ...tags.map((t) => ({ id: t.id, tagId: t.id, label: t.name, color: t.color, items: sessions.filter((s) => (s.tags ?? []).includes(t.id)) })),
    { id: '_untagged', tagId: null, label: 'Untagged', items: sessions.filter((s) => !(s.tags ?? []).length) },
  ]
  // Empty columns AUTO-collapse to a slim rail so the populated column(s) get
  // real width; any column can be manually collapsed/expanded (header chevron
  // / click the rail; localStorage-persisted). Rails stay collapsed during a
  // drag and are themselves drop targets. Shared mechanism with the Tasks
  // board (ui/BoardCollapse) — the template is a pure function of data +
  // stored preference, never of drag state (a mid-drag DOM restructure makes
  // Chrome cancel the native drag).
  const template = boardGridTemplate(columns.map((c) => collapse.isCollapsed(c.id, c.items.length)))
  return (
    <div
      className="grid h-full gap-m overflow-x-auto"
      style={{ gridTemplateColumns: template, gridAutoRows: 'minmax(180px, 1fr)' }}
    >
      {columns.map((c) => {
        const collapsed = collapse.isCollapsed(c.id, c.items.length)
        const dropStyle = {
          background: overCol === c.id
            ? 'color-mix(in srgb, var(--color-primary) 12%, transparent)'
            : 'color-mix(in srgb, var(--color-surface-container) 40%, transparent)',
          outline: overCol === c.id ? '1.5px dashed color-mix(in srgb, var(--color-primary) 60%, transparent)' : 'none',
        }
        const dropHandlers = onMove ? {
          onDragOver: (e: React.DragEvent) => { e.preventDefault(); if (overCol !== c.id) setOverCol(c.id) },
          onDragLeave: (e: React.DragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOverCol((p) => p === c.id ? null : p) },
          onDrop: (e: React.DragEvent) => { e.preventDefault(); const k = e.dataTransfer.getData('text/plain') || dragKey; if (k) onMove(k, c.tagId, dragFromCol.current); setOverCol(null); setDragKey(null); dragFromCol.current = null },
        } : {}
        if (collapsed) {
          // Shared slim rail (ui/BoardCollapse): tag icon, count, rotated label.
          // The rail itself is the drop target — it highlights on dragover
          // (dropStyle); an auto-collapsed (empty) rail expands naturally once
          // a drop moves a chat in, a user-collapsed one stays (count ticks
          // up). Clicking the rail re-expands.
          return (
            <CollapsedBoardColumn key={c.id} icon={TagIcon} label={c.label} count={c.items.length}
              tone={c.color} style={dropStyle} {...dropHandlers}
              onExpand={() => collapse.toggle(c.id, c.items.length)} />
          )
        }
        return (
          <div key={c.id} className="flex min-h-0 flex-col rounded-xl p-2 transition-colors" style={dropStyle} {...dropHandlers}>
            <div className="mb-2 flex items-center gap-1.5 px-1 pt-1 shrink-0 text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550', color: c.color || 'var(--color-on-surface)' }}>
              <TagIcon size={13} /> <span className="truncate flex-1">{c.label}</span> <span className="text-on-surface-low tabular-nums">{c.items.length}</span>
              <CollapseColumnButton onCollapse={() => collapse.toggle(c.id, c.items.length)} />
            </div>
            <div className="flex flex-1 min-h-0 flex-col gap-s overflow-y-auto pr-0.5">
              {c.items.length === 0
                ? <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-outline-variant/30 py-6 text-on-surface-low text-[0.75rem]">{onMove ? 'Drop a chat here' : 'No chats'}</div>
                : c.items.map((s) => onMove
                  // setData stays synchronous (required inside dragstart); the
                  // STATE set defers a frame — flushing a re-render while Chrome
                  // is still committing the native drag cancels it (instant
                  // dragend). select-none: selectable text is its own native
                  // drag source and steals the gesture from the wrapper, so the
                  // whole card (title, badges, anywhere) must be unselectable
                  // for the whole card to initiate the drag.
                  ? <div key={s.key} draggable
                      onDragStart={(e) => { e.dataTransfer.setData('text/plain', s.key); e.dataTransfer.effectAllowed = 'move'; dragFromCol.current = c.tagId; requestAnimationFrame(() => setDragKey(s.key)) }}
                      onDragEnd={() => { setDragKey(null); setOverCol(null); dragFromCol.current = null }}
                      className={`select-none cursor-grab active:cursor-grabbing ${dragKey === s.key ? 'opacity-40' : ''}`}>{card(s)}</div>
                  : card(s))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** Auto-nudge entry for the composer "+" menu — a MenuRow that reflects the
 *  on/off state and opens the config in a Modal. Arms / edits / stops a reactive
 *  same-session loop (when a turn finishes and the user is idle for idle_secs,
 *  the service re-injects `message` into THIS session; survives reload/restart).
 *  On by default; PERSONALCLAW_AUTONUDGE=0 disables it (then the modal says so).
 *  `onOpen` closes the parent "+" menu when the row is chosen. */
function AutoNudgeMenuItem({ session, onOpen }: { session: string; onOpen: () => void }) {
  const [open, setOpen] = useState(false)
  const [enabled, setEnabled] = useState(true)
  const [loop, setLoop] = useState<NudgeLoop | null>(null)
  const [msg, setMsg] = useState('')
  const [idle, setIdle] = useState(60)
  const [maxCycles, setMaxCycles] = useState(0)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.autonudgeGet(session).then((r) => {
      setEnabled(r.enabled)
      setLoop(r.loop)
      if (r.loop) { setMsg(r.loop.message); setIdle(r.loop.idle_secs); setMaxCycles(r.loop.max_cycles) }
    }).catch(() => setEnabled(false))
  }, [session])
  useEffect(load, [load])

  async function arm() {
    if (!msg.trim() || busy) return
    setBusy(true)
    try {
      if (loop) await api.autonudgeUpdate(loop.id, { message: msg.trim(), idle_secs: idle, max_cycles: maxCycles })
      else await api.autonudgeStart({ session_name: session, message: msg.trim(), idle_secs: idle, max_cycles: maxCycles })
      load(); setOpen(false)
    } finally { setBusy(false) }
  }
  async function stop() {
    if (!loop || busy) return
    setBusy(true)
    try { await api.autonudgeDelete(loop.id); setLoop(null) } finally { setBusy(false) }
  }

  return (
    <>
      <MenuRow icon={<Repeat size={16} />} label="Auto-nudge" hint={loop?.active ? 'On — keeps this chat working when idle' : 'Keep this chat working when idle'}
        onClick={() => { onOpen(); load(); setOpen(true) }} />
      {open && (
        <Modal title="Auto-nudge" icon={<Repeat size={18} className="text-primary" />} onClose={() => setOpen(false)}>
          <div className="flex flex-col gap-2">
            {!enabled ? (
              <p className="text-[0.8125rem] text-on-surface-low">Disabled on this server (<code className="font-mono">PERSONALCLAW_AUTONUDGE=0</code>).</p>
            ) : (<>
              <p className="text-[0.8125rem] text-on-surface-low">When a turn finishes and you're idle, this message is re-injected into this chat to keep it working on its own.</p>
              <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={3} autoFocus
                placeholder="e.g. Continue toward the goal; if done, write a summary and stop."
                className="w-full rounded-md bg-surface-high px-2 py-1.5 text-on-surface text-[0.8125rem] outline-none resize-y" />
              <div className="flex items-center gap-3 text-[0.8125rem] text-on-surface-var">
                <label className="flex items-center gap-1">Idle
                  <input type="number" min={15} value={idle} onChange={(e) => setIdle(Number(e.target.value))} className="w-16 rounded bg-surface-high px-1.5 py-0.5 text-on-surface outline-none" />s</label>
                <label className="flex items-center gap-1">Max cycles
                  <input type="number" min={0} value={maxCycles} onChange={(e) => setMaxCycles(Number(e.target.value))} className="w-14 rounded bg-surface-high px-1.5 py-0.5 text-on-surface outline-none" /></label>
              </div>
              {loop && <p className="text-[0.75rem] text-on-surface-low">Active · {loop.cycle_count} cycle(s) fired{loop.max_cycles ? ` / ${loop.max_cycles}` : ''}.</p>}
              <div className="flex justify-end gap-2 mt-1">
                {loop && <Button variant="ghost" size="sm" onClick={stop} disabled={busy}><X size={14} /> Stop</Button>}
                <Button size="sm" onClick={arm} disabled={busy || !msg.trim()}><Check size={14} /> {loop ? 'Update' : 'Arm'}</Button>
              </div>
            </>)}
          </div>
        </Modal>
      )}
    </>
  )
}


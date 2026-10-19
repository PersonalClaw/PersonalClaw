import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ResultAnnouncement } from '../ui/ListControls'
import { failureSentence, reportActionFailure, reportingWrite } from '../app/reportingWrite'
import { unavailableWhen, BUSY_REASON } from '../ui/unavailable'

import { fvs, withWeight } from '../design/fontWeight'
import { motion, AnimatePresence } from 'framer-motion'
import { Edit3, History, Search, MessageSquare, Trash2, Activity, PanelRight, Pin, Pencil, Sparkles, Link2, Folder, FolderPlus, Tag as TagIcon, Columns3, List as ListIcon, Filter, Loader2, Target, Code2 as CodeIcon, ArrowLeft, ArrowRight, ArrowUp, GripVertical, Bot, ShieldCheck, Shield, Eye, Zap, ClipboardList, Hammer, NotebookPen, FolderCog, Archive, ArchiveRestore, Download, Share2, ListTree, Send } from 'lucide-react'
import { IconButton } from '../ui/IconButton'
import { SquareIconButton } from '../ui/SquareIconButton'
import { SearchField } from '../ui/SearchField'
import { TopBar } from '../ui/TopBar'
import { SidePanel } from '../ui/SidePanel'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/forms'
import { Segmented } from '../ui/Segmented'
import { ContextMenu, type ContextMenuItem } from '../ui/motion'
import { HeaderActions, HeaderControl, HeaderSegmented, HeaderModePill } from '../ui/HeaderActions'
import { ClawMark } from '../ui/ClawMark'
import { CollapseColumnButton, CollapsedBoardColumn, boardGridTemplate, useBoardCollapse } from '../ui/BoardCollapse'
import { type RoutingSuggestion } from './chat/RoutingChip'
import { workingDirectoryPrompt, workingDirectoryToast } from './chat/workingDirectory'
import { sessionRowMeta } from './chat/sessionRowMeta'
import { StartedByApp } from './chat/StartedByApp'
import { FromChannel } from './chat/FromChannel'
import { chatContextChips } from './chat/ChatContextLine'
import { chatFindPath, searchCoverage, searchSourceLabel } from './chat/searchDeepLink'
import { DotGlow } from '../ui/DotGlow'
import { EmptyState, ListSkeleton, LoadError, Skeleton, LoadingStatus } from '../ui/ListScaffold'
import { WindowedList } from '../ui/WindowedList'
import { FieldError } from '../ui/forms'
import { Spark } from '../ui/Spark'
import { Markdown } from '../ui/Markdown'
import { InlineError } from '../ui/InlineError'
import { PartialNotice } from '../ui/PartialNotice'
import { NeedsModelNote } from './chat/NoModelSetupState'
import { onToolResultFull } from './chat/toolResultBridge'
import { withoutFence } from '../lib/untrustedFence'
import { RoomView } from './chat/RoomView'
import { RoomsScope } from './chat/RoomsScope'
import { ChatFilePanel } from './chat/ChatFilePanel'
import { sameSessionTarget, type CommentTarget } from '../ui/content/commentTarget'
import { ChatActivityPanel } from './chat/ChatActivityPanel'
import { parseOptions, parseSwitchToAgent } from './chat/parseAssistant'
import { Modal } from '../ui/Modal'
import { confirm, promptInput } from '../ui/dialog'
import { deriveActivity } from './chat/chatTypes'
import { useIdentity, firstNameOf } from '../app/identity'
import { notify } from '../app/appSdk'
import { spring, stagger, listItemEnter, expr } from '../design/motion'
import { api, isSwitchedOff, type ApprovalMode, type TaskMode, type ChatSessionSummary, type ChatHistoryMsg, type ChatFolder, type ChatTag, type RetagJob, type SessionTemplate, type ChannelRuntime, type SessionSearchAnswer } from '../lib/api'
import { refreshKinds, useChatSocket, type WsMessage } from '../lib/useChatSocket'
import { useChatVoice } from './chat/useChatVoice'
import { useChatComposer } from './chat/composer/useChatComposer'
import { ChatComposer } from './chat/composer/ChatComposer'
import { useChatStream, detailKey } from './chat/useChatStream'
import { useTranscript } from './chat/transcript/useTranscript'
import { Transcript } from './chat/transcript/Transcript'
import { MessagesSkeleton } from './chat/transcript/MessagesSkeleton'
import { SessionMapReturnLatest, scrollToLatest } from './chat/SessionMapReturnLatest'
import { SessionMapDrawer } from './chat/SessionMapDrawer'
import { useQuery, invalidateKeys } from '../lib/data'
import { downloadFrom } from '../lib/download'
import { sessionRecencyMs, sessionActivitySeconds, epochSeconds } from '../lib/epoch'
import { sessionTitle } from '../lib/sessionTitle'
import { Popover, MenuRow } from '../ui/Popover'
import { useQueryFlag, useQueryParam, type RouteProps } from '../app/useQueryState'
import { copyText } from '../app/clipboard'

// Options for the chat-header segmented controls. Permission mirrors the
// composer's approval modes; memory mirrors MEMORY_MODES — both as the canonical
// Segmented slider rather than a menu.
// `title` carries the same explanatory hint the composer's ApprovalPill shows,
// so hovering a header tab (now the primary approval control) tells the user
// what e.g. "YOLO" or "Plan" actually does rather than just its name.
const APPROVAL_SLIDER = [
  { key: 'normal', label: 'Normal', icon: Shield, title: 'Normal — a tool that only reads runs; the rest ask when they need approval' },
  { key: 'trust_reads', label: 'Trust reads', icon: Eye, title: 'Trust reads — read-only shell commands run without asking too' },
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
 *  sends) rather than firing immediately. Silent when none are available. While no model is
 *  chosen for the chores the list is the standard one, and the line under it says why and links
 *  to where one is chosen (`needs_model`). */
function SuggestionChips({ onPick }: { onPick: (s: string) => void }) {
  // No `.catch`: this key is PERSISTED, so a swallowed rejection wrote a fabricated `[]` into
  // sessionStorage as though it were an answer, and the next visit painted "no suggestions" from
  // cache. Without it the rejection leaves `data` undefined and nothing is cached. The strip still
  // hides on failure, which is honest — a decoration that quietly does not appear claims nothing.
  const { data, loading } = useQuery('chat:suggestions', () => api.suggestions(), { persist: true })
  const items = (data?.suggestions ?? []).slice(0, 6)
  const needsModel = data?.needs_model ?? ''
  // The read answers at once with the list there is; a new one is written in the background and
  // lands as a `suggestions` refresh hint, so the strip re-reads then.
  useChatSocket((m) => { if (refreshKinds(m).includes('suggestions')) invalidateKeys('chat:suggestions') },
    () => invalidateKeys('chat:suggestions'))
  // 🪤 "STILL ASKING" IS NOT "NONE AVAILABLE", and this strip used to render `null` for both. The
  // docstring's "silent when none are available" is a deliberate product choice about the EMPTY
  // answer; it was never meant to cover the pending one. Collapsing the two is expensive here for a
  // reason specific to this hero: it is vertically CENTERED, so the strip arriving does not push
  // content down, it moves the mark, the greeting and the composer ALL of them, by half the strip's
  // height. `/api/suggestions` answers at once now, but a busy gateway can still answer late, and
  // nothing on screen would say a read is open.
  //
  // Measured on the e2e harness at 398e6b7a6: ONE `toHaveScreenshot` call on `#/chat` produced two
  // consecutive screenshots differing by 501,409 pixels — 54% of the image — so the route never
  // reached rest and yielded no verdict about its baseline at all. Gating on `loading` (the flag
  // `useQuery` documents for exactly this) puts a `.skeleton` on screen, which is what
  // `e2e/helpers.ts`'s `LOADING_SELECTOR` counts, so the settle barrier waits for the strip instead
  // of photographing the hero mid-flight. The settled render is unchanged, so no baseline moves.
  //
  // The placeholder mirrors the real strip's geometry — same flex container, same `maxWidth`, pill
  // heights matching the chips' `py-2` + `text-[0.8125rem]` box — so the swap is a text change
  // inside a stable layout rather than a second reflow of everything above it.
  if (loading && !data) {
    return (
      <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }} role="status" aria-busy="true">
        <LoadingStatus what="prompt suggestions" />
        {['w-[132px]', 'w-[104px]', 'w-[168px]', 'w-[148px]', 'w-[120px]', 'w-[156px]'].map((w) => (
          <Skeleton key={w} className={`h-[37px] ${w} rounded-pill`} />
        ))}
      </div>
    )
  }
  if (!items.length) return null
  return (
    <div className="flex flex-col items-center gap-s">
      <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }}>
        {items.map((s, i) => (
          <motion.button key={i} type="button" onClick={() => onPick(s)}
            initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: 0.04 * i }}
            className="rounded-pill border border-outline-variant/60 bg-surface-container px-3.5 py-2 text-left text-[0.8125rem] text-on-surface-var transition-colors hover:border-primary/40 hover:bg-surface-high hover:text-on-surface">
            {s}
          </motion.button>
        ))}
      </div>
      {needsModel && <NeedsModelNote sentence={needsModel} />}
    </div>
  )
}

/** Saved starters on the new-chat screen.
 *
 *  Picking one PREFILLS the composer selection (and the prompt, if the template has
 *  one) instead of creating a session server-side. A server-side
 *  `create_from_template() -> session_key` was considered, but this page mints a session lazily on
 *  first send — a second server-side creation path would mean two ways a session comes
 *  into existence, and an abandoned starter would leave an empty chat behind. Prefilling
 *  reuses the one `ensureSession` path, so a starter the user opens and walks away from
 *  costs nothing. */
function StarterChips({ onPick }: { onPick: (t: SessionTemplate) => void }) {
  // Same as the suggestion strip: persisted key, so the swallow cached a fabricated empty list.
  const { data, loading } = useQuery('chat:starters', () => api.sessionTemplates(), { persist: true })
  const items = (data ?? []).slice(0, 6)
  // Same pending-vs-empty split as the suggestion strip directly above, and it belongs here too even
  // though a fresh install answers `[]`: this strip sits ABOVE the suggestion strip in the same
  // centered hero, so a read that resolves late moves the same four elements. Gating on `loading`
  // keeps the two strips' waiting states consistent — the settle barrier sees ONE page that is still
  // reading rather than a page that is at rest between two arrivals. An install that genuinely has no
  // starters still renders nothing once the read lands, so the settled baseline is unchanged.
  if (loading && !data) {
    return (
      <div className="flex w-full flex-col items-center gap-2" role="status" aria-busy="true">
        <LoadingStatus what="your starters" />
        <Skeleton className="h-4 w-24" />
        <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }}>
          {['w-[146px]', 'w-[118px]', 'w-[162px]'].map((w) => (
            <Skeleton key={w} className={`h-[37px] ${w} rounded-pill`} />
          ))}
        </div>
      </div>
    )
  }
  if (!items.length) return null
  return (
    <div className="flex w-full flex-col items-center gap-2">
      <p className="text-[0.75rem] text-on-surface-low">Your starters</p>
      <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }}>
        {items.map((t, i) => (
          <motion.button key={t.id} type="button" onClick={() => onPick(t)}
            title={t.first_prompt || t.name}
            initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: 0.04 * i }}
            className="flex items-center gap-2 rounded-pill border border-primary/30 bg-primary-container/30 px-3.5 py-2 text-left text-[0.8125rem] text-on-surface-var transition-colors hover:border-primary/60 hover:bg-primary-container/50 hover:text-on-surface">
            <Sparkles size={13} className="shrink-0 text-primary" />
            {t.name}
          </motion.button>
        ))}
      </div>
    </div>
  )
}

/** Relative-time label for the history list (compact: "2m", "3h", "5d", "1w").
 *
 *  Parses through `lib/epoch`'s `epochSeconds` rather than its own `Date.parse`, which makes it
 *  the last time formatter in the tree to converge on that parser (`lib/epoch.test.ts` carried
 *  it as a NAMED EXEMPTION until now — see the correction there: the reason recorded for the
 *  exemption did not hold). Two consequences beyond the de-duplication:
 *
 *    · it accepts a NUMBER as well as an ISO string, so a numeric wire field renders instead of
 *      silently blanking — the failure the exemption was worried about, now impossible
 *    · `epochSeconds` guards on READABILITY, so the epoch itself (0) formats instead of being
 *      discarded by the old `if (!t)` truthiness test
 */
function relTimeShort(at?: string | number): string {
  const at_s = epochSeconds(at)
  if (at_s == null) return ''
  const s = Math.max(0, Date.now() / 1000 - at_s)
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
  // No `.catch(() => [])`: swallowing the rejection makes a 500 indistinguishable from
  // "you have no chats", and this panel then says exactly that. The error rides through so
  // the ladder below can tell the two apart.
  const { data, error: sessionsError, refresh: refreshSessions } = useQuery<ChatSessionSummary[]>('chat:sessions', () => api.chatSessions(), { persist: false })
  const recent = useMemo(() => {
    const all = data ?? []
    return all
      .filter((s) => (s.origin ?? 'manual') === 'manual')
      .sort((a, b) => sessionRecencyMs(b) - sessionRecencyMs(a))
      .slice(0, 20)
  }, [data])
  return (
    <div className="flex flex-col gap-1">
      {data === undefined && sessionsError ? (
        <LoadError what="chats" error={sessionsError} onRetry={refreshSessions} />
      ) : data === undefined ? (
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
              <span className="min-w-0 flex-1 truncate text-on-surface-var text-[0.8125rem] group-hover:text-on-surface">{sessionTitle(s)}</span>
              <StartedByApp s={s} />
              <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{relTimeShort(sessionActivitySeconds(s))}</span>
            </motion.button>
          ))}
        </motion.div>
      )}
      {/* deep-link to the full, filterable/organizable chat-history page */}
      <button type="button" onClick={() => navigate('chat/history')}
        className="mt-1 flex items-center justify-center gap-1.5 rounded-md border border-outline-variant/40 px-2 py-2 text-on-surface-var text-[0.8125rem] transition-colors hover:bg-surface-high hover:text-on-surface"
        style={fvs(470)}>
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

  // The socket came back: a reply that ended while it was down sent its chunks and its end to
  // nobody, and a restart ends every reply. The saved chat says how it ended, so it is read again
  // in place of the partial reply, and the composer is free unless a reply is still running.
  const resync = () => {
    api.chatSessionDetail(sessionKey)
      .then((d) => {
        setDetail({ title: d.title, messages: d.messages ?? [] })
        setStreamText(null)
        setBusy(!!d.run_state.turn)
      })
      .catch(() => { /* the last read stays: a failed re-read is no news about the chat */ })
  }
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
  }, resync)
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
  // so the panel stays snappy on long chats. A turn that ended without its reply says why in an
  // `error` row (a restart cut it off, the model failed), which is part of the turn: without it
  // the peek showed her question unanswered, with nothing saying so.
  const shown = detail.messages.filter((m) => m.role === 'user' || m.role === 'assistant' || m.role === 'error').slice(-12)
  return (
    <div className="flex h-full min-h-0 flex-col gap-m">
      <div className="flex min-h-0 flex-1 flex-col gap-m overflow-y-auto">
        {shown.length === 0 && !streamText ? (
          <p className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">No messages yet — say hi below.</p>
        ) : shown.map((m, i) => (
          m.role === 'user' ? (
            <div key={i} className="ml-6 self-end rounded-lg bg-surface-high px-m py-s">
              <p className="whitespace-pre-wrap break-words text-on-surface text-[0.8125rem] leading-relaxed">{String(m.content || '').slice(0, 800)}</p>
            </div>
          ) : m.role === 'error' ? (
            <p key={i} data-type="body-s" className="mr-2 whitespace-pre-wrap break-words text-error">{String(m.content || '').slice(0, 800)}</p>
          ) : (
            <div key={i} className="mr-2 min-w-0 text-[0.8125rem]">
              <Markdown widgets className="[&_p]:text-[0.8125rem]">{parseSwitchToAgent(parseOptions(String(m.content || '').slice(0, 2000)).body).body}</Markdown>
            </div>
          )
        ))}
        {/* the streaming reply, growing live */}
        {streamText && (
          <div className="mr-2 min-w-0 text-[0.8125rem]">
            <Markdown widgets streaming className="[&_p]:text-[0.8125rem]">{streamText}</Markdown>
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
          // 🔑 `outline-none` WITH NO REPLACEMENT ON EITHER SIDE. It lives in `@layer utilities`,
          // which beats the global `:focus-visible` rule in `@layer base` — so Tab into the session
          // peek panel and the caret landed here with NO visible indicator at all.
          // 🪤 THE RING GOES ON THE CONTROL, NOT THE CONTAINER, even though this is a transparent
          // input inside a box-drawing container — normally `focusRingPerElement`'s `focus-within`
          // case. The container also holds the button row directly below, so a container ring would
          // paint the whole composer whenever Open or Send takes focus: the exact ambiguity that rail
          // records for `CodePlanReview`'s shared box.
          // The pre-existing raw 0.8125rem size on this control is deliberately left alone —
          // converting it to a data-type role is a type-scale change, and moving that ratchet inside
          // a focus fix would muddle both. (Written without spelling the utility: the type-scale
          // scanner counts the pattern wherever it appears, comments included, so naming it here
          // would have raised the ceiling by one. It did, on the first draft of this comment.)
          className="w-full resize-none bg-transparent px-s py-xs text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary placeholder:text-on-surface-low"
        />
        <div className="flex items-center gap-s">
          <Button variant="ghost" size="xs" onClick={onOpen} title="Open the full chat UI"
            className="gap-1 text-on-surface-low hover:text-on-surface">
            Continue in full chat <ArrowRight size={11} className="shrink-0" />
          </Button>
          <motion.button type="button" onClick={send}
            {...unavailableWhen(!input.trim(), 'Type a message first', { busy })}
            whileTap={{ scale: 0.92 }} transition={spring.spatialFast}
            aria-label="Send"
            className="ml-auto inline-flex size-8 shrink-0 items-center justify-center rounded-pill bg-primary text-on-primary transition-colors hover:bg-primary-emphasis disabled:opacity-40 disabled:pointer-events-none aria-disabled:opacity-40 aria-disabled:cursor-not-allowed">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <ArrowUp size={14} />}
          </motion.button>
        </div>
      </div>
    </div>
  )
}



/** Chat routing — the nav target (#/chat) lands on the HISTORY list by default;
 *  #/chat/new is a fresh NEW chat (reachable via "New chat" on the history page
 *  and the "History" button on the new-chat page); opening a session deep-links
 *  to #/chat/<sessionKey>. No left sidebar. */

export function ChatPage({ sub, navigate, navEpoch = 0, query, setQuery }: { sub: string; navigate: (p: string, opts?: { replace?: boolean }) => void; navEpoch?: number; query?: Record<string, string>; setQuery?: RouteProps['setQuery'] }) {
  const seg = (sub || '').split('/')[0]
  // The pending routing suggestion lives HERE, not in ChatSession, because sending on
  // a brand-new chat CREATES the session and re-keys ChatSession (`new-<epoch>` → the
  // session key). That remount destroys the instance that issued the send along with
  // its state and its WebSocket — which is why the suggestion never surfaced on the
  // first message (issue 569). ChatPage spans the boundary (the route wrapper is keyed
  // on the route name, so it does not remount when the session id appears in the URL),
  // so the send's own response can still land somewhere the new instance will read.
  // The payload names its session; `deliverableToOpenSession` is the ONE place that
  // decides whether it may be shown.
  const [routing, setRouting] = useState<RoutingSuggestion | null>(null)
  // The session a run was just DISPATCHED for — owned here for exactly the reason above.
  // The remount that destroys the sending instance also resets its `streaming` to a fresh
  // `false` while the run is live, so the composer's action button read "Send message" for
  // one async round trip: an idle state it was not in. `send()` branches on the same flag,
  // so a click in that window started a FRESH turn — painting a bubble for a turn the
  // server never dispatched (it queued the message and answered `{queued:true}`, which
  // that path does not read) (#3444). `streamingAtMount` is the ONE place that decides
  // whether a handoff belongs to this mount.
  const [liveRun, setLiveRun] = useState('')
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
  if (projectId && (!seg || seg === 'new')) return <ChatSession key={`new-proj-${projectId}-${navEpoch}`} sessionId={null} navigate={navigate} query={q} setQuery={setQ} projectId={projectId} seed={seed} agent={agentParam} routing={routing} setRouting={setRouting} liveRun={liveRun} setLiveRun={setLiveRun} />
  // #/chat/history → the history list. (Chat history is also reachable as a
  // right-docked rail from the new-chat page, so bare #/chat lands on new chat.)
  if (seg === 'history') return <ChatHistoryPage navigate={navigate} query={q} setQuery={setQ} />
  // #/chat/room/<id> → one Agent Room. A room is a MODE of this page rather than a nav peer:
  // there is no sidebar to be a peer of, the session list is this page, and
  // its origin Segmented is the navigation that does exist — so the Rooms scope lists rooms and
  // this branch opens one. It sits ABOVE the bare/new branch and above the session-key
  // fallthrough, because that fallthrough treats any unrecognised segment as a session key and
  // would try to resume a session called "room".
  if (seg === 'room') {
    const roomId = (sub || '').split('/').slice(1).join('/')
    // No room id → the list, not a blank room. `replace` so Back does not bounce through a URL
    // that never rendered anything.
    if (!roomId) return <RoomsRedirect navigate={navigate} />
    return <RoomView key={roomId} roomId={decodeURIComponent(roomId)} navigate={navigate} setQuery={setQ} />
  }
  // bare #/chat AND #/chat/new → a fresh NEW chat (the default landing — the Chat
  // nav target opens straight into a new conversation). The key folds in navEpoch
  // so clicking "New chat" always remounts a fresh session even when the URL was
  // silently rewritten by the composer's replaceState (the "New Chat stuck" fix).
  if (!seg || seg === 'new') return <ChatSession key={`new-${navEpoch}`} sessionId={null} navigate={navigate} query={q} setQuery={setQ} seed={seed} agent={agentParam} routing={routing} setRouting={setRouting} liveRun={liveRun} setLiveRun={setLiveRun} />
  // else it's a session key to resume (deep-linked; keyed off `sub` only so
  // unrelated navigations don't remount/reload it). `seed` rides along for
  // sessions STAGED before their first turn (the investigate opening
  // prompt) — the composer pre-fill is editable, never auto-sent.
  return <ChatSession key={sub} sessionId={sub} navigate={navigate} query={q} setQuery={setQ} seed={seed} routing={routing} setRouting={setRouting} liveRun={liveRun} setLiveRun={setLiveRun} />
}

/** `#/chat/room` with no id is not a room — send the reader to the list.
 *
 *  A component rather than a bare `navigate()` call in the routing branch, because navigating
 *  during render is the classic React warning; an effect is the shape that is allowed to. */
function RoomsRedirect({ navigate }: { navigate: (p: string, opts?: { replace?: boolean }) => void }) {
  useEffect(() => { navigate('chat/history?origin=room', { replace: true }) }, [navigate])
  return null
}

function ChatSession({ sessionId, navigate, query, setQuery, projectId: initialProjectId = '', seed = '', agent: initialAgent = '', routing: pendingRouting, setRouting: setRoutingSuggestion, liveRun, setLiveRun }: { sessionId: string | null; navigate: (p: string, opts?: { replace?: boolean }) => void; query: Record<string, string>; setQuery: RouteProps['setQuery']; projectId?: string; seed?: string; agent?: string; routing: RoutingSuggestion | null; setRouting: (s: RoutingSuggestion | null) => void; liveRun: string; setLiveRun: (s: string) => void }) {
  const { name } = useIdentity()
  // The open chat as the gateway tells it — its transcript, its live turn and everything its reads
  // and frames carry — and the machinery that keeps it true (`chat/useChatStream`).
  const stream = useChatStream({
    sessionId, liveRun, setLiveRun,
    setRouting: setRoutingSuggestion,
    // Every tab with this chat open receives a reading's audio; only the tab whose reading it is
    // plays it.
    onVoiceChunk: (audio, request) => { if (request === readingRef.current?.request) enqueueAudio(audio) },
    onReplyFinished: (sid, opts) => replyFinished(sid, opts),
    onOpened: (d) => adoptDetail(d),
  })
  const {
    sessionRef, turns, wsConnected,
    loadingHistory, missing, loadFailure, setLoadAttempt,
    subagents, setSubagents,
    sideMsgs, setSideMsgs, sideBusy, setSideBusy, sideOpenedRef,
    setAutoChain, workingDir, setWorkingDir,
    title, setTitle, titleNeed, setTitleNeed, sessionCost,
    branchedFrom, startedBy, investigateOrigin,
  } = stream
  const started = turns.length > 0
  // The composer: the draft and what it carries, the selection, Stop, and `send`, which every region
  // sends through (`chat/composer/useChatComposer`).
  const composer = useChatComposer({
    sessionId, navigate, seed, initialProjectId, initialAgent, pendingRouting, setRoutingSuggestion, setLiveRun, started, stream,
    followNewTurn: () => followNewTurn(), oweSpokenReply: (sid) => oweSpokenReply(sid),
  })
  const { adoptDetail, applySelection, applyTemplate, composerFocused, composerRef, focusComposerSoon, input, memoryMode, notice, projectId, quoteToComposer, screenShare, selection, send, setInput, setSelection, setToast } = composer
  // Name of the project this chat is bound to — shown as a header chip so the user knows
  // the chat is scoped to that project's workspace.
  const [projectName, setProjectName] = useState('')
  useEffect(() => {
    if (!projectId) { setProjectName(''); return }
    let alive = true
    api.project(projectId).then((p) => { if (alive) setProjectName(p?.name || '') }).catch(() => {})
    return () => { alive = false }
  }, [projectId])
  // activity panel (Stage 5) — Index/Files/Links, chat-only, toggled from header.
  // URL-backed (?activity=1, push → Back closes it; refresh restores it).
  const [activityOpen, setActivityOpen] = useQueryFlag(query, setQuery, 'activity')
  // New-chat page: a right-docked chat-history panel (the shared SidePanel), toggled
  // from the header. URL-bound (?history=1) so Back closes it + refresh restores it.
  const [historyOpen, setHistoryOpen] = useQueryFlag(query, setQuery, 'history')
  // "Show full result": the full raw of a projected tool
  // result, fetched on demand from the per-session store + shown in a modal. The
  // OPEN state is the URL (?result=<rawRef>, push); the fetched body + the tool
  // name (for the title) are derived — the ref is stable so a refresh re-fetches.
  const [resultRef, setResultRef] = useQueryParam(query, setQuery, 'result', '')
  const resultToolRef = useRef<string>('')
  const [resultBody, setResultBody] = useState<{ content: string; length: number } | null>(null)

  // The docked open-file peek panel. URL-backed (?file=<path>, push → Back closes;
  // refresh/deep-link reopens the same file beside the transcript).
  const [openFileRaw, setOpenFileRaw] = useQueryParam(query, setQuery, 'file', '')
  const openFile = openFileRaw || null
  const setOpenFile = (p: string | null) => setOpenFileRaw(p || '')
  // session title + header actions: rename inline, LLM-regenerate, copy link.
  const [renaming, setRenaming] = useState(false)
  const [renameVal, setRenameVal] = useState('')
  const [regenningTitle, setRegenningTitle] = useState(false)
  // The chat channels this chat can continue on — each connected channel with an owner to reach
  // (the Web UI has none). Not Settings → Providers' `settings:channels` key: that read keeps its
  // catch, so the `[]` it caches on a failure would reach this one as a success. A pairing on either
  // page busts both with `invalidateKeys('settings:channels', true)`. A failed read offers no channel.
  const { data: channelList, error: channelListError } = useQuery('settings:channels-owners', () => api.channels(), { persist: true })
  const handoffChannels = channelListError ? [] : (channelList ?? []).filter((c) => c.owner)
  // The transcript's view and its turns' actions (`chat/transcript/useTranscript`).
  const transcript = useTranscript({
    sessionId, query, setQuery, navigate, stream, started, notice, memoryMode, setSelection,
    agent: selection.agent, send,
  })
  const { followNewTurn, scrolledUp, endRef, isMobile, mapOpen, setMapOpen, sessionEntries, pendingMapJump, runPendingMapJump } = transcript





  // "Show full result": a tool card asked to reveal the
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


  // Read-aloud: Speak, "Speak replies aloud" and the audio a reading streams (`chat/useChatVoice`).
  const { voiceCfg, speakingTurn, speak, replyFinished, oweSpokenReply, enqueueAudio, readingRef } = useChatVoice({ sessionRef, turns, notice })


  // activity panel: the Files / Links tabs, derived from the transcript. No navigation —
  // the Session Map's rail and drawer are the session's only jump surface.
  const activity = useMemo(() => deriveActivity(turns), [turns])

  // Kill EVERY running subagent of this chat's fan-out in one click.
  // Optimistically mark the running cards done; the subagent_done WS events reconcile. A batch's
  // tasks are its run's, and stop with that run, so they are not marked.
  async function killFanout() {
    const s = sessionRef.current
    if (!s) return
    setSubagents((prev) => prev.map((c) => (c.done || c.run ? c : { ...c, done: true, error: 'cancelled' })))
    await api.cancelFanout(s).catch(reportActionFailure('cancel the subagents'))
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


  /** Save the current chat's setup as a reusable starter. Captures the SETUP only —
   *  never the transcript — so sharing or reusing a starter can't leak a conversation. */
  async function saveAsTemplate() {
    const name = await promptInput({
      title: 'Save as starter',
      body: 'Saves this chat\'s agent, model and reasoning effort — not its messages.',
      label: 'Starter name',
      placeholder: 'e.g. Research deep dive',
      confirmLabel: 'Save',
    })
    if (!name) return
    try {
      await api.createSessionTemplate({
        name,
        agent: selection.agent || '',
        model: selection.model && selection.model !== 'Auto' ? selection.model : '',
        reasoning_effort: selection.reasoning || '',
        first_prompt: '',
      })
      invalidateKeys('chat:starters')
      notify(`Saved "${name}" — it'll appear on the new-chat screen.`, 'success')
    } catch (e) {
      notify(`Couldn't save this starter: ${String((e as Error)?.message || e)}`, 'error')
    }
  }

  // ── session title actions ──
  function beginRename() { setRenameVal(title || ''); setRenaming(true) }
  async function commitRename() {
    const s = sessionRef.current
    const v = renameVal.trim()
    setRenaming(false)
    if (!s || !v || v === title) return
    setTitle(v)
    setTitleNeed('')
    // Optimistic, so a swallowed rejection left the header showing a name the server refused — it
    // reverted on the next load with no explanation. Reported, not reverted: this family's remedy for
    // an optimistic write is to TELL, and fighting the header while the user may still be editing is
    // the move `chat/selectionPersistReported` already rejected.
    await api.renameSession(s, v).catch(reportActionFailure('rename this chat'))
    // The header updated optimistically, but the session LIST is a different reader — three of them,
    // in fact (the sidebar, the history page, and the dashboard's recent-sessions). Nothing here
    // busted any of them, so a renamed chat kept its old title everywhere but the header it was
    // renamed from, and on the dashboard that survived a reload (`persist: true`).
    invalidateKeys('chat:sessions', true)
  }
  async function regenTitle() {
    const s = sessionRef.current
    if (!s || regenningTitle) return  // guard against concurrent clicks during the AI call
    setRegenningTitle(true)
    try {
      const r = await api.generateTitle(s).catch(reportActionFailure('regenerate the title'))
      if (r?.title) setTitle(r.title)
      // Nothing was asked: no model is chosen for the chores. The chat keeps its name, and the
      // gateway's sentence says what titles are waiting on and where to choose it.
      if (r?.needs_model) notify(r.needs_model, 'info')
    } finally {
      setRegenningTitle(false)
    }
  }
  async function copyLink() {
    const s = sessionRef.current
    if (!s) return
    const url = `${location.origin}${location.pathname}#/chat/${encodeURIComponent(s)}`
    // Gated, because it was NOT: the catch swallowed the failure and "Copied" was set anyway, so
    // a blocked write left the button claiming a link the clipboard did not hold.
    if (!(await copyText(url, 'the chat link'))) return
    // A toast, like this header's other actions ("Save as starter", a branch): the control can be
    // a row of the `…` menu, which has closed by the time the copy lands, so a confirmation drawn
    // on the control itself would say nothing there.
    notify('Chat link copied.', 'success')
  }
  // Continue this chat on a channel: it opens as a thread in your DM there. A channel that does not
  // know who you are cannot reach you, so it says where to fix that instead of sending into nowhere.
  async function handOff(c: ChannelRuntime) {
    const s = sessionRef.current
    if (!s) return
    if (!c.owner?.id) {
      notify(`${c.display_name} doesn't know who you are yet. Pair its owner in Settings → Providers → ${c.display_name} → Configure, then try again.`, 'error')
      return
    }
    const ok = await confirm({
      title: `Continue on ${c.display_name}?`,
      body: `This chat opens as a thread in your ${c.display_name} direct messages, with its latest message. Reply there to keep going.`,
      confirmLabel: 'Continue there',
    })
    if (!ok) return
    if (await reportingWrite(`continue this chat on ${c.display_name}`, () => api.handoffSession(s, c.name))) {
      notify(`This chat is in your ${c.display_name} messages now.`, 'success')
    }
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
  // Set the live session's working directory (agent cwd + memory-partition scope), or clear it.
  async function setWorkspaceDir() {
    const s = sessionRef.current
    if (!s) return
    const dir = await promptInput(workingDirectoryPrompt(workingDir))
    if (dir == null) return
    let shownFor = 2600
    try {
      const r = await api.setSessionWorkspaceDir(s, dir.trim())
      if (typeof r.auto_chain === 'string') setAutoChain(r.auto_chain)
      if (typeof r.workspace_dir === 'string') setWorkingDir(r.workspace_dir)
      setToast(workingDirectoryToast(r, !dir.trim()))
      if (r.moved) shownFor = 6000
    }
    catch (e) { setToast((e as Error).message || 'Failed to set working directory') }
    window.setTimeout(() => setToast(null), shownFor)
  }


  const stage = <ChatComposer composer={composer} stream={stream} sessionId={sessionId} started={started} onOpenFile={setOpenFile}
    voiceCfg={voiceCfg} speakingTurn={speakingTurn} />

  // The header's context line: what this conversation IS, under its title (`ChatContextLine`).
  const contextChips = started ? chatContextChips({
    screenShare: screenShare.sharing ? { onStop: screenShare.toggle } : null,
    startedBy: startedBy?.name,
    project: projectName ? { name: projectName, open: () => navigate(`projects/${projectId}`) } : null,
    branchedFrom: branchedFrom
      ? { title: branchedFrom.title, open: () => navigate(`chat/${branchedFrom.key}`) }
      : null,
    investigate: investigateOrigin?.title
      ? {
          title: investigateOrigin.title,
          open: investigateOrigin.back_link
            ? () => navigate((investigateOrigin.back_link as string).replace(/^#\//, ''))
            : undefined,
        }
      : null,
    cost: sessionCost,
  }) : []

  // The title above reads "Untitled chat" for a reason the chat can fix: the line under it says so
  // first, in the words the other surfaces of these chores use, as the way to where it is fixed.
  const headerLine = started && titleNeed
    ? [<NeedsModelNote key="title-need" sentence={titleNeed} />, ...contextChips]
    : contextChips

  if (missing) return <MissingChat draft={input} navigate={navigate} />
  // A failed read with nothing painted: say it failed, and let the user retry. (A transcript
  // painted from the fresh cache stays on screen — a failed REVALIDATION of it is not news.)
  if (loadFailure && !started) return (
    <div className="flex h-full flex-col items-center justify-center overflow-hidden px-l">
      <LoadError what="chat" error={loadFailure} onRetry={() => setLoadAttempt((n) => n + 1)} />
    </div>
  )

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
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
            // `min-w-0 w-full`: an input's intrinsic width is ~20ch and a flex item will not shrink
            // below it, so a fixed 200px floor here overran a phone's whole row.
            <input autoFocus aria-label="Rename this chat" value={renameVal} onChange={(e) => setRenameVal(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); else if (e.key === 'Escape') setRenaming(false) }}
              className="h-8 w-full min-w-0 max-w-[420px] rounded-md bg-surface-high px-2 text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          ) : (
            // The title's row holds only what names the chat: the way back, the title, and its
            // regenerate affordance. Everything ABOUT the chat is on the context line under it
            // (`below`), and "Copy chat link" is a control in the cluster — see `ChatContextLine`
            // for what sharing this row cost the title.
            <div className="flex items-center gap-1.5 min-w-0">
              {/* Back to the chat history list — replaces the separate right-side
                  "Chat history" button (it sits left of the title, its natural home). */}
              <IconButton icon={ArrowLeft} label="Back to chat history" size={40} onClick={() => navigate('chat/history')} />
              <button type="button" onClick={beginRename} title="Rename chat"
                className="group inline-flex items-center gap-1.5 min-w-0 max-w-[420px] text-on-surface hover:text-on-surface-var transition-colors">
                <span data-type="title-l" className="truncate">{sessionTitle({ key: sessionRef.current ?? '', title })}</span>
                <Pencil size={13} className="shrink-0 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity" />
              </button>
              {/* Regenerate title — a small magic-stars affordance hugging the title's
                  top-right edge, not a space-hungry header control. A chat that is not
                  persistent is given to no model for its title, so it has none to ask for. */}
              {sessionRef.current && memoryMode === 'persistent' && (
                <IconButton icon={Sparkles} label="Regenerate title" onClick={regenTitle}
                  loading={regenningTitle} disabled={regenningTitle} size={20} iconSize={12}
                  className="shrink-0 -ml-0.5 self-start text-on-surface-low hover:text-primary" />
              )}
            </div>
          )
        )}
        below={headerLine.length > 0 ? headerLine : undefined}
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
            {/* In an app's conversation the app's grant decides every turn and your choice here
                would be overwritten by it, so the pill shows that grant and is not yours to move
                (the composer says why). */}
            <HeaderModePill ariaLabel="Permission mode" value={selection.approval ?? 'normal'}
              disabled={!!startedBy}
              disabledReason={startedBy ? `${startedBy.name}'s permissions decide this chat, not yours` : undefined}
              options={APPROVAL_SLIDER} onChange={(v) => applySelection({ approval: v as ApprovalMode })} />
            {/* A control like its neighbours, so it takes the cluster's ladder — icon when the row
                is tight, a row of the `…` menu when tighter. Beside the title it was 40px nothing
                else could use, and past the viewport's edge at 1024px and below. */}
            {started && sessionRef.current && (
              <HeaderControl icon={Link2} label="Copy chat link" priority="low" onClick={copyLink} />
            )}
            {started && sessionRef.current && handoffChannels.map((c) => (
              <HeaderControl key={`handoff-${c.name}`} icon={Send} label={`Continue on ${c.display_name}`} priority="low"
                hint={c.owner?.id ? undefined : 'No owner paired yet'} onClick={() => void handOff(c)} />
            ))}
            {started && sessionRef.current && (
              <HeaderControl icon={NotebookPen} label="Brief the agent" priority="low" onClick={briefAgent} />
            )}
            {started && sessionRef.current && (
              <HeaderControl icon={FolderCog} label="Working directory" priority="low" onClick={setWorkspaceDir} />
            )}
            {started && sessionRef.current && (
              <HeaderControl icon={Sparkles} label="Save as starter" priority="low" onClick={saveAsTemplate} />
            )}
            <HeaderControl icon={Edit3} label="New chat" variant="primary" priority="primary" onClick={() => navigate('chat/new')} />
            {/* The Session Map's ONE control, on every viewport (the "one named control"):
                it shows/hides the gutter rail on a pointer device and opens/closes the drawer on
                the mobile form, so there is never a second name for "show me the map".
                `ariaExpanded`, not `active`: this is a disclosure, not an on/off setting.
                `priority="primary"` because on the mobile form this control IS the in-session
                navigation — shedding it into the overflow `…` menu is exactly the "mobile loses
                session nav" outcome this must prevent. */}
            {started && (
              <HeaderControl icon={ListTree} label="Session map" priority="primary"
                ariaExpanded={mapOpen} onClick={() => setMapOpen(!mapOpen)} />
            )}
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
        {/* 🪤 INERT WHILE THE MOBILE SESSION MAP COVERS IT. The drawer below is a flex SIBLING
            whose fit-width exceeds a phone viewport, so when it opens this column is squeezed to
            ZERO width — measured at 390px: the composer's `.cm-scroller` ends up `clientWidth: 0`
            with `scrollWidth: 19`. That is not merely invisible, it is a keyboard trap in waiting:
            a tab-order stop and (per axe's `getScroll`, buffer 13) a `scrollable-region-focusable`
            [serious] scroll region that no one can reach or see. `inert` is the whole fix — it
            drops the subtree from focus AND from assistive tech, which is exactly what "the drawer
            covers the transcript at this width" already claims. NOT `aria-hidden`, which would
            leave the composer focusable and trade one serious violation for `aria-hidden-focus`.
            The header control that closes the drawer lives ABOVE this row, so the user is never
            shut out. */}
        <div className="relative flex min-w-0 flex-1 flex-col" inert={mapOpen && isMobile && started}>
          {/* The composer's light, staged in THIS column rather than the page: its box is
              the stage `DotGlow` fades the halo out inside of, so a docked panel beside the
              column (Activity, file peek, chat history, the mobile map drawer — all opaque
              `SidePanel`s) bounds the halo instead of slicing it at the panel's edge. */}
          <DotGlow focused={composerFocused} composerRef={composerRef} />
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
                <StarterChips onPick={applyTemplate} />
                <SuggestionChips onPick={(s) => setInput(s)} />
              </div>
            </div>
          ) : (
            <>
              <Transcript transcript={transcript} stream={stream} sessionId={sessionId} memoryMode={memoryMode} navigate={navigate}
                onOpenFile={setOpenFile} onQuote={quoteToComposer} onPickFollowup={(t) => { setInput(t); focusComposerSoon() }}
                send={send} speak={speak} speakingTurn={speakingTurn} />
              <div className="relative shrink-0 px-l pb-l">
                {/* reconnecting cue — the WS dropped; state will re-sync on
                    reconnect, but tell the user the link is down. */}
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
                {/* return-to-newest — the SESSION MAP's control, rendered here
                    rather than defined here: the map owns the one back-to-newest affordance in the
                    app, so the rail and the coarse-pointer drawer cannot each grow their own. */}
                <SessionMapReturnLatest
                  scrolledUp={scrolledUp}
                  onReturnToLatest={() => scrollToLatest(endRef.current)}
                />
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

        {/* ── SESSION MAP, COARSE-POINTER FORM ────────────────────────────────────────────────
            On the mobile form the gutter rail is replaced by this drawer: the SHARED `SidePanel`,
            so the map's WIDTH persists through the primitive's own `storeKey` mechanism
            and the panel matches every other dock in the app. Same `?map` flag and same header
            control as the rail — one open/closed state, two forms.

            CLOSED AT REST on this form — see the per-form default where `mapFlag` is read. This
            drawer is a docked column whose fit-width fills a phone viewport, so an open resting
            state puts it over the composer before the user has typed anything.

            🪤 NO `urlKey`, DELIBERATELY, and the reason survives the per-form default. `SidePanel`'s
            `urlKey` close path writes `{key: null}`, i.e. "back to the default" — which is the
            right value here but the wrong MECHANISM: the flag is shared with the rail, whose
            default is the opposite, so a close routed through `urlKey` would depend on which form
            happened to be mounted. `onClose` writes the closed value explicitly, so close ⇒ URL
            updated holds identically in both forms.

            A tap jumps AND closes: the drawer covers the transcript at this width, so a jump the
            user cannot see is not a jump. It closes FIRST and scrolls after — see
            `pendingMapJump`, where the order is the difference between a tap that navigates and
            one that resolves an offset in a 32px-wide transcript and lands nowhere. */}
        <AnimatePresence onExitComplete={runPendingMapJump}>
          {mapOpen && isMobile && started && (
            <SidePanel title="Session map" icon={<ListTree size={18} className="text-primary" />} storeKey="session-map-w"
              fillHeight onClose={() => setMapOpen(false)}>
              <SessionMapDrawer entries={sessionEntries}
                onJumpTo={(coord) => { pendingMapJump.current = coord; setMapOpen(false) }} />
            </SidePanel>
          )}
        </AnimatePresence>
        {/* Activity rail — a standard right-docked SidePanel (Files / Links / Side),
            a flex sibling that pushes the chat narrower (not a floating overlay).
            URL-bound: ?activity=1 (Back closes; refresh restores). */}
        <AnimatePresence>
          {activityOpen && started && (
            <SidePanel title="Activity" icon={<Activity size={18} className="text-primary" />} storeKey="chat-activity-w"
              fillHeight urlKey={{ key: 'activity', setQuery }} onClose={() => setActivityOpen(false)}>
              <ChatActivityPanel activity={activity} onOpenFile={setOpenFile} subagents={subagents}
                onKillFanout={killFanout}
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
                  <div className="text-on-surface-low text-[0.75rem]">{resultBody.length.toLocaleString()} chars</div>
                )}
                <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-3 py-2 font-mono text-on-surface-var text-[0.75rem] leading-relaxed">{withoutFence(resultBody.content)}</pre>
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








/** The route names a chat that does not exist — a dead deep link, or a chat deleted while it
 *  was open. Said plainly, with the two ways forward, instead of an empty chat whose composer
 *  takes a message the server refuses and does not save. A draft that was typed (or sent and
 *  refused) is not dropped: "Start a new chat" carries it into the new chat's composer through
 *  the same `?seed=` pre-fill every other "start a chat with this" launch uses. */
function MissingChat({ draft, navigate }: { draft: string; navigate: (p: string, opts?: { replace?: boolean }) => void }) {
  const keep = draft.trim()
  return (
    <div className="flex h-full flex-col items-center justify-center gap-s overflow-y-auto px-l">
      <EmptyState icon={MessageSquare} title="This chat doesn’t exist"
        hint={keep
          ? 'It may have been deleted, or the link is out of date. Your message was not sent — it will be waiting in the new chat.'
          : 'It may have been deleted, or the link is out of date.'}
        action={{
          label: 'Start a new chat', icon: Edit3,
          onClick: () => navigate(keep ? `chat/new?seed=${encodeURIComponent(keep)}` : 'chat/new', { replace: true }),
        }} />
      <Button variant="ghost" size="sm" onClick={() => navigate('chat/history')}>See all chats</Button>
    </div>
  )
}

/** Split a search snippet on the index's `<<`/`>>` match markers.
 *
 *  Returned as parts rather than HTML on purpose: the snippet is transcript text
 *  the user typed, so rendering it as markup would be an injection sink. Any
 *  unpaired marker degrades to plain text.
 */
function snippetParts(snippet: string): { text: string; hit: boolean }[] {
  const parts: { text: string; hit: boolean }[] = []
  let rest = snippet
  while (rest) {
    const open = rest.indexOf('<<')
    if (open < 0) { parts.push({ text: rest, hit: false }); break }
    const close = rest.indexOf('>>', open + 2)
    if (close < 0) { parts.push({ text: rest, hit: false }); break }
    if (open > 0) parts.push({ text: rest.slice(0, open), hit: false })
    parts.push({ text: rest.slice(open + 2, close), hit: true })
    rest = rest.slice(close + 2)
  }
  return parts
}

/** Tags in and out of one session — the body of `PUT .../tags` (`api.editSessionTags`). */
type TagEdit = { add?: string[]; remove?: string[] }

/** The gateway's rule for a tag edit, for the optimistic paint: the removals, then each addition
 *  appended unless the session already carries it. */
function applyTagEdit(tags: string[], edit: TagEdit): string[] {
  const out = tags.filter((t) => !(edit.remove ?? []).includes(t))
  for (const t of edit.add ?? []) if (!out.includes(t)) out.push(t)
  return out
}

/** The most matches the chat list's content search asks for: the route's ceiling. */
const SEARCH_LIMIT = 200

/** Dedicated sessions LIST page (#/chat/history) — search, manage, open. */
function ChatHistoryPage({ navigate, query, setQuery }: { navigate: (p: string) => void; query: Record<string, string>; setQuery: RouteProps['setQuery'] }) {
  // Instant-paint cache: sessions revalidate often (in-memory, persist:false);
  // folders/tags rarely change so they survive a hard reload (persist:true).
  // NB: the cache key carries the archived flag. Sharing one key across both views
  // would paint the active list while the archive loaded (and vice versa).
  const archivedView = (query.archived ?? '') === '1'
  // The rejection is NOT swallowed here either. Measured with `/api/chat/sessions` forced to
  // 500 and a cold cache: `.catch(() => [])` made `sessions` an empty array, so this page told
  // an account with 31 sessions "No chats yet" and offered to start its first — with nothing in
  // a live region. An empty list and a failed load are different facts and now render as such.
  const { data: cachedSessions, error: sessionsError, refresh: refreshSessions } = useQuery<ChatSessionSummary[]>(
    archivedView ? 'chat:sessions:archived' : 'chat:sessions',
    () => api.chatSessions(archivedView),
    { persist: false },
  )
  // Both persisted, and both feed a menu whose empty state says "Create a folder or tag first" —
  // an instruction, not just a blank. A swallowed rejection cached that claim.
  const { data: foldersData, error: foldersError, refresh: refreshFolders } = useQuery<ChatFolder[]>('chat:folders', () => api.chatFolders(), { persist: true })
  const { data: tagsData, error: tagsError, refresh: refreshTags } = useQuery<ChatTag[]>('chat:tags', () => api.chatTags(), { persist: true })
  // Agent Rooms — its own read, because a room is not a session. Its namespace
  // is LIVE: a room's contents change behind the app's back by design, since a human message
  // starts a background round whose replies land seconds later with nothing in this tab writing
  // them. `persist: false` for the same reason.
  //
  // Not swallowed, and the distinction matters more here than elsewhere: rooms ship DISABLED, so
  // the common answer is a decided `{"enabled": false}` rather than a list, and `RoomsScope`
  // renders "off", "none" and "could not read" as three different things. A `.catch(() => [])`
  // would have told a user with the feature switched off that they have no rooms. The off answer
  // used to be a 403 `rooms_disabled`, so every chat visit on a default install logged a failed
  // request.
  const { data: roomsData, error: roomsError, refresh: refreshRooms } = useQuery(
    'rooms:list', () => api.rooms().then((d) => (isSwitchedOff(d) ? d : d.rooms)), { persist: false },
  )
  const folders = foldersData ?? []
  const tags = tagsData ?? []
  // Local optimistic overlay so pin/folder/tag mutations paint instantly; it
  // re-syncs whenever the revalidated cache lands.
  const [optimistic, setSessions] = useState<ChatSessionSummary[] | null>(null)
  useEffect(() => { if (cachedSessions !== undefined) setSessions(cachedSessions) }, [cachedSessions])
  const sessions = optimistic
  // View/filter state rides the URL (the unified-URL pattern, matching every
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
  //
  // 🔑 `room` IS A FIFTH SCOPE, AND IT IS NOT A SESSION ORIGIN. This union has always been a
  // VIEW union rather than a mirror of `ChatSessionSummary.origin` — it adds the synthetic
  // `all`. Agent Rooms extends it on the same axis: a room is
  // not a chat session, it is read from `/api/rooms`, and each member's own provider session is
  // filtered out of `/api/chat/sessions` by the backend — so this scope SWAPS THE LIST BODY for
  // the rooms list instead of narrowing `sessions`. That is why `matches` below never sees it.
  const [originRaw, setOriginRaw] = useQueryParam(query, setQuery, 'origin', 'manual', { replace: true })
  const origin: 'manual' | 'loop' | 'code' | 'channel' | 'room' | 'all' =
    originRaw === 'loop' || originRaw === 'code' || originRaw === 'channel' || originRaw === 'room' || originRaw === 'all' ? originRaw : 'manual'
  const setOrigin = (o: 'manual' | 'loop' | 'code' | 'channel' | 'room' | 'all') => setOriginRaw(o)
  const roomsScope = origin === 'room'
  // Archived view. Rides the URL like every other filter, so
  // the archive is deep-linkable. The ACTIVE/ARCHIVED split is enforced server-side —
  // the client asks for one or the other rather than fetching everything and hiding
  // rows, so "archived" can't leak into a surface that forgot to filter.
  const [archivedRaw, setArchivedRaw] = useQueryParam(query, setQuery, 'archived', '', { replace: true })
  const showArchived = archivedRaw === '1'
  const setShowArchived = (v: boolean) => setArchivedRaw(v ? '1' : '')
  // Multi-select for bulk ops. Deliberately NOT in the URL: a selection is transient
  // work-in-progress, and deep-linking "these 12 chats are selected" is meaningless
  // once the list changes underneath it.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkNote, setBulkNote] = useState('')
  const selecting = selected.size > 0
  const toggleSelected = (key: string) => {
    setBulkNote('')   // a fresh selection supersedes the last action's result
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }
  // Clears the selection ONLY. The outcome note deliberately survives: the
  // selection bar unmounts the moment the selection empties, so a note living
  // inside it would flash and vanish — the user would never read the result of the
  // action they just took.
  const clearSelection = () => setSelected(new Set())

  // One runner for every bulk op. Reports per-key outcomes because a selection can
  // go stale between the click and the request — 38-of-40 is a useful answer, a bare
  // failure is not.
  const runBulk = async (op: 'archive' | 'restore' | 'never_archive', args: { value?: boolean } = {}) => {
    if (!selected.size || bulkBusy) return
    setBulkBusy(true); setBulkNote('')
    try {
      const res = await api.bulkSessions(op, [...selected], args)
      const verb = op === 'archive' ? 'Archived' : op === 'restore' ? 'Restored' : 'Updated'
      const parts = [`${verb} ${res.changed.length}`]
      if (res.unchanged.length) parts.push(`${res.unchanged.length} already set`)
      if (res.missing.length) parts.push(`${res.missing.length} not found`)
      setBulkNote(parts.join(' · '))
      clearSelection()
      load()
    } catch {
      setBulkNote('Bulk action failed — nothing was changed.')
    } finally {
      setBulkBusy(false)
    }
  }

  const load = useCallback(() => {
    // Every reader of this collection, in one call: an archive/restore moves a session BETWEEN the
    // two lists so the one we are not looking at is stale too, and the dashboard's recent-sessions
    // list reads the same collection under `chat:sessions:recent`. Prefix mode covers a reader added
    // later, which is exactly how the dashboard's was missed. It does NOT touch `chat:suggestions`
    // and friends — the prefix is the collection, not the namespace.
    invalidateKeys('chat:sessions', true)
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
  // The socket came back: the job's progress and its end went to nobody while it was down (a
  // restart ends the job), so its state is read again, and the tags it changed meanwhile with it.
  const resyncRetag = () => {
    api.retagStatus().then((j) => { setRetag(j ?? null); retagUpdatedRef.current = j?.updated ?? 0 }).catch(() => {})
    load(); refreshTags()
  }
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
  }, resyncRetag)
  async function startRetag() {
    if (retagRunning) { await api.cancelRetag().catch(reportActionFailure('cancel the retag run')); return }
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
  const recency = sessionRecencyMs
  // Full-text conversation search: the local filter (title/key/preview) only sees
  // what the list row carries. For "I remember SAYING X" we also query
  // /api/sessions/search, which scans the persisted JSONL bodies, and union those
  // keys in. Debounced; keys normalized (the search returns dashboard_-prefixed
  // keys, the list uses the stripped form).
  const [contentKeys, setContentKeys] = useState<Set<string> | null>(null)
  // The matching passage per key, so a content-only hit can show WHY it matched
  // rather than looking like an unexplained result (the FTS index returns one).
  const [contentSnippets, setContentSnippets] = useState<Map<string, string>>(new Map())
  // Which path answered the content search — 'index' (FTS5) or 'scan' (the bounded
  // transcript-scan fallback), the `source` the endpoint reports and the client now
  // keeps. null = no content search has resolved, so the indicator stays hidden.
  const [contentSource, setContentSource] = useState<string | null>(null)
  // How far the content search reached (`searched` of the chats, `complete`). While the search
  // index is still being built an answer covers only the chats it holds, and says so; `restFor`
  // is the query the user asked to read the rest of directly, which the next search does.
  const [contentCoverage, setContentCoverage] = useState<Pick<SessionSearchAnswer, 'searched' | 'complete' | 'index' | 'matched'> | null>(null)
  const [restFor, setRestFor] = useState<string | null>(null)
  const [restBusy, setRestBusy] = useState(false)
  // Why the content search failed, or null. It used to fail in silence: the list quietly fell
  // back to title matches, and a chat the user remembered SAYING something in read as
  // "no such chat". Bumping `contentRetry` runs the same search again.
  const [contentFailure, setContentFailure] = useState<string | null>(null)
  const [contentRetry, setContentRetry] = useState(0)
  // List-view drag-to-folder: the chat key being dragged + the folder group hovered
  // (id, or '' for the ungrouped group → clears the folder). Mirrors the Board's
  // tag drag, reusing setFolder as the drop action.
  const [folderDragKey, setFolderDragKey] = useState<string | null>(null)
  const [overFolder, setOverFolder] = useState<string | null>(null)
  useEffect(() => {
    const query = q.trim()
    if (query.length < 2) { setContentKeys(null); setContentSnippets(new Map()); setContentSource(null); setContentCoverage(null); setContentFailure(null); return }
    let alive = true
    const rest = restFor === query
    const t = window.setTimeout(() => {
      // The route's most: a chat that matched but is not in the answer is filtered out of the
      // list below, so the answer holds as many as it may, and says when there were more.
      api.sessionsSearch(query, { rest, limit: SEARCH_LIMIT }).then(({ sessions: rows, source, searched, complete, index, matched }) => {
        if (!alive) return
        const strip = (k: string) => k.replace(/^dashboard[_:]/, '')
        setContentKeys(new Set(rows.map((r) => strip(r.key))))
        setContentSnippets(new Map(
          rows.filter((r) => r.snippet).map((r) => [strip(r.key), r.snippet as string]),
        ))
        setContentSource(source ?? null)
        setContentCoverage({ searched, complete, index, matched })
        setContentFailure(null)
      }).catch((e: unknown) => {
        if (!alive) return
        setContentKeys(null); setContentSnippets(new Map()); setContentSource(null); setContentCoverage(null)
        const sentence = failureSentence('search inside your chats', e)
        setContentFailure(/[.!?]$/.test(sentence) ? sentence : `${sentence}.`)
      }).finally(() => { if (alive) setRestBusy(false) })
    }, rest ? 0 : 300)
    return () => { alive = false; clearTimeout(t) }
  }, [q, contentRetry, restFor])
  const coverage = contentCoverage ? searchCoverage(contentCoverage) : null
  const unlisted = contentKeys && contentCoverage?.matched ? contentCoverage.matched - contentKeys.size : 0
  const matches = useCallback((s: ChatSessionSummary) => {
    const sOrigin = s.origin ?? 'manual'
    // 'all' shows everything; otherwise the row's origin must match the scope.
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
  // Rooms are counted from their OWN read, and deliberately NOT folded into `all`: `all` means
  // "every chat", and a room is not a chat. A room's count of 0 with the feature ON is still a
  // reason to show the tab — otherwise the only way to reach the "New room" action would be to
  // already have a room, which is the discoverability dead end this scope exists to avoid.
  const roomCount = Array.isArray(roomsData) ? roomsData.length : 0
  const roomsAvailable = Array.isArray(roomsData)

  async function del(s: ChatSessionSummary) {
    // The memory half is the backend's rule (`chat_forget.delete_chats`): what memory filed under
    // this chat alone goes, and a record other work also stands behind stays.
    if (!(await confirm({
      title: 'Delete chat?',
      body: `"${sessionTitle(s)}" and its history will be permanently removed, and so will what memory drew from this chat alone: its summary, its episodes, and the facts and lessons that came only from it. What also came from elsewhere stays, such as a lesson you taught in another chat too or a fact you edited in Memory, as do the files, Knowledge, artifacts and skills you kept.`,
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
    invalidateKeys(detailKey(s.key))
    load()
  }
  /** Download a transcript (`downloadFrom`). The export is credential-redacted server-side;
   *  say so, because a user about to attach this to an email should know what it does and
   *  doesn't contain. */
  function downloadExport(key: string, format: 'md' | 'json') {
    downloadFrom(api.sessionExportUrl(key, format))
    notify('Exporting this chat — credentials are redacted from the file.', 'info')
  }
  /** Share a chat as a read-only artifact in THIS instance's library. Nothing is
   *  published: no public link, no token — the artifact is reachable only through this
   *  gateway's auth, same as every other artifact. Lands the user on the created
   *  artifact, because "it worked" is only credible if you can see the thing. */
  async function shareSession(s: ChatSessionSummary) {
    try {
      const res = await api.shareSession(s.key)
      notify(`Shared as "${res.name}" — read-only, credentials redacted.`, 'success')
      navigate(`artifacts/${res.slug}`)
    } catch (e) {
      notify(`Couldn't share this chat: ${String((e as Error)?.message || e)}`, 'error')
    }
  }
  async function togglePin(key: string, pinned: boolean) {
    setSessions((prev) => prev && prev.map((s) => (s.key === key ? { ...s, pinned } : s)))
    await api.pinChatSession(key, pinned).catch(() => load())
  }
  async function setFolder(key: string, folderId: string | null) {
    setSessions((prev) => prev && prev.map((s) => (s.key === key ? { ...s, folder_id: folderId || '' } : s)))
    await api.setSessionFolder(key, folderId).catch(() => load())
  }
  // 🔴 A TAG EDIT IS ONE TAG IN OR OUT, NEVER THIS PAGE'S LIST. Both writes below used to send the
  // session's whole tag list as this page painted it, so a tag set since — in another tab, or by the
  // gateway's auto-tag, re-tag run or bulk tag — was dropped by the next toggle here, and nothing on
  // either screen said so. The gateway applies the edit to the tags as stored, and its answer (the
  // tags as stored after) replaces the optimistic paint.
  async function editTags(key: string, edit: TagEdit, what: string) {
    setSessions((prev) => prev && prev.map((x) => (x.key === key ? { ...x, tags: applyTagEdit(x.tags ?? [], edit) } : x)))
    try {
      const { tags } = await api.editSessionTags(key, edit)
      setSessions((prev) => prev && prev.map((x) => (x.key === key ? { ...x, tags } : x)))
    } catch (e) {
      reportActionFailure(what)(e)
      load()
    }
  }
  async function toggleTag(key: string, tagId: string) {
    const s = (sessions ?? []).find((x) => x.key === key)
    const on = (s?.tags ?? []).includes(tagId)
    await editTags(key, on ? { remove: [tagId] } : { add: [tagId] }, on ? 'untag this chat' : 'tag this chat')
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
    const edit: TagEdit = {
      ...(fromTagId && fromTagId !== toTagId ? { remove: [fromTagId] } : {}),
      ...(toTagId && !cur.includes(toTagId) ? { add: [toTagId] } : {}),
    }
    if (!edit.add && !(edit.remove && cur.includes(edit.remove[0]))) return
    await editTags(key, edit, 'move this chat')
  }
  // Single-row lifecycle. Optimistic then reconciled by load(), matching togglePin:
  // an archive should feel instant even though the list has to re-fetch (the row is
  // moving between two server-filtered lists).
  async function setLifecycle(key: string, lifecycle: 'active' | 'archived') {
    setSessions((prev) => prev && prev.map((x) => (x.key === key ? { ...x, lifecycle } : x)))
    // 🪤 THE REFETCH IS DELIBERATELY *NOT* GATED HERE, unlike every other data-driven write in this
    // family. The optimistic move above already claimed the row changed list, so `load()` on a failure
    // is what puts it BACK — the refetch is the repair, not a wasted round trip. Skipping it would
    // leave the archived-looking row lying. Only the silence was the defect.
    await api.setSessionLifecycle(key, { lifecycle }).catch(
      reportActionFailure(`${lifecycle === 'archived' ? 'archive' : 'unarchive'} this chat`))
    load()
  }
  async function setNeverArchive(key: string, value: boolean) {
    setSessions((prev) => prev && prev.map((x) => (x.key === key ? { ...x, never_archive: value } : x)))
    await api.setSessionLifecycle(key, { never_archive: value }).catch(() => load())
  }
  async function createFolder() {
    const name = await promptInput({ title: 'New folder', label: 'Folder name', placeholder: 'e.g. Research', confirmLabel: 'Create' })
    if (!name) return
    // Data-driven: the folder appears only via `load()`. A swallowed rejection meant the user typed a
    // name into a dialog and nothing appeared, with the reload re-rendering the same list.
    if (!(await reportingWrite(`create the folder "${name}"`, () => api.createChatFolder(name)))) return
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
      { icon: <MessageSquare size={15} />, label: 'Open', onSelect: () => navigate(chatFindPath(s.key, q)) },
      { icon: <Pin size={15} />, label: s.pinned ? 'Unpin' : 'Pin to top', onSelect: () => togglePin(s.key, !s.pinned) },
      ...(s.folder_id ? [{ icon: <Folder size={15} />, label: 'Remove from folder', onSelect: () => setFolder(s.key, null) }] : []),
      ...folders.filter((f) => f.id !== s.folder_id).map((f) => ({ icon: <Folder size={15} />, label: `Move to ${f.name}`, onSelect: () => setFolder(s.key, f.id) })),
      ...(s.lifecycle === 'archived'
        ? [{ icon: <ArchiveRestore size={15} />, label: 'Restore from archive', onSelect: () => setLifecycle(s.key, 'active') }]
        : [{ icon: <Archive size={15} />, label: 'Archive', onSelect: () => setLifecycle(s.key, 'archived') }]),
      {
        icon: <Pin size={15} />,
        label: s.never_archive ? 'Allow auto-archive' : 'Never auto-archive',
        onSelect: () => setNeverArchive(s.key, !s.never_archive),
      },
      // Export is a download link to the endpoint rather than a fetch: the browser saves what
      // the route names in its Content-Disposition, the app stays on screen, and a long
      // transcript is streamed instead of buffered through JS.
      { icon: <Download size={15} />, label: 'Export as Markdown', onSelect: () => downloadExport(s.key, 'md') },
      { icon: <Download size={15} />, label: 'Export as JSON', onSelect: () => downloadExport(s.key, 'json') },
      // Share sits beside export because it is the same intent one step further: export
      // hands you the redacted transcript, share keeps that same redacted transcript in
      // the library as a frozen record. The label says "read-only artifact" rather than
      // just "Share" so nobody reads a publish-to-the-internet promise into it.
      { icon: <Share2 size={15} />, label: 'Share as read-only artifact', onSelect: () => shareSession(s) },
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
      {/* Selection tick. The primitive owns stopPropagation, so ticking a row never
          also opens the peek panel — two intents on one click target. */}
      <Checkbox checked={selected.has(s.key)} onChange={() => toggleSelected(s.key)}
        ariaLabel={`Select ${sessionTitle(s)}`}
        className={`transition-opacity ${selecting ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'}`} />
      <span className="grid size-9 shrink-0 place-items-center rounded-lg" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
        <MessageSquare size={17} className="text-primary" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{sessionTitle(s)}</div>
        {/* Why this chat matched: the passage from the transcript, with the matched
            terms marked. Only for content hits — a title match is already visible
            above, so repeating it would be noise. */}
        {contentSnippets.get(s.key) && (
          <div className="mt-0.5 truncate text-on-surface-var text-[0.8125rem]">
            {snippetParts(contentSnippets.get(s.key) as string).map((part, i) => (
              part.hit
                ? <mark key={i} className="rounded bg-primary/25 px-0.5 text-on-surface">{part.text}</mark>
                : <span key={i}>{part.text}</span>
            ))}
          </div>
        )}
        <div className="flex items-center gap-1.5 flex-wrap text-on-surface-low text-[0.8125rem]">
          <span>{sessionRowMeta(s)}</span>
          {/* Origin chip on worker chats — names the loop / code project and opens its
              cockpit (not the raw chat) so the user dives into context. The 'loop' origin
              covers every non-code kind (general/goal/design), so the label is neutral. A chat
              that came in on a chat channel says which one instead (`FromChannel`). */}
          <FromChannel s={s} />
          {s.origin && s.origin !== 'manual' && s.origin !== 'channel' && (() => {
            const kind = s.origin === 'code' ? 'code project' : 'loop'
            const label = s.source_label || s.source_id || kind
            const canOpen = !!s.source_id
            // A worker that names no loop (its engine tagged it, its key names none) has no
            // cockpit to open, so its chip is provenance, not a control. It used to render as a
            // permanently disabled button element: announced as a button that can never be
            // pressed in ANY state, which no reason could ever unblock. A span is what it actually
            // is; the tag now follows whether there is somewhere to go.
            // (Comment deliberately spells no literal button tag — the primitive-adoption ratchet
            // counts raw source, comments included, so prose markup reds CI.)
            const chip = 'inline-flex items-center gap-1 rounded-pill px-1.5 h-[18px] text-[0.75rem] transition-colors'
            const tint = { background: 'color-mix(in srgb, var(--color-secondary) 18%, transparent)', color: 'var(--color-secondary)' }
            const glyph = s.origin === 'code' ? <CodeIcon size={10} /> : <Target size={10} />
            if (!canOpen) {
              return (
                <span title={`From a ${kind}`} className={`${chip} cursor-default`} style={tint}>
                  {glyph}
                  {label}
                </span>
              )
            }
            return (
              <button type="button"
                onClick={(e) => { e.stopPropagation(); navigate(`${s.origin === 'code' ? 'code' : 'loops'}/${s.source_id}`) }}
                title={`From ${kind} “${label}” — open its cockpit`}
                className={`${chip} hover:brightness-125 cursor-pointer`}
                style={tint}>
                {glyph}
                {label}
              </button>
            )
          })()}
          <StartedByApp s={s} />
          {(s.tags ?? []).map((tid) => tagById[tid] && (
            <span key={tid} className="inline-flex items-center rounded-pill px-1.5 h-[18px] text-[0.75rem]"
              style={{ background: `color-mix(in srgb, ${tagById[tid].color || 'var(--color-primary)'} 18%, transparent)`, color: tagById[tid].color || 'var(--color-primary)' }}>{tagById[tid].name}</span>
          ))}
        </div>
      </div>
      {/* assign folder + tags */}
      <SessionOrgMenu orgLoadFailed={!!foldersError || !!tagsError} s={s} folders={folders} tags={tags} onSetFolder={setFolder} onToggleTag={toggleTag} />
      <SquareIconButton label={s.pinned ? 'Unpin chat' : 'Pin chat'} title={s.pinned ? 'Unpin' : 'Pin to top'} on={s.pinned}
        onClick={(e) => { e.stopPropagation(); togglePin(s.key, !s.pinned) }}
        className={`shrink-0 transition-opacity ${s.pinned ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}`}>
        <Pin size={14} className={s.pinned ? 'fill-current' : ''} />
      </SquareIconButton>
      <IconButton icon={Trash2} label="Delete chat" onClick={(e) => { e.stopPropagation(); del(s) }} size={26} iconSize={14}
        tone="danger"
        className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100" />
    </div>
    </ContextMenu>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<span data-type="title-l" className="text-on-surface">{roomsScope ? 'Agent Rooms' : 'Chat history'}</span>}
        // Every header action here acts on the SESSION list — a view switcher for chat cards, a
        // re-tag job over chats, a chat folder, a new chat. In the Rooms scope they would be
        // controls for the list that is not on screen, so the scope carries its own action
        // (`RoomsScope`'s "New room") and the header keeps only the title.
        right={roomsScope ? undefined : <HeaderActions className="max-w-[60vw]">
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
          {/* `|| roomsAvailable` is load-bearing: a fresh install with Agent Rooms turned on has
              ZERO chats, and without this the whole control strip — including the Rooms tab — was
              hidden by the chats empty state, so the only route to a room would have been to type
              the URL. */}
          {(sessions === null || sessions.length > 0 || roomsAvailable) && (<>
            {/* Origin scope — only shown once worker chats exist (otherwise the
                history is all-manual and the tabs would be noise). Defaults to the
                user's own chats; loop/code workers live behind their tabs.
                Rooms join it the moment `/api/rooms` answers at all, which is the
                moment the feature is switched on — including at zero rooms, because
                the tab is how the first one gets made. */}
            {(originCounts.loop > 0 || originCounts.code > 0 || originCounts.channel > 0 || roomsAvailable) && (
              <div className="mb-m">
                <Segmented ariaLabel="Chat origin" value={origin} onChange={(v) => setOrigin(v as typeof origin)}
                  options={[
                    { key: 'manual', label: `Chat Sessions${originCounts.manual ? ` ${originCounts.manual}` : ''}` },
                    ...(originCounts.loop > 0 ? [{ key: 'loop', label: `Loops ${originCounts.loop}` }] : []),
                    ...(originCounts.code > 0 ? [{ key: 'code', label: `Code ${originCounts.code}` }] : []),
                    ...(originCounts.channel > 0 ? [{ key: 'channel', label: `Channels ${originCounts.channel}` }] : []),
                    ...(roomsAvailable ? [{ key: 'room', label: `Rooms${roomCount ? ` ${roomCount}` : ''}` }] : []),
                    { key: 'all', label: 'All' },
                  ]} />
              </div>
            )}
            {/* Every control below narrows the SESSION list, so none of them applies to the Rooms
                scope — leaving them mounted there would offer a search that filters nothing and a
                tag row for a noun that has no tags. The scope swaps the body, so it swaps its
                controls with it. */}
            {!roomsScope && (<>
            <div className="mb-m">
              <SearchField value={q} onChange={setQ} placeholder="Search chats — title or anything said"
                ariaLabel="Search chats" autoFocus />
              {/* 🪤 `origin !== 'all'` would be WRONG here: this surface OPENS on 'manual', so the
                  comparison is true before the user has done anything and the list would announce its
                  length at rest. Compared against its own default, like inbox's 'open' and loops'
                  'active'. `showArchived` is deliberately absent — the active/archived split is
                  enforced server-side, so it swaps WHICH list is fetched rather than narrowing this
                  one. */}
              <ResultAnnouncement count={filtered.length} noun="chats"
                active={!!n || origin !== 'manual'} />
              {/* How the "things I said" content search was resolved: the FTS index,
                  or the bounded transcript-scan fallback. A quiet legibility caption, not a
                  control — shown only once a content search has resolved with a known source. */}
              {searchSourceLabel(contentSource) && (
                <span data-type="caption" className="mt-1 block text-on-surface-low">
                  {searchSourceLabel(contentSource)}
                </span>
              )}
              {/* A content search that could not look in every chat says so, with the way to
                  read the rest directly — never a partial answer that reads as the whole one. */}
              {coverage && (
                <PartialNotice complete={false} verb="Searched" shown={coverage.shown} total={coverage.total}
                  what="chats" detail={coverage.detail} className="mt-1"
                  action={{
                    label: `Search the other ${coverage.rest.toLocaleString()} directly`,
                    onClick: () => { setRestBusy(true); setRestFor(q.trim()) }, busy: restBusy,
                  }} />
              )}
              {contentKeys && unlisted > 0 && (
                <PartialNotice complete={false} shown={contentKeys.size} total={contentKeys.size + unlisted}
                  what="matching chats" className="mt-1"
                  detail={`the search lists its ${contentKeys.size.toLocaleString()} best; narrow it to find the others.`} />
              )}
              {contentFailure && (
                <InlineError icon multiline className="mt-2" onRetry={() => setContentRetry((n) => n + 1)}>
                  {contentFailure} Only titles and previews are matched below.
                </InlineError>
              )}
            </div>
            {/* Active / Archived. Archived chats keep their transcript AND stay
                searchable — the copy says so, because an "archive" that people read as
                "delete" is one they never use. */}
            <div className="mb-m flex items-center gap-2">
              <Segmented ariaLabel="Chat lifecycle" value={showArchived ? 'archived' : 'active'}
                onChange={(v) => { clearSelection(); setShowArchived(v === 'archived') }}
                options={[{ key: 'active', label: 'Active' }, { key: 'archived', label: 'Archived' }]} />
              {showArchived && (
                <span className="text-on-surface-low text-[0.75rem]">
                  Archived chats stay searchable — restore any of them at any time.
                </span>
              )}
            </div>
            {/* Selection bar — appears only while something is selected, so the
                default list stays uncluttered. */}
            {selecting && (
              <div className="mb-m flex flex-wrap items-center gap-2 rounded-lg bg-surface-low px-m py-2 ring-1 ring-outline-variant/40">
                <span data-type="label-l" className="text-on-surface">{selected.size} selected</span>
                {showArchived ? (
                  <Button variant="tonal" size="xs" disabled={bulkBusy} disabledReason={BUSY_REASON} onClick={() => runBulk('restore')}>
                    <ArchiveRestore size={13} /> Restore
                  </Button>
                ) : (
                  <Button variant="tonal" size="xs" disabled={bulkBusy} disabledReason={BUSY_REASON} onClick={() => runBulk('archive')}>
                    <Archive size={13} /> Archive
                  </Button>
                )}
                <Button variant="ghost" size="xs" disabled={bulkBusy} disabledReason={BUSY_REASON}
                  onClick={() => runBulk('never_archive', { value: true })}
                  title="Exempt these chats from auto-archive">
                  <Pin size={13} /> Never archive
                </Button>
                <Button variant="ghost" size="xs" onClick={clearSelection}>Clear</Button>
              </div>
            )}
            {/* The outcome of the last bulk action, OUTSIDE the selection bar so it
                survives the bar unmounting — "38 archived · 2 not found" is the
                answer to what just happened and must stay readable. */}
            {bulkNote && !selecting && (
              <div role="status" className="mb-m text-on-surface-var text-[0.8125rem]">{bulkNote}</div>
            )}
            {tags.length > 0 && (
              // 🔴 Which tags were filtering was readable only as a tinted pill. `role="group"` goes on
              //    the row that already exists — wrapping only the chips would make them ONE flex item
              //    and break the wrap + gap — and the visible "Filter:" is a bare `<span>`, which labels
              //    nothing, so the group states the dimension itself.
              <div role="group" aria-label="Filter by tag" className="mb-m flex flex-wrap items-center gap-1.5">
                <span className="text-on-surface-low text-[0.75rem] mr-1">Filter:</span>
                {tags.map((t) => {
                  const on = tagFilter.has(t.id)
                  return (
                    <button key={t.id} type="button" aria-pressed={on} onClick={() => { const nx = new Set(tagFilter); nx.has(t.id) ? nx.delete(t.id) : nx.add(t.id); setTagFilter(nx) }}
                      className="inline-flex items-center gap-1 rounded-pill px-2 h-7 text-[0.75rem] transition-colors"
                      style={on ? { background: `color-mix(in srgb, ${t.color || 'var(--color-primary)'} 22%, transparent)`, color: t.color || 'var(--color-primary)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>
                      <TagIcon size={11} /> {t.name}
                    </button>
                  )
                })}
                {tagFilter.size > 0 && <Button variant="ghost" size="xs" onClick={() => setTagFilter(new Set())} className="h-6 px-1 text-[0.75rem] text-on-surface-low">Clear</Button>}
              </div>
            )}
            </>)}
          </>)}
        </div>

        {/* The Rooms scope swaps the BODY, ahead of every session load state: the session read's
            outcome says nothing about rooms, so a failed `/api/chat/sessions` must not replace the
            rooms list with a chat LoadError. */}
        {roomsScope ? (
          <div className="flex-1 min-h-0 overflow-y-auto">
            <div className="mx-auto w-full px-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
              <RoomsScope rooms={roomsData} error={roomsError} loading={false}
                onRefresh={refreshRooms} navigate={navigate} />
            </div>
          </div>
        )
          : sessions === null && sessionsError ? <div className="flex-1 min-h-0"><LoadError what="chats" error={sessionsError} onRetry={refreshSessions} /></div>
          : sessions === null ? <div className="flex-1 min-h-0"><ListSkeleton rows={6} what="chats" /></div>
          : sessions.length === 0 ? <div className="flex-1 min-h-0"><EmptyState icon={MessageSquare} title="No chats yet" hint="Start a conversation — your sessions will appear here to search and revisit." action={{ label: 'New chat', onClick: () => navigate('chat/new'), icon: Edit3 }} /></div>
          : filtered.length === 0 ? <div className="flex-1 min-h-0">{
              /* Reachable through THREE narrowing controls (search, tag filter, origin scope) — and
                 through NO control at all, when every loaded chat is a worker session hidden by the
                 default 'manual' scope. The old single state ("No matches / Try a different search or
                 tag filter") blamed the search at a user who only switched scope, and offered no way
                 out. Same split as the tasks/code lists (emptyStateNoMatch): name the control that
                 actually narrowed, offer the escape that undoes it, and count what is really loaded
                 so the state cannot read as "you have no chats". Search wins the blame when both
                 narrow; the view escape resets tags AND scope — 'all' genuinely shows everything, so
                 unlike tasks there is no scope-only third case. `showArchived` is deliberately not a
                 narrower here: it swaps WHICH list is fetched (server-side), not what this one shows. */
              n ? (
                <EmptyState icon={Search} title={`No chats match “${q.trim()}”`}
                  hint={`You have ${sessions.length} chat${sessions.length === 1 ? '' : 's'} — just none matching the search.`}
                  action={{ label: 'Clear search', onClick: () => setQ('') }} />
              ) : (
                <EmptyState icon={Filter} title="No chats in this view"
                  hint={`You have ${sessions.length} chat${sessions.length === 1 ? '' : 's'} — just none in this view.`}
                  action={{ label: 'View all chats', onClick: () => { setTagFilter(new Set()); setOrigin('all') } }} />
              )
            }</div>
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
                      <div className="mb-2 flex items-center gap-1.5 text-on-surface-var text-[0.8125rem]" style={fvs(500)}>
                        <Folder size={14} /> {g.folder.name}
                        <span className="text-on-surface-low">({g.items.length})</span>
                      </div>
                    )}
                    {g.items.length === 0 ? <div className="text-on-surface-low text-[0.8125rem] italic pl-5">{folderDragKey ? 'Drop here to move into this folder' : 'Empty'}</div>
                      : (
                        // THE surface windowing was first deferred on ("pending
                        // measurement"). `/api/chat/sessions` is uncapped at BOTH ends — no
                        // server page size, no client slice — so this is the one list in the
                        // app that really does reach 5,000 rows. Measured on a real store of
                        // exactly that: 175,683 DOM nodes, 273ms per wheel event.
                        // Windowed per FOLDER GROUP, which composes: each group observes the
                        // same shared scroller and windows its own rows, so a 5,000-chat
                        // ungrouped bucket windows while a 3-chat folder passes through.
                        <WindowedList
                          items={g.items}
                          rowKey={(s) => s.key}
                          // VARIABLE, measured: a plain row is 66px (measured across all
                          // 5,000 fixture rows), but a full-text hit adds a snippet line and
                          // the meta line wraps its origin/tag pills.
                          rowHeights="variable"
                          estimateRowHeight={66}
                          gap={8}
                          noun="chats"
                          findHint="use the Search chats field above, which searches every chat including their contents."
                          anchorKey={peekKey || undefined}
                          className="flex flex-col gap-s"
                        >
                          {(s) => card(s)}
                        </WindowedList>
                      )}
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
          <SidePanel key={peekKey} title={peekSession ? sessionTitle(peekSession) : peekKey} icon={<MessageSquare size={18} className="text-primary" />}
            storeKey="chat-peek-w" fillHeight urlKey={{ key: 'peek', setQuery }}
            onExpand={() => navigate(chatFindPath(peekKey, q))}
            onClose={() => setPeekKey('')}>
            <SessionPeekBody sessionKey={peekKey} onOpen={() => navigate(chatFindPath(peekKey, q))} />
          </SidePanel>
        )}
      </AnimatePresence>
      </div>
    </div>
  )
}

/** Per-session folder + tag assignment menu (hover-revealed in a row). */
function SessionOrgMenu({ s, folders, tags, orgLoadFailed, onSetFolder, onToggleTag }: {
  s: ChatSessionSummary; folders: ChatFolder[]; tags: ChatTag[]
  /** The folder/tag reads failed, so an empty list is not evidence that none exist — without this
   *  the menu instructs the user to create what they may already have. */
  orgLoadFailed?: boolean
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
      {/* A menu trigger: `Popover` renders `trigger(open, toggle)` and adds no ARIA of its own, so the
          caller owns it — `FilterMenu`, `HeaderActions` and the composer's controls all pass
          `aria-expanded={open}` on their own raw buttons. This one could not until the primitive learned
          the prop, so it announced `aria-pressed` for an open menu. */}
      <Popover width={240} align="right" placement="bottom" portal trigger={(open, toggle) => (
        <SquareIconButton icon={TagIcon} label="Organize chat" title="Folder & tags" ariaExpanded={open} onClick={toggle}
          className={`shrink-0 transition-opacity ${open ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}`} />
      )}>
        {() => (
          <div className="max-h-[320px] overflow-y-auto py-1">
            {folders.length > 0 && <div className="px-m pt-1 pb-0.5 text-[0.75rem] uppercase tracking-wide text-on-surface-low">Folder</div>}
            {folders.length > 0 && (
              <MenuRow label="— none —" selected={!s.folder_id} onClick={() => onSetFolder(s.key, null)} />
            )}
            {folders.map((f) => <MenuRow key={f.id} label={f.name} icon={<Folder size={14} />} selected={s.folder_id === f.id} onClick={() => onSetFolder(s.key, f.id)} />)}
            {tags.length > 0 && <div className="px-m pt-2 pb-0.5 text-[0.75rem] uppercase tracking-wide text-on-surface-low">Tags</div>}
            {tags.map((t) => (
              <MenuRow key={t.id} label={t.name} selected={(s.tags ?? []).includes(t.id)} onClick={() => onToggleTag(s.key, t.id)} />
            ))}
            {/* A failed read must not instruct the user to create what they may already have. */}
            {orgLoadFailed && folders.length === 0 && tags.length === 0
              ? <div className="px-m py-2"><FieldError>Couldn't load your folders and tags</FieldError></div>
              : folders.length === 0 && tags.length === 0 && <div className="px-m py-2 text-[0.8125rem] text-on-surface-low">Create a folder or tag first.</div>}
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
            <div className="mb-2 flex items-center gap-1.5 px-1 pt-1 shrink-0 text-[0.8125rem]" style={withWeight({ color: c.color || 'var(--color-on-surface)' }, 550)}>
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


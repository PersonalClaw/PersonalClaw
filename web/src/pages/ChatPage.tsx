import { useEffect, useState } from 'react'
import { reportActionFailure, reportingWrite } from '../app/reportingWrite'

import { motion, AnimatePresence } from 'framer-motion'
import { Edit3, History, MessageSquare, PanelRight, Pencil, Sparkles, Link2, Loader2, ArrowLeft, Bot, ShieldCheck, Shield, Eye, Zap, ClipboardList, Hammer, NotebookPen, FolderCog, ListTree, Send } from 'lucide-react'
import { IconButton } from '../ui/IconButton'
import { TopBar } from '../ui/TopBar'
import { Button } from '../ui/Button'
import { HeaderActions, HeaderControl, HeaderModePill } from '../ui/HeaderActions'
import { ClawMark } from '../ui/ClawMark'
import { type RoutingSuggestion } from './chat/RoutingChip'
import { workingDirectoryPrompt, workingDirectoryToast } from './chat/workingDirectory'
import { chatContextChips } from './chat/ChatContextLine'
import { DotGlow } from '../ui/DotGlow'
import { EmptyState, LoadError, Skeleton, LoadingStatus } from '../ui/ListScaffold'
import { NeedsModelNote } from './chat/NoModelSetupState'
import { RoomView } from './chat/RoomView'
import { confirm, promptInput } from '../ui/dialog'
import { useIdentity, firstNameOf } from '../app/identity'
import { notify } from '../app/appSdk'
import { spring } from '../design/motion'
import { api, type ApprovalMode, type TaskMode, type SessionTemplate, type ChannelRuntime } from '../lib/api'
import { refreshKinds, useChatSocket } from '../lib/useChatSocket'
import { useChatVoice } from './chat/useChatVoice'
import { useChatComposer } from './chat/composer/useChatComposer'
import { ChatComposer } from './chat/composer/ChatComposer'
import { useChatStream } from './chat/useChatStream'
import { useTranscript } from './chat/transcript/useTranscript'
import { Transcript } from './chat/transcript/Transcript'
import { MessagesSkeleton } from './chat/transcript/MessagesSkeleton'
import { SessionMapReturnLatest, scrollToLatest } from './chat/SessionMapReturnLatest'
import { ChatHistoryPage } from './chat/history/ChatHistoryPage'
import { useChatPanes } from './chat/panes/useChatPanes'
import { ChatPanes, ToolResultModal } from './chat/panes/ChatPanes'
import { useQuery, invalidateKeys } from '../lib/data'
import { sessionTitle } from '../lib/sessionTitle'
import { type RouteProps } from '../app/useQueryState'
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
  // The panels docked beside the chat and a tool's full result (`chat/panes/useChatPanes`).
  const panes = useChatPanes({ query, setQuery, stream })
  const { activityOpen, setActivityOpen, historyOpen, setHistoryOpen, setOpenFile } = panes
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
  const { followNewTurn, scrolledUp, endRef, isMobile, mapOpen, setMapOpen } = transcript

  // Read-aloud: Speak, "Speak replies aloud" and the audio a reading streams (`chat/useChatVoice`).
  const { voiceCfg, speakingTurn, speak, replyFinished, oweSpokenReply, enqueueAudio, readingRef } = useChatVoice({ sessionRef, turns, notice })

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

        <ChatPanes panes={panes} stream={stream} transcript={transcript} started={started} setQuery={setQuery}
          navigate={navigate} send={send} />
      </div>
      <ToolResultModal panes={panes} />
    </div>
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

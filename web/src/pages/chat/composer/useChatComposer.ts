import { useEffect, useMemo, useRef, useState } from 'react'
import { failureSentence, reportActionFailure } from '../../../app/reportingWrite'
import { notify } from '../../../app/appSdk'
import { usePlatform } from '../../../app/usePlatform'
import { useSyncedDraft } from '../../../ui/forms'
import { useComposerNotice } from '../../../ui/composer/ComposerNotice'
import { useScreenShare } from '../../../ui/composer/useScreenShare'
import { chooseCaptureProvider, cropToPngFile, displayCaptureSupported, grabOneFrame, type SnipRect } from '../../../ui/composer/displayCapture'
import { optimizeFailure, optimizeOutcome } from '../../../ui/composer/optimizeOutcome'
import type { ComposerValue } from '../../../ui/composer/types'
import { useWidgetActionBridge, takePendingWidgetAction } from '../../../ui/widget/useWidgetActionBridge'
import { api, ApiError, hasApiCode, transcriptionFailure, type ApprovalMode, type TaskMode, type ReasoningEffort, type DiscoveredAgent, type MemoryMode, type SessionTemplate } from '../../../lib/api'
import { idleRunState } from '../../../lib/workPhase'
import { rewindPreviewText } from '../../../lib/rewindPreview'
import { useComposerData } from '../../../lib/useComposerData'
import { deliverableToOpenSession } from '../sessionDelivery'
import { useReplyStyle } from './useReplyStyle'
import { sessionTemplatePatch } from '../sessionTemplate'
import { buildOptimizerContext } from '../optimizerContext'
import { type PasteBlock, shouldCollapsePaste, nextSeq, makePasteId, markerFor, pruneBlocks, asSent, takeIn } from '../pasteBlocks'
import type { TurnPaste } from '../PasteChip'
import type { RoutingSuggestion } from '../RoutingChip'
import { type HistMsg, userTurn, assistantTurn, hydrateTurns, turnText } from '../chatTypes'
import { readCachedDetail, writeCachedDetail, type ChatDetail, type ChatStream } from '../useChatStream'
import { contextReport } from '../../../ui/composer/ContextDisclosure'
import { repliesToSpeak } from '../useChatVoice'

// ── The composer's controller ─────────────────────────────────────────────────────────────────
//
// Everything that decides the next turn and sends it: the draft and its recall history, the
// mentioned files, knowledge and artifacts, the pasted blocks, the uploads and screen captures,
// optimize, the slash commands, the agent/model/approval/task-mode selection and its persistence,
// the reply style, the memory mode and project of a chat not yet started, the routing chip, Stop,
// and `send` itself, which creates the session on a new chat. The chat's other regions send
// through it (follow-up chips, widget actions, a comment on an open file). `ChatComposer.tsx`
// draws it.


/** How long a permission or task mode pick waits for the gateway before the control says it did
 *  not answer and the next pick may go. Each call answers in well under a second; one that has not
 *  answered in 15 s is not going to, and holding every later pick behind it left both controls
 *  looking dead until a reload. */
export const MODE_PICK_DEADLINE_MS = 15_000
/** How many times the composer reads a chat's mode back after a pick it could not confirm, and how
 *  long it waits between reads that fail: about a minute of a gateway that does not answer. */
export const MODE_READ_TRIES = 5
export const MODE_READ_RETRY_MS = 12_000

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
  '- `/goal <task>` — start a goal loop on the task instead, from a new chat: it works cycle by cycle until the goal is met',
  '- `/tools` — open the Tools page',
  '- `/undo [N]` — roll back the last N conversation turns (default 1; side effects are not reverted)',
  '- `/rewind-to-turn N` — put back the files the agent edited after turn N, with its own file tools or as an agent CLI that asked first (preview first; add `--confirm` to apply). A command\'s changes, and an agent CLI\'s edits made without asking, have no backup: the preview lists the files they changed in the chat\'s folder, and those stay. The conversation is not rewound.',
  '- `/compact` — compact the conversation to free up context',
  '- `/context` — show what fills the context window, part by part',
  '',
  'Type `/` in the message box any time to see and filter the full list.',
].join('\n')

/** What `/goal` says in a chat that has started: a goal loop is a thread of its own. */
const GOAL_FROM_A_NEW_THREAD = 'A goal loop starts from a new thread: open **New thread** (⌘N) and type `/goal` and the goal there, or choose **Start a goal loop** from its **+** menu.'

/** One of her earlier messages for ↑/↓ recall: its text as she wrote it, and the blocks she
 *  pasted into it, which come back to the composer with it. */
export interface RecalledPrompt { text: string; pastes: TurnPaste[] }

/** *list* with *entry* last: a message sent again as it was is one entry, not two. */
export function withPrompt(list: RecalledPrompt[], entry: RecalledPrompt): RecalledPrompt[] {
  const last = list[list.length - 1]
  if (!last || last.text !== entry.text || JSON.stringify(last.pastes) !== JSON.stringify(entry.pastes)) list.push(entry)
  return list
}

export function useChatComposer({ sessionId, navigate, seed, initialProjectId, initialAgent, initialTaskMode = 'agent', pendingRouting, setRoutingSuggestion, setLiveRun, started, stream, followNewTurn, oweSpokenReply }: {
  sessionId: string | null
  navigate: (p: string, opts?: { replace?: boolean }) => void
  /** The draft a launch pre-fills (`?seed=`). */
  seed: string
  /** The project a launch scopes the new chat to (`?project=`). */
  initialProjectId: string
  /** The agent a launch picks (`?agent=`). */
  initialAgent: string
  /** The task mode a new chat starts in (`?mode=`). Shown only: it reaches the gateway when the
   *  person sends, as a mode picked on the pill does. An existing chat takes its own. */
  initialTaskMode?: TaskMode
  /** ChatPage's pending routing suggestion, held above the create-remount. */
  pendingRouting: RoutingSuggestion | null
  setRoutingSuggestion: (s: RoutingSuggestion | null) => void
  /** ChatPage's live-run handoff, set as a send creates the session. */
  setLiveRun: (s: string) => void
  started: boolean
  stream: ChatStream
  /** The transcript follows the turn a send asks for. */
  followNewTurn: () => void
  /** A send owes its reply a reading when "Speak replies aloud" is on (`useChatVoice`). */
  oweSpokenReply: (sid: string) => void
}) {
  const { sessionRef, turns, setTurns, patchLastAssistant, dropTextRun, streamingRef, markStreaming, sayTurnEnded, turnSeqRef, stoppedTurnRef, takesSteersRef, setMissing, setSteered, followups, setFollowups, followupsNeed, setFollowupsNeed, checkWorkOffer, setCheckWorkOffer, sessionBindingRef, bindingToldRef, bindingNonce } = stream
  const data = useComposerData()

  // The project this chat scopes under. Seeded from the launch URL (?project=<id> from a
  // project page's Chat button), but ALSO user-pickable on a bare new chat via the
  // composer's project chooser (the vision's "optional project chooser"). Frozen once the
  // session starts (project_id is fixed at create, like memory mode).
  const [projectId, setProjectId] = useSyncedDraft(initialProjectId)

  const [input, setInput] = useState(seed)
  const [composerFocused, setComposerFocused] = useState(false)
  const [promptPaletteOpen, setPromptPaletteOpen] = useState(false)
  // Bumped to open the model / agent / effort / project pickers for the "/model",
  // "/agent", "/effort" and "/project" GUI-affordance slash commands (see handleSlashCommand).
  const [openModelSignal, setOpenModelSignal] = useState(0)
  const [openAgentSignal, setOpenAgentSignal] = useState(0)
  const [openReasoningSignal, setOpenReasoningSignal] = useState(0)
  const [openProjectSignal, setOpenProjectSignal] = useState(0)
  const composerRef = useRef<HTMLDivElement>(null)

  // Agent routing suggestion: a non-blocking chip proposing a
  // better-fit specialist for this default-agent chat. Cleared on send / agent switch;
  // arrives EITHER on the routing_suggestion WS push (an already-open session) or on
  // the send response (a session this send just created — its push cannot reach the
  // socket the create-remount closed). The pending payload is held by ChatPage, above
  // that remount; here it is DERIVED, so a suggestion the server named for another
  // session cannot render against this one no matter which transport carried it.
  const routingSuggestion = deliverableToOpenSession(pendingRouting?.session, sessionId)
    ? pendingRouting
    : null
  // Opening a DIFFERENT session retires a pending suggestion — the moment it was
  // proposed for has passed. Same resolution, so this cannot disagree with the render
  // gate above; it just stops a stale chip resurfacing when the user navigates back.
  useEffect(() => {
    if (pendingRouting && !deliverableToOpenSession(pendingRouting.session, sessionId)) setRoutingSuggestion(null)
  }, [sessionId])

  // composer extras: @-mentioned file paths (sent as meta.files) + large-paste
  // blocks (collapsed to cards + inline [Paste #N] markers, expanded on send).
  const [mentionedFiles, setMentionedFiles] = useState<string[]>([])
  // @-mentioned knowledge library items (id+name) → threaded into send meta.knowledge;
  // the backend inlines each item's content for the turn.
  const [mentionedKnowledge, setMentionedKnowledge] = useState<{ id: string; name: string }[]>([])
  const [knowledgePickerOpen, setKnowledgePickerOpen] = useState(false)  // "Add knowledge to prompt" picker
  // @-mentioned artifacts (slug+name) → threaded into send meta.artifacts; the backend
  // inlines each one's CURRENT version for the turn (referencing an artifact means
  // "what it is now", not a pinned snapshot) and records a `referenced` event.
  const [mentionedArtifacts, setMentionedArtifacts] = useState<{ slug: string; name: string }[]>([])
  const [artifactPickerOpen, setArtifactPickerOpen] = useState(false)
  const [pasteBlocks, setPasteBlocks] = useState<PasteBlock[]>([])
  // uploaded-attachment workspace paths (threaded into send meta.files) +
  // composer extras: prompt history (↑/↓), context-usage %, optimize-in-flight,
  // memory mode for the next NEW session, queued-while-streaming message.
  const [attachedPaths, setAttachedPaths] = useState<string[]>([])
  // Which provider the "Capture screen area" entry uses — macOS keeps the native
  // `screencapture -i` snip, everywhere else grabs a frame in the browser and crops it
  // in-app. One decision point (chooseCaptureProvider), two providers.
  const platform = usePlatform()
  const displayCapture = displayCaptureSupported()
  const captureProvider = chooseCaptureProvider(platform, displayCapture)
  // A captured frame awaiting a crop. Non-null = SnipOverlay is up; the capture is
  // ALREADY stopped by then, so nothing is watching the screen while the user crops.
  const [snip, setSnip] = useState<{ url: string; width: number; height: number; source: HTMLCanvasElement } | null>(null)
  // Live upload progress: one row per file still uploading, from every attach in flight (a second
  // paste does not replace the first's rows). Each row names the attach it came from (`batch`), so
  // an attach that finishes takes down its own rows and no one else's.
  // `finishing`: every byte of that file is in and the gateway is completing it, so it can no
  // longer be cancelled (`lib/chunkedUpload`) and its row stops offering to.
  const [uploads, setUploads] = useState<{ batch: number; name: string; pct: number; finishing?: boolean }[]>([])
  // Each in-flight attach's AbortController, by batch, so a row's Cancel stops the upload it shows.
  const uploadAborts = useRef(new Map<number, AbortController>())
  const uploadBatch = useRef(0)
  // 🔴 A MESSAGE IS NOT SENT WHILE A FILE IT CARRIES IS STILL UPLOADING. The file has no path until
  // its upload answers, so a message sent before then went without it — and on a new chat that send
  // creates the chat and remounts this component, so the upload that finished afterwards landed in
  // an instance that was gone: the file was stored and attached to nothing, with nothing said. While
  // any row is up, Send is off and says this, and `send` refuses with it.
  const uploadCancellable = uploads.some((u) => !u.finishing)
  const uploadHold = uploads.length === 0 ? ''
    : uploads.length === 1 ? `Wait for ${uploads[0].name} to finish uploading${uploadCancellable ? ', or cancel it' : ''}.`
    : `Wait for ${uploads.length} files to finish uploading${uploadCancellable ? ', or cancel them' : ''}.`
  // Her earlier messages for ↑/↓ recall, each with the blocks she pasted into it, which come back
  // to the composer with it (`recallPrompt`).
  const [promptHistory, setPromptHistory] = useState<RecalledPrompt[]>([])
  const historyTexts = useMemo(() => promptHistory.map((h) => h.text), [promptHistory])
  const [optimizing, setOptimizing] = useState(false)
  // the draft as it was just before an optimize-prompt rewrite, so the user can
  // revert if they don't like the optimized version (otherwise it's lost).
  const [preOptimize, setPreOptimize] = useState<string | null>(null)
  // The line above the composer that says what failed (it stays until dismissed, or until the
  // next send) or what just happened (it clears on its own): `ui/composer/ComposerNotice`.
  const notice = useComposerNotice()
  // Screen context. The HOST owns the display stream because it
  // also owns the header chip that must stay lit for the stream's whole life — a
  // composer-local stream could not keep a header indicator honest. Errors ride the
  // composer's notice line rather than a second mechanism.
  const screenShare = useScreenShare(sessionId ?? '', notice.showError)
  const [toast, setToast] = useState<string | null>(null)  // transient confirmation (brief/workspace-dir)
  // Upload rejection (oversize / upload failure) — a message the user must ACT on
  // (pick a smaller file), so it's dismissible-but-persistent, on a line of its own so a
  // later notice cannot replace it.
  const [attachError, setAttachError] = useState<string | null>(null)
  // A chat's memory mode is fixed when it is made, so the copy a chat paints from (seeded by its
  // first send, or kept from a visit moments ago) already says it. Start there, not at Persistent,
  // so a private chat never reads as kept (its tint, its notice, its Execution Boundary control)
  // until its read lands, or for the whole visit when the read fails.
  const [memoryMode, setMemoryMode] = useState<MemoryMode>(() => ((sessionId && readCachedDetail(sessionId)?.memory_mode) || 'persistent') as MemoryMode)

  // In-flight session-creation promise. ensureSession checks a ref then awaits an
  // async create; two sends fired back-to-back on a brand-new chat (send #1, then
  // send #2 before create resolves) would BOTH see sessionRef=null and create two
  // sessions — send #2's turn lands in an orphaned session and is silently lost.
  // Memoizing the promise makes concurrent callers await the SAME creation.
  const ensureInFlightRef = useRef<Promise<string> | null>(null)
  const [selection, setSelection] = useState<ComposerValue>({ agent: initialAgent, model: 'Auto', approval: 'normal', taskMode: sessionId ? 'agent' : initialTaskMode, reasoning: '' })
  // Whether the selection shows the chat's own posture: at once on a new chat, and once the first
  // read is in on an existing one. Until then its task mode is a default, not the chat's.
  const [postureKnown, setPostureKnown] = useState(!sessionId)
  const replyStyle = useReplyStyle(() => sessionRef.current)

  // Restore the composer selection from the resumed session's binding, once both
  // the binding (from detail) and the discovered-agent catalog have loaded. ACP
  // sessions store provider + provider_agent (+ effort) → map back to the
  // discovered agent's DISPLAY name; native sessions use agent/model directly.
  useEffect(() => {
    const b = sessionBindingRef.current
    if (!b) return
    const told = bindingToldRef.current
    bindingToldRef.current = false
    // reasoning_effort is a per-turn session SETTING (no longer an effort-agent),
    // so restore it for BOTH native and ACP sessions.
    const reasoning = (b.reasoning_effort || '') as ReasoningEffort
    if (b.acp_provider) {
      const list = data.discovered?.[b.acp_provider] ?? []
      const match = list.find((a) => a.provider_agent === b.acp_provider_agent)
      if (match) setSelection((s) => ({ ...s, agent: match.name, model: b.model || 'Auto', reasoning }))
    } else if (told || b.agent || b.model || reasoning) {
      setSelection((s) => ({ ...s, agent: b.agent, model: b.model || 'Auto', reasoning }))
    }
  }, [bindingNonce, data.discovered])

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

  // Widget action bridge: an interactive `<widget>` button posts widget-action, the
  // frame validates + republishes it, and this conversation sends it as the next
  // turn. Claiming the bridge here is what keeps a chat-born action in THIS session
  // rather than falling through to the shell's launcher.
  // `meta.label` is the genui dual payload's `humanFriendlyMessage`: the bubble
  // shows THAT, the model receives `text`. Absent for a raw-HTML widget, whose turn
  // text is its own visible text.
  useWidgetActionBridge((text, meta) => { void send(text, { uiLabel: meta.label }) })

  // A widget action raised from a NON-chat host (artifact-library preview, dashboard
  // tile band) staged its `[UI]` text and navigated here through `ne:launch-chat`.
  // Drain it once and send — the action's whole point is that it lands as a turn.
  useEffect(() => {
    const pending = takePendingWidgetAction()
    if (pending) void send(pending.text, { uiLabel: pending.label })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // The paste cards above the composer are the blocks whose `[Paste #N]` marker is still in the
  // draft — DERIVED from the draft on render, never synced back into state.
  //
  // 🔴 This used to be an effect that re-set `pasteBlocks` on every `input` change, and it was the
  // chat half of the composer's update loop (React #185). Its updater returned `prev` for an empty
  // list, but a same-value set still SCHEDULES a render — the keystroke has just updated this
  // component, so React cannot drop the update eagerly — and after a keystroke that render lands
  // inside the keystroke's own synchronous commit. Typing fast, every commit ended with one pending
  // and React counted them as nested updates until it threw (see `ui/composer/MarkdownInput`).
  //
  // Deriving also keeps a block whose marker comes BACK: "revert to original" restores the draft an
  // optimize rewrote, and pruning state on the rewrite had already thrown the pasted content away,
  // so the restored marker pointed at nothing and was sent as literal text. `send` and
  // `onLargePaste` read the full list on purpose — `send` expands and keeps only the markers present
  // in what it sends, and numbering over every block means a new paste can never reuse the number
  // of one whose marker is only hidden.
  const livePasteBlocks = useMemo(() => (pasteBlocks.length ? pruneBlocks(input, pasteBlocks) : pasteBlocks), [input, pasteBlocks])

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
        // session to the project's workspace + feeds its loop history/context.
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
        writeCachedDetail(created.key, { key: created.key, title: '', messages: seedMessages, run_state: idleRunState(created.key), memory_mode: memoryMode } as unknown as ChatDetail)
      }
      // Hand the live run ACROSS the remount, before causing it. This instance is about
      // to be destroyed and its `streaming` state with it; the replacement reads the
      // handoff at mount so the composer never advertises idle over a run the user just
      // started (#3444). Recorded here rather than after the send's round trip, because
      // the replacement mounts before that resolves — which is the whole window.
      setLiveRun(created.key)
      // Replace (not push) so the freshly-created session id backfills the URL
      // without adding a history entry — and via the router, not a raw
      // history.replaceState bypass.
      navigate(`chat/${created.key}`, { replace: true })
      // Same contract at session START: the composer already shows these picks, so a swallowed failure
      // means the brand-new session runs under settings the user can see but does not have.
      if (acp) await persistSelection('this agent', api.setSessionAcpAgent(created.key, { provider: acp.providerId, provider_agent: acp.agent.provider_agent, model: selection.model && selection.model !== 'Auto' ? selection.model : undefined }))
      if (selection.approval !== 'normal') await persistSelection('this approval mode', api.setApprovalMode(selection.approval, created.key))
      if (selection.taskMode !== 'agent') await persistSelection('this task mode', api.setTaskMode(selection.taskMode, created.key))
      // Persist a pre-start reasoning-effort pick (applySelection couldn't, since the
      // session didn't exist yet).
      if (selection.reasoning) await persistSelection('this reasoning effort', api.setReasoningEffort(created.key, selection.reasoning))
      await replyStyle.persistOnCreate(created.key)
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
      case '/goal':
        // A goal loop is a thread of its own (#3824): a new chat hands its draft to the loop composer,
        // which drafts and plans it as ever. A started chat is already its own thread, so it says
        // where a goal loop starts instead of sending the text to the model.
        setInput('')
        if (started) {
          setTurns((prev) => [...prev, userTurn(t), assistantTurn(GOAL_FROM_A_NEW_THREAD)])
          return true
        }
        startGoalLoop(arg)
        return true
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
      case '/context':
        // The same rows the Context control lists, from the same reading, so the two agree.
        setInput('')
        setTurns((prev) => [...prev, userTurn(t), assistantTurn(contextReport({ pct: stream.contextPct, window: stream.contextWindow, parts: stream.contextParts }))])
        return true
      default:
        return false
    }
  }

  async function send(
    text = input,
    opts?: { original?: string; inputOrigin?: string; uiLabel?: string },
  ) {
    const t = text.trim()
    if (!t) return
    // Sending dismisses any follow-up chips from the prior turn and
    // clears a pending routing suggestion — the moment passed.
    if (followups.length) setFollowups([])
    if (followupsNeed) setFollowupsNeed('')
    if (checkWorkOffer) setCheckWorkOffer(null)
    if (routingSuggestion) setRoutingSuggestion(null)
    // …and takes down the composer's notice: an error stays until it is dismissed or the
    // user sends again, and sending is the user moving on.
    notice.clear()
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
      // /rewind-to-turn N [--confirm] — restore FILES to their state at the end of turn N.
      // Two steps on purpose: bare form previews (reads only), --confirm writes. A
      // destructive filesystem action must be readable before it happens.
      const rw = t.match(/^\/rewind-to-turn\s+(\d+)(\s+--confirm)?$/i)
      if (rw) {
        setInput('')
        void rewindToTurn(parseInt(rw[1], 10), Boolean(rw[2]))
        return
      }
    }
    // GUI-affordance slash commands run instantly and never hit the model.
    if (!isStreaming && handleSlashCommand(t)) return
    // Optimize provenance: if this send carries an explicit `original` (from
    // /optimize), use it; otherwise the Sparkles preview path leaves the optimized
    // text in the input with `preOptimize` holding what the user first typed.
    const original = opts?.original ?? (preOptimize !== null && preOptimize.trim() !== t ? preOptimize.trim() : undefined)
    // Her message as it leaves the page, whichever way it goes from here (a steer, a queued send,
    // a send): `llmText` holds each block she pasted in place of its marker, which is what the
    // agent reads and her row keeps, and `turnPastes` are the blocks, which her row keeps beside it
    // so her bubble shows each as its chip (`asSent`). Her bubble keeps the markers she wrote.
    const { text: llmText, pastes: turnPastes } = asSent(t, pasteBlocks)
    // Mid-run send → ask for what the composer's mid-stream button said: STEER (inject into
    // the answer being written) on a turn that takes one, QUEUE on a turn that does not. The
    // label follows the gateway's word for the running turn (`takesSteers`), so the request
    // does too: a button that says Steer and sends `followup` — or says Steer over a turn
    // that cannot take one — is a lie either way.
    //
    // The server decides and says which it did: `{steered:true}` when the running
    // turn has a live drain path, `{queued:true}` when it does not (an ACP-backed
    // turn, or a turn that just ended). Either of those two OUTCOMES drops nothing —
    // a queued message still echoes `queue_push`, so the strip above the composer
    // shows it with its cancel affordance. We render the outcome rather than
    // assuming one.
    //
    // 🔴 BUT THERE IS A THIRD OUTCOME, AND IT USED TO DESTROY THE USER'S TEXT. The
    // sentence above says "either way nothing is dropped", and that was true of the
    // two SUCCESS shapes and false on REJECTION — which the `.catch(() => {})` hid.
    // `setInput('')` ran BEFORE the request, so a failed steer (gateway restart, a
    // 500, a dropped connection) emptied the composer, never pushed a `steered`
    // chip, and showed no error. The text was then nowhere: not on screen, not in
    // `queued`, not on the server. Nothing to copy and nothing to retry, while the
    // model kept streaming the answer the user was trying to correct.
    //
    // 🪤 THE DRAFT IS CLEARED ON SUCCESS, AND ONLY IF THE USER HAS NOT TYPED SINCE.
    // Moving `setInput('')` into the success path unconditionally would swap one
    // data-loss bug for another: the request is in flight for a round trip, and a
    // user who starts their next message during it would have it wiped. The
    // functional setter compares against exactly what was sent, so an untouched
    // composer clears and a re-typed one is left alone.
    //
    // `reportActionFailure` is this file's own convention for a user-initiated write
    // (11 other uses), and it is what `DesignCockpitPage.sendNudge` does for the
    // same shape — a rule `loops/loopActionReported.test.ts` states in prose as
    // "the nudge KEEPS its text on failure".
    //
    // 🔴 AND THERE IS A FOURTH OUTCOME — THE SERVER RAN IT AS A FRESH TURN. The two
    // success shapes above are the only ones this branch used to render, and the server
    // gates on ITS OWN `session.running`, not on our `queue_mode`: when the turn is
    // already over it ignores the steer, persists the message and dispatches a NORMAL
    // turn, answering `{ok, session}` with neither `steered` nor `queued`. On that path
    // the backend also suppresses its own user echo, on the standing contract that the
    // FE adds the bubble optimistically — which this branch does not. So a message sent
    // on a stale belief that a turn was running was accepted, persisted and RUNNING,
    // with nothing on screen anywhere: the composer cleared and the text was gone
    // (measured: `queue: []`, a persisted `user` row, no bubble, no reply). The server
    // needs no change — it already refuses to queue into a session that is not running.
    // The client has to believe what it answered.
    //
    // 🔑 This is the floor UNDER the streaming-claim fixes, not a duplicate of them.
    // The snapshot replay (a terminal frame that lands during a read replays AFTER it) stops
    // one stale claim being made and `resolveStalledStream` heals a claim that outlived its
    // turn — but both are corrections to a belief, and any
    // remaining way for `streamingRef` to be stale re-opens this branch. Nothing here
    // should lose a message even when the belief IS wrong.
    if (isStreaming) {
      // Stamped for the same reason the normal send path stamps one: the server stores
      // the ts we send, and Edit & resend locates a message by it.
      const steerTs = new Date().toISOString()
      const meta = turnPastes.length ? { client_ts: steerTs, pastes: turnPastes } : { client_ts: steerTs }
      ensureSession()
        .then((s) => api.sendChat(llmText, s, meta, takesSteersRef.current ? 'steer' : 'followup').then((r) => [s, r] as const))
        .then(([s, r]) => {
          setInput((cur) => (cur === t ? '' : cur))
          if (r?.steered) { setSteered((prev) => [...prev, { text: t, ts: steerTs }]); return }
          // Queued or dispatched fresh, it gets a reply of its own; a steer does not. Owed in the
          // session it was sent to: a queued answer names none, and this chat may have closed.
          oweSpokenReply(r?.session || s)
          if (r?.queued) return  // the paired queue_push frame renders the strip card
          // Dispatched as a fresh turn. Render exactly what the normal send path would:
          // the user's bubble, then arm streaming so its reply has somewhere to land.
          followNewTurn()
          setTurns((prev) => [...prev, userTurn(t, steerTs, turnPastes.length ? turnPastes : undefined)])
          markStreaming(true)
          dropTextRun()
        })
        .catch(reportActionFailure('steer this turn'))
      return
    }
    // Held while a file it carries uploads (`uploadHold`) — whichever way this send came: the
    // composer, a follow-up chip, the prompt palette, a spoken submit. Refused where the user is
    // looking, and the text is kept: a caller that emptied the draft to send it gets it back
    // there, unless the user has typed something else since. A steer (above) carries no files.
    if (uploadHold) {
      if (!opts?.uiLabel) setInput((cur) => (cur.trim() ? cur : t))
      notice.showError(uploadHold, 'upload')
      return
    }
    // meta.files = @-mentioned workspace files + uploaded attachments.
    const files = [...mentionedFiles, ...attachedPaths]
    // Stamp a client ts and pass it to the backend so it stores the SAME ts on the
    // user message. The server otherwise skips broadcasting the user echo ("FE adds
    // optimistically"), leaving a live turn's ts undefined until a reload — which
    // broke Edit & resend (it locates the message by ts → "index or ts required").
    const clientTs = new Date().toISOString()
    // When optimized: bubble shows `original` as primary + `t` (optimized) collapsed.
    // When this turn came from a genui widget action, `uiLabel` is the dual
    // payload's `humanFriendlyMessage` and it is ALL the bubble shows — the machine
    // payload is the turn's content (what the model reads), never its rendered text.
    // Deliberately NOT passed as `optimized`: that disclosure would put the JSON back
    // on screen under a chip that says "Optimized", which is both ugly and untrue.
    const uiLabel = opts?.uiLabel?.trim() || undefined
    // Sending is asking for the outcome: follow the new turn wherever the view was.
    followNewTurn()
    setTurns((prev) => [...prev, userTurn(uiLabel ?? original ?? t, clientTs, turnPastes.length ? turnPastes : undefined, files, original ? t : undefined)])
    // A widget action is not something the user TYPED, so it never joins ↑-history —
    // replaying a machine payload as a prompt is not an affordance anyone wants.
    if (!uiLabel) setPromptHistory((prev) => { const h = original ?? t; return withPrompt([...prev], { text: h, pastes: pruneBlocks(h, pasteBlocks).map(({ seq, lines, content }) => ({ seq, lines, content })) }).slice(-50) })
    const knowledgeIds = mentionedKnowledge.map((k) => k.id)
    const artifactSlugs = mentionedArtifacts.map((a) => a.slug)
    // dropTextRun: a fresh send must open a NEW coalesced text run. A follow-up in an
    // existing chat streams in right after the prior turn — the backend does NOT always emit
    // a chat_done/chat_segment boundary between turns (esp. YOLO/queued dispatch), so without
    // this the new turn's chunks would append onto the PRIOR turn's still-live coalescer run
    // → turn N+1's bubble absorbed turn N's whole answer. Safe on the very first turn
    // too (clearing an empty core is a no-op). DISCARD rather than seal: the user turn is
    // added locally just above, so a landed tail would be written into the new turn instead
    // of the finished one.
    setInput(''); setPreOptimize(null); markStreaming(true); dropTextRun()
    setPasteBlocks([]); setMentionedFiles([]); setAttachedPaths([]); setMentionedKnowledge([]); setMentionedArtifacts([])
    try {
      const meta: Record<string, unknown> = { client_ts: clientTs }
      if (files.length) meta.files = files
      if (knowledgeIds.length) meta.knowledge = knowledgeIds
      if (artifactSlugs.length) meta.artifacts = artifactSlugs
      // persist paste blocks so chips survive reload — hydrateTurns re-collapses
      // the expanded content back to [Paste #N] markers using these.
      if (turnPastes.length) meta.pastes = turnPastes
      // record the user's original prompt so the optimized-turn bubble can show
      // both after reload (content sent to the model is the optimized text).
      if (original) meta.original = original
      // Persist the human-friendly label so a RELOAD still shows the label rather
      // than the machine payload. Without this the transcript is honest live and shows
      // raw JSON after F5 — the defect surviving one refresh.
      if (uiLabel) meta.ui_label = uiLabel
      // On a brand-new chat, seed the instant-paint cache with THIS user message so
      // the post-create remount paints it immediately (no skeleton over the user's
      // own words) — mirrors the persisted history shape hydrateTurns expects.
      const seed: HistMsg[] = [{ role: 'user', content: llmText, ts: clientTs, meta: meta as HistMsg['meta'] }]
      const sid = await ensureSession(seed)
      // Frame-on-send: capture ONE frame at the instant the question is asked,
      // so the model sees the screen the user was looking at when they asked — not a
      // continuous stream, and not a frame from whenever sharing happened to start.
      // Awaited before sendChat so the slot is staged when the runner drains it.
      if (screenShare.sharing) await screenShare.captureAndStage(sid)
      oweSpokenReply(sid)
      const sent = await api.sendChat(llmText, sid, meta, undefined, opts?.inputOrigin)
      // Agent routing (569): the server emits its suggestion among the FIRST frames of the
      // send, which on a chat created BY this send is before the remounted ChatSession's
      // socket has finished reconnecting — so the WS copy reaches nobody and the chip
      // never appeared on the one message where routing matters most. The response
      // carries the same payload and cannot be raced (it exists only because the request
      // did). Stored, not shown: the payload names its own session and the render gate
      // resolves it, so a suggestion is surfaced for the session the server named or not
      // at all. `setRoutingSuggestion` belongs to ChatPage, which survives the remount.
      if (sent?.routing_suggestion?.agent) setRoutingSuggestion(sent.routing_suggestion)
    }
    catch (e) {
      markStreaming(false, 'error')
      // The chat is gone (a dead link whose read lost the race, or deleted in another tab).
      // The server did NOT save this message, so it goes back into the draft — which the
      // not-found state carries into a new chat — rather than sitting in the transcript as
      // a sent bubble above a one-line refusal.
      if (hasApiCode(e, 'session_not_found')) { setInput(llmText); setMissing(true); return }
      // A failure is not something the assistant said, so it is the turn's error strip with the
      // platform's sentence, not a warning-sign line of prose carrying the raw error.
      patchLastAssistant((segs) => [...segs, { kind: 'error', text: failureSentence('send this message', e) }])
    }
  }

  // Pin the frame currently being shared. The bytes come from the client
  // because the server kept none — the drain destroyed the staged copy the moment the
  // turn used it. On success the pinned file joins the turn like any other
  // attachment, so from here on it is an ordinary upload with nothing screen-specific
  // about it.
  async function pinScreenFrame() {
    const sid = sessionRef.current
    const frame = screenShare.lastFrame()
    if (!sid) return
    if (!frame) {
      notice.showError('Send a message while sharing first — there is no frame to pin yet.')
      return
    }
    try {
      const r = await api.pinScreenFrame(sid, frame)
      if (r?.path) setAttachedPaths((prev) => [...prev, r.path])
    } catch (e) {
      // Surface the server's own reason (incognito, switch off) rather than inventing one.
      notice.showError((e as Error)?.message || 'Could not pin the frame.')
    }
  }

  // Plan mode — the composer affordance. One explicit user action opens the
  // chat's `planning/session.py` walkthrough and puts the session in the `plan` task
  // mode, which is what makes the backend's tool gate refuse mutations. There is no
  // heuristic anywhere: a chat only ever gets a plan gate from this click. When a turn
  // is in flight the server PARKS it (the transcript is kept) and resumes on approval,
  // so the mid-conversation case needs nothing extra here.
  /** Hand *task* (the draft, by default) to the loop composer as a goal loop, with the project this
   *  new chat was scoped to. The loop composer drafts and plans it as it always has, through the
   *  loop-start route and its guards, and opens the loop's thread once it is made. */
  function startGoalLoop(task: string = input) {
    const params = new URLSearchParams({ kind: 'goal' })
    if (task.trim()) params.set('task', task.trim())
    if (projectId) params.set('project', projectId)
    setInput('')
    navigate(`loop?${params}`)
  }

  async function activatePlanMode() {
    const sid = sessionRef.current
    if (!sid) return
    try {
      const r = await api.chatPlanActivate(sid)
      setSelection((sel) => ({ ...sel, taskMode: 'plan' }))
      if (r.parked) {
        notice.showInfo('This run is parked — approve the plan below to resume it.')
      }
    } catch (e) {
      notice.showError((e as Error)?.message || 'Could not start plan mode.')
    }
  }

  // Optimize the current draft via the prompt optimizer. The context is role-labeled
  // and newest-last (see chat/optimizerContext.ts) — that shape is what lets the
  // optimizer resolve "that file from earlier" instead of guessing at it.
  async function optimize() {
    const t = input.trim()
    if (!t || optimizing) return
    setOptimizing(true)
    try {
      const ctx = buildOptimizerContext(turns)
      // #277: every answer now says something. The `if` here used to have no else and the
      // catch was a bare comment, so `changed:false` (a ~15s wait, byte-identical text) and
      // an unreachable optimizer both rendered as nothing at all. See `optimizeOutcome`.
      const out = optimizeOutcome(await api.optimizePrompt(t, ctx))
      if (out.kind === 'rewritten') { setPreOptimize(input); setInput(out.optimized) }
      else notify(out.message, out.level)
    } catch (e) { const f = optimizeFailure(e); notify(f.message, f.level) }
    finally { setOptimizing(false) }
  }
  // /optimize one-shot: optimize `raw` in the background, then send the optimized
  // text (recording `raw` as the original so the bubble shows both). If the
  // optimizer returns unchanged or errors, just send the original as-is.
  async function optimizeAndSend(raw: string) {
    setOptimizing(true)
    let optimized = ''
    try {
      const ctx = buildOptimizerContext(turns)
      // Same classifier as the button, so `changed` is read in exactly ONE place (#277) — but
      // deliberately NO toast on this path: `/optimize` SENDS either way, so the turn appearing
      // in the transcript is already the answer to "what did my click do". A toast here would
      // announce a non-event beside a visible one. The extra `!== raw` guard stays: an optimizer
      // that returns the input with `changed:true` must not be recorded as an "original".
      const out = optimizeOutcome(await api.optimizePrompt(raw, ctx))
      if (out.kind === 'rewritten' && out.optimized.trim() !== raw) optimized = out.optimized.trim()
    } catch { /* fall through — send the original unchanged */ }
    finally { setOptimizing(false) }
    if (optimized) await send(optimized, { original: raw })
    else await send(raw)
  }
  // Put the caret in the composer once the text just set into it has rendered: a revert, a
  // follow-up pick, a paste marker or a palette insert each leaves focus where the user is not.
  function focusComposerSoon() {
    requestAnimationFrame(() => composerRef.current?.querySelector<HTMLElement>('.cm-content')?.focus())
  }
  // restore the pre-optimize draft (the optimize rewrite is otherwise lossy).
  function revertOptimize() {
    if (preOptimize === null) return
    setInput(preOptimize)
    setPreOptimize(null)
    // Clearing preOptimize unmounts this button, so without this focus lands on <body>
    // and the next keystroke goes nowhere — you reverted in order to keep typing.
    focusComposerSoon()
  }
  // /undo [N] — roll back N conversation turns via the backend, then re-hydrate the
  // transcript from the truncated server state (so the UI matches disk) + append an
  // honest notice that side effects were NOT reverted.
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
  // /rewind-to-turn N — the FILESYSTEM counterpart of /undo.
  // `confirm=false` renders the preview as an assistant notice and stops; `confirm=true`
  // applies. Nothing on disk changes on the preview pass.
  async function rewindToTurn(turn: number, confirm: boolean) {
    const s = sessionRef.current
    if (!s) return
    try {
      if (!confirm) {
        const p = await api.rewindPreview(s, turn)
        setTurns((prev) => [...prev, assistantTurn(rewindPreviewText(p, turn))])
        return
      }
      const r = await api.rewindToTurn(s, turn)
      const lines = [
        r.notice,
        ...r.restored.map((p) => `- restored \`${p}\``),
        ...r.deleted.map((p) => `- deleted \`${p}\``),
        ...r.errors.map((e) => `- FAILED: ${e}`),
      ]
      setTurns((prev) => [...prev, assistantTurn(lines.join('\n'))])
    } catch (e) {
      // A failure is unrequested bad news, not something the assistant said: appending it as an
      // assistant turn put `String(e)` — "ApiError: <raw message>" — into the transcript in the
      // assistant's voice. The success notices above stay as assistant turns on purpose
      // (the preview/result IS the reply); only the failure routes to the error funnel, whose
      // message is already the backend's sentence with no exception-class prefix.
      reportActionFailure(`rewind to turn ${turn}`)(e)
    }
  }
  async function transcribe(blob: Blob, opts?: { duplex?: boolean }): Promise<string> {
    const r = await api.transcribeAudio(blob, { duplex: opts?.duplex, session: sessionRef.current || '' })
    // Surface failures: otherwise a denied/unconfigured STT just drops the
    // recording silently after the spinner — the user has no idea why no text
    // appeared. The reason stays until it is dismissed or the next send.
    const failed = transcriptionFailure(r)
    if (failed) {
      notice.showError(failed, 'voice-input')
      return ''
    }
    // The echo filter dropped this capture. Say so — silence
    // here is indistinguishable from a deaf microphone.
    if (r.filtered === 'echo') {
      notice.showInfo('Ignored the assistant’s own voice coming back through the microphone.')
      return ''
    }
    // Dictation worked, so a microphone or transcription failure no longer holds; a notice
    // something else raised is not dictation's to take down.
    notice.clear('voice-input')
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
    focusComposerSoon()
    return true
  }
  function removePaste(seq: number) {
    setPasteBlocks((prev) => prev.filter((b) => b.seq !== seq))
    setInput((prev) => prev.replace(markerFor(seq), '').replace(/  +/g, ' '))
  }
  // A message she sent comes back to the composer (↑/↓, or Edit on a queued one) with the blocks
  // she pasted into it, as their cards, numbered so none shares a marker with a block the composer
  // holds (`takeIn`). Sending it again then sends what she pasted, not its marker. Returns the
  // message's text as the composer now holds it.
  function takeBack(text: string, pastes: readonly TurnPaste[]): string {
    const back = takeIn(text, pastes, pasteBlocks)
    if (back.blocks.length) setPasteBlocks((prev) => [...prev, ...back.blocks])
    return back.text
  }
  const recallPrompt = (index: number) => takeBack(promptHistory[index].text, promptHistory[index].pastes)

  async function stop() {
    stoppedTurnRef.current = true
    const s = sessionRef.current
    // A reply you stopped is not read out, and neither is the rest of a queue it ends.
    if (s) repliesToSpeak.delete(s)
    // The composer settles at once so the button answers the press. The ENDING is said only when
    // the gateway confirms this press stopped the turn, and only if that turn is still the one on
    // screen: a stop that failed, or found the turn already over, is not a stopped turn, and the
    // turn's own terminal frame says how it did end.
    const turn = turnSeqRef.current
    markStreaming(false)
    if (!s) return
    const answer = await api.stopChat(s).catch(reportActionFailure('stop this turn'))
    if (answer?.stopped && turnSeqRef.current === turn) sayTurnEnded('stopped')
  }

  // Insert a rendered prompt (from the prompt palette) into the composer at the end,
  // then focus so the user can keep editing before sending.
  function insertPrompt(text: string) {
    if (!text) return
    setInput((prev) => (prev ? `${prev}\n${text}` : text))
    focusComposerSoon()
  }

  // select-to-quote: insert the highlighted passage into the composer as an
  // attributed blockquote. `attribution` ("You" / the agent name) prefixes the
  // block so the quoted passage carries who said it.
  function quoteToComposer(text: string, attribution?: string) {
    const q = text.trim()
    if (!q) return
    const lines = q.split('\n').map((l) => `> ${l}`)
    const block = attribution ? `> **${attribution} said:**\n${lines.join('\n')}` : lines.join('\n')
    setInput((prev) => (prev ? `${prev}\n\n${block}\n\n` : `${block}\n\n`))
    // The composer is a CodeMirror editor (.cm-content), not a <textarea> — focus
    // it so the user can keep typing after quoting a selection.
    composerRef.current?.querySelector<HTMLElement>('.cm-content')?.focus()
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

  // 🔑 ONE REPORTER FOR EVERY SELECTION WRITE. `applySelection` flips the composer's local state FIRST
  // and then fires the persistence calls — the optimistic shape `saveFailureReported` names, where a
  // swallowed rejection "is a lie, because the control is left showing a value the server refused". Here
  // that lie is worse than a settings toggle: the composer can show an agent, model or APPROVAL MODE the
  // session is not actually using, so the user's next message runs under settings they did not pick.
  //
  // These are deliberately fire-and-forget (the composer must not block on a round-trip), so the remedy
  // is the family's: TELL the user. Reverting `setSelection` was considered and rejected — the rail's own
  // fix for this shape is to report, not to fight the input the user is still editing.
  //
  // It is one helper rather than eleven catch blocks because eleven is how one gets missed.
  const persistSelection = <T,>(what: string, p: Promise<T>): Promise<T | void> =>
    p.catch((e) => { notify(`Couldn't apply ${what} to this session: ${String((e as Error)?.message || e)}`, 'error') })

  function applySelection(patch: Partial<ComposerValue>) {
    const nextSel = { ...selection, ...patch }
    setSelection(nextSel)
    const s = sessionRef.current
    if (!s) return
    const acp = acpFor(nextSel.agent)
    if (patch.agent) {
      // ACP agents bind via /acp-agent (provider + provider_agent + model);
      // native agents via /agent.
      if (acp) persistSelection('this agent', api.setSessionAcpAgent(s, { provider: acp.providerId, provider_agent: acp.agent.provider_agent, model: nextSel.model && nextSel.model !== 'Auto' ? nextSel.model : undefined }))
      else persistSelection('this agent', api.setSessionAgent(s, patch.agent))
    }
    if (patch.model && !patch.agent) {
      // model-only change: ACP model goes through /acp-agent too (re-bind w/ model)
      if (acp) persistSelection('this model', api.setSessionAcpAgent(s, { provider: acp.providerId, provider_agent: acp.agent.provider_agent, model: patch.model === 'Auto' ? undefined : patch.model }))
      else persistSelection('this model', api.setSessionModel(s, patch.model === 'Auto' ? '' : patch.model))
    }
    // Approval mode first among these three deliberately: it is the one whose silent divergence has a
    // safety cost, not just a cosmetic one.
    if (patch.approval) persistSelection('this approval mode', api.setApprovalMode(patch.approval as ApprovalMode, s))
    if (patch.taskMode) persistSelection('this task mode', api.setTaskMode(patch.taskMode as TaskMode, s))
    if (patch.reasoning !== undefined) persistSelection('this reasoning effort', api.setReasoningEffort(s, patch.reasoning as ReasoningEffort))
  }

  /** Set the permission mode from the Execution Boundary control, and show it only once the gateway
   *  has taken it: the call the header's pill made (`POST /api/chat/mode`), answered before the
   *  composer says the new mode. The gateway refuses a mode its bounds do not allow (an operator
   *  ceiling under which nothing approves without a person), and then the control keeps the mode the
   *  chat is in and says why: `applySelection`'s report-and-keep-going is right for an agent or a
   *  model, never for the mode that decides what runs without asking. A chat not started yet keeps
   *  the pick, and it is set when the chat is made (`ensureSession`). Resolves `null` once taken,
   *  else the gateway's reason, or that it did not answer (`sendModePick`). */
  function chooseApproval(mode: ApprovalMode): Promise<string | null> {
    return sendModePick('approval', 'permission mode', (s, n) => api.setApprovalMode(mode, s, n), () => setSelection((sel) => ({ ...sel, approval: mode })),
      (d) => setSelection((sel) => ({ ...sel, approval: (d.approval || 'normal') as ApprovalMode })))
  }

  /** The task mode, the same way (`POST /api/chat/task-mode`): the gateway refuses to take a chat
   *  out of Plan while its plan waits on her review, and the pill then stays on Plan and says so. */
  function chooseTaskMode(mode: TaskMode): Promise<string | null> {
    return sendModePick('taskMode', 'task mode', (s, n) => api.setTaskMode(mode, s, n), () => setSelection((sel) => ({ ...sel, taskMode: mode })),
      (d) => setSelection((sel) => ({ ...sel, taskMode: (d.task_mode || 'agent') as TaskMode })))
  }

  /** The mode picks, one at a time: a pick is sent once the one before it is answered, or once its
   *  wait (`MODE_PICK_DEADLINE_MS`) is over. A pick shown only once the gateway takes it must not
   *  race the last: two in flight are answered in either order, and the composer would end on
   *  whichever answer came last, not on the mode the gateway was last asked for. */
  const modePicks = useRef<Promise<unknown>>(Promise.resolve())
  /** Numbers the picks, in the order they were made. */
  const pickCount = useRef(0)
  /** For each control, the number of the latest pick made on it, taken or not. */
  const madePick = useRef<Record<'approval' | 'taskMode', number>>({ approval: 0, taskMode: 0 })
  /** For each control, how many picks on it the gateway has said it took: a read of the chat begun
   *  before the latest of them may have read the mode the chat left. */
  const takes = useRef<Record<'approval' | 'taskMode', number>>({ approval: 0, taskMode: 0 })
  /** For each control, how many reads of the chat it has sent: only the latest one is shown, since
   *  an earlier one may read the chat as it was before a pick whose answer never came back. */
  const reads = useRef<Record<'approval' | 'taskMode', number>>({ approval: 0, taskMode: 0 })
  /** The number the next pick is sent with: increasing for the chat across reloads and tabs on this
   *  computer (it is read off the clock), and above any number the gateway said it took (a pick
   *  made on another computer whose clock is ahead). The gateway refuses a pick numbered no higher
   *  than the last it took on that control, so one given up on cannot land after a newer one. */
  const sentFloor = useRef(0)
  /** Show the mode the chat `s` is in on `control`, read from the gateway, unless the gateway says
   *  it took a pick on it while the read is on its way (the read may predate that pick, whose own
   *  answer then says the mode), or a later read was sent. Only the gateway knows which mode the
   *  chat ended in after a wait given up on, a pick that failed in any way, or an older pick's
   *  answer coming after a newer pick was made. */
  function readModeBack(control: 'approval' | 'taskMode', s: string, adopt: (detail: ChatDetail) => void, tries = MODE_READ_TRIES) {
    const before = takes.current[control]
    const read = ++reads.current[control]
    const current = () => takes.current[control] === before && reads.current[control] === read
    api.chatSessionDetail(s).then((d) => { if (current()) adopt(d) }, () => {
      // Unread: the control's message says the pick may or may not have been taken, and the read
      // is tried again, a few times, while nothing newer has said the mode.
      if (current() && tries > 1) setTimeout(() => { if (current()) readModeBack(control, s, adopt, tries - 1) }, MODE_READ_RETRY_MS)
    })
  }
  function sendModePick(
    control: 'approval' | 'taskMode', what: string, send: (session: string, pick: number) => Promise<unknown>,
    take: () => void, adopt: (detail: ChatDetail) => void,
  ): Promise<string | null> {
    const pick = ++pickCount.current
    madePick.current[control] = pick
    /** The gateway took this pick. The latest pick made on the control is shown; an older one's
     *  answer says nothing of the newer pick, which may have been taken after it, so the chat is
     *  read back instead. */
    const taken = (s: string) => {
      takes.current[control]++
      if (madePick.current[control] === pick) take()
      else readModeBack(control, s, adopt)
    }
    const next = modePicks.current.then(() => new Promise<string | null>((resolve) => {
      const s = sessionRef.current
      if (!s) { take(); resolve(null); return }
      const number = Math.max(Date.now(), sentFloor.current + 1)
      sentFloor.current = number
      const timer = setTimeout(() => {
        resolve(`The gateway didn't answer in ${MODE_PICK_DEADLINE_MS / 1000} s, so this ${what} may or may not have been taken. This shows the ${what} once the gateway says which; pick it again to try again.`)
        readModeBack(control, s, adopt)
      }, MODE_PICK_DEADLINE_MS)
      send(s, number).then(
        // The gateway took it, in time or after its wait (`taken`).
        () => { clearTimeout(timer); taken(s); resolve(null) },
        (e) => {
          clearTimeout(timer)
          if (hasApiCode(e, 'stale_pick')) {
            const last = (e as { detail?: { last?: unknown } }).detail?.last
            if (typeof last === 'number') sentFloor.current = Math.max(sentFloor.current, last)
          }
          // A refusal changed nothing, but a lost connection or a server error may have come after
          // the gateway took it: either way the chat's own read says where it is.
          readModeBack(control, s, adopt)
          const refused = e instanceof ApiError && e.status >= 400 && e.status < 500
          resolve(refused
            ? failureSentence(`change the ${what}`, e)
            : `No clear answer came back (${String((e as Error)?.message || e)}), so this ${what} may or may not have been taken. This shows the ${what} once the gateway says which.`)
        },
      )
    }))
    // The queue goes on after every pick, answered or not: it never holds a pick's result, so the
    // tail keeps nothing (the pick itself resolves, whatever the gateway did).
    modePicks.current = next
    return next
  }

  /** Apply a saved starter to the composer.
   *
   *  Only fields the template actually carries are applied: a template saved with no
   *  model must not silently reset the user's current pick to "Auto". `applySelection`
   *  handles the no-session case (it just sets local state), so this works on the
   *  new-chat screen before any session exists. */
  function applyTemplate(t: SessionTemplate) {
    // Selection patch = only the fields the starter carries (`sessionTemplatePatch`), so a
    // starter saved with no model never resets the current pick to Auto. The prompt is applied
    // separately because it feeds the composer INPUT (and enables Send), not the selection.
    const patch = sessionTemplatePatch(t)
    if (Object.keys(patch).length) applySelection(patch)
    if (t.first_prompt) setInput(t.first_prompt)
    notify(`Started from "${t.name}".`, 'info')
  }

  async function attach(files: File[]) {
    setAttachError(null)
    // The rows go up before the size check, which reads the gateway's limits: sending is held
    // from the moment a file is attached, not from the moment its bytes start to move.
    const batch = ++uploadBatch.current
    const ctrl = new AbortController()
    uploadAborts.current.set(batch, ctrl)
    const rows = (fs: File[]) => fs.map((f) => ({ batch, name: f.name, pct: 0 }))
    setUploads((prev) => [...prev, ...rows(files)])
    const done = () => {
      uploadAborts.current.delete(batch)
      setUploads((prev) => prev.filter((u) => u.batch !== batch))
      if (uploadAborts.current.size === 0) notice.clear('upload')
    }
    // Client-side per-filetype pre-check → reject oversize BEFORE uploading a byte,
    // with the same category message the server would give (better UX than a late 413).
    const { precheck } = await import('../../../lib/chunkedUpload')
    const ok: File[] = []
    const rejected: string[] = []
    for (const f of files) {
      const err = await precheck(f)
      if (err) rejected.push(err)
      else ok.push(f)
    }
    if (rejected.length) setAttachError(rejected.join(' · '))
    if (!ok.length) { done(); return }
    setUploads((prev) => [...prev.filter((u) => u.batch !== batch), ...rows(ok)])
    const r = await api.uploadFiles(ok, (idx, p) => {
      setUploads((prev) => { let i = -1; return prev.map((u) => (u.batch === batch && ++i === idx ? { ...u, pct: p.pct, finishing: !!p.finishing } : u)) })
    }, ctrl.signal).catch(async (e) => {
      // A user cancel is not an error — just clear silently; other failures surface.
      // (abort is named inconsistently across engines — isAbortError normalises it.)
      const { isAbortError } = await import('../../../lib/chunkedUpload')
      if (!isAbortError(e)) setAttachError((e as Error).message)
      return { paths: [] as string[] }
    })
    // In the same render as the chips below, so the hold never lifts before the file is attached.
    done()
    const paths = (r as { paths?: string[] }).paths ?? []
    // Thread uploaded paths into the next send's meta.files + show them as
    // removable chips alongside @-mentioned files.
    if (paths.length) setAttachedPaths((prev) => [...prev, ...paths.filter((p) => !prev.includes(p))])
  }

  // macOS: interactive region capture on the GATEWAY host (`screencapture -i`) →
  // attach the resulting PNG to the next send. The screenshot lands in a server dir the
  // send path already reads, so we thread its path straight into attachedPaths (same
  // pipeline as an upload result). Returns '' when it handled the request (attached, or
  // the user cancelled), else the reason it could not run.
  async function captureNative(): Promise<string> {
    try {
      const r = await api.screenshot()
      if (r.error) return r.error
      if (r.path) setAttachedPaths((prev) => (prev.includes(r.path) ? prev : [...prev, r.path]))
      // r.path === '' means the user cancelled the capture — no-op, no error.
      return ''
    } catch (e) { return (e as Error).message || 'Screen capture failed' }
  }

  // Everywhere else: one frame out of the browser's display capture (tracks stopped
  // immediately), then crop it in-app. The PNG goes through the ORDINARY upload path,
  // so it gets the same policy check, uploads dir, extraction-at-upload and chip as a
  // dragged-in file — a snip is an attachment, not a special case.
  async function captureInBrowser() {
    const r = await grabOneFrame()
    if ('error' in r) {
      // A dismissed picker is a decision, not a failure — say nothing.
      if (r.error === 'cancelled') return
      setAttachError(r.error === 'unsupported'
        ? 'This browser cannot capture the screen.'
        : 'The screen capture did not produce a frame.')
      return
    }
    setSnip({ url: r.frame.toDataURL('image/png'), width: r.frame.width, height: r.frame.height, source: r.frame })
  }

  async function captureScreenArea() {
    setAttachError(null)
    if (captureProvider === 'native') {
      const err = await captureNative()
      if (!err) return
      // The native snip could not run on this host (no display server, binary refused).
      // Re-run the SAME decision with the native path marked failed rather than writing
      // a second fallback policy here; only dead-end with the error if nothing is left.
      if (chooseCaptureProvider(platform, displayCapture, true) !== 'browser') { setAttachError(err); return }
    }
    await captureInBrowser()
  }

  async function attachSnip(rect: SnipRect) {
    const src = snip
    setSnip(null)
    if (!src) return
    const file = await cropToPngFile(src.source, rect)
    if (!file) { setAttachError('The cropped capture could not be encoded.'); return }
    await attach([file])
  }

  /** The settled detail of the read that opened the session: what the composer keeps of it. */
  function adoptDetail(d: ChatDetail) {
    // Seed ↑/↓ prompt-history from the conversation's existing user turns, so
    // recall works immediately on a revisited chat (not only after sending a
    // new message this render). Oldest→newest, deduped against repeats.
    setPromptHistory(hydrateTurns(d.messages || []).reduce<RecalledPrompt[]>((acc, t) => {
      if (t.role !== 'user') return acc
      const txt = turnText(t).trim()
      if (txt) withPrompt(acc, { text: txt, pastes: t.pastes ?? [] })
      return acc
    }, []).slice(-50))
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
    setPostureKnown(true)
    // Restore the session's memory mode too, so mode-gated affordances (e.g. Fork,
    // which the backend refuses on a non-persistent session) reflect the real
    // posture of a reopened chat instead of the 'persistent' default.
    setMemoryMode((d.memory_mode || 'persistent') as MemoryMode)
    replyStyle.restore(d)
  }

  return {
    setRoutingSuggestion, acpFor, activatePlanMode, startGoalLoop, adoptDetail, applySelection, applyTemplate, chooseApproval, chooseTaskMode, artifactPickerOpen, attach, attachError, attachSnip, attachedPaths, captureProvider, captureScreenArea, composerFocused, composerRef, data, displayCapture, ensureSession, focusComposerSoon, historyTexts, input, insertPrompt, knowledgePickerOpen, livePasteBlocks, memoryMode, mentionedArtifacts, mentionedFiles, mentionedKnowledge, replyStyle, notice, onLargePaste, onMentionFile, onMentionKnowledge, openAgentSignal, openModelSignal, openProjectSignal, openReasoningSignal, optimize, optimizing, pasteBlocks, pinScreenFrame, postureKnown, preOptimize, projectId, promptPaletteOpen, quoteToComposer, recallPrompt, removePaste, revertOptimize, routingSuggestion, screenShare, selection, send, setArtifactPickerOpen, setAttachError, setAttachedPaths, setComposerFocused, setInput, setKnowledgePickerOpen, setMemoryMode, setMentionedArtifacts, setMentionedFiles, setMentionedKnowledge, setPreOptimize, setProjectId, setPromptPaletteOpen, setSelection, setSnip, setToast, snip, stop, takeBack, toast, transcribe, uploadAborts, uploadHold, uploads,
  }
}

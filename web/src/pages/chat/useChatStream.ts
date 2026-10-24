import { useCallback, useEffect, useRef, useState } from 'react'
import { playCue } from '../../design/soundCues'
import { isAChatsPhase, workPhaseOf, type WorkPhase } from '../../lib/workPhase'
import { api, ApiError, type ContextPart, type RunState } from '../../lib/api'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useQuery, invalidateKeys, peekQuery, writeQuery } from '../../lib/data'
import { useStreamCoalescer } from './useStreamCoalescer'
import { TextRunOwnership } from './coalesceReducers'
import { SnapshotReplay } from './snapshotReplay'
import { resolveStalledStream, STREAM_HEAL_WARNING } from './streamStall'
import { chatDoneOutcome, TURN_RESPONDING, TURN_SUPERSEDED, turnEndedSentence, turnOutcomeOf, type TurnOutcome } from './turnOutcome'
import { deliverableToOpenSession } from './sessionDelivery'
import { joinsATurnStartedElsewhere } from './joinTurn'
import { snapshotPredatesSend, streamingAtMount } from './liveRun'
import { startedByName } from './StartedByApp'
import { branchParentKey } from './branchLineage'
import { foldSubagentEvent } from './subagentCards'
import { turnErrorText } from './turnError'
import { applyApprovalFrame, applyApprovalResolved, applyToolCallFrame, applyToolResultFrame } from './liveToolFrames'
import { applyQuestionFrame, applyQuestionResolved, graftPendingQuestions } from './questionFrames'
import { withRowRuns, withRunEnded } from './turnRuns'
import type { RoutingSuggestion } from './RoutingChip'
import type { TurnPaste } from './PasteChip'
import type { TurnEnding } from './transcript/TurnHead'
import { type ChatTurn, type Segment, type ApprovalSegment, type QuestionSegment, appendThinking, type SubagentCard, type MemoryCitation, type SkillUsed, userTurn, assistantTurn, hydrateTurns, livePartialOf, foldStepLine, noteOf, ranPromptOf, replyCutOf, type ReplyCut, pastesOf, shownText, servedModelOf, imageDeliveryOf, noticeSegment } from './chatTypes'

// ── The chat's stream: the open session as the gateway tells it ───────────────────────────────
//
// `ChatSession` composes the chat's regions; this hook is the one that listens. It owns what the
// session's reads and its `/api/ws` frames say — the transcript and the live text run it is
// folded into (`coalesceReducers`), the streaming claim and how a turn ended, the run state, the
// queue and the steers, the helpers a turn started, the side chat, the follow-ups, the context
// reading, the binding, the title and what the chat's detail says about where it came from — and
// the machinery that keeps them true: the snapshot read that holds the stream and replays it
// (`snapshotReplay`), the resync on a socket that comes back, and the stall reconciler.
//
// What belongs to other regions reaches them through the sinks: the routing suggestion ChatPage
// holds above the create-remount, the audio of a reading this tab asked for, a finished reply that
// may be owed a reading, and the parts of the chat's first read the page restores itself (the
// composer's posture and its ↑/↓ recall). The sinks are read through a ref, so the frame handler —
// made once, like every handler here that a socket calls — always reaches the page as it is now.

// Instant-paint cache for opened chat sessions, held in the ONE data layer.
//
// 🔴 THIS WAS A SECOND CACHE. It was a private `sessionStorage` store under its own
// `chat-detail:` prefix, with its own reader and writer and NO age on the record — the exact
// hand-rolled fetch-and-cache shape the data layer replaced, and invisible to any census of the shared
// helper because it never called it. Two caches over one endpoint is what produces the flicker:
// this one seeded the transcript, the shared `chat:sessions*` keys held the list, and a mutation
// that busted one could not reach the other.
//
// Now it is `chat:detail:<key>` in the shared store (`persist: true`, so it still survives a full
// reload), which means `invalidateKeys('chat:', true)` reaches it like every other chat key.
//
// 🔑 THE SEED READS `peekQuery`, WHICH IS FRESH-ONLY, AND THAT IS THE FIX. The transcript load is
// bespoke (it hydrates the full segment model and restores selection/queue/side chat), so it
// cannot be a `useQuery` call — but it must not paint an unlabelled stale transcript either, and
// there is no sane place to hang an "updating" label on a transcript. So the paint is gated on
// FRESHNESS instead: within the `chat` window a revisit paints instantly, and anything older —
// including everything seeded from a previous page load, which the store deliberately treats as
// not-current — falls through to the normal loading state. Fresh, or explicitly loading; never a
// confident old transcript that is silently replaced.
export type ChatDetail = Awaited<ReturnType<typeof api.chatSessionDetail>>
export const detailKey = (key: string) => `chat:detail:${key}`
export const readCachedDetail = (key: string): ChatDetail | null => peekQuery<ChatDetail>(detailKey(key)) ?? null
export function writeCachedDetail(key: string, d: ChatDetail): void {
  // Never cache a running turn's partial transcript — it would paint a stale,
  // mid-stream snapshot on revisit. Only settled sessions are safe to seed from.
  if (d.run_state.turn) return
  // A Temporary chat is forgotten when its session ends, so its copy lives in this page only,
  // never in the browser's session storage, where it would outlive the chat.
  writeQuery(detailKey(key), d, d.memory_mode !== 'temporary')
}

/** How long a chat's mount read waits for the chat to be listening (see the load effect in
 *  `useChatStream`). It is a cost bound, not a correctness one: a read that gives up is taken again
 *  the moment the socket opens, so a slow handshake costs one extra read. */
const LISTEN_BEFORE_READ_MS = 2_000

/** How long a session read may hold the chat's live frames (see `readSnapshot`): a round trip,
 *  generously. Past it the frames flow live and the read lands late, a repaint rather than a
 *  wait — the measured cost of an unbounded hold was a gateway under load answering in 25-45 s
 *  while the answer, its `chat_done` included, sat held. */
const HOLD_LIMIT_MS = 4_000

/** The frames whose effect lives in what a snapshot adoption replaces — the turns, the text
 *  run, the streaming claim. A snapshot adopted after its frames flowed live (HOLD_LIMIT_MS)
 *  paints over them, so they are applied again on top of it; every other frame's effect
 *  survives an adoption and is not repeated (a voice chunk would play twice, a side-chat delta
 *  would be appended twice). */
const TRANSCRIPT_FRAMES = new Set([
  'chat_chunk', 'work_phase', 'chat_thinking', 'chat_message', 'activity_event', 'tool_call',
  'tool_result', 'approval', 'approval_resolved', 'chat_segment', 'chat_variant_switch',
  'chat_done', 'chat_user_message', 'session_clear',
])

/** A message waiting in the chat's queue, as the strip shows it: her words with each block she
 *  pasted into it as its marker (`content`), and the blocks, which Edit gives back with it. */
export interface QueuedMessage { id: string; content: string; pastes?: TurnPaste[] }

/** A queued message as the gateway tells it (a `queue_push` frame, a read of the chat). */
function queuedMessage(id: string, content: string, raw: unknown): QueuedMessage {
  const pastes = pastesOf(raw)
  return { id, content: shownText(content, pastes), ...(pastes ? { pastes } : {}) }
}
/** What `useChatStream` returns: the open chat as its regions read it. */
export type ChatStream = ReturnType<typeof useChatStream>

/** Where the stream hands what other regions own (see the note at the top of this file). */
export interface ChatStreamSinks {
  /** A `routing_suggestion` frame's payload, for ChatPage to hold. */
  setRouting: (s: RoutingSuggestion | null) => void
  /** A `voice_chunk` frame: its audio, and the reading it belongs to. */
  onVoiceChunk: (audio: string, request: unknown) => void
  /** A reply finished — the turn ended (`last`), or a queued message is starting the next one. */
  onReplyFinished: (session: string | null, opts: { last: boolean }) => void
  /** The settled detail of the read that opened the session. */
  onOpened: (d: ChatDetail) => void
}

export function useChatStream({ sessionId, liveRun, setLiveRun, ...given }: {
  sessionId: string | null
  /** ChatPage's live-run handoff across the create-remount (`chat/liveRun.ts`). */
  liveRun: string
  setLiveRun: (s: string) => void
} & ChatStreamSinks) {
  const sinks = useRef<ChatStreamSinks>(given)
  sinks.current = given
  // Session cost total: "$X · N tokens" for
  // THIS chat, read from the usage rollup scoped to the session key. Refreshed on
  // load + after each chat_done. `priced=false` ⇒ the total mixes an unpriced model,
  // so we show a "~" prefix rather than a confidently-complete figure.
  const [sessionCost, setSessionCost] = useState<{ cost: number; tokens: number; priced: boolean } | null>(null)
  const refreshSessionCost = useCallback((key: string | null, signal?: AbortSignal) => {
    if (!key) { setSessionCost(null); return }
    api.usageTotals({ session: key }, { signal }).then((d) => {
      const t = d.totals
      const tokens = (t.input_tokens || 0) + (t.output_tokens || 0)
      // Show the chip only once the session has recorded real usage.
      setSessionCost(t.turns > 0 && tokens > 0 ? { cost: t.cost_usd, tokens, priced: t.priced } : null)
    }).catch(() => { /* leave the chip as-is; a transient read failure isn't worth clearing it */ })
  }, [])
  // Instant-paint seed: if we have a cached detail for this session, hydrate its
  // turns synchronously so the transcript paints on the FIRST frame (skeleton only
  // shows for a genuinely-uncached first open). The load effect below revalidates.
  const seededDetail = useRef<ChatDetail | null>(sessionId ? readCachedDetail(sessionId) : null).current
  const [turns, setTurns] = useState<ChatTurn[]>(
    () => (seededDetail ? hydrateTurns(seededDetail.messages || [], false) : []),
  )
  // Seeded from ChatPage's live-run handoff, NOT from a bare `false`: this instance is
  // often the REPLACEMENT for the one that issued a send (creating a session re-keys
  // ChatSession), and a fresh `false` there advertised an idle composer over a live run
  // (#3444). `streamingAtMount` is the only thing that reads the handoff.
  const [streaming, setStreaming] = useState(() => streamingAtMount(liveRun, sessionId))
  // Synchronous mirror of `streaming` for send()'s queue-vs-fresh-turn decision.
  // Two sends fired in one tick both close over the stale `streaming=false` state
  // (React hasn't re-rendered), so the 2nd would wrongly start a fresh turn instead
  // of queuing. This ref flips the instant a turn is committed, so the 2nd send
  // sees it and queues. Kept in sync with the state setter everywhere it changes.
  // Seeded from `streaming` so a handed-off run reaches the send path too — the ref is
  // what send() actually branches on, so a `false` here would reopen the window a layer
  // below the button's label.
  const streamingRef = useRef(streaming)
  // Whether the running turn takes a message in (Steer) or runs it after it ends (Queue), as the
  // gateway says it (`running_turn.set_steer_drains`): a turn whose runtime pulls no message in
  // is never offered a Steer it cannot take. The ref is what send() branches on.
  const [takesSteers, setTakesSteersState] = useState(false)
  const takesSteersRef = useRef(false)
  const setTakesSteers = (v: boolean) => { takesSteersRef.current = v; setTakesSteersState(v) }
  // Set by a Stop in this tab and cleared by that turn's `chat_done`. Stop drops the streaming
  // claim at once, before the turn has sent its last frames, and those frames are not a turn
  // some other tab started (`chat/joinTurn.ts`).
  const stoppedTurnRef = useRef(false)
  // Bumped when a turn settles (streaming → false) so the session-skills review
  // re-checks for drafts the agent just captured.
  const [sessionSkillsEpoch, setSessionSkillsEpoch] = useState(0)
  // Screen-reader narration of the turn lifecycle: a polite live region says a turn started, and
  // then how it ENDED, in the words `chat/turnOutcome.ts` owns for the outcome the gateway
  // reported; what the turn does in between is the line at the head of its reply, a live region of
  // its own (`transcript/TurnHead`). The ending is never inferred from `streaming` going false:
  // Stop, a failed turn and a retry notice all end streaming too, and every one of them used to be
  // announced as "Response complete."
  const [srAnnounce, setSrAnnounce] = useState('')
  // Whether the turn this view follows has ended without the region saying how. Stop settles the
  // composer at once, before the gateway has said the turn stopped; whichever terminal fact comes
  // first (the Stop answer, `chat_done`, a snapshot's run state) says it, once.
  const endUnsaidRef = useRef(streaming)
  // Counts the turns this view has watched start, so a late answer (a Stop's) can tell whether
  // the turn it was about is still the one on screen.
  const turnSeqRef = useRef(0)
  const sayTurnEnded = (outcome: TurnOutcome | null) => {
    if (!outcome || !endUnsaidRef.current) return
    endUnsaidRef.current = false
    setSrAnnounce(turnEndedSentence(outcome))
    // The turn-ended cue point, fired where the ending is SAID so the two
    // agree: a failed turn is "something failed", any other end is "a turn finished". Once per turn,
    // like the sentence. Silent unless the user opted in — every gate lives inside playCue, so this
    // call site carries no policy of its own.
    playCue(outcome === 'error' ? 'error' : 'turn_complete')
  }
  /** Start or settle the streaming claim. `outcome` is how a settled turn ended, when the caller
   *  knows it; without one the composer settles and the ending is left for the terminal fact that
   *  follows to say. */
  const markStreaming = (v: boolean, outcome: TurnOutcome | null = null) => {
    if (v && !streamingRef.current) {
      turnSeqRef.current += 1
      endUnsaidRef.current = true
      setSrAnnounce(TURN_RESPONDING)
      setTurnEnding(null)
    }
    if (streamingRef.current && !v) {
      setSessionSkillsEpoch((n) => n + 1)
      // "Assistant is responding…" stops being true the moment the turn settles, even before
      // anything is known about how it ended.
      if (!outcome) setSrAnnounce('')
    }
    if (!v) sayTurnEnded(outcome)
    // A settled turn takes no steer; the next turn says whether it does.
    if (!v) setTakesSteers(false)
    // Release the handoff the moment the run settles. Left set, a later mount of this
    // same session (a revisit) would claim a finished run was live and offer Steer over
    // an idle backend — the mirror image of #3444, and just as dishonest.
    if (!v && liveRun) setLiveRun('')
    streamingRef.current = v
    setStreaming(v)
  }
  // A mount that opens on a live turn (the create-remount's handoff) says so, as a fresh send does.
  useEffect(() => { if (streamingRef.current) setSrAnnounce(TURN_RESPONDING) }, [])
  // Only show the skeleton on a genuine cold open (session with nothing cached).
  // A cache hit paints instantly and revalidates silently in the background.
  const [loadingHistory, setLoadingHistory] = useState(!!sessionId && !seededDetail)
  // 🔴 THE CHAT THIS ROUTE NAMES DOES NOT EXIST. The detail read answered 404 — or it stopped
  // existing under us and a send answered `session_not_found`. Before this, the load's catch
  // only cleared the skeleton, so a dead link rendered exactly like a fresh empty chat and its
  // composer took a message the server then refused and did not save. Terminal
  // for this mount: the page says the chat is gone and offers a new one instead.
  const [missing, setMissing] = useState(false)
  // A read that failed for any OTHER reason is not evidence that the chat is gone, so it is
  // shown as a load failure with a retry — never as an empty chat, and never as "not found".
  const [loadFailure, setLoadFailure] = useState<unknown>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  // WS link state — false while the socket is down (drives the reconnecting cue).
  const [wsConnected, setWsConnected] = useState(true)
  // Follow-up chips: 2-3 suggested next messages pushed over the
  // chat_followups WS after a reply completes. Cleared when the user types, sends or a new
  // turn starts, and reset per session. A pick leaves them in place: taking them away on the
  // first click moved the scrolled transcript under the pointer, so the second click of a
  // double-click landed on the reply's own actions (measured: on Speak, then Stop).
  const [followups, setFollowups] = useState<string[]>([])
  // What stands in their place while no model is chosen for them (the gateway's sentence): cleared
  // as they are.
  const [followupsNeed, setFollowupsNeed] = useState('')
  useEffect(() => { setFollowups([]); setFollowupsNeed('') }, [sessionId])
  // "Check this work" offer: pushed over chat_check_work_offer when
  // the completed turn did 3+ tool calls AND claimed completion. An OFFER only — clicking
  // sends the prompt, so verification is never spent without the user asking for it.
  const [checkWorkOffer, setCheckWorkOffer] = useState<{ label: string; prompt: string } | null>(null)
  useEffect(() => { setCheckWorkOffer(null) }, [sessionId])
  // `undefined` until the backend reports a measurement (it sends `pct: null` when it
  // has none) — an unmeasured context must show no percentage, not 0%.
  const [contextPct, setContextPct] = useState<number | undefined>(undefined)
  // The window the gateway named with its reading: `null` = none is declared or served,
  // `undefined` = not said yet. It is what lets the unmeasured dot say WHY (`AgentModelPill`).
  const [contextWindow, setContextWindow] = useState<number | null | undefined>(undefined)
  // What fills the context, part by part, with the reading it came with: `null` when the gateway
  // measured no parts, `undefined` before it has said anything.
  const [contextParts, setContextParts] = useState<ContextPart[] | null | undefined>(undefined)
  // A reading that arrived live outranks the one a later snapshot carries, which is older.
  const contextSaidLive = useRef(false)
  // The Settings → Models chain a turn here runs on when no model is picked ("Code & tools" for a
  // chat working in a folder of its own, else "Chat"): the composer's Auto says it. From session
  // detail, and from the answer to setting the chat's working directory, which can move it.
  const [autoChain, setAutoChain] = useState('')
  // The folder the chat works in, which its Working directory prompt opens on: from session detail,
  // from the answer to setting it, and from `session_binding` when it moves.
  const [workingDir, setWorkingDir] = useState('')
  const takeContextUsage = (u: { pct?: number | null; window?: number | null; parts?: ContextPart[] | null }) => {
    setContextPct(typeof u.pct === 'number' ? u.pct : undefined)
    if ('window' in u) setContextWindow(typeof u.window === 'number' ? u.window : null)
    // The parts go with their reading: a reading said without them measured none.
    setContextParts(typeof u.pct === 'number' && Array.isArray(u.parts) ? u.parts : null)
  }
  // Branch lineage: when this session was branched off another, the parent's
  // persisted history key + the parent's title, both read from session detail on every
  // open. Sourced from the server (not from the navigation that created the branch) so
  // the breadcrumb is still there after a reload. `title: ''` = the origin is gone.
  const [branchedFrom, setBranchedFrom] = useState<{ key: string; title: string } | null>(null)
  // The app that started this conversation — `null` for one of yours. Read from the session
  // detail on every open.
  const [startedBy, setStartedBy] = useState<{ name: string } | null>(null)
  // Investigate origin: the entity this chat was opened to investigate.
  // Rendered as a header chip deep-linking back to the source surface.
  const [investigateOrigin, setInvestigateOrigin] = useState<import('../../lib/api').InvestigateOrigin | null>(null)
  // Messages typed while a turn is streaming are QUEUED server-side (FIFO) and
  // shown above the composer; the backend dispatches them one-by-one as each turn
  // finishes. Driven by the queue_push / queue_pop / queue_cancel WS events. Each keeps the blocks
  // she pasted into it: the strip shows them as their markers, and Edit gives them back.
  const [queued, setQueued] = useState<QueuedMessage[]>([])
  // Messages the server confirmed it will STEER into the running turn, until the turn takes
  // each: then it is her bubble in the transcript, where the turn took it (the `steer` flavour
  // of `chat_user_message`), and its line here goes. Distinct from `queued`: a steered message
  // has no queue id and nothing to cancel. Keyed by the stamp it was sent with, which the
  // gateway's row and frames carry; one the turn ends without taking is queued (`queue_push`
  // names it by `steer_ts`), and whatever is left goes when the turn ends.
  const [steered, setSteered] = useState<{ text: string; ts: string }[]>([])
  // Async subagents (fire-and-forget) spawned this turn — live cards driven by
  // the subagent_spawn / subagent_tool / subagent_done WS events (`foldSubagentEvent`). The
  // report of one that ran starts its own turn once the parent turn finishes (backend-injected);
  // one that never ran, or that she stopped, starts none, and its card is where it shows.
  const [subagents, setSubagents] = useState<SubagentCard[]>([])
  // side chat (stage 6) — isolated Q&A in the activity panel's Side tab. Each
  // entry is {q, a}; `a` streams in via the chat.side_result WS event by run_id.
  const [sideMsgs, setSideMsgs] = useState<{ q: string; a: string; runId: string; done: boolean }[]>([])
  const [sideBusy, setSideBusy] = useState(false)
  const sideOpenedRef = useRef(false)  // side buffer opened/exists for this session
  const sessionRef = useRef<string | null>(sessionId)
  // Wall-clock of the last WS frame seen for THIS session — drives the idle
  // approval-reconciler: a turn that parks on an approval sends no `chat_done`
  // and goes silent, so if a lost/early `approval` frame left no card, the stream
  // just stalls. When streaming stays quiet, we reconcile from session detail.
  const lastWsActivityRef = useRef<number>(0)
  // The reads a finished turn starts (its cost, its snapshot), so the next turn's abort them.
  const turnDoneReads = useRef<AbortController | null>(null)
  // the resumed session's agent/model binding (from detail), restored into the
  // composer selection once discovered agents load. bindingNonce re-fires the
  // restore effect when a new session's binding arrives.
  const sessionBindingRef = useRef<{ agent: string; model: string; acp_provider: string; acp_provider_agent: string; reasoning_effort: string } | null>(null)
  const [bindingNonce, setBindingNonce] = useState(0)
  // Set when the gateway said the chat now runs on another binding (`session_binding`), so the
  // composer shows it even when it is the default agent — whoever made the change.
  const bindingToldRef = useRef(false)
  // What the running turn is doing, as the gateway measured it (its run state's phase and line), or
  // null while the page holds no running turn's state: a turn it has just sent is waiting to start.
  // `runSeqRef` is the stamp of the run state applied last, which a frame must be newer than to
  // apply; each snapshot sets it again, as its `stream_seq` does the answer's.
  const [turnPhase, setTurnPhase] = useState<WorkPhase | null>(null)
  const [turnLine, setTurnLine] = useState('')
  // How the turn this view follows ended, from its last frame (`done` or `error`, and its ending
  // line), for the head of its reply to settle on; cleared when the next turn starts.
  const [turnEnding, setTurnEnding] = useState<TurnEnding | null>(null)
  // When the running turn began, as its run state says (`turn.started_at`), and the usage its
  // runtime has reported for it (`token_usage` frames), which stays null until one is said: the
  // live status row shows what was reported, never an estimate.
  const [turnStart, setTurnStart] = useState<string | null>(null)
  const [turnUsage, setTurnUsage] = useState<{ input: number; output: number } | null>(null)
  const runSeqRef = useRef(0)
  // The running turn's name and when the gateway started it, from its run state, kept for the
  // frame that says it ended: with that frame's time they say how long its answer worked
  // (`turnRuns.withRunEnded`).
  const runningTurnRef = useRef<{ id: string; start: string } | null>(null)
  const [title, setTitle] = useState(seededDetail?.title || '')
  // The messages bookmarked on this chat's Session Map, by their `ts`: from the chat's read, and
  // from the gateway's session list each time any window changes them.
  const [bookmarks, setBookmarks] = useState<readonly string[]>(seededDetail?.bookmarks ?? [])
  // Why the chat has no title yet, when no model is chosen for the chores (the gateway's sentence);
  // read with the chat, on opening it and after each turn, and gone once it has a title.
  const [titleNeed, setTitleNeed] = useState('')
  // rAF stream coalescer: chat_chunk pushes into this; it flushes ONE growing
  // reveal per animation frame (instead of a setTurns per chunk) via onFlush, which
  // replaces the ACTIVE text run's text with the revealed-so-far prefix. Every
  // ownership decision (replace-or-push, above-or-below the live text) is taken when
  // its frame is DISPATCHED and handed to patchLastAssistant baked into the updater:
  // React may apply that updater only after chat_done has released the run.
  const textRun = useRef(new TextRunOwnership()).current
  // The transcript is a server snapshot plus the frames that arrived after it: while a
  // session-detail read is in flight its session's frames are held, then replayed on top of
  // the adopted snapshot (see snapshotReplay.ts, and `readSnapshot` below).
  const snapshots = useRef(new SnapshotReplay<WsMessage>((m) => TRANSCRIPT_FRAMES.has(m.type))).current
  // The user-message stamps of the last snapshot adopted, so a replayed `chat_user_message`
  // it already holds is recognised at dispatch.
  const adoptedUserTs = useRef<Set<string>>(new Set())
  // Streaming reveal cadence: 'immediate' short-circuits the rAF
  // coalescer so each chunk paints the instant it arrives; 'smooth' (default) keeps
  // the word-boundary-snapped animated reveal. Read from the server dashboard config
  // (cached; paints instantly on revisit). reduced-motion/animSpeed=0 still force
  // immediate inside the coalescer regardless of this preference.
  // `.catch(() => 'smooth')` fabricated a SETTING and persisted it: a failed read cached
  // "smooth" as if the user had chosen it, so the transcript animated one way and the cache kept
  // saying so. The fallback belongs at the USE SITE (below), where it is a default rather than a
  // stored answer.
  const { data: streamRevealCfg } = useQuery('chat:stream-reveal', () => api.dashboardConfig().then((c) => c.stream_reveal), { persist: true })
  // Live thinking rendering is gated by the Settings → Chat toggle. Read through
  // the same persisted-query pattern as stream_reveal (no fabricated default — absent
  // config reads falsy = hidden), mirrored into a ref so the WS callback never acts on
  // a stale closure. Gating happens at INGESTION: while off, chat_thinking frames are
  // dropped, so the transcript state itself stays free of thinking segments.
  const { data: showThinkingCfg } = useQuery('chat:show-thinking-inline', () => api.dashboardConfig().then((c) => c.show_thinking_inline), { persist: true })
  const showThinkingRef = useRef(false)
  useEffect(() => { showThinkingRef.current = !!showThinkingCfg }, [showThinkingCfg])
  const coalescer = useStreamCoalescer((revealed) => patchLastAssistant(textRun.flush(revealed)),
    { immediate: streamRevealCfg === 'immediate' })
  // 🔴 Issue #548 — ONE mechanism for ending a coalesced text run, in two flavours, and
  // BOTH clear the coalescer buffer. That is the invariant that makes the leak unreachable: a
  // finished run holds no text, so no later flush can re-emit it.
  //
  // What used to be here was a `breakText` ref whose clearing was DEFERRED to the `chat_chunk`
  // branch — the one branch of six that consulted it. `chat_thinking`, `chat_message` (error),
  // `tool_call`, `approval`, `chat_segment` and `chat_done` all called a drain-only `flushNow()`
  // without it, and a drain leaves `pending` intact. So a turn whose FIRST frame was a tool call
  // (an agent leading with a search/read — an ordinary turn shape) drained the PREVIOUS turn's
  // whole answer into the new turn's bubble, above the tool card.
  //
  //  * `endTextRun` — a boundary INSIDE a live turn (thinking / error / tool / approval /
  //    segment / done). Lands the buffered tail into the run's own segment first: the transcript
  //    tail is still that turn, so the text belongs there and dropping it would lose words the
  //    model already sent.
  //  * `dropTextRun` — a boundary the CLIENT makes (a fresh send, regenerate, edit-resend, a
  //    queued turn being dequeued, a session switch). Those all move the transcript tail FIRST,
  //    so landing the tail would write the old answer into the new turn — discard is correct.
  const endTextRun = () => { coalescer.seal(); textRun.release() }
  const dropTextRun = () => { coalescer.reset(); textRun.release() }
  /** Take a run state: the turn's phase and line while one runs, and whether it takes a steer. A
   *  snapshot's always (a restarted gateway stamps from the start again), a `work_phase` frame's
   *  only when it is newer than the one held. */
  const takeRunState = (rs: RunState | null | undefined, from: 'frame' | 'snapshot' = 'frame') => {
    if (!rs || typeof rs.seq !== 'number' || (from === 'frame' && rs.seq <= runSeqRef.current)) return
    runSeqRef.current = rs.seq
    const running = !!rs.turn
    setTurnPhase(running ? workPhaseOf(rs.phase) : null)
    setTurnLine(running ? String(rs.line ?? '') : '')
    setTakesSteers(running && !!rs.steerable)
    // A turn's last frame says how it ended. A snapshot's idle run state says nothing of the turn
    // this view watched, so it leaves the ending as the frame said it.
    if (from === 'frame') {
      const ended = running ? null : workPhaseOf(rs.phase)
      setTurnEnding(ended === 'done' || ended === 'error' ? { phase: ended, line: String(rs.line ?? '') } : null)
    }
    // The turn's own start, and its end once its last frame says it ended (`since`, when that frame
    // started being said): the time its answer's folded work says.
    const began = runningTurnRef.current
    runningTurnRef.current = rs.turn ? { id: String(rs.turn.message_id ?? ''), start: String(rs.turn.started_at ?? '') } : null
    // A turn that is not the one held starts with nothing reported.
    if (runningTurnRef.current?.id !== began?.id) setTurnUsage(null)
    setTurnStart(runningTurnRef.current?.start || null)
    if (!running && from === 'frame' && began?.start && rs.since) {
      const run = { ...began, end: rs.since }
      setTurns((prev) => withRunEnded(prev, run))
    }
  }

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

  // Rebuild the transcript from a session snapshot and continue the live answer from it.
  // Every path that adopts session detail comes through here, so the three things that must
  // agree — the turns, the text run's owner, the coalescer's text and watermark — are set
  // together, at dispatch, from ONE snapshot.
  const adoptSnapshot = (d: ChatDetail) => {
    const messages = d.messages || []
    const running = !!d.run_state.turn
    // …with the questions its turn is waiting on her answer to, which no row holds yet.
    setTurns(graftPendingQuestions(hydrateTurns(messages, running), d.pending_questions))
    takeRunState(d.run_state, 'snapshot')
    adoptedUserTs.current = new Set(messages.flatMap((m) => (m.role === 'user' && m.ts ? [m.ts] : [])))
    // The answer still being written continues IN the segment the snapshot paints for it:
    // ownership is claimed before the coalescer can flush, so the next flush extends the
    // partial rather than pushing a second copy beside it.
    const partial = running ? livePartialOf(messages) : null
    if (partial !== null) textRun.adopt(); else textRun.release()
    coalescer.resume(partial, d.stream_seq ?? 0)
    // What the ring was last told, so a chat opened after its turn draws the same ring.
    if (d.context_usage && !contextSaidLive.current) takeContextUsage(d.context_usage)
    setAutoChain(d.auto_chain ?? '')
    setWorkingDir(d.workspace_dir ?? '')
  }
  // Read session detail with this chat's live frames HELD, adopt the snapshot, then replay every
  // frame since the read was issued on top of it — see snapshotReplay.ts for why that one rule
  // is enough. `adopt` reports whether it replaced the transcript (the create-remount's first
  // read can decline). The hold is bounded (HOLD_LIMIT_MS): the frames of a read too slow to
  // hold the stream for flow live, and when it lands late the ones it paints over are applied
  // again on top. Either way the replay paints with the snapshot, at once.
  const readSnapshot = (key: string, adopt: (d: ChatDetail) => boolean): Promise<ChatDetail> => {
    const gen = snapshots.begin()
    const bound = window.setTimeout(() => snapshots.release(gen).forEach(applyFrame), HOLD_LIMIT_MS)
    const settle = (d: ChatDetail | null) => {
      window.clearTimeout(bound)
      let adopted = false
      const tryAdopt = d ? () => (adopted = sessionRef.current === key && adopt(d)) : null
      snapshots.settle(gen, tryAdopt).forEach(applyFrame)
      if (adopted) coalescer.reveal()
    }
    return api.chatSessionDetail(key).then(
      (d) => { settle(d); return d },
      (e: unknown) => { settle(null); throw e },
    )
  }
  // A held frame, applied the way the socket applies a live one: `useChatSocket` isolates each
  // message, so a frame whose handler throws cannot take the frames queued behind it with it —
  // replay must not either, or one bad frame would silently drop the `chat_done` after it.
  const applyFrame = (m: WsMessage) => {
    try { onWs(m) } catch { /* same per-frame boundary as useChatSocket */ }
  }
  // Whether the snapshot this mount adopts may predate the send it was handed off from (the
  // session-create remount). Cleared by the first snapshot it adopts.
  const handedOffRef = useRef(streaming)
  // Whether this chat is listening — subscribed to the tab's socket while it is open — and the
  // reads waiting for it to be.
  const listening = useRef<{ open: boolean; waiters: (() => void)[] }>({ open: false, waiters: [] })
  // Set when the mount read gave up waiting for the socket: its first open must read again.
  const rereadOnOpen = useRef(false)
  // The stall reconciler's read is out (see the reconciler).
  const probing = useRef(false)
  /** Resolves `true` once the socket is listening, or `false` after `ms` without it. */
  const whenListening = (ms: number) => new Promise<boolean>((resolve) => {
    if (listening.current.open) { resolve(true); return }
    const timer = window.setTimeout(() => resolve(false), ms)
    listening.current.waiters.push(() => { window.clearTimeout(timer); resolve(true) })
  })

  // Open a session: hydrate its transcript from a snapshot and continue whatever is still
  // being written.
  useEffect(() => {
    sessionRef.current = sessionId
    dropTextRun()  // drop any in-flight reveal from the prior session
    // So is its run state: the next chat's stamps are its own, and may be older than the last one's.
    runSeqRef.current = 0
    runningTurnRef.current = null
    setTurnPhase(null)
    setTurnLine('')
    setTurnEnding(null)
    setQueued([])  // queue is per-session; clear when the open session changes
    setSubagents([])  // subagent cards are per-session too
    setBranchedFrom(null)  // lineage is per-session; the load below re-reads it
    setStartedBy(null)  // so is which app started it
    if (!sessionId) { setTurns([]); setLoadingHistory(false); return }
    setLoadFailure(null)
    let alive = true
    // Only skeleton if we have nothing seeded from cache; a cache hit already
    // painted the transcript and we revalidate silently underneath it.
    if (!seededDetail) setLoadingHistory(true)
    // A live answer can only resume from a snapshot read while this chat is LISTENING. It
    // subscribes to the tab's socket after this effect, and on a reload that socket is still
    // opening, so a read issued at mount can reach the gateway before any frame can reach the
    // chat, and the chunks broadcast in between are on neither transport — measured on a
    // reload as 6 and 13 chars missing from the middle of the answer. So the read waits for
    // it (at once when the socket is already open). Bounded, because a socket that never
    // opens must not hold the transcript hostage; a read that gave up waiting is taken again
    // when the socket does open (onSocketStatus), so the bound can cost a second read, never
    // text.
    whenListening(LISTEN_BEFORE_READ_MS).then((heard) => {
      if (!alive) return
      if (!heard) rereadOnOpen.current = true
      return readSnapshot(sessionId, (d) => {
        // The create-remount's first read can beat the send it was handed off from, and an
        // honest-but-older snapshot would erase the user's own message. The send's frames
        // replay onto the painted turn instead.
        if (handedOffRef.current && snapshotPredatesSend(seededDetail?.messages, d.messages || [])) return false
        handedOffRef.current = false
        adoptSnapshot(d)
        // Its `running` is the truth as of the read, and every frame after the read replays
        // on top — so a turn that fails fast inside this round trip ends on its replayed
        // terminal frame, and a turn that ENDED before this chat was listening (its
        // `chat_done` reached the instance the remount replaced) settles here rather than
        // stranding the composer on Stop until the stall reconciler notices — and says how it
        // ended, which the snapshot reports for exactly this case.
        const running = !!d.run_state.turn
        if (running || streamingRef.current) markStreaming(running, turnOutcomeOf(d.run_state.last_outcome))
        return true
      }).then((d) => {
        if (!alive) return
        // cache the settled detail so the next revisit paints instantly (writeCachedDetail
        // skips running turns to avoid seeding a stale mid-stream snapshot).
        writeCachedDetail(sessionId, d)
        // rehydrate any still-pending queued messages (mid-stream FIFO) so a reload
        // mid-queue shows them again above the composer.
        setQueued(Array.isArray(d.queue) ? d.queue.filter((q) => q && q.id).map((q) => queuedMessage(q.id, q.content, q.pastes)) : [])
        // What the page keeps of the chat beside its stream (the composer's posture, ↑/↓ recall) is
        // restored by the page from this same read.
        sinks.current.onOpened(d)
        setTitle(d.title || '')
        setTitleNeed(d.title_needs_model || '')
        setBookmarks(Array.isArray(d.bookmarks) ? d.bookmarks : [])
        // Seed the session cost chip from the ledger for a revisited chat.
        refreshSessionCost(sessionId)
        // Branch lineage — restore the "Branched from" breadcrumb on every open,
        // including a plain browser reload, because it comes from persisted state rather
        // than from whatever navigation happened to land us here.
        setBranchedFrom(d.forked_from
          ? { key: branchParentKey(d.forked_from), title: d.forked_from_title || '' }
          : null)
        // Which app started this conversation, if one did: a turn here runs under that app's
        // permissions whoever sends it, and the composer and the Permission pill say so
        // (`StartedByApp`).
        setStartedBy(startedByName(d) ? { name: startedByName(d) } : null)
        // Investigate origin chip — present on sessions opened via
        // POST /api/investigate; survives the first turn (display fields kept).
        setInvestigateOrigin((d as { investigate?: import('../../lib/api').InvestigateOrigin | null }).investigate ?? null)
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
        setLoadingHistory(false)
      })
    }).catch((e) => {
      if (!alive) return
      setLoadingHistory(false)
      if (e instanceof ApiError && e.status === 404) { invalidateKeys(detailKey(sessionId)); setMissing(true) }
      else setLoadFailure(e)
    })
    return () => { alive = false }
  }, [sessionId, loadAttempt])
  // A closed chat follows no session. What it left in flight outlives it: a session read, the hold
  // that read put on the stream, the stall reconciler's read. When one of them lands or runs out it
  // meets this chat's session gates, which adopt a snapshot, apply a frame and settle a stall only
  // for the session the chat follows. Left in place, the session still matched them all, so the
  // frames a read held when the chat closed were applied to it, and a `chat_done` among them read
  // the session again for a chat no longer on screen. (StrictMode's rehearsal remount opens it again:
  // the load effect above sets the session.)
  useEffect(() => () => { sessionRef.current = null; turnDoneReads.current?.abort() }, [])
  const onWs = useCallback((m: WsMessage) => {
    const s = sessionRef.current
    const d = m.data || {}
    // ── THE session gate. Every case below inherits it and NONE re-checks. ──
    // This used to be duplicated as `if (d.session !== sessionRef.current) break` at the
    // top of ~11 cases, plus three near-misses that each compared something slightly
    // different (`d.session &&` …, `d.session === …`, and `d.key === …` for
    // session_title). Eleven copies of a rule are eleven chances for the twelfth push
    // kind to gate on the wrong thing — and the routing suggestion (issue 569) showed
    // the cost of identity resolution living in more than one place. One resolver,
    // shared with the send-response delivery path in `send()`; a session-less frame
    // (approval/voice/side-chat, keyed by id) passes, a foreign session is dropped here
    // and nowhere else. `chat/sessionDelivery.test.ts` fails if a case re-adds one.
    if (!deliverableToOpenSession(d.session, s)) return
    lastWsActivityRef.current = Date.now()  // for the idle approval-reconciler
    switch (m.type) {
      case 'chat_chunk': {
        const chunk = String(d.content ?? '')
        // No break-flag check here any more: whichever boundary preceded this chunk already
        // CLEARED the coalescer (endTextRun / dropTextRun), so a push always opens a fresh run
        // when it needs to. Deferring the clear to this branch is what left the other five
        // boundaries unguarded (#548). The gateway's stamp lets the coalescer drop a chunk the
        // transcript already shows because the snapshot it was rebuilt from held it.
        coalescer.push(chunk, typeof d.seq === 'number' ? d.seq : undefined)  // rAF-coalesced; onFlush does the setTurns once/frame
        break
      }
      // What the turn is doing, as the gateway measured it, and whether it takes a steer — applied
      // only when newer than what the page holds: a frame held while a snapshot was read can be
      // older than the snapshot's run state.
      // A helper's frame names the chat it works for too; its phase is its own (`isAChatsPhase`).
      case 'work_phase': if (isAChatsPhase(d)) takeRunState(d as unknown as RunState); break
      // Live reasoning stream. Only consumed while the Settings → Chat
      // "Show thinking inline" toggle is on — off drops the frame here, so no
      // thinking segment ever enters transcript state (and none is persisted:
      // the backend keeps thinking out of the response text and history).
      // Boundary discipline mirrors the tool/approval cards: END the text run (land any
      // buffered prose, then clear), then append-or-extend the thinking block. The next
      // chat_chunk opens a FRESH run rather than extending a segment that now sits above
      // the thinking block.
      case 'chat_thinking': {
        if (!showThinkingRef.current) break
        const chunk = String(d.content ?? '')
        if (!chunk) break
        endTextRun()
        patchLastAssistant((segs) => appendThinking(segs, chunk))
        break
      }
      // A non-streamed message appended server-side (the only one that reaches
      // the UI this way today is a turn-level `error` — e.g. a provider/model
      // rejection). Without this the turn ends blank ("no response").
      //
      // An error row is NOT the end of the turn: `chat_done` is, and it says how the turn ended.
      // A retry notice ("⟳ Connection lost — retrying...") is an error row, and the gateway then
      // runs the message again as the same chat's next turn. Ending streaming here offered Send
      // over that running retry and told a screen reader the answer was complete.
      case 'chat_message': {
        if (d.role === 'error') {
          endTextRun()  // land buffered text before the error segment
          const text = turnErrorText(d.content)
          // The Settings page the failure is lifted on, when the gateway names one (a spend cap).
          const meta = d.meta as { settings?: unknown } | undefined
          const settings = typeof meta?.settings === 'string' ? meta.settings : undefined
          // Idempotent: a snapshot read in flight when the turn failed can already show this
          // (persisted) error by the time the held frame replays on top of it.
          patchLastAssistant((segs) => {
            const last = segs[segs.length - 1]
            return last?.kind === 'error' && last.text === text ? segs : [...segs, { kind: 'error', text, ...(settings ? { settings } : {}) }]
          })
        } else if (d.role === 'notice') {
          // What happened to the conversation, in the gateway's words (a turn moved to another
          // agent): neither the agent's answer nor an error. Idempotent like the error above.
          const text = String(d.content ?? '')
          if (!text) break
          endTextRun()
          patchLastAssistant((segs) => {
            const last = segs[segs.length - 1]
            return last?.kind === 'activity' && last.text === text ? segs : [...segs, noticeSegment(text)]
          })
        } else if (d.role === 'assistant') {
          // A line the gateway wrote into the transcript itself — a compaction's outcome, the notice
          // that it restarted the session at the context threshold, a locally answered command. It
          // streamed nowhere (a streamed answer's words arrive as `chat_chunk`, never as this), so
          // this frame is the page's only news of it; dropping it left the line to the next reload.
          // Joined to the answer as `hydrateTurns` joins consecutive assistant messages, so live and
          // reloaded read the same. Idempotent like the error above: the transcript's announcement
          // and the notice's own frame both carry it, and a held frame can replay over a snapshot
          // that already holds it.
          const text = String(d.content ?? '')
          if (!text) break
          endTextRun()
          patchLastAssistant((segs) => {
            const last = segs[segs.length - 1]
            return last?.kind === 'text' && last.text === text ? segs : [...segs, { kind: 'text', text }]
          })
        } else if (d.role === 'tool' && !(d.meta as { tool_call_id?: string } | undefined)?.tool_call_id) {
          // A line the gateway wrote about a step, folded as a reload folds it (`foldStepLine`): a
          // note on the call it names, or on the turn. A call's own row is drawn from its
          // `tool_call` frames, never from this one. A note can land on the turn itself, so the
          // text run ends first, as it does for a notice.
          const meta = d.meta as Parameters<typeof foldStepLine>[1]
          if (noteOf(meta)) endTextRun()
          const line = String(d.content ?? '')
          patchLastAssistant((segs) => foldStepLine(segs, meta, line))
        }
        break
      }
      case 'activity_event': {
        // Coarse native activity. Skip generic status/session noise (covered by
        // the thinking indicator); surface only substantive lines (tool/hook/
        // permission/context), and only if this turn has no real tool cards yet.
        const kind = String(d.kind ?? '')
        const text = String(d.text ?? '')
        // The skills that joined this turn, announced once as the turn is put together. The
        // turn's chip names them from here — whatever the turn goes on to do, even if it only
        // calls tools or is stopped before it says a word — so the line itself is not repeated.
        if (kind === 'skills') {
          const joined = Array.isArray(d.skills) ? (d.skills as SkillUsed[]) : []
          if (joined.length) setTurns((prev) => {
            const list = ensureAssistant(prev)
            const i = list.length - 1
            const next = [...list]
            next[i] = { ...next[i], skillsUsed: joined }
            return next
          })
          break
        }
        // The saved prompt her message ran, announced as it expands: its text folds under her
        // bubble, labelled as the prompt's, while the bubble keeps what she typed.
        if (kind === 'prompt') {
          const ran = ranPromptOf(d.prompt)
          if (ran) setTurns((prev) => {
            const i = prev.map((t) => t.role).lastIndexOf('user')
            return i < 0 ? prev : prev.map((t, j) => (j === i ? { ...t, ranPrompt: ran } : t))
          })
          break
        }
        // Said elsewhere on this page: the session line, the loop breaker's notes (on the call's card
        // and as the turn's error) and a steer's acknowledgement (her steer is shown itself).
        if (kind === 'session' || kind === 'loop_breaker' || kind === 'steer' || !text) break
        // The turn's stats line names the model that answered and how it was chosen: the turn's
        // chip and the composer's Auto say it from here, the line itself goes to the ledger below.
        if (kind === 'stats') {
          const served = servedModelOf(d as { model?: unknown; chosen?: unknown })
          if (served) setTurns((prev) => {
            const list = ensureAssistant(prev)
            const i = list.length - 1
            const next = [...list]
            next[i] = { ...next[i], servedModel: served }
            return next
          })
        }
        // Which learning path emitted a `learned` event. Absent on every other
        // activity kind, and absent on a `learned` event from a build — which
        // `learnedSurface()` renders as a non-tappable chip rather than a wrong link.
        const origin = String(d.origin ?? '')
        // What undoing a learned preference needs (its key), when the emitter sent one.
        const ref = String(d.ref ?? '')
        // Keeps a mid-stream activity line BEFORE the coalescer's active text run so the next
        // flush replaces-in-place instead of pushing a duplicate; de-dupes adjacent
        // identical lines; tool cards win; carries `origin` onto the new segment. Whether the
        // run is live is decided HERE, not inside the updater: the stats line that ends every
        // turn can share a render batch with `chat_done`, which releases the run first.
        patchLastAssistant(textRun.activity(text, kind, origin, ref))
        break
      }
      // The turn's steps, as the shared reducers apply each frame (`liveToolFrames`) — the live
      // half of what `hydrateTurns` rebuilds after a reload, so the two read the same.
      case 'tool_call':
        endTextRun()  // land any buffered text before the tool card
        patchLastAssistant((segs) => applyToolCallFrame(segs, d))
        break
      case 'tool_result':
        patchLastAssistant((segs) => applyToolResultFrame(segs, d))
        break
      case 'approval':
        endTextRun()  // land buffered text before the approval card
        patchLastAssistant((segs) => applyApprovalFrame(segs, d))
        break
      case 'approval_resolved':
        // Matched by the chat's own id, like the card was created; the session gate above has
        // already dropped a frame for another chat, which may be waiting on the same bare id.
        setTurns((prev) => prev.map((t) => ({ ...t, segments: applyApprovalResolved(t.segments, d) })))
        break
      // An agent's question to her (`questionFrames`): its card, and how it ended.
      case 'question_card':
        endTextRun()  // land buffered text before the question card
        patchLastAssistant((segs) => applyQuestionFrame(segs, d))
        break
      case 'question_resolved':
        setTurns((prev) => prev.map((t) => ({ ...t, segments: applyQuestionResolved(t.segments, d) })))
        break
      case 'chat_segment': endTextRun(); break
      // What the chat runs on now — changed here, in another tab or by any caller of the API —
      // so the composer never names an agent the next message will not go to.
      case 'session_binding':
        sessionBindingRef.current = {
          agent: String(d.agent ?? ''), model: String(d.model ?? ''),
          acp_provider: String(d.acp_provider ?? ''), acp_provider_agent: String(d.acp_provider_agent ?? ''),
          reasoning_effort: String(d.reasoning_effort ?? ''),
        }
        bindingToldRef.current = true
        setBindingNonce((n) => n + 1)
        // Where it works and the chain its next turn on Auto takes, which its folder decides.
        if (typeof d.workspace_dir === 'string') setWorkingDir(d.workspace_dir)
        if (typeof d.auto_chain === 'string') setAutoChain(d.auto_chain)
        break
      // The agent cleared the conversation: the gateway emptied the transcript and says
      // "Conversation cleared." in it next (a `chat_message`), which is all a reload shows.
      case 'session_clear': dropTextRun(); setTurns([]); break
      // A regenerated answer landed (fresh reply → new variant) OR the user switched
      // which variant is active (here or in another tab). The backend has already
      // swapped the stored content; reflect it in place: replace the LAST assistant
      // turn's text with the echoed content and update the ‹n/N› switcher state. No
      // refetch — the event is authoritative. (Only meaningful once >1 variant.)
      case 'chat_variant_switch': {
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
      // What the runtime reported a model call of this chat's turn used. Counted for the turn that
      // runs; a frame with no running turn here says nothing about one.
      case 'token_usage': {
        if (!streamingRef.current) break
        const input = Number(d.input) || 0
        const output = Number(d.output) || 0
        if (input || output) setTurnUsage((u) => ({ input: (u?.input ?? 0) + input, output: (u?.output ?? 0) + output }))
        break
      }
      case 'context_usage':
        // A non-number `pct` (null) is the backend saying "not measured" — clear the
        // ring rather than leaving a stale or fabricated percentage on screen. A frame
        // without `window` (a restart at the threshold) leaves the window it last named.
        contextSaidLive.current = true
        takeContextUsage(d as { pct?: number | null; window?: number | null; parts?: ContextPart[] | null })
        break
      // A title resolved server-side (auto-titled after the first turn, or renamed
      // from elsewhere). Reflect it live in the header of the open session, so the
      // bare session key swaps to the real title with no reload.
      case 'session_title': {
        const key = String(d.key ?? '')
        const t = String(d.title ?? '')
        if (t && deliverableToOpenSession(key, sessionRef.current)) { setTitle(t); setTitleNeed('') }
        break
      }
      // The gateway's session list, pushed whenever a chat changes: this chat's bookmarks as stored,
      // so a bookmark set in another window or on the phone shows here too.
      case 'sessions': {
        const row = (Array.isArray(m.data) ? m.data : []).find((r: { key?: unknown }) => r?.key === s)
        if (row && Array.isArray(row.bookmarks)) setBookmarks(row.bookmarks.filter((t: unknown): t is string => typeof t === 'string'))
        break
      }
      case 'chat_done': {
        endTextRun()  // fully reveal any buffered tail before the turn closes
        markStreaming(false, chatDoneOutcome(d))
        // A turn her rapid follow-up replaced (cancel-and-replace) is said as that: its answer was
        // cut where it stood, and her new message runs next, queued server-side.
        if (d.superseded) setSrAnnounce(TURN_SUPERSEDED)
        sinks.current.onReplyFinished(sessionRef.current, { last: true })
        stoppedTurnRef.current = false
        setSteered([])  // steers belong to the turn they were injected into
        // Mid-stream messages are queued SERVER-side and the backend dispatches
        // the next one itself (streaming will flip back on via the next turn's
        // events). Nothing to drain client-side.
        // Refresh the instant-paint cache from the now-current turns so a revisit
        // seeds the LATEST transcript. This is a silent background write (no
        // skeleton, no visible reload) — the open tab already shows the right
        // content from streaming; we only bring the cached snapshot up to date.
        const sk = sessionRef.current
        // One pair of these reads at a time: the next turn's supersede this turn's, so they abort
        // it, and so does leaving the chat. A read nobody waits for keeps a connection otherwise.
        turnDoneReads.current?.abort()
        const reads = new AbortController()
        turnDoneReads.current = reads
        // Refresh the session cost chip now the turn's ledger row has landed.
        refreshSessionCost(sk, reads.signal)
        if (sk) api.chatSessionDetail(sk, { signal: reads.signal }).then((d) => {
          writeCachedDetail(sk, d)
          // Episodic citations live on the persisted assistant message's meta,
          // not in the WS stream — so the just-streamed turn shows plain `[Memory N]`
          // text until they're grafted on. Copy them from the fresh snapshot onto the
          // matching in-place turns so the chips light up without a visible re-hydrate.
          // Episodic recall injects once per session (the new-session turn), so there is
          // at most one such message; a persisted turn matches by ts, and the trailing
          // just-streamed turn (which has no ts yet) is grafted positionally. Cheap:
          // no-op on the overwhelming majority of turns (no episodic recall).
          if (sk !== sessionRef.current) return
          setTitleNeed(d.title_needs_model || '')
          // A page that missed the frame saying the turn ended times its answer from the rows, as a
          // reload does (`turnRuns`). Not once the next turn runs: the answer at the end is its.
          if (!streamingRef.current) {
            const read = hydrateTurns(d.messages || [])
            setTurns((prev) => withRowRuns(prev, read))
          }
          const byTs = new Map<string, MemoryCitation[]>()
          let lastCites: MemoryCitation[] | null = null
          // A reply that did not finish (`finish_reason`: cut at the output cap, or cut off before
          // the model finished) is stamped on the same meta and is just as invisible to the WS
          // stream. It describes ONE turn, so only the snapshot's LAST assistant message may speak
          // for the trailing just-streamed turn.
          const cutByTs = new Map<string, ReplyCut>()
          let lastCut: ReplyCut | undefined
          // "Ran on X instead of Y" is stamped the same way, for the same one turn.
          const subByTs = new Map<string, string>()
          let lastSub = ''
          for (const m of d.messages || []) {
            if (m.role !== 'assistant') continue
            lastCut = replyCutOf(m.meta)
            if (lastCut && m.ts) cutByTs.set(m.ts, lastCut)
            lastSub = m.meta?.model_substitution || ''
            if (lastSub && m.ts) subByTs.set(m.ts, lastSub)
            const c = m.meta?.memory_citations
            if (Array.isArray(c) && c.length) {
              lastCites = c
              if (m.ts) byTs.set(m.ts, c)
            }
          }
          if (byTs.size || lastCites || cutByTs.size || lastCut || subByTs.size || lastSub) setTurns((prev) => {
            const lastIdx = prev.map((t) => t.role).lastIndexOf('assistant')
            return prev.map((t, i) => {
              if (t.role !== 'assistant') return t
              const patch: Partial<ChatTurn> = {}
              if (!t.citations) {
                if (t.ts && byTs.has(t.ts)) patch.citations = byTs.get(t.ts)
                // Trailing streamed turn with no ts → attach the session's one manifest.
                else if (i === lastIdx && !t.ts && lastCites) patch.citations = lastCites
              }
              if (!t.cutOff) {
                if (t.ts && cutByTs.has(t.ts)) patch.cutOff = cutByTs.get(t.ts)
                else if (i === lastIdx && !t.ts && lastCut) patch.cutOff = lastCut
              }
              if (!t.modelSubstitution) {
                if (t.ts && subByTs.has(t.ts)) patch.modelSubstitution = subByTs.get(t.ts)
                else if (i === lastIdx && !t.ts && lastSub) patch.modelSubstitution = lastSub
              }
              return Object.keys(patch).length ? { ...t, ...patch } : t
            })
          })
          // How the turn's attached images reached the model is decided server-side once the
          // serving model is known, and recorded on the USER message's meta — invisible to the
          // WS stream like the citations above. Only the snapshot's LAST user message may speak
          // for the just-sent turn, and only a turn that carries files can take it.
          const lastUser = [...(d.messages || [])].reverse().find((m) => m.role === 'user')
          const delivered = imageDeliveryOf(lastUser?.meta)
          if (delivered) setTurns((prev) => {
            const i = prev.map((t) => t.role).lastIndexOf('user')
            if (i < 0 || !prev[i].files?.length) return prev
            return prev.map((t, j) => (j === i ? { ...t, imageDelivery: delivered } : t))
          })
          // The prompt that message ran, for a page that missed its live announcement.
          const ran = ranPromptOf(lastUser?.meta?.ran_prompt)
          if (ran) setTurns((prev) => {
            const i = prev.map((t) => t.role).lastIndexOf('user')
            if (i < 0 || prev[i].ranPrompt) return prev
            return prev.map((t, j) => (j === i ? { ...t, ranPrompt: ran } : t))
          })
        }).catch(() => {})
        break
      }
      // Visible message queue (mid-stream sends). The server owns the FIFO; these
      // events keep the strip above the composer in sync.
      case 'queue_push': {
        const id = String(d.queue_id ?? '')
        if (id) setQueued((prev) => (prev.some((q) => q.id === id) ? prev : [...prev, queuedMessage(id, String(d.content ?? ''), d.pastes)]))
        // A steer the turn ended without taking, queued to run next: it is in the strip now.
        if (d.steer_ts) setSteered((prev) => prev.filter((s) => s.ts !== d.steer_ts))
        break
      }
      case 'queue_pop':
      case 'queue_cancel': {
        const id = String(d.queue_id ?? '')
        if (id) setQueued((prev) => prev.filter((q) => q.id !== id))
        break
      }
      // A queued item was promoted to the front (interrupt-now). Reorder the strip
      // so the promoted card jumps to the top on every client (it runs next).
      case 'queue_promoted': {
        const id = String(d.queue_id ?? '')
        if (id) setQueued((prev) => {
          const i = prev.findIndex((q) => q.id === id)
          if (i <= 0) return prev
          const next = [...prev]; next.unshift(next.splice(i, 1)[0]); return next
        })
        break
      }
      // Follow-up chips: 2-3 suggested next messages for the just-
      // completed turn. Render under the last assistant turn; any user activity clears.
      case 'chat_followups': {
        const items = Array.isArray(d.items) ? d.items.filter((x): x is string => typeof x === 'string') : []
        setFollowups(items)
        setFollowupsNeed(typeof d.needs_model === 'string' ? d.needs_model : '')
        break
      }
      // "Check this work" offer for the just-completed turn.
      case 'chat_check_work_offer': {
        const prompt = String(d.prompt ?? 'check your work')
        setCheckWorkOffer({ label: String(d.label ?? 'Check this work'), prompt })
        break
      }
      // Agent routing suggestion: a specialist fits this message
      // better — surface the routing chip above the composer (non-blocking proposal).
      // No session comparison here: the payload carries the session the SERVER named,
      // and `deliverableToOpenSession` decides at the render gate whether that is the
      // session on screen. One resolution point, so this transport and the send-response
      // one cannot drift apart. (The same push also reaches OTHER clients viewing this
      // chat; the sender's own copy comes back on its send response, because this frame
      // is emitted while a just-created session's socket is still reconnecting — 569.)
      case 'routing_suggestion': {
        const agent = String(d.agent ?? '')
        if (!agent || !d.session) break
        sinks.current.setRouting({
          session: String(d.session), agent,
          specialty: String(d.specialty ?? ''),
          score: typeof d.score === 'number' ? d.score : 0,
          method: String(d.method ?? ''),
        })
        break
      }
      // True rewind: the edited turn's later messages were discarded
      // (retained in history server-side) and the provider was reset. Re-hydrate from
      // the now-truncated transcript so the divider chip + tail disclosure appear.
      case 'chat_rewound': {
        const sk = sessionRef.current
        if (sk) readSnapshot(sk, (det) => { writeCachedDetail(sk, det); adoptSnapshot(det); return true }).catch(() => {})
        break
      }
      // A queued message the server just dequeued and is about to run. Normal sends
      // add the user bubble optimistically; queued ones only had a strip card, so
      // render the bubble now (the strip card is removed by the paired queue_pop).
      // dropTextRun so the next chat_chunk starts a fresh assistant turn beneath it — and
      // discards, not lands: the user bubble is pushed below, so a landed tail would end up
      // in the NEW turn.
      case 'chat_user_message': {
        const content = String(d.content ?? '')
        if (!content) break
        // Replayed after a snapshot that already holds this message: the turn it opens is
        // already on screen, and dropping the text run here would cut the answer that snapshot
        // resumed in two.
        if (d.ts && adoptedUserTs.current.has(String(d.ts))) break
        // Her bubble shows each block she pasted as its chip, as the chat does read back.
        const pastes = pastesOf(d.pastes)
        const sentTurn = userTurn(shownText(content, pastes), d.ts ? String(d.ts) : undefined, pastes)
        // Her steer, which the running turn has just taken: her bubble goes here, below what the
        // answer said so far (settled by the `chat_segment` before this frame), and the answer
        // goes on below it. Nothing ends or starts: it is the same turn, and it is running.
        if (d.steer) {
          endTextRun()
          setSteered((prev) => prev.filter((s) => s.ts !== d.ts))
          setTurns((prev) => [...prev, { ...sentTurn, steered: true }])
          markStreaming(true)
          break
        }
        // The reply before this queued message is finished: read it out if it is owed.
        sinks.current.onReplyFinished(sessionRef.current, { last: false })
        dropTextRun()
        setFollowups([])  // a new turn is starting (queued drain) — clear stale chips
        setFollowupsNeed('')
        setTurns((prev) => [...prev, sentTurn])
        // The gateway sends this only as it STARTS the turn, so the turn is running whatever
        // this page believed a moment ago. After a Stop the composer has already settled, and a
        // message queued behind the stopping turn starts next ("Session reset — processing next
        // message"); it used to stream its whole answer under a Send button.
        markStreaming(true)
        break
      }
      // Async subagent lifecycle (fire-and-forget). Cards live in the Activity
      // panel's Subagents tab (`foldSubagentEvent`), a helper that never ran among them.
      case 'subagent_spawn':
      case 'subagent_tool':
      case 'subagent_done':
        setSubagents((prev) => foldSubagentEvent(prev, m.type, d))
        break
      // Speak (stage 4): the backend streams base64 WAV per sentence for
      // immediate playback. Queue them so sentences play in order. Every tab with this chat open
      // receives them; only the tab whose reading they are plays them.
      case 'voice_chunk':
        if (d.audio && d.request) sinks.current.onVoiceChunk(String(d.audio), d.request)
        break
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
  // A socket that (re)opens after this session was last read can have missed frames while it
  // was down — a drop, or a mount read that gave up waiting for it — including the `chat_done`
  // that would have ended the turn. Read again and adopt: the snapshot holds
  // everything broadcast before it (its `running` corrects the streaming claim), and the
  // frames held while it was in flight replay on top.
  const resync = useCallback(() => {
    const s = sessionRef.current
    if (!s) return
    // A context reading said live before the drop is no newer than this read: frames sent while
    // the link was down (a compaction's) are lost, so the snapshot's reading is the current one.
    // Frames arriving during the read are held and replayed on top, so a newer live one still wins.
    contextSaidLive.current = false
    readSnapshot(s, (d) => {
      adoptSnapshot(d)
      // A turn that ended while the socket was down is said as the snapshot reports it ended.
      const running = !!d.run_state.turn
      markStreaming(running, turnOutcomeOf(d.run_state.last_outcome))
      // An idle chat has no stopped turn still sending: its `chat_done` may be what was missed.
      if (!running) stoppedTurnRef.current = false
      return true
    }).catch(() => {})
  }, [])
  // Link state, and the moment this chat starts LISTENING: subscribed while the tab's socket is
  // open (told at once when it already is). The gateway registers a socket in the same step
  // that accepts it, so every frame broadcast from `onopen` on reaches it.
  const onSocketStatus = useCallback((connected: boolean) => {
    setWsConnected(connected)
    listening.current.open = connected
    if (!connected) return
    listening.current.waiters.splice(0).forEach((wake) => wake())
    if (rereadOnOpen.current) { rereadOnOpen.current = false; resync() }
  }, [resync])
  // Every frame the socket delivers: held while a snapshot read holds the stream (readSnapshot
  // replays it), applied now otherwise — and recorded while any read is out, for the late
  // adoption of one that stopped holding (snapshotReplay.ts).
  const onSocketFrame = useCallback((m: WsMessage) => {
    // A turn this tab did not start: read the chat once so its question and Stop appear. The
    // read holds this frame and the ones after it, and replays them on top of the snapshot.
    const following = streamingRef.current || stoppedTurnRef.current
    if (!snapshots.busy() && joinsATurnStartedElsewhere(m, sessionRef.current, following)) resync()
    if (!snapshots.hold(m)) onWs(m)
  }, [onWs, resync])
  useChatSocket(onSocketFrame, resync, onSocketStatus)

  // Idle stream-reconciler. A streaming claim can outlive the turn it describes in two
  // ways, and BOTH are silent — no `chat_done`, no error, and nothing on screen that says
  // the page has stopped tracking the run. So while streaming, once the WS has been quiet
  // for a beat, read session detail and let the server settle it. `resolveStalledStream`
  // owns the two readings and the reasoning for each (a turn parked on an approval whose
  // card never arrived; a turn that FINISHED while nothing was listening), and lives beside
  // this file because nothing in `web/` can mount it — the rule is assertable, the render is
  // not. Self-healing + cheap: fires only inside a silent-while-streaming window, and tears
  // itself down the moment the claim is corrected.
  useEffect(() => {
    // Only a chat opened on a session reconciles it. A new chat learns its key from the send that
    // creates the session and hands the run to the page that replaces it, so a tick of its own
    // between the two read the session beside the replacement's read.
    if (!streaming || !sessionId) return
    const s = sessionId
    // Restarts on every transcript change (the `turns` dep), so a turn that is still
    // painting can never satisfy the settled grace. That is what keeps a send whose dispatch
    // has not landed yet out of it — see STREAM_SETTLED_GRACE_MS.
    const transcriptChangedAt = Date.now()
    const iv = window.setInterval(() => {
      if (Date.now() - lastWsActivityRef.current < 3500) return  // WS still active — no need
      const showingApproval = turns.some((t) => t.segments.some((sg) => (sg.kind === 'approval' && !(sg as ApprovalSegment).resolved)
        || (sg.kind === 'question' && (sg as QuestionSegment).answerable && !(sg as QuestionSegment).outcome)))
      if (showingApproval) return  // card already up
      // One read at a time. A snapshot read in flight settles the claim itself, and this
      // reconciler's own read still out has not answered yet — issuing another every tick is
      // how a slow gateway collected one read per two seconds, each holding the stream.
      if (snapshots.busy() || probing.current) return
      const activityAtIssue = lastWsActivityRef.current
      probing.current = true
      // A plain read, holding nothing: it only acts on a stream that stayed silent throughout.
      api.chatSessionDetail(s).then((d) => {
        if (sessionRef.current !== s) return
        // A frame arrived while it was out, or a snapshot read began: the stream is alive (or
        // that read settles it), and this snapshot can already be older than the transcript.
        if (lastWsActivityRef.current !== activityAtIssue || snapshots.busy()) return
        const stall = resolveStalledStream({
          serverRunning: !!d.run_state.turn,
          serverPendingApproval: d.run_state.phase === 'approval',
          serverPendingQuestion: (d.pending_questions?.length ?? 0) > 0,
          msSinceTranscriptChange: Date.now() - transcriptChangedAt,
        })
        if (stall === 'wait') return  // genuinely just quiet (e.g. long model think) — leave it
        // The server's transcript is authoritative for both readings, so adopt it first and
        // act second. `settled` is by definition no turn running, so adopting it also ends the
        // text run without landing a buffered tail into the history that replaced it.
        adoptSnapshot(d)
        if (stall === 'settled') {
          // The server holds no task for this session, so nothing is in flight and the
          // composer's Stop button and the suppressed assistant action row are both lying:
          // the terminal frame this socket should have delivered never arrived. Said out
          // loud, because a safety net that catches silently hides the loss it caught — the
          // e2e turn driver fails on this line, so a turn that only completed because of the
          // net cannot read as a turn that completed.
          console.warn(`[chat] ${s}: ${STREAM_HEAL_WARNING} — settled from session detail`)
          // The lost frame carried how the turn ended; session detail serves the same fact.
          markStreaming(false, turnOutcomeOf(d.run_state.last_outcome))
          return
        }
        // Server is parked on an approval the client isn't showing → recovered above.
        lastWsActivityRef.current = Date.now()  // don't re-fire every tick
      }).catch(() => {}).finally(() => { probing.current = false })
    }, 2000)
    return () => window.clearInterval(iv)
  }, [streaming, turns, sessionId])

  return {
    // The session the chat follows, and its transcript.
    sessionRef, turns, setTurns, patchLastAssistant, dropTextRun, readSnapshot, adoptSnapshot,
    // The messages bookmarked on its Session Map.
    bookmarks, setBookmarks,
    // The live turn: the streaming claim, how it ends, what it is doing, whether it takes a steer.
    streaming, streamingRef, markStreaming, sayTurnEnded, srAnnounce, turnSeqRef, stoppedTurnRef,
    takesSteers, takesSteersRef, turnPhase, turnLine, turnEnding, turnStart, turnUsage, sessionSkillsEpoch, wsConnected,
    // The read that opened the session.
    loadingHistory, missing, setMissing, loadFailure, setLoadAttempt,
    // What the frames and the reads say beside the transcript.
    queued, setQueued, steered, setSteered, subagents, setSubagents,
    sideMsgs, setSideMsgs, sideBusy, setSideBusy, sideOpenedRef,
    followups, setFollowups, followupsNeed, setFollowupsNeed, checkWorkOffer, setCheckWorkOffer,
    contextPct, contextWindow, contextParts, autoChain, setAutoChain, workingDir, setWorkingDir,
    sessionBindingRef, bindingToldRef, bindingNonce,
    title, setTitle, titleNeed, setTitleNeed, sessionCost,
    branchedFrom, startedBy, investigateOrigin,
  }
}

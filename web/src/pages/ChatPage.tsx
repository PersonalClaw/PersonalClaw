import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { ResultAnnouncement } from '../ui/ListControls'
import { failureSentence, reportActionFailure, reportingWrite } from '../app/reportingWrite'
import { unavailableWhen, BUSY_REASON } from '../ui/unavailable'

import { fvs, withWeight } from '../design/fontWeight'
import { playCue } from '../design/soundCues'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import { Edit3, History, Search, MessageSquare, Trash2, Activity, ChevronRight, ChevronDown, Quote, PanelRight, Clipboard, X, Pin, BookText, AlertTriangle, Pencil, Sparkles, Link2, Check, Repeat, Rewind, PlayCircle, GitBranch, Folder, FolderPlus, Tag as TagIcon, Columns3, List as ListIcon, ListChecks, Filter, EyeOff, Clock, Loader2, Wrench, Target, Code2 as CodeIcon, ArrowLeft, ArrowRight, ArrowUp, GripVertical, Bot, ShieldCheck, ShieldAlert, Shield, Eye, Zap, ClipboardList, Hammer, Camera, NotebookPen, FolderCog, Archive, ArchiveRestore, Boxes, CornerDownLeft, Download, Share2, ListTree, Scissors, Shuffle, Send } from 'lucide-react'
import { IconButton } from '../ui/IconButton'
import { SquareIconButton } from '../ui/SquareIconButton'
import { SearchField } from '../ui/SearchField'
import { TopBar } from '../ui/TopBar'
import { SidePanel } from '../ui/SidePanel'
import { Button } from '../ui/Button'
import { Checkbox, useSyncedDraft } from '../ui/forms'
import { QuietButton } from '../ui/QuietButton'
import { SelectionToolbar } from '../ui/SelectionPill'
import { Segmented } from '../ui/Segmented'
import { Meter } from '../ui/Meter'
import { ContextMenu, type ContextMenuItem } from '../ui/motion'
import { ProjectPicker } from '../ui/ProjectPicker'
import { HeaderActions, HeaderControl, HeaderSegmented, HeaderModePill } from '../ui/HeaderActions'
import { ClawMark } from '../ui/ClawMark'
import { ComposerStage } from '../ui/ComposerStage'
import { CollapseColumnButton, CollapsedBoardColumn, boardGridTemplate, useBoardCollapse } from '../ui/BoardCollapse'
import { PromptPalette } from './chat/PromptPalette'
import { SessionSkillsReview } from './chat/SessionSkillsReview'
import { RoutingChip, type RoutingSuggestion } from './chat/RoutingChip'
import { ComposerNoticeLine, useComposerNotice } from '../ui/composer/ComposerNotice'
import { useVoiceConfig } from './chat/voiceConfig'
import { deliverableToOpenSession } from './chat/sessionDelivery'
import { ModelWaits } from '../ui/ModelWaitNotice'
import { joinsATurnStartedElsewhere } from './chat/joinTurn'
import { MEMORY_MODES, MEMORY_MODE_NOTICE } from './chat/memoryModeCopy'
import { sessionRowMeta } from './chat/sessionRowMeta'
import { AppPermissionNotice, StartedByApp, startedByName } from './chat/StartedByApp'
import { FromChannel } from './chat/FromChannel'
import { chatContextChips } from './chat/ChatContextLine'
import { snapshotPredatesSend, streamingAtMount } from './chat/liveRun'
import { OrganizeChip } from './chat/OrganizeChip'
import { ContextLedger } from './chat/ContextLedger'
import { chatFindPath, searchCoverage, searchSourceLabel } from './chat/searchDeepLink'
import { useScreenShare } from '../ui/composer/useScreenShare'
import { DotGlow } from '../ui/DotGlow'
import { EmptyState, ListSkeleton, LoadError, Skeleton, LoadingStatus } from '../ui/ListScaffold'
import { WindowedList } from '../ui/WindowedList'
import { FieldError } from '../ui/forms'
import { MessageUser } from '../ui/chat/MessageUser'
import { MessageAssistant } from '../ui/chat/MessageAssistant'
import { Spark } from '../ui/Spark'
import { StreamingIndicator } from '../ui/chat/StreamingIndicator'
import { ChatPlanGate } from '../ui/chat/ChatPlanGate'
import { Markdown } from '../ui/Markdown'
import { useWidgetActionBridge, takePendingWidgetAction } from '../ui/widget/useWidgetActionBridge'
import { InlineError } from '../ui/InlineError'
import { TextLink } from '../ui/TextLink'
import { PartialNotice } from '../ui/PartialNotice'
import { NoModelSetupState, isNoModelSetupError, MODELS_PATH } from './chat/NoModelSetupState'
import { BundledFloorNotice } from './chat/BundledFloorNotice'
import { ToolCard } from './chat/ToolCard'
import { onToolResultFull } from './chat/toolResultBridge'
import { withoutFence } from '../lib/untrustedFence'
import { SdlcProgressCard, sdlcRefFromTool } from './chat/SdlcProgressCard'
import { WorkflowProgressCard, liveWorkflowCards, workflowRefFromTool } from './chat/WorkflowProgressCard'
import { ManualAutomationCard, manualAutomationFromTool } from './chat/ManualAutomationCard'
import { ApprovalCard } from './chat/ApprovalCard'
import { QuestionCard, type QuestionReply } from './chat/QuestionCard'
import { RoomView } from './chat/RoomView'
import { RoomsScope } from './chat/RoomsScope'
import { ChatFilePanel } from './chat/ChatFilePanel'
import { sameSessionTarget, type CommentTarget } from '../ui/content/commentTarget'
import { ChatActivityPanel } from './chat/ChatActivityPanel'
import { AssistantActions, UserActions } from './chat/MessageActions'
import { askToRepeat } from './chat/repeatedSteps'
import { parseOptions, parseSwitchToAgent } from './chat/parseAssistant'
import { type PasteBlock, shouldCollapsePaste, nextSeq, makePasteId, markerFor, expandPasteMarkers, pruneBlocks } from './chat/pasteBlocks'
import { sessionTemplatePatch } from './chat/sessionTemplate'
import { Modal } from '../ui/Modal'
import { confirm, promptInput } from '../ui/dialog'
import { type ChatTurn, type Segment, type ToolSegment, type ApprovalSegment, type QuestionSegment, type ActivitySegment, type ThinkingSegment, type ErrorSegment, appendThinking, type SubagentCard, type HistMsg, type MemoryCitation, type SkillUsed, userTurn, assistantTurn, hydrateTurns, livePartialOf, turnText, failedStepCount, unaskedStepCount, foldStepLine, noteOf, LEDGER_ACTIVITY_KINDS, deriveActivity, markCoordOf, skillsUsedLabel, skillsUsedTitle, imageDeliveryOf, noticeSegment, ranPromptOf } from './chat/chatTypes'
import { isImagePath } from './chat/imageAttachments'
import { AttachmentChips, TurnAttachments } from './chat/AttachmentChips'
import { applyApprovalFrame, applyApprovalResolved, applyToolCallFrame, applyToolResultFrame } from './chat/liveToolFrames'
import { applyQuestionFrame, applyQuestionResolved, graftPendingQuestions } from './chat/questionFrames'
import { waitsPastItsTurn } from './chat/approvalSegment'
import { ThinkingBlock } from './chat/ThinkingBlock'
import { branchIndexOf, branchParentKey } from './chat/branchLineage'
import { buildOptimizerContext } from './chat/optimizerContext'
import { optimizeFailure, optimizeOutcome } from '../ui/composer/optimizeOutcome'
import { useIdentity, firstNameOf } from '../app/identity'
import { usePlatform } from '../app/usePlatform'
import { SnipOverlay } from '../ui/SnipOverlay'
import { chooseCaptureProvider, cropToPngFile, displayCaptureSupported, grabOneFrame, type SnipRect } from '../ui/composer/displayCapture'
import { notify } from '../app/appSdk'
import { spring, stagger, listItemEnter, expr } from '../design/motion'
import { api, ApiError, hasApiCode, isSwitchedOff, transcriptionFailure, type ApprovalMode, type TaskMode, type ReasoningEffort, type ChatSessionSummary, type ChatHistoryMsg, type DiscoveredAgent, type MemoryMode, type NudgeLoop, type ChatFolder, type ChatTag, type RetagJob, type SessionTemplate, type RewindFileWire, type ChannelRuntime, type SessionSearchAnswer } from '../lib/api'
import { refreshKinds, useChatSocket, type WsMessage } from '../lib/useChatSocket'
import { useStreamCoalescer } from './chat/useStreamCoalescer'
import { FindBar } from '../ui/FindBar'
import { findSegments } from './chat/findSegments'
import { turnErrorText } from './chat/turnError'
import { editReplacesLaterTurns, replacedTurnsAreKept } from './chat/editReplaces'
import { FollowupChips, followupAnnouncement } from './chat/FollowupChips'
import { CheckWorkChip } from './chat/CheckWorkChip'
import { SessionMapReturnLatest, scrollToLatest } from './chat/SessionMapReturnLatest'
import { SessionMapRail } from './chat/SessionMapRail'
import { SessionMapDrawer } from './chat/SessionMapDrawer'
import { sessionMapEntries } from './chat/sessionMap'
import { TextRunOwnership } from './chat/coalesceReducers'
import { SnapshotReplay } from './chat/snapshotReplay'
import { resolveStalledStream, STREAM_HEAL_WARNING } from './chat/streamStall'
import { chatDoneOutcome, TURN_RESPONDING, turnEndedSentence, turnOutcomeOf, type TurnOutcome } from './chat/turnOutcome'
import { useQuery, invalidateKeys, peekQuery, writeQuery } from '../lib/data'
import { downloadFrom } from '../lib/download'
import { sessionRecencyMs, sessionActivitySeconds, epochSeconds } from '../lib/epoch'
import { sessionTitle } from '../lib/sessionTitle'
import { useComposerData } from '../lib/useComposerData'
import type { ComposerControls, ComposerValue } from '../ui/composer/types'
import { Popover, MenuRow } from '../ui/Popover'
import { useQueryFlag, useQueryParam, type RouteProps } from '../app/useQueryState'
import { useIsMobile } from '../app/useIsMobile'
import { copyText } from '../app/clipboard'

// Instant-paint cache for opened chat sessions, held in the ONE data layer.
//
// 🔴 THIS WAS A SECOND CACHE. It was a private `sessionStorage` store under its own
// `chat-detail:` prefix, with its own reader and writer and NO age on the record — the exact
// hand-rolled fetch-and-cache shape DSC-14 converges, and invisible to any census of the shared
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
type ChatDetail = Awaited<ReturnType<typeof api.chatSessionDetail>>
const detailKey = (key: string) => `chat:detail:${key}`
const readCachedDetail = (key: string): ChatDetail | null => peekQuery<ChatDetail>(detailKey(key)) ?? null
function writeCachedDetail(key: string, d: ChatDetail): void {
  // Never cache a running turn's partial transcript — it would paint a stale,
  // mid-stream snapshot on revisit. Only settled sessions are safe to seed from.
  if (d.running) return
  // A Temporary chat is forgotten when its session ends, so its copy lives in this page only,
  // never in the browser's session storage, where it would outlive the chat.
  writeQuery(detailKey(key), d, d.memory_mode !== 'temporary')
}

/** How long a chat's mount read waits for the chat to be listening (see the load effect in
 *  ChatSession). It is a cost bound, not a correctness one: a read that gives up is taken again
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
  'chat_chunk', 'chat_status', 'chat_thinking', 'chat_message', 'activity_event', 'tool_call',
  'tool_result', 'approval', 'approval_resolved', 'chat_segment', 'chat_variant_switch',
  'chat_done', 'chat_user_message', 'session_clear',
])

// The approval-card scope picker's one vocabulary (resolved with the user): a per-
// approval SCOPE choice, not a mode toggle. `approved` = allow once; `trust` = allow
// all tools this session (sets session trust); `trust_agent` = always allow all tools
// for this agent (persists AgentProfile.approval_mode="auto") + this session; `rejected`
// = deny. (`trust_reads`/`yolo` remain valid backend actions the Permission axis uses,
// but the card no longer offers them — the card speaks only scope.)
type ApproveAction = 'approved' | 'rejected' | 'trust' | 'trust_agent' | 'trust_reads' | 'yolo'

/** Answer an ask raised by work the chat started (`ApprovalSegment.queued`) where it is held, the
 *  approvals queue, by its registry id. Its card offers Allow and Deny alone; anything but an
 *  Allow is a Deny. */
function answerQueued(id: string, action: ApproveAction) {
  api.resolveApproval(id, action === 'approved' ? 'approve' : 'reject')
    .catch(reportActionFailure('record your decision'))
}

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

/** The transcript's bottom fade: opaque down to its bottom padding (`py-2xl` on the turn
 *  column), transparent at the edge. Keyed to that padding so the resting view — scrolled
 *  to the newest turn — is never faded; see the scroller for why the edge fades at all.
 *  (A mask reads only alpha, so `black` here is "keep", not a colour.) */
const TRANSCRIPT_END_FADE = 'linear-gradient(to bottom, black calc(100% - var(--spacing-2xl)), transparent)'

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
  // No `.catch`: this key is PERSISTED, so a swallowed rejection wrote a fabricated `[]` into
  // sessionStorage as though it were an answer, and the next visit painted "no suggestions" from
  // cache. Without it the rejection leaves `data` undefined and nothing is cached. The strip still
  // hides on failure, which is honest — a decoration that quietly does not appear claims nothing.
  const { data, loading } = useQuery('chat:suggestions', () => api.suggestions().then((r) => r.suggestions), { persist: true })
  const items = (data ?? []).slice(0, 6)
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
    <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }}>
      {items.map((s, i) => (
        <motion.button key={i} type="button" onClick={() => onPick(s)}
          initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: 0.04 * i }}
          className="rounded-pill border border-outline-variant/60 bg-surface-container px-3.5 py-2 text-left text-[0.8125rem] text-on-surface-var transition-colors hover:border-primary/40 hover:bg-surface-high hover:text-on-surface">
          {s}
        </motion.button>
      ))}
    </div>
  )
}

/** Saved starters on the new-chat screen.
 *
 *  Picking one PREFILLS the composer selection (and the prompt, if the template has
 *  one) instead of creating a session server-side. The plan's §C3 sketched a
 *  `create_from_template() -> session_key`, but this page mints a session lazily on
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
        setBusy(!!d.running)
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
  '- `/rewind-to-turn N` — restore the FILES this chat changed after turn N (preview first; add `--confirm` to apply). The conversation is not rewound.',
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
  // #/chat/room/<id> → one Agent Room. A room is a MODE of this page rather than a nav peer
  // (AGENT-ROOMS C9): there is no sidebar to be a peer of, the session list is this page, and
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

/** How many of each chat's replies answer a message THIS tab sent. Settings → Speech &
 *  Transcription → "Speak replies aloud" reads such a reply out here, if it is on when the reply
 *  finishes, and in no other tab, so a chat open in two tabs is read out once. Module scope, not a
 *  ref: a new chat's first send remounts the session view under the created id, and its reply
 *  finishes there. */
const repliesToSpeak = new Map<string, number>()

function ChatSession({ sessionId, navigate, query, setQuery, projectId: initialProjectId = '', seed = '', agent: initialAgent = '', routing: pendingRouting, setRouting: setRoutingSuggestion, liveRun, setLiveRun }: { sessionId: string | null; navigate: (p: string, opts?: { replace?: boolean }) => void; query: Record<string, string>; setQuery: RouteProps['setQuery']; projectId?: string; seed?: string; agent?: string; routing: RoutingSuggestion | null; setRouting: (s: RoutingSuggestion | null) => void; liveRun: string; setLiveRun: (s: string) => void }) {
  const data = useComposerData()
  const { name } = useIdentity()
  // The project this chat scopes under. Seeded from the launch URL (?project=<id> from a
  // project page's Chat button), but ALSO user-pickable on a bare new chat via the
  // composer's project chooser (the vision's "optional project chooser"). Frozen once the
  // session starts (project_id is fixed at create, like memory mode).
  const [projectId, setProjectId] = useSyncedDraft(initialProjectId)
  // Name of the project this chat is bound to — shown as a header chip so the user knows
  // the chat is scoped to that project's workspace.
  const [projectName, setProjectName] = useState('')
  useEffect(() => {
    if (!projectId) { setProjectName(''); return }
    let alive = true
    api.project(projectId).then((p) => { if (alive) setProjectName(p?.name || '') }).catch(() => {})
    return () => { alive = false }
  }, [projectId])
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
  const [input, setInput] = useState(seed)
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
  // (skill-ephemeral-promotion) re-checks for drafts the agent just captured.
  const [sessionSkillsEpoch, setSessionSkillsEpoch] = useState(0)
  // Screen-reader narration of the turn lifecycle: the visual "Thinking"/glow cue is silent to
  // assistive tech, so a polite live region says a turn started, and then how it ENDED, in the
  // words `chat/turnOutcome.ts` owns for the outcome the gateway reported. Never inferred from
  // `streaming` going false: Stop, a failed turn and a retry notice all end streaming too, and
  // every one of them used to be announced as "Response complete."
  const [srAnnounce, setSrAnnounce] = useState('')
  // Whether the turn this view follows has ended without the region saying how. Stop settles the
  // composer at once, before the gateway has said the turn stopped; whichever terminal fact comes
  // first (the Stop answer, `chat_done`, a snapshot's `last_turn_outcome`) says it, once.
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
  // 🔴 THE CHAT THIS ROUTE NAMES DOES NOT EXIST. The detail read answered 404 — or it stopped
  // existing under us and a send answered `session_not_found`. Before this, the load's catch
  // only cleared the skeleton, so a dead link rendered exactly like a fresh empty chat and its
  // composer took a message the server then refused and did not save (day-56b `s33`). Terminal
  // for this mount: the page says the chat is gone and offers a new one instead.
  const [missing, setMissing] = useState(false)
  // A read that failed for any OTHER reason is not evidence that the chat is gone, so it is
  // shown as a load failure with a retry — never as an empty chat, and never as "not found".
  const [loadFailure, setLoadFailure] = useState<unknown>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const composerRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  // true when the transcript is scrolled away from the bottom — drives the
  // "jump to latest" pill (so streamed content arriving above the fold isn't lost).
  const [scrolledUp, setScrolledUp] = useState(false)
  // Armed by a send: follow THIS turn to its outcome — see the auto-scroll effect. Seeded
  // from `streaming`'s INITIAL value, which is true only when this mount is the replacement
  // for the instance whose first send created the session (`streamingAtMount`): that send
  // re-keys and remounts ChatSession, and the turn it asked for must still be followed here
  // (the measured 60k-paste case was exactly a new chat's first message).
  const followTurnRef = useRef(streaming)
  const followNewTurn = () => { followTurnRef.current = true }
  // WS link state — false while the socket is down (drives the reconnecting cue).
  const [wsConnected, setWsConnected] = useState(true)
  // activity panel (Stage 5) — Index/Files/Links, chat-only, toggled from header.
  // URL-backed (?activity=1, push → Back closes it; refresh restores it).
  const [activityOpen, setActivityOpen] = useQueryFlag(query, setQuery, 'activity')
  // New-chat page: a right-docked chat-history panel (the shared SidePanel), toggled
  // from the header. URL-bound (?history=1) so Back closes it + refresh restores it.
  const [historyOpen, setHistoryOpen] = useQueryFlag(query, setQuery, 'history')
  // ── SESSION MAP ────────────────────────────────────────────────────────────────────────────
  // The in-session index: a fixed rail in the transcript's left gutter on a pointer device, a
  // tappable SidePanel drawer on the mobile form. Open/closed is URL state so it is
  // deep-linkable and survives a reload, exactly like `?activity`.
  //
  // 🪤 NOT `useQueryFlag`, and the reason is the DEFAULT. §A.1 makes the map always-available, so
  // its default is OPEN — and a present-or-absent flag can only record the non-default, i.e. it
  // could never express "this user closed it". `useQueryParam` with a '1' default is the same
  // one-key URL contract in both directions: open is the clean URL, `?map=0` records a
  // deliberate close and restores it on refresh. (§A.9 named `useQueryFlag`; this is that
  // mechanism's two-way form, not a second one — same module, same `setQuery`.)
  //
  // Which FORM the map takes. `useIsMobile` (the app's ≤768px breakpoint) rather than a
  // `(pointer: coarse)` query of its own: the shell already switches its nav rail to a drawer on
  // exactly this signal, and a second "is this the touch form?" mechanism would be free to
  // disagree with the first. It is also the discriminator a browser gate can drive — a mobile
  // VIEWPORT is what `web/e2e/a11y.spec.ts` can set; pointer type is not.
  const isMobile = useIsMobile()
  // 🪤 THE DEFAULT IS PER-FORM, AND A SINGLE `'1'` DEFAULT WAS A REAL DEFECT rather than a
  // preference. The "always available" is what the gutter rail delivers for free — it occupies
  // the empty left column §A.0 measured, so open-by-default costs the transcript nothing. The
  // mobile form is not that: it is the shared `SidePanel`, a docked column whose fit-width fills a
  // 390px viewport, so defaulting it open means the drawer is ALREADY COVERING the composer the
  // moment `started` flips — a phone user arrives at a chat they cannot type into. Measured, not
  // reasoned: `a11y.spec.ts`'s mobile tier timed out clicking `Message input` with the panel's
  // resize separator (`aria-valuenow=420`) intercepting the pointer, and `sessionMap.spec.ts`'s
  // own mobile test drives three turns and THEN taps the control to open the drawer, i.e. both
  // gates were written against a closed default.
  //
  // The URL contract is unchanged and still two-way in both forms, because `useQueryParam` writes
  // `null` exactly when the value equals the default it was given: on a pointer device the clean
  // URL means open and `?map=0` records a deliberate close; on the mobile form the clean URL means
  // closed and `?map=1` records a deliberate open. One key, one mechanism, one control — only the
  // resting state differs, which is the thing the two forms genuinely do not share.
  const [mapFlag, setMapFlag] = useQueryParam(query, setQuery, 'map', isMobile ? '0' : '1')
  const mapOpen = mapFlag === '1'
  const setMapOpen = (on: boolean) => setMapFlag(on ? '1' : '0')
  // per-turn DOM nodes so the Index tab and the Session Map can scroll to a turn.
  //
  // 🔑 KEYED BY THE MAP'S COORDINATE (`markCoordOf`), NOT BY ARRAY POSITION, and this was a real
  // defect rather than a preference. `hydrateTurns` collapses loop re-injections and merges
  // consecutive assistant messages, so a turn's array position runs BEHIND its
  // `visibleIndex` — the coordinate every `SessionMark` carries and the one the rail hands back
  // through `onJumpTo`. Registered under `i`, a mark jump on any tool-using transcript resolved
  // an EARLIER turn's node (the same gap `branchLineage.ts` documents for the fork coordinate).
  // One key, defined once on the map's contract, so the registry and the marks cannot disagree.
  const turnNodes = useRef<Map<number, HTMLDivElement>>(new Map())
  // Find-in-conversation — a client-side find bar over the hydrated
  // turns, opened with Cmd/Ctrl+F while the chat page owns focus + a session is open.
  const [findOpen, setFindOpen] = useState(false)
  // Close the find bar when the open session changes (its matches no longer apply).
  useEffect(() => { setFindOpen(false) }, [sessionId])
  // Deep-link from chat-history search: a result opened this session with
  // `?find=<term>`. `findSeed` is the term to pre-seed the find bar with — captured into
  // state so it survives clearing the URL param, and keyed onto the FindBar so a fresh
  // deep-link into an already-open bar re-seeds it. A ⌘F open always clears it first, so
  // the manual find bar is never the deep-link's leftover term.
  const [findParam, setFindParam] = useQueryParam(query, setQuery, 'find', '', { replace: true })
  const [findSeed, setFindSeed] = useState('')
  // Ordered AFTER the sessionId-reset effect above so, on a cross-session deep-link
  // (ChatSession remounts under the new session key), the fresh find param OPENS the bar
  // rather than the reset closing it. Seed + open, then drop `?find` (replace) so a
  // re-render or Back-nav does not re-fire — after the clear `findParam` is '' and the
  // guard returns. The bar itself does the scroll-to-first-match from the seed.
  useEffect(() => {
    if (!findParam) return
    setFindSeed(findParam)
    setFindOpen(true)
    setFindParam('')
  }, [findParam]) // eslint-disable-line react-hooks/exhaustive-deps -- setFindParam is per-render; findParam drives it
  // Follow-up chips: 2-3 suggested next messages pushed over the
  // chat_followups WS after a reply completes. Cleared when the user types, sends or a new
  // turn starts, and reset per session. A pick leaves them in place: taking them away on the
  // first click moved the scrolled transcript under the pointer, so the second click of a
  // double-click landed on the reply's own actions (measured: on Speak, then Stop).
  const [followups, setFollowups] = useState<string[]>([])
  useEffect(() => { setFollowups([]) }, [sessionId])
  // "Check this work" offer: pushed over chat_check_work_offer when
  // the completed turn did 3+ tool calls AND claimed completion. An OFFER only — clicking
  // sends the prompt, so verification is never spent without the user asking for it.
  const [checkWorkOffer, setCheckWorkOffer] = useState<{ label: string; prompt: string } | null>(null)
  useEffect(() => { setCheckWorkOffer(null) }, [sessionId])
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
  // uploaded-attachment workspace paths (threaded into send meta.files, B0) +
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
  const [promptHistory, setPromptHistory] = useState<string[]>([])
  // `undefined` until the backend reports a measurement (it sends `pct: null` when it
  // has none) — an unmeasured context must show no percentage, not 0%.
  const [contextPct, setContextPct] = useState<number | undefined>(undefined)
  // The window the gateway named with its reading: `null` = none is declared or served,
  // `undefined` = not said yet. It is what lets the unmeasured dot say WHY (`ModelPill`).
  const [contextWindow, setContextWindow] = useState<number | null | undefined>(undefined)
  // A reading that arrived live outranks the one a later snapshot carries, which is older.
  const contextSaidLive = useRef(false)
  const takeContextUsage = (u: { pct?: number | null; window?: number | null }) => {
    setContextPct(typeof u.pct === 'number' ? u.pct : undefined)
    if ('window' in u) setContextWindow(typeof u.window === 'number' ? u.window : null)
  }
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
  const [memoryMode, setMemoryMode] = useState<MemoryMode>('persistent')
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
  const [investigateOrigin, setInvestigateOrigin] = useState<import('../lib/api').InvestigateOrigin | null>(null)
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
  // Messages the server confirmed it STEERED into the running turn. Distinct
  // from `queued`: a steered message has no queue id and nothing to cancel — it is
  // already inside the answer being written. Shown so a steer isn't invisible (the
  // backend's "Steering: …" activity_event is deliberately filtered as status noise),
  // and cleared when the turn ends since it belongs to that turn.
  const [steered, setSteered] = useState<string[]>([])
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
  // The reads a finished turn starts (its cost, its snapshot), so the next turn's abort them.
  const turnDoneReads = useRef<AbortController | null>(null)
  const [selection, setSelection] = useState<ComposerValue>({ agent: initialAgent, model: 'Auto', approval: 'normal', taskMode: 'agent', reasoning: '' })
  // the resumed session's agent/model binding (from detail), restored into the
  // composer selection once discovered agents load. bindingNonce re-fires the
  // restore effect when a new session's binding arrives.
  const sessionBindingRef = useRef<{ agent: string; model: string; acp_provider: string; acp_provider_agent: string; reasoning_effort: string } | null>(null)
  const [bindingNonce, setBindingNonce] = useState(0)
  // Set when the gateway said the chat now runs on another binding (`session_binding`), so the
  // composer shows it even when it is the default agent — whoever made the change.
  const bindingToldRef = useRef(false)
  // Natural voice: the per-conversation scope. `choice` is what this
  // conversation states; `effective`/`source` are the backend's resolution against
  // the bound agent's definition — never recomputed here. `source: ''` means "no
  // conversation yet" (a brand-new chat), where the pill shows the choice instead.
  const [naturalVoice, setNaturalVoice] = useState<{ choice: '' | 'on' | 'off'; effective: boolean; source: string; agentDefault: boolean }>(
    { choice: '', effective: false, source: '', agentDefault: false })
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
  const [regenningTitle, setRegenningTitle] = useState(false)
  // P15 rAF stream coalescer: chat_chunk pushes into this; it flushes ONE growing
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
  // "Show timestamps" — the same persisted-query shape as the read above, and read HERE because
  // this is the component that owns a turn's `ts`. Gating happens by NOT PASSING the stamp down, so
  // the action row never learns about the preference: absent means "no time", which is also what a
  // turn mid-stream (no stamp yet) means, so one branch covers both. Default off, matching the
  // config default, so an unresolved read shows nothing rather than flashing times on and off.
  const { data: showTimestamps } = useQuery('chat:show-timestamps', () => api.dashboardConfig().then((c) => c.show_timestamps), { persist: true })
  const stampOf = (turn: { ts?: string }) => (showTimestamps ? turn.ts : undefined)
  // The chat channels this chat can continue on — each connected channel with an owner to reach
  // (the Web UI has none). Not Settings → Providers' `settings:channels` key: that read keeps its
  // catch, so the `[]` it caches on a failure would reach this one as a success. A pairing on either
  // page busts both with `invalidateKeys('settings:channels', true)`. A failed read offers no channel.
  const { data: channelList, error: channelListError } = useQuery('settings:channels-owners', () => api.channels(), { persist: true })
  const handoffChannels = channelListError ? [] : (channelList ?? []).filter((c) => c.owner)
  const showThinkingRef = useRef(false)
  useEffect(() => { showThinkingRef.current = !!showThinkingCfg }, [showThinkingCfg])
  const coalescer = useStreamCoalescer((revealed) => patchLastAssistant(textRun.flush(revealed)),
    { immediate: streamRevealCfg === 'immediate' })
  // 🔴 K44 / issue #548 — ONE mechanism for ending a coalesced text run, in two flavours, and
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
  const started = turns.length > 0
  // show the thinking indicator while streaming and the active assistant turn
  // has produced nothing renderable yet (no text/tool/approval segment)
  const lastTurn = turns[turns.length - 1]
  // thinking indicator only when the active turn has produced nothing visible
  // (an activity line counts as visible, so we don't stack two indicators)
  const showThinking = streaming && (!lastTurn || lastTurn.role === 'user' || lastTurn.segments.length === 0)

  // While a turn runs, its status line ("Thinking…", a tool at work) is narrated as it changes.
  // The start and the end are said where they happen (`markStreaming`, `sayTurnEnded`).
  useEffect(() => { if (streaming && statusText) setSrAnnounce(statusText) }, [streaming, statusText])

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
    const running = !!d.running
    // …with the questions its turn is waiting on her answer to, which no row holds yet.
    setTurns(graftPendingQuestions(hydrateTurns(messages, running), d.pending_questions))
    setTakesSteers(running && !!d.steerable)
    adoptedUserTs.current = new Set(messages.flatMap((m) => (m.role === 'user' && m.ts ? [m.ts] : [])))
    // The answer still being written continues IN the segment the snapshot paints for it:
    // ownership is claimed before the coalescer can flush, so the next flush extends the
    // partial rather than pushing a second copy beside it.
    const partial = running ? livePartialOf(messages) : null
    if (partial !== null) textRun.adopt(); else textRun.release()
    coalescer.resume(partial, d.stream_seq ?? 0)
    // What the ring was last told, so a chat opened after its turn draws the same ring.
    if (d.context_usage && !contextSaidLive.current) takeContextUsage(d.context_usage)
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
        if (d.running || streamingRef.current) markStreaming(!!d.running, turnOutcomeOf(d.last_turn_outcome))
        return true
      }).then((d) => {
        if (!alive) return
        // cache the settled detail so the next revisit paints instantly (writeCachedDetail
        // skips running turns to avoid seeding a stale mid-stream snapshot).
        writeCachedDetail(sessionId, d)
        // rehydrate any still-pending queued messages (mid-stream FIFO) so a reload
        // mid-queue shows them again above the composer.
        setQueued(Array.isArray(d.queue) ? d.queue.filter((q) => q && q.id).map((q) => ({ id: q.id, content: q.content })) : [])
        // Seed ↑/↓ prompt-history from the conversation's existing user turns, so
        // recall works immediately on a revisited chat (not only after sending a
        // new message this render). Oldest→newest, deduped against repeats.
        setPromptHistory(hydrateTurns(d.messages || []).reduce<string[]>((acc, t) => {
          if (t.role !== 'user') return acc
          const txt = turnText(t).trim()
          if (txt && acc[acc.length - 1] !== txt) acc.push(txt)
          return acc
        }, []).slice(-50))
        setTitle(d.title || '')
        // Seed the session cost chip from the ledger for a revisited chat.
        refreshSessionCost(sessionId)
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
        // Natural voice — restore the composer pill from the RESOLVED state the
        // backend sent, so a reopened chat shows what actually takes effect (including
        // an agent-supplied default) rather than reverting to "agent default, off".
        setNaturalVoice({
          choice: ((d.natural_voice || '') as '' | 'on' | 'off'),
          effective: !!d.natural_voice_effective,
          source: d.natural_voice_source || '',
          agentDefault: !!d.natural_voice_agent_default,
        })
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
        setInvestigateOrigin((d as { investigate?: import('../lib/api').InvestigateOrigin | null }).investigate ?? null)
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
        setStatusText('')
        const chunk = String(d.content ?? '')
        // No break-flag check here any more: whichever boundary preceded this chunk already
        // CLEARED the coalescer (endTextRun / dropTextRun), so a push always opens a fresh run
        // when it needs to. Deferring the clear to this branch is what left the other five
        // boundaries unguarded (#548). The gateway's stamp lets the coalescer drop a chunk the
        // transcript already shows because the snapshot it was rebuilt from held it.
        coalescer.push(chunk, typeof d.seq === 'number' ? d.seq : undefined)  // rAF-coalesced; onFlush does the setTurns once/frame
        break
      }
      case 'chat_status': setStatusText(String(d.status ?? '')); break
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
          setStatusText(''); setLatestActivity(null)
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
        if (kind === 'status' || kind === 'session' || !text) break
        setLatestActivity(text)
        // Which learning path emitted a `learned` event. Absent on every other
        // activity kind, and absent on a `learned` event from a build — which
        // `learnedSurface()` renders as a non-tappable chip rather than a wrong link.
        const origin = String(d.origin ?? '')
        // What undoing a learned preference needs (its key), when the emitter sent one.
        const ref = String(d.ref ?? '')
        // Keeps a mid-stream activity line BEFORE the coalescer's active text run so the next
        // flush replaces-in-place instead of pushing a duplicate (K42); de-dupes adjacent
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
      // Whether the running turn takes a steer, said when the turn wires its runtime.
      case 'turn_steerable': setTakesSteers(!!d.steerable); break
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
      case 'context_usage':
        // A non-number `pct` (null) is the backend saying "not measured" — clear the
        // ring rather than leaving a stale or fabricated percentage on screen. A frame
        // without `window` (a restart at the threshold) leaves the window it last named.
        contextSaidLive.current = true
        takeContextUsage(d as { pct?: number | null; window?: number | null })
        break
      // A title resolved server-side (auto-titled after the first turn, or renamed
      // from elsewhere). Reflect it live in the header of the open session, so the
      // bare session key swaps to the real title with no reload.
      case 'session_title': {
        const key = String(d.key ?? '')
        const t = String(d.title ?? '')
        if (t && deliverableToOpenSession(key, sessionRef.current)) setTitle(t)
        break
      }
      case 'chat_done': {
        endTextRun()  // fully reveal any buffered tail before the turn closes
        markStreaming(false, chatDoneOutcome(d)); setStatusText(''); setLatestActivity(null)
        replyFinished(sessionRef.current, { last: true })
        stoppedTurnRef.current = false
        setSteered([])  // steers belong to the turn they were injected into
        // Cancel-and-replace: this turn was superseded by a
        // rapid follow-up. The replacement was queued server-side and the next turn
        // auto-runs; surface a brief note so the truncated answer doesn't read as a
        // silent failure. No ghost bubble — the turn closes cleanly here.
        if (d.superseded) setStatusText('Superseded by your new message…')
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
          const byTs = new Map<string, MemoryCitation[]>()
          let lastCites: MemoryCitation[] | null = null
          // A reply cut at the output cap (`finish_reason: 'length'`) is stamped on the same meta
          // and is just as invisible to the WS stream. It describes ONE turn, so only the
          // snapshot's LAST assistant message may speak for the trailing just-streamed turn.
          const cutByTs = new Set<string>()
          let lastIsCut = false
          // "Ran on X instead of Y" is stamped the same way, for the same one turn.
          const subByTs = new Map<string, string>()
          let lastSub = ''
          for (const m of d.messages || []) {
            if (m.role !== 'assistant') continue
            lastIsCut = m.meta?.finish_reason === 'length'
            if (lastIsCut && m.ts) cutByTs.add(m.ts)
            lastSub = m.meta?.model_substitution || ''
            if (lastSub && m.ts) subByTs.set(m.ts, lastSub)
            const c = m.meta?.memory_citations
            if (Array.isArray(c) && c.length) {
              lastCites = c
              if (m.ts) byTs.set(m.ts, c)
            }
          }
          if (byTs.size || lastCites || cutByTs.size || lastIsCut || subByTs.size || lastSub) setTurns((prev) => {
            const lastIdx = prev.map((t) => t.role).lastIndexOf('assistant')
            return prev.map((t, i) => {
              if (t.role !== 'assistant') return t
              const patch: Partial<ChatTurn> = {}
              if (!t.citations) {
                if (t.ts && byTs.has(t.ts)) patch.citations = byTs.get(t.ts)
                // Trailing streamed turn with no ts → attach the session's one manifest.
                else if (i === lastIdx && !t.ts && lastCites) patch.citations = lastCites
              }
              if (!t.cutOff && ((t.ts && cutByTs.has(t.ts)) || (i === lastIdx && !t.ts && lastIsCut))) {
                patch.cutOff = true
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
        const id = String(d.queue_id ?? ''); const content = String(d.content ?? '')
        if (id) setQueued((prev) => (prev.some((q) => q.id === id) ? prev : [...prev, { id, content }]))
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
        setRoutingSuggestion({
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
        // The reply before this queued message is finished: read it out if it is owed.
        replyFinished(sessionRef.current, { last: false })
        dropTextRun()
        setFollowups([])  // a new turn is starting (queued drain) — clear stale chips
        setTurns((prev) => [...prev, userTurn(content, d.ts ? String(d.ts) : undefined)])
        // The gateway sends this only as it STARTS the turn, so the turn is running whatever
        // this page believed a moment ago. After a Stop the composer has already settled, and a
        // message queued behind the stopping turn starts next ("Session reset — processing next
        // message"); it used to stream its whole answer under a Send button.
        markStreaming(true)
        break
      }
      // Async subagent lifecycle (fire-and-forget). Cards live in the Activity
      // panel's Subagents tab; the final output also posts to the transcript.
      case 'subagent_spawn': {
        const id = String(d.id ?? '')
        if (!id) break
        // A task of a batch this chat started arrives with its batch's run and its step's name.
        const batchTask = d.run ? { run: String(d.run), title: String(d.title ?? '') } : {}
        setSubagents((prev) => prev.some((s) => s.id === id) ? prev
          : [...prev, { id, task: String(d.task ?? ''), agent: String(d.agent ?? ''), done: false, ...batchTask }])
        break
      }
      case 'subagent_tool': {
        const id = String(d.id ?? '')
        if (id) setSubagents((prev) => prev.map((s) => s.id === id ? { ...s, lastTool: String(d.tool ?? '') } : s))
        break
      }
      case 'subagent_done': {
        const id = String(d.id ?? '')
        if (id) setSubagents((prev) => prev.map((s) => s.id === id
          ? { ...s, done: true, error: (d.error as string | null) ?? null, elapsed: typeof d.elapsed === 'number' ? d.elapsed : undefined, result: String(d.result ?? ''),
              costUsd: typeof d.cost_usd === 'number' ? d.cost_usd : undefined, tokens: typeof d.tokens === 'number' ? d.tokens : undefined }
          : s))
        break
      }
      // Speak (stage 4): the backend streams base64 WAV per sentence for
      // immediate playback. Queue them so sentences play in order. Every tab with this chat open
      // receives them; only the tab whose reading they are plays them.
      case 'voice_chunk':
        if (d.audio && d.request && d.request === readingRef.current?.request) enqueueAudio(String(d.audio))
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
    readSnapshot(s, (d) => {
      adoptSnapshot(d)
      // A turn that ended while the socket was down is said as the snapshot reports it ended.
      markStreaming(!!d.running, turnOutcomeOf(d.last_turn_outcome))
      // An idle chat has no stopped turn still sending: its `chat_done` may be what was missed.
      if (!d.running) { setStatusText(''); stoppedTurnRef.current = false }
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
          serverRunning: !!d.running,
          serverPendingApproval: !!d.pending_approval,
          serverPendingQuestion: (d.pending_questions?.length ?? 0) > 0,
          msSinceTranscriptChange: Date.now() - transcriptChangedAt,
        })
        if (stall === 'wait') return  // genuinely just quiet (e.g. long model think) — leave it
        // The server's transcript is authoritative for both readings, so adopt it first and
        // act second. `settled` is by definition `!d.running`, so adopting it also ends the
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
          markStreaming(false, turnOutcomeOf(d.last_turn_outcome)); setStatusText(''); setLatestActivity(null)
          return
        }
        // Server is parked on an approval the client isn't showing → recovered above.
        lastWsActivityRef.current = Date.now()  // don't re-fire every tick
      }).catch(() => {}).finally(() => { probing.current = false })
    }, 2000)
    return () => window.clearInterval(iv)
  }, [streaming, turns, sessionId])

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

  // Cmd/Ctrl+F → open the in-conversation find bar, but ONLY when a
  // session is open and the chat page owns focus (not typing in another field). A
  // SECOND press or Esc closes it and falls through to the browser's native find.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'f' || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return
      if (!sessionRef.current) return  // no open session → let the browser find run
      const t = e.target as HTMLElement | null
      // Don't hijack when the user is typing in a different input/editor — except our
      // own find input, where a repeat ⌘F should close (toggle) rather than no-op.
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) {
        const inFind = !!t.closest('[role="search"]')
        if (!inFind) return
      }
      e.preventDefault()
      // A manual ⌘F is always an EMPTY bar — clear any leftover deep-link seed so
      // re-opening after a `?find=` deep-link doesn't resurrect that term.
      setFindSeed('')
      setFindOpen((o) => !o)
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

  // Her answer to an agent's question, or her Skip. The card says a refusal itself; what took is
  // settled here at once, as the `question_resolved` frame that follows settles it.
  const answerQuestion = useCallback(async (id: string, reply: QuestionReply) => {
    const s = sessionRef.current
    if (!s) return
    const { outcome } = await api.answerChatQuestion(s, id, reply)
    const answers = 'answers' in reply ? reply.answers : undefined
    setTurns((prev) => prev.map((t) => ({ ...t, segments: applyQuestionResolved(t.segments, { id, outcome, ...(answers ? { answers } : {}) }) })))
  }, [])

  const approve = useCallback((id: string, action: ApproveAction) => {
    const s = sessionRef.current
    if (!s) return
    // A card action that raises the session's standing posture must move the
    // Permission-mode pill to match — otherwise the pill keeps claiming "Normal" (ask
    // before a tool that needs approval) while the session silently auto-approves (a dishonest
    // state). `trust`/`trust_agent` → this chat is now trusted; `trust_reads` →
    // read-only auto; `yolo` → everywhere. `approved`/`rejected` are single-shot and
    // leave the mode alone. Mirror-only (no extra API call — the approve request
    // already set the server-side flag).
    const raised: ApprovalMode | null =
      action === 'trust' || action === 'trust_agent' ? 'trust'
      : action === 'trust_reads' ? 'trust_reads'
      : action === 'yolo' ? 'yolo'
      : null
    // 🪤 THE MIRROR IS GATED ON THE WRITE, AND THE FAILURE IS REPORTED. Both halves used to be
    // wrong in the same direction. The request swallowed its rejection, and the mirror ran
    // regardless — so a failed `yolo` left the pill claiming this chat auto-approves everything
    // while the server was still asking. That is the EXACT INVERSE of the
    // dishonest state the comment above sets out to prevent, and it is a claim about a security
    // posture, not a cosmetic one. The pill is a mirror of a server flag, so it may only move once
    // the server has the flag; a failed decision now says so instead of being absorbed.
    //
    // The card itself needs nothing here: it renders from `seg.resolved`, which the backend
    // persists, so an unapproved tool call correctly stays unresolved and still asking.
    api.approve(s, action, id)
      .then(() => {
        if (raised) setSelection((sel) => (sel.approval === raised ? sel : { ...sel, approval: raised }))
      })
      .catch(reportActionFailure('record your decision'))
  }, [])

  // Auto-scroll the transcript to the bottom as content streams in — but only if
  // the user is already near the bottom (don't yank them while reading up)…
  //
  // 🔴 …OR THE USER JUST SENT THE TURN THAT IS ARRIVING. "Near the bottom" is measured AFTER
  // the render, so the user's own new message could unfollow the view by itself: measured, a
  // 60k-character paste put the bottom ~21,000px below the viewport the moment its bubble
  // rendered, and the turn's outcome — its error — landed out of sight, reachable only through
  // "Jump to latest". A send arms `followTurnRef` (`followNewTurn`), so the view follows that
  // turn to its outcome: the frame that ends it still scrolls, and then the arm is spent. A
  // scroll away from the bottom disarms it (the listener below), so a reader who scrolls up
  // mid-answer is left where they went.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 200
    if (followTurnRef.current || nearBottom) endRef.current?.scrollIntoView({ block: 'end' })
    if (!streaming) followTurnRef.current = false
  }, [turns, streaming, showThinking])

  // Track distance from the bottom so a "jump to latest" pill can show when the
  // user has scrolled up (e.g. reading history while a reply streams in below).
  // Content growth fires no scroll event, so a large gap HERE is the user's own movement —
  // the one thing that may cancel following a turn they sent.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      const up = el.scrollHeight - el.scrollTop - el.clientHeight > 240
      setScrolledUp(up)
      if (up) followTurnRef.current = false
    }
    setScrolledUp(el.scrollHeight - el.scrollTop - el.clientHeight > 240)
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [started])

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
        writeCachedDetail(created.key, { key: created.key, title: '', messages: seedMessages, running: false, memory_mode: memoryMode } as unknown as ChatDetail)
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
      // Natural voice: a pre-start pick lands here for the same reason — without
      // it the pill would read "Plain" while turn 1 ran without the instruction, which
      // is the exact "reports itself on while doing nothing" shape.
      if (naturalVoice.choice) await persistSelection('this natural-voice setting', api.setSessionNaturalVoice(created.key, naturalVoice.choice))
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

  async function send(
    text = input,
    opts?: { original?: string; inputOrigin?: string; uiLabel?: string },
  ) {
    const t = text.trim()
    if (!t) return
    // Sending dismisses any follow-up chips from the prior turn and
    // clears a pending routing suggestion — the moment passed.
    if (followups.length) setFollowups([])
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
      ensureSession()
        .then((s) => api.sendChat(t, s, { client_ts: steerTs }, takesSteersRef.current ? 'steer' : 'followup').then((r) => [s, r] as const))
        .then(([s, r]) => {
          setInput((cur) => (cur === t ? '' : cur))
          if (r?.steered) { setSteered((prev) => [...prev, t]); return }
          // Queued or dispatched fresh, it gets a reply of its own; a steer does not. Owed in the
          // session it was sent to: a queued answer names none, and this chat may have closed.
          oweSpokenReply(r?.session || s)
          if (r?.queued) return  // the paired queue_push frame renders the strip card
          // Dispatched as a fresh turn. Render exactly what the normal send path would:
          // the user's bubble, then arm streaming so its reply has somewhere to land.
          followNewTurn()
          setTurns((prev) => [...prev, userTurn(t, steerTs)])
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
    if (!uiLabel) setPromptHistory((prev) => { const h = original ?? t; return (prev[prev.length - 1] === h ? prev : [...prev, h]).slice(-50) })
    const knowledgeIds = mentionedKnowledge.map((k) => k.id)
    const artifactSlugs = mentionedArtifacts.map((a) => a.slug)
    // dropTextRun: a fresh send must open a NEW coalesced text run. A follow-up in an
    // existing chat streams in right after the prior turn — the backend does NOT always emit
    // a chat_done/chat_segment boundary between turns (esp. YOLO/queued dispatch), so without
    // this the new turn's chunks would append onto the PRIOR turn's still-live coalescer run
    // → turn N+1's bubble absorbed turn N's whole answer (K44). Safe on the very first turn
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
  // /rewind-to-turn N — the FILESYSTEM counterpart of /undo.
  // `confirm=false` renders the preview as an assistant notice and stops; `confirm=true`
  // applies. Nothing on disk changes on the preview pass.
  async function rewindToTurn(turn: number, confirm: boolean) {
    const s = sessionRef.current
    if (!s) return
    const fmt = (f: RewindFileWire) =>
      f.action === 'not_captured'
        ? `- \`${f.path}\` — NOT captured (${f.reason}); it will not be restored`
        : f.action === 'delete'
          ? `- \`${f.path}\` — would be DELETED (it did not exist at turn ${turn})`
          : f.action === 'unchanged'
            ? `- \`${f.path}\` — already matches turn ${turn}; no change`
            : `- \`${f.path}\` — restore ${f.current_size} → ${f.restored_size} bytes`
    try {
      if (!confirm) {
        const p = await api.rewindPreview(s, turn)
        const lines = [
          `**Rewind to turn ${turn} — preview.** Nothing has been written yet.`,
          ...(p.warnings || []).map((w) => `> ${w}`),
          ...(p.files || []).map(fmt),
          (p.files || []).length === 0 ? '_No recorded file changes after that turn._' : '',
          `Run \`/rewind-to-turn ${turn} --confirm\` to apply. This restores files only — the conversation stays as the record of what happened.`,
        ].filter(Boolean)
        setTurns((prev) => [...prev, assistantTurn(lines.join('\n'))])
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
      // assistant's voice (AUD-A9). The success notices above stay as assistant turns on purpose
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

  // ── message actions (stage 4) ──
  const [editingTurn, setEditingTurn] = useState<number | null>(null)
  // The inline editor's failure line: the edit could not be resent, and the editor stays open
  // with the text the user wrote, beside the button that failed.
  const [editFailure, setEditFailure] = useState<string | null>(null)

  // Regenerate, Edit & resend and Rewind each REPLACE turns that are on screen, and none of them
  // touches the page until the server has accepted the request. They used to remove the turns
  // first, so a refusal (a turn already running, the message gone) left the later turns missing
  // from the page with only a warning-sign line in the assistant's voice to say why. Now a
  // refusal leaves the page exactly as it was. Once the server accepts, the page adopts the
  // transcript the server just cut, instead of cutting its own copy to match: the new reply can
  // already be streaming by the time the response lands, and the snapshot read holds those
  // frames and replays them on top (see `readSnapshot`). `cutLocally` is the same cut made on
  // the page's own copy, for when that read fails.
  const replacingRef = useRef(false)
  async function replaceTurns(
    request: (session: string) => Promise<unknown>,
    cutLocally: (prev: ChatTurn[]) => ChatTurn[],
    onFailure: (e: unknown) => void,
  ): Promise<boolean> {
    const s = sessionRef.current
    if (!s || streamingRef.current || replacingRef.current) return false
    replacingRef.current = true
    try { await request(s) }
    catch (e) { onFailure(e); return false }
    finally { replacingRef.current = false }
    followNewTurn()
    // The coalescer still holds the PRIOR answer's run; the new reply must open its own (K44/K45).
    dropTextRun()
    markStreaming(true)
    readSnapshot(s, (d) => {
      adoptSnapshot(d)
      // A replacement that already ended by the time the read lands is said as it ended.
      markStreaming(!!d.running, turnOutcomeOf(d.last_turn_outcome))
      return true
    }).catch(() => setTurns(cutLocally))
    return true
  }

  async function regenerate() { await replay() }

  // A Retry over a turn whose finished steps may have changed something is answered with the
  // gateway's question instead (`chat/repeatedSteps`): the turn runs again only on her yes, sent
  // back as `confirmed`, and a No leaves the page as it was.
  async function replay(confirmed?: string) {
    await replaceTurns(
      (s) => (confirmed ? api.regenerate(s, confirmed) : api.regenerate(s)),
      // The last answer goes; the fresh reply streams in beneath its question.
      (prev) => { const i = prev.map((t) => t.role).lastIndexOf('assistant'); return i >= 0 ? prev.slice(0, i) : prev },
      (e) => { if (!askToRepeat(e, (yes) => replay(yes))) reportActionFailure('regenerate this reply')(e) },
    )
  }

  // Page to a prior/next regenerated answer. The backend swaps the active variant
  // and broadcasts chat_variant_switch, which the WS handler applies in place — so
  // this just fires the request (no optimistic mutation, the echo is authoritative).
  async function switchVariant(index: number) {
    const s = sessionRef.current
    if (!s || streaming) return
    try { await api.switchVariant(s, index) }
    catch (e) {
      notice.showError(`Couldn’t switch answer: ${(e as Error).message}`)
    }
  }

  // Branch this conversation at `turnIndex`. Branch DUPLICATES a timeline —
  // both stay live and equal — which is why it is one click with no confirmation:
  // it creates a new session and cannot overwrite anything in this one. (Rewind,
  // which replaces a timeline, does confirm.) A branch of a branch and repeated
  // branches off the same message are properties of the endpoint; nothing here
  // prevents either.
  //
  // The wire coordinate is the backend's VISIBLE user/assistant index, which is NOT
  // `turnIndex`: hydrateTurns collapses loop re-injections and merges consecutive
  // assistant messages, so on a tool-using transcript the turn position runs behind
  // the message index and the fork silently landed EARLIER than the clicked message.
  // branchIndexOf translates; see branchLineage.ts.
  function forkAt(turnIndex: number) {
    const s = sessionRef.current
    if (!s) return
    api.forkSession(s, branchIndexOf(turns, turnIndex))
      .then((r) => {
        if (!r?.key) return
        // Confirm on the SHELL toaster, not this page's inline strip: navigating to the
        // child unmounts this session, and a confirmation that dies with the surface
        // that raised it never gets read.
        notify('Session branched — you’re now in the new branch.', 'success')
        navigate(`chat/${r.key}`)
      })
      .catch((e: Error) => {
        // Surface the failure instead of a silent no-op — the user clicked Branch
        // and nothing happening looks broken. `e.message` is the endpoint's own
        // wording, so the session-cap 429 arrives readable ("session cap reached
        // (500)") rather than as a bare status.
        notice.showError(`Couldn’t branch this chat: ${e.message}`)
      })
  }

  async function editResend(turnIndex: number, content: string, rewind = false) {
    const t = content.trim()
    if (!t) return
    const turn = turns[turnIndex]
    // Locate the message by the ORIGINAL turn's ts (backend truncates from there),
    // and stamp the re-added turn with a FRESH ts that the backend also stores —
    // so an immediate SECOND edit-resend still has a matching ts (the backend
    // re-appends the edited message, which would otherwise get a new server ts the
    // FE doesn't know). Falls back to the index when the original turn has no ts.
    const newTs = new Date().toISOString()
    // A rewind retains the discarded tail on the edited message and resets the provider so
    // context rebuilds from the truncated transcript; the snapshot the page adopts carries the
    // divider chip + read-only tail disclosure. An EARLIER turn is always a rewind — decided
    // here for both callers (the inline editor and Rewind to here), and enforced by the server
    // too, because a plain resend of a middle turn used to delete every later exchange with no
    // trail. Only the latest turn's plain edit replaces just its own reply.
    const asRewind = rewind || editReplacesLaterTurns(turns, turnIndex)
    // Named for what the user pressed: Rewind to here, or the editor's Resend.
    const what = rewind ? 'rewind to this message' : 'resend your edited message'
    // Opened from the inline editor, the failure is said there, where the text still is. Rewind
    // to here has no editor open, so it is said the way every other failed action is.
    const fromEditor = editingTurn === turnIndex
    setEditFailure(null)
    const landed = await replaceTurns(
      (s) => api.editResend(s, t, turn?.ts, turnIndex, newTs, asRewind),
      (prev) => [...prev.slice(0, turnIndex), userTurn(t, newTs)],
      (e) => { if (fromEditor) setEditFailure(failureSentence(what, e)); else reportActionFailure(what)(e) },
    )
    if (landed) setEditingTurn(null)
  }

  // Rewind to an earlier user turn: confirm (it discards the later answers into
  // history), then edit-resend with the same text and rewind=true. The inline
  // editor stays available for changing the text first (Edit & resend); Rewind is
  // the one-click "answer this again, keep the tail" affordance. The later messages
  // are NOT re-sent — only this one is — so the confirmation says replaced, not replayed.
  async function rewindTo(turnIndex: number) {
    const turn = turns[turnIndex]
    if (!turn || turn.role !== 'user') return
    if (!(await confirm({
      title: 'Rewind to this message?',
      body: `Everything below this message is replaced by a fresh reply to it. ${replacedTurnsAreKept(memoryMode === 'persistent')}`,
      confirmLabel: 'Rewind',
    }))) return
    await editResend(turnIndex, turnText(turn), true)
  }

  // Restore a retained rewind tail as a NEW fork (restore = fork, never swap).
  async function forkRewound(turnIndex: number, snapshotIndex?: number) {
    const s = sessionRef.current
    if (!s) return
    try {
      const r = await api.forkRewound(s, turnIndex, snapshotIndex)
      if (r?.key) navigate(`chat/${r.key}`)
    } catch (e) {
      notice.showError(`Couldn’t restore that history: ${(e as Error).message}`)
    }
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
  // The voice settings follow Settings while this chat is open (`chat/voiceConfig`). Read through
  // a ref where a socket frame decides: the frame handler is created once.
  const { voiceCfg, speakReplies } = useVoiceConfig()
  const speakRepliesRef = useRef(speakReplies)
  speakRepliesRef.current = speakReplies
  const [speakingTurn, setSpeakingTurn] = useState<number | null>(null)
  const speakGenRef = useRef(0)
  // The reading this tab asked for, by the name it gave it, and whether "Speak replies aloud"
  // started it. The audio streams to every tab with this chat open; only the one that asked plays
  // it, so a chat open in two tabs (or on a second device) is read out once.
  const readingRef = useRef<{ request: string; auto: boolean } | null>(null)

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
    readingRef.current = null  // and the frames still on their way
    for (const src of audioSourcesRef.current) { try { src.stop() } catch { /* already ended */ } }
    audioSourcesRef.current = []
    audioPlayHeadRef.current = 0
    setSpeakingTurn(null)
  }

  function speak(text: string, turnIndex: number, auto = false) {
    // Toggle: clicking Speak on the turn that's already playing stops it.
    if (speakingTurn === turnIndex) { stopSpeak(); return }
    stopSpeak()  // stop any other turn first — only one plays at a time
    const s = sessionRef.current
    // Prime audio within the click's user-activation so later chunks can play.
    const ctx = getAudioCtx()
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {})
    speakGenRef.current++
    const request = `read-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
    readingRef.current = { request, auto }
    setSpeakingTurn(turnIndex)
    // Surface TTS failures instead of silently doing nothing after the Speak button's brief
    // spinner. The two refusals are the server's own sentences, and each names its fix: no
    // text-to-speech model set up (`tts_unbound`), or text-to-speech switched off (`tts_disabled`).
    // A Speak that works takes such a line down: once the reply is playing, "switched off" is false.
    return api.voiceSynthesize(text, s ?? '', request).then(() => notice.clear('speak'), (e: Error) => {
      if (readingRef.current?.request === request) readingRef.current = null
      setSpeakingTurn((cur) => (cur === turnIndex ? null : cur))
      const refused = hasApiCode(e, 'tts_unbound') || hasApiCode(e, 'tts_disabled')
      notice.showError(refused ? e.message : `Couldn’t play audio: ${e.message}`, 'speak')
    })
  }

  // "Speak replies aloud": a reply to a message this tab sent is spoken once it has rendered,
  // through the same path as its Speak button, if the setting is on when the reply finishes.
  // `replyFinished` asks for it from a socket handler, where the finished text has not rendered
  // yet; the effect runs after it has.
  const [spokenReplyDue, setSpokenReplyDue] = useState(0)
  const lastSpokenReply = useRef<number | null>(null)
  function replyFinished(sid: string | null, { last }: { last: boolean }) {
    if (!sid) return
    const owed = repliesToSpeak.get(sid) ?? 0
    if (last) repliesToSpeak.delete(sid)
    else if (owed > 1) repliesToSpeak.set(sid, owed - 1)
    else repliesToSpeak.delete(sid)
    if (owed > 0 && speakRepliesRef.current) setSpokenReplyDue((n) => n + 1)
  }
  useEffect(() => {
    if (!spokenReplyDue) return
    const i = turns.map((t) => t.role).lastIndexOf('assistant')
    const text = i >= 0 ? turnText(turns[i]) : ''
    if (!text || lastSpokenReply.current === i) return
    lastSpokenReply.current = i
    void speak(text, i, true)
    // Keyed on the request alone: `turns` changing afterwards must not read a reply twice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spokenReplyDue])
  // Counted whatever the setting says now, which may change before the reply finishes. Priming
  // inside the send gesture lets the reply's audio start when it arrives, seconds later.
  function oweSpokenReply(sid: string) {
    repliesToSpeak.set(sid, (repliesToSpeak.get(sid) ?? 0) + 1)
    if (!speakReplies) return
    const ctx = getAudioCtx()
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {})
  }
  // Switched off while a reply is being read out on its own: it stops at once. A Speak you
  // pressed is yours, and keeps playing.
  useEffect(() => {
    if (!speakReplies && readingRef.current?.auto) stopSpeak()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [speakReplies])
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

  // Resolve who said the selected passage: walk up from the selection node to the
  // enclosing turn's DOM node (turnNodes), then read that turn's role. Attribute the
  // agent side to its name when one is bound, else the neutral "Assistant".
  function attributionForNode(node: Node | null): string | undefined {
    if (!node) return undefined
    for (const [coord, el] of turnNodes.current.entries()) {
      if (el.contains(node)) {
        // The registry is keyed by the map's coordinate, so the owning turn is the one whose
        // coordinate matches — not `turns[coord]`, which is the array lookup the key change
        // above made wrong.
        const turn = turns.find((t, i) => markCoordOf(t, i) === coord)
        if (!turn) return undefined
        return turn.role === 'user' ? 'You' : (selection.agent || 'Assistant')
      }
    }
    return undefined
  }

  // activity panel: the Files / Links tabs, derived from the transcript. No navigation —
  // the Session Map's rail and drawer are the session's only jump surface.
  const activity = useMemo(() => deriveActivity(turns), [turns])
  // The tool results that show a run's live card: the first to name each run, however often the
  // agent read it after (`liveWorkflowCards`). Every later read keeps its plain tool card.
  const liveCards = useMemo(() => liveWorkflowCards(turns), [turns])
  // The Session Map's entries — one per USER message, each owning the turns of its exchange.
  // Derived ONCE, here, and handed to both forms, so the rail and the coarse-pointer drawer index
  // the identical array (a "Message 3 of 7" that means a different 7 in each form is two maps).
  const sessionEntries = useMemo(() => sessionMapEntries(turns), [turns])
  /** Scroll the turn at a map coordinate into view — the ONE scroll implementation behind the
   *  rail's tick and the drawer's row, which since SSM-13 are the only two jump surfaces. */
  function jumpToTurn(coord: number) {
    const node = turnNodes.current.get(coord)
    node?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
  /** A drawer row's coordinate, parked between the tap and the drawer finishing its exit.
   *
   *  🔴 THE ORDER IS THE WHOLE FIX, AND JUMPING FIRST WAS A REAL DEFECT. The mobile drawer is a
   *  docked `SidePanel` flex SIBLING of this column, so while it is open the transcript is
   *  squeezed to the 32px `EDGE_PEEK` sliver (measured at a 390px viewport: panel 358, column
   *  32). `scrollIntoView` resolves a target offset against the layout it is called in — so
   *  `jumpToTurn(coord); setMapOpen(false)` computed an offset for a 32px-wide transcript and
   *  then immediately re-flowed it to 390px, where the retained `scrollTop` means something
   *  else entirely. Tapping the oldest row left turn 1 at viewport ratio 0, i.e. the
   *  tap navigated nowhere, which is the one thing the row exists to do.
   *
   *  So the tap now only RECORDS where it wants to go, and the scroll runs from the
   *  `AnimatePresence` `onExitComplete` below — the first moment the panel is out of the DOM and
   *  the transcript's width is final. `onExitComplete` rather than a `requestAnimationFrame` or a
   *  timeout because the exit is a spring on `width` (`ui/SidePanel`): its duration is not a
   *  number this file can know, and under `prefers-reduced-motion` it is zero. Framer's callback
   *  is the only signal that means "the layout has stopped moving" in both cases.
   */
  const pendingMapJump = useRef<number | null>(null)
  /** Run the parked jump, if a row tap (and not the panel's own close button) armed one. */
  function runPendingMapJump() {
    const coord = pendingMapJump.current
    pendingMapJump.current = null
    if (coord !== null) jumpToTurn(coord)
  }

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

  /** Natural voice: set the per-conversation scope, then adopt the backend's
   *  RE-RESOLVED answer rather than assuming the click won. The resolution order lives
   *  once, server-side (`natural_voice.NATURAL_VOICE_PRECEDENCE`), so the pill can only
   *  stay honest by displaying what came back — a locally-computed label would be a
   *  second copy of that order, free to drift from the one the turn uses.
   *
   *  Before the session exists there is nothing to PATCH, so the choice is held locally
   *  with `source: ''` (the pill then shows the choice, not an effect) and persisted by
   *  `ensureSession` at create — the same shape as a pre-start reasoning-effort pick. */
  async function selectNaturalVoice(choice: '' | 'on' | 'off') {
    const s = sessionRef.current
    if (!s) { setNaturalVoice((v) => ({ ...v, choice, source: '' })); return }
    const r = await persistSelection('this natural-voice setting', api.setSessionNaturalVoice(s, choice))
    if (r) setNaturalVoice({
      choice: (r.natural_voice || '') as '' | 'on' | 'off',
      effective: !!r.natural_voice_effective,
      source: r.natural_voice_source || '',
      agentDefault: !!r.natural_voice_agent_default,
    })
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

  /** Apply a saved starter to the composer (S3 T3.2).
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

  // TM8: the model proposed a switch out of a restricted mode and the user clicked
  // "Switch to Agent & run it". Flip the session to Agent (UI toggle + backend) and
  // resume the work — the click IS the consent that makes the escalation safe (no
  // silent self-escalation out of a read-only posture). The backend flip is awaited
  // before resending so the continuation turn runs under Agent's gate + framing.
  async function switchToAgentAndRun(continuation: string) {
    setSelection((sel) => ({ ...sel, taskMode: 'agent' }))
    const s = sessionRef.current
    // 🔑 THIS ONE DOES NOT JUST REPORT — IT STOPS. The comment above states the invariant: the flip is
    // awaited "so the continuation turn runs under Agent's gate + framing". It used to swallow the
    // rejection and send anyway, which breaks exactly that invariant — the turn would run under the
    // posture the user was escalating OUT of, while the composer showed Agent. For a consent-gated
    // escalation, proceeding on a failed flip is the one outcome the click did not authorise, so report
    // and return. The composer keeps showing Agent, which is now the honest state of the user's intent
    // rather than a claim about the session; the next send re-attempts the flip through `applySelection`.
    if (s) {
      try { await api.setTaskMode('agent', s) }
      catch (e) {
        notify(`Couldn't switch this session to Agent: ${String((e as Error)?.message || e)}`, 'error')
        return
      }
    }
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
    const { precheck } = await import('../lib/chunkedUpload')
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
      const { isAbortError } = await import('../lib/chunkedUpload')
      if (!isAbortError(e)) setAttachError((e as Error).message)
      return { paths: [] as string[] }
    })
    // In the same render as the chips below, so the hold never lifts before the file is attached.
    done()
    const paths = (r as { paths?: string[] }).paths ?? []
    // Thread uploaded paths into the next send's meta.files (B0) + show them as
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

  const stage = (
    // `data-tour="chat"` — the product tour's chat stop points at the composer stage
    // (ONBOARDING-UX T5.1). On the chat route this wrapper is the composer.
    <div data-tour="chat" className="w-full" style={{ maxWidth: 'var(--content-width)' }}>
      {/* Memory-mode notice: incognito/temporary sessions look identical to a normal one
          otherwise, so say what this mode does before the user types. The words are
          `memoryModeCopy`'s, which holds each clause to what the backend does; a privacy
          promise that over-states itself is worse than a narrower true one. */}
      {memoryMode !== 'persistent' && (
        <div className="mb-2 flex items-center gap-1.5 text-[0.75rem] text-on-surface-low">
          {memoryMode === 'incognito' ? <EyeOff size={13} className="shrink-0" /> : <Clock size={13} className="shrink-0" />}
          <span>{MEMORY_MODE_NOTICE[memoryMode]}</span>
        </div>
      )}
      {/* An app's conversation: what you send runs under the APP's grant, not your approval
          switches — said before you type, like the memory notice above. */}
      {startedBy && <AppPermissionNotice name={startedBy.name} />}
      {/* The composer's notice: what failed (voice input, screen sharing, a switched answer, a
          branch…), which stays until dismissed or the next send, or what just happened. */}
      <ComposerNoticeLine notice={notice.notice} onDismiss={notice.clear} />
      {toast && (
        <div className="mb-2 flex items-center gap-1.5 text-[0.75rem] text-on-surface-var">
          <Check size={13} className="shrink-0 text-ok" /><span>{toast}</span>
        </div>
      )}
      {/* Upload rejection (oversize / failure) — persistent + dismissible, since the
          user must act on it (choose a smaller file), on its own line beside the notice. */}
      {attachError && (
        <div role="alert" className="mb-2 flex items-start gap-1.5 rounded-md px-2.5 py-1.5 text-[0.75rem]"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}>
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1 break-words">{attachError}</span>
          <IconButton icon={X} label="Dismiss" onClick={() => setAttachError(null)} size={20} iconSize={12}
            className="shrink-0 opacity-70 hover:opacity-100" />
        </div>
      )}
      {/* Upload progress: a row per file still uploading, small and large alike. While any
          is up, sending is held (`uploadHold`). */}
      {uploads.length > 0 && (
        <div className="mb-2 flex flex-col gap-1 rounded-lg bg-surface-container/60 px-3 py-2">
          {uploads.map((u, i) => (
            <div key={`${u.batch}:${i}`} className="flex items-center gap-2.5 text-[0.75rem] text-on-surface-var">
              <Loader2 size={13} className="shrink-0 animate-spin text-primary" />
              <span className="max-w-[40%] shrink-0 truncate" title={u.name}>{u.name}</span>
              {/* The bar takes the row's slack (prominent), pct + cancel stay compact —
                  so it reads as one aligned progress control, not scattered bits. The
                  hand-rolled track this replaced had no role at all, so with several
                  uploads queued a screen reader heard a spinner and a filename and
                  nothing about how far along any of them was. */}
              <Meter size="thin" className="min-w-0 flex-1" label={`Uploading ${u.name}`} pct={u.pct} />
              <span className="shrink-0 tabular-nums text-on-surface-low">{u.pct}%</span>
              {!u.finishing && <IconButton icon={X} label="Cancel upload" onClick={() => uploadAborts.current.get(u.batch)?.abort()} size={20} iconSize={13}
                tone="danger" className="shrink-0" />}
            </div>
          ))}
        </div>
      )}
      <AttachmentChips paths={[...mentionedFiles, ...attachedPaths]} images={attachedPaths.filter(isImagePath)}
        session={sessionId ?? ''} agent={selection.agent} model={selection.model === 'Auto' ? '' : selection.model}
        runtime={acpFor(selection.agent)?.providerId ?? ''}
        onRemove={(p) => { setMentionedFiles((prev) => prev.filter((x) => x !== p)); setAttachedPaths((prev) => prev.filter((x) => x !== p)) }}
        onOpen={setOpenFile} />
      <KnowledgeChips items={mentionedKnowledge}
        onRemove={(id) => setMentionedKnowledge((prev) => prev.filter((k) => k.id !== id))} />
      <PasteCards blocks={livePasteBlocks} onRemove={removePaste} />
      {/* revert-optimize: the optimize rewrite replaces the draft in place, so
          offer a one-click undo back to what the user originally typed. */}
      {preOptimize !== null && (
        <Button variant="secondary" size="xs" onClick={revertOptimize}
          className="mb-2 gap-1.5 px-2.5 text-[0.75rem] text-on-surface-var">
          <Repeat size={12} className="shrink-0" /> Optimized — revert to original
        </Button>
      )}
      {/* Steered messages — already injected into the answer being written, so
          unlike a queued item there is nothing to cancel or reorder. Rendered so a
          steer is visible: the backend broadcasts a "Steering: …" activity_event, but
          activity_event drops `kind === 'status'` as thinking-indicator noise, which
          left a successful steer with no UI at all. */}
      {steered.length > 0 && (
        <div className="mb-2 flex flex-col gap-1" aria-live="polite">
          {steered.map((s, i) => (
            <div key={`${i}-${s.slice(0, 24)}`}
              className="flex items-start gap-1.5 text-[0.75rem] text-on-surface-var">
              <CornerDownLeft size={12} className="mt-0.5 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1 truncate">
                Steered into this answer: {s}
              </span>
            </div>
          ))}
        </div>
      )}
      {/* Queued messages (typed mid-stream) — the backend sends them one-by-one as
          each turn finishes; each can be cancelled while still pending. */}
      <QueueStack items={queued} canInterrupt={streaming}
        onCancel={(id) => { setQueued((prev) => prev.filter((q) => q.id !== id)); const s = sessionRef.current; if (s) api.cancelQueued(s, id).catch(reportActionFailure('cancel that queued message')) }}
        onEdit={(id, content) => {
          // Honest "edit": there's no queue-edit endpoint, so cancel the pending item
          // and drop its text back in the composer for the user to revise + resend
          // (avoids a fake in-place edit that would silently re-queue at the back).
          setQueued((prev) => prev.filter((q) => q.id !== id)); const s = sessionRef.current; if (s) api.cancelQueued(s, id).catch(reportActionFailure('cancel that queued message'))
          setInput((cur) => (cur.trim() ? cur : content))
        }}
        onInterrupt={(id) => {
          // Interrupt-now: soft-stop the running turn and run THIS queued message
          // next (the backend promotes it + the finally-block drain picks it up).
          // The queue_promoted WS echo reorders the strip on every client.
          const s = sessionRef.current; if (s) api.interruptChat(s, id).catch(reportActionFailure('interrupt this turn'))
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
        {artifactPickerOpen && (
          <ArtifactContextPicker
            attached={mentionedArtifacts}
            onPick={(a) => setMentionedArtifacts((prev) => (prev.some((x) => x.slug === a.slug) ? prev : [...prev, a]))}
            onRemove={(slug) => setMentionedArtifacts((prev) => prev.filter((a) => a.slug !== slug))}
            onClose={() => setArtifactPickerOpen(false)} />
        )}
        {/* Crop step for a browser-captured frame. The capture is already stopped by
            the time this renders, so cancelling leaves nothing behind — neither an
            attachment nor a live track. */}
        {snip && (
          <SnipOverlay frame={snip.url} width={snip.width} height={snip.height}
            onCancel={() => setSnip(null)} onConfirm={(rect) => { void attachSnip(rect) }} />
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
        <AnimatePresence>
          {routingSuggestion && (
            <div className="mb-1 flex justify-center">
              <RoutingChip suggestion={routingSuggestion} defaultAgent={selection.agent || ''}
                onRoute={() => { setSelection((s) => ({ ...s, agent: routingSuggestion.agent })); setRoutingSuggestion(null) }}
                onDismiss={() => setRoutingSuggestion(null)} />
            </div>
          )}
        </AnimatePresence>
        {/* Suggested organization (SM T2.1). Keyed on the message count so it re-asks once a
            turn lands and the auto-titler has given the chat a title to reason from — an
            untitled brand-new chat has no signal. Proposal only; nothing applies until
            "File it" is clicked. */}
        {started && sessionRef.current && (
          <div className="mb-1 flex justify-center">
            <OrganizeChip sessionKey={sessionRef.current} refreshKey={turns.length} />
          </div>
        )}
        {/* Plan review gate. Mounted whenever there's a session and renders
            nothing until the "Plan this first" affordance has opened a walkthrough —
            keyed on the turn count so the draft the plan-mode turn just produced shows
            up without a reload. Approve/cancel hand back the restored task mode, so the
            composer's pill can't drift from the posture the backend gate enforces. */}
        {sessionRef.current && (
          <ChatPlanGate session={sessionRef.current} refreshKey={turns.length}
            onTaskMode={(m) => setSelection((sel) => ({ ...sel, taskMode: m }))} />
        )}
        {/* The honest label on the bundled zero-config floor model, immediately above
            the composer so it is read where the answers arrive. Mounted unconditionally and
            renders nothing unless chat really is resolving to a floor provider — which is
            false on every home that has bound anything. */}
        <BundledFloorNotice />
        <ComposerStage ref={composerRef} value={input} onChange={(v) => { setInput(v); if (preOptimize !== null) setPreOptimize(null); if (followups.length && v.trim().length >= 3) setFollowups([]) }} onSend={() => send()}
          streaming={streaming} onStop={stop} controls={CHAT_CONTROLS} data={data}
          selection={selection} onSelect={applySelection} onAttach={attach} onFocusChange={setComposerFocused}
          naturalVoice={{ ...naturalVoice, onSelect: (c) => void selectNaturalVoice(c) }}
          onOpenPrompts={() => setPromptPaletteOpen(true)}
          plusMenuExtra={(close) => (
            <>
              <MenuRow icon={<BookText size={16} />} label="Add knowledge" hint="Search the library → attach to the prompt" onClick={() => { close(); setKnowledgePickerOpen(true) }} />
              <MenuRow icon={<Boxes size={16} />} label="Reference an artifact" hint="Ground the reply in an artifact's current version" onClick={() => { close(); setArtifactPickerOpen(true) }} />
              {/* Feature-detected out entirely where neither provider exists (iOS
                  Safari has no getDisplayMedia and no gateway binary to shell out to) —
                  a control that can only fail is worse than no control. */}
              {captureProvider !== 'none' && <MenuRow icon={<Camera size={16} />} label="Capture screen area" hint="Snip a region → attach" onClick={() => { close(); void captureScreenArea() }} />}
              {/* Pin the shared frame — the ONE deliberate promotion from
                  ephemeral to file. Offered only while sharing, because the frame it
                  pins is the one the browser still holds: nothing older can be pinned,
                  since retaining past frames client-side is exactly the retention this
                  feature exists not to do. Suppressed in a temporary/incognito chat by
                  the server (writes are suppressed there), which is why the failure
                  path surfaces the server's reason rather than a guess. */}
              {screenShare.sharing && sessionRef.current && (
                <MenuRow icon={<Pin size={16} />} label="Pin shared frame" hint="Save the current screen frame as an ordinary attachment"
                  onClick={() => { close(); void pinScreenFrame() }} />
              )}
              {/* Plan mode — an explicit, manual entry. Nothing decides FOR the
                  user that a message needs planning: a quick task just sends. Offered on
                  a started chat because the plan is drafted by the next turn in it. */}
              {started && sessionRef.current && (
                <MenuRow icon={<ListChecks size={16} />} label="Plan this first"
                  hint="Draft a plan for review — nothing runs until you approve it"
                  onClick={() => { close(); void activatePlanMode() }} />
              )}
              {started && sessionRef.current && <AutoNudgeMenuItem session={sessionRef.current!} onOpen={close} />}
            </>
          )}
          onMentionFile={onMentionFile} onMentionKnowledge={onMentionKnowledge} onLargePaste={onLargePaste}
          openModelSignal={openModelSignal} openAgentSignal={openAgentSignal} openReasoningSignal={openReasoningSignal}
          onOptimize={optimize} optimizing={optimizing} history={promptHistory}
          onTranscribe={transcribe} onMicError={(msg) => notice.showError(msg, 'voice-input')} canQueue canSteer={takesSteers} sendHeldReason={uploadHold} contextPct={contextPct} contextWindow={contextWindow}
          handsFree={{ confirmationPhrases: voiceCfg.confirmation_phrases, exitPhrases: voiceCfg.exit_phrases, speaking: speakingTurn !== null, muteWhileSpeaking: voiceCfg.duplex_mute_enabled }}
          onHandsFreeSubmit={(t) => void send(t, { inputOrigin: 'voice' })}
          screenShare={{ available: screenShare.available, sharing: screenShare.sharing, disabledReason: screenShare.disabledReason, onToggle: screenShare.toggle }} />
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
        below={contextChips.length > 0 ? contextChips : undefined}
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
                session nav" outcome §A.8 exists to prevent. */}
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
          <DotGlow intensity={composerFocused ? 1.6 : 1} focused={composerFocused} composerRef={composerRef} />
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
              {/* ── SESSION MAP RAIL + TRANSCRIPT ────────────────────────────────────────────
                  🔑 THE RAIL IS A SIBLING OF THE SCROLLER, NOT A CHILD OF IT, and that IS the
                  "stays fixed while the transcript scrolls" requirement rather than a styling
                  choice: an element outside the scroll container has no scroll offset to
                  inherit, so no `sticky`, no scroll listener and no re-positioning code exists
                  to get wrong. `e2e/sessionMap.spec.ts` asserts BOTH halves in a real browser —
                  the rail's box does not move while the transcript scrolls, AND the scroll
                  container does not contain the rail — because a `sticky` rail inside the
                  scroller would pass the box check and still drift under an ancestor transform.
                  The gutter is the empty column §A.0 measured ("No left sidebar"), so the rail
                  costs the centred transcript nothing. */}
              <div className="relative flex min-h-0 flex-1">
              {mapOpen && !isMobile && (
                <SessionMapRail entries={sessionEntries} turnNodes={turnNodes.current}
                  scrollRef={scrollRef} onJumpTo={jumpToTurn} />
              )}
              {/* `data-transcript-scroll` names THIS element as the one `scrollRef` points at, so
                  the browser gate can assert scroll-fixity and the jump against the real scroll
                  container instead of guessing which ancestor scrolls. */}
              {/* The transcript FADES OUT over its own bottom padding (`py-2xl` below) instead of
                  stopping at a scroll clip. That edge sits in the halo's brightest band, right
                  above the docked composer, and the clip sliced whatever crossed it — a code
                  block or a bubble cut flat, above a strip of glow. At rest nothing is faded
                  (the newest turn ends above the padding); anything scrolled past dissolves
                  into the halo instead of ending in a line. */}
              <div ref={scrollRef} data-transcript-scroll className="relative min-w-0 flex-1 overflow-y-auto"
                style={{ maskImage: TRANSCRIPT_END_FADE, WebkitMaskImage: TRANSCRIPT_END_FADE }}>
                <AnimatePresence>
                  {/* `ui/FindBar` is surface-agnostic; chat supplies what a turn's searchable
                      text is (`findSegments`) and which node to scroll to. Both references are
                      stable, so a composer keystroke does not re-scan the transcript. */}
                  {findOpen && (
                    // key on the seed: a fresh `?find=` deep-link (even into an already-open
                    // bar) remounts it so it re-seeds + re-scrolls; an empty seed (⌘F) is a
                    // constant key, so the manual bar is never remounted out from under a typist.
                    <FindBar key={`find-${findSeed}`} items={turns} segmentsOf={findSegments} nodeOf={(t, i) => turnNodes.current.get(markCoordOf(t, i))}
                      scrollRef={scrollRef} label="Find in conversation" initialQuery={findSeed} onClose={() => setFindOpen(false)} />
                  )}
                </AnimatePresence>
                <SelectionQuote scrollRef={scrollRef} onQuote={quoteToComposer} attributionFor={attributionForNode} />
                <div className="mx-auto flex flex-col gap-2xl px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
                  {turns.map((turn, i) => {
                    const isLast = i === turns.length - 1
                    const turnTextOf = (t: ChatTurn) => t.segments.map((s) => (s.kind === 'text' ? s.text : '')).join('')
                    return (
                      // `data-transcript-turn` names a turn node by its side of the exchange, so the
                      // browser gate can put a REPLY on screen without its question and check the
                      // Session Map still lights that question — the registry itself is a ref.
                      <div key={i} className="relative" data-transcript-turn={turn.role}
                        ref={(el) => { const c = markCoordOf(turn, i); if (el) turnNodes.current.set(c, el); else turnNodes.current.delete(c) }}>
                        {turn.role === 'user' ? (
                          editingTurn === i ? (
                            <UserEditor initial={turnTextOf(turn)} onCancel={() => { setEditingTurn(null); setEditFailure(null) }}
                              replacesLater={editReplacesLaterTurns(turns, i)} canFork={memoryMode === 'persistent'}
                              failure={editFailure} onSubmit={(v) => editResend(i, v)} />
                          ) : (
                            <div className="group/msg">
                              <MessageUser fromComposer={isLast} onFileClick={setOpenFile} pastes={turn.pastes} optimized={turn.optimized} ranPrompt={turn.ranPrompt}
                                onExpand={() => { followTurnRef.current = false }}>{turnTextOf(turn)}</MessageUser>
                              {turn.files && turn.files.length > 0 && <TurnAttachments paths={turn.files} delivery={turn.imageDelivery} onOpenFile={setOpenFile} />}
                              {turn.rewound && turn.rewound.length > 0 && (
                                <RewindDivider snapshots={turn.rewound} canFork={memoryMode === 'persistent'} onFork={(si) => forkRewound(i, si)} />
                              )}
                              {!streaming && <UserActions text={turnTextOf(turn)} canFork={memoryMode === 'persistent'}
                                canRewind={!isLast} onRewind={() => rewindTo(i)} ts={stampOf(turn)}
                                onEdit={() => { setEditFailure(null); setEditingTurn(i) }} onFork={() => forkAt(i)} />}
                            </div>
                          )
                        ) : (
                          <MessageAssistant actions={!(isLast && streaming) && (
                            <AssistantActions text={turnText(turn)} isLast={isLast} canFork={memoryMode === 'persistent'}
                              variantCount={turn.variantCount} variantIdx={turn.variantIdx} ts={stampOf(turn)}
                              onCopy={() => {}} onRegenerate={regenerate} onFork={() => forkAt(i)}
                              onSwitchVariant={isLast ? switchVariant : undefined}
                              speaking={speakingTurn === i} onSpeak={() => speak(turnText(turn), i)} />
                          )}>
                            <AssistantSegments segments={turn.segments} liveCards={liveCards} isLast={isLast} messageTs={turn.ts} streaming={isLast && streaming} onApprove={approve} onAnswerQuestion={answerQuestion} onSwitchToAgent={switchToAgentAndRun} onOpenFile={setOpenFile} onSetupModel={() => navigate(MODELS_PATH)} onRetry={isLast && !streaming ? regenerate : undefined} chatSessionKey={sessionRef.current ?? undefined} citations={turn.citations} skillsUsed={turn.skillsUsed} cutOff={turn.cutOff} modelSubstitution={turn.modelSubstitution} />
                          </MessageAssistant>
                        )}
                        {/* Follow-up chips under the last assistant turn only,
                            once the reply has settled — click fills the composer to edit,
                            send-glyph sends. */}
                        {turn.role === 'assistant' && isLast && !streaming && followups.length > 0 && (
                          <FollowupChips items={followups} onPick={(t) => { setInput(t); focusComposerSoon() }} onSend={(t) => { setFollowups([]); void send(t) }} />
                        )}
                        {/* "Check this work" offer — user-clicked only. */}
                        {turn.role === 'assistant' && isLast && !streaming && checkWorkOffer && (
                          <CheckWorkChip label={checkWorkOffer.label}
                            onRun={() => { const p = checkWorkOffer.prompt; setCheckWorkOffer(null); void send(p) }} />
                        )}
                      </div>
                    )
                  })}
                  <AnimatePresence>
                    {streaming && showThinking && (
                      <StreamingIndicator statusText={statusText} activity={latestActivity} />
                    )}
                  </AnimatePresence>
                  {/* A step of this turn waiting for a busy local model: why, and what next. */}
                  {streaming && sessionId && <ModelWaits session={sessionId} className="py-s" />}
                  <div ref={endRef} />
                  {/* visually-hidden polite live region — narrates the turn lifecycle to screen
                      readers (the glow/Thinking cue is visual-only): that a turn started, its
                      status lines, and how it ended, as the gateway reported it. */}
                  <div aria-live="polite" className="sr-only">{srAnnounce}</div>
                  {/* Second polite region, mounted from first render: the follow-up
                      chips arrive from a WS event AFTER the turn's ending is said — a
                      visual-only change until now. Kept separate from srAnnounce so the turn
                      narration and the chips arrival do not overwrite one another. */}
                  <div role="status" aria-live="polite" className="sr-only">
                    {followupAnnouncement(streaming ? 0 : followups.length)}
                  </div>
                </div>
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

/** "Add knowledge to prompt" — a search picker over the library that shows each
 *  result's token cost against a budget, so the user can attach relevant context
 *  without blowing the window. Selecting toggles the item into mentionedKnowledge
 *  (the same pipeline as an @-mention); the backend inlines it at send. */
/** Pick artifacts to ground the next turn in. Unlike the knowledge picker there is no
 *  token budget meter: an artifact is one whole document the user names deliberately,
 *  not a set of search fragments competing for a context allowance. The list is the
 *  library itself, filtered client-side — an artifact library is small enough that a
 *  server round-trip per keystroke would be the slower option. */
function ArtifactContextPicker({ attached, onPick, onRemove, onClose }: {
  attached: { slug: string; name: string }[]
  onPick: (a: { slug: string; name: string }) => void
  onRemove: (slug: string) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  // The swallow made a failed read indistinguishable from an empty library, and this picker's
  // empty state TEACHES ("Ask in chat for a widget…") — so a 500 told a user with artifacts to go
  // make their first one. Same shape as #1162's chat history, one surface down.
  const { data, loading, error: artifactsError } = useQuery('artifacts:chat-picker', () => api.artifacts())
  const all = data ?? []
  const attachedSlugs = new Set(attached.map((a) => a.slug))
  const n = q.trim().toLowerCase()
  const shown = (n ? all.filter((a) => `${a.name} ${a.slug} ${a.kind}`.toLowerCase().includes(n)) : all).slice(0, 40)
  return (
    <Modal title="Reference an artifact" icon={<Boxes size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-m" style={{ minWidth: 420 }}>
        <SearchField value={q} onChange={setQ} autoFocus placeholder="Search your artifacts…"
          ariaLabel="Search your artifacts"
          trailingSlot={loading ? <Loader2 size={15} className="animate-spin text-on-surface-low" /> : undefined} />
        {/* `shown` is capped at 40, and the count follows the CAP rather than the match total —
            the announcement describes what is on screen, which is the only number a user can act
            on. (That the cap itself is silent is a separate, visible-copy question.) */}
        <ResultAnnouncement count={shown.length} noun="artifacts" active={!!n} />
        {attached.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {attached.map((a) => (
              <Button key={a.slug} variant="ghost" size="xs" onClick={() => onRemove(a.slug)}
                title="Remove from this prompt">
                <Boxes size={11} /> {a.name} <X size={11} />
              </Button>
            ))}
          </div>
        )}
        {data === undefined && artifactsError ? (
          // The error branch comes FIRST: `data === undefined` is true for loading, failure AND an
          // empty library, so a failure test placed after them is unreachable. `FieldError`
          // announces (role=alert), which a teaching empty state deliberately does not.
          <FieldError>Couldn't load your artifacts — {(artifactsError as Error)?.message || 'the server did not respond'}</FieldError>
        ) : all.length === 0 && !loading ? (
          <p className="text-on-surface-low text-[0.8125rem]">
            No artifacts yet. Ask in chat for a widget or a document and it lands here.
          </p>
        ) : (
          <div className="flex max-h-80 flex-col gap-1 overflow-y-auto">
            {shown.map((a) => {
              const on = attachedSlugs.has(a.slug)
              return (
                <MenuRow key={a.slug} icon={<Boxes size={14} />} label={a.name}
                  hint={`${a.kind} · v${a.version} · ${a.slug}`} selected={on}
                  onClick={() => (on ? onRemove(a.slug) : onPick({ slug: a.slug, name: a.name }))} />
              )
            })}
            {n && shown.length === 0 && (
              <p className="px-2 py-2 text-on-surface-low text-[0.8125rem]">No artifact matches that.</p>
            )}
          </div>
        )}
      </div>
    </Modal>
  )
}

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
  // A query's synchronous consequences happen in the handler that changes it, batched into the
  // keystroke's own render; the effect below only runs the debounced search. `setLoading(true)`
  // used to sit in that effect, where it scheduled a render from inside every keystroke's commit —
  // typed fast, ~50 keys threw React's #185 (the mechanism: `ui/composer/MarkdownInput`). Clearing
  // here also clears `loading`, which the effect never did: emptying the box while a search was
  // pending left the spinner turning where "Type to search" belongs.
  const search = (v: string) => {
    setQ(v)
    if (v.trim()) setLoading(true)
    else { setRes(null); setLoading(false) }
  }
  useEffect(() => {
    const query = q.trim()
    if (!query) return
    let alive = true
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
        <SearchField value={q} onChange={search} autoFocus placeholder="Search your knowledge library…"
          ariaLabel="Search your knowledge library"
          trailingSlot={loading ? <Loader2 size={15} className="animate-spin text-on-surface-low" /> : undefined} />
        {/* Remote, and debounced 250ms: `active` waits for `loading` to clear so the count is the
            one for the query the user has actually finished typing, and for `res` to exist so an
            unrun search is not reported as "no matches". */}
        <ResultAnnouncement count={res?.results.length ?? 0} noun="knowledge items"
          active={!!q.trim() && !loading && res !== null} />
        {attached.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between text-[0.75rem] text-on-surface-low">
              <span>{attached.length} attached{attachedTokens ? ` · ~${attachedTokens} tokens` : ''}</span>
              {attachedTokens > 0 && <span className="tabular-nums">{pct}% of {MAX}</span>}
            </div>
            {attachedTokens > 0 && (
              <Meter size="thin" label="Prompt budget used by attached knowledge" pct={pct}
                tone={pct > 90 ? 'var(--color-warn)' : 'var(--color-primary)'} />
            )}
          </div>
        )}
        {/* 🔴 Attached-or-not was announced by COLOUR alone — a tinted row, a coral outline and a
            check glyph, none of which reaches the accessibility tree. `aria-pressed` is the state;
            the group carries the dimension so a row announces what it is being attached TO. No tab
            stop is needed on the scroller because every row in it is a button. */}
        <div role="group" aria-label="Knowledge to attach" className="max-h-[46vh] overflow-y-auto flex flex-col gap-1.5">
          {loading && !res ? <div className="grid place-items-center py-6 text-on-surface-low"><Loader2 size={16} className="animate-spin" /></div>
            : !q.trim() ? <p className="py-6 text-center text-on-surface-low text-[0.8125rem]">Type to search notes, gists, bookmarks, docs…</p>
            : (res?.results.length ?? 0) === 0 ? <p className="py-6 text-center text-on-surface-low text-[0.8125rem]">No matches for “{q}”.</p>
            : res!.results.map((r) => {
              const on = attachedIds.has(r.id)
              return (
                <button key={r.id} type="button" aria-pressed={on}
                  onClick={() => on ? onRemove(r.id) : onPick({ id: r.id, name: r.title })}
                  className="flex items-start gap-2 rounded-lg px-3 py-2 text-left transition-colors"
                  style={on ? { background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)', outline: '1px solid color-mix(in srgb, var(--color-primary) 40%, transparent)' } : { background: 'var(--color-surface-container)' }}>
                  <span className="mt-0.5 shrink-0 grid size-4 place-items-center rounded border" style={on ? { background: 'var(--color-primary)', borderColor: 'var(--color-primary)', color: 'var(--color-on-primary)' } : { borderColor: 'var(--color-outline-variant)' }}>{on && <Check size={11} />}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>{r.title}</span>
                      <span className="ml-auto shrink-0 tabular-nums text-on-surface-low text-[0.75rem]">~{r.tokens} tok</span>
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
          <IconButton icon={X} label="Remove knowledge reference" onClick={() => onRemove(k.id)} size={20} iconSize={13}
            tone="danger" className="shrink-0" />
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
function QueueStack({ items, onCancel, onEdit, onInterrupt, canInterrupt = false }: {
  items: { id: string; content: string }[]
  onCancel: (id: string) => void
  onEdit: (id: string, content: string) => void
  onInterrupt?: (id: string) => void
  canInterrupt?: boolean  // a turn is running → "Interrupt now" can promote + soft-stop
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
      className={`flex items-center gap-1.5 px-1 text-[0.75rem] uppercase tracking-wide text-on-surface-low ${items.length > 1 ? 'hover:text-on-surface-var' : 'cursor-default'}`}>
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
          {canInterrupt && onInterrupt && (
            <IconButton icon={PlayCircle} label="Interrupt now — stop the current turn and run this next" onClick={() => onInterrupt(q.id)} size={20} iconSize={13}
              className="opacity-0 transition-opacity hover:text-primary group-hover/q:opacity-100 focus-within:opacity-100" />
          )}
          <IconButton icon={Pencil} label="Edit queued message" onClick={() => onEdit(q.id, q.content)} size={20} iconSize={12}
            className="opacity-0 transition-opacity hover:text-primary group-hover/q:opacity-100 focus-within:opacity-100" />
          <IconButton icon={X} label="Cancel queued message" onClick={() => onCancel(q.id)} size={20} iconSize={13}
            tone="danger" />
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
      role="status" aria-busy="true" >
        <LoadingStatus what="conversation" />
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
            <IconButton icon={X} label={`Remove paste #${b.seq}`} onClick={() => onRemove(b.seq)} size={20} iconSize={13}
              tone="danger" className="shrink-0" />
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

/** Rewind divider — shown under a user turn that was
 *  edited-and-replayed. States "N messages kept in history" and discloses the
 *  retained tail read-only. Restoring a tail = forking it into a new session
 *  (restore = fork, never an in-place timeline swap). Shows the most recent
 *  snapshot; earlier ones (repeated rewinds) are reachable via the count. */
function RewindDivider({ snapshots, canFork, onFork }: {
  snapshots: NonNullable<ChatTurn['rewound']>
  canFork: boolean
  onFork: (snapshotIndex?: number) => void
}) {
  const [open, setOpen] = useState(false)
  const latest = snapshots[snapshots.length - 1]
  // The retained tail begins with the edited turn's OLD content; count the
  // messages AFTER it as "kept" (what the user actually rewound past).
  const kept = Math.max(0, (latest?.messages?.length ?? 0) - 1)
  return (
    <div className="mt-2 flex flex-col items-end gap-1.5">
      <div className="flex items-center gap-2 text-on-surface-low text-[0.75rem]">
        <Rewind size={12} className="shrink-0" />
        <span>Rewound from here · {kept} message{kept === 1 ? '' : 's'} kept in history</span>
        <QuietButton onClick={() => setOpen((o) => !o)} ariaExpanded={open} className="h-6">
          {open ? 'Hide' : 'View'} <ChevronDown size={11} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
        </QuietButton>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            transition={spring.spatialFast}
            className="w-full max-w-[452px] overflow-hidden rounded-xl border border-outline-variant/50 bg-surface-container/60">
            <div className="flex items-center justify-between border-b border-outline-variant/40 px-3 py-2">
              <span className="text-on-surface-low text-[0.6875rem] uppercase tracking-wide">Retained history (read-only)</span>
              {canFork && (
                <Button size="sm" variant="ghost" onClick={() => onFork()} className="h-6 px-2 text-[0.75rem]">
                  <GitBranch size={12} /> Restore as fork
                </Button>
              )}
            </div>
            <div className="flex flex-col gap-2 px-3 py-2.5">
              {(latest?.messages ?? []).map((m, mi) => (
                <div key={mi} className={`text-[0.8125rem] leading-relaxed ${m.role === 'user' ? 'text-on-surface-var' : 'text-on-surface-low'}`}>
                  <span className="mr-1.5 text-on-surface-low text-[0.6875rem] uppercase tracking-wide">{m.role === 'user' ? 'You' : 'Assistant'}</span>
                  <span className="whitespace-pre-wrap">{m.content.length > 400 ? m.content.slice(0, 400) + '…' : m.content}</span>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** Inline editor for a user turn (Edit & resend). Replaces the bubble with a
 *  right-aligned textarea + Cancel/Resend; ⌘↵ submits, Esc cancels.
 *
 *  `replacesLater`: this is an EARLIER turn, so resending replaces every exchange below it.
 *  That is said while the editor is open — beside the button that does it, and on the button
 *  itself — together with where the replaced turns go, because the old editor resent a middle
 *  turn with no warning and the later turns were simply gone. */
function UserEditor({ initial, onSubmit, onCancel, replacesLater = false, canFork = false, failure = null }: {
  initial: string; onSubmit: (v: string) => void; onCancel: () => void
  replacesLater?: boolean; canFork?: boolean
  /** Why the last Resend did not go through. The editor stays open with the text, and the
   *  transcript below it is untouched, so the user can try again or cancel. */
  failure?: string | null
}) {
  const [v, setV] = useState(initial)
  const noticeId = useId()
  return (
    <div className="flex flex-col items-end gap-2">
      <textarea autoFocus value={v} onChange={(e) => setV(e.target.value)} rows={Math.min(10, v.split('\n').length + 1)}
        aria-label="Edit your message"
        aria-describedby={replacesLater ? noticeId : undefined}
        onKeyDown={(e) => {
          if (e.key === 'Escape') { e.preventDefault(); onCancel() }
          else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); onSubmit(v) }
        }}
        className="w-full resize-none rounded-2xl bg-surface-container px-5 py-4 text-on-surface text-[1.0625rem] leading-relaxed outline-none focus:ring-2 focus:ring-inset focus:ring-primary"
        style={{ maxWidth: 452 }} />
      {replacesLater && (
        <p id={noticeId} data-type="caption" className="flex w-full items-start gap-1.5 text-on-surface-var" style={{ maxWidth: 452 }}>
          <Rewind size={12} className="mt-0.5 shrink-0" />
          <span>Resending replaces everything below this message. {replacedTurnsAreKept(canFork)}</span>
        </p>
      )}
      {failure && (
        <div className="w-full" style={{ maxWidth: 452 }}>
          <InlineError icon multiline>{failure}</InlineError>
        </div>
      )}
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} className="px-3 text-on-surface-low">Cancel</Button>
        <Button size="sm" onClick={() => onSubmit(v)} disabled={!v.trim()} className="px-4"
          disabledReason={!v.trim() ? 'The message cannot be empty' : undefined}>{replacesLater ? 'Resend & replace' : 'Resend'}</Button>
      </div>
    </div>
  )
}

/** Select-to-quote — when the user selects text inside the transcript, float a
 *  toolbar (Quote + Copy) near the selection. Quote inserts an
 *  ATTRIBUTED blockquote into the composer (who said it, resolved from the
 *  enclosing turn); Copy copies the plain text. Positioning tracks BOTH mouseup
 *  and `selectionchange` so keyboard/touch selections get the toolbar too (not
 *  only mouse drags). */
function SelectionQuote({ scrollRef, onQuote, attributionFor }: {
  scrollRef: React.RefObject<HTMLDivElement | null>
  onQuote: (text: string, attribution?: string) => void
  attributionFor: (node: Node | null) => string | undefined
}) {
  const [pos, setPos] = useState<{ x: number; y: number; text: string; attribution?: string } | null>(null)
  const barRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const root = scrollRef.current
    if (!root) return
    const recompute = () => {
      const sel = window.getSelection()
      const text = sel?.toString().trim() ?? ''
      if (!text || !sel || sel.rangeCount === 0) { setPos(null); return }
      const range = sel.getRangeAt(0)
      // only within the transcript
      if (!root.contains(range.commonAncestorContainer)) { setPos(null); return }
      const r = range.getBoundingClientRect()
      const pr = root.getBoundingClientRect()
      // The toolbar is position:absolute inside the SCROLLING root, so its
      // coordinates are content-relative, not viewport-relative. Add scrollLeft/
      // scrollTop or it drifts far from the selection once the transcript scrolls.
      setPos({
        x: r.left - pr.left + root.scrollLeft + r.width / 2,
        y: r.top - pr.top + root.scrollTop - 8,
        text,
        attribution: attributionFor(range.commonAncestorContainer),
      })
    }
    const onUp = (e: MouseEvent) => {
      // a click ON the toolbar must NOT recompute/clear before its own handler runs.
      if (barRef.current && e.target instanceof Node && barRef.current.contains(e.target)) return
      recompute()
    }
    // selectionchange fires for keyboard + touch selection too; debounce a frame so
    // it settles (and never fights the mouseup path). No-selection clears the bar.
    let raf = 0
    const onSelChange = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const sel = window.getSelection()
        if (!sel || !sel.toString().trim()) { setPos(null); return }
        recompute()
      })
    }
    // clear on a fresh mousedown that ISN'T the toolbar (don't unmount mid-click).
    const onDown = (e: MouseEvent) => {
      if (barRef.current && e.target instanceof Node && barRef.current.contains(e.target)) return
      setPos(null)
    }
    document.addEventListener('mouseup', onUp)
    document.addEventListener('selectionchange', onSelChange)
    root.addEventListener('mousedown', onDown)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('selectionchange', onSelChange)
      root.removeEventListener('mousedown', onDown)
    }
  }, [scrollRef, attributionFor])
  if (!pos) return null
  const clear = () => { setPos(null); window.getSelection()?.removeAllRanges() }
  return (
    <SelectionToolbar ref={barRef} x={pos.x} y={pos.y} actions={[
      { icon: Quote, label: 'Quote', onPress: () => { onQuote(pos.text, pos.attribution); clear() } },
      { icon: Clipboard, label: 'Copy', onPress: () => { void copyText(pos.text, 'the selection'); clear() } },
    ]} />
  )
}

/** Render an assistant turn's ordered segments. Legacy `[OPTIONS: …]` markers in
 *  historical messages get stripped from the prose (they are never rendered as
 *  buttons — follow-up chips are the single suggestion surface) and referenced
 *  file paths surface as clickable chips below the prose. */
function AssistantSegments({ segments, liveCards, isLast, messageTs, streaming, onApprove, onAnswerQuestion, onSwitchToAgent, onOpenFile, onSetupModel, onRetry, chatSessionKey, citations, skillsUsed, cutOff, modelSubstitution }: {
  segments: Segment[]; isLast: boolean
  /** The tool results of the whole chat that show a run's live card, one per run. */
  liveCards: Set<Segment>
  messageTs?: string
  streaming?: boolean
  onApprove: (id: string, action: ApproveAction) => void
  onAnswerQuestion: (id: string, reply: QuestionReply) => Promise<unknown>
  onSwitchToAgent: (continuation: string) => void
  onOpenFile: (path: string) => void
  /** WT-04: the no-model empty-state's CTA — routes to Settings → Models through the hash router. */
  onSetupModel: () => void
  /** Send the turn's message again, for a turn that ended on its error without an answer — a
   *  failure, or a restart that cut it off. Given for the last settled turn only. */
  onRetry?: () => void
  chatSessionKey?: string
  citations?: MemoryCitation[]
  skillsUsed?: SkillUsed[]
  /** The reply stopped at the model's output cap (`meta.finish_reason === 'length'`). */
  cutOff?: boolean
  /** "Ran on X instead of Y: …" — another model answered than the one chosen for this chat. */
  modelSubstitution?: string
}) {
  const fullText = segments.filter((s) => s.kind === 'text').map((s) => (s as { text: string }).text).join('\n')
  // A restricted-mode turn may OFFER a one-click escalation to Agent (TM8).
  const { switchTo } = parseSwitchToAgent(fullText)

  // Transparency signals (what FED the turn / what was LEARNED / telemetry) are
  // pulled OUT of the inline flow and consolidated into one collapsible ledger at
  // the turn footer — holistic, non-intrusive, on demand (not three scattered lines).
  const ledger: { fed?: string; fedNoMemory?: boolean; learned?: string; learnedOrigin?: string; learnedRef?: string; stats?: string } = {}
  for (const s of segments) {
    if (s.kind !== 'activity') continue
    const ak = (s as ActivitySegment).activityKind
    if (ak === 'context' || ak === 'context_without_memory') {
      ledger.fed = (s as ActivitySegment).text
      ledger.fedNoMemory = ak === 'context_without_memory'
    }
    // The learned row carries its emitter's `origin` (and a preference's key) too — the
    // ledger is where the chip lives, so the discriminator has to travel with the text or the
    // tap has nothing to route on. Read off the SAME segment, so the two can never describe
    // different events.
    else if (ak === 'learned') {
      ledger.learned = (s as ActivitySegment).text
      ledger.learnedOrigin = (s as ActivitySegment).origin
      ledger.learnedRef = (s as ActivitySegment).ref
    }
    else if (ak === 'stats') ledger.stats = (s as ActivitySegment).text
  }
  const hasLedger = Boolean(ledger.fed || ledger.learned || ledger.stats)
  // The last segment the turn shows in its body: the ledger's rows are pulled out into its footer.
  const inLedger = (s: Segment) => s.kind === 'activity' && LEDGER_ACTIVITY_KINDS.includes((s as ActivitySegment).activityKind || '')
  const lastShown = [...segments].reverse().find((s) => !inLedger(s))

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
      // A workflow run started or inspected from chat renders as a live progress card for
      // the same reason: a run is a living thing, not the frozen JSON the tool returned. Once,
      // where the chat first names it: every status read the agent made after is its tool card.
      const wf = liveCards.has(seg) ? workflowRefFromTool(t.tool, t.output) : null
      if (wf) return <WorkflowProgressCard key={seg.id || i} refObj={wf} />
      // An automation made to run when she runs it ("a button that runs …") is shown with its
      // Run now, the button she asked for.
      const manual = t.done && t.ok !== false ? manualAutomationFromTool(t.tool, t.output) : null
      if (manual) return <ManualAutomationCard key={seg.id || i} refObj={manual} />
      return <ToolCard key={seg.id || i} seg={t} />
    }
    if (seg.kind === 'activity') return <ActivityLine key={i} seg={seg as ActivitySegment} />
    if (seg.kind === 'thinking') return <ThinkingBlock key={i} text={(seg as ThinkingSegment).text} defaultOpen={streaming} />
    if (seg.kind === 'error') {
      const { text, settings } = seg as ErrorSegment
      // WT-04: a fresh instance with no model resolves the turn to a WHAT/WHY/FIX
      // envelope that reads as a stack dump. Reframe THAT case as a calm setup
      // nudge; every other turn error keeps the plain danger strip.
      // The notice a turn ENDED on — nothing after it but the footer ledger — is where she reads
      // that her question went unanswered, so the way to send it again is right there, not only
      // in the hover row below.
      const endsTheTurn = seg === lastShown
      return isNoModelSetupError(text)
        ? <NoModelSetupState key={i} detail={text} onSetup={onSetupModel} />
        : (
          <InlineError key={i} icon multiline className="my-1" onRetry={endsTheTurn ? onRetry : undefined}>
            {text}
            {settings && <>{' '}<TextLink href={`#/settings/${settings}`} icon={ArrowRight} iconPosition="trailing" ink="emphasis">Open Settings</TextLink></>}
          </InlineError>
        )
    }
    if (seg.kind === 'question') {
      const q = seg as QuestionSegment
      return <QuestionCard key={q.id} seg={q} onAnswer={onAnswerQuestion} />
    }
    if (seg.kind === 'approval') {
      const ap = seg as ApprovalSegment
      return ap.queued
        ? <ApprovalCard key={ap.id || i} seg={ap} answers="once" onAct={answerQueued} />
        : <ApprovalCard key={ap.id || i} seg={ap} onAct={onApprove} />
    }
    if (seg.kind === 'text') {
      // hide the raw [OPTIONS: …] and [SWITCH_TO_AGENT: …] markers from the prose
      const body = parseSwitchToAgent(parseOptions(seg.text).body).body
      return body ? <Markdown key={i} widgets onFileClick={onOpenFile} chatSessionKey={chatSessionKey} messageTs={messageTs} streaming={streaming} citations={citations}>{body}</Markdown> : null
    }
    return null
  }
  const isProcess = (s: Segment) =>
    s.kind === 'tool' || s.kind === 'error' || s.kind === 'approval' || s.kind === 'question' ||
    (s.kind === 'activity' && !inLedger(s))

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
  const failedCount = failedStepCount(workSegs)
  const unaskedCount = unaskedStepCount(workSegs)
  const finalSegs = (lastProcessIdx >= 0 ? segments.slice(lastProcessIdx + 1) : segments).filter((s) => s.kind === 'text')

  // An SDLC create/start/status segment becomes a LIVE progress card that must stay
  // visible — never buried inside the collapsed "Worked through N steps" disclosure
  // (the card is a living, auto-refreshing widget, not a log line). Pull those out of
  // the work fold + render them at the top level between the work + the final answer.
  const isSdlc = (s: Segment) => s.kind === 'tool'
    && !!(s as ToolSegment).done && !!sdlcRefFromTool((s as ToolSegment).tool, (s as ToolSegment).output)
  // A workflow card is live too, so it gets the same exemption: burying an auto-refreshing
  // widget inside a collapsed disclosure hides the one thing the user came back to check. So is
  // an ask still waiting for work the chat started (`waitsPastItsTurn`).
  const isWorkflow = (s: Segment) => liveCards.has(s)
  // A question still waiting on her is never folded away either: the agent is halted on it.
  const waitsForHer = (s: Segment) => s.kind === 'question' && (s as QuestionSegment).answerable && !(s as QuestionSegment).outcome
  const isLiveCard = (s: Segment) => isSdlc(s) || isWorkflow(s) || waitsPastItsTurn(s) || waitsForHer(s)
  const sdlcNodes = segments.filter(isLiveCard).map(renderItem).filter(Boolean)
  // The ledger's rows are said in the footer only. What fed the turn arrives as the turn starts,
  // inside the work's span, and was drawn there too, live: a line a reload never showed.
  const workNodes = workSegs.filter((s) => !isLiveCard(s) && !inLedger(s)).map(renderItem).filter(Boolean)
  const finalNodes = finalSegs.map(renderItem).filter(Boolean)
  const hasFinal = finalNodes.length > 0
  // Collapse the work only when the turn is done AND produced a final answer to
  // focus on; otherwise show it inline (still streaming, or it ended on a step).
  const collapseWork = !streaming && hasFinal && stepCount > 0

  return (
    <>
      {workNodes.length > 0 && (
        collapseWork
          ? <AgentWork stepCount={stepCount} toolNames={toolNames} failedCount={failedCount} unaskedCount={unaskedCount}>{workNodes}</AgentWork>
          : <div className="flex flex-col gap-1">{workNodes}</div>
      )}
      {/* Live SDLC progress cards stay at the top level, always visible — never
          folded into the collapsed work disclosure. */}
      {sdlcNodes.length > 0 && <div className="flex flex-col gap-1">{sdlcNodes}</div>}
      {finalNodes}
      {cutOff && !streaming && <ReplyCutNote />}
      {modelSubstitution && !streaming && <ModelSubstitutionNote text={modelSubstitution} />}

      {/* What CAPABILITY fed the turn — a peer of the ledger's "what context fed it",
          kept as its own always-visible chip rather than a collapsed ledger row: the count is
          the whole signal, and burying it behind a disclosure would make "which skills am I
          actually paying for" a thing you have to go looking for. */}
      {skillsUsed && skillsUsed.length > 0 && <SkillsUsedChip skills={skillsUsed} />}

      {hasLedger && <ContextLedger fed={ledger.fed} fedNoMemory={ledger.fedNoMemory} learned={ledger.learned} learnedOrigin={ledger.learnedOrigin} learnedRef={ledger.learnedRef} stats={ledger.stats} />}

      {/* Agent-driven one-click escalation (TM8): the model proposed a switch out
          of a restricted mode; the user approves with a single click, which flips
          the session to Agent AND runs the continuation. Shown on the last turn
          once it's done (the consent gate that keeps Ask/Plan from self-escalating). */}
      {isLast && !streaming && switchTo !== null && (
        <div className="mt-3">
          <Button variant="primary" size="sm" onClick={() => onSwitchToAgent(switchTo)} className="gap-1.5">
            <Bot size={15} strokeWidth={2.2} />
            Switch to Agent &amp; run it
          </Button>
        </div>
      )}

    </>
  )
}

/** The agent's intermediate work for a completed turn, folded into one compact
 *  disclosure so the FINAL ANSWER leads and the steps that produced it open only
 *  on demand. Summarizes as "Worked through N steps" + how many of them failed +
 *  the distinct tools used. Collapsed by default; expanding reveals the original
 *  tool/approval/activity cards unchanged. (Replaces the old dot+rail timeline, whose
 *  connector added little once every step was followed by prose and whose dot ✓
 *  duplicated each card's own status glyph.)
 *
 *  The failure count sits before the tool names and never truncates: the reply below
 *  can say the work succeeded when a step did not, and the fold is where that shows. The count
 *  of steps the agent CLI ran without asking her sits beside it for the same reason: folded, the
 *  cards that say so are out of sight. */
function AgentWork({ stepCount, toolNames, failedCount, unaskedCount, children }: { stepCount: number; toolNames: string[]; failedCount: number; unaskedCount: number; children: React.ReactNode }) {
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
        <span className="shrink-0" style={fvs(500)}>
          {open ? 'Hide work' : `Worked through ${stepCount} ${stepCount === 1 ? 'step' : 'steps'}`}
        </span>
        {!open && failedCount > 0 && (
          <span className="inline-flex shrink-0 items-center gap-xs text-danger" style={fvs(550)}>
            · <AlertTriangle size={12} aria-hidden /> {failedCount} failed
          </span>
        )}
        {!open && unaskedCount > 0 && (
          <span className="inline-flex shrink-0 items-center gap-xs text-warning" style={fvs(550)}>
            · <ShieldAlert size={12} aria-hidden /> {unaskedCount} ran without asking you
          </span>
        )}
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


/** A reply that stopped at the model's OUTPUT cap, said at the point it stops.
 *
 *  Measured on the bundled model: a 320-token cap ended replies mid-sentence and nothing said
 *  so, which reads as the model trailing off — or as the product being broken — rather than as
 *  a limit. The line sits directly under the reply, where the unfinished sentence is. */
function ReplyCutNote() {
  return (
    <div className="mt-1.5 mb-1 flex items-center gap-1.5 text-on-surface-low/80 text-[0.75rem]">
      <Scissors size={11} className="shrink-0 opacity-70" aria-hidden />
      <span>Cut off: this reply reached the model's maximum length.</span>
    </div>
  )
}

/** The reply came from another model than the one chosen for it, said under the reply.
 *
 *  An agent's pinned model — or the chat's own pick — could not run, and the chat model answered.
 *  The live line said so before the reply streamed; this is the half that survives a reload, so
 *  an old reply never reads as the chosen model's. The sentence is the server's, word for word. */
function ModelSubstitutionNote({ text }: { text: string }) {
  return (
    <div data-testid="model-substitution-note" className="mt-1.5 mb-1 flex items-start gap-1.5 text-[0.75rem]" style={{ color: 'var(--color-warning)' }}>
      <Shuffle size={11} className="mt-0.5 shrink-0 opacity-80" aria-hidden />
      <span>{text}</span>
    </div>
  )
}

/** "used skill trip-research" — the per-turn skill-allocation chip, naming what joined the turn.
 *
 *  Hover carries the skills' full keys in the allocator's own order via `title` — the same
 *  affordance the {@link ContextLedger} trigger beside it uses, so the two footer chips
 *  behave alike instead of introducing a second hover mechanism for one line of text.
 *  `title` on a non-interactive element is a HOVER affordance only, not an accessible
 *  name, so the names are also written into the visible label below (`skillsUsedLabel`)
 *  rather than living solely in the tooltip.
 *
 *  A `reduced` skill is called out two ways rather than one, because a name alone would
 *  present a summary-only load as a full one: the chip appends "· M summarized" so the
 *  distinction survives without hovering, and the hover list marks each such skill by name.
 *  Rendered as a non-interactive element on purpose — there is no per-skill surface to land
 *  on, and a chip that looked like a button but did nothing would be the worse lie. */
function SkillsUsedChip({ skills }: { skills: SkillUsed[] }) {
  const reduced = skills.filter((s) => s.state === 'reduced').length
  return (
    <div className="mt-2 mb-1 flex items-center gap-1.5 text-on-surface-low/80 text-[0.75rem]"
      title={skillsUsedTitle(skills)}>
      <Sparkles size={11} className="shrink-0 opacity-70" />
      <span>
        {skillsUsedLabel(skills)}
        {reduced > 0 && <span className="opacity-80"> · {reduced} summarized</span>}
      </span>
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
    if (!(await confirm({
      title: 'Delete chat?',
      body: `"${sessionTitle(s)}" and its history will be permanently removed.`,
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
                        // THE surface SM-3 deferred windowing on ("pending
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

  // 🔴 THE CATCH USED TO `setEnabled(false)`, and the `!enabled` branch below names a specific
  // environment variable as the cause — so an unreachable `/api/autonudge` told the user
  // "Disabled on this server (PERSONALCLAW_AUTONUDGE=0)" about a server that might have it on.
  // A confident wrong answer about a setting is worse than a silent one; the rejection is recorded
  // and rendered as itself.
  const [cfgErr, setCfgErr] = useState<unknown>(null)
  const load = useCallback(() => {
    api.autonudgeGet(session).then((r) => {
      setCfgErr(null)
      setEnabled(r.enabled)
      setLoop(r.loop)
      if (r.loop) { setMsg(r.loop.message); setIdle(r.loop.idle_secs); setMaxCycles(r.loop.max_cycles) }
    }).catch((e) => setCfgErr(e))
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
            {cfgErr ? (
              // Tested BEFORE `!enabled`: `enabled` is unchanged by a failed read, so an error
              // branch after it would be unreachable for whichever value it happens to hold.
              <FieldError>Couldn't load your auto-nudge setting — {(cfgErr as Error)?.message || 'the server did not respond'}. Reopen this panel to try again.</FieldError>
            ) : !enabled ? (
              <p className="text-[0.8125rem] text-on-surface-low">Disabled on this server (<code className="font-mono">PERSONALCLAW_AUTONUDGE=0</code>).</p>
            ) : (<>
              <p className="text-[0.8125rem] text-on-surface-low">When a turn finishes and you're idle, this message is re-injected into this chat to keep it working on its own.</p>
              <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={3} autoFocus
                placeholder="e.g. Continue toward the goal; if done, write a summary and stop."
                className="w-full rounded-md bg-surface-high px-2 py-1.5 text-on-surface text-[0.8125rem] outline-none resize-y focus:ring-2 focus:ring-inset focus:ring-primary" />
              <div className="flex items-center gap-3 text-[0.8125rem] text-on-surface-var">
                <label className="flex items-center gap-1">Idle
                  <input type="number" min={15} value={idle} onChange={(e) => setIdle(Number(e.target.value))} className="w-16 rounded bg-surface-high px-1.5 py-0.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />s</label>
                <label className="flex items-center gap-1">Max cycles
                  <input type="number" min={0} value={maxCycles} onChange={(e) => setMaxCycles(Number(e.target.value))} className="w-14 rounded bg-surface-high px-1.5 py-0.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" /></label>
              </div>
              {loop && <p className="text-[0.75rem] text-on-surface-low">Active · {loop.cycle_count} cycle{loop.cycle_count === 1 ? '' : 's'} fired{loop.max_cycles ? ` / ${loop.max_cycles}` : ''}.</p>}
              <div className="flex justify-end gap-2 mt-1">
                {loop && <Button variant="ghost" size="sm" onClick={stop} disabled={busy} disabledReason={BUSY_REASON}><X size={14} /> Stop</Button>}
                <Button size="sm" onClick={arm} disabled={busy || !msg.trim()}
                  disabledReason={!msg.trim() ? 'Write the message first' : BUSY_REASON}><Check size={14} /> {loop ? 'Update' : 'Arm'}</Button>
              </div>
            </>)}
          </div>
        </Modal>
      )}
    </>
  )
}


import { useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { api } from '../../../lib/api'
import { useQuery } from '../../../lib/data'
import { FindBar } from '../../../ui/FindBar'
import { MessageUser } from '../../../ui/chat/MessageUser'
import { MessageAssistant } from '../../../ui/chat/MessageAssistant'
import { ModelWaits } from '../../../ui/ModelWaitNotice'
import { AssistantActions, UserActions } from '../MessageActions'
import { TurnAttachments } from '../AttachmentChips'
import { FollowupChips, followupAnnouncement } from '../FollowupChips'
import { CheckWorkChip } from '../CheckWorkChip'
import { SessionMapRail } from '../SessionMapRail'
import { NeedsModelNote, MODELS_PATH } from '../NoModelSetupState'
import { findSegments } from '../findSegments'
import { editReplacesLaterTurns } from '../editReplaces'
import { asSent } from '../pasteBlocks'
import { type ChatTurn, turnText, markCoordOf } from '../chatTypes'
import { workedMs } from '../turnRuns'
import type { ChatStream } from '../useChatStream'
import type { useTranscript } from './useTranscript'
import { AssistantSegments } from './AssistantSegments'
import { UserEditor } from './UserEditor'
import { RewindDivider } from './RewindDivider'
import { SelectionQuote } from './SelectionQuote'
import { QuestionInView } from './QuestionInView'

// ── The chat's transcript region ──────────────────────────────────────────────────────────────
//
// The Session Map's gutter rail and the scrolling transcript beside it: the find bar, the
// select-to-quote toolbar, every turn with its actions, the follow-up chips and check-work offer
// under the last one, the live line that says what the running turn is doing (`liveLine`, the page's
// `ui/TurnStatusRow`), last in the turn column under the latest message and scrolling with the thread,
// and the live regions that narrate the turn. Its state and its actions are `useTranscript`'s; the
// turns are the stream's.

/** The transcript's bottom fade: opaque down to its bottom padding (`py-2xl` on the turn
 *  column), transparent at the edge. Keyed to that padding so the resting view — scrolled
 *  to the newest turn — is never faded; see the scroller for why the edge fades at all.
 *  (A mask reads only alpha, so `black` here is "keep", not a colour.) */
const TRANSCRIPT_END_FADE = 'linear-gradient(to bottom, black calc(100% - var(--spacing-2xl)), transparent)'

export function Transcript({ transcript, stream, sessionId, memoryMode, navigate, onOpenFile: setOpenFile, onQuote: quoteToComposer, onPickFollowup, send, speak, speakingTurn, mapAudio = false, liveLine }: {
  transcript: ReturnType<typeof useTranscript>
  stream: Pick<ChatStream, 'sessionRef' | 'turns' | 'streaming' | 'srAnnounce' | 'followups' | 'setFollowups' | 'followupsNeed' | 'checkWorkOffer' | 'setCheckWorkOffer' | 'subagents'>
  sessionId: string | null
  memoryMode: string
  navigate: (p: string, opts?: { replace?: boolean }) => void
  onOpenFile: (path: string) => void
  /** A passage selected in the transcript, quoted into the composer. */
  onQuote: (text: string, attribution?: string) => void
  /** A follow-up chip picked: its text goes to the composer to edit before sending. */
  onPickFollowup: (text: string) => void
  send: (text: string) => Promise<void>
  speak: (text: string, turnIndex: number) => void
  speakingTurn: number | null
  /** The owner turned on Settings › Speech › "Session Map reacts to audio". */
  mapAudio?: boolean
  /** What the running turn is doing now: the last thing in the turn column, under the latest
   *  message, while a turn runs. */
  liveLine?: React.ReactNode
}) {
  const { scrollRef, endRef, followTurnRef, turnNodes, findOpen, setFindOpen, findSeed, isMobile, mapOpen, sessionEntries, jumpToTurn, toggleBookmark, liveCards, answerQuestion, approve, editingTurn, setEditingTurn, editFailure, setEditFailure, editResend, rewindTo, forkAt, forkRewound, regenerate, switchVariant, switchToAgentAndRun, attributionForNode } = transcript
  const { sessionRef, turns, streaming, srAnnounce, followups, setFollowups, followupsNeed, checkWorkOffer, setCheckWorkOffer } = stream
  // The latest question, which "Keep my question in view" pins while a long reply streams.
  const lastQuestion = turns.map((t) => t.role).lastIndexOf('user')
  const [questionNode, setQuestionNode] = useState<HTMLElement | null>(null)
  // "Show timestamps" — the same persisted-query shape as the stream's settings reads, and read HERE because
  // this is the component that draws a turn's `ts`. Gating happens by NOT PASSING the stamp down, so
  // the action row never learns about the preference: absent means "no time", which is also what a
  // turn mid-stream (no stamp yet) means, so one branch covers both. Default off, matching the
  // config default, so an unresolved read shows nothing rather than flashing times on and off.
  const { data: showTimestamps } = useQuery('chat:show-timestamps', () => api.dashboardConfig().then((c) => c.show_timestamps), { persist: true })
  const stampOf = (turn: { ts?: string }) => (showTimestamps ? turn.ts : undefined)
  // ── SESSION MAP RAIL + TRANSCRIPT ────────────────────────────────────────────
  // 🔑 THE RAIL IS A SIBLING OF THE SCROLLER, NOT A CHILD OF IT, and that IS the
  // "stays fixed while the transcript scrolls" requirement rather than a styling
  // choice: an element outside the scroll container has no scroll offset to
  // inherit, so no `sticky`, no scroll listener and no re-positioning code exists
  // to get wrong. `e2e/sessionMap.spec.ts` asserts BOTH halves in a real browser —
  // the rail's box does not move while the transcript scrolls, AND the scroll
  // container does not contain the rail — because a `sticky` rail inside the
  // scroller would pass the box check and still drift under an ancestor transform.
  // The gutter is an empty column (measured: "No left sidebar"), so the rail
  // costs the centred transcript nothing.
  return (
    <div className="relative flex min-h-0 flex-1">
      {mapOpen && !isMobile && (
        <SessionMapRail entries={sessionEntries} turnNodes={turnNodes.current}
          scrollRef={scrollRef} onJumpTo={jumpToTurn} onToggleBookmark={toggleBookmark} replyPending={streaming}
          audioPresence={mapAudio} />
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
        {/* The turn column spans the chat's pane, as does the composer under it, inside the pane's
            inner padding: a thread uses the room the window gives it.
            `--composer-fold-room`: reading mode's folded composer floats over the transcript's end,
            so the end makes room for it (`composer/ComposerFold`). */}
        <div className="flex w-full flex-col gap-2xl px-l py-2xl"
          style={{ paddingBottom: 'calc(var(--spacing-2xl) + var(--composer-fold-room, 0px))' }}>
          {/* "Keep my question in view": the latest question, pinned while a reply that scrolled
              it out streams (Settings › Chat). */}
          <QuestionInView streaming={streaming} node={questionNode} scrollRef={scrollRef}
            question={lastQuestion >= 0 ? turns[lastQuestion].segments.map((s) => (s.kind === 'text' ? s.text : '')).join('') : ''} />
          {turns.map((turn, i) => {
            const isLast = i === turns.length - 1
            const turnTextOf = (t: ChatTurn) => t.segments.map((s) => (s.kind === 'text' ? s.text : '')).join('')
            return (
              // `data-transcript-turn` names a turn node by its side of the exchange, so the
              // browser gate can put a REPLY on screen without its question and check the
              // Session Map still lights that question — the registry itself is a ref.
              // `data-turn-coord` is the turn's map coordinate, so a media element playing inside it
              // knows which Session Map marker it belongs to (`lib/mediaPresence`).
              <div key={i} className="relative" data-transcript-turn={turn.role} data-turn-coord={markCoordOf(turn, i)}
                ref={(el) => {
                  const c = markCoordOf(turn, i)
                  if (el) turnNodes.current.set(c, el); else turnNodes.current.delete(c)
                  if (i === lastQuestion) setQuestionNode(el)
                }}>
                {turn.role === 'user' ? (
                  editingTurn === i ? (
                    <UserEditor initial={turnTextOf(turn)} onCancel={() => { setEditingTurn(null); setEditFailure(null) }}
                      replacesLater={editReplacesLaterTurns(turns, i)} canFork={memoryMode === 'persistent'}
                      failure={editFailure} onSubmit={(v) => editResend(i, v)} />
                  ) : (
                    <div className="group/msg">
                      <MessageUser onFileClick={setOpenFile} pastes={turn.pastes} optimized={turn.optimized} ranPrompt={turn.ranPrompt} steered={turn.steered}
                        onExpand={() => { followTurnRef.current = false }}>{turnTextOf(turn)}</MessageUser>
                      {turn.files && turn.files.length > 0 && <TurnAttachments paths={turn.files} delivery={turn.imageDelivery} onOpenFile={setOpenFile} />}
                      {turn.rewound && turn.rewound.length > 0 && (
                        <RewindDivider snapshots={turn.rewound} canFork={memoryMode === 'persistent'} onFork={(si) => forkRewound(i, si)} />
                      )}
                      {!streaming && <UserActions text={asSent(turnTextOf(turn), turn.pastes ?? []).text} canFork={memoryMode === 'persistent'}
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
                    <AssistantSegments segments={turn.segments} liveCards={liveCards} helpers={stream.subagents} isLast={isLast} messageTs={turn.ts} streaming={isLast && streaming} onApprove={approve} onAnswerQuestion={answerQuestion} onSwitchToAgent={switchToAgentAndRun} onOpenFile={setOpenFile} onSetupModel={() => navigate(MODELS_PATH)} onRetry={isLast && !streaming ? regenerate : undefined} chatSessionKey={sessionRef.current ?? undefined} citations={turn.citations} skillsUsed={turn.skillsUsed} cutOff={turn.cutOff} modelSubstitution={turn.modelSubstitution} servedModel={turn.servedModel} workedMs={workedMs(turn.runs)} />
                  </MessageAssistant>
                )}
                {/* Follow-up chips under the last assistant turn only,
                    once the reply has settled — click fills the composer to edit,
                    send-glyph sends. */}
                {turn.role === 'assistant' && isLast && !streaming && followups.length > 0 && (
                  <FollowupChips items={followups} onPick={onPickFollowup} onSend={(t) => { setFollowups([]); void send(t) }} />
                )}
                {/* No chips because no model is chosen for them: the place says why, and
                    where to choose one. */}
                {turn.role === 'assistant' && isLast && !streaming && followupsNeed && (
                  <div className="mt-m"><NeedsModelNote sentence={followupsNeed} /></div>
                )}
                {/* "Check this work" offer — user-clicked only. */}
                {turn.role === 'assistant' && isLast && !streaming && checkWorkOffer && (
                  <CheckWorkChip label={checkWorkOffer.label}
                    onRun={() => { const p = checkWorkOffer.prompt; setCheckWorkOffer(null); void send(p) }} />
                )}
              </div>
            )
          })}
          {/* A step of this turn waiting for a busy local model: why, and what next. */}
          {streaming && sessionId && <ModelWaits session={sessionId} className="py-s" />}
          {/* What the running turn is doing now, bare, under the latest message: the column's last
              line, before the end the view follows, so following the end keeps it in view. */}
          {liveLine}
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
            {streaming ? '' : followupsNeed || followupAnnouncement(followups.length)}
          </div>
        </div>
      </div>
    </div>
  )
}

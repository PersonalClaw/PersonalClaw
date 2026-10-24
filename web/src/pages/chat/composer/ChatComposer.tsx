import { useEffect } from 'react'
import { AnimatePresence } from 'framer-motion'
import { AlertTriangle, BookText, Boxes, Camera, Check, Clock, CornerDownLeft, EyeOff, ListChecks, Loader2, NotebookPen, Pin, Repeat, X } from 'lucide-react'
import { api } from '../../../lib/api'
import { failureSentence, reportActionFailure } from '../../../app/reportingWrite'
import { Button } from '../../../ui/Button'
import { IconButton } from '../../../ui/IconButton'
import { Meter } from '../../../ui/Meter'
import { MenuRow } from '../../../ui/Popover'
import { ProjectPicker } from '../../../ui/ProjectPicker'
import { SnipOverlay } from '../../../ui/SnipOverlay'
import { ComposerStage } from '../../../ui/ComposerStage'
import { ComposerNoticeLine } from '../../../ui/composer/ComposerNotice'
import type { ComposerControls } from '../../../ui/composer/types'
import { rememberSwitchAnswered, switchAnswered, switchSentence, type AskBeforeSwitch } from '../../../ui/composer/dearerSwitch'
import { ChatPlanGate } from '../../../ui/chat/ChatPlanGate'
import type { VoiceLoopConfig } from '../voiceConfig'
import { PromptPalette } from '../PromptPalette'
import { SessionSkillsReview } from '../SessionSkillsReview'
import { RoutingChip } from '../RoutingChip'
import { OrganizeChip } from '../OrganizeChip'
import { BundledFloorNotice } from '../BundledFloorNotice'
import { AppPermissionNotice } from '../StartedByApp'
import { AttachmentChips } from '../AttachmentChips'
import { isImagePath } from '../imageAttachments'
import { MEMORY_MODE_NOTICE } from '../memoryModeCopy'
import { lastServedModel } from '../chatTypes'
import type { ChatStream } from '../useChatStream'
import type { useChatComposer } from './useChatComposer'
import { ArtifactContextPicker, KnowledgeContextPicker } from './ContextPickers'
import { KnowledgeChips } from './KnowledgeChips'
import { PasteCards } from './PasteCards'
import { QueueStack } from './QueueStack'
import { AutoNudgeMenuItem } from './AutoNudgeMenuItem'
import { ExecutionBoundary, PERMISSION_MODES, TaskModePill, said, useBoundaryReading } from './ExecutionBoundary'
import { MEMORY_MODES } from '../memoryModeCopy'
import { publishChatEnvironment, useBoundaryAsks } from '../inspector/chatEnvironment'
import { publishContextReading, setContextPinned, useContextPinned } from '../../../app/pinnedContext'

// ── The chat's composer region ────────────────────────────────────────────────────────────────
//
// The composer and everything stacked above and below it: the memory-mode and app-permission
// notices, the notice line, uploads and their progress, the attachment, knowledge and paste chips,
// the revert-optimize offer, the steers and the queue, the pickers and the crop step, the skills
// review, the routing chip, the organize chip, the plan gate, the floor-model label, and a new
// chat's project. In the composer's footer, beside the agent-and-model pill: the task mode and the
// Execution Boundary control (`ExecutionBoundary`), where the next turn may act. Its state and its
// actions are `useChatComposer`'s.

const CHAT_CONTROLS: ComposerControls = {
  agent: true, model: true, approval: false, reasoning: true,
  attach: true, mic: true, optimize: true, slash: true,
}

/** The ask before a switch of the open chat `session`'s model: the gateway says whether to ask and
 *  with what prices (`GET /api/chat/switch-price`), and a chat whose owner answered Switch once is
 *  not asked again. A price that could not be read is asked about with the reason, never switched
 *  past in silence. */
function askBeforeSwitch(session: string): AskBeforeSwitch {
  return async (m) => {
    if (switchAnswered(session)) return null
    const switched = () => rememberSwitchAnswered(session)
    const id = m.model_name || m.name
    try {
      const price = await api.chatSwitchPrice(session, m.provider, id)
      return price.ask ? { said: switchSentence(price), switched } : null
    } catch (e) {
      return { said: `${failureSentence(`read what the next reply costs on ${id}`, e)} The next reply re-reads this whole chat on the model it runs on.`, switched }
    }
  }
}

export function ChatComposer({ composer, stream, sessionId, started, navigate, onOpenFile: setOpenFile, voiceCfg, speakingTurn, onBriefAgent, onChangeFolder }: {
  composer: ReturnType<typeof useChatComposer>
  /** Where the Context control's actions go: the Tools page, and what memory recalls. */
  navigate: (p: string) => void
  stream: ChatStream
  sessionId: string | null
  started: boolean
  onOpenFile: (path: string) => void
  /** Prime the next turn with background context (`useChatHeader`'s Brief the agent), a row of the
   *  `+` menu once the chat has started. */
  onBriefAgent?: () => void
  /** Open the Working directory prompt, from the Execution Boundary control of a started chat. */
  onChangeFolder?: () => void
  /** The voice settings hands-free reads (`useChatVoice`). */
  voiceCfg: VoiceLoopConfig
  /** The reply being read out, which hands-free waits on. */
  speakingTurn: number | null
}) {
  const { acpFor, activatePlanMode, startGoalLoop, applySelection, chooseApproval, chooseTaskMode, postureKnown, artifactPickerOpen, attach, attachError, attachSnip, attachedPaths, captureProvider, captureScreenArea, composerRef, data, historyTexts, input, insertPrompt, knowledgePickerOpen, livePasteBlocks, memoryMode, mentionedArtifacts, mentionedFiles, mentionedKnowledge, replyStyle, notice, onLargePaste, onMentionFile, onMentionKnowledge, openAgentSignal, openModelSignal, openProjectSignal, openReasoningSignal, optimize, optimizing, pinScreenFrame, preOptimize, projectId, promptPaletteOpen, recallPrompt, removePaste, revertOptimize, routingSuggestion, screenShare, selection, send, setArtifactPickerOpen, setAttachError, setAttachedPaths, setComposerFocused, setInput, setKnowledgePickerOpen, setMemoryMode, setMentionedArtifacts, setMentionedFiles, setMentionedKnowledge, setPreOptimize, setProjectId, setPromptPaletteOpen, setSelection, setSnip, snip, stop, takeBack, toast, transcribe, uploadAborts, uploadHold, uploads, setRoutingSuggestion } = composer
  const { sessionRef, turns, streaming, turnPhase, takesSteers, sessionSkillsEpoch, queued, setQueued, steered, followups, setFollowups, contextPct, contextWindow, contextParts, sessionCost, autoChain, startedBy, workingDir } = stream
  // The status-line pin is the owner's; the reading it shows is this chat's, live, and goes with it.
  const contextPinned = useContextPinned()
  useEffect(() => {
    publishContextReading(sessionId ? { pct: contextPct, window: contextWindow, parts: contextParts, cost: sessionCost } : null)
  }, [sessionId, contextPct, contextWindow, contextParts, sessionCost])
  useEffect(() => () => publishContextReading(null), [])
  // The sandbox and the network of the next turn, the gateway's words: an open chat's once its own
  // read has landed, a new chat's for the agent it is set to.
  const boundaryReading = useBoundaryReading(sessionId, !sessionId || postureKnown, acpFor(selection.agent)?.providerId ?? '')
  // The working folder in words: the chat's own, else the folder a chat with none works in.
  const folder = workingDir || (!sessionId && projectId ? 'the project’s folder' : 'the workspace')
  // What the Execution Boundary shows, for the session inspector's Environment section, which opens
  // the control to change it (`inspector/chatEnvironment`).
  const approval = selection.approval ?? 'normal'
  useEffect(() => {
    publishChatEnvironment({
      projectId: projectId || '', folder,
      permission: (PERMISSION_MODES.find((m) => m.id === approval) ?? PERMISSION_MODES[0]).label,
      memory: (MEMORY_MODES.find((m) => m.id === memoryMode) ?? MEMORY_MODES[0]).label,
      sandbox: said(boundaryReading, 'sandbox'), reach: said(boundaryReading, 'reach'),
    })
  }, [projectId, folder, approval, memoryMode, boundaryReading])
  useEffect(() => () => publishChatEnvironment(null), [])
  const boundaryAsks = useBoundaryAsks()
  return (
    // `data-tour="chat"` — the product tour's chat stop points at the composer stage.
    // On the chat route this wrapper is the composer.
    // `data-turn-stop-scope`: Esc typed here stops a running turn (`ui/TurnStatusRow`), as it does
    // outside any field.
    <div data-tour="chat" data-turn-stop-scope="" className="w-full" style={{ maxWidth: 'var(--content-width)' }}>
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
      {/* Steered messages on their way into the answer being written: the turn takes each
          at its next step, and it then moves into the transcript as her bubble. Unlike a
          queued item there is nothing to cancel or reorder. Rendered so a steer is visible
          before then: the backend broadcasts a "Steering: …" activity_event, which this page
          drops (`kind === 'steer'`) for this list. */}
      {steered.length > 0 && (
        <div className="mb-2 flex flex-col gap-1" aria-live="polite">
          {steered.map((s) => (
            <div key={s.ts}
              className="flex items-start gap-1.5 text-[0.75rem] text-on-surface-var">
              <CornerDownLeft size={12} className="mt-0.5 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1 truncate">
                Steering into this answer: {s.text}
              </span>
            </div>
          ))}
        </div>
      )}
      {/* Queued messages (typed mid-stream) — the backend sends them one-by-one as
          each turn finishes; each can be cancelled while still pending. */}
      <QueueStack items={queued} canInterrupt={streaming}
        onCancel={(id) => { setQueued((prev) => prev.filter((q) => q.id !== id)); const s = sessionRef.current; if (s) api.cancelQueued(s, id).catch(reportActionFailure('cancel that queued message')) }}
        onEdit={(q) => {
          // Honest "edit": there's no queue-edit endpoint, so cancel the pending item
          // and drop its text back in the composer for the user to revise + resend
          // (avoids a fake in-place edit that would silently re-queue at the back). It comes back
          // with what she pasted into it, and after the draft she is writing, if any: the
          // message leaves the queue, so nothing of it may be lost on the way to the composer.
          setQueued((prev) => prev.filter((x) => x.id !== q.id)); const s = sessionRef.current; if (s) api.cancelQueued(s, q.id).catch(reportActionFailure('cancel that queued message'))
          const back = takeBack(q.content, q.pastes ?? [])
          setInput((cur) => (cur.trim() ? `${cur.trimEnd()}\n\n${back}` : back))
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
        {/* Suggested organization. Keyed on the message count so it re-asks once a
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
            onTaskMode={(m) => setSelection((sel) => ({ ...sel, taskMode: m }))}
            onOpenChat={(key) => navigate(`chat/${encodeURIComponent(key)}`)} />
        )}
        {/* The honest label on the bundled zero-config floor model, immediately above
            the composer so it is read where the answers arrive. Mounted unconditionally and
            renders nothing unless chat really is resolving to a floor provider — which is
            false on every home that has bound anything. */}
        <BundledFloorNotice />
        <ComposerStage ref={composerRef} value={input} onChange={(v) => { setInput(v); if (preOptimize !== null) setPreOptimize(null); if (followups.length && v.trim().length >= 3) setFollowups([]) }} onSend={() => send()}
          streaming={streaming} phase={turnPhase} onStop={stop} controls={CHAT_CONTROLS} data={data}
          selection={selection} onSelect={applySelection} onAttach={attach} onFocusChange={setComposerFocused}
          replyStyle={{ ...replyStyle.pill, onSelect: (s) => void replyStyle.pill.onSelect(s) }}
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
              {/* A goal loop, from a new thread (#3824): the draft becomes the loop's task, and the loop
                  composer drafts and plans it. A started chat is a thread already. */}
              {!started && (
                <MenuRow icon={<Repeat size={16} />} label="Start a goal loop"
                  hint="Hand this draft to a goal loop, which works cycle by cycle until it is done"
                  onClick={() => { close(); startGoalLoop() }} />
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
              {/* Background context for the next reply, never shown in the transcript: it left the
                  header for here, beside what else goes into a message. */}
              {started && sessionRef.current && onBriefAgent && (
                <MenuRow icon={<NotebookPen size={16} />} label="Brief the agent" hint="Background context for your next message"
                  onClick={() => { close(); onBriefAgent() }} />
              )}
            </>
          )}
          onMentionFile={onMentionFile} onMentionKnowledge={onMentionKnowledge} onLargePaste={onLargePaste}
          openModelSignal={openModelSignal} openAgentSignal={openAgentSignal} openReasoningSignal={openReasoningSignal}
          onOptimize={optimize} optimizing={optimizing} history={historyTexts} onRecall={recallPrompt}
          onTranscribe={transcribe} onMicError={(msg) => notice.showError(msg, 'voice-input')} canQueue canSteer={takesSteers} sendHeldReason={uploadHold} contextPct={contextPct} contextWindow={contextWindow} sessionCost={sessionId ? sessionCost : null} contextPin={{ pinned: contextPinned, onPin: setContextPinned }} onCompact={sessionId ? () => { void send('/compact') } : undefined}
          contextParts={contextParts}
          contextActions={sessionId ? { tools: () => navigate('tools'), memory: () => navigate('memory?tab=recall'), compact: () => { void send('/compact') } } : undefined} servedModel={lastServedModel(turns)} autoChain={autoChain} askBeforeModelSwitch={sessionId ? askBeforeSwitch(sessionId) : undefined}
          handsFree={{ confirmationPhrases: voiceCfg.confirmation_phrases, exitPhrases: voiceCfg.exit_phrases, speaking: speakingTurn !== null, muteWhileSpeaking: voiceCfg.duplex_mute_enabled }}
          onHandsFreeSubmit={(t) => void send(t, { inputOrigin: 'voice' })}
          screenShare={{ available: screenShare.available, sharing: screenShare.sharing, disabledReason: screenShare.disabledReason, onToggle: screenShare.toggle }}
          hostPills={(
            <>
              <TaskModePill value={selection.taskMode ?? 'agent'} onChoose={chooseTaskMode} />
              {/* In an app's conversation the app's grant decides every turn and a choice here
                  would be overwritten by it, so the permission mode shows that grant and is not
                  hers to move (the notice above the composer says why too). */}
              <ExecutionBoundary approval={approval} onApproval={chooseApproval} openSignal={boundaryAsks}
                lockedReason={startedBy ? `${startedBy.name}'s permissions decide this chat, not yours` : undefined}
                memoryMode={memoryMode} onMemoryMode={!sessionId ? setMemoryMode : undefined}
                folder={folder} onChangeFolder={started && sessionRef.current ? onChangeFolder : undefined}
                reading={boundaryReading} />
            </>
          )} />
      </div>
      {/* CREATE-TIME session setup — the project binding, frozen once the chat starts, so it
          is NOT a composer control (the composer's controls stay live for the whole chat); it
          lives just below the new-chat composer and disappears once started. Keyed off
          !sessionId (a genuinely NEW chat) rather than !started, so it doesn't flash while an
          existing session's history is still loading (turns empty → started false, but
          sessionId set). Project scoping opens via /project. The memory mode, frozen the same
          way, is chosen in the Execution Boundary control. */}
      {!sessionId && (
        <div className="mt-2.5 flex flex-wrap items-center justify-center gap-x-3 gap-y-2">
          <ProjectPicker value={projectId} onChange={setProjectId}
            emptyLabel="No project" emptyHint="" openSignal={openProjectSignal} />
        </div>
      )}
    </div>
  )
}

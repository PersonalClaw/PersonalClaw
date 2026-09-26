import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Archive, ArrowLeft, Download, PanelRight, Send, Users } from 'lucide-react'
import { api, hasApiCode, isTransientFailure, type RoomListenPolicy } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { notify } from '../../app/appSdk'
import { confirm } from '../../ui/dialog'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import { EmptyState, InlineLoadError, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { Markdown } from '../../ui/Markdown'
import { PageTitle } from '../../ui/PageTitle'
import { SidePanel } from '../../ui/SidePanel'
import { StatusPill } from '../../ui/StatusPill'
import { TextArea } from '../../ui/forms'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { MessageAssistant } from '../../ui/chat/MessageAssistant'
import { MessageUser } from '../../ui/chat/MessageUser'
import { NumberField } from '../../ui/forms'
import { fvs } from '../../design/fontWeight'
import { RoomPauseCard } from './RoomPauseCard'
import { RoomMembersPanel } from './RoomMembersPanel'
import {
  ROOM_ICON,
  roomLines,
  roomState,
  roomStateMeta,
  roundBudgetLabel,
  type RoomLine,
} from './roomMeta'
import type { RouteProps } from '../../app/useQueryState'

/** The DOM id of the room composer. A constant because the budget pause card's primary action
 *  focuses it, which is the shipped pattern (`settings/SecretsPanel` does exactly this): a budget
 *  pause has no resume endpoint — ANY human message resets the budget — so "resume" is "write
 *  something", and the honest affordance is to put the cursor where the user has to type. */
const COMPOSER_ID = 'room-composer'

/** How often a room re-reads itself while a round is running.
 *
 *  A room answers in the BACKGROUND: `POST .../messages` returns as soon as the human's line is
 *  durable, and each member's reply lands on the transcript seconds later. The surface polls
 *  while — and ONLY while — the ROOM says a round is running (`room.round_running`, which the
 *  backend reads off the live task). That is the whole mechanism: an idle room costs nothing, the
 *  poll stops when the round ends, and it survives a reload because it is the room's state, not
 *  this tab's. The room emits no push frame to follow instead; a page-scoped feed like this one
 *  would be its own per-room SSE stream under the transport doctrine, not a WS frame. */
const ACTIVE_POLL_MS = 2500

/** One room (`AGENT-ROOMS` C9 / `AR-8`) — the attributed transcript, the pause card, and
 *  per-member status.
 *
 *  ── WHY THIS IS NOT `ChatSession`, AND WHY THAT IS NOT A SECOND CHAT UI ──
 *
 *  A room is not a session. Its transcript lives at `rooms/<id>/transcript.jsonl` behind
 *  `/api/rooms/{id}`, its messages carry a `speaker` and no tool segments, and posting goes to
 *  `/api/rooms/{id}/messages` — a route that returns a speaker QUEUE rather than a stream.
 *  `ChatSession` is 2,400 lines of session machinery (streaming WS, tool projections, rewind,
 *  variants, the session map) none of which a room has. Driving it from a different data source
 *  would be a fork of it, which is the second chat UI the clause forbids.
 *
 *  What "not a second chat UI" actually asks for is that a room does not RE-DRAW chat, and it
 *  does not: the human's words render through `MessageUser`, a member's through
 *  `MessageAssistant` + `Markdown`, and the surface is a `WorkbenchLayout` + `TopBar` +
 *  `SidePanel` like every other workbench page. Nothing visual is minted here.
 *
 *  ── ATTRIBUTION IS THE PRODUCT, SO IT IS STRUCTURAL ──
 *
 *  Every line is rendered from `roomMeta.roomLines`, which resolves each message into exactly
 *  one of three kinds — the human, a member, or the ROOM itself — and there is **no merging**.
 *  The session transcript collapses consecutive assistant messages into one turn; in a room
 *  consecutive assistant messages are usually different members, so that collapse would print
 *  one member's words under another's name. AGENT-ROOMS names it as the likeliest room-specific
 *  defect. A room simply has no merge, so it cannot have that bug, and `roomMeta.test.ts` pins
 *  the shape.
 */
export function RoomView({ roomId, navigate, setQuery }: {
  roomId: string
  navigate: (path: string, opts?: { replace?: boolean }) => void
  setQuery: RouteProps['setQuery']
}) {
  const { data, error, refresh, revalidating } = useQuery(
    `rooms:${roomId}`,
    () => api.room(roomId),
    { persist: false },
  )
  // Every configured binding, for the member picker. A separate read because it is CONFIG and
  // the room is LIVE — folding it into the room poll would re-fetch the agent list every 2.5s.
  //
  // 🪤 `error` is BOUND, not dropped. `agents` is only ever `undefined` or a list, and the picker
  // reads `undefined` as "still loading" — so a rejected read left it saying `Loading…` forever,
  // a failure wearing a loading state. Binding the rejection is what lets the picker tell the two
  // apart. `ui/loadErrorState.test.tsx`'s `UNBOUND_ERROR_BUDGET` is the rail over this class.
  const { data: agentData, error: agentsError, refresh: refreshAgents } =
    useQuery('agents:list', () => api.agents())

  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [busy, setBusy] = useState(false)
  // Which member's removal is in flight. A name, not a boolean: the remove control has to say
  // "working" about itself rather than dimming every peer — see `RoomMembersPanel`'s `removing`.
  const [removing, setRemoving] = useState('')
  // The last write that failed, and — only when running it AGAIN could succeed — how to. The banner
  // used to offer a Retry that re-read the room for every failure: it never re-ran the write, and
  // for a refusal (`room_member_limit` at 8/8) no retry of anything could succeed.
  const [actionError, setActionError] = useState<{ error: unknown; retry?: () => void } | null>(null)
  const [membersOpen, setMembersOpen] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

  const room = data?.room
  const lines = useMemo(() => (data ? roomLines(data) : []), [data])

  // 🔴 WHO THE ROOM OWES, AND WHETHER A ROUND IS ANSWERING THEM — both read off the room.
  //
  // This used to be derived here: the queue the last POST returned, minus every member that had
  // EVER spoken. So once every member had spoken once, the next message's queue subtracted to
  // nothing, `roundRunning` went false, the poll never started, and new replies stayed invisible
  // until a reload — measured, the first conversation refreshed 141 times and the third twice.
  // The queue also lived only in this tab, so a reload lost it and a member's `@`-summons never
  // reached it. The backend owns both answers now: `owed` is its queue (the open turn first) and
  // `round_running` is whether its round task is alive.
  const owed = room?.owed ?? []
  const roundRunning = !!room && room.round_running && !room.archived

  // Poll while a round is running. A plain interval rather than `useVisiblePoll`, because this
  // one must also STOP when the round ends — the hook's `null` off-switch is per-render, and
  // pairing it with the room's own running flag is the whole mechanism.
  useEffect(() => {
    if (!roundRunning) return
    const t = window.setInterval(refresh, ACTIVE_POLL_MS)
    return () => window.clearInterval(t)
  }, [roundRunning, refresh])

  // Keep the newest line in view as replies land — the follow-the-stream scroll, in exactly the
  // form `ChatPage` uses for the same job (`{ block: 'end' }`, instant).
  //
  // 🪤 NOT `behavior: 'smooth'`. `block: 'end'` + `smooth` together are the Session Map's
  // return-to-newest GESTURE, and `SessionMapReturnLatest.test.tsx` derives that control's
  // uniqueness from exactly that option pair specifically so a duplicate minted under another
  // name is caught. Following a stream is a different concern and is instant, which is also the
  // right behaviour: a smooth animation per arriving line would fight the next one.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [lines.length])

  const send = useCallback(async () => {
    const content = draft.trim()
    if (!content || sending) return
    setSending(true)
    setActionError(null)
    try {
      await api.postRoomMessage(roomId, content)
      setDraft('')
      // Re-read the room: it now owes this message's turns (behind any it already owed) and says
      // a round is running them, which is what starts the poll above.
      refresh()
    } catch (e) {
      // No banner Retry: the draft is kept, so Send right below IS the retry, and it sends what
      // the composer holds now rather than a copy captured when this failed.
      setActionError({ error: e })
    } finally {
      setSending(false)
    }
  }, [draft, sending, roomId, refresh])

  const act = useCallback(async function act(run: () => Promise<unknown>, what: string) {
    setBusy(true)
    setActionError(null)
    try {
      await run()
      refresh()
    } catch (e) {
      notify(`Couldn't ${what}: ${String((e as Error)?.message || e)}`, 'error')
      setActionError({ error: e, retry: isTransientFailure(e) ? () => void act(run, what) : undefined })
      // A refusal can mean this view of the room is stale (it filled up in another tab), so it is
      // re-read: the panel then shows the room as it is — at its ceiling, archived — not as it was.
      refresh()
    } finally {
      setBusy(false)
    }
  }, [refresh])

  const archive = useCallback(async () => {
    if (!room) return
    if (!(await confirm({
      title: `Archive “${room.title}”?`,
      body: 'The room stops accepting messages and leaves your list. Its transcript stays on disk and stays exportable — archiving is not deletion.',
      confirmLabel: 'Archive the room',
    }))) return
    await act(() => api.archiveRoom(roomId), 'archive the room')
  }, [room, roomId, act])

  // Finish an interrupted round: the members it still owes answer the message already on the
  // transcript. Through `act`, so a transient failure offers a Retry that re-runs THIS call; with
  // its own in-flight flag, so it is the Continue button that says "working", not every control.
  const [continuing, setContinuing] = useState(false)
  const continueRound = useCallback(() => {
    setContinuing(true)
    void act(() => api.continueRoom(roomId), 'continue the round').finally(() => setContinuing(false))
  }, [roomId, act])

  // ── the load states, as ONE ladder ──
  // `rooms_disabled` first, and it is not a failure: the feature ships off and the backend refuses
  // the READS too, so a 403 here means "switched off" and a LoadError would tell the user their
  // room is broken. `room_not_found` second, for the same reason in the other direction — a room
  // that is gone is an empty state, not an error to retry.
  if (data === undefined && error && hasApiCode(error, 'rooms_disabled')) {
    return <RoomsOffState navigate={navigate} />
  }
  if (data === undefined || !room) {
    return (
      <WorkbenchLayout topBar={<RoomTopBar title="Room" navigate={navigate} />}>
        <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
          {error && hasApiCode(error, 'room_not_found') ? (
            <EmptyState
              icon={ROOM_ICON}
              title="This room no longer exists"
              hint="It may have been removed from disk. Your other rooms are unaffected."
              // "See your rooms" rather than repeating the header's "Back to rooms": two controls
              // with the SAME accessible name doing the same thing on one screen is the duplicate-
              // name defect this repo keeps measuring out of the AX tree.
              action={{ label: 'See your rooms', onClick: () => navigate('chat/history?origin=room'), icon: ArrowLeft }} />
          ) : error ? (
            <LoadError what="room" error={error} onRetry={refresh} />
          ) : (
            <ListSkeleton rows={4} what="room" />
          )}
        </div>
      </WorkbenchLayout>
    )
  }

  const state = roomState(room)
  const stateMeta = roomStateMeta(state)
  const canSpeak = !room.archived
  return (
    <WorkbenchLayout
      scroll={false}
      topBar={
        <TopBar
          keepCornerPadding
          left={
            <div className="flex min-w-0 items-center gap-s">
              {/* The back label collapses on a phone. Measured at 430px: "Rooms" plus the state
                  pill left the room's own title truncated to "S…" — a heading that names nothing.
                  The button keeps its accessible name either way, so nothing is lost. */}
              <Button size="xs" variant="ghost" ariaLabel="Back to rooms" onClick={() => navigate('chat/history?origin=room')}>
                <ArrowLeft size={14} aria-hidden /> <span className="hidden sm:inline">Rooms</span>
              </Button>
              <PageTitle className="truncate">{room.title}</PageTitle>
              {state !== 'active' && <StatusPill tone={stateMeta.tone}>{stateMeta.label}</StatusPill>}
            </div>
          }
          right={
            <HeaderActions>
              <HeaderControl
                icon={PanelRight}
                label="Members"
                active={membersOpen}
                ariaExpanded={membersOpen}
                priority="primary"
                onClick={() => setMembersOpen((o) => !o)} />
              <HeaderControl
                icon={Download}
                label="Export transcript"
                onClick={() => { window.location.href = api.roomExportUrl(room.id, 'md') }} />
              <HeaderControl
                icon={Archive}
                label="Archive"
                danger
                priority="low"
                disabled={room.archived || busy}
                onClick={archive} />
            </HeaderActions>
          } />
      }
      panel={membersOpen && (
        <SidePanel
          title="Members"
          icon={<Users size={18} className="text-primary" />}
          storeKey="room-members-w"
          fillHeight
          urlKey={{ key: 'members', setQuery }}
          onClose={() => setMembersOpen(false)}>
          <div className="flex flex-col gap-2xl p-s">
            <RoomMembersPanel
              detail={data}
              agents={agentData?.agents}
              agentsError={agentsError}
              onRetryAgents={refreshAgents}
              busy={busy}
              onAdd={(body: { name: string; role_blurb: string; listen_policy: RoomListenPolicy }) =>
                void act(() => api.addRoomMember(room.id, body), 'add the member')}
              removing={removing}
              onRemove={(name) => {
                setRemoving(name)
                void act(() => api.removeRoomMember(room.id, name), 'remove the member')
                  .finally(() => setRemoving(''))
              }} />
            <RoomBudgetControl
              roomId={room.id}
              declared={room.round_budget}
              effective={room.effective_round_budget}
              max={room.max_round_budget}
              onSet={(n) => void act(() => api.setRoomRoundBudget(room.id, n), "set this room's budget")} />
          </div>
        </SidePanel>
      )}>
      <div className="flex min-h-0 flex-1 flex-col">
        {/* THE TRANSCRIPT'S OWN SCROLLER. `min-h-0` on every ancestor down to here is what makes
            it scroll instead of growing the page — the defect the onboarding page shipped, and
            the one no `web/` gate can catch because jsdom has no layout. Deliberately NOT
            carrying `data-transcript-scroll`: that attribute is the session-map e2e's handle on
            the CHAT scroller, and a second element answering to it would make that selector
            ambiguous. */}
        <div
          data-room-transcript-scroll
          tabIndex={0}
          role="log"
          aria-label={`${room.title} transcript`}
          // `min-h-0` is load-bearing and is the one thing no `web/` gate can verify: jsdom has no
          // layout, so an unscrollable pane passes every rail (the onboarding page shipped exactly
          // that). In a column flex a `flex-1` child defaults to `min-height: auto` and GROWS to fit
          // its content instead of scrolling, which would push the composer off the bottom of the
          // page. Spelled out here rather than relied on from an ancestor.
          className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex flex-col gap-xl px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
            {lines.length === 0 ? (
              <EmptyState
                icon={ROOM_ICON}
                title={room.members.length === 0 ? 'Add two members and start the argument' : 'Say something to start'}
                hint={room.members.length === 0
                  ? 'A room needs members before anyone can answer. Each one is a configured agent with its own role, its own provider session and its own tool reach.'
                  : 'Write a message. Everyone listening answers in turn, and you can call on one member directly by writing @its-name.'}
                action={room.members.length === 0
                  ? { label: 'Open members', onClick: () => setMembersOpen(true), icon: Users }
                  : undefined} />
            ) : (
              lines.map((line, i) => <RoomTranscriptLine key={`${line.ts}-${i}`} line={line} />)
            )}
            {(state === 'paused' || state === 'interrupted') && (
              <RoomPauseCard
                room={room}
                interrupted={state === 'interrupted'}
                continuing={continuing}
                onContinue={continueRound}
                onReply={() => document.getElementById(COMPOSER_ID)?.focus()}
                onArchive={archive} />
            )}
            <div ref={endRef} aria-hidden />
          </div>
        </div>

        {/* The one live region for the round. `role="status"` and polite: a member answering is
            progress, not news that changes what the screen means. Always mounted, empty when
            idle, so the announcement is a CONTENT change — an `aria-label` on a live region is a
            name, not an announcement. Only while a round RUNS: a paused or interrupted room is
            not answering, and its card already names who it owes. */}
        <div role="status" aria-live="polite" className="sr-only">
          {roundRunning && owed.length > 0
            ? `${owed.length} member${owed.length === 1 ? '' : 's'} still to answer: ${owed.join(', ')}`
            : ''}
        </div>

        <div className="border-t border-outline-variant/40">
          <div className="mx-auto flex flex-col gap-s px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
            {actionError !== null && (
              <InlineLoadError what="that" error={actionError.error} onRetry={actionError.retry} />
            )}
            <div className="flex flex-wrap items-center justify-between gap-s">
              <Eyebrow as="span">{roundBudgetLabel(room)} since your last message</Eyebrow>
              {roundRunning && owed.length > 0 && (
                <span data-type="caption" className="text-on-surface-low">
                  Answering: {owed.join(' → ')}
                </span>
              )}
              {revalidating && !roundRunning && (
                <span data-type="caption" className="text-on-surface-low">Re-reading the room…</span>
              )}
            </div>
            {canSpeak ? (
              <>
                <TextArea
                  id={COMPOSER_ID}
                  value={draft}
                  onChange={setDraft}
                  rows={3}
                  ariaLabel={`Message ${room.title}`}
                  placeholder={room.members.length === 0
                    ? 'Add a member first — nobody is in this room yet.'
                    : 'Write to the room. @name a member to call on it directly.'}
                  disabled={sending}
                  disabledReason="Sending your message"
                  onKeyDown={(e) => {
                    // ⌘/Ctrl+Enter sends; plain Enter is a newline. A room message is a
                    // deliberation prompt rather than a chat line, so the multi-line default is
                    // the right one — and it needs no `send_on_enter` config read to be correct.
                    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                      e.preventDefault()
                      void send()
                    }
                  }} />
                <div className="flex items-center gap-s">
                  <Button
                    size="sm"
                    loading={sending}
                    loadingLabel="Sending your message"
                    disabled={!draft.trim()}
                    disabledReason="Write something first"
                    title="Send (⌘/Ctrl + Enter)"
                    onClick={() => void send()}>
                    <Send size={14} aria-hidden /> Send
                  </Button>
                  <span data-type="caption" className="text-on-surface-low">
                    Your message resets the round budget and lets any parked turns run.
                  </span>
                </div>
              </>
            ) : (
              <p data-type="body-s" className="text-on-surface-var" style={fvs(400)}>
                This room is archived. Its transcript is still readable and exportable, but
                nobody can speak in it.
              </p>
            )}
          </div>
        </div>
      </div>
    </WorkbenchLayout>
  )
}

/** One transcript line, attributed.
 *
 *  Three shapes for three kinds, and the third is why this is not a boolean. A `room` line — a
 *  tool refusal today — carries the refused MEMBER's name as its speaker, because that is the
 *  attribution a reader needs; rendering it as that member's message would put the room's words
 *  in that member's mouth. So it renders as a room note that NAMES the member it is about,
 *  visually distinct from both a human bubble and a member's contribution. */
function RoomTranscriptLine({ line }: { line: RoomLine }) {
  if (line.kind === 'human') {
    return <MessageUser>{line.content}</MessageUser>
  }
  if (line.kind === 'room') {
    return (
      <div className="rounded-lg bg-surface-container px-m py-s">
        <Eyebrow as="span">Room note{line.speaker ? ` · ${line.speaker}` : ''}</Eyebrow>
        <p data-type="body-s" className="mt-0.5 text-on-surface-var" style={fvs(400)}>{line.content}</p>
      </div>
    )
  }
  return (
    <div>
      {/* The attribution sits ABOVE the words, not below them: a reader has to know whose
          position this is before reading it, which is the whole difference between a room and a
          chat. The blurb rides along because a member's position is only legible next to the
          role it argues from. */}
      <div className="mb-xs flex flex-wrap items-baseline gap-s">
        <span data-type="title-m" className="text-on-surface" style={fvs(550)}>{line.label}</span>
        {line.blurb && <span data-type="body-s" className="text-on-surface-low">{line.blurb}</span>}
      </div>
      <MessageAssistant>
        <Markdown>{line.content}</Markdown>
      </MessageAssistant>
    </div>
  )
}

/** This room's OWN round budget, or the configured default it inherits.
 *
 *  The write path this control exists for shipped LATE: `Room.round_budget` was readable on the
 *  wire and settable nowhere, which is worse than an absent field because publishing it implies
 *  it is settable. The stepper's bounds come from `max_round_budget` on the wire rather than a
 *  restated literal — a control whose range disagrees with the save path's is an offer the save
 *  path refuses.
 *
 *  0 is a real value and is spelled out rather than hidden, because "inherit" is the default a
 *  user needs to be able to get BACK to. */
function RoomBudgetControl({ roomId, declared, effective, max, onSet }: {
  roomId: string
  declared: number
  effective: number
  max: number
  onSet: (n: number) => void
}) {
  const fieldId = `room-budget-${roomId}`
  return (
    <div>
      <Eyebrow as="h3">Round budget</Eyebrow>
      <p data-type="body-s" className="mt-xs text-on-surface-var" style={fvs(400)}>
        How many turns the members may take among themselves before the room pauses and asks
        you. {declared === 0
          ? `This room inherits the configured default of ${effective}.`
          : `This room sets its own: ${declared}.`}
      </p>
      <div className="mt-s flex items-center gap-s">
        <label htmlFor={fieldId} data-type="body-s" className="text-on-surface-var">
          Exchanges
        </label>
        <NumberField
          value={declared}
          min={0}
          max={max}
          step={1}
          width="w-20"
          ariaLabel="Round budget for this room, 0 to inherit the configured default"
          onChange={onSet} />
        {/* Deliberately UNGATED. Setting the budget back to 0 is idempotent on the server, so a
            double click writes the same value twice and changes nothing — and a `disabled={busy}`
            here would dim a control the user can safely press while some other roster write is in
            flight, which is a false "unavailable". */}
        {declared !== 0 && (
          <Button size="xs" variant="ghost" onClick={() => onSet(0)}>
            Use the default
          </Button>
        )}
      </div>
    </div>
  )
}

/** Agent Rooms is switched off. Not an error — the feature ships disabled, and every route
 *  refuses including the reads, so this is the one honest reading of a 403 here. */
function RoomsOffState({ navigate }: { navigate: (path: string) => void }) {
  return (
    <WorkbenchLayout topBar={<RoomTopBar title="Rooms" navigate={navigate} />}>
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        <EmptyState
          icon={ROOM_ICON}
          title="Agent Rooms is switched off"
          hint="A room is a standing conversation where several of your agents and you deliberate together. Turn it on in Settings › Chat to create one."
          action={{ label: 'Open chat settings', onClick: () => navigate('settings/chat') }} />
      </div>
    </WorkbenchLayout>
  )
}

function RoomTopBar({ title, navigate }: { title: string; navigate: (path: string) => void }) {
  return (
    <TopBar
      keepCornerPadding
      left={
        <div className="flex min-w-0 items-center gap-s">
          <Button size="xs" variant="ghost" ariaLabel="Back to rooms" onClick={() => navigate('chat/history?origin=room')}>
            <ArrowLeft size={14} aria-hidden /> <span className="hidden sm:inline">Rooms</span>
          </Button>
          <PageTitle className="truncate">{title}</PageTitle>
        </div>
      } />
  )
}

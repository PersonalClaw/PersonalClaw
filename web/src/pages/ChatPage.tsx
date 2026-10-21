import { useCallback, useEffect, useRef, useState } from 'react'

import { motion, AnimatePresence } from 'framer-motion'
import { Loader2 } from 'lucide-react'
import { type RoutingSuggestion } from './chat/RoutingChip'
import { DotGlow } from '../ui/DotGlow'
import { PaneHost } from '../ui/PaneHost'
import { BesideRegistry, OpenBesideProvider, useBesideBlock } from '../ui/OpenBeside'
import { LoadError } from '../ui/ListScaffold'
import { RoomView } from './chat/RoomView'
import { useIdentity } from '../app/identity'
import { spring } from '../design/motion'
import { useChatVoice } from './chat/useChatVoice'
import { useChatComposer } from './chat/composer/useChatComposer'
import { taskModeFromQuery, useTaskModeInUrl } from './chat/composer/taskModeInUrl'
import { ChatComposer } from './chat/composer/ChatComposer'
import { ComposerFold, FOLD_ROOM } from './chat/composer/ComposerFold'
import { useChatStream } from './chat/useChatStream'
import { useTranscript } from './chat/transcript/useTranscript'
import { Transcript } from './chat/transcript/Transcript'
import { MessagesSkeleton } from './chat/transcript/MessagesSkeleton'
import { SessionMapReturnLatest, scrollToLatest } from './chat/SessionMapReturnLatest'
import { ChatHistoryPage } from './chat/history/ChatHistoryPage'
import { useChatPanes } from './chat/panes/useChatPanes'
import { ChatPanes, ToolResultModal } from './chat/panes/ChatPanes'
import { besidePane } from './chat/beside/blockBeside'
import { useChatHeader } from './chat/header/useChatHeader'
import { ChatHeader } from './chat/header/ChatHeader'
import { ChatHero } from './chat/hero/ChatHero'
import { MissingChat } from './chat/MissingChat'
import { type RouteProps } from '../app/useQueryState'
import { useViewModes } from '../app/viewModes'

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
  } = stream
  const started = turns.length > 0
  // The composer: the draft and what it carries, the selection, Stop, and `send`, which every region
  // sends through (`chat/composer/useChatComposer`).
  const composer = useChatComposer({
    sessionId, navigate, seed, initialProjectId, initialAgent, initialTaskMode: taskModeFromQuery(query) ?? 'agent',
    pendingRouting, setRoutingSuggestion, setLiveRun, started, stream,
    followNewTurn: () => followNewTurn(), oweSpokenReply: (sid) => oweSpokenReply(sid),
  })
  const { adoptDetail, applyTemplate, composerFocused, composerRef, focusComposerSoon, input, memoryMode, notice, quoteToComposer, selection, send, setInput, setSelection } = composer
  // The task mode in the address (`?mode=`), for what is shown only (`chat/composer/taskModeInUrl`).
  // A new thread the address moves to another mode is set up as one a link opens: in that mode,
  // with approval back to Normal.
  useTaskModeInUrl({
    query, setQuery, sessionId, taskMode: selection.taskMode, postureKnown: composer.postureKnown,
    followMode: (taskMode) => setSelection((s) => ({ ...s, taskMode, approval: 'normal' })),
  })
  // The header's title actions, its context line's project and its cluster's actions
  // (`chat/header/useChatHeader`).
  const header = useChatHeader({ stream, composer })
  // Focus mode and reading mode, the shell's (`app/viewModes`): focus mode closes the docked panes
  // and opens only the one asked for; reading mode folds the composer once there is something to read.
  const { focus, reading, setReading } = useViewModes()
  // The panels docked beside the chat and a tool's full result (`chat/panes/useChatPanes`).
  const panes = useChatPanes({ query, setQuery, stream, focus })
  // The transcript's two callbacks into the panes, each one stable for the chat's life: a reply's
  // text is memoized, and a new callback would draw every block in it again, which drops the focus
  // on a block's controls and highlights its code once more.
  const panesRef = useRef(panes)
  panesRef.current = panes
  const setOpenFile = useCallback((path: string | null) => panesRef.current.setOpenFile(path), [])
  // A reply's block opened beside the reading (`chat/beside/blockBeside`): the blocks on screen
  // register under their addresses here, and Open beside puts one in the address.
  const openBeside = useCallback((address: string) => panesRef.current.setBeside(address), [])
  const [besideRegistry] = useState(() => new BesideRegistry())
  const besideBlock = useBesideBlock(besideRegistry, panes.beside)
  // Where a stacked block goes on a compact window: between the thread and the composer, so the
  // composer stays last and the thread and the block share the room above it.
  const [stackSlot, setStackSlot] = useState<HTMLDivElement | null>(null)
  // The transcript's view and its turns' actions (`chat/transcript/useTranscript`).
  const transcript = useTranscript({
    sessionId, query, setQuery, navigate, stream, started, notice, memoryMode, setSelection,
    agent: selection.agent, send,
  })
  const { followNewTurn, scrolledUp, endRef, isMobile, mapOpen } = transcript
  // A chat with nothing to read yet has nothing to fold: its composer is the page.
  const folded = reading && started && !loadingHistory
  useEffect(() => { if (reading && !started) setReading(false) }, [reading, started, setReading])

  // Read-aloud: Speak, "Speak replies aloud" and the audio a reading streams (`chat/useChatVoice`).
  const { voiceCfg, speakingTurn, speak, replyFinished, oweSpokenReply, enqueueAudio, readingRef } = useChatVoice({ sessionRef, turns, notice })

  const stage = <ChatComposer composer={composer} stream={stream} sessionId={sessionId} started={started} onOpenFile={setOpenFile}
    voiceCfg={voiceCfg} speakingTurn={speakingTurn} onBriefAgent={header.briefAgent} onChangeFolder={header.setWorkspaceDir} />

  if (missing) return <MissingChat draft={input} navigate={navigate} />
  // A failed read with nothing painted: say it failed, and let the user retry. (A transcript
  // painted from the fresh cache stays on screen — a failed REVALIDATION of it is not news.)
  if (loadFailure && !started) return (
    <div className="flex h-full flex-col items-center justify-center overflow-hidden px-l">
      <LoadError what="chat" error={loadFailure} onRetry={() => setLoadAttempt((n) => n + 1)} />
    </div>
  )

  // The block the address opens beside the reading, once the chat's turns have loaded: the split,
  // in the chat's pane host, which remembers its width and stacks the block under the thread where
  // the two do not fit side by side.
  const beside = besidePane({
    address: panes.beside, block: besideBlock, ready: started && !loadingHistory, onClose: () => panes.setBeside(null),
  })

  return (
    <OpenBesideProvider open={openBeside} registry={besideRegistry}>
    <div className="relative flex h-full flex-col overflow-hidden">
      <ChatHeader header={header} stream={stream} composer={composer} panes={panes} transcript={transcript}
        started={started} navigate={navigate} />

      {/* body row: chat column + (optional) right-docked panels that PUSH the chat
          narrower (flex siblings) — the FILE peek and the ACTIVITY rail. Both use
          the shared SidePanel primitive so they match every other page. */}
      <div className="relative flex min-h-0 flex-1">
        {/* 🪤 INERT WHILE THE MOBILE SESSION MAP COVERS IT. The drawer below is a flex SIBLING
            whose fit-width exceeds a phone viewport, so when it opens this column, and a block open
            beside it in the pane host, is squeezed to
            ZERO width — measured at 390px: the composer's `.cm-scroller` ends up `clientWidth: 0`
            with `scrollWidth: 19`. That is not merely invisible, it is a keyboard trap in waiting:
            a tab-order stop and (per axe's `getScroll`, buffer 13) a `scrollable-region-focusable`
            [serious] scroll region that no one can reach or see. `inert` is the whole fix — it
            drops the subtree from focus AND from assistive tech, which is exactly what "the drawer
            covers the transcript at this width" already claims. NOT `aria-hidden`, which would
            leave the composer focusable and trade one serious violation for `aria-hidden-focus`.
            The header control that closes the drawer lives ABOVE this row, so the user is never
            shut out. */}
        <div className="flex min-h-0 min-w-0 flex-1" inert={mapOpen && isMobile && started}>
        <PaneHost surface="chat" pane={beside} stackInto={stackSlot}>
        <div className="relative flex h-full min-w-0 flex-col"
          style={folded ? { '--composer-fold-room': FOLD_ROOM } as React.CSSProperties : undefined}>
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
            <ChatHero name={name} stage={stage} onPickStarter={applyTemplate} onPickSuggestion={(s) => setInput(s)} />
          ) : (
            <>
              <Transcript transcript={transcript} stream={stream} sessionId={sessionId} memoryMode={memoryMode} navigate={navigate}
                onOpenFile={setOpenFile} onQuote={quoteToComposer} onPickFollowup={(t) => { setInput(t); focusComposerSoon() }}
                send={send} speak={speak} speakingTurn={speakingTurn} />
              <div ref={setStackSlot} className="contents" />
              {/* Folded (reading mode), this region floats over the end of the transcript, which then
                  takes the column's whole height; its controls are the only part that takes a click. */}
              <div className={folded ? 'pointer-events-none absolute inset-x-0 bottom-0 z-20 px-l pb-l pt-8' : 'relative shrink-0 px-l pb-l'}>
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
                <span className="pointer-events-auto">
                  <SessionMapReturnLatest
                    scrolledUp={scrolledUp}
                    onReturnToLatest={() => scrollToLatest(endRef.current)}
                  />
                </span>
                <ComposerFold folded={folded} onUnfold={() => setReading(false)} composerRef={composerRef} draft={input}
                  onType={(key) => setInput((prev) => prev + key)}>
                  {stage}
                </ComposerFold>
              </div>
            </>
          )}
        </div>
        </PaneHost>
        </div>

        <ChatPanes panes={panes} stream={stream} transcript={transcript} started={started} setQuery={setQuery}
          navigate={navigate} send={send} />
      </div>
      <ToolResultModal panes={panes} />
    </div>
    </OpenBesideProvider>
  )
}

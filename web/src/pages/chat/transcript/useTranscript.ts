import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type ApprovalMode } from '../../../lib/api'
import { failureSentence, reportActionFailure } from '../../../app/reportingWrite'
import { notify } from '../../../app/appSdk'
import { confirm } from '../../../ui/dialog'
import { useQueryParam, type RouteProps } from '../../../app/useQueryState'
import { useIsMobile } from '../../../app/useIsMobile'
import type { useComposerNotice } from '../../../ui/composer/ComposerNotice'
import type { ComposerValue } from '../../../ui/composer/types'
import { liveWorkflowCards } from '../WorkflowProgressCard'
import { liveLoopCards } from '../SdlcProgressCard'
import { sessionMapEntries } from '../sessionMap'
import { askToRepeat } from '../repeatedSteps'
import { branchIndexOf } from '../branchLineage'
import { editReplacesLaterTurns, replacedTurnsAreKept } from '../editReplaces'
import { asSent } from '../pasteBlocks'
import { applyQuestionResolved } from '../questionFrames'
import { turnOutcomeOf } from '../turnOutcome'
import type { QuestionReply } from '../QuestionCard'
import { type ChatTurn, userTurn, turnText, markCoordOf } from '../chatTypes'
import type { ChatStream } from '../useChatStream'
import type { ApproveAction } from './AssistantSegments'

// ── The transcript's controller ───────────────────────────────────────────────────────────────
//
// What the chat's transcript region keeps and does: where the view is (following a turn, scrolled
// up, the turn nodes the find bar and the Session Map jump to), the find bar and its deep link,
// the Session Map's open state and its jumps, and the actions a turn offers (answer a card,
// approve, edit and resend, rewind, regenerate, branch, switch the answer, switch to Agent and
// run it). The turns themselves are the stream's (`useChatStream`); `Transcript.tsx` draws them.

export function useTranscript({ sessionId, query, setQuery, navigate, stream, started, notice, memoryMode, setSelection, agent, send }: {
  sessionId: string | null
  query: Record<string, string>
  setQuery: RouteProps['setQuery']
  navigate: (p: string, opts?: { replace?: boolean }) => void
  stream: Pick<ChatStream, 'sessionRef' | 'turns' | 'setTurns' | 'streaming' | 'streamingRef' | 'markStreaming' | 'dropTextRun' | 'readSnapshot' | 'adoptSnapshot'>
  /** The chat has turns on screen. */
  started: boolean
  /** The composer's notice line, where a failed turn action is said. */
  notice: ReturnType<typeof useComposerNotice>
  memoryMode: string
  setSelection: React.Dispatch<React.SetStateAction<ComposerValue>>
  /** The agent the chat runs on, which a quoted passage of its is attributed to. */
  agent: string
  send: (text: string) => Promise<void>
}) {
  const { sessionRef, turns, setTurns, streaming, streamingRef, markStreaming, dropTextRun, readSnapshot, adoptSnapshot } = stream
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

  // ── SESSION MAP ────────────────────────────────────────────────────────────────────────────
  // The in-session index: a fixed rail in the transcript's left gutter on a pointer device, a
  // tappable SidePanel drawer on the mobile form. Open/closed is URL state so it is
  // deep-linkable and survives a reload, exactly like `?activity`.
  //
  // 🪤 NOT `useQueryFlag`, and the reason is the DEFAULT. The map is always available, so
  // its default is OPEN — and a present-or-absent flag can only record the non-default, i.e. it
  // could never express "this user closed it". `useQueryParam` with a '1' default is the same
  // one-key URL contract in both directions: open is the clean URL, `?map=0` records a
  // deliberate close and restores it on refresh. (This is `useQueryFlag`'s two-way form,
  // not a second mechanism — same module, same `setQuery`.)
  //
  // Which FORM the map takes. `useIsMobile` (the app's ≤768px breakpoint) rather than a
  // `(pointer: coarse)` query of its own: the shell already switches its nav rail to a drawer on
  // exactly this signal, and a second "is this the touch form?" mechanism would be free to
  // disagree with the first. It is also the discriminator a browser gate can drive — a mobile
  // VIEWPORT is what `web/e2e/a11y.spec.ts` can set; pointer type is not.
  const isMobile = useIsMobile()
  // 🪤 THE DEFAULT IS PER-FORM, AND A SINGLE `'1'` DEFAULT WAS A REAL DEFECT rather than a
  // preference. The "always available" is what the gutter rail delivers for free — it occupies
  // the empty left column, so open-by-default costs the transcript nothing. The
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
    // It resolves `true` once the decision landed, so the card sends nothing more for it while it
    // is still on the page (`ApprovalChoice.onClick`), and a failure leaves the card answerable.
    return api.approve(s, action, id)
      .then(() => {
        if (raised) setSelection((sel) => (sel.approval === raised ? sel : { ...sel, approval: raised }))
        return true
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
  // The head of the running reply shows a moment after its turn starts (`TurnHead`), so it follows
  // the end into view the same way when it does.
  const followEnd = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 200
    if (followTurnRef.current || nearBottom) endRef.current?.scrollIntoView({ block: 'end' })
  }, [])
  useEffect(() => {
    followEnd()
    if (!streaming) followTurnRef.current = false
  }, [turns, streaming, followEnd])

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
    // The coalescer still holds the PRIOR answer's run; the new reply must open its own.
    dropTextRun()
    markStreaming(true)
    readSnapshot(s, (d) => {
      adoptSnapshot(d)
      // A replacement that already ended by the time the read lands is said as it ended.
      markStreaming(!!d.run_state.turn, turnOutcomeOf(d.run_state.last_outcome))
      return true
    }).catch(() => setTurns(cutLocally))
    return true
  }

  async function regenerate() { await replay() }

  // Retry or Regenerate over a turn whose finished steps may have changed something is answered
  // with the gateway's question instead (`chat/repeatedSteps`): the turn runs again only on her
  // yes, sent back as `confirmed`, and a No leaves the page as it was.
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

  async function editResend(turnIndex: number, content: string, rewind = false, confirmed?: string) {
    const t = content.trim()
    if (!t) return
    const turn = turns[turnIndex]
    // Her message as it leaves the page again: each block she pasted into it in place of its
    // marker, and the blocks beside it (`asSent`), as every send has. The page shows her message
    // with the markers, so the editor and Rewind to here both hold them.
    const sent = asSent(t, turn?.pastes ?? [])
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
    // Her message sent as it is (Rewind to here, or a resend she did not change) runs its turn
    // again, so the gateway asks first when that turn finished steps that may have changed
    // something (`chat/repeatedSteps`), as it does for Retry. What the page shows of a message is
    // not always the row's own words, so the page says it is sending the message again; the turn
    // runs only on her yes, sent back as `confirmed`, and a No leaves the page as it was.
    const again = !!turn && t === turnText(turn)
    const landed = await replaceTurns(
      (s) => api.editResend(s, sent.text, turn?.ts, turnIndex, newTs, asRewind, { again, confirm: confirmed }, sent.pastes),
      (prev) => [...prev.slice(0, turnIndex), userTurn(t, newTs, sent.pastes.length ? sent.pastes : undefined)],
      (e) => {
        if (askToRepeat(e, (yes) => editResend(turnIndex, content, rewind, yes))) return
        if (fromEditor) setEditFailure(failureSentence(what, e)); else reportActionFailure(what)(e)
      },
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
        return turn.role === 'user' ? 'You' : (agent || 'Assistant')
      }
    }
    return undefined
  }

  // The tool results that show a run's or a loop's live card: the first to name each, however often the
  // agent read it after (`liveWorkflowCards`, `liveLoopCards`). Every later read keeps its plain tool card.
  const liveCards = useMemo(() => new Set([...liveWorkflowCards(turns), ...liveLoopCards(turns)]), [turns])
  // The Session Map's entries — one per USER message, each owning the turns of its exchange.
  // Derived ONCE, here, and handed to both forms, so the rail and the coarse-pointer drawer index
  // the identical array (a "Message 3 of 7" that means a different 7 in each form is two maps).
  const sessionEntries = useMemo(() => sessionMapEntries(turns), [turns])
  /** Scroll the turn at a map coordinate into view — the ONE scroll implementation behind the
   *  rail's tick and the drawer's row, which are the only two jump surfaces. */
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
   *  So the tap now only RECORDS where it wants to go, and the scroll runs from the drawer's
   *  `AnimatePresence` `onExitComplete` in the page — the first moment the panel is out of the DOM and
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

  // The model proposed a switch out of a restricted mode and the user clicked
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

  return {
    // Where the view is.
    scrollRef, endRef, scrolledUp, followTurnRef, followNewTurn, followEnd, turnNodes,
    // The find bar.
    findOpen, setFindOpen, findSeed,
    // The Session Map: its form, its open state, its entries and its jumps.
    isMobile, mapOpen, setMapOpen, sessionEntries, jumpToTurn, pendingMapJump, runPendingMapJump,
    // What a turn offers.
    liveCards, answerQuestion, approve, editingTurn, setEditingTurn, editFailure, setEditFailure,
    editResend, rewindTo, forkAt, forkRewound, regenerate, switchVariant, switchToAgentAndRun,
    attributionForNode,
  }
}

import { useEffect, useMemo, useRef, useState } from 'react'
import { reportActionFailure } from '../../../app/reportingWrite'
import { onToolResultFull } from '../toolResultBridge'
import { deriveActivity } from '../chatTypes'
import { api } from '../../../lib/api'
import { useQueryParam, type RouteProps } from '../../../app/useQueryState'
import { useIsMobile } from '../../../app/useIsMobile'
import { useKeyBinding } from '../../../app/keymap'
import type { ChatStream } from '../useChatStream'

// ── The chat's docked panes ────────────────────────────────────────────────────────────────────
//
// What the panels beside the chat keep: the Activity rail (`?activity`) with the files and links
// the transcript touched, the subagents a fan-out started and the side chat; the open file
// (`?file`); the chat's working folder (`?tree`, ⌘⇧E); the new chat's history panel (`?history`);
// a block of a reply open beside the reading (`?beside`, `beside/blockBeside`); and a tool's full
// result (`?result`), fetched when a card asks for it. Each rides the URL, so Back closes it and a
// reload reopens it. `ChatPanes.tsx` draws them, and the chat page the block beside the reading.
//
// Focus mode (`app/viewModes.tsx`) gives the window to the transcript: entering it closes the docked
// panes, and leaving it opens again the ones it closed. While it is on, a pane opens only when
// something asks for it, and only that pane, since asking for one closes the others. A chat opened
// in focus mode keeps the pane its address names: the address is an ask too.

/** The panes docked beside the chat on every form, by their URL key. */
const DOCKED_PANES = ['activity', 'history', 'file', 'tree', 'beside']

export function useChatPanes({ query, setQuery, stream, focus = false }: {
  query: Record<string, string>
  setQuery: RouteProps['setQuery']
  stream: Pick<ChatStream, 'sessionRef' | 'turns' | 'setSubagents' | 'sideBusy' | 'setSideBusy' | 'setSideMsgs' | 'sideOpenedRef'>
  focus?: boolean
}) {
  const { sessionRef, turns, setSubagents, sideBusy, setSideBusy, setSideMsgs, sideOpenedRef } = stream
  // The docked panes' URL keys. The Session Map is a pane only on a phone, where it is a drawer; on
  // a pointer device it is the transcript's own gutter rail, which costs the transcript nothing.
  const isMobile = useIsMobile()
  const paneKeys = isMobile ? [...DOCKED_PANES, 'map'] : DOCKED_PANES
  /** In focus mode, the patch that opens one pane closes the others. */
  const alone = (key: string): Record<string, null> =>
    focus ? Object.fromEntries(paneKeys.filter((k) => k !== key).map((k) => [k, null])) : {}
  const closedByFocus = useRef<Record<string, string> | null>(null)
  const focusWas = useRef(focus)
  useEffect(() => {
    if (focus === focusWas.current) return
    focusWas.current = focus
    if (focus) {
      const open = Object.fromEntries(paneKeys.filter((k) => query[k]).map((k) => [k, query[k]]))
      closedByFocus.current = open
      if (Object.keys(open).length) setQuery(Object.fromEntries(Object.keys(open).map((k) => [k, null])), { replace: true })
      return
    }
    const closed = closedByFocus.current ?? {}
    closedByFocus.current = null
    const reopen = Object.fromEntries(Object.entries(closed).filter(([k]) => !query[k]))
    if (Object.keys(reopen).length) setQuery(reopen, { replace: true })
  }, [focus]) // eslint-disable-line react-hooks/exhaustive-deps
  // activity panel (Stage 5) — Index/Files/Links, chat-only, toggled from header.
  // URL-backed (?activity=1, push → Back closes it; refresh restores it).
  const activityOpen = query.activity === '1'
  const setActivityOpen = (on: boolean) => setQuery({ ...(on ? alone('activity') : {}), activity: on ? '1' : null })
  // New-chat page: a right-docked chat-history panel (the shared SidePanel), toggled
  // from the header. URL-bound (?history=1) so Back closes it + refresh restores it.
  const historyOpen = query.history === '1'
  const setHistoryOpen = (on: boolean) => setQuery({ ...(on ? alone('history') : {}), history: on ? '1' : null })
  // "Show full result": the full raw of a projected tool
  // result, fetched on demand from the per-session store + shown in a modal. The
  // OPEN state is the URL (?result=<rawRef>, push); the fetched body + the tool
  // name (for the title) are derived — the ref is stable so a refresh re-fetches.
  const [resultRef, setResultRef] = useQueryParam(query, setQuery, 'result', '')
  const resultToolRef = useRef<string>('')
  const [resultBody, setResultBody] = useState<{ content: string; length: number } | null>(null)

  // The docked open-file peek panel. URL-backed (?file=<path>, push → Back closes;
  // refresh/deep-link reopens the same file beside the transcript).
  const openFile = query.file || null
  const setOpenFile = (p: string | null) => setQuery({ ...(p ? alone('file') : {}), file: p || null })

  // The chat's working folder, beside the conversation (`WorkingFolderTree`). URL-backed (?tree=1,
  // push → Back closes it; refresh restores it). ⌘⇧E shows and hides it once the chat has started,
  // as the Activity rail is there once it has; before that the key is left to the browser.
  const treeOpen = query.tree === '1'
  const setTreeOpen = (on: boolean) => setQuery({ ...(on ? alone('tree') : {}), tree: on ? '1' : null })
  useKeyBinding('workspace-tree', turns.length > 0 ? () => setTreeOpen(!treeOpen) : undefined)

  // A block of a reply open beside the reading (`beside/blockBeside`). URL-backed (?beside=<block>,
  // push → Back closes it; refresh and a copied link reopen the same block). Its absence is the
  // thread read in its column alone.
  const beside = query.beside || null
  const setBeside = (address: string | null) => setQuery({ ...(address ? alone('beside') : {}), beside: address || null })

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
  return {
    activityOpen, setActivityOpen, historyOpen, setHistoryOpen, openFile, setOpenFile, treeOpen, setTreeOpen, beside, setBeside,
    resultRef, setResultRef, resultToolRef, resultBody, activity, killFanout, openSide, askSide,
  }
}

/** What the panes' view and the chat's header read from the controller. */
export type ChatPanesState = ReturnType<typeof useChatPanes>

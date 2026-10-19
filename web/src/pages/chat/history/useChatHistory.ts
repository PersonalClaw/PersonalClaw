import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { failureSentence, reportActionFailure, reportingWrite } from '../../../app/reportingWrite'
import { searchCoverage } from '../searchDeepLink'
import { confirm, promptInput } from '../../../ui/dialog'
import { notify } from '../../../app/appSdk'
import { api, isSwitchedOff, type ChatSessionSummary, type ChatFolder, type ChatTag, type RetagJob, type SessionSearchAnswer } from '../../../lib/api'
import { refreshKinds, useChatSocket, type WsMessage } from '../../../lib/useChatSocket'
import { detailKey } from '../useChatStream'
import { useQuery, invalidateKeys } from '../../../lib/data'
import { downloadFrom } from '../../../lib/download'
import { sessionRecencyMs } from '../../../lib/epoch'
import { sessionTitle } from '../../../lib/sessionTitle'
import { useQueryParam, type RouteProps } from '../../../app/useQueryState'

// ── The chat history page's controller ─────────────────────────────────────────────────────────
//
// What `#/chat/history` keeps and does: the session list and its folders, tags and rooms (each
// its own read), the filters that ride the URL, the content search across transcripts, the bulk
// selection, the retag run, and every row action (pin, folder, tags, archive, export, share,
// delete). `ChatHistoryPage.tsx` draws it.

/** Tags in and out of one session — the body of `PUT .../tags` (`api.editSessionTags`). */
export type TagEdit = { add?: string[]; remove?: string[] }

/** The gateway's rule for a tag edit, for the optimistic paint: the removals, then each addition
 *  appended unless the session already carries it. */
function applyTagEdit(tags: string[], edit: TagEdit): string[] {
  const out = tags.filter((t) => !(edit.remove ?? []).includes(t))
  for (const t of edit.add ?? []) if (!out.includes(t)) out.push(t)
  return out
}

/** The most matches the chat list's content search asks for: the route's ceiling. */
const SEARCH_LIMIT = 200

/** How long the frames saying the list moved gather before it is read again (`relistSoon`). */
const RELIST_AFTER_MS = 500

export function useChatHistory({ navigate, query, setQuery }: { navigate: (p: string) => void; query: Record<string, string>; setQuery: RouteProps['setQuery'] }) {
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
  // View/filter state rides the URL (the unified-URL pattern, matching every
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
  // ── A row follows its chat. The list is what the route answered when it was read, and a chat
  // moves on behind it: its turn ends, its count grows, one started in another tab appears. It
  // read "3 messages · running" for a chat the route already served idle with 7, until reopened.
  // So the list is read again on the gateway's list push (`sessions`, sent on every change to a
  // chat it lists), its `history` hint (a turn ended, chats deleted or archived), and a
  // `work_phase` frame that starts or ends a turn its row does not show; a running turn saying
  // its phase again is not news to the row. One turn's end says itself in three of those frames,
  // and many chats at once say it for each, so the first frame schedules one read and the rest
  // join it. The read is the collection's bust, as `load()`'s is, rather than a refresh: a refresh
  // would join a request already on the wire, sent before the turn ended.
  const relistTimer = useRef<number | undefined>(undefined)
  const relistSoon = () => {
    if (relistTimer.current !== undefined) return
    relistTimer.current = window.setTimeout(() => { relistTimer.current = undefined; invalidateKeys('chat:sessions', true) }, RELIST_AFTER_MS)
  }
  useEffect(() => () => window.clearTimeout(relistTimer.current), [])
  const turnMoved = (frame: Record<string, unknown>) => {
    const s = (sessions ?? []).find((x) => x.key === frame.session)
    return !!s && !!s.run_state?.turn !== !!frame.turn
  }
  // The socket came back: every frame went to nobody while it was down, so the list is read again,
  // and the retag job's state with the tags it changed meanwhile (a restart ends the job).
  const resync = () => {
    api.retagStatus().then((j) => { setRetag(j ?? null); retagUpdatedRef.current = j?.updated ?? 0 }).catch(() => {})
    load(); refreshTags()
  }
  useChatSocket((m: WsMessage) => {
    if (m.type === 'sessions' || refreshKinds(m).includes('history') || (m.type === 'work_phase' && turnMoved(m.data ?? {}))) {
      relistSoon()
      return
    }
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
  }, resync)
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
    // The memory half is the backend's rule (`chat_forget.delete_chats`): what memory filed under
    // this chat alone goes, and a record other work also stands behind stays.
    if (!(await confirm({
      title: 'Delete chat?',
      body: `"${sessionTitle(s)}" and its history will be permanently removed, and so will what memory drew from this chat alone: its summary, its episodes, and the facts and lessons that came only from it. What also came from elsewhere stays, such as a lesson you taught in another chat too or a fact you edited in Memory, as do the files, Knowledge, artifacts and skills you kept.`,
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

  return {
    sessionsError, refreshSessions, foldersError, tagsError, roomsData, roomsError, refreshRooms, folders,
    tags, sessions, q, setQ, view, setView, peekKey, setPeekKey, peekSession, tagFilter, setTagFilter, origin,
    setOrigin, roomsScope, showArchived, setShowArchived, selected, bulkBusy, bulkNote, selecting,
    toggleSelected, clearSelection, runBulk, retag, retagRunning, startRetag, tagById, n, contentKeys,
    contentSnippets, contentSource, setRestFor, restBusy, setRestBusy, contentFailure, setContentRetry,
    folderDragKey, setFolderDragKey, overFolder, setOverFolder, coverage, unlisted, filtered,
    originCounts, roomCount, roomsAvailable, del, downloadExport, shareSession, togglePin, setFolder,
    toggleTag, setColumnTag, setLifecycle, setNeverArchive, createFolder, byFolder,
  }
}

/** What the history page reads from its controller. */
export type ChatHistory = ReturnType<typeof useChatHistory>

import { ResultAnnouncement } from '../../../ui/ListControls'
import { BUSY_REASON } from '../../../ui/unavailable'
import { fvs } from '../../../design/fontWeight'
import { AnimatePresence } from 'framer-motion'
import { Edit3, Search, MessageSquare, Trash2, Pin, Sparkles, Folder, FolderPlus, Tag as TagIcon, Columns3, List as ListIcon, Filter, Loader2, Target, Code2 as CodeIcon, GripVertical, Eye, Archive, ArchiveRestore, Download, Share2 } from 'lucide-react'
import { IconButton } from '../../../ui/IconButton'
import { SquareIconButton } from '../../../ui/SquareIconButton'
import { SearchField } from '../../../ui/SearchField'
import { TopBar } from '../../../ui/TopBar'
import { SidePanel } from '../../../ui/SidePanel'
import { Button } from '../../../ui/Button'
import { Checkbox, FieldError } from '../../../ui/forms'
import { Popover, MenuRow } from '../../../ui/Popover'
import { Segmented } from '../../../ui/Segmented'
import { ContextMenu, type ContextMenuItem } from '../../../ui/motion'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../../ui/HeaderActions'
import { sessionRowMeta } from '../sessionRowMeta'
import { StartedByApp } from '../StartedByApp'
import { FromChannel } from '../FromChannel'
import { chatFindPath, searchSourceLabel } from '../searchDeepLink'
import { EmptyState, ListSkeleton, LoadError } from '../../../ui/ListScaffold'
import { WindowedList } from '../../../ui/WindowedList'
import { InlineError } from '../../../ui/InlineError'
import { PartialNotice } from '../../../ui/PartialNotice'
import { RoomsScope } from '../RoomsScope'
import { type ChatSessionSummary, type ChatFolder, type ChatTag } from '../../../lib/api'
import { sessionTitle } from '../../../lib/sessionTitle'
import { type RouteProps } from '../../../app/useQueryState'
import { SessionPeekBody } from './SessionPeek'
import { TagBoard } from './TagBoard'
import { useChatHistory } from './useChatHistory'

// ── The chat history page ──────────────────────────────────────────────────────────────────────
//
// `#/chat/history`: the sessions list with its search, the list and tag-board views, folders,
// tags, the archive and Agent Rooms, and a peek panel on a row (`SessionPeek.tsx`). What it
// keeps and does is `useChatHistory`'s; the board is `TagBoard.tsx`.

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

/** Dedicated sessions LIST page (#/chat/history) — search, manage, open. */
export function ChatHistoryPage({ navigate, query, setQuery }: { navigate: (p: string) => void; query: Record<string, string>; setQuery: RouteProps['setQuery'] }) {
  const {
    sessionsError, refreshSessions, foldersError, tagsError, roomsData, roomsError, refreshRooms, folders,
    tags, sessions, q, setQ, view, setView, peekKey, setPeekKey, peekSession, tagFilter, setTagFilter, origin,
    setOrigin, roomsScope, showArchived, setShowArchived, selected, bulkBusy, bulkNote, selecting,
    toggleSelected, clearSelection, runBulk, retag, retagRunning, startRetag, tagById, n, contentKeys,
    contentSnippets, contentSource, setRestFor, restBusy, setRestBusy, contentFailure, setContentRetry,
    folderDragKey, setFolderDragKey, overFolder, setOverFolder, coverage, unlisted, filtered,
    originCounts, roomCount, roomsAvailable, del, downloadExport, shareSession, togglePin, setFolder,
    toggleTag, setColumnTag, setLifecycle, setNeverArchive, createFolder, byFolder,
  } = useChatHistory({ navigate, query, setQuery })

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
                        // THE surface windowing was first deferred on ("pending
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

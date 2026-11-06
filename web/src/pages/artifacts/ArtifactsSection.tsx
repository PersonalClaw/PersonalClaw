import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, FolderInput, MessagesSquare } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { Loading, LoadError } from '../../ui/ListScaffold'
import { ResultAnnouncement } from '../../ui/ListControls'
import { QuietButton } from '../../ui/QuietButton'
import { type FilterSectionDef } from '../../ui/FilterMenu'
import { HeaderSearch } from '../../ui/HeaderSearch'
import { HeaderFilter } from '../../ui/HeaderFilter'
import { api, type Artifact } from '../../lib/api'
import { refreshKinds, useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { newSessionTarget } from '../../ui/content/commentTarget'
import { notify } from '../../app/appSdk'
import { promptInput } from '../../ui/dialog'
import { ARTIFACT_KINDS } from '../files/fileMeta'
import { Morph } from '../../ui/motion'
import { ArtifactGrid } from './ArtifactGrid'
import { ArtifactViewer } from './ArtifactViewer'
import { ArtifactIteratePanel, ITERATE_PENDING } from './ArtifactIteratePanel'
import { DeployedAppsMenu } from './ArtifactDeploy'
import { ArtifactPinButton } from './ArtifactPin'
import { PageTitle } from '../../ui/PageTitle'

const SOURCES = ['chat', 'cron', 'subagent', 'manual', 'import'] as const
const SORTS = [
  { key: 'updated', label: 'Recent', said: 'Recent first' },
  { key: 'name', label: 'Name', said: 'By name' },
  { key: 'kind', label: 'Kind', said: 'By kind' },
] as const

/** Artifacts — the library surface.
 *
 *  No slug → the LIBRARY: a grid of live sandboxed preview cards with a
 *  URL-query-backed toolbar (search ?q, kind ?kind, source ?src, collection
 *  ?col, sort ?sort — shareable filter state). A slug → the full-page detail
 *  viewer (version rail + events timeline), with `?v=N` deep-linking a
 *  historical snapshot. Selection state IS the URL. */
export function ArtifactsSection({ sub, navigate, query: routeQuery, setQuery }: RouteProps) {
  const slug = (sub || '').split('/')[0] || ''

  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [loading, setLoading] = useState(false)
  /** The library read's rejection. Distinct from `artifacts.length === 0`, which a failure and an
   *  empty library both produce — the whole defect this replaces. */
  const [loadErr, setLoadErr] = useState<unknown>(null)
  // The base library stays the cheap metadata-only read. Searching is a separate, debounced
  // server query because cards show body excerpts that the base rows deliberately omit.
  const [searchResult, setSearchResult] = useState<{ q: string; artifacts: Artifact[] } | null>(null)
  const [searchErr, setSearchErr] = useState<{ q: string; error: unknown } | null>(null)
  const [searchAttempt, setSearchAttempt] = useState(0)

  // URL-backed toolbar state (replace — in-place refinements, not navigations).
  const [q, setQ] = useQueryParam(routeQuery, setQuery, 'q', '', { replace: true })
  const [kind, setKind] = useQueryParam(routeQuery, setQuery, 'kind', '', { replace: true })
  const [src, setSrc] = useQueryParam(routeQuery, setQuery, 'src', '', { replace: true })
  const [col, setCol] = useQueryParam(routeQuery, setQuery, 'col', '', { replace: true })
  const [sort, setSort] = useQueryParam(routeQuery, setQuery, 'sort', 'updated', { replace: true })
  // ?v=N pins the detail viewer to a historical version (deep-linkable snapshot).
  const [vParam, setVParam] = useQueryParam(routeQuery, setQuery, 'v', '', { replace: true })
  // ?iterate=<session-key> opens the split-view iterate panel on that session.
  // `ITERATE_PENDING` means "open it, it still needs staging" — the panel
  // rewrites the param once /api/investigate hands back the real key, which makes
  // an open iteration thread deep-linkable and survives a close/reopen.
  const [iterate, setIterate] = useQueryParam(routeQuery, setQuery, 'iterate', '', { replace: true })

  const load = useCallback(async () => {
    setLoading(true)
    // `catch { setArtifacts([]) }` made a failed read indistinguishable from an empty library, and
    // the grid then told a user with a full library to go ask the agent to save their first artifact.
    // The rejection is kept instead; the list only renders once it is absent.
    try { setArtifacts(await api.artifacts()); setLoadErr(null) }
    catch (e) { setLoadErr(e) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])
  // An artifact written or removed anywhere (the agent in a chat or the Iterate panel, a workflow
  // step, another tab) moves the library and the open artifact's header name: the gateway's
  // `artifacts` refresh hint says so once the store has written it, and the socket coming back
  // re-reads what it may have missed. The open viewer hears the same hint for its own body.
  useChatSocket((m: WsMessage) => { if (refreshKinds(m).includes('artifacts')) void load() }, () => { void load() })

  const searchQuery = q.trim()
  useEffect(() => {
    if (!searchQuery) {
      setSearchResult(null)
      setSearchErr(null)
      return
    }
    let alive = true
    const timer = window.setTimeout(() => {
      api.artifacts({ q: searchQuery })
        .then((rows) => {
          if (!alive) return
          setSearchResult({ q: searchQuery, artifacts: rows })
          setSearchErr(null)
        })
        .catch((error) => {
          if (!alive) return
          setSearchResult(null)
          setSearchErr({ q: searchQuery, error })
        })
    }, 200)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [searchQuery, searchAttempt, artifacts])

  const currentSearch = searchResult?.q === searchQuery ? searchResult : null
  const currentSearchErr = searchErr?.q === searchQuery ? searchErr.error : null
  const searchPending = !!searchQuery && !currentSearch && !currentSearchErr
  const retrySearch = () => {
    setSearchResult(null)
    setSearchErr(null)
    setSearchAttempt((n) => n + 1)
  }

  // Collections present in the library (derived; free-form labels).
  const collections = useMemo(() => {
    const s = new Set<string>()
    for (const a of artifacts) if (a.collection) s.add(a.collection)
    return [...s].sort()
  }, [artifacts])

  // The header filter (`ui/HeaderFilter`): every choice set of the library, the kind, the source,
  // the collection and the order, as the sections of one menu, its label naming what is set. The
  // kind leads, so the label reads "Show: All kinds", and the rest are named there once set.
  const filterSections = useMemo<FilterSectionDef[]>(() => {
    const bySource = new Map<string, number>()
    const byCollection = new Map<string, number>()
    for (const a of artifacts) {
      if (a.source) bySource.set(a.source, (bySource.get(a.source) ?? 0) + 1)
      if (a.collection) byCollection.set(a.collection, (byCollection.get(a.collection) ?? 0) + 1)
    }
    const list: FilterSectionDef[] = [{
      title: 'Kind', value: kind, defaultKey: '', onChange: setKind,
      options: [{ key: '', label: 'All kinds' }, ...ARTIFACT_KINDS.map((k) => ({ key: k.key, label: k.label }))],
    }, {
      title: 'Source', value: src, defaultKey: '', onChange: setSrc, quiet: true,
      options: [
        { key: '', label: 'Any source', count: artifacts.length },
        ...SOURCES.map((k) => ({ key: k, label: k[0].toUpperCase() + k.slice(1), count: bySource.get(k) })),
      ],
    }]
    // Collections are user-created, so the section only exists once one does — same conditional the
    // old dropdown carried.
    if (collections.length > 0) list.push({
      title: 'Collection', value: col, defaultKey: '', onChange: setCol, quiet: true,
      options: [
        { key: '', label: 'All collections', count: artifacts.length },
        ...collections.map((c) => ({ key: c, label: c, count: byCollection.get(c) })),
      ],
    })
    list.push({
      title: 'Sort by', value: sort, defaultKey: 'updated', onChange: setSort, quiet: true,
      said: (k) => SORTS.find((o) => o.key === k)?.said ?? SORTS[0].said,
      options: SORTS.map((o) => ({ key: o.key, label: o.label })),
    })
    return list
  }, [artifacts, collections, kind, src, col, sort, setKind, setSrc, setCol, setSort])

  // Exact filters + sort stay client-side over the server's q-matched rows. This gives the
  // URL one search meaning everywhere: name, description, tags, collection, or card body.
  const filtered = useMemo(() => {
    const base = searchQuery ? (currentSearch?.artifacts ?? []) : artifacts
    let out = base.filter((a) => {
      if (kind && a.kind !== kind) return false
      if (src && a.source !== src) return false
      if (col && a.collection !== col) return false
      return true
    })
    if (sort === 'name') out = [...out].sort((a, b) => a.name.localeCompare(b.name))
    else if (sort === 'kind') out = [...out].sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name))
    // 'updated' keeps the server order (newest first).
    return out
  }, [artifacts, currentSearch, searchQuery, kind, src, col, sort])

  const open = (a: Artifact) => navigate(`artifacts/${a.slug}`)
  const back = () => { setVParam(''); setIterate(''); navigate('artifacts') }

  // "Source file" on a file-backed artifact opens it in the Files page (its home).
  const openSourceFile = useCallback((path: string) => {
    const dir = path.replace(/\/[^/]*$/, '')
    const params = new URLSearchParams()
    if (dir && dir !== path) params.set('dir', dir)
    params.set('file', path)
    navigate(`files?${params.toString()}`)
  }, [navigate])

  // Assign/clear a collection from the detail header (persists via PATCH).
  const active = slug
    ? artifacts.find((a) => a.slug === slug)
      ?? currentSearch?.artifacts.find((a) => a.slug === slug)
    : undefined
  const assignCollection = async () => {
    if (!slug) return
    const name = await promptInput({
      title: 'Set collection', label: 'Collection',
      placeholder: collections[0] ?? 'e.g. Dashboards',
      initial: active?.collection ?? '',
      confirmLabel: 'Save',
    })
    if (name === null || name === undefined) return
    try {
      await api.updateArtifact(slug, { collection: name.trim() })
      await load()
    } catch (e) { notify(`Couldn't set collection: ${String((e as Error)?.message || e)}`, 'error') }
  }

  const initialVersion = vParam ? Number(vParam) || undefined : undefined
  // A read-only artifact refuses every update, collection included — it is a frozen
  // record, not a filing target — so the control is absent rather than
  // present-and-always-failing.
  const canFile = !!slug && !!active && !active.readonly
  // Iterating means the agent WRITES this slug (the session is staged in `agent`
  // mode), so a frozen record is not an iteration target — same reason it can't be
  // filed. The panel is absent rather than present-and-always-refused.
  const canIterate = canFile
  const iterateOpen = canIterate && !!iterate

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<div className="flex min-w-0 items-center gap-m">
          {slug
            ? <QuietButton onClick={back} title="Back to the library"><ArrowLeft size={13} /> Library</QuietButton>
            : <PageTitle className="shrink-0">Artifacts</PageTitle>}
          {slug && active && <span className="truncate text-on-surface" data-type="title-l">{active.name}</span>}
        </div>}
        right={!slug ? (
          // The library's controls: the search, the filter, then the deployed-app listing, which
          // is not a filter and is absent when nothing is served.
          <HeaderActions>
            <HeaderSearch value={q} onChange={setQ} placeholder="Search artifacts" />
            <HeaderFilter sections={filterSections} />
            <DeployedAppsMenu onOpen={(s) => navigate(`artifacts/${s}`)} onChanged={load} />
          </HeaderActions>
        ) : active ? (
          <HeaderActions>
            {/* The one order: the actions, then the panel opener (Iterate) last. A pin is Home's
                bookmark, not a write to the artifact, so a read-only record takes one as well. */}
            {canFile && (
              <HeaderControl icon={FolderInput} label={active.collection || 'Set collection'} onClick={assignCollection}
                title="Group this artifact under a library collection" />
            )}
            <ArtifactPinButton slug={slug} name={active.name} />
            {canFile && (
              <HeaderControl icon={MessagesSquare} label={iterateOpen ? 'Close iterate' : 'Iterate with agent'}
                onClick={() => setIterate(iterate ? '' : ITERATE_PENDING)} ariaExpanded={iterateOpen}
                title={iterateOpen ? 'Close the iterate panel' : 'Open a chat beside this artifact and change it by asking'} />
            )}
          </HeaderActions>
        ) : undefined}
      />

      {slug ? (
        // The split view: artifact left, chat right — stacked below `lg`,
        // where a 26rem side panel would leave the preview unreadable.
        <div className="mx-auto flex min-h-0 w-full flex-1 flex-col lg:flex-row">
          {/* The CLOSING half of the library's shared-element morph — the same
              `artifact-<slug>` id the grid card carries, so the viewer flies out of that
              card's box and folds back into it on Back. The id is on this stable
              container, NOT on anything inside the viewer: the viewer mounts before its
              fetch resolves, and a morph end that appears a tick late is silently no
              morph at all. */}
          <Morph id={`artifact-${slug}`} className="flex min-h-0 min-w-0 flex-1 flex-col">
            <ArtifactViewer key={slug} slug={slug} onChanged={load}
              onDeleted={() => { back(); load() }} onOpenSourceFile={openSourceFile}
              initialVersion={initialVersion}
              onVersionChange={(v) => setVParam(v === null ? '' : String(v))}
              defaultDetailsOpen={initialVersion != null}
              commentTarget={navigate ? newSessionTarget(navigate, { title: `Comments: ${active?.name ?? slug}` }) : undefined} />
          </Morph>
          {iterateOpen && (
            <div className="flex min-h-0 shrink-0 basis-1/2 flex-col lg:w-[26rem] lg:basis-auto xl:w-[30rem]">
              <ArtifactIteratePanel key={slug} slug={slug} name={active?.name ?? slug}
                session={iterate} onSession={setIterate} onClose={() => setIterate('')} />
            </div>
          )}
        </div>
      ) : (
        <div className="mx-auto flex min-h-0 w-full flex-1 flex-col">
          {/* `narrowed` is this surface's own definition of "the user has filtered" — the grid
              already uses it to tell an empty library from a filtered-to-nothing one, so the
              announcement rides the same flag rather than inventing a second rule.
              🪤 `&& !searchPending` rather than unmounting the tag while the query is in
              flight: text search is now a REMOTE read, and an un-gated `active` would
              announce the PREVIOUS query's count as though it answered this one. Gating
              `active` is how the other two remote searches (Local/Ollama model managers)
              do it, and it matters more than it looks — `ResultAnnouncement` renders its
              live region unconditionally and only blanks the TEXT, so the region stays in
              the DOM across the fetch. Unmounting it instead would re-insert an
              aria-live node with its content already present, which screen readers do not
              reliably announce; the count would land silently on exactly the path this
              change adds. */}
          <ResultAnnouncement count={filtered.length} noun="artifacts"
            active={!!(q.trim() || kind || src || col) && !searchPending} />
          <div className="min-h-0 flex-1 overflow-y-auto">
            {loadErr && artifacts.length === 0 && !currentSearch
              ? <LoadError what="artifacts" error={loadErr} onRetry={load} />
              : loading && artifacts.length === 0
              ? <Loading what="artifacts" />
              : currentSearchErr
              ? <LoadError what="artifact search" error={currentSearchErr} onRetry={retrySearch} />
              : searchPending
              ? <Loading what="matching artifacts" />
              // `narrowed` lets the grid distinguish an empty library from a filtered-to-nothing
              // one — it only ever sees the post-filter list.
              : <ArtifactGrid artifacts={filtered} onOpen={open}
                  onBrowseFiles={() => navigate('files')}
                  narrowed={!!(searchQuery || kind || src || col)} />}
          </div>
        </div>
      )}
    </div>
  )
}

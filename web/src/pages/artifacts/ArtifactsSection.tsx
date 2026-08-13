import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, ChevronDown, FolderInput } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions } from '../../ui/HeaderActions'
import { Loading } from '../../ui/ListScaffold'
import { SearchField } from '../../ui/SearchField'
import { ResultAnnouncement } from '../../ui/ListControls'
import { Segmented } from '../../ui/forms'
import { QuietButton } from '../../ui/QuietButton'
import { Popover, MenuRow } from '../../ui/Popover'
import { api, type Artifact } from '../../lib/api'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { newSessionTarget } from '../../ui/content/commentTarget'
import { notify } from '../../app/appSdk'
import { promptInput } from '../../ui/dialog'
import { ARTIFACT_KINDS } from '../files/fileMeta'
import { ArtifactGrid } from './ArtifactGrid'
import { ArtifactViewer } from './ArtifactViewer'
import { PageTitle } from '../../ui/PageTitle'

const SOURCES = ['chat', 'cron', 'subagent', 'manual', 'import'] as const
const SORTS = [
  { key: 'updated', label: 'Recent' },
  { key: 'name', label: 'Name' },
  { key: 'kind', label: 'Kind' },
] as const

/** Artifacts — the library surface (ARTIFACTS S2 on the S1b route).
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

  // URL-backed toolbar state (replace — in-place refinements, not navigations).
  const [q, setQ] = useQueryParam(routeQuery, setQuery, 'q', '', { replace: true })
  const [kind, setKind] = useQueryParam(routeQuery, setQuery, 'kind', '', { replace: true })
  const [src, setSrc] = useQueryParam(routeQuery, setQuery, 'src', '', { replace: true })
  const [col, setCol] = useQueryParam(routeQuery, setQuery, 'col', '', { replace: true })
  const [sort, setSort] = useQueryParam(routeQuery, setQuery, 'sort', 'updated', { replace: true })
  // ?v=N pins the detail viewer to a historical version (deep-linkable snapshot).
  const [vParam, setVParam] = useQueryParam(routeQuery, setQuery, 'v', '', { replace: true })

  const load = useCallback(async () => {
    setLoading(true)
    try { setArtifacts(await api.artifacts()) } catch { setArtifacts([]) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  // Collections present in the library (derived; free-form labels).
  const collections = useMemo(() => {
    const s = new Set<string>()
    for (const a of artifacts) if (a.collection) s.add(a.collection)
    return [...s].sort()
  }, [artifacts])

  // Client-side filter + sort over the (content-free) list.
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    let out = artifacts.filter((a) => {
      if (kind && a.kind !== kind) return false
      if (src && a.source !== src) return false
      if (col && a.collection !== col) return false
      if (needle) {
        const hay = `${a.name}\n${a.description}\n${a.tags.join(' ')}\n${a.collection ?? ''}`.toLowerCase()
        if (!hay.includes(needle)) return false
      }
      return true
    })
    if (sort === 'name') out = [...out].sort((a, b) => a.name.localeCompare(b.name))
    else if (sort === 'kind') out = [...out].sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name))
    // 'updated' keeps the server order (newest first).
    return out
  }, [artifacts, q, kind, src, col, sort])

  const open = (a: Artifact) => navigate(`artifacts/${a.slug}`)
  const back = () => { setVParam(''); navigate('artifacts') }

  // "Source file" on a file-backed artifact opens it in the Files page (its home).
  const openSourceFile = useCallback((path: string) => {
    const dir = path.replace(/\/[^/]*$/, '')
    navigate(`files?dir=${encodeURIComponent(dir)}`)
  }, [navigate])

  // Assign/clear a collection from the detail header (persists via PATCH).
  const active = slug ? artifacts.find((a) => a.slug === slug) : undefined
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
  // A read-only artifact (SM-9) refuses every update, collection included — it is a frozen
  // record, not a filing target — so the control is absent rather than
  // present-and-always-failing.
  const canFile = !!slug && !!active && !active.readonly

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
        right={canFile ? (
          <HeaderActions>
            <QuietButton onClick={assignCollection} title="Group this artifact under a library collection">
              <FolderInput size={13} /> {active.collection || 'Set collection'}
            </QuietButton>
          </HeaderActions>
        ) : undefined}
      />

      {slug ? (
        <div className="mx-auto flex min-h-0 w-full flex-1" style={{ maxWidth: 'var(--content-width)' }}>
          <div className="min-w-0 flex-1">
            <ArtifactViewer key={slug} slug={slug} onChanged={load}
              onDeleted={() => { back(); load() }} onOpenSourceFile={openSourceFile}
              initialVersion={initialVersion}
              onVersionChange={(v) => setVParam(v === null ? '' : String(v))}
              defaultDetailsOpen={initialVersion != null}
              commentTarget={navigate ? newSessionTarget(navigate, { name: `Comments: ${active?.name ?? slug}` }) : undefined} />
          </div>
        </div>
      ) : (
        <div className="mx-auto flex min-h-0 w-full flex-1 flex-col" style={{ maxWidth: 'var(--content-width)' }}>
          {/* toolbar — URL-backed filter state (shareable) */}
          <div className="flex flex-wrap items-center gap-m border-b border-outline/40 px-l py-2.5">
            <div className="w-64"><SearchField size="sm" value={q} onChange={setQ} placeholder="Search artifacts…" ariaLabel="Search artifacts" name="artifacts-search" /></div>
            {/* `ARTIFACT_KINDS` is 16 kinds, so this strip renders 17 tabs at a measured 1152px —
                nearly 3x the next-largest registry feeding a Segmented anywhere in the app (6). With
                no collapse strategy the strip could not shrink and the row it sits in is
                `overflow: hidden`, so the tail was simply CUT OFF and unreachable: 7 of 17 tabs off
                screen at 834px (tablet), 12 of 17 at 390px — 'SVG' through 'Video' had no way to be
                picked at all. `collapse="scroll"` is the primitive's own form of what the Files
                root-tab strip hand-rolls for the same reason (a variable-count strip in a bounded
                slot), and what the inbox strip already declares; the strip is untouched at any width
                where it fits, so desktop is byte-identical. Keyboard reach is unaffected — the
                roving-tabindex arrow nav scrolls each tab into view as it takes focus. */}
            <Segmented ariaLabel="Artifact kind" value={kind} onChange={setKind} collapse="scroll"
              options={[{ key: '', label: 'All kinds' }, ...ARTIFACT_KINDS.map((k) => ({ key: k.key, label: k.label }))]} />
            <FilterMenu label={src ? `via ${src}` : 'Any source'} value={src} onPick={setSrc}
              options={[{ key: '', label: 'Any source' }, ...SOURCES.map((s) => ({ key: s, label: `via ${s}` }))]} />
            {collections.length > 0 && (
              <FilterMenu label={col || 'All collections'} value={col} onPick={setCol}
                options={[{ key: '', label: 'All collections' }, ...collections.map((c) => ({ key: c, label: c }))]} />
            )}
            <div className="ml-auto"><Segmented ariaLabel="Sort artifacts" value={sort} onChange={setSort} options={SORTS.map((s) => ({ key: s.key, label: s.label }))} /></div>
            {/* `narrowed` is this surface's own definition of "the user has filtered" — the grid
                already uses it to tell an empty library from a filtered-to-nothing one, so the
                announcement rides the same flag rather than inventing a second rule. */}
            <ResultAnnouncement count={filtered.length} noun="artifacts" active={!!(q.trim() || kind || src || col)} />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {loading && artifacts.length === 0
              ? <Loading what="artifacts" />
              // `narrowed` lets the grid distinguish an empty library from a filtered-to-nothing
              // one — it only ever sees the post-filter list.
              : <ArtifactGrid artifacts={filtered} activeSlug={null} onOpen={open}
                  narrowed={!!(q.trim() || kind || src || col)} />}
          </div>
        </div>
      )}
    </div>
  )
}

/** A compact dropdown filter (source/collection) — QuietButton trigger + Popover menu. */
function FilterMenu({ label, value, options, onPick }: {
  label: string
  value: string
  options: { key: string; label: string }[]
  onPick: (key: string) => void
}) {
  return (
    <Popover placement="bottom" trigger={(_open, toggle) => (
      <QuietButton onClick={toggle} title={label}>{label} <ChevronDown size={11} /></QuietButton>
    )}>
      {(close) => (
        <>
          {options.map((o) => (
            <MenuRow key={o.key || '(all)'} label={o.label} selected={o.key === value}
              onClick={() => { onPick(o.key); close() }} />
          ))}
        </>
      )}
    </Popover>
  )
}

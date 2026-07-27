import { useCallback, useEffect, useState } from 'react'
import { Box, PanelRight } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { SidePanel } from '../../ui/SidePanel'
import { EmptyState, Loading } from '../../ui/ListScaffold'
import { api, type Artifact } from '../../lib/api'
import type { RouteProps } from '../../app/useQueryState'
import { newSessionTarget } from '../../ui/content/commentTarget'
import { ArtifactList } from './ArtifactList'
import { ArtifactViewer } from './ArtifactViewer'

/** Artifacts — the standalone library surface (ARTIFACTS S1b route split).
 *
 *  Artifacts were one tab inside Files for navigational similarity only; they are
 *  their own entity (named, versioned, agent-produced) and now own `#/artifacts`
 *  [/<slug>]. The layout carries over the Files-era behavior byte-for-byte in
 *  spirit: the viewer fills the width, the list is a right-docked hidable
 *  SidePanel, and "Source file" cross-navigates to the Files page. The S2 library
 *  grid (live previews, collections, search) lands on this surface next. */
export function ArtifactsSection({ sub, navigate }: RouteProps) {
  // Deep-link `#/artifacts/<slug>` opens that artifact.
  const deepSlug = (sub || '').split('/')[0] || ''

  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [loading, setLoading] = useState(false)
  const [active, setActive] = useState<Artifact | null>(null)
  const [listOpen, setListOpen] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try { setArtifacts(await api.artifacts()) } catch { setArtifacts([]) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  // Deep-link → select that artifact once the list is in.
  useEffect(() => {
    if (!deepSlug || !artifacts.length) return
    const match = artifacts.find((a) => a.slug === deepSlug)
    if (match) setActive(match)
  }, [deepSlug, artifacts])

  // Selecting an artifact keeps the URL addressable (replace — selection is an
  // in-place refinement, not a navigation the Back button should unwind per click).
  const select = (a: Artifact | null) => {
    setActive(a)
    navigate(a ? `artifacts/${a.slug}` : 'artifacts', { replace: true })
  }

  // "Source file" on a file-backed artifact opens it in the Files page (its home).
  const openSourceFile = useCallback((path: string) => {
    const dir = path.replace(/\/[^/]*$/, '')
    navigate(`files?dir=${encodeURIComponent(dir)}`)
  }, [navigate])

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<span data-type="title-l" className="text-on-surface shrink-0">Artifacts</span>}
        right={
          <HeaderActions>
            <HeaderControl icon={PanelRight}
              label={listOpen ? 'Hide artifact list' : 'Show artifact list'}
              active={listOpen} onClick={() => setListOpen((v) => !v)} />
          </HeaderActions>
        }
      />

      <div className="mx-auto flex min-h-0 w-full flex-1" style={{ maxWidth: 'var(--content-width)' }}>
        <div className="min-w-0 flex-1">
          {active
            ? <ArtifactViewer key={active.slug} slug={active.slug} onChanged={load}
                onDeleted={() => { select(null); load() }} onOpenSourceFile={openSourceFile}
                commentTarget={navigate ? newSessionTarget(navigate, { name: `Comments: ${active.name}` }) : undefined} />
            : <EmptyState icon={Box} title="No artifact selected" hint="Artifacts are named, versioned snapshots — widgets, docs, and files agents produce. Pick one to view its render, history, and timeline." />}
        </div>
        {listOpen && (
          <SidePanel title="Artifacts" icon={<Box size={18} />} storeKey="artifacts-list-w" fillHeight onClose={() => setListOpen(false)}>
            {loading && artifacts.length === 0 ? <Loading /> : <ArtifactList artifacts={artifacts} activeSlug={active?.slug ?? null} onSelect={select} />}
          </SidePanel>
        )}
      </div>
    </div>
  )
}

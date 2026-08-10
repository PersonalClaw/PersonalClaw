import { memo } from 'react'
import { Box } from 'lucide-react'
import type { Artifact } from '../../lib/api'
import { EmptyState } from '../../ui/ListScaffold'
import { ArtifactCard } from './ArtifactCard'

/** The library grid (ARTIFACTS S2) — responsive card grid of live previews.
 *  Pure layout: filtering/sorting live in the toolbar (ArtifactsSection); the
 *  per-card lazy/LRU cost controls live in ArtifactCard. */
export const ArtifactGrid = memo(function ArtifactGrid({ artifacts, activeSlug, onOpen, narrowed }: {
  artifacts: Artifact[]
  activeSlug: string | null
  onOpen: (a: Artifact) => void
  /** True when a search or filter is active. The grid receives ALREADY-FILTERED artifacts, so
   *  without this it cannot tell "you have none" from "none match" — and told a user with a full
   *  library to go create their first artifact. Matches the `q ? 'No matching X' : 'No X'` shape
   *  the other list pages use (apps, knowledge, prompts, inbox). */
  narrowed?: boolean
}) {
  if (!artifacts.length) {
    return narrowed
      ? <EmptyState icon={Box} title="No matching artifacts" hint="Try a different search, kind, or collection." />
      : <EmptyState icon={Box} title="No artifacts" hint="Artifacts are named, versioned snapshots — widgets, docs, images, and files agents produce. Ask the agent to save one, or save a file as an artifact from the Files page." />
  }
  return (
    <div className="grid grid-cols-1 gap-m p-l sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {artifacts.map((a) => (
        <ArtifactCard key={a.slug} art={a} active={a.slug === activeSlug} onOpen={onOpen} />
      ))}
    </div>
  )
})

import { memo } from 'react'
import { Box } from 'lucide-react'
import type { Artifact } from '../../lib/api'
import { EmptyState } from '../../ui/ListScaffold'
import { Morph } from '../../ui/motion'
import { ArtifactCard } from './ArtifactCard'

/** The library grid (ARTIFACTS S2) — responsive card grid of live previews.
 *  Pure layout: filtering/sorting live in the toolbar (ArtifactsSection); the
 *  per-card lazy/LRU cost controls live in ArtifactCard. */
export const ArtifactGrid = memo(function ArtifactGrid({ artifacts, onOpen, narrowed }: {
  artifacts: Artifact[]
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
        // The card is the OPENING half of the library's shared-element morph (FM-2): it
        // hands its box to the full-page viewer, which flies out of the card the user
        // actually clicked instead of the grid cutting to a page. `ArtifactsSection`
        // renders the grid OR the viewer, never both, so the two ends swap in one commit
        // in both directions — which is the whole precondition for the morph.
        //
        // `grid` on the wrapper, not `h-full`: this div is now the grid item (so it
        // stretches to the row), and a single-child grid container passes that height
        // down to the card's own root — which used to be the grid item itself. Without it
        // the card would fall back to its content height and a row of unequal cards would
        // stop lining up at the bottom.
        <Morph key={a.slug} id={`artifact-${a.slug}`} className="grid">
          <ArtifactCard art={a} onOpen={onOpen} />
        </Morph>
      ))}
    </div>
  )
})

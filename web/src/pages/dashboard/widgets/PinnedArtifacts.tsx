import { useCallback, useEffect, useState } from 'react'
import { MoreRow } from '../../../ui/MoreRow'
import { AnimatePresence } from 'framer-motion'
import { Package, PinOff } from 'lucide-react'
import { api, type Artifact, type PinnedArtifact } from '../../../lib/api'
import { SlotEmptyState, WidgetRow, RowAction } from './kit'
import type { RouteProps } from '../../../app/useQueryState'

/** Pinned artifacts — the dashboard's pin surface (WORK-CONTAINERS §6.5d, R13).
 *
 *  **Why one widget and not a tile registry.** The dashboard has NO tile registry: the bento grid
 *  and per-user layout persistence were deliberately retired, and widgets are hard-imported by
 *  `DashboardPage`. So pinning is not a layout feature — it registers a slug in a list
 *  (`entity_settings/pinned_artifacts.json`) and THIS one component renders it. Inventing a
 *  per-tile registry to serve one feature would rebuild exactly what was removed.
 *
 *  **A pin is a reference, resolved at render.** The backend stores only the slug and when it was
 *  pinned. The name, kind and version come from the artifact itself on every load, so a rename
 *  shows up immediately — and a DELETED artifact simply drops off the list instead of leaving a
 *  card that navigates nowhere. That self-healing is the reason the pin holds no copy. */
export function PinnedArtifacts({ navigate }: RouteProps) {
  const [pins, setPins] = useState<PinnedArtifact[] | null>(null)
  const [byslug, setBySlug] = useState<Record<string, Artifact>>({})

  const load = useCallback(async () => {
    try {
      const { pins: rows } = await api.pinnedArtifacts()
      // Resolve the references. Both halves load together: a pin whose artifact is gone must not
      // render at all, and deciding that needs the artifact list in hand.
      const all = await api.artifacts().catch(() => [] as Artifact[])
      const index: Record<string, Artifact> = {}
      for (const a of all) index[a.slug] = a
      setBySlug(index)
      setPins(rows)
    } catch {
      setPins([])
    }
  }, [])

  useEffect(() => { load() }, [load])

  const unpin = async (slug: string) => {
    // Optimistic: a pin is a bookmark, so the cheap, reversible action should feel instant. A
    // failed write reconciles on the next load rather than blocking the interaction.
    setPins((p) => (p ?? []).filter((x) => x.slug !== slug))
    try { await api.pinArtifact(slug, false) } catch { load() }
  }

  // A pin whose artifact no longer exists is DROPPED, not rendered as a broken row. The pin stays
  // in the store — the artifact could be a provider read that is briefly unavailable, and silently
  // deleting a user's pin because one list read came back short would be worse than hiding it.
  const resolved = (pins ?? []).filter((p) => !!byslug[p.slug])

  if (pins === null) return null
  if (resolved.length === 0) {
    return (
      <SlotEmptyState icon={Package}>
        No pinned artifacts. Pin one from its page to keep it here.
      </SlotEmptyState>
    )
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      <AnimatePresence initial={false}>
        {resolved.slice(0, 6).map((p) => {
          const art = byslug[p.slug]
          return (
            <WidgetRow
              key={p.slug}
              onClick={() => navigate(`artifacts?slug=${encodeURIComponent(p.slug)}`)}
              label={art?.name ?? p.slug}
              actions={
                <RowAction tone="default" onClick={() => unpin(p.slug)} title="Unpin"
                  ariaLabel={`Unpin: ${art?.name ?? p.slug}`}>
                  <PinOff size={15} />
                </RowAction>
              }
            >
              <span className="flex min-w-0 flex-col">
                <span data-type="label-m" className="truncate text-on-surface">{art.name}</span>
                <span data-type="body-s" className="truncate text-on-surface-low">
                  {art.kind}
                  {art.version > 1 ? ` · v${art.version}` : ''}
                </span>
              </span>
            </WidgetRow>
          )
        })}
      </AnimatePresence>
      {/* Pins are deliberate — the user chose each one — so a pin that silently does not appear is
          worse here than in a generated list. */}
      <MoreRow total={resolved.length} shown={6} />
    </div>
  )
}

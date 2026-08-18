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
  /** Resolution index, or `null` when the artifact list read FAILED — the two are different facts
   *  and `resolved` below only drops a pin when the index is trustworthy. */
  const [byslug, setBySlug] = useState<Record<string, Artifact> | null>({})
  /** The PINS read's rejection — kept apart from `pins === null` (not loaded) and `[]` (none). */
  const [pinsErr, setPinsErr] = useState<unknown>(null)

  const load = useCallback(async () => {
    try {
      const { pins: rows } = await api.pinnedArtifacts()
      // Resolve the references. Both halves load together: a pin whose artifact is gone must not
      // render at all, and deciding that needs the artifact list in hand.
      // 🪤 `.catch(() => [])` here did not just lose detail — it emptied the resolution index, and
      // `resolved` DROPS any pin missing from it, so one failed list read hid EVERY pin behind
      // "No pinned artifacts. Pin one from its page to keep it here." The comment below reasoned about
      // a single artifact being briefly unavailable; a failed read of the whole list is a different
      // fact, and the index has to be able to say "I don't know" rather than "none of them exist".
      let index: Record<string, Artifact> | null = null
      try {
        const all = await api.artifacts()
        index = {}
        for (const a of all) index[a.slug] = a
      } catch { index = null }
      setBySlug(index)
      setPins(rows)
      setPinsErr(null)
    } catch (e) {
      // 🪤 `setPins([])` here made a failed PINS read say "No pinned artifacts. Pin one from its page
      // to keep it here." — inviting a user who already has pins to go create their first. Found by
      // this cycle's own rail, which flagged the shape in the outer catch after the inner one was
      // fixed: the same widget had the same defect on its OTHER read.
      setPinsErr(e)
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
  // But that reasoning only holds when the index is TRUSTWORTHY: `byslug === null` means the artifact
  // read failed, and then dropping is a claim we cannot support, so every pin is kept and labelled by
  // its slug (which is what the row already falls back to).
  const resolved = byslug === null ? (pins ?? []) : (pins ?? []).filter((p) => !!byslug[p.slug])

  if (pinsErr) {
    return (
      <SlotEmptyState icon={Package}>
        Couldn't load your pins. They're safe — this is a read error.
      </SlotEmptyState>
    )
  }
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
          const art = byslug?.[p.slug]
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
                {/* Unresolved only happens when the artifact read failed (a pin missing from a
                    TRUSTED index is dropped above). The row still names the pin and still unpins —
                    saying "details unavailable" is honest, where vanishing was not. */}
                <span data-type="label-m" className="truncate text-on-surface">{art?.name ?? p.slug}</span>
                <span data-type="body-s" className="truncate text-on-surface-low">
                  {art ? art.kind : 'details unavailable'}
                  {art && art.version > 1 ? ` · v${art.version}` : ''}
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

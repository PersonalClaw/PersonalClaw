/** The L2 overlay band — where a user/agent surface overlay actually renders.
 *
 *  The call site that makes the L2 producer a producer. `overlay.tsx` loads and validates;
 *  this renders, and it renders through the machinery §6 already had rather than a second
 *  copy of it: each accepted overlay is a `GenUiWidget` (so its actions, its per-component
 *  `LayerBoundary` and its dropped-line notices are the ones chat already uses) wrapped in
 *  a `LayerBoundary` at L2 — a user tree that throws loses its band, never the page.
 *
 *  Renders NOTHING when there are no overlays and no refusals, so a home with an empty
 *  `surfaces/` directory is byte-identical to today — the same safety property
 *  `PinnedTiles` holds for the tile band. */
import { useEffect, useState } from 'react'
import { AlertTriangle, Layers } from 'lucide-react'
import { GenUiWidget } from '../genui/GenUiWidget'
import { LayerBoundary } from './LayerBoundary'
import { LAYER_USER } from './layers'
import { loadSurfaceOverlays, overlayRefusalsFor, overlaysFor } from './overlay'

/** One refused overlay, stated in the three lines the platform's error envelope carries.
 *  A refusal is the POINT here: an overlay that silently did not appear is the failure
 *  mode this band exists to make impossible. */
function OverlayRefusal({ file, what, fix }: { file: string; what: string; fix: string }) {
  return (
    <div
      role="alert"
      data-testid="overlay-refusal"
      className="flex items-start gap-2 rounded-lg px-3 py-2 text-[0.75rem] text-warn"
      style={{ background: 'color-mix(in srgb, var(--color-warn) 8%, transparent)' }}
    >
      <AlertTriangle size={13} className="mt-0.5 shrink-0" />
      <span className="min-w-0 break-words">
        <span className="font-mono">{file}</span> was refused — {what} {fix}
      </span>
    </div>
  )
}

export function SurfaceOverlay({ surface }: { surface: string }) {
  // The load is once-per-session and idempotent; this state exists only to re-render the
  // band when it resolves. A band that never re-rendered would leave a valid overlay
  // invisible until the next route change.
  const [, setLoaded] = useState(0)
  useEffect(() => {
    let live = true
    void loadSurfaceOverlays().then(() => { if (live) setLoaded((n) => n + 1) })
    return () => { live = false }
  }, [])

  const overlays = overlaysFor(surface)
  const refusals = overlayRefusalsFor(surface)
  if (!overlays.length && !refusals.length) return null

  return (
    <section className="flex min-w-0 flex-col gap-s" data-testid="surface-overlay">
      <div className="flex items-center gap-s">
        <Layers size={14} className="shrink-0 text-on-surface-low" />
        {/* h2 to match the dashboard's other section headings — see the note on
            DashboardPage's `Section`. */}
        <h2 data-type="label-l" className="text-on-surface-var">Yours</h2>
        <span className="h-px flex-1 bg-outline-variant/40" />
      </div>
      {refusals.map((r) => (
        <OverlayRefusal key={r.file} file={r.file} what={r.error.what} fix={r.error.fix} />
      ))}
      {overlays.map((o) => (
        <LayerBoundary key={o.file} layer={LAYER_USER} what={o.title || o.file}>
          <GenUiWidget content={o.body} title={o.title || o.file} />
        </LayerBoundary>
      ))}
    </section>
  )
}

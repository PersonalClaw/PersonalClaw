import { useState } from 'react'
import { CloudOff } from 'lucide-react'
import { api, type DegradedSurface } from '../lib/api'
import { useVisiblePoll } from '../lib/useVisiblePoll'

// Prettify a surface slug for display ("search_ranking" → "Search ranking").
function label(surface: string): string {
  const s = surface.replace(/[_-]/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** A compact shell chip shown when any model-dependent surface is running on its
 *  no-model floor (PLATFORM-RESILIENCE §5). Self-polls the degraded endpoint on a
 *  slow cadence (the state changes rarely — a provider comes/goes), exactly like
 *  IncidentBanner; it does NOT use DashboardLive, which only wraps the dashboard
 *  page, not the shell. Renders nothing when every surface has a model. Click to
 *  expand a popover listing each degraded surface, its floor, and its backlog. */
export function DegradedChip() {
  const [surfaces, setSurfaces] = useState<DegradedSurface[] | null>(null)
  const [open, setOpen] = useState(false)

  useVisiblePoll(() => {
    api.degraded().then((r) => setSurfaces(r.surfaces)).catch(() => {})
  }, 20000)

  const down = (surfaces ?? []).filter((s) => !s.available)
  if (down.length === 0) return null

  const worst = down[0]
  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded-pill px-2.5 py-1 text-[0.75rem] transition-colors hover:brightness-110"
        style={{ background: 'var(--color-warn-container, color-mix(in srgb, var(--color-warn) 20%, transparent))', color: 'var(--color-warn)' }}
        aria-expanded={open}
        title={`${down.length} surface(s) running without a model — click for detail`}>
        <CloudOff size={13} className="shrink-0" />
        <span>{down.length === 1 ? `${label(worst.surface)} degraded` : `${down.length} degraded`}</span>
      </button>
      {open && (
        <>
          {/* click-away scrim */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden />
          <div role="dialog" aria-label="Degraded surfaces"
            className="absolute right-0 z-50 mt-1.5 w-80 rounded-xl bg-surface-container p-3 shadow-lg"
            style={{ border: '1px solid var(--color-outline-variant)' }}>
            <div className="mb-2 flex items-center gap-1.5 text-on-surface text-[0.8125rem]" style={{ color: 'var(--color-warn)' }}>
              <CloudOff size={14} /> Running without a model
            </div>
            <div className="flex flex-col gap-2">
              {down.map((s) => (
                <div key={s.surface} className="border-b border-outline-variant/30 pb-2 last:border-0 last:pb-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-on-surface text-[0.8125rem]">{label(s.surface)}</span>
                    {s.backlog > 0 && (
                      <span className="shrink-0 text-on-surface-low text-[0.6875rem]">{s.backlog} queued</span>
                    )}
                  </div>
                  <div className="mt-0.5 text-on-surface-low text-[0.75rem]">{s.floor}</div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

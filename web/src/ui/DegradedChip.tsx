import { useEffect, useRef, useState } from 'react'
import { CloudOff } from 'lucide-react'
import { api, type DegradedSurface } from '../lib/api'
import { useVisiblePoll } from '../lib/useVisiblePoll'
import { useIsMobile } from '../app/useIsMobile'

// Prettify a surface slug for display ("search_ranking" → "Search ranking").
function label(surface: string): string {
  const s = surface.replace(/[_-]/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** Use-case slugs the registry can name, in the SAME words ModelsPanel's `USE_CASE_META` uses —
 *  so "no model for Speech-to-text" here and the "Speech-to-text" row you go bind it in agree.
 *
 *  Not imported from that map: it is a page-local const carrying icons, descriptions and chain
 *  flags for 14 use cases, and a shell chip pulling in a settings page would be a far worse
 *  dependency than three labels. Kept minimal on purpose — only the slugs `degraded.py`'s registry
 *  actually declares (`chat`, `embedding`, `stt`), with `label()` as the fallback so a new
 *  contract still reads sensibly instead of rendering a raw slug. */
const USE_CASE_LABEL: Record<string, string> = {
  chat: 'Chat',
  embedding: 'Embedding',
  stt: 'Speech-to-text',
}

function useCaseLabel(uc: string): string {
  return USE_CASE_LABEL[uc] ?? label(uc)
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
  const triggerRef = useRef<HTMLButtonElement | null>(null)

  // Escape closes the popover and returns focus to the chip — the same contract `ui/Popover`
  // documents and `NotificationBell` already honours. Without it the ONLY way to dismiss this was
  // the click-away scrim: a keyboard user was stranded, and because that scrim is a full-viewport
  // `fixed inset-0` layer it also swallowed pointer events for the whole app until a mouse click
  // landed on it. Measured before the fix: after Escape the panel was still open and the scrim was
  // still up.
  //
  // `stopPropagation` keeps Escape SINGLE-LAYER, as Popover explains: without it the same keydown
  // bubbles to other document-level Escape handlers (a docked SidePanel), so one press would close
  // two layers.
  useEffect(() => {
    if (!open) return
    const onEsc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      setOpen(false)
      triggerRef.current?.focus()
    }
    document.addEventListener('keydown', onEsc)
    return () => document.removeEventListener('keydown', onEsc)
  }, [open])
  // The shell corner is FIXED-WIDTH chrome that floats over every page header, and the
  // header pads itself by the corner's measured width. So a wide corner does not merely
  // look wide — it starves every page's title/control row. This chip's text label was
  // 103px of a 257px corner (40%), while every sibling control is a 28-36px icon; at
  // 390px that left the header's content slot 28px for content wanting 259px, and the
  // corner painted over 22 of 37 surfaces' titles and controls. Icon-only below the
  // mobile breakpoint keeps the indicator (and its popover, which carries the real
  // detail) while returning ~100px to the page. Same reasoning as WidthPill's drop above.
  const isMobile = useIsMobile()

  useVisiblePoll(() => {
    api.degraded().then((r) => setSurfaces(r.surfaces)).catch(() => {})
  }, 20000)

  const down = (surfaces ?? []).filter((s) => !s.available)
  if (down.length === 0) return null

  const worst = down[0]
  const summary = down.length === 1 ? `${label(worst.surface)} degraded` : `${down.length} degraded`
  return (
    <div className="relative">
      <button ref={triggerRef} type="button" onClick={() => setOpen((o) => !o)}
        className={`flex min-h-6 items-center gap-1.5 rounded-pill py-1 text-[0.75rem] transition-colors hover:brightness-110 ${isMobile ? 'px-1.5' : 'px-2.5'}`}
        // 16%, like every other warn-toned chip in the app (ToolInspector's "needs approval",
        // bento's warn tile). This chip was the ONLY 20% site, and that extra 4% is what put
        // warn ink on a warn-hued tint under AA: axe measured it FAILING at 20% and PASSING at
        // 16% on #/learning in light mode (4.35:1 vs the 4.5 floor — same hue on same hue, so
        // deepening the tint darkens the ground toward the ink).
        //
        // The `--color-warn-container` var it used to reach for DOES NOT EXIST — no theme
        // defines it and this was its only reference, so the fallback was always what shipped.
        // Naming a token that isn't there hid the real value from anyone reading the line;
        // dropping it makes the tint depth visible and lints alongside its siblings.
        style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }}
        aria-expanded={open}
        // Icon-only has no visible text, so the name must come from aria-label — a title
        // alone is not an accessible name for AT in every engine.
        aria-label={isMobile ? summary : undefined}
        title={`${summary} — ${down.length} surface(s) running without a model, click for detail`}>
        <CloudOff size={13} className="shrink-0" />
        {!isMobile && <span>{summary}</span>}
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
                  {/* WHAT IS MISSING, beside what still works. `floor` is the reassurance ("these
                      parts keep running"); `use_cases` is the diagnosis — which model binding is
                      absent, and therefore what to go bind. The panel showed only the floor, so it
                      told a user their surface was degraded and nothing about the cause, on a chip
                      whose entire job is "a provider went away".

                      The backend already treats this as the headline: its own degradation notice
                      reads `No model for {', '.join(contract.use_cases)} — {contract.floor}`. The
                      popover was the one surface stating the second half without the first. */}
                  {/* Defaulted read, not `s.use_cases.length`: an older/partial payload can omit
                      the key entirely (the chip's own pre-existing test fixture does), and a chip
                      that crashes the shell corner because a field is absent is a worse failure
                      than the one being fixed. */}
                  {(s.use_cases ?? []).length > 0 && (
                    <div className="mt-0.5 text-on-surface-var text-[0.75rem]">
                      No model for {(s.use_cases ?? []).map(useCaseLabel).join(', ')}
                    </div>
                  )}
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

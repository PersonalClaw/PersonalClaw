import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { WidgetBoundary } from '../../app/ErrorBoundary'

/** Home's bare section header: a label and a hairline, NOT a bordered card, so the content flows
 *  directly beneath and owns the horizontal space. A section's own controls (its filter, its
 *  link) sit at the heading's right, after the hairline: they belong to the section, not the page. */
export function Section({ label, icon: Icon, actions, children }: {
  label: string; icon: LucideIcon; actions?: ReactNode; children: ReactNode
}) {
  return (
    <section className="flex min-w-0 flex-col gap-s">
      <div className="flex min-w-0 flex-wrap items-center gap-s">
        <Icon size={14} className="shrink-0 text-on-surface-low" />
        {/* h2, not h3: this is a top-level section directly under the page's h1 greeting, and
            every one of the dashboard's sections renders through here. As h3 the page read
            h1 -> h3 seven times over, skipping a level for a screen-reader user navigating by
            heading. `data-type="label-l"` carries the size and weight, so the level is purely
            structural — the heading looks identical. */}
        <h2 data-type="label-l" className="text-on-surface-var">{label}</h2>
        <span className="h-px min-w-m flex-1 bg-outline-variant/40" />
        {actions}
      </div>
      {/* Under the band's heading, so a band that throws keeps its label and says it could not show. */}
      <WidgetBoundary what={`the ${label} section`}>{children}</WidgetBoundary>
    </section>
  )
}

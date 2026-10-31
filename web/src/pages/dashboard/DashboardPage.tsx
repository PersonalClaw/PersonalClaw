import { type ReactNode } from 'react'
import { History, type LucideIcon, ListTodo, Compass, Package, Orbit, Monitor, Globe } from 'lucide-react'
import { DashboardLiveProvider } from './DashboardLive'
import { AgentWorld } from './world/AgentWorld'
import { TasksWidget } from './widgets/TasksWidget'
import { Discover } from './widgets/Discover'
import { PinnedArtifacts } from './widgets/PinnedArtifacts'
import { DesktopLiveView } from './widgets/DesktopLiveView'
import { BrowseMirror } from './widgets/BrowseMirror'
import { ScheduleWidget } from './widgets/ScheduleWidget'
import { EntranceGroup, EntranceRegion } from '../../ui/motion'
import { WidgetBoundary } from '../../app/ErrorBoundary'
import type { RouteProps } from '../../app/useQueryState'

/** Home's Overview view (`#/dashboard?view=overview`): the bands that have no place in the
 *  attention stack, the view Home opens on (`Home.tsx`). Needs You, Running Now and Recent and
 *  completed took what this page's To triage, Active work and launcher held, the toolbar's needs-you
 *  badge took its count pills, and its suggestions belong to the stack's empty state. What is left
 *  here is what a person comes to look at rather than to answer: the Agent world, the desktop and
 *  browser live views, tasks, Discover, pinned artifacts and recent activity.
 *
 *  Boundary-less bands under hairline section labels: content owns the space, chrome doesn't. */
export function HomeOverview(route: RouteProps) {
  return (
    <DashboardLiveProvider>
      {/* The column is the Overview's ENTRANCE GROUP: its bands arrive together rather than as
          widgets landing one by one. It sits above every band's own loading state on purpose (the
          replay rule in `ui/motion/Entrance`), so a band's data landing re-renders inside a
          mounted group instead of remounting it and re-running the entrance. */}
      <EntranceGroup className="mx-auto flex w-full flex-col gap-2xl px-l py-xl">
        {/* Every band renders inside a boundary (in `Section`), so a widget that throws shows
            "Couldn't show …" with Retry in its place and the other bands keep working. */}
        <EntranceRegion className="min-w-0">
          <Section label="Tasks" icon={ListTodo}>
            <TasksWidget {...route} />
          </Section>
        </EntranceRegion>

        {/* `min-w-0` on each region: the region is the column's flex item, and a flex item
            defaults to `min-width: auto`, which would let a long widget line push the column wider
            than the content width instead of truncating. */}

        {/* Agent world: the live picture of the agents at work as a scene you glance at rather
            than a list you read. It takes its data from `useAgentActivity()` only. */}
        <EntranceRegion className="min-w-0">
          <Section label="Agent world" icon={Orbit}>
            <AgentWorld />
          </Section>
        </EntranceRegion>

        {/* Discover: a curated spotlight of the parts of PersonalClaw you haven't tried yet, each a
            deep link. The full grouped list is the Discover hub (the widget's "See all"). */}
        <EntranceRegion className="min-w-0">
          <Section label="Discover" icon={Compass}>
            <Discover {...route} />
          </Section>
        </EntranceRegion>

        {/* Pinned artifacts: a pin is a slug in a list that THIS component renders. */}
        <EntranceRegion className="min-w-0">
          <Section label="Pinned artifacts" icon={Package}>
            <PinnedArtifacts {...route} />
          </Section>
        </EntranceRegion>

        {/* Desktop live view: what the agent is doing on this desktop, the attempt feed, plus the
            optional live-view mirror and cursor-motion overlay. Observation only; the toggles change
            what this page draws, never what the agent can do. */}
        <EntranceRegion className="min-w-0">
          <Section label="Desktop live view" icon={Monitor}>
            <DesktopLiveView />
          </Section>
        </EntranceRegion>

        {/* Browse live view: watch an unattended browse step by step, stop it in one click, and see
            a persistent banner when a saved session expires. A grant it asks for is answered in
            Needs You, beside every other ask. */}
        <EntranceRegion className="min-w-0">
          <Section label="Browse live view" icon={Globe}>
            <BrowseMirror />
          </Section>
        </EntranceRegion>

        <EntranceRegion className="min-w-0">
          <Section label="Recent activity" icon={History}>
            <ScheduleWidget {...route} />
          </Section>
        </EntranceRegion>
      </EntranceGroup>
    </DashboardLiveProvider>
  )
}

// ── Bare section header ───────────────────────────────────────────────────────
// A section is a label + hairline, NOT a bordered card. Content flows directly
// beneath, owning the horizontal space.
function Section({ label, icon: Icon, children }: {
  label: string; icon: LucideIcon; children: ReactNode
}) {
  return (
    <section className="flex min-w-0 flex-col gap-s">
      <div className="flex items-center gap-s">
        <Icon size={14} className="shrink-0 text-on-surface-low" />
        {/* h2, not h3: this is a top-level section directly under the page's h1 greeting, and
            every one of the dashboard's sections renders through here. As h3 the page read
            h1 -> h3 seven times over, skipping a level for a screen-reader user navigating by
            heading. `data-type="label-l"` carries the size and weight, so the level is purely
            structural — the heading looks identical. */}
        <h2 data-type="label-l" className="text-on-surface-var">{label}</h2>
        <span className="h-px flex-1 bg-outline-variant/40" />
      </div>
      {/* Under the band's heading, so a band that throws keeps its label and says it could not show. */}
      <WidgetBoundary what={`the ${label} section`}>{children}</WidgetBoundary>
    </section>
  )
}


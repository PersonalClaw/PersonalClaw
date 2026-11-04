import { History, ListTodo, Compass, Package, Monitor, Globe } from 'lucide-react'
import { DashboardLiveProvider } from './DashboardLive'
import { AgentDay } from './agents/AgentDay'
import { Section } from './Section'
import { TasksWidget } from './widgets/TasksWidget'
import { Discover } from './widgets/Discover'
import { PinnedArtifacts } from './widgets/PinnedArtifacts'
import { DesktopLiveView, useDesktopLive } from './widgets/DesktopLiveView'
import { BrowseMirror } from './widgets/BrowseMirror'
import { ScheduleWidget } from './widgets/ScheduleWidget'
import { EntranceGroup, EntranceRegion } from '../../ui/motion'
import type { RouteProps } from '../../app/useQueryState'

/** Home's Overview view (`#/dashboard?view=overview`): the bands that have no place in the
 *  attention stack, the view Home opens on (`Home.tsx`). Needs You, Running Now and Recent and
 *  completed took what this page's To triage, Active work and launcher held, the toolbar's needs-you
 *  badge took its count pills, and its suggestions belong to the stack's empty state. What is left
 *  here is what a person comes to look at rather than to answer: your agents' day, the desktop and
 *  browser live views, tasks, Discover, pinned artifacts and recent activity.
 *
 *  Boundary-less bands under hairline section labels: content owns the space, chrome doesn't. */
export function HomeOverview(route: RouteProps) {
  const desktop = useDesktopLive()
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

        {/* Your agents today: a row per agent on the hours around now, what each did, does, asks
            and runs next. It holds its own heading, which carries its filter. */}
        <EntranceRegion className="min-w-0">
          <AgentDay />
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

        {/* Desktop live view: what the agent is doing on this desktop, while it is doing it — the
            walked window, the fake cursor and the attempt feed. Observation only. Not drawn at all
            while nothing drives the desktop: the gateway says when it does (`useDesktopLive`). */}
        {desktop.live && desktop.view && (
          <EntranceRegion className="min-w-0">
            <Section label="Desktop live view" icon={Monitor}>
              <DesktopLiveView view={desktop.view} />
            </Section>
          </EntranceRegion>
        )}

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

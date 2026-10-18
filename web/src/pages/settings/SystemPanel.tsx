import { SystemLiveProvider } from './SystemLive'
import { SystemHealth } from '../dashboard/widgets/SystemHealth'
import { WidgetBoundary } from '../../app/ErrorBoundary'
import { TextLink } from '../../ui/TextLink'
import { PanelHeader, Section } from './settingsUI'
import type { RouteProps } from '../../app/useQueryState'

/** Settings › System: the machine PersonalClaw runs on, read live.
 *
 *  The health strip: uptime, version, cpu with its sparkline, memory, network, disk, load,
 *  triggers and subagents, the Update action, the YOLO notice and the Doctor rollup. It used to sit
 *  on Home, where it competed with the owner's work for the first screen; Home now shows only what
 *  waits on the owner, and this is where the machine's own state is read.
 *
 *  What is holding memory (the resident local models, their Unload, and the pressure meter) has
 *  one home, Settings › Models, and this page links there rather than drawing a second copy.
 *
 *  The strip reads the machine's own feed (`SystemLive`), mounted here alone, so this page polls
 *  the status, the host metrics and the Doctor and none of Home's lanes. `@container` makes the
 *  strip's wrap rules track this column's width. */
export function SystemPanel({ navigate }: { navigate?: RouteProps['navigate'] }) {
  const go = navigate ?? (() => {})
  const route = { sub: '', navEpoch: 0, navigate: go, query: {}, setQuery: () => {} }
  return (
    <div>
      <PanelHeader title="System" hint="This machine, read live: health, versions, and what the agent is running." />
      <Section title="Health">
        <div className="@container rounded-lg border border-outline-variant/50 bg-surface-low/70 px-l py-s">
          <SystemLiveProvider>
            <WidgetBoundary what="the system health strip"><SystemHealth {...route} /></WidgetBoundary>
          </SystemLiveProvider>
        </div>
        <p data-type="body-m" className="mt-m text-on-surface-low">
          Which local models are holding memory, with Unload and the memory meter, is in{' '}
          <TextLink onClick={() => go('settings/models')}>Settings › Models</TextLink>.
        </p>
      </Section>
    </div>
  )
}

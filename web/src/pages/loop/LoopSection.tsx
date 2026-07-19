import { LoopComposer } from './LoopComposer'
import { api } from '../../lib/api'
import { invalidateCache } from '../../lib/useCachedData'
import type { RouteProps } from '../../app/useQueryState'
import type { LoopKind } from '../../lib/api'

/** The unified Loop front door (Slice 3). Hosts the kind-sliding composer; on create
 *  it routes into the kind's existing planning/cockpit screens — which already resolve
 *  the loop by id regardless of kind (goal+code are on the unified client). General +
 *  Design cockpits land in later slices; until then they route to the goal-style
 *  cockpit, which renders any kind's spine.
 *
 *  Deep links: #/loop = the composer. A created loop deep-links under its kind's
 *  section (#/loops/<id> for goal/general/design, #/code/<id> for code) so the
 *  existing cockpits + planning views are reused with no duplication. Slice-3 B2 will
 *  alias those under one address; this sub-step is the composer itself. */
export function LoopSection({ navigate, query }: RouteProps) {
  const routeCreated = async (loopId: string, kind: LoopKind, planning: boolean) => {
    // Invalidate the cached lists so the new loop appears immediately on its list.
    invalidateCache('loops')
    // MINIMAL rigor (planning=false) is the magic path: there's no Plan Review, so
    // start the loop now and drop straight into its cockpit. (Suggested skills/
    // workflows were already persisted into the create body by the composer.)
    //
    // NON-minimal rigor routes into the kind's planning walkthrough. The section that
    // owns the route (LoopsSection / CodeSection) only opens the walkthrough when the
    // loop's STATUS is `planning`/`review` — a freshly-created loop is `ready`, so we
    // must kick the plan to flip ready→planning BEFORE navigating, or the by-id resume
    // shows the cockpit's Start button and silently skips the walkthrough the rigor
    // earned. plan/start is idempotent over prelaunch statuses; the walkthrough view
    // re-uses the same session.
    if (!planning) await api.uLoopAction(loopId, 'start').catch(() => {})
    else await api.uLoopPlanStart(loopId).catch(() => {})
    navigate(kind === 'code' ? `code/${loopId}` : `loops/${loopId}`)
  }
  // Optional preselect via query (?project=<id>&kind=<kind>) — e.g. the Code cockpit's
  // "New target" reuse-workspace flow deep-links here with its source project + code
  // kind, and the composer inherits that project's bound codebase.
  const kindParam = query.kind as LoopKind | undefined
  const validKind = kindParam && ['general', 'goal', 'code', 'design', 'research'].includes(kindParam) ? kindParam : undefined
  return <LoopComposer onCreated={routeCreated} onHistory={() => navigate('loops/history')}
    initialProjectId={query.project || ''} initialKind={validKind} initialWorkspace={query.ws || ''} />
}

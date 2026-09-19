import type { RouteProps } from '../../app/useQueryState'
import { WorkflowDefDetail } from './WorkflowDefDetail'
import { WorkflowRunDetail } from './WorkflowRunDetail'
import { WorkflowsListPage } from './WorkflowsListPage'

/** Workflows — the route root (WORKFLOWS-V2 Slice 7b).
 *
 *  Selection state IS the URL, matching the other entity sections:
 *    · `#/workflows`                  → the list (runs by default)
 *    · `#/workflows/runs/<run_id>`    → one run, live
 *    · `#/workflows/runs/<id>?node=<node_id>` → that run with the node inspector open on one node
 *    · `#/workflows/defs/<name>`      → one definition
 *
 *  Deep-linkable on purpose: a needs-input notification, a chat card, and the
 *  `[ACTIVE WORKFLOWS]` context block all want to point a human at one specific run, and a
 *  section that held selection in component state could not be linked to. */
export function WorkflowsSection(props: RouteProps) {
  const { sub, navigate } = props
  const parts = (sub || '').split('/').filter(Boolean)
  const back = () => navigate('workflows')

  if (parts[0] === 'runs' && parts[1]) {
    // `?node=<id>` is the chat card's active-node deep link (WV-10). It rides the QUERY rather than
    // a path segment because the grammar above reserves `?query` for exactly this — "which detail
    // panel is open" — so the node inspector became addressable without a second route.
    return <WorkflowRunDetail runId={parts[1]} onBack={back} deepLinkNodeId={props.query.node || null} />
  }
  if (parts[0] === 'defs' && parts[1]) {
    return (
      <WorkflowDefDetail
        name={decodeURIComponent(parts[1])}
        onBack={back}
        onStarted={(runId) => navigate(`workflows/runs/${runId}`)}
      />
    )
  }
  return <WorkflowsListPage {...props} />
}

import type { RouteProps } from '../../app/useQueryState'
import { WorkflowDefDetail } from './WorkflowDefDetail'
import { WorkflowDefEditor } from './WorkflowDefEditor'
import { WorkflowRunDetail } from './WorkflowRunDetail'
import { WorkflowsListPage } from './WorkflowsListPage'

/** Workflows — the route root (WORKFLOWS-V2 Slice 7b).
 *
 *  Selection state IS the URL, matching the other entity sections:
 *    · `#/workflows`                  → the list (runs by default)
 *    · `#/workflows/runs/<run_id>`    → one run, live
 *    · `#/workflows/runs/<id>?node=<node_id>` → that run with the node inspector open on one node
 *    · `#/workflows/defs/<name>`      → one definition
 *    · `#/workflows/defs/<name>/edit` → the editor on it (a copy, for a shipped template)
 *    · `#/workflows/defs/<name>/edit?from=<version>` → the editor on one recorded version (a restore)
 *
 *  Deep-linkable on purpose: a needs-input notification, a chat card, and the
 *  `[ACTIVE WORKFLOWS]` context block all want to point a human at one specific run, and a
 *  section that held selection in component state could not be linked to. */
export function WorkflowsSection(props: RouteProps) {
  const { sub, navigate } = props
  const parts = (sub || '').split('/').filter(Boolean)
  const back = () => navigate('workflows')

  if (parts[0] === 'runs' && parts[1]) {
    // `?node=<id>` is the chat card's active-node deep link. It rides the QUERY rather than
    // a path segment because the grammar above reserves `?query` for exactly this — "which detail
    // panel is open" — so the node inspector became addressable without a second route.
    return (
      <WorkflowRunDetail
        runId={parts[1]}
        onBack={back}
        onOpenRun={(id) => navigate(`workflows/runs/${id}`)}
        deepLinkNodeId={props.query.node || null}
      />
    )
  }
  if (parts[0] === 'defs' && parts[1]) {
    const name = decodeURIComponent(parts[1])
    const defPath = `workflows/defs/${encodeURIComponent(name)}`
    if (parts[2] === 'edit') {
      const from = Number.parseInt(props.query.from ?? '', 10)
      return (
        <WorkflowDefEditor
          key={`${name}@${from || 'current'}`}
          name={name}
          fromVersion={from > 0 ? from : undefined}
          onCancel={() => navigate(defPath)}
          onSaved={(saved) => navigate(`workflows/defs/${encodeURIComponent(saved)}`, { replace: true })}
        />
      )
    }
    return (
      <WorkflowDefDetail
        name={name}
        onBack={back}
        onStarted={(runId) => navigate(`workflows/runs/${runId}`)}
        onEdit={(version) => navigate(`${defPath}/edit${version ? `?from=${version}` : ''}`)}
      />
    )
  }
  return <WorkflowsListPage {...props} />
}

import { ApiError, api, type WorkflowCascadePreview } from '../../lib/api'
import { confirm } from '../../ui/dialog'
import { reentrySummary } from './revalidate'

/** The steps a re-entry verb would reset, when the engine refused it pending the owner's consent
 *  (`confirmation_required` carrying `detail.preview`), or `null` for any other refusal. */
export function confirmationPreview(error: unknown): WorkflowCascadePreview | null {
  if (!(error instanceof ApiError) || error.code !== 'confirmation_required') return null
  const detail = error.detail
  if (!detail || typeof detail !== 'object' || !('preview' in detail)) return null
  return (detail as { preview?: WorkflowCascadePreview }).preview ?? null
}

/** Re-run one step of a LIVE run, and the steps that read its output (`POST …/rewind`).
 *
 *  A re-run that resets other steps is refused with the steps it would reset, and this asks the
 *  owner first, in those words, then sends it again with their consent. The run page's Re-run and
 *  the Inbox's "Run this step again" are this one flow, so the two cannot ask differently.
 *
 *  Resolves `true` once the re-run is applied and `false` when the owner declines. Any other
 *  refusal (a run that has ended has no live controller: `run_not_live`) is thrown as the server
 *  said it. */
export async function rewindNode(runId: string, nodeId: string): Promise<boolean> {
  try {
    await api.rewindWorkflowRun(runId, { node_id: nodeId })
    return true
  } catch (error) {
    const preview = confirmationPreview(error)
    if (preview === null) throw error
    const ok = await confirm({
      title: `Re-run "${nodeId}"?`,
      body: `${reentrySummary('rewind', preview)} Previous outputs are archived, not lost.`,
      confirmLabel: 'Re-run',
    })
    if (!ok) return false
    await api.rewindWorkflowRun(runId, { node_id: nodeId, confirm_cascade: true })
    return true
  }
}

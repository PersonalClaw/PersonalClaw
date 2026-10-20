import { AlertTriangle } from 'lucide-react'
import type { SavedAgent } from '../../lib/api'
import type { ModelOption } from '../../lib/agents'

/** An agent's pinned model that cannot run, as the Agents page says it.
 *
 *  The pin is KEPT — the backend never rewrites it — and until it is changed the agent answers on
 *  the chat model, with each reply and room turn saying which model answered. This page is where
 *  the pin was chosen, so it is where the fix is offered. The reason comes from the server
 *  (`GET /api/agents` → `model_unavailable`), asked through the same rule the runtime uses, so
 *  the page and the turn cannot disagree about one pin. */
export type ModelUnavailable = NonNullable<SavedAgent['model_unavailable']>

/** The picker option for a pin that is not among the models it offers.
 *
 *  Without it the Combobox fell back to its placeholder — "Auto — …" — and the editor claimed the
 *  agent was on Auto while every surface around it showed the pin. Listed as what it is. */
export function unavailableModelOption(model: string, options: ModelOption[]): ModelOption[] {
  if (!model || options.some((o) => o.value === model)) return []
  return [{ value: model, label: `${model} (unavailable)`, group: 'Unavailable' }]
}

/** The inline note under an agent's model. `fixHere` says where on this surface a new model is
 *  chosen ("above" beside an editor, "with Edit" on the read-only panel). */
export function ModelUnavailableNote({ model, unavailable, fixHere }: {
  model: string; unavailable: ModelUnavailable; fixHere: string
}) {
  return (
    <div role="note" data-testid="agent-model-unavailable" data-type="body-s"
      className="flex items-start gap-s rounded-lg bg-warn/10 px-m py-s text-warn">
      <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="font-mono">{model}</span> is unavailable: {unavailable.why}. Until you choose another,
        this agent answers on your chat model, and each reply says which model answered. Choose another model {fixHere},
        or {unavailable.fix}.
      </span>
    </div>
  )
}

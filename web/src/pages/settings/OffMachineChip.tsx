import type { AvailableModel } from '../../lib/api'
import { StatusPill } from '../../ui/StatusPill'

/** The chip's visible words: short, because it sits in a narrow row beside the model's name. */
export const OFF_MACHINE_LABEL = 'off this machine'

/** What the chip means, in full: its accessible name's second half, and its title. */
export const OFF_MACHINE_REASON =
  'Runs on another machine, so its prompts leave this one: they get the outbound scan your ' +
  'settings ask for, and it is not counted as free unless its price is $0.'

/** A model a model server lists that does not run on this machine (`runs_here: false`): one an
 *  Ollama answers from its cloud, or any model of a server on another machine. It stands where a
 *  local model's fit chip would, which such a model has none of. Nothing at all for a model that
 *  runs here, or for a hosted provider's model, whose rows carry no `runs_here`.
 *
 *  `role="img"` + `aria-label` is this repo's form for a chip whose label carries state (the fit
 *  chip's, in `LocalModelManager`), and it is grounded on the resting tier the fit chip is, because
 *  it sits in the same rows. */
export function OffMachineChip({ model }: { model: AvailableModel }) {
  if (model.runs_here !== false) return null
  const described = `Off this machine — ${OFF_MACHINE_REASON}`
  return (
    <StatusPill tone="info" groundedOn="var(--color-surface-container)"
      role="img" aria-label={described} title={OFF_MACHINE_REASON}>
      {OFF_MACHINE_LABEL}
    </StatusPill>
  )
}

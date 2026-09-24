import type { SessionTemplate, ReasoningEffort } from '../../lib/api'
import type { ComposerValue } from '../../ui/composer/types'

/** The composer selection a saved starter contributes (SESSION-MANAGEMENT S3 T3.2 / SM-7).
 *
 *  Only the fields the template ACTUALLY carries appear in the patch: a starter saved with no
 *  model must not silently reset the user's current pick to "Auto". That is the whole subtlety
 *  of "prefills the composer selection" — a partial starter is a partial patch, never a full
 *  overwrite — so it lives in one pure function the composer applies and a test can pin.
 *
 *  The `first_prompt` is deliberately NOT here: it prefills the composer INPUT (a separate piece
 *  of state, and the thing that enables Send), not the selection. Keeping the two apart is what
 *  lets `applyTemplate` apply the selection only when there is one to apply. */
export function sessionTemplatePatch(t: SessionTemplate): Partial<ComposerValue> {
  const patch: Partial<ComposerValue> = {}
  if (t.agent) patch.agent = t.agent
  if (t.model) patch.model = t.model
  if (t.reasoning_effort) patch.reasoning = t.reasoning_effort as ReasoningEffort
  return patch
}

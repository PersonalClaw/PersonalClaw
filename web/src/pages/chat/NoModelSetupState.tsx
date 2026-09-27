import { Sparkles } from 'lucide-react'
import { Button } from '../../ui/Button'
import { fvs } from '../../design/fontWeight'

/** Where "Set up a model" goes — the same destination DegradedChip's "Bind a
 *  model" nudge uses, so the two agree. `MODELS_PATH` is the router-path form the
 *  page hands to `navigate()`; `MODELS_ROUTE` is the same destination as a hash
 *  href (what DegradedChip's link carries). */
export const MODELS_PATH = 'settings/models'
export const MODELS_ROUTE = `#/${MODELS_PATH}`

/** True when a turn-level error is the "no model configured yet" case — a fresh
 *  instance where no provider declares the capability the chat use case needs.
 *
 *  Keyed on the STABLE render() of `AgentError(code=ERR_MODEL_UNRESOLVED)` raised by
 *  `resolve_provider_for_use_case` (src/personalclaw/providers/provider_bridge.py)
 *  on the final "No provider configured for use case" path. That WHAT/WHY prose is
 *  the tripwire the co-located test pins against, so a backend reword fails the test
 *  rather than silently reverting this surface to the raw envelope.
 *
 *  Deliberately NOT the stale-pin variant ("… cannot be built" / "isn't available"):
 *  there a model WAS chosen and later went missing, which is a different situation
 *  than first-touch setup and keeps its own fixing-toned message. */
export function isNoModelSetupError(text: string | null | undefined): boolean {
  if (!text) return false
  const t = text.toLowerCase()
  return (
    t.includes('no model provider resolves for use case') ||
    t.includes('no provider in config.json declares the capability')
  )
}

/** The provider a no-model refusal names when the cause is that no model is CHOSEN for it —
 *  an instance saved from the Add-instance form without a Default Model, with nothing bound in
 *  Settings → Models — or `null` for every other cause. A model provider IS connected then, so
 *  "No model connected yet" would be false about it.
 *
 *  Keyed on the stable WHY of `no_model_chosen` (src/personalclaw/llm/registry.py), which the
 *  co-located test pins verbatim, as the WHAT above is. */
export function noModelChosenFor(text: string | null | undefined): string | null {
  const m = /^WHY: no model is chosen for “(.+)”$/m.exec(text ?? '')
  return m ? m[1] : null
}

/** WT-04: the calm setup empty-state shown in the transcript when a turn cannot run
 *  because no model is connected yet. Replaces the raw WHAT/WHY/FIX danger block —
 *  which read as a stack dump on a newcomer's very first screen — with one plain
 *  sentence, the way forward as a CTA, and the full envelope tucked behind a
 *  collapsed disclosure (charter: calm setup-framing, error-shape rule). When a provider is
 *  connected but no model is chosen for it, the sentence names that provider instead. */
export function NoModelSetupState({ detail, onSetup }: { detail: string; onSetup: () => void }) {
  const provider = noModelChosenFor(detail)
  return (
    <div
      role="status"
      className="my-1 rounded-lg bg-surface-container px-3.5 py-3"
      style={{ border: '1px solid var(--color-outline-variant)' }}
    >
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-lg"
          style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}
        >
          <Sparkles size={18} className="text-primary" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p data-type="title-m" className="text-on-surface" style={fvs(600)}>
            {provider ? `No model chosen for ${provider}` : 'No model connected yet'}
          </p>
          <p data-type="body-s" className="mt-0.5 text-on-surface-var">
            {provider
              ? 'Choose which of its models to chat with in Settings → Models.'
              : 'Connect a model to start chatting. You can set one up in Settings → Models.'}
          </p>
          <div className="mt-2.5">
            <Button size="sm" onClick={onSetup}>
              {provider ? 'Choose a model' : 'Set up a model'}
            </Button>
          </div>
          <details className="mt-2">
            <summary data-type="caption" className="cursor-pointer text-on-surface-low hover:text-on-surface-var">
              Technical details
            </summary>
            <pre data-type="caption" className="mt-1.5 whitespace-pre-wrap break-words rounded-md bg-surface-high px-2.5 py-2 font-mono text-on-surface-low">
              {detail}
            </pre>
          </details>
        </div>
      </div>
    </div>
  )
}

import { Sparkles } from 'lucide-react'
import { Button } from '../../ui/Button'
import { fvs } from '../../design/fontWeight'

/** Where "Set up a model" goes — the same destination DegradedChip's "Bind a
 *  model" nudge uses, so the two agree. Hash route; assigning it drives the app's
 *  `useHashRoute` router. */
export const MODELS_ROUTE = '#/settings/models'

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

/** WT-04: the calm setup empty-state shown in the transcript when a turn cannot run
 *  because no model is connected yet. Replaces the raw WHAT/WHY/FIX danger block —
 *  which read as a stack dump on a newcomer's very first screen — with one plain
 *  sentence, the way forward as a CTA, and the full envelope tucked behind a
 *  collapsed disclosure (charter: calm setup-framing, error-shape rule). */
export function NoModelSetupState({ detail }: { detail: string }) {
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
          <p className="text-on-surface text-[0.9375rem]" style={fvs(600)}>No model connected yet</p>
          <p className="mt-0.5 text-on-surface-var text-[0.8125rem] leading-snug">
            Connect a model to start chatting. You can set one up in Settings → Models.
          </p>
          <div className="mt-2.5">
            <Button size="sm" onClick={() => { window.location.hash = MODELS_ROUTE }}>
              Set up a model
            </Button>
          </div>
          <details className="mt-2">
            <summary className="cursor-pointer text-on-surface-low text-[0.75rem] hover:text-on-surface-var">
              Technical details
            </summary>
            <pre className="mt-1.5 whitespace-pre-wrap break-words rounded-md bg-surface-high px-2.5 py-2 font-mono text-on-surface-low text-[0.6875rem] leading-snug">
              {detail}
            </pre>
          </details>
        </div>
      </div>
    </div>
  )
}

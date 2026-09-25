import { api, type OnboardingModelCheck, type ProviderTestResult } from '../../lib/api'
import { boundModelLabel } from '../../lib/modelRef'

/** What onboarding may say about the chat model, decided in ONE place.
 *
 *  - `ok` — chat builds and whatever answers it answers: say "ready", naming `model` (the
 *    bound model, `''` for the implicit fallback) and whether it is the small `floor` model.
 *  - `refused` — the build refused; `check` is the bridge's own `why`/`fix`, relayed verbatim.
 *  - `silent` — the build passed, but the provider it resolves to did not answer its
 *    connection test; `message` is that test's own sentence.
 *  - `unknown` — a check could not run at all. "We do not know" is never reported as either
 *    of the two failures above, and never as ready.
 */
export type ChatModelVerdict =
  | { kind: 'ok'; model: string; floor: boolean }
  | { kind: 'refused'; check: Extract<OnboardingModelCheck, { ok: false }> }
  | { kind: 'silent'; provider: string; message: string }
  | { kind: 'unknown'; message: string }

/** Onboarding's one proof that chat works, read by the model step's verification and by the
 *  flow's seeding of a re-entered run, so the two cannot disagree about "ready".
 *
 *  Two questions, because each answers what the other cannot:
 *
 *  1. `GET /api/onboarding/model-check` BUILDS what chat builds — the configuration,
 *     registration, capability, credential and readiness family, in the bridge's words.
 *  2. A build is not a call: a provider pointed at an address nothing listens on builds fine.
 *     So the entry that verdict names (`provider`) is asked whether it ANSWERS, through
 *     `POST /api/model-providers/{name}/test` — the probe Settings already uses. Measured on a
 *     real image before this: an Ollama entry saved at the default `http://localhost:11434`
 *     with nothing listening, and nothing bound, reloaded into "A chat model is configured —
 *     you're ready", after which every background run failed.
 *
 *  The small floor model runs in-process, so it has no address to ask; a type with no
 *  connectivity probe answers `no_probe` (`ok: true`), and has nothing more to learn here. */
export async function checkChatModel(): Promise<ChatModelVerdict> {
  let r: OnboardingModelCheck
  try { r = await api.onboardingModelCheck() }
  catch (e) { return { kind: 'unknown', message: thrownMessage(e) || 'The check could not run.' } }
  if (!r.ok) return { kind: 'refused', check: r }
  if (!r.floor && r.provider) {
    let probe: ProviderTestResult
    try { probe = await api.testModelProvider(r.provider) }
    catch (e) {
      return { kind: 'unknown', message: `${r.provider}'s connection test could not run: ${thrownMessage(e) || 'the request failed'}` }
    }
    if (!probe.ok) return { kind: 'silent', provider: r.provider, message: probe.message || 'Its connection test failed.' }
  }
  // 🔴 THE MODEL IS READ OFF THE VERDICT, not off whichever control did the binding. `bound` is
  // `active_model_refs('chat')` — the contents of `active_models.json` — so a first pass and a
  // re-entered one name the same model (#3528). An empty answer means nothing is explicitly
  // bound (`source: 'fallback'`), and the caller names the mechanism rather than a choice.
  return { kind: 'ok', model: boundModelLabel(r.bound), floor: !!r.floor }
}

/** A THROWN api-client error's text, unwrapping the JSON error body the client
 *  stringifies into `Error.message`. Distinct from `lib/errText`, which turns a failed
 *  `Response` into user-facing copy — this one reads an already-rejected promise.
 *  Never includes a submitted credential: only the server's own message. */
export function thrownMessage(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e ?? '')
  try { const p = JSON.parse(raw); return String(p?.error ?? raw) } catch { return raw }
}

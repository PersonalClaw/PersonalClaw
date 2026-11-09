import { useEffect, useState } from 'react'
import { api } from '../../../lib/api'
import { notify } from '../../../app/appSdk'
import { Button } from '../../../ui/Button'
import { productTourRunning } from '../../../app/onboarding/tourLaunch'
import explanationsTable from '../../../../../src/personalclaw/explanations.json'

/** The answers: the Explanations choices (`explanations.json`, the Settings row's table) other
 *  than "No preference", each as the table names it. */
const ANSWERS = (explanationsTable as { id: string; label: string }[]).filter((r) => r.id !== 'default')

/** "How should I explain things?" — the one optional question, asked once.
 *
 *  Shown on a new chat once onboarding is done and the question is still open
 *  (`explain_asked` in the onboarding state). A card in the hero, never a dialog over the
 *  composer: the chat is usable whether or not it is answered. An answer is the owner's
 *  Explanations default (`agent.explain`, written as the Settings row writes it), and the server
 *  records the question as asked with that write; Skip only claims the question. Either way the
 *  card does not come back. A state that cannot be read shows nothing, rather than asking someone
 *  who may already have answered. While the product tour walks the app it asks nothing, of the
 *  owner or the gateway (`tourLaunch.productTourRunning`): the next new chat after the tour
 *  asks. */
export function ExplainCard() {
  const [open, setOpen] = useState(false)
  /** The answer being saved (a choice's id, or `skip`); every button waits on it, and its own
   *  says so. */
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    if (productTourRunning()) return
    let live = true
    api.onboarding()
      .then((s) => { if (live) setOpen(s?.step === 'done' && s?.explain_asked === false) })
      .catch(() => {})
    return () => { live = false }
  }, [])

  if (!open) return null

  const answer = (a: string) => {
    setBusy(a)
    const saved: Promise<unknown> = a === 'skip' ? api.answerExplain('skip') : api.patchConfig('agent.explain', a)
    saved
      .then(() => setOpen(false))
      .catch((e) => notify(`Couldn't save how you like things explained: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(null))
  }

  return (
    <section aria-labelledby="explain-card-title"
      className="w-full rounded-lg border border-outline-variant/40 bg-surface-container px-l py-m flex flex-col gap-s">
      <h2 id="explain-card-title" data-type="title-s" className="text-on-surface">How should I explain things?</h2>
      <p data-type="body-s" className="text-on-surface-low">
        Your answer becomes your Explanations default. Change it any time in Settings → Agent → Agent defaults → Explanations.
      </p>
      <div className="flex flex-wrap gap-s">
        {ANSWERS.map(({ id: a, label }) => (
          <Button key={a} variant="tonal" size="sm" loading={busy === a} disabled={busy !== null} onClick={() => answer(a)}>{label}</Button>
        ))}
        <Button variant="ghost" size="sm" loading={busy === 'skip'} disabled={busy !== null} onClick={() => answer('skip')}>Skip</Button>
      </div>
    </section>
  )
}

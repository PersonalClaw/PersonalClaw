import { useState } from 'react'
import { Brain } from 'lucide-react'
import { api } from '../../lib/api'
import { Button } from '../../ui/Button'
import { InlineError } from '../../ui/InlineError'

/** What the Learning page says while `learning.enabled` is off, in place of the four panels that
 *  switch covers (capture week, identity report, health and proposals).
 *
 *  🔑 ONE SENTENCE AND THE WAY BACK ON. Those four reads answer `{"enabled": false}` while the
 *  switch is off; they used to 404, and the page drew four red "Couldn't load" alerts whose Retry
 *  could never succeed, because a switch that is off does not flip when the fetch is repeated. The
 *  button is the PATCH itself (`learning.enabled` is in the config allowlist for exactly this), so
 *  there is nothing to go and find: before it, the only way back on was editing `config.json`.
 *
 *  Not `role="alert"`, and no Retry: a decided answer is not unrequested bad news. The one failure
 *  here is the button's own write, and that is said beside the button.
 *
 *  The copy is the gate's own behaviour (`learning/gate.py`: the per-turn, session-end, run-end
 *  and capture cadences are all denied with `learning_disabled` while it is off), and turning it
 *  off deletes nothing, which is why the sentence can promise that what was learned stays. */
export function LearningOff({ onTurnedOn }: { onTurnedOn: () => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const turnOn = async () => {
    setBusy(true)
    setErr('')
    try {
      await api.patchConfig('learning.enabled', true)
    } catch (e) {
      setErr(`Couldn't turn learning on: ${String((e as Error)?.message || e)}`)
      setBusy(false)
      return
    }
    setBusy(false)
    onTurnedOn()
  }

  return (
    <section aria-labelledby="learning-off-heading" className="flex flex-col gap-s rounded-lg bg-surface-container px-l py-l">
      <h2 id="learning-off-heading" data-type="title-m" className="flex items-center gap-s text-on-surface">
        <Brain size={16} className="text-on-surface-var" aria-hidden /> Learning is off
      </h2>
      <p className="text-on-surface-low text-[0.8125rem]">
        While it is off, nothing new is learned from your chats or runs. What was already learned
        stays.
      </p>
      <div>
        <Button size="sm" onClick={turnOn} loading={busy} loadingLabel="Turning learning on…">Turn learning on</Button>
      </div>
      {err && <InlineError icon onDismiss={() => setErr('')}>{err}</InlineError>}
    </section>
  )
}

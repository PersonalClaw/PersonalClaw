import { useState } from 'react'
import { PauseCircle, Play } from 'lucide-react'
import { api, type RestoreHold } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { failureSentence } from '../../app/reportingWrite'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import { BUSY_REASON } from '../../ui/unavailable'

/** The automations a replace restore holds (`triggers/restore_hold.py`), and Resume.
 *
 *  A restore writes this home back from a snapshot, and every automation that ran on its own where
 *  the snapshot was taken came back with it. Restored onto a second machine they ran beside the
 *  original, which still ran them, so every brief and digest went out twice. So the restore holds
 *  each one, switched off, and the row says where the snapshot came from — what decides what may
 *  be said about why it waits, and when resuming it is safe. */

/** The hold whose words are the most careful, when holds from more than one restore are present:
 *  another home's snapshot over one that does not say, over this home's own. */
export function mostCautious(holds: RestoreHold[]): RestoreHold {
  for (const hold of ['another_home', 'unknown', 'this_home'] as const) {
    if (holds.includes(hold)) return hold
  }
  return ''
}

/** Why the automations wait, and when to resume them, for the notice above the list. */
export function restoreHoldSentence(count: number, hold: RestoreHold): string {
  const counted = count === 1 ? '1 automation' : `${count} automations`
  const them = count === 1 ? 'it' : 'them'
  const same = count === 1 ? 'the same automation runs' : 'the same automations run'
  if (hold === 'this_home') {
    return `${counted} came back paused when this home was restored from its own snapshot. Nothing else runs ${them}: resume ${them} when you are ready.`
  }
  const from = hold === 'another_home'
    ? 'a snapshot of another PersonalClaw home'
    : 'a snapshot that does not say which PersonalClaw home it comes from'
  return `${counted} came back paused when this home was restored from ${from}. If that home is still running, ${same} there too: resume ${them} here once it is retired.`
}

/** The same, for one automation's panel. */
export function restoreHoldNoteSentence(hold: RestoreHold): string {
  if (hold === 'this_home') {
    return 'This home was restored from its own snapshot, so nothing else runs it. Resume it when you are ready.'
  }
  const from = hold === 'another_home'
    ? 'a snapshot of another PersonalClaw home'
    : 'a snapshot that does not say which PersonalClaw home it comes from'
  return `This home was restored from ${from}. If that home is still running, this automation runs there too: resume it here once that home is retired.`
}

/** Above the Triggers list: how many automations the restore holds, why, and Resume all. Nothing
 *  when none is held. `holds` is each held automation's `restore_hold`; `onResumed` re-reads the
 *  lists. */
export function RestoreHoldNotice({ holds, onResumed }: { holds: RestoreHold[]; onResumed: () => void }) {
  const [busy, setBusy] = useState(false)
  if (holds.length === 0) return null

  const resumeAll = async () => {
    setBusy(true)
    try {
      const r = await api.resumeRestoredTriggers()
      const n = r.resumed.length
      if (n > 0) notify(n === 1 ? `Resumed ${r.resumed[0].name}.` : `Resumed ${n} automations.`, 'success')
      for (const kept of r.still_held) notify(`${kept.name} stays paused: ${kept.reason}`, 'error')
    } catch (e) {
      notify(failureSentence('resume the paused automations', e), 'error')
    } finally {
      setBusy(false)
      onResumed()
    }
  }

  return (
    <section aria-labelledby="restore-hold-heading" data-testid="restore-hold"
      className="mb-l rounded-lg border border-outline-variant/50 bg-surface-container px-l py-m">
      <Eyebrow as="h2" id="restore-hold-heading">Paused by the restore</Eyebrow>
      <p data-type="body-s" className="mt-xs text-on-surface-var">
        {restoreHoldSentence(holds.length, mostCautious(holds))}
      </p>
      <div className="mt-m">
        <Button size="sm" onClick={() => void resumeAll()} loading={busy} loadingLabel="Resuming…">
          <Play size={14} aria-hidden /> Resume all
        </Button>
      </div>
    </section>
  )
}

/** In one automation's panel: why the restore paused it, and Resume, which switches it on the way
 *  its switch does (so a row whose action needs your yes asks first). */
export function RestoreHoldNote({ hold, onResume, busy = false }: {
  hold: RestoreHold
  onResume: () => void | Promise<void>
  busy?: boolean
}) {
  // Whether THIS Resume is in flight: `busy` is the panel's one flag, shared with its other actions.
  const [resuming, setResuming] = useState(false)
  const resume = async () => {
    setResuming(true)
    try { await onResume() } finally { setResuming(false) }
  }
  return (
    <div role="note" className="flex items-start gap-s text-warn">
      <PauseCircle size={14} aria-hidden className="mt-0.5 shrink-0" />
      <div data-type="body-s" className="flex min-w-0 flex-1 flex-col gap-xs">
        <p data-type="label-m">Paused by the restore</p>
        <p className="text-on-surface-var">{restoreHoldNoteSentence(hold)}</p>
        <div>
          <Button variant="secondary" size="sm" onClick={resume} loading={resuming} disabled={busy} disabledReason={BUSY_REASON}>
            Resume
          </Button>
        </div>
      </div>
    </div>
  )
}

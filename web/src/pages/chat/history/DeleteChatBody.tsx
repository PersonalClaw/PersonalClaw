import { useState } from 'react'
import { Checkbox } from '../../../ui/forms'

/** The Delete dialog's body: what goes with the chat (*said*), then the workflow runs it started
 *  itself that are still going (*going*, `GET /api/chat/sessions/{session}/runs-going`). With some,
 *  a tick stops them with it (`onStopRuns`); left unticked they keep running, as they do when the
 *  gateway could not count them (*going* null), which it says. */
export function DeleteChatBody({ said, going, onStopRuns }: {
  said: string
  going: number | null
  onStopRuns: (stop: boolean) => void
}) {
  const [stop, setStop] = useState(false)
  const runs = going === 1 ? 'the workflow run it started, still going' : `the ${going} workflow runs it started, still going`
  return (
    <div className="flex flex-col gap-s">
      <p>{said}</p>
      {going == null ? (
        <p>PersonalClaw couldn’t tell whether it started workflow runs that are still going. Any it did keep running.</p>
      ) : going > 0 ? (
        <label className="flex items-start gap-xs text-on-surface">
          <Checkbox checked={stop} ariaLabel={`Also stop ${runs}`} onChange={(v) => { setStop(v); onStopRuns(v) }} />
          <span>{`Also stop ${runs}. Left unticked, ${going === 1 ? 'it keeps' : 'they keep'} running.`}</span>
        </label>
      ) : null}
    </div>
  )
}

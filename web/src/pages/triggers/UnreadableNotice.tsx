import type { TriggerSourceUnreadable } from '../../lib/api'
import { InlineError } from '../../ui/InlineError'

/** Above the Triggers list and the week grid: each file the page lists from that cannot be read
 *  (`GET /api/triggers`' `unreadable`), what that stops, where its copy is kept, and what to do.
 *
 *  Such a file used to read as empty: the page listed nothing from it and, with nothing else
 *  listed, offered the newcomer's "No triggers" over automations that were there — and the next
 *  write replaced them. Now nothing is written to it until it can be read, and this says so, in
 *  the gateway's words (`triggers.store.unreadable_said`, which the Doctor says too). Nothing when
 *  every file reads. */
export function UnreadableNotice({ sources }: { sources: TriggerSourceUnreadable[] }) {
  if (sources.length === 0) return null
  return (
    <InlineError icon multiline className="mb-l">
      <span data-type="label-m" className="block">Your automations could not be read</span>
      {sources.map((source) => (
        <span key={source.file} className="mt-xs block">
          {source.said} {source.remedy}
        </span>
      ))}
    </InlineError>
  )
}

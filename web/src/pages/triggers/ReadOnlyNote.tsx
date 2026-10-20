import { Users, Boxes } from 'lucide-react'

/** What a row someone else wrote says in its inspector, and the reason its panel offers no Run
 *  now, Dry run, Edit, Delete or switch: this machine shows it and never runs it, and every one of
 *  those routes refuses it (`automation_read_only`). One note for the schedule and the store
 *  inspectors, so one row is never described two ways. */
export function ReadOnlyNote({ author }: { author?: string }) {
  return (
    <div role="note" data-type="body-s" className="rounded-lg bg-surface-container px-m py-s text-on-surface-var">
      <span className="inline-flex items-center gap-1.5 text-on-surface"><Users size={13} aria-hidden /> {author || 'Someone else'}</span>
      {' '}wrote this automation. It is shown for reference: PersonalClaw does not run it on this
      computer, and it cannot be edited, switched or deleted here.
    </div>
  )
}

/** What a row an app serves says in its inspector, and the reason it offers no Edit: the app keeps
 *  what the automation is and takes back only what running it changes, so an edit is refused
 *  (`automation_kept_elsewhere`) in these same words. */
export function KeptByAppNote({ app }: { app: string }) {
  return (
    <div role="note" data-type="body-s" className="rounded-lg bg-surface-container px-m py-s text-on-surface-var">
      <span className="inline-flex items-center gap-1.5 text-on-surface"><Boxes size={13} aria-hidden /> {app}</span>
      {' '}keeps this automation: it is changed in {app}. It can be run, switched on or off, or
      deleted here.
    </div>
  )
}

import { ShieldAlert, ShieldQuestion } from 'lucide-react'
import { Button } from '../../ui/Button'
import { BUSY_REASON } from '../../ui/unavailable'

/** Why a trigger brought over from an older version is off, and what switching it on does.
 *
 *  Shown by both inspectors (a data event or another store kind, and a schedule), because an
 *  imported row of either kind waits for the same decision: the older version's file recorded no
 *  permission for what it runs, so the upgrade brought it over switched off
 *  (`triggers/legacy_import.py`). Switching it on is where the owner is asked. */
export function ReviewNote() {
  return (
    <div role="note" className="flex items-start gap-s text-warn">
      <ShieldQuestion size={14} aria-hidden className="mt-0.5 shrink-0" />
      <div data-type="body-s" className="flex min-w-0 flex-1 flex-col gap-xs">
        <p data-type="label-m">Brought over from an older version</p>
        <p className="text-on-surface-var">
          It has not been allowed to run here. Check what it runs, then switch it on if you want it:
          PersonalClaw asks you to allow that first.
        </p>
      </div>
    </div>
  )
}

/** A trigger whose action it is not allowed to run (`needs_grant` — the server's verdict): what is
 *  missing, and the one control that gives it. Nothing runs it until the owner does: Run now, a
 *  view refresh and every fire are refused (`triggers/grants.py`).
 *
 *  On a trigger that is ON the control is Allow, which sends the switch `on` again — the toggle is
 *  where a grant is asked for, so Allow asks first like switching on does. On one that is OFF the
 *  switch itself is the control, so the note says what switching it on will ask. */
export function GrantNote({ labels, enabled, onAllow, busy = false }: {
  labels: string[]
  enabled: boolean
  onAllow: () => void
  busy?: boolean
}) {
  const actions = labels.map((label) => `“${label}”`).join(' and ')
  return (
    <div role="note" className="flex items-start gap-s text-warn">
      <ShieldAlert size={14} aria-hidden className="mt-0.5 shrink-0" />
      <div data-type="body-s" className="flex min-w-0 flex-1 flex-col gap-xs">
        <p data-type="label-m">Not allowed to use {actions}</p>
        <p className="text-on-surface-var">
          {enabled
            ? 'It does not run until you allow it. Check what it runs, then choose Allow: PersonalClaw asks you first.'
            : 'It does not run until you allow it. Switching it on asks you to allow it first.'}
        </p>
        {enabled && (
          <div>
            <Button variant="secondary" size="sm" onClick={onAllow} disabled={busy} disabledReason={BUSY_REASON}>
              Allow
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

/** A step's config as the owner reviews it: each key and its value, verbatim. Read-only — the
 *  editor is `ActionConfig`. "Run a shell command" is not enough to allow one; the command is. */
export function ConfigReadout({ config }: { config?: Record<string, unknown> }) {
  const entries = Object.entries(config ?? {})
  if (entries.length === 0) return null
  return (
    <dl data-type="body-s" className="mt-xs flex flex-col gap-xs">
      {entries.map(([key, value]) => (
        <div key={key} className="flex min-w-0 gap-xs">
          <dt className="shrink-0 text-on-surface-low">{key}:</dt>
          <dd className="min-w-0 break-all font-mono text-on-surface">
            {typeof value === 'string' ? value : JSON.stringify(value)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

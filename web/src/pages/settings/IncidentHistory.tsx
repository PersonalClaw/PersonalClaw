/** Settings › Guardrails › Past incidents: every incident the history keeps, newest first, with why
 *  it started, who started and ended it, how long it ran, what it held back and its newest note, and
 *  a way to add one. Read from `GET /api/incidents` a page at a time; a note goes to
 *  `POST /api/incidents/{id}/note`. */
import { useState } from 'react'
import { api, type IncidentLine } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { notify } from '../../app/appSdk'
import { fmtElapsed } from '../../lib/duration'
import { Button } from '../../ui/Button'
import { LoadError } from '../../ui/ListScaffold'
import { promptInput } from '../../ui/dialog'
import { Section } from './settingsUI'
import { NOTE_MAX, heldParts, whoSaid } from './incidentWords'

/** The data store's key for the first page, which Resume makes stale. */
export const INCIDENTS_KEY = 'settings:incidents'

function when(iso: string): string {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? 'an unrecorded time' : at.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function lasted(line: IncidentLine): string {
  const secs = (Date.parse(line.ended_at) - Date.parse(line.started_at)) / 1000
  return Number.isFinite(secs) ? fmtElapsed(secs) : ''
}

/** Ask for a note on *line*, then keep it; true once it is kept. */
export async function addIncidentNote(line: IncidentLine): Promise<boolean> {
  const note = await promptInput({
    title: 'What happened?',
    body: `Kept with the incident of ${when(line.started_at)}. Only you read it; no agent is shown it.`,
    label: 'Note',
    type: 'textarea',
    confirmLabel: 'Add the note',
    required: true,
    validate: (value) => (value.trim().length > NOTE_MAX ? `Keep it to ${NOTE_MAX.toLocaleString()} characters.` : null),
  })
  if (note === null) return false
  try {
    await api.incidentNote(line.id, note.trim())
    return true
  } catch (e) {
    notify(`Couldn't add the note: ${String((e as Error)?.message || e)}`, 'error')
    return false
  }
}

function IncidentRow({ line, onNoted }: { line: IncidentLine; onNoted: () => void }) {
  const held = heldParts(line.suppressed)
  const took = lasted(line)
  const started = whoSaid(line.activated_by, line.app)
  const ended = whoSaid(line.resumed_by)
  return (
    <li className="flex flex-col gap-1 py-3">
      <div data-type="title-s" className="text-on-surface">
        {when(line.started_at)}{took ? ` · ${took}` : ''}
      </div>
      <div data-type="body-m" className="break-words text-on-surface">{line.reason || 'No reason was recorded.'}</div>
      <div data-type="body-s" className="text-on-surface-var">
        Started by {started} · ended by {ended}
      </div>
      <div data-type="body-s" className="text-on-surface-var">
        {held.length ? `${held.join('; ')}.` : 'Nothing was held back.'}
      </div>
      {line.note
        ? <div data-type="body-s" className="whitespace-pre-wrap break-words text-on-surface">Note: {line.note}</div>
        : null}
      <div>
        <Button variant="ghost-accent" size="xs" ariaLabel={`Add a note to the incident of ${when(line.started_at)}`}
          onClick={async () => { if (await addIncidentNote(line)) onNoted() }}>
          Add a note
        </Button>
      </div>
    </li>
  )
}

export function IncidentHistory() {
  const { data, error, refresh } = useQuery(INCIDENTS_KEY, async () => api.incidents())
  const [later, setLater] = useState<{ items: IncidentLine[]; next: string | null } | null>(null)
  const [moreErr, setMoreErr] = useState<unknown>(null)
  const [loadingMore, setLoadingMore] = useState(false)

  const items = [...(data?.items ?? []), ...(later?.items ?? [])]
  const next = later ? later.next : data?.next_cursor ?? null
  const reread = () => { setLater(null); invalidateKeys(INCIDENTS_KEY); refresh() }

  const showMore = async () => {
    if (!next) return
    setLoadingMore(true)
    try {
      const page = await api.incidents(next)
      setLater({ items: [...(later?.items ?? []), ...page.items], next: page.next_cursor })
      setMoreErr(null)
    } catch (e) {
      setMoreErr(e)
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <Section title="Past incidents" hint="Every time incident mode was on: why, who started and ended it, what it held back, and your notes.">
      {!data && error ? (
        <LoadError what="past incidents" error={error} onRetry={refresh} />
      ) : !data ? (
        <div data-type="body-s" className="text-on-surface-low">Loading…</div>
      ) : items.length === 0 ? (
        <div data-type="body-s" className="text-on-surface-low">No incidents yet.</div>
      ) : (
        <div className="rounded-lg bg-surface-container px-4">
          <ul aria-label="Past incidents" className="divide-y divide-outline-variant">
            {items.map((line) => <IncidentRow key={line.id} line={line} onNoted={reread} />)}
          </ul>
          {next ? (
            <div className="py-2">
              <Button variant="ghost-accent" size="xs" loading={loadingMore} onClick={showMore}>Show older incidents</Button>
              {moreErr ? (
                <span role="alert" data-type="body-s" className="ml-2 text-error">
                  Couldn't load older incidents: {String((moreErr as Error)?.message || moreErr)}
                </span>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </Section>
  )
}

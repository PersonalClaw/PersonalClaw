import { useState } from 'react'
import { FieldError } from '../../ui/forms'
import { Trash2, Play, FlaskConical, Loader2, AlertTriangle, Users } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { confirmDelete } from '../../ui/dialog'
import { api, type Trigger as WireTrigger } from '../../lib/api'
import { RunHistory } from '../schedule/ScheduleDetail'
import { triggerHealthMeta } from '../schedule/scheduleMeta'
import { actionLabel } from './triggerMeta'
import { reportingWrite } from '../../app/reportingWrite'

/** Inspector for a store-backed trigger (file/web_watch/idle/…) in the SidePanel.
 *
 *  Read-only by design: these automations are AUTHORED in chat ("when a file in ~/notes changes,
 *  summarize it…") through the automation_* tools, so the create/edit surface is the conversation,
 *  not a form. What the page owns is management — pause/resume, run/dry-run, delete — which is
 *  exactly what the user cannot do from chat once the automation exists. Every mutation routes
 *  through the same /api/triggers store namespace the backend added (S94), which itself reuses the
 *  chat tools' own functions, so this panel and a chat command cannot answer differently.
 */
export function StoreTriggerDetail({ trigger, onChanged, onDeleted }: {
  trigger: WireTrigger
  onChanged: () => void
  onDeleted: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [runFlash, setRunFlash] = useState<string | null>(null)
  // Bumped after a real run so the history reloads — a fire the user just triggered has to appear
  // without a manual refresh, or the panel looks like it did nothing.
  const [histKey, setHistKey] = useState(0)
  const [err, setErr] = useState('')

  // The SERVER's verdict, not a comparison this panel makes (TSE-4): `read_only` is computed with
  // the same `ownership.is_owner_authored` the arm path uses, so the controls this hides are exactly
  // the ones the backend would refuse to honour.
  const readOnly = trigger.read_only === true
  const broken = trigger.broken ?? []
  const paths = Array.isArray(trigger.spec?.paths) ? (trigger.spec!.paths as string[]) : []

  async function toggle() {
    setBusy(true)
    setErr('')
    try {
      await api.toggleStoreTrigger(trigger.raw_id, !trigger.enabled)
      onChanged()
    } catch (e) {
      // A broken row refuses to enable (S87), surfaced by the API as an error — show WHY rather
      // than flipping a switch that silently did nothing.
      setErr(e instanceof Error ? e.message : 'Could not change this automation')
    } finally {
      setBusy(false)
    }
  }

  // 🔴 The lifecycle state, in words (S169). This panel's only status line was
  // `enabled ? 'Firing on its own' : 'Paused — it will not fire until re-enabled'`, so an
  // AUTOPAUSED automation (five consecutive failures) and a QUARANTINED one (a payload matched an
  // injection pattern) both read as if the USER had paused them. `TriggerState`'s own docstring
  // names the failure: *"Showing both as 'paused' would make the user look for a switch they never
  // flipped."* Measured — all three states produced that identical sentence.
  //
  // Reuses S164's shared `triggerHealthMeta` for the dot + label rather than inventing a third
  // vocabulary mapper on a third surface.
  const lc = triggerHealthMeta(trigger.health, trigger.state)
  const stopped = trigger.state === 'autopaused' || trigger.state === 'quarantined'
  const statusLine =
    trigger.state === 'autopaused'
      ? 'Stopped by the system after repeated failures'
      : trigger.state === 'quarantined'
        ? 'Quarantined — a payload matched an injection pattern; re-author it to resume'
        : trigger.state === 'parked'
          ? 'Parked — a resource it needs is busy; it resumes on its own'
          : trigger.enabled
            ? 'Firing on its own'
            : 'Paused — it will not fire until re-enabled'

  async function run(dry: boolean) {
    setBusy(true)
    setErr('')
    setRunFlash(null)
    try {
      const r = await api.runStoreTrigger(trigger.raw_id, dry)
      // 🔴 A 200 is not a success (#395). The backend answers `ok: false` when the fire was refused
      // (incident mode) or the action could not be resolved — flashing "Ran" for those is how a
      // silent no-op looked like a completed run for a whole release. `result`/`refused` carry the
      // reason, so show it instead of inventing one.
      if (!dry && r.ok === false) {
        setErr(r.refused || (typeof r.result === 'string' && r.result) || 'This automation did not run.')
        return
      }
      setRunFlash(dry ? 'Dry run — nothing executed' : 'Ran')
      if (!dry) setHistKey((k) => k + 1)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Run failed')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!(await confirmDelete('automation', trigger.name))) return
    setBusy(true)
    try {
      if (!(await reportingWrite(`delete ${trigger.name}`, () => api.deleteStoreTrigger(trigger.raw_id)))) return
      onDeleted()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-l px-m py-m">
      {broken.length > 0 && (
        <div
          className="flex items-start gap-2 rounded-lg px-3 py-2 text-[0.8125rem]"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}
        >
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <div>
            <div style={{ fontWeight: 500 }}>This automation has a problem and will not fire.</div>
            <div className="mt-0.5">{broken[0]}</div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between">
        <div>
          <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Status</div>
          <div className="flex items-center gap-1.5">
            <lc.icon size={13} style={{ color: lc.tone }} />
            <span className="text-on-surface text-[0.8125rem]">{statusLine}</span>
          </div>
          {/* 🔴 The CAUSE, which this panel never showed (S169). `last_error` has been on the wire
              all along and had no reader here, so an autopaused automation offered no way to learn
              WHY — the user had to go digging, which is exactly what `attention_card`'s docstring
              says the error text exists to prevent. */}
          {stopped && trigger.last_error && (
            <div className="mt-0.5 font-mono text-on-surface-low text-[0.75rem] break-all">
              {trigger.last_error}
            </div>
          )}
        </div>
        {/* A FOREIGN row shows STATE, not a control (TEAM-SHARED-ENTITIES §2.2 — TSE-4): its
            author's harness decides whether it is enabled, and this one will never arm or fire it,
            so a toggle here would report a change it cannot make. A `disabled` Toggle was the
            other option and is worse — it says "you may not", where the truth is "this is not
            yours to set". */}
        {readOnly
          ? <span className="shrink-0 text-on-surface-var text-[0.8125rem]">{trigger.enabled ? 'Enabled' : 'Disabled'}</span>
          : <Toggle on={trigger.enabled} onChange={toggle} disabled={busy} label="Enabled" />}
      </div>

      {readOnly && (
        <div className="rounded-lg bg-surface-container px-3 py-2 text-on-surface-var text-[0.8125rem]">
          <span className="inline-flex items-center gap-1.5 text-on-surface"><Users size={13} /> {trigger.author || 'Someone else'}</span>
          {' '}wrote this automation. It is shown for reference: this harness never runs it, and it
          cannot be edited or deleted here.
        </div>
      )}

      <Section label="When it runs">
        <div className="text-on-surface text-[0.875rem]">{storeKindLabel(trigger.store_kind)}</div>
        {paths.length > 0 && (
          <ul className="mt-1 flex flex-col gap-0.5">
            {paths.map((p) => (
              <li key={p} className="font-mono text-on-surface-low text-[0.8125rem] break-all">{p}</li>
            ))}
          </ul>
        )}
      </Section>

      <Section label="What it runs">
        <div className="text-on-surface text-[0.875rem]">{actionLabel(trigger.action?.provider)}</div>
      </Section>

      {/* 🔴 A store trigger's run history, which this panel never showed (S168). The backend has
          served the list since S166 and the per-run detail since S167, and the only UI for either
          lived inside `ScheduleDetail` behind a hardcoded `schedule:` id — so a `web_watch` or
          `file` automation showed "When it runs" and "What it runs" and nothing about whether it
          ever HAD. Reusing the exported `RunHistory` rather than writing a second one: a duplicate
          renderer is how two surfaces start disagreeing about what a run looks like. */}
      <RunHistory triggerId={trigger.id} reloadKey={histKey} />

      {trigger.created_by === 'agent' && (
        <div className="text-on-surface-low text-[0.75rem]">Created for you automatically. Manage it here or ask in chat to change it.</div>
      )}

      {err && <FieldError>{err}</FieldError>}
      {runFlash && !err && <div className="text-on-surface-low text-[0.8125rem]">{runFlash}</div>}

      {/* No action row at all for a foreign automation — Run now, Dry run and Delete are all
          writes to somebody else's row. Rendering them disabled would still assert they are
          things that could apply to it. */}
      {!readOnly && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Button variant="secondary" size="sm" onClick={() => run(false)} disabled={busy}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Run now
          </Button>
          <Button variant="ghost" size="sm" onClick={() => run(true)} disabled={busy}>
            <FlaskConical size={14} /> Dry run
          </Button>
          <div className="flex-1" />
          <Button variant="ghost" size="sm" onClick={remove} disabled={busy} className="text-danger">
            <Trash2 size={14} /> Delete
          </Button>
        </div>
      )}
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">{label}</div>
      {children}
    </div>
  )
}

function storeKindLabel(kind?: string): string {
  const map: Record<string, string> = {
    file: 'When a watched file changes',
    web_watch: 'When a watched web page changes',
    idle: 'After a period of inactivity',
    run_completed: 'When a workflow run finishes',
    view: 'When its surface is viewed',
    webhook: 'When its webhook receives a request',
  }
  return map[kind ?? ''] ?? (kind || 'Automation')
}

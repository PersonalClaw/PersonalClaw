import { useEffect, useRef, useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { api, type WorkflowRunDetailData } from '../../lib/api'
import { notify } from '../../app/appSdk'
import type { Rebase, Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { Field, NumberField, TextInput } from '../../ui/forms'
import { Toggle } from '../../ui/Toggle'
import { QuietButton } from '../../ui/QuietButton'
import { HELD_CHANGE_REASON } from '../../lib/staleWrite'

/** The prelaunch per-run policy editor (PP-16 seam 4f) — the write surface over the run's
 *  sparse `SupervisorPolicy` overlay.
 *
 *  SPARSE IS THE CONTRACT, and the editor shows it honestly: a knob the user never touched
 *  renders as "Kind default" — NOT as some guessed value — because the actual default is
 *  resolved server-side from the run's kind, and inventing a number here would teach the
 *  user the overlay says something it does not. Each set knob carries its own "Clear"
 *  affordance, and the header offers "Clear all" (the store's `{}`-clears semantics).
 *
 *  Every commit PUTs the WHOLE overlay (replace semantics, matching the store contract) over the
 *  revision of the copy it was built from, and re-syncs from the response, so what is rendered is
 *  always what was persisted. A copy another tab has changed since is refused (`409 stale_write`)
 *  and the edit is offered back for re-applying (`ui/StaleWriteNotice`).
 *
 *  The caller mounts this only for a PRELAUNCH run (`isPrelaunch`, mirroring the backend's
 *  phase gate): once launched the overlay is frozen — the engine's whole-row saves would
 *  silently revert a live edit — and the route answers 409 `run_not_prelaunch`, which this
 *  panel surfaces as-is rather than pretending the edit landed. */

/** The five ruled per-instance knobs (`supervisor_policy.OVERRIDABLE_POLICY_KEYS`), with the
 *  presentation each needs. Exported so the test can assert the editor covers the whole
 *  vocabulary rather than a remembered subset. */
export const POLICY_KNOBS: ReadonlyArray<{
  key: string
  label: string
  hint: string
  kind: 'toggle' | 'number' | 'text'
  /** What "Override" seeds the control with — a starting point for editing, not a claim
   *  about the kind default. */
  seed: boolean | number | string
}> = [
  {
    key: 'attended',
    label: 'Attended',
    hint: 'A human is in this loop — gates resolve to you instead of the unattended posture.',
    kind: 'toggle',
    seed: true,
  },
  {
    key: 'autopilot',
    label: 'Autopilot',
    hint: 'The run drives its own phases without asking (approval posture "auto").',
    kind: 'toggle',
    seed: true,
  },
  {
    key: 'max_cycles',
    label: 'Max cycles',
    hint: 'Hard cycle budget for this run; 0 means uncapped.',
    kind: 'number',
    seed: 0,
  },
  {
    key: 'idle_secs',
    label: 'Idle seconds',
    hint: 'How long the run may sit idle before the supervisor steps in.',
    kind: 'number',
    seed: 120,
  },
  {
    key: 'success_criteria',
    label: 'Success criteria',
    hint: 'One line defining done — it becomes the judge rubric’s single criterion.',
    kind: 'text',
    seed: '',
  },
]

/** Blur-commit wrapper for the free-text knob — the same commit discipline `NumberField`
 *  ships internally. A per-keystroke PUT would race its own responses and garble typing;
 *  Enter blurs, blur commits, and an external change re-syncs the draft. */
function TextOverride({ value, onCommit, ariaLabel, placeholder }: {
  value: string
  onCommit: (v: string) => void
  ariaLabel: string
  placeholder?: string
}) {
  const [draft, setDraft] = useState(value)
  const synced = useRef(value)
  useEffect(() => {
    if (synced.current === value) return
    synced.current = value
    setDraft(value)
  }, [value])
  return (
    <div onBlur={() => { if (draft !== value) onCommit(draft) }}>
      <TextInput
        value={draft}
        onChange={setDraft}
        placeholder={placeholder}
        ariaLabel={ariaLabel}
        size="sm"
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
      />
    </div>
  )
}

type Overlay = Record<string, unknown>

/** A run read's overlay with the revision the same read reported for it — what the editor seeds
 *  from and what its first write names. */
export const overlayOf = (run: WorkflowRunDetailData): Revisioned<Overlay> => ({
  value: run.policy_overrides ?? {},
  revision: run.revisions?.policy_overrides ?? '',
})

export function PolicyOverridesPanel({ runId, initial, onSaved }: {
  runId: string
  /** The overlay as the run-detail read delivered it — only the knobs the user overrode — with
   *  that read's revision of it (`overlayOf`). */
  initial: Revisioned<Overlay>
  onSaved?: (overrides: Overlay) => void
}) {
  // What is stored, as the gateway last reported it: the overlay each edit is applied to, and the
  // revision the write names.
  const [stored, setStored] = useState<Revisioned<Overlay>>(initial)
  const overrides = stored.value
  const [busy, setBusy] = useState(false)
  // The copy the guard's own reads and writes returned — what a landed save or a discard re-syncs
  // the panel from, so what is rendered is always what was persisted.
  const latest = useRef<Revisioned<Overlay> | null>(null)
  // 🔴 THE OVERLAY IS WRITTEN WHOLE, OVER THE COPY IT WAS BUILT FROM. Every edit PUTs this panel's
  // overlay with one knob changed, so a tab opened before another tab's edit reverted that edit the
  // moment it touched a different knob. A stale copy is refused now, and the edit — an operation on
  // the overlay (this knob set, that one cleared) — is re-applied onto what is stored.
  const guard = useStaleWriteGuard<Overlay>({
    read: () => api.workflowRun(runId).then((run) => { latest.current = overlayOf(run); return latest.current }),
    write: (next, revision) => api.setWorkflowRunPolicyOverrides(runId, next, revision).then((res) => {
      latest.current = { value: res.policy_overrides, revision: res.revisions.policy_overrides }
    }),
    onSaved: () => {
      if (!latest.current) return
      setStored(latest.current)
      onSaved?.(latest.current.value)
    },
    onDiscard: () => { if (latest.current) setStored(latest.current) },
  })
  const locked = busy || guard.conflict !== null

  // One writer for every mutation: the edit as an operation on the overlay, PUT as the whole overlay
  // (replace semantics) and re-synced from what the server persisted, so a refused write never
  // leaves the UI claiming it won.
  const commit = async (op: Rebase<Overlay>) => {
    setBusy(true)
    try {
      await guard.apply(stored, op)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Saving policy overrides failed', 'error')
    } finally {
      setBusy(false)
    }
  }

  const set = (key: string, value: unknown) => commit((theirs) => ({ ...theirs, [key]: value }))
  const clear = (key: string) => commit((theirs) => {
    const next = { ...theirs }
    delete next[key]
    return next
  })

  const hasAny = POLICY_KNOBS.some(({ key }) => key in overrides)

  return (
    <section className="flex flex-col gap-m rounded-lg bg-surface-high p-m">
      <div className="flex items-center justify-between gap-m">
        <span data-type="title-m" className="inline-flex items-center gap-s text-on-surface">
          <SlidersHorizontal size={14} /> Policy overrides
        </span>
        {hasAny && (
          <QuietButton
            onClick={() => commit(() => ({}))}
            disabled={locked}
            disabledReason={guard.conflict !== null ? HELD_CHANGE_REASON : undefined}
            title="Clear every override — the run falls back to its kind defaults"
          >
            Clear all
          </QuietButton>
        )}
      </div>
      <p data-type="caption" className="text-on-surface-low">
        Set only what this run should differ on; anything left unset follows the kind default.
        Editable until launch — a launched run&rsquo;s policy is frozen.
      </p>
      {/* Every knob is locked while a refused save waits for the user's choice: the notice
          re-applies the edit it kept, and a second edit made meanwhile would replace it. */}
      <fieldset disabled={locked} className="flex min-w-0 flex-col gap-m">
        {POLICY_KNOBS.map(({ key, label, hint, kind, seed }) => {
          const isSet = key in overrides
          const value = overrides[key]
          return (
            <Field
              key={key}
              label={label}
              hint={hint}
              right={isSet ? (
                <QuietButton
                  onClick={() => clear(key)}
                  disabled={locked}
                  title={`Clear the ${label} override — this run falls back to the kind default`}
                >
                  Clear override
                </QuietButton>
              ) : undefined}
            >
              {!isSet ? (
                <div className="flex items-center gap-s">
                  {/* The honest unset state: the default is resolved from the run's KIND on
                      the server, so this names where the value comes from rather than
                      inventing a number the overlay does not carry. */}
                  <span data-type="body-s" className="text-on-surface-low">Kind default</span>
                  <QuietButton
                    onClick={() => set(key, seed)}
                    disabled={locked}
                    title={`Override ${label} for this run only`}
                  >
                    Override
                  </QuietButton>
                </div>
              ) : kind === 'toggle' ? (
                <Toggle
                  on={value === true}
                  onChange={(v) => set(key, v)}
                  label={`${label} override`}
                />
              ) : kind === 'number' ? (
                <NumberField
                  value={typeof value === 'number' ? value : Number(value) || 0}
                  onChange={(n) => set(key, n)}
                  min={0}
                  ariaLabel={`${label} override`}
                />
              ) : (
                <TextOverride
                  value={typeof value === 'string' ? value : String(value ?? '')}
                  onCommit={(v) => set(key, v)}
                  placeholder="e.g. the PR is opened and CI is green"
                  ariaLabel={`${label} override`}
                />
              )}
            </Field>
          )
        })}
      </fieldset>
      <StaleWriteNotice guard={guard} what="This run's policy overrides" />
    </section>
  )
}

import { useEffect, useRef, useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { Field, NumberField, TextInput } from '../../ui/forms'
import { Toggle } from '../../ui/Toggle'
import { QuietButton } from '../../ui/QuietButton'

/** The prelaunch per-run policy editor (PP-16 seam 4f) — the write surface over the run's
 *  sparse `SupervisorPolicy` overlay.
 *
 *  SPARSE IS THE CONTRACT, and the editor shows it honestly: a knob the user never touched
 *  renders as "Kind default" — NOT as some guessed value — because the actual default is
 *  resolved server-side from the run's kind, and inventing a number here would teach the
 *  user the overlay says something it does not. Each set knob carries its own "Clear"
 *  affordance, and the header offers "Clear all" (the store's `{}`-clears semantics).
 *
 *  Every commit PUTs the WHOLE overlay (replace semantics, matching the store contract) and
 *  re-syncs from the response, so what is rendered is always what was persisted.
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

export function PolicyOverridesPanel({ runId, initial, onSaved }: {
  runId: string
  /** The overlay as the run-detail read delivered it — only the knobs the user overrode. */
  initial: Record<string, unknown>
  onSaved?: (overrides: Record<string, unknown>) => void
}) {
  const [overrides, setOverrides] = useState<Record<string, unknown>>(initial)
  const [busy, setBusy] = useState(false)

  // One writer for every mutation: PUT the whole overlay (replace semantics) and re-sync
  // from what the server persisted, so a refused write never leaves the UI claiming it won.
  const commit = async (next: Record<string, unknown>) => {
    setBusy(true)
    try {
      const res = await api.setWorkflowRunPolicyOverrides(runId, next)
      setOverrides(res.policy_overrides ?? next)
      onSaved?.(res.policy_overrides ?? next)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Saving policy overrides failed', 'error')
    } finally {
      setBusy(false)
    }
  }

  const set = (key: string, value: unknown) => commit({ ...overrides, [key]: value })
  const clear = (key: string) => {
    const next = { ...overrides }
    delete next[key]
    commit(next)
  }

  const hasAny = POLICY_KNOBS.some(({ key }) => key in overrides)

  return (
    <section className="flex flex-col gap-m rounded-lg bg-surface-high p-m">
      <div className="flex items-center justify-between gap-m">
        <span data-type="title-m" className="inline-flex items-center gap-s text-on-surface">
          <SlidersHorizontal size={14} /> Policy overrides
        </span>
        {hasAny && (
          <QuietButton
            onClick={() => commit({})}
            disabled={busy}
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
      <div className="flex flex-col gap-m">
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
                  disabled={busy}
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
                    disabled={busy}
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
      </div>
    </section>
  )
}

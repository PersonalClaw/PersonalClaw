import { useState } from 'react'
import { Check, TriangleAlert, X } from 'lucide-react'
import { Button } from '../../ui/Button'
import { QuietButton } from '../../ui/QuietButton'
import { Checkbox, Field, NumberField, Select, TextArea, TextInput } from '../../ui/forms'
import type { WorkflowContinuation } from '../../lib/api'

/** The ONE renderer for every human-input gate (WF2-R7).
 *
 *  The backend ships a TYPED ask payload — approval | choice | text | form — precisely so a
 *  single component covers every gate any template will ever declare. A per-template
 *  renderer is how "just add a prompt string" becomes twelve half-broken dialogs.
 *
 *  An expired token renders as a dead end WITH a next step: a button that silently does
 *  nothing is indistinguishable from a bug. */
export function WorkflowAsk({ continuation, busy, onAnswer }: {
  continuation: WorkflowContinuation
  busy: boolean
  onAnswer: (c: WorkflowContinuation, value: unknown, alwaysAllow: boolean) => void | Promise<void>
}) {
  const { ask, handoff, expired } = continuation
  const kind = ask.kind || 'approval'
  const [text, setText] = useState('')
  const [choice, setChoice] = useState(ask.choices?.[0] ?? '')
  const [form, setForm] = useState<Record<string, unknown>>(() => {
    const seed: Record<string, unknown> = {}
    for (const f of ask.fields ?? []) if (f.type === 'boolean') seed[f.name] = false
    return seed
  })
  const [alwaysAllow, setAlwaysAllow] = useState(false)
  const setField = (name: string, value: unknown) => setForm((p) => ({ ...p, [name]: value }))

  if (expired) {
    return (
      <div className="flex flex-col gap-s rounded-xl border border-outline-variant p-l">
        <span className="inline-flex items-center gap-s text-on-surface-low text-[0.8125rem]">
          <TriangleAlert size={14} /> This request expired before it was answered.
        </span>
        <p className="text-on-surface-low text-[0.75rem]">
          Re-run the workflow from <span className="font-mono">{continuation.node_id}</span> to ask again.
        </p>
      </div>
    )
  }

  const hasContext = !!(handoff.checks_run?.length || handoff.outstanding?.length || handoff.risks?.length)

  return (
    <div className="flex flex-col gap-m rounded-xl border border-outline-variant p-l">
      <p className="text-on-surface text-[0.9375rem]">{ask.prompt || 'This run needs your input.'}</p>

      {/* The handoff bundle: what a returning human needs to re-acquire context without
          reading the whole journal. */}
      {hasContext && (
        <div className="flex flex-col gap-2xs text-on-surface-low text-[0.75rem]">
          {!!handoff.checks_run?.length && <span>Already done: {handoff.checks_run.length} step(s)</span>}
          {!!handoff.outstanding?.length && <span>Still to do: {handoff.outstanding.length} step(s)</span>}
          {handoff.risks?.map((r) => <span key={r} className="text-warning">Risk: {r}</span>)}
        </div>
      )}

      {kind === 'choice' && (
        <Field label="Choose one">
          <Select
            value={choice}
            onChange={setChoice}
            options={(ask.choices ?? []).map((c) => ({ value: c, label: c }))}
          />
        </Field>
      )}

      {kind === 'text' && (
        <Field label="Your answer">
          <TextArea value={text} onChange={setText} rows={3} ariaLabel="Your answer" />
        </Field>
      )}

      {kind === 'form' && (
        <div className="flex flex-col gap-s">
          {(ask.fields ?? []).map((f) => (
            <Field key={f.name} label={f.label || f.name}>
              {f.type === 'boolean' ? (
                <Checkbox
                  checked={!!form[f.name]}
                  onChange={(v) => setField(f.name, v)}
                  ariaLabel={f.label || f.name}
                />
              ) : f.type === 'choice' ? (
                <Select
                  value={String(form[f.name] ?? f.choices?.[0] ?? '')}
                  onChange={(v) => setField(f.name, v)}
                  options={(f.choices ?? []).map((c) => ({ value: c, label: c }))}
                />
              ) : f.type === 'number' ? (
                <NumberField
                  value={Number(form[f.name] ?? 0)}
                  onChange={(v) => setField(f.name, v)}
                  ariaLabel={f.label || f.name}
                />
              ) : (
                <TextInput
                  value={String(form[f.name] ?? '')}
                  onChange={(v) => setField(f.name, v)}
                  ariaLabel={f.label || f.name}
                />
              )}
            </Field>
          ))}
        </div>
      )}

      <label className="inline-flex items-center gap-s text-on-surface-low text-[0.75rem]">
        <Checkbox checked={alwaysAllow} onChange={setAlwaysAllow} ariaLabel="Don't ask again for this step in this run" />
        Don&apos;t ask again for this step in this run
      </label>

      <div className="flex items-center gap-s">
        {kind === 'approval' ? (
          <>
            <Button onClick={() => onAnswer(continuation, true, alwaysAllow)} disabled={busy}>
              <Check size={14} /> Approve
            </Button>
            {/* Deny is quiet, not destructive-styled: rejecting a gate is a normal answer,
                and dressing it in red implies the run broke. */}
            <QuietButton onClick={() => onAnswer(continuation, false, alwaysAllow)} title="Deny this step">
              <X size={13} /> Deny
            </QuietButton>
          </>
        ) : (
          <Button
            onClick={() => onAnswer(
              continuation,
              kind === 'choice' ? choice : kind === 'text' ? text : form,
              alwaysAllow,
            )}
            disabled={busy || (kind === 'text' && !text.trim())}
          >
            <Check size={14} /> Submit
          </Button>
        )}
      </div>
    </div>
  )
}

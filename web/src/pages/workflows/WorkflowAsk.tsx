import { useState } from 'react'
import { Check, Play, TriangleAlert, X } from 'lucide-react'
import { Button } from '../../ui/Button'
import { QuietButton } from '../../ui/QuietButton'
import { Checkbox, Field, NumberField, Select, TextArea, TextInput } from '../../ui/forms'
import type { WorkflowContinuation } from '../../lib/api'
import { findGenUiBlock, widgetlessText } from '../../ui/widget/blocks'
import { GenUiWidget } from '../../ui/genui/GenUiWidget'
import { GenUiHostCtx } from '../../ui/genui/actions'
import { BUSY_REASON } from '../../ui/unavailable'

/** The ONE renderer for every human-input gate.
 *
 *  The backend ships a TYPED ask payload — approval | choice | text | form | event — precisely so
 *  a single component covers every gate any template will ever declare. A per-template
 *  renderer is how "just add a prompt string" becomes twelve half-broken dialogs.
 *
 *  An `event` gate asks nothing: its run is parked until something wakes it (a monitor's own
 *  trigger), so it offers one thing, waking it now. No Deny, because an event cannot be refused
 *  (cancel the run to stop it), and no "Don't ask again", because a remembered wake would wake it
 *  at once every time.
 *
 *  An expired token renders as a dead end WITH a next step: a button that silently does
 *  nothing is indistinguishable from a bug.
 *
 *  A gate may ALSO author its prompt as a generative-UI tree: when
 *  the prompt carries a `<widget kind="genui">` block, that widget renders here with THIS
 *  gate declared as its action producer, so submitting its form answers the gate through the
 *  same resume path the typed controls use — the run advances instead of the answer landing
 *  in a chat. `runId` is required rather than optional precisely so a caller cannot render
 *  an interactive gate widget whose submit has nowhere to go. */
export function WorkflowAsk({ continuation, runId, busy, onAnswer, rerunCaption, stepName = '' }: {
  continuation: WorkflowContinuation
  runId: string
  busy: boolean
  onAnswer: (c: WorkflowContinuation, value: unknown, alwaysAllow: boolean) => void | Promise<void>
  /** What the asking step is called where the caller knows it (the run page: its label). */
  stepName?: string
  /** What Approve and Deny do for a step that parked (`ask.rerun`), when its owner is not a run —
   *  a trigger's park runs again from the trigger, and Deny leaves it for its next run. */
  rerunCaption?: string
}) {
  const { ask, handoff, expired } = continuation
  const kind = ask.kind || 'approval'
  const event = kind === 'event'
  const [text, setText] = useState('')
  const [choice, setChoice] = useState(ask.choices?.[0] ?? '')
  const [form, setForm] = useState<Record<string, unknown>>(() => {
    const seed: Record<string, unknown> = {}
    for (const f of ask.fields ?? []) if (f.type === 'boolean') seed[f.name] = false
    return seed
  })
  const [alwaysAllow, setAlwaysAllow] = useState(false)
  // Which answer THIS panel sent is still in flight, which `busy` cannot say: it is the caller's one
  // flag, shared by every gate a run page lists. So `busy` dims every answer while any is out, this
  // panel's own answer dims the rest of its buttons too — Deny included, so a second, contrary answer
  // cannot follow the first — and only the pressed primary button spins (`loading`, which is what
  // publishes `aria-busy`). `send` serves all three primaries because exactly one of Wake, Approve and
  // Submit renders; a Deny in flight is `deny`, so Approve never spins for a Deny.
  const [answering, setAnswering] = useState<'send' | 'deny' | null>(null)
  const setField = (name: string, value: unknown) => setForm((p) => ({ ...p, [name]: value }))
  const answerWith = async (which: 'send' | 'deny', value: unknown, remember: boolean) => {
    setAnswering(which)
    try { await onAnswer(continuation, value, remember) } finally { setAnswering(null) }
  }
  const send = (value: unknown, remember: boolean) => answerWith('send', value, remember)

  if (expired) {
    return (
      <div className="flex flex-col gap-s rounded-xl border border-outline-variant p-l">
        <span data-type="body-s" className="inline-flex items-center gap-s text-on-surface-low">
          <TriangleAlert size={14} /> This request expired before it was answered.
        </span>
        <p data-type="caption" className="text-on-surface-low">
          Re-run the workflow from {stepName && stepName !== continuation.node_id
            ? <>“{stepName}”</>
            : <span className="font-mono">{continuation.node_id}</span>} to ask again.
        </p>
      </div>
    )
  }

  // A gate whose prompt carries a genui tree. Parsed with the SAME seam chat uses
  // (`parseWidgetBlocks`), so a widget authored for a transcript and one authored for a gate
  // are the same artifact — only the producer differs.
  const gateWidget = findGenUiBlock(ask.prompt || '')
  const promptText = widgetlessText(ask.prompt || '')
  const gateHost = {
    producer: { kind: 'workflow-gate' as const, runId, token: continuation.resume_token },
  }

  // A step that PARKED on the user (browse stopped at a sign-in page) asks with `rerun`: approving
  // runs that step again once they have lifted what stopped it, rather than recording a "yes" as
  // its output. So the buttons say what they do, and "Don't ask again" is not offered — nothing can
  // sign in on the user's behalf the next time it stops, and the engine does not keep that answer.
  const rerun = ask.rerun === true
  const hasContext = !!(
    handoff.attempted?.length || handoff.checks_run?.length || handoff.outstanding?.length || handoff.risks?.length
  )

  return (
    <div className="flex flex-col gap-m rounded-xl border border-outline-variant p-l">
      {gateWidget ? (
        <>
          {promptText && <p data-type="body-m" className="text-on-surface">{promptText}</p>}
          <GenUiHostCtx.Provider value={gateHost}>
            <GenUiWidget content={gateWidget.html} title={gateWidget.title} />
          </GenUiHostCtx.Provider>
        </>
      ) : (
        <p data-type="body-m" className="text-on-surface">{ask.prompt || 'This run needs your input.'}</p>
      )}

      {/* The handoff bundle: what a returning human needs to re-acquire context without
          reading the whole journal. */}
      {hasContext && (
        <div data-type="caption" className="flex flex-col gap-xs text-on-surface-low">
          {/* What the asking step already tried — the reason it is asking. First, because it is
              the line that explains the question above it. */}
          {handoff.attempted?.map((line) => <span key={line}>Tried: {line}</span>)}
          {!!handoff.checks_run?.length && <span>Already done: {handoff.checks_run.length} step{handoff.checks_run.length === 1 ? '' : 's'}</span>}
          {!!handoff.outstanding?.length && <span>Still to do: {handoff.outstanding.length} step{handoff.outstanding.length === 1 ? '' : 's'}</span>}
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

      {/* What each answer DOES, because Deny is not a soft no: nothing after a declined approval
          runs, and the run ends there (`gate_answers.end_at_gate`). */}
      {rerun ? (
        <p data-type="caption" className="text-on-surface-low">
          {rerunCaption ?? 'Approve runs this step again. Deny ends the run here.'}
        </p>
      ) : event ? (
        <p data-type="caption" className="text-on-surface-low">
          This step waits for something to happen, and the run carries on when it does. Wake it
          now to carry on without waiting.
        </p>
      ) : (
        <>
          {kind === 'approval' && (
            <p data-type="caption" className="text-on-surface-low">
              Deny ends the run here — nothing after this step runs.
            </p>
          )}
          <label data-type="caption" className="inline-flex items-center gap-s text-on-surface-low">
            <Checkbox checked={alwaysAllow} onChange={setAlwaysAllow} ariaLabel="Don't ask again for this step in this run" />
            Don&apos;t ask again for this step in this run
          </label>
        </>
      )}

      <div className="flex items-center gap-s">
        {event ? (
          // The wake carries no verdict, so it is sent as a bare `true`: "it happened".
          <Button onClick={() => send(true, false)} loading={answering === 'send'}
            disabled={busy || answering !== null} disabledReason={BUSY_REASON}>
            <Play size={14} /> Wake it now
          </Button>
        ) : kind === 'approval' ? (
          <>
            <Button onClick={() => send(true, rerun ? false : alwaysAllow)} loading={answering === 'send'}
              disabled={busy || answering !== null} disabledReason={BUSY_REASON}>
              <Check size={14} /> Approve
            </Button>
            {/* Deny is quiet, not destructive-styled: rejecting a gate is a normal answer,
                and dressing it in red implies the run broke. Off while any answer is out, like
                Approve: a Deny pressed after an Approve would send the opposite answer. */}
            <QuietButton onClick={() => answerWith('deny', false, rerun ? false : alwaysAllow)} title="Deny this step"
              disabled={busy || answering !== null} disabledReason={BUSY_REASON}>
              <X size={13} /> Deny
            </QuietButton>
          </>
        ) : (
          <Button
            onClick={() => send(
              kind === 'choice' ? choice : kind === 'text' ? text : form,
              alwaysAllow,
            )}
            loading={answering === 'send'}
            disabled={busy || answering !== null || (kind === 'text' && !text.trim())}
            disabledReason={kind === 'text' && !text.trim() ? 'Type an answer first' : BUSY_REASON}
          >
            <Check size={14} /> Submit
          </Button>
        )}
      </div>
    </div>
  )
}

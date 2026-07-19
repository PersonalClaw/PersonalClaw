import { Rocket } from 'lucide-react'
import type { LaunchSpec, LoopKind } from '../../lib/api'

// The loop kinds a template can launch (research/design excluded — they need their own
// multi-modal/step intake, not a fill-and-launch; goal is the natural default).
const LAUNCH_KINDS: { id: LoopKind; label: string }[] = [
  { id: 'goal', label: 'Goal' }, { id: 'general', label: 'General' }, { id: 'code', label: 'Code' },
]
const RIGORS = ['minimal', 'grill', 'thorough']

/** Runnable-template (#17) authoring — turn a prompt into a "campaign template" you
 *  fill + launch into a Project/Loop run. Off by default (plain prompt); enabling it
 *  seeds a minimal goal launch_spec, then exposes the loop-launch knobs. Shared by the
 *  create form (PromptForm) AND the in-place editor (PromptEditFields) so a template's
 *  launch config is authored the same way in both — no dual implementation. */
export function RunnableTemplateField({ spec, onChange }: { spec?: LaunchSpec; onChange: (s: LaunchSpec | undefined) => void }) {
  const on = spec != null
  const s = spec ?? {}
  const setK = <K extends keyof LaunchSpec>(k: K, v: LaunchSpec[K]) => onChange({ ...s, [k]: v })
  return (
    <div className="flex flex-col gap-2">
      <button type="button" onClick={() => onChange(on ? undefined : { kind: 'goal', intake_rigor: 'minimal' })}
        className="inline-flex items-center gap-1.5 self-start rounded-pill px-3 h-8 text-[0.8125rem] transition-colors"
        style={on ? { background: 'color-mix(in srgb, var(--color-primary) 18%, transparent)', color: 'var(--color-primary)' } : { background: 'var(--color-surface-container)', color: 'var(--color-on-surface-var)' }}>
        <Rocket size={14} /> {on ? 'Runnable — launches a loop' : 'Make runnable'}
      </button>
      {on && (
        <div className="flex flex-col gap-2 rounded-lg bg-surface-container p-3">
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-on-surface-var text-[0.75rem]">Kind
              <select value={s.kind ?? 'goal'} onChange={(e) => setK('kind', e.target.value as LoopKind)}
                className="h-8 rounded-md bg-surface px-2 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]">
                {LAUNCH_KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-on-surface-var text-[0.75rem]">Intake depth
              <select value={s.intake_rigor ?? 'minimal'} onChange={(e) => setK('intake_rigor', e.target.value)}
                className="h-8 rounded-md bg-surface px-2 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]">
                {RIGORS.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-on-surface-var text-[0.75rem]">Agent (optional)
              <input value={s.agent ?? ''} onChange={(e) => setK('agent', e.target.value)} placeholder="default worker"
                className="h-8 rounded-md bg-surface px-2 font-mono text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
            </label>
            <label className="flex flex-col gap-1 text-on-surface-var text-[0.75rem]">Model (optional)
              <input value={s.model ?? ''} onChange={(e) => setK('model', e.target.value)} placeholder="active model"
                className="h-8 rounded-md bg-surface px-2 font-mono text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
            </label>
          </div>
          <p className="text-on-surface-low text-[0.7rem]">The variables above are filled at launch, rendered into the task, then this loop is created + started.</p>
        </div>
      )}
    </div>
  )
}

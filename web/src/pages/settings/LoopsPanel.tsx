import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, RowGroup, ToggleRow, NumberRow, SelectRow } from './settingsUI'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'

// The editable `loops.*` fields, mirroring the backend `_EDITABLE_CONFIG` allowlist
// (`config/learning.py` LoopsConfig). All four were PATCH-editable with no control anywhere in
// `web/` (issue #2801) — this is the panel the two deferral notes that recorded the gap
// (HARNESS-CRAFT HC-2, and `test_check_work.py`'s docstring) both pointed at.
type LoopsCfg = Record<string, unknown>

// The model use cases the loop judge may ride — the chat family from `ModelsPanel`'s USE_CASE_META,
// which is the vocabulary `_EDITABLE_CONFIG`'s enum declares for this key. 6 options (>4) → a
// Select, and a Select over the real names rather than a free-text box, so an unbindable typo
// cannot be entered at all.
const JUDGE_USE_CASES = [
  { value: 'reasoning', label: 'Reasoning' },
  { value: 'chat', label: 'Chat' },
  { value: 'code_tools', label: 'Code & tools' },
  { value: 'background', label: 'Background' },
  { value: 'orchestration', label: 'Orchestration' },
  { value: 'loops', label: 'Loops' },
] as const

/** Autonomous loops — how a long-running goal loop is judged, when it counts as stalled, and how
 *  its parallel task worktrees are built.
 *
 *  The loops themselves live on `#/loops`; this panel is only the settings they run under. */
export function LoopsPanel() {
  const [cfg, setCfg] = useState<LoopsCfg | null>(null)

  const { data, error: loadErr, refresh } = useQuery('settings:loops', () =>
    api.personalclawConfig().then((c) => (c.loops ?? {}) as LoopsCfg),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  // A failed read reaches the hook, so the form is replaced by the failure rather than rendering
  // four controls at their fallbacks — a panel must not present fabricated values as saved state.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={2} what="settings" />

  // Optimistic single-field PATCH; a rejected save rolls back and surfaces the error
  // (a swallowed 400 would look exactly like a successful save).
  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`loops.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Autonomous loops" hint="Long-horizon goal loops: which model certifies a cycle is done, how many stuck cycles count as stalled, and how much of your repo a parallel task checks out." />

      <Section title="Judging" hint="The reviewer that certifies a cycle, and the patience the supervisor has for a loop that is not moving.">
        <RowGroup>
          <SelectRow label="Judge model axis" cfg={cfg} field="judge_use_case" patch={patch}
            options={[...JUDGE_USE_CASES]} fallback="reasoning"
            hint="Which model use case the loop JUDGE rides — deliberately not the Loops axis the worker rides. Reasoning by default, so the reviewer gets its own (typically stronger) binding and a reviewer mistake is not correlated with the mistake it is reviewing. Set it to Loops to put judge and worker back on one binding." />
          <NumberRow label="Stagnation window (cycles)" cfg={cfg} field="stagnation_window" min={2} max={50} patch={patch}
            hint="How many consecutive cycles of no progress stall a loop. Lower is more trigger-happy; higher spends more cycles before asking you for direction. Minimum 2 — a window of 1 can only compare a cycle with itself." />
        </RowGroup>
      </Section>

      <Section title="Verification" hint="An extra pass that checks what a stage CLAIMED, not just that its gate command exited zero.">
        <RowGroup>
          <ToggleRow label="Check work after stage gates" cfg={cfg} field="check_work_stages" patch={patch}
            hint="After an SDLC stage's gate passes, re-derive a few executable checks from what the stage claimed and run them. Catches the case where the gate command passed but the claim was broader than the command — a deliverable file the stage said it wrote but didn't. Off by default: it adds a filesystem pass per stage advance." />
        </RowGroup>
      </Section>

      <Section title="Parallel task worktrees" hint="How much of the repository a parallel task hydrates before it starts.">
        <RowGroup>
          <ToggleRow label="Sparse task worktrees" cfg={cfg} field="worktree_sparse" patch={patch}
            hint="When a parallel task's plan names the files it will touch, hydrate only those directories instead of the whole repo — on a large codebase that is most of a worktree's setup cost. A task that writes outside its stated scope widens its own worktree automatically, and the merged result is identical either way. Off always hydrates the full repo." />
        </RowGroup>
      </Section>
    </div>
  )
}

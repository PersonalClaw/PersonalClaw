import { useId } from 'react'
import { X } from 'lucide-react'
import { SquareIconButton } from '../../ui/SquareIconButton'
import type { PromptVariable, PromptVarType } from '../../lib/api'
import { VAR_TYPES } from './promptMeta'

/** One editable prompt/snippet variable row: name · type · required · default · (choices).
 *  Extracted verbatim from the canonical `PromptForm` copy — the richest of the three
 *  hand-rolled duplicates (it alone carried `useId()`-based `name=` autofill defeat + the
 *  full set of `aria-label`s), so the edit-field and snippet call sites GAIN those a11y +
 *  autofill affordances byte-for-byte. `descriptionPlaceholder` is the ONE thing the copies
 *  varied: a prompt is *invoked* ("…shown when invoked"), a snippet is *inserted* ("Description"). */
export function VariableRow({ v, onChange, onRemove, descriptionPlaceholder = 'Description (shown when invoked)' }: {
  v: PromptVariable
  onChange: (patch: Partial<PromptVariable>) => void
  onRemove: () => void
  descriptionPlaceholder?: string
}) {
  const rid = useId()
  return (
    <div className="rounded-md bg-surface-container p-2 flex flex-col gap-2">
      <div className="flex items-center gap-s">
        <input value={v.name} onChange={(e) => onChange({ name: e.target.value.replace(/[^a-zA-Z0-9_]/g, '_') })} placeholder="variable_name" aria-label="Variable name" name={`var-name-${rid}`}
          className="flex-1 h-8 rounded-md bg-surface px-m font-mono text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <div className="relative">
          <select value={v.type} onChange={(e) => onChange({ type: e.target.value as PromptVarType })} aria-label="Variable type" name={`var-type-${rid}`}
            className="h-8 appearance-none rounded-md bg-surface pl-m pr-7 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]">
            {VAR_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
          </select>
        </div>
        <button type="button" onClick={() => onChange({ required: !v.required })} className="rounded-pill px-2 h-7 text-[0.75rem] transition-colors" style={v.required ? { background: 'color-mix(in srgb, var(--color-danger) 18%, transparent)', color: 'var(--color-danger)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>{v.required ? 'required' : 'optional'}</button>
        <SquareIconButton icon={X} tone="danger" label="Remove variable" onClick={onRemove} />
      </div>
      <div className="flex items-center gap-s">
        <input value={v.description ?? ''} onChange={(e) => onChange({ description: e.target.value })} placeholder={descriptionPlaceholder} aria-label="Variable description" name={`var-desc-${rid}`}
          className="flex-1 h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <input value={v.default == null ? '' : String(v.default)} onChange={(e) => onChange({ default: e.target.value })} placeholder="default" aria-label="Variable default value" name={`var-default-${rid}`}
          className="w-28 h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      </div>
      {v.type === 'select' && (
        <input value={(v.options ?? []).join(', ')} onChange={(e) => onChange({ options: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} placeholder="Choices, comma-separated" aria-label="Variable choices" name={`var-opts-${rid}`}
          className="h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      )}
    </div>
  )
}

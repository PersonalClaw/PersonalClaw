import { useEffect, useId, useState } from 'react'
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
export function VariableRow({ v, onChange, onRemove, descriptionPlaceholder = 'Description (shown when invoked)', rowIndex }: {
  v: PromptVariable
  onChange: (patch: Partial<PromptVariable>) => void
  onRemove: () => void
  descriptionPlaceholder?: string
  /** 0-based position, used to name the row while its variable is still unnamed. Without it a set of
   *  blank new rows would all announce identically again. */
  rowIndex?: number
}) {
  const rid = useId()
  // Every control below had an aria-label ALREADY — but a CONSTANT one, on a component rendered once
  // per variable. Measured on the live DOM: a prompt with 3 variables produced 3 boxes all announcing
  // "Variable name", so the names were non-null and still ambiguous. `rid` already made the `name=`
  // attributes unique for autofill; the accessible name needed the same treatment. Scope it to the
  // variable the row edits — its own name once typed, falling back to the position while it is blank.
  const which = v.name?.trim() ? `"${v.name.trim()}"` : `row ${rowIndex != null ? rowIndex + 1 : ''}`.trim()
  return (
    <div className="rounded-md bg-surface-container p-2 flex flex-col gap-2">
      <div className="flex items-center gap-s">
        <input value={v.name} onChange={(e) => onChange({ name: e.target.value.replace(/[^a-zA-Z0-9_]/g, '_') })} placeholder="variable_name" aria-label={`Name of variable ${which}`} name={`var-name-${rid}`}
          className="flex-1 h-8 rounded-md bg-surface px-m font-mono text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <div className="relative">
          <select value={v.type} onChange={(e) => onChange({ type: e.target.value as PromptVarType })} aria-label={`Type of variable ${which}`} name={`var-type-${rid}`}
            className="h-8 appearance-none rounded-md bg-surface pl-m pr-7 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50">
            {VAR_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
          </select>
        </div>
        <button type="button" onClick={() => onChange({ required: !v.required })} className="rounded-pill px-2 h-7 text-[0.75rem] transition-colors" style={v.required ? { background: 'color-mix(in srgb, var(--color-danger) 18%, transparent)', color: 'var(--color-danger)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>{v.required ? 'required' : 'optional'}</button>
        <SquareIconButton icon={X} tone="danger" label={`Remove variable ${which}`} onClick={onRemove} />
      </div>
      <div className="flex items-center gap-s">
        <input value={v.description ?? ''} onChange={(e) => onChange({ description: e.target.value })} placeholder={descriptionPlaceholder} aria-label={`Description of variable ${which}`} name={`var-desc-${rid}`}
          className="flex-1 h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <input value={v.default == null ? '' : String(v.default)} onChange={(e) => onChange({ default: e.target.value })} placeholder="default" aria-label={`Default value of variable ${which}`} name={`var-default-${rid}`}
          className="w-28 h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      </div>
      {v.type === 'select' && <ChoicesInput options={v.options} onChange={onChange} name={`var-opts-${rid}`} which={which} />}
    </div>
  )
}

/** The comma-separated choices field for a `select` variable.
 *
 *  Held as raw draft text while focused, normalized to `string[]` on blur. The
 *  normalization (`trim` + drop empties) is right for the PERSISTED list but
 *  destroys the intermediate states typing produces: as a controlled field it
 *  round-tripped `options` back through `join(', ')` on every keystroke, so
 *  "red," normalized to ["red"] and re-rendered as "red" — the comma vanished
 *  before the next character could be typed, and a select could never hold more
 *  than one option (#594). `trim` was the same class of bug for the space after
 *  a comma. Do not move either back onto the keystroke path.
 *
 *  The parent still owns the value; the draft only shadows it between focus and
 *  blur, and re-syncs whenever the committed list changes from outside. */
function ChoicesInput({ options, onChange, name, which }: {
  options?: string[]
  onChange: (patch: Partial<PromptVariable>) => void
  name: string
  /** Which variable this belongs to — one ChoicesInput renders per `select` variable, so a constant
   *  name would announce identically across rows. */
  which: string
}) {
  const committed = (options ?? []).join(', ')
  const [draft, setDraft] = useState(committed)
  useEffect(() => { setDraft(committed) }, [committed])
  return (
    <input value={draft} onChange={(e) => setDraft(e.target.value)}
      onBlur={() => onChange({ options: draft.split(',').map((s) => s.trim()).filter(Boolean) })}
      placeholder="Choices, comma-separated" aria-label={`Choices for variable ${which}`} name={name}
      className="h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
  )
}

/** The fold-out iteration rail beside a rendered artifact (AMBIENT-SURFACES §3+§4).
 *
 *  Two things a user could previously only do by asking in prose:
 *   · move the artifact's own declared tunables and watch it restyle immediately —
 *     no turn, no model, no request;
 *   · point at the elements that are wrong and send ONE correction naming all of them.
 *
 *  Presentation only: every decision lives in `useArtifactIteration`. The controls are
 *  the shared primitives (Slider / Toggle / Select / Button / SquareIconButton) so a
 *  tweak rail looks like the rest of the app rather than like a widget's own chrome.
 */
import { Check, Crosshair, Save, Sliders, X } from 'lucide-react'
import { Button } from '../Button'
import { SquareIconButton } from '../SquareIconButton'
import { Slider } from '../Slider'
import { Toggle } from '../Toggle'
import { FieldError, Select, TextInput } from '../forms'
import { cx } from '../cx'
import { fvs } from '../../design/fontWeight'
import { hexForPicker, MAX_EDIT_PARAMS, rangeNumber, type EditModeParam } from './editMode'
import type { ArtifactIteration } from './useArtifactIteration'

/** One tunable's control, derived from its declared type. */
function ParamControl({ param, value, onChange }: {
  param: EditModeParam
  value: string
  onChange: (next: string) => void
}) {
  if (param.type === 'color') {
    const hex = hexForPicker(value)
    // A non-hex authored colour (oklch/hsl/named) cannot seed a native picker, so it
    // stays a text field rather than being silently rewritten to the nearest hex.
    if (!hex) {
      return <TextInput value={value} onChange={onChange} size="sm" mono ariaLabel={`${param.label} value`} />
    }
    return (
      <div className="flex items-center gap-s">
        <span className="relative size-6 overflow-hidden rounded-sm border border-outline-variant" style={{ background: value }}>
          <input type="color" value={hex} onChange={(e) => onChange(e.target.value)}
            className="absolute inset-0 cursor-pointer opacity-0" aria-label={`${param.label} colour`} />
        </span>
        <span className="font-mono text-on-surface-low text-[0.75rem]">{hex}</span>
      </div>
    )
  }
  if (param.type === 'range') {
    const n = rangeNumber({ ...param, value })
    return (
      <div className="flex items-center gap-s">
        <Slider value={n} min={param.min} max={param.max} step={param.step}
          ariaLabel={param.label}
          onChange={(next) => onChange(`${next}${param.unit ?? ''}`)} />
        <span className="w-14 shrink-0 text-right font-mono text-on-surface-low text-[0.75rem] tabular-nums">{value}</span>
      </div>
    )
  }
  if (param.type === 'select') {
    return (
      <Select value={value} ariaLabel={param.label}
        options={(param.options ?? []).map((o) => ({ value: o, label: o }))}
        onChange={onChange} />
    )
  }
  return (
    <Toggle on={value === param.on} label={param.label} size="sm"
      onChange={(on) => onChange((on ? param.on : param.off) ?? '')} />
  )
}

export function ArtifactIterationRail({ it, onClose, className = 'w-64 shrink-0 overflow-y-auto border-l' }: {
  it: ArtifactIteration
  onClose: () => void
  /** Placement only — the host decides whether the rail sits BESIDE the frame (the
   *  artifact library's full-height pane) or FOLDS OUT beneath it (an inline chat
   *  widget, which has no side room). Tone, spacing and controls are fixed. */
  className?: string
}) {
  return (
    <aside aria-label="Artifact iteration"
      className={cx('flex flex-col gap-m border-outline-variant/50 bg-surface-container/60 p-m', className)}>
      <div className="flex items-center gap-s">
        <Sliders size={13} className="text-on-surface-low" />
        <span className="flex-1 text-on-surface text-[0.75rem] uppercase tracking-wide" style={fvs(500)}>Iterate</span>
        <SquareIconButton icon={X} label="Close the iteration rail" onClick={onClose} iconSize={13} />
      </div>

      {it.params.length > 0 ? (
        <div className="flex flex-col gap-m">
          {it.params.map((p) => (
            <div key={p.key} className="flex flex-col gap-1.5">
              <span className="text-on-surface-low text-[0.75rem]">{p.label}</span>
              <ParamControl param={p} value={it.values[p.key] ?? p.value}
                onChange={(next) => it.setValue(p.key, next)} />
            </div>
          ))}
          {it.savable && (
            <Button variant="secondary" size="sm" onClick={it.save} loading={it.saving}
              disabled={!it.dirty} disabledReason="Move a control first — there is nothing to save yet.">
              <Save size={13} /> Save as a new version
            </Button>
          )}
        </div>
      ) : (
        <p className="text-on-surface-low text-[0.75rem]">
          This artifact declares no tunable parameters. An agent adds them with an
          <code className="mx-1 font-mono">EDITMODE</code> block.
        </p>
      )}
      {it.droppedParams > 0 && (
        <p className="text-on-surface-low text-[0.75rem]">
          {it.droppedParams} declared parameter{it.droppedParams === 1 ? ' was' : 's were'} ignored —
          malformed, or past the {MAX_EDIT_PARAMS}-parameter limit.
        </p>
      )}

      <div className="flex flex-col gap-s border-t border-outline-variant/50 pt-m">
        <Button variant={it.annotating ? 'primary' : 'secondary'} size="sm" onClick={it.toggleAnnotate}
          ariaPressed={it.annotating}>
          <Crosshair size={13} /> {it.annotating ? 'Click elements to mark them' : 'Mark elements'}
        </Button>
        {it.annotations.map((a, i) => (
          <div key={`${a.selector}:${i}`} className="flex flex-col gap-1.5 rounded-md bg-surface-high p-s">
            <div className="flex items-start gap-s">
              <code className="min-w-0 flex-1 truncate font-mono text-on-surface-var text-[0.6875rem]" title={a.selector}>{a.selector}</code>
              <SquareIconButton icon={X} label={`Unmark ${a.selector}`} onClick={() => it.removeAnnotation(i)} iconSize={11} />
            </div>
            <TextInput value={a.note} onChange={(v) => it.setNote(i, v)} size="sm"
              placeholder="What should change?" ariaLabel={`Correction for ${a.selector}`} />
          </div>
        ))}
        {it.annotations.length > 0 && (
          <Button variant="primary" size="sm" onClick={it.sendCorrection}>
            <Check size={13} /> Send one correction ({it.annotations.length})
          </Button>
        )}
      </div>

      {it.error && <FieldError>{it.error}</FieldError>}
    </aside>
  )
}

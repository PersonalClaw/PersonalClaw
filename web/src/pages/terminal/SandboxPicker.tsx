import { Shield } from 'lucide-react'
import { Select } from '../../ui/forms'

export interface SandboxProvider { name: string; display_name: string; available: boolean }

/**
 * Per-session sandbox tier picker for a NEW terminal (EXECUTION-ISOLATION EI-4 §1.3(3)).
 *
 * The host tier (`none`) is always selectable; a container/VM tier (docker/lima) is disabled —
 * greyed, with the reason in its `title` — when its live probe reports it unavailable, so a
 * stopped Lima instance or a missing daemon cannot be chosen (SC3 "greyed-out-with-reason"). The
 * choice applies to the NEXT session opened; existing sessions are unaffected.
 *
 * Rides the shared form-family `Select` (a native `<select>` under the hood, so keyboard,
 * screen-reader, and disabled-option semantics come for free) at the `sm` scale for this
 * toolbar row.
 */
export function SandboxPicker({ providers, value, onChange, busy }: {
  providers: SandboxProvider[]
  value: string
  onChange: (name: string) => void
  /** In-flight gate: the picker locks while the next session is being opened. */
  busy?: boolean
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-[0.8125rem] text-on-surface-low">
      <Shield size={14} aria-hidden />
      <span className="sr-only">Sandbox for new terminal sessions</span>
      <span className="w-44">
        <Select
          ariaLabel="Sandbox for new terminal sessions"
          value={value}
          disabled={busy}
          onChange={onChange}
          size="sm"
          options={providers.map((p) => ({
            value: p.name,
            label: `${p.display_name}${p.available ? '' : ' (unavailable)'}`,
            disabled: !p.available,
            title: p.available ? undefined : `${p.display_name} is unavailable — start the tier to use it`,
          }))}
        />
      </span>
    </label>
  )
}

import { Shield } from 'lucide-react'
import { Select } from '../../ui/forms'
import { useHeaderChild, useHeaderProbe } from '../../ui/HeaderActions'

export interface SandboxProvider { name: string; display_name: string; available: boolean }

/**
 * Per-session sandbox tier picker for a NEW terminal.
 *
 * The host tier (`none`) is always selectable; a container/VM tier (docker/lima) is disabled —
 * greyed, with the reason in its `title` — when its live probe reports it unavailable, so a
 * stopped Lima instance or a missing daemon cannot be chosen ("greyed-out-with-reason"). The
 * choice applies to the NEXT session opened; existing sessions are unaffected.
 *
 * Rides the shared form-family `Select` (a native `<select>` under the hood, so keyboard,
 * screen-reader, and disabled-option semantics come for free) at the `sm` scale for this
 * toolbar row.
 *
 * A member of the header's cluster: it registers, so the row is measured with it, and narrows its
 * field where the row has shed to icons. A choice has no menu-row form, so it never goes into the
 * `…`. In the cluster's offscreen probes it draws a box of its width, not a second select.
 */
export function SandboxPicker({ providers, value, onChange, busy }: {
  providers: SandboxProvider[]
  value: string
  onChange: (name: string) => void
  /** In-flight gate: the picker locks while the next session is being opened. */
  busy?: boolean
}) {
  const { tier, mark } = useHeaderChild({ priority: 'low', canIcon: true, neverOverflow: true, menu: { label: 'Sandbox for new terminal sessions', icon: Shield } })
  const probe = useHeaderProbe()
  const width = tier === 'icon' || tier === 'overflow' ? 'w-32' : 'w-44'
  if (probe) {
    return mark(
      <span aria-hidden className="inline-flex items-center gap-1.5">
        <Shield size={14} /><span className={`${width} h-8`} />
      </span>
    )
  }
  return mark(
    <label className="inline-flex items-center gap-1.5 text-[0.8125rem] text-on-surface-low">
      <Shield size={14} aria-hidden />
      <span className="sr-only">Sandbox for new terminal sessions</span>
      <span className={width}>
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

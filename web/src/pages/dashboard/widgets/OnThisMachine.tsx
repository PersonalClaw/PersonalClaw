import { useState } from 'react'
import { MoreRow } from '../../../ui/MoreRow'
import { HardDrive } from 'lucide-react'
import { api } from '../../../lib/api'
import { useCachedData } from '../../../lib/useCachedData'
import {
  occupantDetail, pressureDetail, pressureTone, sortOccupants,
} from '../../../lib/residency'
import { Meter } from '../../../ui/Meter'
import { notify } from '../../../app/appSdk'
import { confirm } from '../../../ui/dialog'
import { RowAction, SlotEmptyState, WidgetRow } from './kit'

/** "On this machine" (LOCAL-MODEL-MANAGER-V2 §7) — what is occupying RAM right now.
 *
 *  The dashboard half of the residency surface: the same
 *  ``GET /api/models/loaded`` snapshot Settings → Models renders, ordered by the same
 *  shared derivations (lib/residency), so the two surfaces cannot drift into disagreeing
 *  about which model is reclaimable. Deliberately read-mostly here — one Unload per row,
 *  no install/repair controls, because the dashboard answers "what is going on" and
 *  Settings is where a model's lifecycle is managed.
 *
 *  There is no bento tile registry to register with: the dashboard's bento grid and its
 *  per-user layout persistence were retired, so this is a module that DashboardPage
 *  hard-imports into a `<Section>` band, matching PinnedArtifacts. */
export function OnThisMachine() {
  const { data, error, refresh } = useCachedData('dashboard:on-this-machine', () =>
    api.modelsLoaded(), { persist: false },
  )
  const [busy, setBusy] = useState('')

  // A failed fetch must not render as an empty machine: "nothing is loaded" and "the
  // gateway didn't answer" look identical, and one of them is wrong about your memory.
  if (!data && error) {
    return (
      <SlotEmptyState icon={HardDrive}>
        Couldn&rsquo;t read what&rsquo;s loaded on this machine.
      </SlotEmptyState>
    )
  }
  if (!data) return null

  const rows = sortOccupants(data.loaded)

  const unload = async (provider: string, subject: string) => {
    const ok = await confirm({
      title: `Unload ${provider}?`,
      body: 'Frees the memory this provider holds. The next request loads the model again.',
      confirmLabel: 'Unload',
    })
    if (!ok) return
    setBusy(provider)
    try {
      await api.unloadModelProvider(provider)
      refresh()
    } catch (e) {
      notify(`Couldn't unload ${subject}: ${String((e as Error)?.message || e)}`, 'error')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-s pt-xs">
      <Meter
        label="System memory in use"
        pct={data.pressure.used_pct}
        tone={pressureTone(data.pressure)}
        detail={pressureDetail(data.pressure)}
      />
      {rows.length === 0 ? (
        <SlotEmptyState icon={HardDrive}>
          No models are loaded. One loads on its first use.
        </SlotEmptyState>
      ) : (
        rows.slice(0, 5).map((row) => {
          const subject = row.model || row.provider
          return (
            <WidgetRow
              key={`${row.provider}:${row.model}`}
              actions={
                <RowAction
                  tone="default"
                  onClick={() => unload(row.provider, subject)}
                  title={busy === row.provider ? 'Unloading…' : 'Unload'}
                  ariaLabel={`Unload: ${subject}`}
                >
                  Unload
                </RowAction>
              }
            >
              <span className="flex min-w-0 flex-col">
                <span data-type="label-m" className="truncate text-on-surface">{subject}</span>
                <span data-type="body-s" className="truncate text-on-surface-low">
                  {row.provider} · {occupantDetail(row)}
                </span>
              </span>
            </WidgetRow>
          )
        })
      )}
      {/* Loaded models hold RAM. "Five loaded" and "nine loaded" are different facts about this
          machine, and the widget was showing the same five either way. */}
      <MoreRow total={rows.length} shown={5} />
    </div>
  )
}

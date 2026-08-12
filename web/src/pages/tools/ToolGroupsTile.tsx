import { useMemo, useState } from 'react'
import { Layers, Loader2 } from 'lucide-react'
import { Toggle } from '../../ui/Toggle'
import { api, type ToolGroupsData } from '../../lib/api'

/** Tool groups (Context Economy §5) — the Tools-page surface for the tool-surface
 *  partition and what it costs in context.
 *
 *  DESIGN HONESTY: activation is **per-session runtime state** — the agent drives
 *  it with `reset_tools`, seeded from the per-surface defaults. So this tile does
 *  NOT offer a per-group on/off switch: a persisted toggle would imply a
 *  durability the mechanism doesn't have. What IS configurable is the feature
 *  flag (one switch here) and the per-surface defaults (config), so those are what
 *  the tile exposes — alongside the read-only partition, which is derived from the
 *  registered providers, and a plain statement of what each surface starts with. */

const SURFACE_LABEL: Record<string, string> = {
  chat: 'Chat',
  background: 'Background tasks',
  loops: 'Autonomous runs',
  orchestration: 'Subagents',
}

const SURFACE_HINT: Record<string, string> = {
  chat: 'Conversations you are watching',
  background: 'Titles, tags, digests, consolidation',
  loops: 'Goal and code loop workers',
  orchestration: 'Agents spawned by another agent',
}

export function ToolGroupsTile({ data, onChanged }: { data: ToolGroupsData; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const groups = data.groups
  const offerable = useMemo(() => groups.filter((g) => g.offerable), [groups])
  const hidden = useMemo(() => groups.filter((g) => !g.offerable), [groups])

  async function toggleEnabled() {
    setBusy(true)
    try {
      await api.setToolGroupsEnabled(!data.enabled)
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  // What the focused surfaces save, in the terms that matter: how much of the
  // tool surface they DON'T carry. Counted from the partition, not guessed.
  const total = offerable.reduce((sum, g) => sum + g.toolCount, 0)

  return (
    <div>
      <div className="mb-s flex items-center gap-s">
        <Layers size={14} className="text-on-surface-low" />
        <span className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Tool groups</span>
        <span className="text-on-surface-low text-[0.75rem]">
          · {offerable.length} group{offerable.length === 1 ? '' : 's'}, {total} tool{total === 1 ? '' : 's'}
        </span>
        <div className="ml-auto flex items-center gap-s">
          {busy && <Loader2 size={13} className="animate-spin text-on-surface-low" />}
          {/* Toggle IS the interactive primitive (it owns the switch role + label) —
              no wrapping button, which would nest a control inside a control. */}
          <Toggle on={data.enabled} onChange={toggleEnabled} disabled={busy} label="Tool groups" />
        </div>
      </div>

      <div className="rounded-lg bg-surface-container px-m py-m">
        <p className="text-on-surface-low text-[0.8125rem] leading-relaxed">
          Every tool schema an agent carries costs context on every turn. Groups let a
          session load only the groups it needs and pick up the rest on demand — the
          agent activates them itself mid-task.{' '}
          {data.enabled
            ? 'Chat keeps every group loaded; background, autonomous, and subagent runs start focused.'
            : 'Currently off: every session loads every group.'}
        </p>
        <p className="mt-2 text-on-surface-low text-[0.75rem] leading-relaxed">
          Nothing is ever unavailable — a tool stays callable by name even when its
          group is not loaded, and the agent can search across all of them.
        </p>

        <div className="mt-m flex flex-wrap gap-1.5">
          {offerable.map((g) => (
            <span key={g.name}
              title={`${g.toolCount} tool${g.toolCount === 1 ? '' : 's'}: ${g.tools.slice(0, 8).join(', ')}${g.tools.length > 8 ? '…' : ''}${g.alwaysOn ? '\nAlways loaded — these are the primitives an agent cannot work without.' : ''}`}
              className={`inline-flex items-center gap-1.5 rounded-pill px-2.5 h-6 text-[0.75rem] ${g.alwaysOn ? 'bg-primary-container text-on-primary-container' : 'bg-surface-high text-on-surface-low'}`}>
              {g.display}
              {/* No `opacity-*` on the count: the chip's own colour is ALREADY the dimmed token
                  (`text-on-surface-low` / `text-primary`), so dimming again halves an intentional
                  value — measured 3.62:1, under the 4.5:1 AA floor. Without it: 5.93:1. The
                  canonical count chip (LoopsListPage) has never carried opacity. */}
              <span className="tabular-nums">{g.toolCount}</span>
              {g.alwaysOn && <span>always</span>}
            </span>
          ))}
        </div>

        {hidden.length > 0 && (
          <p className="mt-m text-on-surface-low text-[0.75rem] leading-relaxed">
            <span className="text-warn">Not available in this install:</span>{' '}
            {hidden.map((g) => g.display).join(', ')} — the capability these tools need
            isn’t configured, so they’re hidden rather than offered in a state where
            they’d fail.
          </p>
        )}

        {data.enabled && (
          <div className="mt-m border-t border-outline-variant/30 pt-m">
            <div className="mb-s text-on-surface-low text-[0.75rem] uppercase tracking-wide">What each surface starts with</div>
            <div className="grid gap-1.5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
              {Object.entries(data.surfaceDefaults).map(([surface, names]) => {
                const all = names.length === 0
                return (
                  <div key={surface} className="rounded-md bg-surface-high px-2.5 py-2" title={SURFACE_HINT[surface] ?? ''}>
                    <div className="text-on-surface text-[0.8125rem]">{SURFACE_LABEL[surface] ?? surface}</div>
                    <div className="mt-0.5 text-on-surface-low text-[0.75rem]">
                      {all ? 'every group' : names.map((name, i) => {
                        // A default may name a group this install can't offer (e.g.
                        // subagents with no model bound). Strike it rather than listing
                        // it plainly — otherwise the panel contradicts the
                        // "not available" line above it.
                        const unavailable = hidden.some((g) => g.name === name)
                        return (
                          <span key={name}>
                            {i > 0 && ', '}
                            <span className={unavailable ? 'line-through opacity-60' : ''}
                              title={unavailable ? `${name} isn’t available in this install, so this surface starts without it` : undefined}>
                              {name}
                            </span>
                          </span>
                        )
                      })}
                    </div>
                  </div>
                )
              })}
            </div>
            <p className="mt-s text-on-surface-low text-[0.75rem]">
              Tune these with <code className="font-mono">tools.group_defaults</code> in
              config. A surface listed as “every group” behaves exactly as it did before
              groups existed.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

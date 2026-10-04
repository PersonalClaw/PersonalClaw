import { useState } from 'react'
import { Bot, RotateCcw } from 'lucide-react'
import { Popover, MenuRow } from '../../ui/Popover'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import { SearchField } from '../../ui/SearchField'
import { ResultAnnouncement } from '../../ui/ListControls'
import { PillButton, cleanAgentHint } from '../../ui/composer/controls'
import { useRuntimeGroups, type RuntimeGroup } from '../../lib/agents'
import type { LoopAgentCliSelfApproval } from '../../lib/api'
import { providerMeta } from '../agents/agentMeta'
import { LoopSelfApproval } from './LoopSelfApproval'
import { ON_PERSONALCLAW, notReadyWhy, runtimeOf, runtimeOfAgent, sameRuntime, showRuntime, type LoopRuntime } from './loopRuntime'

/** "Runs on" — what a loop's planner and workers run on: PersonalClaw, or an agent CLI set up here
 *  as one of the agents it offers. The chat composer's agent catalog (`useRuntimeGroups`, built on
 *  the same discovery `useAgentCatalog` uses), drawn as the composer's own pill.
 *
 *  Every runtime set up here is listed, a runtime that is not ready included: it is shown with why,
 *  and cannot be picked, rather than left out — a CLI missing from the list reads as one that is not
 *  installed. A ready runtime whose agents could not be listed says so instead of offering none. */
export function LoopRuntimePill({ value, onChange, chip = false }: {
  value: LoopRuntime
  onChange: (rt: LoopRuntime) => void
  /** Drawn as a loop page's status chip (`RunsOnChip`) instead of a composer pill. */
  chip?: boolean
}) {
  const { groups, loaded, error, reload } = useRuntimeGroups()
  const [q, setQ] = useState('')
  const shown = showRuntime(value, loaded ? groups : undefined)
  const total = groups.reduce((n, g) => n + g.agents.length, 0)
  const nq = q.trim().toLowerCase()
  const match = (g: RuntimeGroup) => (a: { name: string; description: string }) =>
    !nq || `${a.name} ${a.description} ${providerMeta(g.providerId, g.label).label}`.toLowerCase().includes(nq)
  return (
    <Popover portal width={300} placement={chip ? 'bottom' : 'top'} trigger={(open, toggle) => chip ? (
      <RunsOnChip shown={shown} onClick={toggle} expanded={open} />
    ) : (
      <span title={shown.title} className="inline-flex">
        <PillButton icon={<Bot size={16} strokeWidth={2} />} label={runsOnLabel(shown)}
          dimension="Runs on" open={open} toggle={toggle} wide />
      </span>
    )}>
      {(close) => (
        <div className="flex max-h-[360px] flex-col">
          {total > 8 && (
            <div className="shrink-0 px-xs pb-xs">
              <SearchField value={q} onChange={setQ} placeholder="Search agents" autoFocus size="sm" />
              <ResultAnnouncement count={groups.reduce((n, g) => n + g.agents.filter(match(g)).length, 0)} noun="agents" active={!!nq} />
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto">
            <MenuRow label="PersonalClaw" hint="The loop’s own worker, in this gateway"
              selected={!value.provider} onClick={() => { onChange(ON_PERSONALCLAW); close() }} />
            {groups.map((g) => {
              const cli = providerMeta(g.providerId, g.label).label
              const agents = g.agents.filter(match(g))
              if (nq && g.ready && !g.failure && agents.length === 0) return null
              return (
                <div key={g.providerId}>
                  <Eyebrow className="px-m pt-m pb-xs">{cli}</Eyebrow>
                  {!g.ready ? (
                    // Not pickable, and said why: the runtime's own words about what it needs.
                    <p data-type="caption" aria-disabled="true" className="px-m pb-s text-on-surface-low">
                      Can’t run a loop now: {notReadyWhy(g)}.
                    </p>
                  ) : g.failure ? (
                    <p data-type="caption" role="alert" className="px-m pb-s" style={{ color: 'var(--color-danger)' }}>{g.failure}</p>
                  ) : g.agents.length === 0 ? (
                    <p data-type="caption" className="px-m pb-s text-on-surface-low">Its last Test listed no agents.</p>
                  ) : agents.map((a) => {
                    const rt = runtimeOfAgent(g, a)
                    return <MenuRow key={a.id} label={a.name} hint={cleanAgentHint(a.description)}
                      selected={sameRuntime(rt, value)} onClick={() => { onChange(rt); close() }} />
                  })}
                </div>
              )
            })}
            {/* Three states before a list: not read yet, could not be read, and none set up. Only
                the last is a statement about her setup. */}
            {!loaded && !error && <p data-type="caption" className="px-m py-s text-on-surface-low">Loading the agent CLIs set up here…</p>}
            {!loaded && !!error && (
              <div role="alert" className="px-m py-s">
                <p data-type="caption" className="text-on-surface-low">Couldn’t read the agent CLIs set up here — a load error, not a missing install.</p>
                <Button variant="ghost-accent" size="xs" onClick={() => reload()} className="mt-xs"><RotateCcw size={13} /> Try again</Button>
              </div>
            )}
            {loaded && groups.length === 0 && (
              <p data-type="caption" className="px-m py-s text-on-surface-low">No agent CLI is set up here. Add an agent CLI’s app to run a loop on it.</p>
            )}
          </div>
          <p data-type="caption" className="shrink-0 border-t border-outline-variant/30 px-m py-s text-on-surface-low">
            The loop’s planner and workers run on what you pick, under the loop’s Mode.
          </p>
        </div>
      )}
    </Popover>
  )
}

/** The visible name of what a loop runs on, marked when it can't run there now. */
function runsOnLabel(shown: { label: string; unavailable: string }): string {
  return shown.unavailable ? `${shown.label} (unavailable)` : shown.label
}

/** What a loop runs on, as a chip in a loop page's status strip: a button when it opens the picker
 *  (`onClick`, before launch), else plain text. Its title carries the whole sentence — why it is
 *  unavailable, when it is. */
export function RunsOnChip({ shown, onClick, expanded }: {
  shown: { label: string; title: string; unavailable: string }
  onClick?: () => void
  expanded?: boolean
}) {
  const body = <><Bot size={11} className="shrink-0" /><span className="truncate"><span className="sr-only">Runs on: </span>{runsOnLabel(shown)}</span></>
  const cls = `inline-flex h-5 max-w-[16rem] items-center gap-xs rounded-pill px-s ${shown.unavailable ? 'bg-warn/15 text-warn' : 'bg-surface-high text-on-surface-var'}`
  return onClick
    ? <button type="button" data-type="caption" onClick={onClick} aria-expanded={expanded} title={shown.title}
        className={`${cls} hover:brightness-110`}>{body}</button>
    : <span data-type="caption" title={shown.title} className={cls}>{body}</span>
}

/** What *loop* runs on, on its own page: a picker until it launches (`onChange`, when given), the
 *  runtime's name after — and, either way, marked when that runtime can't run it now. Beside it,
 *  for an Unattended loop on an agent CLI, whether that CLI asks PersonalClaw about its calls
 *  (`LoopSelfApproval`). */
export function LoopRunsOn({ loop, onChange }: {
  loop: {
    id?: string; attended?: boolean; provider?: string; provider_agent?: string
    agent_cli_self_approval?: LoopAgentCliSelfApproval
  }
  onChange?: (rt: LoopRuntime) => void
}) {
  const { groups, loaded } = useRuntimeGroups()
  const value = runtimeOf(loop)
  const runsOn = onChange
    ? <LoopRuntimePill chip value={value} onChange={onChange} />
    : <RunsOnChip shown={showRuntime(value, loaded ? groups : undefined)} />
  if (!loop.id) return runsOn
  return <>{runsOn}<LoopSelfApproval loop={{ ...loop, id: loop.id, attended: loop.attended !== false }} /></>
}

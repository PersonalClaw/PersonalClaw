import { useEffect, useRef, useState } from 'react'
import { fvs, withWeight } from '../../design/fontWeight'
import { motion } from 'framer-motion'
import { Loader2, ArrowUpRight, CircleDot, CheckCircle2, Circle, AlertTriangle, HelpCircle, Clock, Search } from 'lucide-react'
import { api, type Loop } from '../../lib/api'
import { loopStatusLabel, loopStatusTone, effectiveLoopStatus } from '../../lib/loopStatus'
import { loopKindMeta } from '../../lib/loopKind'
import { foldRunSnapshot } from '../loops/runFold'
import { RunProgress } from '../loops/RunProgress'
import { messageEnter } from '../../design/motion'

/** Live in-chat progress widget for a Code project / Goal Loop the agent created
 *  or started from chat (see sdlc_tools.py). Detected from the tool segment by
 *  tool name + the `/#/code/<id>` or `/#/loops/<id>` deep link the tool returns,
 *  then this card polls the entity directly — status + stage/sub-goal progress +
 *  recent activity + a jump to the cockpit. Replaces the bare ToolCard for these
 *  tools so a created/launched entity reads as a living thing, not a log line.
 *
 *  It is a status mirror: it pauses, stops and deletes nothing. Those live on the loop's own
 *  page, where a Code project's Delete says what it removes: its branches, and the work its run
 *  kept. The card once carried a second copy of them, a Delete with no word of that, for a
 *  Projects hub that no longer renders it; nothing turned that copy on, so it is gone. */

const SDLC_TOOLS = new Set([
  // Unified project-run tools (code/goal/general/design/research kinds). A status
  // check also renders the live card (its output carries the cockpit deep-link);
  // `created` stays false since it doesn't end with `_create`.
  'project_run_create', 'project_run_start', 'project_run_status',
])

export interface SdlcRef { kind: 'code' | 'loop'; id: string; created: boolean }

/** Recognize an SDLC create/start tool segment and pull the entity ref out of
 *  its output. Returns null for any other tool. */
export function sdlcRefFromTool(toolName: string | undefined, output: string | undefined): SdlcRef | null {
  if (!toolName || !SDLC_TOOLS.has(toolName) || !output) return null
  const m = output.match(/\/#\/(code|loops)\/([0-9a-f]{6,})/i)
  if (!m) return null
  return { kind: m[1].toLowerCase() === 'code' ? 'code' : 'loop', id: m[2], created: toolName.endsWith('_create') }
}

// Live (in-flight) statuses worth polling on a fast cadence; terminal ones we
// stop polling once reached.
const LIVE = new Set(['running', 'intake', 'planning', 'review'])
const TERMINAL = new Set(['complete', 'failed', 'stopped'])

/** Compact elapsed for the widget header — "3m" / "1h 4m" / "2d". */
function fmtE(sec: number): string {
  if (!sec || sec < 60) return `${Math.max(0, Math.floor(sec))}s`
  const m = Math.floor(sec / 60); if (m < 60) return `${m}m`
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

export function SdlcProgressCard({ refObj }: { refObj: SdlcRef }) {
  const { kind, id, created } = refObj
  // Both kinds are the ONE unified Loop now — fetched via uLoop, read by kind below.
  const [entity, setEntity] = useState<Loop | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const gone = useRef(false)  // 404 → entity deleted; stop polling
  const loaded = useRef(false)  // have we ever fetched the entity? (ref, not stale state)

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    let cancelled = false
    // Reset the per-entity refs when the watched entity changes — they persist across
    // dep changes otherwise (a card reused for a new id would inherit the old entity's
    // gone/loaded state: never polling, or suppressing the new id's first-load error).
    gone.current = false
    loaded.current = false
    const tick = async () => {
      // Cadence is driven by the JUST-FETCHED status, not the closure's `code`/`loop`
      // state — those are captured at effect-creation (null on the first tick) and
      // never update inside this closure, so reading them made every poll fall to the
      // 10s fallback (the 4s-live / 30s-terminal tiers were dead).
      let st: string | undefined
      try {
        const e = await api.uLoop(id); if (!cancelled) { setEntity(e); setErr(null) } st = e?.status
        loaded.current = true
      } catch (e) {
        const status = (e as { status?: number })?.status
        if (status === 404) { gone.current = true; if (!cancelled) setErr('No longer exists (deleted).'); return }
        // Only show the load error before the FIRST successful fetch. A transient
        // blip on a LATER poll must NOT flash an error banner over an already-rendered
        // card — gate on the `loaded` ref, not the stale-closure `code`/`loop` (which
        // are captured null at effect creation + never update here, so the old
        // `!code && !loop` guard was always true → spurious banner on every blip).
        if (!cancelled && !loaded.current) setErr('Could not load progress.')
      }
      if (cancelled || gone.current) return
      // A TERMINAL entity (complete/failed/stopped) never changes again — STOP polling
      // entirely rather than hitting the API every 30s forever. The just-rendered
      // snapshot is final; an old chat with many finished SDLC cards would otherwise
      // each keep a perpetual background poll alive.
      if (st && TERMINAL.has(st)) return
      const delay = st && LIVE.has(st) ? 4000 : 10000
      timer = setTimeout(tick, delay)
    }
    void tick()
    return () => { cancelled = true; if (timer) clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, id])

  // Use the EFFECTIVE status so a budget-exhausted finish (complete + a stop_reason other
  // than 'done') reads as the honest warn-toned "Ended early" here too — matching the Code
  // list + cockpit OutcomeBanner. Raw entity.status alone showed a green "Complete" pill for a
  // non-genuine finish, the one surface that skipped effectiveLoopStatus. Only changes
  // complete→ended_early, so the needs_input/blocked attention branches are unaffected.
  const status = entity ? effectiveLoopStatus(entity.status, entity.stop_reason, entity.held) : (created ? 'ready' : '…')
  // Display the REAL kind from the fetched loop (general/goal/code/design), not the
  // coarse refObj.kind ('code'|'loop') — otherwise a general/design loop mislabels as
  // "Goal Loop". Fall back to the coarse kind until the entity loads.
  const dispKind = entity?.kind || (kind === 'code' ? 'code' : 'goal')
  const meta = loopKindMeta(dispKind)
  const title = entity?.name || `${meta.noun}${dispKind === 'code' ? ' project' : ''}`
  // Code keeps its own section/cockpit route; every other kind resolves under #/loops/<id>.
  const href = `#/${dispKind === 'code' ? 'code' : 'loops'}/${id}`
  const Icon = meta.icon
  const cycles = entity?.total_cycles

  // Progress + steps + parked are the SHARED run view-model (P16 foldRun) — the same
  // pure derivation the cockpits use, so this card can never drift from them. Feed it
  // the DISPLAY kind + EFFECTIVE status (the two card-specific resolutions above:
  // dispKind fixes design/general mislabel, effectiveLoopStatus derives ended_early).
  const vm = entity ? foldRunSnapshot({ ...entity, kind: dispKind, status }) : null
  const progress = vm?.progressLabel ?? ''
  const parked = vm?.parked ?? false
  const steps = vm?.steps ?? []
  // The done/total bar + score/marginal ROI strip are rendered by the shared RunProgress
  // (off `vm`); the header's elapsed clock stays the card's own render concern.
  const elapsed = entity?.elapsed_seconds ?? 0

  // Recent activity — the latest findings (newest last in the store → show tail).
  const findings = entity?.findings || []
  const recent = findings.slice(-3).reverse()

  // When the run is parked ON the user, surface WHY inline — else the pill flips to
  // "needs input"/"blocked" while watching, but the user has to click through to the
  // cockpit to learn what's being asked / what stalled. needs_input → the pending
  // question (+ why); blocked/failed/stagnant → the persisted reason. Mirrors the
  // sdlc_status tool, which relays the same so the chat agent can too.
  const attention = (() => {
    if (!entity) return null
    if (status === 'needs_input') {
      const pq = entity.pending_question
      const q = (typeof pq === 'string' ? pq : pq?.question || '').trim()
      const why = (typeof pq === 'string' ? '' : pq?.why || '').trim()
      // A spend cap's pause asks nothing: its text is the cap's own sentence.
      if (typeof pq !== 'string' && pq?.spend_cap) return { tone: 'warn' as const, label: 'Paused by a spend cap', text: q, sub: why }
      if (typeof pq !== 'string' && pq?.no_tools) return { tone: 'warn' as const, label: 'Paused: its model can’t use tools', text: q, sub: why }
      return { tone: 'info' as const, label: 'Needs your input', text: q || 'Waiting on your answer.', sub: why }
    }
    if (status === 'blocked' || status === 'failed' || status === 'stagnant') {
      const reason = (entity.error_message || '').trim()
      if (reason) return { tone: 'warn' as const, label: loopStatusLabel(status), text: reason, sub: '' }
    }
    return null
  })()

  const isPolling = !entity && !err

  return (
    <motion.div variants={messageEnter} initial="initial" animate="animate" className="my-1.5 overflow-hidden border border-outline-variant/40 bg-surface-low/40" style={{ borderRadius: 'var(--radius-md)' }}>
      {/* header */}
      <div className="flex items-center gap-2 px-3 py-2">
        <Icon size={15} className="shrink-0 text-primary" />
        <span data-type="label-s" className="min-w-0 flex-1 truncate text-on-surface" style={fvs(600)}>{title}</span>
        {progress && <span data-type="caption" className="shrink-0 text-on-surface-low">{progress}</span>}
        {/* The "·" is a SEPARATOR, so it only appears when there is a preceding label to
            separate from. An untracked kind now has no progress label (its cycle count IS
            its progress), and a hard-coded "·" left the header opening with a bare bullet. */}
        {typeof cycles === 'number' && cycles > 0 && <span data-type="caption" className="shrink-0 text-on-surface-low/70">{progress ? '· ' : ''}{cycles} cycles</span>}
        {elapsed > 0 && <span data-type="caption" className="shrink-0 inline-flex items-center gap-0.5 text-on-surface-low/70" title="Elapsed (running time)"><Clock size={10} />{fmtE(elapsed)}</span>}
        <span data-type="caption" className="shrink-0 rounded-pill px-2 py-0.5" style={loopStatusTone(status)}>
          {isPolling ? <Loader2 size={10} className="inline animate-spin" /> : loopStatusLabel(status)}
        </span>
      </div>

      {/* progress bar (done/total stage fill) + score/marginal ROI strip — the shared
          RunProgress, driven by the same foldRun view-model the cockpits use. */}
      {vm && <RunProgress vm={vm} />}

      {/* error / deleted */}
      {err && (
        <div data-type="caption" className="flex items-center gap-1.5 border-t border-outline-variant/30 px-3 py-1.5 text-on-surface-low">
          <AlertTriangle size={11} className="text-warn" /> {err}
        </div>
      )}

      {/* attention banner — what the run needs from the user (question / block reason),
          so the user doesn't have to open the cockpit to find out why it parked. */}
      {attention && (
        <div data-type="caption" className="border-t border-outline-variant/30 px-3 py-2"
          style={{ background: attention.tone === 'info'
            ? 'color-mix(in srgb, var(--color-info) 9%, transparent)'
            : 'color-mix(in srgb, var(--color-warn) 9%, transparent)' }}>
          <div className="mb-0.5 inline-flex items-center gap-1.5"
            style={withWeight({ color: attention.tone === 'info' ? 'var(--color-info)' : 'var(--color-warn)' }, 600)}>
            {attention.tone === 'info' ? <HelpCircle size={11} /> : <AlertTriangle size={11} />} {attention.label}
          </div>
          <p className="whitespace-pre-wrap text-on-surface-var line-clamp-4">{attention.text}</p>
          {attention.sub && <p className="mt-0.5 whitespace-pre-wrap text-on-surface-low line-clamp-2">{attention.sub}</p>}
        </div>
      )}

      {/* steps */}
      {steps.length > 0 && (
        <div className="border-t border-outline-variant/30 px-3 py-2">
          <div data-type="caption" className="mb-1 text-on-surface-low uppercase tracking-wide">{dispKind === 'goal' ? 'Sub-goals' : 'Stages'}</div>
          <ul className="flex flex-col gap-0.5">
            {steps.map((s, i) => (
              <li key={i} data-type="caption" className="flex items-center gap-1.5">
                {/* A checkbox asserts trackable done-state, so only a step whose done-state
                  * someone actually WRITES may get one; everything else gets a neutral dot.
                  * That test comes off the fold's step state now — this used to ask
                  * `dispKind === 'goal'`, a THIRD place enumerating the untracked kinds, and
                  * it was wrong in the same way runFold's was: research and general have no
                  * phase writer either, so a finished research run drew five empty
                  * checkboxes beside a "Complete" badge (issue 448). */}
                {s.state === 'untracked'
                  ? <span className="size-1 shrink-0 rounded-full bg-on-surface-low/50" />
                  : s.state === 'done' ? <CheckCircle2 size={12} className="shrink-0" style={{ color: 'var(--color-ok)' }} />
                  // Active stage warns when the project is PARKED at it (blocked/needs_input/
                  // stagnant/failed/stopped/ended_early) rather than progressing — agrees with
                  // the cockpit StageTrail + the attention banner above. Primary only while live.
                  : s.state === 'active' ? <CircleDot size={12} className="shrink-0" style={{ color: parked ? 'var(--color-warn)' : 'var(--color-primary)' }} />
                  : <Circle size={12} className="shrink-0 text-on-surface-low/40" />}
                <span className={`min-w-0 truncate ${s.state === 'done' ? 'text-on-surface-low line-through' : 'text-on-surface-var'}`}>{s.label}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* recent activity */}
      {recent.length > 0 && (
        <div className="border-t border-outline-variant/30 px-3 py-2">
          <div data-type="caption" className="mb-1 text-on-surface-low uppercase tracking-wide">Recent activity</div>
          <ul className="flex flex-col gap-1.5">
            {recent.map((f, i) => {
              const srcN = Array.isArray((f as { sources_checked?: string[] }).sources_checked) ? (f as { sources_checked?: string[] }).sources_checked!.length : 0
              const newN = typeof (f as { new_findings_count?: number }).new_findings_count === 'number' ? (f as { new_findings_count?: number }).new_findings_count! : null
              return (
                <li key={i} data-type="caption" className="flex flex-col gap-0.5 text-on-surface-var">
                  <div className="flex gap-1.5">
                    <span className="shrink-0 text-on-surface-low/60">#{f.cycle}</span>
                    <span className="min-w-0 line-clamp-2">{f.summary || f.key_insight || '—'}</span>
                  </div>
                  {/* per-cycle signal chips — sources read + new findings (research/goal). */}
                  {(srcN > 0 || newN !== null) && (
                    <div data-type="caption" className="flex flex-wrap gap-1 pl-5 text-on-surface-low/80">
                      {srcN > 0 && <span className="inline-flex items-center gap-0.5 rounded-pill bg-surface-container px-1.5 py-px"><Search size={9} />{srcN} source{srcN === 1 ? '' : 's'}</span>}
                      {newN !== null && newN > 0 && <span className="inline-flex items-center gap-0.5 rounded-pill bg-surface-container px-1.5 py-px">+{newN} new</span>}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {/* cockpit link */}
      <a href={href} data-type="caption" className="flex items-center gap-1 border-t border-outline-variant/30 px-3 py-1.5 text-primary transition-colors hover:bg-surface-low/70">
        Open {dispKind === 'code' ? 'in Code' : `in ${meta.noun}`} <ArrowUpRight size={12} />
      </a>
    </motion.div>
  )
}

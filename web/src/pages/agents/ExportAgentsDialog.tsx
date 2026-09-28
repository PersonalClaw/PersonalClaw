import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Upload } from 'lucide-react'
import { Modal } from '../../ui/Modal'
import { Button } from '../../ui/Button'
import { Checkbox } from '../../ui/forms'
import { InlineError } from '../../ui/InlineError'
import { FormFooter } from '../../ui/FormFooter'
import { notify } from '../../app/appSdk'
import { readableErrText } from '../../lib/errText'
import { api, type AgentExportFileState, type AgentExportPlan, type SavedAgent } from '../../lib/api'

/** What each file state means, beside the agent it belongs to. The gateway decides the state
 *  (`packs/external_formats.py`); these are its words on the one list that shows them. */
const STATE_WORDS: Record<AgentExportFileState, string> = {
  new: 'New file',
  same: 'Already there, unchanged',
  replace: 'Replaces the copy exported earlier. Edits made to that file since are lost.',
  theirs: 'A file PersonalClaw did not write is there. It is never overwritten.',
}

/** A state that stops the export, painted as one. */
const STOPS = new Set<AgentExportFileState>(['theirs'])

type PlannedFile = AgentExportPlan['files'][number]

/** Export your agents into Claude Code's agents folder.
 *
 *  Every tick reads the plan again from the gateway, which writes nothing to answer: the folder
 *  Claude Code reads its agents from (it follows `CLAUDE_CONFIG_DIR`), what writing each agent's
 *  file would do there, and — when the write would not go ahead — the gateway's sentence for why.
 *  "Export" is the confirmation, and it names the folder shown, so a folder that moved since is
 *  refused rather than written. A file PersonalClaw did not write refuses the whole export; the
 *  list says which, and unticking that agent exports the rest. */
export function ExportAgentsDialog({ agents, onClose }: { agents: SavedAgent[]; onClose: () => void }) {
  const order = useMemo(() => agents.map((a) => a.name), [agents])
  const [selected, setSelected] = useState<string[]>(order)
  const [plan, setPlan] = useState<AgentExportPlan | null>(null)
  // Kept across an empty selection, so the folder being confirmed never blinks out of the dialog.
  const [dest, setDest] = useState('')
  const [planErr, setPlanErr] = useState<unknown>(null)
  const [reviewing, setReviewing] = useState(false)
  const [writing, setWriting] = useState(false)
  const [writeErr, setWriteErr] = useState('')
  // Ticks arrive faster than answers; only the answer to the latest selection may paint.
  const latest = useRef(0)

  const review = useCallback((names: string[]) => {
    const mine = ++latest.current
    if (names.length === 0) { setPlan(null); setPlanErr(null); setReviewing(false); return }
    setReviewing(true)
    api.previewAgentExport(names)
      .then((p) => { if (latest.current === mine) { setPlan(p); setDest(p.dest); setPlanErr(null) } })
      .catch((e: unknown) => { if (latest.current === mine) { setPlan(null); setPlanErr(e) } })
      .finally(() => { if (latest.current === mine) setReviewing(false) })
  }, [])
  useEffect(() => { review(selected) }, [selected, review])

  // In the list's own order, whatever order the ticks came in.
  const toggle = (name: string, on: boolean) => {
    setWriteErr('')
    setSelected((cur) => order.filter((n) => (n === name ? on : cur.includes(n))))
  }

  const fileOf = useMemo(() => {
    const m = new Map<string, PlannedFile>()
    for (const f of plan?.files ?? []) for (const e of f.entities) m.set(e, f)
    return m
  }, [plan])
  const heldBack = useMemo(() => new Set((plan?.blocked ?? []).map((b) => b.path)), [plan])

  async function write() {
    if (!plan) return
    setWriting(true)
    setWriteErr('')
    try {
      const r = await api.exportAgents(selected, plan.dest)
      notify(r.message, 'success')
      onClose()
    } catch (e) {
      // The gateway refused (the folder moved, a file appeared in the way) or stopped part-way;
      // its sentence says which and what was written. The plan is read again, so the list shows
      // the folder as it is now.
      setWriteErr(readableErrText(e) || 'The gateway did not answer. Review the list below to see the folder as it is now.')
      review(selected)
    } finally { setWriting(false) }
  }

  const count = selected.length
  const noun = count === 1 ? 'agent' : 'agents'
  const ready = !!plan && !plan.refusal && count > 0 && !reviewing
  // Why Export cannot be pressed, announced on the button itself (`disabledReason`).
  const why = count === 0 ? 'Pick at least one agent to export.'
    : reviewing ? 'The export is still being checked.'
    : planErr || !plan ? 'The export could not be checked, so nothing can be written yet.'
    : plan.refusal ?? ''

  return (
    <Modal title="Export to Claude Code" icon={<Upload size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-l">
        <p data-type="body-s" className="text-on-surface-var">
          Each agent you pick becomes a file Claude Code loads as one of its own agents: its name,
          description, instructions, voice and skill names. Its model, tools, triggers and approval
          settings stay in PersonalClaw, and Claude Code runs it with its own.
        </p>
        {agents.length === 0 ? (
          <p data-type="body-s" className="rounded-lg bg-surface-container px-m py-m text-on-surface-low">
            You have no agents of your own to export yet. The built-in agents and the default agent stay
            with PersonalClaw.
          </p>
        ) : (
          <>
            <div>
              <div data-type="label-s" className="text-on-surface">Destination</div>
              <p data-type="body-s" className="mt-0.5 break-all font-mono text-on-surface">
                {dest || "Finding Claude Code's agents folder…"}
              </p>
              <p data-type="caption" className="mt-0.5 text-on-surface-low">
                Claude Code's agents folder. It follows CLAUDE_CONFIG_DIR when that is set.
              </p>
            </div>
            <ul className="flex flex-col gap-s" aria-label="Agents to export">
              {agents.map((a) => {
                const on = selected.includes(a.name)
                const file = on ? fileOf.get(a.name) : undefined
                const held = !!file && heldBack.has(file.path)
                const stops = held || (!!file && STOPS.has(file.state))
                return (
                  <li key={a.name}>
                    <label className="flex cursor-pointer items-start gap-s rounded-lg bg-surface-container px-m py-2.5">
                      <span className="pt-0.5"><Checkbox checked={on} onChange={(v) => toggle(a.name, v)} ariaLabel={`Export ${a.name}`} /></span>
                      <span className="min-w-0 flex-1">
                        <span className="flex min-w-0 items-center gap-s">
                          <span data-type="body-s" className="truncate font-mono text-on-surface" title={a.name}>{a.name}</span>
                          {file && <span data-type="caption" className="shrink-0 text-on-surface-low">{file.path}</span>}
                        </span>
                        {file && (
                          <span data-type="caption" className={`mt-0.5 block ${stops ? 'text-danger' : 'text-on-surface-low'}`}>
                            {held ? 'Held back by the credential check.' : STATE_WORDS[file.state]}
                          </span>
                        )}
                      </span>
                    </label>
                  </li>
                )
              })}
            </ul>
            {planErr ? (
              <InlineError icon multiline onRetry={() => review(selected)}>
                Couldn't check the export: {readableErrText(planErr) || 'the gateway did not answer'}. Nothing was written.
              </InlineError>
            ) : plan?.refusal ? (
              <InlineError icon multiline>{plan.refusal}</InlineError>
            ) : null}
          </>
        )}
        <FormFooter error={writeErr}>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          {agents.length > 0 && (
            <Button variant="primary" onClick={() => { void write() }} loading={writing} loadingLabel="Exporting…"
              disabled={!ready || writing} disabledReason={ready ? undefined : why}>
              Export {count} {noun}
            </Button>
          )}
        </FormFooter>
      </div>
    </Modal>
  )
}

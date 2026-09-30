import { useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowUpRight, Hand, Play } from 'lucide-react'
import { api } from '../../lib/api'
import { messageEnter } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import { bareName } from './toolRenderers/native'

/** An automation the chat made that runs only when she runs it (`kind: "manual"`). */
export interface ManualAutomationRef {
  id: string
  name: string
  /** What its action still needs allowed when it was made (`needs_grant`); [] when nothing. */
  needsGrant: string[]
}

/** Recognize an `automation_create` result that made a manual automation, from the data the tool
 *  answers with (`<automation-data>`: the saved row and what it still needs allowed). A result that
 *  made another kind, or none, is not one. */
export function manualAutomationFromTool(
  toolName: string | undefined,
  output: string | undefined,
): ManualAutomationRef | null {
  if (!toolName || bareName(toolName) !== 'automation_create' || !output) return null
  const found = output.match(/<automation-data>([\s\S]*?)<\/automation-data>/)
  if (!found) return null
  let data: { trigger?: { id?: unknown; name?: unknown; kind?: unknown }; needs_grant?: unknown }
  try { data = JSON.parse(found[1]) } catch { return null }
  const row = data?.trigger
  if (!row || row.kind !== 'manual' || typeof row.id !== 'string' || !row.id) return null
  const needs = Array.isArray(data.needs_grant) ? data.needs_grant.filter((n): n is string => typeof n === 'string') : []
  return { id: row.id, name: typeof row.name === 'string' && row.name ? row.name : row.id, needsGrant: needs }
}

/** The button she asked for: Run now on the automation the chat made, where the chat made it.
 *
 *  A chat widget's button cannot run anything (its click only comes back to the chat as a message),
 *  so "make me a button that runs the comparison" makes a manual automation, and this is its
 *  button. The run is the Triggers page's Run now, with every check that one has: an automation not
 *  yet allowed answers with what it needs and where to allow it, and that is what shows here. */
export function ManualAutomationCard({ refObj }: { refObj: ManualAutomationRef }) {
  const [busy, setBusy] = useState(false)
  const [said, setSaid] = useState<{ text: string; ok: boolean } | null>(null)
  const open = `#/triggers?open=${encodeURIComponent(refObj.id)}`

  async function run() {
    setBusy(true); setSaid(null)
    try {
      const r = await api.runStoreTrigger(refObj.id)
      if (r.refused) setSaid({ text: r.refused, ok: false })
      else if (r.running) setSaid({ text: 'It is already running.', ok: false })
      else if (r.ok) setSaid({ text: r.status ? `Started: ${r.status}.` : 'Started.', ok: true })
      else setSaid({ text: typeof r.result === 'string' && r.result ? r.result : 'It did not run.', ok: false })
    } catch (e) {
      setSaid({ text: e instanceof Error ? e.message : 'It did not run.', ok: false })
    } finally { setBusy(false) }
  }

  return (
    <motion.div {...messageEnter} className="my-s flex flex-col gap-s rounded-xl border border-outline-variant p-m">
      <div className="flex min-w-0 items-center gap-s">
        <Hand size={15} className="shrink-0 text-on-surface-low" aria-hidden />
        <span data-type="label-s" className="min-w-0 flex-1 truncate text-on-surface" style={fvs(500)}>{refObj.name}</span>
        <TextLink href={open} size="xs" icon={ArrowUpRight} iconPosition="trailing" iconSize={12}
          className="shrink-0 transition-colors" title="Open it on the Triggers page">
          Open
        </TextLink>
      </div>
      <p data-type="caption" className="text-on-surface-low">
        {refObj.needsGrant.length > 0
          ? 'It runs only when you run it, and not until you allow it on the Triggers page.'
          : 'It runs only when you run it.'}
      </p>
      <div className="flex flex-wrap items-center gap-s">
        <Button size="sm" variant="secondary" onClick={() => void run()} loading={busy} ariaLabel={`Run ${refObj.name} now`}>
          <Play size={14} /> Run now
        </Button>
        {said && (
          <span data-type="caption" role="status" className={said.ok ? 'text-ok' : 'text-on-surface-var'}>{said.text}</span>
        )}
      </div>
    </motion.div>
  )
}

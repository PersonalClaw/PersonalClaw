import { useEffect, useState } from 'react'
import { FieldError } from '../../ui/forms'
import { fvs } from '../../design/fontWeight'
import { ShieldAlert, Play, ChevronRight, Check, AlertTriangle } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Markdown } from '../../ui/Markdown'
import { api, hasApiCode, type ToolItem, type ToolInvokeResult } from '../../lib/api'
import { confirm, promptInput } from '../../ui/dialog'
import { schemaProps, typeLabel, SchemaField, SchemaFields, buildArgs, useArgs, type JsonSchema } from './schema'
import { ToolOutput } from './ToolOutput'
import { BUSY_REASON } from '../../ui/unavailable'

/** Tool inspector body for the SidePanel: full parameter signature (view) plus
 *  an expandable "Try it" panel that auto-builds an editable input form from the
 *  param schema and invokes the tool for real via /api/tools/invoke, behind a
 *  confirmation that SCALES WITH `tool.risk_level` (see `RunPanel`).
 *
 *  This used to read "behind a confirm (every tool reports requires_approval)", which was
 *  the stated reason the single confirm was considered sufficient and was measurably false:
 *  30 of 92 tools report `requires_approval: false` (7 of them `caution`), and the run path
 *  never consulted the flag at all — the confirm was unconditional local state. A parenthetical
 *  that attributes the safety to a field nothing reads is how the gap stayed invisible, so it
 *  names the real guard now (#506). */
export function ToolInspector({ tool, serverStatus }: { tool: ToolItem; serverStatus?: { state: string; detail?: string } }) {
  const { props, required } = schemaProps(tool.parameters)

  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap items-center gap-s">
        <span data-type="body-s" className="rounded-pill px-m h-7 inline-flex items-center bg-surface-high text-on-surface-var">{tool.provider}</span>
        <RiskPill risk={tool.risk_level} />
        {tool.requires_approval && <span data-type="body-s" className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }}><ShieldAlert size={13} /> needs approval</span>}
        {tool.disabled && <span data-type="body-s" className="rounded-pill px-m h-7 inline-flex items-center bg-surface-high text-on-surface-low" title="Disabled in the tools list — the agent doesn't see it and Try it can't run it">Disabled</span>}
        {serverStatus && <span data-type="body-s" className="inline-flex items-center gap-1.5" style={{ color: serverStatus.state === 'ready' ? 'var(--color-ok)' : 'var(--color-danger)' }}><span className="size-1.5 rounded-pill" style={{ background: 'currentColor' }} /> {serverStatus.state}</span>}
      </div>

      {tool.description && <Section label="Description"><Markdown>{tool.description}</Markdown></Section>}

      <Section label={`Parameters${props.length ? ` · ${props.length}` : ''}`}>
        {props.length === 0 ? <p data-type="body-s" className="text-on-surface-low">No parameters.</p> : (
          <div className="flex flex-col gap-1.5">
            {props.map(([name, s]) => <ParamRow key={name} name={name} schema={s} required={required.has(name)} />)}
          </div>
        )}
      </Section>

      <RunPanel tool={tool} />
    </div>
  )
}

function ParamRow({ name, schema, required, depth = 0 }: { name: string; schema: JsonSchema; required: boolean; depth?: number }) {
  const nested = schema.type === 'object' ? Object.entries(schema.properties ?? {}) : []
  return (
    <div className="rounded-md bg-surface-container px-m py-2" style={{ marginLeft: depth * 12 }}>
      <div className="flex items-center gap-s flex-wrap">
        <span data-type="body-s" className="font-mono text-on-surface">{name}</span>
        <span data-type="caption" className="text-on-surface-low">{typeLabel(schema)}</span>
        {required && <span data-type="caption" className="text-danger">required</span>}
        {schema.enum && <span data-type="caption" className="text-on-surface-low">· {schema.enum.map(String).join(' | ').slice(0, 60)}</span>}
      </div>
      {/* `inline`: the sink is a `<p>`, and a `<div>` inside a `<p>` is invalid HTML the parser
          hoists out — which moves the text out of this row. A parameter description is a
          sentence anyway; what it needs is its `` `code` `` and emphasis, not paragraphs. */}
      {schema.description && (
        <p data-type="body-s" className="mt-0.5 text-on-surface-var leading-snug">
          <Markdown inline>{schema.description}</Markdown>
        </p>
      )}
      {nested.length > 0 && <div className="mt-1.5 flex flex-col gap-1.5">{nested.map(([n, s]) => <ParamRow key={n} name={n} schema={s} required={(schema.required ?? []).includes(n)} depth={depth + 1} />)}</div>}
    </div>
  )
}

/** The Try-it escalation ladder (#506).
 *
 *  `risk_level` was fetched, rendered as a pill two rows above the run controls, and then
 *  ignored: one warn-toned inline step ran `artifact_list` and `automation_delete_all` with
 *  the same two clicks, while the same page used a `danger: true` modal to remove an MCP
 *  server config. The ceremony was inverted from the risk.
 *
 *  Each rung costs strictly more than the one below it, and the LOWEST rung costs exactly
 *  what it always did — a gate that taxes every tool call trains people to click through it,
 *  which is the failure mode this exists to prevent:
 *
 *   | tier        | ceremony                                        | wire            |
 *   |-------------|-------------------------------------------------|-----------------|
 *   | safe        | the inline "Confirm & run" step (unchanged)      | —               |
 *   | caution     | a modal, dismissible, with a named confirm verb  | —               |
 *   | destructive | a modal that requires TYPING the tool name       | confirm_risk    |
 *
 *  An absent tier takes the CAUTION rung, not the safe one: an external MCP tool that
 *  declares nothing is the least known call on the page, and defaulting the unknown to the
 *  cheapest path is what a risk ladder is for.
 *
 *  Only the destructive rung has backend authority. `POST /api/tools/invoke` refuses an
 *  effective-destructive call that does not name the tier, so that rung is a wire
 *  requirement rather than a local boolean; the caution rung is a UI-side escalation only,
 *  deliberately, because the route cannot gate caution without breaking the cron scripts
 *  that write through `task_create`/`knowledge_create`. */
type Rung = 'inline' | 'modal' | 'typed'

function rungFor(risk?: string): Rung {
  if (risk === 'destructive') return 'typed'
  if (risk === 'safe') return 'inline'
  return 'modal'  // caution, and anything undeclared
}

function RunPanel({ tool }: { tool: ToolItem }) {
  const [open, setOpen] = useState(false)
  const [args, setArgs] = useArgs(tool.parameters)
  const [confirming, setConfirming] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<ToolInvokeResult | null>(null)
  const [formErr, setFormErr] = useState('')
  const { props, required } = schemaProps(tool.parameters)
  const rung = rungFor(tool.risk_level)

  // reset when switching tools
  useEffect(() => { setOpen(false); setResult(null); setConfirming(false); setFormErr('') }, [tool.name])

  /** The destructive ceremony: type the tool's own name. Returns true when it was completed.
   *
   *  The returned value is re-checked here rather than trusted from the dialog's `validate`:
   *  the ceremony IS the gate on this surface, so it cannot live only in the host that
   *  renders it. */
  async function typedConfirm(): Promise<boolean> {
    const typed = await promptInput({
      title: `Run ${tool.name} for real?`,
      body: `${tool.name} is classified DESTRUCTIVE — it can delete data or execute arbitrary commands on this machine. There is no undo.`,
      label: `Type ${tool.name} to confirm`,
      placeholder: tool.name,
      confirmLabel: `Run ${tool.name}`,
      required: true,
      validate: (v) => (v.trim() === tool.name ? null : `Type "${tool.name}" exactly to confirm.`),
    })
    return typed?.trim() === tool.name
  }

  async function invoke(ack?: string): Promise<'ok' | 'needs-ack' | 'failed'> {
    const { args: built, error } = buildArgs(tool.parameters, args)
    if (error) { setFormErr(error); return 'failed' }
    setFormErr(''); setRunning(true); setResult(null)
    try {
      setResult(await api.invokeTool(tool.name, built, tool.provider, ack))
      return 'ok'
    } catch (e) {
      // The route resolves the EFFECTIVE tier per invocation, which can exceed the DECLARED
      // tier rendered here — name inference on an undeclared MCP tool, or a shell call whose
      // command could not be screened. Escalating on the refusal is what keeps the gate from
      // becoming a dead end: the alternative is the user filling in arguments, confirming,
      // and collecting a 403 with no control that can satisfy it (#3062 was the same shape).
      if (!ack && hasApiCode(e, 'risk_confirmation_required')) return 'needs-ack'
      setResult({ ok: false, error: e instanceof Error ? e.message : 'invoke failed' })
      return 'failed'
    } finally { setRunning(false); setConfirming(false) }
  }

  async function run() {
    if (await invoke() !== 'needs-ack') return
    if (!(await typedConfirm())) {
      setResult({
        ok: false,
        error: `${tool.name} resolves as a destructive call for these arguments, so it was not run. Confirm the tool name to run it.`,
      })
      return
    }
    await invoke('destructive')
  }

  /** The entry control at every tier — "Run tool" always means the same thing, only what it
   *  costs to get past it changes. */
  async function onRunPressed() {
    if (rung === 'inline') { setConfirming(true); return }
    if (rung === 'typed') {
      if (!(await typedConfirm())) return
      await invoke('destructive')
      return
    }
    const ok = await confirm({
      title: `Run ${tool.name}?`,
      body: `This invokes ${tool.name} for real with the arguments above — it is not a dry run.`,
      confirmLabel: `Run ${tool.name}`,
    })
    if (ok) await run()
  }

  return (
    <div className="rounded-lg border border-outline-variant/40">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex w-full items-center gap-s px-m py-2.5 text-left">
        <Play size={14} className="text-primary" />
        <span data-type="label-s" className="flex-1 text-on-surface" style={fvs(500)}>Try it</span>
        <ChevronRight size={15} className={`text-on-surface-low transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <div className="px-m pb-m flex flex-col gap-m border-t border-outline-variant/30 pt-m">
          {props.length === 0 ? <p data-type="body-s" className="text-on-surface-low">No inputs — runs as-is.</p> : (
            <div className="flex flex-col gap-m">
              <SchemaFields
                fields={props}
                required={required}
                values={args}
                renderField={(name, schema, isRequired) => (
                  <SchemaField name={name} schema={schema} required={isRequired}
                    value={args[name]} onChange={(v) => setArgs((a) => ({ ...a, [name]: v }))} />
                )}
              />
            </div>
          )}
          {formErr && <FieldError>{formErr}</FieldError>}

          {tool.disabled ? (
            // The invoke endpoint refuses a disabled tool (403 tool_disabled), so
            // offering Run → Confirm here only led to a dead end after the user
            // filled in arguments. The form above stays — it documents the
            // tool's parameters — but the action says why it can't fire.
            <p data-type="body-s" className="flex items-center gap-1.5 text-on-surface-low">
              <ShieldAlert size={14} /> Disabled — turn it on in the tools list to run it.
            </p>
          ) : !confirming ? (
            <Button size="sm" onClick={onRunPressed} disabled={running} disabledReason={BUSY_REASON}><Play size={15} /> Run tool</Button>
          ) : (
            <div className="rounded-md px-m py-2.5" style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)' }}>
              <div data-type="label-s" className="flex items-center gap-1.5 text-warn mb-2" style={fvs(500)}><AlertTriangle size={14} /> This runs <span className="font-mono">{tool.name}</span> for real.</div>
              <div className="flex gap-s">
                <Button size="sm" onClick={run} loading={running} loadingLabel="Running…"><Check size={15} /> Confirm & run</Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={running} disabledReason={BUSY_REASON}>Cancel</Button>
              </div>
            </div>
          )}

          {result && (
            <div className="rounded-md bg-surface-container p-m">
              <div data-type="body-s" className="flex items-center gap-1.5 mb-1.5" style={{ color: result.ok ? 'var(--color-ok)' : 'var(--color-danger)' }}>
                {result.ok ? <Check size={14} /> : <AlertTriangle size={14} />} {result.ok ? 'Success' : 'Error'}
              </div>
              {/* Named, focusable scroll region: measured at 384px tall holding 7051px of output
                  in issue 2515, which is `scrollable-region-focusable` with a computed name of the
                  whole result. `role="group"` + an explicit label is what stops the output becoming
                  the name — same trio as the rest of the family. */}
              <div tabIndex={0} role="group" aria-label="Tool result" className="max-h-96 overflow-y-auto">
                {result.ok
                  ? <ToolOutput text={result.output ?? ''} />
                  : <pre data-type="body-s" className="text-danger font-mono whitespace-pre-wrap break-words">{result.error}</pre>}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div data-type="caption" className="text-on-surface-low uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}

/** Risk pill for the inspector header (tool risk taxonomy). Unlike the list badge
 *  (caution/destructive only), the detail view labels all three levels — including
 *  a green Safe — so the full classification is explicit when inspecting a tool. */
function RiskPill({ risk }: { risk?: 'safe' | 'caution' | 'destructive' }) {
  if (!risk) return null
  const meta = risk === 'destructive' ? { label: 'Destructive', color: 'var(--color-danger)', Icon: ShieldAlert }
    : risk === 'caution' ? { label: 'Caution', color: 'var(--color-warn)', Icon: AlertTriangle }
    : { label: 'Safe', color: 'var(--color-ok)', Icon: Check }
  const { label, color, Icon } = meta
  return (
    <span data-type="body-s" className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" title={`Risk: ${label}`}
      style={{ background: `color-mix(in srgb, ${color} 16%, transparent)`, color }}>
      <Icon size={13} /> {label}
    </span>
  )
}

import { useEffect, useState } from 'react'
import { FieldError } from '../../ui/forms'
import { fvs } from '../../design/fontWeight'
import { ShieldAlert, Play, ChevronRight, Check, AlertTriangle } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Markdown } from '../../ui/Markdown'
import { api, type ToolItem, type ToolInvokeResult } from '../../lib/api'
import { schemaProps, typeLabel, SchemaField, buildArgs, useArgs, type JsonSchema } from './schema'
import { ToolOutput } from './ToolOutput'

/** Tool inspector body for the SidePanel: full parameter signature (view) plus
 *  an expandable "Try it" panel that auto-builds an editable input form from the
 *  param schema and invokes the tool for real via /api/tools/invoke, behind a
 *  confirm (every tool reports requires_approval). */
export function ToolInspector({ tool, serverStatus }: { tool: ToolItem; serverStatus?: { state: string; detail?: string } }) {
  const { props, required } = schemaProps(tool.parameters)

  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap items-center gap-s">
        <span data-type="body-s" className="rounded-pill px-m h-7 inline-flex items-center bg-surface-high text-on-surface-var">{tool.provider}</span>
        <RiskPill risk={tool.risk_level} />
        {tool.requires_approval && <span data-type="body-s" className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }}><ShieldAlert size={13} /> needs approval</span>}
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
      {schema.description && <p data-type="body-s" className="mt-0.5 text-on-surface-var leading-snug">{schema.description}</p>}
      {nested.length > 0 && <div className="mt-1.5 flex flex-col gap-1.5">{nested.map(([n, s]) => <ParamRow key={n} name={n} schema={s} required={(schema.required ?? []).includes(n)} depth={depth + 1} />)}</div>}
    </div>
  )
}

function RunPanel({ tool }: { tool: ToolItem }) {
  const [open, setOpen] = useState(false)
  const [args, setArgs] = useArgs(tool.parameters)
  const [confirming, setConfirming] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<ToolInvokeResult | null>(null)
  const [formErr, setFormErr] = useState('')
  const { props, required } = schemaProps(tool.parameters)

  // reset when switching tools
  useEffect(() => { setOpen(false); setResult(null); setConfirming(false); setFormErr('') }, [tool.name])

  async function run() {
    const { args: built, error } = buildArgs(tool.parameters, args)
    if (error) { setFormErr(error); return }
    setFormErr(''); setRunning(true); setResult(null)
    try { setResult(await api.invokeTool(tool.name, built, tool.provider)) }
    catch (e) { setResult({ ok: false, error: e instanceof Error ? e.message : 'invoke failed' }) }
    finally { setRunning(false); setConfirming(false) }
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
              {props.map(([name, s]) => (
                <SchemaField key={name} name={name} schema={s} required={required.has(name)}
                  value={args[name]} onChange={(v) => setArgs((a) => ({ ...a, [name]: v }))} />
              ))}
            </div>
          )}
          {formErr && <FieldError>{formErr}</FieldError>}

          {!confirming ? (
            <Button size="sm" onClick={() => setConfirming(true)} disabled={running}><Play size={15} /> Run tool</Button>
          ) : (
            <div className="rounded-md px-m py-2.5" style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)' }}>
              <div data-type="label-s" className="flex items-center gap-1.5 text-warn mb-2" style={fvs(500)}><AlertTriangle size={14} /> This runs <span className="font-mono">{tool.name}</span> for real.</div>
              <div className="flex gap-s">
                <Button size="sm" onClick={run} loading={running} loadingLabel="Running…"><Check size={15} /> Confirm & run</Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={running}>Cancel</Button>
              </div>
            </div>
          )}

          {result && (
            <div className="rounded-md bg-surface-container p-m">
              <div data-type="body-s" className="flex items-center gap-1.5 mb-1.5" style={{ color: result.ok ? 'var(--color-ok)' : 'var(--color-danger)' }}>
                {result.ok ? <Check size={14} /> : <AlertTriangle size={14} />} {result.ok ? 'Success' : 'Error'}
              </div>
              <div className="max-h-96 overflow-y-auto">
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

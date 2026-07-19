import { useState } from 'react'
import { ShieldBan, ScanLine, FileCode2, EyeOff, Plus, X, Lock, Globe } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api, type EgressPolicyConfig } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section } from './settingsUI'
import { CardGridSkeleton } from '../../ui/ListScaffold'

/** Security posture → /api/security/stats (counts) + /api/security/denied-commands
 *  (the bash denylist: always-on built-ins shown read-only; user patterns editable). */
export function SecurityPanel() {
  // Posture stats change slowly — persist so a revisit (and a full reload) paints
  // instantly from cache and revalidates in the background.
  const { data: s, refresh: refreshStats } = useCachedData(
    'settings:security', () => api.securityStats().catch(() => null), { persist: true },
  )
  const { data: denied, refresh: refreshDenied } = useCachedData(
    'settings:denied-commands', () => api.deniedCommands().catch(() => null), { persist: true },
  )
  // Adding/removing a user pattern changes the denied-commands COUNT too —
  // refresh both, or the stat tile shows the stale pre-edit number.
  const onDeniedChange = () => { refreshDenied(); refreshStats() }
  if (!s) return <CardGridSkeleton cards={4} cols={2} />

  const cards: { icon: LucideIcon; label: string; value: number; hint: string }[] = [
    { icon: ShieldBan, label: 'Denied commands', value: s.denied_commands, hint: 'Shell patterns blocked from execution' },
    { icon: ScanLine, label: 'Suspicious patterns', value: s.suspicious_patterns, hint: 'Prompt-injection / exfiltration signatures watched' },
    { icon: FileCode2, label: 'Tool schemas', value: s.tool_schemas, hint: 'Tools with enforced argument validation' },
    { icon: EyeOff, label: 'Redaction paths', value: s.redaction_paths, hint: 'Sensitive paths redacted from output' },
  ]

  return (
    <div>
      <PanelHeader title="Security" hint="The enforcement posture protecting this self-hosted instance. Built-in protections are managed in code; you can extend the shell denylist below." />
      <Section title="Active protections">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {cards.map((c) => (
            <div key={c.label} className="flex items-start gap-3 rounded-lg bg-surface-container px-4 py-3">
              <span className="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-md" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
                <c.icon size={17} className="text-primary" />
              </span>
              <div className="min-w-0">
                <div className="text-on-surface text-[1.25rem] tabular-nums" style={{ fontVariationSettings: '"wght" 600' }}>{c.value}</div>
                <div className="text-on-surface text-[0.875rem]">{c.label}</div>
                <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">{c.hint}</div>
              </div>
            </div>
          ))}
        </div>
      </Section>
      {denied && (
        <DeniedCommandsEditor builtin={denied.builtin} user={denied.user} onChange={onDeniedChange} />
      )}
      <EgressPolicyEditor />
    </div>
  )
}

/** Operator overrides for the outbound egress guard. The guard blocks non-public
 *  destinations by default on every fetch/scrape/webhook; a self-hoster relaxes that for
 *  THEIR network here (a homelab LAN service) without weakening the default. A deny wins
 *  over an allow. */
function EgressPolicyEditor() {
  const { data: eg, refresh } = useCachedData(
    'settings:egress', () => api.securityEgress().catch(() => null), { persist: true },
  )
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  if (!eg) return null

  const save = async (next: EgressPolicyConfig) => {
    setBusy(true); setErr('')
    try { await api.setSecurityEgress(next); refresh() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to save') }
    finally { setBusy(false) }
  }

  return (
    <Section title="Network egress" hint="The agent's outbound fetches, scrapes, and webhooks are blocked from reaching non-public addresses (loopback, LAN, cloud metadata) by default — SSRF protection. Relax it for your own network below; a deny always wins over an allow.">
      <div className="flex flex-col gap-4">
        <HostList label="Allowed hosts" hint="Reachable even if they resolve to a private/LAN address (e.g. a homelab service). Bare domain covers subdomains."
          hosts={eg.allow_hosts} disabled={busy}
          onChange={(hosts) => save({ ...eg, allow_hosts: hosts })} />
        <HostList label="Denied hosts" hint="Never reachable, even if public. Overrides an allow."
          hosts={eg.deny_hosts} disabled={busy}
          onChange={(hosts) => save({ ...eg, deny_hosts: hosts })} />
        <label className="flex items-start gap-2.5 rounded-lg bg-surface-container px-3 py-2.5 cursor-pointer">
          <input type="checkbox" checked={eg.allow_private} disabled={busy}
            onChange={(e) => save({ ...eg, allow_private: e.target.checked })}
            className="mt-0.5 size-4 shrink-0 accent-primary" />
          <span className="min-w-0">
            <span className="text-on-surface text-[0.875rem]">Allow all private networks</span>
            <span className="block text-on-surface-low text-[0.8125rem]">Permit egress to any private/LAN address, not just the allow-list. Only on a fully trusted network — this removes SSRF protection for the whole LAN.</span>
          </span>
        </label>
        {err && <div className="text-error text-[0.8125rem]">{err}</div>}
      </div>
    </Section>
  )
}

/** A small add/remove editor for a bare-hostname list. */
function HostList({ label, hint, hosts, disabled, onChange }: {
  label: string; hint: string; hosts: string[]; disabled: boolean; onChange: (hosts: string[]) => void
}) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const h = draft.trim().toLowerCase()
    if (!h || hosts.includes(h)) { setDraft(''); return }
    // bare hostname only (mirror the server guard) — reject scheme/path/port.
    if (h.includes('/') || h.includes(':') || h.includes(' ')) return
    onChange([...hosts, h]); setDraft('')
  }
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 text-on-surface text-[0.875rem]"><Globe size={13} className="text-on-surface-low" /> {label}</div>
      <div className="mb-2 text-on-surface-low text-[0.8125rem]">{hint}</div>
      <div className="flex flex-col gap-1.5">
        {hosts.map((h) => (
          <div key={h} className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
            <code className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]">{h}</code>
            <button type="button" disabled={disabled} onClick={() => onChange(hosts.filter((x) => x !== h))}
              className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface" aria-label={`Remove ${h}`}>
              <X size={15} />
            </button>
          </div>
        ))}
        <div className="flex items-center gap-2">
          <input value={draft} disabled={disabled}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') add() }}
            placeholder="e.g. nas.local"
            className="min-w-0 flex-1 rounded-lg bg-surface-container px-3 py-2 text-on-surface text-[0.8125rem] outline-none placeholder:text-on-surface-low" />
          <button type="button" disabled={disabled || !draft.trim()} onClick={add}
            className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-primary px-3 py-2 text-on-primary text-[0.8125rem] disabled:opacity-50">
            <Plus size={15} /> Add
          </button>
        </div>
      </div>
    </div>
  )
}

/** The bash denied-command denylist: built-in regexes (read-only) + an editable
 *  user list. User patterns are validated as regexes server-side and appended to
 *  the always-on built-ins. */
function DeniedCommandsEditor({ builtin, user, onChange }: { builtin: string[]; user: string[]; onChange: () => void }) {
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const save = async (next: string[]) => {
    setBusy(true)
    setErr('')
    try {
      await api.setUserDeniedCommands(next)
      onChange()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setBusy(false)
    }
  }

  const add = async () => {
    const p = draft.trim()
    if (!p || user.includes(p)) { setDraft(''); return }
    try { new RegExp(p) } catch { setErr('Not a valid regular expression'); return }
    await save([...user, p])
    setDraft('')
  }

  return (
    <Section title="Shell denylist" hint="Regexes matched against every command the agent runs. Built-ins are always enforced; add your own below.">
      <div className="flex flex-col gap-4">
        <div>
          <div className="mb-2 flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]">
            <Lock size={13} /> Built-in ({builtin.length}) — always enforced
          </div>
          <div className="max-h-44 overflow-y-auto rounded-lg bg-surface-container p-2">
            {builtin.map((p) => (
              <code key={p} className="block px-2 py-1 text-on-surface-low text-[0.75rem] tabular-nums">{p}</code>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-2 text-on-surface-low text-[0.8125rem]">Your patterns ({user.length})</div>
          <div className="flex flex-col gap-1.5">
            {user.map((p) => (
              <div key={p} className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
                <code className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]">{p}</code>
                <button type="button" disabled={busy} onClick={() => save(user.filter((x) => x !== p))}
                  className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface" aria-label={`Remove ${p}`}>
                  <X size={15} />
                </button>
              </div>
            ))}
            <div className="flex items-center gap-2">
              <input
                value={draft}
                onChange={(e) => { setDraft(e.target.value); setErr('') }}
                onKeyDown={(e) => { if (e.key === 'Enter') add() }}
                placeholder="e.g. my-secret-tool .*"
                className="min-w-0 flex-1 rounded-lg bg-surface-container px-3 py-2 text-on-surface text-[0.8125rem] outline-none placeholder:text-on-surface-low"
              />
              <button type="button" disabled={busy || !draft.trim()} onClick={add}
                className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-primary px-3 py-2 text-on-primary text-[0.8125rem] disabled:opacity-50">
                <Plus size={15} /> Add
              </button>
            </div>
            {err && <div className="text-error text-[0.8125rem]">{err}</div>}
          </div>
        </div>
      </div>
    </Section>
  )
}

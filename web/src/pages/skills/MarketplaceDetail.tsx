import { useEffect, useState } from 'react'
import { Download, Check, Loader2, FileText, FileDigit, ShieldAlert, ShieldX } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Markdown } from '../../ui/Markdown'
import { api, type SkillSearchResult, type SkillMarketplaceDetail } from '../../lib/api'
import { useGuardedInstall, guardedFromSkill } from '../../lib/useGuardedInstall'
import { fmtInstalls } from './skillMeta'

/** Marketplace skill detail for the SidePanel: frontmatter + rendered SKILL.md
 *  body + file list, with an Install action. The result carries its source
 *  marketplace so detail/install target the right one. */
export function MarketplaceDetail({ result, installed, onInstalled }: {
  result: SkillSearchResult
  installed: boolean
  onInstalled: () => void
}) {
  const [detail, setDetail] = useState<SkillMarketplaceDetail | null>(null)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(installed)
  const marketplace = result.source || 'skills.sh'
  // Shared guarded-install state machine: `blocked` carries the scan outcome —
  // an overridable warning (offer "Install anyway") or a terminal dangerous
  // verdict — plus findings, so the decision is informed, not blind.
  const guarded = useGuardedInstall((force) =>
    api.installSkill(result.id, marketplace, force).then(guardedFromSkill))
  const installing = guarded.busy
  const blocked = guarded.blocked

  useEffect(() => {
    setDetail(null); setErr(''); setDone(installed); guarded.reset()
    api.skillMarketplaceDetail(result.id, marketplace).then(setDetail).catch((e) => setErr(e instanceof Error ? e.message : 'failed to load'))
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [result.id])

  async function install(force = false) {
    const res = force ? await guarded.confirmInstall() : await guarded.install()
    if (res?.ok) { setDone(true); onInstalled() }
  }

  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap items-center gap-s">
        <span className="rounded-pill px-m h-7 inline-flex items-center text-[0.8125rem] bg-surface-high text-on-surface-var">{marketplace}</span>
        {result.installs ? <span className="text-on-surface-low text-[0.8125rem]">{fmtInstalls(result.installs)}</span> : null}
        {detail?.audit_status && <span className="text-on-surface-low text-[0.8125rem]">· {detail.audit_status}</span>}
      </div>

      <div>
        {done
          ? <Button size="sm" variant="secondary" disabled><Check size={15} /> Installed</Button>
          : <Button size="sm" onClick={() => install(false)} disabled={installing}>{installing ? <><Loader2 size={15} className="animate-spin" /> Installing…</> : <><Download size={15} /> Install</>}</Button>}
      </div>
      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
      {guarded.error && <p className="text-danger text-[0.8125rem]">{guarded.error}</p>}

      {/* Supply-chain scan verdict: a dangerous verdict is a hard block (no override); a
          warning is an overridable, calculated risk with "Install anyway". Findings show
          exactly what tripped the scanner so the decision is informed, not blind. */}
      {blocked && (
        <div role="alert" className="rounded-lg px-m py-3 flex flex-col gap-2"
          style={{ background: `color-mix(in srgb, var(--color-${blocked.needsConsent ? 'warning' : 'danger'}) 12%, transparent)` }}>
          <div className="flex items-center gap-2 text-[0.875rem]" style={{ color: `var(--color-${blocked.needsConsent ? 'warning' : 'danger'})`, fontVariationSettings: '"wght" 600' }}>
            {blocked.needsConsent ? <ShieldAlert size={16} /> : <ShieldX size={16} />}
            {blocked.needsConsent
              ? `Security scan flagged ${blocked.scan?.findings?.length ?? 0} warning(s)`
              : 'Blocked: the security scan found dangerous content'}
          </div>
          <p className="text-on-surface-var text-[0.8125rem]">
            {blocked.needsConsent
              ? `This ${blocked.scan?.tier || 'community'} skill scanned with warnings. Review the findings — you can install anyway if you trust the source.`
              : 'This skill contains high-confidence dangerous patterns (e.g. remote code execution, credential exfiltration). It cannot be installed, even with override — this protects against malicious or prompt-injected installs.'}
          </p>
          {blocked.scan?.findings?.length ? (
            <div className="flex flex-col gap-1 mt-0.5">
              {blocked.scan.findings.slice(0, 8).map((f, i) => (
                <div key={i} className="flex items-start gap-2 text-[0.75rem] text-on-surface-low font-mono">
                  <span className="shrink-0 rounded px-1.5 uppercase" style={{ background: `color-mix(in srgb, var(--color-${f.severity === 'dangerous' ? 'danger' : 'warning'}) 18%, transparent)`, color: `var(--color-${f.severity === 'dangerous' ? 'danger' : 'warning'})` }}>{f.severity}</span>
                  <span className="min-w-0"><span className="text-on-surface-var">{f.rule}</span> {f.path && <span className="text-on-surface-low">in {f.path}</span>}</span>
                </div>
              ))}
            </div>
          ) : null}
          {blocked.needsConsent && (
            <div className="mt-1">
              <Button size="sm" variant="secondary" onClick={() => install(true)} disabled={installing}>
                {installing ? <Loader2 size={15} className="animate-spin" /> : <ShieldAlert size={15} />} Install anyway
              </Button>
            </div>
          )}
        </div>
      )}

      {result.description && <p className="text-on-surface text-[0.9375rem] leading-relaxed">{result.description}</p>}

      {detail === null && !err ? <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem]"><Loader2 size={14} className="animate-spin" /> Loading…</div> : detail && (
        <>
          {detail.body && <Section label="SKILL.md"><Markdown>{detail.body}</Markdown></Section>}
          {detail.files?.length > 0 && (
            <Section label={`Files · ${detail.files.length}`}>
              <div className="flex flex-col gap-1">
                {detail.files.map((f) => (
                  <div key={f.path} className="flex items-center gap-s rounded-md bg-surface-container px-m py-1.5">
                    {f.binary
                      ? <FileDigit size={13} className="text-on-surface-low shrink-0" aria-label="binary file" />
                      : <FileText size={13} className="text-on-surface-low shrink-0" />}
                    <span className="flex-1 truncate font-mono text-on-surface-var text-[0.75rem]">{f.path}</span>
                    {f.binary && <span className="shrink-0 rounded bg-surface-high px-1.5 text-on-surface-low text-[0.6rem] uppercase tracking-wide">binary</span>}
                  </div>
                ))}
              </div>
            </Section>
          )}
        </>
      )}
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}

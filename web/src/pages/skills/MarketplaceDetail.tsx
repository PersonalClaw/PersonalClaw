import { useEffect, useState } from 'react'
import { SCAN_FINDINGS_SHOWN, hiddenFindingsNote, ruleGloss } from '../../lib/scanFindings'
import { FieldError } from '../../ui/forms'
import { withWeight } from '../../design/fontWeight'
import { Download, Check, Loader2, FileText, FileDigit, ShieldAlert, ShieldX } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Markdown } from '../../ui/Markdown'
import { api, type SkillSearchResult, type SkillMarketplaceDetail } from '../../lib/api'
import { useGuardedInstall, guardedFromSkill } from '../../lib/useGuardedInstall'
import { fmtInstalls } from './skillMeta'

/** Marketplace skill detail for the SidePanel: the skill's own declarations + rendered
 *  SKILL.md body + file list, with an Install action. The result carries its source
 *  marketplace so detail/install target the right one.
 *
 *  🔑 THIS IS AN INSTALL-CONSENT SURFACE, and it owes what `apps/installConsent` owes.
 *  Same decision — third-party code, a scanner verdict, a list of findings, a yes/no —
 *  reached through a `SidePanel` rather than a `Modal`, so everything the user needs is
 *  already on one scrolling surface with nothing overlaying anything. That is exactly why
 *  a missing disclosure here is silence rather than occlusion, and silence reads as
 *  "probably fine". The findings are glossed from the SHARED `lib/scanFindings` map (never
 *  a second one), a refusal disables the action it refuses, and the skill's declarations
 *  sit above the button rather than after the click. */
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
  // A TERMINAL refusal — the exact condition the card below already branches on to print
  // "It cannot be installed, even with override". Read once so the copy and the control
  // cannot disagree about it (#2527's defect shape, which on this surface is an
  // affordance rather than a sentence: the card said the install was impossible while
  // the primary Install button above it stayed live, offering the refused action again).
  const refused = !!blocked && !blocked.needsConsent

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

      {detail && <SkillDeclarations frontmatter={detail.frontmatter} />}

      <div>
        {done
          ? <Button size="sm" variant="secondary" disabled><Check size={15} /> Installed</Button>
          : <Button size="sm" onClick={() => install(false)} disabled={installing || refused}
              disabledReason={refused ? 'The security scanner refused this skill — a dangerous verdict cannot be overridden.' : undefined}>
              {installing ? <><Loader2 size={15} className="animate-spin" /> Installing…</> : <><Download size={15} /> Install</>}
            </Button>}
      </div>
      {err && <FieldError>{err}</FieldError>}
      {guarded.error && <FieldError>{guarded.error}</FieldError>}

      {/* Supply-chain scan verdict: a dangerous verdict is a hard block (no override); a
          warning is an overridable, calculated risk with "Install anyway". Findings show
          exactly what tripped the scanner so the decision is informed, not blind. */}
      {blocked && (
        <div role="alert" className="rounded-lg px-m py-3 flex flex-col gap-2"
          style={{ background: `color-mix(in srgb, var(--color-${blocked.needsConsent ? 'warning' : 'danger'}) 12%, transparent)` }}>
          <div className="flex items-center gap-2 text-[0.8125rem]" style={withWeight({ color: `var(--color-${blocked.needsConsent ? 'warning' : 'danger'})` }, 600)}>
            {blocked.needsConsent ? <ShieldAlert size={16} /> : <ShieldX size={16} />}
            {blocked.needsConsent
              // 🪤 `hiddenFindingsNote` in `lib/scanFindings` — the residue sentence THIS surface renders
              // directly beneath this heading — already writes `finding${hidden === 1 ? '' : 's'}`.
              // So one card stated the count two ways: a correct plural in the note and a hedge in the
              // heading above it. Zero is reachable here (`?? 0`) and takes the plural, which the
              // conditional gives for free.
              ? `Security scan flagged ${blocked.scan?.findings?.length ?? 0} warning${(blocked.scan?.findings?.length ?? 0) === 1 ? '' : 's'}`
              : 'Blocked: the security scan found dangerous content'}
          </div>
          <p className="text-on-surface-var text-[0.8125rem]">
            {blocked.needsConsent
              ? `This ${blocked.scan?.tier || 'community'} skill scanned with warnings. Review the findings — you can install anyway if you trust the source.`
              : 'This skill contains high-confidence dangerous patterns (e.g. remote code execution, credential exfiltration). It cannot be installed, even with override — this protects against malicious or prompt-injected installs.'}
          </p>
          {blocked.scan?.findings?.length ? (
            <div className="flex flex-col gap-1 mt-0.5">
              {blocked.scan.findings.slice(0, SCAN_FINDINGS_SHOWN).map((f, i) => (
                <div key={i} className="flex items-start gap-2 text-[0.75rem] text-on-surface-low font-mono">
                  <span className="shrink-0 rounded px-1.5 uppercase" style={{ background: `color-mix(in srgb, var(--color-${f.severity === 'dangerous' ? 'danger' : 'warning'}) 18%, transparent)`, color: `var(--color-${f.severity === 'dangerous' ? 'danger' : 'warning'})` }}>{f.severity}</span>
                  <span className="min-w-0"><span className="text-on-surface-var">{f.rule}</span> {f.path && <span className="text-on-surface-low">in {f.path}</span>}
                    {/* 🔑 A RULE NAME IS NOT A DISCLOSURE, on this surface either. `python_exec in
                        scripts/fetch.py` names the scanner's pattern and the file; neither says what
                        the skill could then do to this machine, which is the only question the card
                        asks. PR 2534 fixed exactly this on the app-install card and left `ruleGloss`
                        exported for the second consumer — so this IMPORTS that map rather than
                        writing a second answer to "what does this rule mean", which is the defect
                        shape being removed, not a shortcut.
                        A second line, not a replacement: the mono `rule in path` above stays
                        byte-for-byte what the scanner said, so it still compares against the
                        scanner's own output. `font-sans` because the row is `font-mono` and this is
                        prose — a sentence in a code face reads as more evidence, not as the
                        explanation of it. */}
                    {ruleGloss(f.rule) && (
                      <span className="block font-sans text-on-surface-var" data-type="body-s">{ruleGloss(f.rule)}</span>
                    )}
                  </span>
                </div>
              ))}
              {/* This surface already states the true total above ("flagged N warning(s)"), so what
                  was missing is that the LIST is not all of it. */}
              {hiddenFindingsNote(blocked.scan.findings.length) && (
                <div className="text-[0.75rem] text-on-surface-low italic">
                  {hiddenFindingsNote(blocked.scan.findings.length)}
                </div>
              )}
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
                    {f.binary && <span className="shrink-0 rounded bg-surface-high px-1.5 text-on-surface-low text-[0.75rem] uppercase tracking-wide">binary</span>}
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

/** What the skill DECLARES about itself, on the same surface as the Install button and
 *  ABOVE it — so it is read before the click, not after.
 *
 *  🔑 A DECLARATION DISCLOSED AFTER CONSENT IS NOT A DISCLOSURE. `always: true` is
 *  enforced: `SkillsLoader.get_always_skills()` puts the skill's FULL text into every
 *  session, and the INSTALLED surfaces already say so (`SkillInspector`'s "always loaded"
 *  pill, the chip on the installed list). This panel said nothing, so the one declaration
 *  that changes every future conversation reached the user only once they had already
 *  agreed to it. The wire was never the problem — `handlers/skills.py` puts the parsed
 *  frontmatter on the detail payload under the comment "a marketplace preview must show
 *  the same metadata the loader will read once installed, or the consent surface lies".
 *  Nothing read it.
 *
 *  🪤 A SKILL IS NOT AN APP, so this must not read like `PermissionList`. An app's bullets
 *  are grants the gateway ENFORCES; a skill is instructions the agent reads, and
 *  `allowed-tools` has NO enforcement call site in the backend — the only mention is a
 *  docstring in `skills/marketplace.py`. Rendering it as a bounded grant would invert the
 *  `network` defect (#2513 item 1): copy claiming a confinement the platform does not
 *  impose. So the tool line is attributed to the author, and the advisory floor is stated
 *  either way — absence of `allowed-tools` is not a restriction and must never read as one.
 *
 *  🪤 `always` IS PARSED THE WAY THE LOADER PARSES IT, not the way its neighbour does.
 *  Two backend readers disagree: `handlers/skills.py::_parse_always` accepts
 *  true/yes/1, while `loader.get_always_skills()` — the one that actually performs the
 *  injection — accepts only `"true"`. Mirroring the looser reader would promise
 *  always-on for a `always: yes` skill the loader will not load always. */
function SkillDeclarations({ frontmatter }: { frontmatter: Record<string, unknown> | undefined }) {
  const alwaysOn = String(frontmatter?.always ?? '').trim().toLowerCase() === 'true'
  const tools = String(frontmatter?.['allowed-tools'] ?? '').trim()
  return (
    <div className="rounded-lg bg-surface-container px-m py-3 flex flex-col gap-1.5">
      <div data-type="label-m" className="text-on-surface">What this skill declares</div>
      <div data-type="body-s" className="text-on-surface-var">
        {alwaysOn
          ? 'Loads into every session automatically — its full text sits in the agent\'s context for every conversation, not only when it is relevant.'
          : 'Loads only when it is relevant — the agent sees its name and description, and reads the body when it matches what you are doing.'}
      </div>
      {tools && (
        <div data-type="body-s" className="text-on-surface-var">
          The author says it uses: {tools}.
        </div>
      )}
      <div data-type="body-s" className="text-on-surface-low">
        A skill is instructions your agent reads, not sandboxed code. PersonalClaw does not confine a
        skill to the tools it names, so it can direct the agent to use anything the agent already has
        — the security scan is what gates what a skill may contain.
      </div>
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}

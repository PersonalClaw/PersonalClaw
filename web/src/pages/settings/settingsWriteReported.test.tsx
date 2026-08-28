import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── Reconciling is not the same as reporting ─────────────────────────────────────────────────────
//
// This is the third axis of the same programme: reads claimed things by going silent (669–676),
// writes claimed things by flipping state they never earned (677), and here a write's failure is
// *invisible* — the control ends up honest, or not, but the user is never told which.
//
// The settings hub's shared `mutate()` already did the hard half: it invalidates the widget's cache
// key so a rejected write RECONCILES — the toggle snaps back to the server's answer. Its own doc said
// "Errors are swallowed (the control resets visually)", and that was the whole story. A toggle that
// flips itself back with nothing said reads as a glitchy UI, not as a refusal. Reconciling makes the
// state honest; it does not make the outcome legible.
//
// Three sites, three different states of that idea — and two canonical forms already in the tree:
//
//   settingsWidgets.mutate()   reconciled, said nothing   → now notifies too (7 hub tiles inherit it)
//   VoicePanel.saveSettings    kept the REFUSED value     → rolls back (owns no read) + notifies
//   ModelsPanel.repair         no catch at all            → notifies, like its two siblings in-file
//
// 🪤 `VoicePanel`'s section takes `settings`/`setSettings` as PROPS and owns no read, so it cannot
// reconcile by re-reading — it rolls back to the pre-patch value instead. `WidgetFrame.pin` rolls
// back, `PinnedArtifacts.unpin` reconciles; both are sanctioned. Keeping a refused value is not.

const notified: string[] = []
function mockNotify() {
  notified.length = 0
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (msg: string) => { notified.push(msg) },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('a refused speech setting rolls back and says so', () => {
  async function mountVoice(save: () => Promise<unknown>) {
    mockNotify()
    vi.doMock('../../lib/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        useCaseSettings: () => Promise.resolve({ enabled: false }),
        // 🪤 The toggle is `disabled={!bound}`, and `bound` comes from `modelsActive` — with an empty
        // map the switch never moves and both drives failed for a reason that had nothing to do with
        // the fix. Bind a model for both use cases so the control is actually operable.
        modelsActive: () => Promise.resolve({ stt: ['whisper:base'], tts: ['piper:en'] }),
        saveUseCaseSettings: save,
        personalclawConfig: () => Promise.resolve({}),
        voiceLoopConfig: () => Promise.resolve({}),
      },
    }))
    const { VoicePanel } = await import('./VoicePanel')
    render(<VoicePanel go={() => {}} />)
  }

  it('does not keep a value the server refused', async () => {
    await mountVoice(() => Promise.reject(new Error('stt model unavailable')))
    const toggle = await waitFor(() => screen.getAllByRole('switch')[0])
    const before = toggle.getAttribute('aria-checked')
    fireEvent.click(toggle)
    await waitFor(() => expect(notified.some((m) => /speech setting/i.test(m))).toBe(true))
    // 🔑 The claim, not just the message: the control must not still show the refused value.
    await waitFor(() => expect(screen.getAllByRole('switch')[0].getAttribute('aria-checked')).toBe(before))
    expect(notified[0], "carries the server's own reason").toMatch(/stt model unavailable/)
  })

  it('keeps the new value and stays quiet when the save succeeds', async () => {
    await mountVoice(() => Promise.resolve({ ok: true }))
    const toggle = await waitFor(() => screen.getAllByRole('switch')[0])
    const before = toggle.getAttribute('aria-checked')
    fireEvent.click(toggle)
    await waitFor(() => expect(screen.getAllByRole('switch')[0].getAttribute('aria-checked')).not.toBe(before))
    expect(notified, 'a successful save says nothing').toEqual([])
  })
})

describe('the shared settings mutation reports as well as reconciles', () => {
  const SRC = join(process.cwd(), 'src')
  const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('mutate() notifies on rejection AND still invalidates on both paths', () => {
    const code = codeOf('pages/settings/settingsWidgets.tsx')
    const at = code.indexOf('async function mutate(')
    expect(at, 'the helper must still exist').toBeGreaterThan(-1)
    const fn = code.slice(at, at + 600)
    expect(fn, 'the rejection must be reported').toMatch(/catch \(e\)[\s\S]{0,160}?notify\(/)
    expect(fn, 'and never swallowed silently again').not.toMatch(/catch \{\s*\}/)
    // 🪤 The reconcile is the half that was already RIGHT: the invalidate must run after a failure
    // too, or the control keeps the value the server refused. Asserted by position, not by presence.
    const notifyAt = fn.indexOf('notify(')
    // DSC-14: the helper applies its callers' declared key list through the one data layer
    // (`invalidateSpecs`) instead of looping `invalidateCache` itself, so the busted keys reach
    // every MOUNTED reader of that config rather than only the tile that saved.
    const invalidateAt = fn.indexOf('invalidateSpecs(')
    expect(invalidateAt, 'invalidate must come AFTER the catch block, i.e. on both paths')
      .toBeGreaterThan(notifyAt)
  })

  it('every hub tile inherits it — the call sites still route through mutate', () => {
    const code = codeOf('pages/settings/settingsWidgets.tsx')
    const sites = code.match(/\bmutate\(/g) ?? []
    // 7 call sites + the definition. A tile that stops using it silently loses the report.
    expect(sites.length, 'call sites through the shared helper').toBeGreaterThanOrEqual(8)
  })

  it("the model repair reports, like the two siblings in its own file", () => {
    const code = codeOf('pages/settings/ModelsPanel.tsx')
    const at = code.indexOf('const repair =')
    const fn = code.slice(at, at + 520)
    expect(fn, 'an unhandled rejection made a failed repair look like a dead click').toMatch(/catch \(e\)[\s\S]{0,200}?notify\(/)
    expect(fn, 'and the pending flag still clears on both paths').toMatch(/finally \{ setRepairing\(null\) \}/)
  })

  it('the deliberate optimists are still named, and still deliberate', () => {
    // Not every optimistic write is a defect, and these three were judged rather than swept:
    //   • FeedbackThumbs — documented low-stakes optimism; a re-thumb supersedes, and breaking a chat
    //     turn over a telemetry write would be worse than dropping the signal.
    //   • ProvidersPanel.recheckRuntimes — a READ refresh. Keeping the last known list is the
    //     ux-672 doctrine, not a swallow.
    //   • identity.tsx — belongs to the recorded owner taste call about that file.
    expect(codeOf('ui/FeedbackThumbs.tsx'), 'still optimistic on purpose').toMatch(/catch \{/)
    expect(codeOf('pages/settings/ProvidersPanel.tsx'), 'still keeps the last known runtimes')
      .toMatch(/setRuntimeOverride\(await api\.agentRuntimes\(true\)\)/)
  })

  it('no settings panel still swallows a write into silence', () => {
    // Tree-wide over the settings area: a mutation awaited with an empty catch and no report.
    const dir = join(SRC, 'pages/settings')
    const files = readdirSync(dir).filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\./.test(n))
    const offenders: string[] = []
    for (const n of files) {
      const code = codeOf(join('pages/settings', n))
      // 🪤 THIS MATCHER USED `[^;]` AND THEREFORE ONLY SAW A SINGLE-STATEMENT TRY BODY. Two real
      // offenders sat in this very directory and the sweep reported the area clean for both:
      //
      //   MemoryPanel      `await api.saveMemoryDoc(...); setContent(draft); setSaved(true); ...`
      //   DiagnosticsPanel `await api.setLogLevel(l); setLevel(r.level)`
      //
      // The first `;` after the write ended the character class, so the `catch {}` was never reached.
      // A swallowed write is MORE likely to have a multi-statement body, not less — the statements
      // after the await are exactly the success signals (`setSaved(true)`, a row removal, a value
      // update) whose absence is what makes the failure invisible. Widened to `[\s\S]`, measured
      // against this directory before and after: 0 → 2 offenders on the unfixed tree, and **no false
      // positives across all 52 files**, because the quantifier is lazy and bounded at 200 chars.
      for (const m of code.matchAll(/await api\.(save|patch|set|start|delete|create|update)\w*\([\s\S]{0,200}?catch \{\s*\}/g)) {
        offenders.push(`${n}: ${m[0].slice(0, 60)}`)
      }
    }
    expect(offenders, `these fail silently:\n${offenders.join('\n')}`).toEqual([])
    expect(files.length, 'vacuity floor: the settings area must have been scanned').toBeGreaterThan(20)
  })
})

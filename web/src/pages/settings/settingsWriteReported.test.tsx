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
  /** 🪤 Comments stripped before ANY match, and this is `codeOf`'s own long-standing behaviour lifted
   *  into a name so the tree walk below can share it. The two converted sites now carry comments
   *  quoting the bare `catch { }` they replaced, so a matcher that reads prose finds the defect in its
   *  own documentation — measured twice in this programme, both times in a negative assertion. ONE
   *  definition, called by both: a private copy beside the real thing is the "synthetic guard holding
   *  a COPY of the mechanism" defect this suite has already been bitten by. */
  const strip = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const codeOf = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

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

  it('NO source file anywhere in the tree still swallows a write into silence', () => {
    // 🔑 THE SCAN ROOT WAS THE BUG, NOT THE MATCHER. #2237 widened this pattern from `[^;]` to
    // `[\s\S]` and kept the scope at `pages/settings`, which is where it was born. Run tree-wide over
    // `src`, the same matcher found **four more** offenders in three other areas — and one of them,
    // `knowledge/KnowledgeListPage`, sits ELEVEN LINES ABOVE that file's own first `reportingWrite`
    // call. The file already knew the idiom, four times over; nothing was looking at it.
    //
    // That is this programme's most-repeated shape (a census whose root is narrower than its claim:
    // #2209's excluded-ancestor walk, #2219's `pages/tasks`-rooted "every browsing view"), so the
    // scope moves to `src` and the exemptions below carry the reason instead.
    //
    // 🪤 The file name still says `settings`. The matcher and the hard-won comment above it live here,
    // and copying them to a tree-wide file would create the "synthetic guard holding a COPY of the
    // mechanism" defect this suite has already been bitten by — so the sweep widens in place.
    const walk = (dir: string, out: string[] = []): string[] => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const abs = join(dir, e.name)
        // `withFileTypes` rather than a `statSync` per entry: the per-entry form took 20.5s here and
        // intermittently blew the 20s test timeout, and a rail that reds under load is worse than none.
        if (e.isDirectory()) walk(abs, out)
        else if (/\.tsx?$/.test(e.name) && !/\.(test|doc|spec)\./.test(e.name)) out.push(abs)
      }
      return out
    }
    const WRITE = /await api\.(save|patch|set|start|delete|create|update)\w*\([\s\S]{0,200}?catch \{\s*\}/g

    /** Silent ON PURPOSE, with the reason each one states — keyed by file + the write it guards, never
     *  by line number (see `ui/disabledReasonCensus`, re-keyed after four renumbers).
     *
     *  Both were read in full before being excused, because "it has a comment" is not a reason: */
    const ALLOWED: Record<string, { n?: number; why: string }> = {
      // NOT user-initiated. A mount-time effect deep-merges the step's extracted overrides into the
      // loop so the preview resolves them, guarded by a `merged.current` latch. Nobody asked for it and
      // the very next line loads the tokens anyway, so "preview still loads from whatever's there" is
      // an accurate reason rather than a description of the outcome.
      // 🪤 Its SIBLING in the same file — `setOverride`, wired to `TokensView.onOverride` — is a user
      // edit and is NOT excused; it now reports. One file, two catches, opposite verdicts.
      'pages/loops/DesignStepPreview.tsx  api.updateULoop': {
        why: 'mount-time auto-merge, not a user action; the preview loads regardless',
      },
      // 🔑 EXCUSED ON A MEASUREMENT, AND MY FIRST JUSTIFICATION FOR IT WAS WRONG. I originally wrote
      // that "the status machine surfaces it: `ws.onclose` / an `error` frame paint a Session error
      // overlay" — reasoned from the source, never driven. Driving it says something better:
      //
      //   create a session -> type `exit`      -> overlay reads "Process exited" + "Restart"
      //   refuse POST /api/terminal/sessions   -> click Restart
      //   t+3s / t+9s / t+17s                  -> no overlay at all
      //   type `echo AFTER_RESTART`            -> it ECHOES AND EXECUTES
      //
      // So no error is painted because none is needed: `restart()` keeps the existing session id and
      // bumps `restartKey`, the connect effect re-runs, and the WEBSOCKET RE-BINDS a working session.
      // The user is not stranded and nothing was silently lost — the click did what it promised by a
      // path that does not involve the create call at all.
      //
      // 🪤 Both readings excuse the catch, which is exactly why the wrong one was dangerous: a rail
      // that records a false mechanism sends the next reader to `ws.onclose` looking for a guarantee
      // that lives in the reconnect. The verdict survived; the reason had to be replaced.
      'pages/terminal/TerminalView.tsx  api.createTerminal': {
        why: 'measured: the reconnect re-binds a working session, so the click still succeeds',
      },
    }

    const found: string[] = []
    const files = walk(SRC)
    for (const abs of files) {
      const rel = abs.replace(SRC + '/', '')
      const code = strip(readFileSync(abs, 'utf8'))
      for (const m of code.matchAll(WRITE)) {
        const method = /await (api\.\w+)\(/.exec(m[0])?.[1] ?? 'api.?'
        found.push(`${rel}  ${method}`)
      }
    }

    const surprises = [...new Set(found)].filter((k) => !(k in ALLOWED))
    expect(surprises, `a write whose failure reaches nobody:\n${surprises.join('\n')}`).toEqual([])

    // 🪤 EXACT, not "at most". A `<=` passes once an excused site gains a report and its entry becomes
    // dead weight — an allowance nobody prunes is how the next reader inherits a stale excuse.
    const actual: Record<string, number> = {}
    for (const k of found) actual[k] = (actual[k] ?? 0) + 1
    const expected = Object.fromEntries(Object.entries(ALLOWED).map(([k, v]) => [k, v.n ?? 1]))
    expect(actual, 'the allowance must match the remainder exactly, count included').toEqual(expected)

    expect(files.length, 'vacuity floor: the whole tree must have been scanned').toBeGreaterThan(400)
    expect(
      files.some((f) => f.includes('pages/settings/')) && files.some((f) => f.includes('pages/knowledge/')),
      'the walk must reach outside pages/settings — that scope was the original defect',
    ).toBe(true)
  })

  it('the two writes this sweep found outside settings now report', () => {
    // Named explicitly so a later refactor cannot quietly revert them to a bare catch and re-add an
    // allowance entry instead.
    const shelf = codeOf('pages/knowledge/KnowledgeListPage.tsx')
    expect(shelf, 'the create-shelf call reports').toMatch(
      /createKnowledgeCollection\([\s\S]{0,140}?reportActionFailure\(/)
    expect(shelf, 'and its follow-ups are gated on the result').toMatch(/if \(!res\) return/)

    const preview = codeOf('pages/loops/DesignStepPreview.tsx')
    expect(preview, 'the user token edit reports').toMatch(/reportingWrite\(`save the \$\{path\} override`/)
    expect(preview, 'and its refetch is gated').toMatch(/if \(ok\) await loadTokens\(\)/)
  })
})

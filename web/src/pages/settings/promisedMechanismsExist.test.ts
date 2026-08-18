import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The empty-state hints that promise an automatic future, pinned to the mechanism ─────────────
//
// #1631 found one of these lying: the routing panel promised three axes would "fill in" when only one
// can. That prompted checking the rest of the family, and **the other three are TRUE** — which is worth
// a test rather than a shrug, because each is a claim about a mechanism that could be descoped,
// disabled-by-default, or quietly unwired later. A hint saying "it fills in as…" is the same shape as an
// inert control: it costs the user a wait instead of a click.
//
// Verified here, each by the thing that would falsify it:
//
//   DurabilityPanel  "One appears after the first nightly run"  → `durability.auto_backup` defaults to
//                    True AND the loop's `enabled()` fails SAFE to True, so the nightly job runs on a
//                    default install. (Drills and sync each have their own gate; the snapshot does not.)
//   FeedbackPanel    "👍/👎 appear on inbox classifications, drafted replies, digests, and loop
//                    findings" → all four target kinds really are rendered.
//   MemoryPanel      digests "build on the maintenance cadence, or press Build / refresh above" → the
//                    builder is called from the consolidation cadence AND from the endpoint the button
//                    hits with `?rebuild=1`.

const SRC = join(process.cwd(), 'src')
const PY = join(__dirname, '../../../../src/personalclaw')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const web = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))
const py = (rel: string) => readFileSync(join(PY, rel), 'utf8')

/** One Python method's body: from its `def` line to the next def at the SAME indentation.
 *
 *  🪤 THE HABIT THIS REPLACES. Seven times across these copy-vs-backend cycles I reached for
 *  `.slice(0, N)` as a stand-in for a scope, and it failed on CORRECT code every time the interesting
 *  line sat further down than my guess (here: the digest call is 235 lines into
 *  `_consolidate_locked`, well past 12 000 characters). A character budget is not a scope — anchor the
 *  END. */
function pyMethod(src: string, header: string): string {
  const start = src.indexOf(header)
  if (start < 0) return ''
  const indent = (header.match(/^\s*/) ?? [''])[0]
  const rest = src.slice(start + header.length)
  const next = rest.search(new RegExp(`\n${indent}(async )?def `))
  return next < 0 ? rest : rest.slice(0, next)
}

describe('the nightly-snapshot promise', () => {
  it('is claimed, and the config default makes it true', () => {
    expect(web('pages/settings/DurabilityPanel.tsx')).toContain('One appears after the first nightly run')
    const loader = py('config/loader.py')
    const cfg = loader.slice(loader.indexOf('class DurabilityConfig'))
    const field = cfg.slice(cfg.indexOf('auto_backup'), cfg.indexOf('keep_daily'))
    expect(field, 'auto_backup defaults ON').toMatch(/default=True/)
  })

  it('and the service gate fails SAFE, so an unreadable config still backs up', () => {
    const svc = py('durability/service.py')
    const enabled = pyMethod(svc, 'def enabled()')
    expect(enabled, 'reads the flag').toMatch(/AppConfig\.load\(\)\.durability\.auto_backup/)
    expect(enabled, 'and defaults ON when it cannot').toMatch(/except Exception[\s\S]{0,200}return True/)
    // 🪤 The snapshot has NO extra gate of its own — unlike the drill (`cfg.restore_drills`) and the sync
    // (`sync_enabled`). If one is ever added, the copy has to say so and this fails.
    const due = svc.slice(svc.indexOf('def run_due_jobs'))
    const snapshotBranch = due.slice(due.indexOf('force == "snapshot"'), due.indexOf('drills_on'))
    expect(snapshotBranch, 'no enablement flag inside the snapshot branch').not.toMatch(/_cfg\(\)\./)
  })
})

describe('the feedback-thumbs promise', () => {
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
    })

  it('names four surfaces, and all four render the control', () => {
    expect(web('pages/settings/FeedbackPanel.tsx')).toContain(
      '👍/👎 appear on inbox classifications, drafted replies, digests, and loop findings',
    )
    const kinds = new Set<string>()
    for (const abs of walk(SRC)) {
      for (const m of strip(readFileSync(abs, 'utf8')).matchAll(/<FeedbackThumbs[\s\S]{0,200}?targetKind="([a-z_]+)"/g)) {
        kinds.add(m[1])
      }
    }
    // One per clause of the sentence. A clause without a control is a promise with nothing behind it.
    for (const k of ['inbox_classification', 'inbox_draft', 'inbox_digest', 'loop_finding']) {
      expect([...kinds], `the hint promises ${k}`).toContain(k)
    }
  })
})

describe('the daily-digest promise', () => {
  it('claims a cadence AND a button, and both reach the builder', () => {
    expect(web('pages/settings/MemoryPanel.tsx')).toContain(
      'They build on the maintenance cadence, or press Build / refresh above.',
    )
    expect(web('pages/settings/MemoryPanel.tsx'), 'the button it points at').toContain("'Build / refresh'")
    // The cadence half: reached from session consolidation, not a timer — which is what the code calls
    // its maintenance cadence.
    const consolidate = pyMethod(py('history.py'), '    async def _consolidate_locked')
    expect(consolidate, 'the method body must be found').toMatch(/single_flight|include_history/)
    expect(consolidate, 'the cadence builds digests').toMatch(/self\._svc\.build_daily_digest\(\)/)
    // The button half: the endpoint forces a synchronous build.
    const h = py('dashboard/handlers/memory.py')
    expect(h, 'the rebuild query param drives the same builder').toMatch(
      /rebuild[\s\S]{0,200}?build_daily_digest/,
    )
  })

  it('the builder is still real — not a descoped stub', () => {
    // 🪤 Its section header reads "daily digest (mem-tree, descoped)", which is about the wider mem-tree
    // plan, not this node kind. Worth pinning precisely because that word sits next to working code: a
    // future reader (or a cleanup pass) could take it as licence to delete the builder while the hint
    // keeps promising it.
    const svc = py('memory_service.py')
    const fn = pyMethod(svc, '    def build_daily_digest')
    expect(fn, 'it synthesises per completed day').toMatch(/Only \*completed\* days are digested/)
    expect(fn, 'and writes an episodic record').toMatch(/MemoryKind|MemoryRecord/)
  })
})

describe('the routing card on the Settings home', () => {
  it('asks for an axis that CAN have telemetry', () => {
    // 🔴 It asked for `chat`, the one axis never recorded — so the card was permanently empty for every
    // user while promising the numbers would "land here as models handle work". The fix is the axis, not
    // just the sentence: a card that can never fill is worse than a card that says why.
    const w = web('pages/settings/settingsWidgets.tsx')
    expect(w).toMatch(/use_case: 'reasoning', query_class: 'long_reasoning'/)
    expect(w, 'and the cache key follows the params').toContain("'settings:routing-telemetry:reasoning:long_reasoning'")
    expect(w, 'the unmeasured axis must not come back').not.toMatch(/use_case: 'chat'/)
  })

  it('its empty copy names what is measured', () => {
    expect(web('pages/settings/settingsWidgets.tsx')).toContain(
      'land here as unattended work runs — reasoning, background, loops and orchestration',
    )
  })

  it('the axis it asks for is one the backend actually guards', () => {
    // Paired against the gate, like #1631's panel: widen or narrow the guard and this fails.
    const bridge = py('providers/provider_bridge.py')
    expect(bridge).toMatch(
      /if use_case in \("reasoning", "background", "loops", "orchestration"\):/,
    )
  })
})

describe('the remaining promise-hints, verified and pinned', () => {
  it('suggestions really are built from activity', () => {
    expect(web('pages/dashboard/widgets/Suggestions.tsx')).toContain('they build from your activity')
    const sug = py('suggestions.py')
    expect(sug, 'the prompt context is assembled from memory + recent activity').toMatch(
      /Assemble context for the suggestions prompt from memory and recent activity/,
    )
    expect(sug, 'and it really reads recent history').toMatch(/read_recent_history\(days=2\)/)
  })

  it('the design canvas asks for exactly what the loop is told to write', () => {
    const ui = web('pages/loops/DesignCockpitPage.tsx')
    expect(ui).toContain('As the design loop generates React components')
    expect(ui, 'it fetches artifacts tagged to this loop').toMatch(/api\.artifacts\(\{ tag: `loop:\$\{id\}` \}\)/)
    expect(ui, 'and filters the kind the hint names').toMatch(/\.kind === 'react'/)
    // The other end: the loop's own prompt tells the worker to save exactly that shape.
    expect(py('loop/kinds/design.py'), "the loop's instruction matches the filter").toMatch(
      /artifact_save\(kind='react', tags=\['loop:\{loop\.id\}'\]\)/,
    )
  })

  it('the memory history really does log every write', () => {
    expect(web('pages/settings/MemoryPanel.tsx')).toContain('It fills as agents remember things.')
    const vm = py('vector_memory.py')
    expect(vm, 'the events table is written').toMatch(/INSERT INTO memory_events/)
    // Not "a writer exists" but "the writer is used widely" — one call site would not cover
    // writes AND updates AND deletions, which is what the sentence claims.
    const calls = (vm.match(/self\._log_event\(/g) ?? []).length
    expect(calls, 'writes, updates and deletions all log').toBeGreaterThanOrEqual(10)
  })

  it('intents really do gather as items are saved', () => {
    expect(web('pages/knowledge/KnowledgeListPage.tsx')).toContain('As you save items, it gathers what matches')
    expect(py('knowledge/pipeline/runner.py'), 'the ingest pipeline records the matches').toMatch(
      /relevant matches are recorded as intent_outcomes by value/,
    )
  })
})

describe('the census: every empty-state promise is accounted for', () => {
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
    })

  it('no unverified "nothing here YET, but it will fill" copy ships', () => {
    // 🔑 THE POINT OF A CENSUS over a pile of pins: it makes "have we checked them all?" mechanical. The
    // population is deliberately narrow — an EMPTY state ("No … yet" / "Nothing … yet") whose copy also
    // promises an automatic future — because that pairing is the lie-prone shape. A control description
    // that merely uses the word "as" is not a promise about data arriving.
    const PROMISE = /fills? in|fills as|appears? (after|on)|land here|they build|build (on|from)|will appear|generates/i
    const EMPTY = /No [a-z .'’\-]{2,40}yet|Nothing [a-z .'’\-]{2,40}yet/i
    const VERIFIED = [
      'pages/dashboard/widgets/Suggestions.tsx',      // built from activity — checked
      'pages/settings/DurabilityPanel.tsx',           // nightly job on by default — checked
      'pages/settings/FeedbackPanel.tsx',             // four thumbs surfaces — checked
      'pages/settings/MemoryPanel.tsx',               // digests cadence + memory-event log — checked
      'pages/loops/DesignCockpitPage.tsx',            // loop writes kind:react tagged to the loop — checked
      'pages/settings/RoutingPanel.tsx',              // was FALSE → copy now names the measured axes (#1631)
      'pages/settings/settingsWidgets.tsx',           // was FALSE and unfillable → measured axis (this PR)
      // 🪤 A DIFFERENT AND SAFE CLASS, kept in the list with its reason rather than excluded by a regex
      // I would have had to invent. These promise a future the USER or a LIVE RUN produces, not a
      // background mechanism that might be descoped:
      'pages/ChatPage.tsx',                           // "start a conversation — your sessions will appear": the user's own action makes the row
      'pages/code/CodeCockpitPage.tsx',               // gated on `running`; the files come from the run in flight, and the panel lists the workspace
    ]
    const unverified: string[] = []
    for (const abs of walk(SRC)) {
      const rel = abs.replace(SRC + '/', '')
      if (VERIFIED.includes(rel)) continue
      const src = strip(readFileSync(abs, 'utf8'))
      for (const line of src.split('\n')) {
        if (EMPTY.test(line) && PROMISE.test(line)) unverified.push(`${rel}: ${line.trim().slice(0, 90)}`)
      }
    }
    expect(unverified, 'a new empty state promising a future must be traced to its mechanism first')
      .toEqual([])
  })

  it('the census is not vacuous — the verified files really do carry such copy', () => {
    // 🪤 A list of exemptions with nothing behind it would pass the test above forever. Each entry must
    // still contain the shape it is excused for.
    const PROMISE = /fills? in|fills as|appears? (after|on)|land here|they build|build (on|from)|will appear|generates/i
    for (const rel of ['pages/dashboard/widgets/Suggestions.tsx', 'pages/settings/DurabilityPanel.tsx',
      'pages/settings/FeedbackPanel.tsx', 'pages/settings/MemoryPanel.tsx',
      'pages/loops/DesignCockpitPage.tsx', 'pages/settings/settingsWidgets.tsx',
      'pages/ChatPage.tsx', 'pages/code/CodeCockpitPage.tsx']) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} should still carry a promise`)
        .toMatch(PROMISE)
    }
  })
})

describe('the two user-driven promises are what they claim', () => {
  it('the code cockpit only promises files while a run is in flight', () => {
    // The distinction that keeps it out of the "verify the mechanism" bucket: with no run, the copy says
    // files appear "once it runs" — a conditional, not a background job.
    const ui = web('pages/code/CodeCockpitPage.tsx')
    expect(ui).toMatch(/running \? 'No files yet — the worker will create them here\.'/)
    expect(ui, 'and the idle case says what has to happen first').toContain("appear here once it runs.")
  })

  it('the chat empty state promises only what the user does next', () => {
    expect(web('pages/ChatPage.tsx')).toMatch(
      /title="No chats yet" hint="Start a conversation — your sessions will appear here/,
    )
  })
})

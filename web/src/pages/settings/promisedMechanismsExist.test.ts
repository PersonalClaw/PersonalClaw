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

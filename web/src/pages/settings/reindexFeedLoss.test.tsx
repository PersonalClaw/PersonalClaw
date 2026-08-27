import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Losing the progress feed is not the job failing, and the panel said neither ─────────────────────
//
// Changing the embedding model kicks off a re-index of ALL knowledge and memory, with a live SSE
// progress row. That stream's handler was `es.onerror = () => es.close()` — so on a dropped feed the
// panel froze on its last percentage **forever**. A user could not tell "still working" from "we stopped
// hearing about it", and nothing said which.
//
// 🔑 THE VOCABULARY WAS ALREADY IN THE FILE. The `.catch` beside it records failure ON THE JOB
// (`status: 'error'` + a message) and the panel renders that. The stream path now does the same — no new
// surface, no new state shape.
//
// 🪤 BUT THE EXISTING COPY WOULD HAVE LIED. The error row was hardcoded to
// "Re-index not started: <error>", which is true only for the `.catch` path — that one never starts a job
// and sets `id: ''`. A job with an id DID start; its feed merely dropped. So the prefix is now conditional
// on `reindex.id`, using a discriminator the code already had.
//
// 🪤 AND THE MESSAGE ITSELF MUST NOT OVERCLAIM. The server-side re-index keeps running when the browser's
// stream dies, so the copy says the feed was lost and the job "may still be running" — not that it
// failed. Same discipline as "a failed read is not a deletion".

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('a dropped re-index feed reports itself honestly', () => {
  it('the stream handler records the loss instead of closing in silence', () => {
    const code = read('pages/settings/ModelsPanel.tsx')
    expect(code, 'it still closes the dead stream').toMatch(/es\.onerror = \(\) => \{\s*\n\s*es\.close\(\)/)
    expect(code, 'and records it on the job the panel already renders')
      .toMatch(/status: 'error', error: 'Lost the progress feed/)
  })

  it('the copy does not claim the re-index failed', () => {
    const code = read('pages/settings/ModelsPanel.tsx')
    expect(code).toMatch(/may still be running in the background/)
    expect(code, 'never asserts the job stopped').not.toMatch(/error: 'Re-index failed/)
  })

  it('it only overwrites a RUNNING job, so a finished one is never relabelled', () => {
    expect(read('pages/settings/ModelsPanel.tsx')).toMatch(/r && r\.status === 'running'/)
  })

  it('"Re-index not started" is now conditional on the job never having started', () => {
    // The `.catch` path sets `id: ''`; a stream failure carries a real id.
    const code = read('pages/settings/ModelsPanel.tsx')
    expect(code).toMatch(/\{reindex\.id \? reindex\.error : `Re-index not started: \$\{reindex\.error\}`\}/)
  })

  it('the not-started path that the prefix belongs to still sets an empty id', () => {
    // Vacuity floor: if that path starts carrying an id, the discriminator above stops working and the
    // prefix disappears from a case that needs it.
    expect(read('pages/settings/ModelsPanel.tsx')).toMatch(/setReindex\(\{ id: '', model: '', status: 'error'/)
  })
})

// ── And the bar itself must not invent progress ──────────────────────────────────────────────────────
//
// The same panel's progress bar was pinned to a hardcoded `'40%'` whenever the running phase had no
// total (`reindex.total === 0`, which is exactly what the copy beside it signals with a bare "…").
// So a job that had reported no denominator at all rendered as *nearly half done*, and then jumped
// BACKWARDS the moment a real total arrived. That is the same class of defect as the frozen feed
// above — a surface asserting something it has no basis for — on the visual half instead of the copy.
//
// The fix is the indeterminate wave (`ui/WavyProgress` with no `value`), which is the one primitive
// that already expresses "running, extent unknown". `ui/Meter` deliberately has no indeterminate
// mode, so it is not the answer here. The wave is `aria-hidden` by design, so this does not trade a
// lying bar for an unnamed progressbar.
describe('the re-index bar reports only progress it can compute', () => {
  it('is determinate ONLY when there is a denominator', () => {
    const code = read('pages/settings/ModelsPanel.tsx')
    expect(code, 'the determinate track is gated on a real total').toMatch(
      /\{reindex\.total > 0 \? \([\s\S]{0,400}?rounded-pill bg-primary transition-\[width\]/,
    )
  })

  it('falls back to the indeterminate wave, not a fabricated fill', () => {
    const code = read('pages/settings/ModelsPanel.tsx')
    expect(code, 'the no-total branch renders the indeterminate wave').toMatch(/<WavyProgress width=\{\d+\} \/>/)
    // The wave must stay VALUELESS — passing `value` would make it a determinate bar again, and the
    // type then also demands a label. Either way it would stop meaning "extent unknown".
    expect(code, 'the wave must not carry a value').not.toMatch(/<WavyProgress[^>]*\bvalue=/)
  })

  it("never pins the fill to a literal percentage", () => {
    // Vacuity floor for the sibling rail in `design/meterAdoption.test.ts`: assert the exact defect
    // string is gone from THIS file, so a revert reds here by name and not only in a tree-wide sweep.
    const code = read('pages/settings/ModelsPanel.tsx')
    expect(code, "the hardcoded '40%' fill must stay gone").not.toMatch(/: '40%'/)
    expect(code, 'and no other literal percentage may drive a fill width')
      .not.toMatch(/width: [^,}\n]*['"](?!0%|100%)\d+(?:\.\d+)?%['"]/)
  })
})

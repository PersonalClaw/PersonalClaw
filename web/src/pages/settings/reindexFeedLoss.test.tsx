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

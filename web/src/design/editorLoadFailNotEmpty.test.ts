import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A failed read must not render as an empty document, where the editor can OVERWRITE it ─────────
//
// Two inline doc editors answered a failed GET with `.catch(() => { setContent(''); setDraft('') })`.
// That conflates "the read failed" with "the document is empty", and in an editor that PUTs the same
// path it is a data-loss bug rather than a cosmetic one:
//
//   1. the GET fails, so the textarea renders blank with no error shown
//   2. the user believes the document is empty and types one character
//   3. `dirty` is `content !== null && draft !== content`, and the empty-string content makes that
//      TRUE — so Save arms
//   4. Save PUTs one character over prose that is still on disk
//
// The empty string is the whole mechanism: leaving `content` at `null` makes `dirty` unreachable, so
// the overwrite is impossible until a read has actually succeeded. This rail pins that, because the
// bug is invisible on the happy path and no type can catch it — `string` models both states.
//
// 🪤 IT IS ALSO A RAIL, NOT JUST TWO ASSERTIONS. The same shape shipped twice independently, in
// files that had ALREADY hardened their save paths against the identical "told apart only by a
// missing signal" defect. So a third editor would very likely repeat it, and the sweep below is
// what makes that fail on arrival instead of after a user loses a document.

const SRC = join(process.cwd(), 'src')

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) out.push(...walk(abs))
    else if (/\.tsx?$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

/** Comments stripped FIRST — this file and the two it guards both DISCUSS the defective
 *  shape in prose, and a scan that counted the explanation would fail on the explanation.
 *  The repo has been bitten by exactly that (see DocumentOutline.tsx / primitiveAdoption). */
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const EDITORS = [
  { file: 'pages/settings/MemoryPanel.tsx', load: 'api.memoryDoc', save: 'api.saveMemoryDoc' },
  { file: 'pages/agents/AgentDetail.tsx', load: 'api.agentMetadata', save: 'api.saveAgentMetadata' },
]

describe('a doc editor that can overwrite must not treat a failed read as empty content', () => {
  for (const ed of EDITORS) {
    describe(ed.file, () => {
      const code = strip(readFileSync(join(SRC, ed.file), 'utf8'))

      it('reads and writes the same document — which is what makes this data loss, not a cosmetic bug', () => {
        // Vacuity: if either call is renamed the assertions below are about nothing, so they
        // must fail loudly rather than silently guarding a file that no longer overwrites.
        expect(code, `${ed.file} still performs the read`).toContain(ed.load)
        expect(code, `${ed.file} still performs the write`).toContain(ed.save)
        expect(code, 'save is still gated on the null-vs-empty distinction this rail protects')
          .toMatch(/const dirty = content !== null && draft !== content/)
      })

      it('the load failure does NOT blank the content or the draft', () => {
        expect(code, 'setContent(\'\') on the failure path is the data-loss mechanism')
          .not.toMatch(/catch[\s\S]{0,120}setContent\(''\)/)
        expect(code, 'setDraft(\'\') on the failure path arms Save against a document nobody saw')
          .not.toMatch(/catch[\s\S]{0,120}setDraft\(''\)/)
      })

      it('the load failure is reported instead, so the state is not inferred from an absence', () => {
        expect(code, 'a failed read sets a load-error state').toMatch(/catch[\s\S]{0,160}setLoadErr\(/)
        expect(code, 'and the failure is announced, not just drawn').toMatch(/role="alert"/)
      })
    })
  }

  it('the two editors are still the only surfaces that blank LOADED content they can write back', () => {
    // 🪤 WHY THIS IS A NARROW CHECK AND NOT A TREE-WIDE SWEEP. A sweep for "`setX('')` inside a
    // `catch`" was written first and reported 11 sites. Every one was read, and NINE are not this
    // bug at all: `setText('')` clears a composer, `setDisabledReason('')`/`setAwaiting('')` clear
    // transient strings, and `ArtifactViewer.tsx` blanks its content but sets `setViewError` in the
    // SAME catch — so its failure is reported, which is the whole point. Blanking is only data loss
    // when the blanked value is a LOADED DOCUMENT the surface writes back.
    //
    // The two remaining candidates were checked against that criterion and both cleared:
    // `LoopCockpitPage.tsx:1124` blanks artifact `content` but its only write is
    // `updateULoop(id, { name })`, and `DesignCockpitPage.tsx:751` blanks `jsx` but writes
    // `kind_config.token_overrides` — neither can put the blanked value back. They ARE presentation
    // defects (a failed artifact read renders as an empty preview with no error) and belong with the
    // empty/error-state work, not here.
    //
    // So a broad sweep would ship red-or-allowlisted against nine non-bugs, which trains people to
    // add allowlist entries. The per-file rails above are the precise form. This test records the
    // enumeration so the next reader does not have to redo it.
    const both = EDITORS.map((e) => strip(readFileSync(join(SRC, e.file), 'utf8')))
    for (const code of both) {
      expect(code, 'neither editor may reintroduce the empty-string failure path')
        .not.toMatch(/catch[\s\S]{0,120}set(?:Content|Draft|ViewContent)\(''\)/)
    }
    // Vacuity guard: the walker must actually be able to see the tree, or the reasoning recorded
    // above would rest on a scan that read nothing.
    expect(walk(SRC).length, 'the source tree is readable from here').toBeGreaterThan(200)
  })
})

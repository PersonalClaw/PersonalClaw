import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── Declining a PROPOSAL is "Reject"; clearing an ITEM is "Dismiss" ───────────
//
// The app already draws this line, and draws it consistently — `InboxDetail` uses BOTH verbs, in
// the right places, in one file:
//
//   Dismiss  triages an item off your list        → writes `status: 'dismissed'`
//   Reject   declines a proposal, paired w/Accept → `act('reject')`
//
// So the two words are a real distinction, not interchangeable synonyms, and this test does NOT
// try to unify them. What it pins is the narrower rule: the negative half of an ACCEPT/DECLINE
// pair says "Reject".
//
// Census of every accept/decline pair in the app — there are exactly three:
//
//   skills/SkillProposals.tsx    Accept        ↔ Reject    (api.rejectSkillProposal)
//   inbox/InboxDetail.tsx        Install skill ↔ Reject    (act('reject'))
//   learning/LearningPage.tsx    Accept        ↔ Dismiss   ← the outlier
//
// In the outlier, EVERY layer already said reject: the handler is `decide(row, 'reject')`, the
// verb union is `'accept' | 'reject'`, the endpoint is `rejectLearningProposal`, the prop is
// `onReject`, and the file's own doc comment reads "accept installs, reject …". Only the button
// label said Dismiss — and `LearningPage` has no separate dismiss concept for the label to mean.
// A user reading "Dismiss" would reasonably expect "hide this from my list", not "decline and
// record the decision", which is what the endpoint does.
//
// Source scan because the invariant is about which WORD appears next to Accept across files. A
// render test of one page cannot see a cross-file naming rule, and the pages need live data to
// mount their rows at all.

const PAGES = join(process.cwd(), 'src/pages')

function pageFiles(): string[] {
  const out: string[] = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (/\.tsx$/.test(e.name) && !/\.test\.tsx$/.test(e.name)) out.push(p)
    }
  }
  walk(PAGES)
  return out
}

/** JSX button labels in a source string, as rendered text. */
const LABELS = /<Button\b[^>]*>([\s\S]{0,200}?)<\/Button>/g
/** An accept-side label: "Accept", or a domain verb that installs/applies the proposal. */
const ACCEPTISH = /\bAccept\b|\bInstall skill\b/
/** The decline-side words we care about. */
const DECLINE = /\b(Reject|Dismiss|Decline|Discard)\b/

describe('accept/decline verb parity', () => {
  const files = pageFiles()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(files.length).toBeGreaterThan(40)
    expect(files.some((f) => f.endsWith(join('learning', 'LearningPage.tsx')))).toBe(true)
  })

  it('the decline half of an accept/decline pair says "Reject"', () => {
    const offenders: string[] = []
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      const buttons = [...src.matchAll(LABELS)].map((m) => ({ text: m[1], at: m.index ?? 0 }))
      const accepts = buttons.filter((b) => ACCEPTISH.test(b.text))
      if (!accepts.length) continue
      for (const a of accepts) {
        // The decline button sits beside its Accept — same row, so within a short window.
        const near = buttons.filter((b) => Math.abs(b.at - a.at) < 900 && DECLINE.test(b.text))
        for (const n of near) {
          const word = (n.text.match(DECLINE) ?? [])[1]
          if (word && word !== 'Reject') {
            const line = src.slice(0, n.at).split('\n').length
            offenders.push(`${f.slice(PAGES.length + 1)}:${line} — "${word}" paired with Accept`)
          }
        }
      }
    }
    expect(
      [...new Set(offenders)],
      'The negative half of an Accept/Decline pair must say "Reject". "Dismiss" means something ' +
        'else in this app — triaging an item off a list (InboxDetail writes status: dismissed) — ' +
        'so using it to decline a proposal promises the wrong outcome:\n  ' +
        [...new Set(offenders)].join('\n  '),
    ).toEqual([])
  })

  it('"Dismiss" is still available for its own meaning', () => {
    // The counterpart direction: this rail must not become "ban the word Dismiss". It stays
    // correct for clearing an item, and InboxDetail is the reference for that usage — if this
    // ever fails, the distinction has been flattened rather than respected.
    const inbox = readFileSync(join(PAGES, 'inbox/InboxDetail.tsx'), 'utf8')
    expect(inbox).toMatch(/Dismiss/)
    expect(inbox, 'InboxDetail should still write the dismissed status').toMatch(/status:\s*'dismissed'/)
  })
})

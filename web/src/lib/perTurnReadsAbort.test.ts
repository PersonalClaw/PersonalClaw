// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'

// ── A read its host re-asks every turn aborts the one before it ───────────────────────────────
//
// A component that takes a `refreshKey` prop is told by its host to read again once per turn. The
// organize chip did that with a fresh request each turn and only stopped LISTENING to the one
// before, so a slow answer kept its connection. A browser keeps six HTTP/1.1 connections to the
// gateway for every tab together; seven such reads held them all, and the chat's own send waited
// 138 s inside the browser. `useLatestRead` aborts the read before each new one and the last on
// unmount, so every per-turn reader goes through it, and none keeps a raw effect keyed on the turn.

const SRC = join(process.cwd(), 'src')
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** The components that take a `refreshKey` prop: the population, never the compliant ones. */
function perTurnReaders(): string[] {
  return filesUnder(SRC, (n) => /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n))
    .filter((path) => /\brefreshKey\??:\s*number\b/.test(strip(readSource(path))))
}

describe('a read re-asked every turn aborts the one before it', () => {
  it('finds the per-turn readers it exists for', () => {
    const found = perTurnReaders().map((path) => path.slice(SRC.length + 1))
    for (const known of ['pages/chat/OrganizeChip.tsx', 'pages/chat/SessionSkillsReview.tsx', 'ui/chat/ChatPlanGate.tsx']) {
      expect(found, 'the positive control').toContain(known)
    }
  })

  it('every one reads through useLatestRead, with no effect of its own keyed on the turn', () => {
    const offenders: string[] = []
    for (const path of perTurnReaders()) {
      const src = strip(readSource(path))
      if (!/\buseLatestRead\(/.test(src)) offenders.push(`${path}: reads without useLatestRead`)
      if (/\[[^[\]]*\brefreshKey\b[^[\]]*\]\s*\)/.test(src)) offenders.push(`${path}: an effect keyed on refreshKey`)
    }
    expect(offenders, 'a per-turn read that is never aborted keeps a connection per turn').toEqual([])
  })
})

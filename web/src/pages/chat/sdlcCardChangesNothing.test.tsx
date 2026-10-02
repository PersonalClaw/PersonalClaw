import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { api, type Loop } from '../../lib/api'
import { SdlcProgressCard, type SdlcRef } from './SdlcProgressCard'

// ── The chat card is a status mirror: it pauses, stops and deletes nothing ─────────────────────────
//
// It carried lifecycle controls behind a `controllable` prop for a Projects hub that no longer renders
// it, so nothing turned them on and only its own test reached them. Among them was a Delete that said
// nothing of what it removes, though deleting a Code project force-deletes its task branches and the
// work its ended run kept, which the project's own page names before it asks
// (`code/codeMeta.codeDeleteBody`). The copy is gone: the loop's own page holds the one Delete.

const REF: SdlcRef = { kind: 'code', id: 'abc123', created: false }
const NAME = 'Escape the digest titles'

const loopIn = (status: string): Loop => ({
  id: REF.id, kind: 'code', name: NAME, task: NAME,
  execution: 'solo', agent: 'personalclaw-coder', model: 'local', attended: true,
  max_cycles: 10, idle_secs: 60, success_criteria: null,
  status, total_cycles: 0, error_message: null,
  created_at: 0, started_at: null, completed_at: null,
} as unknown as Loop)

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('the chat card changes nothing about the loop it shows', () => {
  it.each(['running', 'paused', 'needs_input', 'blocked', 'failed', 'complete', 'stopped'])(
    'a %s loop is shown with no control to pause, stop or delete it',
    async (status) => {
      vi.spyOn(api, 'uLoop').mockResolvedValue(loopIn(status))
      render(<SdlcProgressCard refObj={REF} />)
      await waitFor(() => expect(screen.getByText(NAME), 'the card rendered this loop').toBeTruthy())

      expect(screen.queryAllByRole('button'), 'the card offers no lifecycle action').toEqual([])
      // Positive control: what the card is for, the way to the loop's own page, is there.
      expect(screen.getByRole('link', { name: /Open in Code/ }).getAttribute('href')).toBe('#/code/abc123')
    },
  )

  it('and holds no call that would change one', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/chat/SdlcProgressCard.tsx'), 'utf8')
    for (const call of ['deleteULoop', 'uLoopAction']) {
      expect(src, `${call} belongs to the loop's own page`).not.toMatch(new RegExp(`api\\.${call}\\(`))
    }
  })
})

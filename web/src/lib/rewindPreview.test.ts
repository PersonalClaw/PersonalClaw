import { describe, it, expect } from 'vitest'
import type { RewindFileWire } from './api'
import { rewindFileLine, rewindPreviewText } from './rewindPreview'

// What /rewind-to-turn's preview says about each file. The promise it keeps: every file the
// turns being undone changed is on it, and one the rewind cannot put back says so in words —
// never a bare reason code, and never a line a reader takes for "back as it was" when it is not.

const file = (over: Partial<RewindFileWire>): RewindFileWire => ({
  path: '/home/user/project/notes.md',
  action: 'restore',
  turn: 3,
  reason: '',
  current_size: 40,
  restored_size: 12,
  current_sha256: '',
  restored_sha256: '',
  diff: '',
  ...over,
})

describe('rewindFileLine', () => {
  it('says a file changed with no backup stays as it is, by what happened to it', () => {
    expect(rewindFileLine(file({ action: 'not_captured', reason: 'changed' }), 2))
      .toBe('- `/home/user/project/notes.md` — changed after turn 2 with no backup; it stays as it is now')
    expect(rewindFileLine(file({ action: 'not_captured', reason: 'created' }), 2)).toContain('it is not removed')
    expect(rewindFileLine(file({ action: 'not_captured', reason: 'deleted' }), 2)).toContain('it is not brought back')
  })

  it('says why a skipped backup was skipped, in words rather than the store\'s code', () => {
    const line = rewindFileLine(file({ action: 'not_captured', reason: 'outside' }), 2)
    expect(line).toContain('outside this chat’s folders')
    expect(line).toContain('it will not be restored')
    expect(line).not.toContain('(outside)')
    expect(rewindFileLine(file({ action: 'not_captured', reason: 'secret' }), 2)).toContain('a credential file')
  })

  it('says a restore goes back only as far as a backup that came after a change with no backup', () => {
    const partly = rewindFileLine(file({ reason: 'changed_before_backup', turn: 4 }), 2)
    expect(partly).toContain('back to how it was before the agent changed it in turn 4')
    expect(partly).toContain('had no backup, and stays')
    // Vacuity floor: a plain restore promises nothing more than the byte change.
    expect(rewindFileLine(file({}), 2)).toBe('- `/home/user/project/notes.md` — restore 40 → 12 bytes')
  })

  it('says a delete that cannot bring back the earlier file so', () => {
    expect(rewindFileLine(file({ action: 'delete' }), 2)).toContain('it did not exist at turn 2')
    const partly = rewindFileLine(file({ action: 'delete', reason: 'changed_before_backup', turn: 4 }), 2)
    expect(partly).toContain('removed with no backup and is not brought back')
    expect(partly).not.toContain('did not exist at turn 2')
  })
})

describe('rewindPreviewText', () => {
  it('carries the warnings and a line per file, then how to apply it, each block apart', () => {
    const text = rewindPreviewText({
      session: 's', turn: 2, turns_affected: [3],
      warnings: [
        '1 file changed after turn 2 with no backup, so the rewind leaves it as it is.',
        '/home/user/project/plan.md: changed with no backup before its backup in turn 3, so the rewind puts it back only to how it was then',
      ],
      files: [file({ action: 'not_captured', reason: 'changed' }), file({ path: '/home/user/project/plan.md', reason: 'changed_before_backup' })],
    }, 2)
    // Apart, because Markdown reads a sentence right under a list as part of its last item, and two
    // quoted lines next to each other as one quote.
    expect(text.split('\n\n')).toEqual([
      '**Rewind to turn 2 — preview.** Nothing has been written yet.',
      '> 1 file changed after turn 2 with no backup, so the rewind leaves it as it is.',
      '> /home/user/project/plan.md: changed with no backup before its backup in turn 3, so the rewind puts it back only to how it was then',
      '- `/home/user/project/notes.md` — changed after turn 2 with no backup; it stays as it is now\n'
        + '- `/home/user/project/plan.md` — restore 40 → 12 bytes, back to how it was before the agent changed it in turn 3; what changed it before that had no backup, and stays',
      'Run `/rewind-to-turn 2 --confirm` to apply. This restores files only — the conversation stays as the record of what happened.',
    ])
  })

  it('says when no file changed after the turn', () => {
    const text = rewindPreviewText({ session: 's', turn: 2, turns_affected: [3], warnings: [], files: [] }, 2)
    expect(text.split('\n\n')[1]).toBe('_No recorded file changes after that turn._')
  })
})

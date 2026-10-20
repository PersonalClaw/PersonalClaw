import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LifecycleDetail } from './LifecycleDetail'
import type { HookItem } from '../../lib/api'

// ── Cancel must DISCARD the draft, not hand it to the next edit session ─────────────────────────
//
// `#510`. `LifecycleDetail`'s Cancel was `setEditing(false); setErr('')` — it left edit mode
// without restoring anything, and the only restoring effect keys on `[hook.id]`. So re-opening the
// SAME trigger fires no reset and the form re-appears holding the event/matcher/provider the user
// explicitly abandoned. The next Save then writes them.
//
// 🪤 THE `[hook.id]` KEY IS WHY THIS IS NOT SELF-HEALING. Switching to a DIFFERENT trigger and back
// does restore (the id changed twice), so the defect only reproduces on the path a user actually
// takes — cancel, then re-open the same row — which is how it survived. The reset therefore cannot
// live in the effect; it has to be on the Cancel action itself. Six of seven sibling edit surfaces
// already did this, `ScheduleDetail` in the same panel most directly: its Cancel is
// `setDraft(toDraft(job)); setEditing(false); setErr('')`.
//
// Driven through the real component with `editing` threaded as a controlled prop (it is owned by
// the URL, `?edit=1`), because the defect lives in the interaction between that prop and local
// draft state — a source-reading rail could assert the call and still miss a restore that reads
// the wrong thing.

vi.mock('../schedule/ScheduleDetail', () => ({ RunHistory: () => null }))

const SAVED_MATCHER = 'write_file'

const hook = (over: Partial<HookItem> = {}): HookItem => ({
  id: 'h1', name: 'guard-writes', event: 'PreToolUse', matcher: SAVED_MATCHER,
  provider: 'bash', provider_config: { command: 'exit 2' },
  timeout: 30, enabled: true, last_run: 0, last_status: '', run_count: 0, used_by: [],
  ...over,
})

/** The matcher field. Selected by the placeholder the edit form renders for a TOOL-matcher event
 *  (`PreToolUse`) — the component's own output, not a label guess. */
function matcherInput(): HTMLInputElement {
  return screen.getByPlaceholderText('write_file') as HTMLInputElement
}

const providers = [
  { name: 'bash', display_name: 'Bash', supports_blocking: true, settingsSchema: {} },
]

describe('LifecycleDetail Cancel discards the abandoned draft', () => {
  it('re-opening the SAME trigger shows the saved matcher, not the cancelled edit', () => {
    const h = hook()
    const props = { hook: h, providers, onSaved: () => {}, onDeleted: () => {} }
    const { rerender } = render(
      <LifecycleDetail {...props} editing={true} onEditingChange={() => {}} />,
    )

    // 1. The form opens on the SAVED value.
    expect(matcherInput().value).toBe(SAVED_MATCHER)

    // 2. The user edits it…
    fireEvent.change(matcherInput(), { target: { value: 'delete_everything' } })
    expect(matcherInput().value).toBe('delete_everything')

    // 3. …then Cancels. `editing` is controlled, so the page flips it to false.
    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }))
    rerender(<LifecycleDetail {...props} editing={false} onEditingChange={() => {}} />)

    // 4. Re-opening the SAME hook — no id change, so the reset effect does NOT fire.
    rerender(<LifecycleDetail {...props} editing={true} onEditingChange={() => {}} />)
    expect(
      matcherInput().value,
      'the cancelled draft leaked into the next edit session',
    ).toBe(SAVED_MATCHER)
  })

  it('Save is still reachable after a cancel — the restore must not blank the form', () => {
    // Guards the lazy fix: resetting to empty strings would also make the assertion above pass
    // while breaking the form, and `disabled={!name.trim()}` is where that would surface.
    const h = hook()
    const props = { hook: h, providers, onSaved: () => {}, onDeleted: () => {} }
    const { rerender } = render(
      <LifecycleDetail {...props} editing={true} onEditingChange={() => {}} />,
    )
    fireEvent.change(matcherInput(), { target: { value: 'whatever' } })
    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }))
    rerender(<LifecycleDetail {...props} editing={true} onEditingChange={() => {}} />)

    expect(screen.getByDisplayValue('guard-writes'), 'the name must survive the restore').toBeTruthy()
    expect(screen.getByRole('button', { name: /Save/i })).not.toBeDisabled()
  })
})

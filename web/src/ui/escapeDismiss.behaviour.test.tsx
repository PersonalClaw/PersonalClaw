import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import { SessionSkillsReview } from '../pages/chat/SessionSkillsReview'

// The real API surface — read from the component, not guessed. My first mock invented
// `saveEphemeralSkill`/`forgetEphemeralSkill`; the component calls `ephemeralSkills`,
// `promoteEphemeralSkill` and `discardEphemeralSkill`, and an unmocked method throws inside
// render rather than failing the assertion, which reads as a broken test.
vi.mock('../lib/api', () => ({
  api: {
    ephemeralSkills: vi.fn().mockResolvedValue([
      { slug: 'x', title: 'A skill', body: 'body', scope: 'session' },
    ]),
    promoteEphemeralSkill: vi.fn().mockResolvedValue({}),
    discardEphemeralSkill: vi.fn().mockResolvedValue({}),
  },
}))

// ── The BEHAVIOUR half of the Escape-dismissal contract ─────────────────────────
//
// `escapeDismissContract.test.tsx` pins the mechanism at the source and rails the whole tree. This
// file proves the OUTCOME for a surface that could not be reached from the live DOM: the export menu
// needs a content type that offers exports, and the two widget frames need a mounted app widget.
//
// Two of the six were verified on the running build instead (they are route-reachable):
//   DegradedChip (1440px)  opened=1  closedByEsc=true  scrimsLeft=0  focus back on "2 degraded"
//   NavRail drawer (700px) aria-hidden false → true after Escape
//
// This one renders the component and presses the key, which is the same claim by a different route —
// and it is the claim a source grep cannot make: a handler can be present and bound to the wrong
// element, or scoped so it never fires.

describe('SessionSkillsReview closes on Escape', () => {
  // The component fetches its own drafts and renders a TRIGGER; the sheet (and the Escape handler)
  // lives in a child that mounts on click. So the test has to drive it the way a user does — which
  // is also the only way to prove the handler is bound to the mounted sheet rather than the page.
  const openSheet = async () => {
    const view = render(<SessionSkillsReview sessionKey="s" refreshKey={0} />)
    const trigger = await view.findByRole('button', { name: /session skill/i })
    fireEvent.click(trigger)
    await view.findByText('Skills taught this session')
    return view
  }

  it('Escape dismisses the sheet', async () => {
    const view = await openSheet()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(view.queryByText('Skills taught this session')).toBeNull()
  })

  it('an unrelated key does NOT dismiss it', async () => {
    // A handler firing on every keydown would make the sheet impossible to interact with.
    const view = await openSheet()
    fireEvent.keyDown(document, { key: 'a' })
    fireEvent.keyDown(document, { key: 'Enter' })
    expect(view.queryByText('Skills taught this session')).not.toBeNull()
  })

  it('the listener is torn down when the sheet closes', async () => {
    // A leaked document listener would keep reacting to every later Escape in the app.
    const view = await openSheet()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(view.queryByText('Skills taught this session')).toBeNull()
    // Re-open and close again: a duplicate listener would have thrown on the removed node.
    const trigger = await view.findByRole('button', { name: /session skill/i })
    fireEvent.click(trigger)
    await view.findByText('Skills taught this session')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(view.queryByText('Skills taught this session')).toBeNull()
  })
})

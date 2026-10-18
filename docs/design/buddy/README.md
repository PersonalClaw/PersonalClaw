# Desktop buddy

PersonalClaw's own character, living at the top edge of the screen or floating where the owner puts
it. It shows what the agents are doing while the owner works in other apps, and asks only when
something needs the owner. It is a new face on signals PersonalClaw already has, not a second app,
and it adds no authority: it never approves, sends or runs anything without an explicit click or
key press.

v0.3.0 ships it as an opt-in status surface, off by default: the exact state of the work in focus,
one Waiting on You list, one Running Now list, one focused status card, and Open in PersonalClaw.
Four visual families (quiet, active, waiting and blocked) carry urgency, and words always name the
exact phase. The release has to show that the buddy is useful, stays out of the owner's way, is
accessible, and costs next to nothing while hidden. Gate D ([#3902]) proves the last two; the
maintainer's dated sign-off after a week of daily use, recorded in the release notes, is the
evidence for the first two.

Play, the drop, quick ask, sound, the portable sprite sheet and bridges to other desktop companions
are designed here as [later work](#later-work). None of them can block v0.3.0, and they return only
after that sign-off.

[`web/PRODUCT.md`](../../../web/PRODUCT.md) and [`web/DESIGN.md`](../../../web/DESIGN.md) win every
conflict with this record, and [`web/REDESIGN.md` §7](../../../web/REDESIGN.md#7-desktop-buddy)
holds the redesign's rules for the buddy. Each convention here says what it does and why it helps,
in PersonalClaw's own terms. The record follows the design posted on the buddy's epic ([#3829]) as
each task's approved update and settled answers change it.

## How it behaves

- **Two layers in v0.3.0, with a fixed priority.** The truth layer decides what the face and its
  one line say: the phase of the work in focus and the system's state. The presence layer decides
  how much room it has. Presence changes the room, never the words, and a truth change always shows.
- **Hidden unless work is running or something waits on the owner, and free while hidden:** no
  timers, no animation frames, no pointer sampling, and no polling, media or audio work.
- **It peeks** when the owner rests the pointer on the top edge, shows the work in focus while the
  owner is active, opens on a hover, a click or an alert, and folds back on its own.
- **One piece of work in focus, the rest in sight.** The focused work gets its face, its state and
  its run as a three-line step window that scrolls as steps finish; every other piece of live work
  is a small face and one line.
- **Only approvals, questions and errors interrupt.** They come one at a time, in the order every
  surface keeps (consequence, then age), stay until answered, and show their brief exactly as the
  app does. ⌘Enter (Ctrl+Enter elsewhere) approves the request that has focus, Esc closes without
  deciding, Deny is an explicit control with no key, and a broad-reach approval asks to confirm
  first.
- **It respects** absence (it hides while the owner is away and says what happened on return),
  quiet hours (a quiet peek) and full-screen apps (alerts only). Listening always wins.
- **One character and one state contract on every surface:** the top-edge island or the floating
  character, the tray and dock faces (a state, not a red count), notifications (the face and one
  line), and the phone companion's two views.
- **Reduced motion and the expressiveness and bounciness dials are honoured everywhere,** and every
  colour scheme retints it.

## Order of work and the maintainer's two gates

1. This record, and the shared signals: one phase contract ([#3833]), one list of what is waiting
   on the owner ([#3888]) and one list of what is running now ([#3889]).
2. The character, chosen by the maintainer from three directions at real sizes (**gate one**,
   [#3830]), and the rig, chosen by a measured spike ([#3887]), side by side.
3. The state machine ([#3890]) and the renderer ([#3891]), then the playground ([#3892]), which the
   maintainer plays before any surface ships (**gate two**).
4. The window ([#3831]), presence and alerts ([#3832]) and the expanded card ([#3893]).
5. The tray and dock ([#3896]), alert notifications ([#3897]), the phone companion ([#3898]), copy
   ([#3899]) and settings ([#3901]). Copy is needed early: presence and alerts depend on it.
6. The acceptance task ([#3902]): accessible, honest about cost, checked in CI. It is the buddy's
   part of the redesign's Gate D ([#3800]).

The originality record ([#4570]) is checked on this written design before the window starts, and
gate two confirms its motion rows.

## Out of scope for v0.3.0

Answering from notifications (an answer needs its brief), a colour per agent (coral stays the one
colour that means an agent is alive), voice conversation with the buddy, native phone apps, and the
later work below.

## Ships in v0.3.0

- [`state-machine.md`](state-machine.md): the two layers and their priority,
  [the four families](state-machine.md#the-four-families),
  [the anatomy of a state](state-machine.md#the-anatomy-of-a-state),
  [inputs](state-machine.md#inputs),
  [which work is in focus](state-machine.md#which-work-is-in-focus),
  [the alert queue](state-machine.md#the-alert-queue),
  [presence rules](state-machine.md#presence-rules), [the badge](state-machine.md#the-badge),
  [the invariants](state-machine.md#invariants-each-a-test) and
  [the timing names](state-machine.md#timing-names).
- [`interruption-matrix.md`](interruption-matrix.md), with the same matrix as data in
  [`interruption-matrix.json`](interruption-matrix.json):
  [how to read it](interruption-matrix.md#reading-it) and
  [the shipping situations](interruption-matrix.md#the-shipping-situations).
- [`signals.md`](signals.md):
  [one phase contract](signals.md#1-one-phase-contract) with its four families,
  [one list of what is waiting on the owner](signals.md#2-one-list-of-what-is-waiting-on-the-owner)
  (Waiting on You),
  [one list of what is running now](signals.md#3-one-list-of-what-is-running-now) (Running Now)
  and [the doorbell](signals.md#the-doorbell).
- [`surfaces.md`](surfaces.md): [the room budget](surfaces.md#the-room-budget),
  [many pieces of work at once](surfaces.md#many-pieces-of-work-at-once),
  [the expanded card](surfaces.md#the-expanded-card-top-to-bottom) (the focused status card, with
  Open in PersonalClaw), [compact layouts](surfaces.md#compact-layouts),
  [docking](surfaces.md#docking) and
  [how a state change travels](surfaces.md#how-a-state-change-travels).
- [`interactions.md`](interactions.md): every input's outcome,
  [the context menu](interactions.md#the-context-menu),
  [keyboard and focus](interactions.md#keyboard-and-focus),
  [screen readers](interactions.md#screen-readers) and
  [pointer and click-through](interactions.md#pointer-and-click-through).
- [`motion.md`](motion.md): [timing tokens](motion.md#timing-tokens),
  [transitions](motion.md#transitions), [the dials](motion.md#the-dials) with reduced motion, and
  [cost rules](motion.md#cost-rules), what it costs while hidden among them. The timings' values are
  in [`docs/design/motion.md` §9](../motion.md#9-the-desktop-buddys-timings).
- [`copy.md`](copy.md): [voice](copy.md#voice),
  [one line per state](copy.md#one-line-per-state),
  [maximum length per surface](copy.md#maximum-length-per-surface) and [tests](copy.md#tests).
- [`settings.md`](settings.md): every setting's default and home, the opt-in that is off by default
  among them, and [where each setting lives, and why](settings.md#where-each-setting-lives-and-why).
- [`character.md`](character.md): [constraints](character.md#constraints),
  [three directions to explore](character.md#three-directions-to-explore),
  [the choice](character.md#the-choice), still open,
  [the poses](character.md#poses-every-direction-must-cover) and
  [the gesture vocabulary](character.md#the-gesture-vocabulary).
- [`rig.md`](rig.md): [candidates](rig.md#candidates), [what is measured](rig.md#what-is-measured)
  and [the starting position](rig.md#starting-position).
- [`test-plan.md`](test-plan.md): [logic](test-plan.md#logic), [consent](test-plan.md#consent),
  [pixels](test-plan.md#pixels), [motion](test-plan.md#motion), [cost](test-plan.md#cost),
  [the real window](test-plan.md#the-real-window),
  [accessibility](test-plan.md#accessibility) and [the acceptance run](test-plan.md#acceptance-run).
- [`decisions/`](decisions/README.md): where the rig decision and the originality record go.

## Later work

Designed here, and not part of v0.3.0: none of it can block the status MVP or Gate D. Each
later-work section opens with a line naming its task, and the matrix test
(`tests/test_buddy_design_matrix.py`) fails if a shipping cell names a play reaction, a drop or a
quick ask.

- **Play** ([#3894]): [play rules](state-machine.md#play-rules),
  [play timing names](state-machine.md#play-timing-names) and
  [later inputs](state-machine.md#later-inputs); in the matrix,
  [the play situation](interruption-matrix.md#play-reaction-poked-annoyed-dizzy-greeting-or-a-glance),
  [play in the shipping situations](interruption-matrix.md#play-in-the-shipping-situations) and
  [play clauses in the shipping cells](interruption-matrix.md#play-clauses-in-the-shipping-cells);
  [play inputs](interactions.md#play-inputs);
  [play and drop transitions](motion.md#play-and-drop-transitions) and
  [the gaze spring](motion.md#the-gaze-spring);
  [play and drop poses](character.md#play-and-drop-poses);
  [the play setting](settings.md#play-reactions);
  [measurements that move with later work](rig.md#measurements-that-move-with-later-work); and
  [play and drop tests](test-plan.md#play-and-drop-tests).
- **Drop and quick ask** ([#3895]):
  [the file situation](interruption-matrix.md#file-held-over-it-or-the-drop-choice-is-open),
  [the drop in the shipping situations](interruption-matrix.md#the-drop-in-the-shipping-situations),
  [the drop choice](interactions.md#the-drop-choice) and [quick ask](interactions.md#quick-ask),
  and the drop's parts of the play sections above: its rule, inputs, transitions, poses and tests.
- **Sound** ([#3900]): [`sound.md`](sound.md), with [the event map](sound.md#event-map),
  [its rules](sound.md#rules) and [the brief for the original set](sound.md#brief-for-the-original-set);
  and [the sound and volume settings](settings.md#sound-and-volume).
- **The portable sprite sheet** ([#3903]): [the sprite sheet](rig.md#the-portable-sprite-sheet).
- **Bridges to other desktop companions** ([PersonalClaw/PersonalClawApps#164]):
  [the bridge](surfaces.md#bridges-to-other-desktop-companions).

[#3800]: https://github.com/PersonalClaw/PersonalClaw/issues/3800
[#3829]: https://github.com/PersonalClaw/PersonalClaw/issues/3829
[#3830]: https://github.com/PersonalClaw/PersonalClaw/issues/3830
[#3831]: https://github.com/PersonalClaw/PersonalClaw/issues/3831
[#3832]: https://github.com/PersonalClaw/PersonalClaw/issues/3832
[#3833]: https://github.com/PersonalClaw/PersonalClaw/issues/3833
[#3887]: https://github.com/PersonalClaw/PersonalClaw/issues/3887
[#3888]: https://github.com/PersonalClaw/PersonalClaw/issues/3888
[#3889]: https://github.com/PersonalClaw/PersonalClaw/issues/3889
[#3890]: https://github.com/PersonalClaw/PersonalClaw/issues/3890
[#3891]: https://github.com/PersonalClaw/PersonalClaw/issues/3891
[#3892]: https://github.com/PersonalClaw/PersonalClaw/issues/3892
[#3893]: https://github.com/PersonalClaw/PersonalClaw/issues/3893
[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#3896]: https://github.com/PersonalClaw/PersonalClaw/issues/3896
[#3897]: https://github.com/PersonalClaw/PersonalClaw/issues/3897
[#3898]: https://github.com/PersonalClaw/PersonalClaw/issues/3898
[#3899]: https://github.com/PersonalClaw/PersonalClaw/issues/3899
[#3900]: https://github.com/PersonalClaw/PersonalClaw/issues/3900
[#3901]: https://github.com/PersonalClaw/PersonalClaw/issues/3901
[#3902]: https://github.com/PersonalClaw/PersonalClaw/issues/3902
[#3903]: https://github.com/PersonalClaw/PersonalClaw/issues/3903
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
[PersonalClaw/PersonalClawApps#164]: https://github.com/PersonalClaw/PersonalClawApps/issues/164

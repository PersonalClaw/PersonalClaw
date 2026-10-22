# The originality record

The side-by-side check of the buddy's design ([#4570]): every element that ships in v0.3.0, and
every element of its later work, compared with other products' status indicators and assistant
orbs, one row each. The comparison used public material only (no install, no sign-in), and its
notes are kept outside the repository. A row describes the buddy's own element in product terms:
this record names no other product, and no image, clip or sample from the comparison is committed.

**The rule** (the owner ruling of 2026-10-18 on [#3830]): a shared mechanism is fine (a badge on
an edge, an orb that answers a state, a nod for yes); a drawn form or choreography a viewer would
recognise as another product's is not. The dots and their states are the agent orb's licensed base
([`agent-orb.md`](../../agent-orb.md#the-component), [#3807]), shared on purpose, so they are not
judged on their own: the character's rows judge what the buddy adds and the whole orb. Layouts are
compared by where the orbs sit and how they group, motion by what the orb and its dots do and in
which order, and sounds by pitch contour, timbre and length.

**The verdicts:** `distinct`, nothing in it a viewer would recognise; `changed`, a close match the
design now replaces, with what it replaced; `pending`, later work, compared and resolved with the
task its row names before that feature ships. No row that ships in v0.3.0 reads `pending`.

`tests/rails/test_buddy_originality_record.py` reads the elements from the design files themselves
(the character's aspects, each surface of [the room budget](../surfaces.md#the-room-budget), both
docking shapes, the four families, each row of [the transitions](../motion.md#transitions), the
notification treatment, and the later play and drop rows, the sound event map, the export and the
bridges), so an element added to the design without a row here fails it.

## Ships in v0.3.0

Compared on the written design and the drawn poses on 2026-10-22. Gate two ([#3892]) confirms the
motion rows in motion.

### The character

One row per aspect of the check, as [`character.md`](../character.md#the-side-by-side-check) records
it ([#3830]).

| Element | In product terms | Verdict | What changed and why | Date |
|---|---|---|---|---|
| `character.outline` | a round orb of many small separate dots in the scheme's one primary, neutral at rest | distinct | no glow, no gradient fill, no liquid surface; never a few large dots, never a colour per dot, never a ring | 2026-10-22 |
| `character.badge` | a small still mark on the orb's upper edge (the working mark, an alert kind's mark, the check, the microphone, offline's slash) that unfolds into the state's words | distinct | the marks are the app's own and hold still; the working mark never pulses dot by dot, and the badge sits on the upper edge, not a lower corner | 2026-10-22 |
| `character.wash` | a soft gradient up from the orb's lower edge, in the one tone of the alert | distinct | it rises once under the orb and holds; it never pulses, never takes several colours and never lights the screen's edge | 2026-10-22 |
| `character.poses` | each phase's pose: the dots' density, spread and sink, in two colours, with the badge | distinct | the state is in the dots and the badge, never in a colour per state or a spinning arc | 2026-10-22 |
| `character.gestures` | point, wave, stir, raise, hold, droop, flourish: motions of the orb's dots and of the whole orb | distinct | the wave is a ripple out from the centre, never dots bouncing in a row; the flourish is one swirl that comes to rest, never a continuous churn | 2026-10-22 |
| `character.presence` | where the orb lives: one orb where the owner put it, its card beside it, the doze | changed | was a docked pill that changed shape with the state, a peek and a floating orb at their own sizes, and a card growing out of the pill; now one orb of one size that stays where the owner put it, its state only in its dots, badge and wash, with a card beside it on the side that has room, and nothing forms around the orb or grows out of the edge ([#5216], the owner's ruling on [#3829]) ([`surfaces.md`](../surfaces.md#docking), the transitions below) | 2026-10-22 |
| `character.whole-orb` | the whole orb: a sphere of dots in the app's primary, with a still badge and a one-tone wash | distinct | it reads as the app's agent orb, at the top edge or floating | 2026-10-22 |

### The status layouts

Each surface of [the room budget](../surfaces.md#the-room-budget), and the orb's default on both
display shapes ([docking](../surfaces.md#docking), where the orb stays).

| Element | In product terms | Verdict | What changed and why | Date |
|---|---|---|---|---|
| `layout.the-orb` | the orb at 48 where the owner put it, the same size and place in every state | changed | was an orb at 18, 22, 48 or 56 by its room and its place, inside a pill when docked; now one orb of one size on no shape of its own, its state only in its dots, badge and wash ([#5216], the owner's ruling on [#3829]) | 2026-10-22 |
| `layout.the-line-card` | one line and `+N` beside the orb | changed | was the pill around the orb; now a line card beside it on the app's surface, and the other work is a count, never a group of orbs ([#5216], the owner's ruling on [#3829]) | 2026-10-22 |
| `layout.the-card` | the card beside the orb: its state, its step flow, the alert's brief and answer controls, then every other piece of live work as a row | changed | was a card the pill opened into, with the focused orb inside it at 48; now a card beside the orb on the side that has room, drawing no orb of its own, and nothing grows out of the edge or the orb ([#5216], the owner's ruling on [#3829]) | 2026-10-22 |
| `layout.tray-or-menu-bar-icon` | a still orb per state, its words in the tooltip and the menu's first line | distinct | a system tray icon drawn as the app's still orb; a count never stands alone | 2026-10-22 |
| `layout.tray-menu` | the system menu: what waits on the owner, then live work, then show, hide and open | distinct | a plain system menu of rows | 2026-10-22 |
| `layout.dock-or-app-icon` | the app icon shows the state's still orb while something needs the owner | distinct | the still orb in place of a red count, only while something waits | 2026-10-22 |
| `layout.os-notification` | the system's banner: the still orb, one line, a tap that opens the buddy | distinct | the system's own banner; no answer buttons | 2026-10-22 |
| `layout.phone-companion` | an app screen of rows, or one piece of work owning the screen | distinct | rows in the app's own screen, never tiles on the lock screen | 2026-10-22 |
| `layout.docked-notch` | at its default on a notched display | changed | was the focused orb in the left wing beside the camera and up to four orbs in the right wing; then the pill a screen without a notch has, hung just below the notch; now the one orb, at its default just below the notch and centred on it until a drag moves it, so no orbs sit on both sides of the camera and no group of orbs sits in a wing (settled 2026-10-14) ([#5216], the owner's ruling on [#3829]) | 2026-10-22 |
| `layout.docked-plain` | at its default on a screen without a notch: the orb below the menu bar at the top centre | changed | was the pill below the menu bar; now the one orb there until a drag moves it, with a small gap below the menu bar and its card beside it ([#5216], the owner's ruling on [#3829]) | 2026-10-22 |

### The four families

The families as [`character.md`](../character.md#the-poses) draws them, compared by pose, badge and
wash.

| Element | In product terms | Verdict | What changed and why | Date |
|---|---|---|---|---|
| `family.quiet` | at rest: sparse, still dots in the neutral; after done, the check | distinct | no light at rest and no breathing glow | 2026-10-22 |
| `family.active` | working or listening: the dots stir in the alive colour, the working or microphone mark | distinct | the motion is the dots' own, never a spinning arc or a row of bouncing dots | 2026-10-22 |
| `family.waiting` | needs the owner: the dots draw in tight or the orb lifts, "!" or "?", the wash in the warn or info tone | distinct | it goes still and lets the badge and the wash speak, never a pulsing colour ring | 2026-10-22 |
| `family.blocked` | an error or offline: the dots sink and settle in the neutral, "×" or the slash, the danger wash | distinct | it sinks and dims; no flashing and no red ring | 2026-10-22 |

### The transitions

Each row of [the transitions](../motion.md#transitions), compared by what the orb and its dots do
and in which order.

| Element | In product terms | Verdict | What changed and why | Date |
|---|---|---|---|---|
| `transition.peek-in` | the orb, hidden at its default, fades in where it stays | changed | was the orb sliding down from the top edge; now it appears where it is, and nothing slides it into place ([#5216], the owner's ruling on [#3829]) | 2026-10-22 |
| `transition.compact-in` | the line card fades in beside the orb | changed | was the pill fading in below the edge around the orb; now the line card fades in beside it, and the orb does not move or change size ([#5216], the owner's ruling on [#3829]) | 2026-10-22 |
| `transition.expand` | the card opens beside the orb, on the side that has room | changed | was the card taking the pill's place with the focused orb scaling to 48; now the card opens from the orb's edge, on `smooth` with no overshoot, and the orb does not move or change size ([#5216], the owner's ruling on [#3829]) | 2026-10-22 |
| `transition.collapse` | the card folds back toward the orb, and the line card returns while work runs | distinct | the effects curve, no overshoot, and the orb stays as it is | 2026-10-22 |
| `transition.phase-change` | one pose exits and the next enters; the badge swaps with a small pop | distinct | the app's own poses and marks | 2026-10-22 |
| `transition.working-hold` | the dots stir in the phase's state; the step's label fills with the alive colour | distinct | the agent orb's own motion, never a spinning arc | 2026-10-22 |
| `transition.alert-arrives` | the orb holds or lifts and goes still; the badge pops; the wash rises once | distinct | stillness, a mark and one tone, never a pulse | 2026-10-22 |
| `transition.answer-yes` | a nod: the orb dips once, the check | distinct | a shared mechanism: a nod for yes | 2026-10-22 |
| `transition.answer-no` | a small side shake after an explicit Deny or Dismiss | distinct | a shared mechanism: a shake for no, sized by the expressiveness dial; it rejects nothing the owner typed | 2026-10-22 |
| `transition.error` | the dots sink toward the lower edge and settle in the neutral; the danger wash rises | distinct | the orb settles, never a flash | 2026-10-22 |
| `transition.done` | the check, one swirl round the orb, then rest | distinct | one swirl that ends, never a continuous churn | 2026-10-22 |
| `transition.listening` | the orb swells a little toward the owner, its dots listen, the microphone badge pops | distinct | the orb swells a little and its dots move, with no glow and no wave of light | 2026-10-22 |
| `transition.dock-and-undock` | only a drag moves the orb, and it stays where it is dropped | changed | was the orb moving into or out of a pill that faded in or out around it; now nothing forms around the orb, and only the owner's drag moves it ([#5216], the owner's ruling on [#3829]) | 2026-10-22 |
| `transition.doze-and-wake` | the dots slow to a stop and the orb dims; on wake it brightens | distinct | the orb dims and brightens, with no breathing light | 2026-10-22 |
| `transition.badge-unfolds` | the badge widens into the state's words and folds back | distinct | the app's own badge and words | 2026-10-22 |

### The notification treatment

| Element | In product terms | Verdict | What changed and why | Date |
|---|---|---|---|---|
| `notification.icon` | the alert kind's still orb | distinct | the app's still orb, beside words that say the same thing | 2026-10-22 |
| `notification.line` | the one line of what changed | distinct | one exact state change in words | 2026-10-22 |
| `notification.tap` | a tap opens the buddy on that alert, or the app with Buddy status off | distinct | no answer buttons, because an answer needs its brief | 2026-10-22 |

## Later work

Each row is compared and resolved with the task it names, which adds its own dated sign-off before
its feature ships.

### Play and drop

[The play and drop transitions](../motion.md#play-and-drop-transitions).

| Element | In product terms | Verdict | What changed and why | Date |
|---|---|---|---|---|
| `play.greeting` | a pop to full size, one ripple out through the dots, a blink | pending | with play, [#3894] | 2026-10-22 |
| `play.follow` | the orb's lean turns toward the pointer and settles | pending | with play, [#3894] | 2026-10-22 |
| `play.poked` | the dots scatter a little and regather, a blink | pending | with play, [#3894] | 2026-10-22 |
| `play.annoyed` | a shiver through the dots, the orb grows a little | pending | with play, [#3894]; the hit chain now ends here (settled 2026-10-14) | 2026-10-22 |
| `play.dizzy` | the dots wobble out of step, then settle | pending | with play, [#3894]: settled 2026-10-14 that there is no dizzy, and #3894 removes the row | 2026-10-22 |
| `play.drag` | the orb dangles behind the pointer and settles on release | pending | with play, [#3894] | 2026-10-22 |
| `drop.file-over` | the dots spread wide, ready to catch | pending | with drop, [#3895] | 2026-10-22 |
| `drop.swallow` | the orb takes a dropped file in | pending | with drop, [#3895]: settled 2026-10-14 that it becomes the catch (the pose "holding"), the spread dots closing round the file and holding it up | 2026-10-22 |

### Sound

[The event map](../sound.md#event-map).

| Element | In product terms | Verdict | What changed and why | Date |
|---|---|---|---|---|
| `sound.an-approval-arrives` | a soft rising two-note call | pending | with sound, [#3900] | 2026-10-22 |
| `sound.a-question-arrives` | the call with a lifted last note | pending | with sound, [#3900] | 2026-10-22 |
| `sound.work-fails` | a low settling note | pending | with sound, [#3900] | 2026-10-22 |
| `sound.work-is-done` | a warm single chime | pending | with sound, [#3900] | 2026-10-22 |
| `sound.an-answer-is-sent-from-the` | a short tick | pending | with sound, [#3900] | 2026-10-22 |
| `sound.listening-starts-and-ends` | a small open and a small close | pending | with sound, [#3900] | 2026-10-22 |
| `sound.a-dropped-file-is-swallowed` | the drop's sound | pending | with sound, [#3900]: settled 2026-10-14 that it is a soft closing note on the catch | 2026-10-22 |
| `sound.poked-annoyed-dizzy` | short voice-like blips, rising in fuss | pending | with sound, [#3900]: for poked and annoyed only, with the hit chain ending at annoyed | 2026-10-22 |
| `sound.phase-changes-work-starting-in-sight` | silence for ambient updates | pending | with sound, [#3900] | 2026-10-22 |

### The export and the bridges

| Element | In product terms | Verdict | What changed and why | Date |
|---|---|---|---|---|
| `export.sprite-sheet` | the poses exported as a portable sprite sheet | pending | with the export, [#3903] | 2026-10-22 |
| `bridge.companions` | live work shown in the desktop companions an owner already runs | pending | with the bridges, [PersonalClaw/PersonalClawApps#164] | 2026-10-22 |

## Sign-off

Signed off by the maintainer on 2026-10-22, for the v0.3.0 rows. Each later row is signed with its
task. The presence, layout and transition rows changed on 2026-10-22 record the maintainer's ruling
on [#3829] after playing the playground: one orb of one size that stays where the owner put it,
with its card beside it ([#5216]).

[#3807]: https://github.com/PersonalClaw/PersonalClaw/issues/3807
[#3829]: https://github.com/PersonalClaw/PersonalClaw/issues/3829
[#3830]: https://github.com/PersonalClaw/PersonalClaw/issues/3830
[#3892]: https://github.com/PersonalClaw/PersonalClaw/issues/3892
[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#3900]: https://github.com/PersonalClaw/PersonalClaw/issues/3900
[#3903]: https://github.com/PersonalClaw/PersonalClaw/issues/3903
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
[#5216]: https://github.com/PersonalClaw/PersonalClaw/issues/5216
[PersonalClaw/PersonalClawApps#164]: https://github.com/PersonalClaw/PersonalClawApps/issues/164

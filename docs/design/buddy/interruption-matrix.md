# The interruption matrix

Every situation the buddy can be in, crossed with every event that can reach it: 14 situations by
20 events, 280 cells. 224 of them are outcomes and 56 are "cannot occur here", which means the
event has no source in that situation and the state machine asserts it is never delivered there.
[`interruption-matrix.json`](interruption-matrix.json) is the same matrix as data, and the state
machine's tests read it. `tests/test_buddy_design_matrix.py` holds this table and the data to each
other cell for cell, so a cell changes in both or the test fails.

## Reading it

- The data's `situations` and `events` give each id its label, `cells` gives every outcome, and
  `later` lists the cells that wait for later work.
- v0.3.0 ships the truth and presence layers with no play and no drop, so its state machine is
  tested on every cell outside `later` ([#3890]). The later cells are every cell of the play and
  file situations, the file event in every other situation, and every other cell whose whole
  outcome is a play reaction. Their tests come with their work ([#3894], [#3895]).
- In v0.3.0 a later cell's event has no outcome of its own. A file dragged over the buddy changes
  nothing, a click on the orb while its card is open changes nothing, and with no reaction to count
  them, repeated clicks are plain clicks that do what their situation's click cell says.
- Three shipping cells gain a play clause when play lands. The data keeps those clauses in
  `later_clauses`, and the table shows them under
  [Play clauses in the shipping cells](#play-clauses-in-the-shipping-cells).
- A cell names a timing by its token, such as hoverExpand or offlineGrace;
  [`state-machine.md`](state-machine.md#timing-names) says what each one times.
- `muted` is the id of "hidden by the owner", not of notification mute.

## The shipping situations

Every cell below ships in v0.3.0.

### Hidden: nothing to show, or the owner is away (docked)

| Event | Outcome |
|---|---|
| Work starts | compact, unless the owner is away (then stays hidden; the start joins the return summary) |
| The focused work changes phase | stays hidden; the state is kept, nothing is drawn |
| Other work changes phase | stays hidden; nothing is drawn |
| An alert arrives | expanded on the alert, through absence (in quiet hours: a quiet peek, no sound) |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | stays hidden; it joins the return summary |
| Work fails | error alert, as an alert arriving |
| The connection is lost | stays hidden; offline is shown the next time it appears |
| The connection is back | stays hidden; everything is re-read |
| Listening starts | compact on the listening orb |
| Listening ends | cannot occur here |
| The pointer rests on it for hoverExpand | peek |
| A click on the orb | cannot occur here |
| Quick repeated hits on the orb | cannot occur here |
| The owner goes idle for absence | no change |
| The owner returns | compact with the return summary when anything happened while away, else stays hidden |
| No interaction for collapse | cannot occur here |
| A drag starts on the orb | cannot occur here |

### Peek: the pointer rests on the top-edge zone

| Event | Outcome |
|---|---|
| Work starts | compact |
| The focused work changes phase | peek, the orb shows the new phase |
| Other work changes phase | no change |
| An alert arrives | expanded on the alert |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | finished |
| Work fails | error alert |
| The connection is lost | peek on the offline orb after offlineGrace |
| The connection is back | peek on the true pose |
| Listening starts | listening |
| Listening ends | cannot occur here |
| The pointer rests on it for hoverExpand | expanded |
| A click on the orb | expanded |
| Quick repeated hits on the orb | expanded (hits count as play only once it is open) |
| The owner goes idle for absence | hidden |
| The owner returns | cannot occur here |
| No interaction for collapse | hidden after peekLeave once the pointer has left the zone |
| A drag starts on the orb | dragging (undocks) |

### Compact: work is running and the owner is active

| Event | Outcome |
|---|---|
| Work starts | compact; the in-sight count grows, focus stays |
| The focused work changes phase | compact; orb and line change with one phase beat |
| Other work changes phase | compact; only its in-sight entry changes, no motion |
| An alert arrives | expanded on the alert; the orb plays the alert's entrance |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | finished; focus moves to the next live work after the hold |
| Work fails | error alert |
| The connection is lost | compact on the offline orb and line after offlineGrace |
| The connection is back | everything is re-read; the true pose returns |
| Listening starts | listening |
| Listening ends | cannot occur here |
| The pointer rests on it for hoverExpand | expanded |
| A click on the orb | expanded |
| Quick repeated hits on the orb | expanded |
| The owner goes idle for absence | hidden (respect absence) |
| The owner returns | cannot occur here |
| No interaction for collapse | no change (compact folds away only when nothing is live) |
| A drag starts on the orb | dragging (undocks) |

### Expanded: the card is open, no alert

| Event | Outcome |
|---|---|
| Work starts | expanded; a new orb joins the in-sight row |
| The focused work changes phase | the step track moves on: the new step replaces the last |
| Other work changes phase | only its in-sight line changes |
| An alert arrives | alert; the run moves into the in-sight row |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | finished inside the card; focus moves after the hold |
| Work fails | error alert |
| The connection is lost | the card shows offline and greys the last known step after offlineGrace |
| The connection is back | everything is re-read; the card updates in place |
| Listening starts | listening; the card keeps its content under the listening orb |
| Listening ends | cannot occur here |
| The pointer rests on it for hoverExpand | no change |
| The owner goes idle for absence | hidden |
| The owner returns | cannot occur here |
| No interaction for collapse | compact, after the countdown line has run for collapseCountdown |
| A drag starts on the orb | dragging (undocks with the card closed) |

### Alert shown: an approval, a question or an error

| Event | Outcome |
|---|---|
| Work starts | the alert stays; the in-sight count grows |
| The focused work changes phase | the alert stays (its work is waiting on the owner) |
| Other work changes phase | the alert stays; only the in-sight line changes |
| An alert arrives | queued behind it, in the waiting list's order (consequence, then age); the waiting count pops once |
| The shown alert is answered here | the answer beat (nod for yes, a small shake for no), then the next alert after alertGap, else what was under it |
| The shown alert is answered elsewhere, expires or is withdrawn | it leaves with an 'answered elsewhere' line for one beat (or `<work>: the request expired` when it expired), then the next alert or what was under it |
| The focused work finishes | the alert stays; done work joins the in-sight row only |
| Work fails | queued as an error alert |
| The connection is lost | the alert stays; its actions disable with a line saying nothing can be sent |
| The connection is back | re-read: still pending, actions come back; gone, as answered elsewhere |
| Listening starts | listening holds the alert (never answers it); it returns when listening ends |
| Listening ends | cannot occur here |
| The pointer rests on it for hoverExpand | no change |
| A click on the orb | no play during an alert; only its controls act |
| Quick repeated hits on the orb | nothing: no play during an alert |
| The owner goes idle for absence | the alert stays (alerts are sticky) |
| The owner returns | the alert is still there |
| No interaction for collapse | never: alerts stay until answered |
| A drag starts on the orb | dragging, carrying the alert (a docked alert undocks as a floating one) |

### Finished: the done beat and its hold

| Event | Outcome |
|---|---|
| Work starts | the beat completes, then the new work shows |
| The focused work changes phase | cannot occur here |
| Other work changes phase | no change |
| An alert arrives | the hold ends within one beat; alert |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | the hold extends; the line counts the finished work |
| Work fails | the hold ends within one beat; error alert |
| The connection is lost | after the beat, the offline orb |
| The connection is back | no change |
| Listening starts | the hold ends within one beat; listening |
| Listening ends | cannot occur here |
| The pointer rests on it for hoverExpand | expanded on the finished work, with Open |
| A click on the orb | expanded on the finished work, with Open |
| Quick repeated hits on the orb | expanded on the finished work |
| The owner goes idle for absence | hidden; the work stays in the return summary |
| The owner returns | cannot occur here |
| No interaction for collapse | after finishedHold: compact when other work is live, else hidden |
| A drag starts on the orb | dragging |

### Floating: the undocked orb, awake

| Event | Outcome |
|---|---|
| Work starts | the orb wakes into the working look (no bubble) |
| The focused work changes phase | the orb shows the new phase with one beat |
| Other work changes phase | no change |
| An alert arrives | a one-line bubble with the alert badge; a click opens the alert card beside it |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | the done beat on the orb, no bubble |
| Work fails | error alert in the bubble |
| The connection is lost | the offline orb after offlineGrace |
| The connection is back | the true pose |
| Listening starts | listening |
| Listening ends | cannot occur here |
| The pointer rests on it for hoverExpand | a hover card with the line and the in-sight count |
| A click on the orb | the card opens beside the orb |
| The owner goes idle for absence | sleeping (dozing: still and dimmed), never hidden |
| The owner returns | cannot occur here |
| No interaction for collapse | the open card closes; the orb stays |
| A drag starts on the orb | dragging |

### Sleeping or dozing: floating with nothing live, or the owner away

| Event | Outcome |
|---|---|
| Work starts | the wake beat, then the working orb |
| The focused work changes phase | cannot occur here |
| Other work changes phase | no change |
| An alert arrives | wakes within one beat into the alert bubble |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | stays asleep; the work joins the return summary |
| Work fails | wakes within one beat into the error alert |
| The connection is lost | stays asleep with a small offline mark |
| The connection is back | no change |
| Listening starts | wakes within one beat into listening |
| Listening ends | cannot occur here |
| The pointer rests on it for hoverExpand | brightens a little; the line shows on hover |
| A click on the orb | wakes and opens the card |
| The owner goes idle for absence | no change |
| The owner returns | wakes with the return summary in the bubble when anything happened |
| No interaction for collapse | no change |
| A drag starts on the orb | wakes into dragging |

### Listening: push-to-talk capture is live

| Event | Outcome |
|---|---|
| Work starts | listening stays; the in-sight count grows |
| The focused work changes phase | listening stays |
| Other work changes phase | no change |
| An alert arrives | queued until listening ends (the waiting count shows) |
| The shown alert is answered here | cannot: alert actions are disabled while listening |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | listening stays; the work joins the in-sight row |
| Work fails | queued as an error alert |
| The connection is lost | listening stays, with the offline mark (transcription will wait) |
| The connection is back | no change |
| Listening starts | cannot occur here |
| Listening ends | within one beat, the queued alert if any, else what was under listening |
| The pointer rests on it for hoverExpand | no change |
| A click on the orb | only Stop acts (it stops the capture); no play |
| Quick repeated hits on the orb | nothing |
| The owner goes idle for absence | cannot occur here |
| The owner returns | cannot occur here |
| No interaction for collapse | no change |
| A drag starts on the orb | dragging, still on the listening orb |

### Dragging: the owner is moving the orb

| Event | Outcome |
|---|---|
| Work starts | the orb changes on the next frame |
| The focused work changes phase | the orb changes; no beat until the release |
| Other work changes phase | no change |
| An alert arrives | the badge shows at once; the bubble opens on release |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | no beat during the drag |
| Work fails | the badge shows at once; the bubble opens on release |
| The connection is lost | the orb changes |
| The connection is back | the orb changes |
| Listening starts | the listening orb, still dragged |
| Listening ends | back to the true pose, still dragged |
| The pointer rests on it for hoverExpand | cannot occur here |
| A click on the orb | cannot occur here |
| Quick repeated hits on the orb | cannot occur here |
| The owner goes idle for absence | cannot occur here |
| The owner returns | cannot occur here |
| No interaction for collapse | cannot occur here |
| A drag starts on the orb | cannot occur here |

### Hidden by the owner: for an hour, or until tomorrow

| Event | Outcome |
|---|---|
| Work starts | nothing shows; the tray and dock still tell |
| The focused work changes phase | nothing shows |
| Other work changes phase | nothing shows |
| An alert arrives | nothing shows here; the tray, the dock and notifications still tell |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | nothing shows |
| Work fails | nothing shows here; the other surfaces still tell |
| The connection is lost | nothing shows |
| The connection is back | nothing shows |
| Listening starts | the listening orb shows: the one thing hiding never hides |
| Listening ends | hidden again |
| The pointer rests on it for hoverExpand | nothing (no peek while hidden by the owner) |
| A click on the orb | cannot occur here |
| Quick repeated hits on the orb | cannot occur here |
| The owner goes idle for absence | no change |
| The owner returns | no change |
| No interaction for collapse | no change |
| A drag starts on the orb | cannot occur here |

### Offline: PersonalClaw cannot be reached

| Event | Outcome |
|---|---|
| Work starts | cannot occur here |
| The focused work changes phase | cannot occur here |
| Other work changes phase | cannot occur here |
| An alert arrives | cannot occur here |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | cannot occur here |
| The focused work finishes | cannot occur here |
| Work fails | cannot occur here |
| The connection is lost | cannot occur here |
| The connection is back | everything is re-read; the line says `Connected again`, the true state returns, and alerts that came in show |
| Listening starts | listening, with the offline mark |
| Listening ends | back to offline |
| The pointer rests on it for hoverExpand | expanded on the offline card: since when, and Open PersonalClaw |
| A click on the orb | expanded on the offline card |
| The owner goes idle for absence | hidden, or dozing when floating |
| The owner returns | the offline orb |
| No interaction for collapse | compact, then hidden |
| A drag starts on the orb | dragging |

## Later work

These cells wait for play and the drop. None of them blocks v0.3.0.

### Play reaction: poked, annoyed, dizzy, greeting or a glance

Later work: play ([#3894]). The machine enters this situation only once play lands. Settled for
its return, the hit chain ends at annoyed ([#4570]), so the click and hits cells here change with
[#3894].

| Event | Outcome |
|---|---|
| Work starts | the reaction ends within one beat; the true state shows |
| The focused work changes phase | the reaction ends within one beat; the new phase shows |
| Other work changes phase | the reaction carries on |
| An alert arrives | the reaction ends within one beat; alert |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | the reaction ends within one beat; finished |
| Work fails | the reaction ends within one beat; error alert |
| The connection is lost | the reaction ends within one beat; offline |
| The connection is back | the reaction ends within one beat; the true pose |
| Listening starts | the reaction ends within one beat; listening |
| Listening ends | cannot occur here |
| The pointer rests on it for hoverExpand | the reaction carries on |
| A click on the orb | the next hit while the reaction still plays: poked, then annoyed, then dizzy |
| Quick repeated hits on the orb | escalates to dizzy; after dizzy, hits only blink for playCooldown |
| A file is dragged over it | the reaction ends; file |
| The owner goes idle for absence | the reaction ends; hidden or dozing |
| The owner returns | cannot occur here |
| No interaction for collapse | the reaction finishes, then the collapse happens |
| A drag starts on the orb | the reaction ends; dragging |

### File held over it, or the drop choice is open

Later work: drop and quick ask ([#3895]). Settled for its return, the drop is a catch, never a
swallow ([#4570]), so the drop's words here change with [#3895].

| Event | Outcome |
|---|---|
| Work starts | no change: the drop or the choice stays |
| The focused work changes phase | no change |
| Other work changes phase | no change |
| An alert arrives | a held file is refused; an open choice yields to the alert and comes back after it |
| The shown alert is answered here | cannot occur here |
| The shown alert is answered elsewhere, expires or is withdrawn | no change (the queue drops it) |
| The focused work finishes | no beat while the owner is choosing |
| Work fails | as an alert arriving |
| The connection is lost | the drop is refused, or the choice disables, with a line saying why |
| The connection is back | the choice comes back |
| Listening starts | the choice is held; listening; then the choice returns |
| Listening ends | the choice returns |
| The pointer rests on it for hoverExpand | no change |
| A click on the orb | a click on a choice does that choice (Ask about it, Add to Knowledge, Put it aside) |
| Quick repeated hits on the orb | nothing |
| A file is dragged over it | drop: the swallow beat, then the choice; leave: the drop target closes, back to what was under it |
| The owner goes idle for absence | the choice stays until collapse |
| The owner returns | no change |
| No interaction for collapse | the choice closes and the file is put aside: nothing was done with it |
| A drag starts on the orb | cannot occur here |

### The drop in the shipping situations

Later work: drop and quick ask ([#3895]). The file event in every other situation.

| Situation | Event | Outcome |
|---|---|---|
| Hidden | A file is dragged over it | peek with the drop target open, then file |
| Peek | A file is dragged over it | file |
| Compact | A file is dragged over it | file |
| Expanded | A file is dragged over it | file |
| Alert shown | A file is dragged over it | the drop is refused while an alert shows |
| Finished | A file is dragged over it | the hold ends; file |
| Floating | A file is dragged over it | file |
| Sleeping or dozing | A file is dragged over it | wakes into file |
| Listening | A file is dragged over it | the drop is refused while listening |
| Dragging | A file is dragged over it | cannot occur here |
| Hidden by the owner | A file is dragged over it | nothing (no drop target while hidden by the owner) |
| Offline | A file is dragged over it | the drop is refused: nothing can be asked while offline |

### Play in the shipping situations

Later work: play ([#3894]). The cells of the other situations whose whole outcome is a play
reaction.

| Situation | Event | Outcome |
|---|---|---|
| Expanded | A click on the orb | play: hit one (poked); a click on a control does that control |
| Expanded | Quick repeated hits on the orb | play: annoyed, then dizzy |
| Floating | Quick repeated hits on the orb | play: poked, annoyed, then dizzy |
| Sleeping or dozing | Quick repeated hits on the orb | play: a startled wake, then floating |
| Offline | Quick repeated hits on the orb | play is allowed (offline is not an alert) |

### Play clauses in the shipping cells

Later work: play ([#3894]). These cells ship without the clause and gain it when play lands.

| Situation | Event | Adds |
|---|---|---|
| Hidden | The pointer rests on it for hoverExpand | the first peek of a session plays the greeting |
| Dragging | Work starts | the dangle carries on |
| Dragging | The connection is lost | the dangle carries on |

[#3890]: https://github.com/PersonalClaw/PersonalClaw/issues/3890
[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570

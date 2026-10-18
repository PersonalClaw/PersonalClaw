# Buddy copy

The face does the attention work; one line underneath says what changed. Every state has exactly
one line, and every surface has a length it never exceeds. The line names what changed or what is
happening without leaning on the pose, the colour or the animation ([#3899]).

## Voice

- Plain present tense, naming what changed and for which work: "Release notes: running the tests".
- An alert's line names what changed and what the owner can do ([#3832]): the approval line asks
  for an OK, and the question line for an answer. The error line below names only what stopped and
  why; the words for what the owner can do about it, which its card offers as Open and Dismiss, are
  [#3899]'s to add.
- The work's own name first, then what it is doing. The buddy never speaks as "I" and never
  apologises.
- No exclamation marks, no emoji, no jokes in the line. Personality belongs to the face.
- Numbers as digits. Times as the app shows them.
- Approval, question and error text is the app's own: the buddy reuses the approval prompt's brief
  and the existing decline wording word for word, because security copy is never reworded. Its
  approval controls are the app's Allow and Deny, with the app's accessible names.

## One line per state

`<work>` is the work's name (a chat's title, a loop's goal, a run's workflow name), cut to fit with
an ellipsis.

| State | Line |
|---|---|
| queued | `<work>: waiting to start` |
| thinking (no output yet) | `<work>: thinking` |
| searching | `<work>: searching <where>` (your knowledge, the web, the files) |
| tool | `<work>: <the tool's own label>` |
| planning | `<work>: planning` |
| delegating | `<work>: handing off to <n> helpers` |
| streaming | `<work>: writing` |
| listening | `Listening. Press <chord> to stop` |
| approval | `<work> needs your OK: <the brief's first line>` |
| question | `<work> asks: <the question>` |
| error | `<work> stopped: <the reason>` |
| done | `<work> is done in <time it took>` |
| several done | `<n> finished` |
| return summary | `While you were away: <n> done, <m> need you` |
| idle (opened by hand) | `Nothing running` |
| restarting | `PersonalClaw is restarting` |
| offline | `Can't reach PersonalClaw since <time>` |
| recovery (connected again after offline) | `Connected again` |
| answered elsewhere | `Answered in PersonalClaw` |
| approval expired before an answer | `<work>: the request expired` |
| quiet hours, alert | the alert's line, shown only when expanded |

## Maximum length per surface

| Surface | Characters |
|---|---|
| Top-edge compact | 40 |
| Floating bubble | 48 |
| Expanded card line | 72 (the brief below it has its own layout) |
| In-sight row | 48 |
| Tray menu row | 56 |
| Tray tooltip | 80 |
| OS notification body | 120 |
| Phone, all-work view | 48 |

A line is cut at a word boundary with an ellipsis; the work's name is cut first, never the state.

## Tests

Every state has a line on every surface that shows lines; no rendered line exceeds its surface's
limit for names up to 200 characters; approval and decline text matches the app's prompt exactly.
The checks also cover longer translated words, what a screen reader announces (the same text as
the visible line), lines cut to the compact limits, and the offline, expired-approval and recovery
lines ([#3899]).

[#3832]: https://github.com/PersonalClaw/PersonalClaw/issues/3832
[#3899]: https://github.com/PersonalClaw/PersonalClaw/issues/3899

# Pre-channel arrival-funnel baseline (DL-13)

**This is a dated, one-time snapshot, not a live figure.** GitHub's traffic API
(`traffic/views`, `traffic/clones`, `traffic/popular/referrers`,
`traffic/popular/paths`) serves a **rolling 14-day window only**. Every count
below is scoped to the window GitHub was serving at the exact moment each `gh`
command ran; a re-pull on any later date returns a different window and
different totals, and the pre-channel window this snapshot covers becomes
permanently unreconstructable once it rolls off (on or before 2026-10-02).
Do not read any number below as current at the time you are reading this file
— re-run the cited command for that.

- Repository: `PersonalClaw/PersonalClaw`
- Captured by: `dev-junior.1` (OpenOrganization instance `personalclaw`), DL-13
- Auth: `gh` CLI, account `keyurgolani`, token scopes `gist, read:org, repo, workflow`
- All timestamps below are UTC, taken immediately before/after each call

## 1. Referrers — `gh api repos/PersonalClaw/PersonalClaw/traffic/popular/referrers`

Captured 2026-09-19T03:22:44Z – 2026-09-19T03:22:45Z

3 rows returned (per-row, not aggregated):

| referrer | count | uniques |
|---|---|---|
| github.com | 15 | 8 |
| Google | 5 | 3 |
| personalclaw.dev | 1 | 1 |

## 2. Popular paths — `gh api repos/PersonalClaw/PersonalClaw/traffic/popular/paths`

Captured 2026-09-19T03:22:45Z – 2026-09-19T03:22:46Z

10 rows returned (per-row, not aggregated):

| path | title | count | uniques |
|---|---|---|---|
| /PersonalClaw/PersonalClaw/pulls | /pulls | 111 | 2 |
| /PersonalClaw/PersonalClaw | Overview | 56 | 21 |
| /PersonalClaw/PersonalClaw/commits/main | /commits/main | 46 | 2 |
| /PersonalClaw/PersonalClaw/pulse | /pulse | 11 | 1 |
| /PersonalClaw/PersonalClaw/blob/main/SHOWCASE.md | /blob/main/SHOWCASE.md | 3 | 3 |
| /PersonalClaw/PersonalClaw/issues | /issues | 3 | 3 |
| /PersonalClaw/PersonalClaw/issues/2490 | /issues/2490 | 3 | 3 |
| /PersonalClaw/PersonalClaw/tree/main/docs | /tree/main/docs | 3 | 1 |
| /PersonalClaw/PersonalClaw/blob/main/docs/screenshots/dark/01-dashboard.png | /blob/main/docs/screenshots/dark/01-dashboard.png | 2 | 2 |
| /PersonalClaw/PersonalClaw/blob/main/docs/screenshots/dark/03-knowledge.png | /blob/main/docs/screenshots/dark/03-knowledge.png | 2 | 2 |

## 3. Views — `gh api repos/PersonalClaw/PersonalClaw/traffic/views`

Captured 2026-09-19T03:22:50Z – 2026-09-19T03:22:51Z

Window total: **count 310, uniques 34** (this is the API's own rolling-window
total field, reproduced verbatim — not a total we computed).

Per-day rows (14 days, 2026-09-04 through 2026-09-17):

| date (UTC) | count | uniques |
|---|---|---|
| 2026-09-04 | 45 | 10 |
| 2026-09-05 | 32 | 4 |
| 2026-09-06 | 15 | 4 |
| 2026-09-07 | 42 | 9 |
| 2026-09-08 | 33 | 6 |
| 2026-09-09 | 44 | 5 |
| 2026-09-10 | 6 | 5 |
| 2026-09-11 | 1 | 1 |
| 2026-09-12 | 4 | 2 |
| 2026-09-13 | 1 | 1 |
| 2026-09-14 | 3 | 3 |
| 2026-09-15 | 5 | 2 |
| 2026-09-16 | 30 | 5 |
| 2026-09-17 | 49 | 1 |

## 4. Clones — `gh api repos/PersonalClaw/PersonalClaw/traffic/clones`

Captured 2026-09-19T03:22:51Z – 2026-09-19T03:22:52Z

Window total: **count 20418, uniques 1043** (API's own rolling-window total
field, reproduced verbatim).

Per-day rows (14 days, 2026-09-04 through 2026-09-17):

| date (UTC) | count | uniques |
|---|---|---|
| 2026-09-04 | 3114 | 186 |
| 2026-09-05 | 2721 | 198 |
| 2026-09-06 | 2020 | 189 |
| 2026-09-07 | 3225 | 273 |
| 2026-09-08 | 1875 | 146 |
| 2026-09-09 | 2130 | 200 |
| 2026-09-10 | 137 | 96 |
| 2026-09-11 | 136 | 91 |
| 2026-09-12 | 66 | 45 |
| 2026-09-13 | 57 | 29 |
| 2026-09-14 | 71 | 19 |
| 2026-09-15 | 962 | 93 |
| 2026-09-16 | 1445 | 113 |
| 2026-09-17 | 2459 | 187 |

Note (observation, not interpretation for others to redo): the 2026-09-04
through 2026-09-09 clone counts (1875–3225/day against 146–273 uniques/day)
are far higher than the view/star/referrer traffic for the same days. This
reads as automated/CI/mirror cloning rather than organic developer traffic,
but the atom's `done_when` bars recording anything but the raw figures, so no
adjustment or exclusion has been applied above — this is exactly what the API
returned.

## 5. Stargazers (starred_at) — `gh api repos/PersonalClaw/PersonalClaw/stargazers -H "Accept: application/vnd.github.star+json"`

Captured 2026-09-19T03:22:59Z

5 entries returned, each `starred_at` individually (not aggregated):

| starred_at (UTC) | user |
|---|---|
| 2026-07-28T16:01:12Z | baokhang83 |
| 2026-08-13T16:20:16Z | adisakshya |
| 2026-08-20T00:04:46Z | Alchemica369 |
| 2026-09-04T03:42:42Z | ahoken50 |
| 2026-09-18T10:49:54Z | tharunramagiri |

## 6. Repo-level counts — `gh api repos/PersonalClaw/PersonalClaw --jq '{forks_count: .forks_count, subscribers_count: .subscribers_count, watchers_count: .watchers_count, stargazers_count: .stargazers_count, created_at: .created_at}'`

Captured 2026-09-19T03:23:01Z

| field | value |
|---|---|
| `forks_count` | 7 |
| `stargazers_count` | 5 |
| `watchers_count` | 5 |
| `subscribers_count` | 1 |
| `created_at` | 2026-07-19T16:15:56Z |

Note on a GitHub API quirk, so a later reader doesn't misread this table:
`watchers_count` mirrors `stargazers_count` in the current API and is **not**
the count of people who clicked "Watch" for notifications — `subscribers_count`
is that figure. Both raw fields are recorded above rather than picking one.

## Delta observed within this same shift

The atom's own `done_when` clause (re-authored 2026-09-18, same UTC day as
this capture minus ~7 hours) records the PM re-pulling referrers and views
mid-shift and finding both had already moved from an earlier same-day pull:
referrers went from 2 rows (github.com 19/10, Google 7/3) to 3 rows
(github.com 15/8, Google 5/3, personalclaw.dev 1/1), and views moved from
323/41 to 310/34. This capture (2026-09-19, run separately) reproduces that
same 3-row referrer shape and the identical 310/34 views total, and adds one
new stargazer (`tharunramagiri`, 2026-09-18T10:49:54Z) that was not in the
PM's 4-entry list. This is the exact property the atom exists to defend
against: within a single day the window already moved twice, so only a
dated, per-row, command-attributed snapshot — not a remembered or summarized
figure — is honestly reconstructable after the window rolls off.

## Outstanding direction question (not resolved by this capture)

DL-13's own scope text records an unresolved direction question, already
escalated to the Chairman on 2026-09-18: ROADMAP-100 ruling #31 closed the
public-launch track, so there is currently no scheduled post-channel
comparison for this pre-channel baseline to serve, and the atom explicitly
says not to decide "capture now, or close the atom instead" without the
Chairman's answer. This snapshot performs the "capture now" action only — it
was cleared as a read-only `gh` read that publishes nothing (CEO/CTO #34) —
and does not itself decide, or attempt to decide, whether the broader DL-13
line of work continues.

# Reply styles: a recorded real-model run

The real-model half of #3828's Done-when: the same question, asked in three new chats under Default, Terse and Conversational, answered by a real model. The scripted-model tests prove each style's instruction reaches the model ([`tests/test_reply_style_reaches_the_model.py`](../../tests/test_reply_style_reaches_the_model.py)); they cannot show the reply change, because the scripted model ignores the prompt. This page is that half.

## How it was run

- **Date:** 2026-10-21.
- **Build:** personalclaw 0.2.4 from PyPI in a new virtual environment, on a scratch home seeded with `personalclaw gateway --seed demo-home --seed-replace`, with the gateway on a loopback port. The bundled style texts (`src/personalclaw/config/prompt_snippets/reply-style-terse.md` and `reply-style-conversational.md`) and `src/personalclaw/reply_style.py` are the same at v0.2.4 and on `main` when this was written.
- **Model:** `us.anthropic.claude-opus-5-5` through the bedrock-models app 0.1.21 (chat bound to it, with `us.anthropic.claude-sonnet-5-5` as its fallback; every turn below ran on the first). No tool was called.
- **Chats:** each one new, created with `POST /api/chat/sessions`, its style set with `PATCH /api/chat/sessions/{session}/reply-style` (`""`, `"terse"`, `"conversational"`), and the preview read back from `GET /api/chat/sessions/{session}` before the turn. The agent and the platform name no style, so the conversation's choice is the one that applies.
- **Prompt,** the same for every chat, sent once with `POST /api/chat`:

  > A script fetches three RSS feeds one after another every Friday and builds a digest from them. Explain in plain words why one slow feed can make the whole Friday run fail.

- **Word count:** whitespace-separated words of the reply's text as stored, markdown included.
- **Rounds:** two. The first round is the three replies below. A second round in three more new chats checks the first round's Terse result, which was not shorter than Default.

One note for whoever repeats this: a fresh `personalclaw==0.2.4` install resolves python-dateutil 3.0.0, and the bedrock-models app's boto3 needs python-dateutil below 3, so the gateway refused the app until the scratch environment was given `python-dateutil<3`. Nothing about the replies depends on it.

## Results

| Style | Instruction the turn carried | Words, round 1 | Words, round 2 |
|---|---|---:|---:|
| Default | none (`applies: none`, source `platform`) | 250 | 282 |
| Terse | `reply-style-terse` (`applies: style`, source `conversation`) | 274 | 255 |
| Conversational | `reply-style-conversational` (`applies: style`, source `conversation`) | 375 | 494 |

- **Conversational changes the reply, both times.** Its replies are half again to nearly twice as long as Default's (375 and 494 words against 250 and 282). They are written as short sections with bold lead-ins, give the background first (round 2 opens by walking through how a sequential run spends its time), and work a concrete example with numbers (a feed that normally takes 2 seconds now taking 4 minutes).
- **Terse does not make this reply shorter.** Its replies (274 and 255 words) sit inside Default's own range (250 and 282). What it changes is the shape: lists throughout, and neither Terse reply ends with an offer to continue, where Default's second reply does ("If you share the script, I can tell you which of these is actually happening"). On a question this size the default reply is already direct and list-shaped, so Terse has little left to cut. Its "Terse is shorter wording, not less content" rule also keeps every cause in.
- **The facts agree across styles.** Every reply gives the same mechanism: sequential waits, a missing or whole-job time limit, an uncaught timeout error and an all-or-nothing digest. Every reply gives the same fixes: a timeout per feed, error handling per feed, a digest built from what arrived, and (in five of the six) fetching the feeds at once. Which details a reply adds varies from draw to draw: retries come up in round 1's Terse and Conversational replies only. No turn called a tool.

## The replies, round 1, verbatim

### Default (250 words)

> Because the feeds are fetched one after another, the run is a single chain, and any link can hold up or break everything after it.
>
> 1. **Each feed waits for the one before it.** Feed 2 doesn't start until feed 1 is done. If feed 1 takes four minutes to answer instead of two seconds, feeds 2 and 3 wait those four minutes too.
>
> 2. **The run has a time budget.** The scheduler, the server or the script itself usually gives the run a time limit. The slow feed uses up most of it, so feeds 2 and 3 run out of time, or the whole job is stopped partway through.
>
> 3. **One feed's error stops the whole script.** If the slow request hits a timeout and nothing catches that error, the script crashes at that point. The two healthy feeds are never fetched, or they were fetched and their results are thrown away.
>
> 4. **The digest needs all three feeds.** The digest is built only after all three come back. If one is missing, the build step either fails or never runs, so you get nothing, not two thirds of a digest.
>
> So one feed's problem becomes the whole run's problem.
>
> **The fix:** give each feed its own short timeout, catch a failure for each feed separately, and build the digest from whatever feeds arrived, with a note naming the one that didn't. Fetching the three at the same time also stops one slow feed from delaying the others.

### Terse (274 words)

> The script can't finish until every feed has answered, and it only builds the digest after all three fetches. A single slow feed can therefore stop all three from reaching the digest, in one of three ways:
>
> 1. **Everything waits in line.** The feeds are fetched one after another. If feed 1 is slow, feeds 2 and 3 can't start until it's done.
> 2. **Unlimited waiting turns into a hang.** If a fetch has no time limit, the script can wait forever for a feed that never answers. Then the whole run stalls, or the scheduler kills it.
> 3. **The run has a time budget.** The run takes as long as all three fetches added together. If the system that starts it has a maximum run time, one slow feed can push the total over that limit and the run gets killed.
> 4. **One error ends the whole run.** If the slow fetch finally times out with an error and nothing catches it, the script stops right there. Feeds fetched before it are thrown away, and feeds after it are never fetched.
> 5. **Retries make it worse.** If failed fetches are retried, each retry adds another full wait. That makes going over the time limit more likely.
>
> **Ways to fix it:**
> - Give each feed its own short time limit.
> - Catch errors for each feed separately, so one failure doesn't stop the others.
> - Build the digest from whatever feeds arrived, and note which ones are missing.
> - Optionally, fetch the three feeds at the same time. Then the run takes about as long as the slowest feed, not all three added up.

### Conversational (375 words)

> The key is that the script does things **one after another** and treats the run as **all or nothing**. A slow feed exploits both.
>
> **Waiting adds up.** Because the fetches run in sequence, feed two can't start until feed one is done, and feed three waits on both. If one feed's server is slow on a Friday, everything behind it just sits there. Say each feed normally takes 2 seconds and one now takes 4 minutes. The run doesn't take 6 seconds anymore. It takes more than 4 minutes, and the two healthy feeds are stuck in that queue too.
>
> **Waits often have no limit.** Many HTTP libraries never give up on a server that accepted the connection but is sending data very slowly. Without an explicit timeout, the script can wait for a very long time. Meanwhile, whatever runs it on Friday (cron, a scheduler or a CI job) usually has its own time limit. When that limit is hit, the whole process is killed, and the feeds that already arrived are thrown away with it.
>
> **One error stops everything.** Even if there is a timeout, it usually works by raising an error. If nothing catches that error around each individual fetch, it escapes and ends the script before the "build the digest" step runs. So two perfectly good feeds give you zero digest, because one bad feed took down the shared run.
>
> **Retries make it worse.** If the script retries failed fetches, a slow feed can use up its full timeout three or four times in a row. That's often what finally pushes the run past the scheduler's limit.
>
> So the slow feed isn't really the problem on its own. The design lets one feed's bad day become everyone's bad day. The usual fixes:
>
> 1. Give each fetch its own short timeout.
> 2. Catch errors per feed, so one failure only skips that feed.
> 3. Build the digest from whatever arrived, with a line naming the missing feed. For example: "Feed X timed out after 30s, skipped." That beats a silent failure.
>
> I haven't seen your script, so I can't say which of these is the one hurting you. If you share it, I can point to the exact line where it happens.

## The replies, round 2, verbatim

<details>
<summary>Default (282 words)</summary>

> The feeds are fetched one after another, so they behave like a single chain. If one link stalls, everything after it waits. Here are the usual ways that becomes a failed run:
>
> 1. **The waits add up.** The fetches run in sequence, so the run takes as long as all three feeds combined. If feed 1 takes 3 minutes instead of 3 seconds, feeds 2 and 3 can't start until it's done.
>
> 2. **There may be no time limit, or only one for the whole job.** If the script never tells a request to give up, a feed that's slow or hung can keep it waiting for a very long time. Whatever runs it on Fridays (cron, a scheduler, a CI job) often has its own overall time limit. When that runs out, the whole job is killed, even though the other two feeds were fine.
>
> 3. **One error can stop the whole script.** When a slow feed finally gives up, it usually does so by raising a timeout error. If nothing catches that error, the script stops right there and never builds the digest. Feeds that were already fetched get thrown away with it.
>
> 4. **The digest may need all three feeds.** If the build step expects all three, a missing one counts as a failure, not as a smaller digest.
>
> **The usual fixes:** give each feed its own short timeout, catch errors for each feed separately, and build the digest from whatever arrived, with a note saying which feed was missing. Fetching the feeds in parallel also helps, so a slow one only delays itself.
>
> If you share the script, I can tell you which of these is actually happening.

</details>

<details>
<summary>Terse (255 words)</summary>

> The feeds are fetched one after another, so the script can't start feed 2 until feed 1 finishes. A slow feed holds up everything behind it. It can sink the whole run in three ways:
>
> 1. **No timeout, so it waits forever.** If the slow server never answers or never finishes sending, the script just sits there. Feeds 2 and 3 are never fetched and the digest is never built.
> 2. **The run gets cut off.** Whatever starts the Friday job (a scheduler, cron wrapper or CI job) usually has a time limit. The waiting adds up: one feed taking 10 minutes can push the whole run past that limit, and the job is killed partway through. You get nothing, not even the parts already fetched.
> 3. **One error stops everything.** If there is a timeout, it usually shows up as an error. If the script doesn't catch that error, the script stops there. The two healthy feeds are thrown away along with the slow one.
>
> The real problem is that the script treats three separate feeds as one task that either all works or all fails. One weak feed is enough to break it.
>
> To fix it:
> - Give each fetch its own short timeout, for example 30 seconds.
> - Catch the failure for each feed separately. Build the digest from whatever arrived and note which feed was missing.
> - Optionally, fetch the three feeds at the same time. Then the run takes as long as the slowest feed, not all three added together.

</details>

<details>
<summary>Conversational (494 words)</summary>

> The short version is that when things run one after another, they share a single clock and a single fate. A slow feed holds up the whole job, and in many scripts a problem with one feed takes down the whole job too.
>
> **How a sequential run works.** The script asks feed A for its data and waits. Only after A answers does it ask feed B, and only after B answers does it ask feed C. Then it builds the digest. The total time is A's time plus B's time plus C's time. If each feed usually answers in two seconds, the run takes about six. If feed B has a bad day and takes four minutes, the run takes about four minutes, and feed C isn't even asked until B is finished.
>
> **Why that turns into a failure.** Being slow is not the same as failing, so something has to turn the delay into an error. There are three common ways that happens, and I can't tell which one applies to your script without seeing it:
>
> 1. **A per-request timeout.** The script or its HTTP library might give up on any single request after, say, 30 seconds and raise an error. That error is about feed B alone. But if the code doesn't catch it, it travels up and stops the whole script. Feed C never gets fetched and the digest never gets built, even though A and C were fine.
>
> 2. **A limit on the whole job.** The scheduler running your Friday job might kill anything that runs past a set time. In that case the slow feed doesn't fail on its own. It just uses up the time budget, and the scheduler stops the run partway through.
>
> 3. **No timeout at all.** Some HTTP clients will wait forever by default. A feed that accepts the connection and then sends nothing can leave the script hanging until something outside kills it, or until the next Friday's run collides with it.
>
> **The all-or-nothing digest.** All three of these hurt more because the digest is usually built only after all three fetches succeed. So a problem with one feed loses the two good ones as well. Here's a concrete case: feed A arrives fine, feed B times out, and the script crashes. You get nothing, even though one-third of the data was already in hand and the other third was ready to fetch.
>
> **What the fix usually looks like.** Give each request its own short timeout, and catch the failure for each feed separately, so that one bad feed is skipped and noted instead of crashing everything. Then build the digest from whatever arrived, with a line like "feed B unavailable this week". If you want, fetch the three in parallel, so that a slow feed costs its own time instead of adding to everyone else's. If you share the script, I can tell you which of the three ways it's actually failing.

</details>

## What proves the rest

- Resolution, the preview, reset, the backfill and the identity rule: [`tests/test_reply_style.py`](../../tests/test_reply_style.py).
- Each style's instruction in the request the model receives, and none under Default: [`tests/test_reply_style_reaches_the_model.py`](../../tests/test_reply_style_reaches_the_model.py).
- The composer's picker: [`web/src/pages/chat/composer/useReplyStyle.test.tsx`](../../web/src/pages/chat/composer/useReplyStyle.test.tsx); the agent's default on its form: [`web/src/pages/agents/agentReplyStyle.test.tsx`](../../web/src/pages/agents/agentReplyStyle.test.tsx).

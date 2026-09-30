# Changelog

All notable changes to PersonalClaw are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."

## [Unreleased]

### Added

- **The Inbox reply panel asks what the reply should say, and Generate draft and Regenerate write the draft to it.**
- **Files, a chat's file panel and the Code cockpit save an image or a PDF as a versioned artifact: a copy of the file, checked by its contents, which gains its next version each time it is saved again after the file changes.**
- **A model server app can declare that it runs the models it serves where it is, `ProviderCapability.hosts_model` (an SDK addition, set by the bundled Ollama app and `vllm-models`): only an instance of such a type on this machine is a local model.**
- **An answer drawn from a learned lesson can cite it as [Lesson N], which opens that lesson in Memory (`ContextBuilder.build_session_context` and `MemoryService.lessons_context` take an optional `citations_out`: an SDK addition no app has to change for).**
- **A watched-source provider can be handed the number of items one poll keeps and stop there, instead of moving its cursor past what the engine leaves out: `max_items` in `personalclaw.sdk.knowledge.ENGINE_POLL_KWARGS` (an SDK addition no app has to change for; `git-repo` can use it).**

- **A diarization app can say why it could not tell the speakers apart: `personalclaw.sdk.diarization.DiarizationError`, used by `diarization-onnx` and `diarization-pyannote`.**
- **Memory Studio edits a fact where it is shown: Edit changes its value under the same key, and the Audit tab can undo it.**

- **An app's test suite can load model libraries the way PersonalClaw does, reporting nothing and writing nothing outside its home: `personalclaw.sdk.testing.library_env()`, used by the apps repository's test harness.**

- **Settings → Usage → Model prices sets the price a model's calls are counted at, and lists each model you use with its price and where that comes from.**

- **A provider type can say where an instance that names no endpoint sends, and that it runs inside the gateway: `ProviderCapability.default_endpoint` and `in_process` (SDK additions no app has to change for; every branded app declares its default through `register_branded_app`, and `ollama-models` and `bundled-chat` declare theirs).**

- **An app can sign git in with a token it keeps in the credential store, never on a command line, in the clone or in a keychain: `personalclaw.sdk.git.git_argv(…, token=True)` with `git_env(token=…)`, used by `git-sync`.**

- **An app can say why its media features fail: `personalclaw.sdk.stt.SttError`, `unavailable_reason()` on speech-to-text and embedding providers, and `personalclaw.sdk.net.sentence_with_detail` (SDK additions no app has to change for; `bedrock-models` uses all three, and `openrouter-models`, `google-models`, `alibaba-models`, `git-sync`, `dir-sync`, `rsync-sync`, `s3-sync` and `vector-store-qdrant` use `sentence_with_detail`).**
- **The channel conformance kit checks how each approval ends and what a press after it is told, and every task status a stream is given: `assert_channel_contract(press=…)`, used by `telegram-channel`, `discord-channel`, `slack-channel` and `email-channel`.**

- **A channel app can say it sends as you, not as a bot, and then answers no stranger: `ChannelCapabilities.speaks_as_owner`, used by `email-channel`.**

- **A channel app waits for an approval as long as PersonalClaw does: `personalclaw.sdk.channel.approval_window_secs()`, used by `slack-channel`.**

- **A channel app can say the agent compacted the conversation on its own: `personalclaw.sdk.channel.COMPACTION_AUTOMATIC`, used by `slack-channel`.**

- **An app's provider runs git the way core runs its own through `personalclaw.sdk.git`, masks what a program it starts printed with `personalclaw.sdk.security.mask_child_output`, and passes only the SSH agent to a program that signs in over ssh with `child_process_env(ssh_agent=True)` (`git-repo`, `git-sync`, `notes`, `spec-builder`, `skills-sh` and `rsync-sync` use them).**

- **An image or video provider can say why it can't generate: `unavailable_reason()` on `ImageGenProvider` and `VideoGenProvider` (an SDK addition no app has to change for; `google-models`, `bedrock-models`, `openrouter-models`, `alibaba-models`, `fal-image` and `local-image-gen` say why).**

- **An app can say that git is too old to run: `personalclaw.sdk.git.git_argv` refuses a git older than 2.12 with `GitTooOld`, and `git_problem()` says so before anything runs (`git-repo`, `git-sync`, `notes` and `spec-builder` use them).**

- **An app's test suite can keep the OS keychain out: `personalclaw.sdk.testing.keychain_off()`.**

- **An app's test suite can refuse its tests a real local model server: `personalclaw.sdk.testing.refuse_ports()`.**

- **An app's test suite can keep its tests' git off the machine's credential helpers, as core's own suite now does: `personalclaw.sdk.testing.neutral_git_env` and `refuse_git_helpers`, used by the apps repository's test harness.**

- **A sync transport can refuse a key that leads out of its folder, and the sync report names it: `personalclaw.sdk.sync.KeysRefused`, with `is_path_in_store` and `is_safe_relative_path`, the rule the sync holds another machine's paths to, used by `dir-sync` and `git-sync`.**

- **The sign-in key can be replaced, from Settings → Security or `personalclaw auth rotate-key`: every device is signed out and told why.**

- **Settings → Security sets the sign-in lockout: how many wrong attempts, and how long it lasts.**

- **Settings → Security sets how long a sign-in lasts.**

- **A sidecar app's engine installs from the dashboard: Install engine, on its card in Settings → Providers and on its Configure page.**

- **Install consent and the Store card say what an app needs that PersonalClaw doesn't install.**

- **You choose which chat channel asks your approvals: Settings → Notifications → Send approvals to.**

- **Codex's compressed sessions come over too.**

- **A task's due date now reminds you the day before, once, and you can turn that off per task.**

- **A workflow can be edited in the dashboard.**

- **A remote MCP server that signs in with OAuth connects: Sign in on its Tools page card, and it stays signed in.**

- **⌘K searches what is inside the app, not only its pages.**

- **An SDK change is a reviewed diff, and CI runs the first-party apps' contract on it.**

- **Bringing your setup over lists what comes across, with a count and a checkbox per group, and lets you pick item by item.**

- **A fresh install can hold a conversation without an account, an API key or a provider — after one download it tells you about first.**
- **A workflow step whose output ignored its declared `schema` now says so on its own row.**

- **Each room member now reads only what it has not seen.**

- **Validation warns about words on a gate that its kind never shows anyone.**

- **Agents → Export to Claude Code writes your agents into Claude Code's agents folder, after you confirm it, and never over a file PersonalClaw did not write.**

- **An Invoke Agent automation can work in a folder you allowed in Settings → Agent defaults, so its agent reads the files there; the automation's save refuses any other folder, as a Run prompt automation's now does too.**

### Changed

- **`personalclaw.sdk.channel.transcribe_audio` raises `SttError` with the reason when there is no transcript, instead of answering `None` (`slack-channel` already catches it).**

- **The snapshot is the backup: Settings → Backups and `personalclaw backup` say so, and that the hourly export and sync carry your records only, not your skills, scripts or uploads.**

- **The hourly export and each sync no longer copy your skills, uploads, workspace and installed apps as files nothing could restore.**

- **`EmbeddingProvider.embed_batch` answers `None` for a text it did not embed, never an empty vector (`bedrock-models` and `sentence-transformers` implement it).**

- **MCP Tool Servers ships with PersonalClaw: an MCP server you add or import is one every agent can call, with nothing to install from the Store.**

- **Checking a workflow spec, previewing an edit to a running workflow, a workflow's drift report and an automation's dry run are tools of their own that only read (`workflow_check`, `workflow_edit_preview`, `workflow_audit`, `automation_dry_run`), so none of them asks; `workflow_repair` repairs, and `workflow_author`, `workflow_edit` and `automation_run` refuse the old preview arguments.**

- **A `ToolDefinition` that declares `RiskLevel.SAFE` asks nobody, whatever its `requires_approval` says (SDK: a change no app has to make, and MCP Tool Servers now says so itself).**

- **Trusting an MCP server's read-only labels lets its reads run without asking in every approval mode, as the Tools page now says.**

- **An approval's `is_read_only` is true or false: whether the call is established as a read.**

- **The agent lists the triage rules with `triage_rules_list`; `triage_rules` adds and revokes them.**

- **A subagent works in the workspace unless you add another folder: `agent.subagent_cwd_allowed_roots` is empty by default.**

- **The git PersonalClaw runs refuses a remote at a local path; reach it over ssh or https.**
- **Settings → Agent defaults → Runners runs a runner's `--version` only when you press its Check, and only for a runner an installed agent app set up.**

- **PersonalClaw needs git 2.12 or newer, and refuses an older one with the version it needs: an older git ignores the settings that stop a repository's own configuration from running a program.**

- **`note_unknown_sender` loses its unused `silent` argument.**
- **`make build` is the one distribution build, and it proves what it built.**

- **`credentials.json` is gone: the gateway moves what it held into the credential store at its first start.**

- **The Session Map is a map of your messages: one marker for each message you sent, all one length, with colour showing which are on screen.**

- **Settings → Updates lists each change as one plain line, shows what an available update brings, and links the upgrade notes.**

### Removed

- **`personalclaw.sdk.channel.generate_token` is off the channel SDK.**

- **The `api_key` and `oauth2` auth modes are gone.**

- **The per-site browse "profile key", which nothing used; a key an earlier release stored is deleted at start.**

- **The README's coverage badge, so the job that measures coverage no longer holds a token that can write the repository.**

### Fixed

- **A Deny, or an approval nobody answered in time, refuses the call it is about and the calls sent with it, and the next call the agent makes asks again, as the approval card says.**
- **A finished turn's folded steps say how many of them failed, so a failed step shows under a reply that says it worked, without opening the fold.**
- **A tool result shows its text without the markers that tell the model it is data: web search hits, fetched pages and every other fenced result, on the chat's tool card, in the full-result view and on the Tools page.**
- **A turn sent again after an empty answer or a lost connection is the same message: no second bubble holding the chat's context blocks, and the retry is given the context the first attempt had.**
- **A message reaches its model a second or more sooner: the bundled prompts are seeded into the store once, not again at each of the prompt lookups a turn makes.**
- **A model instance that stops answering says so on its Settings → Providers card, on each Settings → Models chain entry that uses it, and in the Doctor, by name and in words.**
- **A chain entry whose model is gone from its instance reads "unavailable", and the Doctor offers to prune it; pruning removes the model from the chain.**
- **A loop that is stopped, fails or finishes leaves none of its tasks "in progress", and a task its worker left unfinished can be taken again when the loop resumes.**
- **Mission Control lists each loop once, by its name, however many of its workers are running, and never names a chat by its internal key.**
- **The Inbox's "Needs reply" lists only messages that need a reply, not app-update notices, notes you wrote or requests from your agents.**
- **The Inbox's Dismiss all dialog names the Done filter by the name the page shows.**
- **An image or document artifact never takes text as its next version, so its file, the bytes it serves and its details always agree; the agent is told the tool that does make one (for an image, `image_generate` with its slug), and the Iterate panel names that tool from the start.**
- **An open artifact shows each new version as soon as it is written, whoever wrote it: the Iterate panel's chat, another chat, a workflow step or another tab. An image's page and its library card draw the new picture, not the first one they loaded.**
- **A declined or retried tool call proposes a refinement only of a skill whose procedure asked for that call, never of one that joined the turn on a matching word.**
- **A workflow run that fails or stops before it finished says so in the Inbox and the bell, and the row opens that run.**
- **The daily dollar cap holds when calls run at the same time: a call that costs money sets aside what it may cost and starts only when that fits beside what is spent and what the calls already running have set aside.**
- **Past a spent daily dollar cap, calls to a model that costs nothing keep running, so automations, loops, subagents and app workers on local models no longer stop until midnight; only the token cap stops work before it starts.**
- **A model reached through an OpenAI-compatible instance on this machine is no longer counted as free: it is priced by its model's id or has no price, a daily dollar cap refuses a model with no price, and Settings → Usage → Model prices lists it, and every model spent on in the last 30 days, to price ($0 declares it free).**
- **An agent's memory recall, project context, triage rules, prompt render, batched subagents and self-nudge stop work on every install, not only on the development server, and a call the gateway refuses says why instead of asking to sign in.**
- **A loop's model call on an instance with a Request Timeout waits as long as that timeout says, not a fixed 300 seconds, and a call that runs out of time says which model on which instance stopped, after how long, and where to bind a faster one.**
- **A code loop has one writer at a time: a task worker starts only from a tree with no uncommitted changes while the stage worker is between cycles, the stage worker stands down while task workers run, and a loop on a model on this machine runs one task worker at a time.**
- **A loop's planner writes its walkthrough files into the loop's own folder, never into your repository, and a file of the same name you keep there is never read or removed.**
- **A loop's workers are told where the loop's status file and brief are, and a read repeated with its output thrown away (`2>/dev/null`) counts as a repeat.**
- **A request about one piece of work, such as “a shorter version of those notes”, is no longer saved as a standing preference; a preference that is saved is said on its turn, still after a reload, can be forgotten from there, and reads in words in Memory rather than as stored data.**
- **A room's Export transcript downloads the file instead of replacing the app with it, and names the member who said each line; every time in a chat or room export carries its UTC offset.**
- **The “text-to-speech is switched off” line under a chat goes away once Speak works.**
- **Voices says what a clone voice needs where it is chosen, and refuses one its engine cannot speak with.**
- **Regenerating an answer that made an image saves the new image as that image's next version, not as a second image.**
- **Stopping or restarting the gateway waits a few seconds for the app package repair its start began, so an app it has just repaired stops with the rest instead of starting again after them.**
- **A scheduled automation's notification and chat message say what it produced, or why it failed, instead of its name alone; an automation that had nothing to do says nothing.**
- **A failed automation whose failures go to the Inbox is an item in the Inbox, not only a notification.**
- **A task a workflow run files for one of its steps is the run's: it starts with its step's state, names no author, is not counted as yours or as ready work, and links to its run; the morning triage files none.**
- **The agent's file tools read a path that starts with `~/` as your home, within the folders they may reach.**
- **Tasks → Filter & sort → Mine leaves out the tasks that are not yours; choosing it used to leave the list as it was.**
- **The first message after a restart no longer holds every page up for half a minute while the agent's tools are indexed: the index is kept across restarts, filled in batches, and never on the path that answers requests.**
- **A reply a restart cuts off says so in the chat, after the restart too, and offers Retry; it no longer reads as a Stop you pressed.**
- **A workflow step you allowed is not asked again when a restart resumes it, while every other start still asks (SDK: `SubagentInfo.request_key` and `approved_at`, and the same two on `SubagentManager.spawn`, additions `slack-channel` does not have to change for).**
- **A page open across a restart or a dropped connection reads again what it may have missed, approvals first: a run's page shows the approval its resumed step asked for, the approval nudge still comes, and the Inbox, notifications, lists, artifacts and chat history catch up.**
- **A restart no longer waits ten seconds on a page left open, or on a model's answer nobody will read, and its log names what a stop could not finish and whether the gateway restarts or exits.**
- **A chat turn that ran its steps and wrote no answer asks its model once for the reply, and if none comes says the turn has no answer, with Retry, instead of "Response complete."**
- **Regenerating a reply an automation, a subagent's report or an auto-nudge started runs that turn again, instead of deleting it and asking the question before it.**
- **A phone that pairs opens on its companion, and on a phone the menu's Companion row leads back to it.**
- **The Morning triage card's link to the item opens that run, and a knowledge result's link opens the item.**
- **Opening a terminal past the limit says how many run at once and to close one, and New session waits until you do.**
- **Start from template opens the chosen template's form with what you typed already in it.**
- **A trigger that cannot be created yet says why beside Create trigger, naming a required setting by its label.**
- **A Run workflow trigger's panel shows the workflow it starts and the inputs it starts it with.**
- **Knowledge a workflow saves goes through the same enrichment as anything you add, so semantic search can find it.**
- **`personalclaw --version` and `--help`, building the web app, and reading a setting, a credential, your apps or memory no longer create a PersonalClaw home; the first thing written into it creates it, readable only by you.**
- **A chat you leave while it is still loading no longer reads the conversation again, or plays its speech and sounds, seconds after you have left it; and a new chat's first message reads its conversation once.**

- **An automation that starts an agent starts it on the Allow you gave the automation, without asking again; its run's history says how the agent's run went instead of "launched", and a start you decline reads "declined", with no note calling it a failure (SDK: `ActionResult.work_id` and `SubagentInfo.declined`, additions no app has to change for).**

- **A schedule keeps its time across a change of the clocks: a cron whose next run falls after daylight saving time ends or begins runs, and is described, at the hour it names instead of an hour off, and a time the clocks skip runs as they jump.**

- **Moving a task to another project from its editor saves it in that project, and a task in no project opens on "(none)".**

- **`automation_create` tells the agent which automations wait for your Allow: one that sends your own words is active at once.**

- **A pairing code says the time it stops working, with one full stop, on a channel's Owner and on Sender trust.**

- **The quiet-hours window's two times and the speaking-speed slider have names a screen reader reads.**
- **A folder you watch brings in the notes already in it, newest first up to 1,000 files or 100 MB: the add form says so, the folder's row says what is still to come and what the bound left for later, and later checks bring in only what changed.**

- **A folder you watch is checked as often as you set it, down to once a minute, and every source's row states how often it is really checked.**

- **What you add to the library yourself is read before a watched source's backlog, and a queued item says how many items are ahead of it and how long recent ones took.**

- **A feed or page with more new entries than one check takes brings the rest in on the next checks instead of never.**

- **Library enrichment, history consolidation and thread compression move to the next model in their chain when one is too slow, and a slow model is reported as timing out, not as no model being available.**

- **A feed's or page's entries are stored as their words, not their HTML, so they preview and read as text.**

- **An item that has been embedded no longer says no embedding model is bound.**

- **A title you give an upload, the file's own name included, is kept after enrichment; the suggested title stays on the item.**

- **Labels that name a type read as English: "Choose an audio file", "New PDF", "an 8-cycle budget".**

- **A video or voice memo whose speech could not be transcribed no longer reads "Transcription done": the step fails with the reason, and a recording with no speech says "No speech found".**

- **The microphone and the OpenAI-compatible transcription endpoint answer a transcription that could not run with its reason, instead of an empty transcript.**

- **Speaker diarization that cannot tell the speakers apart fails its step with the reason, instead of finishing with no speakers, and long recordings get the time transcription gets.**

- **A recording's text keeps who said what and the corrections the Vocabulary made, and a video's text and description include its narration alongside its slides' text.**

- **The Vocabulary builds itself from your knowledge graph, people first, so transcriptions are biased toward the names in your notes without pressing Rebuild; a word that only sounds like a term is proposed, never rewritten, and a graph term is turned off rather than deleted.**

- **A skill joins a message only when it is clearly the one the message asks for, and every turn names the skills that joined it, a turn that only calls tools or is stopped included.**

- **A request to set up an automation or a reminder carries the automation tool's inputs, a path in a request no longer crowds out the tools it needs, and a refused tool argument names the ones the tool takes.**

- **Reads that reach one store at the same moment — Memory Studio's reload after a save, the knowledge library, the lexicon, the code index — no longer fail or return wrong rows.**

- **A page whose read fails says whether the server answered with an error or did not answer at all.**

- **`personalclaw doctor` passes in the one-container install: signing in needs no channel (`personalclaw token`), the token check asks the container's own address, and the Runtime block shows no warning without words.**

- **A second tab opened during first-run setup joins the setup where it is, with the name already entered, instead of starting over at step 1.**

- **A model provider whose Save and test fails in first-run setup is no longer a dead end: every step of it offers Pick a different provider, which brings back the local model, the network scan and the provider list.**

- **Only a source checkout offers to install updates on its own: first-run setup and Settings → Updates tell a pip, uv, container or desktop install how its updates arrive, and Staged no longer hides the update notice there.**

- **A refused model endpoint names the control that allows it, Allowed hosts in Settings → Security → Network egress, instead of config keys, and so do a web fetch's and a net-fetch action's refusals.**
- **The instructions you bring over from Claude Code or Codex reach every chat whole, not as 220-character summaries a small model's window cut again, and the notes they were remembering become memories recall finds and the re-index embeds.**

- **The embedding re-index embeds every memory with an Ollama embedding model, where it used to fail on every other one and leave half of them read by keyword.**

- **Settings → Secrets no longer lists a switch PersonalClaw sets for itself as a credential inherited from the host, and Settings → Security no longer says the credential store is always in `~/.personalclaw`.**
- **A tool server's card says what the server does after every add, edit, Allow, removal or switch-on, reading "checking" until its probe lands, and never shows a removed server's state.**

- **Sign in is offered only on a tool server that has a sign-in; one that takes a token instead says what it wants, on its card.**

- **A tool server whose host cannot be looked up says so, instead of reading as a timeout.**

- **MCP Tool Servers names every transport it connects over (a command, Streamable HTTP and SSE), and its card in Settings → Providers can save a Streamable HTTP server.**

- **A message from Telegram, Discord, email or a Slack thread linked to a chat is answered however long its turn runs: the session sweep no longer ends a runtime a turn is using, and it knows every chat that exists however the chat was made (SDK: `SessionManager.register_dashboard_sessions` replaces `set_active_dashboard_sessions`, a change no app has to make).**

- **A turn that ends without an answer says why on the channel its conversation is linked to: the error the chat shows, that it was stopped from the dashboard, or that it stopped before it finished, which the chat now says too.**

- **The install scanner no longer reports a comment or a docstring as something an app does, and a finding's evidence is the code it matched rather than a comment above it.**

- **A settings list whose schema describes its entries is edited as a list in Apps › Configure and Settings › Providers, chips for texts and a row per entry for records, instead of typed as JSON (`slack-channel` describes its lists).**

- **A local model that takes minutes to read a long conversation is given the time: an Ollama instance waits up to 600 seconds for its first word (10 for the server to answer at all), and a model that still has not started is stopped once, not retried, with a sentence naming the setting to raise (`personalclaw.sdk.model.FirstTokenTimeout`, used by `ollama-models`).**

- **A turn sends the tool schemas its request needs, and no more than an eighth of a small window: tools are chosen by what you asked rather than by the prompt's own wording, and every other tool is still listed by name (`personalclaw.sdk.prompt.user_request`, used by `bundled-chat`).**

- **Text typed after an @prompt fills the prompt's arguments: `@standup ~/src/app` renders it for that path, and single words fill `{{arg1}}` to `{{arg9}}`.**

- **A tool loop that keeps getting the same answers is stopped: a read that returned the same result three times in a turn is refused, and a turn with more than eight repeats ends with a sentence in the chat saying why, a CLI agent's turn too.**

- **A model's reasoning markers no longer show in its reply, and a local model's thinking shows as thinking, not as its answer (`StreamingTagSplitter(channel=…, inside=…)`, `make_think_splitter(inside=…)`, `CHANNEL_OPEN` and `CHANNEL_CLOSE`, used by `ollama-models`).**

- **`memory_remember` takes the `scope` and `workspace` the system prompt teaches and saves a lesson's `negative`, and every worked example in the tool reference is one its tool accepts.**

- **A model that did not answer is logged as one warning naming the model and the cause, not a traceback, for a chat turn and for an auto-title.**

- **An approval is answered where it is announced: its Inbox row and its notification offer Approve and Deny, and once it has ended they say so instead.**

- **The trigger presets are on the form New trigger opens, so Morning briefing and the others can be picked on a home that already has triggers.**

- **PersonalClaw's own triggers no longer ask you to confirm their cadence: the cadence floor warns only about one someone set, not the heartbeat pass's designed every 60s.**

- **A subagent waiting for you to approve its start is not stopped by its time limit, which counts from when it starts running, so the loop or trigger that asked is not reported as failed while it waits; one that does end while waiting on you says so.**

- **Memory consolidation no longer turns PersonalClaw's own records (procedures, lessons, the self-model) into facts with no value, so Memory Studio no longer lists them as "null", and a fact with no value reads "No value".**

- **A lesson learned from "never do X" reads as you said it, not "Never: never do X".**

- **The Incognito and Temporary chat notices say what happens: the chat stays out of your history and search, and PersonalClaw still keeps its transcript.**

- **The import step says why an item starts unticked: a skill the security scan warned about is named as that, not as something the other tool does not use.**

- **`personalclaw setup` and `personalclaw doctor` run each app's step with that app's own modules: a second app's `from provider import …` no longer runs the first app's `provider.py`.**

- **PersonalClaw's git reads the configuration files your own git reads: the ones `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` name, and none of the system ones with `GIT_CONFIG_NOSYSTEM` set (`personalclaw.sdk.git.git_env`, used by `git-repo`, `git-sync`, `notes` and `spec-builder`).**

- **The Routing tab shows a model nothing prices as unpriced, never free, and such a model no longer knocks a priced one off the frontier or reads as cheaper in a proposal.**

- **An evaluation gate's spend and the Usage page's "Not included" figure say how many calls they could not price, and the gate's dollar bound counts them as calls it could not count.**

- **The judge bench and the model bake-off read a model's cost as unknown when any of its calls had no price, and a model on this machine as free.**

- **The bundled offline model, and a provider instance that names no endpoint and relies on its app's default on this machine, are priced at a known $0 and tried first by local-first routing: pricing, routing and the spend guard's outbound scan decide what runs here by one rule.**
- **On Linux x86_64, the hourly export, the daily reclaim and every other job that reads a store open it through the same SQLite as the store, so the store's next writes no longer fail or vanish after them.**

- **The spend caps, the Usage page and every turn's cost price a call at the rate you set in `model_rates.json`, and a cap says how many calls it could not price rather than counting them as free.**

- **An open-weight model is free only on this machine: `mistral-large` and a `llama3.1` served elsewhere are no longer priced at $0 by their names.**

- **A rate a provider app declares prices that app's instances whatever you named them, and never another provider's.**

- **Routing orders a model first as local only when its provider's endpoint is on this machine, whatever the provider is called.**

- **The monthly usage recap no longer calls its total a floor for turns that ran on this machine.**

- **Stopping PersonalClaw, a workflow run, a loop or a channel no longer waits forever on a task that will not stop: it waits a few seconds, logs what did not finish, and goes on.**

- **A generated image or video larger than 10 MB is saved whole instead of cut off, and one that can't be saved says why.**

- **An encrypted sync that can't read its store is reported as a failed read, as an unencrypted one is.**

- **A sync whose first registry write lost to another machine's write that never landed writes it on the next try, instead of losing every try.**

- **Settings → Speech & Transcription shows speech-to-text on until you turn it off, as voice input behaves.**

- **Settings → Models keeps an image or video provider that can't generate in its row, with the reason it gives.**

- **Voice input shows the reason speech-to-text can't run instead of setup advice for a model already chosen, and the loop composer says why a recording did not become text.**

- **The Models page's Test shows a failure's whole sentence, next step included.**

- **The OpenAI-compatible transcription endpoint no longer puts "Transcription failed:" in front of a provider's own sentence.**

- **`personalclaw app new` writes a `.gitignore`, so an app's first commit no longer publishes its compiled bytecode and the path it was built on.**
- **First run's Web search lane is ready only once the agent can search: after a search provider, it offers Web Tools, the app that gives the agent its `web_search` tool.**

- **A tool that only reads asks nobody over an agent CLI either: in a Normal chat, in a background agent's run and in a room.**

- **`personalclaw run --allow` runs the writes it grants: the run's chat approves its own calls, within the operator's approval ceiling.**

- **The Normal and Trust reads approval modes say what they do.**

- **The notification that someone you haven't paired messaged you offers Allow and Deny, and Allow asks you first.**

- **An automation's agent can message you (SDK: `ToolDefinition.tells_owner` and `LLMEvent.tells_owner`, additions no app has to change for).**

- **An automation that runs an agent or a workflow says it finished when it has, not when it started.**

- **A conversation held on Telegram, Discord, email or Slack is told there when it is compacted, as its dashboard chat is: a `/compact`'s result, the agent compacting on its own, and a restart at the context threshold.**

- **A chat linked to a channel thread (`POST /api/chat/sessions/{session}/channel-link`) continues there, as a handed-off chat does: its answers and notices go to the thread, a reply there continues it, and the thread opens on the channel that issued its id.**

- **A message to a chat or user id with no channel named goes out on the channel that id belongs to, and an id two channels could take is refused with both (`email-channel` takes only an address).**

- **The Triggers page checks a send-message action's chat channel when you save it.**

- **A chat or room turn that fails with an error PersonalClaw doesn't recognize says what to do next, with the error's own words after it.**
- **The container update commands are the ones you installed with: the README's `docker run`, or Compose's.**
- **`personalclaw update` in a container says you're on the newest release when nothing newer is published, and no update offers or installs an older release; release candidates compare in order.**
- **On the beta channel, a container's update commands pull an image that exists: `:beta` only while a release candidate is the newest release.**
- **With update checks off, a container's Settings → Updates makes no call to GitHub.**
- **The update check picks the release every update installs, the highest version on your channel, so a back-patch of an older line can't hide a newer one.**
- **Settings → Updates says when a pin names an older release than the one running, and shows the rollback it sets up.**
- **The rollback confirm in Settings → Updates shows `personalclaw snapshot` as a command instead of printing the backticks around it.**
- **A release candidate's release no longer fails its own checks: the image smoke, the wheel gate and the release notes compare versions, not their spelling.**

- **In a container, `personalclaw service`, `stop` and `restart` say the container runtime runs the gateway, print the host command, and change nothing.**

- **`personalclaw` is found in the dashboard's terminal on the container image.**

- **`personalclaw stop` needs neither `lsof` nor `ps`, stops only this home's gateway and waits for it to exit, and `restart` never starts a second gateway beside one it could not stop.**

- **On a host without `ps`, the container image included, an app update names the processes still running its previous version.**
- **Sync brings another machine's automations and hooks into a home that has its own, and a run on either machine is not a conflict.**

- **Keeping this machine's version of a sync conflict leaves it as it is.**

- **Sync leaves each machine's own counters, model spend, scheduler state and runner health on it: two machines' copies no longer turn every pull into a conflict to review.**

- **The hourly export and sync carry saved prompts, prompt snippets and every other file in a store's folder, and name any file they cannot carry.**

- **A sync pull writes only what it changed, and never over a file this machine wrote while it merged.**

- **A workflow run or a loop stays on the machine that ran it: another machine's, synced or merged in, used to run again here.**

- **Sync no longer writes chat sessions, scheduled-run history or channel history into a file named for the year: those folders stay on each machine.**

- **A replace restore holds every workflow run, loop and agent its snapshot had working, until you resume it, instead of running again what each did after the snapshot.**

- **A replace restore keeps this machine's workflow run history in its pre-restore copy, which the snapshot's used to overwrite.**

- **A merge restore and an import leave this machine's spend, tool and savings counters, backup schedule and due-date notices as they are: another machine's spend no longer counts against your budget caps.**

- **`personalclaw backup export` into a folder that holds anything but an earlier export refuses, instead of deleting the folder.**

- **An import in Settings → Import / Export merges; a replace, which rewrites what the running gateway holds open, runs from `personalclaw restore <archive> --mode replace` with the gateway stopped.**

- **Merging a snapshot in Settings → Backups runs, where the gateway it runs in used to refuse it.**

- **`personalclaw restore` takes an export archive as well as a snapshot.**

- **Searching your chats as you type reads a bounded amount per keystroke, and a chat found by reading it shows why it matched.**

- **Every change of the embedding model re-embeds what it has not: its provider removed, a local model bound in setup, a binding another process wrote, and a model not ready yet, once it is.**

- **A re-embed that stops partway keeps what it did, and leaves no search index holding the previous model's vectors.**

- **A tool that only reads no longer asks for approval: a chat stops asking about recalling memory or a workflow's status, and a run with nobody to ask uses them.**

- **A message you ask for on one chat channel goes out there and on no other: "message me on Telegram" no longer lands on Discord.**

- **A workflow run's steps work in the folder the run owns instead of being refused: its scratch workspace (every batch has one), its own worktree, its project's folder when it works in place, or its project's context folder.**

- **A workflow run that works in place runs its steps in the folder its project is bound to, instead of in the project's context folder.**

- **A workflow run whose worktree or container could not be made works in the scratch folder it falls back to, and its record says so.**

- **Git over ssh signs in through your SSH agent, and the service install keeps git's own certificate settings.**
- **The Agents page says why an agent runtime's agents couldn't be listed, instead of "No agents discovered."**

- **An agent runtime on the Agents page that isn't ready says why, instead of a count of 0.**

- **A reminder a trigger starts arrives named for its trigger, not as a subagent's completion (`SubagentManager.spawn` takes an optional `title`, which `SubagentInfo` carries; `slack-channel`, the one app that spawns, needs no change).**

- **A run's Document panel shows what its steps wrote, also on a run with no project, and only what they wrote.**

- **A stop or a Restart records the run it cut off as interrupted, names which, and puts it on the review; Run now counts as running while it runs.**

- **The chat says when it compacted a conversation on its own, and how much, in the words `/compact` answers with.**

- **An automation's Run now spins only for its own run and says what the run recorded; a question's Deny waits while an answer is being sent.**

- **You are told which background agents a restart stopped.**

- **The workflow editor and the chat's progress card name steps by their labels.**

- **Settings hints show commands and paths as code, not wrapped in backticks.**

- **Something you type into a setting just as its panel opens is kept.**

- **The last lines the gateway prints as it stops reach its log.**

- **An agent runtime whose ACP adapter isn't installed offers Retry on its card even when no install of it failed.**

- **Every model call PersonalClaw makes is on the Usage page, chat titles, tags, judges and digests included.**

- **The Usage page's daily budget sets the spend the cap counts beside the cap, not every chat turn.**

- **`personalclaw setup --provider` takes an agent runtime (`native`, `acp`, `acp:<cli>`) and refuses anything else instead of saving it.**

- **`personalclaw setup` shows the workspace `PERSONALCLAW_WORKSPACE` sets, instead of asking for a folder it would not use.**

- **An agent's tool is told why the gateway refused it, not only the HTTP status.**

- **A run the chat's `automation_run` could not start is reported as not run.**

- **The gateway's missing-dependency errors go to stderr.**

- **A masked secret no longer takes the backslash of an escaped quote with it, so masked JSON still parses.**

- **A text-to-speech model that is chosen but cannot speak yet says so, instead of answering with no audio.**

- **A loop's cost counts the turns its workers ran, so its cost cap stops it.**

- **The daily spend cap counts a subagent given its own model, a loop's planning, a webhook's agent turn and a scheduled job's reading of its subagent's result.**

- **The daily spend cap counts each subagent once, including one on an agent CLI.**

- **A session stopped the hard way comes back as the agent it was, where it was.**

- **A webhook stopped by the spend cap says so, not "internal failure".**

- **A model selftest fails on an empty reply, and a voice selftest on an engine that wrote no audio.**

- **A library's model steps run on the model itself and count against the daily spend cap, so a video's description is written by the model instead of left as its raw transcript.**

- **A loop's judge bound to Chat or Code is the model itself, with no tools, and counts against the daily spend cap.**

- **The daily spend cap counts a loop, a subagent or a webhook's turn on an agent CLI, and refuses its next turn once the day is spent.**

- **A subagent's report back to a webhook's or an app's turn counts against the daily spend cap.**

- **A provider's selftest tests that provider and says a failure whole, and one for a name no provider has is not found.**

- **A model server on another machine is no longer priced as free: only one on this machine is, and any other is priced by a rate set or declared for it, or reads as unpriced.**

- **Routing & Efficiency says what it measures: every call on Reasoning, and on Chat or Code & tools only the calls automation makes, not the turns you type.**

- **The re-index that re-embeds your library shows wherever it runs, and a stop no longer leaves passages behind.**

- **Knowledge compares two embeddings only when one model wrote both, as memory does.**

- **Voice input and a refused re-index say why speech-to-text or the embedding model can't work, in its provider's words, and a transcription that failed no longer reads as a recording with no speech.**

- **An error above the chat composer stays until you dismiss it or send again; only an update clears on its own.**
- **Sync, a merge restore and an import bring another machine's inbox items, document comments, research reports, tags, tag boards, folders and dashboard views into a home that has its own.**

- **An automation, hook, inbox item, tag or other record written while a sync, a merge restore or an import runs is no longer lost.**

- **`personalclaw restore` merges while the gateway runs, and refuses a replace in the words the dashboard uses.**

- **Memories and knowledge another machine's model embedded are re-embedded when a sync, a merge restore or an import brings them in.**

- **An edit made on one machine reaches the other by sync, whichever machine made it and whatever either clock says; an edit made on both is a conflict to review.**

- **The image and video tools say where to choose a model, and name no vendor.**
- **Clearing Chat posts one notice, not ten.**
- **A memory no model embedded is counted everywhere search is described, as one number.**
- **A step you open from a chat's workflow card shows its result once it finishes, and a loop or fan-out of more than ten items shows its real last one.**
- **A chat found by a search is named as the chat list names it, not by its internal key.**

- **Searching your chats as you type finds what was said late in a long chat.**

- **Searching your chats lists each chat once, with its latest words, instead of sometimes twice.**

- **A replace restore keeps the engine each returning app has here, instead of moving it into `pre-restore-<ts>/` and asking for Install engine again, and says which engines it kept or set aside.**

- **A gate's Wake it now, Approve and Submit, and a trigger's Allow, show they are working while their own answer is out, and a screen reader hears it.**

- **`personalclaw setup` never says "Done!" after a step that failed: it names each failed step with the command that runs it again, and exits 1.**

- **Settings → Usage counts a room's summaries and a chat's history compression, and its "Not included" figure no longer counts a call a usage row already holds (SDK: `LLMEvent` gains `audit_ids`, an addition no app has to change for).**

- **"Remind me at 5 pm" makes a task that runs once, at 5 pm, where you are.**

- **An automation made in chat runs what it says.**

- **The banner `personalclaw` and `personalclaw chat` print draws PersonalClaw.**

- **`personalclaw` or a command group run with none of its commands (`personalclaw cron`) is a usage error, exit 2 with the usage on stderr, and a refusal prints on stderr, never on stdout.**

- **An automation set to run every 0 seconds or fewer is refused where it is made, instead of saved as one that never runs.**

- **Each cycle of a gate inside a loop asks its own question.**

- **A goal monitor run no longer promises a log it never writes.**

- **OpenAI-compatible clients, A2A agents and capture imports reach `/v1`, `/a2a` and `/capture/import` with their own token.**

- **The status card says how long this browser's sign-in has left.**

- **A Restart saves what a stop saves, time travel's pending history included.**

- **Time travel's memory history follows your memory, and resolving it no longer creates a folder.**

- **Installing or updating an app with a `source` that is not a string is a `400` naming the field.**

- **After a reload, Settings → Models finds a Repair still running and shows its progress under its model, and how it ended.**

- **A chat that started on a chat channel is asked its approvals in that chat, with nothing to set up.**

- **A channel's approval prompt is told how its approval ended.**

- **A chat's progress lines on its channel say how each call ended.**

- **A goal monitor wakes on the trigger it armed, and finishes when its goal is met.**

- **`personalclaw cron trigger` runs the jobs `cron add` makes and `cron list` shows, and the chat can run a job whose name has a space.**

- **An app's request whose body is not a JSON object is refused before it reaches the route.**

- **Stopping the gateway saves the settings history it was still holding.**
- **A run that went on past a failed step says so, a Run button says what its run recorded, no gate carries words nobody sees, a run's history row is a sentence for every action that prints JSON, and a snapshot merge leaves another home's trigger stamps behind.**
- **An agent's or a trigger's call to an app route reaches the app.**

- **Allowing something no longer shows a failed request in the browser's console.**

- **A chat that started on a channel can be continued on another one.**
- **A check gate that fails ends what follows it, a revise closes the question it answered, every queued edit applies, and four surfaces say what happened.**
- **A workflow's approval gate waits your approval window, a retry a grant ran settles its note, and a lifecycle hook's agent knows its trigger.**
- **A chat channel's approval prompt shows what will run: the tool, its arguments and why, as the dashboard's approval card shows them.**
- **Two mails sent in the same second are two Inbox rows.**
- **Slack's inbox source reads the channels you choose, in Settings → Inbox.**
- **After Send, the Inbox's open item shows your reply as sent.**
- **A channel's dashboard link signs in its owner, and nobody else.**
- **Email's owner is paired from its Configure page, like every channel's.**
- **An Embedding rebind or clear reaches every memory store at its next use, and memory never compares one embedding model's vectors with another's.**
- **The status chip says "Choose a model" when no model is chosen, not "12 degraded".**
- **A media call names its model, like chat, and so does every binding.**
- **A memory no model embedded joins semantic search once one is bound, a keyword hit ranks by its score, and the knowledge re-index re-embeds only what the model has not.**
- **A notice about a surface going down says what is wrong, in the chip's words.**
- **The chat list answers at once on a 12,005-chat history, the search index catches up in two minutes instead of five hours, and a search says when it has not looked in every chat.**

- **A file-backed artifact never writes its file unless the request carries the text to write.**

- **Only an approval lets what follows an approval gate run, a trigger that stops for you asks you, and every question a run asks is its own.**
- **Signing in on one more device no longer signs another one out without a word; a device that is signed out is told why; and Settings → Devices lists every sign-in.**
- **Nothing signs in for longer than 90 days, and asking for longer says so; and every refusal of a sign-in says why and how to sign in, in the desktop app too.**
- **Installing an app's packages, speaking a reply, the Doctor's speech probe and uninstalling the service no longer leave anything outside PersonalClaw's home.**

- **A first loop from onboarding stops after one cycle, each home has a tmux server of its own, and the last files PersonalClaw left outside its home are gone.**

- **A loop that finishes on its last allowed cycle ends complete, so onboarding's first loop no longer ends asking for a decision nobody can give.**

- **The Terminal page no longer lists a workflow run's durable workers as detached terminals, and a durable worker is live only while its command runs.**

- **An agent CLI, MCP server, app backend, app worker or the artifact bundler run through Node keeps its compile cache in the home.**

- **A CLI command that refuses exits 1 and says why on stderr, so a script can tell it did nothing.**
- **A Repair on Settings → Models shows its download where you pressed it, and is checked for free space first.**
- **Backups leave an app's engine behind, and a restore says which apps need theirs installed again.**

- **An installed inbox app is polled: Mail Inbox and Slack's inbox source did nothing at all.**

- **A model provider you add without choosing a model says so, and never answers on a model nobody chose; its Default Model is the model it answers with.**
- **A room's turns count in Settings → Usage, and every usage row names the provider that answered.**
- **Mission Control follows a workflow run as it changes, an approval nobody answered no longer reads "(rejected)", and its Inbox note runs the call again where it was asked and resolves once it is answered.**
- **Bringing your setup over answers in seconds on a months-long history, and imports all of it with progress you can stop.**
- **An app update keeps the engine installed in the app's folder, as it keeps the app's data.**
- **A chat turn is priced by the model that answered it, and a room member's turn falls back down its chain too.**
- **The install scanner reads a language's rule only in that language.**
- **Opening a chat you brought over from another tool no longer logs a 404.**
- **A pasted image the model is shown is not also read into text, and what you see names it as you attached it.**
- **Someone you haven't paired gets "I don't recognize you yet…" once a day, not once per message, on every channel.**
- **A chat channel's card shows its whole status sentence.**
- **A rewind confirmed while a run waits at a gate applies, a cancel closes the run's gates, and a step that parks on a sign-in page asks you.**
- **An app's own page can save over the copy it read, and a refused save says what it refused.**
- **A trigger refused away from the dashboard says where it is allowed, not who may allow it.**
- **Rebinding a model in Settings → Models reaches everything already running, not only new chats.**
- **A model bound in Settings → Models is the model that answers, even when its id has a slash in it.**
- **A first model download checks free space before it starts.**
- **A download refused for disk space says why in a sentence, a download that could not check the disk says so while it runs, and model sizes read in one unit.**
- **Reading an image names the model that reads it, or says no image model is set up.**
- **Tapping a due-task reminder's desktop notification opens Tasks.**

- **The terminal draws its letters in its own font, and every bundled font draws the weights `fonts.css` says it has.**
- **A failed download of the bundled chat model says what to do about it.**
- **A chat turn whose model fails before it replies is answered by the next model in its chain, and says so; a provider's refusal names the real fix.**
- **Every chat channel's trust is set on the Sender trust page, the Channel DM target sends, and a schedule's results reach the channel it names.**
- **A screen reader hears how a chat turn ended: complete, stopped, or with an error.**

- **Two programs saving settings at the same moment no longer undo each other.**

- **Mission Control's Working lane lists every running workflow run, and a call denied without an answer is in the Inbox the next morning.**

- **The chat and an app's Configure page see a failed channel read.**
- **An app installed from the Store shows its update, and Update starts from where it came from.**

- **The dashboard's bundled fonts ship with their licences, and every font, image, binary and fixture in the tree has a recorded source.**

- **On the project pages, Resume resumes, Plan a project plans, and an exported project can be imported again from the Projects page.**

- **The npm packages bundled into the dashboard ship with their licence notices, and Settings → Updates links them.**

- **A trigger's Run workflow action names its workflow, a restart's missed and interrupted runs wait on the Triggers page for your decision, and the HEARTBEAT.md queue is an automation you can see and switch off.**
- **A channel set up in the dashboard knows its owner, and a chat can continue on it.**
- **The answer to a message from a chat channel goes back to that channel.**

- **A failed chat action says so, and leaves the page as it was.**

- **A turn's details keep their telemetry line after a reload, and the gateway's messages say things in words.**

- **The credential guard protects `~/.aws`, `~/.ssh` and the other credential locations when they are symlinks.**
- **The Backups panel names a replace-restore command that works, and so does every other command PersonalClaw tells you to run.**
- **Settings › Security says when Max memory and Max processes don't contain a child.**
- **A model you chose is the model that answers, or PersonalClaw says it is not: an agent's missing model is no longer swapped in silence.**
- **A browse task that stops at a sign-in page says why on its needs-input card.**

- **A default install can call MCP tools, and a server reads "ready" only when an agent can call it.**
- **`personalclaw footprint --reclaim` and the daily reclaim say when compacting made your stores bigger, instead of calling it space freed.**
- **Onboarding's app step says what you can do when it has nothing to list, instead of describing PersonalClaw's source tree.**
- **Bringing your setup over from Claude Code reads the files Claude Code writes, and says what stays behind.**
- **Bringing your setup over from Codex reads the files Codex writes, and keeps what its settings mean.**

- **A skill whose security scan has warnings can be brought over, and you decide with the warnings in front of you.**

- **`personalclaw setup` accepts a named timezone on minimal Linux, and a missing timezone database is reported as a broken install, not as your typo.**

- **A service starts the gateway with the environment you install it from, and never with a secret.**
- **The install kind comes from where the running package lives, never from the directory PersonalClaw was started in.**
- **"Speak replies aloud" reads each finished reply out, and Speak says what is actually missing.**
- **An image reaches a model that takes images as the image itself, and a pasted screenshot attaches.**

- **An app update runs the new version at once, or says a restart is needed, and why.**
- **Gateway startup, the Settings → Providers switch and agent sessions go through the one app load path too.**
- **A proposal's second opinion no longer stops the gateway, and no decision stays hidden in Filtered.**
- **An event trigger fires: a memory write, an inbox message or an app event runs its action once, and the run shows in the trigger's history.**

- **A save from a page that is out of date is refused instead of overwriting a change made elsewhere.**

- **A workflow step whose model is still generating is no longer stopped as stalled, and every step records what its model calls used, the failed ones included.**
- **The chat header fits at every width, and a chat an app started says so under its title.**

- **A secret saved in Settings → Secrets reaches everything that names it, and `personalclaw setup --credential` saves where Settings → Secrets lists it.**

- **"Try it" runs a tool from an external MCP server, stdio or remote, and so does an agent.**

- **An app update installs exactly the Python package versions its new manifest pins, older or newer.**

- **A feature that is switched off says so, with the way to turn it on, and a page that could not load says that instead of waiting forever.**

- **A channel starts and stops receiving the moment you enable, change or remove it.**
- **A failed workflow step says whether a Retry can help, and the run page offers Retry only when it can.**

- **Every model call carries the model it is bound to, the sampling its model accepts, and its output budget.**

- **Settings forms and controls do what they show.**

- **Typing fast in the chat composer no longer trips React's "Maximum update depth exceeded" (#185), and five search boxes with the same defect are fixed with it.**

- **A snapshot carries every file PersonalClaw writes, and the Doctor's health score counts the checks it shows as failed.**

- **A notification for you goes to the first channel that can actually reach you, and to the Inbox when none can.**

- **An app's setup and doctor steps load, a step that cannot run fails the command, a saved app setting takes effect without a restart, each channel keeps its own owner, and "a channel is configured" is the channel's own answer.**

- **The `rich-ingest` template persists what its lenses extract, and a workflow can no longer save a pipe call the engine cannot evaluate.**
- **An unattended loop runs unattended, every loop is listed wherever you look, and Pause stops the worker.**
- **The chat page has no drifting shadow shapes any more, and the glow around the composer fades out smoothly in every state instead of ending in a hard line.**
- **A chat model that validates tool schemas strictly (Gemini, directly or through a router) can chat again, and one tool with a sloppy schema can no longer fail every turn.**
- **If PersonalClaw can't read your name when it opens, it shows a retry instead of first-run setup, so "Skip setup for now" can no longer overwrite your name.**

- **A room keeps showing replies after everyone has spoken, a restart no longer loses a round without a word, and a member whose turn fails says so.**
- **Settings → Providers never freezes the gateway, and every model instance — Ollama included — can be tested, edited and removed.**
- **An approval ends with the work that asked for it, and an approval whose work is gone can no longer run anything.**
- **Mission Control can decide.**
- **An idle tab no longer floods the gateway and the security log.**
- **The attention surfaces agree with each other.**
- **The container image can offer and fetch its default chat model, keeps your workspace on its volume, and says when a project's folder is gone.**
- **In a container, a refused `localhost` names the address your container runtime gives your computer, such as `192.168.5.2` for Finch and Lima, instead of a host name that may not resolve there.**
- **Apps with Python dependencies install on the published Docker image, and keep working after `docker rm` + `docker run`.**
- **Generated chat titles no longer keep the model's label.**
- **A room at its member ceiling no longer offers another agent.**
- **Boot no longer logs three chained tracebacks for a correctly configured embedding model.**
- **`personalclaw chat` can use app-provided models, the bundled default model included.**
- **The network-egress census says `huggingface.co` is fetched, and it now checks what it says.**
- **A warm page load no longer prints a console warning every 8 seconds.**
- **A long answer no longer deletes your question, and a restart keeps everything you saw in a chat.**
- **Background compression no longer rewrites your chats. It shortens what the model reads, and the chat keeps every message.**
- **A tool approval a chat is waiting on now shows up everywhere you would look for it, and can be answered from any of them.**
- **A long message no longer takes the gateway down, and every part of a turn uses the same answer for how big the model's window is.**
- **Updates → Check gives a pip, container or desktop install a result, and the Settings home never says "Up to date" for an install nothing compared.**
- **A version pin must be a release version, and a pin that matches no release says so.**
- **The Settings home's Search tile says what is actually installed.**
- **The Settings home's Chat tile names widget density for what it is.**
- **A failed workflow is blamed on the step that failed, a transient failure offers a Retry that works, and best-of-n's temperatures reach the model.**
- **What a chat turn sends the model now matches your settings: the assistant's name and yours arrive, the prompt bound in Settings → Prompts is the one used, each message is sent once, and no other conversation's messages are included.**
- **A streamed answer renders once and whole: a new chat's first answer is no longer painted twice, and reloading in the middle of an answer no longer cuts off its start.**
- **First-run setup offers the small offline model up front, notices when its download finishes, and says a chat model is ready only when one exists.**
- **"Run setup again" keeps your name and handle when you skip it.**
- **"Escalate on name mention" now escalates a notification that mentions *you*, not one that mentions the assistant.**
- **Knowledge says what actually failed when no model is set up.**
- **A refused task save now appears next to Save, and ticking the missing criterion in the same form fixes it.**
- **The task panel's Status choices fit inside the panel.**
- **A cancelled task no longer shows as done on a project's Work board.**
- **An artifact saved from a project's workspace now appears on that project.**
- **Your default project is now the same on every device.**
- **Triggers: Dry run shows its result, a trigger's notification opens the trigger, one fire makes one notification, and the 900s floor only warns about model calls.**
- **A judged loop now works past its first iteration: a loop body is handed its previous iteration, a stage's output arrives in its declared shape, and each iteration takes its own execution claim.**
- **An escalation no longer blames the iteration ceiling for iterations that never ran, and a tripped breaker is visible.**
- **First-run setup's "All set" recap no longer calls the model-provider app your chat model after a reload.**
- **The loop cockpit names the whole model, so `ollama:qwen2.5vl:7b` no longer reads `7b`.**
- **A reload on first-run setup's "All set" recap no longer says `Chat model — set up later in Settings` for a model that is bound.**
- **An assistant name with an accent, an apostrophe or a non-Latin script is saved as typed, and "Saved" no longer sits beside a name the server did not store.**
- **The workspace picker could create your project folder at the root of the disk.**
- **The Files page no longer goes blank the first time you open a file of a new type.**
- **An empty folder in Files now shows how to fill it.**
- **The README and the guides describe the small default model, and say plainly what it can't do.**
- **New skill works on the first try, and a skill you create there says so.**
- **Your prompts are no longer buried under PersonalClaw's own.**
- **The Learning page no longer logs six errors on every visit when evals are off.**
- **A failed chat turn says what failed and lands where you can see it, and editing an earlier message no longer deletes the turns after it without a trace.**

- **Signing a device out ends its push notifications.**

- **A terminal opened in a sandbox tier opens in that tier every time, or not at all: it never falls back to a shell on this computer.**

- **A durable setup step's arguments stay arguments.**

- **An installed app's panel says what it needs that PersonalClaw doesn't install.**

- **Device sync brings another machine's automations in switched off, without its runs, fire times or alerts.**

- **The run page names each step by its label, as the run's own error line does.**

- **A stopped run's panel offers Retry or the workflow's editor, and no longer says it needs a decision.**

- **What a run recorded shows under the schedule panel's Run button at full strength.**

- **A lookup that fails says so instead of showing an empty list: the Tools page's MCP servers and import list, the network scan for a local model, the routing, learning and pack suggestions, and the skill search.**

- **Another tool's config file that can't be read is named on the onboarding step and in the Tools page's import list, and no longer shows as nothing to import.**

- **The onboarding step names a Claude Code conversation, a prompt history or a Codex session index it can't read, instead of dropping it, and importing that conversation says why it can't come over.**

- **The Routing tab says when your routing table can't be read, and a reorder or an accepted proposal no longer writes over it.**

- **Pack suggestions say when project fingerprinting is off, and name each project whose folder is missing or protected.**

- **The skill search answers every refusal in the shared error shape, and an unusable `limit` is refused instead of failing.**

### Security

- **A prompt sent through an OpenAI-compatible instance on this machine gets the outbound secret scan the setting asks for, since a proxy there can pass it on to a cloud service.**
- **The gateway's internal credential opens only the operations PersonalClaw's own processes call, each one method on one route; it used to open every route under a listed path, every trigger route among them.**
- **An Attended loop asks before its workers act: a call that needs your approval, from any of its workers, the per-task workers' included, waits for your answer on the loop's page, the bell and your approval channel, as a chat's does, and "This loop" lets its workers act without asking until the run ends. An Unattended loop's standing grant ends for every worker when its trust window does.**
- **The agent's file tools, and a file sent to you, refuse a path with a control character in it, as the Files view already did.**
- **What the dashboard shows from models, tools, feeds, pages, files, apps and other people is Markdown only: HTML in it reads as text (plain formatting tags such as `<kbd>` and `<br>` aside), only web and email links open, images load only over https or from the artifact library, and a `<widget>` runs only in a chat reply.**
- **A saved page, an uploaded or watched-folder HTML file and a mirrored document artifact are stored as their words, and feed and page items and document artifacts stored as markup before are converted once.**
- **The dashboard's page policy lets no form post to another site and no WebSocket reach another port on this machine.**
- **The agent's shell refuses PersonalClaw's credential store and keys wherever the home is, a container's included, and the sign-in files of Codex, Claude Code, Gemini CLI, the GitHub and GitLab CLIs and Hugging Face.**
- **The libraries PersonalClaw's model features load report nothing and write nothing outside your PersonalClaw home: onnxruntime starts no telemetry and leaves no device identifier, the Hugging Face library keeps no list of AI tools in your shared Hugging Face folder and sends no usage pings, and the code map's grammars download into the home.**
- **An MCP server's command, arguments and URL show every credential in them masked on the Tools page's edit form and in Settings → Providers, as the Allow question does, and a save keeps or replaces a masked value.**

- **Widgets and react artifacts load nothing from a third party: their Tailwind CSS is compiled by the dashboard and React is written into their own document from the installed packages, so they render offline too, and neither the dashboard nor a deployed artifact allows a CDN to run code or styles. Widgets are told to draw their charts in SVG or on a canvas.**

- **A sync writes nothing outside the stores it syncs: a path another machine names outside them is refused, nothing of that change is taken in, and the sync report names it.**

- **Importing a pack writes nothing outside your PersonalClaw home: a pack whose name or component id would climb out of its folder is refused.**

- **A subagent's report into a scheduled job or an Inbox sweep runs with read tools only, as every turn nobody watches does, and is not handed to an agent CLI.**

- **A webhook's agent turn runs unattended with read tools only, as every turn nobody watches does, and does not run on an agent CLI.**

- **An automation or hook edited on another machine keeps its switch here, and its yes only for what it still runs as it ran here, whether a sync brings the edit or you take the other machine's version of a conflict; a new schedule re-arms its next fire.**

- **A workflow from another machine arrives with every step asking before it acts and without write access, and another machine's edit keeps what you allowed its steps here only on the steps it left as they were.**

- **A runner definition runs its CLI only once you allow what it runs on this machine: one from another machine, or changed since you allowed it, waits for Allow in Settings → Agent defaults.**

- **Sync never brings another machine's agent CLI runtime config: the tools it runs without asking and the servers it starts are this machine's.**

- **A merge restore or an import brings another machine's workflows in with every step asking before it acts, and never its agent CLI runtime config.**

- **Another machine never gives an agent here a tool you kept from it: which tools an agent is kept from is each machine's own.**

- **What you upload is written 0600, in folders only you can open, like every other file PersonalClaw keeps in its home.**

- **Sync's shared registry names none of your records: what each machine last agreed on with another stays on that machine.**

- **Automations, tasks, projects, themes and the other stores PersonalClaw rewrites whole are written 0600, like the configuration, and a crash midway no longer leaves one half-written.**

- **Switching one chat to Trust answers that chat's pending approvals and its own agents', not every other chat's and every background run's.**

- **An automation or hook from another machine arrives switched off, and asks here before it runs, however it arrives: sync, a merge restore or an import.**
- **The email channel no longer answers someone new from your address: their mail waits in your Inbox, where you reply, pair them, or ignore it.**

- **A model server on another machine is scanned like a hosted provider: only one at `localhost`, a loopback address or `0.0.0.0` counts as local.**

- **What a hook or another program PersonalClaw starts prints reaches the gateway log masked, and a hook's output is logged by its length only.**

- **The git PersonalClaw runs gets no gateway secret, and runs no program a repository's own configuration names: no hook, file-system monitor, ssh command, external diff, credential helper or `ext::` remote.**
- **PersonalClaw never starts an agent CLI on its own: a gateway start, the Providers page and `personalclaw doctor` only check that it is installed, and the Test on its card is what starts it.**

- **A Hugging Face download sends only the token PersonalClaw resolved: the library no longer reads `huggingface-cli login`'s token from outside the home by itself.**

- **An agent app's ACP adapter installs from npm only when you install or enable the app, never at a gateway start; a failed install says why on its card, with Retry.**

- **Hugging Face's transfer cache stays in the PersonalClaw home, for the gateway and every process it starts.**

- **A research report from another machine arrives switched off, and sync never brings in another machine's project trust, autonomy grants or integration clients.**
- **The keys that are not sign-ins have a lifetime: an app's proxy secret is new each time its backend starts, the 2FA secret is deleted when 2FA is turned off, and a trigger's question can be answered for a week.**

- **The `embeddings` extra needs sentence-transformers 5.6 or newer, the oldest release checked for a model card that stays local.**

- **The gateway log, the console a service manager keeps and the Logs page mask what they write, and an evaluation keeps what its step printed masked in its artifacts.**

- **`personalclaw service install`, `service status` and `update` show what sudo, systemctl, launchctl or git said masked, and a failure keeps its end, where the reason is.**

- **A text PersonalClaw cannot mask is withheld instead of shown, sent or stored as it came: the local-model health message, run notifications, send-message hooks, the run ledger, crash records, the doctor, the trigger history, skill drafts and proposals, and every log sink.**

- **Markup a web source's sanitizer fails on is withheld instead of stored as the page sent it, and every text PersonalClaw withholds because it could not mask it says so in the same words.**

- **An install that cannot load its HTML sanitizer refuses a page's markup and says why, instead of cleaning it with a weaker pass that kept event handlers and `javascript:` links.**

- **Without the HTML sanitizer, `web_fetch`, `web_extract` and `document_create` fail like any other call, in the refusal's words, instead of raising (an SDK change web-tools and design-critique see through `personalclaw.sdk.net`).**

- **A password that holds an `@`, a `:` or a `/` is masked whole in every detail and log line instead of leaving its tail behind, and the login of an scp-style `user:password@host:path` address is masked too.**

- **The repository publishes no list of names to keep out, in any form, and its publication check reads none.**

- **The agent's commands, its loops and workflows, and the git that fetches an app get no gateway secret, and an app's own children can have the same allowlist through `personalclaw.sdk.util.child_process_env` (an SDK addition piper-tts and skills-sh use).**

- **A workflow step on the native runtime is held to a step's limits, as on an agent CLI.**

- **A tool runs as a read only when it says it only reads, never because of its name.**
- **What an agent's model is handed is masked, and a credential it needs is named, not shown.**

- **The sandbox fences the names of what runs as you, not whichever files are there when the agent's shell starts.**

- **Only you answer an approval, and never the party that asked for it.**
- **"Is YOLO permanent from the config?" is answered from the config.**
- **The chat's approval card and the phone's approval queue no longer show an autonomy rung for a tool call.**

- **An MCP server runs only once you allow what it runs, and an agent cannot add one.**

- **An ACP agent CLI no longer gets the gateway's secrets.**

- **An ACP agent CLI keeps the provider you picked for it: each ACP app passes the variables its CLI reads to choose a provider, a region and a model, and never a credential.**

- **A background chore runs with no tools.**

- **Every file tool stays inside the places the Files view reaches.**

- **A trigger that drives an app route needs your grant unless the route only reads, and a plan asks about each step by what it declares.**

- **The operator ceiling bounds every approval grant: under `"approval": "ask"` nothing runs without a person, whatever an automation, an agent or a switch says.**
- **A read-only run's write tools stay refused while a grant approves its calls.**
- **An approval follows your setting as it is now: a change in Settings reaches the next call, even in a run already going, with no restart.**
- **A tool call's audit row says what was decided, and by whom, in every runtime.**
- **A channel is never handed a key: core masks every text it gives Slack, Telegram, Discord or email, once, before the app sends it.**
- **The tokens your integrations reach PersonalClaw with stop working within 90 days, are listed in Settings → Devices with a revoke, and say why when they stop.**

- **The hourly backup exports your prompt override alone, not every file in your home with it.**
- **What an agent writes no longer runs as you until you allow it: its webhook callbacks, its heartbeat tasks and the agent CLI's hooks.**

- **What PersonalClaw runs for an app no longer gets your credentials.**
- **A file-backed artifact points only at a file PersonalClaw's own surfaces reach, so saving one can no longer overwrite a file anywhere on disk.**
- **Saving something that shows a hidden credential no longer writes `[REDACTED: credential]` over the real value.**
- **An automation's prompt and command are masked on every read, and so is everything else one read showed while another masked it.**
- **A masked tag, memory fact key or lesson names the real one, so removing or editing it acts on it.**
- **A goal loop's source file opens in Files, and a loop's deliverable is read only from inside its folders.**

- **Allowing a trigger allows what it runs now, and only you allow one.**

- **A trigger runs only what it was allowed to run, and a restart never allows anything.**

- **A trigger file from an older version is read once, and what its triggers would run waits for you to allow it.**

- **An app can no longer read your first-run setup or run its network scan.**

- **An app can no longer change your models.**

- **An app can no longer reconfigure another app's provider, and editing a disabled app's instance leaves it off.**

- **Bringing your setup over no longer puts a credential in memory or in a file nothing reads, and both ways in store a server's keys the same way.**

- **The owner token travels in an `Authorization: Bearer` header, so it no longer has to ride a URL.**

- **An app can no longer switch another app on or off through the Settings → Providers routes.**

- **PersonalClaw reads and writes inside its home, and reads anywhere else only where you allow it.**

- **A provider cannot take over another provider's tool.**

- **A registry listing can no longer point the gateway at this computer, a private network or the cloud metadata service.**

- **An app reads only the notifications it raised, and its socket no longer says whether your tool calls run without asking.**

- **An app can no longer read another owner's key by naming it.**

- **Every part of PersonalClaw asks one resolver where your home is, so a rail that refuses your real home refuses it however it is named, and importing PersonalClaw creates and opens nothing.**

- **No model's verdict can hide or hold up a pending approval, or any other decision you owe.**

- **An app can no longer change what you dictate, and packs are uninstalled only by you.**

- **A tool you switch off is off for every agent, and a tool call that failed no longer shows a green check.**

- **A secret reference resolves only against its own owner's credentials.**

- **An installed app can no longer read your conversations, and its socket carries only its own.**
- **An installed app can no longer post into your chats, rooms or runs, or steer them, and a conversation of its own runs under its own grant.**
- **An installed app can no longer rewrite your agents or install skills, and install consent says its code runs as you.**
- **Installing an app never copies files from outside its bundle.**
- **An app installs exactly what was scanned.**
- **An installed app can no longer run a command through the gateway, set up an automation that approves itself, or take your access away.**
- **An installed app can no longer change your security settings, and every write that loosens one asks you first.**
- **The Settings home's YOLO switch now asks before turning auto-approve-everything on, and the server refuses to turn it on without that consent.**
- **API keys and channel tokens are no longer stored in plaintext, world-readable, or copied into backups.**
- **MCP server secrets and the webhook token live in the credential store too, so an export truly carries no credential.**
- **Importing an MCP server from Claude Code keeps it, its values never reach the browser, and a server can be edited and fully removed.**
- **A remote MCP server works: it imports, can be added and edited on the Tools page, and connects with its headers, and the import list shows no credential.**
- **Every app install now shows what the app gets and waits for you — a clean security scan no longer installs in one click.**
- **A gateway running as root can now use its own home directory as a workspace — and its credentials under it stay refused.**
- **The owner token no longer stays in the address bar after it is used.**

## [0.2.0] — 2026-09-23

The first release since 0.1.3 (2026-07-30). The theme is **surfaces that tell you the
truth**: controls that now do what their label says, numbers that admit when they were
never measured rather than showing a confident `0`, and unattended work bounded by
something you can read and audit.

**Run `personalclaw snapshot` before upgrading.** This is a pre-1.0 clean break — state
shapes changed with no automatic migration, several defaults flipped, and some routes now
refuse input they used to accept. The breaking list below is not optional reading.

### Highlights

- **Updates track releases, not `main` — and the update you get is the one you chose.**
- **Unattended work is read-only by default, and bounded by a ceiling you control.**
- **An approval is a brief, not a name and four buttons.**
- **Cost and context stop lying to you.**
- **Your library is searchable by what is inside documents.**
- **Scheduled automations fire when you meant.**
- **Chat craft: Stop stops, and you can branch, plan and rewind.**
- **A fresh install boots with a working chat provider.**
- **Backups you can step through.**
- **Secrets stay secret.**
- **Apps are honest about what they can reach.**
- **The dashboard stops showing an old number and quietly changing it.**
- **Phone, devices and the desktop shell on Linux and macOS.**

### ⚠️ Breaking changes — read before upgrading

- **Updates.**
- **Timed triggers change the hour they fire.**
- **Existing automations now honour the action denylist**
- **Hooks, cron scripts and app backends no longer inherit PersonalClaw's environment.**
- **Python 3.14 is refused at install time**
- **Config fields removed**
- **`inbound` is renamed `external_access`**
- **API breaks.**
- **State shapes changed with no migration.**
- **SDK breaks for app authors.**
- **Chat's Activity → Index tab is gone**

### Added
- **A first-time contributor's three dead ends are closed: a compose service that runs a command the CLI does not have, a Discussions category that does not exist, and a dev setup that fails on the `python3` most machines have.**
- **The `WF_*` workflow error codes now have a registry and a both-directions rail: `workflows/error_codes.py` (`WF_ERROR_CODES`), 162 codes with a meaning each.**
- **"When PersonalClaw is not the right tool (yet)" gains an eleventh scenario: there is no spend cap on a fresh install, and the ceilings that exist never cover the chat window.**
- **Browse can now click a `<canvas>`: an opt-in vision-grounding fallback, using a vision model you pull yourself.**
- **"When PersonalClaw is not the right tool (yet)" — ten situations where a new reader should walk away today, and the two README claims that contradicted the code.**
- **Agent Rooms are now something you can see and use: a Rooms tab, an attributed transcript, the pause card and per-member status.**
- **Agent Rooms take turns deterministically, and a round budget pauses the room to you rather than running on.**
- **Deep research is now a judged, bounded research loop, and a template can say which input carries a loop's task.**
- **A loop kind can now be started as a workflow run: `general` is the first.**
- **Agent Rooms: a shared transcript several bound agents deliberate in.**
- **Agent Rooms: every member now carries its own tool reach, and the human is the only one who can approve a tool call.**
- **The tool-loop breaker's abort ceiling is now tunable: `guardrails.loop_breaker.circuit_threshold`.**
- **A fresh install now ships a working model provider: `ollama-models` is bundled.**
- **`StructuredOutput` is exported from `personalclaw.sdk.model`.**
- **A skill now says HOW it came to exist, not only which tier it lives in.**
- **CSV is a generated document format, and it stores as text rather than as a binary body.**
- **A pack's staged roster could be deployed only by `curl`. `POST /api/packs/{name}/roster/deploy` shipped complete — route, handler, `deploy_roster`, and the `roster` rows already on the `/api/packs/installed` wire — with no control anywhere in the dashboard**
- **Nine config sections were PATCH-editable, backend-read and reachable from NO control in the dashboard. They have controls now — two new Settings panels and five new sections on existing ones.**
- **`knowledge.synthesis_window` and `knowledge.max_mentions_per_claim` now do what they say.**
- **The contradiction judge's typed relations are now stored instead of discarded.**
- **A notification can now say WHO it is for, and one addressed to somebody else is visible here but fired nowhere here.**
- **`sharing_policy: shared` knowledge now actually goes somewhere: the provider contract gains its outbound half.**
- **One install of a knowledge connector can now watch MANY sources: the engine hands `poll` the source row's validated `spec`.**
- **Bring your own vector store: knowledge vector search can run against your own Qdrant (or pgvector, or Chroma) instead of the built-in `sqlite-vec` index.**
- **A Session Map mark now says what the turn DID, and you can tell the map to show fewer of them.**
- **A scanned PDF stops ingesting EMPTY: `ocr` is a new provider type, and a PDF with no text layer is rasterized and read.**
- **A clean run down one branch of an either/or no longer reports as `partial`.**
- **A relevance reranker arm for knowledge retrieval, OFF by default.**
- **The Session Map is reachable: chat now carries an in-session index rail, and on a phone it becomes a tappable drawer.**
- **A mark jump now lands on the turn you clicked.**
- **`personalclaw footprint` reports where your disk went, and the gateway now actually gives it back.**
- **A chat session now has a durable index: `GET /api/chat/sessions/{session}/map`.**
- **The first-run essentials step now offers a zero-key on-ramp for a local Ollama — on this machine and, opt-in, on your network.**
- `personalclaw gateway --seed …` gains **`--seed-local-model`**, which binds a local Ollama provider into the seeded `$PERSONALCLAW_HOME` so a demo home can actually run a turn.
- The dashboard gains a **Desktop live view** widget: the computer-use action feed straight off the audit log (every attempt, allowed or refused), an optional picture-in-picture mirror of the screenshots the model already read, and an optional cursor-motion overlay that draws where a click will land.
- The desktop app now ships for **Linux x86-64**: every release attaches an AppImage and a `.deb`, built and smoke-tested by CI from the release tag.
- The desktop app now ships for **macOS (Apple silicon)**: every release attaches `PersonalClaw-<version>-arm64.dmg`, built and smoke-tested by CI on a macOS runner from the release tag (the smoke executes the dmg's bundled backend, so a bundle that packages but cannot start fails the release).
- **The macOS build is now deterministically ad-hoc signed, and that fact is verified rather than assumed**
- Durable tmux-backed run workers gain their **spawn** half.
- **An MCP server can now ask *you* a question mid-tool-call, through the approval card PersonalClaw already had**
- The Learning page gains a **Lab vs field** panel: one row per subject (bundled template or registered action type) showing its pinned lab score beside its live field record — 👍/👎 rate, edit-before-approve rate, and approval/rejection/undo rates derived from the feedback and earned-autonomy ledgers, computed by query and stored nowhere new.
- **The published HTTP route reference is now generated, and its count is measured rather than asserted**
- **Homebrew and Nix are now real install paths, and a fresh-install validator proves it on a machine that does not already have them.**

### Changed
- **The app SDK now exports every type its own published surface names — 25 of them were unimportable, so apps had to derive or re-declare them.**
- **The SDK surface is now closed under its published *functions* too, not only fields and methods — 22 more types an app could not name, and one collision resolved.**
- **The published docs stop sending readers after internal plan identifiers they cannot resolve.**
- **The Session Map rail is redesigned after Codex's, and LENGTH is now the channel that carries it.**
- **The five Settings switches that relax a security or safety default now confirm before they take effect.**
- **`workflow_start` now validates inputs against the same tree-derived parameter contract shown by `workflow_plan`.**
- **Loop end-state labels now come from the structured `stop_reason`, not free-text `error_message` prose.**
- **66 more spacing values now obey the Density slider (Appearance → Density), in 29 whole files.**
- **A tool's risk level now gates the control that runs it, and "Always for this agent" only promises a saved grant when it will actually save one ([#506](https://github.com/PersonalClaw/PersonalClaw/issues/506), [#541](https://github.com/PersonalClaw/PersonalClaw/issues/541), [#683](https://github.com/PersonalClaw/PersonalClaw/issues/683)).**
- **⚠️ PERSONALCLAW NOW TRACKS RELEASES, NOT `main` — AND THE UPDATE IT APPLIES IS THE ONE YOU CHOSE.**
- **Inbox maintenance no longer runs on its own 6h loop — the remediation engine owns it, like every other store-tidying pass.**
- **A release candidate no longer moves `:latest`, a `-beta` tag is no longer published as a stable release, and the moving `:X.Y` / `:beta` image tags the updater pulls now actually exist.**
- **A container install's update commands now carry the image tag your `updates` channel/pin resolves to, not a bare `latest`.**
- **A pip / pipx / uv wheel install now upgrades to the release your `updates` channel/pin selects, not a blind `releases/latest`.**
- **Unattended auto-update is now OPT-IN and STAGED, and the always-on `auto_update` bool is retired.**
- **The in-app updater no longer tracks raw `main` — a git checkout rides release TAGS by channel, and the destructive `git reset --hard origin/main` is gone from every unattended and dashboard apply path.**
- **The install scanner's terminal tier is no longer shell-only: destruction written in Python is refused, and a bundle that deletes your home directory can no longer install with a clean bill of health.**
- **`run_chat` is no longer exported from `personalclaw.sdk.channel`.**
- **Workflow runs gain a sparse `policy_overrides` overlay: the five per-instance supervisor knobs (`attended`, `autopilot`, `max_cycles`, `idle_secs`, `success_criteria`) can now be persisted per run, composed on top of the template/kind defaults at resolution time.**
- **The install one-liner now verifies something, and stops claiming to verify what it does not ([#2582](https://github.com/PersonalClaw/PersonalClaw/issues/2582)).**
- **The inbox has one definition of "open" and publishes one count.**
- **The `runs` table no longer declares a `task_list_id` column and `WorkflowRun` no longer carries the field.**
- **⚠️ TIMED TRIGGERS WITH NO EXPLICIT TIMEZONE NOW FIRE AT THEIR LOCAL WALL-CLOCK TIME, NOT AT UTC.**
- **The eval report can now tell "not recorded" from "recorded as zero/none"**

### Removed
- **⚠️ Four Settings controls that wrote config keys nobody read are gone, and so are the keys: Ambient surfaces → **Surface layers** (`ambient.surfaces_max_layer`) and **Menu-bar companion** (`ambient.tray_enabled`), Watched sources → **Daily request budget per source** (`sources.daily_request_budget`), and Packs → **Connector catalog URL** (`packs.connector_catalog_url`).**
- **⚠️ The app permission `memory: "app-scoped"` is gone; `memory` is now a boolean grant.**
- **⚠️ Four remaining runtime-editable config paths that governed nothing are gone: `workflows.max_active_runs`, `knowledge.conflict_model_pass`, `knowledge.lint_every_n_persists`, and `learning.min_session_score`.**
- **⚠️ Two earlier runtime-editable config fields that governed nothing are gone: `knowledge.idempotent_persist` and `workflows.max_concurrent_nodes`.**
- **Chat's Activity → **Index** tab is gone; the Session Map is the session's index.**

### Fixed
- **Settings → Search no longer shows a search binding whose provider app was uninstalled as the active provider — and no longer makes it unremovable.**
- **A chat whose finishing frame went missing no longer strands: it now recovers the finished turn from the server instead of claiming "Assistant is responding…" forever.**
- **A small-window chat model refused the FIRST message of a new chat, because the context assembler and the headroom contract disagreed about that model's window by 97.7x.**
- **A context refusal no longer prescribes `/compact` to a chat with no history, and it names the model.**
- **A chat turn that fails FAST no longer puts the composer on Stop at all, and the message you send next is no longer swallowed.**
- **`personalclaw doctor` said `(Python Python 3.13.14)` on a pipx or system install — and the rail that forbids exactly that could not see the branch it happened in.**
- **App installs that declare Python dependencies were refused on every clean install: `packaging` was never a declared dependency of core.**
- **The install-consent dialog no longer freezes on "Install anyway" when a confirmed install fails for a reason the scanner never anticipated.**
- **A failed workflow stage can be retried immediately, instead of being refused by its own no-double-execution claim for fifteen minutes.**
- **A loop body's `{{last.output.summary | default("…")}}` can finally use its own default, and its failure no longer reads "user error".**
- **Every field in an app's Configure form now has an accessible name: 16 of 16 were unnamed.**
- **The published tree no longer cites an internal authority a reader cannot resolve, and six user-facing claims that the code contradicts are now true.**
- **Emptying a stored provider credential field now actually clears it, instead of silently surviving under the new-looking blank form.**
- **The app install-consent dialog now says that installing an app `pip install`s into the interpreter your gateway is running out of, and names the packages.**
- **A workflow run of `stage` nodes now reports what it spent, and its token cap can actually fire.**
- **An app uninstall, a force uninstall, an activate/deactivate and a highlight delete now say when they fail, instead of stopping the spinner and leaving you to guess.**
- **An empty code fence no longer paints the literal word `undefined` into a chat answer.**
- **A `<widget>` shown inside a code fence is now shown, not run — and the fence keeps the line it was about.**
- **A `javascript:` URL with a tab in its scheme no longer survives the HTML sanitizer, and a URL it cannot classify is now rejected instead of kept.**
- **A room's member picker no longer says "Loading…" forever when the agent list cannot be read, and no longer tells a user with no agents that they are all already in the room.**
- **The learning panel's "out of scope" day drew its pass count in a border token at 3.18:1, and the rail that should have caught it only looked at one file.**
- **The default accent was under AA as chip text on every light surface — 3.52:1 on a page every user visits — so `--color-primary`'s light value is retuned across all 12 schemes.**
- **The `neutral` status pill is readable: its text was drawn in a border token at 1.63:1, and the contrast rail that should have caught it measured four of the six tones.**
- **The shell no longer corrects away the two routes it renders itself: `#/companion` — the PWA's own `start_url` — is reachable again, and every deep-link out of first-run setup lands where its button says.**
- **A local-model download now shows real progress from the moment you start it, instead of freezing at `0 MiB` through completion.**
- **Five surfaces now say a load failed instead of spinning forever or showing an empty picker, and the ratchet that guards the contract learned the third way it was being defeated.**
- **Three Settings switches that governed nothing now govern what they promise: Ambient surfaces → **Composable home**, Ambient surfaces → **Generative UI**, and Agent defaults → **Propose fix branches**.**
- **A handled tool failure now reaches the wire AND the audit log as a failure, so a refused destructive operation is no longer recorded as one that ran.**
- **Doctor is clean on a fresh install: the last two "in NO snapshot" paths are now recorded as deliberately not state, with the reasoning beside them.**
- **A misspelled permission in `app.json` is now refused at install by name, instead of vanishing silently from both the manifest and the consent screen.**
- **Adding a model-provider instance now shows on screen that it landed, instead of leaving the section you acted in saying "No remote model providers yet."**
- **`personalclaw doctor` no longer fails a fresh install over ffmpeg, and no longer tells a Linux user to run `brew`.**
- **An unreadable spend ceiling no longer reads as an unlimited one, and the four unattended seams now refuse rather than spend against an unknown.**
- **First run no longer tells you your name is saved before it has been saved.**
- **The audit-outcome rail now sees the outcome words a subsystem names as CONSTANTS, and its raisable ceiling is replaced by a named ledger — so the better practice is no longer the one that evades the rail.**
- **The Doctor no longer tells every pip-installed instance that its dashboard “serves a stale SPA”.**
- **A workflow run that called no model at all is no longer told a free local model ran.**
- **Launching a loop with no model bound now tells you where to fix it, instead of asking you whether you fixed it.**
- **Four labelled cells on a workflow run page rendered a label with nothing after it, on any run that finished in under a second.**
- **"Needs you" now names one set, so the dashboard can no longer say "All clear" while a project shows work waiting on you.**
- **A screen reader no longer says "tab 2 of 6" for the task form's Status field — every single-choice field is now announced as the radio group it is.**
- **The Inbox no longer tells a fresh install its inbox “is not connected yet” while its own banner says the native source is active.**
- **Prompt-cache counts now survive the native runtime, which is why every ledger row read a structural zero.**
- **The home screen’s “Needs you” card now opens the inbox item it names, instead of dropping you on the inbox list.**
- **Two security controls that resolved an unreadable config to their most permissive value now refuse instead.**
- **A first chat no longer shows the assistant's finalized reply twice until reload.**
- **A `config.json` that cannot be parsed no longer re-widens a deliberately narrowed security posture — it now fails CLOSED.**
- **The composer no longer reports itself idle while the previous run is still live, so a message sent in that window is not silently absorbed.**
- **First-run setup no longer tells you no model provider exists when it simply could not read its app sources — and it names a missing `git` instead of blaming the network.**
- **A browse run that got stuck or was refused by the egress policy now explains itself in a sentence instead of printing a reason code.**
- **First-run setup no longer tells you a chat model is ready when chat cannot use it, and when it cannot, it names the actual reason.**
- **First-run setup is navigable: you can see where you are, go back without losing anything, refresh without starting over, and come back later without wiping your name.**
- **A credential typed into a project name no longer reaches the download filename, and download filenames now carry non-ASCII names instead of dropping them.**
- **`personalclaw config --help` no longer advertises a key the command refuses, and a rail now holds every key it advertises to that standard.**
- **`personalclaw config set --file` no longer reports `✅` for a deletion it did not apply, and `config unset` gives removal a path at all.**
- **The packaged desktop app carries its data files, its `sdk.*` submodules and its own `personalclaw-core` MCP server.**
- **"Check for updates" no longer runs `git fetch` inside an app bundle.**
- **The config is validated once per file content instead of once per request, and three retired keys are consumed instead of reported.**
- **Inbox, Apps and Settings reach a terminal state on a first run instead of spinning forever; the onboarding page scrolls; and a structured tool argument no longer kills a subagent from inside the approval path.**
- **Prompt-cache savings are now reported on OpenAI-family models, which had silently reported a flat zero on every cached turn.**
- **A loop template's first iteration no longer dies on its own `{{last.… | default(…)}}` guard.**
- **A binding failure no longer tells you to add the `| default(...)` pipe your expression already has.**
- **A fan-out's `[i/total]` marker reports the item count on both surfaces that render it.**
- **Your local models no longer disappear when you delete one of two endpoints of the same model provider, and a healthy endpoint no longer reports itself as "not available on this machine".**
- **`ERR_MODEL_UNRESOLVED` now states the cause that actually fired instead of asserting one cause for all of them.**
- **A scheduled script can finally read a tool refusal instead of crashing on it.**
- **A content search that hits its deadline inside the worker thread now answers 504 with guidance and records the audit row, instead of letting the exception escape the handler.**
- **A room's export reports when the room was created, not when someone first spoke in it — and a room nobody has spoken in exports a real date instead of an empty one.**
- **A failed model-catalog or file-roots read no longer renders as "you have none".**
- **`LedgerRailsPanel` no longer renders a token FLOOR as a plain number on the same run page where `IntrospectPanel` discloses it.**
- **A never-fired store trigger no longer reads `never run` in the list and ok-green "Firing on its own" one click later.**
- **The context gauge is monotone, in range, and honest about a window nobody declared.**
- **The macOS sandbox wrap no longer resolves its own enforcement binaries through the PATH of the child it is about to confine.**
- **An unreadable `entity_settings/inbox.json` no longer ENABLES retention cleanup and deletes the items you told it to keep.**
- **A workflow loop whose body is an agent step now runs more than one round.**
- **A workflow loop's round cap written as `{{inputs.…}}` was not a cap at all.**
- **`personalclaw doctor` no longer loads `torch` to answer a yes/no question, which is what aborted macOS verification runs inside `faiss`.**
- **The desktop app's dashboard window runs inside the Chromium process sandbox again, and a test can now see whether its bridge actually loaded.**
- **Uninstalling an app while keeping its data, or updating an app, no longer refuses because the app's own background process touched a file.**
- **Citing an issue number inside a block comment no longer fails token-lint as a "raw color hex."**
- **A stray `/*` in code no longer blanks out the lines under it and takes a raw colour hex with it.**
- **A chat on a local model now reports a real context percentage and compacts before it overflows, instead of reading a confident 0% forever.**
- **The paired evals now score against a model that actually resolved, and refuse to score when none does.**
- **A watched page that builds itself with JavaScript now reports content instead of nothing, forever.**
- **Re-ingesting a document now re-embeds only the sections that changed.**
- **A bundled app's CODE now reaches an already-installed home, not just its `app.json`.**
- **`POST /api/tools/invoke` can now run the nine filesystem and shell tools it always listed.**
- **Workflow `rewind` and `run-from` now stop at the same committed-effect boundary as edits, and tool-bearing stages are inside that boundary.**
- **Notification settings no longer advertise production-unowned rows.**
- **Pre-first-pass days no longer render as silent capture failures.**
- **Projects now expose both sides of their archived lifecycle.**
- **Creating or editing a skill now rejects an invalid `SKILL.md` before touching disk.**
- **The Automations week grid now defines both request bounds as explicit instants before comparing or projecting them.**
- **Current macOS releases now use the sandbox capability they actually provide instead of being rejected by version number.**
- **`personalclaw agent list` measures its columns instead of hardcoding them, so the table is aligned on a fresh install.**
- **A credential in a chat title no longer reaches the export download's filename.**
- **Code-loop runnability now distinguishes a missing command binary from a project that has not been scaffolded yet.**
- **A run ledger whose completed steps never recorded token counts no longer reports `tokens: 0`.**
- **The token total a user actually reads now carries the same disclosure as the ledger's own aggregate.**
- **The persistence toggle no longer lights up for a setting the host cannot honour, the durable-workers hint now answers the requirement it names, and "sessions survive a restart" has exactly one owner.**
- **An automation can no longer be EDITED into a state it could never run from, and the doctor names the rows already on disk that are.**
- **`pip install personalclaw` on Python 3.14 is now refused at install time instead of succeeding and handing you a connector-pack parser that refuses every import.**
- **A trigger set to fire faster than the 900s LLM-invoking floor now says so — in the form, on the list, and in the doctor — and a cosmetic edit no longer re-phases its cadence.**
- **Four defects in the workflows engine, each one a documented promise the code did not keep.**
- **Settings → Notifications is a promise the app now keeps, in the five places it was breaking it.**
- **An IPv4 address or a date in your prompt reaches the model as itself, not as `[REDACTED_PHONE]`.**
- **`personalclaw doctor` now reports only what its checks actually established.**
- **The kiro-cli runner id the product advertised could not be bound, and now it can.**
- A memory **entity** can be deleted, and the deletion sticks.
- **The documented `config get > f.json` → edit → `config set --file f.json` loop no longer deletes every provider you have configured.**
- **`personalclaw config get` no longer prints your provider API keys and Slack tokens, and the fix does not delete them instead.**
- **Launching a loop from Plan Review no longer wipes every `kind_config` field that screen doesn't render.**
- **`personalclaw snapshot` now carries your provider API keys, and the durability census stopped counting decisions that had already been made as debt.**
- **`personalclaw config set` no longer deletes every configured model provider.**
- **A tool call's input has one owner, so a persisted chat renders the same as a live one.**
- **The health strip no longer goes coral just because you set a login password.**
- **A scanned document read only as far as the page cap now SAYS so, and the OCR true-type gate covers both OCR backends instead of one.**
- **Settings → Agent defaults no longer shows a "Sandbox" switch that makes no sandbox decision.**
- **A loop's first cycle is credited again: a fast first cycle no longer leaves its SDLC stage un-advanced.**
- **One unreachable git source no longer makes the Store take two minutes to open, or re-pay that cost on every load.**
- **A parallel loop phase no longer loses a task's worktree to a race inside `git worktree add`.**
- **Trigger "Silent" is a switch again, not a one-way latch you can turn on and never off.**
- **A read-only session GET no longer WRITES a session workspace for any id the caller invents.**
- **A brand-new conversation's title, pin, colour, folder, tags and `never_archive` now survive a restart instead of being accepted `200 {"ok": true}` and lost.**
- **An ABSENT field on a session-metadata PATCH is no longer read as a request to CLEAR it.**
- **`{"confirm": "false"}` no longer reads as a YES on a destructive door — one strict predicate replaced two incompatible ones across 24 gates (issue 3000).**
- **`security.egress` no longer accepts a host entry the matcher can never match, so a denylist that reads as blocking a domain family can no longer block nothing (issue 2956).**
- **`GET /api/security/audit?token=…` no longer 400s as an unknown filter, so a query-token client can read the audit trail at all (issue 2927).**
- **`doctor` now names the one bypass-behind-a-proxy combination that silently hands the internet a token-free dashboard.**
- **The forwarded-header contract is true: `trusted_proxies` and `docs/guides/remote-access.md` now name the header the code actually reads.**
- **An app that declares `permissions.api: ["/api/ws"]` no longer gets an owner shell with it (#2964).**
- **A 56 KB `.docx` no longer costs 153 seconds of gateway CPU, and the office parsers finally inherit the zip-bomb posture the rest of the codebase has shipped for years (#2747).**
- **Swapping to a different embedding model of the SAME dimension no longer leaves semantic search silently scoring the old model's vectors, and the re-index can no longer report success over a half-converted library.**
- **`deploy/compose/compose.yaml` is finally true to its own header, and the guide no longer describes the failure backwards.**
- **An app-scoped WebSocket no longer receives events its manifest never declared.**
- **Workflow tools now pass through the shared MCP boundary: their 19 argument schemas are actually enforced (they were defined but never consulted), calls are SEL-logged, and a compiled batch leaf can no longer call workflow_start past the orchestration denial.**
- **A subprocess whose deadline expires is now killed AND reaped, with its process group, at twelve more spawn sites.**
- **A `PERSONALCLAW_AUTH_MODE` the runtime cannot honor is now NAMED at startup and by `doctor`, instead of being downgraded in silence.**
- **The task API's doors now validate what they accept, so a wrong-but-plausible request is refused instead of answered with a quietly wrong result.**
- **Discover's engagement probes now measure the user, not the system: four of the ten auto-hide checks counted machine-produced signals, so tips vanished before they could teach — the automation tip was unreachable on every install (boot registers the notification-digest trigger before the first interaction), the skills tip hid after one plain chat message (passive turn-time injection of bundled skills), the inbox tip hid on the first system-generated proposal (presence, not interaction, on a surface built to receive system items), and the memory tip hid on auto-consolidation rows a user never reviewed.**
- **Content-hashed `/assets/*` Vite bundles no longer defeat the browser cache.**
- **An ingest that leaves nothing you can find no longer reports success.**
- **`personalclaw --help` no longer prints argparse's internal `==SUPPRESS==` sentinel, and the internal command it was meant to hide is now genuinely hidden.**
- **Accepting a learning proposal installs it, or says it cannot.**
- **Creating a loop on a fresh install with no model bound now says so, instead of silently building a bare-defaults plan.**
- **The unattended auto-update no longer silently discards a user's uncommitted tracked-file edits.**
- **A native-Windows gateway no longer `ImportError`s at boot on the POSIX-only `resource` module.**
- **The desktop app now tells the gateway it IS the desktop app, so a packaged install stops offering to `pip install -U` its own frozen backend.**
- **The three surfaces that tell a desktop user how to update stopped promising an updater the shell does not ship.**
- **Every channel's outbound reply now goes to that channel, so a multi-provider install stops losing answers.**
- **⚠️ THREE DESTRUCTIVE ROUTES NOW REQUIRE `confirm: true`, AND TWO UI CONTROLS ASK BEFORE THEY DESTROY.**
- **A chat session's on-disk identity has one owner, so a send can no longer destroy a transcript or resurrect a deleted conversation.**
- **A path containing a NUL byte is refused instead of crashing the request — and the credential guard now fails closed on it.**
- **Five MCP paths ignored `PERSONALCLAW_HOME` and reached into the real home instead — one of them wrote there.**
- The **Tools page no longer badges an installed community bundle `built-in`** — the same word core's own first-party providers get.
- A successful local-source add now **shows up in the Manage Sources panel**.
- **`--port` now reaches the tool subprocesses too.**
- Every listing in the community app registry now carries a real **scan verdict** before you install it.
- **The files surface now derives its credential blocklist from one declaration instead of a hand-copied list, and the PersonalClaw home's `auth/` directory is refused along with it.**
- A provider's **sensitive settings are write-only again on the route the dashboard actually uses**.
- **A keep-data uninstall (`Uninstall`, the middle removal rung) no longer destroys or silently overwrites an earlier copy of the app's `data/` that is still on disk.**
- **The ledger's run totals now say whether a dollar figure was actually measured.**
- A local model downloaded from a `catalog.json` now reports its **on-disk** state, so the truncated-model **Repair** button can finally appear — and finally repairs.
- **A model id that escapes its cache root is refused instead of followed.**
- **The diff view's committed-side read (`GET /api/file-git-original`) no longer leaks a process per timed-out read, no longer answers `HTTP 500` when git cannot be executed, and no longer serves a directory listing as a file's committed content.**
- **An agent write now validates the SHAPE of every field before storing it.**
- **An OpenAI-compatible provider now discovers the models its endpoint actually lists, and says why when it cannot.**
- **Knowledge search no longer hands back the whole library for a term the library happens to talk about — and no longer collapses to a single result when one title happens to match.**
- **Removed**
- **A loop deleted while its cockpit is open now says so instead of rendering the loop forever.**
- A knowledge **intent is now editable and pausable**, which turns three dead controls back on.
- The inbox no longer offers **Approve / Deny** on a workflow run that already ended.
- **A widget's Submit button can no longer fail in silence.**
- Entity **aliases** are now written, so the knowledge graph's deterministic alias pre-pass can actually match one.
- The **Tasks** page now loads every task instead of the server's default first 50, and says so when it cannot.
- A project or task-list **name** is now validated the same way on create and update, and is length-capped.
- The project hub's task-list **count badge** now renders.
- The inbox header and its own filters no longer describe different sets, and **glancing at an item no longer decrements the count**.
- **An app's `icon` can no longer crash the dashboard, and it is now validated at install rather than only at paint.**
- **The Doctor's health score now says what it is excluding, and so does `personalclaw doctor`.**
- A routing mute can now be undone from **Settings → Chat → Agent routing → Muted agents**, and the panel stops promising a control it did not have.
- The **skill-proposal queue can now be emptied, and it stops refilling itself.** The 409-forever half of this cycle was fixed earlier; two links survived and kept proposals at 89% of the open inbox.
- **Set default**
- A lifecycle trigger's **Test** button is now a rehearsal, not a fire: it no longer writes into the trigger's real `run_count`/`last run`/`last status` (“Ran 2× · ok” could previously describe a trigger that had never actually fired — both runs were Test clicks), and the action's payload is tagged the way the event-trigger test path already tags it, so a provider can tell a rehearsal from the real thing.
- The **YOLO mode** toggle (Settings → Agent defaults) now applies immediately instead of at the next gateway start: the config field's only reader was the startup seed, so flipping it changed the file while the running instance kept its previous posture — worst in the OFF direction, where revoking the approval bypass silently did nothing and the UI read `false` while approvals stayed bypassed until restart.
- **A refused inbox-settings write now rolls back instead of keeping the rejected value on screen: Retention accepted `-5`/`0`/`99999`, and even once the failure notification landed, the optimistic merge stood — the field showed the refused value as saved until a reload silently restored the stored one.**
- The app **Configure** dialog now reads its schema's `required` array.
- Config validation now enforces the constraint keywords a manifest declares, through **one** validator instead of two that had drifted.
- **Manually reclassifying an inbox item now marks the verdict as the user's own ("Set by you") instead of keeping the AI's confidence in the overridden verdict, and the classification feedback thumbs hide once no machine judgment is on display — Mark wrong can no longer file training feedback against a classification the model never produced.**
- One-line previews are plain text: inbox rows strip markdown marks from both the painted preview and the announced name (a digest's ** and ## no longer render literally or get read aloud), and the artifact card's clipped excerpt is aria-hidden — a decorative thumbnail no longer walks 600 chars of raw markdown into the accessible tree.
- **The week grid and the server now agree on one projection window: the grid sends its exact local-week end (7 local calendar days = 167h/169h across a DST transition, not a fixed 168h), so a real fire is no longer dropped on spring-forward weeks and no phantom hour is drawn on fall-back weeks; any out-of-window occurrence is disclosed in the caption instead of vanishing.**
- **The Files page keeps unsaved edits across a rename: it now owns the draft cache FileViewer documents, the cache entry moves with the file (no more "Rename and discard" consent — nothing is discarded), a confirmed close purges the draft so a discarded edit cannot resurrect on reopen, and the Code cockpit's two programmatic close paths (workspace switch, worker delete) purge theirs too.**
- The **Speaking speed** slider (Settings → Speech & Transcription) no longer tells every provider the same story: its Fast/Slow ends and "lower is faster" hint were Piper's `--length-scale` semantics, which are exactly backwards for OpenAI-compatible remote voices (the same raw number is the API's multiplier, where higher is faster) — so dragging toward "Fast" made remote speech slower.
- **Editing a prompt or snippet whose body contains a credential-shaped string no longer destroys it.**
- **Acking one notification can no longer permanently delete up to 200 older ones.**
- **A retired built-in app no longer lingers as an undeletable broken card.**
- **An upgraded home no longer ends up with two of a system-owned scheduled job.**
- **Resetting a Repeatable task list now actually returns it to a not-yet-run state.**
- **The tool inspector no longer walks a user into a dead end on a disabled tool: Try it kept offering the full Run → Confirm flow and only failed after arguments were filled in, via the server's 403 refusal.**
- **Artifact tags are now editable where they display: the details rail's static pills become the house chip editor (add on Enter, named remove buttons), giving `PATCH /api/artifacts/{slug}`'s long-accepted `tags` field its first UI writer — previously whatever an agent set was what you had, while the sibling `collection` field got an editor and the list endpoint's tag filter stayed load-bearing for loop cockpits.**
- **Reordering an action plan no longer deletes the completed steps: a locked row is deliberately rendered outside the reorder group so it cannot be dragged, which means Motion's `onReorder` can only describe the DRAGGABLE rows — and the write-back treated that partial array as the whole list, so one drag destroyed every ticked step (six of ten on a half-finished plan) and Save persisted it, under a tooltip promising "A completed step keeps its place".**
- **Picking a personality now actually changes the assistant's voice: the chat send path attaches the active personality's persona theme, wiring the frontend half of a persona-injection path whose backend was fully built but read a field no client ever sent.**
- **An unsaved edit to a markdown memory doc (Preferences / Projects / History) now survives clicking another Studio item.**
- **Install consent now distinguishes "this app asked for nothing" from "nobody has read its manifest yet", on both surfaces that ask you to consent.**
- **Code cockpit findings now attribute to tasks: the finding ingest canonicalizes model-authored stage labels ('1 — Write bell_times.py', 'Stage 2/2 — Verify & QA') against the loop's plan at the one write into the ledger, and the cockpit's matcher gains a normalized fallback for already-recorded labels — a 12-cycle blocked loop's entire reasoning trail (including the cycle that diagnosed the blockage) was invisible behind 'No activity yet' on every task.**
- **The Vocabulary section's promises are now kept on both ends: the composer's mic dictation route was calling the flat transcriber — a function with no bias parameter at all — so the personal lexicon and learned corrections biased only knowledge audio/video ingestion while four copy sites claimed "mic input" too.**
- **The app enable/disable toggle now reads Activate/Deactivate on every surface (card, menus, detail panel) — the detail panel's old "Install" implied a re-download that never happens, since a deactivated app's files stay on disk; "Install" is reserved for real store downloads.**
- **Deleting a single notification now confirms first: the per-row Delete removed the entry from disk on one unconfirmed click with no undo, while Clear all on the same page confirms and every sibling per-row delete in the app gates on the shared confirm dialog.**
- **The Settings → Apps tile now counts what its caption says: it read "0 installed apps" on an instance with 33 installed, because the stat deliberately filters to non-provider apps (providers configure under Settings › Providers) but captioned the filtered count with the unqualified noun.**
- **The agent detail panel now names its trigger bindings instead of printing raw hex ids: bound triggers resolve through the same endpoint the picker built its options from and render as "name · event", matching Skills/Tools (whose stored values are already labels).**
- **The trigger list's lifecycle badge now reads each event's real fire path: globally-fired events (like MemoryWrite) no longer wear "dormant" while actually firing, agent-scoped events with no referencing agent say "no agent references this", "dormant" is reserved for events nothing fires, and the create form discloses agent scoping at the point of choice.**
- **The artifact viewer tells the truth about a version that failed to load — a danger banner with Back to current instead of showing CURRENT content under a "historical vN (read-only)" claim with Revert armed for a nonexistent version — and closing version-compare (or the cockpit's diff) no longer throws Monaco's TextModel-disposed error: both DiffEditor sites detach their models through one shared teardown hook.**
- **An exited terminal pane now retires itself from the run-in-terminal bridge: hasActiveTerminal() no longer reports a dead pane as live, so a queued "Run in terminal" command is no longer claimed and burned against a shell that already showed "Process exited" — it opens a fresh pane instead.**
- **Prompt authoring now reads the same variable grammar the engine renders: inline typed declarations ({{ name::type }}, {{ name::select::[a, b] }}) appear in the undeclared-placeholders strip with their declared type and options, and the add-chip creates exactly the variable row the declaration asks for.**
- A project's linked **Artifacts** row now actually lists its loops' deliverables: `/api/projects/{id}/linked` filtered artifacts on `project_id`, which the loop-deliverable convention never wrote — those artifacts carry a `loop:<id>` tag instead, so the row was permanently empty on exactly the artifact class a project is guaranteed to produce.
- **Dismiss all now records the same per-item dismiss engagement signal for every item it sweeps, so the strongest topic-rejection gesture trains inbox ranking instead of being discarded (one store write per sweep).**
- **Generate draft no longer runs on inbox items that can never be replied to: `can_reply` gated only the Send button, so on a read-only item the model ran, a full reply persisted, the row gained a `draft` badge — and Send stayed disabled.**
- **Quiet hours no longer accepts a zero-length window: `08:00 → 08:00` saved with a Saved ✓ while the delivery gate documents start == end as never matching — quiet hours read as enabled and suppressed nothing (the opposite of the all-day quiet a user plausibly meant).**
- Project guard refusals now speak the UI's own vocabulary: deleting or renaming a protected project says "the built-in project 'Personal' cannot be deleted" instead of "the **default** project…" — a singular article that was returned for two different projects and clashed with the Built-in badge those rows already carry.
- **Artifact list responses no longer serve a fabricated `live_dirty`: the flag is computed per read by the detail path while list rows come off persisted metadata that never stores it — so the same artifact reported `False` in the list and `True` in the detail, and every consumer had learned to distrust the list copy.**
- **The Agents page's group headers now count what the group shows: every group filters its rows through the search box, and the no-match empty states already say so, but each header's `count` read the unfiltered catalog — so searching for something absent rendered `Native | 8` directly above "No matching agents".**
- **Creating an artifact with an unrecognized `kind` is now a 400 naming the allowed set instead of a silent success that stored the artifact as `widget` — the sandboxed-*execution* kind, so a typo like `"markdwon"` or a plausible `"md"` had its content treated as executable widget payload rather than prose, and lost the comment layer without explanation (sandboxed kinds are not commentable).**
- **A reaped trigger run reads as a failure everywhere it renders: the reaper writes `health_status: degraded` plus the reap reason while the run-store row still says `success`, and both the detail panel's Last-run badge and the list's schedule-row dot read the two fields through a bare `last_run_status || last_status` chain — so the one record where the fields disagree rendered a green "ok · 1d ago" two lines above the red "Reaped after 1818s" banner.**
- **Reset everything to defaults**
- **`GET /api/artifacts/{slug}/versions/{n}` now self-describes as the version it carries: `native.get()` read the head metadata and swapped only the content, so v1 and v5 both reported `version: 5` next to different bytes — any caller labeling a version from its own payload mislabeled every historical fetch.**
- Prompt render and preview now accept the same variable-value payload: preview took the map under `values` while render/launch/snippet-render demanded `variables`, and each silently ignored the other's key — so render returned a false `missing required variable` for a variable that **was** supplied (pointing the caller at the template instead of the request), and preview returned `ok: true` with unsubstituted braces.
- **The Week grid's empty state no longer blames cron for an empty week: the hint still said "Only enabled interval schedules are plotted.**
- **Discarding a session skill draft or rejecting a skill proposal that no longer exists now returns 404 — matching the sibling skill-delete in the same module — instead of `200 {ok: false}`.**
- **Routing notes can be cleared again: emptying the field and saving PUT an empty `content` that the backend rejected with 400 `content required` — yet an agent with no note is a supported state everywhere else (a missing metadata file reads as `""`), so a note, once set, could never be removed from the UI. An empty (or whitespace-only) PUT now clears the stored note — the file is removed so the canonical empty representation stays "absent" — and clearing an agent that has no note is an idempotent 200.**
- An existing tool-output projection rule can be edited normally again: the rule editor saved the whole list on **every keystroke** and the follow-up refresh re-seeded the input from server state, so keystrokes typed during the round-trip were silently discarded (measured: one of five survived) — a regex could not be corrected at all except by delete-and-re-add.
- The prompt **Tags** field in the edit form can hold more than one tag again: it was a raw input whose value was re-parsed on every keystroke (`split(',')` → trim → `filter(Boolean)` → `join`), so the comma was eaten the moment it was typed and `red,green` became the single tag `redgreen` — while the placeholder instructed "comma-separated".
- Signing in with a **correct** password from a LAN/tunnel address no longer reports "Wrong username or password." Three layers independently converted a CSRF origin rejection into a credentials error: the origin branch in the auth routes returned `auth_invalid_credentials`, the server-level CSRF middleware answered in plain text (which the login page parsed as `{}`), and the page JS defaulted every unmodelled error to the credentials message.
- The agent edit form's **Name** field no longer silently swallows typing: the lock was enforced by discarding writes inside `onChange`, so the field looked pixel-identical to the editable Description below it — focusable, no disabled/readOnly/aria cue — and threw keystrokes away.
- **The dashboard's Action Center and the HeroPulse "inbox" pill no longer double-count skill proposals: most pending inbox items are proposal *mirrors* (`item_kind: proposal`, `refs.skill_proposal`) of the same proposals the triage queue lists directly with Accept/Reject, so every open proposal rendered twice, "+N more to triage" over-reported ~2×, and the pill badged "31 inbox" for one real message.**
- **`POST /api/artifacts` no longer reports a larger body than it saved: the store persists a text body sliced to `MAX_CONTENT_BYTES` (1 MiB) but `create()` echoed the full un-sliced input in its 201 response, so a >1 MiB save looked successful at full size while a subsequent `GET` returned only the first MiB. `create()` now returns the persisted (capped) body — matching what `update()`/`get()` already return by re-reading from disk — so the create response and the next read agree ([#781](https://github.com/PersonalClaw/PersonalClaw/issues/781)).**
- Setting the backend log level from **Settings → Agent defaults** now takes effect immediately instead of only at the next restart.

- **Artifact history now records exactly the real changes.**
- **Context compaction no longer silently disables itself on providers that report no token usage — the normal local-model configuration, where an OpenAI-compatible endpoint rejects `stream_options` and the usage chunk never arrives.**
- **Task lists now reject a duplicate name within one project, matching how projects reject duplicate project names: `create_task_list` had no uniqueness check, so a project could hold two lists of the same name — including two "General" lists, which made a `project_id`-only task attach to whichever id sorted first, arbitrarily.**

- **The backend `skipped` task status now has a frontend representation: `TaskStatus` and the `STATUSES` vocabulary omitted it, so a skipped task matched no Kanban column (invisible on the board), rendered with the not-started glyph in the list, and sorted among active work.**
- **Saving a session template with `reasoning_effort: "max"` is no longer rejected: the template validator's `_VALID_EFFORTS` allowlist omitted `max`, while the composer offers it, the native runtime accepts it, and the OpenAI adapter maps it to `high` — so the one value that could not be stored as a template default was one every other layer honours.**
- **A tagged local-model id (`family:tag`, the normal Ollama form — `llama3.1:8b`, `qwen2.5:0.5b-instruct-q4_0`) now resolves to its FAMILY's context window instead of the 200k default: `model_context_window` split the id and kept the tail (right for a `Provider:` qualifier, wrong for `family:tag` where the family is the head), so the adaptive memory-injection budget scaled a local model's prompt to a window 1.5–6x larger than it actually has.**
- **A schedule the trigger store kept despite a malformed field (lenient load) now flags "needs attention" on Settings → Triggers instead of listing as a healthy-looking row: the backend already carried the parse errors as `broken`, but the `ScheduleJob` type omitted the field, `scheduleToTrigger` dropped it, and the indicator was gated to `kind === 'store'` — so a schedule that silently could not fire gave the user no signal.**
- **The chat approval wait no longer raises `UnboundLocalError` in its `finally` when the wait is cancelled (gateway shutdown, client disconnect, navigation away, or a CI timeout): `outcome` is now bound before the try, so the cancellation propagates unmasked and the mirrored inbox approval item is resolved instead of left asking for a decision the turn is already tearing down ([#1536](https://github.com/PersonalClaw/PersonalClaw/issues/1536)).**
- **Dashboard failure feedback: Action Center approve/reject/accept/dismiss now report a failed action with the server's own message instead of an empty catch that made a 409 look like a dead button ([#324](https://github.com/PersonalClaw/PersonalClaw/issues/324)); bulk task Complete/Delete reads the per-item outcomes from the 200 body and names refused items instead of reporting success ([#478](https://github.com/PersonalClaw/PersonalClaw/issues/478)); the chat workflow progress card only collapses on a real 404 — transient fetch failures keep the card and offer Try again instead of silently erasing it ([#549](https://github.com/PersonalClaw/PersonalClaw/issues/549)).**
- **Task/project integrity: completing a task now refuses while a BLOCKS prerequisite is still open (the DONE write enforced only the task's own exit criteria, so a kanban drag could complete a task with an unfinished prerequisite, strand a `blocked_reason_kind="auto"` stamp on the done row, and inflate graph completion — [#475](https://github.com/PersonalClaw/PersonalClaw/issues/475)); and deleting a project now cascades its tasks instead of orphaning them pointing at dead task-list ids, unreachable from every scoped view ([#457](https://github.com/PersonalClaw/PersonalClaw/issues/457)).**
- **Task/project integrity, create path: `create_task(status="done", …)` now runs the same completion gates as the update path instead of persisting a done row straight from a caller-supplied status — a task born with `status=done` while an exit criterion is unfinished or a BLOCKS prerequisite is non-terminal is refused (400), closing the create-side backdoor to the `can_mark_complete()==False` DONE row that [#475](https://github.com/PersonalClaw/PersonalClaw/issues/475)'s gate refuses only on update.**
- **Trigger specs are now validated at the door: `POST /api/triggers` and the chat/agent create tool refuse invalid cron expressions, 6/7-field (seconds-cadence) expressions, malformed skip dates, and unregistered action providers instead of persisting enabled rows that can never fire or dispatch; sub-900s cron cadences and skip dates the schedule never fires on are warned about at creation and reported by the trigger doctor ([#483](https://github.com/PersonalClaw/PersonalClaw/issues/483), [#687](https://github.com/PersonalClaw/PersonalClaw/issues/687), [#612](https://github.com/PersonalClaw/PersonalClaw/issues/612), [#560](https://github.com/PersonalClaw/PersonalClaw/issues/560), [#270](https://github.com/PersonalClaw/PersonalClaw/issues/270), [#779](https://github.com/PersonalClaw/PersonalClaw/issues/779)).**
- **The native agent loop now retries ONE pre-stream inference transient per turn (provider 5xx class, timeout) with the taxonomy's correction note and guard-shaped audit rows — a single provider blip no longer kills the chat turn ([#2287](https://github.com/PersonalClaw/PersonalClaw/issues/2287), [#252](https://github.com/PersonalClaw/PersonalClaw/issues/252)).**
- **Regenerate on a failed chat turn now works as a clean Retry of that turn's own user message instead of returning HTTP 400 (first-turn failure) or silently truncating the failed turn's message and replaying the previous question (later failures).**

- **Chat:**
- **Dashboard:**
- **Security (skills marketplace):**
- **Prompts:**
- **Loops:**
- **Security (egress guard):**
- **Three views that show less than they were given now say so, and the numbers they state are the numbers they honour.**
- **The API reference no longer claims to be complete when it is not.**
- **Advancing an onboarding step no longer drops focus on the floor, the step you are on is a real heading, and the step body stops spending a fifth of a phone screen on alignment.**
- **A release can no longer publish a gateway image whose dashboard does not work.**

### Added

- **Proposals can now show whether they would have helped on YOUR work.**

- **Automations can now read a web page.**
- **"Open at login" is now a working switch in Settings, and it agrees with the menu bar.**
- **You can put your own note in your inbox.**

- **Notification rules can now deliver as real OS notifications.**

- **Your phone can wake you when a run is blocked on your approval — and the notification carries nothing but two ids.**
- **A reviewer's findings now get triaged by you before anything touches your code.**

- **Your watched sources now write you a morning digest, without you scheduling anything.**

- **App cards now tell you whether an app is tested, styled like the rest of PersonalClaw, and accessible — and for our own apps, a card that claims it and isn't fails the build.**

- **"Is this template edit actually better?" is now a question you can answer, not settle by taste.**

- **A resumed session no longer redoes yesterday's finished work.**
- **A big skill can no longer take the conversation.**
- **A stale browser tab now says it is stale instead of going blank.**
- **The app stops showing you an old number and then quietly changing it.**
- **Your knowledge library can live as plain markdown files you own, and you can edit them.**
- **A loop now tells you what it cost.**

- **A turn that will not fit says so before it runs, and says what to do about it.**
- **Ask your library a question about structure and get a traversal, not a guess.**
- **Independent lookups in one turn now run at the same time.**
- **Long lists stay fast however long they get.**
- **Pair a phone or a second browser with your gateway over your home network.**
- **Ask for plainer prose without editing a prompt.**
- **Stop actually stops.**
- **Know whether a model will actually run on your machine before you download it.**
- **Put back one file, not your whole configuration.**

- **See every device paired with your gateway, and cut one off.**

- **Plan a task before anything runs, from the chat you are already in.**

- **See everything your agents are doing at a glance.**
- **The desktop app has a live menu-bar item, and quitting it no longer risks the gateway.**
- **Undo a bad edit instead of restoring a backup.**

- **Apps can subscribe to platform events they declare.**

- **Pair a phone or tablet as its own device.**

- **Nudge an artifact's look without spending a message on it.**
- **Point at what is wrong instead of describing it.**
- **A pinned dashboard tile can now keep its own numbers up to date, for free.**

- **Sync through storage you don't trust, and it still can't read your data.**
- **Mistyping your sync passphrase is now a mistake you can take back.**
- **Saved the same article twice? Knowledge will now tell you, and fold the two together.**
- **Hold a thought, press a key, keep your hands where they are.**
- **The design-system tool can list what it has, instead of making the assistant guess.**
- **PersonalClaw can now notice what a project is and suggest a pack for it — and it only ever suggests.**
- **Branch a conversation from any message, and see where a branch came from.**
- **A bad edit is no longer permanent.**

- **Your phone can find this machine on its own now, if you ask it to.**
- **Two ready-made setups you can install in one go — Personal CFO and Health OS.**
- **A pack can bring a team, and only the people you actually hired show up.**
- **Paste a prompt card and turn it into something you can actually use.**
- **Share a setup as one link.**
- **Take your data out — all of it, or just the part you asked for.**
- **You can now edit the memory registers the assistant reads every session, and see who your memories are about.**

- **See what a run actually built, in a browser.**

### Fixed

- **Searching the skill store said "No results" when there was nothing to search.**
- **Selecting many conversations at once could file them under a tag or folder that doesn't exist.**

- **Two knowledge shelves could have the same name, with nothing to tell them apart.**
- **The dashboard file explorer's "Uploads" and "PersonalClaw" roots now follow the active home instead of a hardcoded `~/.personalclaw`.**

- **The audit log's "Rotate" control described a key rotation it never performed.**

- **Regenerating a project's agent-instruction files could destroy your own notes, or write them to the wrong place entirely.**

- **Choosing a project while editing a task now moves the task.**
- **A crash no longer explains itself to you as "Server got itself in trouble."**

- **A list-typed action setting is no longer thrown away without a word.**
- **"Speak replies aloud" did nothing: replies were spoken whether it was on or off.**

- **The "Streaming transcription" toggle is gone, because there is nothing behind it.**

- **Editing an artifact and navigating away lost the edit.**

- **`personalclaw snapshot` left your custom themes behind.**
- **When a model's answer is cut off mid-tool-call, it is now told that, instead of being told it forgot something.**

- **Renaming a file you were editing asked permission after the fact, then stranded the tab.**
- **Workflow controls no longer answer confidently about a run that is not there.**
- **A trigger's skip dates could be set once and never changed.**
- **Starring an inbox item now shows you something.**

- **The Doctor's "backfill missing knowledge embeddings" repair could not repair anything, and said it had.**

- **Two settings saved at the same moment could lose one of them.**
- **Renaming an automation replaced what it does.**

- **A too-long file or folder name reported a server error instead of telling you the name was too long.**

- **When the model was unavailable, knowledge enrichment told you it had found nothing.**
- **Anything named like a path was treated as a credential — which broke a bundled template and stripped native libraries out of a workflow's leaves.**
- **Saying "approve it" stopped working for good once a run had answered its first gate.**

- **Creating a knowledge intent could silently delete one you already had.**

- **Merging a knowledge tag into one nested underneath it made the tag disappear from the tree.**

- **Knowledge search's keyword fallback never returned anything, in any install.**

- **A loop interrupted by a restart could stay stuck "running" forever, with nothing working on it.**

- **Turning a tool off on the Tools page now turns it off everywhere.**

- **The skill-proposal queue could fill up and never empty.**
- **Editing a task can no longer corrupt it.**

- **Scheduled automations now actually run.**

- **Four places where sending the wrong kind of value did something worse than refuse it.**

- **Switching an automation off now survives a restart.**

- **Asking for an automation to be created switched off now creates it switched off.**

- **Restarting PersonalClaw quietly moved a chat onto a different agent.**

- **Restarting PersonalClaw mid-conversation lost what the agent had actually done.**

- **The assistant learned nothing from turns run through an external coding CLI.**
- **A read-only command is no longer called "destructive", and read-only tools no longer wait on you.**
- **The approval card now tells you which tool it is asking about.**
- **Ask mode no longer refuses a read-only `ls`.**
- **The session line said "Session created" on every single turn, and named the wrong runtime.**
- **The assistant learned "never more" as a permanent rule.**
- **A reasoning-effort setting that the coding CLI cannot honor is now refused instead of silently stored.**
- **A reasoning-effort setting no longer quietly lapses partway through long-running work.**
- **A tool blocked by Ask or Plan mode no longer ends the whole conversation.**

- **The context gauge said "0%" on turns that were nearly full.**
- **Typing `/compact` at a coding-CLI agent killed the whole turn.**
- **The sign-in page said "Sign-in failed" no matter what went wrong.**
- **The audit log's "Failed" filter hid most failures.**

- **Setting a chat's working directory with a mistyped field no longer silently unbinds it.**
- **Settings → Prompts named four of its forty-four rows.**

- **A scheduler tick wrote its history into the wrong PersonalClaw home.**
- **An agent CLI could run in your home directory instead of the folder you gave the chat.**
- **A "Pre tool use" hook that blocks nothing now says so.**
- **A chat with no folder set could run its agent CLI wherever the app itself happened to be started.**
- **Tinted "chip" buttons had unreadable labels in six of the twelve colour schemes.**

- **"Unattended runs need a verified adapter" only covered one kind of unattended run.**

- **Settings → Agents could show you a stale runner reading as if it were current.**
- **A provider that rides a CLI subscription could look signed in and still fail.**
- **"Possible duplicates" only looked at your 25 newest items.**
- **An export could carry the same database twice, and one copy was the unsafe one.**

- **Your ready-task list was in no particular order.**

- **Forking a conversation could cut it earlier than the message you clicked.**

- **A "replace everything" restore could run while the app was running.**

- **Buttons inside a generated widget now work everywhere you can see the widget.**
- **Home, the Inbox and Discover now arrive in sequence instead of all at once.**
- **A guided tour of the app, and you can take it again whenever you like.**
- **Empty pages now explain themselves and give you something to press.**
- **The audit log can now answer "what did my agent actually do?" — and tell you which record was tampered with.**
- **Artifacts are now findable from knowledge search.**
- **First run now picks up where you left it, and you can walk out of it at any point.**
- **Moving between pages now crossfades instead of cutting.**
- **The two shipped personalities now arrive with their own motion and their own tone.**
- **You can show the assistant your screen for one message — off until you turn it on.**
- **You can snip a region of your screen into a message, on any platform.**
- **Optional sound cues, off until you turn them on.**
- **First run now ends with three things you can actually do, not a tour.**
- **A knowledge item now has a reading mode, and a passage you highlight in it stays highlighted.**
- **You can now see which local models are eating your RAM, and free one.**
- **A model provider can now run in its own process, so a crash in a native library can no longer take the gateway down with it.**
- **A new install now opens on a short sidebar that grows as you use the app.**
- **You can now open your memory in Obsidian and edit it there.**

- **An empty Triggers page now offers four working starters instead of a blank form.**
- **PersonalClaw can now watch things for you, and new entries land in your library on their own.**
- **App installs now check who published the bundle, not just what's in it.**
- **You can install the phone companion to your home screen.**
- **Memory now decides what to do with a new fact instead of just piling it on.**
- **Memory can record whose claim something is (opt-in).**
- **An optional topology block orients a new session in your memory graph.**
- **Papers now ingest as papers.**
- **An app can now teach PersonalClaw to watch a source it has never heard of — by shipping a parser, not a client.**
- **Watching a site you have a link to now starts with "we already know this one".**

- **A voice is now a thing you own, not a dropdown value.**

- **Proposals are now one thing you approve in one place — and an app can raise one.**
- **An HTML artifact can now be opened as a real page, not just previewed in a card.**
- **You can point PersonalClaw at an outside skill catalog and browse it in the Skills store.**
- **When two machines edit the same thing while offline, nothing is overwritten — you get asked.**
- **Memory now has slots: a handful of small, always-there notes about you, instead of facts the assistant has to go looking for.**

- **PersonalClaw can now try a local model first for background work, and fall back to a cloud model when it can't.**
- **"Check this work" — verification that actually runs, instead of a second opinion from the same voice.**
- **Ask for a few versions and get the best one, with the others one click away.**
- **Apps can now share data with each other, read-only, only when both sides agree.**
- **Evaluation scenarios are now yours to keep, version and extend.**
- **Voice input can now run hands-free, and spoken replies stop talking over you.**
- **Approval memory: teach the assistant what it may do without asking again.**
- **Attention notifications can now ask for a second opinion before interrupting you.**
- **A Companion apps settings section — turn on LAN discovery so phone/desktop clients can find this gateway.**
- **You can now replay a finished workflow run and see exactly where an edit would change it.**
- **A run's introspection now shows how its branches and judges actually decided, across the template's history.**
- **A workflow template can now learn from its own runs — and you stay in control of every change.**
- **Accepting a skill refinement no longer rewrites the skill.**

- **Runs now record what LANDED, not just what they did.**

- **You can now ask which runs of a template went a different way — and get warned when they start going a worse way.**
- **Work that nobody reads now says so.**
- **A second starter home, for looking around before you commit anything.**

### Changed
- **PersonalClaw no longer writes anything into your coding CLI's own config, and an ACP agent app can no longer ask it to.**
- **The agent can no longer edit a file it has not read.**
- **Rewinding a conversation no longer throws the old ending away — and that history is now stored inside your chats.**
- **Finding something in a long conversation now works properly with a keyboard and a screen reader.**
- **The Optimize button now knows who said what, and leaves an already-good prompt alone.**
- **The assistant now needs to see a habit work three times, not twice, before it offers to make it a standing principle.**
- **A step that reads another step's output no longer needs a hand-written ordering — and steps in different branches of a workflow can now feed each other.**
- **Autonomous loops now learn the way workflows do.**
- **A fan-out step that shares a limited resource now waits its turn instead of racing.**

- **A workflow that reads another step's output now refuses to save unless that step is guaranteed to run first.**

- **A workflow step that declares what its output will contain is now checked against the steps that read it.**

- **A loop that keeps working but stops getting anywhere now stalls, even when it insists it is making progress.**

- **A loop's judge no longer runs on the same model as the worker it grades.**

- **The approval prompt now tells you what a tool call can touch, and how far your answer reaches.**

### Security

- **A "read-only" background task could write to your memory, open a webhook, and schedule itself.**

- **Uninstalling or disabling an app did not stop it, and uninstalling briefly gave it more access than it had.**

- **PersonalClaw's own keys were protected in the files area and nowhere else.**

- **A password inside a URL was invisible to every place PersonalClaw redacts secrets.**

- **Adding an app source no longer accepts anything you type.**

- **Three more ways a path could leave the folders PersonalClaw is allowed to touch.**

- **A record id can no longer address a file outside its own store.**

- **An argument a tool call carries can no longer lower that call's risk.**

- **Ways *in* now share one gate instead of each inventing their own.**

- **Inbound settings and tokens moved, and old ones stop working.**

- **A `.env` reached a file checkpoint through a symlink.**

- **A rewind now refuses to write outside the workspace it belongs to.**

- **An app can no longer change the version of a library PersonalClaw itself depends on.**
- **Credentials can now live in your OS keychain, and Doctor tells you where they actually are.**
- **Unattended automations now run read-only by default, and you're asked before one runs scripts in a project folder.**
- **The built-in command denylist now repairs itself.**
- **Settings → Security now shows which denylist is actually protecting you.**
- **Installed apps' backends no longer inherit PersonalClaw's environment.**
- **A scheduled Python script can no longer exhaust PersonalClaw's file descriptors.**
- **The Store now tells you which other apps an app may message.**
- **The Store no longer implies PersonalClaw confines an app's network access.**
- **Your hooks and cron scripts no longer inherit PersonalClaw's environment.**
- **Scheduled, file-watch, webhook and chained automations now honour the action denylist — they never did.**
- **A governance ceiling an operator writes once now bounds every unattended run — and the safety profile it bounds is finally read at all.**
- **An egress "allow-list" now actually restricts.**
- **A watched-source poll now honours your denied hosts on the headless-browser tier too.**
- **An auto-approval grant for a spawned subagent can be refused by the ceiling.**
- **Path rules are matched correctly.**

- **App backends now authenticate inbound requests, closing a direct-to-port bypass.**

### Fixed

- **Generated documents no longer show up in your library as broken images, and a generated PDF finally previews.**
- **The Loops page no longer tells you that you have no loops when it simply could not load them.**
- **A security-audit write that fails is no longer swallowed.**

- **A lesson saved for one project no longer becomes a rule for every project.**

- **`until_dry` workflow loops now end when the work reports no progress, instead of always running to their iteration cap.**
- **Run history no longer says "ran" for automations that did not run.**

### Changed

- **Knowledge search now finds the passage, not just the document — and tells you which passage.**

- **Semantic search on a large library got about twenty times faster.**

- **The library you already have becomes searchable by content, without you doing anything.**
- **Anthropic models now reuse the stable head of a conversation instead of re-reading it every turn.**
- **The Retro Terminal and Claw Arcade personalities now skin the error surfaces too.**
- **The Retro Terminal personality now lays a CRT raster over the whole shell.**

- **A goal loop's judge verdict now shows you what the supervisor checked for itself.**
- **"Reduce motion" now actually stops the springs — and the Bounciness slider reaches everything it claimed to.**

- **Housekeeping now runs when your machine actually needs it, instead of on a fixed clock — and one system does it, not two.**
- **A workflow judge now has to show its work, and a PASS it cannot justify is refused.**

- **A workflow plan now tells you which of its stops survive an unattended run.**
- **A `foreach` with `on_item_error: collect` now has defined behaviour, and it collects.**

- **Security docs now describe what the sandbox actually does — credential-hiding, not confinement.**

- **The desktop app can now tell the dashboard what it is actually allowed to do.**

- **First run now sets you up with a working model instead of pointing at Settings.**
- **Prompt caching is now a switch you can find, in Settings → Models.**

- **You can approve what PersonalClaw is waiting on from your phone.**
- **PersonalClaw can now earn autonomy one action at a time, and lose it instantly.**
- **The autonomy ladder now actually decides whether an automated action runs.**
- **You can now see, grant and take back what each automation may do on its own.**
- **You can share a chat as a read-only artifact — inside your own instance, never on the internet.**
- **A Routing & Efficiency panel in Settings shows which model is efficient for which kind of work.**
- **A Usage panel in Settings shows what you're spending.**
- **The chat header shows what the whole conversation has cost.**
- **The "Turn complete" line now shows what the turn cost.**
- **`personalclaw doctor` now reports your SQLite driver and its capabilities.**
- **Memory-backed answers cite their sources, and say so when memory is empty.**
- **A muted agent can be un-muted from its detail page.**
- **Local models now carry a capability matrix and a runtime/license contract from a declarative catalog.**
- **Mid-run steering now takes effect, and the judge leaves a paper trail.**

### Fixed

- **An automation fired from a background write could be dropped without a trace when the retry it was owed was skipped.**
- **A workflow set to `on_overlap: queue` started a second run alongside the first instead of queueing it.**
- **The Inbox's Mentions and Email filters could never match anything.**
- **`personalclaw update` was a dead end unless you had installed from git.**
- **`personalclaw update` could run `git reset --hard` without anyone agreeing to it.**
- **A detached-HEAD update fetched a branch that does not exist.**
- **Deleting a knowledge item mid-enrichment crashed its background pipeline with a noisy error.**
- **Renaming the built-in Personal or Repeatable project quietly broke your projects.**
- **"Run now" did nothing for almost every automation, while reporting success.**
- **A manual "Run now" left no trace and the "Running…" pill never cleared.**
- **Knowledge ingest reported steps as finished that never ran.**
- **Accepting a "refine an existing skill" proposal always failed with an error.**
- **Importing a memory file that wasn't a JSON object failed with an unhelpful server error.**
- **A request that named no task mode relaxed every chat to full execution.**
- **A project could be pointed at your credential directories.**
- **The dashboard could be tricked into handing over your secrets by changing the case of a filename.**

- **One app could borrow another app's permission to run an agent, and read agent runs that weren't its own.**
- **Discover's "see goal loops" tip opened a blank new-loop form instead of your loops.**
- **Changing your embedding model silently stopped the assistant remembering anything.**
- **A task comment could be signed as anyone, and never taken back.**
- **`personalclaw app new` no longer names your app as its own copyright holder, and every licence file in the tree is now held to the real MIT grant.**

### Security

- **Seven known vulnerabilities were shipping in the dashboard's bundled dependencies, and the usual way of checking said there were none.**

### Fixed

- **First-run setup could require a model provider it offered no way to configure: Ollama's on-ramp is discovery-only, and Ollama is never in the app catalogue because it is already installed.**

## [0.1.3] — 2026-07-30

The **attention-and-access** release. Two themes:

**One place for everything waiting on you.** The inbox stops being a message list and becomes
the single attention surface — a goal loop that needs a decision, a proposed skill, and a tool
approval you walked away from all land there as items you can answer in place, instead of a
toast that scrolls past while the work stays stalled. Delivery becomes a choice per kind of
notification (notify / badge / digest / never) rather than one global severity floor, with a
daily digest for the noisy kinds.

**Reach your own assistant from anywhere.** Sessions now survive a restart (they didn't — every
restart logged you out, and away from home that meant locked out), and an optional password
sign-in with 2FA and device pairing lets a browser anywhere get in. It is off by default and
purely additive: the local token link keeps working and remains the way back in, so a login you
misconfigure cannot lock you out of your own box.

Plus: artifacts get a real library, knowledge gets shelves and a proper tag taxonomy, the agent
navigates code by symbol instead of grepping blind, backups run and verify themselves, and
👍/👎 on AI judgments starts actually teaching.

> **Note (0.x clean break):** model bindings in `active_models.json` now carry
> ordered fallback-chain semantics. Old stores read cleanly (a single binding is a
> one-entry chain); consider `personalclaw snapshot` before upgrading, per the
> pre-1.0 banner.
>
> **Note (0.x clean break):** true rewind adds a `rewound` field to persisted chat
> messages (the retained discarded tail). Old sessions read cleanly (missing field =
> today's behavior — no migration); consider `personalclaw snapshot` before upgrading.
>
> **Note (0.x clean break):** knowledge-item tags move from a JSON column into their own
> tables, and the old column is dropped. Opening your library migrates it in place — the
> upgrade is verified against duplicates, blanks, non-ASCII and malformed values, and
> refuses to drop the column if any tag would be lost. Consider `personalclaw snapshot`
> before upgrading, per the pre-1.0 banner.
>
> **Note (0.x clean break):** the unread badge now counts unresolved **inbox** items instead
> of unacknowledged notifications, so **it resets once on upgrade** — any old unacked toasts
> stop contributing to it. Nothing is lost: the notification list keeps its full history and
> becomes a delivery audit. The badge is more honest afterwards (dismissing a toast no longer
> hides work that is still outstanding, and handling something in the inbox actually clears
> it). Your inbox alert keywords move to notification rules automatically. Consider
> `personalclaw snapshot` before upgrading, per the pre-1.0 banner.

### Added

- **Sign in from outside your home network.**
- **Pair a phone without typing your password into it.**
- **Sessions survive a restart.**
- **Hardening for an internet-exposed instance.**

- **One place for everything waiting on you.**
- **Per-notification-kind delivery rules.**
- **A daily digest.**

- **Memory records who contributed them.**

- **Memory can now offer itself, not just answer when asked.**

- **Take a conversation with you, and stop rebuilding the same chat setup.**

- **Hand an artifact to the agent, or point at one mid-conversation.**
- **Shelves for your knowledge library — including ones that fill themselves.**
- **Clean up a long chat list in one action, and let old chats retire themselves.**
- **On a shared task board, your assistant only works on *your* tasks.**
- **Decks and PDFs too — and anything already saved can become a document.**

- **It can make you a Word document or a spreadsheet you can actually send.**

- **Tags are a real taxonomy now — nest them, rename them, merge them.**

- **Your reading state and favorites are now visible, and filterable.**

- **Curate a whole shelf of saved items in one action.**
- **See what changed between two versions of an artifact.**
- **Backups now happen on their own, and they get checked.**
- **Find any chat by what was said in it.**
- **The agent navigates your code by symbol instead of grepping blind.**
- **Memory now knows what it's *about*.**
- **Your IDE can now actually ask your assistant things.**
- **Point your IDE at your assistant: a read-only MCP endpoint.**
- **Tool groups: the agent loads the tools it needs, not all of them.**
- **The artifacts library: live previews, search, and collections.**
- **Artifacts get their own page.**
- **Artifacts: collections + save-time dedup.**
- **Agent routing: suggest the right specialist, never route silently.**
- **Chat craft: seven chat-surface mechanics.**
- **Background compression keeps long chats fast.**
- **Feedback that actually teaches: 👍/👎 on AI judgments.**
- **Investigate anywhere: chat about any entity with its context pre-loaded.**
- **Model use-cases v2: routing sub-categories + fallback chains.**
- **Every model binding is an ordered fallback chain.**
- **Type-routed tool-output compressors.**
- **Projection rules: three layers + line operations.**
- **Background prose summarizer.**

- **"Investigate in chat" is now on everything worth asking about.**
- **Tool groups are now visible, and they hide what can't work.**

- **Personalities: themes that carry an identity, not just a palette.**

- **A username, so your contributions stay attributable.**

- **Backups you can actually read and verify: `personalclaw backup`.**

### Fixed

- **`personalclaw logout` never actually revoked anything.**

- **Auto-archive skipped the very chats it existed to tidy — and you couldn't see or change the rule.**

- **The "Steer" button never steered.**
- **Knowledge and memory could never embed with a config-defined provider.**
- **Binding a model can no longer fail silently.**
- **Settings and the Store no longer blink to a loading skeleton when you touch anything.**
- **Installing an app and updating PersonalClaw both failed on a `uv` virtualenv.**
- **`personalclaw snapshot` was not backing up everything — and could copy a live database unsafely.**
- **Snapshots of a non-default home no longer land in your real home.**

### Changed

- **Breaking-change policy is now written down, and it distinguishes maintainer from contributor.**

## [0.1.2] — 2026-07-26

The **safety-and-resilience** release: the autonomy guardrails program (kill switch,
spend budgets, denylist, outbound scanning, named safety profiles), the full Platform
Resilience program (Doctor health probes, no-model degraded mode, mid-turn message
policy, confirm-gated fixes + trust simulators + crash capture, and a health-scored
self-maintenance engine), first-party apps in the Store on a plain install, the
legibility surfaces (self-documenting UI kit, Discover, routed project context, offline
agent reference), and a render-smoke gate that closes the v0.1.0 blank-dashboard hole.

### Added

- **One health-scored maintenance engine replaces scattered upkeep.**
- **The Doctor can now fix what it finds, explain what it surfaces, and remember what crashed.**
- **Mid-turn message policy: queue (default) or cancel-and-replace.**
- **No-model degraded mode: the assistant stays useful, and honest, with no model bound.**
- **A Doctor tab now diagnoses every subsystem from one read-only view.**
- **First-party apps now appear in the Store on a plain install.**
- **Every non-interactive model call now passes through one guarded seam.**
- **Unattended spend now has budgets, and outbound prompts are scanned for secrets.**
- **A kill switch, a path/action denylist, and a live-write guard for unattended work.**
- **A Guardrails settings surface, a provider-health view, and named safety profiles.**
- **The animated dot-wave backdrop is now a choosable background style.**
- **PersonalClaw describes its own UI kit, guides you to the parts of itself you haven't tried, and hands external agents a routed project context.**
- **Apps surface their skills and backend routes to the agent (declared, not discovered).**
- **Offline agent reference + `pclaw-api` skill**

### Changed

- **The dashboard's system indicators are now a docked bottom rail.**

## [0.1.1] — 2026-07-22

### Fixed

- **Blank dashboard in v0.1.0 (critical).**
- **`monaco-editor` was never declared as a dependency**

## [0.1.0] — 2026-07-19

### Added

- **App-contributed CLI seams**
- **CI & release engineering**

### Changed

- **Provider-boundary completion (Slack residue retired from core):**
- **LLM SDKs demoted out of core dependencies (`openai`, `anthropic`):**
- **Self-update is now install-kind aware (git · pip · container · desktop):**

### Removed

- **`personalclaw gateway --slack-only`**

### Fixed

- **Release wheel now bundles the SPA when built via `python -m build`.**

### Added

- **Agentic chat**
- **Goal loops**
- **Memory**
- **Knowledge base**
- **Skills**
- **Automation**
- **App platform**
- **Agent runtimes**
- **Model layer**
- **Security**
- **Delivery surfaces**

### Notes

- **Single-user, self-hosted, MIT-licensed.**
- **Requires Python 3.12+; a model-provider API key (or a local Ollama) to start chatting.**

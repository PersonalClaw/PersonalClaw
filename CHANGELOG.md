# Changelog

All notable changes to PersonalClaw are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."

## [Unreleased]

### Added

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

### Changed

- **`note_unknown_sender` loses its unused `silent` argument.**
- **`make build` is the one distribution build, and it proves what it built.**

- **`credentials.json` is gone: the gateway moves what it held into the credential store at its first start.**

- **The Session Map is a map of your messages: one marker for each message you sent, all one length, with colour showing which are on screen.**

### Fixed

- **A check gate that fails ends what follows it, a revise closes the question it answered, every queued edit applies, and four surfaces say what happened.**
- **A workflow's approval gate waits your approval window, a retry a grant ran settles its note, and a lifecycle hook's agent knows its trigger.**
- **An Embedding rebind or clear reaches every memory store at its next use, and memory never compares one embedding model's vectors with another's.**
- **The status chip says "Choose a model" when no model is chosen, not "12 degraded".**
- **The chat list answers at once on a 12,005-chat history, the search index catches up in two minutes instead of five hours, and a search says when it has not looked in every chat.**
- **Only an approval lets what follows an approval gate run, a trigger that stops for you asks you, and every question a run asks is its own.**
- **Signing in on one more device no longer signs another one out without a word; a device that is signed out is told why; and Settings → Devices lists every sign-in.**
- **Nothing signs in for longer than 90 days, and asking for longer says so; and every refusal of a sign-in says why and how to sign in, in the desktop app too.**
- **Installing an app's packages, speaking a reply, the Doctor's speech probe and uninstalling the service no longer leave anything outside PersonalClaw's home.**

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
- **An installed app's panel says what it needs that PersonalClaw doesn't install.**

### Security

- **The operator ceiling bounds every approval grant: under `"approval": "ask"` nothing runs without a person, whatever an automation, an agent or a switch says.**
- **A read-only run's write tools stay refused while a grant approves its calls.**
- **An approval follows your setting as it is now: a change in Settings reaches the next call, even in a run already going, with no restart.**
- **A tool call's audit row says what was decided, and by whom, in every runtime.**
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
- The **YOLO mode** toggle (Settings → Agent defaults) now applies immediately instead of at the next gateway start: the config field's only reader was the startup seed, so flipping it changed the file while the running instance kept its previous posture — worst in the OFF direction, where revoking the approval bypass silently did nothing and the UI read `false` while approvals stayed bypassed until restart.
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


# PersonalClaw Provider Reference

The extension-provider taxonomy (the capability types an app can contribute) and the providers currently registered in this build.

## Provider types

- `action`
- `agent`
- `channel`
- `duty_gate`
- `inbox`
- `knowledge`
- `memory`
- `model`
- `notification`
- `prompt`
- `sandbox`
- `search`
- `skills`
- `sync`
- `task`
- `tool`
- `workflow`

## Registered providers

- **bash-action** — type `action` / `` (enabled); capabilities: execute, blocking
- **create-task-action** — type `action` / `` (enabled); capabilities: execute
- **invoke-agent-action** — type `action` / `` (enabled); capabilities: execute
- **notify-action** — type `action` / `` (enabled); capabilities: execute
- **run-prompt-action** — type `action` / `` (enabled); capabilities: execute
- **run-script-action** — type `action` / `` (enabled); capabilities: execute
- **send-message-action** — type `action` / `` (enabled); capabilities: execute
- **native-agents** — type `agent` / `` (enabled); capabilities: crud, acp
- **filesystem-inbox** — type `inbox` / `` (enabled); capabilities: approvals, inputs
- **native-knowledge** — type `knowledge` / `` (enabled); capabilities: bookmarks, documents, search
- **native-vector-memory** — type `memory` / `` (enabled); capabilities: semantic_search, episodic, preferences
- **native-prompts** — type `prompt` / `` (enabled); capabilities: list, read, write, render
- **native-skills** — type `skills` / `` (enabled); capabilities: crud, triggers, auto_generation
- **native-tasks** — type `task` / `` (enabled); capabilities: crud, comments, labels, dependencies
- **personalclaw-artifacts** — type `tool` / `` (enabled); capabilities: artifacts
- **personalclaw-automation-tools** — type `tool` / `` (enabled); capabilities: automation_management
- **personalclaw-code-map** — type `tool` / `` (enabled); capabilities: code_map
- **personalclaw-inbox-tools** — type `tool` / `` (enabled); capabilities: inbox
- **personalclaw-knowledge-tools** — type `tool` / `` (enabled); capabilities: knowledge
- **personalclaw-memory** — type `tool` / `` (enabled); capabilities: memory
- **personalclaw-project-tools** — type `tool` / `` (enabled); capabilities: projects
- **personalclaw-prompts** — type `tool` / `` (enabled); capabilities: prompts
- **personalclaw-subagents** — type `tool` / `` (enabled); capabilities: subagents
- **personalclaw-tasks-tools** — type `tool` / `` (enabled); capabilities: task
- **personalclaw-tools** — type `tool` / `` (enabled); capabilities: skills, notification, system
- **personalclaw-ui-docs** — type `tool` / `` (enabled); capabilities: ui_docs
- **personalclaw-workflows** — type `tool` / `` (enabled); capabilities: workflows

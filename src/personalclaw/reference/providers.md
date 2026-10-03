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
- `ocr`
- `prompt`
- `sandbox`
- `search`
- `skills`
- `sync`
- `task`
- `tool`
- `trigger`
- `trigger_source`
- `vector_store`
- `workflow`

## Registered providers

- **bash-action** — type `action` / ``; capabilities: execute, blocking
- **browse-action** — type `action` / ``; capabilities: execute
- **create-task-action** — type `action` / ``; capabilities: execute
- **inbox-op-action** — type `action` / ``; capabilities: execute, reverse
- **invoke-agent-action** — type `action` / ``; capabilities: execute
- **notify-action** — type `action` / ``; capabilities: execute
- **run-prompt-action** — type `action` / ``; capabilities: execute
- **run-script-action** — type `action` / ``; capabilities: execute
- **run-workflow-action** — type `action` / ``; capabilities: execute
- **send-message-action** — type `action` / ``; capabilities: execute
- **native-agents** — type `agent` / ``; capabilities: crud, acp
- **filesystem-inbox** — type `inbox` / ``; capabilities: approvals, inputs
- **native-knowledge** — type `knowledge` / ``; capabilities: bookmarks, documents, search
- **native-vector-memory** — type `memory` / ``; capabilities: semantic_search, episodic, preferences
- **bundled-chat** — type `model` / `bundled-chat`; capabilities: chat
- **ollama-models** — type `model` / ``; capabilities: chat, embedding
- **native-prompts** — type `prompt` / ``; capabilities: list, read, write, render
- **native-skills** — type `skills` / ``; capabilities: crud, triggers, auto_generation
- **native-tasks** — type `task` / ``; capabilities: crud, comments, labels, dependencies
- **mcp-tools** — type `tool` / ``; capabilities: tool_execution, tool_discovery
- **personalclaw-artifacts** — type `tool` / ``; capabilities: artifacts
- **personalclaw-automation-tools** — type `tool` / ``; capabilities: automation_management
- **personalclaw-calendar-tools** — type `tool` / ``; capabilities: calendar
- **personalclaw-code-map** — type `tool` / ``; capabilities: code_map
- **personalclaw-computer-use-tools** — type `tool` / ``; capabilities: desktop_automation
- **personalclaw-inbox-tools** — type `tool` / ``; capabilities: inbox
- **personalclaw-knowledge-tools** — type `tool` / ``; capabilities: knowledge
- **personalclaw-memory** — type `tool` / ``; capabilities: memory
- **personalclaw-project-tools** — type `tool` / ``; capabilities: projects
- **personalclaw-prompts** — type `tool` / ``; capabilities: prompts
- **personalclaw-subagents** — type `tool` / ``; capabilities: subagents
- **personalclaw-tasks-tools** — type `tool` / ``; capabilities: task
- **personalclaw-tools** — type `tool` / ``; capabilities: skills, notification, system
- **personalclaw-ui-docs** — type `tool` / ``; capabilities: ui_docs
- **personalclaw-workflows** — type `tool` / ``; capabilities: workflows

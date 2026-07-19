"""Tests for hooks module."""

from unittest.mock import MagicMock

import pytest

from personalclaw.hooks import (
    HOOK_INJECT_CONTEXT,
    HOOK_MODIFY,
    HOOK_PASSTHROUGH,
    HOOK_REPLY,
    TOOL_ALLOW,
    TOOL_AUTO_APPROVE,
    TOOL_DENY,
    AutoReplyHook,
    ContextRule,
    HookManager,
    HooksConfig,
    TransformHook,
    _tool_matches,
    safe_read_file,
)


class TestToolMatches:
    def test_exact(self):
        assert _tool_matches("ReadFile", "ReadFile")
        assert _tool_matches("readfile", "ReadFile")
        assert not _tool_matches("Read", "ReadFile")

    def test_wildcard_all(self):
        assert _tool_matches("*", "anything")

    def test_prefix_wildcard(self):
        assert _tool_matches("my-mcp-server--*", "my-mcp-server--ReadFile")
        assert not _tool_matches("my-mcp-server--*", "other-tool")

    def test_suffix_wildcard(self):
        assert _tool_matches("*_bash", "execute_bash")
        assert not _tool_matches("*_bash", "execute_python")

    def test_contains_wildcard(self):
        assert _tool_matches("*weather*", "my-mcp-server--weatherlookup")
        assert not _tool_matches("*weather*", "my-mcp-server--search")


class TestMessageHooks:
    def test_passthrough(self):
        mgr = HookManager()
        result = mgr.on_message("hello")
        assert result.action == HOOK_PASSTHROUGH

    def test_auto_reply_exact(self):
        cfg = HooksConfig(auto_replies=[AutoReplyHook(pattern="ping", reply="pong", exact=True)])
        mgr = HookManager(cfg)
        assert mgr.on_message("ping").action == HOOK_REPLY
        assert mgr.on_message("ping").text == "pong"
        assert mgr.on_message("not ping").action == HOOK_PASSTHROUGH

    def test_auto_reply_contains(self):
        cfg = HooksConfig(
            auto_replies=[AutoReplyHook(pattern="help", reply="Try /help", exact=False)]
        )
        mgr = HookManager(cfg)
        assert mgr.on_message("I need help please").action == HOOK_REPLY

    def test_transform(self):
        cfg = HooksConfig(transforms=[TransformHook(pattern="deploy", prefix="[DEPLOY MODE]")])
        mgr = HookManager(cfg)
        result = mgr.on_message("deploy my app")
        assert result.action == HOOK_MODIFY
        assert result.text.startswith("[DEPLOY MODE]")
        assert "deploy my app" in result.text

    def test_context_injection(self):
        cfg = HooksConfig(
            context_rules=[
                ContextRule(
                    triggers=["pipeline", "deploy"],
                    context="Use GetDeployStatus for pipeline queries.",
                )
            ]
        )
        mgr = HookManager(cfg)
        result = mgr.on_message("check my pipeline")
        assert result.action == HOOK_INJECT_CONTEXT
        assert "GetDeployStatus" in result.text

        assert mgr.on_message("hello").action == HOOK_PASSTHROUGH

    def test_auto_reply_wins_over_transform(self):
        """First match wins — auto_replies checked before transforms."""
        cfg = HooksConfig(
            auto_replies=[AutoReplyHook(pattern="ping", reply="pong", exact=True)],
            transforms=[TransformHook(pattern="ping", prefix="[X]")],
        )
        mgr = HookManager(cfg)
        assert mgr.on_message("ping").action == HOOK_REPLY


class TestToolHooks:
    def test_allow_by_default(self):
        mgr = HookManager()
        assert mgr.on_tool_call("ReadFile").action == TOOL_ALLOW

    def test_auto_approve(self):
        cfg = HooksConfig(auto_approve_tools=["ReadFile", "my-mcp-server--*"])
        mgr = HookManager(cfg)
        assert mgr.on_tool_call("ReadFile").action == TOOL_AUTO_APPROVE
        assert mgr.on_tool_call("my-mcp-server--Search").action == TOOL_AUTO_APPROVE
        assert mgr.on_tool_call("DeleteFile").action == TOOL_ALLOW

    def test_deny(self):
        cfg = HooksConfig(auto_deny_tools=["DangerousTool"])
        mgr = HookManager(cfg)
        result = mgr.on_tool_call("DangerousTool")
        assert result.action == TOOL_DENY
        assert "blocked" in result.reason.lower()

    def test_deny_overrides_approve(self):
        cfg = HooksConfig(
            auto_approve_tools=["*"],
            auto_deny_tools=["DangerousTool"],
        )
        mgr = HookManager(cfg)
        assert mgr.on_tool_call("DangerousTool").action == TOOL_DENY
        assert mgr.on_tool_call("SafeTool").action == TOOL_AUTO_APPROVE

    def test_running_prefix_stripped_for_approve(self):
        cfg = HooksConfig(auto_approve_tools=["ls *"])
        mgr = HookManager(cfg)
        assert mgr.on_tool_call("Running: ls *").action == TOOL_AUTO_APPROVE

    def test_running_prefix_stripped_for_deny(self):
        cfg = HooksConfig(auto_deny_tools=["rm *"])
        mgr = HookManager(cfg)
        result = mgr.on_tool_call("Running: rm -rf /")
        assert result.action == TOOL_DENY

    def test_reading_prefix_stripped(self):
        cfg = HooksConfig(auto_deny_tools=["*secret*"])
        mgr = HookManager(cfg)
        assert mgr.on_tool_call("Reading secret.key:1-10").action == TOOL_DENY
        assert mgr.on_tool_call("secret.key").action == TOOL_DENY

    def test_no_prefix_unchanged(self):
        cfg = HooksConfig(auto_approve_tools=["ReadFile"])
        mgr = HookManager(cfg)
        assert mgr.on_tool_call("ReadFile").action == TOOL_AUTO_APPROVE

    def test_running_prefix_pattern_auto_approves(self):
        """'Running: *' matches bash tools whose title starts with 'Running: '."""
        cfg = HooksConfig(auto_approve_tools=["Running: *"])
        mgr = HookManager(cfg)
        assert (
            mgr.on_tool_call("Running: export PATH=x && npm run test").action == TOOL_AUTO_APPROVE
        )
        assert mgr.on_tool_call("Running: ls -la").action == TOOL_AUTO_APPROVE
        # MCP tools without prefix should NOT match
        assert mgr.on_tool_call("TrackerCreateIssue").action == TOOL_ALLOW

    def test_reading_prefix_pattern_auto_approves(self):
        """'Reading *' matches file-read tools whose title starts with 'Reading '."""
        cfg = HooksConfig(auto_approve_tools=["Reading *"])
        mgr = HookManager(cfg)
        assert mgr.on_tool_call("Reading /workplace/src/file.py").action == TOOL_AUTO_APPROVE
        assert mgr.on_tool_call("TrackerCreateIssue").action == TOOL_ALLOW

    def test_mixed_prefix_and_name_patterns(self):
        """Both prefix-based and tool-name patterns should work in the same config."""
        cfg = HooksConfig(auto_approve_tools=["Running: *", "Reading *", "*TrackerGetIssue*"])
        mgr = HookManager(cfg)
        assert mgr.on_tool_call("Running: npm run test").action == TOOL_AUTO_APPROVE
        assert mgr.on_tool_call("Reading /tmp/file.txt").action == TOOL_AUTO_APPROVE
        assert mgr.on_tool_call("TrackerGetIssue").action == TOOL_AUTO_APPROVE
        assert mgr.on_tool_call("TrackerCreateIssue").action == TOOL_ALLOW

    def test_deny_matches_original_tool_name(self):
        """Deny must also match against the original (prefixed) tool name."""
        cfg = HooksConfig(
            auto_approve_tools=["Running: *"],
            auto_deny_tools=["Running: rm *"],
        )
        mgr = HookManager(cfg)
        # "Running: rm -rf /" should be DENIED even though "Running: *" would approve
        result = mgr.on_tool_call("Running: rm -rf /")
        assert result.action == TOOL_DENY
        # Non-denied prefixed tools still auto-approve
        assert mgr.on_tool_call("Running: ls -la").action == TOOL_AUTO_APPROVE
        # Plain tool name deny still works via normalized
        assert mgr.on_tool_call("Running: rm foo").action == TOOL_DENY


class TestHooksConfigFromDict:
    def test_empty(self):
        cfg = HooksConfig.from_dict({})
        assert cfg.auto_approve_tools == []
        assert cfg.auto_approve_subagent_spawn is False
        assert cfg.auto_approve_subagent_tools is False
        assert cfg.auto_replies == []

    def test_full(self):
        cfg = HooksConfig.from_dict(
            {
                "auto_approve_tools": ["ReadFile"],
                "auto_deny_tools": ["Danger"],
                "auto_replies": [{"pattern": "ping", "reply": "pong", "exact": True}],
                "transforms": [{"pattern": "deploy", "prefix": "[DEPLOY]"}],
                "auto_approve_subagent_spawn": True,
                "context_rules": [{"triggers": ["pipeline"], "context": "Use pipeline tool."}],
            }
        )
        assert len(cfg.auto_approve_tools) == 1
        assert len(cfg.auto_replies) == 1
        assert cfg.auto_replies[0].exact is True
        assert len(cfg.context_rules) == 1
        assert cfg.auto_approve_subagent_spawn is True
        assert cfg.auto_approve_subagent_tools is False  # independent flag, not inherited

    def test_subagent_tools_independent_of_spawn(self):
        cfg = HooksConfig.from_dict({
            "auto_approve_subagent_spawn": True,
            "auto_approve_subagent_tools": False,
        })
        assert cfg.auto_approve_subagent_spawn is True
        assert cfg.auto_approve_subagent_tools is False

    def test_subagent_tools_explicit_true(self):
        cfg = HooksConfig.from_dict({
            "auto_approve_subagent_spawn": False,
            "auto_approve_subagent_tools": True,
        })
        assert cfg.auto_approve_subagent_spawn is False
        assert cfg.auto_approve_subagent_tools is True

    def test_hook_manager_auto_approve_subagent_tools_property(self):
        from personalclaw.hooks import HookManager
        cfg = HooksConfig.from_dict({"auto_approve_subagent_tools": True})
        mgr = HookManager(cfg)
        assert mgr.auto_approve_subagent_tools is True

    def test_hook_manager_auto_approve_subagent_tools_default(self):
        from personalclaw.hooks import HookManager
        cfg = HooksConfig.from_dict({})
        mgr = HookManager(cfg)
        assert mgr.auto_approve_subagent_tools is False


class TestHookReload:
    def test_reload(self):
        mgr = HookManager()
        assert mgr.on_message("ping").action == HOOK_PASSTHROUGH

        mgr.reload(
            HooksConfig(auto_replies=[AutoReplyHook(pattern="ping", reply="pong", exact=True)])
        )
        assert mgr.on_message("ping").action == HOOK_REPLY


class TestSafeReadFile:
    def test_blocks_sensitive_path(self):
        with pytest.raises(PermissionError, match="sensitive path"):
            safe_read_file("~/.aws/credentials")

    def test_allows_normal_file(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}')
        assert safe_read_file(str(f)) == '{"key": "value"}'

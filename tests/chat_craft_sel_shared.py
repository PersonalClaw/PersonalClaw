"""What the chat-surface audit and its guide both name: each operation the mechanics emit.

Shared by ``tests/test_chat_craft_sel_audit.py``, which checks each name is written by the module
that owns it, and ``tests/rails/test_chat_craft_sel_audit_tree.py``, which checks the chat-surface
guide prints every one, so no rail imports a behaviour test module.
"""

from __future__ import annotations

#: Every operation the nine mechanics emit, mapped to the module that emits it. This is the
#: audit's contract in one place: one distinct name per action, and the name the user-facing guide
#: prints is the name the code actually writes.
EMITTERS = {
    "chat.rewind": "dashboard/chat_regenerate.py",
    "chat.fork_rewound": "dashboard/chat_fork.py",
    "chat.session_fork": "dashboard/chat_fork.py",
    "chat.plan_activate": "dashboard/chat_plan.py",
    "chat.plan_approve": "dashboard/chat_plan.py",
    "dashboard_interrupt": "dashboard/chat_handlers.py",
    "chat_followups": "dashboard/chat_followups.py",
    "upload.file": "dashboard/handlers/files.py",
}

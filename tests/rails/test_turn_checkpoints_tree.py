"""The source-tree checks split from
``tests/test_turn_checkpoints.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations


def test_the_hard_delete_handler_purges_the_checkpoint_tree(tmp_path):
    """The cap is per session, so a tree the delete path forgets is never reclaimed.
    Asserts the CALL SITES, not just that prune_session works in isolation: the handler hands the
    delete to the one way a chat is deleted, which hands its purge to the one owner of forgetting a
    chat, and that owner prunes the store."""
    import ast
    import inspect

    from personalclaw.dashboard import chat_forget, chat_handlers

    def _calls(fn) -> set[str]:
        tree = ast.parse(inspect.getsource(fn).lstrip())
        return {
            n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
        }

    assert "delete_chats" in _calls(chat_handlers.api_chat_session_delete), "the one delete path"
    assert "purge_chat" in _calls(chat_forget.delete_chats), "the delete must purge"
    assert "prune_session" in _calls(
        chat_forget.purge_chat
    ), "session hard-delete must prune the checkpoint store"

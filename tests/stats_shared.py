"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_stats.py``

Its checks:
``tests/rails/test_stats_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

# The counters, and the runtime path that writes each. Grouped so a failure names the subsystem.
_WRITERS = {
    "sessions_created": "inc_session_created",
    "sessions_cleaned": "inc_session_cleaned",
    "subagents_spawned": "inc_subagent_spawned",
    "subagents_completed": "inc_subagent_completed",
    "subagents_failed": "inc_subagent_failed",
    "input_tokens": "inc_input_tokens",
    "output_tokens": "inc_output_tokens",
    "cache_creation_tokens": "inc_cache_creation_tokens",
    "cache_read_tokens": "inc_cache_read_tokens",
    "total_turns": "inc_turns",
    "total_duration_ms": "inc_duration_ms",
}

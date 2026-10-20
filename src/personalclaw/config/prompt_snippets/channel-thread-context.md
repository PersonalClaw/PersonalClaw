[CHANNEL THREAD CONTEXT]
channel_id: {{channel_id}}
thread_ts: {{thread_ts}}
{% if thread_parent_text %}This thread was started by a prior session. Here is what was posted:
{{thread_parent_text}}
If you need more context from this thread, use the channel MCP tool (e.g. batch_get_thread_replies) with the identifiers above.
{% else %}You are responding inside a channel thread. If you need prior conversation context that is not shown above, use the channel MCP tool (e.g. batch_get_thread_replies) with these identifiers.
{% endif %}[END CHANNEL THREAD CONTEXT]
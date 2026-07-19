"""Shared constants used across cli and gateway modules."""

DATA_WARNING = (
    "⚠️  Do not share confidential, sensitive, or regulated data with AI models.\n"
    "   Review your organization's AI usage and data handling policies\n"
    "   before entering sensitive information."
)

CHAT_TURN_TIMEOUT = 600.0

# Top-level logger namespaces used by bundled app backends (they log under
# their OWN root, not ``personalclaw``). Log-level plumbing (CLI boot +
# the runtime /api/logs/level endpoint) applies levels/handlers to each of
# these too, or an app's operational logs would be invisible.
APP_LOGGER_ROOTS = ("slack_runtime",)

"""Whether a model can use tools: the one reader of what its provider declares, and the words for a
model that can't.

A provider says whether the model it serves takes the tools a turn offers
(``ModelProvider.supports_tools``). The declaration is about the MODEL, so a provider may answer it
model by model (an Ollama server says which of its models call tools), and it may change once the
provider learns more (a server that refuses a request for its tools has said its model takes none).

Everything that needs the answer asks it here: PersonalClaw's own agent loop when it starts and
after each call it offered tools on, the fallback walk choosing a model for a turn that carries
tools, the spend guard reading it through for the provider it wraps, and every place a loop, a
workflow step or a subagent is refused before it would run without them
(``providers.provider_bridge.model_without_tools``). A second reading of the attribute somewhere
else is how two surfaces come to disagree about the same model.
"""

from __future__ import annotations

#: The name a model that can't use tools goes by when nothing names it (a provider built outside
#: the resolution seam, with no model of its own): never ``""``, which reads as "it has its tools".
UNNAMED_MODEL = "this model"


def uses_tools(provider: object) -> bool:
    """Whether the model *provider* serves can use tools, as the provider declares it."""
    return bool(getattr(provider, "supports_tools", False))


def tool_less_model(runtime: object) -> str:
    """The model *runtime* runs on without tools (``NativeAgentRuntime.tool_less_model``), or ``""``
    while it has them, and for a runtime that says nothing about it (an agent CLI brings its own).
    Only a name counts: anything else read there is no model."""
    model = getattr(runtime, "tool_less_model", "")
    return model if isinstance(model, str) else ""


def cannot_use_tools(model: str) -> str:
    """The clause every sentence about such a model starts from: “<model>” can't use tools."""
    named = f"“{model}”" if model and model != UNNAMED_MODEL else "This model"
    return f"{named} can't use tools"


def runs_without_tools(model: str) -> str:
    """What a session whose model can't use tools does instead, as the gateway log and a turn say
    it: it answers from what it was told, and can read, write or run nothing."""
    return (
        f"{cannot_use_tools(model)}, so it runs without them: it can't read or write a file, run a "
        "command or look anything up"
    )

"""A workflow tier offers only the use cases its steps run on, and says what a stage runs on.

🔴 The defect (Backlog: "A workflow model tier set to Chat or Code & tools runs on Reasoning,
and stage steps ignore the tier"). Settings › Workflows › Model tiers offered six use cases for
each tier, Chat and Code & tools among them, and saved any text. A step that makes its own model
call (an `infer` step, a judge gate) asks the one-shot call path for its tier's use case, and
that path never runs a call on Chat or Code & tools: they are the axes a person's own turns run
on (`llm_helpers.one_shot_use_case`). So a tier set to either ran on Reasoning, and neither the
page nor the run said so. A `stage` step's agent rides Orchestration (or the model the step
names) whatever its tier, and the page did not say that either.

Now a tier may name only a use case its steps run on (`WORKFLOW_TIER_USE_CASES`): the edit
allowlist refuses the other two, a tier saved as one before reads as its tier's default with a
log line naming it, and the page offers the four and says what a stage step runs on.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from personalclaw.config.edit_spec import ConfigValueError, coerce_edit_value
from personalclaw.config.editable import _EDITABLE_CONFIG
from personalclaw.config.loader import WORKFLOW_TIER_USE_CASES, AppConfig
from personalclaw.llm_helpers import one_shot_use_case
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.engine import dispatch_infer
from personalclaw.workflows.models import Node

TIERS = ("model_tier_reasoning", "model_tier_standard", "model_tier_fast")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def saved(tmp_path):
    """A scratch config.json the loader reads, written with the test's workflows block."""
    path = tmp_path / "config.json"

    def write(workflows: dict) -> AppConfig:
        path.write_text(json.dumps({"workflows": workflows}), encoding="utf-8")
        return AppConfig.load()

    with patch("personalclaw.config.loader.config_path", return_value=path):
        yield write


def test_every_use_case_a_tier_may_name_is_the_one_its_steps_run_on():
    for use_case in WORKFLOW_TIER_USE_CASES:
        assert one_shot_use_case(use_case) == use_case, use_case
    # Why the two are not offered: a step's call never runs on them.
    assert one_shot_use_case("chat") == "reasoning"
    assert one_shot_use_case("code_tools") == "reasoning"


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("use_case", ["chat", "code_tools", "reasoning-x"])
def test_a_tier_cannot_be_saved_as_a_use_case_its_steps_would_not_run_on(tier, use_case):
    """🔴 Before: the allowlist took any text of up to 32 characters."""
    spec = _EDITABLE_CONFIG[f"workflows.{tier}"]
    with pytest.raises(ConfigValueError):
        coerce_edit_value(f"workflows.{tier}", use_case, spec)
    for honoured in WORKFLOW_TIER_USE_CASES:
        assert coerce_edit_value(f"workflows.{tier}", honoured, spec) == honoured


def test_a_tier_saved_as_chat_or_code_tools_reads_as_its_default_and_the_log_names_it(
    saved, caplog
):
    """🔴 Before: the saved value read back as itself, so the page showed Chat while its steps ran
    on Reasoning."""
    with caplog.at_level(logging.WARNING):
        cfg = saved(
            {
                "model_tier_reasoning": "code_tools",
                "model_tier_standard": "chat",
                "model_tier_fast": "loops",
            }
        ).workflows
    assert (cfg.model_tier_reasoning, cfg.model_tier_standard, cfg.model_tier_fast) == (
        "reasoning",
        "orchestration",
        "loops",
    ), "each refused tier reads as its default; an honoured one is kept"
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "workflows.model_tier_standard" in said and "'chat'" in said, said[:600]
    assert "workflows.model_tier_reasoning" in said, said[:600]
    assert "workflows.model_tier_fast" not in said, said[:600]


async def _runs_on(tiers: dict[str, str], tier: str) -> str:
    """The use case a step of `tier`'s model call runs on, under the configured tier map."""
    asked: list[str] = []

    async def completion(prompt, *, use_case="background", output_type=None):
        asked.append(use_case)
        return "ok"

    node = Node.from_dict(
        {"kind": "infer", "id": "say", "config": {"prompt": "Say ok.", "model_tier": tier}}
    )
    await dispatch_infer(node, BindingContext(), tiers=tiers, completion=completion)
    (use_case,) = asked
    return one_shot_use_case(use_case)


@pytest.mark.anyio
@pytest.mark.parametrize("saved_as", ["chat", "code_tools"])
async def test_a_standard_step_runs_on_the_use_case_its_tier_reads_as(saved, saved_as):
    """🔴 As the row's steps found it: Standard set to Chat, and a standard-tier step's call went
    to Reasoning. Now the tier reads as Orchestration, and that is what the call runs on."""
    cfg = saved({"model_tier_standard": saved_as}).workflows
    assert await _runs_on(cfg.model_tiers(), "standard") == cfg.model_tier_standard


@pytest.mark.anyio
@pytest.mark.parametrize("use_case", WORKFLOW_TIER_USE_CASES)
async def test_each_use_case_the_page_offers_is_the_one_a_step_runs_on(saved, use_case):
    cfg = saved({"model_tier_fast": use_case}).workflows
    assert cfg.model_tier_fast == use_case
    assert await _runs_on(cfg.model_tiers(), "fast") == use_case

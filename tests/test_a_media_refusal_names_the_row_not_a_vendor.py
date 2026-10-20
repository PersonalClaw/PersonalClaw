"""The image and video tools, refused for want of a model, say where to choose one — and no vendor.

Core is provider-neutral: the vendors live in removable apps. The two refusals used to end with an
example, "(e.g. an OpenAI gpt-image-1 or a FAL model)" and "(e.g. a FAL Kling or Veo model)", so a
home with neither app installed was told to choose models it had no way to get, by the names of
two vendors. They also named the use case by its slug ('image_gen'), which the Models page never
shows. Each now names the row a user chooses the model in, in the page's own words, read from the
page here so the two cannot drift apart.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MODELS_PAGE = (
    Path(__file__).resolve().parents[1] / "web" / "src" / "pages" / "settings" / "ModelsPanel.tsx"
)


def _row_label(use_case: str) -> str:
    """The label Settings → Models gives *use_case*'s row (``USE_CASE_META``)."""
    source = _MODELS_PAGE.read_text(encoding="utf-8")
    found = re.search(rf"^\s*{use_case}: \{{ label: '([^']+)'", source, re.M)
    assert found, f"ModelsPanel.tsx has no USE_CASE_META row for {use_case}"
    return found.group(1)


@pytest.fixture
def _no_model(tmp_path, monkeypatch):
    """No image or video model is chosen; the artifact store is a scratch one."""
    from personalclaw.artifacts import native
    from personalclaw.artifacts import registry as art_reg
    from personalclaw.image_gen import registry as image_registry
    from personalclaw.video_gen import registry as video_registry

    monkeypatch.setattr(
        art_reg, "get_provider", lambda name="native": native.NativeArtifactProvider(root=tmp_path)
    )
    monkeypatch.setattr(image_registry, "active_image_gen", lambda: None)
    monkeypatch.setattr(video_registry, "active_video_gen", lambda: None)
    monkeypatch.setattr("personalclaw.mcp_artifacts._resolve_session_key", lambda: None)


@pytest.mark.parametrize(
    ("tool", "use_case", "what"),
    [("image_generate", "image_gen", "image"), ("video_generate", "video_gen", "video")],
)
def test_the_refusal_names_the_row_to_choose_a_model_in(_no_model, tool, use_case, what):
    from personalclaw.mcp_artifacts import _call_tool_inner

    out = _call_tool_inner(tool, {"prompt": "a heron by the lake"})

    assert out == (
        f"Error: no {what}-generation model is configured. Choose one under "
        f"{_row_label(use_case)} in Settings → Models."
    )

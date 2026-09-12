"""Prompts (local fallback) and tracing helpers with Langfuse disabled."""

from __future__ import annotations

from pathlib import Path

import pytest

from sc_core.infra import context, tracing
from sc_core.infra.settings import LangfuseCfg, Settings
from sc_core.prompts import Prompt, get_prompt
from sc_core.shared.errors import ConfigurationError


def test_local_prompt_and_compile(tmp_path: Path) -> None:
    (tmp_path / "greet.md").write_text("Hola {{ name }}, orden {{po_name}}.\n", encoding="utf-8")
    prompt = get_prompt("greet", local_dir=tmp_path, cfg=LangfuseCfg(enabled=False))
    assert prompt.source == "local" and prompt.version == "local"
    assert prompt.variables == ["name", "po_name"]
    assert prompt.compile(name="Ana", po_name="P00015") == "Hola Ana, orden P00015."
    assert prompt.compile(name="Ana") == "Hola Ana, orden {{po_name}}."  # unknown left in place


def test_bundled_shared_prompts_exist() -> None:
    cfg = LangfuseCfg(enabled=False)
    for name in ("supplier_tone", "formats", "escalation_policy"):
        assert get_prompt(name, cfg=cfg).text
    assert "{{po_name}}" in get_prompt("formats", cfg=cfg).text


def test_missing_prompt_raises() -> None:
    with pytest.raises(ConfigurationError, match="neither"):
        get_prompt("does_not_exist", cfg=LangfuseCfg(enabled=False))


def test_prompt_dataclass() -> None:
    assert Prompt("n", "x", "1", "langfuse").compile() == "x"


def test_tracing_disabled_is_noop_but_binds_context() -> None:
    tracing.configure_tracing(Settings(_env_file=None, service_name="t", environment="test"))
    with tracing.start_case("case_1", "test-case", input={"a": 1}) as span:
        assert context.case_id.get() == "case_1"
        with tracing.span("step", input={"b": 2}) as step:
            step.update(output={"ok": True})
        with tracing.tool_span("get_po", input={"po": "P00015"}) as tool:
            tool.update(output={"lines": 2})
        span.update(output={"done": True})
    assert context.case_id.get() is None
    assert isinstance(tracing.trace_metadata(), dict)
    tracing.flush()


def test_continue_trace_binds_ids() -> None:
    with tracing.continue_trace(
        "a" * 32, "agent-step", case_id="case_2", parent_observation_id="b" * 16
    ):
        assert context.case_id.get() == "case_2"
        assert context.trace_id.get() == "a" * 32

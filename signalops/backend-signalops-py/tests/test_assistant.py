import json
from types import SimpleNamespace

import pytest

from signalops.assistant import service
from signalops.assistant.service import AssistantAnswer
from signalops.assistant.tools import execute_tool, tool_definitions


def test_tool_schemas_are_strict_and_read_only() -> None:
    definitions = tool_definitions()
    assert {tool["name"] for tool in definitions} == {
        "get_business_analytics",
        "list_recent_signals",
        "get_current_positions",
        "get_strategy_summary",
    }
    for tool in definitions:
        assert tool["strict"] is True
        assert tool["parameters"]["additionalProperties"] is False
        assert set(tool["parameters"]["required"]) == set(tool["parameters"]["properties"])


def test_unknown_tool_is_rejected() -> None:
    with pytest.raises(ValueError, match="不允許的工具"):
        execute_tool(None, "place_order", {})  # type: ignore[arg-type]


def test_deterministic_sse_has_complete_event_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_assistant_answer",
        lambda _question: AssistantAnswer(
            text="唯讀回答",
            citations=[{"id": "event:1", "label": "事件一", "href": "#/signals"}],
            mode="deterministic",
        ),
    )
    monkeypatch.setattr(
        service,
        "settings",
        SimpleNamespace(assistant_mode="off", openai_api_key=None),
    )

    chunks = list(service.assistant_event_stream("最近有哪些訊號？"))

    assert [chunk.splitlines()[0] for chunk in chunks] == [
        "event: meta",
        "event: delta",
        "event: citations",
        "event: done",
    ]
    assert json.loads(chunks[1].split("data: ", 1)[1])["text"] == "唯讀回答"

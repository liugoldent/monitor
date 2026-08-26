from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from signalops.models import SignalEventModel
from signalops.repository import get_business_analytics, get_signal_overview, list_current_positions
from signalops.schemas import SignalEvent


@dataclass(frozen=True)
class ToolExecution:
    data: dict[str, Any] | list[dict[str, Any]]
    citations: list[dict[str, str]]


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "get_business_analytics",
            "description": "取得策略活躍度、曝險比例、反轉率、事件趨勢與資料品質。",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "periods": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 36,
                        "description": "最近幾個有資料的月份。",
                    }
                },
                "required": ["periods"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_recent_signals",
            "description": "查詢最近的匿名化策略事件，可依事件動作篩選。",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": ["string", "null"],
                        "enum": ["enter", "exit", "reverse", None],
                        "description": "事件動作；null 表示全部。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "回傳筆數。",
                    },
                },
                "required": ["action", "limit"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "get_current_positions",
            "description": "取得所有策略目前的多、空或空手持倉。",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "get_strategy_summary",
            "description": "依策略代碼取得事件統計與目前持倉。",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_code": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 80,
                        "description": "完整策略代碼。",
                    }
                },
                "required": ["strategy_code"],
                "additionalProperties": False,
            },
        },
    ]


def _public_signal(row: SignalEventModel) -> dict[str, Any]:
    event = SignalEvent.model_validate(row).model_dump(mode="json")
    event.pop("account_ref", None)
    event.pop("attributes", None)
    return event


def execute_tool(session: Session, name: str, arguments: dict[str, Any]) -> ToolExecution:
    if name == "get_business_analytics":
        periods = max(1, min(int(arguments.get("periods", 12)), 36))
        analytics = get_business_analytics(session, periods=periods)
        return ToolExecution(
            data=analytics.model_dump(mode="json"),
            citations=[
                {
                    "id": "analytics:current",
                    "label": "策略營運分析與資料品質",
                    "href": "#/analytics",
                }
            ],
        )

    if name == "list_recent_signals":
        limit = max(1, min(int(arguments.get("limit", 10)), 20))
        query = select(SignalEventModel)
        action = arguments.get("action")
        if action:
            query = query.where(SignalEventModel.action == action)
        rows = list(
            session.scalars(
                query.order_by(
                    SignalEventModel.occurred_at.desc(), SignalEventModel.id.desc()
                ).limit(limit)
            )
        )
        return ToolExecution(
            data=[_public_signal(row) for row in rows],
            citations=[
                {
                    "id": f"event:{row.id}",
                    "label": f"{row.strategy_name}・{row.action}・{row.occurred_at.isoformat()}",
                    "href": f"#/signals?event={row.id}",
                }
                for row in rows
            ],
        )

    if name == "get_current_positions":
        positions = list_current_positions(session)
        return ToolExecution(
            data=[position.model_dump(mode="json") for position in positions],
            citations=[
                {
                    "id": f"position:{position.strategy_code}",
                    "label": f"{position.strategy_name}目前持倉",
                    "href": "#/overview",
                }
                for position in positions
            ],
        )

    if name == "get_strategy_summary":
        strategy_code = str(arguments["strategy_code"])
        overview = get_signal_overview(session)
        summary = next(
            (item for item in overview.strategies if item.strategy_code == strategy_code),
            None,
        )
        if summary is None:
            return ToolExecution(
                data={"error": f"找不到策略 {strategy_code}"},
                citations=[],
            )
        return ToolExecution(
            data=summary.model_dump(mode="json"),
            citations=[
                {
                    "id": f"strategy:{strategy_code}",
                    "label": f"{summary.strategy_name}策略統計",
                    "href": "#/overview",
                }
            ],
        )

    raise ValueError(f"不允許的工具：{name}")

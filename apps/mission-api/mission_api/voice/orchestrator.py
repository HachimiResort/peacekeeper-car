"""Conversation loop joining Ark Responses with validated robot tools."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .providers import ArkResponsesClient, VoiceProviderError
from .tools import TOOL_DEFINITIONS, RobotToolExecutor

EventSink = Callable[[dict[str, Any]], Awaitable[None]]

SYSTEM_PROMPT = """你是 Peacekeeper 巡护小车上的中文语音助手。回复要自然、简短，通常不超过两句话。
只能通过提供的工具操作小车，绝不声称未执行的动作已经完成。移动仅用于用户明确要求的短距离演示。
听到停止、停车或危险含义时优先调用 stop。观察环境时调用 observe，并根据实际画面回答。"""


class ConversationOrchestrator:
    def __init__(self, ark: ArkResponsesClient, tools: RobotToolExecutor, max_rounds: int = 3):
        self.ark = ark
        self.tools = tools
        self.max_rounds = max_rounds

    async def run_turn(
        self,
        session: AsyncSession,
        robot_id: str,
        transcript: str,
        history: list[dict[str, Any]],
        emit: EventSink,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        input_items = [
            {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
            *history[-12:],
            {"role": "user", "content": [{"type": "input_text", "text": transcript}]},
        ]
        audit: list[dict[str, Any]] = []
        for round_index in range(self.max_rounds + 1):
            response = await self.ark.create(input_items, TOOL_DEFINITIONS)
            output = response.get("output") or []
            calls = [item for item in output if item.get("type") == "function_call"]
            if not calls:
                reply = self._text(output)
                if not reply:
                    raise VoiceProviderError("Ark returned neither text nor tool calls")
                new_history = [
                    *history[-10:],
                    {"role": "user", "content": [{"type": "input_text", "text": transcript}]},
                    {"role": "assistant", "content": [{"type": "output_text", "text": reply}]},
                ]
                return reply[:300], audit, new_history
            if round_index >= self.max_rounds:
                raise VoiceProviderError("Voice tool loop exceeded the configured round limit")
            input_items.extend(output)
            for call in calls:
                name = str(call.get("name") or "")
                arguments = call.get("arguments") or "{}"
                call_id = str(call.get("call_id") or call.get("id") or "")
                await emit({"type": "tool.call", "name": name, "arguments": arguments})
                try:
                    result = await self.tools.execute(session, robot_id, name, arguments)
                    image = result.pop("_image_data_url", None)
                    record = {"name": name, "arguments": self._arguments(arguments), "result": self._redact(result), "ok": True}
                    audit.append(record)
                    await emit({"type": "tool.result", **record})
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result, ensure_ascii=False, default=str),
                    })
                    if image:
                        input_items.append({
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "这是 observe 工具刚拍摄的当前画面，请结合工具检测结果回答。"},
                                {"type": "input_image", "image_url": image},
                            ],
                        })
                except Exception as exc:
                    record = {"name": name, "arguments": self._arguments(arguments), "error": str(exc), "ok": False}
                    audit.append(record)
                    await emit({"type": "tool.result", **record})
                    input_items.append({"type": "function_call_output", "call_id": call_id, "output": json.dumps(record, ensure_ascii=False)})
        raise VoiceProviderError("Voice tool loop exceeded the configured round limit")

    @staticmethod
    def _text(output: list[dict[str, Any]]) -> str:
        parts = []
        for item in output:
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    parts.append(str(content["text"]))
        return "".join(parts).strip()

    @staticmethod
    def _arguments(value: str | dict) -> dict:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            return {"raw": value[:500]}

    @staticmethod
    def _redact(value: Any) -> Any:
        text = json.dumps(value, ensure_ascii=False, default=str)
        return json.loads(text[:8000]) if len(text) <= 8000 else {"truncated": True}

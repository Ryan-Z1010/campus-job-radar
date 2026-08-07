from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Protocol


class LlmClientError(RuntimeError):
    """A safe-to-display LLM transport or response error."""


@dataclass
class LlmResponse:
    data: Dict[str, Any]
    response_id: str = ""
    model: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)


class StructuredLlmClient(Protocol):
    model: str

    def complete(
        self,
        *,
        agent_name: str,
        instructions: str,
        input_data: Mapping[str, Any],
        schema_name: str,
        schema: Mapping[str, Any],
    ) -> LlmResponse:
        ...


class OpenAIResponsesClient:
    """Minimal Responses API client using strict JSON-schema output.

    Keeping the transport in the standard library avoids forcing the optional LLM
    feature on users of the deterministic monitor.
    """

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: int = 60,
        opener: Optional[Callable[..., Any]] = None,
    ):
        if not api_key or not api_key.strip():
            raise ValueError("OPENAI_API_KEY 未配置")
        if not model or not model.strip():
            raise ValueError("OpenAI 模型名称不能为空")
        self._api_key = api_key.strip()
        self.model = model.strip()
        self.timeout = max(1, int(timeout))
        self._opener = opener or urllib.request.urlopen

    def complete(
        self,
        *,
        agent_name: str,
        instructions: str,
        input_data: Mapping[str, Any],
        schema_name: str,
        schema: Mapping[str, Any],
    ) -> LlmResponse:
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": [
                {
                    "role": "user",
                    "content": json.dumps(
                        input_data,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(schema),
                }
            },
            "max_output_tokens": 2000,
            "store": False,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer {}".format(self._api_key),
                "Content-Type": "application/json",
                "User-Agent": "CampusJobRadar/0.1",
                "X-Campus-Job-Radar-Agent": agent_name,
            },
            method="POST",
        )

        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            request_id = ""
            if exc.headers:
                request_id = exc.headers.get("x-request-id", "")
            suffix = "，request_id={}".format(request_id) if request_id else ""
            raise LlmClientError(
                "OpenAI API 返回 HTTP {}{}".format(exc.code, suffix)
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LlmClientError("OpenAI API 连接失败: {}".format(exc)) from exc

        try:
            response_payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LlmClientError("OpenAI API 返回了无法解析的响应") from exc

        if response_payload.get("status") == "incomplete":
            reason = response_payload.get("incomplete_details", {}).get(
                "reason", "unknown"
            )
            raise LlmClientError("OpenAI API 响应不完整: {}".format(reason))

        output_text = self._extract_output_text(response_payload)
        try:
            data = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise LlmClientError("大模型输出未形成有效 JSON") from exc
        if not isinstance(data, dict):
            raise LlmClientError("大模型结构化输出顶层必须是对象")

        usage = response_payload.get("usage", {})
        return LlmResponse(
            data=data,
            response_id=str(response_payload.get("id", "")),
            model=str(response_payload.get("model", self.model)),
            usage=dict(usage) if isinstance(usage, dict) else {},
        )

    @staticmethod
    def _extract_output_text(payload: Mapping[str, Any]) -> str:
        for item in payload.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "refusal":
                    raise LlmClientError(
                        "大模型拒绝处理该岗位: {}".format(
                            content.get("refusal", "未提供原因")
                        )
                    )
                if content.get("type") == "output_text" and isinstance(
                    content.get("text"), str
                ):
                    return content["text"]
        fallback = payload.get("output_text")
        if isinstance(fallback, str) and fallback:
            return fallback
        raise LlmClientError("OpenAI API 响应中没有 output_text")

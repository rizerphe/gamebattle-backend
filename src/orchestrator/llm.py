"""A client for any OpenAI-compatible chat completions endpoint.

Cerebras is the default provider, but nothing here is specific to it: the
base URL, key, and model come from the environment. Without a key there is no
client, and every AI feature switches itself off.

Two request options are not universal: ``reasoning_effort`` and a strict JSON
schema in ``response_format``. A provider that rejects either makes the call
raise ``LlmError``, and callers treat that as "no answer".
"""

import logging
from typing import Any, Literal

import httpx

logger = logging.getLogger("orchestrator.llm")

Effort = Literal["low", "medium", "high"]


class LlmError(Exception):
    """The provider did not give a usable answer."""


class LlmClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str, model: str):
        self._http = http
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self.model = model

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        effort: Effort,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 8192,
        timeout: float = 10.0,
    ) -> str:
        """The assistant's reply text. ``schema`` asks for a strict JSON object."""

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "reasoning_effort": effort,
            "max_completion_tokens": max_tokens,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": schema},
            }
        try:
            response = await self._http.post(
                self._url, headers=self._headers, json=body, timeout=timeout
            )
        except httpx.HTTPError as error:
            raise LlmError(f"request failed: {type(error).__name__}") from error
        if response.status_code >= 400:
            logger.warning("llm answered %s: %s", response.status_code, response.text[:300])
            raise LlmError(f"provider answered {response.status_code}")
        try:
            choice = response.json()["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            logger.warning("llm reply has no choices: %s", response.text[:500])
            raise LlmError("unexpected response shape") from error
        content = choice.get("message", {}).get("content") if isinstance(choice, dict) else None
        if not isinstance(content, str) or not content.strip():
            # Reasoning tokens count towards the limit, so a long think can leave no answer.
            reason = choice.get("finish_reason") if isinstance(choice, dict) else None
            logger.warning("llm gave no answer (finish_reason=%s): %s", reason, response.text[:500])
            raise LlmError(f"no answer (finish_reason={reason})")
        return content.strip()

"""Minimal JSON-mode chat client for the DeepSeek API (no SDK dependency)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMError(RuntimeError):
    """Raised when the API cannot produce a usable answer."""


@dataclass
class ChatResult:
    """One completion plus its token accounting."""

    content: str
    usage: dict[str, int]
    model: str
    elapsed_seconds: float


def _post(url: str, payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def chat_json(
    *,
    api_base: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: int,
    retries: int = 3,
) -> ChatResult:
    """Call the chat-completions endpoint in JSON mode with bounded retries."""
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    started = time.time()
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            data = _post(url, payload, api_key, timeout)
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            return ChatResult(
                content=content,
                usage={
                    "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                    "completion_tokens": int(usage.get("completion_tokens", 0)),
                    "total_tokens": int(usage.get("total_tokens", 0)),
                },
                model=data.get("model", model),
                elapsed_seconds=round(time.time() - started, 1),
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            last_error = LLMError(f"HTTP {exc.code}: {detail}")
            if exc.code in {400, 401, 402, 403, 404, 422}:
                break
            time.sleep(2**attempt)
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            last_error = LLMError(f"{type(exc).__name__}: {exc}")
            time.sleep(2**attempt)
    raise last_error or LLMError("unknown API failure")


def parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object from a completion, tolerating code fences."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("completion is not a JSON object")
    return value

"""Shared building blocks for agents that call an LLM and expect a
structured JSON response back.
"""

import json
import re

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _strip_code_fence(content: str) -> str:
    """Strips a surrounding ``` or ```json ... ``` markdown fence, if present —
    Claude often wraps JSON responses in one despite instructions not to."""
    match = _CODE_FENCE_RE.match(content.strip())
    return match.group(1) if match else content


class InvalidLLMResponseError(Exception):
    """Raised when an LLM response fails to parse into the expected shape —
    empty content, invalid JSON, or missing/invalid fields — as opposed to a
    transient API failure (rate limit, timeout, connection error).
    """

    def __init__(self, reason: str, raw_content: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.raw_content = raw_content


def parse_json_object(content: str | None) -> dict:
    """Strictly parses `content` as a single JSON object (response_format=json_object)."""
    if not content:
        raise InvalidLLMResponseError("empty response content", content)
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise InvalidLLMResponseError(f"invalid JSON ({exc})", content) from exc
    if not isinstance(result, dict):
        raise InvalidLLMResponseError(f"expected a JSON object, got {result!r}", content)
    return result


def parse_leading_json_object(content: str | None) -> dict:
    """Parses the JSON object at the start of `content`, ignoring trailing text —
    for models (e.g. Claude) that may append stray text after the JSON object."""
    if not content:
        raise InvalidLLMResponseError("empty response content", content)
    try:
        result, _ = json.JSONDecoder().raw_decode(_strip_code_fence(content))
    except json.JSONDecodeError as exc:
        raise InvalidLLMResponseError(f"invalid JSON ({exc})", content) from exc
    if not isinstance(result, dict):
        raise InvalidLLMResponseError(f"expected a JSON object, got {result!r}", content)
    return result


def require_keys(result: dict, *keys: str, raw_content: str | None = None) -> None:
    missing = [key for key in keys if key not in result]
    if missing:
        raise InvalidLLMResponseError(
            f"missing required field(s) {missing} in response {result!r}", raw_content
        )


def build_correction_messages(
    messages: list[dict], raw_content: str | None, reason: str, expected_format: str
) -> list[dict]:
    """Appends the previous (invalid) assistant response plus a corrective
    instruction, so a retry fixes the specific problem instead of repeating it."""
    corrected = list(messages)
    if raw_content:
        corrected.append({"role": "assistant", "content": raw_content})
    corrected.append(
        {
            "role": "user",
            "content": (
                f"Your previous response was invalid: {reason}. Return only a valid JSON "
                f"object in the exact format {expected_format}."
            ),
        }
    )
    return corrected

import os

from anthropic import Anthropic

from src.utils.llm_support import (
    InvalidLLMResponseError,
    build_correction_messages,
    parse_leading_json_object,
)
from src.prompt.sql_validator_prompt import SQL_VALIDATOR_SYSTEM_PROMPT
from src.utils.rate_limiter import call_anthropic

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
MAX_TOKENS = 1024

_EXPECTED_FORMAT = (
    '{"verdict": "valid"|"invalid", "accuracy_score": <integer 0-100>, "feedback": "<...>"}'
)


class SqlValidatorAgent:
    """Judges a SqlGeneratorAgent SQL query against the schema context and the
    original user query, and returns structured feedback the generator can use
    to correct its next attempt."""

    def __init__(self, client: Anthropic):
        self._client = client

    def validate(
        self, query: str, context: str, sql: str, correction: str | None = None
    ) -> dict:
        messages = self._build_messages(query, context, sql, correction)

        response = call_anthropic(
            self._client.messages.create,
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SQL_VALIDATOR_SYSTEM_PROMPT,
            messages=messages,
        )

        text = self._extract_text(response)
        result = parse_leading_json_object(text)

        verdict = result.get("verdict", "invalid")
        if verdict not in ("valid", "invalid"):
            verdict = "invalid"

        try:
            score = int(result.get("accuracy_score", 0))
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(100, score))

        return {"verdict": verdict, "score": score, "feedback": result.get("feedback", "")}

    @staticmethod
    def _extract_text(response) -> str:
        for block in response.content:
            if block.type == "text":
                return block.text
        raise InvalidLLMResponseError("response contained no text block", None)

    @staticmethod
    def _build_messages(
        query: str, context: str, sql: str, correction: str | None
    ) -> list[dict]:
        contents = f"Schema Context:\n{context}\n\nUser Query: {query}\n\nGenerated SQL: {sql}"
        messages = [{"role": "user", "content": contents}]
        if correction:
            messages = build_correction_messages(messages, None, correction, _EXPECTED_FORMAT)
        return messages

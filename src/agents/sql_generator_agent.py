import os

from openai import OpenAI

from src.utils.llm_support import build_correction_messages, parse_json_object
from src.utils.rate_limiter import call_openai
from src.prompt.sql_generator_prompt import SQL_GENERATOR_SYSTEM_PROMPT

MODEL = os.getenv("SQL_GENERATOR_MODEL", "gpt-4o-mini")

_EXPECTED_FORMAT = '{"sql": "<the SQL query, or empty string>", "explanation": "<...>"}'


class SqlGeneratorAgent:
    """Generates a SQL query for the user's request using the schema context
    (tables, columns, relations, business rules) built by SchemaLoader."""

    def __init__(self, client: OpenAI):
        self._client = client

    def generate(
        self,
        query: str,
        context: str,
        feedback: str | None = None,
        correction: str | None = None,
    ) -> dict:
        """`feedback` is the validator's critique of a prior attempt. `correction` is a
        separate note used only when this agent's own previous response failed to parse
        as valid JSON. Raises InvalidLLMResponseError on a malformed response."""
        messages = self._build_messages(query, context, feedback, correction)

        response = call_openai(
            self._client.chat.completions.create,
            model=MODEL,
            messages=messages,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        result = parse_json_object(content)
        return {"sql": result.get("sql", ""), "explanation": result.get("explanation", "")}

    def _build_messages(
        self, query: str, context: str, feedback: str | None, correction: str | None
    ) -> list[dict]:
        if feedback:
            user_content = (
                f"Schema Context:\n{context}\n\n"
                f"Query: {query}\n\n"
                f"Previous attempt feedback (fix these issues in the new query):\n{feedback}"
            )
        else:
            user_content = f"Schema Context:\n{context}\n\nQuery: {query}"

        messages = [
            {"role": "system", "content": SQL_GENERATOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        if correction:
            messages = build_correction_messages(messages, None, correction, _EXPECTED_FORMAT)
        return messages

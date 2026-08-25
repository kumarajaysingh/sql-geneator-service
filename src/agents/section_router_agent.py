import os

from dotenv import load_dotenv
from openai import OpenAI

from src.utils.llm_support import (
    InvalidLLMResponseError,
    build_correction_messages,
    parse_json_object,
    require_keys,
)
from src.parser.okf_parser import BundleNavigator
from src.prompt.section_router_prompt import SECTION_ROUTER_SYSTEM_PROMPT
from src.utils.rate_limiter import call_openai

load_dotenv()

MODEL = os.getenv("SQL_GENERATOR_MODEL", "gpt-4o-mini")

_EXPECTED_FORMAT = '{"sections": {"<Section Name>": ["<Title>", ...], ...}}'


def get_bundle_path() -> str:
    """Set OKF_BUNDLE_PATH in .env to point at the OKF knowledge bundle root."""
    path = os.getenv("OKF_BUNDLE_PATH")
    if not path:
        raise RuntimeError("OKF_BUNDLE_PATH is not set. Add it to your .env file.")
    return path


def _is_valid_sections(sections: object) -> bool:
    """True if sections is a dict mapping section name -> list of title strings."""
    return isinstance(sections, dict) and all(
        isinstance(titles, list) and all(isinstance(t, str) for t in titles)
        for titles in sections.values()
    )


class SectionRouterAgent:
    """Routes a user query to the relevant OKF bundle section(s) and the
    specific entry title(s) within each, using the root index.md summary.
    Does not load section concepts — that is the KBContextAgent's job."""

    def __init__(self, client: OpenAI, bundle_navigator: BundleNavigator):
        self._client = client
        self._bundle_navigator = bundle_navigator

    def route_query_to_sections(
        self, query: str, correction: str | None = None
    ) -> dict[str, list[str]]:
        """Returns {section_name: [title, ...]}. Raises InvalidLLMResponseError on a
        malformed response; raises OpenAIError on a transient API failure. Both are
        retried by the orchestrator."""
        messages = self._build_messages(query, correction)

        response = call_openai(
            self._client.chat.completions.create,
            model=MODEL,
            messages=messages,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        result = parse_json_object(content)
        require_keys(result, "sections", raw_content=content)

        sections = result["sections"]
        if not _is_valid_sections(sections):
            raise InvalidLLMResponseError(
                "'sections' must be a JSON object mapping each section name to a list of "
                f"title strings, got {sections!r}",
                content,
            )

        return sections

    def _build_messages(self, query: str, correction: str | None) -> list[dict]:
        section_summary = self._bundle_navigator.get_section_summary()
        messages = [
            {"role": "system", "content": SECTION_ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": f"{section_summary}\n\nQuery: {query}"},
        ]
        if correction:
            messages = build_correction_messages(messages, None, correction, _EXPECTED_FORMAT)
        return messages
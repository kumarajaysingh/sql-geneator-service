import json
from dataclasses import dataclass
from typing import Callable, TypedDict, TypeVar

from anthropic import AnthropicError
from openai import OpenAIError

from logger.logger_config import get_logger
from src.utils.env import required_env_int
from src.utils.llm_support import InvalidLLMResponseError
from src.agents.section_router_agent import SectionRouterAgent
from src.agents.kb_context_agent import KBContextAgent
from src.agents.sql_generator_agent import SqlGeneratorAgent
from src.agents.sql_validator_agent import SqlValidatorAgent

_T = TypeVar("_T")

_logger = get_logger()

MAX_VALIDATION_ATTEMPTS = required_env_int("ORCHESTRATOR_MAX_VALIDATION_ATTEMPTS")
VALIDATION_SCORE_THRESHOLD = required_env_int("ORCHESTRATOR_VALIDATION_SCORE_THRESHOLD")
STAGE_RETRY_ATTEMPTS = required_env_int("ORCHESTRATOR_STAGE_RETRY_ATTEMPTS")

_RETRYABLE_ERRORS = (OpenAIError, AnthropicError, json.JSONDecodeError, KeyError, TypeError, OSError)

_STAGE_DESCRIPTIONS = {
    "section_routing": "figuring out which knowledge sections are relevant",
    "context_building": "loading the knowledge-base context",
    "sql_generation": "generating the SQL query",
    "sql_validation": "validating the generated SQL query",
}


class OrchestratorResult(TypedDict):
    context: str | None
    sql: str | None
    explanation: str | None
    status: str
    validation_feedback: str | None
    validation_score: int | None
    attempts: int
    message: str | None


class StageExecutionError(Exception):
    def __init__(self, stage: str, cause: Exception):
        super().__init__(f"stage {stage!r} failed after {STAGE_RETRY_ATTEMPTS} attempt(s): {cause}")
        self.stage = stage
        self.cause = cause


@dataclass
class _GenerationOutcome:
    skip_result: "OrchestratorResult | None" = None
    sql_result: dict | None = None
    feedback: str | None = None
    score: int = 0
    attempts: int = 0
    passed: bool = False


def _run_stage(stage: str, call: Callable[[str | None], _T]) -> _T:
    correction: str | None = None
    last_error: Exception | None = None

    for attempt in range(1, STAGE_RETRY_ATTEMPTS + 1):
        try:
            return call(correction)
        except InvalidLLMResponseError as exc:
            last_error = exc
            correction = exc.reason
            _logger.warning(
                "orchestrator stage=%s attempt=%d/%d got an invalid LLM response: %s",
                stage, attempt, STAGE_RETRY_ATTEMPTS, exc.reason,
            )
        except _RETRYABLE_ERRORS as exc:
            last_error = exc
            _logger.warning(
                "orchestrator stage=%s attempt=%d/%d failed: %s",
                stage, attempt, STAGE_RETRY_ATTEMPTS, exc,
            )

    raise StageExecutionError(stage, last_error) from last_error


class OrchestratorAgent:
    """Routes the query to relevant OKF sections via SectionRouterAgent, loads the
    knowledge-base context for those sections via KBContextAgent, then runs a
    generate/validate loop between SqlGeneratorAgent and SqlValidatorAgent — feeding the
    validator's feedback back into the generator on each retry — for up to
    MAX_VALIDATION_ATTEMPTS attempts. Never executes the SQL; it only produces and
    validates it."""

    def __init__(
        self,
        section_router_agent: SectionRouterAgent,
        kb_context_agent: KBContextAgent,
        sql_generator_agent: SqlGeneratorAgent,
        sql_validator_agent: SqlValidatorAgent,
    ):
        self._section_router_agent = section_router_agent
        self._kb_context_agent = kb_context_agent
        self._sql_generator_agent = sql_generator_agent
        self._sql_validator_agent = sql_validator_agent

    def generate_sql(self, query: str) -> OrchestratorResult:
        try:
            return self._run_pipeline(query)
        except StageExecutionError as exc:
            _logger.error(
                "orchestrator pipeline aborted for query=%r at stage=%s: %s",
                query, exc.stage, exc.cause,
            )
            description = _STAGE_DESCRIPTIONS.get(exc.stage, exc.stage)
            return self._negative_result(
                message=f"Something went wrong while {description}. Please try again in a moment."
            )
        except Exception:
            _logger.exception("orchestrator pipeline hit an unexpected error for query=%r", query)
            return self._negative_result(
                message="Something unexpected went wrong while processing your request. Please try again."
            )

    def _run_pipeline(self, query: str) -> OrchestratorResult:
        sections = _run_stage(
            "section_routing",
            lambda correction: self._section_router_agent.route_query_to_sections(query, correction),
        )
        _logger.debug("query=%r routed to sections=%r", query, sections)

        if not sections:
            return self._negative_result(
                message="I couldn't find any relevant knowledge-base section for this query."
            )

        context = _run_stage(
            "context_building", lambda _correction: self._kb_context_agent.get_context(sections)
        )

        if not context:
            return self._negative_result(
                message="I found a relevant section but no usable content for this query."
            )

        outcome = self._generate_and_validate(query, context)

        if outcome.skip_result is not None:
            return outcome.skip_result

        return {
            "context": context,
            "sql": outcome.sql_result["sql"],
            "explanation": outcome.sql_result["explanation"],
            "status": "positive" if outcome.passed else "negative",
            "validation_feedback": outcome.feedback,
            "validation_score": outcome.score,
            "attempts": outcome.attempts,
            "message": None,
        }

    def _generate_and_validate(self, query: str, context: str) -> "_GenerationOutcome":
        feedback = None
        score = 0
        attempts = 0
        sql_result = None

        for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
            attempts = attempt
            sql_result = _run_stage(
                "sql_generation",
                lambda correction: self._sql_generator_agent.generate(
                    query, context, feedback, correction
                ),
            )

            if not sql_result["sql"]:
                skip_result: OrchestratorResult = {
                    "context": context,
                    "sql": "",
                    "explanation": sql_result["explanation"],
                    "status": "negative",
                    "validation_feedback": None,
                    "validation_score": None,
                    "attempts": attempts,
                    "message": None,
                }
                return _GenerationOutcome(skip_result=skip_result)

            validation = _run_stage(
                "sql_validation",
                lambda correction: self._sql_validator_agent.validate(
                    query, context, sql_result["sql"], correction
                ),
            )
            verdict = validation["verdict"]
            score = validation["score"]
            feedback = validation["feedback"]
            _logger.info(
                "user_query=%r | validation attempt=%d: sql=%r verdict=%s score=%s feedback=%s",
                query, attempt, sql_result["sql"], verdict, score, feedback,
            )

            if verdict == "valid" and score >= VALIDATION_SCORE_THRESHOLD:
                return _GenerationOutcome(
                    sql_result=sql_result, feedback=feedback, score=score,
                    attempts=attempts, passed=True,
                )

        return _GenerationOutcome(
            sql_result=sql_result, feedback=feedback, score=score, attempts=attempts, passed=False
        )

    @staticmethod
    def _negative_result(*, message: str, attempts: int = 0) -> OrchestratorResult:
        return {
            "context": None,
            "sql": None,
            "explanation": None,
            "status": "negative",
            "validation_feedback": None,
            "validation_score": None,
            "attempts": attempts,
            "message": message,
        }
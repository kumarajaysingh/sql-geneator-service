import os

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

from openAi_client import get_openai_client
from claude_client import get_claude_client
from logger.query_logger import log_query_details
from src.parser.okf_parser import BundleNavigator
from src.agents.section_router_agent import SectionRouterAgent, get_bundle_path
from src.agents.kb_context_agent import KBContextAgent
from src.agents.sql_generator_agent import SqlGeneratorAgent
from src.agents.sql_validator_agent import SqlValidatorAgent
from src.agents.orchestrator_agent import OrchestratorAgent

app = FastAPI(title="SQL Generator Service", version="1.0.0")

client = get_openai_client()
claude_client = get_claude_client()
bundle_navigator = BundleNavigator(get_bundle_path())

section_router_agent = SectionRouterAgent(client, bundle_navigator)
kb_context_agent = KBContextAgent(bundle_navigator)
sql_generator_agent = SqlGeneratorAgent(client)
sql_validator_agent = SqlValidatorAgent(claude_client)
orchestrator_agent = OrchestratorAgent(
    section_router_agent,
    kb_context_agent,
    sql_generator_agent,
    sql_validator_agent,
)


class NL2SQLRequest(BaseModel):
    query: str


class NL2SQLResponse(BaseModel):
    sql: str | None = None
    explanation: str | None = None
    status: str
    validation_feedback: str | None = None
    validation_score: int | None = None
    attempts: int
    message: str | None = None


@app.post("/api/nl2sql/generate", response_model=NL2SQLResponse)
def generate_sql(request: NL2SQLRequest) -> NL2SQLResponse:
    result = orchestrator_agent.generate_sql(request.query)

    log_query_details(
        query=request.query,
        sql=result.get("sql"),
        explanation=result.get("explanation"),
        status=result.get("status"),
        validation_feedback=result.get("validation_feedback"),
        validation_score=result.get("validation_score"),
        attempts=result.get("attempts", 0),
    )

    return NL2SQLResponse(**{k: v for k, v in result.items() if k != "context"})


if __name__ == "__main__":
    uvicorn.run("main:app", host=os.getenv("API_HOST"), port=int(os.getenv("API_PORT")), reload=True)
# SQL Generator Service

A NL2SQL service that turns a natural-language question into a validated
SQL query, grounded in an OKF (Open Knowledge Format) knowledge-base bundle
that describes the schema, relationships, and business rules. It never
executes the SQL — that's `sql-executor-service`'s job.

## Flow

```
Actor -> POST /api/nl2sql/generate {"query": "..."}
      -> OrchestratorAgent
           1. SectionRouterAgent   -> which OKF sections/titles are relevant   (OpenAI)
           2. KBContextAgent       -> load those concepts + follow cross-links -> context string
           3. loop (up to ORCHESTRATOR_MAX_VALIDATION_ATTEMPTS):
                SqlGeneratorAgent  -> generate SQL + explanation from context  (OpenAI)
                SqlValidatorAgent  -> judge the SQL, return verdict/score/feedback (Claude)
                verdict=valid and score >= ORCHESTRATOR_VALIDATION_SCORE_THRESHOLD -> done
                else -> feed feedback back into the next generation attempt
      -> NL2SQLResponse {sql, explanation, status, validation_feedback, validation_score, attempts, message}
```

Each stage call is retried up to `ORCHESTRATOR_STAGE_RETRY_ATTEMPTS` times on
a transient failure (rate limit, malformed LLM JSON, etc.) before the whole
pipeline aborts with a friendly `message`. Every run is logged to
`logger/logs/nl2sql_agent.log` regardless of outcome.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirement.txt
```

Create a `.env` file in the project root:

```
OPENAI_API_KEY=sk-...
CLAUDE_API_KEY=sk-ant-...
OKF_BUNDLE_PATH=/absolute/path/to/okf-bundle-nl2sql

ORCHESTRATOR_MAX_VALIDATION_ATTEMPTS=4
ORCHESTRATOR_VALIDATION_SCORE_THRESHOLD=90
ORCHESTRATOR_STAGE_RETRY_ATTEMPTS=2

OPENAI_MAX_CONCURRENCY=5
OPENAI_RPM=60
ANTHROPIC_MAX_CONCURRENCY=5
ANTHROPIC_RPM=60
RATE_LIMITER_ACQUIRE_TIMEOUT_SECONDS=30

API_HOST=0.0.0.0
API_PORT=8089
```

- `OKF_BUNDLE_PATH` must point at the root of an OKF bundle (see
  `okf-bundle-nl2sql`) — an `index.md` plus per-section concept markdown
  files with YAML frontmatter.
- `SQL_GENERATOR_MODEL` (optional, default `gpt-4o-mini`) selects the OpenAI
  model used by both `SectionRouterAgent` and `SqlGeneratorAgent`.
- `CLAUDE_MODEL` (optional, default `claude-sonnet-5`) selects the Anthropic
  model used by `SqlValidatorAgent`.
- The rate-limiter variables cap concurrent + per-minute calls to each
  provider independently, so one endpoint can't blow through your API quota.

## Run

```bash
python main.py
```

Swagger UI: http://localhost:8089/docs

## Call it

```bash
curl -X POST http://localhost:8089/api/nl2sql/generate \
  -H "Content-Type: application/json" \
  -d '{"query": "How many claims were denied last month?"}'
```

Response:

```json
{
  "sql": "SELECT COUNT(*) FROM claims WHERE status = 'denied' AND submitted_at >= ...",
  "explanation": "...",
  "status": "positive",
  "validation_feedback": "...",
  "validation_score": 95,
  "attempts": 1,
  "message": null
}
```

`status` is `"negative"` when validation never passed within the attempt
budget, the router found no relevant section, or the pipeline aborted — in
which case `message` explains why and `sql` may be `null` or empty.

## Project layout

```
main.py                              FastAPI app, wires clients + agents, POST /api/nl2sql/generate
openAi_client.py                     cached OpenAI client from OPENAI_API_KEY
claude_client.py                     cached Anthropic client from CLAUDE_API_KEY
logger/
  logger_config.py                   file logger -> logger/logs/nl2sql_agent.log
  query_logger.py                    logs each generation run's inputs/outputs
src/
  agents/
    orchestrator_agent.py            drives the routing -> context -> generate/validate pipeline
    section_router_agent.py          query -> relevant OKF sections/titles      (OpenAI)
    kb_context_agent.py              sections -> concept context (with linked concepts)
    sql_generator_agent.py           context + query (+ feedback) -> SQL         (OpenAI)
    sql_validator_agent.py           SQL -> verdict/score/feedback               (Claude)
  parser/
    okf_parser.py                    BundleNavigator: parses/caches the OKF bundle's markdown+YAML
  prompt/
    section_router_prompt.py         system prompt for SectionRouterAgent
    sql_generator_prompt.py          system prompt for SqlGeneratorAgent
    sql_validator_prompt.py          system prompt for SqlValidatorAgent
  utils/
    env.py                           required_env_int — fail fast on missing/bad .env values
    llm_support.py                   JSON parsing + correction-retry helpers for LLM responses
    rate_limiter.py                  per-provider concurrency + requests-per-minute limiter
```

## Notes

- Read-only by design: this service only produces and validates SQL text; it
  never opens a database connection or executes anything.
- `SqlGeneratorAgent` and `SqlValidatorAgent` use different providers on
  purpose — using a different model to critique the generator's output
  catches mistakes a single model is more likely to rationalize.
- `.env` holds real API keys for local dev only — do not commit it.

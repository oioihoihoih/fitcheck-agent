# Backend Harness Agent Instructions

This file is for agent sessions whose working root is `backend_harness`.
It is scoped to backend/API/runtime work. Do not use it as end-user FitCheck
conversation context.

## Scope

There are two separate surfaces:

- FitCheck product agent prototype: `app/`, `/api/agent/runs`, production agent
  runtime, backend-owned tool registry, provider boundaries, grounding, and
  verification.
- Generic development harness API: mounted from sibling package
  `developer_harness` through `web_server.py` at `/api/harness/v1`
  and `/api/dev-harness`.

Keep these surfaces separate. Do not route FitCheck product behavior through the
generic development harness unless the task explicitly asks for development
orchestration.

## Non-Negotiable Boundaries

- Do not call a fixed workflow a production AI agent.
- Production FitCheck agent behavior requires real LLM configuration and a
  model-selected backend-owned tool loop.
- If LLM, live search, OCR, or provider credentials are missing, return or show
  `configuration_required`, `not_connected`, or the specific provider error.
- Cached search, fixture OCR, mock LLM, deterministic scripts, and fallback
  parsers are harnesses. Do not present them as proof of production AI behavior.
- Do not invent measurements, material composition, price, source URLs, reviews,
  or product claims.
- Final reports must be trace-grounded by stored observations.

## Development Harness Rules

The generic development harness is available through the FastAPI app, but its
state is session-isolated.

- API base: `/api/harness/v1`
- Legacy alias: `/api/dev-harness`
- Stateful calls require `X-Harness-Client-Id`.
- Use a unique client id per human session, automation controller, or external
  agent session.
- Do not read, resume, claim, or mutate another client's task, loop, event
  stream, worker queue, provider diagnostics, or eval cases.
- Use `workspace_mode=client_isolated` when a delegated Codex run must avoid
  repository-level `AGENTS.md`; it creates a client-scoped workspace outside the
  repository with a fresh session-local `AGENTS.md`.
- If `HARNESS_API_KEY` is configured, callers must pass `X-Harness-Api-Key` or
  `Authorization: Bearer <key>`.

Useful harness endpoints:

```text
GET  /api/harness/v1/health
GET  /api/harness/v1/ready
GET  /api/harness/v1/capabilities
POST /api/harness/v1/tasks
GET  /api/harness/v1/tasks/{task_id}
GET  /api/harness/v1/tasks/{task_id}/events
GET  /api/harness/v1/tasks/{task_id}/evidence
POST /api/harness/v1/tasks/{task_id}/reply
POST /api/harness/v1/tasks/{task_id}/subagents
POST /api/harness/v1/tasks/{task_id}/tool-calls
POST /api/harness/v1/tasks/{task_id}/observations
POST /api/harness/v1/tasks/{task_id}/artifacts
POST /api/harness/v1/workers/tick
POST /api/harness/v1/workers/heartbeat
POST /api/harness/v1/loops
POST /api/harness/v1/loops/{loop_id}/tick
GET  /api/harness/v1/llm/providers
POST /api/harness/v1/llm/probe
GET  /api/harness/v1/llm/diagnostics
GET  /api/harness/v1/eval-cases
POST /api/harness/v1/eval-cases
```

## Local LLM And Agent SDK Direction

FitCheck core tools are backend-owned. Hosted OpenAI tools are references only
for this project unless explicitly requested.

- OpenAI cloud default: Responses-style tool calling.
- Local LLM default: OpenAI-compatible `/v1/chat/completions` tool calling.
- Local provider profiles include Ollama, LM Studio, vLLM, and other compatible
  servers.
- Probe `/models` through the harness before claiming a local endpoint is
  connected.
- Agents SDK can be used later for handoffs, SDK tracing, or specialist agents,
  but a single backend-owned tool loop is preferred until that complexity is
  justified.

## Backend Implementation Shape

Prefer this structure:

```text
app/
  routes/
  schemas.py
  agent_runtime.py
  tool_registry.py
  agent_run_store.py
  report_grounding.py
  tools/
```

Route bodies should validate and delegate. Business logic belongs in runtime,
tool handlers, stores, provider adapters, or grounding validators.

Runtime responsibilities:

- enforce LLM configuration;
- build model input from current run state;
- inject backend-owned tool schemas;
- parse Responses or chat-completions tool calls;
- validate and execute backend tools;
- persist observations and trace;
- stop on completed, waiting_for_user, configuration_required, failed tool,
  timeout, max turns, or grounding failure.

## Verification

From repository root:

```powershell
python developer_harness\verify.py
python -B backend_harness\verify_backend_infrastructure.py
python -B backend_harness\verify_production_agent_runtime.py
python -B backend_harness\verify_local_llm_tool_adapter.py
python -B backend_harness\verify_input_variation_matrix.py
```

From this directory:

```powershell
python ..\developer_harness\verify.py
python -B verify_backend_infrastructure.py
python -B verify_production_agent_runtime.py
python -B verify_local_llm_tool_adapter.py
python -B verify_input_variation_matrix.py
```

HTTP smoke and live provider checks are opt-in and may require a running server
or credentials:

```powershell
python -m uvicorn backend_harness.web_server:app --host 127.0.0.1 --port 8000
set FITCHECK_RUN_HTTP_API_SMOKE=1
python -B backend_harness\verify_http_api_smoke.py
```

## Communication

Be explicit about what is verified and what is only a harness, mock, fixture,
fallback, cached result, or intended production design. Do not imply an LLM,
search provider, OCR provider, Agent SDK trace backend, or production agentic
loop is connected unless the relevant verification proves it.

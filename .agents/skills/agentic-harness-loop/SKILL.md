---
name: agentic-harness-loop
description: Use when Codex is implementing or verifying agentic AI, Agent SDK, OpenAI Responses API, or local OpenAI-compatible LLM work through the generic development harness. Guides provider probes, delegated task prompts, evidence capture, eval-case creation, worker leases, and verification without claiming production AI connectivity from mocks or deterministic fallbacks.
---

# Agentic Harness Loop

Use the generic harness as the outer loop owner. The model or delegated Codex run may choose actions, but the harness must own state, evidence, tools, verification, replay, and final claims.

Use `scripts/scaffold_agent_harness.py` when starting a new agentic harness workspace. Prefer `scripts/harness_api.py` for routine harness API calls. Use `scripts/trace_ingest.py` when you already have Agents SDK, Responses API, or trace/result JSON to convert into harness evidence. Use direct HTTP only when the scripts do not cover the needed endpoint.

## Workflow

1. Read `C:\com2us\developer_harness\AGENTS.md` and `C:\com2us\docs\20-general-agent-loop-engineering.md` before changing harness behavior.
2. Call `GET /api/harness/v1/health`, `GET /api/harness/v1/ready`, then `GET /api/harness/v1/capabilities` and confirm the needed capabilities are advertised.
3. For LLM-backed work, select an explicit `llm_provider` profile and call `POST /api/harness/v1/llm/probe` before saying a provider is connected.
4. Create delegated tasks with `POST /api/harness/v1/tasks`, using `X-Harness-Client-Id`, an explicit `cwd`, sandbox, verification commands, and `metadata.agentic_loop_template` when applicable.
5. During tool-loop work, record typed evidence:
   - `POST /tasks/{task_id}/tool-calls`
   - `POST /tasks/{task_id}/observations`
   - `POST /tasks/{task_id}/artifacts`
6. Convert repeatable trace/runtime gaps into `POST /api/harness/v1/eval-cases` records.
7. For long-running workers, renew current leases through `POST /api/harness/v1/workers/heartbeat` with the active `lease_epoch`.
8. Finish by inspecting `GET /tasks/{task_id}`, `GET /tasks/{task_id}/evidence`, and event SSE/replay. Report only evidence-backed claims.

## Tool Selection

- Starting a new reusable harness workspace: use `scaffold_agent_harness.py --output-dir <dir>`.
- Unsure which API to use: run `python scripts/harness_api.py plan --goal <provider|task|tool-evidence|eval|worker|failure>`.
- Need provider status: use `harness_api.py providers`, then `harness_api.py probe`.
- Need delegated Codex work: use `harness_api.py task` with explicit `--cwd`, sandbox, skills, and verifier commands.
- Already have trace/result JSON: use `trace_ingest.py --trace-json <file> --task-id <task_id>`.
- Need to persist model-selected tool behavior: use `tool-call`, `observation`, then `evidence`.
- Need a regression seed from a failure: use `eval-case`.
- Need long-running worker safety: use `heartbeat` with the current lease epoch.

## Guardrails

- Use `--client-id` or `HARNESS_CLIENT_ID` for every stateful command.
- Use an idempotency key for task and subagent creation that may be retried.
- Keep scaffold output product-neutral; do not turn it into FitCheck production runtime code.
- Probe `/models` before claiming provider connectivity.
- Record observations for tool outputs, not prose summaries only.
- Preserve tool calls, tool outputs, handoffs, approvals/interruptions, guardrail results, raw traces, and final output as separate evidence surfaces when available.
- Convert repeated failures into eval cases instead of retrying from chat memory.
- Label deterministic, cached, fixture, mocked, or fallback behavior as harness evidence only.

## Failure Recovery

- `configuration_required`: record the diagnostic, fix profile fields/env, probe again.
- API not reachable: verify the backend server and `HARNESS_BASE_URL`; do not infer provider failure.
- 404 on task/loop/evidence: check `X-Harness-Client-Id`; cross-client access should fail.
- 409 on heartbeat or state update: refresh task state and use the current `state_version` or `lease_epoch`.
- Verification failure: inspect task evidence, record an eval case if repeatable, then patch and rerun the verifier.

## Boundaries

- Do not route FitCheck production behavior through this generic harness unless explicitly asked.
- Do not treat mock, cached, fixture, or deterministic fallback behavior as proof of a live production AI agent.
- Do not claim OpenAI, Ollama, LM Studio, vLLM, search, OCR, or Agent SDK trace backend connectivity unless the relevant probe or verifier proves it.
- Keep route bodies thin; put business logic in `runtime.py`, `store.py`, `models.py`, `auth.py`, `catalog.py`, or `service.py`.

## Reference

For exact API checklist, script examples, evidence expectations, and source-backed design rules, read `references/api-checklist.md`.

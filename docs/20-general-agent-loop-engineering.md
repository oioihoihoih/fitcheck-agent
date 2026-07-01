# General Agent Loop Engineering Harness

This document defines a reusable harness engineering baseline for future AI
agent projects. It is intentionally product-neutral. FitCheck can use it, but
the contract is for any agent that needs durable execution, observable tool
loops, subagents, hooks, and evaluations.

## Sources Reviewed

- OpenAI, "Harness engineering: leveraging Codex in an agent-first world":
  https://openai.com/index/harness-engineering/
- OpenAI Cookbook, "Build an Agent Improvement Loop with Traces, Evals, and
  Codex": https://developers.openai.com/cookbook/examples/agents_sdk/agent_improvement_loop
- Anthropic, "Building Effective Agents":
  https://www.anthropic.com/engineering/building-effective-agents
- Anthropic, "Demystifying evals for AI agents":
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Anthropic, "Writing effective tools for AI agents":
  https://www.anthropic.com/engineering/writing-tools-for-agents
- LangGraph overview and persistence docs:
  https://docs.langchain.com/oss/python/langgraph/overview and
  https://docs.langchain.com/oss/python/langgraph/persistence
- Microsoft Agent Framework overview:
  https://learn.microsoft.com/en-us/agent-framework/overview/
- Martin Fowler, "Harness engineering for coding agent users":
  https://martinfowler.com/articles/harness-engineering.html
- Local package probes installed under `harness_outputs/agent_framework_probe`:
  `langgraph==1.2.4`, `openai-agents==0.17.5`, `autogen-agentchat==0.7.5`.
- Codex app-server schemas generated under `harness_outputs/codex_app_server_schema`.
- Claude Code/Codex-like historical implementation inspected at `C:/ainovel/src`.

## What Is Established vs Community Terminology

Established patterns:

- Anthropic distinguishes workflows from agents: workflows follow fixed code
  paths; agents dynamically choose process and tool use.
- LangGraph treats durable execution, persistence, interrupts, streaming, and
  human-in-the-loop as core orchestration runtime concerns.
- OpenAI Agents SDK exposes agents, tools, handoffs, sessions, tracing, guardrails,
  and interruption/resume state.
- OpenAI Agents SDK docs describe `Agent` + `Runner` as the SDK-owned loop for
  turns, tools, guardrails, handoffs, and sessions. If this project wants to own
  the loop directly, keep using Responses API or OpenAI-compatible chat
  completions with backend-owned tools.
- OpenAI Agents SDK tracing records LLM generations, tool calls, handoffs,
  guardrails, and custom events. Treat trace capture as a first-class harness
  artifact, not as optional logging.
- OpenAI Agents SDK can sit over Responses API, Chat Completions-compatible
  APIs, or provider routing layers; local mode should be treated as an
  OpenAI-compatible chat-completions tool-calling path unless stateful Responses
  support is explicitly verified.
- Ollama and LM Studio both expose OpenAI-compatible local endpoints. Do not
  assume hosted OpenAI tools, conversation IDs, or stateful `previous_response_id`
  semantics in local mode.
- Microsoft Agent Framework emphasizes session-based state, type safety,
  middleware, telemetry, and graph-based orchestration.
- Codex supports skills, custom agents, hooks, subagents, app-server JSON-RPC,
  and noninteractive JSONL execution.

Community/new terminology:

- "Harness engineering" means the scaffolding, tools, feedback loops,
  observability, evals, and repository legibility that let agents do reliable
  work.
- "Loop engineering" is the practical discipline of designing the outer loop:
  observe, act, verify, critique, repair, persist, and resume.
- "Ralph loop" is an informal coding-agent review loop: ask an agent to make a
  change, have agents review it, incorporate feedback, and repeat until no
  reviewer finds blocking issues.

## Reference Architecture

```text
client / another agent session
-> versioned harness API
-> run/task store with optimistic state_version
-> append-only event log with monotonic seq
-> worker lease and heartbeat
-> loop runtime
   -> model call or delegated Codex/app-server run
   -> tool call
   -> hook dispatch
   -> observation
   -> verification/eval
   -> next action | human interrupt | final
-> trace/eval artifacts
```

Core rule: the outer harness owns state, tools, events, permissions, and
verification. The model may choose actions, but the harness records and enforces
the loop.

Isolation rule: a shared harness API must not imply shared working memory.
Every stateful operation needs an explicit session/client namespace, and task
queues, event streams, loops, worker leases, idempotency keys, artifacts, and
resume handles must be filtered by that namespace. Repository-level instruction
files can provide global policy, but they must not be treated as per-session
memory.

## LLM Provider Profiles

Every agent run should declare an LLM provider profile:

```json
{
  "provider": "ollama",
  "api_style": "chat_completions",
  "base_url": "http://localhost:11434/v1",
  "model": "qwen3-coder",
  "supports_tool_calling": true,
  "supports_stateful_responses": false
}
```

Rules:

- Cloud OpenAI can use `api_style=responses` when stateful Responses behavior is
  desired and configured.
- Local Ollama, LM Studio, vLLM, and similar servers default to
  `api_style=chat_completions`.
- The backend harness owns tool schemas, tool execution, observations, retries,
  timeouts, traces, and final grounding. The local model only selects tool calls.
- Probe `/models` and record the result before claiming that a local endpoint is
  connected.
- Treat tool-calling support as model-dependent even when the server implements
  an OpenAI-compatible endpoint.

## Loop Taxonomy

- `single_turn`: one prompt, one result, no durable continuation.
- `tool_loop`: model-selected tool call, backend execution, observation, next
  model step.
- `durable_loop`: every logical step is checkpointed so a worker crash can
  resume from the last committed event.
- `review_loop`: implementation followed by deterministic tests and agent/human
  review, with feedback converted into a new turn.
- `eval_loop`: real traces and failures become repeatable eval cases.
- `harness_evolution_loop`: eval failures propose changes to prompts, tools,
  schemas, docs, or runtime guardrails, then re-run the eval set.
- `scheduled_loop`: bounded recurring checks with explicit cadence, maximum
  iterations, and idempotent task prompts.

## Minimum Data Model

Use normalized tables, not a single mutable JSON blob as source of truth:

```text
runs/tasks:
  id, status, state_version, parent_id, join_policy,
  lease_owner, lease_epoch, lease_expires_at,
  created_at, updated_at, snapshot_json

events:
  event_id, task_id, seq, type, actor, parent_event_id,
  status, created_at, payload_json

tool_calls:
  call_id, task_id, tool_name, input_json, status, started_at, completed_at

observations:
  observation_id, task_id, call_id, tool_name, result_json, evidence_refs

hook_executions:
  hook_id, task_id, event_name, status, payload_json

verification_results:
  verification_id, task_id, command/eval_name, status, artifacts, payload_json

artifacts:
  artifact_id, task_id, kind, path/url, digest, metadata_json
```

`state_version` is for optimistic concurrency on mutating API calls. `seq` is
for ordered replay and SSE reconnect. `lease_epoch` prevents stale workers from
writing after a newer worker has reclaimed the task.

## Skills, Agents, Hooks, Subagents

Skills:

- Store reusable workflows in `.agents/skills/*/SKILL.md`.
- Keep initial context to name, description, and path; load full instructions
  only when needed.
- Each skill should state trigger, inputs, outputs, tools, and verification.

Custom agents:

- Use `.codex/agents/*.toml` for bounded roles such as explorer, worker, and
  verifier.
- Give workers disjoint write ownership. Give explorers/verifiers read-only
  sandboxes by default.

Hooks:

- Use hooks for deterministic lifecycle enforcement, event capture, policy,
  prompt scanning, and post-run validation.
- Hook failures must be isolated and recorded.
- Hooks should not secretly mutate run state except through typed harness APIs.

Subagents:

- Treat subagents as child tasks with `parent_task_id`, `join_policy`, budget,
  capabilities, and final handoff summary.
- Prefer subagents for read-heavy exploration, independent verification, or
  disjoint implementation slices.
- Avoid recursive fan-out unless explicitly budgeted.

## Tool Design Rules

Good agent tools are not thin wrappers around messy systems. They should:

- expose typed schemas and clear error codes;
- return concise, inspectable observations;
- include provenance, artifact paths, and retry hints;
- be idempotent where possible;
- separate dry-run, read, write, and destructive modes;
- have deterministic unit tests and trajectory/eval tests.

## Feedback Flywheel

1. Persist traces and tool observations.
2. Label failures: missing context, bad tool, bad prompt, bad runtime policy,
   flaky environment, or impossible task.
3. Convert failures into evals.
4. Let Codex propose harness changes.
5. Re-run evals and record before/after.
6. Promote repeated review feedback into docs, skills, linters, hooks, or tests.

This is the important part of "loop engineering": improvement is stored in the
harness and repository, not in transient chat memory.

## Current Implementation Notes

As of 2026-06-11, `developer_harness.runtime.DevelopmentHarness.capabilities()`
exposes an `agentic_ai_harness_contract` and `agentic_loop_templates`.
It also exposes a `read_model_contract` with SDK event types, client transcript
sections, summary fields, cursor semantics, and the historical-evidence claim
boundary so controllers can discover projection support before fetching a task.
As of 2026-06-14, the harness also exposes an SDK-like client transcript
projection for each task at
`/api/harness/v1/tasks/{task_id}/client-transcript`. It combines the task
request, Codex JSONL events, tool calls, observations, artifacts, hooks, and
verification results into a compact read model for external controllers. This
is a projection over stored harness evidence; it is not a separate source of
truth and it does not imply a live model/provider connection.
The harness also exposes SDK-like task session metadata through `/sessions`,
`/tasks/{task_id}/session-info`, `/tasks/{task_id}/annotations`, and
`/tasks/{task_id}/fork`.
As of 2026-06-28, `scripts/harness_api.py` exposes SDK and read-model commands
for `sdk-initialize`, `sdk-mcp-status`, `sdk-context-usage`, `tasks`,
`sessions`, `eval-cases`, `loops`, and bounded raw `events` SSE replay, so
routine harness testing can use the same client-id-aware script instead of
falling back to ad hoc HTTP calls.
The `capabilities --summary true` command summarizes the structured
`read_model_contract` from `/capabilities`, including supported SDK event
types, transcript sections, summary fields, cursor semantics, and support flags
for eval-case verification and harness verification result projections.
The CLI diagnostics command accepts `--limit N` and maps it to
`/llm/diagnostics?limit=N`, so provider diagnostic readbacks used by matrix
validation can be reproduced from the same script without ad hoc HTTP. It also
accepts `--expect-diagnostic-id N` plus optional `--expect-status`,
`--expect-provider`, `--expect-model`, `--expect-api-style`,
`--expect-provider-request-attempted`, `--expect-provider-response-received`,
and `--expect-external-api-called` fields, appends a client-side
`diagnostics_validation` summary, and exits nonzero when the expected
diagnostic id or supplied fields are not visible in the current client-scoped
read model.
The CLI `probe` command uses provider-aware defaults: `--provider openai`
targets OpenAI Responses at `https://api.openai.com/v1` with
`OPENAI_API_KEY`, while local providers such as Ollama, LM Studio, and vLLM
default to OpenAI-compatible chat-completions endpoints. This keeps routine
provider diagnostics from accidentally probing a local endpoint when the caller
asked for OpenAI cloud.
Probe responses expose non-secret top-level controller fields such as
`provider_name`, `api_style`, `model`, `api_key_env`,
`provider_request_attempted`, `provider_response_received`, and
`external_api_called`. For provider probes, `external_api_called=true` means
the `/models` endpoint returned an HTTP response; it does not prove that the
FitCheck production agent route made a model call.
The helper scripts under `.agents/skills/agentic-harness-loop/scripts/` carry
POSIX shebangs and executable bits for Linux/WSL direct execution while keeping
the explicit `python <script>` form documented for portable and Windows use.
`agents_sdk_smoke.py` writes a top-level live-smoke `summary` that is useful
before trace ingestion: new item type/class maps, raw response output-type and
usage totals, phase names and per-phase counts, approval decisions, guardrail
tripwire counts, SDK session item types, and stream distribution maps. This is
quick trace metadata from the live smoke result; authoritative replay still
comes from stored harness evidence and read-model summaries after ingestion.
`agents_sdk_smoke_matrix.py` wraps those single-scenario smokes into a
repeatable live matrix gate. It can probe the OpenAI provider through the
harness, run selected scenarios, write per-scenario traces plus an aggregate
matrix JSON, and optionally publish each passing trace into a separate harness
evidence-holder task with `trace_ingest.py --readback`-equivalent validation.
The matrix gate validates scenario-specific evidence, not only final text:
tools need tool call/output records, approvals need request/decision/resume
phase records, guardrails need guardrail or tripwire records, sessions need
multi-turn persisted session state, and streamed handoffs need streamed handoff
event and tool-name preservation. When publishing readback, the matrix applies
the same scenario-specific expectations to SDK-event and client-transcript
summaries before accepting the published scenario, and trace-ingest readback
binds the expected task id to the readback envelope, evidence, SDK-events
summary, and client-transcript summary. It also validates raw event SSE replay
for each published scenario task, checking the replay task id plus artifact,
observation, tool-call, and expected tool-call status-update event coverage
against trace-ingest results. Status-update coverage is based on
`tool_call_status_update_expected_count`, so tool calls recorded as terminal do
not require a redundant status-update event, while queued or otherwise
non-terminal tool calls that complete through a later observation do. The
matrix event replay fetch size is derived from each task's readback summaries,
using SDK-event `raw_event_count` when present and stored evidence counts as
fallback. Scenario and aggregate replay summaries preserve the resulting
`fetch_plan`, and validation fails if the replay is shorter than the expected
raw event count reported by that plan. Compact live and stored-evidence
summaries should mirror the same fetch-plan basis, selected `max_events`,
expected raw event count, and truncation-check results so controllers can
diagnose replay sizing without opening the full matrix JSON. The
per-scenario `scenario_event_replay_validation` payload also returns
`expected_event_counts` and `observed_event_counts` for artifact, observation,
tool-call, and status-update event coverage, so controllers can explain the
exact replay contract without recombining result summaries and event summaries.
The aggregate matrix summary and scenario rollup expose per-scenario
`scenario_event_replay_expected_event_counts`,
`scenario_event_replay_observed_event_counts`,
`scenario_event_replay_event_counts`,
`scenario_event_replay_count_map_totals`,
`scenario_event_replay_count_map_total_totals`,
`scenario_event_replay_count_total_checks`,
`scenario_event_replay_sse_event_name_counts`,
`scenario_event_replay_event_type_counts`,
`scenario_event_replay_event_status_counts`,
`scenario_event_replay_event_actor_counts`, matching raw replay totals
(`scenario_event_replay_sse_event_name_totals`,
`scenario_event_replay_event_type_totals`,
`scenario_event_replay_event_status_totals`,
`scenario_event_replay_event_actor_totals`), and matching coverage event totals, plus
the narrower `tool_call_status_update_expected_counts` and
`tool_call_status_updated_event_counts`, so controllers can explain terminal
tool calls, queued-to-terminal tool calls, replay coverage for artifact,
observation, tool-call, and status-update events, and the raw replay name/type/
status/actor distribution without reopening each scenario result. Event replay
validation also checks that the raw SSE event-name, event-type, event-status,
and event-actor count-map totals each match the replay `event_count`, so stale
or partially updated distribution maps fail with specific readback checks.
Live compact output also exposes `scenario_readback_event_replay_*` maps from
the published scenario evidence-holder readback itself, so controllers can
compare source matrix rollups with the current readback raw replay composition
from stdout. For session scenarios it exposes `scenario_session_state_summaries`
from the source SDK smoke trace, with turn count, phase names, item-count
before/after maps, source-vs-summary match checks, and session-growth flags
while omitting raw prompts, raw preview text, and local session database paths.
It also exposes session stored evidence through `scenario_session_evidence_summaries`
and `scenario_readback_session_evidence_summaries`, with stored revalidation mirrored
as `published_scenario_session_evidence_summaries`, so controllers can verify
persisted `agent_session` observation coverage and stateful-session readback without
opening the full trace JSON.
It also exposes stream stored evidence through `scenario_stream_evidence_summaries`
and `scenario_readback_stream_evidence_summaries`, with stored revalidation mirrored
as `published_scenario_stream_evidence_summaries`, so controllers can verify
persisted `agent_stream_event` observation coverage, count-map preservation, and
stream-handoff tool-name preservation without opening full trace JSON.
It also exposes live `scenario_guardrail_tripwire_summaries` plus
read-model `scenario_guardrail_projection_summaries` and
`scenario_readback_guardrail_projection_summaries`, so guardrail names,
tripwire counts, run-error projection, error type, and tripwire reason can be
checked from compact stdout without opening full trace JSON. It also exposes
guardrail stored evidence summaries through `scenario_guardrail_evidence_summaries`
and `scenario_readback_guardrail_evidence_summaries`, with stored revalidation mirrored
as `published_scenario_guardrail_evidence_summaries`. These summaries prove that
`agent_guardrail_result` evidence was persisted for guardrail runs and `agent_run_error`
evidence was persisted for tripwire runs while omitting raw guardrail results, model output
text, run-error payloads, observation payloads, call ids, observation ids, and database ids.
The aggregate matrix summary
also exposes provider-probe validity, generic readback status counts,
scenario-projection status counts, scenario event-replay status counts, failed
projection/replay scenarios, scenario-to-failed-check maps, published task ids,
and provider diagnostic ids so controllers can make and explain a top-level
gate decision without walking each result item. When
`--publish-matrix-readback` is used, the matrix also creates
an aggregate evidence-holder task with an `agents_sdk_smoke_matrix` artifact and
typed summary, scenario-rollup, and provider-probe-validation observations, then
validates that stored evidence before accepting the aggregate readback. Existing
aggregate tasks can be revalidated without new live provider calls by passing
`--validate-matrix-readback-task-id` and `--validate-matrix-json`; that mode
fetches stored evidence, raw event SSE replay, and read-model summaries only.
The aggregate validation checks replay cursor advancement and artifact/
observation event coverage, returns aggregate `expected_event_counts` and
`observed_event_counts`, checks aggregate raw replay name/type/status/actor
count-map totals against `event_count`, preserves `event_replay_fetch_plan`,
and rejects truncated replays when the fetch plan expected more raw events, so
stored evidence is not accepted without a replayable event trail. Passing
`--validate-published-scenarios` in that mode
also walks each `publish_readback.task_id` from the matrix JSON and revalidates
the stored scenario evidence-holder task with generic trace-ingest readback,
scenario projection checks, and raw event replay checks. Compact validation
summaries preserve each published scenario's replay `expected_event_counts`,
`observed_event_counts`, and matching totals, so a controller can see exactly
which artifact, observation, tool-call, and tool-call status-update coverage
was expected and observed without reopening nested results. That gives a later
controller one stored-evidence command that proves both the aggregate matrix
task and every published scenario task still bind to their current read-model
summaries, without new live SDK/provider calls. The aggregate summary also
includes
`scenario_api_usage_totals`, aggregate `api_usage_totals`,
`scenario_sdk_events_projection_counts`,
`scenario_client_transcript_projection_counts`, per-scenario SDK-event and
client-transcript projection maps such as model-response provider/API-style
counts, SDK-event projection totals, and client-transcript projection totals so
controllers can see per-scenario provider cost and which scenario produced
handoff, guardrail, run-error, session, stream-event, final-output, and
provider/API-style surfaces without walking every scenario result. It also
exposes aggregate and per-scenario `external_api_called` evidence derived from
the SDK smoke result validation and stored trace artifact metadata, so compact
controllers can separate live SDK/API evidence from dry-run or fixture-only
summaries. When those
projection totals differ because one
surface is lifecycle-oriented and the other is compact-state-oriented, the
matrix reports `projection_total_deltas` with both counts and the signed delta.
It also classifies those deltas with `projection_total_delta_assessment`:
known lifecycle-vs-compact differences such as SDK-event `tool_progress_count`
being higher than client-transcript `tool_progress_count` are marked allowed,
while unregistered deltas such as missing final-output, guardrail, run-error,
handoff, approval, session, or stream-event coverage are marked unexpected and
fail the aggregate matrix gate. The summary exposes
`projection_total_delta_status`, allowed keys, unexpected keys, and
`aggregate_failed_reasons` so controllers do not have to infer whether a count
difference is acceptable. Nested projection-map deltas, such as mismatched
`agent_model_response_provider_counts` or
`agent_model_response_api_style_counts`, are reported separately through
`projection_map_deltas` and are always unexpected.
Aggregate readback validation compares the stored matrix summary observation
against the source matrix JSON summary, including both projection-total maps and
the projection-delta map and delta assessment. It also has dedicated checks for
tool-call status-update expected/observed maps and totals, so a stale
controller-facing status-update summary fails with a specific readback check
instead of only a broad payload mismatch. It also has dedicated checks for base
tool state/evidence/projection summaries, so stale compact tool-call evidence fails
with `matrix_summary_scenario_tool_*` checks before a controller trusts it.
Later revalidation catches
corrupted or stale controller-facing aggregate evidence. It excludes the
self-referential `matrix_readback_status` and `matrix_readback_valid` fields
from that payload comparison because those are written to the output summary
only after aggregate readback evidence has already been stored. Aggregate
readback also compares the stored provider-probe validation observation against
the source matrix provider-probe validation payload, not only the boolean valid
state and diagnostic id, so stale or corrupted provider detail evidence fails
revalidation. The same
aggregate validation compares the stored scenario-rollup observation against a
fresh rollup derived from the matrix JSON. That rollup preserves per-scenario
generic readback failed checks, projection failed checks, scenario event-replay
validity, and readback/replay task-binding fields, so controllers can explain
scenario-level failures without reopening each scenario result. Dedicated
rollup checks also compare the tool-call status-update expected/observed maps
and totals plus base tool state/evidence/projection summaries before the full rollup
payload comparison. It also checks
the stored `agents_sdk_smoke_matrix` artifact path against the aggregate
evidence-source JSON. Publish-time aggregate validation
now writes `matrix_evidence_json` into the evidence-source payload before
recording the artifact, so `expected_path` and both recorded/expected SHA-256
digests are available immediately, not only during later revalidation. After
the aggregate readback task id is attached to the final matrix payload, the
publish flow also writes `matrix_payload_readback_validation`; that validation
fetches the stored aggregate task evidence, read-model summaries, and raw event
replay again so the final output proves `matrix_readback.task_id` is bound
without requiring a separate revalidation command. This publish-time payload
validation also refetches client-scoped `/llm/diagnostics` and checks that the
provider diagnostic id is still present with matching status, model, and API
style plus provider request/response/external-call evidence, so the live output
is tied to the provider diagnostic read model before a later stored
revalidation runs. The top-level matrix summary
mirrors each aggregate readback gate's status, validity, failed checks, task
binding, and error where present; failed aggregate readback gates also append
explicit values such as `matrix_readback_failed` or
`matrix_payload_readback_failed` to `aggregate_failed_reasons`, so controllers
can explain a failed top-level gate without walking nested validation objects.
Live compact `matrix_run_summary` also exposes a top-level `valid` boolean
using the same status, failure-reason, stage failure-map, provider-probe, and
aggregate-readback gate conditions so controllers do not need to infer it from
multiple fields.
FitCheck product-route live-smoke exports published through
`backend_harness/publish_live_agent_smoke_export.py` use the same stored-harness
evidence boundary: after writing the trace artifact, tool calls, observations,
and eval case, the publisher refetches evidence, SDK-event summaries,
client-transcript summaries, and raw `/tasks/{task_id}/events` replay, then
fails if artifact/tool/observation events are not replayable from the generic
harness event log. The same publisher can run `--readback-only --task-id ...`
against an already-published evidence-holder task to revalidate stored product
route evidence without making a new FitCheck product, provider, or evidence
write call. Publisher results expose `revalidation_inputs`,
`revalidation_ready`, `revalidation_missing_fields`, and non-secret
`revalidation_cli_args`, so later controllers can rerun the stored-evidence
gate from the JSON output without reconstructing paths from chat history.
Controllers can also pass a previous compact or full publisher JSON directly
with `--revalidate-from-summary-json`; the script reads `revalidation_inputs`
and runs the same readback-only stored-evidence validation. When supplied with
`--record-result-task-id`, a successful publisher revalidation is recorded as an
`eval_case_verification` observation. The publisher then refetches stored
evidence, `/sdk-events`, and `/client-transcript` to prove the just-recorded
observation id is present in evidence and projected by both read models; compact
output mirrors `record_result_readback_valid`, failed checks, and SDK-event /
client-transcript match counts. If the recording or post-record readback gate
fails, the CLI preserves the original stored-evidence status as `publish_status`
but returns top-level `status=failed` and a nonzero exit code so controllers do
not need to parse nested readback details before failing the gate.
`backend_harness/verify_live_agent_smoke.py` can exercise the same gate during
an actual FitCheck product-route live smoke when
`FITCHECK_LIVE_AGENT_PUBLISH_HARNESS=1` and
`FITCHECK_LIVE_AGENT_HARNESS_RECORD_RESULT=1` are set; it publishes the new
trace/eval evidence, records the publish revalidation result on the evidence
holder task, and fails the smoke if that record-result gate fails.
The live-smoke wrapper mirrors the nested publisher status into top-level
summary fields such as `harness_publish_task_id`,
`harness_publish_readback_valid`,
`harness_publish_record_result_readback_valid`, and
`harness_publish_record_result_gate_valid`, so controllers can gate a run
without scraping the full nested publish payload.
It also mirrors non-secret replay handles at the top level, including
`run_id`, `trace_id`, `harness_trace_json`, `harness_trace_digest`,
`harness_eval_case_json`, `harness_eval_case_digest`,
`harness_publish_trace_json`, `harness_publish_eval_case_json`,
`harness_publish_revalidation_inputs`, and
`harness_publish_revalidation_cli_args`, so controllers can launch stored
evidence revalidation without walking the nested export or publisher payload.
`publish_live_agent_smoke_export.py --revalidate-from-summary-json` accepts
those live-smoke wrapper fields directly as well as publisher-native
`revalidation_inputs`.
It also exposes agent-loop evidence counts such as `llm_raw_response_count`,
`trace_model_requested_tool_call_count`,
`trace_backend_recovery_tool_call_count`, `trace_tool_output_count`, and
`observation_count`; `model_selected_tool_call_missing` is now reserved for
runs where only backend recovery tool calls occurred and no model-requested
tool call was traced.
Product-route discovery smokes also expose candidate-search evidence at the
top level: latest search candidate counts, rejected/product-like candidate
counts, fetch and normalization coverage, ranked match counts, top-ranked
scores, report candidate-audit counts, and close-fit search budget. By default
the completed smoke requires at least two searched candidates, two ranked
matches, one normalized candidate, and one reported candidate; controller
overrides can set the `FITCHECK_LIVE_AGENT_EXPECT_MIN_*` environment variables
for single-candidate scenarios.
For completed product-route runs, the smoke also rejects final reports and SDK
`final_output` projections that still contain pending missing-info question
language such as "send the label/size chart and I will compare again"; completed
reports must express evidence limits as grounded confidence limitations instead.
Set `FITCHECK_LIVE_AGENT_PROBE_PROVIDER=1` on the same smoke to record a
client-scoped harness `/llm/probe` diagnostic and read it back from
`/llm/diagnostics`; the smoke fails if the probe did not attempt a provider
request, receive a provider response, record `external_api_called=true`, or
return a matching diagnostic id in the read model. This provider diagnostic is
connection evidence for the configured profile and does not by itself prove the
product route's model-selected tool loop.
When the recorded or
expected artifact file is available locally, it recomputes the SHA-256 digest
and compares it with the stored digest; when files are unavailable, it reports
that availability boundary instead of failing portable stored-evidence
revalidation. Existing aggregate
revalidation also binds the supplied task id to the stored evidence,
SDK-events summary, client-transcript summary, event replay, and
`matrix_readback.task_id` in the matrix JSON when present, preventing a
controller from accidentally validating one aggregate task with another task's
output JSON.
Published scenario revalidation also returns a compact
`published_scenario_readback_summary` beside the detailed per-scenario results,
with task ids, status counts, check totals, failed-scenario maps, and
`error_scenarios`. If a scenario task cannot be fetched or validated, the
matrix command records a scenario-level `readback_error:*` failed check instead
of aborting before writing structured evidence. The compact published-scenario
summary also exposes per-scenario SDK-event and client-transcript projection
maps plus their totals, and stored-evidence validation mirrors those fields
under `published_scenario_*_projection_maps` and
`published_scenario_*_projection_map_totals`. This lets controllers verify
model-response provider/API-style preservation for each stored scenario without
opening every scenario result. Stored-evidence validation compact output should
also mirror the published-scenario readback status, valid flag, scenario count,
check count, status counts, failed scenarios, failed checks, and error scenarios
as top-level `published_scenario_readback_*` fields, so controllers can make a
gate decision from stdout without first unpacking the nested summary. The matrix CLI also supports
`--compact-output` for live, dry-run, and validation modes: live/dry-run output
prints only `matrix_run_summary`, validation output prints only
`readback_validation_summary`, and `--output-json` still preserves the full
evidence payload. Live compact summaries include the aggregate matrix readback
task id, readback check counts, failed-check lists, and task-binding maps so a
controller can launch later stored-evidence revalidation without reopening the
full matrix file. They also mirror aggregate scenario status-count maps,
scenario validation failed-check maps, and generic/projection/event-replay
failed scenario lists so a controller can identify the failed stage from
stdout alone. They also expose aggregate artifact provenance: live compact
output includes `matrix_readback_artifact`, `matrix_readback_artifact_checks`,
`matrix_payload_readback_artifact`, and `matrix_payload_readback_artifact_checks`,
while stored-evidence validation compact output includes `aggregate_artifact`
and `aggregate_artifact_checks`. These fields surface the recorded path,
expected path, stored SHA-256 digest, locally recomputed digests when available,
path availability, and the matrix artifact path/digest check results from
compact stdout. Live compact `matrix_run_summary` output and stored-evidence
validation compact `readback_validation_summary` output also mirror source
matrix API usage, aggregate projection-count maps, SDK-event/client-transcript
projection totals, provider/API-style projection maps, projection deltas,
projection-map deltas, allowed delta keys, and the full projection delta
assessment so controllers can verify stream, final-output, guardrail, handoff,
session, run-error, provider/API-style, and lifecycle-vs-compact delta coverage
from compact stdout. The same compact outputs expose guardrail/tripwire
summaries and published-scenario guardrail projection summaries, keeping policy
tripwire evidence visible next to the count maps. They also expose provider probe validity, diagnostic id,
failed-check lists, check maps, probe status, HTTP status, endpoint, non-secret
provider profile, model, configured API-key environment variable name, and
Agents SDK fit flags so compact controller logs can tie the smoke result back
to the stored provider diagnostic without reopening the full matrix JSON. Stored
matrix revalidation also refetches client-scoped `/llm/diagnostics` and checks
that the source matrix provider diagnostic id is still visible with matching
status, model, and API style. Its compact output mirrors
`provider_diagnostic_readback_status`, `provider_diagnostic_readback_valid`,
`provider_diagnostic_readback_failed_checks`,
`provider_diagnostic_readback_expected_id`, and
`provider_diagnostic_readback_matched_id`; live compact output mirrors the same
fields from `matrix_payload_readback_validation`, so a controller can
distinguish stale aggregate evidence from a missing provider-diagnostic read
model without running a new provider probe. Live
and validation compact summaries also include
`revalidation_inputs`, a non-secret bundle of matrix JSON path, aggregate
readback task id, client id, base URL, and published-scenario/compact-output
flags that can be fed directly into the stored-evidence revalidation command.
Those compact summaries also expose `revalidation_ready`,
`revalidation_missing_fields`, and `revalidation_required_fields` so controllers
can distinguish a runnable stored-evidence revalidation command from a dry-run
summary that still lacks an aggregate readback task id.
They also expose `revalidation_cli_args`, a non-secret argv fragment for this
same script. The fragment omits the Python executable, script path, API key, and
caller-chosen `--output-json` path so controllers can safely choose their own
execution wrapper while reusing the validated flags.
Controllers can also pass a previous compact or full summary file directly to
`--revalidate-from-summary-json`; the script reads `revalidation_inputs` from
the root object, `readback_validation_summary`, or `matrix_run_summary`, then
runs the same stored-evidence validation path. This mode does not run new live
SDK or provider calls. Compact summaries also expose
`summary_revalidation_inputs`, `summary_revalidation_ready`,
`summary_revalidation_missing_fields`, and `summary_revalidation_cli_args` so a
controller can discover the summary-file revalidation command without deriving
it from `matrix_output_json` or the current `--output-json` path. When
`--revalidate-from-summary-json` is itself used without a new `--output-json`,
the resulting compact summary should preserve the source summary path as the
next `summary_revalidation_inputs.revalidate_from_summary_json`, so a
controller can repeat stored-evidence validation from compact stdout alone.
Stored-evidence matrix revalidation also supports
`--record-eval-case-on-failure`; when a validation fails, the script records a
client-scoped `/eval-cases` regression seed with the aggregate task id,
matrix JSON path, failed checks, evidence refs, and a non-secret verifier
command that reruns the same stored-evidence matrix validation without new live
SDK/provider calls. Compact validation summaries expose `eval_case_recording`
so controllers can see the eval case id, failure label, source task, and
verifier command without opening the full payload. The script should also
refetch client-scoped `/eval-cases` after recording and expose readback status,
validity, failed checks, match strategy, and the matched eval case id, so a
controller does not have to trust the POST response alone. Before posting, it
should check the same read model for an existing eval case with the same source
task, failure label, verifier command, evidence refs, and expected status; when
one is found, compact output should report `status=already_recorded` with
`duplicate_prevented=true` instead of creating another regression seed. The
same readback summary should expose `readback_matching_eval_case_count` and
`readback_matching_eval_case_ids` so controllers can see whether older
duplicate rows already exist for the same failure signature. New failure eval
cases should store a deterministic `metadata.failure_signature`, and compact
output should mirror it as `eval_case_recording.failure_signature`, so external
controllers can group repeated stored-evidence failures without reparsing the
full eval-case payload. The signature should use a canonical verifier command
that ignores transport/output flags such as `--base-url`, `--client-id`, and
`--compact-output`; raw verifier command differences from those flags may be
reported as non-blocking readback diagnostics, but they should not create a new
failure group. Readback summaries should also report whether matching eval-case
rows have current, missing, or mismatched stored signature metadata, so older
rows can be grouped by the computed signature while still surfacing metadata
drift. The signature metadata and compact output should include a
`failure_signature_version` and the canonical `failure_signature_material` used
for hashing, so controllers can audit future signature-rule changes without
reverse-engineering the script. Compact readback should also expose
`failure_signature_material_digest` and checks that the digest matches
`failure_signature` and the signature version is current. When the pre-recording
duplicate lookup does not find a match or fails, the eventual `recorded` result
should preserve `dedupe_lookup_*` diagnostics so controllers can distinguish
"no duplicate existed" from "duplicate prevention could not be verified before
POST". Eval-case recording output should also expose `post_attempted` plus
`post_skipped_reason` or `post_required_reason`, so a controller can tell from
compact output whether the script posted a new row or skipped POST because a
matching client-scoped eval case already existed.
The CLI also exposes `harness_api.py eval-case-verify` as the local bridge from
stored regression seed to runnable verifier. It reads the client-scoped
`/eval-cases` list, selects by eval-case id, name, or failure label, and reports
the stored command without executing it unless `--execute` is supplied. With
`--execute --cwd <absolute-cwd>`, it runs the stored `verifier_command`, returns
stdout/stderr/return code, and exits nonzero on verifier failure. Treat this as
stored harness regression verification; it is not live provider evidence unless
the verifier command itself performs and records a provider probe. When supplied
with `--record-result-task-id <task_id>`, the CLI posts the selected verifier
result back to `/tasks/{task_id}/observations` as an
`eval_case_verification` observation, preserving the eval-case id, selected
metadata, expected-status comparison, failed checks, command output tail, and
stored evidence refs.
Stored `eval_case_verification` observations are projected by both
`/sdk-events` and `/client-transcript` as `agent_eval_case_verification`
records. Their summaries include verification count, failed count, and
failure-label counts so controllers can see which regression seed was rerun and
whether it still fails without reparsing the raw observation. This remains
stored harness verification evidence only; it does not prove current provider
connectivity unless the verifier itself probes and records that provider.
Live and validation compact summaries also expose scenario evidence-holder readback depth via
`scenario_publish_readback_count`,
`scenario_publish_readback_status_by_scenario`,
`scenario_generic_readback_check_counts`, `scenario_projection_check_counts`,
`scenario_event_replay_check_counts`, `scenario_total_readback_check_counts`,
`scenario_readback_task_bindings`, `scenario_trace_artifact_digest_matches`,
`scenario_trace_artifact_bindings`, `scenario_artifact_summaries`,
`published_scenario_generic_check_counts`,
`published_scenario_projection_check_counts`,
`published_scenario_event_replay_check_counts`,
`published_scenario_total_check_counts`, `published_scenario_task_bindings`,
`published_scenario_trace_artifact_digest_matches`,
`published_scenario_trace_artifact_bindings`, and
`published_scenario_artifact_summaries`
where applicable. They also expose sanitized SDK session details through
`scenario_session_summaries` and `published_scenario_session_summaries`,
including backend, SDK session id, turn counts, persisted item counts, item
type counts, turn phases, status, and evidence refs without copying raw session
item previews or local session database paths. They also expose session stored
evidence through `scenario_session_evidence_summaries`,
`scenario_readback_session_evidence_summaries`, and
`published_scenario_session_evidence_summaries`, proving that the stored
`agent_session` observation and readback session summary still agree on backend,
turn count, item count, phases, and stateful-session signals while omitting raw
session previews, local session database paths, observation payloads, call ids,
observation ids, and database ids. They also expose stream stored evidence
through `scenario_stream_evidence_summaries`,
`scenario_readback_stream_evidence_summaries`, and
`published_scenario_stream_evidence_summaries`, proving that stored
`agent_stream_event` observations and readback stream count maps still agree on
event types, event names, raw event types, item classes, tool names, and
stream-handoff signals while omitting raw stream items, raw response events,
model output text, observation payloads, call ids, observation ids, and database
ids. They also expose
`scenario_stream_projection_summaries` and
`published_scenario_stream_projection_summaries` with SDK-event and
client-transcript stream-event counts plus event-type, event-name,
raw-event-type, item-class, and tool-name count maps, so streamed handoff
tool-name preservation is visible from compact stdout. They also expose raw event replay expected/observed event count maps,
SSE event-name maps, raw event-type maps, status maps, actor maps, matching
aggregate raw replay totals, matching coverage event totals, and tool-call status-update expected/observed counts and totals, so replay
coverage and raw replay composition can be explained from compact stdout. They also expose stage-specific failed-check maps such as
`scenario_generic_readback_failed_checks`, `scenario_projection_failed_checks`,
`scenario_event_replay_failed_checks`, `scenario_readback_failed_checks`,
`published_scenario_generic_failed_checks`,
`published_scenario_projection_failed_checks`,
`published_scenario_event_replay_failed_checks`, and
`published_scenario_stage_failed_checks`, so controllers can inspect scenario
generic readback, read-model projection, and event replay coverage or failures
without opening the full matrix JSON.
The same compact surfaces expose raw replay `event_count` maps, replay
count-map totals, aggregate count-map totals, and per-scenario count-total
check maps under `scenario_event_replay_*`,
`scenario_readback_event_replay_*`, `matrix_readback_event_replay_*`,
`matrix_payload_readback_event_replay_*`, `aggregate_event_replay_*`, and
`published_scenario_event_replay_*` fields, so controllers can verify raw
replay distribution consistency and aggregate expected/observed event coverage
from stdout without summing name/type/status/actor maps or opening the full
validation payload.
Agents SDK trace ingestion now preserves run `phases` as typed
`agent_run_phase` observations, and both `/sdk-events` and `/client-transcript`
project them as `agent_phase` records. This keeps approval/resume boundaries
such as `initial` and `after_approval` visible without depending on prose
summaries or raw trace inspection.
Approval-required SDK tool calls are recorded as ordinary tool-call evidence
with `approval_required=true` and an initial queued status; the separate
approval request/decision observations preserve the interruption boundary, and
the later tool output observation completes the tool call.
The matrix compact controller surface also exposes sanitized base tool-call
state as `scenario_tool_state_summaries`, including tool call/output counts,
expected tool names, paired call/output records, phase counts, sanitized
call/output summaries, argument keys, output keys, and marker-presence booleans
without copying raw prompts, final text, tool argument values, or tool output
values. Stored harness evidence is mirrored through
`scenario_tool_evidence_summaries`,
`scenario_readback_tool_evidence_summaries`, and
`published_scenario_tool_evidence_summaries`, including trace-ingest tool-call
counts, observation counts, tool-call name counts, observation-tool counts,
readback evidence count coverage booleans, id counts, and expected
`record_signal` call/output-observation booleans without copying raw
observation payloads, tool arguments, tool outputs, call id lists, observation
id lists, or database primary-key values. Read-model tool projection is mirrored through
`scenario_tool_projection_summaries`,
`scenario_readback_tool_projection_summaries`, and
`published_scenario_tool_projection_summaries`, so controllers can compare
SDK-event and client-transcript tool-progress/phase/model-response/final-output
counts directly from compact stdout. This tool projection is scoped to the base
`tool` smoke scenario; approval-required tool calls remain under approval
summaries.
The matrix compact controller surface also exposes sanitized approval state as
`scenario_approval_state_summaries`, including request/decision counts,
approved decisions, `initial` and `after_approval` phase evidence, phase
interruption counts, sanitized call/tool/agent/argument-key summaries, and
booleans for observed interruption and approved resume without copying raw
approval items, prompts, or tool outputs. Stored approval evidence is mirrored through
`scenario_approval_evidence_summaries`,
`scenario_readback_approval_evidence_summaries`, and
`published_scenario_approval_evidence_summaries`, including
`approval_required_signal` tool-call counts, approval request/tool-output/
decision observation counts and booleans, readback evidence count coverage,
and id counts without copying raw approval items, tool arguments, tool outputs,
observation payloads, call id lists, observation id lists, or database
primary-key values. Read-model approval projection is
mirrored through `scenario_approval_projection_summaries`,
`scenario_readback_approval_projection_summaries`, and
`published_scenario_approval_projection_summaries`, so controllers can compare
SDK-event and client-transcript approval/phase/tool-progress/final-output
counts directly from compact stdout.
The same matrix compact surface exposes sanitized non-stream handoff state as
`scenario_handoff_state_summaries`, including handoff call/output/pair counts,
source and target agent names, handoff tool names, expected source/target/tool
booleans, phase counts, sanitized call/output summaries, and paired
call/output records without copying raw prompts, final text, or handoff output
payload values. Stored handoff evidence is mirrored through
`scenario_handoff_evidence_summaries`,
`scenario_readback_handoff_evidence_summaries`, and
`published_scenario_handoff_evidence_summaries`, including `agent_handoff`
observation counts and booleans, readback evidence count coverage, and id
counts without copying raw handoff items, handoff output payloads, observation
payloads, call id lists, observation id lists, or database primary-key values.
Read-model handoff projection is mirrored through
`scenario_handoff_projection_summaries`,
`scenario_readback_handoff_projection_summaries`, and
`published_scenario_handoff_projection_summaries`. Approval projection is kept
approval-specific and should not appear for handoff-only scenarios just because
phase records exist.
Trace ingestion also preserves SDK-managed session evidence as typed
`agent_session` observations, and both read models project session backend,
session id, turn counts, persisted item counts, item types, evidence refs, and
per-turn summaries. This distinguishes SDK session state from the harness task
session metadata.
Trace-ingested `agent_model_response` observations are also projected by both
`/sdk-events` and `/client-transcript`, preserving response id, request id,
model, provider, API style, usage, output summary, evidence refs, and the
historical-evidence claim boundary in SDK-like read models. When an Agents SDK
raw response omits a per-response model, trace ingestion falls back to the
trace-level model and records `model_source`; when older observations lack
provider/API-style fields, the projections can recover them from matching
`agent_trace` artifact metadata.
Trace-ingested Agents SDK streaming events are recorded as typed
`agent_stream_event` observations and projected by both read models, preserving
stream index, event type, event name, raw response event type, run item/tool
metadata, agent name, and evidence refs. Streamed handoff call/output events
preserve the handoff tool name through the shared call id. This makes
`Runner.run_streamed` behavior replayable without requiring controllers to
parse the full trace artifact.
Stored `agent_question` observations are also projected by both read models,
preserving missing-information/interruption questions, reason, evidence status,
source observation, agent, and evidence refs so waiting-for-user boundaries do
not disappear into prose summaries.
Stored `agent_guidance` observations are also projected by both read models,
preserving backend recovery guidance, required action, suggested tool calls,
close-fit search budget, recovery policy, agent, status, and evidence refs. This
keeps "continue searching/enriching before reporting" signals visible to SDK-like
controllers without treating the guidance itself as proof of live provider
connectivity.
Stored `eval_case_verification` observations are projected by both read models,
preserving eval-case id, source task id, failure label, verifier command,
expected-status match, failed checks, execution status, return code, status, and
evidence refs. This lets SDK-like controllers consume regression rerun outcomes
as first-class events instead of scraping prose.
Stored harness verification results from `verification.completed` events are
also projected by `/sdk-events` and `/client-transcript` as
`harness_verification_result` records, preserving command, executed command,
status, return code, duration, short stdout/stderr tails, error payload, and a
historical-evidence claim boundary. This lets controllers replay verifier
outcomes without treating verifier logs as proof of current provider
connectivity.
The script also exposes control/evidence write commands for `progress`,
`queued-message`, `cancel-queued-message`, and `permission-decision`, covering
the SDK-style task progress, deferred input, cancellation, and permission
decision surfaces without requiring external controllers to craft ad hoc HTTP
payloads.
It also exposes scheduled-loop commands for `loop-create`, `loop-tick`, and
`loop-evidence`, so recurring harness checks can be created, advanced, and
audited through the same client-id-aware script instead of direct HTTP calls.
The CLI defaults `loop-tick` to `start=false` so smoke tests can advance loop
state and capture iteration evidence without accidentally launching a delegated
Codex run; callers can opt into execution with `--start true`.
The script also exposes `reply` and `worker-tick`, covering resumable task
continuation and client-scoped worker claims without requiring SDK-style
controllers to craft direct HTTP requests for those route surfaces.
Bounded `events` SSE replay now treats read timeout as a completed partial
replay with `timed_out=true`, preserving already received events and `next_after`
instead of crashing long-poll style clients on paused or idle tasks. The
client-side `harness_api.py events` replay envelope also includes SSE event-name,
raw harness event-type, status, and actor count maps so controllers can inspect
event coverage without parsing the raw `events` list.
The CLI `plan` helper includes a `trace` goal, so Agents SDK trace ingestion
workflows can discover the evidence-holder task, `trace_ingest.py`, evidence,
SDK-event, transcript, and eval-case commands from the same client-id-aware
entrypoint as provider, task, loop, and failure workflows. The trace plan also
advertises the live SDK smoke command, ingest-time `--readback` validation, and
`--readback-only` digest validation so controllers can follow the same
trace-capture and revalidation path without relying on chat memory.
`trace_ingest.py` dry-run and execution output include a compact
`trace_ingest_action_summary` with action, endpoint, artifact kind, tool-call,
tool-call status-update expected, observation-tool, status, and call-id counts. This lets SDK-style controllers
quickly verify what will be written, or what was attempted. Execution output
also includes `trace_ingest_result_summary` with stored artifact ids,
observation ids, tool-call primary keys, tool-call status-update expected
counts, status counts, and returned call ids,
so controllers can detect whether the ingestion API accepted the expected
evidence before fetching the authoritative task evidence and SDK read models.
When `trace_ingest.py --readback` is used, the script also fetches
`/evidence`, `/sdk-events?summary=true`,
`/client-transcript?summary=true`, and raw `/events?after=0` SSE replay
immediately after ingestion and returns a
`trace_ingest_readback_validation` block comparing stored evidence counts with
the SDK-event and transcript summaries. When event replay is present, the same
validation also checks replay task binding, completion, timeout status, cursor
advancement, and artifact/observation/tool-call/status-update event coverage
against trace-ingest result counts. The event replay fetch size is derived from
the SDK-event summary `raw_event_count` when available, with evidence counts as
fallback, so large trace-ingest tasks are not truncated by the default SSE event
limit; the resulting `fetch_plan` is preserved in readback and validation
payloads. It also returns compact
`artifact_summaries` with artifact id, kind, path, digest, status, and a
sanitized metadata subset such as trace id, provider, SDK, model, status, and
item type, so controllers can identify the stored trace artifact without
fetching full evidence or exposing raw approval/interruption item payloads. It
also returns `agent_session_summaries` with backend, SDK session id, turn
counts, persisted item counts, item type counts, turn phases, status, and
evidence refs, while omitting raw session item previews and local session
database paths. The comparison covers model responses,
stream events, phases, sessions, handoffs, approvals, questions, guidance,
guardrails, run errors, and SDK final-output coverage where those surfaces were
ingested. For stream events it also compares event-type, event-name, raw-event
type, item-class, and tool-name distribution maps. For model-response evidence
it also compares provider and API-style count maps derived from stored
`agent_model_response` observations, falling back to `agent_trace` artifact
metadata for older observations, against both SDK-event and client-transcript
summaries. If model responses exist but provider/API-style attribution is empty
or only `none`, the standalone trace-ingest readback gate fails. The validation
payload includes `check_count`, `failed_checks`, `event_replay_validation`,
and expected/observed replay event counts when raw event replay is fetched, so
controllers do not need to derive failure names from the raw boolean map. This
is a controller convenience over
stored evidence; the read models remain projections, not a live provider
connectivity claim. Controllers can add `--fail-on-readback-invalid` to turn a
failed readback validation into a nonzero process exit for CI-style smoke
checks.
Use `trace_ingest.py --readback-only` when a controller already has an ingested
trace task and needs to revalidate stored evidence, SDK-events, client
transcript summaries, and raw event replay without writing new artifacts,
observations, or tool calls. In that mode the script derives expected result
counts from the trace file, performs only readback requests, and can still be
combined with `--compact-output`, `--output-json`, and
`--fail-on-readback-invalid` for CI-style gates. The readback validation also
compares the expected trace file digest with stored `agent_trace` artifact
digests, so a task with matching counts but a different trace artifact does not
pass the readback-only gate.
`trace_ingest.py --compact-output` prints a `trace_ingest_compact_summary` to
stdout with action/result counts, readback validity, failed checks, task
binding, provider/API-style projection maps, stream distribution maps from
evidence, SDK-events, and client transcript summaries, artifact/session
summaries, raw event replay coverage, and raw replay name/type/status/actor
count maps. Use `--output-json` with compact mode
when the full payload with detailed actions, results, and readback envelopes
should be preserved for later inspection.
`GET /api/harness/v1/tasks/{task_id}/sdk-events?summary=true`,
`GET /api/harness/v1/tasks/{task_id}/client-transcript?summary=true`, and the
matching `harness_api.py ... --summary true` commands return compact read-model
summaries with event/message counts, `summary_requested=true`, `agent_guidance`
counts, unique reasons, reason-count maps, missing-info question counts,
phase/session/handoff/approval/guardrail/run-error counts, model-response
provider/API-style counts, stream distribution maps, harness verification
result counts/status maps, final-output flags, cursor metadata, and an explicit
historical-evidence claim boundary. The transcript
summary also exposes an aggregate `item_type_counts` map across message and
top-level transcript projections.
These summaries are for quick controller checks; the full `/sdk-events` and
`/client-transcript` payloads remain the authoritative read models.
The CLI forwards `--summary true` to the server query parameter and preserves an
already summarized response instead of fetching the full read model and
re-summarizing it locally. When a local fallback summary is needed, the CLI
uses the same `developer_harness.read_model_summary` functions as the FastAPI
runtime so script and server summary contracts do not drift.
`backend_harness/publish_live_agent_smoke_export.py` uses the same broad
projection-count contract when publishing live-smoke trace exports: it derives
minimum counts for model responses, stream events, phases, sessions, handoffs,
approvals, questions, guidance, guardrails, run errors, and final output from
the trace-ingest actions, then checks both SDK-event and client-transcript
readback summaries before accepting the publish result. It also compares
model-response provider/API-style maps and stream distribution maps before
accepting the publish result, so unattributed model-call evidence is rejected
even outside the matrix wrapper. Publisher readback also binds the expected
trace file digest to the stored `agent_trace` artifact, so a publish with
matching counts but a different trace artifact fails validation. It also
validates raw `/tasks/{task_id}/events` replay coverage for the stored artifact,
tool-call, and observation events before accepting the publish result.
`--readback-only --task-id ...` reruns the same stored-evidence validation
without posting new artifacts, observations, tool calls, or eval cases.
Publisher readback validation reports `check_count` and `failed_checks`, and
`--compact-output` prints the gate decision, revalidation readiness, non-secret
readback-only CLI args, summary-file revalidation args, record-result status,
and replay counts for controller logs.
When publisher `--compact-output` is paired with `--output-json`, the JSON file
must still preserve the full payload and also mirror the compact gate at the
root under fields such as `readback_valid` and under `publish_summary` /
`compact_summary`, so file-only controllers do not need stdout to make the same
gate decision.
The same capability is advertised through
`loop_engineering_contract.read_model_summaries` and the structured
`read_model_contract` on `/api/harness/v1/capabilities`, so external
controllers can discover summary views, projection types such as
`agent_eval_case_verification` and `harness_verification_result`, transcript
sections, and summary fields before choosing a readback strategy.
The same discovery is available from the CLI through
`harness_api.py capabilities --summary true` for controller logs that should
avoid dumping the full capabilities catalog.
Controllers can add `--require-sdk-event-type`,
`--require-transcript-section`, and `--require-summary-field` to make that
capabilities check fail closed when an expected projection surface disappears.
The CLI `plan` helper should surface this gate for task, trace, and eval
workflows so controller loops validate read-model support before depending on
projection summaries.

Continue generic harness work from `C:\com2us\developer_harness` when possible.
That directory now has its own `AGENTS.md` so Codex sessions rooted there can
work on the harness without using `backend_harness` as the primary working root.
Use `backend_harness` only when verifying the mounted FastAPI integration or
when a task explicitly changes the FitCheck product runtime.

Current templates:

- `agents_sdk_trace_eval_codex`: traces, feedback, eval targets, and verifier
  commands become a Codex task prompt for improving the agent harness.
- `backend_owned_tool_loop_hardening`: stored observations, tool schemas,
  provider status, and focused verifiers drive backend-owned tool-loop changes.

Task transcript projection includes:

- a compact status line with task status, state version, client id, model,
  agent, sandbox, workspace mode, lease, latest event id/seq, evidence counts,
  queued message counts, subagent counts, and verification status;
- stable `uuid` and `session_id` values for user and streamlined assistant text
  messages extracted from the stored request and Codex JSONL events;
- deterministic tool-use summary rows paired with recorded observations;
- permission decision records for tool calls, including allow/ask/deny/
  passthrough behavior and decision reason;
- hook progress and verification summaries;
- subagent handoff summaries for child tasks spawned from the parent;
- source endpoint links for task, events, SDK-event projection, evidence,
  transcript, task progress, and subagents.

SDK event projection:

- `GET /api/harness/v1/tasks/{task_id}/sdk-events` projects raw harness events
  into client-friendly system events such as `task_started`, `task_progress`,
  `task_notification`, and `session_state_changed`.
- The projection preserves source event ids and supports an `after` cursor so
  SDK-like clients can resume without parsing raw `harness_event` SSE payloads.
- The `after` cursor is based on raw `harness_events.event_id` with exclusive
  semantics. Responses expose `cursor_policy`, `projected_event_count`,
  `terminal_event_id`, `terminal_event_included`, and `final_output_included`
  so clients can distinguish full history, terminal-inclusive deltas, and
  already-caught-up responses.
- These events are a read model over harness evidence; raw append-only events
  remain the durable audit log.
- Recorded tool calls are also projected as SDK-like `tool_progress` messages
  with tool use id, tool name, parent tool use id, elapsed time, status, uuid,
  and session id.
- Recorded `agent_handoff` observations are projected as SDK-like
  `agent_handoff` messages with source agent, target agent, handoff tool name,
  call id, item/raw type, arguments or output when present, agent metadata,
  evidence refs, status, uuid, and session id.
- Recorded `agent_approval_request` and `agent_approval_decision` observations
  are projected as SDK-like `agent_approval` messages with approval subtype,
  requested tool name, call id, decision, approval arguments, agent/item
  metadata, evidence refs, status, uuid, and session id.
- Recorded `agent_guardrail_result` observations are projected as SDK-like
  `agent_guardrail` messages with guardrail subtype, guardrail name, tripwire
  status, behavior, output info, agent output, reason, evidence refs, status,
  uuid, and session id.
  A tripwire-triggered guardrail is recorded as failed policy evidence, while
  the raw model response remains separate evidence that a provider call occurred.
- Recorded `agent_guidance` observations are projected as SDK-like
  `agent_guidance` messages with guidance type, reason, candidate key, missing
  fields, required action, ask-user allowance, suggested backend tools,
  close-fit search budget, recovery policy, agent, evidence refs, status, uuid,
  and session id. Trace ingestion should create this observation for top-level
  guidance records and for guidance-like trace items such as `agent_guardrail`
  records carrying `result.guidance_type` or `next_tool_suggestions`.
- Recorded `agent_run_error` observations are projected as SDK-like
  `agent_run_error` messages with error type, error text, scenario, trace id,
  provider-call flag, evidence refs, status, uuid, and session id. This keeps
  tripwire failures visible without collapsing raw model output or guardrail
  evidence into a single terminal message. If the SDK trace contains final
  output text despite a tripwire, keep it as separate `agent_final_output`
  evidence rather than treating it as a successful run.
- Recorded `verification.completed` events project as SDK-like
  `harness_verification_result` messages with command, executed command,
  status, return code, duration, short stdout/stderr tails, error payload, uuid,
  and session id. These are stored harness verifier results, not live provider
  connectivity claims.
- Recorded `permission_decision.recorded` events project as SDK-like
  `permission_decision` messages with behavior, tool name, tool use id,
  decision reason, blocked path, suggestions, and summary.
- Queued input events `task.message_queued`, `task.message_dequeued`, and
  `task.message_cancelled` project as SDK-like `task_control` messages so
  external controllers can replay deferred input lifecycle changes without
  separately parsing raw SSE.
- Terminal task events with `result.final_output` or `result.last_message`
  produce a separate SDK-like `final_output` message. This keeps final output
  replayable as its own evidence surface instead of hiding it only inside
  `task_notification` prose.
- Trace-ingested `agent_final_output` observations also project as SDK-like
  `final_output` messages with `output_source=observation.agent_final_output`
  and their evidence refs preserved. This keeps Agents SDK final output distinct
  from the task process result.
- Trace-ingested `agent_model_response` observations project as SDK-like
  `agent_model_response` messages with response id, request id, model,
  provider, API style, usage, output summary, and evidence refs preserved.
  These are stored trace evidence only and do not prove that a provider is
  currently connected.
- Trace-ingested `agent_session` observations project as SDK-like
  `agent_session` messages with backend, SDK session id, turn count, stored
  item count, item types, evidence refs, uuid, and session id.
- Trace-ingested `agent_stream_event` observations project as SDK-like
  `agent_stream_event` messages with stream index, stream event type/name, raw
  response event type, run item/tool metadata, agent, evidence refs, uuid, and
  session id. Streamed handoff output items should retain the original handoff
  tool name through the shared call id.
- `/client-transcript` also includes `agent_model_responses` with the same raw
  response metadata, provider/API-style fields, and claim boundary, so external
  controllers that consume only the compact transcript do not need to parse raw
  observations to find model-call usage evidence.
- `/client-transcript` also includes `agent_final_outputs` from stored
  `agent_final_output` observations, and transcript summaries expose
  `final_output_count`.

SDK initialization snapshots:

- `GET /api/harness/v1/sdk/initialize` returns a client-scoped SDK-style
  startup snapshot with commands, agents, output styles, model/provider
  templates, account/auth source metadata, process id, and links to MCP/context
  status endpoints.
- `GET /api/harness/v1/sdk/mcp-status` returns a Claude-SDK-shaped MCP status
  response. It only reports catalog evidence and must not be treated as proof
  of live MCP server connections.
- `GET /api/harness/v1/sdk/context-usage` returns a client-scoped usage
  estimate from stored tasks, events, and evidence. It is a harness estimate,
  not tokenizer-backed model context accounting.
- When trace ingestion records `agent_model_response` observations with provider
  usage metadata, `/sdk/context-usage` exposes an `apiUsage` summary aggregated
  from that stored evidence. This is historical trace evidence only and does
  not prove that a live provider is currently connected.
- The context usage read model also exposes source task ids, event count,
  evidence counts, per-task usage maps, per-model/per-status/per-provider/
  per-API-style token and request-count usage maps, aggregate request counts,
  per-response request counts, and aggregate evidence-ref counts so
  SDK-style controllers can verify where a usage summary came from before
  treating it as trace evidence.

Queued input control:

- `POST /api/harness/v1/tasks/{task_id}/queued-messages` records a durable
  follow-up prompt with Claude-style `now`, `next`, or `later` priority.
- `POST /api/harness/v1/tasks/{task_id}/queued-messages/cancel` marks a queued
  message as cancelled by `message_uuid`, mirroring the SDK
  `cancel_async_message` shape without deleting replay evidence.
- The next resumable task run drains queued messages by priority order
  (`now`, then `next`, then `later`) and records queued/dequeued/cancelled
  events. This is an external-controller queue contract; it does not pretend to
  implement Claude's full mid-tool streaming interrupt semantics.

Subagent handoff:

- `POST /api/harness/v1/tasks/{task_id}/subagents` creates a child task with
  `parent_task_id`, preserving client isolation and the requested join policy.
- `GET /api/harness/v1/tasks/{task_id}/subagents` returns child status,
  state version, thread id, join policy, agent/model/workspace metadata,
  prompt preview, final message preview, return code, and error summary.
- Parent task evidence and client transcript projections include the same
  child summaries so external controllers can decide whether a parent is ready
  to join child results without scanning all task rows.

Permission evidence:

- `POST /api/harness/v1/tasks/{task_id}/permission-decisions` records a
  structured permission decision for a tool call.
- Supported behaviors are `allow`, `ask`, `deny`, and `passthrough`, with
  optional updated input, decision reason, blocked path, suggestions, and
  metadata.
- The harness records these as evidence and replay events. It does not yet
  enforce a full permission engine; callers that execute tools remain
  responsible for honoring the decision.

Verification command portability:

- Harness verification results store both the requested `command` and the
  actual `executed_command`.
- If a verification command starts with `python` but the host has no `python`
  alias, the runner executes it with the current Python interpreter. This keeps
  verifier evidence portable across WSL/Linux/venv setups without rewriting the
  user-provided command stored in task evidence.
- Verification results are exposed through both read-model summaries as
  `harness_verification_result_count`, `harness_verification_failed_count`, and
  `harness_verification_status_counts`.

Task progress evidence:

- `POST /api/harness/v1/tasks/{task_id}/progress` records a TodoWrite-style
  task progress snapshot with `content`, `active_form`, and `status` for each
  item.
- Progress snapshots are append-only evidence and replay events, not proof that
  the delegated worker actually completed the listed work.
- The client transcript includes the latest progress snapshot, counts, and a
  verification nudge flag when a 3+ item list is closed without any verification
  item.

Session metadata and fork:

- `GET /api/harness/v1/sessions` returns a client-scoped, paginated session
  list derived from harness tasks.
- `GET /api/harness/v1/tasks/{task_id}/session-info` returns SDK-like metadata:
  session id, thread id, title, custom title, tag, summary, cwd, model, agent,
  status, state version, timestamps, fork origin, and evidence counts.
- `POST /api/harness/v1/tasks/{task_id}/annotations` updates title, tag, and
  summary with optimistic `state_version` control.
- `POST /api/harness/v1/tasks/{task_id}/fork` creates a new queued task from
  the source task request with `metadata.forked_from_task_id`. It is a harness
  task-spec fork, not a byte-for-byte transcript fork.

Task metadata can select a template:

```json
{
  "metadata": {
    "agentic_loop_template": "agents_sdk_trace_eval_codex",
    "agentic_loop": {
      "trace_sources": ["harness_events", "agent_run_store"],
      "eval_targets": ["tool_loop_regression"],
      "verification_commands": ["python -B verify_production_agent_runtime.py"]
    }
  }
}
```

When present, the development harness injects an "Agentic loop harness mode"
block into the delegated Codex prompt. That block requires the delegated run to
inspect trace/store/API evidence, identify the smallest agent or harness gap,
turn gaps into verifiers or evals when possible, patch the runtime or harness,
rerun named verification commands, and report only evidence-backed claims.

This is development orchestration only. It does not make the FitCheck
production route agentic by itself, and it does not prove that any LLM, search,
OCR, or Agent SDK provider is connected.

## Anti-Patterns

- A fixed deterministic sequence presented as an AI agent.
- In-memory retry loops without durable checkpoints.
- Whole-run JSON blob overwrites under concurrent workers.
- SSE IDs derived from trace count instead of event IDs.
- Tools that return prose without structured evidence.
- Asking the user to solve ordinary search/tool recovery.
- Monolithic instruction files that crowd out task context.
- Background jobs with no lease, heartbeat, cancellation, or replayable events.
- Agent reviews that are not turned into evals or mechanical checks.

## Maturity Levels

Level 0: script harness

- One command, one output, manual inspection.

Level 1: observable harness

- Structured events, artifacts, verification commands.

Level 2: durable harness

- State versions, append-only events, worker leases, resume, cancellation.

Level 3: agentic harness

- Model-selected tools, backend-owned execution, persistent observations,
  human interrupts, trace-grounded final outputs.

Level 4: multi-agent harness

- Child tasks, join policies, handoffs, budgets, isolated write scopes.

Level 5: self-improving harness

- Trace-to-eval flywheel, automated harness-change proposals, regression gates,
  and documented convergence history.

## Acceptance Matrix

- API: versioned endpoints, idempotency key, authenticated caller identity,
  optimistic concurrency, health/ready.
- LLM provider: explicit profile, local/cloud distinction, `/models` probe,
  tool-calling compatibility check, no hidden fallback from production agent
  behavior to mock parsing.
- Events: append-only ordered replay, `Last-Event-ID`/cursor support, actor and
  parent span metadata.
- Runtime: max turns, max wall time, cancellation, worker lease expiry recovery.
- Tools: schema validation, timeouts that actually stop subprocess work,
  structured errors, evidence references.
- Hooks: typed lifecycle events, timeout, failure isolation, execution records.
- Skills: catalog, progressive disclosure, dependency metadata, version/checksum.
- Subagents: parent-child links, join policy, result handoff, cancellation.
- Verification: commands/evals persisted as artifacts and visible in final state.
- Security: workspace boundary, sandbox mode, permission policy, secret redaction.
- Separation: product-specific agents must not import the generic harness runtime
  as a hidden product behavior path unless explicitly wired.

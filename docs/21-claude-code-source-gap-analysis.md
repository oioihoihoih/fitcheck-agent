# Claude Code Source Gap Analysis for com2us Agent Runtime

Date: 2026-06-14

## Scope

This document compares the current com2us FitCheck production agent runtime and
developer harness with the historical Claude Code source under `C:\study\src`.
It focuses on SDK-like agent construction behavior, not FitCheck product UX.

Current com2us baseline:

- Product route: `backend_harness\app\routes\agent.py`
- Product runtime: `backend_harness\app\agent_runtime.py`
- Product tool registry: `backend_harness\app\tool_registry.py`
- Product LLM adapter: `backend_harness\app\tools\llm_client.py`
- Generic developer harness: `developer_harness`

Important current fact: com2us still uses a custom product loop rather than the
OpenAI Agents SDK orchestration layer. The OpenAI cloud transport now uses the
official OpenAI Python SDK for Responses-style calls, and OpenAI-compatible
local endpoints use the SDK transport by default when the package is installed.
The raw HTTP fallback remains available with `FITCHECK_LLM_USE_OPENAI_SDK=0`
for Ollama, LM Studio, vLLM, or other servers with SDK compatibility gaps.

## Source Areas Inspected

The Claude-style source has several mature agent-runtime surfaces that are only
partially present in com2us:

- `bootstrap/state.ts`: session identity, parent session lineage, global cost,
  API/tool duration, model usage, request capture, strict tool-result pairing,
  session persistence flags, invoked skill tracking, and tracing providers.
- `Tool.ts`: a broad `ToolUseContext` that carries tools, MCP clients,
  permission context, abort controller, messages, progress callbacks, file
  state, app state, agent id, and query-chain tracking.
- `services/tools/toolExecution.ts`: lifecycle around validation, permission
  checks, pre/post hooks, telemetry, progress, denials, duration, MCP metadata,
  and structured tool errors.
- `services/tools/StreamingToolExecutor.ts`: streaming-time tool execution with
  concurrency-safety gates, ordered result emission, sibling cancellation, and
  synthetic tool results on interruption or fallback.
- `services/api/claude.ts`: API request assembly, retry, request ids, usage and
  cost accounting, model feature gates, cache behavior, and message
  normalization including tool-result pairing.
- `utils/sessionStorage.ts`: JSONL transcript persistence, parent chains,
  resume support, progress-message exclusion, subagent transcript paths, and
  read-size guardrails.
- `services/compact/compact.ts`: token-aware compaction, pre/post compact
  hooks, file and skill restoration budgets, and prompt-too-long recovery.
- `tools/AgentTool/runAgent.ts`: subagent isolation with agent-specific MCP,
  cleanup, transcript recording, inherited context, and per-agent tools.
- `components/StatusLine.tsx`: model, permission mode, cost, duration,
  context-window usage, rate-limit, workspace, and session metadata surfaced as
  a compact live status line.
- `components/messages/GroupedToolUseContent.tsx`,
  `components/messages/HookProgressMessage.tsx`, and
  `components/messages/CompactBoundaryMessage.tsx`: transcript UI patterns for
  grouping tool calls with results, showing hook progress, and marking
  compaction boundaries.
- `components/PromptInput/PromptInputFooter.tsx` and
  `types/textInputTypes.ts`: prompt footer state, queued commands, command
  priority (`now`, `next`, `later`), meta messages, bridge-origin messages, and
  agent-specific queue routing.
- `tools/TodoWriteTool/*`, `tools/TaskCreateTool/*`, and
  `tools/TaskUpdateTool/*`: structured session task lists with `pending`,
  `in_progress`, and `completed` states, active-form text for live progress
  display, and verification nudges when work closes without verification.
- `utils/sdkEventQueue.ts` and `utils/task/sdkProgress.ts`: SDK-only system
  events for task started/progress/notification, session state changes, usage
  counters, summaries, and resumable event draining.
- `entrypoints/sdk/controlSchemas.ts` and `entrypoints/agentSdkTypes.ts`: SDK
  stdout/stdin/control message unions, session listing, transcript reads,
  resume/fork concepts, and streamlined tool-use summary messages.
- `entrypoints/sdk/coreSchemas.ts`: initialization response shapes for slash
  commands, subagent info, model info, account info, MCP server status, and
  context-usage breakdowns, plus `tool_progress` messages for live tool
  execution display.
- `services/toolUseSummary/toolUseSummaryGenerator.ts`: optional concise
  single-line summaries of completed tool batches for client UIs.

## Detailed Gaps

### 1. SDK Surface and Trace Shape

Claude-style runtime exposes a rich internal run shape: messages, stream events,
tool use, tool result, request ids, usage, cost, hooks, and transcript state are
all correlated. com2us stores useful trace entries, observations, final report,
and LLM status, but the product API did not expose one SDK-like read model for
replay, evals, or external controllers.

Implemented improvement:

- Added `GET /api/agent/runs/{run_id}/evidence`.
- Added `AgentRunEvidenceResponse`.
- The endpoint returns counts, trace, observations, grounding validation,
  citation badges, and an `sdk_like` block with `new_items`,
  `raw_response_ids`, guardrail results, final output, and tool-call/result
  pairing diagnostics.
- The runtime now accumulates Claude-style run usage accounting under
  `state.usage_accounting` and exposes it through run evidence and the client
  transcript summary. It tracks request count, API duration, provider
  input/output/total tokens, cache token details when the provider returns
  them, web-search request count, model breakdown, response/request ids, and
  cost status.
- USD cost is calculated only when explicit local pricing env vars are present:
  `FITCHECK_LLM_INPUT_COST_PER_1M`, `FITCHECK_LLM_OUTPUT_COST_PER_1M`,
  `FITCHECK_LLM_CACHE_READ_INPUT_COST_PER_1M`, and
  `FITCHECK_LLM_CACHE_CREATION_INPUT_COST_PER_1M`. No model price is hardcoded.
- SQLite-backed production runs now mirror trace steps into an append-only
  `agent_run_events` ledger with monotonic per-run `seq`, event type, status,
  name, session id, and raw step payload. Run evidence exposes this as
  `sdk_like.normalized_events` and reports `counts.normalized_events`.

Remaining work:

- Backfill normalized event ledgers for historical JSON-only run files if those
  need to be promoted into long-term eval fixtures.

### 1a. OpenAI Python SDK Transport

The first implementation used direct `requests.post` calls to `/responses` and
`/chat/completions`. That worked for OpenAI-compatible local servers, but it
meant the OpenAI cloud path was not using the official SDK surface.

Implemented improvement:

- Added `openai` to the Python dependencies.
- OpenAI cloud profiles now probe models with `OpenAI(...).models.list()`.
- OpenAI cloud Responses calls now go through
  `OpenAI(...).responses.create(...)` with the existing `instructions`, `input`,
  `tools`, `tool_choice`, `store`, and `previous_response_id` payload.
- Chat-completions calls go through
  `OpenAI(...).chat.completions.create(...)` by default when the SDK package is
  installed, including local OpenAI-compatible endpoints with a custom
  `base_url`.
- The status response now exposes `transport=openai_python_sdk` or
  `transport=requests_http_fallback`, so harness diagnostics can distinguish SDK
  usage from explicit raw HTTP compatibility fallback.
- OpenAI SDK status/connection/rate-limit exceptions are normalized back into
  the existing product provider-error contract. For example SDK 429 failures
  still surface as `HTTP_429_RATE_LIMITED`, `provider_error_category=rate_limit`,
  retryable, and resumable when pending tool outputs exist.
- SDK response and exception request ids are preserved as
  `provider_request_id` in response payloads, runtime trace entries, context
  budget records, run evidence, and the client transcript summary.
- Provider `usage` from SDK responses is preserved in context budget records and
  rolled up into run-level usage accounting.
- `verify_local_llm_tool_adapter.py` now injects a fake OpenAI SDK client and
  verifies `models.list`, `responses.create`, `previous_response_id`, Responses
  tools, `chat.completions.create`, chat tool schema conversion, SDK request-id
  preservation, and SDK rate-limit normalization without making a network call.

Remaining work:

- Consider the OpenAI Agents SDK only after the product loop needs handoffs,
  tracing export, or SDK-managed tool orchestration. For now, backend-owned tool
  execution is intentional.

### 2. Tool Result Pairing

Claude Code treats tool use and tool result pairing as a hard runtime contract.
It even has a strict mode that fails instead of repairing malformed trajectories.
com2us already records `call_id` on tool calls, tool outputs, and observations,
and it has malformed/unknown tool guardrails, but pairing was not summarized as
a first-class evidence signal.

Implemented improvement:

- The new evidence endpoint reports `tool_result_pairing.status`,
  `missing_results`, and `orphan_results`.
- The production runtime now checks pending `function_call_output` items before
  the next LLM request. If they do not match the latest model-selected tool
  calls, the run fails with `TOOL_RESULT_PAIRING_MISMATCH` and records a
  `stop_tool_result_pairing_mismatch` guardrail trace instead of sending a
  malformed trajectory to the model.

Remaining work:

- Decide whether resumable repair should ever be allowed for administrative
  recovery. The default product path should stay strict.

### 3. Streaming and Replay

Claude-style streaming executes tools as they stream in, buffers ordered
results, and cancels sibling tools when needed. com2us supports SSE polling and
parallel candidate analysis. Earlier SSE ids were derived from
`run_id:status:trace_count` and did not support cursor replay.

Implemented improvement:

- Product SSE events now expose `event_sequence` and use a monotonic
  `run_id:trace:{sequence}` event id based on the trace sequence.
- Reconnects can pass browser `Last-Event-ID` or `last_event_id` query
  parameter. The route resumes `trace_delta` after that sequence. Legacy
  `run_id:status:trace_count` event ids are still parsed for compatibility.
- SQLite-backed runs also mirror trace entries into normalized
  `agent_run_events` rows, so future consumers can replay from durable,
  monotonic event records instead of reparsing the run blob.

### 4. Tool Lifecycle, Permissions, and Hooks

Claude Code runs a full lifecycle around every tool: schema parsing, permission
resolution, pre-tool hooks, post-tool hooks, denial hooks, telemetry spans,
duration, progress, and structured failure conversion. com2us tools have
metadata, backend-owned execution, timeouts, provider retry badges, and
guardrails, but there is no general product permission/hook lifecycle.

Implemented improvement:

- Product runs now persist `state.hook_lifecycle` records for tool execution.
  The runtime records `PreToolUse`, `PostToolUse`, and `PostToolUseFailure`
  lifecycle entries with tool name, call id, status, duration, and summarized
  output.
- These records are explicitly `mode=record_only`. They are evidence for
  replay/evals and do not execute external hooks or route product behavior
  through the developer harness.
- The run evidence endpoint exposes hook lifecycle counts and records.

Next improvement:

- Decide which future hook points, if any, may block execution. Until then,
  keep product hooks record-only.
- Keep permission decisions explicit for external I/O tools such as web search,
  page fetch, OCR, and URL registration.

### 5. Context Management and Compaction

Claude Code has token-aware compaction, prompt-too-long recovery, restored file
and skill context budgets, and pre/post compact hooks. com2us currently relies
on its bounded state and tool observations. Local chat-completions continuity is
held in process memory in the LLM adapter, so restart or multi-worker behavior
can lose local model conversation history.

Implemented improvement:

- The product runtime now lets the LLM adapter prepare provider-specific input
  items before a request.
- The local chat-completions adapter reconstructs chat history from durable run
  `trace` and `state.observations` when pending tool outputs need to be sent.
  This gives Hugging Face/local OpenAI-compatible models an SDK-like assistant
  `tool_calls` plus `tool` result transcript even when the in-memory
  `previous_response_id` cache is unavailable.
- The runtime now records `state.context_budget` before each LLM request. These
  records include input kind, instruction/input character and byte sizes,
  estimated token counts, current trace/observation/hook counts, pending tool
  output count, and provider usage when the response includes it. The estimate
  is explicitly labeled `measurement=estimated`.
- The evidence endpoint exposes context budget counts and records for replay,
  evals, and local-model diagnostics.
- When estimated context size crosses
  `FITCHECK_CONTEXT_COMPACTION_ESTIMATED_TOKEN_THRESHOLD`, the runtime records a
  deterministic `state.context_compactions` evidence summary. It preserves the
  latest user input, recent observation summaries, pending tool output ids,
  loaded skill names, and grounding constraints.
- For non-tool-result initial requests that cross the threshold, the runtime now
  applies that deterministic compaction summary to the LLM input. Pending
  `function_call_output` batches are not compacted, so tool-result pairing stays
  intact. Applied records are marked `status=applied` and include the compacted
  estimated input-token count.

Next improvement:

- Replace the rough estimate with provider tokenizer counts when a reliable
  tokenizer is configured.
- Expand compaction beyond deterministic summaries only after live runs prove a
  summarizer or tokenizer-backed reducer is safe for grounded reports.

### 6. Subagents and Agent-Specific Tooling

Claude Code supports subagents with inherited context, per-agent MCP servers,
transcripts, cleanup, and agent ids. The developer harness has subagent and
loop support; the FitCheck product runtime intentionally remains a single
agent loop. That is appropriate until FitCheck needs specialist agents or
handoffs.

Next improvement:

- Keep subagent orchestration in `developer_harness`.
- Only escalate product runtime to SDK/subagents if there is a concrete product
  need such as separate web-research, closet-memory, or scoring specialists.

### 7. Local LLM SDK Reproduction

For a Hugging Face-downloaded local model, com2us should not try to reproduce
hosted OpenAI tools. The practical target is an SDK-like local runtime contract.
The official OpenAI Python SDK can be used as the transport when the local
server is compatible enough. FitCheck now uses that SDK path by default when the
package is installed, while keeping a raw HTTP fallback because local servers
differ in tool-call and error-shape behavior:

- Serve the model through an OpenAI-compatible server such as vLLM, TGI with
  an OpenAI shim, llama.cpp server, LM Studio, or Ollama.
- Probe `/models` before declaring it connected.
- Use `/v1/chat/completions` with function/tool calling if the server supports
  it.
- Keep all tool execution in the backend registry.
- Return configuration and tool-calling capability diagnostics separately from
  product result quality.
- Persist trace, observations, tool pairing, provider errors, and grounding
  evidence so local runs can be evaluated like cloud Responses runs.

Recommended local-model path:

1. Pick a model with tool-call reliability, not just general chat quality.
2. Serve it behind an OpenAI-compatible endpoint.
3. Set the com2us local LLM environment profile.
4. If the local server fails specifically through the SDK transport, set
   `FITCHECK_LLM_USE_OPENAI_SDK=0` to use the raw HTTP fallback.
5. Run `verify_local_llm_tool_adapter.py`.
6. Run a product agent run and inspect `/api/agent/runs/{run_id}/evidence`.
7. Add eval cases for malformed tool arguments, no-tool final answers, repeated
   failed tools, sparse search results, and grounded report citation coverage.

### 8. Chat UI/UX and SDK-Like Client Read Models

Claude Code does not expose agent work as only a raw debug log. It keeps the
conversation readable by separating several surfaces:

- a compact status line for model, cost/duration, context-window usage,
  workspace, permission mode, and rate-limit state;
- grouped tool-use content that correlates tool calls, in-progress state,
  progress messages, and tool results;
- hook progress messages and compact-boundary markers in the transcript;
- prompt footer state for loading, suggestions, queued commands, remote bridge
  state, and notifications;
- SDK stdout/control messages, including streamlined tool-use summaries for
  client surfaces that cannot render the whole transcript.

FitCheck's web chat already had messages, plan, trace, tool flow, latest
observation, and provider configuration diagnostics. The missing part was a
client-facing SDK-like read model that made the newer backend evidence visible
without forcing the user to inspect raw JSON.

Implemented improvement:

- The web chat now has a `Stream` panel showing SSE connection state, replay
  cursor, and monotonic event sequence.
- The browser client reconnects same-run SSE streams with `last_event_id`,
  while clearing the cursor when switching to a different run.
- The web chat now has a `Run Evidence` panel backed by
  `/api/agent/runs/{run_id}/evidence`. It summarizes trace count,
  observations, hook lifecycle records, context budget records, and compaction
  summaries.
- The evidence panel renders the latest context-budget record, compaction
  boundary, hook lifecycle record, tool-result pairing status, and provider raw
  response ids in the same compact style as trace/tool-flow panels.
- Added `GET /api/agent/runs/{run_id}/client-transcript` as a compact
  SDK-like read model. It returns a status line, stream cursor, evidence
  summary, `streamlined_text` messages, compact-boundary and hook-progress
  messages, and a `streamlined_tool_use_summary` with both `summary` and
  SDK-compatible `tool_summary`.
- Client transcript messages now include stable `uuid` values and `session_id`
  so downstream clients can render or diff the compact transcript without
  inspecting full trace JSON.
- Broken mojibake in the chat shell was repaired so the Korean first screen,
  placeholder, accessibility labels, and empty states are readable.
- `verify_frontend_ui_smoke.js` now covers the evidence panel, SSE cursor,
  and event sequence.
- `verify_browser_ui_smoke.js` now uses a deterministic completed-run fixture
  and evidence fixture instead of depending on whichever completed run happens
  to exist in SQLite. It verifies readable Korean chat output, plan/tool-flow
  panels, run evidence, screenshot rendering, and absence of visible mojibake.

Remaining work:

- Consider optional server-generated tool-use summary labels for long batches,
  but keep them explicitly auxiliary so a failed summarizer cannot affect the
  product agent result.
- If FitCheck adds permission-gated external actions, add user-visible
  permission request/decision records rather than hiding them inside trace.

### 9. Generic Harness Client Transcript

Claude Code's SDK/control surfaces include session message reads, streamlined
text messages, streamlined tool-use summaries, post-turn summaries, and control
messages for external SDK clients. The generic `developer_harness` already
persisted tasks, events, tool calls, observations, artifacts, hooks, and
verification results, but external controllers still had to stitch these
surfaces together themselves.

Implemented improvement:

- Added `GET /api/harness/v1/tasks/{task_id}/client-transcript`.
- The endpoint is client-isolated with the same `X-Harness-Client-Id` boundary
  as the task, event, and evidence endpoints.
- The transcript projection returns a compact `status_line` with task status,
  state version, client id, model, agent, sandbox, workspace mode, lease,
  latest event id/seq, evidence counts, and verification status.
- It returns stable `uuid` and `session_id` values for the original user prompt
  and streamlined assistant text extracted from stored Codex JSONL events.
- It includes deterministic `tool_use_summary` / `tool_summary`, hook progress,
  verification summaries, and source endpoint links for task, events, evidence,
  and transcript reads.
- `developer_harness.verify.py` now covers the runtime projection, OpenAPI
  path, API response shape, and cross-client isolation.

### 10. Generic Harness Queued Input Control

Claude Code's prompt/input layer has a durable command queue with priority
semantics: `now` interrupts, `next` drains between tool result and the next API
round trip, and `later` waits until the turn finishes. Its SDK control protocol
also exposes `cancel_async_message` by message uuid. The generic
`developer_harness` previously had `/reply`, but that only worked for an
already resumable thread and did not let an external controller preserve or
cancel follow-up input while a task was in flight.

Implemented improvement:

- Added `POST /api/harness/v1/tasks/{task_id}/queued-messages` with
  `prompt`, `priority`, `message_uuid`, `expected_state_version`, and metadata.
- Added `POST /api/harness/v1/tasks/{task_id}/queued-messages/cancel`.
- Queued messages are stored on the task payload, participate in optimistic
  `state_version`, and emit `task.message_queued`,
  `task.message_cancelled`, and `task.message_dequeued` events.
- Completed/resumable tasks drain queued messages in priority order:
  `now`, then `next`, then `later`.
- The client transcript status line now includes queued-message counts, and
  the transcript includes a `queued_messages` projection with stable uuids,
  priority, status, timestamps, and text.
- `now` records an interrupt request and attempts to cancel the active Codex
  process when the task is currently running, but the harness intentionally does
  not claim Claude's full streaming mid-tool interruption semantics yet.
- `developer_harness.verify.py` covers direct runtime enqueue/cancel/drain,
  event replay, transcript projection, OpenAPI paths, and cross-client API
  isolation.

### 11. Generic Harness Subagent Handoff Read Model

Claude Code records subagent identity, agent type, transcript path, parent
session linkage, worktree metadata, progress, final output, and usage so the
parent agent and SDK clients can resume, display, or hand off child work
without scanning unrelated sessions. The generic `developer_harness` could
create child tasks through `/subagents`, but parent evidence and transcript
reads did not summarize those children.

Implemented improvement:

- Added a store-level child task query keyed by `parent_task_id`.
- Parent task evidence now includes `subagents`: child task id, status,
  state version, thread id, join policy, agent/model/workspace metadata,
  prompt preview, final-message preview, return code, and error summary.
- Added `GET /api/harness/v1/tasks/{task_id}/subagents` as a client-isolated
  parent read model for child handoff status.
- The generic harness client transcript status line now includes subagent
  counts, and the transcript body includes the same subagent summaries plus a
  `source_endpoints.subagents` link.
- `developer_harness.verify.py` covers direct runtime child summaries, API
  child creation, cross-client isolation, parent evidence, and transcript
  projection.

Remaining work:

- Keep per-agent MCP servers, permission-mode inheritance, worktree isolation,
  and streaming progress as explicit future harness features. The current
  improvement is a durable read/handoff model over child tasks, not full Claude
  subagent execution parity.

### 12. Generic Harness Permission Decision Evidence

Claude Code treats permission as a typed runtime contract. Tool checks can
return `allow`, `ask`, `deny`, or `passthrough`, carry updated input, blocked
paths, suggestions, hook-derived reasons, classifier reasons, and permission
mode context. SDK control messages can request `can_use_tool`, and hook outputs
can include `PermissionRequest` and `PermissionDenied` decisions. The generic
`developer_harness` previously recorded hook executions but did not have a
first-class permission-decision evidence surface.

Implemented improvement:

- Added `PermissionDecisionRecord` to the harness API model.
- Added `POST /api/harness/v1/tasks/{task_id}/permission-decisions`.
- Added durable `harness_permission_decisions` rows and
  `permission_decision.recorded` replay events.
- Task evidence now includes `permission_decisions` alongside tool calls,
  observations, artifacts, hooks, verification results, and subagents.
- The generic harness client transcript status line now counts permission
  decisions and includes a compact permission decision projection with stable
  uuid, call id, tool name, behavior, status, message, blocked path, reason,
  and timestamp.
- `developer_harness.verify.py` covers runtime recording, API recording,
  evidence/transcript projection, event replay, OpenAPI path, and cross-client
  isolation.

Remaining work:

- Build an actual permission engine only if delegated harness workers need to
  enforce tool decisions before execution. The current implementation is an
  evidence/control-plane surface so external controllers can record and replay
  decisions without implying enforcement.

### 13. Generic Harness Session Metadata and Task Fork

Claude Code's SDK-oriented surface includes session-level operations such as
listing sessions, reading session info, renaming, tagging, summarizing, and
forking a session. The historical source stores session-facing fields such as
custom titles, tags, summaries, and resume/fork metadata. The generic
`developer_harness` previously exposed task state and events, but did not have a
compact SDK-like session read/update surface over those tasks.

Implemented improvement:

- Added `GET /api/harness/v1/sessions` for a client-isolated, paginated
  session list.
- Added `GET /api/harness/v1/tasks/{task_id}/session-info` for SDK-like task
  session metadata including session id, thread id, status, state version,
  title, custom title, tag, summary, request profile, counts, and fork origin.
- Added `POST /api/harness/v1/tasks/{task_id}/annotations` to update custom
  title, tag, and summary with optional optimistic `state_version` protection.
- Added `POST /api/harness/v1/tasks/{task_id}/fork` to create a queued task
  from the source task request while recording `forked_from_task_id` and
  `forked_from_thread_id`.
- The generic harness client transcript now includes `session_info`, so SDK
  clients can obtain both user-facing transcript text and session metadata from
  one read model.
- `developer_harness.verify.py` covers runtime annotation, session info, session
  list, task fork, API routes, OpenAPI paths, transcript projection, and
  cross-client isolation.

Remaining work:

- The current fork is a harness task-spec fork, not a byte-for-byte transcript
  fork with parent message UUID lineage. Full transcript fork parity should wait
  until delegated task transcript persistence and resumable Codex thread-copy
  semantics are available.

### 14. Generic Harness Task Progress Evidence

Claude Code's chat UX does not rely only on final responses. `TodoWriteTool`
records a structured checklist with `pending`, `in_progress`, and `completed`
states plus a present-continuous `activeForm` for live display. The newer task
tools (`TaskCreateTool`, `TaskUpdateTool`, task list/read/stop/output tools)
extend this into a durable task-list surface with ownership, blocking
relationships, metadata, hooks, and verification nudges. The generic
`developer_harness` previously had event logs and verification records, but no
first-class progress snapshot that an SDK or chat client could render as the
current task checklist.

Implemented improvement:

- Added `TaskProgressItem` and `TaskProgressUpdate` API models with
  `content`, `active_form`, `status`, optional item ids, and metadata.
- Added `POST /api/harness/v1/tasks/{task_id}/progress` as a client-isolated
  append-only progress evidence endpoint.
- Added durable `harness_task_progress` rows and `task.progress_updated` replay
  events.
- Task evidence now includes `task_progress`.
- The generic harness client transcript now includes the latest task progress
  snapshot, status counts, source endpoint, and a `verification_nudge_needed`
  flag when a 3+ item checklist is closed without a verification item.
- `developer_harness.verify.py` covers runtime progress recording,
  verification nudge projection, API recording, OpenAPI path, evidence and
  transcript projection, event replay, and cross-client isolation.

Remaining work:

- This is a progress evidence/read-model surface, not full Claude Task V2
  parity. Ownership, blocking relationships, teammate mailbox behavior,
  task-created/task-completed hook enforcement, and background task output
  streaming should be added only if delegated harness workers need that richer
  coordination model.

### 15. Generic Harness SDK Event Projection

Claude Code keeps raw internal events separate from SDK-facing events. The
historical `sdkEventQueue.ts` emits SDK-only `system` events such as
`task_started`, `task_progress`, `task_notification`, and
`session_state_changed`, with session ids, UUIDs, usage counters, summaries, and
progress batches. This lets VS Code, remote sessions, and headless SDK clients
update UI state without parsing raw tool/runtime logs. The generic
`developer_harness` already had append-only harness events and SSE replay, but
clients still had to understand harness-specific event types such as
`task.started`, `task.progress_updated`, and `task.completed`.

Implemented improvement:

- Added `GET /api/harness/v1/tasks/{task_id}/sdk-events`.
- Added `DevelopmentHarness.task_sdk_events(...)` to project raw task events
  into SDK-like system events.
- The projection emits `task_started`, `task_progress`, `task_notification`,
  and `session_state_changed` events with stable UUIDs, `session_id`, source
  event id/seq, timestamps, progress items, usage counters, and terminal
  summaries.
- The endpoint supports an `after` cursor over source event ids, matching the
  replay pattern of raw `/events` while avoiding raw harness-event parsing.
- The client transcript now links `source_endpoints.sdk_events`.
- `developer_harness.verify.py` covers runtime projection, API projection,
  OpenAPI path, `after` replay behavior, transcript source link, and
  cross-client isolation.

Remaining work:

- This projection does not yet stream as a dedicated SDK event SSE/WebSocket
  channel, and usage token counts are zero unless a delegated worker records
  model usage evidence. Add a live SDK stream only when an external SDK client
  needs it.

### 16. Generic Harness SDK Initialization Snapshot

Claude Code's SDK control protocol has an explicit `initialize` request and
response. The response gives SDK clients a stable startup snapshot: available
slash commands, subagents, output styles, models, account/auth metadata, process
id, fast-mode state, MCP server status, and context-usage breakdowns. The
generic `developer_harness` exposed `/capabilities`, `/llm/providers`, and
catalog data, but an SDK client still had to stitch together several
harness-specific endpoints and could not ask for MCP status or context usage in
Claude-SDK-shaped responses.

Implemented improvement:

- Added `GET /api/harness/v1/sdk/initialize`.
- Added `GET /api/harness/v1/sdk/mcp-status`.
- Added `GET /api/harness/v1/sdk/context-usage`.
- `DevelopmentHarness.sdk_initialize(...)` now returns commands from harness
  skills, agents from the agent catalog, output style metadata, model/provider
  templates, account/auth source metadata, process id, fast-mode state, and
  links to MCP/context endpoints.
- `DevelopmentHarness.sdk_mcp_status(...)` returns an MCP-status shaped
  response without implying live MCP connectivity.
- `DevelopmentHarness.sdk_context_usage(...)` returns a client-scoped estimate
  from stored tasks, events, and evidence counts.
- `developer_harness.verify.py` covers runtime snapshots, API routes, OpenAPI
  paths, client-scoped empty/non-empty context usage, and capability metadata.

Remaining work:

- Context usage is count-based harness evidence, not tokenizer-backed context
  accounting. MCP status is catalog evidence, not a live MCP connection probe.
  Add live MCP probing and tokenizer-backed context analysis only if a real SDK
  host needs those guarantees.

### 17. Generic Harness Tool Progress Projection

Claude Code emits individual `tool_progress` SDK messages with `tool_use_id`,
`tool_name`, `parent_tool_use_id`, elapsed time, task id, UUID, and session id.
The TUI and remote SDK adapters use this to render long-running tool activity
separately from final tool results. The generic `developer_harness` already
recorded tool calls and observations, but SDK clients had to infer progress
from raw evidence rows and could not reuse the SDK-shaped `tool_progress`
message contract.

Implemented improvement:

- The client transcript now includes `tool_progress` rows projected from
  recorded tool calls.
- `GET /api/harness/v1/tasks/{task_id}/sdk-events` now includes SDK-like
  `tool_progress` messages for `tool_call.recorded` source events.
- The projection includes tool use id, tool name, optional parent tool use id
  from metadata, elapsed time, task id, status, UUID, and session id.
- `developer_harness.verify.py` covers capability metadata, runtime transcript
  projection, SDK-event projection, API transcript projection, and API
  SDK-event projection.

Remaining work:

- This projection depends on recorded tool-call evidence. It does not actively
  heartbeat long-running tool execution unless the tool runner records updates
  while the tool is running.

## Priority Backlog

1. Backfill normalized event ledgers for historical JSON-only run files only if
   those files need to become long-term eval fixtures.
2. Keep product runtime single-agent until specialist handoffs are justified.

## Tool Schema Strictness

Claude Code tools use typed schemas and parse failures are part of the tool
execution contract. FitCheck keeps provider-level `strict=false` by default for
local OpenAI-compatible model compatibility, but backend execution now enforces
a stricter JSON Schema subset before calling handlers.

Implemented improvement:

- Backend tool validation now covers `type`, `required`,
  `additionalProperties`, array `items`, `enum`, `minLength`, `maxLength`,
  `minimum`, `maximum`, `minItems`, and `maxItems`.
- Validation failures include `schema_strictness.backend_validation =
  strict_json_schema_subset`, current `provider_strict` state, and the
  provider strict policy, making the split between provider compatibility and
  backend-owned strictness explicit.
- Provider-level strict tool schemas can now be opted in with
  `FITCHECK_LLM_PROVIDER_STRICT_TOOLS=1`, but only for OpenAI cloud-looking
  profiles and only when the tool schema satisfies the current OpenAI strict
  function-calling requirements: every object has
  `additionalProperties:false`, and every property is present in `required`
  (optional values must be modeled as nullable required fields). Local
  OpenAI-compatible endpoints stay non-strict.
- Verifiers cover enum, string length, numeric bounds, array length, array item
  schemas, additional properties, and existing malformed model tool arguments.
- Verifiers also cover provider strict export policy: default disabled, local
  endpoint disabled, OpenAI cloud strict-compatible schema enabled, and
  optional-field schema rejected.
- Product tool schemas now add concrete constraints for structured fields:
  candidate keys, closet item ids, source URLs, search query length, free-text
  size, uploaded image roles, candidate names, and `missing_fields` item/count
  limits. Open-world search remains permissive enough for Korean free text,
  while malformed empty keys, empty missing-info arrays, and too-short URLs fail
  before handler execution.

Remaining work:

- Convert additional product tool schemas to nullable-required form only when
  live OpenAI cloud runs prove that provider strict mode improves reliability
  without reducing useful recovery behavior.

## Verification

Use:

```powershell
cd backend_harness
node .\verify_frontend_ui_smoke.js
node .\verify_browser_ui_smoke.js
python -B verify_production_agent_runtime.py
python -B verify_local_llm_tool_adapter.py
```

For generic harness changes:

```powershell
cd developer_harness
python -B verify.py
```

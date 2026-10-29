# Developer Harness Agent Instructions

This directory is the working root for the generic development harness used by
other `/com2us` Codex or agent sessions to implement, verify, and iterate on the
FitCheck webapp.

Keep this package product-neutral. It may orchestrate development work for
FitCheck, but it must not become the FitCheck end-user product agent runtime.

## Scope

Work here is about:

- `/api/harness/v1` and `/api/dev-harness` task, event, reply, subagent, worker,
  loop, and LLM-provider APIs;
- session-isolated task state and event logs;
- delegated Codex execution and resumable threads;
- loop engineering for agentic AI construction;
- Agent SDK-oriented trace/eval/improvement harness support;
- provider profile diagnostics for OpenAI Responses-style APIs and local
  OpenAI-compatible chat-completions endpoints.

FitCheck production behavior lives under `backend_harness\app` and
the `/api/agent/runs` route. Do not route product behavior through this generic
harness unless the task explicitly asks for development orchestration.

## Current Direction

Prioritize building the harness that other `/com2us` Codex sessions use to
implement and verify the FitCheck webapp. The current emphasis is Agent
SDK/agentic AI construction support through loop engineering:

- expose agentic harness capabilities and loop templates;
- preserve traces, events, verification results, and provider diagnostics;
- turn trace or runtime gaps into evals/verifiers;
- inject clear loop-engineering instructions into delegated Codex tasks when
  requested by task metadata;
- keep mock, cached, fixture, and deterministic behavior labeled as harness
  evidence, not proof of a connected production AI agent.

Detailed reference:

```text
docs\20-general-agent-loop-engineering.md
```

## API Rules

- Stateful API calls require `X-Harness-Client-Id`.
- Use a unique client id per human session, automation controller, or external
  agent session.
- Do not read, resume, claim, or mutate another client's task, loop, event
  stream, or worker queue.
- Use `workspace_mode=client_isolated` when a delegated Codex run must avoid
  repository-level `AGENTS.md`; it creates a client-scoped workspace with a
  fresh session-local `AGENTS.md`.
- If `HARNESS_API_KEY` is configured, callers must pass `X-Harness-Api-Key` or
  `Authorization: Bearer <key>`.

## Provider Rules

- Probe `/models` before claiming a local endpoint is connected.
- Return or surface `configuration_required`, `not_connected`, or the provider
  error when LLM/provider configuration is missing.
- OpenAI cloud profiles may use Responses-style APIs.
- Local Ollama, LM Studio, vLLM, and other compatible servers default to
  OpenAI-compatible `/v1/chat/completions` tool-calling assumptions.
- Provider profiles are diagnostics for development harness work. They do not
  prove that the FitCheck production route has real LLM reasoning.

## Implementation Shape

Keep route bodies thin. Business logic belongs in:

```text
runtime.py
store.py
models.py
auth.py
catalog.py
service.py
```

The harness should make agent work observable and replayable through:

- append-only events with monotonic sequence ids;
- optimistic `state_version`;
- worker leases;
- parent/child task links for subagents;
- verification result records;
- hook lifecycle records;
- explicit capability and template metadata.

## Verification

Run from this directory when possible:

```powershell
python -B verify.py
```

From the repository root, the equivalent command is:

```powershell
python developer_harness\verify.py
```

If changing mounted FastAPI behavior, also run the relevant backend smoke or
infrastructure verifier from `backend_harness`, but keep product
runtime changes separate from generic harness changes.

## Communication

Be explicit about what is verified versus what is only a harness, mock, fixture,
cached result, fallback, or intended design. Do not imply that a live LLM,
search provider, OCR provider, Agent SDK trace backend, or production agentic
loop is connected unless the relevant probe or verifier proves it.

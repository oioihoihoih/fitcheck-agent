# Cross-Session Development Harness

This surface is separate from the FitCheck product agent. It lets another Codex
session or automation client create and resume development tasks through HTTP.

## Surfaces

- `GET /api/harness/v1/capabilities`
- `POST /api/harness/v1/tasks`
- `GET /api/harness/v1/tasks/{task_id}`
- `GET /api/harness/v1/tasks/{task_id}/events`
- `POST /api/harness/v1/tasks/{task_id}/reply`
- `POST /api/harness/v1/tasks/{task_id}/commands`
- `POST /api/harness/v1/tasks/{task_id}/subagents`
- `POST /api/harness/v1/workers/tick`
- `POST /api/harness/v1/loops`
- `POST /api/harness/v1/loops/{loop_id}/tick`
- `GET /api/harness/v1/llm/providers`
- `POST /api/harness/v1/llm/probe`

`/api/dev-harness/*` remains as a compatibility alias while other sessions move
to `/api/harness/v1/*`.

Tasks run through `codex exec --json`, and SQLite stores task state plus raw
Codex JSONL events. A durable Codex `thread_id` enables later replies. The
store also records optimistic `state_version`, append-only event `seq`,
worker lease owner/epoch/expiry, lifecycle hook records, and verification
command results.

## Isolation Contract

The API surface is shared, but harness state is not shared by default. Any
endpoint that reads or mutates tasks, loops, events, replies, worker claims, or
subagent runs requires `X-Harness-Client-Id`. Requests without that header are
rejected for stateful operations.

Rules:

- Use a unique `X-Harness-Client-Id` per human session, automation controller,
  or external agent session.
- Task lists, task detail, event streams, replies, commands, worker claims,
  loops, and loop ticks are filtered to the exact client id.
- Workers claim only queued work owned by the same client id.
- Reuse of an `idempotency_key` is scoped to `client_id + idempotency_key`.
- `AGENTS.md`, `CLAUDE.md`, and docs are global repository policy and discovery
  files. They are not per-session memory.
- Per-session working memory lives in the task request, task state, event log,
  verification records, and any client-owned artifacts referenced from those
  records.

If a session must avoid the repository-level `AGENTS.md` context entirely, run
the delegated task with `workspace_mode=client_isolated`, or pass a separate
`cwd` with its own instruction files. `workspace_mode=client_isolated` creates
a client-scoped workspace outside this repository and writes a fresh
`AGENTS.md` for that session. A Codex CLI run inside this repository can still
see repository-level project instructions.

Each task can include an `llm_provider` profile for OpenAI Responses, local
Ollama/LM Studio/vLLM OpenAI-compatible chat completions, or another provider.
The harness records this profile in the prompt context and exposes probe
endpoints so another session can verify provider connectivity before claiming
that an Agent SDK-style loop is ready.

Repository customization lives in:

- `.agents/skills/`: reusable workflows loaded progressively by Codex.
- `.codex/agents/`: bounded explorer, worker, and verifier roles.
- `.codex/hooks.json`: deterministic lifecycle event capture.
- `developer_harness/`: API, runtime, catalog, SQLite store, and verifier.

Loops are deliberately scheduler-neutral. A trusted controller calls `tick`;
each tick creates an independently traceable task and stops at `max_iterations`.
This avoids hiding an unbounded daemon inside the web process.

The general design baseline is documented in
`docs/20-general-agent-loop-engineering.md`.

## Run

```powershell
python -m uvicorn backend_harness.web_server:app --host 127.0.0.1 --port 8000
python developer_harness/verify.py
```

Codex project hooks require project trust. Review them with `/hooks`.

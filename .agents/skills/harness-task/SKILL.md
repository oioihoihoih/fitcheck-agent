---
name: harness-task
description: Use the shared development harness API to start, inspect, resume, and trace Codex tasks owned by another session.
---

# Shared Harness Task

1. Call `GET /api/harness/v1/capabilities` before relying on a named skill or agent.
2. Start work with `POST /api/harness/v1/tasks`. Provide a concrete prompt, absolute `cwd`, sandbox, optional agent/skills, optional `llm_provider`, and optional verification commands.
3. Treat the returned `task_id` as the durable handle. Poll the task or consume its SSE events.
4. Resume only when the task has a `thread_id`; use `POST /api/harness/v1/tasks/{task_id}/reply` and include `expected_state_version` when available.
5. Report completion from persisted task state and events, not from an assumed background process.
6. For parallel work, create child tasks through `POST /api/harness/v1/tasks/{task_id}/subagents`.
7. For local LLM or Agent SDK work, inspect `GET /api/harness/v1/llm/providers` and probe the selected endpoint before claiming it is connected.

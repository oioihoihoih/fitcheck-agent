---
name: local-llm-provider-diagnostics
description: Use when Codex needs to configure, verify, or report OpenAI Responses API, Ollama, LM Studio, vLLM, or other local OpenAI-compatible LLM provider profiles through the development harness. Requires /models probing, explicit api_style labeling, tool-calling assumptions, diagnostic evidence capture, and careful reporting of configuration_required, not_connected, failed, or completed status.
---

# Local LLM Provider Diagnostics

Use this skill before claiming any LLM provider is connected to the harness.

## Workflow

1. Call `GET /api/harness/v1/llm/providers` to inspect supported profile templates.
2. Choose an explicit provider profile:
   - OpenAI cloud: `api_style=responses` only when configured.
   - Ollama, LM Studio, vLLM, compatible servers: `api_style=chat_completions`.
3. Call `POST /api/harness/v1/llm/probe` with `X-Harness-Client-Id`.
4. Inspect `GET /api/harness/v1/llm/diagnostics` for the persisted probe record.
5. Use `supports_tool_calling` as an assumption to verify, not as proof.
6. Report provider state with the exact harness status:
   - `configuration_required`: missing or incomplete configuration.
   - `failed` or `not_connected`: endpoint rejected or could not be reached.
   - `completed`: `/models` probe succeeded.

## Evidence Rules

- Do not say "live LLM connected" from environment variables, docs, or a configured URL alone.
- Do not assume local servers support Responses API state or `previous_response_id`.
- Do not use FitCheck production route behavior as evidence for generic harness provider connectivity.
- If a probe fails, keep the failure as harness diagnostic evidence and propose the next verifier or configuration fix.

## Reference

Read `references/provider-profiles.md` for request examples and reporting phrases.

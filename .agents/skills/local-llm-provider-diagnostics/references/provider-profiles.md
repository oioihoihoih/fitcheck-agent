# Provider Profiles

Use these examples with `POST /api/harness/v1/llm/probe`.

## Ollama

```json
{
  "provider": {
    "provider": "ollama",
    "api_style": "chat_completions",
    "base_url": "http://localhost:11434/v1",
    "model": "qwen3-coder",
    "api_key_env": null,
    "supports_tool_calling": true,
    "supports_stateful_responses": false
  }
}
```

## LM Studio

```json
{
  "provider": {
    "provider": "lm_studio",
    "api_style": "chat_completions",
    "base_url": "http://localhost:1234/v1",
    "api_key_env": null,
    "supports_tool_calling": true,
    "supports_stateful_responses": false
  }
}
```

## OpenAI Cloud

```json
{
  "provider": {
    "provider": "openai",
    "api_style": "responses",
    "base_url": "https://api.openai.com/v1",
    "api_key_env": "OPENAI_API_KEY",
    "supports_tool_calling": true,
    "supports_stateful_responses": true
  }
}
```

## Reporting Phrases

- "Probe status is `configuration_required`; this is harness evidence of missing provider configuration."
- "Probe status is `failed`; the endpoint is not proven connected."
- "Probe status is `completed`; `/models` responded and the diagnostic is persisted for this harness client."
- "Tool calling remains model-dependent until a tool-loop verifier passes."

# FitCheck Web App (modern UI)

React + TypeScript + Vite front-end for the FitCheck agent. It talks to the same
FastAPI backend as the legacy `webapp/`, and exercises the agent-runtime features
added during the docs/24 gap remediation:

- **Streaming** — consumes the SSE `/events` stream, rendering the model's text
  token-by-token via `agent_delta` events (docs/24 #5) and live trace via
  `agent_state` events (docs/24 #6).
- **Async runs** — always creates runs with `?background=true` for an immediate
  handle (docs/24 #4).
- **Cancel** — a Cancel button calls `POST /runs/{id}/cancel` (docs/24 #1).
- **Steering** — sending a message while a run is in flight enqueues it via
  `POST /runs/{id}/reply` and shows the queued state (docs/24 #2/#3).
- **Multi-turn** — follow-up messages on a finished run reuse the same run so the
  backend can resolve ordinal/comparative references (docs/24 #7/#8/#16).
- **Run controls** — the side panel sets `max_turns`, `tool_budget`, and
  per-run `search_provider` (docs/24 #9/#11).

## Build

```bash
cd webapp-next
npm install
npm run build      # emits webapp-next/dist/
```

FastAPI serves `webapp-next/dist/` at `/` automatically when it exists
(`active_webapp_dir()` in `backend_harness/app/server_context.py`); otherwise it
falls back to the legacy `webapp/`. The legacy UI stays available at `/legacy`.

## Dev

```bash
npm run dev        # Vite dev server on :5199, proxies /api and /outputs to :8731
```

`dist/` is a build artifact and is not committed; run `npm run build` after a
checkout to get the modern UI.

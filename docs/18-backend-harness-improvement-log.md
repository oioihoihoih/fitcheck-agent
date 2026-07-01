# Backend Harness Improvement Log

작성일: 2026-06-07

이 문서는 FitCheck backend harness의 부족한 부분을 계속 개선하기 위한 조사, 판단, 테스트 변경 로그입니다.

## 2026-06-29 Trace ingest compact stream maps

Problem:

- `trace_ingest.py --compact-output` exposed readback validity and provider/API-style maps, but
  streaming distribution details still required opening the full readback payload.
- That made standalone stream and streamed-handoff SDK trace ingestion less ergonomic than matrix
  compact output, where stream event/tool-name preservation is already visible from stdout.

Changes:

- `trace_ingest_compact_summary` now exposes stream distribution maps from three surfaces:
  evidence counts, `/sdk-events?summary=true`, and `/client-transcript?summary=true`.
- New compact fields include stream event count, event type/name counts, raw response event type
  counts, item class counts, and tool-name counts for SDK-events and client transcript summaries,
  plus evidence-derived stream maps.
- Compact output also mirrors `readback_expected_stream_event_counts` from validation so
  controllers can compare expected stream coverage without opening the full payload.
- `developer_harness/verify.py` now covers stream map preservation in compact trace-ingest output.
- `docs/20-general-agent-loop-engineering.md` and the `agentic-harness-loop` API checklist now
  state that compact trace-ingest output includes stream distribution maps.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced harness API smoke confirmed `GET /health`, `GET /ready`, and
  `GET /capabilities` for client `codex-trace-ingest-stream-compact-live-20260629`; harness auth
  was `not_configured`, and capabilities advertised `provider_diagnostics` plus
  `read_model_summaries`.
- Live OpenAI provider probe returned `status=completed`, HTTP `200`, provider `openai`, API style
  `responses`, model `gpt-5.4-mini`, and diagnostic id `116`.
- Live Agents SDK `stream_handoff` smoke wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-stream-handoff-trace-ingest-compact-live-20260629.json`
  with `external_api_called=true`, raw response count `2`, stream event count `30`, total API
  tokens `244`, and final output `AGENTS_SDK_STREAM_HANDOFF_FINAL_OK`.
- Compact trace ingest:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/trace_ingest.py --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-trace-ingest-stream-compact-live-20260629 --task-id task_10a53cc8bb48 --trace-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-stream-handoff-trace-ingest-compact-live-20260629.json --readback --fail-on-readback-invalid --compact-output --output-json harness_outputs/sdk-live-smoke-matrix/trace-ingest-stream-handoff-compact-live-20260629.full.json`
  exited `0` and compact stdout was saved to
  `harness_outputs/sdk-live-smoke-matrix/trace-ingest-stream-handoff-compact-live-20260629.compact.json`.
- Compact stdout reported `readback_valid=true`, failed checks `[]`, task binding
  `task_10a53cc8bb48`, SDK-event stream count `30`, client-transcript stream count `30`,
  SDK-event and transcript stream tool-name counts
  `{"none": 27, "transfer_to_harness_specialist": 3}`, and raw event replay count `42`.
- The compact file contains no bulky `actions` or `results`, while the full output JSON preserves
  `results` and the full `readback.validation` payload.

## 2026-06-29 Trace ingest compact output

Problem:

- `trace_ingest.py --readback` produced strong evidence, but stdout contained the full action,
  result, observation, and readback payload.
- Matrix runs already had compact controller output, while standalone trace ingestion still required
  parsing bulky JSON to see validity, failed checks, task binding, provider/API-style maps, and raw
  event replay coverage.

Changes:

- `trace_ingest.py` now accepts `--compact-output`.
- Compact stdout uses `source=trace_ingest_compact_summary` and includes action/result counts,
  stored artifact/tool/observation ids, readback validity, failed checks, task binding,
  provider/API-style projection maps, artifact/session summaries, and raw event replay fetch-plan
  plus expected/observed event counts.
- `trace_ingest.py` now also accepts `--output-json` so compact stdout can be used while preserving
  the full action/result/readback payload in a file.
- `developer_harness/verify.py` covers dry-run compact stdout, full-payload file preservation,
  compact readback/replay summaries, and failed-readback compact status.
- `docs/20-general-agent-loop-engineering.md` and
  `.agents/skills/agentic-harness-loop/references/api-checklist.md` document the compact trace
  ingest workflow.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced harness API smoke confirmed `GET /health`, `GET /ready`, and
  `GET /capabilities` for client `codex-trace-ingest-compact-live-20260629`; harness auth was
  `not_configured`, and capabilities advertised `provider_diagnostics` plus
  `read_model_summaries`.
- Live OpenAI provider probe returned `status=completed`, HTTP `200`, provider `openai`, API style
  `responses`, model `gpt-5.4-mini`, and diagnostic id `115`.
- Live Agents SDK `tool` smoke wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-tool-trace-ingest-compact-live-20260629.json`
  with `external_api_called=true`, raw response count `2`, total API tokens `331`, tool call/output
  count `1/1`, and final output `AGENTS_SDK_LIVE_FINAL_OK`.
- Compact trace ingest:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/trace_ingest.py --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-trace-ingest-compact-live-20260629 --task-id task_8dbe911fbd9b --trace-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-tool-trace-ingest-compact-live-20260629.json --readback --fail-on-readback-invalid --compact-output --output-json harness_outputs/sdk-live-smoke-matrix/trace-ingest-compact-live-20260629.full.json`
  exited `0` and compact stdout was saved to
  `harness_outputs/sdk-live-smoke-matrix/trace-ingest-compact-live-20260629.compact.json`.
- Compact stdout reported `readback_valid=true`, `readback_check_count=60`, failed checks `[]`,
  task binding `task_8dbe911fbd9b`, SDK-event provider counts `{"openai": 2}`, API-style counts
  `{"responses": 2}`, raw event replay count `10`, and event replay failed checks `[]`.
- The compact file contains no bulky `actions` or `results`, while the full output JSON preserves
  `results` and the full `readback.validation` payload.

## 2026-06-29 Harness diagnostics expected-field check

Problem:

- Matrix provider diagnostic readback checks diagnostic id, status, model, and API style, but the
  routine `harness_api.py diagnostics` command could only gate on diagnostic id.
- That left external controllers with a weaker standalone verification path when replaying a live
  SDK/provider smoke outside the matrix wrapper.

Changes:

- `harness_api.py diagnostics` now accepts optional `--expect-status`, `--expect-provider`,
  `--expect-model`, and `--expect-api-style` alongside `--expect-diagnostic-id`.
- The CLI appends those expected fields and the matched provider details to
  `diagnostics_validation`.
- Both the CLI diagnostics gate and matrix provider-diagnostic readback now recover provider,
  model, API style, and base URL from either nested provider dictionaries or top-level scalar
  diagnostic fields.
- The command exits nonzero when any supplied expected field does not match the client-scoped
  diagnostics read-model row.
- `developer_harness/verify.py` now covers field matches, provider details stored directly on the
  diagnostic row, provider details nested under `result.provider`, field mismatch failures, and
  dry-run behavior.
- `docs/20-general-agent-loop-engineering.md` and
  `.agents/skills/agentic-harness-loop/references/api-checklist.md` now document the stronger
  diagnostics CLI gate.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/harness_api.py .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced harness API smoke confirmed `GET /health`, `GET /ready`, and `GET /capabilities`
  for client `codex-diagnostics-field-live-20260629`; harness auth was `not_configured`.
- Live OpenAI provider probe:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/harness_api.py --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-diagnostics-field-live-20260629 probe --provider openai --model gpt-5.4-mini --timeout-seconds 10`
  returned `status=completed`, HTTP `200`, provider `openai`, API style `responses`, model
  `gpt-5.4-mini`, and diagnostic id `114`.
- Dry-run diagnostics field check:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/harness_api.py --dry-run --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-diagnostics-field-live-20260629 diagnostics --limit 1 --expect-diagnostic-id 114 --expect-status completed --expect-provider openai --expect-model gpt-5.4-mini --expect-api-style responses`
  produced `GET /llm/diagnostics?limit=1` and did not perform validation.
- Live diagnostics expected-field success:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/harness_api.py --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-diagnostics-field-live-20260629 diagnostics --limit 1 --expect-diagnostic-id 114 --expect-status completed --expect-provider openai --expect-model gpt-5.4-mini --expect-api-style responses`
  returned exit code `0`, `diagnostics_validation.valid=true`, and checks for diagnostic id,
  status, provider, model, and API style all `true`.
- Live diagnostics expected-field failure:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/harness_api.py --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-diagnostics-field-live-20260629 diagnostics --limit 1 --expect-diagnostic-id 114 --expect-status completed --expect-provider openai --expect-model wrong-model --expect-api-style responses`
  returned exit code `1`, `diagnostics_validation.valid=false`, and failed check
  `diagnostic_model_matches`.
- Live Agents SDK `tool` smoke:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke.py --scenario tool --model gpt-5.4-mini --max-tokens 80 --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-tool-diagnostics-field-live-20260629.json`
  returned `status=completed`, `external_api_called=true`, raw response count `2`, tool call/output
  count `1/1`, total API tokens `331`, and final output `AGENTS_SDK_LIVE_FINAL_OK`.
- Trace ingest readback:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/trace_ingest.py --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-diagnostics-field-live-20260629 --task-id task_5d720020a7c5 --trace-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-tool-diagnostics-field-live-20260629.json --readback --fail-on-readback-invalid`
  returned `trace_ingest_readback_validation.valid=true`, check count `60`, failed checks `[]`,
  SDK-event and client-transcript model response provider counts `{"openai": 2}`, API-style counts
  `{"responses": 2}`, and raw event replay count `10`.

## 2026-06-29 Harness diagnostics expected-id check

Problem:

- `harness_api.py diagnostics --limit N` could reproduce the bounded diagnostics readback request,
  but controllers still had to parse JSON manually to prove that a specific provider diagnostic id
  from a matrix run was visible.
- That made the CLI less useful as a standalone verification gate for the provider diagnostic
  read-model binding added to matrix publish/revalidation flows.

Changes:

- `harness_api.py diagnostics` now accepts `--expect-diagnostic-id N`.
- When the expected id is supplied and the command is not a dry run, the CLI appends a
  `diagnostics_validation` summary to stdout and exits nonzero if the id is not present in the
  current client-scoped `/llm/diagnostics` response.
- Dry-run diagnostics requests still only show the outgoing request shape and do not fail on the
  expected id.
- `developer_harness/verify.py` now covers matched, missing, and dry-run expectation behavior.
- `docs/20-general-agent-loop-engineering.md` and
  `.agents/skills/agentic-harness-loop/references/api-checklist.md` now document the expected-id
  diagnostics CLI gate.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/harness_api.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Dry-run CLI check:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/harness_api.py --dry-run --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-matrix-payload-diagnostic-live-20260629 diagnostics --limit 1 --expect-diagnostic-id 113`
  produced `GET /llm/diagnostics?limit=1` and did not fail on the expected id.
- `.env`-sourced harness API smoke confirmed `GET /health` and `GET /ready` for client
  `codex-matrix-payload-diagnostic-live-20260629`; harness auth was `not_configured`.
- Live diagnostics expected-id success:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/harness_api.py --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-matrix-payload-diagnostic-live-20260629 diagnostics --limit 1 --expect-diagnostic-id 113`
  returned exit code `0`, `diagnostics_validation.status=matched`,
  `diagnostics_validation.valid=true`, and `matched_diagnostic_id=113`.
- Live diagnostics expected-id failure:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/harness_api.py --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-matrix-payload-diagnostic-live-20260629 diagnostics --limit 1 --expect-diagnostic-id 999999`
  returned exit code `1`, `diagnostics_validation.status=missing`,
  `diagnostics_validation.valid=false`, and failed check `diagnostic_id_matched`.

## 2026-06-29 Harness diagnostics CLI limit

Problem:

- Matrix validation now uses bounded provider diagnostic readback via
  `/llm/diagnostics?limit=200`, but the routine `harness_api.py diagnostics` command could only call
  the default diagnostics endpoint.
- This forced controllers to use ad hoc HTTP when they wanted to reproduce the same bounded
  diagnostics read-model check outside the matrix script.

Changes:

- `harness_api.py diagnostics` now accepts `--limit N`, clamps it to the API range `1..200`, and
  sends `GET /llm/diagnostics?limit=N`.
- The provider plan now suggests `diagnostics --limit 20`.
- `developer_harness/verify.py` covers diagnostics dry-run URL generation.
- `docs/20-general-agent-loop-engineering.md` and
  `.agents/skills/agentic-harness-loop/references/api-checklist.md` now document the bounded
  diagnostics CLI path.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/harness_api.py .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Dry-run CLI check:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/harness_api.py --dry-run --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-matrix-payload-diagnostic-live-20260629 diagnostics --limit 1`
  produced `GET /llm/diagnostics?limit=1`.
- `.env`-sourced harness API smoke confirmed `GET /health` and `GET /ready` for client
  `codex-matrix-payload-diagnostic-live-20260629`; harness auth was `not_configured`.
- Live diagnostics CLI check:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/harness_api.py --base-url http://127.0.0.1:8000/api/harness/v1 --client-id codex-matrix-payload-diagnostic-live-20260629 diagnostics --limit 1`
  returned `status=completed`, one diagnostic, and diagnostic id `113`.

## 2026-06-29 Matrix publish-time provider diagnostic readback gate

Problem:

- Stored matrix revalidation now checked `/llm/diagnostics`, but the live matrix publish path still
  accepted `matrix_payload_readback_validation` based on aggregate evidence/read-model/event replay
  only.
- That meant a live `--publish-matrix-readback` run could emit a compact success summary before
  proving that the newly referenced provider diagnostic id was visible in the client-scoped
  diagnostics read model.

Changes:

- `validate_published_matrix_payload_readback()` now refetches client-scoped
  `/llm/diagnostics?limit=200` and requires the source matrix provider diagnostic id to match by
  id, status, model, and API style before the publish-time payload readback gate completes.
- `attach_matrix_gate_readback_summary()` now includes provider diagnostic failed checks under the
  relevant matrix gate's compact failed-check list, so a provider-diagnostic-only failure is visible
  from `matrix_run_summary`.
- Live compact `matrix_run_summary` now mirrors `provider_diagnostic_readback_status`,
  `provider_diagnostic_readback_valid`, `provider_diagnostic_readback_failed_checks`,
  `provider_diagnostic_readback_expected_id`, `provider_diagnostic_readback_matched_id`,
  `provider_diagnostic_readback_diagnostic_count`, and the readback check/provider summary from
  `matrix_payload_readback_validation`.
- `developer_harness/verify.py` now covers publish-time provider diagnostic success and failure
  paths, plus compact stdout projection of the readback fields.
- `docs/20-general-agent-loop-engineering.md` and
  `.agents/skills/agentic-harness-loop/references/api-checklist.md` now require publish-time matrix
  payload readback to bind the provider diagnostic read model, not only stored revalidation.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/store.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced harness API smoke confirmed `GET /health`, `GET /ready`, `GET /capabilities`, and
  empty initial `GET /llm/diagnostics` for client
  `codex-matrix-payload-diagnostic-live-20260629`; harness auth was `not_configured`.
- Live Agents SDK matrix:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario tool --run-id live-payload-diagnostic-readback-20260629 --model gpt-5.4-mini --max-tokens 160 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-matrix-payload-diagnostic-live-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-payload-diagnostic-readback-20260629.json --compact-output`
- The live matrix completed with provider diagnostic id `113`, scenario task
  `task_ec3a6927072b`, aggregate task `task_22520629da08`,
  `matrix_payload_readback_status=completed`, `matrix_payload_readback_valid=true`,
  `provider_diagnostic_readback_valid=true`, `provider_diagnostic_readback_matched_id=113`,
  `external_api_called=true`, and `321` total API tokens.
- Stored-evidence revalidation:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --revalidate-from-summary-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-payload-diagnostic-readback-20260629.json --base-url http://127.0.0.1:8000/api/harness/v1 --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-payload-diagnostic-readback-stored-revalidate-20260629.json --compact-output`
- Stored revalidation returned `status=completed`, `valid=true`, aggregate `check_count=80`,
  `provider_probe_diagnostic_id=113`, `provider_diagnostic_readback_valid=true`,
  `provider_diagnostic_readback_matched_id=113`, provider diagnostic readback `check_count=6`,
  `external_api_called=true`, `published_scenario_external_api_called.tool=true`, and `321` total
  API tokens. A final `GET /llm/diagnostics` confirmed diagnostic id `113` is visible for the
  client.

## 2026-06-29 Matrix provider diagnostic readback validation

Problem:

- Stored matrix revalidation compared aggregate harness evidence against the matrix JSON and mirrored
  provider probe details in compact output, but it did not refetch `/llm/diagnostics` to prove that
  the referenced provider diagnostic id was still visible in the client-scoped harness read model.
- A stale or missing provider-diagnostic read model could leave compact output tied only to copied
  matrix payload fields, not to the durable provider diagnostic surface.

Changes:

- `agents_sdk_smoke_matrix.py` now extracts the source matrix provider diagnostic id and, during
  stored-evidence revalidation, refetches client-scoped `/llm/diagnostics?limit=200`.
- Stored revalidation now fails when a matrix has a provider diagnostic id but that id is not found,
  or when the matched diagnostic has a mismatched status, model, or API style.
- `HarnessStore.list_provider_diagnostics()` now returns `diagnostic_id` from the SQLite row id even
  for older diagnostics whose stored JSON payload was written before `diagnostic_id` was attached to
  the runtime return value.
- Stored compact output now exposes `provider_diagnostic_readback_status`,
  `provider_diagnostic_readback_valid`, `provider_diagnostic_readback_failed_checks`,
  `provider_diagnostic_readback_expected_id`, `provider_diagnostic_readback_matched_id`,
  `provider_diagnostic_readback_diagnostic_count`, and the compact checks/provider summary.
- `developer_harness/verify.py` now covers both the successful provider diagnostic readback path and
  the failure path where aggregate evidence is valid but `/llm/diagnostics` no longer contains the
  expected diagnostic id.
- `docs/20-general-agent-loop-engineering.md` and
  `.agents/skills/agentic-harness-loop/references/api-checklist.md` now require stored matrix
  revalidation to bind provider probe evidence to the diagnostics read model without running a new
  provider probe.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/store.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced harness API smoke confirmed `GET /health` and client-scoped
  `GET /llm/diagnostics` for client `codex-matrix-external-api-compact-20260629`; diagnostics
  readback now exposes historical provider diagnostic id `112`.
- Stored-evidence revalidation:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --revalidate-from-summary-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-external-api-compact-20260629.json --base-url http://127.0.0.1:8000/api/harness/v1 --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-provider-diagnostic-readback-stored-revalidate-20260629.json --compact-output`
- Stored revalidation returned `status=completed`, `valid=true`, aggregate `check_count=80`,
  `provider_probe_diagnostic_id=112`, `provider_diagnostic_readback_valid=true`,
  `provider_diagnostic_readback_matched_id=112`, provider diagnostic readback `check_count=6`,
  `external_api_called=true`, `published_scenario_external_api_called.tool=true`, and `321` total
  API tokens.

## 2026-06-29 Matrix compact external API evidence

Problem:

- Matrix compact summaries already exposed provider probe status and API token usage, but they did
  not expose `external_api_called` directly at the aggregate or per-scenario level.
- A controller could infer live SDK/API use from nested artifact metadata, but compact stdout should
  distinguish live API evidence from dry-run, fixture, or cached harness output without opening the
  full trace artifact.

Changes:

- `agents_sdk_smoke_matrix.py` now preserves each smoke result's root `external_api_called` flag in
  matrix result items.
- Matrix summaries now expose aggregate `external_api_called` plus
  `scenario_external_api_called` derived from smoke result validation.
- Live compact output now exposes `external_api_called`, `scenario_external_api_called`, and
  `scenario_readback_external_api_called` from stored trace artifact metadata.
- Stored-evidence revalidation compact output now exposes `external_api_called`,
  `scenario_external_api_called`, and `published_scenario_external_api_called`.
- Aggregate matrix readback validation now checks that summary and scenario-rollup external API
  evidence match the source matrix payload.
- `developer_harness/verify.py` covers summary, rollup, aggregate validation, dry-run compact
  defaults, live compact output, and stored published-scenario compact output for these fields.
- `docs/20-general-agent-loop-engineering.md` and
  `.agents/skills/agentic-harness-loop/references/api-checklist.md` now require compact summaries
  to expose external API evidence.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced harness API smoke confirmed `GET /health`, `GET /ready`, and `GET /capabilities`
  for client `codex-matrix-external-api-compact-20260629`; harness auth was `not_configured`.
- Live Agents SDK matrix:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario tool --run-id live-external-api-compact-20260629 --model gpt-5.4-mini --max-tokens 160 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-matrix-external-api-compact-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-external-api-compact-20260629.json --compact-output`
- The live matrix completed with provider diagnostic id `112`, scenario task `task_3dd20b0387f9`,
  aggregate task `task_56a8b3fdefb2`, `external_api_called=true`,
  `scenario_external_api_called.tool=true`, `scenario_readback_external_api_called.tool=true`, `321`
  total API tokens, and no failed checks.
- Stored-evidence revalidation:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --revalidate-from-summary-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-external-api-compact-20260629.json --base-url http://127.0.0.1:8000/api/harness/v1 --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-external-api-compact-stored-revalidate-20260629.json --compact-output`
- Stored revalidation returned `status=completed`, `valid=true`, aggregate `check_count=80`,
  `external_api_called=true`, `scenario_external_api_called.tool=true`,
  `published_scenario_external_api_called.tool=true`, `321` total API tokens, and no failed checks.

## 2026-06-29 Matrix compact fetch-plan summaries

Problem:

- Matrix event replay validation preserved `event_replay_fetch_plan` in full payloads, but compact
  live and stored-evidence summaries still forced controllers to reopen the full matrix JSON to see
  why a replay fetch size was chosen or whether truncation checks passed.
- This weakened the compact controller contract: compact stdout could show event replay check counts
  without exposing the replay sizing basis, selected `max_events`, expected raw event count, or
  fetch-plan truncation checks.

Changes:

- `agents_sdk_smoke_matrix.py` now exposes compact fetch-plan summaries for live matrix output:
  `matrix_readback_event_replay_fetch_plan`,
  `matrix_payload_readback_event_replay_fetch_plan`, `scenario_event_replay_fetch_plans`, and
  matching `*_fetch_plan_checks` fields.
- Stored-evidence `readback_validation_summary` now exposes `aggregate_event_replay_fetch_plan`,
  `aggregate_event_replay_fetch_plan_checks`, `published_scenario_event_replay_fetch_plans`, and
  `published_scenario_event_replay_fetch_plan_checks`.
- `developer_harness/verify.py` now covers helper-level compact extraction, live compact output, and
  stored published-scenario compact output for fetch-plan basis, `max_events`, expected raw event
  count, and truncation checks.
- `docs/20-general-agent-loop-engineering.md` and
  `.agents/skills/agentic-harness-loop/references/api-checklist.md` now require compact live and
  stored summaries to expose replay fetch-plan and truncation-check details.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced harness API smoke confirmed `GET /health`, `GET /ready`, and `GET /capabilities`
  for client `codex-matrix-compact-fetch-plan-20260629`; harness auth was `not_configured`.
- Live Agents SDK matrix:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario tool --run-id live-compact-fetch-plan-20260629 --model gpt-5.4-mini --max-tokens 160 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-matrix-compact-fetch-plan-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-compact-fetch-plan-20260629.json --compact-output`
- The live matrix completed with provider diagnostic id `111`, `external_api_called=true`, `321`
  total API tokens, scenario task `task_42e82678c0a7`, aggregate task `task_9a014cd8dda5`, no failed
  checks, matrix fetch-plan `basis=sdk_events_raw_event_count`, matrix
  `expected_raw_event_count=6`, scenario `expected_raw_event_count=10`, and both fetch-plan
  truncation checks true in compact stdout.
- Stored-evidence revalidation:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --revalidate-from-summary-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-compact-fetch-plan-20260629.json --base-url http://127.0.0.1:8000/api/harness/v1 --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-compact-fetch-plan-stored-revalidate-20260629.json --compact-output`
- Stored revalidation returned `status=completed`, `valid=true`, aggregate `check_count=76`,
  published scenario event replay `check_count=11`, aggregate fetch-plan
  `expected_raw_event_count=6`, scenario fetch-plan `expected_raw_event_count=10`, and true
  `event_replay_fetch_plan_covers_expected_raw_event_count` /
  `event_replay_not_truncated_by_fetch_limit` checks in compact output.

## 2026-06-29 Matrix event replay dynamic fetch plan

Problem:

- Standalone `trace_ingest.py --readback` had dynamic raw event replay fetch sizing, but
  `agents_sdk_smoke_matrix.py` still fetched scenario and aggregate raw `/events` replay through
  the matrix path without carrying the readback-derived fetch plan into matrix validation output.
- That meant a matrix controller could prove generic evidence/read-model binding while losing the
  exact replay fetch sizing basis needed to diagnose large evidence-holder tasks or SSE truncation.

Changes:

- `agents_sdk_smoke_matrix.py` now derives scenario and aggregate raw event replay `max_events`
  from each task's readback summaries before fetching `/tasks/{task_id}/events?after=0`.
- Matrix replay summaries and validations now preserve `event_replay_fetch_plan` and check
  `event_replay_fetch_plan_covers_expected_raw_event_count` plus
  `event_replay_not_truncated_by_fetch_limit`.
- Publish-time scenario readback, publish-time aggregate readback, stored aggregate revalidation,
  stored published-scenario revalidation, and final payload readback validation all pass the
  readback envelope into replay fetches.
- `developer_harness/verify.py` covers dynamic SSE `max_events` propagation, scenario truncation
  failure, aggregate fetch-plan preservation, aggregate truncation failure, and updated compact
  check counts. The `/sessions` verifier assertion was also narrowed to the created task id instead
  of assuming a stable first list item.
- `docs/20-general-agent-loop-engineering.md` and
  `.agents/skills/agentic-harness-loop/references/api-checklist.md` now state that matrix scenario
  and aggregate replay validation should preserve fetch plans and reject truncated replay.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced harness API smoke confirmed `GET /health`, `GET /ready`, and `GET /capabilities`
  for client `codex-matrix-dynamic-replay-20260629`; OpenAI provider probe returned HTTP 200 and
  recorded diagnostic id `109` before live matrix execution.
- Live Agents SDK matrix:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario tool --run-id live-matrix-dynamic-replay-20260629 --model gpt-5.4-mini --max-tokens 160 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-matrix-dynamic-replay-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-matrix-dynamic-replay-20260629.json --compact-output`
- The live matrix completed with provider diagnostic id `110`, `external_api_called=true`, `321`
  total API tokens, scenario task `task_8c18f500bd83`, aggregate task `task_9949c2667555`,
  `matrix_readback_valid=true`, `matrix_payload_readback_valid=true`, aggregate `check_count=76`,
  scenario event replay `check_count=11`, and no failed checks.
- Stored-evidence revalidation:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --revalidate-from-summary-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-matrix-dynamic-replay-20260629.json --base-url http://127.0.0.1:8000/api/harness/v1 --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-matrix-dynamic-replay-stored-revalidate-20260629.json --compact-output`
- Stored revalidation returned `status=completed`, `valid=true`, aggregate `check_count=76`,
  published scenario event replay `check_count=11`, no failed checks, aggregate
  `event_replay_fetch_plan` with `basis=sdk_events_raw_event_count`, `max_events=500`,
  `expected_raw_event_count=6`, and scenario `event_replay_fetch_plan` with
  `expected_raw_event_count=10`; both fetch-plan truncation checks were true.

## 2026-06-29 Standalone trace-ingest dynamic event replay fetch

Problem:

- Standalone `trace_ingest.py --readback` now validates raw event replay, but the first version used
  a fixed SSE replay limit of 500 events.
- Large Agents SDK traces, or evidence-holder tasks that are reused, can legitimately have more
  than 500 raw task events. In that case evidence/read-model projections may be correct while the
  standalone event replay gate fails because the client truncated its own replay.

Changes:

- `trace_ingest.py` now derives the replay `max_events` from the SDK-events summary
  `raw_event_count` when available, with stored evidence counts as fallback and a fixed buffer.
- The standalone readback now preserves an event replay `fetch_plan` with basis, selected
  `max_events`, default limit, buffer, expected raw event count, evidence event floor, and claim
  boundary.
- `trace_ingest_event_replay_validation` now fails if the fetch plan reports an expected raw event
  count but the replay returned fewer events.
- `developer_harness/verify.py` covers raw-event-count sizing, evidence-count fallback sizing,
  actual SSE `max_events` propagation, and truncated replay failure.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced harness API smoke confirmed `GET /health`, `GET /ready`, and `GET /capabilities`
  for client `codex-trace-dynamic-replay-20260629`; OpenAI provider probe returned HTTP 200 and
  recorded diagnostic id `108` with `provider=openai`, `api_style=responses`, and
  `model=gpt-5.4-mini`.
- Direct standalone `trace_ingest.py --readback --fail-on-readback-invalid` against the prior live
  Agents SDK `tool` trace wrote
  `harness_outputs/sdk-live-smoke-matrix/trace-ingest-live-dynamic-event-replay-20260629.json` for
  task `task_4fa66bfac6e3`. It returned `valid=true`, full readback `check_count=60`, no failed
  checks, nested `trace_ingest_event_replay_validation.check_count=11`, and an
  `event_replay.fetch_plan` with `basis=sdk_events_raw_event_count`, `max_events=500`,
  `expected_raw_event_count=10`, and `evidence_event_floor=8`.

## 2026-06-29 Standalone trace-ingest event replay gate

Problem:

- Matrix scenario publish-readback already validated raw `/events` replay for each scenario task,
  but direct `trace_ingest.py --readback --fail-on-readback-invalid` only fetched stored evidence
  plus SDK-event/client-transcript summaries.
- A controller using trace ingestion directly could accept evidence/read-model consistency without
  proving the append-only event trail covered the artifact, observation, tool-call, and required
  tool-call status-update writes.

Changes:

- `trace_ingest.py --readback` now fetches raw `/tasks/{task_id}/events?after=0` SSE replay after
  ingestion and exposes a compact `event_replay` summary.
- `trace_ingest_readback_validation` now folds in `trace_ingest_event_replay_validation` when event
  replay is present, checking task binding, replay completion, timeout status, cursor advancement,
  and expected versus observed event counts for `artifact.recorded`, `observation.recorded`,
  `tool_call.recorded`, and `tool_call.status_updated`.
- The direct dry-run readback plan now shows the SSE replay request, while matrix callers keep their
  existing separate scenario event replay validation to avoid double-counting scenario gates.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the standalone event replay gate.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced harness API smoke confirmed `GET /health`, `GET /ready`, and `GET /capabilities`
  for client `codex-trace-event-replay-20260629`; OpenAI provider probe returned HTTP 200 and
  recorded diagnostic id `107` with `provider=openai`, `api_style=responses`, and
  `model=gpt-5.4-mini`.
- Direct standalone `trace_ingest.py --readback --fail-on-readback-invalid` against the prior live
  Agents SDK `tool` trace wrote
  `harness_outputs/sdk-live-smoke-matrix/trace-ingest-live-event-replay-gate-20260629.json` for
  task `task_a1d338f16556`. It returned `valid=true`, full readback `check_count=58`, no failed
  checks, nested `trace_ingest_event_replay_validation.check_count=9`, no failed event replay
  checks, and matching expected/observed event counts:
  `artifact.recorded=1`, `observation.recorded=6`, `tool_call.recorded=1`, and
  `tool_call.status_updated=0`.

## 2026-06-29 Live-smoke publisher provider/API-style map gate

Problem:

- Standalone `trace_ingest.py --readback` and the Agents SDK matrix gate already rejected
  provider/API-style projection-map drift, but `backend_harness/publish_live_agent_smoke_export.py`
  still focused on broad projection counts and stream distribution maps.
- A controller publishing a live-smoke trace through that helper could accept SDK-events and
  client-transcript summaries that retained `agent_model_response_count` while losing
  `agent_model_response_provider_counts` or `agent_model_response_api_style_counts`.

Changes:

- `publish_live_agent_smoke_export.py` now derives
  `expected_model_response_projection_maps` from trace-ingest actions.
- Publisher readback validation now compares those maps against both SDK-event and
  client-transcript summaries and fails if provider/API-style attribution is missing or unknown.
- Publisher readback output now includes the expected provider/API-style maps and summary slices
  include `agent_model_response_provider_counts` and `agent_model_response_api_style_counts`.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the publisher gate.

Verification:

- `.venv/bin/python -m py_compile backend_harness/publish_live_agent_smoke_export.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Stored live SDK trace publisher run wrote
  `harness_outputs/sdk-live-smoke-matrix/publish-live-agent-smoke-export-map-gate-20260629.json`.
  It published task `task_451748c6e68f`, returned `valid=true`, `check_count=75`, no failed checks,
  and reported expected model-response maps:
  `agent_model_response_provider_counts.openai=2` and
  `agent_model_response_api_style_counts.responses=2`. The SDK-events provider-map check,
  SDK-events known-map check, client-transcript API-style map check, and client-transcript known-map
  check all returned `true`.

## 2026-06-29 Trace ingest provider/API-style readback gate

Problem:

- Matrix-level projection validation already failed provider/API-style map drift, but standalone
  `trace_ingest.py --readback --fail-on-readback-invalid` still mainly compared model-response
  counts.
- A controller using trace ingestion directly could accept a readback where
  `/sdk-events?summary=true` or `/client-transcript?summary=true` kept the model-response rows but
  lost `agent_model_response_provider_counts` or `agent_model_response_api_style_counts`.

Changes:

- `trace_ingest.summarize_evidence` now derives expected
  `agent_model_response_provider_counts` and `agent_model_response_api_style_counts` from stored
  `agent_model_response` observations, with `agent_trace` artifact metadata as fallback.
- `trace_ingest.validate_readback` now checks those expected maps against SDK-event and
  client-transcript summaries, and fails when model responses exist but the provider/API-style maps
  are empty or only `none`.
- The validation payload now includes `expected_model_response_projection_maps` for direct
  controller inspection.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the standalone trace-ingest readback gate.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live `tool` scenario matrix with OpenAI provider probe and scenario publish wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-trace-ingest-map-gate-20260629.json`.
  It recorded provider diagnostic id `106`, published scenario task `task_8cf35fb9e94a`,
  generic trace-ingest readback `check_count=49`, total scenario readback `check_count=71`, no
  failed checks, and matching provider/API-style maps:
  `agent_model_response_provider_counts.openai=2` and
  `agent_model_response_api_style_counts.responses=2`.
- Direct standalone `trace_ingest.py --readback --fail-on-readback-invalid` against the same live
  trace wrote
  `harness_outputs/sdk-live-smoke-matrix/trace-ingest-live-provider-map-gate-20260629.json` for
  task `task_756c3891cbd1`. It returned `valid=true`, `check_count=49`, no failed checks, and
  `expected_model_response_projection_maps` with `openai=2` and `responses=2`.

## 2026-06-29 Published scenario projection-map compact readback

Problem:

- Matrix-level compact summaries exposed provider/API-style projection maps, but
  `published_scenario_readback_summary` still centered on check counts, failed-check maps,
  artifacts, sessions, and stream summaries.
- A controller revalidating stored per-scenario evidence could see that projection checks passed
  without seeing the exact `agent_model_response_provider_counts` and
  `agent_model_response_api_style_counts` preserved for each published scenario.

Changes:

- `published_scenario_readback_summary` now includes
  `sdk_events_projection_maps_by_scenario`, `client_transcript_projection_maps_by_scenario`,
  `sdk_events_projection_map_totals`, and `client_transcript_projection_map_totals`.
- Stored-evidence `readback_validation_summary` mirrors those as
  `published_scenario_sdk_events_projection_maps`,
  `published_scenario_client_transcript_projection_maps`,
  `published_scenario_sdk_events_projection_map_totals`, and
  `published_scenario_client_transcript_projection_map_totals`.
- The live matrix compact path can also fall back to scenario publish-readback projection maps when
  matrix summary projection maps are absent.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the new published-scenario compact readback fields.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Stored-evidence revalidation from prior live aggregate matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-projection-maps-published-summary-revalidate-20260629.json`.
  It returned `valid=true`, aggregate `check_count=74`, published scenario `tool`
  total `check_count=63`, no failed checks, and exposed matching published-scenario projection maps:
  `agent_model_response_provider_counts.openai=2` and
  `agent_model_response_api_style_counts.responses=2`.
- `.env`-sourced live `tool` scenario matrix with OpenAI provider probe and scenario publish wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-published-projection-summary-20260629.json`.
  It recorded provider diagnostic id `105`, published task `task_918fb1565b78`, used `321` total
  API tokens, and reported matching SDK-events/client-transcript projection maps with
  `projection_map_delta_status=no_deltas`.

## 2026-06-29 SDK matrix projection-map gate

Problem:

- Individual `/sdk-events?summary=true` and `/client-transcript?summary=true` readbacks exposed
  `agent_model_response_provider_counts` and `agent_model_response_api_style_counts`, but the
  Agents SDK matrix compact summary still only surfaced flat projection counts such as
  `agent_model_response_count`.
- A controller reading only matrix stdout or stored aggregate readback could miss a drift where
  SDK-events and client-transcript both retained model-response rows but lost provider/API-style
  attribution.

Changes:

- `agents_sdk_smoke_matrix.py` now validates provider/API-style projection maps during
  `validate_readback_projection`.
- Matrix summaries now expose `scenario_sdk_events_projection_maps`,
  `scenario_client_transcript_projection_maps`, `sdk_events_projection_map_totals`,
  `client_transcript_projection_map_totals`, `projection_map_deltas`,
  `projection_map_delta_status`, and `projection_map_delta_assessment`.
- Nested map deltas, such as mismatched `agent_model_response_provider_counts.openai` or
  `agent_model_response_api_style_counts.responses`, are always unexpected and add
  `unexpected_projection_map_deltas`.
- Matrix scenario rollup and aggregate readback validation include dedicated checks for the new
  projection-map fields.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the new matrix-level projection-map contract.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live `tool` scenario matrix with provider probe and scenario publish wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-projection-maps-20260629.json`.
  It recorded provider diagnostic id `103`, published task `task_7d4ee09d9240`, and compact output
  reported matching SDK-events/client-transcript projection maps:
  `agent_model_response_provider_counts.openai=2` and
  `agent_model_response_api_style_counts.responses=2`, with `projection_map_delta_status=no_deltas`.
- `.env`-sourced live `tool` scenario matrix with aggregate matrix readback wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-projection-maps-aggregate-20260629.json`
  and aggregate evidence source
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-projection-maps-aggregate-20260629.evidence-source.json`.
  It recorded provider diagnostic id `104`, scenario task `task_1edcbc372a9b`, aggregate task
  `task_378ff105661d`, `matrix_readback_check_count=74`, and no failed checks.
- Stored-evidence revalidation with `--revalidate-from-summary-json` wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-projection-maps-aggregate-revalidate-20260629.json`.
  It returned `valid=true`, `aggregate_check_count=74`, no failed checks, and preserved the same
  provider/API-style projection maps without new live SDK/model calls.

## 2026-06-29 SDK model response provider/API-style read-model projection

Problem:

- `/sdk/context-usage` could group stored model-response usage by provider and API style, but
  `/sdk-events` and `/client-transcript` still projected `agent_model_response` mostly as
  response/model/usage records.
- A controller that consumed only the SDK-like read models could verify model-response counts but
  could not quickly confirm whether those responses came from OpenAI Responses or a local
  OpenAI-compatible chat-completions profile.

Changes:

- `developer_harness.runtime` now projects `provider` and `api_style` on
  `agent_model_response` records in both `/sdk-events` and `/client-transcript`.
- The projection falls back to matching `agent_trace` artifact metadata when older
  `agent_model_response` observations do not contain provider/API-style fields directly.
- `developer_harness.read_model_summary` now exposes
  `agent_model_response_provider_counts` and `agent_model_response_api_style_counts` for both
  SDK-events and client-transcript summaries.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the new projection contract.

Verification:

- `.venv/bin/python -m py_compile developer_harness/runtime.py developer_harness/read_model_summary.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live single-scenario Agents SDK matrix:
  `agents_sdk_smoke_matrix.py --scenario tool --probe-provider --publish-readback --run-id live-projection-provider-20260629`
  wrote `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-projection-provider-20260629.json`.
  It passed `1/1` scenario, recorded provider diagnostic id `102`, endpoint
  `https://api.openai.com/v1/models`, HTTP `200`, model `gpt-5.4-mini`, and published task
  `task_3688ea8fffe6`.
- Live readback files:
  - `harness_outputs/sdk-live-smoke-matrix/sdk-events-summary-live-projection-provider-tool-20260629.json`
  - `harness_outputs/sdk-live-smoke-matrix/client-transcript-summary-live-projection-provider-tool-20260629.json`
  - `harness_outputs/sdk-live-smoke-matrix/sdk-context-usage-live-projection-provider-tool-20260629.json`
- The live task reported `agent_model_response_count=2`,
  `agent_model_response_provider_counts.openai=2`,
  `agent_model_response_api_style_counts.responses=2`, and context usage totals
  `inputTokens=286`, `outputTokens=35`, `totalTokens=321`.
- Backward-compatibility readback against the earlier all-scenario live matrix task
  `task_cb5f56656ca4` also recovered `openai=2` and `responses=2` in both SDK-events and
  client-transcript summaries through artifact metadata fallback.

## 2026-06-29 SDK context usage provider/API-style usage

Problem:

- `/sdk/context-usage` could already split stored model-response usage by task, model, and
  observation status, but it still did not show whether the usage came from OpenAI Responses,
  local OpenAI-compatible chat completions, or another provider profile.
- This matters for the current harness direction because one client can mix OpenAI cloud evidence
  with local-provider evidence, and controllers need to separate those traces before comparing
  cost, capability, and failure behavior.

Changes:

- `developer_harness.runtime` now adds `apiUsage.providers`, `apiUsage.apiStyles`,
  `apiUsage.providerCounts`, `apiUsage.apiStyleCounts`, `apiUsage.providerUsage`, and
  `apiUsage.apiStyleUsage`.
- `providerUsage` is keyed by provider and includes response count, token totals, evidence-ref
  count, task ids, model counts, status counts, and API-style counts.
- `apiStyleUsage` is keyed by API style and includes response count, token totals,
  evidence-ref count, task ids, model counts, status counts, and provider counts.
- Existing ingested evidence can still be grouped because the runtime falls back from
  `agent_model_response` observations to matching `agent_trace` artifact metadata.
- New trace ingestion writes `provider` and `api_style` directly into each
  `agent_model_response` observation.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the new provider/API-style usage maps.

Verification:

- `.venv/bin/python -m py_compile developer_harness/runtime.py developer_harness/verify.py .agents/skills/agentic-harness-loop/scripts/trace_ingest.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced harness API checks passed for `health`, `ready`, and `capabilities`.
- `.env`-sourced `harness_api.py probe --provider openai --model "$FITCHECK_LLM_MODEL"`
  recorded diagnostic id `101`, endpoint `https://api.openai.com/v1/models`, HTTP `200`, model
  `gpt-5.4-mini`, API style `responses`.
- `.env`-sourced live `/sdk/context-usage` against client
  `codex-live-full-matrix-20260629` wrote
  `harness_outputs/sdk-live-smoke-matrix/sdk-context-usage-provider-api-style-live-full-matrix-20260629.json`.
  The response reported `providerCounts.openai=14`, `apiStyleCounts.responses=14`,
  `providerUsage.openai.responseCount=14`, `apiStyleUsage.responses.responseCount=14`,
  `inputTokens=1611`, `outputTokens=223`, and `totalTokens=1834`, matching the existing
  all-scenario live Agents SDK matrix evidence.

## 2026-06-29 SDK context usage model/status API usage

Problem:

- `/sdk/context-usage` exposed aggregate token totals, response rows, and task-level usage, but
  model-level and status-level token distribution still required controllers to regroup the flat
  response list.
- This becomes noisy when one client mixes OpenAI cloud, local OpenAI-compatible profiles, failed
  responses, and completed responses in the same evidence set.

Changes:

- `developer_harness.runtime` now adds `apiUsage.modelUsage` and `apiUsage.statusUsage`.
- `modelUsage` is keyed by model and includes response count, token totals, evidence-ref count,
  task ids, and status counts.
- `statusUsage` is keyed by observation status and includes response count, token totals,
  evidence-ref count, task ids, and model counts.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the new usage maps.

Verification:

- `.venv/bin/python -m py_compile developer_harness/runtime.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live `/sdk/context-usage` against client
  `codex-live-full-matrix-20260629` wrote
  `harness_outputs/sdk-live-smoke-matrix/sdk-context-usage-model-status-live-full-matrix-20260629.json`.
  The response reported `modelUsage.gpt-5.4-mini.responseCount=14`, `inputTokens=1611`,
  `outputTokens=223`, `totalTokens=1834`, `statusCounts.completed=14`, and
  `statusUsage.completed.modelCounts.gpt-5.4-mini=14`, matching
  `agents-sdk-smoke-matrix-live-full-20260629.json` aggregate usage totals.

## 2026-06-29 SDK context usage task-level API usage

Problem:

- The all-scenario live Agents SDK matrix stored 14 `agent_model_response` observations across 8
  scenario evidence-holder tasks, but `/sdk/context-usage` only exposed aggregate usage and a flat
  response list.
- A controller could not inspect task-level token/cost distribution without scanning every response
  entry and regrouping it by task id.

Changes:

- `developer_harness.runtime` now adds `apiUsage.taskCount` and `apiUsage.taskUsage`.
- `taskUsage` is keyed by harness task id and includes response count, input/output/total/cached
  tokens, evidence-ref count, model counts, and status counts.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the task-level usage contract.

Verification:

- `.venv/bin/python -m py_compile developer_harness/runtime.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live `/sdk/context-usage` against client
  `codex-live-full-matrix-20260629` wrote
  `harness_outputs/sdk-live-smoke-matrix/sdk-context-usage-live-full-matrix-20260629.json`.
  The response reported `apiUsage.taskCount=8`, `responseCount=14`,
  `inputTokens=1611`, `outputTokens=223`, `totalTokens=1834`, and every published scenario task
  from `agents-sdk-smoke-matrix-live-full-20260629.json` was present in `taskUsage`.

## 2026-06-29 Provider-aware probe CLI defaults

Problem:

- During a live all-scenario Agents SDK matrix check, a standalone
  `harness_api.py probe --provider openai --model "$FITCHECK_LLM_MODEL"` call probed the Ollama
  default URL `http://localhost:11434/v1/models` and recorded a misleading `not_connected`
  diagnostic even though OpenAI cloud was configured.
- This made the CLI easier to misuse than the matrix runner, which already records an OpenAI
  provider probe against `https://api.openai.com/v1/models`.

Changes:

- `harness_api.py probe` now resolves provider-aware defaults. `--provider openai` uses
  `api_style=responses`, `base_url=https://api.openai.com/v1`,
  `api_key_env=OPENAI_API_KEY`, and `supports_stateful_responses=true` unless the caller
  explicitly overrides those fields.
- Local provider defaults remain OpenAI-compatible chat-completions profiles for Ollama,
  LM Studio, and vLLM-compatible endpoints.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the OpenAI probe default.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/harness_api.py .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced simplified OpenAI probe:
  `harness_api.py probe --provider openai --model "$FITCHECK_LLM_MODEL" --timeout-seconds 10`
  recorded diagnostic id `100` against `https://api.openai.com/v1/models` with HTTP 200.
- `.env`-sourced live all-scenario Agents SDK matrix:
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-full-20260629.json`.
  It passed all 8 scenarios, recorded provider diagnostic id `99` against
  `https://api.openai.com/v1/models`, published 8 scenario evidence-holder tasks, and wrote
  aggregate readback task `task_b4c5f97357b2` with `matrix_readback_valid=true` and
  `matrix_payload_readback_valid=true`.
- Stored-evidence revalidation without new live SDK/provider calls wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-full-revalidate-20260629.json`
  with `aggregate_valid=true`, `aggregate_check_count=64`, and no failed aggregate checks.

## 2026-06-29 Streamed handoff compact distribution maps

Problem:

- Live `stream_handoff` matrix runs validated streamed handoff event/tool-name preservation, but
  compact stdout only showed aggregate stream event counts.
- A controller could not see `handoff_requested`, `handoff_occured`, item-class distribution, or
  `transfer_to_harness_specialist` tool-name preservation without opening the full matrix JSON.

Changes:

- `agents_sdk_smoke_matrix.py` now derives `scenario_stream_projection_summaries` from stored
  SDK-event and client-transcript summaries.
- Stored-evidence revalidation now exposes the same data under
  `published_scenario_stream_projection_summaries`.
- Aggregate matrix summary and scenario-rollup evidence include the stream distribution summaries,
  with dedicated readback checks for summary and rollup drift.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the new compact fields.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live `stream_handoff` matrix:
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-stream-distribution-20260629.json`.
  Provider probe diagnostic id `96` completed against `https://api.openai.com/v1/models`, published
  scenario task `task_197245ffaf95`, aggregate readback task `task_6f19ed6d8d02`, and compact stdout
  exposed `scenario_stream_projection_summaries.stream_handoff` with
  `agent_stream_event_count=30`, `handoff_requested=1`, `handoff_occured=1`,
  `HandoffCallItem=1`, `HandoffOutputItem=1`, and `transfer_to_harness_specialist=3` for both
  SDK-event and client-transcript summaries.
- Stored-evidence revalidation without new live SDK/provider calls wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-stream-distribution-revalidate-20260629.json`
  and exposed the same details under `published_scenario_stream_projection_summaries`.

## 2026-06-29 Agents SDK session compact readback details

Problem:

- The live `session` smoke matrix proved `agent_session_count=1`, two SDK turns, and stored
  observations, but compact controller output only exposed counts.
- A controller could not verify SDK session backend, SDK session id, persisted item counts, item
  type counts, or turn phases from compact stdout alone.

Changes:

- `trace_ingest.py` now derives sanitized `agent_session_summaries` from stored `agent_session`
  observations in `/evidence`.
- `agents_sdk_smoke_matrix.py` mirrors those summaries into live `scenario_session_summaries` and
  stored-evidence `published_scenario_session_summaries`.
- The compact summaries intentionally omit raw session item previews and local session database
  paths.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist cover the new compact fields.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live `session` matrix:
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-session-summaries-20260629.json`.
  Provider probe diagnostic id `94` completed against `https://api.openai.com/v1/models`, published
  scenario task `task_8e1bed716964`, aggregate readback task `task_3b1f8ef151e2`, and compact stdout
  exposed `scenario_session_summaries.session[0]` with `backend=SQLiteSession`,
  `turn_count=2`, `final_item_count=4`, and item type counts `message=2`, `user=2`.
- Stored-evidence revalidation without new live SDK/provider calls wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-session-summaries-revalidate-20260629.json`
  and exposed the same details under `published_scenario_session_summaries`.

## 2026-06-29 Agents SDK smoke matrix runner

Problem:

- Live SDK smoke coverage existed, but running multiple scenarios, probing provider connectivity,
  publishing trace evidence, and validating readback still required several manual commands.
- That made it easy to verify one scenario while accidentally skipping approval, handoff, guardrail,
  session, or stream surfaces.

Changes:

- Added `.agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py`.
- The matrix runner executes `agents_sdk_smoke.py` per selected scenario, writes per-scenario trace
  JSON plus an aggregate matrix JSON, validates each scenario's expected status and `summary.final_ok`,
  aggregates usage totals, and can fail fast.
- With `--probe-provider`, it records an OpenAI `/models` provider diagnostic through the harness
  before running scenarios.
- With `--publish-readback`, it creates one harness evidence-holder task per passing scenario,
  ingests the trace through `trace_ingest` actions, and validates `/sdk-events?summary=true` plus
  `/client-transcript?summary=true` readback.
- `developer_harness/verify.py`, the loop-engineering doc, and the agentic harness API checklist now
  cover the matrix runner contract.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --dry-run --scenario approval --scenario stream_handoff --run-id local-dry-run --model gpt-5.4-mini --output-dir /tmp/fitcheck-sdk-matrix-dry-run --output-json /tmp/fitcheck-sdk-matrix-dry-run/matrix.json`
- `.venv/bin/python -B developer_harness/verify.py`
- Actual FastAPI harness checks returned `health.status=ok`, `ready.status=ready`, and advertised
  `loop_engineering_contract.read_model_summaries`.
- Actual `.env`-backed matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --scenario stream_handoff --run-id live-matrix-20260629 --model gpt-5.4-mini --max-tokens 160 --probe-provider --publish-readback --client-id codex-sdk-matrix-runner-20260629 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-20260629.json`
  completed with provider probe HTTP 200 and diagnostic id `35`.
- The matrix ran 2 scenarios, both passed, and aggregated stored usage evidence of 4 requests,
  565 input tokens, 70 output tokens, and 635 total tokens.
- Matrix readback stored `approval` on `task_8c080d805b1a` and `stream_handoff` on
  `task_cc1e9d626899`; each `trace_ingest` readback ran 36 checks with no failed checks.

### Scenario-specific matrix evidence checks

Problem:

- The first matrix runner version checked process success, expected status, provider-call evidence,
  summary presence, and `summary.final_ok`, but did not fail a scenario if the scenario-specific SDK
  surface disappeared from the summary.
- A bad `approval` trace could therefore pass if it had the final answer but lost approval
  request/decision or resume phase metadata.

Changes:

- `agents_sdk_smoke_matrix.py` now adds scenario-specific required evidence checks.
- `tool` requires tool call/output evidence; `handoff` requires handoff call/output evidence;
  `approval` requires approval request, approval decision, approved decision, `initial` and
  `after_approval` phases, interruption evidence, and approved tool output.
- `guardrail` requires a non-tripped output guardrail result, `guardrail_tripwire` requires the
  tripwire error/result, `session` requires multi-turn session state, `stream` requires raw/run-item
  stream events and tool names, and `stream_handoff` requires streamed handoff events and handoff
  tool-name preservation.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- Direct helper probe confirmed `stream_handoff` and `session` synthetic summaries pass their
  scenario-specific checks.
- `.venv/bin/python -B developer_harness/verify.py`
- Actual `.env`-backed full matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario all --run-id live-required-matrix-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --client-id codex-sdk-matrix-required-20260629 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-required-live-20260629.json`
  completed with provider probe HTTP 200 and diagnostic id `36`.
- The full matrix ran 8 scenarios: `tool`, `handoff`, `approval`, `guardrail`,
  `guardrail_tripwire`, `session`, `stream`, and `stream_handoff`. All 8 passed the
  scenario-specific checks with no failed checks.
- The matrix published 8 harness evidence-holder tasks:
  `task_94de4e2d09f5`, `task_b35e2455e1ad`, `task_3dae6c63b216`,
  `task_27402566a5ce`, `task_4f98ec0c7034`, `task_e0f477795d41`,
  `task_8e32767da710`, and `task_9263542ee3e0`.
- Each published scenario ran 36 `trace_ingest` readback checks with no failed checks.
- The aggregate matrix summary recorded 14 live SDK requests, 1611 input tokens, 223 output tokens,
  and 1834 total tokens as historical usage evidence.

### Scenario-specific readback projection checks

Problem:

- The matrix checked scenario-specific evidence in the live smoke JSON and generic projection counts
  after publish, but it did not separately require those scenario-specific surfaces to survive in
  `/sdk-events?summary=true` and `/client-transcript?summary=true`.
- A projection drift could therefore pass generic `trace_ingest` readback while losing, for example,
  streamed handoff tool-name maps or approval phase/approval counts from the matrix-level contract.

Changes:

- `agents_sdk_smoke_matrix.py` now validates scenario-specific readback projections after
  `--publish-readback`.
- The added `agents_sdk_smoke_matrix_projection_validation` checks both SDK-event and
  client-transcript summaries for scenario-specific projected evidence such as tool progress,
  handoff counts, approval counts/phases, guardrail/run-error records, session records, stream event
  maps, and streamed handoff tool-name preservation.
- A published scenario is accepted only when both generic `trace_ingest_readback_validation` and
  scenario-specific matrix projection validation pass.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- Direct helper probe confirmed `stream_handoff` readback summaries pass only when handoff event and
  tool-name maps are present.
- `.venv/bin/python -B developer_harness/verify.py`
- Actual `.env`-backed projection matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --scenario stream_handoff --run-id live-projection-matrix-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --client-id codex-sdk-matrix-projection-20260629 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-projection-live-20260629.json`
  completed with provider probe HTTP 200 and diagnostic id `37`.
- The approval scenario published to `task_f53a32e5a41a`; generic readback passed and
  `agents_sdk_smoke_matrix_projection_validation` ran 11 checks with no failures.
- The stream handoff scenario published to `task_2b6261316ff6`; generic readback passed and
  `agents_sdk_smoke_matrix_projection_validation` ran 13 checks with no failures.

### Matrix aggregate provider and projection summaries

Problem:

- Matrix output had per-scenario generic readback and scenario projection validation details, but
  the aggregate `summary` did not expose enough controller-friendly status maps.
- External controllers still had to inspect every result item to answer whether the provider probe,
  generic readbacks, scenario-specific projections, and published task ids were all healthy.

Changes:

- `agents_sdk_smoke_matrix.py` now records `provider_probe_validation` as a top-level field.
- Matrix `summary` now includes provider probe validity and failed checks, generic readback status
  counts, scenario projection validation status counts, published task ids, generic readback failed
  scenarios, and projection failed scenarios.
- A failed provider probe now prevents aggregate `summary.status=completed` even if later scenario
  records exist.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- Direct helper probe confirmed aggregate summary exposes `provider_probe_valid=true`,
  `generic_readback_status_counts.valid=1`, `projection_validation_status_counts.valid=1`, and the
  published task id for a synthetic approval record.
- `.venv/bin/python -B developer_harness/verify.py`
- Actual `.env`-backed aggregate matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --scenario stream_handoff --run-id live-aggregate-matrix-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --client-id codex-sdk-matrix-aggregate-20260629 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-aggregate-live-20260629.json`
  completed with provider probe HTTP 200, diagnostic id `38`, and
  `provider_probe_validation.valid=true`.
- Aggregate summary reported `scenario_count=2`, `passed_count=2`, `failed_count=0`,
  `generic_readback_status_counts={"valid": 2}`,
  `projection_validation_status_counts={"valid": 2}`, no failed readback/projection scenarios, and
  API usage totals of 4 requests, 565 input tokens, 70 output tokens, and 635 total tokens.
- The aggregate `published_task_ids` map recorded `approval=task_bb15c4bd33b7` and
  `stream_handoff=task_4756b5f5f2c8`.

### Matrix aggregate evidence readback

Problem:

- The live matrix aggregate JSON could be inspected from the filesystem, but the aggregate gate
  itself was not stored as a harness evidence task.
- Scenario trace readbacks were replayable through harness evidence, while the top-level provider
  probe validation, scenario rollup, and aggregate pass/fail decision remained outside the harness
  readback path.

Changes:

- `agents_sdk_smoke_matrix.py` now supports `--publish-matrix-readback`.
- When enabled, the script writes a stable `.evidence-source.json` aggregate payload, creates an
  aggregate evidence-holder task, records an `agents_sdk_smoke_matrix` artifact, and stores
  `agents_sdk_smoke_matrix_summary`, `agents_sdk_smoke_matrix_scenario_rollup`, and
  `agents_sdk_smoke_matrix_provider_probe_validation` observations.
- The matrix readback validator checks artifact/observation counts, summary status and scenario
  counts, published task id rollup, provider probe validation, and SDK/transcript summary endpoint
  availability before accepting the aggregate task.
- Matrix `summary` now records `matrix_readback_status` and `matrix_readback_valid` when aggregate
  readback publishing is requested.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Synthetic verifier confirms aggregate readback actions create one matrix artifact and three
  matrix observations, and that validation fails when the stored scenario rollup no longer matches
  `summary.published_task_ids`.
- Actual `.env`-backed aggregate readback matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --scenario stream_handoff --run-id live-aggregate-readback-matrix-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-sdk-matrix-aggregate-readback-20260629 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-aggregate-readback-live-20260629.json`
  completed with provider probe diagnostic id `39`, `matrix_readback_status=completed`, and
  `matrix_readback_valid=true`.
- Scenario traces published to `approval=task_be712dee4107` and
  `stream_handoff=task_1be8d60df5dd`; the aggregate matrix published to `task_b9b6f1bce71a`.
- Aggregate matrix readback stored one `agents_sdk_smoke_matrix` artifact and three observations;
  `agents_sdk_smoke_matrix_readback_validation` ran 13 checks with no failures.

### Existing matrix aggregate readback revalidation

Problem:

- After an aggregate matrix task was published, controllers still needed to rerun the live matrix or
  manually combine `/evidence`, `/sdk-events?summary=true`, and `/client-transcript?summary=true`
  to confirm that the stored aggregate task was still valid.
- The aggregate readback validator existed inside the live matrix flow, but not as a standalone
  check over already-published harness evidence.
- The aggregate readback check did not include raw append-only event replay, so controllers could
  verify stored artifacts and observations without proving the same writes were replayable through
  `/events`.

Changes:

- `agents_sdk_smoke_matrix.py` now supports `--validate-matrix-readback-task-id` with
  `--validate-matrix-json`.
- This mode fetches the aggregate task evidence and read-model summaries, derives a result summary
  from the stored evidence counts, runs `agents_sdk_smoke_matrix_readback_validation`, and exits
  nonzero if the stored aggregate evidence no longer matches the supplied matrix JSON.
- Matrix aggregate readback now also fetches raw SSE event replay and validates cursor advancement,
  timeout status, `artifact.recorded` event coverage, and `observation.recorded` event coverage.
- The mode explicitly reports that it does not run new live SDK or provider calls.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Synthetic verifier patches the harness API request function and confirms the mode reads
  `/evidence`, `/sdk-events?summary=true`, `/client-transcript?summary=true`, and `/events`, then
  validates the aggregate task without starting a live smoke run. It also fails when replayed
  `observation.recorded` events no longer cover stored observations.
- Actual stored-task revalidation:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --validate-matrix-readback-task-id task_b9b6f1bce71a --validate-matrix-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-aggregate-readback-live-20260629.json --client-id codex-sdk-matrix-aggregate-readback-20260629 --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-aggregate-readback-revalidate-20260629.json`
  completed with `valid=true`, 18 checks, no failed checks, one matrix artifact, three matrix
  observations, six replayed events, `artifact.recorded=1`, and `observation.recorded=3`.
- Actual `.env`-backed publish-path check:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario tool --run-id live-event-replay-matrix-20260629 --model gpt-5.4-mini --max-tokens 160 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-sdk-matrix-event-replay-20260629 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-event-replay-live-20260629.json`
  completed with provider probe diagnostic id `40`, scenario task `task_07f4456b9466`, aggregate
  task `task_aca3a3e46ae7`, `matrix_readback_valid=true`, 18 aggregate checks, and event replay
  counts of `task.queued=1`, `task.paused=1`, `artifact.recorded=1`, and
  `observation.recorded=3`.

### Tripwire projection totals and transcript final output

Problem:

- A live `guardrail_tripwire` matrix run proved that SDK-event readback exposed
  `agent_guardrail`, `agent_run_error`, and final output evidence, but the top-level matrix summary
  did not aggregate those projection counts.
- The client-transcript summary exposed guardrail and run-error counts, but it did not project
  stored `agent_final_output` observations, so transcript-side final output count stayed at zero
  while SDK events showed `final_output_count=1`.

Changes:

- Matrix `summary` now includes `sdk_events_projection_totals` and
  `client_transcript_projection_totals`, aggregating model response, stream, phase, session,
  handoff, approval, question, guidance, guardrail, run-error, tool-progress, and final-output
  counts from published scenario readbacks.
- `/client-transcript` now includes `agent_final_outputs` projected from stored
  `agent_final_output` observations.
- `summarize_client_transcript()` now reports `final_output_count` and
  `item_type_counts.final_output`.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/runtime.py developer_harness/read_model_summary.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Actual `.env`-backed `guardrail_tripwire` matrix run before the transcript final-output fix:
  diagnostic id `42`, scenario task `task_763f12269d59`, aggregate task `task_ee15e4c7bc01`,
  SDK projection totals included `agent_guardrail_count=1`, `agent_run_error_count=1`, and
  `final_output_count=1`, while transcript final-output count was missing.
- Actual `.env`-backed `guardrail_tripwire` matrix run after the fix:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario guardrail_tripwire --run-id live-tripwire-final-output-projection-matrix-20260629 --model gpt-5.4-mini --max-tokens 160 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-sdk-tripwire-final-output-projection-20260629 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-tripwire-final-output-projection-live-20260629.json`
  completed with provider probe diagnostic id `44`, scenario task `task_fe67cfce9a8d`, aggregate
  task `task_4615b07d6044`, `matrix_readback_valid=true`, 18 aggregate checks, and both
  `sdk_events_projection_totals` and `client_transcript_projection_totals` reporting
  `agent_guardrail_count=1`, `agent_run_error_count=1`, and `final_output_count=1`.

### Scenario trace event replay validation

Problem:

- Aggregate matrix evidence tasks validated raw `/events` replay, but individual scenario trace
  evidence-holder tasks only validated stored evidence and SDK/transcript read-model summaries.
- A scenario trace could therefore pass generic readback and projection checks without proving that
  its artifact, observation, and tool-call writes were replayable through the append-only event log.

Changes:

- `publish_trace_readback()` now fetches raw SSE event replay for each scenario evidence-holder
  task.
- Scenario publish-readback now includes `scenario_event_replay_validation` and an event replay
  summary.
- Matrix `summary` now includes `scenario_event_replay_status_counts` and
  `scenario_event_replay_failed_scenarios`.
- Scenario event replay validation checks replay completion, timeout status, cursor advancement,
  `artifact.recorded` coverage, `observation.recorded` coverage, and `tool_call.recorded`
  coverage against the trace-ingest result summary.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Synthetic verifier confirms scenario event replay validation passes when replayed events cover
  artifact/observation/tool-call counts and fails when `tool_call.recorded` is missing.
- Actual `.env`-backed full matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario all --run-id live-full-replay-matrix-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-sdk-full-matrix-replay-20260629 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-full-replay-live-20260629.json`
  completed with provider probe diagnostic id `45`, `scenario_count=8`, `passed_count=8`,
  `failed_count=0`, `generic_readback_status_counts={"valid": 8}`,
  `projection_validation_status_counts={"valid": 8}`,
  `scenario_event_replay_status_counts={"valid": 8}`, no failed scenarios, and
  `matrix_readback_valid=true`.
- Full matrix scenario tasks: `tool=task_c7b3e22aafbc`, `handoff=task_99d9476517cb`,
  `approval=task_900e36eaa0ae`, `guardrail=task_feb6d7eba157`,
  `guardrail_tripwire=task_416e37c6f03c`, `session=task_1ffeafc9c6d6`,
  `stream=task_ed738d41af29`, and `stream_handoff=task_996677c11f69`.
- Full matrix aggregate task `task_382e73f6e6ea` validated one matrix artifact, three matrix
  observations, and replay counts `task.queued=1`, `task.paused=1`, `artifact.recorded=1`, and
  `observation.recorded=3`.
- Full matrix provider usage totals were 14 requests, 1611 input tokens, 223 output tokens, and
  1834 total tokens.

### Projection total deltas

Problem:

- The full matrix passed, but the aggregate projection totals showed
  `sdk_events_projection_totals.tool_progress_count=4` and
  `client_transcript_projection_totals.tool_progress_count=3`.
- The difference was meaningful: SDK events expose tool progress lifecycle events such as approval
  tool queued and later completed, while client transcript exposes a compact current-state tool row.
- Without an explicit delta field, controllers could miss or misinterpret projection count
  differences when comparing SDK-event and transcript summaries.

Changes:

- Matrix `summary` now includes `projection_total_deltas`, a map of count keys whose SDK-event and
  client-transcript projection totals differ.
- Each delta records `sdk_events`, `client_transcript`, and signed `delta`.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Synthetic verifier fixes the approval-style lifecycle difference:
  `projection_total_deltas={"tool_progress_count": {"sdk_events": 2, "client_transcript": 1, "delta": 1}}`.
- Actual `.env`-backed approval matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --run-id live-approval-delta-matrix-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-sdk-approval-delta-20260629 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-delta-live-20260629.json`
  completed with provider probe diagnostic id `46`, scenario task `task_e6d2eef117f7`,
  aggregate task `task_ebf7ad7707a6`, `generic_readback_status_counts={"valid": 1}`,
  `projection_validation_status_counts={"valid": 1}`, `scenario_event_replay_status_counts={"valid": 1}`,
  and `projection_total_deltas.tool_progress_count={sdk_events: 2, client_transcript: 1, delta: 1}`.

### Aggregate summary payload validation

Problem:

- Aggregate matrix readback validation stored the full `agents_sdk_smoke_matrix_summary` observation,
  but only compared selected fields such as status and scenario count.
- A controller-facing field such as `projection_total_deltas` could be corrupted in stored evidence
  while aggregate readback validation still passed.

Changes:

- `agents_sdk_smoke_matrix_readback_validation` now checks that the stored matrix summary payload
  matches the matrix JSON summary.
- It also reports dedicated checks for SDK projection totals, client-transcript projection totals,
  and projection-total deltas.
- The summary payload comparison intentionally excludes `matrix_readback_status` and
  `matrix_readback_valid`, because those fields are appended to the output summary after aggregate
  readback evidence has already been recorded.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- Synthetic verifier now corrupts stored `projection_total_deltas` and expects
  `matrix_summary_payload_matches` plus `matrix_summary_projection_deltas_match` to fail.
- Revalidating the earlier actual approval matrix task
  `task_ebf7ad7707a6` against
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-delta-live-20260629.json`
  first exposed the self-referential field issue, then passed after the comparison fix with
  `validation.check_count=22` and no failed checks.
- New `.env`-backed approval matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --run-id live-approval-summary-validate-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-sdk-approval-summary-validate-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-summary-validate-live-20260629.json`
  completed with provider probe diagnostic id `47`, scenario task `task_ac2efa1d51c7`,
  aggregate task `task_bb696bc2f5b6`, `matrix_readback_valid=true`, `validation.check_count=22`,
  `failed_checks=[]`, and `projection_total_deltas.tool_progress_count={sdk_events: 2, client_transcript: 1, delta: 1}`.
- Revalidating `task_bb696bc2f5b6` with
  `--validate-matrix-readback-task-id task_bb696bc2f5b6 --validate-matrix-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-summary-validate-live-20260629.json`
  also completed with `validation.check_count=22` and no failed checks.

### Aggregate artifact provenance validation

Problem:

- Aggregate matrix readback validation checked that an `agents_sdk_smoke_matrix` artifact existed, but
  did not prove that the artifact path and digest matched the aggregate evidence-source JSON.
- A stored summary observation could therefore look correct while being attached to the wrong matrix
  artifact.

Changes:

- `agents_sdk_smoke_matrix_readback_validation` now records and validates the matrix artifact path,
  stored SHA-256 digest, digest recomputed from the recorded artifact path, and digest recomputed
  from the expected evidence-source path when available.
- New checks include `matrix_artifact_path_matches_payload`,
  `matrix_artifact_digest_matches_recorded_path`, and
  `matrix_artifact_digest_matches_expected_path`.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Synthetic verifier now corrupts the stored aggregate artifact digest and path and expects the
  artifact provenance checks to fail.
- Revalidating actual aggregate task `task_bb696bc2f5b6` against
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-summary-validate-live-20260629.json`
  completed with `validation.check_count=27`, no failed checks, and matching recorded/expected
  artifact digest `sha256:4e503808f6db972a483c7afc03f0ec1811f3d15725c50e20128bb481ab9f1bbe`.
- Revalidating earlier actual aggregate task `task_ebf7ad7707a6` against
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-delta-live-20260629.json`
  completed with `validation.check_count=27`, no failed checks, and matching recorded/expected
  artifact digest `sha256:00f685d66abc9d05d94fa23f9a2ef4123aaa84eb03ae739bef7d392712290980`.

### Availability-aware artifact digest checks

Problem:

- The aggregate artifact provenance docs said digest recomputation applies when the artifact file is
  available, but the validator failed if an expected artifact path was present and the local file was
  unavailable.
- That was too strict for portable stored-evidence revalidation in another workspace or session where
  the harness database still has artifact metadata but the original file path cannot be read.

Changes:

- Aggregate matrix readback still validates stored artifact path against the matrix payload and
  still validates stored digest format.
- Digest recomputation checks are now enforced only when the recorded or expected artifact file is
  locally available.
- The validation result reports `recorded_path_available` and `expected_path_available` under
  `matrix_artifact` so controllers can distinguish strong local digest verification from portable
  metadata-only revalidation.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- Synthetic verifier now covers a missing local artifact file path and expects stored readback
  validation to remain valid while reporting both availability flags as false.
- `.venv/bin/python -B developer_harness/verify.py`
- Revalidating actual aggregate task `task_bb696bc2f5b6` against
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-summary-validate-live-20260629.json`
  completed with `validation.check_count=27`, no failed checks,
  `recorded_path_available=true`, `expected_path_available=true`, and matching digest
  `sha256:4e503808f6db972a483c7afc03f0ec1811f3d15725c50e20128bb481ab9f1bbe`.

### Aggregate task binding validation

Problem:

- Existing aggregate readback validation fetched evidence for the supplied task id, but did not bind
  that task id back to the SDK-events summary, client-transcript summary, event replay, or
  `matrix_readback.task_id` recorded in the matrix output JSON.
- A controller mistake could therefore ask to validate one aggregate task while supplying another
  task's matrix JSON, and the failure mode would not be explicit.

Changes:

- `agents_sdk_smoke_matrix_readback_validation` now accepts an expected aggregate task id when
  publishing or revalidating matrix readback.
- New checks bind the expected task id to `evidence.task_id`, `/sdk-events?summary=true`,
  `/client-transcript?summary=true`, raw event replay, and `matrix_readback.task_id` when present.
- Validation output now includes a `task_binding` object with every observed task id surface.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- Synthetic verifier now validates the positive task-binding case and a mismatched expected task id,
  expecting evidence, SDK summary, transcript summary, event replay, and matrix payload task-id
  checks to fail together.
- `.venv/bin/python -B developer_harness/verify.py`
- Revalidating actual aggregate task `task_bb696bc2f5b6` against
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-summary-validate-live-20260629.json`
  completed with `validation.check_count=33`, no failed checks, and `task_binding` showing
  `expected_task_id`, evidence task id, SDK-events summary task id, client-transcript summary task
  id, event replay task id, and matrix payload task id all equal to `task_bb696bc2f5b6`.

### Scenario trace readback task binding

Problem:

- Aggregate readback task binding was explicit, but per-scenario trace readback still relied on
  projection and event-count consistency.
- A scenario controller needed the same task-id binding guarantee across the trace-ingest readback
  envelope, stored evidence, SDK-events summary, client-transcript summary, and raw event replay.

Changes:

- `trace_ingest.fetch_readback` now preserves `evidence_task_id`.
- `trace_ingest.validate_readback` accepts `expected_task_id`, checks readback/evidence/SDK summary/
  client-transcript summary task ids, and returns a `task_binding` object.
- `agents_sdk_smoke_matrix.py` passes the scenario evidence-holder task id into trace-ingest
  validation and into scenario event replay validation.
- `agents_sdk_trace_task_event_replay_validation` now checks `event_replay_task_id_matches` when an
  expected task id is supplied.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Synthetic verifier now covers positive trace readback binding, mismatched trace readback task id,
  positive scenario event replay binding, and mismatched scenario event replay task id.
- Revalidating actual approval scenario task `task_ac2efa1d51c7` from
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-summary-validate-live-20260629.json`
  completed with trace-ingest readback `check_count=41`, scenario projection `check_count=11`,
  event replay `check_count=8`, no failed checks, and task binding surfaces all equal to
  `task_ac2efa1d51c7`.
- The raw event replay summary claim boundary now says "requested harness task" instead of
  "aggregate matrix task", so scenario replay output no longer carries aggregate-specific wording.
- New `.env`-backed approval matrix scenario run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --run-id live-approval-scenario-binding-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --client-id codex-sdk-approval-scenario-binding-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-scenario-binding-live-20260629.json`
  completed with provider probe diagnostic id `48`, scenario task `task_8ef761d69ac4`,
  trace-ingest readback `check_count=41`, scenario projection `check_count=11`, event replay
  `check_count=8`, no failed checks, all task-binding surfaces equal to `task_8ef761d69ac4`,
  and `projection_total_deltas.tool_progress_count={sdk_events: 2, client_transcript: 1, delta: 1}`.

### Strict task-binding omissions

Problem:

- The task-binding checks failed when a task id was different, but some missing task-id surfaces could
  still pass when `expected_task_id` was supplied.
- That left a weaker contract than intended: a readback could prove projection counts while not
  proving that every required read-model surface identified the same task.

Changes:

- `trace_ingest_readback_validation` now fails missing readback, evidence, SDK-events summary, or
  client-transcript summary task ids when `expected_task_id` is supplied.
- `agents_sdk_trace_task_event_replay_validation` now fails missing event replay task id when an
  expected task id is supplied.
- Aggregate matrix readback now also fails missing SDK-events, client-transcript, or event replay
  task ids under an expected aggregate task id. `matrix_readback.task_id` remains "compare when
  present" because the publish-time evidence-source JSON is created before the aggregate readback
  result is appended to the final output JSON.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Synthetic verifier now covers missing task ids in trace-ingest readback, scenario event replay, and
  aggregate matrix readback.
- Strict revalidation of actual scenario task `task_8ef761d69ac4` completed with trace-ingest
  readback `check_count=41`, event replay `check_count=8`, no failed checks, and all task-binding
  surfaces equal to `task_8ef761d69ac4`.
- Strict revalidation of actual aggregate task `task_bb696bc2f5b6` completed with
  `validation.check_count=33`, no failed checks, and all task-binding surfaces equal to
  `task_bb696bc2f5b6`.
- Full actual `.env`-backed matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario all --run-id live-full-binding-matrix-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --client-id codex-sdk-full-binding-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-full-binding-live-20260629.json`
  completed with provider diagnostic id `49`, `scenario_count=8`, `passed_count=8`,
  `failed_count=0`, `generic_readback_status_counts={"valid": 8}`,
  `projection_validation_status_counts={"valid": 8}`, and
  `scenario_event_replay_status_counts={"valid": 8}`.
- The full matrix published `tool=task_b723aacc0aa3`, `handoff=task_9b536d29ca61`,
  `approval=task_821c5f3ec7d5`, `guardrail=task_e328ef5c0aa7`,
  `guardrail_tripwire=task_eadaf2526979`, `session=task_d1da0e5b519b`,
  `stream=task_57b0cbcb42b1`, and `stream_handoff=task_e3e31d0ce51c`; each scenario had no
  failed validation checks and all readback/event-replay task-binding surfaces matched its
  published task id.

### Projection delta assessment and aggregate artifact path binding

Problem:

- Actual full matrix readback exposed `projection_total_deltas.tool_progress_count`, but controllers
  still had to know out-of-band whether that difference was an expected lifecycle-vs-compact
  projection difference or evidence drift.
- Actual aggregate publish-time validation recorded the aggregate matrix artifact path, but
  `matrix_artifact.expected_path` was empty until later revalidation because `matrix_evidence_json`
  was added to the output payload after the evidence-source artifact had already been written.

Changes:

- Matrix summaries now include `projection_total_delta_status`,
  `projection_total_delta_allowed_keys`, `projection_total_delta_unexpected_keys`,
  `projection_total_delta_assessment`, and `aggregate_failed_reasons`.
- A higher SDK-event `tool_progress_count` than client-transcript `tool_progress_count` is explicitly
  marked as an allowed lifecycle-vs-compact projection delta.
- Any unregistered projection-count delta, such as missing final-output coverage, is marked
  unexpected and fails the aggregate matrix gate with
  `aggregate_failed_reasons=["unexpected_projection_total_deltas"]`.
- Aggregate readback validation now checks that stored summary evidence preserves the delta status,
  unexpected keys, and full delta assessment.
- Matrix publishing now writes `matrix_evidence_json` into the evidence-source payload before
  storing the aggregate artifact, so publish-time validation can compare `expected_path` and
  recompute both recorded and expected artifact digests immediately.
- Matrix publishing now also emits `matrix_payload_readback_validation` after attaching
  `matrix_readback.task_id` to the final matrix payload, so publish-time output proves final
  task-id binding without requiring a separate revalidation command.
- Matrix summary now mirrors `status`, `valid`, `failed_checks`, and `task_binding` for both the
  initial aggregate readback and the final payload readback; failed readback gates append
  `matrix_readback_failed` or `matrix_payload_readback_failed` to `aggregate_failed_reasons`.
- Matrix summary now also exposes `provider_probe_diagnostic_id` and scenario-to-failed-check maps:
  `scenario_failed_checks`, `generic_readback_failed_checks`, `projection_failed_checks`, and
  `scenario_event_replay_failed_checks`.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Actual `.env`-backed approval matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --run-id live-delta-assessment-path-matrix-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-delta-assessment-path-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-delta-assessment-path-live-20260629.json`
  completed with provider diagnostic id `51`, scenario task `task_846bae6e76c3`, aggregate task
  `task_dac8478ddf1f`, `projection_total_delta_status=allowed_deltas`,
  `projection_total_delta_allowed_keys=["tool_progress_count"]`, no unexpected delta keys,
  `matrix_readback_valid=true`, and aggregate validation `check_count=36`.
- Existing aggregate readback revalidation:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --validate-matrix-readback-task-id task_dac8478ddf1f --validate-matrix-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-delta-assessment-path-live-20260629.json --client-id codex-delta-assessment-path-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-delta-assessment-path-revalidate-20260629.json`
  completed with no failed checks, `matrix_payload_task_id=task_dac8478ddf1f`, matching
  `matrix_artifact.expected_path`, and matching recorded/expected SHA-256 digests.
- Actual `.env`-backed final-payload readback matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --run-id live-final-payload-readback-matrix-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-final-payload-readback-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-final-payload-readback-live-20260629.json`
  completed with provider diagnostic id `52`, scenario task `task_9ca81b80fb22`, aggregate task
  `task_9fd101b0518e`, `matrix_payload_readback_valid=true`, no payload failed checks, and all
  final-payload task-binding surfaces equal to `task_9fd101b0518e`.
- Existing aggregate readback revalidation of the same final payload:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --validate-matrix-readback-task-id task_9fd101b0518e --validate-matrix-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-final-payload-readback-live-20260629.json --client-id codex-final-payload-readback-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-final-payload-readback-revalidate-20260629.json`
  completed with no failed checks, `matrix_payload_task_id=task_9fd101b0518e`, and matching
  recorded/expected artifact path plus digest.
- Actual `.env`-backed summary readback rollup matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --run-id live-summary-readback-rollup-matrix-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-summary-readback-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-readback-rollup-live-20260629.json`
  completed with provider diagnostic id `53`, scenario task `task_8c1e9331b2d3`, aggregate task
  `task_baabedf982a2`, empty aggregate failed reasons, empty readback failed checks, and summary
  task-binding rollups for both aggregate readback stages.
- Existing aggregate readback revalidation of the same summary-rollup payload:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --validate-matrix-readback-task-id task_baabedf982a2 --validate-matrix-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-readback-rollup-live-20260629.json --client-id codex-summary-readback-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-readback-rollup-revalidate-20260629.json`
  completed with no failed checks and `matrix_payload_task_id=task_baabedf982a2`.
- Actual `.env`-backed summary failure-map matrix run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --run-id live-summary-failure-map-matrix-20260629 --model gpt-5.4-mini --max-tokens 180 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-summary-failure-map-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-failure-map-live-20260629.json`
  completed with provider diagnostic id `54`, scenario task `task_5f3ee64f8ff6`, aggregate task
  `task_050ae60c87fd`, empty scenario/readback/projection/replay failed-check maps, and final
  payload task binding equal to `task_050ae60c87fd`.
- Existing aggregate readback revalidation of the same failure-map payload:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --validate-matrix-readback-task-id task_050ae60c87fd --validate-matrix-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-failure-map-live-20260629.json --client-id codex-summary-failure-map-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-failure-map-revalidate-20260629.json`
  completed with no failed checks and `matrix_payload_task_id=task_050ae60c87fd`.

## 2026-06-29 Live SDK smoke summary expansion

Problem:

- `agents_sdk_smoke.py` stored rich live SDK evidence in `new_items`, `raw_responses`, phases,
  sessions, guardrails, and stream events, but the top-level `summary` only exposed shallow totals.
- Controllers had to inspect the full trace or wait for harness readback to quickly distinguish
  approval/resume boundaries, raw response usage, item classes, stream subtypes, and tripwires.
- A refactor bug in the approval resume path was caught by the actual `.env`-backed approval smoke
  before publishing, confirming that this path needs live coverage in addition to synthetic tests.

Changes:

- `agents_sdk_smoke.py` now builds a richer top-level `summary` with new item type/class maps,
  raw response count/output-type maps, API usage totals, phase names and per-phase counts,
  approval decision maps, guardrail tripwire counts, session item type maps, and stream event
  distribution maps.
- Stream smoke summaries preserve tool names across streamed tool/handoff output events by call id,
  while avoiding generic SDK item classes as false tool names.
- `developer_harness/verify.py` now asserts the expanded SDK smoke summary contract with synthetic
  approval, stream, guardrail, session, usage, and phase evidence.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- OpenAI provider probe through the harness used `X-Harness-Client-Id:
  codex-live-sdk-summary-20260629`, returned HTTP 200 from `https://api.openai.com/v1/models`,
  and recorded diagnostic id `34`.
- Live `.env`-backed `approval` smoke produced `status=completed`, model `gpt-5.4-mini`, final
  output `AGENTS_SDK_APPROVAL_FINAL_OK`, phase names `initial` and `after_approval`, approval
  decision count `approved=1`, and usage total `396` tokens.
- Live `.env`-backed `guardrail_tripwire` smoke produced
  `status=guardrail_tripwire_triggered`, model `gpt-5.4-mini`, final output
  `AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK`, and `guardrail_tripwire_count=1`.
- Live `.env`-backed `stream_handoff` smoke produced `status=completed`, model `gpt-5.4-mini`,
  final output `AGENTS_SDK_STREAM_HANDOFF_FINAL_OK`, 30 stream events, and stream tool-name counts
  `transfer_to_harness_specialist=3`, `none=27`.
- Actual publisher readback stored `task_ae38b28ac7e7` for approval, `task_86a13a57baf4` for
  guardrail tripwire, and `task_ff8529c62560` for stream handoff. Each ran 67 readback checks with
  no failed checks.
- Actual `trace_ingest.py --readback --fail-on-readback-invalid` stored the approval trace on
  `task_33d5faace4d9`, ran 36 checks with no failures, and confirmed `agent_approval=2`,
  `agent_phase=2`, `agent_model_response=2`, and `final_output=1`.

## 2026-06-29 Live streaming SDK readback distribution

Problem:

- Previous live matrix coverage verified stream event totals, but not whether important streaming
  subtypes survived summary readback.
- For `stream_handoff`, the total event count alone would not catch losing `handoff_requested`,
  `handoff_occured`, `HandoffCallItem`, or `HandoffOutputItem`.
- Stream event tool-name extraction could expose generic item class names such as
  `MessageOutputItem` as if they were actual tool names.

Changes:

- `developer_harness.read_model_summary` now includes stream event distribution maps for event
  type, event name, raw response event type, item class, and tool name in both SDK-event and
  client-transcript summaries.
- `trace_ingest.py --readback` compares those stream distribution maps against stored
  `agent_stream_event` observations and returns them under `expected_stream_event_counts`.
- `backend_harness/publish_live_agent_smoke_export.py` validates the same stream distribution maps
  for publisher readback.
- Stream event ingestion no longer records generic item class names as tool names. Tool output
  stream events use the call-id tool-name map when available.

Verification:

- Live `.env`-backed `stream` smoke produced `status=completed`, `external_api_called=true`,
  model `gpt-5.4-mini`, 37 stream events, and final output `AGENTS_SDK_STREAM_FINAL_OK`.
- Live `.env`-backed `stream_handoff` smoke produced `status=completed`,
  `external_api_called=true`, model `gpt-5.4-mini`, 30 stream events, and final output
  `AGENTS_SDK_STREAM_HANDOFF_FINAL_OK`.
- Actual publisher readback for `stream` stored `task_4dda46cdd2bb`, ran 67 checks with no
  failures, and confirmed stream tool-name counts `record_signal=2`, `none=35`.
- Actual publisher readback for `stream_handoff` stored `task_0d97bf1e4249`, ran 67 checks with no
  failures, and confirmed `handoff_requested=1`, `handoff_occured=1`, `HandoffCallItem=1`,
  `HandoffOutputItem=1`, and tool-name counts `transfer_to_harness_specialist=3`, `none=27`.
- Actual `trace_ingest.py --readback --fail-on-readback-invalid` stored the same
  `stream_handoff` trace on `task_94080ffba0d2`, ran 36 checks with no failures, recorded
  3 artifacts and 37 observations, and validated the same stream distribution maps.
- `.venv/bin/python -m py_compile developer_harness/read_model_summary.py .agents/skills/agentic-harness-loop/scripts/trace_ingest.py backend_harness/publish_live_agent_smoke_export.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`

## 2026-06-29 Live SDK scenario matrix readback

Problem:

- Approval-only live testing proved the approval/resume path, but not the SDK surfaces for
  guardrail tripwires, SDK sessions, or handoffs.
- Readback validation exposed the full boolean check map, but controllers still had to derive
  `failed_checks` and check counts themselves.

Changes:

- Ran live `.env`-backed Agents SDK smoke tests for `guardrail_tripwire`, `session`, and
  `handoff`, then published each trace into the generic harness with expanded publisher readback.
- `trace_ingest_readback_validation` and publisher readback validation now include `check_count`
  and `failed_checks` alongside the raw `checks` map.
- `developer_harness/verify.py` now asserts the new validation metadata contract.

Verification:

- Live `guardrail_tripwire` smoke produced `status=guardrail_tripwire_triggered`,
  `external_api_called=true`, model `gpt-5.4-mini`, error
  `OutputGuardrailTripwireTriggered`, and final output
  `AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK`.
- Live `session` smoke produced `status=completed`, `external_api_called=true`, model
  `gpt-5.4-mini`, and final output `AGENTS_SDK_SESSION_FINAL_OK`.
- Live `handoff` smoke produced `status=completed`, `external_api_called=true`, model
  `gpt-5.4-mini`, and final output `AGENTS_SDK_HANDOFF_FINAL_OK`.
- Actual FastAPI publisher readbacks with `X-Harness-Client-Id: codex-live-sdk-matrix-20260629`
  stored:
  - `task_9af912af55b3`: expected `agent_guardrail=1`, `agent_run_error=1`,
    `agent_model_response=1`, `agent_phase=1`, `final_output=1`; validation passed.
  - `task_5625c361e784`: expected `agent_session=1`, `agent_model_response=2`,
    `agent_phase=2`, `final_output=1`; validation passed.
  - `task_e1de92295761`: expected `agent_handoff=2`, `agent_model_response=2`,
    `agent_phase=1`, `final_output=1`; validation passed.
- Actual publisher metadata smoke with
  `X-Harness-Client-Id: codex-live-sdk-matrix-metadata-20260629` stored `task_900ed933cfeb` and
  returned `validation.check_count=57`, `validation.failed_checks=[]`, expected
  `agent_guardrail=1`, `agent_run_error=1`, `agent_model_response=1`, `agent_phase=1`, and
  `final_output=1`.
- Actual `trace_ingest.py --readback --fail-on-readback-invalid` metadata smoke stored the same
  guardrail tripwire trace on `task_245498633d6c`, returned `validation.check_count=26`,
  `validation.failed_checks=[]`, and recorded 2 artifacts, 6 observations, and 0 tool calls.
- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py backend_harness/publish_live_agent_smoke_export.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`

## 2026-06-29 Expanded publisher readback validation

Problem:

- `trace_ingest.py --readback` had been expanded to validate more Agent SDK surfaces, but
  `backend_harness/publish_live_agent_smoke_export.py` still used a narrower readback contract.
- A live-smoke export could therefore be accepted by the publisher while approval, phase, session,
  handoff, guardrail, run-error, or final-output projection counts drifted in the generic harness
  read models.

Changes:

- The publisher now derives minimum projection counts from trace-ingest actions for model
  responses, stream events, phases, sessions, handoffs, approvals, questions, guidance, guardrails,
  run errors, and final output.
- Publisher readback validation now checks both `/sdk-events` and `/client-transcript` projections,
  plus the direct server summary count fields for those surfaces.
- Publisher readback output now includes the expanded transcript counts and summary slices so
  controllers can see what was checked without fetching the full read models again.

Verification:

- `.venv/bin/python -m py_compile backend_harness/publish_live_agent_smoke_export.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Actual FastAPI harness checks returned `health.status=ok`, `ready.status=ready`, and advertised
  `loop_engineering_contract.read_model_summaries`.
- Actual publisher verification with
  `X-Harness-Client-Id: codex-publisher-expanded-counts-20260629` and the `.env`-backed live SDK
  approval trace `harness_outputs/sdk-live-smoke/agents-sdk-approval-live-readback-20260629.json`
  stored `task_ae647347e4b7` with 2 artifacts, 9 observations, and 1 tool call.
- The publisher readback validation ran 57 checks with no failures. It confirmed expected
  projection counts `agent_approval=2`, `agent_phase=2`, `agent_model_response=2`, and
  `final_output=1`, with matching SDK-event and client-transcript summary counts.
- `sdk-context-usage` for the publisher client reported stored API usage from 2
  `agent_model_response` observations, model `gpt-5.4-mini`, and 396 total tokens. This remains
  stored evidence readback, not a current provider-connectivity claim.

## 2026-06-29 Expanded trace ingest readback validation

Problem:

- `trace_ingest.py --readback` checked model response, stream event, and guidance counts, but it
  did not validate several Agent SDK surfaces already preserved by the harness.
- Approval, phase, session, handoff, guardrail, run-error, and final-output projection drift could
  therefore be missed even when the trace was accepted and summary endpoints were readable.

Changes:

- `developer_harness.read_model_summary` now summarizes phase, session, handoff, approval,
  guardrail, and run-error counts for both `/sdk-events?summary=true` and
  `/client-transcript?summary=true`.
- `trace_ingest_readback_validation` now compares stored evidence with read-model summary counts
  for model responses, stream events, phases, sessions, handoffs, approvals, questions, guidance,
  guardrails, run errors, and SDK final-output coverage.
- `developer_harness/verify.py`, the loop-engineering doc, and the agentic harness API checklist
  now cover the expanded count contract.

Verification:

- `.venv/bin/python -m py_compile developer_harness/read_model_summary.py .agents/skills/agentic-harness-loop/scripts/harness_api.py .agents/skills/agentic-harness-loop/scripts/trace_ingest.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Live `.env`-backed Agents SDK approval smoke:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke.py --scenario approval --output-json harness_outputs/sdk-live-smoke/agents-sdk-approval-live-readback-20260629.json --max-tokens 160`
  produced `status=completed`, `external_api_called=true`, model `gpt-5.4-mini`, 2 raw model
  responses, one approval decision, final output `AGENTS_SDK_APPROVAL_FINAL_OK`, and the CLI
  reported the approval marker was observed.
- Actual HTTP ingest/readback with
  `X-Harness-Client-Id: codex-readback-expanded-counts-20260629` stored the trace on
  `task_e59376eee0d9` with 2 artifacts, 9 observations, 1 tool call, 2 model-response
  observations, 2 phase observations, approval request/decision observations, and one
  `agent_final_output` observation.
- Strict readback smoke with `--fail-on-readback-invalid` exited 0. The validation ran 26 checks,
  had no failed checks, and confirmed `agent_approval=2`, `agent_phase=2`, `agent_model_response=2`,
  and `final_output=1` across evidence and read-model summaries.
- `sdk-context-usage` for the same client reported stored API usage from 2 `agent_model_response`
  observations, model `gpt-5.4-mini`, and 396 total tokens. This is stored evidence readback, not a
  current connectivity claim.

## 2026-06-28 Trace ingest readback validation option

Problem:

- The manual SDK trace workflow required running `trace_ingest.py`, then separately fetching
  `/evidence`, `/sdk-events?summary=true`, and `/client-transcript?summary=true`.
- That made controller smoke tests and human verification repeat the same readback steps and made
  it easier to miss a projection drift after the trace was accepted by the API.

Changes:

- `trace_ingest.py` now supports `--readback`.
- Dry-run output includes the readback GET paths that will be checked.
- Execution output includes `readback.source=trace_ingest_readback`, task evidence counts,
  SDK-event summary, client-transcript summary, and
  `validation.source=trace_ingest_readback_validation`.
- The validation compares stored evidence counts against the result summary and checks that
  key projection counts for model responses, stream events, and guidance match between evidence
  and read models.
- `--fail-on-readback-invalid` can be combined with `--readback` to return a nonzero process exit
  when projection validation fails.
- The API checklist and loop-engineering doc now document the option and its claim boundary.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Live `.env`-backed Agents SDK approval smoke:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke.py --scenario approval --output-json harness_outputs/sdk-live-smoke/agents-sdk-approval-live-readback-20260628.json --max-tokens 160`
  produced `status=completed`, `external_api_called=true`, model `gpt-5.4-mini`, 2 raw model
  responses, one approval request/decision, and final output `AGENTS_SDK_APPROVAL_FINAL_OK`.
- Actual HTTP ingest/readback with `X-Harness-Client-Id: codex-live-sdk-readback-20260628` stored
  the trace on `task_d2ed4c19713f` with 2 artifacts, 9 observations, 1 tool call,
  `trace_ingest_readback_validation.valid=true`, SDK summary `agent_model_response_count=2`, and
  final-output projection present.
- Strict readback smoke with `--fail-on-readback-invalid` and
  `X-Harness-Client-Id: codex-live-sdk-readback-strict-20260628` stored the same live trace on
  `task_3692d55d2885`; the command exited 0 with 2 artifacts, 9 observations, 1 tool call,
  `validation.valid=true`, and `sdk-context-usage` reported 2 stored model responses and 396 total
  tokens for `gpt-5.4-mini`.

## 2026-06-28 Shared read-model summary implementation

Problem:

- `developer_harness.runtime` used `developer_harness.read_model_summary` for server-side
  `/sdk-events?summary=true` and `/client-transcript?summary=true` responses.
- `harness_api.py` carried a duplicate local summary implementation for fallback behavior.
- That duplication could make SDK-style controller checks drift between direct server readback and
  CLI fallback readback as new projection fields are added.

Changes:

- `harness_api.py` now imports `summarize_sdk_events` and `summarize_client_transcript` from
  `developer_harness.read_model_summary`.
- The duplicate CLI summary helper implementations were removed.
- `developer_harness/verify.py` now asserts that the CLI exposes the shared summary functions from
  `developer_harness.read_model_summary`.
- `docs/20-general-agent-loop-engineering.md` and the `agentic-harness-loop` checklist document
  the shared summary contract.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/harness_api.py developer_harness/read_model_summary.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`

## 2026-06-28 Close-fit guidance projection in generic harness

Problem found while checking whether FitCheck's close-fit loop evidence survives SDK/harness
readback:

- Product traces can express "do not report yet; continue candidate enrichment/search" as
  backend guidance or an `agent_guardrail` item with `result.guidance_type`.
- The generic harness trace ingest path preserved model/tool evidence, questions, guardrails, and
  stream events, but did not turn this runtime guidance into a typed observation.
- A controller reading only `/sdk-events` or `/client-transcript` could therefore miss the reason
  why the agent should keep searching for closer size measurements instead of finalizing a weak
  shortlist.

Changes:

- `trace_ingest.py` now recognizes top-level guidance records and `items[]` records such as
  `agent_guardrail` with `guidance_type`, `required_action`, or `next_tool_suggestions`.
- Guidance is stored as `tool_name=agent_guidance` with evidence refs like
  `artifact:agent_trace:<file>` and `agent_guidance:<index>`.
- The generic harness projects `agent_guidance` observations into both `/sdk-events` and
  `/client-transcript`, preserving reason, required action, suggested backend tools,
  `close_fit_search_budget`, recovery policy, agent, status, and claim boundary.
- The live-smoke publisher readback now derives expected `agent_guidance` counts from the source
  trace and checks both SDK-event and transcript guidance projection.
- `developer_harness/verify.py` now covers dry-run trace ingestion, mocked publisher readback, and
  runtime read-model projection for `continue_search_for_closer_measurements`.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py developer_harness/runtime.py developer_harness/verify.py backend_harness/publish_live_agent_smoke_export.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`
- Actual publisher verification with harness API:
  `.venv/bin/python backend_harness/publish_live_agent_smoke_export.py --client-id codex-guidance-readback-2-20260628 --idempotency-key fitcheck-live-smoke-guidance-readback-v2-run_770c3fcec4ad --trace-json harness_outputs/live_agent_smoke_autopublish_20260628/run_770c3fcec4ad-fitcheck-live-agent-trace.json --eval-case-json harness_outputs/live_agent_smoke_autopublish_20260628/run_770c3fcec4ad-fitcheck-live-agent-eval-case.json`
  produced `task_51650700ba2c`, `eval_case_id=53`, read back 25 tool calls, 129 observations,
  132 SDK-projected events, `sdk_events.agent_guidance=3`,
  `client_transcript.agent_guidance_count=3`, and `readback.validation.valid=true`.

Follow-up improvement from the next SDK/harness pass:

- Live-smoke trace exports now preserve SDK-projected guidance as top-level `agent_guidance`, not
  only as converted trace `items[]`. Eval-case expected metadata now records whether guidance should
  be projected and the expected guidance reason.
- `trace_ingest.py` deduplicates semantically identical guidance when the same signal appears both
  as top-level `agent_guidance` and as a guidance-like trace item. The fingerprint ignores source,
  item type, status, and guidance-type decoration so a controller does not see duplicate
  "continue searching before reporting" events.
- The `agentic-harness-loop` API checklist now documents `agent_guidance` evidence and the semantic
  dedupe rule.

Follow-up verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py backend_harness/verify_live_agent_smoke.py developer_harness/verify.py backend_harness/publish_live_agent_smoke_export.py`
- `cd backend_harness && ../.venv/bin/python -B verify_live_agent_smoke.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Actual HTTP publisher verification with duplicate top-level/item guidance trace:
  `.venv/bin/python backend_harness/publish_live_agent_smoke_export.py --client-id codex-guidance-dedupe-20260628 --idempotency-key fitcheck-guidance-dedupe-http-run_guidance_dedupe_http --trace-json harness_outputs/guidance_dedupe_http_20260628/run_guidance_dedupe_http-fitcheck-live-agent-trace.json --eval-case-json harness_outputs/guidance_dedupe_http_20260628/run_guidance_dedupe_http-fitcheck-live-agent-eval-case.json`
  produced `task_0c4bba550091`, `eval_case_id=54`, `agent_guidance=1` in both SDK events and
  client transcript, and `readback.validation.valid=true`.
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`
- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`

Additional SDK/read-model CLI improvement:

- `harness_api.py sdk-events --summary true` now returns a compact SDK-event summary with event
  type counts, `summary_requested=true`, `agent_guidance` count/reasons, reason-count maps,
  suggested tool count, missing-info question count, final-output flags, cursor metadata, and a
  historical-evidence claim boundary.
- `harness_api.py client-transcript --summary true` now returns the same style of compact
  transcript summary from top-level transcript projections or message items, including aggregate
  `item_type_counts`, so external controllers can quickly check whether "continue searching before
  reporting" guidance survived readback.
- `harness_api.py plan --goal task` and `plan --goal trace` now recommend the summary commands.

Additional SDK/read-model CLI verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/harness_api.py developer_harness/verify.py`
- `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/harness_api.py plan --goal trace`
- `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/harness_api.py --dry-run --client-id verify-client sdk-events --task-id task_verify --after 12 --summary true`
- `.venv/bin/python -B developer_harness/verify.py`
- Actual HTTP summary readback against `task_0c4bba550091` with
  `X-Harness-Client-Id: codex-guidance-dedupe-20260628` returned
  `harness_sdk_events_summary.agent_guidance_count=1`,
  `harness_client_transcript_summary.agent_guidance_count=1`, and guidance reason
  `continue_search_for_closer_measurements`.
- Actual live product smoke on run `run_8d971a5ab1bb` used OpenAI-compatible Responses transport,
  Brave Search, Tesseract OCR, and backend-owned tool execution. It recorded
  `external_api_called=true`, 54 model-selected/backend-executed tool calls, final product status
  `waiting_for_user`, missing field `baseline_evidence`, and no weak final report. Export/publish
  created harness task `task_00e0ffacb6d8`, `eval_case_id=55`, 148 SDK stream events, 54
  `tool_progress` records, and `readback.validation.valid=true`.
- Actual HTTP summary readback against `task_00e0ffacb6d8` with
  `X-Harness-Client-Id: codex-live-guidance-summary-20260628` returned
  `agent_guidance_count=4`, reason counts
  `context_sources_require_backend_tool_followup=1`,
  `candidate_enrichment_required_before_scoring=2`, and
  `baseline_evidence_recovery_required_before_report=1`; the extra candidate-enrichment guidance
  reflected a later, larger suggested-tool set rather than a semantic duplicate.
- The live-smoke publisher readback now embeds compact SDK-event and client-transcript summaries
  in its accepted readback result. Validation checks require `summary_requested=true`, guidance
  counts to match the projected read models, and client transcript `item_type_counts` to be present.
- Actual OpenAI provider probe through the generic harness with
  `X-Harness-Client-Id: codex-publisher-summary-20260628` reached
  `https://api.openai.com/v1/models`, returned HTTP 200, and recorded `diagnostic_id=33`.
- Actual publisher verification with the summary readback contract:
  `.venv/bin/python backend_harness/publish_live_agent_smoke_export.py --client-id codex-publisher-summary-20260628 --idempotency-key fitcheck-live-smoke-publisher-summary-run_8d971a5ab1bb --trace-json harness_outputs/live_agent_smoke_guidance_summary_20260628/run_8d971a5ab1bb-fitcheck-live-agent-trace.json --eval-case-json harness_outputs/live_agent_smoke_guidance_summary_20260628/run_8d971a5ab1bb-fitcheck-live-agent-eval-case.json`
  produced `task_2780a8d338c3`, `eval_case_id=56`, 54 tool calls, 225 observations, 1 artifact,
  `readback.validation.valid=true`, SDK summary `agent_guidance_count=4`, and transcript
  `item_type_counts` with `tool_progress=54`, `agent_model_response=15`, `agent_stream_event=148`,
  `agent_question=2`, and `agent_guidance=4`.
- External CLI readback against `task_2780a8d338c3` with
  `harness_api.py sdk-events --summary true` and `client-transcript --summary true` returned
  the same guidance reason-count map and `missing_info_question_count=2`.
- Server-side read-model summaries are now available directly through
  `/api/harness/v1/tasks/{task_id}/sdk-events?summary=true` and
  `/api/harness/v1/tasks/{task_id}/client-transcript?summary=true`, not only through the CLI
  wrapper. Actual HTTP readback against `task_2780a8d338c3` with
  `X-Harness-Client-Id: codex-publisher-summary-20260628` returned
  `harness_sdk_events_summary.summary_requested=true`,
  `harness_client_transcript_summary.summary_requested=true`, `agent_guidance_count=4`,
  `missing_info_question_count=2`, `tool_progress_count=54`, and transcript `item_type_counts`
  with `agent_stream_event=148`, `agent_model_response=15`, and `agent_guidance=4`.
- `harness_api.py sdk-events --summary true` and `client-transcript --summary true` now forward
  `summary=true` to the server instead of fetching the full read model and summarizing locally.
  Dry-run now shows `/sdk-events?after=12&summary=true` and
  `/client-transcript?summary=true`; live CLI calls against `task_2780a8d338c3` hit those
  server routes and returned the same summary counts above.
- The live-smoke publisher readback now also calls the server summary endpoints directly:
  `GET /tasks/{task_id}/sdk-events?summary=true` and
  `GET /tasks/{task_id}/client-transcript?summary=true`. The full read models are still fetched
  for projection-count validation, but compact summary validation no longer depends on local
  `harness_api.summarize_*` fallback behavior.
- Publisher readback validation now checks server-summary source labels, matching projected SDK
  event counts, guidance counts, and transcript `item_type_counts`.
- `/api/harness/v1/capabilities` now advertises the summary discovery surface as
  `loop_engineering_contract.read_model_summaries =
  sdk_events_and_client_transcript_summary_views_for_fast_controller_checks`, so external
  controllers can discover the compact summary views before fetching full read models.
- Actual HTTP checks against a running FastAPI instance returned `health.status=ok`,
  `ready.status=ready`, and the new `read_model_summaries` capability value above.
- Actual publisher verification after the direct-server-summary change:
  `.venv/bin/python backend_harness/publish_live_agent_smoke_export.py --client-id codex-publisher-server-summary-20260628 --idempotency-key fitcheck-live-smoke-publisher-server-summary-run_8d971a5ab1bb --trace-json harness_outputs/live_agent_smoke_guidance_summary_20260628/run_8d971a5ab1bb-fitcheck-live-agent-trace.json --eval-case-json harness_outputs/live_agent_smoke_guidance_summary_20260628/run_8d971a5ab1bb-fitcheck-live-agent-eval-case.json`
  produced `task_a8b0d71d9597`, `eval_case_id=57`, and `readback.validation.valid=true`.
  Uvicorn logs showed direct `GET /api/harness/v1/tasks/task_a8b0d71d9597/sdk-events?summary=true`
  and `GET /api/harness/v1/tasks/task_a8b0d71d9597/client-transcript?summary=true` requests.
  The accepted server summaries reported `agent_guidance_count=4`,
  `missing_info_question_count=2`, SDK `tool_progress=54`, `agent_stream_event=148`, and
  transcript `item_type_counts.agent_guidance=4`.
- `.venv/bin/python -B developer_harness/verify.py`
- `cd backend_harness && ../.venv/bin/python -B verify_live_agent_smoke.py` returned the expected
  non-live skip contract when `FITCHECK_RUN_LIVE_AGENT_SMOKE` was unset.
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`
- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`

## 2026-06-28 Live smoke SDK/eval evidence export

Problem found while improving the SDK and harness loop:

- `verify_live_agent_smoke.py` checked the live product agent route and SDK-like projections, but
  the result was only printed to stdout. That made it harder to preserve a live failure or
  waiting-for-user trace as reusable harness evidence or an eval seed.
- The generic development harness already supports typed evidence, trace ingestion, and
  `eval-cases`, so product live smoke should be able to produce compatible artifacts without
  routing FitCheck production behavior through the generic harness.

Changes:

- `verify_live_agent_smoke.py` can now export a trace-ingest-compatible JSON artifact when
  `FITCHECK_LIVE_AGENT_EXPORT_DIR` or `FITCHECK_LIVE_AGENT_TRACE_EXPORT_PATH` is set.
- The same smoke can export a generic harness `EvalCaseRecord` JSON when
  `FITCHECK_LIVE_AGENT_EXPORT_DIR` or `FITCHECK_LIVE_AGENT_EVAL_CASE_EXPORT_PATH` is set.
- The exported trace preserves model response summaries, tool call/tool output items, SDK events,
  client transcript counts, missing-info questions, final output grounding metadata, and the
  strict expectation values used by the smoke.
- The exported eval case records the expected run status, SDK events projection, client transcript
  projection, backend recovery reason/tools, and an explicit claim boundary that live provider
  connectivity is proven only by the smoke result evidence.
- `developer_harness/verify.py` now loads the FitCheck live smoke export contract so the generic
  harness verification catches drift in the product smoke artifact/eval shape.
- `backend_harness/publish_live_agent_smoke_export.py` publishes an exported live-smoke trace and
  eval JSON into the generic harness. It can create an evidence-holder task, reuse
  `trace_ingest.py` actions, attach the eval case to the created task, and report evidence counts.
  This codifies the manual `task -> trace_ingest -> eval-case` workflow while keeping FitCheck
  production runtime separate from the generic development harness.
- The publisher now performs a readback verification after live publication: `GET /tasks/{task_id}`,
  `/evidence`, `/sdk-events`, `/client-transcript`, and `/eval-cases` must all agree that the
  trace artifact, typed evidence, SDK-style projections, and eval case are stored under the same
  client-scoped task before the publish result is accepted.
- The readback validation now derives minimum SDK projection counts from the source trace. It checks
  that recorded tool calls appear as `tool_progress`, raw model responses appear as
  `agent_model_response`, missing-info questions appear as `agent_question`, streamed events appear
  as `agent_stream_event`, and final output is projected when the source trace contains one.
- `developer_harness/verify.py` now dry-runs the publisher and asserts that the eval case receives
  the created evidence task id plus trace artifact evidence refs. It also runs an offline mocked
  publish to verify the new readback contract.

Verification:

- `.venv/bin/python -m py_compile backend_harness/verify_live_agent_smoke.py developer_harness/verify.py`
- `cd backend_harness && ../.venv/bin/python -B verify_live_agent_smoke.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Actual publisher verification with harness API:
  `.venv/bin/python backend_harness/publish_live_agent_smoke_export.py --client-id codex-sdk-harness-publisher-20260628 --idempotency-key fitcheck-live-smoke-export-run_3dc34e2d63f0-publisher-verification --trace-json harness_outputs/live_agent_smoke_export_20260628/run_3dc34e2d63f0-fitcheck-live-agent-trace.json --eval-case-json harness_outputs/live_agent_smoke_export_20260628/run_3dc34e2d63f0-fitcheck-live-agent-eval-case.json`
  produced `task_d8a0ac9fea59`, `eval_case_id=48`, 26 tool calls, 120 observations, and 1 artifact.
- Automatic smoke export/publish verification with live product route:
  `FITCHECK_RUN_LIVE_AGENT_SMOKE=1 FITCHECK_LIVE_AGENT_PUBLISH_HARNESS=1 ... verify_live_agent_smoke.py`
  produced run `run_770c3fcec4ad`, status `waiting_for_user`, `external_api_called=true`,
  25 model-selected/backend-executed tool outputs, exported trace/eval artifacts under
  `harness_outputs/live_agent_smoke_autopublish_20260628/`, and published harness evidence task
  `task_f404301a3748` with `eval_case_id=49`, 25 tool calls, 126 observations, and 1 artifact.
- Publisher readback verification against the real harness API:
  `.venv/bin/python backend_harness/publish_live_agent_smoke_export.py --client-id codex-publish-readback-20260628 --idempotency-key fitcheck-live-smoke-readback-run_770c3fcec4ad --trace-json harness_outputs/live_agent_smoke_autopublish_20260628/run_770c3fcec4ad-fitcheck-live-agent-trace.json --eval-case-json harness_outputs/live_agent_smoke_autopublish_20260628/run_770c3fcec4ad-fitcheck-live-agent-eval-case.json`
  produced `task_73e81710c3bf`, `eval_case_id=50`, read back 25 tool calls, 126 observations,
  1 artifact, 129 SDK-projected events, 25 transcript tool-progress items, 11 transcript model
  response records, and `readback.validation.valid=true`.
- Projection-count readback verification against the real harness API:
  `.venv/bin/python backend_harness/publish_live_agent_smoke_export.py --client-id codex-projection-readback-20260628 --idempotency-key fitcheck-live-smoke-projection-readback-run_770c3fcec4ad --trace-json harness_outputs/live_agent_smoke_autopublish_20260628/run_770c3fcec4ad-fitcheck-live-agent-trace.json --eval-case-json harness_outputs/live_agent_smoke_autopublish_20260628/run_770c3fcec4ad-fitcheck-live-agent-eval-case.json`
  produced `task_b479ce98e8d8`, `eval_case_id=51`, read back the expected minimum projection
  counts `tool_progress=25`, `agent_model_response=11`, `agent_question>=1`,
  `agent_stream_event=86`, and returned all SDK/transcript projection checks as true.

## 2026-06-28 More persistent close-fit search loop

Problem found while reviewing product behavior:

- The close-fit loop already used backend-owned recovery, but the default budget could still feel
  shallow for clothing search because a first page or small candidate set often does not contain a
  size-chart row close to the user's baseline item.
- Similar-fit requests should not stop at a merely acceptable or size-label-only result.
- External apparel UX/product-data references point in the same direction: apparel shoppers need
  concrete sizing information, and AI/product discovery should rely on structured product facts
  rather than inferred claims.

Changes:

- Default close-fit recovery increased from 12 to 24 measurement-focused search attempts before a
  no-close-match report is allowed.
- Default candidate enrichment increased from 5 to 8 candidates before `score_candidate_match(all)`
  treats the remaining candidates as deferred.
- Runtime instructions and the similar-product skill now explicitly reject stopping at the first
  page, tiny shortlist, or size-label-only match.
- Max-turn fallback now treats report-tool close-fit blockers as recoverable guidance before any
  weak fallback report. If a single weak candidate report is blocked by
  `candidate_not_close_enough_for_similar_fit_report`, the backend executes the suggested search,
  rescoring, and all-candidate report path before allowing a terminal report.
- The no-close report verifier now constructs a 24-search exhausted state and asserts the default
  close-fit budget is 24.
- Added a regression verifier where the LLM only scores a weak single candidate and then hits the
  runtime turn limit. The runtime must run report-blocker search recovery, rescore `all`, and
  complete from the recovered close shortlist without setting
  `force_no_close_report_after_search_exhausted`.
- Follow-up hardening removed the remaining max-turn fallback writes of
  `force_no_close_report_after_search_exhausted`. When `write_purchase_report(all)` or a recovered
  `score_candidate_match(all)` still reports `continue_search_for_closer_measurements*`, the runtime
  now converts that result into backend recovery guidance and re-enters the search loop instead of
  adopting a weak no-close report.
- Added a verifier for the non-exhausted state: with a weak measured candidate and remaining
  close-fit search budget, `write_purchase_report(all)` must return failed guidance with
  `search_web_candidates` suggestions and no `final_report`. The existing exhausted-state verifier
  still permits a `no_close_measurement_match` report after the 24-attempt budget is used.

Verification:

- `../.venv/bin/python -m py_compile app/agent_runtime.py verify_backend_infrastructure.py`
- `../.venv/bin/python -B verify_backend_infrastructure.py`
- `../.venv/bin/python -B verify_production_agent_runtime.py`
- `../.venv/bin/python -B verify_input_variation_matrix.py`
- `.venv/bin/python -m py_compile backend_harness/app/agent_runtime.py backend_harness/app/tool_registry.py backend_harness/verify_backend_infrastructure.py`
- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`

## 2026-06-29 Live stream projection compact counts

Problem found while using compact controller output for the live stream smoke:

- Full matrix summaries already contained `scenario_sdk_events_projection_counts`,
  `scenario_client_transcript_projection_counts`, SDK/client projection totals, and
  `projection_total_deltas`.
- Compact `matrix_run_summary` did not expose those maps, so controllers had to reopen the full
  matrix JSON to confirm stream-event, model-response, tool-progress, phase, and final-output
  projection coverage.

Changes:

- `agents_sdk_smoke_matrix.py` compact live/dry-run summary now exposes
  `scenario_sdk_events_projection_counts`, `scenario_client_transcript_projection_counts`,
  `sdk_events_projection_totals`, `client_transcript_projection_totals`,
  `projection_total_deltas`, and `projection_total_delta_allowed_keys`.
- `developer_harness/verify.py` covers empty dry-run compact values and synthetic live compact
  values for those fields.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Existing stored-evidence summary-file revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-stream-projection-revalidate-20260629.json`
  for aggregate task `task_a73218f476b8` and scenario task `task_0f622f5e7523`, with aggregate
  `check_count=61`, scenario `check_count=61`, and no failed checks.
- `.env`-sourced live `stream` smoke with the new compact fields wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-stream-projection-compact-fields-20260629.json`.
  It probed OpenAI provider diagnostic id `80`, used `320` total API tokens, published scenario
  task `task_13800d6d24ea`, and published aggregate task `task_021d2a4e9ab4`.
- That live compact stdout exposed matching SDK-event and client-transcript projection totals:
  `agent_model_response_count=2`, `agent_stream_event_count=35`, `agent_phase_count=1`,
  `tool_progress_count=1`, and `final_output_count=1`, with `projection_total_deltas={}` and no
  failed readback checks.
- Summary-file revalidation for the new live compact output wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-stream-projection-compact-fields-revalidate-20260629.json`
  with aggregate `check_count=61`, scenario `check_count=61`, and no failed checks.

## 2026-06-29 Stored-evidence compact projection counts

Problem found while revalidating live stream smoke evidence:

- Live compact `matrix_run_summary` exposed projection count maps, but stored-evidence
  `readback_validation_summary` still focused on aggregate/scenario readback checks.
- A controller that revalidated an old matrix from compact stdout could prove readback validity, but
  still had to reopen the source matrix JSON to inspect API usage and stream/final-output projection
  coverage.

Changes:

- `validate_existing_matrix_readback()` now passes the source matrix `summary` into
  `existing_matrix_readback_compact_summary()`.
- Stored-evidence compact summaries now expose `scenario_api_usage_totals`, `api_usage_totals`,
  `scenario_sdk_events_projection_counts`, `scenario_client_transcript_projection_counts`,
  projection totals, `projection_total_deltas`, and projection delta status/key fields.
- `developer_harness/verify.py` checks those fields for stored-evidence validation and for
  summary-file revalidation.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced stored-evidence revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-validation-compact-projection-counts-20260629.json`
  from live matrix
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-stream-projection-compact-fields-20260629.json`.
  It validated aggregate task `task_021d2a4e9ab4` and scenario task `task_13800d6d24ea` with
  aggregate `check_count=61`, scenario `check_count=61`, and no failed checks.
- The validation compact stdout exposed `scenario_api_usage_totals.stream.total_tokens=320`,
  matching SDK-event/client-transcript projection totals
  `agent_model_response_count=2`, `agent_stream_event_count=35`, `agent_phase_count=1`,
  `tool_progress_count=1`, and `final_output_count=1`, with `projection_total_deltas={}`.
- Revalidating from that validation compact summary wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-validation-compact-projection-counts-rerun-20260629.json`
  with the same aggregate/scenario task bindings and no failed checks.

## 2026-06-29 Compact projection delta assessment

Problem found while checking approval scenario compact output:

- Full matrix summaries recorded `projection_total_delta_assessment`, including the reason an
  SDK-event/client-transcript count delta is allowed.
- Compact `matrix_run_summary` and `readback_validation_summary` exposed delta status and allowed
  keys, but not the detailed assessment item. For approval runs, a controller could see
  `tool_progress_count` was allowed but could not explain why without opening the full matrix JSON.

Changes:

- Live compact `matrix_run_summary` now includes `projection_total_delta_assessment`.
- Stored-evidence compact `readback_validation_summary` now includes the same assessment copied from
  the source matrix summary.
- `developer_harness/verify.py` now checks dry-run absence, synthetic live `no_deltas`, and
  stored-evidence allowed-delta assessment preservation.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live `approval` smoke wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-delta-assessment-compact-20260629.json`.
  It probed OpenAI provider diagnostic id `81`, used `391` total API tokens, published scenario
  task `task_72408f29a9f5`, and published aggregate task `task_59d870e667c2`.
- Live compact stdout exposed `projection_total_deltas.tool_progress_count={sdk_events: 2,
  client_transcript: 1, delta: 1}` and
  `projection_total_delta_assessment.items.tool_progress_count.status=allowed` with the reason that
  SDK-event projection is lifecycle-oriented while the client transcript is compact.
- Stored-evidence revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-delta-assessment-compact-revalidate-20260629.json`
  with aggregate `check_count=61`, scenario `check_count=61`, and no failed checks while preserving
  the same allowed-delta assessment.
- Revalidating from that validation compact summary wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-delta-assessment-compact-revalidate-rerun-20260629.json`
  with the same aggregate/scenario task bindings and no failed checks.

## 2026-06-29 Stored-evidence compact provider probe fields

Problem found while checking stored-evidence revalidation stdout:

- Full live matrix payloads preserved `provider_probe_valid`, `provider_probe_failed_checks`, and
  `provider_probe_diagnostic_id`.
- Stored-evidence compact `readback_validation_summary` did not copy those fields from the source
  matrix summary, so a controller using compact stdout had to reopen the full matrix JSON to tie a
  revalidation result back to the provider diagnostic.

Changes:

- Live compact `matrix_run_summary` now explicitly includes `provider_probe_failed_checks`.
- Stored-evidence compact `readback_validation_summary` now includes `provider_probe_valid`,
  `provider_probe_diagnostic_id`, and `provider_probe_failed_checks` copied from the source matrix
  summary.
- `developer_harness/verify.py` checks provider probe fields in stored-evidence validation,
  summary-file revalidation, dry-run compact output, and synthetic live compact output.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced stored-evidence revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-provider-compact-revalidate-20260629.json`
  from live approval matrix
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-delta-assessment-compact-20260629.json`.
  Compact stdout exposed `provider_probe_valid=true`, `provider_probe_diagnostic_id=81`, and
  `provider_probe_failed_checks=[]`, with aggregate `check_count=61`, scenario `check_count=61`,
  and no failed checks.
- Revalidating from that compact validation summary wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-provider-compact-revalidate-rerun-20260629.json`
  and preserved the same provider probe fields and task bindings.

## 2026-06-29 Compact replay event count evidence

Problem found while checking replay validation output:

- Full matrix summaries and scenario rollups already preserved raw event replay expected/observed
  event counts and tool-call status-update expected/observed counts.
- Compact `matrix_run_summary` and stored-evidence `readback_validation_summary` exposed stage
  check counts and failed-check maps, but not the event-count maps that explain why replay coverage
  passed.

Changes:

- Live compact `matrix_run_summary` now includes
  `scenario_event_replay_expected_event_counts`,
  `scenario_event_replay_observed_event_counts`, expected/observed event totals,
  `tool_call_status_update_expected_counts`, `tool_call_status_updated_event_counts`, and their
  aggregate totals.
- Stored-evidence compact `readback_validation_summary` mirrors the same fields from the source
  matrix summary.
- `developer_harness/verify.py` checks dry-run absence, synthetic live compact values,
  stored-evidence validation values, and summary-file revalidation preservation.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced stored-evidence revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-replay-count-compact-revalidate-20260629.json`
  from live approval matrix
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-delta-assessment-compact-20260629.json`.
  Compact stdout exposed matching expected/observed replay counts for `artifact.recorded=2`,
  `observation.recorded=9`, `tool_call.recorded=1`, and `tool_call.status_updated=1`, plus
  `tool_call_status_update_expected_total=1` and `tool_call_status_updated_event_total=1`.
- Revalidating from that compact validation summary wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-replay-count-compact-revalidate-rerun-20260629.json`
  with the same aggregate/scenario task bindings and no failed checks.

## 2026-06-29 Compact aggregate artifact provenance evidence

Problem found while checking aggregate matrix compact output:

- Full aggregate readback validation already recorded `matrix_artifact` with the stored artifact
  path, expected evidence-source path, stored SHA-256 digest, recomputed digests, and availability
  flags.
- Compact `matrix_run_summary` and stored-evidence `readback_validation_summary` only exposed
  aggregate readback check counts and failed-check lists. A controller could see the aggregate gate
  passed, but could not confirm which artifact path and digest were actually compared without
  reopening the full JSON payload.

Changes:

- Live compact `matrix_run_summary` now exposes `matrix_readback_artifact`,
  `matrix_readback_artifact_checks`, `matrix_payload_readback_artifact`, and
  `matrix_payload_readback_artifact_checks`.
- Stored-evidence compact `readback_validation_summary` now exposes `aggregate_artifact` and
  `aggregate_artifact_checks`.
- These compact fields include recorded/expected artifact paths, stored and recomputed SHA-256
  digests when the files are locally available, path availability flags, and the matrix artifact
  path/digest check results.
- `developer_harness/verify.py` now checks dry-run absence, synthetic live compact preservation,
  and stored-evidence aggregate artifact path/digest preservation.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced stored-evidence revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-artifact-compact-revalidate-20260629.json`
  from the live approval matrix
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-delta-assessment-compact-20260629.json`.
  Compact stdout exposed aggregate artifact path
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-delta-assessment-compact-20260629.evidence-source.json`,
  digest `sha256:4a41dc7095357b2063bd082f3c9d56c4ba71cbf677a6acbe596e607bc4e1db7d`,
  all aggregate artifact checks as `true`, aggregate `check_count=61`, scenario `check_count=61`,
  and no failed checks.
- `.env`-sourced live `approval` compact matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-artifact-compact-20260629.json`.
  It probed OpenAI provider diagnostic id `84`, used `370` total API tokens, published scenario
  task `task_4f98ccd27630`, published aggregate task `task_f1e19be1613f`, and compact stdout
  exposed matching live matrix readback and payload-readback artifact digest
  `sha256:a288262717e3221bca603714ec0b2f4122a37ab0b21949c9211d7cbb4e44b722`.

## 2026-06-29 Compact provider probe detail evidence

Problem found while checking live matrix compact output:

- Full matrix JSON preserved provider probe details such as `/models` endpoint, HTTP status,
  provider profile, configured API-key environment variable name, and Agents SDK fit flags.
- Compact `matrix_run_summary` and stored-evidence `readback_validation_summary` only exposed
  `provider_probe_valid`, `provider_probe_diagnostic_id`, and failed checks. A controller could
  see that a probe passed, but could not tell which endpoint/model/API style was probed without
  opening the full matrix JSON or diagnostic record.

Changes:

- `validate_provider_probe()` now preserves non-secret probe details: status, HTTP status,
  endpoint, models-preview count, provider/API style/base URL/model/API-key env variable name,
  tool/stateful support flags, and Agents SDK fit flags.
- Live compact summaries now expose `provider_probe_check_count`, `provider_probe_checks`,
  `provider_probe_status`, `provider_probe_http_status`, `provider_probe_endpoint`,
  `provider_probe_models_preview_count`, `provider_probe_profile`, and
  `provider_probe_agent_sdk_fit`.
- Stored-evidence compact summaries mirror the same provider detail fields from the source matrix
  summary or stored provider validation observation.
- Aggregate readback validation now includes `provider_validation_payload_matches`, comparing the
  stored `agents_sdk_smoke_matrix_provider_probe_validation` observation against the source matrix
  `provider_probe_validation` payload. This raises aggregate validation count from `61` to `62`
  for new matrices.
- `developer_harness/verify.py` checks successful, failed, dry-run, synthetic live compact, and
  stored-evidence compact provider detail preservation.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live `approval` compact matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-provider-detail-compact-20260629.json`.
  It probed OpenAI provider diagnostic id `85`, exposed `provider_probe_http_status=200`,
  `provider_probe_endpoint=https://api.openai.com/v1/models`, `provider_probe_profile.model=gpt-4.1-mini`,
  `provider_probe_profile.api_key_env=OPENAI_API_KEY`, and `provider_probe_check_count=5` in compact
  stdout. It used `370` total API tokens, published scenario task `task_cc58ab65ee9f`, aggregate
  task `task_5f596c938ef2`, and both aggregate readback gates reported `check_count=62`.
- Stored-evidence revalidation from that live matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-provider-detail-compact-revalidate-20260629.json`.
  Compact stdout preserved the same provider detail fields, aggregate `check_count=62`, scenario
  `check_count=61`, and no failed checks.

## 2026-06-29 Compact scenario artifact summaries

Problem found while checking per-scenario evidence-holder readback:

- Scenario publish/readback compact summaries exposed check counts, failed-check maps, and task
  bindings, but not the actual scenario trace artifact path and digest.
- Full task evidence contained `agent_trace` and approval/interruption artifacts, but a controller
  had to fetch full evidence to verify which trace artifact backed a passing scenario.
- Directly copying artifact metadata into compact output was too noisy because approval/interruption
  artifacts can carry raw item payload details.

Changes:

- `trace_ingest.fetch_readback()` now returns `artifact_summaries` beside evidence counts and read
  model summaries.
- `artifact_summaries` include artifact id, kind, path, digest, status, and a sanitized metadata
  subset such as trace id, provider, SDK, model, status, external API flag, scenario, surface, and
  item type. Raw approval/interruption item payloads are intentionally not copied.
- Live matrix compact `matrix_run_summary` now exposes `scenario_artifact_summaries`.
- Stored-evidence compact `readback_validation_summary` now exposes
  `published_scenario_artifact_summaries`, and the nested `published_scenario_readback` summary
  includes `artifact_summaries_by_scenario`.
- `developer_harness/verify.py` checks trace-ingest artifact summary sanitization, dry-run absence,
  synthetic live compact preservation, and stored-evidence compact preservation.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live `approval` compact matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-scenario-artifact-sanitized-20260629.json`.
  It probed OpenAI provider diagnostic id `87`, used `370` total API tokens, published scenario
  task `task_648a8da370cf`, published aggregate task `task_b8b1e5ea4cb1`, and compact stdout
  exposed `scenario_artifact_summaries.approval[0].kind=agent_trace` with digest
  `sha256:ed8b2b6c2fd933ae6592b252f65789bf3ccc93fa4d28c546bc014997f290db36`.
- Stored-evidence revalidation from that live matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-scenario-artifact-compact-revalidate-20260629.json`.
  Compact stdout preserved `published_scenario_artifact_summaries.approval`, aggregate
  `check_count=62`, scenario `check_count=61`, and no failed checks.
- Live smoke run `run_770c3fcec4ad` used the strict Korean request "대충 찾고 멈추지 말고"
  and did not finalize a weak shortlist. It searched and enriched candidates, retried baseline
  evidence recovery, rescored, then stopped at `waiting_for_user` with `baseline_evidence` because
  precise baseline measurements/material were still not grounded.

External references checked:

- Baymard, "Apparel: 10 Best Practices on Sizing" notes that apparel sizing information is central
  to purchase decisions and that many apparel sites do not provide enough sizing information.
  https://baymard.com/blog/apparel-size-information
- Google Search Central, "Product structured data" documents structured product facts such as
  price, availability, review ratings, and shipping information as page-grounded product data.
  https://developers.google.com/search/docs/appearance/structured-data/product
- Google Search Central, "Product Variant Structured Data" covers products that vary by size,
  color, material, or pattern, including single-page variant selection. This supports treating
  per-size measurements as variant-specific evidence, not one page-level measurement blob.
  https://developers.google.com/search/docs/appearance/structured-data/product-variants

## 2026-06-29 Summary-file revalidation self-handle

Problem found while exercising stored-evidence matrix revalidation:

- `--revalidate-from-summary-json` could rerun validation from a compact or full summary file, but
  if the caller did not also pass a new `--output-json`, the resulting compact stdout lost the
  summary-file revalidation handle.
- That meant a controller could run from a summary file once, but could not repeat the same
  stored-evidence validation from the returned compact stdout alone.

Changes:

- `existing_matrix_readback_compact_summary()` now falls back to
  `revalidation_source_summary_json` when building `summary_revalidation_inputs`.
- Summary-file revalidation compact output preserves
  `summary_revalidation_inputs.revalidate_from_summary_json`, sets
  `summary_revalidation_ready=true` when the underlying stored-evidence inputs are complete, and
  emits a repeatable non-secret `summary_revalidation_cli_args`.
- `developer_harness/verify.py` now checks the self-handle for summary-file revalidation.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live `tool` compact matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-summary-selfhandle-20260629.json`.
  It probed OpenAI provider diagnostic id `88`, used `321` total API tokens, published scenario
  task `task_40f2211bb881`, published aggregate task `task_5793d7171a84`, and both aggregate
  readback gates reported `check_count=62`.
- Running `--revalidate-from-summary-json` against that live matrix without a new `--output-json`
  returned compact stdout with `status=completed`, aggregate `check_count=62`, scenario
  `tool=57`, `revalidation_source_summary_key=matrix_run_summary`, and
  `summary_revalidation_inputs.revalidate_from_summary_json` still pointing at the live matrix JSON.
- File-backed summary revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-revalidate-20260629.json`
  with `status=completed`, aggregate `check_count=62`, scenario `tool=57`, and a next
  `summary_revalidation_cli_args` that points at the new revalidation summary file.

## 2026-06-29 Matrix failure eval-case duplicate reuse

Problem found while repeating the live failure-to-eval path:

- Once readback validation was added, rerunning the same failed stored-evidence matrix validation
  created another identical `/eval-cases` row every time.
- That made the feedback flywheel noisy: the same regression seed should stay discoverable, but
  repeated controller checks should not inflate the eval-case list.

Changes:

- `agents_sdk_smoke_matrix.py` now queries `GET /eval-cases` before posting a new failure eval
  case.
- If an existing row matches the same name, source task, failure label, verifier command,
  evidence refs, and expected status, the script returns `eval_case_recording.status=already_recorded`
  with `duplicate_prevented=true`.
- The duplicate path still runs the readback validator and exposes the matched eval case id,
  match strategy, check count, and failed checks.
- `developer_harness/verify.py` now asserts that the first failed matrix recording posts once,
  while the second identical recording only reads `/eval-cases` and reuses the existing eval case.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Before the live reuse check, `GET /api/harness/v1/eval-cases` for client
  `codex-summary-selfhandle-20260629` returned matching eval case ids `60`, `59`, and `58`.
- Rerunning the same missing-scenario matrix validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-missing-scenario-evalcase-reuse-20260629.json`
  and exited `1` as expected because the matrix still references `task_missing_eval_case_verify`.
- The compact output reported `eval_case_recording.status=already_recorded`,
  `duplicate_prevented=true`, `eval_case_id=60`, `readback_status=matched`,
  `readback_valid=true`, `readback_match_strategy=name_source_failure_label`, and zero readback
  failed checks.
- A follow-up `GET /api/harness/v1/eval-cases` still returned only ids `60`, `59`, and `58` for
  that failure, proving the repeated validation did not create another duplicate row.

## 2026-06-29 Matrix failure eval-case duplicate visibility

Problem found after adding duplicate reuse:

- Repeated validation no longer posted new eval-case rows, but compact output still exposed only the
  single matched eval case id.
- Because older duplicate rows already existed from earlier runs, controllers still needed a
  separate `/eval-cases` listing to know whether the failure signature was already duplicated.

Changes:

- `validate_eval_case_recording_readback()` now computes all eval cases matching the same failure
  signature: name, source task, failure label, verifier command, evidence refs, and expected status.
- `eval_case_recording` now exposes `readback_matching_eval_case_count` and
  `readback_matching_eval_case_ids` for both newly recorded and reused eval cases.
- `developer_harness/verify.py` now simulates an existing duplicate and asserts that reuse reports
  both matching ids while avoiding another POST.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Rerunning the same missing-scenario matrix validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-missing-scenario-evalcase-duplicates-20260629.json`
  and exited `1` as expected because the matrix still references `task_missing_eval_case_verify`.
- The compact output reported `eval_case_recording.status=already_recorded`,
  `duplicate_prevented=true`, `readback_matching_eval_case_count=3`, and
  `readback_matching_eval_case_ids=[60, 59, 58]`.
- A follow-up `GET /api/harness/v1/eval-cases` still returned only matching ids `60`, `59`, and
  `58`, proving the visibility check did not create another duplicate row.

## 2026-06-29 Matrix failure eval-case signature

Problem found after exposing duplicate ids:

- Compact output could show the matching eval case ids, but external controllers still had to
  reconstruct the grouping key from name, source task, failure label, verifier command, evidence
  refs, and expected status.
- New eval cases did not persist an explicit stable signature in metadata.

Changes:

- `agents_sdk_smoke_matrix.py` now computes a deterministic SHA-256 `failure_signature` from the
  stable failure seed fields.
- New eval cases store this under `metadata.failure_signature`, and compact
  `eval_case_recording` mirrors the same value.
- Readback duplicate matching now uses the computed signature, while still supporting older eval
  cases that do not have the metadata field by computing their signature from stored fields.
- `developer_harness/verify.py` now asserts that newly recorded and reused eval-case paths expose
  the same `sha256:` signature.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Rerunning the same missing-scenario matrix validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-missing-scenario-evalcase-signature-20260629.json`
  and exited `1` as expected because the matrix still references `task_missing_eval_case_verify`.
- The compact output reported
  `eval_case_recording.failure_signature=sha256:a28991da0dc691c4e1decc9ee402d9605b879f7783c3e63a8cb593905773a02f`,
  `eval_case_recording.status=already_recorded`, `duplicate_prevented=true`,
  `readback_matching_eval_case_count=3`, and `readback_matching_eval_case_ids=[60, 59, 58]`.
- A follow-up `GET /api/harness/v1/eval-cases` still returned only matching ids `60`, `59`, and
  `58`, proving the signature visibility check did not create another duplicate row.

## 2026-06-29 Matrix failure eval-case URL-stable signature

Problem found while reviewing the new failure signature:

- The first signature included the raw verifier command.
- That made the signature vulnerable to harmless transport/output differences such as
  `--base-url http://127.0.0.1:8000/...` versus `--base-url http://localhost:8000/...`.

Changes:

- Failure signature material now uses a canonical verifier command.
- The canonical command ignores `--base-url`, `--client-id`, and `--compact-output`.
- Readback validation still exposes exact verifier-command drift as
  `readback_non_blocking_failed_checks`, while `readback_failed_checks` remains empty when the
  canonical command and failure seed match.
- `eval_case_record_signature()` recomputes the current canonical signature from stored fields, so
  older rows with pre-canonical `metadata.failure_signature` still group under the current rule.
- `developer_harness/verify.py` now asserts that a verifier command with a different base URL has
  the same failure signature and is reused without posting a duplicate.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Rerunning the same missing-scenario matrix validation with
  `--base-url http://localhost:8000/api/harness/v1` wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-missing-scenario-evalcase-url-stable-20260629.json`
  and exited `1` as expected because the matrix still references `task_missing_eval_case_verify`.
- The compact output reported
  `eval_case_recording.failure_signature=sha256:2147dba6f28ed6b82e9ce548ebfa1cab4bb1019f16541bb67ceb230825b57059`,
  `eval_case_recording.status=already_recorded`, `duplicate_prevented=true`,
  `readback_matching_eval_case_ids=[60, 59, 58]`, `readback_failed_checks=[]`, and
  `readback_non_blocking_failed_checks=["eval_case_readback_verifier_command_exact_matches"]`.
- A follow-up `GET /api/harness/v1/eval-cases` still returned only matching ids `60`, `59`, and
  `58`, proving the URL-stable signature check did not create another duplicate row.

## 2026-06-29 Matrix failure eval-case signature metadata diagnostics

Problem found after making signatures URL-stable:

- The readback matcher intentionally recomputes the current canonical signature so older rows can
  still be grouped.
- Compact output did not say whether matching rows had current metadata, missing metadata, or stale
  pre-canonical metadata.

Changes:

- `validate_eval_case_recording_readback()` now reports
  `readback_signature_metadata_status`.
- It also reports `readback_matching_eval_case_stored_signature_missing_ids` and
  `readback_matching_eval_case_stored_signature_mismatch_ids` plus counts.
- `developer_harness/verify.py` now covers the current-metadata path and a synthetic stale
  metadata path.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Rerunning the same missing-scenario matrix validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-missing-scenario-evalcase-signature-metadata-20260629.json`
  and exited `1` as expected because the matrix still references `task_missing_eval_case_verify`.
- The compact output reported `eval_case_recording.status=already_recorded`,
  `readback_signature_metadata_status=missing`,
  `readback_matching_eval_case_stored_signature_missing_count=3`,
  `readback_matching_eval_case_stored_signature_missing_ids=[60, 59, 58]`, and
  `readback_matching_eval_case_stored_signature_mismatch_count=0`.
- A follow-up `GET /api/harness/v1/eval-cases` still returned only matching ids `60`, `59`, and
  `58`, proving the metadata diagnostic did not create another duplicate row.

## 2026-06-29 Matrix failure eval-case signature material versioning

Problem found after adding signature metadata diagnostics:

- Compact output exposed the computed signature, but not the exact canonical material or version
  used to compute it.
- That would make future signature-rule changes harder to audit from stored evidence alone.

Changes:

- Added `EVAL_CASE_FAILURE_SIGNATURE_VERSION=agents_sdk_matrix_failure_v2`.
- New eval-case metadata now stores `failure_signature_version` and
  `failure_signature_material` next to `failure_signature`.
- Compact `eval_case_recording` mirrors `failure_signature_version` and
  `failure_signature_material`.
- `developer_harness/verify.py` now asserts that the metadata and compact material match, exclude
  ignored transport flags, and stay stable across base URL changes.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Rerunning the same missing-scenario matrix validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-missing-scenario-evalcase-signature-material-20260629.json`
  and exited `1` as expected because the matrix still references `task_missing_eval_case_verify`.
- The compact output reported
  `eval_case_recording.failure_signature=sha256:f08403fec86aec116ccb9fe479d87d75a87e5e053e605a1b96babcca352aba41`,
  `failure_signature_version=agents_sdk_matrix_failure_v2`, and a
  `failure_signature_material.verifier_command` list that excludes `--base-url`, `--client-id`,
  and `--compact-output`.
- The same output still reused matching ids `[60, 59, 58]` with
  `readback_signature_metadata_status=missing`, and a follow-up `GET /api/harness/v1/eval-cases`
  still returned only those three matching ids.

## 2026-06-29 Matrix failure eval-case signature material self-check

Problem found after adding signature material:

- Compact output exposed `failure_signature_material`, but did not explicitly prove that hashing
  that material reproduced `failure_signature`.
- Controllers could inspect the fields manually, but the check was not part of the readback
  contract.

Changes:

- `agents_sdk_smoke_matrix.py` now exposes `failure_signature_material_digest`.
- Readback checks now include `eval_case_readback_signature_matches_material` and
  `eval_case_readback_signature_version_current`.
- New eval-case metadata stores `failure_signature_material_digest` alongside material and
  signature.
- `developer_harness/verify.py` now recomputes the material digest and asserts both readback
  checks are true for recorded and reused eval-case paths.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Rerunning the same missing-scenario matrix validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-missing-scenario-evalcase-signature-selfcheck-20260629.json`
  and exited `1` as expected because the matrix still references `task_missing_eval_case_verify`.
- The compact output reported
  `failure_signature_material_digest=sha256:f08403fec86aec116ccb9fe479d87d75a87e5e053e605a1b96babcca352aba41`,
  `readback_checks.eval_case_readback_signature_matches_material=true`,
  `readback_checks.eval_case_readback_signature_version_current=true`, and
  `readback_check_count=13`.
- The same output still reused matching ids `[60, 59, 58]`, and a follow-up
  `GET /api/harness/v1/eval-cases` still returned only those three matching ids.

## 2026-06-29 Matrix failure eval-case dedupe lookup diagnostics

Problem found while reviewing the failure-to-eval path:

- The matrix script checks `/eval-cases` before POSTing a new failure eval case.
- If that preflight lookup returned no match or failed, the final `recorded` compact result did
  not preserve enough information to explain whether duplicate prevention had actually run.

Changes:

- `agents_sdk_smoke_matrix.py` now carries `dedupe_lookup_status`,
  `dedupe_lookup_readback_status`, `dedupe_lookup_valid`,
  `dedupe_lookup_matching_eval_case_count`, and `dedupe_lookup_matching_eval_case_ids` into
  recorded eval-case compact output.
- If preflight lookup fails, the recorded result also exposes `dedupe_lookup_error_type`,
  `dedupe_lookup_error`, and `dedupe_lookup_claim_boundary`.
- `developer_harness/verify.py` now covers `not_found -> POST`, `already_recorded -> no POST`, and
  `lookup_failed -> POST with explicit warning` paths.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Rerunning the same missing-scenario matrix validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-missing-scenario-evalcase-dedupe-lookup-20260629.json`
  and exited `1` as expected because the matrix still references `task_missing_eval_case_verify`.
- The compact output reported `eval_case_recording.status=already_recorded`,
  `dedupe_lookup_status=matched`, `duplicate_prevented=true`, `readback_valid=true`, and
  `readback_matching_eval_case_ids=[60, 59, 58]`.
- A follow-up client-scoped `GET /api/harness/v1/eval-cases` still returned only matching ids
  `[60, 59, 58]`, so this rerun did not POST another duplicate eval-case row.
- The synthetic verifier covers the `lookup_failed -> POST with explicit warning` branch without
  forcing the live API to fail.

## 2026-06-29 Matrix failure eval-case POST action diagnostics

Problem found after adding dedupe lookup diagnostics:

- `already_recorded` output implied that a duplicate POST was prevented, but did not expose a
  direct `post_attempted=false` field.
- `dedupe_lookup_*` fields were richer on newly recorded output than on the duplicate-reuse path,
  so controllers still had to inspect `readback_*` fields to explain the preflight lookup result.

Changes:

- `agents_sdk_smoke_matrix.py` now exposes `post_attempted`, `post_skipped_reason`, and
  `post_required_reason` on eval-case recording output.
- Duplicate reuse now mirrors `dedupe_lookup_readback_status`, `dedupe_lookup_valid`,
  `dedupe_lookup_matching_eval_case_count`, and `dedupe_lookup_matching_eval_case_ids` from the
  preflight readback validation.
- `developer_harness/verify.py` now asserts POST action diagnostics for `not_found -> POST`,
  `already_recorded -> no POST`, and `lookup_failed -> POST with explicit warning` paths.
- The agentic harness checklist and loop-engineering document now include the `post_attempted`
  output contract.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Rerunning the same missing-scenario matrix validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-missing-scenario-evalcase-post-action-20260629.json`
  and exited `1` as expected because the matrix still references `task_missing_eval_case_verify`.
- The compact output reported `eval_case_recording.status=already_recorded`,
  `post_attempted=false`, `post_skipped_reason=matching_eval_case_found`,
  `dedupe_lookup_readback_status=matched`, `dedupe_lookup_valid=true`,
  `dedupe_lookup_matching_eval_case_count=3`, and matching ids `[60, 59, 58]`.
- A follow-up client-scoped `GET /api/harness/v1/eval-cases` still returned only matching ids
  `[60, 59, 58]`, so this rerun did not POST another duplicate eval-case row.

## 2026-06-29 Live matrix compact scenario API usage

Problem found while running a real SDK `stream_handoff` matrix:

- The live compact stdout exposed aggregate `api_usage_totals`, but omitted
  `scenario_api_usage_totals`.
- The full matrix JSON already contained the per-scenario usage map, and stored-evidence
  revalidation compact output already mirrored it. The live compact controller surface was the
  inconsistent one.
- Without that field, a controller could see total OpenAI token/request usage but not which
  scenario produced the usage without reopening the full matrix JSON.

Changes:

- `agents_sdk_smoke_matrix.py` now mirrors `summary.scenario_api_usage_totals` into
  `matrix_run_compact_summary`.
- `developer_harness/verify.py` now asserts that the live compact summary preserves the
  per-scenario usage map alongside aggregate usage totals.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- A real provider-backed `stream_handoff` matrix run wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-stream-handoff-postdiag-20260629.json`
  and passed with provider diagnostic id `89`, two OpenAI requests, 30 projected stream events,
  two handoff projections, and valid aggregate matrix readback.
- A second real provider-backed `tool` matrix run wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-tool-scenario-usage-compact-20260629.json`
  and compact stdout included
  `scenario_api_usage_totals.tool={input_tokens:286, output_tokens:35, requests:2, total_tokens:321}`
  alongside matching aggregate `api_usage_totals`.
- Summary-file revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-tool-scenario-usage-compact-revalidate-20260629.json`
  with `aggregate_check_count=62`, published scenario `tool` total check count `57`, no failed
  checks, and the same `scenario_api_usage_totals.tool` map.

## 2026-06-29 Live matrix compact scenario failure maps

Problem found while running a real SDK `approval` + `guardrail_tripwire` matrix:

- The live compact stdout exposed aggregate pass/fail counts, projection counts, and readback
  failed-check maps.
- The full matrix JSON also had scenario validation failed-check maps and stage status counts, but
  `matrix_run_compact_summary` did not mirror those top-level fields.
- On a future partial failure, a controller would need to reopen the full matrix JSON to tell
  whether the failing stage was scenario validation, generic readback, read-model projection, or
  raw event replay.

Changes:

- `agents_sdk_smoke_matrix.py` now mirrors scenario status counts, validation/publish/readback
  status counts, scenario validation failed-check maps, and failed-scenario lists into live compact
  output.
- `developer_harness/verify.py` now covers both the all-passing compact path and a synthetic
  failing scenario so the compact output preserves the failed stage maps.
- The agentic harness checklist and loop-engineering document now require compact stdout to expose
  enough failed-stage information for controller decisions without reopening the full matrix JSON.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- A real provider-backed `approval` + `guardrail_tripwire` matrix run wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-approval-guardrail-failuremaps-20260629.json`
  and passed with provider diagnostic id `92`, three OpenAI requests, two approval projections,
  one guardrail projection, one run-error projection, and valid matrix/payload readback.
- Compact stdout included the new controller fields:
  `scenario_status_counts={completed:1, guardrail_tripwire_triggered:1}`,
  `validation_status_counts={completed:2}`, `publish_status_counts={completed:2}`,
  `generic_readback_status_counts={valid:2}`, `projection_validation_status_counts={valid:2}`,
  `scenario_event_replay_status_counts={valid:2}`, empty failed-check maps, and empty failed
  scenario lists.
- Summary-file revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-approval-guardrail-failuremaps-revalidate-20260629.json`
  with `aggregate_check_count=62`, published scenario total check count `120`, no failed checks,
  and the same approval/guardrail projection, usage, and replay coverage maps.

## 2026-06-29 Matrix failure eval-case readback validation

Problem found while running the live failure-to-eval path:

- The matrix script could POST a failed stored-evidence validation into `/eval-cases`, but the
  script did not verify that the new eval case was visible through the client-scoped read model.
- During the first live check, the new readback call exposed a script bug: `harness_api.request()`
  requires a `body` argument even for GET calls, so the eval-case POST succeeded but the script
  reported `readback_status=failed` with `TypeError`.

Changes:

- `agents_sdk_smoke_matrix.py` now calls `GET /eval-cases` immediately after recording a failure
  eval case.
- `eval_case_recording` now includes `readback_status`, `readback_valid`,
  `readback_failed_checks`, `readback_checks`, `readback_eval_case_count`,
  `readback_matched_eval_case_id`, and `readback_match_strategy`.
- Readback validation matches by `eval_case_id` first, then falls back to name, source task, and
  failure label. It checks name, failure label, source task, verifier command, evidence refs, and
  expected status.
- `developer_harness/verify.py` now fakes both POST and GET `/eval-cases`, so the synthetic
  verifier fails if eval-case recording is not visible through readback.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Live harness API check against
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-summary-selfhandle-missing-scenario-20260629.json`
  wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-missing-scenario-evalcase-readback-fixed-20260629.json`.
- The live command exited `1` as expected because the matrix intentionally referenced
  `task_missing_eval_case_verify`, while `eval_case_recording` reported `status=recorded`,
  `eval_case_id=60`, `readback_status=matched`, `readback_valid=true`,
  `readback_matched_eval_case_id=60`, `readback_match_strategy=eval_case_id`, and zero
  readback failed checks.

## 2026-06-29 Matrix failure eval-case recording

Problem found while reviewing the loop-engineering feedback path:

- The matrix scripts could detect and summarize stored-evidence revalidation failures, but the
  controller still had to manually convert those failures into `/eval-cases`.
- That weakened the feedback flywheel described in `agentic-harness-loop`: repeatable trace or
  replay gaps should become eval seeds instead of remaining only as failed stdout.

Changes:

- `agents_sdk_smoke_matrix.py` now accepts `--record-eval-case-on-failure` for stored-evidence
  matrix validation and summary-file revalidation modes.
- On failed validation, it records a client-scoped eval case with source task id, failure label,
  failed-check input, expected completed status, evidence refs, and a non-secret verifier command
  that reruns the same stored-evidence matrix validation without new live SDK/provider calls.
- Stored-evidence compact summaries now expose `eval_case_recording` with eval case id, failure
  label, source task id, verifier command, and evidence refs.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Using the live matrix
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-summary-selfhandle-20260629.json`,
  a negative matrix copy was written to
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-summary-selfhandle-missing-scenario-20260629.json`
  by replacing the published scenario task id with `task_missing_eval_case_verify`.
- Stored-evidence validation with `--record-eval-case-on-failure` wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-missing-scenario-evalcase-20260629.json`,
  exited `1` as expected, and compact stdout exposed `eval_case_recording.status=recorded`,
  `eval_case_id=58`, `source_task_id=task_5793d7171a84`, and
  `failure_label=aggregate:matrix_summary_payload_matches`.
- `GET /api/harness/v1/eval-cases` for client `codex-summary-selfhandle-20260629` returned eval
  case `58` with verifier command
  `python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --validate-matrix-readback-task-id task_5793d7171a84 ...`.
- Positive stored-evidence validation with the same flag wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-selfhandle-success-evalcase-noop-20260629.json`,
  returned `status=completed`, aggregate `check_count=62`, scenario `tool=57`, and
  `eval_case_recording.status=not_recorded`.

## 2026-06-28 Size-specific measurement rows and closer-fit loop budget

Problem found while reviewing FitCheck scoring:

- Product pages often expose a size chart where measurements differ by size. The previous parser
  collapsed page measurements into one dict, so a candidate could be compared using the first or
  arbitrary row instead of the user's intended size row.
- Score output exposed `size`, `material`, `price`, and `confidence` numbers without enough
  structured basis for clients to inspect why those numbers were assigned.
- The product loop could feel too shallow because the default candidate enrichment limit was low
  and close-fit recovery stopped after fewer measurement-focused attempts.

Changes:

- `product_tools.py` now parses `size_measurement_rows` from pipe and whitespace size charts.
- `_parse_measurements(..., target_size_label=...)` selects the matching size row and refuses to
  fall back to another row when a requested size is present but missing.
- `normalize_product_spec` and live product normalization now preserve `measurement_selection`
  metadata with requested size, selected row, available rows, row count, and policy text.
- `score_candidate_match` now exposes `measurement_selection` and `score_basis`, including
  per-field measurement thresholds and penalties.
- Evidence coverage now records all observed measurement fields rather than only upper-body fields.
- Earlier in this iteration, default candidate enrichment increased from 2 to 5 candidates and
  close-fit recovery attempts increased from 5 to 12 before the backend wrote a no-close-match
  report. This was later superseded by the more persistent 8-candidate / 24-attempt defaults above.
  The limit is still configurable with `FITCHECK_CLOSE_FIT_SEARCH_ATTEMPTS`.
- Close-fit score/report outputs now expose `close_fit_search_budget` with attempt count, limit,
  remaining attempts, attempted queries, and the policy that search should continue until a close
  measured match is found or the configured budget is exhausted.

Verification:

- `.venv/bin/python -m py_compile backend_harness/app/tools/product_tools.py backend_harness/app/tool_registry.py backend_harness/app/tools/match_tools.py backend_harness/verify_backend_infrastructure.py`
- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`

Follow-up hardening:

- Close-fit recovery guidance is now eligible for backend-owned recovery when the model ignores it
  and tries to stop with final text. Reasons such as
  `continue_search_for_closer_measurements`,
  `continue_search_for_closer_measurements_before_report`, and
  `candidate_not_close_enough_for_similar_fit_report` can trigger backend recovery
  `search_web_candidates` before a final report is accepted.
- `verify_production_agent_runtime.py` includes a regression where the fake LLM attempts to stop
  after a no-close-fit score; the runtime must execute backend-owned search recovery, preserve the
  deferred score observation, rescore, and only then accept a grounded report.
- `verify_backend_infrastructure.py` checked the then-default 12-attempt close-fit budget and
  confirmed a no-close-match report is produced only after that budget is exhausted. The active
  verifier now checks the 24-attempt default described above.
- `score_candidate_match(candidate_key="all")` now directly verifies the close-fit recovery path
  with actual tool-registry state: multiple normalized candidates with completed fetch/classify/OCR/
  normalize observations but no close measured match must return
  `score_deferred=true`, reason `continue_search_for_closer_measurements`, another
  `search_web_candidates` suggestion, and positive `close_fit_search_budget.remaining_attempts`.
  This catches regressions where a rough shortlist could be accepted or the recovery branch could
  fail before issuing the next search.

Verification:

- `.venv/bin/python -m py_compile backend_harness/app/tool_registry.py backend_harness/verify_backend_infrastructure.py`
- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`
- `.venv/bin/python -m py_compile backend_harness/app/agent_runtime.py backend_harness/verify_production_agent_runtime.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`

## 2026-06-28 Material claim grounding excludes search arguments

Problem found while auditing clothing-domain hallucination risks:

- A shortlist report is validated with `candidate_key=None` so it can cite evidence across multiple
  candidates.
- In that path, material keywords in `search_web_candidates.arguments.query` such as `cotton tee`
  could be accepted as evidence for a candidate material claim.
- A search query is a retrieval instruction, not an observed product fact. For clothing, this is
  risky because material composition affects fit, feel, care, durability, and return risk.

Changes:

- `report_grounding.py` now applies material-claim eligibility checks similar to fit/style claims.
- Tool arguments, planning context, query hints, and identity fields such as product names/URLs no
  longer ground material composition claims.
- Material-specific observations and `normalized_spec.material` still ground claims such as cotton
  or polyester.
- `verify_production_agent_runtime.py` now includes a regression that fails when a report claims
  `면 소재` from only a `cotton tee` query, and passes once `normalized_spec.material.cotton` is
  present.

Verification:

- `.venv/bin/python -m py_compile backend_harness/app/report_grounding.py backend_harness/verify_production_agent_runtime.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`

## 1. 이번 조사에서 확인한 기준

OpenAI function calling 문서는 tool calling을 모델과 애플리케이션 사이의 다단계 대화로 설명한다. 모델은 tool call을 선택하고, 애플리케이션은 실제 함수를 실행한 뒤 tool output을 다시 모델에 제공한다.

- Source: https://platform.openai.com/docs/guides/function-calling?api-mode=responses
- FitCheck 적용: production agent route는 LLM final text만 보는 것이 아니라, `search -> fetch -> classify -> OCR/extract -> normalize -> score -> report` tool observation sequence를 검증해야 한다.

Jina Reader API는 `https://s.jina.ai`를 web search와 LLM-friendly text 변환 endpoint로 제공한다.

- Source: https://jina.ai/en-US/reader/
- FitCheck 적용: `FITCHECK_SEARCH_PROVIDER=jina_search`는 live search adapter로 유지하되, API key가 없으면 `configuration_required`로 남긴다.

Brave Search API 문서는 web search endpoint가 `https://api.search.brave.com/res/v1/web/search`이고, `X-Subscription-Token` header와 `q`, `count`, `country`, `search_lang` 같은 query parameter를 사용한다고 설명한다.

- Source: https://api-dashboard.search.brave.com/api-reference/web/search/get
- Source: https://api-dashboard.search.brave.com/documentation/guides/authentication
- FitCheck 적용: `FITCHECK_SEARCH_PROVIDER=brave_search`는 key 기반 live search adapter로 추가한다. `BRAVE_SEARCH_API_KEY` 또는 `FITCHECK_SEARCH_API_KEY`가 없으면 `configuration_required`로 남긴다.

SearXNG Search API 문서는 `/` 또는 `/search` endpoint에 `q`와 `format=json` parameter를 사용해 JSON search result를 받을 수 있지만, instance 설정에서 JSON format이 비활성화되어 있으면 403이 날 수 있다고 설명한다.

- Source: https://docs.searxng.org/dev/search_api.html
- FitCheck 적용: `FITCHECK_SEARCH_PROVIDER=searxng`는 self-hosted metasearch adapter로 추가한다. `FITCHECK_SEARXNG_URL` 또는 `SEARXNG_URL`이 없으면 `configuration_required`로 남기고, JSON output이 비활성화된 instance는 request failure로 드러낸다.

Tesseract는 command-line OCR engine이다. 실제 OCR provider로 사용하려면 로컬 실행 파일과 언어 데이터가 있어야 한다.

- Source: https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html
- FitCheck 적용: `FITCHECK_OCR_PROVIDER=tesseract_cli`는 local dependency가 없으면 live OCR로 주장하지 않고 `configuration_required` evidence를 남긴다.

Python `subprocess.run` 문서는 `timeout`을 넘긴 process가 제한 시간 내 종료되지 않으면 `TimeoutExpired`를 raise한다고 설명한다.

- Source: https://docs.python.org/3/library/subprocess.html
- FitCheck 적용: Tesseract CLI OCR 실행 timeout은 `OCR_TESSERACT_TIMEOUT` / `timeout` provider error로 정규화한다.

Python `shutil.which` 문서는 실행 파일을 찾기 위해 `PATH`를 검색한다고 설명한다.

- Source: https://docs.python.org/3/library/shutil.html#shutil.which
- FitCheck 적용: Tesseract executable 미발견은 live OCR 성공으로 위장하지 않고 `OCR_TESSERACT_CLI_NOT_FOUND` / `configuration` provider error로 정규화한다.

FastAPI는 route decorator의 `response_model`을 사용해 반환 dict를 Pydantic model로 문서화, 검증, 필터링한다.

- Source: https://fastapi.tiangolo.com/tutorial/response-model/
- FitCheck 적용: `/api/status`, `/api/agent/runs`, `/api/uploads/images`, `/api/results/web-candidates`에 response model을 연결하고 OpenAPI schema 노출을 검증한다.

FastAPI testing 문서는 `TestClient`가 HTTPX 기반이며, 사용하려면 HTTPX dependency가 필요하다고 설명한다.

- Source: https://fastapi.tiangolo.com/tutorial/testing/
- FitCheck 적용: 현재 venv의 `TestClient` dependency가 충족되지 않으므로 live agent route smoke는 in-process TestClient가 아니라 실행 중인 API server를 호출하는 opt-in HTTP verifier로 둔다.

FastAPI header/cookie parameter 문서는 header와 cookie를 request parameter로 선언할 수 있다고 설명한다.

- Source: https://fastapi.tiangolo.com/tutorial/header-params/
- Source: https://fastapi.tiangolo.com/tutorial/cookie-params/
- FitCheck 적용: 익명 세션은 `X-FitCheck-Session-Id` header 또는 `fitcheck_session_id` cookie를 임시 경계로 사용하므로, cookie-only 요청이 같은 session으로 이어지는지와 seeded run이 다른 session header에서 detail/list/report로 노출되지 않는지 HTTP smoke로 검증한다.

FastAPI request body 문서는 request body를 Pydantic model로 선언하고, 클라이언트가 보낸 body를 검증하며, model의 JSON Schema를 generated OpenAPI schema에 포함한다고 설명한다.

- Source: https://fastapi.tiangolo.com/tutorial/body/
- FitCheck 적용: upload/create/reply/web-candidate request body가 Pydantic request schema로 OpenAPI requestBody `$ref`에 연결되어 있는지 검증하고, 잘못된 타입이나 필수 필드 누락은 route handler나 LLM runtime에 들어가기 전에 HTTP 422 validation error로 막히는지 opt-in HTTP smoke로 검증한다.

Pydantic v2는 `model_config`로 extra field 정책을 제어한다.

- Source: https://pydantic.dev/docs/validation/2.0/usage/model_config/
- FitCheck 적용: 현재 agent run response는 trace/state 확장이 많으므로 `extra="allow"` 기반의 얇은 response model로 기존 payload 호환성을 유지하면서 최소 계약을 세운다.

MDN Server-Sent Events 문서는 SSE가 단방향 연결이며, 서버가 `text/event-stream` MIME type으로 `event`, `data`, `id`, `retry` field를 담은 UTF-8 text block을 보낸다고 설명한다.

- Source: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events
- FitCheck 적용: `/api/agent/runs/{run_id}/events`는 `id`, `event: agent_state`, `retry`, JSON `data`를 유지하고, payload를 `AgentStateEventResponse`로 검증한다. 변경이 없는 running stream에는 keepalive comment를 보낼 수 있다.

FastAPI SSE 문서는 SSE가 `text/event-stream`을 사용하며, 각 event가 `data`, `event`, `id`, `retry` 같은 field를 갖는 작은 text block이라고 설명한다.

- Source: https://fastapi.tiangolo.com/tutorial/server-sent-events/
- FitCheck 적용: OpenAPI의 `/api/agent/runs/{run_id}/events` 200 response에 `text/event-stream` content와 `AgentStateEventResponse` schema를 문서화하고, stream response에 `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`를 명시한다.

OpenTelemetry trace 문서는 operation span에 attributes와 timestamped events를 붙여 trace를 구성한다고 설명한다.

- Source: https://opentelemetry.io/docs/specs/otel/trace/api/
- FitCheck 적용: OTel SDK를 바로 도입하지는 않지만, 현재 SQLite payload의 `trace` entry를 span/event처럼 읽을 수 있도록 tool timeout, risk, provider timeout, retry count, duration metadata를 정규화한다.

MDN HTTP status reference는 HTTP status code가 request 결과를 나타내며, `429`는 rate limit, `500`-`599`는 server error class로 분류된다고 설명한다.

- Source: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status
- FitCheck 적용: provider retry/failure string의 `http_status:429`, `http_status:500`, `http_status:502`, `http_status:503`, `http_status:504`를 안정적인 `provider_error_code`와 `provider_error_category`로 정규화한다.

Requests 문서는 network problem은 `ConnectionError`, unsuccessful status는 `HTTPError`, timeout은 `Timeout`으로 드러난다고 설명한다.

- Source: https://requests.readthedocs.io/en/master/user/quickstart/#errors-and-exceptions
- FitCheck 적용: live search/page fetch boundary의 raw exception message를 `REQUEST_TIMEOUT`, `REQUEST_CONNECTION_ERROR`, `REQUEST_CHUNKED_ENCODING_ERROR` 같은 provider error code로 정규화한다.

OpenTelemetry HTTP semantic conventions는 instrumentation이 감지한 error에 span status와 `error.type` attribute를 설정하도록 설명한다.

- Source: https://opentelemetry.io/docs/specs/semconv/http/http-spans/
- FitCheck 적용: FitCheck는 아직 OTel SDK를 직접 붙이지 않지만, trace/observation payload에 `provider_error_code`와 `provider_error_category`를 안정 속성처럼 남겨 UI/SSE와 verifier가 raw message parsing에 의존하지 않게 한다.

OpenAI grader 문서는 string check, text similarity, score model 같은 grader를 사용해 output을 평가하는 방식을 설명한다.

- Source: https://platform.openai.com/docs/guides/graders/
- FitCheck 적용: report factuality의 1차 방어선은 LLM judge가 아니라 deterministic validator로 둔다. 숫자, URL, 소재 claim을 stored observation과 직접 대조하고, 어떤 observation field가 근거인지 `grounding_evidence`로 반환한다.

Jina Reader API는 `s.jina.ai`가 web search 결과를 LLM-friendly text로 변환하는 endpoint라고 설명한다. Search result에는 상품 페이지뿐 아니라 블로그/기사/커뮤니티 결과도 섞일 수 있으므로 후보 품질 메타데이터와 후보 통과 정책이 필요하다.

- Source: https://jina.ai/en-US/reader/
- FitCheck 적용: Jina/DuckDuckGo live search candidate에 `commerce_score`, `commerce_signals`, `content_risk_flags`, `candidate_type`, `search_result_rank`, `search_priority`를 추가한다. 상품 URL 가능성이 높은 `product_like` 결과만 `candidates`에 넣고, 불확실하거나 콘텐츠성인 결과는 `rejected_candidates` 진단으로만 남긴다.

MDN responsive image 문서는 `srcset`이 브라우저가 선택할 수 있는 여러 이미지 후보를 제공한다고 설명한다.

- Source: https://developer.mozilla.org/en-US/docs/Web/HTML/Guides/Responsive_images
- FitCheck 적용: live page fetch에서 `og:image`, `img src`, `data-src`, `srcset` 이미지 URL 후보를 수집한다. `srcset`은 OCR 후보 중복을 줄이기 위해 가장 큰 width 후보 1개만 대표로 사용한다.

## 2. 이번 개선

### Tool-first missing evidence policy

이전 흐름은 missing field가 나오면 고정 질문으로 빠질 수 있었다. 현재 정책은 다음과 같다.

1. 원문 사용자 입력과 현재 run state를 LLM에 전달한다.
2. LLM은 필요한 backend-owned tool을 선택한다.
3. 검색 전이면 `load_closet_memory`, `inspect_uploaded_images`, `search_web_candidates` 같은 evidence tool을 먼저 실행한다.
4. 검색 이후에는 `fetch_product_page`, `classify_product_assets`, `extract_size_material_info`, `normalize_product_spec`를 먼저 실행한다.
5. 충분한 observation 이후에만 `score_candidate_match`, `write_purchase_report`를 실행한다.

`parse_user_goal`은 optional normalization/helper tool이며 production control plane의 필수 관문이 아니다. `ask_missing_info`는 필드별 고정 질문을 만들지 않는다. 검색 전이면 raw input에서 바로 load/search suggestion을 반환하고, 검색 이후에는 fetch/classify/OCR/normalize suggestion을 반환한다. 이 tool enrichment 이후에도 근거가 없을 때만 마지막 수단으로 unresolved evidence 질문을 만든다.

### Raw-input direct tool selection verifier 추가

사용자가 지적한 parser-first 문제를 회귀 방지 대상으로 고정했다.

`verify_production_agent_runtime.py`는 raw Korean free text 입력을 LLM 요청에 그대로 전달하고, fake LLM이 첫 tool로 `search_web_candidates`를 선택하는 경로를 검증한다.

검증 항목:

- 첫 tool observation이 `search_web_candidates`
- observation 목록에 `parse_user_goal` 없음
- LLM 요청에는 `parse_user_goal` schema가 여전히 optional tool로 노출됨
- search query는 raw input에서 나온 `무신사 와이드 데님 팬츠 M 디자인 색`
- 이후 `load_closet_memory`는 같은 raw input에서 `pants`, `무신사`, `user_text_partial_memory`를 만든다
- fetch/classify/OCR/normalize/score/report observation이 이어지고 final report는 grounded 상태로 완료된다

### Raw-input prompt contract verifier 추가

문서와 verifier만 raw-input direct search를 허용하고 실제 runtime prompt가 parser-first로 남아 있으면 실제 LLM 경로가 다시 흔들릴 수 있다.

변경:

- `FITCHECK_AGENT_INSTRUCTIONS`에 `parse_user_goal`은 mandatory first step이 아니며 raw conversation에 충분한 cue가 있으면 `search_web_candidates`를 직접 호출하라고 명시했다.
- `_initial_model_input.required_behavior`에도 `parse_user_goal` 선행 요구 금지와 direct search 허용을 포함했다.
- `verify_production_agent_runtime.py`가 첫 LLM 요청의 instructions와 input item에 이 prompt contract가 들어 있는지 검증한다.

### User reply resume verifier 추가

GPT식 대화형 agent에서는 첫 입력만 처리하고 끝나는 것이 아니라, `waiting_for_user` 이후 사용자의 후속 메시지, 답변, 첨부 사진이 다음 LLM 요청과 tool 선택에 반영되어야 한다.

변경:

- `FitCheckAgentRuntime.reply()`가 후속 입력을 받으면 stale `parsed_liked_item`, `closet_item`, `missing_info`를 정리한다.
- `tool_registry._run_text_for_query()`가 `input.user_replies[].message`와 `answers[].field/value`를 검색/closet memory source text에 포함한다.
- `load_closet_memory` compact output에 `size_label`을 포함해 LLM observation에서 후속 reply의 사이즈 라벨을 볼 수 있게 했다.
- 긴 대화 텍스트에서 `유니폼브릿지에서 산` 같은 brand cue가 앞 문장에 섞여도 마지막 문장 segment를 기준으로 brand를 추출하도록 `_extract_brand()`를 보강했다.

검증:

- fake LLM이 첫 run에서 `load_closet_memory -> ask_missing_info`로 `waiting_for_user` 상태를 만든다.
- reply message, answer, attachment가 다음 LLM request input에 포함된다.
- reply 후 tool path가 `inspect_uploaded_images -> search_web_candidates -> load_closet_memory -> fetch/classify/OCR/normalize/score/report`로 이어진다.
- 두 번째 `load_closet_memory` observation이 reply에서 `유니폼브릿지`, `hoodie`, `XL`, `면 80%`, `fit/material` preference를 복구한다.
- stale `missing_info`는 reply 이후 비워지고 final report는 grounded 상태로 완료된다.

### User reply stale final report guardrail 추가

완료된 run에 사용자가 다시 reply했을 때, 모델이 새 tool을 호출하지 않고 바로 final text를 내면 과거 `write_purchase_report` observation과 과거 `final_report`가 재사용될 수 있다. 이는 대화형 agent에서 후속 사용자 입력을 반영하지 않은 stale answer다.

변경:

- `FitCheckAgentRuntime.reply()`는 후속 입력을 받으면 기존 `final_report`와 `completed_at`을 비운다.
- final text를 받을 때는 가장 최근 `user_reply` 이후 `write_purchase_report` tool output이 있었는지 확인한다.
- 최신 reply 이후 새 report tool output이 없으면 `FINAL_REPORT_TOOL_REQUIRED`로 실패한다.

검증:

- completed run을 만든 뒤 reply를 보낸다.
- fake LLM이 reply 이후 tool 없이 final text를 반환한다.
- runtime은 old report observation을 근거로 인정하지 않고 `FINAL_REPORT_TOOL_REQUIRED`로 실패한다.
- 실패 결과에는 이전 `final_report`와 `completed_at`이 남지 않는다.

### Runtime-level reply session guard 추가

HTTP route는 이미 `_get_session_run()`으로 다른 session의 reply를 `not_found`로 막는다. 하지만 `FitCheckAgentRuntime.reply()` 자체가 session을 검증하지 않으면, future route나 테스트 harness가 runtime을 직접 호출할 때 cross-session reply가 run state를 변형할 수 있다.

변경:

- `FitCheckAgentRuntime.reply()`가 run의 `session_id`와 payload `session_id`를 비교한다.
- run에 session이 있는데 payload session이 없거나 다르면 `not_found`를 반환한다.
- 이 경우 LLM 호출, `user_replies` append, `user_reply` trace append, status 변경을 수행하지 않는다.

검증:

- owner session으로 `waiting_for_user` run을 만든다.
- session 없는 payload와 다른 session payload로 runtime `reply()`를 직접 호출한다.
- 결과는 `not_found`, fake LLM request count는 그대로, 저장된 run은 `waiting_for_user`, `input.user_replies`와 `user_reply` trace는 추가되지 않는다.

### Cached search production gate

사용자가 "비슷한 반팔티"를 요청했을 때 production agent가 기본 cached harness 후보 3개를 그대로 반환하면 agentic search가 아니라 fixture 응답이다. 따라서 production agent route의 `search_web_candidates`는 `cached_harness_search` 결과를 기본으로 노출하지 않는다.

정책:

- `FITCHECK_SEARCH_PROVIDER`가 live provider이면 해당 provider 결과를 사용한다.
- `cached_harness_search`는 `FITCHECK_ALLOW_CACHED_AGENT_SEARCH=1` 또는 run input의 explicit harness opt-in이 있을 때만 허용한다.
- opt-in이 없으면 `LIVE_SEARCH_PROVIDER_REQUIRED` error와 `required_configuration`을 반환하고 run은 `configuration_required`로 멈춘다.
- 이 경우 cached fixture candidates는 observation result에 노출하지 않는다.
- provider가 cached harness이고 opt-in이 없으면 provider를 실행하기 전에 멈춘다. 따라서 fixed candidate cache 생성 같은 부작용도 발생하지 않는다.
- 예전 `/api/run/web-candidates`, `/api/results/web-candidates` deterministic demo route는 현재 `disabled` 응답만 반환한다.
- `run_keyword_web_probe.py`는 더 이상 특정 상품 URL을 기본 source로 갖지 않는다. `FITCHECK_HARNESS_SOURCES_JSON` 또는 `FITCHECK_HARNESS_SOURCES_PATH`로 source를 명시해야 probe가 후보를 만든다.

`verify_production_agent_runtime.py`는 LLM이 `search_web_candidates`를 선택했더라도 cached search opt-in이 없으면 후보가 0개이고 `LIVE_SEARCH_PROVIDER_REQUIRED`로 멈추는지 검증한다. 기존 mock agent loop 검증은 `FITCHECK_ALLOW_CACHED_AGENT_SEARCH=1`을 명시한 harness mode에서만 cached 후보를 사용한다.

### Live search failure and empty-result guardrails

Provider가 live search로 선택된 상태에서도 검색 실패를 tool observation 하나로만 넘기면 모델이 빈 근거로 다음 tool/report를 시도할 수 있다. `search_web_candidates` 결과가 `failed`이면 runtime은 즉시 `SEARCH_PROVIDER_FAILED`로 멈추고 `stop_search_provider_failed` trace를 남긴다.

후보 0건 검색은 첫 번째에는 실패로 보지 않고 `search_deferred=true`, `reason=search_query_expansion_required`, `next_tool_suggestions`를 반환한다. 여기서 후보 0건은 live provider가 아무 결과도 주지 않았다는 뜻만이 아니라, 검색 결과가 있어도 `product_like_only` filter를 통과한 상품형 후보가 없다는 뜻일 수 있다. 이는 모델이 같은 provider에서 검색어를 넓혀 다시 `search_web_candidates`를 호출하게 만드는 backend guidance다. 같은 query로 후보 0건 검색을 반복하면 `REPEATED_EMPTY_SEARCH` guardrail로 run을 종료한다.

검증:

- `verify_production_agent_runtime.py`는 DuckDuckGo challenge page fake provider로 `SEARCH_PROVIDER_FAILED`를 검증한다.
- 같은 빈 검색 query를 두 번 반복하는 fake LLM으로 `REPEATED_EMPTY_SEARCH`와 `continue_candidate_enrichment` trace를 검증한다.
- `verify_live_agent_smoke.py`는 live run이 `failed`로 끝나면 실패로 판정한다.

### Grounding validator 강화

`score_candidate_match`라는 tool 이름만으로 final report 근거를 인정하지 않는다.

인정 조건:

- result status가 `completed`
- `score_deferred`가 아님
- 단일 후보 `score` 또는 전체 후보 `ranked_matches`가 존재
- `write_purchase_report` observation은 completed + grounded final_report가 있을 때만 report observation으로 인정

### Production runtime verifier 강화

`verify_production_agent_runtime.py`는 이제 LLM mock이 다음 tool sequence를 선택했는지 확인한다.

```text
load_closet_memory
search_web_candidates
fetch_product_page
classify_product_assets
extract_size_material_info
normalize_product_spec
score_candidate_match
write_purchase_report
```

### Free-text state change verifier 추가

`state_changes_from_input` 검증을 강화했다. 서로 다른 사용자 옷 설명이 같은 fixture 결과를 그대로 쓰는지, 아니면 parsed state와 search query hint를 바꾸는지 확인한다.

이 검증은 optional parser/helper boundary의 품질을 확인하는 보조 검증이다. Production agent가 반드시 `parse_user_goal`을 먼저 호출해야 한다는 뜻은 아니다.

`verify_backend_infrastructure.py`는 다음 두 입력을 비교한다.

- 반팔 티셔츠: `테스트브랜드`, `L`, `면 100%`, `핏`, `재질`, `오래 입음`
- 데님 팬츠: `무신사`, `M`, `디자인`, `색`, `바지`

검증 항목:

- `parse_user_goal.liked_item.brand`
- `parse_user_goal.liked_item.category`
- `parse_user_goal.liked_item.size_label`
- `parse_user_goal.liked_item.material`
- `parse_user_goal.liked_item.preference_signals`
- `parse_user_goal.search_query_hint`
- `load_closet_memory.closet_item`

`search_query_hint`는 이제 티셔츠에만 고정되지 않고, `pants`, `shirt`, `hoodie`, `knit`, `jacket` 등 `_category_label()` 결과를 반영한다. 소재가 텍스트에서 파싱되면 `면 소재 100%` 같은 material query fragment도 포함한다.

### Raw input tool-call variation verifier 복구

기존 `Free-text state change verifier`는 optional parser/helper boundary를 확인한다. 다만 사용자가 지적한 핵심 문제는 parser가 아니라 production agent route에서 원문 대화가 LLM에게 전달되고, 모델이 그 원문을 보고 다른 backend-owned tool call을 선택할 수 있느냐이다.

`backend_harness/verify_input_variation_matrix.py`를 추가했다.

검증 방식:

- fake Responses-style LLM이 `_initial_model_input`에 들어온 raw Korean text를 읽는다.
- 반팔 티셔츠, 와이드 데님 팬츠, 후드집업 입력을 각각 별도 run으로 실행한다.
- 첫 model-selected tool이 `parse_user_goal`이 아니라 `search_web_candidates`인지 확인한다.
- 각 입력이 서로 다른 query로 이어지는지 확인한다.
- `parse_user_goal`은 tool schema에 남아 있지만, direct raw-input run에서 필수 선행 tool로 호출되지 않는지 확인한다.

검증 query:

- `테스트브랜드 반팔 티셔츠 L 면 소재 100% 핏 내구성`
- `무신사 와이드 데님 팬츠 M 디자인 색`
- `유니클로 후드 집업 XL 기모 여유핏`

### Response schema boundary 추가

핵심 route의 응답에 Pydantic response model을 연결했다.

- `ApiStatusResponse`
- `AgentRunResponse`
- `AgentRunListResponse`
- `AgentReportResponse`
- `ImageUploadResponse`
- `WebCandidateRunResponse`

`verify_backend_infrastructure.py`는 OpenAPI components와 route별 200 response `$ref`가 이 schema로 연결되어 있는지 검증한다.

### Trace/Event schema boundary 추가

agent runtime trace와 SSE event payload에 최소 typed schema를 추가했다.

- `TraceEntryResponse`
- `ToolObservationResponse`
- `AgentStateResponse`
- `AgentStateEventResponse`

tool output trace summary와 observation top-level에는 가능한 경우 다음 metadata를 정규화해 남긴다.

- `provider`
- `provider_mode`
- `external_api_called`
- `timeout_seconds`
- `retry_count`
- `provider_timeout_seconds`
- `provider_retry_count`
- `provider_retry_errors`
- `retry_badges`
- `provider_retry_error_codes`
- `provider_latest_retry_error_code`
- `provider_latest_retry_error_category`
- `provider_error_code`
- `provider_error_category`
- `provider_error_message_preview`
- `tool_timeout_seconds`
- `tool_risk`
- `duration_ms`
- `evidence_status`
- `score_deferred`
- `grounded`

`verify_production_agent_runtime.py`는 search/OCR trace에 provider metadata, tool timeout/risk, execution duration, retry badge가 남는지, production run payload가 `AgentRunResponse`와 `AgentStateEventResponse`로 검증되는지 확인한다.

### Live search candidate ranking 추가

live search provider 후보에 commerce/product-likeness metadata를 추가했다.

- `commerce_score`
- `commerce_signals`
- `content_risk_flags`
- `candidate_type`
- `search_result_rank`
- `search_priority`

Jina/DuckDuckGo fake provider test는 블로그 결과가 먼저 나와도 상품 URL 후보가 우선순위로 올라오는지 검증한다.

### Live search candidate filter 강화

이전 ranking은 상품형 결과를 우선 정렬했지만, 검색 결과 목록에 남은 블로그/기사/코디 글도 `candidates` 배열에 섞일 수 있었다. 이 경우 LLM이 일반 콘텐츠 결과를 후보 상품처럼 해석해 고정 추천처럼 보이는 답변을 만들 위험이 있다.

변경:

- `candidate_filter_policy="product_like_only"`를 search provider 결과에 추가한다.
- `candidate_type=="product_like"`인 결과만 `candidates`에 남긴다.
- 콘텐츠성/불확실 결과는 `rejected_candidates`와 `rejected_candidate_count`에만 남긴다.
- 모든 검색 결과가 콘텐츠성이라면 `candidate_count=0`, `candidates=[]`가 되고, tool layer의 `search_query_expansion_required` guidance가 이어진다.

검증:

- DuckDuckGo, Jina, Brave fake provider에서 블로그 결과가 먼저 나오고 상품 결과가 뒤에 있어도 상품 결과만 후보로 승격되는지 확인한다.
- DuckDuckGo, Jina, Brave fake provider에서 결과가 전부 블로그/기사이면 후보가 0개이고 rejected diagnostics만 남는지 확인한다.

### Live page image asset boundary 추가

live page fetch가 HTML에서 이미지 URL 후보를 수집하고 asset boundary로 노출한다.

- `og:image`
- `twitter:image`
- `link rel=image_src/preload`
- `img src`
- `img data-src/data-original/data-lazy/data-image`
- `img srcset`

수집된 이미지는 URL/alt/class/id/title 기반 heuristic으로 `product_image`, `size_chart`, `material_chart`, `detail_text`로만 분류한다. OCR/vision provider가 연결되지 않은 상태에서는 `image_url_boundary`와 `not_connected`를 남기며, 이미지 내용을 사실로 주장하지 않는다.

### Live page OCR-target image download cache 추가

live page fetch는 OCR 대상 가능성이 있는 이미지 후보를 local cache로 다운로드한다.

- 저장 위치: `harness_outputs/live_assets/<candidate_key>/`
- 다운로드 대상: `size_chart`, `material_chart`, `detail_text`
- 비대상 대표 상품 이미지는 `skipped_not_ocr_target`로 남긴다.
- 다운로드된 target은 `local_path`/`asset_path`를 통해 OCR provider 경계로 전달한다.

`TesseractCliOcrProvider`는 이제 product image 같은 비대상 asset을 OCR 실패로 기록하지 않고, OCR target asset만 처리한다. Tesseract 실행 파일이나 language data가 없으면 여전히 `configuration_required` evidence를 남긴다.

### OCR evidence to normalized spec merge

이전 경계는 `extract_size_material_info`가 OCR evidence를 반환해도 `normalize_product_spec`가 그 evidence를 실제 상품 spec에 반영하지 않았다. 그러면 agent trace에는 OCR tool이 보이지만 실측/소재/가격 비교에는 OCR 결과가 쓰이지 않는 문제가 생긴다.

변경:

- `extract_size_material_info`는 OCR evidence를 candidate product state에도 저장한다.
- `normalize_product_spec`는 최신 `extract_size_material_info` observation의 completed text evidence를 읽어 실측, 소재, 가격 후보를 파싱한다.
- 기존 HTML/page 관측값을 무조건 덮어쓰지 않고 비어 있는 핵심 field만 OCR evidence로 채운다.
- 결과 spec에 `ocr_evidence_used`, `resolved_fields_from_ocr`, 갱신된 `missing_fields`, `field_confidence`를 남긴다.

검증:

- `verify_backend_infrastructure.py`는 fixture OCR evidence의 `총장/어깨/가슴단면/소매/면 100%/39,000원`이 `normalize_product_spec` 후 `measurements`, `material`, `price`로 반영되고 `missing_fields=[]`가 되는지 확인한다.

### Premature missing-info guardrail verifier 추가

LLM이 `fetch_product_page`, `classify_product_assets`, `extract_size_material_info`, `normalize_product_spec` 전에 `ask_missing_info`를 호출해도 backend tool이 즉시 `waiting_for_user`로 전환하지 않는지 검증한다.

검증 결과:

- `ask_missing_info` result는 `status=completed`, `deferred_user_question=true`로 남는다.
- 검색 전 `ask_missing_info`는 `parse_user_goal`을 필수 제안하지 않고, `load_closet_memory`, `search_web_candidates`를 `next_tool_suggestions`에 포함한다.
- run state의 `missing_info`는 설정되지 않는다.
- runtime이 `backend_runtime_guidance` input item을 다음 LLM 요청에 추가한다.
- runtime이 `tool_guidance: continue_candidate_enrichment` trace를 남긴다.
- 이후 fetch/classify/OCR/normalize/score/report tool chain이 계속 진행된다.
- 같은 조기 `ask_missing_info`를 동일 인자로 반복하면 `REPEATED_PREMATURE_MISSING_INFO` guardrail로 종료한다.

이 변경은 backend가 LLM을 건너뛰고 deterministic tool chain을 몰래 실행하는 것이 아니다. OpenAI tool-calling flow처럼 tool output을 다음 모델 요청에 다시 전달하되, premature missing-info/score/report output에는 backend-owned enrichment requirement를 명시해 모델이 다음 tool을 선택하도록 한다.

### Final report tool grounding guardrail 강화

실제 API smoke에서 `write_purchase_report`가 grounding 실패했는데 LLM final text가 completed로 받아들여질 수 있는 경로를 확인했다.

현재 runtime은 LLM final text를 수락하기 전에 가장 최근 `write_purchase_report` observation을 확인한다.

- report tool observation이 없으면 `FINAL_REPORT_TOOL_REQUIRED`
- 가장 최근 report tool이 completed/grounded가 아니면 `FINAL_REPORT_TOOL_NOT_GROUNDED`
- `score_candidate_match(candidate_key="all")`의 `ranked_matches` 안에 있는 특정 후보는 해당 후보 리포트 grounding 근거로 인정한다.

검증은 `verify_production_agent_runtime.py`의 failed-report-then-final mock과 `verify_backend_infrastructure.py`의 ranked match validator case로 고정했다.

### Report grounding validator URL/material claim 검증 추가

기존 validator는 observation에 없는 숫자를 막는 데 집중했다. 현재는 final report의 다음 claim도 relevant observation과 대조한다.

- URL claim: report에 `https://...` URL이 있으면 search/fetch/normalization/score/report observation 안에 같은 URL이 있어야 한다.
- 소재 claim: report에 면/코튼/cotton, 폴리/폴리에스터/polyester, 린넨, 울, 나일론, 레이온, 아크릴, 스판/폴리우레탄 등 소재 claim이 있으면 observation에서 같은 소재 계열이 관측되어야 한다.

추가 reason:

- `report_contains_unobserved_urls`
- `report_contains_unobserved_material_claims`

`verify_backend_infrastructure.py`는 관측된 URL/면 100% report가 통과하는지, 관측되지 않은 URL/린넨 claim이 실패하는지 검증한다.

### Grounding evidence span 반환 추가

`validate_report_grounding`은 이제 통과/실패 여부만 반환하지 않고, report 안의 관측된 claim이 어떤 observation field에서 근거를 얻었는지 `grounding_evidence`로 반환한다.

예시 field:

- `claim_type`: `number`, `url`, `material`
- `claim`: `81`, `https://...`, `cotton`
- `evidence[].path`: `observations[2].result.score.overall`
- `evidence[].value`: 해당 field 값

`verify_backend_infrastructure.py`는 다음 evidence path를 검증한다.

- URL claim -> `result.source_url`
- 소재 claim `cotton` -> `normalized_spec.material.cotton`
- 숫자 claim `81` -> `score.overall`

### Grounding evidence response schema 추가

`grounding_validation`은 더 이상 route response에서 느슨한 `dict[str, Any]`로만 노출되지 않는다. `backend_harness/app/schemas.py`에 다음 Pydantic response schema를 추가했다.

- `GroundingEvidenceSourceResponse`
- `GroundingEvidenceItemResponse`
- `GroundingValidationResponse`

`FinalReportResponse.grounding_validation`과 `AgentReportResponse.grounding_validation`은 같은 `GroundingValidationResponse`를 사용한다. 따라서 OpenAPI에서 `valid`, `reason`, `suspicious_numbers`, `suspicious_urls`, `suspicious_material_claims`, `suspicious_identity_claims`, `suspicious_style_claims`, `grounding_evidence[].claim_type`, `grounding_evidence[].evidence[].path/value` 구조를 확인할 수 있다.

`verify_backend_infrastructure.py`는 schema component 노출과 샘플 `grounding_evidence` payload validation을 함께 검증한다.

### Grounding suspicious field API smoke 보강

상품명/브랜드 identity claim과 핏/색상/디자인 style claim validator가 내부 결과에만 존재하면 UI나 API consumer가 grounding 실패 사유를 안정적으로 표시할 수 없다. 따라서 suspicious field들이 Pydantic model에 있다는 수준을 넘어 OpenAPI와 실제 HTTP 응답 표면에 노출되는지 smoke에서 확인한다.

변경:

- `verify_backend_infrastructure.py`가 `GroundingValidationResponse` OpenAPI properties에 `suspicious_identity_claims`, `suspicious_style_claims`, `grounding_evidence`, `citation_badges`가 존재하는지 직접 확인한다.
- `verify_http_api_smoke.py`가 `/openapi.json`에서 `GroundingValidationResponse`와 grounding suspicious properties 누락을 확인한다.
- HTTP smoke의 seeded final report에 `suspicious_identity_claims=[]`, `suspicious_style_claims=[]`를 포함한다.
- `/api/agent/runs/{run_id}/report`와 `/api/agent/runs/{run_id}/events`의 SSE payload가 같은 suspicious field를 유지하는지 확인한다.

검증:

- `backend_harness/verify_backend_infrastructure.py`
- `FITCHECK_RUN_HTTP_API_SMOKE=1 backend_harness/verify_http_api_smoke.py`

### Grounding citation badge 추가

`grounding_evidence`는 validator와 개발자가 확인하기 좋은 원본 path/value 구조지만, UI나 SSE consumer가 바로 표시하기에는 길다. 따라서 같은 근거를 사람이 읽기 쉬운 축약 badge로도 반환한다.

변경:

- `validate_report_grounding` 결과에 `citation_badges`를 추가한다.
- 각 badge는 `id`, `label`, `claim_type`, `claim`, `source_path`, `source_value_preview`, `observation_index`, `source_kind`, `source_count`를 포함한다.
- 예: `{"id": "G1", "label": "[G1]", "claim": "81", "source_path": "observations[0].result.score.overall"}`
- `CitationBadgeResponse` schema를 추가하고 `GroundingValidationResponse.citation_badges`, `AgentReportResponse.citation_badges`, `AgentStateEventResponse.citation_badges`로 노출한다.
- `/api/agent/runs/{run_id}/report`와 `/api/agent/runs/{run_id}/events`는 top-level `citation_badges`도 반환한다.
- evidence 수집 시 `citation_badges` subtree를 제외해 이전 badge 값이 다음 grounding 근거로 재사용되는 self-grounding을 막는다.

검증:

- `verify_backend_infrastructure.py`는 validator가 `[G1]` badge를 생성하는지, OpenAPI schema에 `CitationBadgeResponse`가 노출되는지, badge 내부 값이 report 근거로 재수집되지 않는지 확인한다.
- `verify_http_api_smoke.py`는 seeded run의 `/report`와 SSE `agent_state` payload에 top-level `citation_badges`가 보존되는지 확인한다.

### Grounding evidence self-grounding 차단

실제 `/api/agent/runs/{run_id}/report` smoke 중 `grounding_evidence`가 과거 `write_purchase_report.result.grounding_validation` 내부 path를 다시 evidence 후보로 수집할 수 있는 경로를 확인했다.

`report_grounding.py`는 이제 evidence 수집 시 다음 subtree를 claim source에서 제외한다.

- `final_report`
- `grounding_validation`
- `grounding_evidence`
- `citation_badges`

또한 URL 안의 숫자와 `1. 후보` 같은 순번 marker는 별도 숫자 claim으로 검증하지 않는다. URL claim은 URL validator가, 점수/실측 숫자는 observation의 실제 numeric field가 각각 검증한다.

`verify_backend_infrastructure.py`는 다음 회귀 케이스를 검증한다.

- `write_purchase_report.result.final_report`에만 있는 99점은 근거로 인정하지 않는다.
- `grounding_validation.grounding_evidence` 내부 path/value는 evidence source가 될 수 없다.
- URL path의 `123`은 별도 number claim으로 노출되지 않는다.

### Premature score/report enrichment guardrail 추가

LLM이 `fetch_product_page`, `classify_product_assets`, `extract_size_material_info`, `normalize_product_spec`를 건너뛰고 바로 `score_candidate_match` 또는 `write_purchase_report`를 호출하는 경로를 강화했다.

현재 동작:

- enrichment가 끝나기 전 `score_candidate_match`는 실제 점수를 만들지 않고 `score_deferred=true`를 반환한다.
- score deferred result는 `reason=candidate_enrichment_required_before_scoring`와 `next_tool_suggestions`를 포함한다.
- 이 상태에서 `write_purchase_report`가 호출되면 generic "score 없음" 실패가 아니라 `CANDIDATE_ENRICHMENT_REQUIRED_BEFORE_REPORT`로 실패한다.
- report 실패 result도 `score_deferred=true`, `reason=candidate_enrichment_required_before_report`, `next_tool_suggestions`를 유지한다.
- deferred score는 `match_results`에 저장되지 않으므로 final report grounding 근거가 될 수 없다.

`verify_production_agent_runtime.py`는 mock LLM이 검색 직후 `score_candidate_match(candidate_key="all")`와 `write_purchase_report(candidate_key="all")`를 조급하게 호출하는 케이스를 검증한다. 기대 결과는 final rejection이며, report result에 enrichment plan이 보존되어야 한다.

### Backend-owned tool argument validation 추가

기존 `ToolDefinition.parameters`는 LLM에게 전달되는 JSON schema였지만, backend 실행 전에 직접 검증하지는 않았다. 이제 `ToolDefinition.execute()`는 handler 호출 전에 현재 tool schema subset을 검증한다.

검증 대상:

- top-level arguments는 object여야 한다.
- `required` field가 누락되면 실패한다.
- `additionalProperties=false`에서 schema에 없는 field가 오면 실패한다.
- `string`, `boolean`, `array`, `object`, `number`, `integer`, `null` type을 확인한다.
- array item schema도 확인한다.

검증 실패 result:

```json
{
  "status": "failed",
  "error": {
    "code": "TOOL_ARGUMENT_VALIDATION_FAILED",
    "message": "fetch_product_page arguments did not match the backend tool schema.",
    "details": [
      {"path": "$.candidate_key", "code": "missing_required", "expected": "present"}
    ]
  },
  "validation_errors": [
    {"path": "$.candidate_key", "code": "missing_required", "expected": "present"}
  ]
}
```

`agent_runtime.py`는 `validation_errors`를 tool output trace summary에 포함한다. malformed tool call도 observation으로 저장되고, 모델에는 function output으로 반환된다. LLM이 그 뒤 final text를 내더라도 final report guardrail은 별도로 적용된다.

`ToolDefinition.to_responses_tool()`은 현재 optional argument schema를 유지하기 위해 `strict=false`를 명시한다. OpenAI Responses에서 `strict`를 생략하면 schema가 strict 쪽으로 정규화될 수 있고, strict mode는 모든 property를 required로 요구한다. 따라서 `parse_user_goal`처럼 인자를 비워도 run context fallback으로 동작해야 하는 optional-helper tool은 현재 schema 그대로 strict mode로 전환하면 동작 의미가 바뀐다. strict-mode schema migration은 optional fields를 nullable required field로 재작성한 뒤 별도 단계에서 진행한다.

검증:

- `verify_backend_infrastructure.py`: 모든 tool schema가 `additionalProperties=false`, `required` list, `strict=false`를 가지는지 확인한다.
- `verify_backend_infrastructure.py`: missing required, unexpected argument, wrong type을 `TOOL_ARGUMENT_VALIDATION_FAILED`로 검증한다.
- `verify_production_agent_runtime.py`: mock LLM이 malformed `fetch_product_page({})`를 호출해도 structured failed observation/trace가 남는지 확인한다.
- `verify_local_llm_tool_adapter.py`: Responses-style `strict=false`가 chat-completions function schema로 보존되는지 확인한다. `load_closet_memory`뿐 아니라 required field가 있는 `search_web_candidates`도 `strict=false`와 `required=["query"]`를 유지하는지 확인한다.

### Tool argument JSON parse failure 분리

OpenAI Responses와 local chat-completions adapter 모두 function call arguments를 JSON string으로 전달한다. 기존 runtime은 `json.loads()` 실패 시 arguments를 `{}`로 바꿨기 때문에, 실제 원인은 JSON parse 실패인데도 `candidate_key` 누락 같은 schema validation 실패로만 보일 수 있었다.

현재 `_extract_tool_calls()`는 parse 실패 정보를 보존하고, runtime은 handler 실행 전에 `TOOL_ARGUMENT_JSON_PARSE_FAILED` result를 만든다.

예:

```json
{
  "status": "failed",
  "error": {
    "code": "TOOL_ARGUMENT_JSON_PARSE_FAILED",
    "message": "fetch_product_page arguments were not valid JSON.",
    "details": {
      "code": "invalid_json",
      "message": "Expecting value",
      "line": 1,
      "column": 18,
      "position": 17,
      "raw_arguments_excerpt": "{\"candidate_key\":"
    }
  },
  "validation_errors": [
    {"path": "$", "code": "invalid_json", "message": "Expecting value"}
  ],
  "raw_arguments_excerpt": "{\"candidate_key\":"
}
```

`tool_call` trace input에도 `arguments_parse_error`가 남고, `tool_output` trace summary에는 `raw_arguments_excerpt`와 `validation_errors`가 남는다. 따라서 JSON parse 실패, schema validation 실패, unknown tool 실패를 서로 다른 failure code로 구분할 수 있다.

`verify_production_agent_runtime.py`는 mock LLM이 `arguments="{\"candidate_key\":"`를 보낸 케이스를 검증한다.

### Unknown tool과 반복 실패 guardrail 추가

모델이 registry에 없는 tool name을 호출할 수 있는 경로를 structured failure로 바꿨다. 기존에는 `ToolRegistry.get()`의 `KeyError`가 generic string error로 observation에 남을 수 있었다.

현재 unknown tool result:

```json
{
  "status": "failed",
  "error": {
    "code": "UNKNOWN_TOOL",
    "message": "Model requested unknown backend tool: invent_product_specs",
    "tool": "invent_product_specs",
    "available_tools": ["parse_user_goal", "load_closet_memory", "..."]
  }
}
```

같은 failed tool call을 arguments/error code까지 동일하게 2회 연속 반복하면 run을 즉시 실패시킨다. max turns까지 불필요하게 LLM 호출을 반복하지 않고, trace에 `stop_repeated_failed_tool_call` guardrail step을 남긴다.

Run error:

```json
{
  "code": "REPEATED_FAILED_TOOL_CALL",
  "tool": "fetch_product_page",
  "error_code": "TOOL_ARGUMENT_VALIDATION_FAILED",
  "repeat_count": 2
}
```

`verify_production_agent_runtime.py`는 다음 mock LLM 케이스를 검증한다.

- registry에 없는 `invent_product_specs` 호출 -> `UNKNOWN_TOOL` observation/trace
- malformed `fetch_product_page({})`를 2회 반복 -> `REPEATED_FAILED_TOOL_CALL` guardrail로 run failed

### Live provider opt-in smoke verifier 추가

`backend_harness/verify_live_provider_smoke.py`를 추가했다.

기본 동작:

- `FITCHECK_RUN_LIVE_PROVIDER_SMOKE`가 없으면 외부 서비스를 호출하지 않고 skipped 결과를 출력한다.
- opt-in 상태에서도 현재 선택된 provider가 cached/fixture이면 해당 check만 skipped로 남긴다.

검증하는 live gap:

- `FITCHECK_SEARCH_PROVIDER=jina_search`인데 `JINA_API_KEY`/`FITCHECK_SEARCH_API_KEY`가 없으면 실패
- `FITCHECK_SEARCH_PROVIDER=searxng`인데 `FITCHECK_SEARXNG_URL`/`SEARXNG_URL`이 없으면 실패
- 선택된 live search provider가 request를 완료하지 못하면 실패
- live search가 external call을 기록하지 않거나 후보를 반환하지 못하면 실패
- `FITCHECK_OCR_PROVIDER=tesseract_cli`인데 local `tesseract` 실행 파일이 없으면 실패
- Tesseract provider가 OCR target asset의 `asset_path`를 받지 못하면 실패
- Tesseract 실행은 되었지만 language/configuration 문제로 OCR evidence가 usable하지 않으면 실패

2026-06-07 현재 환경 관측:

- 기본 provider 설정 `cached_harness_search`/`fixture_ocr_boundary`에서는 provider verifier가 completed/skipped로 끝난다. 단, production agent route의 search tool은 별도 opt-in 없이는 cached 후보를 사용하지 않는다.
- `FITCHECK_OCR_PROVIDER=tesseract_cli`를 선택하면 local `tesseract` 실행 파일이 없어 live OCR smoke가 실패한다.
- `FITCHECK_SEARCH_PROVIDER=jina_search`를 선택하면 API key가 없어 live search smoke가 실패한다.
- `FITCHECK_SEARCH_PROVIDER=searxng`를 선택하면 base URL이 없어 live search smoke가 실패한다.
- `FITCHECK_SEARCH_PROVIDER=duckduckgo_html`을 선택하면 DuckDuckGo가 anomaly/challenge page를 반환한다.

DuckDuckGo HTML adapter는 challenge page를 빈 후보 성공으로 처리하지 않고 `DUCKDUCKGO_HTML_CHALLENGE` 실패로 기록하도록 바꿨다.

### Brave Search live provider boundary 추가

DuckDuckGo HTML provider가 현재 환경에서 challenge page로 막히는 것이 관측되어, key 기반 대체 live search provider로 Brave Search adapter를 추가했다.

구현:

- `FITCHECK_SEARCH_PROVIDER=brave_search`
- API key: `BRAVE_SEARCH_API_KEY` 또는 공용 `FITCHECK_SEARCH_API_KEY`
- endpoint: `https://api.search.brave.com/res/v1/web/search`
- header: `X-Subscription-Token`
- 기본 지역/언어: `FITCHECK_SEARCH_COUNTRY=KR`, `FITCHECK_SEARCH_LANG=ko`
- Brave `web.results`의 `title`, `url`, `description`을 FitCheck candidate로 변환
- 기존 commerce/product-likeness ranking을 그대로 적용

검증:

- fake Brave response가 product-like 후보를 블로그 후보보다 우선순위로 올리는지 확인
- Brave request에 subscription token header가 들어가는지 확인
- API key가 없으면 `configuration_required`인지 확인
- opt-in live smoke에서 key 누락 시 provider별 key requirement를 명확히 출력

### No-LLM HTTP API route smoke verifier 추가

`backend_harness/verify_http_api_smoke.py`를 추가했다.

기본 실행은 running server를 요구하지 않고 `status=skipped`를 출력한다. `FITCHECK_RUN_HTTP_API_SMOKE=1`을 설정하면 실행 중인 API server를 대상으로 HTTP route contract를 검증한다. 이 smoke는 seeded run을 SQLite store에 직접 넣고 조회 route만 호출한다. `POST /api/agent/runs`를 호출하지 않으므로 외부 LLM API를 호출하지 않는다.

검증 대상:

- `GET /api/status`: status/session/provider/production route field
- cookie-only `GET /api/status` 2회: `fitcheck_session_id` cookie와 `X-FitCheck-Session-Id` response header가 같은 session을 유지
- `GET /openapi.json`: 핵심 response schema/response `$ref`와 create/reply/upload/web-candidate requestBody `$ref`
- invalid request body: upload `{}`는 `media_type`, agent create `attachments="not-a-list"`는 `attachments`, reply `answers="not-a-list"`는 `answers` 422 validation error
- `POST /api/uploads/images`: unsupported media validation과 png upload success
- seeded agent run: owner session에서는 detail/list/report/SSE 조회 가능, 다른 session의 detail/report/reply/SSE에서는 `not_found`
- seeded run trace: `run -> tool_call(search_web_candidates) -> tool_output(search_web_candidates)` 3단계를 만들고, detail trace input/output, observation, SSE `latest_trace`가 같은 `call_id`를 유지하는지 확인
- `GET /api/agent/runs/{missing}`: `not_found` JSON
- `GET /api/agent/runs/{missing}/events`: `text/event-stream`, `id`, `event: agent_state`, `retry`, JSON `data.status=not_found`, no-buffer headers

세션 격리 check를 추가하면서 `/api/agent/runs/{run_id}/report`의 not-found branch가 `AgentReportResponse.trace_count` 필수 필드를 누락해 500을 내는 것을 발견했다. route를 수정해 not-found report response도 `trace_count=0`을 반환하도록 했고, `verify_backend_infrastructure.py`에 not-found report schema validation을 추가했다.

이후 seeded run의 `/events`도 session isolation 대상에 포함했다. owner session은 `id=<run_id>:trace:3`, `event: agent_state`, `retry=3000`, `data.status=completed`, `trace_count=3`을 받고, SSE `latest_trace[0].output.call_id`가 detail trace/observation의 `call_id`와 같아야 한다. 다른 session은 같은 run id에 대해 `id=<run_id>:trace:0`, `data.status=not_found`를 받아야 한다.

이후 seeded run의 `/reply`도 session isolation 대상에 포함했다. owner reply는 실제 LLM을 호출할 수 있으므로 smoke에서는 실행하지 않고, 다른 session의 reply가 `not_found`로 막혀 runtime resume으로 들어가지 않는지만 확인한다.

이후 invalid request body 422 validation도 HTTP smoke 대상에 포함했다. 이 check는 `POST /api/agent/runs`에 invalid body를 보내지만 validation 단계에서 422로 끝나야 하므로 LLM configuration이나 external API call에 의존하지 않는다.

현재 실행 중인 `http://127.0.0.1:8000` 서버에서 opt-in smoke가 통과했다. 이로써 app 객체의 OpenAPI 검사뿐 아니라 실제 HTTP serialization, request body validation, cookie persistence, header session isolation, upload route, report/reply not-found schema, SSE wire format과 SSE session isolation을 함께 검증할 수 있다.

### Live production agent route opt-in smoke verifier 추가

`backend_harness/verify_live_agent_smoke.py`를 추가했다.

기본 실행은 외부 LLM API를 호출하지 않고 `status=skipped`를 출력한다. `FITCHECK_RUN_LIVE_AGENT_SMOKE=1`을 설정하면 실행 중인 API server를 대상으로 실제 HTTP route를 호출한다.

검증 대상:

- `GET /api/status`의 `llm.status=connected`
- `POST /api/agent/runs`에서 run 생성
- run의 `llm.external_api_called=true`
- trace에 model-selected `tool_call`과 backend `tool_output` 기록
- state에 backend tool observation 저장
- 같은 session header로 `GET /api/agent/runs/{run_id}` 조회 가능

### Live agent smoke boundary/completion 분리와 빈 검색 attempt guardrail

실제 OpenAI-compatible LLM으로 live agent smoke를 실행하자 LLM API 호출, model-selected tool call, backend observation 저장은 확인됐지만 검색 후보 0건이 반복된 뒤 final text를 내면서 `FINAL_REPORT_TOOL_REQUIRED` guardrail에 걸렸다. 이 결과는 "agentic boundary는 동작하지만 end-to-end completion은 provider/search evidence 부족으로 실패"라는 상태인데, 기존 smoke는 이를 단일 실패로만 보고했다.

변경:

- `verify_live_agent_smoke.py`가 `agentic_boundary_status`와 `completion_status`를 분리해 출력한다.
- 기본 smoke status는 external LLM call, model-selected tool call, backend tool output/observation, session fetch 같은 boundary 실패만 실패 gate로 본다.
- live smoke boundary는 `tool_call` trace input, `tool_output` trace output, observation이 같은 `call_id`를 공유하는지도 확인하고, summary에 `call_id_correlation_count`를 노출한다. OpenAI Responses function calling 문서에서 tool call output은 특정 model tool call을 `call_id`로 참조하므로, 같은 session의 `GET /api/agent/runs/{run_id}` 재조회에서도 이 correlation이 유지되는지 확인한다. Verifier helper는 missing `call_id`와 mismatched `call_id`를 모두 실패로 보는 self-check를 skip 실행에서도 수행한다.
- `FITCHECK_LIVE_AGENT_REQUIRE_COMPLETION=1`을 설정하면 final completion 실패도 smoke 실패로 처리한다.
- 현재 live search provider가 없을 때도 LLM/tool loop만 확인할 수 있도록 `FITCHECK_LIVE_AGENT_ALLOW_CACHED_SEARCH=1`을 payload의 `allow_cached_harness_search=true`로 전달한다. 이는 production default가 아니라 명시적 테스트 opt-in이다.
- runtime에 `EMPTY_SEARCH_ATTEMPT_LIMIT_EXCEEDED` guardrail을 추가해 같은 query 반복뿐 아니라 서로 다른 query로도 연속 빈 검색 attempt가 limit을 넘으면 비용 루프를 종료한다.

검증:

- `verify_production_agent_runtime.py`가 서로 다른 0건 query 5개를 던지는 fake LLM에서 `EMPTY_SEARCH_ATTEMPT_LIMIT_EXCEEDED`와 `stop_empty_search_attempt_limit` trace를 확인한다.
- `FITCHECK_RUN_LIVE_AGENT_SMOKE=1 FITCHECK_LIVE_AGENT_ALLOW_CACHED_SEARCH=1` 실행 결과, `agentic_boundary_status=completed`, `completion_status=failed`, `llm.external_api_called=true`, model-selected tool calls, backend observations, `call_id_correlation_count=6`, `fetched_call_id_correlation_count=6`가 확인됐다.

FastAPI `TestClient`는 공식 테스트 방식이지만 현재 venv에서는 `starlette.testclient`가 `httpx2`를 요구해 사용할 수 없다. 그래서 이 smoke는 기본 회귀 테스트가 아니라 실행 중인 서버를 대상으로 하는 opt-in HTTP smoke로 둔다.

### Tool trace metadata 정규화 강화

agent runtime의 tool call/output trace와 observation에 실행 metadata를 추가했다.

추가된 field:

- `tool_timeout_seconds`
- `tool_risk`
- `duration_ms`
- provider result의 `timeout_seconds`
- provider result의 `retry_count`
- observation top-level의 `provider_timeout_seconds`
- observation top-level의 `provider_retry_count`

적용된 도구 risk:

- `search_web_candidates`: `external_search_io`
- `fetch_product_page`: `external_page_fetch_io`
- `extract_size_material_info`: `ocr_or_vision_io`
- `inspect_uploaded_images`: `local_file_or_ocr_io`

검색/OCR provider 결과도 `timeout_seconds`와 `retry_count`를 반환한다. cached/fixture provider는 `retry_count=0`이고, live search/page fetch provider는 제한적 retry가 설정되어 있으면 실제 재시도 횟수를 기록한다.

검증은 `verify_production_agent_runtime.py`에서 search/OCR tool call, tool output, stored observation에 metadata가 남는지 확인한다.

### Live search/page fetch retry policy 추가

Jina, Brave, DuckDuckGo HTML search provider와 live product page fetch에 제한적 GET retry를 추가했다.

적용 범위:

- `FITCHECK_SEARCH_PROVIDER=jina_search`
- `FITCHECK_SEARCH_PROVIDER=brave_search`
- `FITCHECK_SEARCH_PROVIDER=searxng`
- `FITCHECK_SEARCH_PROVIDER=duckduckgo_html`
- live search candidate에 대한 `fetch_product_page`

설정:

- `FITCHECK_SEARCH_MAX_RETRIES`: live search provider retry 횟수, 기본 `1`
- `FITCHECK_PAGE_FETCH_MAX_RETRIES`: live product page fetch retry 횟수, 기본 `1`

Retry 대상:

- `requests` timeout/connection/chunked encoding error
- HTTP status `429`, `500`, `502`, `503`, `504`

Provider/tool result에는 다음 field가 남는다.

- `retry_count`: 성공 전 실제 retry 횟수
- `retry_errors`: retry를 유발한 짧은 error/status 기록

`verify_backend_infrastructure.py`는 fake Jina timeout 후 성공과 fake live page timeout 후 성공을 검증한다.

### Provider retry badge 추가

Provider retry raw field는 debugging에는 충분하지만 UI/SSE 소비자가 매번 `retry_count`, `retry_errors`, provider status를 조합해 표시하면 route별 해석이 달라질 수 있다. Runtime이 tool output summary와 observation top-level에 `retry_badges`를 추가해 표시용 상태를 정규화한다.

Badge 상태:

- `not_retried`: provider tool이 성공했고 retry가 없었다.
- `retried`: provider tool이 retry 후 성공했다.
- `retry_failed`: retry를 수행했지만 tool/provider 결과가 실패했다.
- `not_started`: configuration_required 등으로 provider 실행이 시작되지 않았다.
- `failed_no_retry`: retry 없이 provider tool이 실패했다.

Badge에는 `provider`, `provider_mode`, `retry_count`, `retry_error_count`, `latest_error_preview`, `timeout_seconds`, `external_api_called`, `provider_latest_retry_error_code`, `provider_error_code`, `provider_error_category`, `source_kind=provider_runtime`을 가능한 만큼 담는다.

검증:

- `verify_backend_infrastructure.py`는 retry 성공/실패/configuration_required/non-provider 결과의 badge 변환을 검증한다.
- `verify_production_agent_runtime.py`는 fake LLM loop의 search/OCR trace output과 stored observation에 `retry_badges`가 들어오는지 검증한다.
- `RetryBadgeResponse`와 `ToolObservationResponse.retry_badges`가 Pydantic/OpenAPI schema에 노출된다.

### Provider error code normalization 추가

Provider별 raw error 문자열은 UI/SSE filter와 운영 로그 grouping에 부적합하다. `provider_error_metadata_from_result` helper를 추가해 최종 provider 실패와 retry 중 발생한 실패 원인을 안정 field로 정규화한다.

정규화 field:

- `provider_retry_error_codes`: retry 중 발생한 raw error string을 순서대로 code화한다.
- `provider_latest_retry_error_code`: 마지막 retry 원인.
- `provider_latest_retry_error_category`: 마지막 retry 원인의 category.
- `provider_evidence_error_codes`: OCR evidence별 failed/configuration/not_connected reason을 순서대로 code화한다.
- `provider_latest_evidence_error_code`: 마지막 OCR evidence error 원인.
- `provider_latest_evidence_error_category`: 마지막 OCR evidence error category.
- `provider_error_code`: 최종 provider failure/configuration/fetch failure 원인. 성공 후 retry에는 이 field를 붙이지 않는다.
- `provider_error_category`: UI grouping용 category.
- `provider_error_message_preview`: 원본 message의 짧은 preview.

대표 mapping:

- `http_status:429` -> `HTTP_429_RATE_LIMITED` / `rate_limited`
- `http_status:503` -> `HTTP_503_SERVICE_UNAVAILABLE` / `provider_server_error`
- timeout exception text -> `REQUEST_TIMEOUT` / `timeout`
- connection/DNS/refused exception text -> `REQUEST_CONNECTION_ERROR` / `connection_error`
- `SEARCH_PROVIDER_API_KEY_REQUIRED`, `SEARCH_PROVIDER_BASE_URL_REQUIRED`, `LIVE_SEARCH_PROVIDER_REQUIRED` -> `configuration`
- `DUCKDUCKGO_HTML_CHALLENGE` -> `challenge`
- `live_ocr_provider_not_selected` evidence -> `OCR_PROVIDER_NOT_CONNECTED` / `provider_not_connected`
- `tesseract_cli_not_found` evidence -> `OCR_TESSERACT_CLI_NOT_FOUND` / `configuration`
- `tesseract_cli_timeout` evidence -> `OCR_TESSERACT_TIMEOUT` / `timeout`
- `tesseract_cli_failed` evidence -> `OCR_TESSERACT_PROCESS_FAILED` / `provider_process_failed`
- `uploaded_file_not_found` evidence -> `OCR_UPLOAD_FILE_NOT_FOUND` / `local_file_missing`
- `no_local_asset_path_for_live_ocr` evidence -> `OCR_LOCAL_ASSET_PATH_REQUIRED` / `asset_boundary`

Runtime 적용:

- tool output trace summary에 provider error metadata를 추가한다.
- observation top-level에 provider error metadata를 추가한다.
- `retry_badges`에도 최신 retry/final error code를 포함한다.
- `SEARCH_PROVIDER_FAILED`와 configuration_required guardrail의 top-level error에 normalized provider error metadata를 포함한다.
- search provider matrix summary에도 같은 metadata를 포함한다.
- `inspect_uploaded_images`와 `extract_size_material_info` tool result 자체에도 OCR evidence 기반 provider error metadata를 포함해 LLM이 provider capability gap을 볼 수 있게 한다.

검증:

- `verify_backend_infrastructure.py`는 retry timeout, HTTP 503, challenge mapping을 검증한다.
- `verify_production_agent_runtime.py`는 cached search configuration gate와 DuckDuckGo challenge failure에서 run error, observation, trace output에 normalized code/category가 남는지 검증한다.
- `fetch_product_page` result는 live page fetch 실패 정규화를 위해 `fetch_status`와 `fetch_error`도 노출한다.
- `verify_backend_infrastructure.py`는 fixture OCR upload inspection이 `OCR_PROVIDER_NOT_CONNECTED`, Tesseract executable missing이 `OCR_TESSERACT_CLI_NOT_FOUND`, live page fixture OCR target이 `OCR_PROVIDER_NOT_CONNECTED`로 정규화되는지 검증한다.

### Self-hosted SearXNG search provider 추가

DuckDuckGo HTML provider는 no-key best-effort라 challenge page에 취약하고, Brave/Jina는 cloud API key가 필요하다. Com2uS 방향의 local/self-hosted 구성도 지원하려면 search boundary가 자체 운영 가능한 metasearch endpoint를 선택할 수 있어야 한다.

변경:

- `FITCHECK_SEARCH_PROVIDER=searxng`를 추가한다.
- `FITCHECK_SEARXNG_URL` 또는 `SEARXNG_URL`을 base URL로 사용한다.
- provider는 `<base_url>/search`에 `q`, `format=json`, `categories=general`, `language`, `safesearch` parameter를 보내고, 응답 `results[].title/url/content`를 후보 관측값으로 변환한다.
- 기존 `product_like_only` filter를 동일하게 적용한다.
- base URL이 없으면 `SEARCH_PROVIDER_BASE_URL_REQUIRED` configuration gate를 반환한다.
- `/api/status` provider payload에 `requires_base_url`, `base_url_configured`, `base_url`을 노출한다.
- `verify_live_provider_smoke.py`는 opt-in 상태에서 SearXNG base URL 누락을 실패로 판정한다.

검증:

- fake SearXNG JSON 결과에서 블로그 결과가 먼저 나오고 상품 결과가 뒤에 있어도 상품 결과만 후보로 승격되는지 확인한다.
- fake SearXNG 결과가 전부 블로그/기사이면 후보가 0개이고 rejected diagnostics만 남는지 확인한다.
- SearXNG base URL 미설정이 `configuration_required`로 남는지 확인한다.

### Search provider matrix verifier 추가

기존 `verify_live_provider_smoke.py`는 현재 선택된 provider 하나만 확인한다. 실제 운영 후보를 비교하려면 Jina, Brave, SearXNG, DuckDuckGo HTML을 같은 query로 확인하고, 설정 누락과 실행 실패를 같은 출력에서 비교할 수 있어야 한다.

변경:

- `backend_harness/verify_search_provider_matrix.py`를 추가한다.
- 기본 실행은 외부 호출 없이 `skipped`를 출력한다.
- `FITCHECK_RUN_SEARCH_PROVIDER_MATRIX=1`일 때만 provider matrix를 실행한다.
- `FITCHECK_SEARCH_PROVIDER_MATRIX`로 provider 목록을 지정할 수 있다. 기본값은 `jina_search,brave_search,searxng,duckduckgo_html`이다.
- 각 provider별로 `configuration_required`, `completed`, `completed_no_candidates`, `failed`를 구분한다.
- 결과에는 `external_api_called`, `duration_ms`, `retry_count`, `candidate_count`, `candidate_filter_policy`, `rejected_candidate_count`, 첫 후보 요약을 남긴다.
- `FITCHECK_SEARCH_PROVIDER_MATRIX_REQUIRE_SUCCESS=1`이면 최소 1개 provider가 상품형 후보를 반환해야 통과한다.

검증:

- opt-in이 없으면 `skipped`를 반환한다.
- Jina/SearXNG만 선택하고 key/base URL이 없으면 `configuration_required`로 정리하되 외부 호출은 시도하지 않는다.
- `FITCHECK_SEARCH_PROVIDER_MATRIX_REQUIRE_SUCCESS=1`일 때 성공 provider가 없으면 exit 1로 실패 gate가 된다.

### OCR provider matrix verifier 추가

기존 `verify_live_provider_smoke.py`는 현재 선택된 OCR provider 하나만 확인한다. OCR은 product detail image path와 uploaded image path가 별도 경계이므로, provider 후보를 같은 출력에서 비교하고 두 경로 모두 확인할 수 있어야 한다.

변경:

- `backend_harness/verify_ocr_provider_matrix.py`를 추가한다.
- 기본 실행은 OCR provider 호출 없이 `skipped`를 출력한다.
- `FITCHECK_RUN_OCR_PROVIDER_MATRIX=1`일 때만 OCR matrix를 실행한다.
- `FITCHECK_OCR_PROVIDER_MATRIX`로 provider 목록을 지정할 수 있다. 기본값은 `fixture_boundary,tesseract_cli`이다.
- 각 provider마다 product OCR target과 upload OCR path를 모두 실행한다.
- 결과에는 `external_api_called`, `duration_ms`, `timeout_seconds`, `product_evidence_statuses`, `upload_evidence_statuses`, evidence reason, `provider_error_code`, `provider_error_category`, `provider_evidence_error_codes`를 남긴다.
- `FITCHECK_OCR_PROVIDER_MATRIX_REQUIRE_SUCCESS=1`이면 `completed` 또는 `completed_empty` provider가 하나도 없을 때 실패 gate가 된다.
- `FITCHECK_OCR_PROVIDER_MATRIX_REQUIRE_TEXT=1`이면 실제 text evidence가 있는 `completed` provider가 없을 때 실패 gate가 된다.
- `FITCHECK_OCR_PROVIDER_MATRIX_IMAGE_PATH`를 설정하면 기본 1x1 PNG 대신 실제 OCR 테스트 이미지를 사용할 수 있다.

현재 환경 결과:

- opt-in이 없으면 `skipped`를 반환한다.
- `fixture_boundary`는 product/upload evidence 모두 `not_connected`이고 `OCR_PROVIDER_NOT_CONNECTED`로 정리된다.
- `tesseract_cli`는 현재 local executable이 없어 product/upload evidence 모두 `configuration_required`, `OCR_TESSERACT_CLI_NOT_FOUND`로 정리된다.
- `FITCHECK_OCR_PROVIDER_MATRIX_REQUIRE_SUCCESS=1`이면 현재 환경에서는 exit 1 failure gate가 된다.

### Agent create/reply 빈 입력 validation gate 추가

Codex식 agent는 고정 parser 없이 원문 대화에서 바로 tool을 고를 수 있어야 하지만, 아무 사용자 의도/URL/이미지도 없는 빈 요청은 agent run으로 만들면 안 된다. 기존 schema는 모든 create/reply 필드가 optional이라 `{}`가 route validation을 통과할 수 있었고, runtime/store의 기본 goal 문자열이 실제 사용자 입력처럼 보일 위험이 있었다.

변경:

- `AgentRunCreateRequest`에 model validator를 추가해 `goal`, `free_text`, `candidate_url`, `attachments` 중 최소 하나가 없으면 422 validation error가 나게 했다.
- `AgentRunReplyRequest`에도 `message`, `answers`, `attachments` 중 최소 하나가 없으면 422 validation error가 나게 했다.
- 공백뿐인 `goal`, `free_text`, `message`는 입력으로 인정하지 않는다.
- 이 validation은 LLM configuration 확인이나 run 생성 전에 끝나므로, 빈 요청이 외부 LLM/API call로 이어지지 않는다.
- Runtime `start_run()`을 route 밖에서 직접 호출하는 경우도 같은 빈 payload를 `invalid_request` / `INITIAL_USER_INPUT_REQUIRED`로 거부하고, run store에 저장하거나 LLM client를 호출하지 않는다.
- Runtime `reply()`을 route 밖에서 직접 호출하는 경우도 빈 payload를 `invalid_request` / `REPLY_USER_INPUT_REQUIRED`로 거부하고, 기존 run의 `input.user_replies`, `user_reply` trace, LLM request count를 변경하지 않는다.

검증:

- `verify_backend_infrastructure.py`가 `{}` create, 공백 create, `{}` reply, 공백 reply의 Pydantic validation error를 직접 확인한다.
- `verify_production_agent_runtime.py`가 빈 `start_run({})`에서 LLM request가 0회이고 run store가 비어 있는지 확인한다.
- `verify_production_agent_runtime.py`가 빈 `reply()` 직접 호출에서 LLM request가 0회이고 기존 waiting run state가 변하지 않는지 확인한다.
- `verify_http_api_smoke.py`가 실행 중인 서버에서 빈 `POST /api/agent/runs`와 빈 `POST /api/agent/runs/{run_id}/reply`가 422로 끝나는지 확인한다.
- 기존 invalid type validation도 유지해 `attachments`/`answers` type error와 빈 turn validation을 구분한다.

### Invalid candidate key structured tool failure 추가

LLM tool-calling loop에서는 모델이 검색 observation에 없는 `candidate_key`를 잘못 선택할 수 있다. 이 상황이 Python `KeyError`나 generic `TOOL_EXECUTION_FAILED`로 새면 모델이 어떤 후보 key로 회복해야 하는지 알기 어렵고, trace도 product-state 문제와 backend crash를 구분하지 못한다.

변경:

- `fetch_product_page`, `classify_product_assets`, `extract_size_material_info`, `normalize_product_spec`, `score_candidate_match`가 존재하지 않는 후보 key를 구조화된 `CANDIDATE_NOT_FOUND` failed tool output으로 반환하게 했다.
- 결과에는 `available_candidate_keys`와 `next_tool_suggestions`를 포함한다.
- 검색 후보가 있으면 유효한 후보 key로 `fetch_product_page`를 재시도하라는 suggestion을 반환한다.
- 검색 후보가 아직 없으면 기존 discovery suggestion을 반환한다.
- 같은 잘못된 tool call을 반복하면 기존 `REPEATED_FAILED_TOOL_CALL` guardrail이 계속 적용된다.

검증:

- `verify_production_agent_runtime.py`에 `FakeInvalidCandidateThenCorrectedLlmClient`를 추가했다.
- 이 verifier는 모델이 먼저 `missing_candidate`로 `fetch_product_page`를 호출한 뒤 `CANDIDATE_NOT_FOUND` tool output을 받고, 다음 turn에서 `web_test_candidate`로 회복해 최종 report까지 완료하는지 확인한다.
- 같은 검증에서 invalid candidate failure가 `TOOL_EXECUTION_FAILED`로 기록되지 않는지, 다음 LLM request에 `CANDIDATE_NOT_FOUND` output이 포함되는지도 확인한다.

### Max agent turns guardrail trace 추가

LLM이 계속 tool call만 반환하고 final report, `waiting_for_user`, configuration gate, failed guardrail 중 어디로도 수렴하지 않으면 runtime은 `max_turns`에서 종료한다. 기존 구현은 top-level `MAX_AGENT_TURNS_EXCEEDED` error만 남기고 guardrail trace를 남기지 않았으며, 마지막 loop의 `pending_tool_outputs`가 남을 수 있었다.

변경:

- `MAX_AGENT_TURNS_EXCEEDED` 발생 시 `stop_max_agent_turns_exceeded` guardrail trace를 추가한다.
- top-level error에 `max_turns` 값을 포함한다.
- 종료 직전에 `state.pending_tool_outputs`를 비운다.
- 이는 OpenAI function calling flow에서 tool output은 다음 model request로 넘기는 항목이라는 점을 기준으로, 더 이상 진행하지 않을 pending output을 명확히 정리하기 위한 것이다.

검증:

- `FakeMaxTurnsToolLoopLlmClient`를 추가했다.
- `verify_production_agent_runtime.py`가 `max_turns=2`에서 LLM request 2회 후 `MAX_AGENT_TURNS_EXCEEDED`로 실패하는지 확인한다.
- 같은 검증에서 `stop_max_agent_turns_exceeded` trace, 빈 `pending_tool_outputs`, LLM response trace count를 확인한다.

### LLM response failure pending output cleanup 추가

Responses-style tool-calling loop에서는 backend tool output이 `function_call_output` item으로 다음 LLM request에 전달된다. 따라서 tool 실행은 성공했지만 그 다음 LLM request가 timeout/exception으로 실패하는 경우, runtime에 남은 `pending_tool_outputs`를 그대로 두면 UI/SSE/API 소비자가 아직 재개 가능한 tool output처럼 오해할 수 있다.

변경:

- LLM client `create_response()`가 예외를 던지면 run을 `LLM_RESPONSE_FAILED`로 실패 처리한다.
- 실패 직전에 `state.pending_tool_outputs`를 비운다.
- failed `llm_response` trace의 input/output에 `pending_tool_output_count`를 기록한다.
- `stop_llm_response_failed` guardrail trace를 추가해 provider 실패와 agent guardrail 종료를 구분한다.

검증:

- `FakeToolThenLlmFailureClient`를 추가했다.
- 첫 번째 LLM request는 `load_closet_memory` tool call을 반환하고, tool output을 넘기는 두 번째 LLM request에서 timeout을 발생시킨다.
- `verify_production_agent_runtime.py`가 `LLM_RESPONSE_FAILED`, LLM request 2회, 빈 `pending_tool_outputs`, failed `llm_response` trace, `stop_llm_response_failed` guardrail trace, `pending_tool_output_count=1`을 확인한다.

### Multiple function calls in one response verifier 추가

OpenAI function calling 문서는 model response에 zero, one, multiple tool/function call이 들어올 수 있으므로 여러 호출을 가정하고 처리하라고 설명한다. 기존 production verifier는 대부분 한 LLM 응답에 tool call 하나만 반환하는 fake client로 구성되어 있어, 같은 응답의 다중 `function_call` 처리와 `call_id`별 output 반환 경계가 약했다.

변경:

- `FakeMultiToolCallLlmClient`를 추가했다.
- 첫 LLM 응답이 `load_closet_memory`와 `search_web_candidates` 두 개의 `function_call`을 함께 반환한다.
- runtime이 두 tool을 모두 backend-owned handler로 실행하고 observation/trace를 남기는지 확인한다.
- 두 번째 LLM request의 input이 두 개의 `function_call_output` item을 포함하고, 각각 `call_multi_closet`, `call_multi_search`와 연결되는지 확인한다.
- `tool_call` trace input, `tool_output` trace output, observation, 다음 LLM request의 `function_call_output`이 모두 같은 `call_id`를 보존하는지 확인한다.
- 실제 runtime `tool_output` trace를 `AgentStateEventResponse.latest_trace` payload로 검증해 SSE schema serialization 후에도 `call_id`가 유지되는지 확인한다.
- 첫 `llm_response` trace의 `tool_call_count=2`를 확인한다.

이 검증은 agent가 항상 parallel/다중 tool call을 해야 한다는 뜻이 아니다. 모델이 그런 응답을 냈을 때 FastAPI runtime이 OpenAI-compatible tool-calling contract대로 안전하게 처리하는지 확인하는 harness다.

### Local chat-completions multi tool_calls adapter verifier 강화

Com2uS 방향상 production architecture는 cloud OpenAI Responses API만 전제하지 않고 local OpenAI-compatible `/v1/chat/completions` tool calling도 지원해야 한다. 이전 local adapter verifier는 chat-completions 응답의 tool call 하나만 확인했기 때문에, local model이 한 턴에 여러 `tool_calls`를 반환하는 경우의 변환 경계가 약했다.

변경:

- `verify_local_llm_tool_adapter.py`의 fake chat-completions 응답이 `load_closet_memory`, `search_web_candidates` 두 개의 `tool_calls`를 반환한다.
- adapter가 두 호출을 Responses-like `function_call` item 두 개로 정규화하는지 확인한다.
- 두 번째 model request에서 두 tool output이 각각 `role=tool` 메시지로 들어가고, `tool_call_id`가 `call_1`, `call_2`로 보존되는지 확인한다.
- assistant history에 원래 `tool_calls` 배열 2개가 유지되는지 확인한다.
- chat-completions tool schema 변환에서 두 번째 tool schema도 보존되는지 확인한다.

이는 local LLM에서도 모델이 tool을 선택하고 backend가 실행/기록한다는 agentic boundary를 유지하기 위한 adapter harness다.

### Deprecated chat-completions function_call adapter 추가

OpenAI Chat Completions API의 `message.function_call`은 deprecated이고 `tool_calls`로 대체되었다. 하지만 일부 local/OpenAI-compatible server는 여전히 `tool_calls` 대신 `message.function_call`만 반환할 수 있다. 기존 local adapter는 이 경우 output tool call을 만들지 않아 runtime에서 빈 LLM 응답처럼 보일 수 있었다.

변경:

- `_chat_completion_to_responses_like()`가 `message.function_call`을 synthetic call id가 있는 Responses-like `function_call` item으로 정규화한다.
- `_assistant_message_for_history()`가 legacy `function_call` history를 보존한다.
- `_build_chat_messages()`가 synthetic call id에 대응하는 tool output을 deprecated `role=function`, `name=<function_call.name>` 메시지로 제출한다.
- `verify_local_llm_tool_adapter.py`에 `FakeLegacyFunctionCallRequests`를 추가해 legacy function call name/arguments, synthetic call id, legacy function output follow-up message를 확인한다.

### Nested chat-style function.arguments extractor 보강

OpenAI Chat Completions tool call은 `tool_calls[*].function.name`과 `tool_calls[*].function.arguments`에 tool 이름과 JSON argument 문자열을 담는다. FitCheck local adapter는 이 구조를 Responses-like `function_call` item으로 정규화하지만, OpenAI-compatible server나 테스트 client가 chat-style nested item을 runtime `output`에 직접 넘기는 경우도 있을 수 있다.

기존 `_extract_tool_calls()`는 nested `function.name`은 읽었지만 nested `function.arguments`는 읽지 않아, 이런 형태가 들어오면 arguments가 `{}`로 떨어질 수 있었다.

변경:

- `_extract_tool_calls()`가 `type=function` item도 model-selected tool call로 인정한다.
- `item.arguments`가 없으면 `item.function.arguments`를 읽는다.
- `call_id`, `id`, `tool_call_id` 순서로 call id를 보존한다.
- `FakeNestedChatStyleToolCallLlmClient`를 추가해 nested `function.arguments`의 search query가 observation, trace, 다음 `function_call_output`까지 보존되는지 확인한다.

### Responses reasoning context forwarding verifier 추가

OpenAI Responses reasoning/tool-calling 흐름에서는 tool call과 함께 반환된 reasoning context를 다음 요청의 tool output과 함께 유지해야 한다. 기존 runtime은 `function_call_output`만 다음 LLM 요청에 보내고, `reasoning` output item은 버렸다. 그러면 reasoning model이 직전 tool 선택의 내부 context를 잃거나, provider별 Responses 호환 구현에서 다음 요청 계약이 약해질 수 있다.

변경:

- `_response_context_items_for_next_llm()`를 추가해 Responses-style `reasoning` output item을 다음 LLM 요청에 보존한다.
- `llm_response` trace output에 `reasoning_context_item_count`를 기록한다.
- local chat-completions adapter는 Responses 전용 `reasoning` item을 user JSON message로 변환하지 않고 건너뛴다.
- `FakeReasoningContextToolCallLlmClient`를 추가해 `reasoning` item과 `search_web_candidates` tool call이 같은 응답에 들어오는 케이스를 검증한다.
- `verify_production_agent_runtime.py`가 두 번째 LLM request에 `reasoning` item과 `function_call_output`이 함께 들어가는지 확인한다.
- `verify_local_llm_tool_adapter.py`가 local chat messages에 `reasoning` item id가 누출되지 않는지 확인한다.

### Optional parser argument contract 보강

사용자가 지적한 Codex식 agent 흐름에서는 자연어 입력을 먼저 고정 parser로 통과시키는 것이 필수 조건이 아니다. `parse_user_goal`은 선택 가능한 정규화 helper여야 하며, 모델이 원문 입력을 이미 받은 상태에서 `free_text`만 넘기거나 빈 args로 호출해도 run context를 통해 동작해야 한다. 기존 schema는 `goal`을 required로 둬서, 모델이 `parse_user_goal({"free_text": "..."})`만 호출하면 backend validation에서 실패할 수 있었다.

변경:

- `parse_user_goal` tool schema의 required args를 `[]`로 변경했다.
- handler는 기존처럼 args에 없는 `goal`, `free_text`, `candidate_url`을 현재 run context에서 fallback으로 읽는다.
- `verify_backend_infrastructure.py`가 `parse_user_goal` schema의 required args가 비어 있는지, `free_text` 단독 호출과 `{}` context fallback 호출이 성공하는지 확인한다.
- `FakeOptionalParserFreeTextOnlyLlmClient`를 추가해 production loop에서 모델이 `parse_user_goal`을 `free_text`만으로 호출한 뒤 `search_web_candidates`로 이어지는지 확인한다.

### Claimless final report grounding 차단

기존 `report_grounding.py`는 관측되지 않은 숫자, URL, 소재 claim을 막았지만, final text에 그런 claim이 아예 없으면 `score_candidate_match` 또는 `write_purchase_report` observation이 있다는 이유로 통과할 수 있었다. 이는 LLM이 `write_purchase_report`를 실행한 뒤에도 "좋아 보입니다" 같은 claim 없는 답변을 내는 경로를 허용한다.

변경:

- `validate_report_grounding()`은 `grounding_evidence`가 비어 있으면 `report_contains_no_grounded_claims`로 실패한다.
- 성공하는 fake final text는 관측된 score claim을 포함하도록 수정했다.
- `verify_backend_infrastructure.py`가 claim 없는 final text를 invalid로 검증한다.

### Product identity claim grounding 추가

사용자가 처음 지적한 하드코딩 후보명 문제는 숫자/URL/소재 grounding만으로는 완전히 막을 수 없다. 예를 들어 report가 관측된 `전체 81점`을 포함하면서도 `**고피플 샤인 피그먼트 반팔티 차콜**` 같은 관측되지 않은 상품명을 함께 쓰면 기존 validator는 숫자 claim 때문에 통과할 수 있었다.

변경:

- `report_grounding.py`가 observation의 `candidate_key`, `brand`, `name`, `title`, `product_name` 계열 field를 identity evidence로 수집한다.
- report의 markdown bold, quote, numbered-list item에 들어간 product-like identity claim을 추출한다.
- identity claim이 관측된 identity evidence와 매칭되지 않으면 `report_contains_unobserved_identity_claims`로 실패한다.
- `GroundingValidationResponse`에 `suspicious_identity_claims` field를 추가했다.
- `verify_backend_infrastructure.py`가 `**테스트 후보 반팔티**`처럼 관측된 상품명은 통과하고, `**고피플 샤인 피그먼트 반팔티 차콜**`처럼 관측되지 않은 상품명은 실패하는지 확인한다.

### Fit/color/design style claim grounding 추가

FitCheck의 핵심 비교 대상은 상품명뿐 아니라 핏, 색상, 디자인, 워싱 같은 의류 속성이다. 기존 grounding은 상품명 identity까지 막아도, LLM final이 관측되지 않은 `그래픽`, `차콜`, `피그먼트`, `오버핏` 같은 속성을 덧붙이는 경로를 모두 막지는 못했다.

변경:

- `report_grounding.py`가 `오버핏`, `루즈핏`, `와이드`, `차콜`, `데님`, `피그먼트`, `워싱`, `그래픽`, `포켓`, `무지`, `스트라이프` 등 구체적인 style claim을 canonical label로 추출한다.
- style evidence는 `.arguments` 경로를 제외하고 tool result/observation value에서만 수집한다. 검색 query에 들어간 사용자 희망어만으로 후보 속성을 grounding하지 않기 위한 제한이다.
- 관측되지 않은 style claim은 `report_contains_unobserved_style_claims`로 실패한다.
- `GroundingValidationResponse`에 `suspicious_style_claims` field를 추가했다.
- `verify_backend_infrastructure.py`가 관측된 `차콜/피그먼트/오버핏` claim은 통과하고, 관측되지 않은 `그래픽` claim은 실패하는지 확인한다.

### Tool result common schema 보강

조사 근거:

- FastAPI 공식 문서는 Pydantic model을 response model 또는 additional response model로 선언하면 JSON Schema를 생성해 OpenAPI의 올바른 위치에 넣고 `$ref`로 참조한다고 설명한다.
- OpenAI Responses API reference는 function tool call output의 `output`이 애플리케이션 코드가 생성한 결과이며 string 또는 content list가 될 수 있다고 설명한다. FitCheck는 이 output payload에 JSON으로 tool result를 넣으므로, backend API가 UI/SSE 소비자에게 동일한 공통 result 계약을 노출해야 한다.

변경:

- `ToolResultErrorResponse`: tool result error의 `code`, `message`, `details` 공통 schema.
- `ToolSuggestionResponse`: backend guidance의 `tool`, `arguments`, `reason` 공통 schema.
- `ToolResultResponse`: tool result의 `status`, `error`, provider/retry/error metadata, `missing_fields`, `next_tool_suggestions` 공통 schema.
- `ToolObservationResponse.result`를 generic `dict`에서 `ToolResultResponse`로 바꾸고, observation top-level provider/error/retry metadata도 schema field로 노출했다.

검증:

- `verify_backend_infrastructure.py`가 `ToolObservationResponse.result`의 typed validation, `ToolResultResponse` OpenAPI schema, `ToolObservationResponse.result -> ToolResultResponse` `$ref`, provider error/result suggestion field를 확인한다.
- `verify_http_api_smoke.py`가 실행 중인 서버의 `/openapi.json`에서도 `ToolResultErrorResponse`, `ToolResultResponse`, `ToolSuggestionResponse`와 핵심 result/observation properties를 확인한다.

### Search result schema 보강

조사 근거:

- FastAPI 공식 문서는 response model이 반환 데이터를 검증하고 response용 JSON Schema를 OpenAPI path operation에 추가한다고 설명한다.
- OpenAI Responses API reference는 function tool call output의 `output`이 애플리케이션 코드가 생성한 결과라고 설명한다. FitCheck search tool output은 다음 LLM 판단과 UI/SSE 표시의 근거이므로, product-like 후보와 rejected content 후보의 경계를 schema로 드러내야 한다.

변경:

- `SearchCandidateResponse`: `candidate_key`, `name`, `source_url`, `candidate_type`, `commerce_score`, `commerce_signals`, `content_risk_flags`, `missing_fields` 등 search candidate 공통 field.
- `RejectedSearchCandidateResponse`: 콘텐츠성/불확실 결과의 rejected summary field.
- `ToolResultResponse`에 `query`, `candidate_filter_policy`, `search_result_count`, `product_like_candidate_count`, `candidate_count`, `candidates`, `rejected_candidate_count`, `rejected_candidates`, `search_deferred`, `ask_user_allowed`를 추가했다.

검증:

- `verify_backend_infrastructure.py`가 typed `SearchCandidateResponse`/`RejectedSearchCandidateResponse` validation과 `ToolResultResponse.candidates`/`rejected_candidates` OpenAPI `$ref`를 확인한다.
- `verify_http_api_smoke.py`가 실행 중인 `/openapi.json`에서도 search candidate schema와 result properties를 확인한다.

## 3. 아직 harness인 부분

- 기본 provider 상태는 `cached_harness_search`일 수 있지만 production agent search는 `FITCHECK_ALLOW_CACHED_AGENT_SEARCH=1` 없이는 cached 후보를 반환하지 않는다.
- 기본 OCR은 `fixture_ocr_boundary`이다.
- Brave/Jina live search는 API key가 있어야 실제 provider smoke가 가능하다.
- SearXNG live search는 self-hosted/public instance base URL이 있어야 실제 provider smoke가 가능하고, 해당 instance에서 JSON output이 활성화되어 있어야 한다.
- DuckDuckGo HTML provider는 no-key best-effort라 외부 HTML 구조와 차단 상태에 의존한다. 현재 환경에서는 challenge page가 관측되어 `DUCKDUCKGO_HTML_CHALLENGE`로 실패한다.
- Tesseract provider는 local CLI와 language data가 없으면 실행되지 않는다. OCR provider matrix는 이 상태를 `configuration_required`로 드러내지만, positive OCR evidence를 만들지는 않는다.
- 후보 source page의 OCR target 이미지는 local cache로 다운로드되지만, 기본 OCR은 fixture boundary이고 실제 OCR은 `FITCHECK_OCR_PROVIDER=tesseract_cli`와 local dependency가 있을 때만 동작한다.
- image classifier는 아직 URL/label heuristic이다. 이미지 픽셀 기반 vision classifier는 연결되어 있지 않다.
- 기준 옷 memory는 현재 자유 텍스트와 fixture baseline 중심이다. 실제 closet DB/auth/profile memory가 없다.
- trace/event/tool observation schema, 공통 tool result schema, search candidate schema는 도입됐지만, fetch/OCR/score/report별 상세 result schema는 아직 확장 여지가 있다.

## 4. 다음 개선 후보

1. 실제 API key/base URL이 있는 환경에서 search provider matrix positive 결과를 수집한다.
2. DuckDuckGo challenge가 잦은 환경에서는 Serper 또는 Playwright provider를 추가할지 비교한다.
3. live OCR dependency가 있는 환경에서 Tesseract language data까지 통과하는 positive smoke result를 수집한다.
4. provider error code/category를 프론트 trace panel에서 badge/filter로 렌더링한다.
## 2026-06-09 Source planner and Musinsa Markdown size table parsing

## 2026-06-09 OCR skipped metadata and material terms

Problem found in live API testing:

- Tesseract provider correctly returned `skipped` for OCR target assets without `local_path`, but provider metadata promoted those skipped evidence items into `OCR_LOCAL_ASSET_PATH_REQUIRED` error codes.
- This made UI/trace output look like the OCR provider failed even when usable OCR evidence was attempted or completed for other assets.
- Product descriptions sometimes contain material family hints such as Supima/cotton/polyester without exact composition percentages. Storing those as `material={"cotton":100}` would invent a composition claim.

Changes:

- `provider_error_metadata_from_result` no longer treats skipped OCR evidence as provider error evidence.
- `_normalize_live_product_spec` and OCR merge now store descriptive material family hints in `material_terms`.
- `material` remains reserved for observed composition percentages.
- `material_terms` can appear in evidence coverage, but `material` remains missing if no composition was observed.
- Material-term extraction was kept conservative to reduce false positives from short Korean syllables such as `면`, `울`, and `폴리`.

Verification:

- `python -B backend_harness\verify_backend_infrastructure.py`
- `python -B backend_harness\verify_production_agent_runtime.py`
- Live production-agent route:
  - endpoint: `POST /api/agent/runs`
  - run id: `run_c3431403c346`
  - input: `https://www.musinsa.com/products/1902619 이거 L 사이즈 구매했었는데, 비슷한 핏, 재질의 티셔츠 찾아봐`
  - status: `waiting_for_user`
  - final report: not emitted
  - OCR metadata: `provider_error_code=null`, `provider_evidence_error_codes=[]`; skipped assets remain visible in evidence status counts but are not provider failures.
  - Normalized candidate examples:
    - `https://www.musinsa.com/products/5223358`: `material_terms=["cotton"]`, `material={}`, `price=null`, missing `material, price`.
    - `https://www.musinsa.com/products/2056565`: `material_terms=["cotton","polyester"]`, `material={}`, `price=null`.
    - `https://www.musinsa.com/products/999602`: `material={"cotton":100}`, `material_terms=["cotton"]`, `price=null`, missing `price`.

Remaining gaps:

- Price extraction still avoids selecting a price when Jina Reader text includes multiple related-product prices.
- Next work should extract current-product price only from stronger source structures such as JSON-LD/meta/embedded product state tied to the current product id.

Problem found in live API testing:

- The agent could classify a user-supplied Musinsa URL as the owned baseline source, but product measurements from Jina Reader Markdown pipe tables were not reliably normalized.
- Jina Reader returns Musinsa size charts in flattened Markdown such as `| 총장 | 어깨너비 | 가슴단면 | 소매길이 | ... | 69 | 54.5 | 58 | 24 |`.
- The previous measurement parser only handled adjacent label/value patterns such as `총장 69`.
- A first pipe-table parser pass revealed another live bug: descriptive cells such as `반소매티셔츠 사이즈 측정법 * 내 사이즈 L` could be mistaken for a `sleeve` header, and product names such as `일반기장 2팩` could be mistaken for `length=2`.

Changes:

- `_parse_measurements` now merges adjacent label/value extraction with Markdown pipe-table extraction.
- Pipe-table extraction only treats short header-like cells as measurement labels.
- Numeric values are accepted only when they are plausibly garment measurements in the `10..200` range.
- Regression checks cover Musinsa/Jina Markdown size table rows, measurement-guide prefix text before the header row, body stats such as `175cm 65kg L 착용`, and product pack counts such as `일반기장 2팩`.
- `AGENTS.md` now records `plan_context_and_sources`, source/reference resolution, and Markdown table parsing as required implementation guidance.

Verification:

- `python -B backend_harness\verify_backend_infrastructure.py`
- `python -B backend_harness\verify_production_agent_runtime.py`
- Live production-agent route:
  - endpoint: `POST /api/agent/runs`
  - run id: `run_4c4453e2ffc9`
  - input: `https://www.musinsa.com/products/1902619 이거 L 사이즈 구매했었는데, 비슷한 핏, 재질의 티셔츠 찾아봐`
  - status: `completed`
  - model-selected/backend-owned sequence: `plan_context_and_sources -> load_closet_memory -> enrich_closet_item_from_web -> search_web_candidates -> fetch_product_page x5 -> classify_product_assets x5 -> extract_size_material_info x5 -> normalize_product_spec x5 -> score_candidate_match -> write_purchase_report`
  - baseline URL was stored under `source_refs.baseline`, fetched through `enrich_closet_item_from_web`, and skipped separate baseline search with `search_status=skipped_user_supplied_baseline_url`.
  - baseline measurements normalized to `length=72.0`, `shoulder=48.5`, `chest=54.5`, `sleeve=23.0`.
  - Brave search expanded to product detail query `site:musinsa.com/products 무신사 티셔츠 L 오버핏 박시핏 탄탄한 면 소재` and returned 5 product candidates.
  - Candidate examples normalized:
    - `https://www.musinsa.com/products/5223358`: `length=69.0`, `shoulder=54.5`, `chest=58.0`, `sleeve=24.0`.
    - `https://www.musinsa.com/products/2056565`: `length=68.0`, `shoulder=51.0`, `chest=52.0`, `sleeve=21.0`.
    - `https://www.musinsa.com/products/3075259`: `length=72.8`, `shoulder=56.5`, `chest=57.5`, `sleeve=23.0`.
    - `https://www.musinsa.com/products/999602`: `length=68.5`, `shoulder=49.5`, `chest=53.0`, `sleeve=23.0`, `material={"cotton":100}`.

Remaining gaps:

- Material and price still remain missing for several Musinsa candidates, so the grounded report should not be presented as a confident recommendation.
- Next work should improve price extraction, material sentence parsing, and the OCR local asset path boundary that still produces `OCR_LOCAL_ASSET_PATH_REQUIRED` for some fetched assets.

## 2026-06-28 Fit claim grounding and SDK event ownership

Problem found while reviewing prompt/context and live product reports:

- Size labels such as `L` were documented as weak hints, but final report grounding did not have a separate claim channel for affirmative fit claims.
- A report could include an observed numeric score and still say "similar fit", "true-to-size", or "fits well" without garment measurement evidence.
- `/api/agent/runs/{run_id}/sdk-events` projected tool events with `backend_owned=false` unless provider-boundary flags were present, even when the event was paired to a persisted backend observation.

Changes:

- `report_grounding.py` now extracts `fit` claims separately from style claims.
- Unsupported similar-fit, close-measurement, true-to-size, and fits-well claims fail with `report_contains_unobserved_fit_claims`.
- Negative/uncertain text such as "fit evidence is missing" is allowed and does not become an affirmative fit claim.
- Close-fit claims can be grounded by observed `measurement_deltas` when size evidence is close enough, or by explicit observed source text for true-to-size style phrases.
- `GroundingValidationResponse`, OpenAPI checks, HTTP smoke, and backend verifier now expose and verify `suspicious_fit_claims`.
- SDK-like event projection now marks tool events as `backend_owned=true` when their `call_id` is paired with a persisted backend observation.

Verification:

- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`

## 2026-06-29 Compact revalidation readiness

Problem:

- Compact stdout now included the non-secret `revalidation_inputs` bundle, but controllers still had
  to infer whether the bundle was actually runnable.
- Dry-run compact output naturally has no aggregate readback task id, while live and stored-evidence
  validation compact output should be immediately reusable for later revalidation.

Changes:

- `agents_sdk_smoke_matrix.py` now adds `revalidation_ready`,
  `revalidation_missing_fields`, and `revalidation_required_fields` beside `revalidation_inputs` in
  both live/dry-run `matrix_run_summary` and validation `readback_validation_summary`.
- Required non-secret fields are `validate_matrix_readback_task_id`, `validate_matrix_json`,
  `client_id`, and `base_url`.
- The readiness flag is only a CLI-input completeness check for stored-evidence revalidation. It is
  not a provider-connectivity claim and does not prove the referenced task will validate.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced OpenAI provider probe completed against `/v1/models` with HTTP 200 and diagnostic
  id `70`.
- `.env`-sourced stored-evidence compact validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-validation-readiness-20260629.json`
  with aggregate `check_count=61`, published scenario `check_count=57`,
  `revalidation_ready=true`, and `revalidation_missing_fields=[]`.
- Dry-run compact validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-dry-compact-readiness-20260629.json`
  with `revalidation_ready=false` and
  `revalidation_missing_fields=["validate_matrix_readback_task_id"]`.

## 2026-06-29 Compact revalidation CLI args

Problem:

- After `revalidation_inputs`, controllers still had to manually map the summary object to
  `agents_sdk_smoke_matrix.py` CLI flags.
- That extra mapping is easy to drift from the script's actual contract and makes repeated
  stored-evidence checks harder to automate.

Changes:

- Compact live/dry-run `matrix_run_summary` and validation `readback_validation_summary` now expose
  `revalidation_cli_args`.
- The argv fragment includes only non-secret flags for this same script. It intentionally omits the
  Python executable, script path, API key, and caller-chosen `--output-json` path.
- Dry-run output still reports `revalidation_ready=false`; its `revalidation_cli_args` omits the
  missing aggregate task id and pairs with `revalidation_missing_fields` for explanation.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced OpenAI provider probe completed against `/v1/models` with HTTP 200 and diagnostic
  id `71`.
- `.env`-sourced stored-evidence compact validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-validation-cli-args-20260629.json`
  with aggregate `check_count=61`, published scenario `check_count=57`, `revalidation_ready=true`,
  and a 10-item `revalidation_cli_args` array.
- Re-running the script by reading only that `revalidation_cli_args` array wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-validation-cli-args-rerun-20260629.json`
  and again reported aggregate `check_count=61`, published scenario `check_count=57`, and
  `revalidation_ready=true`.
- Dry-run compact validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-dry-compact-cli-args-20260629.json`
  with `revalidation_ready=false`, missing field `validate_matrix_readback_task_id`, and no
  `--validate-matrix-readback-task-id` in `revalidation_cli_args`.

## 2026-06-29 Summary-file revalidation entrypoint

Problem:

- `revalidation_cli_args` removed most controller-side mapping, but controllers still had to parse
  the summary and launch the script themselves.
- A stored compact/full summary should be reusable as the direct input for another stored-evidence
  revalidation pass.

Changes:

- `agents_sdk_smoke_matrix.py` now accepts `--revalidate-from-summary-json <path>`.
- The script reads `revalidation_inputs` from the root object, `readback_validation_summary`, or
  `matrix_run_summary`, then calls the same existing aggregate/scenario stored-evidence validation
  path.
- The output records `revalidation_source_summary_json`, `revalidation_source_summary_key`, and
  `revalidation_source_ready` so controllers can see which summary surface drove the rerun.
- This mode does not run new live SDK/provider calls; it refetches stored harness evidence,
  read-model summaries, and raw event replay.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced OpenAI provider probe completed against `/v1/models` with HTTP 200 and diagnostic
  id `72`.
- `.env`-sourced summary-file revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-summary-revalidate-20260629.json`
  from source
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-validation-cli-args-20260629.json`.
  It reported aggregate `check_count=61`, published scenario `check_count=57`,
  `revalidation_source_summary_key=readback_validation_summary`, and
  `revalidation_source_ready=true`.

## 2026-06-29 Live guardrail summary revalidation hints

Problem:

- The new `--revalidate-from-summary-json` path worked, but compact summaries still required a
  controller to infer that command from `matrix_output_json` or from its own `--output-json` path.
- Live guardrail/tripwire traces are important evidence surfaces because they include guardrail,
  run-error, final-output, model-response, and stored read-model projection behavior.

Changes:

- Compact live/dry-run `matrix_run_summary` and validation `readback_validation_summary` now expose
  `summary_revalidation_inputs`, `summary_revalidation_ready`,
  `summary_revalidation_missing_fields`, and `summary_revalidation_cli_args`.
- `summary_revalidation_cli_args` is a non-secret argv fragment for the direct
  `--revalidate-from-summary-json` path.
- Dry-run output can expose the summary JSON path while still reporting
  `summary_revalidation_ready=false` when the embedded aggregate task id is missing.

Verification:

- `.env`-sourced live `guardrail_tripwire` matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-guardrail-tripwire-20260629.json`.
  It probed provider diagnostic id `73`, used `72` total API tokens, published scenario task
  `task_6323ecfcce75`, published aggregate task `task_f1e725b9c979`, and completed aggregate
  readback with `check_count=61`.
- `.env`-sourced summary-file revalidation of that live matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-guardrail-summary-revalidate-20260629.json`
  with aggregate `check_count=61`, published scenario `check_count=59`,
  `summary_revalidation_ready=true`, and
  `summary_revalidation_cli_args=["--revalidate-from-summary-json", "...live-guardrail-summary-revalidate-20260629.json", "--compact-output"]`.
- Dry-run compact output wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-dry-summary-revalidation-20260629.json`
  with `summary_revalidation_ready=false` and
  `summary_revalidation_missing_fields=["validate_matrix_readback_task_id"]`.

## 2026-06-29 Live scenario readback compact counts

Problem:

- Live compact matrix output exposed aggregate matrix readback counts, but scenario evidence-holder
  readback depth still required opening the full matrix JSON.
- Controllers need a quick way to see whether each published scenario passed generic trace-ingest
  readback, scenario projection validation, and raw event replay validation.

Changes:

- `matrix_run_summary` now includes `scenario_publish_readback_count`,
  `scenario_publish_readback_status_by_scenario`, `scenario_generic_readback_check_counts`,
  `scenario_projection_check_counts`, `scenario_event_replay_check_counts`,
  `scenario_total_readback_check_counts`, and `scenario_readback_task_bindings`.
- Dry-run compact output returns empty maps and `scenario_publish_readback_count=0`.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live `tool` matrix with current code wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-current-scenario-counts-tool-20260629.json`.
  It probed provider diagnostic id `75`, used `321` total API tokens, published scenario task
  `task_4a0e4e6f2893`, published aggregate task `task_1e829c13d8da`, and compact stdout reported
  scenario generic/projection/event-replay check counts `41/7/9` with total `57`.
- Summary-file revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-current-scenario-counts-tool-revalidate-20260629.json`
  with aggregate `check_count=61`, published scenario `check_count=57`, and no failed scenarios.

## 2026-06-29 Validation scenario readback compact counts

Problem:

- Live `matrix_run_summary` exposed scenario generic/projection/event-replay readback counts, but
  validation `readback_validation_summary` still required opening nested
  `published_scenario_readback_validation.results` for the same stage-level detail.

Changes:

- `published_scenario_readback_summary` now includes `status_by_scenario`,
  `generic_check_counts`, `projection_check_counts`, `event_replay_check_counts`,
  `total_check_counts`, and `task_bindings`.
- `readback_validation_summary` mirrors those maps as
  `published_scenario_status_by_scenario`, `published_scenario_generic_check_counts`,
  `published_scenario_projection_check_counts`, `published_scenario_event_replay_check_counts`,
  `published_scenario_total_check_counts`, and `published_scenario_task_bindings`.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced OpenAI provider probe completed against `/v1/models` with HTTP 200 and diagnostic
  id `76`.
- `.env`-sourced summary-file revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-validation-stage-counts-20260629.json`
  from source
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-current-scenario-counts-tool-20260629.json`.
  It reported aggregate `check_count=61`, published scenario `check_count=57`,
  `published_scenario_generic_check_counts={"tool": 41}`,
  `published_scenario_projection_check_counts={"tool": 7}`, and
  `published_scenario_event_replay_check_counts={"tool": 9}`.

## 2026-06-29 Scenario readback failed-check compact maps

Problem:

- Compact summaries exposed scenario readback check counts, but a controller still had to open full
  nested results to see which generic, projection, or event-replay checks failed.

Changes:

- Live `matrix_run_summary` now includes `scenario_generic_readback_failed_checks`,
  `scenario_projection_failed_checks`, `scenario_event_replay_failed_checks`, and
  `scenario_readback_failed_checks`.
- Validation `readback_validation_summary` now includes `published_scenario_generic_failed_checks`,
  `published_scenario_projection_failed_checks`, `published_scenario_event_replay_failed_checks`,
  and `published_scenario_stage_failed_checks`.
- The full verifier's broken event-replay fixture confirms that a missing status-update replay
  check appears in the compact failed-check maps.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced OpenAI provider probe completed against `/v1/models` with HTTP 200 and diagnostic
  id `77`.
- `.env`-sourced summary-file revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-validation-failed-check-maps-20260629.json`
  with aggregate `check_count=61`, published scenario `check_count=57`, empty failed-check maps for
  the passing `tool` scenario, and the new failed-check map fields present in compact output.

## 2026-06-28 Trace ingest action summary for SDK controllers

Problem:

- `trace_ingest.py` wrote useful typed evidence, but its CLI output was only raw `actions` in
  dry-run mode or raw `results` in execution mode.
- External SDK-style controllers had to scan the full payload to know whether the trace would
  create model-response, tool-call, final-output, phase, guidance, or other evidence records.
- That made trace ingestion less ergonomic than the existing `/sdk-events?summary=true` and
  `/client-transcript?summary=true` read models.

Changes:

- `trace_ingest.py` now includes `summary.source=trace_ingest_action_summary` in both dry-run and
  execution output.
- The summary includes action count, method counts, endpoint counts, artifact kind counts,
  tool-call name counts, observation tool counts, status counts, call ids, and an explicit claim
  boundary that task evidence and SDK read models remain authoritative.
- Execution output also includes `result_summary.source=trace_ingest_result_summary` with stored
  artifact ids, observation ids, tool-call primary keys, returned status counts, and call ids.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist now verify/document the action/result summary contract.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Actual FastAPI harness API:
  - `GET /api/harness/v1/health`, `/ready`, and `/capabilities` succeeded for client
    `codex-trace-ingest-summary-20260628`.
  - Created evidence-holder task `task_804697eccb7f`.
  - Directly executed
    `.agents/skills/agentic-harness-loop/scripts/trace_ingest.py --client-id codex-trace-ingest-summary-20260628 --task-id task_804697eccb7f --trace-json harness_outputs/sdk-live-smoke/agents-sdk-tool-smoke.json`.
  - The CLI summary reported `action_count=8`, `artifact_count=1`, `tool_call_count=1`,
    `observation_count=6`, `observation_tool_counts.agent_model_response=2`,
    `observation_tool_counts.agent_final_output=1`, and the live tool call id.
  - `/tasks/task_804697eccb7f/evidence` showed one `agent_trace` artifact, one `record_signal`
    tool call, six observations, and two `agent_model_response` observations.
  - `/tasks/task_804697eccb7f/sdk-events?summary=true` returned
    `agent_model_response_count=2`, `tool_progress_count=1`, and `final_output_count=1`.
  - `/tasks/task_804697eccb7f/client-transcript?summary=true` returned
    `agent_model_response_count=2` and `tool_progress_count=1`.
  - `/sdk/context-usage` returned `apiUsage.responseCount=2`, `apiUsage.totalTokens=331`,
    `apiUsage.models=["gpt-5.4-mini"]`, and `apiUsage.evidenceRefCount=4`.

## 2026-06-28 Agentic harness helper direct execution

Problem:

- During an actual SDK/helper smoke, `.agents/skills/agentic-harness-loop/scripts/harness_api.py`
  was invoked directly.
- The file had executable bits but no shebang, so Linux/WSL fell back to interpreting the Python
  source as shell and failed before reaching the harness API.
- The skill text naturally mentions `harness_api.py providers` and similar shorthand, so this is a
  real controller usability gap even though the portable documentation also shows `python ...`.

Changes:

- Added `#!/usr/bin/env python3` to `harness_api.py`, `trace_ingest.py`,
  `agents_sdk_smoke.py`, and `scaffold_agent_harness.py`.
- `developer_harness/verify.py` now asserts the helper scripts have shebangs, remain executable on
  POSIX, and can be invoked directly for `harness_api.py --dry-run` plus helper `--help` smoke.
- Updated the loop-engineering doc and API checklist to state that POSIX direct execution is
  supported while `python <script>` remains the portable/Windows form.

Verification:

- `.venv/bin/python -m py_compile developer_harness/verify.py .agents/skills/agentic-harness-loop/scripts/harness_api.py .agents/skills/agentic-harness-loop/scripts/trace_ingest.py .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke.py .agents/skills/agentic-harness-loop/scripts/scaffold_agent_harness.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Actual FastAPI harness API with direct helper execution:
  - `.agents/skills/agentic-harness-loop/scripts/harness_api.py --client-id codex-helper-shebang-20260628 health` returned `status=ok`.
  - `.agents/skills/agentic-harness-loop/scripts/harness_api.py --client-id codex-helper-shebang-20260628 ready` returned `status=ready`.
  - `.agents/skills/agentic-harness-loop/scripts/harness_api.py --client-id codex-helper-shebang-20260628 capabilities` returned `status=ready`.
  - `.agents/skills/agentic-harness-loop/scripts/harness_api.py --client-id codex-helper-shebang-20260628 sdk-context-usage` returned `status=completed` with the explicit context-usage claim boundary.

## 2026-06-28 Generic harness SDK context usage evidence readback

Problem:

- `/api/harness/v1/sdk/context-usage` aggregated stored `agent_model_response.usage`, but SDK-style
  controllers still had to inspect full task evidence to see which task and evidence refs supported
  the aggregate usage summary.
- That made quick readback weaker than `/sdk-events?summary=true` and
  `/client-transcript?summary=true`, which already expose compact controller-check fields.

Changes:

- `developer_harness.runtime.sdk_context_usage` now exposes `sourceTaskCount`, `sourceTaskIds`,
  `eventCount`, `evidenceCounts`, and an explicit claim boundary for the harness estimate.
- `apiUsage` now exposes `taskIds`, `models`, `modelCounts`, `statusCounts`, `evidenceRefCount`,
  and preserves per-response `evidence_refs`.
- `developer_harness/verify.py`, `docs/20-general-agent-loop-engineering.md`, and the
  `agentic-harness-loop` API checklist now verify/document the stronger evidence readback.

Verification:

- `.venv/bin/python -m py_compile developer_harness/runtime.py developer_harness/verify.py developer_harness/read_model_summary.py .agents/skills/agentic-harness-loop/scripts/harness_api.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Actual FastAPI harness API:
  - `GET /api/harness/v1/health` returned `status=ok`.
  - `GET /api/harness/v1/ready` returned `status=ready`.
  - `GET /api/harness/v1/capabilities` advertised `loop_engineering_contract.read_model_summaries`.
  - Created evidence-holder task `task_0d49b094b820` for client
    `codex-sdk-context-usage-20260628`.
  - Recorded `agent_model_response` observation `observation_id=1840` with usage
    `input_tokens=321`, `output_tokens=45`, `total_tokens=366`, `cached_tokens=21`, and two
    evidence refs.
  - `GET /api/harness/v1/sdk/context-usage` returned `sourceTaskIds=["task_0d49b094b820"]`,
    `eventCount=3`, `evidenceCounts.observations=1`, `apiUsage.models=["gpt-5-mini"]`,
    `apiUsage.modelCounts.gpt-5-mini=1`, `apiUsage.statusCounts.completed=1`,
    `apiUsage.evidenceRefCount=2`, and the per-response evidence refs.
  - `GET /tasks/task_0d49b094b820/client-transcript?summary=true` returned
    `agent_model_response_count=1`.
  - `GET /tasks/task_0d49b094b820/sdk-events?summary=true` returned
    `agent_model_response_count=1`.

## 2026-06-28 Close-fit search should not run out of query variants before budget

Problem:

- Product behavior should not stop after finding a merely weak or rough clothing candidate when the
  user asks for the closest similar fit.
- The runtime already blocked weak no-close reports while `close_fit_search_budget` remained, but
  the measurement-focused query generator could exhaust its fixed query variants before the
  configured 24-attempt budget was used.
- That left a path where no new `search_web_candidates` suggestion was available even though the
  product policy said autonomous close-fit search should continue.

Changes:

- Expanded `_measurement_focused_candidate_queries` with product-detail and size-chart variants
  across Musinsa, 29CM, W Concept, EQL, SSF, and SmartStore style search constraints.
- Each generated close-fit recovery query keeps the baseline measurement labels and values in the
  query so the budget counter advances only on measurement-focused attempts.
- Added `_bounded_search_query_text` so generated recovery queries stay within the
  `SEARCH_QUERY_SCHEMA` 240-character limit.
- Added `_assert_close_fit_search_variants_cover_budget` to verify at least 24 unique
  measurement-focused queries are available and recovery still suggests a new search after the
  first static attempts have been used.

Verification:

- `../.venv/bin/python -m py_compile app/tool_registry.py verify_backend_infrastructure.py`
- `../.venv/bin/python -B verify_backend_infrastructure.py`
- `../.venv/bin/python -B verify_production_agent_runtime.py`
- `../.venv/bin/python -B verify_input_variation_matrix.py`

## 2026-06-28 Domain prompt/context harness hardening

Problem found while comparing the current prompt/context with the clothing-domain research:

- The production prompt already says unfamiliar target words must be preserved, size labels are weak
  hints, and fit-sensitive claims need garment measurement evidence.
- The harness still mostly exercised familiar categories such as t-shirt, pants, and hoodie, so it
  did not prove the open-world target rule for unseen apparel/accessory phrases.
- Live SDK-event smoke checked that `final_output` existed and was grounded, but did not fail on a
  missing suspicious-claim summary or zero citations.

Changes:

- Added an `unseen_free_form_target` input variation case using `바라클라바`/`넥워머`.
- `verify_input_variation_matrix.py` now asserts that actual model request instructions still carry
  the domain grounding guidance for unfamiliar words, weak size labels, garment measurements, and
  body/model measurement separation.
- The same verifier now checks the initial context keeps `goal`, raw `input`, and backend-owned
  progressive skill memory separated.
- `verify_live_agent_smoke.py` now fails a completed run when terminal SDK `final_output` lacks
  suspicious-claim arrays, has any non-empty suspicious array, has no citation badges, or omits the
  report/evidence endpoint links.

Verification:

- `.venv/bin/python -m py_compile backend_harness/verify_input_variation_matrix.py backend_harness/verify_live_agent_smoke.py`
- `.venv/bin/python -B backend_harness/verify_input_variation_matrix.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`
- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`
- `FITCHECK_RUN_HTTP_API_SMOKE=1 .venv/bin/python -B backend_harness/verify_http_api_smoke.py`
- `FITCHECK_RUN_LIVE_AGENT_SMOKE=1 .venv/bin/python -B backend_harness/verify_live_agent_smoke.py`
- Live smoke run `run_09bbe2a43824` completed with `external_api_called=true`, 35 correlated
  backend observations, terminal SDK `final_output`, `citation_badge_count=10`, and every
  suspicious claim array count equal to 0.

## 2026-06-28 Product SDK event cursor replay metadata

Problem found while continuing SDK/harness testing:

- Product `/api/agent/runs/{run_id}/sdk-events` supported an `after` cursor, but clients had to infer
  whether the cursor was raw event based, how many projected events were returned, and whether a
  terminal delta included the synthetic `final_output` event.
- Full-history smoke verified `final_output`, but terminal-only replay and already-caught-up replay
  were not part of the regression contract.

Changes:

- `AgentSdkEventsResponse` now exposes `projected_event_count`, `cursor_policy`,
  `terminal_event_seq`, `terminal_event_included`, and `final_output_included`.
- The route documents the cursor as `after` over raw source event sequence with exclusive semantics.
- Runtime verification now checks full fetch, terminal-delta fetch (`after=latest-1`), and caught-up
  fetch (`after=latest`) behavior.
- HTTP smoke now verifies OpenAPI schema exposure and real route behavior for terminal replay and
  caught-up replay.
- Live agent smoke now fetches the terminal delta for the live LLM run and fails if `final_output`
  does not replay in that delta.

Verification:

- `.venv/bin/python -m py_compile backend_harness/app/routes/agent.py backend_harness/app/schemas.py backend_harness/verify_production_agent_runtime.py backend_harness/verify_http_api_smoke.py backend_harness/verify_live_agent_smoke.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`
- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`
- `.venv/bin/python -B backend_harness/verify_input_variation_matrix.py`
- `GET /api/harness/v1/health`, `/ready`, and `/capabilities` with
  `X-Harness-Client-Id: fitcheck-sdk-replay-check`
- `FITCHECK_RUN_HTTP_API_SMOKE=1 .venv/bin/python -B backend_harness/verify_http_api_smoke.py`
- `FITCHECK_RUN_LIVE_AGENT_SMOKE=1 .venv/bin/python -B backend_harness/verify_live_agent_smoke.py`
- HTTP smoke run `run_73f680a35311` returned `projected_event_count=6`, terminal delta raw count
  `1`, terminal delta `final_output_included=true`, and caught-up raw/projected count `0`.
- Live smoke run `run_0e9c547eb3d7` completed with `external_api_called=true`,
  `sdk_event_projected_count=96`, terminal delta raw count `1`, terminal delta
  `final_output_included=true`, and every suspicious claim array count equal to 0.

## 2026-06-28 Generic harness SDK event replay metadata

Problem found while aligning the generic development harness with the product SDK event projection:

- `/api/harness/v1/tasks/{task_id}/sdk-events` supported an `after` cursor but did not state that
  the cursor was based on raw `harness_events.event_id`.
- SDK-like clients could not distinguish full history, terminal-inclusive replay, and already
  caught-up replay from response metadata alone.
- Terminal task output was only available through `task_notification` summary/prose even when the
  task result had `final_output` or `last_message` evidence.

Changes:

- `DevelopmentHarness.task_sdk_events()` now returns `projected_event_count`, `latest_source_event_id`,
  `next_after`, `cursor_policy`, `terminal_event_id`, `terminal_event_included`, and
  `final_output_included`.
- Generic harness `cursor_policy.cursor_basis` is `raw_harness_event_id` with exclusive semantics,
  matching the actual SQLite event store.
- Terminal task events now project a separate SDK-like `final_output` item from
  `result.final_output` or `result.last_message`.
- Verifier coverage now checks in-memory runtime projection and FastAPI route projection for full
  history, terminal-inclusive deltas, and caught-up `next_after` replay.
- The generic contract explicitly allows raw terminal-inclusive deltas to contain later hook events,
  because `after_task_run` is appended after task terminal events.

Verification:

- `.venv/bin/python -m py_compile developer_harness/runtime.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`
- Actual HTTP `GET /api/harness/v1/health`, `/ready`, and `/capabilities` with
  `X-Harness-Client-Id: generic-sdk-replay-check`
- `FITCHECK_RUN_HTTP_API_SMOKE=1 .venv/bin/python -B backend_harness/verify_http_api_smoke.py`
- HTTP product smoke run `run_068fd2daaa3c` still passed with SDK cursor metadata and grounded
  terminal `final_output`.

## 2026-06-28 Trace-ingested final output SDK projection

Problem found while checking the Agents SDK trace ingest path:

- `trace_ingest.py` already recorded top-level trace `final_output` as an `agent_final_output`
  observation.
- Generic `/api/harness/v1/tasks/{task_id}/sdk-events` projected handoff, approval, and guardrail
  observations, but did not project `agent_final_output` observations as SDK `final_output` events.
- That meant a controller consuming SDK events could miss final output evidence from an ingested
  Agents SDK/Responses trace unless it separately inspected task evidence.

Changes:

- `_sdk_event_projection()` now maps `observation.recorded` with `tool_name=agent_final_output` to a
  `final_output` event with `subtype=agent_trace_final_output`.
- The projected event preserves `output_source=observation.agent_final_output`, the raw output, and
  `evidence_refs`.
- `developer_harness/verify.py` now checks both that `trace_ingest.py --dry-run` emits
  `agent_final_output` observations and that recorded observations appear in SDK event projection.

Verification:

- `.venv/bin/python -m py_compile developer_harness/runtime.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`
- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`
- Actual HTTP `GET /api/harness/v1/health`, `/ready`, and `/capabilities` with
  `X-Harness-Client-Id: trace-final-output-projection-check`
- `FITCHECK_RUN_HTTP_API_SMOKE=1 .venv/bin/python -B backend_harness/verify_http_api_smoke.py`
- HTTP product smoke run `run_6604b19a0358` still passed with grounded terminal `final_output` and
  SDK cursor metadata.

## 2026-06-28 Product SDK final-output event grounding

Problem found while comparing the generic development harness SDK projection with the FitCheck
product route:

- `/api/agent/runs/{run_id}/sdk-events` exposed model responses, tool progress, guardrails, and
  terminal session state.
- The terminal SDK event only carried a report preview. A controller consuming SDK events had to
  fetch `/report` or `/evidence` separately to know whether the final report was grounded, why
  grounding passed, or which citation badges were available.

Changes:

- The product `sdk-events` projection now emits a terminal `final_output` event when a final report
  exists.
- The event includes `grounded`, `grounding`, `report_type`, compact `grounding_validation`,
  `citation_badges`, `citation_badge_count`, and report/evidence source endpoints.
- HTTP smoke now verifies that owner-session SDK events include a grounded final output event with
  `reason=ok`, empty suspicious evidence claims, and citation badge metadata.
- Live agent smoke now fetches `/sdk-events` for the real LLM run and treats a missing or ungrounded
  terminal `final_output` event as a boundary failure when the run completed.

Verification:

- `.venv/bin/python -m py_compile backend_harness/app/routes/agent.py backend_harness/verify_http_api_smoke.py`
- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`
- `FITCHECK_RUN_HTTP_API_SMOKE=1 .venv/bin/python -B backend_harness/verify_http_api_smoke.py`
- Existing live run `run_0cddcc326967` returned terminal SDK `final_output` with
  `grounded=true`, `report_type=shortlist`, `grounding_reason=ok`, `suspicious_evidence_claims=[]`,
  and `citation_badge_count=10`.
- `FITCHECK_RUN_LIVE_AGENT_SMOKE=1 .venv/bin/python -B backend_harness/verify_live_agent_smoke.py`
- Live smoke run `run_4b6f3f933aee` completed with `external_api_called=true`,
  `sdk_event_final_output_count=1`, `sdk_event_final_output_grounded=true`, and
  `sdk_event_final_output_citation_badge_count=10`.
- `FITCHECK_RUN_HTTP_API_SMOKE=1 .venv/bin/python -B backend_harness/verify_http_api_smoke.py`
- `FITCHECK_RUN_LIVE_AGENT_SMOKE=1 .venv/bin/python -B backend_harness/verify_live_agent_smoke.py`
- Live smoke run `run_00aff1c2bd2a` completed with `external_api_called=true`, 35 backend tool outputs, grounded final report, and `suspicious_fit_claims=[]`.

Follow-up found from live citation inspection:

- The report was valid, but numeric citations for score text could prefer unrelated values such as
  rejected-candidate commerce scores, OCR text lengths, or timeout seconds when the same number
  appeared elsewhere in observations.
- Product names such as `[3장가격] ...` also created generic number claims even though the number was
  part of the observed candidate identity.

Additional changes:

- Score statements are parsed as `score` claims: `overall`, `size`, `material`, `price`, and
  `confidence` must cite the matching `score.<field>` observation.
- `GroundingValidationResponse`, OpenAPI smoke, and backend verifier now expose and verify
  `suspicious_score_claims`.
- Query/planning context such as `search_query_hint` no longer grounds product style or fit claims.
- Product-name pack numbers such as `3장가격` and `2팩` are ignored by numeric claim extraction.

Additional verification:

- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`
- `FITCHECK_RUN_HTTP_API_SMOKE=1 .venv/bin/python -B backend_harness/verify_http_api_smoke.py`
- `FITCHECK_RUN_LIVE_AGENT_SMOKE=1 .venv/bin/python -B backend_harness/verify_live_agent_smoke.py`
- Live smoke run `run_1647178687b8` completed with `external_api_called=true`, 23 backend tool
  outputs, grounded final report, `suspicious_score_claims=[]`, and score citations pointing to
  `ranked_matches[0].score.confidence/material/overall/size`.

## 2026-06-29 Agents SDK matrix scenario-rollup readback binding

Problem found while hardening the generic development harness:

- The aggregate Agents SDK smoke matrix summary exposed scenario failed-check maps, but the stored
  `agents_sdk_smoke_matrix_scenario_rollup` observation did not preserve all per-scenario
  readback/replay details.
- Aggregate readback validation checked scenario count and published task ids, but not that the
  stored rollup payload still matched the source matrix JSON.

Changes:

- `matrix_scenario_rollup` now stores per-scenario generic readback failed checks, generic
  readback task binding, projection failed checks, scenario event-replay validity, scenario
  event-replay failed checks, and scenario event-replay task binding.
- The aggregate scenario rollup also stores top-level scenario failed-check maps and event-replay
  failed scenario/check maps copied from the matrix summary.
- `validate_matrix_readback` now recomputes the expected scenario rollup from the matrix payload
  and fails `matrix_rollup_payload_matches` if the stored aggregate observation drifts.
- The verifier covers both the positive rollup match and a corrupted replay task-binding case.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live generic harness SDK run:
  `.venv/bin/python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py --scenario approval --run-id live-scenario-rollup-matrix-20260629 --max-tokens 180 --probe-provider --publish-readback --publish-matrix-readback --client-id codex-scenario-rollup-20260629 --base-url http://127.0.0.1:8000/api/harness/v1 --output-dir harness_outputs/sdk-live-smoke-matrix --output-json harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-scenario-rollup-live-20260629.json`
- Live provider probe diagnostic id `55`, scenario task `task_1211a09854f8`, aggregate task
  `task_7f896a784027`.
- Live aggregate validation reported `matrix_rollup_payload_matches=true`,
  `matrix_payload_readback_valid=true`, and no failed checks.
- Existing aggregate revalidation without new live provider calls wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-scenario-rollup-revalidate-20260629.json`
  with `validation.valid=true`.

## 2026-06-29 Tool-call status-update replay expectation

Problem found by a `.env`-sourced live generic harness SDK run:

- A first implementation made scenario event replay require `tool_call.status_updated` for every
  ingested tool call.
- Live `tool` scenario run `live-tool-status-event-replay-20260629` disproved that assumption:
  trace-ingest recorded the tool call as terminal `completed`, so no separate status-update event
  was emitted.
- The failed live run still had a valid provider probe and readback surfaces:
  provider diagnostic id `56`, scenario task `task_0120b464f8d6`, aggregate task
  `task_b5f0df653a66`, failed check `event_replay_covers_tool_call_status_updated`.

Changes:

- `trace_ingest.py` now reports `tool_call_status_update_expected_count` in both dry-run action
  summaries and execution result summaries.
- The expected count is the number of tool calls recorded in a non-terminal state that later receive
  a terminal observation with the same `call_id`.
- Scenario event replay validation now checks `tool_call.status_updated` coverage against that
  expected count, not against all tool calls.
- The verifier covers terminal tool calls expecting `0`, queued-to-completed tool calls expecting
  `1`, and replay failure when the expected status-update event is missing.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/trace_ingest.py .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- Fixed live `tool` run wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-tool-status-replay-fixed-live-20260629.json`
  with provider diagnostic id `57`, scenario task `task_116807661e09`, aggregate task
  `task_90799eb380e9`, `tool_call_status_update_expected_count=0`, replay
  `tool_call.status_updated` count `0`, and `event_replay_covers_tool_call_status_updated=true`.
- Live `approval` run wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-status-replay-live-20260629.json`
  with provider diagnostic id `58`, scenario task `task_aeac93dc5851`, aggregate task
  `task_877a1e5bf18b`, `tool_call_status_update_expected_count=1`, replay
  `tool_call.status_updated` count `1`, and `event_replay_covers_tool_call_status_updated=true`.
- Existing aggregate revalidation without new provider calls wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-tool-status-replay-fixed-revalidate-20260629.json`
  and
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-approval-status-replay-revalidate-20260629.json`
  with `validation.valid=true` and `matrix_rollup_payload_matches=true`.

Additional aggregate-surface hardening:

- Matrix summaries now expose `tool_call_status_update_expected_counts`,
  `tool_call_status_updated_event_counts`, `tool_call_status_update_expected_total`, and
  `tool_call_status_updated_event_total`.
- The stored `agents_sdk_smoke_matrix_scenario_rollup` observation also preserves those maps and
  per-scenario expected/observed counts, so controllers can explain terminal tool calls versus
  queued-to-terminal tool calls from aggregate evidence alone.
- The verifier asserts both summary maps and scenario-rollup per-scenario counts.

Additional verification:

- `.env`-sourced live two-scenario matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-status-update-summary-map-live-20260629.json`
  with provider diagnostic id `59`, scenario tasks `tool=task_ea24ed53d94e` and
  `approval=task_9012518138e5`, aggregate task `task_72695fc8193e`,
  `tool_call_status_update_expected_counts={"tool":0,"approval":1}`, and
  `tool_call_status_updated_event_counts={"tool":0,"approval":1}`.
- Stored aggregate evidence for `task_72695fc8193e` included the same maps plus scenario rollup
  items `{tool: expected 0, observed 0}` and `{approval: expected 1, observed 1}`.
- Existing aggregate revalidation without new provider calls wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-status-update-summary-map-revalidate-20260629.json`
  with `validation.valid=true` and `matrix_rollup_payload_matches=true`.

Additional readback-check hardening:

- Aggregate readback validation now has dedicated status-update map checks for both summary and
  scenario-rollup observations:
  `matrix_summary_tool_call_status_update_expected_counts_match`,
  `matrix_summary_tool_call_status_updated_event_counts_match`,
  `matrix_summary_tool_call_status_update_expected_total_match`,
  `matrix_summary_tool_call_status_updated_event_total_match`,
  `matrix_rollup_tool_call_status_update_expected_counts_match`,
  `matrix_rollup_tool_call_status_updated_event_counts_match`,
  `matrix_rollup_tool_call_status_update_expected_total_match`, and
  `matrix_rollup_tool_call_status_updated_event_total_match`.
- Synthetic verifier mutates summary and rollup status-update maps and confirms the dedicated
  check names fail, in addition to the broad payload mismatch checks.

Additional revalidation:

- Existing live SDK matrix evidence was revalidated without a new provider/model call:
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-status-update-summary-map-readback-checks-revalidate-20260629.json`.
- Revalidated aggregate task `task_72695fc8193e` returned `validation.valid=true`,
  empty `failed_checks`, and all eight status-update summary/rollup readback checks set to `true`.

Additional scenario event-count payload hardening:

- `scenario_event_replay_validation` now exposes `expected_event_counts` and
  `observed_event_counts` for `artifact.recorded`, `observation.recorded`,
  `tool_call.recorded`, and `tool_call.status_updated`.
- The replay checks read from those maps, so controllers can inspect one payload to explain which
  events were required and which were observed.
- The verifier asserts the positive expected/observed maps and the broken missing-status-update
  case where `tool_call.status_updated` is expected as `1` but observed as `0`.

Additional verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live generic harness SDK approval run wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-event-count-payload-live-20260629.json`
  with provider diagnostic id `60`, scenario task `task_2f0a7fdba1c5`, aggregate task
  `task_d365948c3204`,
  `expected_event_counts={"artifact.recorded":2,"observation.recorded":9,"tool_call.recorded":1,"tool_call.status_updated":1}`,
  matching `observed_event_counts`, empty `failed_checks`, and
  `matrix_payload_readback_valid=true`.
- Existing aggregate revalidation without new provider calls wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-event-count-payload-revalidate-20260629.json`
  with `validation.valid=true`, `check_count=45`, and empty `failed_checks`.

Additional aggregate replay-count hardening:

- Matrix summaries and stored `agents_sdk_smoke_matrix_scenario_rollup` observations now expose
  `scenario_event_replay_expected_event_counts`, `scenario_event_replay_observed_event_counts`,
  `scenario_event_replay_expected_event_totals`, and
  `scenario_event_replay_observed_event_totals`.
- Each scenario rollup item also preserves its own
  `scenario_event_replay_expected_event_counts` and
  `scenario_event_replay_observed_event_counts`.
- Aggregate readback validation now has dedicated checks for those summary and rollup maps/totals,
  so stale controller-facing replay-count evidence fails with a specific check name instead of only
  `matrix_summary_payload_matches` or `matrix_rollup_payload_matches`.
- The verifier mutates both summary and rollup replay-count maps and confirms the dedicated checks
  fail.

Additional live verification:

- Before the patch, a `.env`-sourced live generic harness SDK matrix
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-replay-count-aggregate-gap-live-20260629.json`
  completed with provider diagnostic id `61`, aggregate task `task_0743714dd00d`, and valid
  scenario replay counts, but the aggregate summary did not expose the expected/observed event-count
  maps.
- After the patch, `.env`-sourced live matrix
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-replay-count-aggregate-fixed-live-20260629.json`
  completed with provider diagnostic id `62`, scenario tasks `tool=task_bca1c76f69ca`,
  `approval=task_9bc2afb481e7`, `stream=task_268a3cf96131`, aggregate task
  `task_dfa1be3fc726`, `matrix_payload_readback_valid=true`, and `check_count=53`.
- The fixed live summary reported expected and observed totals
  `{"artifact.recorded":4,"observation.recorded":56,"tool_call.recorded":3,"tool_call.status_updated":1}`
  with no failed checks.
- Existing aggregate revalidation without new provider calls wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-replay-count-aggregate-fixed-revalidate-20260629.json`
  with `validation.valid=true`, `check_count=53`, and empty `failed_checks`.

Additional aggregate projection-count hardening:

- A `.env`-sourced live generic harness SDK matrix for the remaining scenarios
  `handoff`, `guardrail`, `guardrail_tripwire`, `session`, and `stream_handoff` completed before
  this patch:
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-remaining-scenarios-live-20260629.json`.
- That run had provider diagnostic id `63`, aggregate task `task_bb3efed7b516`, and valid
  readbacks, but controllers still had to reopen each scenario result to see which scenario
  contributed handoff, guardrail, run-error, session, stream-event, phase, and final-output
  projection counts.
- Matrix summaries and stored scenario-rollup observations now expose
  `scenario_sdk_events_projection_counts` and
  `scenario_client_transcript_projection_counts`, while each rollup scenario item preserves
  `sdk_events_projection_counts` and `client_transcript_projection_counts`.
- Aggregate readback validation now has dedicated checks for summary and rollup projection-count
  maps, and the verifier mutates both surfaces to confirm the specific checks fail.

Additional live verification:

- `.env`-sourced live matrix
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-remaining-scenarios-projection-map-fixed-live-20260629.json`
  completed with provider diagnostic id `64`, scenario tasks `handoff=task_5e522267f4b8`,
  `guardrail=task_1220127386e5`, `guardrail_tripwire=task_ef0013e71c88`,
  `session=task_c45cbcea1895`, `stream_handoff=task_67f429e2f1ca`, aggregate task
  `task_810c79b7b936`, `matrix_payload_readback_valid=true`, and `check_count=57`.
- The fixed live summary exposed per-scenario SDK-event and client-transcript projection maps.
  Totals matched with `agent_model_response_count=8`, `agent_stream_event_count=30`,
  `agent_phase_count=6`, `agent_session_count=1`, `agent_handoff_count=4`,
  `agent_guardrail_count=2`, `agent_run_error_count=1`, and `final_output_count=5`.
- Existing aggregate revalidation without new provider calls wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-remaining-scenarios-projection-map-fixed-revalidate-20260629.json`
  with `validation.valid=true`, `check_count=57`, and empty `failed_checks`.

Additional aggregate API-usage hardening:

- A `.env`-sourced all-scenario live matrix completed before this patch:
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-all-scenarios-usage-gap-live-20260629.json`.
- That run had provider diagnostic id `65`, aggregate task `task_0a3bbd2ec460`, valid readbacks,
  and aggregate `api_usage_totals={"requests":14,"input_tokens":1611,"output_tokens":223,"total_tokens":1834}`,
  but no `scenario_api_usage_totals`, so controllers had to reopen each scenario result to find the
  expensive scenario.
- Matrix summaries and stored scenario-rollup observations now expose `scenario_api_usage_totals`
  and aggregate `api_usage_totals`, while each rollup scenario item preserves its own
  `api_usage_totals`.
- Aggregate readback validation now has dedicated checks for summary and rollup usage maps/totals,
  and the verifier mutates both surfaces to confirm the specific checks fail.

Additional live verification:

- `.env`-sourced all-scenario live matrix
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-all-scenarios-usage-map-fixed-live-20260629.json`
  completed with provider diagnostic id `66`, aggregate task `task_c862cecd0b57`,
  `matrix_payload_readback_valid=true`, and `check_count=61`.
- The fixed live summary exposed `scenario_api_usage_totals` for all eight scenarios and preserved
  the same aggregate totals:
  `{"input_tokens":1611,"output_tokens":223,"requests":14,"total_tokens":1834}`.
- Existing aggregate revalidation without new provider calls wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-all-scenarios-usage-map-fixed-revalidate-20260629.json`
  with `validation.valid=true`, `check_count=61`, and empty `failed_checks`.

Additional published-scenario readback revalidation:

- Existing aggregate revalidation proved the aggregate matrix evidence-holder task, but a later
  controller still had to reopen each `publish_readback.task_id` manually to prove the per-scenario
  evidence-holder tasks had not drifted.
- `agents_sdk_smoke_matrix.py` now accepts `--validate-published-scenarios` with
  `--validate-matrix-readback-task-id` and `--validate-matrix-json`.
- That mode refetches each published scenario task from the matrix JSON and reruns generic
  trace-ingest readback, scenario-specific projection validation, and raw event replay validation.
  It adds `published_scenario_readback_validation` with scenario task ids, status counts, check
  totals, failed-scenario maps, and per-scenario readback summaries.
- `developer_harness/verify.py` now covers the default aggregate-only revalidation path, the
  aggregate-plus-scenario path, and a broken scenario replay path that fails with a prefixed
  `event_replay:*` failed check.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced stored-evidence revalidation without new live SDK/provider calls:
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-all-scenarios-scenario-revalidate-20260629.json`.
  The aggregate validation remained valid with `check_count=61`; published scenario revalidation
  covered all eight scenario tasks, ran `check_count=476`, and had empty failed-scenario and
  failed-check maps.

Additional controller-summary and readback-error handling:

- Published scenario revalidation now writes a compact
  `published_scenario_readback_summary` beside the detailed per-scenario validation results.
- A scenario readback fetch or validation error is now captured as a scenario-level
  `readback_error:*` failed check with `error_type`, `error`, and `error_scenarios` in the compact
  summary, rather than aborting the whole matrix command before JSON can be written.
- `developer_harness/verify.py` now covers a missing scenario task that raises `SystemExit` from the
  harness API client and confirms the failure remains structured.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced positive stored-evidence revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-all-scenarios-scenario-revalidate-summary-fixed-20260629.json`
  with `status=completed`, aggregate `check_count=61`, published scenario `check_count=476`,
  eight published scenarios, and empty `error_scenarios`.
- `.env`-sourced negative stored-evidence revalidation used
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-all-scenarios-missing-scenario-task-20260629.json`
  and wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-all-scenarios-missing-scenario-task-revalidate-20260629.json`
  with exit code `1`, `failed_scenarios=["tool"]`, and
  `failed_checks.tool=["readback_error:SystemExit"]`.

Additional compact validation output:

- `agents_sdk_smoke_matrix.py` validation mode now accepts `--compact-output`.
- The full payload is still written to `--output-json`, but stdout contains only
  `readback_validation_summary` for CI/controller logs.
- The compact summary includes aggregate validity/check counts/task binding and the published
  scenario readback summary.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced positive compact-output revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-all-scenarios-compact-output-revalidate-20260629.json`.
  Stdout was `source=agents_sdk_smoke_matrix_existing_readback_compact_summary`, had no detailed
  `results`, matched the file's `readback_validation_summary`, and reported eight scenarios with
  `check_count=476`.
- `.env`-sourced negative compact-output revalidation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-all-scenarios-compact-output-missing-scenario-revalidate-20260629.json`.
  It exited `1`, printed compact failure stdout with no detailed `results`, and exposed
  `failed_checks.tool=["readback_error:SystemExit"]`.

Additional live/dry-run compact matrix output:

- `--compact-output` now applies to live and dry-run matrix modes as well as validation mode.
- Live and dry-run full payloads include `matrix_run_summary`; compact stdout prints that summary
  without the detailed per-scenario `results` or dry-run `plan`.
- `developer_harness/verify.py` now covers dry-run compact output and confirms stdout matches the
  full file's `matrix_run_summary` while the full plan remains in `--output-json`.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live compact matrix smoke:
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-compact-output-20260629.json`.
  It ran the `tool` scenario through the live Agents SDK path, probed provider diagnostic id `67`,
  published scenario task `task_355d0c2e948a`, published aggregate task `task_86ee9682ab0d`,
  and completed both aggregate readback gates. Stdout was compact
  `source=agents_sdk_smoke_matrix_compact_summary`, had no detailed `results`, and matched the full
  file's `matrix_run_summary`.
- Existing stored-evidence revalidation for that live compact matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-compact-output-revalidate-20260629.json`
  with compact stdout, aggregate `check_count=61`, published scenario `check_count=57`, and no
  failed scenarios.

Additional compact readback handles:

- Live compact stdout now exposes the aggregate `matrix_readback_task_id`, matrix/payload readback
  check counts, failed-check lists, and task-binding maps.
- This means a controller can start later stored-evidence revalidation from compact stdout without
  reopening the full matrix JSON just to discover the aggregate evidence-holder task id.
- `developer_harness/verify.py` now covers those fields for both dry-run absence and synthetic
  live payload presence.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live compact-handle matrix smoke:
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-compact-handles-20260629.json`.
  It ran the live `tool` scenario, probed provider diagnostic id `68`, published scenario task
  `task_739ceeabe14f`, published aggregate task `task_0f70f00d8dcc`, and compact stdout exposed
  `matrix_readback_check_count=61` plus `matrix_payload_readback_check_count=61`.
- Existing stored-evidence revalidation for that matrix wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-compact-handles-revalidate-20260629.json`
  with aggregate `check_count=61`, published scenario `check_count=57`, and no failed scenarios.

Additional revalidation input bundle:

- Live compact stdout now includes `client_id`, `base_url`, and `revalidation_inputs`.
- `revalidation_inputs` contains the aggregate readback task id, matrix JSON path, client id, base
  URL, and flags for published-scenario validation and compact output. It intentionally does not
  include any API key.
- This lets a controller start stored-evidence revalidation from compact stdout alone.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced live compact revalidation-input matrix smoke:
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-revalidation-inputs-20260629.json`.
  It ran the live `tool` scenario, probed provider diagnostic id `69`, and compact stdout included
  `revalidation_inputs.validate_matrix_readback_task_id=task_7f62af89f0de`,
  `client_id=codex-live-revalidation-inputs-20260629`, and
  `base_url=http://127.0.0.1:8000/api/harness/v1`.
- Building a revalidation command from only that stdout bundle wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-revalidation-inputs-revalidate-20260629.json`
  with aggregate `check_count=61`, published scenario `check_count=57`, and no failed scenarios.

Additional validation-summary revalidation input bundle:

- Existing stored-evidence validation compact stdout now also includes `client_id`, `base_url`, and
  `revalidation_inputs`.
- This lets a controller rerun or retry validation from validation stdout alone, not only from the
  original live compact matrix stdout.
- API keys remain excluded from all compact summaries and revalidation input bundles.

Verification:

- `.venv/bin/python -m py_compile .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py developer_harness/verify.py`
- `.venv/bin/python -B developer_harness/verify.py`
- `.env`-sourced stored-evidence validation wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-validation-reinput-20260629.json`.
  Its compact stdout exposed `revalidation_inputs` for
  `task_7f62af89f0de`, matrix JSON
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-live-revalidation-inputs-20260629.json`,
  client id `codex-live-revalidation-inputs-20260629`, and base URL
  `http://127.0.0.1:8000/api/harness/v1`.
- Building a second validation command from only that validation stdout wrote
  `harness_outputs/sdk-live-smoke-matrix/agents-sdk-smoke-matrix-validation-reinput-rerun-20260629.json`
  with aggregate `check_count=61`, published scenario `check_count=57`, and no failed scenarios.

## 2026-06-28 Evidence coverage claim grounding

Problem found while reviewing live report prose and trace context:

- Final reports can say `검색, 상세페이지 확인, OCR, 정규화로 실측, 소재, 가격 근거를 확인했습니다`.
- That sentence is not just narration; it is a factual claim that measurements/material/price
  evidence exists.
- Previous grounding checked numbers, URLs, material composition, score fields, identity, style,
  and fit claims, but did not separately validate evidence coverage claims.
- Candidate audit names such as `Candidate 3 tee` or product display text such as `3Colors` could
  still create unhelpful numeric claim failures even though those numbers are product-name noise.

Changes:

- `report_grounding.py` now extracts evidence coverage claims for `measurements`, `material`, and
  `price`.
- Positive coverage text must be grounded by `evidence_coverage.observed_fields` or equivalent
  `normalized_spec.measurements/material/price` evidence.
- Missing-evidence fragments such as `가격 근거가 부족합니다(missing evidence)` are allowed as
  uncertainty and do not become affirmative coverage claims.
- `GroundingValidationResponse` and HTTP smoke now expose `suspicious_evidence_claims`.
- `write_purchase_report.result` is not used as a self-grounding source; report facts must come from
  prior backend observations such as score/normalize/fetch results.
- Numeric extraction ignores product-name color counts and candidate-audit list numbers such as
  `3Colors` and `Candidate 3 tee`.
- Max-turn fallback keeps all-candidate report provenance when a completed `candidate_key=all`
  shortlist is adopted after model turn exhaustion.

Verification:

- `.venv/bin/python -B backend_harness/verify_backend_infrastructure.py`
- `.venv/bin/python -B backend_harness/verify_production_agent_runtime.py`
